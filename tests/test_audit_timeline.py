"""TX-01 audit read-model (§30): AuditTimeline projection + /audit route.

Seeds a session that ran one allowed + one denied tool, an ``llm.completed``
token event, and a lifecycle completion, then asserts the canonical timeline
collapses them into the {tool, decision, token, lifecycle} kinds, time-ordered
with stable ids, and that the /audit route filters + keyset-paginates.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    EventRecord,
    PermissionMode,
    Session,
    SessionStatus,
    ToolInvocation,
    UndoJournal,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import audit as audit_routes
from iron_jarvis.eval.observability import AuditTimeline

_BASE = datetime(2026, 1, 1, 12, 0, 0)


def _client(tmp_path) -> TestClient:
    """App with the audit routes registered (the coordinator wires this in
    create_app; here we attach the module directly so the route is exercised
    independent of that wiring). The routes only need ``d.platform``."""
    app = create_app(str(tmp_path))
    audit_routes.register(app, SimpleNamespace(platform=app.state.platform))
    return TestClient(app)


def _seed(engine, session_id="session_a") -> None:
    """One session: token event, allowed+undoable tool, denied tool, completion."""
    with session_scope(engine) as db:
        db.add(
            Session(
                id=session_id,
                origin="user_chat",
                project_id="project_x",
                status=SessionStatus.COMPLETED,
                created_at=_BASE,
            )
        )
        db.add(AgentRun(id="run_a", session_id=session_id, created_at=_BASE))
        # llm.completed -> token (t+1)
        db.add(
            EventRecord(
                id="evt_llm",
                type="llm.completed",
                session_id=session_id,
                payload_json=(
                    '{"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.02,'
                    ' "provider": "mock", "model": "m", "run_id": "run_a"}'
                ),
                created_at=_BASE + timedelta(seconds=1),
            )
        )
        # allowed reversible tool -> tool(ok) + undoable (t+2)
        db.add(
            ToolInvocation(
                id="tool_ok",
                session_id=session_id,
                agent_run_id="run_a",
                tool="write_file",
                verdict=PermissionMode.ALLOW,
                ok=True,
                reversibility="reversible",
                created_at=_BASE + timedelta(seconds=2),
            )
        )
        db.add(
            UndoJournal(
                action_id="tool_ok",
                session_id=session_id,
                agent_run_id="run_a",
                tool="write_file",
                kind="file_restore",
                reversible=True,
                created_at=_BASE + timedelta(seconds=2),
            )
        )
        # denied tool -> decision(deny) (t+3)
        db.add(
            ToolInvocation(
                id="tool_deny",
                session_id=session_id,
                agent_run_id="run_a",
                tool="run_shell",
                verdict=PermissionMode.DENY,
                ok=False,
                output="denied by policy",
                reversibility="irreversible",
                created_at=_BASE + timedelta(seconds=3),
            )
        )
        # session.completed -> lifecycle (t+4)
        db.add(
            EventRecord(
                id="evt_done",
                type="session.completed",
                session_id=session_id,
                payload_json="{}",
                created_at=_BASE + timedelta(seconds=4),
            )
        )
        db.commit()


def test_timeline_projects_canonical_kinds(platform):
    _seed(platform.engine)
    res = AuditTimeline(platform.engine).query(session_id="session_a")
    entries = res["entries"]

    # One entry per audited row, collapsed into the four canonical kinds.
    assert {e["kind"] for e in entries} == {"tool", "decision", "token", "lifecycle"}
    assert len(entries) == 4  # tool call NOT double-listed with its event
    assert res["total"] == 4

    # Newest-first, time-ordered.
    ts = [e["ts"] for e in entries]
    assert ts == sorted(ts, reverse=True)

    by_id = {e["id"]: e for e in entries}
    # Allowed reversible tool with a live UndoJournal row -> undoable.
    ok_tool = by_id["tool_ok"]
    assert ok_tool["kind"] == "tool"
    assert ok_tool["ok"] is True
    assert ok_tool["reversible"] is True
    assert ok_tool["undoable"] is True
    assert ok_tool["actor"] == "user_chat"  # derived from Session.origin
    assert ok_tool["project_id"] == "project_x"

    # Denied tool -> decision.
    deny = by_id["tool_deny"]
    assert deny["kind"] == "decision"
    assert deny["verdict"] == "deny"
    assert deny["ok"] is False
    assert deny["undoable"] is False

    # Token entry carries the LLM usage.
    tok = by_id["evt_llm"]
    assert tok["kind"] == "token"
    assert tok["input_tokens"] == 10
    assert tok["output_tokens"] == 5
    assert tok["cost_usd"] == 0.02

    # Ids are stable across calls.
    res2 = AuditTimeline(platform.engine).query(session_id="session_a")
    assert [e["id"] for e in res2["entries"]] == [e["id"] for e in entries]


def test_timeline_undoable_false_when_undone(platform):
    _seed(platform.engine)
    with session_scope(platform.engine) as db:
        inv = db.get(ToolInvocation, "tool_ok")
        inv.undone_at = _BASE + timedelta(seconds=5)
        db.add(inv)
        db.commit()
    entry = {
        e["id"]: e
        for e in AuditTimeline(platform.engine).query(session_id="session_a")["entries"]
    }["tool_ok"]
    assert entry["reversible"] is True
    assert entry["undoable"] is False  # already undone


def test_audit_route_filters_and_pagination(tmp_path):
    client = _client(tmp_path)
    # Use the app's real engine (isolated per TestClient) so we control the data.
    engine = client.app.state.platform.engine
    _seed(engine)

    # kind filter -> only the executed tool.
    r = client.get("/audit", params={"session_id": "session_a", "kind": "tool"})
    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body["entries"]] == ["tool_ok"]

    # kind=decision -> the denied tool.
    r = client.get("/audit", params={"session_id": "session_a", "kind": "decision"})
    assert [e["id"] for e in r.json()["entries"]] == ["tool_deny"]

    # tool filter -> only that tool's invocation.
    r = client.get("/audit", params={"session_id": "session_a", "tool": "write_file"})
    assert [e["id"] for e in r.json()["entries"]] == ["tool_ok"]

    # session filter isolates a session (empty for an unknown one).
    r = client.get("/audit", params={"session_id": "nope"})
    assert r.json()["entries"] == []

    # Keyset pagination: two pages of 2 cover all four rows with no overlap.
    r1 = client.get("/audit", params={"session_id": "session_a", "limit": 2})
    page1 = r1.json()
    assert len(page1["entries"]) == 2
    assert page1["next_cursor"]
    r2 = client.get(
        "/audit",
        params={
            "session_id": "session_a",
            "limit": 2,
            "before": page1["next_cursor"],
        },
    )
    page2 = r2.json()
    ids1 = [e["id"] for e in page1["entries"]]
    ids2 = [e["id"] for e in page2["entries"]]
    assert set(ids1).isdisjoint(ids2)
    assert set(ids1) | set(ids2) == {"evt_llm", "tool_ok", "tool_deny", "evt_done"}
    # Still newest-first across the page break.
    all_ts = [e["ts"] for e in page1["entries"] + page2["entries"]]
    assert all_ts == sorted(all_ts, reverse=True)


def test_audit_export_markdown_and_json(tmp_path):
    client = _client(tmp_path)
    _seed(client.app.state.platform.engine)

    r = client.get("/audit/export", params={"session_id": "session_a", "format": "json"})
    assert r.status_code == 200
    assert r.json()["count"] == 4

    r = client.get("/audit/export", params={"session_id": "session_a"})
    assert r.status_code == 200
    assert "audit timeline" in r.text
    assert "write_file" in r.text
