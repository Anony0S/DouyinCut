import json
import shutil
from pathlib import Path

from ..core.config import Settings
from ..core.domain import Asset, Plan, new_id
from .editing import srt_text
from .processing import MediaError, probe, probe_stream_timing, run_process


def export_plan(plan: Plan, assets: dict[str, Asset], settings: Settings, job_id: str,
                package: bool = True, checkpoint=None, progress=None, preview: bool = False):
    # Each attempt has a distinct staging folder; immutable published results are never overwritten.
    export_id = f"export_{job_id}"
    root = settings.data_dir / ("previews" if preview else "exports")
    root.mkdir(parents=True, exist_ok=True)
    final = root / export_id
    if final.is_dir():
        manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
        if manifest["plan_id"] != plan.id or manifest["revision"] != plan.revision:
            raise MediaError("导出任务与已有产物不一致")
        probe(final / "video.mp4", settings)
        return manifest["artifact"]
    stage = root / f".{export_id}-{new_id('attempt')}.partial"
    stage.mkdir(parents=True)
    clip_dir = stage / "clips"
    clip_dir.mkdir()
    try:
        for index, clip in enumerate(plan.clips):
            if checkpoint:
                checkpoint()
            asset = assets[clip.source_asset_id]
            if not Path(asset.path).is_file():
                raise MediaError("源素材已移动，请重新定位素材")
            stream_timing = probe_stream_timing(Path(asset.path), settings)
            out = clip_dir / f"{index + 1:03}.mp4"
            duration = f"{clip.duration_us / 1_000_000:.6f}"
            # Seek both streams at one absolute media timestamp so their PTS alignment survives.
            stream_start_us = stream_timing["audio_start_us"] if asset.has_audio else stream_timing["video_start_us"]
            args = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-seek_timestamp", "1", "-ss", f"{(clip.source_start_us + stream_start_us) / 1_000_000:.6f}",
                    "-i", asset.path]
            if not asset.has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            args += ["-t", duration, "-map", "0:v:0", "-map", "0:a:0" if asset.has_audio else "1:a:0",
                     "-vf", f"scale={plan.canvas.width}:{plan.canvas.height}:force_original_aspect_ratio=decrease,pad={plan.canvas.width}:{plan.canvas.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={plan.canvas.fps}",
                     "-af", f"aresample=48000,volume={clip.audio_gain_db}dB,apad",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out)]
            run_process(args, checkpoint, log_path=settings.data_dir / "logs" / f"{job_id}-{index}.log")
            probe(out, settings)
            if progress:
                progress((index + 1) / (len(plan.clips) + 1))
        # All paths here are generated numeric names, not user-controlled concat expressions.
        concat = stage / "concat.txt"
        concat.write_text("".join(f"file 'clips/{i + 1:03}.mp4'\n" for i in range(len(plan.clips))), encoding="utf-8")
        run_process([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts",
                     "-f", "concat", "-safe", "1", "-i", str(concat),
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-ar", "48000", "-ac", "2", "-fps_mode", "cfr",
                     "-movflags", "+faststart", str(stage / "video.mp4")], checkpoint,
                    log_path=settings.data_dir / "logs" / f"{job_id}-concat.log")
        rendered = probe(stage / "video.mp4", settings)
        tolerance = max(200_000, len(plan.clips) * 2_000_000 // plan.canvas.fps)
        if abs(rendered["duration_us"] - plan.duration_us) > tolerance:
            raise MediaError("输出时长与剪辑方案不一致，请检查源素材时间基准")
        (stage / "subtitles.srt").write_text(srt_text(plan.subtitles), encoding="utf-8-sig")
        (stage / "timeline.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        result = {"id": export_id, "plan_id": plan.id, "revision": plan.revision,
                  "format": "preview" if preview else ("package" if package else "mp4"), "path": str(final),
                  "video_path": str(final / "video.mp4"), "duration_us": rendered["duration_us"]}
        (stage / "manifest.json").write_text(json.dumps({
            "schema_version": "1.0", "plan_id": plan.id, "revision": plan.revision,
            "video": "video.mp4", "subtitles": "subtitles.srt", "timeline": "timeline.json",
            "clips": [{"file": f"clips/{i + 1:03}.mp4", **clip.model_dump()} for i, clip in enumerate(plan.clips)],
            "source_paths": {key: asset.path for key, asset in assets.items()},
            "jianying_compatibility": "untested",
            "artifact": result,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        concat.unlink()
        if not package:
            shutil.rmtree(clip_dir)
            manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
            manifest["clips"] = []
            (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if checkpoint:
            checkpoint()
        stage.rename(final)
        return result
    except BaseException:
        # Staging contains only generated files belonging to this attempt.
        shutil.rmtree(stage, ignore_errors=True)
        raise
