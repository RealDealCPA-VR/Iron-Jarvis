"""v1.417.0 (a USER BUG): a Code-view Claude Code session that started helper
agents in the BACKGROUND was stopped by the 15-minute idle reaper, which only
looked at whether a reply was running — so every helper died with it. On the
user's PC seven builder teams died together twice in one day, each time about
15 minutes after the reply that said "I'll report as teams finish".

Claude Code says when a background task opens (``system`` ``task_started``)
and when it ends (``task_notification``); while any is open the process is
neither reaped nor evicted, a new message is still welcome, and the session
row says how many run. Bounded: two silent hours and it is idle again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, use_fake


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def _say(c, sid, turn_id="t1", text="ready?"):
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})
    return frames(r.text)


def _task(driver, sub: str, tid: str, **extra) -> None:
    driver.on_message({"type": "system", "subtype": sub, "task_id": tid, **extra})


async def test_open_background_tasks_keep_the_process_and_their_end_frees_it(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        live = svc.live[s["id"]]
        _task(live.driver, "task_started", "a1", task_type="local_agent", description="builder")
        _task(live.driver, "task_started", "b1", task_type="local_bash")
        assert live.background_tasks == 2
        late = svc.clock() + S.IDLE_S + 1
        assert await svc.reap_once(now=late) == [], "a session with helpers still working was reaped"
        assert live.proc is not None
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        assert row.get("background", (row.get("session") or {}).get("background")) == 2
        _task(live.driver, "task_notification", "a1", status="completed")
        assert await svc.reap_once(now=late) == []
        _task(live.driver, "task_notification", "b1", status="stopped")
        assert live.background_tasks == 0
        # The tool's own frames count as activity: idle is measured from them.
        assert await svc.reap_once(now=svc.clock() + S.IDLE_S - 5) == []
        assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == [s["id"]]
    await stop_all(app)


async def test_two_silent_hours_end_the_exemption(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        live = svc.live[s["id"]]
        _task(live.driver, "task_started", "dev", task_type="local_bash")
        now = svc.clock()
        assert await svc.reap_once(now=now + S.BACKGROUND_SILENT_MAX_S - 60) == []
        assert await svc.reap_once(now=live.last_heard + S.BACKGROUND_SILENT_MAX_S + 1) == [s["id"]]
    await stop_all(app)


async def test_a_message_is_still_welcome_while_helpers_run(app, work, tmp_path, monkeypatch):
    # Claude Code takes a new message while its helpers run (it answers and
    # they keep going): the open tasks must never make the session BUSY, which
    # refuses a message ("still working") the way Pi's run of its own does.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        live = svc.live[s["id"]]
        _task(live.driver, "task_started", "a1", task_type="local_agent")
        assert live.background_tasks == 1
        assert not live.busy and not live.working_alone
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        view = row.get("session", row)
        assert view["running"] is False and view["process"] is True
    await stop_all(app)


async def test_the_pool_never_evicts_a_session_whose_helpers_run(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(S, "MAX_LIVE", 1)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, work)
        b = await new_session(c, work)
        await _say(c, a["id"])
        _task(svc.live[a["id"]].driver, "task_started", "a1", task_type="local_agent")
        r = await c.post(f"/build/app/sessions/{b['id']}/turn", json={"text": "hi", "turn_id": "tb"})
        assert svc.live[a["id"]].proc is not None, "the pool stopped a session whose helpers were working"
        assert r.status_code != 200 or any(e.get("type") == "error" for e in frames(r.text))
    await stop_all(app)


async def test_idle_counts_from_the_tools_last_frame_not_the_last_reply(app, work, tmp_path, monkeypatch):
    # After a helper finishes, Claude Code wakes ITSELF and writes a reply no
    # message of the user's owns: that is work, so the reaper's 15 minutes
    # start again from the tool's newest frame, whatever reply came before.
    from iron_jarvis.hosted import process as P

    seen: dict = {}
    real_start = P.HarnessProcess.start

    def start(self, loop, on_message, on_exit):
        seen["on_message"] = on_message
        return real_start(self, loop, on_message, on_exit)

    monkeypatch.setattr(P.HarnessProcess, "start", start)
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        live = svc.live[s["id"]]
        long_ago = svc.clock() - S.IDLE_S - 100
        live.last_active = live.last_heard = long_ago
        seen["on_message"]({"type": "system", "subtype": "status", "status": "requesting"})
        assert await svc.reap_once(now=svc.clock() + 60) == [], "a session the tool just spoke in was reaped"
        live.last_heard = long_ago
        assert await svc.reap_once(now=svc.clock() + 60) == [s["id"]]
    await stop_all(app)
