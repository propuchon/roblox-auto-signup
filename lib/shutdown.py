"""Turn catchable terminal shutdown signals into normal stack unwinding."""

from contextlib import contextmanager
import signal

_defer_depth = 0
_pending_stop = None


@contextmanager
def defer_stop_signals():
    """Finish a short file append and its bookkeeping before honoring a stop."""
    global _defer_depth, _pending_stop
    _defer_depth += 1
    try:
        yield
    finally:
        _defer_depth -= 1
        if _defer_depth == 0 and _pending_stop is not None:
            pending, _pending_stop = _pending_stop, None
            raise pending


class StopRequested(BaseException):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"Stopped by {signal.Signals(signum).name}")


@contextmanager
def stop_signals():
    previous = {}
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        global _pending_stop
        if not stopping:
            stopping = True  # Further signals must not interrupt the export/cleanup.
            request = StopRequested(signum)
            if _defer_depth:
                _pending_stop = request
            else:
                raise request

    try:
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signum = getattr(signal, name)
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
