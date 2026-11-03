import contextlib
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import config
import main
from lib.lib import Main
from lib.proxies import parse_proxy, split_proxy_list


class ProxyTests(unittest.TestCase):
    def test_inline_credentials_override_shared_defaults(self):
        proxy = parse_proxy("alice:p%40ss%3Aword@proxy.example:8080", ("shared", "secret"))
        self.assertEqual(proxy.server, "http://proxy.example:8080")
        self.assertEqual(proxy.auth, ("alice", "p@ss:word"))
        self.assertNotIn("p@ss", repr(proxy))
        self.assertEqual(parse_proxy("proxy.example:8080", ("shared", "secret")).auth,
                         ("shared", "secret"))
        self.assertIsNone(parse_proxy("127.0.0.1:8080").auth)
        self.assertEqual(parse_proxy("http://[::1]:8080").server, "http://[::1]:8080")

    def test_list_deduplicates_only_identical_endpoint_and_credentials(self):
        entries = split_proxy_list("# comment\nalice:one@proxy.example:8080, bob:two@proxy.example:8080\n"
                                   "http://alice:one@proxy.example:8080\n")
        library = NS(testProxy=Mock(return_value=(True, "ok")))
        with patch.dict(os.environ, {}, clear=True):
            proxies = main.setup_proxies(library, {"proxy_list": entries})
        self.assertEqual(len(proxies), 2)
        self.assertEqual(library.testProxy.call_count, 2)
        self.assertEqual([proxy.auth for proxy in proxies], [("alice", "one"), ("bob", "two")])

    def test_invalid_entries_do_not_expose_credentials(self):
        for value in ("alice:secret@proxy.example:bad", "alice:secret@proxy.example:70000",
                      "alice:secret@proxy.example", "socks5://alice:secret@proxy.example:8080",
                      "http://alice:secret@proxy.example:8080/path"):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                parse_proxy(value)
            self.assertNotIn("secret", str(error.exception))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            main.setup_proxies(NS(testProxy=Mock(return_value=(False, "unreachable"))),
                               {"proxy_list": ["proxy.example:8080"]})

    def test_request_encodes_credentials_and_redacts_exceptions(self):
        with patch("lib.lib.requests.get", return_value=NS(status_code=200)) as get:
            self.assertTrue(Main().testProxy("proxy.example:8080", ("alice", "p@ss:word"))[0])
        self.assertEqual(get.call_args.kwargs["proxies"]["http"],
                         "http://alice:p%40ss%3Aword@proxy.example:8080")
        with patch("lib.lib.requests.get", side_effect=RuntimeError("private_password")):
            success, message = Main().testProxy("proxy.example:8080", ("alice", "private_password"))
        self.assertFalse(success)
        self.assertNotIn("private_password", message)

    def test_config_echo_hides_inline_credentials(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"PROXIES": "alice:secret@proxy.example:8080"}), \
                contextlib.redirect_stdout(output):
            value = config.ask("PROXIES", "")
        self.assertIn("secret", value)
        self.assertNotIn("secret", output.getvalue())


class ProxyWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_file_accepts_one_entry_per_line(self):
        with tempfile.TemporaryDirectory() as directory:
            proxy_file = Path(directory) / "proxies.txt"
            proxy_file.write_text("alice:one@proxy.example:8080\nbob:two@proxy.example:8080\n")
            with patch.dict(os.environ, {"PROXY_FILE": str(proxy_file)}, clear=True), \
                    patch.object(main, "ask", return_value=""):
                settings = await main.gather_config(1)
        self.assertEqual(settings["proxy_list"], ["alice:one@proxy.example:8080", "bob:two@proxy.example:8080"])

    async def test_each_browser_uses_its_own_auth_and_removes_extension(self):
        settings = dict(browser_path="", proxy_list=["alice:one@proxy.example:8080",
                        "bob:two@proxy.example:8080"], incognito=True, account_count=2,
                        fixed_password="test_password", name_format="", scrambled=True,
                        verification=False, customization=False, follow_list=[])
        options, extension_paths, credentials = [], [], []
        library = Main()
        library.checkUpdate = AsyncMock()
        library.usernameCreator = Mock(return_value="test_user")
        library.testProxy = Mock(return_value=(True, "ok"))
        library.saveAccount = AsyncMock()
        library.saveTiming = AsyncMock()

        def extension(username, password):
            credentials.append((username, password))
            path = tempfile.mkdtemp()
            extension_paths.append(path)
            return path

        def make_options():
            option = Mock()
            option.auto_port.return_value = option
            options.append(option)
            return option

        page = NS(url="https://www.roblox.com/home", set=NS(window=NS(max=lambda: None)),
                  eles=lambda *args, **kwargs: [],
                  cookies=lambda: [{"name": ".ROBLOSECURITY", "value": "test"}])
        library.proxyAuthExtension = extension
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(main, "Main", return_value=library), \
                patch.object(main, "gather_config", AsyncMock(return_value=settings)), \
                patch.object(main, "ChromiumOptions", side_effect=make_options), \
                patch.object(main, "Chromium", return_value=NS(latest_tab=page)), \
                patch.object(main, "run_signup", AsyncMock()), \
                patch.object(main, "post_process_account", AsyncMock(return_value=(True, page.cookies()))), \
                patch.object(main, "closeBrowser") as close, \
                patch.object(main, "tqdm", side_effect=lambda **kwargs: Mock(n=0)), \
                patch.object(main, "ask", return_value="n"), \
                patch.object(main.asyncio, "sleep", AsyncMock()), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(await main.main(), 0)
        self.assertEqual(credentials, [("alice", "one"), ("bob", "two")])
        self.assertEqual(len(options), 2)
        for option, path in zip(options, extension_paths):
            option.set_proxy.assert_called_once_with("http://proxy.example:8080")
            option.add_extension.assert_called_once_with(path)
            option.incognito.assert_not_called()
            self.assertFalse(Path(path).exists())
        self.assertEqual(close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
