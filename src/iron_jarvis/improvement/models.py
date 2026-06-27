"""ImprovementEngine persistence models — closing the measurement->learning loop.

The Evaluation Engine (§29) scores every session, but nothing consumed those
scores: a lesson lived forever regardless of whether it helped. These tables let
measured OUTCOMES feed back into lesson weighting and per-agent quality trends.

Three SQLModel tables (auto-created via ``init_db`` once this package is imported,
§22):

* :class:`OutcomeRecord` — one row per finished session: its derived quality
  ``score`` + ``success`` flag, plus the lessons that were active and the tools
  that ran. The durable attribution substrate.
* :class:`LessonStatRecord` — rolling per-lesson outcome stats (how sessions that
  carried this lesson actually scored). Drives the lesson's effective weight.
* :class:`AgentStatRecord` — rolling per-agent quality (count / score sum /
  success / a recent-scores window) for the quality-trend read.

Everything here is plain DB state — written cheaply on every session completion,
never with a model call.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class OutcomeRecord(SQLModel, table=True):
    """The measured outcome of one finished session (attribution substrate)."""

    id: str = Field(default_factory=lambda: new_id("outcome"), primary_key=True)
    session_id: str = Field(index=True)
    agent_type: str = "builder"
    score: float = 0.0  # derived composite quality in [0, 1]
    success: bool = False
    lessons_applied: str = "[]"  # JSON list[str] of lesson ids active for the run
    tools_used: str = "[]"  # JSON list[str] of tool names invoked
    created_at: datetime = Field(default_factory=utcnow)


class LessonStatRecord(SQLModel, table=True):
    """Rolling outcome stats for a single lesson — how its sessions scored."""

    lesson_id: str = Field(primary_key=True)
    applied_count: int = 0
    score_sum: float = 0.0  # sum of session scores while this lesson was active
    success_count: int = 0
    last_applied_at: datetime | None = None


class AgentStatRecord(SQLModel, table=True):
    """Rolling per-agent quality stats (for the quality-trend dashboard read)."""

    agent_type: str = Field(primary_key=True)
    session_count: int = 0
    score_sum: float = 0.0
    success_count: int = 0
    recent_json: str = "[]"  # JSON list[float] of the most recent scores (capped)
    last_at: datetime | None = None
