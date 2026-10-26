"""v1.392.0 review (decision 3): a coding session's card on the phone, the
claims checked through the REAL notifier, ``comm/prompts`` and the inbound
poller with a fake phone channel.

- A card that went to the phone and is then answered IN THE APP closes its
  phone prompt: the phone's "approve" a moment later answers nothing, and it
  never lands on the NEXT card the reply puts up.
- The next card goes to the phone on its own five minutes, and the phone's
  "approve" answers exactly it — Allow once, never wider.
- A batch card says how many calls ride on the one answer.

The five minutes are read through ``harness.stall.CLOCK`` (moved by the test).
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.comm.channels import DesktopChannel
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_phone_ask_v1392 import (
    CHAT_CFG,
    ChatMockChannel,
    FakeClock,
    _card,
    _looks,
    _msg,
)
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    new_session,
    open_turn,
    turn_frames,
    turn_log,
    until,
    wire,
)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(stall, "CLOCK", c)
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.02)
    return c


@pytest.fixture()
def phone(app):
    """The base file's phone: a chat-enabled channel with a conversation,
    and a "This PC" destination that must stay quiet."""
    platform = app.state.platform
    ch = ChatMockChannel(dict(CHAT_CFG))
    platform.notifier.add_channel("tg", ch)
    desk = DesktopChannel({})
    desk_sent: list[str] = []
    desk.send = lambda message: desk_sent.append(message) or {"ok": True}  # type: ignore[method-assign]
    platform.notifier.add_channel("this-pc", desk)
    app.state.inbound_poller.thread_store.resolve("tg", "777", "Val")
    return ch, desk_sent


@pytest.fixture()
def ticks(monkeypatch):
    seen = {"n": 0}
    real = stall.PhoneAsk.tick

    def counted(self):
        seen["n"] += 1
        return real(self)

    monkeypatch.setattr(stall.PhoneAsk, "tick", counted)
    return seen


def _cards(app, turn: str) -> list[dict]:
    return [d for e, d in turn_frames(app, turn) if e == "approval"]


async def test_an_in_app_answer_closes_the_phone_prompt_and_the_phone_answers_only_the_next_card(
    app, work, clock, phone, ticks,
):
    ch, desk_sent = phone
    pstore = app.state.pending_prompt_store
    sid = new_session(app, work, title="Two steps")["id"]  # approve-for-me: each command cards
    wire(app, Script([
        [ToolCall(id="a1", name="Bash", arguments={"command": "echo one"})],
        [ToolCall(id="b1", name="Bash", arguments={"command": "echo two"})],
        # With nothing waiting on the phone, its "approve" is an ordinary
        # message to Jarvis (the bare words resolve only an open prompt).
        "Nothing of yours is waiting.",
        "Both ran.",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run both", "turn_id": "bt-two-cards"}, drop=drop))
    try:
        first = await _card(app, "bt-two-cards")
        clock.advance(301)
        assert await until(lambda: len(ch.sent) == 1, 30), "the first card never reached the phone"
        assert await until(lambda: pstore.newest_open("tg", "777") is not None, 30)
        # Answered in the app: its phone prompt closes with it.
        assert (await asgi(app, "POST", f"/chat/approvals/{first['id']}", {"decision": "once"}))[0] == 200
        assert await until(lambda: pstore.newest_open("tg", "777") is None, 30), "the phone prompt stayed open"
        second = await until(lambda: [c for c in _cards(app, "bt-two-cards") if c["id"] != first["id"]], 30)
        assert second, "no second card"
        second = second[0]
        # The phone's "approve" now: nothing is waiting there — it must not
        # answer the second card, which the phone has not been asked yet.
        res = await app.state.inbound_poller._handle("tg", ch, _msg("approve", update_id=2))
        assert res["status"] != "approval_answered", res
        assert res.get("approval_id") is None
        await _looks(ticks)
        assert [d for e, d in turn_frames(app, "bt-two-cards")
                if e == "approval_resolved" and d.get("id") == second["id"]] == []
        # Its own five minutes: the second card reaches the phone, and the
        # phone's approve answers exactly it, once.
        clock.advance(301)
        assert await until(lambda: sum("waiting for your OK" in s for s in ch.sent) == 2, 30)
        res = await app.state.inbound_poller._handle("tg", ch, _msg("approve", update_id=3))
        assert res["status"] == "approval_answered" and res["approval_id"] == second["id"], res
        assert res["decision"] == "once"
        lg = turn_log(app, "bt-two-cards")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        drop.set()
        await task
    resolved = {d["id"]: d["decision"] for e, d in turn_frames(app, "bt-two-cards") if e == "approval_resolved"}
    assert resolved == {first["id"]: "once", second["id"]: "once"}
    assert dict(turn_frames(app, "bt-two-cards"))["done"]["reply"] == "Both ran."
    assert desk_sent == []


async def test_a_batch_card_on_the_phone_says_how_many_calls_ride_on_it(app, work, clock, phone, ticks):
    ch, _desk = phone
    sid = new_session(app, work, title="Batch")["id"]
    wire(app, Script([
        [
            ToolCall(id="m1", name="Bash", arguments={"command": "echo one"}),
            ToolCall(id="m2", name="Bash", arguments={"command": "echo two"}),
        ],
        "Both ran.",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run both", "turn_id": "bt-batch"}, drop=drop))
    try:
        card = await _card(app, "bt-batch")
        assert card.get("count") == 2
        clock.advance(301)
        assert await until(lambda: ch.sent, 30)
        assert "(2 calls)" in ch.sent[0] and "echo" not in ch.sent[0]
        res = await app.state.inbound_poller._handle("tg", ch, _msg("approve"))
        assert res["status"] == "approval_answered" and res["decision"] == "once"
        lg = turn_log(app, "bt-batch")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        drop.set()
        await task
    assert dict(turn_frames(app, "bt-batch"))["done"]["tools_used"] == ["shell", "shell"]


@pytest.mark.parametrize("kind", ["local_ask", "priced"])
def test_a_cloud_or_priced_card_never_goes(kind):
    assert stall.phone_may_ask(**{"once_only": False, "local_ask": False, "priced": False, kind: True}) is False
