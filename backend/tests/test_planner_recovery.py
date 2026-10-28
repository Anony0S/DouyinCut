import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import PropertyMock, patch

from app.ai.providers import CloudConfig
from app.core.config import Settings
from app.core.domain import Asset
from app.core.storage import Store
from app.media.processing import import_asset, probe, run_process
from app.worker import Worker

SELECTION = {"clips": [{"start_ms": 3000, "end_ms": 4500, "reason": "面料"},
                       {"start_ms": 0, "end_ms": 1000, "reason": "上身"}]}
ASR_RESPONSE = {"output": {"sentence": {"begin_time": 0, "end_time": 6000,
                "text": "上身显瘦。只要九十九。面料柔软。", "words": [
                    {"begin_time": 0, "end_time": 1000, "text": "上身显瘦", "punctuation": "。"},
                    {"begin_time": 1000, "end_time": 2000, "text": "只要九十九", "punctuation": "。"},
                    {"begin_time": 3000, "end_time": 4500, "text": "面料柔软", "punctuation": "。"}]}}}


class PlannerHandler(BaseHTTPRequestHandler):
    """Planner endpoint whose next reply is chosen by the test: block, answer, or return an HTTP error."""
    requests = 0
    asr_requests = 0
    arrived = None
    release = None
    mode = "answer"
    content = json.dumps(SELECTION)

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/asr":
            type(self).asr_requests += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(ASR_RESPONSE).encode())
            return
        type(self).requests += 1
        self.arrived.set()
        if self.mode == "block" or (self.mode == "block_first" and type(self).requests == 1):
            self.release.wait(30)
        if self.mode == "drop":
            # Connection closes without a reply, like a gateway timeout or network loss.
            self.close_connection = True
            return
        status = 401 if self.mode == "reject" else 200
        body = (json.dumps({"code": "InvalidApiKey"}) if status == 401 else
                json.dumps({"choices": [{"message": {"content": self.content}}]}))
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        except OSError:
            pass

    def log_message(self, *_args):
        pass


class PlannerRecoveryFixture:
    seed_asr = True

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = Settings(self.root / "data")
        self.settings.prepare()
        self.store = Store(self.settings.database)
        self.asset = self.make_asset()
        self.store.put("asset", self.asset.model_dump())
        self.config = CloudConfig()

        PlannerHandler.requests = PlannerHandler.asr_requests = 0
        PlannerHandler.arrived = threading.Event()
        PlannerHandler.release = threading.Event()
        PlannerHandler.mode = "answer"
        PlannerHandler.content = json.dumps(SELECTION)
        server = ThreadingHTTPServer(("127.0.0.1", 0), PlannerHandler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(PlannerHandler.release.set)

        stack = ExitStack()
        self.addCleanup(stack.close)
        # CloudConfig only accepts HTTPS; the child process receives this plain URL as an argument.
        stack.enter_context(patch.object(CloudConfig, "chat_url", new_callable=PropertyMock,
                                         return_value=f"http://127.0.0.1:{server.server_port}/v1/chat/completions"))
        stack.enter_context(patch.object(CloudConfig, "asr_url", new_callable=PropertyMock,
                                         return_value=f"http://127.0.0.1:{server.server_port}/asr"))
        stack.enter_context(patch("app.ai.providers.read_secret", side_effect=lambda name:
                                  {"llm_api_key": "mock-text-key", "asr_api_key": "mock-asr-key"}.get(name)))
        self.upload = stack.enter_context(patch("app.ai.providers.upload_audio", return_value="https://oss.example/audio"))
        if self.seed_asr:
            # Completed ASR is already cached (keyed by the patched endpoint), so a recovered job must reuse it without uploading.
            endpoint_hash = hashlib.sha256(self.config.asr_url.encode()).hexdigest()
            self.store.cache(f"qwen-audio-v1:{self.asset.sha256}:{endpoint_hash}:{self.config.asr_model}:0:6000000",
                             {"status": "succeeded", "response": ASR_RESPONSE})

    def make_asset(self):
        return Asset(id="asset-planner", sha256="planner-digest", path=str(self.root / "missing.mp4"),
                     name="退出选片", duration_us=6_000_000, width=320, height=240, has_audio=True)

    def enqueue(self):
        return self.store.enqueue("analyze", {"asset_id": self.asset.id, "product_name": "退出选片",
                                              "config": self.config.model_dump()})

    def run_job(self, job_id):
        # Earlier successes queue preview follow-ups; drain them so the target analyze job runs.
        worker = Worker(self.settings, interruptible_cloud=True)
        while self.store.job(job_id)["state"] == "queued" and worker.run_once():
            pass

    def run_worker_in_background(self):
        worker = Worker(self.settings, interruptible_cloud=True)
        thread = threading.Thread(target=worker.run_once, daemon=True)
        thread.start()
        return thread

    def restart(self):
        restarted = Store(self.store.path)
        restarted.start_session()
        return restarted



class PlannerExitRecoveryTests(PlannerRecoveryFixture, unittest.TestCase):
    def test_explicit_exit_stops_waiting_for_planner_and_restart_resends_only_the_text_step(self):
        job = self.enqueue()
        PlannerHandler.mode = "block"
        started = time.monotonic()
        thread = self.run_worker_in_background()
        self.assertTrue(PlannerHandler.arrived.wait(10), "planner request was not sent")
        self.assertTrue(self.store.cached(f"roughcut-transcript:{job['id']}")["ready"])

        self.store.pause_for_shutdown()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "worker kept waiting for the planner response")
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(self.store.job(job["id"])["state"], "paused")

        restarted = self.restart()
        self.assertEqual(restarted.job(job["id"])["state"], "queued")
        PlannerHandler.mode = "answer"
        Worker(self.settings, interruptible_cloud=True).run_once()

        finished = restarted.job(job["id"])
        self.assertEqual(finished["state"], "succeeded", finished.get("error"))
        self.assertEqual(PlannerHandler.requests, 2)
        self.upload.assert_not_called()
        self.assertEqual([c["reason"] for c in finished["result"]["selected_intervals"]], ["面料", "上身"])

    def test_each_exit_record_requeues_once_and_finished_jobs_survive_repeated_restarts(self):
        job = self.enqueue()
        PlannerHandler.mode = "block"
        thread = self.run_worker_in_background()
        self.assertTrue(PlannerHandler.arrived.wait(10))
        local_export = self.store.enqueue("export", {"plan_id": "earlier-plan", "revision": 1, "format": "mp4"})
        self.store.pause_for_shutdown()
        thread.join(5)

        self.restart()
        second = self.restart()
        self.assertEqual(second.job(job["id"])["state"], "queued")
        PlannerHandler.mode = "answer"
        worker = Worker(self.settings, interruptible_cloud=True)
        self.assertTrue(worker.run_once())
        self.assertEqual(second.job(job["id"])["state"], "succeeded")
        previews = [j for j in second.jobs() if j["kind"] == "preview"]
        # The recovered plan's new preview runs normally; work paused by the exit waits for the user.
        self.assertEqual([p["state"] for p in previews], ["queued"])
        self.assertEqual(second.job(local_export["id"])["state"], "paused")

        for _ in range(2):
            self.restart()
        self.assertEqual(self.store.job(job["id"])["state"], "succeeded")
        self.assertEqual(PlannerHandler.requests, 2)
        self.assertEqual(len(self.store.list("plan")), 1)
        self.assertEqual([j["id"] for j in self.store.jobs() if j["kind"] == "preview"], [p["id"] for p in previews])

    def test_invalid_rejected_and_dropped_planner_replies_fail_normally_without_exit_recovery(self):
        cases = [("answer", "not JSON", "剪辑决策时间区间无效"),
                 ("reject", "", "HTTP 401"),
                 ("drop", "", "对端在返回 HTTP 响应前断开连接")]
        for mode, content, message in cases:
            with self.subTest(mode=mode):
                PlannerHandler.requests = 0
                PlannerHandler.mode, PlannerHandler.content = mode, content
                job = self.enqueue()
                self.run_job(job["id"])
                failed = self.store.job(job["id"])
                self.assertEqual(failed["state"], "failed")
                self.assertIn(message, failed["error"])

                self.restart()
                self.assertEqual(self.store.job(job["id"])["state"], "failed")
                PlannerHandler.mode, PlannerHandler.content = "answer", json.dumps(SELECTION)
                self.store.control(job["id"], "retry")
                self.run_job(job["id"])
                self.assertEqual(self.store.job(job["id"])["state"], "succeeded")

    def test_invalid_reply_that_lands_during_exit_still_fails_instead_of_being_resent(self):
        job = self.enqueue()
        PlannerHandler.content = "not JSON"
        original = Store.complete_cloud_operation

        def saved_then_exit(store, operation_id, *args, **kwargs):
            # Timing injection: exit is confirmed after the reply is saved but before it is parsed.
            saved = original(store, operation_id, *args, **kwargs)
            store.pause_for_shutdown()
            return saved

        with patch.object(Store, "complete_cloud_operation", saved_then_exit):
            Worker(self.settings, interruptible_cloud=True).run_once()
        self.assertEqual(self.store.job(job["id"])["state"], "failed")
        self.assertIn("剪辑决策时间区间无效", self.store.job(job["id"])["error"])

        restarted = self.restart()
        self.assertEqual(restarted.job(job["id"])["state"], "failed")
        self.assertEqual(PlannerHandler.requests, 1)

    def test_late_reply_from_an_executor_that_lost_its_lease_cannot_replace_the_new_decision(self):
        job = self.enqueue()
        PlannerHandler.mode = "block_first"
        stale = Worker(self.settings, interruptible_cloud=True)
        claim = stale.store.claim
        stale.store.claim = lambda owner: claim(owner, lease_seconds=1)
        thread = threading.Thread(target=stale.run_once, daemon=True)
        thread.start()
        self.assertTrue(PlannerHandler.arrived.wait(10))
        time.sleep(1.5)

        Worker(self.settings, interruptible_cloud=True).run_once()  # Reclaims the expired lease.
        fresh = self.store.job(job["id"])
        self.assertEqual(fresh["state"], "succeeded", fresh.get("error"))
        PlannerHandler.content = json.dumps({"clips": [{"start_ms": 0, "end_ms": 1000, "reason": "迟到"}]})
        PlannerHandler.release.set()
        thread.join(10)

        self.assertEqual(self.store.cached(f"roughcut-timestamps-v1:{job['id']}"), SELECTION)
        self.assertEqual(self.store.job(job["id"])["result"], fresh["result"])
        self.assertEqual(len(self.store.list("plan")), 1)

    def test_response_saved_before_exit_is_reused_without_a_new_request(self):
        job = self.enqueue()
        original = Store.complete_cloud_operation

        def saved_then_exit(store, operation_id, *args, **kwargs):
            # Timing injection: the user confirms exit right after the planner reply reaches disk.
            saved = original(store, operation_id, *args, **kwargs)
            store.pause_for_shutdown()
            return saved

        with patch.object(Store, "complete_cloud_operation", saved_then_exit):
            Worker(self.settings, interruptible_cloud=True).run_once()
        self.assertEqual(self.store.job(job["id"])["state"], "paused")
        self.assertEqual(PlannerHandler.requests, 1)

        restarted = self.restart()
        self.assertEqual(restarted.job(job["id"])["state"], "queued")
        Worker(self.settings, interruptible_cloud=True).run_once()
        self.assertEqual(restarted.job(job["id"])["state"], "succeeded")
        self.assertEqual(PlannerHandler.requests, 1)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg 与 ffprobe")
class PlannerExitRecoveryMediaTests(PlannerRecoveryFixture, unittest.TestCase):
    # Real ASR runs in the same attempt as the interrupted planner request.
    seed_asr = False

    def make_asset(self):
        source = self.root / "退出选片.mp4"
        run_process([self.settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=160x120:r=25:d=6",
                     "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
                     "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)])
        return import_asset(source, self.settings)

    def test_invalid_reply_during_exit_fails_even_after_asr_succeeded_in_the_same_attempt(self):
        job = self.enqueue()
        PlannerHandler.content = "not JSON"
        original = Store.complete_cloud_operation

        def saved_then_exit(store, operation_id, *args, **kwargs):
            saved = original(store, operation_id, *args, **kwargs)
            if store.job(job["id"])["progress"] >= 0.7:  # Only once the planner reply is saved.
                store.pause_for_shutdown()
            return saved

        with patch.object(Store, "complete_cloud_operation", saved_then_exit):
            self.run_job(job["id"])
        self.assertEqual(self.store.job(job["id"])["state"], "failed")
        restarted = self.restart()
        self.assertEqual(restarted.job(job["id"])["state"], "failed")
        self.assertEqual((PlannerHandler.asr_requests, PlannerHandler.requests), (1, 1))

    def test_recovered_plan_keeps_clip_reasons_and_renders_the_stitched_preview(self):
        job = self.enqueue()
        PlannerHandler.mode = "block"
        thread = self.run_worker_in_background()
        self.assertTrue(PlannerHandler.arrived.wait(10))
        self.store.pause_for_shutdown()
        thread.join(5)
        self.restart()
        PlannerHandler.mode = "answer"
        self.run_job(job["id"])
        self.assertEqual((PlannerHandler.asr_requests, PlannerHandler.requests), (1, 2))
        self.assertEqual(self.upload.call_count, 1)

        result = self.store.job(job["id"])["result"]
        plan = self.store.get("plan", result["plan_id"])
        reasons = [self.store.get("candidate", clip["candidate_id"])["reason"] for clip in plan["clips"]]
        self.assertEqual(reasons, ["面料", "上身"])
        preview = next(j for j in self.store.jobs() if j["kind"] == "preview")
        self.assertEqual(preview["state"], "queued")
        Worker(self.settings, interruptible_cloud=True).run_once()
        self.assertEqual(self.store.job(preview["id"])["state"], "succeeded", self.store.job(preview["id"]).get("error"))
        artifact = self.store.list("export")[0]
        self.assertLess(abs(probe(Path(artifact["video_path"]), self.settings)["duration_us"] - 2_500_000), 150_000)
        subtitles = (Path(artifact["path"]) / "subtitles.srt").read_text(encoding="utf-8-sig")
        self.assertIn("00:00:00,000 --> 00:00:01,500\n面料柔软。", subtitles)
        self.assertNotIn("九十九", subtitles)


if __name__ == "__main__":
    unittest.main()
