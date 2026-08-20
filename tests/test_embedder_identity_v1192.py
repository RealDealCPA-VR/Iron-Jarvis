"""Finding 27: an embedder switch must not silently orphan stored vectors.

``build_embedder`` picks per BOOT — the offline ``MockEmbedder`` is 64-dim, a
local ``nomic-embed-text`` is 768-dim — so the daily driver whose Ollama comes
and goes writes memories under one embedder and queries them under the other.
Recall used to DROP every row whose vector length differed from the query's and
return ``[]``: the facts were intact in SQLite, still listed by the awareness
index, and permanently unreachable. These tests pin the three halves of the fix:
the stored vector records WHO wrote it, a non-comparable row is ranked by
keyword instead of dropped, and the degradation is REPORTED.
"""

from __future__ import annotations

import json

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.memory.embeddings import MockEmbedder
from iron_jarvis.memory.models import MemoryRecord
from iron_jarvis.memory.retrieval import (
    SqliteVectorRetriever,
    decode_embedding,
    encode_embedding,
)
from iron_jarvis.projects.knowledge import add_knowledge, ground


class FakeEmbedder(MockEmbedder):
    """A deterministic embedder with a chosen identity + dimensionality."""

    def __init__(self, model: str, dim: int) -> None:
        super().__init__(dim=dim)
        self.model = model


MOCK64 = ("mock", 64)
OLLAMA768 = ("nomic-embed-text", 768)


def _seed(platform, embedder) -> None:
    retriever = SqliteVectorRetriever(platform.engine, embedder)
    retriever.add(
        MemoryRecord(
            layer="semantic",
            scope_id="s",
            key="deadline",
            text="the client filing deadline is March 15",
        )
    )
    retriever.add(
        MemoryRecord(
            layer="semantic", scope_id="s", key="colour", text="the logo is crimson"
        )
    )


# --------------------------------------------------------------------------
# codec
# --------------------------------------------------------------------------


def test_codec_tags_identity_and_still_reads_legacy_bare_vectors():
    tagged = encode_embedding([1.0, 0.0, 0.5], "nomic-embed-text")
    assert json.loads(tagged)["model"] == "nomic-embed-text"
    assert decode_embedding(tagged) == ([1.0, 0.0, 0.5], "nomic-embed-text")
    # A row written before the tag (and by every path still writing a bare list)
    # decodes as "identity not recorded" — not as "written by another model".
    assert decode_embedding(json.dumps([1.0, 0.0])) == ([1.0, 0.0], "")
    assert decode_embedding("") == ([], "")
    assert decode_embedding("{not json") == ([], "")
    # No identity to record → no identity fabricated.
    assert encode_embedding([1.0, 2.0]) == "[1.0, 2.0]"


def test_add_stamps_the_writing_embedder_on_the_row(platform):
    _seed(platform, FakeEmbedder(*OLLAMA768))
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(MemoryRecord)))
    assert rows
    for row in rows:
        vec, model = decode_embedding(row.embedding_json)
        assert model == "nomic-embed-text" and len(vec) == 768


# --------------------------------------------------------------------------
# the cross-embedder recall (the reported defect)
# --------------------------------------------------------------------------


def test_rows_written_at_64_dims_are_still_found_at_768_dims(platform):
    """Write a week of memories under the offline mock, restart under Ollama."""
    _seed(platform, FakeEmbedder(*MOCK64))

    after_restart = SqliteVectorRetriever(platform.engine, FakeEmbedder(*OLLAMA768))
    hits, note = after_restart.search_report(
        "client filing deadline", k=5, layer="semantic", scope_id="s"
    )

    # Before the fix this was [] — the rows were dropped by the size filter.
    assert hits, "64-dim rows went invisible under the 768-dim embedder"
    assert hits[0][0].text == "the client filing deadline is March 15"
    # A keyword rescue is damped: it can never be mistaken for a cosine hit.
    assert 0.0 < hits[0][1] <= 0.5
    # ...and it is SAID OUT LOUD rather than passed off as a normal result.
    assert "cannot be similarity-ranked" in note
    assert "nomic-embed-text" in note and "768 dims" in note
    assert "64 dims" in note
    # The plain search() contract is unchanged (same hits, note dropped).
    assert [r.id for r, _ in after_restart.search(
        "client filing deadline", k=5, layer="semantic", scope_id="s"
    )] == [r.id for r, _ in hits]


def test_same_dimension_but_a_different_model_is_also_incomparable(platform):
    """Two 768-dim models produce vectors whose cosine means nothing. Length
    alone cannot see that; the recorded identity can."""
    _seed(platform, FakeEmbedder("embed-a", 768))

    other = SqliteVectorRetriever(platform.engine, FakeEmbedder("embed-b", 768))
    hits, note = other.search_report(
        "client filing deadline", k=5, layer="semantic", scope_id="s"
    )
    assert hits and hits[0][0].key == "deadline"
    assert hits[0][1] <= 0.5  # keyword-ranked, not passed off as similarity
    assert "embed-a" in note and "embed-b" in note


def test_clean_run_reports_nothing_and_ranks_by_cosine(platform):
    """No mismatch → no note, and real cosine scores (no damping)."""
    embedder = FakeEmbedder(*OLLAMA768)
    _seed(platform, embedder)
    hits, note = SqliteVectorRetriever(platform.engine, embedder).search_report(
        "the client filing deadline is March 15", k=5, layer="semantic", scope_id="s"
    )
    assert note == ""
    assert hits[0][0].key == "deadline" and hits[0][1] > 0.9


def test_legacy_untagged_rows_of_matching_length_still_rank_normally(platform):
    """Backward compatibility: a bare-list row (written by MemoryLayers.write, or
    by any pre-fix install) is comparable on dimension alone — an unrecorded
    identity is not evidence of a different one."""

    class StubEmbedder:  # no `.model` at all
        def embed(self, text):
            return [1.0, 0.0] if "gold" in text else [0.0, 1.0]

    with session_scope(platform.engine) as db:
        db.add(
            MemoryRecord(
                layer="semantic",
                scope_id="s",
                key="k",
                text="gold",
                embedding_json=json.dumps([1.0, 0.0]),
            )
        )
        db.commit()
    hits, note = SqliteVectorRetriever(platform.engine, StubEmbedder()).search_report(
        "gold please", k=3, layer="semantic", scope_id="s"
    )
    assert note == ""
    assert hits and hits[0][0].text == "gold" and hits[0][1] > 0.99


# --------------------------------------------------------------------------
# project knowledge grounding
# --------------------------------------------------------------------------


def test_ground_rescues_and_reports_items_embedded_by_another_model(platform):
    """A knowledge base too large to include whole, written under the mock and
    grounded under Ollama: cosine scores every item 0.0, so selection silently
    collapses to newest-first and the one relevant (OLDEST) item is dropped."""
    platform.embedder = FakeEmbedder(*MOCK64)
    add_knowledge(
        platform,
        "big",
        "invoice-policy",
        "Invoices are due net-30 and must reference the purchase order number. " * 20,
    )
    filler = "lorem ipsum dolor sit amet " * 60
    for i in range(12):
        add_knowledge(platform, "big", f"filler-{i}", filler)

    platform.embedder = FakeEmbedder(*OLLAMA768)  # the next boot
    out = ground(
        platform,
        "big",
        "when are invoices due and what must they reference",
        char_budget=1500,
    )

    assert "Invoices are due net-30" in out, "the relevant item was buried by recency"
    assert "could not be relevance-ranked" in out
    assert "nomic-embed-text, 768 dims" in out
    assert "lorem ipsum" not in out


def test_ground_stays_silent_when_every_item_matches_the_active_embedder(platform):
    platform.embedder = FakeEmbedder(*OLLAMA768)
    filler = "lorem ipsum dolor sit amet " * 60
    for i in range(12):
        add_knowledge(platform, "same", f"filler-{i}", filler)
    add_knowledge(
        platform,
        "same",
        "invoice-policy",
        "Invoices are due net-30 and must reference the purchase order number. " * 20,
    )
    out = ground(platform, "same", "when are invoices due", char_budget=1500)
    assert "could not be relevance-ranked" not in out
    assert len(out) <= 1500 + 600
