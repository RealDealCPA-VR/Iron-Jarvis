"""List Claude Code / Codex sessions and load one as normalized EVENTs.

READ-ONLY over the user's ``~/.claude`` and ``~/.codex``: files are opened
``rb`` and nothing is ever written, moved or deleted there.

* The listing is FAST: each file's summary parses only its HEAD and TAIL
  (``HEAD_BYTES`` / ``TAIL_BYTES``); ``events`` / ``tools`` come from a byte
  count (newlines / tool-call needles) — no JSON parse of the middle. Those
  two count the session's OWN file; ``subagents`` is the number of subagent
  transcripts under it (Claude Code only) and ``subagent_tools`` their
  tool calls, from the same byte scan, cached per subagent file.
  A summary is cached by ``(path, mtime_ns, size, subagent signature)``, so an
  unchanged session is never re-read and a changed one always is; entries
  for files that no longer exist are evicted on the next listing.
* Loading a session parses every line of its file AND of every subagent
  transcript under it (tagged ``subagent: <file stem>``), merged by
  timestamp. Each file is read up to ``MAX_READ_BYTES`` bytes; a file with
  more makes the loaded summary say ``truncated: true``.
* A session is found by matching the id against the LISTED sessions — the id
  is never joined into a path, so ``../`` goes nowhere.
* Tolerant: a malformed line, an unknown record type, an empty or unreadable
  file, a missing directory — each is skipped, never raised.

Everything here is synchronous file I/O; callers on the event loop wrap it in
``asyncio.to_thread``.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import claude_code, codex
from .common import iso, parse_line

HARNESSES = {claude_code.HARNESS: claude_code, codex.HARNESS: codex}

HEAD_BYTES = 256 * 1024
TAIL_BYTES = 256 * 1024
#: Loading reads at most this many BYTES of any one transcript.
MAX_READ_BYTES = 50 * 1024 * 1024
_SCAN_CHUNK = 4 * 1024 * 1024
_MAX_LIMIT = 5000

_cache: dict[str, tuple[tuple, dict]] = {}
_count_cache: dict[str, tuple[tuple, tuple[int, int]]] = {}
_cache_lock = threading.Lock()
#: harness -> (monotonic time, every summary of that harness) from the last
#: COMPLETE listing; ``find_session`` reuses it while it is this young.
LISTING_TTL_S = 5.0
_listing: dict[str, tuple[float, list[dict]]] = {}


class SessionNotFound(LookupError):
    """No listed session of that harness carries that id."""


def _modules(harness: str | None):
    if harness in (None, "", "all"):
        return list(HARNESSES.values())
    mod = HARNESSES.get(harness)
    if mod is None:
        raise ValueError(
            f"unknown harness {harness!r}; expected one of {sorted(HARNESSES)}"
        )
    return [mod]


def _subagent_files(mod, path: Path) -> list[Path]:
    finder = getattr(mod, "subagent_files", None)
    return finder(path) if finder is not None else []


def _sub_signature(files: list[Path]) -> tuple:
    """(count, total size, newest mtime) of a session's subagent files."""
    total = newest = 0
    for f in files:
        try:
            st = os.stat(f)
        except OSError:
            continue
        total += st.st_size
        newest = max(newest, st.st_mtime_ns)
    return (len(files), total, newest)


def _cache_key(path: Path, st: os.stat_result, sub_sig: tuple = ()) -> tuple:
    """What makes a cached summary stale: the file's mtime OR size moved, or
    any subagent transcript under it was added, grown or touched."""
    return (str(path), st.st_mtime_ns, st.st_size, sub_sig)


def _lines(blob: bytes, start_line: int) -> list[tuple[int, dict]]:
    out = []
    for i, raw in enumerate(blob.split(b"\n")):
        rec = parse_line(raw)
        if rec is not None:
            out.append((start_line + i, rec))
    return out


def _head_tail(path: Path, size: int) -> tuple[list, list]:
    """Parsed records from the first and last window of the file.

    A window's cut-off partial line is dropped; tail line numbers are 0 (only
    the head's are real, and the summary uses neither).
    """
    with open(path, "rb") as fh:
        head = fh.read(HEAD_BYTES)
        if size <= HEAD_BYTES:
            return _lines(head, 1), []
        head = head[: head.rfind(b"\n") + 1] if b"\n" in head else b""
        start = max(HEAD_BYTES, size - TAIL_BYTES)
        fh.seek(start - 1)
        prev = fh.read(1)
        tail = fh.read()
    if prev != b"\n":
        cut = tail.find(b"\n")
        tail = tail[cut + 1 :] if cut >= 0 else b""
    return _lines(head, 1), _lines(tail, 0)


def _count(path: Path, needles: tuple[bytes, ...]) -> tuple[int, int]:
    """(records, tool calls) by a chunked byte scan — no JSON parsing."""
    records = tools = 0
    keep = max(len(n) for n in needles) - 1
    carry = b""
    last = b""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_SCAN_CHUNK)
            if not chunk:
                break
            records += chunk.count(b"\n")
            data = carry + chunk  # carry is shorter than any needle: no double count
            tools += sum(data.count(n) for n in needles)
            carry = data[-keep:] if keep else b""
            last = chunk[-1:]
    if last and last != b"\n":
        records += 1
    return records, tools


def _count_cached(path: Path, needles: tuple[bytes, ...]) -> tuple[int, int]:
    """``_count`` for a subagent file, cached by its own (mtime, size)."""
    try:
        st = os.stat(path)
    except OSError:
        return 0, 0
    key = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _count_cache.get(str(path))
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        counts = _count(path, needles)
    except OSError:
        return 0, 0
    with _cache_lock:
        _count_cache[str(path)] = (key, counts)
    return counts


def summarize(harness: str, path: Path) -> dict | None:
    """One file's session summary (cached); ``None`` if it cannot be read."""
    mod = HARNESSES[harness]
    try:
        st = os.stat(path)
    except OSError:
        return None
    subs = _subagent_files(mod, path)
    key = _cache_key(path, st, _sub_signature(subs))
    with _cache_lock:
        hit = _cache.get(str(path))
    if hit is not None and hit[0] == key:
        return dict(hit[1])
    try:
        head, tail = _head_tail(path, st.st_size)
        records, tools = _count(path, mod.TOOL_NEEDLES)
    except OSError:
        return None
    sub_tools = 0
    sub_big = False
    for sub in subs:
        sub_tools += _count_cached(sub, mod.TOOL_NEEDLES)[1]
        try:
            sub_big = sub_big or os.stat(sub).st_size > MAX_READ_BYTES
        except OSError:
            pass
    fields = mod.summarize(head, tail, path)
    mtime = iso(st.st_mtime)
    summary: dict[str, Any] = {
        "harness": harness,
        "id": fields.get("id") or mod.id_from_path(path),
        "project": fields.get("project") or "",
        "title": fields.get("title") or "",
        "started": fields.get("started") or fields.get("ended") or mtime,
        "ended": fields.get("ended") or mtime,
        "events": records,
        "tools": tools,
        "subagents": len(subs),
        "subagent_tools": sub_tools,
        "file": str(path.absolute()),
        "size": st.st_size,
        "truncated": st.st_size > MAX_READ_BYTES or sub_big,
    }
    with _cache_lock:
        _cache[str(path)] = (key, summary)
    return dict(summary)


def _evict_missing(seen: set[str]) -> None:
    """Drop cached summaries/counts for files that no longer exist."""
    with _cache_lock:
        keys = [k for k in _cache if k not in seen] + list(_count_cache)
    gone = [k for k in keys if not os.path.exists(k)]
    with _cache_lock:
        for k in gone:
            _cache.pop(k, None)
            _count_cache.pop(k, None)


def _all(harness: str | None, limit: int | None = None) -> list[dict]:
    """Summaries, newest first. With ``limit``, only the ``limit`` files with
    the newest mtime are summarised (one ``stat`` each decides which)."""
    mods = _modules(harness)
    candidates: list[tuple[int, str, Path]] = []
    seen: set[str] = set()
    for mod in mods:
        for path in mod.discover():
            seen.add(str(path))
            try:
                mtime = os.stat(path).st_mtime_ns
            except OSError:
                continue
            candidates.append((mtime, mod.HARNESS, path))
    _evict_missing(seen)
    candidates.sort(key=lambda c: c[0], reverse=True)
    complete = limit is None or len(candidates) <= limit
    if not complete:
        candidates = candidates[:limit]
    out: list[dict] = []
    for _, name, path in candidates:
        summary = summarize(name, path)
        if summary is not None:
            out.append(summary)
    out.sort(key=lambda x: (x.get("ended") or "", x.get("started") or ""), reverse=True)
    if complete:
        now = time.monotonic()
        for mod in mods:
            rows = [s for s in out if s["harness"] == mod.HARNESS]
            _listing[mod.HARNESS] = (now, rows)
    return out


def list_sessions(harness: str | None = None, limit: int = 200) -> list[dict]:
    """Session summaries, newest first. ``harness``: None / "claude-code" / "codex".

    Only the ``limit`` most recently written files are summarised.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, _MAX_LIMIT))
    return _all(harness, limit)[:limit]


def find_session(harness: str, session_id: str) -> dict | None:
    """The listed session with exactly this id (never a path join), or None.

    ``harness`` must name ONE harness; ``"all"`` / ``None`` raise ValueError
    (an id is only unique within its harness). A complete listing younger than
    ``LISTING_TTL_S`` is reused (the found file is re-summarised, cheaply, so
    the summary is current); an id it does not hold gets a fresh listing.
    """
    if harness not in HARNESSES:
        raise ValueError(f"harness must be one of {', '.join(sorted(HARNESSES))}")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        return None
    hit = _listing.get(harness)
    if hit is not None and time.monotonic() - hit[0] < LISTING_TTL_S:
        for row in hit[1]:
            if row["id"] == session_id:
                fresh = summarize(harness, Path(row["file"]))
                if fresh is not None and fresh["id"] == session_id:
                    return fresh
                break
    for summary in _all(harness):
        if summary["id"] == session_id:
            return summary
    return None


def session_signature(harness: str, summary: dict) -> tuple:
    """What a session's derived results (its findings) are valid for: its
    file's (mtime, size) and its subagent transcripts' signature, read now."""
    mod = HARNESSES[harness]
    path = Path(summary["file"])
    try:
        st = os.stat(path)
    except OSError:
        return (str(path),)
    return _cache_key(path, st, _sub_signature(_subagent_files(mod, path)))


def _records(path: Path, cut: list) -> Iterator[tuple[int, dict]]:
    """Parsed records of ``path``, reading at most ``MAX_READ_BYTES`` bytes.

    A line the budget would split is not parsed; ``cut`` gets ``True``
    appended when any byte of the file was left unread.
    """
    cap = MAX_READ_BYTES
    read = 0
    line_no = 0
    with open(path, "rb") as fh:
        while True:
            budget = cap - read
            if budget <= 0:
                if fh.read(1):
                    cut.append(True)
                return
            raw = fh.readline(budget)
            if not raw:
                return
            read += len(raw)
            line_no += 1
            if not raw.endswith(b"\n") and read >= cap and fh.read(1):
                cut.append(True)  # this line runs past the budget
                return
            rec = parse_line(raw)
            if rec is not None:
                yield line_no, rec


def load_session(harness: str, session_id: str) -> tuple[dict, list[dict]]:
    """``(summary, events)`` for one listed session; raises SessionNotFound.

    Events of the session's own file and of each subagent transcript under it
    (``subagent: <file stem>``) are merged by timestamp; an event without one
    takes its predecessor's in the same file, and ties keep the parent first,
    then the subagent files in name order.
    """
    summary = find_session(harness, session_id)
    if summary is None:
        raise SessionNotFound(f"no {harness} session {session_id!r}")
    mod = HARNESSES[harness]
    path = Path(summary["file"])
    streams: list[tuple[str | None, Path]] = [(None, path)]
    streams += [(p.stem, p) for p in _subagent_files(mod, path)]
    keyed: list[tuple[str, int, int, dict]] = []
    truncated = False
    for order, (tag, file) in enumerate(streams):
        cut: list = []
        try:
            events = mod.map_events(_records(file, cut), file, summary["id"])
        except OSError:
            events = []
        truncated = truncated or bool(cut)
        last_ts = ""
        for idx, ev in enumerate(events):
            if tag is not None:
                ev["subagent"] = tag
            last_ts = ev.get("ts") or last_ts
            keyed.append((last_ts, order, idx, ev))
    keyed.sort(key=lambda k: (k[0], k[1], k[2]))
    summary = dict(summary, truncated=truncated)
    return summary, [k[3] for k in keyed]


def session_events(harness: str, session_id: str) -> list[dict]:
    """The full normalized EVENT list of one listed session (subagents included)."""
    return load_session(harness, session_id)[1]
