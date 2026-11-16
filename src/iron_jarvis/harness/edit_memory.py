"""What ONE session has seen of each file, for the edit tools (v1.508.0).

The ledger of v1.500.2 showed the edit failures the exact-edit engine cannot
explain on its own: a team worker sent eleven refused ``edit_file`` calls on
two test files that an earlier worker had already changed. The worker had
never read either file: its ``old`` text came from the lead's brief, which
described the files as they were BEFORE the other worker's edits. Each
refusal said "read the file again", the worker grepped, and guessed again.

This module gives the edit tools the two facts that refusal lacked:

* WHETHER THE TEXT CAN BE CURRENT. ``read_file``, ``write_file``,
  ``edit_file`` and ``multi_edit`` record the file's stamp (modified time and
  size) when they succeed. A refused edit then says, in one sentence, either
  "this session has not read the file" (the text came from somewhere else) or
  "the file changed after this session last read it" (another step, helper or
  program wrote it). Nothing is said when the session's view is current.
* WHEN TO STOP GUESSING. After :data:`EDIT_FAIL_GATE` refused edits of the
  SAME file in a row, the next edit of that file is not run: the answer asks
  for a read first. A successful read or write of the file clears the count,
  so the gate never blocks an edit made from fresh text. The repeated-call
  breakers (``agents/runtime.py``, ``daemon/chat_turn.py``) only see the
  IDENTICAL call; these loops change ``old`` a little every time.

Keyed by (session, absolute path): a team worker is its own session, so one
worker's read never vouches for another's. In memory, bounded
(:data:`MAX_KEYS`, oldest dropped), lost on restart (a restarted session has
read nothing — which is what it is told). Never raises into a tool.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Refused edits of one file in a row before the next is not run.
EDIT_FAIL_GATE = 3
#: Files remembered across all sessions (oldest forgotten first).
MAX_KEYS = 4000

NOT_READ = (
    "This session has not read {name}, so the `old` text came from somewhere else (a brief, an "
    "earlier version or memory). Read the file, then copy `old` from what it holds now."
)
CHANGED = (
    "{name} changed after this session last read it (another step, helper or program wrote "
    "it). Read it again before the next edit."
)
GATED = (
    "NOT RUN: the last {n} edits of {name} were refused, so this one was not tried. Read the "
    "file first (read_file, numbered: true) and copy `old` from what it holds now, or replace "
    "the whole file with write_file."
)


@dataclass
class _Seen:
    stamp: "tuple[int, int] | None" = None
    fails: int = 0


_LOCK = threading.Lock()
_FILES: "OrderedDict[tuple[str, str], _Seen]" = OrderedDict()


def _scope(ctx: Any) -> str:
    return str(getattr(ctx, "session_id", "") or getattr(ctx, "agent_run_id", "") or "")


def _key(ctx: Any, path: Any) -> "tuple[str, str] | None":
    scope = _scope(ctx)
    if not scope or path is None:
        return None
    try:
        return scope, os.path.normcase(str(Path(path)))
    except (TypeError, ValueError):
        return None


def stamp(path: Any) -> "tuple[int, int] | None":
    """``(mtime_ns, size)`` of ``path``, or None. BLOCKING (one stat)."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return int(st.st_mtime_ns), int(st.st_size)


def _entry(key: "tuple[str, str]") -> _Seen:
    got = _FILES.get(key)
    if got is None:
        got = _FILES[key] = _Seen()
        while len(_FILES) > MAX_KEYS:
            _FILES.popitem(last=False)
    else:
        _FILES.move_to_end(key)
    return got


def note_seen(ctx: Any, path: Any) -> None:
    """``path`` was read or written by this session just now: its stamp is
    the session's view, and the failure count starts again. BLOCKING (one
    stat). Never raises."""
    try:
        key = _key(ctx, path)
        if key is None:
            return
        now = stamp(path)
        with _LOCK:
            seen = _entry(key)
            seen.stamp, seen.fails = now, 0
    except Exception:  # noqa: BLE001 — memory must never break a tool
        return


def gate(ctx: Any, path: Any, name: str) -> str:
    """The refusal of an edit after :data:`EDIT_FAIL_GATE` refused edits of
    this file in a row, else "". Never raises."""
    try:
        key = _key(ctx, path)
        if key is None:
            return ""
        with _LOCK:
            seen = _FILES.get(key)
            fails = seen.fails if seen is not None else 0
        if fails >= EDIT_FAIL_GATE:
            return GATED.format(n=fails, name=name)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def note_failed_edit(ctx: Any, path: Any, name: str) -> str:
    """Count one refused edit of ``path`` and return the sentence that says
    whether the session's text can be current ("" when it is). BLOCKING (one
    stat). Never raises."""
    try:
        key = _key(ctx, path)
        if key is None:
            return ""
        now = stamp(path)
        with _LOCK:
            seen = _entry(key)
            seen.fails += 1
            had = seen.stamp
        if had is None:
            return NOT_READ.format(name=name)
        if now is not None and now != had:
            return CHANGED.format(name=name)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def with_note(error: str, note: str) -> str:
    """The refusal with the session's note on its own line."""
    return f"{error}\n{note}" if note else error


def forget_all() -> None:
    """Drop everything (tests)."""
    with _LOCK:
        _FILES.clear()


__all__ = [
    "CHANGED", "EDIT_FAIL_GATE", "GATED", "NOT_READ", "forget_all", "gate", "note_failed_edit",
    "note_seen", "stamp", "with_note",
]
