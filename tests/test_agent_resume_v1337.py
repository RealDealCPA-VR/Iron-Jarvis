"""v1.337.0 — Continue after a restart resumes the run's own conversation.

Part 3 of the pi-durable borrow. Model messages were never stored, so
Continue on a job a restart cut off started a NEW run from a one-paragraph
recap and redid (or guessed at) everything the first run had seen. Now the
flat perceive→act loop keeps its conversation (``agents/transcript``) as the
tools start and when their results are in; Continue with ``resume`` names
that run (``options.resume_run``); and the new run starts from it — every
call the stop cut off answered with what the ledger says became of it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import transcript as tr
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    AgentType,
    PermissionMode,
    RunTranscript,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools import registry as registry_mod


class _Recording(MockLLMAdapter):
    seen: list[list[LLMMessage]] = []

    async def complete(self, *, system, messages, tools, **kw):
        _Recording.seen.append(list(messages))
        return await super().complete(system=system, messages=messages, tools=tools, **kw)


def _script(p, responses):
    _Recording.seen = []
    p.providers.register("mock", lambda model=None: _Recording(script=list(responses)))


def _call(name, args, cid="c1"):
    return LLMResponse(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_use")


def _final(text):
    return LLMResponse(text=text, finish_reason="stop")


DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["read_file", "write_file"])


def _transcript_of(engine, run_id):
    with session_scope(engine) as db:
        row = db.get(RunTranscript, run_id)
        return json.loads(row.messages_json) if row else None


@pytest.mark.asyncio
async def test_a_flat_run_keeps_its_conversation(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("read a.txt and tell me what it says", AgentType.BUILDER)
    (tmp_path / "home").mkdir(exist_ok=True)
    from pathlib import Path

    Path(s.workspace_path, "a.txt").write_text("hello from a", encoding="utf-8")
    _script(p, [_call("read_file", {"path": "a.txt"}), _final("It says hello.")])
    row = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=30)
    assert row.status is SessionStatus.COMPLETED
    with session_scope(p.engine) as db:
        run = db.exec(select(AgentRun).where(AgentRun.session_id == s.id)).first()
    kept = _transcript_of(p.engine, run.id)
    assert [m["role"] for m in kept] == ["user", "assistant", "tool"]
    assert kept[0]["content"].startswith("read a.txt")
    assert kept[1]["tool_calls"] == [{"id": "c1", "name": "read_file", "arguments": {"path": "a.txt"}}]
    assert kept[2]["tool_call_id"] == "c1" and "hello from a" in kept[2]["content"]


def _seed_cut_off_run(p, *, ledger_note: str | None, tool: str = "write_file", options: str = ""):
    """A session whose run was cut off while `write_file` ran: the transcript
    ends on the assistant turn that asked for it (no result)."""
    with session_scope(p.engine) as db:
        sess = Session(task="tidy the notes", status=SessionStatus.FAILED,
                       workspace_path=str(p.config.workspaces_dir / "x"), options_json=options)
        db.add(sess)
        db.flush()
        run = AgentRun(session_id=sess.id)
        db.add(run)
        db.flush()
        msgs = [
            {"role": "user", "content": "tidy the notes"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "r1", "name": "read_file", "arguments": {"path": "n.txt"}}]},
            {"role": "tool", "tool_call_id": "r1", "name": "read_file", "content": "old notes"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "w1", "name": tool, "arguments": {"path": "n.txt", "content": "new"}}]},
        ]
        db.add(RunTranscript(run_id=run.id, session_id=sess.id, messages_json=json.dumps(msgs)))
        if ledger_note:
            db.add(ToolInvocation(session_id=sess.id, agent_run_id=run.id, tool=tool,
                                  verdict=PermissionMode.ALLOW, ok=False, output=ledger_note))
        db.commit()
        return sess.id, run.id


def test_a_cut_off_call_is_answered_with_what_the_ledger_says(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _sid, run_id = _seed_cut_off_run(p, ledger_note=registry_mod.INTERRUPTED_MAY_HAVE_LANDED)
    msgs = tr.load_for_resume(p.engine, run_id, p.registry)
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant", "tool"]
    assert msgs[-1].tool_call_id == "w1" and msgs[-1].name == "write_file"
    assert msgs[-1].content == registry_mod.INTERRUPTED_MAY_HAVE_LANDED
    # A completed round is left exactly as it was.
    assert msgs[2].content == "old notes"


def test_the_ledgers_word_wins_over_a_guess(tmp_path):
    """A tool this registry does not know is guessed "may have landed"; the
    reconcile's row (written from the tool's own replay flag) wins."""
    p = build_platform(str(tmp_path / "home"))
    _sid, run_id = _seed_cut_off_run(p, ledger_note=registry_mod.INTERRUPTED_SAFE, tool="custom:lookup")
    msgs = tr.load_for_resume(p.engine, run_id, p.registry)
    assert msgs[-1].content == registry_mod.INTERRUPTED_SAFE


def test_with_no_ledger_row_the_tools_own_kind_decides(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _sid, run_id = _seed_cut_off_run(p, ledger_note=None)
    msgs = tr.load_for_resume(p.engine, run_id, p.registry)
    # write_file is not a read: never "safe to run again".
    assert msgs[-1].content == registry_mod.INTERRUPTED_MAY_HAVE_LANDED


@pytest.mark.asyncio
async def test_continue_with_resume_starts_from_the_conversation(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    sid, run_id = _seed_cut_off_run(p, ledger_note=registry_mod.INTERRUPTED_MAY_HAVE_LANDED)
    new = await orch.continue_session(sid, "Continue where you left off", resume=True)
    opts = json.loads(new.options_json or "{}")
    assert opts.get("resume_run") == run_id
    _script(p, [_final("Checked: the notes were already rewritten; nothing to redo.")])
    row = await asyncio.wait_for(orch.run_session(new.id, DEFN), timeout=30)
    assert row.status is SessionStatus.COMPLETED
    first = _Recording.seen[0]
    roles = [m.role for m in first]
    assert roles[:5] == ["user", "assistant", "tool", "assistant", "tool"]
    assert first[0].content == "tidy the notes"  # the ORIGINAL task leads
    assert first[4].content == registry_mod.INTERRUPTED_MAY_HAVE_LANDED
    assert first[-1].role == "user" and first[-1].content.startswith(tr.RESUME_NOTE)
    assert "Continue where you left off" in first[-1].content


@pytest.mark.asyncio
async def test_an_ordinary_continue_never_resumes(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    # Even when the parent itself was a resume (its options carry the link).
    sid, _run_id = _seed_cut_off_run(p, ledger_note=None, options=json.dumps({"resume_run": "run_old"}))
    new = await orch.continue_session(sid, "now also sort them")
    assert "resume_run" not in json.loads(new.options_json or "{}")
    _script(p, [_final("ok")])
    await asyncio.wait_for(orch.run_session(new.id, DEFN), timeout=30)
    first = _Recording.seen[0]
    assert len(first) == 1 and first[0].role == "user"


def test_the_continue_route_passes_resume_through(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    p = app.state.platform
    sid, run_id = _seed_cut_off_run(p, ledger_note=None)
    r = client.post(f"/sessions/{sid}/continue", json={"message": "carry on", "wait": False, "resume": True})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    with session_scope(p.engine) as db:
        row = db.get(Session, new_id)
        assert json.loads(row.options_json or "{}").get("resume_run") == run_id
