"""Round-table IDENTITY (v1.193.0): a panelist is the agent it claims to be.

Two verified defects, both proven end to end through ``POST /agents/threads/
{id}/say`` (the real round, the real one-shot path, the offline mock adapter):

1. a BUILTIN seat was handed a synthesized one-liner instead of the real
   ``types._DEFINITIONS`` prompt — the "reviewer" at the table was a model told
   to sound like one;
2. a DYNAMIC seat was handed ``row.system_prompt`` RAW, which bypasses
   ``DynamicAgentRegistry.definition`` — where the v1.171.0 identity anchor is
   applied — so a named agent was never told its own name in the one room where
   agents address each other by name.

And the correction that has to ride along: a panelist speaks through
``_one_shot_complete`` with ``tools=[]``, so the real prompt's tool
instructions must arrive with an explicit "you have no tools here, do not claim
you acted" — otherwise the fix for impersonation buys a fabrication.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.threads import PANEL_NO_TOOLS
from iron_jarvis.agents.types import _DEFINITIONS
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def _spy_systems(platform) -> list[str]:
    """Capture every adapter-level system prompt (same seam as v1.144.0's)."""
    seen: list[str] = []
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        # Adapters are CACHED per provider, so a second get() would wrap an
        # already-wrapped complete and record one call twice.
        if getattr(adapter, "_ij_spied", False):
            return adapter
        adapter._ij_spied = True
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            seen.append(system)
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get
    return seen


def _round(client, participants, message="what do you think?") -> None:
    r = client.post("/agents/threads", json={"participants": participants})
    assert r.status_code == 200, r.text
    say = client.post(
        f"/agents/threads/{r.json()['id']}/say", json={"message": message}
    )
    assert say.status_code == 200, say.text


def test_builtin_panelist_carries_the_real_definition_and_the_no_tools_framing(app):
    client = TestClient(app)
    systems = _spy_systems(app.state.platform)
    _round(
        client,
        [
            {"source": "builtin", "name": "reviewer", "role": "critic"},
            {"source": "builtin", "name": "researcher", "role": "digger"},
        ],
    )
    assert len(systems) == 2, systems

    reviewer, researcher = systems
    # (1) THE REAL AGENT, verbatim — not a synthesized impression of one.
    assert _DEFINITIONS[AgentType.REVIEWER].system_prompt.strip() in reviewer
    assert "careful, constructive second pair" in reviewer
    assert _DEFINITIONS[AgentType.RESEARCHER].system_prompt.strip() in researcher
    assert "report findings with sources" in researcher
    # The old stand-in is gone from both seats.
    assert "answer with the judgement and focus of a" not in reviewer
    assert "answer with the judgement and focus of a" not in researcher
    # Panelists stay DISTINCT (the reason they get profile include=("how",)).
    assert reviewer != researcher
    assert "As the Researcher" not in reviewer

    # (2) ...AND IS TOLD IT HAS NO HANDS HERE, after the prompt that mentions
    # tools, so it advises instead of claiming it acted.
    for system in systems:
        assert PANEL_NO_TOOLS in system
        assert system.index(PANEL_NO_TOOLS) > system.index("As the ")
        assert "You ADVISE; you do not act." in system

    # Still the seat it was given, and still forbidden to speak for others.
    assert "You are reviewer, the critic in a panel conversation" in reviewer
    assert "fabricate their views" in reviewer


def test_dynamic_panelist_gets_its_identity_anchor(app):
    client = TestClient(app)
    assert client.post(
        "/agents",
        json={"name": "taxpro", "system_prompt": "You are a sharp tax accountant."},
    ).status_code == 200
    systems = _spy_systems(app.state.platform)
    _round(client, [{"source": "dynamic", "name": "taxpro", "role": "lead"}])
    assert len(systems) == 1, systems
    system = systems[0]

    from iron_jarvis.agents.dynamic import identity_anchor

    assert identity_anchor("taxpro") in system      # the composed anchor
    assert "sharp tax accountant" in system         # ...and the stored persona
    assert system.index(identity_anchor("taxpro")) < system.index("sharp tax")
    assert PANEL_NO_TOOLS in system


def test_an_unknown_builtin_name_is_not_silently_the_builder(app):
    """``get_agent_definition`` falls back to BUILDER for anything it does not
    know. Taking that fallback here would seat "designer" and answer as the
    Builder — the impersonation this wave removes, reintroduced by its fix."""
    client = TestClient(app)
    systems = _spy_systems(app.state.platform)
    _round(client, [{"source": "builtin", "name": "designer", "role": "lead"}])
    assert len(systems) == 1, systems
    system = systems[0]
    assert "You are a designer agent" in system
    assert _DEFINITIONS[AgentType.BUILDER].system_prompt.strip() not in system
    assert PANEL_NO_TOOLS in system


def test_a_vanished_dynamic_agent_is_still_an_honest_error(app):
    """The prompt path changed; the honest-failure path must not have."""
    client = TestClient(app)
    r = client.post(
        "/agents/threads",
        json={"participants": [{"source": "dynamic", "name": "ghost", "role": "lead"}]},
    )
    say = client.post(
        f"/agents/threads/{r.json()['id']}/say", json={"message": "hello"}
    )
    entry = say.json()["entries"][-1]
    assert entry["content"] == ""
    assert "no longer exists" in entry["error"]
