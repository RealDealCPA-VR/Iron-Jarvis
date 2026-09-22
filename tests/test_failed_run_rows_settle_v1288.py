"""A crashed run settles its AgentRun rows with the session (v1.288.0, agents-04).

``AgentRuntime.run`` lets a provider error escape with the run row still
RUNNING. ``_finalize_failed`` marked the SESSION failed but — unlike the cancel
finalizer and the boot reconcile — never touched its run rows, and nothing
revisits a FAILED session, so the team tree showed a live agent inside a failed
job forever. Now every non-terminal row becomes FAILED with ``finished_at`` and
the error text, on the solo lane and on a crashed delegate child, and the settle
waits for a pause's in-flight WAITING -> RUNNING restore first (v1.227.1 order).
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from sqlmodel import select

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, Session, SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

_ERR = "fleet down at 3am"


class _Boom(MockLLMAdapter):
    async def complete(self, **kw):
        raise RuntimeError(_ERR)

    async def stream(self, **kw):
        raise RuntimeError(_ERR)
        yield  # pragma: no cover


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.providers.register("mock", lambda model=None: _Boom())
    return p


def _runs(p, session_id: str) -> list[AgentRun]:
    with session_scope(p.engine) as db:
        return list(db.exec(select(AgentRun).where(AgentRun.session_id == session_id)))


def test_solo_crash_settles_the_run_row_failed(platform):
    orch = Orchestrator(platform)

    async def go():
        s = await orch.create_session("say hi", provider="mock", model="mock-1")
        with pytest.raises(RuntimeError):
            await orch.run_session(s.id)
        return s.id

    sid = asyncio.run(go())
    assert orch.get_session(sid).status is SessionStatus.FAILED
    runs = _runs(platform, sid)
    assert runs, "the real runtime wrote a run row (anti-vacuity)"
    for r in runs:
        assert r.state is AgentState.FAILED, f"ghost {r.state.value} row in a FAILED session"
        assert r.finished_at is not None
        assert _ERR in (r.result or ""), "the row says why it failed"


def test_crashed_delegate_child_settles_its_run_row_failed(platform, tmp_path):
    platform.registry.register(DelegateTool(platform))
    platform.permissions = PermissionEngine({**platform.config.permissions, "delegate": "allow"})
    orch = Orchestrator(platform)

    async def go():
        parent = await orch.create_session("lead", provider="mock", model="mock-1")
        ctx = ToolContext(
            workspace=tmp_path,
            session_id=parent.id,
            agent_run_id="parent1",
            config=platform.config,
            event_bus=platform.event_bus,
            engine=platform.engine,
        )
        res = await platform.registry.invoke(
            "delegate", {"agent_type": "builder", "task": "x"}, ctx, platform.permissions
        )
        return parent.id, res

    parent_id, res = asyncio.run(go())
    assert res.ok is False and _ERR in (res.error or "")
    with session_scope(platform.engine) as db:
        children = [s for s in db.exec(select(Session)).all() if s.id != parent_id]
    assert children, "delegate created a child session"
    child = children[-1]
    assert child.status is SessionStatus.FAILED
    runs = _runs(platform, child.id)
    assert runs, "the child's real runtime wrote a run row (anti-vacuity)"
    assert all(r.state is AgentState.FAILED and r.finished_at for r in runs), [
        (r.state, r.finished_at) for r in runs
    ]


def test_settle_waits_for_an_inflight_waiting_restore(platform, monkeypatch):
    """A pause's shielded WAITING -> RUNNING restore that is still in flight
    when the run crashes must land BEFORE the settle, never after it."""
    orch = Orchestrator(platform)
    settled = threading.Event()
    real_settle = orch._settle_finished_run

    def spy_settle(*a, **kw):
        settled.set()  # runs in the thread hop, after the run rows are settled
        return real_settle(*a, **kw)

    monkeypatch.setattr(orch, "_settle_finished_run", spy_settle)

    async def go():
        s = await orch.create_session("say hi", provider="mock", model="mock-1")

        async def late_restore():
            # Lands after the settle if the finalizer does not wait for it;
            # bounded so a finalizer that DOES wait is never parked.
            for _ in range(100):
                if settled.is_set():
                    break
                await asyncio.sleep(0.01)
            with session_scope(platform.engine) as db:
                for r in db.exec(select(AgentRun).where(AgentRun.session_id == s.id)):
                    r.state = AgentState.RUNNING
                    db.add(r)
                db.commit()

        restore = asyncio.ensure_future(late_restore())
        orch.runtime._inflight_state.add(restore)
        restore.add_done_callback(orch.runtime._inflight_state.discard)
        with pytest.raises(RuntimeError):
            await orch.run_session(s.id)
        await restore  # wait for the thing we assert on
        return s.id

    sid = asyncio.run(go())
    runs = _runs(platform, sid)
    assert runs
    assert all(r.state is AgentState.FAILED for r in runs), [r.state for r in runs]
