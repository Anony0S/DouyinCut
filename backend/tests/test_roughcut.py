import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings
from app.core.domain import Asset, Transcript
from app.ai.providers import ASR_CHUNK_US, CloudConfig, ProviderError, audio_segments, transcribe
from app.ai.roughcut import (DEFAULT_INSTRUCTIONS, RoughcutRequest, TimeSelection,
                          candidates_from_intervals, select_intervals, validate_transcripts,
                          validate_selection_duration)
from app.core.storage import Store
from app.worker import Worker


class RoughcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(self.root / 'data')
        self.settings.prepare()
        self.store = Store(self.settings.database)
        self.asset = Asset(id='one-garment', path=str(self.root / 'source.mp4'), name='奶黄千层.mp4',
                           sha256='mock-hash', duration_us=250_000_000, width=160, height=120, has_audio=True)
        self.config = CloudConfig(base_url='https://text.example/v1', planner_model='test-text-model',
                                  asr_base_url='https://speech.example', asr_model='qwen-audio-3.1-asr-flash',
                                  oss_endpoint='https://oss-cn-beijing.aliyuncs.com', oss_bucket='test-bucket')
        self.sentences = [Transcript(source_asset_id=self.asset.id, start_us=i * 10_000_000,
                                     end_us=i * 10_000_000 + 5_000_000, text=text)
                          for i, text in enumerate(['上身很显瘦。', '今天只要九十九。', '面料柔软透气。'])]
        self.selection = TimeSelection.model_validate({'clips': [
            {'start_ms': 20000, 'end_ms': 25000, 'reason': '面料'},
            {'start_ms': 0, 'end_ms': 5000, 'reason': '上身效果'}]})

    def test_real_two_minute_chunks_map_every_sentence_and_tail_and_reuse_cache(self):
        self.assertEqual(ASR_CHUNK_US, 120_000_000)
        response = {'output': {'sentences': [
            {'begin_time': 600, 'end_time': 1500, 'text': '第一句'},
            {'begin_time': 2000, 'end_time': 3800, 'text': '第二句'}]}}
        with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                patch('app.ai.providers.extract_audio') as extract, \
                patch('app.ai.providers.upload_audio', return_value='https://oss.example/audio'), \
                patch('app.ai.providers.cloud_json', return_value=response) as network:
            rows = transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None)
            self.assertEqual([(c.kwargs['start_us'], c.kwargs['end_us']) for c in extract.call_args_list],
                             [(0, 120_000_000), (120_000_000, 240_000_000), (240_000_000, 250_000_000)])
            self.assertEqual([(t.start_us, t.end_us) for t in rows],
                             [(600_000, 1_500_000), (2_000_000, 3_800_000),
                              (120_600_000, 121_500_000), (122_000_000, 123_800_000),
                              (240_600_000, 241_500_000), (242_000_000, 243_800_000)])
            for call in network.call_args_list:
                self.assertEqual(call.kwargs, {'sse': False, 'operation': 'asr', 'timeout_seconds': 300})
            self.assertEqual(validate_transcripts(rows, self.asset), rows)
            self.assertEqual(transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None), rows)
            self.assertEqual(network.call_count, 3)

    def test_planner_receives_all_text_and_product_and_defaults_without_images(self):
        request = RoughcutRequest(product_name='奶黄千层', instructions='')
        response = {'choices': [{'message': {'content': self.selection.model_dump_json()}}]}
        with patch('app.ai.roughcut.model_api_key', return_value='mock-key'), \
                patch('app.ai.roughcut.cloud_json', return_value=response) as network:
            self.assertEqual(select_intervals(self.sentences, request, self.config, self.asset.duration_us), self.selection)
        payload = network.call_args.args[2]
        self.assertIsInstance(payload['messages'][0]['content'], str)
        prompt = json.loads(payload['messages'][0]['content'].split('\n', 1)[1])
        self.assertNotIn('product_info', prompt)
        self.assertEqual(prompt['duration_tolerance_ms'], 15000)
        self.assertEqual(prompt['instructions'], DEFAULT_INSTRUCTIONS)
        self.assertEqual([t['start_ms'] for t in prompt['transcript']], [0, 10000, 20000])
        self.assertEqual(prompt['source_duration_ms'], 250000)
        self.assertNotIn('image_url', json.dumps(payload))
        self.assertEqual(network.call_args.kwargs, {'timeout_seconds': 300, 'operation': 'planner'})

    def test_text_only_asr_keeps_response_for_hover_and_retry_never_repays(self):
        response = {'output': {'text': '只有整段文本'}, 'request_id': 'text-only-request'}
        with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                patch('app.ai.providers.extract_audio'), \
                patch('app.ai.providers.upload_audio', return_value='https://oss.example/audio'), \
                patch('app.ai.providers.cloud_json', return_value=response) as network:
            for _ in range(2):
                with self.assertRaisesRegex(ProviderError, '缺少句级时间戳') as raised:
                    transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None,
                               require_sentence_timing=True)
                self.assertEqual(json.loads(raised.exception.details['response_body']), response)
            network.assert_called_once()

    def test_selection_omits_price_and_reorders_exact_source_sentences(self):
        clips = candidates_from_intervals(self.selection, self.sentences, 'product')
        self.assertEqual([(c.start_us, c.end_us) for c in clips], [(20_000_000, 25_000_000), (0, 5_000_000)])
        self.assertEqual([c.evidence for c in clips], ['面料柔软透气。', '上身很显瘦。'])


    def test_coarse_wrong_source_overlapping_or_out_of_bounds_asr_cannot_cut(self):
        for rows in ([], [self.sentences[0].model_copy(update={'timing': 'audio_chunk'})],
                     [self.sentences[0].model_copy(update={'source_asset_id': 'wrong'})],
                     [self.sentences[0].model_copy(update={'end_us': self.asset.duration_us + 1})],
                     [self.sentences[0], self.sentences[1].model_copy(update={'start_us': 1})]):
            with self.subTest(rows=rows), self.assertRaises(ProviderError):
                validate_transcripts(rows, self.asset)

    def test_bad_planner_result_retains_full_cloud_response(self):
        response = {'request_id': 'test-request', 'choices': [{'message': {'content': '{"clips":[]}'}}]}
        with patch('app.ai.roughcut.model_api_key', return_value='mock-key'), \
                patch('app.ai.roughcut.cloud_json', return_value=response):
            with self.assertRaises(ProviderError) as raised:
                select_intervals(self.sentences, RoughcutRequest(product_name='test'), self.config, self.asset.duration_us)
        self.assertEqual(json.loads(raised.exception.details['response_body']), response)

    def test_worker_publishes_single_product_plan_and_automatic_preview_without_vision(self):
        self.store.put('asset', self.asset.model_dump())
        job = self.store.enqueue('analyze', {'asset_id': self.asset.id, 'config': self.config.model_dump(),
                                            'product_name': '奶黄千层'})
        worker = Worker(self.settings)
        with patch('app.ai.providers.transcribe', return_value=self.sentences), \
                patch('app.ai.roughcut.select_intervals', return_value=self.selection), \
                patch('app.media.processing.extract_frame') as vision:
            worker.run_once()
            vision.assert_not_called()
        result = self.store.job(job['id'])
        self.assertEqual(result['state'], 'succeeded', result.get('error'))
        self.assertEqual(result['result']['mode'], 'asr_text_roughcut')
        self.assertEqual(result['result']['timestamp_unit'], 'milliseconds')
        self.assertEqual(result['result']['selected_intervals'], self.selection.model_dump()['clips'])
        snapshot = self.store.cached(f"roughcut-transcript:{job['id']}")
        self.assertTrue(snapshot['ready'])
        self.assertEqual(snapshot['sentences'], [t.model_dump() for t in self.sentences])
        self.assertEqual(len(self.store.list('product')), 1)
        plan = self.store.get('plan', result['result']['plan_id'])
        self.assertEqual([c['source_start_us'] for c in plan['clips']], [20_000_000, 0])
        self.assertEqual([s['text'] for s in plan['subtitles']], ['面料柔软透气。', '上身很显瘦。'])
        previews = [j for j in self.store.jobs() if j['kind'] == 'preview']
        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0]['payload']['plan_id'], plan['id'])
        self.assertTrue(any('素材不足' in w for w in plan['warnings']))

    def test_worker_checks_cached_selection_against_task_tolerance(self):
        self.store.put('asset', self.asset.model_dump())
        sentences=[Transcript(source_asset_id=self.asset.id,start_us=i*10_000_000,
            end_us=i*10_000_000+5_000_000,text='面料柔软。') for i in range(4)]
        decision=TimeSelection.model_validate({'clips':[{'start_ms':i*10000,'end_ms':i*10000+5000,
            'reason':'面料'} for i in range(4)]})
        for tolerance, expected in ((4_000_000,'failed'),(5_000_000,'succeeded')):
            job=self.store.enqueue('analyze',{'asset_id':self.asset.id,'product_name':'测试',
                'config':self.config.model_dump(),'target_duration_us':15_000_000,'duration_tolerance_us':tolerance})
            self.store.cache(f"roughcut-timestamps-v1:{job['id']}",decision.model_dump())
            with patch('app.ai.providers.transcribe',return_value=sentences), \
                    patch('app.ai.roughcut.select_intervals') as planner:
                Worker(self.settings).run_once()
            result=self.store.job(job['id'])
            self.assertEqual(result['state'],expected,result.get('error'))
            planner.assert_not_called()
            if expected=='failed':
                self.assertIn('4 秒偏差',result['error'])
                self.assertEqual(self.store.list('plan'),[])
            else:
                plan=self.store.get('plan',result['result']['plan_id'])
                self.assertFalse(any('片段较长' in warning for warning in plan['warnings']))

    def test_planner_retry_reuses_real_asr_cache_and_does_not_publish_partial_plan(self):
        self.store.put('asset', self.asset.model_dump())
        response = {'output': {'sentences': [{'begin_time': 0, 'end_time': 5000, 'text': '上身很显瘦。'}]}}
        job = self.store.enqueue('analyze', {'asset_id': self.asset.id, 'product_name': '奶黄千层',
                                            'config': self.config.model_dump()})
        worker = Worker(self.settings)
        with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                patch('app.ai.providers.extract_audio'), \
                patch('app.ai.providers.upload_audio', return_value='https://oss.example/audio'), \
                patch('app.ai.providers.cloud_json', return_value=response) as network, \
                patch('app.ai.roughcut.select_intervals', side_effect=[ProviderError('planner failed'), TimeSelection.model_validate({'clips': [{'start_ms': 0, 'end_ms': 5000, 'reason': '上身效果'}]})]):
            worker.run_once()
            self.assertEqual(self.store.job(job['id'])['state'], 'failed')
            self.assertEqual(self.store.list('plan'), [])
            self.store.control(job['id'], 'retry')
            worker.run_once()
            self.assertEqual(self.store.job(job['id'])['state'], 'succeeded')
            self.assertEqual(network.call_count, 3)
        self.assertEqual(len(self.store.list('product')), 1)
        self.assertEqual(len(self.store.list('plan')), 1)


    def test_paragraph_words_split_to_real_short_sentences_without_fabricated_times(self):
        sentence = {'begin_time': 0, 'end_time': 120000, 'text': '上身显瘦。今天九十九。面料柔软。', 'words': [
            {'begin_time': 600, 'end_time': 4000, 'text': '上身显瘦', 'punctuation': '。'},
            {'begin_time': 10000, 'end_time': 14000, 'text': '今天九十九', 'punctuation': '。'},
            {'begin_time': 40000, 'end_time': 45000, 'text': '面料柔软', 'punctuation': '。'}]}
        rows = audio_segments({'output': {'sentence': sentence}}, self.asset.id, 120000000, 240000000)
        self.assertEqual([(t.start_us, t.end_us, t.text) for t in rows],
                         [(120600000, 124000000, '上身显瘦。'), (130000000, 134000000, '今天九十九。'),
                          (160000000, 165000000, '面料柔软。')])
        self.assertEqual(validate_transcripts(rows, self.asset), rows)
        selection = TimeSelection.model_validate({'clips': [
            {'start_ms': 120600, 'end_ms': 124000, 'reason': '上身'},
            {'start_ms': 160000, 'end_ms': 165000, 'reason': '面料'}]})
        self.assertEqual([c.evidence for c in candidates_from_intervals(selection, rows, 'product')],
                         ['上身显瘦。', '面料柔软。'])

    def test_word_timestamps_split_long_asr_paragraphs_at_commas_and_eight_second_limit(self):
        words = []
        text = ''
        for index, character in enumerate('一二三四五六七八九十一二三四'):
            punctuation = '，' if index == 3 else ''
            words.append({'begin_time': index * 1000, 'end_time': (index + 1) * 1000,
                          'text': character, 'punctuation': punctuation})
            text += character + punctuation
        words[-1]['punctuation'] = '。'
        text += '。'
        rows = audio_segments({'sentence': {'begin_time': 0, 'end_time': 30000,
                                             'text': text, 'words': words}}, self.asset.id, 0, 30000000)
        self.assertEqual([row.text for row in rows], ['一二三四，', '五六七八九十一二', '三四。'])
        self.assertEqual([(row.start_us, row.end_us) for row in rows],
                         [(0, 4000000), (4000000, 12000000), (12000000, 14000000)])

    def test_invalid_or_incomplete_word_timing_is_not_used_to_guess_cuts(self):
        for words in ([{'begin_time': 0, 'end_time': 4000, 'text': '上身'}],
                      [{'begin_time': -1, 'end_time': 4000, 'text': '上身显瘦。'}],
                      [{'begin_time': 0, 'end_time': 130000, 'text': '上身显瘦。'}]):
            with self.subTest(words=words), self.assertRaises(ProviderError):
                audio_segments({'sentence': {'text': '上身显瘦。', 'begin_time': 0,
                                             'end_time': 120000, 'words': words}}, self.asset.id, 0, 120000000)

    def test_cached_paragraph_response_stops_instead_of_exporting_entire_chunk(self):
        import hashlib
        endpoint_hash = hashlib.sha256(self.config.asr_url.encode()).hexdigest()
        key = f'qwen-audio-v1:{self.asset.sha256}:{endpoint_hash}:{self.config.asr_model}:0:120000000'
        self.store.cache(key, {'status': 'succeeded', 'response': {'sentence':
            {'begin_time': 0, 'end_time': 120000, 'text': '面料柔软。今天九十九。' * 20}}})
        with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                patch('app.ai.providers.cloud_json') as network:
            with self.assertRaisesRegex(ProviderError, '长段口播'):
                transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None,
                           require_sentence_timing=True)
            network.assert_not_called()

    def test_saved_native_words_can_be_reparsed_without_another_request(self):
        import hashlib
        endpoint_hash = hashlib.sha256(self.config.asr_url.encode()).hexdigest()
        asset = self.asset.model_copy(update={'duration_us': 120000000})
        key = f'qwen-audio-v1:{asset.sha256}:{endpoint_hash}:{self.config.asr_model}:0:120000000'
        sentence = {'text': '上身显瘦。面料柔软。', 'begin_time': 0, 'end_time': 120000,
                    'words': [{'begin_time': 1000, 'end_time': 3000, 'text': '上身显瘦。'},
                              {'begin_time': 10000, 'end_time': 14000, 'text': '面料柔软。'}]}
        self.store.cache(key, {'status': 'succeeded', 'response': {'sentence': sentence}, 'segments': [
            {'start_us': 0, 'end_us': 120000000, 'text': sentence['text'], 'timing': 'sentence'}]})
        with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                patch('app.ai.providers.cloud_json') as network:
            rows = transcribe(asset, self.config, self.settings, self.store, self.root, lambda: None,
                              require_sentence_timing=True)
        self.assertEqual([(r.start_us, r.end_us) for r in rows], [(1000000, 3000000), (10000000, 14000000)])
        network.assert_not_called()

    def test_explicit_refresh_is_cached_per_task_and_does_not_bypass_uncertain_requests(self):
        response = {'sentence': {'text': '上身显瘦。', 'begin_time': 600, 'end_time': 4000}}
        with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                patch('app.ai.providers.extract_audio'), \
                patch('app.ai.providers.upload_audio', return_value='https://oss.example/audio'), \
                patch('app.ai.providers.cloud_json', return_value=response) as network:
            rows = transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None,
                              require_sentence_timing=True, refresh_scope='one-task')
            self.assertEqual(transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None,
                                        require_sentence_timing=True, refresh_scope='one-task'), rows)
            self.assertEqual(network.call_count, 3)
            self.assertEqual(transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None), rows)
            self.assertEqual(network.call_count, 3)
            network.side_effect = ProviderError('timeout')
            with self.assertRaises(ProviderError):
                transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None,
                           refresh_scope='uncertain-task')
            for scope in (None, 'uncertain-task', 'new-refresh-task'):
                with self.assertRaises(ProviderError):
                    transcribe(self.asset, self.config, self.settings, self.store, self.root, lambda: None,
                               refresh_scope=scope)
            self.assertEqual(network.call_count, 4)

    def test_whole_two_minute_decision_cannot_pass_for_sixty_second_roughcut(self):
        selection = TimeSelection.model_validate({'clips': [{'start_ms': 840080, 'end_ms': 960000, 'reason': '整段'}]})
        with self.assertRaisesRegex(ValueError, '119.9 秒'):
            validate_selection_duration(selection, 60000000)

    def test_duration_tolerance_controls_actual_selection_limit(self):
        selection = TimeSelection.model_validate({'clips': [
            {'start_ms': index * 10000, 'end_ms': (index + 1) * 10000, 'reason': '独立卖点'}
            for index in range(8)]})
        with self.assertRaisesRegex(ValueError, '15 秒偏差'):
            validate_selection_duration(selection, 60_000_000, 15_000_000)
        validate_selection_duration(selection, 60_000_000, 20_000_000)
        with self.assertRaisesRegex(ValueError, '0 秒偏差'):
            validate_selection_duration(selection, 60_000_000, 0)
        # Insufficient material may be shorter; tolerance never invents source content.
        validate_selection_duration(self.selection, 60_000_000, 0)

    def test_custom_tolerance_is_in_planner_prompt(self):
        response = {'choices': [{'message': {'content': self.selection.model_dump_json()}}]}
        with patch('app.ai.roughcut.model_api_key', return_value='mock-key'), \
                patch('app.ai.roughcut.cloud_json', return_value=response) as network:
            select_intervals(self.sentences, RoughcutRequest(product_name='测试', duration_tolerance_us=7_000_000),
                             self.config, self.asset.duration_us)
        prompt = json.loads(network.call_args.args[2]['messages'][0]['content'].split('\n', 1)[1])
        self.assertEqual(prompt['duration_tolerance_ms'], 7000)

    def test_single_long_clip_is_rejected_even_when_total_duration_is_valid(self):
        selection = TimeSelection.model_validate({'clips': [{'start_ms': 0, 'end_ms': 11000, 'reason': '整段'}]})
        with self.assertRaisesRegex(ValueError, '单个连续片段最长 10 秒'):
            validate_selection_duration(selection, 60000000)

    def test_planner_prompt_limits_clip_length_and_excludes_other_products(self):
        response = {'choices': [{'message': {'content': self.selection.model_dump_json()}}]}
        with patch('app.ai.roughcut.model_api_key', return_value='mock-key'), \
                patch('app.ai.roughcut.cloud_json', return_value=response) as network:
            select_intervals(self.sentences, RoughcutRequest(product_name='奶黄千层'), self.config,
                             self.asset.duration_us)
        prompt = network.call_args.args[2]['messages'][0]['content']
        self.assertIn('每个连续选片区间最多 10 秒', prompt)
        self.assertIn('其他商品 / 款式', prompt)

    def test_invalid_repeated_overlapping_or_unknown_timestamps_are_rejected(self):
        for clips in ([{'start_ms': 1, 'end_ms': 5000, 'reason': 'unknown'}],
                      [{'start_ms': 0, 'end_ms': 5000, 'reason': 'a'}, {'start_ms': 0, 'end_ms': 5000, 'reason': 'b'}],
                      [{'start_ms': 0, 'end_ms': 15000, 'reason': 'a'}, {'start_ms': 10000, 'end_ms': 25000, 'reason': 'b'}]):
            with self.subTest(clips=clips), self.assertRaises(ValueError):
                candidates_from_intervals(TimeSelection.model_validate({'clips': clips}), self.sentences, 'product')


class RoughcutApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name) / 'data')
        self.app = create_app(self.settings, token='test-token')
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'X-Session-Token': 'test-token'}

    def test_saved_speech_model_is_used_by_new_analysis_and_failed_job_retry(self):
        store = self.app.state.store
        asset = Asset(id='video', path='source.mp4', sha256='hash', name='video', duration_us=10_000_000,
                      width=160, height=120, has_audio=True)
        store.put('asset', asset.model_dump())
        previous = CloudConfig(asr_model='other-speech-model', oss_endpoint='https://oss-cn-beijing.aliyuncs.com',
                               oss_bucket='test-bucket')
        store.put('config', {'id': 'cloud', 'config': previous.model_dump()})
        with patch('app.api.model_api_key', return_value='mock-key'), patch('app.api.asr_api_key', return_value='mock-asr-key'):
            task = self.client.post('/api/v1/assets/video/analyze', headers=self.headers,
                                    json={'product_name': '奶黄千层'}).json()
        worker = Worker(self.settings)
        with patch('app.ai.providers.transcribe', side_effect=ProviderError('模型调用失败')):
            worker.run_once()
        current = previous.model_copy(update={'asr_model': 'qwen-audio-3.1-asr-flash'})
        saved = self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=current.model_dump())
        self.assertEqual(saved.status_code, 200, saved.text)
        retried = self.client.post(f"/api/v1/jobs/{task['id']}/retry", headers=self.headers)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()['payload']['config']['asr_model'], current.asr_model)
        self.assertIsNone(retried.json()['error'])
        selection = TimeSelection.model_validate({'clips': [{'start_ms': 600, 'end_ms': 4000, 'reason': '面料'}]})
        response = {'sentence': {'text': '面料柔软。', 'begin_time': 600, 'end_time': 4000}}
        with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                patch('app.ai.providers.extract_audio'), \
                patch('app.ai.providers.upload_audio', return_value='https://oss.example/audio'), \
                patch('app.ai.providers.cloud_json', return_value=response) as network, \
                patch('app.ai.roughcut.select_intervals', return_value=selection):
            worker.run_once()
        network.assert_called_once()
        self.assertEqual(network.call_args.args[2]['model'], 'qwen-audio-3.1-asr-flash')
        self.assertEqual(store.job(task['id'])['state'], 'succeeded')
        with patch('app.api.model_api_key', return_value='mock-key'), patch('app.api.asr_api_key', return_value='mock-asr-key'):
            fresh = self.client.post('/api/v1/assets/video/analyze', headers=self.headers,
                                     json={'product_name': '奶黄千层'})
        self.assertEqual(fresh.status_code, 202, fresh.text)
        self.assertEqual(fresh.json()['payload']['config']['asr_model'], current.asr_model)

    def test_file_copy_is_local_authenticated_and_uses_safe_name(self):
        url = '/api/v1/assets/upload'
        self.assertEqual(self.client.post(url, params={'filename': 'video.mp4'}, content=b'video').status_code, 401)
        result = self.client.post(url, headers=self.headers, params={'filename': '../../奶黄千层.MP4'},
                                  content=b'mock-video-binary')
        self.assertEqual(result.status_code, 202, result.text)
        job = result.json()
        path = Path(job['payload']['path'])
        self.assertTrue(path.is_relative_to(self.settings.data_dir / 'recordings' / 'imports'))
        self.assertEqual(path.read_bytes(), b'mock-video-binary')
        self.assertEqual(job['payload']['display_name'], '奶黄千层.MP4')
        for filename, body in [('bad.exe', b'bad'), ('empty.mp4', b'')]:
            failure = self.client.post(url, headers=self.headers, params={'filename': filename}, content=body)
            self.assertEqual(failure.status_code, 422, failure.text)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_full_transcript_snapshot_is_authenticated_even_after_planner_failure(self):
        store = self.app.state.store
        job = store.enqueue('analyze', {'asset_id': 'video', 'product_name': '奶黄千层'})
        saved = {'job_id': job['id'], 'source_asset_id': 'video', 'ready': True, 'sentences': [
            {'source_asset_id': 'video', 'start_us': 1000000, 'end_us': 4000000, 'text': '上身显瘦。', 'timing': 'sentence'}]}
        store.cache(f"roughcut-transcript:{job['id']}", saved)
        url = f"/api/v1/jobs/{job['id']}/transcript"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.headers).json(), saved)
        self.assertEqual(self.client.get(url, headers=self.headers).headers['Cache-Control'], 'no-store')

    def test_product_audio_config_preflight_and_active_analysis_guard(self):
        asset = Asset(id='video', path='source.mp4', sha256='hash', name='video', duration_us=10_000_000,
                      width=160, height=120, has_audio=True)
        store = self.app.state.store
        store.put('asset', asset.model_dump())
        store.put('config', {'id': 'cloud', 'config': CloudConfig(oss_endpoint='https://oss-cn-beijing.aliyuncs.com',
                                                               oss_bucket='test-bucket').model_dump()})
        url = '/api/v1/assets/video/analyze'
        self.assertEqual(self.client.post(url, headers=self.headers, json={}).status_code, 422)
        with patch('app.api.model_api_key', return_value='mock-key'), patch('app.api.asr_api_key', return_value='mock-asr-key'):
            self.assertEqual(self.client.post(url, headers=self.headers, json={'product_name': '  '}).status_code, 422)
            result = self.client.post(url, headers=self.headers, json={'product_name': ' 奶黄千层 ', 'instructions': ''})
            self.assertEqual(result.status_code, 202, result.text)
            self.assertEqual(result.json()['payload']['product_name'], '奶黄千层')
            self.assertEqual(result.json()['payload']['instructions'], DEFAULT_INSTRUCTIONS)
            self.assertEqual(self.client.post(url, headers=self.headers, json={'product_name': 'new'}).status_code, 409)
            store.put('asset', asset.model_copy(update={'id': 'silent', 'has_audio': False}).model_dump())
            self.assertEqual(self.client.post('/api/v1/assets/silent/analyze', headers=self.headers,
                                              json={'product_name': 'new'}).status_code, 422)
