import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lib.run_export import RunExport
from lib.shutdown import StopRequested, stop_signals
import main


class ExportTests(unittest.TestCase):
    def test_finalization_preserves_already_recorded_success_on_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            exporter = RunExport(directory)
            exporter.begin(1, username="test", password="test")
            exporter.update(status="success")
            exporter.end("interrupted", "Stopped during export")
            saved = json.loads((exporter.directory / "results.json").read_text())
            self.assertEqual(saved["results"][0]["status"], "success")

    def test_failed_atomic_write_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            exporter = RunExport(directory)
            exporter.begin(1, username="test", password="test")
            exporter.finish("success")
            before = (exporter.directory / "results.json").read_bytes()
            with patch("lib.run_export.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    exporter.begin(2)
            self.assertEqual((exporter.directory / "results.json").read_bytes(), before)
            self.assertFalse(list(exporter.directory.glob(".export-*")))

    def run_fixture(self, mode, signum=None, export_mode="dated"):
        fixture = Path(__file__).with_name("shutdown_fixture.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if export_mode == "append":
                (root / "output").mkdir()
                for name in ("cookies.txt", "userpass.txt", "userfail.txt"):
                    (root / "output" / name).write_text("existing\n")
            process = subprocess.Popen([sys.executable, str(fixture), mode, export_mode], cwd=directory,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                if signum is not None:
                    deadline = time.monotonic() + 10
                    while not (root / "ready").exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail("Fixture did not reach the interruption point")
                        time.sleep(0.02)
                    process.send_signal(signum)
                stdout, stderr = process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            if export_mode == "dated":
                exports = list((root / "output").glob("*/results.json"))
                self.assertEqual(len(exports), 1, stdout + stderr)
                data = json.loads(exports[0].read_text())
                files = {path.name: path.read_text() for path in exports[0].parent.iterdir() if path.is_file()}
            else:
                data = None
                entries = list((root / "output").iterdir())
                self.assertEqual({path.name for path in entries}, {"cookies.txt", "userpass.txt", "userfail.txt"})
                files = {path.name: path.read_text() for path in entries}
            opened = len((root / "opened.txt").read_text().splitlines())
            closed = len((root / "closed.txt").read_text().splitlines())
            return process.returncode, data, files, opened, closed

    def test_captcha_stops_entire_run_in_both_modes(self):
        for mode in ("captcha", "headless_captcha"):
            with self.subTest(mode=mode):
                code, data, files, opened, closed = self.run_fixture(mode)
                self.assertEqual(code, 1)
                self.assertEqual(data["status"], "captcha")
                self.assertEqual([row["status"] for row in data["results"]], ["success", "failed"])
                self.assertIn("CAPTCHA", data["results"][1]["reason"])
                self.assertEqual((opened, closed), (2, 2))
                self.assertEqual(files["success.txt"], "test_1:test_password\n")
                self.assertEqual(files["failed.txt"], "test_2:test_password\n")

    def test_success_and_failure_export_without_external_export_command(self):
        code, data, files, opened, closed = self.run_fixture("failure")
        self.assertEqual(code, 1)
        self.assertEqual([row["status"] for row in data["results"]], ["success", "failed", "success"])
        self.assertEqual((opened, closed), (3, 3))
        self.assertEqual(data["counts"]["success"], 2)
        self.assertIn("test_2", files["results.csv"])
        self.assertEqual(len(files["cookies.txt"].splitlines()), 2)

    def test_successful_run_exports_and_exits_zero(self):
        code, data, files, opened, closed = self.run_fixture("success")
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["counts"]["success"], 3)
        self.assertEqual((opened, closed), (3, 3))
        self.assertEqual(len(files["success.txt"].splitlines()), 3)
        self.assertEqual(files["failed.txt"], "")

    def test_append_terminal_signals_keep_previous_and_completed_results(self):
        for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            with self.subTest(signum=signum):
                code, _, files, opened, closed = self.run_fixture("interrupt", signum, "append")
                self.assertEqual(code, 128 + signum)
                self.assertEqual((opened, closed), (2, 2))
                self.assertEqual(files["userpass.txt"], "existing\ntest_1:test_password\n")
                self.assertEqual(files["userfail.txt"], "existing\ntest_2:test_password\n")
                self.assertEqual(files["cookies.txt"], "existing\ntest_cookie\n")

    def test_append_captcha_keeps_existing_content_and_stops(self):
        code, _, files, opened, closed = self.run_fixture("captcha", export_mode="append")
        self.assertEqual(code, 1)
        self.assertEqual((opened, closed), (2, 2))
        self.assertEqual(files["userfail.txt"], "existing\ntest_2:test_password\n")
        self.assertEqual(files["userpass.txt"], "existing\ntest_1:test_password\n")

    def test_append_force_kill_preserves_already_written_results(self):
        if not hasattr(signal, "SIGKILL"):
            self.skipTest("SIGKILL unavailable")
        code, _, files, _, _ = self.run_fixture("interrupt", signal.SIGKILL, "append")
        self.assertEqual(code, -signal.SIGKILL)
        self.assertEqual(files["userpass.txt"], "existing\ntest_1:test_password\n")
        self.assertEqual(files["userfail.txt"], "existing\n")

    def test_terminal_signals_export_and_close_browser(self):
        for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            with self.subTest(signum=signum):
                code, data, files, opened, closed = self.run_fixture("interrupt", signum)
                self.assertEqual(code, 128 + signum)
                self.assertEqual(data["status"], "interrupted")
                self.assertEqual([row["status"] for row in data["results"]], ["success", "interrupted"])
                self.assertEqual((opened, closed), (2, 2))
                self.assertIn("test_2:test_password", files["interrupted.txt"])
                self.assertIn("test_1:test_password", files["success.txt"])

    def test_interrupt_after_signup_preserves_login_cookie(self):
        code, data, files, opened, closed = self.run_fixture("post_interrupt", signal.SIGTERM)
        self.assertEqual(code, 128 + signal.SIGTERM)
        current = data["results"][1]
        self.assertEqual(current["status"], "interrupted")
        self.assertTrue(current["account_created"])
        self.assertEqual(current["cookies"][0]["value"], "test_cookie")
        self.assertEqual(len(files["cookies.txt"].splitlines()), 2)
        self.assertEqual(opened, closed)

    @unittest.skipUnless(hasattr(signal, "SIGKILL"), "SIGKILL unavailable")
    def test_force_kill_leaves_completed_results_and_in_progress_checkpoint(self):
        code, data, files, opened, closed = self.run_fixture("interrupt", signal.SIGKILL)
        self.assertEqual(code, -signal.SIGKILL)
        self.assertEqual([row["status"] for row in data["results"]], ["success", "in_progress"])
        self.assertEqual(data["status"], "running")
        self.assertIn("test_1:test_password", files["success.txt"])
        self.assertEqual((opened, closed), (2, 1))


class ExportModeTests(unittest.TestCase):
    def test_signal_during_append_does_not_duplicate_written_line(self):
        import os
        original_fsync = os.fsync
        signaled = False

        def interrupt_once(descriptor):
            nonlocal signaled
            original_fsync(descriptor)
            if not signaled:
                signaled = True
                os.kill(os.getpid(), signal.SIGINT)

        with tempfile.TemporaryDirectory() as directory, stop_signals():
            exporter = RunExport(directory, mode="append")
            exporter.begin(1, username="test", password="password")
            with patch("lib.run_export.os.fsync", side_effect=interrupt_once):
                with self.assertRaises(StopRequested):
                    exporter.update(account_created=True,
                                    cookies=[{"name": ".ROBLOSECURITY", "value": "cookie"}])
            exporter.end("interrupted")
            self.assertEqual((Path(directory) / "userpass.txt").read_text(), "test:password\n")
            self.assertEqual((Path(directory) / "cookies.txt").read_text(), "cookie\n")

    def test_cli_export_mode(self):
        self.assertEqual(main.parse_args(["1"]).export_mode, "dated")
        self.assertEqual(main.parse_args(["1", "--export-mode", "append"]).export_mode, "append")

    def test_append_keeps_same_files_across_runs_without_duplicate_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ("cookies.txt", "userfail.txt", "userpass.txt")
            for name in names:
                (root / name).write_text("old_without_newline")
            inodes = {name: (root / name).stat().st_ino for name in names}
            for username in ("one", "two"):
                exporter = RunExport(directory, mode="append")
                exporter.begin(1, username=username, password="password")
                exporter.update(account_created=True, cookies=[{"name": ".ROBLOSECURITY", "value": username}])
                exporter.update(stage="Optional processing")
                exporter.finish("success")
                exporter.begin(2, username=username + "_failed", password="password")
                exporter.finish("failed")
                exporter.end("failed")
                exporter.save()
            self.assertEqual({path.name for path in root.iterdir()}, set(names))
            self.assertEqual({name: (root / name).stat().st_ino for name in names}, inodes)
            self.assertEqual((root / "cookies.txt").read_text(), "old_without_newline\none\ntwo\n")
            self.assertEqual((root / "userpass.txt").read_text(),
                             "old_without_newline\none:password\ntwo:password\n")
            self.assertEqual((root / "userfail.txt").read_text(),
                             "old_without_newline\none_failed:password\ntwo_failed:password\n")

    def test_append_preserves_created_account_when_optional_processing_is_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            exporter = RunExport(directory, mode="append")
            exporter.begin(1, username="test", password="password")
            exporter.update(account_created=True, cookies=[{"name": ".ROBLOSECURITY", "value": "cookie"}])
            exporter.end("interrupted")
            self.assertEqual((Path(directory) / "userpass.txt").read_text(), "test:password\n")
            self.assertEqual((Path(directory) / "userfail.txt").read_text(), "")

    def test_dated_has_generate_and_counted_format_files(self):
        with tempfile.TemporaryDirectory() as directory:
            exporter = RunExport(directory)
            exporter.begin(1, username="test", password="password")
            exporter.update(account_created=True, cookies=[{"name": ".ROBLOSECURITY", "value": "cookie"}])
            exporter.finish("success")
            exporter.end("success")
            root = exporter.directory
            self.assertEqual((root / "format" / "cookies_1.txt").read_text(), "cookie\n")
            self.assertEqual((root / "format" / "userpass_1.txt").read_text(), "test:password\n")
            self.assertEqual((root / "format" / "userfail_0.txt").read_text(), "")
            self.assertFalse((root / "format" / "cookies_0.txt").exists())
            self.assertEqual(json.loads((root / "generate" / "cookies.json").read_text())[0]["username"], "test")


if __name__ == "__main__":
    unittest.main()
