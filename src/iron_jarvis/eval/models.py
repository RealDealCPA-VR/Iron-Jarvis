"""Evaluation persistence model (SPEC §29 Evaluation Engine).

One row records the derived quality + observability metrics for a single agent
run, computed from the persisted Session / AgentRun / ToolInvocation rows.
Stored as a SQLModel table so it auto-creates via ``init_db`` once imported.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class Evaluation(SQLModel, table=True):
    """Scored outcome of a session/agent-run (SPEC §29)."""

    id: str = Field(default_factory=lambda: new_id("eval"), primary_key=True)
    session_id: str = Field(index=True)
    agent_run_id: str = Field(index=True)
    completion: float = 0.0
    tool_success_rate: float = 1.0
    tool_calls: int = 0
    step_count: int = 0
    latency_s: float = 0.0
    cost: float = 0.0
    review_acceptance: float | None = None
    created_at: datetime = Field(default_factory=utcnow)
