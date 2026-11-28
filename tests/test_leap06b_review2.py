"""Leap 06-b review 2: four more sentences made true.

A. Every held tool that refuses a path in its own code is refused in the
   report too (multi_edit, apply_patch, notebook_edit, process_start,
   batch_documents, stack_apply, download_manage, pixio_upload,
   transcribe_recording), driven through the REAL registry with a
   rehearsal on the call: one rehearsed row ``would="refuse"``, nothing run.
B. A stop at an If or a For each names the block's own steps first.
C. A step that ran on a held step's output is held too (transitively): an If
   on it guesses no lane.
D. A For each of several at once that stops counts only the items never
   started, and names the ones under way.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workflows.store import WorkflowStore

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


def _no_model():
    async def never(**kw):
        raise AssertionError("a model was called")
        yield  # pragma: no cover

    return never


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.router.stream = _no_model()
    sent: list[Any] = []
    p.notifier.notify = lambda *a, **k: sent.append(a) or {"mock": {"ok": True}}  # type: ignore[method-assign]
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")
    (folder / "book.ipynb").write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}',
                                       encoding="utf-8")
    with session_scope(p.engine) as db:
        proj = Project(name="Acme", root=str(folder))
        db.add(proj)
        db.commit()
        pid = proj.id
    return p, sent, folder, pid, tmp_path


OUTSIDE = "../outside.txt"
CASES = {
    "multi_edit": {"path": OUTSIDE, "edits": [{"old": "a", "new": "b"}]},
    "apply_patch": {"patch": f"*** Begin Patch\n*** Add File: {OUTSIDE}\n+hi\n*** End Patch"},
    "notebook_edit": {"path": "../outside.ipynb", "new_source": "x"},
    "process_start": {"command": "echo hi", "cwd": ".."},
    "batch_documents": {"folder": "no-such-folder"},
    "stack_apply": {"plan_id": "plan_none"},
    "download_manage": {"download": "nothing.pdf", "action": "move", "to": "x.pdf"},
    "pixio_upload": {"path": "no-such-picture.png"},
    "transcribe_recording": {"path": "no-such-recording.m4a"},
}


@pytest.mark.parametrize("name", sorted(CASES))
async def test_a_held_tool_the_real_run_refuses_is_refused(daemon, name):
    p, sent, folder, pid, tmp_path = daemon
    tool = p.registry.get(name)
    assert tool is not None, name
    ctx = ToolContext(workspace=folder, session_id=f"reh_run_{name}", agent_run_id=f"reh_run_{name}",
                      config=p.config, event_bus=p.event_bus, engine=p.engine, rehearsal_id="reh_example")
    res = await p.registry.invoke(name, CASES[name], ctx, p.permissions, None,
                                  session_allow=[tool.perm_key()])
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == f"reh_run_{name}")))
    assert [(r.rehearsed, r.would) for r in rows] == [(True, "refuse")], [(r.tool, r.would, r.output) for r in rows]
    assert res.ok is False
    assert not (tmp_path / "outside.txt").exists() and not (tmp_path / "outside.ipynb").exists()


async def _rehearse(p, name: str) -> dict:
    started = await RR.start(p, "workflow", name)
    driver = RR._DRIVERS.get(started["id"])
    if driver is not None:
        await driver
    return await asyncio.to_thread(RR.report, p, started["id"])


async def test_b_a_stop_at_an_if_names_its_own_lanes_first(daemon):
    p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Lanes", [
        {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "made.txt", "content": "x"}},
        {"name": "Check", "kind": "if", "condition": {"left": "{{W}}", "op": "contains", "right": "wrote"},
         "steps": [{"name": "Fine", "kind": "notify", "message": "ok"}],
         "else_steps": [{"name": "Oops", "kind": "notify", "message": "no"}]},
    ], project_id=pid)
    view = await _rehearse(p, "Lanes")
    assert view["stopped_at"]["not_rehearsed"] == ["“Fine”", "“Oops”"], view["stopped_at"]
    assert "Nothing comes after it" not in view["stopped_words"]


async def test_b_a_stop_at_a_for_each_names_its_body_first(daemon):
    p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Body", [
        {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "made.txt", "content": "x"}},
        {"name": "Each", "kind": "for_each", "over": {"from": "{{W.data.paths}}"},
         "steps": [{"name": "Tell", "kind": "notify", "message": "{{item}}"}]},
        {"name": "End", "kind": "notify", "message": "done"},
    ], project_id=pid)
    view = await _rehearse(p, "Body")
    assert view["stopped_at"]["not_rehearsed"] == ["“Tell”", "“End”"], view["stopped_at"]


async def test_c_a_step_that_ran_on_a_held_steps_output_is_held_too(daemon):
    p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Chain", [
        {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "made.txt", "content": "x"}},
        {"name": "S", "kind": "tool", "tool": "file_search", "args": {"query": "{{W}}"}},
        {"name": "Check", "kind": "if", "condition": {"left": "{{S}}", "op": "contains", "right": "made.txt"},
         "steps": [{"name": "Found", "kind": "notify", "message": "then"}],
         "else_steps": [{"name": "Missed", "kind": "notify", "message": "else"}]},
    ], project_id=pid)
    view = await _rehearse(p, "Chain")
    assert view["status"] == "ready", view
    words = [w["words"] for w in view["would"]]
    assert not any("“else”" in w or "“then”" in w for w in words), words
    assert view["stopped_at"]["where"] == "would decide using what “S” would have made"
    assert sent == []


async def test_d_a_for_each_of_several_at_once_counts_only_items_never_started(daemon):
    p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Four", [
        {"name": "Each", "kind": "for_each", "over": {"from": '["a", "b", "c", "d"]'}, "parallel": 2,
         "steps": [
             {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "{{item}}.txt", "content": "x"}},
             {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "{{item}}.txt"}},
         ]},
    ], project_id=pid)
    view = await _rehearse(p, "Four")
    after = view["stopped_at"]["not_rehearsed"]
    # Two items ran at once and both stopped at their read; c and d never started.
    assert after == ["the rest of the items under way", "2 more items of “Each”"], after
    assert not Path(folder / "a.txt").exists()
