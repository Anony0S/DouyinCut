"""Prepare an isolated native Windows tray smoke fixture from the release build."""

from pathlib import Path
import shutil
import sys
import time

if sys.platform != "win32":
    raise SystemExit("This smoke fixture is only available on Windows.")

repo = Path(__file__).resolve().parents[1]
source_app = repo / "desktop/src-tauri/target/release/douyincut.exe"
source_runtime = repo / "desktop/src-tauri/target/release/runtime"
if not source_app.is_file() or not source_runtime.is_dir():
    raise SystemExit("Build the Windows release program with its runtime resources first.")

sys.path.insert(0, str(repo / "backend"))
from app.work_directory import WorkDirectoryRegistry

root = repo / ".runtime/windows-packaging/tray-smoke" / str(time.time_ns())
app_dir = root / "app"
work = root / "work"
config = root / "config/workspace.json"
app_dir.mkdir(parents=True)
work.mkdir(parents=True)
shutil.copy2(source_app, app_dir / source_app.name)
shutil.copytree(source_runtime, app_dir / "runtime")
WorkDirectoryRegistry(config).select(work)

launcher = root / "launch-tray-check.cmd"
launcher.write_text(
    "@echo off\r\n"
    "setlocal\r\n"
    "title DouyinCut tray smoke\r\n"
    'set "ROOT=%~dp0"\r\n'
    'set "WEBVIEW2_USER_DATA_FOLDER=%ROOT%webview-profile"\r\n'
    "echo Starting the isolated DouyinCut tray test...\r\n"
    '"%ROOT%app\\douyincut.exe" --workspace-config "%ROOT%config\\workspace.json"\r\n'
    'set "APP_EXIT_CODE=%ERRORLEVEL%"\r\n'
    "echo.\r\n"
    "echo DouyinCut exited with code %APP_EXIT_CODE%.\r\n"
    'echo Runtime log: "%ROOT%config\\logs\\desktop-runtime.log"\r\n'
    "echo Keep this window open and share the exit code if startup failed.\r\n"
    "pause\r\n",
    encoding="utf-8",
    newline="",
)
print(f"Isolated launcher: {launcher}")
print(f"Isolated work data: {work}")
