"""HTTP signup transport. No browser, asset loading, or challenge solving."""

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import time
from urllib.parse import quote, urljoin, urlsplit
import zlib

import requests
from lib.diagnostics import OperationTimeout, RequestWatch

PAGE_URL = "https://www.roblox.com/CreateAccount"
SIGNUP_URL = "https://auth.roblox.com/v2/signup"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class CaptchaDetected(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


class SignupFailed(RuntimeError):
    pass


class StreamingSession(requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # Requests otherwise consumes redirect bodies, even with allow_redirects=False.
        # The client below follows GET redirects itself after measuring each response.
        return iter(())


class FormMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name", "").lower() == "csrf-token":
            self.csrf = attrs.get("data-token") or attrs.get("content")


@dataclass
class ResponseData:
    status: int
    headers: object
    body: bytes
    url: str

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            return {}


class SignupClient:
    def __init__(self, user_agent=DEFAULT_USER_AGENT, timeout=30,
                 page_url=PAGE_URL, signup_url=SIGNUP_URL, logger=None,
                 request_timeout=60, heartbeat=5):
        self.session = StreamingSession()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip",
                                     "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
        self.timeout = timeout
        self.page_url, self.signup_url = page_url, signup_url
        self.proxies = {}
        self.proxy_key = None
        self.validators = {}  # Headers only, never response bodies or old CSRF tokens.
        self.csrf = None
        self.requests = []
        self.logger = logger
        self.request_timeout = request_timeout
        self.heartbeat = heartbeat
        self.account_label = "-"

    def log(self, level, message, *args):
        if self.logger:
            getattr(self.logger, level)(message, *args)

    def begin_round(self, proxy=None):
        self.session.cookies.clear()
        self.session.headers.pop("X-CSRF-TOKEN", None)
        self.csrf = None
        self.requests = []
        self.proxies = {}
        self.proxy_key = proxy
        if proxy:
            url = proxy.server
            if proxy.auth:
                auth = ":".join(quote(part, safe="") for part in proxy.auth)
                url = url.replace("://", f"://{auth}@", 1)
            self.proxies = {"http": url, "https": url}
        # Requests has no response-body cache. Pools stay open in this one Session.

    def close(self):
        self.session.cookies.clear()
        self.session.close()

    def bandwidth(self):
        records = [dict(record) for record in self.requests]
        names = ("request_body_bytes", "response_encoded_bytes", "response_decoded_bytes",
                 "request_headers_estimate", "response_headers_estimate")
        result = {name: sum(record.get(name, 0) for record in records) for name in names}
        result.update(request_count=len(records), not_modified=sum(r["status"] == 304 for r in records),
                      requests=records)
        result["http_bytes_estimate"] = sum(result[name] for name in names if name != "response_decoded_bytes")
        return result

    def _validator_key(self, url):
        return (url, self.proxy_key, self.session.headers.get("User-Agent"),
                self.session.headers.get("Accept"), self.session.headers.get("Accept-Encoding"))

    def _remember_validators(self, url, response):
        headers = response.headers
        control = {part.strip().lower().split("=", 1)[0] for part in headers.get("Cache-Control", "").split(",")}
        vary = {part.strip().lower() for part in headers.get("Vary", "").split(",") if part.strip()}
        key = self._validator_key(url)
        if (control & {"no-store", "private"} or headers.get("Set-Cookie")
                or vary - {"accept", "accept-encoding", "user-agent"}):
            self.validators.pop(key, None)
            return
        if response.status_code == 200:
            self.validators[key] = {name: headers[name] for name in ("ETag", "Last-Modified") if name in headers}
        elif response.status_code == 304:
            self.validators.setdefault(key, {}).update(
                {name: headers[name] for name in ("ETag", "Last-Modified") if name in headers})

    def request(self, method, url, *, payload=None, conditional=False, headers=None):
        headers = dict(headers or {})
        if conditional and method == "GET":
            validators = self.validators.get(self._validator_key(url), {})
            for source, target in (("ETag", "If-None-Match"), ("Last-Modified", "If-Modified-Since")):
                if source in validators:
                    headers[target] = validators[source]
        prepared = self.session.prepare_request(requests.Request(method, url, json=payload, headers=headers))
        body = prepared.body or b""
        if isinstance(body, str):
            body = body.encode("utf-8")
        parsed = urlsplit(url)
        record = dict(method=method, endpoint=f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                      status=None, request_body_bytes=len(body), response_encoded_bytes=0,
                      response_decoded_bytes=0, content_encoding="", error=None,
                      request_headers_estimate=len(f"{method} {prepared.path_url} HTTP/1.1\r\n".encode())
                      + sum(len(f"{key}: {value}\r\n".encode()) for key, value in prepared.headers.items())
                      + len(f"Host: {parsed.netloc}\r\n\r\n".encode()), response_headers_estimate=0)
        self.requests.append(record)
        response = None
        started = time.perf_counter()
        label = f"[{self.account_label}] request {len(self.requests)} {method} {record['endpoint']}"
        watch = RequestWatch(label, self.logger, self.request_timeout, self.heartbeat)
        self.log("info", "REQUEST START %s | conditional=%s | timeout=%s | total_deadline=%gs",
                 label, any(key.startswith("If-") for key in headers), self.timeout, self.request_timeout)
        try:
            with watch.running():
                response = self.session.send(prepared, proxies=self.proxies, stream=True,
                                             timeout=self.timeout, allow_redirects=False)
                record["status"] = response.status_code
                record["content_encoding"] = response.headers.get("Content-Encoding", "identity").lower()
                watch.phase = "reading response body"
                self.log("info", "RESPONSE HEADERS %s | HTTP %d | encoding=%s | elapsed=%.2fs",
                         label, response.status_code, record["content_encoding"], time.perf_counter() - started)
                record["response_headers_estimate"] = len(f"HTTP/1.1 {response.status_code} {response.reason}\r\n".encode()) + 2
                record["response_headers_estimate"] += sum(
                    len(f"{key}: {value}\r\n".encode()) for key, value in response.raw.headers.items())
                if conditional and method == "GET":
                    self._remember_validators(url, response)
                if response.status_code in (204, 304) or method == "HEAD":
                    return ResponseData(response.status_code, response.headers, b"", url)
                encoding = record["content_encoding"]
                if encoding not in ("identity", "", "gzip", "deflate"):
                    raise SignupFailed(f"Unsupported response Content-Encoding: {encoding}")
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding in ("gzip", "deflate") else None
                chunks = []
                while True:
                    watch.check()
                    chunk = response.raw.read(64 * 1024, decode_content=False)
                    if not chunk:
                        break
                    record["response_encoded_bytes"] += len(chunk)
                    watch.received = record["response_encoded_bytes"]
                    if record["response_encoded_bytes"] > 16 * 1024 * 1024:
                        raise SignupFailed("Response exceeds 16 MiB compressed size limit")
                    decoded = decoder.decompress(chunk, 32 * 1024 * 1024 - record["response_decoded_bytes"] + 1) if decoder else chunk
                    record["response_decoded_bytes"] += len(decoded)
                    if record["response_decoded_bytes"] > 32 * 1024 * 1024:
                        raise SignupFailed("Response exceeds 32 MiB decoded size limit")
                    chunks.append(decoded)
                if decoder and not decoder.eof:
                    raise SignupFailed("Incomplete compressed response")
                return ResponseData(response.status_code, response.headers, b"".join(chunks), url)
        except OperationTimeout as exc:
            record["error"] = type(exc).__name__
            self.log("error", "BLOCKED %s", exc)
            raise SignupFailed(str(exc)) from None
        except requests.RequestException as exc:
            record["error"] = type(exc).__name__
            reason = (f"{label}: HTTP transport failed ({type(exc).__name__}); "
                      f"phase={watch.phase}; elapsed={time.perf_counter() - started:.2f}s; "
                      f"connect/read timeout={self.timeout}; request was not retried")
            self.log("error", "BLOCKED %s", reason)
            raise SignupFailed(reason) from None
        except BaseException as exc:
            record["error"] = type(exc).__name__
            self.log("error", "REQUEST ERROR %s | type=%s | phase=%s", label, type(exc).__name__, watch.phase)
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - started, 4)
            if response is not None:
                # raw.tell also counts bytes consumed before a read was interrupted.
                record["response_encoded_bytes"] = max(record["response_encoded_bytes"], response.raw.tell())
                response.close()
            self.log("info" if record["error"] is None else "error",
                     "REQUEST END %s | HTTP %s | elapsed=%.2fs | encoded=%d B | decoded=%d B | error=%s",
                     label, record["status"], record["seconds"], record["response_encoded_bytes"],
                     record["response_decoded_bytes"], record["error"] or "none")

    @staticmethod
    def check_challenge(response):
        data = response.json()
        errors = data.get("errors", []) if isinstance(data, dict) else []
        if any(response.headers.get(name) for name in ("rblx-challenge-id", "rblx-challenge-type")):
            raise CaptchaDetected("Server requires CAPTCHA/security verification")
        if any(isinstance(error, dict) and (error.get("code") == 2 or "captcha" in str(error.get("message", "")).lower()) for error in errors):
            raise CaptchaDetected("CAPTCHA detected")
        if response.status == 403 and b"captcha" in response.body.lower() and "text/html" in response.headers.get("Content-Type", ""):
            raise CaptchaDetected("CAPTCHA detected in HTTP response")
        if response.status == 429 or any(isinstance(error, dict) and error.get("code") in (3, 12) for error in errors):
            raise RateLimited("Server rate limit reached; stopping without retry")

    def load_form(self):
        url = self.page_url
        allowed_host = urlsplit(url).hostname
        for _ in range(6):
            response = self.request("GET", url, conditional=True)
            self.check_challenge(response)
            if response.status in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get("Location", ""))
                if urlsplit(target).hostname != allowed_host or urlsplit(target).scheme != urlsplit(url).scheme:
                    raise SignupFailed("Unexpected cross-origin form redirect")
                url = target
                self.log("info", "[%s] Following same-origin form redirect (HTTP %d)", self.account_label, response.status)
                continue
            if response.status not in (200, 304):
                raise SignupFailed(f"Could not load signup form (HTTP {response.status})")
            parser = FormMetadata()
            parser.feed(response.body.decode("utf-8", errors="replace"))
            self.csrf = response.headers.get("x-csrf-token") or parser.csrf
            self.log("info", "[%s] Form ready | HTTP %d | CSRF=%s | body=%s",
                     self.account_label, response.status, "present" if self.csrf else "absent",
                     "skipped (304)" if response.status == 304 else "loaded")
            return response
        raise SignupFailed("Too many form redirects")

    def submit(self, username, password, birthday):
        payload = dict(username=username, password=password, birthday=birthday,
                       gender=0, isTosAgreementBoxChecked=True)
        visible_fields = {"username", "birthday", "gender", "isTosAgreementBoxChecked"}
        for field, value in payload.items():
            # Allowlist public form fields; keep any future sensitive fields hidden too.
            display = json.dumps(value, ensure_ascii=True) if field in visible_fields else "[hidden]"
            self.log("info", "[%s] FORM FIELD | %s=%s", self.account_label, field, display)
        self.log("info", "[%s] FORM READY | %d fields prepared for signup POST",
                 self.account_label, len(payload))
        origin = urlsplit(self.page_url)
        headers = {"Accept": "application/json", "Origin": f"{origin.scheme}://{origin.netloc}",
                   "Referer": self.page_url}
        if self.csrf:
            headers["X-CSRF-TOKEN"] = self.csrf
        response = self.request("POST", self.signup_url, payload=payload, headers=headers)
        self.check_challenge(response)
        data = response.json()
        errors = data.get("errors", []) if isinstance(data, dict) else []
        token = response.headers.get("x-csrf-token")
        # Only a definitive CSRF rejection can be resubmitted, once, with its new token.
        if response.status == 403 and token and errors and all(
                isinstance(error, dict) and error.get("code") == 0
                and "token" in str(error.get("message", "")).lower() for error in errors):
            self.csrf = token
            self.log("warning", "[%s] CSRF token rejected; retrying once with the server's new token (1/1)", self.account_label)
            headers["X-CSRF-TOKEN"] = token
            response = self.request("POST", self.signup_url, payload=payload, headers=headers)
            self.check_challenge(response)
            data = response.json()
        if response.status != 200 or not isinstance(data, dict) or data.get("errors"):
            codes = [error.get("code") for error in data.get("errors", []) if isinstance(error, dict)] if isinstance(data, dict) else []
            raise SignupFailed(f"Signup rejected (HTTP {response.status}, error codes {codes})")
        cookies = [{"name": cookie.name, "value": cookie.value} for cookie in self.session.cookies]
        user_id = data.get("userId")
        if not (isinstance(user_id, int) and user_id > 0) and not any(c["name"] == ".ROBLOSECURITY" for c in cookies):
            raise SignupFailed("Signup response did not confirm an account or login cookie")
        return {"user_id": user_id, "cookies": cookies}
