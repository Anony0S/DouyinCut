import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings
from app.product_platform import xingdongli


class XingdongliTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.app = create_app(Settings(Path(workspace.name)), token="local-test")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Session-Token": "local-test"}
        self.token = ""
        self.requests = []
        self.expired = False
        self.scopes = [{"id": 42, "nickname": "达人 A"}]
        self.addCleanup(patch.stopall)
        patch.object(xingdongli, "read_secret", side_effect=lambda _: self.token).start()
        patch.object(xingdongli, "write_secret", side_effect=self.store_token).start()
        patch.object(xingdongli.XingdongliService, "_client", side_effect=self.remote_client).start()

    def store_token(self, key, token):
        self.assertEqual(key, xingdongli.TOKEN_KEY)
        self.token = token

    def remote_client(self, token):
        return httpx.Client(base_url=xingdongli.ORIGIN,
                            headers={**xingdongli.MINI_PROGRAM_HEADERS, "member-token": token},
                            transport=httpx.MockTransport(self.remote_request))

    def remote_request(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.headers["member-token"] == "invalid":
            return httpx.Response(401, json={"message": "invalid"})
        if path == "/frontend/member/get":
            value = {"id": 1, "mobile": "13800001234", "nickname": "测试账号", "weixinOpenid": "private"}
        elif path == "/frontend/contract/contractIp":
            value = self.scopes
        elif path == "/frontend/product/list-by-ip":
            if self.expired:
                return httpx.Response(200, json={"success": False, "code": 401, "message": "expired"})
            value = {"total": 21, "data": [
                {"id": "relation", "productId": "9912345678901234567", "productName": "测试商品",
                 "productImg": "https://lf3-cm.ecombdstatic.com/product.jpg", "todaySale": 0, "productPrice": 12.5},
            ]}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={"success": True, "data": value})

    def connect(self, token="fixture-token"):
        return self.client.post("/api/v1/product-platform/xingdongli/connect", headers=self.headers,
                                json={"member_token": token})

    def bind(self, ip_id="42"):
        return self.client.post("/api/v1/live/monitors", headers=self.headers,
                                json={"url": "https://live.douyin.com/123", "name": "测试直播间",
                                      "product_platform": {"platform": "xingdongli", "ip_id": ip_id}})

    def search(self, monitor_id, keyword, start=0):
        return self.client.post("/api/v1/product-platform/products/search", headers=self.headers,
                                json={"monitor_id": monitor_id, "keyword": keyword, "start": start})

    def test_token_is_verified_before_saving_and_never_returned(self):
        self.assertEqual(self.client.get("/api/v1/product-platform/xingdongli").status_code, 401)
        status = self.connect().json()
        self.assertTrue(status["logged_in"])
        self.assertEqual(status["mobile"], "138****1234")
        self.assertEqual(status["scopes"], [{"id": "42", "name": "达人 A"}])
        self.assertNotIn(self.token, json.dumps(status))
        self.assertNotIn("weixinOpenid", status)
        self.assertEqual([request.url.path for request in self.requests],
                         ["/frontend/member/get", "/frontend/contract/contractIp"])
        for request in self.requests:
            self.assertEqual(request.headers["member-token"], self.token)
            self.assertIn("MiniProgramEnv/Windows", request.headers["user-agent"])
            self.assertTrue(request.headers["referer"].endswith("/page-frame.html"))
            self.assertNotIn("cookie", request.headers)
        config = self.app.state.store.get("config", xingdongli.CONFIG_ID)
        self.assertNotIn(self.token, json.dumps(config))
        self.assertEqual(self.connect("invalid").status_code, 401)
        self.assertEqual(self.token, "fixture-token")
        self.assertEqual(self.client.get("/api/v1/product-platform/xingdongli", headers=self.headers).json(), status)

    def test_room_search_and_listing_use_the_bound_authorized_ip(self):
        self.assertEqual(self.bind().status_code, 401)
        self.assertEqual(self.connect().status_code, 200)
        self.assertEqual(self.bind("77").status_code, 409)
        room = self.bind()
        self.assertEqual(room.status_code, 201)
        monitor_id = room.json()["id"]
        response = self.search(monitor_id, " 测试 ", 20)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(self.requests[-1].content), {
            "ipId": 42, "start": 20, "length": 20, "searchProduct": "测试",
            "sortType": "time", "sortOrder": "desc", "onlyHasMaterial": False,
        })
        self.assertEqual(response.json()["products"][0]["platform_product_id"], "9912345678901234567")
        self.assertFalse(response.json()["can_add_to_showcase"])
        self.assertEqual(self.search(monitor_id, "").status_code, 200)
        self.assertEqual(json.loads(self.requests[-1].content)["searchProduct"], "")
        self.app.state.store.put("asset", {"id": "asset_fixture", "source_monitor_id": monitor_id})
        cover_search = self.client.post("/api/v1/product-platform/products/search", headers=self.headers,
                                        json={"asset_id": "asset_fixture", "keyword": "测试"})
        self.assertEqual(cover_search.json()["detail"]["code"], "REFERENCE_IMAGES_UNSUPPORTED")

    def test_refresh_revalidates_scope_and_search_detects_expired_login(self):
        self.connect()
        monitor_id = self.bind().json()["id"]
        self.scopes = []
        refresh = self.client.post("/api/v1/product-platform/xingdongli/refresh", headers=self.headers)
        self.assertEqual(refresh.json()["scopes"], [])
        self.assertEqual(self.search(monitor_id, "测试").status_code, 409)
        self.scopes = [{"id": 42, "nickname": "达人 A"}]
        self.client.post("/api/v1/product-platform/xingdongli/refresh", headers=self.headers)
        self.expired = True
        response = self.search(monitor_id, "测试")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], xingdongli.AUTH_REQUIRED)
        self.assertFalse(self.client.get("/api/v1/product-platform/xingdongli", headers=self.headers).json()["logged_in"])
        self.assertEqual(self.client.post("/api/v1/product-platform/xingdongli/logout", headers=self.headers).status_code, 200)
        self.assertFalse(self.token)
