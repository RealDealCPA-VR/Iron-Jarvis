"""Mid-turn tool approvals — ONE registry for every asking surface.

Born in the daemon (v1.187.0) for chat's mid-turn ask; moved to ``core``
(v1.189.0) when agent SESSIONS gained the same pause, because the runtime
lives in the platform layer and a platform module importing from ``daemon``
is the layering inversion v1.185.0 spent a release removing. The instance
lives on the platform; the chat route and the agent runtime share it, so ONE
route — ``POST /chat/approvals/{id}`` — answers a pause wherever it happened.


The missing half of a mechanism the app has carried for two releases. The
permission engine names "the interactive per-session grant (``session_allow``)"
as the sanctioned way to hand an ``ask``-tier tool to one task, and
``registry.invoke`` has carried ``deny_reason=`` since v1.155.0 for "a caller
that already asked a human and was refused". Both halves assume somebody ASKS
THE HUMAN — and nothing in the chat lane ever did. A tool that resolved to
``ask`` was silently denied mid-turn, the model read "permission denied", and
the user learned about it (at best) from a footnote under the reply.

The experience that closes: the stream lane pauses the turn, emits an
``approval`` frame, the dashboard renders Allow once / Allow for this
conversation / Deny, and ``POST /chat/approvals/{id}`` resolves the wait. The
turn then proceeds with the grant — or invokes with ``deny_reason=`` so the
refusal is ledger-recorded as the user's decision, which it now genuinely is.

WHY THIS IS IN-MEMORY AND PROCESS-LOCAL (the ``capability._CLAIMS`` argument):
a pending approval is a wait INSIDE one live SSE response on one event loop.
It cannot outlive the stream that is waiting on it, so persistence would only
manufacture orphans — a row promising an answer to a request whose asker is
gone. A daemon restart kills the stream and the question together, which is
the correct outcome for both.

The AWAITER owns the deadline, not a sweeper: the stream loop knows when it
stopped waiting and pops the entry in its ``finally``, so an id can never
resolve a future whose turn has moved on. ``resolve`` on an unknown id is a
clean False (the route's 404) rather than an error, because a double-click on
the card races the pop and the second click must read as "already answered",
not as a failure.

LOOP-AWARE SINCE v1.209.0 (D3): "one live event loop" stopped being true the
moment scheduled work could ask. A schedule fire runs inside ``asyncio.run``
on the APScheduler WORKER thread (``platform._run_scheduled`` →
``scheduling/service.py::_fire``), so a scheduled goal iteration's ask-tier
pause creates its future on that PRIVATE loop — while every answering surface
(``POST /chat/approvals/{id}``, the Telegram resolve) runs on the daemon's
MAIN loop. A bare ``fut.set_result`` from the main thread sets the result but
never WAKES the private loop's selector: the user's Allow applied only when
the ``wait_for`` timeout fired (~300s late, reading as a timeout), and under
debug mode it RuntimeErrors — so approved receipts effectively could not mint
from scheduled runs. :meth:`request` therefore records the CREATING loop
beside the future, and :meth:`resolve` marshals a foreign-loop answer via
``call_soon_threadsafe`` — with the ``fut.done()`` guard repeated INSIDE the
marshaled callable, because the answered-already race moves with the marshal.
The same-loop path is byte-identical to the pre-v1.209 behavior. A creating
loop that already died (``asyncio.run`` closes its loop when the fire ends)
answers an honest False, never raises — the asker is gone and so is the
question, exactly the in-memory registry's founding argument.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterable

#: What a resolution may say. "once" grants THIS call; "conversation" grants
#: the rest of the turn too (and the client re-arms the tool for later turns —
#: the existing "+"-menu machinery, not a second grant store); "deny" refuses;
#: "tab" (v1.266.0) grants this call AND the browser tab it acts on, for as long
#: as that tab stays open — the chat lane records it on
#: ``BrowserRuntime.tab_grants`` (browser/grants.py) and then treats the call as
#: "once". A lane that knows no tabs (the agent runtime) reads it as "once".
#: "always" (v1.299.0) is "once" for THIS call PLUS a ``StandingGrantRecord``
#: (``core/grants.py``) for the EXACT arguments in the strongest scope the
#: asker knows (``grants.pick_scope``: agent > project > goal/chat), 30 days
#: — so the next identical call, in a new session of that scope, needs no
#: card. A lane with no store or no scope reads it as "once".
DECISIONS = ("once", "conversation", "deny", "tab", "always")

#: How long a turn will hold for an answer before denying honestly. Long
#: enough to read what the tool wants and decide; short enough that a stream
#: whose user walked away ends instead of pinning a connection forever.
APPROVAL_TIMEOUT_S = 180.0

#: Sessions whose waited-on-asks total is remembered (v1.333.0, D9). The map
#: is read right after a run ends (the goal engine), and it gained one entry
#: per session that ever asked, for the daemon's life; the least recently
#: updated entries past this are forgotten.
WAITED_SESSIONS_MAX = 1024

#: The longest cost sentence a pending ask keeps (v1.356.0, G79). The words
#: are the service's quote ("Generate 1 image on Pixio for 12 credits (your
#: price)"), never the call's arguments; the cap only bounds what a listing
#: carries.
COST_NOTE_MAX = 300


def _cost_sentence(cost_note: Any) -> str:
    """The quote's sentence as a listing may carry it: a string, masked
    (``detections.redact.mask`` — belt and braces: the words are built from
    a price, never from arguments), cut to :data:`COST_NOTE_MAX`. ``""``
    for anything else."""
    if not isinstance(cost_note, str):
        return ""
    text = " ".join(cost_note.split())
    if not text:
        return ""
    try:
        from ..detections.redact import mask

        text = mask(text)
    except Exception:  # noqa: BLE001 — wording only, never a new failure
        pass
    if len(text) > COST_NOTE_MAX:
        text = text[: COST_NOTE_MAX - 1].rstrip() + "…"
    return text


class ChatApprovals:
    """Pending mid-turn approval requests, keyed by id."""

    def __init__(self) -> None:
        #: id -> (future, THE LOOP THAT CREATED IT). The loop is recorded so an
        #: answer arriving on another loop/thread (main-loop route vs a
        #: scheduled fire's private ``asyncio.run`` loop) can be marshaled to
        #: the one place the future may legally be resolved — see the module
        #: docstring (D3, v1.209.0).
        self._pending: dict[
            str, tuple["asyncio.Future[str]", asyncio.AbstractEventLoop]
        ] = {}
        #: id -> what was asked, for WHOM (v1.227.0, A2/A4). Recorded so a
        #: 'conversation' answer on one of a batch of parallel asks can find
        #: the siblings (``resolve_where``) and so a session row can say what
        #: it is waiting on (``pending_for``). ``args`` here are whatever the
        #: asker passed — both lanes pass the REDACTED args they already
        #: publish/stream — never the raw call.
        self._meta: dict[str, dict[str, Any]] = {}
        #: session id -> seconds its run has spent PARKED on asks (v1.231.0,
        #: audit AE13). Accumulated in :meth:`pop` from ``requested_at``, on
        #: every exit — answer, timeout, cancel — so a budget that charges
        #: wall-clock can subtract the time nobody was working.
        self._waited: "OrderedDict[str, float]" = OrderedDict()

    def request(
        self,
        tool: str,
        args: dict[str, Any] | None,
        session_id: str | None = None,
        *,
        count: int = 1,
        examples: "list[Any] | None" = None,
        cost_note: str | None = None,
        once_only: bool = False,
        once_reason: str = "",
    ) -> tuple[str, "asyncio.Future[str]"]:
        """File one request; returns ``(id, future)``. The future resolves to a
        DECISIONS value. ``tool``/``args`` are kept only as display/matching
        metadata (the asker passes already-redacted args); the SSE frame /
        event still carries them to the client that answers. ``session_id``
        (v1.227.0) names the run that is waiting, so ``pending_for`` and
        ``resolve_where`` can scope to it; a chat-lane ask passes none.

        ``cost_note`` (v1.356.0, G79) is what a PRICED call would spend, in
        the service's own words — the sentence the ask's own frame/event
        carries (``SpendCheck.words``). Recorded only when there is one, so
        an ask with no price keeps its exact shape, and handed on by
        ``pending_for`` so a surface that reads a run's waiting ask (the
        session row, the mission screen) can say the cost before Allow."""
        approval_id = f"apr_{secrets.token_hex(8)}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending[approval_id] = (fut, loop)
        self._meta[approval_id] = {
            "approval_id": approval_id,
            "session_id": str(session_id) if session_id else None,
            "tool": str(tool or ""),
            "args": args,
            "requested_at": time.time(),
        }
        # ONE ASK FOR A BATCH (v1.247.0): how many calls this one answer
        # covers, and up to three of their (already redacted) argument sets.
        # Recorded only for a real batch, so a single ask keeps its shape.
        if int(count or 1) > 1:
            self._meta[approval_id]["count"] = int(count)
            self._meta[approval_id]["examples"] = list(examples or [])[:3]
        # G79 (v1.356.0): the price sentence, only for a priced ask.
        _cost = _cost_sentence(cost_note)
        if _cost:
            self._meta[approval_id]["cost_note"] = _cost
        # v1.381.0: an ask answered ONCE, every time (a once-only spend check:
        # a commit / push on a Build session's branch, a project's or a
        # plugin's command's shell), and WHY (a short id — "git", "command"
        # — never a sentence). Recorded only when set.
        if once_only is True:
            self._meta[approval_id]["once_only"] = True
            reason = str(once_reason or "").strip()
            if reason and len(reason) <= 32 and reason.isidentifier():
                self._meta[approval_id]["once_reason"] = reason
        return approval_id, fut

    def pending_for(self, session_id: str | None) -> list[dict[str, Any]]:
        """The asks a session is currently waiting on, oldest first (v1.227.0).

        Each entry: ``{approval_id, tool, args, requested_at}``, plus
        ``count``/``examples`` on a batched ask and ``cost_note`` (a
        sentence) on a priced one. Only asks filed WITH that session id
        match — a chat-lane ask (no session) is never attributed to a run,
        and an empty/None id answers ``[]``."""
        sid = str(session_id or "")
        if not sid:
            return []
        rows = [
            {
                "approval_id": m["approval_id"],
                "tool": m["tool"],
                "args": m["args"],
                "requested_at": m["requested_at"],
                # v1.247.0: present only on a batched ask (see ``request``).
                **(
                    {"count": m["count"], "examples": m.get("examples", [])}
                    if "count" in m
                    else {}
                ),
                # v1.356.0 (G79): present only on a priced ask.
                **({"cost_note": m["cost_note"]} if m.get("cost_note") else {}),
                # v1.381.0: present only on an ask answered once, every time.
                **({"once_only": True} if m.get("once_only") is True else {}),
                **({"once_reason": m["once_reason"]} if m.get("once_reason") else {}),
            }
            for m in self._meta.values()
            if m.get("session_id") == sid
        ]
        rows.sort(key=lambda m: m["requested_at"])
        return rows

    def resolve_where(
        self, session_id: str | None, tool: "str | Iterable[str]", decision: str,
        *, actor: str = "grant",
    ) -> int:
        """Answer EVERY pending ask for ``session_id`` whose tool is ``tool``
        (a name or a set of names) with ``decision``; returns how many were
        delivered (v1.227.0, A2).

        The runtime pauses PER CALL inside a gather, so a batch of N parallel
        asks for the same tool is N cards. 'Allow for this conversation' on
        one used to widen the run's grant while the N-1 siblings stayed parked
        in their own ``wait_for`` and were then denied by the clock. The
        caller widens the grant, then calls this so the siblings wake with
        the same answer. Same delivery rules as :meth:`resolve` (cross-loop
        marshal, done-guard inside the marshal).

        ``actor`` (v1.402.0, the autonomy metrics): who settled them — a
        sibling woken by an answer on another card is settled by the GRANT
        that answer made, never counted as a second answer by the person."""
        if decision not in DECISIONS:
            return 0
        sid = str(session_id or "")
        if not sid:
            return 0
        names = {tool} if isinstance(tool, str) else {str(t) for t in tool}
        names.discard("")
        n = 0
        for approval_id, m in list(self._meta.items()):
            if m.get("session_id") != sid or m.get("tool") not in names:
                continue
            entry = self._pending.get(approval_id)
            if entry is not None and not entry[0].done():
                m.setdefault("actor", _clean_actor(actor))
            if entry is not None and self._deliver(entry, decision):
                n += 1
        return n

    def actor_of(self, approval_id: str) -> str:
        """Who answered ``approval_id`` (v1.402.0): the ``actor`` the answer
        that settled it named (``app`` | ``phone`` | ``grant`` | ``bench``),
        ``""`` when nobody has answered it (yet) or the id is unknown. Read
        it BEFORE :meth:`pop`, which forgets the ask."""
        meta = self._meta.get(str(approval_id or ""))
        return str((meta or {}).get("actor") or "")

    def resolve(self, approval_id: str, decision: str, *, actor: str = "app") -> bool:
        """Answer a pending request. False = unknown/expired/already answered,
        or the asker's loop is already gone (a scheduled fire that ended).

        Same-loop answers resolve inline, byte-identical to the pre-v1.209
        behavior. A CROSS-loop answer is marshaled with
        ``call_soon_threadsafe`` and True means "accepted for delivery": the
        authoritative ``fut.done()`` guard runs INSIDE the marshaled callable
        on the future's own loop, because the answered-already race moves with
        the marshal (two concurrent cross-loop answers may both return True;
        exactly one lands, the loser is a no-op — never an exception in
        somebody else's loop).

        ``actor`` (v1.402.0, the autonomy metrics): who answered — ``app``
        (a press in Iron Jarvis, the default), ``phone`` (a reply on the
        user's phone), ``bench`` (the benchmark answering for nobody). The
        FIRST answer that reaches a still-open ask names it."""
        if decision not in DECISIONS:
            return False
        entry = self._pending.get(approval_id)
        if entry is None:
            return False
        meta = self._meta.get(approval_id)
        if meta is not None and not entry[0].done():
            meta.setdefault("actor", _clean_actor(actor))
        return self._deliver(entry, decision)

    @staticmethod
    def _deliver(
        entry: tuple["asyncio.Future[str]", asyncio.AbstractEventLoop],
        decision: str,
    ) -> bool:
        """Set one future to ``decision`` on the loop that owns it — the body
        ``resolve`` has carried since v1.209.0, shared with ``resolve_where``
        so both answer paths marshal identically."""
        fut, home = entry
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:  # a bare-thread caller (Telegram poller shapes)
            current = None
        if current is home:
            # The pre-v1.209 path, unchanged.
            if fut.done():
                return False
            fut.set_result(decision)
            return True
        # Foreign loop/thread: the future may only be resolved on its own loop
        # (set_result from here would not wake the sleeping selector — the
        # user's Allow would land when the timeout fires, ~300s late).
        if fut.done():
            return False  # cheap pre-check; the real guard rides the marshal

        def _deliver() -> None:
            if not fut.done():
                fut.set_result(decision)

        try:
            if home.is_closed():
                return False  # the asker died with its loop — honest no
            home.call_soon_threadsafe(_deliver)
        except RuntimeError:
            # Closed between the check and the call (asyncio.run tearing
            # down): the question no longer has an asker.
            return False
        return True

    def pop(self, approval_id: str) -> "dict[str, Any] | None":
        """The awaiter is done with this id (answered, timed out, or the stream
        died). After this, ``resolve`` honestly reports it unknown. Returns
        what was recorded for it (v1.402.0: its ``actor`` included), or None."""
        self._pending.pop(approval_id, None)
        meta = self._meta.pop(approval_id, None)
        sid = str((meta or {}).get("session_id") or "")
        if sid:
            waited = max(0.0, time.time() - float((meta or {}).get("requested_at") or time.time()))
            self._waited[sid] = self._waited.get(sid, 0.0) + waited
            self._waited.move_to_end(sid)
            while len(self._waited) > WAITED_SESSIONS_MAX:
                self._waited.popitem(last=False)
        return meta

    def waited_s(self, session_id: str | None) -> float:
        """Seconds ``session_id``'s run has spent waiting on asks so far
        (v1.231.0, AE13) — the goal engine subtracts this from an iteration's
        wall-clock charge, because five minutes of an unattended 3 am ask is
        not five minutes of work. ``0.0`` for a session that never asked."""
        return float(self._waited.get(str(session_id or ""), 0.0))

    def pending_count(self) -> int:
        return len(self._pending)

    def pending_ids(self) -> list[str]:
        """Snapshot of the ids currently awaiting an answer (read-only).

        Only IDS — tool/args deliberately never live here (see ``request``),
        so a listing surface reconstructs display metadata from the
        ``approval.requested`` event log instead."""
        return list(self._pending)


# --------------------------------------------------------------------------- #
# Interruptions, measured (v1.402.0, the autonomy metrics)
# --------------------------------------------------------------------------- #
#
# EVERY card or question a lane puts to the person, and how it was settled,
# is published as ``interrupt.asked`` / ``interrupt.answered`` so ``GET
# /autonomy/metrics`` (``eval/autonomy``) can count how often Jarvis stopped
# to ask, how long the person took, and which of those asks could have been
# avoided.
#
# FOR COUNTING ONLY. These two events are never a default alert, never a bell
# row, never a desktop notice and never sent to the phone (pinned in
# ``tests/test_autonomy_metrics_v1402.py``); the asking surfaces keep their own
# frames and ``approval.requested`` exactly as they were. They are published
# with NO session id (the id rides the payload) so a run's live activity list,
# which shows every event tagged with its session, does not grow a second row
# per ask, and the audit timeline leaves them out (``eval/observability``).
#
# NEVER THE ARGUMENTS, NEVER TEXT: a payload carries the tool's NAME, the
# card's kind, where it was asked, the exact call's ``args_hash`` (a hash of
# the real arguments — what the standing grants key on — never the arguments
# themselves) and plain flags. A question's answer is reduced to one boolean
# (``deferred``: the person skipped or handed the choice back) by the asker.
#
# FIRE AND FORGET: the publish runs as its own task, so a card is never held
# up by the event log's write (a busy SQLite retries for up to ~2 s). A
# publish failure is swallowed — a metric never breaks a turn.

#: Who settled an ask. ``app``: a press in Iron Jarvis; ``phone``: a reply on
#: the user's phone; ``grant``: a grant the person made a moment earlier (a
#: sibling woken by "Allow for this run", the sidebar's "allow everything"
#: switch); ``timeout``: nobody answered in time (the night shift's card wait
#: included); ``bench``: the benchmark answering because nobody is there.
ACTORS = ("app", "phone", "grant", "timeout", "bench")
#: What was asked: an approval ``card``, a ``question`` (the model's own
#: ``ask_user``, an app's question), or a ``plan`` to approve.
INTERRUPT_KINDS = ("card", "question", "plan")
#: Where it was asked.
INTERRUPT_SURFACES = ("chat", "build", "agent", "night", "bench")
#: The two metric event types (== ``core.events.EventType.INTERRUPT_*``).
INTERRUPT_ASKED = "interrupt.asked"
INTERRUPT_ANSWERED = "interrupt.answered"

#: The longest a name or an id may be on a payload.
_FACT_CHARS = 120

#: Tasks of in-flight publishes (strong references: an unreferenced task may
#: be collected before it runs).
_NOTE_TASKS: "set[asyncio.Task[Any]]" = set()


def _clean_actor(actor: Any) -> str:
    text = str(actor or "").strip().lower()
    return text if text in ACTORS else "app"


def _short(value: Any) -> str:
    return " ".join(str(value or "").split())[:_FACT_CHARS]


def _publish_later(bus: Any, event_type: str, payload: dict[str, Any]) -> None:
    publish = getattr(bus, "publish", None)
    if publish is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop, no publish — a metric is never worth a new failure

    async def _go() -> None:
        try:
            await publish(event_type, payload)
        except Exception:  # noqa: BLE001 — a metric never breaks a turn
            pass

    try:
        task = loop.create_task(_go())
    except Exception:  # noqa: BLE001
        return
    _NOTE_TASKS.add(task)
    task.add_done_callback(_NOTE_TASKS.discard)


async def flush_interrupt_notes() -> None:
    """Wait for every interrupt event still being published on THIS loop
    (tests, and a benchmark that reads its own event log right after a run)."""
    loop = asyncio.get_running_loop()
    for _ in range(50):
        pending = [t for t in list(_NOTE_TASKS) if not t.done() and t.get_loop() is loop]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


class InterruptNote:
    """One ask being measured: :meth:`asked` when it goes to the person,
    :meth:`answered` ONCE when it settles (a second call is a no-op, so every
    exit of a wait may call it). ``wait_ms`` is measured on this object's own
    monotonic clock, from :meth:`asked` to :meth:`answered`."""

    def __init__(
        self,
        bus: Any,
        *,
        kind: str,
        tool: str,
        surface: str,
        ask_id: str = "",
        thread_id: str = "",
        turn_id: str = "",
        session_id: str = "",
        once_only: bool = False,
        reason: str = "",
        args_hash: str = "",
        thread_for: "Callable[[], str] | None" = None,
    ) -> None:
        self._bus = bus
        #: (review) Where to find the chat LATER when it had no id yet: a new
        #: chat's first question goes out before its first save names the
        #: chat, and the page links the turn to it a moment after
        #: (``POST /chat/turns/{id}/thread``). Asked once, when the ask
        #: settles; never raises.
        self._thread_for = thread_for
        facts: dict[str, Any] = {
            "kind": kind if kind in INTERRUPT_KINDS else "card",
            "tool": _short(tool),
            "surface": surface if surface in INTERRUPT_SURFACES else "chat",
        }
        for key, value in (("ask_id", ask_id), ("thread_id", thread_id), ("turn_id", turn_id),
                           ("session_id", session_id), ("args_hash", args_hash)):
            text = _short(value)
            if text:
                facts[key] = text
        if once_only is True:
            facts["once_only"] = True
            why = str(reason or "").strip()
            if why and len(why) <= 32 and why.replace("-", "_").isidentifier():
                facts["reason"] = why
        self.facts = facts
        self._t0 = time.monotonic()
        self._asked = False
        self._done = False

    def asked(self) -> None:
        if self._asked:
            return
        self._asked = True
        self._t0 = time.monotonic()
        _publish_later(self._bus, INTERRUPT_ASKED, {**self.facts, "at": round(time.time(), 3)})

    def answered(self, decision: str, actor: str, **flags: Any) -> None:
        """Settle it: ``decision`` (the lane's own word: once / conversation
        / deny / tab / always / timeout / stopped, or a question's answered /
        skipped / unanswered / accept / decline …), ``actor`` (:data:`ACTORS`)
        and plain boolean ``flags`` (``deferred``, ``edited``) — only a
        ``True`` flag is written."""
        if self._done:
            return
        self._done = True
        if not self._asked:
            # (review) Never put to the person (a cancel before the card went
            # out): no interruption, so no answer either.
            return
        if "thread_id" not in self.facts and self._thread_for is not None:
            try:
                thread = _short(self._thread_for())
            except Exception:  # noqa: BLE001 — a metric never breaks a turn
                thread = ""
            if thread:
                self.facts["thread_id"] = thread
        payload: dict[str, Any] = {
            **self.facts,
            "decision": _short(decision) or "none",
            "actor": _clean_actor(actor),
            "wait_ms": max(0, int((time.monotonic() - self._t0) * 1000)),
            "at": round(time.time(), 3),
        }
        for key, value in flags.items():
            if value is True and str(key).isidentifier():
                payload[str(key)] = True
        _publish_later(self._bus, INTERRUPT_ANSWERED, payload)


def surface_for(session_id: Any, *, night: Any = None) -> str:
    """Where a STREAM-lane ask was put: a night task's turn ``night``, the
    benchmark's Build turn (``build:bench-…``) ``bench``, a Build session
    ``build``, anything else ``chat``."""
    if night is not None:
        return "night"
    sid = str(session_id or "")
    if sid.startswith("build:bench-"):
        return "bench"
    if sid.startswith("build:"):
        return "build"
    return "chat"


def run_place(session: Any, *, night: Any = None) -> tuple[str, str]:
    """``(surface, thread_id)`` of an AGENT RUN's ask: a helper of a Build
    session (``options.build_session``) asks in that session — ``build``, or
    ``bench`` for the benchmark's; a run a chat started (``options.
    chat_thread_id``) reports to that chat; every other run is ``agent`` with
    no thread. A night task's helper is ``night``."""
    import json as _json

    try:
        raw = getattr(session, "options_json", None) or "{}"
        opts = _json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        if not isinstance(opts, dict):
            opts = {}
    except Exception:  # noqa: BLE001
        opts = {}
    build = str(opts.get("build_session") or "")
    thread = build.split(":", 1)[1] if build.startswith("build:") else str(opts.get("chat_thread_id") or "")
    if night is not None:
        return "night", thread
    if build.startswith("build:bench-"):
        return "bench", thread
    if build.startswith("build:"):
        return "build", thread
    return "agent", thread


def settled_actor(registry: Any, approval_id: str, decision: str) -> str:
    """Who settled ``approval_id`` — read from the registry BEFORE it is
    popped. A wait that ran out is ``timeout`` whoever else was asked; an ask
    nobody named (a registry double, a Stop) reads ``app``."""
    if str(decision or "") == "timeout":
        return "timeout"
    fn = getattr(registry, "actor_of", None)
    try:
        actor = fn(approval_id) if callable(fn) else ""
    except Exception:  # noqa: BLE001
        actor = ""
    return _clean_actor(actor) if actor else "app"


def resolve_as(registry: Any, approval_id: str, decision: str, *, actor: str) -> bool:
    """Answer through ``registry`` naming WHO answered — for a caller that may
    hold a registry double whose ``resolve`` takes no ``actor`` (only the real
    :class:`ChatApprovals` is asked to record one)."""
    if isinstance(registry, ChatApprovals):
        return registry.resolve(approval_id, decision, actor=actor)
    return bool(registry.resolve(approval_id, decision))
