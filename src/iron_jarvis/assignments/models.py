"""The assignment row — a job GIVEN to an agent that waits for it (v1.296.0).

There was no task entity before this wave: the project Board is ``Session``
rows, and a session is a RUN, not a job. An assignment is the job: who it is
for (``assignee``, a roster name), what it is (``task``), why it exists
(``reason``), and how far it has got (``status``). The dispatcher
(:mod:`.dispatcher`) turns a queued assignment into a session when the agent
is free, and the session's end settles the assignment through the
orchestrator's post-run hook.

Importing this module registers the table on the shared SQLModel metadata
BEFORE ``init_db`` runs (``core.db._LATE_MODEL_MODULES`` imports it), so the
table is created on platform boot. Every column is a plain str/int/datetime:
no enum column, so a status this process does not know can never poison a
read on a newer database.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: The lifecycle. ``queued`` waits for the dispatcher; ``claimed`` is the
#: moment between the atomic claim and a session row existing; ``running``
#: has a session; the four terminal words say how it ended. ``blocked`` is the
#: breaker's word (three failed runs in a row) and is reversible by
#: :meth:`~.store.AssignmentStore.unblock`.
QUEUED = "queued"
CLAIMED = "claimed"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
BLOCKED = "blocked"
CANCELLED = "cancelled"
STATUSES = (QUEUED, CLAIMED, RUNNING, DONE, FAILED, BLOCKED, CANCELLED)
#: Statuses that count as "live" — an idempotency key may exist once among
#: them per assignee, and a requeue at boot looks only at claimed/running.
LIVE_STATUSES = (QUEUED, CLAIMED, RUNNING)
TERMINAL_STATUSES = (DONE, FAILED, BLOCKED, CANCELLED)

#: Length of the derived title (the task's first line, clipped).
TITLE_CHARS = 80


def derive_title(task: str) -> str:
    """The first non-empty line of ``task``, collapsed and clipped to 80."""
    for line in str(task or "").splitlines():
        text = " ".join(line.split())
        if text:
            if len(text) > TITLE_CHARS:
                return text[: TITLE_CHARS - 1].rstrip() + "…"
            return text
    return ""


class AssignmentRecord(SQLModel, table=True):
    __tablename__ = "assignment"

    id: str = Field(default_factory=lambda: new_id("asg"), primary_key=True)
    project_id: str = Field(default="", index=True)
    #: A roster name: ``"builder"`` (a builtin type) or ``"custom:<slug>"``.
    #: Remotes are refused at create in this wave.
    assignee: str = Field(default="", index=True)
    task: str = ""
    #: First line of the task, ≤ 80 chars; derived when the caller gave none.
    title: str = ""
    #: Higher runs first; ties go to the oldest.
    priority: int = 0
    status: str = Field(default=QUEUED, index=True)
    #: Who queued it: "user" | "agent:<session_id>" | "project" | "api" |
    #: "retry:<old id>".
    source: str = "user"
    #: One plain line: why it exists.
    reason: str = ""
    #: JSON object: allow_tools, workspace_root, max_steps, provider, model —
    #: all optional.
    payload_json: str = "{}"
    #: A non-empty key is unique per assignee among LIVE statuses — enforced
    #: in store code (a SQLite partial unique index is awkward to reconcile).
    idempotency_key: str = Field(default="", index=True)
    #: How many creates with the same live key folded into this row.
    coalesced_count: int = 0
    attempts: int = 0
    #: Failed runs in a row; reset by ``unblock``.
    failure_count: int = 0
    blocked_reason: str = ""
    #: Why the dispatcher is NOT starting it right now — informational,
    #: cleared when it starts.
    held_reason: str = ""
    #: Agent-queued-by-agent nesting: 0 = a person or the API, 1 = an agent,
    #: 2 = an agent that was itself running an assignment. Capped at 2.
    depth: int = 0
    claim_token: str = ""
    #: The latest session that ran it.
    session_id: str = Field(default="", index=True)
    last_error: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)
