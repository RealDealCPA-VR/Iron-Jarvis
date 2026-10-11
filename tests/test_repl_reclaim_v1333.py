"""REPL children are reclaimed (v1.333.0, D3) and a run's in-run maps are
dropped when it ends (D9).

``ReplRegistry.sweep`` / ``dispose`` / ``dispose_all`` had no callers: an idle
child was reclaimed only when a 17th namespace was asked for, so every
finished agent run kept its interpreter, and the daemon never killed any at
shutdown. Now the daemon sweeps once a minute, a finished agent run disposes
its own namespaces, and the lifespan's teardown disposes them all.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon import app as app_mod
from iron_jarvis.repl.session import ReplRegistry, ReplSession, namespace_key


def _plant(registry: ReplRegistry, session_id: str, workspace, *, idle_s: float = 0.0) -> ReplSession:
    """A namespace as the registry holds one, never spawned (the child is lazy)."""
    s = ReplSession(session_id, workspace)
    s.last_used = time.monotonic() - idle_s
    registry._sessions[namespace_key(session_id, workspace)] = s
    return s


async def _until(predicate, cap_s: float = 10.0) -> None:
    """Liveness cap only: yield until *predicate* holds (never a speed bar)."""
    deadline = time.monotonic() + cap_s
    while not predicate():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_the_sweep_loop_disposes_idle_namespaces_and_keeps_live_and_busy_ones(tmp_path):
    reg = ReplRegistry(idle_ttl_s=900)
    stale = _plant(reg, "s-stale", tmp_path, idle_s=1000)
    fresh = _plant(reg, "s-fresh", tmp_path, idle_s=5)
    busy = _plant(reg, "s-busy", tmp_path, idle_s=1000)
    await busy._lock.acquire()  # a cell has been running since 1000 s ago
    sweeps: list[list[str]] = []
    real_sweep = reg.sweep

    async def counted_sweep():
        gone = await real_sweep()
        sweeps.append(gone)
        return gone

    reg.sweep = counted_sweep
    loop = asyncio.create_task(app_mod._repl_sweep_loop(reg, interval_s=0))
    try:
        await _until(lambda: len(sweeps) >= 3)  # whole passes, not a moment mid-pass
    finally:
        loop.cancel()
        busy._lock.release()
    assert stale._closed is True
    assert reg.session_ids == ["s-fresh", "s-busy"]
    assert fresh._closed is False and busy._closed is False


@pytest.mark.asyncio
async def test_a_finished_agent_run_disposes_its_own_namespaces_and_leaves_chat_alone(
    platform, tmp_path
):
    orch = Orchestrator(platform)
    session = await orch.create_session("say hello")
    own_a = _plant(platform.repl, session.id, tmp_path / "a")
    own_b = _plant(platform.repl, session.id, tmp_path / "b")
    chat = _plant(platform.repl, "chat", tmp_path)
    await orch.runtime.run(session, get_agent_definition(AgentType.BUILDER))
    assert session.id not in platform.repl
    assert own_a._closed and own_b._closed
    assert "chat" in platform.repl and chat._closed is False


@pytest.mark.asyncio
async def test_the_idle_sweep_spares_a_run_still_in_progress(tmp_path):
    # Review (v1.333.0): an attended run parked on an unanswered ask (no clock)
    # or a long model call is idle, not finished; its next step expects its
    # variables. The timer spares it; the run's own end disposes it.
    reg = ReplRegistry(idle_ttl_s=900)
    waiting = _plant(reg, "s-waiting", tmp_path, idle_s=1000)
    reg.hold("s-waiting")
    reg.hold("s-waiting")  # counted: two holds need two releases
    assert await reg.sweep() == []
    assert waiting._closed is False
    reg.release("s-waiting")
    assert await reg.sweep() == []
    reg.release("s-waiting")
    assert await reg.sweep() == ["s-waiting"]
    assert waiting._closed is True


@pytest.mark.asyncio
async def test_a_running_agent_holds_its_namespace_until_the_run_ends(platform, tmp_path, monkeypatch):
    orch = Orchestrator(platform)
    rt = orch.runtime
    session = await orch.create_session("say hello")
    own = _plant(platform.repl, session.id, tmp_path / "a", idle_s=10_000)
    real_body = rt._run_body
    seen: dict[str, object] = {}

    async def body_that_waits_long(sess, definition, parent_id=None):
        # The minute timer firing while the run waits on the user.
        seen["swept"] = await platform.repl.sweep()
        seen["alive"] = not own._closed
        return await real_body(sess, definition, parent_id)

    monkeypatch.setattr(rt, "_run_body", body_that_waits_long)
    await rt.run(session, get_agent_definition(AgentType.BUILDER))
    assert seen == {"swept": [], "alive": True}
    assert own._closed is True  # the run's end disposed it
    assert session.id not in platform.repl._held


@pytest.mark.asyncio
async def test_a_runs_grants_and_taint_are_dropped_when_it_ends(platform, monkeypatch):
    orch = Orchestrator(platform)
    rt = orch.runtime
    session = await orch.create_session("say hello")
    real_body = rt._run_body

    async def body_that_grants(sess, definition, parent_id=None):
        run = await real_body(sess, definition, parent_id)
        # What an "Allow for this run" answer and a flagged read leave behind.
        rt._run_grants.setdefault(sess.id, set()).add("shell")
        rt._tainted[sess.id] = True
        return run

    monkeypatch.setattr(rt, "_run_body", body_that_grants)
    await rt.run(session, get_agent_definition(AgentType.BUILDER))
    assert session.id not in rt._run_grants
    assert session.id not in rt._tainted


async def _task_names() -> set[str]:
    return {getattr(t.get_coro(), "__name__", "") for t in asyncio.all_tasks()}


def test_the_daemon_sweeps_while_up_and_disposes_every_namespace_at_shutdown(tmp_path, monkeypatch):
    calls: list[str] = []
    real = ReplRegistry.dispose_all

    async def spy(self):
        calls.append("dispose_all")
        await real(self)

    monkeypatch.setattr(ReplRegistry, "dispose_all", spy)
    app = app_mod.create_app(str(tmp_path))
    with TestClient(app) as client:
        assert "_repl_sweep_loop" in client.portal.call(_task_names)
        assert calls == []
    assert calls == ["dispose_all"]
