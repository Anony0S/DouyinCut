"""Production process ownership shared by source and frozen desktop launchers."""

import multiprocessing
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx

from .core.config import Settings


@dataclass(frozen=True)
class SessionInfo:
    url: str
    token: str = field(repr=False)


class StopSignal:
    """Single-writer stop flag that remains usable when a reader dies.

    multiprocessing.Event uses a condition whose notification can deadlock
    after a waiting child is killed. A shared byte has no reader-owned lock.
    """

    def __init__(self, context):
        self._flag = context.RawValue('b', False)

    def is_set(self):
        return bool(self._flag.value)

    def set(self):
        self._flag.value = True

    def clear(self):
        self._flag.value = False

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(0.05, remaining))
            else:
                time.sleep(0.05)
        return True


def _run_role(role, settings, frontend, token, listener, stop, report):
    # Only the launcher writes descriptors to stdout. Third-party diagnostics
    # stay on stderr, which the native host redirects to its diagnostic log.
    sys.stdout = sys.stderr
    try:
        if role == 'api':
            import uvicorn
            from .api import create_app

            app = create_app(settings, token, frontend_dir=frontend)
            server = uvicorn.Server(uvicorn.Config(app, loop='asyncio', http='h11', ws='none', lifespan='on',
                                                  log_level='warning', access_log=False,
                                                  timeout_graceful_shutdown=2))

            def request_stop():
                stop.wait()
                server.should_exit = True

            threading.Thread(target=request_stop, daemon=True).start()
            report.send({'ready': True})
            server.run(sockets=[listener])
        else:
            if role == 'worker':
                from .worker import Worker
                worker = Worker(settings)
            else:
                from .live_monitoring.service import LiveMonitorWorker
                worker = LiveMonitorWorker(settings)
            report.send({'ready': True})
            worker.run(stop)
    except BaseException as exc:
        report.send({'error': f'{role}: {type(exc).__name__}: {exc}'})
        raise
    finally:
        report.close()
        if listener is not None:
            listener.close()


class DesktopSession:
    """Own an authenticated loopback API and both background roles.

    The descriptor is private launcher IPC, never a URL parameter or a file.
    Work-directory selection belongs to the caller; this boundary requires an
    existing directory and never substitutes another path.
    """

    def __init__(self, settings: Settings, frontend_dir: Path, *, startup_timeout: float = 30,
                 workspace_config: Path | None = None, application_dir: Path | None = None):
        self.settings = settings
        self.frontend_dir = Path(frontend_dir).resolve()
        self.startup_timeout = startup_timeout
        self.workspace_config = workspace_config
        self.application_dir = application_dir
        self._context = multiprocessing.get_context('spawn')
        self._stop = StopSignal(self._context)
        self._roles = {}
        self._listener = None
        self._info = None

    def start(self) -> SessionInfo:
        if self._info is not None:
            raise RuntimeError('桌面会话已经启动')
        if not (self.frontend_dir / 'index.html').is_file():
            raise RuntimeError(f'缺少工作台资源：{self.frontend_dir / "index.html"}')
        if not self.settings.data_dir.is_dir():
            raise RuntimeError(f'工作目录不可用：{self.settings.data_dir}')
        if self.workspace_config is not None:
            from .work_directory import WorkDirectoryRegistry
            WorkDirectoryRegistry(self.workspace_config, application_dir=self.application_dir).require(self.settings.data_dir)
        self.settings.prepare()
        # Initialize the current schema before concurrent service construction.
        from .core.storage import Store
        from .live_monitoring import LiveStore
        LiveStore(Store(self.settings.database))
        self._stop.clear()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener = listener
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        settings = replace(self.settings, port=port)
        info = SessionInfo(f'http://127.0.0.1:{port}', secrets.token_urlsafe(32))
        try:
            for role in ('api', 'worker', 'live_monitor'):
                receive, send = self._context.Pipe(duplex=False)
                process = self._context.Process(
                    name=f'douyincut-{role}', target=_run_role,
                    args=(role, settings, self.frontend_dir, info.token,
                          listener if role == 'api' else None, self._stop, send),
                )
                process.start()
                send.close()
                self._roles[role] = {'process': process, 'report': receive, 'ready': False}
            deadline = time.monotonic() + self.startup_timeout
            with httpx.Client(base_url=info.url, trust_env=False, timeout=0.3) as client:
                while time.monotonic() < deadline:
                    for role, state in self._roles.items():
                        if state['report'].poll():
                            try:
                                message = state['report'].recv()
                            except EOFError:
                                message = {}
                            if 'error' in message:
                                raise RuntimeError(f'桌面服务启动失败：{message["error"]}')
                            state['ready'] = message.get('ready', state['ready'])
                        if not state['process'].is_alive():
                            raise RuntimeError(f'桌面服务提前退出：{role}')
                    if all(state['ready'] for state in self._roles.values()):
                        try:
                            response = client.get('/api/v1/projects', headers={'X-Session-Token': info.token})
                            if response.status_code == 200:
                                self._info = info
                                return info
                        except httpx.TransportError:
                            pass
                    time.sleep(0.05)
            raise RuntimeError('桌面服务启动超时')
        except BaseException:
            self.close()
            raise

    def status(self) -> dict:
        return {role: {'pid': state['process'].pid,
                       'alive': state['process'].is_alive(),
                       'ready': state['ready'] and state['process'].is_alive(),
                       'exitcode': state['process'].exitcode}
                for role, state in self._roles.items()}

    def close(self):
        self._stop.set()
        deadline = time.monotonic() + 30
        for state in self._roles.values():
            process = state['process']
            process.join(max(0, deadline - time.monotonic()))
            if process.is_alive():
                process.terminate()
                process.join(5)
            state['report'].close()
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        self._info = None
