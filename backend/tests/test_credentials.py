import os
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
        with patch.object(credentials, "read_secret", side_effect=lambda name, service=credentials.SERVICE:
                          __import__("keyring").get_password(service, name)):
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


if __name__ == "__main__":
    unittest.main()
