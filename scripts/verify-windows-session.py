"""Exercise a relocated frozen runtime with development paths removed."""

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from runtime_process import assert_process_exited, stop_owned_process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / 'backend'))
    from app.core.config import Settings
    from app.core.domain import Asset
    from app.core.storage import Store
    from app.work_directory import WorkDirectoryRegistry

    with tempfile.TemporaryDirectory(prefix='DouyinCut 中文 空格 ') as temporary:
        root = Path(temporary)
        copied = root / '应用 runtime'
        shutil.copytree(args.runtime.resolve().parent, copied)
        executable = copied / args.runtime.name
        work = root / '工作 data'
        settings = Settings(work)
        work.mkdir()
        config = root / 'user config' / 'workspace.json'
        WorkDirectoryRegistry(config).select(work)
        Store(settings.database).put('asset', Asset(
            id='frozen-fixture', path=str(work / 'source.mp4'), name='冻结会话项目.mp4',
            sha256='fixture', duration_us=1_000_000, width=160, height=120, has_audio=True,
        ).model_dump())
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(('PYTHON', 'VITE_', 'DOUYINCUT_', 'UV_', 'VIRTUAL_ENV'))}
        system_root = os.environ['SystemRoot']
        env['PATH'] = os.pathsep.join([str(Path(system_root) / 'System32'), system_root])
        messages = queue.Queue()
        with (root / 'stderr.log').open('w+', encoding='utf-8') as errors:
            process = subprocess.Popen([str(executable), '--work-dir', str(work), '--host-controlled',
                '--workspace-config', str(config)], cwd=root, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, encoding='utf-8')

            def read_messages():
                for line in process.stdout:
                    messages.put(json.loads(line))
                messages.put({'event': 'eof'})

            threading.Thread(target=read_messages, daemon=True).start()
            try:
                waiting = messages.get(timeout=20)
                assert waiting['event'] == 'awaiting_host', waiting
                process.stdin.write('start\n')
                process.stdin.flush()
                info = messages.get(timeout=45)
                if info['event'] != 'ready':
                    errors.seek(0)
                    raise AssertionError(f'Frozen runtime failed: {info}; {errors.read()}')
                token = info['token']
                assert len(token) >= 32 and token not in info['url']
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

                def get(path, headers=None):
                    request = urllib.request.Request(info['url'] + path, headers=headers or {})
                    try:
                        return opener.open(request, timeout=5)
                    except urllib.error.HTTPError as error:
                        return error

                assert get('/api/v1/projects').status == 401
                assert get('/api/v1/projects', {'X-Session-Token': token, 'Origin': 'https://untrusted.example'}).status == 401
                response = get('/api/v1/projects', {'X-Session-Token': token})
                assert response.status == 200
                assert json.load(response)['items'][0]['name'] == '冻结会话项目.mp4'
                diagnostics = json.load(get('/api/v1/diagnostics', {'X-Session-Token': token}))
                assert diagnostics['credentials']['available'], diagnostics['credentials']
                assert diagnostics['credentials']['backend'].startswith('keyring.backends.Windows.'), diagnostics['credentials']
                page = get('/')
                assert page.status == 200 and 'Content-Security-Policy' in page.headers
                assert token.encode() not in page.read()
                assert get('/projects/frozen-fixture').status == 200
                assert get('/assets/missing.js').status == 404
                process.stdin.write('shutdown\n')
                process.stdin.flush()
                process.wait(timeout=15)
                assert process.returncode == 0
                stopped = messages.get(timeout=5)
                assert stopped['event'] == 'stopped'
                assert set(stopped['roles']) == {'api', 'worker', 'live_monitor'}
                for role in stopped['roles'].values():
                    assert role['exitcode'] == 0 and not role['alive']
                    assert_process_exited(role['pid'])
                print(json.dumps({'result': 'passed', 'relocated': True, 'development_path_removed': True,
                    'chinese_and_space_paths': True, 'authenticated_project_list': True,
                    'roles_exited': list(stopped['roles']),
                    'credential_store': diagnostics['credentials']}, ensure_ascii=False))
            finally:
                stop_owned_process(process)
        settings.database.unlink()
        inspection = subprocess.run([str(executable), '--workspace-action', 'inspect',
            '--workspace-config', str(config)], cwd=root, env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=20, check=True)
        assert json.loads(inspection.stdout)['state'] == 'repair_required'
        rejected = subprocess.run([str(executable), '--work-dir', str(work), '--workspace-config', str(config)],
            cwd=root, env=env, input='', capture_output=True, text=True, encoding='utf-8', timeout=20)
        assert rejected.returncode == 1
        failure = json.loads(rejected.stdout)
        assert failure['event'] == 'error' and failure['roles'] == {}, failure
        assert not settings.database.exists(), 'Missing data must never become a replacement empty database'
        print('Frozen workspace preflight rejected missing database without starting background roles.')


if __name__ == '__main__':
    main()
