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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

#: What a resolution may say. "once" grants THIS call; "conversation" grants
#: the rest of the turn too (and the client re-arms the tool for later turns —
#: the existing "+"-menu machinery, not a second grant store); "deny" refuses.
DECISIONS = ("once", "conversation", "deny")

#: How long a turn will hold for an answer before denying honestly. Long
#: enough to read what the tool wants and decide; short enough that a stream
#: whose user walked away ends instead of pinning a connection forever.
APPROVAL_TIMEOUT_S = 180.0


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

    def request(
        self,
        tool: str,
        args: dict[str, Any] | None,
        session_id: str | None = None,
    ) -> tuple[str, "asyncio.Future[str]"]:
        """File one request; returns ``(id, future)``. The future resolves to a
        DECISIONS value. ``tool``/``args`` are kept only as display/matching
        metadata (the asker passes already-redacted args); the SSE frame /
        event still carries them to the client that answers. ``session_id``
        (v1.227.0) names the run that is waiting, so ``pending_for`` and
        ``resolve_where`` can scope to it; a chat-lane ask passes none."""
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
        return approval_id, fut

    def pending_for(self, session_id: str | None) -> list[dict[str, Any]]:
        """The asks a session is currently waiting on, oldest first (v1.227.0).

        Each entry: ``{approval_id, tool, args, requested_at}``. Only asks
        filed WITH that session id match — a chat-lane ask (no session) is
        never attributed to a run, and an empty/None id answers ``[]``."""
        sid = str(session_id or "")
        if not sid:
            return []
        rows = [
            {
                "approval_id": m["approval_id"],
                "tool": m["tool"],
                "args": m["args"],
                "requested_at": m["requested_at"],
            }
            for m in self._meta.values()
            if m.get("session_id") == sid
        ]
        rows.sort(key=lambda m: m["requested_at"])
        return rows

    def resolve_where(
        self, session_id: str | None, tool: "str | Iterable[str]", decision: str
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
        marshal, done-guard inside the marshal)."""
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
            if entry is not None and self._deliver(entry, decision):
                n += 1
        return n

    def resolve(self, approval_id: str, decision: str) -> bool:
        """Answer a pending request. False = unknown/expired/already answered,
        or the asker's loop is already gone (a scheduled fire that ended).

        Same-loop answers resolve inline, byte-identical to the pre-v1.209
        behavior. A CROSS-loop answer is marshaled with
        ``call_soon_threadsafe`` and True means "accepted for delivery": the
        authoritative ``fut.done()`` guard runs INSIDE the marshaled callable
        on the future's own loop, because the answered-already race moves with
        the marshal (two concurrent cross-loop answers may both return True;
        exactly one lands, the loser is a no-op — never an exception in
        somebody else's loop)."""
        if decision not in DECISIONS:
            return False
        entry = self._pending.get(approval_id)
        if entry is None:
            return False
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

    def pop(self, approval_id: str) -> None:
        """The awaiter is done with this id (answered, timed out, or the stream
        died). After this, ``resolve`` honestly reports it unknown."""
        self._pending.pop(approval_id, None)
        self._meta.pop(approval_id, None)

    def pending_count(self) -> int:
        return len(self._pending)

    def pending_ids(self) -> list[str]:
        """Snapshot of the ids currently awaiting an answer (read-only).

        Only IDS — tool/args deliberately never live here (see ``request``),
        so a listing surface reconstructs display metadata from the
        ``approval.requested`` event log instead."""
        return list(self._pending)
