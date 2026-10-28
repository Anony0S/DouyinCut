"""Prepare an isolated Windows desktop fixture for ASR / upload exit-retry acceptance (ticket 06)."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

if sys.platform != "win32":
    raise SystemExit("This acceptance fixture is only available on Windows.")

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--release-dir", type=Path, default=repo / "desktop/src-tauri/target/release",
                    help="Release directory containing douyincut.exe and runtime/")
parser.add_argument("--minutes", type=int, default=7, choices=range(3, 11), metavar="3-10",
                    help="Synthetic speech length; ASR sends one request per 2 minutes")
args = parser.parse_args()
release_dir = args.release_dir.resolve()
source_app = release_dir / "douyincut.exe"
source_runtime = release_dir / "runtime"
media = repo / ".runtime/windows-packaging/media"
ffmpeg = media / "ffmpeg.exe"
ffprobe = media / "ffprobe.exe"
required = [source_app, source_runtime, ffmpeg, ffprobe]
missing = [str(path) for path in required if not path.exists()]
if missing:
    raise SystemExit("Missing release inputs:\n" + "\n".join(missing))

sys.path.insert(0, str(repo / "backend"))
from app.core.config import Settings
from app.core.storage import Store
from app.media.processing import import_asset
from app.projects.service import ProjectDraft, save_draft
from app.work_directory import WorkDirectoryRegistry

SPEECH = ("大家好，今天给大家介绍这款纯棉宽松短袖。面料是新疆长绒棉，透气不闷汗，"
          "洗了也不容易变形。版型是落肩宽松款，一百斤到一百六十斤都能穿。"
          "颜色有白色、黑色和雾霾蓝，三个颜色都很百搭。现在下单还送运费险，喜欢的可以直接拍。")

root = repo / ".runtime/windows-packaging/cloud-exit-check" / str(time.time_ns())
app_dir = root / "app"
work = root / "work"
config = root / "config/workspace.json"
app_dir.mkdir(parents=True)
work.mkdir(parents=True)
shutil.copy2(source_app, app_dir / source_app.name)
shutil.copytree(source_runtime, app_dir / "runtime")
WorkDirectoryRegistry(config).select(work)

settings = Settings(work, ffmpeg=str(ffmpeg.resolve()), ffprobe=str(ffprobe.resolve()))
settings.prepare()
fixture = work / "recordings/imports/cloud-exit-check-source.mp4"
fixture.parent.mkdir(parents=True, exist_ok=True)
duration = args.minutes * 60
with tempfile.TemporaryDirectory() as temp:
    # Real speech makes ASR return sentences, so reused chunks are visible in the result.
    speech = Path(temp) / "speech.wav"
    script = Path(temp) / "speak.ps1"
    script.write_text(
        "Add-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$voice = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1\n"
        "if (-not $voice) { throw 'No zh-CN speech voice is installed.' }\n"
        "$s.SelectVoice($voice.VoiceInfo.Name)\n"
        "$s.SetOutputToWaveFile($args[0])\n"
        "$s.Speak($args[1])\n"
        "$s.Dispose()\n",
        encoding="utf-8-sig",
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    str(speech), SPEECH], check=True, timeout=120)
    subprocess.run([
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={duration}",
        "-stream_loop", "-1", "-i", str(speech),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-t", str(duration), str(fixture),
    ], check=True, timeout=600)

store = Store(settings.database)
asset = import_asset(fixture, settings)
store.put("asset", asset.model_dump())
save_draft(store, asset.id, ProjectDraft(product_name="退出重发验收短袖"))
launcher = root / "launch-cloud-exit-check.cmd"
launcher.write_text(
    "@echo off\r\n"
    "setlocal\r\n"
    "title DouyinCut cloud exit acceptance\r\n"
    'set "ROOT=%~dp0"\r\n'
    'set "WEBVIEW2_USER_DATA_FOLDER=%ROOT%webview-profile"\r\n'
    "echo Starting isolated cloud exit acceptance...\r\n"
    '"%ROOT%app\\douyincut.exe" --workspace-config "%ROOT%config\\workspace.json"\r\n'
    'set "APP_EXIT_CODE=%ERRORLEVEL%"\r\n'
    "echo.\r\n"
    "echo DouyinCut exited with code %APP_EXIT_CODE%.\r\n"
    'echo Runtime log: "%ROOT%config\\logs\\desktop-runtime.log"\r\n'
    "pause\r\n",
    encoding="utf-8",
    newline="",
)
inspect = root / "inspect.cmd"
inspect.write_text(
    "@echo off\r\n"
    f'cd /d "{repo}"\r\n'
    f'uv run --no-sync python scripts/inspect-windows-cloud-exit-check.py "{work}"\r\n'
    "pause\r\n",
    encoding="utf-8",
    newline="",
)
chunks = (duration + 119) // 120
(root / "README.txt").write_text(
    "工单 06 云端退出重发验收夹具\n\n"
    "本夹具会调用真实 OSS 和 ASR 并产生少量费用；不会调用真实直播或剪映。\n"
    "云端凭据保存在 Windows 凭据管理器，与正式 DouyinCut 共用：在此填写会覆盖已保存的密钥。\n"
    f"测试视频为 {args.minutes} 分钟合成中文口播，ASR 将分 {chunks} 段请求（每段 2 分钟）。\n\n"
    "检查工具：每个检查点双击 inspect.cmd，它只读打印任务状态、云端操作记录和各分段缓存。\n"
    "另开 PowerShell 查看进程：\n"
    "  Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('douyincut.exe','DouyinCutRuntime.exe') } |\n"
    "    Select-Object Name, ProcessId, ParentProcessId | Format-Table -AutoSize\n\n"
    "步骤：\n"
    "0. 双击 launch-cloud-exit-check.cmd，在“设置”填写 OSS endpoint / Bucket 与模型密钥并保存。\n"
    "1. 退出中断：打开“退出重发验收短袖”，点“使用云端 AI 分析并粗剪”。看到 1 段完成后\n"
    "   （inspect.cmd 中有 succeeded 分段），从托盘退出。退出确认应提示后台分析任务、\n"
    "   “不等于撤销”和“可能重复执行并产生费用”。确认后应用应在数秒内关闭，不等云端超时。\n"
    "   运行 inspect.cmd：应有 exit_interrupted / explicit_exit 记录，已完成分段仍为 succeeded。\n"
    "   进程查询不应有本次应用遗留的 douyincut.exe / DouyinCutRuntime.exe。\n"
    "2. 重新启动：任务应直接重新排队并自动继续，不弹核查或确认。inspect.cmd 中上次中断\n"
    "   记录变为 consumed；此前 succeeded 的分段不应出现新的 asr 操作（只重发被中断段）。\n"
    "3. 二次主动退出：在第 2 步任务仍有分段未完成时再次从托盘退出。inspect.cmd 应出现\n"
    "   新的 exit_interrupted 记录，之前 consumed 的记录不变。再启动，任务继续直到完成。\n"
    "4. 连续两次启动：任务完成后退出、再启动两次。consumed 记录不应再次触发重排，\n"
    "   任务保持完成状态，操作记录数量不变。\n"
    "5. 本地暂停任务不受影响：工单 05 的暂停导出仍需手动恢复（可用 prepare-windows-exit-check.py 夹具复测）。\n\n"
    "若分段太快、来不及在第 3 步前退出，可加长视频重新生成：\n"
    "  uv run python scripts/prepare-windows-cloud-exit-check.py --minutes 10\n\n"
    "无法稳定手动触发的场景：上传进行中退出（单段音频约 3.8 MB，上传通常 1 秒内完成）、\n"
    "成功响应与退出的精确竞态、普通超时。这些依赖后端受控阻塞测试，不要求在此复现；\n"
    "若恰好在上传中退出，inspect.cmd 会显示 oss_upload 的 exit_interrupted 记录，可一并记录。\n\n"
    f"隔离工作目录：{work}\n"
    f"素材：{asset.id}\n",
    encoding="utf-8",
)
print(f"Isolated launcher: {launcher}")
print(f"Inspector: {inspect}")
print(f"Instructions: {root / 'README.txt'}")
print(f"Isolated work data: {work}")
