"""Read the actual release WebView using a temporary loopback debugging port.

Only this verification subprocess receives the debugging environment variable.
The generated application does not enable a debugging listener by default.
"""

import base64
import json
import os
from pathlib import Path
import shutil
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

root = repo / ('.runtime/windows-packaging/native 中文 测试 ' + str(time.time_ns()))
appdir = root / '应用'
appdir.mkdir(parents=True, exist_ok=True)
shutil.copy2(repo / 'desktop/src-tauri/target/release/douyincut.exe', appdir)
shutil.copytree(repo / 'desktop/src-tauri/target/release/runtime', appdir / 'runtime', dirs_exist_ok=True)
work = root / '工作目录'
settings = Settings(work)
work.mkdir()
config = root / '用户配置' / 'workspace.json'
WorkDirectoryRegistry(config).select(work)
Store(settings.database).put('asset', Asset(
    id='native-session-fixture', path=str(work / 'source.mp4'), name='Windows 桌面会话验收.mp4',
    sha256='native-session', duration_us=3_000_000, width=160, height=120, has_audio=True,
).model_dump())
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    debug_port = listener.getsockname()[1]
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PYTHON', 'VITE_', 'DOUYINCUT_', 'UV_', 'VIRTUAL_ENV'))}
env['PATH'] = os.pathsep.join([str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']])
env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (
    f'--remote-debugging-port={debug_port} --enable-logging --log-file="{root / "browser.log"}"')
env['WEBVIEW2_USER_DATA_FOLDER'] = str(root / 'webview-data')
native_log = (root / 'native-host.log').open('wb')
process = subprocess.Popen([str(appdir / 'douyincut.exe'), '--workspace-config', str(config)], env=env, cwd=root,
                           stdout=native_log, stderr=native_log)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
connection = None
sequence = 0
def cdp(method, params=None):
    global sequence
    sequence += 1
    request_id = sequence
    connection.send(json.dumps({'id': request_id, 'method': method, 'params': params or {}}))
    while True:
        message = json.loads(connection.recv())
        if message.get('id') == request_id:
            if 'error' in message:
                raise RuntimeError(message['error'])
            return message['result']

try:
    deadline = time.monotonic() + 50
    target = None
    targets = []
    last_debug_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            native_log.flush()
            raise AssertionError(f'Native host exited {process.returncode}; diagnostics: {root / "native-host.log"}')
        try:
            with opener.open(f'http://127.0.0.1:{debug_port}/json/list', timeout=1) as response:
                targets = json.load(response)
            target = next((t for t in targets if t.get('type') == 'page' and t.get('url', '').startswith('http://127.0.0.1:')), None)
            if target:
                break
        except OSError as exc:
            last_debug_error = str(exc)
        time.sleep(0.2)
    if not target:
        (root / 'debug-discovery.json').write_text(json.dumps(
            {'targets': targets, 'last_error': last_debug_error, 'browser_log': str(root / 'browser.log')},
            ensure_ascii=False, indent=2), encoding='utf-8')
        setup = next((t for t in targets if t.get('type') == 'page' and t.get('url', '').endswith('/setup.html')), None)
        if setup:
            connection = websocket.create_connection(setup['webSocketDebuggerUrl'], timeout=10, suppress_origin=True)
            observed = cdp('Runtime.evaluate', {'expression': 'document.body?.innerText', 'returnByValue': True})
            text = observed.get('result', {}).get('value', '')
            (root / 'setup-error.txt').write_text(text, encoding='utf-8')
            print('Setup page: ' + text, flush=True)
        raise AssertionError(f'WebView did not expose the loopback workbench; evidence: {root}')
    connection = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=10, suppress_origin=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        observed = cdp('Runtime.evaluate', {'expression': 'document.body && JSON.stringify({text:document.body.innerText, url:location.href, injected:typeof globalThis.__DOUYINCUT_SESSION_TOKEN__, local:JSON.stringify(localStorage), session:JSON.stringify(sessionStorage)})', 'returnByValue': True})
        value = observed.get('result', {}).get('value')
        if not isinstance(value, str):
            time.sleep(0.1)
            continue
        page = json.loads(value)
        if 'Windows 桌面会话验收.mp4' in page['text']:
            break
        time.sleep(0.2)
    assert 'Windows 桌面会话验收.mp4' in page['text'], page['text']
    assert page['url'].endswith('/#/projects'), page['url']
    assert page['injected'] == 'undefined'
    assert 'token' not in page['local'].lower() and 'token' not in page['session'].lower()
    result = cdp('Runtime.evaluate', {'expression': "fetch('/api/v1/projects').then(async r=>({status:r.status, project:(await r.json()).items[0].name}))", 'awaitPromise': True, 'returnByValue': True})['result']['value']
    assert result == {'status': 200, 'project': 'Windows 桌面会话验收.mp4'}, result
    assert cdp('Runtime.evaluate', {'expression': "location.origin", 'returnByValue': True})['result']['value'] + '/#/projects' == page['url']
    pid_expression = "fetch('/api/v1/diagnostics').then(async r=>r.status)"
    assert cdp('Runtime.evaluate', {'expression': pid_expression, 'awaitPromise': True, 'returnByValue': True})['result']['value'] == 200
    screenshot = cdp('Page.captureScreenshot', {'format': 'png'})
    (root / 'workbench.png').write_bytes(base64.b64decode(screenshot['data']))
    evidence = {'native_project_list': result, 'url_has_no_token': True,
        'injection_consumed': True, 'browser_storage_has_no_token': True, 'relocated_and_no_dev_path': True,
        'screenshot': str(root / 'workbench.png')}
    print('Native WebView authenticated project rendering passed.', flush=True)
    # Page.close is a browser action and does not exercise Tauri's native window
    # close path. Check abrupt owner loss here; graceful shutdown has a separate
    # native SessionHost integration test using the same production boundary.
    snapshot = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        "Get-CimInstance Win32_Process -Filter \"Name='DouyinCutRuntime.exe'\" | Select-Object ProcessId,ParentProcessId,ExecutablePath | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding='utf-8', timeout=15, check=True)
    all_roles = json.loads(snapshot.stdout or '[]')
    if isinstance(all_roles, dict):
        all_roles = [all_roles]
    owned_roles = [p['ProcessId'] for p in all_roles if p.get('ExecutablePath') and str(appdir).lower() in p['ExecutablePath'].lower()]
    assert len(owned_roles) >= 4, f'Expected frozen supervisor and three roles, observed {owned_roles}'
    process.kill()
    process.wait(10)
    deadline = time.monotonic() + 10
    while True:
        try:
            for pid in owned_roles:
                assert_process_exited(pid)
            break
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    evidence['abrupt_owner_cleanup'] = True
    evidence['owned_runtime_process_count'] = len(owned_roles)
    evidence['passed'] = True
    (root / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Native owner loss cleaned {len(owned_roles)} owned runtime processes. Evidence: {root}', flush=True)
finally:
    if connection:
        connection.close()
    if process.poll() is None:
        process.kill()
        process.wait(10)
    assert_process_exited(process.pid)
    native_log.close()
