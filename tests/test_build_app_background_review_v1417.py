"""v1.417.0 review of "helpers at work are said": the parts the first cut left.

* Claude Code's own background tasks that are AGENTS are not only
  ``local_agent``: its task table calls ``local_workflow`` an agent too, and a
  ``remote_agent`` / ``in_process_teammate`` (an agent team's member) is a
  helper as well (task types read from the 2.1.316 binary). Counted as
  "background commands" they were said as commands, read again rarely, and
  never listed in Running now. A shell command and a monitor stay commands.
* The count is read from a worker thread (``GET /chat/turns/running`` is a
  sync route) while the loop changes the task table: it never raises.
* A process that exits clears a reply of its own (no row lingers).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import news as NEWS
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted.claude import ClaudeDriver
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, use_fake


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _task(driver, sub: str, tid: str, **extra) -> None:
    driver.on_message({"type": "system", "subtype": sub, "task_id": tid, **extra})


def _driver() -> ClaudeDriver:
    async def write(obj):  # pragma: no cover - nothing is written here
        return None

    return ClaudeDriver(write=write, emit=lambda ev: None, ask=lambda a: None, on_session_id=lambda s: None,
                        mode="ask", model="", effort="", cwd=".")


@pytest.mark.parametrize("kind", ["local_agent", "local_workflow", "remote_agent", "in_process_teammate"])
def test_every_agent_kind_of_background_task_is_a_helper(kind):
    drv = _driver()
    _task(drv, "task_started", "t1", task_type=kind)
    assert (drv.background_tasks, drv.background_helpers) == (1, 1), kind


@pytest.mark.parametrize("kind", ["local_bash", "monitor_mcp", "monitor_ws", ""])
def test_commands_and_monitors_are_not_helpers(kind):
    drv = _driver()
    _task(drv, "task_started", "t1", task_type=kind)
    assert (drv.background_tasks, drv.background_helpers) == (1, 0), kind


def test_the_helper_count_never_raises_while_the_table_changes():
    drv = _driver()
    for i in range(200):
        _task(drv, "task_started", f"t{i}", task_type="local_agent")

    class Moving(dict):
        def values(self):  # the loop adds a task while the count walks the table
            vals = super().values()
            for i, v in enumerate(vals):
                if i == 3:
                    self["late"] = "local_agent"
                yield v

    drv._tasks = Moving(drv._tasks)
    assert drv.background_helpers >= 200


def test_the_exit_ends_a_reply_of_its_own():
    drv = _driver()
    _task(drv, "task_started", "a1", task_type="local_agent")
    _task(drv, "task_notification", "a1", status="completed")
    assert drv.replying_alone is True
    drv.on_exit()
    assert drv.replying_alone is False and drv.background_helpers == 0


async def test_a_workflow_at_work_is_listed_in_running_now(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "go", "turn_id": "t1"})
        assert frames(r.text)[-1]["type"] == "done"
        _task(svc.live[s["id"]].driver, "task_started", "w1", task_type="local_workflow")
        v = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert (v["background"], v["background_helpers"]) == (1, 1)
        rows = NEWS.running_rows(app.state.platform)
        assert len(rows) == 1 and rows[0]["helpers"] == 1
    await stop_all(app)


async def test_helpers_never_heard_from_stop_counting_as_work(app, work, tmp_path, monkeypatch):
    # A helper whose end never comes (its notice lost) must not keep the PC
    # awake, Running now listing it and an update waiting, forever: past
    # BACKGROUND_SILENT_MAX_S of silence from the tool it is not work any more.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    platform = app.state.platform
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "go", "turn_id": "t1"})
        assert frames(r.text)[-1]["type"] == "done"
        live = svc.live[s["id"]]
        _task(live.driver, "task_started", "a1", task_type="local_agent")
        now = svc.clock()
        assert live.helpers_working(now) is True
        assert NEWS.counts(platform)["working"] == 1
        live.last_heard = now - S.BACKGROUND_SILENT_MAX_S - 1
        assert live.helpers_working(svc.clock()) is False
        assert NEWS.running_rows(platform) == []
        assert NEWS.counts(platform) == {"running": 0, "working": 0, "tools": []}
    await stop_all(app)
