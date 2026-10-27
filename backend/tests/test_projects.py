import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings
from app.projects.service import (ProjectDraft, delete_plan, delete_project, job_summary,
                          project_detail, project_list, save_draft)
from app.core.storage import Store


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Settings(Path(self.temp.name)), token='test')
        self.store = self.app.state.store
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'X-Session-Token': 'test'}

    def asset(self, identifier):
        self.store.put('asset', {'id': identifier, 'name': f'{identifier}.mp4', 'path': 'local.mp4',
                                'duration_us': 180000000, 'width': 1080, 'height': 1920, 'has_audio': True, 'sha256': identifier})

    def test_server_pagination_search_sort_and_empty_page(self):
        for index in range(45):
            self.asset(f'video-{index:02d}')
        first = project_list(self.store)
        second = project_list(self.store, page=2)
        third = project_list(self.store, page=3)
        self.assertEqual((first['total'], first['pages']), (45, 3))
        self.assertEqual([len(p['items']) for p in (first, second, third)], [20, 20, 5])
        self.assertEqual(len({r['id'] for p in (first, second, third) for r in p['items']}), 45)
        save_draft(self.store, 'video-00', ProjectDraft(product_name='奶黄千层'))
        self.assertEqual(project_list(self.store)['items'][0]['id'], 'video-00')
        found = project_list(self.store, search='奶黄')
        self.assertEqual([r['id'] for r in found['items']], ['video-00'])
        self.assertEqual(project_list(self.store, search='VIDEO-01')['total'], 1)
        self.assertEqual(project_list(self.store, search="%' OR 1=1 --")['total'], 0)
        beyond = project_list(self.store, page=999)
        self.assertEqual(beyond['page'], 3)
        empty = project_list(self.store, page=99, search='不存在')
        self.assertEqual((empty['page'], empty['pages'], empty['total']), (1, 1, 0))

    def test_status_tracks_analysis_preview_failures_and_current_plan_revision(self):
        self.asset('a')
        task = self.store.enqueue('analyze', {'asset_id': 'a', 'product_name': '针织开衫'})
        self.assertEqual(project_list(self.store)['items'][0]['status'], 'processing')
        self.store.control(task['id'], 'pause')
        self.assertEqual(project_list(self.store, status='paused')['total'], 1)
        self.store.control(task['id'], 'resume')
        self.store.claim('worker')
        self.store.finish(task['id'], 'worker', 'failed', error='失败')
        self.assertEqual(project_list(self.store, status='failed')['total'], 1)
        self.assertEqual(project_list(self.store, search='针织')['total'], 1)
        self.store.control(task['id'], 'retry')
        self.store.claim('worker')
        self.store.finish(task['id'], 'worker', 'succeeded', result={'plan_id': 'p'})
        self.store.put('plan', {'id': 'p', 'revision': 1, 'clips': [
            {'source_asset_id': 'a', 'source_start_us': 1000000, 'source_end_us': 9000000}]})
        preview = self.store.enqueue('preview', {'plan_id': 'p', 'revision': 1})
        self.store.claim('worker')
        self.store.finish(preview['id'], 'worker', 'failed', error='预览失败')
        self.assertEqual(project_list(self.store)['items'][0]['status'], 'failed')
        self.store.control(preview['id'], 'retry')
        self.store.claim('worker')
        self.store.put('export', {'id': 'e', 'plan_id': 'p', 'revision': 1, 'format': 'preview', 'path': 'preview'})
        self.store.finish(preview['id'], 'worker', 'succeeded')
        row = project_list(self.store)['items'][0]
        self.assertEqual((row['status'], row['output_duration_us']), ('completed', 8000000))
        self.store.next_revision('plan', 'p', self.store.get('plan', 'p'))
        self.assertEqual(project_list(self.store)['items'][0]['status'], 'pending')

    def test_project_detail_isolated_restores_draft_and_all_plans_beyond_library_limit(self):
        self.asset('a'); self.asset('b')
        for index in range(115):
            self.store.put('plan', {'id': f'p{index}', 'revision': 1, 'clips': [{'source_asset_id': 'a'}]})
        self.store.put('plan', {'id': 'other', 'revision': 1, 'clips': [{'source_asset_id': 'b'}]})
        self.store.put('candidate', {'id': 'ca', 'source_asset_id': 'a'})
        self.store.put('candidate', {'id': 'cb', 'source_asset_id': 'b'})
        self.store.put('export', {'id': 'ea', 'plan_id': 'p0', 'revision': 1})
        self.store.put('export', {'id': 'eb', 'plan_id': 'other', 'revision': 1})
        self.store.enqueue('analyze', {'asset_id': 'a', 'product_name': '已提交资料'})
        self.store.enqueue('analyze', {'asset_id': 'b', 'product_name': '另一个商品'})
        saved = ProjectDraft(product_name='新资料', duration_tolerance_us=8_000_000, instructions='突出面料', target_duration_us=90000000)
        save_draft(self.store, 'a', saved)
        detail = project_detail(self.store, 'a')
        self.assertEqual(detail['draft'], saved.model_dump())
        self.assertEqual(len(detail['plans']), 115)
        self.assertEqual([r['id'] for r in detail['candidates']], ['ca'])
        self.assertEqual([r['id'] for r in detail['artifacts']], ['ea'])
        self.assertTrue(all(j['payload']['asset_id'] == 'a' for j in detail['jobs']))
        self.assertNotIn('asset_id', detail['jobs'][0])
        self.assertEqual(project_detail(self.store, 'b')['draft']['product_name'], '另一个商品')
        with self.assertRaises(KeyError):
            save_draft(self.store, 'missing', saved)

    def test_saved_default_instructions_survive_failure_and_new_app(self):
        from app.ai.roughcut import DEFAULT_INSTRUCTIONS
        from app.projects.service import new_project_draft
        url='/api/v1/settings/editing-defaults'
        self.assertEqual(self.client.put(url, json={'instructions':'新要求'}).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.headers).json()['instructions'], DEFAULT_INSTRUCTIONS)
        self.asset('existing'); self.asset('new')
        save_draft(self.store, 'existing', ProjectDraft(product_name='已编辑', instructions='此项目专用'))
        result=self.client.put(url, headers=self.headers, json={'instructions':'  保留细节，去掉价格  '})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['instructions'], '保留细节，去掉价格')
        job=self.store.enqueue('analyze', {'asset_id':'existing'})
        self.store.claim('test-worker')
        self.store.finish(job['id'], 'test-worker', 'failed', error='模型调用失败')
        self.assertEqual(project_detail(self.store, 'new')['draft']['instructions'], '保留细节，去掉价格')
        self.assertEqual(project_detail(self.store, 'existing')['draft']['instructions'], '此项目专用')
        self.assertEqual(new_project_draft(self.store, product_name='直播导入').instructions, '保留细节，去掉价格')
        app=create_app(Settings(Path(self.temp.name)),token='test')
        with TestClient(app) as client:
            self.assertEqual(client.get(url,headers=self.headers).json()['instructions'],'保留细节，去掉价格')
        self.assertEqual(self.client.put(url,headers=self.headers,json={'instructions':'x'*4001}).status_code,422)
        reset=self.client.put(url,headers=self.headers,json={'instructions':'  '})
        self.assertEqual(reset.json()['instructions'],DEFAULT_INSTRUCTIONS)

    def test_tolerance_draft_persists_and_invalid_fields_are_rejected(self):
        self.asset('a')
        draft=ProjectDraft(product_name='测试',duration_tolerance_us=30_000_000).model_dump()
        url='/api/v1/projects/a'
        self.assertEqual(self.client.patch(url,headers=self.headers,json=draft).status_code,200)
        self.assertEqual(self.client.get(url,headers=self.headers).json()['draft']['duration_tolerance_us'],30_000_000)
        for value in (-1,30_000_001,'15000000',False,1.5):
            self.assertEqual(self.client.patch(url,headers=self.headers,json={**draft,'duration_tolerance_us':value}).status_code,422)
        self.assertEqual(self.client.patch(url,headers=self.headers,json={**draft,'product_info':'removed'}).status_code,422)

    def test_auth_validation_and_saved_draft_survive_restart(self):
        self.asset('a')
        self.assertEqual(self.client.get('/api/v1/projects').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/projects/a').status_code, 401)
        self.assertEqual(self.client.patch('/api/v1/projects/a', json={}).status_code, 401)
        for query in ('page=0', 'page_size=101', 'status=bad'):
            self.assertEqual(self.client.get('/api/v1/projects?' + query, headers=self.headers).status_code, 422)
        saved = self.client.patch('/api/v1/projects/a', headers=self.headers,
                                  json={'product_name': '测试商品', 'instructions': '去掉价格'})
        self.assertEqual(saved.status_code, 200, saved.text)
        restarted = Store(self.store.path)
        self.assertEqual(project_detail(restarted, 'a')['draft']['product_name'], '测试商品')
        detail = self.client.get('/api/v1/projects/a', headers=self.headers)
        self.assertEqual(detail.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get('/api/v1/projects/missing', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.patch('/api/v1/projects/a', headers=self.headers,
                                          json={'asr_model': 'unexpected'}).status_code, 422)

    def test_project_active_jobs_not_hidden_by_many_newer_jobs(self):
        self.asset('a')
        active = self.store.enqueue('analyze', {'asset_id': 'a', 'product_name': '商品'})
        for index in range(105):
            self.store.enqueue('import', {'path': f'{index}.mp4'})
        detail = project_detail(self.store, 'a')
        self.assertEqual(detail['jobs'][0]['id'], active['id'])
        self.assertEqual(job_summary(self.store)['active'], 106)
        summary = self.client.get('/api/v1/jobs-summary', headers=self.headers)
        self.assertEqual(summary.json()['active'], 106)
        self.assertNotIn('payload', summary.text)

    def test_delete_project_removes_only_managed_local_data_and_preserves_shared_oss_cache(self):
        imports = Path(self.temp.name) / 'recordings' / 'imports'
        imports.mkdir(parents=True)
        imported = imports / 'copy.mp4'
        imported.write_bytes(b'app copy')
        original = Path(self.temp.name) / 'original.mp4'
        original.write_bytes(b'user video')
        self.store.put('asset', {'id':'a','name':'a.mp4','path':str(imported),'sha256':'shared-hash',
                                 'duration_us':10,'width':2,'height':2,'has_audio':True})
        self.store.put('asset', {'id':'b','name':'b.mp4','path':str(original),'sha256':'shared-hash',
                                 'duration_us':10,'width':2,'height':2,'has_audio':True})
        self.store.put('project', {'id':'a','draft':{'product_name':'商品'}})
        self.store.put('plan', {'id':'p','revision':1,'product_id':'product','clips':[
            {'source_asset_id':'a','candidate_id':'c','source_start_us':0,'source_end_us':5}]})
        self.store.put('product', {'id':'product','name':'商品'})
        self.store.put('candidate', {'id':'c','source_asset_id':'a','product_id':'product'})
        self.store.put('transcript', {'id':'t','segment':{'source_asset_id':'a','text':'口播'}})
        output = Path(self.temp.name) / 'exports' / 'out'
        output.mkdir(parents=True)
        self.store.put('export', {'id':'e','plan_id':'p','path':str(output)})
        self.store.enqueue('import', {'path':str(imported)})
        import_job = self.store.claim('worker')
        self.store.finish(import_job['id'], 'worker', 'succeeded', result={'asset_id':'a'})
        self.store.cache('qwen-audio-v1:shared-hash:provider:model:0:10', {'response':'cached'})
        result = delete_project(self.store, 'a', Path(self.temp.name))
        self.assertEqual(result['deleted_plans'], 1)
        self.assertFalse(imported.exists())
        self.assertFalse(output.exists())
        self.assertTrue(original.exists(), 'A source outside the managed imports directory is user-owned')
        self.assertEqual(self.store.get('asset','b')['id'], 'b')
        self.assertEqual(self.store.cached('qwen-audio-v1:shared-hash:provider:model:0:10'), {'response':'cached'})
        self.assertEqual(self.store.jobs(), [])
        for kind, identifier in [('asset','a'),('plan','p'),('product','product'),('candidate','c')]:
            with self.assertRaises(KeyError): self.store.get(kind, identifier)
        self.assertEqual(self.store.list('transcript'), [])

    def test_delete_plan_keeps_source_transcript_and_other_plans(self):
        self.asset('a')
        for plan_id, product_id, candidate_id in [('p1','prod1','c1'),('p2','prod2','c2')]:
            self.store.put('plan', {'id':plan_id,'revision':1,'product_id':product_id,'clips':[
                {'source_asset_id':'a','candidate_id':candidate_id,'source_start_us':0,'source_end_us':5}]})
            self.store.put('product', {'id':product_id,'name':plan_id})
            self.store.put('candidate', {'id':candidate_id,'source_asset_id':'a','product_id':product_id})
        self.store.put('transcript', {'id':'t','segment':{'source_asset_id':'a','text':'口播'}})
        output = Path(self.temp.name) / 'previews' / 'preview'
        output.mkdir(parents=True)
        self.store.put('export', {'id':'e1','plan_id':'p1','path':str(output)})
        delete_plan(self.store, 'p1', Path(self.temp.name))
        with self.assertRaises(KeyError): self.store.get('plan','p1')
        self.assertEqual(self.store.get('plan','p2')['id'],'p2')
        self.assertEqual(self.store.get('product','prod2')['id'],'prod2')
        with self.assertRaises(KeyError): self.store.get('product','prod1')
        self.assertEqual(len(self.store.list('transcript')),1)
        self.assertFalse(output.exists())

    def test_delete_plan_rejects_active_jianying_draft_generation(self):
        self.asset('a')
        self.store.put('plan', {'id':'p','revision':1,'product_id':'product','clips':[
            {'source_asset_id':'a','candidate_id':'c','source_start_us':0,'source_end_us':5}]})
        self.store.put('product', {'id':'product','name':'p'})
        self.store.put('candidate', {'id':'c','source_asset_id':'a','product_id':'product'})
        self.store.enqueue('jianying', {'plan_id':'p','revision':1})
        with self.assertRaisesRegex(ValueError, '正在处理'):
            delete_plan(self.store, 'p', Path(self.temp.name))
        self.assertEqual(self.store.get('plan','p')['id'], 'p')

    def test_delete_project_rejects_active_jobs_without_deleting_asset(self):
        self.asset('a')
        self.store.enqueue('analyze', {'asset_id':'a'})
        response = self.client.delete('/api/v1/projects/a', headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.get('asset','a')['id'],'a')
        self.assertEqual(self.client.delete('/api/v1/projects/missing', headers=self.headers).status_code, 404)


if __name__ == '__main__':
    unittest.main()
