"""Capability proposals (v1.178.0, P4) — the row an agent files when the app
has no verb for the job.

THE MEASURED FAILURE. *"Rename all files in this folder"* ran FOUR times and
renamed nothing, because no rename tool existed. What made it expensive was not
the gap — it was the silence: the agent never said "I can't do this". It shelled
out and wrote PyMuPDF scripts to re-read PDFs it had already read successfully,
25 ``shell`` calls in one run (ledger ``run_ab82dea4bf8a``). Five capabilities in
a row then shipped without reaching the agent that needed them, and every one was
found by a live job failing rather than by anything in the app noticing.

A :class:`CapabilityProposalRecord` is that missing sentence, made durable and
reviewable: *this job would have gone better with X, here is why, here is exactly
what it would be allowed to do.* Filing one changes NOTHING. It is the
``memory_propose`` shape (v1.143.0) pointed at the app's own toolbox instead of
at the user's notes, and for the same reason: the thing being asked for is more
powerful than the asking, so the asking must be free and the granting must be a
click.

WHAT THE ROW DELIBERATELY IS NOT. It is not a permission. Nothing here can raise
a tool's mode, and approval never writes an entry into ``config.permissions`` —
:attr:`CapabilityProposalRecord.requested_permission` is RECORDED so the user can
read what was wanted, and is then ignored. An approved custom tool lands on
``custom:<name>``, whose absence from the permission table resolves to ``ask``
(fail-closed), which is exactly where a tool an agent designed for itself belongs.

TWO NORMALIZED FIELDS, both load-bearing:

* ``name_norm`` — how "Rename_File", "rename_file " and "rename_file" become one
  request. Every existence check and the signature use it, never the raw text.
* ``signature`` — ``kind::name_norm``. A PENDING or REJECTED signature is
  suppressed, so a model that re-derives the same gap on every run cannot nag,
  and "no" sticks. ``approved`` is deliberately NOT suppressed: if the user later
  deletes the tool, asking for it again is a real request and not a repeat.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: What can be asked for. Only ``tool`` has a creation path this app owns (the
#: existing ``tool_create``); the other two are asks the USER has to satisfy —
#: see ``store.describe_kind``, which says so before the user clicks rather than
#: after.
KINDS: tuple[str, ...] = ("tool", "mcp", "connection")

#: kind -> the words the user (and the model reading its own tool output) sees.
KIND_LABELS: dict[str, str] = {
    "tool": "a custom tool",
    "mcp": "an MCP server",
    "connection": "a connection",
}

#: pending -> approved | rejected. No other states (memory's rule: a third
#: status is a fourth code path nobody tests).
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
STATUSES: tuple[str, ...] = (PENDING, APPROVED, REJECTED)

#: Caps. Every field here is MODEL OUTPUT steered by documents the user did not
#: write, so nothing unbounded reaches the database or the review card.
MAX_NAME = 64
MAX_RATIONALE = 2000
MAX_SCOPE = 1000
MAX_TASK = 1000
MAX_SPEC_JSON = 20_000


def normalize_name(name: str) -> str:
    """The comparison form of a proposed capability's name.

    Case and surrounding whitespace cannot change WHICH capability is meant, so
    they stop being differences here — the same reasoning as
    ``worklist.models.normalize_key``. Nothing else is stripped: ``read_file``
    and ``read-file`` are two different names to ``tool_create``, and collapsing
    them would let a proposal pass an existence check it should have failed.
    """
    return (name or "").strip().casefold()


def signature_for(kind: str, name: str) -> str:
    """Stable dedup key for one request (``kind::name_norm``)."""
    return f"{(kind or '').strip().lower()}::{normalize_name(name)}"[:200]


class CapabilityProposalRecord(SQLModel, table=True):
    """One suggest-only request for a capability this app does not have."""

    id: str = Field(default_factory=lambda: new_id("capprop"), primary_key=True)
    #: tool | mcp | connection (see :data:`KINDS`).
    kind: str = Field(default="tool", index=True)
    #: The capability as the agent named it. Shown verbatim.
    name: str = ""
    #: :func:`normalize_name` of ``name`` — every lookup uses THIS.
    name_norm: str = Field(default="", index=True)
    #: WHY, in the agent's own plain sentences. The user reads this verbatim and
    #: decides on it, so it is never summarized or rewritten downstream.
    rationale: str = ""
    #: Precisely what the capability would be ALLOWED to do, in one line. This is
    #: the sentence a user approves; a vague one is a bad approval.
    scope: str = ""
    #: The job that was in hand when the gap was hit ("rename 26 files in
    #: C:/clients/2025"). Without it "I'd like a rename tool" is unreviewable.
    task: str = ""
    #: The concrete shape: for a tool ``{"command": [...], "parameters": [...],
    #: "timeout_seconds": n}``; for mcp/connection whatever the agent could name
    #: (a package, a URL). Never executed at file time — only ever read by
    #: ``approve``.
    spec_json: str = "{}"
    #: The mode the agent WANTED. Recorded for the user to read and NEVER
    #: applied: approval writes no permission entry at all, so an approved
    #: custom tool runs at ``custom:<name>`` -> absent -> "ask" (fail-closed).
    #: Keeping the wish visible is the honest half of refusing to grant it.
    requested_permission: str = "ask"
    #: ``kind::name_norm``; a pending/rejected signature is suppressed.
    signature: str = Field(default="", index=True)
    status: str = Field(default=PENDING, index=True)
    #: What approving actually DID (or why it could not) — set by ``approve``.
    applied_json: str = "{}"
    #: The session that filed it (``ctx.session_id``), so a request is auditable
    #: back to the run that hit the wall.
    run_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None

    def decoded_spec(self) -> dict[str, Any]:
        """``spec_json`` as a dict (never raises — a mangled row reads empty)."""
        try:
            parsed = json.loads(self.spec_json or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def decoded_applied(self) -> dict[str, Any]:
        """``applied_json`` as a dict (never raises)."""
        try:
            parsed = json.loads(self.applied_json or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
