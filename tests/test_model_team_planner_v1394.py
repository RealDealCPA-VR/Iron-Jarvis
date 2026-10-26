"""v1.394.0 (the long-jobs plan's Phase 5): an OBJECTIVE's planner sees the
model team too.

A coding session's prompt carries the team section (``test_model_team_live_
v1394``); a mission's coordinator (a supervisor run) reads the roster block
(``runtime`` → ``roster_block``), and a member's line there carries its model,
place, cost type, roles and usage — the same list with more facts. Under a
Local only run the runtime sets the scope around the whole body, so a member
whose model leaves the network is not in the coordinator's prompt at all.

Driven through the REAL ``AgentRuntime`` and router; only the model is a
recording double that keeps the system prompt it was shown.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse


class Seen(LLMAdapter):
    def __init__(self, provider: str, model: str | None, systems: list, endpoint: str = "") -> None:
        self.provider = provider
        self.model = model or "default"
        self.systems = systems
        self._endpoint = endpoint

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        self.systems.append((self.provider, system))
        return LLMResponse(text="Done.", usage={"input_tokens": 1, "output_tokens": 1})

    async def stream(self, *, system, messages, tools, **kw):
        self.systems.append((self.provider, system))
        resp = LLMResponse(text="Done.", usage={"input_tokens": 1, "output_tokens": 1})
        yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


@pytest.fixture
def world(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path))
    systems: list = []
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434", ollama_model="qwen3:8b",
                                custom_base_url="", custom_model="")
    p.providers.register("google", lambda model=None: Seen("google", model, systems))
    p.providers.register("ollama", lambda model=None: Seen("ollama", model or "qwen3:8b", systems,
                                                           endpoint="http://127.0.0.1:11434"))
    real = p.providers.available
    monkeypatch.setattr(p.providers, "available",
                        lambda name: True if name in ("google", "ollama", "codex-cli") else real(name))
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    reg = p.agents_registry
    reg.register("local-reader", "r", [], description="reads and summarizes", provider="ollama",
                 model="qwen3:8b", roles=["read"])
    reg.register("codex-coder", "c", [], description="writes code", provider="codex-cli",
                 model="subscription", roles=["code", "test"])
    return p, systems


async def _coordinate(p, systems, *, provider: str, model: str, local_only: bool = False) -> str:
    orch = Orchestrator(p)
    session = await orch.create_session(
        "Build the report with the team.", AgentType.SUPERVISOR, provider=provider, model=model,
        **({"options": {"local_only": True}} if local_only else {}),
    )
    await AgentRuntime(p).run(session, get_agent_definition(AgentType.SUPERVISOR))
    assert systems, "the coordinator was never asked"
    return systems[0][1]


async def test_an_objectives_coordinator_sees_each_members_model_place_cost_and_roles(world):
    p, systems = world
    system = await _coordinate(p, systems, provider="google", model="gemini-3.8-flash")
    assert "# Who can take this work" in system
    assert "- custom:local-reader — reads and summarizes [qwen3:8b, this PC, free; roles: read]" in system
    assert ("- custom:codex-coder — writes code [Codex, cloud, your plan, usage not known yet; "
            "roles: code, test]") in system


async def test_a_local_only_objective_never_shows_its_coordinator_a_cloud_member(world):
    p, systems = world
    system = await _coordinate(p, systems, provider="ollama", model="qwen3:8b", local_only=True)
    assert "custom:local-reader" in system
    assert "codex-coder" not in system
