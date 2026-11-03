"""Live terminal display; percentages count completed work, never guessed wait time."""

import logging

from rich.progress import BarColumn, Progress, ProgressColumn, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text


def human_bytes(value):
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1000


class StatusColumn(SpinnerColumn):
    def render(self, task):
        status = task.fields.get("status", "running")
        if status != "running":
            return Text("✓" if status in {"success", "done"} else "!",
                        style="green" if status in {"success", "done"} else "yellow")
        return super().render(task)


class DetailsColumn(ProgressColumn):
    def __init__(self, display):
        super().__init__()
        self.display = display

    def render(self, task):
        display = self.display
        if task.fields["kind"] == "overall":
            total = "∞" if task.total is None else int(task.total)
            return Text(f"{int(task.completed)}/{total} processed | "
                        f"success={display.successes} failed={display.failures} interrupted={display.interrupted}")
        if task.fields["kind"] == "cooldown":
            return Text(task.fields["label"])
        metrics = display.metrics()
        phase = display.watch.phase if display.watch else display.phase
        return Text(f"{phase} | RX {human_bytes(metrics.get('response_encoded_bytes', 0))} | "
                    f"{metrics.get('request_count', 0)} requests")


class PercentageColumn(ProgressColumn):
    def render(self, task):
        return Text("∞" if task.total is None else f"{task.percentage:>3.0f}%")


class TerminalProgress:
    def __init__(self, console):
        self.started = False
        self.active = False
        self.watch = None
        self.phase = "Preparing"
        self.successes = self.failures = self.interrupted = 0
        self.byte_counter = None
        self.final_metrics = {}
        self.meter_ready = False
        self.progress = Progress(
            StatusColumn(), TextColumn("{task.description}", markup=False),
            BarColumn(bar_width=24), PercentageColumn(),
            TimeElapsedColumn(), DetailsColumn(self), console=console,
            refresh_per_second=4, redirect_stdout=False, redirect_stderr=False,
        )

    def start(self, total, byte_counter):
        self.byte_counter = byte_counter
        self.overall = self.progress.add_task("Accounts", total=total or None, kind="overall", status="running")
        self.account = self.progress.add_task("Steps", total=5, kind="account", status="running", visible=False)
        self.cooldown_task = self.progress.add_task("Pause", total=1, kind="cooldown", label="",
                                                    status="running", visible=False)
        self.started = True
        self.progress.start()

    def begin(self, index, total):
        self.active, self.meter_ready = True, False
        self.final_metrics = {}
        self.index = index
        self.phase = "Preparing"
        self.progress.reset(self.account, description=f"Steps [{index}/{total or '∞'}]", total=5,
                            kind="account", status="running", visible=True)

    def stage(self, label):
        self.phase = label
        self.progress.refresh()

    def advance(self):
        self.meter_ready = True
        self.progress.advance(self.account)

    def metrics(self):
        if self.active and self.meter_ready:
            return self.byte_counter()
        return self.final_metrics

    def finish(self, status, metrics):
        self.active = False
        self.watch = None
        self.final_metrics = metrics
        self.phase = status.upper()
        if status == "success":
            self.successes += 1
        elif status == "failed":
            self.failures += 1
        else:
            self.interrupted += 1
        # Export is the fifth milestone. Failed/skipped stages are not completed.
        self.progress.update(self.account, advance=1, status=status)
        self.progress.stop_task(self.account)
        self.progress.update(self.overall, completed=self.index)

    def cooldown(self, seconds, remaining, label):
        task = self.progress.tasks[self.cooldown_task]
        if not task.visible:
            self.progress.reset(self.cooldown_task, total=seconds, visible=True,
                                kind="cooldown", status="running", label=label)
        self.progress.update(self.cooldown_task, completed=max(0, seconds - remaining), label=label)

    def end_cooldown(self):
        self.progress.stop_task(self.cooldown_task)
        self.progress.update(self.cooldown_task, visible=False)

    def close(self):
        if not self.started:
            return
        if self.active:
            self.active = False
            self.phase = "STOPPED"
            self.progress.update(self.account, status="stopped")
        self.watch = None
        overall = self.progress.tasks[self.overall]
        self.progress.update(self.overall, status="done" if overall.completed == overall.total else "stopped")
        self.progress.stop_task(self.account)
        self.progress.stop_task(self.overall)
        self.end_cooldown()
        self.progress.stop()
        self.started = False


class HideLiveWaits(logging.Filter):
    def __init__(self, display):
        super().__init__()
        self.display = display

    def filter(self, record):
        # Keep every heartbeat in process.log; the live row already shows the wait.
        return not (self.display.started and record.getMessage().startswith("WAIT "))
