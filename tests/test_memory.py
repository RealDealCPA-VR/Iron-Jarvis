"""Memory subsystem tests (§21 layered memory, §22 retrieval). Fully offline."""

from __future__ import annotations

import numpy as np
import pytest

import iron_jarvis.memory.models  # noqa: F401  (register table before init_db)
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.memory.embeddings import MockEmbedder
from iron_jarvis.memory.layers import MemoryLayers
from iron_jarvis.memory.tools import memory_tools
from iron_jarvis.tools.base import ToolContext


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


@pytest.fixture
def layers(engine):
    return MemoryLayers(engine, MockEmbedder())


@pytest.fixture
def ctx(engine, tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


def _tool(layers, name):
    return {t.name: t for t in memory_tools(layers)}[name]


async def test_search_returns_closest_topic(layers, ctx):
    layers.write("project", "py", "python testing with pytest and fixtures")
    layers.write("project", "tax", "tax deductions and write-offs for businesses")
    layers.write("project", "docker", "docker containers and image layers")

    search = _tool(layers, "memory_search")
    res = await search.execute({"query": "best python testing frameworks"}, ctx)

    assert res.ok
    top = res.data["results"][0]
    assert top["text"] == "python testing with pytest and fixtures"
    assert top["score"] > 0.0


def test_read_hit_and_miss(layers):
    layers.write("project", "greeting", "hello world")
    assert layers.read("project", "greeting") == "hello world"
    assert layers.read("project", "missing") is None


def test_write_is_upsert_not_duplicate(layers):
    layers.write("project", "note", "first version")
    layers.write("project", "note", "second version")

    rows = layers.list("project")
    note_rows = [r for r in rows if r.key == "note"]
    assert len(note_rows) == 1
    assert layers.read("project", "note") == "second version"


def test_mock_embedder_deterministic_and_unit_norm():
    emb = MockEmbedder()
    v1 = emb.embed("layered memory retrieval")
    v2 = emb.embed("layered memory retrieval")
    assert v1 == v2  # deterministic: same text -> identical vector
    assert np.isclose(np.linalg.norm(np.asarray(v1)), 1.0)


async def test_read_tool_and_memory_updated_event(layers, ctx):
    seen = []
    ctx.event_bus.add_handler(lambda ev: seen.append(ev))

    write = _tool(layers, "memory_write")
    read = _tool(layers, "memory_read")

    w = await write.execute({"layer": "project", "key": "k", "text": "v"}, ctx)
    assert w.ok
    assert any(ev.type == EventType.MEMORY_UPDATED for ev in seen)

    hit = await read.execute({"layer": "project", "key": "k"}, ctx)
    assert hit.ok and hit.output == "v" and hit.data["found"] is True

    miss = await read.execute({"layer": "project", "key": "nope"}, ctx)
    assert miss.ok and miss.data["found"] is False
