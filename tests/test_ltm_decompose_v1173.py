"""v1.173.0 — the shared brain ANSWERS a multi-word question (P1).

MEASURED, on the user's live MCP-served wiki (search delegated to the remote
server's own tool, which matches literally):

    s-corp                  -> 4 hits (incl. comparisons/s-corp-vs-llc)
    llc                     -> 4 hits
    comparisons             -> 2 hits
    "s-corp vs llc"         -> 0 hits
    "scorp llc comparison"  -> 0 hits

Single terms work; the question a human (or another agent) would actually ask
returns nothing. These tests reproduce that connector behaviour exactly —
substring matching over ref + text, nothing smarter — and pin the manager-level
decomposition that makes the note reachable again WITHOUT degrading a store
that handles the whole phrase.

Everything here is offline: no sockets, no clock dependence beyond a
monkeypatched budget.
"""

from __future__ import annotations

import time

import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.ltm.base import LTMConnector, MarkdownDirConnector
from iron_jarvis.ltm.manager import (
    LongTermMemory,
    query_variants,
    shared_deadline,
    significant_terms,
)
from iron_jarvis.ltm.tools import ltm_tools
from iron_jarvis.memory.fabric import MemoryFabric
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.tools.registry import ToolRegistry

# --------------------------------------------------------------------------
# The measured remote: a store that can only match a literal substring.
# --------------------------------------------------------------------------
#: A slice of the real wiki. NOTE what the body text does NOT contain: the
#: phrase "s-corp vs llc". The slug carries the comparison; the prose does not
#: repeat it — which is precisely why the literal phrase search finds nothing.
WIKI = {
    "comparisons/s-corp-vs-llc": (
        "Reasonable compensation, self-employment tax, payroll obligations and "
        "when each entity choice wins."
    ),
    "entities/s-corp-election": "Form 2553 timing and late-election relief.",
    "entities/llc-operating-agreement": "Member classes, capital accounts.",
    "comparisons/c-corp-vs-s-corp": "Double taxation and QSBS considerations.",
}


class LiteralConnector(LTMConnector):
    """Substring matching over ``ref`` + text — the measured remote behaviour.

    Records every query it is asked, so a test can assert the number of round
    trips (a decomposition that fires when it was not needed is a real cost:
    each pass is a network call inside a chat turn).
    """

    def __init__(self, name: str = "brain", notes: "dict[str, str] | None" = None):
        self.name = name
        self.notes = dict(WIKI if notes is None else notes)
        self.calls: list[str] = []

    def search(self, query: str, k: int = 5) -> list[dict]:
        self.calls.append(query)
        q = (query or "").strip().lower()
        out: list[dict] = []
        if not q:
            return out
        for ref, text in self.notes.items():
            if q in f"{ref} {text}".lower():
                out.append(
                    {
                        "title": ref.rsplit("/", 1)[-1],
                        "snippet": text,
                        "ref": ref,
                        "source": self.name,
                    }
                )
                if len(out) >= k:
                    break
        return out

    def append(self, title: str, content: str) -> str:  # pragma: no cover
        raise NotImplementedError


class PhraseConnector(LTMConnector):
    """A store that HANDLES the whole phrase: always answers ``k`` hits."""

    def __init__(self, name: str = "smart") -> None:
        self.name = name
        self.calls: list[str] = []

    def search(self, query: str, k: int = 5) -> list[dict]:
        self.calls.append(query)
        return [
            {"title": f"n{i}", "snippet": query, "ref": f"{self.name}/{i}",
             "source": self.name}
            for i in range(k)
        ]

    def append(self, title: str, content: str) -> str:  # pragma: no cover
        raise NotImplementedError


def _manager(*connectors: LTMConnector) -> LongTermMemory:
    ltm = LongTermMemory()
    for c in connectors:
        ltm.register(c)
    return ltm


# --------------------------------------------------------------------------
# THE REPORT: the multi-term question now reaches the note.
# --------------------------------------------------------------------------
def test_literal_store_answers_the_multi_term_question():
    conn = LiteralConnector()
    ltm = _manager(conn)

    # Baseline: the measured failure is real for this connector.
    assert conn.search("s-corp vs llc") == []
    assert len(conn.search("s-corp")) == 3

    hits = ltm.search("s-corp vs llc", k=5, source="brain")

    refs = [h["ref"] for h in hits]
    assert "comparisons/s-corp-vs-llc" in refs, refs
    # It leads: it is the only note credited with BOTH significant terms.
    assert refs[0] == "comparisons/s-corp-vs-llc"
    # And it says how it was found — a decomposed match is not an exact one.
    assert hits[0]["match"] == "partial"
    assert hits[0]["matched_terms"] == ["s-corp", "llc"]


def test_merged_path_across_every_connector_decomposes_too():
    """The fallback lives where results merge, so it is not per-connector."""
    a = LiteralConnector("brain")
    b = LiteralConnector("wiki", {"kb/llc-vs-s-corp": "entity comparison table"})
    hits = _manager(a, b).search("s-corp vs llc", k=6)

    by_source = {h["source"] for h in hits}
    assert by_source == {"brain", "wiki"}
    assert "kb/llc-vs-s-corp" in [h["ref"] for h in hits]


async def test_agent_tool_reaches_the_note(tmp_path):
    """Every agent calls ``ltm_search``; the fix must land THERE, not only in
    automatic recall."""
    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    registry = ToolRegistry()
    for tool in ltm_tools(_manager(LiteralConnector())):
        registry.register(tool)
    ctx = ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )
    res = await registry.invoke(
        "ltm_search", {"query": "what do we have on s-corp vs llc"}, ctx,
        PermissionEngine({"ltm_search": "allow"}),
    )
    assert res.ok
    assert res.data["results"][0]["ref"] == "comparisons/s-corp-vs-llc"


# --------------------------------------------------------------------------
# NEVER DEGRADE A WORKING SEARCH
# --------------------------------------------------------------------------
def test_full_phrase_store_is_asked_exactly_once():
    conn = PhraseConnector()
    hits = _manager(conn).search("s-corp vs llc", k=3, source="smart")

    assert conn.calls == ["s-corp vs llc"], conn.calls  # no extra round trips
    assert len(hits) == 3
    assert all("match" not in h for h in hits)


def test_single_significant_term_never_decomposes():
    conn = LiteralConnector("brain", {"kb/nothing": "unrelated"})
    assert _manager(conn).search("llc", k=5, source="brain") == []
    assert conn.calls == ["llc"]  # nothing to decompose, nothing spent


def test_thin_result_is_returned_as_the_connectors_own_objects():
    """No fallback ⇒ no copying, no marker keys, same objects in the same
    order. The pre-v1.173.0 behaviour, byte for byte."""
    conn = PhraseConnector()
    original = conn.search("q")
    conn.calls.clear()

    class Echo(PhraseConnector):
        def search(self, query, k=5):
            self.calls.append(query)
            return original

    echo = Echo("smart")
    out = _manager(echo).search("s-corp vs llc", k=5, source="smart")
    assert out is original
    assert all(a is b for a, b in zip(out, original))


def test_expand_false_is_the_old_literal_search():
    conn = LiteralConnector()
    assert _manager(conn).search("s-corp vs llc", k=5, source="brain", expand=False) == []
    assert conn.calls == ["s-corp vs llc"]


# --------------------------------------------------------------------------
# MERGE / DEDUPE / RANK
# --------------------------------------------------------------------------
def _ordering_manager() -> tuple[LongTermMemory, LiteralConnector]:
    # NOTE the insertion order: within the "alpha" pass this connector returns
    # ``one`` BEFORE ``both``. Rank inside a pass must therefore lose to the
    # number of terms a hit covers, or the note that answers half the question
    # outranks the one that answers all of it.
    conn = LiteralConnector(
        "brain",
        {
            "exact": "alpha beta together in one phrase",
            "one": "alpha only",
            "both": "alpha here and beta over there",
        },
    )
    return _manager(conn), conn


def test_ordering_full_query_then_multi_term_then_single_term():
    ltm, conn = _ordering_manager()
    hits = ltm.search("alpha beta", k=5, source="brain")

    assert [h["ref"] for h in hits] == ["exact", "both", "one"]
    assert "match" not in hits[0]                    # the full-query hit
    assert hits[1]["matched_terms"] == ["alpha", "beta"]
    assert hits[2]["matched_terms"] == ["alpha"]
    # "exact" was returned by three passes and appears exactly once.
    assert len(conn.calls) > 1
    assert [h["ref"] for h in hits].count("exact") == 1


def test_k_is_respected_across_the_merged_passes():
    ltm, _ = _ordering_manager()
    assert len(ltm.search("alpha beta", k=2, source="brain")) == 2
    assert len(ltm.search("alpha beta", k=1, source="brain")) == 1


def test_best_scored_copy_of_a_duplicate_survives():
    """Among copies from passes of the SAME class, the best-scored one wins."""

    class Scored(LiteralConnector):
        def search(self, query, k=5):
            self.calls.append(query)
            if query == "alpha beta":
                return []  # the measured remote: the whole phrase finds nothing
            score = 9.0 if query == "beta" else 1.0
            return [{"title": "alpha beta", "snippet": query, "ref": "same",
                     "source": self.name, "score": score}]

    hits = _manager(Scored("brain")).search("alpha beta", k=5, source="brain")
    assert len(hits) == 1
    assert hits[0]["score"] == 9.0
    assert hits[0]["snippet"] == "beta"  # the copy that scored it, not the first


def test_a_primary_hit_is_never_replaced_by_a_higher_scored_fallback_copy():
    """`primary` describes the OBJECT handed back (it is emitted unmarked, as
    an exact match). Adopting a fallback pass's copy of the same ref — its own
    snippet window, its own score — would hand the caller a decomposed body
    wearing the face of the exact hit."""

    class Shifty(LiteralConnector):
        def search(self, query, k=5):
            self.calls.append(query)
            exact = query == "alpha beta"
            return [{
                "title": "n",
                "snippet": "the window the question matched" if exact
                           else "a window some term matched",
                "ref": "same",
                "source": self.name,
                "score": 1.0 if exact else 9.0,
            }]

    hits = _manager(Shifty("brain")).search("alpha beta", k=5, source="brain")
    assert len(hits) == 1
    assert hits[0]["snippet"] == "the window the question matched"
    assert hits[0]["score"] == 1.0
    assert "match" not in hits[0]  # ...and it is still the exact hit


def test_ref_less_hits_are_not_collapsed_into_one():
    """An MCP server answering in prose returns hits with no ref; keying them
    all to (source, "") would silently merge distinct answers."""

    class Prose(LiteralConnector):
        def search(self, query, k=5):
            self.calls.append(query)
            if query != "alpha":
                return []
            return [
                {"title": "", "snippet": "first answer", "ref": "", "source": self.name},
                {"title": "", "snippet": "second answer", "ref": "", "source": self.name},
            ]

    hits = _manager(Prose("brain")).search("alpha beta", k=5, source="brain")
    assert [h["snippet"] for h in hits] == ["first answer", "second answer"]


def test_ref_less_hits_sharing_a_title_are_not_collapsed():
    """A ref-less hit has NO stable identity, so the title alone must not be
    treated as one: an MCP server answering in prose under one repeated
    heading (or a Notion page group) would otherwise keep only the
    first-arrived copy and silently drop the better one."""

    class Repeated(LiteralConnector):
        def search(self, query, k=5):
            self.calls.append(query)
            if query != "alpha":
                return []
            return [
                {"title": "Findings", "snippet": "alpha, the early note",
                 "ref": "", "source": self.name},
                {"title": "Findings", "snippet": "alpha, the later and fuller note",
                 "ref": "", "source": self.name},
            ]

    hits = _manager(Repeated("brain")).search("alpha beta", k=5, source="brain")
    assert [h["snippet"] for h in hits] == [
        "alpha, the early note", "alpha, the later and fuller note",
    ]


# --------------------------------------------------------------------------
# A FALLBACK IS A BONUS: it can never cost the primary result
# --------------------------------------------------------------------------
def test_a_raising_fallback_pass_keeps_the_primary_hits():
    class BreaksOnRetry(LiteralConnector):
        def search(self, query, k=5):
            self.calls.append(query)
            if query != "alpha beta":
                raise RuntimeError("brain: connection reset")
            return [{"title": "n", "snippet": "s", "ref": "kept", "source": self.name}]

    conn = BreaksOnRetry("brain")
    hits = _manager(conn).search("alpha beta", k=5, source="brain")

    assert [h["ref"] for h in hits] == ["kept"]
    assert len(conn.calls) > 1  # the retries really were attempted
    assert "match" not in hits[0]


def test_a_raising_primary_still_propagates():
    """Unchanged: callers are told WHICH base is broken. Swallowing this would
    turn a dead brain into an empty one."""

    class Dead(LiteralConnector):
        def search(self, query, k=5):
            raise RuntimeError("brain: the MCP server exposes no search-like tool")

    with pytest.raises(RuntimeError):
        _manager(Dead("brain")).search("s-corp vs llc", source="brain")
    # ...while the MERGED path keeps its long-standing tolerance.
    assert _manager(Dead("brain")).search("s-corp vs llc") == []


# --------------------------------------------------------------------------
# BOUNDS (this runs inside a chat turn)
# --------------------------------------------------------------------------
def test_time_budget_stops_after_the_first_fallback_pass(monkeypatch):
    from iron_jarvis.ltm import manager as mod

    monkeypatch.setattr(mod, "_FALLBACK_BUDGET_S", 0.0)
    conn = LiteralConnector()
    hits = _manager(conn).search("s-corp vs llc", k=5, source="brain")

    # ONE fallback pass always runs — a slow base must not read as "no note".
    assert conn.calls == ["s-corp vs llc", "s-corp"], conn.calls
    assert [h["ref"] for h in hits] == [
        "comparisons/s-corp-vs-llc",
        "entities/s-corp-election",
        "comparisons/c-corp-vs-s-corp",
    ]


def test_considered_ceiling_stops_the_last_pass():
    class Flood(LiteralConnector):
        def __init__(self, name):
            super().__init__(name, {})
            self.asked: list[int] = []

        def search(self, query, k=5):
            self.calls.append(query)
            self.asked.append(k)
            n = 1 if query == "alpha bravo charlie" else k
            return [
                {"title": query, "snippet": query, "ref": f"{query}/{i}",
                 "source": self.name}
                for i in range(n)
            ]

    conn = Flood("brain")
    hits = _manager(conn).search("alpha bravo charlie", k=2, source="brain")

    # k*3 = 6 results considered: 1 + 2 + 2 + 1 exhausts it, so the LAST
    # planned pass ("bravo") is never spent...
    assert conn.calls == [
        "alpha bravo charlie", "charlie", "alpha-bravo-charlie", "alpha",
    ], conn.calls
    # ...and the pass that would have overshot asks only for what is left, so
    # the ceiling is a real bound rather than a rounding.
    assert conn.asked == [2, 2, 2, 1], conn.asked
    assert len(hits) == 2


def test_at_most_three_term_passes_and_no_whole_sentence_slug():
    conn = LiteralConnector("brain", {"kb/none": "nothing matches here"})
    _manager(conn).search("alpha bravo charlie delta echo", k=5, source="brain")

    assert conn.calls[0] == "alpha bravo charlie delta echo"
    # 3 term passes and NOTHING else: a five-word query has no hyphen to open
    # up and is far too long to be a slug, so neither rewrite is worth a call.
    assert len(conn.calls) == 1 + 3
    assert conn.calls[1:] == ["charlie", "alpha", "bravo"]  # longest first
    assert "echo" not in conn.calls  # the 4th/5th terms are never spent
    assert "delta" not in conn.calls
    assert "alpha-bravo-charlie-delta-echo" not in conn.calls


# --------------------------------------------------------------------------
# TERM SELECTION (an over-eager stopword list is its own bug)
# --------------------------------------------------------------------------
def test_significant_terms_keeps_the_domain_nouns_and_the_hyphen():
    assert significant_terms("what is our s-corp vs llc policy?") == [
        "s-corp", "llc", "policy",
    ]
    # Case is preserved (the remote may match case-sensitively) but duplicates
    # are dropped case-insensitively.
    assert significant_terms("Tax tax TAX return") == ["Tax", "return"]
    # Domain nouns are never treated as noise.
    assert significant_terms("tax return deadline") == [
        "tax", "return", "deadline",
    ]
    # Too short / pure connective tissue. "into" is the same joint as the "in"
    # and "to" already on the list, and it is long enough to survive the length
    # filter — which is exactly how it once outranked "llc" for a pass.
    assert significant_terms("in on to of a an vs into") == []
    assert significant_terms("id k9") == []
    assert significant_terms("look into the history of the llc") == [
        "look", "history", "llc",
    ]


def test_no_invented_spellings():
    """``s-corp`` must never become ``scorp``: a spelling the user never typed
    and the vault never used is a guaranteed miss with a network cost."""
    assert "scorp" not in significant_terms("s-corp vs llc")
    assert "scorp" not in query_variants("s-corp vs llc")


def test_query_variants_are_rewrites_only():
    assert query_variants("s-corp vs llc") == ["s corp vs llc", "s-corp-vs-llc"]
    assert query_variants("llc") == []
    assert query_variants("  s-corp   election  ") == [
        "s corp election", "s-corp-election",
    ]
    # Four words is still slug-shaped (the case measured to work).
    assert query_variants("s-corp vs llc policy") == [
        "s corp vs llc policy", "s-corp-vs-llc-policy",
    ]


def test_a_sentence_is_never_welded_into_a_slug():
    """The hyphen->space direction is a rewrite of something the user typed.
    The space->hyphen direction INVENTS a string, and past slug length that
    string is a coinage no store holds — 0 hits on a literal matcher, and a
    stolen slot of the considered-ceiling on a store that answers anything."""
    assert query_variants("what do we have on reasonable compensation rules") == []
    assert query_variants("look into the history of the llc") == []
    # ...while the hyphen the user DID type is still opened up.
    assert query_variants("the history of the s-corp election") == [
        "the history of the s corp election",
    ]


def test_no_whole_sentence_slug_pass_is_ever_spent():
    conn = LiteralConnector("brain", {"kb/none": "nothing matches here"})
    _manager(conn).search(
        "what do we have on reasonable compensation rules", k=5, source="brain"
    )
    assert "what-do-we-have-on-reasonable-compensation-rules" not in conn.calls
    assert conn.calls == [
        "what do we have on reasonable compensation rules",
        "compensation", "reasonable", "rules",
    ], conn.calls


# --------------------------------------------------------------------------
# THE TOPIC NOUN IS ALWAYS ASKED (the wave's own headline failure)
# --------------------------------------------------------------------------
#: The general calling phrasings v1.173.0 §P3 blesses — "terms like 'search
#: your memory' or 'look into the history' would be more general for all the
#: users". Every one of them wraps the topic in LONG verbs, so ranking the
#: terms by raw length spent the three passes on `history`/`look`/`into` and
#: never asked `llc` at all: the brain held the note and answered nothing.
P3_PHRASINGS = [
    "search your memory for the llc",
    "look in your memory for the llc",
    "check your memory for the llc",
    "look into the history of the llc",
    "search the history of the llc",
    "what do we have on the llc",
    "what do you have on the llc",
    "check your notes on the llc",
    "search your notes for the llc",
    "can you check your notes and tell me about the llc",
    "dig up what we have on the llc",
    "pull up what we have on the llc",
    "look this up in your knowledge base: the llc",
]


@pytest.mark.parametrize("phrasing", P3_PHRASINGS)
def test_the_topic_noun_is_asked_for_every_blessed_phrasing(phrasing):
    conn = LiteralConnector()
    ltm = _manager(conn)

    # The brain holds it and answers the bare noun...
    assert len(conn.search("llc")) == 2
    conn.calls.clear()

    hits = ltm.search(phrasing, k=5, source="brain")

    assert "llc" in conn.calls, conn.calls  # ...so the noun MUST be asked
    refs = [h["ref"] for h in hits]
    assert "entities/llc-operating-agreement" in refs, refs
    assert "comparisons/s-corp-vs-llc" in refs, refs


def test_calling_verbs_never_outrank_the_topic_they_ask_about():
    conn = LiteralConnector()
    _manager(conn).search("look into the history of the llc", k=5, source="brain")

    # Every pass after the full phrase is about the TOPIC. Not one round trip
    # is spent on the words the user asked WITH.
    assert conn.calls == ["look into the history of the llc", "llc"], conn.calls


def test_a_query_of_only_calling_words_still_decomposes():
    """The calling vocabulary is DEMOTED, not dropped — when it is all the
    query has, it is what gets asked. An over-eager stopword list is its own
    bug, and a query with nothing left to decompose into is that bug."""
    conn = LiteralConnector("brain", {"kb/notes-index": "check the notes index"})
    hits = _manager(conn).search("check your notes", k=5, source="brain")

    assert "check" in conn.calls, conn.calls
    assert [h["ref"] for h in hits] == ["kb/notes-index"]


# --------------------------------------------------------------------------
# A DECOMPOSED MATCH CLAIMS ONLY WHAT IT CAN PROVE
# --------------------------------------------------------------------------
def test_matched_terms_never_names_a_term_absent_from_the_hit():
    """`matched_terms` is a claim ABOUT THE HIT, and it also drives the rank.
    Crediting a pass with every term of the query — a rewrite pass knows
    nothing about which term matched — put unverified hits at the TOP of the
    ranking wearing the vocabulary of an exact match."""

    class Loose(LiteralConnector):
        """Answers anything once the query is short enough (a semantic-ish
        store), but never the whole sentence."""

        def search(self, query, k=5):
            self.calls.append(query)
            if len(query.split()) > 3:
                return []
            return [{
                "title": "compensation memo",
                "snippet": "reasonable compensation for owner-employees",
                "ref": "kb/comp",
                "source": self.name,
            }]

    hits = _manager(Loose("brain")).search(
        "what do we have on reasonable compensation rules", k=5, source="brain"
    )

    assert len(hits) == 1
    assert hits[0]["match"] == "partial"
    # Present in the hit's own text; ordered as the user typed them.
    assert hits[0]["matched_terms"] == ["reasonable", "compensation"]
    # "rules" was ASKED and answered; nothing proves it MATCHED.
    assert "rules" not in hits[0]["matched_terms"]
    # ...and "have" is a word from the calling phrase, never evidence.
    assert "have" not in hits[0]["matched_terms"]


def test_a_verified_partial_hit_outranks_an_unverifiable_one():
    class Mixed(LiteralConnector):
        def search(self, query, k=5):
            self.calls.append(query)
            if query == "alpha beta":
                return []
            return [
                {"title": "vague", "snippet": "no query word appears here",
                 "ref": "vague", "source": self.name},
                {"title": "solid", "snippet": f"about {query} exactly",
                 "ref": "solid", "source": self.name},
            ]

    hits = _manager(Mixed("brain")).search("alpha beta", k=5, source="brain")
    assert [h["ref"] for h in hits] == ["solid", "vague"]
    assert hits[0]["matched_terms"] == ["alpha", "beta"]
    assert hits[1]["matched_terms"] == []  # honest about having no evidence


# --------------------------------------------------------------------------
# LOCAL MARKDOWN INHERITS IT TOO (no per-connector special-casing)
# --------------------------------------------------------------------------
def test_local_markdown_base_also_gains_the_fallback(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "s-corp-vs-llc.md").write_text(
        "# Comparison\n\nreasonable compensation and payroll\n", encoding="utf-8"
    )
    conn = MarkdownDirConnector(vault)
    conn.name = "brain"
    hits = _manager(conn).search("s-corp vs llc", k=5, source="brain")
    assert [h["title"] for h in hits] == ["s-corp-vs-llc"]


# --------------------------------------------------------------------------
# THE FABRIC INHERITS IT (contract 6) — and stays honest about how
# --------------------------------------------------------------------------
def test_fabric_notes_lane_inherits_the_decomposition():
    fab = MemoryFabric(ltm=_manager(LiteralConnector()))
    hits = fab.recall("s-corp vs llc", k=4, sources=["notes"])

    refs = [h.ref for h in hits]
    assert "comparisons/s-corp-vs-llc" in refs
    top = next(h for h in hits if h.ref == "comparisons/s-corp-vs-llc")
    assert top.source == "notes"
    assert top.extra["match"] == "partial"
    assert top.extra["matched_terms"] == ["s-corp", "llc"]


def test_fabric_damps_a_partial_note_below_an_exact_one():
    """A note found by taking the question apart must not present itself as
    though the whole question matched it."""
    fab = MemoryFabric(
        ltm=_manager(
            LiteralConnector(
                "brain",
                {
                    "exact": "alpha beta in one phrase",
                    "partial": "alpha over here",
                },
            )
        )
    )
    hits = fab.recall("alpha beta", k=4, sources=["notes"])
    scores = {h.ref: h.score for h in hits}

    assert scores["exact"] > scores["partial"]
    # Exact: both query terms present -> lexical 1.0, undamped.
    assert scores["exact"] == pytest.approx(1.0)
    # Partial: one of two terms present -> 0.5, then the 0.7 damping (the
    # history index's LOOSE_PENALTY rule). Still far above ground()'s 0.05
    # floor, so the note stays REACHABLE — damped, never hidden.
    assert scores["partial"] == pytest.approx(0.5 * 0.7)
    assert scores["partial"] > 0.05


def test_fabric_leaves_an_exact_note_hit_unmarked():
    fab = MemoryFabric(ltm=_manager(PhraseConnector()))
    hits = fab.recall("s-corp vs llc", k=4, sources=["notes"])
    assert hits
    assert all("match" not in h.extra for h in hits)


def test_fabric_ground_block_carries_the_recovered_note():
    fab = MemoryFabric(ltm=_manager(LiteralConnector()))
    block = fab.ground("what do we have on s-corp vs llc", sources=["notes"])
    assert "s-corp-vs-llc" in block


# --------------------------------------------------------------------------
# ONE WALL CLOCK, SPENT BETWEEN THE BASES (not one apiece)
# --------------------------------------------------------------------------
def test_an_expired_shared_deadline_still_buys_one_fallback_pass():
    """The per-base floor: a slow FIRST base must not turn a second base's
    held note into "no such note"."""
    conn = LiteralConnector()
    hits = _manager(conn).search(
        "s-corp vs llc", k=5, source="brain", deadline=time.monotonic() - 1
    )
    assert conn.calls == ["s-corp vs llc", "s-corp"], conn.calls
    assert "comparisons/s-corp-vs-llc" in [h["ref"] for h in hits]


def test_a_fresh_deadline_leaves_the_default_budget_untouched():
    before = time.monotonic()
    assert shared_deadline() > before
    assert shared_deadline(0.0) <= time.monotonic() + 0.001


class _SpyLTM(LongTermMemory):
    """Records the deadline every search() call was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.deadlines: list[float | None] = []

    def search(self, query, k=5, source=None, *, expand=True, deadline=None):
        self.deadlines.append(deadline)
        return super().search(
            query, k=k, source=source, expand=expand, deadline=deadline
        )


def test_the_notes_lane_spends_one_deadline_across_every_bound_base():
    """Four bound bases used to mean four fresh 2.5s ceilings, multiplied on
    the event loop (both chat lanes call this lane synchronously)."""
    spy = _SpyLTM()
    spy.register(LiteralConnector("brain"))
    spy.register(LiteralConnector("wiki", {"kb/llc-vs-s-corp": "entity comparison"}))
    fab = MemoryFabric(ltm=spy)

    hits = fab._notes("s-corp vs llc", 4, {"s-corp", "llc"}, ["brain", "wiki"])

    assert len(spy.deadlines) == 2
    assert all(d is not None for d in spy.deadlines)
    assert len(set(spy.deadlines)) == 1  # ONE ceiling, spent between them
    refs = [h.ref for h in hits]
    assert "comparisons/s-corp-vs-llc" in refs and "kb/llc-vs-s-corp" in refs


def test_a_duck_typed_ltm_keeps_the_historical_signature():
    """The notes lane swallows a per-base exception, so handing an unexpected
    keyword to a stand-in LTM would return NO notes at all — silently, which
    is the exact failure mode this wave exists to end."""

    class OldStyle:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        def sources(self):
            return ["brain"]

        def search(self, query, k=5, source=None):
            self.calls.append((query, source))
            return [{"title": "t", "snippet": "s-corp vs llc", "ref": "r",
                     "source": "brain"}]

    old = OldStyle()
    hits = MemoryFabric(ltm=old)._notes(
        "s-corp vs llc", 4, {"s-corp", "llc"}, ["brain"]
    )
    assert [h.ref for h in hits] == ["r"]
    assert old.calls == [("s-corp vs llc", "brain")]
