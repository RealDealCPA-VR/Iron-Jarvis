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
_CALENDAR_ICS_SECRET = "calendar_ics_url"


class CalendarTriggerBody(BaseModel):
    """Configure the calendar trigger. ``enabled`` toggles the background poller;
    ``ics_url`` (when non-empty) is stored in the vault as a secret; ``lead_minutes``
    (when given) sets how early a due event fires its matching rules."""

    enabled: bool
    ics_url: str = ""
    lead_minutes: int | None = None


def register(app: FastAPI, d) -> None:
    """Attach the /triggers routes to *app*; ``d`` is the create_app deps object."""

    def _email_status() -> dict[str, Any]:
        """Email trigger readiness: any comm channel whose ``reflex_source`` is
        ``"email"`` that has opted into inbound AND resolves its credentials.

        Email triggers ride the EXISTING per-channel comm inbound toggle (there is
        no global email flag) — so "enabled" here means an email-source channel has
        ``inbound_enabled`` on, and "credentialed" means it also has_credentials()."""
        names: list[str] = []
        enabled = False
        credentialed = False
        notifier = getattr(d.platform, "notifier", None)
        if notifier is not None:
            for name in notifier.channels():
                ch = notifier.get(name)
                if ch is None or getattr(ch, "reflex_source", "comm") != "email":
                    continue
                names.append(name)
                if _safe_bool(ch, "inbound_enabled"):
                    enabled = True
                    if _safe_bool(ch, "has_credentials"):
                        credentialed = True
        return {"enabled": enabled, "credentialed": credentialed, "channels": names}

    def _slack_status() -> dict[str, Any]:
        """Slack trigger readiness: any configured Slack channel. Slack triggers
        ride the existing Slack channel/socket path — "enabled" means a Slack
        channel exists at all, "credentialed" means one can receive (a bot token
        that resolves)."""
        names: list[str] = []
        credentialed = False
        notifier = getattr(d.platform, "notifier", None)
        if notifier is not None:
            for name in notifier.channels():
                ch = notifier.get(name)
                if ch is None:
                    continue
                if getattr(ch, "reflex_source", "") == "slack" or getattr(ch, "name", "") == "slack":
                    names.append(name)
                    if _safe_bool(ch, "has_credentials"):
                        credentialed = True
        return {"enabled": bool(names), "credentialed": credentialed, "channels": names}

    def _calendar_status() -> dict[str, Any]:
        """Calendar trigger readiness. ``has_url`` reports PRESENCE only — the raw
        ICS URL is a vault secret and is NEVER returned."""
        cfg = d.platform.config
        has_url = False
        try:
            has_url = bool(d.platform.secrets.get(_CALENDAR_ICS_SECRET))
        except Exception:  # noqa: BLE001 — status must never raise
            has_url = False
        return {
            "enabled": bool(getattr(cfg, "calendar_trigger_enabled", False)),
            "has_url": has_url,
            "lead_minutes": int(getattr(cfg, "calendar_lead_minutes", 15)),
            "tick_seconds": int(getattr(cfg, "calendar_tick_seconds", 300)),
        }

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

    @app.post("/triggers/calendar")
    def set_calendar_trigger(body: CalendarTriggerBody) -> dict[str, Any]:
        """Enable/disable the calendar trigger and (optionally) store its ICS URL.

        Uses the SAME persistence as PUT /settings (mutate the live config, then
        ``_persist_config`` = atomic temp + os.replace), then re-arms the live
        calendar poll loop through ``_live_rearm["calendar"]`` if the coordinator
        wired it. The ICS URL, when provided, is stored in the ENCRYPTED vault and
        is never echoed back."""
        cfg = d.platform.config
        lead = body.lead_minutes
        if lead is not None and lead < 0:
            raise HTTPException(status_code=400, detail="lead_minutes must be >= 0")
        updated: list[str] = []
        try:
            cfg.calendar_trigger_enabled = bool(body.enabled)
            updated.append("calendar_trigger_enabled")
            if lead is not None:
                cfg.calendar_lead_minutes = int(lead)
                updated.append("calendar_lead_minutes")
        except Exception:  # noqa: BLE001 — pydantic validate_assignment rejects bad values
            raise HTTPException(status_code=400, detail="invalid calendar trigger settings")
        # Persist atomically (mirrors the settings route) so the toggle + lead time
        # survive a restart.
        d._persist_config(updated)
        # Store the ICS URL in the vault (a secret) — only when a non-empty value
        # was supplied, so toggling enabled on/off never clobbers a stored URL.
        ics_url = (body.ics_url or "").strip()
        if ics_url:
            d.platform.secrets.set(_CALENDAR_ICS_SECRET, ics_url, kind="password")
        # LIVE re-arm: hop onto the daemon loop (this sync handler runs in a
        # threadpool) and re-arm the calendar poller so the change takes effect now
        # instead of at the next restart. Guarded — the coordinator wires this.
        rearm = getattr(d, "_live_rearm", {}) or {}
        loop = rearm.get("loop")
        fn = rearm.get("calendar")
        if loop is not None and fn is not None:
            try:
                loop.call_soon_threadsafe(fn)
            except Exception:  # noqa: BLE001 — a re-arm hiccup must not fail the write
                pass
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
