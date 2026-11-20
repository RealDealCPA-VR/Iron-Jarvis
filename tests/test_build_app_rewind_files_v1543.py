"""v1.543.0 — Build › Code: "Rewind to here" can put the folder's FILES back
(``hosted/branching.py`` over ``hosted/checkpoints.py``; the routes
``GET …/rewind/preview``, ``POST …/rewind {files}``, ``…/rewind/files`` and
``…/rewind/undo``).

Every flow runs through the real routes, service, store and REAL git in a
scratch repository. Claude Code and Codex are the recorded fake
(``fake_harness_v1409``); the test itself writes the files a coding tool would
have written between the messages.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import branching as BR
from iron_jarvis.daemon.routes import build_git as BG
from iron_jarvis.hosted import checkpoints as CK
from iron_jarvis.hosted import handoff as HO
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, stop_all
from tests.test_build_app_branch_v14410 import _claude_transcript, _codex_scenario

CLAUDE_FIX = Path(__file__).parent / "fixtures" / "build_app_v1409" / "claude_resume.json"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(cwd: Path, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00+00:00"
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, env=env).stdout.decode()


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    _git(folder, "config", "core.autocrlf", "false")  # the runners' global setting is true
    (folder / "a.py").write_bytes(b"a = 0\n")
    (folder / "keep.py").write_bytes(b"keep = 0\n")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first")
    return folder


@pytest.fixture(autouse=True)
def _a_checkpoint_for_every_message(monkeypatch):
    """A message waits at most 3 s for its checkpoint; on a loaded PC it then
    simply has none (the product's own limit, said in the Handbook). These
    tests are about what happens WITH a checkpoint, so they wait for it: run
    beside other git-heavy tests the 3 s were missed and a test found no
    checkpoint to rewind to."""
    real = PN.record_turn_start_async

    async def patient(home, sid, folder, turn_id, *, bound=120.0, uid=""):
        return await real(home, sid, folder, turn_id, bound=120.0, uid=uid)

    monkeypatch.setattr(PN, "record_turn_start_async", patient)


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


def _files(folder: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_file() and not rel.startswith(".git/"):
            out[rel] = p.read_bytes()
    return out


def _refs(folder: Path) -> list[str]:
    return [ln for ln in _git(folder, "for-each-ref", "--format=%(refname)", "refs/ironjarvis/").splitlines() if ln]


def _first_message_work(work: Path) -> None:
    (work / "a.py").write_bytes(b"a = 1\n")
    (work / "one.py").write_bytes(b"one\n")


def _second_message_work(work: Path) -> None:
    (work / "a.py").write_bytes(b"a = 2\n")
    (work / "one.py").unlink()
    (work / "two.py").write_bytes(b"two\n")


async def _claude_two_messages(app, c, work, monkeypatch, tmp_path) -> tuple[str, str, list[str], dict[str, bytes]]:
    """Two messages, each followed by the tool's file work. Returns the
    folder as it was when the SECOND message started."""
    _fake(monkeypatch, CLAUDE_FIX, tmp_path / "fake.log")
    sid = (await new_session(c, work))["id"]
    await _turn(c, sid, "first one", "t1")
    _first_message_work(work)
    at_second = _files(work)
    await _turn(c, sid, "second one", "t2")
    _second_message_work(work)
    row = store.get(app.state.platform.engine, sid)
    uuids = [m["uuid"] for m in sent(tmp_path / "fake.log") if m.get("type") == "user"]
    return sid, row.harness_session_id, uuids, at_second


def _ats(view: dict) -> list[str]:
    return [e["at"] for e in view["events"] if e["type"] == "user"]


# -- the view and the preview ------------------------------------------------------
async def test_every_message_gets_a_kept_checkpoint_and_the_view_names_them(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, _hid, _uuids, _ = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        ats = _ats(view)
        assert view["branch"]["files_at"] == ats and view["branch"]["undo_rewind"] is False
        home = S.service_for(app.state.platform).home
        starts = [r for r in PN.read_trees(home, sid) if r["how"] == "start"]
        assert [r["uid"] for r in starts] == ats and [r["turn"] for r in starts] == ["t1", "t2"]
        assert set(r["ref"] for r in starts) <= set(_refs(work))
    await stop_all(app)


async def test_the_preview_lists_the_files_and_changes_nothing(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, _hid, _uuids, _ = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        as_is = _files(work)
        r = await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at2})
        assert r.status_code == 200, r.text
        got = r.json()
        assert sorted(got["back"]) == ["a.py", "one.py"] and got["new"] == ["two.py"]
        assert got["can"] is True and got["blocked"] == "" and got["conversation"] is True
        assert got["label"] == "Claude Code" and len(got["id"]) == 16 and "tree" not in got
        assert got["own"] is False  # v1.546.0: a git folder's checkpoints are in its own repository
        assert _files(work) == as_is
        r = await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": "no-such-message"})
        assert r.status_code == 404
    await stop_all(app)


# -- conversation and files --------------------------------------------------------
async def test_rewind_with_files_puts_the_folder_back_then_undo_returns_it(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids, at_second = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        at1, at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())
        before_rewind = _files(work)
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at2})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2, "files": True, "expect": plan["id"]})
        assert r.status_code == 200, r.text
        got = r.json()
        # The folder is as it was when the second message started, byte for byte.
        assert _files(work) == at_second
        assert got["restored"]["back"] == 2 and got["restored"]["moved"] == 1
        assert (Path(got["restored"]["trash"]) / "made-since" / "two.py").read_bytes() == b"two\n"
        assert (Path(got["restored"]["trash"]) / "replaced" / "a.py").read_bytes() == b"a = 2\n"
        assert got["text"] == "second one" and "2 put back" in got["note"]
        assert "not changed" not in got["note"]  # the old "your files were not changed" is not said
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one"]
        assert view["branch"]["undo_rewind"] is True
        # The dropped message's checkpoint went with it (and its hidden ref);
        # the copy of the folder from just before the rewind is kept for Undo.
        assert view["branch"]["files_at"] == [at1]
        home = S.service_for(app.state.platform).home
        rows = PN.read_trees(home, sid)
        assert [r["turn"] for r in rows if r["how"] == "start"] == ["t1"]
        assert sorted(_refs(work)) == sorted(r["ref"] for r in rows)
        assert [r["how"] for r in rows].count(CK.HOW_BEFORE) == 1
        # The conversation itself says what happened to the folder.
        assert any(e["type"] == "notice" and e["text"].startswith("Rewind: ") for e in view["events"])
        # Undo rewind: the folder is as it was just before the rewind.
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 200, r.text
        assert _files(work) == before_rewind
        assert r.json()["branch"]["undo_rewind"] is False
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 409 and r.json()["detail"] == CK.NOTHING_TO_UNDO
    await stop_all(app)


async def test_rewind_without_files_touches_nothing_in_the_folder(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids, _ = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        as_is = _files(work)
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})  # an older page's press
        assert r.status_code == 200, r.text
        assert _files(work) == as_is and "restored" not in r.json()
        assert BR.FILES_SAME in r.json()["note"]
        assert (await c.get(f"/build/app/sessions/{sid}")).json()["branch"]["undo_rewind"] is False
    await stop_all(app)


async def test_a_files_refusal_leaves_the_conversation_as_it_was(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids, _ = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        before = (await c.get(f"/build/app/sessions/{sid}")).json()
        at2 = _ats(before)[1]
        as_is = _files(work)
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2, "files": True, "expect": "stale-id"})
        assert r.status_code == 409 and r.json()["detail"] == CK.FOLDER_CHANGED
        after = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in after["events"] if e["type"] == "user"] == ["first one", "second one"]
        assert _files(work) == as_is
        assert BR.read_pending(S.service_for(app.state.platform).home, sid) == {}
        # The session is free again: the same rewind with the right list goes through.
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at2})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2, "files": True, "expect": plan["id"]})
        assert r.status_code == 200, r.text
    await stop_all(app)


async def test_when_the_tool_refuses_the_rewind_the_files_are_returned(app, work, tmp_path, monkeypatch):
    scen = _codex_scenario(tmp_path / "codex.json", [
        {"await": "request", "method": "thread/revert"},
        {"out": {"id": 0, "error": {"code": -32600, "message": "no such turn"}}, "reply_to": "thread/revert"},
    ])
    _fake(monkeypatch, scen, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work, harness="codex"))["id"]
        await _turn(c, sid, "first one", "t1")
        _first_message_work(work)
        await _turn(c, sid, "second one", "t2")
        _second_message_work(work)
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        as_is = _files(work)
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at2})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2, "files": True, "expect": plan["id"]})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert "Codex refused" in detail and detail.endswith(BR.FILES_BACK_AGAIN)
        # Codex was asked (so the files had already gone back), and they are returned.
        assert any(m.get("method") == "thread/revert" for m in sent(tmp_path / "fake.log"))
        assert _files(work) == as_is
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one", "second one"]
        assert view["branch"]["undo_rewind"] is False
    await stop_all(app)


async def test_codex_rewind_with_files_puts_the_folder_back(app, work, tmp_path, monkeypatch):
    scen = _codex_scenario(tmp_path / "codex.json", [
        {"await": "request", "method": "thread/revert"},
        {"out": {"id": 0, "result": {"thread": {"id": "th-1"}}}, "reply_to": "thread/revert"},
    ])
    _fake(monkeypatch, scen, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work, harness="codex"))["id"]
        await _turn(c, sid, "first one", "t1")
        _first_message_work(work)
        at_second = _files(work)
        await _turn(c, sid, "second one", "t2")
        _second_message_work(work)
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at2})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2, "files": True, "expect": plan["id"]})
        assert r.status_code == 200, r.text
        assert _files(work) == at_second
        rev = next(m for m in sent(tmp_path / "fake.log") if m.get("method") == "thread/revert")
        assert rev["params"] == {"threadId": "th-1", "beforeTurnId": "turn-b"}
    await stop_all(app)


# -- the files only (every tool) ----------------------------------------------------
async def _plain_session(app, work, harness: str) -> tuple[str, list[str], dict[str, bytes]]:
    """A session of a tool with no conversation rewind, two messages in its
    log, each with its checkpoint (what ``_drive`` records)."""
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness=harness, folder=str(work), harness_session_id="x-1")
    live = svc._live(row)
    ats: list[str] = []
    at_second: dict[str, bytes] = {}
    for n, text in enumerate(("first one", "second one"), start=1):
        if n == 2:
            at_second = _files(work)
        ev = make("user", text=text, turn=f"t{n}")
        live.log.append(ev)
        assert PN.record_turn_start(svc.home, row.id, str(work), f"t{n}", uid=ev["at"])
        live.log.append(make("text_done", item=f"i{n}", text="ok", turn=f"t{n}"))
        live.log.append(make("done", stop="completed", turn=f"t{n}"))
        ats.append(ev["at"])
        (_first_message_work if n == 1 else _second_message_work)(work)
        await asyncio.sleep(0.02)
    await asyncio.to_thread(live.log.flush)
    return row.id, ats, at_second


@pytest.mark.parametrize("harness", ["grok", "antigravity", "opencode"])
async def test_a_tool_with_no_conversation_rewind_still_gets_its_files_back(app, work, harness):
    sid, ats, at_second = await _plain_session(app, work, harness)
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert view["branch"]["rewind"] is False and view["branch"]["files_at"] == ats
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()
        assert plan["conversation"] is False and plan["can"] is True
        # Its conversation cannot be rewound, and that is still refused.
        r = await c.post(f"/build/app/sessions/{sid}/rewind",
                         json={"to": ats[1], "files": True, "expect": plan["id"]})
        assert r.status_code == 409 and r.json()["code"] == "not_offered"
        assert _files(work) != at_second
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 200, r.text
        got = r.json()
        assert _files(work) == at_second
        assert "The conversation was not changed" in got["note"] and got["branch"]["undo_rewind"] is True
        after = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in after["events"] if e["type"] == "user"] == ["first one", "second one"]
        assert any(e["type"] == "notice" and e["text"].startswith("Rewind: ") for e in after["events"])
    await stop_all(app)


async def test_the_files_wait_while_anything_still_works_in_the_folder(app, work, monkeypatch):
    sid, ats, _ = await _plain_session(app, work, "grok")
    svc = S.service_for(app.state.platform)
    as_is = _files(work)
    async with client(app) as c:
        # 1. the conversation is open in a terminal (the tool writes from there)
        monkeypatch.setattr(svc, "_in_a_terminal", lambda r: True)
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": "any"})
        assert r.status_code == 409 and "open in a terminal" in r.json()["detail"]
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()
        assert "open in a terminal" in plan["blocked"]  # the preview says why before the press
        monkeypatch.setattr(svc, "_in_a_terminal", lambda r: False)
        # 2. another session has a reply running in this same folder: refused
        #    at the FIRST look, before any lock is waited for (the second look,
        #    inside the lock, is pinned by its own test).
        waited: list[str] = []
        real_locked = BG._locked
        monkeypatch.setattr(BG, "_locked", lambda key, fn: (waited.append(key), real_locked(key, fn))[1])
        monkeypatch.setattr(BR, "_same_folder_running", lambda s, row: True)
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": "any"})
        assert r.status_code == 409 and r.json()["detail"] == BR.OTHER_SESSION
        assert waited == []
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 409 and r.json()["detail"] == BR.OTHER_SESSION
        monkeypatch.undo()
        # 3. the tool works by itself (no reply of the user's)
        live = svc._live(store.get(app.state.platform.engine, sid))
        monkeypatch.setattr(type(live), "working_alone", property(lambda self: True))
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": "any"})
        assert r.status_code == 409 and "still working in this folder" in r.json()["detail"]
        assert _files(work) == as_is
    await stop_all(app)


async def test_another_sessions_running_reply_in_the_same_folder_is_seen(app, work):
    svc = S.service_for(app.state.platform)
    engine = app.state.platform.engine
    mine = store.create(engine, harness="grok", folder=str(work), harness_session_id="x-1")
    inside = store.create(engine, harness="codex", folder=str(work / "sub"), harness_session_id="x-2")
    elsewhere = store.create(engine, harness="codex", folder=str(work.parent / "other"), harness_session_id="x-3")

    class _Turn:
        running = True
        ending = False

    assert BR._same_folder_running(svc, mine) is False
    svc._live(elsewhere).turn = _Turn()
    assert BR._same_folder_running(svc, mine) is False
    svc._live(inside).turn = _Turn()  # a folder inside this one
    assert BR._same_folder_running(svc, mine) is True
    assert BR._same_folder_running(svc, inside) is False  # its own reply is not "another"
    svc.live.clear()


async def test_a_message_with_no_checkpoint_says_so(app, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="grok", folder=str(plain), harness_session_id="x-1")
    live = svc._live(row)
    ev = make("user", text="hello", turn="t1")
    live.log.append(ev)
    await asyncio.to_thread(live.log.flush)
    async with client(app) as c:
        assert (await c.get(f"/build/app/sessions/{row.id}")).json()["branch"]["files_at"] == []
        r = await c.get(f"/build/app/sessions/{row.id}/rewind/preview", params={"to": ev["at"]})
        assert r.status_code == 409 and r.json()["detail"] == CK.NO_CHECKPOINT
        r = await c.post(f"/build/app/sessions/{row.id}/rewind/files", json={"to": ev["at"], "expect": "any"})
        assert r.status_code == 409 and r.json()["detail"] == CK.NO_CHECKPOINT
    await stop_all(app)


async def test_the_files_that_stand_in_the_way_come_back_with_the_refusal(app, work, monkeypatch):
    sid, ats, _ = await _plain_session(app, work, "grok")
    real = CK._writable
    monkeypatch.setattr(CK, "_writable", lambda p: False if p.endswith("a.py") else real(p))
    as_is = _files(work)
    async with client(app) as c:
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()
        assert plan["can"] is False and plan["problems"] == [{"rel": "a.py", "why": CK.WHY_LOCKED}]
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 409
        assert r.json() == {"detail": CK.PROBLEMS, "problems": [{"rel": "a.py", "why": CK.WHY_LOCKED}]}
        assert _files(work) == as_is
    await stop_all(app)


# -- deleting the session -----------------------------------------------------------
async def test_deleting_the_session_leaves_no_iron_jarvis_name_in_the_repository(app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, _hid, _uuids, _ = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        await c.get(f"/build/app/sessions/{sid}/changes")  # an idle read keeps a checkpoint too
        assert len(_refs(work)) >= 2
        as_is = _files(work)
        r = await c.delete(f"/build/app/sessions/{sid}")
        assert r.status_code == 200, r.text
        assert _refs(work) == []
        assert _files(work) == as_is  # the user's folder is not the session's to remove
        assert json.loads(json.dumps(r.json()))["deleted"] == sid
    await stop_all(app)


async def test_a_checkpoint_recorded_before_they_were_kept_is_not_offered(app, work):
    """A row with no hidden ref (recorded by an older version) may be gone
    from git: its message offers no file rewind."""
    sid, ats, _ = await _plain_session(app, work, "grok")
    home = S.service_for(app.state.platform).home
    path = PN._trees_path(home, sid)
    rows = json.loads(path.read_text(encoding="utf-8"))
    first = next(r for r in rows if r.get("turn") == "t1")
    first.pop("ref")
    path.write_text(json.dumps(rows), encoding="utf-8")
    async with client(app) as c:
        assert (await c.get(f"/build/app/sessions/{sid}")).json()["branch"]["files_at"] == [ats[1]]
    await stop_all(app)


# -- what the review of the first version found (routes and service) ----------------
async def test_the_files_go_back_only_from_a_list_that_was_read(app, work, claude_home, tmp_path, monkeypatch):
    """The preview's id is REQUIRED: without it the "folder changed since you
    read the list" check could be skipped."""
    async with client(app) as c:
        sid, hid, uuids, _ = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        as_is = _files(work)
        for path, body in (("rewind", {"to": at2, "files": True}), ("rewind/files", {"to": at2}),
                           ("rewind", {"to": at2, "files": True, "expect": "  "})):
            r = await c.post(f"/build/app/sessions/{sid}/{path}", json=body)
            assert r.status_code == 400 and r.json()["detail"] == BR.READ_FIRST, (path, r.text)
        assert _files(work) == as_is
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one", "second one"]
    await stop_all(app)


async def test_every_read_and_write_of_the_folder_holds_the_sessions_lock_and_the_folders(app, work, monkeypatch):
    sid, ats, _ = await _plain_session(app, work, "grok")
    held: list[str] = []
    seen: list[tuple[str, tuple[str, ...]]] = []
    real = BG._locked

    def locked(key, fn):
        held.append(key)
        try:
            return real(key, fn)
        finally:
            held.pop()

    monkeypatch.setattr(BG, "_locked", locked)
    for name in ("plan", "restore", "undo"):
        inner = getattr(CK, name)

        def watched(*a, _name=name, _inner=inner, **k):
            seen.append((_name, tuple(held)))
            return _inner(*a, **k)

        monkeypatch.setattr(CK, name, watched)
    # One lock per REPOSITORY: a session in a folder inside it shares it.
    folder_key = CK.lock_key(str(work))
    assert folder_key == "code-folder:" + os.path.normcase(str(work / ".git"))
    (work / "inner").mkdir()
    assert CK.lock_key(str(work / "inner")) == folder_key
    both = (f"app:{sid}", folder_key)
    async with client(app) as c:
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 200, r.text
        assert (await c.post(f"/build/app/sessions/{sid}/rewind/undo")).status_code == 200
    # ``restore`` reads the plan again itself, under the same locks.
    assert [n for n, _ in seen] == ["plan", "restore", "plan", "undo"]
    assert all(keys == both for _, keys in seen), seen
    await stop_all(app)


async def test_two_sessions_in_one_folder_share_the_folders_lock(app, work, monkeypatch):
    one, ats_one, _ = await _plain_session(app, work, "grok")
    row_two = store.create(app.state.platform.engine, harness="grok", folder=str(work), harness_session_id="x-2")
    keys: list[str] = []
    real = BG._locked
    monkeypatch.setattr(BG, "_locked", lambda key, fn: (keys.append(key), real(key, fn))[1])
    async with client(app) as c:
        await c.get(f"/build/app/sessions/{one}/rewind/preview", params={"to": ats_one[1]})
        await c.post(f"/build/app/sessions/{row_two.id}/rewind/undo")
    folder_keys = [k for k in keys if k.startswith("code-folder:")]
    assert len(folder_keys) == 2 and len(set(folder_keys)) == 1
    assert {k for k in keys if k.startswith("app:")} == {f"app:{one}", f"app:{row_two.id}"}
    await stop_all(app)


async def test_a_rewind_with_files_waits_for_another_sessions_reply_and_changes_nothing(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids, _ = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at2})).json()
        as_is = _files(work)
        monkeypatch.setattr(BR, "_same_folder_running", lambda s, row: True)
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2, "files": True, "expect": plan["id"]})
        assert r.status_code == 409 and r.json()["detail"] == BR.OTHER_SESSION
        assert _files(work) == as_is
        # Refused at the first look: not even the log's safety copy was made.
        home = S.service_for(app.state.platform).home
        assert not list(Path(home, "trash").glob("*/hosted-rewind/*"))
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert [e["text"] for e in view["events"] if e["type"] == "user"] == ["first one", "second one"]
        # The conversation alone still rewinds (it writes nothing in the folder).
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})
        assert r.status_code == 200, r.text
        assert _files(work) == as_is
    await stop_all(app)


async def test_the_session_is_held_while_its_files_go_back_and_let_go_after(app, work, monkeypatch):
    sid, ats, _ = await _plain_session(app, work, "grok")
    svc = S.service_for(app.state.platform)
    claims: list[str] = []
    real = CK.restore

    def watched(*a, **k):
        claims.append(svc.live[sid].claim)
        return real(*a, **k)

    monkeypatch.setattr(CK, "restore", watched)
    async with client(app) as c:
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 200, r.text
    assert claims == ["being rewound"] and svc.live[sid].claim == ""
    # A refusal lets go of it too.
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": "stale"})
        assert r.status_code == 409 and svc.live[sid].claim == ""
    await stop_all(app)


async def test_an_archived_session_and_a_running_helper_keep_the_files_where_they_are(app, work, monkeypatch):
    sid, ats, _ = await _plain_session(app, work, "grok")
    as_is = _files(work)
    async with client(app) as c:
        monkeypatch.setattr(HO, "_helper_rows", lambda svc, parent: [types.SimpleNamespace(id="helper-1")])
        monkeypatch.setattr(HO, "_running", lambda svc, hid: hid == "helper-1")
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": "any"})
        assert r.status_code == 409 and r.json()["detail"] == BR.HELPER_BUSY_FILES
        monkeypatch.setattr(HO, "_running", lambda svc, hid: False)
        store.update(app.state.platform.engine, sid, archived_at=dt.datetime.now(dt.timezone.utc))
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": "any"})
        assert r.status_code == 409 and r.json()["detail"] == BR.ARCHIVED
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 409 and r.json()["detail"] == BR.ARCHIVED
    assert _files(work) == as_is
    await stop_all(app)


async def test_a_message_ends_the_undo_and_a_later_conversation_rewind_does_not_bring_it_back(
        app, work, claude_home, tmp_path, monkeypatch):
    """The path the review walked on screen: files-only rewind, a message, then
    "Rewind to here" (conversation only) on that message. Undo came back and
    put the folder back to before the FIRST rewind, over the message's work."""
    _fake(monkeypatch, CLAUDE_FIX, tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await _turn(c, sid, "first one", "t1")
        _first_message_work(work)
        at1 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[0]
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at1})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": at1, "expect": plan["id"]})
        assert r.status_code == 200 and r.json()["branch"]["undo_rewind"] is True
        # The next message's own checkpoint is NOT recorded (the folder could
        # not be read in time): the send path alone must end the undo.
        monkeypatch.setattr(PN, "record_turn_start", lambda *a, **k: None)
        await _turn(c, sid, "second one", "t2")
        (work / "a.py").write_bytes(b"a = the second message's work\n")
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert view["branch"]["undo_rewind"] is False
        home = S.service_for(app.state.platform).home
        assert [r_["turn"] for r_ in PN.read_trees(home, sid) if r_["how"] == "start"] == ["t1"]
        hid = store.get(app.state.platform.engine, sid).harness_session_id
        uuids = [m["uuid"] for m in sent(tmp_path / "fake.log") if m.get("type") == "user"]
        _claude_transcript(claude_home, hid, uuids)
        at2 = _ats(view)[1]
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})
        assert r.status_code == 200, r.text
        assert (await c.get(f"/build/app/sessions/{sid}")).json()["branch"]["undo_rewind"] is False
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 409 and r.json()["detail"] == CK.UNDO_TOO_LATE
        assert (work / "a.py").read_bytes() == b"a = the second message's work\n"
    await stop_all(app)


async def test_the_preview_carries_the_line_ending_note_and_the_limit_in_words(app, work, monkeypatch):
    sid, ats, _ = await _plain_session(app, work, "grok")
    _git(work, "config", "core.autocrlf", "input")
    monkeypatch.setattr(CK, "RESTORE_MAX_FILES", 1)
    async with client(app) as c:
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()
    assert "core.autocrlf is input" in plan["endings"]
    assert plan["can"] is False and "3 files changed" in plan["too_many"]
    await stop_all(app)


async def test_another_sessions_message_in_the_folder_ends_this_sessions_undo(
        app, work, claude_home, tmp_path, monkeypatch):
    """Session one rewinds; session two, in the same folder, then sends a
    message. Undo in session one would put that message's work back."""
    one, ats, _ = await _plain_session(app, work, "grok")
    _fake(monkeypatch, CLAUDE_FIX, tmp_path / "fake.log")
    async with client(app) as c:
        plan = (await c.get(f"/build/app/sessions/{one}/rewind/preview", params={"to": ats[1]})).json()
        r = await c.post(f"/build/app/sessions/{one}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 200 and r.json()["branch"]["undo_rewind"] is True
        (work / "inner").mkdir()
        two = (await new_session(c, work / "inner"))["id"]  # a folder INSIDE session one's
        await _turn(c, two, "do some work", "t-two")
        (work / "a.py").write_bytes(b"a = session two's work\n")
        assert (await c.get(f"/build/app/sessions/{one}")).json()["branch"]["undo_rewind"] is False
        r = await c.post(f"/build/app/sessions/{one}/rewind/undo")
        assert r.status_code == 409 and r.json()["detail"] == CK.UNDO_TOO_LATE
        assert (work / "a.py").read_bytes() == b"a = session two's work\n"
    await stop_all(app)


async def test_a_reply_that_started_while_the_press_waited_for_the_lock_is_seen(app, work, monkeypatch):
    """The who-may-write check runs before the wait for the folder's lock,
    which can be long. It is looked at again once the lock is held."""
    sid, ats, _ = await _plain_session(app, work, "grok")
    as_is = _files(work)
    async with client(app) as c:
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()

        async def free(svc, row, live):  # the first look saw nothing
            return None

        monkeypatch.setattr(BR, "_files_free", free)
        monkeypatch.setattr(BR, "_same_folder_running", lambda s, row: True)  # ...then a reply started
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 409 and r.json()["detail"] == BR.OTHER_SESSION
        assert _files(work) == as_is
        monkeypatch.setattr(BR, "_same_folder_running", lambda s, row: False)
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 200, r.text
        monkeypatch.setattr(BR, "_same_folder_running", lambda s, row: True)
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 409 and r.json()["detail"] == BR.OTHER_SESSION
    await stop_all(app)
