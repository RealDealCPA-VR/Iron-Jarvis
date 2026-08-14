"""Worklist persistence model (v1.174.0 — THE AGENT FINISHES THE JOB).

THE FAILURE THIS EXISTS FOR. A real run — *"rename all files in this folder to
a name that is more appropriate given the content"* over 26 entries — spent 12
steps and 18 tool calls, renamed ZERO files, and read three of the same
documents twice. Everything it learned lived in the transcript, so hitting the
step ceiling threw all of it away and a re-run started from nothing.

A :class:`WorklistItem` is ONE unit of that job, durable in the database
instead of in the transcript: which files exist, which are claimed, which are
finished, and — critically — WHAT each finished one turned into. A resumed run
therefore processes only what is still pending, and a re-run does no work
twice.

SCOPE is the department, exactly like :class:`~iron_jarvis.blackboard.models.
BlackboardRecord`: ``board_id`` is the ROOT session id
(:func:`iron_jarvis.blackboard.store.resolve_board_id`), so a supervisor and
every subagent it delegates to share ONE list while an unrelated task's list is
invisible. Chunked delegation depends on that: two children must never claim
the same item, which is only meaningful if they are looking at the same list.

TWO NORMALIZED COLUMNS, and they are load-bearing rather than tidy:

* ``key_norm`` — the identity the uniqueness constraint and every lookup use.
  ``worklist_add`` is therefore IDEMPOTENT: re-adding a key that is already
  tracked never resets its status, which is the whole resume property.
* ``result_norm`` — where a DONE item ended up. A rename job's re-survey sees
  the NEW filenames, which are not keys of anything; without this column each
  renamed file would look like a brand-new unit of work and the "finished" job
  would run forever. Matching a proposed key against completed items' results
  is what makes the second run a no-op.

``result_sha256``/``result_size`` copy :class:`~iron_jarvis.core.models.
UndoJournal`'s discipline: a claim of "done" is checkable against the disk
later ("done" vs "done, but the file it produced is gone" — STALE), instead of
being taken on faith from prose.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: The four states an item can be in. Deliberately plain strings on a plain
#: ``str`` column rather than a SQLAlchemy ``Enum`` type: the claim is a
#: compare-and-swap ``UPDATE ... WHERE status = 'pending'`` issued as one
#: statement (see :meth:`~iron_jarvis.worklist.store.WorklistStore.claim`), and
#: a plain column keeps that statement obvious in every dialect. Validation
#: happens at the tool boundary, where a model's typo can be answered honestly.
PENDING = "pending"
DOING = "doing"
DONE = "done"
FAILED = "failed"

#: Every status, in report order. ``STATUSES`` is the single source of truth —
#: the tools validate against it and the summary counts each one.
STATUSES: tuple[str, ...] = (PENDING, DOING, DONE, FAILED)

#: Terminal states: an item here is not handed out by ``worklist_next``.
TERMINAL: frozenset[str] = frozenset({DONE, FAILED})


def normalize_key(key: str) -> str:
    """The comparison form of an item key.

    Item keys are almost always FILE PATHS on a Windows box, so three things
    have to stop being differences: surrounding whitespace, the separator
    flavour (``C:\\a\\b`` and ``C:/a/b`` are one file), and case
    (``INVOICE.PDF`` and ``invoice.pdf`` are one file). Case folding is applied
    on EVERY platform on purpose. It is the safer failure: on a case-sensitive
    filesystem two genuinely distinct names could collapse into one item —
    while the alternative, on the user's actual machine, is re-processing a
    file the job already finished, which is precisely the bug being fixed.

    Also collapses repeated separators and drops a single trailing one, so
    ``C:/a//b/`` and ``C:/a/b`` are the same unit of work.
    """
    text = (key or "").strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text.casefold()


class WorklistItem(SQLModel, table=True):
    """One durable unit of work on a department's worklist."""

    __table_args__ = (
        # The identity of an item within its department. This constraint is what
        # makes `worklist_add` idempotent at the STORAGE layer and not merely by
        # convention — a second survey of the same folder cannot duplicate a row
        # even if the read-then-write in the store were ever raced.
        UniqueConstraint("board_id", "key_norm", name="uq_worklist_board_key"),
    )

    id: str = Field(default_factory=lambda: new_id("wl"), primary_key=True)
    #: Department scope — the ROOT session id shared by a supervisor and every
    #: subagent it delegates to (blackboard.store.resolve_board_id).
    board_id: str = Field(index=True)
    #: The item as the agent named it (a path, usually). Shown verbatim.
    key: str = ""
    #: :func:`normalize_key` of ``key`` — every lookup and the uniqueness
    #: constraint use THIS, never the raw text.
    key_norm: str = Field(default="", index=True)
    #: Optional human label ("1099-INT from Vanguard"), for the UI and reports.
    label: str = ""
    status: str = Field(default=PENDING, index=True)
    #: The agent's own words about this item — why it failed, what it became.
    note: str = ""
    #: agent_run_id currently holding the claim (empty once terminal).
    claimed_by: str = ""
    claimed_at: datetime | None = None
    #: Opaque per-claim token. ``claim`` stamps it in the same UPDATE that flips
    #: the status, then reads back exactly the rows carrying it — so a claim
    #: returns the rows THIS call won and never a row another caller took.
    claim_token: str = ""
    #: What a DONE item produced (the new path after a rename, the written
    #: file). Verbatim for display; ``result_norm`` is what re-survey matches.
    result_key: str = ""
    result_norm: str = Field(default="", index=True)
    #: Content fingerprint of ``result_key`` at the moment it was marked done —
    #: UndoJournal's discipline, so "done" can later be checked rather than
    #: believed. Absent when the agent reported no result path.
    result_sha256: str | None = None
    result_size: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
