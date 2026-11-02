"""v1.426.0 (Wave 6, E3) — a conversation handed between a Build terminal and
Build › Code's Code view has ONE writer, and the newest one is handed over.

THE AUDIT: "Terminal → Code view can open the OLDER conversation after
`/clear`, or put a second writer on one session" (``hosted/service.py``: the
terminal's RECORDED Claude session id was trusted for ever).

* ``/clear`` typed in a terminal moves Claude Code to a NEW conversation id:
  "Open in Code view" hands over THAT one (the newest the pane wrote since it
  started, after the recorded one), never the older one; the pane then
  records it, so a second press reuses the same row. Several new ones is
  never a guess (refused in words), and a Code-view row's own conversation in
  the same folder is never taken for the pane's.
* While that terminal's Claude still runs, the conversation it moved to is
  HELD: opening it in the Code view is refused in words (a second writer).
* "Open in terminal" pressed twice never opens a second terminal on one
  conversation (refused in words; one launch).
* Two hand-overs at once (a double click) make ONE row.

Driven through the real routes and service; the panes are stand-ins with the
real pane fields, the conversation files are real files in a scratch Claude
home.
"""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import client, new_session, stop_all, until, use_fake
from tests.test_build_app_review_v1409 import (  # noqa: F401 — fixtures by name
    EXITED,
    RUNNING,
    _Pane,
    _panes,
    _write_claude,
    app,
    work,
)
# v1.426.0 review: a conversation a `/clear` started, as Claude Code writes it
# (the only kind ever read as the terminal's — the review file's pins).
from tests.test_build_app_handover_review_v14260 import write_cleared

OLD = "0a0a0a0a-1111-4222-8333-444444444444"
NEW = "0b0b0b0b-1111-4222-8333-444444444444"
NEWER = "0c0c0c0c-1111-4222-8333-444444444444"
ROWS = "0d0d0d0d-1111-4222-8333-444444444444"


def _aged(path, seconds_ago: float) -> None:
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def _home(tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


# --------------------------------------------------------------------------- #
# /clear: the NEWEST conversation is handed over
# --------------------------------------------------------------------------- #
async def test_after_clear_the_newest_conversation_is_handed_over_not_the_older_one(
        app, work, tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD, "the first question"), 120)
    _aged(write_cleared(home, work, NEW, "after clear"), 20)  # the user typed /clear
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p")
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 201, r.text
        made = r.json()
        assert made["harness_session_id"] == NEW, "the OLDER conversation was handed over after /clear"
        # The pane records the conversation it ran last (its restart Resume
        # and a second press read it): the second press reuses the row.
        assert pane.claude_session_id == NEW
        again = (await c.post("/build/app/from-terminal/p")).json()
        assert again["id"] == made["id"] and again["reused"] is True


async def test_with_no_clear_the_recorded_conversation_is_still_the_one(app, work, tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 20)
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p")
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 201 and r.json()["harness_session_id"] == OLD


async def test_a_code_view_rows_own_newer_conversation_is_never_taken_for_the_panes(
        app, work, tmp_path, monkeypatch):
    """A Code-view session in the same folder (same Claude home) wrote after
    the pane: that conversation is the ROW's, and the pane still ran its own."""
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 120)
    _aged(write_cleared(home, work, ROWS), 10)
    store.create(app.state.platform.engine, harness="claude-code", folder=str(work), harness_session_id=ROWS)
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p")
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 201 and r.json()["harness_session_id"] == OLD, r.text
    assert pane.claude_session_id == OLD


async def test_several_new_conversations_is_never_a_guess(app, work, tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 120)
    _aged(write_cleared(home, work, NEW), 60)
    _aged(write_cleared(home, work, NEWER), 10)
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p")
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
    assert r.status_code == 409 and "cannot tell" in r.json()["detail"]
    assert pane.claude_session_id == OLD, "a refused hand-over records nothing"


# --------------------------------------------------------------------------- #
# a running terminal HOLDS the conversation it moved to
# --------------------------------------------------------------------------- #
async def test_a_running_terminal_holds_the_conversation_it_cleared_into(app, work, tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 120)
    _aged(write_cleared(home, work, NEW, "after clear"), 10)
    pane = _Pane(work, RUNNING, sid=OLD, pane_id="p")  # its Claude still runs, writing NEW
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work),
                                                      "harness_session_id": NEW})
        assert r.status_code == 409, f"a second writer was opened on the terminal's conversation: {r.text}"
        assert "Build terminal" in r.json()["detail"]
        # Once its Claude has exited, the conversation is free to open.
        pane._tail = EXITED
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work),
                                                      "harness_session_id": NEW})
        assert r.status_code == 201, r.text


async def test_a_running_terminal_never_holds_a_code_view_rows_own_conversation(
        app, work, tmp_path, monkeypatch):
    """The Code view and a terminal in the SAME folder: the row's newer
    conversation is the row's (its turn starts), the terminal keeps its own."""
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 120)
    _aged(write_cleared(home, work, ROWS), 10)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work),
                       harness_session_id=ROWS)
    _panes(app, monkeypatch, {"p": _Pane(work, RUNNING, sid=OLD, pane_id="p")})
    held = await asyncio.to_thread(S.service_for(app.state.platform)._holder, "claude-code", ROWS,
                                   except_sid=row.id)
    assert held == "", held
    await stop_all(app)


# --------------------------------------------------------------------------- #
# Open in terminal: one terminal per conversation
# --------------------------------------------------------------------------- #
async def test_open_in_terminal_pressed_twice_opens_one_terminal(app, work, tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import terminals as T

    panes: dict = {}
    launched: list = []

    def launch(platform, body, **kw):
        launched.append(kw.get("command"))
        tid = f"term_{len(launched)}"
        panes[tid] = _Pane(work, RUNNING, sid=kw.get("claude_session_id"), pane_id=tid)
        return SimpleNamespace(id=tid)

    monkeypatch.setattr(T, "launch_pane", launch)
    _panes(app, monkeypatch, panes)
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id)
        first = await c.post(f"/build/app/sessions/{s['id']}/open-terminal")
        assert first.json() == {"terminal_id": "term_1"}
        again = await c.post(f"/build/app/sessions/{s['id']}/open-terminal")
        assert again.status_code == 409, f"a second terminal on one conversation: {again.text}"
        assert "already open in a Build terminal" in again.json()["detail"]
        assert len(launched) == 1
        # Its Claude exited there: opening it again is a person's choice.
        panes["term_1"]._tail = EXITED
        assert (await c.post(f"/build/app/sessions/{s['id']}/open-terminal")).status_code == 200
        assert len(launched) == 2
    await stop_all(app)


async def test_a_double_click_on_open_in_terminal_is_refused_in_words(app, work, tmp_path, monkeypatch):
    """Both presses arrive before the first has opened the terminal: the
    session is held while it is handed over, so the second is refused."""
    from iron_jarvis.daemon.routes import terminals as T

    panes: dict = {}
    launched: list = []

    def launch(platform, body, **kw):
        time.sleep(0.5)  # a terminal takes a moment to open
        launched.append(kw.get("command"))
        tid = f"term_{len(launched)}"
        panes[tid] = _Pane(work, RUNNING, sid=kw.get("claude_session_id"), pane_id=tid)
        return SimpleNamespace(id=tid)

    monkeypatch.setattr(T, "launch_pane", launch)
    _panes(app, monkeypatch, panes)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work),
                       harness_session_id=OLD)
    async with client(app) as c:
        a, b = await asyncio.gather(
            c.post(f"/build/app/sessions/{row.id}/open-terminal"),
            c.post(f"/build/app/sessions/{row.id}/open-terminal"),
        )
    codes = sorted([a.status_code, b.status_code])
    assert codes == [200, 409], (a.text, b.text)
    refused = a if a.status_code == 409 else b
    assert "try again in a moment" in refused.json()["detail"] or "already open" in refused.json()["detail"]
    assert len(launched) == 1
    await stop_all(app)


# --------------------------------------------------------------------------- #
# two hand-overs at once: ONE row
# --------------------------------------------------------------------------- #
async def test_two_hand_overs_at_once_make_one_row(app, work, tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 20)
    _panes(app, monkeypatch, {"p": _Pane(work, EXITED, sid=OLD, pane_id="p")})
    real = store.create

    def slow(*a, **kw):
        time.sleep(0.4)  # both presses read "no row holds it" before either writes
        return real(*a, **kw)

    monkeypatch.setattr(store, "create", slow)
    async with client(app) as c:
        a, b = await asyncio.gather(c.post("/build/app/from-terminal/p"), c.post("/build/app/from-terminal/p"))
    assert {a.status_code, b.status_code} <= {200, 201}, (a.text, b.text)
    assert a.json()["id"] == b.json()["id"]
    rows = [r for r in store.list_rows(app.state.platform.engine) if r.harness_session_id == OLD]
    assert len(rows) == 1, f"two rows on one conversation: {[r.id for r in rows]}"


async def test_two_opens_of_one_saved_conversation_at_once_make_one_row(app, work, tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 20)
    real = store.create

    def slow(*a, **kw):
        time.sleep(0.4)
        return real(*a, **kw)

    monkeypatch.setattr(store, "create", slow)
    body = {"harness": "claude-code", "folder": str(work), "harness_session_id": OLD}
    async with client(app) as c:
        a, b = await asyncio.gather(c.post("/build/app/sessions", json=body),
                                    c.post("/build/app/sessions", json=body))
    assert sorted([a.status_code, b.status_code]) == [201, 409], (a.text, b.text)
    refused = a if a.status_code == 409 else b
    assert "already open" in refused.json()["detail"]
    rows = [r for r in store.list_rows(app.state.platform.engine) if r.harness_session_id == OLD]
    assert len(rows) == 1
