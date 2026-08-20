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
  success / a recent-scores window) for the quality-trend read. Keyed by the
  ROSTER NAME, not by the builtin enum — see the class docstring.

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
    #: The ROSTER NAME this run is attributed to (v1.193.0): ``"builder"`` for a
    #: builtin, ``"custom:<name>"`` for a dynamic agent, ``"remote:<name>"`` for
    #: a remote one. Additive nullable-ish column (auto-reconciled); EMPTY on
    #: every pre-v1.193.0 row and on any run whose name could not be resolved,
    #: which reads as "the bare ``agent_type``" — never as a distinct agent.
    #: ``agent_type`` keeps meaning the BUILTIN type the run executed as, so the
    #: substrate still answers "how do builder-shaped runs do?" as well as "how
    #: does *this teammate* do?".
    agent_name: str = ""
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
    """Rolling per-agent quality stats (for the quality-trend dashboard read).

    THE KEY IS THE ROSTER NAME (v1.193.0), not the builtin ``AgentType``. Until
    then every dynamic agent's runs were folded into its BASE type and every
    remote agent had no history at all, so the roster block a supervisor reads
    said "(no runs yet)" forever for every teammate the user created — it could
    pick the right ROLE but never the right AGENT.

    NO MIGRATION, NO ORPHANS: a builtin's roster name IS its type string
    (``"builder"``), so every existing row is already keyed correctly and keeps
    its history verbatim. Only the new namespaces (``custom:<name>`` /
    ``remote:<name>``) are new keys. The COLUMN keeps its ``agent_type`` name on
    purpose: it is the primary key, and renaming it would need the bespoke
    migration this scheme exists to avoid.
    """

    agent_type: str = Field(primary_key=True)  # roster name; see the docstring
    session_count: int = 0
    score_sum: float = 0.0
    success_count: int = 0
    recent_json: str = "[]"  # JSON list[float] of the most recent scores (capped)
    last_at: datetime | None = None
