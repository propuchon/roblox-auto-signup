import json
from datetime import datetime, timezone
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
    def test_daily_requested_count_remains_unlimited_with_finite_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            unlimited = RunExport(directory)
            unlimited.data["requested_count"] = 0
            unlimited.end("interrupted")
            finite = RunExport(directory)
            finite.data["requested_count"] = 3
            finite.end("success")
            daily = json.loads((finite.directory / "results.json").read_text())
            self.assertEqual(daily["requested_count"], 0)
            self.assertEqual([run["requested_count"] for run in daily["runs"]], [0, 3])

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

    def run_fixture(self, mode, signum=None, export_mode="dated", engine="http"):
        fixture = Path(__file__).with_name("shutdown_fixture.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if export_mode == "append":
                (root / "output").mkdir()
                for name in ("cookies.txt", "userpass.txt", "userfail.txt"):
                    (root / "output" / name).write_text("existing\n")
            process = subprocess.Popen([sys.executable, str(fixture), mode, export_mode, engine], cwd=directory,
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
                self.assertEqual({path.name for path in entries}, {"cookies.txt", "userpass.txt", "userfail.txt", "bandwidth.jsonl", "process.log"})
                files = {path.name: path.read_text() for path in entries}
            opened = len((root / "opened.txt").read_text().splitlines())
            closed = len((root / "closed.txt").read_text().splitlines()) if (root / "closed.txt").exists() else 0
            if engine in {"terminal", "browser"} and signum != getattr(signal, "SIGKILL", None):
                rounds = (root / "rounds_closed.txt").read_text().splitlines()
                self.assertEqual(rounds, ["1"] if "cooldown_interrupt" in mode else ["1", "2"])
            return process.returncode, data, files, opened, closed

    def test_browser_signals_preserve_bandwidth_results_and_close_each_round(self):
        for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            with self.subTest(signum=signum):
                code, data, files, opened, closed = self.run_fixture("interrupt", signum, engine="browser")
                self.assertEqual(code, 128 + signum)
                self.assertEqual((opened, closed), (1, 1))
                self.assertEqual(data["mode"], "browser")
                metrics = [json.loads(line) for line in files["bandwidth.jsonl"].splitlines()]
                self.assertEqual([row["status"] for row in metrics], ["success", "interrupted"])
                self.assertTrue(all(row["mode"] == "browser" and row["http_bytes_estimate"] > 0 for row in metrics))

    def test_stop_during_cooldown_preserves_completed_attempt_without_extra_row(self):
        for mode, expected in (("cooldown_interrupt", "success"), ("failed_cooldown_interrupt", "failed")):
            for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
                with self.subTest(mode=mode, signum=signum):
                    code, data, files, opened, closed = self.run_fixture(mode, signum, engine="terminal")
                    self.assertEqual(code, 128 + signum)
                    self.assertEqual(data["status"], "interrupted")
                    self.assertEqual([row["status"] for row in data["results"]], [expected])
                    self.assertEqual(len(files["bandwidth.jsonl"].splitlines()), 1)
                    self.assertEqual((opened, closed), (1, 1))

    def test_stop_during_captcha_countdown_exports_and_closes_early(self):
        started = time.monotonic()
        code, data, files, opened, closed = self.run_fixture("captcha_wait", signal.SIGTERM, engine="browser")
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(code, 128 + signal.SIGTERM)
        self.assertEqual((opened, closed), (1, 1))
        self.assertEqual(data["status"], "interrupted")
        self.assertEqual(data["results"][1]["status"], "interrupted")
        self.assertIn("CAPTCHA", data["results"][1]["reason"])
        self.assertEqual(len(files["bandwidth.jsonl"].splitlines()), 2)

    def test_captcha_failure_exports_and_continues_remaining_attempts(self):
        for mode in ("captcha", "captcha_form"):
            with self.subTest(mode=mode):
                code, data, files, opened, closed = self.run_fixture(mode)
                self.assertEqual(code, 1)
                self.assertEqual(data["status"], "failed")
                self.assertEqual([row["status"] for row in data["results"]], ["success", "failed", "success"])
                self.assertIn("CAPTCHA", data["results"][1]["reason"])
                self.assertEqual((opened, closed), (1, 1))
                self.assertEqual(files["success.txt"], "test_1:test_password\ntest_3:test_password\n")
                self.assertEqual(files["failed.txt"], "test_2:test_password\n")

    def test_success_and_failure_export_without_external_export_command(self):
        code, data, files, opened, closed = self.run_fixture("failure")
        self.assertEqual(code, 1)
        self.assertEqual([row["status"] for row in data["results"]], ["success", "failed", "success"])
        self.assertEqual((opened, closed), (1, 1))
        self.assertEqual(data["counts"]["success"], 2)
        self.assertIn("test_2", files["results.csv"])
        self.assertEqual(len(files["cookies.txt"].splitlines()), 2)

    def test_successful_run_exports_and_exits_zero(self):
        code, data, files, opened, closed = self.run_fixture("success")
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["counts"]["success"], 3)
        self.assertEqual((opened, closed), (1, 1))
        self.assertEqual(len(files["success.txt"].splitlines()), 3)
        self.assertEqual(files["failed.txt"], "")

    def test_append_terminal_signals_keep_previous_and_completed_results(self):
        for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            with self.subTest(signum=signum):
                code, _, files, opened, closed = self.run_fixture("interrupt", signum, "append")
                self.assertEqual(code, 128 + signum)
                self.assertEqual((opened, closed), (1, 1))
                self.assertEqual(files["userpass.txt"], "existing\ntest_1:test_password\n")
                self.assertEqual(files["userfail.txt"], "existing\ntest_2:test_password\n")
                self.assertEqual(files["cookies.txt"], "existing\ntest_cookie\n")

    def test_append_captcha_keeps_existing_content_and_continues(self):
        code, _, files, opened, closed = self.run_fixture("captcha", export_mode="append")
        self.assertEqual(code, 1)
        self.assertEqual((opened, closed), (1, 1))
        self.assertEqual(files["userfail.txt"], "existing\ntest_2:test_password\n")
        self.assertEqual(files["userpass.txt"], "existing\ntest_1:test_password\ntest_3:test_password\n")

    def test_append_force_kill_preserves_already_written_results(self):
        if not hasattr(signal, "SIGKILL"):
            self.skipTest("SIGKILL unavailable")
        code, _, files, _, _ = self.run_fixture("interrupt", signal.SIGKILL, "append")
        self.assertEqual(code, -signal.SIGKILL)
        self.assertEqual(files["userpass.txt"], "existing\ntest_1:test_password\n")
        self.assertEqual(files["userfail.txt"], "existing\n")

    def test_terminal_signals_export_and_close_client(self):
        for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            with self.subTest(signum=signum):
                code, data, files, opened, closed = self.run_fixture("interrupt", signum)
                self.assertEqual(code, 128 + signum)
                self.assertEqual(data["status"], "interrupted")
                self.assertEqual([row["status"] for row in data["results"]], ["success", "interrupted"])
                self.assertEqual((opened, closed), (1, 1))
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
        self.assertEqual((opened, closed), (1, 0))


class ExportModeTests(unittest.TestCase):
    def test_dated_reuses_day_and_preserves_all_runs_without_duplicate_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            first = RunExport(directory)
            first.begin(1, username="first", password="password")
            first.update(account_created=True, cookies=[{"name": ".ROBLOSECURITY", "value": "first-cookie"}])
            first.record_bandwidth(1, "success", {"http_bytes_estimate": 100})
            first.finish("success")
            first.end("success")
            paths = [first.directory / name for name in ("userpass.txt", "userfail.txt", "cookies.txt")]
            inodes = [path.stat().st_ino for path in paths]
            second = RunExport(directory)
            self.assertEqual(second.directory, first.directory)
            second.begin(1, username="second", password="password")
            second.finish("failed")
            second.record_bandwidth(1, "failed", {"http_bytes_estimate": 50})
            second.end("failed")
            second.save()
            first.save()  # A late checkpoint from an earlier writer must retain the newer run.
            self.assertEqual([path.stat().st_ino for path in paths], inodes)
            daily = json.loads((first.directory / "results.json").read_text())
            self.assertEqual(len(daily["runs"]), 2)
            self.assertEqual([row["username"] for row in daily["results"]], ["first", "second"])
            self.assertEqual({row["run_id"] for row in daily["results"]}, {first.data["run_id"], second.data["run_id"]})
            self.assertEqual(daily["counts"]["success"], 1)
            self.assertEqual(daily["counts"]["failed"], 1)
            self.assertEqual(paths[0].read_text(), "first:password\n")
            self.assertEqual(paths[1].read_text(), "second:password\n")
            self.assertEqual(paths[2].read_text(), "first-cookie\n")
            self.assertEqual(len((first.directory / "bandwidth.jsonl").read_text().splitlines()), 2)
            self.assertEqual((first.directory / "format/userpass_1.txt").read_text(), "first:password\n")
            self.assertEqual((first.directory / "format/userfail_1.txt").read_text(), "second:password\n")
            self.assertFalse((first.directory / "format/userfail_0.txt").exists())
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_dated_uses_bangkok_date_and_keeps_run_in_start_day(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("lib.run_export.datetime") as clock:
                clock.now.return_value = datetime(2026, 11, 3, 16, 59, tzinfo=timezone.utc)
                first = RunExport(directory)
                self.assertEqual(first.directory.name, "2026-11-03")
                clock.now.return_value = datetime(2026, 11, 3, 17, 1, tzinfo=timezone.utc)
                first.begin(1, username="before-midnight", password="password")
                first.finish("success")
                first.end("success")
                second = RunExport(directory)
                self.assertEqual(second.directory.name, "2026-11-04")
                self.assertEqual(first.directory.name, "2026-11-03")
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_corrupt_daily_snapshot_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            exporter = RunExport(directory)
            exporter.begin(1, username="existing", password="password")
            exporter.finish("success")
            snapshot = exporter.directory / "results.json"
            snapshot.write_text("invalid existing JSON")
            with self.assertRaisesRegex(ValueError, "existing data was not replaced"):
                RunExport(directory)
            self.assertEqual(snapshot.read_text(), "invalid existing JSON")
            self.assertEqual((exporter.directory / "userpass.txt").read_text(), "existing:password\n")

    @unittest.skipUnless(sys.platform != "win32", "Daily cross-process lock uses POSIX flock")
    def test_parallel_daily_writers_preserve_all_accounts(self):
        script = '''
import sys, time
sys.path.insert(0, sys.argv[3])
from lib.run_export import RunExport
exporter = RunExport(sys.argv[1])
exporter.data["requested_count"] = 2
for index in (1, 2):
    username = sys.argv[2] + str(index)
    exporter.begin(index, username=username, password="password")
    exporter.update(account_created=True, cookies=[{"name": ".ROBLOSECURITY", "value": username}])
    time.sleep(0.02)
    exporter.record_bandwidth(index, "success", {"http_bytes_estimate": 100})
    exporter.finish("success")
exporter.end("success")
'''
        with tempfile.TemporaryDirectory() as directory:
            project = str(Path(__file__).resolve().parents[1])
            processes = [subprocess.Popen([sys.executable, "-c", script, directory, f"writer{index}_", project],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                         for index in range(4)]
            try:
                for process in processes:
                    stdout, stderr = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            root = next(Path(directory).iterdir())
            daily = json.loads((root / "results.json").read_text())
            self.assertEqual(len(daily["runs"]), 4)
            self.assertEqual(len(daily["results"]), 8)
            self.assertEqual(daily["counts"]["success"], 8)
            self.assertEqual(daily["requested_count"], 8)
            for name in ("userpass.txt", "cookies.txt", "bandwidth.jsonl"):
                self.assertEqual(len((root / name).read_text().splitlines()), 8)
            self.assertEqual(len(json.loads((root / "generate/cookies.json").read_text())), 8)
            self.assertEqual(len((root / "format/userpass_8.txt").read_text().splitlines()), 8)

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
