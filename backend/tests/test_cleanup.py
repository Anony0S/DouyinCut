import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cleanup import CleanupPreviewChanged, DEFAULT_RETENTION_DAYS, SECONDS_PER_DAY, cleanup_unused_files
from app.core.storage import Store
from app.live_monitoring import LiveStore


class CleanupUnusedFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "database" / "douyincut.sqlite3")
        self.live = LiveStore(self.store)
        for name in ("analysis", "previews", "exports", "covers", "recordings"):
            (self.root / name).mkdir()
        self.now = 2_000_000_000.0

    def old(self, path: Path, *, directory: bool | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        if directory is None:
            directory = not path.suffix
        if not directory:
            path.write_bytes(b"old")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "output.bin").write_bytes(b"old")
        timestamp = self.now - DEFAULT_RETENTION_DAYS * SECONDS_PER_DAY - 1
        for item in [path, *path.rglob("*")] if path.is_dir() else [path]:
            os.utime(item, (timestamp, timestamp))
        return path

    def test_removes_old_unreferenced_generated_outputs(self):
        export = self.old(self.root / "exports" / "export_orphan")
        nested_dir = export / "nested"
        nested_dir.mkdir()
        nested_file = nested_dir / "additional.bin"
        nested_file.write_bytes(b"old")
        timestamp = self.now - DEFAULT_RETENTION_DAYS * SECONDS_PER_DAY - 1
        os.utime(nested_dir, (timestamp, timestamp))
        os.utime(nested_file, (timestamp, timestamp))
        analysis = self.old(self.root / "analysis" / ("job_" + "a" * 32))
        result = cleanup_unused_files(self.store, self.root, now=self.now)
        self.assertFalse(export.exists())
        self.assertFalse(analysis.exists())
        self.assertEqual((result["deleted_directories"], result["deleted_files"], result["bytes_reclaimed"]),
                         (3, 3, 9))

    def test_preserves_referenced_assets_outputs_and_recent_files(self):
        asset_id = "asset_1"
        self.store.put("asset", {"id": asset_id, "path": str(self.root / "recordings" / "imports" / "source.mp4")})
        asset_cover = self.old(self.root / "covers" / asset_id)
        export = self.old(self.root / "exports" / "export_kept")
        self.store.put("export", {"id": "export_kept", "path": str(export)})
        recent = self.root / "previews" / "recent"
        recent.mkdir()
        (recent / "output.bin").write_bytes(b"new")
        os.utime(recent, (self.now, self.now))
        imported = self.root / "recordings" / "imports" / "source.mp4"
        imported.parent.mkdir(parents=True)
        imported.write_bytes(b"user video")

        cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertTrue(asset_cover.exists())
        self.assertTrue(export.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(imported.exists())

    def test_preserves_active_staging_and_analysis_jobs(self):
        active = self.store.enqueue("export", {})
        staging = self.old(self.root / "previews" / f".export_{active['id']}-attempt_{'b' * 32}.partial",
                           directory=True)
        analysis = self.old(self.root / "analysis" / active["id"])

        cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertTrue(staging.exists())
        self.assertTrue(analysis.exists())

    def test_expanded_cleanup_deletes_expired_records_and_original_but_retains_recent_dependencies(self):
        workspace = self.root / "work"
        original = self.old(self.root / "source.mp4")
        other_source = self.old(self.root / "other.mp4")
        info = original.stat()
        copy = self.old(workspace / "recordings" / "imports" / "copy.mp4")
        self.store.put("asset", {"id": "expired", "path": str(copy), "original_path": str(original),
                                 "original_file_id": [info.st_dev, info.st_ino]})
        self.store.put("asset", {"id": "retained", "path": str(other_source)})
        self.store.put("config", {"id": "cloud"})
        self.store.put("project", {"id": "retained"})
        stamp = self.now - 8 * SECONDS_PER_DAY
        monitor = self.live.create_monitor("https://live.douyin.com/123", "old")
        session_id = self.live.start_session(monitor["id"], workspace / "recordings" / "live" / monitor["id"])
        recording = self.old(workspace / "recordings" / "live" / monitor["id"] / session_id / "recording.ts")
        self.live.upsert_segment(session_id, recording, 1, "completed", 3)
        self.live.stop_session(monitor["id"], session_id)
        with self.store.connection() as conn:
            conn.execute("UPDATE objects SET created=? WHERE kind='asset'", (stamp,))
            conn.execute("UPDATE objects SET created=? WHERE kind='project'", (self.now,))
            conn.execute("UPDATE live_monitors SET created=?", (stamp,))
            conn.execute("UPDATE live_monitor_controls SET updated=?", (stamp,))
            conn.execute("UPDATE live_sessions SET started=?,ended=?", (stamp, stamp))
            conn.execute("UPDATE live_segments SET started=?,ended=?", (stamp, stamp))
        default = cleanup_unused_files(self.store, workspace, now=self.now, preview=True)
        expanded = cleanup_unused_files(self.store, workspace, now=self.now, preview=True, clear_expired_data=True)
        self.assertEqual(expanded["original_videos"], [str(original)])
        self.assertEqual((expanded["file_count"], expanded["record_count"]), (3, 5))
        self.assertTrue(all(path.exists() for path in (original, other_source, copy, recording)))
        with self.assertRaises(CleanupPreviewChanged):
            cleanup_unused_files(self.store, workspace, now=self.now, clear_expired_data=True,
                                 preview_id=default["preview_id"])
        result = cleanup_unused_files(self.store, workspace, now=self.now, clear_expired_data=True,
                                      preview_id=expanded["preview_id"])
        self.assertEqual((result["deleted_files"], result["deleted_records"]), (3, 5))
        self.assertTrue(other_source.exists())
        self.assertTrue(all(not path.exists() for path in (original, copy, recording)))
        self.assertEqual(self.store.get("config", "cloud")["id"], "cloud")
        self.assertEqual(self.store.get("asset", "retained")["id"], "retained")

    def test_expanded_cleanup_keeps_records_for_retry_if_file_cannot_be_deleted(self):
        original = self.old(self.root / "recordings" / "imports" / "old.mp4")
        self.store.put("asset", {"id": "expired", "path": str(original)})
        with self.store.connection() as conn:
            conn.execute("UPDATE objects SET created=?", (self.now - 8 * SECONDS_PER_DAY,))
        preview = cleanup_unused_files(self.store, self.root, now=self.now, preview=True, clear_expired_data=True)
        with patch("app.cleanup.Path.unlink", side_effect=PermissionError("busy")):
            result = cleanup_unused_files(self.store, self.root, now=self.now, clear_expired_data=True,
                                          preview_id=preview["preview_id"])
        self.assertEqual((result["retained_files"], result["retained_records"], result["deleted_records"]), (1, 1, 0))
        self.assertTrue(original.exists())
        self.assertEqual(self.store.get("asset", "expired")["id"], "expired")

    def test_expanded_cleanup_leaves_replacement_at_original_path(self):
        original = self.old(self.root / "source.mp4")
        info = original.stat()
        original.rename(original.with_name("moved.mp4"))
        self.old(original)
        self.store.put("asset", {"id": "expired", "path": str(self.root / "copy.mp4"),
                                 "original_path": str(original), "original_file_id": [info.st_dev, info.st_ino]})
        with self.store.connection() as conn:
            conn.execute("UPDATE objects SET created=?", (self.now - 8 * SECONDS_PER_DAY,))

        preview = cleanup_unused_files(self.store, self.root, now=self.now, preview=True, clear_expired_data=True)
        self.assertEqual((preview["file_count"], preview["record_count"], preview["original_videos"]), (0, 1, []))
        cleanup_unused_files(self.store, self.root, now=self.now, clear_expired_data=True,
                             preview_id=preview["preview_id"])
        self.assertTrue(original.exists())

    def test_stale_preview_cannot_delete_a_different_batch(self):
        export = self.old(self.root / "exports" / "export_orphan")
        first = cleanup_unused_files(self.store, self.root, now=self.now, preview=True)
        other = self.old(self.root / "exports" / "new_candidate")
        with self.assertRaises(CleanupPreviewChanged):
            cleanup_unused_files(self.store, self.root, now=self.now, preview_id=first["preview_id"])
        self.assertTrue(export.exists())
        self.assertTrue(other.exists())

    def test_removes_old_analysis_workspace_for_terminal_job(self):
        job = self.store.enqueue("analyze", {})
        with self.store.connection() as conn:
            conn.execute("UPDATE jobs SET state='failed' WHERE id=?", (job["id"],))
        analysis = self.old(self.root / "analysis" / job["id"])

        cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertFalse(analysis.exists())

    def test_removes_stale_cover_conversion_temp_but_not_other_cover_files(self):
        job = self.store.enqueue("cover", {})
        with self.store.connection() as conn:
            conn.execute("UPDATE jobs SET state='succeeded' WHERE id=?", (job["id"],))
        self.store.put("asset", {"id": "asset_1", "path": str(self.root / "recordings" / "imports" / "source.mp4")})
        cover_dir = self.root / "covers" / "asset_1"
        temporary = self.old(cover_dir / f"{job['id']}.tmp.jpg")
        image = self.old(cover_dir / f"{job['id']}.jpg")
        original = self.old(cover_dir / f"{job['id']}.original.png")

        cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertFalse(temporary.exists())
        self.assertTrue(image.exists())
        self.assertTrue(original.exists())

    def test_failed_directory_removal_does_not_report_deleted_bytes(self):
        export = self.old(self.root / "exports" / "export_locked")

        with patch("app.cleanup.Path.unlink", side_effect=PermissionError("busy")):
            result = cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertTrue(export.exists())
        self.assertEqual(result["deleted_directories"], 0)
        self.assertEqual(result["deleted_files"], 0)
        self.assertEqual(result["bytes_reclaimed"], 0)
        self.assertEqual(result["retained_files"], 1)

    def test_partial_directory_removal_reports_only_files_actually_removed(self):
        export = self.old(self.root / "exports" / "export_partially_locked")

        with patch("app.cleanup.Path.rmdir", side_effect=PermissionError("busy")):
            result = cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertTrue(export.exists())
        self.assertFalse((export / "output.bin").exists())
        self.assertEqual(result["deleted_directories"], 0)
        self.assertEqual(result["deleted_files"], 1)
        self.assertEqual(result["bytes_reclaimed"], 3)
        self.assertEqual(result["retained_files"], 0)


if __name__ == "__main__":
    unittest.main()
