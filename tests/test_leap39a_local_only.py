"""Idea 39, phase 39-a: a schedule made in a Local only chat fires a Local only run.

The chat's Local only switch is read at FIRE time (``egress.thread_local_only``
on the chat the schedule reports to), so the run gets ``local_only: True``
(read by the orchestrator, which keeps it on this PC or your network), along
with the chat (its asks show there) and the schedule as its report source. A
goal made in such a chat does the same for each try. A chat that is not Local
only adds no ``local_only``. The bell's report notice carries titles only
(``test_leap39a_report_writer``).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling.tools import ScheduleCreateTool
from iron_jarvis.tools.base import ToolContext

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]


@pytest.fixture()
def desk(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    yield c, p
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _chat(p, local_only: bool) -> str:
    rec = ChatThreadRecord(title="Prices", messages_json="[]",
                           setup_json=json.dumps({"local_only": True}) if local_only else "")
    with session_scope(p.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _spy(monkeypatch, orch) -> list[dict]:
    seen: list[dict] = []
    real = orch.create_session

    async def spy(task, *a, **kw):
        seen.append(dict(kw.get("options") or {}))
        return await real(task, *a, **kw)

    monkeypatch.setattr(orch, "create_session", spy)
    return seen


@pytest.mark.parametrize("local_only", [True, False])
def test_a_schedule_made_in_a_local_only_chat_fires_a_local_only_run(desk, tmp_path, monkeypatch, local_only):
    c, p = desk
    chat = _chat(p, local_only)
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                      event_bus=p.event_bus, engine=p.engine, thread_id=chat)
    r = asyncio.run(ScheduleCreateTool(p).execute(
        {"name": "Prices", "cron": "0 9 * * 5", "task": "check the prices"}, ctx))
    assert r.ok, r.error
    seen = _spy(monkeypatch, p.orchestrator)
    c.post("/schedules/Prices/run")
    assert seen, "the fire made no run"
    options = seen[0]
    assert options.get("chat_thread_id") == chat
    assert options.get("report_from", {}).get("kind") == "schedule"
    assert (options.get("local_only") is True) is local_only


def test_a_goal_made_in_a_local_only_chat_tries_local_only(desk, monkeypatch):
    c, p = desk
    chat = _chat(p, True)
    goal = c.post("/goals", json={"name": "G", "contract_text": "do it", "budget": {"max_dollars": 1},
                                  "chat_thread_id": chat}).json()["goal"]
    seen = _spy(monkeypatch, p.goal_engine.orch)
    c.post(f"/goals/{goal['id']}/run")
    assert seen and seen[0].get("local_only") is True
    assert seen[0]["report_from"] == {"kind": "goal", "name": "G", "id": goal["id"],
                                      "href": "/autonomy", "by": "source"}
