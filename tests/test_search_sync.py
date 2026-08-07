"""History-search WRITE seams (v1.142.0) — every place a conversation lands.

Pair S1 built the index (``tests/test_search_index.py`` proves the substrate).
This suite proves the other half: that the app actually FEEDS it, that deletes
actually empty it, and that neither can cost the user a saved conversation.

What is pinned here:

* **Server-side ``at``** — ``PUT /chat/threads/{id}`` and ``CommThreadStore
  .append`` stamp a timestamp the message shape never carried, and an ``at``
  the client already supplied is NEVER overwritten (fact 3: the dashboard
  round-trips unknown fields verbatim, so the stamp survives every later
  autosave — and a client that starts sending real per-message times wins
  immediately).
* **Five write seams** — desktop chat, comm/phone threads, agent round tables,
  finished agent sessions, and the chat-thread rename path — each round-trips:
  write it, then find it.
* **Two delete paths** (plus the round-table one) — ``DELETE /chat/threads``
  and ``prune_events`` leave NO doc behind. An orphan is invisible: it can only
  ever show up as a search result that opens onto nothing.
* **Transactionality** — the sync runs inside the caller's session, so a rolled
  back write leaves no docs, and a broken index never breaks the write it
  shadows.
* **The fabric upgrade** — the ``sessions`` source keeps its exact FabricHit
  contract while moving onto the index, and the new ``chats`` source puts
  conversations into automatic recall for the first time.

Fully offline: a real daemon on a temp root, no network, no model calls.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import prune_events, search_index, session_scope
from iron_jarvis.core.models import AgentRun, ChatThreadRecord, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.fabric import FABRIC_SOURCES, MemoryFabric
from iron_jarvis.search.models import SearchDocRecord


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


def _docs(engine, **where) -> list[SearchDocRecord]:
    with session_scope(engine) as db:
        rows = list(db.exec(select(SearchDocRecord)))
    for key, value in where.items():
        rows = [r for r in rows if getattr(r, key) == value]
    return rows


def _put(client, messages, thread_id="new", **extra) -> str:
    r = client.put(
        f"/chat/threads/{thread_id}", json={"messages": messages, **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_session(engine, task: str, summary: str = "") -> Session:
    with session_scope(engine) as db:
        s = Session(task=task, summary=summary)
        db.add(s)
        db.commit()
        db.refresh(s)
        return s


# --------------------------------------------------------------------------- #
# (1) `at` stamping — new messages stamped, supplied ones preserved
# --------------------------------------------------------------------------- #
def test_put_stamps_at_on_messages_that_have_none(client):
    tid = _put(client, [{"role": "user", "content": "how do S-corp elections work?"}])
    msgs = client.get(f"/chat/threads/{tid}").json()["messages"]
    assert len(msgs) == 1
    stamped = datetime.fromisoformat(msgs[0]["at"])
    assert stamped.tzinfo is not None, "the stamp must be offset-aware"
    # Everything the client sent is still there, untouched.
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "how do S-corp elections work?"


def test_put_never_overwrites_an_at_the_client_supplied(client):
    theirs = "2024-03-12T09:30:00+00:00"
    tid = _put(
        client,
        [
            {"role": "user", "content": "march question", "at": theirs},
            {"role": "assistant", "content": "march answer"},
        ],
    )
    msgs = client.get(f"/chat/threads/{tid}").json()["messages"]
    assert msgs[0]["at"] == theirs                       # preserved verbatim
    assert msgs[1]["at"] != theirs and msgs[1]["at"]     # the other one stamped

    # And the preserved date is what the index filters on — the whole point.
    index = search_index(client.app.state.platform.engine)
    hits = index.search(
        "march question", after="2024-03-01", before="2024-03-31T23:59:59+00:00"
    )
    assert [h.thread_id for h in hits] == [tid]


def test_put_at_survives_the_dashboard_round_trip(client):
    """The client GETs a thread and PUTs the same objects back (queueSave).
    The stamp written on save 1 must be the SAME after save 2 — otherwise every
    autosave would silently re-date the whole conversation to 'now'."""
    tid = _put(client, [{"role": "user", "content": "first"}])
    first = client.get(f"/chat/threads/{tid}").json()["messages"]
    stamp = first[0]["at"]
    _put(client, first + [{"role": "assistant", "content": "second"}], thread_id=tid)
    after = client.get(f"/chat/threads/{tid}").json()["messages"]
    assert after[0]["at"] == stamp
    assert after[1]["at"] and after[1]["at"] != stamp


def test_a_garbage_at_from_the_client_never_breaks_the_save_or_the_dates(client):
    """``at`` is client-controlled, so it is hostile input.

    ``"tomorrow"``, ``9e99``, an object, ``NaN`` — none of it may 500 the save,
    wedge the sync, or make a date-filtered search miss the rows it should find.
    Anything unreadable falls back to the save time, which is why the whole
    corpus below is still inside a sane window."""
    junk = ["tomorrow", 9e99, -9e99, {"a": 1}, [1, 2], True, 1e18, "1e400",
            "\x00bad", "9999-99-99T99:99:99", "not/a/date", "-1"]
    tid = _put(
        client,
        [{"role": "user", "content": f"junkat{i} eviction clause", "at": v}
         for i, v in enumerate(junk)],
    )
    # Every message is still indexed, and each doc carries a REAL datetime.
    docs = _docs(client.app.state.platform.engine, thread_id=tid)
    assert len(docs) == len(junk)
    assert all(isinstance(d.at, datetime) for d in docs)

    index = search_index(client.app.state.platform.engine)
    assert [h.thread_id for h in index.search("junkat0")] == [tid]
    # A date window around "now" still finds the rows whose `at` was unreadable
    # (they fell back to the save time) — junk cannot hide a message from search.
    now = datetime.now(timezone.utc)
    window = index.search(
        "eviction clause", after=now - timedelta(days=1), before=now + timedelta(days=1),
        limit=50,
    )
    assert len(window) >= len(junk) - 4  # the 4 entries carrying a plausible date


def test_a_live_thread_restamps_until_it_is_reopened(client):
    """HONEST LIMITATION, pinned so it cannot rot into a surprise.

    The stamp is stable only for messages the CLIENT round-trips. The dashboard
    round-trips whatever a GET gave it — but a message typed in the current
    session lives in ``messagesRef`` with no ``at`` at all, so every autosave
    re-stamps it. The stored time therefore means "this thread's last activity"
    until the thread is reopened, at which point it freezes.

    Bounded and monotonic (never goes backwards), and day-granularity date
    search is unaffected for any normal session. The real fix is one line of
    dashboard, out of this pair's footprint: stamp ``at`` at compose time
    client-side (the server already yields to a client-supplied value)."""
    live = [{"role": "user", "content": "typed in this session"}]
    tid = _put(client, live)  # the client keeps ITS objects, which have no `at`
    first = client.get(f"/chat/threads/{tid}").json()["messages"][0]["at"]
    _put(client, [{"role": "user", "content": "typed in this session"}], thread_id=tid)
    second = client.get(f"/chat/threads/{tid}").json()["messages"][0]["at"]
    assert second >= first, "a re-stamp must never move a message backwards"

    # Once the client re-reads the thread, the stamp is frozen for good.
    reopened = client.get(f"/chat/threads/{tid}").json()["messages"]
    _put(client, reopened, thread_id=tid)
    _put(client, reopened, thread_id=tid)
    assert client.get(f"/chat/threads/{tid}").json()["messages"][0]["at"] == second


def test_comm_append_stamps_at(client):
    p = client.app.state.platform
    store = CommThreadStore(p.engine)
    thread = store.resolve("telegram", "42", "Val")
    store.append(thread.id, "user", "the invoice went out friday")
    with session_scope(p.engine) as db:
        msgs = json.loads(db.get(ChatThreadRecord, thread.id).messages_json)
    assert msgs[0]["role"] == "user"
    assert datetime.fromisoformat(msgs[0]["at"]).tzinfo is not None


# --------------------------------------------------------------------------- #
# (2) Seam round-trips: write it, then find it
# --------------------------------------------------------------------------- #
def test_chat_seam_round_trips_into_the_index(client):
    p = client.app.state.platform
    tid = _put(
        client,
        [
            {"role": "user", "content": "remind me about the quarterly elections"},
            {"role": "assistant", "content": "the S-corp election deadline is march"},
        ],
    )
    index = search_index(p.engine)
    hits = index.search("election")           # porter stemming: elections -> election
    assert hits, "the saved chat must be searchable"
    assert {h.kind for h in hits} == {"chat"}
    assert {h.ref for h in hits} == {tid}
    assert all(h.thread_id == tid for h in hits)
    assert all(0.0 <= h.score <= 1.0 for h in hits)


def test_comm_seam_round_trips_into_the_index(client):
    p = client.app.state.platform
    store = CommThreadStore(p.engine)
    thread = store.resolve("telegram", "7", "Val")
    store.append(thread.id, "user", "did the depreciation schedule ship?")
    hits = search_index(p.engine).search("depreciation")
    assert [(h.kind, h.ref) for h in hits] == [("comm", thread.id)]
    assert hits[0].title.startswith("Telegram")


def test_round_seam_round_trips_into_the_index(client):
    p = client.app.state.platform
    threads = AgentThreads(p.engine)
    rec = threads.create("Pricing panel", [{"key": "builtin:planner"}])
    threads._append(
        rec.id,
        [{"who": "user", "content": "should we raise the retainer?",
          "at": "2026-01-05T10:00:00+00:00"}],
    )
    hits = search_index(p.engine).search("retainer")
    assert [(h.kind, h.ref, h.title) for h in hits] == [
        ("round", rec.id, "Pricing panel")
    ]
    # `at` came from the entry, not from "now".
    assert hits[0].at.year == 2026 and hits[0].at.month == 1


def test_session_seam_round_trips_via_post_run_learning(client):
    p = client.app.state.platform
    s = _mk_session(p.engine, "Reconcile the Karbon export", "wrote reconciliation.md")
    p.orchestrator._post_run_learning(s)
    hits = search_index(p.engine).search("reconciliation")
    assert [(h.kind, h.ref) for h in hits] == [("session", s.id)]
    assert hits[0].thread_id == ""          # sessions have no thread


def test_rename_reindexes_a_daemon_owned_thread(client):
    """A metadata-only PUT on a comm thread must refresh the docs' title — the
    index carries the label the UI shows, so a rename that skipped this would
    keep serving the old name forever."""
    p = client.app.state.platform
    store = CommThreadStore(p.engine)
    thread = store.resolve("telegram", "9", "Val")
    store.append(thread.id, "user", "sales tax nexus question")
    r = client.put(f"/chat/threads/{thread.id}", json={"title": "Val (phone)"})
    assert r.status_code == 200, r.text
    hits = search_index(p.engine).search("nexus")
    assert [h.title for h in hits] == ["Val (phone)"]
    # The transcript itself is untouched by a metadata edit.
    assert len(client.get(f"/chat/threads/{thread.id}").json()["messages"]) == 1


# --------------------------------------------------------------------------- #
# (3) Idempotence + the 200-message cap parity
# --------------------------------------------------------------------------- #
def test_resaving_a_thread_replaces_docs_instead_of_duplicating(client):
    p = client.app.state.platform
    msgs = [{"role": "user", "content": "alpha"}, {"role": "assistant", "content": "beta"}]
    tid = _put(client, msgs)
    _put(client, client.get(f"/chat/threads/{tid}").json()["messages"], thread_id=tid)
    assert len(_docs(p.engine, thread_id=tid)) == 2


def test_index_keeps_the_same_200_message_horizon_as_the_row(client):
    p = client.app.state.platform
    tid = _put(
        client,
        [{"role": "user", "content": f"message number {i}"} for i in range(250)],
    )
    stored = client.get(f"/chat/threads/{tid}").json()["messages"]
    docs = _docs(p.engine, thread_id=tid)
    assert len(stored) == 200 and len(docs) == 200
    # seq indexes into the STORED array, so a deep link lands on the right row.
    assert sorted(d.seq for d in docs) == list(range(200))
    assert stored[0]["content"] == "message number 50"


# --------------------------------------------------------------------------- #
# (4) Deletes leave nothing behind
# --------------------------------------------------------------------------- #
def test_delete_thread_removes_its_docs(client):
    p = client.app.state.platform
    tid = _put(client, [{"role": "user", "content": "ephemeral partnership basis"}])
    assert _docs(p.engine, thread_id=tid)
    assert client.delete(f"/chat/threads/{tid}").status_code == 200
    assert _docs(p.engine, thread_id=tid) == []
    assert search_index(p.engine).search("partnership") == []


def test_delete_round_table_removes_its_docs(client):
    p = client.app.state.platform
    threads = AgentThreads(p.engine)
    rec = threads.create("Doomed", [{"key": "builtin:planner"}])
    threads._append(rec.id, [{"who": "user", "content": "amortization schedule"}])
    assert _docs(p.engine, thread_id=rec.id)
    assert threads.delete(rec.id) is True
    assert _docs(p.engine, thread_id=rec.id) == []


def test_prune_events_drops_docs_for_expiring_runs(client):
    """``prune_events`` bulk-deletes AgentRun rows with a raw DELETE that fires
    no ORM event, so it has to drop the matching docs explicitly. Docs are
    addressed by ``ref``, so this pins the ref-keyed drop."""
    p = client.app.state.platform
    old = datetime.now(timezone.utc) - timedelta(days=90)
    with session_scope(p.engine) as db:
        run = AgentRun(session_id="sess_x", created_at=old)
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
    index = search_index(p.engine)
    index.sync_thread(
        "thr_for_run", "chat", "Run transcript", "",
        [{"role": "assistant", "content": "capital gains harvesting"}],
        ref=run_id,
    )
    assert _docs(p.engine, ref=run_id)

    prune_events(p.engine, older_than_days=30)

    with session_scope(p.engine) as db:
        assert db.get(AgentRun, run_id) is None
    assert _docs(p.engine, ref=run_id) == []
    assert index.search("harvesting") == []


def test_prune_pages_the_expiring_ids_instead_of_materializing_them(client,
                                                                    monkeypatch):
    """``prune_events`` exists to stay O(page), not O(backlog) — its bulk DELETE
    replaced an ORM walk that cost ~1.3s and the whole backlog in memory per 33k
    rows. Reading every expiring id into a Python list to feed the index put that
    straight back, so the read is a bounded keyset page. Squeeze the page down
    and prove the paging actually walks the whole set."""
    import iron_jarvis.core.db as core_db

    monkeypatch.setattr(core_db, "_PRUNE_ID_PAGE", 2)
    p = client.app.state.platform
    old = datetime.now(timezone.utc) - timedelta(days=90)
    index = search_index(p.engine)
    run_ids = []
    for i in range(5):
        with session_scope(p.engine) as db:
            run = AgentRun(session_id=f"sess_{i}", created_at=old)
            db.add(run)
            db.commit()
            db.refresh(run)
            run_ids.append(run.id)
        index.sync_thread(
            f"thr_{i}", "chat", f"Run {i}", "",
            [{"role": "assistant", "content": f"installment sale {i}"}],
            ref=run.id,
        )
    assert all(_docs(p.engine, ref=r) for r in run_ids)

    prune_events(p.engine, older_than_days=30)

    assert [r for r in run_ids if _docs(p.engine, ref=r)] == []
    assert index.search("installment") == []


def test_deleting_a_session_removes_its_doc(client):
    """Both session-delete routes (``DELETE /sessions/{id}`` and the Kanban bulk
    ``POST /sessions/clear``) funnel through ``Orchestrator.delete_session``, so
    the drop is wired at that single point."""
    p = client.app.state.platform
    s = _mk_session(p.engine, "Disposable run", "notarized appraisal draft")
    search_index(p.engine).sync_session(s)
    assert _docs(p.engine, ref=s.id)

    assert client.delete(f"/sessions/{s.id}").status_code == 200
    assert _docs(p.engine, ref=s.id) == []
    assert search_index(p.engine).search("appraisal") == []


def test_prune_keeps_session_docs_whose_rows_survive(client):
    """Sessions are NOT pruned by ``prune_events`` — so their docs must not be
    either, or recall would go blind to runs the app still lists."""
    p = client.app.state.platform
    s = _mk_session(p.engine, "Old but living run", "kept summary")
    search_index(p.engine).sync_session(s)
    prune_events(p.engine, older_than_days=0)
    assert _docs(p.engine, ref=s.id)


# --------------------------------------------------------------------------- #
# (5) The sync joins the caller's transaction, and never breaks the write
# --------------------------------------------------------------------------- #
def test_docs_roll_back_with_the_write_they_shadow(client):
    """``db=`` means the CALLER's transaction decides — that is the whole reason
    every seam syncs inside its own ``session_scope``. A rolled back write must
    leave neither the row nor its docs: a transcript without docs is silently
    unsearchable, and docs without a transcript are a search result that opens
    onto nothing."""
    p = client.app.state.platform
    index = search_index(p.engine)
    with session_scope(p.engine) as db:
        rec = ChatThreadRecord(title="Doomed")
        db.add(rec)
        db.flush()
        tid = rec.id
        index.sync_thread(
            tid, "chat", "Doomed", "",
            [{"role": "user", "content": "unpersisted charitable deduction"}],
            db=db,
        )
        # The docs are visible INSIDE the transaction...
        assert db.execute(
            select(SearchDocRecord).where(SearchDocRecord.thread_id == tid)
        ).all()
        db.rollback()

    with session_scope(p.engine) as db:
        assert db.get(ChatThreadRecord, tid) is None
    assert _docs(p.engine, thread_id=tid) == []
    assert index.search("charitable") == []


def test_the_three_write_locks_never_deadlock_each_other(client):
    """``PUT /chat/threads`` (``_THREAD_SAVE_LOCK``), ``CommThreadStore._lock``
    and ``AgentThreads._APPEND_LOCK`` now ALL hold a lock across a session that
    also takes ``SearchIndex._lock``.

    Three locks over one SQLite writer is exactly the shape that deadlocks, so
    the ordering is pinned by construction: the three outer locks are
    peers — no code path holds one and enters another (the chat route calls
    neither store, and neither store calls the route) — and the index lock is
    always innermost, because ``SearchIndex`` never calls back out. Drive all
    three at once and require them to finish."""
    import threading as _t
    from concurrent.futures import ThreadPoolExecutor

    p = client.app.state.platform
    comm = CommThreadStore(p.engine)
    rounds = AgentThreads(p.engine)
    chat_ids = [_put(client, [{"role": "user", "content": f"seed {i}"}]) for i in range(4)]
    comm_ids = [comm.resolve("telegram", f"c{i}", "Val").id for i in range(4)]
    round_ids = [rounds.create(f"R{i}", [{"key": "builtin:planner"}]).id for i in range(4)]

    barrier = _t.Barrier(12)

    def chat(i):
        barrier.wait(timeout=30)
        return _put(client, [{"role": "user", "content": f"chat body {i}"}],
                    thread_id=chat_ids[i])

    def phone(i):
        barrier.wait(timeout=30)
        return comm.append(comm_ids[i], "user", f"phone body {i}")

    def table(i):
        barrier.wait(timeout=30)
        return rounds._append(round_ids[i], [{"who": "user", "content": f"round {i}"}])

    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(fn, i) for fn in (chat, phone, table) for i in range(4)]
        results = [f.result(timeout=60) for f in futures]  # a deadlock => timeout

    assert len(results) == 12
    # Every writer's payload landed in the index, none lost to a botched lock.
    index = search_index(p.engine)
    for i in range(4):
        assert index.search(f"body {i}") or index.search(f"round {i}")


def test_a_broken_index_never_breaks_a_save(client, monkeypatch):
    p = client.app.state.platform
    index = search_index(p.engine)

    def boom(*a, **kw):
        raise RuntimeError("index on fire")

    monkeypatch.setattr(index, "sync_thread", boom)
    monkeypatch.setattr(index, "drop_thread", boom)

    tid = _put(client, [{"role": "user", "content": "still saved"}])
    assert client.get(f"/chat/threads/{tid}").json()["messages"][0]["content"] == "still saved"
    assert client.delete(f"/chat/threads/{tid}").status_code == 200

    store = CommThreadStore(p.engine)
    thread = store.resolve("telegram", "99", "Val")
    assert store.append(thread.id, "user", "still appended") == 1

    threads = AgentThreads(p.engine)
    rec = threads.create("Resilient", [{"key": "builtin:planner"}])
    assert threads._append(rec.id, [{"who": "user", "content": "still rounded"}]) == 1


# --------------------------------------------------------------------------- #
# (6) Fabric: the sessions source keeps its contract on the index
# --------------------------------------------------------------------------- #
def test_fabric_sessions_contract_is_identical_on_both_paths(client):
    p = client.app.state.platform
    s = _mk_session(p.engine, "Draft the Rust invoice summary",
                    "Produced invoices.md with a markdown table")

    # (a) lexical path — the row exists but nothing has indexed it yet.
    assert p.fabric._sessions_indexed("rust invoice markdown", 5) == []
    before = p.fabric.recall("rust invoice markdown", sources=["sessions"], k=5)
    assert [h.ref for h in before] == [s.id]

    # (b) index path — same query, same contract. Asserted through the index
    # method directly so a silent fall-through to (a) cannot pass this test.
    search_index(p.engine).sync_session(s)
    assert [h.ref for h in p.fabric._sessions_indexed("rust invoice markdown", 5)] == [s.id]
    after = p.fabric.recall("rust invoice markdown", sources=["sessions"], k=5)
    assert [h.ref for h in after] == [s.id]

    for hits in (before, after):
        (h,) = hits
        assert h.source == "sessions"
        assert h.ref == s.id                      # BARE session id, not a url
        assert h.title == "Draft the Rust invoice summary"
        assert h.snippet
        assert 0.0 <= h.score <= 1.0
        assert h.extra["status"] == "active"
        assert set(h.as_dict()) >= {"source", "ref", "title", "snippet", "score", "status"}


def test_fabric_sessions_drops_hits_whose_row_is_gone(client):
    """A stale index row must not surface a dead deep link."""
    p = client.app.state.platform
    s = _mk_session(p.engine, "Vanishing run", "obscure terminology: xylophone audit")
    search_index(p.engine).sync_session(s)
    with session_scope(p.engine) as db:
        db.delete(db.get(Session, s.id))
        db.commit()
    assert p.fabric.recall("xylophone audit", sources=["sessions"]) == []


# --------------------------------------------------------------------------- #
# (7) Fabric: the NEW chats source
# --------------------------------------------------------------------------- #
def test_chats_is_a_registered_fabric_source():
    assert "chats" in FABRIC_SOURCES
    from iron_jarvis.memory.fabric import _SOURCE_LABEL

    assert _SOURCE_LABEL["chats"] == "conversation"
    from iron_jarvis.memory.recall import RecallTool

    desc = RecallTool.input_schema["properties"]["sources"]["description"]
    assert "chats" in desc


def test_recall_and_ground_surface_a_past_conversation(client):
    p = client.app.state.platform
    tid = _put(
        client,
        [
            {"role": "user", "content": "we agreed the retainer covers quarterly filings"},
            {"role": "assistant", "content": "yes — quarterly filings are included"},
        ],
        title="Retainer scope",
    )

    hits = p.fabric.recall("retainer quarterly filings", sources=["chats"], k=5)
    assert hits, "a saved conversation must be recallable"
    assert all(h.source == "chats" for h in hits)
    assert hits[0].ref == tid
    assert hits[0].title == "Retainer scope"
    assert 0.0 <= hits[0].score <= 1.0
    assert hits[0].extra["kind"] == "chat"

    block = p.fabric.ground("retainer quarterly filings", sources=["chats"])
    assert "[conversation]" in block
    assert "Retainer scope" in block


def test_chats_collapses_a_chatty_thread_to_one_hit(client):
    p = client.app.state.platform
    _put(
        client,
        [{"role": "user", "content": f"escrow reconciliation note {i}"} for i in range(12)],
        title="Escrow",
    )
    hits = p.fabric.recall("escrow reconciliation", sources=["chats"], k=5)
    assert len(hits) == 1, "one thread should contribute its best passage, once"


def test_chats_join_the_default_federation(client):
    p = client.app.state.platform
    _put(client, [{"role": "user", "content": "the widget catalogue needs repricing"}])
    hits = p.fabric.recall("widget catalogue repricing", k=8)
    assert "chats" in {h.source for h in hits}


def test_a_spoken_question_still_finds_the_conversation(client):
    """The gap that would have shipped the feature dead.

    FTS5's implicit operator is AND, and the query the chat turn retrieves with
    is the user's SENTENCE (``chat_turn._compose_recall_query`` returns the last
    user message verbatim) — so every hit had to contain "what did we say about
    the rental property depreciation" in full. Against one seeded conversation
    and seven realistic ways of asking for it, the strict query found it 2 times
    out of 7; the widening retry finds it 7 out of 7."""
    p = client.app.state.platform
    _put(
        client,
        [{"role": "user",
          "content": "the rental property basis is 412,000 and we use MACRS 27.5 "
                     "year depreciation schedules"}],
        title="Rental basis",
    )
    asks = [
        "what did we say about the rental property depreciation?",
        "remind me the basis on the rental",
        "how are we handling depreciation for the rental property again?",
        "rental property basis",
        "MACRS 27.5",
        "what's the depreciation schedule for the rental?",
        "can you pull up what we decided about the rental property basis and MACRS",
    ]
    found = [a for a in asks if p.fabric.recall(a, sources=["chats"], k=4)]
    assert found == asks, f"missed: {[a for a in asks if a not in found]}"

    # ...and a question about something else still finds NOTHING. The widening
    # must not turn recall into "always returns the newest conversation".
    assert p.fabric.recall(
        "what did we decide about the payroll tax deposit schedule?",
        sources=["chats"], k=4,
    ) == []


def test_a_widened_hit_is_damped_below_an_exact_hit(client):
    """A partial-term match is a weaker claim than a conjunctive one, so it must
    not outrank an exact hit from another store — otherwise the widening buys
    reach by spending answer quality."""
    p = client.app.state.platform
    p.memory.write(layer="user", key="rate",
                   text="the mileage rate for 2026 is 0.71 per mile", scope_id=None)
    _put(client, [{"role": "user", "content": "mileage rate reimbursements policy"}],
         title="Mileage")

    hits = p.fabric.recall(
        "what is the mileage rate we agreed on", k=4,
        sources=["memory", "chats"],
    )
    by_source = {h.source: h.score for h in hits}
    assert "chats" in by_source, "the widened lane must still surface it"
    assert by_source["chats"] < 0.7, by_source
    if "memory" in by_source:
        assert by_source["memory"] >= by_source["chats"]


def test_chats_are_global_on_purpose_and_say_which_project_they_came_from(client):
    """DECISION, pinned: the ``chats`` source is NOT project-filtered.

    Reviewed as a privacy question and accepted as a scoping one. Iron Jarvis is
    single-user and local-first — there is no second reader for a conversation to
    leak TO — and every other global store (notes, memory, lessons, sessions)
    already searches everything the user has; ``knowledge`` is the one
    deliberately project-scoped source. Scoping conversations would make a
    project chat unable to recall the conversation where the user explained what
    they wanted, which is the exact thing this feature exists to fix.

    The cost is noise, not exposure, and it is bounded by ``_seat``'s cap. The
    owning project rides along in ``extra`` so any caller that DOES want to scope
    can. Flip this test if that judgement ever changes."""
    p = client.app.state.platform
    with session_scope(p.engine) as db:
        from iron_jarvis.core.models import Project

        proj = Project(name="Acme")
        db.add(proj)
        db.commit()
        db.refresh(proj)
        pid = proj.id
    _put(client, [{"role": "user", "content": "the qualified opportunity zone basis"}],
         title="Personal", project_id=None)

    hits = p.fabric.recall("qualified opportunity zone", sources=["chats"],
                           project_id=pid, k=5)
    assert [h.title for h in hits] == ["Personal"], "global by design"
    assert hits[0].extra["project_id"] == "", "the owning project must ride along"


def test_chats_cannot_crowd_the_facts_out_of_a_grounded_block(client):
    """The regression this release almost shipped.

    ``SearchIndex`` normalizes BM25 into a TIGHT high band (0.95 / 0.92 / 0.90 /
    0.87), so every conversation that mentions the topic outranks a real cosine
    hit. Measured on ``ground()``'s ``k=4`` before the cap: the block went from
    ``past run + memory + lesson`` to THREE near-identical conversations plus one
    past run — the actual number the user wrote down was evicted by chatter about
    it. ``_seat`` holds chats to one slot in three when other stores are in
    play."""
    p = client.app.state.platform
    query = "depreciation schedules for the rental property"
    sources = ["files", "notes", "memory", "lessons", "sessions", "chats"]

    p.memory.write(
        layer="user",
        key="dep",
        text="Depreciation schedules for the rental property use MACRS 27.5yr",
        scope_id=None,
    )
    p.learning.note_preference("Always show depreciation schedules as a table")
    _mk_session(
        p.engine,
        "Build the depreciation schedules workbook",
        "produced schedules.xlsx for the rental property",
    )
    baseline = {h.source for h in p.fabric.recall(query, k=4, sources=sources[:-1])}
    assert {"sessions", "memory", "lessons"} <= baseline

    for i in range(6):
        _put(
            client,
            [{"role": "user",
              "content": f"talk {i}: depreciation schedules for the rental "
                         f"property, MACRS 27.5"}],
            title=f"chat{i}",
        )

    hits = p.fabric.recall(query, k=4, sources=sources)
    assert len([h for h in hits if h.source == "chats"]) == 1, [h.source for h in hits]
    # ...and nothing the user actually recorded was displaced.
    assert baseline <= {h.source for h in hits}
    assert "MACRS" in p.fabric.ground(query, sources=sources)


def test_a_chats_only_recall_still_fills_k(client):
    """The cap is a diversity floor, not a results ceiling: when the other
    stores have nothing (or were not asked for), the held-back conversations top
    the answer back up to ``k``."""
    p = client.app.state.platform
    for i in range(6):
        _put(client, [{"role": "user", "content": f"barter exchange note {i}"}],
             title=f"barter{i}")

    assert len(p.fabric.recall("barter exchange", k=4, sources=["chats"])) == 4
    # Even in a MIXED recall — the other stores simply have nothing to seat.
    mixed = p.fabric.recall(
        "barter exchange", k=4,
        sources=["files", "notes", "memory", "lessons", "sessions", "chats"],
    )
    assert len(mixed) == 4 and all(h.source == "chats" for h in mixed)


def test_deleted_conversations_leave_recall(client):
    p = client.app.state.platform
    tid = _put(client, [{"role": "user", "content": "forget the mezzanine financing"}])
    assert p.fabric.recall("mezzanine financing", sources=["chats"])
    client.delete(f"/chat/threads/{tid}")
    assert p.fabric.recall("mezzanine financing", sources=["chats"]) == []


# --------------------------------------------------------------------------- #
# (8) Degraded modes: no index, no FTS5, no engine
# --------------------------------------------------------------------------- #
def test_sessions_still_recall_when_fts5_is_unavailable(client, monkeypatch):
    p = client.app.state.platform
    s = _mk_session(p.engine, "Ledger cleanup", "removed duplicate journal entries")
    index = search_index(p.engine)
    index.sync_session(s)
    monkeypatch.setattr(index, "available", lambda: False)

    hits = p.fabric.recall("duplicate journal entries", sources=["sessions"], k=5)
    assert [h.ref for h in hits] == [s.id]
    assert all(0.0 <= h.score <= 1.0 for h in hits)


def test_chats_degrade_to_the_basic_scan_without_fts5(client, monkeypatch):
    p = client.app.state.platform
    _put(client, [{"role": "user", "content": "the leasehold improvement schedule"}])
    index = search_index(p.engine)
    monkeypatch.setattr(index, "available", lambda: False)
    hits = p.fabric.recall("leasehold improvement", sources=["chats"], k=5)
    assert hits and hits[0].source == "chats"
    assert 0.0 <= hits[0].score <= 1.0


def test_an_engineless_fabric_still_no_ops():
    bare = MemoryFabric()
    assert bare.recall("anything", sources=["chats"]) == []
    assert bare.recall("anything", sources=["sessions"]) == []
    assert bare.ground("anything") == ""


def test_search_index_is_shared_per_engine(client):
    p = client.app.state.platform
    assert search_index(p.engine) is search_index(p.engine)


def test_the_index_cache_does_not_pin_engines():
    """The shared index must not outlive its engine.

    The first cut cached engines in a ``WeakKeyDictionary`` — which does NOT
    work here, because the cached ``SearchIndex`` holds ``self.engine`` and a
    weak dict's strong VALUE keeps its own weak KEY alive. Every engine the
    process ever built stayed reachable, with its pool and its open SQLite
    handles; this suite alone mints one per daemon. Parking the index on the
    engine object makes the two lifetimes identical."""
    import gc
    import weakref

    from iron_jarvis.core.db import init_db, make_engine

    refs = []
    for i in range(3):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(f"{tmp}/pin{i}.db")
            init_db(engine)
            assert search_index(engine) is not None
            refs.append(weakref.ref(engine))
            engine.dispose()
            del engine
            gc.collect()
    gc.collect()
    assert [r() for r in refs] == [None, None, None], "engines are still pinned"


def test_a_fabric_built_from_a_platform_shares_the_seams_index(client):
    """``MemoryFabric.from_platform`` must land on the SAME ``SearchIndex`` the
    five write seams write through — the one ``core.db.search_index(engine)``
    hands out.

    It read ``getattr(platform, "search", None)`` until v1.142.0. No such
    attribute exists (it is ``search_index``), so the line was dead and only the
    lazy ``_index()`` fallback rescued it — which meant the fabric's index was
    decided by an accident of import order rather than by the wiring, and a
    fabric built from a platform whose engine had not primed the accessor could
    have gone anywhere. The first assertion below is the one that fails without
    the fix: it pins the value COPIED AT CONSTRUCTION, not the lazily healed one.
    """
    platform = client.app.state.platform
    canonical = search_index(platform.engine)
    assert canonical is not None and platform.search_index is canonical

    fabric = MemoryFabric.from_platform(platform)
    assert fabric._search is canonical, "from_platform did not copy the platform index"
    assert fabric._index() is canonical

    # And it is the same object the seams write through, end to end.
    tid = _put(client, [{"role": "user", "content": "depreciation on the new server rack"}])
    hits = fabric._index().search("depreciation server rack")
    assert [h.thread_id for h in hits] == [tid]
