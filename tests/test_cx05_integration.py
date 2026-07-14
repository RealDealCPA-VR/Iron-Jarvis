"""CX-05 "inbound everything" — end-to-end integration.

The per-piece suites (test_cx05_email/calendar/slack/api) prove each trigger in
isolation. THIS suite proves the whole story ties together on a real daemon: a
world signal on each NEW source (email / calendar / slack) flows through the
extended Reflex Loop and fires a bound action through the SAME gated path a local
user's signal uses — nothing bypasses the orchestrator/permission engine. It also
proves source-scoping (an email rule never fires on a Slack signal) and the
calendar poller's at-most-once cursor end-to-end.

Fully offline against a temp-root daemon; assertions are on the SYNCHRONOUSLY
created run-record, and background tasks are cancelled at TestClient teardown.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.daemon.app import create_app
from iron_jarvis.core.db import session_scope
from iron_jarvis.triggers.calendar import CalendarPoller
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore

_STEPS = [{"agent": "builder", "task": "say hi"}]


def _seed(p) -> None:
    WorkflowStore(p.engine).save("nightly", _STEPS, description="t")


def _runs(p) -> int:
    with session_scope(p.engine) as db:
        return len(list(db.exec(select(WorkflowRunRecord))))


# --------------------------------------------------------------------------- #
# 1. Each new source fires its bound action through the real router.
# --------------------------------------------------------------------------- #
def test_email_signal_fires_reflex(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        _seed(p)
        p.reflex.add(
            name="invoices", source="email", match="invoice",
            action="workflow", target="nightly",
        )
        before = _runs(p)
        res = asyncio.run(
            router.on_email(sender="boss@acme.com", subject="INVOICE #22 due",
                            body="please review")
        )
        assert len(res) == 1 and res[0]["ok"] and res[0]["kind"] == "workflow"
        assert _runs(p) == before + 1
        # A non-matching email is a no-op (no fabricated action).
        assert asyncio.run(
            router.on_email(sender="boss@acme.com", subject="lunch?", body="today")
        ) == []
        assert _runs(p) == before + 1


def test_calendar_signal_fires_reflex(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        _seed(p)
        p.reflex.add(
            name="standups", source="calendar", match="standup",
            action="workflow", target="nightly",
        )
        before = _runs(p)
        res = asyncio.run(
            router.on_calendar(title="Team Standup", start="2026-07-14T09:00:00Z",
                               description="daily sync")
        )
        assert len(res) == 1 and res[0]["ok"]
        assert _runs(p) == before + 1


def test_slack_signal_fires_reflex(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        _seed(p)
        p.reflex.add(
            name="deploys", source="slack", match="deploy",
            action="workflow", target="nightly",
        )
        before = _runs(p)
        res = asyncio.run(
            router.on_slack(text="please deploy prod now", channel="C1", sender="U1")
        )
        assert len(res) == 1 and res[0]["ok"]
        assert _runs(p) == before + 1


# --------------------------------------------------------------------------- #
# 2. Source-scoping: a rule fires ONLY for its own source.
# --------------------------------------------------------------------------- #
def test_sources_are_scoped(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        _seed(p)
        p.reflex.add(name="e", source="email", match="ping", action="workflow", target="nightly")
        before = _runs(p)
        # A Slack signal carrying the email rule's keyword must NOT fire it.
        assert asyncio.run(router.on_slack(text="ping ping", channel="c", sender="u")) == []
        # A comm signal must not fire it either.
        assert asyncio.run(router.on_comm("ping")) == []
        assert _runs(p) == before
        # ...but an email signal does.
        assert len(asyncio.run(router.on_email(sender="x@y.z", subject="ping", body=""))) == 1
        assert _runs(p) == before + 1


# --------------------------------------------------------------------------- #
# 3. Calendar poller end-to-end: due event -> fired rule, at-most-once.
# --------------------------------------------------------------------------- #
_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:evt-standup-001\r\n"
    "SUMMARY:Morning Standup\r\n"
    "DTSTART:{start}\r\n"
    "DESCRIPTION:daily sync\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:evt-far-002\r\n"
    "SUMMARY:Quarterly Review\r\n"
    "DTSTART:{far}\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _ics_stamps():
    """A 'due within lead' event and a far-future one, in ICS UTC form."""
    from datetime import timedelta
    from iron_jarvis.core.ids import utcnow

    now = utcnow()
    soon = now + timedelta(minutes=5)
    far = now + timedelta(days=3)
    fmt = "%Y%m%dT%H%M%SZ"
    return soon.strftime(fmt), far.strftime(fmt)


def test_calendar_poller_end_to_end(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        _seed(p)
        p.reflex.add(
            name="standups", source="calendar", match="standup",
            action="workflow", target="nightly",
        )
        # Opt in: flag ON + a stored secret ICS URL.
        p.config.calendar_trigger_enabled = True
        p.secrets.set("calendar_ics_url", "https://cal.example/secret.ics", kind="password")

        soon, far = _ics_stamps()
        ics = _ICS.format(start=soon, far=far)

        def fake_get(url: str) -> str:
            return ics

        poller = CalendarPoller(p, router, p.engine, http_get=fake_get)
        assert poller.enabled() is True

        before = _runs(p)
        fired = asyncio.run(poller.poll_once())
        # Exactly the DUE 'standup' event fired (not the far-future review).
        assert _runs(p) == before + 1, fired
        # At-most-once: a second pass fires nothing new (durable cursor).
        asyncio.run(poller.poll_once())
        assert _runs(p) == before + 1


def test_calendar_poller_off_by_default(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        # No flag, no secret => disabled, and poll_once is a pure no-op.
        poller = CalendarPoller(p, router, p.engine, http_get=lambda url: _ICS)
        assert poller.enabled() is False
        assert asyncio.run(poller.poll_once()) == []
