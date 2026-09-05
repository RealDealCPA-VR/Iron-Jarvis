"""Tool interruption, the shell tree kill, and the tool-call deadline
(v1.228.0, audit Wave 2: CL1, RT3, RT6).

CL1 — a client that disconnects while a tool is mid-flight used to leave the
tool's side effect on disk with NO ToolInvocation row and NO `tool.executed`
event (`_record` sat after `execute`, and the cancel landed at the `execute`
await). Now the registry records a failed row ("client disconnected while the
tool was running — its effect may have landed") and publishes the event from
an independent task, then re-raises the cancellation. An unknown tool name is
ledgered too.

RT3 — `NativeSandbox.run` killed only cmd.exe/sh on timeout; the command it
had started ran to completion and the tool blocked until it exited (6 s for a
1 s timeout). Now Popen + communicate(timeout) + a tree kill: the tool returns
at ~timeout and the child AND grandchild are gone.

RT6 — a tool call in an agent run has a deadline (`config.tool_call_timeout_s`,
default 600 s, passed as `registry.invoke(..., deadline_s=)`): a wedged tool
becomes a recorded failed result and the run continues. A step that streams
past `_MAX_STEP_STREAM_CHARS` ends the run FAILED with the reason instead of
being consumed forever.

Converted from tests/_audit_20260904/test_chat_stream_disconnect_mid_tool_audit.py,
regrade_rt_3.py and test_q1_termination.py (a)/(b).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import psutil
import pytest
from sqlmodel import select

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.config import Config
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    EventRecord,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import _SETTINGS_KEYS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.sandbox.native import NativeSandbox
from iron_jarvis.sandbox.policy import SandboxPolicy
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_chat_stream_cancel_ledger_v1192 import _frames, _scope


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _call(name: str, args: dict, i: int = 1) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id=f"c{i}", name=name, arguments=args)],
        finish_reason="tool_use",
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(text=text, finish_reason="stop")


def _script(platform, responses) -> None:
    platform.providers.register(
        "mock", lambda model=None: MockLLMAdapter(script=list(responses))
    )


def _runs_for(platform, session_id: str) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(AgentRun).where(AgentRun.session_id == session_id)))
        for r in rows:
            db.expunge(r)
        return rows


def _invocations(engine, tool: str) -> list[ToolInvocation]:
    with session_scope(engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


def _tool_events(engine, tool: str) -> list[dict]:
    with session_scope(engine) as db:
        out = []
        for r in db.exec(select(EventRecord)):
            if r.type != "tool.executed":
                continue
            payload = json.loads(r.payload_json or "{}")
            if payload.get("tool") == tool:
                out.append(payload)
        return out


async def _wait_for_rows(fn, *, want: int, seconds: float = 3.0):
    deadline = time.monotonic() + seconds
    rows = fn()
    while len(rows) < want and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        rows = fn()
    return rows


def _ctx(p, tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="t-session",
        agent_run_id="t-run",
        config=p.config,
        event_bus=p.event_bus,
        engine=p.engine,
    )


async def _drive(app, body: dict, abort: asyncio.Event) -> list[tuple[str, dict]]:
    """Drive /chat/stream through the raw ASGI app and disconnect on `abort`."""
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await abort.wait()
        return {"type": "http.disconnect"}

    q: asyncio.Queue = asyncio.Queue()

    async def send(msg):
        await q.put(msg)

    task = asyncio.create_task(app(_scope("/chat/stream", raw), receive, send))
    frames: list[tuple[str, dict]] = []
    buf = ""
    try:
        while True:
            get = asyncio.create_task(q.get())
            done, _ = await asyncio.wait(
                {get, task}, return_when=asyncio.FIRST_COMPLETED, timeout=30
            )
            if get in done:
                msg = get.result()
            else:
                get.cancel()
                if task in done:
                    break
                raise AssertionError("stream produced nothing for 30s")
            if msg["type"] != "http.response.body":
                continue
            buf += msg.get("body", b"").decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                frames.extend(_frames(block + "\n\n"))
            if not msg.get("more_body", False):
                break
    finally:
        await task
    return frames


# --------------------------------------------------------------------------- #
# CL1 — a disconnect mid-tool is on the ledger
# --------------------------------------------------------------------------- #
class _SlowWriteTool(Tool):
    name = "slow_write_v1228"
    description = "fixture: a slow irreversible write on a worker thread"
    reversibility = Reversibility.IRREVERSIBLE
    parameters = {"type": "object", "properties": {}}
    target: Path | None = None
    started: asyncio.Event | None = None
    loop: asyncio.AbstractEventLoop | None = None

    async def execute(self, args, ctx):
        def _work():
            assert self.loop is not None and self.started is not None
            self.loop.call_soon_threadsafe(self.started.set)
            time.sleep(0.8)
            assert self.target is not None
            self.target.write_text("side effect landed", encoding="utf-8")
            return "wrote it"

        out = await asyncio.to_thread(_work)
        return ToolResult(ok=True, output=out)


@pytest.mark.asyncio
async def test_disconnect_mid_tool_records_a_failed_row_and_event(tmp_path):
    app = create_app(str(tmp_path))
    plat = app.state.platform
    tool = _SlowWriteTool()
    tool.target = tmp_path / "effect.txt"
    tool.started = asyncio.Event()
    tool.loop = asyncio.get_running_loop()
    plat.registry.register(tool)
    plat.permissions._base[tool.name] = "allow"

    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        if rounds["n"] == 0:
            rounds["n"] += 1
            resp = LLMResponse(
                text="", tool_calls=[ToolCall(id="c1", name=tool.name, arguments={})],
                usage={"input_tokens": 10, "output_tokens": 2},
            )
            yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}
        else:
            yield {"type": "final", "response": LLMResponse(text="done"),
                   "provider": "mock", "model": "mock"}

    plat.router.stream = fake_stream
    abort = asyncio.Event()

    async def _abort_once_started():
        await tool.started.wait()
        abort.set()

    aborter = asyncio.create_task(_abort_once_started())
    frames = await _drive(
        app,
        {"messages": [{"role": "user", "content": "go"}], "tools": [tool.name],
         "auto_tools": False},
        abort,
    )
    await aborter
    assert any(ev == "tool_call" and d.get("status") == "started" for ev, d in frames)
    assert not any(ev == "done" for ev, _ in frames)

    await asyncio.sleep(1.5)  # let the abandoned worker thread finish
    assert tool.target.exists(), "the tool's side effect still lands (the thread runs on)"

    invs = await _wait_for_rows(lambda: _invocations(plat.engine, tool.name), want=1)
    assert len(invs) == 1, invs
    row = invs[0]
    assert row.ok is False
    assert "client disconnected while the tool was running" in row.output
    assert "its effect may have landed" in row.output
    assert row.reversibility == "irreversible"
    assert row.session_id == "chat"

    events = await _wait_for_rows(lambda: _tool_events(plat.engine, tool.name), want=1)
    assert len(events) == 1, events
    assert events[0]["ok"] is False
    assert events[0]["interrupted"] is True
    assert events[0]["invocation_id"] == row.id


@pytest.mark.asyncio
async def test_cancelled_agent_run_tool_is_ledgered_as_cancelled(tmp_path):
    """The same seam from an AGENT run (session id != "chat"): the row says
    the run was cancelled, not that a client disconnected, and the
    cancellation still unwinds."""
    p = build_platform(str(tmp_path / "home"))
    tool = _SleepTool()
    tool.seconds = 5.0
    p.registry.register(tool)
    perms = PermissionEngine({**p.config.permissions, "sleep_v1228": "allow"})
    ctx = _ctx(p, tmp_path)  # session_id "t-session"
    task = asyncio.create_task(p.registry.invoke("sleep_v1228", {}, ctx, perms))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    invs = await _wait_for_rows(lambda: _invocations(p.engine, "sleep_v1228"), want=1)
    assert len(invs) == 1 and invs[0].ok is False
    assert invs[0].output == (
        "the run was cancelled while the tool was running — its effect may have landed"
    )
    assert invs[0].session_id == "t-session"
    events = await _wait_for_rows(lambda: _tool_events(p.engine, "sleep_v1228"), want=1)
    assert events[0]["ok"] is False and events[0]["interrupted"] is True


@pytest.mark.asyncio
async def test_unknown_tool_name_is_ledgered(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    ctx = _ctx(p, tmp_path)
    res = await p.registry.invoke("no_such_tool_v1228", {"x": 1}, ctx, p.permissions)
    assert res.ok is False and "unknown tool" in (res.error or "")
    invs = await _wait_for_rows(lambda: _invocations(p.engine, "no_such_tool_v1228"), want=1)
    assert len(invs) == 1 and invs[0].ok is False
    assert "unknown tool 'no_such_tool_v1228'" in invs[0].output
    events = await _wait_for_rows(
        lambda: _tool_events(p.engine, "no_such_tool_v1228"), want=1
    )
    assert len(events) == 1 and events[0]["ok"] is False


# --------------------------------------------------------------------------- #
# RT6 — the registry deadline (chat, passing none, is unchanged)
# --------------------------------------------------------------------------- #
class _SleepTool(Tool):
    name = "sleep_v1228"
    description = "fixture: sleeps a while"
    reversibility = Reversibility.READONLY
    parameters = {"type": "object", "properties": {}}
    seconds = 1.0

    async def execute(self, args, ctx):
        await asyncio.sleep(self.seconds)
        return ToolResult(ok=True, output="slept")


@pytest.mark.asyncio
async def test_registry_deadline_records_a_failed_row_and_none_means_no_deadline(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    p.registry.register(_SleepTool())
    perms = PermissionEngine({**p.config.permissions, "sleep_v1228": "allow"})
    ctx = _ctx(p, tmp_path)

    t0 = time.monotonic()
    res = await p.registry.invoke("sleep_v1228", {}, ctx, perms, deadline_s=0.2)
    elapsed = time.monotonic() - t0
    assert res.ok is False
    assert res.error == "sleep_v1228 did not finish within 0.2 s — it was stopped"
    assert elapsed < 0.9, elapsed
    invs = _invocations(p.engine, "sleep_v1228")
    assert len(invs) == 1 and invs[0].ok is False
    assert "did not finish within 0.2 s" in invs[0].output
    events = await _wait_for_rows(lambda: _tool_events(p.engine, "sleep_v1228"), want=1)
    assert events[-1]["ok"] is False

    # No deadline (the chat lanes' call shape) still lets the call finish.
    res2 = await p.registry.invoke("sleep_v1228", {}, ctx, perms)
    assert res2.ok is True and res2.output == "slept"


def test_tool_deadline_setting_is_registered_and_defaults_to_600(tmp_path):
    cfg = Config(project_root=tmp_path, home=tmp_path / ".ironjarvis")
    assert cfg.tool_call_timeout_s == 600
    assert "tool_call_timeout_s" in _SETTINGS_KEYS
    assert runtime_mod._tool_deadline(cfg) == 600.0
    cfg.tool_call_timeout_s = 0
    assert runtime_mod._tool_deadline(cfg) is None
    cfg.tool_call_timeout_s = 7
    assert runtime_mod._tool_deadline(cfg) == 7.0


# --------------------------------------------------------------------------- #
# RT6 — the runtime passes the deadline and the run CONTINUES
# --------------------------------------------------------------------------- #
class _HangTool(Tool):
    name = "hang_forever_v1228"
    description = "fixture: a tool that never returns (a wedged subprocess/MCP)"
    permission_key = "hang_forever_v1228"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, ctx: ToolContext) -> ToolResult:
        await asyncio.Event().wait()
        return ToolResult(ok=True, output="never")


@pytest.mark.asyncio
async def test_hung_tool_in_a_run_is_stopped_recorded_and_the_run_continues(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    p.config.tool_call_timeout_s = 1
    p.registry.register(_HangTool())
    p.permissions = PermissionEngine({**p.config.permissions, "hang_forever_v1228": "allow"})
    orch = Orchestrator(p)
    defn = AgentDefinition(
        type=AgentType.BUILDER, system_prompt="t", tools=["hang_forever_v1228", "read_file"]
    )
    _script(p, [_call("hang_forever_v1228", {}), _final("done after the hang")])
    s = await orch.create_session("poke the wedged tool once", AgentType.BUILDER)
    t0 = time.monotonic()
    row = await asyncio.wait_for(orch.run_session(s.id, defn), timeout=20)
    elapsed = time.monotonic() - t0
    assert elapsed < 10, elapsed
    assert row.status is SessionStatus.COMPLETED, (row.status, row.summary)
    assert row.summary == "done after the hang"
    tools = orch.transcript(s.id)["tools"]
    hung = [t for t in tools if t["tool"] == "hang_forever_v1228"]
    assert len(hung) == 1 and hung[0]["ok"] is False
    assert "hang_forever_v1228 did not finish within 1 s — it was stopped" in (
        hung[0]["output"] or ""
    )
    runs = _runs_for(p, s.id)
    assert len(runs) == 1 and runs[0].state is AgentState.COMPLETED


# --------------------------------------------------------------------------- #
# RT6 — a step that streams forever is cut off honestly
# --------------------------------------------------------------------------- #
class _ForeverRouter:
    default_provider = "mock"

    def __init__(self) -> None:
        self.frames = 0

    async def stream(self, **kw):
        while True:
            self.frames += 1
            yield {"type": "text", "text": "la " * 400}
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stream_past_the_step_ceiling_ends_the_run_failed_with_the_reason(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    router = _ForeverRouter()
    p.router = router
    orch = Orchestrator(p)
    s = await orch.create_session("say hello", AgentType.BUILDER)
    row = await asyncio.wait_for(orch.run_session(s.id), timeout=20)
    assert row.status is SessionStatus.FAILED
    assert "streamed more than 200,000 characters in step 1" in (row.summary or "")
    assert "cut off" in (row.summary or "")
    runs = _runs_for(p, s.id)
    assert len(runs) == 1 and runs[0].state is AgentState.FAILED and runs[0].steps == 1
    # ~170 frames carry 200k chars; the stream was closed, not consumed forever.
    assert router.frames < 2000, router.frames


# --------------------------------------------------------------------------- #
# RT3 — the native shell timeout kills the whole tree
# --------------------------------------------------------------------------- #
def _pid_gone(pid: int) -> bool:
    try:
        if not psutil.pid_exists(pid):
            return True
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.Error:
        return True


def test_native_timeout_kills_child_and_grandchild_and_returns_on_time(tmp_path):
    child_pid = tmp_path / "child.pid"
    grand_pid = tmp_path / "grand.pid"
    marker = tmp_path / "STILL_RAN.txt"
    grand = tmp_path / "grandchild.py"
    grand.write_text(
        "import os, time, pathlib\n"
        f"pathlib.Path({str(grand_pid)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(8)\n",
        encoding="utf-8",
    )
    prog = tmp_path / "sleeper.py"
    prog.write_text(
        "import os, subprocess, sys, time, pathlib\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
        f"subprocess.Popen([sys.executable, {str(grand)!r}])\n"
        "time.sleep(8)\n"
        f"pathlib.Path({str(marker)!r}).write_text('alive')\n",
        encoding="utf-8",
    )
    limit = 1.0
    sb = NativeSandbox(SandboxPolicy(timeout_s=limit, modify_env="allow"))
    t0 = time.monotonic()
    res = sb.run(f'"{sys.executable}" "{prog}"', cwd=tmp_path, timeout=limit)
    elapsed = time.monotonic() - t0
    assert res.timed_out is True
    assert res.returncode == -1
    # The tool returns at ~timeout, not when the command feels like exiting
    # (measured before the fix: 4-6 s for a 1 s timeout).
    assert elapsed < limit + 1.5, elapsed
    assert child_pid.exists() and grand_pid.exists(), "the tree had started before the deadline"
    pids = [int(child_pid.read_text()), int(grand_pid.read_text())]
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not all(_pid_gone(pid) for pid in pids):
        time.sleep(0.1)
    assert all(_pid_gone(pid) for pid in pids), f"still alive: {pids}"
    assert not marker.exists(), "the command ran to completion after the timeout"


def test_native_result_shape_unchanged_for_a_normal_command(tmp_path):
    sb = NativeSandbox(SandboxPolicy(modify_env="allow"))
    res = sb.run(f'"{sys.executable}" -c "print(\'hi\')"', cwd=tmp_path, timeout=20)
    assert res.timed_out is False and res.returncode == 0
    assert res.stdout.strip() == "hi"


# RT6 fix round — a TimeoutError the TOOL raises is NOT the deadline.
# On 3.11+ `asyncio.TimeoutError is TimeoutError`, so a handler that catches the
# builtin and assumes it came from its own deadline (a) crashed formatting
# ``None`` for the no-deadline call shape both chat lanes use and (b) ledgered
# the tool's own failure with the deadline wording. Only `cm.expired()` is ours.
# --------------------------------------------------------------------------- #
class _RaisesTimeoutTool(Tool):
    name = "raises_timeout_v1228"
    description = "fixture: raises its own TimeoutError"
    reversibility = Reversibility.READONLY
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args, ctx):
        raise TimeoutError("socket read timed out after 30s")


@pytest.mark.asyncio
async def test_tool_raised_timeout_keeps_its_own_words_with_and_without_a_deadline(tmp_path):
    assert asyncio.TimeoutError is TimeoutError  # the premise this guards
    p = build_platform(str(tmp_path / "home"))
    p.registry.register(_RaisesTimeoutTool())
    perms = PermissionEngine({**p.config.permissions, "raises_timeout_v1228": "allow"})
    ctx = _ctx(p, tmp_path)

    # No deadline (chat's call shape): an ordinary failed result, one row.
    res = await p.registry.invoke("raises_timeout_v1228", {}, ctx, perms, deadline_s=None)
    assert res.ok is False
    assert res.error.startswith("TimeoutError: socket read timed out after 30s"), res.error
    assert "did not finish within" not in res.error
    invs = _invocations(p.engine, "raises_timeout_v1228")
    assert len(invs) == 1 and invs[0].ok is False
    assert "socket read timed out after 30s" in invs[0].output

    # With a deadline (every agent run): still the tool's message, not ours.
    res2 = await p.registry.invoke("raises_timeout_v1228", {}, ctx, perms, deadline_s=600)
    assert res2.ok is False
    assert "socket read timed out after 30s" in res2.error
    assert "did not finish within" not in res2.error
    invs = _invocations(p.engine, "raises_timeout_v1228")
    assert len(invs) == 2 and invs[1].ok is False
    assert "socket read timed out after 30s" in invs[1].output
    assert "did not finish within" not in invs[1].output
