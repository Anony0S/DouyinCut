import json
import tempfile
import unittest
from pathlib import Path

from app.integrations.jianying.cover_sync import sync_pending_cover


class CoverSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.draft = Path(self.temp.name)
        self.timeline_id = "A" * 32
        self.marker = self.draft / "douyincut_draft.json"
        self.marker.write_text(json.dumps({"state": "complete", "timeline_cover_pending": self.timeline_id}))
        (self.draft / "Resources").mkdir()
        (self.draft / "Resources" / "DouyinCut_cover.jpg").write_bytes(b"generated cover")

    def initialize_timeline(self, timeline_id=None):
        timeline_id = timeline_id or self.timeline_id
        timeline = self.draft / "Timelines" / timeline_id
        timeline.mkdir(parents=True)
        (timeline.parent / "project.json").write_text(json.dumps({"main_timeline_id": timeline_id}))
        return timeline

    def test_waits_until_complete_timeline_exists_and_only_publishes_once(self):
        timeline = self.initialize_timeline()
        self.assertFalse(sync_pending_cover(self.draft))
        self.assertFalse((timeline / "draft_cover.jpg").exists())
        (timeline / "draft_content.json").write_text("{}")
        self.assertTrue(sync_pending_cover(self.draft))
        cover = timeline / "draft_cover.jpg"
        self.assertEqual(cover.read_bytes(), b"generated cover")
        cover.write_bytes(b"user cover")
        self.assertFalse(sync_pending_cover(self.draft))
        self.assertEqual(cover.read_bytes(), b"user cover")

    def test_existing_user_cover_is_never_replaced(self):
        timeline = self.initialize_timeline()
        (timeline / "draft_content.json").write_text("{}")
        cover = timeline / "draft_cover.jpg"
        cover.write_bytes(b"user cover")
        self.assertTrue(sync_pending_cover(self.draft))
        self.assertEqual(cover.read_bytes(), b"user cover")
        self.assertNotIn("timeline_cover_pending", json.loads(self.marker.read_text(encoding="utf-8")))

    def test_unexpected_timeline_id_is_ignored(self):
        timeline = self.initialize_timeline("B" * 32)
        (timeline / "draft_content.json").write_text("{}")
        self.assertFalse(sync_pending_cover(self.draft))
        self.assertFalse((timeline / "draft_cover.jpg").exists())
