"""Shared headless/visible-browser signup with per-round CDP bandwidth tracking."""

import base64
from datetime import date
import json
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import urlsplit

from lib.browser_bandwidth import BrowserBandwidth
from lib.diagnostics import OperationTimeout, RequestWatch
from lib.http_signup import CaptchaDetected, PAGE_URL, RateLimited, ResponseData, SIGNUP_URL, SignupClient, SignupFailed


class BrowserSignupClient:
    def __init__(self, browser_path="", incognito=False, page_url=PAGE_URL, signup_url=SIGNUP_URL,
                 headless=False):
        self.browser_path, self.incognito = browser_path, incognito
        self.headless = headless
        self.page_url, self.signup_url = page_url, signup_url
        self.logger = None
        self.request_timeout, self.heartbeat = 60, 5
        self.account_label = "-"
        self.chrome = self.page = None
        self.extension_path = None
        self.collector = BrowserBandwidth(signup_url)
        self.checked_responses = set()

    def log(self, level, message, *args):
        if self.logger:
            getattr(self.logger, level)(message, *args)

    def _auth_extension(self, auth):
        self.extension_path = Path(tempfile.mkdtemp(prefix="signup-proxy-auth-"))
        manifest = dict(manifest_version=3, name="Signup Proxy Auth", version="1.0",
                        permissions=["webRequest", "webRequestAuthProvider"],
                        host_permissions=["<all_urls>"], background={"service_worker": "background.js"})
        (self.extension_path / "manifest.json").write_text(json.dumps(manifest))
        credentials = json.dumps(dict(username=auth[0], password=auth[1]))
        (self.extension_path / "background.js").write_text(
            "chrome.webRequest.onAuthRequired.addListener((details, callback) => "
            f"callback(details.isProxy ? {{authCredentials: {credentials}}} : {{}}), "
            "{urls: ['<all_urls>']}, ['asyncBlocking']);")

    def begin_round(self, proxy=None):
        self.finish_round()
        self.collector = BrowserBandwidth(self.signup_url)
        self.checked_responses = set()
        try:
            from DrissionPage import Chromium, ChromiumOptions
        except ImportError:
            raise SignupFailed("Browser engine needs: venv/bin/python -m pip install -r requirements.txt") from None
        options = ChromiumOptions().auto_port().headless(self.headless)
        options.set_argument("--lang", "en")
        options.set_argument("--disable-background-networking")
        options.set_argument("--window-size", "1280,900")
        options.set_timeouts(base=min(10, self.request_timeout), page_load=self.request_timeout, script=min(10, self.request_timeout))
        if self.browser_path:
            options.set_browser_path(self.browser_path)
        if proxy:
            options.set_proxy(proxy.server)
        if proxy and proxy.auth:
            self._auth_extension(proxy.auth)
            options.add_extension(str(self.extension_path))
            self.log("info", "[%s] Proxy authentication extension enabled; incognito disabled", self.account_label)
        elif self.incognito:
            options.incognito()
        watch = self._watch("startup")
        watch.phase = "starting Chromium/network capture"
        try:
            with watch.running():
                self.chrome = Chromium(addr_or_opts=options)
                self.page = self.chrome.latest_tab
                self.collector.attach(self.page)
                self.page.run_cdp("Network.clearBrowserCookies")
                self.page.run_cdp("Network.clearBrowserCache")
                self.page.run_cdp("Network.setCacheDisabled", cacheDisabled=True)
                self.log("info", "[%s] Browser ready | headless=%s | fresh profile | cache/cookies cleared | CDP measurement active",
                         self.account_label, self.headless)
        except BaseException as exc:
            try:
                self.finish_round()
            except Exception as cleanup_error:
                self.log("warning", "Browser startup cleanup failed (%s)", type(cleanup_error).__name__)
            if isinstance(exc, OperationTimeout):
                self.log("error", "BLOCKED | %s", exc)
                raise SignupFailed(str(exc)) from None
            if isinstance(exc, Exception):
                raise SignupFailed(f"Browser startup failed ({type(exc).__name__}); check --browser-path/BROWSER_PATH") from None
            raise

    def bandwidth(self):
        return self.collector.snapshot()

    def confirmed_result(self):
        """Read a fresh login cookie across this isolated browser profile's domains."""
        if not self.page:
            return None
        try:
            cookies = self.page.run_cdp("Storage.getCookies", _timeout=2)["cookies"]
        except Exception as exc:
            self.log("warning", "[%s] Could not read login cookies (%s)", self.account_label, type(exc).__name__)
            return None
        hosts = {urlsplit(url).hostname for url in (self.page_url, self.signup_url)}
        cookies = [dict(cookie) for cookie in cookies if cookie.get("domain") and any(
            host == cookie["domain"].lstrip(".") or host.endswith("." + cookie["domain"].lstrip("."))
            for host in hosts if host)]
        if any(cookie.get("name") == ".ROBLOSECURITY" and cookie.get("value") for cookie in cookies):
            return {"user_id": None, "cookies": cookies}
        return None

    def _watch(self, operation):
        return RequestWatch(f"[{self.account_label}] browser {operation}", self.logger,
                            self.request_timeout, self.heartbeat,
                            byte_counter=lambda: self.bandwidth()["response_encoded_bytes"])

    def _blocking(self):
        if self.collector.block == "captcha":
            raise CaptchaDetected("CAPTCHA/security verification detected in browser")
        if self.collector.block == "rate_limit":
            raise RateLimited("Signup rate limit detected in browser; stopping the entire run")
        selectors = ('css:[data-qa-id="captcha"], .captcha-modal, .captcha-container, '
                     'iframe[src*="arkoselabs"], iframe[src*="funcaptcha"], '
                     'iframe[title*="challenge"], iframe[title*="Challenge"]')
        if any(element.states.is_displayed and element.states.has_rect
               for element in self.page.eles(selectors, timeout=0.1)):
            raise CaptchaDetected("Visible CAPTCHA detected")
        with self.collector.lock:
            responses = list(self.collector.signup_finished)
        for request_id, status in responses:
            if request_id in self.checked_responses:
                continue
            self.checked_responses.add(request_id)
            try:
                body = self.page.run_cdp("Network.getResponseBody", requestId=request_id)
            except Exception:
                continue
            content = base64.b64decode(body["body"]) if body.get("base64Encoded") else body["body"].encode()
            response = ResponseData(status, {}, content, self.signup_url)
            SignupClient.check_challenge(response)
            data = response.json()
            errors = data.get("errors", []) if isinstance(data, dict) else []
            # Allow the page's own definitive CSRF-token negotiation to finish.
            csrf_rejection = status == 403 and errors and all(
                isinstance(error, dict) and error.get("code") == 0
                and "token" in str(error.get("message", "")).lower() for error in errors)
            if (status >= 400 or errors) and not csrf_rejection:
                codes = [error["code"] for error in errors
                         if isinstance(error, dict) and isinstance(error.get("code"), int)]
                raise SignupFailed(f"Browser signup rejected: HTTP {status}; error_codes={codes}; not retried")

    def load_form(self):
        watch = self._watch("navigation")
        watch.phase = "loading browser signup page and assets"
        try:
            with watch.running():
                if not self.page.get(self.page_url, show_errmsg=True, retry=0, timeout=self.request_timeout):
                    raise SignupFailed("Browser navigation did not complete")
                self._blocking()
        except (CaptchaDetected, RateLimited, SignupFailed):
            raise
        except OperationTimeout as exc:
            self.log("error", "BLOCKED | %s", exc)
            raise SignupFailed(str(exc)) from None
        except Exception as exc:
            raise SignupFailed(f"Browser navigation failed ({type(exc).__name__})") from None

    def submit(self, username, password, birthday):
        watch = self._watch("signup")
        watch.phase = "filling signup form"
        try:
            with watch.running():
                self._blocking()
                cookie_button = self.page.ele("css:.cookie-btn.btn-primary-md", timeout=0.2)
                if cookie_button:
                    cookie_button.click()
                born = date.fromisoformat(birthday[:10])
                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                for selector, value in (("#MonthDropdown", months[born.month - 1]),
                                        ("#DayDropdown", f"{born.day:02d}"), ("#YearDropdown", str(born.year))):
                    field = self.page.ele(selector)
                    if not field.select.by_value(value) or field.value != value:
                        raise SignupFailed(f"Browser form could not select {selector}")
                    self.log("info", "[%s] FORM FIELD | %s=%s", self.account_label, selector, value)
                for selector, value in (("#signup-username", username), ("#signup-password", password)):
                    field = self.page.ele(selector)
                    field.clear()
                    field.input(value, clear=False)
                    if field.value != value:
                        raise SignupFailed(f"Browser form value did not match for {selector}")
                    self.log("info", "[%s] FORM FIELD | %s=%s", self.account_label, selector,
                             "[hidden]" if "password" in selector else json.dumps(value))
                checkbox = self.page.ele("#signup-checkbox", timeout=0.2)
                if checkbox and not checkbox.states.is_checked:
                    checkbox.click()
                    if not checkbox.states.is_checked:
                        raise SignupFailed("Browser terms checkbox could not be checked")
                self.log("info", "[%s] FORM FIELD | terms_checkbox=%s", self.account_label,
                         "checked" if checkbox else "not present")
                button = self.page.ele("#signup-button")
                watch.phase = "waiting for signup button to become enabled"
                while not button.states.is_enabled:
                    self._blocking()
                    watch.check()
                    watch.received = self.bandwidth()["response_encoded_bytes"]
                    time.sleep(0.2)
                self._blocking()
                if not button.click():  # Exactly one click attempt; no resubmission.
                    raise SignupFailed("Sign Up button could not be clicked; not retried")
                self.log("info", "[%s] SIGNUP SUBMITTED | button clicked once", self.account_label)
                watch.phase = "waiting for signup result/login cookie"
                while True:
                    result = self.confirmed_result()
                    if result:
                        self.log("info", "[%s] Signup confirmed by fresh login cookie", self.account_label)
                        return result
                    self._blocking()
                    watch.check()
                    watch.received = self.bandwidth()["response_encoded_bytes"]
                    time.sleep(0.2)
        except (CaptchaDetected, RateLimited, SignupFailed):
            raise
        except OperationTimeout as exc:
            self.log("error", "BLOCKED | %s", exc)
            raise SignupFailed(str(exc)) from None
        except Exception as exc:
            raise SignupFailed(f"Browser signup failed ({type(exc).__name__}); phase={watch.phase}; not retried") from None

    def finish_round(self):
        if self.page:
            try:
                self.page.run_cdp("Page.stopLoading")
                self.page.run_cdp("Network.disable")
                deadline = time.monotonic() + 2
                while self.page.driver.event_queue.unfinished_tasks and time.monotonic() < deadline:
                    time.sleep(0.02)
            except Exception as exc:
                self.log("warning", "[%s] Could not finish network capture (%s); exporting observed bytes", self.account_label, type(exc).__name__)
            finally:
                self.collector.detach()
        try:
            if self.chrome:
                self.chrome.quit(timeout=5, force=True, del_data=True)
                self.log("info", "[%s] Browser closed", self.account_label)
        finally:
            self.page = self.chrome = None
            if self.extension_path:
                shutil.rmtree(self.extension_path, ignore_errors=True)
                self.extension_path = None

    def close(self):
        self.finish_round()
