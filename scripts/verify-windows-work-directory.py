"""Verify release setup and original-location recovery in the actual WebView."""

import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import websocket

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo / 'backend'), str(repo / 'scripts')]
from app.core.config import Settings
from app.core.domain import Asset
from app.core.storage import Store
from app.work_directory import WorkDirectoryRegistry
from runtime_process import assert_process_exited

root = repo / ('.runtime/windows-packaging/目录恢复 ' + str(time.time_ns()))
root.mkdir()
executable = repo / 'desktop/src-tauri/target/release/douyincut.exe'
config = root / '用户配置' / 'workspace.json'
work = root / '原工作 目录'
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PYTHON', 'VITE_', 'DOUYINCUT_', 'UV_', 'VIRTUAL_ENV'))}
env['PATH'] = os.pathsep.join([str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']])
env['WEBVIEW2_USER_DATA_FOLDER'] = str(root / 'webview-data')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
process = None
connection = None
sequence = 0


def cdp(method, params=None):
    global sequence
    sequence += 1
    identity = sequence
    connection.send(json.dumps({'id': identity, 'method': method, 'params': params or {}}))
    while True:
        result = json.loads(connection.recv())
        if result.get('id') == identity:
            if 'error' in result:
                raise RuntimeError(result['error'])
            return result['result']


def evaluate(expression):
    result = cdp('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
    if 'exceptionDetails' in result:
        raise RuntimeError(result['exceptionDetails'])
    return result['result'].get('value')


def page(target_filter):
    global connection
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        assert process.poll() is None, 'Native host exited unexpectedly'
        try:
            with opener.open(f'http://127.0.0.1:{debug_port}/json/list', timeout=1) as response:
                targets = json.load(response)
            target = next((t for t in targets if t.get('type') == 'page' and target_filter(t.get('url', ''))), None)
            if target:
                if connection:
                    connection.close()
                connection = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=15, suppress_origin=True)
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError('Expected WebView page did not appear')


def text_contains(value):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        text = evaluate('document.body?.innerText') or ''
        if value in text:
            return text
        time.sleep(0.1)
    raise AssertionError(f'Missing visible text: {value}; actual: {text}')


def start():
    global process, debug_port
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        debug_port = listener.getsockname()[1]
    env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = f'--remote-debugging-port={debug_port}'
    process = subprocess.Popen([str(executable), '--workspace-config', str(config)], cwd=root, env=env)
    page(lambda url: url.endswith('/setup.html'))


def stop():
    global connection
    if connection:
        connection.close()
        connection = None
    if process is not None and process.poll() is None:
        process.kill()
        process.wait(10)


try:
    start()
    text_contains('请选择新的空目录')
    assert not config.exists() and not work.exists(), 'Initial page must not choose a fallback workspace'
    screenshot = cdp('Page.captureScreenshot', {'format': 'png'})
    (root / 'first-launch.png').write_bytes(base64.b64decode(screenshot['data']))
    stop()
    work.mkdir()
    WorkDirectoryRegistry(config).select(work)
    Store(Settings(work).database).put('asset', Asset(
        id='recovery-fixture', path=str(work / 'source.mp4'), name='恢复原目录验收.mp4',
        sha256='recovery', duration_us=1_000_000, width=160, height=120, has_audio=True,
    ).model_dump())
    unavailable = root / '断开设备'
    work.rename(unavailable)
    before = Settings(unavailable).database.read_bytes()
    start()
    text_contains('原工作目录不可用')
    assert not work.exists(), 'Missing original location must not be recreated'
    assert Settings(unavailable).database.read_bytes() == before
    snapshot = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        "Get-CimInstance Win32_Process -Filter \"Name='DouyinCutRuntime.exe'\" | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=15, check=True)
    roles = json.loads(snapshot.stdout or '[]')
    if isinstance(roles, dict):
        roles = [roles]
    assert not [p for p in roles if p['ParentProcessId'] == process.pid], 'Repair page must not retain runtime roles'
    screenshot = cdp('Page.captureScreenshot', {'format': 'png'})
    (root / 'repair.png').write_bytes(base64.b64decode(screenshot['data']))
    unavailable.rename(work)
    assert evaluate("(() => { const button = [...document.querySelectorAll('button')].find(b=>b.textContent==='重试原位置'); if(!button || button.disabled) return false; button.click(); return true; })()")
    page(lambda url: url.startswith('http://127.0.0.1:'))
    text_contains('恢复原目录验收.mp4')
    assert evaluate("fetch('/api/v1/projects').then(r=>r.status)") == 200
    (root / 'result.json').write_text(json.dumps({'passed': True, 'initial_page_no_fallback': True,
        'missing_path_no_database_or_roles': True, 'original_location_retry': True,
        'authenticated_project_restored': True, 'native_picker_clicks_verified': False}, indent=2), encoding='utf-8')
    print(f'Actual setup and missing-path retry passed. Evidence: {root}', flush=True)
finally:
    stop()
    if process is not None:
        assert_process_exited(process.pid)
