"""Prepare an isolated desktop fixture for manually checking native video import."""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

if sys.platform != "win32":
    raise SystemExit("This desktop import fixture is only available on Windows.")

repo = Path(__file__).resolve().parents[1]
source_app = repo / "desktop/src-tauri/target/release/douyincut.exe"
source_runtime = repo / "desktop/src-tauri/target/release/runtime"
source_video = repo / ".runtime/m03-final/用户 原视频.mp4"
if not source_app.is_file() or not source_runtime.is_dir():
    raise SystemExit("Build the Windows release program with its runtime resources first.")
if not source_video.is_file():
    raise SystemExit("The synthetic media acceptance video is missing.")

sys.path.insert(0, str(repo / "backend"))
from app.ai.providers import CloudConfig
from app.core.storage import Store
from app.work_directory import WorkDirectoryRegistry

root = repo / ".runtime/windows-packaging/media-import-check" / str(time.time_ns())
app_dir = root / "app"
work = root / "work"
config_dir = root / "config"
input_dir = root / "input"
app_dir.mkdir(parents=True)
work.mkdir(parents=True)
config_dir.mkdir(parents=True)
input_dir.mkdir(parents=True)

shutil.copy2(source_app, app_dir / source_app.name)
shutil.copytree(source_runtime, app_dir / "runtime")
test_video = input_dir / source_video.name
shutil.copy2(source_video, test_video)
workspace_config = config_dir / "workspace.json"
WorkDirectoryRegistry(workspace_config).select(work)

# The fresh fixture cannot reach a real model endpoint if the test button is
# accidentally pressed; no actual service credential is copied into it.
store = Store(work / "database/douyincut.sqlite3")
store.put("config", {"id": "cloud", "config": CloudConfig(
    base_url="https://offline-fixture.invalid/v1",
    asr_base_url="https://offline-fixture.invalid",
).model_dump()})

launcher = root / "launch-import-check.cmd"
launcher.write_text(
    "@echo off\r\n"
    "chcp 65001 >nul\r\n"
    "setlocal\r\n"
    "title DouyinCut isolated video import check\r\n"
    'set "ROOT=%~dp0"\r\n'
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-Process -Name douyincut,DouyinCutRuntime -ErrorAction SilentlyContinue; if ($p) { Write-Output \'Exit other DouyinCut windows from their tray before testing.\'; exit 1 }"\r\n'
    "if errorlevel 1 goto already_running\r\n"
    'set "WEBVIEW2_USER_DATA_FOLDER=%ROOT%webview-profile"\r\n'
    'set "PATH=%SystemRoot%\\System32;%SystemRoot%"\r\n'
    'set "DOUYINCUT_LLM_API_KEY=offline-fixture-never-submit"\r\n'
    'set "DOUYINCUT_ASR_API_KEY=offline-fixture-never-submit"\r\n'
    'set "DOUYINCUT_OSS_ACCESS_KEY_ID=offline-fixture-never-submit"\r\n'
    'set "DOUYINCUT_OSS_ACCESS_KEY_SECRET=offline-fixture-never-submit"\r\n'
    "echo Starting isolated import test. Do not run cloud analysis.\r\n"
    '"%ROOT%app\\douyincut.exe" --workspace-config "%ROOT%config\\workspace.json" >"%ROOT%config\\desktop-runtime.log" 2>&1\r\n'
    'set "APP_EXIT_CODE=%ERRORLEVEL%"\r\n'
    "echo.\r\n"
    "echo Import check exited with code %APP_EXIT_CODE%.\r\n"
    'echo Log: "%ROOT%config\\desktop-runtime.log"\r\n'
    "pause\r\n"
    "exit /b %APP_EXIT_CODE%\r\n"
    ":already_running\r\n"
    "echo Another DouyinCut app is running. Exit it from its tray and retry.\r\n"
    "pause\r\n"
    "exit /b 2\r\n",
    encoding="utf-8",
    newline="",
)

metadata = {
    "purpose": "manual_native_file_picker_import_check",
    "synthetic_video": str(test_video),
    "synthetic_video_sha256": hashlib.sha256(test_video.read_bytes()).hexdigest(),
    "expected_duration_seconds": 6,
    "expected_dimensions": "160x120",
    "workspace": str(work),
    "workspace_config": str(workspace_config),
    "cloud_endpoint": "offline-fixture.invalid",
    "launcher": str(launcher),
}
(root / "fixture.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
instructions = f"""DouyinCut 隔离原生导入验收

仅使用本目录下的 launcher 和 input 文件。工作区是全新目录：
{work}

1. 先通过所有其他 DouyinCut 窗口的托盘菜单选择“退出 DouyinCut”。
2. 双击 launch-import-check.cmd。启动器检查没有其他 DouyinCut 进程；如有会停止本次测试。
3. 在“视频项目”点击“新建视频项目”，从这里选择测试视频：
   {test_video}
4. 等待导入完成，确认项目详情显示约 6 秒、160 × 120，项目能打开。
5. 不点击“开始分析”或其他云端按钮；测试配置使用不可用的 .invalid 地址和假凭据。
6. 通过托盘菜单退出 DouyinCut。原输入视频哈希：
   {metadata['synthetic_video_sha256']}

出现错误时保留命令窗口、复制本目录 config\\desktop-runtime.log 中的错误，并记录界面步骤；不要改选任何真实工作区或个人视频。
"""
(root / "README.txt").write_text(instructions, encoding="utf-8")
print(json.dumps(metadata, ensure_ascii=False, indent=2))
