"""Skill-learning persistence models — the durable substrate of the skill loop.

``skill_create`` (v1.90.0) lets an agent SAVE a proven approach, but only when
it thinks to. These tables let finished sessions feed a *suggest-only* loop:
qualifying sessions become candidates, candidates are distilled into reviewable
proposals, and every skill use accumulates outcome stats so an underperforming
skill can earn a refinement draft.

Four SQLModel tables (auto-created via ``init_db`` once this package is
imported, §22 — plain new tables, nothing for the additive-column reconciler):

* :class:`SkillCandidateRecord` — one row per qualifying finished session: a
  deterministic "this might be worth a skill" marker minted by
  ``observe_session`` (pure DB, no model call). ``attempts`` bounds the retry
  loop when distillation replies are unusable.
* :class:`SkillProposalRecord` — a reviewable draft SKILL.md (agentskills.io
  format: YAML frontmatter + markdown body) minted by the model-backed distill
  step. Nothing lands in the skills directory until it is approved.
* :class:`SkillUseRecord` — one row per (session, skill) use, derived
  post-session from successful ``skill_load`` tool invocations.
* :class:`SkillStatRecord` — rolling per-skill outcome stats (how sessions
  that loaded this skill actually scored), keyed by skill name.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class SkillCandidateRecord(SQLModel, table=True):
    """A finished session flagged as skill-worthy (create) or skill-hurting
    (refine) by the deterministic gate. Cheap DB state; the model only ever
    sees candidates during an explicit distill sweep."""

    id: str = Field(default_factory=lambda: new_id("skc"), primary_key=True)
    session_id: str = Field(index=True)
    #: The session's task text (the seed of a create-lane prompt).
    task: str = ""
    kind: str = "create"  # create | refine
    #: Refine lane only: the underperforming skill this candidate targets.
    skill_name: str = ""
    #: Normalized dedup key — ``create::<normalized task>`` /
    #: ``refine::<skill name>`` — so the same procedure is proposed once.
    signature: str = Field(default="", index=True)
    status: str = "pending"  # pending | distilled | dismissed
    #: Distillation attempts that produced an unusable reply. At
    #: ``_MAX_ATTEMPTS`` (3) the candidate is dismissed instead of retried.
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class SkillProposalRecord(SQLModel, table=True):
    """A reviewable draft skill (suggest-only — approval writes it to disk)."""

    id: str = Field(default_factory=lambda: new_id("skp"), primary_key=True)
    kind: str = "create"  # create | refine
    skill_name: str = ""
    description: str = ""
    #: The FULL draft SKILL.md content (YAML frontmatter + markdown body).
    body_md: str = ""
    #: Refine lane: the current on-disk body at distill time, kept so the
    #: dashboard can show a before/after diff.
    prev_body_md: str = ""
    #: JSON list[str] of the session ids this proposal was distilled from.
    source_session_ids: str = "[]"
    #: Carried from the candidate; a REJECTED proposal's signature joins the
    #: create-lane suppression set so the same procedure isn't re-proposed.
    signature: str = Field(default="", index=True)
    status: str = "pending"  # pending | approved | rejected
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None


class SkillUseRecord(SQLModel, table=True):
    """One (session, skill) use — derived from successful ``skill_load`` rows."""

    id: str = Field(default_factory=lambda: new_id("sku"), primary_key=True)
    session_id: str = Field(index=True)
    skill_name: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class SkillStatRecord(SQLModel, table=True):
    """Rolling outcome stats for one skill — how its sessions actually scored."""

    skill_name: str = Field(primary_key=True)
    use_count: int = 0
    score_sum: float = 0.0  # sum of session scores while this skill was loaded
    success_count: int = 0
    last_used_at: datetime | None = None
