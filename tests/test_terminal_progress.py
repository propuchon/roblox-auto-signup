import contextlib
import io
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib.diagnostics import close_logger, create_logger
from rich.console import Console
from lib.terminal_progress import TerminalProgress


class TtyOutput(io.StringIO):
    def isatty(self):
        return True


class TerminalProgressTests(unittest.TestCase):
    def test_unlimited_progress_shows_count_without_completion_percentage(self):
        output = io.StringIO()
        console = Console(file=output, width=160)
        display = TerminalProgress(console)
        try:
            display.start(0, lambda: {})
            for index in range(1, 4):
                display.begin(index, 0)
                display.finish("success", {})
            overall = display.progress.tasks[display.overall]
            self.assertIsNone(overall.total)
            self.assertFalse(overall.finished)
            console.print(display.progress.get_renderable())
            self.assertIn("3/∞ processed", output.getvalue())
            self.assertIn("Steps [3/∞]", output.getvalue())
        finally:
            display.close()
        self.assertEqual(overall.fields["status"], "stopped")

    @patch.dict(os.environ, {"TERM": "xterm"})
    def test_failed_attempt_does_not_show_skipped_stages_as_complete(self):
        output = io.StringIO()
        display = TerminalProgress(Console(file=output, width=160, force_terminal=True))
        metrics = {"response_encoded_bytes": 12500, "request_count": 3}
        try:
            display.start(2, lambda: metrics)
            display.begin(1, 2)
            display.stage("Reset HTTP session")
            display.advance()
            display.stage("GET signup form")
            display.advance()
            display.stage("POST signup")
            display.watch = SimpleNamespace(phase="waiting for response headers")
            display.progress.refresh()
            self.assertIn("waiting for response headers", output.getvalue())
            self.assertIn("RX 12.5 KB", output.getvalue())
            display.finish("failed", metrics)
            self.assertEqual(display.progress.tasks[display.account].completed, 3)
            self.assertEqual(display.progress.tasks[display.overall].completed, 1)
            self.assertEqual(display.failures, 1)
            display.begin(2, 2)
            self.assertEqual(display.metrics(), {})
            for _ in range(4):
                display.advance()
            display.finish("success", metrics)
            self.assertEqual(display.progress.tasks[display.account].completed, 5)
            self.assertEqual(display.successes, 1)
        finally:
            display.close()
        self.assertIn("\x1b[?25h", output.getvalue())  # Cursor is restored on close.

    def test_live_wait_is_in_file_only_and_errors_remain_visible(self):
        output = TtyOutput()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output), \
                patch.dict(os.environ, {"TERM": "xterm", "COLUMNS": "160"}):
            path = Path(directory) / "process.log"
            logger = create_logger(path, "test-progress")
            try:
                logger.progress.start(2, lambda: {})
                logger.progress.begin(1, 2)
                logger.info("WAIT pending-test-request")
                logger.error("BLOCKED test-timeout")
            finally:
                close_logger(logger)
            saved = path.read_text()
            self.assertIn("WAIT pending-test-request", saved)
            self.assertIn("BLOCKED test-timeout", saved)
            self.assertNotIn("\x1b", saved)
            self.assertNotIn("WAIT pending-test-request", output.getvalue())
            self.assertIn("BLOCKED test-timeout", output.getvalue())
            self.assertFalse(logger.progress.started)

    def test_disabled_or_redirected_output_keeps_plain_logs(self):
        for output, enabled in ((io.StringIO(), True), (TtyOutput(), False)):
            with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output):
                logger = create_logger(Path(directory) / "process.log", "test", show_progress=enabled)
                try:
                    self.assertIsNone(logger.progress)
                    logger.info("WAIT plain-output")
                finally:
                    close_logger(logger)
                self.assertIn("WAIT plain-output", output.getvalue())
                self.assertNotIn("\x1b", output.getvalue())

    @unittest.skipUnless(os.name == "posix", "PTY and SIGTERM integration requires POSIX")
    def test_live_terminal_termination_restores_cursor_and_exports(self):
        import json
        import pty
        fixture = Path(__file__).with_name("shutdown_fixture.py")
        for mode in ("terminal", "browser"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                master, slave = pty.openpty()
                process = subprocess.Popen([sys.executable, str(fixture), "interrupt", "dated", mode],
                                           cwd=directory, stdout=slave, stderr=slave,
                                           env={**os.environ, "TERM": "xterm"})
                os.close(slave)
                captured = bytearray()
                sent = False
                deadline = time.monotonic() + 10
                try:
                    while time.monotonic() < deadline:
                        if not sent and (Path(directory) / "ready").exists():
                            process.send_signal(signal.SIGTERM)
                            sent = True
                        if select.select([master], [], [], 0.05)[0]:
                            try:
                                chunk = os.read(master, 65536)
                            except OSError:
                                break
                            if not chunk:
                                break
                            captured.extend(chunk)
                    process.wait(timeout=2)
                    self.assertTrue(sent)
                    self.assertEqual(process.returncode, 128 + signal.SIGTERM, captured.decode(errors="replace"))
                    self.assertIn(b"\x1b[?25l", captured)
                    self.assertIn(b"\x1b[?25h", captured)
                    export = next(Path(directory).glob("output/*/results.json"))
                    results = json.loads(export.read_text())
                    self.assertEqual([row["status"] for row in results["results"]], ["success", "interrupted"])
                    self.assertNotIn("\x1b", (export.parent / "process.log").read_text())
                finally:
                    os.close(master)
                    if process.poll() is None:
                        process.kill()
                        process.wait()
