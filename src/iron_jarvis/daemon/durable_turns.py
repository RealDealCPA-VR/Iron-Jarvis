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
#: Finished records older than this are removed at boot.
KEEP_RECORDS_DAYS = 7
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

    def mark_away(self, viewer: str) -> bool:
        """The chat page *viewer* is no longer on screen (the user went to
        another page of the same window); True when it is following this
        turn. Kept until that follower ends (a new page is a new viewer)."""
        v = str(viewer or "").strip()[:80]
        if not v or v not in self._viewers.values():
            return False
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
        return {
            "thread_id": self.thread_id,
            "status": self.status,
            "frames_json": [[e, d] for e, d in self.frames],
            "updated_at": utcnow(),
        }

    def persist_interval(self) -> float:
        """Seconds between two writes of the running record (v1.384.0 review:
        it grows with the record — see :data:`PERSIST_BYTES_PER_S`)."""
        return max(PERSIST_EVERY_S, self._chars / PERSIST_BYTES_PER_S)

    def maybe_persist(self, engine: Any) -> None:
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
        await asyncio.to_thread(
            _write_record, engine, self.turn_id, self.started_at, self._row_values()
        )


def _write_record(engine: Any, turn_id: str, started_at: Any, values: dict[str, Any]) -> None:
    try:
        if not isinstance(values.get("frames_json", ""), str):
            # v1.418.0 (C3.6): serialised HERE, off the loop.
            values = {**values, "frames_json": json.dumps(values["frames_json"])}
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

    def mark_away(self, turn_id: str, viewer: str) -> "bool | None":
        """Readiness 12B (G05): the chat page *viewer* left the screen while
        following ``turn_id``. None when no such turn is RUNNING here; else
        whether that viewer was following it (:meth:`TurnLog.mark_away`)."""
        lg = self._logs.get(turn_id)
        if lg is None or lg.status != "running":
            return None
        return lg.mark_away(viewer)

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
        # v1.420.0 (C6.10): a long CHAT reply that ended says so from the
        # tray. Outside the ``finally``: a cut-off (``CancelledError``) never
        # reaches here, and a stopped turn is not news.
        await self._announce_finished(lg, status)

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
