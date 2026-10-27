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
app_dir = root / "测试程序"
work = root / "工作目录"
config = root / "用户配置/workspace.json"
app_dir.mkdir(parents=True)
work.mkdir(parents=True)
shutil.copy2(source_app, app_dir / source_app.name)
shutil.copytree(source_runtime, app_dir / "runtime")
WorkDirectoryRegistry(config).select(work)

launcher = root / "launch-tray-check.cmd"
launcher.write_text(
    "@echo off\r\n"
    "title DouyinCut tray smoke\r\n"
    "setlocal\r\n"
    'set "WEBVIEW2_USER_DATA_FOLDER=%~dp0webview-profile"\r\n'
    '"%~dp0测试程序\\douyincut.exe" --workspace-config "%~dp0用户配置\\workspace.json"\r\n',
    encoding="utf-8",
    newline="",
)
print(f"Isolated launcher: {launcher}")
print(f"Isolated work data: {work}")
