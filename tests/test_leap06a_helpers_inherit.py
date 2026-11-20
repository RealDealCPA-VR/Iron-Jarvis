"""Leap 06-a: a helper agent's write is held too.

A rehearsal rides on its run's options. A helper agent's run is created
while the parent's ``delegate`` call runs inside the rehearsal
(``tools/rehearsal.current``), so ``Orchestrator.create_session`` stamps the
same rehearsal on the helper, and the helper's write is held by the same
gate. Driven through the REAL daemon: a rule's supervisor delegates to a
builder that writes a file. Only the model is a double.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, ToolInvocation
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools import rehearsal as R
from iron_jarvis.tools import rehearsal_runs as RR

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


def _scripted(steps):
    state = {"n": 0}

    async def fake_stream(*, system=None, messages=None, tools=None, **kw):
        i = state["n"]
        state["n"] += 1
        if i < len(steps):
            resp = LLMResponse(text="", tool_calls=list(steps[i]))
        else:
            resp = LLMResponse(text="Done.")
            yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _options(engine, sid: str) -> dict:
    with session_scope(engine) as db:
        return json.loads(db.get(SessionRow, sid).options_json or "{}")


async def test_a_run_made_inside_a_rehearsal_inherits_it(tmp_path):
    app = create_app(str(tmp_path / "home"))
    orch: Orchestrator = app.state.orchestrator
    with R.scope("reh_parent"):
        child = await orch.create_session("write the note", AgentType.BUILDER)
    plain = await orch.create_session("write the note", AgentType.BUILDER)
    assert _options(app.state.platform.engine, child.id).get("rehearsal_id") == "reh_parent"
    assert "rehearsal_id" not in _options(app.state.platform.engine, plain.id), "the scope leaked"
    # A door's own rehearsal is kept.
    with R.scope("reh_other"):
        own = await orch.create_session("x", AgentType.BUILDER, options={"rehearsal_id": "reh_door"})
    assert _options(app.state.platform.engine, own.id)["rehearsal_id"] == "reh_door"


async def test_a_helper_agents_write_is_held(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.reflex.add(name="Note it", source="webhook", match="note-it", action="session",
                 target="", task_template="Write a note about {text}", enabled=True)
    p.router.stream = _scripted([
        # the rule's supervisor hands the writing to a builder…
        [ToolCall(id="d1", name="delegate",
                  arguments={"agent_type": "builder", "task": "Write note.txt saying hello."})],
        # …which writes.
        [ToolCall(id="w1", name="write_file", arguments={"path": "note.txt", "content": "hello"})],
    ])
    started = await RR.start(p, "rule", "Note it")
    deadline = time.monotonic() + 60
    view = started
    while view["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        view = await asyncio.to_thread(RR.report, p, started["id"])
    assert view["status"] == "ready", view

    sessions = RR.sessions_of(p, RR.RehearsalStore(p.engine).get(started["id"]))
    assert len(sessions) == 2, "the helper's run is not part of the rehearsal"
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == "write_file")))
        workspaces = [db.get(SessionRow, s).workspace_path for s in sessions]
    assert [(r.rehearsed, r.would, r.ok) for r in rows] == [(True, "run", True)]
    assert rows[0].session_id != sessions[0], "the write was not the helper's"
    for ws in workspaces:
        assert ws and not (Path(ws) / "note.txt").exists()
    assert [w["words"] for w in view["would"] if w["kind"] == "write"] == ["write note.txt"]
