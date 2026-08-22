"""v1.200.0 — reflex work reaches the context spine (CONNECT-AUDIT item 5).

A :class:`ReflexRule` had no project field and ``ReflexRouter._run_session``
spawned every session ungrounded, so an inbound "client emailed the missing
1099" reflex ran with ZERO client context — the whole grounding pipeline
(``runtime._project_context``, the memory fabric) never fired for reflex work.

This suite pins the four halves of the fix, offline against a real daemon:

  * MIGRATION — ``reflexrule`` exists on every live install, so the new
    ``project_id`` column must land on an OLD-shape database via
    ``core.db._reconcile_additive_columns`` (the v1.151.2 lesson: a column
    that only lands on fresh DBs ships a total failure through a green suite).
    Proven by building the pre-v1.200.0 table by hand, then booting the app.
  * SESSION GROUNDING — a rule tagged with a project spawns a session
    CARRYING it; an untagged rule stays project-agnostic (None, never the
    globally active project).
  * WORKFLOW PRECEDENCE — the def's own pin WINS: a def pinned to project A
    triggered by a rule tagged project B keeps A. The rule's project grounds
    only a def with no pin of its own.
  * HTTP — create/list/patch carry ``project_id`` with the three-intent PATCH
    contract (omitted = unchanged, "" = clear, non-empty = set).
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore

_STEPS = [{"agent": "builder", "task": "say hi"}]


def _mk_project(client: TestClient, name: str) -> str:
    resp = client.post("/projects", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _last_run(p, name: str) -> WorkflowRunRecord:
    with session_scope(p.engine) as db:
        runs = list(
            db.exec(
                select(WorkflowRunRecord).where(WorkflowRunRecord.workflow_name == name)
            )
        )
        assert runs, f"no run record for workflow '{name}'"
        return runs[-1]


# --------------------------------------------------------------------------- #
# 1. MIGRATION — the column reaches an EXISTING database, rows stay readable.
# --------------------------------------------------------------------------- #

#: The pre-v1.200.0 ``reflexrule`` — the shape every live install has. Built
#: with raw DDL so ``create_all``'s ``checkfirst=True`` sees the table and adds
#: nothing; only the reconciler can save it.
_OLD_DDL = """
CREATE TABLE reflexrule (
    id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    "match" VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    target VARCHAR NOT NULL,
    task_template VARCHAR NOT NULL,
    enabled BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    last_fired_at DATETIME,
    fire_count INTEGER NOT NULL,
    PRIMARY KEY (id)
)
"""


def _write_old_schema(db_path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(_OLD_DDL)
        con.execute("CREATE INDEX ix_reflexrule_source ON reflexrule (source)")
        con.execute(
            'INSERT INTO reflexrule (id, name, source, "match", action, target, '
            "task_template, enabled, created_at, last_fired_at, fire_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "reflex_old1",
                "legacy",
                "webhook",
                "deploy",
                "session",
                "",
                "",
                1,
                "2026-01-01 00:00:00.000000",
                None,
                0,
            ),
        )
        con.commit()
    finally:
        con.close()


def test_project_id_column_reaches_an_existing_database(tmp_path):
    """OLD-shape table + a real row → boot → column present, row readable."""
    home = tmp_path / ".ironjarvis"
    home.mkdir(parents=True)
    db_path = home / "ironjarvis.db"
    _write_old_schema(db_path)

    with TestClient(create_app(str(tmp_path))) as client:
        # The reconciler ALTERed the live table at boot.
        con = sqlite3.connect(str(db_path))
        try:
            cols = {r[1] for r in con.execute('PRAGMA table_info("reflexrule")')}
        finally:
            con.close()
        assert "project_id" in cols, (
            "the additive column did not reach an existing database — every "
            "live install would answer 'no such column' on the reflex page"
        )

        # The pre-migration row is still there, readable, and ungrounded.
        rules = client.get("/reflex/rules").json()["rules"]
        old = next((r for r in rules if r["id"] == "reflex_old1"), None)
        assert old is not None, "the ALTER must not lose existing rules"
        assert old["project_id"] is None

        # And the migrated store accepts the NEW shape (write path post-ALTER).
        pid = _mk_project(client, "Client A")
        created = client.post(
            "/reflex/rules",
            json={"name": "n", "source": "comm", "match": "x",
                  "action": "session", "project_id": pid},
        )
        assert created.status_code == 200
        assert created.json()["project_id"] == pid


# --------------------------------------------------------------------------- #
# 2. SESSION GROUNDING — the spawned session carries the rule's project.
# --------------------------------------------------------------------------- #
def test_rule_with_project_spawns_a_grounded_session(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        pid = _mk_project(client, "Client 1099")
        p.reflex.add(
            name="missing-1099", source="webhook", match="mail",
            action="session", task_template="Handle: {body}", project_id=pid,
        )

        results = asyncio.run(router.on_webhook("mail", {"x": 1}))

        assert len(results) == 1 and results[0]["kind"] == "session"
        with session_scope(p.engine) as db:
            session = db.get(Session, results[0]["session_id"])
            assert session is not None
            assert session.project_id == pid, (
                "the reflex-spawned session must CARRY the rule's project — "
                "this is the whole grounding pipeline firing or not"
            )


def test_untagged_rule_still_spawns_project_agnostic(tmp_path):
    """No project on the rule → None on the session (never a leaked default)."""
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        _mk_project(client, "Some Other Project")  # exists, must NOT leak in
        p.reflex.add(name="plain", source="webhook", match="note", action="session")

        results = asyncio.run(router.on_webhook("note", {"x": 1}))

        with session_scope(p.engine) as db:
            session = db.get(Session, results[0]["session_id"])
            assert session is not None and session.project_id is None


# --------------------------------------------------------------------------- #
# 3. WORKFLOW PRECEDENCE — the def's own pin beats the rule's project.
# --------------------------------------------------------------------------- #
def test_workflow_defs_own_pin_wins_over_the_rules_project(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        pid_a = _mk_project(client, "Project A")
        pid_b = _mk_project(client, "Project B")

        WorkflowStore(p.engine).save("pinned", _STEPS, description="t", project_id=pid_a)
        p.reflex.add(
            name="wf", source="webhook", match="go",
            action="workflow", target="pinned", project_id=pid_b,
        )

        results = asyncio.run(router.on_webhook("go", {"ref": "main"}))

        assert results[0]["ok"] is True
        run = _last_run(p, "pinned")
        assert run.project_id == pid_a, (
            "a def pinned to project A triggered by a rule tagged project B "
            "must keep A — the pin is part of what the workflow IS"
        )


def test_rules_project_grounds_an_unpinned_workflow(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        pid_b = _mk_project(client, "Project B")

        WorkflowStore(p.engine).save("unpinned", _STEPS, description="t")
        p.reflex.add(
            name="wf2", source="webhook", match="go2",
            action="workflow", target="unpinned", project_id=pid_b,
        )

        results = asyncio.run(router.on_webhook("go2", {"ref": "main"}))

        assert results[0]["ok"] is True
        run = _last_run(p, "unpinned")
        assert run.project_id == pid_b, (
            "an unpinned def fired by a grounded rule used to run "
            "project-agnostic even though the rule knew better"
        )


# --------------------------------------------------------------------------- #
# 4. HTTP — create/list/patch carry project_id (three-intent PATCH).
# --------------------------------------------------------------------------- #
def test_http_crud_carries_project_id(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        pid = _mk_project(client, "Client A")
        pid2 = _mk_project(client, "Client B")

        created = client.post(
            "/reflex/rules",
            json={"name": "r", "source": "comm", "match": "kw",
                  "action": "session", "project_id": pid},
        )
        assert created.status_code == 200
        rid = created.json()["id"]
        assert created.json()["project_id"] == pid

        listed = client.get("/reflex/rules").json()["rules"]
        assert next(r for r in listed if r["id"] == rid)["project_id"] == pid

        # Omitted field = UNCHANGED: the enabled-only toggle the dashboard has
        # always sent must not eat the grounding.
        toggled = client.patch(f"/reflex/rules/{rid}", json={"enabled": False})
        assert toggled.status_code == 200
        assert toggled.json()["enabled"] is False
        assert toggled.json()["project_id"] == pid

        # Non-empty = SET (re-ground), enabled untouched.
        moved = client.patch(f"/reflex/rules/{rid}", json={"project_id": pid2})
        assert moved.status_code == 200
        assert moved.json()["project_id"] == pid2
        assert moved.json()["enabled"] is False

        # Explicit "" = CLEAR.
        cleared = client.patch(f"/reflex/rules/{rid}", json={"project_id": ""})
        assert cleared.status_code == 200
        assert cleared.json()["project_id"] is None

        # A rule created without the field is ungrounded, not "".
        bare = client.post(
            "/reflex/rules",
            json={"name": "b", "source": "comm", "match": "z", "action": "session"},
        )
        assert bare.status_code == 200 and bare.json()["project_id"] is None
