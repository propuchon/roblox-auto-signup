import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import main
from lib.lib import Main


class Options:
    def __getattr__(self, name):
        return lambda *args, **kwargs: self


class Bar:
    n = 0

    def set_description(self, *args, **kwargs):
        pass

    def update(self, value):
        self.n += value

    def close(self):
        pass


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def run_workflow(self, mode):
        events, browsers, elapsed = [], [], [0]

        class Library(Main):
            async def checkUpdate(self):
                return "test"

            def usernameCreator(self, *args, **kwargs):
                return "test_user"

            async def saveAccount(self, account):
                events.append("save")

            async def saveTiming(self, username, status, seconds, stages):
                events.append(status)

        element = NS(click=lambda: None, input=lambda *a: None,
                     select=NS(by_value=lambda *a: None), states=NS(is_displayed=True))

        class Page:
            submitted_at = None
            listen = NS(start=lambda *a, **k: None, wait=lambda *a, **k: [])
            set = NS(window=NS(max=lambda: None), cookies=NS(clear=lambda: None))

            def get(self, url):
                pass

            def run_js_loaded(self, *args):
                return "en-US"

            @property
            def url(self):
                success = self.submitted_at is not None and mode != "manual_timeout"
                if mode == "manual":
                    success = success and elapsed[0] - self.submitted_at >= 20
                return "https://www.roblox.com/home" if success else "https://www.roblox.com/CreateAccount"

            def ele(self, selector, *args, **kwargs):
                if "signup-button" in selector:
                    return NS(click=lambda: setattr(self, "submitted_at", elapsed[0]))
                if "korea-compliance" in selector:
                    return False
                if selector.startswith("xpath:"):
                    return False
                return element

            def get_frames(self, **kwargs):
                return []

            def cookies(self):
                return [{"name": ".ROBLOSECURITY", "value": "test_cookie"}] if "/home" in self.url else []

            def clear_cache(self):
                events.append("cleanup")
                if mode == "cleanup_error":
                    raise RuntimeError("Simulated cleanup failure")

        def browser(*args, **kwargs):
            if browsers:
                raise AssertionError("A second browser should not start")
            page = Page()
            browsers.append(page)
            return NS(latest_tab=page, set=NS(cookies=NS(clear=lambda: None)),
                      clear_cache=lambda: None, quit=lambda: events.append("quit"))

        async def sleep(seconds):
            elapsed[0] += seconds

        answers = dict(PASSWORD="", BROWSER_PATH="", EMAIL_VERIFICATION="n", NAME_PREFIX="",
                       SCRAMBLED_USERNAME="y", CUSTOMIZATION="n", FOLLOW_USERS="", PROXIES="",
                       INCOGNITO="n", ACCOUNT_COUNT="1", EXPORT_TO_CLIPBOARD="n")
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                patch.multiple(main, Main=Library, ChromiumOptions=Options, Chromium=browser,
                               tqdm=lambda **k: Bar(), ask=lambda key, prompt: answers[key]), \
                patch.object(main.sys, "argv", ["main.py"]), \
                patch.object(main.os, "environ", {}), \
                patch.object(main.asyncio, "sleep", sleep), \
                patch.object(main.time, "monotonic", lambda: elapsed[0]), \
                contextlib.redirect_stdout(io.StringIO()):
            await main.main()
        return events, browsers, elapsed[0]

    async def test_plain_invocation_saves_before_cleanup(self):
        events, browsers, _ = await self.run_workflow("normal")
        self.assertEqual(len(browsers), 1)
        self.assertLess(events.index("save"), events.index("cleanup"))
        self.assertIn("success", events)

    async def test_cleanup_failure_keeps_saved_account_and_closes(self):
        events, _, _ = await self.run_workflow("cleanup_error")
        self.assertIn("save", events)
        self.assertIn("quit", events)
        self.assertIn("success", events)

    async def test_manual_verification_continues_when_user_finishes(self):
        events, browsers, elapsed = await self.run_workflow("manual")
        self.assertEqual(len(browsers), 1)
        self.assertIn("success", events)
        self.assertLess(elapsed, 180)

    async def test_unfinished_signup_fails_after_180_seconds_and_closes(self):
        events, browsers, elapsed = await self.run_workflow("manual_timeout")
        self.assertEqual(elapsed - browsers[0].submitted_at - 5, 180)
        self.assertIn("failed", events)
        self.assertIn("quit", events)
        self.assertNotIn("save", events)

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


if __name__ == "__main__":
    unittest.main()
