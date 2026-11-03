import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib.lib import Main

class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_storage_preserves_existing_data_on_error(self):
        account = dict(username="test", password="test", email=None, emailPassword=None, cookies=[])
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
            await Main().saveAccount(account)
            saved = Path("cookies.json").read_bytes()
            self.assertEqual(json.loads(saved), [account])
            with patch("lib.lib.os.replace", side_effect=OSError("Simulated disk failure")):
                with self.assertRaises(OSError):
                    await Main().saveAccount(account)
            self.assertEqual(Path("cookies.json").read_bytes(), saved)
            self.assertFalse(list(Path(".").glob(".cookies-*.tmp")))
            Path("cookies.json").write_text("invalid json")
            with self.assertRaises(json.JSONDecodeError):
                await Main().saveAccount(account)
            self.assertEqual(Path("cookies.json").read_text(), "invalid json")
