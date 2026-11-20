"""Leap 06-a: the side doors — effects that never pass ``registry.invoke``.

``tools/rehearsal.SIDE_DOORS`` lists every one a rehearsal of a rule or a
schedule can reach: a schedule's pre-run script and its delivery line, the
chat delivery, the post-run lessons, a remote agent's hand-off. Each one, in
a rehearsal, records ONE rehearsed row and does nothing. This file scans the
code each names for its guard, then drives each through the real daemon.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from sqlmodel import select

import iron_jarvis
from iron_jarvis.agents import chat_delivery as _chat_delivery
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.tools import rehearsal as R
from iron_jarvis.tools import rehearsal_runs as RR

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]

SRC = Path(iron_jarvis.__file__).parent


def _quiet_stream():
    async def fake_stream(**kw):
        yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.router.stream = _quiet_stream()
    sent: list = []
    p.notifier.notify = lambda *a, **k: sent.append(a)  # type: ignore[method-assign]
    return app, p, sent


def _rows(p, session_id: str, tool: str) -> list[ToolInvocation]:
    with session_scope(p.engine) as db:
        out = list(db.exec(select(ToolInvocation).where(
            ToolInvocation.session_id == session_id, ToolInvocation.tool == tool,
        )))
        for r in out:
            db.expunge(r)
        return out


async def _settled(p, rid: str) -> dict:
    deadline = time.monotonic() + 60
    view = await asyncio.to_thread(RR.report, p, rid)
    while view["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        view = await asyncio.to_thread(RR.report, p, rid)
    return view


def test_every_side_door_is_guarded_where_the_table_says():
    for door, spec in R.SIDE_DOORS.items():
        text = (SRC / spec.where).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert f'door="{door}"' in text, f"{door}: no rehearsal guard in {spec.where}"


async def test_the_pre_run_script_and_the_delivery_line_do_nothing(daemon, tmp_path):
    app, p, sent = daemon
    folder = tmp_path / "work"
    folder.mkdir()
    p.scheduler.add_task(
        "Weekly export", "0 6 * * 1", kind="task",
        payload={"task": "Export the week.", "workspace_root": str(folder),
                 "script": {"command": "echo ran > marker.txt", "timeout_s": 30, "cwd": "workspace"},
                 "notify_channels": ["slack"]},
    )
    started = await RR.start(p, "schedule", "Weekly export")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert not (folder / "marker.txt").exists(), "the pre-run script ran in a rehearsal"
    words = [w["words"] for w in view["would"]]
    assert "run the schedule's pre-run script `echo ran > marker.txt`" in words
    assert "send the result to Slack" in words
    assert view["sentence"] == "This would send 1 message and run 1 command. Nothing was changed."
    await asyncio.sleep(0)
    assert sent == [], "a rehearsal delivered the schedule's line"


async def test_the_chat_delivery_and_the_lessons_do_nothing(daemon, monkeypatch):
    app, p, sent = daemon
    orch = app.state.orchestrator
    delivered: list = []
    monkeypatch.setattr(_chat_delivery, "deliver_session_line", lambda *a, **k: delivered.append(a) or {"ok": True})
    reflected: list = []
    monkeypatch.setattr(p.learning, "reflect", lambda *a, **k: reflected.append(a))
    session = await orch.create_session(
        "write the report", AgentType.BUILDER, chat_thread_id="thread_06a",
        options={"rehearsal_id": "reh_side"},
    )
    out = orch._deliver_to_chat_sync(session)
    assert out["why"] == "rehearsal" and delivered == []
    assert [r.would for r in _rows(p, session.id, "chat_delivery")] == ["run"]
    orch._post_run_learning(session)
    assert reflected == [], "a rehearsal kept a lesson"
    assert [r.rehearsed for r in _rows(p, session.id, "post_run_learning")] == [True]
    # Control: a real run's chat line is delivered.
    real = await orch.create_session("write the report", AgentType.BUILDER, chat_thread_id="thread_06a")
    orch._deliver_to_chat_sync(real)
    assert len(delivered) == 1


async def test_a_remote_agents_hand_off_sends_nothing(daemon, monkeypatch):
    app, p, sent = daemon
    from iron_jarvis.agents import remote as _remote

    calls: list = []
    monkeypatch.setattr(_remote.RemoteAgentRegistry, "run", lambda *a, **k: calls.append(a))
    p.reflex.add(name="To hermes", source="webhook", match="hermes", action="remote_agent",
                 target="hermes", task_template="Handle {text}", enabled=True)
    view = await RR.start(p, "rule", "To hermes")
    assert view["status"] == "ready"
    assert calls == []
    assert [w["words"] for w in view["would"]] == ["hand the task to the remote agent hermes"]
    rows = _rows(p, view["id"], "remote_agent")
    assert [(r.rehearsed, json.loads(r.args_json)["target"]) for r in rows] == [(True, "hermes")]
