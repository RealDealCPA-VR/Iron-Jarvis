"""v1.412.0 review — Build › Code's notes, modes and pictures, the holes an
independent review found (each pin was RED on the builder's commit 9f06b7b3):

* A note whose reply ENDED while the note was being prepared (a picture read,
  one thread hop) was still written to the tool: Claude Code then ran it as a
  turn nobody follows and Pi queued it for the NEXT message — while the page
  put the same words back in the box (two deliveries).
* A note the tool READ, on a turn that ended before the note's own answer
  came back, was reported ``returned`` — the page put it back in the box and
  the user sent it twice.
* An attachment spelled as a network share was STAT-ed (an SMB connection —
  the v1.379.0 lesson) before anything refused it.
* "Allow for this session" could switch Claude Code to its own Auto mode (its
  classifier runs actions without asking) as a side effect of one card.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import ControlRefused
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, stop_all, until


def _driver(cls):
    written: list[dict] = []
    emitted: list[dict] = []

    async def write(obj):
        written.append(obj)
        replies = getattr(d, "_replies", None)
        if replies is not None and obj.get("id"):  # Pi answers each command
            asyncio.get_running_loop().call_soon(replies.settle, obj["id"], {"disposition": "queued"})

    d = cls(write=write, emit=emitted.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    return d, written, emitted


# --------------------------------------------------------------------------- #
# A note whose reply ends while it is being prepared never reaches the tool
# --------------------------------------------------------------------------- #
async def test_claude_a_note_whose_reply_ended_while_it_was_prepared_is_never_written():
    d, written, emitted = _driver(C.ClaudeDriver)
    d.turn_open = True
    d._lifecycle = True
    real = d._user_message

    async def prepared_while_the_reply_ends(text, files, ref):
        msg = await real(text, files, ref)
        # The reply's `result` arrives during the hop (reading the pictures).
        d._on_result({"type": "result", "subtype": "success", "result": "done", "usage": {}})
        return msg

    d._user_message = prepared_while_the_reply_ends
    with pytest.raises(ControlRefused) as got:
        await d.steer("note-1", "also this", [])
    assert str(got.value) == W.STEER_TOO_LATE.format(label="Claude Code")
    assert [o for o in written if o.get("type") == "user"] == [], "Claude Code would run it as a turn nobody follows"
    assert "note-1" not in d.steers
    assert [e["type"] for e in emitted].count("done") == 1


async def test_pi_a_note_whose_run_ended_while_it_was_prepared_is_never_queued():
    d, written, emitted = _driver(P.PiDriver)
    d.turn_open = True
    d._turn_n = 1
    d._run_open, d._run_owner = True, 1
    real = d._message

    async def prepared_while_the_run_ends(text, files):
        msg = await real(text, files)
        d._settled(False)  # agent_end arrives during the hop
        return msg

    d._message = prepared_while_the_run_ends
    with pytest.raises(ControlRefused) as got:
        await d.steer("note-1", "also this", [])
    assert str(got.value) == W.STEER_TOO_LATE.format(label="Pi")
    assert [o for o in written if o.get("type") == "steer"] == [], "Pi would hand it to the NEXT message"
    assert "note-1" not in d.steers


# --------------------------------------------------------------------------- #
# A note the tool READ is never reported as returned
# --------------------------------------------------------------------------- #
def _write_scenario(base: Path, script: dict) -> Path:
    path = base / "scenario.json"
    path.write_bytes(json.dumps(script).encode("utf-8"))
    return path


async def test_a_note_codex_read_on_a_turn_that_ended_first_is_read_not_returned(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    tid, turn = "01a1-thread", "01a1-turn"
    script = {"steps": [
        {"await": "request", "method": "initialize"},
        {"out": {"id": 1, "result": {"codexHome": "C:\\x"}}, "reply_to": "initialize"},
        {"await": "request", "method": "thread/start"},
        {"out": {"id": 2, "result": {"thread": {"id": tid}, "model": "gpt-6"}}, "reply_to": "thread/start"},
        {"await": "request", "method": "turn/start"},
        {"out": {"id": 3, "result": {"turn": {"id": turn, "status": "inProgress"}}}, "reply_to": "turn/start"},
        {"out": {"method": "item/started", "params": {"item": {
            "type": "commandExecution", "id": "exec-1", "command": "ping", "status": "inProgress"}}}},
        {"await": "request", "method": "turn/steer"},
        # Codex took the note into the turn, finished the turn, and only THEN
        # answered the steer request.
        {"out": {"method": "item/started", "params": {"item": {
            "type": "userMessage", "id": "um-2", "clientId": "{{steer_ref}}",
            "content": [{"type": "text", "text": "end with BANANA"}]}}}},
        {"out": {"method": "item/completed", "params": {"item": {
            "type": "commandExecution", "id": "exec-1", "command": "ping", "status": "completed", "exitCode": 0}}}},
        {"out": {"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "id": "msg-1", "text": "BANANA"}}}},
        {"out": {"method": "turn/completed", "params": {"turn": {"id": turn, "status": "completed"}}}},
        {"sleep": 0.5},
        {"out": {"id": 4, "result": {"turnId": turn}}, "reply_to": "turn/steer"},
    ]}
    log = tmp_path / "fake.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(_write_scenario(tmp_path, script)))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    c = client(app)
    sid = (await new_session(c, tmp_path / "work", harness="codex"))["id"]
    task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t"}))
    await until(lambda: svc.turn("t") and any(e["type"] == "tool" for e in svc.turn("t").events))
    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "end with BANANA"})
    await asyncio.wait_for(task, 30)
    view = (await c.get(f"/build/app/sessions/{sid}")).json()
    await c.aclose()
    await stop_all(app)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "read", "Codex read it: the page must not put it back in the box"
    notes = [e for e in view["events"] if e["type"] == "steer"]
    assert [n["state"] for n in notes] == ["sent", "read"]
    assert notes[0]["text"] == "end with BANANA"


# --------------------------------------------------------------------------- #
# A network-share spelling is refused before anything touches the disk
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", [
    r"\\ij-review-nohost\share\shot.png",
    "//ij-review-nohost/share/shot.png",
    r"\\?\UNC\ij-review-nohost\share\shot.png",
    r"\\?\GLOBALROOT\Device\x.png",
])
def test_an_attachment_on_a_network_share_is_refused_before_any_disk_call(tmp_path, monkeypatch, spelling):
    touched: list[str] = []

    def spy(name):
        def fn(p, *a, **kw):
            touched.append(f"{name}:{p}")
            return False
        return fn

    monkeypatch.setattr(S.os.path, "isfile", spy("isfile"))
    monkeypatch.setattr(S.os.path, "isabs", lambda p: True)
    svc = S.HostedService.__new__(S.HostedService)
    why = svc._file_problems([spelling], folder=str(tmp_path))
    assert why == W.ATTACHED_ON_SHARE
    assert touched == [], "an SMB connection (and the sign-in hash) went out before the refusal"


def test_an_attachment_on_a_share_is_allowed_when_the_session_folder_is_on_one(tmp_path, monkeypatch):
    monkeypatch.setattr(S.os.path, "isfile", lambda p: False)
    svc = S.HostedService.__new__(S.HostedService)
    why = svc._file_problems([r"\\host\share\work\shot.png"], folder=r"\\host\share\work")
    assert why != W.ATTACHED_ON_SHARE and "not found" in why


async def test_the_steer_and_turn_routes_refuse_a_share_spelling(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    touched: list[str] = []
    real_isfile = os.path.isfile

    def isfile(p):
        if str(p).replace("/", "\\").startswith("\\\\"):
            touched.append(str(p))
        return real_isfile(p)

    monkeypatch.setattr(S.os.path, "isfile", isfile)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        sid = (await new_session(c, tmp_path / "work"))["id"]
        r = await c.post(f"/build/app/sessions/{sid}/turn",
                         json={"text": "x", "turn_id": "t", "files": [r"\\ij-review-nohost\s\a.png"]})
        body = r.text
        assert W.ATTACHED_ON_SHARE in body, body
    await stop_all(app)
    assert touched == []


# --------------------------------------------------------------------------- #
# Auto is chosen in the mode chip, never as a side effect of one card
# --------------------------------------------------------------------------- #
def test_allow_for_this_session_never_switches_claude_code_to_auto_or_full_access():
    assert C.session_suggestions([{"type": "setMode", "mode": "auto", "destination": "session"}]) == []
    assert C.session_suggestions([
        {"type": "addRules", "rules": [{"toolName": "Bash"}], "behavior": "allow", "destination": "session"},
        {"type": "setMode", "mode": "bypassPermissions", "destination": "session"},
    ]) == []
    kept = [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    assert C.session_suggestions(kept) == kept


# --------------------------------------------------------------------------- #
# Pins the builder's own guarantees lacked (each survived a mutation)
# --------------------------------------------------------------------------- #
async def test_claude_a_note_claude_code_itself_cancels_ends_a_held_reply_at_once():
    """Claude Code says `cancelled` for a queued note (its lifecycle): the
    reply held for that note ends NOW — never after HOLD_FOR_NOTE_S, and never
    held for ever."""
    d, written, emitted = _driver(C.ClaudeDriver)
    d.turn_open = True
    d._lifecycle = True
    d._uuids["u-1"] = "note-1"
    d.steers["note-1"] = "sent"
    d.on_message({"type": "command_lifecycle", "command_uuid": "u-1", "state": "queued"})
    d.on_message({"type": "result", "subtype": "success", "result": "done", "usage": {}})
    assert [e["type"] for e in emitted].count("done") == 0, "held for the queued note"
    d.on_message({"type": "command_lifecycle", "command_uuid": "u-1", "state": "cancelled"})
    assert [e["type"] for e in emitted].count("done") == 1
    assert d.turn_open is False and d.unread_steers() == ["note-1"]


async def test_pi_a_note_still_queued_when_the_reply_ends_is_taken_out_of_pis_queue():
    """Pi would hand a note still in its steering queue to the NEXT message,
    while the box gets it back: the reply's end clears the queue."""
    d, written, emitted = _driver(P.PiDriver)
    d.turn_open = True
    d._turn_n = 1
    d._run_open, d._run_owner = True, 1
    d._waiting_run = False
    d.steers["note-1"] = "sent"
    d._steer_order = ["note-1"]
    d._settled(False)
    await asyncio.sleep(0.05)
    assert any(o.get("type") == "clear_queue" for o in written), written
    assert d.unread_steers() == ["note-1"]


async def test_a_note_after_the_reply_finished_is_refused_so_the_page_sends_a_message(tmp_path, monkeypatch):
    """A session whose process is still up but whose reply has ENDED: 409
    `not_working` (the page sends it as a message) — never a 500."""
    (tmp_path / "work").mkdir()
    script = {"steps": [
        {"await": "control", "subtype": "initialize"},
        {"out": {"type": "control_response", "response": {
            "subtype": "success", "request_id": "x", "response": {"commands": []}}}, "reply_to_control": "initialize"},
        {"await": "user"},
        {"out": {"type": "system", "subtype": "init", "session_id": "rec-sid", "cwd": "C:\work",
                 "model": "m", "permissionMode": "default"}},
        {"out": {"type": "result", "subtype": "success", "result": "ok", "session_id": "rec-sid",
                 "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0.0}},
    ]}
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(_write_scenario(tmp_path, script)))
    monkeypatch.setenv("IJ_FAKE_LOG", str(tmp_path / "fake.log"))
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, tmp_path / "work"))["id"]
        r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t"})
        assert r.status_code == 200
        assert sid in svc.live, "the process is kept between messages"
        r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "and then?"})
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "not_working"
    await stop_all(app)
