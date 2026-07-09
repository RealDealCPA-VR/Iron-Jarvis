"""Total Recall tests (§22 retrieval — real local embedder + persistent cache).

Fully OFFLINE: there is no Ollama server here. Every Ollama interaction is either
mocked (injected fake HTTP client / reachability probe) or exercised through the
graceful fallback to the deterministic MockEmbedder.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.core.config import load_config
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.filesearch.service import FileSearchService
from iron_jarvis.ltm import LongTermMemory, MarkdownBrainConnector
from iron_jarvis.memory.embedding_cache import EmbeddingRecord, EmbeddingStore, text_hash
from iron_jarvis.memory.embeddings import (
    CachingEmbedder,
    EmbedderError,
    MockEmbedder,
    OllamaEmbedder,
    build_embedder,
)
from iron_jarvis.memory.fabric import MemoryFabric
from iron_jarvis.memory.recall import recall_tools
from iron_jarvis.tools.base import ToolContext


# -- fakes ------------------------------------------------------------------


class FakeResp:
    def __init__(self, status: int = 200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeHTTP:
    """Minimal httpx.Client stand-in: returns a canned response or raises."""

    def __init__(self, resp: FakeResp | None = None, exc: Exception | None = None):
        self.resp = resp
        self.exc = exc
        self.calls = 0

    def post(self, url, json=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.resp


class CountingEmbedder:
    model = "count"

    def __init__(self):
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return [float(len(text))]


@pytest.fixture
def engine(tmp_path: Path):
    e = make_engine(str(tmp_path / "recall.db"))
    init_db(e)
    return e


# -- OllamaEmbedder: parse + fail-loud --------------------------------------


def test_ollama_embedder_parses_mocked_response():
    http = FakeHTTP(resp=FakeResp(200, {"embedding": [0.1, 0.2, 0.3]}))
    emb = OllamaEmbedder("http://localhost:11434", model="nomic-embed-text", http=http)
    vec = emb.embed("hello world")
    assert vec == [0.1, 0.2, 0.3]
    assert http.calls == 1


def test_ollama_embedder_parses_api_embed_shape():
    # Newer /api/embed shape: {"embeddings": [[...]]} — also accepted.
    http = FakeHTTP(resp=FakeResp(200, {"embeddings": [[1.0, 2.0]]}))
    emb = OllamaEmbedder("http://localhost:11434", http=http)
    assert emb.embed("x") == [1.0, 2.0]


def test_ollama_embedder_non_200_raises():
    emb = OllamaEmbedder("http://localhost:11434", http=FakeHTTP(resp=FakeResp(500, {})))
    with pytest.raises(EmbedderError):
        emb.embed("x")


def test_ollama_embedder_connection_error_raises():
    emb = OllamaEmbedder("http://localhost:11434", http=FakeHTTP(exc=ConnectionError("down")))
    with pytest.raises(EmbedderError):
        emb.embed("x")


def test_ollama_embedder_empty_vector_raises():
    emb = OllamaEmbedder("http://localhost:11434", http=FakeHTTP(resp=FakeResp(200, {"embedding": []})))
    with pytest.raises(EmbedderError):
        emb.embed("x")


# -- build_embedder: selection + offline fallback ---------------------------


def test_build_embedder_defaults_to_mock_offline(tmp_path: Path):
    # A real default Config has no ollama_base_url -> the probe never succeeds.
    config = load_config(tmp_path)
    emb = build_embedder(config)
    assert isinstance(emb, MockEmbedder)


def test_build_embedder_falls_back_when_ollama_down():
    config = SimpleNamespace(
        embedder_provider="auto",
        embedder_model="nomic-embed-text",
        ollama_base_url="http://localhost:11434",
    )
    emb = build_embedder(config, reachable=lambda _url: False)
    assert isinstance(emb, MockEmbedder)


def test_build_embedder_explicit_ollama_falls_back_when_down():
    config = SimpleNamespace(
        embedder_provider="ollama",
        embedder_model="nomic-embed-text",
        ollama_base_url="http://localhost:11434",
    )
    emb = build_embedder(config, reachable=lambda _url: False)
    assert isinstance(emb, MockEmbedder)


def test_build_embedder_returns_ollama_when_reachable():
    config = SimpleNamespace(
        embedder_provider="auto",
        embedder_model="nomic-embed-text",
        ollama_base_url="http://localhost:11434",
    )
    http = FakeHTTP(resp=FakeResp(200, {"embedding": [1.0, 2.0]}))
    emb = build_embedder(config, reachable=lambda _url: True, http=http)
    assert isinstance(emb, OllamaEmbedder)
    assert emb.embed("hi") == [1.0, 2.0]


def test_build_embedder_mock_provider_ignores_ollama():
    config = SimpleNamespace(
        embedder_provider="mock",
        embedder_model="nomic-embed-text",
        ollama_base_url="http://localhost:11434",
    )
    emb = build_embedder(config, reachable=lambda _url: True)
    assert isinstance(emb, MockEmbedder)


def test_build_embedder_wraps_in_cache_with_engine(engine):
    config = SimpleNamespace(
        embedder_provider="mock", embedder_model="x", ollama_base_url=None
    )
    emb = build_embedder(config, engine)
    assert isinstance(emb, CachingEmbedder)
    assert isinstance(emb.base, MockEmbedder)


# -- EmbeddingStore: round-trip + incremental -------------------------------


def test_embedding_store_round_trip(engine):
    store = EmbeddingStore(engine)
    assert store.get("hello", model="mock") is None
    store.put("hello", [1.0, 2.0, 3.0], model="mock")
    assert store.get("hello", model="mock") == [1.0, 2.0, 3.0]


def test_embedding_store_incremental_recompute_on_change(engine):
    store = EmbeddingStore(engine)
    store.put("v1", [1.0], model="mock", source="f.py", chunk_id="f.py:1")
    assert store.get("v1", model="mock", source="f.py", chunk_id="f.py:1") == [1.0]

    # Same chunk, changed text -> cache MISS (must be recomputed).
    assert store.get("v2", model="mock", source="f.py", chunk_id="f.py:1") is None
    # Same text, different model -> MISS (vectors are model-specific).
    assert store.get("v1", model="other", source="f.py", chunk_id="f.py:1") is None

    # Upsert replaces the row in place (no duplicate).
    store.put("v2", [2.0], model="mock", source="f.py", chunk_id="f.py:1")
    assert store.get("v2", model="mock", source="f.py", chunk_id="f.py:1") == [2.0]

    from sqlmodel import Session, select

    with Session(engine) as db:
        rows = list(
            db.exec(
                select(EmbeddingRecord).where(
                    EmbeddingRecord.source == "f.py",
                    EmbeddingRecord.chunk_id == "f.py:1",
                    EmbeddingRecord.model == "mock",
                )
            )
        )
    assert len(rows) == 1
    assert rows[0].text_hash == text_hash("v2")


def test_caching_embedder_is_incremental_and_survives_restart(engine):
    base = CountingEmbedder()
    ce = CachingEmbedder(base, engine)
    assert ce.embed("abc") == [3.0]
    assert ce.embed("abc") == [3.0]
    assert base.calls == 1  # second call served from the persistent cache

    # A fresh embedder over the same engine == a daemon restart: still cached.
    base2 = CountingEmbedder()
    ce2 = CachingEmbedder(base2, engine)
    assert ce2.embed("abc") == [3.0]
    assert base2.calls == 0


# -- filesearch end-to-end with the factory embedder ------------------------


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "readme.md").write_text(
        "# Iron Jarvis\nLocal-first AI operating system.\n", encoding="utf-8"
    )
    (root / "src" / "beta.py").write_text(
        "value = 42\nTODO refactor\n", encoding="utf-8"
    )
    return root


def test_filesearch_semantic_with_factory_embedder(tmp_path: Path, engine):
    root = _make_tree(tmp_path)
    config = load_config(tmp_path)  # offline -> Mock, wrapped in cache
    emb = build_embedder(config, engine)
    svc = FileSearchService([root], embedder=emb)
    hits = svc.search_semantic("local first operating system", k=3)
    assert hits
    assert Path(hits[0]["path"]).name == "readme.md"
    assert hits[0]["score"] > 0.0

    # The cache was populated by the chunk embeddings.
    from sqlmodel import Session, select

    with Session(engine) as db:
        assert db.exec(select(EmbeddingRecord)).first() is not None


# -- recall tool: spans files + long-term memory ----------------------------


def _ctx(engine, tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


async def test_recall_tool_blends_files_and_ltm(tmp_path: Path, engine):
    root = _make_tree(tmp_path)
    config = load_config(tmp_path)
    emb = build_embedder(config, engine)
    svc = FileSearchService([root], embedder=emb)

    ltm = LongTermMemory()
    brain = MarkdownBrainConnector(tmp_path / "brain", embedder=emb)
    ltm.register(brain)
    brain.append("Operating System Notes", "Local-first AI operating system design")

    # The recall tool now federates every store through the Memory Fabric.
    fabric = MemoryFabric(filesearch=svc, ltm=ltm)
    tool = recall_tools(fabric)[0]
    assert tool.name == "recall"
    res = await tool.execute({"query": "local first operating system", "k": 3}, _ctx(engine, tmp_path))
    assert res.ok
    assert res.data["count"] >= 1
    # Files (readme.md) and long-term memory (brain) both surface — the Fabric
    # tags them "files" and "notes" respectively.
    sources = {r["source"] for r in res.data["results"]}
    assert "files" in sources
    assert "notes" in sources
    assert any(
        "readme.md" in (r.get("ref") or "")
        for r in res.data["results"]
        if r["source"] == "files"
    )


async def test_recall_tool_survives_no_ltm(tmp_path: Path, engine):
    root = _make_tree(tmp_path)
    config = load_config(tmp_path)
    emb = build_embedder(config, engine)
    svc = FileSearchService([root], embedder=emb)
    fabric = MemoryFabric(filesearch=svc, ltm=None)
    tool = recall_tools(fabric)[0]
    res = await tool.execute({"query": "operating system"}, _ctx(engine, tmp_path))
    assert res.ok
    # With no LTM wired, no "notes" hits can surface.
    assert all(r["source"] != "notes" for r in res.data["results"])


# --- swarm-review fixes: strong probe + URL normalization -------------------
def test_ollama_url_strips_v1_and_trailing_slash():
    from iron_jarvis.memory.embeddings import OllamaEmbedder

    assert OllamaEmbedder("http://localhost:11434/v1").base_url == "http://localhost:11434"
    assert OllamaEmbedder("http://localhost:11434/").base_url == "http://localhost:11434"
    assert OllamaEmbedder("http://h:1/v1/").base_url == "http://h:1"


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _Http:
    def __init__(self, status, body):
        self._r = _Resp(status, body)

    def post(self, *a, **k):
        return self._r


class _Cfg:
    def __init__(self, provider="ollama", base="http://localhost:11434"):
        self.embedder_provider = provider
        self.embedder_model = "nomic-embed-text"
        self.ollama_base_url = base


def test_build_embedder_falls_back_when_model_not_pulled():
    # Server reachable but the embed model returns 404 -> strong probe fails -> Mock.
    from iron_jarvis.memory.embeddings import MockEmbedder, build_embedder

    emb = build_embedder(_Cfg(), http=_Http(404, {}))
    assert isinstance(emb, MockEmbedder)


def test_build_embedder_uses_ollama_when_embed_works():
    from iron_jarvis.memory.embeddings import OllamaEmbedder, build_embedder

    emb = build_embedder(_Cfg(base="http://localhost:11434/v1"), http=_Http(200, {"embedding": [0.1, 0.2, 0.3]}))
    assert isinstance(emb, OllamaEmbedder)
    assert emb.base_url == "http://localhost:11434"  # /v1 stripped for /api/embeddings
    assert emb.embed("hi") == [0.1, 0.2, 0.3]
