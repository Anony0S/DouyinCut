import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.config import Settings
from app.core.storage import Store
from app.douyin_product_events.protocol import ProductChangeEvent
from app.live_monitoring import LiveMonitorWorker, LiveStore
from app.live_monitoring.catalog import ProductCatalogRefresher


class LiveProductCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name))
        self.settings.prepare()
        self.store = Store(self.settings.database)
        self.live = LiveStore(self.store)
        self.monitor = self.live.create_monitor("https://live.douyin.com/catalog-a", "A")
        self.session_id = self.live.start_session(self.monitor["id"], Path(self.temp.name) / "a")
        self.now = 1000

    def refresher(self, *, monitor=None, session_id=None, resolver=None, force=False):
        resolver = resolver or Mock(lookup_catalog=Mock(return_value=({}, None)))
        refresher = ProductCatalogRefresher(
            self.live, (monitor or self.monitor)["id"], session_id or self.session_id,
            resolver, threading.Event(), force=force, clock=lambda: self.now,
        )
        self.addCleanup(refresher.close)
        return refresher

    def wait_for_refresh(self, refresher):
        self.assertIsNotNone(refresher.future)
        refresher.future.result(timeout=5)

    def test_known_names_are_prewarmed_and_backfilled_without_database_lookups(self):
        self.live.replace_monitor_products(self.monitor["id"], {"known": "已知商品"})
        self.live.record_product_event(self.session_id, event_key="known", promotion_id="known")
        with patch.object(self.live, "product_catalog_state", wraps=self.live.product_catalog_state) as state, \
             patch.object(self.live, "product_name", wraps=self.live.product_name) as lookup:
            refresher = self.refresher()
            for _ in range(3):
                refresher.request_missing("known")
            state.assert_called_once_with(self.monitor["id"])
            lookup.assert_not_called()
        refresher.resolver.lookup_catalog.assert_not_called()
        events = self.live.recording_session(self.monitor["id"], self.session_id)["product_events"]
        self.assertEqual(events[0]["product_name"], "已知商品")

    def test_database_name_found_after_construction_is_cached(self):
        refresher = self.refresher()
        self.live.replace_monitor_products(self.monitor["id"], {"new": "新商品"})
        with patch.object(self.live, "product_name", wraps=self.live.product_name) as lookup:
            for _ in range(3):
                refresher.request_missing("new")
            lookup.assert_called_once_with(self.monitor["id"], "new")
        refresher.resolver.lookup_catalog.assert_not_called()

    def test_unknown_id_uses_database_once_then_reuses_refreshed_name(self):
        entered, release = threading.Event(), threading.Event()

        def fetch():
            entered.set()
            self.assertTrue(release.wait(5))
            return {"new": "新商品"}, None

        refresher = self.refresher(resolver=Mock(lookup_catalog=Mock(side_effect=fetch)))
        self.addCleanup(release.set)
        with patch.object(self.live, "product_name", wraps=self.live.product_name) as lookup:
            refresher.request_missing("new")
            self.assertTrue(entered.wait(5))
            lookup.assert_called_once_with(self.monitor["id"], "new")
            release.set()
            self.wait_for_refresh(refresher)
            for _ in range(3):
                refresher.request_missing("new")
            lookup.assert_called_once_with(self.monitor["id"], "new")
        refresher.resolver.lookup_catalog.assert_called_once()

    def test_failed_refresh_preserves_cached_names(self):
        self.live.replace_monitor_products(self.monitor["id"], {"known": "已有商品"})
        refresher = self.refresher(resolver=Mock(lookup_catalog=Mock(side_effect=RuntimeError("网络失败"))))
        refresher.request_missing("missing")
        self.wait_for_refresh(refresher)
        with patch.object(self.live, "product_name", wraps=self.live.product_name) as lookup, \
             patch.object(self.live, "update_product_name", wraps=self.live.update_product_name) as update:
            refresher.request_missing("known")
            lookup.assert_not_called()
            update.assert_called_once_with(self.session_id, "known", "已有商品")
        refresher.resolver.lookup_catalog.assert_called_once()

    def test_successful_refresh_replaces_cached_names_and_removes_absent_ids(self):
        self.live.replace_monitor_products(self.monitor["id"], {"known": "旧名称", "removed": "已下架"})
        resolver = Mock(lookup_catalog=Mock(side_effect=[({"known": "新名称"}, None), ({}, None)]))
        refresher = self.refresher(resolver=resolver)
        refresher.request_missing("new")
        self.wait_for_refresh(refresher)
        with patch.object(self.live, "product_name", wraps=self.live.product_name) as lookup, \
             patch.object(self.live, "update_product_name", wraps=self.live.update_product_name) as update:
            refresher.request_missing("known")
            lookup.assert_not_called()
            update.assert_called_once_with(self.session_id, "known", "新名称")
            refresher.request_missing("removed")
            lookup.assert_called_once_with(self.monitor["id"], "removed")
        refresher.request_refresh()
        self.wait_for_refresh(refresher)
        with patch.object(self.live, "product_name", wraps=self.live.product_name) as lookup:
            refresher.request_missing("known")
            lookup.assert_called_once_with(self.monitor["id"], "known")
        self.assertEqual(resolver.lookup_catalog.call_count, 2)

    def test_name_caches_are_isolated_between_monitors(self):
        other = self.live.create_monitor("https://live.douyin.com/catalog-b", "B")
        other_session = self.live.start_session(other["id"], Path(self.temp.name) / "b")
        self.live.replace_monitor_products(self.monitor["id"], {"shared": "A 的商品"})
        self.live.replace_monitor_products(other["id"], {"shared": "B 的商品"})
        first = self.refresher()
        second = self.refresher(monitor=other, session_id=other_session)
        with patch.object(self.live, "product_name", wraps=self.live.product_name) as lookup, \
             patch.object(self.live, "update_product_name", wraps=self.live.update_product_name) as update:
            first.request_missing("shared")
            second.request_missing("shared")
            first.request_missing("shared")
            lookup.assert_not_called()
            self.assertEqual([call.args for call in update.call_args_list], [
                (self.session_id, "shared", "A 的商品"),
                (other_session, "shared", "B 的商品"),
                (self.session_id, "shared", "A 的商品"),
            ])

    def test_first_fetch_then_reuse_across_sessions_and_restart_including_empty_catalog(self):
        first = self.refresher()
        first.tick()
        self.wait_for_refresh(first)
        first.resolver.lookup_catalog.assert_called_once()
        self.assertIsNotNone(self.live.product_catalog_state(self.monitor["id"])["fetched"])
        self.now += 10000
        first.tick()
        first.resolver.lookup_catalog.assert_called_once()

        self.live.stop_session(self.monitor["id"], self.session_id)
        session_id = self.live.start_session(self.monitor["id"], Path(self.temp.name) / "a2")
        reopened_store = LiveStore(Store(self.settings.database))
        self.assertIsNotNone(reopened_store.product_catalog_state(self.monitor["id"])["fetched"])
        second = self.refresher(session_id=session_id)
        second.tick()
        second.resolver.lookup_catalog.assert_not_called()
        self.assertIsNone(self.live.recording_session(self.monitor["id"], session_id)["current_product"])

    def test_rooms_are_isolated_and_deleting_session_retains_room_catalog(self):
        self.live.replace_monitor_products(self.monitor["id"], {"shared": "A 的商品"})
        other = self.live.create_monitor("https://live.douyin.com/catalog-b", "B")
        other_session = self.live.start_session(other["id"], Path(self.temp.name) / "b")
        self.assertEqual(self.live.product_name(other["id"], "shared"), "")
        resolver = Mock(lookup_catalog=Mock(return_value=({"shared": "B 的商品"}, None)))
        other_refresh = self.refresher(monitor=other, session_id=other_session, resolver=resolver)
        other_refresh.tick()
        self.wait_for_refresh(other_refresh)
        self.assertEqual(self.live.product_name(self.monitor["id"], "shared"), "A 的商品")
        self.assertEqual(self.live.product_name(other["id"], "shared"), "B 的商品")
        self.live.stop_session(self.monitor["id"], self.session_id)
        self.live.delete_session(self.monitor["id"], self.session_id, self.settings.data_dir / "recordings" / "live")
        self.assertEqual(self.live.product_name(self.monitor["id"], "shared"), "A 的商品")
        self.live.delete_monitor(self.monitor["id"])
        self.assertIsNone(self.live.product_catalog_state(self.monitor["id"])["fetched"])
        self.assertEqual(self.live.product_name(other["id"], "shared"), "B 的商品")

    def test_unknown_ids_during_refresh_share_one_request_and_backfill_names(self):
        entered, release = threading.Event(), threading.Event()

        def fetch():
            entered.set()
            self.assertTrue(release.wait(5))
            return {"new": "新商品"}, None

        resolver = Mock(lookup_catalog=Mock(side_effect=fetch))
        refresher = self.refresher(resolver=resolver)
        # Always release the fake HTTP call before executor cleanup on failure.
        self.addCleanup(release.set)
        refresher.tick()
        self.assertTrue(entered.wait(5))
        self.live.record_product_event(self.session_id, event_key="unknown:new", promotion_id="new")
        for product_id in ("new", "missing", "new", "missing"):
            refresher.request_missing(product_id)
        self.live.set_product_listener_state(self.session_id, "listening")
        self.live.set_product_catalog_error(self.session_id, {"summary": "上一轮失败"})
        self.live.request_product_listener_retry(self.monitor["id"], self.session_id)
        self.live.take_product_listener_retries()
        refresher.request_refresh()
        refresher.on_listening()
        with self.assertRaisesRegex(ValueError, "正在重试"):
            self.live.request_product_listener_retry(self.monitor["id"], self.session_id)
        release.set()
        self.wait_for_refresh(refresher)
        resolver.lookup_catalog.assert_called_once()
        events = self.live.recording_session(self.monitor["id"], self.session_id)["product_events"]
        self.assertEqual(events[0]["product_name"], "新商品")
        self.assertEqual(self.live.product_catalog_state(self.monitor["id"])["misses"], {"missing": 1300})
        self.assertEqual(self.live.recording_session(self.monitor["id"], self.session_id)["product_listener"]["status"],
                         "listening")
        # A delayed worker dispatch of a request made during that refresh also reuses its result.
        refresher.request_refresh(requested_at=1000)
        resolver.lookup_catalog.assert_called_once()

    def test_automatic_requests_wait_sixty_seconds_and_merge_pending_ids(self):
        resolver = Mock(lookup_catalog=Mock(side_effect=[({}, None), ({"p1": "一", "p2": "二"}, None)]))
        refresher = self.refresher(resolver=resolver)
        refresher.tick()
        self.wait_for_refresh(refresher)
        self.now = 1010
        refresher.request_missing("p1")
        refresher.request_missing("p2")
        refresher.tick()
        resolver.lookup_catalog.assert_called_once()
        self.now = 1059
        refresher.tick()
        resolver.lookup_catalog.assert_called_once()
        self.now = 1060
        refresher.tick()
        self.wait_for_refresh(refresher)
        self.assertEqual(resolver.lookup_catalog.call_count, 2)
        self.assertEqual(self.live.product_name(self.monitor["id"], "p2"), "二")
        self.now = 10000
        refresher.tick()
        self.assertEqual(resolver.lookup_catalog.call_count, 2, "idle time must never poll the catalog")

    def test_missing_id_cooldown_survives_reconnect_and_needs_new_message_after_five_minutes(self):
        self.live.replace_monitor_products(self.monitor["id"], {})
        first = self.refresher()
        first.request_missing("missing")
        self.wait_for_refresh(first)
        second = self.refresher()
        self.now = 1100
        second.request_missing("missing")
        second.resolver.lookup_catalog.assert_not_called()
        self.now = 1300
        second.tick()
        second.resolver.lookup_catalog.assert_not_called()
        second.request_missing("missing")
        self.wait_for_refresh(second)
        second.resolver.lookup_catalog.assert_called_once()

    def test_failure_keeps_previous_catalog_and_manual_retry_bypasses_cooldown(self):
        self.live.replace_monitor_products(self.monitor["id"], {"old": "旧目录"})
        resolver = Mock(lookup_catalog=Mock(side_effect=[RuntimeError("网络失败"), ({"new": "新目录"}, None)]))
        refresher = self.refresher(resolver=resolver)
        refresher.request_missing("new")
        self.wait_for_refresh(refresher)
        self.assertEqual(self.live.product_name(self.monitor["id"], "old"), "旧目录")
        self.assertIsNotNone(self.live.recording_session(self.monitor["id"], self.session_id)["product_catalog_error"])
        self.now = 1001
        refresher.request_missing("new")
        resolver.lookup_catalog.assert_called_once()
        self.live.set_product_listener_state(self.session_id, "listening")
        self.live.request_product_listener_retry(self.monitor["id"], self.session_id)
        request = self.live.take_product_listener_retries()[0]
        self.assertEqual((request["reconnect"], request["refresh_catalog"]), (0, 1))
        with patch.object(LiveMonitorWorker, "_recover_interrupted_sessions"):
            worker = LiveMonitorWorker(self.settings, resolver=object())
        worker.product_catalogs[self.monitor["id"]] = refresher
        with patch.object(worker, "_start_python_product_listener") as reconnect:
            # Submit the same request through the real worker path.
            self.live.set_product_listener_state(self.session_id, "listening")
            self.live.request_product_listener_retry(self.monitor["id"], self.session_id)
            worker._process_product_listener_retries()
        self.wait_for_refresh(refresher)
        reconnect.assert_not_called()
        self.assertEqual(resolver.lookup_catalog.call_count, 2)
        result = self.live.recording_session(self.monitor["id"], self.session_id)
        self.assertIsNone(result["product_catalog_error"])
        self.assertEqual(result["product_listener"]["status"], "listening")

    def run_listener(self, resolver, events=(), *, force=False):
        stop = threading.Event()
        prepared = SimpleNamespace(room=SimpleNamespace(room_id="room"), user_agent="test", cookie="")
        with patch.object(LiveMonitorWorker, "_recover_interrupted_sessions"):
            worker = LiveMonitorWorker(self.settings, resolver=object())
        test = self

        class Client:
            def __init__(self, on_event, on_current_product, on_state, **_kwargs):
                self.current, self.state = on_current_product, on_state

            def start_prepared(self, _session):
                self.state("listening", "")
                for event in events:
                    self.current(event)
                catalog = worker.product_catalogs[test.monitor["id"]]
                if catalog.future is not None:
                    catalog.future.result(timeout=5)
                stop.set()

            def stop(self):
                pass

        with patch("app.douyin_product_events.DouyinWebcastSessionPreparer") as preparer, \
             patch("app.douyin_product_events.DouyinProductEventClient", Client), \
             patch("app.douyin_product_events.promotions.DouyinPromotionNameResolver", return_value=resolver):
            preparer.return_value.prepare.return_value = prepared
            worker._run_python_product_listener(
                self.monitor["id"], self.session_id, self.monitor["url"], stop, refresh_catalog=force,
            )

    def test_cached_catalog_and_known_message_connect_without_http_or_reusing_old_current_product(self):
        self.live.replace_monitor_products(self.monitor["id"], {"known": "已知商品"})
        self.live.set_current_product(self.session_id, promotion_id="old", event_time=1, received_at=1)
        self.live.stop_session(self.monitor["id"], self.session_id)
        self.session_id = self.live.start_session(self.monitor["id"], Path(self.temp.name) / "next")
        resolver = Mock()
        self.run_listener(resolver)
        resolver.lookup_catalog.assert_not_called()
        self.assertIsNone(self.live.recording_session(self.monitor["id"], self.session_id)["current_product"])
        event = ProductChangeEvent(1, 2000, 2000, "known", None, None, "live_shopping")
        self.run_listener(resolver, [event])
        resolver.lookup_catalog.assert_not_called()
        current = self.live.recording_session(self.monitor["id"], self.session_id)["current_product"]
        self.assertEqual((current["promotion_id"], current["product_name"]), ("known", "已知商品"))

    def test_explicit_catalog_retry_fetches_even_with_cached_directory(self):
        self.live.replace_monitor_products(self.monitor["id"], {"old": "旧目录"})
        resolver = Mock(lookup_catalog=Mock(return_value=({"new": "新目录"}, ("new", "新目录"))))
        self.run_listener(resolver, force=True)
        resolver.lookup_catalog.assert_called_once()
        self.assertEqual(self.live.product_name(self.monitor["id"], "new"), "新目录")
        self.assertIsNone(self.live.recording_session(self.monitor["id"], self.session_id)["current_product"],
                          "refreshing a reused directory cannot invent a current product")

    def test_new_session_seeds_current_product_from_one_page_even_with_cached_catalog(self):
        self.live.replace_monitor_products(self.monitor["id"], {"known": "已知商品"})
        self.live.stop_session(self.monitor["id"], self.session_id)
        self.session_id = self.live.start_session(self.monitor["id"], Path(self.temp.name) / "seeded")
        resolver = Mock(lookup_current=Mock(return_value=("known", "已知商品")))
        self.run_listener(resolver)
        resolver.lookup_current.assert_called_once()
        resolver.lookup_catalog.assert_not_called()
        result = self.live.recording_session(self.monitor["id"], self.session_id)
        self.assertEqual(result["current_product"]["promotion_id"], "known")
        self.assertEqual([p["promotion_id"] for p in result["product_periods"]], ["known"])

    def test_probe_current_product_is_quiet_when_it_fails_or_is_unsupported(self):
        from app.live_monitoring.service import probe_current_product

        failing = Mock(lookup_current=Mock(side_effect=RuntimeError("网络失败")))
        self.assertFalse(probe_current_product(self.live, self.session_id, self.monitor["id"],
                                               failing, threading.Event()))
        self.assertIsNone(self.live.recording_session(self.monitor["id"], self.session_id)["current_product"])
        self.assertIsNone(self.live.recording_session(self.monitor["id"], self.session_id)["product_catalog_error"],
                          "a failed probe must not report a competing catalog error")
        self.assertFalse(probe_current_product(self.live, self.session_id, self.monitor["id"],
                                               Mock(), threading.Event()))

    def test_stopping_the_listener_keeps_a_connection_error_and_explains_a_quiet_session(self):
        self.live.set_product_listener_state(self.session_id, "listening")
        self.assertTrue(self.live.mark_product_listener_stopped(self.session_id, "正常收尾"))
        self.assertEqual(self.live.recording_session(self.monitor["id"], self.session_id)["product_listener"]["status"],
                         "stopped")
        self.live.set_product_listener_state(self.session_id, "error", "Python 商品 WebSocket 连接异常：1006")
        self.assertFalse(self.live.mark_product_listener_stopped(self.session_id, "不应写入"))
        listener = self.live.recording_session(self.monitor["id"], self.session_id)["product_listener"]
        self.assertEqual(listener["status"], "error")
        self.assertEqual(listener["detail"], "Python 商品 WebSocket 连接异常：1006")

        self.live.set_product_listener_state(self.session_id, "listening")
        self.assertTrue(self.live.mark_product_listener_stopped(self.session_id, "已停止"))
        listener = self.live.recording_session(self.monitor["id"], self.session_id)["product_listener"]
        self.assertEqual((listener["status"], listener["detail"]), ("stopped", "已停止"))
        self.assertIn("本场没有讲解记录", self.live.listener_detail(self.session_id, self.monitor["id"]))

    def test_connected_session_without_messages_is_reported_as_silent(self):
        from unittest.mock import patch as _patch
        from app.live_monitoring import service as service_module

        self.live.set_product_listener_state(self.session_id, "listening")
        detail = lambda: self.live.recording_session(self.monitor["id"], self.session_id)["product_listener"]
        self.assertFalse(detail()["silent"], "a freshly connected listener is not silent yet")
        with _patch.object(service_module.time, "time",
                           return_value=detail()["updated"] + service_module.LISTENER_SILENCE_SECONDS + 1):
            self.assertTrue(detail()["silent"])
            self.assertIn("尚未收到商品消息", detail()["silent_detail"])
        self.live.set_current_product(self.session_id, promotion_id="any", event_time=1, received_at=1)
        self.live.record_product_event(self.session_id, event_key="k", promotion_id="any")
        self.assertFalse(detail()["silent"], "a received message clears the silent state")
