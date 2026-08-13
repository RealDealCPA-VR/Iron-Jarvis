"""``GET /sessions/teams`` — the board-wide child→parent session map (v1.168.0, P5).

One flat ``{"parents": {child_session_id: parent_session_id}}`` derived from
``AgentRun.parent_id`` links, so the Kanban can nest team members under their
parent's card without probing ``/sessions/{id}/team`` per session.

The quiet ways this can fail, each pinned below:

* ROUTE SHADOWING — ``GET /sessions/{session_id}`` is registered in the same
  module; if ``/sessions/teams`` lands after it, every call becomes a 404
  ("session not found" for the id "teams").
* SELF-MAPPING — a continuation links two runs of the SAME session; treating
  that as a team edge would nest a session under itself.
* DANGLING LINKS — a ``parent_id`` naming a run that no longer exists, or a
  run whose ``session_id`` is blank, must map nowhere (never a KeyError, never
  a phantom edge).
* NON-DETERMINISM — a session whose runs disagree about their parent must
  resolve the same way on every poll (earliest link wins).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    Session as SessionRow,
    SessionStatus,
)
from iron_jarvis.daemon.app import create_app


def _client(tmp_path, **kw):
    return TestClient(create_app(str(tmp_path)), **kw)


def _add_run(db, rid, sid, parent, created_at=None):
    run = AgentRun(
        id=rid,
        session_id=sid,
        parent_id=parent,
        agent_type=AgentType.BUILDER,
        state=AgentState.COMPLETED,
    )
    if created_at is not None:
        run.created_at = created_at
    db.add(run)


def _seed_tree(engine):
    """s-root spawns s-c1 + s-c1b; s-c1 spawns s-c2 (all via run links)."""
    with session_scope(engine) as db:
        for sid in ("s-root", "s-c1", "s-c1b", "s-c2"):
            db.add(SessionRow(id=sid, task=f"task {sid}", status=SessionStatus.COMPLETED))
        _add_run(db, "r-root", "s-root", None)
        _add_run(db, "r-c1", "s-c1", "r-root")
        _add_run(db, "r-c1b", "s-c1b", "r-root")
        _add_run(db, "r-c2", "s-c2", "r-c1")
        db.commit()


# --------------------------------------------------------------------------- #
# Route registration + empty shape
# --------------------------------------------------------------------------- #
def test_teams_route_is_not_shadowed_and_empty_db_is_an_empty_map(tmp_path):
    """If ``/sessions/{session_id}`` matched first this would be a 404 with
    detail "session not found" — the exact regression the registration-order
    comment in routes/sessions.py guards."""
    with _client(tmp_path) as client:
        r = client.get("/sessions/teams")
        assert r.status_code == 200, r.text
        assert r.json() == {"parents": {}}


def test_existing_session_routes_are_untouched(tmp_path):
    """Additive guarantee: the list stays ``{"sessions": [...]}``, GET-one stays
    NESTED, and a solo run (no delegation links) contributes no edge."""
    with _client(tmp_path) as client:
        sid = client.post(
            "/sessions", json={"task": "solo note", "wait": True}
        ).json()["id"]
        listed = client.get("/sessions").json()
        assert [s["id"] for s in listed["sessions"]] == [sid]
        one = client.get(f"/sessions/{sid}").json()
        assert one["session"]["id"] == sid  # nested shape preserved
        assert "transcript" in one
        # The real mock run wrote AgentRun rows — none with a parent link.
        assert client.get("/sessions/teams").json() == {"parents": {}}


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def test_map_resolves_run_links_to_session_ids_exactly(tmp_path):
    with _client(tmp_path) as client:
        _seed_tree(client.app.state.platform.engine)
        r = client.get("/sessions/teams")
        assert r.status_code == 200
        # Exact VALUES — a mutation swapping child/parent, or resolving the
        # parent RUN id instead of its session, cannot pass this.
        assert r.json() == {
            "parents": {
                "s-c1": "s-root",
                "s-c1b": "s-root",
                "s-c2": "s-c1",
            }
        }


def test_same_session_run_links_produce_no_self_edge(tmp_path):
    """A continuation run whose parent_id points at an earlier run of the SAME
    session is not a delegation — mapping it would nest a card under itself."""
    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        with session_scope(engine) as db:
            db.add(SessionRow(id="s-a", task="t", status=SessionStatus.COMPLETED))
            _add_run(db, "r-a1", "s-a", None)
            _add_run(db, "r-a2", "s-a", "r-a1")  # continuation, same session
            db.commit()
        assert client.get("/sessions/teams").json() == {"parents": {}}


def test_blank_session_and_dangling_parent_run_are_skipped(tmp_path):
    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        with session_scope(engine) as db:
            db.add(SessionRow(id="s-root", task="t", status=SessionStatus.COMPLETED))
            db.add(SessionRow(id="s-kid", task="t", status=SessionStatus.COMPLETED))
            _add_run(db, "r-root", "s-root", None)
            _add_run(db, "r-blank", "", "r-root")  # run outlived its session
            _add_run(db, "r-kid", "s-kid", "r-gone")  # parent run deleted
            db.commit()
        # Neither corrupt link becomes an edge; neither raises.
        assert client.get("/sessions/teams").json() == {"parents": {}}


def test_parent_link_may_resolve_via_a_blank_session_run_never(tmp_path):
    """A parent run with a BLANK session_id cannot anchor an edge either —
    ``run_session`` must not contain the empty string as a value."""
    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        with session_scope(engine) as db:
            db.add(SessionRow(id="s-kid", task="t", status=SessionStatus.COMPLETED))
            _add_run(db, "r-ghostparent", "", None)  # parent's session gone
            _add_run(db, "r-kid", "s-kid", "r-ghostparent")
            db.commit()
        assert client.get("/sessions/teams").json() == {"parents": {}}


def test_teams_endpoint_issues_only_bounded_agentrun_queries(tmp_path):
    """AgentRun is unbounded run history and every mounted board polls this
    endpoint every 8s — a bare SELECT over the whole table grows forever
    (v1.168.0 review finding). Pin the shape: every agentrun SELECT the
    request issues must carry a WHERE clause (parent_id IS NOT NULL for the
    link pass, id IN (...) for the parent lookup), and the bounded rewrite
    must still resolve the exact same map."""
    from sqlalchemy import event

    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        _seed_tree(engine)
        with session_scope(engine) as db:
            # Solo runs (no parent link) are the vast majority of history —
            # the endpoint must not read them at all.
            for i in range(5):
                _add_run(db, f"r-solo-{i}", "s-root", None)
            db.commit()

        statements: list[str] = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            r = client.get("/sessions/teams")
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        assert r.status_code == 200, r.text
        assert r.json() == {
            "parents": {"s-c1": "s-root", "s-c1b": "s-root", "s-c2": "s-c1"}
        }
        agentrun_selects = [
            s
            for s in statements
            if s.lstrip().upper().startswith("SELECT") and "agentrun" in s.lower()
        ]
        assert agentrun_selects, "expected the endpoint to query agentrun"
        for stmt in agentrun_selects:
            assert "WHERE" in stmt.upper(), f"unbounded agentrun scan: {stmt}"


def test_conflicting_links_resolve_to_the_earliest_deterministically(tmp_path):
    """A session rerun under a different parent must not flap between parents
    on every poll — rows are walked in created_at order and the FIRST link
    wins (setdefault)."""
    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        t0 = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 8, 12, 11, 0, 0, tzinfo=timezone.utc)
        with session_scope(engine) as db:
            for sid in ("s-p1", "s-p2", "s-kid"):
                db.add(SessionRow(id=sid, task="t", status=SessionStatus.COMPLETED))
            _add_run(db, "r-p1", "s-p1", None, created_at=t0)
            _add_run(db, "r-p2", "s-p2", None, created_at=t0)
            _add_run(db, "r-kid-early", "s-kid", "r-p1", created_at=t0)
            _add_run(db, "r-kid-late", "s-kid", "r-p2", created_at=t1)
            db.commit()
        assert client.get("/sessions/teams").json() == {
            "parents": {"s-kid": "s-p1"}
        }
