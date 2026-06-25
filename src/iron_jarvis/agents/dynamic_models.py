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
    created_at: datetime = Field(default_factory=utcnow)
