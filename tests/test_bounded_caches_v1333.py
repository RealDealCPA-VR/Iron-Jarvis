"""Caches that lived for the daemon's life are bounded (v1.333.0, D8).

* the file-search meaning index kept every chunk's TEXT plus a float64 vector,
  over an unbounded walk: it now keeps a normalised float32 row and a
  (file, line) reference per chunk, reads snippets back for the top hits only,
  and is capped (the cap is SAID in the search's notes);
* the tool registry's read cache was bounded by entry count only (256 x 400k
  characters): it now has a total-characters budget too, and a finished
  session's reads are forgotten when its run ends;
* an MCP stdio server's stdout queue grew without bound while nothing read it.
"""

from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.filesearch import service as fs_mod
from iron_jarvis.filesearch.service import FileSearchService, SearchNotes
from iron_jarvis.mcp import client as mcp_client
from iron_jarvis.memory.embeddings import MockEmbedder
from iron_jarvis.tools import registry as reg_mod
from iron_jarvis.tools.base import ToolResult
from iron_jarvis.tools.registry import ToolRegistry


# --------------------------------------------------------------------------- #
# File search: the meaning index
# --------------------------------------------------------------------------- #
@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "a.md").write_text(
        "local first operating system\n" * 3 + "\n".join(f"line {i}" for i in range(90)),
        encoding="utf-8",
    )
    (tmp_path / "b.txt").write_text("a recipe for bread and butter\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("def operating_system():\n    return 'local'\n", encoding="utf-8")
    return tmp_path


def _brute_force(svc: FileSearchService, query: str, k: int) -> list[tuple[str, int, str]]:
    """The pre-v1.333.0 answer: float64 cosine over every chunk, stable sort."""
    emb = svc.embedder
    q = np.asarray(emb.embed(query), dtype=np.float64)
    rows = []
    for path in svc._indexed:
        text = svc._read_text(path) or ""
        for start, chunk in svc._chunks(text):
            v = np.asarray(emb.embed(chunk), dtype=np.float64)
            denom = float(np.linalg.norm(q)) * float(np.linalg.norm(v))
            rows.append((float(q @ v / denom) if denom else 0.0, str(path), start, chunk[:200]))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(p, s, t) for _, p, s, t in rows[:k]]


def test_the_meaning_index_holds_rows_not_text_and_answers_as_before(tree):
    svc = FileSearchService([tree], embedder=MockEmbedder())
    hits = svc.search_semantic("local first operating system", k=4)
    index = svc._chunk_cache
    assert index is not None
    assert index.matrix.dtype == np.float32
    assert np.allclose(np.linalg.norm(index.matrix, axis=1), 1.0, atol=1e-5)
    assert all(isinstance(a, int) and isinstance(b, int) for a, b in index.refs)
    assert [(h["path"], h["line"], h["text"]) for h in hits] == _brute_force(
        svc, "local first operating system", 4
    )


def test_a_full_meaning_index_says_how_much_it_did_not_cover(tree, monkeypatch):
    monkeypatch.setattr(fs_mod, "MAX_SEMANTIC_CHUNKS", 2)
    svc = FileSearchService([tree], embedder=MockEmbedder())
    notes = SearchNotes()
    hits = svc.search("operating system", mode="semantic", limit=10, notes=notes)
    assert len(svc._chunk_cache.refs) == 2
    assert len(hits) == 2
    assert notes.not_indexed >= 1
    assert "not searched by meaning" in notes.note()


def test_the_index_walk_is_bounded_like_every_other_walk(tmp_path, monkeypatch):
    for i in range(12):
        (tmp_path / f"f{i:02}.txt").write_text(f"file {i}\n", encoding="utf-8")
    monkeypatch.setattr(fs_mod, "DEFAULT_MAX_WALK", 5)
    svc = FileSearchService([tmp_path], embedder=MockEmbedder())
    assert svc.index() == 5
    notes = SearchNotes()
    svc.search_semantic("file", k=3, notes=notes)
    assert notes.not_indexed >= 1


# --------------------------------------------------------------------------- #
# The tool registry's read cache
# --------------------------------------------------------------------------- #
def _remember(reg: ToolRegistry, scope: str, name: str, chars: int) -> tuple:
    key = (scope, name, 1, 0, "{}")  # same file identity whatever the text length
    reg._remember_read(key, "read_file", ToolResult(ok=True, output="x" * chars), SimpleNamespace(agent_run_id=""), 0)
    return key


def test_the_read_cache_keeps_a_total_character_budget(monkeypatch):
    monkeypatch.setattr(reg_mod, "_READ_CACHE_MAX_TOTAL_CHARS", 1_000)
    reg = ToolRegistry()
    a = _remember(reg, "s1@w", "a", 400)
    b = _remember(reg, "s1@w", "b", 400)
    c = _remember(reg, "s1@w", "c", 400)  # 1,200 > 1,000: the oldest goes
    assert list(reg._read_cache) == [b, c]
    assert reg._read_cache_chars == 800
    _remember(reg, "s1@w", "b", 100)  # a refresh replaces, never double-counts
    assert reg._read_cache_chars == 500
    assert sum(len(e["output"]) for e in reg._read_cache.values()) == 500
    assert a not in reg._read_cache


def test_forgetting_a_session_drops_only_its_reads():
    reg = ToolRegistry()
    _remember(reg, "s1@C:/w", "a", 10)
    _remember(reg, "s1@C:/other", "b", 20)
    keep = _remember(reg, "chat@C:/w", "c", 30)
    keep2 = _remember(reg, "s10@C:/w", "d", 40)  # a different id that shares a prefix
    reg._scope_gen["s1@C:/w"] = 3
    reg.forget_session("s1")
    assert list(reg._read_cache) == [keep, keep2]
    assert reg._read_cache_chars == 70
    assert "s1@C:/w" not in reg._scope_gen


@pytest.mark.asyncio
async def test_a_finished_run_forgets_its_cached_reads(platform):
    orch = Orchestrator(platform)
    session = await orch.create_session("say hello")
    mine = _remember(platform.registry, f"{session.id}@C:/w", "a", 10)
    chat = _remember(platform.registry, "chat@C:/w", "b", 10)
    await orch.runtime.run(session, get_agent_definition(AgentType.BUILDER))
    assert mine not in platform.registry._read_cache
    assert chat in platform.registry._read_cache


# --------------------------------------------------------------------------- #
# MCP stdio: the unread stdout queue
# --------------------------------------------------------------------------- #
def test_an_idle_stdio_servers_chatter_is_bounded_and_the_newest_lines_kept(monkeypatch):
    monkeypatch.setattr(mcp_client, "STDOUT_QUEUE_MAX", 10)
    out: queue.Queue = queue.Queue()
    lines = [f"line {i}\n" for i in range(25)]
    mcp_client.StdioTransport._pump_stdout(iter(lines), out)
    held = []
    while not out.empty():
        held.append(out.get_nowait())
    assert held == lines[-10:] + [None]  # the newest ten, then the EOF sentinel
