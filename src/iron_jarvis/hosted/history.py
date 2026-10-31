"""The Code view's HISTORY that stays (v1.418.0, C4.6 + C4.11).

Three pieces, each read-only on everything it reads:

* :class:`TailLog` — a session's ``events.jsonl`` WITH an in-memory tail (the
  ``EventLog`` writer is unchanged; it is subclassed, never edited). Every
  event appended through it gets a ``seq`` (monotonic, stamped on the copy the
  log keeps — the caller's dict, which the live stream sends, is left as it
  was) and is kept in memory, coalesced the log's own way and bounded. A view
  of a session this daemon has driven is served from memory; the file is read
  at most ONCE, to learn what was there before this daemon started (only the
  lines before the one carrying the first ``seq`` this process wrote — so an
  event can never be counted twice, whatever the writer thread flushed
  meanwhile, and a clock that moved back hides nothing). C4.11: ``EventLog.read`` used to read and parse the whole log
  (up to 20 MB) on every GET.

* :func:`read_page` — the events BEFORE a cursor (``GET …/events?before=``),
  for "Load earlier". A cursor names the oldest event the page already holds:
  ``s<seq>`` (an event written by v1.418.0 or later) or ``i<index>:<dropped>``
  (an older event, by its place in the file, with the count of lines the log
  had trimmed when that place was read — a later trim moves the place back).
  The seq form survives the in-memory merge of text deltas (a merged entry
  keeps its FIRST event's seq, exactly as the file's merged line does).

* :func:`transcript_events` — the coding tool's OWN saved conversation (Claude
  Code's session jsonl, Codex's rollout, Pi's session file, OpenCode's
  database opened ``mode=ro``) as the view's normalized events (``user``,
  ``text_done``, ``tool``), masked, bounded to the newest
  :data:`TRANSCRIPT_EVENTS`. A conversation opened from disk shows what was
  said before; after "Open in terminal" and back, what the user did in the
  terminal is MERGED by time (:func:`merge_transcript`): the transcript's
  entries that fall inside one of the log's own turns are that turn's, never
  drawn twice. Cached by the file's (size, mtime), so a repeated view stats
  the file and reads nothing.

Nothing here ever writes to a coding tool's files.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import events as _E
from .events import (
    INPUT_CHARS,
    OUTPUT_CHARS,
    EventLog,
    clip,
    coalesce_into,
)

log = logging.getLogger(__name__)

#: v1.418.0 (C4.4, the events module's): the key a driver puts a step's kept
#: texts under (the log's alone), and the key the log writes them as.
FULL_KEY = getattr(_E, "FULL_KEY", "_full")
LOG_FULL_KEY = getattr(_E, "LOG_FULL_KEY", "full")
#: Never part of what a view sends.
_PRIVATE = ("seq", LOG_FULL_KEY, FULL_KEY)

#: How many events the in-memory tail keeps (the view's default read).
TAIL_EVENTS = 2000
#: How many events one "Load earlier" page holds at most.
PAGE_MAX = 2000
#: How many events a coding tool's own transcript gives the view (newest).
TRANSCRIPT_EVENTS = 2000
#: A transcript's text, cut (after masking): an answer is rarely longer.
TRANSCRIPT_TEXT_CHARS = 50_000
#: Transcripts kept parsed in memory (by path, size and mtime).
TRANSCRIPT_CACHE = 16


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _parse_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str:
    """Any timestamp the tools write (ISO text, epoch s or ms) as the log's own
    ``…Z`` form; "" when it cannot be read."""
    dt: datetime | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = float(value)
        if n > 1e12:  # milliseconds
            n /= 1000.0
        try:
            dt = datetime.fromtimestamp(n, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            dt = None
    else:
        dt = _parse_at(value)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_events(path: Path) -> list[dict[str, Any]]:
    """BLOCKING: every event of a log file, in order (unreadable lines left out)."""
    out: list[dict[str, Any]] = []
    try:
        with open(path, "rb") as fh:
            raw = fh.read().splitlines()
    except OSError:
        return out
    for line in raw:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _meta_dropped(meta_path: Path) -> int:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return int(data.get("dropped") or 0) if isinstance(data, dict) else 0
    except (OSError, ValueError, TypeError):
        return 0


def public(ev: dict[str, Any]) -> dict[str, Any]:
    """An event as the view sends it: the log's own ``seq`` is the daemon's
    (the cursor carries it) and a step's kept texts are the log's (read back
    with ``GET …/events/{item}``) — never part of the contract."""
    if not any(k in ev for k in _PRIVATE):
        return ev
    return {k: v for k, v in ev.items() if k not in _PRIVATE}


def _seq_of(ev: dict[str, Any]) -> int | None:
    v = ev.get("seq")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def cursor_for(ev: dict[str, Any] | None, index: int, dropped: int) -> str | None:
    """The cursor naming ``ev`` (the oldest event a read holds) — its seq when
    it has one, else its place in the file."""
    if ev is None:
        return None
    seq = _seq_of(ev)
    if seq is not None:
        return f"s{seq}"
    return f"i{max(0, int(index))}:{max(0, int(dropped))}"


# --------------------------------------------------------------------------- #
# The in-memory tail (C4.11)
# --------------------------------------------------------------------------- #
_SEQ_LOCK = threading.Lock()
_LAST_SEQ = 0


def next_seq() -> int:
    """A process-wide, strictly increasing number that is also larger than
    any a previous run of Iron Jarvis wrote (microseconds since 1970)."""
    global _LAST_SEQ
    with _SEQ_LOCK:
        now = time.time_ns() // 1000
        _LAST_SEQ = now if now > _LAST_SEQ else _LAST_SEQ + 1
        return _LAST_SEQ


class TailLog(EventLog):
    """A session's log with its newest events kept in memory (see the module
    docstring). :meth:`served` answers a view without touching the file once
    :meth:`seed` has run (the one read)."""

    def __init__(self, home: Any, sid: str, *, keep: int = TAIL_EVENTS) -> None:
        super().__init__(home, sid)
        self.keep = max(1, int(keep))
        self._tail_lock = threading.Lock()
        #: Events this process appended (coalesced, the newest ``keep``).
        self.mine: list[dict[str, Any]] = []
        #: How many coalesced entries this process appended in all.
        self.mine_total = 0
        #: The seq of the first event this process appended (None = none yet).
        self.first_seq: int | None = None
        #: What the file held before this process wrote to it (filled by
        #: :meth:`seed`): its newest ``keep`` events with their file index.
        self.prior: list[tuple[int, dict[str, Any]]] = []
        self.prior_total = 0
        self.dropped = 0
        self.seeded = False

    def append(self, event: dict[str, Any]) -> None:  # noqa: D401 — EventLog's own
        # The kept texts come off the CALLER's dict in place (the events
        # module's rule: the same dict goes to the turn's watchers next) and
        # ride the log's copy only.
        full = event.pop(FULL_KEY, None)
        stamped = dict(event)
        if full:
            stamped[FULL_KEY] = full
        seq = next_seq()
        stamped["seq"] = seq
        with self._tail_lock:
            if self.first_seq is None:
                self.first_seq = seq
            before = len(self.mine)
            coalesce_into(self.mine, stamped)
            if len(self.mine) > before:
                self.mine_total += 1
            if len(self.mine) > self.keep:
                del self.mine[: len(self.mine) - self.keep]
        super().append(stamped)

    def seed(self) -> None:
        """BLOCKING, the one file read: what the log held before this process
        wrote to it. Safe against the writer thread: only the lines before
        the one carrying this process's first ``seq`` count."""
        if self.seeded:
            return
        events = _read_events(self.path)
        dropped = _meta_dropped(self.meta_path)
        with self._tail_lock:
            if self.seeded:
                return
            first = self.first_seq
            prior: list[tuple[int, dict[str, Any]]] = []
            for i, ev in enumerate(events):
                seq = _seq_of(ev)
                # This process's own lines start at the one carrying its
                # FIRST seq exactly (a batch's first event opens its first
                # line; a merged line keeps its first event's seq). Review
                # (v1.418.0): never "every seq at or above it" — a seq is the
                # wall clock, and after the clock moved back an earlier run's
                # newer lines would vanish from the view.
                if first is not None and seq is not None and seq == first:
                    break  # this process's own (already in memory)
                prior.append((i, {k: v for k, v in ev.items() if k != LOG_FULL_KEY} if LOG_FULL_KEY in ev else ev))
            self.prior_total = len(prior)
            self.prior = prior[-self.keep:]
            self.dropped = dropped
            self.seeded = True

    def served(self, limit: int) -> dict[str, Any] | None:
        """The newest ``limit`` events from memory, with ``events_total``,
        ``dropped`` and the ``older`` cursor — None when memory cannot answer
        (not seeded yet, or the read wants more than memory holds)."""
        with self._tail_lock:
            if not self.seeded:
                return None
            limit = max(1, int(limit))
            mine = list(self.mine)
            overflow = self.mine_total > len(self.mine)
            prior = list(self.prior)
            prior_cut = self.prior_total > len(self.prior)
            total = self.prior_total + self.mine_total
            dropped = self.dropped
        picked: list[tuple[int | None, dict[str, Any]]]
        if len(mine) >= limit:
            picked = [(None, e) for e in mine[-limit:]]
        else:
            if overflow:
                return None  # older in-process events live only in the file
            want = limit - len(mine)
            if want > len(prior) and prior_cut:
                return None
            picked = list(prior[-want:]) + [(None, e) for e in mine]
        events = [e for _, e in picked]
        oldest_index = total - len(events)
        older = None
        if picked and oldest_index > 0:
            idx, first_ev = picked[0]
            older = cursor_for(first_ev, idx if idx is not None else oldest_index, dropped)
        return {
            "events": [public(e) for e in events],
            "events_total": total,
            "dropped": dropped,
            "older": older,
            "from_memory": True,
        }


def file_view(path: Path, meta_path: Path, limit: int) -> dict[str, Any]:
    """BLOCKING: the newest ``limit`` events of a log file (the view of a
    session nothing in memory holds), with the same keys as
    :meth:`TailLog.served`."""
    events = _read_events(path)
    dropped = _meta_dropped(meta_path)
    total = len(events)
    limit = max(1, int(limit))
    start = max(0, total - limit)
    picked = events[start:]
    older = cursor_for(picked[0], start, dropped) if picked and start > 0 else None
    return {
        "events": [public(e) for e in picked],
        "events_total": total,
        "dropped": dropped,
        "older": older,
        "from_memory": False,
    }


class BadCursor(ValueError):
    """A ``before`` cursor this daemon did not write."""


def _cut_of(events: list[dict[str, Any]], cursor: str, dropped_now: int) -> int:
    """Where ``cursor`` cuts the file's events: the events before it are older."""
    cursor = str(cursor or "").strip()
    if cursor.startswith("s"):
        try:
            seq = int(cursor[1:])
        except ValueError:
            raise BadCursor(cursor) from None
        # Review (v1.418.0): the event the cursor names, exactly — a seq is
        # the wall clock, and a restart after the clock moved back writes
        # smaller seqs after larger ones. Only a seq no line carries (it was
        # trimmed) falls back to the first larger one.
        first_larger: int | None = None
        for i, ev in enumerate(events):
            s = _seq_of(ev)
            if s is None:
                continue
            if s == seq:
                return i
            if first_larger is None and s > seq:
                first_larger = i
        return len(events) if first_larger is None else first_larger
    if cursor.startswith("i"):
        body = cursor[1:]
        index_s, _, dropped_s = body.partition(":")
        try:
            index = int(index_s)
            then = int(dropped_s or 0)
        except ValueError:
            raise BadCursor(cursor) from None
        # A trim since the cursor was read moved every line back.
        return max(0, min(len(events), index - max(0, dropped_now - then)))
    raise BadCursor(cursor)


def read_page(path: Path, meta_path: Path, before: str, limit: int) -> dict[str, Any]:
    """BLOCKING: up to ``limit`` events older than ``before`` (oldest first),
    with ``older`` (the cursor for the page before this one, or None) and
    ``earlier`` (how many events are older still)."""
    events = _read_events(path)
    dropped = _meta_dropped(meta_path)
    cut = _cut_of(events, before, dropped)
    limit = max(1, min(int(limit or PAGE_MAX), PAGE_MAX))
    start = max(0, cut - limit)
    page = events[start:cut]
    older = cursor_for(page[0], start, dropped) if page and start > 0 else None
    return {
        "events": [public(e) for e in page],
        "older": older,
        "earlier": start,
        "events_total": len(events),
        "dropped": dropped,
    }


def item_text(path: Path, item: str) -> dict[str, Any] | None:
    """BLOCKING: the newest whole text the log keeps for one item (a tool's
    output, an answer's text), or None — the fallback while
    ``EventLog.item_text`` is not there."""
    item = str(item or "")
    if not item:
        return None
    found: dict[str, Any] | None = None
    deltas: list[str] = []
    for ev in _read_events(path):
        if ev.get("item") != item:
            continue
        kind = ev.get("type")
        if kind == "tool":
            found = {"item": item, "kind": "tool", "input": str(ev.get("input") or ""),
                     "output": str(ev.get("output") or "")}
        elif kind == "text_done":
            found = {"item": item, "kind": "text", "text": str(ev.get("text") or "")}
        elif kind in ("text", "thinking"):
            deltas.append(str(ev.get("delta") or ""))
    if found is None and deltas:
        found = {"item": item, "kind": "text", "text": "".join(deltas)}
    return found


# --------------------------------------------------------------------------- #
# The coding tool's own transcript (C4.6)
# --------------------------------------------------------------------------- #
def _tool_event(item: str, at: str, *, kind: str, name: str, title: str, inp: Any,
                status: str = "done", output: str = "", path: str = "", diff: str = "") -> dict[str, Any]:
    try:
        line = inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        line = ""
    ev: dict[str, Any] = {
        "type": "tool", "at": at, "item": item, "kind": kind, "name": clip(name, 200),
        "title": clip(title, 120), "input": clip(line, INPUT_CHARS), "status": status,
        "imported": True,
    }
    if output:
        ev["output"] = clip(output, OUTPUT_CHARS)
    if path:
        ev["path"] = clip(path, 1000)
    if diff:
        from .events import DIFF_CHARS

        ev["diff"] = clip(diff, DIFF_CHARS)
    return ev


def _user(at: str, text: str) -> dict[str, Any]:
    return {"type": "user", "at": at, "text": clip(text, TRANSCRIPT_TEXT_CHARS), "imported": True}


def _answer(item: str, at: str, text: str) -> dict[str, Any]:
    return {"type": "text_done", "at": at, "item": item, "text": clip(text, TRANSCRIPT_TEXT_CHARS),
            "imported": True}


def _settle(ev: dict[str, Any] | None, output: str, failed: bool) -> None:
    if ev is None:
        return
    if output:
        ev["output"] = clip(output, OUTPUT_CHARS)
    ev["status"] = "failed" if failed else "done"


def _claude_events(path: Path) -> list[dict[str, Any]]:
    """Claude Code's session jsonl (the ``history`` reader's bounded records)."""
    from ..history.claude_code import _prompt_text
    from ..history.common import result_text
    from ..history.index import _records
    from . import claude as C

    out: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    n = 0
    for _line, rec in _records(path, []):
        kind = rec.get("type")
        if kind not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        at = _iso(rec.get("timestamp"))
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        content = msg.get("content")
        if kind == "user":
            prompt = _prompt_text(rec)
            if prompt:
                out.append(_user(at, prompt))
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    _settle(pending.pop(str(block.get("tool_use_id") or ""), None),
                            result_text(block.get("content")), bool(block.get("is_error")))
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and str(block.get("text") or "").strip():
                n += 1
                out.append(_answer(f"imp-{n}", at, str(block.get("text"))))
            elif btype == "tool_use" and block.get("name"):
                n += 1
                name = str(block["name"])
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                kind_ = C.tool_kind(name)
                path_ = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
                diff = C._edit_diff(name, inp) if kind_ in ("edit", "write") else ""
                ev = _tool_event(f"imp-{n}", at, kind=kind_, name=name, title=C.tool_title(name, inp),
                                 inp=inp, path=path_, diff=diff)
                out.append(ev)
                if block.get("id"):
                    pending[str(block["id"])] = ev
    return out


_CODEX_KIND = {
    "command.executed": "command", "file.read": "read", "file.write": "edit",
    "network.request": "web", "tool.called": "other",
}


def _codex_events(path: Path, hid: str) -> list[dict[str, Any]]:
    """Codex's rollout, through the ``history`` reader's normalized events."""
    from ..history import codex as HC
    from ..history.index import _records
    from .events import one_line

    out: list[dict[str, Any]] = []
    n = 0
    for ev in HC.map_events(_records(path, []), path, hid):
        action = ev.get("action")
        at = _iso(ev.get("ts"))
        if action == "prompt.submitted":
            out.append(_user(at, str(ev.get("text") or "")))
            continue
        if action == "response.completed":
            n += 1
            out.append(_answer(f"imp-{n}", at, str(ev.get("text") or "")))
            continue
        kind = _CODEX_KIND.get(str(action), "other")
        n += 1
        command = str(ev.get("command") or "")
        path_ = str(ev.get("path") or "")
        base = os.path.basename(path_.replace("\\", "/")) or path_
        tool = str(ev.get("tool") or "")
        if kind == "command":
            title = f"Run {one_line(command, 90)}" if command else "Run a command"
        elif kind == "read":
            title = f"Read {base}" if base else "Read a file"
        elif kind == "edit":
            title = f"Edit {base}" if base else "Edit files"
        elif kind == "web":
            title = f"Fetch {ev.get('url')}" if ev.get("url") else "Search the web"
        else:
            title = f"Use {tool}" if tool else "Use a tool"
        out.append(_tool_event(
            f"imp-{n}", at, kind=kind, name=tool, title=title, inp=command or path_,
            status="failed" if ev.get("ok") is False else "done",
            output=str(ev.get("text") or ""), path=path_,
        ))
    return out


def _pi_events(path: Path) -> list[dict[str, Any]]:
    """Pi's session file: ``message`` rows (user, assistant with text and
    ``toolCall`` blocks, ``toolResult``)."""
    from ..history.common import parse_line
    from . import pi as P

    out: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    n = 0
    try:
        fh = open(path, "rb")
    except OSError:
        return out
    with fh:
        for raw in fh:
            row = parse_line(raw)
            if not row or row.get("type") != "message":
                continue
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            at = _iso(msg.get("timestamp") or row.get("timestamp"))
            role = msg.get("role")
            content = msg.get("content")
            if role == "user":
                text = P._user_text(msg).strip()
                if text:
                    out.append(_user(at, text))
            elif role == "assistant":
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and str(block.get("text") or "").strip():
                        n += 1
                        out.append(_answer(f"imp-{n}", at, str(block["text"])))
                    elif block.get("type") == "toolCall" and block.get("name"):
                        n += 1
                        name = str(block["name"])
                        args = block.get("arguments") if isinstance(block.get("arguments"), dict) else {}
                        kind = P.tool_kind(name)
                        ev = _tool_event(
                            f"imp-{n}", at, kind=kind, name=name, title=P.tool_title(name, args), inp=args,
                            path=str(args.get("path") or args.get("file_path") or ""),
                            diff=P.edit_diff(name, args) if kind in ("edit", "write") else "",
                        )
                        out.append(ev)
                        if block.get("id"):
                            pending[str(block["id"])] = ev
            elif role == "toolResult":
                _settle(pending.pop(str(msg.get("toolCallId") or ""), None),
                        P.result_text(msg), bool(msg.get("isError")))
    return out


#: OpenCode parts read at most (newest).
OPENCODE_PARTS = 6000


def _opencode_events(db: Path, hid: str) -> list[dict[str, Any]]:
    """OpenCode's database, opened READ-ONLY (``mode=ro``)."""
    from . import opencode as O

    con = O._connect_ro(db)
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT m.data, p.data, p.time_created FROM part p JOIN message m ON m.id = p.message_id "
            "WHERE p.session_id = ? ORDER BY p.time_created DESC, p.id DESC LIMIT ?",
            (hid, OPENCODE_PARTS),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    n = 0
    for mdata, pdata, created in reversed(rows):
        try:
            m = json.loads(mdata) if isinstance(mdata, str) else {}
            p = json.loads(pdata) if isinstance(pdata, str) else {}
        except ValueError:
            continue
        if not isinstance(m, dict) or not isinstance(p, dict):
            continue
        at = _iso(created)
        role = m.get("role")
        ptype = p.get("type")
        if ptype == "text" and str(p.get("text") or "").strip() and not p.get("synthetic"):
            if role == "user":
                out.append(_user(at, str(p["text"])))
            else:
                n += 1
                out.append(_answer(f"imp-{n}", at, str(p["text"])))
        elif ptype == "tool":
            n += 1
            name = str(p.get("tool") or "")
            state = p.get("state") if isinstance(p.get("state"), dict) else {}
            args = state.get("input") if isinstance(state.get("input"), dict) else {}
            kind = O.tool_kind(name)
            status = str(state.get("status") or "")
            out.append(_tool_event(
                f"imp-{n}", at, kind=kind, name=name, title=O.tool_title(name, kind, args), inp=args,
                status="failed" if status == "error" else "done",
                output=str(state.get("output") or state.get("error") or ""),
                path=O._path_of(args), diff=O.args_diff(name, args) if kind in ("edit", "write") else "",
            ))
    return out


def transcript_source(harness: str, home: str, folder: str, hid: str) -> Path | None:
    """BLOCKING: the file holding the tool's own saved conversation ``hid``
    (matched by NAME or id, never a path joined from it), else None."""
    from . import opencode as O
    from . import pi as P

    hid = str(hid or "")
    if not hid or not home:
        return None
    root = Path(home)
    try:
        if harness == "claude-code":
            for p in root.glob("projects/*/*.jsonl"):
                if p.stem == hid:
                    return p
            return None
        if harness == "codex":
            from ..history.codex import id_from_path

            for p in root.glob("sessions/*/*/*/*.jsonl"):
                if hid in p.stem and id_from_path(p) == hid:
                    return p
            return None
        if harness == "pi":
            from ..harness.sessions import _network_spelling

            same = os.path.normcase(os.path.normpath(home)) == os.path.normcase(os.path.normpath(str(P.agent_dir())))
            where = P.sessions_dir(folder) if same else root / "sessions"
            # Review (v1.418.0; the v1.379.0 rule): a project's own
            # .pi/settings.json may name a network sessionDir — listing it
            # would open an SMB connection on every view. Refused by its
            # TEXT, before any stat, unless the session's folder is itself
            # on a share.
            if _network_spelling(str(where)) and not _network_spelling(folder):
                return None
            return P.find_session_file(where, hid)
        if harness == "opencode":
            db = root / "opencode.db"
            return db if db.is_file() and O.SESSION_ID_RX.match(hid) else None
    except OSError:
        return None
    return None


_CACHE: dict[tuple[str, str], tuple[tuple[int, int], list[dict[str, Any]]]] = {}
#: Where each conversation's file was found ((harness, home, folder, id) → path).
_SOURCES: dict[tuple[str, str, str, str], Path] = {}
_CACHE_LOCK = threading.Lock()


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    sig = (int(st.st_size), int(st.st_mtime_ns))
    if path.name == "opencode.db":
        wal = path.with_name(path.name + "-wal")
        try:
            w = wal.stat()
            sig = (sig[0] + int(w.st_size), max(sig[1], int(w.st_mtime_ns)))
        except OSError:
            pass
    return sig


def transcript_events(harness: str, home: str, folder: str, hid: str, *,
                      limit: int = TRANSCRIPT_EVENTS, allow_stale: bool = False) -> list[dict[str, Any]]:
    """BLOCKING, never raises: the tool's own conversation ``hid`` as view
    events (oldest first, the newest ``limit``); [] when it cannot be read.
    Cached by the file's signature; ``allow_stale``: a cached reading is used
    even when the file moved (a reply still running here writes to it)."""
    try:
        # Review (v1.418.0, C4.11): the file is FOUND once (a listing of
        # every project folder) — a later view only stats it.
        where = (str(harness), str(home), str(folder), str(hid))
        with _CACHE_LOCK:
            path = _SOURCES.get(where)
        sig = _signature(path) if path is not None else None
        if sig is None:
            path = transcript_source(harness, home, folder, hid)
            if path is None:
                return []
            sig = _signature(path)
            if sig is None:
                return []
            with _CACHE_LOCK:
                _SOURCES[where] = path
                while len(_SOURCES) > TRANSCRIPT_CACHE * 4:
                    _SOURCES.pop(next(iter(_SOURCES)))
        key = (str(path), str(hid))
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit is not None and (hit[0] == sig or allow_stale):
            return [dict(e) for e in hit[1][-limit:]]
        if harness == "claude-code":
            events = _claude_events(path)
        elif harness == "codex":
            events = _codex_events(path, hid)
        elif harness == "pi":
            events = _pi_events(path)
        elif harness == "opencode":
            events = _opencode_events(path, hid)
        else:
            events = []
        events = events[-max(1, TRANSCRIPT_EVENTS):]
        with _CACHE_LOCK:
            _CACHE[key] = (sig, events)
            while len(_CACHE) > TRANSCRIPT_CACHE:
                _CACHE.pop(next(iter(_CACHE)))
        return [dict(e) for e in events[-limit:]]
    except Exception:  # noqa: BLE001 — a transcript that cannot be read is no history, never an error
        log.debug("a coding tool's own transcript could not be read", exc_info=True)
        return []


def forget_transcripts() -> None:
    """Tests: drop every cached transcript."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _SOURCES.clear()


def turn_windows(events: list[dict[str, Any]]) -> list[tuple[datetime, datetime | None]]:
    """The log's own turns as (start, end) — a turn with no ``done`` yet runs
    on (end None)."""
    windows: list[tuple[datetime, datetime | None]] = []
    open_at: datetime | None = None
    for ev in events:
        kind = ev.get("type")
        if kind == "user":
            at = _parse_at(ev.get("at"))
            if at is None:
                continue
            if open_at is not None:
                windows.append((open_at, at))
            open_at = at
        elif kind == "done" and open_at is not None:
            end = _parse_at(ev.get("at"))
            windows.append((open_at, end or open_at))
            open_at = None
    if open_at is not None:
        windows.append((open_at, None))
    return windows


#: A transcript entry this close to a log turn's edge is that turn's (the
#: tool stamps its records a moment before or after Iron Jarvis's own).
EDGE_S = 2.0


def merge_transcript(log_events: list[dict[str, Any]], transcript: list[dict[str, Any]], *,
                     cut: bool = False) -> list[dict[str, Any]]:
    """The log's events and the transcript's, merged by time. A transcript
    entry inside one of the log's turns (with :data:`EDGE_S` either side) is
    that turn's and left out. ``cut``: the log view holds only its newest
    events, so transcript entries older than its oldest are left out too
    (their turns are not on screen to say what is a duplicate)."""
    if not transcript:
        return list(log_events)
    if not log_events:
        return list(transcript)
    from datetime import timedelta

    edge = timedelta(seconds=EDGE_S)
    windows = turn_windows(log_events)
    oldest = None
    for ev in log_events:
        oldest = _parse_at(ev.get("at"))
        if oldest is not None:
            break
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for ev in transcript:
        at = _parse_at(ev.get("at"))
        if at is None:
            continue
        if cut and oldest is not None and at < oldest:
            continue
        if any(start - edge <= at and (end is None or at <= end + edge) for start, end in windows):
            continue
        kept.append((at, ev))
    if not kept:
        return list(log_events)
    out: list[dict[str, Any]] = []
    i = 0
    for ev in log_events:
        at = _parse_at(ev.get("at"))
        while i < len(kept) and at is not None and kept[i][0] < at:
            out.append(kept[i][1])
            i += 1
        out.append(ev)
    out.extend(e for _, e in kept[i:])
    return out


# --------------------------------------------------------------------------- #
# Which sessions lived outside the view
# --------------------------------------------------------------------------- #
def _outside_path(home: Any, sid: str) -> Path:
    from .events import session_dir

    return session_dir(home, sid) / "outside.json"


def mark_outside(home: Any, sid: str, why: str) -> None:
    """BLOCKING, never raises: this session's conversation lived (or lives)
    outside the Code view — opened from the tool's saved sessions, brought
    from a terminal, or opened in one — so its view merges the tool's own
    transcript in from now on. Written in Iron Jarvis's own folder only."""
    path = _outside_path(home, sid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"why": str(why or ""), "at": _iso(time.time())}), encoding="utf-8")
    except OSError:
        log.debug("could not note that a Code-view session lived outside it", exc_info=True)


def went_outside(home: Any, sid: str) -> bool:
    """BLOCKING (one stat): :func:`mark_outside` was written for this session."""
    try:
        return _outside_path(home, sid).is_file()
    except OSError:
        return False
