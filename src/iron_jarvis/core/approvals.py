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
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

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
        self._pending: dict[str, asyncio.Future[str]] = {}

    def request(self, tool: str, args: dict[str, Any] | None) -> tuple[str, "asyncio.Future[str]"]:
        """File one request; returns ``(id, future)``. The future resolves to a
        DECISIONS value. ``tool``/``args`` are not stored here — the SSE frame
        carries them to the one client that can answer, and a registry holding
        argument payloads would just be a second place secrets could linger."""
        approval_id = f"apr_{secrets.token_hex(8)}"
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = fut
        return approval_id, fut

    def resolve(self, approval_id: str, decision: str) -> bool:
        """Answer a pending request. False = unknown/expired/already answered."""
        if decision not in DECISIONS:
            return False
        fut = self._pending.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    def pop(self, approval_id: str) -> None:
        """The awaiter is done with this id (answered, timed out, or the stream
        died). After this, ``resolve`` honestly reports it unknown."""
        self._pending.pop(approval_id, None)

    def pending_count(self) -> int:
        return len(self._pending)

    def pending_ids(self) -> list[str]:
        """Snapshot of the ids currently awaiting an answer (read-only).

        Only IDS — tool/args deliberately never live here (see ``request``),
        so a listing surface reconstructs display metadata from the
        ``approval.requested`` event log instead."""
        return list(self._pending)
