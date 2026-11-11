import logging
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import create_app
from app.core import logs as logs_module
from app.core.config import Settings
from app.core.logs import PREVIOUS_RUNTIME_LOG, READ_LIMIT, RUNTIME_LOG, configure_logging
from app.core.storage import Store
from app.live_monitoring import LiveStore
from app.media.processing import MediaError, run_process


class LogApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.runtime_dir = root / "config" / "logs"
        self.runtime_dir.mkdir(parents=True)
        self.settings = replace(Settings(root / "work"), runtime_log=self.runtime_dir / RUNTIME_LOG)
        self.settings.prepare()
        self.store = Store(self.settings.database)
        self.task_dir = self.settings.data_dir / "logs"
        self.client = TestClient(create_app(self.settings, token="test-token"))
        self.addCleanup(self.client.close)
        self.headers = {"X-Session-Token": "test-token"}

    def get(self, path, **params):
        return self.client.get(f"/api/v1/logs{path}", headers=self.headers, params=params)

    def test_lists_non_empty_files_with_their_owner(self):
        job = self.store.enqueue("export", {})
        deleted_job = "job_" + "0" * 32
        clip_id = "product_clip_" + "a" * 32
        (self.runtime_dir / RUNTIME_LOG).write_text("runtime\n", encoding="utf-8")
        (self.task_dir / f"{job['id']}-0.log").write_text("ffmpeg\n", encoding="utf-8")
        (self.task_dir / f"{job['id']}-1.log").write_bytes(b"")
        (self.task_dir / f"{deleted_job}-concat.log").write_text("old\n", encoding="utf-8")
        (self.task_dir / f"product-clip-{clip_id}-h264_amf.log").write_text("encoder\n", encoding="utf-8")
        (self.task_dir / "notes.txt").write_text("not a log", encoding="utf-8")
        self.assertEqual(self.client.get("/api/v1/logs").status_code, 401)
        body = self.get("").json()
        self.assertTrue(body["runtime_available"])
        files = {item["name"]: item for item in body["files"]}
        self.assertEqual(set(files), {RUNTIME_LOG, f"{job['id']}-0.log", f"{deleted_job}-concat.log",
                                      f"product-clip-{clip_id}-h264_amf.log"},
                         "empty files and other file types are not listed")
        self.assertEqual(body["files"][0]["category"], "runtime", "the application log is listed first")
        listed = files[f"{job['id']}-0.log"]
        self.assertEqual((listed["category"], listed["group"], listed["group_type"], listed["job_kind"]),
                         ("task", job["id"], "job", "export"))
        self.assertIsNone(files[f"{deleted_job}-concat.log"]["job_kind"], "a removed job keeps its log without a kind")
        self.assertEqual(files[f"product-clip-{clip_id}-h264_amf.log"]["group_type"], "product_clip")

    def test_reads_the_end_then_only_appended_content(self):
        path = self.runtime_dir / RUNTIME_LOG
        path.write_bytes(b"first\n" + "中文\n".encode())
        first = self.get(f"/runtime/{RUNTIME_LOG}").json()
        self.assertEqual(first["text"], "first\n中文\n")
        self.assertEqual((first["start"], first["next"], first["truncated"], first["reset"]),
                         (0, path.stat().st_size, False, False))
        split = "末".encode()
        with path.open("ab") as file:
            file.write(b"tail " + split[:1])
        partial = self.get(f"/runtime/{RUNTIME_LOG}", after=first["next"]).json()
        self.assertEqual(partial["text"], "tail ", "a character still being written is left for the next read")
        with path.open("ab") as file:
            file.write(split[1:] + b"\n")
        rest = self.get(f"/runtime/{RUNTIME_LOG}", after=partial["next"]).json()
        self.assertEqual(rest["text"], "末\n")
        self.assertEqual(self.get(f"/runtime/{RUNTIME_LOG}", after=rest["next"]).json()["text"], "")
        path.write_bytes(b"new\n")
        restarted = self.get(f"/runtime/{RUNTIME_LOG}", after=rest["next"]).json()
        self.assertEqual((restarted["text"], restarted["reset"]), ("new\n", True),
                         "a file shorter than the known offset is read again from its end")

    def test_long_file_returns_a_bounded_tail_on_a_line_boundary(self):
        path = self.task_dir / ("job_" + "b" * 32 + "-0.log")
        line = "x" * 99 + "\n"
        path.write_text(line * (READ_LIMIT // 100 + 50), encoding="utf-8", newline="")
        body = self.get(f"/task/{path.name}").json()
        self.assertTrue(body["truncated"])
        self.assertLessEqual(len(body["text"]), READ_LIMIT)
        self.assertTrue(body["text"].startswith("x" * 99 + "\n"), "the tail begins with a complete line")
        self.assertEqual(body["next"], path.stat().st_size)
        self.assertEqual(body["start"] % 100, 0)

    def test_rejects_names_outside_the_log_directories(self):
        (self.settings.data_dir / "secret.log").write_text("secret", encoding="utf-8")
        (self.runtime_dir / "other.log").write_text("other", encoding="utf-8")
        for path, status in ((r"/task/..\secret.log", 422), ("/task/notes.txt", 422),
                             ("/task/.hidden.log", 422), ("/runtime/other.log", 422),
                             ("/task/missing.log", 404), (f"/runtime/{PREVIOUS_RUNTIME_LOG}", 404),
                             ("/unknown/x.log", 422)):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, status, response.text)
                self.assertNotIn("secret", response.text)
        self.assertIn(self.get("/task/..%2Fsecret.log").status_code, (404, 422))
        self.assertEqual(self.get(f"/runtime/{RUNTIME_LOG}", after=-1).status_code, 422)

    def test_runtime_category_is_absent_without_a_runtime_log(self):
        settings = Settings(Path(self.temp.name) / "plain")
        with TestClient(create_app(settings, token="test-token")) as client:
            body = client.get("/api/v1/logs", headers=self.headers).json()
            self.assertEqual(body, {"files": [], "runtime_available": False})
            self.assertEqual(client.get(f"/api/v1/logs/runtime/{RUNTIME_LOG}", headers=self.headers).status_code, 404)

    def test_clear_empties_the_open_runtime_log_and_keeps_active_task_logs(self):
        live = LiveStore(self.store)
        active = self.store.enqueue("export", {})
        finished = self.store.enqueue("export", {})
        with self.store.connection() as conn:
            conn.execute("UPDATE jobs SET state='succeeded' WHERE id=?", (finished["id"],))
        monitor = live.create_monitor("https://live.douyin.com/logs", "日志")
        session_id = live.start_session(monitor["id"], Path(self.temp.name) / "recording")
        segment = Path(self.temp.name) / "recording" / "segment.ts"
        segment.parent.mkdir(parents=True, exist_ok=True)
        segment.write_bytes(b"ts")
        segment_id = live.upsert_segment(session_id, segment, 1, "completed", 2, 1_000_000)
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO live_segment_product_clips(id,session_id,segment_id,segment_index,period_id,"
                "promotion_id,start,end,path,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("product_clip_" + "c" * 32, session_id, segment_id, 1, "period", "p", 0, 1, "clip.mp4", "processing"))
        runtime = self.runtime_dir / RUNTIME_LOG
        writer = runtime.open("ab")  # The running processes keep the runtime log open.
        self.addCleanup(writer.close)
        writer.write(b"before\n")
        writer.flush()
        (self.runtime_dir / PREVIOUS_RUNTIME_LOG).write_bytes(b"previous\n")
        kept = [self.task_dir / f"{active['id']}-0.log",
                self.task_dir / ("product-clip-product_clip_" + "c" * 32 + "-libx264.log")]
        removed = [self.task_dir / f"{finished['id']}-0.log", self.task_dir / f"{finished['id']}-1.log",
                   self.task_dir / ("job_" + "d" * 32 + "-concat.log")]
        for path in kept + removed[:1] + removed[2:]:
            path.write_bytes(b"ffmpeg\n")
        removed[1].write_bytes(b"")  # Empty leftovers are removed as well.
        (self.task_dir / "keep.txt").write_text("unrelated", encoding="utf-8")
        self.assertEqual(self.client.delete("/api/v1/logs").status_code, 401)
        response = self.client.delete("/api/v1/logs", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"runtime_cleared": True, "deleted_files": 4, "retained_files": 2,
                                           "bytes_reclaimed": 7 + 9 + 7 + 7})
        self.assertEqual(runtime.stat().st_size, 0)
        writer.write(b"after\n")
        writer.flush()
        self.assertEqual(runtime.read_bytes(), b"after\n", "appending continues at the start of the emptied file")
        self.assertFalse((self.runtime_dir / PREVIOUS_RUNTIME_LOG).exists())
        self.assertTrue(all(path.exists() for path in kept), "logs of active jobs and clips being generated stay")
        self.assertFalse(any(path.exists() for path in removed))
        self.assertTrue((self.task_dir / "keep.txt").exists(), "only log files are removed")

    def test_open_location_selects_a_validated_file(self):
        path = self.task_dir / ("job_" + "e" * 32 + "-0.log")
        path.write_text("ffmpeg\n", encoding="utf-8")
        url = "/api/v1/logs/open"
        with patch.object(logs_module.os, "name", "nt"), patch.object(logs_module.subprocess, "Popen") as popen, \
                patch.object(logs_module.os, "startfile", create=True) as startfile:
            opened = self.client.post(url, headers=self.headers, json={"category": "task", "name": path.name})
            self.assertEqual(opened.status_code, 200, opened.text)
            popen.assert_called_once_with(["explorer", "/select,", str(path)])
            folder = self.client.post(url, headers=self.headers, json={"category": "runtime"})
            self.assertEqual(folder.status_code, 200, folder.text)
            startfile.assert_called_once_with(str(self.runtime_dir))
            outside = self.client.post(url, headers=self.headers, json={"category": "task", "name": r"..\secret.log"})
            self.assertEqual(outside.status_code, 422)
            self.assertEqual(popen.call_count, 1)
        with patch.object(logs_module.os, "name", "posix"):
            self.assertEqual(self.client.post(url, headers=self.headers, json={"category": "task"}).status_code, 422)


class ProcessLogTests(unittest.TestCase):
    def test_silent_process_leaves_no_log_and_removes_a_stale_one(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "logs" / "task.log"
            run_process([sys.executable, "-c", "print('output')"], log_path=log)
            self.assertEqual(log.read_text(encoding="utf-8").strip(), "output")
            run_process([sys.executable, "-c", "pass"], log_path=log)
            self.assertFalse(log.exists(), "a rerun without output must not keep the earlier attempt's log")
            with self.assertRaises(MediaError):
                run_process([sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"], log_path=log)
            self.assertEqual(log.read_text(encoding="utf-8").strip(), "boom")


class LoggingFormatTests(unittest.TestCase):
    def test_lines_carry_time_level_and_role_and_application_info_is_kept(self):
        root = logging.getLogger()
        handlers, uvicorn = list(root.handlers), logging.getLogger("uvicorn")
        # Other tests start uvicorn servers, which leaves this logger at their level.
        uvicorn_error = logging.getLogger("uvicorn.error")
        error_level = uvicorn_error.level
        uvicorn_error.setLevel(logging.WARNING)
        state = (uvicorn.handlers, uvicorn.propagate, logging.getLogger("app").level, logging.getLogger("douyincut").level)

        def restore():
            for handler in root.handlers[:]:
                if handler not in handlers:
                    root.removeHandler(handler)
                    handler.close()
            uvicorn.handlers, uvicorn.propagate = state[0], state[1]
            uvicorn_error.setLevel(error_level)
            logging.getLogger("app").setLevel(state[2])
            logging.getLogger("douyincut").setLevel(state[3])
            logs_module._configured = False

        self.addCleanup(restore)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / RUNTIME_LOG
            logs_module._configured = False
            with patch.dict(os.environ, {"DOUYINCUT_LOG_FILE": str(path)}):
                configure_logging("worker")
                configure_logging("api")  # A second call must not duplicate handlers.
            logging.getLogger("douyincut.worker").info("任务开始")
            logging.getLogger("httpx").info("HTTP Request: GET https://example.com/?signature=secret")
            logging.getLogger("uvicorn.error").error("Exception in ASGI application")
            restore()
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertRegex(lines[0], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO +\[worker\] douyincut\.worker: 任务开始$")
        self.assertRegex(lines[1], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ERROR +\[worker\] uvicorn\.error: ")
        self.assertNotIn("signature", "\n".join(lines), "third-party request lines stay out of the log")


if __name__ == "__main__":
    unittest.main()
