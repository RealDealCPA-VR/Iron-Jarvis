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
#: How long a finished turn's log stays in memory for a late rejoin.
FINISHED_KEEP_S = 600.0
#: A turn nobody has followed for this long is stopped (see the module doc).
ORPHAN_STOP_S = 3600.0
#: Stored frames are capped; past it, later TOKEN detail is not stored (the
#: final ``done`` frame still carries the whole reply).
MAX_STORED_CHARS = 8_000_000
#: Finished records older than this are removed at boot.
KEEP_RECORDS_DAYS = 7
#: The error a turn cut off by a restart ends with.
INTERRUPTED_DETAIL = (
    "Iron Jarvis stopped while answering (a restart or a crash). What it had"
    " written so far is kept; press Retry to ask again."
)

_MERGEABLE = ("token", "thinking")
_END = object()


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


def tagged_replay(frames: list[list[str]], *, resumed: bool = False, running: bool = False) -> str:
    """The WHOLE stored turn as a tagged answer: ``resume`` (``resumed``
    false = drop what you hold), every frame with its position, ``caught_up``.
    The answer for a page with no usable resume point, and for a turn replayed
    from its record."""
    out = [sse("resume", {"resumed": resumed, "running": running})]
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
        self.frames: list[list[str]] = []  # [event, data_json], merged
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
        self.task: "asyncio.Task | None" = None
        self.finished_at: float | None = None
        #: approval id -> (tool, asked at ISO) for every approval card this turn
        #: has shown and not yet seen answered (v1.351.0, G05). NAMES ONLY —
        #: never the card's arguments: :func:`waiting_unwatched` lists these
        #: to every page and to the desktop.
        self._asks: dict[str, tuple[str, str]] = {}

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
        if not self.frames:
            return ""
        last = len(self.frames) - 1
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

    def _note_ask(self, event: str, data: str) -> None:
        """Keep the open approval cards' ids and tool names (v1.351.0, G05)."""
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

    def open_asks(self, pending: "set[str] | frozenset[str]") -> list[tuple[str, str, str]]:
        """``[(approval id, tool, asked at)]`` for this turn's cards that are
        still waiting — the shared registry (``pending``) is the truth: a card
        it no longer holds was answered, timed out or abandoned."""
        return [(aid, tool, at) for aid, (tool, at) in self._asks.items() if aid in pending]

    def _store(self, event: str, data: str) -> None:
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
                return
            except (ValueError, AttributeError, TypeError):
                pass
        if self._chars + len(data) > MAX_STORED_CHARS and event in _MERGEABLE:
            return
        self.frames.append([event, data])
        self._chars += len(data)
        self._dirty = True
        self._tail = (len(self.frames) - 1, _text_len(event, data))

    def last_event(self) -> str:
        return self.frames[-1][0] if self.frames else ""

    # -- following ------------------------------------------------------------
    def watched(self) -> bool:
        return bool(self._followers)

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
            return tagged_replay(self.frames, resumed=False, running=running)
        out = [sse("resume", {"resumed": True, "running": running})]
        out += [tagged(p, e, d) for p, e, d in tail]
        out.append(sse("caught_up", {"id": self.position()}))
        return "".join(out)

    async def follow(self, *, with_ids: bool = False, after: "str | None" = None) -> AsyncIterator[str]:
        """The turn so far as ONE chunk, then each new chunk, until it ends.
        Snapshot and subscription happen in one step (no await between), so
        nothing is missed or sent twice.

        ``with_ids`` (v1.384.0): every frame tagged with its resume position,
        the stored part between ``resume`` and ``caught_up``, and — with
        ``after`` — only what came after that position (see the module doc).
        False: byte-for-byte what it always sent."""
        q: asyncio.Queue = asyncio.Queue()
        head = self.tagged_head(after) if with_ids else self.snapshot()
        running = self.status == "running"
        if running:
            self._followers.add(q)
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
            self._last_seen = time.monotonic()

    def _end_followers(self) -> None:
        for q in list(self._followers):
            q.put_nowait(_END)

    # -- the record -----------------------------------------------------------
    def _row_values(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "status": self.status,
            "frames_json": json.dumps(self.frames),
            "updated_at": utcnow(),
        }

    def maybe_persist(self, engine: Any) -> None:
        now = time.monotonic()
        if not self._dirty or now - self._last_persist < PERSIST_EVERY_S:
            return
        if self._persisting is not None and not self._persisting.done():
            return
        self._last_persist = now
        self._dirty = False
        values = self._row_values()
        self._persisting = asyncio.ensure_future(
            asyncio.to_thread(_write_record, engine, self.turn_id, self.started_at, values)
        )

    async def persist_now(self, engine: Any) -> None:
        if self._persisting is not None:
            with contextlib.suppress(Exception):
                await self._persisting
        self._dirty = False
        await asyncio.to_thread(
            _write_record, engine, self.turn_id, self.started_at, self._row_values()
        )


def _write_record(engine: Any, turn_id: str, started_at: Any, values: dict[str, Any]) -> None:
    try:
        with session_scope(engine) as db:
            row = db.get(ChatTurnRecord, turn_id)
            if row is None:
                row = ChatTurnRecord(id=turn_id, started_at=started_at)
            for k, v in values.items():
                setattr(row, k, v)
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — the live turn never fails on its record
        log.warning("could not save chat turn %s", turn_id, exc_info=True)


class TurnLogs:
    """Every daemon-owned turn this process holds (one per platform)."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self._logs: dict[str, TurnLog] = {}
        #: chat ids linked before their turn started (bounded).
        self._links: dict[str, str] = {}

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
        self._logs[turn_id] = lg
        lg.task = asyncio.ensure_future(self._drive(lg, frames))
        return lg

    async def _drive(self, lg: TurnLog, frames: AsyncIterator[str]) -> None:
        from ..core.turns import CHAT_INFLIGHT

        status = "stopped"
        CHAT_INFLIGHT.enter()
        try:
            async with contextlib.aclosing(frames):  # type: ignore[arg-type]
                async for chunk in frames:
                    lg.add(chunk)
                    lg.maybe_persist(self.engine)
            last = lg.last_event()
            status = "done" if last == "done" else "error" if last == "error" else "stopped"
        except asyncio.CancelledError:
            # The daemon is shutting down: what was written is kept, and the
            # log says the turn was cut off.
            lg.add(sse("error", {"detail": INTERRUPTED_DETAIL}))
            status = "interrupted"
            raise
        except Exception as exc:  # noqa: BLE001 — the lane yields its own errors
            log.warning("chat turn %s failed outside its stream", lg.turn_id, exc_info=True)
            lg.add(sse("error", {"detail": f"{type(exc).__name__}: {exc}"}))
            status = "error"
        finally:
            CHAT_INFLIGHT.exit()
            lg.status = status
            lg.finished_at = time.monotonic()
            lg._end_followers()
            try:
                await asyncio.shield(lg.persist_now(self.engine))
            except BaseException:  # noqa: BLE001 — shutdown: the boot reconcile covers it
                pass

    async def shutdown(self) -> None:
        """Lifespan exit: cut every running turn off and keep what it wrote."""
        tasks = [lg.task for lg in self.running() if lg.task is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


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
    following (``watched()`` — a chat open in front of the user shows its own
    card, and a second announcement would double-surface it), and that is
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
            if lg.watched():
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


def stop_predicate(get_log: Any) -> Any:
    """The lane's ``should_stop`` for a daemon-owned CHAT turn: never a
    connection — only the orphan guard (Stop itself goes through the turn
    registry's handle, which the lane checks first). v1.384.0: a BUILD turn
    is never stopped for being unwatched (``routes/build._never_orphaned``):
    a long Build reply is meant to run while the user works elsewhere."""

    def _stop() -> bool:
        lg = get_log()
        return lg is not None and lg.unwatched_for() > ORPHAN_STOP_S

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
    Finished records older than ``KEEP_RECORDS_DAYS`` are removed."""
    from datetime import timedelta

    from sqlmodel import select

    marked = 0
    cutoff = utcnow() - timedelta(days=KEEP_RECORDS_DAYS)
    with session_scope(engine) as db:
        for row in db.exec(select(ChatTurnRecord)).all():
            if row.status == "running":
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
            elif row.updated_at is not None and row.updated_at.replace(tzinfo=None) < cutoff.replace(tzinfo=None):
                db.delete(row)
        db.commit()
    return marked
