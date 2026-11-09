import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.core.domain import Asset, Candidate, Product, Transcript
from app.media.editing import build_plan
from app.ai.providers import (ASR_MIN_CHUNK_US, CloudConfig, ProviderError, ProviderHTTPError,
                           interruptible_network_call, audio_segments, audio_text, cloud_json,
                           qwen_audio_payload, transcribe)
from app.core.storage import Store
from app.worker import Paused, Worker


class BlockingHandler(BaseHTTPRequestHandler):
    started = None
    release = None

    def do_POST(self):
        self.started.set()
        self.release.wait(10)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"output":{"text":"late"}}')
        except OSError:
            pass

    def log_message(self, *_args):
        pass


def response(text):
    return {"output": {"choices": [{"message": {"content": [{"text": text}]}}]}}


def sentence_response():
    # Exact user-provided response, including duplicate root/output and word timestamps.
    return json.loads((Path(__file__).parent / "fixtures/qwen_audio_sentence.json").read_text(encoding="utf-8"))


class QwenAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.settings = Settings(self.folder)
        self.settings.prepare()
        self.store = Store(self.settings.database)
        self.asset = Asset(id="asset-test", sha256="digest", path="source.mp4", name="source",
                           duration_us=10_000_000, width=320, height=240, has_audio=True)
        self.config = CloudConfig(base_url="https://text.example/v1",
                                  planner_model="test-text-model", asr_model="qwen-audio-3.1-asr-flash",
                                  asr_base_url="https://llm-4xet6gf32m32iuj1.cn-beijing.maas.aliyuncs.com",
                                  asr_timeout_seconds=420)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Short segments keep recovery/boundary fixtures small; production 120s is tested separately.
        self.stack.enter_context(patch("app.ai.providers.ASR_CHUNK_US", 10_000_000))
        self.stack.enter_context(patch("app.ai.providers.read_secret", side_effect=lambda name:
                                      {"llm_api_key": "mock-text-key", "asr_api_key": "mock-asr-key"}.get(name)))
        self.extract = self.stack.enter_context(patch("app.ai.providers.extract_audio"))
        self.upload = self.stack.enter_context(patch("app.ai.providers.upload_audio", return_value="https://oss.example/audio"))
        self.network = self.stack.enter_context(patch("app.ai.providers.cloud_json", return_value=response("这件衣服")))

    def cache_key(self, start=0, end=10_000_000):
        endpoint_hash = hashlib.sha256(self.config.asr_url.encode()).hexdigest()
        return f"qwen-audio-v1:digest:{endpoint_hash}:{self.config.asr_model}:{start}:{end}"

    def transcribe(self, checkpoint=lambda: None, progress=None):
        return transcribe(self.asset, self.config, self.settings, self.store, self.folder, checkpoint, progress)

    def test_exact_native_endpoint_payload_and_independent_key_and_timeout(self):
        rows = self.transcribe()
        endpoint = "https://llm-4xet6gf32m32iuj1.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        self.assertEqual(self.config.asr_url, endpoint)
        self.network.assert_called_once_with("POST", endpoint, {
            "model": "qwen-audio-3.1-asr-flash",
            "input": {"messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": "https://oss.example/audio"}}
            ]}]}, "parameters": {"format": "wav", "sample_rate": "16000"}
        }, "mock-asr-key", sse=False, operation="asr", timeout_seconds=420)
        self.assertEqual(rows[0].timing, "audio_chunk")
        self.assertEqual(rows[0].text, "这件衣服")

    def test_qwen_payload_matches_bailian_native_example(self):
        self.assertEqual(qwen_audio_payload("qwen-audio-3.1-asr-flash", "https://oss.example/audio.wav"), {
            "model": "qwen-audio-3.1-asr-flash",
            "input": {"messages": [{"role": "user", "content": [{
                "type": "input_audio", "input_audio": {"data": "https://oss.example/audio.wav"}
            }]}]},
            "parameters": {"format": "wav", "sample_rate": "16000"},
        })

    def test_chunk_source_times_tail_and_progress(self):
        self.asset = self.asset.model_copy(update={"duration_us": 25_000_000})
        progress = []
        rows = self.transcribe(progress=progress.append)
        bounds = [(0, 10_000_000), (10_000_000, 20_000_000), (20_000_000, 25_000_000)]
        self.assertEqual([(t.start_us, t.end_us) for t in rows], bounds)
        self.assertEqual([c.kwargs for c in self.extract.call_args_list],
                         [{"start_us": start, "end_us": end} for start, end in bounds])
        self.assertEqual([c.args[2] for c in self.upload.call_args_list],
                         [f"digest-qwen-audio-v1-{start}-{end}" for start, end in bounds])
        self.assertEqual(progress, [1/3, 2/3, 1])
        self.assertEqual(self.transcribe(), rows)
        self.assertEqual(self.network.call_count, 3)
        self.assertEqual(self.upload.call_count, 3)

    def test_subsecond_probe_tail_is_skipped_without_an_asr_request(self):
        self.asset = self.asset.model_copy(update={"duration_us": 10_000_000 + ASR_MIN_CHUNK_US // 1000})
        progress = []
        rows = self.transcribe(progress=progress.append)
        self.assertEqual([(t.start_us, t.end_us) for t in rows], [(0, 10_000_000)])
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(self.upload.call_count, 1)
        self.assertEqual(progress[-1], 1)

    def test_finished_chunks_survive_rejection_and_retry(self):
        self.asset = self.asset.model_copy(update={"duration_us": 20_000_000})
        self.network.side_effect = [response("第一段"), ProviderHTTPError(401)]
        with self.assertRaisesRegex(ProviderError, "语音识别模型卡片中的 API Key"):
            self.transcribe()
        self.network.side_effect = [response("第二段")]
        self.assertEqual([t.text for t in self.transcribe()], ["第一段", "第二段"])
        self.assertEqual(self.network.call_count, 3)

    def test_rejected_requests_can_retry(self):
        for status in (400, 401, 403, 404, 405, 413, 415, 422, 429):
            with self.subTest(status=status):
                self.store.cache(self.cache_key(), {"status": "rejected"})
                self.network.side_effect = ProviderHTTPError(status)
                with self.assertRaisesRegex(ProviderError, f"HTTP {status}"):
                    self.transcribe()
                self.assertEqual(self.store.cached(self.cache_key()), {"status": "rejected", "http_status": status})
                self.network.side_effect = None
                self.assertEqual(self.transcribe()[0].text, "这件衣服")

    def test_timeout_and_server_errors_block_resubmission(self):
        for failure in (ProviderError("timeout"), ProviderHTTPError(408), ProviderHTTPError(500)):
            with self.subTest(failure=str(failure)):
                self.store.cache(self.cache_key(), {"status": "rejected"})
                self.network.reset_mock()
                self.network.side_effect = failure
                with self.assertRaises(ProviderError):
                    self.transcribe()
                self.assertEqual(self.store.cached(self.cache_key())["status"], "submitting")
                with self.assertRaisesRegex(ProviderError, "请求结果不确定"):
                    self.transcribe()
                self.assertEqual(self.network.call_count, 1)

    def test_pause_before_post_does_not_mark_submitting(self):
        checkpoints = 0
        def checkpoint():
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                raise Paused()
        with self.assertRaises(Paused):
            self.transcribe(checkpoint)
        self.assertIsNone(self.store.cached(self.cache_key()))
        self.network.assert_not_called()
        self.assertEqual(self.transcribe()[0].text, "这件衣服")

    def test_pause_after_response_preserves_completed_chunk(self):
        self.asset = self.asset.model_copy(update={"duration_us": 20_000_000})
        def progress(_):
            raise Paused()
        with self.assertRaises(Paused):
            self.transcribe(progress=progress)
        self.assertEqual(self.store.cached(self.cache_key())["status"], "succeeded")
        self.assertEqual(len(self.transcribe()), 2)
        self.assertEqual(self.network.call_count, 2)

    def test_explicit_exit_terminates_blocked_asr_request_and_requeues_once(self):
        BlockingHandler.started = threading.Event()
        BlockingHandler.release = threading.Event()
        server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingHandler)
        server.daemon_threads = True
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: BlockingHandler.release.set())
        self.addCleanup(server.shutdown)

        task = self.store.enqueue("analyze", {"asset_id": self.asset.id, "config": self.config.model_dump()})
        self.store.claim("worker")
        cache_key = self.cache_key()
        operation_id = self.store.begin_cloud_operation(task["id"], "asr", self.config.model_dump(), cache_key)

        def checkpoint():
            if self.store.is_shutting_down():
                raise Paused()

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(interruptible_network_call, "asr",
                                      ("POST", f"http://127.0.0.1:{server.server_port}/blocked", {}, "mock-key"),
                                      store=self.store, operation_id=operation_id, cache_key=cache_key,
                                      checkpoint=checkpoint)
            self.assertTrue(BlockingHandler.started.wait(5), "test server did not receive request")
            self.store.pause_for_shutdown()
            with self.assertRaises(Paused):
                request.result(timeout=3)

        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(self.store.interruptible_operation(operation_id))
        restarted = Store(self.store.path)
        restarted.start_session()
        self.assertEqual(restarted.job(task["id"])["state"], "queued")
        self.assertIsNone(restarted.cached(cache_key))
        self.assertEqual(restarted.recover_exit_interrupted_operations(), 0)

    def test_empty_audio_text_is_cached_silence(self):
        self.network.return_value = response("")
        self.assertEqual(self.transcribe(), [])
        self.assertEqual(self.transcribe(), [])
        self.network.assert_called_once()

    def test_model_and_provider_changes_invalidate_cache_and_old_cache_is_ignored(self):
        self.store.cache("asr-v1:digest:paraformer-v2:cn", {"status": "submitting"})
        self.transcribe()
        self.config = self.config.model_copy(update={"asr_model": "custom-qwen-audio-v1"})
        self.transcribe()
        self.config = self.config.model_copy(update={"asr_base_url": "https://dashscope.aliyuncs.com"})
        self.transcribe()
        self.assertEqual(self.network.call_count, 3)
        self.assertEqual(self.network.call_args.args[2]["model"], "custom-qwen-audio-v1")
        self.assertEqual(self.store.cached("asr-v1:digest:paraformer-v2:cn"), {"status": "submitting"})

    def test_text_endpoint_changes_preserve_speech_cache_and_uncertain_guard(self):
        self.transcribe()
        self.config = self.config.model_copy(update={"base_url": "https://another-text.example/v1"})
        self.transcribe()
        self.network.assert_called_once()
        self.store.cache(self.cache_key(), {"status": "submitting"})
        self.config = self.config.model_copy(update={"base_url": "https://third-text.example/v1"})
        with self.assertRaisesRegex(ProviderError, "请求结果不确定"):
            self.transcribe()
        self.network.assert_called_once()

    def test_missing_speech_key_never_falls_back_to_text_key(self):
        with patch("app.ai.providers.read_secret", side_effect=lambda name:
                   {"llm_api_key": "text-only-key"}.get(name)) as secret:
            with self.assertRaisesRegex(ProviderError, "语音识别模型卡片"):
                self.transcribe()
        secret.assert_called_once_with("asr_api_key")
        self.upload.assert_not_called()
        self.network.assert_not_called()

    def test_malformed_response_is_not_cached_as_success(self):
        for data in ({}, {"text": 123}, {"output": {"choices": []}}, response("x"*5001)):
            self.store.cache(self.cache_key(), {"status": "rejected"})
            self.network.return_value = data
            with self.assertRaises(ProviderError):
                self.transcribe()
            self.assertEqual(self.store.cached(self.cache_key())["status"], "invalid_response")
            self.assertIsNotNone(self.store.cached(self.cache_key())["error_detail"]["response_body"])
        self.network.return_value = response("修正后的文本")
        self.assertEqual(self.transcribe()[0].text, "修正后的文本")

    def test_user_response_has_one_sentence_with_ms_to_source_us_mapping(self):
        sample = sentence_response()
        text = "hello world，这里是阿里巴巴语音实验室。"
        self.assertEqual(audio_text(sample), text)
        rows = audio_segments(sample, self.asset.id, 20_000_000, 24_000_000)
        self.assertEqual(len(rows), 1, "Root and output must not duplicate sentences or words")
        self.assertEqual((rows[0].start_us, rows[0].end_us, rows[0].text, rows[0].timing),
                         (20_600_000, 23_834_000, text, "sentence"))
        for payload in (sample["output"], {"output": sample["output"]}, {"sentence": sample["sentence"]}):
            self.assertEqual(audio_segments(payload, self.asset.id, 20_000_000, 24_000_000), rows)

    def test_sentence_arrays_are_sorted_and_deduplicated(self):
        first = {"begin_time": 600, "end_time": 1500, "text": "第一句", "sentence_end": True}
        second = {"begin_time": 2000, "end_time": 3834, "text": "第二句", "sentence_end": True}
        for field in ("sentence", "sentences"):
            rows = audio_segments({"output": {field: [second, first, first]}}, self.asset.id, 0, 4_000_000)
            self.assertEqual([(t.start_us, t.end_us, t.text) for t in rows],
                             [(600_000, 1_500_000, "第一句"), (2_000_000, 3_834_000, "第二句")])

    def test_native_text_without_timestamps_remains_explicitly_coarse(self):
        for payload in ({"text": "只有文本"}, {"output": {"text": "只有文本"}},
                        {"sentence": {"text": "只有文本"}}):
            rows = audio_segments(payload, self.asset.id, 10_000_000, 20_000_000)
            self.assertEqual((rows[0].start_us, rows[0].end_us, rows[0].timing),
                             (10_000_000, 20_000_000, "audio_chunk"))
        self.assertEqual(audio_segments({"output": {"text": ""}}, self.asset.id, 0, 4_000_000), [])

    def test_invalid_or_partial_sentence_timestamps_are_not_silently_coarsened(self):
        for begin, end in ((-1, 1000), (1000, 1000), (2000, 1000), (0, 4001),
                           (True, 1000), (0.5, 1000), ("600", 1000), (600, None)):
            with self.subTest(begin=begin, end=end):
                self.network.return_value = {"output": {"sentence": {
                    "text": "文本", "begin_time": begin, "end_time": end}, "text": "文本"}}
                self.store.cache(self.cache_key(), {"status": "rejected"})
                self.asset = self.asset.model_copy(update={"duration_us": 4_000_000})
                with self.assertRaisesRegex(ProviderError, "句级时间戳"):
                    self.transcribe()
                self.assertEqual(self.store.cached(self.cache_key(end=4_000_000))["status"], "invalid_response")

    def test_nonfinal_or_invalid_sentences_are_not_treated_as_complete_text(self):
        for sentence in ({"text": "未完成", "sentence_end": False}, {"text": 123}, "invalid"):
            with self.assertRaises(ProviderError):
                audio_segments({"output": {"sentence": sentence, "text": "完整文本"}}, self.asset.id, 0, 4_000_000)

    def test_precise_timestamps_survive_chunk_cache_and_same_content_new_asset(self):
        self.asset = self.asset.model_copy(update={"duration_us": 24_000_000})
        self.network.return_value = sentence_response()
        rows = self.transcribe()
        self.assertEqual([(t.start_us, t.end_us) for t in rows],
                         [(600_000, 3_834_000), (10_600_000, 13_834_000), (20_600_000, 23_834_000)])
        self.assertEqual(self.transcribe(), rows)
        self.asset = self.asset.model_copy(update={"id": "asset-same-content"})
        self.assertTrue(all(t.source_asset_id == self.asset.id for t in self.transcribe()))
        self.assertEqual(self.network.call_count, 3)


    def test_existing_format_failure_can_retry_with_user_sentence_response(self):
        self.store.cache(self.cache_key(), {"status": "invalid_response"})
        self.network.return_value = sentence_response()
        self.assertEqual(self.transcribe()[0].timing, "sentence")
        self.assertEqual(self.transcribe()[0].start_us, 600_000)
        self.network.assert_called_once()

    def test_sentence_subtitle_survives_clip_shorter_than_audio_chunk_without_coarse_warning(self):
        product = Product(name="测试款", confirmed=True)
        candidate = Candidate(product_id=product.id, source_asset_id=self.asset.id, start_us=500_000, end_us=4_000_000)
        transcripts = audio_segments(sentence_response(), self.asset.id, 0, self.asset.duration_us)
        plan = build_plan(product, [candidate], {self.asset.id: self.asset}, transcripts, target_us=3_500_000)
        self.assertEqual((plan.subtitles[0].start_us, plan.subtitles[0].end_us), (100_000, 3_334_000))
        self.assertFalse(any("粗略定位" in warning for warning in plan.warnings))

    def test_unconfigured_and_custom_model_ids_are_preserved(self):
        self.assertEqual(CloudConfig().asr_model, "")
        self.assertEqual(CloudConfig(asr_model=" custom-qwen-audio-v1 ").asr_model, "custom-qwen-audio-v1")


    def test_native_and_compatible_text_response_parsing(self):
        self.assertEqual(audio_text(response("  中文  ")), "中文")
        self.assertEqual(audio_text({"output": {"choices": [{"message": {"content": "文本"}}]}}), "文本")
        self.assertEqual(audio_text({"choices": [{"message": {"content": [{"text": "一"}, {"text": "二"}]}}]}), "一\n二")
        for invalid in (None, [], {"choices": [{"message": {"content": []}}]},
                        {"choices": [{"message": {"content": [{"type": "audio"}]}}]}):
            with self.assertRaises(ProviderError):
                audio_text(invalid)

    def test_sync_transport_headers_and_no_secret_leaks(self):
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(response("文本")).encode())
            self.assertEqual(audio_text(cloud_json("POST", self.config.asr_url, {}, "mock-key", sse=False)), "文本")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual({k.lower(): v for k, v in request.header_items()}, {
                "content-type": "application/json", "authorization": "Bearer mock-key", "x-dashscope-sse": "disable"})
            opener.return_value.open.side_effect = urllib.error.HTTPError(
                "https://secret.example/?token=private", 401, "private-key", {}, None)
            with self.assertRaises(ProviderHTTPError) as raised:
                cloud_json("POST", self.config.asr_url, {}, "mock-key", sse=False)
            self.assertEqual(raised.exception.status_code, 401)
            self.assertNotIn("private", str(raised.exception))
            self.assertNotIn("https://", str(raised.exception))

    def test_plan_warns_for_coarse_timing_and_omits_cropped_text(self):
        product = Product(name="测试款", confirmed=True)
        candidate = Candidate(product_id=product.id, source_asset_id=self.asset.id, start_us=0, end_us=9_000_000)
        transcript = Transcript(source_asset_id=self.asset.id, start_us=0, end_us=10_000_000,
                                text="整个音频段", timing="audio_chunk")
        plan = build_plan(product, [candidate], {self.asset.id: self.asset}, [transcript], target_us=9_000_000)
        self.assertEqual(plan.subtitles, [])
        self.assertTrue(any("粗略定位" in warning for warning in plan.warnings))
        candidate.end_us = 10_000_000
        plan = build_plan(product, [candidate], {self.asset.id: self.asset}, [transcript], target_us=10_000_000)
        self.assertEqual(plan.subtitles[0].text, "整个音频段")

    def test_completed_analysis_atomically_replaces_old_transcripts_and_silence_clears_them(self):
        self.store.put("transcript", {"id": "old", "segment": {"source_asset_id": self.asset.id, "text": "旧转写"}})
        self.store.put("transcript", {"id": "other", "segment": {"source_asset_id": "other-asset", "text": "其他素材"}})
        new_row = {"id": "new", "segment": self.transcribe()[0].model_dump()}
        self.store.commit_result("job-result:new", {}, [("transcript", new_row)], replace_transcripts_for=self.asset.id)
        self.assertEqual({t["id"] for t in self.store.list("transcript")}, {"new", "other"})
        self.store.commit_result("job-result:new", {}, [], replace_transcripts_for=self.asset.id)
        self.assertEqual({t["id"] for t in self.store.list("transcript")}, {"new", "other"})
        self.store.commit_result("job-result:silent", {}, [], replace_transcripts_for=self.asset.id)
        self.assertEqual({t["id"] for t in self.store.list("transcript")}, {"other"})


if __name__ == "__main__":
    unittest.main()
