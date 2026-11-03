"""Subprocess fixture: run the real lifecycle/exporter with fake browser and network."""

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
settings = dict(browser_path="", proxy_list=[], incognito=False, account_count=3,
                fixed_password="test_password", name_format="", scrambled=True,
                verification=False, customization=False, follow_list=[])
library = NS(checkUpdate=AsyncMock(), usernameCreator=Mock(side_effect=["test_1", "test_2", "test_3"]),
             saveAccount=AsyncMock(), saveTiming=AsyncMock())


class Page:
    url = "https://www.roblox.com/CreateAccount"
    challenge = False
    set = NS(window=NS(max=lambda: None))

    def eles(self, *args, **kwargs):
        return [NS(states=NS(is_displayed=True))] if self.challenge else []

    def cookies(self):
        return [{"name": ".ROBLOSECURITY", "value": "test_cookie"}] if "/home" in self.url else []


def browser(*args, **kwargs):
    with open("opened.txt", "a") as stream:
        stream.write("browser\n")
    return NS(latest_tab=Page())


def close(*args):
    with open("closed.txt", "a") as stream:
        stream.write("closed\n")


async def signup(page, username, password, index, *args, **kwargs):
    if index == 1:
        if mode in {"captcha", "headless_captcha"}:
            page.challenge = True
            return
        if mode == "failure":
            raise RuntimeError("Simulated form error")
        if mode not in {"post_interrupt", "success"}:
            Path("ready").touch()
            # A real signal must also interrupt synchronous library calls.
            time.sleep(60)
    page.url = "https://www.roblox.com/home"


async def post_process(lib, page, config, username, *args):
    if username == "test_2" and mode == "post_interrupt":
        Path("ready").touch()
        await asyncio.sleep(60)
    return True, page.cookies()


with patch.object(main, "Main", return_value=library), \
        patch.object(main, "gather_config", AsyncMock(return_value=settings)), \
        patch.object(main, "setup_browser", return_value=(Mock(), None)), \
        patch.object(main, "Chromium", side_effect=browser), \
        patch.object(main, "closeBrowser", side_effect=close), \
        patch.object(main, "run_signup", side_effect=signup), \
        patch.object(main, "post_process_account", side_effect=post_process), \
        patch.object(main, "tqdm", side_effect=lambda **kwargs: Mock(n=0)), \
        patch.object(main, "ask", return_value="n"), main.stop_signals():
    try:
        code = asyncio.run(main.main(3, headless=mode == "headless_captcha",
                                     export_mode=export_mode))
    except main.StopRequested as exc:
        code = 128 + exc.signum
    sys.exit(code)
