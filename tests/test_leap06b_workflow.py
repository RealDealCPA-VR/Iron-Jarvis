"""Leap 06-b: rehearse a workflow, and see the list.

Done when: a three-step workflow with a write, a notify and an ask rehearses
to a report naming all three and changes NOTHING.

Driven through the REAL daemon (``create_app``) and its served routes
(``POST /rehearsals``, ``GET /rehearsals/{id}``): the workflow engine's own
door (``create_record(…, rehearsal_id=)``), the real registry, permission
engine and ledger. A workflow of fixed steps runs with no model at all: the
model is a double that fails the test if it is ever called. The background
run is AWAITED (its driver task), never slept on.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.workflows.models import WorkflowRecord, WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


def _no_model():
    async def never(**kw):
        raise AssertionError("a workflow of fixed steps called a model in its rehearsal")
        yield  # pragma: no cover - makes this an async generator

    return never


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.router.stream = _no_model()
    events: list[dict[str, Any]] = []
    real = p.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        events.append({"type": str(getattr(type, "value", type)), "payload": payload or {}})
        return await real(type, payload, session_id=session_id, **kw)

    p.event_bus.publish = spy
    sent: list[Any] = []
    p.notifier.notify = lambda *a, **k: sent.append((a, k)) or {"mock": {"ok": True}}  # type: ignore[method-assign]
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")
    return app, p, events, sent, folder


def _save(p, folder: Path, name: str, steps: list[dict]) -> WorkflowRecord:
    """Save a workflow pinned to a project whose folder is ``folder`` (its
    tool steps run there, as a real run's would)."""
    from iron_jarvis.core.models import Project

    with session_scope(p.engine) as db:
        proj = db.exec(select(Project).where(Project.root == str(folder))).first()
        if proj is None:
            proj = Project(name="Acme", root=str(folder))
            db.add(proj)
            db.commit()
        pid = proj.id
    return WorkflowStore(p.engine).save(name, steps, description="", project_id=pid)


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _rehearse(app, kind: str, name: str) -> dict:
    async with await _client(app) as c:
        r = await c.post("/rehearsals", json={"kind": kind, "id": name})
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        driver = RR._DRIVERS.get(rid)
        if driver is not None:
            await driver  # the background run, awaited (never slept on)
        r = await c.get(f"/rehearsals/{rid}")
        assert r.status_code == 200, r.text
        return r.json()


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {str(f.relative_to(folder)): f.read_bytes() for f in sorted(folder.rglob("*")) if f.is_file()}


def _rows(p, **where) -> list[ToolInvocation]:
    with session_scope(p.engine) as db:
        stmt = select(ToolInvocation)
        for k, v in where.items():
            stmt = stmt.where(getattr(ToolInvocation, k) == v)
        out = list(db.exec(stmt))
        for r in out:
            db.expunge(r)
        return out


def _tables_empty(p) -> dict[str, int]:
    from iron_jarvis.core.models import ToolIntent
    from iron_jarvis.core.models import UndoJournal

    with session_scope(p.engine) as db:
        return {
            "undo": len(list(db.exec(select(UndoJournal)))),
            "intent": len(list(db.exec(select(ToolIntent)))),
            "sessions": len(list(db.exec(select(SessionRow)))),
        }


async def test_a_write_a_notify_and_an_ask_rehearse_to_a_report_naming_all_three_and_change_nothing(daemon):
    app, p, events, sent, folder = daemon
    target = folder / "summary.txt"
    _save(p, folder, "Monday summary", [
        {"name": "Write", "kind": "tool", "tool": "write_file",
         "args": {"path": "summary.txt", "content": "the week in one page"}},
        {"name": "Tell", "kind": "notify", "message": "The summary is ready."},
        {"name": "Check", "kind": "ask", "message": "Send it to the team?"},
        {"name": "Archive", "kind": "tool", "tool": "rename_file",
         "args": {"path": "notes.txt", "new_path": "old-notes.txt"}},
    ])
    before = _snapshot(folder)

    view = await _rehearse(app, "workflow", "Monday summary")

    assert view["status"] == "ready", view
    assert view["subject"]["kind"] == "workflow" and view["subject"]["name"] == "Monday summary"
    words = [w["words"] for w in view["would"]]
    assert words[0] == "write summary.txt", words
    assert words[1].startswith("send the message “The summary is ready.”"), words
    assert words[2] == "ask you: Send it to the team?", words
    assert len(words) == 3, words
    assert view["sentence"] == (
        "This would write 1 file, send 1 message and ask you 1 question. Nothing was changed."
    )
    assert view["counts"] == {"write": 1, "send": 1, "ask": 1}
    # The ask ENDS the rehearsal and names what came after it.
    assert view["stopped_at"] == {"question": "Send it to the team?", "not_rehearsed": ["“Archive”"]}
    assert view["stopped_words"] == (
        "It stopped where it would ask you, so what comes after was not rehearsed: “Archive”."
    )
    assert view["guessed_after"] is False and view["guessed_words"] == ""

    # NOTHING changed: the folder, the destinations, the bus, the journal.
    assert _snapshot(folder) == before, "a rehearsal changed the folder"
    assert not target.exists()
    assert sent == [], "a rehearsal sent a message"
    assert not [e for e in events if e["type"].startswith("workflow.")], "a rehearsal published a workflow event"
    assert _tables_empty(p) == {"undo": 0, "intent": 0, "sessions": 0}, "no journal, no intent, no model run"
    # Never parked, never asked: its run ended, and is no run of the workflow.
    with session_scope(p.engine) as db:
        run = db.exec(select(WorkflowRunRecord)).one()
    assert run.rehearsal_id == view["id"] and run.status == "completed" and run.waiting_json == ""
    async with await _client(app) as c:
        listed = (await c.get("/workflows/runs")).json()["runs"]
        resumed = await c.post(f"/workflows/runs/{run.id}/resume")
    assert listed == [], "a rehearsal's run shows in the workflow's history"
    assert resumed.status_code == 409
    # The held write is ONE rehearsed row of the registry's own (the real gate).
    held = _rows(p, session_id=run.id, tool="write_file")
    assert [(r.rehearsed, r.would, r.ok) for r in held] == [(True, "run", True)]


async def test_a_real_run_of_the_same_workflow_does_all_three(daemon):
    """Control: the rehearsal's guards are what kept the folder and the
    destinations untouched (a real run writes and sends, then parks)."""
    from iron_jarvis.workflows.engine import WorkflowEngine

    app, p, events, sent, folder = daemon
    target = folder / "summary.txt"
    _save(p, folder, "Monday summary", [
        {"name": "Write", "kind": "tool", "tool": "write_file",
         "args": {"path": "summary.txt", "content": "the week in one page"}},
        {"name": "Tell", "kind": "notify", "message": "The summary is ready."},
        {"name": "Check", "kind": "ask", "message": "Send it to the team?"},
    ])
    wf = WorkflowStore(p.engine).load_def("Monday summary")
    engine = WorkflowEngine(p)
    run = await engine.run_record(engine.create_record(wf), wf)
    assert target.exists()
    assert any("The summary is ready." in str(a) for a in sent)
    assert run.status == "waiting"


async def test_a_held_steps_expectation_is_not_judged(daemon):
    app, p, events, sent, folder = daemon
    _save(p, folder, "Expecting", [
        {"name": "Write", "kind": "tool", "tool": "write_file",
         "args": {"path": "out.txt", "content": "x"}, "expect": {"files": ["out.txt"]}},
    ])
    view = await _rehearse(app, "workflow", "Expecting")
    assert view["status"] == "ready", view
    assert view["sentence"] == "This would write 1 file. Nothing was changed."


async def test_a_step_the_real_run_refuses_is_refused_in_the_report_too(daemon, monkeypatch):
    """A tool set to ask is refused by a stored workflow (no one to ask): the
    rehearsal records that refusal through the registry and stops there."""
    app, p, events, sent, folder = daemon
    monkeypatch.setitem(p.permissions._base, "write_file", "ask")
    _save(p, folder, "Asks first", [
        {"name": "Write", "kind": "tool", "tool": "write_file",
         "args": {"path": "out.txt", "content": "x"}},
        {"name": "Tell", "kind": "notify", "message": "Done."},
    ])
    view = await _rehearse(app, "workflow", "Asks first")
    assert view["status"] == "failed", view
    assert view["error"].startswith("The step “Write” failed:"), view["error"]
    assert [(r["words"], r["would"]) for r in view["refused"]] == [("write out.txt", "refuse")]
    assert view["would"] == [], "the step after the refusal ran"
    assert sent == [] and not (folder / "out.txt").exists()


async def test_blocks_are_rehearsed_by_the_same_engine_path(daemon):
    """09-a's If and For each: the folder is listed for real (a read), each
    item's write is held, the If decides with no model and its lane's write
    is held."""
    app, p, events, sent, folder = daemon
    inbox = folder / "inbox"
    inbox.mkdir()
    for n in ("a.txt", "b.txt", "c.txt"):
        (inbox / n).write_text(n, encoding="utf-8")
    _save(p, folder, "Each and if", [
        {"name": "Each file", "kind": "for_each", "over": {"files": {"folder": str(inbox), "glob": "*.txt"}},
         "steps": [
             {"name": "Copy", "kind": "tool", "tool": "write_file",
              "args": {"path": "copy-{{item.name}}", "content": "copy"}},
         ]},
        {"name": "Enough", "kind": "if",
         "condition": {"left": "{{Each file}}", "op": "contains", "right": "done"},
         "steps": [{"name": "Mark", "kind": "tool", "tool": "write_file",
                    "args": {"path": "done.txt", "content": "done"}}],
         "else_steps": [{"name": "Warn", "kind": "notify", "message": "Fewer than three."}]},
    ])
    before = _snapshot(folder)
    view = await _rehearse(app, "workflow", "Each and if")
    assert view["status"] == "ready", view
    words = [w["words"] for w in view["would"]]
    assert words == [
        "write copy-a.txt", "write copy-b.txt", "write copy-c.txt", "write done.txt",
    ], words
    assert view["reads"] == 1, "the folder was listed for real"
    assert _snapshot(folder) == before and sorted(f.name for f in inbox.iterdir()) == ["a.txt", "b.txt", "c.txt"]
    assert sent == []


async def test_an_ask_inside_a_for_each_lists_the_items_it_did_not_reach(daemon):
    app, p, events, sent, folder = daemon
    _save(p, folder, "Ask each", [
        {"name": "Each", "kind": "for_each", "over": {"from": '["one", "two", "three"]'},
         "steps": [
             {"name": "Confirm", "kind": "ask", "message": "Keep {{item}}?"},
             {"name": "Note", "kind": "tool", "tool": "write_file",
              "args": {"path": "kept.txt", "content": "{{item}}"}},
         ]},
        {"name": "Tell", "kind": "notify", "message": "All done."},
    ])
    view = await _rehearse(app, "workflow", "Ask each")
    assert view["status"] == "ready", view
    assert view["stopped_at"]["question"] == "Keep one?"
    assert view["stopped_at"]["not_rehearsed"] == ["“Note”", "2 more items of “Each”", "“Tell”"]
    assert sent == [] and not (folder / "kept.txt").exists()


async def test_an_agent_step_runs_as_a_rehearsal_on_the_workflows_own_model(daemon):
    app, p, events, sent, folder = daemon
    calls: list[dict] = []

    async def scripted(**kw):
        calls.append({"provider": kw.get("provider"), "model": kw.get("model")})
        if len(calls) == 1:
            resp = LLMResponse(text="", tool_calls=[ToolCall(id="w", name="write_file", arguments={
                "path": "draft.txt", "content": "draft"})])
        else:
            resp = LLMResponse(text="Wrote the draft.")
            yield {"type": "text", "text": "Wrote the draft."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    p.router.stream = scripted
    _save(p, folder, "Drafting", [
        {"name": "Draft", "kind": "agent", "agent": "builder", "task": "Write a draft."},
    ])
    view = await _rehearse(app, "workflow", "Drafting")
    assert view["status"] == "ready", view
    assert [w["words"] for w in view["would"] if "draft.txt" in w["words"]] == ["write draft.txt"]
    assert not (folder / "draft.txt").exists()
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(SessionRow)))
    assert len(rows) == 1
    assert json.loads(rows[0].options_json or "{}").get("rehearsal_id") == view["id"]
    # The workflow names no model: its rehearsal names none either (the
    # router picks what a real run's step would get).
    assert calls and all(c["provider"] in (None, "", "mock") for c in calls), calls


async def test_keep_it_stamps_the_workflow(daemon):
    app, p, events, sent, folder = daemon
    rec = _save(p, folder, "Keepable", [{"name": "Tell", "kind": "notify", "message": "Hello."}])
    view = await _rehearse(app, "workflow", "Keepable")
    assert view["status"] == "ready"
    async with await _client(app) as c:
        kept = await c.post(f"/rehearsals/{view['id']}/keep")
    assert kept.status_code == 200, kept.text
    with session_scope(p.engine) as db:
        assert db.get(WorkflowRecord, rec.id).rehearsed_at is not None


async def test_stop_ends_its_run_stopped(daemon):
    app, p, events, sent, folder = daemon
    gate = asyncio.Event()
    real_invoke = p.registry.invoke

    async def slow(name, *a, **kw):
        if name == "read_file":
            await gate.wait()
        return await real_invoke(name, *a, **kw)

    p.registry.invoke = slow  # type: ignore[method-assign]
    _save(p, folder, "Slow", [
        {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "notes.txt"}},
        {"name": "Tell", "kind": "notify", "message": "Read it."},
    ])
    async with await _client(app) as c:
        r = await c.post("/rehearsals", json={"kind": "workflow", "id": "Slow"})
        rid = r.json()["id"]
        driver = RR._DRIVERS[rid]
        stopped = (await c.post(f"/rehearsals/{rid}/stop")).json()
    with pytest.raises(asyncio.CancelledError):
        await driver
    assert stopped["status"] == "stopped" and stopped["sentence"] == "Stopped. Nothing was changed."
    with session_scope(p.engine) as db:
        run = db.exec(select(WorkflowRunRecord)).one()
    assert run.status == "cancelled"
    assert sent == []


async def test_chat_rehearses_a_workflow_by_name(daemon):
    from iron_jarvis.tools.base import ToolContext

    app, p, events, sent, folder = daemon
    _save(p, folder, "Say hello", [{"name": "Tell", "kind": "notify", "message": "Hello."}])
    tool = p.registry.get("rehearse")
    ctx = ToolContext(workspace=folder, session_id="chat", agent_run_id="chat", event_bus=p.event_bus,
                      engine=p.engine, config=p.config)
    res = await tool.execute({"kind": "workflow", "name": "say hello"}, ctx)
    assert res.ok, res.error
    rid = res.data["rehearsal"]["id"]
    driver = RR._DRIVERS.get(rid)
    if driver is not None:
        await driver
    view = await asyncio.to_thread(RR.report, p, rid)
    assert view["status"] == "ready" and view["sentence"] == "This would send 1 message. Nothing was changed."
    assert res.output.startswith("Rehearsing the workflow “Say hello”"), res.output


async def test_an_unknown_workflow_is_refused_with_the_names_there_are(daemon):
    app, p, events, sent, folder = daemon
    _save(p, folder, "Say hello", [{"name": "Tell", "kind": "notify", "message": "Hello."}])
    async with await _client(app) as c:
        r = await c.post("/rehearsals", json={"kind": "workflow", "id": "Nope"})
    assert r.status_code == 404
    assert r.json()["detail"] == "There is no workflow called “Nope”. Your workflows: “Say hello”."


async def test_an_ask_inside_an_if_lists_the_rest_of_its_lane_then_the_workflows_steps(daemon):
    app, p, events, sent, folder = daemon
    _save(p, folder, "Ask in if", [
        {"name": "Check", "kind": "if", "condition": {"left": "yes", "op": "is", "right": "yes"},
         "steps": [
             {"name": "Confirm", "kind": "ask", "message": "Go ahead?"},
             {"name": "Write", "kind": "tool", "tool": "write_file", "args": {"path": "go.txt", "content": "go"}},
         ],
         "else_steps": [{"name": "Skip", "kind": "notify", "message": "Skipped."}]},
        {"name": "Tell", "kind": "notify", "message": "Finished."},
    ])
    view = await _rehearse(app, "workflow", "Ask in if")
    assert view["status"] == "ready", view
    assert view["stopped_at"] == {"question": "Go ahead?", "not_rehearsed": ["“Write”", "“Tell”"]}
    assert sent == [] and not (folder / "go.txt").exists()


async def test_a_rehearsal_writes_no_record_of_checks(daemon, monkeypatch):
    """A step that only reads still has its expectation judged (the read
    ran), but the rehearsal writes no row to the record of checks."""
    from iron_jarvis.checks import record as checks_record

    app, p, events, sent, folder = daemon
    noted: list = []

    async def spy(*a, **k):
        noted.append(a)

    monkeypatch.setattr(checks_record, "note", spy)
    _save(p, folder, "Read and check", [
        {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "notes.txt"},
         "expect": {"summary_contains": ["keep me"]}},
    ])
    view = await _rehearse(app, "workflow", "Read and check")
    assert view["status"] == "ready", view
    assert noted == [], "a rehearsal wrote to the record of checks"


async def test_an_unpinned_workflows_scratch_folder_is_removed(tmp_path, monkeypatch):
    """No project folder: the tool steps get a scratch folder, which goes
    when the rehearsal ends (nothing was written in it)."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.router.stream = _no_model()
    WorkflowStore(p.engine).save("Loose", [
        {"name": "Write", "kind": "tool", "tool": "write_file", "args": {"path": "out.txt", "content": "x"}},
    ])
    view = await _rehearse(app, "workflow", "Loose")
    assert view["status"] == "ready", view
    assert view["sentence"] == "This would write 1 file. Nothing was changed."
    assert list(scratch.glob("ijwf-*")) == [], "its scratch folder was left behind"


async def test_no_outgoing_webhook_hears_a_workflow_rehearsal_and_the_report_lists_it(daemon):
    """A webhook that tells another app about a step's call or a workflow's
    end hears nothing of a rehearsal (its tool events carry the RUN's id,
    which is no session: the run record says it is a rehearsal), and the
    report lists the one that would hear the workflow finish."""
    app, p, events, sent, folder = daemon
    hooks = p.outbound_webhooks
    hooks.allow_internal = True
    posted: list = []
    hooks.http_post = lambda url, payload, headers: posted.append(payload.get("type")) or {"status": 200}
    hooks.register("zap", "http://127.0.0.1:9/hook", ["tool.executed", "tool.rehearsed", "workflow.completed"])
    _save(p, folder, "Read and write", [
        {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "notes.txt"}},
        {"name": "Write", "kind": "tool", "tool": "write_file", "args": {"path": "copy.txt", "content": "x"}},
    ])
    view = await _rehearse(app, "workflow", "Read and write")
    assert view["status"] == "ready", view
    # (``publish`` awaits its handlers, the webhook's delivery among them.)
    assert posted == [], f"an outgoing webhook heard a rehearsal: {posted}"
    assert "tell another app through the webhook “zap” how the workflow's run went" in [
        w["words"] for w in view["would"]
    ], view["would"]
