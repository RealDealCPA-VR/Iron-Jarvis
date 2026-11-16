"""v1.502.0 (north-star plan 2.7, D6): an agent asks before it makes an
OUTBOUND webhook.

THE DEFECT. ``webhook_add`` is allow-tier (``core/config.py``) so an agent
may wire its own INBOUND trigger unasked. The same tool with
``direction="outbound"`` registers a delivery that POSTs Iron Jarvis's events
to any address the model wrote, from then on, and that ran unasked too. Chat
asks (``settings/records.ASK_FIRST_CREATE_TOOLS``, the stream lane only).

THE RULE. The tool's own check (``WebhookAddTool.spend_check``, the seam every
lane already reads: the runtime pauses, the stream lane cards, the POST lane
refuses) says ASK for an outbound webhook, naming only the address's host
(a webhook address can be its own secret). An unattended run is refused in
words. Inbound is unchanged.

Proved through the layer that refuses: the real runtime's pause and the real
registry's invoke; only the model (and the outbound registry's network-checked
``register``, which sits behind the gate) is a double. The approval is
answered the moment it is filed (an event-bus spy), never after a sleep.
"""

from __future__ import annotations

import asyncio

from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_trust_posture_v1298 import (
    _app,
    _complete_scripted,
    _definition,
    _invocations,
    _scripted,
)

OUT = {"slug": "nightly", "direction": "outbound",
       "target_url": "https://hooks.example.test/path/abc", "event_types": ["session.completed"]}
IN = {"slug": "incoming", "direction": "inbound"}


def _world(tmp_path, decision: str):
    """The real app, a recorder behind the outbound registry, and a bus spy
    that answers each approval the moment it is published (no clock)."""
    _app_, platform, orch = _app(tmp_path)
    registered: list[str] = []
    platform.outbound_webhooks.register = lambda slug, *a, **k: registered.append(slug) or slug
    asked: list[dict] = []
    real = platform.event_bus.publish

    async def spy(etype, payload=None, session_id=None, **kw):
        out = await real(etype, payload, session_id=session_id, **kw)
        if str(etype) == "approval.requested":
            asked.append(dict(payload or {}))
            aid = (payload or {}).get("approval_id")
            asyncio.get_running_loop().call_soon(platform.approvals.resolve, aid, decision)
        return out

    platform.event_bus.publish = spy
    return platform, orch, registered, asked


async def _run(orch, platform, args: dict, *, origin: str | None) -> str:
    platform.router.stream = _scripted([[ToolCall(id="w1", name="webhook_add", arguments=args)]])
    session = await orch.create_session("wire up the webhook", AgentType.BUILDER, origin=origin)
    await orch.run_session(session.id, definition=_definition("webhook_add"))
    return session.id


def _row(platform, sid: str):
    return [r for r in _invocations(platform.engine, sid) if r.tool == "webhook_add"][-1]


async def test_an_attended_run_asks_and_a_no_registers_nothing(tmp_path):
    platform, orch, registered, asked = _world(tmp_path, "deny")
    sid = await _run(orch, platform, OUT, origin="user")
    assert [a.get("tool") for a in asked] == ["webhook_add"], asked
    why = str(asked[0].get("why") or "")
    assert "hooks.example.test" in why and "/path/abc" not in why, why
    assert _row(platform, sid).ok is False
    assert registered == []


async def test_an_attended_run_that_is_allowed_registers_it(tmp_path):
    platform, orch, registered, asked = _world(tmp_path, "once")
    sid = await _run(orch, platform, OUT, origin="user")
    assert len(asked) == 1
    assert _row(platform, sid).ok is True, _row(platform, sid).output
    assert registered == ["nightly"]


async def test_an_unattended_run_is_refused_in_words_and_never_parks(tmp_path):
    platform, orch, registered, asked = _world(tmp_path, "once")
    sid = await _run(orch, platform, OUT, origin="schedule:nightly")
    assert asked == [], "an unattended run parked for an answer"
    row = _row(platform, sid)
    assert row.ok is False and "nobody is watching" in row.output, row.output
    assert registered == []


async def test_an_inbound_webhook_is_still_made_unasked(tmp_path):
    platform, orch, _registered, asked = _world(tmp_path, "deny")
    sid = await _run(orch, platform, IN, origin="user")
    assert asked == []
    assert _row(platform, sid).ok is True, _row(platform, sid).output


async def test_the_post_chat_lane_refuses_an_outbound_webhook(tmp_path, monkeypatch):
    platform, _orch, registered, _asked = _world(tmp_path, "once")
    body = ChatBody(messages=[{"role": "user", "content": "send my events out"}], tools=["webhook_add"])
    _complete_scripted(platform, monkeypatch, [[ToolCall(id="w1", name="webhook_add", arguments=OUT)]])
    await run_chat_turn(platform, {}, body)
    row = _row(platform, "chat")
    # v1.507.0 (north-star plan 8.3, moved on purpose): the app's words,
    # never "this lane cannot ask for approval".
    assert row.ok is False and "needs your OK, which can only be given in the Iron Jarvis window" in row.output, row.output
    assert "lane" not in row.output
    assert "for free" not in row.output, "a webhook is not a spend"
    assert registered == []
