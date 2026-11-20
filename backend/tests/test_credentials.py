import os
import sys
import unittest
from unittest.mock import patch

from app.core import credentials


class CredentialStoreTests(unittest.TestCase):
    """The desktop keeps cloud keys in Windows Credential Manager, never in the work directory."""

    service = "DouyinCut 验收测试"
    name = "llm_api_key"

    def tearDown(self):
        credentials.write_secret(self.name, "", service=self.service)

    def test_secret_survives_a_new_process_and_clears_with_an_empty_value(self):
        credentials.write_secret(self.name, "sk-isolated-验收-value", service=self.service)
        self.assertEqual(credentials.read_secret(self.name, service=self.service), "sk-isolated-验收-value")
        credentials.write_secret(self.name, "", service=self.service)
        self.assertIsNone(credentials.read_secret(self.name, service=self.service))

    def test_environment_override_wins_and_never_writes_back(self):
        credentials.write_secret(self.name, "stored-value", service=self.service)
        with patch.dict(os.environ, {"DOUYINCUT_LLM_API_KEY": "env-value"}), \
                patch.object(credentials, "write_secret") as write:
            self.assertEqual(credentials.read_secret(self.name, service=self.service), "env-value")
        write.assert_not_called()
        self.assertEqual(credentials.read_secret(self.name, service=self.service), "stored-value")

    def test_storage_reports_the_native_backend_without_exposing_values(self):
        summary = credentials.storage_status()
        self.assertTrue(summary["available"], summary)
        self.assertNotIn("stored-value", str(summary))
        self.assertEqual(set(summary), {"available", "backend"})
        self.assertEqual(summary["backend"], "keyring.backends.Windows.WinVaultKeyring")


class CredentialServiceTests(unittest.TestCase):
    def test_source_runtime_writes_and_deletes_separately_from_frozen_runtime(self):
        store = {("DouyinCut", "llm_api_key"): "installed-key"}
        name = "llm_api_key"
        with patch.dict(os.environ, {"DOUYINCUT_LLM_API_KEY": ""}), \
                patch("keyring.get_password", side_effect=lambda service, key: store.get((service, key))), \
                patch("keyring.set_password", side_effect=lambda service, key, value: store.__setitem__((service, key), value)), \
                patch("keyring.delete_password", side_effect=lambda service, key: store.pop((service, key))):
            with patch.object(sys, "frozen", False, create=True):
                self.assertIsNone(credentials.read_secret(name))
                credentials.write_secret(name, "development-key")
                self.assertEqual(credentials.read_secret(name), "development-key")
            with patch.object(sys, "frozen", True, create=True):
                self.assertEqual(credentials.read_secret(name), "installed-key")
            with patch.object(sys, "frozen", False, create=True):
                credentials.write_secret(name, "")
                self.assertIsNone(credentials.read_secret(name))
            self.assertEqual(store, {("DouyinCut", name): "installed-key"})


if __name__ == "__main__":
    unittest.main()
