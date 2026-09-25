"""v1.292.0 (platform-04): a two-way Slack (Socket Mode) channel added on the
Channels page connects NOW -- no restart -- and removing it stops the pump.

The lifespan used to arm the socket pump once at boot (``enabled()`` checked
once, ``run()`` snapshotting ``candidates()`` once); POST /comm/channels added
the channel live for a Send-test but never re-armed the pump, so DMs got no
reply until the app restarted. Now ``_live_rearm["slack"]`` swaps the pump
(fresh stop Event + task) and POST/DELETE /comm/channels hop it onto the loop.

Drives the REAL create_app lifespan and the REAL routes; stubs ONLY
``run_channel`` (the network dial). Waits are bounded polls for the thing
asserted -- no wall-clock threshold is asserted.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm import slack_socket
from iron_jarvis.daemon.app import create_app


def _slack(token: str) -> dict:
    return {
        "name": "slackdm",
        "type": "slack",
        "config": {
            "webhook_url": "https://hooks.slack.com/services/T/B/X",
            "app_token": token,
            "inbound_enabled": "true",
            "allowed_senders": "U0123",
        },
    }


@pytest.fixture
def pump(monkeypatch):
    """Records every pump start/stop the REAL ``run()`` performs, with the
    token it was handed, and blocks like the real pump until ``stop``."""
    events: list[tuple[str, str, str]] = []

    async def fake_run_channel(self, name, app_token, *, stop):
        events.append(("start", name, app_token))
        try:
            await stop.wait()
        finally:
            events.append(("stop", name, app_token))

    monkeypatch.setattr(slack_socket.SlackSocketMode, "run_channel", fake_run_channel)
    return events


def _wait_for(pred, ceiling: float = 20.0) -> None:
    deadline = time.time() + ceiling
    while not pred() and time.time() < deadline:
        time.sleep(0.05)


def _socket_tasks(client) -> list[str]:
    """The socket-pump tasks alive on the DAEMON loop (by coroutine name)."""
    loop = client.app.state.d._live_rearm["loop"]

    async def _list():
        out = []
        for t in asyncio.all_tasks():
            coro = t.get_coro()
            name = getattr(coro, "__qualname__", "") or ""
            if name.endswith("_slack_socket_loop") and not t.done():
                out.append(name)
        return out

    return asyncio.run_coroutine_threadsafe(_list(), loop).result(10)


def test_boot_with_no_slack_channel_arms_no_task_but_wires_the_rearm(tmp_path, pump):
    with TestClient(create_app(str(tmp_path))) as client:
        assert _socket_tasks(client) == []
        # The live re-arm is wired even when nothing is armed yet -- that is
        # what lets a channel added later connect without a restart.
        assert callable(client.app.state.d._live_rearm.get("slack"))
        assert pump == []


def test_channel_added_while_running_connects_and_removed_stops(tmp_path, pump):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/comm/channels", json=_slack("xapp-1-one"))
        assert r.status_code == 200, r.text
        _wait_for(lambda: ("start", "slackdm", "xapp-1-one") in pump)
        assert ("start", "slackdm", "xapp-1-one") in pump, (
            f"Slack Socket Mode never started for a channel added at runtime: {pump}"
        )
        assert _socket_tasks(client), "no socket-pump task on the daemon loop"

        r = client.delete("/comm/channels/slackdm")
        assert r.status_code == 200, r.text
        _wait_for(lambda: ("stop", "slackdm", "xapp-1-one") in pump)
        assert ("stop", "slackdm", "xapp-1-one") in pump, (
            f"removing the channel never stopped its pump: {pump}"
        )
        _wait_for(lambda: _socket_tasks(client) == [])
        assert _socket_tasks(client) == [], "a pump task outlived its channel"
        # Nothing restarted for a channel set with no candidate.
        assert [e for e in pump if e[0] == "start"] == [("start", "slackdm", "xapp-1-one")]


def test_token_change_restarts_the_pump_with_the_new_token(tmp_path, pump):
    """Editing the channel re-submits POST with a new app token: the old pump
    stops and a fresh one dials with the NEW token (read per run, never
    captured once)."""
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/comm/channels", json=_slack("xapp-1-one")).status_code == 200
        _wait_for(lambda: ("start", "slackdm", "xapp-1-one") in pump)
        assert ("start", "slackdm", "xapp-1-one") in pump, pump

        assert client.post("/comm/channels", json=_slack("xapp-1-two")).status_code == 200
        _wait_for(lambda: ("start", "slackdm", "xapp-1-two") in pump)
        assert ("stop", "slackdm", "xapp-1-one") in pump, pump
        assert ("start", "slackdm", "xapp-1-two") in pump, pump
        # The old pump is gone BEFORE the new one dials (never two on one channel).
        assert pump.index(("stop", "slackdm", "xapp-1-one")) < pump.index(
            ("start", "slackdm", "xapp-1-two")
        )
        assert len(_socket_tasks(client)) == 1
    # Shutdown stops whichever pump was CURRENT.
    _wait_for(lambda: ("stop", "slackdm", "xapp-1-two") in pump, ceiling=10.0)
    assert ("stop", "slackdm", "xapp-1-two") in pump, pump


def test_a_rearm_landing_after_shutdown_starts_no_pump_even_from_an_empty_holder(
    tmp_path, pump, monkeypatch
):
    """Review: with no pump ever running the old guard was skipped, so a
    channel add queued just before the lifespan's finally dialled Slack AFTER
    the teardown. The guard keys on the cleared loop, not on the old task."""
    with TestClient(create_app(str(tmp_path))) as client:
        d = client.app.state.d
        rearm = d._live_rearm["slack"]
        assert _socket_tasks(client) == []
    # The lifespan's finally has cleared _live_rearm. A channel is now
    # configured (the probe says yes) and the queued re-arm runs late.
    assert d._live_rearm.get("loop") is None
    monkeypatch.setattr(slack_socket.SlackSocketMode, "enabled", lambda self: True)
    rearm()  # the plain re-arm the route queues with call_soon_threadsafe
    assert pump == [], "a re-arm after shutdown must never start a pump"
