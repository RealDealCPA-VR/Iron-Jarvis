"""v1.335.0 — a tool call is recorded as STARTED before it runs.

Borrowed from pi-durable ("the call's intent is committed before execute").
Before this a process that died mid-call left nothing: the effect could land
with no ledger row at all. Now ``registry.invoke`` writes a ``ToolIntent``
before ``tool.execute``; the finished row takes the intent's id and removes it
in the same commit; and the boot step ``reconcile_interrupted_tool_calls``
turns any intent a dead process left behind into a failed row that says so —
and says whether the call is safe to run again (only reads are).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.tools.registry as registry_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import new_id
from iron_jarvis.core.models import PermissionMode, ToolIntent, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult, replay_safe
from iron_jarvis.tools.permissions import PermissionEngine


class _ParkedTool(Tool):
    """Waits until the test releases it, so the test can look mid-call."""

    name = "parked_v1335"
    description = "fixture"
    reversibility = Reversibility.IRREVERSIBLE
    input_schema = {"type": "object", "properties": {"secret": {"type": "string"}}}
    started: asyncio.Event
    release: asyncio.Event

    def redact_args(self, args):
        return {k: ("***" if k == "secret" else v) for k, v in args.items()}

    async def execute(self, args, ctx):
        self.started.set()
        await self.release.wait()
        return ToolResult(ok=True, output="done")


class _ReadTool(Tool):
    name = "read_v1335"
    description = "fixture"
    reversibility = Reversibility.READONLY
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, ctx):
        return ToolResult(ok=True, output="read")


def _ctx(p, tmp_path, sid="s-1335") -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id=sid,
        agent_run_id="run-1335",
        config=p.config,
        event_bus=p.event_bus,
        engine=p.engine,
    )


def _all(engine, model):
    with session_scope(engine) as db:
        rows = list(db.exec(select(model)).all())
        for r in rows:
            db.expunge(r)
        return rows


async def _until(fn, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        await asyncio.sleep(0.02)
    return fn()


@pytest.mark.asyncio
async def test_a_running_call_has_an_intent_and_the_finished_row_takes_its_id(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    tool = _ParkedTool()
    tool.started, tool.release = asyncio.Event(), asyncio.Event()
    p.registry.register(tool)
    perms = PermissionEngine({**p.config.permissions, tool.name: "allow"})
    task = asyncio.create_task(
        p.registry.invoke(tool.name, {"secret": "hunter2", "n": 1}, _ctx(p, tmp_path), perms)
    )
    await asyncio.wait_for(tool.started.wait(), 3)
    intents = _all(p.engine, ToolIntent)
    # Written BEFORE execute: it is already there while the tool runs.
    assert len(intents) == 1
    it = intents[0]
    assert it.tool == tool.name and it.session_id == "s-1335" and it.agent_run_id == "run-1335"
    assert "hunter2" not in it.args_json and '"***"' in it.args_json  # redacted like the row
    assert it.replay_safe is False and it.reversibility == "irreversible"
    assert _all(p.engine, ToolInvocation) == []
    tool.release.set()
    res = await task
    assert res.ok
    rows = _all(p.engine, ToolInvocation)
    assert [r.id for r in rows] == [it.id]  # the record IS the intent's
    assert rows[0].ok is True and rows[0].output == "done"
    assert _all(p.engine, ToolIntent) == []  # gone in the same commit


@pytest.mark.asyncio
async def test_a_cancelled_call_leaves_one_row_under_the_intent_id_and_no_intent(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    tool = _ParkedTool()
    tool.started, tool.release = asyncio.Event(), asyncio.Event()
    p.registry.register(tool)
    perms = PermissionEngine({**p.config.permissions, tool.name: "allow"})
    task = asyncio.create_task(p.registry.invoke(tool.name, {}, _ctx(p, tmp_path), perms))
    await asyncio.wait_for(tool.started.wait(), 3)
    intent_id = _all(p.engine, ToolIntent)[0].id
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = await _until(lambda: _all(p.engine, ToolInvocation))
    assert [r.id for r in rows] == [intent_id]
    assert rows[0].ok is False and "may have landed" in rows[0].output
    assert await _until(lambda: _all(p.engine, ToolIntent) == [] or None)


def _plant_intent(engine, *, safe: bool, tool="write_file", sid="sess-dead") -> str:
    iid = new_id("tool")
    with session_scope(engine) as db:
        db.add(
            ToolIntent(
                id=iid,
                session_id=sid,
                agent_run_id="run-dead",
                tool=tool,
                args_json='{"path": "a.txt"}',
                verdict=PermissionMode.ALLOW,
                reversibility="readonly" if safe else "reversible",
                replay_safe=safe,
            )
        )
        db.commit()
    return iid


def test_boot_reconcile_records_what_a_dead_process_was_running(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    risky = _plant_intent(p.engine, safe=False)
    safe = _plant_intent(p.engine, safe=True, tool="read_file")
    assert registry_mod.reconcile_interrupted_tool_calls(p.engine) == 2
    rows = {r.id: r for r in _all(p.engine, ToolInvocation)}
    assert set(rows) == {risky, safe}
    assert rows[risky].ok is False
    assert rows[risky].output == registry_mod.INTERRUPTED_MAY_HAVE_LANDED
    assert rows[safe].output == registry_mod.INTERRUPTED_SAFE
    assert rows[risky].session_id == "sess-dead" and rows[risky].args_json == '{"path": "a.txt"}'
    assert _all(p.engine, ToolIntent) == []
    # Idempotent: a second boot finds nothing.
    assert registry_mod.reconcile_interrupted_tool_calls(p.engine) == 0


def test_an_intent_whose_row_already_exists_is_only_removed(tmp_path):
    """A cancel can write its row before a late intent write lands."""
    p = build_platform(str(tmp_path / "home"))
    iid = _plant_intent(p.engine, safe=False)
    with session_scope(p.engine) as db:
        db.add(ToolInvocation(id=iid, session_id="sess-dead", agent_run_id="r", tool="write_file",
                              ok=False, output="the run was cancelled"))
        db.commit()
    assert registry_mod.reconcile_interrupted_tool_calls(p.engine) == 0
    rows = _all(p.engine, ToolInvocation)
    assert [(r.id, r.output) for r in rows] == [(iid, "the run was cancelled")]
    assert _all(p.engine, ToolIntent) == []


def test_the_daemon_boot_runs_the_reconcile(tmp_path):
    home = str(tmp_path / "home")
    p = build_platform(home)
    iid = _plant_intent(p.engine, safe=False)
    p.engine.dispose()
    app = create_app(home)
    with TestClient(app) as client:
        loops = client.get("/diagnostics").json().get("background_loops", {})
        assert loops.get("reconcile_tool_calls", {}).get("ok") is True
    rows = _all(app.state.platform.engine, ToolInvocation)
    assert [r.id for r in rows] == [iid]
    assert rows[0].output == registry_mod.INTERRUPTED_MAY_HAVE_LANDED
    # The Activity page reads EVENTS: the cut-off call is there too.
    import json

    from iron_jarvis.core.models import EventRecord

    evs = [
        json.loads(e.payload_json)
        for e in _all(app.state.platform.engine, EventRecord)
        if e.type == "tool.executed"
    ]
    assert [(e["invocation_id"], e["ok"], e["interrupted"]) for e in evs] == [(iid, False, True)]


@pytest.mark.asyncio
async def test_a_failed_intent_write_never_blocks_the_tool(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    p.registry.register(_ReadTool())
    perms = PermissionEngine({**p.config.permissions, "read_v1335": "allow"})

    def _broken(_tool):
        raise RuntimeError("disk full")

    # Anything raising inside the intent write (here: building the row).
    monkeypatch.setattr(registry_mod, "replay_safe", _broken)
    res = await p.registry.invoke("read_v1335", {}, _ctx(p, tmp_path), perms)
    assert res.ok and res.output == "read"
    assert len(_all(p.engine, ToolInvocation)) == 1


def test_replay_safe_is_reads_only_unless_a_tool_says_otherwise():
    assert replay_safe(_ReadTool()) is True
    assert replay_safe(_ParkedTool()) is False
    assert replay_safe(None) is False

    class _Idempotent(_ParkedTool):
        replay = True

    class _ReadButNot(_ReadTool):
        replay = False

    assert replay_safe(_Idempotent()) is True
    assert replay_safe(_ReadButNot()) is False
