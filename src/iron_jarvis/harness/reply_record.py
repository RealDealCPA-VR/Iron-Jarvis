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

WHAT A SAVE COSTS (v1.418.0, C3.6). A save used to encode the WHOLE list and
rewrite its row — up to 4 M chars, twice a round — for a round that added two
messages. Now a turn's FIRST save writes the list whole; every later one
APPENDS only the messages added since: they alone are encoded, and SQLite
joins them onto the stored text in one ``UPDATE`` (guarded by the length the
row had, so a row someone else moved or changed is never appended to). Both
of a round's saves stay — each covers a moment a restart can land in (the
model's call; the tools' run), and :mod:`tests.test_build_resume_v1391` pins
both — but each is now the size of what the round added. Anything that would
make an append wrong falls back to the whole write, so the row is always
byte-identical to what a whole write would have stored: an earlier message
changed in place (``trim_tool_images``), the row changed or gone, or the cap
(the whole write then leaves the earliest rounds out). SQLite has no
partial-value write: the ``UPDATE`` still rewrites the row's pages (in C),
but Python no longer encodes, reads or sends the whole list.

BOUNDED. A list over ``agents.transcript.MAX_TRANSCRIPT_CHARS`` (4 M chars)
keeps its question and its NEWEST whole rounds, with one note saying how many
of its earliest tool calls are left out (review: :func:`_fit` — the long
replies this exists for reach the cap, and the model only ever sees the
newest rounds that fit its window anyway). Only when even the question and
the newest round do not fit is nothing kept — the stale shorter copy is
DELETED, never resumed (resuming it would run finished steps again). A write
that fails or times out does the same. Either way the reply keeps v1.389.0's
Retry.

HOW LONG. Only while it can be resumed: a reply that ends — done or stopped
by the user — deletes its list (``routes/build.recorded_turn``); a boot
resumes or drops every list it finds (``routes/build.resume_after_restart``),
and :func:`prune` removes any left older than :data:`KEEP_DAYS`.

A REPLY THAT FAILED KEEPS ITS LIST (v1.422.0, Wave 2 "Never lose work", B1).
A provider error at round 35 of a 40-minute reply used to delete the record,
and the only way on was Retry — the question again FROM THE BEGINNING. Now a
reply that ended on an error (never a safety refusal, never beside an
account's limit card) keeps its list under a key of its own,
:data:`FAILED_PREFIX` ``+ <turn id>`` (:meth:`ReplyRecord.keep_failed`), and
its reply line offers "Continue from where it stopped": a new turn that
carries it on from its kept rounds, the restart resume's own way
(``routes/build.start_build_turn`` with ``resume_failed``). The boot's resume
pass CANNOT see such a list (:func:`kept` reads :data:`RUN_PREFIX` only), so a
failed reply is never carried on by itself — only by the user's press
(:func:`claim` with ``failed=True``). A kept failed list goes when the
session starts any other turn (:func:`drop_failed` — a newer message, or
Retry, makes it moot), and :func:`prune` removes it after :data:`KEEP_DAYS`
like the others.

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
#: v1.422.0 (B1): ``RunTranscript.run_id`` of a reply that FAILED and kept its
#: list for "Continue from where it stopped": this + the turn id. A key of its
#: own so the boot's resume pass (:func:`kept`) never sees it.
FAILED_PREFIX = "build-failed:"
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
#: v1.422.0 (B1): the model's note at the end of a FAILED reply's kept
#: conversation, when the user pressed "Continue from where it stopped".
FAILED_RESUME_NOTE = (
    "[Your reply stopped here on an error from the model's service (it was busy, timed out or"
    " the connection dropped), and the user asked you to continue. Your work so far is above,"
    " exactly as it was. A tool call that was cut off says so in its result: a call that only"
    " reads is safe to run again; for anything else, check whether it took effect before doing"
    " it again. Carry on with the reply from where you stopped, and do not repeat work that is"
    " already done.]"
)
#: v1.422.0 (B1): that continue turn's question line (a continuation: the
#: screen hides it; a later turn's history reads it).
FAILED_RESUME_QUESTION = "Continue the reply that stopped on an error, from where it stopped."

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


def failed_run_id_for(turn_id: str) -> str:
    """v1.422.0 (B1): the key a FAILED reply's kept list moves to."""
    return FAILED_PREFIX + str(turn_id or "")


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


def _fingerprint(m: Any) -> tuple[int, str, int, int, bool, int]:
    """What a kept message looked like when it was written (v1.418.0): the
    object, its role, its text's length AND hash, whether it carried a
    picture and how many calls — enough to see the in-place changes the
    lanes make to an earlier message (``trim_tool_images``; ``attach_check``,
    whose cut text can come out the SAME length — review: a length alone
    missed it) and any replacement. A ``str`` caches its own hash, so an
    unchanged message costs nothing on the next save."""
    content = getattr(m, "content", "") or ""
    text = content if isinstance(content, str) else str(content)
    return (
        id(m), str(getattr(m, "role", "") or ""), len(text), hash(text),
        bool(getattr(m, "images", None)), len(getattr(m, "tool_calls", None) or []),
    )


#: v1.391.0 review: what stands right after the question when a kept list
#: had to leave its EARLIEST steps out to stay under the cap (see
#: :func:`_fit`). The model reads it on a resume.
LEFT_OUT_NOTE = (
    "[Iron Jarvis kept this reply's record small by leaving out its earliest {n} tool calls "
    "(above this note they are not shown). What they did is already in the folder: check the "
    "files before doing any of that work again.]"
)


def _encode_rows(snaps: list[_Snap], real: dict[str, str]) -> list[dict[str, Any]]:
    from ..agents.transcript import _encode

    rows = _encode(snaps)
    for row in rows:
        for c in row.get("tool_calls") or []:
            name = real.get(str(c.get("id") or ""), "")
            if name and name != c.get("name"):
                c["tool"] = name
    return rows


def _encode_text(snaps: list[_Snap], real: dict[str, str]) -> str:
    return json.dumps(_encode_rows(snaps, real))


def _fit(rows: list[dict[str, Any]], cap: int) -> "str | None":
    """The kept list as JSON, at most ``cap`` chars, or None when even the
    question and the newest round do not fit (v1.391.0 review).

    The doer kept NOTHING once a reply's list passed the cap — and the cap
    (``agents.transcript.MAX_TRANSCRIPT_CHARS``, 4 M chars) is what the long
    replies this feature exists for reach (120 reads of up to 32,000 chars
    each), so exactly those replies lost the carry-on and fell back to Retry
    from the beginning. The model never sees that much anyway: each round
    sends the list fitted to its window (``chat_turn._fit_turn_transcript``
    drops the oldest rounds first). So the list keeps its question and its
    NEWEST rounds, each round whole (an assistant turn with its tool results
    is ONE unit — never split), and the earliest ones are left out, with
    :data:`LEFT_OUT_NOTE` saying how many tool calls are not shown."""
    parts = [json.dumps(r) for r in rows]

    def size(ps: list[str]) -> int:
        return 2 + sum(len(p) for p in ps) + 2 * max(0, len(ps) - 1)

    if size(parts) <= cap:
        return "[" + ", ".join(parts) + "]"
    if len(rows) < 2:
        return None
    # The units after the question: an assistant turn with the tool results
    # that follow it, or any other row alone.
    units: list[tuple[int, int]] = []
    i = 1
    while i < len(rows):
        j = i + 1
        if rows[i].get("role") == "assistant":
            while j < len(rows) and rows[j].get("role") == "tool":
                j += 1
        units.append((i, j))
        i = j
    left_out = 0
    for k, (a, b) in enumerate(units):
        if k == len(units) - 1:
            return None  # even the newest round alone does not fit
        left_out += sum(len(r.get("tool_calls") or []) for r in rows[a:b])
        keep = parts[units[k + 1][0]:]
        note = json.dumps({"role": "user", "content": LEFT_OUT_NOTE.format(n=left_out)})
        kept = [parts[0], note, *keep]
        if size(kept) <= cap:
            return "[" + ", ".join(kept) + "]"
    return None


def _write(engine: Any, run_id: str, session_id: str, snaps: list[_Snap],
           real: dict[str, str], sizes: "dict[str, int] | None" = None,
           low_trust: str = "") -> str:
    """"ok", "over" (over the cap even after leaving the earliest rounds out:
    the row is removed), or "failed" (the row is removed when it can be). On
    the writer thread. ``sizes`` (v1.418.0) receives the stored text's length
    (``"len"``) on "ok" — what the next save's append is guarded by."""
    from ..agents import transcript as _transcript

    try:
        text = _fit(_encode_rows(snaps, real), _transcript.MAX_TRANSCRIPT_CHARS)
    except Exception:  # noqa: BLE001
        log.warning("could not encode the conversation of %s", run_id, exc_info=True)
        _delete(engine, run_id)
        return "failed"
    if text is None:
        _delete(engine, run_id)
        return "over"
    try:
        with session_scope(engine) as db:
            row = db.get(RunTranscript, run_id) or RunTranscript(run_id=run_id)
            row.session_id = session_id
            row.messages_json = text
            row.updated_at = utcnow()
            if low_trust:
                # v1.422.0 review: in the same write as the rounds it covers.
                row.low_trust = low_trust
            db.add(row)
            db.commit()
        if sizes is not None:
            sizes["len"] = len(text)
        return "ok"
    except Exception:  # noqa: BLE001 — a kept list never fails a reply
        log.warning("could not keep the conversation of %s", run_id, exc_info=True)
        _delete(engine, run_id)
        return "failed"


def _append(engine: Any, run_id: str, session_id: str, snaps: list[_Snap],
            real: dict[str, str], start: int, expect: int,
            sizes: "dict[str, int] | None" = None, low_trust: str = "") -> str:
    """v1.418.0 (C3.6): add ``snaps[start:]`` to the stored list, whose text
    is ``expect`` chars long: only those messages are encoded, and SQLite
    joins them onto the row (``messages_json`` minus its closing ``]``, then
    ``, <rows>]`` — exactly the text :func:`_fit` writes for the whole list,
    because :func:`_encode` encodes each message on its own and every row is
    ASCII JSON). Falls back to :func:`_write` (the whole list) when the
    result would pass the cap, or when the row is not the one this record
    wrote (gone, moved, changed: the length guard). Same outcomes as
    :func:`_write`. On the writer thread."""
    from sqlalchemy import func, update

    from ..agents import transcript as _transcript

    try:
        rows = _encode_rows(snaps[start:], real)
        delta = "".join(", " + json.dumps(r) for r in rows) + "]"
    except Exception:  # noqa: BLE001 — the whole write decides
        return _write(engine, run_id, session_id, snaps, real, sizes, low_trust)
    new_len = expect - 1 + len(delta)
    if new_len > _transcript.MAX_TRANSCRIPT_CHARS:
        return _write(engine, run_id, session_id, snaps, real, sizes, low_trust)
    try:
        with session_scope(engine) as db:
            col = RunTranscript.messages_json
            res = db.exec(  # type: ignore[call-overload]
                update(RunTranscript)
                .where(RunTranscript.run_id == run_id)
                .where(RunTranscript.session_id == session_id)
                .where(func.length(col) == expect)
                .values(messages_json=func.substr(col, 1, expect - 1).op("||")(delta),
                        updated_at=utcnow(),
                        # v1.422.0 review: the reply's low trust, in the same
                        # statement as the rounds it covers.
                        **({"low_trust": low_trust} if low_trust else {}))
            )
            db.commit()
            appended = int(getattr(res, "rowcount", 0) or 0) == 1
    except Exception:  # noqa: BLE001
        log.warning("could not add to the conversation of %s; writing it whole", run_id, exc_info=True)
        appended = False
    if not appended:
        return _write(engine, run_id, session_id, snaps, real, sizes, low_trust)
    if sizes is not None:
        sizes["len"] = new_len
    return "ok"


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


def _move_failed(engine: Any, run_id: str, to_id: str) -> bool:
    """v1.422.0 (B1): move a reply's kept list to its FAILED key (no longer
    waiting). True when exactly that row moved. On the writer thread."""
    from sqlalchemy import update

    try:
        with session_scope(engine) as db:
            res = db.exec(  # type: ignore[call-overload]
                update(RunTranscript)
                .where(RunTranscript.run_id == run_id)
                .values(run_id=to_id, waiting=None, updated_at=utcnow())
            )
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0) == 1
    except Exception:  # noqa: BLE001 — a kept list never fails a reply
        log.warning("could not keep the conversation of the failed reply %s", run_id, exc_info=True)
        return False


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
        #: v1.422.0 (B1): how ``resumed_from`` is carried on — "restart" (the
        #: boot's pass) or "failed" (the user's "Continue from where it
        #: stopped" after an error); the reply line says which.
        self.resume_kind = "restart"
        #: False once a save failed or the list went over its cap: nothing is
        #: written again for this turn (its list is gone; Retry stays).
        self.enabled = True
        #: Why it stopped keeping its list ("over" | "failed"), for a test.
        self.dropped = ""
        self._real: dict[str, str] = {}
        self._last: "tuple[int, int] | None" = None
        self._waiting = False
        #: v1.418.0 (C3.6): what the row holds now — a fingerprint of each
        #: message it was written from (identity, role, content length and
        #: hash, picture, calls) and the stored text's length — so the next save
        #: appends only what was added. None: the next save writes it whole.
        self._kept: "list[tuple[int, str, int, int, bool, int]] | None" = None
        self._kept_len = 0
        #: v1.422.0 review: why the reply runs at LOW trust ("" = full) —
        #: noted by the lane before each save (:meth:`note_trust`) and written
        #: with the rounds, so a restart's carry-on (and "Continue from where
        #: it stopped") starts low from it (:func:`low_trust_of`).
        self.low_trust = ""
        self._low_written = False
        #: v1.423.0 (Wave 3 lane D, item 5): the lane's ONE trust record for
        #: the turn (:meth:`watch_trust`), read LIVE by :meth:`trust_now` — a
        #: reply the user stops right after a flagged read is low before any
        #: later save could note it.
        self._live_trust: "dict[str, Any] | None" = None

    def watch_trust(self, state: Any) -> None:
        """v1.423.0 (Wave 3 lane D, item 5): the stream lane hands the turn's
        ONE trust record (``{low, tainted, reason}`` — mutated in place by a
        taint) when the turn starts, so :meth:`trust_now` reads the reply's
        trust as it stands at any moment, a Stop included. Never raises."""
        if isinstance(state, dict):
            self._live_trust = state

    def trust_now(self) -> str:
        """Why the reply runs at LOW trust right now ("" = full): the live
        record (:meth:`watch_trust`) first, else what a save noted. A low
        reply stays low. The saved line of a reply the user STOPPED says so
        from this (``routes/build._ReplyWatch``), and the fix-request report
        (``agents/fix_request``) then reads nothing from it. Never raises."""
        if self._live_trust is not None:
            self.note_trust(self._live_trust)
        return self.low_trust

    def note_trust(self, state: Any) -> None:
        """v1.422.0 review: the lane's trust record (``{low, tainted,
        reason}``): once the reply is low it stays low (a taint never
        reverses), and the next save writes it. Never raises."""
        try:
            if self.low_trust or not isinstance(state, dict):
                return
            if state.get("low") or state.get("tainted"):
                self.low_trust = str(state.get("reason") or "").strip() or "low trust"
        except Exception:  # noqa: BLE001
            return

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
        if mark == self._last and (self._low_written or not self.low_trust):
            return
        low = self.low_trust
        snaps = [_Snap(m) for m in tail]
        prints = [_fingerprint(m) for m in tail]
        real = dict(self._real)
        loop = asyncio.get_running_loop()
        sizes: dict[str, int] = {}
        kept = self._kept
        # v1.418.0 (C3.6): append when every message the row was written from
        # is still there, unchanged and in place; else the whole list.
        if kept is not None and len(prints) > len(kept) and prints[:len(kept)] == kept:
            fut = loop.run_in_executor(
                _writer(), _append, self.engine, self.run_id, self.session_id, snaps, real,
                len(kept), self._kept_len, sizes, low,
            )
        else:
            fut = loop.run_in_executor(
                _writer(), _write, self.engine, self.run_id, self.session_id, snaps, real, sizes, low,
            )
        self._kept = None  # until this write is known to have landed
        try:
            outcome = await asyncio.wait_for(asyncio.shield(fut), SAVE_WAIT_S)
        except asyncio.TimeoutError:
            outcome = "failed"
            loop.run_in_executor(_writer(), _delete, self.engine, self.run_id)
        if outcome == "ok":
            self._last = mark
            if low:
                self._low_written = True
            if "len" in sizes:
                self._kept, self._kept_len = prints, sizes["len"]
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

    @property
    def kept_rounds(self) -> bool:
        """v1.422.0 (B1): does the kept list hold anything past the question
        (a round, a steer note, a resumed reply's earlier rounds)? A reply
        that failed before that has nothing to continue from — its Retry is
        the same thing."""
        return self.enabled and self._last is not None and self._last[0] >= 2

    async def keep_failed(self) -> bool:
        """The reply ENDED ON AN ERROR (v1.422.0, Wave 2 "Never lose work",
        B1): keep its list for "Continue from where it stopped" — MOVED to
        :func:`failed_run_id_for` (the boot's resume pass never reads that
        key), no longer waiting. Off the loop, on the writer thread (after
        any save asked for before it). True only when a list holding a round
        past the question was really kept; False leaves the caller to forget
        it as before."""
        if not self.kept_rounds:
            return False
        fut = asyncio.get_running_loop().run_in_executor(
            _writer(), _move_failed, self.engine, self.run_id, failed_run_id_for(self.turn_id),
        )
        try:
            moved = bool(await asyncio.wait_for(asyncio.shield(fut), SAVE_WAIT_S))
        except asyncio.TimeoutError:
            return False
        if moved:
            # Nothing more is written for this turn: its list now lives under
            # the failed key, which only the user's press reads.
            self.enabled = False
        return moved

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
    key columns only (never a list's text), oldest first. A FAILED reply's
    list (:data:`FAILED_PREFIX`, v1.422.0) is never among them: only the
    user's "Continue from where it stopped" carries one on."""
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


def low_trust_of(engine: Any, turn_id: str, *, failed: bool = False) -> str:
    """v1.422.0 review: why the kept reply ran at LOW trust ("" = it did not,
    or no list is kept) — the restart's list, or (``failed``) a failed
    reply's. BLOCKING; never raises."""
    key = failed_run_id_for(turn_id) if failed else run_id_for(turn_id)
    try:
        with session_scope(engine) as db:
            row = db.get(RunTranscript, key)
            return str(getattr(row, "low_trust", None) or "") if row is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def has_failed(engine: Any, turn_id: str) -> bool:
    """v1.422.0 (B1): is a FAILED reply's list kept for ``turn_id``?"""
    with session_scope(engine) as db:
        return db.get(RunTranscript, failed_run_id_for(turn_id)) is not None


def drop_failed(engine: Any, session_id: str) -> int:
    """v1.422.0 (B1): forget every FAILED reply's list of one session (its
    ``build:<thread id>``) — another turn of the session started, so "Continue
    from where it stopped" is moot (a newer message, or Retry, which asks the
    question again from the start). A list the starting turn just took has
    already moved to that turn's own key. BLOCKING; never raises. Returns how
    many."""
    from sqlalchemy import delete

    sid = str(session_id or "")
    if not sid:
        return 0
    try:
        with session_scope(engine) as db:
            res = db.exec(
                delete(RunTranscript)  # type: ignore[call-overload]
                .where(RunTranscript.run_id.like(FAILED_PREFIX + "%"))  # type: ignore[attr-defined]
                .where(RunTranscript.session_id == sid)
            )
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001
        log.warning("could not forget the failed replies of %s", sid, exc_info=True)
        return 0


def prune(engine: Any, *, days: int = KEEP_DAYS) -> int:
    """Remove kept lists older than ``days`` — a failed reply's too
    (v1.422.0). Returns how many."""
    from sqlalchemy import delete, or_

    cutoff = _naive(utcnow() - timedelta(days=days))
    try:
        with session_scope(engine) as db:
            res = db.exec(
                delete(RunTranscript)  # type: ignore[call-overload]
                # v1.422.0 (B1): a FAILED reply's kept list expires the same.
                .where(or_(
                    RunTranscript.run_id.like(RUN_PREFIX + "%"),  # type: ignore[attr-defined]
                    RunTranscript.run_id.like(FAILED_PREFIX + "%"),  # type: ignore[attr-defined]
                ))
                .where(RunTranscript.updated_at < cutoff)
            )
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001 — a boot step never blocks the boot
        log.warning("pruning kept coding-session conversations failed", exc_info=True)
        return 0


def load(engine: Any, turn_id: str, registry: Any = None, *, since: Any = None,
         failed: bool = False, session_id: str = "") -> "list[Any] | None":
    """The cut-off turn's messages ready to carry on from (its question first;
    every call with no result answered), or None when none was kept.
    ``failed`` (v1.422.0): read the list a FAILED reply kept
    (:func:`failed_run_id_for`) instead of a cut-off one's.

    v1.422.0 review: ``session_id`` (``build:<thread id>``) — None unless the
    list was kept BY THAT SESSION. A turn id alone named the list, and Branch
    from here copies a failed reply's line (its ``turnId``): Continue in the
    branch took the ORIGINAL session's kept rounds."""
    from ..agents.transcript import load_for_resume
    from . import sessions as S

    key = failed_run_id_for(turn_id) if failed else run_id_for(turn_id)
    with session_scope(engine) as db:
        row = db.get(RunTranscript, key)
        if row is None:
            return None
        owner = str(row.session_id or "")
    if not owner.startswith(S.SESSION_PREFIX):
        return None
    if session_id and owner != str(session_id):
        return None
    session_id = owner
    out = load_for_resume(
        engine, key, registry,
        ledger_run_id=session_id, since=_naive(since) if since is not None else None,
    )
    if not out or getattr(out[0], "role", "") != "user":
        return None
    return out


def claim(engine: Any, turn_id: str, to_turn: str = "", *, failed: bool = False,
          session_id: str = "") -> bool:
    """TAKE a kept list for the ONE resume that carries it on, in one
    statement, so of two resumes of the same turn (a boot that runs twice, a
    retry racing the first) exactly one sees it. A list marked ``waiting`` is
    never taken. BLOCKING.

    v1.391.0 review: ``to_turn`` — the turn that carries it on. The list is
    MOVED to that turn's key (not deleted): until the carrying-on turn's own
    first save — after its grounding, a summary, its folder snapshot, which
    can take seconds or a model call — the list it was given is still kept
    under ITS name, so a stop in that window leaves the reply resumable by
    the next boot instead of lost. "" removes it (the old take).

    v1.422.0 (B1): ``failed`` — take a FAILED reply's list instead (the
    user's "Continue from where it stopped": :func:`failed_run_id_for`). The
    boot's pass never passes it, so it can never carry a failed reply on.
    v1.422.0 review: ``session_id`` — only a list that session kept (see
    :func:`load`)."""
    from sqlalchemy import delete, or_, true, update

    not_waiting = or_(RunTranscript.waiting.is_(None), RunTranscript.waiting == False)  # noqa: E712
    source = failed_run_id_for(turn_id) if failed else run_id_for(turn_id)
    owned = RunTranscript.session_id == str(session_id) if session_id else true()
    try:
        with session_scope(engine) as db:
            if to_turn:
                stmt = (
                    update(RunTranscript)
                    .where(RunTranscript.run_id == source)
                    .where(not_waiting)
                    .where(owned)
                    .values(run_id=run_id_for(to_turn), updated_at=utcnow(), waiting=None)
                )
            else:
                stmt = (
                    delete(RunTranscript)
                    .where(RunTranscript.run_id == source)
                    .where(not_waiting)
                    .where(owned)
                )
            res = db.exec(stmt)  # type: ignore[call-overload]
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
    "FAILED_PREFIX", "FAILED_RESUME_NOTE", "FAILED_RESUME_QUESTION", "KEEP_DAYS", "RESUME_NOTE",
    "RESUME_QUESTION", "RUN_PREFIX", "SAVE_WAIT_S", "ReplyRecord", "claim", "drop", "drop_failed",
    "failed_run_id_for", "has_failed", "has_record", "kept", "load", "low_trust_of", "prune", "run_id_for",
    "started_at", "turn_of",
]
