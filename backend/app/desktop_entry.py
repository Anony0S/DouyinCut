"""Private stdio protocol used by the native desktop host."""

import argparse
import json
import multiprocessing
import sys
import threading
import traceback
from pathlib import Path

from .core.config import Settings
from .desktop_runtime import DesktopSession


def main() -> int:
    # The native owner decodes private stdout and diagnostics as UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='DouyinCut production runtime')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--frontend-dir', type=Path)
    parser.add_argument('--host-controlled', action='store_true')
    parser.add_argument('--workspace-config', type=Path)
    parser.add_argument('--workspace-action', choices=('inspect', 'select'))
    parser.add_argument('--application-dir', type=Path)
    args = parser.parse_args()
    if args.workspace_action:
        if args.workspace_config is None:
            parser.error('--workspace-action requires --workspace-config')
        if args.host_controlled and sys.stdin.readline().strip() != 'start':
            return 1
        from .work_directory import WorkDirectoryRegistry
        registry = WorkDirectoryRegistry(args.workspace_config, application_dir=args.application_dir)
        try:
            if args.workspace_action == 'select':
                if args.work_dir is None:
                    parser.error('select requires --work-dir')
                state = registry.select(args.work_dir)
            else:
                state = registry.inspect()
        except (RuntimeError, OSError) as exc:
            state = {'state': 'repair_required', 'path': str(args.work_dir) if args.work_dir else None,
                     'message': str(exc)}
        print(json.dumps(state, ensure_ascii=True), flush=True)
        return 0
    if args.work_dir is None:
        parser.error('session startup requires --work-dir')
    resources = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    frontend = args.frontend_dir or resources / 'frontend'
    session = DesktopSession(Settings(args.work_dir.absolute()), frontend,
                             workspace_config=args.workspace_config, application_dir=args.application_dir)
    shutdown = threading.Event()

    def read_control():
        # EOF also closes the session if the native host exits unexpectedly.
        for line in sys.stdin:
            if line.strip() == 'shutdown':
                break
        shutdown.set()

    def emit(body):
        print(json.dumps(body, ensure_ascii=True), flush=True)

    try:
        if args.host_controlled:
            # No database or service startup until the native owner has attached
            # the process to its Windows job. EOF here leaves no child roles.
            emit({'event': 'awaiting_host'})
            if sys.stdin.readline().strip() != 'start':
                raise RuntimeError('原生外壳未授权启动桌面会话')
        info = session.start()
        emit({'event': 'ready', 'url': info.url, 'token': info.token, 'roles': session.status()})
        threading.Thread(target=read_control, daemon=True).start()
        while not shutdown.wait(0.2):
            failed = [role for role, state in session.status().items() if not state['alive']]
            if failed:
                raise RuntimeError(f'关键桌面服务已退出：{", ".join(failed)}')
        session.close()
        emit({'event': 'stopped', 'roles': session.status()})
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        traceback.print_exc(file=sys.stderr)
        session.close()
        emit({'event': 'error', 'message': str(exc) or type(exc).__name__, 'roles': session.status()})
        return 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
