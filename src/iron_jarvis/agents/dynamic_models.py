"""Persistence model for runtime-defined (dynamic) agents — "agents that add agents".

A dynamic agent is an :class:`~iron_jarvis.agents.types.AgentDefinition` created at
runtime by a user or another agent and persisted as a row here: a unique name, a
system prompt, a JSON-encoded tool allowlist, and the *base* ``AgentType`` whose
enum value the agent borrows for lifecycle/persistence (``AgentType`` is a fixed
enum, so dynamic agents reuse a base type but carry their own prompt + tools).

Importing this module registers the table on the shared SQLModel metadata BEFORE
``init_db`` runs (the same convention used by the workflow/eval models), so the
table is created on platform boot.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class DynamicAgentRecord(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("dyn"), primary_key=True)
    name: str = Field(index=True, unique=True)
    system_prompt: str = ""
    tools_json: str = "[]"  # JSON list[str] of tool names the agent may use
    base_type: str = "builder"  # value of the base AgentType the agent borrows
    description: str = ""
    provider: str = ""  # preferred LLM provider (e.g. "anthropic"); "" = platform default
    model: str = ""  # preferred model id (e.g. "claude-opus-4-8"); "" = platform default
    # --- the job card (v1.295.0): a custom agent is an EMPLOYEE -------------
    # Every column below is ADDITIVE with a default: ``core.db._reconcile_
    # additive_columns`` ALTER-TABLEs them onto a DB created before this wave,
    # and a row written then reads these defaults. Pinned in
    # ``tests/test_agent_allowance_v1295.py``.
    #: "" | "approve_for_me" | "always_ask" — the posture its runs take when
    #: the door states none. "yolo" never lands here (``create_session``
    #: normalises every posture through ``inherited_approval_mode``).
    approval_mode: str = ""
    #: Per-run step budget; None = the config default.
    max_steps: int | None = None
    #: Monthly allowance (per CALENDAR month). 0 = unlimited.
    allowance_tokens: int = 0
    allowance_usd: float = 0.0
    #: "YYYY-MM" once the 80% warning went out for that month (one per month).
    allowance_warned_month: str = ""
    #: "" = not paused. Set by the allowance auto-pause or by the user (a day
    #: off); every door refuses with this reason while it is set.
    paused_reason: str = ""
    paused_at: datetime | None = None
    #: A roster name ("builder" | "custom:<slug>") or "" = reports to the user.
    reports_to: str = ""
    #: JSON list[str] of skill names injected into its runs (in addition to
    #: ``config.default_skills``).
    skills_json: str = "[]"
    #: JSON list[str] of tools this agent may NEVER use (a "deny" override;
    #: the deny floor in ``tools/permissions.py`` means it can only narrow).
    deny_tools_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
