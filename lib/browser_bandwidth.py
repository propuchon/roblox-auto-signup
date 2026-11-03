"""Count observed page-target traffic using Chrome DevTools Network events."""

import threading
import time
from urllib.parse import urlsplit


class BrowserBandwidth:
    EVENTS = {
        "Network.requestWillBeSent": "request",
        "Network.responseReceived": "response",
        "Network.dataReceived": "data",
        "Network.loadingFinished": "finished",
        "Network.loadingFailed": "failed",
        "Network.requestServedFromCache": "cached",
    }

    def __init__(self, signup_url):
        self.signup_url = signup_url
        self.lock = threading.RLock()
        self.rows = []
        self.active = {}
        self.block = None
        self.signup_finished = []
        self.driver = None
        self.previous_callbacks = {}

    def attach(self, page):
        self.driver = page.driver
        for event, method in self.EVENTS.items():
            self.previous_callbacks[event] = self.driver.event_handlers.get(event)
            self.driver.set_callback(event, getattr(self, method))
        page.run_cdp("Network.enable")

    def detach(self):
        if self.driver:
            for event, callback in self.previous_callbacks.items():
                self.driver.set_callback(event, callback)
            self.driver = None

    def request(self, requestId, request, timestamp=None, type="Other", redirectResponse=None, **kwargs):
        parsed = urlsplit(request["url"])
        if parsed.scheme not in {"http", "https"}:
            return
        with self.lock:
            if redirectResponse and requestId in self.active:
                self._response(self.active[requestId], redirectResponse)
                self._finish(self.active[requestId], redirectResponse.get("encodedDataLength", 0))
            endpoint = f"{parsed.scheme}://{parsed.hostname}"
            if parsed.port:
                endpoint += f":{parsed.port}"
            endpoint += parsed.path
            row = dict(method=request["method"], endpoint=endpoint, resource_type=type,
                       status=None, request_body_bytes=len(request.get("postData", "").encode("utf-8")),
                       response_encoded_bytes=0, response_decoded_bytes=0,
                       request_headers_estimate=sum(len(f"{key}: {value}\r\n".encode()) for key, value in request.get("headers", {}).items()),
                       response_headers_estimate=0, content_encoding="identity", error=None,
                       from_cache=False, from_service_worker=False, completed=False,
                       _started=time.monotonic(), _signup=request["url"].split("?", 1)[0] == self.signup_url)
            self.rows.append(row)
            self.active[requestId] = row

    def _response(self, row, response):
        headers = {key.lower(): value for key, value in response.get("headers", {}).items()}
        row.update(status=int(response["status"]), content_encoding=headers.get("content-encoding", "identity"),
                   from_cache=row["from_cache"] or response.get("fromDiskCache", False),
                   from_service_worker=response.get("fromServiceWorker", False))
        if row["_signup"] or row["resource_type"] == "Document":
            if any(headers.get(key) for key in ("rblx-challenge-id", "rblx-challenge-type")):
                self.block = "captcha"
            elif row["status"] == 429:
                self.block = "rate_limit"

    def response(self, requestId, response, **kwargs):
        with self.lock:
            if requestId in self.active:
                self._response(self.active[requestId], response)

    def data(self, requestId, dataLength, encodedDataLength, **kwargs):
        with self.lock:
            if requestId in self.active:
                row = self.active[requestId]
                row["response_decoded_bytes"] += max(0, dataLength)
                row["response_encoded_bytes"] += max(0, encodedDataLength)

    def _finish(self, row, encoded):
        row["response_encoded_bytes"] = max(row["response_encoded_bytes"], max(0, encoded))
        row["completed"] = True
        row["seconds"] = round(time.monotonic() - row["_started"], 4)

    def finished(self, requestId, encodedDataLength, **kwargs):
        with self.lock:
            if requestId in self.active:
                row = self.active[requestId]
                self._finish(row, encodedDataLength)
                if row["_signup"]:
                    self.signup_finished.append((requestId, row["status"]))

    def failed(self, requestId, errorText, **kwargs):
        with self.lock:
            if requestId in self.active:
                row = self.active[requestId]
                # Chrome network error names only; do not persist arbitrary response text.
                row["error"] = errorText if errorText.startswith("net::ERR_") else "network request failed"
                self._finish(row, row["response_encoded_bytes"])

    def cached(self, requestId, **kwargs):
        with self.lock:
            if requestId in self.active:
                self.active[requestId]["from_cache"] = True

    def snapshot(self):
        with self.lock:
            rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in self.rows]
        names = ("request_body_bytes", "response_encoded_bytes", "response_decoded_bytes",
                 "request_headers_estimate", "response_headers_estimate")
        result = {name: sum(row[name] for row in rows) for name in names}
        result.update(request_count=len(rows), requests=rows, not_modified=sum(row["status"] == 304 for row in rows),
                      cached_requests=sum(row["from_cache"] for row in rows),
                      pending_requests=sum(not row["completed"] for row in rows),
                      measurement="CDP Network.loadingFinished encodedDataLength; partial dataReceived for unfinished requests",
                      scope="observed HTTP(S) page target; excludes unobserved workers/frames, browser background traffic, WebSockets and TLS overhead")
        result["http_bytes_estimate"] = result["request_body_bytes"] + result["request_headers_estimate"] + result["response_encoded_bytes"]
        return result
