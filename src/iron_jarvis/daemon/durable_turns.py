"""Chat turns the daemon owns (v1.336.0).

Borrowed from pi-durable (ideas only, MIT): "conversations, model turns and
tool calls are committed to storage before anything is shown; if the process
dies mid-turn, reopening picks the work up where it stopped", and "cancelling
a wait only cancels that wait, never the work".

BEFORE: ``POST /chat/stream`` ran the turn INSIDE the HTTP response, with
``request.is_disconnected`` as its stop predicate. A reload, a closed window
or leaving the chat ended the turn; the partial answer was lost (only the
question had been saved, at send); a reopened window had nothing to rejoin.

NOW, for a NAMED turn (``body.turn_id`` — the chat page always mints one):

* the turn runs as its own task (:func:`start`), and its stop predicate no
  longer looks at any connection — only the turn's own Stop
  (``POST /chat/turns/{id}/stop``) ends it, plus one guard: a turn nobody
  has watched for ``ORPHAN_STOP_S`` stops (it would otherwise hold an
  unanswered question forever) — a chat turn; v1.384.0: never a Build
  session's turn (``routes/build._never_orphaned``);
* every frame goes into a :class:`TurnLog` — consecutive token and thinking
  frames merged — which is written to ``ChatTurnRecord`` about once a second
  (``PERSIST_EVERY_S``) and when the turn ends;
* any number of windows FOLLOW it (:meth:`TurnLog.follow`): the frames so far
  as one snapshot, then every new frame as it comes, then the end. The
  response that started the turn is just the first follower;
* a repeated ``POST /chat/stream`` with a turn id already running (or
  finished in the last ``FINISHED_KEEP_S``) follows that turn instead of
  starting a second one — the request id pi-durable keys submissions on;
* ``GET /chat/turns/{id}/stream`` rejoins from anywhere; a turn this process
  no longer holds is replayed from its record;
* the boot reconcile marks a turn still "running" on disk as ``interrupted``
  and ends its log with an error frame that says so, so a reopened window
  shows what was written and offers Retry.

Unnamed turns (API callers, the browser sidebar's own lane) are untouched.

RESUME POINTS (v1.384.0). Returning to a Build session whose reply still runs
used to replay EVERY frame from the start (the user's: ~840 frames, 1.2 MB)
and redraw the conversation frame by frame — "my work jumped, there was a lag
getting back". A follower that asks for them (``follow(with_ids=True)`` —
Build's page; every other caller is byte-identical) gets each frame tagged
with its POSITION, ``id: <i>:<c>``: ``i`` is the index of the STORED frame it
landed in, ``c`` that frame's text length once it landed (0 for a frame with
no text). Token and thinking frames are merged on disk, so a position names
"frame i, the first c characters of its text" — a page that kept what it had
and comes back with ``after=<i>:<c>`` is sent the REST of frame i (its text
from character c on) and every frame after it, then the live frames: nothing
lost, nothing twice, by construction (:meth:`TurnLog.frames_after`). The
tagged answer opens with ``event: resume`` (``{"resumed": bool}`` — false =
this is the WHOLE turn from its start: drop what you hold) and the stored part
ends with ``event: caught_up`` (``{"id": <position>}``), so a page can draw a
whole replay in ONE pass. A position this log does not hold (another turn's,
past its end, mid-text past what is stored) and a turn replayed from its
record are the full replay (``resumed: false``).

THE COST PER TOKEN (v1.418.0, C3.6). A merged token/thinking frame used to be
parsed and serialised again on EVERY token (``json.loads`` of the whole text
so far, then ``json.dumps``): O(L²) on the loop over a long reply. A frame of
the canonical shape ``{"text": "<escaped>"}`` (what the lanes send) now keeps
its text as a list of ESCAPED chunks (``_live``) — JSON escaping is per
character, so the chunks joined are exactly the escaped whole — and is joined
only when the frames are READ (:attr:`TurnLog.frames`: a persist, a rejoin, a
replay). A plain token makes no ``json.loads`` at all; a token that needs
escaping is parsed ALONE (for its length), never the frame it joins. Any
other shape takes the old path, byte for byte. The record's ``json.dumps``
runs on the worker thread that writes it, from a snapshot taken on the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, AsyncIterator

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import ChatTurnRecord

log = logging.getLogger("iron_jarvis.daemon.durable_turns")

#: How often a running turn's log is written to disk (at most).
PERSIST_EVERY_S = 1.0
#: v1.384.0 review: the record is rewritten WHOLE (``frames_json``) each
#: time, so its interval grows with its size — at most about this many bytes
#: a second, averaged (``TurnLog.persist_interval``). A chat-sized turn (under
#: 64 KB) is still written every second; the user's 60-round Build reply
#: (0.44 MB) every ~7 s; a 300-round one (~2 MB) about every half minute —
#: it was ~2 MB EVERY second, ~8 GB an hour, and a Build reply with no step
#: limit can run for a day. A crash loses at most one interval of what the
#: record shows (the files, the ledger and a clean end are untouched).
PERSIST_BYTES_PER_S = 64_000
#: How long a finished turn's log stays in memory for a late rejoin.
FINISHED_KEEP_S = 600.0
#: v1.420.0 (C6.10): a CHAT reply longer than this that ended is announced
#: (``chat.turn_finished``) — the chat twin of Build's
#: ``FINISH_NOTICE_AFTER_S``: a short answer is read where it was asked.
CHAT_FINISH_NOTICE_AFTER_S = 60.0

#: v1.420.0 (C6.10): the event bus each database's turns announce on —
#: bound once when the app is built (``routes/system.register``), read by
#: :class:`TurnLogs` (made on first use, with only the engine). Weak keys: a
#: closed test platform's engine never keeps its bus alive.
import threading as _threading  # noqa: E402
import weakref as _weakref  # noqa: E402

_BUSES: "_weakref.WeakKeyDictionary[Any, Any]" = _weakref.WeakKeyDictionary()


def bind_event_bus(engine: Any, bus: Any) -> None:
    """Say which event bus the turns of *engine*'s database announce on.
    Never raises."""
    try:
        # A weak VALUE too: the bus's handlers hold the platform, which holds
        # the engine — a strong value would keep the key alive for ever.
        _BUSES[engine] = _weakref.ref(bus)
    except Exception:  # noqa: BLE001 — an engine that cannot be weakly held
        log.debug("could not bind the turns' event bus", exc_info=True)


#: v1.455.0: every turn this process is driving, by id (weak: a finished
#: turn's log leaves with its registry) — read by the notifier, off the loop,
#: through :func:`turn_looked_at` / :func:`turn_chat`.
_LIVE: "_weakref.WeakValueDictionary[str, TurnLog]" = _weakref.WeakValueDictionary()
#: Third plan CS7: every turn registry, weakly (a dropped platform's goes with
#: it) — deleting a chat forgets its turns in whichever registry holds them.
_REGISTRIES: "_weakref.WeakSet[TurnLogs]" = _weakref.WeakSet()


def turn_looked_at(turn_id: str) -> "bool | None":
    """Is a window showing turn *turn_id* right now? ``None`` when this
    process holds no such turn. Never raises (v1.455.0: a failed chat turn's
    phone alert stays quiet while the user is looking at the chat, which
    already shows the failure)."""
    try:
        lg = _LIVE.get(str(turn_id or ""))
        if lg is None:
            return None
        return bool(lg.looked_at())
    except Exception:  # noqa: BLE001
        return None


def after_turn(turn_id: str, hook: Any) -> bool:
    """Call *hook* ``(status, looked_at)`` once the turn *turn_id* this
    process holds has ended (``status`` "done" | "error" | "stopped" |
    "interrupted"; ``looked_at``: was a window showing it as it ended) — at
    once when it already has. False when this process holds no such turn (the
    hook is never called). Called from any thread; the hook runs on a worker
    thread, never the loop (v1.455.0 review: a failed chat turn's phone line
    waits for the turn's end, so a failure the turn got past — a failover, a
    retry, a one-shot inside it — is never said as "didn't finish"). Never
    raises."""
    try:
        lg = _LIVE.get(str(turn_id or ""))
        if lg is None:
            return False
        lg.on_end(hook)
        return True
    except Exception:  # noqa: BLE001
        log.debug("could not wait for turn %s's end", turn_id, exc_info=True)
        return False


def _run_end_hooks(hooks: list[Any], status: str, looked: bool) -> None:
    """Each hook on a worker thread (they may send a message). Never raises."""
    if not hooks:
        return

    def _call() -> None:
        for hook in hooks:
            try:
                hook(status, looked)
            except Exception:  # noqa: BLE001 — a hook's fault is its own
                log.debug("a turn's end hook failed", exc_info=True)

    try:
        asyncio.get_running_loop().run_in_executor(None, _call)
    except Exception:  # noqa: BLE001 — no loop or a closed executor: run here
        _call()


def turn_chat(turn_id: str, thread_id: str = "") -> "tuple[str, str, str]":
    """``(thread id, title, surface)`` of the chat turn *turn_id* belongs to,
    as far as this process knows: the live turn's chat (else *thread_id*, the
    one the event carried) and that chat's title ("" when unknown); surface
    is "build" for a coding session's turn, else "chat". BLOCKING (one
    primary-key read of the title column): call it off the loop. Never
    raises."""
    thread, surface, engine = str(thread_id or ""), "chat", None
    try:
        lg = _LIVE.get(str(turn_id or ""))
        if lg is not None:
            thread = str(lg.thread_id or "") or thread
            surface = "build" if _surface_of(lg) == "build" else "chat"
            engine = getattr(lg, "engine", None)
    except Exception:  # noqa: BLE001
        pass
    title = ""
    if thread and engine is not None:
        title = reply_titles(engine, [thread]).get(thread, "")
    return thread, title, surface


def _bus_for(engine: Any) -> Any:
    try:
        ref = _BUSES.get(engine)
        return ref() if ref is not None else None
    except Exception:  # noqa: BLE001
        return None
#: A turn nobody has followed for this long is stopped (see the module doc).
ORPHAN_STOP_S = 3600.0
#: Stored frames are capped; past it, later TOKEN detail is not stored (the
#: final ``done`` frame still carries the whole reply).
MAX_STORED_CHARS = 8_000_000
#: Finished records older than this are removed at boot — never one whose
#: reply has not reached its chat or coding session (:func:`drop_old_records`,
#: third plan G6 and RB9).
KEEP_RECORDS_DAYS = 7

# --------------------------------------------------------------------------- #
# THE REPLY'S HOME (third plan G6, CS5): the daemon writes every reply into
# its chat. Before, only a Chat PAGE wrote a finished reply into the chat: a
# reply that ended while no page followed it was in no chat and no history
# search until that chat was opened, and was deleted with its record after a
# week. Now, when a chat turn ends, its reply is appended through the ONE
# server-side writer (``chat_append.append_to_chat_thread``) as
# ``{role: "assistant", content, turnId, savedBy: "daemon"}`` — at once when
# no page follows the turn; after :data:`HOME_GRACE_S` when one does (that
# page normally saves its own, richer message first, and then nothing is
# written); when ``POST /chat/turns/{id}/thread`` names a new chat's first
# turn's chat; and at start-up for a record a restart left behind.
# ``ChatTurnRecord.home`` keeps whether that happened: "due" until the reply
# is seen in its chat (or written, or there is nothing to write), then
# "home". A record from an older version (None) is never written home: its
# reply was saved by the page without a turn name, and a second copy would
# be a duplicate.
# --------------------------------------------------------------------------- #
HOME_DUE = "due"
HOME_DONE = "home"
#: How long the daemon waits, after a turn a page FOLLOWED ended, for that
#: page to save the reply itself before writing it home (a page saves at
#: once; a 409 and its re-read take a second or two).
HOME_GRACE_S = 15.0
#: The longest the turn's end waits for the write (it goes on in its thread).
HOME_WRITE_TIMEOUT_S = 30.0
#: The most records the start-up pass writes home in one start (bounded).
HOME_BOOT_LIMIT = 500
#: What a turn's reply text is when the reply was empty — the page's own
#: placeholder (``page.tsx``: ``(reply ?? "").trim() || "(no response)"``).
NO_RESPONSE = "(no response)"
#: Readiness 0j.5 item 17: WHY the orphan guard stopped a chat turn, in the
#: turn's own error frame (and so on its record). The title / fix split the
#: page's failure line reads (``chat_turn.error_payload``'s keys).
ORPHAN_STOP_TITLE = "Iron Jarvis stopped this answer"
ORPHAN_STOP_FIX = (
    "No window had shown it for an hour, so it stopped rather than keep working"
    " unwatched. What it had written so far is kept; press Retry to ask again."
)
ORPHAN_STOP_DETAIL = f"{ORPHAN_STOP_TITLE}. {ORPHAN_STOP_FIX}"
#: Frames that open a card the turn waits on besides an approval (an app's
#: question or model request, the model's own question), and the frame that
#: closes them. A turn parked on any of them is never an orphan.
_CARD_OPEN_EVENTS = frozenset({"mcp_elicitation", "mcp_sampling", "ask_user"})
_CARD_CLOSE_EVENT = "mcp_resolved"

#: The error a turn cut off by a restart ends with.
INTERRUPTED_DETAIL = (
    "Iron Jarvis stopped while answering (a restart or a crash). What it had"
    " written so far is kept; press Retry to ask again."
)
#: Third plan G23 (RB12): the error a turn ends with when its lane broke
#: OUTSIDE its own error handling (a fault in Iron Jarvis, not the model's).
#: The frame has the shape the page draws for a failed turn (``title`` +
#: ``fix``, ``dashboard/lib/failureLine.ts``); the exception goes to the log
#: only — the live stream and the stored record used to carry
#: "TypeError: ...".
LANE_BROKE_TITLE = "Iron Jarvis hit a problem while answering"
LANE_BROKE_FIX = "What it had written so far is kept; press Retry to ask again."
LANE_BROKE_DETAIL = f"{LANE_BROKE_TITLE}. {LANE_BROKE_FIX}"


def lane_broke_frame(exc: BaseException) -> dict[str, str]:
    """The ``error`` frame for a lane that raised outside its own handling:
    a sentence the app wrote itself is kept as the headline
    (``core.failures.plain_failure``), anything else is
    :data:`LANE_BROKE_TITLE`; the raw text is logged, never sent. Never
    raises."""
    try:
        from ..core.failures import plain_failure

        words = plain_failure(LANE_BROKE_DETAIL, exc)
    except Exception:  # noqa: BLE001 — the words never become the failure
        words = LANE_BROKE_DETAIL
    if words == LANE_BROKE_DETAIL:
        return {"detail": LANE_BROKE_DETAIL, "title": LANE_BROKE_TITLE, "fix": LANE_BROKE_FIX}
    return {"detail": words, "title": words, "fix": LANE_BROKE_FIX}


_MERGEABLE = ("token", "thinking")
_END = object()
#: The canonical text frame (``json.dumps({"text": t})``) the fast path keeps
#: as escaped chunks (v1.418.0, C3.6).
_TEXT_HEAD = '{"text": "'
_TEXT_TAIL = '"}'


def _escaped_text(data: str) -> "str | None":
    """The ESCAPED text of a canonical text frame (``{"text": "<esc>"}``
    exactly as ``json.dumps`` writes it, ASCII only), or None for any other
    shape (another key, a raw non-ASCII character, not JSON). No parse: a
    quote that is not escaped (after dropping escaped backslashes and escaped
    quotes) means the value ended early — another shape."""
    if not (data.startswith(_TEXT_HEAD) and data.endswith(_TEXT_TAIL)) or len(data) < len(_TEXT_HEAD) + 2:
        return None
    esc = data[len(_TEXT_HEAD):-len(_TEXT_TAIL)]
    if not (esc.isascii() and esc.isprintable()):
        return None  # a raw character json.dumps would have escaped
    plain = esc.replace("\\\\", "").replace('\\"', "") if "\\" in esc else esc
    if '"' in plain or (plain.endswith("\\") and "\\" in esc):
        return None
    return esc


def _escaped_len(esc: str) -> int:
    """The decoded length of an escaped chunk: its own length when nothing in
    it is escaped (no parse), else the chunk ALONE is parsed."""
    if "\\" not in esc:
        return len(esc)
    try:
        return len(json.loads('"' + esc + '"'))
    except ValueError:
        return -1  # not a JSON string after all: the old path decides


def sse(event: str, data: Any) -> str:
    """The wire format of ``routes.chat._sse`` (kept identical)."""
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def tagged(position: str, event: str, data: Any) -> str:
    """One frame with its resume position (v1.384.0): an SSE ``id:`` line,
    then the frame exactly as :func:`sse` writes it."""
    return f"id: {position}\n" + sse(event, data)


def _text_len(event: str, data: str) -> int:
    """The text length a stored frame carries (0 for a frame without text)."""
    if event not in _MERGEABLE:
        return 0
    try:
        parsed = json.loads(data)
    except ValueError:
        return 0
    return len(str(parsed.get("text") or "")) if isinstance(parsed, dict) else 0


def position_of(frames: list[list[str]], index: int) -> str:
    """The resume position of stored frame ``index`` as it stands."""
    event, data = frames[index]
    return f"{index}:{_text_len(event, data)}"


def tagged_replay(frames: list[list[str]], *, resumed: bool = False, running: bool = False,
                  started_ms: "int | None" = None) -> str:
    """The WHOLE stored turn as a tagged answer: ``resume`` (``resumed``
    false = drop what you hold), every frame with its position, ``caught_up``.
    The answer for a page with no usable resume point, and for a turn replayed
    from its record.

    ``started_ms`` (v1.384.0 review): when the turn STARTED (ms since the
    epoch), sent as ``resume.started_at`` — a page coming back after a reload
    counts "Working for" from it, not from the rejoin. Given only for a turn
    still running in memory; absent otherwise (the frame as before)."""
    head: dict[str, Any] = {"resumed": resumed, "running": running}
    if started_ms is not None:
        head["started_at"] = int(started_ms)
    out = [sse("resume", head)]
    out += [tagged(position_of(frames, j), e, d) for j, (e, d) in enumerate(frames)]
    out.append(sse("caught_up", {"id": position_of(frames, len(frames) - 1) if frames else ""}))
    return "".join(out)


def parse_chunk(chunk: str) -> list[tuple[str, str]]:
    """``[(event, data_json)]`` for every frame in ``chunk``; comments (the
    ``: keepalive`` lines) are not frames."""
    out: list[tuple[str, str]] = []
    for block in chunk.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        out.append((event, "\n".join(data)))
    return out


def _frame_data(data: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(data) if isinstance(data, str) else data
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def reply_line(turn_id: str, frames: Any, status: str) -> "dict[str, Any] | None":
    """The chat line a turn's stored frames make (third plan G6): what the
    Chat page would have saved for this turn, so a reply written home reads as
    the one the user would have read. None = the page would save nothing.

    The text is the page's: the ``token`` frames joined, a ``reset`` taking
    them back (all of them, or its ``drop`` characters), the ``done`` frame's
    ``reply`` winning when it has one; never thinking, steps or tool frames.

    * ``done``: that text, or the page's ``(no response)``;
    * ``error``: the words written before it failed, marked ``interrupted``;
      with none, the sentence of what the turn kept (``kept_note``, with its
      documents); with neither, nothing (no empty bubble);
    * ``stopped`` / ``interrupted``: the words written, ``interrupted``; with
      none, nothing.
    Pure; never raises."""
    try:
        acc = ""
        done: "dict[str, Any] | None" = None
        err: "dict[str, Any] | None" = None
        for frame in frames if isinstance(frames, list) else []:
            if not (isinstance(frame, (list, tuple)) and len(frame) == 2):
                continue
            event, data = frame
            if event == "token":
                acc += str(_frame_data(data).get("text") or "")
            elif event == "reset":
                drop = _frame_data(data).get("drop")
                if isinstance(drop, int) and not isinstance(drop, bool) and drop > 0:
                    acc = acc[:-drop] if drop < len(acc) else ""
                else:
                    acc = ""
            elif event == "done":
                done = _frame_data(data)
            elif event == "error":
                err = _frame_data(data)
        base: dict[str, Any] = {"role": "assistant", "turnId": turn_id, "savedBy": "daemon"}
        partial = acc.strip()
        if status == "done":
            text = (str((done or {}).get("reply") or "") or acc).strip()
            return {**base, "content": text or NO_RESPONSE}
        if status == "error":
            if partial:
                return {**base, "content": partial, "interrupted": True}
            kept = str((err or {}).get("kept_note") or "").strip()
            if not kept:
                return None
            line = {**base, "content": kept}
            docs = (err or {}).get("documents")
            if isinstance(docs, list) and docs:
                line["documents"] = [d for d in docs if d]
            return line
        if status in ("stopped", "interrupted") and partial:
            return {**base, "content": partial, "interrupted": True}
        return None
    except Exception:  # noqa: BLE001 — nothing written rather than a guess
        log.warning("could not read turn %s's reply", turn_id, exc_info=True)
        return None


def _set_home(engine: Any, turn_ids: "list[str] | set[str]", *, only_due: bool = True) -> int:
    """Mark records home in one statement. BLOCKING; never raises."""
    ids = sorted({str(t) for t in turn_ids if str(t or "").strip()})
    if not ids:
        return 0
    try:
        from sqlalchemy import update

        stmt = update(ChatTurnRecord).where(ChatTurnRecord.id.in_(ids))  # type: ignore[attr-defined]
        if only_due:
            stmt = stmt.where(ChatTurnRecord.home == HOME_DUE)
        with session_scope(engine) as db:
            res = db.exec(stmt.values(home=HOME_DONE))  # type: ignore[call-overload]
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001
        log.warning("could not mark chat turns as saved to their chat", exc_info=True)
        return 0


#: What :func:`write_home` answers, and which answers mean the record is done.
HOME_WRITTEN, HOME_THERE, HOME_NOTHING, HOME_NOT_OURS = "written", "there", "nothing", "not ours"
HOME_NO_CHAT, HOME_FAILED = "no chat", "failed"
_SETTLED = frozenset({HOME_WRITTEN, HOME_THERE, HOME_NOTHING, HOME_NOT_OURS})


def write_home(engine: Any, turn_id: str, thread_id: str, frames: Any, status: str, *,
               event_bus: Any = None) -> str:
    """Write turn ``turn_id``'s reply into its chat ``thread_id`` unless the
    chat already holds a reply for that turn (third plan G6). Returns one of
    "written", "there" (the page saved it), "nothing" (nothing to write),
    "not ours" (a messaging or coding-session thread: its own writer),
    "no chat" (none named, or it is gone) or "failed"; on the first four the
    record is marked home. BLOCKING (the shared conversation lock and one
    write): call it off the loop. Never raises."""
    thread = str(thread_id or "").strip()
    if not thread:
        return HOME_NO_CHAT
    line = reply_line(turn_id, frames, status)
    if line is None:
        outcome = HOME_NOTHING
    else:
        from .chat_append import REPLY_WHO, append_to_chat_thread

        res = append_to_chat_thread(
            engine, thread, [line], event_bus=event_bus, who=REPLY_WHO,
            event_extra={"turn_id": turn_id},
        )
        if res.get("ok"):
            outcome = HOME_WRITTEN if res.get("appended") else HOME_THERE
        elif res.get("refused"):
            outcome = HOME_NOT_OURS
        elif res.get("why") == "no such chat":
            outcome = HOME_NO_CHAT
        else:
            outcome = HOME_FAILED
    if outcome in _SETTLED:
        _set_home(engine, [turn_id])
    return outcome


def bring_home_record(engine: Any, turn_id: str, *, event_bus: Any = None) -> str:
    """:func:`write_home` for a turn this process no longer holds, from its
    record — only a record the current code marked "due" and that finished.
    BLOCKING; never raises ("" when there was nothing to do)."""
    try:
        with session_scope(engine) as db:
            row = db.get(ChatTurnRecord, turn_id)
            if row is None or row.home != HOME_DUE or row.status == "running":
                return ""
            thread, status, raw = row.thread_id, row.status, row.frames_json
        try:
            frames = json.loads(raw or "[]")
        except ValueError:
            frames = []
        return write_home(engine, turn_id, thread, frames, status, event_bus=event_bus)
    except Exception:  # noqa: BLE001
        log.warning("could not bring chat turn %s's reply home", turn_id, exc_info=True)
        return HOME_FAILED


def mark_home(engine: Any, turn_ids: "set[str] | list[str]") -> int:
    """A page saved replies for these turns into their chat (``PUT
    /chat/threads/{id}``): their records are home, in the database and in
    this process — none is written home again, whatever the chat's array
    looks like later (a Continue that merges a reply into the next keeps only
    the newest turn's name). BLOCKING; never raises."""
    ids = {str(t) for t in turn_ids if str(t or "").strip()}
    if not ids:
        return 0
    for turn in ids:
        try:
            lg = _LIVE.get(turn)
            if lg is not None:
                lg.home = HOME_DONE
        except Exception:  # noqa: BLE001
            pass
    return _set_home(engine, ids)


def stamp_usage_rows(engine: Any, run_ids: "list[str]", thread_id: str) -> int:
    """CS6: the usage rows a turn wrote before its chat was named now name it
    (only rows that name none). BLOCKING; never raises."""
    ids = sorted({str(r) for r in run_ids if str(r or "").strip()})
    thread = str(thread_id or "").strip()[:80]
    if not ids or not thread:
        return 0
    try:
        from sqlalchemy import update

        from ..core.models import AgentRun

        with session_scope(engine) as db:
            res = db.exec(  # type: ignore[call-overload]
                update(AgentRun)
                .where(AgentRun.id.in_(ids))  # type: ignore[attr-defined]
                .where(AgentRun.thread_id.is_(None))  # type: ignore[union-attr]
                .values(thread_id=thread)
            )
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001 — accounting never breaks a link
        log.warning("could not name the chat on a turn's usage", exc_info=True)
        return 0


def current_turn() -> "tuple[str, str]":
    """``(turn id, chat id)`` of the daemon-owned chat turn the code running
    now belongs to (``core.turn_ref``), ``("", "")`` outside one or once it
    ended. The chat id is the LIVE one: a new chat's first turn learns it from
    the link. Never raises."""
    try:
        from ..core.turn_ref import CURRENT

        ref = CURRENT.get()
        if ref is None or not ref.turn_id or not ref.running():
            return "", ""
        return str(ref.turn_id), str(ref.thread_id() or "")
    except Exception:  # noqa: BLE001
        return "", ""


def note_usage_row(turn_id: str, run_id: str) -> None:
    """CS6: turn *turn_id* wrote usage row *run_id* before its chat was
    named; the link names it (:func:`stamp_usage_rows`). Never raises."""
    try:
        lg = _LIVE.get(str(turn_id or ""))
        if lg is not None and run_id and len(lg.usage_rows) < 50:
            lg.usage_rows.append(str(run_id))
    except Exception:  # noqa: BLE001
        pass


class TurnLog:
    """The frames of one daemon-owned turn, and the windows following it."""

    def __init__(self, turn_id: str, thread_id: str = "") -> None:
        self.turn_id = turn_id
        self.thread_id = thread_id
        #: v1.367.0 (Phase 4 review): "build" for an IJ harness session's
        #: turn (set by ``routes/build``), "" for a chat's — the waiting row
        #: below says which, so a surface that opens it can tell a Build
        #: session from a chat (Phase 6 routes it to Build).
        self.surface = ""
        self._frames: list[list[str]] = []  # [event, data_json], merged
        #: v1.418.0 (C3.6): the LAST frame's escaped text chunks while it is a
        #: canonical text frame still growing (None otherwise), how many of
        #: them :attr:`frames` already joined into it, and its decoded length.
        self._live: "list[str] | None" = None
        self._live_joined = 0
        self._live_len = 0
        self.status = "running"
        self.started_at = utcnow()
        self._chars = 0
        #: v1.384.0: (index, text length) of the last stored frame (see
        #: :meth:`position`).
        self._tail: "tuple[int, int] | None" = None
        self._followers: set[asyncio.Queue] = set()
        self._last_seen = time.monotonic()
        self._dirty = False
        self._last_persist = 0.0
        self._persisting: "asyncio.Task | None" = None
        #: v1.391.0: the pending end-of-interval write (see :meth:`maybe_persist`).
        self._flush_handle: "asyncio.TimerHandle | None" = None
        self.task: "asyncio.Task | None" = None
        self.finished_at: float | None = None
        #: approval id -> (tool, asked at ISO) for every approval card this turn
        #: has shown and not yet seen answered (v1.351.0, G05). NAMES ONLY —
        #: never the card's arguments: :func:`waiting_unwatched` lists these
        #: to every page and to the desktop.
        self._asks: dict[str, tuple[str, str]] = {}
        #: Readiness 12B (G05): follower queue -> the VIEWER it belongs to (a
        #: chat page names itself with ``?viewer=``; "" for every other
        #: follower), and the viewers that said they left (:meth:`mark_away`).
        #: A chat page the user navigated away from in the SAME window keeps
        #: following (its fetch is not ended — the reply still lands and is
        #: saved), but nobody is looking at its card any more.
        self._viewers: dict[asyncio.Queue, str] = {}
        self._away: set[str] = set()
        #: Readiness 0j.5 item 17: the other cards this turn waits on (an
        #: app's question, a model request, the model's own question) — ids
        #: only. Beside ``_asks``: a turn parked on any card is never stopped
        #: for being unwatched (:func:`stop_predicate`).
        self._cards: set[str] = set()
        #: WHY the turn was stopped by the daemon itself (the orphan guard),
        #: "" otherwise; ``TurnLogs._drive`` writes it as the turn's error.
        self.stop_why: str = ""
        #: v1.455.0: the database this turn's chat lives in (set by
        #: :meth:`TurnLogs.start`; read by :func:`turn_chat`).
        self.engine: Any = None
        #: v1.455.0 review: what to call when the turn ends (:func:`after_turn`)
        #: — ``(status, looked_at)`` — and, once it has, that pair. A worker
        #: thread registers while the loop ends the turn, so both sides go
        #: through ``_end_lock``.
        self._end_hooks: list[Any] = []
        self._end_lock = _threading.Lock()
        self.ended: "tuple[str, bool] | None" = None
        #: Third plan G6: has the reply reached its chat (see the module's
        #: "THE REPLY'S HOME")? Kept on the record from its first write, so a
        #: record a restart left behind is known to be this code's.
        self.home = HOME_DUE
        #: Was a window following the turn as it ended (its page saves the
        #: reply itself; the daemon then waits :data:`HOME_GRACE_S`)?
        self.followed_at_end = False
        #: CS7: the turn's chat was deleted — its record goes when it ends.
        self.chat_deleted = False
        #: CS6: usage rows this turn wrote before its chat was named.
        self.usage_rows: list[str] = []
        #: Third plan G19: the turn registry's handle of this turn (set by
        #: :meth:`TurnLogs.start`) — a Stop from ANY path (the route, Stop
        #: and archive, the night, an attempt) is read off it — and the time
        #: the stop route wrote on the record (:meth:`ask_stop`).
        self.handle: Any = None
        self._stop_asked_at: Any = None
        #: Fourth plan G27 (CS6): who asked the stop the route marked.
        self._stop_author: str = ""

    # -- Stop is written down (third plan G19) -------------------------------
    def ask_stop(self, when: Any, by: str = "") -> None:
        """A stop was asked for this turn at *when*, by *by* (``core.turns.
        STOP_AUTHORS``; "" = not recorded, never "you" by default): every
        later write of the record carries both (the first press wins; never
        cleared)."""
        if self._stop_asked_at is None and when is not None:
            from ..core.turns import stop_author

            self._stop_asked_at = when
            self._stop_author = stop_author(by)

    def _stop_mark(self) -> "tuple[Any, str] | None":
        """``(when, who)`` of the FIRST stop asked for this turn — the
        route's mark or the registry handle's own stop, whichever came first
        (fourth plan G27: a Stop pressed after Iron Jarvis's own stop never
        rewrites who stopped it) — or None. In memory: no database read."""
        marks: list[tuple[Any, str]] = []
        if self._stop_asked_at is not None:
            marks.append((self._stop_asked_at, self._stop_author))
        handle = self.handle
        if handle is not None and getattr(handle, "stopped", False):
            marks.append((
                getattr(handle, "stopped_at", None) or utcnow(),
                str(getattr(handle, "stop_author", "") or ""),
            ))
        if not marks:
            return None
        try:
            return min(marks, key=lambda m: m[0])
        except TypeError:  # two clocks that cannot be compared: the route's
            return marks[0]

    @property
    def stop_asked_at(self) -> Any:
        """When a stop was asked for this turn (the route's mark, else the
        registry handle's own stop time — the earlier of the two), or None.
        In memory: the live paths need no database read."""
        mark = self._stop_mark()
        return mark[0] if mark is not None else None

    @property
    def stop_author(self) -> str:
        """Who asked that stop (``core.turns.STOP_AUTHORS``); "" when not
        stopped or not recorded (fourth plan G27, CS6)."""
        mark = self._stop_mark()
        return mark[1] if mark is not None else ""

    @property
    def stop_asked(self) -> bool:
        """Was this turn asked to stop (by any Stop, not the orphan guard)?"""
        return self.stop_asked_at is not None

    # -- the stored frames ---------------------------------------------------
    @property
    def frames(self) -> list[list[str]]:
        """The stored frames (``[event, data_json]``, merged), the growing
        text frame joined first (v1.418.0, C3.6)."""
        self._settle()
        return self._frames

    @frames.setter
    def frames(self, value: list[list[str]]) -> None:
        self._frames = value
        self._live = None
        self._live_joined = 0
        self._live_len = 0
        if value:
            self._track(value[-1][0], value[-1][1])

    def _settle(self) -> None:
        live = self._live
        if live is not None and self._live_joined != len(live):
            joined = "".join(live)
            live[:] = [joined]
            self._live_joined = 1
            self._frames[-1][1] = _TEXT_HEAD + joined + _TEXT_TAIL

    def _track(self, event: str, data: str) -> None:
        """Start keeping the last frame as chunks when it is a canonical text
        frame (else stop). O(frame) — only when a frame is appended or taken
        the old way, never per token."""
        esc = _escaped_text(data) if event in _MERGEABLE else None
        length = _escaped_len(esc) if esc is not None else -1
        if esc is None or length < 0:
            self._live = None
            self._live_joined = 0
            self._live_len = 0
            return
        self._live = [esc]
        self._live_joined = 1
        self._live_len = length

    # -- writing --------------------------------------------------------------
    def add(self, chunk: str) -> None:
        # Stored FIRST (v1.384.0): each frame's position is the store's state
        # once it landed, and a tagged follower is sent exactly that. A plain
        # follower still gets the chunk as it came (comments included).
        parts: list[str] = []
        for event, data in parse_chunk(chunk):
            self._note_ask(event, data)
            self._store(event, data)
            parts.append(tagged(self.position(), event, data))
        item = (chunk, "".join(parts))
        for q in list(self._followers):
            q.put_nowait(item)

    def position(self) -> str:
        """Where the store stands now (the last frame's position; "" when
        nothing is stored yet). The last frame's text length is kept as it is
        stored (``_tail``), so a long reply's thousands of deltas never parse
        its growing text again just to say where they landed."""
        if not self._frames:
            return ""
        last = len(self._frames) - 1
        if self._tail is not None and self._tail[0] == last:
            return f"{last}:{self._tail[1]}"
        return position_of(self.frames, last)

    def frames_after(self, after: str) -> "list[tuple[str, str, str]] | None":
        """``[(position, event, data)]`` — everything stored after resume
        position ``after`` (see the module doc): the rest of frame ``i``'s
        text past character ``c`` when it is a token/thinking frame that grew,
        then every later frame. None when this log does not hold that position
        (malformed, past the end, a text frame shorter than ``c``, a frame with
        no text given a ``c``) — the caller then sends the whole turn."""
        try:
            i_s, c_s = str(after or "").split(":", 1)
            i, c = int(i_s), int(c_s)
        except (TypeError, ValueError):
            return None
        if i < 0 or c < 0 or i >= len(self.frames):
            return None
        event, data = self.frames[i]
        out: list[tuple[str, str, str]] = []
        if event in _MERGEABLE:
            try:
                parsed = json.loads(data)
            except ValueError:
                return None
            if not isinstance(parsed, dict):
                return None
            text = str(parsed.get("text") or "")
            if c > len(text):
                return None
            if c < len(text):
                out.append((f"{i}:{len(text)}", event, json.dumps({**parsed, "text": text[c:]})))
        elif c != 0:
            return None
        for j in range(i + 1, len(self.frames)):
            e, d = self.frames[j]
            out.append((position_of(self.frames, j), e, d))
        return out

    def parked_on_a_card(self) -> bool:
        """Is this turn waiting on a card right now (an approval, an app's
        question, a model request, the model's own question)? Readiness 0j.5
        item 17: such a turn waits for its person however long that takes."""
        return bool(self._asks) or bool(self._cards)

    def _note_card(self, event: str, data: str) -> None:
        if event not in _CARD_OPEN_EVENTS and event != _CARD_CLOSE_EVENT:
            return
        try:
            frame = json.loads(data)
        except ValueError:
            return
        cid = frame.get("id") if isinstance(frame, dict) else None
        if not isinstance(cid, str) or not cid:
            return
        if event in _CARD_OPEN_EVENTS:
            self._cards.add(cid)
        else:
            self._cards.discard(cid)
            self._card_closed()

    def _card_closed(self) -> None:
        """A card closed: the time the turn spent parked on it was not time
        it worked unwatched, so the orphan clock starts again now."""
        if not self._followers and not self.parked_on_a_card():
            self._last_seen = time.monotonic()

    def _note_ask(self, event: str, data: str) -> None:
        """Keep the open approval cards' ids and tool names (v1.351.0, G05)."""
        self._note_card(event, data)
        if event not in ("approval", "approval_resolved"):
            return
        try:
            frame = json.loads(data)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        aid = frame.get("id")
        if not isinstance(aid, str) or not aid:
            return
        if event == "approval":
            self._asks[aid] = (str(frame.get("tool") or ""), _iso_utc(utcnow()) or "")
        else:
            self._asks.pop(aid, None)
            self._card_closed()

    def open_asks(self, pending: "set[str] | frozenset[str]") -> list[tuple[str, str, str]]:
        """``[(approval id, tool, asked at)]`` for this turn's cards that are
        still waiting — the shared registry (``pending``) is the truth: a card
        it no longer holds was answered, timed out or abandoned.

        SNAPSHOT first (harness gaps review): ``GET /chat/turns/running`` is a
        SYNC route and reads this from a worker thread while the loop adds a
        card (``_note_ask``). The membership check between two items is
        Python code, where a thread switch lands — walking the live dict there
        raised "dictionary changed size during iteration" and the listing came
        back empty. ``list()`` copies the items in C, with no Python code
        between them."""
        asks = list(self._asks.items())
        return [(aid, tool, at) for aid, (tool, at) in asks if aid in pending]

    def _store(self, event: str, data: str) -> None:
        frames = self._frames
        # v1.418.0 (C3.6): a canonical token joining a canonical frame of its
        # kind is one more chunk — no parse, no serialisation (see the module
        # doc). Exactly the bytes, length and cap of the old path below.
        if self._live is not None and event in _MERGEABLE and frames and frames[-1][0] == event:
            esc = _escaped_text(data)
            more = _escaped_len(esc) if esc is not None else -1
            if esc is not None and more >= 0:
                if self._chars + more > MAX_STORED_CHARS:
                    return
                self._live.append(esc)
                self._live_len += more
                self._chars += more
                self._dirty = True
                self._tail = (len(frames) - 1, self._live_len)
                return
        self._settle()
        if event in _MERGEABLE and self.frames and self.frames[-1][0] == event:
            try:
                prev = json.loads(self.frames[-1][1])
                more = json.loads(data)
                if self._chars + len(more.get("text", "")) > MAX_STORED_CHARS:
                    return
                prev["text"] = (prev.get("text") or "") + (more.get("text") or "")
                self.frames[-1][1] = json.dumps(prev)
                self._chars += len(more.get("text") or "")
                self._dirty = True
                self._tail = (len(self.frames) - 1, len(prev["text"]))
                self._track(event, self._frames[-1][1])
                return
            except (ValueError, AttributeError, TypeError):
                pass
        if self._chars + len(data) > MAX_STORED_CHARS and event in _MERGEABLE:
            return
        self._frames.append([event, data])
        self._chars += len(data)
        self._dirty = True
        self._track(event, data)
        self._tail = (
            len(self._frames) - 1,
            self._live_len if self._live is not None else _text_len(event, data),
        )

    def last_event(self) -> str:
        return self._frames[-1][0] if self._frames else ""

    # -- following ------------------------------------------------------------
    def watched(self) -> bool:
        return bool(self._followers)

    def looked_at(self) -> bool:
        """Is a window SHOWING this turn (readiness 12B, G05)? A follower that
        named no viewer (the Build pane, the sidebar, an older page) counts as
        looking, exactly as :meth:`watched` did; a chat page that said it left
        (:meth:`mark_away`) does not. :meth:`watched` keeps its meaning — the
        orphan guard reads it, and a page still following is no orphan."""
        # SNAPSHOT first (the v1.367.0 rule above ``TurnLogs.running``): a
        # reader on a worker thread must never meet the loop's insert.
        viewers = dict(self._viewers)
        away = set(self._away)
        return any(not viewers.get(q) or viewers[q] not in away for q in list(self._followers))

    def mark_away(self, viewer: str, back: bool = False) -> bool:
        """The chat page *viewer* is no longer on screen (the user went to
        another page of the same window); True when it is following this
        turn. Kept until that follower ends (a new page is a new viewer).

        v1.455.0 review: also while its WINDOW is hidden (minimised, covered,
        the screen locked) — nobody is looking, so a failed turn's phone line
        is not held back for it. ``back``: that window shows the page again."""
        v = str(viewer or "").strip()[:80]
        if not v or v not in self._viewers.values():
            return False
        if back:
            self._away.discard(v)
        else:
            self._away.add(v)
        return True

    def unwatched_for(self) -> float:
        if self._followers:
            return 0.0
        return time.monotonic() - self._last_seen

    def snapshot(self) -> str:
        return "".join(sse(e, d) for e, d in self.frames)

    def tagged_head(self, after: "str | None") -> str:
        """The tagged answer's stored part (v1.384.0): ``resume``, what is
        stored after ``after`` (or the whole turn when that position is not
        held), ``caught_up``."""
        running = self.status == "running"
        tail = self.frames_after(after) if after else None
        if tail is None:
            started = int(self.started_at.timestamp() * 1000) if running else None
            return tagged_replay(self.frames, resumed=False, running=running, started_ms=started)
        out = [sse("resume", {"resumed": True, "running": running})]
        out += [tagged(p, e, d) for p, e, d in tail]
        out.append(sse("caught_up", {"id": self.position()}))
        return "".join(out)

    async def follow(
        self, *, with_ids: bool = False, after: "str | None" = None, viewer: str = "",
    ) -> AsyncIterator[str]:
        """The turn so far as ONE chunk, then each new chunk, until it ends.
        Snapshot and subscription happen in one step (no await between), so
        nothing is missed or sent twice.

        ``with_ids`` (v1.384.0): every frame tagged with its resume position,
        the stored part between ``resume`` and ``caught_up``, and — with
        ``after`` — only what came after that position (see the module doc).
        False: byte-for-byte what it always sent.

        ``viewer`` (readiness 12B, G05): the chat page following, by the id it
        named itself with — :meth:`mark_away` / :meth:`looked_at`. "" (every
        other follower): it counts as looking while it follows."""
        q: asyncio.Queue = asyncio.Queue()
        head = self.tagged_head(after) if with_ids else self.snapshot()
        running = self.status == "running"
        if running:
            self._followers.add(q)
            v = str(viewer or "").strip()[:80]
            if v:
                self._viewers[q] = v
        try:
            if head:
                yield head
            if not running:
                return
            while True:
                item = await q.get()
                if item is _END:
                    return
                raw, ids = item
                out = ids if with_ids else raw
                if out:
                    yield out
        finally:
            self._followers.discard(q)
            gone = self._viewers.pop(q, "")
            if gone and gone not in self._viewers.values():
                self._away.discard(gone)
            self._last_seen = time.monotonic()

    def end(self, status: str, looked_at: bool) -> list[Any]:
        """The turn is over (v1.455.0 review): record *status* and whether a
        window was showing it at its end, and hand back the hooks
        :func:`after_turn` registered (each to be called with that pair)."""
        with self._end_lock:
            self.status = status
            self.ended = (status, bool(looked_at))
            hooks, self._end_hooks = self._end_hooks, []
        return hooks

    def on_end(self, hook: Any) -> bool:
        """Call *hook* ``(status, looked_at)`` when this turn ends — at once,
        on this thread, when it already has. Returns whether it was called
        now. Never raises."""
        with self._end_lock:
            ended = self.ended
            if ended is None:
                self._end_hooks.append(hook)
                return False
        try:
            hook(*ended)
        except Exception:  # noqa: BLE001 — a hook's fault is its own
            log.debug("a turn's end hook failed", exc_info=True)
        return True

    def _end_followers(self) -> None:
        for q in list(self._followers):
            q.put_nowait(_END)

    # -- the record -----------------------------------------------------------
    def _row_values(self) -> dict[str, Any]:
        """The record's values. v1.418.0 (C3.6): the frames as a SNAPSHOT
        (a copy of each pair, taken on the loop — a token added while the
        write runs is in the next one, never half in this one); the
        ``json.dumps`` of them runs in :func:`_write_record`, on the worker
        thread that writes it."""
        values = {
            "thread_id": self.thread_id,
            "status": self.status,
            "frames_json": [[e, d] for e, d in self.frames],
            "updated_at": utcnow(),
            "home": self.home,
        }
        # Third plan G19: the stop mark rides every write once asked (and
        # :func:`_write_record` never clears one already on the record).
        # Fourth plan G27 (CS6): and who asked it, beside it.
        mark = self._stop_mark()
        if mark is not None:
            values["stop_asked_at"] = mark[0]
            values["stop_author"] = mark[1]
        return values

    def persist_interval(self) -> float:
        """Seconds between two writes of the running record (v1.384.0 review:
        it grows with the record — see :data:`PERSIST_BYTES_PER_S`)."""
        return max(PERSIST_EVERY_S, self._chars / PERSIST_BYTES_PER_S)

    def maybe_persist(self, engine: Any) -> None:
        if self.chat_deleted:
            # Fourth plan G27 (DT5): the chat is gone — nothing of this turn
            # is written any more (its end, :meth:`persist_now`, deletes the
            # record instead, after any write already in flight).
            return
        now = time.monotonic()
        if not self._dirty:
            return
        wait = self.persist_interval() - (now - self._last_persist)
        if wait > 0 or (self._persisting is not None and not self._persisting.done()):
            # v1.391.0: the frames that arrived inside the interval are written
            # when it ends even if NO later frame comes — a reply that goes
            # quiet (the model thinking for minutes, a long command) used to
            # keep its last burst of steps in memory only, and a crash then
            # lost them from the record the boot rebuilds the reply from.
            self._flush_later(engine, max(wait, 0.25))
            return
        self._last_persist = now
        self._dirty = False
        values = self._row_values()
        self._persisting = asyncio.ensure_future(
            asyncio.to_thread(_write_record, engine, self.turn_id, self.started_at, values)
        )

    def _flush_later(self, engine: Any, delay: float) -> None:
        """One pending write of the record at the end of the interval (none
        when one is already pending, or no loop runs)."""
        if self._flush_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _fire() -> None:
            self._flush_handle = None
            if self.status == "running":
                self.maybe_persist(engine)

        self._flush_handle = loop.call_later(delay, _fire)

    async def persist_now(self, engine: Any) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        if self._persisting is not None:
            with contextlib.suppress(Exception):
                await self._persisting
        self._dirty = False
        if self.chat_deleted:
            # Fourth plan G27 (DT5): a deleted chat's turn leaves no record —
            # on a normal end and on a shutdown's cut alike (the driver's
            # ``finally`` calls this). The write awaited above may have
            # re-created the row the delete removed: it goes here.
            await asyncio.to_thread(forget_records, engine, [self.turn_id])
            return
        await asyncio.to_thread(
            _write_record, engine, self.turn_id, self.started_at, self._row_values()
        )


def _write_record(engine: Any, turn_id: str, started_at: Any, values: dict[str, Any]) -> None:
    try:
        if not isinstance(values.get("frames_json", ""), str):
            # v1.418.0 (C3.6): serialised HERE, off the loop.
            values = {**values, "frames_json": json.dumps(values["frames_json"])}
        from sqlalchemy.exc import IntegrityError

        # Third plan G19: twice at most — the stop mark (:func:`write_stop_mark`)
        # may create the row between this write's read and its insert.
        for attempt in (1, 2):
            with session_scope(engine) as db:
                row = db.get(ChatTurnRecord, turn_id)
                if row is None:
                    row = ChatTurnRecord(id=turn_id, started_at=started_at)
                else:
                    # A row the stop mark made first starts when the turn did.
                    row.started_at = started_at
                marked = row.stop_asked_at is not None
                for k, v in values.items():
                    if k in ("stop_asked_at", "stop_author") and (v is None or marked):
                        # Third plan G19: never cleared, first press kept —
                        # fourth plan G27: and who asked it stays with it.
                        continue
                    setattr(row, k, v)
                db.add(row)
                try:
                    db.commit()
                    return
                except IntegrityError:
                    db.rollback()
                    if attempt == 2:
                        raise
    except Exception:  # noqa: BLE001 — the live turn never fails on its record
        log.warning("could not save chat turn %s", turn_id, exc_info=True)


# --------------------------------------------------------------------------- #
# STOP IS WRITTEN DOWN (third plan G19: RB1, CS4). A Stop used to be a flag in
# memory only (``core/turns``): a restart in the seconds after it lost it, and
# the coding resume pass then carried the stopped reply on by itself. The stop
# route now marks the live log, stops the turn in memory AT ONCE (fourth plan
# G27, DT6: a slow disk never lets another step start), then writes WHEN the
# stop was asked, and WHO asked it, on the turn's record (:func:`record_stop`)
# before it answers; the live log carries both on every later write
# (``TurnLog.stop_asked``), and :func:`stop_asked` / :func:`stop_fact` are
# what every carry-on reads.
# --------------------------------------------------------------------------- #
#: The longest the stop route waits for the mark to be written before it
#: answers anyway (the write goes on in its thread; the turn is stopped).
STOP_MARK_TIMEOUT_S = 3.0
#: The stop route's 404 words (with ``"finished": true``): the reply had
#: already finished, so nothing was stopped. The Chat page reads the flag and
#: shows the finished reply instead of "Stopped.".
STOP_FINISHED_DETAIL = "That reply had already finished."


def write_stop_mark(engine: Any, turn_id: str, when: Any, by: str = "") -> bool:
    """Write *when* as turn ``turn_id``'s ``stop_asked_at``, and *by* (who
    asked it: ``core.turns.STOP_AUTHORS``; "" = not recorded, never "you" by
    default) as its ``stop_author``, in one guarded update — the first press
    is kept, a later one changes nothing. A record that does not exist yet
    (the turn has not written its first) is CREATED with the mark, so there
    is no moment in which the stop is in memory only; the turn's own first
    write then fills it in. True when the record carries a mark. BLOCKING;
    never raises."""
    tid = str(turn_id or "").strip()
    if not tid:
        return False
    try:
        from sqlalchemy import update
        from sqlalchemy.exc import IntegrityError

        from ..core.turns import stop_author

        who = stop_author(by)
        for _attempt in (1, 2):
            with session_scope(engine) as db:
                res = db.exec(  # type: ignore[call-overload]
                    update(ChatTurnRecord)
                    .where(ChatTurnRecord.id == tid)  # type: ignore[arg-type]
                    .where(ChatTurnRecord.stop_asked_at.is_(None))  # type: ignore[union-attr]
                    .values(stop_asked_at=when, stop_author=who)
                )
                db.commit()
                if int(getattr(res, "rowcount", 0) or 0):
                    return True
                if db.get(ChatTurnRecord, tid) is not None:
                    return True  # already marked by an earlier press
                db.add(ChatTurnRecord(id=tid, started_at=when, stop_asked_at=when, stop_author=who))
                try:
                    db.commit()
                    return True
                except IntegrityError:
                    db.rollback()  # the turn's own first write won: mark it
        return False
    except Exception:  # noqa: BLE001 — the stop still happens (the caller logs)
        log.warning("could not write the stop of chat turn %s", tid, exc_info=True)
        return False


async def record_stop(engine: Any, turn_id: str, when: Any, by: str = "") -> bool:
    """:func:`write_stop_mark` off the event loop, SHIELDED (a shutdown in
    that instant does not drop the write) and bounded by
    :data:`STOP_MARK_TIMEOUT_S`. False (logged) when it failed or took too
    long — the turn is stopped either way. Never raises except a cancel of
    the caller itself."""
    try:
        return bool(await asyncio.wait_for(
            asyncio.shield(asyncio.to_thread(write_stop_mark, engine, turn_id, when, by)),
            timeout=STOP_MARK_TIMEOUT_S,
        ))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a slow disk never holds a Stop
        log.warning("the stop of chat turn %s was not written in time; it stops anyway",
                    turn_id, exc_info=True)
        return False


def stop_fact(engine: Any, turn_id: str) -> "tuple[Any, str] | None":
    """``(when, who)`` of the stop asked for turn ``turn_id`` — who is one
    of ``core.turns.STOP_AUTHORS`` ("" = not recorded, e.g. a record older
    than the column) — or None when it was never asked to stop (or the id is
    unknown). The live turn this process holds answers from memory;
    otherwise its record (fourth plan G27, CS6/DT4). BLOCKING (one
    primary-key read of two columns); never raises (unreadable = None)."""
    tid = str(turn_id or "").strip()
    if not tid:
        return None
    try:
        lg = _LIVE.get(tid)
        if lg is not None:
            mark = lg._stop_mark()
            if mark is not None:
                return mark
    except Exception:  # noqa: BLE001
        pass
    try:
        from sqlmodel import select

        with session_scope(engine) as db:
            got = db.exec(
                select(ChatTurnRecord.stop_asked_at, ChatTurnRecord.stop_author)
                .where(ChatTurnRecord.id == tid)
            ).first()
        if got is None or got[0] is None:
            return None
        from ..core.turns import stop_author

        return got[0], stop_author(got[1])
    except Exception:  # noqa: BLE001 — unknown reads as "not asked"
        log.warning("could not read whether chat turn %s was stopped", tid, exc_info=True)
        return None


def stop_asked(engine: Any, turn_id: str) -> bool:
    """Was turn ``turn_id`` asked to stop — by ANYONE (decision G9: no
    stopped reply is ever carried on, whoever stopped it)? The live turn this
    process holds answers from memory (``TurnLog.stop_asked``); otherwise its
    record's ``stop_asked_at`` (:func:`stop_fact`). False for an unknown id
    or a turn never stopped. BLOCKING; never raises."""
    return stop_fact(engine, turn_id) is not None


def record_exists(engine: Any, turn_id: str) -> bool:
    """Does turn ``turn_id`` have a record (one primary-key read)? BLOCKING;
    never raises (False when unreadable)."""
    try:
        from sqlmodel import select

        with session_scope(engine) as db:
            return db.exec(
                select(ChatTurnRecord.id).where(ChatTurnRecord.id == str(turn_id))
            ).first() is not None
    except Exception:  # noqa: BLE001
        log.warning("could not read chat turn %s's record", turn_id, exc_info=True)
        return False


class TurnLogs:
    """Every daemon-owned turn this process holds (one per platform)."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self._logs: dict[str, TurnLog] = {}
        #: chat ids linked before their turn started (bounded).
        self._links: dict[str, str] = {}
        #: Third plan G6: turn id -> the pending end-of-grace write home (a
        #: loop timer, never a sleeping task: nothing waits on it, and a
        #: closed loop drops it), and the writes it started.
        self._home_timers: dict[str, asyncio.TimerHandle] = {}
        self._home_tasks: set[asyncio.Task] = set()
        try:
            _REGISTRIES.add(self)
        except Exception:  # noqa: BLE001
            pass

    def link_later(self, turn_id: str, thread_id: str) -> None:
        if len(self._links) > 200:
            self._links.pop(next(iter(self._links)))
        self._links[turn_id] = thread_id

    def get(self, turn_id: str) -> TurnLog | None:
        self._evict()
        return self._logs.get(turn_id)

    def thread_for(self, turn_id: str) -> str:
        """The chat ``turn_id`` belongs to as far as THIS process knows — the
        live turn's link, else a link waiting for the turn to start; "" when
        neither (the turn's record may still say; v1.347.0, read by a media
        job that has to find the chat a new chat's first turn ended up in)."""
        lg = self._logs.get(turn_id)
        if lg is not None and lg.thread_id:
            return lg.thread_id
        return self._links.get(turn_id, "")

    def mark_away(self, turn_id: str, viewer: str, back: bool = False) -> "bool | None":
        """Readiness 12B (G05): the chat page *viewer* left the screen while
        following ``turn_id`` (``back``: v1.455.0 review, its window shows it
        again). None when no such turn is RUNNING here; else whether that
        viewer was following it (:meth:`TurnLog.mark_away`)."""
        lg = self._logs.get(turn_id)
        if lg is None or lg.status != "running":
            return None
        return lg.mark_away(viewer, back=back)

    def running(self) -> list[TurnLog]:
        # SNAPSHOT first (v1.367.0 review): Build's sync routes read this from
        # FastAPI's threadpool while the loop starts turns; a comprehension
        # straight over the dict can meet an insert between two items
        # ("dictionary changed size during iteration"). ``list()`` copies the
        # values in C, with no Python code between items to switch threads.
        return [lg for lg in list(self._logs.values()) if lg.status == "running"]

    def _evict(self) -> None:
        now = time.monotonic()
        for tid, lg in list(self._logs.items()):
            if lg.finished_at is not None and now - lg.finished_at > FINISHED_KEEP_S:
                self._logs.pop(tid, None)

    def start(self, turn_id: str, thread_id: str, frames: AsyncIterator[str]) -> TurnLog:
        """Run ``frames`` (the lane's SSE iterator) as its own task."""
        lg = TurnLog(turn_id, thread_id or self._links.pop(turn_id, ""))
        lg.engine = self.engine
        # Third plan G19: the turn's stop handle (registered by the lane
        # before this start), so a Stop from any path rides its record —
        # an early Stop (kept for an id not yet known) included.
        from ..core.turns import TURNS

        lg.handle = TURNS.handle(turn_id)
        self._logs[turn_id] = lg
        try:
            _LIVE[turn_id] = lg
        except Exception:  # noqa: BLE001 — a turn never fails on its index
            pass
        lg.task = asyncio.ensure_future(self._drive(lg, frames))
        return lg

    async def _drive(self, lg: TurnLog, frames: AsyncIterator[str]) -> None:
        from ..core.turns import CHAT_INFLIGHT
        from ..core.turn_ref import TurnRef, scope as _turn_scope

        status = "stopped"
        CHAT_INFLIGHT.enter()
        # v1.455.0: everything the lane runs (its router calls, the tasks it
        # makes) knows which turn it is in — the router's provider.failed
        # carries it, so the phone alert names the chat and stays quiet while
        # a window shows the turn.
        ref = TurnRef(
            lg.turn_id, lambda: lg.thread_id, lg.looked_at,
            lambda: lg.status == "running",
        )
        try:
            with _turn_scope(ref):
                async with contextlib.aclosing(frames):  # type: ignore[arg-type]
                    async for chunk in frames:
                        lg.add(chunk)
                        lg.maybe_persist(self.engine)
            last = lg.last_event()
            status = "done" if last == "done" else "error" if last == "error" else "stopped"
            if lg.stop_why and last != "done":
                # Readiness 0j.5 item 17: the daemon stopped it (no window had
                # shown it for an hour) — the turn's own error frame says so,
                # and so does its record; it is still a stop, not a failure.
                lg.add(sse("error", {
                    "detail": lg.stop_why,
                    "title": ORPHAN_STOP_TITLE,
                    "fix": ORPHAN_STOP_FIX,
                    "stopped_by": "unwatched",
                }))
                status = "stopped"
        except asyncio.CancelledError:
            # The daemon is shutting down: what was written is kept, and the
            # log says the turn was cut off.
            lg.add(sse("error", {"detail": INTERRUPTED_DETAIL}))
            status = "interrupted"
            raise
        except Exception as exc:  # noqa: BLE001 — the lane yields its own errors
            log.warning("chat turn %s failed outside its stream", lg.turn_id, exc_info=True)
            # Third plan G23 (RB12): plain words live and on the record; the
            # exception is in the log line above (``exc_info``).
            lg.add(sse("error", lane_broke_frame(exc)))
            status = "error"
        finally:
            CHAT_INFLIGHT.exit()
            # v1.455.0 review: whether a window shows the turn as it ends is
            # read BEFORE the followers are told (they leave right after).
            try:
                looked = bool(lg.looked_at())
            except Exception:  # noqa: BLE001
                looked = False
            # Third plan G6: and whether a page follows it (that page saves
            # the reply itself; the daemon then waits before writing it home).
            lg.followed_at_end = lg.watched()
            hooks = lg.end(status, looked)
            lg.finished_at = time.monotonic()
            lg._end_followers()
            _run_end_hooks(hooks, status, looked)
            try:
                await asyncio.shield(lg.persist_now(self.engine))
            except BaseException:  # noqa: BLE001 — shutdown: the boot reconcile covers it
                pass
        # Third plan G6 (CS5): the reply goes into its chat. Outside the
        # ``finally``: a turn a shutdown cut off is written home by the next
        # start (its record is marked).
        await self._after_end(lg, status)
        # v1.420.0 (C6.10): a long CHAT reply that ended says so from the
        # tray. Outside the ``finally``: a cut-off (``CancelledError``) never
        # reaches here, and a stopped turn is not news.
        if not lg.chat_deleted:
            await self._announce_finished(lg, status)

    # -- the reply's home (third plan G6) -------------------------------------
    async def _after_end(self, lg: TurnLog, status: str) -> None:
        """Where the ended turn's reply goes: nowhere for a coding session's
        (its own writer); its record goes when its chat was deleted (CS7);
        nothing yet with no chat (the link brings it home); after the grace
        when a page followed it; else at once. Never raises."""
        try:
            if _surface_of(lg) == "build":
                return
            if not str(lg.thread_id or "").strip():
                return
            if lg.followed_at_end:
                self._home_later(lg)
            else:
                await self._home_now(lg)  # writes nothing for a deleted chat
            if lg.chat_deleted:  # deleted while it ran, or as its end was written
                await self._forget_ended(lg)
        except Exception:  # noqa: BLE001 — a reply's home never fails its turn
            log.warning("chat turn %s: writing its reply into its chat failed", lg.turn_id,
                        exc_info=True)

    async def _forget_ended(self, lg: TurnLog) -> None:
        self._logs.pop(lg.turn_id, None)
        with contextlib.suppress(Exception):
            _LIVE.pop(lg.turn_id, None)
        await asyncio.shield(asyncio.to_thread(forget_records, self.engine, [lg.turn_id]))

    async def _home_now(self, lg: TurnLog) -> str:
        """Write the reply home now, off the loop, shielded (a shutdown in
        that instant does not lose it) and bounded. Never raises."""
        timer = self._home_timers.pop(lg.turn_id, None)
        if timer is not None:
            timer.cancel()
        if lg.home != HOME_DUE or lg.chat_deleted or lg.status == "running":
            return ""
        frames = [[e, d] for e, d in lg.frames]  # a snapshot, on the loop
        try:
            outcome = await asyncio.wait_for(
                asyncio.shield(asyncio.to_thread(
                    write_home, self.engine, lg.turn_id, lg.thread_id, frames, lg.status,
                    event_bus=_bus_for(self.engine),
                )),
                timeout=HOME_WRITE_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the next start writes it home
            log.warning("chat turn %s: its reply could not be written into its chat yet",
                        lg.turn_id, exc_info=True)
            return HOME_FAILED
        if outcome in _SETTLED:
            lg.home = HOME_DONE
        return outcome

    def _home_later(self, lg: TurnLog, delay: "float | None" = None) -> None:
        """The write home after :data:`HOME_GRACE_S` (one per turn)."""
        if lg.turn_id in self._home_timers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _fire() -> None:
            self._home_timers.pop(lg.turn_id, None)
            self._start_home_write(lg)

        self._home_timers[lg.turn_id] = loop.call_later(
            HOME_GRACE_S if delay is None else delay, _fire,
        )

    def _start_home_write(self, lg: TurnLog) -> "asyncio.Task | None":
        try:
            task = asyncio.ensure_future(self._home_now(lg))
        except RuntimeError:
            return None
        self._home_tasks.add(task)
        task.add_done_callback(self._home_tasks.discard)
        return task

    async def home_due_now(self, turn_id: str) -> str:
        """End the grace of turn *turn_id* now and write its reply home (if
        the chat still has none). "" when no write was waiting."""
        timer = self._home_timers.pop(turn_id, None)
        lg = self._logs.get(turn_id)
        if timer is None or lg is None:
            return ""
        timer.cancel()
        return await self._home_now(lg)

    def home_pending(self, turn_id: str) -> bool:
        """Is a write home waiting out its grace for *turn_id*?"""
        return turn_id in self._home_timers

    async def linked(self, lg: TurnLog, thread_id: str) -> None:
        """``POST /chat/turns/{id}/thread`` named the chat of a turn this
        process holds (third plan G6, CS6): the usage rows it wrote before
        name the chat now, and a turn that already ENDED has its reply
        written home — after the grace when a page followed it, else now.
        Never raises."""
        try:
            if lg.usage_rows:
                rows, lg.usage_rows = list(lg.usage_rows), []
                await asyncio.to_thread(stamp_usage_rows, self.engine, rows, thread_id)
            if lg.status == "running" or _surface_of(lg) == "build" or lg.chat_deleted:
                return
            if lg.followed_at_end:
                self._home_later(lg)
            else:
                await self._home_now(lg)
        except Exception:  # noqa: BLE001 — the link itself already landed
            log.warning("chat turn %s: its reply could not be written home on link", lg.turn_id,
                        exc_info=True)

    def forget_thread(self, thread_id: str) -> "list[str]":
        """CS7: the chat *thread_id* was deleted. Its finished turns leave
        memory now; a running one is marked: nothing of it is written any
        more and its record goes when it ends (fourth plan G27, DT5). Whether
        a running turn stops is the delete's own rule (since fourth plan
        G27, D5: it does — ``routes/chat.delete_thread``, which stops the
        ids returned: this chat's turns still running). Called from any
        thread; never raises."""
        tid = str(thread_id or "").strip()
        running: list[str] = []
        if not tid:
            return running
        try:
            for turn_id, linked in list(self._links.items()):
                if linked == tid:
                    self._links.pop(turn_id, None)
            for turn_id, lg in list(self._logs.items()):
                if str(lg.thread_id or "") != tid:
                    continue
                lg.chat_deleted = True
                if lg.status == "running":
                    running.append(turn_id)
                if lg.status != "running" and (lg.task is None or lg.task.done()):
                    self._logs.pop(turn_id, None)
                    with contextlib.suppress(Exception):
                        _LIVE.pop(turn_id, None)
        except Exception:  # noqa: BLE001
            log.warning("could not forget the turns of deleted chat %s", tid, exc_info=True)
        return running

    async def _announce_finished(self, lg: TurnLog, status: str) -> None:
        """``chat.turn_finished`` (v1.420.0, C6.10) for a CHAT reply longer
        than :data:`CHAT_FINISH_NOTICE_AFTER_S` that finished or failed —
        never a Chat › Code turn (``surface == "build"``: Build's own
        ``build.turn_finished``), a stopped one or one with no chat. The
        chat's title is read off the loop. Never raises."""
        try:
            if status not in ("done", "error") or _surface_of(lg) != "chat":
                return
            thread = str(lg.thread_id or "").strip()
            if not thread:
                return
            seconds = (utcnow() - lg.started_at).total_seconds()
            if seconds <= CHAT_FINISH_NOTICE_AFTER_S:
                return
            bus = _bus_for(self.engine)
            if bus is None:
                return
            from ..core.events import EventType

            titles = await asyncio.to_thread(reply_titles, self.engine, [thread])
            await bus.publish(EventType.CHAT_TURN_FINISHED, {
                "thread_id": thread,
                "turn_id": lg.turn_id,
                "title": titles.get(thread, ""),
                "seconds": int(seconds),
                "outcome": "done" if status == "done" else "failed",
            })
        except Exception:  # noqa: BLE001 — a notice never fails a turn
            log.warning("announcing chat turn %s's end failed", lg.turn_id, exc_info=True)

    async def shutdown(self) -> None:
        """Lifespan exit: cut every running turn off and keep what it wrote."""
        # Third plan G6: a write home still in its grace is not waited for —
        # its record stays "due" and the next start writes it home.
        for timer in list(self._home_timers.values()):
            timer.cancel()
        self._home_timers.clear()
        tasks = [lg.task for lg in self.running() if lg.task is not None]
        for t in tasks:
            t.cancel()
        # v1.428.1: a turn that has just ENDED tells its followers before it
        # saves its record (the driver's ``finally``). Wait for that save too,
        # or a stop in between leaves the record "running" and the next boot
        # reads a finished reply as cut off.
        pending = [
            lg.task for lg in list(self._logs.values())
            if lg.task is not None and not lg.task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # A write home already started finishes (it is short and shielded).
        writes = [t for t in list(self._home_tasks) if not t.done()]
        if writes:
            await asyncio.gather(*writes, return_exceptions=True)


def waiting_unwatched(
    logs: "TurnLogs | None",
    thread_states: "dict[str, dict[str, bool]] | None",
    pending_ids: "Any" = (),
) -> list[dict[str, Any]]:
    """One row per chat that is WAITING FOR THE USER with nobody watching it
    (v1.351.0, G05): ``{id: "chat:<thread id>", kind: "chat", thread_id,
    tool, requested_at, mission_id: None, project_id: None}``.

    THE SILENCE THIS ENDS: since v1.336.0 a chat turn keeps running after the
    user leaves, but its approval card was announced only on its own SSE
    stream. The user who asked for a file edit, went to Build before
    answering and closed the window to the tray heard nothing — the bell and
    the desktop watcher read ``GET /chat/approvals/pending``, which drops the
    chat lane's asks — and an hour later the orphan guard stopped the turn.

    WHICH turns: a daemon-owned turn still running, that NO window is
    SHOWING (``looked_at()`` — a chat open in front of the user shows its own
    card, and a second announcement would double-surface it; readiness 12B: a
    chat page the user left for another page of the same window still
    follows, but said so with ``mark_away``, so it no longer counts), and that is
    parked on the user — an approval card this turn showed that the shared
    registry still holds (``pending_ids``; this also covers a NEW chat's first
    turn, which has no per-chat entry), or its chat reading ``waiting`` in
    ``TURNS.thread_states()`` (an app's question, a model request).

    NEVER ARGUMENTS: the tool's NAME and the chat's id only — the same posture
    as the listing it joins (it fans out to every page and to a toast on a
    possibly shared screen). One row per chat (two waiting turns on one chat
    are one thing to open). Never raises."""
    rows: list[dict[str, Any]] = []
    if logs is None:
        return rows
    try:
        pending = frozenset(str(i) for i in (pending_ids or ()))
        states = thread_states or {}
        seen: set[str] = set()
        for lg in logs.running():
            # Readiness 12B (G05): a window SHOWING the turn has its card in
            # front of the user; a chat page the user left for another page of
            # the same window still follows (its fetch is not ended) but shows
            # nothing, so it is listed (``TurnLog.looked_at``). A test double
            # without the reader keeps the old rule.
            looked = getattr(lg, "looked_at", None)
            if (looked() if callable(looked) else lg.watched()):
                continue
            thread = str(lg.thread_id or "").strip()[:80]
            asks = lg.open_asks(pending)
            parked = bool(thread) and bool((states.get(thread) or {}).get("waiting"))
            if not asks and not parked:
                continue
            row_id = f"chat:{thread}" if thread else f"chat:turn:{lg.turn_id}"
            if row_id in seen:
                continue
            seen.add(row_id)
            newest = asks[-1] if asks else ("", "", "")
            rows.append({
                "id": row_id,
                "kind": "chat",
                "thread_id": thread,
                "tool": newest[1],
                "requested_at": newest[2] or (_iso_utc(lg.started_at) or ""),
                # The listing's contract (v1.310.0): present on EVERY row.
                "mission_id": None,
                "project_id": None,
                # v1.367.0 (Phase 4 review): a Build session is not a chat —
                # additive and LAST, on a Build session's row only (a chat's
                # row is byte-for-byte what it was).
                **({"surface": "build"} if getattr(lg, "surface", "") == "build" else {}),
            })
    except Exception:  # noqa: BLE001 — a listing never fails because of this
        log.warning("could not list waiting chat turns", exc_info=True)
    return rows


#: The two kinds of reply a daemon-owned turn writes (``TurnLog.surface``):
#: a chat's, and a Build coding session's.
REPLY_SURFACES: tuple[str, ...] = ("chat", "build")


def _surface_of(lg: Any) -> str:
    return "build" if getattr(lg, "surface", "") == "build" else "chat"


def running_replies(
    logs: "TurnLogs | None",
    thread_states: "dict[str, dict[str, bool]] | None" = None,
    pending_ids: "Any" = (),
) -> list[dict[str, Any]]:
    """Every reply a daemon-owned turn is writing RIGHT NOW (harness gaps,
    fix 1): ``{turn_id, thread_id, surface: "chat" | "build", started_at,
    waiting}``, oldest first.

    THE GAP THIS CLOSES: a Build coding session's reply runs for an hour
    with no Session row (a Build turn is a chat-shaped turn the daemon owns),
    so Running now said "Nothing is running right now.", the status orb sat
    idle and the home line counted nothing — while "Install when idle"
    (which reads ``CHAT_INFLIGHT``) already knew it ran. The durable turn
    registry is the ONE truth for "which reply runs": this reads it, and
    every surface that says what runs reads this (``GET /chat/turns/running``,
    ``/health``'s ``replies``, the home line, Everything › Status).

    ``waiting`` — parked on the user: its chat reads ``waiting`` in
    ``TURNS.thread_states()`` (an approval card, the model's own question,
    an app's question), or one of its approval cards is still in the shared
    registry (``pending_ids``). In memory only — no database read, nothing
    that blocks; safe from a worker thread (``TurnLogs.running`` snapshots).
    Never raises."""
    rows: list[dict[str, Any]] = []
    if logs is None:
        return rows
    try:
        states = thread_states or {}
        pending = frozenset(str(i) for i in (pending_ids or ()))
        for lg in logs.running():
            thread = str(lg.thread_id or "").strip()[:80]
            parked = bool(thread) and bool((states.get(thread) or {}).get("waiting"))
            asks = bool(pending) and bool(lg.open_asks(pending))
            rows.append({
                "turn_id": lg.turn_id,
                "thread_id": thread,
                "surface": _surface_of(lg),
                "started_at": _iso_utc(lg.started_at),
                "waiting": parked or asks,
            })
    except Exception:  # noqa: BLE001 — a listing never fails because of this
        log.warning("could not list running replies", exc_info=True)
    return rows


def reply_counts(logs: "TurnLogs | None") -> dict[str, int]:
    """How many replies are being written right now, per surface:
    ``{"chat": n, "build": n}`` — the cheap count ``/health`` and the status
    lines carry (in memory, no database). Never raises."""
    out = {s: 0 for s in REPLY_SURFACES}
    if logs is None:
        return out
    try:
        for lg in logs.running():
            out[_surface_of(lg)] += 1
    except Exception:  # noqa: BLE001
        pass
    return out


def reply_titles(engine: Any, thread_ids: "list[str]") -> dict[str, str]:
    """``{thread id: title}`` for the chats / coding sessions whose replies
    run — ONE primary-key read of the title column (never a message body).
    BLOCKING: call it from a sync route (FastAPI's threadpool) or a worker
    thread. Never raises; an unreadable title is left out."""
    ids = sorted({str(t) for t in thread_ids if str(t or "").strip()})
    if not ids:
        return {}
    try:
        from sqlmodel import select

        from ..core.models import ChatThreadRecord as T

        with session_scope(engine) as db:
            return {
                str(tid): str(title or "")
                for tid, title in db.exec(select(T.id, T.title).where(T.id.in_(ids)))  # type: ignore[attr-defined]
            }
    except Exception:  # noqa: BLE001
        log.warning("could not read the running replies' titles", exc_info=True)
        return {}


def stop_predicate(get_log: Any) -> Any:
    """The lane's ``should_stop`` for a daemon-owned CHAT turn: never a
    connection — only the orphan guard (Stop itself goes through the turn
    registry's handle, which the lane checks first). v1.384.0: a BUILD turn
    is never stopped for being unwatched (``routes/build._never_orphaned``):
    a long Build reply is meant to run while the user works elsewhere."""

    def _stop() -> bool:
        lg = get_log()
        if lg is None:
            return False
        # Readiness 0j.5 item 17: a turn parked on a card waits for its
        # person, however long — never stopped for being unwatched.
        if lg.parked_on_a_card():
            return False
        if lg.unwatched_for() > ORPHAN_STOP_S:
            # ...and a turn this guard DOES stop says why (``_drive``).
            lg.stop_why = ORPHAN_STOP_DETAIL
            return True
        return False

    return _stop


def replay_record(engine: Any, turn_id: str, *, with_ids: bool = False) -> "tuple[str, str] | None":
    """``(sse_text, status)`` of a turn this process does not hold, from its
    record; None when there is none. ``with_ids`` (v1.384.0): the tagged
    WHOLE turn (``resume`` with ``resumed: false``, every frame with its
    position, ``caught_up``) — a record is never resumed part-way."""
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        if row is None:
            return None
        try:
            frames = json.loads(row.frames_json or "[]")
        except ValueError:
            frames = []
        frames = [f for f in frames if isinstance(f, list) and len(f) == 2]
        if with_ids:
            return tagged_replay(frames, resumed=False, running=False), row.status
        return "".join(sse(e, d) for e, d in frames), row.status


def link_record(engine: Any, turn_id: str, thread_id: str) -> None:
    """Stamp a FINISHED turn's record with the chat it belongs to, when it
    has none yet (see ``POST /chat/turns/{id}/thread``)."""
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        if row is not None and not row.thread_id:
            row.thread_id = thread_id
            db.add(row)
            db.commit()


def list_for_thread(engine: Any, thread_id: str, limit: int = 3) -> list[dict[str, Any]]:
    from sqlmodel import select

    with session_scope(engine) as db:
        rows = list(
            db.exec(
                select(ChatTurnRecord)
                .where(ChatTurnRecord.thread_id == thread_id)
                .order_by(ChatTurnRecord.started_at.desc())  # type: ignore[union-attr]
                .limit(max(1, min(limit, 20)))
            ).all()
        )
        return [
            {
                "turn_id": r.id,
                "status": r.status,
                "started_at": _iso_utc(r.started_at),
                "updated_at": _iso_utc(r.updated_at),
            }
            for r in rows
        ]


def _iso_utc(when: Any) -> "str | None":
    """``2026-10-12T17:02:41.123456Z`` — stored UTC (SQLite hands it back
    naive), said so, so a browser parses it as UTC and not as local time."""
    if when is None:
        return None
    return when.replace(tzinfo=None).isoformat() + "Z"


def reconcile_interrupted_turns(engine: Any) -> int:
    """Boot step: a turn still "running" on disk was cut off by the stop of
    the process that ran it. Its log ends with an error frame that says so.

    Third plan G6 / RB9: old records are no longer removed HERE — that ran
    before a coding session's unsaved reply was put back
    (``sessions.reconcile_unsaved_replies``) and before a chat's reply was
    written home, and deleted the very record either needed. The start
    removes them after both, with :func:`drop_old_records`. Only the running
    rows are read (never every record whole)."""
    from sqlmodel import select

    marked = 0
    with session_scope(engine) as db:
        for row in db.exec(select(ChatTurnRecord).where(ChatTurnRecord.status == "running")).all():
            try:
                frames = json.loads(row.frames_json or "[]")
            except ValueError:
                frames = []
            frames.append(["error", json.dumps({"detail": INTERRUPTED_DETAIL})])
            row.frames_json = json.dumps(frames)
            row.status = "interrupted"
            row.updated_at = utcnow()
            db.add(row)
            marked += 1
        db.commit()
    return marked


def turn_state(engine: Any, turn_id: str) -> "dict[str, Any] | None":
    """``{turn_id, status, running, started_at, thread_id}`` of a turn known
    only from its record (third plan CS10: ``GET /chat/turns/{id}``); None
    when there is none. One primary-key read, never the frames. BLOCKING."""
    from sqlmodel import select

    with session_scope(engine) as db:
        row = db.exec(
            select(ChatTurnRecord.status, ChatTurnRecord.started_at, ChatTurnRecord.thread_id)
            .where(ChatTurnRecord.id == turn_id)
        ).first()
    if row is None:
        return None
    status, started, thread = row
    return {
        "turn_id": turn_id,
        "status": str(status or ""),
        # This process does not hold it, so it is not running here (a record
        # says "running" only until the next start reconciles it).
        "running": False,
        "started_at": _iso_utc(started),
        "thread_id": str(thread or ""),
    }


def forget_records(engine: Any, turn_ids: "list[str]") -> int:
    """Delete these turn records. BLOCKING; never raises."""
    ids = sorted({str(t) for t in turn_ids if str(t or "").strip()})
    if not ids:
        return 0
    try:
        from sqlalchemy import delete

        with session_scope(engine) as db:
            res = db.exec(delete(ChatTurnRecord).where(ChatTurnRecord.id.in_(ids)))  # type: ignore[call-overload, attr-defined]
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001
        log.warning("could not delete chat turn records", exc_info=True)
        return 0


def drop_thread_records(db: Any, thread_id: str) -> int:
    """CS7: delete chat *thread_id*'s turn records in the CALLER's
    transaction (the chat is being deleted: each reply's text, thinking and
    tool inputs and results go with it). Never raises."""
    try:
        from sqlalchemy import delete

        res = db.exec(delete(ChatTurnRecord).where(ChatTurnRecord.thread_id == thread_id))  # type: ignore[call-overload]
        return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001
        log.warning("could not delete the turn records of chat %s", thread_id, exc_info=True)
        return 0


def forget_thread(engine: Any, thread_id: str) -> "list[str]":
    """CS7: chat *thread_id* was deleted — every turn registry on *engine*
    forgets its turns (:meth:`TurnLogs.forget_thread`). Returns the ids of
    its turns still running (fourth plan G27: the delete stops them). Never
    raises."""
    running: list[str] = []
    try:
        for reg in list(_REGISTRIES):
            if getattr(reg, "engine", None) is engine:
                running.extend(reg.forget_thread(thread_id) or [])
    except Exception:  # noqa: BLE001
        log.warning("could not forget the turns of deleted chat %s", thread_id, exc_info=True)
    return running


def _thread_rows(engine: Any, thread_ids: "set[str]", *, messages: bool) -> dict[str, Any]:
    """``{id: (id, surface, owner[, messages_json])}`` for these chats in ONE
    query (500 ids a statement). BLOCKING."""
    from sqlmodel import select

    from ..core.models import ChatThreadRecord as T

    ids = sorted({t for t in thread_ids if t})
    if not ids:
        return {}
    cols = [T.id, T.surface, T.owner] + ([T.messages_json] if messages else [])
    out: dict[str, Any] = {}
    with session_scope(engine) as db:
        for i in range(0, len(ids), 500):
            for row in db.exec(select(*cols).where(T.id.in_(ids[i:i + 500]))):  # type: ignore[attr-defined, call-overload]
                out[str(row[0])] = tuple(row)
    return out


def _messages_of(raw: Any) -> list:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _record_frames(engine: Any, turn_id: str) -> list:
    with session_scope(engine) as db:
        rec = db.get(ChatTurnRecord, turn_id)
        return _messages_of(rec.frames_json if rec is not None else "[]")


def bring_replies_home(engine: Any, *, event_bus: Any = None, limit: int = HOME_BOOT_LIMIT) -> int:
    """Start-up (third plan G6): a FINISHED record this code marked "due" —
    its reply ended while nobody saved it, its grace was cut by a stop, or a
    shutdown cut the turn off — has its reply written into its chat now,
    unless the chat already holds one for that turn (at the top level or in a
    fork). A record from an older version (no mark) is never touched. The
    chats are read in ONE query; bounded by *limit*. Returns the replies
    written. BLOCKING (run it off the loop); never raises."""
    from sqlmodel import select

    from .chat_append import has_turn_reply

    written = 0
    try:
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(ChatTurnRecord.id, ChatTurnRecord.thread_id, ChatTurnRecord.status)
                .where(ChatTurnRecord.home == HOME_DUE)
                .where(ChatTurnRecord.status != "running")
                .where(ChatTurnRecord.thread_id != "")
                .order_by(ChatTurnRecord.started_at)  # type: ignore[arg-type]
                .limit(max(1, int(limit)))
            ))
        if not rows:
            return 0
        threads = _thread_rows(engine, {str(r[1] or "") for r in rows}, messages=True)
        settled: list[str] = []
        for turn_id, thread_id, _status in rows:
            t = threads.get(str(thread_id or ""))
            if t is None:
                continue  # the chat is gone: the clean-up removes it by age
            _id, surface, owner, raw = t
            if str(surface or "") == "build" or str(owner or "user") == "daemon":
                settled.append(turn_id)  # its own writer's, not a chat's
                continue
            if has_turn_reply(_messages_of(raw), turn_id):
                settled.append(turn_id)  # the page saved it
                continue
            if bring_home_record(engine, turn_id, event_bus=event_bus) == HOME_WRITTEN:
                written += 1
        _set_home(engine, settled)
    except Exception:  # noqa: BLE001 — a boot step never blocks the boot
        log.warning("could not write chat replies into their chats at start", exc_info=True)
    if written:
        log.info("wrote %d chat reply(ies) into their chats at start", written)
    return written


#: The states a turn's record ends in (``_drive``, the boot reconcile).
_ENDED = ("done", "error", "stopped", "interrupted")


def _build_reply_due(engine: Any, turn_id: str, status: str, started: Any, msgs: list) -> bool:
    """RB9: does a coding session still need this record to put its reply
    back (``sessions.reconcile_interrupted_turns`` /
    ``reconcile_unsaved_replies``)? The session holds the turn's question and
    no reply, and the record would give it a reply line. Never raises (True
    when unsure: a record is kept rather than lost)."""
    try:
        if str(status) not in _ENDED:
            return False
        if any(isinstance(m, dict) and m.get("role") == "assistant" and m.get("turnId") == turn_id
               for m in msgs):
            return False
        if not any(isinstance(m, dict) and m.get("role") == "user" and m.get("turnId") == turn_id
                   and not m.get("steer") for m in msgs):
            return False
        if str(status) == "interrupted":
            return True  # the cut-off pass always writes a line for it
        from datetime import timezone

        from .routes.build import replayed_reply_line

        when = 0.0
        if started is not None:
            when = (started if started.tzinfo is not None else started.replace(tzinfo=timezone.utc)).timestamp()
        lines = replayed_reply_line(
            turn_id, _record_frames(engine, turn_id), started_at=when, status=str(status),
        ) or []
        return bool(lines) and lines[-1].get("role") == "assistant"
    except Exception:  # noqa: BLE001
        log.warning("could not tell whether coding reply %s is saved", turn_id, exc_info=True)
        return True


def drop_old_records(engine: Any) -> int:
    """Start-up clean-up: finished records older than ``KEEP_RECORDS_DAYS``
    are removed — run AFTER the passes that put replies back (the coding
    session's, :func:`bring_replies_home`). Never one whose reply has not
    reached its home (third plan G6, RB9):

    * a chat's record marked "due" whose chat still lacks its reply is kept
      (the next start tries again) — unless the chat is gone, the record names
      no chat (a chat never saved: there is nowhere to put it), or there is
      nothing to write;
    * a coding session's record is kept while the session holds its question
      and no reply and the record would give one (:func:`_build_reply_due`).

    Reads only the old rows' ids and marks (never their frames), and their
    chats in one query. Returns how many were removed. BLOCKING; never raises."""
    from datetime import timedelta

    from sqlmodel import select

    from .chat_append import has_turn_reply

    try:
        cutoff = (utcnow() - timedelta(days=KEEP_RECORDS_DAYS)).replace(tzinfo=None)
        with session_scope(engine) as db:
            # Third plan G23 (CS9): only the OLD finished rows come back (the
            # database filters them; the check below stays as the rule).
            rows = [
                tuple(r) for r in db.exec(select(  # type: ignore[call-overload]
                    ChatTurnRecord.id, ChatTurnRecord.thread_id, ChatTurnRecord.status,
                    ChatTurnRecord.home, ChatTurnRecord.updated_at, ChatTurnRecord.started_at,
                ).where(ChatTurnRecord.status != "running")
                 .where(ChatTurnRecord.updated_at < cutoff))  # type: ignore[operator]
                if r[2] != "running" and r[4] is not None and r[4].replace(tzinfo=None) < cutoff
            ]
        if not rows:
            return 0
        kinds = _thread_rows(engine, {str(r[1] or "") for r in rows}, messages=False)
        # The messages only of the chats that must be looked into.
        look = {
            str(r[1]) for r in rows
            if str(r[1] or "") in kinds
            and (r[3] == HOME_DUE or str(kinds[str(r[1])][1] or "") == "build")
        }
        bodies = _thread_rows(engine, look, messages=True) if look else {}
        gone: list[str] = []
        for turn_id, thread_id, status, home, _updated, started in rows:
            tid = str(thread_id or "")
            kind = kinds.get(tid)
            if not tid or kind is None:
                gone.append(turn_id)  # no chat, or the chat is gone
                continue
            msgs = _messages_of(bodies[tid][3]) if tid in bodies else []
            if str(kind[1] or "") == "build":
                if not _build_reply_due(engine, turn_id, status, started, msgs):
                    gone.append(turn_id)
                continue
            if (
                home == HOME_DUE
                and str(kind[2] or "user") != "daemon"
                and not has_turn_reply(msgs, turn_id)
                and reply_line(turn_id, _record_frames(engine, turn_id), str(status)) is not None
            ):
                continue  # its reply has not reached its chat yet: kept
            gone.append(turn_id)
        removed = 0
        for i in range(0, len(gone), 500):
            removed += forget_records(engine, gone[i:i + 500])
        return removed
    except Exception:  # noqa: BLE001 — a boot step never blocks the boot
        log.warning("could not remove old chat turn records", exc_info=True)
        return 0


def home_and_tidy(engine: Any, *, event_bus: Any = None) -> "tuple[int, int]":
    """The start's chat pass, in its order (third plan G6, RB9): write the
    replies that never reached their chat, THEN remove the old records.
    ``(written, removed)``. BLOCKING; never raises."""
    written = bring_replies_home(engine, event_bus=event_bus)
    removed = drop_old_records(engine)
    return written, removed
