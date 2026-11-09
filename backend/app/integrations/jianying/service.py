import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

from ...core.config import Settings
from ...core.domain import Asset, Plan
from ...media.processing import MediaError, probe, probe_stream_timing, run_process
from ..._vendor.pyJianYingDraft import (
    ClipSettings,
    DraftFolder,
    TextBorder,
    TextSegment,
    TextStyle,
    Timerange,
    TrackType,
    VideoMaterial,
    VideoSegment,
)


def draft_root() -> Path:
    configured = os.environ.get("DOUYINCUT_JIANYING_DRAFTS_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    if sys.platform != "win32":
        raise RuntimeError("剪映草稿目录仅支持 Windows；测试时可设置 DOUYINCUT_JIANYING_DRAFTS_DIR")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("未找到 Windows 本地应用数据目录，请设置 DOUYINCUT_JIANYING_DRAFTS_DIR")
    candidates = [
        Path(local_app_data) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        Path(local_app_data) / "Jianying" / "User Data" / "Projects" / "com.lveditor.draft",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    raise RuntimeError("未找到剪映草稿目录；请设置 DOUYINCUT_JIANYING_DRAFTS_DIR 为剪映的草稿根目录")


def _draft_name(name: str, plan: Plan, job_id: str) -> str:
    asset_id = plan.clips[0].source_asset_id
    asset_token = re.sub(r"[^A-Za-z0-9]", "", asset_id)[-6:] or "asset"
    plan_token = re.sub(r"[^A-Za-z0-9]", "", plan.id)[-6:] or "plan"
    job_token = re.sub(r"[^A-Za-z0-9]", "", job_id)[-4:] or "job"
    return f"DC_{asset_token}_{plan_token}_r{plan.revision}_{job_token}"


def create_jianying_draft(plan: Plan, assets: dict[str, Asset], settings: Settings,
                          job_id: str, checkpoint=None, progress=None) -> dict:
    root = draft_root()
    name = _draft_name(assets[plan.clips[0].source_asset_id].name, plan, job_id)
    draft_path = root / name
    if draft_path.exists():
        marker_path = draft_path / "douyincut_draft.json"
        if not marker_path.is_file():
            raise FileExistsError("同名剪映草稿已存在，为避免覆盖用户工程已停止生成")
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("job_id") != job_id or marker.get("plan_id") != plan.id or marker.get("revision") != plan.revision:
            raise FileExistsError("同名剪映草稿不属于当前任务，为避免覆盖用户工程已停止生成")
        if marker.get("state") == "complete" and (draft_path / "draft_info.json").is_file() and (draft_path / "Resources").is_dir():
            return {"draft_path": str(draft_path), "draft_name": name, "plan_id": plan.id,
                    "revision": plan.revision, "clip_count": len(plan.clips),
                    "subtitle_count": len(plan.subtitles), "duration_us": plan.duration_us}
        shutil.rmtree(draft_path)

    created = False
    try:
        folder = DraftFolder(str(root))
        script = folder.create_draft(name, plan.canvas.width, plan.canvas.height, plan.canvas.fps)
        (draft_path / "douyincut_draft.json").write_text(json.dumps({
            "job_id": job_id, "plan_id": plan.id, "revision": plan.revision, "state": "building",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        resources = draft_path / "Resources"
        resources.mkdir()
        script.add_track(TrackType.video, "DouyinCut 视频")
        timeline_cursor = 0
        clip_timeline = []
        for index, clip in enumerate(plan.clips):
            if checkpoint:
                checkpoint()
            asset = assets[clip.source_asset_id]
            source = Path(asset.path)
            if not source.is_file():
                raise MediaError(f"源素材已移动，无法生成第 {index + 1} 段剪映素材")
            output = resources / f"DouyinCut_{index + 1:03}.mp4"
            timing = probe_stream_timing(source, settings)
            start = timing["audio_start_us"] if asset.has_audio else timing["video_start_us"]
            args = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-seek_timestamp", "1",
                    "-ss", f"{(clip.source_start_us + start) / 1_000_000:.6f}", "-i", str(source)]
            if not asset.has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            args += ["-t", f"{clip.duration_us / 1_000_000:.6f}", "-map", "0:v:0",
                     "-map", "0:a:0" if asset.has_audio else "1:a:0",
                     "-vf", f"scale={plan.canvas.width}:{plan.canvas.height}:force_original_aspect_ratio=decrease,pad={plan.canvas.width}:{plan.canvas.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={plan.canvas.fps}",
                     "-af", f"aresample=48000,volume={clip.audio_gain_db}dB,apad",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output)]
            run_process(args, checkpoint, log_path=settings.data_dir / "logs" / f"{job_id}-jianying-{index}.log")
            media = probe(output, settings)
            tolerance = max(200_000, 2_000_000 // plan.canvas.fps)
            if abs(media["duration_us"] - clip.duration_us) > tolerance:
                raise MediaError(f"第 {index + 1} 段剪映素材时长与方案不一致")
            material = VideoMaterial(str(output), material_name=f"片段 {index + 1:02}",
                                     duration=media["duration_us"], ffprobe=settings.ffprobe)
            material.duration = media["duration_us"]
            material.path = str(output.resolve())
            material.local_material_id = output.stem
            segment = VideoSegment(material, Timerange(timeline_cursor, media["duration_us"]),
                                   source_timerange=Timerange(0, media["duration_us"]),
                                   speed=1.0, volume=10 ** (clip.audio_gain_db / 20))
            script.add_segment(segment, "DouyinCut 视频")
            clip_timeline.append((clip.timeline_start_us, clip.duration_us,
                                  timeline_cursor, media["duration_us"]))
            timeline_cursor += media["duration_us"]
            if progress:
                progress((index + 1) / (len(plan.clips) + 1))

        if plan.subtitles:
            text_track_name = "DouyinCut 字幕"
            script.add_track(TrackType.text, text_track_name, relative_index=999)

            def map_timeline_time(time_us: int) -> int:
                for planned_start, planned_duration, actual_start, actual_duration in clip_timeline:
                    planned_end = planned_start + planned_duration
                    if planned_start <= time_us < planned_end:
                        offset = time_us - planned_start
                        return actual_start + round(offset * actual_duration / planned_duration)
                if time_us == plan.duration_us and clip_timeline:
                    _, _, actual_start, actual_duration = clip_timeline[-1]
                    return actual_start + actual_duration
                raise MediaError("字幕时间超出剪映草稿视频时间线")

            subtitle_style = TextStyle(size=5, bold=True, align=1,
                                       auto_wrapping=True, max_line_width=0.86)
            subtitle_position = ClipSettings(transform_y=-0.8)
            subtitle_border = TextBorder(width=35)
            for subtitle in plan.subtitles:
                start_us = map_timeline_time(subtitle.start_us)
                end_us = map_timeline_time(subtitle.end_us)
                if end_us <= start_us:
                    raise MediaError("字幕时间映射后长度无效")
                script.add_segment(TextSegment(
                    subtitle.text,
                    Timerange(start_us, end_us - start_us),
                    style=subtitle_style,
                    clip_settings=subtitle_position,
                    border=subtitle_border,
                ), text_track_name)

        for material in script.materials.videos:
            material_path = Path(material.path).resolve()
            if material_path.parent != resources.resolve() or not material_path.is_file():
                raise MediaError("剪映草稿素材路径未能正确绑定到工程目录")
        script.content["name"] = name
        script.content["id"] = uuid.uuid4().hex.upper()
        script.content["create_time"] = int(time.time())
        script.content["update_time"] = int(time.time())
        script.save()

        meta_path = draft_path / "draft_meta_info.json"
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata.update({"draft_id": script.content["id"], "draft_name": name,
                         "draft_root_path": str(root), "draft_fold_path": str(root),
                         "tm_duration": plan.duration_us / 1_000_000,
                         "tm_draft_cloud_modified": int(time.time())})
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        settings_path = draft_path / "draft_settings"
        settings_text = settings_path.read_text(encoding="utf-8")
        settings_text = settings_text.replace("draft_create_time=0", f"draft_create_time={int(time.time())}")
        settings_text = settings_text.replace("draft_last_edit_time=0", f"draft_last_edit_time={int(time.time())}")
        settings_path.write_text(settings_text, encoding="utf-8")
        marker_path = draft_path / "douyincut_draft.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["state"] = "complete"
        marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
        if checkpoint:
            checkpoint()
        if progress:
            progress(1)
        created = True
        return {"draft_path": str(draft_path), "draft_name": name, "plan_id": plan.id,
                "revision": plan.revision, "clip_count": len(plan.clips),
                "subtitle_count": len(plan.subtitles), "duration_us": plan.duration_us}
    finally:
        if not created:
            shutil.rmtree(draft_path, ignore_errors=True)
