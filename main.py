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
from lib.lib import Main, getResourcePath
from config import ask


warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="pkg_resources")


async def main():
    lib = Main()
    co = ChromiumOptions()
    co.set_argument("--lang", "en")
    co.auto_port().mute(True)

    print("Checking for updates...")
    version = await lib.checkUpdate()

    lib.downloadUngoogledChromium()

    while True:
        try:
            browserPath = lib.returnUngoogledChromiumPath()
        except Exception as e:
            print(f"An error occurred while checking for Ungoogled Chromium: {e}")
            browserPath = None
        if browserPath is None:
            browserPath = ask("BROWSER_PATH",
                "\033[1m"
                "\n(RECOMMENDED) Press enter in order to use the default browser path (If you have Chrome installed)"
                "\033[0m"
                "\nIf you prefer to use other Chromium browser other than Chrome, please enter its executable path here. (e.g: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe)"
                "\nNote that if captcha bypass is chosen, it will be using Ungoogled Chromium which is already included."
                "\nHere are some supported browsers that are tested and able to use:"
                "\n- Chrome Browser"
                "\n- Brave Browser"
                "\n- Ungoogled Chromium"
                "\nBrowser executable path: "
            ).replace('"', "").replace("'", "")
            if browserPath != "":
                if os.path.exists(browserPath):
                    co.set_browser_path(browserPath)
                    break
                else:
                    print("Please enter a valid path.")
            else:
                break
        else:
            ungoogledChromiumUsage = ask("USE_UNGOOGLED_CHROMIUM",
                "Ungoogled Chromium is detected in the lib folder, would you like to use it? [y/n] (Default: Yes): "
            )
            if ungoogledChromiumUsage.lower() in ["y", "n", ""]:
                if ungoogledChromiumUsage.lower() == "y" or ungoogledChromiumUsage == "":
                    ungoogledPath = lib.returnUngoogledChromiumPath()
                    if ungoogledPath:
                        co.set_browser_path(f"{ungoogledPath}/chrome.exe")
                    break
                else:
                    browserPath = ask("BROWSER_PATH",
                        "\033[1m"
                        "\n(RECOMMENDED) Press enter in order to use the default browser path (If you have Chrome installed)"
                        "\033[0m"
                        "\nIf you prefer to use other Chromium browser other than Chrome, please enter its executable path here. (e.g: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe)"
                        "\nNote that if captcha bypass is chosen, it will be using Ungoogled Chromium which is already included."
                        "\nHere are some supported browsers that are tested and able to use:"
                        "\n- Chrome Browser"
                        "\n- Brave Browser"
                        "\n- Ungoogled Chromium"
                        "\nBrowser executable path: "
                    ).replace('"', "").replace("'", "")
                    if browserPath != "":

                        if any(char in browserPath for char in ['&', '|', ';', '$', '`', '(', ')', '{', '}', '[', ']']):
                            print("Invalid characters detected in browser path. Please enter a valid executable path.")
                        elif not browserPath.lower().endswith(('.exe', '.app', '.bin')) and os.name == 'nt':
                            print("Browser path should end with .exe on Windows.")
                        elif os.path.exists(browserPath):
                            co.set_browser_path(browserPath)
                            break
                        else:
                            print("Please enter a valid path.")
                    else:
                        break
            else:
                print("\nPlease enter a valid option.")

    while True:
        fixedPassword = ask("PASSWORD",
            "\033[1m"
            "\n(RECOMMENDED) Press enter in order to use a random password for each account"
            "\033[0m"
            "\nThe password will be used for the account and email.\nIf you prefer to use your own password for all accounts, do make sure that your password is strong enough.\nThis script has a built in password complexity checker.\nPassword: "
        )
        if fixedPassword:
            result = await lib.checkPassword(lib.usernameCreator(), fixedPassword)
            print(result)
            if "Password is valid" in result:
                break
        else:
            break

    while True:
        verification = ask("EMAIL_VERIFICATION",
            "\033[1m"
            "\n(RECOMMENDED) Press enter in order to enable email verification"
            "\033[0m"
            "\nIf you prefer to turn off email verification, you will risk losing the account. It might also be applicable for people who do not have email verification element"
            "\nWould you like to enable email verification? [y/n] (Default: Yes): "
        )
        if verification.lower() in ["y", "n", ""]:
            break
        else:
            print("\nPlease enter a valid option.")

    nameFormat = ask("NAME_PREFIX",
        "\033[1m"
        "\n(RECOMMENDED) Press enter in order to use randomized name prefix"
        "\033[0m"
        "\nIf you prefer to go by your own name prefix, please enter it here.\nIt will go by this example: (If name prefix is 'qing', then the account generated will be named 'qing_0', 'qing_1' and so on)\nName prefix: "
    )

    if nameFormat:
        scrambledUsername = None
    else:
        while True:
            scrambledUsername = ask("SCRAMBLED_USERNAME", "\nWould you like to use a scrambled username?\nIf no, the script will try to generate a structured username, this might increase generation time. [y/n] (Default: Yes): ")
            if scrambledUsername.lower() in ["y", "n", ""]:
                if scrambledUsername.lower() == "y" or scrambledUsername == "":
                    scrambledUsername = True
                else:
                    scrambledUsername = False
                break
            else:
                print("\nPlease enter a valid option.")

    while True:
        customization = ask("CUSTOMIZATION",
            "\nWould you like to customize the account after the generation process with a randomizer? [y/n] (Default: Yes): "
        )
        if customization.lower() in ["y", "n", ""]:
            break
        else:
            print("\nPlease enter a valid option.")

    followUser = ask("FOLLOW_USERS",
        "\nWould you like to follow any additional accounts after generating this account?\n"
        "If yes, enter the usernames separated by commas (,).\n"
        "Leave blank if none.\n"
        "Usernames: "
    )

    proxyUsage = ask("PROXIES",
        "\nWould you like to use proxies?\n"
        "If yes, please enter the proxy IP and port in the format of IP:PORT separated by commas (,). (Example: http://localhost:1080).\n"
        "Leave blank if none.\n"
        "Proxy: "
    )

    captchaBypass = ask("NOPECHA_API_KEY",
        "\nWould you like to bypass captcha through NopeCHA? (Note that there's only up to 200 free solves per day)"
        "\nYou can get a free API key from https://nopecha.com/manage and paste it here."
        "\nIf yes, please enter the API key for the service."
        "\nLeave blank if none."
        "\nAPI Key: "
    ).strip()

    if captchaBypass:
        if not re.match(r'^[a-zA-Z0-9_-]+$', captchaBypass):
            print("Warning: API key contains invalid characters. Only letters, numbers, hyphens and underscores are allowed.")
            captchaBypass = ""
        elif len(captchaBypass) < 10:
            print("Warning: The API key seems too short. Please make sure you entered it correctly.")
            confirm = input("Continue anyway? [y/n]: ")
            if confirm.lower() != "y":
                captchaBypass = ""

    while True:
        incognitoUsage = ask("INCOGNITO",
            "\nWould you like to use incognito mode? Note that if captcha bypass is chosen, it will not be using incognito mode automatically. [y/n] (Default: Yes): "
        )
        if incognitoUsage.lower() in ["y", "n", ""]:
            break
        else:
            print("\nPlease enter a valid option.")

    accounts = []

    while True:
        executionCount = ask("ACCOUNT_COUNT",
            "\nNumber of accounts to generate (Default: 1): "
        )
        try:
            if executionCount == "":
                executionCount = 1
                break
            executionCount = int(executionCount)
            if executionCount <= 0:
                print("Please enter a positive number.")
                continue
            if executionCount > 100:
                print("Warning: Generating more than 100 accounts may take a very long time and could trigger rate limits.")
                confirm = input("Are you sure you want to continue? [y/n]: ")
                if confirm.lower() != "y":
                    continue
            break
        except ValueError:
            print("Please enter a valid number.")

    print()

    if customization.lower() == "y" or customization == "":
        customization = True
    else:
        customization = False

    if followUser != "":
        following = True
        followUserList = followUser.split(",")
        followUserList = [user.strip() for user in followUserList if user.strip()]

        validFollowUserList = []
        for user in followUserList:
            if re.match(r'^[a-zA-Z0-9_]+$', user) and len(user) <= 20:
                validFollowUserList.append(user)
            else:
                print(f"Invalid username '{user}' - usernames can only contain letters, numbers, and underscores (max 20 chars)")
        followUserList = validFollowUserList
        if not followUserList:
            print("No valid usernames found in follow list.")
            following = False
    else:
        following = False

    if verification.lower() == "y" or verification == "":
        verification = True
    else:
        verification = False

    if (incognitoUsage.lower() == "y" or incognitoUsage == "") and captchaBypass == "":
        co.incognito()

    if captchaBypass != "":
        co.add_extension(getResourcePath("lib/NopeCHA"))

    if proxyUsage.strip():
        proxyList = [proxy.strip() for proxy in proxyUsage.split(",") if proxy.strip()]
    else:
        proxyList = []
    usableProxies = []
    for proxy in proxyList:
        if proxy:
            result = lib.testProxy(proxy)
            if result[0] is True:
                usableProxies.append(proxy)
            else:
                print(result[1])
    proxyNumber = len(usableProxies)

    failures = []
    for x in range(int(executionCount)):
        captchaPresence = True
        captchaRetries = 0
        maxCaptchaRetries = 5
        startTime = time.perf_counter()
        stages = []
        failReason = None
        while captchaPresence and captchaRetries < maxCaptchaRetries:
            failReason = None
            if proxyUsage != "" and usableProxies:
                try:
                    selectedProxy = random.choice(usableProxies)
                    co.set_proxy(selectedProxy)
                    print(f"Using proxy: {selectedProxy}")
                except Exception as e:
                    print(f"Error setting proxy: {e}")

            if "--no-analytics" not in sys.argv:
                lib.checkAnalytics(version)
            passw = fixedPassword or secrets.token_urlsafe(12)
            if nameFormat:
                username = lib.usernameCreator(nameFormat)
            else:
                if scrambledUsername is True:
                    username = lib.usernameCreator(None, scrambled=True)
                else:
                    username = lib.usernameCreator(None, scrambled=False)
            bar = tqdm(total=100)
            setDescription = bar.set_description

            def logStage(desc, *args, **kwargs):
                # Record when each progress stage is reached, saved to timings.csv
                stages.append(f"{desc.split(' [')[0]}@{time.perf_counter() - startTime:.1f}s")
                setDescription(desc, *args, **kwargs)
            bar.set_description = logStage
            bar.set_description(f"Initial setup completed [{x + 1}/{executionCount}]")
            bar.update(10)

            try:
                chrome = Chromium(addr_or_opts=co)
                page = chrome.latest_tab
                page.set.window.max()
            except Exception as e:
                print(f"Failed to initialize browser: {e}")
                bar.close()
                failReason = f"Browser failed to start: {' '.join(str(e).split())}"
                captchaRetries += 1
                continue

            accountCookies = []
            email = None
            emailPassword = None

            if verification is True:
                try:
                    email, emailPassword, token, emailID = await lib.generateEmail(passw)
                    bar.set_description(f"Generated email [{x + 1}/{executionCount}]")
                    bar.update(10)
                except Exception as e:
                    print(f"Failed to generate email: {e}")
                    bar.close()
                    chrome.quit()
                    failReason = f"Email creation failed: {' '.join(str(e).split())}"
                    captchaRetries += 1
                    continue

            try:
                if captchaBypass != "":
                    page.get(f"https://nopecha.com/setup#{captchaBypass}")
                page.get("https://www.roblox.com/CreateAccount")
                try:
                    lang_result = page.run_js_loaded("return window.navigator.userLanguage || window.navigator.language")
                    lang = lang_result.split("-")[0] if lang_result and "-" in lang_result else "en"
                except Exception:
                    lang = "en"
                try:
                    page.ele('@class=btn-cta-lg cookie-btn btn-primary-md btn-min-width', timeout=3).click()
                except errors.ElementNotFoundError:
                    pass
                birthday = date(2000, 1, 1) + timedelta(days=random.randrange((date(2008, 1, 1) - date(2000, 1, 1)).days))
                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                page.ele("#MonthDropdown", timeout=10).select.by_value(months[birthday.month - 1])
                bdaydayelement = page.ele("#DayDropdown", timeout=10)
                try:
                    bdaydayelement.select.by_value(f"{birthday.day:02d}")
                except Exception as e:
                    try:
                        bdaydayelement.select.by_value(str(birthday.day))
                    except Exception as e2:
                        print(f"Warning: Could not set day to {birthday.day}, using default. Errors: {e}, {e2}")
                page.ele("#YearDropdown", timeout=10).select.by_value(str(birthday.year))
                page.ele("#signup-username", timeout=10).input(username)
                page.ele("#signup-password", timeout=10).input(passw)
                await asyncio.sleep(2)
                try:
                    page.ele('@@id=signup-checkbox@@class=checkbox').click()
                except errors.ElementNotFoundError:
                    pass
                await asyncio.sleep(1)
                # Capture Roblox requests (signup, challenge) so a stuck signup can be diagnosed.
                # No method filter here: DrissionPage keeps it for later listen.start() calls, which broke the avatar GET in lib.customization
                page.listen.start("roblox.com")
                page.ele("@@id=signup-button@@name=signupSubmit@@class=btn-primary-md signup-submit-button btn-full-width", timeout=10).click()
                bar.set_description(f"Signup submitted [{x + 1}/{executionCount}]")
                bar.update(20)

                if page.ele(".text-left korea-compliance-description-text-margin-left korea-compliance-description-text-margin-right small text", timeout=2):
                    print("\nKorean compliance detected, accepting terms...\n")
                    page.ele("@@id=compliance-privacy-policy-0@@class=checkbox").click()
                    page.ele("@@id=compliance-privacy-policy-1@@class=checkbox").click()
                    page.ele("@@id=compliance-privacy-policy-3@@class=checkbox").click()
                    page.ele("@@id=compliance-privacy-policy-4@@class=checkbox").click()
                    page.ele("@@id=signup-agreements-button@@class=btn-primary-md signup-submit-button btn-full-width@@name=signupAgreementsSubmit").click()

                # Stop waiting as soon as signup redirects home instead of always waiting the full timeout for a captcha frame
                captchaPresence = False
                for _ in range(20):
                    if "/home" in page.url:
                        break
                    if page.ele('#arkose-iframe', timeout=0):
                        captchaPresence = True
                        break
                    await asyncio.sleep(0.5)
                # With NopeCHA loaded, leave the captcha for the extension to solve during the longer wait below
                if captchaPresence and captchaBypass == "":
                    print(f"Captcha detected for account {x + 1}, retrying... (Attempt {captchaRetries + 1}/{maxCaptchaRetries})")
                    bar.close()
                    chrome.quit()
                    failReason = "Captcha detected"
                    captchaRetries += 1
                    continue

            except Exception as e:
                print(f"\nAn error occurred\n{e}\n")
                captchaPresence = False
                failReason = f"Signup error: {' '.join(str(e).split())}"

        if not failReason:
            timeout = 10 if captchaBypass == "" else 120

            try:
                if lang == "en":
                    page.wait.url_change("https://www.roblox.com/home", timeout=timeout)
                else:
                    page.wait.url_change("https://www.roblox.com/home", timeout=timeout)
            except errors.TimeoutError:
                if lang != "en":
                    try:
                        page.wait.url_change(f"https://www.roblox.com/{lang}/home", timeout=timeout)
                    except errors.TimeoutError:
                        pass
            # The login cookie only exists if Roblox actually created the account
            if not any(cookie["name"] == ".ROBLOSECURITY" for cookie in page.cookies()):
                failReason = "Signup was not completed (no login cookie after signup)"
                print("\nRoblox POST requests after signup:")
                for packet in page.listen.wait(count=500, timeout=1, fit_count=False) or []:
                    if packet.method != "POST":
                        continue
                    res = packet.response
                    print(f"  {res.status if res else 'no response'} {packet.url.split('?')[0]}")
                    if res and res.status >= 400:
                        print(f"    {str(res.body)[:300]}")

        if failReason:
            print(f"\nAccount {x + 1} failed: {failReason}. Skipping this account.\n")
            failures.append(f"[{x + 1}/{executionCount}] {failReason}")
            await lib.saveTiming(username, "failed", time.perf_counter() - startTime, stages + [f"Failed: {failReason}"])
            with open("./userfail.txt", "a", encoding="utf-8") as f:
                f.write(f"{username}:{passw}\n")
            bar.close()
            try:
                if 'chrome' in locals():
                    chrome.quit()
            except Exception as e:
                print(f"Warning: Could not quit browser: {e}")
            continue

        if not captchaPresence:
            bar.set_description(f"Signup process [{x + 1}/{executionCount}]")
            bar.update(20)

            if verification is True:
                try:
                    # Roblox no longer shows a verify-email prompt after signup, so add the email from account settings
                    page.get("https://www.roblox.com/my/account#!/info")
                    page.ele("xpath://*[contains(text(),'Email')]/ancestor::div[.//button][1]//button[normalize-space()='Add']", timeout=15).click()
                    page.ele("#emailAddress", timeout=10).input(email)
                    page.ele("@@tag()=button@@text()=Add Email", timeout=10).click()
                    if page.ele("text:An email has been sent for verification", timeout=15):
                        link = None
                        for _ in range(30):
                            try:
                                messages = lib.fetchVerification(email, emailPassword, emailID)
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
                            bar.set_description(f"Verifying email address [{x + 1}/{executionCount}]")
                            bar.update(20)
                            page.get(link)
                            await asyncio.sleep(3)
                        else:
                            print("Email verification timeout - no verification link received within expected time")
                            bar.update(10)
                    else:
                        bar.set_description(f"Verification email not sent [{x + 1}/{executionCount}]")
                        bar.update(10)
                except Exception as e:
                    print(f"\nAn error occurred during email verification\n{e}\n")
                    print(f"\nFailed to find email verification element. You may need to verify the account manually. Skipping and continuing...\n{e}\n")
                finally:
                    bar.set_description(f"Saving cookies and clearing data [{x + 1}/{executionCount}]")
                    for i in page.cookies():
                        cookie = {
                            "name": i["name"],
                            "value": i["value"],
                        }
                        accountCookies.append(cookie)
                    bar.update(5)

                    if customization is True:
                        bar.set_description(f"Customizing account [{x + 1}/{executionCount}]")
                        await lib.customization(page)
                        bar.update(5)
                    else:
                        bar.set_description(f"Skipping customization [{x + 1}/{executionCount}]")
                        bar.update(5)

                    if following is True:
                        bar.set_description(f"Following users [{x + 1}/{executionCount}]")
                        follow_error = None
                        try:
                            userIDs = await lib.followUser(followUserList, page)
                        except Exception as e:
                            print(f"An error occurred while following users: {e}")
                            follow_error = e
                        bar.update(5)

                    page.set.cookies.clear()
                    page.clear_cache()
                    chrome.set.cookies.clear()
                    chrome.clear_cache()
                    chrome.quit()

                    currentAccount = {"username": username, "password": passw, "email": email, "emailPassword": emailPassword, "cookies": accountCookies}
                    accounts.append(currentAccount)
                    await lib.saveAccount(currentAccount)
                    await lib.saveTiming(username, "success", time.perf_counter() - startTime, stages)

                    if 'follow_error' in locals() and follow_error is not None:
                        bar.set_description(f"Finished account generation with errors [{x + 1}/{executionCount}]")
                    else:
                        bar.set_description(f"Finished account generation [{x + 1}/{executionCount}]")

                    remaining = max(0, 100 - bar.n)
                    if remaining > 0:
                        bar.update(remaining)
                    bar.close()
            else:
                for i in page.cookies():
                    cookie = {
                        "name": i["name"],
                        "value": i["value"],
                    }
                    accountCookies.append(cookie)
                bar.update(10)

                if customization is True:
                    bar.set_description(f"Customizing account [{x + 1}/{executionCount}]")
                    await lib.customization(page)
                    bar.update(15)
                else:
                    bar.set_description(f"Skipping customization [{x + 1}/{executionCount}]")
                    bar.update(15)

                if following is True:
                    bar.set_description(f"Following users [{x + 1}/{executionCount}]")
                    try:
                        userIDs = await lib.followUser(followUserList, page)
                    except Exception as e:
                        print(f"An error occurred while following users: {e}")
                    bar.update(10)

                page.set.cookies.clear()
                page.clear_cache()
                chrome.set.cookies.clear()
                chrome.clear_cache()
                chrome.quit()
                email = None
                emailPassword = None

                currentAccount = {"username": username, "password": passw, "email": email, "emailPassword": emailPassword, "cookies": accountCookies}
                accounts.append(currentAccount)
                await lib.saveAccount(currentAccount)
                await lib.saveTiming(username, "success", time.perf_counter() - startTime, stages)

                bar.set_description(f"Finished account generation [{x + 1}/{executionCount}]")

                remaining = max(0, 100 - bar.n)
                if remaining > 0:
                    bar.update(remaining)
                bar.close()

    print(f"\nCreated {len(accounts)}/{executionCount} accounts, {len(failures)} failed.")
    for failure in failures:
        print(f"Failed {failure}")

    if not accounts:
        print("No accounts were successfully created.")
        return

    print("\033[1m" "Credentials:")

    for account in accounts:
        print(f"Username: {account['username']}, Password: {'*' * len(account['password'])}, Email: {account['email']}, Email Password: {'*' * len(account['emailPassword']) if account['emailPassword'] is not None else 'N/A'}")
    print("\033[0m" "\nCredentials saved to accounts.txt\nCookies are saved to cookies.json file\n\nHave fun playing Roblox!")

    accountManagerFormat = ask("EXPORT_TO_CLIPBOARD",
        "\nWould you like to export the account manager format into your clipboard? [y/n] (Default: No): "
    ) or "n"
    if accountManagerFormat.lower() in ["y", "yes"]:
        accountManagerFormatString = ""

        for account in accounts:
            roblosecurityCookie = None
            for cookie in account["cookies"]:
                if cookie["name"] == ".ROBLOSECURITY":
                    roblosecurityCookie = cookie["value"]
                    break

            if roblosecurityCookie:
                accountManagerFormatString += f"{roblosecurityCookie}\n"
            else:
                print(f"Warning: No .ROBLOSECURITY cookie found for user {account['username']}")

        pyperclip.copy(accountManagerFormatString)
        print("Account manager format (cookies) copied to clipboard!")
        print("Select the 'Cookie(s)' option in Roblox Account Manager and paste it into the input field.")
        print("Do note that you'll have to complete the signup process manually in Roblox Account Manager.\n")
    else:
        print()

    for i in range(5, 0, -1):
        print(f"\rExiting in {i} seconds...", end="", flush=True)
        await asyncio.sleep(1)
    print("\r\033[KExiting now...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nScript interrupted by user. Cleaning up...")
        try:
            gc.collect()
        except Exception:
            pass
        print("Cleanup complete.")
    except Exception as e:
        print(f"\nUnexpected error occurred: {e}")
        print("Please report this issue at https://qing762.is-a.dev/discord if the error persists.")
