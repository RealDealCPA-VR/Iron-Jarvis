"""A start that does not finish says WHERE it is stuck (v1.400.4).

On 2026-10-27 the first start after each of two updates hung inside the
lifespan with nothing in the log: uvicorn printed "Waiting for application
startup", and 90 s later the desktop's gate gave up and the user pressed
Retry, whose fresh start was ready in 2 s. The steps between are synchronous
and log only when they FAIL, so a step that never returns left no trace at
all, and the cause could not be named afterwards.

:class:`BootWatch` runs beside the lifespan on its own daemon thread. The
lifespan tells it which step is running (:meth:`step`) and when boot is done
(:meth:`done`). At each of :data:`STALL_AFTER_S` seconds still unfinished it
logs ONE warning: the step, how long that step has run, and where the boot
thread (and any other busy thread) is waiting, as ``module:function:line``
frames. Names and numbers only, never a path or a value: the same rule as the
boot summary line (v1.250.0). It never raises into the boot.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("ironjarvis.daemon")

#: When an unfinished start is reported. The desktop's gate waits 90 s, so the
#: first two land before it gives up (and before a person presses Retry).
STALL_AFTER_S: tuple[float, ...] = (30.0, 60.0, 120.0)
#: Frames kept for the boot thread, and for each other busy thread.
BOOT_FRAMES = 14
OTHER_FRAMES = 5
#: Other threads shown at most (a pool of workers would flood the line).
OTHER_THREADS = 6

#: Threads that are always waiting and say nothing about a stuck boot.
_IDLE_FUNCTIONS = frozenset({"wait", "_wait_for_tstate_lock", "select", "_worker", "get", "_recv_bytes"})


def _frames(frame, limit: int) -> list[str]:
    """``module:function:line`` for the innermost ``limit`` frames, outermost
    first. The module NAME (``__name__``), never the file's path."""
    out: list[str] = []
    while frame is not None and len(out) < limit:
        code = frame.f_code
        module = frame.f_globals.get("__name__", "?") if isinstance(frame.f_globals, dict) else "?"
        out.append(f"{module}:{code.co_name}:{frame.f_lineno}")
        frame = frame.f_back
    out.reverse()
    return out


def where(boot_thread: int | None) -> str:
    """Where the boot thread and the other busy threads are, in one string."""
    try:
        frames = sys._current_frames()
    except Exception:  # noqa: BLE001
        return "(the threads could not be read)"
    names = {t.ident: t.name for t in threading.enumerate()}
    parts: list[str] = []
    if boot_thread is not None and boot_thread in frames:
        parts.append("boot thread: " + " > ".join(_frames(frames[boot_thread], BOOT_FRAMES)))
    others = 0
    me = threading.get_ident()
    for ident, frame in frames.items():
        if ident in (boot_thread, me) or others >= OTHER_THREADS:
            continue
        if frame is not None and frame.f_code.co_name in _IDLE_FUNCTIONS:
            continue
        parts.append(f"{names.get(ident, 'thread')}: " + " > ".join(_frames(frame, OTHER_FRAMES)))
        others += 1
    return "; ".join(parts) or "(no thread to show)"


class BootWatch:
    """Reports an unfinished start. ``clock`` and ``sleep_until`` are for
    tests; the daemon uses the monotonic clock and the thread's own wait."""

    def __init__(
        self,
        *,
        stall_after_s: tuple[float, ...] = STALL_AFTER_S,
        clock: Callable[[], float] = time.monotonic,
        report: Callable[[str], None] | None = None,
    ) -> None:
        self.stall_after_s = tuple(sorted(stall_after_s))
        self._clock = clock
        self._report = report or (lambda text: log.warning("%s", text))
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._started = clock()
        self._step = "starting"
        self._step_started = self._started
        self._boot_thread: int | None = None
        self._thread: threading.Thread | None = None
        self.reports: int = 0

    def start(self) -> "BootWatch":
        """Watch from now. Call it ON the boot thread (the lifespan's), so the
        report can show where that thread is."""
        try:
            self._boot_thread = threading.get_ident()
            self._started = self._clock()
            self._step_started = self._started
            self._thread = threading.Thread(target=self._run, name="boot-watch", daemon=True)
            self._thread.start()
        except Exception:  # noqa: BLE001 — the watch never breaks a boot
            log.debug("boot watch could not start", exc_info=True)
        return self

    def step(self, name: str) -> None:
        """The boot is now in ``name``. Never raises."""
        try:
            with self._lock:
                self._step = str(name)
                self._step_started = self._clock()
        except Exception:  # noqa: BLE001
            pass

    def done(self) -> None:
        """Boot finished: no more reports."""
        self._done.set()

    def line(self, waited_s: float) -> str:
        """The words of one report (pure enough to pin)."""
        with self._lock:
            step, since = self._step, self._step_started
        in_step = max(0.0, self._clock() - since)
        return (
            f"startup has not finished after {waited_s:.0f} s: still in step "
            f"{step} (for {in_step:.0f} s). Where it is waiting: {where(self._boot_thread)}"
        )

    def _run(self) -> None:
        for after in self.stall_after_s:
            left = after - (self._clock() - self._started)
            if left > 0 and self._done.wait(left):
                return
            if self._done.is_set():
                return
            try:
                self._report(self.line(after))
                self.reports += 1
            except Exception:  # noqa: BLE001 — a report never breaks a boot
                pass


#: When a frozen event loop is reported (v1.407.1). The desktop watchdog kills
#: the daemon after ~90 s of missed /health, so all three land before it.
FREEZE_AFTER_S: tuple[float, ...] = (20.0, 45.0, 75.0)
#: The C-level stack dump fires when the watch's own thread has not re-armed
#: it for this long (v1.409.1): a freeze that holds the interpreter lock or a
#: log handler's lock silences every Python-level report, this one included.
TRACE_AFTER_S = 30.0
#: How often the watch's thread re-arms that dump while Python still runs.
TRACE_REARM_S = 10.0
#: The file freeze traces go to, beside daemon.log — never inside the state
#: home, which the backup walks (the v1.337.1 rule for files held open).
TRACE_FILE = "daemon-freeze.txt"


def trace_path_for(home: str | os.PathLike[str]) -> str:
    """Where a freeze trace is written: the desktop's logs folder beside the
    state home when it exists (``<userData>/logs``), else the home."""
    home_path = Path(home)
    logs = home_path.parent / "logs"
    return str((logs if logs.is_dir() else home_path) / TRACE_FILE)


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class LoopWatch:
    """A frozen event loop says WHERE it is stuck (v1.407.1).

    On 2026-10-29 the daemon stopped answering everything ~90 s after a boot
    whose Build panes were stuck starting, and the log said nothing: the
    v1.245.0 loop-lag line is written by the loop itself, AFTER a stall ends,
    so a stall that never ends leaves no trace before the watchdog's kill.
    This runs on its own daemon thread. The loop calls :meth:`beat` about
    once a second (the loop-lag task); when the last beat is older than each
    of :data:`FREEZE_AFTER_S`, ONE warning per threshold per freeze names
    where the loop thread and the other busy threads are (``where``: module
    names and line numbers only). A beat ends the freeze. Never raises.

    v1.409.1 (a USER BUG: the same freeze, on the first start after the
    v1.409.0 update): the whole daemon went silent and THIS watch wrote
    nothing either, so the stop held something every Python thread needs
    (the interpreter lock, a log handler's lock) or left no worker free to
    answer. Three more reports, each written straight to the trace file
    before the log is tried:

    * ``trace_path``: :mod:`faulthandler`'s C watchdog thread, which needs
      neither the interpreter lock nor a log lock, dumps every thread's stack
      when this thread has not re-armed it for :data:`TRACE_AFTER_S` (it
      re-arms every :data:`TRACE_REARM_S` while Python runs), and again every
      :data:`TRACE_AFTER_S` while it stays stuck. The process has ONE such
      dump, so only the frozen daemon passes a path — never a test's app.
    * a frozen loop's words go to that file first, then to the log.
    * ``pool_full``: when every worker that answers a plain route is busy
      (the loop alive, /health waiting for a worker) past each threshold,
      the same words and stacks are reported.
    """

    def __init__(
        self,
        *,
        freeze_after_s: tuple[float, ...] = FREEZE_AFTER_S,
        clock: Callable[[], float] = time.monotonic,
        report: Callable[[str], None] | None = None,
        poll_s: float = 1.0,
        trace_path: str | None = None,
        pool_full: Callable[[], bool] | None = None,
        trace_after_s: float = TRACE_AFTER_S,
        trace_rearm_s: float = TRACE_REARM_S,
    ) -> None:
        self.freeze_after_s = tuple(sorted(freeze_after_s))
        self._clock = clock
        self._report = report or (lambda text: log.warning("%s", text))
        self._poll_s = poll_s
        self._last = clock()
        self._said: set[float] = set()
        self._stop = threading.Event()
        self._loop_thread: int | None = None
        self._trace_path = trace_path
        self._trace = None
        self._trace_after_s = trace_after_s
        self._trace_rearm_s = trace_rearm_s
        self._armed_at: float | None = None
        self._pool_full = pool_full
        self._pool_since: float | None = None
        self._pool_said: set[float] = set()
        self.reports = 0

    def start(self) -> "LoopWatch":
        """Watch from now. Call it ON the loop's thread."""
        try:
            self._loop_thread = threading.get_ident()
            self._last = self._clock()
            self._open_trace()
            threading.Thread(target=self._run, name="loop-watch", daemon=True).start()
        except Exception:  # noqa: BLE001
            log.debug("loop watch could not start", exc_info=True)
        return self

    def beat(self) -> None:
        """The loop is alive (call from the loop)."""
        self._last = self._clock()
        self._said.clear()

    def stop(self) -> None:
        self._stop.set()
        if self._trace is None:
            return
        try:
            import faulthandler

            faulthandler.cancel_dump_traceback_later()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._trace.close()
        except Exception:  # noqa: BLE001
            pass
        self._trace = None

    def _open_trace(self) -> None:
        if not self._trace_path:
            return
        try:
            self._trace = open(self._trace_path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            self._trace = None
            return
        self._write(f"--- {_stamp()} watching for freezes (pid {os.getpid()}) ---\n")
        self._arm()

    def _arm(self) -> None:
        """(Re-)arm the C watchdog's dump. Never raises."""
        if self._trace is None:
            return
        try:
            import faulthandler

            faulthandler.dump_traceback_later(self._trace_after_s, repeat=True, file=self._trace)
            self._armed_at = self._clock()
        except Exception:  # noqa: BLE001
            pass

    def _write(self, text: str) -> None:
        if self._trace is None:
            return
        try:
            self._trace.write(text)
            self._trace.flush()
        except Exception:  # noqa: BLE001
            pass

    def _say(self, text: str) -> None:
        """A report goes to the trace file FIRST (a blocked log lock cannot
        stop that), then to the log."""
        self._write(f"{_stamp()} {text}\n")
        self.reports += 1
        self._report(text)

    def check(self) -> None:
        """One look (the thread calls this every ``poll_s``; tests call it)."""
        try:
            now = self._clock()
            if self._armed_at is not None and now - self._armed_at >= self._trace_rearm_s:
                self._arm()
            frozen = now - self._last
            for after in self.freeze_after_s:
                if frozen >= after and after not in self._said:
                    self._said.add(after)
                    self._say(
                        f"event loop frozen for {frozen:.0f} s (nothing has answered since). "
                        f"Where it is waiting: {where(self._loop_thread).replace('boot thread', 'loop thread', 1)}"
                    )
            self._check_pool(now)
        except Exception:  # noqa: BLE001 — the watch never adds a failure
            pass

    def _check_pool(self, now: float) -> None:
        if self._pool_full is None:
            return
        try:
            full = bool(self._pool_full())
        except Exception:  # noqa: BLE001
            full = False
        if not full:
            self._pool_since = None
            self._pool_said.clear()
            return
        if self._pool_since is None:
            self._pool_since = now
        busy = now - self._pool_since
        for after in self.freeze_after_s:
            if busy >= after and after not in self._pool_said:
                self._pool_said.add(after)
                self._say(
                    f"every worker that answers requests has been busy for {busy:.0f} s, "
                    f"so /health and most pages wait. Where they are waiting: {where(None)}"
                )

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            self.check()
