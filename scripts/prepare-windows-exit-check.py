"""Prepare an isolated Windows desktop fixture for explicit-exit acceptance."""

from pathlib import Path
import shutil
import subprocess
import sys
import time

if sys.platform != "win32":
    raise SystemExit("This acceptance fixture is only available on Windows.")

repo = Path(__file__).resolve().parents[1]
source_app = repo / "desktop/src-tauri/target/release/douyincut.exe"
source_runtime = repo / "desktop/src-tauri/target/release/runtime"
media = repo / ".runtime/windows-packaging/media"
ffmpeg = media / "ffmpeg.exe"
ffprobe = media / "ffprobe.exe"
required = [source_app, source_runtime, ffmpeg, ffprobe]
missing = [str(path) for path in required if not path.exists()]
if missing:
    raise SystemExit("Missing release inputs:\n" + "\n".join(missing))

sys.path.insert(0, str(repo / "backend"))
from app.core.config import Settings
from app.core.domain import Asset, Candidate, Canvas, Clip, Plan, Product
from app.core.storage import Store
from app.media.processing import import_asset
from app.projects.service import ProjectDraft, save_draft
from app.work_directory import WorkDirectoryRegistry

root = repo / ".runtime/windows-packaging/exit-check" / str(time.time_ns())
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
fixture = work / "recordings/imports/exit-check-source.mp4"
fixture.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([
    str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=120",
    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=120",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-shortest", str(fixture),
], check=True, timeout=300)

store = Store(settings.database)
asset = import_asset(fixture, settings)
product = Product(name="退出验收测试商品", confirmed=True)
candidate = Candidate(product_id=product.id, source_asset_id=asset.id,
                      start_us=0, end_us=60_000_000, reason="隔离退出验收")
plan = Plan(product_id=product.id, status="approved", canvas=Canvas(width=1080, height=1920),
            clips=[Clip(source_asset_id=asset.id, source_start_us=0, source_end_us=60_000_000,
                        timeline_start_us=0, candidate_id=candidate.id)])
store.put("asset", asset.model_dump())
store.put("product", product.model_dump())
store.put("candidate", candidate.model_dump())
store.put("plan", plan.model_dump())
save_draft(store, asset.id, ProjectDraft(product_name=product.name))
jobs = [store.enqueue("export", {"plan_id": plan.id, "revision": plan.revision, "format": "mp4"})
        for _ in range(2)]

launcher = root / "launch-exit-check.cmd"
launcher.write_text(
    "@echo off\r\n"
    "setlocal\r\n"
    "title DouyinCut exit acceptance\r\n"
    'set "ROOT=%~dp0"\r\n'
    'set "WEBVIEW2_USER_DATA_FOLDER=%ROOT%webview-profile"\r\n'
    "echo Starting isolated exit acceptance...\r\n"
    '"%ROOT%app\\douyincut.exe" --workspace-config "%ROOT%config\\workspace.json"\r\n'
    'set "APP_EXIT_CODE=%ERRORLEVEL%"\r\n'
    "echo.\r\n"
    "echo DouyinCut exited with code %APP_EXIT_CODE%.\r\n"
    'echo Runtime log: "%ROOT%config\\logs\\desktop-runtime.log"\r\n'
    "pause\r\n",
    encoding="utf-8",
    newline="",
)
(root / "README.txt").write_text(
    "隔离退出验收夹具\n\n"
    "只操作本目录中的测试工作区，不要选择真实工作数据或填写云端凭据。\n"
    "启动：双击 launch-exit-check.cmd。项目列表中有一个“退出验收测试商品”项目，\n"
    "其中有一个已审核方案和两个排队的本地导出任务。导出使用随程序提供的 FFmpeg。\n\n"
    "建议步骤：\n"
    "1. 打开项目，修改商品资料但不要保存；从托盘选择退出，再选“返回保存”。\n"
    "   确认窗口仍开、草稿仍在。再次退出并选择“放弃并退出”，确认应用正常结束。\n"
    "2. 再运行启动器。进入项目/后台任务页，等第一个导出开始后从托盘选择退出。\n"
    "   在二次确认中选取消；确认任务仍在运行且进度继续。\n"
    "3. 再次请求退出并确认；看到收尾状态后等待应用关闭。检查没有本次应用遗留的\n"
    "   DouyinCutRuntime.exe / ffmpeg.exe 进程。两个导出任务应在重启后都保持暂停。\n"
    "4. 重启后确认任务没有自动运行；手动恢复一个任务，确认它能继续并完成。\n"
    "5. 若还要验证检查点中断，恢复第二个任务，导出进行时再确认退出；重启后\n"
    "   检查任务暂停、临时产物已清理，原始合成视频与已保存项目仍存在。\n\n"
    f"隔离工作目录：{work}\n"
    f"已排队任务：{', '.join(job['id'] for job in jobs)}\n",
    encoding="utf-8",
)
print(f"Isolated launcher: {launcher}")
print(f"Instructions: {root / 'README.txt'}")
print(f"Isolated work data: {work}")
