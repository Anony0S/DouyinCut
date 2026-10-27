import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import create_app
from app.ai.providers import CloudConfig, ProviderError, ProviderHTTPError
from app.core.config import Settings


class ModelConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Settings(Path(self.temp.name)), token="test-token")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Session-Token": "test-token"}
        self.url = "/api/v1/settings/cloud/test-model"
        self.request = {"config": {"base_url": "https://gateway.example/v1",
                                   "planner_model": "draft-model", "chat_timeout_seconds": 300},
                        "api_key": "draft-private-key"}
        self.cloud = self.enterContext(patch("app.ai.connection.cloud_json",
            return_value={"choices": [{"message": {"content": "OK"}}]}))
        self.secret = self.enterContext(patch("app.ai.providers.read_secret", return_value="saved-private-key"))

    def post(self, body=None):
        return self.client.post(self.url, headers=self.headers, json=body or self.request)

    def test_draft_configuration_is_probed_once_without_saving(self):
        store = self.app.state.store
        saved = {"id": "cloud", "config": CloudConfig(planner_model="saved-model").model_dump()}
        store.put("config", saved)
        with patch("app.api.write_secret") as write:
            response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["model"], "draft-model")
        self.assertGreaterEqual(response.json()["elapsed_ms"], 0)
        self.cloud.assert_called_once()
        args, kwargs = self.cloud.call_args
        self.assertEqual(args[:2], ("POST", "https://gateway.example/v1/chat/completions"))
        self.assertEqual(args[2]["model"], "draft-model")
        self.assertIs(args[2]["stream"], False)
        self.assertEqual(args[2]["max_tokens"], 128)
        self.assertEqual(args[3], "draft-private-key")
        self.assertEqual(kwargs["timeout_seconds"], 60)
        self.assertEqual(kwargs["operation"], "model_test")
        self.secret.assert_not_called()
        write.assert_not_called()
        self.assertEqual(store.get("config", "cloud"), saved)
        self.assertEqual(store.jobs(), [])
        self.assertNotIn("private-key", response.text)

    def test_blank_draft_key_uses_saved_model_key(self):
        self.request["api_key"] = "  "
        self.request["config"]["chat_timeout_seconds"] = 30
        response = self.post()
        self.assertTrue(response.json()["ok"])
        self.secret.assert_called_once_with("llm_api_key")
        self.assertEqual(self.cloud.call_args.args[3], "saved-private-key")
        self.assertEqual(self.cloud.call_args.kwargs["timeout_seconds"], 30)

    def test_missing_key_fails_without_cloud_request(self):
        self.request["api_key"] = ""
        self.secret.return_value = None
        response = self.post()
        self.assertFalse(response.json()["ok"])
        self.assertIn("API Key", response.json()["message"])
        self.cloud.assert_not_called()

    def test_authentication_and_https_validation_precede_cloud_request(self):
        self.assertEqual(self.client.post(self.url, json=self.request).status_code, 401)
        for url in ("http://gateway.example/v1", "https://user:password@gateway.example/v1",
                    "https://gateway.example/v1?api_key=draft-private-key",
                    "https://gateway.example/v1/chat/completions"):
            with self.subTest(url=url):
                self.request["config"]["base_url"] = url
                response = self.post()
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("draft-private-key", response.text)
        self.cloud.assert_not_called()

    def test_empty_or_incompatible_response_is_not_success(self):
        for response in ({}, [], {"error": "bad model"}, {"choices": []},
                         {"choices": [{"message": {"content": None}}]},
                         {"choices": [{"message": {"content": "  "}}]},
                         {"choices": [{"message": {"content": [{"text": "OK"}]}}]}):
            with self.subTest(response=response):
                self.cloud.return_value = response
                result = self.post()
                self.assertFalse(result.json()["ok"])
                self.assertIn("有效文本", result.json()["message"])

    def test_http_failures_explain_status_without_exposing_response_or_secret(self):
        for status, hint in ((401, "API Key"), (403, "权限"), (404, "模型 ID"),
                             (429, "配额"), (503, "服务状态"), (302, "云端")):
            with self.subTest(status=status):
                self.cloud.side_effect = ProviderHTTPError(status, {"response_body": "draft-private-key"})
                response = self.post()
                self.assertFalse(response.json()["ok"])
                self.assertIn(str(status), response.json()["message"])
                self.assertIn(hint, response.json()["message"])
                self.assertNotIn("draft-private-key", response.text)

    def test_transport_and_response_errors_are_reported_without_retry(self):
        for message in ("云端连接超时（60 秒），悬停错误图标查看详情",
                        "云端连接失败，悬停错误图标查看详情",
                        "云端响应不是有效 JSON，悬停错误图标查看完整返回内容"):
            with self.subTest(message=message):
                self.cloud.reset_mock()
                self.cloud.side_effect = ProviderError(message, {"reason": "draft-private-key"})
                result = self.post()
                self.assertFalse(result.json()["ok"])
                self.assertEqual(result.json()["message"], message.split("，悬停")[0])
                self.assertNotIn("draft-private-key", result.text)
                self.cloud.assert_called_once()
