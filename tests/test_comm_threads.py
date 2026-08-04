"""Offline tests for comm threads (src/iron_jarvis/comm/threads.py).

The messaging-surfaces thread core: identity → daemon-owned chat thread,
atomic appends, /new retirement, and the CHAT_THREAD_UPDATED publish from
both async and sync-thread contexts. No network, no model calls.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading

import pytest
from sqlalchemy import text
from sqlmodel import select

from iron_jarvis.comm.models import CommIdentityRecord
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import init_db, make_engine, open_db, session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import ChatThreadRecord


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine(tmp_path / "comm.db")
    init_db(eng)
    return eng


@pytest.fixture()
def store(engine):
    return CommThreadStore(engine)


class FakeBus:
    """Captures publish(type, payload) as a REAL coroutine — the store must
    schedule it (create_task / run_coroutine_threadsafe), not call it sync."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.fired = threading.Event()

    async def publish(self, type: str, payload=None, session_id=None):
        self.published.append((type, dict(payload or {})))
        self.fired.set()


# --------------------------------------------------------------------------- #
# resolve: find-or-create + reuse
# --------------------------------------------------------------------------- #
def test_resolve_creates_daemon_thread_once_and_reuses(store, engine):
    t1 = store.resolve("telegram", "12345", "Val")
    assert t1.owner == "daemon"
    assert t1.comm_channel == "telegram"
    assert t1.comm_display == "Val"
    assert t1.title == "Telegram · Val"
    t2 = store.resolve("telegram", "12345", "Val")
    assert t2.id == t1.id  # reused, not re-minted
    with session_scope(engine) as db:
        idents = list(db.exec(select(CommIdentityRecord)))
        threads = list(db.exec(select(ChatThreadRecord)))
    assert len(idents) == 1 and len(threads) == 1
    assert idents[0].thread_id == t1.id


def test_resolve_falls_back_to_sender_id_and_stringifies(store):
    t = store.resolve("telegram", 999, "")  # int sender, no display
    assert t.title == "Telegram · 999"
    assert t.comm_display == "999"
    # same identity even when the caller later passes the sender as a str
    assert store.resolve("telegram", "999").id == t.id


def test_resolve_distinct_per_channel_and_sender(store):
    a = store.resolve("telegram", "1")
    b = store.resolve("telegram", "2")
    c = store.resolve("slack", "1")
    assert len({a.id, b.id, c.id}) == 3


def test_resolve_remints_thread_deleted_from_dashboard(store, engine):
    t1 = store.resolve("telegram", "77", "Val")
    with session_scope(engine) as db:  # the dashboard DELETE /chat/threads/{id}
        db.delete(db.get(ChatThreadRecord, t1.id))
        db.commit()
    t2 = store.resolve("telegram", "77", "Val")
    assert t2.id != t1.id
    assert t2.owner == "daemon" and t2.title == "Telegram · Val"
    with session_scope(engine) as db:  # identity re-bound, not duplicated
        idents = list(db.exec(select(CommIdentityRecord)))
    assert len(idents) == 1 and idents[0].thread_id == t2.id


def test_resolve_heal_keeps_known_display_when_call_carries_none(store, engine):
    """Re-minting after a dashboard delete must reuse the identity's known
    display name, not degrade the title to the raw sender id."""
    t1 = store.resolve("telegram", "88", "Val")
    with session_scope(engine) as db:
        db.delete(db.get(ChatThreadRecord, t1.id))
        db.commit()
    t2 = store.resolve("telegram", "88")  # display-less call (e.g. a bare update)
    assert t2.title == "Telegram · Val"
    assert t2.comm_display == "Val"


def test_resolve_concurrent_same_identity_mints_one_thread(store, engine):
    """Two OS threads hitting first-contact for the SAME identity at the same
    instant must not mint two identities/threads (find-or-create is under the
    store lock). A per-round barrier lines both threads up on each fresh
    identity so the find→insert window is actually contested."""
    rounds = 40
    ids: dict[int, set[str]] = {i: set() for i in range(rounds)}
    errors: list = []
    gate = threading.Barrier(2)

    def race() -> None:
        try:
            for i in range(rounds):
                gate.wait(timeout=10)  # both threads enter resolve() together
                ids[i].add(store.resolve("telegram", f"race{i}", "Val").id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=race) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=60)
    assert not errors
    for i in range(rounds):  # each identity resolved to ONE thread for both
        assert len(ids[i]) == 1, f"round {i} split into threads {ids[i]}"
    with session_scope(engine) as db:
        idents = list(db.exec(select(CommIdentityRecord)))
        threads = list(db.exec(select(ChatThreadRecord)))
    assert len(idents) == rounds and len(threads) == rounds


# --------------------------------------------------------------------------- #
# append: atomicity, caps, events
# --------------------------------------------------------------------------- #
def test_append_persists_and_returns_count(store, engine):
    t = store.resolve("telegram", "1", "Val")
    assert store.append(t.id, "user", "hello") == 1
    assert store.append(t.id, "assistant", "hi there") == 2
    with session_scope(engine) as db:
        r = db.get(ChatThreadRecord, t.id)
        msgs = json.loads(r.messages_json)
        assert r.updated_at is not None
    assert msgs == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_append_missing_thread_raises(store):
    with pytest.raises(ValueError):
        store.append("chat_nope", "user", "lost?")


def test_append_caps_content_at_12000(store, engine):
    t = store.resolve("telegram", "1")
    store.append(t.id, "user", "x" * 13000)
    with session_scope(engine) as db:
        msgs = json.loads(db.get(ChatThreadRecord, t.id).messages_json)
    assert len(msgs[0]["content"]) == 12000


def test_append_caps_tail_at_200_like_put(store, engine):
    t = store.resolve("telegram", "1")
    for i in range(205):
        n = store.append(t.id, "user", f"m{i}")
    assert n == 200
    with session_scope(engine) as db:
        msgs = json.loads(db.get(ChatThreadRecord, t.id).messages_json)
    assert len(msgs) == 200
    assert msgs[0]["content"] == "m5"  # oldest five dropped
    assert msgs[-1]["content"] == "m204"


def test_append_atomic_under_concurrent_threads(store, engine):
    """Poller thread + route thread hammering the same thread: no lost writes."""
    t = store.resolve("telegram", "1")
    per_thread, errors = 60, []

    def hammer(tag: str) -> None:
        try:
            for i in range(per_thread):
                store.append(t.id, "user", f"{tag}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=hammer, args=(tag,)) for tag in ("a", "b")]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=30)
    assert not errors
    with session_scope(engine) as db:
        msgs = json.loads(db.get(ChatThreadRecord, t.id).messages_json)  # valid JSON
    assert len(msgs) == 2 * per_thread  # count == writes, nothing lost
    contents = {m["content"] for m in msgs}
    assert contents == {f"{tag}-{i}" for tag in ("a", "b") for i in range(per_thread)}


async def test_append_publishes_event_on_running_loop(engine):
    bus = FakeBus()
    store = CommThreadStore(engine, event_bus=bus)
    t = store.resolve("telegram", "1", "Val")
    n = store.append(t.id, "user", "ping")
    for _ in range(10):  # create_task path: let the scheduled task run
        await asyncio.sleep(0)
        if bus.published:
            break
    assert bus.published == [
        (EventType.CHAT_THREAD_UPDATED, {"thread_id": t.id, "messages": n})
    ]


def test_append_publishes_from_sync_thread_via_set_loop(engine):
    bus = FakeBus()
    store = CommThreadStore(engine, event_bus=bus)
    t = store.resolve("telegram", "1")
    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    try:
        store.set_loop(loop)
        store.append(t.id, "user", "from the poller thread")
        assert bus.fired.wait(timeout=5), "publish never reached the loop"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=5)
        loop.close()
    assert bus.published[0][0] == EventType.CHAT_THREAD_UPDATED
    assert bus.published[0][1] == {"thread_id": t.id, "messages": 1}


def test_append_without_loop_skips_event_silently(engine):
    bus = FakeBus()
    store = CommThreadStore(engine, event_bus=bus)  # no set_loop, sync context
    t = store.resolve("telegram", "1")
    assert store.append(t.id, "user", "no loop anywhere") == 1  # no raise
    assert bus.published == []  # coroutine closed unawaited, by design


def test_append_survives_broken_event_bus(engine):
    class BoomBus:
        def publish(self, *a, **k):
            raise RuntimeError("bus down")

    store = CommThreadStore(engine, event_bus=BoomBus())
    t = store.resolve("telegram", "1")
    assert store.append(t.id, "user", "still lands") == 1


# --------------------------------------------------------------------------- #
# history_body
# --------------------------------------------------------------------------- #
def test_history_body_shape_coercion_and_limit(store):
    t = store.resolve("telegram", "1")
    store.append(t.id, "user", "q1")
    store.append(t.id, "assistant", "a1")
    store.append(t.id, "system", "internal note")  # coerced to assistant
    store.append(t.id, "user", "q2")
    full = store.history_body(t.id)
    assert full == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "assistant", "content": "internal note"},
        {"role": "user", "content": "q2"},
    ]
    assert store.history_body(t.id, limit=2) == full[-2:]
    assert store.history_body(t.id, limit=0) == []  # -0 must not mean "all"


def test_history_body_never_raises(store):
    assert store.history_body("chat_missing") == []


def test_history_body_survives_poisoned_entries(store, engine):
    """Hand-corrupted messages_json (non-dict entries, non-str role/content)
    must degrade gracefully, never crash a phone turn."""
    t = store.resolve("telegram", "1")
    poisoned = [
        "just a string",  # non-dict → skipped
        {"role": {"x": 1}, "content": "weird role"},  # dict role → assistant
        {"role": "user", "content": {"x": 1}},  # dict content → str()
        {"role": "user", "content": None},  # None content → ""
        {"role": "user", "content": "fine"},
    ]
    with session_scope(engine) as db:
        r = db.get(ChatThreadRecord, t.id)
        r.messages_json = json.dumps(poisoned)
        db.add(r)
        db.commit()
    out = store.history_body(t.id)
    assert out == [
        {"role": "assistant", "content": "weird role"},
        {"role": "user", "content": "{'x': 1}"},
        {"role": "user", "content": ""},
        {"role": "user", "content": "fine"},
    ]
    # corrupt blob (not JSON at all) → append resets to [] rather than wedging
    with session_scope(engine) as db:
        r = db.get(ChatThreadRecord, t.id)
        r.messages_json = "{not json"
        db.add(r)
        db.commit()
    assert store.history_body(t.id) == []
    assert store.append(t.id, "user", "recovered") == 1
    assert store.history_body(t.id) == [{"role": "user", "content": "recovered"}]


# --------------------------------------------------------------------------- #
# retire (/new)
# --------------------------------------------------------------------------- #
def test_retire_then_resolve_mints_fresh_thread(store, engine):
    t1 = store.resolve("telegram", "5", "Val")
    store.append(t1.id, "user", "old conversation")
    assert store.retire("telegram", "5") is True
    assert store.retire("telegram", "5") is False  # already unbound
    t2 = store.resolve("telegram", "5", "Val")
    assert t2.id != t1.id
    with session_scope(engine) as db:  # the old thread SURVIVES on the desktop
        assert db.get(ChatThreadRecord, t1.id) is not None


# --------------------------------------------------------------------------- #
# route/fan-out helpers
# --------------------------------------------------------------------------- #
def test_is_daemon_owned_and_thread_channel(store, engine):
    t = store.resolve("telegram", "42", "Val")
    assert store.is_daemon_owned(t.id) is True
    assert store.thread_channel(t.id) == ("telegram", "42")
    # a plain browser-owned thread is neither
    with session_scope(engine) as db:
        plain = ChatThreadRecord(title="desktop chat")
        db.add(plain)
        db.commit()
        plain_id = plain.id
    assert store.is_daemon_owned(plain_id) is False
    assert store.thread_channel(plain_id) is None
    # unknown ids: never raise
    assert store.is_daemon_owned("chat_missing") is False
    assert store.thread_channel("chat_missing") is None
    # retiring unbinds the channel but the thread stays daemon-owned history
    store.retire("telegram", "42")
    assert store.thread_channel(t.id) is None
    assert store.is_daemon_owned(t.id) is True


# --------------------------------------------------------------------------- #
# schema: defaults + additive reconciler
# --------------------------------------------------------------------------- #
def test_plain_thread_defaults_stay_user_owned(engine):
    """Regression: every existing ChatThreadRecord() creation site is untouched."""
    with session_scope(engine) as db:
        r = ChatThreadRecord(title="normal chat")
        db.add(r)
        db.commit()
        db.refresh(r)
        assert r.owner == "user"
        assert r.comm_channel == "" and r.comm_display == ""


def test_reconciler_adds_comm_columns_to_existing_db(tmp_path):
    """A pre-v1.136 DB gains owner/comm_channel/comm_display via open_db's
    additive diff-migration; the old row reads as user-owned (NULL → user)."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE chatthreadrecord ("
        "id TEXT PRIMARY KEY, title TEXT, persona TEXT, messages_json TEXT, "
        "project_id TEXT, created_at TIMESTAMP, updated_at TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO chatthreadrecord VALUES ("
        "'chat_old1', 'legacy', '', '[{\"role\": \"user\", \"content\": \"hi\"}]', "
        "NULL, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()
    engine = open_db(db_path)  # boot path: create_all + additive reconciler
    with engine.connect() as c:
        cols = {r[1] for r in c.execute(text('PRAGMA table_info("chatthreadrecord")'))}
    assert {"owner", "comm_channel", "comm_display", "setup_json"} <= cols
    store = CommThreadStore(engine)
    assert store.is_daemon_owned("chat_old1") is False  # NULL owner == "user"
    assert store.history_body("chat_old1") == [{"role": "user", "content": "hi"}]
    # and the healed DB supports the full comm-thread lifecycle
    t = store.resolve("telegram", "1", "Val")
    assert store.append(t.id, "user", "works on migrated DBs") == 1
