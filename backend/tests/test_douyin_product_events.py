import gzip
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
from app.douyin_product_events.protocol import (
    decode_push_frame,
    encode_ack_frame,
    iter_fields,
    parse_live_pop_product_id,
    parse_live_shopping_product,
    parse_product_change,
)
from app.douyin_product_events.signing import (
    WebSocketSignatureGenerator,
    sign_stub_with_values,
    websocket_signature_stub,
)
from app.douyin_product_events.session import (
    DouyinWebcastSessionPreparer,
    InitialIMState,
    PreparedWebSocketSession,
    RoomInfo,
    build_initial_im_query,
    build_room_enter_query,
    build_websocket_url,
    live_id_from_url,
    normalize_push_url,
    parse_initial_im_response,
    parse_room_enter_response,
    _response_error,
)
from app.douyin_product_events.webcast_http import a_bogus, sm3_digest
from app.douyin_product_events.client import DouyinProductEventClient
from app.douyin_product_events.client import _message_sample
from app.douyin_product_events.probe import _safe_exception, _safe_text, _safe_url
from app.douyin_product_events.replay import replay_samples
from app.douyin_product_events.promotions import (
    build_promotions_page_query,
    parse_promotions_page,
)


def varint(value):
    output = bytearray()
    while value > 0x7F:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def integer_field(number, value):
    return varint(number << 3) + varint(value)


def bytes_field(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


def push_message(method, payload, message_id):
    return (bytes_field(1, method.encode()) + bytes_field(2, payload)
            + integer_field(3, message_id))


def push_frame(messages):
    response = b"".join(bytes_field(1, message) for message in messages)
    return bytes_field(8, gzip.compress(response))


class DouyinProductProtocolTests(unittest.TestCase):
    def test_decodes_product_events_and_normalizes_timestamp(self):
        product_a = integer_field(1, 101) + integer_field(2, 3) + integer_field(4, 2)
        product_b = integer_field(1, 202) + integer_field(4, 1)
        payload = integer_field(2, 1_800_000_000_000)
        payload += bytes_field(4, product_a) + bytes_field(4, product_b)

        events = parse_product_change(payload, message_id=77, received_at=1_800_000_001)

        self.assertEqual([item.promotion_id for item in events], ["101", "202"])
        self.assertEqual([item.product_index for item in events], [3, None])
        self.assertEqual([item.explain_type for item in events], [2, 1])
        self.assertEqual([item.event_time for item in events], [1_800_000_000] * 2)

    def test_unreasonable_timestamp_falls_back_to_receive_time(self):
        payload = integer_field(2, 1_700_000_000_000)
        payload += bytes_field(4, integer_field(1, 123))
        event = parse_product_change(payload, message_id=1, received_at=1_800_000_000)[0]
        self.assertEqual(event.event_time, 1_800_000_000)

    def test_parses_live_shopping_product_and_live_pop_confirmation(self):
        received_at = 1_800_000_000
        shopping = bytes_field(1, integer_field(4, 1_799_999_999_500))
        shopping += integer_field(2, 2) + integer_field(3, 3849253912720834593)
        event = parse_live_shopping_product(shopping, message_id=51, received_at=received_at)
        live_pop = bytes_field(2, b"LivePopMessage")
        live_pop += bytes_field(6, integer_field(3, 3849253912720834593))
        refresh = bytes_field(2, b"ProductRefreshMessage")
        refresh += bytes_field(6, integer_field(3, 3849253912720834593))

        self.assertEqual(event.promotion_id, "3849253912720834593")
        self.assertEqual(event.source, "live_shopping")
        self.assertEqual(event.message_id, 51)
        self.assertEqual(event.event_time, 1_799_999_999.5)
        self.assertEqual(parse_live_pop_product_id(live_pop), event.promotion_id)
        self.assertIsNone(parse_live_pop_product_id(refresh))

    def test_decodes_gzipped_push_frame_and_ack_flags(self):
        message = bytes_field(1, b"WebcastProductChangeMessage")
        message += bytes_field(2, integer_field(1, 55)) + integer_field(3, 88)
        response = bytes_field(1, message) + bytes_field(5, b"cursor") + integer_field(9, 1)
        compressed = gzip.compress(response)
        frame = bytes_field(6, b"gzip") + bytes_field(8, compressed)

        decoded = decode_push_frame(frame)

        self.assertTrue(decoded.need_ack)
        self.assertEqual(decoded.internal_ext, "cursor")
        self.assertEqual(decoded.messages[0].method, "WebcastProductChangeMessage")
        self.assertEqual(decoded.messages[0].message_id, 88)
        self.assertEqual(decoded.messages[0].payload, integer_field(1, 55))

        frame_without_encoding_hint = bytes_field(8, compressed)
        decoded_without_hint = decode_push_frame(frame_without_encoding_hint)
        self.assertEqual(decoded_without_hint.messages, decoded.messages)
        self.assertTrue(decoded_without_hint.need_ack)

    def test_ack_contains_log_id_and_internal_extension(self):
        fields = list(iter_fields(encode_ack_frame(321, "ack-token")))
        self.assertEqual([(item.number, item.value) for item in fields],
                         [(2, 321), (7, b"ack-token")])

    def test_rejects_malformed_wire_data(self):
        with self.assertRaises(ValueError):
            list(iter_fields(b"\x80"))
        with self.assertRaises(ValueError):
            decode_push_frame(bytes_field(8, b"\xff"))

    def test_unsupported_wire_type_reports_field_and_offset(self):
        with self.assertRaisesRegex(
            ValueError,
            r"wire type: 7 \(field=9, key=0x4f, offset=2, near=08 01 4f\)",
        ):
            list(iter_fields(b"\x08\x01\x4f"))


class DouyinWebSocketSigningTests(unittest.TestCase):
    def test_signature_matches_douyin_live_v231_javascript_vector(self):
        signature = sign_stub_with_values(
            "704f436b2558b0d7a1c7e758527dd8f1", 1, True, 63, 63
        )
        self.assertEqual(signature, "6pt0VC0aRZDP7n07")

    def test_custom_base64_preserves_plus_character(self):
        signature = sign_stub_with_values(
            "704f436b2558b0d7a1c7e758527dd8f1", 0, False, 0, 6
        )
        self.assertEqual(signature, "fD+evFuzasjOC8Uu")

    def test_stub_uses_canonical_webcast_fields(self):
        self.assertEqual(
            websocket_signature_stub("12345", "67890"),
            "9636a8a962bb45529866d0afcb779b9b",
        )

    def test_session_generator_advances_counter_and_random_values(self):
        values = iter((1, 63, 63, 0, 1, 2))
        generator = WebSocketSignatureGenerator(lambda: next(values))
        first = generator.sign("12345", "67890")
        second = generator.sign("12345", "67890")
        stub = websocket_signature_stub("12345", "67890")
        self.assertEqual(first, sign_stub_with_values(stub, 1, True, 63, 63))
        self.assertEqual(
            second,
            sign_stub_with_values(stub, 2, False, 1, 2),
        )

    def test_signer_requires_room_and_user_identity(self):
        with self.assertRaises(ValueError):
            websocket_signature_stub("12345", "")
        with self.assertRaises(ValueError):
            sign_stub_with_values("not-md5", 1, False, 1, 1)


class DouyinSessionPreparationTests(unittest.TestCase):
    def test_promotions_page_query_uses_room_pagination_and_cookie_tokens(self):
        room = RoomInfo("share", "9001", "8002")
        query = parse_qs(build_promotions_page_query(
            room, "Mozilla/5.0 Chrome/133.0.0.0",
            "msToken=token-value; UIFID=ui-value; webid=web-id; sessionid=private",
            offset=40, limit=20, webid="web-id",
        ), keep_blank_values=True)
        self.assertEqual(query["room_id"], ["9001"])
        self.assertEqual(query["author_id"], ["8002"])
        self.assertEqual(query["offset"], ["40"])
        self.assertEqual(query["limit"], ["20"])
        self.assertEqual(query["msToken"], ["token-value"])
        self.assertEqual(query["uifid"], ["ui-value"])
        self.assertEqual(query["webid"], ["web-id"])
        self.assertNotIn("sessionid", query)
        prefixed = parse_qs(build_promotions_page_query(
            room, "Mozilla/5.0 Chrome/133.0.0.0", "Cookie: msToken=token-value"
        ), keep_blank_values=True)
        self.assertEqual(prefixed["msToken"], ["token-value"])

    def test_promotions_page_parses_catalog_and_current_product(self):
        names, next_offset, total, current, item_count = parse_promotions_page(json.dumps({
            "status_code": 0,
            "next_offset": 20,
            "total": 277,
            "promotions": [
                {"promotion_id": "101", "title": "针织开衫"},
                {"promotion_id": "202", "title": "半身裙"},
                {"promotion_id": "303", "title": ""},
                None,
            ],
            "top_promotion": {"promotion_id": "202", "title": "当前讲解商品"},
        }))
        self.assertEqual(names, {"101": "针织开衫", "202": "半身裙"})
        self.assertEqual((next_offset, total, current),
                         (20, 277, ("202", "当前讲解商品")))
        self.assertEqual(item_count, 4)
        self.assertEqual(parse_promotions_page(
            '{"status_code":0,"next_offset":0,"total":0,"promotions":[]}'
        ), ({}, 0, 0, None, 0))
        with self.assertRaisesRegex(ValueError, "缺少 promotions"):
            parse_promotions_page('{"status_code":0}')
        with self.assertRaisesRegex(ValueError, "错误或无效"):
            parse_promotions_page('{"status_code":1,"promotions":[]}')

    def test_promotion_resolver_fetches_all_pages_with_signed_empty_post(self):
        room = RoomInfo("live-id", "9001", "8002")
        requests = []

        def respond(request):
            requests.append(request)
            offset = parse_qs(urlsplit(str(request.url)).query)["offset"][0]
            if offset == "0":
                return httpx.Response(200, json={
                    "status_code": 0, "next_offset": 2, "total": 3,
                    "promotions": [
                        {"promotion_id": "p-1", "title": "第一页商品"},
                        {"promotion_id": "p-2", "title": "直播商品标题"},
                    ],
                    "top_promotion": {"promotion_id": "p-2", "title": "当前商品"},
                })
            return httpx.Response(200, json={
                "status_code": 0, "next_offset": 0, "total": 3,
                "promotions": [{"promotion_id": "p-3", "title": "第二页商品"}],
                "top_promotion": None,
            })

        transport = httpx.MockTransport(respond)
        client = httpx.Client(transport=transport)
        from app.douyin_product_events.promotions import DouyinPromotionNameResolver
        resolver = DouyinPromotionNameResolver(
            room, "Mozilla/5.0 Chrome/133.0.0.0", "sessionid=temporary", client=client,
            min_request_interval=0, retry_delay=0,
        )
        try:
            names, current = resolver.lookup_catalog(page_size=2)
            self.assertEqual(names, {
                "p-1": "第一页商品", "p-2": "直播商品标题", "p-3": "第二页商品"
            })
            self.assertEqual(current, ("p-2", "当前商品"))
        finally:
            client.close()
        self.assertEqual(len(requests), 2)
        self.assertEqual(urlsplit(str(requests[0].url)).path, "/live/promotions/page/")
        self.assertIn("a_bogus", parse_qs(urlsplit(str(requests[0].url)).query))
        self.assertEqual(requests[0].headers["cookie"], "sessionid=temporary")
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].content, b"")

    def test_promotion_request_error_keeps_http_diagnostics_without_secrets(self):
        def respond(_request):
            return httpx.Response(
                403, headers={"x-tt-logid": "request-123", "content-type": "application/json"},
                json={"code": "AUTH_REQUIRED", "message": "login required"},
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        from app.douyin_product_events.promotions import (
            DouyinPromotionNameResolver, PromotionCatalogRequestError,
        )
        resolver = DouyinPromotionNameResolver(
            RoomInfo("live-id", "9001", "8002"), "Mozilla/5.0 Chrome/133.0.0.0",
            "sessionid=private; passport_csrf_token=csrf-private", client=client,
            min_request_interval=0, retry_delay=0,
        )
        try:
            with self.assertRaises(PromotionCatalogRequestError) as raised:
                resolver.lookup_catalog()
        finally:
            client.close()
        detail = raised.exception.detail
        self.assertEqual(detail["response"]["http_status"], 403)
        self.assertEqual(detail["response"]["request_id"], "request-123")
        self.assertTrue(detail["request"]["csrf_header_sent"])
        serialized = json.dumps(detail)
        self.assertNotIn("private", serialized)
        self.assertNotIn("csrf-private", serialized)

    def test_promotion_pagination_error_includes_the_successful_http_response(self):
        def respond(_request):
            return httpx.Response(
                200, headers={"x-tt-logid": "page-request-9", "content-type": "application/json"},
                json={
                    "status_code": 0, "next_offset": 0, "total": 10,
                    "promotions": [
                        {"promotion_id": "p1", "title": "商品一"},
                        {"promotion_id": "p2", "title": "商品二"},
                    ],
                    "message": "diagnostic-response-marker",
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        from app.douyin_product_events.promotions import (
            DouyinPromotionNameResolver, PromotionCatalogRequestError,
        )
        resolver = DouyinPromotionNameResolver(
            RoomInfo("live-id", "9001", "8002"), "Mozilla/5.0 Chrome/133.0.0.0",
            client=client, min_request_interval=0, retry_delay=0,
        )
        try:
            with self.assertRaises(PromotionCatalogRequestError) as raised:
                resolver.lookup_catalog(page_size=2)
        finally:
            client.close()
        detail = raised.exception.detail
        self.assertEqual(detail["stage"], "pagination")
        self.assertEqual(detail["pagination"], {
            "offset": 0, "next_offset": 0, "total": 10,
            "page_item_count": 2, "collected_items": 2,
        })
        self.assertEqual(detail["response"]["http_status"], 200)
        self.assertEqual(detail["response"]["request_id"], "page-request-9")
        self.assertIn("diagnostic-response-marker", detail["response"]["body"])

    def test_empty_catalog_response_retries_three_attempts_and_preserves_history(self):
        requests = []

        def respond(request):
            requests.append(request)
            if len(requests) < 3:
                return httpx.Response(200, headers={"x-tt-logid": f"empty-{len(requests)}"}, content=b"")
            return httpx.Response(200, json={
                "status_code": 0, "next_offset": 0, "total": 1,
                "promotions": [{"promotion_id": "p1", "title": "恢复后的商品"}],
                "top_promotion": {"promotion_id": "p1", "title": "恢复后的商品"},
            })

        client = httpx.Client(transport=httpx.MockTransport(respond))
        from app.douyin_product_events.promotions import DouyinPromotionNameResolver
        resolver = DouyinPromotionNameResolver(
            RoomInfo("live-id", "9001", "8002"), "Mozilla/5.0 Chrome/133.0.0.0",
            client=client, min_request_interval=0, retry_delay=0,
        )
        try:
            names, current = resolver.lookup_catalog()
        finally:
            client.close()
        self.assertEqual(len(requests), 3)
        self.assertEqual(names, {"p1": "恢复后的商品"})
        self.assertEqual(current, ("p1", "恢复后的商品"))

    def test_persistent_empty_catalog_response_reports_all_three_attempts(self):
        requests = []
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: (requests.append(request) or httpx.Response(200, content=b""))
        ))
        from app.douyin_product_events.promotions import (
            DouyinPromotionNameResolver, PromotionCatalogRequestError,
        )
        resolver = DouyinPromotionNameResolver(
            RoomInfo("live-id", "9001", "8002"), "Mozilla/5.0 Chrome/133.0.0.0",
            client=client, min_request_interval=0, retry_delay=0,
        )
        try:
            with self.assertRaises(PromotionCatalogRequestError) as raised:
                resolver.lookup_catalog()
        finally:
            client.close()
        self.assertEqual(len(requests), 3)
        self.assertEqual(raised.exception.detail["attempts"], 3)
        self.assertEqual(len(raised.exception.detail["previous_attempts"]), 2)
        self.assertEqual(raised.exception.detail["response"]["body"], "<empty>")

    def test_catalog_requests_are_rate_limited(self):
        starts = []

        def respond(request):
            starts.append(time.monotonic())
            offset = int(parse_qs(urlsplit(str(request.url)).query)["offset"][0])
            return httpx.Response(200, json={
                "status_code": 0, "next_offset": 1 if offset == 0 else 0, "total": 2,
                "promotions": [{"promotion_id": f"p{offset}", "title": "商品"}],
            })

        client = httpx.Client(transport=httpx.MockTransport(respond))
        from app.douyin_product_events.promotions import DouyinPromotionNameResolver
        resolver = DouyinPromotionNameResolver(
            RoomInfo("live-id", "9001", "8002"), "Mozilla/5.0 Chrome/133.0.0.0",
            client=client, min_request_interval=0.02, retry_delay=0,
        )
        try:
            resolver.lookup_catalog(page_size=1)
        finally:
            client.close()
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.015)

    def test_sm3_and_a_bogus_output_shape(self):
        self.assertEqual(
            sm3_digest(b"abc").hex(),
            "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0",
        )
        # Fixed-clock reference vector from streamget's Douyin ab_sign implementation.
        signature = a_bogus(
            "aid=6383&app_name=douyin_web&room_id=123456789",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            now_ms=1_700_000_000_000,
        )
        self.assertEqual(
            signature,
            "E7mhBmg6mEVNgf6X56KLfY3q6333YlQI0HViMD2fJV3MqL39HMYD9exoIBGvXKWjwG/"
            "-IeYjy4hbO3xprQAjM36UHWwEUdQ2mgWkKl5Q5I0j53iruyRDntmF4vj3SFlm5XNAEOk0y"
            "75rKb70Woqe-vIlO62-zo0/9Vu=",
        )

    def test_resolves_room_ids_and_short_link_redirect_targets(self):
        self.assertEqual(live_id_from_url("https://live.douyin.com/abc_123"), "abc_123")
        self.assertEqual(live_id_from_url("live.douyin.com/12345"), "12345")
        with self.assertRaises(ValueError):
            live_id_from_url("https://v.douyin.com/abc123/")

    def test_room_enter_response_resolves_room_and_anchor_identity(self):
        room = parse_room_enter_response(
            '{"status_code":0,"data":{"data":[{"id_str":"9001","status":2,'
            '"title":"直播标题","owner":{"id_str":"8002","nickname":"主播"}}]}}',
            "share_id",
        )
        self.assertEqual((room.room_id, room.user_unique_id), ("9001", "8002"))
        self.assertEqual((room.anchor_name, room.title, room.is_live), ("主播", "直播标题", True))

    def test_query_builders_keep_required_parameters_and_escape_tokens(self):
        room = RoomInfo("share", "9001", "8002")
        enter = build_room_enter_query("share", "Mozilla/5.0 Chrome/133.0.0.0", "a b")
        initial = build_initial_im_query(room, "Mozilla/5.0 Chrome/133.0.0.0", "token")
        self.assertEqual(
            enter,
            "aid=6383&app_name=douyin_web&live_id=1&device_platform=web&language=zh-CN&"
            "browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome&"
            "browser_version=133.0.0.0&web_rid=share&is_need_double_stream=false&msToken=a+b",
        )
        self.assertIn("web_rid=share", enter)
        self.assertIn("msToken=a+b", enter)
        self.assertIn("user_unique_id=8002", initial)
        self.assertIn("room_id=9001", initial)
        self.assertIn("resp_content_type=protobuf", initial)

    def test_initial_im_response_extracts_push_state_and_builds_signed_ws_url(self):
        room = RoomInfo("share", "9001", "8002")
        raw = bytes_field(2, b"cursor-2") + bytes_field(5, b"internal-state")
        raw += bytes_field(14, b"https://push.example.com") + integer_field(8, 10)
        state = parse_initial_im_response(raw, room, now_ms=1_800_000_000_000)
        self.assertEqual(state.push_url, "wss://push.example.com/webcast/im/push/v2/")
        self.assertEqual((state.cursor, state.internal_ext, state.heartbeat_duration),
                         ("cursor-2", "internal-state", 10))

        values = iter((1, 2, 3))
        url = build_websocket_url(
            room, state, "Mozilla/5.0 Chrome/133.0.0.0",
            WebSocketSignatureGenerator(lambda: next(values)),
        )
        self.assertTrue(url.startswith("wss://push.example.com/webcast/im/push/v2/?"))
        self.assertIn("cursor=cursor-2", url)
        self.assertIn("internal_ext=internal-state", url)
        self.assertIn("user_unique_id=8002", url)
        self.assertRegex(url, r"&signature=.{16}$")

    def test_push_url_priority_normalization_and_default(self):
        self.assertEqual(
            normalize_push_url("http://push.example.com/path"),
            "ws://push.example.com/path/webcast/im/push/v2/",
        )
        room = RoomInfo("share", "9001", "8002")
        self.assertEqual(
            parse_initial_im_response(b"", room, now_ms=1000).push_url,
            "wss://webcast100-ws-web-lf.douyin.com/webcast/im/push/v2/",
        )

    def test_preparer_builds_session_from_room_and_initial_im_responses(self):
        class FakeResponse:
            def __init__(self, content, url):
                self.content = content
                self.url = url
                self.is_redirect = False
                self.headers = {}

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self):
                self.cookies = types.SimpleNamespace(jar=[])
                self.calls = []
                self.responses = [
                    FakeResponse(b"<html>room</html>", "https://live.douyin.com/share-id"),
                    FakeResponse(
                        b'{"status_code":0,"data":{"data":[{"id_str":"9001",'
                        b'"status":2,"owner":{"id_str":"8002"}}]}}',
                        "https://live.douyin.com/webcast/room/web/enter/",
                    ),
                    FakeResponse(
                        bytes_field(2, b"cursor") + bytes_field(5, b"internal")
                        + bytes_field(14, b"wss://push.example.com"),
                        "https://live.douyin.com/webcast/im/fetch/",
                    ),
                ]

            def get(self, url, *, headers):
                self.calls.append((url, headers))
                return self.responses.pop(0)

        http = FakeClient()
        preparer = DouyinWebcastSessionPreparer(
            cookie="sessionid=test-cookie",
            user_agent="Mozilla/5.0 Chrome/133.0.0.0",
            client=http,
        )

        session = preparer.prepare("https://live.douyin.com/share-id")

        self.assertEqual(session.room.room_id, "9001")
        self.assertEqual(session.room.user_unique_id, "8002")
        self.assertIn("cursor=cursor", session.websocket_url)
        self.assertTrue(session.websocket_url.startswith("wss://push.example.com/"))
        self.assertEqual(len(http.calls), 3)
        self.assertIn("/share-id", http.calls[0][0])
        self.assertIn("/webcast/room/web/enter/", http.calls[1][0])
        self.assertIn("/webcast/im/fetch/", http.calls[2][0])
        self.assertTrue(all("a_bogus=" in call[0] for call in http.calls[1:]))
        self.assertTrue(all(call[1]["Cookie"] == "sessionid=test-cookie" for call in http.calls))
        self.assertIn("msToken=", http.calls[1][0])
        self.assertNotIn("msToken=ey", http.calls[1][0])

    def test_short_link_redirects_are_checked_before_following(self):
        class RedirectResponse:
            is_redirect = True
            headers = {"location": "https://evil.example/room"}

        class FakeClient:
            def __init__(self):
                self.cookies = types.SimpleNamespace(jar=[])
                self.requested = []

            def get(self, url, *, headers):
                self.requested.append((url, headers))
                return RedirectResponse()

        http = FakeClient()
        preparer = DouyinWebcastSessionPreparer(cookie="sessionid=secret", client=http)
        with self.assertRaisesRegex(ValueError, "非 HTTPS 抖音域名"):
            preparer._resolve_live_id("https://v.douyin.com/share/")
        self.assertEqual(len(http.requested), 1)
        self.assertEqual(http.requested[0][1]["Cookie"], "sessionid=secret")

    def test_preparer_rejects_non_douyin_urls_before_request(self):
        class FakeClient:
            def __init__(self):
                self.cookies = types.SimpleNamespace(jar=[])
                self.called = False

            def get(self, *_args, **_kwargs):
                self.called = True
                raise AssertionError("unexpected request")

        http = FakeClient()
        preparer = DouyinWebcastSessionPreparer(client=http)
        with self.assertRaisesRegex(ValueError, "仅支持 HTTPS 抖音"):
            preparer.prepare("https://live.douyin.com.evil.example/room")
        self.assertFalse(http.called)

    def test_websocket_query_percent_encodes_signature_and_state(self):
        room = RoomInfo("share", "9001", "8002")
        state = InitialIMState(
            "wss://push.example.com/webcast/im/push/v2/",
            "cursor+with/slash=",
            "internal + / =",
        )

        class FixedSigner:
            def sign(self, _room_id, _user_unique_id):
                return "signature+/="

        url = build_websocket_url(room, state, "Mozilla/5.0 Chrome/133.0.0.0", FixedSigner())
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        self.assertEqual(query["cursor"], [state.cursor])
        self.assertEqual(query["internal_ext"], [state.internal_ext])
        self.assertEqual(query["signature"], ["signature+/="])


class DouyinProductClientTests(unittest.TestCase):
    def test_starts_from_prepared_session_headers_and_url(self):
        created = []

        class FakeWebSocketApp:
            def __init__(self, url, *, header, **callbacks):
                self.url = url
                self.header = header
                self.callbacks = callbacks
                created.append(self)

            def run_forever(self, **_kwargs):
                return None

            def close(self):
                return None

        fake_websocket = types.ModuleType("websocket")
        fake_websocket.WebSocketApp = FakeWebSocketApp
        fake_websocket.ABNF = types.SimpleNamespace(OPCODE_BINARY=2)
        session = PreparedWebSocketSession(
            RoomInfo("share", "9001", "8002"),
            "wss://push.example.com/webcast/im/push/v2/?signature=x",
            "sessionid=cookie-value",
            "TestBrowser/1.0",
            InitialIMState("wss://push.example.com", "cursor", "ext"),
        )
        client = DouyinProductEventClient(lambda _event: None)

        with patch.dict(sys.modules, {"websocket": fake_websocket}):
            client.start_prepared(session)
            client._thread.join(1)
            client.stop()

        self.assertEqual(created[0].url, session.websocket_url)
        self.assertIn("User-Agent: TestBrowser/1.0", created[0].header)
        self.assertIn("Cookie: sessionid=cookie-value", created[0].header)

    def test_probe_diagnostics_hide_signed_query_strings(self):
        self.assertEqual(
            _safe_url("wss://push.example.com/path?signature=secret&cursor=private"),
            "wss://push.example.com/path?[query omitted]",
        )
        safe = _safe_text("websocket error at wss://push.example.com/path?signature=secret")
        self.assertNotIn("signature=secret", safe)
        self.assertIn("[query omitted]", safe)

    def test_session_error_includes_http_diagnostics_without_query(self):
        response = httpx.Response(
            200,
            content=b"<html>verify</html>",
            headers={"content-type": "text/html", "x-tt-logid": "trace-123"},
            request=httpx.Request(
                "GET", "https://live.douyin.com/webcast/room/web/enter/?a_bogus=secret"
            ),
        )
        error = _response_error("room_enter", response, "invalid JSON")
        diagnostic = json.loads(_safe_exception(error))
        self.assertEqual(diagnostic["stage"], "room_enter")
        self.assertEqual(diagnostic["http_status"], 200)
        self.assertEqual(diagnostic["content_type"], "text/html")
        self.assertEqual(diagnostic["request_id"], "trace-123")
        self.assertNotIn("a_bogus=secret", diagnostic["endpoint"])
        self.assertEqual(diagnostic["response_preview"], "<html>verify</html>")

    def test_invalid_frame_reports_short_hex_prefix_without_disconnect_state(self):
        states = []

        class FakeWebSocketApp:
            def __init__(self, _url, *, on_message, **_callbacks):
                self.on_message = on_message

            def run_forever(self, **_kwargs):
                self.on_message(self, b"\x1f\x8b\x08\x00unexpected-frame")

            def close(self):
                return None

        fake_websocket = types.ModuleType("websocket")
        fake_websocket.WebSocketApp = FakeWebSocketApp
        fake_websocket.ABNF = types.SimpleNamespace(OPCODE_BINARY=2)
        client = DouyinProductEventClient(
            lambda _event: None,
            on_state=lambda state, detail: states.append((state, detail)),
        )

        with patch.dict(sys.modules, {"websocket": fake_websocket}):
            client.start("wss://push.example.com/path")
            client._thread.join(1)
            client.stop()

        frame_error = next(detail for state, detail in states if state == "frame_error")
        self.assertIn("bytes=20", frame_error)
        self.assertIn("hex_prefix=1f 8b 08 00", frame_error)
        self.assertNotIn("error", [state for state, _ in states])

    def test_valid_frame_reports_methods_and_acknowledgement(self):
        states = []
        sent = []
        message = bytes_field(1, b"WebcastChatMessage") + bytes_field(2, b"")
        sample_payload = integer_field(1, 123) + bytes_field(2, b"product")
        shopping_message = bytes_field(1, b"WebcastLiveShoppingMessage")
        shopping_message += bytes_field(2, sample_payload) + integer_field(3, 88)
        response = bytes_field(1, message) + bytes_field(1, shopping_message)
        response += bytes_field(5, b"cursor") + integer_field(9, 1)
        frame = integer_field(2, 321) + bytes_field(6, b"gzip")
        frame += bytes_field(8, gzip.compress(response))

        class FakeWebSocketApp:
            def __init__(self, _url, *, on_open, on_message, **_callbacks):
                self.on_open = on_open
                self.on_message = on_message

            def run_forever(self, **_kwargs):
                self.on_open(self)
                self.on_message(self, frame)

            def send(self, payload, *, opcode):
                sent.append((payload, opcode))

            def close(self):
                return None

        fake_websocket = types.ModuleType("websocket")
        fake_websocket.WebSocketApp = FakeWebSocketApp
        fake_websocket.ABNF = types.SimpleNamespace(OPCODE_BINARY=2)
        client = DouyinProductEventClient(
            lambda _event: None,
            on_state=lambda state, detail: states.append((state, detail)),
        )

        with patch.dict(sys.modules, {"websocket": fake_websocket}):
            client.start("wss://push.example.com/path")
            client._thread.join(1)
            client.stop()

        frame_detail = json.loads(next(
            detail for state, detail in states if state == "frame_received"
        ))
        self.assertEqual(frame_detail["methods"], [
            "WebcastChatMessage", "WebcastLiveShoppingMessage",
        ])
        sample = frame_detail["samples"][0]
        self.assertEqual(sample["method"], "WebcastLiveShoppingMessage")
        self.assertEqual(sample["fields"][0]["number"], 1)
        self.assertEqual(
            [(item["path"], item["value"]) for item in sample["values"]],
            [("1", 123), ("2", "product")],
        )
        self.assertTrue(frame_detail["need_ack"])
        self.assertTrue(frame_detail["ack_sent"])
        self.assertEqual(len(sent), 1)

    def test_live_shopping_id_change_requires_live_pop_confirmation(self):
        now = time.time()

        def shopping(product_id, timestamp, message_id):
            common = bytes_field(1, b"WebcastLiveShoppingMessage")
            common += integer_field(4, int(timestamp * 1000))
            payload = bytes_field(1, common) + integer_field(2, 2) + integer_field(3, product_id)
            return push_message("WebcastLiveShoppingMessage", payload, message_id)

        def live_pop(product_id, message_id):
            payload = bytes_field(2, b"LivePopMessage") + bytes_field(3, b"application/vnd.google.protobuf")
            payload += bytes_field(6, integer_field(3, product_id))
            return push_message("WebcastLiveEcomGeneralMessage", payload, message_id)

        changed_at = now + 1
        frames = [
            push_frame([shopping(101, now, 1), live_pop(101, 1)]),
            push_frame([shopping(202, changed_at, 2)]),
            push_frame([live_pop(202, 3)]),
            push_frame([shopping(202, changed_at + 1, 4), live_pop(202, 4)]),
        ]
        events = []
        current_products = []

        class FakeWebSocketApp:
            def __init__(self, _url, *, on_open, on_message, **_callbacks):
                self.on_open = on_open
                self.on_message = on_message

            def run_forever(self, **_kwargs):
                self.on_open(self)
                for frame in frames:
                    self.on_message(self, frame)

            def close(self):
                return None

        fake_websocket = types.ModuleType("websocket")
        fake_websocket.WebSocketApp = FakeWebSocketApp
        fake_websocket.ABNF = types.SimpleNamespace(OPCODE_BINARY=2)
        client = DouyinProductEventClient(events.append, on_current_product=current_products.append)

        with patch.dict(sys.modules, {"websocket": fake_websocket}):
            client.start("wss://push.example.com/path")
            client._thread.join(1)
            client.stop()

        self.assertEqual(len(events), 1)
        self.assertEqual([event.promotion_id for event in current_products], ["101"])
        self.assertEqual(events[0].promotion_id, "202")
        self.assertEqual(events[0].message_id, 2)
        self.assertAlmostEqual(events[0].event_time, changed_at, places=2)
        self.assertIsNone(events[0].explain_type)

    def test_replay_summarizes_sanitized_jsonl_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.jsonl"
            sample = {
                "session_id": "session-test",
                "method": "WebcastLiveShoppingMessage",
                "received_at": 1_800_000_000,
                "summary": {"method": "WebcastLiveShoppingMessage", "bytes": 12,
                            "fields": [{"number": 1, "wire_type": 0, "kind": "integer"}]},
            }
            path.write_text(json.dumps(sample) + "\n" + json.dumps(sample) + "\n", encoding="utf-8")
            self.assertEqual(replay_samples(path), {
                "samples": 2,
                "methods": {"WebcastLiveShoppingMessage": 2},
                "unique_shapes": {"WebcastLiveShoppingMessage": 1},
            })

    def test_message_sample_omits_text_and_scalar_values(self):
        sample = _message_sample(
            "WebcastLiveShoppingMessage",
            bytes_field(1, b"secret product text") + integer_field(2, 987654321),
        )
        encoded = json.dumps(sample)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("987654321", encoded)
        self.assertEqual(sample["fields"][0]["kind"], "text")
        self.assertEqual(sample["fields"][1]["kind"], "integer")

    def test_diagnostic_sample_keeps_bounded_text_and_nested_scalar_values(self):
        nested = integer_field(1, 77) + bytes_field(2, "独束针织开衫".encode())
        sample = _message_sample(
            "WebcastLiveEcomGeneralMessage",
            integer_field(1, 123456) + bytes_field(6, nested) + bytes_field(7, b"\x00\xff"),
            include_values=True,
        )
        self.assertEqual(sample["diagnostic_version"], 1)
        self.assertEqual(sample["values"], [
            {"path": "1", "kind": "integer", "value": 123456},
            {"path": "6.1", "kind": "integer", "value": 77},
            {"path": "6.2", "kind": "text", "value": "独束针织开衫"},
        ])

    def test_diagnostic_sample_truncates_long_text(self):
        sample = _message_sample("WebcastLiveEcomGeneralMessage", bytes_field(2, b"x" * 200), include_values=True)
        self.assertEqual(sample["values"], [
            {"path": "2", "kind": "text", "value": "x" * 160, "truncated": True},
        ])


if __name__ == "__main__":
    unittest.main()
