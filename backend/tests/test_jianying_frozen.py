import unittest
from pathlib import Path


class JianyingFrozenResourceTests(unittest.TestCase):
    def test_jianying_templates_are_accessible_in_frozen_runtime(self):
        """Verify that pyJianYingDraft can locate its template resources.

        This test checks that the assets module correctly resolves template paths
        using Path(__file__).parent, which works in both development and frozen
        PyInstaller environments.
        """
        from app._vendor.pyJianYingDraft import assets

        # All template files should be accessible
        for asset_name in assets.ASSET_FILES:
            with self.subTest(asset=asset_name):
                path = assets.get_asset_path(asset_name)
                self.assertTrue(path.exists(), f"{asset_name} at {path} does not exist")
                self.assertGreater(path.stat().st_size, 0, f"{asset_name} is empty")

    def test_draft_folder_copies_templates_on_creation(self):
        """Verify that DraftFolder copies template files when creating a draft."""
        import tempfile
        from app._vendor.pyJianYingDraft import DraftFolder

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = DraftFolder(str(root))

            # Create a minimal draft
            draft_name = "test_draft"
            draft.create_draft(draft_name, width=1920, height=1080, fps=30)

            # Verify the draft structure was created with required template files
            draft_path = root / draft_name
            self.assertTrue(draft_path.exists(), "Draft directory not created")
            self.assertTrue((draft_path / "draft_meta_info.json").exists(), "draft_meta_info.json missing")
            self.assertTrue((draft_path / "draft_settings").exists(), "draft_settings missing")
            self.assertTrue((draft_path / "key_value.json").exists(), "key_value.json missing")

            # Verify template content was copied (not empty)
            meta = (draft_path / "draft_meta_info.json").read_text(encoding="utf-8")
            self.assertIn('"draft_name"', meta, "draft_meta_info.json is not valid JSON")


if __name__ == "__main__":
    unittest.main()
