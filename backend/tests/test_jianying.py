import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.core.domain import Asset, Candidate, Product, Transcript
from app.media.editing import build_plan
from app.integrations.jianying.service import create_jianying_draft, draft_root
from app.media.processing import import_asset, run_process


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg 与 ffprobe")
class JianyingDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(self.root / "data", ffmpeg=shutil.which("ffmpeg"),
                                 ffprobe=shutil.which("ffprobe"))
        self.settings.prepare()
        self.source = self.root / "直播.mp4"
        run_process([self.settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                     "color=s=160x120:r=25:d=6", "-f", "lavfi", "-i",
                     "sine=frequency=440:sample_rate=48000:duration=6",
                     "-c:v", "libx264", "-c:a", "aac", "-shortest", str(self.source)])
        self.asset = import_asset(self.source, self.settings)
        product = Product(name="奶黃千層", confirmed=True)
        candidates = [
            Candidate(source_asset_id=self.asset.id, start_us=500_000, end_us=2_000_000,
                      product_id=product.id, reason="展示上身", evidence="显瘦透气"),
            Candidate(source_asset_id=self.asset.id, start_us=3_500_000, end_us=5_000_000,
                      product_id=product.id, reason="介绍面料", evidence="柔软亲肤"),
        ]
        transcripts = [
            Transcript(source_asset_id=self.asset.id, start_us=500_000, end_us=2_000_000,
                       text="上身显瘦。"),
            Transcript(source_asset_id=self.asset.id, start_us=3_500_000, end_us=5_000_000,
                       text="面料柔软。"),
        ]
        self.plan = build_plan(product, candidates, {self.asset.id: self.asset}, transcripts, target_us=3_000_000,
                               ordered_ids=[candidate.id for candidate in candidates])

    def test_draft_has_separate_timeline_clips_and_private_media_copies(self):
        output_root = self.root / "jianying-projects"
        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root), "PATH": ""}), \
                patch("app._vendor.pyJianYingDraft.local_materials.pymediainfo", None):
            result = create_jianying_draft(self.plan, {self.asset.id: self.asset}, self.settings, "job_12345678")
        draft_path = Path(result["draft_path"])
        content = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
        metadata = json.loads((draft_path / "draft_meta_info.json").read_text(encoding="utf-8"))
        track = next(track for track in content["tracks"] if track["name"] == "DouyinCut 视频")
        segments = track["segments"]
        self.assertEqual(len(segments), 2)
        self.assertEqual([segment["target_timerange"]["start"] for segment in segments], [0, 1_500_000])
        self.assertTrue(all(segment["source_timerange"]["start"] == 0 for segment in segments))
        paths = [Path(material["path"]) for material in content["materials"]["videos"]]
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(path.is_file() and path.parent == draft_path / "Resources" for path in paths))
        self.assertTrue(all(draft_path.name in str(path) for path in paths))
        self.assertLessEqual(len(draft_path.name), 36)
        self.assertTrue(all(path.resolve() != Path(self.asset.path).resolve() for path in paths))
        media_entries = next(group["value"] for group in metadata["draft_materials"] if group["type"] == 0)
        source_copy = draft_path / "Resources" / "DouyinCut_source.mp4"
        self.assertEqual(len(media_entries), 1)
        source_entry = media_entries[0]
        self.assertEqual(Path(source_entry["file_Path"]), source_copy)
        self.assertEqual(source_copy.read_bytes(), self.source.read_bytes())
        self.assertEqual(source_entry["metetype"], "video")
        self.assertEqual(source_entry["duration"], self.asset.duration_us)
        self.assertEqual((source_entry["width"], source_entry["height"]), (self.asset.width, self.asset.height))
        self.assertEqual(source_entry["roughcut_time_range"], {"start": 0, "duration": self.asset.duration_us})
        self.assertEqual(result["clip_count"], 2)
        self.assertEqual(result["subtitle_count"], 2)
        subtitle_track = next(track for track in content["tracks"] if track["name"] == "DouyinCut 字幕")
        self.assertEqual([segment["target_timerange"]["start"] for segment in subtitle_track["segments"]],
                         [0, 1_500_000])
        text_materials = {material["id"]: json.loads(material["content"])
                          for material in content["materials"]["texts"]}
        captions = [text_materials[segment["material_id"]] for segment in subtitle_track["segments"]]
        self.assertEqual([caption["text"] for caption in captions], ["上身显瘦。", "面料柔软。"])
        self.assertTrue(all("effectStyle" not in caption["styles"][0] for caption in captions))
        self.assertEqual(content["materials"]["effects"], [])
        self.assertTrue(all(segment["clip"]["transform"]["y"] == -0.8
                            for segment in subtitle_track["segments"]))
        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root)}):
            retried = create_jianying_draft(self.plan, {self.asset.id: self.asset}, self.settings, "job_12345678")
        self.assertEqual(retried, result)
        self.assertEqual(len(list((draft_path / "Resources").glob("*.mp4"))), 3)

    def test_failed_generation_removes_partial_draft(self):
        output_root = self.root / "jianying-projects"
        checkpoints = 0

        def stop_during_source_copy():
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                raise RuntimeError("cancel test")

        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root)}):
            with self.assertRaisesRegex(RuntimeError, "cancel test"):
                create_jianying_draft(self.plan, {self.asset.id: self.asset}, self.settings,
                                      "job_cancelled", checkpoint=stop_during_source_copy)
        self.assertEqual(list(output_root.iterdir()), [])

    def test_cover_is_copied_and_written_to_both_draft_metadata_files(self):
        output_root = self.root / "jianying-projects-with-cover"
        cover = self.root / "generated-cover.png"
        run_process([self.settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                     "color=c=red:s=300x400", "-frames:v", "1", str(cover)])
        references = (self.root / "reference-one.png", self.root / "reference-two.jpg")
        for reference, color in zip(references, ("blue", "green"), strict=True):
            run_process([self.settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                         f"color=c={color}:s=240x160", "-frames:v", "1", str(reference)])
        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root), "PATH": ""}), \
                patch("app._vendor.pyJianYingDraft.local_materials.pymediainfo", None):
            result = create_jianying_draft(self.plan, {self.asset.id: self.asset}, self.settings,
                                           "job_cover_test", cover_image_path=cover,
                                           reference_image_paths=references)
        draft_path = Path(result["draft_path"])
        cover_copy = draft_path / "Resources" / "DouyinCut_cover.jpg"
        cover_resource = next((draft_path / "Resources" / "cover").glob("*.jpg"))
        cover_preview = draft_path / "draft_cover.jpg"
        content = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
        metadata = json.loads((draft_path / "draft_meta_info.json").read_text(encoding="utf-8"))
        self.assertTrue(cover_copy.is_file())
        self.assertEqual(cover_copy.read_bytes()[:2], b"\xff\xd8")
        image_info = json.loads(subprocess.check_output([
            self.settings.ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height", "-of", "json", str(cover_resource),
        ], text=True))["streams"][0]
        self.assertEqual((image_info["codec_name"], image_info["width"], image_info["height"]),
                         ("mjpeg", 300, 400))
        self.assertTrue(cover_preview.is_file())
        self.assertEqual(cover_preview.read_bytes()[:2], b"\xff\xd8")
        self.assertEqual(cover_resource.read_bytes(), cover_copy.read_bytes())
        self.assertEqual(cover_preview.read_bytes(), cover_copy.read_bytes())
        self.assertTrue(cover.is_file())
        self.assertNotIn("static_cover_image_path", content)
        self.assertFalse((draft_path / "Timelines").exists(),
                         "不能预先生成没有工程结构的时间线目录")
        self.assertTrue(content["cover_enable"])
        self.assertEqual(content["cover"]["type"], "image")
        self.assertEqual(content["cover"]["sub_type"], "local")
        cover_draft = content["cover"]["cover_draft"]
        self.assertEqual(content["cover"]["cover_draft_id"], cover_draft["id"])
        self.assertEqual(len(content["materials"]["drafts"]), 1)
        self.assertEqual(content["materials"]["drafts"][0]["draft"]["id"], cover_draft["id"])
        cover_track = cover_draft["tracks"][0]
        cover_segment = cover_track["segments"][0]
        end_frame = (content["duration"] * self.plan.canvas.fps + 999_999) // 1_000_000
        self.assertEqual(cover_segment["target_timerange"], {
            "start": (end_frame + self.plan.canvas.fps + 1) * 1_000_000 // self.plan.canvas.fps,
            "duration": 3_000_000,
        })
        self.assertEqual(cover_segment["render_index"], 101)
        self.assertEqual(cover_segment["track_render_index"], 101)
        self.assertEqual(cover_draft["materials"]["videos"][0]["check_flag"], 62978047)
        self.assertEqual(cover_track["segments"][0]["material_id"],
                         cover_draft["materials"]["videos"][0]["id"])
        self.assertEqual(cover_draft["materials"]["videos"][0]["type"], "photo")
        self.assertIn("##_draftpath_placeholder_", cover_draft["materials"]["videos"][0]["path"])
        self.assertTrue(cover_draft["materials"]["videos"][0]["path"].endswith(cover_resource.name))
        self.assertEqual(metadata["draft_cover"], "draft_cover.jpg")
        media_entries = next(group["value"] for group in metadata["draft_materials"] if group["type"] == 0)
        self.assertEqual([entry["metetype"] for entry in media_entries], ["video", "photo", "photo", "photo"])
        self.assertEqual(len({entry["id"] for entry in media_entries}), 4)
        self.assertEqual(Path(media_entries[0]["file_Path"]),
                         draft_path / "Resources" / "DouyinCut_source.mp4")
        for entry, reference in zip(media_entries[1:-1], references, strict=True):
            path = Path(entry["file_Path"])
            self.assertEqual(path.parent, draft_path / "Resources")
            self.assertEqual(path.suffix, reference.suffix)
            self.assertEqual(path.read_bytes(), reference.read_bytes())
            self.assertEqual((entry["width"], entry["height"]), (240, 160))
        self.assertEqual(len(content["materials"]["videos"]), 2)
        self.assertTrue(all(len(track["segments"]) == 2 for track in content["tracks"]))
        cover_entry = media_entries[-1]
        self.assertEqual(Path(cover_entry["file_Path"]), cover_resource)
        self.assertEqual(cover_entry["id"], cover_draft["materials"]["videos"][0]["local_material_id"])
        self.assertEqual((cover_entry["width"], cover_entry["height"], cover_entry["duration"]), (300, 400, 5_000_000))
        self.assertEqual(cover_entry["roughcut_time_range"], {"start": -1, "duration": -1})

    def test_override_directory_is_created(self):
        output_root = self.root / "configured" / "drafts"
        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root)}):
            self.assertEqual(draft_root(), output_root.resolve())
        self.assertTrue(output_root.is_dir())
