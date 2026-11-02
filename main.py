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
from datetime import date, timedelta
from DrissionPage import Chromium, ChromiumOptions, errors
from tqdm import TqdmExperimentalWarning
from tqdm.rich import tqdm
from lib.lib import Main
from config import ask


warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="pkg_resources")

# Seconds to wait for Roblox to open /home after submitting, including any verification finished by hand
SIGNUP_TIMEOUT = 180


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
        browser_path = ask("BROWSER_PATH", browser_prompt).replace('"', "").replace("'", "")
        if not browser_path:
            return browser_path

        if any(char in browser_path for char in ['&', '|', ';', '$', '`', '(', ')', '{', '}', '[', ']']):
            print("Invalid characters detected.")
            continue
        if browser_path.lower().endswith(('.exe', '.app', '.bin')) or os.name != 'nt':
            if os.path.exists(browser_path):
                return browser_path
            else:
                print("Path does not exist.")
        else:
            print("Browser path should end with .exe on Windows.")


async def gather_config():
    """Gather all user configuration upfront."""
    lib = Main()

    # Browser
    browser_path = get_browser_path()

    # Password
    fixed_password = ""
    while True:
        pwd = ask("PASSWORD",
            "\033[1m(RECOMMENDED) Press enter for random password per account\033[0m\n"
            "Password: ")
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
        v = ask("EMAIL_VERIFICATION",
            "\nEnable email verification? [y/n] (Default: No): ")
        if v.lower() in ["y", "n", ""]:
            verification = v.lower() == "y"
            break

    # Username format
    name_format = ask("NAME_PREFIX",
        "\033[1m(RECOMMENDED) Press enter for randomized prefix\033[0m\n"
        "Name prefix (e.g., 'qing' -> 'qing_0', 'qing_1'): ")

    scrambled = True
    if not name_format:
        while True:
            s = ask("SCRAMBLED_USERNAME",
                "Use scrambled username? [y/n] (Default: Yes): ")
            if s.lower() in ["y", "n", ""]:
                scrambled = s.lower() != "n"
                break

    # Post-signup customization
    customization = False
    while True:
        c = ask("CUSTOMIZATION",
            "Customize account after generation? [y/n] (Default: Yes): ")
        if c.lower() in ["y", "n", ""]:
            customization = c.lower() != "n"
            break

    # Follow users
    follow_str = ask("FOLLOW_USERS",
        "Follow users after signup? Enter usernames comma-separated (leave blank for none): ")
    follow_list = []
    if follow_str:
        follow_list = [u.strip() for u in follow_str.split(",") if u.strip()]
        follow_list = [u for u in follow_list
                      if re.match(r'^[a-zA-Z0-9_]+$', u) and len(u) <= 20]
        if not follow_list:
            print("No valid usernames found.")

    # Proxies
    proxy_str = ask("PROXIES",
        "Use proxies? Enter IP:PORT comma-separated (leave blank for none): ")
    proxy_list = [p.strip() for p in proxy_str.split(",") if p.strip()] if proxy_str else []

    # Incognito
    incognito = False
    while True:
        i = ask("INCOGNITO",
            "Use incognito mode? [y/n] (Default: Yes): ")
        if i.lower() in ["y", "n", ""]:
            incognito = i.lower() != "n"
            break

    # Account count
    count = 1
    while True:
        c = ask("ACCOUNT_COUNT", "Number of accounts to generate (Default: 1): ")
        try:
            count = int(c) if c else 1
            if count <= 0:
                print("Please enter positive number.")
                continue
            if count > 100:
                if input("Generating >100 accounts takes long time. Continue? [y/n]: ").lower() != "y":
                    continue
            break
        except ValueError:
            print("Invalid number.")

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

    if config["browser_path"]:
        co.set_browser_path(config["browser_path"])

    # The proxy login is an extension, and extensions don't run in incognito
    if proxy_auth:
        co.add_extension(lib.proxyAuthExtension(*proxy_auth.split(":", 1)))
    elif config["incognito"]:
        co.incognito()

    return co


def setup_proxies(lib, config):
    """Setup and test proxies."""
    proxy_auth = None
    usable_proxies = []

    if not config["proxy_list"]:
        return usable_proxies, proxy_auth

    # ponytail: proxy auth from env only if needed
    proxy_user = os.environ.get("PROXY_USERNAME", "")
    proxy_pass = os.environ.get("PROXY_PASSWORD", "")
    if proxy_user:
        proxy_auth = f"{proxy_user}:{proxy_pass}"

    for proxy in config["proxy_list"]:
        result = lib.testProxy(proxy, proxy_auth)
        if result[0]:
            usable_proxies.append(proxy)
        else:
            print(result[1])

    if config["proxy_list"] and not usable_proxies:
        sys.exit("No working proxies. Aborting.")

    return usable_proxies, proxy_auth


async def run_signup(page, username, password, x, total, bar, logStage):
    """Fill and submit the signup form. Raises on any page error."""
    page.get("https://www.roblox.com/CreateAccount")

    try:
        page.ele('@class=btn-cta-lg cookie-btn btn-primary-md btn-min-width', timeout=3).click()
    except errors.ElementNotFoundError:
        pass

    # Birthday
    birthday = date(2000, 1, 1) + timedelta(days=random.randrange((date(2008, 1, 1) - date(2000, 1, 1)).days))
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    page.ele("#MonthDropdown", timeout=10).select.by_value(months[birthday.month - 1])

    day_ele = page.ele("#DayDropdown", timeout=10)
    try:
        day_ele.select.by_value(f"{birthday.day:02d}")
    except:
        try:
            day_ele.select.by_value(str(birthday.day))
        except Exception as e:
            print(f"Warning: Could not set day {birthday.day}: {e}")

    page.ele("#YearDropdown", timeout=10).select.by_value(str(birthday.year))

    # Username & password
    page.ele("#signup-username", timeout=10).input(username)
    page.ele("#signup-password", timeout=10).input(password)

    await asyncio.sleep(2)

    try:
        page.ele('@@id=signup-checkbox@@class=checkbox').click()
    except errors.ElementNotFoundError:
        pass

    await asyncio.sleep(1)

    page.ele("@@id=signup-button@@name=signupSubmit@@class=btn-primary-md signup-submit-button btn-full-width", timeout=10).click()
    logStage(f"Signup submitted [{x + 1}/{total}]")
    bar.update(20)

    # Korean compliance
    if page.ele(".text-left korea-compliance-description-text-margin-left korea-compliance-description-text-margin-right small text", timeout=2):
        print("\nKorean compliance detected, accepting...\n")
        page.ele("@@id=compliance-privacy-policy-0@@class=checkbox").click()
        page.ele("@@id=compliance-privacy-policy-1@@class=checkbox").click()
        page.ele("@@id=compliance-privacy-policy-3@@class=checkbox").click()
        page.ele("@@id=compliance-privacy-policy-4@@class=checkbox").click()
        page.ele("@@id=signup-agreements-button@@class=btn-primary-md signup-submit-button btn-full-width@@name=signupAgreementsSubmit").click()


async def post_process_account(lib, page, config, username, password, email, email_password, x, total, bar, logStage):
    """Email verification, customization, following. Returns (success, cookies)."""
    accountCookies = []

    try:
        if config["verification"] and email:
            logStage(f"Adding email [{x + 1}/{total}]")
            page.get("https://www.roblox.com/my/account#!/info")
            page.ele("xpath://*[contains(text(),'Email')]/ancestor::div[.//button][1]//button[normalize-space()='Add']", timeout=15).click()
            page.ele("#emailAddress", timeout=10).input(email)
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
                        body = getattr(msg, 'text', None) or (msg.html[0] if getattr(msg, 'html', None) else "")
                        match = re.search(r'https://www\.roblox\.com/account/settings/verify-email\?ticket=[^\s)"]+', body)
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
            accountCookies.append({
                "name": cookie["name"],
                "value": cookie["value"],
            })
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

    except Exception as e:
        print(f"\nPost-process error: {e}\n")
        return False, accountCookies


async def main():
    lib = Main()

    print("Checking for updates...")
    await lib.checkUpdate()

    print("\nGathering configuration...\n")
    config = await gather_config()

    usable_proxies, proxy_auth = setup_proxies(lib, config)
    co = setup_browser(lib, config, proxy_auth)

    accounts = []
    failures = []
    total = config["account_count"]

    # One attempt per account: open a fresh browser, sign up, save, close, next
    for x in range(total):
        if usable_proxies:
            proxy = usable_proxies[x % len(usable_proxies)]
            co.set_proxy(proxy)
            print(f"Using proxy: {proxy}")

        password = config["fixed_password"] or secrets.token_urlsafe(12)
        if config["name_format"]:
            username = lib.usernameCreator(config["name_format"])
        else:
            username = lib.usernameCreator(None, scrambled=config["scrambled"])

        bar = tqdm(total=100)
        setDescription = bar.set_description
        start_time = time.perf_counter()
        stages = []

        def logStage(desc, *args, **kwargs):
            stages.append(f"{desc.split(' [')[0]}@{time.perf_counter() - start_time:.1f}s")
            setDescription(desc, *args, **kwargs)

        bar.set_description = logStage
        bar.set_description(f"Initializing [{x + 1}/{total}]")
        bar.update(10)

        chrome = page = None
        failReason = None
        try:
            email = email_password = None
            if config["verification"]:
                email, email_password, _, _ = await lib.generateEmail(password)

            chrome = Chromium(addr_or_opts=co)
            page = chrome.latest_tab
            page.set.window.max()

            await run_signup(page, username, password, x, total, bar, logStage)

            # Any verification Roblox shows is left for the user to finish in the open browser
            logStage(f"Waiting for signup, finish any verification in the browser [{x + 1}/{total}]")
            deadline = time.monotonic() + SIGNUP_TIMEOUT
            while time.monotonic() < deadline and "/home" not in page.url:
                await asyncio.sleep(1)

            if not any(c["name"] == ".ROBLOSECURITY" for c in page.cookies()):
                failReason = f"Signup not finished within {SIGNUP_TIMEOUT}s (no login cookie)"
            else:
                bar.update(10)
                _, cookies = await post_process_account(
                    lib, page, config, username, password, email, email_password,
                    x, total, bar, logStage
                )
                account = {
                    "username": username,
                    "password": password,
                    "email": email,
                    "emailPassword": email_password,
                    "cookies": cookies,
                }
                await lib.saveAccount(account)
                accounts.append(account)
        except Exception as e:
            failReason = f"Error: {' '.join(str(e).split())}"

        if chrome:
            closeBrowser(chrome, page)

        if failReason:
            print(f"\nAccount {x + 1} failed: {failReason}\n")
            failures.append(f"[{x + 1}] {failReason}")
            with open("./userfail.txt", "a", encoding="utf-8") as f:
                f.write(f"{username}:{password}\n")
            await lib.saveTiming(username, "failed", time.perf_counter() - start_time, stages)
        else:
            await lib.saveTiming(username, "success", time.perf_counter() - start_time, stages)
            bar.update(max(0, 100 - bar.n))
        bar.close()

    # Summary
    print(f"\n\nCreated {len(accounts)}/{total} accounts")
    for failure in failures:
        print(f"Failed {failure}")

    if accounts:
        print("\033[1mCredentials:\033[0m")
        for acc in accounts:
            print(f"  {acc['username']}: {'*' * len(acc['password'])}")

        if ask("EXPORT_TO_CLIPBOARD", "\nExport to clipboard? [y/n]: ").lower() == "y":
            export_str = ""
            for acc in accounts:
                for cookie in acc["cookies"]:
                    if cookie["name"] == ".ROBLOSECURITY":
                        export_str += f"{cookie['value']}\n"
                        break
            pyperclip.copy(export_str)
            print("Copied to clipboard!")

    for i in range(5, 0, -1):
        print(f"\rExiting in {i}s...", end="", flush=True)
        await asyncio.sleep(1)
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted. Cleaning up...")
        gc.collect()
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
