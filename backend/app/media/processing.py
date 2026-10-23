import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from ..core.config import Settings
from ..core.domain import Asset, new_id


class MediaError(RuntimeError):
    pass


def run_process(args: list[str], checkpoint: Callable | None = None, timeout: int = 3600,
                log_path: Path | None = None) -> str:
    """No shell interpolation. Drain output to disk to avoid pipe deadlocks."""
    import tempfile
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError as exc:
            raise MediaError(f"找不到程序 {Path(args[0]).name}，请安装或设置其路径") from exc
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if checkpoint:
                    checkpoint()
                if time.monotonic() > deadline:
                    raise MediaError("媒体处理超时")
                time.sleep(0.15)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        output.seek(0)
        text = output.read().decode("utf-8", errors="replace")
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(text, encoding="utf-8")
        if process.returncode:
            raise MediaError(f"媒体处理失败（退出码 {process.returncode}），请检查任务日志")
        return text


def probe(path: Path, settings: Settings) -> dict:
    result = run_process([settings.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=60)
    data = json.loads(result)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise MediaError("素材没有可用视频轨")
    duration = data.get("format", {}).get("duration") or video.get("duration")
    if duration is None or float(duration) <= 0:
        raise MediaError("无法确定素材时长")
    def timestamp_us(value) -> int:
        try:
            return round(float(value) * 1_000_000)
        except (TypeError, ValueError):
            return 0

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration_us": round(float(duration) * 1_000_000),
        "width": int(video["width"]), "height": int(video["height"]),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "video_start_us": timestamp_us(video.get("start_time")),
        "audio_start_us": timestamp_us(audio.get("start_time")) if audio else 0,
    }


def probe_stream_timing(path: Path, settings: Settings) -> dict[str, int]:
    result = run_process([settings.ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)], timeout=60)
    streams = json.loads(result).get("streams", [])

    def timestamp_us(value) -> int:
        try:
            return round(float(value) * 1_000_000)
        except (TypeError, ValueError):
            return 0

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {"video_start_us": timestamp_us(video.get("start_time")) if video else 0,
            "audio_start_us": timestamp_us(audio.get("start_time")) if audio else 0}


def import_asset(path: Path, settings: Settings, checkpoint: Callable | None = None) -> Asset:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise MediaError("请选择视频文件")
    initial = path.stat()
    info = probe(path, settings)
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1024 * 1024 * 4):
            digest.update(block)
            if checkpoint:
                checkpoint()
    current = path.stat()
    if initial.st_size != current.st_size or initial.st_mtime_ns != current.st_mtime_ns:
        raise MediaError("文件仍在写入，请等待录制结束后导入")
    return Asset(id=new_id("asset"), path=str(path), name=path.name,
                 sha256=digest.hexdigest(), **info)


def convert_to_mp4(source: Path, target: Path, settings: Settings,
                   checkpoint: Callable | None = None) -> Path:
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        return target
    if target.is_file() and target.stat().st_size:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.stem}.partial.mp4")
    temporary.unlink(missing_ok=True)
    try:
        details = json.loads(run_process([
            settings.ffprobe, "-v", "error", "-show_streams", "-of", "json", str(source),
        ], timeout=60))
        video = next((stream for stream in details.get("streams", [])
                      if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in details.get("streams", [])
                      if stream.get("codec_type") == "audio"), None)
        if video is None:
            raise MediaError("素材没有可用视频轨")
        copy_video = video.get("codec_name") == "h264"
        copy_audio = audio is None or audio.get("codec_name") == "aac"
        codecs = ["-c:v", "copy" if copy_video else "libx264"]
        if not copy_video:
            codecs += ["-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        codecs += ["-c:a", "copy" if copy_audio else "aac"]
        if not copy_audio:
            codecs += ["-b:a", "128k"]
        base = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-map", "0:v:0", "-map", "0:a?", *codecs,
                "-movflags", "+faststart", "-f", "mp4", str(temporary)]
        try:
            run_process(base, checkpoint=checkpoint)
        except MediaError:
            temporary.unlink(missing_ok=True)
            run_process([
                settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4", str(temporary),
            ], checkpoint=checkpoint)
        probe(temporary, settings)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def extract_audio(asset: Asset, target: Path, settings: Settings, checkpoint=None,
                  start_us: int = 0, end_us: int | None = None, audio_start_us: int | None = None):
    if not asset.has_audio:
        raise MediaError("素材没有音轨，无法进行语音识别")
    end_us = asset.duration_us if end_us is None else end_us
    if not 0 <= start_us < end_us <= asset.duration_us:
        raise MediaError("音频区间超出素材范围")
    audio_start_us = getattr(asset, "audio_start_us", 0) if audio_start_us is None else audio_start_us
    run_process([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-seek_timestamp", "1", "-ss", f"{(start_us + audio_start_us) / 1_000_000:.6f}", "-i", asset.path,
                 "-t", f"{(end_us - start_us) / 1_000_000:.6f}",
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)], checkpoint)
    # FFmpeg can create a header-only WAV when a probed duration leaves a
    # sub-frame tail. Never upload that as a valid ASR input.
    if not target.exists() or target.stat().st_size <= 44:
        raise MediaError("音频片段为空，已停止上传 ASR；请检查素材末尾时长")


def extract_frame(asset: Asset, time_us: int, target: Path, settings: Settings, checkpoint=None):
    run_process([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{time_us / 1_000_000:.6f}", "-i", asset.path,
                 "-frames:v", "1", "-vf", "scale=768:768:force_original_aspect_ratio=decrease",
                 "-q:v", "3", str(target)], checkpoint, timeout=60)


def diagnostics(settings: Settings):
    return {"ffmpeg": shutil.which(settings.ffmpeg), "ffprobe": shutil.which(settings.ffprobe)}
