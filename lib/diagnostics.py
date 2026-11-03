"""Timestamped process logs and visible, bounded HTTP waits."""

from contextlib import contextmanager
from datetime import datetime
import logging
import os
import signal
import sys
import threading
import time
from zoneinfo import ZoneInfo


class BangkokFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, ZoneInfo("Asia/Bangkok")).isoformat(timespec="seconds")


def create_logger(path, run_id, show_progress=True, quiet=False):
    logger = logging.Logger(f"signup.{run_id}", level=logging.INFO)
    logger.progress = None
    terminal_handler = logging.StreamHandler(sys.stdout)
    if show_progress and sys.stdout.isatty() and os.environ.get("TERM") != "dumb":
        try:
            from rich.console import Console
            from rich.logging import RichHandler
            from lib.terminal_progress import HideLiveWaits, TerminalProgress
        except ImportError:
            pass  # Plain logs still work before optional terminal dependencies are installed.
        else:
            console = Console(file=sys.stdout)
            logger.progress = TerminalProgress(console)
            terminal_handler = RichHandler(console=console, show_time=False, show_level=False,
                                           show_path=False, markup=False, highlighter=None)
            terminal_handler.addFilter(HideLiveWaits(logger.progress))
    terminal_handler.setLevel(logging.WARNING if quiet else logging.INFO)
    formatter = BangkokFormatter(f"%(asctime)s %(levelname)-7s [run {run_id[:8]}] %(message)s")
    for handler in (terminal_handler, logging.FileHandler(path, mode="a", encoding="utf-8")):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def close_logger(logger):
    if getattr(logger, "progress", None):
        logger.progress.close()
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


class OperationTimeout(RuntimeError):
    # Do not inherit OSError/TimeoutError: urllib3 would wrap the signal exception
    # as a socket error and discard the total-deadline diagnostic.
    pass


class RequestWatch:
    def __init__(self, label, logger, timeout, interval, byte_counter=None):
        self.label, self.logger = label, logger
        self.timeout, self.interval = timeout, interval
        self.phase = "connecting/sending/waiting for response headers"
        self.received = 0
        self.byte_counter = byte_counter
        self.started = time.monotonic()

    def received_bytes(self):
        return self.byte_counter() if self.byte_counter else self.received

    def timeout_message(self):
        return (f"{self.label}: total request timeout {self.timeout:g}s exceeded; "
                f"phase={self.phase}; elapsed={time.monotonic() - self.started:.2f}s; "
                f"received={self.received_bytes()} B; request not retried")

    def check(self):
        if time.monotonic() - self.started >= self.timeout:
            raise OperationTimeout(self.timeout_message())

    @contextmanager
    def running(self):
        stopped = threading.Event()
        progress = getattr(self.logger, "progress", None)

        def heartbeat():
            while not stopped.wait(self.interval):
                if self.logger:
                    self.logger.info("WAIT %s | elapsed=%.1fs | phase=%s | received=%d B | deadline=%gs",
                                     self.label, time.monotonic() - self.started, self.phase,
                                     self.received_bytes(), self.timeout)

        def deadline(signum, frame):
            raise OperationTimeout(self.timeout_message())

        thread = None
        previous_handler = None
        timer_installed = False
        try:
            if progress:
                progress.watch = self
            if self.logger:
                thread = threading.Thread(target=heartbeat, name="http-progress", daemon=True)
                thread.start()
            # On macOS/Linux this interrupts DNS, socket reads, and slow trickle responses.
            # Do not take ownership of a timer already installed by another caller.
            if (threading.current_thread() is threading.main_thread()
                    and hasattr(signal, "setitimer")
                    and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)):
                previous_handler = signal.getsignal(signal.SIGALRM)
                signal.signal(signal.SIGALRM, deadline)
                signal.setitimer(signal.ITIMER_REAL, self.timeout)
                timer_installed = True
            yield self
            self.check()
        finally:
            if progress and progress.watch is self:
                progress.watch = None
            if timer_installed:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
            stopped.set()
            if thread:
                thread.join(timeout=1)
