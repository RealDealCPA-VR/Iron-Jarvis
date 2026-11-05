"""v1.434.0 (Wave 2 of the chat-first plan, group F) REVIEW: the two
person-pressed doors that start a coding-session turn the first cut missed.

Group F made a coding session's message, its "Continue from where it
stopped" and its "Try on several models" carry the screen's device id, so a
per-device setting (read aloud, the theme) changed there lands on the screen
that asked. Two more turns are started by the PERSON's press and carried the
id nowhere, so the settings tool answered "open this chat in the app" to a
user already in it:

* "Choose account" on the limit card with ``continue`` (``POST
  /build/sessions/{id}/account``) — the press starts the carried-on turn;
* Allow on the plan door — the daemon starts the act turn
  (``run_approved_plan``) that carries out the person's own message; the id
  of the screen the plan was asked from now rides along.

Each pin reads the ``ChatBody`` the REAL stream lane received (the field both
chat lanes put into the tool context), through the real app factory and
routes; only the models are scripted doubles.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as B
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_account_turns_v1368 import _finish, _isolated, _limit_then_arm, fake  # noqa: F401
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    messages_of,
    new_session,
    open_turn,
    turn_frames,
    turn_log,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post

DEVICE = "dev_phone0123456"
PLAN = "My plan:\n1. Change x in app.py\n2. Run the tests"


@pytest.fixture
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    yield a
    B._PLAN_RUNS.clear()


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _spy_lane(monkeypatch) -> list:
    """Every ChatBody the real stream lane receives."""
    seen: list = []
    real = B.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(body)
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(B, "stream_chat_turn", spy)
    return seen


def _asking(bodies: list, text: str) -> list:
    """The ChatBodies whose question (the last user message) is ``text``."""
    return [b for b in bodies if b.messages and b.messages[-1].content == text]


# --------------------------------------------------------------------------- #
# "Choose account" with continue
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("sent", "landed"), [(DEVICE, DEVICE), (None, None)])
async def test_choose_account_and_continue_carries_the_screens_id(app, fake, work, monkeypatch, sent, landed):  # noqa: F811
    _beh, sid, _card, _view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=600)
    bodies = _spy_lane(monkeypatch)
    body: dict = {"account": "c-spare", "continue": True}
    if sent is not None:
        body["device_id"] = sent
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/account", body)
    assert status == 200, out
    frames = await _finish(app, out["turn_id"])
    assert frames[-1][0] == "done", frames[-3:]
    carried = _asking(bodies, B.CONTINUE_TEXT)
    assert len(carried) == 1, [b.messages[-1].content for b in bodies]
    # Where the screen's id lands: the carried-on turn's ChatBody (None = an
    # older dashboard sent none, exactly as before).
    assert carried[0].device_id == landed


# --------------------------------------------------------------------------- #
# Allow on the plan door: the act turn
# --------------------------------------------------------------------------- #


async def _plan_turn(app, sid: str, turn_id: str, extra: dict) -> None:
    """One plan turn (the v1402 helper's shape): the model presents its plan
    on the plan door, the user allows it, the turn ends and its end
    callbacks have run."""
    drop = asyncio.Event()
    task = asyncio.create_task(
        open_turn(app, sid, {"text": "plan it", "turn_id": turn_id, **extra}, drop=drop),
    )
    card = (await until(lambda: [d for e, d in turn_frames(app, turn_id) if e == "approval"]))[0]
    assert card["tool"] == "exit_plan_mode"
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "once"}))[0] == 200
    lg = await until(lambda: turn_log(app, turn_id))
    await until(lambda: lg.status != "running", 30)
    await lg.task
    for _ in range(5):
        await asyncio.sleep(0)
    drop.set()
    await task


@pytest.mark.parametrize(("sent", "landed"), [(DEVICE, DEVICE), (None, None)])
async def test_the_act_turn_of_an_approved_plan_carries_the_screens_id(app, work, monkeypatch, sent, landed):
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    wire(app, Script([(PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Ready.", "Done it."]))
    bodies = _spy_lane(monkeypatch)
    await _plan_turn(app, sid, "dp-1", {"device_id": sent} if sent is not None else {})
    act = (await until(lambda: _asking(bodies, B.PLAN_RUN_TEXT), 30))
    assert len(act) == 1
    assert act[0].device_id == landed
    # The plan turn itself carried it too (group F's own path).
    assert _asking(bodies, "plan it")[0].device_id == landed
    # Let the act turn end (nothing left running at teardown).
    act_line = await until(lambda: [m for m in messages_of(app, sid) if m.get("continuation")])
    lg = await until(lambda: turn_log(app, act_line[0]["turnId"]))
    await until(lambda: lg.status != "running", 30)
