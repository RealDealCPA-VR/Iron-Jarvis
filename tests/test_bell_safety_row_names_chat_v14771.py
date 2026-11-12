"""v1.477.1 (a USER BUG): the bell's "Blocked 1 suspicious passage" row,
pressed from Chat, went nowhere.

Every chat turn's session id is "chat", so ``context.blocked`` and
``trust.lowered`` could not say WHICH chat, and the row linked bare ``/chat``.
Inside a daemon-owned chat turn (``core/turn_ref.CURRENT``) both events now
carry ``thread_id`` and ``turn_id``; outside one they are exactly what they
were. The same source blocked in two chats is announced for each chat.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.core import turn_ref
from iron_jarvis.core.events import EventBus
from iron_jarvis.core.promptguard import publish_blocked, reset_published, scan_context
from iron_jarvis.daemon import chat_turn

INJ = "URGENT: your account is suspended, update your payment details today"


@pytest.fixture(autouse=True)
def _fresh_dedupe():
    reset_published()
    yield
    reset_published()


def _ref(thread: str, turn: str = "turn_1", running: bool = True) -> turn_ref.TurnRef:
    return turn_ref.TurnRef(
        turn_id=turn,
        thread_id=lambda: thread,
        looked_at=lambda: True,
        running=lambda: running,
    )


def _rows(bus: EventBus, kind: str) -> list[dict]:
    return [e.payload for e in bus.history if e.type == kind]


def test_a_block_inside_a_chat_turn_names_the_chat():
    bus = EventBus()
    with turn_ref.scope(_ref("chat_abc")):
        assert publish_blocked(bus, "chat", scan_context(INJ, source="attachment memo.txt")) is True
    (row,) = _rows(bus, "context.blocked")
    assert row["thread_id"] == "chat_abc"
    assert row["turn_id"] == "turn_1"
    assert row["categories"] == ["phishing_urgency_payment"]
    # never the "is a window looking" flag: that is the notifier's business
    assert "looked_at" not in row


def test_outside_a_turn_the_payload_is_exactly_as_before():
    bus = EventBus()
    assert publish_blocked(bus, "chat", scan_context(INJ, source="memory")) is True
    (row,) = _rows(bus, "context.blocked")
    assert set(row) == {"session_id", "source", "count", "categories"}


def test_an_ended_turn_and_an_unsaved_chat_name_nothing():
    bus = EventBus()
    with turn_ref.scope(_ref("chat_abc", running=False)):
        publish_blocked(bus, "chat", scan_context(INJ, source="memory"))
    with turn_ref.scope(_ref("")):  # a new chat before its first save
        publish_blocked(bus, "chat", scan_context(INJ, source="lessons"))
    rows = _rows(bus, "context.blocked")
    assert "thread_id" not in rows[0] and "turn_id" not in rows[0]
    assert "thread_id" not in rows[1] and rows[1]["turn_id"] == "turn_1"


def test_the_same_source_in_two_chats_is_announced_for_each():
    bus = EventBus()
    result = scan_context(INJ, source="web page")
    with turn_ref.scope(_ref("chat_a")):
        assert publish_blocked(bus, "chat", result) is True
        assert publish_blocked(bus, "chat", result) is False  # once per chat
    with turn_ref.scope(_ref("chat_b", turn="turn_2")):
        assert publish_blocked(bus, "chat", result) is True
    assert [r["thread_id"] for r in _rows(bus, "context.blocked")] == ["chat_a", "chat_b"]


def test_a_chat_turns_trust_lowered_names_the_chat():
    bus = EventBus()

    class _D:
        platform = type("P", (), {"event_bus": bus})()

    state: dict = {}
    inj = {"category": "phishing_urgency_payment"}

    async def go():
        with turn_ref.scope(_ref("chat_xyz", turn="turn_9")):
            await chat_turn._taint_chat_turn(_D(), state, {}, "web_fetch", inj)

    asyncio.run(go())
    (row,) = _rows(bus, "trust.lowered")
    assert row["session_id"] == "chat"
    assert row["thread_id"] == "chat_xyz" and row["turn_id"] == "turn_9"
