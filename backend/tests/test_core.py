import concurrent.futures
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import Settings
from app.core.domain import Asset, Candidate, Canvas, Clip, Plan, Product, Subtitle, Transcript
from app.media.editing import build_plan, make_batches, remap_subtitles, srt_text, validate_plan
from app.core.storage import Store


def asset(identifier="a", duration=2_000_000_000):
    return Asset(id=identifier, path="source.mp4", name="source", sha256=identifier,
                 duration_us=duration, width=320, height=240, has_audio=True)


class EditingTests(unittest.TestCase):
    def test_batches_split_crossing_file_and_tail_without_duplication(self):
        batches = make_batches([asset("a", 1_700_000_000), asset("b", 250_000_000)], flush=True)
        self.assertEqual([b.duration_us for b in batches], [1_800_000_000, 150_000_000])
        self.assertEqual([(s.source_asset_id,s.start_us,s.end_us) for b in batches for s in b.spans],
                         [("a",0,1_700_000_000),("b",0,100_000_000),("b",100_000_000,250_000_000)])
        self.assertTrue(batches[1].partial)
        self.assertEqual(len(make_batches([asset("a", 1000)], flush=False)), 0)

    def test_invalid_and_non_integer_bounds_rejected(self):
        for start,end in [(1,1),(-1,5),(3,2),(0.5,5)]:
            with self.assertRaises(ValidationError):
                Candidate(product_id="p",source_asset_id="a",start_us=start,end_us=end)

    def test_manual_selection_rejects_other_product_and_unknown_id(self):
        product=Product(id="p",name="外套",confirmed=True)
        candidate=Candidate(id="c",product_id="other",source_asset_id="a",start_us=0,end_us=100)
        with self.assertRaises(ValueError):
            build_plan(product,[candidate],{"a":asset()},[],ordered_ids=["c"])

    def test_unconfirmed_product_cannot_generate(self):
        with self.assertRaises(ValueError):
            build_plan(Product(id="p",name="外套"),[],{},[])

    def test_overlapping_source_segments_rejected(self):
        p=Product(id="p",name="外套",confirmed=True)
        candidates=[Candidate(id="c1",product_id="p",source_asset_id="a",start_us=0,end_us=10),
                    Candidate(id="c2",product_id="p",source_asset_id="a",start_us=5,end_us=15)]
        with self.assertRaises(ValueError):
            build_plan(p,candidates,{"a":asset()},[],ordered_ids=["c1","c2"])

    def test_excluded_price_and_short_material_not_padded(self):
        p=Product(id="p",name="外套",confirmed=True)
        candidates=[Candidate(id="c1",product_id="p",source_asset_id="a",start_us=0,end_us=10_000_000,tags=["front"]),
                    Candidate(id="c2",product_id="p",source_asset_id="a",start_us=20_000_000,end_us=40_000_000,tags=["price"])]
        plan=build_plan(p,candidates,{"a":asset()},[],exclude_tags=["price"])
        self.assertEqual(plan.duration_us,10_000_000)
        self.assertEqual(len(plan.clips),1)
        self.assertTrue(plan.warnings)

    def test_subtitles_reorder_and_removed_sentence_does_not_survive(self):
        clips=[Clip(source_asset_id="a",source_start_us=20_000_000,source_end_us=30_000_000,timeline_start_us=0,candidate_id="c2"),
               Clip(source_asset_id="a",source_start_us=0,source_end_us=10_000_000,timeline_start_us=10_000_000,candidate_id="c1")]
        transcripts=[Transcript(source_asset_id="a",start_us=1_000_000,end_us=3_000_000,text="第一句"),
                     Transcript(source_asset_id="a",start_us=12_000_000,end_us=15_000_000,text="被删除"),
                     Transcript(source_asset_id="a",start_us=21_000_000,end_us=24_000_000,text="第二句"),
                     Transcript(source_asset_id="a",start_us=29_000_000,end_us=32_000_000,text="被切断的整句")]
        subtitles=remap_subtitles(clips,transcripts)
        self.assertEqual([(s.text,s.start_us,s.end_us) for s in subtitles],
                         [("第二句",1_000_000,4_000_000),("第一句",11_000_000,13_000_000)])
        self.assertNotIn("被删除",srt_text(subtitles))
        self.assertIn("00:00:11,000",srt_text(subtitles))

    def test_timeline_gap_and_subtitle_overrun_rejected(self):
        kwargs=dict(product_id="p",canvas=Canvas(width=320,height=240))
        with self.assertRaises(ValidationError):
            Plan(**kwargs,clips=[Clip(source_asset_id="a",source_start_us=0,source_end_us=10,timeline_start_us=1,candidate_id="c")])
        with self.assertRaises(ValidationError):
            Plan(**kwargs,clips=[Clip(source_asset_id="a",source_start_us=0,source_end_us=10,timeline_start_us=0,candidate_id="c")],subtitles=[Subtitle(start_us=0,end_us=11,text="越界")])

    def test_trim_cannot_escape_product_candidate(self):
        p=Product(id="p",name="外套",confirmed=True)
        candidate=Candidate(id="c",product_id="p",source_asset_id="a",start_us=10,end_us=20)
        plan=Plan(product_id="p",canvas=Canvas(width=320,height=240),clips=[Clip(source_asset_id="a",source_start_us=0,source_end_us=20,timeline_start_us=0,candidate_id="c")])
        with self.assertRaises(ValueError):
            validate_plan(plan,p,{"c":candidate},{"a":asset()})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"db.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def failed_analysis(self, config):
        task = self.store.enqueue('analyze', {'asset_id': 'a', 'config': config, 'refresh_asr': True})
        self.store.claim('worker')
        self.store.finish(task['id'], 'worker', 'failed', error='cloud failed')
        return task['id']

    def test_retry_updates_model_and_invalidates_only_task_selection_caches(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig(asr_model='custom-speech').model_dump()
        job_id = self.failed_analysis(config)
        keys = [f'roughcut-timestamps-v1:{job_id}', f'roughcut-transcript:{job_id}']
        for key in keys:
            self.store.cache(key, {'saved': True})
        self.store.cache('asr-uncertain-marker', {'status': 'submitting'})
        self.store.cache('asr-paid-response', {'status': 'succeeded', 'response': {'text': '口播'}})
        current = {**config, 'asr_model': 'qwen-audio-3.1-asr-flash'}
        retried = self.store.control(job_id, 'retry', retry_config=current)
        self.assertEqual(retried['payload']['config'], current)
        self.assertTrue(retried['payload']['refresh_asr'])
        self.assertTrue(all(self.store.cached(key) is None for key in keys))
        self.assertEqual(self.store.cached('asr-uncertain-marker'), {'status': 'submitting'})
        self.assertEqual(self.store.cached('asr-paid-response')['status'], 'succeeded')
        self.assertIsNone(retried['error'])

    def test_speech_endpoint_retry_clears_task_caches_but_preserves_paid_speech(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig(base_url='https://text.example/v1', asr_base_url='https://speech.example').model_dump()
        job_id = self.failed_analysis(config)
        task_key = f'roughcut-transcript:{job_id}'
        self.store.cache(task_key, {'segments': []})
        endpoint_hash = hashlib.sha256(CloudConfig.model_validate(config).asr_url.encode()).hexdigest()
        paid_key = f"qwen-audio-v1:a:{endpoint_hash}:{config['asr_model']}:0:120000000"
        self.store.cache(paid_key, {'status': 'succeeded', 'response': {'text': 'paid'}})
        current = {**config, 'asr_base_url': 'https://another-speech.example'}
        queued = self.store.control(job_id, 'retry', retry_config=current)
        self.assertIsNone(self.store.cached(task_key))
        self.assertEqual(self.store.cached(paid_key)['status'], 'succeeded')
        self.assertEqual(queued['payload']['config'], current)

    def test_retry_timeout_change_reuses_completed_decision(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig().model_dump()
        job_id = self.failed_analysis(config)
        key = f'roughcut-timestamps-v1:{job_id}'
        self.store.cache(key, {'clips': [{'start_ms': 0, 'end_ms': 4000, 'reason': '面料'}]})
        self.store.control(job_id, 'retry', retry_config={**config, 'chat_timeout_seconds': 600})
        self.assertIsNotNone(self.store.cached(key))

    def test_acknowledge_asr_uncertain_releases_only_matching_submitting_markers(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig(base_url='https://text.example/v1',
                             asr_base_url='https://speech.example/gateway').model_dump()
        self.store.put('asset', asset().model_dump())
        self.store.put('asset', asset('other').model_dump())
        job_id = self.failed_analysis(config)
        asr_url = CloudConfig.model_validate(config).asr_url
        prefix = f"qwen-audio-v1:a:{hashlib.sha256(asr_url.encode()).hexdigest()}:{config['asr_model']}:"
        self.store.cache(prefix + '0:120000000', {'status': 'submitting'})
        self.store.cache(prefix + '120000000:240000000', {'status': 'succeeded', 'response': {'text': 'paid'}})
        other_prefix = prefix.replace(':a:', ':other:')
        self.store.cache(other_prefix + '0:120000000', {'status': 'submitting'})

        queued = self.store.control(job_id, 'acknowledge_asr_uncertain', retry_config=config)
        self.assertEqual(queued['state'], 'queued')
        self.assertIsNone(self.store.cached(prefix + '0:120000000'))
        self.assertIsNotNone(self.store.cached(prefix + '120000000:240000000'))
        self.assertIsNotNone(self.store.cached(other_prefix + '0:120000000'))

    def test_changed_model_retry_cannot_overwrite_published_result(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig().model_dump()
        job_id = self.failed_analysis(config)
        self.store.cache(f'job-result:{job_id}', {'plan_id': 'already-published'})
        with self.assertRaisesRegex(ValueError, '已发布粗剪结果'):
            self.store.control(job_id, 'retry', retry_config={**config, 'asr_model': 'another-model'})
        self.assertEqual(self.store.job(job_id)['state'], 'failed')
        self.assertEqual(self.store.job(job_id)['payload']['config'], config)

    def test_retry_cannot_run_two_analyses_for_same_asset(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig().model_dump()
        job_id = self.failed_analysis(config)
        self.store.enqueue('analyze', {'asset_id': 'a', 'config': config})
        with self.assertRaisesRegex(ValueError, '已有活动分析任务'):
            self.store.control(job_id, 'retry', retry_config=config)
        self.assertEqual(self.store.job(job_id)['state'], 'failed')

    def test_concurrent_claim_has_single_owner(self):
        job=self.store.enqueue("import",{"path":"a"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            claimed=list(executor.map(lambda i:self.store.claim(f"worker{i}"),range(4)))
        self.assertEqual(sum(x is not None for x in claimed),1)
        self.assertEqual(self.store.job(job["id"])["attempts"],1)

    def test_expired_lease_recovery_prevents_stale_finish(self):
        job=self.store.enqueue("import",{"path":"a"})
        self.store.claim("old",lease_seconds=-1)
        reclaimed=self.store.claim("new")
        self.assertEqual(reclaimed["id"],job["id"])
        with self.assertRaises(RuntimeError):
            self.store.finish(job["id"],"old","succeeded")
        self.store.finish(job["id"],"new","succeeded",result={"ok":True})
        self.assertEqual(self.store.job(job["id"])["state"],"succeeded")

    def test_pause_cancel_retry_and_restart(self):
        job=self.store.enqueue("export",{})
        self.store.control(job["id"],"pause")
        self.assertIsNone(self.store.claim("worker"))
        self.store.control(job["id"],"resume")
        self.store.claim("worker")
        self.store.finish(job["id"],"worker","failed",error="expected")
        restarted=Store(self.store.path)
        restarted.control(job["id"],"retry")
        self.assertEqual(restarted.claim("worker2")["attempts"],2)
        restarted.control(job["id"],"cancel")
        restarted.finish(job["id"],"worker2","succeeded",result={"output":"saved"})
        self.assertEqual(restarted.job(job["id"])["state"],"cancelled")

    def test_shutdown_pauses_queued_jobs_and_requests_checkpoint_for_running_jobs(self):
        active = self.store.enqueue("import", {"path": "active.mp4"})
        waiting = self.store.enqueue("import", {"path": "waiting.mp4"})
        cloud = self.store.enqueue("analyze", {"asset_id": "cloud"})
        claimed = self.store.claim("worker-owner")
        self.assertEqual(claimed["id"], active["id"])

        self.store.pause_for_shutdown()

        self.assertEqual(self.store.job(active["id"])["state"], "pause_requested")
        self.assertEqual(self.store.job(waiting["id"])["state"], "paused")
        self.assertEqual(self.store.job(cloud["id"])["state"], "queued", "cloud retry lifecycle belongs to its own tickets")
        self.assertEqual(self.store.job(active["id"])["payload"], {"path": "active.mp4"})
        self.assertIsNone(self.store.claim("another-worker"))
        with self.assertRaisesRegex(RuntimeError, "正在退出"):
            self.store.enqueue("preview", {"plan_id": "new"})
        with self.assertRaisesRegex(RuntimeError, "正在退出"):
            self.store.control(waiting["id"], "resume")

        restarted = Store(self.store.path)
        restarted.start_session()
        self.assertEqual(restarted.job(waiting["id"])["state"], "paused",
                         "Local work remains paused when a new session starts")
        restarted.control(waiting["id"], "resume")
        self.assertEqual(restarted.job(waiting["id"])["state"], "queued")

    def test_only_explicitly_interrupted_cloud_operations_are_requeued_once(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig().model_dump()
        task = self.store.enqueue("analyze", {"asset_id": "a", "config": config})
        self.store.claim("worker")
        cache_key = "qwen-audio-v1:a:endpoint:model:0:120000000"
        operation_id = self.store.begin_cloud_operation(task["id"], "asr", config, cache_key)

        self.store.pause_for_shutdown()
        self.assertTrue(self.store.interruptible_operation(operation_id))

        restarted = Store(self.store.path)
        restarted.start_session()
        self.assertEqual(restarted.job(task["id"])["state"], "queued")
        self.assertIsNone(restarted.cached(cache_key))
        self.assertEqual(restarted.recover_exit_interrupted_operations(), 0)
        self.assertEqual(restarted.job(task["id"])["state"], "queued")

    def test_saved_cloud_response_wins_race_with_shutdown(self):
        from app.ai.providers import CloudConfig
        config = CloudConfig().model_dump()
        task = self.store.enqueue("analyze", {"asset_id": "a", "config": config})
        self.store.claim("worker")
        cache_key = "qwen-audio-v1:a:endpoint:model:0:120000000"
        operation_id = self.store.begin_cloud_operation(task["id"], "asr", config, cache_key)
        cached = {"status": "response_received", "response": {"output": {"text": "saved"}}}
        self.assertTrue(self.store.complete_cloud_operation(operation_id, cache_key, cached))

        self.store.pause_for_shutdown()
        restarted = Store(self.store.path)
        restarted.start_session()
        self.assertEqual(restarted.cached(cache_key), cached)
        self.assertEqual(restarted.job(task["id"])["state"], "queued")

    def test_revision_compare_and_swap_preserves_history(self):
        self.store.put("plan",{"id":"p","revision":1,"value":"original"})
        new=self.store.next_revision("plan","p",{"id":"p","value":"edited"},expected_revision=1)
        self.assertEqual(new["revision"],2)
        self.assertEqual(self.store.get("plan","p",1)["value"],"original")
        with self.assertRaises(ValueError):
            self.store.next_revision("plan","p",{"id":"p"},expected_revision=1)

    def test_result_metadata_cache_and_followup_are_idempotent(self):
        objects=[("asset",{"id":"a","name":"source"})]
        self.store.commit_result("result:job",{"asset_id":"a"},objects,("preview",{"plan_id":"p"}))
        self.store.commit_result("result:job",{"asset_id":"other"},objects,("preview",{"plan_id":"p"}))
        self.assertEqual(len(self.store.list("asset")),1)
        self.assertEqual(len(self.store.jobs()),1)
        self.assertEqual(self.store.cached("result:job"),{"asset_id":"a"})


class ProviderTests(unittest.TestCase):
    def test_custom_endpoint_routes_planner_and_requires_model_key(self):
        from app.ai.providers import CloudConfig, ProviderError, model_api_key
        from app.ai.roughcut import RoughcutRequest, select_intervals
        config = CloudConfig(base_url="https://gateway.example/v1/")
        self.assertEqual(config.chat_url, "https://gateway.example/v1/chat/completions")
        with patch('app.ai.providers.read_secret', return_value=None):
            with self.assertRaises(ProviderError):
                model_api_key(config)
        transcript = [Transcript(source_asset_id='a', start_us=0, end_us=5000000, text='面料柔软。')]
        result = {'choices': [{'message': {'content': '{"clips":[{"start_ms":0,"end_ms":5000,"reason":"面料"}]}'}}]}
        with patch('app.ai.providers.read_secret', return_value='model-key'), patch('app.ai.roughcut.cloud_json', return_value=result) as network:
            select_intervals(transcript, RoughcutRequest(product_name='测试'), config, 10000000)
            self.assertEqual(network.call_args.args[1], config.chat_url)
            self.assertEqual(network.call_args.args[3], 'model-key')

    def test_current_config_rejects_removed_visual_settings(self):
        from app.ai.providers import CloudConfig
        with self.assertRaises(ValidationError):
            CloudConfig.model_validate({'vision_model': 'unused'})

    def test_base_url_rejects_credentials_query_and_full_operation_path(self):
        from app.ai.providers import CloudConfig
        for url in ['http://gateway.example/v1','https://key:secret@gateway.example/v1',
                    'https://gateway.example/v1?key=secret','https://gateway.example/v1#fragment',
                    'https://gateway.example/v1/chat/completions']:
            with self.assertRaises(ValidationError):
                CloudConfig(base_url=url)



if __name__ == "__main__":
    unittest.main()
