"""Build's Code view (v1.409.0, found live): a session is titled by its
first message, as a chat is.

The live run showed every row as "New Claude Code session" / "New Codex
session" forever. ``HostedService.start_turn`` now stores the first line of
the first message (spaces collapsed, masked, cut to ``TITLE_CHARS``) when the
row's title is EMPTY and the turn really started. A title the user gave is
kept; a refused send titles nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, use_fake


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def test_the_title_is_the_first_line_with_words_cut_short():
    assert S.first_message_title("\n\n  Fix   the off-by-one\nin cart.py") == "Fix the off-by-one"
    long = "word " * 40
    got = S.first_message_title(long)
    assert len(got) <= S.TITLE_CHARS and got.startswith("word word")
    assert S.first_message_title("   \n ") == ""
    # Masked before the cut: a key never becomes a title.
    assert "ABCDEFGHIJKLMNOP" not in S.first_message_title("use sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 please")


async def test_a_new_session_is_titled_by_its_first_message_and_the_list_says_so(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_signin", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        assert s["title"] == ""
        frames((await c.post(f"/build/app/sessions/{s['id']}/turn",
                             json={"text": "Fix the off-by-one in cart.py\nthen run the tests", "turn_id": "t1"})).text)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert row["title"] == "Fix the off-by-one in cart.py"
        listed = (await c.get("/build/app/sessions")).json()["sessions"]
        assert [r["title"] for r in listed if r["id"] == s["id"]] == ["Fix the off-by-one in cart.py"]
        # The second message never renames it.
        frames((await c.post(f"/build/app/sessions/{s['id']}/turn",
                             json={"text": "something else", "turn_id": "t2"})).text)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert row["title"] == "Fix the off-by-one in cart.py"
    await stop_all(app)


async def test_a_title_the_user_gave_is_kept(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_signin", tmp_path / "fake.log")
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work), "title": "Mine"})
        s = r.json()
        frames((await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hello there", "turn_id": "t1"})).text)
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["title"] == "Mine"
    await stop_all(app)


async def test_a_refused_send_titles_nothing(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_signin", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "   ", "turn_id": "t1"})
        assert r.status_code == 400
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["title"] == ""
    await stop_all(app)
