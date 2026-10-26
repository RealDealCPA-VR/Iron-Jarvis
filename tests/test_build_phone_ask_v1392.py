"""v1.392.0 (the long-jobs plan's Phase 3, decision 3): A CODING SESSION'S
QUESTION REACHES THE PHONE.

A coding session's approval card that stays unanswered IN THE APP for five
minutes is published as ``approval.requested`` — the agent runtime's own
event — so the EXISTING path makes it an answerable phone prompt
(``comm/prompts.handle_approval_requested`` + the notifier's one phone line)
and the phone's "approve" resolves the card through the one registry, like a
press in the app. A card answered within five minutes sends nothing. The
SAFETY cards — once-only (the plan door, a project's or plugin's command
line, a commit / push), a Local only cloud card, a priced spend — are NEVER
sent: the whole card must be read where it is answered. ONE notice: "This
PC" stays quiet (the bell and the session's own waiting row say it there),
and the pending listing gives the card no second row as an agent's.

Driven through the REAL app factory with a fake phone channel; the five
minutes are read through ``harness.stall.CLOCK``, moved by the test.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.comm import InboundMessage, MockChannel
from iron_jarvis.comm.channels import DesktopChannel
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools.base import SpendCheck
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


class ChatMockChannel(MockChannel):
    supports_inbound = True

    def has_credentials(self) -> bool:
        return True


CHAT_CFG = {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}


class FakeClock:
    def __init__(self) -> None:
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


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
    # The card's wait looks every slice; a short slice keeps the test quick
    # (the five minutes are the fake clock's, never a sleep).
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.02)
    return c


@pytest.fixture()
def phone(app):
    """A chat-enabled phone channel with an established conversation, plus a
    "This PC" destination (which must stay quiet)."""
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


async def _looks(ticks, n: int = 5) -> None:
    start = ticks["n"]
    assert await until(lambda: ticks["n"] >= start + n, 30), "the card's wait never looked"


def _msg(text: str, update_id: int = 1) -> InboundMessage:
    return InboundMessage(sender_id="777", text=text, update_id=update_id, reply_to="777")


async def _card(app, turn: str) -> dict:
    got = await until(lambda: [d for e, d in turn_frames(app, turn) if e == "approval"], 30)
    assert got, "no approval card"
    return got[0]


async def test_a_card_unanswered_for_five_minutes_goes_to_the_phone_and_the_phones_approve_runs_it(
    app, work, clock, phone, ticks,
):
    ch, desk_sent = phone
    sid = new_session(app, work, title="Fix the nav bar")["id"]  # approve-for-me: a command cards
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo phoned"})], "Ran it."]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "bt-ph"}, drop=drop))
    try:
        card = await _card(app, "bt-ph")
        # Four minutes 59: nothing on the phone.
        clock.advance(299)
        await _looks(ticks)
        assert ch.sent == []
        # Five minutes: ONE answerable line.
        clock.advance(2)
        assert await until(lambda: ch.sent, 30), "the card never reached the phone"
        await _looks(ticks)
        assert len(ch.sent) == 1
        line = ch.sent[0]
        # v1.392.0 review (moved on purpose): a NEW sentence says "coding
        # session" (lib/buildWords, v1.389.0).
        assert "Your coding session" in line and "Fix the nav bar" in line
        assert "approve / deny" in line and "echo phoned" not in line  # names, never arguments
        # "This PC" says nothing (the bell and the session's own row do).
        assert desk_sent == []
        # The bell's listing gives it no second row as an agent's.
        status, listed = await asgi(app, "GET", "/chat/approvals/pending")
        assert status == 200 and not [a for a in listed["approvals"] if a.get("id") == card["id"]]
        # The phone's approve resolves the card like the press in the app.
        res = await app.state.inbound_poller._handle("tg", ch, _msg("approve"))
        assert res["status"] == "approval_answered", res
        lg = turn_log(app, "bt-ph")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        drop.set()
        await task
    frames = dict(turn_frames(app, "bt-ph"))
    assert frames["approval_resolved"]["decision"] == "once"
    assert frames["done"]["tools_used"] == ["shell"] and frames["done"]["reply"] == "Ran it."
    # The prompt expired with the card (approval.resolved followed it).
    pstore = app.state.pending_prompt_store
    assert await until(lambda: pstore.newest_open("tg", "777") is None, 30)


async def test_a_card_answered_within_five_minutes_sends_nothing(app, work, clock, phone, ticks):
    ch, desk_sent = phone
    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "bt-quick"}, drop=drop))
    try:
        card = await _card(app, "bt-quick")
        clock.advance(240)
        await _looks(ticks)
        assert (await asgi(app, "POST", f"/chat/approvals/{card['id']}", {"decision": "once"}))[0] == 200
        lg = turn_log(app, "bt-quick")
        assert await until(lambda: lg.status != "running", 30)
        clock.advance(3600)
    finally:
        drop.set()
        await task
    assert ch.sent == [] and desk_sent == []
    assert [e for e in app.state.platform.event_bus.history if e.type == "approval.requested"] == []


async def test_a_safety_card_is_never_sent_to_the_phone(app, work, clock, phone, ticks, monkeypatch):
    """A project's command line asks once, every time (``harness/
    command_shell``, once_reason "command") — app-only, however long it waits."""
    ch, desk_sent = phone
    from iron_jarvis.harness import command_shell

    monkeypatch.setattr(
        command_shell, "tool_check",
        lambda command: SpendCheck(ask=True, words="run the project's command", once_only=True,
                                   reason="command"),
    )
    decided: list[bool] = []
    real = stall.phone_may_ask

    def spy(**kw):
        decided.append(real(**kw))
        return decided[-1]

    monkeypatch.setattr(stall, "phone_may_ask", spy)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "bt-safe"}, drop=drop))
    try:
        card = await _card(app, "bt-safe")
        assert card.get("once_only") is True and card.get("once_reason") == "command"
        # The card was judged app-only when it went up: no phone to tick, ever.
        assert await until(lambda: decided, 30) and decided == [False]
        clock.advance(3600)
        for _ in range(10):
            await asyncio.sleep(0.02)
        assert ticks["n"] == 0 and ch.sent == []
        assert (await asgi(app, "POST", f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
        lg = turn_log(app, "bt-safe")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        drop.set()
        await task
    assert ch.sent == [] and desk_sent == []
    assert [e for e in app.state.platform.event_bus.history if e.type == "approval.requested"] == []


async def test_the_plan_door_is_never_sent_to_the_phone(app, work, clock, phone):
    ch, _desk = phone
    sid = new_session(app, work, mode="plan")["id"]
    wire(app, Script([[ToolCall(id="p1", name="ExitPlanMode", arguments={"plan": "1. Do it"})], "ok"]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "plan it", "turn_id": "bt-plan"}, drop=drop))
    try:
        card = await _card(app, "bt-plan")
        assert card.get("once_only") is True
        clock.advance(3600)
        # Give the wait many slices to look.
        for _ in range(20):
            await asyncio.sleep(0.02)
        assert ch.sent == []
        assert (await asgi(app, "POST", f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
        lg = turn_log(app, "bt-plan")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        drop.set()
        await task
    assert ch.sent == []


def test_which_cards_may_go_to_the_phone():
    assert stall.phone_may_ask(once_only=False, local_ask=False, priced=False) is True
    for kind in ("once_only", "local_ask", "priced"):
        assert stall.phone_may_ask(**{"once_only": False, "local_ask": False, "priced": False, kind: True}) is False


async def test_only_a_coding_sessions_card_goes_to_the_phone(app, work, clock, phone, monkeypatch):
    """A harness turn with no coding session behind it (the benchmark's, a
    caller of the lane that is not Build) waits in its own screen: the lane
    makes the phone hand-off only for a turn that carries a reply record."""
    ch, _desk = phone
    made: list[str] = []
    real_init = stall.PhoneAsk.__init__

    def spy(self, *a, **kw):
        made.append(str(a[1] if len(a) > 1 else kw.get("approval_id")))
        return real_init(self, *a, **kw)

    monkeypatch.setattr(stall.PhoneAsk, "__init__", spy)
    from iron_jarvis.core.turns import TURNS
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody

    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="run echo hi")],
                    workspace_dir=str(work), turn_id="ht-1", provider="anthropic", model="claude-sonnet-4-5")
    frames = await stream_chat_turn(app.state.platform, {}, body, harness="auto", round_budget=5,
                                    stay_in_chat=True, session_id="build:bench-x")
    got: list[str] = []

    async def drain():
        async for chunk in frames:
            got.append(chunk)

    task = asyncio.create_task(drain())
    try:
        assert await until(lambda: any("event: approval\n" in c for c in got), 30), "no card"
        clock.advance(3600)
        for _ in range(10):
            await asyncio.sleep(0.02)
        assert made == [] and ch.sent == []
    finally:
        TURNS.stop("ht-1")
        await asyncio.wait_for(task, 30)


def test_the_phone_line_names_the_session_and_the_call_in_the_cards_words():
    from iron_jarvis.comm.notifier import build_ask_words, format_event

    payload = {"approval_id": "apr_x", "tool": "shell", "surface": "build", "title": "Fix the nav bar",
               "thread_id": "bs1", "timeout_s": 0}
    words = build_ask_words(payload)
    # v1.392.0 review (moved on purpose): "coding session", the user's word.
    assert words.startswith("Your coding session “Fix the nav bar” is waiting for your OK to ")
    line = format_event({"type": "approval.requested", "payload": payload})
    assert line.startswith("⏸ Your coding session") and line.endswith("reply here: approve / deny.")
    # An agent's ask keeps its exact words.
    agent = format_event({"type": "approval.requested", "payload": {"approval_id": "a", "tool": "shell"}})
    assert agent == ("⏸ An agent is asking to use shell — approve from the dashboard bell, or reply here: "
                     "approve / deny.")
