import asyncio
import warnings
import os
import sys
import re
import pyperclip
import random
import secrets
import gc
import time
import argparse
import shutil
from pathlib import Path
from datetime import date, timedelta
from DrissionPage import Chromium, ChromiumOptions, errors
from tqdm import TqdmExperimentalWarning
from tqdm.rich import tqdm
from lib.lib import Main
from lib.proxies import parse_proxy, split_proxy_list
from lib.run_export import RunExport
from lib.shutdown import StopRequested, stop_signals
from config import ask


warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="pkg_resources")

# Maximum signup wait; a detected CAPTCHA stops the run immediately.
SIGNUP_TIMEOUT = 180


class CaptchaDetected(RuntimeError):
    pass


async def humanized_move_click(element, headless=False):
    """Click through Chromium; use the existing delayed click in windowed mode."""
    if headless:
        # DrissionPage clicks through Chromium's protocol, without a desktop mouse.
        element.click()
        return
    try:
        # Random delay before click (like human hesitation)
        await asyncio.sleep(random.uniform(0.05, 0.2))
        # JavaScript click — no system mouse involved
        element.run_js("arguments[0].click();", element)
    except Exception:
        element.click()


async def fill_field(element, value, headless=False):
    """Use protocol input in headless mode; retain gradual typing in windowed mode."""
    element.clear()
    if headless:
        # clear=True skips input focus on macOS in the installed DrissionPage.
        # A separate clear followed by input focuses the field and emits input events.
        element.input(value, clear=False)
    else:
        for char in value:
            element.input(char, clear=False)
            await asyncio.sleep(random.uniform(0.05, 0.15))


def verification_required(page):
    selectors = (
        'css:[data-qa-id="captcha"], .captcha-modal, .captcha-container, '
        'iframe[src*="arkoselabs"], iframe[src*="funcaptcha"], '
        'iframe[title*="challenge"], iframe[title*="Challenge"]'
    )
    return any(element.states.is_displayed for element in page.eles(selectors, timeout=0.2))


async def wait_for_signup(page, headless=False):
    deadline = time.monotonic() + SIGNUP_TIMEOUT
    while time.monotonic() < deadline and "/home" not in page.url:
        if verification_required(page):
            raise CaptchaDetected("CAPTCHA detected; stopping the entire run.")
        await asyncio.sleep(1)
    if not any(cookie["name"] == ".ROBLOSECURITY" for cookie in page.cookies()):
        hint = " Run with --no-headless to inspect the form." if headless else ""
        raise RuntimeError(f"Signup not finished within {SIGNUP_TIMEOUT}s (no login cookie).{hint}")


def closeBrowser(chrome, page):
    """Complete cleanup even if one browser operation fails."""
    for label, operation in (
        ("tab cookies", lambda: page.set.cookies.clear()),
        ("tab cache", lambda: page.clear_cache()),
        ("browser cookies", lambda: chrome.set.cookies.clear()),
        ("browser cache", lambda: chrome.clear_cache()),
        ("browser close", lambda: chrome.quit()),
    ):
        try:
            operation()
        except Exception:
            print(f"Warning: Could not complete {label} cleanup.")


def get_browser_path():
    """Get browser path from user, empty means system Chrome."""
    browser_prompt = (
        "\033[1m\n(RECOMMENDED) Press enter to use default browser path\033[0m\n"
        "Or enter path to Chrome, Brave, Chromium, etc.\n"
        "Examples: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\n"
        "Browser path: "
    )

    while True:
        browser_path = (
            ask("BROWSER_PATH", browser_prompt).replace('"', "").replace("'", "")
        )
        if not browser_path:
            return browser_path

        if any(
            char in browser_path
            for char in ["&", "|", ";", "$", "`", "(", ")", "{", "}", "[", "]"]
        ):
            print("Invalid characters detected.")
            continue
        if browser_path.lower().endswith((".exe", ".app", ".bin")) or os.name != "nt":
            if os.path.exists(browser_path):
                return browser_path
            else:
                print("Path does not exist.")
        else:
            print("Browser path should end with .exe on Windows.")


async def gather_config(cli_account_count=None):
    """Gather all user configuration upfront."""
    lib = Main()

    # Browser
    browser_path = get_browser_path()

    # Password
    fixed_password = ""
    while True:
        pwd = ask(
            "PASSWORD",
            "\033[1m(RECOMMENDED) Press enter for random password per account\033[0m\n"
            "Password: ",
        )
        if not pwd:
            break
        result = await lib.checkPassword(lib.usernameCreator(), pwd)
        if "Password is valid" in result:
            fixed_password = pwd
            break
        print(result)

    # Email verification
    verification = False
    while True:
        v = ask(
            "EMAIL_VERIFICATION", "\nEnable email verification? [y/n] (Default: No): "
        )
        if v.lower() in ["y", "n", ""]:
            verification = v.lower() == "y"
            break

    # Username format
    name_format = ask(
        "NAME_PREFIX",
        "\033[1m(RECOMMENDED) Press enter for randomized prefix\033[0m\n"
        "Name prefix (e.g., 'qing' -> 'qing_0', 'qing_1'): ",
    )

    scrambled = True
    if not name_format:
        while True:
            s = ask(
                "SCRAMBLED_USERNAME", "Use scrambled username? [y/n] (Default: Yes): "
            )
            if s.lower() in ["y", "n", ""]:
                scrambled = s.lower() != "n"
                break

    # Post-signup customization
    customization = False
    while True:
        c = ask(
            "CUSTOMIZATION",
            "Customize account after generation? [y/n] (Default: Yes): ",
        )
        if c.lower() in ["y", "n", ""]:
            customization = c.lower() != "n"
            break

    # Follow users
    follow_str = ask(
        "FOLLOW_USERS",
        "Follow users after signup? Enter usernames comma-separated (leave blank for none): ",
    )
    follow_list = []
    if follow_str:
        follow_list = [u.strip() for u in follow_str.split(",") if u.strip()]
        follow_list = [
            u for u in follow_list if re.match(r"^[a-zA-Z0-9_]+$", u) and len(u) <= 20
        ]
        if not follow_list:
            print("No valid usernames found.")

    # Proxies
    proxy_str = ask(
        "PROXIES", "Proxies: [username:password@]host:port, comma-separated (blank for none): "
    )
    proxy_list = split_proxy_list(proxy_str)
    proxy_file = os.environ.get("PROXY_FILE", "").strip()
    if proxy_file:
        path = Path(proxy_file).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        proxy_list.extend(split_proxy_list(path.read_text(encoding="utf-8")))

    # Incognito
    incognito = False
    while True:
        i = ask("INCOGNITO", "Use incognito mode? [y/n] (Default: Yes): ")
        if i.lower() in ["y", "n", ""]:
            incognito = i.lower() != "n"
            break

    # Account count
    count = cli_account_count or 1
    if not cli_account_count:
        while True:
            c = ask("ACCOUNT_COUNT", "Number of accounts to generate (Default: 1): ")
            try:
                count = int(c) if c else 1
                if count <= 0:
                    print("Please enter positive number.")
                    continue
                if count > 100:
                    if (
                        input(
                            "Generating >100 accounts takes long time. Continue? [y/n]: "
                        ).lower()
                        != "y"
                    ):
                        continue
                break
            except ValueError:
                print("Invalid number.")
    else:
        if count > 100:
            if (
                input(
                    "Generating >100 accounts takes long time. Continue? [y/n]: "
                ).lower()
                != "y"
            ):
                sys.exit("Aborted.")

    return {
        "browser_path": browser_path,
        "fixed_password": fixed_password,
        "verification": verification,
        "name_format": name_format,
        "scrambled": scrambled,
        "customization": customization,
        "follow_list": follow_list,
        "proxy_list": proxy_list,
        "incognito": incognito,
        "account_count": count,
    }


def setup_browser(lib, config, proxy_auth):
    """Setup Chromium browser options."""
    co = ChromiumOptions()
    co.set_argument("--lang", "en")
    co.auto_port().mute(True)
    co.headless(config.get("headless", False))
    if config.get("headless", False):
        co.set_argument("--window-size", "1280,900")

    if config["browser_path"]:
        co.set_browser_path(config["browser_path"])

    # Each browser gets only its own proxy's authentication extension.
    extension_path = None
    try:
        if proxy_auth:
            extension_path = lib.proxyAuthExtension(*proxy_auth)
            co.add_extension(extension_path)
        elif config["incognito"]:
            co.incognito()
        return co, extension_path
    except BaseException:
        if extension_path:
            shutil.rmtree(extension_path, ignore_errors=True)
        raise


def setup_proxies(lib, config):
    """Setup and test proxies."""
    proxy_auth = None
    usable_proxies = []

    if not config["proxy_list"]:
        return usable_proxies

    # Inline credentials override these optional shared defaults.
    proxy_user = os.environ.get("PROXY_USERNAME", "")
    proxy_pass = os.environ.get("PROXY_PASSWORD", "")
    if proxy_user:
        proxy_auth = (proxy_user, proxy_pass)

    seen = set()
    for index, value in enumerate(config["proxy_list"], start=1):
        try:
            proxy = parse_proxy(value, proxy_auth)
        except ValueError as exc:
            sys.exit(f"Invalid proxy entry {index}: {exc}")
        if proxy in seen:
            continue
        seen.add(proxy)
        result = lib.testProxy(proxy.server, proxy.auth)
        if result[0]:
            usable_proxies.append(proxy)
        else:
            print(result[1])

    if config["proxy_list"] and not usable_proxies:
        sys.exit("No working proxies. Aborting.")

    return usable_proxies


async def run_signup(page, username, password, x, total, bar, logStage, headless=False):
    """Fill and submit the signup form. Raises on any page error."""
    page.get("https://www.roblox.com/CreateAccount")
    if verification_required(page):
        raise CaptchaDetected("CAPTCHA detected; stopping the entire run.")

    try:
        page.ele(
            "@class=btn-cta-lg cookie-btn btn-primary-md btn-min-width", timeout=3
        ).click()
    except errors.ElementNotFoundError:
        pass

    # Birthday — direct fill
    birthday = date(2000, 1, 1) + timedelta(
        days=random.randrange((date(2008, 1, 1) - date(2000, 1, 1)).days)
    )
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    # Option values are "Jan", "01", "2004" (texts are "January", "01", "2004"), so select by value
    for dropdown, value in (
        ("#MonthDropdown", months[birthday.month - 1]),
        ("#DayDropdown", f"{birthday.day:02d}"),
        ("#YearDropdown", str(birthday.year)),
    ):
        page.ele(dropdown, timeout=10).select.by_value(
            value
        )  # raises RuntimeError if option missing
        await asyncio.sleep(random.uniform(0.05, 0.15))

    # Both modes keep browser input events; headless sends each complete value.
    username_field = page.ele("#signup-username", timeout=10)
    await fill_field(username_field, username, headless)

    password_field = page.ele("#signup-password", timeout=10)
    await fill_field(password_field, password, headless)

    await asyncio.sleep(random.uniform(0, 0.3))

    try:
        checkbox = page.ele("@@id=signup-checkbox@@class=checkbox")
        await humanized_move_click(checkbox, headless)
    except errors.ElementNotFoundError:
        pass

    await asyncio.sleep(random.uniform(0, 0.7))

    signup_btn = page.ele("@@id=signup-button@@name=signupSubmit", timeout=10)
    await humanized_move_click(signup_btn, headless)
    logStage(f"Signup submitted [{x + 1}/{total}]")
    bar.update(20)

    # Korean compliance
    if page.ele("#compliance-privacy-policy-0", timeout=2):
        print("\nKorean compliance detected, accepting...\n")
        await humanized_move_click(
            page.ele("@@id=compliance-privacy-policy-0@@class=checkbox"), headless
        )
        await humanized_move_click(
            page.ele("@@id=compliance-privacy-policy-1@@class=checkbox"), headless
        )
        await humanized_move_click(
            page.ele("@@id=compliance-privacy-policy-3@@class=checkbox"), headless
        )
        await humanized_move_click(
            page.ele("@@id=compliance-privacy-policy-4@@class=checkbox"), headless
        )
        await humanized_move_click(
            page.ele(
                "@@id=signup-agreements-button@@class=btn-primary-md signup-submit-button btn-full-width@@name=signupAgreementsSubmit"
            ), headless
        )


async def post_process_account(
    lib,
    page,
    config,
    username,
    password,
    email,
    email_password,
    x,
    total,
    bar,
    logStage,
):
    """Email verification, customization, following. Returns (success, cookies)."""
    accountCookies = []

    try:
        if config["verification"] and email:
            logStage(f"Adding email [{x + 1}/{total}]")
            page.get("https://www.roblox.com/my/account#!/info")
            page.ele(
                "xpath://*[contains(text(),'Email')]/ancestor::div[.//button][1]//button[normalize-space()='Add']",
                timeout=15,
            ).click()
            email_field = page.ele("#emailAddress", timeout=10)
            await fill_field(email_field, email, config.get("headless", False))
            page.ele("@@tag()=button@@text()=Add Email", timeout=10).click()

            if page.ele("text:An email has been sent for verification", timeout=15):
                link = None
                for _ in range(30):
                    try:
                        messages = lib.fetchVerification(email, email_password, email)
                    except Exception as e:
                        print(f"Error checking email: {e}")
                        messages = []

                    for msg in messages:
                        body = getattr(msg, "text", None) or (
                            msg.html[0] if getattr(msg, "html", None) else ""
                        )
                        match = re.search(
                            r'https://www\.roblox\.com/account/settings/verify-email\?ticket=[^\s)"]+',
                            body,
                        )
                        if match:
                            link = match.group(0)
                            break

                    if link:
                        break
                    await asyncio.sleep(5)

                if link:
                    logStage(f"Verifying email [{x + 1}/{total}]")
                    page.get(link)
                    await asyncio.sleep(3)
                else:
                    print("Email verification timeout")
                    bar.update(10)

        logStage(f"Saving data [{x + 1}/{total}]")
        for cookie in page.cookies():
            accountCookies.append(
                {
                    "name": cookie["name"],
                    "value": cookie["value"],
                }
            )
        bar.update(5)

        if config["customization"]:
            logStage(f"Customizing [{x + 1}/{total}]")
            await lib.customization(page)
            bar.update(5)

        if config["follow_list"]:
            logStage(f"Following users [{x + 1}/{total}]")
            try:
                await lib.followUser(config["follow_list"], page)
            except Exception as e:
                print(f"Follow error: {e}")
            bar.update(5)

        return True, accountCookies

    except CaptchaDetected:
        raise
    except Exception as e:
        print(f"\nPost-process error: {e}\n")
        return False, accountCookies


async def main(cli_account_count=None, headless=False, export_mode="dated"):
    exporter = RunExport(mode=export_mode)
    run_status, reason = "failed", None
    try:
        print(f"Live export ({export_mode}): {exporter.directory}", flush=True)
        code = await run_workflow(cli_account_count, headless, exporter)
        run_status = "success" if code == 0 else "failed"
        return code
    except CaptchaDetected as exc:
        run_status, reason = "captcha", str(exc)
        print(reason)
        return 1
    except (StopRequested, KeyboardInterrupt, asyncio.CancelledError) as exc:
        run_status, reason = "interrupted", str(exc) or "Stopped by user"
        return 128 + exc.signum if isinstance(exc, StopRequested) else 130
    except BaseException as exc:
        reason = ' '.join(str(exc).split()) or type(exc).__name__
        raise
    finally:
        exporter.end(run_status, reason)
        try:
            print(f"Export saved: {exporter.directory}", flush=True)
        except OSError:
            pass  # The terminal may already be closed; the files are still saved.


async def run_workflow(cli_account_count, headless, exporter):
    lib = Main()
    print("Checking for updates...")
    await lib.checkUpdate()
    print("\nGathering configuration...\n")
    config = await gather_config(cli_account_count)
    config["headless"] = headless
    print(f"Browser mode: {'headless' if headless else 'visible window'}")
    total = config["account_count"]
    exporter.data["requested_count"] = total
    exporter.save()
    usable_proxies = setup_proxies(lib, config)
    accounts, failures = [], []

    for x in range(total):
        proxy = usable_proxies[x % len(usable_proxies)] if usable_proxies else None
        start_time = time.perf_counter()
        password = config["fixed_password"] or secrets.token_urlsafe(12)
        username = None
        stages = []
        chrome = page = bar = None
        extension_path = None
        status, failReason = "interrupted", "Attempt did not finish"
        exporter.begin(x + 1, password=password, proxy=proxy.server if proxy else None)

        def logStage(desc, *args, **kwargs):
            stage = desc.split(' [')[0]
            stages.append(f"{stage}@{time.perf_counter() - start_time:.1f}s")
            exporter.update(stage=stage, stages=stages[:], seconds=time.perf_counter() - start_time)
            if page is not None and verification_required(page):
                raise CaptchaDetected("CAPTCHA detected; stopping the entire run.")
            setDescription(desc, *args, **kwargs)

        try:
            bar = tqdm(total=100)
            setDescription = bar.set_description
            bar.set_description = logStage
            logStage(f"Initializing [{x + 1}/{total}]")
            bar.update(10)
            if config["name_format"]:
                username = lib.usernameCreator(config["name_format"])
            else:
                username = lib.usernameCreator(None, scrambled=config["scrambled"])
            exporter.update(username=username)
            co, extension_path = setup_browser(lib, config, proxy.auth if proxy else None)
            if proxy:
                co.set_proxy(proxy.server)
                print(f"Using proxy: {proxy.server}")
            email = email_password = None
            if config["verification"]:
                email, email_password, _, _ = await lib.generateEmail(password)
                exporter.update(email=email, emailPassword=email_password)

            chrome = Chromium(addr_or_opts=co)
            page = chrome.latest_tab
            if not headless:
                page.set.window.max()
            await run_signup(page, username, password, x, total, bar, logStage, headless=headless)
            logStage(f"Waiting for signup [{x + 1}/{total}]")
            await wait_for_signup(page, headless)

            # Persist login data before optional steps or browser cleanup can fail.
            cookies = [{"name": cookie["name"], "value": cookie["value"]} for cookie in page.cookies()]
            account = dict(username=username, password=password, email=email,
                           emailPassword=email_password, cookies=cookies)
            exporter.update(**account, account_created=True, stage="Signed in")
            bar.update(10)
            processed, processed_cookies = await post_process_account(
                lib, page, config, username, password, email, email_password,
                x, total, bar, logStage,
            )
            if verification_required(page):
                raise CaptchaDetected("CAPTCHA detected; stopping the entire run.")
            if processed_cookies:
                account["cookies"] = processed_cookies
            exporter.update(**account)
            await lib.saveAccount(account)
            accounts.append(account)
            status = "success"
            failReason = None if processed else "Signup succeeded; optional post-processing failed"
        except CaptchaDetected as exc:
            status, failReason = "failed", str(exc)
            raise
        except (StopRequested, KeyboardInterrupt, asyncio.CancelledError) as exc:
            status, failReason = "interrupted", str(exc) or "Stopped by user"
            raise
        except Exception as exc:
            status, failReason = "failed", f"Error: {' '.join(str(exc).split())}"
            # A challenge can replace a form element before the next stage check.
            if page is not None:
                try:
                    captcha = verification_required(page)
                except Exception:
                    captcha = False
                if captcha:
                    failReason = "CAPTCHA detected; stopping the entire run."
                    raise CaptchaDetected(failReason) from None
        finally:
            try:
                exporter.finish(status, failReason, seconds=time.perf_counter() - start_time,
                                stages=stages[:])
            finally:
                try:
                    if chrome:
                        closeBrowser(chrome, page)
                finally:
                    if extension_path:
                        shutil.rmtree(extension_path, ignore_errors=True)
                    if bar:
                        bar.close()

        if status == "failed":
            print(f"\nAccount {x + 1} failed: {failReason}\n")
            failures.append(f"[{x + 1}] {failReason}")
            if username:
                with open("./userfail.txt", "a", encoding="utf-8") as stream:
                    stream.write(f"{username}:{password}\n")
        await lib.saveTiming(username, status, time.perf_counter() - start_time, stages)

    print(f"\n\nCreated {len(accounts)}/{total} accounts")
    for failure in failures:
        print(f"Failed {failure}")
    if accounts:
        print("Credentials:")
        for account in accounts:
            print(f"  {account['username']}: {'*' * len(account['password'])}")
        if ask("EXPORT_TO_CLIPBOARD", "\nExport to clipboard? [y/n]: ").lower() == "y":
            values = [cookie["value"] for account in accounts for cookie in account["cookies"]
                      if cookie["name"] == ".ROBLOSECURITY"]
            pyperclip.copy("".join(value + "\n" for value in values))
            print("Copied to clipboard!")
    return 1 if failures else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Roblox auto signup tool")
    parser.add_argument(
        "count",
        nargs="?",
        type=int,
        default=None,
        help="Number of accounts to generate (optional)",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run Chromium without a visible window (default: --no-headless)",
    )
    parser.add_argument(
        "--export-mode", choices=("dated", "append"), default="dated",
        help="dated: a separate run folder; append: keep adding to output/*.txt",
    )
    args = parser.parse_args(argv)

    if args.count is not None and args.count <= 0:
        parser.error("Account count must be a positive number.")
    return args


if __name__ == "__main__":
    args = parse_args()
    try:
        with stop_signals():
            exit_code = asyncio.run(main(args.count, headless=args.headless,
                                         export_mode=args.export_mode))
        sys.exit(exit_code)
    except StopRequested as exc:
        sys.exit(128 + exc.signum)
    except KeyboardInterrupt:
        print("\n\nInterrupted. Cleaning up...")
        gc.collect()
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
