import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from fastapi.testclient import TestClient

from app.api import create_app
from app.ai.providers import CloudConfig, ProviderError, interruptible_network_call, _network_child
from app.core.config import Settings


class OssConnectionTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.app = create_app(Settings(Path(temporary)), token="test-token")
        self.client = self.enterContext(TestClient(self.app))
        self.url = "/api/v1/settings/cloud/test-oss"
        self.body = {"config": {"oss_endpoint": "https://oss-cn-beijing.aliyuncs.com",
                                "oss_bucket": "test-bucket"},
                     "access_key_id": "draft-id", "access_key_secret": "draft-secret"}
        self.cloud = self.enterContext(patch("app.ai.connection.interruptible_network_call", return_value=True))
        self.secret = self.enterContext(patch("app.ai.connection.read_secret", side_effect=lambda name: "saved-" + name))

    def post(self):
        return self.client.post(self.url, headers={"X-Session-Token": "test-token"}, json=self.body)

    def test_draft_probe_does_not_save_secrets_settings_or_jobs(self):
        with patch("app.api.write_secret") as write:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.cloud.assert_called_once()
        args = self.cloud.call_args.args
        self.assertEqual(args[0], "oss_test")
        self.assertEqual(args[1][0].oss_bucket, "test-bucket")
        self.assertEqual(args[1][2:], ("draft-id", "draft-secret"))
        self.assertEqual(self.cloud.call_args.kwargs, {"deadline_seconds": 30})
        write.assert_not_called()
        self.secret.assert_not_called()
        self.assertEqual(self.app.state.store.jobs(), [])
        self.assertEqual(self.app.state.store.list("config"), [])
        self.assertNotIn("draft-secret", response.text)

    def test_each_blank_key_uses_its_saved_credential(self):
        self.body["access_key_id"] = " "
        self.body["access_key_secret"] = " "
        self.assertTrue(self.post().json()["ok"])
        self.assertEqual(self.cloud.call_args.args[1][2:],
                         ("saved-oss_access_key_id", "saved-oss_access_key_secret"))

    def test_auth_config_and_missing_keys_prevent_requests(self):
        self.assertEqual(self.client.post(self.url, json=self.body).status_code, 401)
        for endpoint in ("http://oss-cn-beijing.aliyuncs.com", "https://example.com",
                         "https://id:secret@oss-cn-beijing.aliyuncs.com", "https://oss-cn-beijing.aliyuncs.com/path",
                         "https://oss-cn-beijing.aliyuncs.com?secret=bad"):
            self.body["config"]["oss_endpoint"] = endpoint
            self.assertFalse(self.post().json()["ok"])
        self.body["config"]["oss_endpoint"] = "https://oss-cn-beijing.aliyuncs.com"
        self.body["access_key_id"] = ""
        self.secret.return_value = None
        self.secret.side_effect = None
        self.assertFalse(self.post().json()["ok"])
        self.cloud.assert_not_called()

    def test_http_code_and_timeout_are_returned_without_raw_errors(self):
        self.cloud.side_effect = ProviderError("hidden", {
            "stage": "oss_upload", "reason": "AccessDenied", "http_status": 403,
            "oss_error_code": "AccessDenied", "response_body": "draft-secret"})
        response = self.post()
        self.assertFalse(response.json()["ok"])
        self.assertIn("HTTP 403", response.json()["message"])
        self.assertNotIn("draft-secret", response.text)
        self.cloud.side_effect = ProviderError("hidden", {"stage": "process_timeout"})
        self.assertIn("30 秒", self.post().json()["message"])


class OssChildTests(unittest.TestCase):
    def local_server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return CloudConfig(oss_endpoint=f"http://127.0.0.1:{server.server_port}", oss_bucket="test-bucket")

    def test_real_child_uploads_only_small_test_object(self):
        class Handler(BaseHTTPRequestHandler):
            received = None

            def do_PUT(self):
                Handler.received = (unquote(self.path), self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        config = self.local_server(Handler)
        result = interruptible_network_call("oss_test", (config, "fixture", "id", "secret"), deadline_seconds=10)
        self.assertTrue(result)
        self.assertEqual(Handler.received, ("/test-bucket/douyincut/audio/test-fixture.txt", b"DouyinCut OSS test"))

    def test_stalled_request_is_terminated_by_deadline(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self):
                started.set()
                release.wait(10)

            def log_message(self, *_args):
                pass

        config = self.local_server(Handler)
        # Start the deadline only after the child reaches the real stalled upload.
        original = time.monotonic
        with patch("app.ai.providers.time.monotonic", side_effect=lambda: original() + (20 if started.is_set() else 0)):
            before = original()
            with self.assertRaises(ProviderError) as raised:
                interruptible_network_call("oss_test", (config, "fixture", "id", "secret"), deadline_seconds=5)
        self.assertTrue(started.is_set(), "The probe must reach the upload before testing its timeout")
        self.assertEqual(raised.exception.details["stage"], "process_timeout")
        self.assertLess(original() - before, 8)
        release.set()

    def test_oss_child_sanitizes_sdk_exception(self):
        class Pipe:
            value = None

            def send(self, value):
                self.value = value

            def close(self):
                pass

        class Denied(Exception):
            status = 403
            code = "AccessDenied"

        pipe = Pipe()
        with patch("app.ai.providers._test_oss_request", side_effect=Denied("secret https://signed?secret=key")):
            _network_child(pipe, "oss_test", (), {})
        self.assertEqual(pipe.value[2]["http_status"], 403)
        self.assertEqual(pipe.value[2]["oss_error_code"], "AccessDenied")
        self.assertNotIn("secret", str(pipe.value))
