"""Per-workflow project pin (context spine): 'run this brief every Monday for
project X'.

A workflow def may carry an EXPLICIT ``project_id`` pin. A pinned run stamps the
pin on its :class:`WorkflowRunRecord` and every step session (so the runtime
grounds steps in the project's instructions/knowledge), and — when the project
has a valid folder — runs steps directly IN that folder. Unpinned workflows are
untouched: the globally-active project must never leak in. Old stored defs (no
pin row, even a DB without the pin table) keep loading unchanged.
"""

from __future__ import annotations

import json

# Register the workflow tables on SQLModel.metadata BEFORE any platform is
# built (build_platform -> init_db creates the tables). Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401
import iron_jarvis.workflows.store  # noqa: F401 — registers WorkflowPinRecord

from sqlalchemy import text
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project, Session as SessionRow
from iron_jarvis.platform import build_platform
from iron_jarvis.workflows.engine import (
    Step,
    WorkflowDef,
    WorkflowEngine,
    load_workflow,
    load_workflow_toml,
)
from iron_jarvis.workflows.models import WorkflowRecord
from iron_jarvis.workflows.store import WorkflowStore


def _add_project(platform, project_id: str, root: str) -> None:
    with session_scope(platform.engine) as db:
        db.add(Project(id=project_id, name="Pinned", root=root))
        db.commit()


def _step_sessions(platform, rec) -> list[SessionRow]:
    ids = json.loads(rec.session_ids_json)
    with session_scope(platform.engine) as db:
        return [db.get(SessionRow, sid) for sid in ids]


# --- def parsing ------------------------------------------------------------


def test_load_workflow_parses_optional_project_pin():
    pinned = load_workflow(
        {"name": "wf", "steps": [{"name": "s", "task": "t"}], "project_id": "project_x"}
    )
    assert pinned.project_id == "project_x"

    # Absent and empty both mean unpinned — old def dicts keep loading.
    assert load_workflow({"name": "wf", "steps": []}).project_id is None
    assert load_workflow({"name": "wf", "steps": [], "project_id": ""}).project_id is None


def test_load_workflow_toml_carries_pin():
    toml = """
name = "monday"
project_id = "project_x"

[[steps]]
name = "brief"
task = "run the brief"
"""
    wf = load_workflow_toml(toml)
    assert wf.project_id == "project_x"
    # A TOML def without the key stays unpinned.
    assert load_workflow_toml('name = "plain"\n[[steps]]\nname = "s"\ntask = "t"\n').project_id is None


# --- store round-trip -------------------------------------------------------


def test_store_round_trips_project_pin(tmp_path):
    platform = build_platform(str(tmp_path))
    store = WorkflowStore(platform.engine)

    steps = [{"name": "s1", "agent": "builder", "task": "do the thing"}]
    store.save("pinned", steps, description="d", project_id="project_x")

    assert store.get_project_id("pinned") == "project_x"
    assert store.pins() == {"pinned": "project_x"}
    wf = store.load_def("pinned")
    assert isinstance(wf, WorkflowDef)
    assert wf.project_id == "project_x"
    assert [s.name for s in wf.steps] == ["s1"]

    # Each save rewrites the whole def: omitting project_id UNPINS.
    store.save("pinned", steps, description="d")
    assert store.get_project_id("pinned") is None
    assert store.load_def("pinned").project_id is None
    assert store.pins() == {}


def test_store_remove_deletes_pin_row(tmp_path):
    platform = build_platform(str(tmp_path))
    store = WorkflowStore(platform.engine)
    store.save("gone", [{"name": "s", "task": "t"}], project_id="project_x")

    assert store.remove("gone") is True
    assert store.get_project_id("gone") is None
    assert store.pins() == {}
    # Re-saving the same name without a pin must not resurrect the old pin.
    store.save("gone", [{"name": "s", "task": "t"}])
    assert store.get_project_id("gone") is None


def test_old_stored_defs_load_unpinned(tmp_path):
    """A def saved before pinning existed (no pin row — even no pin TABLE, as in
    an old DB) loads unchanged and reports unpinned."""
    platform = build_platform(str(tmp_path))
    # Simulate the old DB: the def row exists but the pin table does not.
    with session_scope(platform.engine) as db:
        db.add(
            WorkflowRecord(
                name="legacy",
                steps_json=json.dumps([{"name": "s1", "agent": "builder", "task": "t"}]),
            )
        )
        db.commit()
    with platform.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS workflowpinrecord"))

    store = WorkflowStore(platform.engine)  # __init__ self-heals the pin table
    assert store.get_project_id("legacy") is None
    wf = store.load_def("legacy")
    assert wf is not None
    assert wf.project_id is None
    assert [s.name for s in wf.steps] == ["s1"]


# --- engine runs ------------------------------------------------------------


async def test_pinned_run_stamps_project_and_grounds_steps(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()
    platform = build_platform(str(home))
    _add_project(platform, "project_pin", str(proj))
    engine = WorkflowEngine(platform)

    wf = WorkflowDef(
        name="monday-brief",
        steps=[
            Step(name="s1", agent="builder", task="run the brief"),
            Step(name="s2", agent="builder", task="summarize it"),
        ],
        project_id="project_pin",
    )
    rec = await engine.run(wf)

    assert rec.status == "completed"
    assert rec.project_id == "project_pin"
    sessions = _step_sessions(platform, rec)
    assert len(sessions) == 2
    for s in sessions:
        # Every step session carries the pin (the runtime injects the project's
        # instructions/knowledge off it) AND runs IN the project's folder.
        assert s.project_id == "project_pin"
        assert s.workspace_path == str(proj)


async def test_pinned_run_missing_root_skips_workspace(tmp_path):
    home = tmp_path / "home"
    gone = tmp_path / "proj-gone"  # never created on disk
    platform = build_platform(str(home))
    _add_project(platform, "project_pin", str(gone))
    engine = WorkflowEngine(platform)

    wf = WorkflowDef(
        name="monday-brief",
        steps=[Step(name="s1", agent="builder", task="run the brief")],
        project_id="project_pin",
    )
    rec = await engine.run(wf)

    # A moved/deleted folder must NOT fail the run: the pin's context still
    # applies; only the folder degrades to a normal per-session workspace.
    assert rec.status == "completed"
    assert rec.project_id == "project_pin"
    (session,) = _step_sessions(platform, rec)
    assert session.project_id == "project_pin"
    assert session.workspace_path != str(gone)


async def test_unpinned_run_unchanged(tmp_path):
    platform = build_platform(str(tmp_path))
    # Even with a project in the DB, an unpinned workflow must stay
    # project-agnostic — the globally-active project never leaks in.
    proj = tmp_path / "proj"
    proj.mkdir()
    _add_project(platform, "project_other", str(proj))
    engine = WorkflowEngine(platform)

    wf = WorkflowDef(
        name="plain",
        steps=[Step(name="s1", agent="builder", task="do a thing")],
    )
    rec = await engine.run(wf)

    assert rec.status == "completed"
    assert rec.project_id is None
    (session,) = _step_sessions(platform, rec)
    assert session.project_id is None
    assert session.workspace_path != str(proj)


async def test_scheduled_payload_pin_reaches_run(tmp_path):
    """The scheduled path builds its def via load_workflow(payload) — a payload
    carrying project_id must produce a pinned run end-to-end."""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()
    platform = build_platform(str(home))
    _add_project(platform, "project_pin", str(proj))

    payload = {
        "name": "monday",
        "project_id": "project_pin",
        "steps": [{"name": "s1", "agent": "builder", "task": "run the brief"}],
    }
    rec = await WorkflowEngine(platform).run(load_workflow(payload))

    assert rec.status == "completed"
    assert rec.project_id == "project_pin"
    (session,) = _step_sessions(platform, rec)
    assert session.project_id == "project_pin"
    assert session.workspace_path == str(proj)


# --- API surface (coordinator wiring): schemas + routes -----------------------


def test_routes_pin_round_trip_preserve_and_unpin(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    steps = [{"name": "s", "agent": "builder", "task": "t"}]

    # Save with a pin -> visible on the save response and on GET.
    saved = client.post(
        "/workflows", json={"name": "monthly", "steps": steps, "project_id": "proj_1"}
    ).json()
    assert saved["project_id"] == "proj_1"
    assert client.get("/workflows/monthly").json()["project_id"] == "proj_1"

    # Re-save WITHOUT project_id (a pin-unaware dashboard) preserves the pin.
    client.post("/workflows", json={"name": "monthly", "steps": steps})
    assert client.get("/workflows/monthly").json()["project_id"] == "proj_1"

    # Explicit "" unpins.
    client.post("/workflows", json={"name": "monthly", "steps": steps, "project_id": ""})
    assert client.get("/workflows/monthly").json()["project_id"] is None


def test_run_by_name_inherits_saved_pin(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    steps = [{"name": "s", "agent": "builder", "task": "say hi"}]
    client.post(
        "/workflows", json={"name": "pinned", "steps": steps, "project_id": "proj_9"}
    )
    run = client.post("/workflows/run", json={"name": "pinned", "steps": steps}).json()
    assert run["project_id"] == "proj_9"
    # An explicit "" on the run body forces an unpinned run despite the def pin.
    run2 = client.post(
        "/workflows/run", json={"name": "pinned", "steps": steps, "project_id": ""}
    ).json()
    assert run2["project_id"] is None
