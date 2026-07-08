"""Frontier memory wiring: MemoryLayers ranks working-memory recall against the
SHARED embedder threaded in from the platform (§22 Total Recall). Fully offline —
a tiny deterministic fake embedder stands in for the real Ollama model."""

from __future__ import annotations

import iron_jarvis.memory.models  # noqa: F401  (register table before init_db)
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.memory.layers import MemoryLayers


class _FakeEmbedder:
    """Deterministic, network-free embedder keyed off a fixed keyword vocabulary.

    Each dimension counts occurrences of one vocabulary token, so texts sharing
    topics land close under cosine. Records every text it embeds so a test can
    assert the layer actually routed writes/queries through THIS embedder.
    """

    model = "fake"
    _VOCAB = ("python", "pytest", "tax", "docker", "container")

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.seen.append(text)
        low = text.lower()
        return [float(low.count(tok)) for tok in self._VOCAB]


def _engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


def test_shared_embedder_powers_semantic_search(tmp_path):
    emb = _FakeEmbedder()
    layers = MemoryLayers(_engine(tmp_path), embedder=emb)

    layers.write("project", "py", "python testing with pytest and fixtures")
    layers.write("project", "tax", "tax deductions and write-offs for businesses")
    layers.write("project", "docker", "docker container image layers")

    # Writes embedded through the injected embedder (not a fresh Mock).
    assert any("pytest" in t for t in emb.seen)

    hits = layers.search("best python pytest frameworks", k=3, layer="project")
    assert hits, "expected at least one recall hit"
    top_record, top_score = hits[0]
    assert top_record.text == "python testing with pytest and fixtures"
    assert top_score > 0.0
    # The query itself went through the shared embedder.
    assert "best python pytest frameworks" in emb.seen


def test_embedder_none_degrades_gracefully(tmp_path):
    # No embedder -> falls back to the offline MockEmbedder; today's behavior holds.
    layers = MemoryLayers(_engine(tmp_path), embedder=None)

    layers.write("project", "py", "python testing with pytest and fixtures")
    layers.write("project", "tax", "tax deductions and write-offs for businesses")

    assert layers.read("project", "py") == "python testing with pytest and fixtures"
    hits = layers.search("python testing frameworks", k=2, layer="project")
    assert hits
    assert hits[0][0].text == "python testing with pytest and fixtures"
