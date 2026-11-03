"""Incremental daily or flat append exports with per-run checkpoints."""

import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import uuid
from zoneinfo import ZoneInfo
from lib.shutdown import defer_stop_signals

try:
    import fcntl
except ImportError:
    fcntl = None


def atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".export-", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class RunExport:
    def __init__(self, root="output", mode="dated"):
        if mode not in {"dated", "append"}:
            raise ValueError("Export mode must be dated or append")
        self.mode = mode
        started = datetime.now(timezone.utc)
        self.day = started.astimezone(ZoneInfo("Asia/Bangkok")).date().isoformat()
        self.directory = Path(root).resolve()
        if mode == "dated":
            self.directory /= self.day
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._written = set()
        if mode == "dated":
            (self.directory / "generate").mkdir(exist_ok=True)
            (self.directory / "format").mkdir(exist_ok=True)
        for name in ("cookies.txt", "userfail.txt", "userpass.txt"):
            with (self.directory / name).open("ab"):
                pass
        self.data = dict(run_id=uuid.uuid4().hex, started_at=started.isoformat(),
                         status="running", reason=None, requested_count=None, results=[])
        self.active = None
        self.save()

    def begin(self, index, **values):
        self.active = dict(index=index, status="in_progress", username=None, password=None,
                           email=None, emailPassword=None, cookies=[], account_created=False,
                           stage="Initializing", stages=[], seconds=0, reason=None)
        self.active.update(values)
        self.data["results"].append(self.active)
        self.save()

    def update(self, **values):
        if self.active is not None:
            self.active.update(values)
            self.save()

    def finish(self, status, reason=None, **values):
        self.update(status=status, reason=reason, **values)
        self.active = None

    def end(self, status, reason=None):
        if self.active is not None:
            if self.active["status"] == "in_progress":
                self.finish("interrupted" if status == "interrupted" else "failed", reason)
            else:
                self.active = None  # Preserve a result already finalized before a signal.
        self.data.update(status=status, reason=reason,
                         finished_at=datetime.now(timezone.utc).isoformat())
        self.save()

    def save(self):
        rows = self.data["results"]
        self.data["counts"] = {status: sum(row["status"] == status for row in rows)
                               for status in ("success", "failed", "interrupted", "in_progress")}
        if self.mode == "append":
            self._save_append(rows)
            return
        # Lock the complete read/merge/write operation, not only the replacement.
        # Other processes may have updated this day's snapshot since our last save.
        with defer_stop_signals(), (self.directory / ".export.lock").open("a+b") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            daily = self._merge_daily()
            self._save_daily_snapshot(daily)

    def _merge_daily(self):
        path = self.directory / "results.json"
        runs, previous_rows = [], []
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                previous_rows = previous["results"]
                runs = previous.get("runs", [{key: value for key, value in previous.items()
                                              if key != "results"}])
                if not isinstance(previous_rows, list) or not isinstance(runs, list):
                    raise ValueError("Invalid snapshot structure")
                ids = [run["run_id"] for run in runs]
                if len(set(ids)) != len(ids) or not all(isinstance(value, str) for value in ids):
                    raise ValueError("Invalid run IDs")
                required = {"index", "status", "username", "password", "email", "emailPassword",
                            "account_created", "seconds", "stage", "stages", "reason", "cookies"}
                for row in previous_rows:
                    if not isinstance(row, dict) or not required.issubset(row):
                        raise ValueError("Invalid result row")
                    row.setdefault("run_id", previous["run_id"])
                    if row["run_id"] not in ids or not isinstance(row["cookies"], list):
                        raise ValueError("Invalid result owner/cookies")
                    if any(not isinstance(cookie, dict) or not {"name", "value"}.issubset(cookie)
                           for cookie in row["cookies"]):
                        raise ValueError("Invalid cookie record")
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError("Cannot merge existing daily results.json; existing data was not replaced") from exc
        run_id = self.data["run_id"]
        metadata = {key: value for key, value in self.data.items() if key != "results"}
        existing_ids = [run["run_id"] for run in runs]
        if run_id in existing_ids:
            runs[existing_ids.index(run_id)] = metadata
        else:
            runs.append(metadata)
        by_run = {run["run_id"]: [] for run in runs}
        for row in previous_rows:
            by_run[row["run_id"]].append(row)
        by_run[run_id] = [dict(row, run_id=run_id) for row in self.data["results"]]
        rows = [row for run in runs for row in by_run[run["run_id"]]]
        return dict(self.data, schema_version=2, date=self.day, timezone="Asia/Bangkok",
                    runs=runs, results=rows, last_updated_run_id=run_id,
                    requested_count=(0 if any(run.get("requested_count") == 0 for run in runs)
                                     else sum(run.get("requested_count") or 0 for run in runs)),
                    counts={status: sum(row["status"] == status for row in rows)
                            for status in ("success", "failed", "interrupted", "in_progress")})

    def _save_daily_snapshot(self, daily):
        rows = daily["results"]
        # Canonical snapshot is replaced first; derived formats may lag after SIGKILL.
        atomic_write(self.directory / "results.json", json.dumps(daily, indent=2))
        self._save_append(self.data["results"])
        buffer = io.StringIO(newline="")
        fields = ["run_id", "index", "status", "username", "password", "email", "account_created",
                  "seconds", "stage", "reason"]
        writer = csv.DictWriter(buffer, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        atomic_write(self.directory / "results.csv", buffer.getvalue())
        for status in ("success", "failed", "interrupted", "in_progress"):
            text = "".join(f"{row['username'] or ''}:{row['password'] or ''}\n"
                           for row in rows if row["status"] == status)
            atomic_write(self.directory / f"{status}.txt", text)
        cookies = [cookie["value"] for row in rows for cookie in row["cookies"]
                   if cookie["name"] == ".ROBLOSECURITY"]
        self._save_legacy_layout(rows, cookies)

    def _append_once(self, key, filename, value):
        if key in self._written:
            return
        with defer_stop_signals():
            with (self.directory / filename).open("a+b") as stream:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                stream.seek(0, os.SEEK_END)
                prefix = b""
                if stream.tell():
                    stream.seek(-1, os.SEEK_END)
                    if stream.read(1) != b"\n":
                        prefix = b"\n"
                stream.write(prefix + (value + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
                self._written.add(key)

    def _save_append(self, rows):
        for row in rows:
            # Export a captured account immediately, even during optional processing.
            created = row["account_created"] or row["status"] == "success"
            if row["username"]:
                credential = f"{row['username']}:{row['password'] or ''}"
                if created:
                    self._append_once((row["index"], "userpass"), "userpass.txt", credential)
                elif row["status"] in {"failed", "interrupted"}:
                    self._append_once((row["index"], "userfail"), "userfail.txt", credential)
            for cookie in row["cookies"]:
                if cookie["name"] == ".ROBLOSECURITY":
                    self._append_once((row["index"], "cookie", cookie["value"]),
                                      "cookies.txt", cookie["value"])

    def record_bandwidth(self, index, status, metrics):
        record = dict(run_id=self.data["run_id"], round=index, status=status,
                      timestamp=datetime.now(timezone.utc).isoformat(), **metrics)
        self._append_once((index, "bandwidth"), "bandwidth.jsonl", json.dumps(record))

    def _save_legacy_layout(self, rows, cookies):
        created = [row for row in rows if row["account_created"] or row["status"] == "success"]
        failed = [row for row in rows if row["status"] in {"failed", "interrupted"}
                  and not row["account_created"] and row["username"]]
        keys = ("username", "password", "email", "emailPassword", "cookies")
        accounts = [{key: row[key] for key in keys} for row in created]
        generate = self.directory / "generate"
        atomic_write(generate / "cookies.json", json.dumps(accounts, indent=2))
        atomic_write(generate / "accounts.txt", "".join(
            f"Username: {row['username']}, Password: {row['password']}, Email: {row['email']}, "
            f"Email Password: {row['emailPassword']}\n" for row in created))
        failed_text = "".join(f"{row['username']}:{row['password']}\n" for row in failed)
        atomic_write(generate / "userfail.txt", failed_text)
        timings = io.StringIO(newline="")
        writer = csv.writer(timings)
        writer.writerows((row["username"], row["status"], f"{row['seconds']:.1f}",
                          "; ".join(row["stages"])) for row in rows)
        atomic_write(generate / "timings.csv", timings.getvalue())
        for name, values in (
            ("cookies", cookies),
            ("userpass", [f"{row['username']}:{row['password']}" for row in created]),
            ("userfail", [f"{row['username']}:{row['password']}" for row in failed]),
        ):
            path = self.directory / "format" / f"{name}_{len(values)}.txt"
            atomic_write(path, "".join(value + "\n" for value in values))
            # Counts now cover the entire day, including runs from other processes.
            for previous in path.parent.glob(f"{name}_*.txt"):
                if previous != path and previous.stem[len(name) + 1:].isdigit():
                    previous.unlink(missing_ok=True)
