import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings
from app.live_monitoring import LiveMonitorSettings, LiveMonitorWorker, LiveStore, normalize_douyin_url
from app.core.storage import Store


def fake_media_process(args, **_kwargs):
    if "-show_streams" in args:
        return json.dumps({"streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ]})
    return Path(args[-1]).write_bytes(b"clip")


class LiveMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name))
        self.settings.prepare()
        self.store = Store(self.settings.database)
        self.live = LiveStore(self.store)
        self.app = create_app(self.settings, token="test-token")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Session-Token": "test-token"}

    def test_monitor_api_returns_all_recording_sessions_intersecting_window(self):
        monitor = self.live.create_monitor("https://live.douyin.com/timeline", "时间线")
        expected = set()

        def recorded(started, ended):
            with patch("app.live_monitoring.service.time.time", return_value=started):
                session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "recording")
            if ended is not None:
                with patch("app.live_monitoring.service.time.time", return_value=ended):
                    self.live.stop_session(monitor["id"], session_id)
            return session_id

        recorded(500, 1000)  # Ends exactly at the left boundary: excluded.
        expected.add(recorded(900, 1100))  # Starts before the window: included.
        for index in range(25):
            expected.add(recorded(1200 + index * 10, 1205 + index * 10))
        recorded(2000, 2100)  # Starts exactly at the right boundary: excluded.
        expected.add(recorded(2000, None))  # A recording just starting now is visible.

        response = self.client.get("/api/v1/live/monitors", headers=self.headers,
                                   params={"window_start": 1000, "window_end": 2000,
                                           "include_end_recording": True})
        self.assertEqual(response.status_code, 200, response.text)
        sessions = response.json()[0]["sessions"]
        self.assertEqual({item["id"] for item in sessions}, expected)
        self.assertEqual(len(sessions), 27)

    def test_monitor_api_defaults_to_recent_day_and_validates_window(self):
        monitor = self.live.create_monitor("https://live.douyin.com/timeline-default", "时间线")
        with patch("app.live_monitoring.service.time.time", return_value=1000):
            old_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "old")
            self.live.stop_session(monitor["id"], old_id)
        with patch("app.live_monitoring.service.time.time", return_value=99900):
            recent_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "recent")
        with patch("app.live_monitoring.service.time.time", return_value=100000):
            response = self.client.get("/api/v1/live/monitors", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["id"] for item in response.json()[0]["sessions"]], [recent_id])
        for params in ({"window_start": 1000}, {"window_end": 2000},
                       {"window_start": 2000, "window_end": 1000},
                       {"window_start": 1000, "window_end": 1000},
                       {"window_start": "nan", "window_end": 2000},
                       {"window_start": 0, "window_end": "inf"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/v1/live/monitors", headers=self.headers,
                                                 params=params).status_code, 422)

    def test_monitor_api_historical_days_keep_cross_midnight_recordings_and_live_state(self):
        monitor = self.live.create_monitor("https://live.douyin.com/history", "历史录制")
        # Local midnight in Asia/Shanghai: Oct 22 = 1792598400; Oct 23 = 1792684800.
        sessions = []
        for started, ended in ((1792594800, 1792602000), (1792677600, 1792684800),
                               (1792684800, 1792688400), (1792720800, None)):
            with patch("app.live_monitoring.service.time.time", return_value=started):
                session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "history")
            if ended is not None:
                with patch("app.live_monitoring.service.time.time", return_value=ended):
                    self.live.stop_session(monitor["id"], session_id)
            sessions.append(session_id)
        for start, end, expected in ((1792598400, 1792684800, sessions[:2]),
                                     (1792684800, 1792771200, sessions[2:])):
            with self.subTest(day_start=start):
                response = self.client.get("/api/v1/live/monitors", headers=self.headers,
                                           params={"window_start": start, "window_end": end})
                self.assertEqual(response.status_code, 200, response.text)
                listed = response.json()[0]
                self.assertEqual({item["id"] for item in listed["sessions"]}, set(expected))
                self.assertEqual(listed["live_state"], "recording")

    def test_monitor_api_historical_day_excludes_recording_starting_at_next_midnight(self):
        monitor = self.live.create_monitor("https://live.douyin.com/midnight", "午夜")
        with patch("app.live_monitoring.service.time.time", return_value=1792684800):
            session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "midnight")
        response = self.client.get("/api/v1/live/monitors", headers=self.headers,
                                   params={"window_start": 1792598400, "window_end": 1792684800})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]["sessions"], [])
        recent = self.client.get("/api/v1/live/monitors", headers=self.headers,
                                 params={"window_start": 1792598400, "window_end": 1792684800,
                                         "include_end_recording": True})
        self.assertEqual([item["id"] for item in recent.json()[0]["sessions"]], [session_id])

    def test_recording_session_api_reads_outside_window_and_distinguishes_missing_from_failure(self):
        monitor = self.live.create_monitor("https://live.douyin.com/retained", "保留选择")
        other = self.live.create_monitor("https://live.douyin.com/other", "其他直播间")
        with patch("app.live_monitoring.service.time.time", return_value=1000):
            session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "retained")
            self.live.stop_session(monitor["id"], session_id)
        path = Path(self.temp.name) / "retained" / "segment.ts"
        path.write_bytes(b"recording")
        segment_id = self.live.upsert_segment(session_id, path, 7, "completed", 9, 1_000_000)
        self.live.set_current_product(session_id, promotion_id="product", product_name="保留的商品",
                                      event_time=1000, received_at=1000)
        url = f"/api/v1/live/monitors/{monitor['id']}/sessions/{session_id}"
        self.assertEqual(self.client.get(url).status_code, 401)
        listed = self.client.get("/api/v1/live/monitors", headers=self.headers,
                                 params={"window_start": 2000, "window_end": 3000})
        self.assertTrue(all(not item["sessions"] for item in listed.json()))
        response = self.client.get(url, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], session_id)
        self.assertEqual(response.json()["segments"][0]["id"], segment_id)
        self.assertEqual(response.json()["current_product"]["product_name"], "保留的商品")
        wrong_room = self.client.get(f"/api/v1/live/monitors/{other['id']}/sessions/{session_id}",
                                     headers=self.headers)
        self.assertEqual(wrong_room.status_code, 200)
        self.assertIsNone(wrong_room.json(), "a session cannot be read under another monitor")
        with patch.object(LiveStore, "recording_session", side_effect=RuntimeError("storage unavailable")):
            with TestClient(self.app, raise_server_exceptions=False) as failing_client:
                failed = failing_client.get(url, headers=self.headers)
            self.assertEqual(failed.status_code, 500, "storage errors must not look like a missing session")
        with self.store.connection() as conn:
            conn.execute("DELETE FROM live_sessions WHERE id=?", (session_id,))
        missing = self.client.get(url, headers=self.headers)
        self.assertEqual(missing.status_code, 200)
        self.assertIsNone(missing.json(), "only a successful absence response confirms removal")

    def test_recording_end_time_source_distinguishes_recovery_from_observed_failures(self):
        monitor = self.live.create_monitor("https://live.douyin.com/recovery-source", "结束时间来源")
        with patch("app.live_monitoring.service.time.time", return_value=1000):
            stopped = self.live.start_session(monitor["id"], Path(self.temp.name) / "stopped")
        with patch("app.live_monitoring.service.time.time", return_value=1100):
            self.live.stop_session(monitor["id"], stopped)
            failed = self.live.start_session(monitor["id"], Path(self.temp.name) / "failed")
        with patch("app.live_monitoring.service.time.time", return_value=1200):
            self.live.stop_session(monitor["id"], failed, "failed")
            recovered = self.live.start_session(monitor["id"], Path(self.temp.name) / "recovered")
        url = f"/api/v1/live/monitors/{monitor['id']}/sessions/"
        active = self.client.get(url + recovered, headers=self.headers).json()
        self.assertIsNone(active["end_time_source"])
        with patch("app.live_monitoring.service.time.time", return_value=2000):
            LiveMonitorWorker(self.settings, resolver=object())
        listed = self.client.get("/api/v1/live/monitors", headers=self.headers,
                                 params={"window_start": 900, "window_end": 2100}).json()[0]["sessions"]
        details = {item["id"]: item for item in listed}
        self.assertEqual(details[stopped]["end_time_source"], "observed")
        self.assertEqual(details[failed]["status"], "failed")
        self.assertEqual(details[failed]["end_time_source"], "observed")
        self.assertEqual(details[recovered]["status"], "failed")
        self.assertEqual(details[recovered]["end_time_source"], "recovery_check")
        self.assertEqual(details[recovered]["ended"], 2000)
        self.assertEqual(self.client.get(url + recovered, headers=self.headers).json(), details[recovered])
        # A later worker restart must not replace the first recovery check's time.
        with patch("app.live_monitoring.service.time.time", return_value=3000):
            LiveMonitorWorker(self.settings, resolver=object())
        self.assertEqual(self.client.get(url + recovered, headers=self.headers).json()["ended"], 2000)

    def test_douyin_url_validation_supports_share_short_link_only(self):
        self.assertEqual(normalize_douyin_url("复制这条消息 https://v.douyin.com/abc123/ 去看看"),
                         "https://v.douyin.com/abc123/")
        self.assertEqual(normalize_douyin_url("https://live.douyin.com/123"), "https://live.douyin.com/123")
        for invalid in ("https://example.com/live/123", "file:///C:/video.mp4", "not a url"):
            with self.assertRaises(ValueError):
                normalize_douyin_url(invalid)
        with self.assertRaisesRegex(ValueError, "主播主页"):
            normalize_douyin_url("https://www.douyin.com/user/MS4wLjABAAAA")

    def test_product_listener_preparation_error_exposes_safe_value_error_reason(self):
        from app.live_monitoring.service import _safe_listener_exception

        self.assertEqual(
            _safe_listener_exception(ValueError("抖音返回该直播间当前未开播")),
            "商品事件会话准备失败：抖音返回该直播间当前未开播",
        )
        self.assertEqual(
            _safe_listener_exception(RuntimeError("details withheld")),
            "商品事件会话准备失败：RuntimeError",
        )

    def test_authenticated_monitor_api_add_settings_and_remove_retains_recording_files(self):
        self.assertEqual(self.client.get("/api/v1/live/monitors").status_code, 401)
        created = self.client.post("/api/v1/live/monitors", headers=self.headers,
                                   json={"url": "https://live.douyin.com/room-1", "name": "测试直播间"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue(created.json()["auto_record"] and created.json()["notify_live"])
        monitor_id = created.json()["id"]
        duplicate = self.client.post("/api/v1/live/monitors", headers=self.headers,
                                     json={"url": "https://live.douyin.com/room-1"})
        self.assertEqual(duplicate.status_code, 422)
        changed = self.client.patch(f"/api/v1/live/monitors/{monitor_id}", headers=self.headers,
                                    json={"auto_record": False}).json()
        self.assertEqual((changed["auto_record"], changed["notify_live"]), (False, True))
        changed = self.client.patch(f"/api/v1/live/monitors/{monitor_id}", headers=self.headers,
                                    json={"notify_live": False}).json()
        self.assertEqual((changed["auto_record"], changed["notify_live"]), (False, False))
        offline_start = self.client.post(f"/api/v1/live/monitors/{monitor_id}/recording", headers=self.headers,
                                         json={"action": "start"})
        self.assertEqual(offline_start.status_code, 409, offline_start.text)

        session_id = self.live.start_session(monitor_id, Path(self.temp.name) / "recording")
        with self.store.connection() as conn:
            conn.execute("UPDATE live_sessions SET status='stopped',ended=? WHERE id=?", (1, session_id))
            conn.execute("UPDATE live_monitors SET current_session_id=NULL WHERE id=?", (monitor_id,))
        file = Path(self.temp.name) / "recording" / "segment-000001.ts"
        file.write_bytes(b"recorded-source")
        self.live.upsert_segment(session_id, file, 1, "completed", file.stat().st_size, 1_000_000)
        response = self.client.delete(f"/api/v1/live/monitors/{monitor_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["recordings_retained"])
        self.assertTrue(file.exists())

    def test_only_completed_segments_import_and_duplicate_import_reuses_job(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-2", "room")
        session = self.live.start_session(monitor["id"], Path(self.temp.name) / "rec")
        path = Path(self.temp.name) / "rec" / "segment-000001.ts"
        path.write_bytes(b"ts")
        segment_id = self.live.upsert_segment(session, path, 1, "recording", 2)
        denied = self.client.post(f"/api/v1/live/segments/{segment_id}/import", headers=self.headers)
        self.assertEqual(denied.status_code, 409)
        self.live.upsert_segment(session, path, 1, "completed", 2, 5_000_000)
        first = self.client.post(f"/api/v1/live/segments/{segment_id}/import", headers=self.headers)
        second = self.client.post(f"/api/v1/live/segments/{segment_id}/import", headers=self.headers)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(first.json()["kind"], "import")
        self.assertEqual(first.json()["payload"]["live_segment_id"], segment_id)

    def test_delete_segment_removes_sealed_file_and_rejects_recording_or_imported_sources(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-delete-segment", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "rec")
        root = self.settings.data_dir / "recordings" / "live"
        root.mkdir(parents=True)
        recording_path = root / "recording.ts"
        recording_path.write_bytes(b"recording")
        recording_id = self.live.upsert_segment(
            session_id, recording_path, 1, "recording", recording_path.stat().st_size
        )
        denied = self.client.delete(f"/api/v1/live/segments/{recording_id}", headers=self.headers)
        self.assertEqual(denied.status_code, 409)
        self.assertTrue(recording_path.exists())

        self.live.upsert_segment(session_id, recording_path, 1, "completed", recording_path.stat().st_size)
        self.live.finish_import(recording_id, "asset-segment")
        self.store.put("asset", {"id": "asset-segment", "path": str(recording_path)})
        denied = self.client.delete(f"/api/v1/live/segments/{recording_id}", headers=self.headers)
        self.assertEqual(denied.status_code, 409)
        self.assertIn("视频项目引用", denied.json()["detail"])
        self.assertTrue(recording_path.exists())

        with self.store.connection() as conn:
            conn.execute("DELETE FROM objects WHERE kind='asset' AND id='asset-segment'")
        deleted = self.client.delete(f"/api/v1/live/segments/{recording_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["file_deleted"])
        self.assertFalse(recording_path.exists())
        self.assertIsNone(self.live.segment_status(recording_path))

    def test_start_recorder_uses_stream_copy_and_1200_second_segments(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-3", "room")
        class Process:
            pid = 12345
            stdin = None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
        with patch("app.live_monitoring.service.subprocess.Popen", return_value=Process()) as popen:
            worker = LiveMonitorWorker(self.settings, resolver=object())
            with patch.object(worker, "_start_python_product_listener") as product_listener:
                worker._start_recorder(monitor, "https://media.example/live.flv")
        product_listener.assert_called_once_with(monitor, self.live.monitor(monitor["id"])["current_session_id"])
        args = popen.call_args.args[0]
        self.assertIn("1200", args)
        self.assertIn("copy", args)
        self.assertIn("segment", args)
        self.assertTrue(args[-1].endswith("-%06d.ts"))
        self.assertEqual(self.live.monitor(monitor["id"])["live_state"], "recording")

    def test_python_product_listener_records_confirmed_events_in_formal_timeline(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-shadow", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "shadow-recording")
        from types import SimpleNamespace
        from threading import Event
        from app.douyin_product_events.protocol import ProductChangeEvent

        confirmed = ProductChangeEvent(9, 1_800_000_002, 1_800_000_001,
                                       "promotion-7", None, None, "live_shopping")
        unverified = ProductChangeEvent(10, 1_800_000_003, 1_800_000_003,
                                        "promotion-8", None, None, "product_change")
        stop_event = Event()
        session = SimpleNamespace(room=SimpleNamespace(room_id="room"), user_agent="test-agent", cookie="")
        self.store.put("config", {"id": "douyin_cookie", "value": "sessionid=from-app-settings"})

        class Preparer:
            def prepare(self, _url): return session
            def close(self): pass

        class Client:
            def __init__(self, on_event, on_state, **_options):
                self.on_event, self.on_state = on_event, on_state
            def start_prepared(self, _session):
                self.on_state("listening", "")
                self.on_event(confirmed)
                self.on_event(unverified)
                stop_event.set()
            def stop(self): pass

        worker = LiveMonitorWorker(self.settings, resolver=object())
        class NameResolver:
            def __init__(self, *_args, **_kwargs): pass
            def lookup(self, _promotion_id): return ""
            def close(self): pass

        with patch("app.douyin_product_events.DouyinWebcastSessionPreparer", return_value=Preparer()) as prepare, \
             patch("app.douyin_product_events.DouyinProductEventClient", Client), \
             patch("app.douyin_product_events.promotions.DouyinPromotionNameResolver", NameResolver):
            worker._run_python_product_listener(monitor["id"], session_id, monitor["url"], stop_event)

        prepare.assert_called_once_with(cookie="sessionid=from-app-settings")

        result = self.live.list_monitors()[0]["sessions"][0]
        self.assertEqual(len(result["product_events"]), 1)
        event = result["product_events"][0]
        self.assertEqual(event["product_name"], "")
        self.assertEqual(event["promotion_id"], "promotion-7")
        self.assertEqual(event["source"], "python_live_shopping")
        self.assertEqual(event["event_time"], confirmed.event_time)
        self.assertEqual(event["received_at"], confirmed.received_at)
        self.assertEqual(result["product_listener"]["status"], "stopped")
        self.assertFalse({"product_shadow", "product_shadow_samples", "product_shadow_events"} & result.keys())

    def test_product_change_events_are_deduplicated_per_session_and_listed_with_listener_state(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-products", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "product-recording")
        event_time, received_at = 1_800_000_000.0, 1_800_000_001.0
        event_id = self.live.record_product_event(
            session_id, event_key="message-1:promotion-22:1", promotion_id="promotion-22",
            product_name="针织开衫", explain_type=1, event_time=event_time, received_at=received_at)
        duplicate_id = self.live.record_product_event(
            session_id, event_key="message-1:promotion-22:1", promotion_id="promotion-22",
            product_name="针织开衫", explain_type=1, event_time=event_time, received_at=received_at)
        self.live.set_product_listener_state(session_id, "listening")
        self.live.stop_session(monitor["id"], session_id)
        other_session = self.live.start_session(monitor["id"], Path(self.temp.name) / "other-recording")
        other_id = self.live.record_product_event(
            other_session, event_key="message-1:promotion-22:1", promotion_id="promotion-22",
            event_time=event_time, received_at=received_at)
        self.assertEqual(event_id, duplicate_id)
        self.assertNotEqual(event_id, other_id)
        sessions = self.live.list_monitors()[0]["sessions"]
        by_id = {session["id"]: session for session in sessions}
        self.assertEqual(len(by_id[session_id]["product_events"]), 1)
        self.assertEqual(by_id[session_id]["product_events"][0]["event_time"], event_time)
        self.assertEqual(by_id[session_id]["product_listener"]["status"], "listening")

    def test_product_name_lookup_updates_only_matching_events_without_overwriting_names(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-product-name", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "name-recording")
        self.live.record_product_event(session_id, event_key="m1:p1", promotion_id="p1")
        self.live.record_product_event(session_id, event_key="m2:p2", promotion_id="p2")
        self.live.record_product_event(session_id, event_key="m3:p1", promotion_id="p1",
                                      product_name="用户确认名称")

        self.live.update_product_name(session_id, "p1", "接口商品标题")

        events = self.live.list_monitors()[0]["sessions"][0]["product_events"]
        self.assertEqual(sorted(event["product_name"] for event in events),
                         ["", "接口商品标题", "用户确认名称"])

    def test_session_product_catalog_replaces_atomically_and_resolves_names(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-catalog", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "catalog")
        self.live.replace_session_products(session_id, {
            "p1": "第一件商品", "p2": "第二件商品",
        })
        self.assertEqual(self.live.product_name(session_id, "p2"), "第二件商品")

        self.live.replace_session_products(session_id, {"p3": "更新后的完整目录"})
        self.assertEqual(self.live.product_name(session_id, "p1"), "")
        self.assertEqual(self.live.product_name(session_id, "p3"), "更新后的完整目录")

        self.live.replace_session_products(session_id, {})
        self.assertEqual(self.live.product_name(session_id, "p3"), "")

    def test_product_catalog_request_error_is_exposed_and_cleared_after_success(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-catalog-error", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "catalog-error")
        detail = {"summary": "请求失败", "response": {"http_status": 403}}
        self.live.set_product_catalog_error(session_id, detail)
        listed = self.live.list_monitors()[0]["sessions"][0]["product_catalog_error"]
        self.assertEqual(listed["detail"], detail)
        self.live.set_product_catalog_error(session_id, None)
        self.assertIsNone(self.live.list_monitors()[0]["sessions"][0]["product_catalog_error"])

    def test_current_product_snapshot_is_listed_and_name_lookup_updates_it(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-current-product", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "current-product")
        self.live.set_current_product(
            session_id, promotion_id="p-current", event_time=100, received_at=101,
            product_name="初始商品名",
        )

        session = self.live.list_monitors()[0]["sessions"][0]
        self.assertEqual(session["current_product"]["promotion_id"], "p-current")
        self.assertEqual(session["current_product"]["product_name"], "初始商品名")
        self.assertEqual(session["product_events"], [])

        self.live.update_product_name(session_id, "p-current", "当前商品标题")
        session = self.live.list_monitors()[0]["sessions"][0]
        self.assertEqual(session["current_product"]["product_name"], "初始商品名")

        self.live.set_current_product(
            session_id, promotion_id="p-next", event_time=200, received_at=201,
        )
        session = self.live.list_monitors()[0]["sessions"][0]
        self.assertEqual(session["current_product"]["promotion_id"], "p-next")
        self.assertEqual(session["current_product"]["product_name"], "")

    def test_current_product_changes_create_and_close_product_periods(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-product-periods", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "periods")
        with self.store.connection() as conn:
            started = conn.execute("SELECT started FROM live_sessions WHERE id=?", (session_id,)).fetchone()[0] - 30
            conn.execute("UPDATE live_sessions SET started=? WHERE id=?", (started, session_id))
        self.live.set_current_product(session_id, promotion_id="p1", event_time=started + 1,
                                      received_at=started + 1, product_name="第一件")
        self.live.set_current_product(session_id, promotion_id="p1", event_time=started + 2,
                                      received_at=started + 2, product_name="第一件")
        self.live.set_current_product(session_id, promotion_id="p2", event_time=started + 5,
                                      received_at=started + 5, product_name="第二件")
        self.live.set_current_product(session_id, promotion_id="stale", event_time=started + 3,
                                      received_at=started + 3, product_name="过期商品")
        self.live.stop_session(monitor["id"], session_id)

        with self.store.connection() as conn:
            periods = [dict(row) for row in conn.execute(
                "SELECT * FROM live_product_periods WHERE session_id=? ORDER BY started", (session_id,))]
        self.assertEqual([period["promotion_id"] for period in periods], ["p1", "p2"])
        self.assertEqual(periods[0]["started"], started)
        self.assertEqual(periods[0]["ended"], started + 5)
        self.assertGreaterEqual(periods[1]["ended"], periods[1]["started"])

    def _two_segment_session(self, name: str):
        """Session with two 20 s segments and products switching at 5 s and 25 s."""
        monitor = self.live.create_monitor(f"https://live.douyin.com/{name}", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / name)
        with self.store.connection() as conn:
            started = conn.execute("SELECT started FROM live_sessions WHERE id=?", (session_id,)).fetchone()[0] - 60
            conn.execute("UPDATE live_sessions SET started=? WHERE id=?", (started, session_id))
        directory = self.settings.data_dir / "recordings" / "live" / monitor["id"] / session_id
        directory.mkdir(parents=True, exist_ok=True)
        segment_ids = []
        for index in (1, 2):
            path = directory / f"raw-{index}.ts"
            path.write_bytes(b"raw-recording")
            segment_ids.append(self.live.upsert_segment(session_id, path, index, "completed",
                                                        path.stat().st_size, 20_000_000))
        for offset, promotion_id, name_ in ((0, "p1", "针织开衫"), (5, "p2", "半身裙"), (25, "p3", "风衣")):
            self.live.set_current_product(session_id, promotion_id=promotion_id, event_time=started + offset,
                                          received_at=started + offset, product_name=name_)
        self.live.stop_session(monitor["id"], session_id)
        with self.store.connection() as conn:
            conn.execute("UPDATE live_product_periods SET ended=? WHERE session_id=? AND promotion_id='p3'",
                         (started + 40, session_id))
        return monitor, session_id, segment_ids

    def test_stopping_recording_no_longer_generates_product_clips_automatically(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-no-auto-clips", "room")
        self.assertFalse(hasattr(LiveMonitorWorker(self.settings, resolver=object()), "clip_executor"))
        self.assertEqual(self.live.list_monitors()[0]["id"], monitor["id"])

    def test_manual_split_cuts_only_the_requested_segment_on_its_local_timeline(self):
        from app.live_monitoring.service import generate_segment_product_clips

        _monitor, session_id, (first, second) = self._two_segment_session("room-manual-split")
        queued = self.client.post(f"/api/v1/live/segments/{second}/product-clips", headers=self.headers)
        repeated = self.client.post(f"/api/v1/live/segments/{second}/product-clips", headers=self.headers)
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(queued.json()["id"], repeated.json()["id"])
        self.assertEqual(queued.json()["kind"], "live_product_clips")

        calls = []

        def write_clip(args, **_kwargs):
            if "-show_streams" in args:
                return json.dumps({"streams": [
                    {"codec_type": "video", "codec_name": "h264"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ]})
            calls.append(args)
            Path(args[-1]).write_bytes(b"product-clip")

        with (
            patch("app.media.processing.run_process", side_effect=write_clip),
            patch("app.media.processing.probe", return_value={"duration_us": 5_000_000}),
        ):
            result = generate_segment_product_clips(self.store, self.settings, queued.json()["payload"])

        # Segment 2 covers 20–40 s: p2 (20–25 s → local 0–5) and p3 (25–40 s → local 5–20).
        self.assertEqual(result, {"segment_id": second, "created": 2, "skipped": 0, "failed": 0})
        clip_calls = [args for args in calls if "-ss" in args]
        windows = [(float(a[a.index("-ss") + 1]), float(a[a.index("-t") + 1])) for a in clip_calls]
        self.assertEqual(windows, [(0.0, 5.0), (5.0, 15.0)])
        session = self.live.list_monitors()[0]["sessions"][0]
        self.assertEqual({clip["promotion_id"] for clip in session["product_clips"]}, {"p2", "p3"})
        self.assertTrue(all(clip["segment_id"] == second for clip in session["product_clips"]))
        self.assertTrue(all(Path(clip["path"]).suffix == ".mp4" for clip in session["product_clips"]))
        segments = {segment["id"]: segment for segment in session["segments"]}
        self.assertEqual(segments[second]["clip_job"]["id"], queued.json()["id"])
        self.assertIsNone(segments[first]["clip_job"])
        with (
            patch("app.media.processing.run_process", side_effect=write_clip),
            patch("app.media.processing.probe", return_value={"duration_us": 5_000_000}),
        ):
            again = generate_segment_product_clips(self.store, self.settings, {"segment_id": second})
        self.assertEqual(again["skipped"], 2)

    def test_segment_scanner_does_not_register_generated_product_clips_as_recordings(self):
        from app.live_monitoring.service import generate_segment_product_clips

        monitor, session_id, (segment_id, _second) = self._two_segment_session("room-scan-product-clips")
        with (
            patch("app.media.processing.run_process", side_effect=fake_media_process),
            patch("app.media.processing.probe", return_value={"duration_us": 5_000_000}),
        ):
            generate_segment_product_clips(self.store, self.settings, {"segment_id": segment_id})

        worker = LiveMonitorWorker(self.settings, resolver=object())
        worker._scan_segments(monitor["id"], session_id, final=True)

        with self.store.connection() as conn:
            rows = conn.execute("SELECT path FROM live_segments WHERE session_id=?", (session_id,)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not Path(row["path"]).name.startswith("商品片段_") for row in rows))

    def test_manual_split_rejects_recording_segment_and_blocks_delete_while_active(self):
        _monitor, _session_id, (first, _second) = self._two_segment_session("room-split-guards")
        queued = self.client.post(f"/api/v1/live/segments/{first}/product-clips", headers=self.headers)
        self.assertEqual(queued.status_code, 202, queued.text)
        blocked = self.client.delete(f"/api/v1/live/segments/{first}", headers=self.headers)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segments SET status='recording' WHERE id=?", (first,))
            conn.execute("UPDATE jobs SET state='succeeded' WHERE id=?", (queued.json()["id"],))
        rejected = self.client.post(f"/api/v1/live/segments/{first}/product-clips", headers=self.headers)
        self.assertEqual(rejected.status_code, 409, rejected.text)

    def _recording_worker(self, live_state: dict):
        class Resolver:
            async def resolve(self, _url):
                return {"is_live": live_state["live"], "stream_url": "https://media.example/live.flv",
                        "anchor_name": "", "room_id": "1"}

        class Process:
            pid = 4321
            stdin = None
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0

        worker = LiveMonitorWorker(self.settings, resolver=Resolver(), poll_seconds=20)
        started = []

        def start(monitor, _url):
            session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / monitor["id"])
            worker.processes[monitor["id"]] = (Process(), session_id)
            started.append(monitor["id"])

        patches = [patch.object(worker, "_start_recorder", side_effect=start),
                   patch.object(worker, "_stop_process"), patch.object(worker, "_notify_live"),
                   patch.object(worker, "_scan_segments")]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return worker, started

    def test_poll_records_liveness_and_auto_records_without_a_monitor_switch(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-liveness", "room")
        state = {"live": False}
        worker, started = self._recording_worker(state)
        worker.run_once(now=1000)
        self.assertIs(self.live.list_monitors()[0]["is_live"], False)
        self.assertEqual(started, [])

        state["live"] = True
        worker.run_once(now=1020)
        listed = self.live.monitor(monitor["id"])
        self.assertIs(listed["is_live"], True)
        self.assertEqual(started, [monitor["id"]])
        worker._notify_live.assert_called_once()

        # Manual stop takes effect on the next loop and holds until the room goes offline.
        self.live.request_recording(monitor["id"], "stop")
        worker.run_once(now=1021)
        self.assertNotIn(monitor["id"], worker.processes)
        self.assertEqual(self.live.monitor(monitor["id"])["live_state"], "live")
        worker.run_once(now=1041)
        self.assertEqual(started, [monitor["id"]])
        worker._notify_live.assert_called_once()

        state["live"] = False
        worker.run_once(now=1061)
        self.assertFalse(self.live.monitor(monitor["id"])["record_held"])
        state["live"] = True
        worker.run_once(now=1081)
        self.assertEqual(started, [monitor["id"], monitor["id"]])
        self.assertEqual(worker._notify_live.call_count, 2)

    def test_manual_recording_starts_only_on_request_when_auto_record_is_off(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-manual", "room")
        self.live.update_settings(monitor["id"], LiveMonitorSettings(auto_record=False, notify_live=False))
        state = {"live": True}
        worker, started = self._recording_worker(state)
        worker.run_once(now=1000)
        self.assertEqual(started, [])
        self.assertEqual(self.live.monitor(monitor["id"])["live_state"], "live")
        worker._notify_live.assert_not_called()

        self.live.request_recording(monitor["id"], "start")
        worker.run_once(now=1001)  # The request is applied without waiting for the 20 s poll.
        self.assertEqual(started, [monitor["id"]])

        state["live"] = False
        worker.run_once(now=1030)
        self.assertFalse(self.live.monitor(monitor["id"])["record_requested"])
        state["live"] = True
        worker.run_once(now=1060)
        self.assertEqual(started, [monitor["id"]])

    def test_open_product_clip_uses_system_player_or_explorer_and_stays_in_recordings_root(self):
        from app.live_monitoring.service import generate_segment_product_clips

        _monitor, _session_id, (first, _second) = self._two_segment_session("room-open-clip")
        with (
            patch("app.media.processing.run_process", side_effect=fake_media_process),
            patch("app.media.processing.probe", return_value={"duration_us": 5_000_000}),
        ):
            generate_segment_product_clips(self.store, self.settings, {"segment_id": first})
        clip = self.live.list_monitors()[0]["sessions"][0]["product_clips"][0]

        with patch("app.live_monitoring.service.os.startfile") as startfile, \
             patch("app.live_monitoring.service.subprocess.Popen") as popen:
            opened = self.client.post(f"/api/v1/live/product-clips/{clip['id']}/open", headers=self.headers, json={})
            self.assertEqual(opened.status_code, 200, opened.text)
            startfile.assert_called_once_with(str(Path(clip["path"]).resolve()))
            revealed = self.client.post(f"/api/v1/live/product-clips/{clip['id']}/open", headers=self.headers,
                                        json={"reveal": True})
            self.assertTrue(revealed.json()["revealed"])
            self.assertEqual(popen.call_args.args[0][:2], ["explorer", "/select,"])

        outside = Path(self.temp.name) / "outside.ts"
        outside.write_bytes(b"x")
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segment_product_clips SET path=? WHERE id=?", (str(outside), clip["id"]))
        rejected = self.client.post(f"/api/v1/live/product-clips/{clip['id']}/open", headers=self.headers, json={})
        self.assertEqual(rejected.status_code, 409, rejected.text)

    def test_open_segment_uses_system_player_or_explorer_and_stays_in_recordings_root(self):
        monitor, session_id, (segment_id, _second) = self._two_segment_session("room-open-segment")
        with self.store.connection() as conn:
            segment_path = conn.execute("SELECT path FROM live_segments WHERE id=?", (segment_id,)).fetchone()[0]

        with patch("app.live_monitoring.service.os.startfile") as startfile, \
             patch("app.live_monitoring.service.subprocess.Popen") as popen:
            opened = self.client.post(f"/api/v1/live/segments/{segment_id}/open", headers=self.headers, json={})
            self.assertEqual(opened.status_code, 200, opened.text)
            startfile.assert_called_once_with(str(Path(segment_path).resolve()))
            revealed = self.client.post(f"/api/v1/live/segments/{segment_id}/open", headers=self.headers,
                                        json={"reveal": True})
            self.assertTrue(revealed.json()["revealed"])
            self.assertEqual(popen.call_args.args[0][:2], ["explorer", "/select,"])

        outside = Path(self.temp.name) / "outside.ts"
        outside.write_bytes(b"x")
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segments SET path=? WHERE id=?", (str(outside), segment_id))
        rejected = self.client.post(f"/api/v1/live/segments/{segment_id}/open", headers=self.headers, json={})
        self.assertEqual(rejected.status_code, 409, rejected.text)

        with self.store.connection() as conn:
            conn.execute("UPDATE live_segments SET path=? WHERE id=?",
                         (str(self.settings.data_dir / "recordings" / "live" / monitor["id"] /
                              session_id / "missing.ts"), segment_id))
        missing = self.client.post(f"/api/v1/live/segments/{segment_id}/open", headers=self.headers, json={})
        self.assertEqual(missing.status_code, 409, missing.text)

    def test_generated_product_clip_can_be_imported_and_reused(self):
        from app.live_monitoring.service import generate_segment_product_clips

        _monitor, _session_id, (first, _second) = self._two_segment_session("room-import-product-clip")
        with (
            patch("app.media.processing.run_process", side_effect=fake_media_process),
            patch("app.media.processing.probe", return_value={"duration_us": 5_000_000}),
        ):
            generate_segment_product_clips(self.store, self.settings, {"segment_id": first})
        clip_id = self.live.list_monitors()[0]["sessions"][0]["product_clips"][0]["id"]

        first = self.client.post(f"/api/v1/live/product-clips/{clip_id}/import", headers=self.headers)
        second = self.client.post(f"/api/v1/live/product-clips/{clip_id}/import", headers=self.headers)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(first.json()["payload"]["live_product_clip_id"], clip_id)
        self.assertEqual(first.json()["payload"]["product_name"], "半身裙")

    def test_product_clip_delete_removes_unimported_file_and_retains_imported_project_file(self):
        from app.live_monitoring.service import generate_segment_product_clips

        _monitor, _session_id, (segment_id, _second) = self._two_segment_session("room-delete-product-clip")
        with (
            patch("app.media.processing.run_process", side_effect=fake_media_process),
            patch("app.media.processing.probe", return_value={"duration_us": 5_000_000}),
        ):
            generate_segment_product_clips(self.store, self.settings, {"segment_id": segment_id})
        clip = self.live.list_monitors()[0]["sessions"][0]["product_clips"][0]
        path = Path(clip["path"])
        deleted = self.client.delete(f"/api/v1/live/product-clips/{clip['id']}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["file_deleted"])
        self.assertFalse(path.exists())

        with (
            patch("app.media.processing.run_process", side_effect=fake_media_process),
            patch("app.media.processing.probe", return_value={"duration_us": 5_000_000}),
        ):
            generate_segment_product_clips(self.store, self.settings, {"segment_id": segment_id})
        clip = self.live.list_monitors()[0]["sessions"][0]["product_clips"][0]
        path = Path(clip["path"])
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segment_product_clips SET status='imported',asset_id='asset-test' WHERE id=?",
                         (clip["id"],))
        deleted = self.client.delete(f"/api/v1/live/product-clips/{clip['id']}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["project_file_retained"])
        self.assertTrue(path.exists())

    def test_product_clip_delete_rejects_importing_clip(self):
        clip_id = "clip-importing"
        with self.store.connection() as conn:
            conn.execute("INSERT INTO live_segment_product_clips(id,session_id,segment_id,segment_index,period_id,"
                         "promotion_id,start,end,path,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (clip_id, "session", "segment", 1, "period", "p1", 0, 1,
                          str(self.settings.data_dir / "recordings" / "live" / "clip.ts"), "importing"))
        response = self.client.delete(f"/api/v1/live/product-clips/{clip_id}", headers=self.headers)
        self.assertEqual(response.status_code, 409, response.text)

    def test_stale_initial_product_query_does_not_overwrite_newer_websocket_product(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-current-race", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "current-race")
        self.live.set_current_product(
            session_id, promotion_id="p-new", event_time=202, received_at=202,
        )
        self.live.set_current_product(
            session_id, promotion_id="p-stale", event_time=100, received_at=101,
            product_name="旧商品",
        )
        current = self.live.list_monitors()[0]["sessions"][0]["current_product"]
        self.assertEqual(current["promotion_id"], "p-new")

    def test_delete_monitor_removes_event_indexes_but_retains_recording_files(self):
        monitor = self.live.create_monitor("https://live.douyin.com/room-delete-event", "room")
        session_id = self.live.start_session(monitor["id"], Path(self.temp.name) / "delete-recording")
        self.live.record_product_event(session_id, event_key="m1:p1:2", promotion_id="p1")
        self.live.set_product_listener_state(session_id, "listening")
        with self.store.connection() as conn:
            conn.execute("UPDATE live_sessions SET status='stopped',ended=? WHERE id=?", (1, session_id))
            conn.execute("UPDATE live_monitors SET current_session_id=NULL WHERE id=?", (monitor["id"],))
        self.live.delete_monitor(monitor["id"])
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM live_product_events WHERE session_id=?", (session_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM live_session_current_product WHERE session_id=?", (session_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM live_product_listener_state WHERE session_id=?", (session_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM live_session_products WHERE session_id=?", (session_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM live_session_product_catalog_state WHERE session_id=?", (session_id,)).fetchone()[0], 0)

    def test_blank_monitor_name_uses_resolved_anchor_name_but_custom_name_is_preserved(self):
        automatic = self.live.create_monitor("https://live.douyin.com/room-auto", "")
        custom = self.live.create_monitor("https://live.douyin.com/room-custom", "我的备注")

        class Resolver:
            async def resolve(self, url):
                return {"is_live": False, "stream_url": None, "anchor_name": "主播名称"}

        worker = LiveMonitorWorker(self.settings, resolver=Resolver(), poll_seconds=0)
        worker.run_once()

        self.assertEqual(self.live.monitor(automatic["id"])["name"], "主播名称")
        self.assertEqual(self.live.monitor(custom["id"])["name"], "我的备注")


if __name__ == "__main__":
    unittest.main()
