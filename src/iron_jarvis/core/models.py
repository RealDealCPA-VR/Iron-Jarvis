"""Persistence models (§14 Session, §13 AgentRun, §19 ToolInvocation, §31 events).

SQLModel tables backed by SQLite (zero-setup local-first, §22). JSON-shaped
fields are stored as text columns to keep the slice dependency-light.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlmodel import Field, SQLModel

from .ids import new_id, utcnow


class AgentType(str, enum.Enum):
    SUPERVISOR = "supervisor"
    PLANNER = "planner"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    RESEARCHER = "researcher"
    MEMORY = "memory"
    AUTOMATION = "automation"
    MAINTAINER = "maintainer"  # self-development: edits Iron Jarvis's own source


class AgentState(str, enum.Enum):
    """Lifecycle states (§13)."""

    CREATED = "created"
    INITIALIZING = "initializing"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    DELEGATING = "delegating"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PermissionMode(str, enum.Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Project(SQLModel, table=True):
    """The CONTEXT SPINE: a workspace every session/chat/workflow can tag into.

    The project's brief + recent activity inject into every tagged agent call,
    so every surface shares one thread of "what the user is working on"."""

    id: str = Field(default_factory=lambda: new_id("project"), primary_key=True)
    name: str
    root: str = ""  # optional folder this project lives in (for terminals etc.)
    brief: str = ""  # short description of what the project is
    #: Custom instructions injected as a system directive into EVERY project
    #: chat + task ("respond as…", "always cite…") — the Claude-Projects
    #: instructions concept, distinct from the descriptive brief.
    instructions: str = ""
    #: Optional per-project default model ("provider::model" halves) applied to
    #: project chats/tasks when the user hasn't picked one for the turn.
    default_provider: str = ""
    default_model: str = ""
    status: str = "active"  # active | archived
    created_at: datetime = Field(default_factory=utcnow)


class ProjectKnowledge(SQLModel, table=True):
    """A persistent knowledge item attached to ONE project (a file's extracted
    text, or a pasted note). Every conversation and task in the project is
    grounded in these — the Claude-Projects "project knowledge" concept.
    Embedded on write so large knowledge bases retrieve the relevant parts."""

    id: str = Field(default_factory=lambda: new_id("pk"), primary_key=True)
    project_id: str = Field(index=True)
    name: str = ""
    kind: str = "note"  # note | file
    text: str = ""
    size: int = 0  # characters (shown in the UI, drives the all-vs-retrieve choice)
    embedding_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)


class Session(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("session"), primary_key=True)
    project_id: str | None = Field(default=None, index=True)
    task: str = ""
    agent_type: AgentType = AgentType.BUILDER
    provider: str = "mock"
    model: str = "claude-opus-4-8"
    status: SessionStatus = SessionStatus.ACTIVE
    workspace_path: str = ""
    #: Per-session tool grant (JSON list of perm_keys). The user approved these
    #: tools UP FRONT (bundled) for THIS task, so the runtime treats an "ask" on
    #: one of them as allowed for this session only — never overriding a hard
    #: "deny". Empty = no extra grants. Additive column (auto-reconciled).
    allow_tools_json: str = "[]"
    summary: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    #: TX-01 provenance — WHO or WHAT initiated this session, so the audit
    #: timeline can answer "did I start this, or did it start itself?": user_chat
    #: | user_task | autonomy | schedule | comm | reflex | workflow | self_dev |
    #: continuation | rerun. Additive nullable column (auto-reconciled).
    origin: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class AgentRun(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("run"), primary_key=True)
    session_id: str = Field(index=True)
    parent_id: str | None = Field(default=None, index=True)  # subagents (§12)
    agent_type: AgentType = AgentType.BUILDER
    provider: str = "mock"
    model: str = "claude-opus-4-8"
    state: AgentState = AgentState.CREATED
    steps: int = 0
    result: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class ToolInvocation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("tool"), primary_key=True)
    session_id: str = Field(index=True)
    agent_run_id: str = Field(index=True)
    tool: str = ""
    args_json: str = "{}"
    verdict: PermissionMode = PermissionMode.ALLOW
    ok: bool = True
    output: str = ""
    #: TX-01 undo: the tool's declared reversibility ("readonly"|"reversible"|
    #: "irreversible") captured at call time, so the timeline can offer (or
    #: honestly withhold) an Undo without re-deriving it. Additive nullable.
    reversibility: str | None = None
    #: Set when THIS invocation has been undone (its inverse applied), so a
    #: second undo is refused and the UI shows "undone".
    undone_at: datetime | None = None
    #: When this row is ITSELF an undo action, the id of the invocation it
    #: reverted — so the undo is a first-class, auditable entry in the ledger.
    undo_of: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class UndoJournal(SQLModel, table=True):
    """The captured INVERSE of a reversible tool call (TX-01 time-travel).

    One row per undoable action, keyed by the ToolInvocation id it mirrors. The
    pre-image itself (prior file bytes, dropped memory ref) lives either inline
    (``pre_inline`` for small values) or on disk under ``.ironjarvis/undo/``
    (``pre_ref``) so large snapshots never bloat the DB. ``applied_at`` marks the
    inverse as consumed. All fields are redaction-safe — a secret prior value is
    spilled to the encrypted vault, never persisted here in plaintext."""

    action_id: str = Field(primary_key=True)  # == the ToolInvocation.id
    session_id: str = Field(index=True)
    agent_run_id: str = ""
    tool: str = ""
    #: file_restore | file_delete | memory_delete | setting_restore | …
    kind: str = ""
    reversible: bool = True
    #: Pointer to the on-disk pre-image blob (under .ironjarvis/undo/), or None.
    pre_ref: str | None = None
    #: Small pre-images stored inline (JSON) instead of a blob file.
    pre_inline: str | None = None
    #: sha256 of the pre- and post-mutation target, so a revert can detect that
    #: the target changed since (conflict) and refuse rather than clobber.
    pre_sha256: str | None = None
    post_sha256: str | None = None
    applied_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class PendingReviewRecord(SQLModel, table=True):
    """A git-native session whose change awaits human review, persisted so it
    survives a daemon restart (the in-memory review/worktree state does not).
    Deleted on approve/reject; rehydrated on boot if the worktree still exists."""

    session_id: str = Field(primary_key=True)
    repo: str = ""  # the repo the worktree was created from (project_root or self-dev)
    branch: str = ""
    base: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class DynamicToolRecord(SQLModel, table=True):
    """A runtime-defined, reusable tool authored by a user or an agent — "tools
    that make tools". The tool runs an argv-template command (placeholders filled
    from typed parameters) in the session workspace, gated under ``custom:<name>``.
    Persisted so every FUTURE agent/session sees and can call it."""

    id: str = Field(default_factory=lambda: new_id("tool"), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    params_json: str = "[]"  # JSON list[{name,type,required,description}]
    argv_json: str = "[]"  # JSON list[str] command template ({param} placeholders)
    timeout_seconds: int = 60
    created_by: str = ""  # session id of the author (provenance), or "" for user
    created_at: datetime = Field(default_factory=utcnow)


class LiveDocRecord(SQLModel, table=True):
    """A LIVING document: a prompt + format + optional schedule. Each
    regeneration rewrites the same output file, so reports stay fresh
    (weekly client status PDF, daily briefing, …) instead of going stale."""

    id: str = Field(default_factory=lambda: new_id("livedoc"), primary_key=True)
    name: str = ""
    prompt: str = ""  # what the document should contain, in plain language
    format: str = "md"  # md | html | docx | pdf
    path: str = ""  # the stable output file (rewritten on each regeneration)
    schedule_name: str = ""  # the scheduler entry driving auto-refresh ("" = manual)
    provider: str = ""
    model: str = ""
    last_error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None


class ChatThreadRecord(SQLModel, table=True):
    """A saved chat conversation — frontier parity: threads survive navigation
    and restarts, listed in a sidebar, resumable any time."""

    id: str = Field(default_factory=lambda: new_id("chat"), primary_key=True)
    title: str = ""
    persona: str = ""
    messages_json: str = "[]"  # [{role, content, attachmentNames?}]
    project_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SavedPromptRecord(SQLModel, table=True):
    """A reusable task template / saved prompt the user can re-run with one
    click (daily-driver: stop retyping the same task into a blank box)."""

    id: str = Field(default_factory=lambda: new_id("prompt"), primary_key=True)
    name: str = ""
    agent_type: AgentType = AgentType.BUILDER
    task: str = ""
    provider: str | None = None
    model: str | None = None
    #: WHEN to reach for this template — shown on the card so templates are
    #: self-explanatory ("use this when…"), not just a name.
    description: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class EventRecord(SQLModel, table=True):
    """Persisted event log for observability & replay (§29, §30, §31)."""

    id: str = Field(primary_key=True)
    type: str = Field(index=True)
    session_id: str | None = Field(default=None, index=True)
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)
