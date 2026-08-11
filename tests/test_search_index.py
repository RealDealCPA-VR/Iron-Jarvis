"""Offline tests for the FTS5 history-search substrate (src/iron_jarvis/search/).

Three other subsystems code against ``SearchIndex``, so this file pins the
CONTRACT, not just the happy path:

* the CAPABILITY, not only the code path — ``available()`` must actually be True
  on this interpreter and a porter-stemmed match must actually work. A test that
  only exercised "whatever mode we happen to be in" would have gone green on a
  build with FTS5 silently missing;
* delete/rebuild leaving ZERO orphan rows in either table (the whole reason the
  index is own-content instead of external-content);
* every hostile query the FTS5 grammar rejects returning ``[]`` instead of
  raising — including the NUL byte, which defeats the quoted-phrase fallback too;
* the ``[0,1]`` score band the memory fabric sorts and filters on;
* filter correctness (kind / project / inclusive date range);
* backfill resumability + idempotence;
* shape parity between ``fts5`` and the ``basic`` LIKE fallback;
* the 200-message tail cap PUT /chat/threads/{id} keeps.

No network, no model calls.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel, select

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.core.models import Session as AgentSession
from iron_jarvis.search import SearchDocRecord, SearchHit, SearchIndex
from iron_jarvis.search.index import (
    MAX_LIMIT,
    SCORE_CEIL,
    SCORE_FLOOR,
    _normalize_scores,
)

NOW = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine(tmp_path / "search.db")
    init_db(eng)
    return eng


@pytest.fixture()
def index(engine):
    return SearchIndex(engine)


def _entries(pairs, start=NOW):
    return [
        {"role": role, "content": body, "at": (start + timedelta(minutes=i)).isoformat()}
        for i, (role, body) in enumerate(pairs)
    ]


@pytest.fixture()
def corpus(index):
    """A small but REAL corpus: three threads and one session, spread over
    months, two projects, four kinds."""
    index.sync_thread(
        "chat_tax",
        "chat",
        "S-corp planning",
        "proj_tax",
        _entries(
            [
                ("user", "Should we file an S-corp election for the LLC this year?"),
                ("assistant", "Elections are due March 15 for a calendar-year entity."),
                ("user", "What about reasonable compensation for the owner?"),
            ],
            NOW - timedelta(days=200),
        ),
    )
    index.sync_thread(
        "chat_video",
        "chat",
        "Pixio storyboard",
        "proj_media",
        _entries(
            [
                ("user", "Render the storyboard shots with seedance"),
                ("assistant", "Stitching the clips now; no election of codecs needed."),
            ],
            NOW - timedelta(days=10),
        ),
    )
    index.sync_thread(
        "comm_val",
        "comm",
        "Telegram · Val",
        "",
        _entries(
            [("user", "remind me about the election deadline"), ("assistant", "March 15.")],
            NOW - timedelta(days=2),
        ),
    )
    index.sync_session(
        AgentSession(
            id="session_1",
            task="Draft the S-corp election memo",
            summary="Wrote the memo covering the election deadline and compensation.",
            project_id="proj_tax",
            created_at=NOW - timedelta(days=5),
        )
    )
    return index


# --------------------------------------------------------------- capability --
def test_fts5_is_actually_available_and_stems(index):
    """THE PRESENCE PIN. Not "the code path works" — the CAPABILITY exists.

    If this interpreter's SQLite ever ships without FTS5, or the porter
    tokenizer stops being applied, history search silently drops to a
    substring scan. That is exactly the class of regression the v1.141 lesson
    was about, so it fails LOUDLY here instead of degrading in production."""
    assert index.available() is True
    assert index.mode == "fts5"
    index.sync_thread(
        "t1", "chat", "T", "", [{"role": "user", "content": "We filed two elections"}]
    )
    # "election" (singular) must find "elections" (plural) -> porter stemming.
    hits = index.search("election")
    assert [h.ref for h in hits] == ["t1"]
    assert "[elections]" in hits[0].snippet.lower()


def test_fts_vtable_is_not_a_mapped_model(engine):
    """Non-negotiable fact 1: the virtual table must stay OUT of
    ``SQLModel.metadata`` or ``_reconcile_additive_columns`` would try to
    ``ALTER`` it on every boot."""
    assert "searchdoc_fts" not in SQLModel.metadata.tables
    assert "searchdocrecord" in SQLModel.metadata.tables
    with engine.connect() as conn:
        kinds = dict(
            conn.execute(
                text("SELECT name, type FROM sqlite_master WHERE name LIKE 'searchdoc%'")
            ).all()
        )
    assert kinds["searchdoc_fts"] == "table"
    assert kinds["searchdocrecord"] == "table"


def test_init_db_is_idempotent_for_the_search_substrate(engine, index):
    """``_ensure_fts`` runs on EVERY boot; a second run must not raise or wipe."""
    index.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "keepme"}])
    init_db(engine)
    init_db(engine)
    assert index.stats()["docs"] == 1
    assert [h.ref for h in index.search("keepme")] == ["t1"]


def test_capability_probe_self_heals_and_is_read_only_when_healthy(engine):
    """A ``SearchIndex`` on an engine whose table is missing must re-create it
    rather than silently searching nothing — but the steady-state probe stays a
    READ, so it can never take SQLite's single writer slot away from a caller
    that is mid-transaction (Pair S2 syncs inside its own ``session_scope``)."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE searchdoc_fts"))
    healed = SearchIndex(engine)
    assert healed.available() is True
    healed.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "healed"}])
    assert [h.ref for h in healed.search("healed")] == ["t1"]

    # A healthy index probes with a plain SELECT: it works while another
    # connection holds the write lock.
    fresh = SearchIndex(engine)
    with session_scope(engine) as holder:
        holder.add(ChatThreadRecord(title="writer holds the lock"))
        holder.flush()
        assert fresh.available() is True
        holder.rollback()


def test_concurrent_syncs_do_not_corrupt_the_index(engine, index):
    """The write lock is real: parallel threads writing different threads must
    leave the row table and its shadow in lockstep."""
    import threading

    def work(i):
        for _ in range(6):
            index.sync_thread(
                f"t{i}",
                "chat",
                f"T{i}",
                "",
                [{"role": "user", "content": f"parallel payload {i}"}] * 3,
            )

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _counts(engine) == (12, 12)
    assert len(index.search("parallel", limit=50)) == 12


def test_n_is_a_rowid_alias(engine, index):
    """The FTS join key. ``INTEGER PRIMARY KEY`` must be a true rowid alias or
    ``searchdoc_fts.rowid = searchdocrecord.n`` silently joins nothing."""
    index.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "alias check"}])
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT n, rowid FROM searchdocrecord")).all()
    assert rows and all(r[0] == r[1] for r in rows)


# ------------------------------------------------------- delete / no orphans --
def _counts(engine):
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT COUNT(*) FROM searchdocrecord")).scalar(),
            conn.execute(text("SELECT COUNT(*) FROM searchdoc_fts")).scalar(),
        )


def test_resync_leaves_no_orphan_rows(engine, index):
    """Delete-all-then-insert must keep BOTH tables in lockstep — the failure
    mode own-content FTS5 was chosen to make impossible."""
    for _ in range(3):
        index.sync_thread(
            "t1", "chat", "T", "", [{"role": "user", "content": f"budget {_}"}] * 2
        )
    assert _counts(engine) == (2, 2)
    index.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "just one now"}])
    assert _counts(engine) == (1, 1)
    assert len(index.search("budget")) == 0
    assert len(index.search("one")) == 1


def test_drop_thread_session_run_and_refs_leave_nothing_behind(engine, corpus):
    docs, fts = _counts(engine)
    assert docs == fts > 0
    assert corpus.drop_thread("chat_tax") == 3
    assert corpus.drop_session("session_1") == 1
    assert _counts(engine)[0] == _counts(engine)[1]
    assert corpus.search("compensation") == []
    # drop_run / drop_refs delete by ref regardless of kind.
    assert corpus.drop_run("chat_video") == 2
    assert corpus.drop_refs(["comm_val", "nope"]) == 2
    assert _counts(engine) == (0, 0)
    assert corpus.stats()["docs"] == 0


def test_rebuild_repairs_an_index_damaged_from_outside(engine, corpus):
    """A raw DELETE issued outside SearchIndex (a future migration, a manual
    fix) leaves the shadow stale. ``rebuild()`` re-derives it from the row
    table, which stays the single source of truth."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM searchdoc_fts"))
    assert corpus.search("election") == []
    docs = _counts(engine)[0]
    assert corpus.rebuild() == docs
    assert _counts(engine) == (docs, docs)
    assert len(corpus.search("election")) >= 3


# ------------------------------------------------------------ query hardening -
HOSTILE = [
    "S-corp AND (election",  # unbalanced paren + bareword column
    "s-corp",  # the single most common real query: "no such column: corp"
    'he said "hello',  # unterminated string
    "*",  # unknown special query
    "***",
    "()",
    "^",
    "a AND",
    "AND OR NOT",
    "election -",
    "-election",
    "NEAR(a b",
    "a NEAR/2 b",
    "{a b}",
    "col:foo",
    "bogus:election",
    "",
    "   ",
    "\n\t",
    "\x00bad",  # defeats the quoted-phrase fallback too — must be stripped first
    "bad\x00\x01\x1fchars",
    "élection",
    "naïve café",
    "日本語のテスト",
    "😀🔥",
    "a" * 5000,
    "election " * 900,
    '"""""',
    "%_\\",
    # --- second battery (reviewer): operator-only, prefix, RTL/CJK, quoting,
    # injection. Every one of these is a query a real palette keystroke or a
    # model-supplied tool argument can produce.
    "AND OR NEAR",
    "OR",
    "NOT",
    "a*",
    "*a",
    "x" * 600,  # over MAX_QUERY_CHARS
    'mid"" word',
    'say"hello"there',
    '"unclosed',
    "NEAR/",
    "NEAR(election deadline",
    "(((",
    ")))",
    "^election",
    "election^",
    "text:election",
    "rowid:1",
    "مرحبا بالعالم",  # RTL Arabic
    "עברית שלום",  # RTL Hebrew
    "选举 截止日期",  # CJK
    "‮ reversed",  # RTL override control char
    "🏳️‍🌈👨‍👩‍👧‍👦",  # ZWJ emoji sequences
    "'; DROP TABLE searchdocrecord; --",
    "' OR '1'='1",
    "\\",
    "--",
    "/*",
    "\x00",
    "elec\x00tion",
]


@pytest.mark.parametrize("query", HOSTILE)
def test_hostile_queries_never_raise(corpus, query):
    hits = corpus.search(query)
    assert isinstance(hits, list)
    assert all(isinstance(h, SearchHit) for h in hits)
    # ...and the corpus is still there: no query may mutate the index (the
    # injection strings above go through bound parameters, never string
    # interpolation).
    assert corpus.stats()["docs"] == 8


@pytest.mark.parametrize("query", HOSTILE)
def test_hostile_queries_never_raise_in_basic_mode(corpus, monkeypatch, query):
    monkeypatch.setattr(corpus, "available", lambda: False)
    assert isinstance(corpus.search(query), list)


def test_phrase_fallback_recovers_the_common_hyphen_case(corpus):
    """``s-corp`` is a SYNTAX ERROR verbatim ("no such column: corp"); the
    fully-quoted phrase tier turns it into a real answer."""
    hits = corpus.search("s-corp")
    assert {h.ref for h in hits} == {"chat_tax", "session_1"}


def test_prefix_tier_only_fires_when_nothing_else_matched(corpus):
    """A partially typed word still finds something (the palette types as you
    go), and the prefix retry can only ever turn empty into non-empty — it
    never reorders or dilutes a result set that already had rows."""
    # "storyb" is not a stem of anything; tiers 1 and 2 both come back empty and
    # only the prefix retry can find "storyboard".
    assert [h.ref for h in corpus.search("storyb")] == ["chat_video"]
    # A query that DOES match on tier 1 is answered by tier 1 alone: "election"
    # ranks by BM25 (the shortest, most on-topic doc first), not by prefix.
    exact = corpus.search("election")
    assert {h.ref for h in exact} == {"chat_tax", "chat_video", "comm_val", "session_1"}
    assert exact[0].ref == "comm_val"


def test_fts5_operators_still_work_for_callers_who_mean_them(corpus):
    assert {h.ref for h in corpus.search("seedance OR compensation")} == {
        "chat_tax",
        "chat_video",
        "session_1",
    }
    assert [h.ref for h in corpus.search('"reasonable compensation"')] == ["chat_tax"]
    assert {h.ref for h in corpus.search("NEAR(election deadline, 3)")} == {
        "comm_val",
        "session_1",
    }


def test_search_survives_the_fts_table_vanishing(engine, corpus):
    """Capability is cached, so a table that disappears mid-life must degrade to
    ``[]``, never to a 500."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE searchdoc_fts"))
    assert corpus.search("election") == []


# ------------------------------------------------------------ score normalizing
def test_scores_stay_in_bounds_over_a_real_corpus(corpus):
    """The pinned band. ``MemoryFabric.recall`` filters on ``min_score`` and
    sorts ACROSS sources, so an out-of-band score would swamp every cosine hit
    it is ranked against."""
    seen = 0
    for query in ("election", "march", "the", "compensation OR storyboard", "s-corp"):
        hits = corpus.search(query, limit=50)
        for h in hits:
            seen += 1
            assert 0.0 <= h.score <= 1.0
            assert SCORE_FLOOR <= h.score <= SCORE_CEIL
        assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert seen > 5


def test_every_match_clears_the_fabric_min_score_floor(corpus):
    """FTS5's implicit operator is AND, so a hit contains EVERY query term —
    it must never be filtered out by ``recall(min_score=0.05)``."""
    for h in corpus.search("election", limit=50):
        assert h.score > 0.05


def test_normalize_bounds_are_structural():
    for relevances in ([], [0.0] * 5, [1e-9, 1e-9], [8.0, 4.0, 0.0], [1e9] + [1.0] * 199):
        scores = _normalize_scores(relevances)
        assert len(scores) == len(relevances)
        assert all(SCORE_FLOOR <= s <= SCORE_CEIL for s in scores)
        assert all(0.0 <= s <= 1.0 for s in scores)


def test_a_better_bm25_ranks_higher(corpus):
    hits = corpus.search("election")
    assert len(hits) >= 3
    assert hits[0].score >= hits[-1].score


def test_snippets_are_deterministic(corpus):
    """``MemoryFabric._dedupe`` keys on ``snippet[:120].lower()`` — the same row
    and query must always produce the same snippet."""
    a = [h.snippet for h in corpus.search("election", limit=20)]
    b = [h.snippet for h in corpus.search("election", limit=20)]
    assert a == b and any("[" in s for s in a)


# -------------------------------------------------------------------- filters -
def test_kind_filter(corpus):
    assert {h.kind for h in corpus.search("election", kinds=["comm"])} == {"comm"}
    assert {h.kind for h in corpus.search("election", kinds="session")} == {"session"}
    assert {h.kind for h in corpus.search("election", kinds=["chat", "comm"])} <= {
        "chat",
        "comm",
    }
    assert corpus.search("election", kinds=["nosuchkind"]) == []


def test_project_filter(corpus):
    assert {h.ref for h in corpus.search("election", project_id="proj_tax")} == {
        "chat_tax",
        "session_1",
    }
    assert corpus.search("election", project_id="proj_nobody") == []


def test_date_range_is_inclusive_and_correct(corpus):
    """The stored column is a SQLAlchemy SQLite DATETIME; bounds must be bound
    with the matching type or the comparison silently matches nothing."""
    recent = corpus.search("election", after=NOW - timedelta(days=7))
    assert {h.ref for h in recent} == {"comm_val", "session_1"}
    old = corpus.search("election", before=NOW - timedelta(days=100))
    assert {h.ref for h in old} == {"chat_tax"}
    window = corpus.search(
        "election", after=NOW - timedelta(days=7), before=NOW - timedelta(days=3)
    )
    assert {h.ref for h in window} == {"session_1"}
    # ISO strings (what an HTTP route hands over) work identically.
    iso = corpus.search("election", after=(NOW - timedelta(days=7)).isoformat())
    assert {h.ref for h in iso} == {h.ref for h in recent}
    # An inclusive bound EXACTLY on a doc's timestamp keeps that doc.
    exact = corpus.search("election", after=NOW - timedelta(days=5))
    assert "session_1" in {h.ref for h in exact}


def test_limit_is_clamped(index):
    """The corpus must EXCEED ``MAX_LIMIT`` or the clamp is untestable — with 60
    docs an unclamped ``limit=10_000`` also returns "<= 200" and the assertion
    is a tautology. Two threads of 150 give 300 matching docs."""
    for t in ("t1", "t2"):
        index.sync_thread(
            "t_" + t, "chat", "T", "",
            [{"role": "user", "content": f"row {t} {i}"} for i in range(150)],
        )
    assert index.stats()["docs"] == 300
    assert len(index.search("row", limit=5)) == 5
    assert len(index.search("row", limit=10_000)) == MAX_LIMIT
    assert len(index.search("row", limit=-3)) == 1  # clamped up, never negative
    assert len(index.search("row", limit=0)) == 20  # falsy -> the default
    # The basic fallback clamps identically (its scan window is separate).
    index._available = False
    try:
        assert len(index.search("row", limit=10_000)) == MAX_LIMIT
    finally:
        index._available = True


# ----------------------------------------------------------------- 200-cap ---
def test_thread_sync_keeps_the_same_200_tail_the_route_keeps(engine, index):
    """Parity with ``PUT /chat/threads/{id}``'s ``msgs[-200:]`` and
    ``CommThreadStore._MAX_MESSAGES``: the index must never remember more of a
    thread than the thread itself does, and ``seq`` must still address the
    message's position in the FULL list."""
    entries = [{"role": "user", "content": f"message number {i}"} for i in range(250)]
    assert index.sync_thread("t1", "chat", "T", "", entries) == 200
    with session_scope(engine) as db:
        seqs = sorted(r.seq for r in db.exec(select(SearchDocRecord)))
    assert seqs == list(range(50, 250))
    assert _counts(engine) == (200, 200)
    assert index.search("249") and not index.search("\"number 4\"")


# ------------------------------------------------------------ never-raise -----
def test_writes_never_raise_on_garbage(index):
    assert index.sync_thread("", "chat", "T", "", [{"content": "x"}]) == 0
    assert index.sync_thread("t1", "nonsense-kind", None, None, None) == 0
    assert (
        index.sync_thread(
            "t2",
            "chat",
            "T",
            "",
            ["not a dict", None, 42, {"role": None, "content": None}, {"content": "   "}],
        )
        == 0
    )
    assert index.sync_thread(
        "t3",
        "chat",
        "T",
        "",
        [
            {"role": "user", "content": "ok", "at": "not-a-date", "seq": "banana"},
            {"role": "user", "content": "ok2", "at": 1_770_000_000_000},
        ],
    ) == 2
    assert index.sync_session(object()) == 0
    assert index.sync_session(None) == 0
    assert index.drop_thread("") == 0
    assert index.drop_refs([]) == 0


def test_text_is_capped(index):
    index.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "z" * 9000}])
    with session_scope(index.engine) as db:
        assert len(db.exec(select(SearchDocRecord)).one().text) == 4000


# --------------------------------------------------- caller-owned transaction -
def test_sync_commits_with_the_callers_transaction(engine, index):
    """Pair S2 syncs INSIDE its own ``session_scope`` (and its own lock). The
    docs must live and die with the caller's transaction."""
    with session_scope(engine) as db:
        rec = ChatThreadRecord(title="Rolled back")
        db.add(rec)
        db.flush()
        index.sync_thread(
            rec.id, "chat", rec.title, "", [{"role": "user", "content": "tuna casserole"}], db=db
        )
        db.rollback()
    assert index.search("casserole") == []
    assert _counts(engine) == (0, 0)

    with session_scope(engine) as db:
        rec = ChatThreadRecord(title="Committed")
        db.add(rec)
        db.flush()
        index.sync_thread(
            rec.id, "chat", rec.title, "", [{"role": "user", "content": "tuna casserole"}], db=db
        )
        db.commit()
    assert [h.title for h in index.search("casserole")] == ["Committed"]
    assert _counts(engine) == (1, 1)


def test_drop_inside_a_caller_transaction(engine, index):
    index.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "erase me"}])
    with session_scope(engine) as db:
        index.drop_thread("t1", db=db)
        db.commit()
    assert _counts(engine) == (0, 0)


# ------------------------------------------------------------------- stats ---
def test_stats_shape(corpus):
    stats = corpus.stats()
    assert set(stats) == {"docs", "threads", "sessions", "available", "mode"}
    assert stats["docs"] == 8  # 3 + 2 + 2 messages + 1 session
    assert stats["threads"] == 3  # session docs carry no thread_id
    assert stats["sessions"] == 1
    assert stats["available"] is True and stats["mode"] == "fts5"


def test_stats_is_honest_when_the_table_is_gone(engine, corpus):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE searchdocrecord"))
    stats = corpus.stats()
    assert stats["docs"] == 0 and stats["threads"] == 0 and stats["sessions"] == 0


def test_sync_session_upserts(index):
    row = AgentSession(id="s1", task="Do the thing", summary="Did it", created_at=NOW)
    assert index.sync_session(row) == 1
    row.summary = "Did it twice"
    assert index.sync_session(row) == 1
    assert index.stats()["sessions"] == 1
    assert [h.snippet for h in index.search("twice")]


# ----------------------------------------------------------------- backfill ---
def _seed_history(engine, threads=5, sessions=3):
    with session_scope(engine) as db:
        for i in range(threads):
            db.add(
                ChatThreadRecord(
                    id=f"chat_{i}",
                    title=f"Thread {i}",
                    project_id="proj_x" if i % 2 else None,
                    owner="daemon" if i == 0 else "user",
                    comm_channel="telegram" if i == 0 else "",
                    messages_json=json.dumps(
                        [
                            {"role": "user", "content": f"legacy question {i}"},
                            {"role": "assistant", "content": f"legacy answer {i}"},
                        ]
                    ),
                    created_at=NOW - timedelta(days=threads - i),
                )
            )
        for i in range(sessions):
            db.add(
                AgentSession(
                    id=f"session_{i}",
                    task=f"legacy task {i}",
                    summary=f"legacy summary {i}",
                    created_at=NOW - timedelta(days=sessions - i),
                )
            )
        db.commit()


def _run_backfill(index, batch=2, force=False):
    cursor, calls, indexed = None, 0, 0
    while True:
        out = index.backfill(batch=batch, cursor=cursor, force=force)
        assert set(out) >= {"indexed", "cursor", "done"}
        indexed += out["indexed"]
        cursor = out["cursor"]
        calls += 1
        if out["done"] or calls > 60:
            break
    assert calls <= 60, "backfill did not terminate"
    return indexed


def test_backfill_is_chunked_resumable_and_complete(engine, index):
    _seed_history(engine)
    indexed = _run_backfill(index, batch=2)
    assert indexed == 5 * 2 + 3  # 10 thread messages + 3 sessions
    stats = index.stats()
    assert stats["docs"] == 13 and stats["threads"] == 5 and stats["sessions"] == 3
    assert [h.ref for h in index.search("question 3")] == ["chat_3"]
    assert [h.ref for h in index.search("legacy task 1")] == ["session_1"]
    # The daemon-owned thread is classified "comm", the rest "chat".
    assert {h.kind for h in index.search("legacy", kinds=["comm"], limit=50)} == {"comm"}
    assert index.search("legacy", kinds=["comm"], limit=50)[0].ref == "chat_0"


def test_backfill_is_idempotent(engine, index):
    _seed_history(engine)
    first = _run_backfill(index, batch=3)
    docs = index.stats()["docs"]
    second = _run_backfill(index, batch=3)
    assert first > 0
    assert second == 0, "a second full pass must index nothing new"
    assert index.stats()["docs"] == docs
    assert _counts(engine) == (docs, docs)


def test_backfill_force_reindexes(engine, index):
    _seed_history(engine)
    _run_backfill(index, batch=3)
    docs = index.stats()["docs"]
    assert _run_backfill(index, batch=3, force=True) == docs
    assert index.stats()["docs"] == docs
    assert _counts(engine) == (docs, docs)


def test_backfill_picks_up_where_the_cursor_left_off(engine, index):
    _seed_history(engine, threads=6, sessions=0)
    first = index.backfill(batch=2)
    assert first["done"] is False and first["cursor"].startswith("chat|")
    assert index.stats()["threads"] == 2
    second = index.backfill(batch=2, cursor=first["cursor"])
    assert index.stats()["threads"] == 4
    assert second["cursor"] != first["cursor"]
    # A stale cursor is tolerated, not fatal.
    assert isinstance(index.backfill(batch=2, cursor="garbage")["indexed"], int)
    assert index.backfill(batch=2, cursor="chat|not-a-date|zzz")["done"] in (True, False)


def test_backfill_on_an_empty_database_finishes(index):
    out = _run_backfill(index, batch=10)
    assert out == 0
    assert index.stats()["docs"] == 0


def test_backfill_indexes_round_tables(engine, index):
    from iron_jarvis.agents.threads import AgentThreadRecord

    with session_scope(engine) as db:
        db.add(
            AgentThreadRecord(
                id="athr_1",
                title="Panel",
                messages_json=json.dumps(
                    [
                        {"who": "user", "content": "debate the roadmap"},
                        {"who": "builtin:critic", "content": "the roadmap is overloaded"},
                    ]
                ),
                created_at=NOW,
            )
        )
        db.commit()
    _run_backfill(index, batch=5)
    hits = index.search("roadmap", kinds=["round"])
    assert {h.ref for h in hits} == {"athr_1"}
    assert {h.role for h in hits} == {"user", "builtin:critic"}


# ---------------------------------------------------------- basic fallback ----
def test_basic_mode_has_the_same_shape_and_band(corpus, monkeypatch):
    """Monkeypatched to "no FTS5 in this SQLite build". Every consumer must be
    indifferent to which engine answered."""
    fts_hits = corpus.search("election", limit=10)
    monkeypatch.setattr(corpus, "available", lambda: False)
    assert corpus.mode == "basic"
    assert corpus.stats()["mode"] == "basic"
    assert corpus.stats()["available"] is False

    basic = corpus.search("election", limit=10)
    assert basic and all(isinstance(h, SearchHit) for h in basic)
    for h in basic:
        assert set(h.as_dict()) == set(fts_hits[0].as_dict())
        assert 0.0 <= h.score <= 1.0 and SCORE_FLOOR <= h.score <= SCORE_CEIL
        assert h.kind and h.ref and h.at is not None
        assert "[" in h.snippet and "]" in h.snippet
    assert [h.score for h in basic] == sorted((h.score for h in basic), reverse=True)


def test_basic_mode_filters_identically(corpus, monkeypatch):
    monkeypatch.setattr(corpus, "available", lambda: False)
    assert {h.kind for h in corpus.search("election", kinds=["comm"])} == {"comm"}
    assert {h.ref for h in corpus.search("election", project_id="proj_tax")} == {
        "chat_tax",
        "session_1",
    }
    assert {h.ref for h in corpus.search("election", after=NOW - timedelta(days=7))} == {
        "comm_val",
        "session_1",
    }
    assert len(corpus.search("election", limit=1)) == 1


def test_basic_mode_is_honestly_degraded_not_broken(corpus, monkeypatch):
    """No stemming without FTS5 — an honest miss beats a fake hit. But the
    plural still matches, and writes keep working while degraded."""
    monkeypatch.setattr(corpus, "available", lambda: False)
    assert [h.ref for h in corpus.search("elections")] == ["chat_tax"]
    assert corpus.sync_thread(
        "t_new", "chat", "New", "", [{"role": "user", "content": "written while degraded"}]
    ) == 1
    assert [h.ref for h in corpus.search("degraded")] == ["t_new"]
    assert corpus.drop_thread("t_new") == 1


def test_basic_mode_ranks_whole_words_above_substrings(index, monkeypatch):
    index.sync_thread(
        "t1",
        "chat",
        "T",
        "",
        [
            {"role": "user", "content": "the vote was a landslide"},
            {"role": "user", "content": "devoted to the cause"},
        ],
    )
    monkeypatch.setattr(index, "available", lambda: False)
    hits = index.search("vote")
    assert len(hits) == 2
    assert "landslide" in hits[0].snippet
    assert hits[0].score > hits[1].score


# =============================================================================
# Reviewer regressions (adversarial pass). Each one below either pins a defect
# that was FOUND AND FIXED, or closes a hole a mutation walked straight through.
# =============================================================================


# -- the substrate's own schema: the silent-death defect ----------------------
def test_search_doc_table_is_registered_before_the_reconciler():
    """``searchdocrecord`` must be in ``SQLModel.metadata`` while
    ``_reconcile_additive_columns`` runs, on a COLD interpreter.

    Nothing else imports ``search.models`` at daemon boot. Before the fix the
    only importer was ``_ensure_fts`` — which runs AFTER the reconciler — so the
    doc table was invisible to both ``create_all`` and the additive-column
    self-heal. Run in a subprocess because import state is process-global and
    this whole file imports ``iron_jarvis.search`` at the top.
    """
    code = (
        "import tempfile, pathlib\n"
        "from sqlmodel import SQLModel\n"
        "import iron_jarvis.core.db as db\n"
        "seen = {}\n"
        "orig = db._reconcile_additive_columns\n"
        "def spy(engine):\n"
        "    seen['ok'] = 'searchdocrecord' in SQLModel.metadata.tables\n"
        "    return orig(engine)\n"
        "db._reconcile_additive_columns = spy\n"
        "db.init_db(db.make_engine(pathlib.Path(tempfile.mkdtemp()) / 'a.db'))\n"
        "print('REGISTERED', seen.get('ok'))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
    )
    assert "REGISTERED True" in out.stdout, out.stdout + out.stderr[-2000:]


def test_search_doc_table_self_heals_a_new_column(tmp_path):
    """The consequence of the bug above, end to end: an existing DB whose
    ``searchdocrecord`` predates a column must be reconciled at the next boot.

    Unreconciled, EVERY index write fails with "no such column" — and index
    writes swallow their exceptions by design, so history search would index
    NOTHING, forever, without one visible error. Silent death is the worst
    failure mode this substrate has, and three other pairs sit on top of it.
    """
    import sqlite3

    path = tmp_path / "old.db"
    eng = make_engine(path)
    init_db(eng)
    eng.dispose()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE searchdocrecord DROP COLUMN title")  # "older schema"
    con.commit()
    assert "title" not in {r[1] for r in con.execute('PRAGMA table_info("searchdocrecord")')}
    con.close()

    healed = make_engine(path)
    init_db(healed)  # the next boot
    idx = SearchIndex(healed)
    assert idx.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "revived"}]) == 1
    assert [h.ref for h in idx.search("revived")] == ["t1"]
    assert idx.stats()["docs"] == 1


def test_ensure_fts_failure_cannot_brick_boot(tmp_path, monkeypatch):
    """Same bare-``try`` contract as ``_ensure_indexes``: a missing/broken table
    must never take the daemon down at boot."""
    import iron_jarvis.core.db as dbmod

    # Both halves broken at once: the model module refuses to import AND the
    # FTS5 DDL is invalid. Boot must still complete.
    monkeypatch.setattr(dbmod, "_FTS_DDL", "CREATE VIRTUAL TABLE x USING no_such_module(y)")
    monkeypatch.setitem(sys.modules, "iron_jarvis.search", None)  # -> ImportError
    dbmod._register_search_models()  # never raises on its own either
    init_db(make_engine(tmp_path / "brick.db"))  # must not raise


def test_the_substrate_survives_a_quarantined_database(tmp_path):
    """``open_db`` quarantines a corrupt file and boots a FRESH one — the vtable
    is recreated empty and the index works from zero."""
    from iron_jarvis.core.db import open_db

    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 20)
    idx = SearchIndex(open_db(path))
    assert idx.available() is True and idx.stats()["docs"] == 0
    idx.sync_thread("t1", "chat", "T", "", [{"role": "user", "content": "post quarantine"}])
    assert [h.ref for h in idx.search("quarantine")] == ["t1"]


def test_init_db_orders_the_search_substrate_correctly():
    import inspect

    import iron_jarvis.core.db as dbmod

    src = inspect.getsource(dbmod.init_db)
    assert src.index("_register_search_models(") < src.index("create_all")
    assert src.index("_reconcile_additive_columns(") < src.index("_ensure_indexes(")
    assert src.index("_ensure_indexes(") < src.index("_ensure_fts(")


# -- the caller-transaction contract Pair S2 depends on -----------------------
def test_an_index_failure_never_breaks_the_write_it_shadows(engine, index, monkeypatch):
    """The load-bearing promise to S2: sync runs INSIDE the chat-save
    transaction, so an index failure there must not poison that transaction —
    the user's message has to be saved even when search is broken."""

    def boom(self, db, clause, params, rows):
        db.execute(text("SELECT * FROM a_table_that_does_not_exist"))

    monkeypatch.setattr(SearchIndex, "_replace", boom)
    with session_scope(engine) as db:
        rec = ChatThreadRecord(title="Must survive")
        db.add(rec)
        db.flush()
        thread_id = rec.id
        assert index.sync_thread(thread_id, "chat", "T", "", [{"content": "x"}], db=db) == 0
        db.commit()  # the write being shadowed — must still land
    with session_scope(engine) as db:
        assert db.get(ChatThreadRecord, thread_id) is not None


def test_sync_inside_a_caller_txn_survives_a_missing_fts_table(engine):
    """The no-FTS5-build shape of the same promise: the shadow statements fail,
    the doc row and the caller's row both still commit."""
    index = SearchIndex(engine)
    index.sync_thread("t0", "chat", "T", "", [{"role": "user", "content": "seed"}])
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE searchdoc_fts"))
    degraded = SearchIndex(engine)
    degraded._available = False  # "this SQLite build has no FTS5"
    with session_scope(engine) as db:
        rec = ChatThreadRecord(title="Chat save")
        db.add(rec)
        db.flush()
        thread_id = rec.id
        assert (
            degraded.sync_thread(
                thread_id, "chat", "T", "", [{"role": "user", "content": "the save payload"}], db=db
            )
            == 1
        )
        degraded.drop_thread("t0", db=db)
        db.commit()
    with session_scope(engine) as db:
        assert db.get(ChatThreadRecord, thread_id) is not None
    assert [h.ref for h in degraded.search("payload")] == [thread_id]


def test_a_crash_mid_sync_leaves_the_index_all_or_nothing(engine, index, monkeypatch):
    """Kill the sync AFTER the delete and BEFORE the insert. Both with and
    without a caller transaction, the index must be exactly what it was."""
    seed = [{"role": "user", "content": f"before {i}"} for i in range(4)]
    index.sync_thread("t1", "chat", "T", "", seed)
    assert _counts(engine) == (4, 4)

    def half_write(self, db, clause, params, rows):
        doomed = [
            r[0]
            for r in db.execute(
                text(f"SELECT n FROM searchdocrecord WHERE {clause}"), params
            ).all()
        ]
        if doomed:
            SearchIndex._fts_delete(db, doomed)
            db.execute(text(f"DELETE FROM searchdocrecord WHERE {clause}"), params)
        raise RuntimeError("simulated crash mid-sync")

    monkeypatch.setattr(SearchIndex, "_replace", half_write)
    assert index.sync_thread("t1", "chat", "T", "", [{"content": "after"}]) == 0
    assert _counts(engine) == (4, 4), "the index's own transaction must not half-commit"

    with session_scope(engine) as db:
        index.sync_thread("t1", "chat", "T", "", [{"content": "after"}], db=db)
        db.rollback()
    assert _counts(engine) == (4, 4), "the caller's rollback must restore both tables"
    monkeypatch.undo()
    assert len(index.search("before")) == 4


def test_a_raw_delete_outside_the_api_yields_no_phantom_hits(engine, index):
    """The exact risk own-content FTS5 was chosen to survive: some other code
    path (``prune_events``' bulk delete, a migration, a manual fix) deletes doc
    rows without going through this module. The stale shadow rows must not
    produce a hit for a row that no longer exists — and ``rebuild()`` must heal
    the leak."""
    index.sync_thread("t1", "chat", "T1", "", [{"role": "user", "content": "keeper alpha"}])
    index.sync_thread(
        "t2",
        "chat",
        "T2",
        "",
        [{"role": "user", "content": f"keeper beta {i}"} for i in range(4)],
    )
    assert _counts(engine) == (5, 5)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM searchdocrecord WHERE thread_id='t2'"))
    assert _counts(engine) == (1, 5)  # 4 orphaned shadow rows
    # The INNER JOIN is what makes an orphan invisible instead of a crash.
    assert [h.ref for h in index.search("keeper")] == ["t1"]
    assert index.search("beta") == []
    assert index.stats()["docs"] == 1
    assert index.rebuild() == 1
    assert _counts(engine) == (1, 1)
    assert [h.ref for h in index.search("keeper")] == ["t1"]


# -- score band: the numbers Pair S2 feeds into MemoryFabric.recall -----------
def test_the_last_hit_of_a_full_page_still_clears_the_fabric_floor(index):
    """``recall`` drops anything at/below ``min_score`` (0.05) and sorts ACROSS
    sources. The floor is structural (0.35), so even the 200th hit of a
    maximally-decayed page stays an order of magnitude above it — a real match
    can never be silently filtered out."""
    index.sync_thread(
        "t1",
        "chat",
        "T",
        "",
        [{"role": "user", "content": f"quarterly revenue line {i}"} for i in range(250)],
    )
    twenty = index.search("revenue", limit=20)
    assert len(twenty) == 20
    assert twenty[-1].score > 0.05 and twenty[-1].score >= SCORE_FLOOR

    full = index.search("revenue", limit=MAX_LIMIT)
    assert len(full) == MAX_LIMIT
    assert min(h.score for h in full) > 0.05
    assert min(h.score for h in full) >= SCORE_FLOOR

    # And the WORST case the transform can produce — the least relevant hit on
    # a maximally long page (ratio -> 0, decay -> 1/10.95). Only the FLOOR keeps
    # this above ``min_score``; drop the floor to 0 and the fabric starts
    # silently discarding real matches. Pin the constant, not just the band.
    assert SCORE_FLOOR == 0.35 and SCORE_CEIL == 0.95
    worst = _normalize_scores([1.0] + [0.0] * (MAX_LIMIT - 1))
    assert worst[-1] > 0.05 and worst[-1] == SCORE_FLOOR


def test_a_single_hit_corpus_scores_the_ceiling(index):
    """ratio 1.0 x decay 1.0 -> exactly SCORE_CEIL: the best possible lexical
    hit, still under a perfect cosine 1.0 so the fabric's cross-source sort
    keeps its headroom."""
    index.sync_thread("s1", "chat", "T", "", [{"role": "user", "content": "solitary unicorn"}])
    only = index.search("unicorn")
    assert len(only) == 1 and only[0].score == SCORE_CEIL == 0.95


def test_ranking_and_snippets_are_stable_when_every_row_ties(index):
    """``MemoryFabric._dedupe`` keys on ``(source, ref)`` AND
    ``snippet[:120].lower()``. Rows whose BM25 ties exactly are the worst case:
    a nondeterministic tiebreak would make recall results FLAP between calls.
    ``ORDER BY bm, at DESC, n`` ends in a unique column, so it cannot."""
    index.sync_thread(
        "t1",
        "chat",
        "T",
        "",
        [{"role": "user", "content": "identical body text for tie breaking"} for _ in range(30)],
    )
    runs = [
        [(h.seq, h.score, h.snippet, h.ref) for h in index.search("identical", limit=30)]
        for _ in range(5)
    ]
    assert all(r == runs[0] for r in runs)
    # SQLite happens to be stable for one plan over one dataset, so repeating the
    # query cannot DISTINGUISH a total order from a lucky one. The guarantee is
    # structural: the ORDER BY has to end in a UNIQUE column.
    import inspect

    sql_src = inspect.getsource(SearchIndex._match)
    assert "ORDER BY bm ASC, d.at DESC, d.n ASC" in sql_src, (
        "the rank tiebreak must end in d.n (the rowid alias) — without a unique "
        "final key, equal-BM25 rows may reorder between plans and MemoryFabric "
        "recall results flap"
    )
    assert runs[0][-1][1] > 0.05
    assert [s for _, s, _, _ in runs[0]] == sorted((s for _, s, _, _ in runs[0]), reverse=True)


# -- query hardening: what the mutations walked through ----------------------
def test_control_characters_are_stripped_not_merely_swallowed(corpus):
    """The hostile battery only proves "no exception" — the catch-all satisfies
    that even with the strip DELETED. This proves the FIX: a NUL (or any C0
    control) between words leaves the query ANSWERABLE, returning the same rows
    the clean query returns."""
    clean = [h.ref for h in corpus.search("election deadline")]
    assert clean, "precondition"
    for hostile in (
        "\x00election deadline",
        "election\x00 deadline",
        "election \x01\x1f deadline",
        "election deadline\x7f",
    ):
        assert [h.ref for h in corpus.search(hostile)] == clean


def test_the_phrase_tier_stands_on_its_own_without_the_prefix_tier(corpus, monkeypatch):
    """``s-corp`` is a syntax error verbatim, and BOTH tier 2 (quoted phrase)
    and tier 3 (prefix retry) can rescue it — so a tier-2 test passes even with
    tier 2 deleted. Disable tier 3 and tier 2 must still answer."""
    monkeypatch.setattr("iron_jarvis.search.index._prefix_expr", lambda q: "")
    assert {h.ref for h in corpus.search("s-corp")} == {"chat_tax", "session_1"}
    assert isinstance(corpus.search("S-corp AND (election"), list)


def test_the_prefix_tier_stands_on_its_own_without_the_phrase_tier(corpus, monkeypatch):
    monkeypatch.setattr("iron_jarvis.search.index._phrase_expr", lambda q: "")
    assert [h.ref for h in corpus.search("storyb")] == ["chat_video"]


def test_operator_only_and_bare_wildcard_queries_are_honest_misses(corpus):
    """Not a crash, not a 500, and NOT a full-corpus dump either: a query with
    no searchable term returns nothing rather than everything."""
    for query in ("AND OR NEAR", "*", "***", "OR", "NOT", "^", "()"):
        assert corpus.search(query) == [], query
    # A real prefix, though, is a real query.
    assert corpus.search("a*")
    assert all(0.05 < h.score <= SCORE_CEIL for h in corpus.search("a*"))


def test_an_over_long_query_is_truncated_not_rejected(corpus):
    """MAX_QUERY_CHARS (512) is a clamp, not a wall: a paste-length query is
    TRUNCATED and the surviving prefix is searched normally, rather than being
    rejected or handed to SQLite whole.

    Note the honest consequence, worth knowing before S3 exposes this to a
    model: FTS5's implicit operator is AND, so the truncated remainder still has
    to match. ``"election " + "x"*5000`` finds nothing (there is no ``xxx…``
    token in the corpus) — that is a real miss, not an error."""
    assert corpus.search("election " * 200)  # 512 chars of a real term: matches
    assert corpus.search("election " + "x" * 5000) == []  # honest AND miss
    assert isinstance(corpus.search("x" * 600), list)


def test_bidi_and_cjk_queries_round_trip(index):
    index.sync_thread(
        "t1",
        "chat",
        "T",
        "",
        [
            {"role": "user", "content": "the deadline is jie zhi ri qi for the xuan ju"},
            {"role": "user", "content": "marhaban bialealam from the client"},
        ],
    )
    index.sync_thread(
        "t2",
        "chat",
        "T2",
        "",
        [
            {"role": "user", "content": "截止日期 for the 选举"},
            {"role": "user", "content": "مرحبا بالعالم"},
        ],
    )
    assert [h.ref for h in index.search("选举")] == ["t2"]
    assert [h.ref for h in index.search("مرحبا")] == ["t2"]


# -- backfill: keyset boundaries and phase isolation -------------------------
def test_backfill_page_boundary_with_identical_timestamps(engine, index):
    """Three threads sharing ONE ``created_at`` with ``batch=2`` puts the page
    boundary INSIDE the tie group. An offset cursor (or a keyset missing its id
    tiebreak) skips or duplicates here; the ``(created_at, id)`` keyset must
    not."""
    stamp = NOW - timedelta(days=1)
    with session_scope(engine) as db:
        for i in range(3):
            db.add(
                ChatThreadRecord(
                    id=f"tie_{i}",
                    title=f"Tie {i}",
                    owner="user",
                    messages_json=json.dumps([{"role": "user", "content": f"tiebreak body {i}"}]),
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        db.add(
            ChatThreadRecord(
                id="zz_last",
                title="Last",
                owner="user",
                messages_json=json.dumps([{"role": "user", "content": "tiebreak body last"}]),
                created_at=stamp + timedelta(seconds=1),
            )
        )
        db.commit()
    assert _run_backfill(index, batch=2) == 4
    assert sorted(h.ref for h in index.search("tiebreak", limit=50)) == [
        "tie_0",
        "tie_1",
        "tie_2",
        "zz_last",
    ]
    assert _counts(engine) == (4, 4)


def test_backfill_survives_a_whole_page_of_identical_timestamps(engine, index):
    stamp = NOW - timedelta(days=1)
    with session_scope(engine) as db:
        for i in range(7):
            db.add(
                ChatThreadRecord(
                    id=f"same_{i}",
                    title=f"S{i}",
                    owner="user",
                    messages_json=json.dumps([{"role": "user", "content": f"sametime body {i}"}]),
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        db.commit()
    assert _run_backfill(index, batch=2) == 7
    assert len(index.search("sametime", limit=50)) == 7
    assert _counts(engine) == (7, 7)


def test_a_failing_phase_does_not_starve_the_phases_after_it(engine, index, monkeypatch):
    """A page the chat phase cannot even LIST used to park the WHOLE backfill —
    and because the daemon resumes at cursor ``None`` (the first phase), it
    re-parked every hour and sessions/round tables were NEVER indexed at all. A
    broken phase must skip forward instead."""
    _seed_history(engine, threads=2, sessions=3)
    real_keyset = SearchIndex._keyset

    def poisoned(self, model, when, last, batch):
        if model is ChatThreadRecord:
            raise RuntimeError("this page cannot be deserialized")
        return real_keyset(self, model, when, last, batch)

    monkeypatch.setattr(SearchIndex, "_keyset", poisoned)
    assert _run_backfill(index, batch=2) == 3  # the 3 sessions still get indexed
    assert index.stats()["sessions"] == 3
    # ...and the periodic resume keeps retrying the poisoned phase without wedging.
    out = index.backfill(batch=2, cursor=None)
    assert out["done"] is False and out.get("error") is True
    monkeypatch.undo()
    assert _run_backfill(index, batch=2) == 4  # the healed phase's 2x2 messages
    assert index.stats()["threads"] == 2


def test_backfill_return_shape_is_stable_including_on_error(engine, index, monkeypatch):
    """Pair S3's loop reads these keys into ``loop_health``."""
    for out in (index.backfill(batch=5), index.backfill(batch=5, cursor="round||")):
        assert set(out) >= {"indexed", "scanned", "cursor", "done"}
        assert isinstance(out["indexed"], int) and isinstance(out["done"], bool)

    def explode(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(SearchIndex, "_backfill", explode)
    out = index.backfill()
    assert out["done"] is True and out["error"] is True and out["indexed"] == 0


# -- fallback parity: consumers must be indifferent --------------------------
def test_basic_mode_matches_fts5_filter_for_filter(corpus, monkeypatch):
    """The full battery run twice, once per engine. Not "the same shape" — the
    same ANSWERS for every filter S2/S3/S4 can send."""
    probes = {
        "kind": lambda: [h.ref for h in corpus.search("election", kinds=["chat"])],
        "kinds": lambda: [h.ref for h in corpus.search("election", kinds=["comm", "session"])],
        "nokind": lambda: [h.ref for h in corpus.search("election", kinds=["nope"])],
        "project": lambda: [h.ref for h in corpus.search("election", project_id="proj_tax")],
        "noproject": lambda: [h.ref for h in corpus.search("election", project_id="proj_x")],
        "after": lambda: [h.ref for h in corpus.search("election", after=NOW - timedelta(days=7))],
        "before": lambda: [
            h.ref for h in corpus.search("election", before=NOW - timedelta(days=100))
        ],
        "window": lambda: [
            h.ref
            for h in corpus.search(
                "election", after=NOW - timedelta(days=7), before=NOW - timedelta(days=3)
            )
        ],
        "iso": lambda: [
            h.ref for h in corpus.search("election", after=(NOW - timedelta(days=7)).isoformat())
        ],
        "limit1": lambda: len(corpus.search("election", limit=1)),
        "limit0": lambda: len(corpus.search("election", limit=0)),
        "clamp": lambda: len(corpus.search("election", limit=99_999)),
    }
    fts = {k: fn() for k, fn in probes.items()}
    monkeypatch.setattr(corpus, "available", lambda: False)
    basic = {k: fn() for k, fn in probes.items()}
    for key in probes:
        left, right = fts[key], basic[key]
        if isinstance(left, list):
            assert sorted(left) == sorted(right), f"{key}: fts5={left} basic={right}"
        else:
            assert left == right, key
    for hit in corpus.search("election", limit=50):
        assert SCORE_FLOOR <= hit.score <= SCORE_CEIL
        assert "[" in hit.snippet and "]" in hit.snippet
        assert hit.at is not None and hit.at.tzinfo is not None
        assert isinstance(hit.seq, int) and hit.kind and hit.ref


def test_basic_mode_snippets_are_deterministic_across_calls(corpus, monkeypatch):
    monkeypatch.setattr(corpus, "available", lambda: False)
    a = [h.snippet for h in corpus.search("election deadline", limit=20)]
    b = [h.snippet for h in corpus.search("election deadline", limit=20)]
    assert a == b and a and all("[" in s for s in a)


def test_the_presence_pin_would_fail_without_fts5(index, monkeypatch):
    """Meta-test: prove the capability pin is not a tautology. With
    ``available()`` forced False the pin's own assertions must break."""
    monkeypatch.setattr(SearchIndex, "available", lambda self: False)
    degraded = SearchIndex(index.engine)
    with pytest.raises(AssertionError):
        test_fts5_is_actually_available_and_stems(degraded)


# -- lock discipline ---------------------------------------------------------
def test_the_index_lock_is_always_the_inner_lock(engine, index):
    """Deadlock proof by construction: ``SearchIndex`` never invokes caller
    code, so its RLock can only ever be acquired INSIDE a caller's lock (e.g.
    ``CommThreadStore._lock``), never the other way round. Exercised here as a
    real interleave: one thread holds an outer lock and syncs inside its own
    transaction while another syncs and searches freely."""
    import threading

    outer = threading.Lock()
    errors: list[BaseException] = []

    def outer_first():
        try:
            for i in range(15):
                with outer, session_scope(engine) as db:
                    index.sync_thread(
                        f"a{i}", "chat", "A", "", [{"role": "user", "content": f"aaa {i}"}], db=db
                    )
                    db.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def index_only():
        try:
            for i in range(15):
                index.sync_thread(
                    f"b{i}", "chat", "B", "", [{"role": "user", "content": f"bbb {i}"}]
                )
                index.search("aaa")
                index.stats()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=outer_first), threading.Thread(target=index_only)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "deadlock between the two locks"
    assert not errors
    assert _counts(engine) == (30, 30)


# -- DECISION 4: which write path takes which exclusion ----------------------
def test_the_write_lock_is_taken_only_on_the_caller_transaction_path(
    engine, index, monkeypatch
):
    """The landmine, pinned from both sides.

    A SELF-OWNED write used to hold ``self._lock`` across its own SQLite
    transaction. Every live write seam does the opposite — it is already inside
    a ``session_scope`` when it calls in with ``db=`` and takes the same lock —
    so ONE shared index made the backfill loop and a chat save an ABBA cycle
    resolved only by SQLite's 30s ``busy_timeout``.

    Now: ``db=`` takes the lock (the caller's transaction may not have begun a
    write yet, so nothing else closes the read-modify-write window), and the
    self-owned path takes NO Python lock and instead holds SQLite's writer slot
    from before its first SELECT (``BEGIN IMMEDIATE``) — strictly stronger, and
    impossible to invert against a caller's transaction.
    """
    seen: list[tuple[str, bool, bool]] = []
    real_replace = SearchIndex._replace

    def spy(self, db, clause, params, rows):
        driver = getattr(db.connection().connection, "driver_connection", None)
        who = str(params.get("tid") or params.get("v") or "?")
        seen.append((who, self._lock._is_owned(), bool(getattr(driver, "in_transaction", False))))
        return real_replace(self, db, clause, params, rows)

    monkeypatch.setattr(SearchIndex, "_replace", spy)

    with session_scope(engine) as db:
        index.sync_thread(
            "riding", "chat", "T", "", [{"role": "user", "content": "rides a caller"}], db=db
        )
        db.commit()
    index.sync_thread("owned", "chat", "T", "", [{"role": "user", "content": "owns it"}])

    assert [(name, owned) for name, owned, _ in seen] == [("riding", True), ("owned", False)]
    # ...and the self-owned write is already INSIDE its transaction when it reads
    # the rowids it is about to delete — BEGIN IMMEDIATE doing the job the lock
    # used to only half-do.
    assert seen[1][2] is True, "the self-owned write did not open an immediate transaction"
    assert _counts(engine) == (2, 2)


def test_the_capability_probe_never_queues_behind_the_write_lock(engine):
    """``available()`` can fire from inside a caller's OPEN transaction (from
    ``_replace``). If it waited on the WRITE lock it would re-create the very
    inversion DECISION 4 removes, by the back door — so the probe has its own."""
    import threading

    cold = SearchIndex(engine)
    assert cold._available is None
    finished = threading.Event()

    def probe():
        cold.available()
        finished.set()

    with cold._lock:  # stand in for a db= writer holding the write lock
        t = threading.Thread(target=probe, daemon=True)
        t.start()
        assert finished.wait(15), "available() blocked on the write lock"
    t.join(timeout=15)
    assert cold._available is True


def test_a_backfill_sized_write_cannot_stall_a_chat_save(engine, index):
    """THE benchmark: concurrent chat-save-shaped writes (``db=``, inside an
    outer ``session_scope`` under the real ``CONVERSATION_WRITE_LOCK``) racing a
    self-owned backfill-shaped writer on the SAME index instance.

    The saver FLUSHES before it syncs, which is the whole point: that is the
    moment it takes SQLite's single writer slot, so its order is writer → index
    lock while a self-owned write's order was index lock → writer. (Not every
    seam flushes today — ``routes/chat.py`` deliberately does not — but
    ``prune_events`` does on its second ``drop_refs`` page, and any caller that
    syncs twice in one transaction does. One flush is all it takes.)

    MEASURED on this bench, same machine, same parameters:

    * before — the FIRST save took **165,169 ms** (a Python lock is not fair, so
      the saver starved for as long as the backfiller kept re-taking it), and 6%
      of the self-owned writes were lost to "database is locked";
    * after — p50 **2.0 ms**, p95 **34.1 ms**, max **112 ms**, 75 saves in
      0.2 s wall, zero writes lost on either side.

    THE THRESHOLD IS RELATIVE, and that is a correction (v1.164.0). It used to
    be an absolute ``p95 < 200ms``, justified as "~6x looser than the
    measurement and ~1000x tighter than the pathology: they cannot flake". It
    flaked — CI measured p95 211ms on a shared runner and failed the build,
    while the pathology it guards is 165 SECONDS. An absolute millisecond bar
    measures the HARDWARE as much as the code, and this bench's own numbers
    (0.2s wall for 75 saves) came from a fast desktop.

    So the same saver loop runs TWICE: once alone, once against the backfiller.
    The invariant is that the backfill does not stall a save, which is a
    statement about the RATIO — and the ratio has enormous discriminating power
    here, because the pathology is ~80,000x, not 6x. A slow runner moves both
    numbers together and the test stays honest.
    """
    import statistics
    import threading
    import time

    from iron_jarvis.core.db import CONVERSATION_WRITE_LOCK

    per_saver = 25
    savers = 3
    latencies: list[float] = []
    written: list[int] = []
    errors: list[BaseException] = []
    stop = threading.Event()

    def backfiller():
        """Exactly the shape ``backfill`` issues: self-owned, no caller db."""
        try:
            i = 0
            while not stop.is_set() and i < 600:
                written.append(
                    index.sync_thread(
                        f"bf_{i}",
                        "chat",
                        "Legacy",
                        "",
                        [{"role": "user", "content": f"legacy backfill body {i}"}],
                    )
                )
                i += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def saver(tag: str, into: list[float]):
        """Exactly the shape ``PUT /chat/threads/{id}`` issues."""
        try:
            for i in range(per_saver):
                t0 = time.perf_counter()
                with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
                    db.add(ChatThreadRecord(id=f"save_{tag}_{i}", title="Live save"))
                    db.flush()  # from here the seam holds SQLite's writer slot
                    index.sync_thread(
                        f"save_{tag}_{i}",
                        "chat",
                        "Live save",
                        "",
                        [{"role": "user", "content": f"live save body {tag} {i}"}],
                        db=db,
                    )
                    db.commit()
                into.append((time.perf_counter() - t0) * 1000.0)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def run_savers(prefix: str, into: list[float]) -> None:
        threads = [
            threading.Thread(target=saver, args=(f"{prefix}{n}", into))
            for n in range(savers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)
        assert not any(t.is_alive() for t in threads), "a writer never finished"

    # BASELINE: the identical loop with no backfiller, so the comparison below
    # is against THIS machine rather than against the desktop this was written
    # on. Runs first so a cold index/page cache penalises the baseline, never
    # the contended run — that direction can only make the test stricter.
    baseline: list[float] = []
    run_savers("base", baseline)
    assert not errors, errors
    assert len(baseline) == per_saver * savers

    bf = threading.Thread(target=backfiller, daemon=True)
    bf.start()
    run_savers("hot", latencies)
    stop.set()
    bf.join(timeout=300)

    assert not errors, errors
    assert not bf.is_alive(), "the backfill writer never finished"
    assert len(latencies) == per_saver * savers

    def pct(values: list[float], q: float) -> float:
        ordered = sorted(values)
        return ordered[int(q * (len(ordered) - 1))]

    base_p95 = pct(baseline, 0.95)
    ordered = sorted(latencies)
    p50 = statistics.median(ordered)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    detail = (
        f"contended p50={p50:.1f}ms p95={p95:.1f}ms max={ordered[-1]:.1f}ms; "
        f"alone p95={base_p95:.1f}ms max={max(baseline):.1f}ms"
    )
    # A floor of 200ms so a machine where an uncontended save is sub-millisecond
    # cannot make the ratio absurdly strict; above that it scales with the box.
    # The pathology was ~80,000x, so 50x cannot pass while the inversion exists
    # and cannot fail because a CI runner is busy.
    limit = max(200.0, base_p95 * 50.0)
    assert p95 < limit, (
        f"chat saves are stalling behind the backfill (limit {limit:.0f}ms): {detail}"
    )
    assert ordered[-1] < max(2000.0, max(baseline) * 100.0), (
        f"a chat save starved on the index lock: {detail}"
    )

    # Neither side lost a write: every save is searchable, and no self-owned
    # write returned 0 (which is how SearchIndex reports a swallowed failure).
    assert written and 0 not in written, "the backfill-shaped writer lost documents"
    # BOTH runs' saves are searchable — the baseline pass adds its own set, and
    # counting only one would hide a write lost in the other.
    assert (
        len(index.search("live save body", limit=MAX_LIMIT)) == 2 * per_saver * savers
    )


# -- backfill: a poison ROW must not cost the rest of its PHASE --------------
def test_a_poison_row_does_not_abandon_the_rest_of_its_phase(engine, index, monkeypatch):
    """A page that cannot be LISTED used to skip to the NEXT phase — which reads
    as "contained" and is really permanent starvation: every later sweep runs
    the phase guard again from the same cursor and skips forward again, so every
    row AFTER the bad one is never indexed, silently, forever.

    The bad row must be isolated (retry at ``batch=1``), logged, and STEPPED
    OVER so the rest of its phase still lands."""
    _seed_history(engine, threads=5, sessions=3)
    real_keyset = SearchIndex._keyset

    def poisoned(self, model, when, last, batch):
        rows = real_keyset(self, model, when, last, batch)
        if any(getattr(r, "id", "") == "chat_2" for r in rows):
            raise RuntimeError("chat_2 cannot be deserialized")
        return rows

    monkeypatch.setattr(SearchIndex, "_keyset", poisoned)
    indexed = _run_backfill(index, batch=2)

    # 4 surviving threads x 2 messages + 3 sessions. The old behaviour indexed
    # chat_0/chat_1 and then abandoned chat_3 and chat_4 on every future sweep.
    assert indexed == 4 * 2 + 3
    found = {h.ref for h in index.search("legacy", limit=MAX_LIMIT)}
    assert {"chat_3", "chat_4"} <= found, "the phase was abandoned after the poison row"
    assert "chat_2" not in found
    assert index.stats()["sessions"] == 3

    # A second sweep stays stable: the poison row is skipped again, nothing is
    # double-indexed, and the phase still completes.
    assert _run_backfill(index, batch=2) == 0
    assert {h.ref for h in index.search("legacy", limit=MAX_LIMIT)} == found

    # Once the row heals, the ordinary keyset picks it up with no repair step.
    monkeypatch.undo()
    assert _run_backfill(index, batch=2) == 2
    assert "chat_2" in {h.ref for h in index.search("legacy", limit=MAX_LIMIT)}
