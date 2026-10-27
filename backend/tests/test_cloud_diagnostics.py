import io
import http.client
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.core.domain import Asset, Transcript
from app.ai.providers import (CloudConfig, ProviderError, ProviderHTTPError, cloud_json,
                           transcribe)
from app.ai.roughcut import RoughcutRequest, select_intervals
from app.core.storage import Store
from app.worker import Worker


class FakeResponse(io.BytesIO):
    def __init__(self, body, content_type="text/html", status=200):
        super().__init__(body.encode())
        self.status = status
        self.headers = {"Content-Type": content_type, "X-Request-Id": "request-test"}


class CloudDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name))
        self.settings.prepare()
        self.store = Store(self.settings.database)
        self.asset = Asset(id="test-asset", sha256="test-digest", path="source.mp4", name="测试",
                           duration_us=10_000_000, width=320, height=240, has_audio=True)
        self.config = CloudConfig()
        self.transcripts = [Transcript(source_asset_id=self.asset.id, start_us=0, end_us=5000000, text="面料柔软。")]
        self.request = RoughcutRequest(product_name="测试")

    def test_nonjson_response_preserves_full_body_and_metadata(self):
        body = "<html>502 Bad Gateway\n" + "中文内容" * 3000 + "<script>never execute</script></html>"
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse(body)
            with self.assertRaises(ProviderError) as raised:
                cloud_json("POST", self.config.asr_url, {"model": self.config.asr_model}, "mock-key")
        detail = raised.exception.details
        self.assertEqual(detail["response_body"], body)
        self.assertEqual(detail["stage"], "json_decode")
        self.assertEqual(detail["http_status"], 200)
        self.assertEqual(detail["content_type"], "text/html")
        self.assertEqual(detail["request_id"], "request-test")
        self.assertEqual(detail["model"], self.config.asr_model)

    def test_http_error_preserves_body_without_credential_echo(self):
        body = json.dumps({"code": "InvalidApiKey", "message": "mock-key",
                           "audio": "https://oss.example/audio.wav?Signature=secret-signature&AccessKeyId=id",
                           "authorization": "Bearer another-private-key"})
        error = urllib.error.HTTPError("https://service.example", 401, "Unauthorized",
                                       {"X-Request-Id": "http-request-id", "Content-Type": "application/json"},
                                       io.BytesIO(body.encode()))
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(ProviderHTTPError) as raised:
                cloud_json("POST", self.config.asr_url, {"model": "model-test"}, "mock-key")
        detail = raised.exception.details
        self.assertEqual(detail["http_status"], 401)
        self.assertEqual(detail["request_id"], "http-request-id")
        self.assertIn("InvalidApiKey", detail["response_body"])
        for secret in ("mock-key", "secret-signature", "another-private-key"):
            self.assertNotIn(secret, json.dumps(detail))

    def test_asr_http_error_includes_sanitized_request_details(self):
        payload = {
            "model": "qwen-audio-3.1-asr-flash",
            "input": {"messages": [{"role": "user", "content": [{
                "type": "input_audio",
                "input_audio": {"data": "https://oss.example/audio.wav?Signature=private"},
            }]}]},
            "parameters": {"format": "wav", "sample_rate": "16000"},
        }
        error = urllib.error.HTTPError("https://service.example", 400, "Bad Request",
                                       {"X-Request-Id": "audio-request", "Content-Type": "application/json"},
                                       io.BytesIO(b'{"code":"NO_VALID_AUDIO_ERROR"}'))
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(ProviderHTTPError) as raised:
                cloud_json("POST", self.config.asr_url, payload, "mock-key", sse=False, operation="asr")
        detail = raised.exception.details
        self.assertEqual(detail["request_method"], "POST")
        self.assertEqual(detail["request_headers"]["Content-Type"], "application/json")
        self.assertEqual(detail["request_headers"]["X-DashScope-SSE"], "disable")
        self.assertEqual(detail["request_headers"]["Authorization"], "[已隐藏密钥]")
        self.assertIn('"qwen-audio-3.1-asr-flash"', detail["request_body"])
        self.assertIn('"sample_rate": "16000"', detail["request_body"])
        self.assertIn("https://oss.example/audio.wav?Signature=private", detail["request_body"])
        self.assertNotIn("mock-key", json.dumps(detail))

    def test_connection_and_timeout_errors_are_distinct_with_no_response(self):
        for reason, stage, message in ((TimeoutError("timed out"), "timeout", "连接超时"),
                                       (OSError("DNS lookup failed"), "connection_error", "连接失败")):
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = urllib.error.URLError(reason)
                with self.assertRaisesRegex(ProviderError, message) as raised:
                    cloud_json("POST", self.config.asr_url, {}, "mock-key")
            self.assertEqual(raised.exception.details["stage"], stage)
            self.assertIsNone(raised.exception.details["response_body"])
            self.assertIn(str(reason), raised.exception.details["reason"])

    def test_remote_disconnect_is_transport_exception_not_a_cloud_error_body(self):
        for failure in (http.client.RemoteDisconnected("Remote end closed connection without response"),
                        urllib.error.URLError(http.client.RemoteDisconnected("Remote end closed connection without response"))):
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaisesRegex(ProviderError, "返回 HTTP 响应前断开") as raised:
                    cloud_json("POST", self.config.chat_url, {"model": "qwen3.8-flash"}, "mock-key",
                               timeout_seconds=300, operation="vision")
            detail = raised.exception.details
            self.assertEqual(detail["error_source"], "transport")
            self.assertEqual(detail["stage"], "remote_disconnected")
            self.assertEqual(detail["exception_type"], "RemoteDisconnected")
            self.assertEqual(detail["reason"], "RemoteDisconnected: Remote end closed connection without response")
            self.assertIsNone(detail["response_body"])
            self.assertIsNone(detail["http_status"])
            self.assertEqual(detail["operation"], "vision")

    def test_http_and_parsed_response_sources_are_distinguished(self):
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse("service unavailable", status=503)
            with self.assertRaises(ProviderError) as raised:
                cloud_json("POST", self.config.chat_url, {}, "mock-key")
        self.assertEqual(raised.exception.details["error_source"], "http_response")
        self.assertEqual(raised.exception.details["response_body"], "service unavailable")
        with patch("app.ai.roughcut.model_api_key", return_value="mock-key"), \
                patch("app.ai.roughcut.cloud_json", return_value={"error": "invalid response format"}):
            with self.assertRaises(ProviderError) as raised:
                select_intervals(self.transcripts, self.request, self.config, self.asset.duration_us)
        self.assertEqual(raised.exception.details["error_source"], "response_validation")

    def test_chat_timeout_reaches_transport_and_diagnostics_without_retry(self):
        payload = {"model": "qwen3.8-flash", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "analyze"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,private-image"}}
        ]}]}
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError("The read operation timed out")
            with self.assertRaisesRegex(ProviderError, "300 秒") as raised:
                cloud_json("POST", self.config.chat_url, payload, "mock-key",
                           timeout_seconds=300, operation="vision")
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 300)
            self.assertEqual(opener.return_value.open.call_count, 1)
        detail = raised.exception.details
        self.assertEqual(detail["operation"], "vision")
        self.assertEqual(detail["timeout_seconds"], 300)
        self.assertEqual(detail["image_count"], 1)
        self.assertGreater(detail["request_bytes"], 0)
        self.assertNotIn("private-image", json.dumps(detail))
        self.assertIsNone(detail["http_status"])


    def test_planner_uses_configured_timeout_and_bounded_output(self):
        config = self.config.model_copy(update={"chat_timeout_seconds": 600})
        with patch("app.ai.roughcut.model_api_key", return_value="mock-key"), \
                patch("app.ai.roughcut.cloud_json", return_value={"choices": [{"message": {"content": '{"clips":[{"start_ms":0,"end_ms":5000,"reason":"面料"}]}'}}]}) as network:
            self.assertEqual(select_intervals(self.transcripts, self.request, config, self.asset.duration_us).clips[0].end_ms, 5000)
        self.assertEqual(network.call_args.kwargs, {"timeout_seconds": 600, "operation": "planner"})
        self.assertFalse(network.call_args.args[2]['stream'])
        self.assertEqual(network.call_args.args[2]['max_tokens'], 4096)

    def test_generic_transport_default_timeout_is_90_seconds(self):
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse('{"output":{"text":"test"}}', 'application/json')
            cloud_json("POST", self.config.asr_url, {"model": self.config.asr_model}, "mock-key", sse=False, operation="asr")
        self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 90)

    def test_incomplete_response_retains_received_bytes_and_http_metadata(self):
        response = FakeResponse('{"incomplete":')
        with patch("urllib.request.build_opener") as opener, \
                patch.object(response, "read", side_effect=http.client.IncompleteRead(b'{"incomplete":', 20)):
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ProviderError, "传输中断") as raised:
                cloud_json("POST", self.config.asr_url, {}, "mock-key")
        self.assertEqual(raised.exception.details["stage"], "incomplete_response")
        self.assertEqual(raised.exception.details["response_body"], '{"incomplete":')
        self.assertEqual(raised.exception.details["http_status"], 200)

    def test_asr_timeout_details_survive_uncertain_retry_without_new_request(self):
        details = {"stage": "timeout", "reason": "timed out", "response_body": None}
        with patch("app.ai.providers.asr_api_key", return_value="mock-key"), \
                patch("app.ai.providers.extract_audio"), patch("app.ai.providers.upload_audio", return_value="https://oss.example/audio"), \
                patch("app.ai.providers.cloud_json", side_effect=ProviderError("超时", details)) as network:
            for _ in range(2):
                with self.assertRaises(ProviderError) as raised:
                    transcribe(self.asset, self.config, self.settings, self.store, Path(self.temp.name), lambda: None)
                self.assertEqual(raised.exception.details, details)
            network.assert_called_once()

    def test_asr_validation_and_rejection_keep_response_details(self):
        data = {"output": {"unsupported": "response"}, "request_id": "asr-request"}
        rejection_details = {"stage": "http_error", "http_status": 401, "response_body": "InvalidApiKey"}
        with patch("app.ai.providers.asr_api_key", return_value="mock-key"), \
                patch("app.ai.providers.extract_audio"), patch("app.ai.providers.upload_audio", return_value="https://oss.example/audio"), \
                patch("app.ai.providers.cloud_json", side_effect=[data, ProviderHTTPError(401, rejection_details)]):
            with self.assertRaises(ProviderError) as raised:
                transcribe(self.asset, self.config, self.settings, self.store, Path(self.temp.name), lambda: None)
            self.assertEqual(json.loads(raised.exception.details["response_body"]), data)
            with self.assertRaises(ProviderError) as raised:
                transcribe(self.asset, self.config, self.settings, self.store, Path(self.temp.name), lambda: None)
            self.assertEqual(raised.exception.details, rejection_details)

    def test_planner_invalid_json_content_exposes_entire_response(self):
        data = {"request_id": "model-request", "choices": [{"message": {"content": "not JSON"}}]}
        with patch("app.ai.roughcut.model_api_key", return_value="mock-key"), \
                patch("app.ai.roughcut.cloud_json", return_value=data):
            with self.assertRaises(ProviderError) as raised:
                select_intervals(self.transcripts, self.request, self.config, self.asset.duration_us)
            self.assertEqual(json.loads(raised.exception.details["response_body"]), data)

    def test_worker_persists_full_detail_separately_from_job_list_and_keeps_attempt_history(self):
        self.store.put("asset", self.asset.model_dump())
        detail = {"stage": "json_decode", "response_body": "full response " * 2000}
        job = self.store.enqueue("analyze", {"asset_id": self.asset.id, "product_name": "测试款", "config": self.config.model_dump()})
        worker = Worker(self.settings)
        with patch("app.ai.providers.transcribe", side_effect=ProviderError("解析失败", detail)):
            worker.run_once()
        self.assertTrue(self.store.job(job["id"])["has_error_detail"])
        self.assertNotIn("response_body", json.dumps(self.store.jobs()))
        restarted = Store(self.settings.database)
        self.assertEqual(restarted.job_error_detail(job["id"]), {"summary": "解析失败", **detail})
        self.store.control(job["id"], "retry")
        self.store.claim("next-worker")
        self.assertFalse(self.store.job(job["id"])["has_error_detail"])
        with self.store.connection() as conn:
            history = conn.execute("SELECT error_detail FROM attempts WHERE job_id=? AND attempt=1", (job["id"],)).fetchone()
        self.assertEqual(json.loads(history["error_detail"]), detail)


