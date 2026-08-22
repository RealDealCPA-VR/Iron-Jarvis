"""Panel + consult KNOWLEDGE (v1.200.0): the advisors no longer advise blind.

The round table and ``consult`` were ONE-WAY knowledge valves (CONNECT audit
2026-08-22, item 6): panels WRITE into memory — ``remember`` distills them into
LTM and every round syncs into the history index that feeds chat recall — but a
panelist's prompt received exactly base_prompt + role_line + PANEL_NO_TOOLS +
profile("how"), and a consulted teammate the same shape. No lessons block,
while every sibling lane (chat_turn, agents/runtime) folds it in through
``LearningEngine.apply_to_prompt``.

What this file pins, mutation-proof, through the REAL prompt-builders (the
real ``/agents/threads/{id}/say`` round and the real ``ConsultTool.execute``,
spying only the adapter/router call — the v1.144.0 every-seam idiom, because a
test that mirrors the builder locally cannot catch the builder changing):

1. a recorded lesson's TEXT reaches every panelist prompt and the consult
   prompt — appended, never replacing the identity/seat/no-tools spine;
2. no lessons -> not one added character (no empty heading injected);
3. a learning engine that RAISES costs its block, never the round or the
   consult (consult runs mid-run inside another agent's turn);
4. the injection reuses the ONE renderer — the heading asserted here IS
   ``learning.engine._LESSONS_HEADING``, so a second drifting renderer cannot
   satisfy these tests by accident.

Deliberately absent: PROJECT knowledge for panels. ``AgentThreadRecord``
carries no project_id (the Agents page is global — ``_index_thread`` indexes
rounds with an empty project id), so there is no honest tag to key it on.
Lessons-only is the whole claim, and this docstring is where that decision is
recorded.

Offline: real app / real platform, real DB, the deterministic mock provider.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.consult_tool import ConsultTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.threads import PANEL_NO_TOOLS
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.learning.engine import _LESSONS_HEADING
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext

_MARK = "MARKER-LESSON-V1200: always cite the depreciation schedule by name"
_HEADING = _LESSONS_HEADING.strip()  # "# What I've learned about working with you"


# --------------------------------------------------------------------------- #
# round-table plumbing (the v1.193.0 identity test's own helpers)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def _spy_systems(platform) -> list[str]:
    """Capture every adapter-level system prompt (the v1.144.0 seam)."""
    seen: list[str] = []
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        # Adapters are CACHED per provider — a second get() would double-wrap.
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


def _round(client, participants, message="what do you think?") -> dict:
    r = client.post("/agents/threads", json={"participants": participants})
    assert r.status_code == 200, r.text
    say = client.post(
        f"/agents/threads/{r.json()['id']}/say", json={"message": message}
    )
    assert say.status_code == 200, say.text
    return say.json()


_PANEL = [
    {"source": "builtin", "name": "reviewer", "role": "critic"},
    {"source": "builtin", "name": "researcher", "role": "digger"},
]


# --------------------------------------------------------------------------- #
# consult plumbing (the v1.193.0 consult test's own helpers)
# --------------------------------------------------------------------------- #
@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path / "home"))


def _ctx(platform, session_id: str, agent_run_id: str = "run_1"):
    return ToolContext(
        workspace=platform.config.workspaces_dir,
        session_id=session_id,
        agent_run_id=agent_run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _spy_router(platform) -> list[dict]:
    seen: list[dict] = []
    real = platform.router.complete

    async def spy(**kwargs):
        seen.append(dict(kwargs))
        return await real(**kwargs)

    platform.router.complete = spy
    return seen


async def _caller(platform, agent_type=AgentType.BUILDER):
    return await Orchestrator(platform).create_session("the parent task", agent_type)


# --------------------------------------------------------------------------- #
# 1. a lesson reaches EVERY panelist prompt — and nothing it rode in on broke
# --------------------------------------------------------------------------- #
def test_a_lesson_reaches_every_panelist_prompt(app):
    client = TestClient(app)
    platform = app.state.platform
    platform.learning.note_preference(_MARK)
    systems = _spy_systems(platform)

    _round(client, _PANEL)
    assert len(systems) == 2, systems

    for system in systems:
        # THE HEADLINE: the lesson's text is IN the prompt the model got.
        assert _MARK in system
        assert _HEADING in system
        # Appended through apply_to_prompt, never spliced into the spine: the
        # identity -> seat -> NO TOOLS order the round table guarantees is
        # intact, and the lessons land after the whole of it.
        assert PANEL_NO_TOOLS in system
        assert system.index(PANEL_NO_TOOLS) < system.index(_MARK)

    reviewer, researcher = systems
    # The real definitions still carry the seats — the injection added to the
    # prompt, it did not replace it — and panelists stay DISTINCT.
    assert get_agent_definition(AgentType.REVIEWER).system_prompt.strip() in reviewer
    assert get_agent_definition(AgentType.RESEARCHER).system_prompt.strip() in researcher
    assert reviewer != researcher


# --------------------------------------------------------------------------- #
# 2. no lessons -> not one added character
# --------------------------------------------------------------------------- #
def test_no_lessons_means_no_heading_in_the_panel_prompt(app):
    client = TestClient(app)
    systems = _spy_systems(app.state.platform)
    _round(client, _PANEL)
    assert len(systems) == 2, systems
    for system in systems:
        assert _HEADING not in system  # no empty header injected


# --------------------------------------------------------------------------- #
# 3. a broken learning engine costs its block, never the round
# --------------------------------------------------------------------------- #
def test_a_broken_learning_engine_never_sinks_a_round(app, monkeypatch):
    client = TestClient(app)
    platform = app.state.platform

    def boom(*a, **k):
        raise RuntimeError("learning layer down")

    monkeypatch.setattr(platform.learning, "apply_to_prompt", boom)
    systems = _spy_systems(platform)

    out = _round(client, _PANEL)
    # The round completed with real content — honest entries, no errors.
    spoken = [e for e in out["entries"]]
    assert spoken and all(e["content"] for e in spoken), out
    assert all(not e.get("error") for e in spoken), out
    # ...and the prompt still built (minus the block, which is the deal).
    assert len(systems) == 2
    for system in systems:
        assert PANEL_NO_TOOLS in system
        assert _HEADING not in system


# --------------------------------------------------------------------------- #
# 4. the same lesson reaches the CONSULT prompt
# --------------------------------------------------------------------------- #
async def test_a_lesson_reaches_the_consult_prompt(platform):
    platform.learning.note_preference(_MARK)
    caller = await _caller(platform)
    seen = _spy_router(platform)

    res = await ConsultTool(platform).execute(
        {"agent": "reviewer", "question": "Is a 5-year life defensible here?"},
        _ctx(platform, caller.id),
    )

    assert res.ok is True, res.error
    assert len(seen) == 1
    system = seen[0]["system"]
    assert _MARK in system
    assert _HEADING in system
    # The v1.193.0 spine survives the injection: real prompt, seat, NO TOOLS,
    # in that order, with the lessons appended after — and still tools=[].
    reviewer_prompt = get_agent_definition(AgentType.REVIEWER).system_prompt
    assert reviewer_prompt in system
    assert (
        system.index(reviewer_prompt)
        < system.index("is CONSULTING you")
        < system.index(PANEL_NO_TOOLS)
        < system.index(_MARK)
    )
    assert seen[0]["tools"] == []


# --------------------------------------------------------------------------- #
# 5. no lessons -> no heading in the consult prompt either
# --------------------------------------------------------------------------- #
async def test_no_lessons_means_no_heading_in_the_consult_prompt(platform):
    caller = await _caller(platform)
    seen = _spy_router(platform)
    res = await ConsultTool(platform).execute(
        {"agent": "reviewer", "question": "quick check?"},
        _ctx(platform, caller.id),
    )
    assert res.ok is True, res.error
    assert len(seen) == 1
    assert _HEADING not in seen[0]["system"]


# --------------------------------------------------------------------------- #
# 6. a broken learning engine costs its block, never the consult
# --------------------------------------------------------------------------- #
async def test_a_broken_learning_engine_never_sinks_a_consult(platform, monkeypatch):
    """consult runs MID-RUN inside another agent's turn — a learning-layer
    exception surfacing here would fail a tool call the caller cannot retry
    around, which is a worse trade than one un-grounded answer."""
    caller = await _caller(platform)

    def boom(*a, **k):
        raise RuntimeError("learning layer down")

    monkeypatch.setattr(platform.learning, "apply_to_prompt", boom)
    seen = _spy_router(platform)

    res = await ConsultTool(platform).execute(
        {"agent": "reviewer", "question": "still there?"},
        _ctx(platform, caller.id),
    )
    assert res.ok is True, res.error
    assert res.output.startswith("reviewer answered:")
    assert len(seen) == 1
    assert PANEL_NO_TOOLS in seen[0]["system"]
    assert _HEADING not in seen[0]["system"]
