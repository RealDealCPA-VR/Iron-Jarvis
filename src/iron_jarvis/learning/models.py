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
    source: str = "reflection"  # feedback | reflection | preference
    weight: int = 1  # static base priority (preference/feedback = high)
    # Outcome-driven adjustment maintained by the ImprovementEngine: lessons whose
    # sessions beat the baseline gain weight, those that trail it decay. The
    # EFFECTIVE priority used for injection/recall is ``weight + weight_bonus``.
    weight_bonus: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def effective_weight(self) -> float:
        """Static weight plus the outcome-driven bonus (used for ordering)."""
        return self.weight + (self.weight_bonus or 0.0)
