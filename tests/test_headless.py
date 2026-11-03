from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

import main


class BrowserModeTests(unittest.IsolatedAsyncioTestCase):
    def test_cli_selects_browser_mode(self):
        self.assertFalse(main.parse_args(["1"]).headless)
        self.assertTrue(main.parse_args(["1", "--headless"]).headless)
        self.assertFalse(main.parse_args(["1", "--no-headless"]).headless)

    def test_browser_options_explicitly_set_both_modes(self):
        for headless in (False, True):
            with self.subTest(headless=headless), patch.object(main, "ChromiumOptions") as options:
                settings = dict(headless=headless, browser_path="", incognito=False)
                main.setup_browser(Mock(), settings, None)
                options.return_value.headless.assert_called_once_with(headless)
                if headless:
                    self.assertIn(call("--window-size", "1280,900"),
                                  options.return_value.set_argument.call_args_list)

    async def test_headless_fills_complete_value_without_typing_delays(self):
        element = Mock()
        with patch.object(main.asyncio, "sleep", AsyncMock()) as sleep:
            await main.fill_field(element, "test_value", headless=True)
        self.assertEqual(element.method_calls,
                         [call.clear(), call.input("test_value", clear=False)])
        sleep.assert_not_awaited()

    async def test_windowed_mode_keeps_character_input(self):
        element = Mock()
        with patch.object(main.asyncio, "sleep", AsyncMock()):
            await main.fill_field(element, "abc", headless=False)
        element.clear.assert_called_once()
        self.assertEqual(element.input.call_args_list,
                         [call(char, clear=False) for char in "abc"])

    async def test_headless_verification_stops_without_clicking_or_retrying(self):
        challenge = NS(states=NS(is_displayed=True))
        page = Mock(url="https://www.roblox.com/CreateAccount")
        page.eles.return_value = [challenge]
        with self.assertRaisesRegex(main.CaptchaDetected, "CAPTCHA detected"), \
                patch.object(main.asyncio, "sleep", AsyncMock()) as sleep:
            await main.wait_for_signup(page, headless=True)
        sleep.assert_not_awaited()
        page.ele.assert_not_called()
        page.run_js.assert_not_called()

    async def test_headless_success_requires_login_cookie(self):
        page = Mock(url="https://www.roblox.com/home")
        page.cookies.return_value = [{"name": ".ROBLOSECURITY", "value": "test"}]
        await main.wait_for_signup(page, headless=True)
        page.cookies.return_value = []
        with self.assertRaisesRegex(RuntimeError, "no login cookie"):
            await main.wait_for_signup(page, headless=True)

    async def test_headless_timeout_with_hidden_challenge(self):
        page = Mock(url="https://www.roblox.com/CreateAccount")
        page.eles.return_value = [NS(states=NS(is_displayed=False))]
        page.cookies.return_value = []
        with patch.object(main, "SIGNUP_TIMEOUT", 1), \
                patch.object(main.time, "monotonic", side_effect=[0, 0, 2]), \
                patch.object(main.asyncio, "sleep", AsyncMock()), \
                self.assertRaisesRegex(RuntimeError, "--no-headless"):
            await main.wait_for_signup(page, headless=True)


if __name__ == "__main__":
    unittest.main()
