"""Encode one chronological selection from original recording streams.

The caller owns the staging directory, including temporary seekable remuxes.
"""
from pathlib import Path

from ..core.config import Settings
from . import processing
from .encoding import cpu_threads, encode_h264


def cut_product_clip(sources: list[tuple[Path, dict]], output: Path, settings: Settings,
                     checkpoint=None, progress=None, log_prefix: Path | None = None):
    threads = cpu_threads()
    args = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-filter_threads", str(threads), "-filter_complex_threads", str(threads)]
    filters = []
    canvas = None
    duration = 0.0
    for index, (source, piece) in enumerate(sources):
        if checkpoint:
            checkpoint()
        length = piece["clip_end"] - piece["clip_start"]
        duration += length
        if source.suffix.lower() == ".ts":
            # MPEG-TS seeks can land after the preceding keyframe and silently
            # drop a whole selected video interval. Build a seekable index by
            # copying packets, never by transcoding the full recording block.
            seekable = output.parent / f"source-{index:04d}.mkv"
            processing.run_process([
                settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-f", "matroska", str(seekable),
            ], checkpoint=checkpoint)
            source = seekable
        # Input seek decodes/discards up to the exact start when transcoding;
        # input duration bounds decoding to the selected part of each TS/MP4.
        args += ["-threads", str(max(1, threads // len(sources))),
                 "-ss", f"{piece['clip_start']:.6f}", "-t", f"{length:.6f}", "-i", str(source)]
        if len(sources) > 1:
            info = processing.probe(source, settings)
            if canvas is None:
                canvas = (info["width"] // 2 * 2, info["height"] // 2 * 2)
            width, height = canvas
            filters.append(
                f"[{index}:v:0]setpts=PTS-STARTPTS,trim=duration={length:.6f},"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{index}]")
            audio = (f"[{index}:a:0]asetpts=PTS-STARTPTS,aresample=48000,"
                     "aformat=channel_layouts=stereo,apad" if info["has_audio"] else
                     "anullsrc=channel_layout=stereo:sample_rate=48000")
            filters.append(f"{audio},atrim=duration={length:.6f}[a{index}]")
        if progress:
            progress(.1 * (index + 1) / len(sources))
    if len(sources) > 1:
        inputs = "".join(f"[v{index}][a{index}]" for index in range(len(sources)))
        filters.append(f"{inputs}concat=n={len(sources)}:v=1:a=1[v][a]")
        args += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-fps_mode", "cfr"]
    else:
        args += ["-map", "0:v:0", "-map", "0:a?"]
    args += ["-t", f"{duration:.6f}"]
    encode_h264(args, output, settings, duration=duration, checkpoint=checkpoint, log_prefix=log_prefix)
