"""North-star 2.6 (v1.502.0): phone alerts and outbound webhooks honour Local only.

``comm/notifier._with_summary`` sent the model-written summary of a Local only
run to Slack / Telegram, and ``webhooks/outbound.OutboundWebhooks.on_event``
posted every subscribed event's whole payload to its outside address. Now:

* the event bus STAMPS an event published inside a Local only scope
  (``payload["local_only"] = True``), and every later event of a session it
  saw run under the scope (``session.completed`` is published after the run's
  scope has ended);
* ``core/events.event_is_local_only`` also reads the session row
  (``options.local_only``) and the saved chat (``setup.local_only``), for an
  event the bus never saw run (a run cut by a restart, a chat's
  ``chat.turn_finished``);
* the notifier sends the title-only line (no summary, no chat or session
  title, no question), and the webhook sender skips the event.

Proven through the layers that send: the real ``EventBus``, ``Notifier`` and
``OutboundWebhooks``, the real database rows.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.comm.notifier import Notifier
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import Event, EventBus, EventType, event_is_local_only
from iron_jarvis.providers import egress
from iron_jarvis.webhooks.outbound import OutboundWebhooks

SECRET_SUMMARY = "Hartley's 2025 return: refund of 4,210.55 to account ending 0099"


def _notifier(engine=None):
    n = Notifier()
    ch = MockChannel()
    n.add_channel("phone", ch)
    n.engine = engine
    return n, ch


def _done(payload_extra=None, session_id="sess_lo"):
    payload = {"status": "completed", "summary": SECRET_SUMMARY, "ok": True,
               "outcome": "completed", "origin": None, "project_id": None}
    payload.update(payload_extra or {})
    return Event(type=EventType.SESSION_COMPLETED, payload=payload, session_id=session_id)


# --------------------------------------------------------------------------- #
# The bus stamps
# --------------------------------------------------------------------------- #
def test_one_local_only_chat_does_not_stamp_every_later_chat():
    """Every chat turn publishes as the one session id "chat". A Local only
    chat must not leave that id remembered, or the alerts and webhooks of
    every later chat would lose their words too."""
    bus = EventBus()

    async def go():
        with egress.local_only_scope(True):
            private = await bus.publish(EventType.TOOL_EXECUTED, {"tool": "read_file"}, session_id="chat")
        later = await bus.publish(EventType.TOOL_EXECUTED, {"tool": "read_file"}, session_id="chat")
        return private, later

    private, later = asyncio.run(go())
    assert private.payload["local_only"] is True
    assert "local_only" not in later.payload



def test_an_event_published_under_the_scope_is_stamped_and_so_is_its_runs_finish():
    bus = EventBus()

    async def go():
        with egress.local_only_scope(True):
            during = await bus.publish(EventType.TOOL_EXECUTED, {"tool": "read_file"}, session_id="s1")
        finished = await bus.publish(EventType.SESSION_COMPLETED, {"summary": "x"}, session_id="s1")
        other = await bus.publish(EventType.SESSION_COMPLETED, {"summary": "y"}, session_id="s2")
        return during, finished, other

    during, finished, other = asyncio.run(go())
    assert during.payload["local_only"] is True
    assert finished.payload["local_only"] is True, "the run's finish is published after its scope"
    assert "local_only" not in other.payload, "another run's events are untouched"


def test_the_stamp_never_changes_the_callers_own_dict():
    bus = EventBus()
    mine = {"tool": "x"}

    async def go():
        with egress.local_only_scope(True):
            await bus.publish(EventType.TOOL_EXECUTED, mine)

    asyncio.run(go())
    assert mine == {"tool": "x"}


# --------------------------------------------------------------------------- #
# The notifier: the title-only line
# --------------------------------------------------------------------------- #
def test_a_stamped_runs_alert_is_the_title_only_line():
    n, ch = _notifier()
    n.on_event(_done({"local_only": True}))
    assert ch.sent, "the alert itself still goes: the owner asked to hear when it ends"
    assert SECRET_SUMMARY not in ch.sent[-1] and "Hartley" not in ch.sent[-1]
    assert ch.sent[-1] == "A run finished"
    # Anti-vacuity: an ordinary run's alert still carries its summary.
    n.on_event(_done(session_id="sess_plain"))
    assert "Hartley" in ch.sent[-1]


@pytest.fixture()
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path))


def test_a_local_only_runs_finish_the_bus_never_saw_is_read_off_its_row(app):
    """A run cut by a restart is finished at boot: no scope ever ran in this
    process, the session row says Local only."""
    from iron_jarvis.core.models import AgentType

    orch = app.state.orchestrator
    lo = asyncio.run(orch.create_session("summarise the return", AgentType.BUILDER,
                                         options={"local_only": True}))
    plain = asyncio.run(orch.create_session("summarise the memo", AgentType.BUILDER))
    engine = app.state.platform.engine
    assert event_is_local_only(_done(session_id=lo.id), engine) is True
    assert event_is_local_only(_done(session_id=plain.id), engine) is False

    n, ch = _notifier(engine)
    n.on_event(_done(session_id=lo.id))
    assert "Hartley" not in ch.sent[-1]
    # The daemon hands the live notifier its database.
    assert app.state.platform.notifier.engine is engine


def test_a_local_only_chats_finished_answer_names_no_chat_title(app, tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    made = client.put("/chat/threads/new", json={
        "title": "Hartley refund question",
        "messages": [{"role": "user", "content": "hi"}], "setup": {"local_only": True},
    })
    assert made.status_code == 200, made.text
    tid = made.json()["id"]
    engine = app.state.platform.engine
    n, ch = _notifier(engine)
    n.event_types.add(EventType.CHAT_TURN_FINISHED)
    n.on_event(Event(type=EventType.CHAT_TURN_FINISHED, payload={
        "thread_id": tid, "turn_id": "t1", "title": "Hartley refund question",
        "seconds": 400, "outcome": "done",
    }))
    assert ch.sent and "Hartley" not in ch.sent[-1]


# --------------------------------------------------------------------------- #
# The webhook sender skips
# --------------------------------------------------------------------------- #
def _hooks(tmp_path):
    engine = make_engine(tmp_path / "webhooks.db")
    init_db(engine)
    posted: list[dict] = []
    out = OutboundWebhooks(engine, lambda u, p, h: posted.append(p) or {"status": 200})
    out.register("notify", "https://example.com/hook", ["session.completed"])
    return out, posted


def test_a_stamped_event_is_never_posted_to_an_outbound_webhook(tmp_path):
    out, posted = _hooks(tmp_path)
    deliveries = out.on_event(_done({"local_only": True}))
    assert posted == []
    assert deliveries and deliveries[0].get("skipped") == "local_only"
    # Anti-vacuity: an ordinary run's event is posted as before.
    out.on_event(_done(session_id="sess_plain"))
    assert len(posted) == 1 and SECRET_SUMMARY in json.dumps(posted[0])


def test_a_local_only_runs_row_keeps_its_event_off_the_webhook(app):
    from iron_jarvis.core.models import AgentType

    engine = app.state.platform.engine
    posted: list[dict] = []
    out = OutboundWebhooks(engine, lambda u, p, h: posted.append(p) or {"status": 200})
    out.register("notify2", "https://example.com/hook", ["session.completed"])
    lo = asyncio.run(app.state.orchestrator.create_session(
        "summarise the return", AgentType.BUILDER, options={"local_only": True}))
    out.on_event(_done(session_id=lo.id))
    assert posted == []
