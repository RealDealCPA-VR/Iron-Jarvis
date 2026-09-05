"""v1.227.0 Wave 1, lane BE-1 — THE ROSTER IS THE GATE (RT1, S1).

Converted from ``tests/_audit_20260904/test_q6_local_model_failures.py``
(the two unarmed-tool repros) and ``regrade_rt_1b``'s finding.

The names a caller armed for a step (``tool_specs``) were only ever what the
model was SHOWN; ``registry.invoke`` looked the call up by name in the FULL
registry. Measured on HEAD: a read-only user-authored agent (tools:
read_file) whose model emitted ``write_file`` wrote the file (allow-tier by
default); the built-in REVIEWER emitted ``rename_file`` and renamed the file;
chat with only ``read_file`` armed ran a ``write_file`` call. The live default
is a native tool-calling proxy, which never goes through the prompted-tools
name filter.

The fix is ONE seam: ``registry.invoke(allowed_names=...)`` refuses a name
outside the armed set THROUGH the existing ``deny_reason`` path, so the
refusal is ledgered (ToolInvocation + ``tool.denied`` kind ``not armed``) and
the model reads "`X` is not one of this agent's tools — it was not run". The
runtime passes the names of the ``tool_specs`` it armed; BOTH chat lanes pass
their armed set (lock-step, asserted below by spying the kwarg). ``None``
keeps the old behaviour for callers that do not pass a roster.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition, get_agent_definition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext

NOT_ARMED_WORDING = "is not one of this agent's tools — it was not run"


# --------------------------------------------------------------------------- #
# helpers (the audit's `_helpers.script` shape, kept local so this file has no
# dependency on the audit directory)
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


def _ledger(engine, session_id: str, tool: str) -> list[ToolInvocation]:
    with session_scope(engine) as db:
        rows = list(
            db.exec(
                select(ToolInvocation).where(
                    ToolInvocation.session_id == session_id,
                    ToolInvocation.tool == tool,
                )
            )
        )
        for r in rows:
            db.expunge(r)
        return rows


def _ctx(platform, workspace: Path, session_id: str = "gate_test") -> ToolContext:
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id="run_gate_test",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


# --------------------------------------------------------------------------- #
# 1. The registry seam itself
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_registry_refuses_a_name_outside_allowed_names_and_ledgers_it(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    published: list[dict] = []
    real_publish = p.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": type, "payload": payload or {}})
        return await real_publish(type, payload, session_id=session_id, **kw)

    p.event_bus.publish = spy

    res = await p.registry.invoke(
        "write_file",
        {"path": "PLANTED.md", "content": "planted"},
        _ctx(p, ws),
        p.permissions,
        None,
        allowed_names={"read_file"},
    )
    assert res.ok is False
    # What the model reads: the label, then the plain reason.
    assert res.error == f"not armed: `write_file` {NOT_ARMED_WORDING}", res.error
    assert not (ws / "PLANTED.md").exists()
    # Ledgered like any other denial — `agents/outcome` derives a run's story
    # from this table, and a refusal that never reached it would vanish.
    rows = _ledger(p.engine, "gate_test", "write_file")
    assert len(rows) == 1 and rows[0].ok is False
    assert NOT_ARMED_WORDING in rows[0].output
    denied = [e for e in published if e["type"] == "tool.denied"]
    assert denied and denied[0]["payload"]["tool"] == "write_file"
    assert denied[0]["payload"]["kind"] == "not armed"
    assert not [e for e in published if e["type"] == "tool.executed"]


@pytest.mark.asyncio
async def test_registry_without_a_roster_keeps_todays_behaviour(tmp_path):
    """``allowed_names=None`` (every caller not touched by this wave) runs the
    tool exactly as before; an armed name runs too."""
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await p.registry.invoke(
        "write_file", {"path": "a.md", "content": "a"}, _ctx(p, ws), p.permissions, None,
    )
    assert res.ok is True and (ws / "a.md").exists()
    res = await p.registry.invoke(
        "write_file", {"path": "b.md", "content": "b"}, _ctx(p, ws), p.permissions, None,
        allowed_names={"write_file", "read_file"},
    )
    assert res.ok is True and (ws / "b.md").exists()


@pytest.mark.asyncio
async def test_a_callers_own_deny_reason_still_wins_the_wording(tmp_path):
    """The breaker / a user's refusal arrive as ``deny_reason``; the roster
    gate never overwrites a refusal the caller already made."""
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await p.registry.invoke(
        "write_file", {"path": "c.md", "content": "c"}, _ctx(p, ws), p.permissions, None,
        deny_reason="the user declined this call when asked",
        allowed_names={"read_file"},
    )
    assert res.ok is False
    assert res.error == "permission denied: the user declined this call when asked"
    assert not (ws / "c.md").exists()


# --------------------------------------------------------------------------- #
# 2. The agent runtime (converted from the q6 audit repros)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_read_only_definition_cannot_be_made_to_write(tmp_path):
    """A user-authored agent with ``tools=["read_file"]`` whose model names
    ``write_file``: nothing is written, the refusal is on the ledger, and the
    model was told in plain words."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    read_only = AgentDefinition(
        type=AgentType.BUILDER, system_prompt="read only", tools=["read_file"]
    )
    _script(p, [_call("write_file", {"path": "PLANTED.md", "content": "planted"}), _final("done")])
    s = await orch.create_session("look over the notes", AgentType.BUILDER)
    await orch.run_session(s.id, read_only)
    assert not (Path(s.workspace_path) / "PLANTED.md").exists()
    wf = [t for t in orch.transcript(s.id)["tools"] if t["tool"] == "write_file"]
    assert len(wf) == 1 and wf[0]["ok"] is False
    assert f"`write_file` {NOT_ARMED_WORDING}" in wf[0]["output"]


@pytest.mark.asyncio
async def test_the_builtin_reviewer_cannot_rename(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    reviewer = get_agent_definition(AgentType.REVIEWER)
    assert "rename_file" not in reviewer.tools and "write_file" not in reviewer.tools
    s = await orch.create_session("review the draft", AgentType.REVIEWER)
    (Path(s.workspace_path) / "draft.md").write_text("v1", encoding="utf-8")
    _script(p, [_call("rename_file", {"path": "draft.md", "new_path": "final.md"}), _final("done")])
    await orch.run_session(s.id)
    assert (Path(s.workspace_path) / "draft.md").exists()
    assert not (Path(s.workspace_path) / "final.md").exists()
    rn = [t for t in orch.transcript(s.id)["tools"] if t["tool"] == "rename_file"]
    assert len(rn) == 1 and rn[0]["ok"] is False
    assert NOT_ARMED_WORDING in rn[0]["output"]


@pytest.mark.asyncio
async def test_an_armed_tool_on_the_roster_still_runs(tmp_path):
    """The gate is exact: the BUILDER roster carries write_file, so the same
    call on a BUILDER runs and writes (the regrade_rt_1b shape)."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    _script(p, [_call("write_file", {"path": "NOTES.md", "content": "x"}), _final("ok")])
    s = await orch.create_session("write notes", AgentType.BUILDER)
    await orch.run_session(s.id)
    assert (Path(s.workspace_path) / "NOTES.md").exists()
    wf = [t for t in orch.transcript(s.id)["tools"] if t["tool"] == "write_file"]
    assert len(wf) == 1 and wf[0]["ok"] is True


@pytest.mark.asyncio
async def test_an_unarmed_ask_tier_call_never_pauses_for_the_user(tmp_path):
    """An interactive-origin run whose model names an UNARMED ask-tier tool
    (``shell``) must not card the user for a call that cannot run — the
    registry refuses it as not armed, without an ``approval.requested``."""
    p = build_platform(str(tmp_path / "home"))
    published: list[str] = []
    real_publish = p.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append(str(getattr(type, "value", type)))
        return await real_publish(type, payload, session_id=session_id, **kw)

    p.event_bus.publish = spy
    orch = Orchestrator(p)
    read_only = AgentDefinition(
        type=AgentType.BUILDER, system_prompt="read only", tools=["read_file"]
    )
    _script(p, [_call("shell", {"command": "echo hi"}), _final("done")])
    s = await orch.create_session("look", AgentType.BUILDER, origin="chat")
    await orch.run_session(s.id, read_only)
    assert "approval.requested" not in published
    sh = [t for t in orch.transcript(s.id)["tools"] if t["tool"] == "shell"]
    assert len(sh) == 1 and sh[0]["ok"] is False
    assert NOT_ARMED_WORDING in sh[0]["output"]


# --------------------------------------------------------------------------- #
# 3. Both chat lanes (lock-step)
# --------------------------------------------------------------------------- #
def _write_then_done_complete():
    rounds = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class=None, **kw):
        rounds["n"] += 1
        if rounds["n"] == 1:
            resp = LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name="write_file",
                                     arguments={"path": "PLANTED.md", "content": "planted"})],
            )
        else:
            resp = LLMResponse(text="done")
        return RouteResult(resp, "mock", "mock")

    return fake_complete


def _write_then_done_stream():
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None, **kw):
        rounds["n"] += 1
        if rounds["n"] == 1:
            resp = LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name="write_file",
                                     arguments={"path": "PLANTED.md", "content": "planted"})],
            )
        else:
            resp = LLMResponse(text="done")
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    out: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data))
    return out


def _planted(tmp_path) -> list[Path]:
    return [q for q in tmp_path.rglob("PLANTED.md")]


def test_chat_lane_refuses_a_registered_tool_that_was_not_armed(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    monkeypatch.setattr(platform.router, "complete", _write_then_done_complete())
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": ["read_file"],
    })
    assert r.status_code == 200, r.text
    assert _planted(tmp_path) == []
    rows = _ledger(platform.engine, "chat", "write_file")
    assert len(rows) == 1 and rows[0].ok is False
    assert NOT_ARMED_WORDING in rows[0].output
    assert "write_file" not in r.json().get("tools_used", [])


def test_stream_lane_refuses_a_registered_tool_that_was_not_armed(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    monkeypatch.setattr(platform.router, "stream", _write_then_done_stream())
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": ["read_file"],
    })
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    # No approval card for a call that cannot run — the user's Allow would be
    # a lie — and the finished frame carries the plain wording.
    assert not [d for e, d in frames if e == "approval"]
    finished = next(
        d for e, d in frames
        if e == "tool_call" and d and d.get("status") == "finished"
    )
    assert finished["ok"] is False and NOT_ARMED_WORDING in finished["output"]
    assert _planted(tmp_path) == []
    rows = _ledger(platform.engine, "chat", "write_file")
    assert len(rows) == 1 and rows[0].ok is False
    done = next(d for e, d in frames if e == "done")
    assert "write_file" not in done.get("tools_used", [])


def test_both_chat_lanes_pass_their_armed_set_lock_step(tmp_path, monkeypatch):
    """Mutation-proof for the lock-step rule: the ONE seam only protects a
    lane that hands it the roster. Spy the kwarg on both routes."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    seen: list[set] = []
    real_invoke = platform.registry.invoke

    async def spy_invoke(*a, **kw):
        seen.append(kw.get("allowed_names"))
        return await real_invoke(*a, **kw)

    monkeypatch.setattr(platform.registry, "invoke", spy_invoke)
    monkeypatch.setattr(platform.router, "complete", _write_then_done_complete())
    monkeypatch.setattr(platform.router, "stream", _write_then_done_stream())

    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "go"}], "tools": ["read_file"],
    })
    assert r.status_code == 200, r.text
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "go"}], "tools": ["read_file"],
    })
    assert r.status_code == 200, r.text
    assert len(seen) == 2, seen
    assert seen[0] == {"read_file"}, seen   # chat_turn (blocking lane)
    assert seen[1] == {"read_file"}, seen   # routes/chat (stream mirror)
