"""CX-05 "inbound everything" — trigger status + calendar-trigger config.

Read-only status of the three world-driven Reflex sources (email / calendar /
slack) plus the small config surface the calendar trigger needs: an on/off
switch, a lead-time, and a SECRET ICS URL that lives in the vault (never a
plaintext config value, never echoed back).

Everything here is OFF by default and rides the normal auth middleware — a
world signal gets no more power than a local user, and enabling a trigger only
means matching :class:`ReflexRule` rules run through the SAME orchestrator +
permission engine as any other action. The rules themselves are managed by the
Reflex routes (``routes/reflex.py``); this module only reports readiness and
lets the calendar poller be armed.

Moved-style verbatim register(app, d): ``d`` is the create_app deps object
(``d.platform`` for config/vault/notifier, ``d._persist_config`` for the same
atomic config write the settings route uses, ``d._live_rearm`` to re-arm the
background calendar loop without a restart).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

#: The vault key that holds the (secret) calendar ICS URL. Kept in the encrypted
#: vault — NOT in config.toml — and never returned in any response body.
from ...triggers.readiness import CALENDAR_ICS_SECRET as _CALENDAR_ICS_SECRET  # noqa: E402


class CalendarTriggerBody(BaseModel):
    """Configure the calendar trigger. ``enabled`` toggles the background poller;
    ``ics_url`` (when non-empty) is stored in the vault as a secret; ``lead_minutes``
    (when given) sets how early a due event fires its matching rules."""

    enabled: bool
    ics_url: str = ""
    lead_minutes: int | None = None


def register(app: FastAPI, d) -> None:
    """Attach the /triggers routes to *app*; ``d`` is the create_app deps object."""

    # Leap 05-b: the three readings moved to ``triggers/readiness`` word for
    # word (``GET /starts/kinds`` and chat's ``reflex_create`` read them too).
    from ...triggers import readiness as _readiness

    def _email_status() -> dict[str, Any]:
        return _readiness.email_status(d.platform)

    def _slack_status() -> dict[str, Any]:
        return _readiness.slack_status(d.platform)

    def _calendar_status() -> dict[str, Any]:
        return _readiness.calendar_status(d.platform)

    def _status() -> dict[str, Any]:
        return {
            "triggers": {
                "email": _email_status(),
                "calendar": _calendar_status(),
                "slack": _slack_status(),
            }
        }

    @app.get("/triggers")
    def get_triggers() -> dict[str, Any]:
        """Status summary for the three inbound sources (email / calendar / slack):
        whether each is enabled + credentialed. Read-only, never raises, never
        leaks a secret (the calendar ICS URL is reported only as ``has_url``)."""
        return _status()

    @app.get("/starts")
    def list_starts(workflow: str | None = None) -> dict[str, Any]:
        """Leap 05-b: every way work starts on its own here, in ONE list: your
        schedules (they start at a time) and your rules (something happens).
        Each row is its stored object, read from its own store (no table of
        its own): ``{id, kind, name, when_words, does_words, enabled,
        last_words, href, store}``. ``?workflow=<name>``: only what runs that
        saved workflow (its canvas's trigger node reads it). A plain ``def``
        (FastAPI runs it off the loop)."""
        from ...triggers import starts as _starts

        return {"starts": _starts.list_starts(d.platform, workflow=workflow)}

    @app.get("/starts/kinds")
    def start_kinds() -> dict[str, Any]:
        """Leap 05-b: can each kind of signal start a rule now? ``{kind:
        {ready, words, door}}`` — the plain words and the page that sets it up
        when it cannot (``triggers/readiness``), with ``GET /triggers``'s three
        readings folded in under ``triggers``. Never returns a secret."""
        return {"kinds": _readiness.kinds(d.platform), **_status()}

    @app.post("/triggers/calendar")
    def set_calendar_trigger(body: CalendarTriggerBody) -> dict[str, Any]:
        """Enable/disable the calendar trigger and (optionally) store its ICS URL.

        Uses the SAME persistence as PUT /settings (mutate the live config, then
        ``_persist_config`` = atomic temp + os.replace), then re-arms the live
        calendar poll loop through ``_live_rearm["calendar"]`` if the coordinator
        wired it. The ICS URL, when provided, is stored in the ENCRYPTED vault and
        is never echoed back."""
        lead = body.lead_minutes
        if lead is not None and lead < 0:
            raise HTTPException(status_code=400, detail="lead_minutes must be >= 0")
        # Redesign S2: the one writer (validated, persisted, ledger + Undo,
        # and the calendar loop re-armed live — that re-arm is the writer's).
        from ...settings.writer import SettingError
        from .settings import config_writer

        values: dict[str, Any] = {"calendar_trigger_enabled": bool(body.enabled)}
        if lead is not None:
            values["calendar_lead_minutes"] = int(lead)
        try:
            config_writer(d).apply(values, actor="calendar")
        except SettingError:
            raise HTTPException(status_code=400, detail="invalid calendar trigger settings")
        # Store the ICS URL in the vault (a secret) — only when a non-empty value
        # was supplied, so toggling enabled on/off never clobbers a stored URL.
        ics_url = (body.ics_url or "").strip()
        if ics_url:
            d.platform.secrets.set(_CALENDAR_ICS_SECRET, ics_url, kind="password")
        return _status()

    @app.delete("/triggers/calendar/url")
    def clear_calendar_url() -> dict[str, Any]:
        """Clear the stored (secret) ICS URL. Idempotent — reports whether a secret
        was actually removed, then returns fresh status."""
        removed = False
        try:
            removed = bool(d.platform.secrets.delete(_CALENDAR_ICS_SECRET))
        except Exception:  # noqa: BLE001
            removed = False
        return {"removed": removed, **_status()}


def _safe_bool(obj: Any, method: str) -> bool:
    """Call a no-arg channel predicate defensively — a channel must never make the
    status route raise."""
    try:
        fn = getattr(obj, method, None)
        return bool(fn()) if callable(fn) else False
    except Exception:  # noqa: BLE001
        return False
