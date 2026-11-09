"""Release attachments must match the single application version source."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
module_spec = importlib.util.spec_from_file_location("release_preparation", ROOT / "scripts/prepare-github-release.py")
release = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(release)


class GitHubReleaseTests(unittest.TestCase):
    def test_generates_checksum_for_exact_versioned_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = root / "SPEC.md"
            spec.write_text("- 版本：0.2.137\n", encoding="utf-8")
            installer = root / "DouyinCut-0.2.137-setup.exe"
            installer.write_bytes(b"final signed installer bytes")
            checksum = release.prepare(installer, spec)
            self.assertEqual(checksum.name, installer.name + ".sha256")
            self.assertEqual(checksum.read_text(encoding="utf-8"),
                             f"{hashlib.sha256(installer.read_bytes()).hexdigest()}  {installer.name}\n")
            installer.write_bytes(b"changed after signing")
            release.prepare(installer, spec)
            self.assertIn(hashlib.sha256(installer.read_bytes()).hexdigest(), checksum.read_text())

    def test_rejects_version_mismatch_empty_package_and_missing_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = root / "SPEC.md"
            spec.write_text("- 版本：0.2.137\n", encoding="utf-8")
            installer = root / "DouyinCut-0.2.136-setup.exe"
            installer.write_bytes(b"installer")
            with self.assertRaises(ValueError):
                release.prepare(installer, spec)
            installer = installer.rename(root / "DouyinCut-0.2.137-setup.exe")
            installer.write_bytes(b"")
            with self.assertRaises(ValueError):
                release.prepare(installer, spec)
            spec.write_text("missing version", encoding="utf-8")
            with self.assertRaises(ValueError):
                release.prepare(installer, spec)
