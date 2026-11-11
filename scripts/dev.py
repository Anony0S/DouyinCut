"""Start API, workers and Vue. The development token stays in process memory."""
import importlib.util
import os
import secrets
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    missing = [m for m in ("fastapi", "uvicorn", "pydantic", "streamget") if importlib.util.find_spec(m) is None]
    if missing:
        print("缺少 Python 依赖：" + ", ".join(missing))
        print("请先运行 uv sync，再使用 uv run python scripts/dev.py；详见 README.md。")
        return 1
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if not pnpm or not (ROOT / "desktop/node_modules/vite").exists():
        print("缺少 pnpm 或前端依赖，请安装 pnpm 并在 desktop 运行 pnpm install。")
        return 1
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    env["DOUYINCUT_SESSION_TOKEN"] = secrets.token_urlsafe(32)
    env["VITE_SESSION_TOKEN"] = env["DOUYINCUT_SESSION_TOKEN"]
    env["DOUYINCUT_DATA_DIR"] = str(ROOT / "data")
    # The packaged host redirects stderr into this log; in development each process copies its lines there.
    runtime_log = ROOT / "data" / "runtime-logs" / "desktop-runtime.log"
    if runtime_log.is_file() and runtime_log.stat().st_size > 5 * 1024 * 1024:
        runtime_log.replace(runtime_log.with_name("desktop-runtime.previous.log"))
    env["DOUYINCUT_LOG_FILE"] = str(runtime_log)
    port = env.get("DOUYINCUT_PORT", "8765")
    processes = []
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api:create_app", "--factory", "--host", "127.0.0.1", "--port", port], cwd=ROOT, env=env, creationflags=creationflags))
        processes.append(subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=ROOT, env=env, creationflags=creationflags))
        processes.append(subprocess.Popen([sys.executable, "-m", "app.live_monitoring"], cwd=ROOT, env=env, creationflags=creationflags))
        processes.append(subprocess.Popen([pnpm, "run", "dev"], cwd=ROOT / "desktop", env=env, creationflags=creationflags))
        print("\n开发工作台：http://127.0.0.1:5173\n按 Ctrl+C 停止服务。")
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
        print("一个服务已退出，停止其他服务。")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                if os.name == "nt":
                    try:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    except OSError:
                        pass
                else:
                    process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
