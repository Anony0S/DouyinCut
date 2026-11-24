import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

AVAILABLE=all(importlib.util.find_spec(m) is not None for m in ["fastapi","httpx"])


@unittest.skipUnless(AVAILABLE,"FastAPI/httpx 依赖未安装，API 集成检查待执行")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.api import create_app
        from app.core.config import Settings
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Settings(Path(self.temp.name)),token="test-token")
        self.client=TestClient(self.app)
        self.headers={"X-Session-Token":"test-token"}

    def tearDown(self):
        self.client.close();self.temp.cleanup()

    def test_requires_token_and_rejects_foreign_origin(self):
        self.assertEqual(self.client.get('/api/v1/assets').status_code,401)
        self.assertEqual(self.client.get('/api/v1/assets',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/v1/assets',headers={**self.headers,'Origin':'https://untrusted.example'}).status_code,401)
        self.assertEqual(self.client.get('/api/v1/assets/unknown/media',headers=self.headers).status_code,404)

    def test_cleanup_requires_current_preview_for_the_selected_scope(self):
        from app.cleanup import SECONDS_PER_DAY

        url = '/api/v1/settings/cleanup-unused-files'
        output = Path(self.temp.name) / 'exports' / 'export_referenced'
        output.mkdir()
        video = output / 'video.mp4'
        video.write_bytes(b'example')
        old = time.time() - 4 * SECONDS_PER_DAY
        for path in (output, video):
            os.utime(path, (old, old))
        self.app.state.store.put('export', {'id': output.name, 'path': str(output)})
        with self.app.state.store.connection() as conn:
            conn.execute("UPDATE objects SET created=? WHERE id=?", (old, output.name))

        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post(url, headers=self.headers).status_code, 422)
        default = self.client.get(url, headers=self.headers).json()
        self.assertEqual(default['file_count'], 0)
        params = {'clear_expired_data': 'true'}
        self.assertEqual(self.client.get(url, headers=self.headers, params=params).json()['file_count'], 0)
        for days in (0, -1, 1.5):
            self.assertEqual(self.client.get(url, headers=self.headers,
                             params={**params, 'retention_days': days}).status_code, 422)
        preview = self.client.get(url, headers=self.headers, params={**params, 'retention_days': 3}).json()
        self.assertEqual((preview['file_count'], preview['record_count'], preview['estimated_bytes']), (1, 1, 7))
        self.assertTrue(video.exists())
        body = {'clear_expired_data': True, 'retention_days': 3, 'preview_id': default['preview_id']}
        self.assertEqual(self.client.post(url, headers=self.headers, json=body).status_code, 409)
        self.assertTrue(video.exists())
        body['preview_id'] = preview['preview_id']
        self.assertEqual(self.client.post(url, headers=self.headers,
                         json={**body, 'retention_days': 0}).status_code, 422)
        self.assertEqual(self.client.post(url, headers=self.headers,
                         json={**body, 'retention_days': 2}).status_code, 409)
        self.assertTrue(video.exists())
        result = self.client.post(url, headers=self.headers, json=body)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['deleted_files'], 1)
        self.assertEqual(result.json()['deleted_records'], 1)
        self.assertFalse(video.exists())

    def test_health_requires_auth_without_querying_business_data(self):
        with patch.object(self.app.state.store, 'connection', side_effect=AssertionError('Health queried storage')):
            self.assertEqual(self.client.get('/api/v1/health').status_code, 401)
            self.assertEqual(self.client.get('/api/v1/health', headers={'X-Session-Token': 'wrong-token'}).status_code, 401)
            self.assertEqual(self.client.get('/api/v1/health', headers={
                **self.headers, 'Origin': 'https://untrusted.example'}).status_code, 401)
            response = self.client.get('/api/v1/health', headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'ok')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_import_local_records_original_and_copies_to_workspace(self):
        url = "/api/v1/assets/import-local"
        source = Path(self.temp.name) / "source.mp4"
        source.write_bytes(b"video fixture")
        self.assertEqual(self.client.post(url, json={"path": str(source)}).status_code, 401)
        self.assertEqual(self.client.post(url, headers=self.headers, json={"path": "relative.mp4"}).status_code, 422)
        response = self.client.post(url, headers=self.headers, json={"path": str(source)})
        self.assertEqual(response.status_code, 202)
        payload = self.app.state.store.job(response.json()["id"])["payload"]
        copy = Path(payload["path"])
        self.assertEqual(copy.parent, Path(self.temp.name) / "recordings" / "imports")
        self.assertEqual(copy.read_bytes(), source.read_bytes())
        self.assertEqual(payload["original_path"], str(source))
        self.assertEqual(payload["original_file_id"], [source.stat().st_dev, source.stat().st_ino])

    def test_local_cover_reference_can_queue_generation_without_platform_binding(self):
        import base64
        from app.ai.providers import CloudConfig
        from app.core.domain import Asset, Canvas, Clip, Plan

        store = self.app.state.store
        asset = Asset(id="asset_local", path="source.mp4", name="source.mp4", sha256="hash",
                      duration_us=5_000_000, width=1080, height=1920, has_audio=True,
                      source_monitor_id="unbound_room")
        plan = Plan(product_id="product", canvas=Canvas(width=1080, height=1920),
                    clips=[Clip(source_asset_id=asset.id, source_start_us=0, source_end_us=1_000_000,
                                timeline_start_us=0, candidate_id="candidate")])
        store.put("asset", asset.model_dump())
        store.put("plan", plan.model_dump())
        store.put("config", {"id": "cloud", "config": CloudConfig(
            image_base_url="https://images.example/v1", image_model="cover-model").model_dump()})
        url = f"/api/v1/assets/{asset.id}/cover-references"
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aUioAAAAASUVORK5CYII=")
        self.assertEqual(self.client.post(url, content=png).status_code, 401)
        self.assertEqual(self.client.post(url, headers=self.headers, content=b"<svg/>").status_code, 422)
        with patch("app.ai.image_generation.MAX_IMAGE_BYTES", 8):
            self.assertEqual(self.client.post(url, headers=self.headers, content=png).status_code, 413)
        response = self.client.post(url, headers=self.headers, content=png)
        self.assertEqual(response.status_code, 201, response.text)
        image = response.json()
        self.assertEqual(self.client.post(url, headers=self.headers, content=png).json(), image)
        media = self.client.get(image["url"], headers=self.headers)
        self.assertEqual(media.content, png)
        self.assertEqual(media.headers["content-type"], "image/png")
        self.assertEqual(self.client.get(image["url"].replace(asset.id, "asset_other"),
                                         headers=self.headers).status_code, 404)

        network_id = "coverref_" + "a" * 32
        network_path = self.app.state.settings.data_dir / "covers" / asset.id / "references" / f"{network_id}.png"
        network_path.write_bytes(png)
        self.assertEqual(self.client.get(url, headers=self.headers).json(), {"images": [image]})
        self.assertEqual(self.client.delete(f"{url}/{network_id}", headers=self.headers).status_code, 404)
        self.assertTrue(network_path.is_file())

        prompt = "用参考图生成竖版封面。\n商品名称：轻薄针织开衫"
        with patch("app.api.read_secret", return_value="fixture"):
            queued = self.client.post(f"/api/v1/assets/{asset.id}/covers", headers=self.headers, json={
                "plan_id": plan.id, "revision": plan.revision, "reference_id": image["id"], "prompt": prompt,
            })
        self.assertEqual(queued.status_code, 202, queued.text)
        payload = queued.json()["payload"]
        self.assertEqual(payload["prompt"], prompt)
        self.assertEqual(Path(payload["reference_path"]).read_bytes(), png)
        self.assertEqual(self.client.delete(image["url"], headers=self.headers).status_code, 409)
        with store.connection() as conn:
            conn.execute("UPDATE jobs SET state='succeeded' WHERE id=?", (queued.json()["id"],))
        self.assertEqual(self.client.delete(image["url"], headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(image["url"], headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(url, headers=self.headers).json(), {"images": []})
        self.assertTrue(network_path.is_file())

    def test_cover_original_checks_ownership_and_file_location(self):
        store = self.app.state.store
        root = self.app.state.settings.data_dir / 'covers' / 'asset-one'
        root.mkdir(parents=True)
        original = root / 'original.webp'
        original.write_bytes(b'original-image')
        job = store.enqueue('cover', {'asset_id': 'asset-one'})
        store.claim('test-worker')
        store.finish(job['id'], 'test-worker', 'succeeded', result={
            'original_path': str(original), 'original_content_type': 'image/webp'})
        url = f"/api/v1/assets/asset-one/covers/{job['id']}/original"
        response = self.client.get(url, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'original-image')
        self.assertEqual(response.headers['content-type'], 'image/webp')
        self.assertEqual(self.client.get(url.replace('asset-one', 'asset-two'), headers=self.headers).status_code, 404)
        original.unlink()
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 410)

        outside = Path(self.temp.name) / 'outside.png'
        outside.write_bytes(b'outside')
        other = store.enqueue('cover', {'asset_id': 'asset-one'})
        store.claim('test-worker')
        store.finish(other['id'], 'test-worker', 'succeeded', result={'original_path': str(outside)})
        self.assertEqual(self.client.get(f"/api/v1/assets/asset-one/covers/{other['id']}/original", headers=self.headers).status_code, 410)
        missing = store.enqueue('cover', {'asset_id': 'asset-one'})
        store.claim('test-worker')
        store.finish(missing['id'], 'test-worker', 'succeeded', result={})
        self.assertEqual(self.client.get(f"/api/v1/assets/asset-one/covers/{missing['id']}/original", headers=self.headers).status_code, 410)

    def test_shutdown_keeps_status_readable_but_rejects_mutations(self):
        store = self.app.state.store
        job = store.enqueue('import', {'path': 'queued.mp4'})
        store.control(job['id'], 'pause')
        store.pause_for_shutdown()

        status = self.client.get('/api/v1/jobs', headers=self.headers)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()[0]['state'], 'paused')
        self.assertEqual(self.client.put('/api/v1/settings/editing-defaults', json={},
                                         headers=self.headers).status_code, 503)
        self.assertEqual(self.client.post(f"/api/v1/jobs/{job['id']}/resume",
                                          headers=self.headers).status_code, 503)

    def test_cloud_error_detail_is_authenticated_complete_and_not_in_job_list(self):
        store = self.app.state.store
        job = store.enqueue('analyze', {})
        store.claim('test-worker')
        body = '<html>' + 'full-body' * 2000 + '</html>'
        store.finish(job['id'], 'test-worker', 'failed', error='响应格式错误',
                     error_detail={'stage': 'json_decode', 'response_body': body})
        path = f"/api/v1/jobs/{job['id']}/error-detail"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers={**self.headers, 'Origin': 'https://untrusted.example'}).status_code, 401)
        response = self.client.get(path, headers=self.headers)
        self.assertEqual(response.json()['response_body'], body)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        listing = self.client.get('/api/v1/jobs', headers=self.headers)
        self.assertTrue(listing.json()[0]['has_error_detail'])
        self.assertNotIn(body, listing.text)

    def test_cookie_auth_and_unsupported_export_is_rejected(self):
        response=self.client.post('/api/v1/auth',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/v1/assets').status_code,200)
        response=self.client.post('/api/v1/plans/unknown/exports',json={'format':'jianying'})
        self.assertEqual(response.status_code,422)

    def test_media_cookie_isolated_between_loopback_ports(self):
        """A shared WebView profile must keep both workbenches playable."""
        from fastapi.testclient import TestClient
        from http.cookiejar import CookieJar
        from app.api import create_app
        from app.core.config import Settings
        from app.core.domain import Asset

        cookies = CookieJar()
        with tempfile.TemporaryDirectory() as temporary:
            clients = []
            try:
                for port in (8765, 50479):
                    root = Path(temporary) / str(port)
                    app = create_app(Settings(root, port=port), token=f"session-{port}")
                    source = root / 'source.mp4'
                    source.write_bytes(b'0123456789')
                    asset = Asset(id='source', path=str(source), name=source.name, sha256='test',
                                  duration_us=1_000_000, width=160, height=120, has_audio=True)
                    app.state.store.put('asset', asset.model_dump())
                    preview = root / 'previews' / 'preview.mp4'
                    preview.write_bytes(source.read_bytes())
                    app.state.store.put('export', {'id': 'preview', 'video_path': str(preview)})
                    client = TestClient(app, base_url=f'http://127.0.0.1:{port}', cookies=cookies)
                    clients.append(client)
                    auth = client.post('/api/v1/auth', headers={'X-Session-Token': f'session-{port}'})
                    self.assertEqual(auth.status_code, 200)
                    other_cookie = auth.headers['set-cookie'].split(';', 1)[0]
                # Video elements send cookies, without the fetch API's token header.
                for client in clients:
                    for path in ('/assets/source/media', '/exports/preview/media'):
                        response = client.get('/api/v1' + path, headers={'Range': 'bytes=0-3'})
                        self.assertEqual(response.status_code, 206, response.text)
                        self.assertEqual(response.content, b'0123')
                        self.assertEqual(response.headers['content-range'], 'bytes 0-3/10')
                # A different session's cookie cannot authorize another server.
                self.assertEqual(clients[0].get('/api/v1/assets/source/media', headers={
                    'Cookie': other_cookie}).status_code, 401)
            finally:
                for client in clients:
                    client.close()

    def test_ts_asset_media_is_served_as_source_without_generating_preview_proxy(self):
        from app.core.domain import Asset

        settings = self.app.state.settings
        source = Path(self.temp.name) / "source.ts"
        source.write_bytes(b"original-ts")
        asset = Asset(id="asset-ts-source", path=str(source), name=source.name, sha256="ts-hash",
                      duration_us=1_000_000, width=160, height=120, has_audio=True)
        self.app.state.store.put("asset", asset.model_dump())

        response = self.client.get(f"/api/v1/assets/{asset.id}/media", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"original-ts")
        self.assertFalse((settings.data_dir / "previews" / "assets").exists())
        self.assertTrue(source.is_file())

    def test_jianying_draft_requires_saved_revision_and_prevents_duplicate_active_jobs(self):
        from app.core.domain import Asset, Candidate, Canvas, Clip, Plan, Product
        store = self.app.state.store
        asset = Asset(id='asset', path='missing.mp4', name='直播.mp4', sha256='hash',
                      duration_us=10_000_000, width=1080, height=1920, has_audio=True)
        product = Product(id='product', name='针织开衫', confirmed=True)
        candidate = Candidate(id='candidate', source_asset_id=asset.id, product_id=product.id,
                              start_us=0, end_us=5_000_000, reason='上身效果')
        plan = Plan(id='plan', revision=1, product_id=product.id, canvas=Canvas(width=1080, height=1920),
                    clips=[Clip(source_asset_id=asset.id, source_start_us=0, source_end_us=5_000_000,
                                timeline_start_us=0, candidate_id=candidate.id)])
        for kind, body in [('asset', asset), ('product', product), ('candidate', candidate), ('plan', plan)]:
            store.put(kind, body.model_dump())
        path = '/api/v1/plans/plan/jianying-drafts'
        headers = {**self.headers, 'Content-Type': 'application/json'}
        queued = self.client.post(path, headers=headers, json={'revision': 1})
        self.assertEqual(queued.status_code, 202, queued.text)
        duplicate = self.client.post(path, headers=headers, json={'revision': 1})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.client.post(path, headers=headers, json={'revision': 2}).status_code, 404)

    def test_model_settings_persist_url_without_exposing_secret(self):
        from unittest.mock import patch
        with patch('app.api.read_secret',return_value=None):
            config=self.client.get('/api/v1/settings/cloud',headers=self.headers).json()['config']
        config['base_url']='https://gateway.example/v1/'
        self.assertEqual(self.client.patch('/api/v1/settings/cloud',headers=self.headers,json=config).status_code,200)
        with patch('app.api.read_secret',return_value='private-test-key'):
            response=self.client.get('/api/v1/settings/cloud',headers=self.headers)
        self.assertEqual(response.json()['config']['base_url'],'https://gateway.example/v1')
        self.assertTrue(response.json()['secrets']['llm_api_key'])
        self.assertNotIn('private-test-key',response.text)
        with patch('app.api.write_secret') as save:
            response=self.client.post('/api/v1/settings/secrets',headers=self.headers,json={'name':'llm_api_key','value':'private-test-key'})
            self.assertEqual(response.status_code,200)
            save.assert_called_once_with('llm_api_key','private-test-key')
            self.assertNotIn('private-test-key',response.text)

    def test_asr_model_can_be_manually_saved_and_reloaded(self):
        from unittest.mock import patch
        with patch('app.api.read_secret', return_value=None):
            config = self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']
            self.assertEqual(config['asr_model'], '')
            config['asr_model'] = ' custom-file-asr-v1 '
            response = self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=config)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['asr_model'], 'custom-file-asr-v1')
            self.assertEqual(self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']['asr_model'], 'custom-file-asr-v1')
            for invalid in ('invalid model', 'https://model.example', 'x' * 101):
                config['asr_model'] = invalid
                self.assertEqual(self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=config).status_code, 422)

    def test_speech_settings_persist_independently_and_validate(self):
        from unittest.mock import patch
        with patch('app.api.read_secret', return_value=None):
            config = self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']
            self.assertEqual(config['asr_base_url'], '')
            self.assertEqual(config['asr_timeout_seconds'], 300)
            text_url = config['base_url']
            config.update(asr_base_url='https://speech.example/gateway/', asr_timeout_seconds=420)
            saved = self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=config)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()['asr_base_url'], 'https://speech.example/gateway')
            reloaded = self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']
            self.assertEqual(reloaded['asr_timeout_seconds'], 420)
            self.assertEqual(reloaded['base_url'], text_url)
            self.assertEqual(reloaded['chat_timeout_seconds'], 300)
            for url in ('http://speech.example', 'https://user:secret@speech.example',
                        'https://speech.example?key=private-key', 'https://speech.example#fragment',
                        'https://speech.example/api/v1/services/aigc/multimodal-generation/generation'):
                invalid = self.client.patch('/api/v1/settings/cloud', headers=self.headers,
                                            json={**config, 'asr_base_url': url})
                self.assertEqual(invalid.status_code, 422)
                self.assertNotIn('private-key', invalid.text)
            for timeout in (0, 29, 901, '300', 300.5):
                self.assertEqual(self.client.patch('/api/v1/settings/cloud', headers=self.headers,
                    json={**config, 'asr_timeout_seconds': timeout}).status_code, 422)

    def test_cloud_cards_can_save_before_other_cards_are_changed(self):
        from unittest.mock import patch
        with patch('app.api.read_secret', return_value=None):
            config = self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']
            for name in ('base_url', 'planner_model', 'asr_base_url', 'asr_model'):
                self.assertEqual(config[name], '')
            cards = {
                'text': {'base_url': 'https://text.example/v1', 'planner_model': 'custom-text'},
                'asr': {'asr_base_url': 'https://speech.example', 'asr_model': 'custom-asr'},
                'oss': {'oss_endpoint': 'https://oss-cn-beijing.aliyuncs.com', 'oss_bucket': 'clips'},
            }
            for card, changes in cards.items():
                with self.subTest(card=card):
                    # Each payload starts with untouched defaults for the other cards.
                    response = self.client.patch('/api/v1/settings/cloud', headers=self.headers,
                                                 json={**config, **changes})
                    self.assertEqual(response.status_code, 200, response.text)
                    for name, value in changes.items():
                        self.assertEqual(response.json()[name], value)
                    reloaded = self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']
                    self.assertEqual(reloaded, response.json())

    def test_analysis_requires_each_key_and_snapshots_separate_connections(self):
        from unittest.mock import patch
        from app.ai.providers import CloudConfig
        from app.core.domain import Asset
        store = self.app.state.store
        store.put('asset', Asset(id='split-key-asset', sha256='digest', path='source.mp4', name='source',
            duration_us=10_000_000, width=320, height=240, has_audio=True).model_dump())
        config = CloudConfig(base_url='https://text.example/v1', asr_base_url='https://speech.example',
                             planner_model='text-model', asr_model='speech-model',
                             oss_endpoint='https://oss.example', oss_bucket='audio')
        store.put('config', {'id': 'cloud', 'config': config.model_dump()})
        url = '/api/v1/assets/split-key-asset/analyze'
        for values, message in (({'llm_api_key': 'text-key'}, '语音识别模型卡片'),
                                ({'asr_api_key': 'speech-key'}, '文本剪辑模型卡片')):
            with patch('app.ai.providers.read_secret', side_effect=values.get):
                response = self.client.post(url, headers=self.headers, json={'product_name': '测试商品'})
            self.assertGreaterEqual(response.status_code, 400)
            self.assertIn(message, response.text)
            self.assertEqual(store.jobs(), [])
        with patch('app.ai.providers.read_secret', side_effect={'llm_api_key': 'text-key', 'asr_api_key': 'speech-key'}.get):
            response = self.client.post(url, headers=self.headers, json={'product_name': '测试商品'})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()['payload']['config'], config.model_dump())
        self.assertNotIn('text-key', response.text)
        self.assertNotIn('speech-key', response.text)

    def test_analysis_requires_model_fields_before_creating_job(self):
        from unittest.mock import patch
        from app.ai.providers import CloudConfig
        from app.core.domain import Asset
        store = self.app.state.store
        store.put('asset', Asset(id='unconfigured', sha256='digest', path='source.mp4', name='source',
            duration_us=10_000_000, width=320, height=240, has_audio=True).model_dump())
        valid = CloudConfig(base_url='https://text.example/v1', planner_model='text-model',
                            asr_base_url='https://speech.example', asr_model='speech-model',
                            oss_endpoint='https://oss-cn-beijing.aliyuncs.com', oss_bucket='clips').model_dump()
        with patch('app.ai.providers.read_secret', return_value='saved-test-key'):
            for name in ('base_url', 'planner_model', 'asr_base_url', 'asr_model'):
                with self.subTest(field=name):
                    saved = self.client.patch('/api/v1/settings/cloud', headers=self.headers,
                                              json={**valid, name: ''})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    response = self.client.post('/api/v1/assets/unconfigured/analyze', headers=self.headers,
                                                json={'product_name': '测试商品'})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn('Base URL 和模型 ID', response.text)
                    self.assertEqual(store.jobs(), [])

    def test_chat_timeout_settings_default_persist_and_validate(self):
        from unittest.mock import patch
        with patch('app.api.read_secret', return_value=None):
            config = self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']
            self.assertEqual(config['chat_timeout_seconds'], 300)
            config['chat_timeout_seconds'] = 600
            self.assertEqual(self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=config).status_code, 200)
            self.assertEqual(self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']['chat_timeout_seconds'], 600)
            for invalid in (0, 29, 901, '300', 300.5):
                config['chat_timeout_seconds'] = invalid
                self.assertEqual(self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=config).status_code, 422)

    def test_secret_previews_and_explicit_reveal(self):
        from unittest.mock import patch
        values = {
            'llm_api_key': 'sk-example-private-1234',
            'asr_api_key': 'sk-speech-private-3456',
            'oss_access_key_id': 'LTAIexample-private-5678',
            'oss_access_key_secret': 'oss-example-private-9012',
        }
        with patch('app.api.read_secret', side_effect=values.get):
            response = self.client.get('/api/v1/settings/cloud', headers=self.headers)
            self.assertEqual(response.json()['secret_previews']['llm_api_key'], 'sk-exa*****1234')
            self.assertEqual(response.json()['secret_previews']['oss_access_key_id'], 'LTAI*****5678')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            for name, value in values.items():
                if value:
                    self.assertNotIn(value, response.text)
                    revealed = self.client.post(f'/api/v1/settings/secrets/{name}/reveal', headers=self.headers)
                    self.assertEqual(revealed.json(), {'value': value})
                    self.assertEqual(revealed.headers['Cache-Control'], 'no-store')
        with patch('app.api.read_secret', return_value=None):
            missing = self.client.post('/api/v1/settings/secrets/oss_access_key_secret/reveal', headers=self.headers)
            self.assertEqual(missing.status_code, 404)

    def test_secret_reveal_requires_auth_and_allowed_name(self):
        from unittest.mock import patch
        path = '/api/v1/settings/secrets/llm_api_key/reveal'
        with patch('app.api.read_secret') as read:
            self.assertEqual(self.client.post(path).status_code, 401)
            self.assertEqual(self.client.post(path, headers={**self.headers, 'Origin': 'https://untrusted.example'}).status_code, 401)
            self.assertEqual(self.client.post('/api/v1/settings/secrets/unknown/reveal', headers=self.headers).status_code, 422)
            read.assert_not_called()

    def test_douyin_cookie_is_saved_in_app_settings_and_only_previewed_by_default(self):
        from unittest.mock import patch
        cookie = "sessionid=private-session; msToken=private-token"
        with patch('app.api.write_secret') as save:
            response = self.client.post('/api/v1/settings/secrets', headers=self.headers,
                                        json={'name':'douyin_cookie','value':cookie})
        self.assertEqual(response.status_code, 200)
        save.assert_not_called()
        self.assertNotIn(cookie, response.text)
        with patch('app.api.read_secret', return_value=None):
            settings = self.client.get('/api/v1/settings/cloud', headers=self.headers)
            self.assertTrue(settings.json()['secrets']['douyin_cookie'])
            self.assertEqual(settings.json()['secret_previews']['douyin_cookie'], 'sess*****oken')
            self.assertNotIn(cookie, settings.text)
            revealed = self.client.post('/api/v1/settings/secrets/douyin_cookie/reveal', headers=self.headers)
            self.assertEqual(revealed.json(), {'value': cookie})

    def test_secret_preview_short_values_never_returns_complete_key(self):
        from app.core.credentials import secret_preview
        self.assertEqual(secret_preview(None), '')
        self.assertEqual(secret_preview(''), '')
        for size in range(1, 14):
            value = 'sk-' + 'a' * size if size > 3 else 'a' * size
            preview = secret_preview(value)
            self.assertIn('*****', preview)
            self.assertNotEqual(preview.replace('*****', ''), value)

    def test_all_saved_credentials_return_preview_and_reload(self):
        from unittest.mock import patch
        from app.core.credentials import secret_preview
        values = {}
        with patch('app.api.write_secret', side_effect=lambda name, value: values.update({name: value})), patch('app.api.read_secret', side_effect=values.get):
            for name, value in {
                'llm_api_key': 'sk-model-test-key-1234',
                'asr_api_key': 'sk-speech-test-key-5678',
                'oss_access_key_id': 'LTAI-test-access-id-9012',
                'oss_access_key_secret': 'oss-test-access-secret-3456',
            }.items():
                response = self.client.post('/api/v1/settings/secrets', headers=self.headers, json={'name': name, 'value': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['preview'], secret_preview(value))
                self.assertNotIn(value, response.text)
            response = self.client.get('/api/v1/settings/cloud', headers=self.headers)
            expected = {name: secret_preview(value) for name, value in values.items()}
            expected['image_generation_api_key'] = ''
            expected['douyin_cookie'] = ''
            self.assertEqual(response.json()['secret_previews'], expected)
            self.assertTrue(all(response.json()['secrets'][name] for name in values))
            self.assertFalse(response.json()['secrets']['douyin_cookie'])


if __name__ == '__main__':
    unittest.main()
