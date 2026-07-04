"""Computer-Use persistence models.

Two SQLModel tables, registered on the shared metadata when this module is
imported (so ``init_db`` creates them, §22):

* :class:`ComputerUseRun`  — one harness run: task, terminal status, step count,
  and the full JSON trace (actions / results / screenshot refs / errors).
* :class:`ApprovalRequest` — a pending/approved/denied human-approval gate for a
  sensitive or destructive action.

Both are append-mostly audit rows: nothing here decrypts secrets or stores
credentials.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: Terminal/lifecycle statuses for a run.
RUN_STATUSES = ("running", "completed", "failed", "blocked", "awaiting_approval")
#: Lifecycle statuses for an approval request. ``consumed`` marks an approval
#: that has been spent on a single action (consume-on-use), so a dashboard
#: approval cannot be replayed by a later identical action.
APPROVAL_STATUSES = ("pending", "approved", "denied", "consumed")


class ComputerUseRun(SQLModel, table=True):
    """A single Computer-Use harness run and its recorded trace."""

    id: str = Field(default_factory=lambda: new_id("curun"), primary_key=True)
    task: str = ""
    status: str = "running"  # running|completed|failed|blocked|awaiting_approval
    steps: int = 0
    trace_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class ApprovalRequest(SQLModel, table=True):
    """A human-approval gate for a sensitive/destructive action (§7 consent)."""

    id: str = Field(default_factory=lambda: new_id("appr"), primary_key=True)
    run_id: str = Field(index=True)
    action_json: str = "{}"
    reason: str = ""
    status: str = "pending"  # pending|approved|denied|consumed
    #: What the page looked like when approval was requested (PNG base64) — the
    #: dashboard shows the human the ACTUAL screen they are approving.
    screenshot_b64: str = ""
    created_at: datetime = Field(default_factory=utcnow)
