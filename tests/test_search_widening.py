"""Tier 4 — the WIDENING, and the fact that every consumer now inherits it.

THE FINDING THIS FILE EXISTS FOR. FTS5's implicit operator between bare terms is
AND, so every tier of ``SearchIndex.search``'s original ladder is conjunctive: a
hit has to contain EVERY word the user typed. Nobody's stored message contains
"what did we say about the rental property depreciation" in full. Measured
against one seeded conversation and seven realistic ways of asking for it, the
strict ladder found it **2 times out of 7** — only the two phrasings that
happened to be bare keywords.

That was first fixed inside ``memory/fabric.py``, which covered AUTOMATIC recall
and nothing else: the ``history_search`` tool and the Ctrl+K palette call
``SearchIndex.search`` directly, so a user typing "find that conversation from
March about the S-corp election" as a SENTENCE still got nothing back. The
widening now lives in the index's own tier ladder, and this file holds it there:

* all seven phrasings answered through the INDEX path and through the TOOL path;
* the strict ladder's 2-of-7 pinned as a measurement, so a future "simplify" that
  deletes the tier fails with the original number in the message;
* an unrelated question still finding NOTHING (the widening must not turn recall
  into "always returns the newest conversation");
* an operator-bearing query — quotes, uppercase AND/OR/NOT/NEAR, a prefix — NEVER
  widened, so someone who wrote query language keeps their honest miss;
* the damping arithmetic: a widened hit ranks below an exact one, more of the
  question's words outranks fewer, and the band stays ``0 < score <= 0.95``;
* ``MemoryFabric`` DELEGATING rather than carrying a second implementation.

Offline: no network, no model calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.memory import fabric as fabric_mod
from iron_jarvis.memory.fabric import MemoryFabric
from iron_jarvis.search import SearchHit, SearchIndex
from iron_jarvis.search.index import (
    LOOSE_MIN_OVERLAP,
    LOOSE_PENALTY,
    SCORE_CEIL,
    SCORE_FLOOR,
    _content_words,
    _damp_widened,
    _has_operator,
    _loose_expr,
)
from iron_jarvis.search.tools import history_search_tools
from iron_jarvis.tools.base import ToolContext

NOW = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)

#: The seven realistic ways a person asks for ONE seeded conversation. Two of
#: them are bare keywords (the two the strict ladder ever found); five are
#: sentences, which is what the chat turn actually retrieves with and what a
#: person actually types into a search box.
ASKS = (
    "what did we say about the rental property depreciation?",
    "remind me the basis on the rental",
    "how are we handling depreciation for the rental property again?",
    "rental property basis",
    "MACRS 27.5",
    "what's the depreciation schedule for the rental?",
    "can you pull up what we decided about the rental property basis and MACRS",
)

#: The two the CONJUNCTIVE tiers can answer on their own — both bare keywords.
STRICT_ANSWERABLE = ("rental property basis", "MACRS 27.5")


def _ctx() -> ToolContext:
    return ToolContext(
        workspace=None,
        session_id="t",
        agent_run_id="t",
        config=None,
        event_bus=None,
        engine=None,
    )


@pytest.fixture()
def index(tmp_path):
    engine = make_engine(tmp_path / "widening.db")
    init_db(engine)
    idx = SearchIndex(engine)
    assert idx.available() is True, "these tests are about the FTS5 tier ladder"
    return idx


@pytest.fixture()
def seeded(index):
    """One conversation worth finding, plus an unrelated one worth NOT finding."""
    index.sync_thread(
        "chat_rental",
        "chat",
        "Rental basis",
        "proj_tax",
        [
            {
                "role": "user",
                "content": "the rental property basis is 412,000 and we use MACRS "
                "27.5 year depreciation schedules",
                "at": (NOW - timedelta(days=90)).isoformat(),
            }
        ],
    )
    index.sync_thread(
        "chat_logo",
        "chat",
        "Logo colours",
        "proj_media",
        [
            {
                "role": "user",
                "content": "the crimson logo mark reads better at small sizes",
                "at": (NOW - timedelta(days=2)).isoformat(),
            }
        ],
    )
    return index


# --------------------------------------------------------------------------- #
# the measurement
# --------------------------------------------------------------------------- #
def test_the_strict_ladder_alone_answers_only_two_of_the_seven(seeded, monkeypatch):
    """THE NUMBER. Tier 4 disabled, the original behaviour is reproduced exactly:
    five of seven perfectly ordinary questions about a conversation that IS in
    the index come back empty. Anyone deleting the tier gets this number, and
    the list of what stops working, in the failure message."""
    monkeypatch.setattr("iron_jarvis.search.index._loose_expr", lambda words: "")
    found = tuple(a for a in ASKS if seeded.search(a, kinds=["chat"]))
    assert found == STRICT_ANSWERABLE, (
        "without the widening tier the conjunctive ladder answers only bare "
        f"keywords — 2 of 7. Missed: {[a for a in ASKS if a not in found]}"
    )


def test_every_phrasing_finds_the_conversation_through_the_index(seeded):
    """7 of 7, through ``SearchIndex.search`` itself — which is what the
    ``history_search`` tool and the Ctrl+K palette call."""
    for ask in ASKS:
        hits = seeded.search(ask, kinds=["chat"])
        assert hits, f"no hit for {ask!r}"
        assert hits[0].ref == "chat_rental", ask
        assert hits[0].title == "Rental basis"
        assert 0.0 < hits[0].score <= SCORE_CEIL, ask


async def test_every_phrasing_finds_it_through_the_history_search_tool(seeded):
    """The user-visible symptom: "find that conversation about X" typed as a
    SENTENCE into the tool the model calls, and into the palette behind it."""
    tool = history_search_tools(seeded)[0]
    for ask in ASKS:
        result = await tool.execute({"query": ask, "kind": "chat"}, _ctx())
        assert result.ok is True, ask
        hits = (result.data or {})["hits"]
        assert hits, f"the tool found nothing for {ask!r}"
        assert hits[0]["thread_id"] == "chat_rental", ask
        assert 0.0 < hits[0]["score"] <= 1.0, ask


def test_an_unrelated_question_still_finds_nothing(seeded):
    """The cost side of the ledger. ``OR`` will happily return a conversation
    that shares ONE common word with the question — "payroll tax deposit
    SCHEDULE" against depreciation "SCHEDULES" — and one such line in a
    four-line grounding block is a real cost. The overlap floor is what stops
    the widening from becoming "always returns something"."""
    for ask in (
        "what did we decide about the payroll tax deposit schedule?",
        "did we ever talk about quantum chromodynamics",
        "what was the outcome of the zoning hearing",
    ):
        assert seeded.search(ask, kinds=["chat"]) == [], ask


def test_a_query_too_thin_to_widen_is_left_alone(seeded):
    """One content word is already the loosest form of itself; widening it could
    only mean matching a word the user never typed."""
    assert _loose_expr(_content_words("casserole")) == ""
    assert seeded.search("casserole") == []
    # A question made ENTIRELY of connective tissue has no content words at all.
    assert _loose_expr(_content_words("what did we say about it")) == ""


# --------------------------------------------------------------------------- #
# operator-bearing queries are honoured, never rewritten
# --------------------------------------------------------------------------- #
OPERATOR_QUERIES = (
    '"rental property depreciation"',  # a phrase the caller asked for exactly
    "rental property AND unicorn",  # an uppercase boolean
    "rental property NOT basis",
    "NEAR(rental unicorn, 2)",
    "^depreciation rental property",  # initial-token anchor
    "unicorn*",  # a deliberate prefix
)


def test_an_operator_bearing_query_is_never_widened(seeded, monkeypatch):
    """Someone who wrote query language gets it honoured — including the honest
    miss. ``AND unicorn`` and ``NOT basis`` both mean something precise, and both
    would come back with ``chat_rental`` if tier 4 were allowed to rewrite them
    into an ``OR`` (their content words overlap the doc by three)."""
    for query in ("rental property AND unicorn", "rental property NOT basis"):
        assert _has_operator(query) is True, query
        assert seeded.search(query, kinds=["chat"]) == [], query

    # ...and the GUARD is what did that, not a thin corpus: remove it and the
    # very same queries widen into hits.
    monkeypatch.setattr("iron_jarvis.search.index._has_operator", lambda q: False)
    for query in ("rental property AND unicorn", "rental property NOT basis"):
        assert [h.ref for h in seeded.search(query, kinds=["chat"])] == ["chat_rental"]


def test_operator_queries_are_answered_in_the_strict_band_or_not_at_all(seeded):
    """The honest note on tier 3, which predates the widening and still runs
    first: a quoted phrase or a ``^`` anchor that misses can still be rescued by
    the PREFIX retry, which re-tokenizes the query. That is the pre-existing
    contract and is left alone — what matters here is that such a hit keeps its
    full normalized score, i.e. it came from a strict tier and was never damped
    by tier 4."""
    for query in OPERATOR_QUERIES:
        assert _has_operator(query) is True, query
        for h in seeded.search(query, kinds=["chat"]):
            assert SCORE_FLOOR <= h.score <= SCORE_CEIL, query


def test_operator_queries_that_do_match_keep_their_full_score(seeded):
    """``OR`` written BY the caller is tier 1, not tier 4: it is answered
    verbatim and its hits are never damped."""
    hits = seeded.search("depreciation OR unicorn", kinds=["chat"])
    assert [h.ref for h in hits] == ["chat_rental"]
    assert hits[0].score == SCORE_CEIL


def test_a_sentence_is_not_mistaken_for_an_operator(seeded):
    """Only the UPPERCASE forms are FTS5 operators — which is exactly what keeps
    ordinary prose (full of "and", "or", "not") out of the guard."""
    assert _has_operator("what did we say about the rental and the basis") is False
    assert _has_operator("rental or property, not the logo") is False
    assert seeded.search("what did we say about the rental and the basis", kinds=["chat"])


# --------------------------------------------------------------------------- #
# scoring: a widened hit is a weaker claim, and ranks like one
# --------------------------------------------------------------------------- #
def test_a_widened_hit_ranks_below_the_same_document_matched_exactly(seeded):
    """Same conversation, two questions. The conjunctive one contains every term
    the user typed and keeps the full band; the widened one carries a MEASURED
    share of it and is damped by that share × 0.7."""
    exact = seeded.search("rental property basis", kinds=["chat"])[0]
    widened = seeded.search(
        "what did we say about the rental property depreciation?", kinds=["chat"]
    )[0]
    assert exact.ref == widened.ref == "chat_rental"
    assert exact.score == SCORE_CEIL
    assert widened.score < exact.score
    # 3 of 3 content words present, damped once: 0.95 x 0.7.
    assert widened.score == pytest.approx(SCORE_CEIL * LOOSE_PENALTY, abs=1e-4)


def test_more_of_the_question_outranks_less_of_it(index):
    """Within a widened result set the ranking is the overlap fraction, so the
    conversation that is actually about the question comes first — and the
    thinner match is allowed to fall UNDER the 0.35 floor, which exists to
    protect hits that contain every term."""
    index.sync_thread(
        "chat_full",
        "chat",
        "Rental basis",
        "",
        [{"content": "the rental property basis and its depreciation schedule",
          "at": NOW.isoformat()}],
    )
    index.sync_thread(
        "chat_thin",
        "chat",
        "Property tax",
        "",
        [{"content": "the rental property tax bill arrived today",
          "at": NOW.isoformat()}],
    )
    hits = index.search(
        "what did we say about the rental property depreciation?", kinds=["chat"]
    )
    assert [h.ref for h in hits] == ["chat_full", "chat_thin"]
    assert hits[0].score > hits[1].score
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    for h in hits:
        assert 0.0 < h.score <= SCORE_CEIL


def test_the_widened_band_is_open_below_the_floor_but_never_reaches_zero(seeded):
    """DECISION 2's floor is deliberately NOT a floor for tier 4 — a partial
    match has no claim on it. What survives unconditionally is the contract every
    consumer reads: a hit is always ``> 0``, so it can never be confused with the
    no-hits case, and always ``<= 0.95``, so it can never outrank a perfect
    cosine hit when ``MemoryFabric.recall`` sorts across stores."""
    # The arithmetic floor, exercised directly: the thinnest hit the overlap
    # check can pass (2 of a maximum 8 content words) at the lowest normalized
    # score (the 0.35 band floor).
    thin = _damp_widened(
        [SearchHit(kind="chat", ref="r", snippet="alpha bravo", score=SCORE_FLOOR)],
        ["alpha", "bravo", "c1", "c2", "c3", "c4", "c5", "c6"],
    )
    assert len(thin) == 1
    assert thin[0].score == pytest.approx(SCORE_FLOOR * LOOSE_PENALTY * 2 / 8, abs=1e-4)
    assert 0.0 < thin[0].score < SCORE_FLOOR

    for ask in ASKS:
        for h in seeded.search(ask, kinds=["chat"]):
            assert 0.0 < h.score <= SCORE_CEIL, ask


def test_the_overlap_count_tolerates_the_index_stemming():
    """The index tokenizes with ``porter``, the overlap counter does not, so
    ``schedule``/``schedules`` and ``file``/``filing`` have to agree on a prefix
    — otherwise every stemmed hit would be miscounted as noise and thrown away
    by the floor, which is the one failure mode that would make tier 4 look like
    it simply did not work."""
    kept = _damp_widened(
        [SearchHit(kind="chat", ref="r", snippet="the depreciation schedules we filed")],
        ["depreciation", "schedule", "filing"],
    )
    assert kept and kept[0].score == 0.0  # score 0 in, score 0 out — count only
    dropped = _damp_widened(
        [SearchHit(kind="chat", ref="r", snippet="the depreciation schedules we filed",
                   score=0.9)],
        ["depreciation", "unicorn", "zeppelin"],
    )
    assert dropped == [], f"one shared word is under the {LOOSE_MIN_OVERLAP}-word floor"


# --------------------------------------------------------------------------- #
# the fabric delegates — one implementation, not two
# --------------------------------------------------------------------------- #
def test_the_fabric_no_longer_carries_its_own_widening():
    """Structural, on purpose. Two implementations of the same damping is how a
    "search finds it but recall doesn't" bug gets shipped, so the fabric's copy
    is gone rather than merely unused."""
    for gone in ("_or_query", "_content_words", "_overlap", "_ASK_WORDS",
                 "_LOOSE_PENALTY", "_LOOSE_MIN_OVERLAP", "_STEM_CHARS"):
        assert not hasattr(fabric_mod, gone), (
            f"fabric.{gone} is a second definition of the index's widening — "
            "the tier lives in search/index.py now"
        )


def test_the_fabric_makes_exactly_one_index_call_per_store():
    """The delegation itself: one call in, whatever the index says out. The old
    code issued a second ``search`` with a rewritten query — the retry that now
    happens INSIDE the index, where the tool and the palette get it too."""
    calls: list[str] = []

    class _Spy:
        def search(self, query, **kw):
            calls.append(query)
            return []

    assert MemoryFabric._index_search(_Spy(), "a spoken question about rentals",
                                      ["chat"], 8) == []
    assert calls == ["a spoken question about rentals"]


def test_the_fabric_still_degrades_to_no_hits_when_the_index_misbehaves():
    class _Broken:
        def search(self, query, **kw):
            raise RuntimeError("hand-rolled double")

    assert MemoryFabric._index_search(_Broken(), "anything", ["chat"], 4) == []
