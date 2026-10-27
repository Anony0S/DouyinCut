import tempfile
import json
import os
import queue
import socket
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import httpx

from app.core.config import Settings
from app.core.domain import Asset
from app.core.storage import Store
from scripts.runtime_process import assert_process_exited, stop_owned_process


def read_launcher_message(process):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(process.stdout.readline()), daemon=True).start()
    line = result.get(timeout=20)
    assert line, 'Launcher exited without a startup descriptor'
    return json.loads(line)


class DesktopRuntimeTests(unittest.TestCase):
    def test_workspace_recovers_after_process_dies_before_identity_publication(self):
        from app.work_directory import WorkDirectoryRegistry

        for interrupted_file in ('.douyincut-initializing.json', '.douyincut-workspace.json'):
            with self.subTest(interrupted_file=interrupted_file), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                work = root / 'work'
                work.mkdir()
                config = root / 'user.json'
                script = '''
import os, sys
from pathlib import Path
from app.work_directory import WorkDirectoryRegistry
original = os.replace
def interrupted(source, target):
    if Path(target).name == sys.argv[3]:
        os._exit(27)
    return original(source, target)
os.replace = interrupted
WorkDirectoryRegistry(sys.argv[1]).select(sys.argv[2])
'''
                env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
                process = subprocess.run([sys.executable, '-c', script, str(config), str(work), interrupted_file],
                                         env=env, capture_output=True, timeout=15)
                self.assertEqual(process.returncode, 27, process.stderr)
                self.assertFalse(config.exists())
                registry = WorkDirectoryRegistry(config)
                self.assertEqual(registry.select(work)['state'], 'ready')
                self.assertEqual(registry.inspect()['state'], 'ready')

    def test_workspace_recovers_when_initialization_journal_write_is_interrupted(self):
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / '工作 数据'
            work.mkdir()
            registry = WorkDirectoryRegistry(root / 'user' / 'workspace.json')
            original_replace = os.replace

            def fail_journal(source, target):
                if Path(target) == work / '.douyincut-initializing.json':
                    raise OSError('journal disk full')
                return original_replace(source, target)

            with patch('app.work_directory.os.replace', side_effect=fail_journal):
                with self.assertRaisesRegex(OSError, 'journal disk full'):
                    registry.select(work)
            self.assertFalse(registry.config_path.exists())
            self.assertFalse(Settings(work).database.exists())
            self.assertEqual(registry.select(work)['state'], 'ready')
            self.assertEqual(registry.inspect()['state'], 'ready')
            self.assertFalse(any(work.glob('.douyincut-init-*')))
            self.assertFalse((work / '.douyincut-initializing.json').exists())

    def test_workspace_rejects_application_directory_children(self):
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application = root / 'application'
            selected = application / 'data'
            selected.mkdir(parents=True)
            registry = WorkDirectoryRegistry(root / 'config.json', application_dir=application)
            with self.assertRaisesRegex(RuntimeError, '程序文件分开'):
                registry.select(selected)
            self.assertEqual(list(selected.iterdir()), [])
            self.assertFalse(registry.config_path.exists())

    def test_workspace_initialization_publication_failure_can_retry_without_data_loss(self):
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'work'
            work.mkdir()
            registry = WorkDirectoryRegistry(root / 'config.json')
            original_rename = Path.rename

            def fail_publication(source, target):
                if Path(target) == work / 'analysis':
                    raise PermissionError('publication denied')
                return original_rename(source, target)

            with patch.object(Path, 'rename', fail_publication):
                with self.assertRaisesRegex(PermissionError, 'publication denied'):
                    registry.select(work)
            self.assertFalse(registry.config_path.exists())
            self.assertEqual(registry.select(work)['state'], 'ready')
            self.assertTrue(Settings(work).database.is_file())

    def test_workspace_rejects_missing_job_progress_before_starting_roles(self):
        from app.desktop_runtime import DesktopSession
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'work'
            work.mkdir()
            registry = WorkDirectoryRegistry(root / 'config.json')
            registry.select(work)
            connection = sqlite3.connect(Settings(work).database)
            try:
                connection.execute('ALTER TABLE jobs DROP COLUMN progress')
                connection.commit()
            finally:
                connection.close()
            before = Settings(work).database.read_bytes()
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('fixture')
            session = DesktopSession(Settings(work), frontend, workspace_config=registry.config_path)
            try:
                with self.assertRaisesRegex(RuntimeError, 'jobs'):
                    session.start()
                self.assertEqual(session.status(), {})
            finally:
                session.close()
            self.assertEqual(Settings(work).database.read_bytes(), before)

    def test_workspace_rejects_unknown_content_and_preserves_data_on_locator_write_failure(self):
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'work'
            work.mkdir()
            source = work / '用户原始视频.mp4'
            source.write_bytes(b'original video')
            config = root / 'user' / 'workspace.json'
            registry = WorkDirectoryRegistry(config)
            with self.assertRaisesRegex(RuntimeError, '包含文件'):
                registry.select(work)
            self.assertEqual(source.read_bytes(), b'original video')
            self.assertFalse(Settings(work).database.exists())
            empty = root / 'empty'
            empty.mkdir()
            with patch('app.work_directory.os.replace', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    registry.select(empty)
            self.assertEqual(list(empty.iterdir()), [])
            registry.select(empty)
            database = Settings(empty).database
            before = database.read_bytes()
            config.unlink()
            with patch('app.work_directory.os.replace', side_effect=PermissionError('access denied')):
                with self.assertRaisesRegex(PermissionError, 'access denied'):
                    registry.select(empty)
            self.assertEqual(database.read_bytes(), before)
            self.assertFalse(config.exists())
            self.assertEqual(registry.select(empty)['state'], 'ready')

    def test_workspace_identity_and_all_current_tables_are_required_before_startup(self):
        from app.desktop_runtime import DesktopSession
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'work'
            work.mkdir()
            registry = WorkDirectoryRegistry(root / 'user.json')
            registry.select(work)
            database = Settings(work).database
            connection = sqlite3.connect(database)
            try:
                connection.execute('DROP TABLE live_product_events')
                connection.commit()
            finally:
                connection.close()
            before = database.read_bytes()
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('fixture')
            session = DesktopSession(Settings(work), frontend, workspace_config=registry.config_path)
            try:
                with self.assertRaisesRegex(RuntimeError, 'live_product_events'):
                    session.start()
                self.assertEqual(session.status(), {})
            finally:
                session.close()
            self.assertEqual(database.read_bytes(), before)

    def test_workspace_preflight_rejects_moved_data_and_recovers_original_location(self):
        from app.desktop_runtime import DesktopSession
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'original'
            work.mkdir()
            config = root / 'user' / 'workspace.json'
            registry = WorkDirectoryRegistry(config)
            registry.select(work)
            database = Settings(work).database
            before = database.read_bytes()
            moved = root / 'moved'
            work.rename(moved)
            self.assertEqual(registry.inspect()['state'], 'repair_required')
            self.assertFalse(work.exists())
            config.unlink()
            with self.assertRaisesRegex(RuntimeError, '搬迁'):
                registry.select(moved)
            self.assertFalse(config.exists())
            self.assertEqual(Settings(moved).database.read_bytes(), before)
            moved.rename(work)
            self.assertEqual(registry.select(work)['state'], 'ready')
            with patch('app.work_directory.tempfile.TemporaryFile', side_effect=PermissionError('denied')):
                state = registry.inspect()
                self.assertEqual(state['state'], 'repair_required')
                self.assertIn('权限', state['message'])
                frontend = root / 'frontend'
                frontend.mkdir()
                (frontend / 'index.html').write_text('fixture')
                session = DesktopSession(Settings(work), frontend, workspace_config=config)
                try:
                    with self.assertRaisesRegex(RuntimeError, '权限'):
                        session.start()
                    self.assertEqual(session.status(), {})
                finally:
                    session.close()
            self.assertEqual(database.read_bytes(), before)
            self.assertEqual(registry.inspect()['state'], 'ready')

    def test_workspace_control_protocol_does_not_start_background_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / '工作 目录'
            work.mkdir()
            config = root / 'user' / 'workspace.json'
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
            base = [sys.executable, '-m', 'app.desktop_entry', '--workspace-config', str(config)]
            denied = subprocess.run(base + ['--host-controlled', '--workspace-action', 'select', '--work-dir', str(work)],
                                    input='', env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(denied.returncode, 1)
            self.assertEqual(list(work.iterdir()), [])
            self.assertFalse(config.exists())
            inspect = subprocess.run(base + ['--workspace-action', 'inspect'], env=env,
                                     capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(inspect.returncode, 0, inspect.stderr)
            self.assertEqual(json.loads(inspect.stdout)['state'], 'selection_required')
            self.assertFalse(config.exists())
            self.assertFalse(Settings(work).database.exists())
            select = subprocess.run(base + ['--workspace-action', 'select', '--work-dir', str(work)],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(select.returncode, 0, select.stderr)
            self.assertEqual(json.loads(select.stdout)['state'], 'ready')
            Settings(work).database.unlink()
            inspect = subprocess.run(base + ['--workspace-action', 'inspect'], env=env,
                                     capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(inspect.returncode, 0)
            self.assertEqual(json.loads(inspect.stdout)['state'], 'repair_required')
            self.assertFalse(Settings(work).database.exists())

    def test_registered_work_directory_survives_reinstall_and_rejects_missing_data(self):
        from app.desktop_runtime import DesktopSession
        from app.work_directory import WorkDirectoryRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / '工作 数据'
            work.mkdir()
            config = root / 'user-config' / 'workspace.json'
            registry = WorkDirectoryRegistry(config)
            self.assertEqual(registry.inspect()['state'], 'selection_required')
            self.assertFalse(config.exists())
            self.assertEqual(list(work.iterdir()), [])
            registry.select(work)
            settings = Settings(work)
            Store(settings.database).put('asset', Asset(
                id='registered-project', path=str(work / 'source.mp4'), name='重新关联.mp4',
                sha256='fixture', duration_us=1_000_000, width=160, height=120, has_audio=True,
            ).model_dump())
            config.unlink()  # Reinstallation loses only the independent locator.
            self.assertEqual(registry.select(work)['state'], 'ready')
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>fixture</html>')
            session = DesktopSession(settings, frontend, workspace_config=config)
            try:
                info = session.start()
                with httpx.Client(base_url=info.url, trust_env=False) as client:
                    projects = client.get('/api/v1/projects', headers={'X-Session-Token': info.token})
                    self.assertEqual(projects.json()['items'][0]['name'], '重新关联.mp4')
            finally:
                session.close()
            settings.database.unlink()
            rejected = DesktopSession(settings, frontend, workspace_config=config)
            with self.assertRaisesRegex(RuntimeError, '数据库'):
                rejected.start()
            self.assertEqual(rejected.status(), {})
            self.assertFalse(settings.database.exists())
            self.assertEqual(registry.inspect()['state'], 'repair_required')

    def test_critical_role_failure_closes_other_roles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>fixture</html>', encoding='utf-8')
            work = root / 'work'
            work.mkdir()
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
            process = subprocess.Popen([
                sys.executable, '-m', 'app.desktop_entry', '--work-dir', str(work),
                '--frontend-dir', str(frontend),
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', env=env, start_new_session=os.name != 'nt')
            try:
                info = read_launcher_message(process)
                os.kill(info['roles']['worker']['pid'], signal.SIGTERM)
                process.wait(timeout=15)
                self.assertEqual(process.returncode, 1)
                failure = read_launcher_message(process)
                self.assertEqual(failure['event'], 'error')
                self.assertIn('worker', failure['message'])
                self.assertTrue(all(not role['alive'] for role in failure['roles'].values()))
                self.assertEqual(failure['roles']['api']['exitcode'], 0)
                self.assertEqual(failure['roles']['live_monitor']['exitcode'], 0)
                for role in failure['roles'].values():
                    assert_process_exited(role['pid'])
            finally:
                stop_owned_process(process)

    def test_startup_timeout_cleans_owned_roles_and_restart_rotates_token(self):
        from app.desktop_runtime import DesktopSession

        with tempfile.TemporaryDirectory() as temporary, socket.socket() as occupied:
            root = Path(temporary)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>fixture</html>', encoding='utf-8')
            work = root / 'work'
            work.mkdir()
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            settings = Settings(work, port=occupied.getsockname()[1])
            failed = DesktopSession(settings, frontend, startup_timeout=0)
            with self.assertRaisesRegex(RuntimeError, '启动超时'):
                failed.start()
            self.assertEqual(set(failed.status()), {'api', 'worker', 'live_monitor'})
            self.assertTrue(all(not role['alive'] for role in failed.status().values()))
            failed.close()
            session = DesktopSession(settings, frontend)
            try:
                first = session.start()
                self.assertNotEqual(first.url, f'http://127.0.0.1:{settings.port}')
                session.close()
                second = session.start()
                self.assertNotEqual(first.token, second.token)
                with httpx.Client(base_url=second.url, trust_env=False) as client:
                    self.assertEqual(client.get('/api/v1/projects', headers={'X-Session-Token': first.token}).status_code, 401)
                    self.assertEqual(client.get('/api/v1/projects', headers={'X-Session-Token': second.token}).status_code, 200)
            finally:
                session.close()

    def test_missing_resources_and_missing_work_directory_do_not_create_database(self):
        from app.desktop_runtime import DesktopSession

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(root / 'unavailable-work')
            frontend = root / 'frontend'
            session = DesktopSession(settings, frontend)
            with self.assertRaisesRegex(RuntimeError, '缺少工作台资源'):
                session.start()
            self.assertFalse(settings.data_dir.exists())
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>fixture</html>', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '工作目录不可用'):
                session.start()
            self.assertEqual(session.status(), {})
            self.assertFalse(settings.database.exists())

    def test_launcher_uses_private_pipe_and_cleans_up_on_input_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>launcher fixture</html>', encoding='utf-8')
            work = root / 'work'
            work.mkdir()
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
            process = subprocess.Popen([
                sys.executable, '-m', 'app.desktop_entry', '--work-dir', str(work),
                '--frontend-dir', str(frontend), '--host-controlled',
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', env=env, start_new_session=os.name != 'nt')
            try:
                waiting = read_launcher_message(process)
                self.assertEqual(waiting['event'], 'awaiting_host')
                self.assertFalse((work / 'database').exists(), 'No database or roles before native process ownership is established')
                process.stdin.write('start\n')
                process.stdin.flush()
                info = read_launcher_message(process)
                self.assertEqual(info['event'], 'ready')
                self.assertNotIn(info['token'], info['url'])
                with httpx.Client(base_url=info['url'], trust_env=False) as client:
                    self.assertEqual(client.get('/api/v1/projects', headers={'X-Session-Token': info['token']}).status_code, 200)
                process.stdin.close()
                process.wait(timeout=15)
                self.assertEqual(process.returncode, 0, process.stderr.read())
                stopped = read_launcher_message(process)
                self.assertEqual(stopped['event'], 'stopped')
                self.assertTrue(all(not role['alive'] and role['exitcode'] == 0 for role in stopped['roles'].values()))
                for role in stopped['roles'].values():
                    assert_process_exited(role['pid'])
            finally:
                stop_owned_process(process)

    def test_session_serves_projects_with_private_auth_and_exits_idle_roles(self):
        from app.desktop_runtime import DesktopSession

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>production workbench</html>', encoding='utf-8')
            settings = Settings(root / 'work')
            settings.prepare()
            Store(settings.database).put('asset', Asset(
                id='desktop-project', path='source.mp4', name='桌面测试视频.mp4', sha256='test',
                duration_us=1_000_000, width=160, height=120, has_audio=True,
            ).model_dump())
            session = DesktopSession(settings, frontend)
            try:
                info = session.start()
                self.assertTrue(info.url.startswith('http://127.0.0.1:'))
                self.assertNotIn(info.token, info.url)
                self.assertGreaterEqual(len(info.token), 32)
                self.assertEqual(set(session.status()), {'api', 'worker', 'live_monitor'})
                self.assertTrue(all(role['ready'] for role in session.status().values()))
                with httpx.Client(base_url=info.url, trust_env=False) as client:
                    response = client.get('/')
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('production workbench', response.text)
                    self.assertNotIn(info.token, response.text)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                    self.assertEqual(client.get('/projects/desktop-project').status_code, 200)
                    self.assertEqual(client.get('/api/v1/does-not-exist').status_code, 401)
                    self.assertEqual(client.get('/assets/missing.js').status_code, 404)
                    self.assertEqual(client.get('/api/v1/projects').status_code, 401)
                    headers = {'X-Session-Token': info.token}
                    projects = client.get('/api/v1/projects', headers=headers)
                    self.assertEqual(projects.status_code, 200, projects.text)
                    self.assertEqual(projects.json()['items'][0]['name'], '桌面测试视频.mp4')
                    foreign = client.get('/api/v1/projects', headers={**headers, 'Origin': 'https://untrusted.example'})
                    self.assertEqual(foreign.status_code, 401)
                    development_origin = client.get('/api/v1/projects', headers={**headers, 'Origin': 'http://127.0.0.1:5173'})
                    self.assertEqual(development_origin.status_code, 401)
                    self.assertEqual(client.post('/api/v1/auth', headers=headers).status_code, 200)
                    self.assertEqual(client.get('/api/v1/projects').status_code, 200)
            finally:
                session.close()
            self.assertTrue(all(role['exitcode'] == 0 for role in session.status().values()))
            self.assertTrue(all(not role['alive'] for role in session.status().values()))

    def test_local_jobs_stay_paused_across_runtime_restart_until_authenticated_resume(self):
        from fastapi.testclient import TestClient
        from app.api import create_app
        from app.core.storage import Store
        from app.desktop_runtime import DesktopSession

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'index.html').write_text('<html>exit recovery</html>', encoding='utf-8')
            settings = Settings(root / 'work')
            settings.prepare()
            store = Store(settings.database)
            queued = store.enqueue('import', {'path': str(root / 'queued.mp4')})
            running = store.enqueue('export', {'plan_id': 'not-run-before-resume'})
            store.control(queued['id'], 'pause')
            store.control(running['id'], 'pause')

            first = DesktopSession(settings, frontend)
            first.start()
            first.close()

            self.assertEqual(store.job(queued['id'])['state'], 'paused')
            self.assertEqual(store.job(running['id'])['state'], 'paused')
            self.assertTrue(all(role['exitcode'] == 0 for role in first.status().values()))

            second = DesktopSession(settings, frontend)
            try:
                info = second.start()
                self.assertIsNone(store.claim('must-not-auto-resume'))
                app = create_app(settings, info.token, frontend_dir=frontend)
                with TestClient(app, base_url=info.url) as client:
                    resumed = client.post(f"/api/v1/jobs/{running['id']}/resume",
                                          headers={'X-Session-Token': info.token})
                    self.assertEqual(resumed.status_code, 200, resumed.text)
                    self.assertEqual(resumed.json()['state'], 'queued')
            finally:
                second.close()
