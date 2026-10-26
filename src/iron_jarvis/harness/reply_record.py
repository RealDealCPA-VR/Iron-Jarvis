"""A CODING SESSION'S REPLY KEEPS ITS OWN CONVERSATION (v1.391.0, the
long-jobs plan's Phase 2 — "survives sleep and restarts", decision 2).

THE GAP. A coding session's reply runs for an hour or more. What the MODEL was
told and answered — every tool call and its full result — lived only in the
stream lane's memory (``routes/chat.chat_stream``'s ``msgs``); the session
kept the reply's frames and, at its end, one reply line. An update, a crash or
a Windows restart in the middle cut the reply off, and v1.389.0's Retry
started the question again FROM THE BEGINNING: every file read again, every
command run again. Agent runs had already solved this (v1.337.0,
``agents/transcript``); coding sessions had not.

WHAT THIS KEEPS. The agent-run store, keyed by the TURN: ``RunTranscript``
with ``run_id = "build-turn:<turn id>"`` and ``session_id = "build:<thread
id>"``. The prefix keeps a turn id (chosen by a page) from ever meeting an
agent run's id, and the session id is the key every tool call of the turn is
ledgered under, so a cut-off call's own ledger row can be found. The list is
the turn's OWN part of the model's conversation — its question first, then
every round (the assistant turn with its tool calls and its ``role="tool"``
results, always as ONE unit), steer notes and Iron Jarvis's own notes — in
the store's shape (``agents/transcript._encode``: images become its marker;
native blocks are not kept). The history BEFORE the question is not kept: it
is the session's transcript, and a resumed turn plans it again like any turn.

WHEN. The lane (``chat_stream``, a coding session's turn only — it is handed
a :class:`ReplyRecord` by ``routes/build.start_build_turn``; chat, the POST
lane, the browser sidebar and the benchmark get none and are byte-identical)
saves at the top of every round — the previous round's results are in — and
right after the model asked for tools, BEFORE they run. So whatever stops the
app, the list ends on complete rounds or on ONE assistant turn whose calls
were running; ``load_for_resume`` answers those with what became of them (the
boot reconcile's ledger note, else ``replay_safe``'s words — a cut-off
command is never run again blindly). Every write is off the loop, on ONE
writer thread (so two writes of a turn never land out of order), awaited for
at most :data:`SAVE_WAIT_S`.

BOUNDED. A list over ``agents.transcript.MAX_TRANSCRIPT_CHARS`` (4 M chars) is
not kept at all — the stale shorter copy is DELETED, never resumed (resuming
it would run finished steps again). A write that fails or times out does the
same. Either way the reply keeps v1.389.0's Retry.

HOW LONG. Only while it can be resumed: a reply that ends — done, failed or
stopped by the user — deletes its list (``routes/build.recorded_turn``); a
boot resumes or drops every list it finds (``routes/build.resume_after_
restart``), and :func:`prune` removes any left older than :data:`KEEP_DAYS`.

WAITING. A reply parked on the user (an approval card, its own question, an
app's question or model request) is marked ``waiting`` while it is, and a boot
never resumes it by itself — it stays as v1.389.0 leaves it.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Any

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import RunTranscript

log = logging.getLogger(__name__)

#: ``RunTranscript.run_id`` of a coding session's turn: this + the turn id.
RUN_PREFIX = "build-turn:"
#: How long one save may hold the reply up before it is given up (the list is
#: then dropped: a reply whose record could not be kept is not resumed).
SAVE_WAIT_S = 10.0
#: A kept list a boot could not use is removed after this long.
KEEP_DAYS = 7

#: The model's own note at the end of a resumed reply's conversation
#: (agents/transcript.RESUME_NOTE's idea, in a coding session's words).
RESUME_NOTE = (
    "[Iron Jarvis stopped while you were working on this reply (an update, a restart or a"
    " crash) and has started again. Your work so far is above, exactly as it was. A tool call"
    " that was cut off says so in its result: a call that only reads is safe to run again; for"
    " anything else, check whether it took effect before doing it again. Carry on with the"
    " reply from where you stopped, and do not repeat work that is already done.]"
)
#: The resumed turn's question line (a daemon continuation: the screen hides
#: it; a later turn's history reads it).
RESUME_QUESTION = (
    "Carry on with the reply that stopped when Iron Jarvis restarted, from where it stopped."
)

_WRITER: "concurrent.futures.ThreadPoolExecutor | None" = None
_WRITER_LOCK = threading.Lock()


def _writer() -> concurrent.futures.ThreadPoolExecutor:
    """ONE thread writes every kept list: a turn's writes land in the order
    they were asked for, and none waits behind the shared default executor."""
    global _WRITER
    with _WRITER_LOCK:
        if _WRITER is None:
            _WRITER = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="ij-reply-record",
            )
        return _WRITER


def run_id_for(turn_id: str) -> str:
    return RUN_PREFIX + str(turn_id or "")


def turn_of(run_id: str) -> str:
    return str(run_id or "")[len(RUN_PREFIX):] if str(run_id or "").startswith(RUN_PREFIX) else ""


def _naive(when: Any) -> Any:
    return when.replace(tzinfo=None) if getattr(when, "tzinfo", None) is not None else when


# --------------------------------------------------------------------------- #
# the record a turn writes
# --------------------------------------------------------------------------- #


class _Snap:
    """One message as it stood when the save was asked for (read on the loop;
    encoded on the writer thread)."""

    __slots__ = ("role", "content", "tool_call_id", "name", "tool_calls", "images")

    def __init__(self, m: Any) -> None:
        self.role = getattr(m, "role", "")
        self.content = getattr(m, "content", "") or ""
        self.tool_call_id = getattr(m, "tool_call_id", None)
        self.name = getattr(m, "name", None)
        self.tool_calls = list(getattr(m, "tool_calls", None) or [])
        self.images = bool(getattr(m, "images", None))


def _encode_text(snaps: list[_Snap], real: dict[str, str]) -> str:
    from ..agents.transcript import _encode

    rows = _encode(snaps)
    for row in rows:
        for c in row.get("tool_calls") or []:
            name = real.get(str(c.get("id") or ""), "")
            if name and name != c.get("name"):
                c["tool"] = name
    return json.dumps(rows)


def _write(engine: Any, run_id: str, session_id: str, snaps: list[_Snap],
           real: dict[str, str]) -> str:
    """"ok", "over" (over the cap: the row is removed), or "failed" (the row
    is removed when it can be). On the writer thread."""
    from ..agents.transcript import MAX_TRANSCRIPT_CHARS

    try:
        text = _encode_text(snaps, real)
    except Exception:  # noqa: BLE001
        log.warning("could not encode the conversation of %s", run_id, exc_info=True)
        _delete(engine, run_id)
        return "failed"
    if len(text) > MAX_TRANSCRIPT_CHARS:
        _delete(engine, run_id)
        return "over"
    try:
        with session_scope(engine) as db:
            row = db.get(RunTranscript, run_id) or RunTranscript(run_id=run_id)
            row.session_id = session_id
            row.messages_json = text
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
        return "ok"
    except Exception:  # noqa: BLE001 — a kept list never fails a reply
        log.warning("could not keep the conversation of %s", run_id, exc_info=True)
        _delete(engine, run_id)
        return "failed"


def _mark(engine: Any, run_id: str, waiting: bool) -> None:
    try:
        with session_scope(engine) as db:
            row = db.get(RunTranscript, run_id)
            if row is None:
                return
            row.waiting = True if waiting else None
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("could not mark %s as waiting", run_id, exc_info=True)


def _delete(engine: Any, run_id: str) -> None:
    from sqlalchemy import delete

    try:
        with session_scope(engine) as db:
            db.exec(delete(RunTranscript).where(RunTranscript.run_id == run_id))  # type: ignore[call-overload]
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("could not remove the kept conversation of %s", run_id, exc_info=True)


class ReplyRecord:
    """One coding-session turn's kept conversation (see the module doc).

    ``resume_tail`` (a resumed turn only): the cut-off turn's own rounds,
    closed by ``load_for_resume``, then :data:`RESUME_NOTE` — the lane puts
    them right after the question. ``resumed_from``: the turn it carries on."""

    def __init__(self, engine: Any, thread_id: str, turn_id: str, *,
                 resume_tail: "list[Any] | None" = None, resumed_from: str = "") -> None:
        from . import sessions as S

        self.engine = engine
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.run_id = run_id_for(self.turn_id)
        self.session_id = S.session_id_for(self.thread_id)
        self.resume_tail = list(resume_tail or [])
        self.resumed_from = str(resumed_from or "")
        #: False once a save failed or the list went over its cap: nothing is
        #: written again for this turn (its list is gone; Retry stays).
        self.enabled = True
        #: Why it stopped keeping its list ("over" | "failed"), for a test.
        self.dropped = ""
        self._real: dict[str, str] = {}
        self._last: "tuple[int, int] | None" = None
        self._waiting = False

    def note_calls(self, model_calls: Any, calls: Any) -> None:
        """The REAL tool behind each call the model made (same ids): kept
        beside the model's own name, so a call cut off on a resume is judged
        by what it really was (``Bash`` is ``shell``)."""
        for c in calls or []:
            cid = str(getattr(c, "id", "") or "")
            name = str(getattr(c, "name", "") or "")
            if cid and name:
                self._real[cid] = name

    async def save(self, msgs: list[Any], head: int) -> None:
        """Keep ``msgs[head - 1:]`` — the turn's question and every round
        since (``head`` is the lane's: the index right after the question).
        Read on the loop (references only), encoded and written on the writer
        thread; awaited at most :data:`SAVE_WAIT_S`. A list that has not
        changed since the last save is not written again."""
        if not self.enabled or head <= 0:
            return
        tail = msgs[max(0, head - 1):]
        if not tail:
            return
        mark = (len(tail), id(tail[-1]))
        if mark == self._last:
            return
        snaps = [_Snap(m) for m in tail]
        real = dict(self._real)
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(_writer(), _write, self.engine, self.run_id, self.session_id, snaps, real)
        try:
            outcome = await asyncio.wait_for(asyncio.shield(fut), SAVE_WAIT_S)
        except asyncio.TimeoutError:
            outcome = "failed"
            loop.run_in_executor(_writer(), _delete, self.engine, self.run_id)
        if outcome == "ok":
            self._last = mark
            return
        # Over its cap, or the write failed: the list is gone (the writer
        # removed it), and this reply is no longer resumable.
        self.enabled = False
        self.dropped = outcome

    async def set_waiting(self, waiting: bool) -> None:
        """Mark (or clear) the reply as parked on the user. Off the loop, on
        the writer thread (after any save asked for before it)."""
        waiting = bool(waiting)
        if waiting == self._waiting or not self.enabled:
            self._waiting = waiting
            return
        self._waiting = waiting
        fut = asyncio.get_running_loop().run_in_executor(
            _writer(), _mark, self.engine, self.run_id, waiting,
        )
        try:
            await asyncio.wait_for(asyncio.shield(fut), SAVE_WAIT_S)
        except asyncio.TimeoutError:
            pass

    async def forget(self) -> None:
        """The reply ended cleanly: its list is not kept."""
        fut = asyncio.get_running_loop().run_in_executor(_writer(), _delete, self.engine, self.run_id)
        try:
            await asyncio.wait_for(asyncio.shield(fut), SAVE_WAIT_S)
        except asyncio.TimeoutError:
            pass


# --------------------------------------------------------------------------- #
# what a boot reads (all BLOCKING — callers hop off the loop)
# --------------------------------------------------------------------------- #


def kept(engine: Any) -> list[dict[str, Any]]:
    """Every kept list: ``{turn_id, thread_id, waiting, updated_at}`` — the
    key columns only (never a list's text), oldest first."""
    from sqlmodel import select

    from . import sessions as S

    out: list[dict[str, Any]] = []
    with session_scope(engine) as db:
        rows = db.exec(
            select(RunTranscript.run_id, RunTranscript.session_id, RunTranscript.waiting,
                   RunTranscript.updated_at)
            .where(RunTranscript.run_id.like(RUN_PREFIX + "%"))  # type: ignore[attr-defined]
            .order_by(RunTranscript.updated_at)  # type: ignore[arg-type]
        ).all()
    for run_id, session_id, waiting, updated_at in rows:
        turn = turn_of(run_id)
        if not turn:
            continue
        out.append({
            "turn_id": turn,
            "thread_id": S.thread_of(str(session_id or "")),
            "waiting": bool(waiting),
            "updated_at": updated_at,
        })
    return out


def has_record(engine: Any, turn_id: str) -> bool:
    with session_scope(engine) as db:
        return db.get(RunTranscript, run_id_for(turn_id)) is not None


def drop(engine: Any, turn_id: str) -> None:
    _delete(engine, run_id_for(turn_id))


def prune(engine: Any, *, days: int = KEEP_DAYS) -> int:
    """Remove kept lists older than ``days``. Returns how many."""
    from sqlalchemy import delete

    cutoff = _naive(utcnow() - timedelta(days=days))
    try:
        with session_scope(engine) as db:
            res = db.exec(
                delete(RunTranscript)  # type: ignore[call-overload]
                .where(RunTranscript.run_id.like(RUN_PREFIX + "%"))  # type: ignore[attr-defined]
                .where(RunTranscript.updated_at < cutoff)
            )
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001 — a boot step never blocks the boot
        log.warning("pruning kept coding-session conversations failed", exc_info=True)
        return 0


def load(engine: Any, turn_id: str, registry: Any = None, *, since: Any = None) -> "list[Any] | None":
    """The cut-off turn's messages ready to carry on from (its question first;
    every call with no result answered), or None when none was kept."""
    from ..agents.transcript import load_for_resume
    from . import sessions as S

    with session_scope(engine) as db:
        row = db.get(RunTranscript, run_id_for(turn_id))
        if row is None:
            return None
        session_id = str(row.session_id or "")
    if not session_id.startswith(S.SESSION_PREFIX):
        return None
    out = load_for_resume(
        engine, run_id_for(turn_id), registry,
        ledger_run_id=session_id, since=_naive(since) if since is not None else None,
    )
    if not out or getattr(out[0], "role", "") != "user":
        return None
    return out


def claim(engine: Any, turn_id: str) -> bool:
    """TAKE a kept list for the ONE resume that carries it on: removed in one
    statement, so of two resumes of the same turn (a boot that runs twice, a
    retry racing the first) exactly one sees it. A list marked ``waiting`` is
    never taken. BLOCKING."""
    from sqlalchemy import delete, or_

    try:
        with session_scope(engine) as db:
            res = db.exec(
                delete(RunTranscript)  # type: ignore[call-overload]
                .where(RunTranscript.run_id == run_id_for(turn_id))
                .where(or_(RunTranscript.waiting.is_(None), RunTranscript.waiting == False))  # noqa: E712
            )
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0) == 1
    except Exception:  # noqa: BLE001
        log.warning("could not take the kept conversation of %s", turn_id, exc_info=True)
        return False


def started_at(engine: Any, turn_id: str) -> "datetime | None":
    """When turn ``turn_id`` started (its ``ChatTurnRecord``), or None."""
    from ..core.models import ChatTurnRecord

    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        return None if row is None else row.started_at


__all__ = [
    "KEEP_DAYS", "RESUME_NOTE", "RESUME_QUESTION", "RUN_PREFIX", "SAVE_WAIT_S", "ReplyRecord",
    "claim", "drop", "has_record", "kept", "load", "prune", "run_id_for", "started_at", "turn_of",
]
