"""The Memory Fabric — ONE federated ``recall()`` across every memory store.

Iron Jarvis accumulates knowledge in six places (indexed files, long-term notes,
the layered memory graph, a project's attached knowledge, self-correction
lessons, and past sessions), each behind its own index. The Memory Fabric
(:class:`iron_jarvis.memory.fabric.MemoryFabric`) federates them behind a single
``recall(query)`` — ranked + de-duplicated — plus a ``ground(query)`` that
renders a compact, prompt-ready block. This suite proves, fully offline:

  * FEDERATION: one recall surfaces hits from multiple distinct stores at once,
    ranked by score descending;
  * SAFETY: an empty query and a completely-unwired fabric both no-op (never
    raise);
  * FILTERING: ``sources=`` narrows the federation to the chosen store(s);
  * PROJECT SCOPING: a project's knowledge only participates when ``project_id``
    is supplied;
  * DEDUPE: a near-identical snippet from two rows is collapsed;
  * GROUNDING: ``ground`` renders the "# Relevant from memory" block with
    friendly source labels, and returns "" when nothing is relevant;
  * the agent-facing ``recall`` TOOL (permission-gated, default-allow); and
  * the ``GET /memory/recall`` HTTP endpoint (shape + source filtering + echo).

Everything runs against a real (offline) daemon built on a temp root — no
network, no third-party services.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.fabric import MemoryFabric
from iron_jarvis.projects.knowledge import add_knowledge
from iron_jarvis.tools.base import ToolContext


# --------------------------------------------------------------------------- #
# Seeding helpers — populate three independent stores with content that a
# single query ("rust invoice markdown table …") can match.
# --------------------------------------------------------------------------- #
def _seed_core(p) -> None:
    """Seed the layered memory, a lesson, and a past session."""
    # Layered working/semantic memory (valid layers: session/project/user/org).
    p.memory.write(
        layer="user",
        key="fav-editor",
        text="The user prefers the Zed editor for Rust work",
        scope_id=None,
    )
    # A durable user preference -> a top-weight lesson.
    p.learning.note_preference("Always summarize invoices in a markdown table")
    # A past agent run whose task/summary the query overlaps.
    with session_scope(p.engine) as db:
        db.add(
            Session(
                task="Draft the Rust invoice summary",
                summary="Produced invoices.md with a markdown table",
                result="done",
            )
        )
        db.commit()


# --------------------------------------------------------------------------- #
# (1) Federation: one recall spans multiple stores, ranked by score desc.
# --------------------------------------------------------------------------- #
def test_recall_federates_across_multiple_stores(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_core(p)

        hits = p.fabric.recall("rust invoice markdown table editor", k=8)
        sources = {h.source for h in hits}

        # The two lexical stores are deterministic matches for this query.
        assert "sessions" in sources
        assert "lessons" in sources
        # The federation genuinely blends >= 2 distinct stores (memory's cosine
        # may or may not clear the floor, so assert the robust invariant).
        assert len(sources) >= 2

        # Results come back ranked by score, highest first.
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------- #
# (2) Safety: empty query and a fully-unwired fabric no-op (never raise).
# --------------------------------------------------------------------------- #
def test_empty_query_and_unwired_fabric_are_safe(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_core(p)
        # A blank query short-circuits to nothing even with content present.
        assert p.fabric.recall("") == []
        assert p.fabric.recall("   ") == []

    # A fabric with NO stores wired must degrade gracefully, not explode.
    bare = MemoryFabric()
    assert bare.recall("anything") == []
    assert bare.ground("anything") == ""


# --------------------------------------------------------------------------- #
# (3) sources= filter narrows the federation to the chosen store(s).
# --------------------------------------------------------------------------- #
def test_sources_filter_restricts_to_one_store(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_core(p)

        hits = p.fabric.recall("rust invoice", sources=["sessions"])
        assert hits, "the seeded session should match 'rust invoice'"
        # ONLY sessions — no lessons/memory leak through the filter.
        assert all(h.source == "sessions" for h in hits)


# --------------------------------------------------------------------------- #
# (4) Project knowledge participates ONLY when project_id is supplied.
# --------------------------------------------------------------------------- #
def test_project_knowledge_is_scoped_to_project_id(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform

        # A real project + an attached knowledge item the query matches.
        with session_scope(p.engine) as db:
            proj = Project(name="Finance Q3")
            db.add(proj)
            db.commit()
            db.refresh(proj)
            pid = proj.id
        add_knowledge(
            p,
            pid,
            "Budget notes",
            "The quarterly budget spreadsheet uses SUM formulas across all client columns",
            kind="note",
        )

        query = "quarterly budget spreadsheet formulas"

        scoped = p.fabric.recall(query, k=8, project_id=pid)
        assert any(h.source == "knowledge" for h in scoped)

        # The SAME recall without project_id must NOT reach project knowledge.
        unscoped = p.fabric.recall(query, k=8)
        assert all(h.source != "knowledge" for h in unscoped)


# --------------------------------------------------------------------------- #
# (5) Dedupe: a near-identical snippet from two rows collapses to one hit.
# --------------------------------------------------------------------------- #
def test_near_duplicate_snippets_are_deduped(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform

        # Two sessions whose summaries share an identical >120-char prefix
        # (dedupe keys on the first 120 chars, lowercased) but differ in the tail.
        shared = (
            "Produced the rust invoice report with a markdown table summarizing "
            "every outstanding client balance plus the full finance review commentary block"
        )
        assert len(shared) > 120  # guards the near-duplicate premise
        with session_scope(p.engine) as db:
            db.add(Session(task="Run A", summary=shared + " alpha"))
            db.add(Session(task="Run B", summary=shared + " omega"))
            db.commit()

        hits = p.fabric.recall(
            "rust invoice markdown table finance", sources=["sessions"], k=8
        )
        # Both matched lexically, but the near-duplicate snippet is collapsed.
        assert len(hits) == 1


# --------------------------------------------------------------------------- #
# (6) ground() renders the prompt block with friendly labels; "" on no match.
# --------------------------------------------------------------------------- #
def test_ground_renders_block_and_empty_on_no_match(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_core(p)

        block = p.fabric.ground("rust invoice")
        assert block  # non-empty
        assert "# Relevant from memory" in block
        assert block.lstrip().startswith("# Relevant from memory")
        # At least one hit is rendered with a human-friendly source label.
        assert "[past run]" in block or "[lesson]" in block

        # A query that matches nothing anywhere yields an empty block (no raise).
        assert p.fabric.ground("wxyz9999 qponml8888") == ""


# --------------------------------------------------------------------------- #
# (7) The agent-facing recall TOOL runs through the registry (default-allow).
# --------------------------------------------------------------------------- #
def test_recall_tool_invokes_and_reports_by_source(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_core(p)

        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = ToolContext(
            workspace=ws,
            session_id="t",
            agent_run_id="t",
            config=p.config,
            event_bus=p.event_bus,
            engine=p.engine,
        )

        res = asyncio.run(
            p.registry.invoke("recall", {"query": "rust invoice"}, ctx, p.permissions)
        )
        assert res.ok, res.error
        by_source = res.data["by_source"]
        assert isinstance(by_source, dict) and by_source  # non-empty federation
        # count is self-consistent with the returned results.
        assert res.data["count"] == len(res.data["results"])
        # The per-source tallies sum to the total count.
        assert sum(by_source.values()) == res.data["count"]


# --------------------------------------------------------------------------- #
# (8) HTTP: GET /memory/recall — shape, source filtering, and query echo.
# --------------------------------------------------------------------------- #
def test_http_memory_recall_endpoint(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_core(p)

        r = client.get("/memory/recall", params={"q": "rust invoice", "k": 8})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "results" in body and "by_source" in body and "count" in body
        assert body["query"] == "rust invoice"  # echoed back
        assert body["count"] == len(body["results"])

        # sources= restricts the federation to the named store(s).
        r2 = client.get(
            "/memory/recall",
            params={"q": "rust invoice", "k": 8, "sources": "sessions"},
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["results"], "the seeded session should surface"
        assert all(h["source"] == "sessions" for h in body2["results"])
