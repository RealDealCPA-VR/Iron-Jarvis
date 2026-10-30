"""THE CONTRACT between the daemon and the dashboard's Code view (v1.409.0),
and the bounded ``events.jsonl`` each session keeps.

Every event is ``{type, at, ...}``; a turn's events also carry ``turn`` (the
turn id). The types (``docs/BUILD-APP-VIEW-PLAN.md`` "Normalized events"):

* ``session`` {harness, harness_session_id, model, cwd, mode}
* ``user`` {text, files?}
* ``text`` {item, delta} · ``text_done`` {item, text}
* ``thinking`` {item, delta}
* ``tool`` {item, kind, name, title, input, status, output?, path?, line?, diff?}
* ``plan`` {steps: [{text, status}]}
* ``approval`` {request_id, kind, title, detail, command?, path?, diff?, can_session}
* ``question`` {request_id, questions: [{question, header?, options, multi}]}
* ``resolved`` {request_id, decision}
* ``usage`` {input, output, cache_read, cost_usd?, list_price}
* ``notice`` {text}
* ``error`` {title, fix, technical, fix_kind?, account?}
* ``done`` {stop: completed|interrupted|failed|max_turns, result?}

Every free text field is MASKED (``detections.redact.mask``) BEFORE it is cut
(:func:`clip`): a cut that ran first could leave half a key the mask no longer
recognises.

THE LOG: ``<home>/hosted/<id>/events.jsonl``, append-only, written by ONE
writer thread in batches (:class:`EventLog`). Consecutive ``text`` /
``thinking`` deltas of the same item are merged into one line (the contract's
shape is unchanged — still a delta), so a long answer is a handful of lines,
not thousands. Bounded: past :data:`MAX_LINES` lines or :data:`MAX_BYTES`
bytes the oldest lines are dropped and ``meta.json`` counts them, so a reader
can say that older events were dropped.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..core.ids import utcnow
from ..detections.redact import mask

log = logging.getLogger(__name__)

#: The event types (the contract).
TYPES: tuple[str, ...] = (
    "session", "user", "text", "text_done", "thinking", "tool", "plan", "approval",
    "question", "resolved", "usage", "notice", "error", "done",
)
TOOL_KINDS: tuple[str, ...] = (
    "read", "edit", "write", "command", "search", "web", "mcp", "task", "todo", "other",
)
APPROVAL_KINDS: tuple[str, ...] = ("command", "edit", "tool", "network", "other")
STOPS: tuple[str, ...] = ("completed", "interrupted", "failed", "max_turns")

#: Cuts (characters), each applied AFTER masking.
INPUT_CHARS = 300
OUTPUT_CHARS = 1500
DIFF_CHARS = 20_000
TITLE_CHARS = 120
DETAIL_CHARS = 1500
TEXT_CHARS = 200_000

#: The log's bounds.
MAX_LINES = 20_000
MAX_BYTES = 20 * 1024 * 1024
#: How often the writer thread writes what it holds (seconds).
FLUSH_EVERY_S = 0.25


def clip(text: Any, cap: int) -> str:
    """``text`` masked, THEN cut to ``cap`` characters (an ellipsis marks a cut)."""
    value = mask(str(text if text is not None else ""))
    if len(value) <= cap:
        return value
    return value[: max(0, cap - 1)] + "…"


def one_line(text: Any, cap: int = TITLE_CHARS) -> str:
    """The first non-empty line, masked and cut."""
    for raw in str(text or "").splitlines():
        if raw.strip():
            return clip(" ".join(raw.split()), cap)
    return ""


def now_iso() -> str:
    return utcnow().isoformat().replace("+00:00", "Z")


def make(event_type: str, /, **fields: Any) -> dict[str, Any]:
    """One event: ``type`` first, ``at`` second, then the fields."""
    out: dict[str, Any] = {"type": event_type, "at": now_iso()}
    out.update(fields)
    return out


def unified_from_hunks(path: str, hunks: list[dict[str, Any]]) -> str:
    """A unified diff from Claude Code's ``structuredPatch`` hunks."""
    name = str(path or "file").replace("\\", "/")
    lines = [f"--- a/{name}", f"+++ b/{name}"]
    for h in hunks or []:
        if not isinstance(h, dict):
            continue
        lines.append(
            f"@@ -{h.get('oldStart', 0)},{h.get('oldLines', 0)} "
            f"+{h.get('newStart', 0)},{h.get('newLines', 0)} @@"
        )
        for row in h.get("lines") or []:
            lines.append(str(row))
    return "\n".join(lines)


def unified_new_file(path: str, content: str) -> str:
    name = str(path or "file").replace("\\", "/")
    body = str(content or "").splitlines()
    head = [f"--- /dev/null", f"+++ b/{name}", f"@@ -0,0 +1,{len(body)} @@"]
    return "\n".join(head + ["+" + r for r in body])


def unified_replace(path: str, old: str, new: str) -> str:
    """A small unified diff of one replacement (an Edit asked before it ran)."""
    import difflib

    name = str(path or "file").replace("\\", "/")
    rows = difflib.unified_diff(
        str(old or "").splitlines(), str(new or "").splitlines(),
        fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="",
    )
    return "\n".join(rows)


# --------------------------------------------------------------------------- #
# The bounded log
# --------------------------------------------------------------------------- #
def session_dir(home: Any, sid: str) -> Path:
    return Path(home) / "hosted" / str(sid)


def _mergeable(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a.get("type") == b.get("type")
        and a.get("type") in ("text", "thinking")
        and a.get("item") == b.get("item")
        and a.get("turn") == b.get("turn")
    )


def coalesce(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consecutive deltas of the same item merged (the first one's ``at``)."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if out and _mergeable(out[-1], ev):
            out[-1] = {**out[-1], "delta": str(out[-1].get("delta") or "") + str(ev.get("delta") or "")}
        else:
            out.append(dict(ev))
    return out


class EventLog:
    """One session's ``events.jsonl``. :meth:`append` never blocks (it only
    queues); a writer thread writes in batches. :meth:`flush` (BLOCKING)
    writes what is queued now — for a reader that must see everything."""

    def __init__(self, home: Any, sid: str) -> None:
        self.dir = session_dir(home, sid)
        self.path = self.dir / "events.jsonl"
        self.meta_path = self.dir / "meta.json"
        self._pending: list[dict[str, Any]] = []
        self._lock = threading.Lock()  # guards _pending
        self._io = threading.Lock()  # one writer at a time
        self._wake = threading.Event()
        self._stop = False
        self._lines: int | None = None
        self._thread: threading.Thread | None = None

    # -- writing ---------------------------------------------------------------
    def append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._pending.append(event)
            if self._thread is None and not self._stop:
                self._thread = threading.Thread(
                    target=self._run, name=f"hosted-log-{self.dir.name}", daemon=True
                )
                self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=5.0)
            self._wake.clear()
            if self._stop and not self._pending:
                return
            self.flush()
            if self._stop:
                self.flush()
                return
            # Batch: let deltas arriving together share a write.
            time.sleep(FLUSH_EVERY_S)

    def flush(self) -> None:
        """BLOCKING: write everything queued so far."""
        with self._io:
            with self._lock:
                batch, self._pending = self._pending, []
            if not batch:
                return
            rows = coalesce(batch)
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
                with open(self.path, "ab") as fh:
                    fh.write(data)
                if self._lines is None:
                    self._lines = self._count_lines()
                else:
                    self._lines += len(rows)
                self._bound()
            except OSError:
                log.warning("could not write a Code-view session's events", exc_info=True)

    def close(self) -> None:
        """Write what is queued and stop the writer (BLOCKING, bounded)."""
        self._stop = True
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout=5)
        self.flush()

    def _count_lines(self) -> int:
        try:
            with open(self.path, "rb") as fh:
                return sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 20), b""))
        except OSError:
            return 0

    def _bound(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if (self._lines or 0) <= MAX_LINES and size <= MAX_BYTES:
            return
        with open(self.path, "rb") as fh:
            rows = fh.read().splitlines(keepends=True)
        keep = rows[-MAX_LINES:]
        total = sum(len(r) for r in keep)
        while keep and total > MAX_BYTES:
            total -= len(keep[0])
            keep = keep[1:]
        dropped = len(rows) - len(keep)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(b"".join(keep))
        os.replace(tmp, self.path)
        self._lines = len(keep)
        meta = self.meta()
        meta["dropped"] = int(meta.get("dropped") or 0) + dropped
        self.meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # -- reading (BLOCKING) ----------------------------------------------------
    def meta(self) -> dict[str, Any]:
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def read(self, limit: int = 2000, *, turn: str = "") -> tuple[list[dict[str, Any]], int, int]:
        """``(newest events, events in the log, older events dropped)``. With
        ``turn``: only that turn's events (all of them)."""
        self.flush()
        out: list[dict[str, Any]] = []
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read().splitlines()
        except OSError:
            raw = []
        for line in raw:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and (not turn or ev.get("turn") == turn):
                out.append(ev)
        total = len(out)
        if not turn and limit and len(out) > limit:
            out = out[-limit:]
        return out, total, int(self.meta().get("dropped") or 0)
