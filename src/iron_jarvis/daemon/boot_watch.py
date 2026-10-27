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
import sys
import threading
import time
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
