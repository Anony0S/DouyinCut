import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cleanup import RETENTION_SECONDS, cleanup_unused_files
from app.core.storage import Store


class CleanupUnusedFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "database" / "douyincut.sqlite3")
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
        timestamp = self.now - RETENTION_SECONDS - 1
        for item in [path, *path.rglob("*")] if path.is_dir() else [path]:
            os.utime(item, (timestamp, timestamp))
        return path

    def test_removes_old_unreferenced_generated_outputs(self):
        export = self.old(self.root / "exports" / "export_orphan")
        nested_dir = export / "nested"
        nested_dir.mkdir()
        nested_file = nested_dir / "additional.bin"
        nested_file.write_bytes(b"old")
        timestamp = self.now - RETENTION_SECONDS - 1
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

        with patch("app.cleanup.shutil.rmtree", side_effect=PermissionError("busy")):
            result = cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertTrue(export.exists())
        self.assertEqual(result["deleted_directories"], 0)
        self.assertEqual(result["deleted_files"], 0)
        self.assertEqual(result["bytes_reclaimed"], 0)
        self.assertEqual(result["retained_files"], 1)

    def test_partial_directory_removal_reports_only_files_actually_removed(self):
        export = self.old(self.root / "exports" / "export_partially_locked")

        def remove_one_then_fail(path: Path):
            (path / "output.bin").unlink()
            raise PermissionError("busy")

        with patch("app.cleanup.shutil.rmtree", side_effect=remove_one_then_fail):
            result = cleanup_unused_files(self.store, self.root, now=self.now)

        self.assertTrue(export.exists())
        self.assertFalse((export / "output.bin").exists())
        self.assertEqual(result["deleted_directories"], 0)
        self.assertEqual(result["deleted_files"], 1)
        self.assertEqual(result["bytes_reclaimed"], 3)
        self.assertEqual(result["retained_files"], 0)


if __name__ == "__main__":
    unittest.main()
