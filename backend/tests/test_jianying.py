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
        self.settings = Settings(self.root / "data")
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
        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root)}):
            result = create_jianying_draft(self.plan, {self.asset.id: self.asset}, self.settings, "job_12345678")
        draft_path = Path(result["draft_path"])
        content = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
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
        self.assertEqual(len(list((draft_path / "Resources").glob("*.mp4"))), 2)

    def test_failed_generation_removes_partial_draft(self):
        output_root = self.root / "jianying-projects"
        checkpoints = 0

        def stop_after_first_clip():
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                raise RuntimeError("cancel test")

        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root)}):
            with self.assertRaisesRegex(RuntimeError, "cancel test"):
                create_jianying_draft(self.plan, {self.asset.id: self.asset}, self.settings,
                                      "job_cancelled", checkpoint=stop_after_first_clip)
        self.assertEqual(list(output_root.iterdir()), [])

    def test_override_directory_is_created(self):
        output_root = self.root / "configured" / "drafts"
        with patch.dict(os.environ, {"DOUYINCUT_JIANYING_DRAFTS_DIR": str(output_root)}):
            self.assertEqual(draft_root(), output_root.resolve())
        self.assertTrue(output_root.is_dir())
