"""Small, on-demand local previews. Never queue decodes behind mouse movement."""
from collections import OrderedDict
from pathlib import Path
import re
import subprocess
import time
from threading import Lock

from ..core.config import Settings


class PreviewBusy(Exception):
    pass


class ThumbnailCache:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = Lock()
        self.frames = OrderedDict()

    def frame(self, path: Path, second: float) -> bytes:
        if not self.lock.acquire(blocking=False):
            raise PreviewBusy()
        try:
            stat = path.stat()
            key = (str(path), stat.st_size, stat.st_mtime_ns, second)
            if key in self.frames:
                self.frames.move_to_end(key)
                return self.frames[key]
            try:
                deadline = time.monotonic() + 8
                # Fast seeks may skip the preceding TS keyframe. Check the output
                # timestamp before accepting a short seek; expand only when needed.
                seeks = dict.fromkeys((max(0, second - 3), max(0, second - 15), 0))
                for seek in seeks:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("暂时无法生成画面预览，请稍后重试")
                    result = subprocess.run([
                        self.settings.ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin",
                        "-copyts", "-start_at_zero",
                        "-threads", "1", "-filter_threads", "1",
                        *(["-ss", f"{seek:.6f}"] if seek else []),
                        "-i", str(path), "-map", "0:v:0",
                        "-frames:v", "1", "-an",
                        "-vf", f"select=gte(t\\,{second:.6f}),scale=320:320:force_original_aspect_ratio=decrease,showinfo",
                        "-threads", "1", "-c:v", "mjpeg", "-q:v", "5", "-f", "image2pipe", "pipe:1",
                    ], capture_output=True, timeout=remaining,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if result.returncode or not result.stdout.startswith(b"\xff\xd8"):
                        continue
                    log = result.stderr.decode(errors="replace")
                    pts = re.search(r"pts_time:([\d.eE+-]+)", log)
                    rate = re.search(r"frame_rate:\s*(\d+)/(\d+)", log)
                    interval = int(rate[2]) / int(rate[1]) if rate and int(rate[1]) else 0
                    if seek == 0 or (pts and second - .01 <= float(pts[1]) <= second + max(.05, interval + .01)):
                        break
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError("暂时无法生成画面预览，请稍后重试") from exc
            if result.returncode or not result.stdout.startswith(b"\xff\xd8"):
                raise ValueError("该位置暂时无法解码预览画面")
            self.frames[key] = result.stdout
            while len(self.frames) > 96:
                self.frames.popitem(last=False)
            return result.stdout
        finally:
            self.lock.release()
