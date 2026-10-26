"""A Local only coding session's question never goes to the phone (v1.392.0,
the Phase 3 review's open item, decided at integration).

Local only's promise is "nothing leaves this network without your OK". A
phone channel (Telegram, Slack, email) is a cloud service, and the phone line
carries the session's title and what the card asks. So under Local only every
card stays in the app, however long it waits — the same as the safety cards.
A session without Local only still sends an ordinary card after 5 minutes
(the reviewer's own pins hold that).
"""

from __future__ import annotations

import asyncio

from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_phone_ask_v1392 import (  # noqa: F401 — fixtures
    _card,
    app,
    clock,
    phone,
    ticks,
    work,
)
from tests.test_build_sessions_v1367 import Script, asgi, new_session, open_turn, turn_log, until, wire


def test_local_only_alone_keeps_a_card_off_the_phone():
    assert stall.phone_may_ask(once_only=False, local_ask=False, priced=False) is True
    assert stall.phone_may_ask(once_only=False, local_ask=False, priced=False, local_only=True) is False


async def test_a_local_only_sessions_card_never_goes_to_the_phone(app, work, clock, phone, ticks, monkeypatch):
    ch, desk_sent = phone
    decided: list[bool] = []
    real = stall.phone_may_ask

    def spy(**kw):
        decided.append(real(**kw))
        return decided[-1]

    monkeypatch.setattr(stall, "phone_may_ask", spy)
    # "Ask first" cards every file write; the write reaches nothing outside,
    # so it is an ORDINARY card (not once-only) — the case the rule decides.
    sid = new_session(app, work, approval_mode="always_ask", local_only=True)["id"]
    wire(app, Script([[ToolCall(id="w1", name="Write", arguments={"file_path": "a.txt", "content": "x"})], "ok"]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "write it", "turn_id": "bt-lo"}, drop=drop))
    try:
        card = await _card(app, "bt-lo")
        assert not card.get("once_only"), card
        assert await until(lambda: decided, 30) and decided == [False]
        clock.advance(3600)
        for _ in range(10):
            await asyncio.sleep(0.02)
        assert ticks["n"] == 0 and ch.sent == []
        assert (await asgi(app, "POST", f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
        lg = turn_log(app, "bt-lo")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        drop.set()
        await task
    assert ch.sent == [] and desk_sent == []
