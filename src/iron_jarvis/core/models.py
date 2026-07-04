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
    brief: str = ""  # goal + key facts, injected into tagged agent calls
    status: str = "active"  # active | archived
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
    summary: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
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
