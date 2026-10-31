"""v1.417.0 (a USER BUG: "it seems to be getting stuck"): a Code-view Claude
Code session ends its reply ("I'll report as teams finish") while its helper
agents work on in the background; when they end, Claude Code WAKES ITSELF and
writes a report no message of the user's owns (measured: task_notification ->
a second system init -> a new assistant reply -> result). The session row now
says so — ``background_helpers`` (the helper agents among the open background
tasks) and ``self_reply`` (the tool is writing a reply of its own) — so the
Code view can say what is going on and read the session again until the
report is on screen; and Running now / the busy counts list such a session.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import news as NEWS
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, until, use_fake


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def _say(c, sid, turn_id="t1", text="build it with teams"):
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})
    return frames(r.text)


def _task(driver, sub: str, tid: str, **extra) -> None:
    driver.on_message({"type": "system", "subtype": sub, "task_id": tid, **extra})


def _own_reply(driver, msg: str, text: str) -> None:
    """Claude Code's report of its own, frame by frame (the recorded shape)."""
    driver.on_message({"type": "system", "subtype": "init", "session_id": driver.session_id or "s",
                       "model": "claude-opus-5-5", "cwd": driver.cwd, "permissionMode": "default"})
    driver.on_message({"type": "stream_event", "event": {"type": "message_start", "message": {"id": msg}}})
    driver.on_message({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                                          "content_block": {"type": "text"}}})
    driver.on_message({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                          "delta": {"type": "text_delta", "text": text}}})
    driver.on_message({"type": "assistant", "message": {"id": msg, "content": [{"type": "text", "text": text}]}})


def _result(driver, text: str) -> None:
    driver.on_message({"type": "result", "subtype": "success", "result": text,
                       "usage": {"input_tokens": 10, "output_tokens": 5}})


async def _row(c, sid) -> dict:
    return (await c.get(f"/build/app/sessions/{sid}")).json()


async def test_the_row_says_helpers_then_the_report_then_nothing(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        evs = await _say(c, s["id"])
        assert evs[-1]["type"] == "done"
        live = svc.live[s["id"]]
        drv = live.driver
        _task(drv, "task_started", "a1", task_type="local_agent")
        _task(drv, "task_started", "a2", task_type="local_agent")
        _task(drv, "task_started", "dev", task_type="local_bash")
        v = (await _row(c, s["id"]))["session"]
        assert (v["background"], v["background_helpers"], v["self_reply"]) == (3, 2, False)
        _task(drv, "task_notification", "a1", status="completed")
        v = (await _row(c, s["id"]))["session"]
        assert (v["background_helpers"], v["self_reply"]) == (1, True), "the tool wakes itself to report"
        _task(drv, "task_notification", "a2", status="completed")
        _own_reply(drv, "m_report", "Both teams finished: the cart is fixed.")
        view = await _row(c, s["id"])
        assert view["session"]["self_reply"] is True and view["session"]["background_helpers"] == 0
        assert view["running"] is None, "no reply of the user's runs"
        texts = [e.get("text", "") + e.get("delta", "") for e in view["events"] if e["type"] in ("text", "text_done")]
        assert any("the cart is fixed" in t for t in texts), "the report is in the session's record as it is written"
        _result(drv, "Both teams finished: the cart is fixed.")
        await until(lambda: any(e["type"] == "done" for e in live.log.read(50)[0][-3:]))
        view = await _row(c, s["id"])
        v = view["session"]
        assert (v["background"], v["background_helpers"], v["self_reply"]) == (1, 0, False), v
        # The dev server is not a helper, and its tool is quiet: nothing is
        # being written.
    await stop_all(app)


async def test_a_new_message_ends_the_own_reply_flag(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        drv = svc.live[s["id"]].driver
        _task(drv, "task_started", "a1", task_type="local_agent")
        _task(drv, "task_notification", "a1", status="completed")
        assert drv.replying_alone is True

        async def wrote(obj):  # the message goes nowhere: only the flags count
            return None

        monkeypatch.setattr(drv, "write", wrote)
        await drv.send_user("and now?", [])
        assert drv.replying_alone is False
        # That reply (the user's) ends: nothing of the tool's own is left.
        _result(drv, "Fine.")
        assert drv.turn_open is False
        assert drv.replying_alone is False
        assert (await _row(c, s["id"]))["session"]["self_reply"] is False
    await stop_all(app)


async def test_a_reply_of_its_own_that_goes_silent_stops_being_one(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        live = svc.live[s["id"]]
        _task(live.driver, "task_started", "a1", task_type="local_agent")
        _task(live.driver, "task_notification", "a1", status="completed")
        now = svc.clock()
        assert live.replying_alone(now) is True
        live.last_heard = now - S.SELF_REPLY_QUIET_S - 1
        assert live.replying_alone(now) is False
        assert (await _row(c, s["id"]))["session"]["self_reply"] is False
    await stop_all(app)


async def test_the_flags_are_read_before_the_events(app, work, tmp_path, monkeypatch):
    # A report that ends WHILE the record is read must never answer "nothing
    # is happening" beside events that miss its end — the Code view would
    # stop reading one read too early. The flags are taken first.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        live = svc.live[s["id"]]
        drv = live.driver
        _task(drv, "task_started", "a1", task_type="local_agent")
        _task(drv, "task_notification", "a1", status="completed")
        real_read = live.log.read

        def read_while_it_ends(n):
            drv._alone = False  # the report's result lands during the read
            return real_read(n)

        monkeypatch.setattr(live.log, "read", read_while_it_ends)
        assert (await _row(c, s["id"]))["session"]["self_reply"] is True
        assert (await _row(c, s["id"]))["session"]["self_reply"] is False
    await stop_all(app)


async def test_running_now_and_the_busy_counts_list_helpers_at_work(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    platform = app.state.platform
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        drv = svc.live[s["id"]].driver
        assert NEWS.running_rows(platform) == []
        _task(drv, "task_started", "dev", task_type="local_bash")
        assert NEWS.running_rows(platform) == [], "a background command alone is not a helper at work"
        _task(drv, "task_started", "a1", task_type="local_agent")
        _task(drv, "task_started", "a2", task_type="local_agent")
        rows = (await c.get("/chat/turns/running")).json()["replies"]
        mine = [r for r in rows if r.get("thread_id") == s["id"]]
        assert len(mine) == 1, rows
        assert mine[0]["turn_id"] == f"bg:{s['id']}" and mine[0]["background"] is True
        assert mine[0]["helpers"] == 2 and mine[0]["surface"] == "build_app" and mine[0]["waiting"] is False
        assert NEWS.counts(platform)["working"] == 1, "the PC must not sleep under helpers at work"
        _task(drv, "task_notification", "a1", status="completed")
        _task(drv, "task_notification", "a2", status="completed")
        rows = NEWS.running_rows(platform)
        assert len(rows) == 1 and rows[0]["helpers"] == 0, "the report being written is still work"
        _own_reply(drv, "m_r", "Done.")
        _result(drv, "Done.")
        assert NEWS.running_rows(platform) == []
        assert NEWS.counts(platform)["running"] == 0
    await stop_all(app)


async def test_a_reply_no_message_owns_is_one_of_its_own_even_with_no_notice(app, work, tmp_path, monkeypatch):
    # A reply Claude Code starts by itself for any other reason (no task
    # notice before it) is still one of its own.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        drv = svc.live[s["id"]].driver
        assert drv.replying_alone is False
        drv.on_message({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m_x"}}})
        assert drv.replying_alone is True
        assert (await _row(c, s["id"]))["session"]["self_reply"] is True
        _result(drv, "ok")
        assert drv.replying_alone is False
    await stop_all(app)
