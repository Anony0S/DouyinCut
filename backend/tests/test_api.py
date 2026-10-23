import importlib.util
import tempfile
import unittest
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
            self.assertEqual(config['asr_model'], 'qwen-audio-3.1-asr-flash')
            config['asr_model'] = ' custom-file-asr-v1 '
            response = self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=config)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['asr_model'], 'custom-file-asr-v1')
            self.assertEqual(self.client.get('/api/v1/settings/cloud', headers=self.headers).json()['config']['asr_model'], 'custom-file-asr-v1')
            for invalid in ('', '   ', 'invalid model', 'https://model.example', 'x' * 101):
                config['asr_model'] = invalid
                self.assertEqual(self.client.patch('/api/v1/settings/cloud', headers=self.headers, json=config).status_code, 422)

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
                'oss_access_key_id': 'LTAI-test-access-id-9012',
                'oss_access_key_secret': 'oss-test-access-secret-3456',
            }.items():
                response = self.client.post('/api/v1/settings/secrets', headers=self.headers, json={'name': name, 'value': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['preview'], secret_preview(value))
                self.assertNotIn(value, response.text)
            response = self.client.get('/api/v1/settings/cloud', headers=self.headers)
            expected = {name: secret_preview(value) for name, value in values.items()}
            expected['douyin_cookie'] = ''
            self.assertEqual(response.json()['secret_previews'], expected)
            self.assertTrue(all(response.json()['secrets'][name] for name in values))
            self.assertFalse(response.json()['secrets']['douyin_cookie'])


if __name__ == '__main__':
    unittest.main()
