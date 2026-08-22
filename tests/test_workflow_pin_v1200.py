"""v1.200.0 — workflow pins survive pin-unaware saves, and workflow_create
can finally set one (CONNECT-AUDIT-2026-08-22 item 4).

Two halves of one defect class:

* LATENT STORE DEFECT — ``WorkflowStore.save`` with an OMITTED ``project_id``
  DELETED an existing pin row, so any pin-unaware caller unpinned a def just
  by re-saving it (the dashboard route survived only by pre-fetching the pin
  first). The fix is the v1.164.0 remote-agent-token three-intent pattern:
  pass a project_id to SET, omit (``None``) to KEEP, pass an EXPLICIT ``""``
  to CLEAR — ``""`` was already the route's public unpin contract
  (``POST /workflows`` with ``project_id: ""``), so the dashboard's clear
  path keeps working without touching routes/workflows.py.

* TOOL GAP — ``workflow_create`` had no project input at all and called
  ``save`` without one, so an agent could never pin a workflow AND unpinned
  existing ones on every upsert. It now takes an optional ``project`` (id or
  exact name, honest error on a typo) and defaults to the producing task's
  own project: ``ctx.project_id`` when the caller resolved one (chat lanes),
  else the Session row's pin (the ArtifactStore.save inheritance pattern).
"""

from __future__ import annotations

import json

# Register the workflow tables on SQLModel.metadata BEFORE any platform is
# built (build_platform -> init_db creates the tables). Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401
import iron_jarvis.workflows.store  # noqa: F401 — registers WorkflowPinRecord

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project, Session as SessionRow
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workflows.store import WorkflowStore
from iron_jarvis.workflows.tools import WorkflowCreateTool

_STEPS = [{"name": "s1", "agent": "builder", "task": "do the thing"}]


def _store(tmp_path):
    platform = build_platform(str(tmp_path))
    return platform, WorkflowStore(platform.engine)


def _ctx(platform, tmp_path, session_id="s_test", project_id=None) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id=session_id,
        agent_run_id="r_test",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
        project_id=project_id,
    )


def _add_project(platform, project_id: str, name: str) -> None:
    with session_scope(platform.engine) as db:
        db.add(Project(id=project_id, name=name))
        db.commit()


def _add_session(platform, session_id: str, project_id: str | None) -> None:
    with session_scope(platform.engine) as db:
        db.add(SessionRow(id=session_id, task="t", project_id=project_id))
        db.commit()


# --------------------------------------------------------------------------- #
# Store: three-intent save semantics (set / keep / clear).
# --------------------------------------------------------------------------- #


def test_save_with_omitted_project_id_keeps_the_pin(tmp_path):
    """THE regression this release exists for: a pin-unaware re-save must not
    delete the pin. Asserts the VALUE, not mere row presence (the v1.164.0
    mutation lesson — a flag check alone can pass over corrupted state)."""
    _, store = _store(tmp_path)
    store.save("pinned", _STEPS, description="d", project_id="project_x")
    assert store.get_project_id("pinned") == "project_x"

    store.save("pinned", _STEPS, description="d2")  # project_id OMITTED
    assert store.get_project_id("pinned") == "project_x"
    assert store.pins() == {"pinned": "project_x"}
    # The def itself was still rewritten (KEEP applies to the pin only).
    assert store.get("pinned").description == "d2"
    # And load_def (the one stored-record -> def seam) still carries it.
    assert store.load_def("pinned").project_id == "project_x"


def test_save_with_explicit_project_id_overwrites_the_pin(tmp_path):
    _, store = _store(tmp_path)
    store.save("pinned", _STEPS, project_id="project_a")
    store.save("pinned", _STEPS, project_id="project_b")
    assert store.get_project_id("pinned") == "project_b"
    assert store.pins() == {"pinned": "project_b"}


def test_save_with_explicit_empty_string_clears_the_pin(tmp_path):
    """The dashboard's clear path: routes/workflows.py passes body.project_id
    straight through, and '' has always been its documented unpin intent —
    the store now honours it natively (whitespace-only counts as '')."""
    _, store = _store(tmp_path)
    store.save("pinned", _STEPS, project_id="project_x")
    store.save("pinned", _STEPS, project_id="")
    assert store.get_project_id("pinned") is None
    assert store.pins() == {}
    assert store.load_def("pinned").project_id is None

    store.save("pinned", _STEPS, project_id="project_x")
    store.save("pinned", _STEPS, project_id="   ")  # explicit whitespace = clear
    assert store.get_project_id("pinned") is None


def test_dashboard_prefetch_workaround_still_correct_under_keep(tmp_path):
    """routes/workflows.py (NOT edited this release) pre-fetches the pin and
    passes it back explicitly when the body omits project_id. Under keep
    semantics that explicit re-SET stays a no-op, and its None-when-unpinned
    case flows through as KEEP of nothing. Mimic both call shapes here."""
    _, store = _store(tmp_path)
    store.save("monthly", _STEPS, project_id="proj_1")
    # Pin-unaware dashboard re-save: pid = store.get_project_id(name).
    store.save("monthly", _STEPS, project_id=store.get_project_id("monthly"))
    assert store.get_project_id("monthly") == "proj_1"
    # Unpinned def, pin-unaware re-save: pre-fetch yields None -> KEEP nothing.
    store.save("plain", _STEPS)
    store.save("plain", _STEPS, project_id=store.get_project_id("plain"))
    assert store.get_project_id("plain") is None


# --------------------------------------------------------------------------- #
# Tool: workflow_create pins to the producing task's project.
# --------------------------------------------------------------------------- #


async def test_tool_pins_from_ctx_project_id(tmp_path):
    """A project-grounded chat turn (ToolContext.project_id, v1.200.0) pins
    the saved workflow without the model saying anything."""
    platform, store = _store(tmp_path)
    _add_project(platform, "proj_chat", "Chat Project")
    tool = WorkflowCreateTool(platform)
    res = await tool.execute(
        {"name": "wf", "steps": _STEPS},
        _ctx(platform, tmp_path, session_id="chat", project_id="proj_chat"),
    )
    assert res.ok
    assert res.data["project_id"] == "proj_chat"
    assert "pinned to project proj_chat" in res.output
    assert store.get_project_id("wf") == "proj_chat"


async def test_tool_pins_from_session_row_project(tmp_path):
    """An agent session (a real Session row carrying a project pin) inherits
    it — the ArtifactStore.save pattern."""
    platform, store = _store(tmp_path)
    _add_project(platform, "proj_sess", "Session Project")
    _add_session(platform, "sess_1", "proj_sess")
    tool = WorkflowCreateTool(platform)
    res = await tool.execute(
        {"name": "wf", "steps": _STEPS}, _ctx(platform, tmp_path, session_id="sess_1")
    )
    assert res.ok
    assert res.data["project_id"] == "proj_sess"
    assert store.get_project_id("wf") == "proj_sess"


async def test_tool_ctx_project_id_wins_over_session_row(tmp_path):
    platform, store = _store(tmp_path)
    _add_project(platform, "proj_ctx", "Ctx Project")
    _add_session(platform, "sess_1", "proj_other")
    tool = WorkflowCreateTool(platform)
    res = await tool.execute(
        {"name": "wf", "steps": _STEPS},
        _ctx(platform, tmp_path, session_id="sess_1", project_id="proj_ctx"),
    )
    assert res.ok
    assert store.get_project_id("wf") == "proj_ctx"


async def test_tool_without_any_project_saves_unpinned(tmp_path):
    """No ctx.project_id, no Session row -> unpinned, and the result data
    keeps its pre-v1.200.0 shape (no project_id key) so nothing keying on the
    exact dict changes."""
    platform, store = _store(tmp_path)
    tool = WorkflowCreateTool(platform)
    res = await tool.execute(
        {"name": "wf", "steps": _STEPS}, _ctx(platform, tmp_path)
    )
    assert res.ok
    assert "project_id" not in res.data
    assert store.get_project_id("wf") is None


async def test_tool_resave_without_project_keeps_existing_pin(tmp_path):
    """The end-to-end regression: an agent upserting a pinned workflow from a
    project-less context must not silently unpin it (store KEEP semantics
    reached through the tool)."""
    platform, store = _store(tmp_path)
    store.save("wf", _STEPS, project_id="proj_keep")
    tool = WorkflowCreateTool(platform)
    res = await tool.execute(
        {"name": "wf", "steps": _STEPS + [{"name": "s2", "agent": "builder", "task": "more"}]},
        _ctx(platform, tmp_path),
    )
    assert res.ok and res.data["steps"] == 2
    assert store.get_project_id("wf") == "proj_keep"
    # The steps really were rewritten — KEEP is about the pin, not the def.
    assert len(json.loads(store.get("wf").steps_json)) == 2


async def test_tool_explicit_project_by_id_and_by_name(tmp_path):
    platform, store = _store(tmp_path)
    _add_project(platform, "proj_x", "Tax Season")
    tool = WorkflowCreateTool(platform)

    res = await tool.execute(
        {"name": "by-id", "steps": _STEPS, "project": "proj_x"},
        _ctx(platform, tmp_path),
    )
    assert res.ok and store.get_project_id("by-id") == "proj_x"

    # Exact name, case-insensitive — the model knows names, not ids.
    res2 = await tool.execute(
        {"name": "by-name", "steps": _STEPS, "project": "tax season"},
        _ctx(platform, tmp_path),
    )
    assert res2.ok and store.get_project_id("by-name") == "proj_x"


async def test_tool_explicit_project_overrides_session_project(tmp_path):
    platform, store = _store(tmp_path)
    _add_project(platform, "proj_x", "Explicit")
    _add_session(platform, "sess_1", "proj_session")
    tool = WorkflowCreateTool(platform)
    res = await tool.execute(
        {"name": "wf", "steps": _STEPS, "project": "proj_x"},
        _ctx(platform, tmp_path, session_id="sess_1"),
    )
    assert res.ok
    assert store.get_project_id("wf") == "proj_x"


async def test_tool_unknown_project_is_an_honest_error_not_a_dangling_pin(tmp_path):
    """A typo'd project must refuse and name what exists — a silently-saved
    dangling pin would ground every future run in a project that isn't there.
    Nothing is saved on refusal."""
    platform, store = _store(tmp_path)
    _add_project(platform, "proj_x", "Tax Season")
    tool = WorkflowCreateTool(platform)
    res = await tool.execute(
        {"name": "wf", "steps": _STEPS, "project": "Tax Seasno"},
        _ctx(platform, tmp_path),
    )
    assert res.ok is False
    assert "Tax Seasno" in res.error and "Tax Season" in res.error
    assert store.get("wf") is None


def test_tool_schema_advertises_project(tmp_path):
    """The model can only use what the schema names (and the description is
    the docstring the model reads — it must say pinning is automatic)."""
    platform = build_platform(str(tmp_path))
    tool = WorkflowCreateTool(platform)
    assert "project" in tool.input_schema["properties"]
    assert "project" not in tool.input_schema["required"]
    assert "pinned" in tool.description
