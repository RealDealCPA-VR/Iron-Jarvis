"""v1.412.0 — Build's Code view starts with NO folder chosen.

The user: "it should start automatically with no folder chosen". A Code-view
session may be made without a folder; its FIRST message makes it a folder of
its own under the chat files folder — the native harness's v1.379.0 rule,
through the same helpers (``documents/workfolder.make_chat_workfolder`` and
``routes/build._work_folder_title``) — and the row records it. A first
message that never starts takes the (still empty) folder back.

Every turn replays a RECORDED harness session through the fake harness
(``tests/_build_app_helpers_v1409``): real routes, store, process plumbing.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import client, frames, starts, stop_all, until, use_fake


@pytest.fixture()
def files_root(tmp_path) -> Path:
    return tmp_path / "chat files"


@pytest.fixture()
def app(tmp_path, files_root):
    a = create_app(str(tmp_path / "home"))
    # Never the user's Documents: the chat files folder is a scratch one.
    a.state.platform.config.chat_files_root = str(files_root)
    return a


def _made(files_root: Path) -> list[Path]:
    return sorted(p for p in files_root.iterdir() if p.is_dir()) if files_root.exists() else []


async def _create(c, harness: str = "claude-code", **extra) -> dict:
    r = await c.post("/build/app/sessions", json={"harness": harness, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_session_can_be_made_with_no_folder_and_says_where_its_folder_goes(app, files_root):
    async with client(app) as c:
        s = await _create(c)
        assert s["folder"] == "" and s["no_folder"] is True
        assert s["work_root"] == str(files_root)
        whole = (await c.get("/build/app/sessions")).json()
        assert whole["work_root"] == str(files_root)
        listed = whole["sessions"]
        row = next(x for x in listed if x["id"] == s["id"])
        assert row["no_folder"] is True and row["work_root"] == str(files_root)
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        assert view["session"]["no_folder"] is True and view["session"]["folder"] == ""
        # A session made WITH a folder is no different from before.
        other = files_root.parent / "work"
        other.mkdir()
        w = await _create(c, folder=str(other))
        assert w["no_folder"] is False and "work_root" not in w
    assert _made(files_root) == [], "a folder was made before any message"


async def test_a_saved_conversation_still_needs_its_folder(app):
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={
            "harness": "claude-code", "harness_session_id": "0c9a5a8e-3e0f-4b8e-9a39-111111111111"})
    assert r.status_code == 400 and "folder" in r.json()["detail"]


async def test_the_first_message_makes_the_sessions_own_folder_and_records_it(app, files_root, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await _create(c)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "fix the login page", "turn_id": "t1"})
        assert r.status_code == 200, r.text
        assert frames(r.text)[-1]["type"] == "done"
        made = _made(files_root)
        assert [p.name for p in made] == [f"{date.today().isoformat()} fix the login page"]
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert view["folder"] == str(made[0]) and view["no_folder"] is True
        # The tool ran IN that folder.
        assert starts(log)[0]["cwd"] == str(made[0])
        # The next message (a new process, after the idle reaper) keeps the
        # same folder: nothing new is made, and the tool runs there again.
        svc = S.service_for(app.state.platform)
        await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id)
        await svc.reap_once(now=svc.clock() + S.IDLE_S + 1)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "and the footer", "turn_id": "t2"})
        assert r.status_code == 200, r.text
        assert _made(files_root) == made
        assert [x["cwd"] for x in starts(log)] == [str(made[0])] * 2
    assert store.get(app.state.platform.engine, s["id"]).folder == str(made[0])
    await stop_all(app)


async def test_a_first_message_that_never_starts_takes_the_empty_folder_back(app, files_root, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    async with client(app) as c:
        s = await _create(c, harness="pi")
        # Pi's /login is refused AFTER the folder was made (it is Pi's
        # sign-in screen, a terminal's only).
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "/login", "turn_id": "t1"})
        assert r.status_code == 400
        assert _made(files_root) == [], "the folder made for a message that never started was left behind"
        row = store.get(app.state.platform.engine, s["id"])
        assert row.folder == "" and row.no_folder is True
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert view["folder"] == ""
    await stop_all(app)


async def test_a_folder_that_holds_something_is_never_taken_back(app, files_root, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await _create(c)
    row, made = await asyncio.to_thread(svc._own_folder, s["id"], "keep me")
    assert made and Path(made).is_dir()
    (Path(made) / "notes.txt").write_text("mine\n", encoding="utf-8")
    await asyncio.to_thread(svc._take_back_folder, s["id"], made)
    assert Path(made, "notes.txt").is_file()
    assert store.get(app.state.platform.engine, s["id"]).folder == made


async def test_two_first_messages_keep_one_folder(app, files_root):
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await _create(c)
    _, first = await asyncio.to_thread(svc._own_folder, s["id"], "one")
    row, second = await asyncio.to_thread(svc._own_folder, s["id"], "two")
    assert first and second == "" and row.folder == first
    assert _made(files_root) == [Path(first)], "the second folder was left behind"


async def test_deleting_drops_only_an_empty_own_folder_and_archiving_drops_none(app, files_root):
    # v1.412.0 review (moved on purpose): ARCHIVE keeps the own folder even
    # when empty — Claude Code and Pi keep the conversation by that folder, so
    # unarchiving must find it at the same path. Only a delete removes one.
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await _create(c)
        b = await _create(c)
        _, fa = await asyncio.to_thread(svc._own_folder, a["id"], "empty one")
        _, fb = await asyncio.to_thread(svc._own_folder, b["id"], "full one")
        (Path(fb) / "made.txt").write_text("x", encoding="utf-8")
        assert (await c.post(f"/build/app/sessions/{a['id']}/archive")).status_code == 200
        assert Path(fa).is_dir(), "archive removed the session's own folder"
        assert store.get(app.state.platform.engine, a["id"]).folder == fa
        assert (await c.delete(f"/build/app/sessions/{a['id']}")).status_code == 200
        assert not Path(fa).exists(), "a deleted session's empty own folder was kept"
        assert (await c.delete(f"/build/app/sessions/{b['id']}")).status_code == 200
        assert Path(fb, "made.txt").is_file(), "a folder holding files was removed"
    await stop_all(app)
