"""v1.357.0 re-review: two defects the lane reviews missed, both on the way
between two computers.

R1 A door address saved with a trailing slash. ``task_door.is_task_door_url``
   accepts ``http://desk:8787/agents/task-door/task/`` (the remote-agent form
   says "another computer running Iron Jarvis" for it, and registering it
   turns message-back on), but the install-bearer exemption in
   ``daemon/auth._is_exempt`` is an EXACT match on ``TASK_DOOR_PATH``. Dialled
   as typed, every task and every Test met the install guard (401), and the
   sender read "its token does not match. Copy the token again …" — a loop no
   fresh token could end. The sender now dials the exact leaf
   (``task_door.door_address``).

R2 A demo answer came home undisclosed. A fresh install answers on the demo
   model, so "@desk summarise the Q3 notes" to a second computer nobody chose
   a model on landed in the asking chat as "Delegated the task to a subagent;
   all subtasks complete." — scripted text read as desk's real work (the line
   an inbound door lands carries no receipt). The answer now carries
   ``task_door.DEMO_ANSWER_NOTE`` in the canon's words.

Driven through the REAL code: a real ``create_app`` for the other computer
(install bearer on, its Host allowed on purpose), the real remote registry's
``run`` and ``test``, the real door, runtime and demo adapter, with only the
network between the computers stood in (an httpx transport that hands each
request to the app or catcher its host names).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.remote import RemoteAgentRecord, RemoteAgentRegistry
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH
from iron_jarvis.providers.adapters.base import LLMResponse

INSTALL = "install-bearer-for-the-desk"
DESK = "desk-pc:8787"
LAPTOP = "laptop-pc:8787"
REPLY_TO = f"http://{LAPTOP}/agents/remote/desk/inbound"
ROOT = Path(__file__).resolve().parents[1]


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


# --------------------------------------------------------------------------- #
# R2 — a demo answer says so on the computer that asked
# --------------------------------------------------------------------------- #


class _TwoHosts(httpx.AsyncBaseTransport):
    """The network: desk's requests go to the desk app; the laptop's inbound
    door is a catcher that records what arrives and answers 200."""

    def __init__(self, desk_app: Any) -> None:
        self.desk = httpx.ASGITransport(app=desk_app)
        self.caught: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.netloc.decode()
        if host == DESK:
            return await self.desk.handle_async_request(request)
        if host == LAPTOP:
            self.caught.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"ok": True})
        raise httpx.ConnectError(f"no route to {host}", request=request)


@pytest.fixture()
def desk_and_laptop(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "desk-pc")
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL)
    holder: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        kw.pop("transport", None)
        kw["transport"] = holder["net"]
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)

    def make(home) -> tuple[Any, _TwoHosts]:
        desk = create_app(str(home))
        holder["net"] = _TwoHosts(desk)
        return desk, holder["net"]

    return make


async def _send_a_task(net: _TwoHosts, task: str) -> None:
    async with httpx.AsyncClient(
        base_url=f"http://{DESK}", headers={"Authorization": f"Bearer {INSTALL}"}
    ) as c:
        token = (await c.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        r = await c.post(
            TASK_DOOR_PATH,
            json={
                "task": task,
                "conversation_id": "athr_q3",
                "reply_to": REPLY_TO,
                "reply_token": "laptops-inbound-key",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 202, r.text


def _canon(name: str) -> str:
    src = (ROOT / "dashboard" / "lib" / "demoWords.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(rf'export const {name} = "([^"]+)";', src)
    assert m, name
    return m.group(1)


@pytest.mark.timeout(120)
async def test_a_fresh_desk_on_the_demo_model_says_so_in_the_asking_chat(tmp_path, desk_and_laptop):
    """A fresh install answers on the demo model. Nothing here is scripted:
    the real door, runtime and demo adapter run the task."""
    desk, net = desk_and_laptop(tmp_path / "desk")
    assert desk.state.platform.config.default_provider == "mock", "a fresh install"
    await _send_a_task(net, "summarise the Q3 notes and list the action items")
    await _wait_runs_settle(desk)
    assert len(net.caught) == 1, net.caught
    message = net.caught[0]["message"]
    # v1.359.0 integrate (moved on purpose): the demo model's own answer now
    # opens by saying what it is (mock.DEMO_OPENING), so the note after it is
    # the short one and the demo is named ONCE (lane 9A review, follow-up 4).
    # An answer that does not open that way still gets the full note (the
    # cut test below).
    assert message.endswith(td.DEMO_ANSWER_SHORT_NOTE), message
    assert message.lower().count("demo model") == 1, message
    # The demo's one name and sentence (VOCABULARY.md; lib/demoWords).
    assert _canon("DEMO_MODEL") in td.DEMO_ANSWER_NOTE
    assert _canon("DEMO_REPLIES") in td.DEMO_ANSWER_NOTE
    assert "mock" not in td.DEMO_ANSWER_NOTE.lower()


@pytest.mark.timeout(120)
async def test_a_real_models_answer_comes_home_without_the_note(tmp_path, desk_and_laptop):
    desk, net = desk_and_laptop(tmp_path / "desk")

    async def real_model(**kw):
        yield {
            "type": "final",
            "response": LLMResponse(text="Three action items: A, B and C."),
            "provider": "openai",
            "model": "gpt-5",
        }

    desk.state.platform.router.stream = real_model
    await _send_a_task(net, "summarise the Q3 notes")
    await _wait_runs_settle(desk)
    assert [c["message"] for c in net.caught] == ["Three action items: A, B and C."]


def test_the_note_survives_the_cut_and_never_rides_a_failure():
    long_demo = SimpleNamespace(status="completed", provider="mock", summary="x" * 40_000)
    msg = td._reply_message(long_demo)
    assert len(msg) <= td.REPLY_MESSAGE_CHARS
    assert msg.endswith(td.DEMO_ANSWER_NOTE)
    assert "(The answer was longer; the rest is on the other computer.)" in msg
    # A run that failed or was stopped carries our sentence, not the demo's
    # (the row's provider column defaults to "mock" before anything ran).
    for status in ("failed", "cancelled"):
        row = SimpleNamespace(status=status, provider="mock", summary="It stopped.")
        assert td.DEMO_ANSWER_NOTE not in td._reply_message(row)
