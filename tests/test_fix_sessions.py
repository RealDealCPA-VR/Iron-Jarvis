"""Session lifecycle: cancel / rerun / continue / delete / export."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.providers.adapters.mock import MockLLMAdapter


class BlockingMock(MockLLMAdapter):
    """A mock whose complete() blocks on a gate, so the run can be cancelled mid-flight."""

    def __init__(self, gate: asyncio.Event):
        super().__init__()
        self._gate = gate

    async def complete(self, **kw):
        await self._gate.wait()  # never set in the test -> stays cancellable
        return await super().complete(**kw)


async def _until(predicate, *, what: str, timeout: float = 10.0) -> None:
    """Wait for the condition a test is about to ASSERT — never for a clock.

    v1.257.0: these tests slept a fixed 0.05 s between starting a background run
    and asserting something that run had to have done first. That is not a wait,
    it is a bet on the scheduler: on a contended worker
    `test_cancel_settles_agent_run` read ZERO AgentRun rows (`assert ([])`),
    because the row is written through `asyncio.to_thread` and the cancel landed
    first. The `timeout` below is a safety net so a genuine hang fails loudly; it
    is NOT a speed assertion, which on CI would only measure the hardware.
    """
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting for {what}")
        await asyncio.sleep(0.01)


async def _until_run_row(platform, session_id: str) -> None:
    """Wait until the run is UNDERWAY: its AgentRun row exists and has moved
    past CREATED — which is what the old comment "let the run reach the blocking
    await" actually meant.

    Two orderings break a fixed sleep here, and waiting only for the row to
    EXIST fixes just one of them:

    * cancel before any row is written      -> `assert ([])`, the failure this
      file hit on the v1.257.0 gate run under CI-shaped load;
    * cancel while the row is still CREATED -> a row left at `created`,
      reproduced on an idle machine by shrinking the wait to `sleep(0)`.

    `AgentRunner.run` writes the row with `await asyncio.to_thread(self._save,
    run)` and `_finalize_cancelled` persists off the loop as well, so a very
    early cancel can settle nothing and let the row land afterwards. Waiting for
    the state to advance puts the cancel firmly inside the guarded region.
    """
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentRun, AgentState

    def underway() -> bool:
        with session_scope(platform.engine) as db:
            rows = list(db.exec(select(AgentRun).where(AgentRun.session_id == session_id)))
        return bool(rows) and any(r.state is not AgentState.CREATED for r in rows)

    await _until(underway, what=f"session {session_id}'s run to get past CREATED")


async def test_cancel_running_session(platform):
    gate = asyncio.Event()
    platform.providers.register("blocking", lambda: BlockingMock(gate))
    orch = Orchestrator(platform)
    sess = await orch.create_session("long task", AgentType.BUILDER, provider="blocking")

    task = asyncio.create_task(orch.run_session(sess.id))
    orch.register_running(sess.id, task)
    # Wait for the run to have actually STARTED — not for 50 ms to pass.
    # DELETE this line and the test fails with `ACTIVE is not CANCELLED`:
    # without a yield `run_session` never enters its try block, so
    # `task.cancel()` unwinds a task that never armed the CancelledError
    # handler and `_finalize_cancelled` never runs. One yield is enough here,
    # so shrinking this to `sleep(0)` stays green — the mutation that
    # falsifies THIS wait is deleting it. (The sibling test below needs more:
    # the AgentRun row itself, which is why both share this helper.)
    await _until_run_row(platform, sess.id)

    orch.cancel_session(sess.id)
    with pytest.raises(asyncio.CancelledError):
        await task

    refreshed = orch.get_session(sess.id)
    assert refreshed.status is SessionStatus.CANCELLED
    assert refreshed.finished_at is not None


async def test_cancel_settles_agent_run(platform):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentRun, AgentState

    gate = asyncio.Event()
    platform.providers.register("blk3", lambda: BlockingMock(gate))
    orch = Orchestrator(platform)
    sess = await orch.create_session("x", AgentType.BUILDER, provider="blk3")
    task = asyncio.create_task(orch.run_session(sess.id))
    orch.register_running(sess.id, task)
    await _until_run_row(platform, sess.id)
    orch.cancel_session(sess.id)
    with pytest.raises(asyncio.CancelledError):
        await task
    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(AgentRun).where(AgentRun.session_id == sess.id)))
    assert runs and all(r.state is AgentState.CANCELLED for r in runs)  # no stuck RUNNING


async def test_delete_removes_workspace_dir(platform):
    orch = Orchestrator(platform)
    s1 = await orch.run("ws delete", AgentType.BUILDER)
    ws = Path(s1.workspace_path)
    assert ws.exists()
    orch.delete_session(s1.id)
    assert not ws.exists()  # plain workspace dir cleaned up


async def test_delete_keeps_shared_workspace(platform):
    orch = Orchestrator(platform)
    s1 = await orch.run("parent", AgentType.BUILDER)
    s2 = await orch.continue_session(s1.id, "follow up")
    assert s2.workspace_path == s1.workspace_path  # non-git continuation reuses it
    orch.delete_session(s1.id)
    assert Path(s1.workspace_path).exists()  # preserved — still referenced by s2


async def test_cancel_unknown_session_raises(platform):
    orch = Orchestrator(platform)
    with pytest.raises(KeyError):
        orch.cancel_session("does-not-exist")


async def test_cancel_finished_session_raises(platform):
    orch = Orchestrator(platform)
    sess = await orch.run("quick task", AgentType.BUILDER)  # completes on mock
    assert sess.status is SessionStatus.COMPLETED
    with pytest.raises(ValueError):
        orch.cancel_session(sess.id)


async def test_rerun_clones_inputs(platform):
    orch = Orchestrator(platform)
    s1 = await orch.run("original task", AgentType.BUILDER)
    s2 = await orch.rerun_session(s1.id)
    assert s2.id != s1.id
    assert s2.task == s1.task
    assert s2.agent_type == s1.agent_type


async def test_continue_reuses_workspace_and_recaps(platform):
    orch = Orchestrator(platform)
    s1 = await orch.run("first task", AgentType.BUILDER)
    s2 = await orch.continue_session(s1.id, "now do more")
    assert s2.id != s1.id
    assert s2.workspace_path == s1.workspace_path  # reuses the workspace
    assert "now do more" in s2.task and "first task" in s2.task  # recap included


async def test_delete_removes_session_and_runs(platform):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentRun

    orch = Orchestrator(platform)
    s1 = await orch.run("to delete", AgentType.BUILDER)
    orch.delete_session(s1.id)
    assert orch.get_session(s1.id) is None
    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(AgentRun).where(AgentRun.session_id == s1.id)))
    assert runs == []


async def test_delete_running_is_refused(platform):
    gate = asyncio.Event()
    platform.providers.register("blocking2", lambda: BlockingMock(gate))
    orch = Orchestrator(platform)
    sess = await orch.create_session("x", AgentType.BUILDER, provider="blocking2")
    task = asyncio.create_task(orch.run_session(sess.id))
    orch.register_running(sess.id, task)
    # NO WAIT NEEDED HERE, deliberately. `delete_session` refuses on a LIVE
    # TASK in `_running` (orchestrator.py:1313), which `register_running`
    # above arms synchronously — not on session status. A wait here could not
    # fail, and v1.257.0 briefly had one that could not: the mutation check
    # would not redden it, which is exactly how it was caught.
    with pytest.raises(ValueError):
        orch.delete_session(sess.id)
    orch.cancel_session(sess.id)  # cleanup
    with pytest.raises(asyncio.CancelledError):
        await task


def test_export_session_md_and_json(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    sid = client.post(
        "/sessions", json={"task": "export me", "agent_type": "builder", "wait": True}
    ).json()["id"]
    md = client.get(f"/sessions/{sid}/export", params={"format": "md"})
    assert md.status_code == 200 and "Iron Jarvis session" in md.text
    js = client.get(f"/sessions/{sid}/export", params={"format": "json"})
    assert js.status_code == 200 and js.json()["session"]["id"] == sid
