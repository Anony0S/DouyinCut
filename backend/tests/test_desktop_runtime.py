import tempfile
import json
import os
import queue
import socket
import signal
import subprocess
import sys
import threading
import unittest
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
