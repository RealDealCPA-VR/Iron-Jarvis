"""v1.367.0 (IJ harness Phase 4): PLAN MODE and ASK THE USER in a Build
session — Claude Code's EnterPlanMode / ExitPlanMode / AskUserQuestion and
Codex's request_user_input, served by Jarvis's real tools.

* Plan mode arms ONLY the read-only tools + plan_update (+ exit_plan_mode in
  the stream lane); the tree says "plan only: propose, do not change files".
* exit_plan_mode cards ONCE-ONLY in every posture (auto-approve included):
  Allow once / Deny, no "Always", a wider answer is read as once, and no
  grant covers it. Allow switches the session to act for the NEXT turn and
  keeps the plan; Deny keeps plan mode.
* ask_user waits — no clock — on the turn's side channel for
  ``POST /build/sessions/{id}/answer``; Stop ends the wait cleanly; the POST
  lane (and a client that draws no cards) never arms it.

Driven through the REAL app factory and lanes; only the model is scripted.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.grants import StandingGrantRecord
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon import mcp_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import mode as M
from iron_jarvis.harness import session_tools as T
from iron_jarvis.harness import sessions as S
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
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
    turn_log,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post

PLAN_TOOLS_CLAUDE = [
    "Read", "Glob", "Grep", "LS", "BashOutput", "TodoWrite", "WebFetch", "WebSearch",
    "AskUserQuestion", "ExitPlanMode",
]
EDITORS = {"Write", "Edit", "MultiEdit", "Bash", "TaskStop", "NotebookEdit", "apply_patch", "process_write"}


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _ledger(app, tool: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


async def _start(app, sid: str, text: str, turn_id: str):
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": text, "turn_id": turn_id}, drop=drop))
    return drop, task


async def _finish(app, turn_id: str, drop, task):
    lg = await until(lambda: turn_log(app, turn_id))
    await until(lambda: lg.status != "running", 30)
    drop.set()
    await task
    return lg


# --------------------------------------------------------------------------- #
# plan mode
# --------------------------------------------------------------------------- #


async def test_plan_mode_arms_only_read_tools_and_says_so(app, work):
    sid = new_session(app, work, mode="plan")["id"]
    script = wire(app, Script([
        [ToolCall(id="w", name="Write", arguments={"file_path": "evil.py", "content": "x"})],
        "Here is my plan.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "plan the change", "turn_id": "bp-1"})
    frames = frames_of(raw)
    first = script.calls[0]
    assert first["tools"] == PLAN_TOOLS_CLAUDE
    assert not (set(first["tools"]) & EDITORS)
    assert "## Mode\nPlan only: propose, do not change files." in first["system"]
    assert "call ExitPlanMode" in first["system"]
    done = dict(frames)["done"]
    assert done["route"]["harness"]["mode"] == "plan"
    # A writer the model invents anyway is refused by the registry's gate.
    assert not (work / "evil.py").exists()
    refused = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert refused and refused[0]["ok"] is False
    # Act mode (the default) renders no mode text and arms the editors.
    await asgi(app, "PATCH", f"/build/sessions/{sid}", {"mode": "act"})
    script = wire(app, Script(["ok"]))
    await open_turn(app, sid, {"text": "go", "turn_id": "bp-2"})
    assert "## Mode" not in script.calls[0]["system"] and "Write" in script.calls[0]["tools"]


def test_the_plan_mode_set_changes_nothing_on_this_pc(tmp_path):
    """Every PLAN_MODE_TOOLS member is READONLY or only LOOKS outside (the
    web, a vision model) — never an editor, a command or an app tool."""
    from iron_jarvis.tools.base import Reversibility

    platform = build_platform(str(tmp_path / "home"))
    looks_outside = {"web_fetch", "web_search", "view_image"}
    for name in M.PLAN_MODE_TOOLS:
        tool = platform.registry.get(name)
        assert tool is not None, name
        assert tool.reversibility is Reversibility.READONLY or name in looks_outside, name
    assert not set(M.PLAN_MODE_TOOLS) & {
        "write_file", "edit_file", "multi_edit", "apply_patch", "notebook_edit", "shell",
        "process_start", "process_write", "process_stop",
    }


async def test_exit_plan_mode_cards_once_only_even_on_auto_approve_and_allow_switches_to_act(app, work):
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    plan_text = "My plan:\n1. Change x in app.py\n2. Run the tests"
    wire(app, Script([(plan_text, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Ready."]))
    drop, task = await _start(app, sid, "plan it", "bp-exit")
    card = (await until(lambda: [d for e, d in turn_frames(app, "bp-exit") if e == "approval"]))[0]
    assert card["tool"] == "exit_plan_mode" and card["once_only"] is True and card["can_always"] is False
    # Still plan mode while the card waits.
    assert setup_of(app, sid)["mode"] == "plan"
    # A WIDER answer is read as once: nothing is granted beyond this call.
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "always"}))[0] == 200
    await _finish(app, "bp-exit", drop, task)
    resolved = [d for e, d in turn_frames(app, "bp-exit") if e == "approval_resolved"]
    assert resolved[0]["decision"] == "once"
    with session_scope(app.state.platform.engine) as db:
        assert list(db.exec(select(StandingGrantRecord))) == []
    setup = setup_of(app, sid)
    assert "mode" not in setup, "Allow switches the session to act"
    assert setup["plan"]["text"] == plan_text
    assert [s["step"] for s in setup["plan"]["steps"]] == ["Change x in app.py", "Run the tests"]
    assert setup["plan"]["approved_at"]
    row = _ledger(app, "exit_plan_mode")
    assert row and row[0].ok and row[0].session_id == f"build:{sid}" and row[0].called_as == "ExitPlanMode"
    assert T.PLAN_APPROVED in row[0].output
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["mode"] == "act" and view["plan"]["text"] == plan_text
    # The approved plan is part of the conversation (the lane's reply is only
    # the last round's "Ready."), so the transcript shows it...
    reply = [m for m in messages_of(app, sid) if m["role"] == "assistant"][-1]
    assert reply["content"] == plan_text + "\n\nReady."
    # ...and the NEXT turn — act mode — reads it in its history.
    script = wire(app, Script(["now acting"]))
    await open_turn(app, sid, {"text": "do it", "turn_id": "bp-next"})
    assert "Write" in script.calls[0]["tools"] and "ExitPlanMode" not in script.calls[0]["tools"]
    assert any(plan_text in str(m.content) for m in script.calls[0]["messages"] if m.role == "assistant")


async def test_deny_keeps_plan_mode(app, work):
    sid = new_session(app, work, mode="plan")["id"]
    wire(app, Script([("Plan: 1. a", [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Revising."]))
    drop, task = await _start(app, sid, "plan it", "bp-deny")
    card = (await until(lambda: [d for e, d in turn_frames(app, "bp-deny") if e == "approval"]))[0]
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "bp-deny", drop, task)
    setup = setup_of(app, sid)
    assert setup["mode"] == "plan" and "plan" not in setup
    assert _ledger(app, "exit_plan_mode")[0].ok is False


async def test_no_grant_covers_the_plan_door(app, work):
    sid = new_session(app, work, mode="plan")["id"]
    S.update(app.state.platform.engine, sid, setup={"granted_tools": ["exit_plan_mode"]})
    wire(app, Script([("Plan.", [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "ok"]))
    drop, task = await _start(app, sid, "plan", "bp-grant")
    card = await until(lambda: [d for e, d in turn_frames(app, "bp-grant") if e == "approval"])
    assert card and card[0]["once_only"] is True
    await _asgi_post(app, f"/chat/approvals/{card[0]['id']}", {"decision": "deny"})
    await _finish(app, "bp-grant", drop, task)


async def test_the_door_stays_once_only_even_when_the_user_set_it_to_allow(app, work, monkeypatch):
    """A permission override to "allow" (Settings) never turns the plan door
    into a call that runs unasked: the stream lane still cards it once-only,
    and the POST lane — which can ask nobody — still never arms it."""
    monkeypatch.setitem(app.state.platform.permissions._base, "exit_plan_mode", "allow")
    sid = new_session(app, work, mode="plan")["id"]
    wire(app, Script([("Plan.", [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "ok"]))
    drop, task = await _start(app, sid, "plan", "bp-allow")
    card = (await until(lambda: [d for e, d in turn_frames(app, "bp-allow") if e == "approval"]))[0]
    assert card["tool"] == "exit_plan_mode" and card["once_only"] is True
    await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"})
    await _finish(app, "bp-allow", drop, task)
    assert setup_of(app, sid)["mode"] == "plan"
    # The POST lane.
    platform = app.state.platform
    script = Script([])
    monkeypatch.setattr(platform.router, "complete", script.complete)
    body = ChatBody(messages=[ChatMessageBody(role="user", content="plan")], provider=CLAUDE[0],
                    model=CLAUDE[1], workspace_dir=str(work))
    await run_chat_turn(platform, BUILTIN_PERSONAS, body, harness=True, plan_mode=True)
    assert "ExitPlanMode" not in script.calls[0]["tools"]


async def test_exit_plan_mode_refuses_outside_a_build_session(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                      config=platform.config, event_bus=platform.event_bus, engine=platform.engine)
    res = await T.ExitPlanModeTool().execute({"plan": "x"}, ctx)
    assert res.ok is False and res.error == T.NOT_A_SESSION


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_the_post_lane_has_no_door_and_no_question_in_plan_mode(tmp_path, lane, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    script = Script([])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    body = ChatBody(messages=[ChatMessageBody(role="user", content="plan")], provider=CLAUDE[0],
                    model=CLAUDE[1], workspace_dir=str(ws), mcp_cards=True)
    kw = {"harness": True, "plan_mode": True, "session_id": "build:x"}
    if lane == "stream":
        async for _ in await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw):
            pass
        assert script.calls[0]["tools"] == PLAN_TOOLS_CLAUDE
    else:
        await run_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
        tools = script.calls[0]["tools"]
        assert tools == [t for t in PLAN_TOOLS_CLAUDE if t not in ("AskUserQuestion", "ExitPlanMode")]
        assert "present it in your reply" in script.calls[0]["system"]


def test_each_dialect_names_the_door_and_the_question_its_own_way(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    shown = {}
    for family_model in (("openai", "gpt-5.1"), ("custom", "qwen3-coder"), ("custom", "mystery-7b")):
        turn = M.build(registry=platform.registry, permissions=platform.permissions, requested="auto",
                       provider=family_model[0], model=family_model[1], stream=True,
                       plan_mode=True, ask_user=True, today="2026-10-21")
        shown[turn.plan.dialect.id] = turn.plan.offered_names
    assert "request_user_input" in shown["gpt"] and "exit_plan_mode" in shown["gpt"]
    assert "ask_user" not in shown["gpt"]
    assert "exit_plan_mode" in shown["qwen"] and "ask_user" in shown["qwen"]
    assert "exit_plan_mode" in shown["generic"] and "ask_user" in shown["generic"]
    for names in shown.values():
        assert not {"write_file", "shell", "apply_patch", "edit"} & set(names)


def test_the_auto_approve_sentence_never_promises_the_door_runs_unasked(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    turn = M.build(registry=platform.registry, permissions=platform.permissions, requested="auto",
                   provider=CLAUDE[0], model=CLAUDE[1], stream=True, plan_mode=True,
                   approval_mode="yolo", today="2026-10-21")
    assert "ExitPlanMode always waits for the user's approval." in turn.rendered
    assert "so ExitPlanMode run without pausing" not in turn.rendered


# --------------------------------------------------------------------------- #
# ask the user
# --------------------------------------------------------------------------- #

_QUESTION = {
    "questions": [{
        "question": "Which database?", "header": "DB",
        "options": [{"label": "sqlite", "description": "local file"},
                    {"label": "postgres", "description": "a server"}],
        "multiSelect": False,
    }],
}


async def test_ask_user_waits_for_the_answer_with_no_clock(app, work):
    sid = new_session(app, work)["id"]
    app.state.platform.config.tool_call_timeout_s = 1  # the per-call deadline must not apply
    script = wire(app, Script([[ToolCall(id="q1", name="AskUserQuestion", arguments=_QUESTION)], "Using sqlite."]))
    drop, task = await _start(app, sid, "set up storage", "ba-1")
    ask = (await until(lambda: [d for e, d in turn_frames(app, "ba-1") if e == "ask_user"]))[0]
    assert ask["call_id"] == "q1"
    assert ask["questions"] == [{
        "id": "q1", "question": "Which database?", "header": "DB",
        "options": [{"label": "sqlite", "description": "local file"},
                    {"label": "postgres", "description": "a server"}],
        "multi": False,
    }]
    # The session reads "waiting" while the question is open.
    status, grouped = await asgi(app, "GET", "/build/sessions")
    assert grouped["folders"][0]["sessions"][0]["waiting"] is True
    await asyncio.sleep(1.5)  # past the tool deadline: still waiting
    assert turn_log(app, "ba-1").status == "running"
    # Another session cannot answer it; a bad answer is refused in words.
    other = new_session(app, work)["id"]
    assert (await _asgi_post(app, f"/build/sessions/{other}/answer", {"ask_id": ask["id"], "answer": "x"}))[0] == 404
    status, body = await _asgi_post(app, f"/build/sessions/{sid}/answer", {"ask_id": ask["id"], "answer": {"a": 1}})
    assert status == 422
    status, body = await _asgi_post(app, f"/build/sessions/{sid}/answer", {"ask_id": ask["id"], "answer": "sqlite"})
    assert status == 200 and body == {"ok": True, "skipped": False}
    await _finish(app, "ba-1", drop, task)
    frames = turn_frames(app, "ba-1")
    assert {"id": ask["id"], "kind": "ask_user", "outcome": "answered"} in [d for e, d in frames if e == "mcp_resolved"]
    tool_msgs = [m for m in script.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs[0].content == "The user answered: sqlite" and tool_msgs[0].name == "AskUserQuestion"
    row = _ledger(app, "ask_user")[0]
    assert row.ok and row.session_id == f"build:{sid}" and row.called_as == "AskUserQuestion"
    # A settled question is gone.
    assert (await _asgi_post(app, f"/build/sessions/{sid}/answer", {"ask_id": ask["id"], "answer": "x"}))[0] == 404
    assert messages_of(app, sid)[-1]["content"] == "Using sqlite."


async def test_a_skipped_question_says_so(app, work):
    sid = new_session(app, work)["id"]
    script = wire(app, Script([[ToolCall(id="q1", name="AskUserQuestion", arguments=_QUESTION)], "I chose sqlite."]))
    drop, task = await _start(app, sid, "go", "ba-skip")
    ask = (await until(lambda: [d for e, d in turn_frames(app, "ba-skip") if e == "ask_user"]))[0]
    assert (await _asgi_post(app, f"/build/sessions/{sid}/answer", {"ask_id": ask["id"], "skip": True}))[1]["skipped"]
    await _finish(app, "ba-skip", drop, task)
    tool_msgs = [m for m in script.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs[0].content == T.SKIPPED


async def test_stop_ends_the_wait_cleanly(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="q1", name="AskUserQuestion", arguments=_QUESTION)], "never"]))
    drop, task = await _start(app, sid, "go", "ba-stop")
    ask = (await until(lambda: [d for e, d in turn_frames(app, "ba-stop") if e == "ask_user"]))[0]
    assert mcp_turn.find(ask["id"]) is not None
    assert (await _asgi_post(app, "/chat/turns/ba-stop/stop"))[0] == 200
    lg = await _finish(app, "ba-stop", drop, task)
    assert lg.status == "stopped"
    assert mcp_turn.find(ask["id"]) is None, "a stopped wait leaves nothing parked"
    row = _ledger(app, "ask_user")[0]
    assert row.ok is False and T.STOPPED_BEFORE_ANSWER in (row.output or "")
    assert (await _asgi_post(app, f"/build/sessions/{sid}/answer", {"ask_id": ask["id"], "answer": "x"}))[0] == 404


async def test_codexs_request_user_input_reaches_the_same_question(app, work):
    sid = new_session(app, work, provider="openai", model="gpt-5.1")["id"]
    args = {"questions": [{"id": "db_choice", "header": "DB", "question": "Which one?",
                           "options": [{"label": "a", "description": "x"}, {"label": "b", "description": "y"}]}]}
    script = wire(app, Script([[ToolCall(id="q1", name="request_user_input", arguments=args)], "ok"],
                              who=("openai", "gpt-5.1")))
    drop, task = await _start(app, sid, "go", "ba-gpt")
    ask = (await until(lambda: [d for e, d in turn_frames(app, "ba-gpt") if e == "ask_user"]))[0]
    assert ask["questions"][0]["id"] == "db_choice"
    await _asgi_post(app, f"/build/sessions/{sid}/answer", {"ask_id": ask["id"], "answer": {"db_choice": "b"}})
    await _finish(app, "ba-gpt", drop, task)
    assert "request_user_input" in script.calls[0]["tools"]
    tool_msgs = [m for m in script.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs[0].content == "The user answered: b"


@pytest.mark.parametrize("lane", ["stream-no-cards", "post"])
async def test_ask_user_is_not_armed_where_nobody_can_see_it(tmp_path, lane, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    script = Script([])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    body = ChatBody(messages=[ChatMessageBody(role="user", content="hi")], provider=CLAUDE[0],
                    model=CLAUDE[1], workspace_dir=str(ws), mcp_cards=(lane == "post"))
    if lane == "post":
        await run_chat_turn(platform, BUILTIN_PERSONAS, body, harness=True)
    else:
        async for _ in await stream_chat_turn(platform, BUILTIN_PERSONAS, body, harness=True):
            pass
    assert "AskUserQuestion" not in script.calls[0]["tools"]


async def test_the_tool_with_nobody_to_ask_refuses_at_once(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="x",
                      config=platform.config, event_bus=platform.event_bus, engine=platform.engine)
    res = await T.AskUserTool().execute({"question": "ok?"}, ctx)
    assert res.ok is False and res.error == T.NOBODY_TO_ASK
    res = await T.AskUserTool().execute({}, ctx)
    assert res.ok is False and "question" in res.error


def test_questions_are_parsed_and_answers_worded():
    qs = T.parse_questions({"question": "Pick", "options": ["a", {"label": "b", "description": "bee"}], "multi": True})
    assert qs == [{"id": "q1", "question": "Pick", "header": "", "multi": True,
                   "options": [{"label": "a", "description": ""}, {"label": "b", "description": "bee"}]}]
    with pytest.raises(ValueError):
        T.parse_questions({"questions": [{"question": f"q{i}"} for i in range(5)]})
    two = T.parse_questions({"questions": [{"question": "One?"}, {"question": "Two?", "id": "t"}]})
    assert [q["id"] for q in two] == ["q1", "t"]
    assert T.answer_text(two, {"q1": "yes", "t": ["x", "y"]}) == "The user answered:\n- One? yes\n- Two? x, y"
    assert T.answer_text(two, {"q1": "yes"}) == "The user answered:\n- One? yes\n- Two? (no answer)"
    assert T.plan_steps("intro\n- one\n2) two\n- [ ] three\nnot a step") == [
        {"step": "one", "status": "pending"}, {"step": "two", "status": "pending"},
        {"step": "three", "status": "pending"},
    ]


def test_the_dialect_shapes_match_the_vendor_inventories():
    claude = D.CLAUDE.lookup("AskUserQuestion")
    assert claude.translate({"questions": [{"question": "Q", "header": "H", "options": [], "multiSelect": True}]},
                            frozenset({"ask_user"})) == (
        "ask_user", {"questions": [{"header": "H", "question": "Q", "options": [], "multi": True}]}
    )
    exit_ = D.CLAUDE.lookup("ExitPlanMode")
    assert exit_.input_schema["properties"] == {} and exit_.translate({}, frozenset()) == ("exit_plan_mode", {})
    assert exit_.translate({"plan": "p"}, frozenset()) == ("exit_plan_mode", {"plan": "p"})
    gpt = D.GPT.lookup("request_user_input_async")
    assert gpt.name == "request_user_input"
    real, args = gpt.translate({"questions": [{"title": "T", "options": ["a"]}]}, frozenset())
    assert real == "ask_user" and args == {"questions": [{"question": "T", "options": ["a"]}]}
