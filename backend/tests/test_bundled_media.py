import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class BundledMediaLauncherTests(unittest.TestCase):
    def test_frozen_launcher_refuses_missing_tools_before_creating_work_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / '程序 资源'
            resources.mkdir()
            frontend = resources / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>fixture</html>')
            work = root / '工作 数据'
            work.mkdir()
            bootstrap = '''
import runpy, sys
sys._MEIPASS = sys.argv.pop(1)
sys.frozen = True
runpy.run_module('app.desktop_entry', run_name='__main__')
'''
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
                   'DOUYINCUT_FFMPEG': sys.executable, 'DOUYINCUT_FFPROBE': sys.executable}
            process = subprocess.run(
                [sys.executable, '-c', bootstrap, str(resources), '--work-dir', str(work)],
                input='', capture_output=True, text=True, encoding='utf-8', env=env, timeout=20,
            )
            self.assertEqual(process.returncode, 1, process.stderr)
            event = json.loads(process.stdout.strip().splitlines()[-1])
            self.assertEqual(event['event'], 'error')
            self.assertIn('随包媒体工具', event['message'])
            self.assertEqual(event['roles'], {})
            self.assertEqual(list(work.iterdir()), [])


class BundledMediaExportTests(unittest.TestCase):
    def test_authenticated_upload_explains_unwritable_import_directory(self):
        from fastapi.testclient import TestClient
        from app.api import create_app
        from app.core.config import Settings

        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary) / '工作 数据')
            app = create_app(settings, 'fixture-token')
            mkdir = Path.mkdir

            def deny_imports(path, *args, **kwargs):
                if path == settings.data_dir / 'recordings' / 'imports':
                    raise PermissionError('denied')
                return mkdir(path, *args, **kwargs)

            with TestClient(app, raise_server_exceptions=False) as client:
                with patch.object(Path, 'mkdir', deny_imports):
                    failed = client.post('/api/v1/assets/upload?filename=中文.mp4', content=b'video',
                                         headers={'X-Session-Token': 'fixture-token'})
                self.assertEqual(failed.status_code, 503, failed.text)
                self.assertIn('视频导入写入失败', failed.json()['detail'])
                self.assertIn(str(settings.data_dir / 'recordings' / 'imports'), failed.json()['detail'])
                self.assertEqual(client.get('/api/v1/jobs', headers={'X-Session-Token': 'fixture-token'}).json(), [])

    @unittest.skipUnless((Path(__file__).resolve().parents[2] /
                         '.runtime/windows-packaging/media/manifest.json').is_file(),
                        '先运行 prepare-windows-media.py 准备锁定工具')
    def test_failed_publication_preserves_source_and_completed_export(self):
        from app.bundled_media import bundled_settings
        from app.core.domain import Candidate, Product, Transcript
        from app.media.editing import build_plan
        from app.media.exporting import export_plan
        from app.media.processing import import_asset, run_process

        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / '中文 空格目录'
            root.mkdir()
            settings = bundled_settings(root / 'work', repo / '.runtime/windows-packaging')
            settings.prepare()
            source = root / '用户视频.mp4'
            run_process([settings.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                         'color=c=red:s=160x120:r=30:d=1', '-c:v', 'libx264', str(source)])
            before = source.read_bytes()
            asset = import_asset(source, settings)
            product = Product(name='发布失败验收', confirmed=True)
            candidate = Candidate(product_id=product.id, source_asset_id=asset.id,
                                  start_us=0, end_us=1_000_000)
            plan = build_plan(product, [candidate], {asset.id: asset}, [
                Transcript(source_asset_id=asset.id, start_us=0, end_us=1_000_000, text='保留字幕。')],
                target_us=1_000_000)
            previous = export_plan(plan, {asset.id: asset}, settings, 'completed', package=False)
            previous_video = Path(previous['video_path']).read_bytes()
            rename = Path.rename

            def fail_publication(path, target):
                if Path(target).name == 'export_permission-denied':
                    raise PermissionError('输出目录写入被拒绝')
                return rename(path, target)

            with patch.object(Path, 'rename', fail_publication):
                with self.assertRaisesRegex(PermissionError, '输出目录写入被拒绝'):
                    export_plan(plan, {asset.id: asset}, settings, 'permission-denied', package=False)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(Path(previous['video_path']).read_bytes(), previous_video)
            self.assertEqual(list((settings.data_dir / 'exports').iterdir()), [Path(previous['path'])])

            from app.core.storage import Store
            from app.worker import Worker
            store = Store(settings.database)
            for kind, body in [('asset', asset.model_dump()), ('product', product.model_dump()),
                               ('candidate', candidate.model_dump()),
                               ('plan', plan.model_copy(update={'status': 'approved'}).model_dump())]:
                store.put(kind, body)
            job = store.enqueue('export', {'plan_id': plan.id, 'revision': plan.revision, 'format': 'mp4'})

            def deny_output(path, *args, **kwargs):
                if path.name.startswith('.export_' + job['id']):
                    raise PermissionError('output denied')
                return mkdir(path, *args, **kwargs)

            mkdir = Path.mkdir
            with patch.object(Path, 'mkdir', deny_output):
                Worker(settings).run_once()
            failed = store.job(job['id'])
            self.assertEqual(failed['state'], 'failed')
            self.assertIn('媒体输出写入失败', failed['error'])
            self.assertIn(str(settings.data_dir / 'exports'), failed['error'])
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(Path(previous['video_path']).read_bytes(), previous_video)
