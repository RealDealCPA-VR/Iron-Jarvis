"""A delegated child ALWAYS settles (v1.167.0).

The delegate tool awaited the child's ``AgentRuntime.run`` bare: hitting Stop
on a supervisor mid-delegation, or the child's runtime raising (a provider
refusing per the v1.162.0 no-mock rule), left the child session ACTIVE
forever — never finalized, never learned from, lying on the kanban board.

Now the child run is wrapped: a crash finalizes the child FAILED and returns
an honest tool error to the parent; a cancellation finalizes the child
CANCELLED and keeps propagating so the parent's own cancel unwinds normally.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.registry.register(DelegateTool(p))
    p.permissions = PermissionEngine(
        {**p.config.permissions, "delegate": "allow"}
    )
    return p


def _ctx(platform, tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id="parent1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _child_row(platform) -> Session:
    with session_scope(platform.engine) as db:
        rows = [
            s
            for s in db.exec(
                __import__("sqlmodel").select(Session)
            ).all()
            if s.id != "parent-session"
        ]
    assert rows, "delegate never created a child session"
    return rows[-1]


async def test_child_crash_finalizes_failed_and_reports_honestly(
    platform, tmp_path, monkeypatch
):
    async def exploding_run(self, session, agent_def, parent_id=None):
        raise RuntimeError("provider refused: endpoint unreachable")

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", exploding_run)
    res = await platform.registry.invoke(
        "delegate",
        {"agent_type": "builder", "task": "x"},
        _ctx(platform, tmp_path),
        platform.permissions,
    )
    assert res.ok is False
    assert "crashed" in (res.error or "") and "endpoint unreachable" in res.error
    child = _child_row(platform)
    assert child.status is SessionStatus.FAILED, (
        f"child stranded {child.status.value} — the ACTIVE-forever bug"
    )
    assert child.finished_at is not None
    assert res.data["child_session_id"] == child.id
    assert res.data["state"] == "failed"


async def test_parent_cancel_finalizes_child_cancelled_and_propagates(
    platform, tmp_path, monkeypatch
):
    started = asyncio.Event()

    async def hanging_run(self, session, agent_def, parent_id=None):
        started.set()
        await asyncio.Event().wait()  # the child "works" until cancelled

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", hanging_run)
    task = asyncio.create_task(
        platform.registry.invoke(
            "delegate",
            {"agent_type": "builder", "task": "x"},
            _ctx(platform, tmp_path),
            platform.permissions,
        )
    )
    await started.wait()
    task.cancel()  # the parent's Stop reaching the delegate await
    with pytest.raises(asyncio.CancelledError):
        await task  # cancellation must keep PROPAGATING to the parent

    child = _child_row(platform)
    assert child.status is SessionStatus.CANCELLED, (
        f"child stranded {child.status.value} after the parent's cancel"
    )
    assert child.finished_at is not None
