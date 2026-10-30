"""v1.412.0 — Build › Code: pictures reach the REAL tool, the mode changes at
any time, and a note typed while it works STEERS the running reply.

The user: "I cant copy and past images into the build modules </> code …
the permission blocker does not allow change to auto-- allow all, should be
changeable mid session and the ability to steer work."

Every turn here runs the real service, routes, parser and process plumbing
against ``tests/fixtures/fake_harness_v1409.py`` playing a SCRIPTED harness
written from what the installed tools were MEASURED to say (claude 2.1.311,
codex 0.157.0, pi 1.3.0 — see the module docs of ``hosted/claude.py``,
``hosted/codex.py`` and ``hosted/pi.py``). Never a real tool.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all, until
from tests._build_app_pi_helpers_v1411 import FIX_PI

# --------------------------------------------------------------------------- #
# Scripted harnesses (the frames the real tools were measured to send)
# --------------------------------------------------------------------------- #
INIT = {"out": {"type": "control_response", "response": {
    "subtype": "success", "request_id": "x", "response": {"commands": []}}}, "reply_to_control": "initialize"}
SYS_INIT = {"out": {"type": "system", "subtype": "init", "session_id": "rec-sid", "cwd": "C:\\work",
                    "model": "claude-haiku-5-5", "permissionMode": "default"}}


def life(n: int, state: str) -> dict:
    return {"out": {"type": "command_lifecycle", "command_uuid": "{{uuid:%d}}" % n, "state": state}}


def say(mid: str, text: str) -> dict:
    return {"out": {"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": text}]}}}


def result(text: str = "ok", sub: str = "success", **extra) -> dict:
    return {"out": {"type": "result", "subtype": sub, "result": text, "session_id": "rec-sid",
                    "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0.0, **extra}}


TOOL_USE = {"out": {"type": "assistant", "message": {"id": "m1", "content": [
    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ping -n 6 127.0.0.1"}}]}}}
TOOL_DONE = {"out": {"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "tu1", "content": "6 replies"}]}}}


def claude_script(*steps: dict) -> dict:
    return {"steps": [{"await": "control", "subtype": "initialize"}, INIT, *steps]}


def write_scenario(base: Path, script: dict) -> Path:
    path = base / "scenario.json"
    path.write_bytes(json.dumps(script).encode("utf-8"))
    return path


def use(monkeypatch, scenario: Path, log: Path, *, modes: bool = False) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    if modes:
        monkeypatch.setenv("IJ_FAKE_CLAUDE_MODES", "1")


@pytest.fixture()
def base(tmp_path):
    (tmp_path / "work").mkdir()
    return tmp_path


async def start(app, base: Path, harness: str = "claude-code", text: str = "do it", files=None, **extra):
    c = client(app)
    sid = (await new_session(c, base / "work", harness=harness, **extra))["id"]
    body = {"text": text, "turn_id": "t", **({"files": files} if files else {})}
    task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json=body))
    return c, sid, task


def types(evs: list[dict]) -> list[str]:
    return [e["type"] for e in evs]


# --------------------------------------------------------------------------- #
# A. Pictures reach the tool, as pictures
# --------------------------------------------------------------------------- #
async def test_a_picture_goes_to_claude_code_as_an_image_block_and_a_file_by_path(base, monkeypatch):
    png = base / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pixels" * 10)
    notes = base / "notes.txt"
    notes.write_text("hi", encoding="utf-8")
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, claude_script({"await": "user"}, SYS_INIT, say("m", "seen"),
                                                         result("seen"))), log)
    app = create_app(str(base / "home"))
    c, sid, task = await start(app, base, text="what is in the picture?", files=[str(png), str(notes)])
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    msg = next(m for m in sent(log) if m.get("type") == "user")
    content = msg["message"]["content"]
    assert content[0]["type"] == "text"
    assert "what is in the picture?" in content[0]["text"]
    assert str(png) in content[0]["text"] and f"@{notes}" in content[0]["text"]
    image = next(b for b in content if b["type"] == "image")
    assert image["source"] == {"type": "base64", "media_type": "image/png",
                               "data": base64.b64encode(png.read_bytes()).decode()}
    assert msg.get("uuid"), "every message carries a uuid (Claude Code then says when it read it)"
    user = next(e for e in evs if e["type"] == "user")
    assert user["files"] == [str(png), str(notes)], "the sent message keeps its pictures (thumbnails, rejoin)"


async def test_a_picture_claude_code_cannot_take_goes_by_path_and_says_so(base, monkeypatch):
    big = base / "huge.png"
    big.write_bytes(b"\x89PNG" + b"0" * (C.MAX_IMAGE_BYTES + 10))
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, claude_script({"await": "user"}, SYS_INIT, result())), log)
    app = create_app(str(base / "home"))
    c, sid, task = await start(app, base, text="", files=[str(big)])
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    content = next(m for m in sent(log) if m.get("type") == "user")["message"]["content"]
    assert [b["type"] for b in content] == ["text"], "never sent as a picture past what Claude Code takes"
    assert f"@{big}" in content[0]["text"]
    notice = [e["text"] for e in evs if e["type"] == "notice"]
    assert any("huge.png went to Claude Code as a file path, not as a picture" in t and "5 MB" in t for t in notice)


def test_codex_takes_a_picture_as_a_local_image():
    items = X.user_input("look", [r"C:\p\a.PNG", r"C:\p\b.webp", r"C:\p\c.txt"])
    assert items[0] == {"type": "text", "text": "look\n\nFiles I attached:\nC:\\p\\c.txt"}
    assert items[1:] == [{"type": "localImage", "path": r"C:\p\a.PNG"}, {"type": "localImage", "path": r"C:\p\b.webp"}]


async def test_a_pi_model_that_takes_no_pictures_gets_the_path_and_the_user_is_told(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG" + b"1" * 20)
    said: list[dict] = []

    async def write(obj):  # noqa: ARG001
        return None

    drv = P.PiDriver(write=write, emit=said.append, ask=lambda a: None, on_session_id=lambda s: None, mode="own")
    drv.takes_pictures = P.takes_pictures({"input": ["text"]})
    msg = await drv._message("look", [str(png)])
    assert "images" not in msg and str(png) in msg["message"]
    assert any("s.png went to Pi as a file path, not as a picture: this Pi model takes no pictures" in e["text"]
               for e in said)
    drv.takes_pictures = P.takes_pictures({"input": ["text", "image"]})
    said.clear()
    msg = await drv._message("look", [str(png)])
    assert msg["images"][0]["mimeType"] == "image/png" and not said
    rows = P.model_rows([{"provider": "p", "id": "blind", "input": ["text"]}, {"provider": "p", "id": "sees"}])
    assert rows[0]["pictures"] is False and "pictures" not in rows[1]


# --------------------------------------------------------------------------- #
# B. The mode changes at any time — Full access included
# --------------------------------------------------------------------------- #
def test_claude_code_starts_able_to_switch_to_full_access_later_but_not_in_it():
    drv = C.ClaudeDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                         mode="ask")
    argv = drv.argv(session_id="s", resume=False)
    assert C.ALLOW_FULL_FLAG in argv, "without it Claude Code refuses Full access mid-session (bypass_not_launched)"
    assert argv[argv.index("--permission-mode") + 1] == "default", "the flag switches nothing on"
    assert "--dangerously-skip-permissions" not in argv


async def test_full_access_and_auto_switch_live_mid_reply(base, monkeypatch):
    """THE BUG: a session started in Ask first could not be switched to Full
    access — Claude Code refused (`bypass_not_launched`) because the process
    was not started with `--allow-dangerously-skip-permissions`. The fake
    answers exactly as claude 2.1.311 does."""
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, life(1, "queued"), life(1, "started"), TOOL_USE,
        {"await": "control", "subtype": "set_permission_mode"},
        {"await": "control", "subtype": "set_permission_mode"},
        TOOL_DONE, say("m2", "done"), result("done"))), log, modes=True)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base)
    await until(lambda: svc.turn("t") and any(e["type"] == "tool" for e in svc.turn("t").events))
    r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "full", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["mode"] == "now" and r.json()["session"]["mode"] == "full"
    r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "auto"})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["mode"] == "now" and r.json()["session"]["mode"] == "auto"
    r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "full"})
    assert r.status_code == 400, "Full access still needs its second press (confirm)"
    evs = frames((await asyncio.wait_for(task, 40)).text)
    await c.aclose()
    await stop_all(app)
    modes = [(m.get("request") or {}).get("mode") for m in sent(log)
             if (m.get("request") or {}).get("subtype") == "set_permission_mode"]
    assert modes == ["bypassPermissions", "auto"]
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"


async def test_a_mode_chosen_while_a_card_waits_says_the_card_still_needs_you(base, monkeypatch):
    ask = {"out": {"type": "control_request", "request_id": "cu1", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "echo hi"}, "tool_use_id": "tu1"}}}
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, life(1, "queued"), life(1, "started"), ask,
        {"await": "response", "request_id": "cu1"}, result())), base / "fake.log", modes=True)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base)
    ask_id = await until(lambda: next(iter(svc.live[sid].asks), None) if sid in svc.live else None)
    r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "full", "confirm": True})
    assert r.status_code == 200, r.text
    assert sid in svc.live and ask_id in svc.live[sid].asks, "the card stays: only the user answers it"
    await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask_id, "decision": "allow"})
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    words = W.MODE_CARD_STILL_OPEN.format(label="Claude Code", mode="Full access")
    assert any(e["type"] == "notice" and e["text"] == words for e in evs)


def test_every_mode_says_what_it_does():
    for harness in S.HARNESSES:
        for row in S.mode_rows(harness):
            assert row["about"].strip(), (harness, row)
    claude = {r["id"]: r for r in S.mode_rows("claude-code")}
    assert claude["auto"]["label"] == "Auto" and not claude["auto"]["dangerous"]
    assert "safety check" in claude["auto"]["about"]
    assert C.MODES["auto"] == "auto"


# --------------------------------------------------------------------------- #
# C. A note while it works (a steer)
# --------------------------------------------------------------------------- #
async def test_a_note_during_a_tool_reaches_claude_code_in_the_same_reply(base, monkeypatch):
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, life(1, "queued"), life(1, "started"), TOOL_USE,
        {"await": "user"}, life(2, "queued"), {"sleep": 0.2}, life(2, "started"),
        TOOL_DONE, say("m2", "6 replies. BANANA"), result("6 replies. BANANA"),
        life(1, "completed"), life(2, "completed"))), log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base)
    await until(lambda: svc.turn("t") and any(e["type"] == "tool" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "Also say BANANA."})
    assert r.status_code == 200, r.text
    assert r.json()["how"] == "step"
    evs = frames((await asyncio.wait_for(task, 30)).text)
    notes = [e for e in evs if e["type"] == "steer"]
    assert [n["state"] for n in notes] == ["sent", "read"]
    assert notes[0]["text"] == "Also say BANANA." and notes[0]["how"] == W.STEER_HOW["claude-code"]
    assert types(evs).index("steer") < types(evs).index("done") and types(evs).count("done") == 1
    users = [m for m in sent(log) if m.get("type") == "user"]
    assert len(users) == 2 and users[1]["message"]["content"][0]["text"] == "Also say BANANA."
    # The record holds the note: a rejoin (a reload) shows it in order.
    replay = frames((await c.get(f"/build/app/sessions/{sid}/turns/t/stream")).text)
    assert [e["state"] for e in replay if e["type"] == "steer"] == ["sent", "read"]
    await c.aclose()
    await stop_all(app)


async def test_a_note_after_the_last_step_runs_right_after_and_the_reply_ends_once(base, monkeypatch):
    """Measured: with no tool call left, Claude Code runs the note as its own
    run right after the first `result` — still the same reply here."""
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, life(1, "queued"), life(1, "started"), say("m1", "counting"),
        {"await": "user"}, life(2, "queued"), result("counting"), life(1, "completed"), life(2, "started"),
        SYS_INIT, say("m2", "BANANA"), result("BANANA"), life(2, "completed"))), base / "fake.log")
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base)
    await until(lambda: svc.turn("t") and any(e["type"] == "text_done" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "Now say BANANA."})
    assert r.status_code == 200, r.text
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    assert types(evs).count("done") == 1 and evs[-1]["type"] == "done"
    assert evs[-1]["stop"] == "completed" and evs[-1].get("result") == "BANANA"
    assert [e["text"] for e in evs if e["type"] == "text_done"] == ["counting", "BANANA"]
    assert [e["state"] for e in evs if e["type"] == "steer"] == ["sent", "read"]


async def test_stop_cancels_a_note_not_read_and_it_goes_back_to_the_box(base, monkeypatch):
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, life(1, "queued"), life(1, "started"), say("m1", "a long answer"),
        {"await": "user"}, life(2, "queued"),
        {"await": "control", "subtype": "interrupt"},
        life(2, "cancelled"),
        {"out": {"type": "control_response", "response": {"subtype": "success", "request_id": "x", "response": {
            "still_queued": [], "cancelled": ["{{uuid:2}}"]}}}, "reply_to_control": "interrupt"},
        result("", "error_during_execution", is_error=True, terminal_reason="aborted_streaming"),
        life(1, "cancelled"))), log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base)
    await until(lambda: svc.turn("t") and any(e["type"] == "text_done" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "then do this", "files": []})
    assert r.status_code == 200, r.text
    await c.post(f"/build/app/sessions/{sid}/interrupt")
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    stop = next(m for m in sent(log) if (m.get("request") or {}).get("subtype") == "interrupt")
    assert stop["request"].get("cancel_queued") is True, "a note not read must not run after the stop"
    notes = [e for e in evs if e["type"] == "steer"]
    assert [n["state"] for n in notes] == ["sent", "returned"]
    assert notes[1]["text"] == "then do this"
    assert types(evs).index("steer", types(evs).index("steer") + 1) < types(evs).index("done")
    assert evs[-1]["stop"] == "interrupted"


async def test_a_note_claude_code_never_queues_does_not_hold_the_reply_forever(base, monkeypatch):
    monkeypatch.setattr(C, "HOLD_FOR_NOTE_S", 0.4)
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, life(1, "queued"), life(1, "started"), say("m1", "x"),
        {"await": "user"}, result("x"))), base / "fake.log")
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base)
    await until(lambda: svc.turn("t") and any(e["type"] == "text_done" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "lost?"})
    assert r.status_code == 200, r.text
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    assert [e["state"] for e in evs if e["type"] == "steer"] == ["sent", "returned"]
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"


async def test_a_note_with_no_reply_running_is_refused_so_the_page_sends_a_message(base, monkeypatch):
    use(monkeypatch, write_scenario(base, claude_script()), base / "fake.log")
    app = create_app(str(base / "home"))
    async with client(app) as c:
        sid = (await new_session(c, base / "work"))["id"]
        r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "hello"})
        assert r.status_code == 409
        assert r.json() == {"detail": W.STEER_NOT_WORKING.format(label="Claude Code"), "code": "not_working"}
        r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": " "})
        assert r.status_code == 400
    await stop_all(app)


async def test_an_older_claude_code_without_lifecycle_refuses_a_note_in_words(base, monkeypatch):
    monkeypatch.setattr(C, "LIFECYCLE_WAIT_S", 0.2)
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, TOOL_USE, {"await": "control", "subtype": "interrupt", "within": 2},
        TOOL_DONE, result())), base / "fake.log")
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base)
    await until(lambda: svc.turn("t") and any(e["type"] == "tool" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "hi"})
    assert r.status_code == 409 and r.json()["detail"] == W.STEER_UNSUPPORTED.format(label="Claude Code")
    await asyncio.wait_for(task, 30)
    await c.aclose()
    await stop_all(app)


def codex_script(*middle: dict) -> dict:
    tid, turn = "01a1-thread", "01a1-turn"
    return {"steps": [
        {"await": "request", "method": "initialize"},
        {"out": {"id": 1, "result": {"codexHome": "C:\\x"}}, "reply_to": "initialize"},
        {"await": "request", "method": "thread/start"},
        {"out": {"id": 2, "result": {"thread": {"id": tid}, "model": "gpt-6"}}, "reply_to": "thread/start"},
        {"await": "request", "method": "turn/start"},
        {"out": {"id": 3, "result": {"turn": {"id": turn, "status": "inProgress"}}}, "reply_to": "turn/start"},
        {"out": {"method": "item/started", "params": {"item": {
            "type": "commandExecution", "id": "exec-1", "command": "ping", "status": "inProgress"}}}},
        *middle,
        {"out": {"method": "item/completed", "params": {"item": {
            "type": "commandExecution", "id": "exec-1", "command": "ping", "status": "completed", "exitCode": 0}}}},
        {"out": {"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "id": "msg-1", "text": "6 replies. BANANA"}}}},
        {"out": {"method": "turn/completed", "params": {"turn": {"id": turn, "status": "completed"}}}},
    ]}


async def test_a_note_reaches_codex_through_turn_steer(base, monkeypatch):
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, codex_script(
        {"await": "request", "method": "turn/steer"},
        {"out": {"id": 4, "result": {"turnId": "01a1-turn"}}, "reply_to": "turn/steer"},
        {"out": {"method": "item/started", "params": {"item": {
            "type": "userMessage", "id": "um-2", "clientId": "{{steer_ref}}",
            "content": [{"type": "text", "text": "end with BANANA"}]}}}},
    )), log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base, harness="codex")
    await until(lambda: svc.turn("t") and any(e["type"] == "tool" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "end with BANANA"})
    assert r.status_code == 200, r.text
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    steer = next(m for m in sent(log) if m.get("method") == "turn/steer")
    assert steer["params"]["expectedTurnId"] == "01a1-turn" and steer["params"]["threadId"] == "01a1-thread"
    assert steer["params"]["input"] == [{"type": "text", "text": "end with BANANA"}]
    assert steer["params"]["clientUserMessageId"] == r.json()["id"]
    assert [e["state"] for e in evs if e["type"] == "steer"] == ["sent", "read"]
    assert evs[-1]["type"] == "done"


async def test_codex_refusing_a_late_note_is_said_and_nothing_is_kept(base, monkeypatch):
    use(monkeypatch, write_scenario(base, codex_script(
        {"await": "request", "method": "turn/steer"},
        {"out": {"id": 4, "error": {"code": -32600, "message": "expected turn id does not match"}},
         "reply_to": "turn/steer"},
    )), base / "fake.log")
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base, harness="codex")
    await until(lambda: svc.turn("t") and any(e["type"] == "tool" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "too late"})
    assert r.status_code == 409 and r.json()["detail"] == W.STEER_TOO_LATE.format(label="Codex")
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    assert not [e for e in evs if e["type"] == "steer"], "a note that never went is not drawn"


def pi_prefix() -> list[dict]:
    steps = json.loads((FIX_PI / "pi_read.json").read_text(encoding="utf-8"))["steps"]
    return steps[:15]  # get_state … agent_start (the run of the user's message)


PI_TAIL = [
    {"out": {"type": "message_start", "message": {"role": "assistant", "content": []}}},
    {"out": {"type": "message_update", "assistantMessageEvent": {"type": "text_end", "contentIndex": 0,
                                                                  "content": "6 replies. BANANA"}}},
    {"out": {"type": "message_end", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "6 replies. BANANA"}], "usage": {"input": 1, "output": 1}, "stopReason": "stop"}}},
    {"out": {"type": "agent_end", "messages": []}},
    {"out": {"type": "agent_settled", "aborted": False}},
]


async def test_a_note_reaches_pi_through_steer_and_is_read_when_its_queue_empties(base, monkeypatch):
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, {"steps": [
        *pi_prefix(),
        {"out": {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash",
                 "args": {"command": "ping"}}},
        {"await": "command", "command": "steer"},
        {"out": {"type": "queue_update", "steering": ["end with BANANA"], "followUp": []}},
        {"out": {"id": "x", "type": "response", "command": "steer", "success": True,
                 "data": {"disposition": "queued"}}, "reply_to_command": "steer"},
        {"out": {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "bash",
                 "result": {"content": [{"type": "text", "text": "6"}]}}},
        {"out": {"type": "queue_update", "steering": [], "followUp": []}},
        *PI_TAIL,
    ]}), log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base, harness="pi")
    await until(lambda: svc.turn("t") and any(e["type"] == "tool" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "end with BANANA"})
    assert r.status_code == 200, r.text
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    steer = next(m for m in sent(log) if m.get("type") == "steer")
    assert steer["message"] == "end with BANANA"
    assert [e["state"] for e in evs if e["type"] == "steer"] == ["sent", "read"]
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"


async def test_pi_stop_takes_a_queued_note_out_before_the_abort(base, monkeypatch):
    """Measured: Pi's abort RUNS a note still queued, and its clear_queue
    empties the queue (a queue_update) BEFORE it answers — that is not
    delivery."""
    log = base / "fake.log"
    use(monkeypatch, write_scenario(base, {"steps": [
        *pi_prefix(),
        {"out": {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                                      "delta": "At"}}},
        {"await": "command", "command": "steer"},
        {"out": {"type": "queue_update", "steering": ["then BANANA"], "followUp": []}},
        {"out": {"id": "x", "type": "response", "command": "steer", "success": True,
                 "data": {"disposition": "queued"}}, "reply_to_command": "steer"},
        {"await": "command", "command": "clear_queue"},
        {"out": {"type": "queue_update", "steering": [], "followUp": []}},
        {"out": {"id": "x", "type": "response", "command": "clear_queue", "success": True,
                 "data": {"steering": ["then BANANA"], "followUp": []}}, "reply_to_command": "clear_queue"},
        {"await": "command", "command": "abort"},
        {"out": {"id": "x", "type": "response", "command": "abort", "success": True}, "reply_to_command": "abort"},
        {"out": {"type": "agent_settled", "aborted": True}},
    ]}), log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    c, sid, task = await start(app, base, harness="pi")
    await until(lambda: svc.turn("t") and any(e["type"] == "text" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "then BANANA"})
    assert r.status_code == 200, r.text
    await c.post(f"/build/app/sessions/{sid}/interrupt")
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    order = [m.get("type") for m in sent(log) if m.get("type") in ("clear_queue", "abort")]
    assert order == ["clear_queue", "abort"]
    assert [e["state"] for e in evs if e["type"] == "steer"] == ["sent", "returned"]
    assert evs[-1]["stop"] == "interrupted"


def test_the_contract_lists_the_note():
    assert "steer" in E.TYPES
