"""v1.441.0 review of lane g2 (Branch from here / Rewind to here in Build ›
Code). Each test pins one defect the review found, red on the builder's commit
(f7f9a7bf):

1. A Claude Code rewind not yet sent could be opened in a terminal, where a
   plain ``claude --resume H`` loads the OLD leaf (re-measured on 2.1.316 with
   a stand-in server: the model got the rewound-away message back).
2. What a rewind dropped came back later: the tool's own file still holds it
   (Claude Code keeps the old branch; Iron Jarvis's Codex reader does not read
   a revert), and the next time the conversation lives outside the view (a
   terminal, ``mark_outside``) the transcript was merged in again.
3. A message the tool never received (its send raised) was recorded as sent,
   so it was offered for a branch — and for Pi it would break the text match
   of every later branch.
4. A Pi fork that went through but whose new id could not be read left the
   process switched to the fork: the next message went into the wrong
   conversation.
5. A rewind cut the log even when the copy into the trash failed — and after
   the tool had already dropped the turns (Codex): nothing kept anywhere.
6. Stopping the tool for a branch or rewind ended a program it served (a dev
   server) with no word (v1.422.0 B4: a stop that ends programs is never
   silent).
"""

# ruff: noqa: F401, F811 — the lane file's fixtures are imported and used by name
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.hosted import branching as BR
from iron_jarvis.hosted import history as HIST
from iron_jarvis.hosted import programs as PG
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.driver import ControlRefused
from iron_jarvis.hosted.events import session_dir
from tests._build_app_helpers_v1409 import client, new_session, sent, stop_all
from tests.test_build_app_branch_v14410 import CLAUDE_FIX
from tests.test_build_app_branch_v14410 import (  # noqa: F401 — fixtures
    _PiStub,
    _StubLive,
    _claude_two_turns,
    _codex_scenario,
    _fake,
    _pi_session,
    _turn,
    _user_ats,
    app,
    claude_home,
    work,
)


def _claude_transcript_at(home: Path, hid: str, uuids: list[str], ats: list[str]) -> Path:
    """Claude Code's file, each record stamped with the log's own time (as
    the real tool stamps it a moment after Iron Jarvis does)."""
    path = home / "projects" / "C--work" / f"{hid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = None
    lines = []
    for i, (u, at) in enumerate(zip(uuids, ats)):
        lines.append({"type": "user", "uuid": u, "parentUuid": parent, "sessionId": hid, "timestamp": at,
                      "message": {"role": "user", "content": f"tool copy {i}"}})
        a = f"asst-{i}"
        lines.append({"type": "assistant", "uuid": a, "parentUuid": u, "sessionId": hid, "timestamp": at,
                      "message": {"role": "assistant", "content": [{"type": "text", "text": f"tool answer {i}"}]}})
        parent = a
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


# 1 ---------------------------------------------------------------------------------
async def test_a_claude_rewind_not_yet_sent_is_not_opened_in_a_terminal(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        ats = _user_ats((await c.get(f"/build/app/sessions/{sid}")).json())
        _claude_transcript_at(claude_home, hid, uuids, ats)
        assert (await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": ats[1]})).status_code == 200
        launched = []
        monkeypatch.setattr(S.service_for(app.state.platform), "_launch_terminal",
                            lambda row, h: launched.append(h))
        r = await c.post(f"/build/app/sessions/{sid}/open-terminal")
        assert r.status_code == 409, r.text
        assert "rewound" in r.json()["detail"] and not launched
    await stop_all(app)


# 2 ---------------------------------------------------------------------------------
async def test_what_a_rewind_dropped_never_comes_back_when_the_conversation_goes_outside(
        app, work, claude_home, tmp_path, monkeypatch):
    _fake(monkeypatch, CLAUDE_FIX, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await _turn(c, sid, "first one", "t1")
        # Past the merge's 2 s edge, so the dropped turn is not hidden inside
        # the kept turn's window (the pin would pass for the wrong reason).
        await asyncio.sleep(HIST.EDGE_S + 1.0)
        await _turn(c, sid, "second one", "t2")
        hid = store.get(app.state.platform.engine, sid).harness_session_id
        uuids = [m["uuid"] for m in sent(tmp_path / "fake.log") if m.get("type") == "user"]
        ats = _user_ats((await c.get(f"/build/app/sessions/{sid}")).json())
        _claude_transcript_at(claude_home, hid, uuids, ats)
        assert (await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": ats[1]})).status_code == 200
        home = S.service_for(app.state.platform).home
        # Later the conversation lives outside the view again (a terminal).
        HIST.mark_outside(home, sid, "terminal")
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        words = [str(e.get("text") or "") for e in view["events"]]
        assert "tool copy 1" not in words and "tool answer 1" not in words, words
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one"]
    await stop_all(app)


async def test_a_codex_rewind_to_the_first_message_shows_an_empty_conversation(app, work, tmp_path, monkeypatch):
    """The log is empty after it, so the view merges the tool's transcript
    (no ``outside.json`` needed): what Codex's file still holds from before the
    rewind is not shown again."""
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
        old = [{"type": "user", "at": at1, "text": "first one", "imported": True},
               {"type": "text_done", "at": at1, "item": "x1", "text": "old answer", "imported": True},
               {"type": "user", "at": at2, "text": "second one", "imported": True}]
        monkeypatch.setattr(HIST, "transcript_events", lambda *a, **kw: list(old))
        monkeypatch.setattr(S, "typable_sid", lambda h, s: True)
        assert (await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at1})).status_code == 200
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert view["events"] == [], view["events"]
    await stop_all(app)


# 3 ---------------------------------------------------------------------------------
async def test_a_message_the_tool_never_received_is_not_recorded_as_sent(
        app, work, claude_home, tmp_path, monkeypatch):
    from iron_jarvis.hosted.claude import ClaudeDriver
    from iron_jarvis.hosted.process import HarnessGone

    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)

        async def broken(self, text, files):
            raise HarnessGone("the pipe broke")

        monkeypatch.setattr(ClaudeDriver, "send_user", broken)
        await _turn(c, sid, "never arrives", "t9")
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        ats = _user_ats(view)
        assert len(ats) == 3
        refs = BR.read_refs(S.service_for(app.state.platform).home, sid)
        assert ats[2] not in refs and ats[2] not in view["branch"]["at"]
    await stop_all(app)


# 4 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("action", ["fork", "rewind"])
async def test_a_pi_fork_whose_new_id_cannot_be_read_still_stops_the_switched_process(
        app, work, monkeypatch, action):
    svc, sid, ats = await _pi_session(app, work)

    class _Broken(_PiStub):
        async def request(self, kind, _timeout=30.0, **fields):
            if kind == "get_state":
                raise ControlRefused("pi went quiet")
            return await super().request(kind, _timeout, **fields)

    drv = _Broken([{"entryId": "e1", "text": "first one"}, {"entryId": "e2", "text": "second one"}])
    stopped = []

    async def running(svc_, row, live):
        return _StubLive(drv), drv

    async def stop(live, **kw):
        stopped.append(kw.get("why"))

    monkeypatch.setattr(BR, "_running_driver", running)
    monkeypatch.setattr(svc, "_stop_process", stop)
    body = {"at": ats[1]} if action == "fork" else {"to": ats[1]}
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{sid}/{action}", json=body)
        assert r.status_code == 409, r.text
    assert drv.forked and stopped, "Pi switched to the fork and kept running it"
    assert store.get(app.state.platform.engine, sid).harness_session_id == "pi-old"


# 5 ---------------------------------------------------------------------------------
async def test_a_rewind_whose_log_cannot_be_kept_changes_nothing(app, work, tmp_path, monkeypatch):
    scen = _codex_scenario(tmp_path / "codex.json", [
        {"await": "request", "method": "thread/revert"},
        {"out": {"id": 0, "result": {"thread": {"id": "th-1"}}}, "reply_to": "thread/revert"},
    ])
    _fake(monkeypatch, scen, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work, harness="codex"))["id"]
        await _turn(c, sid, "first one", "t1")
        await _turn(c, sid, "second one", "t2")
        before = (await c.get(f"/build/app/sessions/{sid}")).json()
        at2 = _user_ats(before)[1]
        monkeypatch.setattr(BR, "_trash_log", lambda home, s: "")
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})
        assert r.status_code == 409, r.text
        assert not any(m.get("method") == "thread/revert" for m in sent(tmp_path / "fake.log"))
        after = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert _user_ats(after) == _user_ats(before)
    await stop_all(app)


# 6 ---------------------------------------------------------------------------------
async def test_a_branch_that_stops_the_tool_names_the_program_it_served(app, work, tmp_path, monkeypatch):
    scen = _codex_scenario(tmp_path / "codex.json", [
        {"await": "request", "method": "thread/fork"},
        {"out": {"id": 0, "result": {"thread": {"id": "th-fork", "turns": []}}}, "reply_to": "thread/fork"},
        {"await": "request", "method": "thread/unsubscribe"},
        {"out": {"id": 0, "result": {"status": "unsubscribed"}}, "reply_to": "thread/unsubscribe"},
    ])
    _fake(monkeypatch, scen, tmp_path / "fake.log")
    dev = PG.Program(pid=4242, created=0.0, ports=(3000,), command="npm run dev")

    async def reading(lives):
        return {lv.sid: PG.Reading(known=True, programs=[dev]) for lv in lives}

    async with client(app) as c:
        sid = (await new_session(c, work, harness="codex"))["id"]
        await _turn(c, sid, "first one", "t1")
        await _turn(c, sid, "second one", "t2")
        monkeypatch.setattr(PG, "read_many", reading)
        r = await c.post(f"/build/app/sessions/{sid}/fork", json={})
        assert r.status_code == 201, r.text
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        notices = [e["text"] for e in view["events"] if e["type"] == "notice"]
        assert any("npm run dev" in n and "branch" in n for n in notices), notices
    await stop_all(app)


# 7 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("action", ["fork", "rewind"])
@pytest.mark.parametrize("exc", ["timeout", "gone"])
async def test_a_tool_that_does_not_answer_is_said_in_words_never_a_500(app, work, monkeypatch, action, exc):
    from iron_jarvis.hosted.process import HarnessGone

    row = store.create(app.state.platform.engine, harness="codex", folder=str(work), harness_session_id="th-3")
    home = S.service_for(app.state.platform).home
    svc = S.service_for(app.state.platform)
    from iron_jarvis.hosted.events import make

    live = svc._live(row)
    ev = make("user", text="mine")
    live.log.append(ev)
    await asyncio.to_thread(live.log.flush)
    BR.note_sent(home, row.id, ev["at"], "turn-1", "th-3", "mine")

    class _Quiet:
        async def request(self, method, params=None):
            raise TimeoutError() if exc == "timeout" else HarnessGone("the coding tool stopped")

    async def running(svc_, row_, live_):
        return _StubLive(_Quiet()), _Quiet()

    async def stop(live_, **kw):
        return None

    monkeypatch.setattr(BR, "_running_driver", running)
    monkeypatch.setattr(svc, "_stop_process", stop)
    body = {"at": ev["at"]} if action == "fork" else {"to": ev["at"]}
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/{action}", json=body)
    assert r.status_code == 409, r.text
    assert "internal error" not in r.json()["detail"]
    assert svc.live[row.id].claim == ""


async def test_a_branch_of_a_rewound_session_never_merges_what_the_rewind_dropped(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids = await _claude_two_turns(app, c, work, monkeypatch, tmp_path)
        ats = _user_ats((await c.get(f"/build/app/sessions/{sid}")).json())
        _claude_transcript_at(claude_home, hid, uuids, ats)
        assert (await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": ats[1]})).status_code == 200
        r = await c.post(f"/build/app/sessions/{sid}/fork", json={})
        assert r.status_code == 201, r.text
        home = S.service_for(app.state.platform).home
        new_id = r.json()["session"]["id"]
        assert BR.rewound_at(home, sid) and BR.rewound_at(home, new_id) == BR.rewound_at(home, sid)
    await stop_all(app)
