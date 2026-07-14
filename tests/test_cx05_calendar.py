"""Offline tests for the CX-05 calendar trigger (the world starts work).

No real network: the ICS feed is a canned string returned by an injected
``http_get``, the reflex router is a recording stub, and the durable at-most-once
cursor lives in a real in-memory SQLite engine. Covers the security model:
off-by-default (needs the flag AND the secret URL), lead-window filtering, the
durable cursor that fires each event exactly once, and a malformed feed that
never raises and never fires.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from sqlmodel import SQLModel, create_engine

from iron_jarvis.triggers import models as trigger_models  # registers the table
from iron_jarvis.triggers.calendar import CalendarPoller
from iron_jarvis.triggers.models import CalendarFiredRecord

# Reference the imported module so linters keep the table-registering import.
assert trigger_models.CalendarFiredRecord is CalendarFiredRecord


# --------------------------------------------------------------------------- #
# Fakes.
# --------------------------------------------------------------------------- #
class _Secrets:
    """A dict-backed stand-in for platform.secrets (only ``get`` is used)."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._v = dict(values or {})

    def get(self, name: str) -> str | None:
        return self._v.get(name)

    def set(self, name: str, value: str) -> None:
        self._v[name] = value


class _RecordingRouter:
    """Records ``on_calendar`` calls (the reflex router's calendar entry point)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def on_calendar(
        self, *, title: str, start: str = "", description: str = ""
    ) -> list[dict[str, Any]]:
        self.calls.append({"title": title, "start": start, "description": description})
        return [{"ok": True, "kind": "session"}]  # pretend one rule fired


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def _platform(secrets: _Secrets, *, enabled: bool = True, lead: int = 15):
    return SimpleNamespace(
        config=SimpleNamespace(
            calendar_trigger_enabled=enabled, calendar_lead_minutes=lead
        ),
        secrets=secrets,
    )


def _z(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _ics_two_events() -> str:
    """One event +5 min (inside the 15-min lead) and one +2 h (outside)."""
    now = datetime.now(timezone.utc)
    soon = now + timedelta(minutes=5)
    later = now + timedelta(hours=2)
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:evt-soon-1\r\n"
        "SUMMARY:Standup meeting\r\n"
        f"DTSTART:{_z(soon)}\r\n"
        "DESCRIPTION:Daily sync\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:evt-later-1\r\n"
        "SUMMARY:Afternoon review\r\n"
        f"DTSTART:{_z(later)}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


# --------------------------------------------------------------------------- #
# OFF BY DEFAULT — needs the flag AND the secret URL.
# --------------------------------------------------------------------------- #
def test_disabled_without_secret_url():
    engine = _engine()
    # Flag on, but no ICS url stored => disabled.
    poller = CalendarPoller(
        _platform(_Secrets(), enabled=True), _RecordingRouter(), engine,
        http_get=lambda _u: _ics_two_events(),
    )
    assert poller.enabled() is False


def test_disabled_when_flag_off_even_with_url():
    engine = _engine()
    secrets = _Secrets({"calendar_ics_url": "https://cal.example/feed.ics"})
    poller = CalendarPoller(
        _platform(secrets, enabled=False), _RecordingRouter(), engine,
        http_get=lambda _u: _ics_two_events(),
    )
    assert poller.enabled() is False


# --------------------------------------------------------------------------- #
# DUE EVENT — fires on_calendar exactly once (the +5 min, not the +2 h).
# --------------------------------------------------------------------------- #
async def test_fires_due_event_once_and_cursor_dedupes():
    engine = _engine()
    secrets = _Secrets({"calendar_ics_url": "https://cal.example/feed.ics"})
    router = _RecordingRouter()
    poller = CalendarPoller(
        _platform(secrets, enabled=True), router, engine,
        http_get=lambda _u: _ics_two_events(),
    )
    assert poller.enabled() is True

    results = await poller.poll_once()

    # Exactly the near-term event fired; the +2 h event stayed outside the window.
    assert len(results) == 1
    assert results[0]["uid"] == "evt-soon-1"
    assert len(router.calls) == 1
    assert router.calls[0]["title"] == "Standup meeting"
    assert router.calls[0]["description"] == "Daily sync"
    assert router.calls[0]["start"]  # an ISO start was rendered

    # The durable cursor recorded the fired UID.
    from iron_jarvis.core.db import session_scope

    with session_scope(engine) as db:
        assert db.get(CalendarFiredRecord, "evt-soon-1") is not None

    # SECOND pass over the SAME feed fires nothing (cursor dedupes).
    results2 = await poller.poll_once()
    assert results2 == []
    assert len(router.calls) == 1  # no duplicate fire


# --------------------------------------------------------------------------- #
# MALFORMED FEED — never raises, never fires.
# --------------------------------------------------------------------------- #
async def test_malformed_ics_never_raises_or_fires():
    engine = _engine()
    secrets = _Secrets({"calendar_ics_url": "https://cal.example/feed.ics"})
    router = _RecordingRouter()
    poller = CalendarPoller(
        _platform(secrets, enabled=True), router, engine,
        http_get=lambda _u: "not an ICS file at all\n\x00\x01garbage",
    )

    results = await poller.poll_once()

    assert results == []
    assert router.calls == []


async def test_empty_feed_is_a_noop():
    engine = _engine()
    secrets = _Secrets({"calendar_ics_url": "https://cal.example/feed.ics"})
    router = _RecordingRouter()
    poller = CalendarPoller(
        _platform(secrets, enabled=True), router, engine, http_get=lambda _u: ""
    )
    assert await poller.poll_once() == []
    assert router.calls == []
