"""North-star fix 1.4 (v1.501.0): "Skip memory" reaches helpers.

A schedule's "skip memory" (``options.skip_memory``) kept the lessons, the
memory index and memory recall out of THAT run's prompt, but a helper the run
delegated to read ``skip_memory`` from its OWN options, which nobody set: the
child was told every lesson, the index and the recall block. Now a run
started inside a skip-memory run inherits it (the way Local only already
spreads down through the run's context: ``egress.local_only_scope``, which a
child awaited inside the parent's task already carries, so Local only is not
duplicated here).

Driven through the real orchestrator, runtime and ``delegate`` tool; only the
LLM is scripted, and the three memory injections are sentinels (the
``test_schedule_knobs_v1299`` way).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import iron_jarvis.memory.index_block as index_block
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.permissions import PermissionEngine

MARKERS = ("# LESSON-SENTINEL", "# MEMORY-INDEX-SENTINEL", "# FABRIC-SENTINEL")


class _Team(MockLLMAdapter):
    """Supervisor: delegate one task, then finish. Researcher: finish."""

    systems: list[str] = []

    async def complete(self, *, system, messages, tools, **kw):
        _Team.systems.append(system)
        done = any(m.role == "tool" for m in messages)
        if "As the Supervisor" in system and not done:
            return LLMResponse(
                tool_calls=[ToolCall(id="d1", name="delegate", arguments={
                    "agent_type": "researcher", "task": "look at the three files",
                })],
                finish_reason="tool_use",
            )
        return LLMResponse(text="done", finish_reason="stop")


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    p.providers.register("mock", lambda model=None: _Team())
    p.orchestrator = Orchestrator(p)
    p.permissions = PermissionEngine({**p.config.permissions, "delegate": "allow"})
    monkeypatch.setattr(p.learning, "apply_to_prompt",
                        lambda sp, **kw: sp + "\n\n# LESSON-SENTINEL", raising=True)
    monkeypatch.setattr(index_block, "memory_index_block", lambda *a, **k: "# MEMORY-INDEX-SENTINEL")
    monkeypatch.setattr(p, "fabric", SimpleNamespace(ground=lambda *a, **k: "\n\n# FABRIC-SENTINEL"))
    _Team.systems = []
    return p


async def _run_parent(p, options: dict) -> tuple[str, str]:
    parent = await p.orchestrator.create_session(
        "coordinate the review", AgentType.SUPERVISOR, provider="mock", model="mock-1",
        options=options,
    )
    await AgentRuntime(p).run(parent, get_agent_definition(AgentType.SUPERVISOR))
    sup = [s for s in _Team.systems if "As the Supervisor" in s]
    kid = [s for s in _Team.systems if "As the Researcher" in s]
    assert sup and kid, "the scripted supervisor did not reach its helper"
    return sup[0], kid[0]


async def test_a_child_of_a_skip_memory_run_gets_no_lessons_index_or_recall(platform):
    sup, kid = await _run_parent(platform, {"skip_memory": True})
    for marker in MARKERS:
        assert marker not in sup, marker
        assert marker not in kid, marker


async def test_a_child_of_an_ordinary_run_still_gets_its_memory(platform):
    """CONTROL (anti-vacuity): without the option both are told."""
    sup, kid = await _run_parent(platform, {})
    for marker in MARKERS:
        assert marker in sup, marker
        assert marker in kid, marker


async def test_skip_memory_does_not_leak_out_of_the_run(platform):
    await _run_parent(platform, {"skip_memory": True})
    _Team.systems = []
    sup, kid = await _run_parent(platform, {})
    assert all(m in kid for m in MARKERS)


async def test_local_only_already_reaches_the_child_through_the_context(platform):
    """Checked first, not duplicated: a child created inside a Local only
    run is Local only already (``create_session`` reads the scope the parent's
    run body set, and the child's task copies it)."""
    import json

    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Session

    await _run_parent(platform, {"local_only": True})
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(Session)))
    kids = [r for r in rows if r.agent_type == AgentType.RESEARCHER]
    assert kids, "no helper row"
    assert all(json.loads(r.options_json or "{}").get("local_only") is True for r in kids)
