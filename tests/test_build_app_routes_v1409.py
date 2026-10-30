"""v1.409.0 — Build's Code view: every route of ``daemon/routes/build_app.py``.

Each turn replays a RECORDED Claude Code / Codex session (masked live output,
``tests/fixtures/build_app_v1409``) through a fake harness process started by
the real ``core.procs`` plumbing; the parser, routes and store are the real
ones. Nothing here runs a real harness.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import (
    client,
    frames,
    new_session,
    sent,
    starts,
    stop_all,
    until,
    use_fake,
    wait_pending,
)


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "a.txt").write_text("readme\n", encoding="utf-8")
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def _turn(c, sid: str, text: str = "hi", turn_id: str = "t1"):
    return asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id}))


# --------------------------------------------------------------------------- #
# harnesses, create, list
# --------------------------------------------------------------------------- #
async def test_harnesses_are_read_from_cached_answers_only(app, monkeypatch):
    from iron_jarvis.core import procs
    from iron_jarvis.providers import cli_auth

    spawned = []
    monkeypatch.setattr(procs, "spawn", lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(cli_auth.DEFAULT_PROBE, "_start", lambda b: spawned.append(("probe", b)))
    monkeypatch.setattr(S, "binary_for", lambda h: ["x"] if h == "claude-code" else None)
    async with client(app) as c:
        rows = (await c.get("/build/app/harnesses")).json()["harnesses"]
    by = {r["id"]: r for r in rows}
    assert set(by) == {"claude-code", "codex"}
    assert by["claude-code"]["installed"] is True and by["codex"]["installed"] is False
    assert by["codex"]["signed_in"] is None
    assert [m["id"] for m in by["claude-code"]["modes"]] == ["ask", "edits", "plan", "full"]
    assert [m["id"] for m in by["claude-code"]["modes"] if m["dangerous"]] == ["full"]
    codex_modes = [m["id"] for m in by["codex"]["modes"]]
    assert "plan" not in codex_modes and "ask" in codex_modes and "full" in codex_modes
    assert {h["id"] for h in by["codex"]["hidden_modes"]} >= {"plan"}
    assert all(h["reason"] for h in by["codex"]["hidden_modes"])
    assert by["claude-code"]["models"][0] == {"id": "", "label": "Claude Code's default"}
    assert spawned == [], "a status read started a process"


async def test_create_validates_in_words(app, work, tmp_path):
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "gemini", "folder": str(work)})
        assert r.status_code == 400 and "claude-code" in r.json()["detail"]
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(tmp_path / "nope")})
        assert r.status_code == 400 and r.json()["detail"]
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work), "mode": "full"})
        assert r.status_code == 400 and "twice" in r.json()["detail"]
        r = await c.post("/build/app/sessions",
                         json={"harness": "claude-code", "folder": str(work), "mode": "full", "confirm": True})
        assert r.status_code == 201 and r.json()["mode"] == "full"
        r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(work), "mode": "plan"})
        assert r.status_code == 400 and "Codex" in r.json()["detail"]
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work), "model": "a b"})
        assert r.status_code == 400
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work), "effort": "huge"})
        assert r.status_code == 400 and "effort" in r.json()["detail"]
        ok = await new_session(c, work, model="haiku", effort="high")
    assert ok["harness"] == "claude-code" and ok["model"] == "haiku" and ok["effort"] == "high"
    assert ok["mode"] == "ask" and ok["harness_session_id"] == "" and ok["account"] == ""
    assert "account_json" not in ok and "home" not in json.dumps(ok)


async def test_list_is_newest_first_archived_hidden_and_by_folder(app, work, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    async with client(app) as c:
        a = await new_session(c, work, title="a")
        b = await new_session(c, other, title="b")
        cc = await new_session(c, work, title="c")
        ids = [s["id"] for s in (await c.get("/build/app/sessions")).json()["sessions"]]
        assert ids == [cc["id"], b["id"], a["id"]]
        assert (await c.post(f"/build/app/sessions/{b['id']}/archive")).json()["archived"] is True
        ids = [s["id"] for s in (await c.get("/build/app/sessions")).json()["sessions"]]
        assert b["id"] not in ids
        ids = [s["id"] for s in (await c.get("/build/app/sessions?archived=1")).json()["sessions"]]
        assert b["id"] in ids
        ids = [s["id"] for s in (await c.get(f"/build/app/sessions?folder={work}")).json()["sessions"]]
        assert set(ids) == {a["id"], cc["id"]}
        assert (await c.post(f"/build/app/sessions/{b['id']}/unarchive")).json()["archived"] is False


async def test_ties_on_updated_at_break_on_rowid(app, work):
    from datetime import datetime, timezone

    when = datetime(2026, 10, 29, tzinfo=timezone.utc)
    ids = [
        store.create(app.state.platform.engine, harness="codex", folder=str(work), updated_at=when).id
        for _ in range(4)
    ]
    rows = [r.id for r in store.list_rows(app.state.platform.engine)]
    assert rows == list(reversed(ids))


def write_claude_conversation(home: Path, folder: Path, sid: str, prompt: str) -> Path:
    path = home / "projects" / "C--demo" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "sessionId": sid, "cwd": str(folder),
                                "timestamp": "2026-10-29T10:00:00Z",
                                "message": {"role": "user", "content": prompt}}) + "\n", encoding="utf-8")
    return path


async def test_disk_listing_reads_the_harness_store_read_only(app, work, tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    write_claude_conversation(home, work, "11111111-2222-3333-4444-555555555555", "fix the bug")
    write_claude_conversation(home, tmp_path / "elsewhere", "22222222-2222-3333-4444-555555555555", "x")
    async with client(app) as c:
        r = await c.get("/build/app/sessions?disk=1")
        assert r.status_code == 400
        rows = (await c.get(f"/build/app/sessions?folder={work}&disk=1")).json()["disk"]
        assert [(x["source"], x["harness"], x["harness_session_id"], x["title"]) for x in rows] == [
            ("disk", "claude-code", "11111111-2222-3333-4444-555555555555", "fix the bug")]
        made = await new_session(c, work, harness_session_id="11111111-2222-3333-4444-555555555555")
        assert made["harness_session_id"] == "11111111-2222-3333-4444-555555555555"
        assert made["title"] == "fix the bug"
        rows = (await c.get(f"/build/app/sessions?folder={work}&disk=1")).json()["disk"]
        assert rows == [], "a disk session already opened here is listed once"
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work),
                                                      "harness_session_id": "11111111-2222-3333-4444-555555555555"})
        assert r.status_code == 409 and "already open" in r.json()["detail"], "one conversation, one row"
        r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(work),
                                                      "harness_session_id": "01a189c4-9316-7d30-b03e-8a6c95d7bacd"})
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# turns, answers, rejoin
# --------------------------------------------------------------------------- #
async def test_a_claude_turn_asks_the_user_and_sends_only_the_users_answer(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_allow", log)
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"])
        first = (await wait_pending(c, s["id"]))[0]
        assert first["kind"] == "command" and first["command"].startswith("Get-ChildItem")
        assert first["can_session"] is False and first["once_only"] is True
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": first["request_id"], "decision": "allow_session"})
        assert r.status_code == 400, "no session-wide allow was offered for this one"
        assert (await c.post(f"/build/app/sessions/{s['id']}/answer",
                             json={"request_id": first["request_id"], "decision": "allow"})).status_code == 200
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": first["request_id"], "decision": "allow"})
        assert r.status_code == 404, "an answered ask is gone"
        second = await until(lambda: _next_pending(c, s["id"], first["request_id"]))
        assert second["kind"] == "edit" and second["can_session"] is True and "+hi" in second["diff"]
        assert "once_only" not in second
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": second["request_id"], "decision": "allow_session"})
        assert r.status_code == 200
        resp = await asyncio.wait_for(task, 30)
    evs = frames(resp.text)
    assert [e["type"] for e in evs][0] == "user" and evs[-1] == {**evs[-1], "type": "done", "stop": "completed"}
    assert all(e.get("turn") == "t1" for e in evs)
    responses = [m["response"]["response"] for m in sent(log) if m.get("type") == "control_response"]
    assert responses[0] == {"behavior": "allow", "updatedInput": {
        "command": "Get-ChildItem -Force | Select-Object Mode, Length, Name",
        "description": "List files in current directory"}}
    assert responses[1]["updatedPermissions"] == [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    view = (await _view(app, s["id"]))
    argv = starts(log)[0]["argv"]
    assert view["session"]["harness_session_id"] == argv[argv.index("--session-id") + 1]
    assert view["running"] is None and view["pending"] == []
    await stop_all(app)


async def _next_pending(c, sid, not_id):
    p = (await c.get(f"/build/app/sessions/{sid}")).json()["pending"]
    return next((x for x in p if x["request_id"] != not_id), None)


async def _view(app, sid):
    async with client(app) as c:
        async def got():
            v = (await c.get(f"/build/app/sessions/{sid}")).json()
            return v if v["session"]["harness_session_id"] else None

        return await until(got)


async def test_a_deny_reaches_the_harness_with_the_users_words(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_deny", log)
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"])
        ask = (await wait_pending(c, s["id"]))[0]
        assert ask["title"] == "Write deny.txt"
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": ask["request_id"], "decision": "deny", "message": "not now"})
        assert r.json()["decision"] == "deny"
        resp = await asyncio.wait_for(task, 30)
    evs = frames(resp.text)
    tool_end = [e for e in evs if e["type"] == "tool" and e["status"] != "running"]
    assert tool_end and tool_end[0]["status"] == "failed"
    resp_sent = [m["response"]["response"] for m in sent(log) if m.get("type") == "control_response"]
    assert resp_sent == [{"behavior": "deny", "message": "not now"}]
    await stop_all(app)


async def test_ask_user_question_is_answered_in_claudes_own_shape(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_ask", log)
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"])
        q = (await wait_pending(c, s["id"]))[0]
        assert q["type"] == "question" and "once_only" not in q
        assert q["questions"][0]["options"][1]["label"] == "Blue" and q["questions"][0]["multi"] is False
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": q["request_id"], "decision": "allow"})
        assert r.status_code == 400 and "answer" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": q["request_id"], "decision": "allow",
                               "answers": {"Which colour do you prefer?": "Blue"}})
        assert r.status_code == 200
        await asyncio.wait_for(task, 30)
    got = [m["response"]["response"] for m in sent(log) if m.get("type") == "control_response"][0]
    assert got["behavior"] == "allow"
    assert got["updatedInput"]["answers"] == {"Which colour do you prefer?": "Blue"}
    assert got["updatedInput"]["questions"][0]["question"] == "Which colour do you prefer?"
    await stop_all(app)


async def test_a_second_message_while_one_runs_is_refused_in_words(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"])
        ask = (await wait_pending(c, s["id"]))[0]
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "more", "turn_id": "t2"})
        assert r.status_code == 409 and "still working" in r.json()["detail"]
        r = await c.delete(f"/build/app/sessions/{s['id']}")
        assert r.status_code == 409, "a delete is refused while a turn runs"
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask["request_id"], "decision": "deny"})
        await asyncio.wait_for(task, 30)
    await stop_all(app)


async def test_closing_the_page_never_stops_the_turn_and_a_rejoin_replays_it(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"], turn_id="keep-me")
        ask = (await wait_pending(c, s["id"]))[0]
        task.cancel()  # the page went away
        await asyncio.sleep(0.2)
        assert svc.turn("keep-me").running, "leaving the page stopped the turn"
        rejoin = asyncio.ensure_future(c.get(f"/build/app/sessions/{s['id']}/turns/keep-me/stream"))
        await asyncio.sleep(0.2)
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask["request_id"], "decision": "deny"})
        evs = frames((await asyncio.wait_for(rejoin, 30)).text)
        assert evs[0]["type"] == "user" and evs[-1]["type"] == "done"
        # A repeated send of the same turn id follows it, never runs it twice
        # (its replay: the same events, streamed deltas merged).
        again = frames((await c.post(f"/build/app/sessions/{s['id']}/turn",
                                     json={"text": "hi", "turn_id": "keep-me"})).text)
        from iron_jarvis.hosted.events import coalesce

        assert [e["type"] for e in again] == [e["type"] for e in coalesce(evs)]
        # Evicted from memory: replayed from events.jsonl.
        svc.turns.clear()
        replay = frames((await c.get(f"/build/app/sessions/{s['id']}/turns/keep-me/stream")).text)
        assert [e["type"] for e in replay][0] == "user" and replay[-1]["type"] == "done"
        assert (await c.get(f"/build/app/sessions/{s['id']}/turns/nope/stream")).status_code == 404
    await stop_all(app)


async def test_interrupt_sends_claudes_own_interrupt_and_the_turn_says_interrupted(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_interrupt", log)
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"])

        async def has_text():
            v = (await c.get(f"/build/app/sessions/{s['id']}")).json()
            return any(e["type"] == "text" for e in v["events"])

        await until(has_text)
        r = await c.post(f"/build/app/sessions/{s['id']}/interrupt")
        assert r.json() == {"ok": True, "running": True}
        evs = frames((await asyncio.wait_for(task, 30)).text)
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "interrupted"
    assert any(m.get("type") == "control_request" and m["request"]["subtype"] == "interrupt" for m in sent(log))
    async with client(app) as c:
        assert (await c.post(f"/build/app/sessions/{s['id']}/interrupt")).json() == {"ok": True, "running": False}
    await stop_all(app)


async def test_a_codex_turn_asks_for_its_command_and_its_file_change(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_allow", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="codex")
        task = await _turn(c, s["id"])
        cmd = (await wait_pending(c, s["id"]))[0]
        # R1: Codex did not offer acceptForSession for this command
        # (its `availableDecisions`), so the card offers once only.
        assert cmd["kind"] == "command" and "Get-ChildItem" in cmd["command"]
        assert cmd["can_session"] is False and cmd["once_only"] is True
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": cmd["request_id"], "decision": "allow_session"})
        assert r.status_code == 400
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": cmd["request_id"], "decision": "allow"})
        edit = await until(lambda: _next_pending(c, s["id"], cmd["request_id"]))
        assert edit["kind"] == "edit" and edit["title"] == "Create hello.txt" and "+hi" in edit["diff"]
        await c.post(f"/build/app/sessions/{s['id']}/answer",
                     json={"request_id": edit["request_id"], "decision": "allow_session"})
        evs = frames((await asyncio.wait_for(task, 30)).text)
    assert evs[-1]["stop"] == "completed"
    msgs = sent(log)
    start = next(m for m in msgs if m.get("method") == "thread/start")["params"]
    assert start["approvalPolicy"] == "untrusted" and start["ephemeral"] is False
    assert start["sandbox"] in ("danger-full-access", "workspace-write")
    assert "config" not in start, "no built-in may be switched off"
    decisions = [m["result"] for m in msgs if "result" in m and "method" not in m]
    assert decisions == [{"decision": "accept"}, {"decision": "acceptForSession"}]
    await stop_all(app)


async def test_codex_interrupt_uses_turn_interrupt(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_interrupt", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="codex")
        task = await _turn(c, s["id"])
        await until(lambda: (svc.live.get(s["id"]) and svc.live[s["id"]].driver
                             and svc.live[s["id"]].driver.turn_id))
        await c.post(f"/build/app/sessions/{s['id']}/interrupt")
        evs = frames((await asyncio.wait_for(task, 30)).text)
    assert evs[-1] == {**evs[-1], "type": "done", "stop": "interrupted"}
    interrupt = next(m for m in sent(log) if m.get("method") == "turn/interrupt")["params"]
    assert interrupt == {"threadId": "01a189c5-1571-76b2-b4fd-517728eb3018",
                         "turnId": "01a189c5-15d8-7793-b973-0ec056bfa6dd"}
    await stop_all(app)


@pytest.mark.parametrize("scenario,harness", [("claude_signin", "claude-code"), ("codex_signin", "codex")])
async def test_a_signed_out_harness_says_sign_in_with_the_press(app, work, tmp_path, monkeypatch, scenario, harness):
    use_fake(monkeypatch, scenario, tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work, harness=harness)
        evs = frames((await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t"})).text)
    errors = [e for e in evs if e["type"] == "error"]
    assert len(errors) == 1 and errors[0]["fix_kind"] == "sign_in"
    assert "Sign in" in errors[0]["fix"] and errors[0]["title"].endswith("isn't signed in")
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "failed"
    async with client(app) as c:
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    assert row["last_error"].endswith("isn't signed in")
    await stop_all(app)


# --------------------------------------------------------------------------- #
# patch
# --------------------------------------------------------------------------- #
async def test_patch_applies_mode_and_model_live_and_refuses_in_claudes_words(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "full"})
        assert r.status_code == 400, "Full access needs confirm"
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "edits", "title": "renamed"})
        assert r.status_code == 200 and r.json()["applied"] == {"mode": "now"}
        assert r.json()["session"]["mode"] == "edits" and r.json()["session"]["title"] == "renamed"
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"model": "haiku"})
        assert r.json()["applied"] == {"model": "now"}
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"model": "not-a-real-model-xyz"})
        assert r.status_code == 409 and "not found" in r.json()["detail"]
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["model"] == "haiku"
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"effort": "high"})
        assert r.json()["applied"] == {"effort": "next"}
    reqs = [m["request"] for m in sent(log) if m.get("type") == "control_request"]
    assert {"subtype": "set_permission_mode", "mode": "acceptEdits"} in reqs
    assert {"subtype": "set_model", "model": "haiku"} in reqs
    await stop_all(app)


# --------------------------------------------------------------------------- #
# archive, delete
# --------------------------------------------------------------------------- #
async def test_archive_stops_the_process_and_delete_keeps_events_in_the_trash(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        proc = svc.live[s["id"]].proc
        assert proc is not None and proc.alive
        await c.post(f"/build/app/sessions/{s['id']}/archive")
        assert not proc.alive and svc.live[s["id"]].proc is None
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t2"})
        assert r.status_code == 409 and "archived" in r.json()["detail"]
        r = await c.delete(f"/build/app/sessions/{s['id']}")
        assert r.status_code == 200
        moved = Path(r.json()["events_moved_to"])
        assert moved.is_dir() and (moved / "events.jsonl").is_file() and "trash" in moved.parts
        assert (await c.get(f"/build/app/sessions/{s['id']}")).status_code == 404
    await stop_all(app)


# --------------------------------------------------------------------------- #
# terminals
# --------------------------------------------------------------------------- #
async def test_open_terminal_types_the_harnesses_own_resume_line(app, work, tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import terminals as T

    launched = []

    def fake_launch(platform, body, *, accounts=None, command=None, claude_session_id=None, **kw):
        launched.append({"cli": body.cli, "cwd": body.cwd, "command": command, "sid": claude_session_id,
                         "accounts": accounts})
        return SimpleNamespace(id="term_1")

    monkeypatch.setattr(T, "launch_pane", fake_launch)
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.post(f"/build/app/sessions/{s['id']}/open-terminal")
        assert r.status_code == 409 and "no conversation yet" in r.json()["detail"]
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        sid = (await _view(app, s["id"]))["session"]["harness_session_id"]
        proc = svc.live[s["id"]].proc
        r = await c.post(f"/build/app/sessions/{s['id']}/open-terminal")
        assert r.json() == {"terminal_id": "term_1"}
        assert not proc.alive, "two processes on one session corrupt it"
    assert launched == [{"cli": "claude", "cwd": str(work), "command": f"claude --resume {sid}", "sid": sid,
                         "accounts": launched[0]["accounts"]}]
    # A codex thread: `codex resume <id>`; an id that is not a UUID is never typed.
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work),
                       harness_session_id="01a189c4-9316-7d30-b03e-8a6c95d7bacd")
    bad = store.create(app.state.platform.engine, harness="codex", folder=str(work),
                       harness_session_id="x; del *")
    async with client(app) as c:
        assert (await c.post(f"/build/app/sessions/{row.id}/open-terminal")).status_code == 200
        r = await c.post(f"/build/app/sessions/{bad.id}/open-terminal")
        assert r.status_code == 409
    assert launched[-1]["command"] == "codex resume 01a189c4-9316-7d30-b03e-8a6c95d7bacd"
    await stop_all(app)


async def test_open_terminal_is_refused_while_a_turn_runs(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        store.update(app.state.platform.engine, s["id"], harness_session_id="11111111-2222-3333-4444-555555555555")
        task = await _turn(c, s["id"])
        ask = (await wait_pending(c, s["id"]))[0]
        r = await c.post(f"/build/app/sessions/{s['id']}/open-terminal")
        assert r.status_code == 409 and "working" in r.json()["detail"]
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask["request_id"], "decision": "deny"})
        await asyncio.wait_for(task, 30)
    await stop_all(app)


class _Pane:
    def __init__(self, cwd, tail, cli="claude", sid="11111111-2222-3333-4444-555555555555", accounts=None):
        self.id = "pane1"
        self.cwd = str(cwd)
        self.shell = "pwsh.exe"
        self.agent_cli = cli
        self.resume_cli = None
        self.claude_session_id = sid
        self.accounts = accounts or {}
        self._tail = tail

    def output_tail(self):
        return self._tail


async def test_from_terminal_needs_the_harness_to_have_exited(app, work, monkeypatch):
    panes = {}
    monkeypatch.setattr(app.state.platform.terminals, "get", lambda pid: panes.get(pid))
    async with client(app) as c:
        assert (await c.post("/build/app/from-terminal/nope")).status_code == 404
        panes["p"] = _Pane(work, "PS C:\\work> ", cli=None)
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 409 and "not running Claude Code or Codex" in r.json()["detail"]
        panes["p"] = _Pane(work, "────────────────\n> fix it\n? for shortcuts")
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 409 and "/exit" in r.json()["detail"]
        panes["p"] = _Pane(work, "Bye!\nPS C:\\work> ",
                           accounts={"anthropic": {"id": "p1", "title": "Work", "source": "iron-proxy",
                                                   "home": "C:\\h1", "unset": []}})
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 201
        made = r.json()
    assert made["harness"] == "claude-code" and made["folder"] == str(work)
    assert made["harness_session_id"] == "11111111-2222-3333-4444-555555555555"
    assert made["account"] == "p1" and made["account_name"] == "Work"
    row = store.get(app.state.platform.engine, made["id"])
    assert store.account_record(row)["home"] == "C:\\h1"


# --------------------------------------------------------------------------- #
# the bell
# --------------------------------------------------------------------------- #
async def test_a_waiting_session_nobody_follows_is_on_the_bell_names_only(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"])
        ask = (await wait_pending(c, s["id"]))[0]
        rows = (await c.get("/chat/approvals/pending")).json()["approvals"]
        assert not [r for r in rows if r.get("surface") == "build_app"], "a followed turn shows its own card"
        task.cancel()
        await asyncio.sleep(0.2)
        rows = [r for r in (await c.get("/chat/approvals/pending")).json()["approvals"]
                if r.get("surface") == "build_app"]
        assert rows == [{"id": f"build_app:{s['id']}", "kind": "chat", "thread_id": s["id"], "tool": "Write",
                         "requested_at": rows[0]["requested_at"], "mission_id": None, "project_id": None,
                         "surface": "build_app", "app_session": s["id"], "harness": "claude-code"}]
        assert rows[0]["requested_at"]
        assert "deny.txt" not in json.dumps(rows), "never arguments"
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask["request_id"], "decision": "deny"})
        rows = (await c.get("/chat/approvals/pending")).json()["approvals"]
        assert not [r for r in rows if r.get("surface") == "build_app"]
    await stop_all(app)


async def test_a_running_turn_is_named_and_its_rejoin_starts_with_the_question(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"], text="make deny.txt", turn_id="named")
        ask = (await wait_pending(c, s["id"]))[0]
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["running"] == "named"
        rejoin = asyncio.ensure_future(c.get(f"/build/app/sessions/{s['id']}/turns/named/stream"))
        await asyncio.sleep(0.2)
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask["request_id"], "decision": "deny"})
        evs = frames((await asyncio.wait_for(rejoin, 30)).text)
        assert evs[0] == {**evs[0], "type": "user", "text": "make deny.txt"}
        await asyncio.wait_for(task, 30)
    await stop_all(app)


async def test_skipping_a_question_tells_claude_it_was_not_answered(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_ask", log)
    async with client(app) as c:
        s = await new_session(c, work)
        task = await _turn(c, s["id"])
        q = (await wait_pending(c, s["id"]))[0]
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": q["request_id"], "decision": "deny"})
        await asyncio.wait_for(task, 30)
    got = [m["response"]["response"] for m in sent(log) if m.get("type") == "control_response"][0]
    assert got == {"behavior": "deny", "message": "The user chose not to answer."}
    await stop_all(app)


async def test_the_slash_list_is_the_harnesses_own_never_a_start_to_ask(app, work, tmp_path, monkeypatch):
    from iron_jarvis.hosted import claude as CL

    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        assert (await c.get(f"/build/app/sessions/{s['id']}/commands")).json() == {"commands": []}
        before = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}
        assert before["claude-code"]["commands"] == []
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        want = [{"name": "init", "description": "Set up a CLAUDE.md file"}]
        assert (await c.get(f"/build/app/sessions/{s['id']}/commands")).json() == {"commands": want}
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["commands"] == want
        after = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}
        assert after["claude-code"]["commands"] == want and after["codex"]["commands"] == []
        assert (await c.get("/build/app/sessions/nope/commands")).status_code == 404
    await stop_all(app)


async def test_terminal_rows_say_can_open_in_code_only_when_the_cli_exited(app, work, monkeypatch):
    exited = _Pane(work, "Bye!\nPS C:\\work> ")
    running = _Pane(work, "────────────────\n> fix it\n? for shortcuts")
    no_sid = _Pane(work, "Bye!\nPS C:\\work> ", sid=None)
    shell = _Pane(work, "PS C:\\work> ", cli=None)
    panes = {"a": exited, "b": running, "c": no_sid, "d": shell}
    infos = [{"id": k, "agent_cli": p.agent_cli, "state": "idle", "alive": True} for k, p in panes.items()]
    monkeypatch.setattr(app.state.platform.terminals, "get", lambda pid: panes.get(pid))
    monkeypatch.setattr(app.state.platform.terminals, "list", lambda: [dict(i) for i in infos])
    async with client(app) as c:
        listed = {r["id"]: r for r in (await c.get("/terminals")).json()["terminals"]}
        active = {r["id"]: r for r in (await c.get("/terminals/activity")).json()["panes"]}
    for rows in (listed, active):
        assert rows["a"].get("can_open_in_code") is True
        for k in ("b", "c", "d"):
            assert "can_open_in_code" not in rows[k], k


async def test_the_service_never_sends_a_session_allow_the_harness_did_not_offer(app, work):
    """The service's own guard (the Claude driver refuses too): a driver that
    would send anything is never handed an un-offered session-wide allow."""
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work))
    svc = S.service_for(app.state.platform)
    live = svc._live(row)
    told = []

    class _Driver:
        async def answer(self, ask, decision, message, answers):
            told.append(decision)
            return decision

    live.driver = _Driver()
    live.asks["ask_x"] = S.Ask("ask_x", "approval", "shell", native=1, can_session=False)
    with pytest.raises(S.Refused) as refused:
        await svc.answer(row.id, "ask_x", "allow_session")
    assert refused.value.status == 400 and told == []
    assert (await svc.answer(row.id, "ask_x", "allow"))["decision"] == "allow" and told == ["allow"]
