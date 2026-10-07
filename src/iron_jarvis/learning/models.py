"""Learning persistence models — the durable substrate of the self-correcting loop.

Two SQLModel tables (auto-created via ``init_db`` once imported, §22):

* :class:`FeedbackRecord` — a thumbs up/down (+ optional comment) the user left on
  a past session. The raw signal.
* :class:`LessonRecord` — a distilled, reusable instruction ("lesson") the agent
  carries forward. Lessons are what get injected into every future system prompt,
  so the agent self-corrects and feels like it remembers how you work.

A lesson's ``weight`` and ``source`` decide injection priority: higher weight and
``preference``/``feedback`` sources are surfaced first.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class FeedbackRecord(SQLModel, table=True):
    """A single piece of user feedback on a past session (§29 complements eval)."""

    id: str = Field(default_factory=lambda: new_id("fb"), primary_key=True)
    session_id: str = Field(index=True)
    rating: str = "neutral"  # up | down | neutral
    comment: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class LessonRecord(SQLModel, table=True):
    """A distilled, reusable lesson injected into future prompts (self-correction)."""

    id: str = Field(default_factory=lambda: new_id("lesson"), primary_key=True)
    text: str = ""
    scope: str = "user"  # user | project
    # feedback | reflection | preference | distilled (model-compacted reflections)
    source: str = "reflection"
    weight: int = 1  # static base priority (preference/feedback = high)
    # Outcome-driven adjustment maintained by the ImprovementEngine: lessons whose
    # sessions beat the baseline gain weight, those that trail it decay. The
    # EFFECTIVE priority used for injection/recall is ``weight + weight_bonus``.
    weight_bonus: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    # -- v1.305.0: every preference has a STATUS (additive columns; an older
    # DB gains them as NULL through core.db's additive reconciler). ONLY a
    # confirmed row reaches any prompt. NULL reads as "confirmed" — normalised
    # in ONE place, :func:`lesson_status`, never rewritten on disk.
    #: "confirmed" | "proposed" (noticed, waiting for the user) | "declined"
    #: (the user said "not this" — final: never proposed again).
    status: str | None = "confirmed"
    #: "said" (the user stated it) | "noticed" (a repeated correction) | NULL.
    origin: str | None = None
    #: JSON list of {quote (<=160 chars), at, where, link?} — what was noticed.
    evidence_json: str | None = None
    #: ``learning.corrections.signature`` of the preference — the key the
    #: never-ask-again rule matches on.
    signature: str | None = None
    #: When the user kept / declined it (NULL for rows nobody decided).
    decided_at: datetime | None = None

    @property
    def effective_weight(self) -> float:
        """Static weight plus the outcome-driven bonus (used for ordering)."""
        return self.weight + (self.weight_bonus or 0.0)


#: The three statuses a lesson can carry (v1.305.0).
STATUS_CONFIRMED = "confirmed"
STATUS_PROPOSED = "proposed"
STATUS_DECLINED = "declined"
STATUSES = (STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_DECLINED)


def lesson_status(row) -> str:
    """The status of ``row`` with NULL / unknown read as ``"confirmed"`` — the
    ONE place an older row's missing status is normalised (never on disk)."""
    raw = getattr(row, "status", None)
    return raw if raw in STATUSES else STATUS_CONFIRMED
