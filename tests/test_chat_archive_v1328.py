"""v1.328.0 (calm chat B7) — archive a chat, and stop what it is running first
if the user says so.

``ChatThreadRecord.archived_at`` (additive, nullable: NULL = not archived).
``POST /chat/threads/{id}/archive {stop?}`` and ``POST .../unarchive``.
``GET /chat/threads`` hides archived chats by default; ``?archived=only`` and
``?archived=all`` show them; every row carries ``archived``.

What is guarded, each with its silent failure mode:

1. the column reaches an OLD database (reconciler) and a FRESH one, and an
   old row reads "not archived" (a NULL must never hide a chat);
2. the list: hidden by default, ``only`` / ``all``, a bad value is a 400,
   archiving does not move ``updated_at`` (the list order must not jump),
   unarchive brings it back, an already-archived chat keeps its first stamp,
   404s, a phone (daemon-owned) chat archives too;
3. RUNNING WORK IS NEVER LEFT BEHIND QUIETLY: a chat with a turn in flight
   answers 409 with a plain sentence AND the list ("a reply in progress",
   "a question waiting for you") and nothing changes; another chat's turn
   never blocks this one;
4. ``stop: true`` stops the chat's own named turns with the Stop button's
   stop — a real streaming turn ENDS (no ``done``), a turn parked on an
   approval card ends too — and only then archives, reporting ``stopped``;
5. what cannot be stopped from here (no handle, or a handle whose turn does
   not end in time) is archived anyway and NAMED in ``still_running`` with a
   ``note`` — never claimed as stopped.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import mcp_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.platform import build_platform
from tests.test_chat_approvals_v1187 import _ASK_MSG, _shell_call_stream
from tests.test_chat_approvals_v1187 import _drive_stream as _drive_with_cards
from tests.test_chat_thread_status_v1327 import (
    _READ, _fake_invoke, _gated_two_rounds,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post, _drive_stream


@pytest.fixture(autouse=True)
def _clean_registry():
    def _clear():
        TURNS._threads.clear()
        for tid in TURNS.running_ids():
            TURNS.release(tid)
        chat_routes._THREAD_HANDLES.clear()
        mcp_turn.PENDING.clear()

    _clear()
    yield
    _clear()


def _save_thread(app, title: str = "a chat", owner: str = "user") -> str:
    with session_scope(app.state.platform.engine) as db:
        row = ChatThreadRecord(title=title, messages_json="[]", owner=owner)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _row(app, tid: str) -> ChatThreadRecord:
    with session_scope(app.state.platform.engine) as db:
        return db.get(ChatThreadRecord, tid)


def _ids(client, query: str = "") -> list[str]:
    r = client.get("/chat/threads" + query)
    assert r.status_code == 200, r.text
    return [t["id"] for t in r.json()["threads"]]


# --------------------------------------------------------------------------- #
# 1. The column
# --------------------------------------------------------------------------- #


def test_an_old_database_gains_archived_at_and_its_rows_read_not_archived(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    db_path = home / "ironjarvis.db"
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE chatthreadrecord (id VARCHAR PRIMARY KEY, title VARCHAR, "
        "persona VARCHAR, messages_json VARCHAR, project_id VARCHAR, "
        "created_at DATETIME, updated_at DATETIME)"
    )
    con.execute(
        "INSERT INTO chatthreadrecord VALUES ('chat_old', 'from before', '', '[]', "
        "NULL, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    build_platform(str(tmp_path))  # init_db -> _reconcile_additive_columns
    con = sqlite3.connect(str(db_path))
    cols = {r[1] for r in con.execute("PRAGMA table_info(chatthreadrecord)")}
    con.close()
    assert "archived_at" in cols, "the reconciler did not add archived_at"

    client = TestClient(create_app(str(tmp_path)))
    rows = client.get("/chat/threads").json()["threads"]
    old = next(t for t in rows if t["id"] == "chat_old")
    assert old["archived"] is False, "a NULL stamp must read as not archived"


def test_a_fresh_database_has_the_column(tmp_path):
    app = create_app(str(tmp_path))
    with app.state.platform.engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(chatthreadrecord)")}
    assert "archived_at" in cols


# --------------------------------------------------------------------------- #
# 2. The list, archive and unarchive
# --------------------------------------------------------------------------- #


def test_archive_hides_the_chat_and_unarchive_brings_it_back(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    keep, gone = _save_thread(app, "keep"), _save_thread(app, "gone")
    before = _row(app, gone).updated_at

    r = client.post(f"/chat/threads/{gone}/archive", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"] is True and body["archived_at"]
    assert body["stopped"] == [] and body["still_running"] == []
    assert "note" not in body

    assert _ids(client) == [keep], "the default list hides archived chats"
    assert _ids(client, "?archived=only") == [gone]
    assert set(_ids(client, "?archived=all")) == {keep, gone}
    flags = {t["id"]: t["archived"] for t in client.get("/chat/threads?archived=all").json()["threads"]}
    assert flags == {keep: False, gone: True}
    assert _row(app, gone).updated_at == before, "archiving is not activity"
    assert client.get(f"/chat/threads/{gone}").json()["archived"] is True

    r = client.post(f"/chat/threads/{gone}/unarchive")
    assert r.status_code == 200, r.text
    assert r.json() == {"id": gone, "archived": False, "archived_at": None}
    assert set(_ids(client)) == {keep, gone}
    assert client.get(f"/chat/threads/{gone}").json()["archived"] is False


def test_a_bodiless_archive_is_a_plain_archive(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    tid = _save_thread(app)
    r = client.post(f"/chat/threads/{tid}/archive")
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True


def test_archiving_twice_keeps_the_first_stamp(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    tid = _save_thread(app)
    first = client.post(f"/chat/threads/{tid}/archive", json={}).json()["archived_at"]
    again = client.post(f"/chat/threads/{tid}/archive", json={}).json()["archived_at"]
    assert again == first


def test_unknown_threads_and_bad_filters_answer_plainly(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/chat/threads/nope/archive", json={}).status_code == 404
    assert client.post("/chat/threads/nope/unarchive").status_code == 404
    r = client.get("/chat/threads?archived=yes")
    assert r.status_code == 400
    assert "only" in r.json()["detail"] and "all" in r.json()["detail"]


def test_a_phone_chat_can_be_archived_too(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    tid = _save_thread(app, "Val on telegram", owner="daemon")
    r = client.post(f"/chat/threads/{tid}/archive", json={})
    assert r.status_code == 200, r.text
    assert tid not in _ids(client)
    assert _row(app, tid).owner == "daemon", "it is only hidden"


def test_the_project_filter_and_the_archive_filter_combine(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    with session_scope(app.state.platform.engine) as db:
        a = ChatThreadRecord(title="a", messages_json="[]", project_id="p1")
        b = ChatThreadRecord(title="b", messages_json="[]", project_id="p1")
        c = ChatThreadRecord(title="c", messages_json="[]", project_id="p2")
        db.add_all([a, b, c])
        db.commit()
        ids = (a.id, b.id, c.id)
    client.post(f"/chat/threads/{ids[1]}/archive", json={})
    client.post(f"/chat/threads/{ids[2]}/archive", json={})
    assert _ids(client, "?project_id=p1") == [ids[0]]
    assert _ids(client, "?project_id=p1&archived=only") == [ids[1]]


# --------------------------------------------------------------------------- #
# 3. Running work is refused, listed, and nothing changes
# --------------------------------------------------------------------------- #


def test_a_running_chat_answers_409_with_what_is_running(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    tid = _save_thread(app)
    entry = TURNS.new_thread_turn(tid)
    TURNS.enter_thread(entry)
    try:
        r = client.post(f"/chat/threads/{tid}/archive", json={})
        assert r.status_code == 409, r.text
        assert r.json()["running"] == ["a reply in progress"]
        assert r.json()["detail"] == (
            "This chat is still working: a reply in progress."
            " Stop it and archive, or wait until it finishes."
        )
        entry.park()
        r = client.post(f"/chat/threads/{tid}/archive", json={"stop": False})
        assert r.status_code == 409
        assert r.json()["running"] == ["a reply in progress", "a question waiting for you"]
        assert "a reply in progress and a question waiting for you" in r.json()["detail"]
    finally:
        TURNS.leave_thread(entry)
    assert _row(app, tid).archived_at is None, "a refused archive changes nothing"
    assert tid in _ids(client)


def test_another_chats_turn_never_blocks_this_one(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    mine, other = _save_thread(app, "mine"), _save_thread(app, "other")
    entry = TURNS.new_thread_turn(other)
    TURNS.enter_thread(entry)
    try:
        r = client.post(f"/chat/threads/{mine}/archive", json={})
        assert r.status_code == 200, r.text
        assert r.json()["stopped"] == []
    finally:
        TURNS.leave_thread(entry)


# --------------------------------------------------------------------------- #
# 4. Stop and archive — the real lanes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stop_and_archive_ends_a_streaming_reply_then_archives(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    _fake_invoke(app)
    gate = asyncio.Event()  # never set by the test: only the stop can end it
    app.state.platform.router.stream = _gated_two_rounds(gate)
    seen: dict = {}

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == "first half":
            seen["refused"] = await _asgi_post(app, f"/chat/threads/{tid}/archive", {})
            seen["held"] = list(chat_routes._thread_handles(tid))
            seen["archive"] = await _asgi_post(
                app, f"/chat/threads/{tid}/archive", {"stop": True},
            )
            # Release the model now: a turn the stop really ended is already
            # gone, and one it did not end finishes with `done` (a legible
            # failure below instead of a stream that never ends).
            gate.set()

    try:
        frames = await _drive_stream(
            app, {**_READ, "thread_id": tid, "turn_id": "t-archive"}, on_frame,
        )
    finally:
        gate.set()
    status, body = seen["refused"]
    assert status == 409 and body["running"] == ["a reply in progress"]
    assert len(seen["held"]) == 1 and seen["held"][0].turn_id == "t-archive"
    status, body = seen["archive"]
    assert status == 200, body
    assert body["archived"] is True
    assert body["stopped"] == ["a reply in progress"]
    assert body["still_running"] == [] and "note" not in body
    assert "done" not in [ev for ev, _ in frames], "the reply was stopped, not finished"
    assert TURNS.thread_states() == {}
    assert chat_routes._THREAD_HANDLES == {}, "the handle left with the turn"
    assert _row(app, tid).archived_at is not None


@pytest.mark.asyncio
async def test_stop_and_archive_ends_a_turn_waiting_on_an_approval_card(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    app.state.platform.router.stream = _shell_call_stream()
    ran: list = []
    _fake_invoke(app, ran)
    seen: dict = {}

    async def decide(data):
        # Do NOT answer the card: archive with stop instead.
        seen["archive"] = await _asgi_post(
            app, f"/chat/threads/{tid}/archive", {"stop": True},
        )
        # A turn the stop really ended has already dropped its card (404
        # here); one it did not end is refused, so it ends either way.
        await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})

    frames = await _drive_with_cards(
        app,
        {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True,
         "thread_id": tid, "turn_id": "t-card"},
        decide,
    )
    assert "approval" in [ev for ev, _ in frames], "the turn never asked"
    status, body = seen["archive"]
    assert status == 200, body
    assert body["stopped"] == ["a reply in progress", "a question waiting for you"]
    assert body["still_running"] == []
    assert ran == [], "the asked-for command never ran"
    assert "done" not in [ev for ev, _ in frames]
    assert TURNS.thread_states() == {}
    assert _row(app, tid).archived_at is not None


def test_a_turn_with_no_handle_is_archived_and_named_not_claimed_stopped(tmp_path):
    """The phone's lane (no Stop at all) or an unnamed turn: nothing here can
    stop it, so the archive goes ahead and SAYS what is still running."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    tid = _save_thread(app)
    entry = TURNS.new_thread_turn(tid)
    TURNS.enter_thread(entry)
    try:
        r = client.post(f"/chat/threads/{tid}/archive", json={"stop": True})
    finally:
        TURNS.leave_thread(entry)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"] is True
    assert body["stopped"] == []
    assert body["still_running"] == ["a reply in progress"]
    assert body["note"] == (
        "Archived. This could not be stopped from here and will finish on its"
        " own: a reply in progress."
    )


def test_a_handle_whose_turn_does_not_end_in_time_is_stopped_and_still_named(
    tmp_path, monkeypatch,
):
    """Stop is cooperative (a tool already running finishes first): the press
    reaches the handle, the wait is bounded, and what is left is named."""
    monkeypatch.setattr(chat_routes, "ARCHIVE_STOP_GRACE_S", 0.2)
    app = create_app(str(tmp_path))
    client = TestClient(app)
    tid = _save_thread(app)
    other = _save_thread(app, "other")
    entry = TURNS.new_thread_turn(tid)
    TURNS.enter_thread(entry)
    mine = TURNS.register("t-slow")
    theirs = TURNS.register("t-other")
    chat_routes._hold_thread_handle(tid, mine)
    chat_routes._hold_thread_handle(other, theirs)
    try:
        r = client.post(f"/chat/threads/{tid}/archive", json={"stop": True})
    finally:
        TURNS.leave_thread(entry)
    assert r.status_code == 200, r.text
    assert mine.stopped is True, "the chat's own turn got the Stop button's stop"
    assert theirs.stopped is False, "another chat's turn is never stopped"
    assert r.json()["stopped"] == []
    assert r.json()["still_running"] == ["a reply in progress"]


def test_the_handle_map_drops_by_identity():
    a, b = object(), object()
    chat_routes._hold_thread_handle("chat_x", a)
    chat_routes._hold_thread_handle("chat_x", b)
    chat_routes._drop_thread_handle("chat_x", a)
    assert chat_routes._thread_handles("chat_x") == [b]
    chat_routes._drop_thread_handle("chat_x", a)  # twice removes nothing else
    assert chat_routes._thread_handles("chat_x") == [b]
    chat_routes._drop_thread_handle("chat_x", b)
    assert chat_routes._THREAD_HANDLES == {}
    chat_routes._hold_thread_handle("", a)  # no saved chat: holds nothing
    chat_routes._hold_thread_handle("chat_y", None)
    assert chat_routes._THREAD_HANDLES == {}
