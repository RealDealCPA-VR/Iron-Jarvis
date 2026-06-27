"""Approval queue — the human-in-the-loop gate for sensitive actions.

Sensitive or destructive actions (credentials, payment, PII, delete/buy/pay/…)
never run on the model's say-so. The harness creates an :class:`ApprovalRequest`
row here; a human (via the daemon endpoints) approves or denies it, or an
injected ``approval_resolver`` decides synchronously in tests.
"""

from __future__ import annotations

import json

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from .base import Action
from .models import ApprovalRequest


class ApprovalQueue:
    """Persistence-backed queue of human-approval requests."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_request(
        self, run_id: str, action: Action, reason: str
    ) -> ApprovalRequest:
        """Create a *pending* approval request for ``action`` and persist it."""
        req = ApprovalRequest(
            run_id=run_id,
            action_json=json.dumps(action.to_dict(), default=str),
            reason=reason,
            status="pending",
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
        self, run_id: str, action: Action
    ) -> ApprovalRequest | None:
        """Most recent *approved*, not-yet-consumed request for ``run_id`` + ``action``.

        The action signature is the same ``json.dumps(action.to_dict())`` stored by
        :meth:`create_request`, so a dashboard approval of the FIRST (pending) call
        unblocks the NEXT identical call (consume-on-use). Returns ``None`` when no
        such approval exists (so a consumed approval can never be replayed).
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
            return rows.first()

    def get(self, request_id: str) -> ApprovalRequest | None:
        with session_scope(self.engine) as db:
            return db.get(ApprovalRequest, request_id)

    def pending(self) -> list[ApprovalRequest]:
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(ApprovalRequest).where(ApprovalRequest.status == "pending")
            )
            return list(rows)
