"""Can each kind of signal start a rule on this PC right now? (leap 05-b)

ONE reading, used by ``GET /triggers`` (the Rules page's Triggers card and
each row's "isn't listening" line), ``GET /starts/kinds`` and chat's
``reflex_create`` (which refuses, in words and with the page that sets it up,
a rule whose signal cannot reach it yet). Read-only; never raises; never
returns a secret (the calendar's link is reported as ``has_url`` only).

The email, Slack and calendar readings moved here from
``daemon/routes/triggers.py`` word for word.
"""

from __future__ import annotations

import os
from typing import Any

#: The vault key that holds the (secret) calendar ICS URL. Kept in the
#: encrypted vault, never in config.toml, never returned in any response.
CALENDAR_ICS_SECRET = "calendar_ics_url"

#: The Notifications page (``daemon/page_doors.NOTIFICATIONS_HREF``).
NOTIFICATIONS_HREF = "/channels"
#: The calendar is set up on the Rules page, under Triggers.
CALENDAR_HREF = "/reflex"

#: Leap 05-b: what a rule of a kind that is not set up yet is told, and the
#: page that sets it up. Never "start" as a noun on screen (the master plan's
#: calm note for R2.9): "a rule started by email".
NOT_READY: dict[str, tuple[str, dict[str, str]]] = {
    "email": (
        "A rule started by email needs a mailbox that listens. Open Notifications to add one: "
        "an email destination with two-way listening on and the senders it may hear from.",
        {"href": NOTIFICATIONS_HREF, "label": "Open Notifications in Settings"},
    ),
    "calendar": (
        "A rule started by a calendar event needs your calendar's link. Open Rules and add it "
        "under Triggers, then turn the calendar on.",
        {"href": CALENDAR_HREF, "label": "Set up your calendar on Rules"},
    ),
    "slack": (
        "A rule started by a Slack message needs Slack set up to listen. Open Notifications to "
        "set it up.",
        {"href": NOTIFICATIONS_HREF, "label": "Set up Slack in Settings"},
    ),
    "comm": (
        "A rule started by a message from your phone needs a phone destination that listens. "
        "Open Notifications to turn on two-way listening for one.",
        {"href": NOTIFICATIONS_HREF, "label": "Open Notifications in Settings"},
    ),
}
#: The pages a not-ready answer may open (a door is never built from text).
NOT_READY_HREFS = frozenset(door["href"] for _words, door in NOT_READY.values())


def _safe_bool(obj: Any, method: str) -> bool:
    """Call a no-arg channel predicate defensively: a channel must never make
    a reading raise."""
    try:
        fn = getattr(obj, method, None)
        return bool(fn()) if callable(fn) else False
    except Exception:  # noqa: BLE001
        return False


def _channels(platform: Any) -> list[tuple[str, Any]]:
    notifier = getattr(platform, "notifier", None)
    if notifier is None:
        return []
    out: list[tuple[str, Any]] = []
    try:
        for name in notifier.channels():
            ch = notifier.get(name)
            if ch is not None:
                out.append((name, ch))
    except Exception:  # noqa: BLE001 — unreadable: nothing listens
        return []
    return out


def email_status(platform: Any) -> dict[str, Any]:
    """Email trigger readiness: any comm channel whose ``reflex_source`` is
    ``"email"`` that has opted into inbound AND resolves its credentials.

    Email triggers ride the EXISTING per-channel comm inbound toggle (there is
    no global email flag) — so "enabled" here means an email-source channel has
    ``inbound_enabled`` on, and "credentialed" means it also has_credentials()."""
    names: list[str] = []
    enabled = False
    credentialed = False
    for name, ch in _channels(platform):
        if getattr(ch, "reflex_source", "comm") != "email":
            continue
        names.append(name)
        if _safe_bool(ch, "inbound_enabled"):
            enabled = True
            if _safe_bool(ch, "has_credentials"):
                credentialed = True
    return {"enabled": enabled, "credentialed": credentialed, "channels": names}


def slack_status(platform: Any) -> dict[str, Any]:
    """Slack trigger readiness: any configured Slack channel. Slack triggers
    ride the existing Slack channel/socket path — "enabled" means a Slack
    channel exists at all, "credentialed" means one can receive (a bot token
    that resolves)."""
    names: list[str] = []
    credentialed = False
    for name, ch in _channels(platform):
        if getattr(ch, "reflex_source", "") == "slack" or getattr(ch, "name", "") == "slack":
            names.append(name)
            if _safe_bool(ch, "has_credentials"):
                credentialed = True
    return {"enabled": bool(names), "credentialed": credentialed, "channels": names}


def calendar_status(platform: Any) -> dict[str, Any]:
    """Calendar trigger readiness. ``has_url`` reports PRESENCE only — the raw
    ICS URL is a vault secret and is NEVER returned."""
    cfg = getattr(platform, "config", None)
    has_url = False
    try:
        has_url = bool(platform.secrets.get(CALENDAR_ICS_SECRET))
    except Exception:  # noqa: BLE001 — status must never raise
        has_url = False
    return {
        "enabled": bool(getattr(cfg, "calendar_trigger_enabled", False)),
        "has_url": has_url,
        "lead_minutes": int(getattr(cfg, "calendar_lead_minutes", 15)),
        "tick_seconds": int(getattr(cfg, "calendar_tick_seconds", 300)),
    }


def phone_status(platform: Any) -> dict[str, Any]:
    """Leap 05-b: a message from your phone can start a rule when a phone
    destination (Telegram and the like: ``reflex_source`` ``comm``) listens
    (two-way on) and can receive (its credentials resolve)."""
    names: list[str] = []
    ready = False
    for name, ch in _channels(platform):
        if getattr(ch, "reflex_source", "comm") != "comm" or not _safe_bool(ch, "inbound_enabled"):
            continue
        names.append(name)
        if _safe_bool(ch, "has_credentials"):
            ready = True
    return {"enabled": bool(names), "credentialed": ready, "channels": names}


def folder_watching_on() -> bool:
    """Leap 05-a's switch: ``IRONJARVIS_SENTINELS=off`` stops every folder
    scan on this PC (the card says so)."""
    return os.environ.get("IRONJARVIS_SENTINELS", "on").strip().lower() not in {"0", "false", "no", "off"}


def kinds(platform: Any) -> dict[str, dict[str, Any]]:
    """Leap 05-b: every kind of signal that can start a rule (plus schedules,
    which start at a time), each ``{ready, words, door}``: ``ready`` False
    with the plain words and the page that sets it up. ``file`` and
    ``download`` are always ready to be made (a folder rule's card says when
    folder watching is off; a download rule hears Your browser once it is
    paired). BLOCKING (channel reads); never raises."""
    email = email_status(platform)
    slack = slack_status(platform)
    cal = calendar_status(platform)
    phone = phone_status(platform)
    ready = {
        "schedule": True,
        "webhook": True,
        "email": bool(email["enabled"] and email["credentialed"]),
        "calendar": bool(cal["enabled"] and cal["has_url"]),
        "phone": bool(phone["enabled"] and phone["credentialed"]),
        "slack": bool(slack["enabled"] and slack["credentialed"]),
        "file": True,
        "download": True,
        "run_ended": True,
    }
    out: dict[str, dict[str, Any]] = {}
    for kind, ok in ready.items():
        wire = "comm" if kind == "phone" else kind
        row: dict[str, Any] = {"ready": ok, "words": "", "door": None}
        if not ok and wire in NOT_READY:
            words, door = NOT_READY[wire]
            row.update(words=words, door=dict(door))
        if kind == "file" and not folder_watching_on():
            row["words"] = "Folder watching is turned off on this PC, so a folder rule looks at nothing until it is on again."
        out[kind] = row
    return out


def not_ready(platform: Any, source: str) -> tuple[str, dict[str, str] | None]:
    """``("", None)`` when a rule of ``source`` (a rule's wire value: ``comm``
    for the phone) can hear its signal now; else the words and the page that
    sets it up. BLOCKING; never raises."""
    source = str(source or "")
    if source not in NOT_READY:
        return "", None
    try:
        reading = kinds(platform)
    except Exception:  # noqa: BLE001 — unreadable: never a refusal by guess
        return "", None
    row = reading.get("phone" if source == "comm" else source) or {}
    if row.get("ready", True):
        return "", None
    return str(row.get("words") or ""), (row.get("door") or None)
