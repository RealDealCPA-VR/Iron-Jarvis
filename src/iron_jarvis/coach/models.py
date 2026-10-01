"""The coach's proposal row (v1.297.0).

One row per proposed instruction edit for a CUSTOM agent. ``before`` is the
instructions text at mint time, so ``accept`` can refuse honestly when the
agent was edited in between (``stale``); ``signature`` dedupes — a pending
proposal with the same signature is not minted again, a DECLINED one
suppresses the same signature for 14 days.

Importing this module registers the table on the shared SQLModel metadata.
``core.db._LATE_MODEL_MODULES`` imports it BEFORE ``init_db`` runs (the
v1.151.2 lesson: a lazily created table lands on no real install). Every
column is a plain str/datetime — no enum column, so a status this process
does not know can never poison a read on a newer database.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

PENDING = "pending"
ACCEPTED = "accepted"
DECLINED = "declined"
STALE = "stale"
STATUSES = (PENDING, ACCEPTED, DECLINED, STALE)

KIND_INSTRUCTIONS = "instructions"
KIND_NOTE = "note"
KINDS = (KIND_INSTRUCTIONS, KIND_NOTE)

#: How long a declined signature stays suppressed.
DECLINE_SUPPRESS_DAYS = 14
#: The rationale is a citation line, not an essay.
RATIONALE_CHARS = 600


class CoachProposalRecord(SQLModel, table=True):
    __tablename__ = "coachproposal"

    id: str = Field(default_factory=lambda: new_id("coach"), primary_key=True)
    #: The roster name (``custom:<slug>``) — never a builtin.
    agent: str = Field(default="", index=True)
    #: ``instructions`` (an edit to the system prompt) | ``note``.
    kind: str = KIND_INSTRUCTIONS
    #: The slug the registry keys on.
    target: str = ""
    #: The instructions at mint time (what ``after`` was written against).
    before: str = ""
    #: The proposed full instructions text.
    after: str = ""
    #: ≤ 600 chars; cites the categories and the session ids.
    rationale: str = ""
    #: JSON list of clusters: [{category, count, evidence, weight}].
    evidence_json: str = "[]"
    #: sha256(agent + sorted categories + sorted session ids) — the dedupe key.
    signature: str = Field(default="", index=True)
    status: str = Field(default=PENDING, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
