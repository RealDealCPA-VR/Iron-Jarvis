"""v1.411.0 review — twelve defects an independent review of Pi in Build's Code
view found, each pinned here (red on the branch as first built).

Turns replay Pi's recorded opening (``tests/fixtures/build_app_v1411``) and
then a SCRIPTED tail written here, through the fake harness process
(``tests/fixtures/fake_harness_v1409.py``: ``sleep`` steps and ``await`` with
``within``/``otherwise``) — the real process plumbing, parser, routes and
store. Nothing here runs a real Pi.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import process as PR
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask, AskRefused
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, stop_all, until
from tests._build_app_pi_helpers_v1411 import FIX_PI, use_fake_pi

SID_A = "01a1b2c3-aaaa-7222-8333-444444444444"


@pytest.fixture(autouse=True)
def _fast_handled(monkeypatch):
    monkeypatch.setattr(P, "HANDLED_GRACE_S", 0.3)


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


# --------------------------------------------------------------------------- #
# Scripted Pi: the recorded opening, then the steps a test writes
# --------------------------------------------------------------------------- #
def _opening() -> list[dict]:
    steps = json.loads((FIX_PI / "pi_read.json").read_text(encoding="utf-8"))["steps"]
    end = next(i for i, s in enumerate(steps) if s.get("await") == "command" and s.get("command") == "prompt")
    return steps[:end]


def scripted(tmp_path: Path, monkeypatch, steps: list[dict], name: str = "pi") -> Path:
    """Point the Code view's Pi at the fake replaying the opening + ``steps``;
    returns the fake's log."""
    path = tmp_path / f"{name}.json"
    path.write_bytes(json.dumps({"harness": "pi", "steps": _opening() + steps}).encode("utf-8"))
    log = tmp_path / f"{name}.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(path))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    return log


def await_cmd(cmd: str, **kw) -> dict:
    return {"await": "command", "command": cmd, **kw}


def reply(cmd: str, data: dict | None = None) -> dict:
    out = {"type": "response", "command": cmd, "success": True}
    if data is not None:
        out["data"] = data
    return {"reply_to_command": cmd, "out": {"id": "x", **out}}


def ev(obj: dict) -> dict:
    return {"out": obj}


def text_run(words: str, *, aborted: bool = False) -> list[dict]:
    """One assistant message saying ``words``, then the run's end."""
    stop = "aborted" if aborted else "stop"
    return [
        ev({"type": "message_start", "message": {"role": "assistant", "content": []}}),
        ev({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                                "delta": words}}),
        ev({"type": "message_end", "message": {"role": "assistant", "stopReason": stop, "content": [],
                                               "usage": {"input": 3, "output": 2}}}),
        ev({"type": "agent_end", "messages": []}),
        ev({"type": "agent_settled", "aborted": aborted}),
    ]


TOOL_RUNS = [
    ev({"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash", "args": {"command": "echo ran"}}),
    ev({"type": "tool_execution_end", "toolCallId": "c1", "toolName": "bash",
        "result": {"content": [{"type": "text", "text": "ran"}]}, "isError": False}),
]


async def _turn(c, sid: str, text: str = "do it", turn_id: str = "t1"):
    return asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id}))


def _driver(emitted: list, asks: list | None = None, written: list | None = None) -> P.PiDriver:
    async def write(obj):
        if written is not None:
            written.append(obj)

    return P.PiDriver(write=write, emit=emitted.append,
                      ask=(asks.append if asks is not None else (lambda a: None)),
                      on_session_id=lambda s: None, mode="own")


# --------------------------------------------------------------------------- #
# 1. npm's POSIX `pi` shim beside pi.cmd
# --------------------------------------------------------------------------- #
def test_1_the_lookup_takes_pi_cmd_never_the_sh_shim_beside_it(tmp_path, monkeypatch):
    local = tmp_path / "local"
    pi_dir = local / "pi-node" / "current"
    pi_dir.mkdir(parents=True)
    (pi_dir / "pi").write_bytes(b'#!/bin/sh\nexec node "$basedir/pi.js" "$@"\n')
    (pi_dir / "pi.cmd").write_bytes(b"@echo off\r\nnode pi.js %*\r\n")
    (pi_dir / "pi.ps1").write_bytes(b"node pi.js $args\n")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    if os.name != "nt":
        pytest.skip("Windows extensions")
    got = PR.binary_for("pi")
    assert got is not None and Path(got[0]).name.lower() == "pi.cmd", got


async def test_1_a_program_windows_cannot_start_is_said_in_one_plain_sentence(app, work, tmp_path, monkeypatch):
    shim = tmp_path / "pi"
    shim.write_bytes(b'#!/bin/sh\nexec node pi.js "$@"\n')
    monkeypatch.setattr(S, "binary_for", lambda harness: [str(shim)])
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        evs = frames((await asyncio.wait_for(await _turn(c, sid), 30)).text)
    await stop_all(app)
    err = next(e for e in evs if e["type"] == "error")
    if os.name == "nt":
        assert err["title"] == ("Iron Jarvis could not start Pi from pi: that file is not a program Windows can run. "
                                "Check that Pi runs in a Build terminal.")
    assert "OSError" not in json.dumps(evs) and "WinError" not in err["title"]
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "failed"


def test_1_words_for_other_start_failures():
    assert "not there any more" in W.could_not_start("pi", "C:/x/pi.cmd", FileNotFoundError(2, "gone"))
    assert "did not allow" in W.could_not_start("codex", "codex.exe", PermissionError(13, "no"))


# --------------------------------------------------------------------------- #
# 2. A Stop before Pi's run became active
# --------------------------------------------------------------------------- #
async def test_2_a_stop_before_the_run_starts_is_said_again_when_it_starts(app, work, tmp_path, monkeypatch):
    log = scripted(tmp_path, monkeypatch, [
        await_cmd("prompt"),
        # Pi is still in prompt preflight (input handlers, sign-in check):
        # the abort finds no active run and is answered at once.
        await_cmd("abort"),
        reply("abort"),
        reply("prompt", {"disposition": "started"}),
        ev({"type": "agent_start"}),
        await_cmd("abort", within=3, otherwise=[*TOOL_RUNS, *text_run("I went on.")]),
        *text_run("", aborted=True),
        reply("abort"),
    ])
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        task = await _turn(c, sid)
        await until(lambda: any(m.get("type") == "prompt" for m in sent(log)))
        r = await c.post(f"/build/app/sessions/{sid}/interrupt")
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    assert [m["type"] for m in sent(log)].count("abort") == 2, "the lost abort is sent again once the run starts"
    assert not [e for e in evs if e["type"] == "tool"], "nothing ran after the user's Stop"
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "interrupted"


# --------------------------------------------------------------------------- #
# 3. A Stop on an extension command's dialog
# --------------------------------------------------------------------------- #
async def test_3_a_stop_on_a_waiting_extension_command_ends_interrupted(app, work, tmp_path, monkeypatch):
    use_fake_pi(monkeypatch, "pi_input_command", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        task = await _turn(c, sid, "/rpc-input")
        await until(lambda: svc.live.get(sid) and next(iter(svc.live[sid].asks), None))
        r = await c.post(f"/build/app/sessions/{sid}/interrupt")
        assert r.status_code == 200
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "interrupted"


# --------------------------------------------------------------------------- #
# 4. Pi's two session layouts, and a project's own sessionDir
# --------------------------------------------------------------------------- #
def _session_rows(folder: Path, sid: str, first: str) -> bytes:
    rows = [
        {"type": "session", "version": 3, "id": sid, "timestamp": "2026-10-30T13:12:13.190Z", "cwd": str(folder)},
        {"type": "message", "id": "a1", "parentId": None, "timestamp": "2026-10-30T13:12:20.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": first}]}},
    ]
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")


def write_flat(root: Path, folder: Path, sid: str, first: str, stamp: str = "2026-10-30T13-12-13-190Z") -> Path:
    """How Pi saves under a ``sessionDir`` / PI_CODING_AGENT_SESSION_DIR: FLAT."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stamp}_{sid}.jsonl"
    path.write_bytes(_session_rows(folder, sid, first))
    return path


def write_per_folder(root: Path, folder: Path, sid: str, first: str) -> Path:
    """How Pi saves under its default ``~/.pi/agent/sessions``."""
    project = root / ("--" + str(folder).replace(":", "-").replace("\\", "-").replace("/", "-").lstrip("-") + "--")
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"2026-10-30T13-12-13-190Z_{sid}.jsonl"
    path.write_bytes(_session_rows(folder, sid, first))
    return path


def test_4_flat_and_per_folder_layouts_are_both_read(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    write_per_folder(agent / "sessions", work, SID_A, "default layout")
    got = S.disk_sessions("pi", str(agent), str(work))
    assert [(s["id"], s["title"]) for s in got] == [(SID_A, "default layout")]
    flat = tmp_path / "flat"
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(flat))
    b = "01a1b2c3-bbbb-7222-8333-444444444444"
    write_flat(flat, work, b, "flat layout")
    write_flat(flat, tmp_path / "elsewhere", "01a1b2c3-cccc-7222-8333-444444444444", "another folder")
    got = S.disk_sessions("pi", str(agent), str(work))
    assert [(s["id"], s["title"]) for s in got] == [(b, "flat layout")], "a flat file is matched by its header's cwd"


def test_4_a_projects_own_session_dir_is_honoured(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    work = tmp_path / "work"
    (work / ".pi").mkdir(parents=True)
    (work / ".pi" / "settings.json").write_text(json.dumps({"sessionDir": "../proj-sessions"}), encoding="utf-8")
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({"sessionDir": str(tmp_path / "global")}), encoding="utf-8")
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    assert P.sessions_dir(work) == work / ".." / "proj-sessions", "the project's setting wins over the global one"
    assert P.sessions_dir() == tmp_path / "global"
    write_flat(tmp_path / "proj-sessions", work, SID_A, "kept in the project's folder")
    got = S.disk_sessions("pi", str(agent), str(work))
    assert [s["id"] for s in got] == [SID_A]
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "env"))
    assert P.sessions_dir(work) == tmp_path / "env", "the environment comes first, as in Pi's main.js"


# --------------------------------------------------------------------------- #
# 5. "Pi's default" once a conversation exists
# --------------------------------------------------------------------------- #
async def test_5_a_conversation_with_messages_keeps_its_last_model_and_effort(app, work, tmp_path, monkeypatch):
    root = tmp_path / "pi-sessions"
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(root))
    write_flat(root, work, SID_A, "an earlier message")
    async with client(app) as c:
        made = await new_session(c, work, harness="pi", harness_session_id=SID_A)
        sid = made["id"]
        r = await c.patch(f"/build/app/sessions/{sid}", json={"model": "anthropic/claude-x", "effort": "high"})
        assert r.status_code == 200, r.text
        assert r.json()["keeps_last"] is True
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert view["keeps_last"] is True
        for body in ({"model": ""}, {"effort": ""}):
            r = await c.patch(f"/build/app/sessions/{sid}", json=body)
            assert r.status_code == 409 and r.json()["detail"] == W.PI_KEEPS_LAST, body
        s = (await c.get(f"/build/app/sessions/{sid}")).json()["session"]
        assert (s["model"], s["effort"]) == ("anthropic/claude-x", "high"), "nothing was pretended"
        # A Pi session with no conversation yet: Pi's default still applies.
        fresh = await new_session(c, work, harness="pi")
        await c.patch(f"/build/app/sessions/{fresh['id']}", json={"model": "anthropic/claude-x"})
        r = await c.patch(f"/build/app/sessions/{fresh['id']}", json={"model": ""})
        assert r.status_code == 200 and r.json()["keeps_last"] is False


# --------------------------------------------------------------------------- #
# 6. A run no message owns
# --------------------------------------------------------------------------- #
async def test_6_a_run_an_extension_command_starts_inside_the_grace_belongs_to_its_turn(
        app, work, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "HANDLED_GRACE_S", 2.0)
    scripted(tmp_path, monkeypatch, [
        await_cmd("prompt"),
        reply("prompt", {"disposition": "handled"}),
        {"sleep": 0.3},
        ev({"type": "agent_start"}),
        *text_run("from the command"),
    ])
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        evs = frames((await asyncio.wait_for(await _turn(c, sid, "/goal"), 30)).text)
    await stop_all(app)
    said = "".join(e.get("delta", "") for e in evs if e["type"] == "text")
    assert said == "from the command", "the command's run is this message's"
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"


async def test_6_a_handled_answer_mid_run_never_ends_the_turn_under_the_run(app, work, tmp_path, monkeypatch):
    log = scripted(tmp_path, monkeypatch, [
        await_cmd("prompt"),
        ev({"type": "agent_start"}),
        ev({"type": "message_start", "message": {"role": "assistant", "content": []}}),
        ev({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                                "delta": "part one "}}),
        # The extension command answers only once its own run is under way.
        reply("prompt", {"disposition": "handled"}),
        {"sleep": 0.9},
        ev({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                                "delta": "part two"}}),
        ev({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": []}}),
        ev({"type": "agent_end", "messages": []}),
        ev({"type": "agent_settled", "aborted": False}),
    ])
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        evs = frames((await asyncio.wait_for(await _turn(c, sid, "/goal"), 30)).text)
    await stop_all(app)
    assert log.exists()
    said = "".join(e.get("delta", "") for e in evs if e["type"] == "text")
    assert said == "part one part two" and evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"


async def test_6_a_run_nobody_owns_keeps_the_session_busy_and_a_new_message_gets_our_words(
        app, work, tmp_path, monkeypatch):
    log = scripted(tmp_path, monkeypatch, [
        await_cmd("prompt"),
        reply("prompt", {"disposition": "handled"}),
        {"sleep": 0.8},  # past the grace: the turn has ended
        ev({"type": "agent_start"}),
        ev({"type": "message_start", "message": {"role": "assistant", "content": []}}),
        ev({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                                "delta": "extension work"}}),
        {"sleep": 2.0},  # working, no card open: busy by the run alone
        ev({"type": "extension_ui_request", "id": "dlg-orphan", "method": "confirm", "title": "Go on?",
            "message": "The extension asks"}),
        {"await": "ui", "id": "dlg-orphan"},
        ev({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": []}}),
        ev({"type": "agent_end", "messages": []}),
        ev({"type": "agent_settled", "aborted": False}),
        await_cmd("prompt"),
        reply("prompt", {"disposition": "started"}),
        ev({"type": "agent_start"}),
        *text_run("turn three"),
    ])
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        first = frames((await asyncio.wait_for(await _turn(c, sid, "/goal"), 30)).text)
        assert first[-1]["type"] == "done" and first[-1]["stop"] == "completed"
        live = svc.live[sid]
        await until(lambda: live.working_alone)
        assert not live.asks and live.busy, "busy by the run alone, no card open"
        assert await svc.reap_once(now=live.last_active + S.IDLE_S * 10) == [], "never reaped mid-run"
        assert live.running
        rid = await until(lambda: next(iter(live.asks), None))
        r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "next", "turn_id": "t2"})
        assert r.status_code == 409 and r.json()["detail"] == W.PI_STILL_WORKING
        assert [m["type"] for m in sent(log)].count("prompt") == 1, "nothing reached Pi"
        r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": rid, "decision": "allow"})
        assert r.status_code == 200, r.text
        await until(lambda: not live.working_alone)
        third = frames((await asyncio.wait_for(await _turn(c, sid, "go", "t3"), 30)).text)
    await stop_all(app)
    said = "".join(e.get("delta", "") for e in third if e["type"] == "text")
    assert said == "turn three" and [e["type"] for e in third].count("done") == 1
    assert third[-1]["stop"] == "completed"


async def test_6_pis_own_refusal_of_a_message_mid_run_is_said_in_our_words():
    out: list = []
    d = _driver(out)
    await d.send_user("hi", [])
    d.on_message({"id": "ij-prompt-1", "type": "response", "command": "prompt", "success": False,
                  "error": "Agent is already processing. Specify streamingBehavior ('steer' or 'followUp')."})
    await asyncio.sleep(0)  # the prompt's answer is read by its done callback
    err = next(e for e in out if e["type"] == "error")
    assert err["title"] == W.PI_STILL_WORKING_TITLE and err["fix"] == W.PI_STILL_WORKING_FIX
    assert out[-1]["type"] == "done" and out[-1]["stop"] == "failed"


async def test_6_a_settle_of_a_run_the_open_message_does_not_own_ends_nothing():
    """Defence in depth: should Pi ever take a message while a run that is
    not its own goes on, that run's end must not end the message's turn."""
    out: list = []
    d = _driver(out)
    d.on_message({"type": "agent_start"})  # nobody's message
    assert d.busy_alone
    with pytest.raises(P.PiStillWorking):
        await d.send_user("hi", [])
    d._turn_n, d.turn_open, d._waiting_run = 1, True, True  # a message Pi took anyway
    d.on_message({"type": "agent_settled", "aborted": False})
    assert not [e for e in out if e["type"] == "done"], "the open turn goes on"
    assert d.turn_open


# --------------------------------------------------------------------------- #
# 7. A confirm while several tools run (Pi's parallel tool mode)
# --------------------------------------------------------------------------- #
def test_7_a_confirm_is_a_tools_card_only_when_exactly_one_tool_runs():
    asks: list = []
    d = _driver([], asks)
    d.on_message({"type": "tool_execution_start", "toolCallId": "a", "toolName": "bash", "args": {"command": "rm x"}})
    d.on_message({"type": "tool_execution_start", "toolCallId": "b", "toolName": "write",
                  "args": {"path": "y.txt", "content": "y"}})
    d.on_message({"type": "extension_ui_request", "id": "d1", "method": "confirm", "title": "Allow this?",
                  "message": "the gate asks"})
    card = asks[-1].event
    assert card["title"] == "Allow this?" and card["kind"] == "other"
    assert "command" not in card and "diff" not in card and "path" not in card and asks[-1].tool == "extension"
    d.on_message({"type": "tool_execution_end", "toolCallId": "b", "toolName": "write", "result": {}})
    d.on_message({"type": "extension_ui_request", "id": "d2", "method": "confirm", "title": "And this?"})
    card = asks[-1].event
    assert card["title"] == "Run rm x" and card["command"] == "rm x" and card["kind"] == "command"


# --------------------------------------------------------------------------- #
# 8. A select takes one of its choices only
# --------------------------------------------------------------------------- #
def test_8_a_select_refuses_typed_words_and_offers_no_free_text():
    asks: list = []
    d = _driver([], asks)
    long = "Allow it, " + "x" * 200
    d.on_message({"type": "extension_ui_request", "id": "s1", "method": "select", "title": "Which?",
                  "options": ["Allow", "Block", long]})
    ask: Ask = asks[-1]
    row = ask.event["questions"][0]
    assert row["free_text"] is False
    with pytest.raises(AskRefused):
        d.prepare_answer(ask, "allow", "", {"Which?": "something I typed"})
    assert d.prepare_answer(ask, "allow", "", {"Which?": "Block"})["value"] == "Block"
    shown = row["options"][2]["label"]
    assert d.prepare_answer(ask, "allow", "", {"Which?": shown})["value"] == long, "the card's cut label maps back"
    d.on_message({"type": "extension_ui_request", "id": "i1", "method": "input", "title": "Name?"})
    assert "free_text" not in asks[-1].event["questions"][0], "an input is typed words"


# --------------------------------------------------------------------------- #
# 9. What Pi's cost is
# --------------------------------------------------------------------------- #
def test_9_pis_cost_is_never_called_list_price():
    out: list = []
    d = _driver(out)
    d.turn_open = True
    d.on_message({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [],
                                                     "usage": {"input": 10, "output": 5, "cost": {"total": 0.25}}}})
    d.on_message({"type": "agent_settled", "aborted": False})
    usage = next(e for e in out if e["type"] == "usage")
    assert usage["list_price"] is False and usage["cost_usd"] == 0.25 and usage["cost_reported_by"] == "Pi"


# --------------------------------------------------------------------------- #
# 10. /login
# --------------------------------------------------------------------------- #
def test_10_the_sign_in_words_point_at_a_terminal():
    for words in (W.PI_OWN_SIGN_IN, W.PI_LOGIN_HERE):
        assert "terminal" in words and "/login" in words


async def test_10_login_typed_in_the_code_view_is_answered_never_sent_to_the_model(app, work, tmp_path, monkeypatch):
    log = scripted(tmp_path, monkeypatch, [await_cmd("prompt"), reply("prompt", {"disposition": "started"})])
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        for typed in ("/login", "  /login anthropic", "/logout"):
            r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": typed, "turn_id": "t-" + typed.strip()})
            assert r.status_code == 400 and r.json()["detail"] == W.PI_LOGIN_HERE, typed
    await stop_all(app)
    assert not [m for m in sent(log) if m.get("type") == "prompt"]


# --------------------------------------------------------------------------- #
# 11. A conversation live in a Build terminal
# --------------------------------------------------------------------------- #
class _Pane:
    def __init__(self, cli: str, tail: str):
        self.agent_cli = cli
        self.resume_cli = None
        self.claude_session_id = None
        self.shell = "pwsh.exe"
        self.alive = True
        self._tail = tail

    def output_tail(self):
        return self._tail


@pytest.mark.parametrize("harness,cli,line,exit_cmd", [
    ("pi", "pi", f"pi --session {SID_A}", "/quit"),
    ("codex", "codex", f"codex resume {SID_A}", "/exit"),
])
async def test_11_a_terminal_running_the_conversation_holds_it(app, work, tmp_path, monkeypatch,
                                                               harness, cli, line, exit_cmd):
    root = tmp_path / "pi-sessions"
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(root))
    monkeypatch.setattr(S, "binary_for", lambda h: [sys.executable, str(FAKE)])
    panes = {"p": _Pane(cli, f"PS C:\\work> {line}\n  working on it...\n")}
    terminals = app.state.platform.terminals
    monkeypatch.setattr(terminals, "list", lambda: [{"id": k, "alive": True} for k in panes])
    monkeypatch.setattr(terminals, "get", lambda pid: panes.get(pid))
    svc = S.service_for(app.state.platform)
    from iron_jarvis.hosted import store

    async with client(app) as c:
        sid = (await new_session(c, work, harness=harness))["id"]
        await asyncio.to_thread(store.update, svc.engine, sid, harness_session_id=SID_A)
        r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "hi", "turn_id": "t1"})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == W.HELD_BY_TERMINAL.format(label=W.label(harness), exit=exit_cmd)
        # Another conversation in that terminal holds nothing of this one.
        assert svc._holder(harness, "01a1b2c3-dddd-7222-8333-444444444444", except_sid=sid) == ""
        # The CLI exited there: the conversation is free.
        panes["p"]._tail = f"PS C:\\work> {line}\nbye\nPS C:\\work> "
        assert svc._holder(harness, SID_A, except_sid=sid) == ""
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 12. A dialog Pi resolved by itself
# --------------------------------------------------------------------------- #
def test_12_an_aborted_run_closes_every_card_and_ends_interrupted_without_a_stop():
    out: list = []
    asks: list = []
    forgotten: list = []
    d = _driver(out, asks)
    d.forget_ask = forgotten.append
    d.turn_open, d._turn_n, d._waiting_run = True, 1, True
    # A dialog from the extension command's preflight (no run yet) …
    d.on_message({"type": "extension_ui_request", "id": "pre", "method": "confirm", "title": "First?"})
    d.on_message({"type": "agent_start"})
    # … and one from inside the run.
    d.on_message({"type": "extension_ui_request", "id": "mid", "method": "select", "title": "Which?",
                  "options": ["a", "b"]})
    d.on_message({"type": "agent_settled", "aborted": True})  # an extension aborted it, not our Stop
    assert sorted(forgotten) == sorted(a.id for a in asks), "Pi resolved both on the abort: both cards go"
    assert out[-1]["type"] == "done" and out[-1]["stop"] == "interrupted"


def test_12_a_run_that_ends_closes_only_its_own_cards():
    asks: list = []
    forgotten: list = []
    d = _driver([], asks)
    d.forget_ask = forgotten.append
    d.on_message({"type": "extension_ui_request", "id": "outside", "method": "input", "title": "Name?"})
    d.on_message({"type": "agent_start"})
    d.on_message({"type": "extension_ui_request", "id": "inside", "method": "confirm", "title": "Go?"})
    d.on_message({"type": "agent_settled", "aborted": False})
    assert forgotten == [asks[1].id], "the run's own dialog went with it; the other still waits"


async def test_12_a_closed_card_leaves_the_bell_and_the_session_not_busy(app, work, tmp_path, monkeypatch):
    scripted(tmp_path, monkeypatch, [
        await_cmd("prompt"),
        reply("prompt", {"disposition": "started"}),
        ev({"type": "agent_start"}),
        ev({"type": "extension_ui_request", "id": "dlg", "method": "confirm", "title": "Go?"}),
        {"sleep": 0.5},
        *text_run("", aborted=True),
    ])
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        evs = frames((await asyncio.wait_for(await _turn(c, sid), 30)).text)
        live = svc.live[sid]
        assert not live.asks and not live.busy
        assert S.waiting_rows(app.state.platform) == []
    await stop_all(app)
    assert any(e["type"] == "resolved" and e["decision"] == "expired" for e in evs)
    assert evs[-1]["stop"] == "interrupted"
