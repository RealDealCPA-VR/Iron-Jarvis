"""v1.505.0 (north-star 5.1): a workflow TOOL step has a time limit, and
Cancel stops it while it runs.

What was read (not guessed): the engine called ``registry.invoke`` for a tool
step with no ``deadline_s``, and the registry arms its timeout only when one is
passed, so a pack call that never answered held the run (and, for a schedule's
fire, the scheduler thread) for ever. The cancel route flipped the row to
``cancelling`` and cancelled ``current_session_id`` -- None for a tool step --
and the engine re-reads the flag only between batches, so Cancel waited on the
hung call too.

A hung tool here is an Event the test never sets. Time is the injected pair of
clocks the v1.504.0 sleep tests use (``tests/test_north_star_sleep_v1504.py``):
the loop's clock and the registry's awake clock move forward 601 s at once.
Never a sleep.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import asyncio
import json

import pytest
from sqlmodel import select

import iron_jarvis.tools.registry as registry_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine
from iron_jarvis.workflows.models import WorkflowRunRecord
from tests.test_chat_approvals_v1187 import _asgi_post


class _Hangs(Tool):
    """A read-only tool whose call never answers until the test says so."""

    name = "hangs_v1505"
    description = "fixture"
    reversibility = Reversibility.READONLY
    input_schema = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = False

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.stopped = True
            raise
        return ToolResult(ok=True, output="answered")


class _Clocks:
    """The loop's clock and the registry's awake clock, both injected."""

    def __init__(self, monkeypatch) -> None:
        self.loop = asyncio.get_running_loop()
        self._real = self.loop.time
        self.offset = 0.0
        self.awake = 0.0
        self.loop.time = lambda: self._real() + self.offset  # type: ignore[method-assign]
        monkeypatch.setattr(registry_mod, "_AWAKE_CLOCK", lambda: self.awake)

    def restore(self) -> None:
        del self.loop.time

    def awake_for(self, seconds: float) -> None:
        self.offset += seconds
        self.awake += seconds


def _wf() -> WorkflowDef:
    return WorkflowDef(
        name="rent-list",
        steps=[
            Step(name="Fetch", kind="tool", tool=_Hangs.name),
            Step(name="Tell", kind="notify", message="after"),
        ],
    )


def _row(platform, run_id) -> WorkflowRunRecord:
    with session_scope(platform.engine) as db:
        return db.get(WorkflowRunRecord, run_id)


def _tool_rows(platform, run_id) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(select(ToolInvocation).where(ToolInvocation.session_id == run_id)).all()
        )
        for r in rows:
            db.expunge(r)
        return rows


def _completed_events(platform, run_id) -> list:
    return [
        e for e in platform.event_bus.history
        if e.type == "workflow.completed" and e.payload.get("run_id") == run_id
    ]


async def test_a_hung_tool_step_is_stopped_at_the_deadline_and_the_run_fails(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    platform.config.tool_call_timeout_s = 600
    tool = _Hangs()
    platform.registry.register(tool)
    engine = WorkflowEngine(platform)
    clocks = _Clocks(monkeypatch)
    try:
        rec = await asyncio.to_thread(engine.create_record, _wf())
        task = asyncio.create_task(engine.run_record(rec, _wf()))
        await asyncio.wait_for(tool.started.wait(), 5)
        clocks.awake_for(601)  # past the setting's 600 s, awake the whole time
        done = await asyncio.wait_for(task, 5)
    finally:
        clocks.restore()
    assert tool.stopped, "the hung call was never stopped"
    assert done.status == "failed"
    outs = json.loads(done.outputs_json)
    assert outs["Fetch"]["status"] == "failed"
    assert outs["Fetch"]["summary"].startswith(f"{_Hangs.name} did not finish within 600 s")
    assert outs["Tell"] == {"status": "skipped"}
    # The registry's own ledger holds the call, as a failed row.
    rows = [r for r in _tool_rows(platform, rec.id) if r.tool == _Hangs.name]
    assert rows and rows[-1].ok is False
    ev = _completed_events(platform, rec.id)
    assert ev and ev[-1].payload["status"] == "failed"


async def test_no_deadline_setting_means_none_is_passed(tmp_path, monkeypatch):
    """The step reads the SAME setting an agent run and both chat lanes read
    (``runtime._tool_deadline``), live per call: 0 = no deadline."""
    platform = build_platform(str(tmp_path))
    platform.config.tool_call_timeout_s = 0
    seen: list = []
    real = platform.registry.invoke

    async def spy(*a, **kw):
        seen.append(kw.get("deadline_s", "absent"))
        return await real(*a, **kw)

    monkeypatch.setattr(platform.registry, "invoke", spy)
    tool = _Hangs()
    tool.release.set()
    platform.registry.register(tool)
    rec = await WorkflowEngine(platform).run(
        WorkflowDef(name="once", steps=[Step(name="Fetch", kind="tool", tool=_Hangs.name)])
    )
    assert rec.status == "completed"
    assert seen == [None]
    platform.config.tool_call_timeout_s = 45
    seen.clear()
    await WorkflowEngine(platform).run(
        WorkflowDef(name="twice", steps=[Step(name="Fetch", kind="tool", tool=_Hangs.name)])
    )
    assert seen == [45.0]


async def test_cancel_stops_a_tool_step_while_it_runs(tmp_path):
    """Through the REAL cancel route (a sync handler: it runs on a worker
    thread, so the cancel crosses threads exactly as in the app)."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.tool_call_timeout_s = 600
    tool = _Hangs()
    platform.registry.register(tool)
    engine = WorkflowEngine(platform)
    rec = await asyncio.to_thread(engine.create_record, _wf())
    task = asyncio.create_task(engine.run_record(rec, _wf()))
    await asyncio.wait_for(tool.started.wait(), 5)

    status, body = await _asgi_post(app, f"/workflows/runs/{rec.id}/cancel", {})
    assert status == 200, body
    assert json.loads(body)["status"] == "cancelling"

    done = await asyncio.wait_for(task, 5)
    assert tool.stopped, "Cancel never reached the tool call"
    assert done.status == "cancelled"
    outs = json.loads(done.outputs_json)
    assert outs["Fetch"]["status"] == "cancelled"
    assert outs["Fetch"]["summary"] == (
        "Stopped: the run was cancelled while this step was running. "
        "What the step was doing may already have happened."
    )
    assert outs["Tell"] == {"status": "skipped"}
    # The registry's cancel path ledgered the call it was in.
    rows = [r for r in _tool_rows(platform, rec.id) if r.tool == _Hangs.name]
    assert rows and rows[-1].ok is False
    assert "cancelled while the tool was running" in (rows[-1].output or "")
    ev = _completed_events(platform, rec.id)
    assert ev and ev[-1].payload["status"] == "cancelled"
    assert _row(platform, rec.id).status == "cancelled"


async def test_cancel_reaches_a_tool_step_on_another_threads_loop(tmp_path):
    """A schedule fires a workflow under ``asyncio.run`` on the scheduler's
    thread; Cancel comes from elsewhere. The step is stopped on ITS loop."""
    from iron_jarvis.workflows.engine import cancel_tool_steps

    platform = build_platform(str(tmp_path))
    platform.config.tool_call_timeout_s = 600
    tool = _Hangs()
    platform.registry.register(tool)
    engine = WorkflowEngine(platform)
    rec = engine.create_record(_wf())
    holder: dict = {}

    def fire() -> None:
        async def go():
            # The Event belongs to THIS loop (as a real fire's would).
            tool.started = asyncio.Event()
            tool.release = asyncio.Event()
            holder["loop"] = asyncio.get_running_loop()
            return await engine.run_record(rec, _wf())

        holder["rec"] = asyncio.run(go())

    import threading

    t = threading.Thread(target=fire, daemon=True)
    t.start()
    for _ in range(500):
        if "loop" in holder and tool.started.is_set():
            break
        await asyncio.sleep(0.01)
    assert tool.started.is_set(), "the fire never reached the tool"
    with session_scope(platform.engine) as db:
        row = db.get(WorkflowRunRecord, rec.id)
        row.status = "cancelling"
        db.add(row)
        db.commit()
    assert cancel_tool_steps(rec.id) == 1
    await asyncio.to_thread(t.join, 5)
    assert not t.is_alive(), "the scheduler thread is still held by the tool call"
    assert holder["rec"].status == "cancelled"
    assert cancel_tool_steps(rec.id) == 0, "a finished call is not kept"
