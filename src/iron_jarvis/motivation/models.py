"""Motivation Layer persistence models — standing goals + deliberated proposals.

The Motivation Layer ("the pulse") lets Iron Jarvis hold *standing goals* and,
when the user has opted in, periodically deliberate on the single highest-value
next action toward them. Two SQLModel tables back that loop (auto-created via
``init_db`` once this module is imported, §22):

* :class:`GoalRecord` — a durable intent the system carries forward, with its own
  autonomy dial and rolling action/token budget. Acting is OFF unless the user
  raises the dial AND ``config.autonomy_enabled`` is on.
* :class:`ProposalRecord` — a single deliberated (or event-sourced) candidate
  action. It is a *generalised, pre-execution* review-queue item: unlike the git
  ``ReviewRequest`` (which gates a diff a finished session already produced), a
  proposal gates whether a session should be *spawned at all*. It stays
  ``pending`` until either the goal's dial + risk + budget all permit auto-exec,
  or a human approves it.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

# --- enumerations (kept as plain str sets so TOML/JSON round-trips cleanly) ---

GOAL_SOURCES: tuple[str, ...] = ("user", "inferred", "event")
GOAL_STATUSES: tuple[str, ...] = ("active", "paused", "done", "abandoned")
# Per-goal autonomy dial, ordered least -> most autonomous. ``suggest`` only ever
# proposes; ``act_low`` may auto-execute low-risk actions; ``act_all`` may
# auto-execute low/med-risk actions (high risk ALWAYS needs human approval).
AUTONOMY_LEVELS: tuple[str, ...] = ("suggest", "act_low", "act_all")
RISKS: tuple[str, ...] = ("low", "med", "high")
PROPOSAL_SOURCES: tuple[str, ...] = ("deliberation", "event", "sentinel")
PROPOSAL_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "executed")


class GoalRecord(SQLModel, table=True):
    """A standing goal the system holds and deliberates toward (off by default)."""

    id: str = Field(default_factory=lambda: new_id("goal"), primary_key=True)
    text: str = ""
    source: str = "user"  # user | inferred | event
    category: str = "general"
    priority: int = 3  # 1 (low) .. 5 (high); deliberation favours higher
    autonomy_level: str = "suggest"  # suggest | act_low | act_all
    status: str = "active"  # active | paused | done | abandoned
    # Rolling budget caps (per goal). actions_taken/tokens_spent are the
    # cumulative counters checked against these BEFORE any self-initiated session.
    action_budget: int = 3
    spend_budget: int = 20000  # tokens
    actions_taken: int = 0
    tokens_spent: int = 0
    last_acted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ProposalRecord(SQLModel, table=True):
    """A single candidate next action (generalised pre-execution review item)."""

    id: str = Field(default_factory=lambda: new_id("prop"), primary_key=True)
    goal_id: str | None = Field(default=None, index=True)  # None = system backlog
    title: str = ""
    rationale: str = ""
    action_json: str = "{}"  # {"agent_type": ..., "task": ...}
    risk: str = "med"  # low | med | high
    source: str = "deliberation"  # deliberation | event | sentinel
    status: str = "pending"  # pending | approved | rejected | executed
    session_id: str | None = None  # set once executed
    tokens: int = 0  # tokens the executed session actually spent
    created_at: datetime = Field(default_factory=utcnow)

    def decoded_action(self) -> dict:
        """Parse ``action_json`` into a dict (``agent_type`` + ``task``)."""
        try:
            data = json.loads(self.action_json or "{}")
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}
