"""The night review's record: ONE row per night (17-a).

A LATE model (``core.db._LATE_MODEL_MODULES``), so a fresh install and an
upgraded one both get the table on boot.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import utcnow

#: The states of a night's review.
WAITING, RUNNING, DONE, SKIPPED = "waiting", "running", "done", "skipped"


class NightReviewRecord(SQLModel, table=True):
    """What one night's review did, from its own steps (never a model's
    words about itself).

    * ``night``: the LOCAL date the night window opened (``harness.
      night_morning.night_key``): 01:30 on the 21st is the night of the 20th.
    * ``state``: ``waiting`` (it began, and stopped before it finished: the
      PC became busy, the window closed, Emergency stop, the setting;
      ``skipped_why`` keeps why it stopped), ``running``, ``done``, or
      ``skipped`` (it made nothing because every request it would have sent
      was refused, so no model was asked; ``skipped_why`` says why in one
      sentence, ``needs_you`` whether only the owner can change that). A
      night where one agent was refused and another was reviewed is
      ``done``: the refusal is in ``ran_json``. A night whose window closed
      while it was ``waiting`` ends ``done`` with what it ran (never
      ``skipped``: the jobs it never reached might have asked a model).
    * ``ran_json``: ``[{proposer, target, result, reason, key}]``, one per
      suggestion maker it ran (``result``: made | nothing | refused | named),
      and a last ``cut off`` entry when the window closed on it.
    * ``made_json``: ``[{kind, ref, why}]``: the suggestions it made, by the
      Improvements kind and the suggestion's own id, and why each was made.
    * ``requests``: model requests spent (the budget's tally for the night).
    * ``notified_at``: the night's ONE bell row was claimed (claim first,
      given back when the publish fails).
    """

    __tablename__ = "nightreview"

    night: str = Field(primary_key=True)
    state: str = Field(default=WAITING, index=True)
    skipped_why: str = ""
    needs_you: bool = False
    ran_json: str = "[]"
    made_json: str = "[]"
    requests: int = 0
    notified_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


__all__ = ["DONE", "NightReviewRecord", "RUNNING", "SKIPPED", "WAITING"]
