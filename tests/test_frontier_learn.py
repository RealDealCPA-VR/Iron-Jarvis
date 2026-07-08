"""The learning loop closes for DELEGATED / SPAWNED children (§12 + §29).

The post-run pipeline (evaluate -> record_outcome -> reflect) used to run for
solo sessions only, so multi-agent work taught the system nothing. These tests
drive the delegate + spawn tools offline and assert the child session produced a
learning-pipeline effect — a durable ``OutcomeRecord`` keyed by the CHILD's
session id — proving ``Orchestrator._post_run_learning`` now fires for children.

Importing ``improvement.models`` at the top registers ``OutcomeRecord`` on the
shared SQLModel metadata BEFORE ``build_platform`` -> ``init_db`` runs.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from iron_jarvis.agents import dynamic_models  # noqa: F401  (registers the table)
from iron_jarvis.agents.dynamic import DynamicAgentRegistry
from iron_jarvis.agents.agent_tools import agent_management_tools
from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.core.db import session_scope
from iron_jarvis.improvement.models import OutcomeRecord  # noqa: F401 (registers)
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.registry.register(DelegateTool(p))
    registry = DynamicAgentRegistry(p.engine).load()
    for tool in agent_management_tools(p, registry):
        p.registry.register(tool)
    p.permissions = PermissionEngine(
        {
            **p.config.permissions,
            "delegate": "allow",
            "spawn_agent": "allow",
            "write_file": "allow",
        }
    )
    return p


def _ctx(platform, tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id="parent1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _outcome_for(engine, session_id: str) -> OutcomeRecord | None:
    with session_scope(engine) as db:
        return db.exec(
            select(OutcomeRecord).where(OutcomeRecord.session_id == session_id)
        ).first()


async def test_delegated_child_gets_an_outcome_row(platform, tmp_path):
    """A delegated child runs the full learning pipeline: an OutcomeRecord lands
    for the CHILD session id (not just the parent)."""
    result = await platform.registry.invoke(
        "delegate",
        {"agent_type": "builder", "task": "write a summary file"},
        _ctx(platform, tmp_path),
        platform.permissions,
    )
    assert result.ok, result.error
    child_session_id = result.data["child_session_id"]

    outcome = _outcome_for(platform.engine, child_session_id)
    assert outcome is not None, "delegated child should get a learning OutcomeRecord"
    assert outcome.success is True  # the offline builder completes


async def test_spawned_child_gets_an_outcome_row(platform, tmp_path):
    """The spawn_agent path closes the same loop for its child session."""
    result = await platform.registry.invoke(
        "spawn_agent",
        {"agent": "builder", "task": "do a thing"},
        _ctx(platform, tmp_path),
        platform.permissions,
    )
    assert result.ok, result.error
    child_session_id = result.data["child_session_id"]

    outcome = _outcome_for(platform.engine, child_session_id)
    assert outcome is not None, "spawned child should get a learning OutcomeRecord"


async def test_learning_failure_never_breaks_delegation(platform, tmp_path, monkeypatch):
    """A blow-up in the learning pipeline is swallowed — delegation still returns
    ok and the child session still persisted."""

    def boom(_session):
        raise RuntimeError("evaluator exploded")

    # Patch the method on the class so the tool's freshly-built Orchestrator uses it.
    from iron_jarvis.agents.orchestrator import Orchestrator

    monkeypatch.setattr(Orchestrator, "_post_run_learning", boom)

    result = await platform.registry.invoke(
        "delegate",
        {"agent_type": "builder", "task": "x"},
        _ctx(platform, tmp_path),
        platform.permissions,
    )
    assert result.ok, result.error
    assert result.data["child_session_id"]
