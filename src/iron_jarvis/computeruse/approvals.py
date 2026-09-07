"""Approval queue — the human-in-the-loop gate for sensitive actions.

Sensitive or destructive actions (credentials, payment, PII, delete/buy/pay/…)
never run on the model's say-so. The harness creates an :class:`ApprovalRequest`
row here; a human (via the daemon endpoints) approves or denies it, or an
injected ``approval_resolver`` decides synchronously in tests.
"""

from __future__ import annotations

import json
from datetime import timezone

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .base import Action
from .models import ApprovalRequest


#: How long an APPROVED but unspent approval stays spendable (v1.237.0).
#:
#: THE BUG THIS CLOSES, driven end to end. Chat builds every ToolContext with
#: ``agent_run_id="chat"`` — one constant for every conversation the user will
#: ever have — and :meth:`ApprovalQueue.approved_unconsumed` matched only on
#: ``run_id`` + the action signature. So a card the user approved and the model
#: never came back for stayed spendable FOREVER: days later, in a completely
#: different conversation, an identical ``browser_click`` on "Delete account"
#: found that row, consumed it, and the click landed with the user shown
#: nothing. Consent to one action had authorised the same action in a
#: conversation that had not happened yet.
#:
#: A bound on an approval's AGE is a policy bound, not a performance threshold:
#: it says how long a human's "yes" means yes, and it is measured against the
#: row's own ``created_at``, never against how long anything took. Fifteen
#: minutes is long enough for the user to read a card, approve it and let the
#: model retry, and short enough that the grant is over when the conversation
#: is. Expiry fails in the SAFE direction — the stale grant is ignored and the
#: user is asked again, which is the one outcome nobody can be harmed by.
APPROVAL_MAX_AGE_S = 15 * 60


class ApprovalQueue:
    """Persistence-backed queue of human-approval requests."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_request(
        self, run_id: str, action: Action, reason: str, *, screenshot_b64: str = ""
    ) -> ApprovalRequest:
        """Create a *pending* approval request for ``action`` and persist it.

        ``screenshot_b64``: what the page looked like at request time, so the
        human approves with eyes on the actual screen."""
        req = ApprovalRequest(
            run_id=run_id,
            action_json=json.dumps(action.to_dict(), default=str),
            reason=reason,
            status="pending",
            screenshot_b64=screenshot_b64,
        )
        with session_scope(self.engine) as db:
            db.add(req)
            db.commit()
            db.refresh(req)
        return req

    def _set_status(self, request_id: str, status: str) -> ApprovalRequest | None:
        with session_scope(self.engine) as db:
            req = db.get(ApprovalRequest, request_id)
            if req is None:
                return None
            req.status = status
            db.add(req)
            db.commit()
            db.refresh(req)
            return req

    def approve(self, request_id: str) -> ApprovalRequest | None:
        return self._set_status(request_id, "approved")

    def deny(self, request_id: str) -> ApprovalRequest | None:
        return self._set_status(request_id, "denied")

    def consume(self, request_id: str) -> ApprovalRequest | None:
        """Mark an approval as *consumed* (spent on one action; not replayable)."""
        return self._set_status(request_id, "consumed")

    def approved_unconsumed(
        self,
        run_id: str,
        action: Action,
        *,
        max_age_s: float | None = APPROVAL_MAX_AGE_S,
    ) -> ApprovalRequest | None:
        """Most recent *approved*, not-yet-consumed, UNEXPIRED request for
        ``run_id`` + ``action``.

        The action signature is the same ``json.dumps(action.to_dict())`` stored by
        :meth:`create_request`, so a dashboard approval of the FIRST (pending) call
        unblocks the NEXT identical call (consume-on-use). Returns ``None`` when no
        such approval exists (so a consumed approval can never be replayed).

        ``max_age_s`` (v1.237.0) bounds how old that approval may be — see
        :data:`APPROVAL_MAX_AGE_S` for the conversation-crossing replay it
        closes. ``None`` disables the bound and is for callers that have their
        own scope; nothing in the daemon passes it.

        The age is computed in Python rather than in the WHERE clause because
        SQLite hands these timestamps back without a timezone, and comparing a
        naive column against an aware ``utcnow()`` is exactly the silent
        always-true / always-false comparison an expiry must not have.
        """
        signature = json.dumps(action.to_dict(), default=str)
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(ApprovalRequest)
                .where(ApprovalRequest.run_id == run_id)
                .where(ApprovalRequest.status == "approved")
                .where(ApprovalRequest.action_json == signature)
                .order_by(ApprovalRequest.created_at.desc())
            )
            for req in rows:
                if max_age_s is None or self._age_s(req) <= float(max_age_s):
                    return req
            return None

    @staticmethod
    def _age_s(req: ApprovalRequest) -> float:
        """Seconds since the request was created, tz-normalised.

        A row read back from SQLite is naive and means UTC; one still attached
        to the session it was created in is aware. Treating a naive stamp as
        local time would make an approval look FRESHER or older by the machine's
        UTC offset, which on a negative offset would extend the grant."""
        created = req.created_at
        if created is None:
            return 0.0
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (utcnow() - created).total_seconds()

    def get(self, request_id: str) -> ApprovalRequest | None:
        with session_scope(self.engine) as db:
            return db.get(ApprovalRequest, request_id)

    def pending(self) -> list[ApprovalRequest]:
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(ApprovalRequest).where(ApprovalRequest.status == "pending")
            )
            return list(rows)
