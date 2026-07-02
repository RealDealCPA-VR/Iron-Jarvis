"""Dynamic Agent Registry — agents that add more agents (§11/§12 extension).

A *runtime* registry of user/agent-defined agents. Each entry is persisted as a
:class:`DynamicAgentRecord` and rebuilt into a standard
:class:`~iron_jarvis.agents.types.AgentDefinition` on demand, so a dynamic agent
plugs straight into the existing :class:`~iron_jarvis.agents.runtime.AgentRuntime`
loop with no special casing.

Because ``AgentType`` is a fixed enum, a dynamic agent reuses a *base* ``AgentType``
(default :attr:`AgentType.BUILDER`) for its lifecycle/persistence while carrying its
own system prompt and tool allowlist through the ``AgentDefinition``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlmodel import select

from ..core.db import session_scope
from ..core.models import AgentType
from .dynamic_models import DynamicAgentRecord
from .types import AgentDefinition

if TYPE_CHECKING:  # avoid importing the heavy SQLAlchemy symbol at runtime
    from sqlalchemy import Engine


#: A curated catalog of provider/model options a dynamic agent may select.
#: Offline-safe: the ``mock`` provider drives the deterministic test LLM; the
#: rest are the live providers the platform can route to when keys are present.
KNOWN_MODELS: list[dict] = [
    {"provider": "mock", "model": "mock-1"},
    {"provider": "anthropic", "model": "claude-opus-4-8"},
    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    {"provider": "anthropic", "model": "claude-haiku-4-5"},
    {"provider": "anthropic", "model": "claude-fable-5"},
    {"provider": "openai", "model": "gpt-4o"},
    {"provider": "openai", "model": "gpt-4o-mini"},
    # Served by BOTH api.openai.com and the ChatGPT (Codex) backend — the only
    # family available to a subscription-only ChatGPT account login.
    {"provider": "openai", "model": "gpt-5-codex"},
    {"provider": "google", "model": "gemini-2.0-flash"},
    {"provider": "google", "model": "gemini-1.5-pro"},
    # xAI (Grok) — current generation: the flagship, the fast agentic model,
    # and the two coding-agent models (grok-build-0.1 powers the Grok Build CLI).
    {"provider": "xai", "model": "grok-4"},
    {"provider": "xai", "model": "grok-4-1-fast"},
    {"provider": "xai", "model": "grok-code-fast-1"},
    {"provider": "xai", "model": "grok-build-0.1"},
    # OpenRouter — namespaced ids; openrouter/auto picks the best model per task.
    {"provider": "openrouter", "model": "openrouter/auto"},
    {"provider": "openrouter", "model": "x-ai/grok-code-fast-1"},
]


def available_models() -> list[dict]:
    """Return the catalog of selectable ``{provider, model}`` options (a copy)."""
    return [dict(m) for m in KNOWN_MODELS]


def _base_agent_type(raw: str) -> AgentType:
    """Resolve a stored ``base_type`` string to an ``AgentType`` (fail-soft)."""
    try:
        return AgentType(raw)
    except ValueError:
        return AgentType.BUILDER


class DynamicAgentRegistry:
    """Persisted, in-memory registry of dynamically defined agents."""

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine
        self._records: dict[str, DynamicAgentRecord] = {}

    # --- persistence ------------------------------------------------------

    def load(self) -> "DynamicAgentRegistry":
        """Read every persisted dynamic agent into memory (called on startup)."""
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(DynamicAgentRecord)))
        self._records = {r.name: r for r in rows}
        return self

    def register(
        self,
        name: str,
        system_prompt: str,
        tools: list[str],
        base_type: str = "builder",
        description: str = "",
        provider: str = "",
        model: str = "",
    ) -> DynamicAgentRecord:
        """Create or update a dynamic agent (upsert by unique ``name``).

        ``provider``/``model`` optionally pin the agent to a specific LLM (see
        :func:`available_models`); empty strings mean "use the platform default".
        """
        tools_json = json.dumps(list(tools or []))
        with session_scope(self.engine) as db:
            existing = db.exec(
                select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)
            ).first()
            if existing is not None:
                existing.system_prompt = system_prompt
                existing.tools_json = tools_json
                existing.base_type = base_type
                existing.description = description
                existing.provider = provider
                existing.model = model
                record = existing
            else:
                record = DynamicAgentRecord(
                    name=name,
                    system_prompt=system_prompt,
                    tools_json=tools_json,
                    base_type=base_type,
                    description=description,
                    provider=provider,
                    model=model,
                )
            db.add(record)
            db.commit()
            db.refresh(record)  # reload all columns so the detached copy is usable
        self._records[name] = record
        return record

    # --- lookups ----------------------------------------------------------

    def get(self, name: str) -> DynamicAgentRecord | None:
        record = self._records.get(name)
        if record is not None:
            return record
        # Fall back to the DB for instances that haven't loaded this name yet.
        with session_scope(self.engine) as db:
            record = db.exec(
                select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)
            ).first()
        if record is not None:
            self._records[name] = record
        return record

    def list(self) -> list[DynamicAgentRecord]:
        return sorted(self._records.values(), key=lambda r: r.name)

    def definition(self, name: str) -> AgentDefinition | None:
        """Rebuild a stored agent into an ``AgentDefinition`` (None if unknown)."""
        record = self.get(name)
        if record is None:
            return None
        try:
            tools = json.loads(record.tools_json or "[]")
        except (TypeError, ValueError):
            tools = []
        return AgentDefinition(
            type=_base_agent_type(record.base_type),
            system_prompt=record.system_prompt,
            tools=list(tools),
        )
