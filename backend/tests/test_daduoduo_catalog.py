import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings
from app.core.domain import Asset
from app.product_platform.daduoduo import ORIGIN, fetch_page


def goods(identifier, title):
    return {"goodsId": identifier, "CoopGoodsId": f"coop-{identifier}",
            "goodsInfo": {"GoodName": title, "SellPrice": 49.9, "SaleCntLast1D": "4",
                          "CoopBeginTime": "2026-11-20", "LiveBeginTime": "11/21 21:15"}}


class DaduoduoCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Settings(Path(self.temp.name)), token="test")
        self.client = TestClient(self.app, headers={"X-Session-Token": "test"})
        self.addCleanup(self.client.close)
        self.binding = {"platform": "daduoduo", "ip_id": "ip"}

    def room(self, name, binding):
        response = self.client.post("/api/v1/live/monitors", json={
            "url": f"https://live.douyin.com/{name}", "name": name, "product_platform": binding,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def catalog(self, room_id, method="GET"):
        response = self.client.request(method, f"/api/v1/live/monitors/{room_id}/product-catalog")
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def wait_for_catalog(self, room_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.catalog(room_id)
            if status["state"] not in ("queued", "running"):
                return status
            time.sleep(0.01)
        self.fail("商品目录更新超时")

    def search(self, **parameters):
        return self.client.post("/api/v1/product-platform/products/search", json=parameters)

    def test_pagination_sends_only_ip_id_and_pagination_parameters(self):
        def handle(request):
            self.assertEqual(request.url.host, "qiepian.daduoduo.com")
            self.assertEqual(dict(request.url.params), {
                "action": "GoodsListForPreview", "ipId": "ip", "pageSize": "50", "pageIndex": "2",
            })
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("referer", request.headers)
            return httpx.Response(200, json={"code": 0, "data": {"total": 0, "data": [goods("3", "外套")]}})

        with httpx.Client(base_url=ORIGIN, transport=httpx.MockTransport(handle)) as transport:
            transport.cookies.set("fixture", "1")
            self.assertEqual(fetch_page(transport, "ip", 2), (0, [goods("3", "外套")]))

    def test_startup_and_manual_sync_publish_complete_room_snapshot(self):
        rain = self.room("rain", self.binding)
        snow = self.room("snow", {**self.binding, "ip_id": "snow"})
        second_started, release_page = threading.Event(), threading.Event()
        page_numbers = []
        refresh_page = None

        def page(_client, ip_id, number):
            page_numbers.append((ip_id, number))
            if ip_id == "snow":
                return 1, [goods("a", "雪地靴")]
            if number == 1:
                return 2, [goods("a", "宽松 衬衫")]
            if refresh_page is not None:
                second_started.set()
                if not release_page.wait(5):
                    raise AssertionError("测试未释放分页请求")
                return refresh_page
            return 0, [goods("b", "针织开衫")]

        with patch("app.product_platform.daduoduo.fetch_page", side_effect=page), self.client:
            self.assertEqual(self.wait_for_catalog(rain)["count"], 2)
            self.assertEqual(self.wait_for_catalog(snow)["count"], 1)
            self.assertEqual(page_numbers, [("ip", 1), ("ip", 2), ("snow", 1)])
            self.assertEqual(self.search(keyword="宽松衬衫", monitor_id=rain).json()["product"]["title"], "宽松 衬衫")
            product = self.search(keyword="针织开衫", monitor_id=rain).json()["product"]
            self.assertEqual(product["listing_time"], "2026-11-20")
            self.assertEqual(product["last_explained_at"], "11/21 21:15")
            self.assertIsNone(self.search(keyword="雪地靴", monitor_id=rain).json()["product"])
            for attempt, (refresh_page, error_text) in enumerate((
                ((3, [goods("changed", "更新期间增加的商品")]), "商品总数在分页期间变化"),
                ((0, []), "第 2 页返回 0 条，累计 1 条，与首页总数 2 条不一致"),
            ), start=2):
                second_started.clear()
                release_page.clear()
                self.catalog(rain, "POST")
                try:
                    self.assertTrue(second_started.wait(5))
                    self.catalog(rain, "POST")
                    self.assertEqual(self.search(keyword="针织开衫", monitor_id=rain).json()["product"]["id"], "b")
                finally:
                    release_page.set()
                status = self.wait_for_catalog(rain)
                self.assertEqual(status["state"], "failed")
                self.assertIn(error_text, status["error"])
                self.assertEqual(page_numbers.count(("ip", 1)), attempt)
                self.assertEqual(self.search(keyword="针织开衫", monitor_id=rain).json()["product"]["id"], "b")
            self.assertEqual(self.client.patch(f"/api/v1/live/monitors/{rain}/product-platform",
                                               json={"binding": {**self.binding, "ip_id": "new"}}).status_code, 200)
            self.assertIn("商品列表尚未同步", self.search(keyword="针织开衫", monitor_id=rain).text)

    def test_reference_lookup_follows_current_binding_and_showcase_rejects_daduoduo(self):
        room = self.room("daduoduo", self.binding)
        asset = Asset(id="asset_room", path="source.mp4", name="source", sha256="digest",
                      duration_us=2_000_000, width=320, height=240, has_audio=True, source_monitor_id=room)
        self.app.state.store.put("asset", asset.model_dump())
        self.assertEqual(self.search(keyword="衬衫", asset_id=asset.id).json()["detail"]["code"],
                         "REFERENCE_IMAGES_UNSUPPORTED")
        response = self.client.post("/api/v1/product-platform/products/images",
                                    json={"asset_id": asset.id, "product_id": "test"})
        self.assertEqual(response.json()["detail"]["code"], "REFERENCE_IMAGES_UNSUPPORTED")
        response = self.client.post("/api/v1/product-platform/products/add-to-showcase",
                                    json={"monitor_id": room, "platform_product_id": "test"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.patch(f"/api/v1/live/monitors/{room}/product-platform",
                                           json={"binding": {"platform": "xingdou"}}).status_code, 200)
        with patch.object(self.app.state.product_platform, "search", return_value={"product": None, "scope_name": ""}) as search:
            self.assertEqual(self.search(keyword="衬衫", asset_id=asset.id).status_code, 200)
            search.assert_called_once_with("衬衫")
