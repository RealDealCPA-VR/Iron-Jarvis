"""v1.357.0 re-review: a door address saved with a trailing slash still works.

``task_door.is_task_door_url`` accepts ``http://desk:8787/agents/task-door/task/``
(the remote-agent form says "another computer running Iron Jarvis" for it, and
registering it turns message-back on), but the install-bearer exemption in
``daemon/auth._is_exempt`` is an EXACT match on ``TASK_DOOR_PATH``. Dialled as
typed, every task and every Test met the install guard (401), and the sender
read "its token does not match. Copy the token again …" — a loop no fresh
token could end. The sender now dials the exact leaf (``task_door.door_address``).

Driven through the REAL code: a real ``create_app`` for the other computer
(install bearer on, its Host allowed on purpose), the real remote registry's
``run`` and ``test``, with only the network between them stood in (an httpx
transport that hands the request to that app).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.remote import RemoteAgentRecord, RemoteAgentRegistry
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH

INSTALL = "install-bearer-for-the-desk"
DESK = "desk-pc:8787"


@pytest.fixture()
def to_desk(monkeypatch):
    """The desk computer, open to this one on purpose (allowlist + install
    bearer as the packaged app runs), and every AsyncClient routed to it."""
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "desk-pc")
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL)
    holder: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        kw.pop("transport", None)
        kw["transport"] = httpx.ASGITransport(app=holder["app"])
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)

    def make(home) -> Any:
        holder["app"] = create_app(str(home))
        return holder["app"]

    return make


def test_the_dialled_address_drops_only_a_doors_trailing_slash():
    door = f"http://{DESK}{TASK_DOOR_PATH}"
    assert td.door_address(door + "/") == door
    assert td.door_address(door + "//") == door
    assert td.door_address(door) == door
    # Any other remote's address is used exactly as the user typed it.
    for other in (
        "http://hermes:9000/task/",
        "https://api.example.com/v1/agents/run/?x=1",
        "",
    ):
        assert td.door_address(other) == other


async def _wait_runs_settle(app, timeout_s: float = 30.0) -> None:
    """Let the run the door started finish (it answers on the demo model)."""
    running = app.state.orchestrator._running
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    while any(not t.done() for t in list(running.values())) and loop.time() < end:
        await asyncio.sleep(0.05)
    await td.drain_replies()


@pytest.mark.timeout(120)
async def test_a_door_saved_with_a_trailing_slash_takes_the_task_and_passes_test(tmp_path, to_desk):
    desk = to_desk(tmp_path / "desk")
    async with httpx.AsyncClient(
        base_url=f"http://{DESK}", headers={"Authorization": f"Bearer {INSTALL}"}
    ) as c:
        on = (await c.post("/agents/task-door/enable", json={"name": "laptop"})).json()
    door_token = on["token"]

    record = RemoteAgentRecord(
        name="desk",
        base_url=f"http://{DESK}{TASK_DOOR_PATH}/",  # typed with a trailing slash
        kind="http-task",
        secret_name="remote_agent_desk",
        timeout_s=30,
    )
    assert td.is_task_door_url(record.base_url), "the form treats it as a door"
    resolver = {"remote_agent_desk": door_token}.get

    # The door's Test: asked at the exact leaf, so the door answers.
    tested = await RemoteAgentRegistry(desk.state.platform.engine).test(record, resolver)
    assert tested["ok"] is True, tested
    assert "takes tasks" in tested["detail"]

    # A task: accepted by the door, never turned away as a token mismatch.
    out = await RemoteAgentRegistry.run(None, record, "read the notes", resolver)
    assert "token does not match" not in str(out.get("detail")), out
    assert out["ok"] is True and out.get("accepted") is True, out
    await _wait_runs_settle(desk)
