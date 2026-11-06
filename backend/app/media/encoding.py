"""Runtime-tested H.264 selection for product clips, with bounded CPU fallback."""
from dataclasses import dataclass
import logging
import os
from pathlib import Path

from ..core.config import Settings
from . import processing


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class H264Encoder:
    name: str
    options: tuple[str, ...]
    pixel_format: str = "yuv420p"


CPU_ENCODER = H264Encoder("libx264", ("-preset", "veryfast", "-crf", "23"))
HARDWARE_ENCODERS = (
    H264Encoder("h264_nvenc", ("-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0")),
    H264Encoder("h264_qsv", ("-preset", "medium", "-global_quality", "23"), "nv12"),
    H264Encoder("h264_amf", ("-usage", "transcoding", "-quality", "balanced", "-rc", "cqp",
                             "-qp_i", "21", "-qp_p", "23")),
)
# One worker processes media jobs serially. Cache only capability probes, never
# task results or resolution-specific failures; restart to re-detect drivers.
_encoders: dict[str, H264Encoder] = {}


def cpu_threads() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def _video_options(encoder: H264Encoder) -> list[str]:
    return ["-c:v", encoder.name, *encoder.options, "-pix_fmt", encoder.pixel_format,
            "-threads:v", str(cpu_threads())]


def select_h264_encoder(settings: Settings, checkpoint=None) -> H264Encoder:
    if checkpoint:
        checkpoint()
    if settings.ffmpeg in _encoders:
        return _encoders[settings.ffmpeg]
    try:
        advertised = processing.run_process(
            [settings.ffmpeg, "-hide_banner", "-encoders"], checkpoint=checkpoint, timeout=15)
        available = {fields[1] for line in advertised.splitlines() if len(fields := line.split()) >= 2}
        for encoder in HARDWARE_ENCODERS:
            if encoder.name not in available:
                continue
            try:
                # Being compiled into FFmpeg does not imply a usable GPU/driver.
                processing.run_process([
                    settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-filter_threads", "1", "-f", "lavfi", "-i", "color=s=1280x720:r=30",
                    "-frames:v", "3", "-an", *_video_options(encoder), "-f", "null", "-",
                ], checkpoint=checkpoint, timeout=15)
            except processing.MediaError:
                continue
            _encoders[settings.ffmpeg] = encoder
            logger.info("Product clip hardware encoder available: %s", encoder.name)
            return encoder
    except processing.MediaError:
        logger.info("Product clip hardware detection unavailable; using CPU")
    _encoders[settings.ffmpeg] = CPU_ENCODER
    return CPU_ENCODER


def encode_h264(args: list[str], output: Path, settings: Settings, *, duration: float,
                checkpoint=None, log_prefix: Path | None = None) -> str:
    """Encode prepared inputs/filters; retry a failed hardware job once on CPU.

    Pause/cancel exceptions deliberately propagate without a retry. The caller
    owns atomic publication; an interrupted/failed encode leaves no partial MP4.
    """
    encoder = select_h264_encoder(settings, checkpoint)
    attempts = (encoder,) if encoder == CPU_ENCODER else (encoder, CPU_ENCODER)
    try:
        for candidate in attempts:
            if checkpoint:
                checkpoint()
            output.unlink(missing_ok=True)
            log_path = log_prefix.with_name(f"{log_prefix.name}-{candidate.name}.log") if log_prefix else None
            logger.info("Encoding product clip with %s", candidate.name)
            try:
                processing.run_process([
                    *args, *_video_options(candidate), "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", "-f", "mp4", str(output),
                ], checkpoint=checkpoint, timeout=max(300, int(duration) + 120), log_path=log_path)
                return candidate.name
            except processing.MediaError:
                if candidate == CPU_ENCODER:
                    raise
                logger.warning("Product clip %s failed; retrying with limited CPU threads", candidate.name)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
