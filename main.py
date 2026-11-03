"""Run headless, visible-browser or HTTP signup with live exports and byte counts."""

import argparse
import asyncio
from contextlib import contextmanager
from datetime import date, timedelta
import math
from itertools import count as iteration_count
import os
from pathlib import Path
import random
import re
import secrets
import sys
import time

from config import value_source
from lib.diagnostics import close_logger, create_logger
from lib.http_signup import (
    CaptchaDetected,
    DEFAULT_USER_AGENT,
    RateLimited,
    SignupClient,
)
from lib.lib import Main
from lib.proxies import parse_proxy, split_proxy_list
from lib.run_export import RunExport
from lib.shutdown import StopRequested, defer_stop_signals, stop_signals


def delay_range(key, default):
    value = os.environ.get(key) or default
    try:
        parts = value.split(",")
        if len(parts) != 2:
            raise ValueError
        low, high = (float(part.strip()) for part in parts)
        if (
            not all(math.isfinite(number) for number in (low, high))
            or not 0 <= low <= high
        ):
            raise ValueError
        return low, high
    except ValueError:
        raise ValueError(
            f"{key} must be min,max seconds with finite values and 0 <= min <= max"
        ) from None


def gather_config(cli_account_count=None):
    count = (
        cli_account_count
        if cli_account_count is not None
        else int(os.environ.get("ACCOUNT_COUNT") or "0")
    )
    if count < 0:
        raise ValueError("ACCOUNT_COUNT must be nonnegative (0 = unlimited)")
    prefix = os.environ.get("NAME_PREFIX", "").strip()
    if prefix and not re.fullmatch(r"[A-Za-z0-9_]+", prefix):
        raise ValueError("NAME_PREFIX must contain only letters, digits or underscores")
    birthday = os.environ.get("BIRTHDAY", "").strip()
    if birthday:
        birthday = date.fromisoformat(birthday).isoformat()
        if date.fromisoformat(birthday) >= date.today():
            raise ValueError("BIRTHDAY must be in the past (YYYY-MM-DD)")
    entries = split_proxy_list(os.environ.get("PROXIES", ""))
    proxy_file = os.environ.get("PROXY_FILE", "").strip()
    if proxy_file:
        path = Path(proxy_file).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        entries.extend(split_proxy_list(path.read_text(encoding="utf-8")))
    shared_user = os.environ.get("PROXY_USERNAME", "")
    shared = (
        (shared_user, os.environ.get("PROXY_PASSWORD", "")) if shared_user else None
    )
    proxies = []
    for index, entry in enumerate(entries, 1):
        try:
            proxy = parse_proxy(entry, shared)
        except ValueError as exc:
            raise ValueError(f"Invalid proxy entry {index}: {exc}") from None
        if proxy not in proxies:
            proxies.append(proxy)
    return dict(
        account_count=count,
        proxies=proxies,
        birthday=birthday,
        fixed_password=os.environ.get("PASSWORD", ""),
        name_format=prefix,
        scrambled=os.environ.get("SCRAMBLED_USERNAME", "y").lower() != "n",
        user_agent=os.environ.get("HTTP_USER_AGENT") or DEFAULT_USER_AGENT,
        done_delay=delay_range("RANDOM_WHEN_DONE", "30,45"),
        failed_delay=delay_range("RANDOM_WHEN_FAILED", "60,100"),
        captcha_wait=delay_range("CAPTCHA_BEFORE_CLOSE", "7,15"),
    )


async def main(
    cli_account_count=None,
    export_mode="dated",
    cost_per_gb=0,
    connect_timeout=10,
    read_timeout=30,
    request_timeout=60,
    heartbeat=5,
    mode="browser",
    browser_path=None,
    no_progress=False,
    quiet=False,
):
    exporter = RunExport(mode=export_mode)
    logger = create_logger(
        exporter.directory / "process.log",
        exporter.data["run_id"],
        show_progress=not no_progress,
        quiet=quiet,
    )
    started = time.perf_counter()
    run_status, reason = "failed", None
    try:
        exporter.data["mode"] = mode
        logger.info(
            "RUN START | mode=%s | export_mode=%s | output=%s",
            mode,
            export_mode,
            exporter.directory,
        )
        code = await run_workflow(
            cli_account_count,
            exporter,
            cost_per_gb,
            logger,
            connect_timeout,
            read_timeout,
            request_timeout,
            heartbeat,
            mode,
            browser_path,
        )
        run_status = "success" if code == 0 else "failed"
        return code
    except (CaptchaDetected, RateLimited) as exc:
        run_status = "captcha" if isinstance(exc, CaptchaDetected) else "rate_limited"
        reason = str(exc)
        logger.error("RUN BLOCKED | %s", reason)
        return 1
    except (StopRequested, KeyboardInterrupt, asyncio.CancelledError) as exc:
        run_status, reason = "interrupted", str(exc) or "Stopped by user"
        logger.warning("RUN INTERRUPTED | %s", reason)
        return 128 + exc.signum if isinstance(exc, StopRequested) else 130
    except BaseException as exc:
        reason = str(exc) or type(exc).__name__
        logger.error("RUN ERROR | %s: %s", type(exc).__name__, reason)
        raise
    finally:
        try:
            logger.info("EXPORT START | finalizing status=%s", run_status)
            exporter.end(run_status, reason)
            logger.info("EXPORT DONE | %s", exporter.directory)
            logger.info(
                "RUN END | status=%s | elapsed=%.2fs",
                run_status,
                time.perf_counter() - started,
            )
        finally:
            close_logger(logger)


async def wait_before_captcha_close(client, logger, progress, seconds):
    """Keep the CAPTCHA visible, measuring traffic, without delaying user stops."""
    deadline = time.monotonic() + seconds
    logger.warning(
        "[%s] CAPTCHA detected | checking for successful signup for up to %.2fs before closing; Ctrl+C closes early",
        client.account_label,
        seconds,
    )
    while True:
        result = client.confirmed_result()
        if result:
            logger.info(
                "[%s] SIGNUP CONFIRMED | fresh login cookie found during CAPTCHA wait; saving success",
                client.account_label,
            )
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        label = f"CAPTCHA detected — closing browser in {math.ceil(remaining)}s"
        if progress:
            progress.stage(label)
        logger.info(
            "WAIT [%s] %s | received=%d B",
            client.account_label,
            label,
            client.bandwidth()["response_encoded_bytes"],
        )
        await asyncio.sleep(min(1, remaining))


async def sleep_between_rounds(
    bounds, status, next_index, total, logger, progress, heartbeat=5
):
    seconds = random.uniform(*bounds)
    if seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    logger.info(
        "COOLDOWN START | after=%s | range=%g,%gs | selected=%.2fs | next=%d/%s",
        status,
        *bounds,
        seconds,
        next_index,
        total or "∞",
    )
    next_log = 0
    try:
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                break
            label = f"After {status}: next account {next_index}/{total or '∞'} in {math.ceil(remaining)}s"
            if progress:
                progress.cooldown(seconds, remaining, label)
            if now >= next_log:
                logger.info("WAIT COOLDOWN | %s", label)
                next_log = now + heartbeat
            await asyncio.sleep(min(1, remaining))
    finally:
        if progress:
            progress.end_cooldown()
    logger.info("COOLDOWN DONE | starting account %d/%s", next_index, total or "∞")


async def run_workflow(
    cli_account_count,
    exporter,
    cost_per_gb,
    logger,
    connect_timeout,
    read_timeout,
    request_timeout,
    heartbeat,
    mode="browser",
    browser_path=None,
):
    config = gather_config(cli_account_count)
    uses_browser = mode in {"terminal", "browser"}
    total = config["account_count"]
    done_delay = config.get("done_delay", (0, 0))
    failed_delay = config.get("failed_delay", (0, 0))
    captcha_wait = config.get("captcha_wait", (7, 15))
    for key, bounds in (
        ("RANDOM_WHEN_DONE", done_delay),
        ("RANDOM_WHEN_FAILED", failed_delay),
        ("CAPTCHA_BEFORE_CLOSE", captcha_wait),
    ):
        logger.info("%s=%g,%g seconds (from %s)", key, *bounds, value_source(key))
    logger.info(
        "ACCOUNT_COUNT=%d (from %s)",
        total,
        "CLI" if cli_account_count is not None else value_source("ACCOUNT_COUNT"),
    )
    logger.info(
        "PASSWORD=%s (from %s)",
        "[hidden]" if config["fixed_password"] else "[random per account]",
        value_source("PASSWORD"),
    )
    logger.info(
        "PROXIES=%d unique entries | inline source=%s | file source=%s",
        len(config["proxies"]),
        value_source("PROXIES"),
        value_source("PROXY_FILE"),
    )
    for key in (
        "INCOGNITO",
        "BROWSER_PATH",
        "EMAIL_VERIFICATION",
        "CUSTOMIZATION",
        "FOLLOW_USERS",
    ):
        if key == "BROWSER_PATH" and uses_browser and browser_path is not None:
            logger.info("BROWSER_PATH=[configured] (from CLI)")
            continue
        if key in os.environ:
            value = os.environ[key].lower()
            display = (
                value
                if key in {"INCOGNITO", "EMAIL_VERIFICATION", "CUSTOMIZATION"}
                and value in {"y", "n", ""}
                else "[configured]"
            )
            used = uses_browser and key in {"INCOGNITO", "BROWSER_PATH"}
            logger.info(
                "%s=%s (from %s; %s)",
                key,
                display,
                value_source(key),
                "used by browser transport" if used else "ignored in this mode",
            )
    if not uses_browser:
        logger.info(
            "TIMEOUTS | connect=%gs | read_idle=%gs | request_total=%gs | heartbeat=%gs",
            connect_timeout,
            read_timeout,
            request_timeout,
            heartbeat,
        )
    else:
        logger.info(
            "TIMEOUTS | browser_operation=%gs | heartbeat=%gs | connect/read flags apply to http mode only",
            request_timeout,
            heartbeat,
        )
        logger.info(
            "BANDWIDTH SCOPE | observed browser page requests/assets via CDP; excludes unobserved frames/workers, background traffic and TLS overhead"
        )
    exporter.data["requested_count"] = total
    exporter.save()
    lib = Main()
    if uses_browser:
        from lib.browser_signup import BrowserSignupClient

        client = BrowserSignupClient(
            browser_path=browser_path or os.environ.get("BROWSER_PATH", ""),
            incognito=os.environ.get("INCOGNITO", "n").lower() == "y",
            headless=mode == "terminal",
        )
    else:
        client = SignupClient(user_agent=config["user_agent"])
    logger.info(
        "TRANSPORT | mode=%s | engine=%s | headless=%s",
        mode,
        "browser" if uses_browser else "requests",
        mode == "terminal" if uses_browser else "n/a",
    )
    client.logger = logger
    client.timeout = (connect_timeout, read_timeout)
    client.request_timeout = request_timeout
    client.heartbeat = heartbeat
    progress = getattr(logger, "progress", None)
    if progress:
        progress.start(total, client.bandwidth)
    successes = failures = 0
    try:
        for index in (iteration_count() if total == 0 else range(total)):
            client.account_label = f"{index + 1}/{total or '∞'}"
            proxy = (
                config["proxies"][index % len(config["proxies"])]
                if config["proxies"]
                else None
            )
            logger.info(
                "[%s] ACCOUNT START | Using proxy: %s",
                client.account_label,
                proxy.server if proxy else "none (direct connection)",
            )
            started = time.perf_counter()
            password = config["fixed_password"] or secrets.token_urlsafe(12)
            status, reason = "interrupted", "Attempt did not finish"
            stages = []
            if progress:
                progress.begin(index + 1, total)
            exporter.begin(
                index + 1, password=password, proxy=proxy.server if proxy else None
            )

            @contextmanager
            def stage(label):
                if progress:
                    progress.stage(label)
                stage_started = time.perf_counter()
                stages.append(f"{label}@{time.perf_counter() - started:.1f}s")
                exporter.update(
                    stage=label, stages=stages[:], bandwidth=client.bandwidth()
                )
                logger.info(
                    "[%s] STAGE START | %s | account_elapsed=%.2fs",
                    client.account_label,
                    label,
                    time.perf_counter() - started,
                )
                try:
                    yield
                except BaseException as exc:
                    logger.error(
                        "[%s] STAGE FAILED | %s | elapsed=%.2fs | %s: %s",
                        client.account_label,
                        label,
                        time.perf_counter() - stage_started,
                        type(exc).__name__,
                        str(exc) or "interrupted",
                    )
                    raise
                else:
                    if progress:
                        progress.advance()
                    logger.info(
                        "[%s] STAGE DONE | %s | elapsed=%.2fs",
                        client.account_label,
                        label,
                        time.perf_counter() - stage_started,
                    )

            async def recover_captcha(error):
                if not uses_browser:
                    raise error
                seconds = random.uniform(*captcha_wait)
                exporter.update(
                    reason=str(error),
                    stage=f"CAPTCHA detected; checking signup for {seconds:.2f}s",
                    bandwidth=client.bandwidth(),
                )
                confirmed = await wait_before_captcha_close(client, logger, progress, seconds)
                if confirmed is None:
                    raise error
                return confirmed

            try:
                with stage("Open browser" if uses_browser else "Reset HTTP session"):
                    client.begin_round(proxy)
                logger.info(
                    "[%s] RESET DONE | %s",
                    client.account_label,
                    (
                        "fresh browser profile; cookies/cache cleared"
                        if uses_browser
                        else "cookies and CSRF cleared; no body cache"
                    ),
                )
                username = lib.usernameCreator(
                    config["name_format"], scrambled=config["scrambled"]
                )
                birthday = (
                    config["birthday"]
                    or (
                        date(2000, 1, 1) + timedelta(days=random.randrange(2922))
                    ).isoformat()
                )
                exporter.update(username=username, birthday=birthday)
                result = None
                with stage("Open signup page" if uses_browser else "GET signup form"):
                    try:
                        client.load_form()
                    except CaptchaDetected as exc:
                        result = await recover_captcha(exc)
                with stage(
                    "Fill form and click Sign Up" if uses_browser else "POST signup"
                ):
                    if result is None:
                        try:
                            result = client.submit(
                                username, password, birthday + "T00:00:00.000Z"
                            )
                        except CaptchaDetected as exc:
                            result = await recover_captcha(exc)
                account = dict(
                    username=username,
                    password=password,
                    email=None,
                    emailPassword=None,
                    cookies=result["cookies"],
                )
                exporter.update(
                    **account,
                    account_created=True,
                    user_id=result["user_id"],
                    stage="Signup confirmed",
                    reason=None,
                    bandwidth=client.bandwidth(),
                )
                with stage("Save account data"):
                    await lib.saveAccount(account)
                status, reason = "success", None
                successes += 1
            except CaptchaDetected as exc:
                status, reason = "failed", str(exc)
                failures += 1
                logger.warning(
                    "[%s] ACCOUNT FAILED | %s | remaining accounts follow the failed cooldown",
                    client.account_label,
                    reason,
                )
            except RateLimited as exc:
                status, reason = "failed", str(exc)
                raise
            except (StopRequested, KeyboardInterrupt, asyncio.CancelledError) as exc:
                status, reason = "interrupted", str(exc) or "Stopped by user"
                if exporter.active and "CAPTCHA" in (
                    exporter.active.get("reason") or ""
                ):
                    reason += "; CAPTCHA detected"
                raise
            except Exception as exc:
                status, reason = "failed", str(exc)
                failures += 1
                logger.error("[%s] ACCOUNT FAILED | %s", client.account_label, reason)
            finally:
                with defer_stop_signals():
                    if progress:
                        progress.stage(
                            "Close browser / export"
                            if uses_browser
                            else "Export results"
                        )
                    if uses_browser:
                        if (
                            exporter.active
                            and exporter.active["username"]
                            and not exporter.active["account_created"]
                        ):
                            recovered = client.confirmed_result()
                            if recovered:
                                exporter.update(
                                    cookies=recovered["cookies"],
                                    account_created=True,
                                    user_id=recovered["user_id"],
                                )
                                logger.info(
                                    "[%s] LOGIN COOKIE SAVED | captured before browser cleanup",
                                    client.account_label,
                                )
                        try:
                            client.finish_round()
                        except Exception as exc:
                            logger.warning(
                                "[%s] Browser cleanup failed (%s); saving available results",
                                client.account_label,
                                type(exc).__name__,
                            )
                    logger.info(
                        "[%s] EXPORT START | saving result and bandwidth",
                        client.account_label,
                    )
                    metrics = client.bandwidth()
                    metrics["mode"] = mode
                    metrics["transport"] = "browser" if uses_browser else "http"
                    metrics["headless"] = mode == "terminal" if uses_browser else None
                    metrics["estimated_cost"] = (
                        metrics["http_bytes_estimate"] / 1_000_000_000 * cost_per_gb
                        if cost_per_gb
                        else None
                    )
                    exporter.update(bandwidth=metrics)
                    exporter.record_bandwidth(index + 1, status, metrics)
                    exporter.finish(
                        status,
                        reason,
                        seconds=time.perf_counter() - started,
                        stages=stages[:],
                    )
                    if progress:
                        progress.finish(status, metrics)
                    logger.info("[%s] EXPORT DONE", client.account_label)
                logger.info(
                    "[%s] ACCOUNT END | status=%s | elapsed=%.2fs | request_body=%d B | "
                    "received=%d B | decoded=%d B | 304=%d | reason=%s",
                    client.account_label,
                    status,
                    time.perf_counter() - started,
                    metrics["request_body_bytes"],
                    metrics["response_encoded_bytes"],
                    metrics["response_decoded_bytes"],
                    metrics["not_modified"],
                    reason or "none",
                )
                logger.info(
                    "[%s] BANDWIDTH | mode=%s | requests=%d | total_estimate=%d B (%.4f MB) | estimated_cost=%s",
                    client.account_label,
                    mode,
                    metrics["request_count"],
                    metrics["http_bytes_estimate"],
                    metrics["http_bytes_estimate"] / 1_000_000,
                    metrics["estimated_cost"],
                )
            # The attempt is already exported and its browser closed. User stops
            # during this wait must not create an extra interrupted account row.
            if total == 0 or index + 1 < total:
                bounds = done_delay if status == "success" else failed_delay
                await sleep_between_rounds(
                    bounds, status, index + 2, total, logger, progress, heartbeat
                )
    finally:
        logger.info("CLEANUP START | closing %s client", mode)
        try:
            client.close()
        except Exception as exc:
            logger.warning("CLEANUP FAILED | %s client | %s", mode, type(exc).__name__)
            raise
        logger.info("CLEANUP DONE | %s client closed", mode)
    logger.info("SUMMARY | created=%d/%s | failed=%d", successes, total or "∞", failures)
    return 1 if failures else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Headless terminal, visible-browser or HTTP signup with exports and bandwidth logging"
    )
    parser.add_argument("count", nargs="?", type=int, default=None,
                        help="Number of attempts; 0 = unlimited (default: ACCOUNT_COUNT or 0)")
    parser.add_argument(
        "--mode",
        choices=("terminal", "browser", "http"),
        default="browser",
        help="browser: visible browser (default); terminal: headless browser; http: Requests only",
    )
    parser.add_argument(
        "--browser-path",
        default=None,
        help="Chromium executable (terminal/browser modes); overrides BROWSER_PATH",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable live terminal loading bars; keep plain logs",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide detailed console logs; keep progress, warnings/errors and full process.log (default: show logs)",
    )
    parser.add_argument(
        "--export-mode",
        choices=("dated", "append"),
        default="dated",
        help="dated: accumulate in output/YYYY-MM-DD (Bangkok); append: accumulate directly in output",
    )
    parser.add_argument(
        "--cost-per-gb",
        type=float,
        default=0,
        help="Optional price per decimal GB, in your billing currency (HTTP estimate only)",
    )
    for name, default, help_text in (
        ("connect-timeout", 10, "Connection timeout, seconds"),
        ("read-timeout", 30, "Read inactivity timeout, seconds"),
        (
            "request-timeout",
            60,
            "Deadline for each HTTP request or browser operation, seconds",
        ),
        ("heartbeat", 5, "Print waiting status every N seconds"),
    ):
        parser.add_argument(f"--{name}", type=float, default=default, help=help_text)
    args = parser.parse_args(argv)
    if args.count is not None and args.count < 0:
        parser.error("Account count must be nonnegative (0 = unlimited)")
    if not math.isfinite(args.cost_per_gb) or args.cost_per_gb < 0:
        parser.error("--cost-per-gb must be finite and nonnegative")
    for name in ("connect_timeout", "read_timeout", "request_timeout", "heartbeat"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    return args


if __name__ == "__main__":
    args = parse_args()
    try:
        with stop_signals():
            code = asyncio.run(
                main(
                    args.count,
                    export_mode=args.export_mode,
                    cost_per_gb=args.cost_per_gb,
                    connect_timeout=args.connect_timeout,
                    read_timeout=args.read_timeout,
                    request_timeout=args.request_timeout,
                    heartbeat=args.heartbeat,
                    mode=args.mode,
                    browser_path=args.browser_path,
                    no_progress=args.no_progress,
                    quiet=args.quiet,
                )
            )
        sys.exit(code)
    except StopRequested as exc:
        sys.exit(128 + exc.signum)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)
