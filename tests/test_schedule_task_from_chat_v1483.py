"""Chat can make a plain TASK schedule (v1.483.0).

The Schedules page could always make a schedule whose work is a task in the
user's own words (kind ``task``: an agent task at every fire). Chat's
``schedule_create`` offered only ``workflow`` (a whole workflow definition) or
``event``, so "every Monday at 8, pull together the flats with rent due this
week" became a list of questions instead of a schedule (seen in the live
measurements of the capability lookup). Now ``task`` makes one directly.

What must hold:

* ``task`` alone makes a task schedule whose payload holds that text, and its
  fire runs the text as an agent task, like a schedule made on the page;
* the user's own knobs still go through the same checks, and a pre-run
  SCRIPT is still refused from chat (it would run on the host);
* a workflow or event schedule is made exactly as before;
* nonsense (a task with another kind, a task schedule with no task, a task
  that is not text) is refused in words, and nothing is saved.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import knobs
from iron_jarvis.scheduling.tools import ScheduleCreateTool
from iron_jarvis.tools.base import ToolContext

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

RENT = "pull together the list of flats with rent due this week"


@pytest.fixture()
def desk(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    yield c, p, ScheduleCreateTool(p), ctx
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _run(tool, ctx, args):
    return asyncio.run(tool.execute(args, ctx))


def test_a_task_makes_a_task_schedule_with_the_users_words(desk):
    _c, p, tool, ctx = desk
    r = _run(tool, ctx, {"name": "rent-monday", "cron": "0 8 * * 1", "task": RENT})
    assert r.ok, r.error
    rec = p.scheduler.get("rent-monday")
    assert rec is not None and rec.kind == "task"
    assert rec.decoded_payload() == {"task": RENT}
    assert r.data["kind"] == "task" and r.data["next_run"]


def test_its_fire_runs_the_words_as_an_agent_task(desk, monkeypatch):
    """The fire is the page's own task-schedule fire: one agent session whose
    task is the user's words."""
    c, p, tool, ctx = desk
    assert _run(tool, ctx, {"name": "rent-monday", "cron": "0 8 * * 1", "task": RENT}).ok
    seen: dict = {}
    orch = p.orchestrator
    real = orch.create_session

    def spy(task, *a, **kw):
        seen["task"] = task
        seen["origin"] = kw.get("origin")
        return real(task, *a, **kw)

    monkeypatch.setattr(orch, "create_session", spy)
    c.post("/schedules/rent-monday/run")
    assert RENT in str(seen.get("task") or ""), seen
    assert str(seen.get("origin") or "").startswith("schedule:"), seen


def test_knobs_ride_along_and_a_script_is_still_refused(desk):
    _c, p, tool, ctx = desk
    r = _run(tool, ctx, {"name": "with-knob", "cron": "0 8 * * 1", "task": RENT,
                         "payload": {"skip_memory": True}})
    assert r.ok, r.error
    assert p.scheduler.get("with-knob").decoded_payload() == {"skip_memory": True, "task": RENT}
    # A pre-run script would run on the host: never from chat, task kind too.
    r = _run(tool, ctx, {"name": "with-script", "cron": "0 8 * * 1", "task": RENT,
                         "payload": {"script": {"command": "echo hi"}}})
    assert r.ok is False and r.error == knobs.SCRIPT_FROM_AGENT_REFUSAL
    assert p.scheduler.get("with-script") is None
    # A knob the fire could not honour is refused in the page's words.
    r = _run(tool, ctx, {"name": "bad-skill", "cron": "0 8 * * 1", "task": RENT,
                         "payload": {"skills": ["no-such-skill"]}})
    assert r.ok is False and "unknown skill" in r.error
    assert p.scheduler.get("bad-skill") is None


def test_workflow_and_event_schedules_are_made_as_before(desk):
    _c, p, tool, ctx = desk
    r = _run(tool, ctx, {"name": "ping", "cron": "0 9 * * *", "kind": "event", "payload": {"type": "ping"}})
    assert r.ok and p.scheduler.get("ping").kind == "event"
    flow = {"name": "flow", "steps": [{"id": "a", "kind": "agent", "task": "hello"}]}
    r = _run(tool, ctx, {"name": "flow", "cron": "0 9 * * *", "payload": flow})
    assert r.ok, r.error
    rec = p.scheduler.get("flow")
    assert rec.kind == "workflow" and rec.decoded_payload() == flow


@pytest.mark.parametrize("args, words", [
    ({"task": RENT, "kind": "event", "payload": {"type": "ping"}}, "makes a task schedule"),
    ({"kind": "task"}, "needs `task`"),
    ({"kind": "task", "task": "   "}, "needs `task`"),
    ({"task": 42}, "as text"),
    ({"task": RENT, "payload": ["not", "an", "object"]}, "object of schedule knobs"),
])
def test_nonsense_is_refused_in_words_and_nothing_is_saved(desk, args, words):
    _c, p, tool, ctx = desk
    r = _run(tool, ctx, {"name": "nope", "cron": "0 8 * * 1", **args})
    assert r.ok is False and words in r.error, r.error
    assert p.scheduler.get("nope") is None


def test_the_tool_tells_the_model_how():
    desc = ScheduleCreateTool.description
    assert "`task`" in desc and "own words" in desc
    props = ScheduleCreateTool.input_schema["properties"]
    assert props["task"] == {"type": "string"}
    assert props["kind"]["enum"] == ["task", "workflow", "event"]
    assert "job" not in desc.lower().split()
