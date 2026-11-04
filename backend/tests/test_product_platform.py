import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings
from app.core.storage import Store
from app.product_platform.client import BASE_URL, ProductPlatformClient, ProductPlatformError, sign_params
from app.product_platform.service import ProductPlatformService, TOKEN_KEY


class ClientTests(unittest.TestCase):
    def client(self, handler, token="fixture-token"):
        transport = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        self.addCleanup(transport.close)
        return ProductPlatformClient(token, transport)

    def test_signing_preserves_empty_zero_and_unicode(self):
        # Independent fixed input, containing no user data or captured credential.
        values = {"keyword": "测试", "timestamp": 1234, "start": 0,
                  "hasLive": "", "brand_name": "", "sign_type": "md5"}
        self.assertEqual(sign_params(values), "EC3CECF9038B923C380FF4962EC965CC")
        self.assertNotEqual(sign_params(values), sign_params({k: v for k, v in values.items() if v != ""}))

    def test_signed_post_sends_both_tokens_and_validates_results(self):
        def handle(request):
            self.assertEqual(request.url.params["access-token"], "fixture-token")
            self.assertEqual(request.headers["member-token"], "fixture-token")
            values = json.loads(request.content)
            signature = values.pop("sign")
            self.assertEqual(values["keyword"], "测试商品")
            self.assertEqual(values["mainIpId"], "scope-a")
            self.assertEqual(values["brand_name"], "")
            self.assertEqual(signature, sign_params(values))
            return httpx.Response(200, json={"success": True, "data": {"data": [{"id": "first"}]}})
        self.assertEqual(self.client(handle).search("测试商品", "scope-a"), [{"id": "first"}])

    def test_login_uses_platform_get_contract(self):
        def handle(request):
            self.assertEqual(request.url.path, "/prod/qiongqi/openapi/member/mobile/login")
            self.assertEqual(request.url.params["smsCode"], "123456")
            return httpx.Response(200, json={"success": True, "data": "new-fixture-token"})
        self.assertEqual(self.client(handle, "").login("13800000000", "123456"), "new-fixture-token")

    def test_httpx_diagnostic_log_omits_login_query_and_credentials(self):
        client = self.client(lambda request: httpx.Response(200, json={"success": True, "data": "new-token"}))
        with self.assertLogs("httpx", level="INFO") as logs:
            client.login("13800000000", "123456")
        output = "\n".join(logs.output)
        for value in ("13800000000", "123456", "fixture-token", "sign="):
            self.assertNotIn(value, output)
        self.assertIn("mobile/login", output)

    def test_http_and_business_401_require_login_but_permission_errors_do_not(self):
        for response in (
            httpx.Response(401),
            httpx.Response(200, json={"success": False, "code": "401", "message": "系统未登录"}),
        ):
            with self.assertRaises(ProductPlatformError) as error:
                self.client(lambda request: response).search("测试", "scope-a")
            self.assertEqual(error.exception.code, "PRODUCT_PLATFORM_AUTH_REQUIRED")
        with self.assertRaises(ProductPlatformError) as error:
            self.client(lambda request: httpx.Response(200, json={
                "success": False, "code": 403, "message": "当前登录账号没有访问权限",
            })).search("测试", "scope-a")
        self.assertNotEqual(error.exception.code, "PRODUCT_PLATFORM_AUTH_REQUIRED")

    def test_malformed_and_transport_failures_do_not_masquerade_as_empty_results(self):
        for payload in ({"success": True, "data": {}}, {"success": True, "data": {"data": ["bad"]}}):
            with self.assertRaises(ProductPlatformError) as error:
                self.client(lambda request: httpx.Response(200, json=payload)).search("测试", "scope-a")
            self.assertEqual(error.exception.status, 502)
        def disconnected(request):
            raise httpx.ConnectError("request-url-with-sensitive-fields", request=request)
        with self.assertRaises(ProductPlatformError) as error:
            self.client(disconnected).search("测试", "scope-a")
        self.assertNotIn("sensitive", str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_upstream_error_redacts_submitted_fields(self):
        client = self.client(lambda request: httpx.Response(200, json={
            "success": False, "message": "失败 13800000000 123456 fixture-token",
        }))
        with self.assertRaises(ProductPlatformError) as error:
            client.login("13800000000", "123456")
        for value in ("13800000000", "123456", "fixture-token"):
            self.assertNotIn(value, str(error.exception))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.secrets = {}
        def write_secret(name, value):
            if value:
                self.secrets[name] = value
            else:
                self.secrets.pop(name, None)
        for name, replacement in (("read_secret", self.secrets.get), ("write_secret", write_secret)):
            p = patch(f"app.product_platform.service.{name}", side_effect=replacement)
            p.start(); self.addCleanup(p.stop)
        self.app = create_app(Settings(Path(self.temp.name)), token="local-session")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Session-Token": "local-session"}
        self.remote = patch("app.product_platform.service.ProductPlatformClient").start()
        self.addCleanup(patch.stopall)
        self.remote.return_value.login.return_value = "new-fixture-token"
        self.remote.return_value.member_info.return_value = {
            "id": 123, "name": "测试账号", "mobile": "13800000000", "password": "must-never-persist",
        }
        self.remote.return_value.member_ips.return_value = [
            {"cooperationIpId": 11, "cooperationIpName": "店铺 A", "contactTel": "13800000000"},
            {"cooperationIpId": 22, "cooperationIpName": "店铺 B"},
        ]

    def post(self, path, payload=None):
        return self.client.post("/api/v1/product-platform/" + path, headers=self.headers, json=payload)

    def login(self):
        result = self.post("login", {"mobile": "13800000000", "sms_code": "123456"})
        self.assertEqual(result.status_code, 200)
        return result.json()

    def test_authenticated_local_boundary_and_validation(self):
        self.assertEqual(self.client.get("/api/v1/product-platform/status").status_code, 401)
        for values in ({"mobile": "bad", "sms_code": "secret-input"}, {"mobile": "13800000000", "sms_code": "bad"}):
            result = self.post("login", values)
            self.assertEqual(result.status_code, 422)
            self.assertNotIn(values["sms_code"], result.text)
        self.assertEqual(self.post("products/search", {"keyword": "  "}).status_code, 422)
        self.assertFalse(self.remote.return_value.login.called)

    def test_login_persists_only_safe_account_data_and_restart_restores_status(self):
        data = self.login()
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["mobile"], "138****0000")
        self.assertEqual(self.secrets[TOKEN_KEY], "new-fixture-token")
        self.assertEqual(data["main_ip_id"], "11")
        new_service = ProductPlatformService(Store(self.app.state.settings.database))
        self.assertEqual(new_service.status(), data)
        result = self.client.get("/api/v1/product-platform/status", headers=self.headers)
        self.assertEqual(result.headers["cache-control"], "no-store")
        persisted = json.dumps(self.app.state.store.list("config"))
        for secret in ("13800000000", "123456", "new-fixture-token", "must-never-persist"):
            self.assertNotIn(secret, persisted)
            self.assertNotIn(secret, result.text)

    def test_search_uses_selected_scope_returns_first_and_preserves_empty_result(self):
        self.login()
        self.assertEqual(self.post("scope", {"main_ip_id": "unknown"}).status_code, 422)
        self.assertEqual(self.post("scope", {"main_ip_id": "22"}).status_code, 200)
        self.remote.return_value.search.return_value = [
            {"id": "first", "title": "测试商品", "price": 0, "productStock": "0", "unshown": "discard"},
            {"id": "second"},
        ]
        result = self.post("products/search", {"keyword": " 测试商品 "}).json()
        self.remote.return_value.search.assert_called_once_with("测试商品", "22")
        self.assertEqual(result["product"]["id"], "first")
        self.assertEqual(result["product"]["price"], 0)
        self.assertNotIn("unshown", result["product"])
        self.assertEqual(result["scope_name"], "店铺 B")
        self.remote.return_value.search.return_value = []
        self.assertIsNone(self.post("products/search", {"keyword": "无结果"}).json()["product"])

    def test_missing_and_expired_login_have_same_code_and_remove_session(self):
        result = self.post("products/search", {"keyword": "测试"})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.json()["detail"]["code"], "PRODUCT_PLATFORM_AUTH_REQUIRED")
        self.login()
        self.remote.return_value.search.side_effect = ProductPlatformError("expired", code="PRODUCT_PLATFORM_AUTH_REQUIRED", status=401)
        self.assertEqual(self.post("products/search", {"keyword": "测试"}).status_code, 401)
        self.assertNotIn(TOKEN_KEY, self.secrets)
        self.assertFalse(self.app.state.product_platform.status()["logged_in"])

    def test_permission_and_network_errors_keep_login_and_allow_retry(self):
        self.login()
        for code, status in ((403, 400), (None, 503)):
            self.remote.return_value.search.side_effect = ProductPlatformError("temporary", code=code, status=status)
            self.assertEqual(self.post("products/search", {"keyword": "测试"}).status_code, status)
            self.assertTrue(self.app.state.product_platform.status()["logged_in"])

    def test_sms_cooldown_only_after_success_and_no_credential_write(self):
        self.remote.return_value.send_code.side_effect = ProductPlatformError("发送失败", status=400)
        self.assertEqual(self.post("send-code", {"mobile": "13800000000"}).status_code, 400)
        self.remote.return_value.send_code.side_effect = None
        self.assertEqual(self.post("send-code", {"mobile": "13800000000"}).status_code, 200)
        self.assertEqual(self.post("send-code", {"mobile": "13800000000"}).status_code, 429)
        self.assertEqual(self.secrets, {})

    def test_empty_scopes_and_logout(self):
        self.remote.return_value.member_ips.return_value = []
        self.assertTrue(self.login()["logged_in"])
        self.assertEqual(self.post("products/search", {"keyword": "测试"}).status_code, 409)
        self.assertFalse(self.post("logout").json()["logged_in"])
        self.assertEqual(self.secrets, {})

    def test_failed_system_store_does_not_mark_logged_in(self):
        with patch("app.product_platform.service.write_secret", side_effect=RuntimeError("secret-value")):
            result = self.post("login", {"mobile": "13800000000", "sms_code": "123456"})
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("secret-value", result.text)
        self.assertFalse(self.app.state.product_platform.status()["logged_in"])
