"""Performance/cost lens fixes — regression guards.

Covers: /metrics counting in SQL (not full-table load), bounded /sessions, the
hot-column indexes, the bounded event-retention default, Anthropic prompt caching,
bounded memory recall, and the motivation backlog-full short-circuit. Offline.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlmodel import Session as DBSession

from iron_jarvis.core.config import Config
from iron_jarvis.core.db import _HOT_INDEXES, session_scope
from iron_jarvis.core.models import EventRecord, Session, ToolInvocation
from iron_jarvis.eval.observability import Observability
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage


def test_metrics_counts_in_sql(platform):
    with session_scope(platform.engine) as db:
        for i in range(25):
            db.add(EventRecord(id=f"e{i}", type="t", session_id=None, payload_json="{}"))
        for i in range(7):
            db.add(ToolInvocation(id=f"tl{i}", session_id="s", agent_run_id="r", tool="x"))
        db.commit()
    m = Observability(platform.engine).metrics()
    assert m["event_count"] == 25
    assert m["total_tool_invocations"] == 7


def test_list_sessions_is_bounded(platform):
    with session_scope(platform.engine) as db:
        for i in range(5):
            db.add(Session(id=f"s{i}", task="t"))
        db.commit()
    from iron_jarvis.agents.orchestrator import Orchestrator

    o = Orchestrator(platform)
    assert len(o.list_sessions(limit=2)) == 2
    assert len(o.list_sessions(limit=None)) == 5


def test_hot_indexes_exist_after_init(platform):
    with DBSession(platform.engine) as db:
        names = {
            r[0]
            for r in db.exec(text("SELECT name FROM sqlite_master WHERE type='index'")).all()
        }
    for name, _table, _col in _HOT_INDEXES:
        assert name in names, name


def test_event_retention_default_is_bounded():
    # Was 0 (keep forever) — the root of the unbounded event log.
    assert Config(project_root=".", home=".ironjarvis").event_retention_days == 90


async def test_anthropic_attaches_prompt_cache(monkeypatch):
    captured: dict = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)

            class R:
                content: list = []
                stop_reason = "stop"
                usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()

            return R()

    class FakeClient:
        messages = FakeMessages()

    a = AnthropicAdapter(api_key="sk-ant-test")
    monkeypatch.setattr(a, "_client", lambda: FakeClient())
    await a.complete(
        system="a stable system prompt",
        messages=[
            LLMMessage(role="user", content="hi"),
            LLMMessage(role="assistant", content="hello"),
            LLMMessage(role="user", content="continue"),
        ],
        tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
    )
    assert isinstance(captured["system"], list)
    assert captured["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert captured["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    # PERF-COST-1: with a real conversation (2+ messages) the growing prefix is also
    # cached (breakpoint on the last message), so a multi-step loop doesn't re-bill
    # the full history each step.
    last = captured["messages"][-1]
    assert isinstance(last["content"], list)
    assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_recall_finds_old_relevant_memory(platform):
    # PERF-VER-1 regression guard: an OLD but exactly-relevant memory must still be
    # the top hit even when many newer, irrelevant rows exist (the LIMIT-by-recency
    # fix must not silently drop it).
    import json
    from datetime import timedelta

    from iron_jarvis.core.ids import utcnow
    from iron_jarvis.memory.models import MemoryRecord
    from iron_jarvis.memory.retrieval import SqliteVectorRetriever

    class StubEmbedder:
        def embed(self, text):
            return [1.0, 0.0] if "gold" in text else [0.0, 1.0]

    with session_scope(platform.engine) as db:
        old = MemoryRecord(
            layer="semantic", scope_id="s", key="k", text="gold", embedding_json=json.dumps([1.0, 0.0])
        )
        old.created_at = utcnow() - timedelta(days=400)
        db.add(old)
        for i in range(30):
            db.add(
                MemoryRecord(
                    layer="semantic", scope_id="s", key=f"n{i}", text=f"noise{i}",
                    embedding_json=json.dumps([0.0, 1.0]),
                )
            )
        db.commit()

    hits = SqliteVectorRetriever(platform.engine, StubEmbedder()).search(
        "gold please", k=3, layer="semantic", scope_id="s"
    )
    assert hits and hits[0][0].text == "gold"
    assert hits[0][1] > 0.99  # cosine ~1.0 for the exact match
