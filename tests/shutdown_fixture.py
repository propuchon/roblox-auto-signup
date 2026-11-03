"""Exercise real signal/export handling with a fake HTTP client and no external I/O."""
import asyncio
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

mode = sys.argv[1]
export_mode = sys.argv[2] if len(sys.argv) > 2 else "dated"
engine = sys.argv[3] if len(sys.argv) > 3 else "http"
settings = dict(proxies=[], account_count=3, fixed_password="test_password", name_format="",
                scrambled=True, birthday="2000-01-01", user_agent="test-agent",
                done_delay=(60, 60) if mode == "cooldown_interrupt" else (0, 0),
                failed_delay=(60, 60) if mode == "failed_cooldown_interrupt" else (0, 0))

async def save(account):
    if account["username"] == "test_2" and mode == "post_interrupt":
        Path("ready").touch()
        await asyncio.sleep(60)

library = NS(usernameCreator=Mock(side_effect=["test_1", "test_2", "test_3"]),
             saveAccount=AsyncMock(side_effect=save))

class Client:
    def __init__(self, **kwargs):
        self.index = 0
        Path("opened.txt").write_text("client\n")
    def begin_round(self, proxy):
        self.index += 1
    def load_form(self):
        if self.index == 2 and mode == "captcha_form":
            raise main.CaptchaDetected("CAPTCHA detected")
    def submit(self, username, password, birthday):
        if mode == "failed_cooldown_interrupt":
            raise main.CaptchaDetected("CAPTCHA detected")
        if self.index == 2:
            if mode in {"captcha", "captcha_wait"}:
                raise main.CaptchaDetected("CAPTCHA detected")
            if mode == "failure":
                raise RuntimeError("Simulated HTTP form error")
            if mode not in {"post_interrupt", "success"}:
                Path("ready").touch()
                time.sleep(60)
        return dict(user_id=self.index, cookies=[{"name": ".ROBLOSECURITY", "value": "test_cookie"}])
    def bandwidth(self):
        return dict(request_body_bytes=50, response_encoded_bytes=100, response_decoded_bytes=200,
                    request_headers_estimate=20, response_headers_estimate=20, http_bytes_estimate=190,
                    request_count=2, not_modified=0, requests=[])
    def confirmed_result(self):
        return None
    def close(self):
        Path("closed.txt").write_text("closed\n")
    def finish_round(self):
        with Path("rounds_closed.txt").open("a") as handle:
            handle.write(f"{self.index}\n")

original_captcha_wait = main.wait_before_captcha_close

async def captcha_wait(*args, **kwargs):
    if mode == "failed_cooldown_interrupt":
        return None
    if mode == "captcha_wait":
        Path("ready").touch()
    await original_captcha_wait(*args, **kwargs)

original_cooldown = main.sleep_between_rounds

async def cooldown(*args, **kwargs):
    if mode in {"cooldown_interrupt", "failed_cooldown_interrupt"}:
        Path("ready").touch()
    await original_cooldown(*args, **kwargs)

with patch.object(main, "Main", return_value=library), \
        patch.object(main, "wait_before_captcha_close", side_effect=captcha_wait), \
        patch.object(main, "sleep_between_rounds", side_effect=cooldown), \
        patch.object(main, "gather_config", return_value=settings), \
        patch.object(main, "SignupClient", Client), \
        patch("lib.browser_signup.BrowserSignupClient", Client), main.stop_signals():
    try:
        code = asyncio.run(main.main(3, export_mode=export_mode, mode=engine))
    except main.StopRequested as exc:
        code = 128 + exc.signum
    sys.exit(code)
