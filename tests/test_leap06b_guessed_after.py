"""Leap 06-b: the honest limit of a rehearsal — a read after a held change.

A rehearsal holds every change, so a step that READS after one reads the
world without it. The report then sets ``guessed_after`` and says: "Steps
after the first change were worked out without it, so the real run may
differ." Counted by code from the ledger's rows in the order they were
written: a held change (never a question, never what happens only after the
run: its delivery, a webhook, a lesson) before a read that ran.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools import rehearsal as R
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.workflows.store import WorkflowStore

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]

WORDS = "Steps after the first change were worked out without it, so the real run may differ."


def test_the_rule_in_order():
    assert R.GUESSED_AFTER_WORDS == WORDS
    assert R.guessed_after([("write", True), ("read", False)]) is True
    assert R.guessed_after([("read", False), ("write", True)]) is False
    assert R.guessed_after([("ask", True), ("read", False)]) is False
    for door in ("schedule_delivery", "outbound_webhook", "post_run_learning", "chat_delivery"):
        assert R.guessed_after([(door, True), ("read", False)]) is False, door
    assert R.guessed_after([("send", True), ("read", False)]) is True
    assert R.guessed_after([]) is False


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.notifier.notify = lambda *a, **k: {"mock": {"ok": True}}  # type: ignore[method-assign]
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "notes.txt").write_text("the notes", encoding="utf-8")
    with session_scope(p.engine) as db:
        proj = Project(name="Acme", root=str(folder))
        db.add(proj)
        db.commit()
        pid = proj.id
    return p, folder, pid


async def _settled(p, rid: str) -> dict[str, Any]:
    driver = RR._DRIVERS.get(rid)
    if driver is not None:
        await driver
    return await asyncio.to_thread(RR.report, p, rid)


async def test_a_workflow_that_writes_then_reads_its_own_file_says_so(daemon):
    p, folder, pid = daemon
    WorkflowStore(p.engine).save("Write and check", [
        {"name": "Write", "kind": "tool", "tool": "write_file", "args": {"path": "total.txt", "content": "42"}},
        {"name": "Read it back", "kind": "tool", "tool": "read_file", "args": {"path": "total.txt"}},
    ], project_id=pid)
    started = await RR.start(p, "workflow", "Write and check")
    view = await _settled(p, started["id"])
    # The read found no file (the write was held): the rehearsal ENDS there
    # (06-b review), names it, and says why it may differ from the real run.
    assert view["status"] == "ready", view
    assert view["stopped_at"]["where"] == "would read what “Write” would have written"
    assert view["guessed_after"] is True
    assert view["guessed_words"] == WORDS
    assert [w["words"] for w in view["would"]] == ["write total.txt"]
    assert not (folder / "total.txt").exists()


async def test_a_workflow_that_reads_first_is_not_a_guess(daemon):
    p, folder, pid = daemon
    WorkflowStore(p.engine).save("Read then write", [
        {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "notes.txt"}},
        {"name": "Write", "kind": "tool", "tool": "write_file", "args": {"path": "copy.txt", "content": "{{Read}}"}},
    ], project_id=pid)
    started = await RR.start(p, "workflow", "Read then write")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert view["reads"] == 1
    assert view["guessed_after"] is False and view["guessed_words"] == ""


def _scripted(rounds: list[list[ToolCall]]):
    state = {"n": 0}

    async def fake_stream(**kw):
        i = state["n"]
        state["n"] += 1
        if i < len(rounds):
            resp = LLMResponse(text="", tool_calls=list(rounds[i]))
        else:
            resp = LLMResponse(text="Done.")
            yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


async def test_a_schedules_run_that_reads_after_a_held_write_says_so(daemon):
    """The same rule for a schedule's run (06-a's rehearsals report it too)."""
    p, folder, pid = daemon
    p.scheduler.add_task(
        "Tally", "0 8 * * *", kind="task",
        payload={"task": "Write the total, then check it.", "workspace_root": str(folder)},
    )
    p.router.stream = _scripted([
        [ToolCall(id="w", name="write_file", arguments={"path": "total.txt", "content": "42"})],
        [ToolCall(id="r", name="read_file", arguments={"path": "notes.txt"})],
    ])
    started = await RR.start(p, "schedule", "Tally")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert view["guessed_after"] is True and view["guessed_words"] == WORDS


async def test_a_schedules_delivery_written_before_the_run_is_not_a_change_before_its_reads(daemon):
    p, folder, pid = daemon
    p.scheduler.add_task(
        "Look", "0 8 * * *", kind="task",
        payload={"task": "Read the notes.", "workspace_root": str(folder)},
    )
    p.router.stream = _scripted([[ToolCall(id="r", name="read_file", arguments={"path": "notes.txt"})]])
    started = await RR.start(p, "schedule", "Look")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert any(w["words"].startswith("send the result to") for w in view["would"]), view["would"]
    assert view["reads"] == 1
    assert view["guessed_after"] is False


async def test_a_refused_change_is_not_a_change(daemon, monkeypatch):
    """A step the real run would refuse changes nothing either way."""
    p, folder, pid = daemon
    monkeypatch.setitem(p.permissions._base, "write_file", "deny")
    p.scheduler.add_task(
        "Refused", "0 8 * * *", kind="task",
        payload={"task": "Write, then read.", "workspace_root": str(folder), "notify": False},
    )
    p.router.stream = _scripted([
        [ToolCall(id="w", name="write_file", arguments={"path": "total.txt", "content": "42"})],
        [ToolCall(id="r", name="read_file", arguments={"path": "notes.txt"})],
    ])
    started = await RR.start(p, "schedule", "Refused")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    with session_scope(p.engine) as db:
        from iron_jarvis.core.models import ToolInvocation

        refused = list(db.exec(select(ToolInvocation).where(ToolInvocation.would == "refuse")))
    assert refused, "control: the write was refused"
    assert view["guessed_after"] is False
