import asyncio
import contextlib
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from itertools import product
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import main
from lib.browser_bandwidth import BrowserBandwidth
from lib.browser_signup import BrowserSignupClient
from lib.http_signup import SignupFailed
from lib.shutdown import StopRequested
import signal


class BandwidthTests(unittest.TestCase):
    def test_cookie_confirmation_ignores_old_challenge_and_other_domains(self):
        client = BrowserSignupClient()
        client.page = Mock()
        client.collector.block = "captcha"
        client.page.run_cdp.return_value = {"cookies": [
            {"name": ".ROBLOSECURITY", "value": "local-login-cookie", "domain": ".roblox.com"},
            {"name": ".ROBLOSECURITY", "value": "unrelated-cookie", "domain": ".example.com"},
        ]}
        result = client.confirmed_result()
        self.assertEqual([c["value"] for c in result["cookies"]], ["local-login-cookie"])
        client.page.run_cdp.assert_called_once_with("Storage.getCookies", _timeout=2)

    def test_late_cookie_exports_success_without_premature_failure(self):
        for mode, export_mode in product(("terminal", "browser"), ("append", "dated")):
            with self.subTest(mode=mode, export_mode=export_mode), tempfile.TemporaryDirectory() as directory, \
                    contextlib.chdir(directory), contextlib.redirect_stdout(io.StringIO()):
                client = Mock()
                client.submit.side_effect = main.CaptchaDetected("CAPTCHA detected")
                client.confirmed_result.side_effect = [None, {"user_id": None, "cookies": [
                    {"name": ".ROBLOSECURITY", "value": "local-login-cookie", "domain": ".roblox.com"}]}]
                client.bandwidth.return_value = dict(response_encoded_bytes=20, response_decoded_bytes=40,
                    request_body_bytes=10, request_count=2, http_bytes_estimate=35, not_modified=0)
                settings = dict(account_count=1, proxies=[], fixed_password="local-password", name_format="test",
                                scrambled=True, birthday="2000-01-01", user_agent="test")
                with patch.object(main, "gather_config", return_value=settings), \
                        patch("lib.browser_signup.BrowserSignupClient", return_value=client) as factory, \
                        patch("main.asyncio.sleep", new_callable=AsyncMock):
                    code = asyncio.run(main.main(1, mode=mode, export_mode=export_mode))
                self.assertEqual(code, 0)
                self.assertEqual(factory.call_args.kwargs["headless"], mode == "terminal")
                root = Path("output") if export_mode == "append" else next(Path("output").iterdir())
                self.assertEqual((root / "cookies.txt").read_text(), "local-login-cookie\n")
                failure = root / "userfail.txt" if export_mode == "append" else root / "failed.txt"
                self.assertEqual(failure.read_text(), "")
                metrics = json.loads((root / "bandwidth.jsonl").read_text())
                self.assertEqual(metrics["status"], "success")
                self.assertEqual(metrics["mode"], mode)
                self.assertEqual(metrics["transport"], "browser")
                self.assertEqual(metrics["headless"], mode == "terminal")
                self.assertIn("local-login-cookie", Path("cookies.json").read_text())
                logs = (root / "process.log").read_text()
                self.assertIn("SIGNUP CONFIRMED", logs)
                self.assertNotIn("local-login-cookie", logs)
                self.assertNotIn("local-password", logs)
                client.submit.assert_called_once()
                client.finish_round.assert_called_once()

    def test_interrupt_preserves_cookie_found_before_browser_closes(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                contextlib.redirect_stdout(io.StringIO()):
            client = Mock()
            client.submit.side_effect = main.CaptchaDetected("CAPTCHA detected")
            client.confirmed_result.return_value = {"user_id": None, "cookies": [
                {"name": ".ROBLOSECURITY", "value": "local-login-cookie"}]}
            client.bandwidth.return_value = dict(response_encoded_bytes=20, response_decoded_bytes=40,
                request_body_bytes=10, request_count=2, http_bytes_estimate=35, not_modified=0)
            settings = dict(account_count=1, proxies=[], fixed_password="local-password", name_format="test",
                            scrambled=True, birthday="2000-01-01", user_agent="test")
            with patch.object(main, "gather_config", return_value=settings), \
                    patch("lib.browser_signup.BrowserSignupClient", return_value=client), \
                    patch.object(main, "wait_before_captcha_close", new_callable=AsyncMock,
                                 side_effect=StopRequested(signal.SIGTERM)):
                code = asyncio.run(main.main(1, mode="browser", export_mode="append"))
            self.assertEqual(code, 128 + signal.SIGTERM)
            self.assertEqual(Path("output/cookies.txt").read_text(), "local-login-cookie\n")
            self.assertTrue(Path("output/userpass.txt").read_text())
            self.assertEqual(Path("output/userfail.txt").read_text(), "")
            client.finish_round.assert_called_once()

    def test_captcha_countdown_waits_fifteen_seconds_and_keeps_measuring(self):
        async def exercise():
            clock = [100.0]
            client, logger, progress = Mock(), Mock(), Mock()
            client.account_label = "1/1"
            client.confirmed_result.return_value = None
            client.bandwidth.side_effect = [{"response_encoded_bytes": index * 10} for index in range(1, 16)]

            async def advance(seconds):
                clock[0] += seconds

            with patch("main.time.monotonic", side_effect=lambda: clock[0]), \
                    patch("main.asyncio.sleep", new_callable=AsyncMock, side_effect=advance):
                await main.wait_before_captcha_close(client, logger, progress, seconds=15)
            self.assertEqual(clock[0], 115.0)
            self.assertIn("15s", progress.stage.call_args_list[0].args[0])
            self.assertIn("1s", progress.stage.call_args_list[-1].args[0])
            self.assertEqual(logger.info.call_args.args[-1], 150)
            client.finish_round.assert_not_called()
        asyncio.run(exercise())

    def test_compressed_assets_redirects_and_partial_failures_without_double_counting(self):
        tracker = BrowserBandwidth("http://localhost/signup")
        tracker.request("1", dict(url="http://localhost/start?secret=hidden", method="GET", headers={}))
        tracker.request("1", dict(url="http://localhost/form", method="GET", headers={}),
                        redirectResponse=dict(status=302, headers={}, encodedDataLength=120))
        tracker.response("1", dict(status=200, headers={"Content-Encoding": "gzip"}))
        tracker.data("1", dataLength=1000, encodedDataLength=50)
        tracker.finished("1", encodedDataLength=180)
        tracker.request("2", dict(url="http://localhost/script.js", method="GET", headers={}), type="Script")
        tracker.data("2", dataLength=70, encodedDataLength=30)
        tracker.failed("2", errorText="net::ERR_CONNECTION_RESET")
        metrics = tracker.snapshot()
        self.assertEqual(metrics["request_count"], 3)
        self.assertEqual(metrics["response_encoded_bytes"], 120 + 180 + 30)
        self.assertEqual(metrics["response_decoded_bytes"], 1070)
        self.assertEqual(metrics["pending_requests"], 0)
        self.assertNotIn("secret", json.dumps(metrics))

    def test_caches_challenge_headers_and_secret_redaction(self):
        tracker = BrowserBandwidth("http://localhost/signup")
        tracker.request("1", dict(url="http://localhost/signup", method="POST",
                                  headers={"Cookie": "test-secret-cookie"}, postData="test-secret-password"))
        tracker.response("1", dict(status=403, headers={"rblx-challenge-id": "test-secret-token"}))
        tracker.finished("1", encodedDataLength=200)
        tracker.request("2", dict(url="http://localhost/asset", method="GET", headers={}))
        tracker.cached("2")
        tracker.response("2", dict(status=200, headers={}, fromDiskCache=True))
        tracker.finished("2", encodedDataLength=0)
        tracker.request("3", dict(url="http://localhost/stalled", method="GET", headers={}))
        result = tracker.snapshot()
        self.assertEqual(tracker.block, "captcha")
        self.assertEqual(result["response_encoded_bytes"], 200)
        self.assertEqual(result["cached_requests"], 1)
        self.assertEqual(result["pending_requests"], 1)
        self.assertNotIn("test-secret", json.dumps(result))

    def test_proxy_extension_removed_and_only_answers_proxy_challenges(self):
        client = BrowserSignupClient()
        client._auth_extension(("test-user", "test-pass"))
        directory = client.extension_path
        script = (directory / "background.js").read_text()
        self.assertIn("details.isProxy", script)
        self.assertIn('"password": "test-pass"', script)
        client.close()
        self.assertFalse(directory.exists())

    def test_disconnected_browser_still_closes_and_keeps_counters(self):
        client = BrowserSignupClient()
        client.page = Mock()
        client.page.run_cdp.side_effect = RuntimeError("disconnected")
        chrome = client.chrome = Mock()
        driver = client.collector.driver = Mock()
        client.collector.previous_callbacks = {"Network.dataReceived": None}
        client.collector.request("1", dict(url="http://localhost/form", method="GET", headers={}))
        client.collector.data("1", dataLength=100, encodedDataLength=40)
        client.finish_round()
        chrome.quit.assert_called_once_with(timeout=5, force=True, del_data=True)
        driver.set_callback.assert_called_once_with("Network.dataReceived", None)
        self.assertIsNone(client.chrome)
        self.assertEqual(client.bandwidth()["response_encoded_bytes"], 40)


class BrowserHandler(BaseHTTPRequestHandler):
    submitted = []
    outcome = "success"
    form = b'''<!doctype html><html><head><link rel="stylesheet" href="/style.css"></head><body>
      <form id="signup"><select id="MonthDropdown"><option value="Jan">Jan</option></select>
      <select id="DayDropdown"><option value="01">01</option></select>
      <select id="YearDropdown"><option value="2000">2000</option></select>
      <input id="signup-username"><input id="signup-password" type="password">
      <input id="signup-checkbox" type="checkbox"><button id="signup-button">Sign Up</button></form>
      <img src="/image.svg"><script src="/script.js"></script></body></html>'''
    script = b'''document.querySelector('#signup').onsubmit = async (event) => {
      event.preventDefault();
      await fetch('/signup', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        username:document.querySelector('#signup-username').value,
        password:document.querySelector('#signup-password').value,
        month:document.querySelector('#MonthDropdown').value,
        day:document.querySelector('#DayDropdown').value,
        year:document.querySelector('#YearDropdown').value,
        terms:document.querySelector('#signup-checkbox').checked})});
      };'''

    def log_message(self, *args):
        pass

    def send_body(self, body, content_type, status=200, headers=None):
        encoded = gzip.compress(body)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/form":
            self.send_body(self.form, "text/html")
        elif self.path == "/script.js":
            script = self.script
            if self.outcome == "captcha_then_success":
                script = script.replace(b"await fetch('/signup'", b"const response = await fetch('/signup'")
                script = script.replace(b"terms:document.querySelector('#signup-checkbox').checked})});",
                    b"terms:document.querySelector('#signup-checkbox').checked})});"
                    b"if(response.headers.get('x-test-complete')) setTimeout(() => fetch('/finish'), 1000);")
            self.send_body(script, "application/javascript")
        elif self.path == "/finish":
            self.send_body(b'{"userId":123}', "application/json", headers={"Set-Cookie": ".ROBLOSECURITY=test-cookie; Path=/"})
        elif self.path == "/style.css":
            self.send_body(b"body {padding:30px} input {display:block}", "text/css")
        elif self.path == "/image.svg":
            self.send_body(b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>', "image/svg+xml")
        else:
            self.send_body(b"", "text/plain", 404)

    def do_POST(self):
        self.submitted.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        if self.outcome in {"captcha", "captcha_then_success"}:
            self.send_body(b'{"errors":[{"code":2}]}', "application/json", 403,
                           {"rblx-challenge-id": "local-test-challenge", "x-test-complete": "1"})
        elif self.outcome == "failure":
            self.send_body(b'{"errors":[{"code":1,"message":"Invalid username"}]}', "application/json", 400)
        else:
            self.send_body(b'{"userId":123}', "application/json", headers={"Set-Cookie": ".ROBLOSECURITY=test-cookie; Path=/"})


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Set RUN_BROWSER_TESTS=1 for visible Chromium/local-server tests")
class BrowserIntegrationTests(unittest.TestCase):
    def setUp(self):
        BrowserHandler.submitted = []
        BrowserHandler.outcome = "success"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BrowserHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.client = BrowserSignupClient(browser_path=os.environ.get("BROWSER_PATH", ""),
                                          page_url=self.url + "/form", signup_url=self.url + "/signup")
        self.client.request_timeout = 15
        self.addCleanup(self.client.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_visible_form_and_all_page_assets_counted_and_reset(self):
        for _ in range(2):
            self.client.begin_round()
            self.client.load_form()
            self.assertFalse(any(c["name"] == ".ROBLOSECURITY" for c in self.client.page.cookies()))
            result = self.client.submit("local_test", "local_password", "2000-01-01T00:00:00.000Z")
            self.assertEqual(result["cookies"][0]["value"], "test-cookie")
            self.client.finish_round()
            metrics = self.client.bandwidth()
            endpoints = [row["endpoint"].split(self.url)[-1] for row in metrics["requests"]]
            for path in ("/form", "/script.js", "/style.css", "/image.svg", "/signup"):
                self.assertIn(path, endpoints)
            self.assertEqual(endpoints.count("/signup"), 1)
            self.assertGreater(metrics["response_encoded_bytes"], 0)
            self.assertGreater(metrics["response_decoded_bytes"], len(BrowserHandler.form))
            self.assertNotIn("local_password", json.dumps(metrics))
        self.assertEqual(BrowserHandler.submitted, [dict(username="local_test", password="local_password",
                                                       month="Jan", day="01", year="2000", terms=True)] * 2)

    def test_last_captcha_attempt_exports_failure(self):
        self.check_unresolved_captcha("browser")

    def test_headless_unresolved_captcha_waits_then_exports_failure(self):
        self.client.headless = True
        self.check_unresolved_captcha("terminal")

    def check_unresolved_captcha(self, mode):
        BrowserHandler.outcome = "captcha"
        settings = dict(account_count=1, proxies=[], fixed_password="local_password", name_format="local",
                        scrambled=True, birthday="2000-01-01", user_agent="ignored-in-browser", captcha_wait=(15, 15))
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                patch.object(main, "gather_config", return_value=settings), \
                patch("lib.browser_signup.BrowserSignupClient", return_value=self.client), contextlib.redirect_stdout(io.StringIO()):
            started = time.monotonic()
            code = asyncio.run(main.main(1, mode=mode, export_mode="append", request_timeout=15))
            self.assertGreaterEqual(time.monotonic() - started, 15)
            self.assertEqual(code, 1)
            self.assertEqual(len(BrowserHandler.submitted), 1)
            metrics = json.loads(Path("output/bandwidth.jsonl").read_text())
            self.assertEqual(metrics["mode"], mode)
            self.assertEqual(metrics["headless"], mode == "terminal")
            self.assertEqual(metrics["status"], "failed")
            self.assertGreater(metrics["response_encoded_bytes"], 0)
            self.assertTrue(Path("output/userfail.txt").read_text())
            self.assertEqual(Path("output/userpass.txt").read_text(), "")
            logs = Path("output/process.log").read_text()
            self.assertIn("ACCOUNT FAILED", logs)
            self.assertNotIn("local_password", logs)
            self.assertIsNone(self.client.chrome)

    def test_server_rejection_raises_without_resubmitting(self):
        BrowserHandler.outcome = "failure"
        self.client.begin_round()
        self.client.load_form()
        with self.assertRaisesRegex(SignupFailed, "HTTP 400"):
            self.client.submit("local_test", "local_password", "2000-01-01T00:00:00.000Z")
        self.client.finish_round()
        self.assertEqual(len(BrowserHandler.submitted), 1)
        self.assertGreater(self.client.bandwidth()["response_encoded_bytes"], 0)

    def test_signup_completed_during_captcha_wait_exports_cookie(self):
        self.check_completed_during_captcha("browser")

    def test_headless_signup_completed_during_captcha_wait_exports_cookie(self):
        self.client.headless = True
        self.check_completed_during_captcha("terminal")

    def check_completed_during_captcha(self, mode):
        BrowserHandler.outcome = "captcha_then_success"
        settings = dict(account_count=1, proxies=[], fixed_password="local_password", name_format="local",
                        scrambled=True, birthday="2000-01-01", user_agent="ignored-in-browser", captcha_wait=(15, 15))
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                patch.object(main, "gather_config", return_value=settings), \
                patch("lib.browser_signup.BrowserSignupClient", return_value=self.client), contextlib.redirect_stdout(io.StringIO()):
            # Check the actual engine reports headless mode when launched.
            original_load = self.client.load_form
            def load_and_check():
                original_load()
                agent = self.client.page.run_cdp("Browser.getVersion")["userAgent"]
                self.assertEqual("HeadlessChrome" in agent, mode == "terminal")
            with patch.object(self.client, "load_form", side_effect=load_and_check):
                code = asyncio.run(main.main(1, mode=mode, export_mode="append", request_timeout=15))
            self.assertEqual(code, 0)
            self.assertEqual(len(BrowserHandler.submitted), 1)
            self.assertEqual(Path("output/cookies.txt").read_text(), "test-cookie\n")
            self.assertTrue(Path("output/userpass.txt").read_text())
            self.assertEqual(Path("output/userfail.txt").read_text(), "")
            self.assertEqual(json.loads(Path("output/bandwidth.jsonl").read_text())["status"], "success")
            self.assertIn("SIGNUP CONFIRMED", Path("output/process.log").read_text())
            self.assertIsNone(self.client.chrome)
