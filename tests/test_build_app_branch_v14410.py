"""v1.441.0 — Build › Code's Branch from here and Rewind to here
(``hosted/branching.py``, the routes ``POST /build/app/sessions/{id}/fork``
and ``…/rewind``). Every flow runs through the real routes, service, store and
parser; Claude Code and Codex are the recorded fake (``fake_harness_v1409``);
Pi's and OpenCode's RPC answers are stub drivers built from what Pi 1.3.0 and
OpenCode 1.18.18 answered on this PC (the module docstring says how)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import branching as BR
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.claude import ClaudeDriver
from iron_jarvis.hosted.events import session_dir
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all

CLAUDE_FIX = Path(__file__).parent / "fixtures" / "build_app_v1409" / "claude_resume.json"


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def claude_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _fake(monkeypatch, scenario: Path, log: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


async def _turn(c, sid: str, text: str, turn: str) -> list[dict]:
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn})
    assert r.status_code == 200, r.text
    return frames(r.text)


def _user_ats(view: dict) -> list[str]:
    return [e["at"] for e in view["events"] if e["type"] == "user"]


def _claude_transcript(home: Path, hid: str, uuids: list[str]) -> Path:
    """The transcript Claude Code would have written: each sent message's
    record keeps the uuid it was sent with (measured), an answer after it."""
    path = home / "projects" / "C--work" / f"{hid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = None
    lines = []
    for i, u in enumerate(uuids):
        lines.append({"type": "user", "uuid": u, "parentUuid": parent, "sessionId": hid,
                      "timestamp": f"2026-11-06T10:00:0{i}Z", "message": {"role": "user", "content": f"m{i}"}})
        a = f"asst-{i}"
        lines.append({"type": "assistant", "uuid": a, "parentUuid": u, "sessionId": hid,
                      "timestamp": f"2026-11-06T10:00:0{i}Z",
                      "message": {"role": "assistant", "content": [{"type": "text", "text": f"r{i}"}]}})
        parent = a
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


async def _claude_two_turns(app, c, work, monkeypatch, tmp_path) -> tuple[str, str, list[str]]:
    _fake(monkeypatch, CLAUDE_FIX, tmp_path / "fake.log")
    sid = (await new_session(c, work))["id"]
    await _turn(c, sid, "first one", "t1")
    await _turn(c, sid, "second one", "t2")
    row = store.get(app.state.platform.engine, sid)
    uuids = [m["uuid"] for m in sent(tmp_path / "fake.log") if m.get("type") == "user"]
    return sid, row.harness_session_id, uuids


# -- Claude Code -----------------------------------------------------------------
async def test_each_message_sent_keeps_the_tools_own_reference(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
    refs = BR.read_refs(S.service_for(app.state.platform).home, sid)
    ats = _user_ats(view)
    assert len(ats) == 2 and [refs[a]["ref"] for a in ats] == uuids
    assert all(refs[a]["hid"] == hid for a in ats)
    assert view["branch"] == {"fork": True, "fork_at": True, "rewind": True, "at": ats}
    await stop_all(app)


async def test_claude_branch_from_a_message_makes_a_new_session_with_the_earlier_conversation(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        before = (await c.get(f"/build/app/sessions/{sid}")).json()
        at2 = _user_ats(before)[1]
        r = await c.post(f"/build/app/sessions/{sid}/fork", json={"at": at2})
        assert r.status_code == 201, r.text
        got = r.json()
        new = got["session"]
        assert got["text"] == "second one" and not got["from_end"]
        assert new["id"] != sid and new["folder"] == str(work) and new["harness"] == "claude-code"
        assert new["harness_session_id"] and new["harness_session_id"] != hid
        home = S.service_for(app.state.platform).home
        assert BR.read_pending(home, new["id"]) == {"from": hid, "at": "asst-0", "fork": True}
        branched = (await c.get(f"/build/app/sessions/{new['id']}")).json()
        assert [e["text"] for e in branched["events"] if e["type"] == "user"] == ["first one"]
        # The original is as it was.
        after = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert after["events"] == before["events"]
        # Its first message starts Claude Code's own fork at that entry.
        await _turn(c, new["id"], "edited second", "t3")
        argv = starts(tmp_path / "fake.log")[-1]["argv"]
        i = argv.index("--resume")
        assert argv[i:i + 5] == ["--resume", hid, "--fork-session", "--session-id", new["harness_session_id"]]
        assert argv[argv.index("--resume-session-at") + 1] == "asst-0"
    await stop_all(app)


async def test_claude_rewind_cuts_the_log_and_goes_back_with_the_next_message(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        before = (await c.get(f"/build/app/sessions/{sid}")).json()
        at1, at2 = _user_ats(before)
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["text"] == "second one"
        assert "not changed" in got["note"] and "Revert" in got["note"]
        home = S.service_for(app.state.platform).home
        assert BR.read_pending(home, sid) == {"from": "", "at": "asst-0", "fork": False}
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        # The log and the view agree: everything from that message on is gone.
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one"]
        cut_index = next(i for i, e in enumerate(before["events"]) if e.get("at") == at2)
        assert [e["type"] for e in view["events"]] == [e["type"] for e in before["events"][:cut_index]]
        assert view["branch"]["at"] == [at1]
        assert list(BR.read_refs(home, sid)) == [at1]
        # The whole old log is kept in Iron Jarvis's trash.
        assert list(Path(home, "trash").glob("*/hosted-rewind/" + sid + "/events.jsonl"))
        await _turn(c, sid, "again", "t3")
        argv = starts(tmp_path / "fake.log")[-1]["argv"]
        assert argv[argv.index("--resume") + 1] == hid and "--fork-session" not in argv
        assert argv[argv.index("--resume-session-at") + 1] == "asst-0"
    await stop_all(app)


def test_a_claude_branch_settles_once_its_message_is_in_the_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ch = tmp_path / "ch"
    ch.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ch))
    row = type("R", (), {"id": "app_1", "harness": "claude-code", "harness_session_id": "N1", "folder": "",
                         "account_json": "", "account": ""})()
    BR.write_pending(home, "app_1", {"from": "H", "at": "x", "fork": True})
    path = _claude_transcript(ch, "N1", ["u-old"])
    assert not BR.settle_pending(home, row, "u-new")
    assert BR.read_pending(home, "app_1")
    path.write_text(path.read_text() + json.dumps({"type": "user", "uuid": "u-new"}) + "\n")
    assert BR.settle_pending(home, row, "u-new")
    assert BR.read_pending(home, "app_1") == {}


def test_claude_argv_carries_a_pending_branch_and_rewind():
    d = ClaudeDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                     mode="ask", model="", effort="", cwd="")
    assert d.argv(session_id="H", resume=True)[-2:] == ["--resume", "H"]
    d.fork_from, d.resume_at = "H", "e1"
    out = d.argv(session_id="N", resume=True)
    assert out[-7:] == ["--resume", "H", "--fork-session", "--session-id", "N", "--resume-session-at", "e1"]
    d.fork_from, d.resume_at = "", "e2"
    assert d.argv(session_id="H", resume=True)[-4:] == ["--resume", "H", "--resume-session-at", "e2"]
    # A fresh conversation never carries an entry to resume at.
    assert "--resume-session-at" not in d.argv(session_id="F", resume=False)


async def test_a_pending_claude_branch_cannot_open_in_a_terminal_or_branch_again(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        new = (await c.post(f"/build/app/sessions/{sid}/fork", json={})).json()["session"]
        r = await c.post(f"/build/app/sessions/{new['id']}/open-terminal")
        assert r.status_code == 409 and "next message" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{new['id']}/fork", json={})
        assert r.status_code == 409 and "first" in r.json()["detail"]
        view = (await c.get(f"/build/app/sessions/{new['id']}")).json()
        assert view["branch"]["fork"] is False and view["branch"]["at"] == []
        # The whole conversation came along; Claude Code forks it from the end.
        assert BR.read_pending(S.service_for(app.state.platform).home, new["id"]) == {"from": hid, "at": "", "fork": True}
        assert len(_user_ats(view)) == 2
    await stop_all(app)


async def test_branch_and_rewind_are_refused_while_a_reply_runs(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        svc = S.service_for(app.state.platform)
        live = svc.live[sid]

        class _Running:
            running = True

        live.turn = _Running()
        try:
            for path, body in (("fork", {}), ("rewind", {"to": "x"})):
                r = await c.post(f"/build/app/sessions/{sid}/{path}", json=body)
                assert r.status_code == 409 and "reply is running" in r.json()["detail"]
        finally:
            live.turn = None
    await stop_all(app)


async def test_a_message_sent_before_this_version_offers_no_per_message_action(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        home = S.service_for(app.state.platform).home
        (session_dir(home, sid) / BR.REFS_NAME).unlink()
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert view["branch"]["at"] == [] and view["branch"]["fork"] is True
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": _user_ats(view)[1]})
        assert r.status_code == 409 and "did not record" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": "2001-01-01T00:00:00Z"})
        assert r.status_code == 404
    await stop_all(app)


# -- Codex -----------------------------------------------------------------------
def _codex_scenario(path: Path, extra: list[dict]) -> Path:
    def turn(tid: str) -> list[dict]:
        return [
            {"await": "request", "method": "turn/start"},
            {"out": {"id": 0, "result": {"turn": {"id": tid, "items": [], "status": "inProgress"}}},
             "reply_to": "turn/start"},
            {"out": {"method": "turn/started", "params": {"threadId": "th-1", "turn": {"id": tid, "status": "inProgress"}}}},
            {"out": {"method": "item/completed", "params": {"threadId": "th-1", "turnId": tid, "item": {
                "type": "agentMessage", "id": f"msg-{tid}", "text": f"reply {tid}", "phase": "final_answer"}}}},
            {"out": {"method": "turn/completed", "params": {"threadId": "th-1", "turn": {
                "id": tid, "items": [], "status": "completed", "error": None}}}},
        ]

    steps = [
        {"await": "request", "method": "initialize"},
        {"out": {"id": 0, "result": {"userAgent": "fake"}}, "reply_to": "initialize"},
        {"await": "request", "method": "thread/start"},
        {"out": {"id": 0, "result": {"thread": {"id": "th-1", "turns": []}, "model": "gpt-6"}}, "reply_to": "thread/start"},
        *turn("turn-a"),
        *turn("turn-b"),
        *extra,
    ]
    path.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    return path


async def test_codex_branch_before_a_turn_uses_codexs_own_fork_and_lets_go_of_it(app, work, tmp_path, monkeypatch):
    scen = _codex_scenario(tmp_path / "codex.json", [
        {"await": "request", "method": "thread/fork"},
        {"out": {"id": 0, "result": {"thread": {"id": "th-fork", "turns": []}}}, "reply_to": "thread/fork"},
        {"await": "request", "method": "thread/unsubscribe"},
        {"out": {"id": 0, "result": {"status": "unsubscribed"}}, "reply_to": "thread/unsubscribe"},
    ])
    _fake(monkeypatch, scen, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work, harness="codex"))["id"]
        await _turn(c, sid, "first one", "t1")
        await _turn(c, sid, "second one", "t2")
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        at1, at2 = _user_ats(view)
        home = S.service_for(app.state.platform).home
        refs = BR.read_refs(home, sid)
        assert [refs[at1]["ref"], refs[at2]["ref"]] == ["turn-a", "turn-b"]
        r = await c.post(f"/build/app/sessions/{sid}/fork", json={"at": at2})
        assert r.status_code == 201, r.text
        new = r.json()["session"]
        assert new["harness_session_id"] == "th-fork" and new["harness"] == "codex"
        fork_req = next(m for m in sent(tmp_path / "fake.log") if m.get("method") == "thread/fork")
        assert fork_req["params"]["threadId"] == "th-1" and fork_req["params"]["beforeTurnId"] == "turn-b"
        assert fork_req["params"]["excludeTurns"] is True
        # Measured: the app-server that made the fork stays its writer, so it goes.
        assert not S.service_for(app.state.platform).live[sid].running
        # The branch's own references point at the new thread (Codex keeps turn ids).
        assert BR.read_refs(home, new["id"])[at1] == {**refs[at1], "hid": "th-fork"}
    await stop_all(app)


async def test_codex_rewind_uses_codexs_own_revert(app, work, tmp_path, monkeypatch):
    scen = _codex_scenario(tmp_path / "codex.json", [
        {"await": "request", "method": "thread/revert"},
        {"out": {"id": 0, "result": {"thread": {"id": "th-1"}}}, "reply_to": "thread/revert"},
    ])
    _fake(monkeypatch, scen, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work, harness="codex"))["id"]
        await _turn(c, sid, "first one", "t1")
        await _turn(c, sid, "second one", "t2")
        at1, at2 = _user_ats((await c.get(f"/build/app/sessions/{sid}")).json())
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})
        assert r.status_code == 200, r.text
        rev = next(m for m in sent(tmp_path / "fake.log") if m.get("method") == "thread/revert")
        assert rev["params"] == {"threadId": "th-1", "beforeTurnId": "turn-b"}
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one"]
        assert store.get(app.state.platform.engine, sid).harness_session_id == "th-1"
    await stop_all(app)


# -- Pi and OpenCode (stub drivers with the answers measured live) --------------------
class _StubLive:
    def __init__(self, driver):
        self.driver = driver
        self.running = True


class _PiStub:
    def __init__(self, messages, new_id="pi-new"):
        self.messages = messages
        self.new_id = new_id
        self.calls: list[tuple[str, dict]] = []
        self.forked = False

    async def request(self, kind, _timeout=30.0, **fields):
        self.calls.append((kind, fields))
        if kind == "get_fork_messages":
            return {"messages": self.messages}
        if kind in ("fork", "clone"):
            self.forked = True
            return {"text": "x", "cancelled": False}
        if kind == "get_state":
            return {"sessionId": self.new_id if self.forked else "pi-old"}
        raise AssertionError(kind)


async def _pi_session(app, work) -> tuple[Any, str, list[str]]:
    engine = app.state.platform.engine
    home = S.service_for(app.state.platform).home
    row = store.create(engine, harness="pi", folder=str(work), harness_session_id="pi-old", mode="own")
    from iron_jarvis.hosted.events import make
    svc = S.service_for(app.state.platform)
    live = svc._live(row)
    ats = []
    for text in ("first one", "second one"):
        ev = make("user", text=text)
        live.log.append(ev)
        live.log.append(make("text_done", item=f"t-{text}", text="ok"))
        ats.append(ev["at"])
        BR.note_sent(home, row.id, ev["at"], "", "pi-old", text)
        await asyncio.sleep(0.02)
    await asyncio.to_thread(live.log.flush)
    return svc, row.id, ats


async def test_pi_branch_matches_its_own_list_forks_and_leaves_the_original(app, work, monkeypatch):
    svc, sid, ats = await _pi_session(app, work)
    drv = _PiStub([{"entryId": "e0", "text": "older, before this version"},
                   {"entryId": "e1", "text": "first one"}, {"entryId": "e2", "text": "second one"}])
    stopped = []

    async def running(svc_, row, live):
        return _StubLive(drv), drv

    async def stop(live, **kw):
        stopped.append(kw.get("why"))

    monkeypatch.setattr(BR, "_running_driver", running)
    monkeypatch.setattr(svc, "_stop_process", stop)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{sid}/fork", json={"at": ats[1]})
        assert r.status_code == 201, r.text
        assert ("fork", {"entryId": "e2"}) in drv.calls
        assert r.json()["session"]["harness_session_id"] == "pi-new"
        # Pi switched its process to the fork: it goes, the original reopens next.
        assert stopped == ["branched"]
        assert store.get(app.state.platform.engine, sid).harness_session_id == "pi-old"


async def test_pi_refuses_when_its_list_does_not_match(app, work, monkeypatch):
    svc, sid, ats = await _pi_session(app, work)
    drv = _PiStub([{"entryId": "e1", "text": "first one"}, {"entryId": "e2", "text": "something else"}])

    async def running(svc_, row, live):
        return _StubLive(drv), drv

    monkeypatch.setattr(BR, "_running_driver", running)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": ats[1]})
        assert r.status_code == 409 and "does not match" in r.json()["detail"]
    assert not any(k == "fork" for k, _ in drv.calls)
    assert store.get(app.state.platform.engine, sid).harness_session_id == "pi-old"


async def test_pi_rewind_moves_the_session_to_its_fork(app, work, monkeypatch):
    svc, sid, ats = await _pi_session(app, work)
    drv = _PiStub([{"entryId": "e1", "text": "first one"}, {"entryId": "e2", "text": "second one"}])

    async def running(svc_, row, live):
        return _StubLive(drv), drv

    async def stop(live, **kw):
        return None

    monkeypatch.setattr(BR, "_running_driver", running)
    monkeypatch.setattr(svc, "_stop_process", stop)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": ats[1]})
        assert r.status_code == 200, r.text
        assert "Pi kept" in r.json()["note"]
        assert store.get(app.state.platform.engine, sid).harness_session_id == "pi-new"
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one"]
        assert BR.read_refs(S.service_for(app.state.platform).home, sid)[ats[0]]["hid"] == "pi-new"


async def test_opencode_branches_the_whole_conversation_and_says_so(app, work, monkeypatch):
    engine = app.state.platform.engine
    row = store.create(engine, harness="opencode", folder=str(work), harness_session_id="ses_old", mode="ask")
    S.service_for(app.state.platform)
    calls = []

    class _OC:
        mcp_servers: list = []
        _loading = False

        async def request(self, method, params=None, timeout=None):
            calls.append((method, params, self._loading))
            if method == "session/fork":
                return {"sessionId": "ses_new"}
            return {}

    drv = _OC()

    async def running(svc_, row_, live):
        return _StubLive(drv), drv

    monkeypatch.setattr(BR, "_running_driver", running)
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert view["branch"] == {"fork": True, "fork_at": False, "rewind": False, "at": []}
        r = await c.post(f"/build/app/sessions/{row.id}/fork", json={"at": "anything"})
        assert r.status_code == 201, r.text
        assert "whole conversation" in r.json()["note"]
        assert calls[0] == ("session/fork", {"sessionId": "ses_old", "cwd": str(work), "mcpServers": []}, True)
        assert calls[1][0] == "session/close" and calls[1][1] == {"sessionId": "ses_new"}
        r = await c.post(f"/build/app/sessions/{row.id}/rewind", json={"to": "x"})
        assert r.status_code == 409 and r.json()["code"] == "not_offered"


@pytest.mark.parametrize("harness", ["grok", "antigravity"])
async def test_tools_with_no_fork_offer_neither(app, work, harness):
    row = store.create(app.state.platform.engine, harness=harness, folder=str(work), harness_session_id="x-1")
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert view["branch"] == {"fork": False, "fork_at": False, "rewind": False, "at": []}
        listed = (await c.get("/build/app/sessions")).json()["sessions"]
        assert next(s for s in listed if s["id"] == row.id)["can_branch"] is False
        for path, body in (("fork", {}), ("rewind", {"to": "x"})):
            r = await c.post(f"/build/app/sessions/{row.id}/{path}", json=body)
            assert r.status_code == 409 and r.json()["code"] == "not_offered"


async def test_a_conversation_open_in_a_terminal_is_not_branched(app, work, monkeypatch):
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work), harness_session_id="th-9")
    svc = S.service_for(app.state.platform)
    monkeypatch.setattr(svc, "_in_a_terminal", lambda r: True)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/fork", json={})
        assert r.status_code == 409 and "open in a terminal" in r.json()["detail"]


async def test_rewind_of_a_session_with_merged_history_writes_it_into_the_log(app, work, monkeypatch, tmp_path):
    """The view showed the tool's own transcript (a conversation opened from
    disk): after a rewind the log holds what was shown before the message and
    the transcript is not merged again — nothing rewound comes back."""
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work), harness_session_id="th-7")
    home = S.service_for(app.state.platform).home
    from iron_jarvis.hosted import history as HIST
    from iron_jarvis.hosted.events import make

    HIST.mark_outside(home, row.id, "disk")
    svc = S.service_for(app.state.platform)
    live = svc._live(row)
    mine = make("user", text="mine")
    live.log.append(mine)
    await asyncio.to_thread(live.log.flush)
    BR.note_sent(home, row.id, mine["at"], "turn-9", "th-7", "mine")
    old = [{"type": "user", "at": "2026-01-01T00:00:00Z", "text": "from disk", "imported": True},
           {"type": "text_done", "at": "2026-01-01T00:00:01Z", "item": "d1", "text": "old answer", "imported": True}]

    async def with_transcript(row_, live_, events, *, cut):
        return old + list(events)

    monkeypatch.setattr(svc, "_with_transcript", with_transcript)

    class _CX:
        async def request(self, method, params=None):
            return {"thread": {"id": "th-7"}}

    async def running(svc_, row_, live_):
        return _StubLive(_CX()), _CX()

    async def stop(live_, **kw):
        return None

    monkeypatch.setattr(BR, "_running_driver", running)
    monkeypatch.setattr(svc, "_stop_process", stop)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/rewind", json={"to": mine["at"]})
        assert r.status_code == 200, r.text
    assert not HIST.went_outside(home, row.id)
    events = HIST._read_events(session_dir(home, row.id) / "events.jsonl")
    assert [e["text"] for e in events] == ["from disk", "old answer"]


async def test_a_branch_of_a_rewound_claude_session_ends_where_the_screen_does(
        app, work, claude_home, tmp_path, monkeypatch):
    """Claude Code still follows the OLD leaf until the next message after a
    rewind: a branch from the end in between must end at the rewind's entry."""
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        at2 = _user_ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        assert (await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})).status_code == 200
        r = await c.post(f"/build/app/sessions/{sid}/fork", json={})
        assert r.status_code == 201, r.text
        new = r.json()["session"]
        assert BR.read_pending(S.service_for(app.state.platform).home, new["id"]) == {
            "from": hid, "at": "asst-0", "fork": True}
        assert BR.FORK_FIRST_NOTE not in r.json()["note"]
    await stop_all(app)


async def test_a_reply_ends_only_after_its_messages_reference_is_on_disk(app, work, claude_home, tmp_path, monkeypatch):
    """A fast reply used to end before the reference was written (seen under
    load): a rewind right after it was refused "did not record"."""
    import time as _time

    slow_note = BR.note_sent

    def slow(*a, **kw):
        _time.sleep(0.6)
        return slow_note(*a, **kw)

    monkeypatch.setattr(BR, "note_sent", slow)
    _fake(monkeypatch, CLAUDE_FIX, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await _turn(c, sid, "first one", "t1")
        ats = _user_ats((await c.get(f"/build/app/sessions/{sid}")).json())
        assert list(BR.read_refs(S.service_for(app.state.platform).home, sid)) == ats
    await stop_all(app)
