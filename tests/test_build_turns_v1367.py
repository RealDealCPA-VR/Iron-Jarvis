"""v1.367.0 (IJ harness Phase 4): a BUILD TURN.

``POST /build/sessions/{id}/turn`` runs the stream lane in HARNESS mode with
the session's folder, its round budget (``build_round_budget``, 60) and
``stay_in_chat``, as a NAMED, DAEMON-OWNED turn, every tool call under the
session's OWN ``build:<id>``. Driven through the REAL app factory; only the
model (the router) is a scripted double.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core import procs
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, ChatTurnRecord, Session, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import process as P
from iron_jarvis.harness import sessions as S
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolContext
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    asgi,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    turn_frames,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post


class Gated:
    """A move that streams ``head``, waits for ``gate``, then ``tail``."""

    def __init__(self, gate: asyncio.Event, head: str, tail: str) -> None:
        self.gate, self.head, self.tail = gate, head, tail


class GatedScript(Script):
    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None,
                     task_class=None, **kw):
        move = await self._next(system, messages, tools)
        if isinstance(move, Gated):
            yield {"type": "text", "text": move.head}
            await move.gate.wait()
            yield {"type": "text", "text": move.tail}
            resp = LLMResponse(text=move.head + move.tail, usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}
            return
        async for frame in super().stream(provider=provider, model=model, system=system,
                                          messages=messages, tools=tools, task_class=task_class, **kw):
            yield frame


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _ledger(app) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all())
        for r in rows:
            db.expunge(r)
        return rows


async def _run(app, sid: str, text: str, turn_id: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] in ("done", "error"), frames[-3:]
    return frames


def _spy_lane(monkeypatch) -> list[dict]:
    seen: list[dict] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append({"body": body, **kw})
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    return seen


# --------------------------------------------------------------------------- #
# the turn: harness mode, the session's folder, its budget, stays in chat
# --------------------------------------------------------------------------- #


async def test_a_build_turn_is_a_harness_turn_on_the_sessions_folder_under_its_own_id(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_lane(monkeypatch)
    script = wire(app, Script([
        [ToolCall(id="c1", name="Write", arguments={"file_path": "hello.py", "content": "print(1)\n"})],
        "Wrote hello.py.",
    ]))
    frames = await _run(app, sid, "make hello.py", "bt-1")
    # The lane was asked for exactly this.
    kw = seen[0]
    assert kw["harness"] == "auto" and kw["round_budget"] == 60 and kw["stay_in_chat"] is True
    assert kw["session_id"] == f"build:{sid}" and kw["plan_mode"] is False
    assert kw["suggest_preferences"] is False
    body = kw["body"]
    assert body.workspace_dir == str(work) and body.thread_id == sid and body.turn_id == "bt-1"
    assert body.mcp_cards is True and body.provider == CLAUDE[0] and body.model == CLAUDE[1]
    # The model met the harness: Claude Code's names, the tree, no exits.
    first = script.calls[0]
    assert "Write" in first["tools"] and "escalate_to_agent" not in first["tools"]
    assert "# IJ harness" in first["system"]
    done = dict(frames)["done"]
    assert done["route"]["harness"]["dialect"] == "claude" and done["escalate"] is False
    # The change landed in the session's folder, ledgered under ITS id.
    assert (work / "hello.py").read_text(encoding="utf-8") == "print(1)\n"
    rows = [r for r in _ledger(app) if r.tool == "write_file"]
    assert [(r.session_id, r.agent_run_id, r.called_as) for r in rows] == [
        (f"build:{sid}", f"build:{sid}", "Write")
    ]
    # The daemon wrote the question and the reply.
    msgs = messages_of(app, sid)
    assert [(m["role"], m["content"], m["turnId"]) for m in msgs] == [
        ("user", "make hello.py", "bt-1"), ("assistant", "Wrote hello.py.", "bt-1"),
    ]
    reply = msgs[1]
    assert reply["toolsUsed"] == ["write_file"] and reply["route"]["harness"]["dialect"] == "claude"
    assert any(p.endswith("hello.py") for p in reply["documents"])
    assert reply["steps"][0]["name"] == "write_file" and reply["steps"][0]["ok"] is True
    assert set(reply["timing"]) == {"startedAt", "firstTokenAt", "endedAt"}
    # Its title is its first question; the usage of this thread counts it.
    assert app.state.platform.turn_logs.get("bt-1").status == "done"
    status, usage = await asgi(app, "GET", f"/chat/threads/{sid}/usage")
    assert status == 200 and usage["turns"] == 1 and usage["input_tokens"] > 0
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["title"] == "make hello.py" and view["running"] is False and view["turn_id"] == ""


async def test_the_next_turn_carries_the_sessions_own_history(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_lane(monkeypatch)
    wire(app, Script(["First answer."]))
    await _run(app, sid, "one", "bt-a")
    await _run(app, sid, "two", "bt-b")
    second = seen[1]["body"].messages
    assert [(m.role, m.content) for m in second] == [
        ("user", "one"), ("assistant", "First answer."), ("user", "two"),
    ]


async def test_the_round_budget_is_the_setting_and_the_turn_ends_in_chat(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_lane(monkeypatch)
    app.state.platform.config.build_round_budget = 2
    read = lambda i: [ToolCall(id=f"r{i}", name="Read", arguments={"file_path": "app.py"})]  # noqa: E731
    script = wire(app, Script([read(1), read(2), read(3), read(4)]))
    frames = await _run(app, sid, "keep reading", "bt-budget")
    assert seen[0]["round_budget"] == 2
    done = dict(frames)["done"]
    assert done["escalate"] is False and "escalate_reason" in done and not done["escalate_reason"]
    with_tools = [c for c in script.calls if c["tools"]]
    assert len(with_tools) == 2, "the budget is the setting's two rounds"
    with session_scope(app.state.platform.engine) as db:
        assert list(db.exec(select(Session))) == [], "a build turn never starts an agent run"


def test_the_setting_is_in_the_schema_with_its_range(tmp_path):
    from iron_jarvis.settings import schema

    d = schema.get("build_round_budget")
    assert d is not None and d.tier == "allow" and d.store == "config"
    client = TestClient(create_app(str(tmp_path / "home")))
    assert client.get("/settings").json()["settings"]["build_round_budget"] == 60
    assert client.put("/settings/values", json={"values": {"build_round_budget": 120}}).status_code == 200
    assert client.app.state.platform.config.build_round_budget == 120
    assert client.put("/settings/values", json={"values": {"build_round_budget": 201}}).status_code >= 400


async def test_a_turn_is_refused_in_words_when_it_cannot_run(app, work, tmp_path):
    sid = new_session(app, work)["id"]
    wire(app, Script([]))
    assert (await open_turn(app, sid, {"text": "  "}))[0] == 400
    assert (await open_turn(app, sid, {"text": "hi", "turn_id": "bad id!"}))[0] == 400
    assert (await open_turn(app, "chat_nope", {"text": "hi"}))[0] == 404
    status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/archive", {})
    assert status == 200
    status, raw = await open_turn(app, sid, {"text": "hi"})
    assert status == 409 and "archived" in raw
    await asgi(app, "POST", f"/build/sessions/{sid}/unarchive")
    # Its folder went away.
    gone = tmp_path / "gone"
    gone.mkdir()
    sid2 = new_session(app, gone)["id"]
    gone.rmdir()
    status, raw = await open_turn(app, sid2, {"text": "hi"})
    assert status == 409 and "folder cannot be used now" in raw and "does not exist" in raw
    assert messages_of(app, sid2) == [], "a refused turn writes no question"


# --------------------------------------------------------------------------- #
# durable: leaving, reloading, a second message, Stop, restart, shutdown
# --------------------------------------------------------------------------- #


async def test_the_turn_survives_its_window_and_is_rejoined(app, work):
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()
    wire(app, GatedScript([Gated(gate, "first half, ", "second half.")]))
    drop = asyncio.Event()
    status, first = await open_turn(app, sid, {"text": "go", "turn_id": "bt-drop"}, drop=drop,
                                    stop_after="first half")
    assert status == 200 and "first half" in first and "event: done" not in first
    lg = app.state.platform.turn_logs.get("bt-drop")
    assert lg is not None and lg.status == "running", "leaving Build must not stop the turn"
    # While it runs, the session reads running and names its turn.
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["running"] is True and view["turn_id"] == "bt-drop"
    status, grouped = await asgi(app, "GET", "/build/sessions")
    assert grouped["folders"][0]["sessions"][0]["running"] is True
    gate.set()
    await until(lambda: lg.status != "running")
    assert lg.status == "done"
    # Rejoin from anywhere: the WHOLE turn, ending in done.
    status, text = await asgi(app, "GET", "/chat/turns/bt-drop/stream")
    frames = frames_of(text)
    assert "".join(d.get("text", "") for e, d in frames if e == "token") == "first half, second half."
    assert frames[-1][0] == "done"
    rec = await until(lambda: _record(app, "bt-drop"))
    assert rec.thread_id == sid
    assert messages_of(app, sid)[-1]["content"] == "first half, second half."


def _record(app, tid):
    with session_scope(app.state.platform.engine) as db:
        row = db.get(ChatTurnRecord, tid)
        if row is not None:
            db.expunge(row)
        return row


async def test_a_second_message_is_refused_and_the_same_turn_id_is_followed(app, work):
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()
    script = wire(app, GatedScript([Gated(gate, "working ", "done.")]))
    drop = asyncio.Event()
    await open_turn(app, sid, {"text": "go", "turn_id": "bt-one"}, drop=drop, stop_after="working")
    status, raw = await open_turn(app, sid, {"text": "and another", "turn_id": "bt-two"})
    assert status == 409 and json.loads(raw)["turn_id"] == "bt-one"
    status, raw = await open_turn(app, sid, {"text": "and another"})
    assert status == 409
    follow = asyncio.create_task(open_turn(app, sid, {"text": "go", "turn_id": "bt-one"}))
    await asyncio.sleep(0.1)
    gate.set()
    status, raw = await follow
    assert status == 200 and "event: done" in raw
    assert len([c for c in script.calls if c["tools"]]) == 1, "the same turn id never runs twice"
    assert [m["content"] for m in messages_of(app, sid)] == ["go", "working done."]


async def test_a_running_session_is_not_deleted_or_archived_without_stop(app, work):
    """The chat archive's rules, shared: a session with a reply in progress
    answers 409 and lists it; ``stop`` stops the turn and archives."""
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()
    wire(app, GatedScript([Gated(gate, "working ", "never.")]))
    drop = asyncio.Event()
    await open_turn(app, sid, {"text": "go", "turn_id": "bt-busy"}, drop=drop, stop_after="working")
    status, body = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 409 and "still answering" in body["detail"]
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/archive", {})
    assert status == 409 and body["running"] == ["a reply in progress"]
    assert "This session is still working" in body["detail"]
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/archive", {"stop": True})
    assert status == 200 and body["archived"] is True and body["stopped"] == ["a reply in progress"]
    gate.set()
    lg = app.state.platform.turn_logs.get("bt-busy")
    await until(lambda: lg.status != "running")
    assert lg.status == "stopped"


async def test_stop_ends_it_and_the_partial_is_kept_marked(app, work):
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()
    wire(app, GatedScript([Gated(gate, "half an ans", "wer.")]))
    drop = asyncio.Event()
    await open_turn(app, sid, {"text": "go", "turn_id": "bt-stop"}, drop=drop, stop_after="half an ans")
    assert (await _asgi_post(app, "/chat/turns/bt-stop/stop"))[0] == 200
    gate.set()
    lg = app.state.platform.turn_logs.get("bt-stop")
    await until(lambda: lg.status != "running")
    assert lg.status == "stopped"
    reply = await until(lambda: [m for m in messages_of(app, sid) if m["role"] == "assistant"])
    assert reply[-1]["interrupted"] is True and reply[-1]["content"].startswith("half an ans")


async def test_a_steer_note_lands_in_the_session_as_the_users_line(app, work):
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()

    async def wait_then_read(_script):
        await gate.wait()
        return [ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"})]

    wire(app, Script([wait_then_read, "Answered with the note."]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "go", "turn_id": "bt-steer"}, drop=drop))
    await until(lambda: getattr(app.state.platform, "turn_logs", None) and app.state.platform.turn_logs.get("bt-steer"))
    assert (await _asgi_post(app, "/chat/turns/bt-steer/steer", {"text": "keep it short"}))[0] == 200
    gate.set()
    lg = app.state.platform.turn_logs.get("bt-steer")
    await until(lambda: lg.status != "running")
    drop.set()
    await task
    msgs = messages_of(app, sid)
    assert [(m["role"], m["content"], m.get("steer")) for m in msgs] == [
        ("user", "go", None), ("user", "keep it short", True), ("assistant", "Answered with the note.", None),
    ]


def test_a_restart_marks_the_turn_interrupted_and_keeps_what_it_wrote(tmp_path, work):
    home = str(tmp_path / "home")
    p = build_platform(home)
    row = S.create(p.engine, str(work), "", {"provider": CLAUDE[0], "model": CLAUDE[1]})
    S.append_lines(p.engine, row.id, [{"role": "user", "content": "go", "turnId": "bt-crash"}])
    with session_scope(p.engine) as db:
        db.add(ChatTurnRecord(
            id="bt-crash", thread_id=row.id, status="running",
            frames_json=json.dumps([["token", json.dumps({"text": "half an answ"})]]),
        ))
        db.commit()
    p.engine.dispose()
    app = create_app(home)
    with TestClient(app) as client:  # the lifespan runs the boot reconcile
        listed = client.get("/chat/turns", params={"thread_id": row.id}).json()["turns"]
        assert [(t["turn_id"], t["status"]) for t in listed] == [("bt-crash", "interrupted")]
        frames = frames_of(client.get("/chat/turns/bt-crash/stream").text)
        assert frames[-1][0] == "error" and "stopped while answering" in frames[-1][1]["detail"]
        msgs = client.get(f"/build/sessions/{row.id}").json()["messages"]
        assert [(m["role"], m["content"]) for m in msgs] == [("user", "go"), ("assistant", "half an answ")]
        assert msgs[1]["interrupted"] is True and "stopped while answering" in msgs[1]["error"]
    # A second boot does not write it twice.
    with TestClient(create_app(home)) as client:
        assert len(client.get(f"/build/sessions/{row.id}").json()["messages"]) == 2


async def test_a_shutdown_cuts_the_turn_off_and_its_partial_is_kept(app, work):
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()
    wire(app, GatedScript([Gated(gate, "first half, ", "never.")]))
    drop = asyncio.Event()
    await open_turn(app, sid, {"text": "go", "turn_id": "bt-down"}, drop=drop, stop_after="first half")
    await app.state.platform.turn_logs.shutdown()
    rec = _record(app, "bt-down")
    assert rec.status == "interrupted"
    reply = [m for m in messages_of(app, sid) if m["role"] == "assistant"]
    assert reply and reply[-1]["content"] == "first half, " and reply[-1]["interrupted"] is True


# --------------------------------------------------------------------------- #
# a session's programs belong to the session
# --------------------------------------------------------------------------- #

SLEEPER = f'"{sys.executable}" -c "import time; time.sleep(120)"'


async def test_a_program_a_session_starts_is_its_own_and_ends_with_it(app, work, tmp_path):
    a = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([
        [ToolCall(id="b1", name="Bash", arguments={"command": SLEEPER, "run_in_background": True})],
        "Started it.",
    ]))
    frames = await _run(app, a, "start the server", "bt-proc")
    assert dict(frames)["done"]["tools_used"] == ["process_start"]
    mine = P.TABLE.running(f"build:{a}")
    try:
        assert len(mine) == 1 and mine[0].session_id == f"build:{a}"
        assert P.TABLE.running("chat") == [], "a build session's program is not chat's"
        handle = mine[0].handle
        # Another session cannot see or stop it.
        other = new_session(app, work)["id"]
        ctx = ToolContext(
            workspace=work, session_id=f"build:{other}", agent_run_id="x",
            config=app.state.platform.config, event_bus=app.state.platform.event_bus,
            engine=app.state.platform.engine,
        )
        res = await P.ProcessReadTool().execute({"id": handle}, ctx)
        assert res.ok is False
        res = await P.ProcessStopTool().execute({"id": handle}, ctx)
        assert res.ok is False and mine[0].running()
        # Archiving the session ends what it started.
        status, out = await asgi(app, "POST", f"/build/sessions/{a}/archive", {})
        assert status == 200 and out["archived"] is True
        assert await until(lambda: not mine[0].running(), 20)
        assert P.TABLE.running(f"build:{a}") == []
    finally:
        procs.end_session(f"build:{a}")


async def test_deleting_a_session_ends_its_programs_too(app, work):
    a = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([
        [ToolCall(id="b1", name="Bash", arguments={"command": SLEEPER, "run_in_background": True})],
        "Started it.",
    ]))
    await _run(app, a, "start it", "bt-del")
    mine = P.TABLE.running(f"build:{a}")
    try:
        assert len(mine) == 1
        status, _ = await asgi(app, "DELETE", f"/build/sessions/{a}")
        assert status == 200
        assert await until(lambda: not mine[0].running(), 20)
    finally:
        procs.end_session(f"build:{a}")


async def test_a_command_runs_owned_by_the_session(app, work, monkeypatch):
    """``shell`` marks what it starts as the SESSION's (``procs.owned_by``),
    so a build session's leftover windowless children end with it — never
    with "chat"."""
    owners: list[str] = []
    real = procs.owned_by

    def spy(session_id):
        owners.append(str(session_id))
        return real(session_id)

    monkeypatch.setattr(procs, "owned_by", spy)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    frames = await _run(app, sid, "say hi", "bt-owned")
    assert dict(frames)["done"]["tools_used"] == ["shell"]
    assert owners == [f"build:{sid}"]


async def test_the_chat_queue_and_usage_routes_serve_a_session(app, work):
    sid = new_session(app, work)["id"]
    items = [{"id": "q1", "text": "after this, add tests", "files": []}]
    status, body = await asgi(app, "PUT", f"/chat/threads/{sid}/queue", {"items": items})
    assert status == 200 and body["queued"] == 1
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["queued"] == items
    status, usage = await asgi(app, "GET", f"/chat/threads/{sid}/usage")
    assert status == 200 and usage["turns"] == 0


def test_shutdown_ends_every_sessions_programs_through_the_one_hook():
    """``procs.end_all`` (the daemon's shutdown) runs the process tools' end
    hook for EVERY session — build sessions included (no second hook)."""
    assert P.end_session in procs._END_HOOKS
    called: list = []
    procs.add_end_hook(called.append)
    try:
        procs.end_all()
        procs.end_session("build:abc")
    finally:
        procs._END_HOOKS.remove(called.append)
    assert called == [None, "build:abc"]


# --------------------------------------------------------------------------- #
# "allow for this conversation" is kept with the session
# --------------------------------------------------------------------------- #


async def test_a_conversation_grant_is_kept_for_the_next_turns(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_lane(monkeypatch)
    wire(app, Script([
        [ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "bt-grant"}, drop=drop))
    card = await until(lambda: [d for e, d in turn_frames(app, "bt-grant") if e == "approval"])
    assert card[0]["tool"] == "shell" and card[0].get("once_only") is not True
    assert (await _asgi_post(app, f"/chat/approvals/{card[0]['id']}", {"decision": "conversation"}))[0] == 200
    lg = app.state.platform.turn_logs.get("bt-grant")
    await until(lambda: lg.status != "running")
    drop.set()
    await task
    assert setup_of(app, sid)["granted_tools"] == ["shell"]
    wire(app, Script(["nothing to do"]))
    await _run(app, sid, "again", "bt-grant-2")
    assert "shell" in seen[1]["body"].granted_tools


# --------------------------------------------------------------------------- #
# the lanes, lock-step
# --------------------------------------------------------------------------- #


def _lane_body(ws, **kw) -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content="write it")],
        provider=CLAUDE[0], model=CLAUDE[1], workspace_dir=str(ws), approval_mode="yolo", **kw,
    )


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_both_lanes_run_tools_under_the_session_id_they_are_given(tmp_path, work, lane, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    script = Script([[ToolCall(id="w", name="Write", arguments={"file_path": f"{lane}.py", "content": "x\n"})]])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    kw = {"harness": True, "session_id": "build:lane"}
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, _lane_body(work), **kw)
        async for _ in gen:
            pass
    else:
        await run_chat_turn(platform, BUILTIN_PERSONAS, _lane_body(work), **kw)
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == "write_file"]
        assert [(r.session_id, r.agent_run_id) for r in rows] == [("build:lane", "build:lane")]
        run = list(db.exec(select(AgentRun)))
        assert [r.session_id for r in run] == ["chat"], "billing stays the chat lane's (usage per thread)"


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_both_lanes_refuse_a_bad_session_id_or_plan_mode_without_harness(tmp_path, work, lane):
    platform = build_platform(str(tmp_path / "home"))

    async def call(**kw):
        if lane == "stream":
            await stream_chat_turn(platform, BUILTIN_PERSONAS, _lane_body(work), **kw)
        else:
            await run_chat_turn(platform, BUILTIN_PERSONAS, _lane_body(work), **kw)

    for bad in ("other", "build:", "session_x"):
        with pytest.raises(ValueError, match="session_id"):
            await call(harness=True, session_id=bad)
    with pytest.raises(ValueError, match="plan_mode needs harness"):
        await call(plan_mode=True)


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_without_a_session_id_every_lane_is_still_chat(tmp_path, work, lane, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    script = Script([[ToolCall(id="w", name="write_file", arguments={"path": "c.txt", "content": "x"})]])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    body = _lane_body(work, tools=["write_file"])
    if lane == "stream":
        async for _ in await stream_chat_turn(platform, BUILTIN_PERSONAS, body):
            pass
    else:
        await run_chat_turn(platform, BUILTIN_PERSONAS, body)
    with session_scope(platform.engine) as db:
        assert {r.session_id for r in db.exec(select(ToolInvocation))} == {"chat"}


# --------------------------------------------------------------------------- #
# what a build turn changed (the changes pane)
# --------------------------------------------------------------------------- #


async def test_the_changes_route_reads_a_sessions_own_journal(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script([
        [ToolCall(id="c1", name="Write", arguments={"file_path": "made.py", "content": "y = 2\n"})], "Made it.",
    ]))
    since = datetime.now(timezone.utc).isoformat()
    await _run(app, sid, "make made.py", "bt-changes")
    status, mine = await asgi(app, "POST", "/chat/changes", {"since": since, "session_id": f"build:{sid}"})
    assert status == 200 and [c["rel"] for c in mine["changes"]] == ["made.py"] and mine["files"] == 1
    status, chat = await asgi(app, "POST", "/chat/changes", {"since": since})
    assert status == 200 and chat["changes"] == [], "chat's journal is not the session's"
    status, _ = await asgi(app, "POST", "/chat/changes", {"since": since, "session_id": "someone"})
    assert status == 422
