import gzip
import asyncio
import contextlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib.http_signup import SignupClient, CaptchaDetected, RateLimited, SignupFailed
from lib.proxies import parse_proxy
from lib.lib import Main
from lib.diagnostics import close_logger, create_logger
import main

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests_seen = []
    form = b'<html><meta name="csrf-token" data-token="page-token"><form>signup</form></html>'
    last_modified = "Mon, 02 Nov 2026 00:00:00 GMT"

    def log_message(self, *args):
        pass

    def respond(self, status, body=b"", headers=None, compressed=True):
        wire = gzip.compress(body) if compressed and body else body
        self.send_response(status)
        self.send_header("Content-Length", str(len(wire)))
        if compressed and body:
            self.send_header("Content-Encoding", "gzip")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(wire)

    def do_GET(self):
        self.requests_seen.append(("GET", self.path, dict(self.headers), None))
        if self.path in ("/slowheaders", "/slowbody"):
            if self.path == "/slowbody":
                self.send_response(200)
                self.send_header("Content-Length", "4")
                self.end_headers()
            time.sleep(0.6)
            try:
                if self.path == "/slowheaders":
                    self.respond(200, b"done")
                else:
                    self.wfile.write(b"done")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.path == "/redirect":
            self.respond(302, b"redirect body", {"Location": "/form"})
            return
        if self.path == "/captcha":
            self.respond(403, b'{"errors":[{"code":2,"message":"Captcha Failed"}]}')
            return
        if self.path == "/partial":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"partial")
            self.close_connection = True
            return
        headers = {"ETag": '"version-1"', "Last-Modified": self.last_modified,
                   "Content-Type": "text/html", "Cache-Control": "public, max-age=0"}
        if self.path == "/private":
            headers["Cache-Control"] = "private, no-store"
        elif self.path == "/sessionform":
            headers["Set-Cookie"] = "flow=page; Path=/"
        elif self.path == "/varycookie":
            headers["Vary"] = "Cookie"
        if self.headers.get("If-None-Match") == '"version-1"':
            self.respond(304, headers=headers)
        else:
            self.respond(200, self.form, headers, compressed=self.path != "/identity")

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
        self.requests_seen.append(("POST", self.path, dict(self.headers), payload))
        if self.path == "/captcha":
            self.respond(403, b'{"errors":[{"code":2}]}', {"rblx-challenge-id": "test-challenge"})
        elif self.path == "/ratelimit":
            self.respond(429, b'{"errors":[{"code":3}]}')
        elif self.path == "/ambiguous":
            self.respond(500, b'{}')
        elif self.path == "/badcsrf":
            self.respond(403, b'{"errors":[{"code":0,"message":"Token Validation Failed"}]}', {"x-csrf-token": "fresh-token"})
        elif self.headers.get("X-CSRF-TOKEN") not in {"page-token", "fresh-token"}:
            self.respond(403, b'{"errors":[{"code":0,"message":"Token Validation Failed"}]}',
                         {"x-csrf-token": "fresh-token", "Set-Cookie": "flow=preflight; Path=/"})
        else:
            self.respond(200, b'{"userId":123}', {"Set-Cookie": ".ROBLOSECURITY=test-cookie; Path=/"})

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        Handler.requests_seen.clear()
        self.client = SignupClient(page_url=self.base + "/form", signup_url=self.base + "/signup")
        self.addCleanup(self.client.close)
        self.client.begin_round()

    def submit(self):
        return self.client.submit("test_user", "test_password", "2000-01-01T00:00:00.000Z")

    def test_gzip_actual_encoded_and_decoded_sizes(self):
        response = self.client.load_form()
        self.assertEqual(response.body, Handler.form)
        metrics = self.client.bandwidth()
        self.assertEqual(metrics["response_encoded_bytes"], len(gzip.compress(Handler.form)))
        self.assertEqual(metrics["response_decoded_bytes"], len(Handler.form))
        self.assertEqual(Handler.requests_seen[0][2]["Accept-Encoding"], "gzip")
        self.assertIn("Mozilla/5.0", Handler.requests_seen[0][2]["User-Agent"])

    def test_wait_heartbeat_and_total_deadline_identify_blocked_phase(self):
        import signal
        if not hasattr(signal, "setitimer"):
            self.skipTest("Hard total deadline requires POSIX main thread")
        for path, phase in (("/slowheaders", "response headers"), ("/slowbody", "response body")):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    logger = create_logger(Path(directory) / "process.log", "test-run")
                    self.client.logger = logger
                    self.client.page_url = self.base + path
                    self.client.request_timeout = 0.25
                    self.client.heartbeat = 0.05
                    self.client.timeout = (2, 2)
                    self.client.begin_round()
                    started = time.monotonic()
                    try:
                        with self.assertRaisesRegex(SignupFailed, "total request timeout") as error:
                            self.client.load_form()
                    finally:
                        close_logger(logger)
                self.assertLess(time.monotonic() - started, 1)
                self.assertIn(phase, str(error.exception))
                logged = (Path(directory) / "process.log").read_text()
                self.assertIn("WAIT", logged)
                self.assertIn("BLOCKED", logged)
                self.assertIn("REQUEST END", logged)
                self.assertIn("+07:00", logged)
                self.assertEqual(self.client.bandwidth()["requests"][0]["error"], "OperationTimeout")
                self.assertFalse(any(t.name == "http-progress" for t in threading.enumerate()))

    def test_diagnostics_do_not_log_credentials_cookies_or_csrf_tokens(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            logger = create_logger(Path(directory) / "process.log", "test-run")
            self.client.logger = logger
            try:
                self.client.load_form()
                self.submit()
            finally:
                close_logger(logger)
            logged = (Path(directory) / "process.log").read_text()
            for secret in ("test_password", "test-cookie", "page-token"):
                self.assertNotIn(secret, logged)
            self.assertIn("RESPONSE HEADERS", logged)
            self.assertIn("HTTP 200", logged)
            self.assertIn('FORM FIELD | username="test_user"', logged)
            self.assertIn('FORM FIELD | birthday="2000-01-01T00:00:00.000Z"', logged)
            self.assertIn("FORM FIELD | gender=0", logged)
            self.assertIn("FORM FIELD | isTosAgreementBoxChecked=true", logged)
            self.assertIn("FORM FIELD | password=[hidden]", logged)
            self.assertLess(logged.index("FORM READY"), logged.index("POST " + self.base))

    def test_one_session_resets_cookies_csrf_and_304_skips_body(self):
        session = self.client.session
        self.client.load_form()
        self.submit()
        self.assertTrue(self.client.session.cookies)
        self.client.begin_round()
        self.assertIs(self.client.session, session)
        self.assertFalse(session.cookies)
        self.assertIsNone(self.client.csrf)
        response = self.client.load_form()
        self.assertEqual(response.status, 304)
        self.assertEqual(response.body, b"")
        self.assertEqual(self.client.bandwidth()["response_encoded_bytes"], 0)
        sent = Handler.requests_seen[-1][2]
        self.assertNotIn("Cookie", sent)
        self.assertEqual(sent["If-None-Match"], '"version-1"')
        self.assertEqual(sent["If-Modified-Since"], Handler.last_modified)
        result = self.submit()
        self.assertEqual(result["user_id"], 123)
        posts = [r for r in Handler.requests_seen if r[0] == "POST"][-2:]
        self.assertNotIn("If-None-Match", posts[0][2])
        self.assertEqual(posts[1][2]["X-CSRF-TOKEN"], "fresh-token")
        self.assertIn("flow=preflight", posts[1][2]["Cookie"])
        self.assertEqual(self.client.bandwidth()["request_count"], 3)

    def test_session_cookie_reaches_signup_same_round(self):
        self.client.page_url = self.base + "/sessionform"
        self.client.load_form()
        self.submit()
        self.assertIn("flow=page", Handler.requests_seen[-1][2]["Cookie"])

    def test_private_and_cookie_vary_pages_not_reused_across_sessions(self):
        for path in ("/private", "/sessionform", "/varycookie"):
            self.client.page_url = self.base + path
            self.client.load_form()
            self.client.begin_round()
            self.assertEqual(self.client.load_form().status, 200)
            self.assertNotIn("If-None-Match", Handler.requests_seen[-1][2])

    def test_redirect_bodies_are_measured_before_following(self):
        self.client.page_url = self.base + "/redirect"
        self.client.load_form()
        metrics = self.client.bandwidth()
        self.assertEqual(metrics["request_count"], 2)
        self.assertEqual(metrics["response_encoded_bytes"], len(gzip.compress(b"redirect body")) + len(gzip.compress(Handler.form)))

    def test_identity_response_not_falsely_reported_as_gzip(self):
        self.client.page_url = self.base + "/identity"
        self.client.load_form()
        metrics = self.client.bandwidth()
        self.assertEqual(metrics["response_encoded_bytes"], metrics["response_decoded_bytes"])
        self.assertEqual(metrics["requests"][0]["content_encoding"], "identity")

    def test_captcha_and_rate_limit_stop_without_retry(self):
        for path, exception in (("/captcha", CaptchaDetected), ("/ratelimit", RateLimited)):
            self.client.begin_round()
            self.client.signup_url = self.base + path
            with self.assertRaises(exception):
                self.submit()
            self.assertEqual(self.client.bandwidth()["request_count"], 1)

    def test_ambiguous_signup_is_not_retried(self):
        self.client.signup_url = self.base + "/ambiguous"
        with self.assertRaises(SignupFailed):
            self.submit()
        self.assertEqual(self.client.bandwidth()["request_count"], 1)

    def test_csrf_retry_is_bounded_to_one(self):
        self.client.signup_url = self.base + "/badcsrf"
        with self.assertRaises(SignupFailed):
            self.submit()
        self.assertEqual(self.client.bandwidth()["request_count"], 2)

    def test_proxy_credentials_passed_through_proxies_without_logging(self):
        proxy = parse_proxy("alice:p%40ss@proxy.example:8080")
        self.client.begin_round(proxy)
        with patch.object(self.client.session, "send", side_effect=__import__('requests').exceptions.ProxyError("private password")) as send:
            with self.assertRaises(SignupFailed) as error:
                self.client.load_form()
        self.assertEqual(send.call_args.kwargs["proxies"],
                         {"http": "http://alice:p%40ss@proxy.example:8080", "https": "http://alice:p%40ss@proxy.example:8080"})
        self.assertNotIn("private password", str(error.exception))
        self.assertNotIn("p%40ss", json.dumps(self.client.bandwidth()))

    def test_partial_response_failure_is_measured(self):
        self.client.page_url = self.base + "/partial"
        with self.assertRaises(Exception):
            self.client.load_form()
        self.assertEqual(self.client.bandwidth()["response_encoded_bytes"], len(b"partial"))

    def test_complete_http_workflow_uses_one_client_and_logs_each_round(self):
        settings = dict(account_count=2, proxies=[], fixed_password="test_password",
                        name_format="", scrambled=True, birthday="2000-01-01", user_agent="test")
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                patch.object(main, "gather_config", return_value=settings), \
                patch.object(main, "SignupClient", return_value=self.client) as factory, \
                patch.object(Main, "usernameCreator", side_effect=["local_one", "local_two"]), \
                patch.object(self.client, "close", wraps=self.client.close) as close, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(asyncio.run(main.main(2, cost_per_gb=2.5, mode="http")), 0)
            factory.assert_called_once()
            close.assert_called_once()
            output = next(Path("output").iterdir())
            records = [json.loads(line) for line in (output / "bandwidth.jsonl").read_text().splitlines()]
            self.assertEqual([r["request_count"] for r in records], [2, 3])
            self.assertEqual([r["not_modified"] for r in records], [0, 1])
            self.assertEqual([r["status"] for r in records], ["success", "success"])
            self.assertAlmostEqual(records[1]["estimated_cost"], records[1]["http_bytes_estimate"] / 1e9 * 2.5)
            self.assertNotIn("test_password", json.dumps(records))
            logged = (output / "process.log").read_text()
            for event in ("RUN START", "STAGE START", "STAGE DONE", "ACCOUNT END", "EXPORT DONE", "CLEANUP DONE", "RUN END"):
                self.assertIn(event, logged)
            self.assertNotIn("test_password", logged)
