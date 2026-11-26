"""48-b (build-out plan R2.14; idea 48 "Scroll back forever",
docs/next-leaps/01-comes-to-you-and-scroll-back.md): phone chats, coding
sessions and round-tables keep every message too.

Until this release three kinds of conversation still CUT their oldest
messages at their caps (a phone chat and a coding session at 200, a
round-table at 400): message 201 of a phone chat was simply deleted on the
next append. Now what leaves the live window MOVES, in the same write, to the
conversation's older messages (``context.older_messages.keep_older``, the
store a long chat has kept since v1.499.3), in order, and is deleted only
with its owner.

- message 201 of a phone chat, a coding session (cut at a turn start, as
  before) and a round-table is kept, in order, and read back with the same
  ``GET /chat/threads/{id}/older`` the chat page pages with;
- ``GET /build/sessions/{id}`` answers ``older`` (where its live lines
  start), read with the lines under the conversation lock;
- deleting any of the three deletes its older messages;
- a coding session's rewind leaves its older lines alone;
- lines that cannot be kept stay live (nothing is ever dropped), in a phone
  chat, a coding session (an append, a resumed wait, a branch) and a
  round-table alike, and the next write that can keep them moves them;
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
from iron_jarvis.context import older_messages as OM
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
    monkeypatch.setattr(OM, "ensure_older_table", lambda _engine: False)
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


async def test_the_phone_lane_reads_its_window_off_the_loop(tmp_path, monkeypatch):
    """48-b review: ``history_window`` now reads every older message of the
    phone chat (to count them) before its live rows, for each phone message
    addressed to a remote: the phone lane runs it in a worker, never on the
    loop (CLAUDE.md: nothing blocking runs on the event loop)."""
    import threading
    from types import SimpleNamespace

    from iron_jarvis.agents.remote import RemoteAgentRegistry
    from iron_jarvis.comm.inbound import InboundPoller

    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    app = create_app(str(tmp_path))
    platform = app.state.platform
    RemoteAgentRegistry(platform.engine).upsert("desk", "http://192.168.1.20:8787/task", "http-task")
    asked: list[dict] = []

    async def fake_run(self, record, task, secret_get, **kw):
        asked.append(kw)
        return {"ok": True, "result": "noted"}

    async def jarvis_turn(platform_, personas, body):
        return {"reply": "Jarvis here.", "escalate": False}

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    store = CommThreadStore(platform.engine, event_bus=platform.event_bus)
    poller = InboundPoller(
        platform.notifier, SimpleNamespace(), platform.engine, event_bus=platform.event_bus,
        thread_store=store, chat_turn=jarvis_turn, personas={}, platform=platform,
    )

    class _Phone:
        reflex_source = "comm"
        inbound_enabled = True
        chat_enabled = True

        def __init__(self) -> None:
            self.sent: list[str] = []

        def is_authorized(self, sender_id):
            return True

        def send(self, message, **kw):
            self.sent.append(message)
            return {"ok": True}

    loop_thread = threading.get_ident()
    ran_on: list[int] = []
    real = CommThreadStore.history_window

    def spy(self, thread_id, limit=60):
        ran_on.append(threading.get_ident())
        return real(self, thread_id, limit=limit)

    monkeypatch.setattr(CommThreadStore, "history_window", spy)
    said = SimpleNamespace(sender_id="u1", reply_to="u1", text="@desk hello", is_bot=False, sender_name="Val")
    out = await poller._handle_chat("telegram", _Phone(), said, "@desk hello", "Val")
    assert out["status"] == "remote_chat", out
    assert asked, "the remote was never asked"
    assert ran_on, "the phone lane never read its window"
    assert loop_thread not in ran_on, "the phone chat's window was read on the event loop"


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


def test_rewinding_a_long_session_to_its_first_live_question_keeps_its_name(tmp_path):
    """48-b review: a session cut back to nothing is "New session" again, so
    its next question names it. One that keeps older lines is not back to
    nothing: a rewind to its first LIVE question keeps its name."""
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    row = S.create(engine, str(tmp_path))
    for n in range(110):
        S.append_lines(engine, row.id, _turn(n))
    name = S.get_row(engine, row.id).title
    assert name and name != S.NEW_SESSION_TITLE
    live = S._messages(S.get_row(engine, row.id))
    out = S.cut_transcript(engine, row.id, live[0]["turnId"], 0, live[0].get("at"))
    assert out["ok"], out
    after = S.get_row(engine, row.id)
    assert S._messages(after) == [] and OM.older_count(engine, row.id) == 20
    assert after.title == name
    # A short session cut back to nothing is new again, as before.
    short = S.create(engine, str(tmp_path))
    S.append_lines(engine, short.id, _turn(0))
    first = S._messages(S.get_row(engine, short.id))[0]
    assert S.get_row(engine, short.id).title != S.NEW_SESSION_TITLE
    assert S.cut_transcript(engine, short.id, first["turnId"], 0, first.get("at"))["ok"]
    assert S.get_row(engine, short.id).title == S.NEW_SESSION_TITLE
    engine.dispose()


def test_deleting_a_coding_session_deletes_its_older_lines(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        row = S.create(engine, str(tmp_path))
        for n in range(110):
            S.append_lines(engine, row.id, _turn(n))
        assert CA.older_count(engine, row.id) == 20
        assert client.delete(f"/build/sessions/{row.id}").status_code == 200
        assert CA.older_count(engine, row.id) == 0


def _lock_held() -> bool:
    """Whether the conversation write lock is held right now: another thread
    tries to take it (an RLock lets its own holder take it again)."""
    import threading

    from iron_jarvis.core.db import CONVERSATION_WRITE_LOCK

    got: list[bool] = []

    def probe():
        ok = CONVERSATION_WRITE_LOCK.acquire(blocking=False)
        if ok:
            CONVERSATION_WRITE_LOCK.release()
        got.append(ok)

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return not got[0]


def _assert_read_as_one(events: list[tuple[str, bool]]) -> None:
    """Every count was read under the lock, straight after the conversation
    it describes was read under the SAME hold (48-b review: a copy read
    before the lock, with a count read in it, passes a count-only check)."""
    counts = [i for i, (what, _held) in enumerate(events) if what == "count"]
    assert counts, events
    for i in counts:
        assert events[i][1], f"the count was read while a writer could save: {events}"
        assert i > 0 and events[i - 1] == ("row", True), (
            f"the conversation shown was not read with its count, under the lock: {events}"
        )


def test_the_session_view_counts_older_lines_with_the_lines_it_shows(tmp_path, monkeypatch):
    """The Code view takes ``older`` as where ``messages[0]`` sits, like the
    chat page: a turn that moved lines between the two reads would show one
    twice or miss one at the join. Both are read under the conversation
    lock, so no writer can save between them: the count AND the row."""
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        row = S.create(engine, str(tmp_path))
        for n in range(103):
            S.append_lines(engine, row.id, _turn(n))
        events: list[tuple[str, bool]] = []
        real_count, real_row = CA.older_count, S.get_row

        def counting(eng, thread_id):
            events.append(("count", _lock_held()))
            return real_count(eng, thread_id)

        def reading(eng, thread_id):
            events.append(("row", _lock_held()))
            return real_row(eng, thread_id)

        monkeypatch.setattr(CA, "older_count", counting)
        monkeypatch.setattr(S, "get_row", reading)
        view = client.get(f"/build/sessions/{row.id}").json()
        assert view["older"] == 6 and view["messages"][0]["content"] == "question 3"
        _assert_read_as_one(events)


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
    # When the round read the room, 5 of its 405 were older (r0-r4, one of
    # them a pending note): those 4 were left out. The round's own line then
    # moved r5 too, but r5 was in the 400 the remote was passed: never also
    # counted (48-b review; R0.1: never more than were left out).
    assert seen, "the remote was never called"
    rows = seen[-1].get("history") or []
    assert rows and rows[0]["content"] == "r5"
    assert seen[-1].get("history_earlier") == 4
    # Its answer moved one more.
    assert CA.older_count(platform.engine, room.id) == 7
    assert threads.older_by_who(room.id) == {"user": 3, "remote:desk": 3}
    # Bounded by a position: only the older messages below it.
    assert threads.older_by_who(room.id, 5) == {"user": 2, "remote:desk": 2}
    assert threads.older_by_who(room.id, 0) == {}


async def test_a_remote_seat_after_a_local_one_is_told_only_what_it_was_not_passed(tmp_path, monkeypatch):
    """A local seat that speaks before the remote moves one more of the
    room's oldest messages to its older ones; that message was in the room
    the round read, so it is passed to the remote and never also counted."""
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

    async def fake_local(self, seat, others, transcript, d, **kw):
        return "the critic's words"

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    monkeypatch.setattr(AgentThreads, "_speak_local", fake_local)
    threads = AgentThreads(platform.engine)
    room = threads.create("room", clean_participants([
        {"source": "builtin", "name": "critic", "role": "critic"},
        {"source": "remote", "name": "desk", "role": "participant"},
    ]))
    for i in range(403):
        threads._append(room.id, [{"who": "user" if i % 2 == 0 else "remote:desk", "content": f"r{i}"}])
    assert CA.older_count(platform.engine, room.id) == 3
    out = await threads.run_round(room.id, "@critic @desk go on", SimpleNamespace(platform=platform))
    assert out["spoke"] == ["builtin:critic", "remote:desk"]
    # The user's line moved r3 and the critic's answer r4 before the remote
    # spoke; both were in the room the round read, so both were passed.
    passed = [r["content"] for r in seen[-1].get("history") or []]
    assert passed[:2] == ["r3", "r4"]
    assert seen[-1].get("history_earlier") == 3  # r0, r1, r2: never passed


def _long_room(engine, lines: int = 420):
    threads = AgentThreads(engine)
    room = threads.create("Planning", [{"key": "builtin:critic", "source": "builtin", "name": "critic", "role": "critic"}])
    said = []
    for i in range(lines):
        entry = {"who": "user" if i % 2 == 0 else "builtin:critic", "content": f"round line {i}", "at": "2026-11-20T10:00:00+00:00"}
        said.append(entry)
        threads._append(room.id, [entry])
    return threads, room, said


def test_a_long_round_table_is_read_back_from_its_own_page(tmp_path):
    """The old room page (``/agents?thread=<id>``, where a history hit on a
    round-table opens) reads ``GET /agents/threads/{id}``: it says where the
    live messages start (``older``), and ``GET /agents/threads/{id}/older``
    pages the ones before, as a chat's are paged."""
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        threads, room, said = _long_room(engine)
        body = client.get(f"/agents/threads/{room.id}").json()
        assert body["older"] == 20 and body["messages"][0]["content"] == "round line 20"
        assert body["message_count"] == 400
        page = client.get(f"/agents/threads/{room.id}/older?before=20&limit=50").json()
        assert page["total"] == 20 and page["first_seq"] == 0
        assert page["messages"] == said[:20]
        assert page["start_kept"] is True  # made since this PC started keeping them
        mid = client.get(f"/agents/threads/{room.id}/older?before=15&limit=5").json()
        assert mid["first_seq"] == 10 and _words(mid["messages"]) == [f"round line {i}" for i in range(10, 15)]
        # A room made before then may have lost its first messages: not claimed.
        since = core_db.older_kept_since(engine)
        with session_scope(engine) as db:
            rec = db.get(AgentThreadRecord, room.id)
            rec.created_at = (since - timedelta(seconds=1)).replace(tzinfo=None)
            db.add(rec)
            db.commit()
        assert client.get(f"/agents/threads/{room.id}/older?before=0").json()["start_kept"] is False
        # A room under its own 400 with nothing older never lost one, however old.
        _t, short, _s = _long_room(engine, 300)
        with session_scope(engine) as db:
            rec = db.get(AgentThreadRecord, short.id)
            rec.created_at = (since - timedelta(days=3)).replace(tzinfo=None)
            db.add(rec)
            db.commit()
        assert client.get(f"/agents/threads/{short.id}").json()["older"] == 0
        assert client.get(f"/agents/threads/{short.id}/older?before=0").json()["start_kept"] is True
        # No such room: 404, as the room itself answers.
        assert client.get("/agents/threads/athr_none/older?before=0").status_code == 404
        # A chat's own read never answers for a room.
        assert client.get(f"/chat/threads/{room.id}/older?before=20").status_code == 404


def test_the_room_view_counts_older_messages_with_the_messages_it_shows(tmp_path, monkeypatch):
    """The room page takes ``older`` as where ``messages[0]`` sits: both are
    read under the conversation lock, so no round can move one between: the
    count AND the room."""
    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        _threads, room, _said = _long_room(engine, 406)
        events: list[tuple[str, bool]] = []
        real_count, real_get = CA.older_count, AgentThreads.get

        def counting(eng, thread_id):
            events.append(("count", _lock_held()))
            return real_count(eng, thread_id)

        def reading(self, thread_id):
            events.append(("row", _lock_held()))
            return real_get(self, thread_id)

        monkeypatch.setattr(CA, "older_count", counting)
        monkeypatch.setattr(AgentThreads, "get", reading)
        body = client.get(f"/agents/threads/{room.id}").json()
        assert body["older"] == 6 and body["messages"][0]["content"] == "round line 6"
        _assert_read_as_one(events)


async def test_a_rounds_writes_run_off_the_loop(tmp_path, monkeypatch):
    """A room's append is a locked write that, past its 400, also moves the
    oldest messages: a round runs each one in a worker, never on the loop."""
    import threading
    from types import SimpleNamespace

    from iron_jarvis.agents.remote import RemoteAgentRegistry
    from iron_jarvis.agents.threads import clean_participants

    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    app = create_app(str(tmp_path))
    platform = app.state.platform
    RemoteAgentRegistry(platform.engine).upsert("desk", "http://192.168.1.20:8787/task", "http-task")

    async def fake_run(self, record, task, secret_get, **kw):
        return {"ok": True, "result": "noted"}

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    threads = AgentThreads(platform.engine)
    room = threads.create("room", clean_participants([{"source": "remote", "name": "desk", "role": "participant"}]))
    loop_thread = threading.get_ident()
    ran_on: list[int] = []
    real = AgentThreads._append

    def spy(self, thread_id, entries):
        ran_on.append(threading.get_ident())
        return real(self, thread_id, entries)

    monkeypatch.setattr(AgentThreads, "_append", spy)
    await threads.run_round(room.id, "@desk go on", SimpleNamespace(platform=platform), directed=["desk"])
    assert len(ran_on) >= 2, ran_on  # the user's line and the remote's answer
    assert loop_thread not in ran_on


# --------------------------------------------------------------------------- #
# A branch and a resumed wait
# --------------------------------------------------------------------------- #
def test_a_branch_and_a_resumed_wait_keep_what_leaves_the_window(tmp_path):
    """The two other writes that put lines on a coding session: a branch
    (``create_with_lines``, an origin's lines up to the message) and the
    question a "Resume when ready" wait sends (``take_wait``). What leaves
    the live window moves to the older lines there too, in order."""
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    lines: list[dict] = []
    for n in range(110):
        lines += _turn(n)
    branch = S.create_with_lines(engine, str(tmp_path), "Branch", {}, lines)
    live = S._messages(S.get_row(engine, branch.id))
    older = CA.read_older(engine, branch.id, limit=1000)
    assert len(older) == 20 and len(live) == 200
    assert _words(older + live) == _words(lines)

    session = S.create(engine, str(tmp_path))
    for n in range(100):
        S.append_lines(engine, session.id, _turn(n))
    assert CA.older_count(engine, session.id) == 0  # exactly 200 live
    armed = "2026-11-20T10:00:00+00:00"
    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, session.id)
        setup = json.loads(row.setup_json or "{}")
        setup.update({"account": "acct", "account_wait": {"armed_at": armed, "account": "acct"}})
        row.setup_json = json.dumps(setup)
        db.add(row)
        db.commit()
    question = {"role": "user", "content": "question 100", "turnId": "turn100"}
    assert S.take_wait(engine, session.id, armed, "acct", lines=[question]) is True
    # One line past 200: the oldest whole turn moved, the question is live.
    assert _words(CA.read_older(engine, session.id)) == ["question 0", "reply 0.0"]
    live = S._messages(S.get_row(engine, session.id))
    assert len(live) == 199 and live[-1]["content"] == "question 100"
    engine.dispose()


# --------------------------------------------------------------------------- #
# Lines that cannot be kept stay live (48-b review): sessions and rooms too
# --------------------------------------------------------------------------- #
def _no_table(monkeypatch) -> None:
    """The older store cannot be made (``ensure_older_table`` says False)."""
    monkeypatch.setattr(OM, "ensure_older_table", lambda _engine: False)


def _keep_fails(monkeypatch) -> None:
    """The table is there, but adding the rows to it fails in the write."""
    def boom(*_a, **_k):
        raise RuntimeError("the older rows could not be written")

    monkeypatch.setattr(OM, "keep_older", boom)


_FAULTS = {"no table": _no_table, "keep fails": _keep_fails}


def _arm_wait(engine, thread_id: str, armed: str) -> None:
    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, thread_id)
        setup = json.loads(row.setup_json or "{}")
        setup.update({"account": "acct", "account_wait": {"armed_at": armed, "account": "acct"}})
        row.setup_json = json.dumps(setup)
        db.add(row)
        db.commit()


@pytest.mark.parametrize("fault", sorted(_FAULTS))
def test_coding_session_lines_that_cannot_be_kept_stay_live(tmp_path, monkeypatch, fault):
    """An append and a resumed wait's question past the 200, while the older
    lines cannot be kept: every line stays live, in order (the cut never
    drops one), and the next append that can keep them moves the backlog."""
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    session = S.create(engine, str(tmp_path))
    written: list[dict] = []
    for n in range(100):
        written += _turn(n)
        S.append_lines(engine, session.id, _turn(n))
    _FAULTS[fault](monkeypatch)
    for n in range(100, 103):
        written += _turn(n)
        assert S.append_lines(engine, session.id, _turn(n))["ok"]
    live = S._messages(S.get_row(engine, session.id))
    assert len(live) == 206 and _words(live) == _words(written)
    armed = "2026-11-20T10:00:00+00:00"
    _arm_wait(engine, session.id, armed)
    question = {"role": "user", "content": "question 103", "turnId": "turn103"}
    assert S.take_wait(engine, session.id, armed, "acct", lines=[question]) is True
    written.append(question)
    live = S._messages(S.get_row(engine, session.id))
    assert len(live) == 207 and _words(live) == _words(written)
    monkeypatch.undo()
    assert OM.older_count(engine, session.id) == 0
    # The store works again: the next append moves the backlog, in order.
    written += _turn(104)
    assert S.append_lines(engine, session.id, _turn(104))["ok"]
    older = OM.read_older(engine, session.id, limit=1000)
    live = S._messages(S.get_row(engine, session.id))
    assert len(live) <= S.MAX_MESSAGES and len(older) >= 9
    assert live[0]["role"] == "user" and live[0].get("turnId")
    assert _words(older + live) == _words(written)
    engine.dispose()


@pytest.mark.parametrize("fault", sorted(_FAULTS))
def test_a_branch_whose_lines_cannot_be_kept_holds_them_all_live(tmp_path, monkeypatch, fault):
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    lines: list[dict] = []
    for n in range(110):
        lines += _turn(n)
    _FAULTS[fault](monkeypatch)
    branch = S.create_with_lines(engine, str(tmp_path), "Branch", {}, lines)
    live = S._messages(S.get_row(engine, branch.id))
    assert len(live) == 220 and _words(live) == _words(lines)
    monkeypatch.undo()
    assert OM.older_count(engine, branch.id) == 0
    more = _turn(110)
    assert S.append_lines(engine, branch.id, more)["ok"]
    older = OM.read_older(engine, branch.id, limit=1000)
    live = S._messages(S.get_row(engine, branch.id))
    assert len(live) <= S.MAX_MESSAGES and len(older) >= 22
    assert _words(older + live) == _words(lines + more)
    engine.dispose()


@pytest.mark.parametrize("fault", sorted(_FAULTS))
def test_round_table_messages_that_cannot_be_kept_stay_live(tmp_path, monkeypatch, fault):
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    threads = AgentThreads(engine)
    room = threads.create("Planning", [{"key": "builtin:critic", "source": "builtin", "name": "critic", "role": "critic"}])
    said: list[dict] = []

    def say(i: int) -> None:
        entry = {"who": "user" if i % 2 == 0 else "builtin:critic", "content": f"round line {i}", "at": "2026-11-20T10:00:00+00:00"}
        said.append(entry)
        threads._append(room.id, [entry])

    for i in range(400):
        say(i)
    _FAULTS[fault](monkeypatch)
    for i in range(400, 403):
        say(i)
    assert json.loads(threads.get(room.id).messages_json) == said  # 403, all live
    monkeypatch.undo()
    assert OM.older_count(engine, room.id) == 0
    # The store works again: the next message moves the backlog, in order.
    say(403)
    live = json.loads(threads.get(room.id).messages_json)
    older = OM.read_older(engine, room.id, limit=1000)
    assert len(live) == 400 and older == said[:4]
    assert older + live == said
    engine.dispose()


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

