"""48-b (build-out plan R2.14; idea 48 "Scroll back forever",
docs/next-leaps/01-comes-to-you-and-scroll-back.md): phone chats, coding
sessions and round-tables keep every message too.

Until this release three kinds of conversation still CUT their oldest
messages at their caps (a phone chat and a coding session at 200, a
round-table at 400): message 201 of a phone chat was simply deleted on the
next append. Now what leaves the live window MOVES, in the same write, to the
conversation's older messages (``chat_append.keep_older``, the store a long
chat has kept since v1.499.3), in order, and is deleted only with its owner.

- message 201 of a phone chat, a coding session (cut at a turn start, as
  before) and a round-table is kept, in order, and read back with the same
  ``GET /chat/threads/{id}/older`` the chat page pages with;
- ``GET /build/sessions/{id}`` answers ``older`` (where its live lines
  start), read with the lines under the conversation lock;
- deleting any of the three deletes its older messages;
- a coding session's rewind leaves its older lines alone;
- lines that cannot be kept stay live (nothing is ever dropped);
- the start of a phone chat or coding session is claimed only for one made
  since this PC started keeping them (``core.db.older_kept_since``);
- a phone chat's earlier rows are counted for a remote seat, older ones too.

Mutation: restore any of the three slices and its test goes red.
No real provider is called anywhere here.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.threads import AgentThreadRecord, AgentThreads
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core import db as core_db
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon import chat_append as CA
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.search.models import SearchDocRecord


def _words(rows: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in rows]


def _older_docs(engine, thread_id: str) -> list[SearchDocRecord]:
    with session_scope(engine) as db:
        return [d for d in db.exec(select(SearchDocRecord)) if d.thread_id == thread_id and d.seq < 0]


def _set_created(engine, thread_id: str, when) -> None:
    with session_scope(engine) as db:
        rec = db.get(ChatThreadRecord, thread_id)
        rec.created_at = when.replace(tzinfo=None)
        db.add(rec)
        db.commit()


# --------------------------------------------------------------------------- #
# Phone chats
# --------------------------------------------------------------------------- #
def test_message_201_of_a_phone_chat_is_kept_in_order_and_deleted_with_it(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        store = CommThreadStore(engine)
        thread = store.resolve("telegram", "42", "Val")
        for i in range(201):
            store.append(thread.id, "user" if i % 2 == 0 else "assistant", f"phone line {i}")
        with session_scope(engine) as db:
            live = json.loads(db.get(ChatThreadRecord, thread.id).messages_json)
        # The live window is the same 200 as before...
        assert len(live) == 200 and live[0]["content"] == "phone line 1"
        # ...and message 201's oldest neighbour moved instead of going.
        assert _words(CA.read_older(engine, thread.id)) == ["phone line 0"]
        for i in range(201, 260):
            store.append(thread.id, "user", f"phone line {i}")
        assert CA.older_count(engine, thread.id) == 60
        assert _words(CA.read_older(engine, thread.id, limit=1000)) == [f"phone line {i}" for i in range(60)]
        # Kept as they were stored: the time each was written rides along.
        assert all(m.get("at") for m in CA.read_older(engine, thread.id, limit=1000))
        # The chat page pages a phone chat the way it pages any chat.
        body = client.get(f"/chat/threads/{thread.id}").json()
        assert body["older"] == 60 and body["messages"][0]["content"] == "phone line 60"
        page = client.get(f"/chat/threads/{thread.id}/older?before=60&limit=50").json()
        assert page["first_seq"] == 10 and _words(page["messages"]) == [f"phone line {i}" for i in range(10, 60)]
        # History search still finds the oldest words, under the phone chat.
        assert len(_older_docs(engine, thread.id)) == 60
        # Delete means all of it.
        assert client.delete(f"/chat/threads/{thread.id}").status_code == 200
        assert CA.older_count(engine, thread.id) == 0
        assert _older_docs(engine, thread.id) == []


def test_phone_lines_that_cannot_be_kept_stay_live(tmp_path, monkeypatch):
    """Nothing is ever dropped: when the older store cannot be made, the line
    the cap would have cut stays in the live window, as a chat's does."""
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    store = CommThreadStore(engine)
    thread = store.resolve("slack", "u1", "Val")
    monkeypatch.setattr(CA, "ensure_older_table", lambda _engine: False)
    for i in range(203):
        store.append(thread.id, "user", f"line {i}")
    with session_scope(engine) as db:
        live = json.loads(db.get(ChatThreadRecord, thread.id).messages_json)
    assert len(live) == 203 and live[0]["content"] == "line 0"
    # The store works again: the next append moves the backlog, in order.
    monkeypatch.undo()
    store.append(thread.id, "user", "line 203")
    assert _words(CA.read_older(engine, thread.id, limit=1000)) == [f"line {i}" for i in range(4)]
    with session_scope(engine) as db:
        live = json.loads(db.get(ChatThreadRecord, thread.id).messages_json)
    assert len(live) == 200 and live[0]["content"] == "line 4"
    engine.dispose()


def test_a_remote_seat_is_told_of_the_phone_chats_older_rows_too(tmp_path):
    """Build-out R0.1's count: every row the phone lane does not pass is
    counted by who said it. The older messages are not passed either."""
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    store = CommThreadStore(engine)
    thread = store.resolve("telegram", "7", "Val")
    for i in range(230):
        store.append(thread.id, "user" if i % 2 == 0 else "assistant", f"row {i}")
    store.append(thread.id, "assistant", "   ")  # a blank row is never counted
    assert CA.older_count(engine, thread.id) == 31
    rows, earlier = store.history_window(thread.id, limit=60)
    # The tail of 60 holds the blank row, so 59 rows are passed...
    assert len(rows) == 59 and rows[-1]["content"] == "row 229"
    # ...and every other row with words is counted, the 31 older ones too.
    assert sum(earlier.values()) == 230 - 59
    assert earlier == {"user": 86, "jarvis": 85}
    engine.dispose()


# --------------------------------------------------------------------------- #
# Coding sessions
# --------------------------------------------------------------------------- #
def _turn(n: int, lines: int = 2) -> list[dict]:
    """One coding turn: its question, then ``lines - 1`` reply lines."""
    tid = f"turn{n}"
    out = [{"role": "user", "content": f"question {n}", "turnId": tid}]
    out += [{"role": "assistant", "content": f"reply {n}.{k}", "turnId": tid} for k in range(lines - 1)]
    return out


def test_message_201_of_a_coding_session_is_kept_and_the_cut_is_a_whole_turn(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        row = S.create(engine, str(tmp_path))
        written: list[dict] = []
        for n in range(100):
            lines = _turn(n)
            written += lines
            assert S.append_lines(engine, row.id, lines)["ok"]
        assert CA.older_count(engine, row.id) == 0  # exactly 200: nothing left
        # A three-line turn pushes the transcript past 200: the cut is a
        # whole turn from the front (v1.510.0), and those lines MOVE.
        lines = _turn(100, 3)
        written += lines
        assert S.append_lines(engine, row.id, lines)["ok"]
        live = S._messages(S.get_row(engine, row.id))
        older = CA.read_older(engine, row.id, limit=1000)
        assert live[0]["role"] == "user" and live[0].get("turnId"), "the live lines start at a question"
        assert len(live) <= S.MAX_MESSAGES
        # Every line, once, in order: the older ones, then the live ones.
        assert _words(older + live) == _words(written)
        for n in range(101, 140):
            lines = _turn(n)
            written += lines
            S.append_lines(engine, row.id, lines)
        older = CA.read_older(engine, row.id, limit=1000)
        live = S._messages(S.get_row(engine, row.id))
        assert _words(older + live) == _words(written)
        assert len(older) == CA.older_count(engine, row.id) > 0
        # A coding session is never in Chat's history search, older lines included.
        assert _older_docs(engine, row.id) == []
        # The Code view reads where its live lines start, with them...
        view = client.get(f"/build/sessions/{row.id}").json()
        assert view["older"] == len(older)
        assert view["messages"][0]["content"] == live[0]["content"]
        # ...and pages the older ones through the chat's own read.
        page = client.get(f"/chat/threads/{row.id}/older?before={len(older)}&limit=50").json()
        assert page["first_seq"] == len(older) - 50
        assert _words(page["messages"]) == _words(older[-50:])


def test_a_coding_sessions_rewind_leaves_its_older_lines_alone(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        row = S.create(engine, str(tmp_path))
        for n in range(120):
            S.append_lines(engine, row.id, _turn(n))
        kept = CA.read_older(engine, row.id, limit=1000)
        assert len(kept) == 40
        live = S._messages(S.get_row(engine, row.id))
        # Rewind to before the newest turn's question.
        keep = len(live) - 2
        out = S.cut_transcript(engine, row.id, live[keep]["turnId"], keep, live[keep].get("at"))
        assert out["ok"], out
        assert CA.read_older(engine, row.id, limit=1000) == kept
        assert len(S._messages(S.get_row(engine, row.id))) == keep


def test_deleting_a_coding_session_deletes_its_older_lines(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        row = S.create(engine, str(tmp_path))
        for n in range(110):
            S.append_lines(engine, row.id, _turn(n))
        assert CA.older_count(engine, row.id) == 20
        assert client.delete(f"/build/sessions/{row.id}").status_code == 200
        assert CA.older_count(engine, row.id) == 0


def test_the_session_view_counts_older_lines_with_the_lines_it_shows(tmp_path, monkeypatch):
    """The Code view takes ``older`` as where ``messages[0]`` sits, like the
    chat page: a turn that moved lines between the two reads would show one
    twice or miss one at the join. Both are read under the conversation
    lock, so no writer can save between them."""
    import threading

    from iron_jarvis.core.db import CONVERSATION_WRITE_LOCK

    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        row = S.create(engine, str(tmp_path))
        for n in range(103):
            S.append_lines(engine, row.id, _turn(n))
        seen: list[bool] = []
        real = CA.older_count

        def counting(eng, thread_id):
            got: list[bool] = []

            def probe():
                ok = CONVERSATION_WRITE_LOCK.acquire(blocking=False)
                if ok:
                    CONVERSATION_WRITE_LOCK.release()
                got.append(ok)

            t = threading.Thread(target=probe)
            t.start()
            t.join()
            seen.append(got[0])
            return real(eng, thread_id)

        monkeypatch.setattr(CA, "older_count", counting)
        view = client.get(f"/build/sessions/{row.id}").json()
        assert view["older"] == 6 and view["messages"][0]["content"] == "question 3"
        assert seen and not any(seen), "the count was read while a writer could save"


# --------------------------------------------------------------------------- #
# Round-tables
# --------------------------------------------------------------------------- #
def test_message_401_of_a_round_table_is_kept_in_order_and_deleted_with_it(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        threads = AgentThreads(engine)
        room = threads.create("Planning", [{"key": "builtin:critic", "source": "builtin", "name": "critic", "role": "critic"}])
        said = []
        for i in range(420):
            entry = {"who": "user" if i % 2 == 0 else "builtin:critic", "content": f"round line {i}", "at": f"2026-11-2{i % 7}T10:00:00+00:00"}
            said.append(entry)
            threads._append(room.id, [entry])
        rec = threads.get(room.id)
        live = json.loads(rec.messages_json)
        assert len(live) == 400 and live[0]["content"] == "round line 20"
        older = CA.read_older(engine, room.id, limit=1000)
        assert older == said[:20]
        # Findable in history search, as a round-table's own lines are.
        docs = _older_docs(engine, room.id)
        assert len(docs) == 20 and {d.kind for d in docs} == {"round"}
        assert threads.delete(room.id)
        assert CA.older_count(engine, room.id) == 0
        assert _older_docs(engine, room.id) == []
        with session_scope(engine) as db:
            assert db.get(AgentThreadRecord, room.id) is None


async def test_a_remote_seat_shown_the_room_itself_is_told_of_its_older_rows(tmp_path, monkeypatch):
    """A round shown the room's own transcript (no chat rows) passes only its
    live 400: the older ones are counted for the remote, by the rows
    ``remote_history`` would keep (words; never a pending note)."""
    from types import SimpleNamespace

    from iron_jarvis.agents.remote import RemoteAgentRegistry
    from iron_jarvis.agents.threads import clean_participants

    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    app = create_app(str(tmp_path))
    platform = app.state.platform
    RemoteAgentRegistry(platform.engine).upsert("desk", "http://192.168.1.20:8787/task", "http-task")
    seen: list[dict] = []

    async def fake_run(self, record, task, secret_get, **kw):
        seen.append(kw)
        return {"ok": True, "result": "noted"}

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    threads = AgentThreads(platform.engine)
    room = threads.create("room", clean_participants([{"source": "remote", "name": "desk", "role": "participant"}]))
    for i in range(405):
        entry = {"who": "user" if i % 2 == 0 else "remote:desk", "content": f"r{i}"}
        if i == 2:
            entry["pending"] = True  # Iron Jarvis's note about the remote, not words to count
        threads._append(room.id, [entry])
    await threads.run_round(room.id, "@desk go on", SimpleNamespace(platform=platform), directed=["desk"])
    # When the remote spoke: 405 + the round's own line, so 6 had moved, one
    # of them a pending note.
    assert seen and seen[-1].get("history_earlier") == 5
    # Its answer moved one more.
    assert CA.older_count(platform.engine, room.id) == 7
    assert threads.older_by_who(room.id) == {"user": 3, "remote:desk": 3}


# --------------------------------------------------------------------------- #
# Where the start is claimed
# --------------------------------------------------------------------------- #
def test_the_start_of_a_phone_chat_or_session_is_claimed_only_when_it_was_kept(tmp_path):
    """Before 48-b a phone chat or a coding session past its cap lost its
    oldest messages, and nothing on disk says whether one did. The database
    records when it was first opened by a version that keeps them
    (``core.db.older_kept_since``, written once, never moved): one made since
    then kept everything, one made before may not have, and its top says
    "Earlier messages were not kept before this version". A chat keeps
    v1.499.4's rule."""
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        since = core_db.older_kept_since(engine)
        assert since is not None and since.tzinfo is not None
        # Written once: a second start keeps the first stamp.
        core_db.init_db(engine)
        assert core_db.older_kept_since(engine) == since

        store = CommThreadStore(engine)
        phone = store.resolve("telegram", "9", "Val")
        for i in range(230):
            store.append(phone.id, "user", f"p {i}")
        session = S.create(engine, str(tmp_path))
        for n in range(110):
            S.append_lines(engine, session.id, _turn(n))
        chat = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": f"c {i}"} for i in range(230)]}).json()["id"]

        def start(tid: str) -> bool:
            return client.get(f"/chat/threads/{tid}/older?before=0").json()["start_kept"]

        assert start(phone.id) is True and start(session.id) is True and start(chat) is True
        # Made before this PC started keeping them: not known.
        _set_created(engine, phone.id, since - timedelta(seconds=1))
        _set_created(engine, session.id, since - timedelta(seconds=1))
        assert start(phone.id) is False and start(session.id) is False
        # A chat made then is judged by when chats started keeping (v1.499.4).
        _set_created(engine, chat, since - timedelta(seconds=1))
        assert start(chat) is True

        # A short old phone chat never lost a message; a short old coding
        # session may have (its lines are cut at a turn start, and a rewind
        # cuts them too), so it is not claimed.
        short = store.resolve("telegram", "10", "Ann")
        store.append(short.id, "user", "hello")
        _set_created(engine, short.id, since - timedelta(days=3))
        assert start(short.id) is True
        small = S.create(engine, str(tmp_path))
        S.append_lines(engine, small.id, _turn(1))
        _set_created(engine, small.id, since - timedelta(days=3))
        assert start(small.id) is False


def test_an_unreadable_stamp_claims_no_start(tmp_path):
    from iron_jarvis.core.db import init_db, make_engine
    from sqlalchemy import text

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM _ironjarvis_meta WHERE key = :k"), {"k": core_db.OLDER_KEPT_SINCE_KEY})
    assert core_db.older_kept_since(engine) is None
    rec = ChatThreadRecord(title="Telegram · Val", owner="daemon", comm_channel="telegram",
                           messages_json=json.dumps([{"role": "user", "content": "x"}] * 200))
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    assert CA.start_kept_of(engine, rec, 3, 200) is False
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO _ironjarvis_meta(key, value) VALUES (:k, 'not a time')"),
                     {"k": core_db.OLDER_KEPT_SINCE_KEY})
    assert core_db.older_kept_since(engine) is None
    assert CA.start_kept_of(engine, rec, 3, 200) is False
    engine.dispose()

