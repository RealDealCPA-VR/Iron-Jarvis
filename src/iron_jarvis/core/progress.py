"""A running tool says it is still MOVING (v1.392.0, the long-jobs plan's
Phase 3 — "never stalls silently").

A coding session's reply is watched for stalls (``harness/stall``): no frame
and no tool output for ten minutes is a stall. A long command that keeps
printing is not one — but its output only reaches the reply when it ends. So
the command runners (``core/procs.run`` / ``run_to_files``, the process
tools) call :func:`touch` as output arrives, and the watch hears it.

ONE ContextVar, in ``core`` (the process runner lives here and must not reach
up into ``harness``): the reply writer arms it on the task that drives a
coding session's turn; every tool task copies it, and ``asyncio.to_thread``
carries it into the worker thread a command runs in. A reader thread a runner
starts itself does not inherit a context — the runner reads :func:`current`
in its own thread and hands the callable over. Outside a coding session's
turn nothing is armed and every runner behaves exactly as before.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Callable, Iterator

_PROGRESS: ContextVar["Callable[[], None] | None"] = ContextVar("ij_tool_progress", default=None)


@contextlib.contextmanager
def scope(fn: "Callable[[], None] | None") -> Iterator[None]:
    """Arm ``fn`` as this context's progress callback."""
    token = _PROGRESS.set(fn)
    try:
        yield
    finally:
        _PROGRESS.reset(token)


def current() -> "Callable[[], None] | None":
    """The armed callback (None outside a watched turn) — read in the thread
    that will hand it to a reader thread of its own."""
    return _PROGRESS.get()


def touch() -> None:
    """Something moved. Cheap, thread-safe, never raises; a no-op when
    nothing listens."""
    fn = _PROGRESS.get()
    if fn is None:
        return
    try:
        fn()
    except Exception:  # noqa: BLE001 — a progress mark never fails a tool
        pass


def safe(fn: "Callable[[], None] | None") -> "Callable[[], None]":
    """``fn`` wrapped so a reader thread can call it freely."""

    def _call() -> None:
        if fn is None:
            return
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass

    return _call


__all__ = ["current", "safe", "scope", "touch"]
