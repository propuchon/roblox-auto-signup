import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import config
import main
from lib.proxies import parse_proxy, split_proxy_list

class ProxyTests(unittest.TestCase):
    def test_inline_auth_and_list(self):
        proxy = parse_proxy("alice:p%40ss%3Aword@proxy.example:8080", ("shared", "secret"))
        self.assertEqual(proxy.server, "http://proxy.example:8080")
        self.assertEqual(proxy.auth, ("alice", "p@ss:word"))
        self.assertNotIn("p@ss", repr(proxy))
        self.assertEqual(split_proxy_list("# comment\na:1@host:80,b:2@host:80\n"),
                         ["a:1@host:80", "b:2@host:80"])

    def test_proxy_file_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "proxies.txt"
            file.write_text("alice:one@proxy.example:8080\nbob:two@proxy.example:8080\n")
            with patch.dict(os.environ, {"PROXY_FILE": str(file), "PROXIES": "alice:one@proxy.example:8080"}, clear=True):
                settings = main.gather_config(1)
        self.assertEqual([p.auth for p in settings["proxies"]], [("alice", "one"), ("bob", "two")])

    def test_invalid_proxy_error_hides_password(self):
        with self.assertRaises(ValueError) as error:
            parse_proxy("alice:secret@proxy.example:bad")
        self.assertNotIn("secret", str(error.exception))

    def test_echo_hides_credentials(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"PROXIES": "alice:secret@proxy.example:8080"}), contextlib.redirect_stdout(output):
            self.assertIn("secret", config.ask("PROXIES", ""))
        self.assertNotIn("secret", output.getvalue())
