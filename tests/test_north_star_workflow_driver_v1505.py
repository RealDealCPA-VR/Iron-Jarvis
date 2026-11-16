"""v1.505.0 (north-star 5.3 and the engine's part of 5.4): a workflow run
whose DRIVER raises outside a step ends Failed, with the reason, and the
engine's record reads leave the event loop.

What was read (not guessed): ``run_record`` had no outer ``try``. Its record
writes (``_persist``), its reads (``_get_record``) and the pinned folder's look
up (``_project_folder``) can raise ("database is locked" once SQLite's 30 s
busy wait runs out during Compact or a backup), and the only handler was a log
line in the orchestrator's done-callback, so the row stayed "running" until the
next start-up's reconcile. ``resume_after_answer`` and ``resume_interrupted``
had the same hole (their caller had already claimed the row "resuming").

The database fault is raised by the test at the seam the engine calls; the
settle write itself goes to the real database.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import asyncio
import json
import sqlite3
import threading

import pytest
from sqlalchemy.exc import OperationalError

from iron_jarvis.core.db import dumps, session_scope
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine, step_to_dict
from iron_jarvis.workflows.models import WorkflowRunRecord

LOCKED_WORDS = (
    "The run stopped because Iron Jarvis could not save its progress: the "
    "database was busy, perhaps during Compact or a backup. Run it again."
)


class _Quick(Tool):
    name = "quick_v1505"
    description = "fixture"
    reversibility = Reversibility.READONLY
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output="done")


def _locked() -> OperationalError:
    return OperationalError("UPDATE workflowrunrecord", {}, sqlite3.OperationalError("database is locked"))


def _platform(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(_Quick())
    return platform


def _wf(name="rent-list") -> WorkflowDef:
    return WorkflowDef(
        name=name,
        steps=[
            Step(name="First", kind="tool", tool=_Quick.name),
            Step(name="Second", kind="tool", tool=_Quick.name),
        ],
    )


def _row(platform, run_id) -> WorkflowRunRecord:
    with session_scope(platform.engine) as db:
        return db.get(WorkflowRunRecord, run_id)


def _completed(platform, run_id) -> list:
    return [
        e for e in platform.event_bus.history
        if e.type == "workflow.completed" and e.payload.get("run_id") == run_id
    ]


def _set(platform, run_id, **fields) -> None:
    with session_scope(platform.engine) as db:
        rec = db.get(WorkflowRunRecord, run_id)
        for k, v in fields.items():
            setattr(rec, k, v)
        db.add(rec)
        db.commit()


async def test_a_driver_whose_record_write_raises_ends_failed_with_the_reason(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    engine = WorkflowEngine(platform)
    rec = await asyncio.to_thread(engine.create_record, _wf())
    real = engine._persist
    calls = {"n": 0}

    async def persist(run_id, **fields):
        calls["n"] += 1
        if calls["n"] == 3:  # the batch write after the first step
            raise _locked()
        return await real(run_id, **fields)

    monkeypatch.setattr(engine, "_persist", persist)
    with pytest.raises(OperationalError):
        await engine.run_record(rec, _wf())
    row = _row(platform, rec.id)
    assert row.status == "failed", "a run whose driver raised must not stay running"
    assert row.finished_at is not None and row.current_session_id is None
    assert json.loads(row.notes_json)[-1] == LOCKED_WORDS
    # What the first step finished is kept (the step wrote it itself).
    assert json.loads(row.outputs_json)["First"]["status"] == "completed"
    ev = _completed(platform, rec.id)
    assert len(ev) == 1 and ev[0].payload["status"] == "failed"
    assert ev[0].payload["reason"] == LOCKED_WORDS
    assert ev[0].payload["workflow"] == "rent-list"


async def test_another_fault_is_said_in_the_run_words_never_the_engine_text(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    engine = WorkflowEngine(platform)
    rec = await asyncio.to_thread(engine.create_record, _wf())

    def folder(project_id):
        raise RuntimeError("KeyError: 'root' in session_scope")

    monkeypatch.setattr(engine, "_project_folder", folder)
    with pytest.raises(RuntimeError):
        await engine.run_record(rec, _wf())
    row = _row(platform, rec.id)
    assert row.status == "failed"
    note = json.loads(row.notes_json)[-1]
    assert note.startswith("The run failed: ")
    assert "KeyError" not in note and "RuntimeError" not in note and "session_scope" not in note


async def test_a_resume_after_an_answer_whose_read_raises_ends_failed(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    engine = WorkflowEngine(platform)
    wf = WorkflowDef(
        name="ask-first",
        steps=[Step(name="Ok", kind="ask", message="Go?"), Step(name="Then", kind="tool", tool=_Quick.name)],
    )
    rec = await asyncio.to_thread(engine.create_record, wf)
    # The answer route's claim: waiting -> resuming.
    _set(platform, rec.id, status="resuming",
         waiting_json=dumps({"index": 0, "step": "Ok", "question": "Go?"}))
    rec = _row(platform, rec.id)

    def boom(run_id):
        raise _locked()

    monkeypatch.setattr(engine, "_get_record", boom)
    with pytest.raises(OperationalError):
        await engine.resume_after_answer(rec, "yes")
    row = _row(platform, rec.id)
    assert row.status == "failed", f"left {row.status!r} until the next start-up"
    assert json.loads(row.notes_json)[-1] == LOCKED_WORDS
    assert [e.payload["status"] for e in _completed(platform, rec.id)] == ["failed"]


async def test_a_resume_of_an_interrupted_run_whose_read_raises_ends_failed(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    engine = WorkflowEngine(platform)
    rec = await asyncio.to_thread(engine.create_record, _wf("resumer"))
    _set(platform, rec.id, status="resuming")
    rec = _row(platform, rec.id)

    def boom(run_id):
        raise _locked()

    monkeypatch.setattr(engine, "_get_record", boom)
    with pytest.raises(OperationalError):
        await engine.resume_interrupted(rec)
    row = _row(platform, rec.id)
    assert row.status == "failed"
    assert [e.payload["status"] for e in _completed(platform, rec.id)] == ["failed"]


async def test_a_cancelled_run_whose_driver_raises_ends_stopped_and_one_settle_only(tmp_path, monkeypatch):
    """A row the user asked to stop ends Stopped, not Failed; and a raise that
    passes through the nested wrappers (an answer's resume drives
    ``run_record``) is settled ONCE."""
    platform = _platform(tmp_path)
    engine = WorkflowEngine(platform)
    wf = WorkflowDef(
        name="ask-then",
        steps=[Step(name="Ok", kind="ask", message="Go?"), Step(name="Then", kind="tool", tool=_Quick.name)],
    )
    rec = await asyncio.to_thread(engine.create_record, wf)
    _set(platform, rec.id, status="resuming",
         waiting_json=dumps({"index": 0, "step": "Ok", "question": "Go?"}))
    rec = _row(platform, rec.id)
    real = engine._persist

    async def persist(run_id, **fields):
        if fields.get("notes") is not None:  # run_record's first write
            _set(platform, run_id, status="cancelling")
            raise _locked()
        return await real(run_id, **fields)

    monkeypatch.setattr(engine, "_persist", persist)
    with pytest.raises(OperationalError):
        await engine.resume_after_answer(rec, "yes")
    row = _row(platform, rec.id)
    assert row.status == "cancelled"
    assert [e.payload["status"] for e in _completed(platform, rec.id)] == ["cancelled"]


async def test_a_run_that_already_ended_is_never_rewritten(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    engine = WorkflowEngine(platform)
    rec = await asyncio.to_thread(engine.create_record, _wf("ender"))
    real_publish = platform.event_bus.publish

    async def publish(type_, payload=None, **kw):
        if type_ == "workflow.completed":
            raise RuntimeError("bus down")
        return await real_publish(type_, payload, **kw)

    monkeypatch.setattr(platform.event_bus, "publish", publish)
    with pytest.raises(RuntimeError):
        await engine.run_record(rec, _wf("ender"))
    assert _row(platform, rec.id).status == "completed"


async def test_the_engine_reads_its_record_off_the_event_loop(tmp_path, monkeypatch):
    """Plan 5.4 (this file's part): every ``_get_record`` and the folder look
    up run on a worker thread, never on the loop."""
    platform = _platform(tmp_path)
    engine = WorkflowEngine(platform)
    loop_thread = threading.get_ident()
    on_loop: list[str] = []
    real_get, real_folder = engine._get_record, engine._project_folder

    def get(run_id):
        if threading.get_ident() == loop_thread:
            on_loop.append("_get_record")
        return real_get(run_id)

    def folder(project_id):
        if threading.get_ident() == loop_thread:
            on_loop.append("_project_folder")
        return real_folder(project_id)

    monkeypatch.setattr(engine, "_get_record", get)
    monkeypatch.setattr(engine, "_project_folder", folder)
    wf = WorkflowDef(
        name="reader",
        steps=[Step(name="Do", kind="tool", tool=_Quick.name), Step(name="Ok", kind="ask", message="Go?")],
    )
    rec = await asyncio.to_thread(engine.create_record, wf)
    parked = await engine.run_record(rec, wf)
    assert parked.status == "waiting"
    _set(platform, rec.id, status="resuming")
    done = await engine.resume_after_answer(_row(platform, rec.id), "yes")
    assert done.status == "completed"
    rec2 = await asyncio.to_thread(engine.create_record, _wf("again"))
    _set(platform, rec2.id, status="resuming")
    await engine.resume_interrupted(_row(platform, rec2.id))
    assert on_loop == [], f"read on the event loop: {on_loop}"


@pytest.mark.parametrize("spawn_refuses", [False, True])
async def test_the_workflow_run_tool_writes_its_record_off_the_event_loop(tmp_path, monkeypatch, spawn_refuses):
    """Plan 5.4 (``workflows/tools.py``): chat's ``workflow_run`` created the
    run's row, and wrote a refused launch's ``failed``, on the loop."""
    from types import SimpleNamespace

    from iron_jarvis.workflows.store import WorkflowStore
    from iron_jarvis.workflows.tools import WorkflowRunTool

    platform = _platform(tmp_path)
    WorkflowStore(platform.engine).save("Monthly close", [step_to_dict(s) for s in _wf().steps])
    loop_thread = threading.get_ident()
    on_loop: list[str] = []
    real_create, real_update = WorkflowEngine.create_record, WorkflowEngine._update_record

    def create(self, *a, **kw):
        if threading.get_ident() == loop_thread:
            on_loop.append("create_record")
        return real_create(self, *a, **kw)

    def update(self, *a, **kw):
        if threading.get_ident() == loop_thread:
            on_loop.append("_update_record")
        return real_update(self, *a, **kw)

    monkeypatch.setattr(WorkflowEngine, "create_record", create)
    monkeypatch.setattr(WorkflowEngine, "_update_record", update)
    launched: list = []

    def spawn(run_id, coro):
        if spawn_refuses:
            coro.close()
            return None
        task = asyncio.ensure_future(coro)
        launched.append(task)
        return task

    platform.orchestrator = SimpleNamespace(spawn_managed=spawn)
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="r",
                      config=platform.config, event_bus=platform.event_bus, engine=platform.engine)
    res = await WorkflowRunTool(platform).execute({"name": "Monthly close"}, ctx)
    for t in launched:
        await t
    assert res.ok is (not spawn_refuses), res.error
    if spawn_refuses:
        run_id = [r for r in _rows_named(platform, "Monthly close")][0]
        assert run_id.status == "failed"
    assert on_loop == [], f"written on the event loop: {on_loop}"


def _rows_named(platform, name):
    from sqlmodel import select

    with session_scope(platform.engine) as db:
        return list(db.exec(select(WorkflowRunRecord).where(WorkflowRunRecord.workflow_name == name)))


def test_the_reason_words_are_one_definition():
    from iron_jarvis.workflows.engine import driver_failure_words

    assert driver_failure_words(_locked()) == LOCKED_WORDS
    assert driver_failure_words(RuntimeError("x")).startswith("The run failed: ")
    assert step_to_dict(Step(name="a")) is not None  # import sanity
