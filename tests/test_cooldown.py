import asyncio
import contextlib
import io
import json
import os
import signal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import main


class CooldownTests(unittest.TestCase):
    def test_default_browser_and_unlimited_count(self):
        self.assertEqual(main.parse_args([]).mode, "browser")
        self.assertEqual(main.parse_args(["0"]).count, 0)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main.gather_config()["account_count"], 0)
        with patch.dict(os.environ, {"ACCOUNT_COUNT": "4"}, clear=True):
            self.assertEqual(main.gather_config()["account_count"], 4)
            self.assertEqual(main.gather_config(0)["account_count"], 0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main.parse_args(["-1"])
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            main.gather_config(-1)

    def test_unlimited_continues_after_failure_and_exports_before_stop(self):
        success = dict(user_id=1, cookies=[])
        client = self.client([success, main.CaptchaDetected("CAPTCHA"), success])
        pauses = []

        async def pause(bounds, status, next_index, total, *args):
            self.assertEqual(total, 0)
            self.assertEqual(client.finish_round.call_count, next_index - 1)
            pauses.append((bounds, status))
            if len(pauses) == 3:
                raise main.StopRequested(signal.SIGTERM)

        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch.object(main, "gather_config", return_value=self.settings(0)), \
                patch("lib.browser_signup.BrowserSignupClient", return_value=client) as factory, \
                patch.object(main, "wait_before_captcha_close", new_callable=AsyncMock, return_value=None), \
                patch.object(main, "sleep_between_rounds", new_callable=AsyncMock, side_effect=pause):
            code = asyncio.run(main.main(0))
            self.assertEqual(code, 128 + signal.SIGTERM)
            self.assertFalse(factory.call_args.kwargs["headless"])
            snapshot = next(Path("output").glob("*/results.json"))
            saved = json.loads(snapshot.read_text())
            self.assertEqual(saved["requested_count"], 0)
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual([row["status"] for row in saved["results"]], ["success", "failed", "success"])
            self.assertIn("[3/∞]", (snapshot.parent / "process.log").read_text())
            client.close.assert_called_once()
        self.assertEqual(pauses, [((30, 45), "success"), ((60, 100), "failed"), ((30, 45), "success")])

    def test_ranges_from_environment_and_validation(self):
        with patch.dict(os.environ, {"RANDOM_WHEN_DONE": "30, 45", "RANDOM_WHEN_FAILED": "60,100", "CAPTCHA_BEFORE_CLOSE": "7.5, 15"}, clear=True):
            config = main.gather_config(1)
            self.assertEqual(config["done_delay"], (30, 45))
            self.assertEqual(config["failed_delay"], (60, 100))
            self.assertEqual(config["captcha_wait"], (7.5, 15))
        for value in ("-1,2", "45,30", "30", "30,45,60", "nan,5", "0,inf", "bad,5"):
            with self.subTest(value=value), patch.dict(os.environ, {"RANDOM_WHEN_DONE": value}):
                with self.assertRaisesRegex(ValueError, "RANDOM_WHEN_DONE"):
                    main.delay_range("RANDOM_WHEN_DONE", "30,45")
        with patch.dict(os.environ, {"RANDOM_WHEN_DONE": "0,0"}):
            self.assertEqual(main.delay_range("RANDOM_WHEN_DONE", "30,45"), (0, 0))

    def test_random_wait_uses_selected_duration_and_is_interruptible(self):
        async def exercise():
            clock = [100.0]
            logger, progress = Mock(), Mock()

            async def advance(seconds):
                clock[0] += seconds

            with patch("main.random.uniform", return_value=37.5) as choose, \
                    patch("main.time.monotonic", side_effect=lambda: clock[0]), \
                    patch("main.asyncio.sleep", new_callable=AsyncMock, side_effect=advance):
                await main.sleep_between_rounds((30, 45), "success", 2, 3, logger, progress)
            choose.assert_called_once_with(30, 45)
            self.assertEqual(clock[0], 137.5)
            progress.end_cooldown.assert_called_once()
            with patch("main.random.uniform", return_value=60), \
                    patch("main.asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await main.sleep_between_rounds((60, 100), "failed", 3, 3, logger, progress)
            self.assertEqual(progress.end_cooldown.call_count, 2)
        asyncio.run(exercise())

    def client(self, outcomes):
        client = Mock()
        client.submit.side_effect = outcomes
        client.confirmed_result.return_value = None
        client.bandwidth.return_value = dict(request_body_bytes=10, response_encoded_bytes=20,
            response_decoded_bytes=40, request_count=1, http_bytes_estimate=35, not_modified=0)
        return client

    def settings(self, count):
        return dict(account_count=count, proxies=[], fixed_password="local-password", name_format="test",
                    scrambled=True, birthday="2000-01-01", user_agent="test",
                    done_delay=(30, 45), failed_delay=(60, 100))

    def test_export_and_close_before_success_or_captcha_failure_pause(self):
        result = dict(user_id=1, cookies=[{"name": ".ROBLOSECURITY", "value": "local-cookie"}])
        client = self.client([result, main.CaptchaDetected("CAPTCHA detected"), result])
        pauses = []

        async def pause(bounds, status, next_index, total, *args):
            records = [json.loads(line) for line in Path("output/bandwidth.jsonl").read_text().splitlines()]
            self.assertEqual(len(records), next_index - 1)
            self.assertEqual(records[-1]["status"], status)
            self.assertEqual(client.finish_round.call_count, next_index - 1)
            pauses.append((bounds, status, next_index, total))

        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch.object(main, "gather_config", return_value=self.settings(3)), \
                patch("lib.browser_signup.BrowserSignupClient", return_value=client), \
                patch.object(main, "wait_before_captcha_close", new_callable=AsyncMock, return_value=None) as captcha_wait, \
                patch("main.random.uniform", return_value=9.5) as choose, \
                patch.object(main, "sleep_between_rounds", new_callable=AsyncMock, side_effect=pause):
            code = asyncio.run(main.main(3, mode="terminal", export_mode="append"))
            self.assertEqual(code, 1)  # A failed attempt still gives a nonzero run exit code.
            self.assertEqual(client.submit.call_count, 3)
            choose.assert_called_once_with(7, 15)
            self.assertEqual(captcha_wait.await_args.args[-1], 9.5)
            self.assertEqual(len(Path("output/userpass.txt").read_text().splitlines()), 2)
            self.assertEqual(len(Path("output/userfail.txt").read_text().splitlines()), 1)
        self.assertEqual(pauses, [((30, 45), "success", 2, 3), ((60, 100), "failed", 3, 3)])

    def test_last_attempt_and_rate_limit_do_not_pause_or_retry(self):
        success = dict(user_id=1, cookies=[])
        for count, outcome in ((1, success), (1, main.CaptchaDetected("CAPTCHA")),
                               (3, main.RateLimited("Rate limit")),
                               (0, main.RateLimited("Rate limit"))):
            with self.subTest(count=count, outcome=type(outcome).__name__), \
                    tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    patch.object(main, "gather_config", return_value=self.settings(count)), \
                    patch("lib.browser_signup.BrowserSignupClient", return_value=self.client([outcome])) as factory, \
                    patch.object(main, "wait_before_captcha_close", new_callable=AsyncMock, return_value=None), \
                    patch.object(main, "sleep_between_rounds", new_callable=AsyncMock) as pause:
                asyncio.run(main.main(count, mode="terminal", export_mode="append"))
                pause.assert_not_called()
                self.assertEqual(factory.return_value.submit.call_count, 1)
