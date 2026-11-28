"""Leap 06-b review: the report says nothing the real run would not do.

The reviewer's probes, each red before its fix:

* R1: a write the real tool would refuse (a path outside the work folder) is
  "Would be refused" in the tool's own words, never "would write".
* R2: an If whose condition reads a step the rehearsal HELD guesses no lane:
  the rehearsal ends there, "would decide using what “W” would have made".
* R4: a For each over a held step's output ends there too.
* R5: a read that fails after a held change (the file a held write would
  have made) ends the rehearsal, never "failed".
* A paused goal's rehearsal says the goal will not run until it is resumed.
* A rehearsal's run is no run of the workflow anywhere it is counted: the
  phone's /status, the system map, the live work an update waits on, the run
  history's retention window.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.workflows.engine import WorkflowEngine
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


def _no_model():
    async def never(**kw):
        raise AssertionError("a workflow of fixed steps called a model in its rehearsal")
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
    with session_scope(p.engine) as db:
        proj = Project(name="Acme", root=str(folder))
        db.add(proj)
        db.commit()
        pid = proj.id
    return app, p, sent, folder, pid, tmp_path


async def _rehearse(p, kind: str, name: str) -> dict:
    started = await RR.start(p, kind, name)
    driver = RR._DRIVERS.get(started["id"])
    if driver is not None:
        await driver
    return await asyncio.to_thread(RR.report, p, started["id"])


async def _real(p, name: str) -> WorkflowRunRecord:
    wf = WorkflowStore(p.engine).load_def(name)
    engine = WorkflowEngine(p)
    return await engine.run_record(engine.create_record(wf), wf)


async def test_r1_a_write_the_real_tool_refuses_is_refused_in_the_report(daemon):
    app, p, sent, folder, pid, tmp_path = daemon
    outside = tmp_path / "elsewhere" / "out.txt"
    WorkflowStore(p.engine).save("Outside", [
        {"name": "Write", "kind": "tool", "tool": "write_file", "args": {"path": str(outside), "content": "x"}},
    ], project_id=pid)
    view = await _rehearse(p, "workflow", "Outside")
    assert view["would"] == [], view["would"]
    assert [r["words"] for r in view["refused"]] == [f"write {outside}"]
    assert "escapes the session workspace" in view["refused"][0]["why"], view["refused"]
    assert view["sentence"] != "This would write 1 file. Nothing was changed."
    # The real run refuses it the same way.
    real = await _real(p, "Outside")
    assert real.status == "failed" and not outside.exists()


async def test_r1_a_rename_out_of_the_folder_is_refused_too(daemon):
    app, p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Move out", [
        {"name": "Move", "kind": "tool", "tool": "rename_file",
         "args": {"path": "notes.txt", "new_path": str(tmp_path / "gone.txt")}},
    ], project_id=pid)
    view = await _rehearse(p, "workflow", "Move out")
    assert view["would"] == [] and len(view["refused"]) == 1, view
    assert (folder / "notes.txt").exists()


R2_STEPS = [
    {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "made.txt", "content": "x"}},
    {"name": "Check", "kind": "if", "condition": {"left": "{{W}}", "op": "contains", "right": "wrote"},
     "steps": [{"name": "Fine", "kind": "notify", "message": "Write worked."}],
     "else_steps": [{"name": "Oops", "kind": "notify", "message": "Write failed!"}]},
    {"name": "After", "kind": "notify", "message": "Done."},
]


async def test_r2_an_if_on_a_held_steps_output_guesses_no_lane(daemon):
    app, p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Branchy", R2_STEPS, project_id=pid)
    view = await _rehearse(p, "workflow", "Branchy")
    assert view["status"] == "ready", view
    words = [w["words"] for w in view["would"]]
    assert "write made.txt" in words
    assert not any("Write failed!" in w or "Write worked." in w for w in words), words
    assert view["stopped_at"]["where"] == "would decide using what “W” would have made"
    assert view["stopped_words"] == (
        "It stopped where it would decide using what “W” would have made, "
        "so what comes after was not rehearsed: “Fine”, “Oops” and “After”."
    )
    assert view["sentence"] == "This would write 1 file. Nothing was changed."
    assert sent == []
    # Control: the real run takes the Then lane.
    real = await _real(p, "Branchy")
    assert real.status == "completed"
    assert any("Write worked." in str(a) for a in sent) and not any("Write failed!" in str(a) for a in sent)


async def test_r4_a_for_each_over_a_held_steps_output_ends_there(daemon):
    app, p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Loopy", [
        {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "made.txt", "content": "x"}},
        {"name": "Each", "kind": "for_each", "over": {"from": "{{W.data.paths}}"},
         "steps": [{"name": "Tell", "kind": "notify", "message": "{{item}}"}]},
    ], project_id=pid)
    view = await _rehearse(p, "workflow", "Loopy")
    assert view["status"] == "ready", view
    assert view["error"] == ""
    assert view["stopped_at"]["where"] == "would work through what “W” would have made"
    assert view["stopped_at"]["not_rehearsed"] == ["“Tell”"]  # its body (review 2)
    assert sent == []


async def test_r5_a_read_that_fails_after_a_held_write_ends_the_rehearsal(daemon):
    app, p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Write then read", [
        {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "new.txt", "content": "x"}},
        {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "new.txt"}},
        {"name": "Tell", "kind": "notify", "message": "{{Read}}"},
    ], project_id=pid)
    view = await _rehearse(p, "workflow", "Write then read")
    assert view["status"] == "ready", view
    assert view["error"] == ""
    assert view["stopped_at"]["where"] == "would read what “W” would have written"
    assert view["stopped_at"]["not_rehearsed"] == ["“Tell”"]
    assert view["guessed_after"] is True
    assert sent == []
    # Control: the real run completes.
    assert (await _real(p, "Write then read")).status == "completed"


async def test_a_read_that_fails_with_no_held_change_before_it_still_fails(daemon):
    app, p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Just read", [
        {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "missing.txt"}},
    ], project_id=pid)
    view = await _rehearse(p, "workflow", "Just read")
    assert view["status"] == "failed", view
    assert view["stopped_at"] is None


async def test_a_paused_goals_rehearsal_says_it_will_not_run_until_resumed(daemon):
    from iron_jarvis.providers.adapters.base import LLMResponse

    app, p, sent, folder, pid, tmp_path = daemon

    async def done(**kw):
        yield {"type": "text", "text": "Planned."}
        yield {"type": "final", "response": LLMResponse(text="Planned."), "provider": "mock", "model": "mock"}

    p.router.stream = done
    goal = await p.goal_engine.create_goal(name="Plan", contract_text="Write a plan.", project_id=pid,
                                           budget={"max_tokens": 1000})
    p.goal_engine.store.transition(goal.id, "paused")
    view = await _rehearse(p, "goal", goal.id)
    assert view["status"] == "ready", view
    assert view["note"] == "This goal is paused, so it will not run until you resume it."
    assert "This goal is paused" in RR._report_words(view, "goal", "Plan")
    p.goal_engine.store.transition(goal.id, "active")
    assert (await asyncio.to_thread(RR.report, p, view["id"]))["note"] == ""


async def test_a_rehearsals_run_is_counted_nowhere(daemon):
    from iron_jarvis.reflex.commands import CommandInterpreter
    from iron_jarvis.workflows.store import prune_runs

    app, p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Slow", [{"name": "Tell", "kind": "notify", "message": "Hi."}], project_id=pid)
    wf = WorkflowStore(p.engine).load_def("Slow")
    engine = WorkflowEngine(p)
    # One live rehearsal run, one real finished run, then ten finished
    # rehearsals after it.
    live = engine.create_record(wf, rehearsal_id="reh_example")
    real = engine.create_record(wf)
    with session_scope(p.engine) as db:
        row = db.get(WorkflowRunRecord, real.id)
        row.status, row.started_at = "completed", utcnow() - timedelta(days=2)
        db.add(row)
        for i in range(10):
            db.add(WorkflowRunRecord(workflow_name="Slow", status="completed", rehearsal_id=f"reh_{i}",
                                     started_at=utcnow() - timedelta(hours=i)))
        db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        sysmap = (await c.get("/ui/system-map")).json()
        activity = (await c.get("/system/activity")).json()
    node = next(n for a in sysmap["areas"] for n in a["nodes"] if n["key"] == "workflows")
    assert (node["live"], node["used"]) == (0, 1), node  # the real run only
    assert activity["running_workflow_runs"] == 0, activity
    said = await CommandInterpreter(p, p.orchestrator, None)._cmd_status("")
    assert "Workflows: 0 live, 1 total" in said, said
    # Retention keeps the real run however many rehearsals came after it.
    prune_runs(p.engine, keep=1)
    with session_scope(p.engine) as db:
        assert db.get(WorkflowRunRecord, real.id) is not None
        assert db.get(WorkflowRunRecord, live.id) is not None


async def test_an_if_on_an_agent_step_that_only_read_decides_as_usual(daemon):
    """An agent step whose run held nothing is no held step: an If on its
    words decides, and the rehearsal goes on."""
    from iron_jarvis.providers.adapters.base import LLMResponse

    app, p, sent, folder, pid, tmp_path = daemon

    async def done(**kw):
        yield {"type": "text", "text": "Planned the week."}
        yield {"type": "final", "response": LLMResponse(text="Planned the week."), "provider": "mock",
               "model": "mock"}

    p.router.stream = done
    WorkflowStore(p.engine).save("Think then decide", [
        {"name": "Think", "kind": "agent", "agent": "builder", "task": "Plan the week."},
        {"name": "Check", "kind": "if", "condition": {"left": "{{Think}}", "op": "contains", "right": "Planned"},
         "steps": [{"name": "Yes", "kind": "notify", "message": "Planned."}]},
    ], project_id=pid)
    view = await _rehearse(p, "workflow", "Think then decide")
    assert view["status"] == "ready", view
    assert view["stopped_at"] is None
    assert any(w["words"].startswith("send the message “Planned.”") for w in view["would"]), view["would"]


async def test_a_read_that_fails_inside_a_for_each_of_several_at_once_ends_the_rehearsal(daemon):
    app, p, sent, folder, pid, tmp_path = daemon
    WorkflowStore(p.engine).save("Both at once", [
        {"name": "Each", "kind": "for_each", "over": {"from": '["a", "b"]'}, "parallel": 2,
         "steps": [
             {"name": "W", "kind": "tool", "tool": "write_file", "args": {"path": "{{item}}.txt", "content": "x"}},
             {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "{{item}}.txt"}},
         ]},
    ], project_id=pid)
    view = await _rehearse(p, "workflow", "Both at once")
    assert view["status"] == "ready", view
    assert view["stopped_at"]["where"] == "would read what “W” would have written"
    assert "cannot ask you" not in str(view)
