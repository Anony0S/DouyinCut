import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import create_app
from app.cleanup_records import expired_business_data
from app.core.config import Settings
from app.live_monitoring.catalog import ProductCatalogRefresher
from app.live_monitoring.service import LiveStore, probe_current_product
from app.product_platform.availability import (
    LiveProductAvailabilityWatcher,
    classify_stock,
    match_platform_product,
)
from app.product_platform.client import ProductPlatformError
from app.product_platform.schemas import RoomPlatformBinding
from app.product_platform.service import CONFIG_ID as XINGDOU_CONFIG_ID


class ProductAvailabilityMatchingTests(unittest.TestCase):
    def test_classify_stock_handles_numeric_zero_text_and_missing(self):
        self.assertEqual(classify_stock(128), ("available", 128))
        self.assertEqual(classify_stock(0), ("out_of_stock", 0))
        self.assertEqual(classify_stock(-5), ("out_of_stock", 0))
        self.assertEqual(classify_stock("0"), ("out_of_stock", 0))
        self.assertEqual(classify_stock("0.0"), ("out_of_stock", 0))
        self.assertEqual(classify_stock("无库存"), ("out_of_stock", 0))
        self.assertEqual(classify_stock("1,280"), ("available", 1280))
        self.assertEqual(classify_stock("100+"), ("available", "100+"))
        self.assertEqual(classify_stock("1.2万"), ("available", "1.2万"))
        self.assertEqual(classify_stock(""), ("stock_unknown", None))
        self.assertEqual(classify_stock(None), ("stock_unknown", None))
        self.assertEqual(classify_stock(False), ("stock_unknown", None))

    def test_match_platform_product_prefers_id_then_exact_then_contains(self):
        products = [
            {"id": "1", "platform_product_id": "p-other", "title": "秋季新款宽松卫衣外套"},
            {"id": "2", "platform_product_id": "p-exact", "title": "修身显瘦羽绒服"},
            {"id": "3", "platform_product_id": "p-id", "title": "完全不同标题"},
        ]
        matched, kind = match_platform_product(products, promotion_id="p-id", product_name="修身显瘦羽绒服")
        self.assertEqual((matched["platform_product_id"], kind), ("p-id", "id"))

        matched, kind = match_platform_product(products, promotion_id="none", product_name=" 修身显瘦羽绒服 ")
        self.assertEqual((matched["platform_product_id"], kind), ("p-exact", "exact_title"))

        matched, kind = match_platform_product(products, promotion_id="none", product_name="【现货】修身显瘦羽绒服（国补）")
        self.assertEqual((matched["platform_product_id"], kind), ("p-exact", "contains_title"))

        matched, kind = match_platform_product(products, promotion_id="none", product_name="毫不相关的连衣裙")
        self.assertEqual((matched, kind), (None, None))


class LiveProductAvailabilityApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name))
        self.app = create_app(self.settings, token="test")
        self.app.state.product_availability.request_interval = 0.0
        self.client = TestClient(self.app, headers={"X-Session-Token": "test"})
        self.addCleanup(self.client.close)
        self.store = self.app.state.store
        self.live: LiveStore = self.app.state.live_store
        self.secrets: dict[str, str] = {}
        patchers = [
            patch("app.product_platform.service.read_secret", side_effect=lambda key: self.secrets.get(key)),
            patch("app.product_platform.service.write_secret", side_effect=self._write_secret),
            patch("app.product_platform.xingdongli.read_secret", side_effect=lambda key: self.secrets.get(key)),
            patch("app.product_platform.xingdongli.write_secret", side_effect=self._write_secret),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_secret(self, key: str, value: str):
        self.secrets[key] = value

    def _create_room_with_session(self, slug: str, binding: dict | None, products: list[tuple[str, str]], *,
                                  status: str = "stopped") -> tuple[str, str]:
        monitor = self.live.create_monitor(
            f"https://live.douyin.com/{slug}",
            slug,
            RoomPlatformBinding(**binding) if binding else None,
        )
        monitor_id = monitor["id"]
        session_dir = self.settings.data_dir / "recordings" / "live" / monitor_id / "seed"
        session_id = self.live.start_session(monitor_id, session_dir)
        base = (self.live.session_started(session_id) or time.time()) + 1
        for index, (promotion_id, name) in enumerate(products):
            ts = base + index * 30
            self.live.set_current_product(
                session_id, promotion_id=promotion_id, product_name=name, event_time=ts, received_at=ts
            )
        if status != "recording":
            self.live.stop_session(monitor_id, session_id, status)
        return monitor_id, session_id

    def _wait_availability_done(self, monitor_id: str, session_id: str) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            res = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-availability")
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            if body["state"] not in ("queued", "running"):
                return body
            time.sleep(0.01)
        self.fail("批量库存检测超时")

    def _configure_xingdongli(self, ip_id: str = "42"):
        import hashlib

        token = "xdl-token"
        self.secrets["xingdongli_member_token"] = token
        with self.store.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO objects VALUES ('config', ?, 1, ?, ?)",
                (
                    "xingdongli_product_platform",
                    json.dumps({
                        "id": "xingdongli_product_platform",
                        "member_id": "m1",
                        "member_name": "测试达人",
                        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                        "mobile": "138****0000",
                        "scopes": [{"id": ip_id, "name": "签约达人 A"}],
                        "last_validated_at": time.time(),
                    }, ensure_ascii=False),
                    time.time(),
                ),
            )

    def _configure_xingdou(self, main_ip_id: str = "11", logged_in: bool = True):
        self.secrets["xingdou_product_platform_token"] = "xd-token" if logged_in else ""
        config = {
            "id": XINGDOU_CONFIG_ID,
            "logged_in": logged_in,
            "mobile": "138****0000",
            "member_name": "测试账号",
            "member_id": "m1",
            "main_ip_id": main_ip_id,
            "main_ip_name": f"范围 {main_ip_id}",
            "scopes": [{"id": main_ip_id, "name": f"范围 {main_ip_id}"}],
            "last_validated_at": time.time(),
        }
        try:
            self.store.next_revision("config", XINGDOU_CONFIG_ID, config)
        except KeyError:
            self.store.put("config", config)

    def test_unbound_room_returns_null_platform_product_and_rejects_refresh(self):
        monitor_id, session_id = self._create_room_with_session("unbound", None, [("p1", "宽松卫衣")])
        detail = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        self.assertIsNone(detail["product_periods"][0]["platform_product"])
        refresh = self.client.post(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-availability")
        self.assertEqual(refresh.status_code, 409)

    def test_daduoduo_matches_local_catalog_with_zero_network_requests(self):
        monitor_id, session_id = self._create_room_with_session(
            "ddd",
            {"platform": "daduoduo", "ip_id": "ip-ddd"},
            [
                ("p-in-stock", "法式针织连衣裙"),
                ("p-zero", "重工刺绣夹克"),
                ("p-unknown", "羊毛大衣"),
                ("p-miss", "未收录裤子"),
                ("p-empty", ""),
            ],
        )
        # Before catalog sync -> not_synced for named, None for unnamed
        before = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        by_promo = {period["promotion_id"]: period["platform_product"] for period in before["product_periods"]}
        self.assertEqual(by_promo["p-in-stock"]["state"], "not_synced")
        self.assertIsNone(by_promo["p-empty"])

        # Manual batch endpoint guides user to Daduoduo's catalog sync button
        refresh = self.client.post(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-availability")
        self.assertEqual(refresh.status_code, 409)

        # Insert local catalog snapshot directly
        with self.store.connection() as conn:
            binding_id = conn.execute(
                "SELECT binding_id FROM live_room_platforms WHERE monitor_id=?", (monitor_id,)
            ).fetchone()["binding_id"]
            catalog_items = [
                {"id": "g1", "platform_product_id": "g1", "title": "法式针织连衣裙", "stock": 356},
                {"id": "g2", "platform_product_id": "g2", "title": "重工刺绣夹克", "stock": 0},
                {"id": "g3", "platform_product_id": "g3", "title": "羊毛大衣", "stock": None},
            ]
            conn.execute(
                "INSERT INTO daduoduo_catalogs VALUES (?,?,?)",
                (binding_id, json.dumps(catalog_items, ensure_ascii=False), 1700000000.0),
            )

        after = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        by_promo = {period["promotion_id"]: period["platform_product"] for period in after["product_periods"]}
        self.assertEqual(by_promo["p-in-stock"]["state"], "available")
        self.assertEqual(by_promo["p-in-stock"]["stock"], 356)
        self.assertEqual(by_promo["p-zero"]["state"], "out_of_stock")
        self.assertEqual(by_promo["p-zero"]["stock"], 0)
        self.assertEqual(by_promo["p-unknown"]["state"], "stock_unknown")
        self.assertEqual(by_promo["p-miss"]["state"], "not_found")
        self.assertIsNone(by_promo["p-empty"])

    def test_xingdongli_batch_deduplicates_caches_aborts_on_429_and_syncs_dialog_search(self):
        self._configure_xingdongli("42")
        monitor_id, session_id = self._create_room_with_session(
            "xdl",
            {"platform": "xingdongli", "ip_id": "42"},
            [
                ("p1", "高腰显瘦牛仔裤"),
                ("p2", "冰丝防晒开衫"),
                ("p1", "高腰显瘦牛仔裤"),  # repeated explanation of same product
                ("p3", "平台没有的鞋子"),
            ],
        )
        # Initially unchecked (never auto-fetches on session GET)
        initial = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        for period in initial["product_periods"]:
            self.assertEqual(period["platform_product"]["state"], "unchecked")

        queried: list[str] = []

        def fake_search(_ip_id, keyword, _start, *_args, **_kwargs):
            queried.append(keyword)
            if keyword == "高腰显瘦牛仔裤":
                products = [{"id": "1", "platform_product_id": "p1", "title": "高腰显瘦牛仔裤", "stock": 88}]
            elif keyword == "冰丝防晒开衫":
                products = [{"id": "2", "platform_product_id": "x2", "title": "冰丝防晒开衫", "stock": "0"}]
            else:
                products = [{"id": "9", "platform_product_id": "x9", "title": "无关干扰商品", "stock": 999}]
            return {
                "product": products[0] if products else None,
                "products": products,
                "total": len(products),
                "start": 0,
                "length": 20,
                "scope_name": "签约达人 A",
                "can_add_to_showcase": False,
            }

        with patch("app.product_platform.xingdongli.XingdongliService.search", side_effect=fake_search):
            start_res = self.client.post(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-availability")
            self.assertEqual(start_res.status_code, 202, start_res.text)
            done = self._wait_availability_done(monitor_id, session_id)
            self.assertEqual(done["state"], "idle")

        # 4 periods but only 3 unique product names were queried
        self.assertEqual(len(queried), 3)
        after = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        states = [(p["promotion_id"], p["platform_product"]["state"], p["platform_product"]["stock"])
                  for p in after["product_periods"]]
        self.assertEqual(
            states,
            [
                ("p3", "not_found", None),
                ("p1", "available", 88),
                ("p2", "out_of_stock", 0),
                ("p1", "available", 88),
            ],
        )

        # Single-item dialog search also updates cache immediately
        def dialog_search(_ip_id, keyword, _start, *_args, **_kwargs):
            return {
                "product": {"id": "2", "platform_product_id": "x2", "title": keyword, "stock": 55},
                "products": [{"id": "2", "platform_product_id": "x2", "title": keyword, "stock": 55}],
                "total": 1, "start": 0, "length": 20, "scope_name": "签约达人 A", "can_add_to_showcase": False,
            }

        with patch("app.product_platform.xingdongli.XingdongliService.search", side_effect=dialog_search):
            res = self.client.post(
                "/api/v1/product-platform/products/search",
                json={"monitor_id": monitor_id, "keyword": "冰丝防晒开衫"},
            )
            self.assertEqual(res.status_code, 200)
        refreshed = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        p2 = next(p for p in refreshed["product_periods"] if p["promotion_id"] == "p2")
        self.assertEqual(p2["platform_product"]["state"], "available")
        self.assertEqual(p2["platform_product"]["stock"], 55)

        # 429 rate-limit aborts batch immediately without hitting remaining items
        rate_calls = []

        def rate_limited(_ip_id, keyword, _start, *_args, **_kwargs):
            rate_calls.append(keyword)
            if len(rate_calls) == 1:
                return {
                    "product": None, "products": [], "total": 0,
                    "start": 0, "length": 20, "scope_name": "签约达人 A", "can_add_to_showcase": False,
                }
            raise ProductPlatformError("星动力请求过于频繁，请稍后重试", status=429)

        with patch("app.product_platform.xingdongli.XingdongliService.search", side_effect=rate_limited):
            res = self.client.post(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-availability")
            self.assertEqual(res.status_code, 202)
            failed = self._wait_availability_done(monitor_id, session_id)
        self.assertEqual(failed["state"], "failed")
        self.assertIn("频繁", failed["error"])
        self.assertEqual(len(rate_calls), 2)

        # Changing binding removes cached results
        self.live.set_product_platform(monitor_id, None)
        with self.store.connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM live_platform_product_cache WHERE monitor_id=?", (monitor_id,)
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_xingdou_batch_check_respects_scope_isolation_and_auth_fail_fast(self):
        monitor_id, session_id = self._create_room_with_session(
            "xd", {"platform": "xingdou", "ip_id": ""}, [("p1", "复古格子衬衫")]
        )
        # Logged out -> fails fast with 401
        res = self.client.post(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-availability")
        self.assertEqual(res.status_code, 401)

        self._configure_xingdou("scope-a", logged_in=True)
        with patch.object(
            self.app.state.product_platform,
            "search_items",
            return_value=([{"id": "1", "platform_product_id": "p1", "title": "复古格子衬衫", "stock": 42}], "范围 scope-a", "scope-a"),
        ):
            res = self.client.post(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-availability")
            self.assertEqual(res.status_code, 202)
            done = self._wait_availability_done(monitor_id, session_id)
            self.assertEqual(done["state"], "idle")

        detail_a = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        self.assertEqual(detail_a["product_periods"][0]["platform_product"]["state"], "available")
        self.assertEqual(detail_a["product_periods"][0]["platform_product"]["stock"], 42)

        # Switching Xingdou main_ip_id isolates the old scope's cache
        self._configure_xingdou("scope-b", logged_in=True)
        detail_b = self.client.get(f"/api/v1/live/monitors/{monitor_id}/sessions/{session_id}").json()
        self.assertEqual(detail_b["product_periods"][0]["platform_product"]["state"], "unchecked")

    def test_recording_watcher_checks_new_product_and_resolved_name_and_skips_stopped_sessions(self):
        self._configure_xingdongli("42")
        monitor_id, session_id = self._create_room_with_session(
            "rec", {"platform": "xingdongli", "ip_id": "42"}, [], status="recording"
        )
        watcher = LiveProductAvailabilityWatcher(self.store)
        self.addCleanup(watcher.close)
        checked_event = threading.Event()
        calls: list[str] = []

        def fake_search(_ip_id, keyword, _start, *_args, **_kwargs):
            calls.append(keyword)
            checked_event.set()
            return {
                "product": {"id": "1", "platform_product_id": "p-live", "title": keyword, "stock": 19},
                "products": [{"id": "1", "platform_product_id": "p-live", "title": keyword, "stock": 19}],
                "total": 1, "start": 0, "length": 20, "scope_name": "签约达人 A", "can_add_to_showcase": False,
            }

        with patch("app.product_platform.xingdongli.XingdongliService.search", side_effect=fake_search):
            # 1. Seeded product via probe_current_product triggers check
            class StubResolver:
                def lookup_current(self):
                    return ("p-live", "实时新品针织衫")

            probe_current_product(
                self.live, session_id, monitor_id, StubResolver(), threading.Event(),
                on_product_ready=lambda pid, name: watcher.schedule(monitor_id, session_id, pid, name),
            )
            self.assertTrue(checked_event.wait(3))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                detail = self.live.recording_session(monitor_id, session_id)
                if detail["product_periods"][0]["platform_product"]["state"] == "available":
                    break
                time.sleep(0.01)
            self.assertEqual(detail["product_periods"][0]["platform_product"]["stock"], 19)

            # 2. Repeated same product does NOT return True from set_current_product
            self.assertFalse(
                self.live.set_current_product(
                    session_id, promotion_id="p-live", product_name="实时新品针织衫",
                    event_time=time.time(), received_at=time.time(),
                )
            )

            # 3. New product initially without name -> resolved later via ProductCatalogRefresher
            checked_event.clear()
            ts = time.time() + 10
            self.assertFalse(
                self.live.set_current_product(
                    session_id, promotion_id="p-late", product_name="",
                    event_time=ts, received_at=ts,
                )
            )

            class LateCatalogResolver:
                def lookup_catalog(self):
                    return {"p-late": "稍后补全名称的卫衣"}, None

            refresher = ProductCatalogRefresher(
                self.live, monitor_id, session_id, LateCatalogResolver(), threading.Event(),
                on_product_ready=lambda pid, name: watcher.schedule(monitor_id, session_id, pid, name),
            )
            self.addCleanup(refresher.close)
            refresher.request_missing("p-late")
            self.assertTrue(checked_event.wait(3))
            self.assertIn("稍后补全名称的卫衣", calls)

            # 4. Stopped session does NOT perform online check even if scheduled
            self.live.stop_session(monitor_id, session_id, "stopped")
            before_count = len(calls)
            watcher.schedule(monitor_id, session_id, "p-stopped", "已停录商品")
            watcher.executor.submit(lambda: None).result(timeout=3)
            self.assertEqual(len(calls), before_count)

    def test_cleanup_records_tracks_platform_product_cache_with_monitor(self):
        self._configure_xingdongli("42")
        monitor_id, session_id = self._create_room_with_session(
            "clean", {"platform": "xingdongli", "ip_id": "42"}, [("p1", "待清理商品")]
        )
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO live_platform_product_cache VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (monitor_id, "xingdongli", "42", "待清理商品", "available", "exact_title", "10", "待清理商品", "p1", "", 100.0),
            )
            conn.execute("UPDATE live_monitors SET created=100, updated=100 WHERE id=?", (monitor_id,))
            conn.execute("UPDATE live_monitor_controls SET updated=100 WHERE monitor_id=?", (monitor_id,))
            conn.execute("UPDATE live_sessions SET started=100, ended=120 WHERE id=?", (session_id,))
            conn.execute("UPDATE live_product_periods SET started=100, ended=120 WHERE session_id=?", (session_id,))
            conn.execute("UPDATE live_session_current_product SET event_time=100, received_at=100 WHERE session_id=?", (session_id,))
            _, records, _ = expired_business_data(conn, self.settings.data_dir, cutoff=200.0)
        self.assertIn("live_platform_product_cache", records)
        self.assertEqual(len(records["live_platform_product_cache"]), 1)


if __name__ == "__main__":
    unittest.main()
