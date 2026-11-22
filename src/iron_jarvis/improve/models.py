"""The night review's record: ONE row per night (17-a); the result of each
change the owner accepted: ONE row per change (18-a).

A LATE model (``core.db._LATE_MODEL_MODULES``), so a fresh install and an
upgraded one both get the tables on boot.
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


class ChangeResultRecord(SQLModel, table=True):
    """The result of ONE change the owner accepted (18-a, ``improve/
    results``): its before and after, counted from the records by
    ``improve/metrics`` (never a model's words about itself).

    * ``id``: ``<kind>:<ref>``, so measuring again updates the same row.
    * ``kind``: ``coach`` (an agent's instructions), ``skill`` (a new skill),
      ``skill_refine`` (an improved skill), ``habit`` (a working habit for a
      team member); 18-c adds ``lesson``, ``preference``, ``tool``,
      ``extension``, ``self_fix``.
    * ``ref``: the accepted suggestion's own id; ``target``: what it changed
      (the roster name, the skill's name, the member's name); ``about``: one
      line of what it was (a habit's own words), "" when the title says it.
    * ``accepted_at``: the split point (naive UTC). A task belongs to the
      side its own START is on.
    * ``metric``: ``hands_off`` | ``ended_well`` | ``team_record``.
    * ``before_json`` / ``after_json``: the two sides (``{n, good, ...}``;
      a habit's: the Model team's own side view). ``{}`` = never measured:
      NO numbers, never zeros.
    * ``state``: ``waiting`` (not measured yet: a new row, or every read of
      its record so far failed) | ``not_enough`` (under 5 tasks on a side) |
      ``ready`` | ``not_measured`` (18-c: a kind with nothing honest to
      count).
    * ``verdict``: ``better`` | ``same`` | ``worse`` | "" (no comparison: a
      new skill has no before; a habit says only ``worse``). SHOWN, never
      acted on (18-b offers the undo; nothing here undoes anything).
    * ``undo_json``: the way back for that kind (18-b writes it).
    * ``seen_at``: when the owner was told of a worse result (18-b).
    * ``checked_at``: when it was last measured.
    """

    __tablename__ = "changeresult"

    id: str = Field(primary_key=True)
    kind: str = Field(default="", index=True)
    ref: str = ""
    target: str = ""
    about: str = ""
    accepted_at: datetime = Field(default_factory=utcnow, index=True)
    metric: str = ""
    before_json: str = "{}"
    after_json: str = "{}"
    state: str = Field(default=WAITING, index=True)
    verdict: str = ""
    undo_json: str = "{}"
    seen_at: datetime | None = None
    checked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ChangeResultCheck(SQLModel, table=True):
    """The LAST count of the results (18-a, ``improve/results.refresh``):
    ONE row (id ``last``), written by every count, the night review's and
    the daily one's alike. ``outcome_json``: ``{unreadable: [kinds whose
    store or record could not be read], not_read: [result ids whose record
    could not be read], stores: [kinds whose STORE could not be read]}``.
    So a record that keeps failing is NAMED on Improvements and in chat
    after the count that met it, and the daily pass counts again instead of
    calling the day covered."""

    __tablename__ = "changeresultcheck"

    id: str = Field(default="last", primary_key=True)
    at: datetime = Field(default_factory=utcnow)
    outcome_json: str = "{}"


__all__ = ["ChangeResultCheck", "ChangeResultRecord", "DONE", "NightReviewRecord", "RUNNING", "SKIPPED", "WAITING"]
