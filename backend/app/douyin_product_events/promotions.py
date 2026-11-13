from __future__ import annotations

import json
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import quote, urlencode

import httpx

from .session import LIVE_ORIGIN, RoomInfo, _chrome_version, _query_escape, normalize_cookie_header
from .webcast_http import a_bogus


PROMOTIONS_PAGE_ENDPOINT = f"{LIVE_ORIGIN}/live/promotions/page/"
PROMOTIONS_REQUEST_INTERVAL = 1.0
PROMOTIONS_REQUEST_ATTEMPTS = 3
_promotions_rate_lock = threading.Lock()
_promotions_last_request_at = 0.0


def build_promotions_page_query(room: RoomInfo, user_agent: str, cookie: str = "", *,
                                offset: int = 0, limit: int = 20, webid: str = "") -> str:
    parsed = SimpleCookie()
    parsed.load(normalize_cookie_header(cookie))
    ms_token = parsed.get("msToken").value if parsed.get("msToken") else ""
    uifid = parsed.get("UIFID").value if parsed.get("UIFID") else ""
    browser_version = _chrome_version(user_agent)
    parts = (
        ("device_platform", "webapp"), ("aid", "6383"),
        ("channel", "channel_pc_web"), ("room_id", room.room_id),
        ("author_id", room.user_unique_id), ("offset", str(offset)),
        ("limit", str(limit)), ("pc_client_type", "1"),
        ("pc_libra_divert", "Windows"), ("update_version_code", "170400"),
        ("support_h265", "1"), ("support_dash", "1"),
        ("version_code", "320100"), ("version_name", "32.1.0"),
        ("cookie_enabled", "true"), ("screen_width", "1920"),
        ("screen_height", "1080"), ("browser_language", "zh-CN"),
        ("browser_platform", "Win32"), ("browser_name", "Chrome"),
        ("browser_version", browser_version), ("browser_online", "true"),
        ("engine_name", "Blink"), ("engine_version", browser_version),
        ("os_name", "Windows"), ("os_version", "10"),
        ("cpu_core_num", "8"), ("device_memory", "8"),
        ("downlink", "10"), ("effective_type", "4g"),
        ("round_trip_time", "50"),
        ("platform", "PC"), ("webid", webid),
        ("uifid", uifid), ("msToken", ms_token),
    )
    return urlencode(parts, quote_via=quote, safe="-_.~")


def parse_promotions_page(payload: bytes | str) -> tuple[dict[str, str], int, int, tuple[str, str] | None, int]:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("抖音商品列表接口返回错误或无效 JSON")
    try:
        status_code = int(data["status_code"])
        next_offset = int(data.get("next_offset", 0) or 0)
        total = int(data.get("total", 0) or 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("抖音商品列表接口返回错误或无效 JSON") from exc
    if status_code != 0:
        raise ValueError("抖音商品列表接口返回错误或无效 JSON")
    promotions = data.get("promotions")
    if not isinstance(promotions, list):
        raise ValueError("抖音商品列表响应缺少 promotions 列表")
    names: dict[str, str] = {}
    for item in promotions:
        if not isinstance(item, dict):
            continue
        promotion_id = str(item.get("promotion_id") or item.get("product_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if promotion_id and title:
            names[promotion_id] = title[:500]
    top = data.get("top_promotion")
    current = None
    if isinstance(top, dict):
        promotion_id = str(top.get("promotion_id") or top.get("product_id") or "").strip()
        title = str(top.get("title") or "").strip()[:500]
        if promotion_id:
            current = (promotion_id, title or names.get(promotion_id, ""))
            if current[1]:
                names.setdefault(promotion_id, current[1])
    return names, next_offset, total, current, len(promotions)


class DouyinPromotionNameResolver:
    def __init__(self, room: RoomInfo, user_agent: str, cookie: str = "", *,
                 timeout: float = 8, client: httpx.Client | None = None,
                 min_request_interval: float = PROMOTIONS_REQUEST_INTERVAL,
                 retry_delay: float = 0.5):
        self.room = room
        self.user_agent = user_agent
        self.cookie = normalize_cookie_header(cookie)
        self.timeout = timeout
        self.min_request_interval = max(0.0, min_request_interval)
        self.retry_delay = max(0.0, retry_delay)
        self._client = client or httpx.Client(follow_redirects=False, timeout=timeout, http2=True)
        self._owns_client = client is None

    def lookup_catalog(self, *, page_size: int = 100) -> tuple[dict[str, str], tuple[str, str] | None]:
        if page_size < 1 or page_size > 100:
            raise ValueError("商品列表分页大小无效")
        names: dict[str, str] = {}
        current = None
        offset = 0
        collected_items = 0
        seen_offsets: set[int] = set()
        while True:
            if offset in seen_offsets:
                raise ValueError("抖音商品列表分页位置重复")
            seen_offsets.add(offset)
            page, next_offset, total, top, item_count, response_detail = self._fetch_page(offset, page_size)
            names.update(page)
            collected_items += item_count
            if current is None and top is not None:
                current = top
            if total <= 0 or collected_items >= total or next_offset >= total or (
                next_offset <= offset and item_count < page_size
            ):
                break
            if item_count == 0 or next_offset <= offset:
                raise PromotionCatalogRequestError({
                    "summary": "抖音商品列表分页游标未前进，目录未完整获取",
                    "stage": "pagination",
                    "pagination": {
                        "offset": offset, "next_offset": next_offset,
                        "total": total, "page_item_count": item_count,
                        "collected_items": collected_items,
                    },
                    **response_detail,
                    "reason": "接口返回的 next_offset 未大于当前 offset；为避免用不完整目录覆盖缓存，已中止刷新",
                })
            offset = next_offset
        return names, current

    def lookup_current(self) -> tuple[str, str] | None:
        """Read only the room's top promotion with a single request.

        One page is enough: `top_promotion` is what the host is explaining now, which is
        what a session needs when no WebSocket product message ever arrives. This never
        pages and never replaces the cached catalog, and it is not retried so a failing
        probe cannot hold up the message listener.
        """
        _, _, _, current, _, _ = self._fetch_page_once(0, 1)
        return current

    def _fetch_page(self, offset: int, limit: int):
        failures = []
        for attempt in range(1, PROMOTIONS_REQUEST_ATTEMPTS + 1):
            try:
                result = self._fetch_page_once(offset, limit)
                result[-1]["attempts"] = attempt
                if failures:
                    result[-1]["previous_attempts"] = failures
                return result
            except PromotionCatalogRequestError as exc:
                detail = exc.detail
                failures.append(_attempt_summary(attempt, detail))
                if attempt >= PROMOTIONS_REQUEST_ATTEMPTS or not _retryable_catalog_error(detail):
                    detail["attempts"] = attempt
                    if len(failures) > 1:
                        detail["previous_attempts"] = failures[:-1]
                    detail["summary"] = f"{detail.get('summary', '抖音商品列表请求失败')}（已请求 {attempt} 次）"
                    raise
                time.sleep(self.retry_delay * attempt)
        raise AssertionError("商品列表请求重试循环异常结束")

    def _wait_for_request_slot(self):
        global _promotions_last_request_at
        with _promotions_rate_lock:
            now = time.monotonic()
            wait_seconds = self.min_request_interval - (now - _promotions_last_request_at)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            _promotions_last_request_at = time.monotonic()

    def _fetch_page_once(self, offset: int, limit: int):
        query = build_promotions_page_query(
            self.room, self.user_agent, self.cookie, offset=offset, limit=limit,
            webid=_cookie_value(self.cookie, "webid"),
        )
        signature = _query_escape(a_bogus(query, self.user_agent))
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": LIVE_ORIGIN,
            "Referer": f"{LIVE_ORIGIN}/{self.room.live_id}",
            "Accept-Encoding": "identity",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
            uifid = _cookie_value(self.cookie, "UIFID")
            csrf_token = (_cookie_value(self.cookie, "x-secsdk-csrf-token")
                          or _cookie_value(self.cookie, "csrf_session_id")
                          or _cookie_value(self.cookie, "passport_csrf_token_default")
                          or _cookie_value(self.cookie, "passport_csrf_token"))
            if uifid:
                headers["uifid"] = uifid
            if csrf_token:
                headers["x-secsdk-csrf-token"] = csrf_token
        request_url = f"{PROMOTIONS_PAGE_ENDPOINT}?{query}&a_bogus={signature}"
        try:
            self._wait_for_request_slot()
            response = self._client.post(request_url, content=b"", headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PromotionCatalogRequestError(_http_error_detail(
                exc.response, self.room.room_id, offset, limit,
                cookie_sent=bool(self.cookie), csrf_sent=bool(headers.get("x-secsdk-csrf-token")),
            )) from exc
        except httpx.RequestError as exc:
            raise PromotionCatalogRequestError({
                "summary": "抖音商品列表请求未取得 HTTP 响应",
                "stage": "transport",
                "request": {
                    "method": "POST", "endpoint": PROMOTIONS_PAGE_ENDPOINT,
                    "room_id": self.room.room_id, "offset": offset, "limit": limit,
                    "body_bytes": 0, "cookie_sent": bool(self.cookie),
                    "uifid_sent": bool(_cookie_value(self.cookie, "UIFID")),
                    "csrf_header_sent": bool(headers.get("x-secsdk-csrf-token")),
                },
                "reason": f"{type(exc).__name__}: {str(exc)[:1000]}",
            }) from exc
        try:
            parsed = parse_promotions_page(response.content)
        except (ValueError, json.JSONDecodeError) as exc:
            raise PromotionCatalogRequestError(_http_error_detail(
                response, self.room.room_id, offset, limit,
                cookie_sent=bool(self.cookie), csrf_sent=bool(headers.get("x-secsdk-csrf-token")),
                reason=f"{type(exc).__name__}: {str(exc)[:500]}",
            )) from exc
        names, next_offset, total, current, item_count = parsed
        return names, next_offset, total, current, item_count, _http_response_detail(
            response, self.room.room_id, offset, limit,
            cookie_sent=bool(self.cookie), csrf_sent=bool(headers.get("x-secsdk-csrf-token")),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class PromotionCatalogRequestError(RuntimeError):
    def __init__(self, detail: dict):
        super().__init__(str(detail.get("summary") or "抖音商品列表请求失败"))
        self.detail = detail


def _http_error_detail(response: httpx.Response, room_id: str, offset: int, limit: int,
                       *, cookie_sent: bool, csrf_sent: bool, reason: str = "") -> dict:
    detail = _http_response_detail(
        response, room_id, offset, limit,
        cookie_sent=cookie_sent, csrf_sent=csrf_sent,
    )
    detail.update({
        "summary": "抖音商品列表接口返回错误响应" if response.is_error else "抖音商品列表响应格式无效",
        "stage": "http_response" if response.is_error else "response_parse",
        "reason": reason or None,
    })
    return detail


def _http_response_detail(response: httpx.Response, room_id: str, offset: int, limit: int,
                          *, cookie_sent: bool, csrf_sent: bool) -> dict:
    request_id = next((response.headers.get(name, "") for name in (
        "x-tt-logid", "x-tt-trace-id", "x-tt-request-id", "request-id",
    ) if response.headers.get(name)), "")
    return {
        "request": {
            "method": "POST", "endpoint": PROMOTIONS_PAGE_ENDPOINT,
            "room_id": room_id, "offset": offset, "limit": limit,
            "body_bytes": 0, "cookie_sent": cookie_sent,
            "webid_sent": bool(response.request.url.params.get("webid")),
            "uifid_sent": bool(response.request.url.params.get("uifid")),
            "csrf_header_sent": csrf_sent,
        },
        "response": {
            "http_status": response.status_code, "request_id": request_id or None,
            "content_type": response.headers.get("content-type"),
            "body": response.text[:4000] or "<empty>",
        },
    }


def _cookie_value(cookie: str, key: str) -> str:
    parsed = SimpleCookie()
    parsed.load(normalize_cookie_header(cookie))
    morsel = parsed.get(key)
    return morsel.value if morsel else ""


def _retryable_catalog_error(detail: dict) -> bool:
    stage = detail.get("stage")
    if stage == "transport":
        return True
    response = detail.get("response") or {}
    status = response.get("http_status")
    if status in {408, 429} or (isinstance(status, int) and status >= 500):
        return True
    return (
        stage == "response_parse"
        and response.get("body") == "<empty>"
        and "JSONDecodeError" in str(detail.get("reason", ""))
    )


def _attempt_summary(attempt: int, detail: dict) -> dict:
    response = detail.get("response") or {}
    return {
        "attempt": attempt,
        "stage": detail.get("stage"),
        "http_status": response.get("http_status"),
        "request_id": response.get("request_id"),
        "reason": detail.get("reason"),
        "body": response.get("body"),
    }
