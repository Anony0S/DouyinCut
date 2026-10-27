"""Exercise the media preparation CLI without changing the real staged tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import skipUnless


REPO = Path(__file__).resolve().parents[2]
MEDIA = REPO / '.runtime/windows-packaging/media'


@skipUnless(os.name == 'nt' and (MEDIA / 'ffmpeg.exe').is_file(),
            'Requires Windows and the pinned media tools')
class WindowsMediaPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / '构建 空格'
        self.script = self.root / 'scripts/prepare-windows-media.py'
        self.script.parent.mkdir(parents=True)
        shutil.copy2(REPO / 'scripts/prepare-windows-media.py', self.script)
        packaging = self.root / 'packaging/windows'
        packaging.mkdir(parents=True)
        shutil.copy2(REPO / 'packaging/windows/media-manifest.json', packaging)
        self.source = self.root / 'upstream'
        (self.source / 'bin').mkdir(parents=True)
        for name in ('ffmpeg.exe', 'ffprobe.exe', 'LICENSE', 'README.txt'):
            destination = self.source / ('bin' if name.endswith('.exe') else '') / name
            if name.endswith('.exe'):
                os.link(MEDIA / name, destination)
            else:
                shutil.copy2(MEDIA / name, destination)
        self.target = self.root / '.runtime/windows-packaging/media'
        self.target.mkdir(parents=True)
        (self.target / 'previous-resource.txt').write_text('previous valid resources', encoding='utf-8')

    def prepare(self):
        return subprocess.run([sys.executable, str(self.script), '--source', str(self.source)],
                              capture_output=True, text=True, encoding='utf-8', timeout=45,
                              env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})

    def write_inventory(self):
        inventory = {
            'version': '8.1-full_build-www.gyan.dev',
            'readme_sha256': '557205ec68086511f87c0c2ca7ba077d4c687f71e55f3165aed425ebe704b5d8',
            'redistribution_ready': False,
        }
        path = self.root / 'packaging/windows/media-sources.json'
        path.write_text(json.dumps(inventory), encoding='utf-8')
        return path

    def test_missing_source_inventory_refuses_preparation_and_keeps_previous_resources(self):
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('source inventory', result.stderr.lower())
        self.assertEqual((self.target / 'previous-resource.txt').read_text(encoding='utf-8'),
                         'previous valid resources')

    def test_prepared_source_inventory_is_published_with_a_verified_hash(self):
        self.write_inventory()
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['redistribution_ready'])
        sys.path.insert(0, str(REPO / 'backend'))
        from app.bundled_media import bundled_settings
        settings = bundled_settings(self.root / 'work', self.target.parent)
        self.assertEqual(Path(settings.ffmpeg), self.target / 'ffmpeg.exe')
        (self.target / 'source-inventory.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'source-inventory.json'):
            bundled_settings(self.root / 'work', self.target.parent)

    def test_missing_upstream_license_does_not_replace_previous_resources(self):
        self.write_inventory()
        (self.source / 'LICENSE').unlink()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LICENSE', result.stderr)
        self.assertEqual(list(self.target.iterdir()), [self.target / 'previous-resource.txt'])

    def test_tampered_upstream_readme_does_not_replace_previous_resources(self):
        self.write_inventory()
        (self.source / 'README.txt').write_text('altered provenance', encoding='utf-8')
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Pinned media hash mismatch', result.stderr)
        self.assertEqual(list(self.target.iterdir()), [self.target / 'previous-resource.txt'])

    def test_inventory_for_another_version_is_rejected(self):
        path = self.write_inventory()
        inventory = json.loads(path.read_text(encoding='utf-8'))
        inventory['version'] = 'other-build'
        path.write_text(json.dumps(inventory), encoding='utf-8')
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match', result.stderr)
        self.assertTrue((self.target / 'previous-resource.txt').is_file())

    def test_tampered_source_inventory_is_rejected_before_runtime_creation(self):
        self.write_inventory()
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)

        sys.path.insert(0, str(REPO / 'backend'))
        from app.bundled_media import bundled_settings
        staged_resources = self.target.parent
        inventory = self.target / 'source-inventory.json'
        inventory.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'source-inventory.json'):
            bundled_settings(self.root / 'work', staged_resources)
        self.assertFalse((self.root / 'work').exists())


if __name__ == '__main__':
    unittest.main()
