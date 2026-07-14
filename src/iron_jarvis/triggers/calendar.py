"""Calendar trigger — a secret ICS URL that starts agent work (CX-05).

:class:`CalendarPoller` fetches a user-configured iCalendar (``.ics``) feed,
finds events coming due within the configured lead window, and fires the matching
``calendar`` reflex rules — so "a meeting starts in 15 minutes" can start a
workflow / session, the world starting work rather than the user.

SECURITY (the project ethos — the world drives the machine here, so it is hardened
by design):

* OFF BY DEFAULT / OPT-IN — :meth:`enabled` is True only when
  ``config.calendar_trigger_enabled`` is set *and* the ``calendar_ics_url`` secret
  resolves. The default install + the offline test suite never poll, never hit the
  network.
* NORMAL GATES — a fired rule runs through :class:`~iron_jarvis.reflex.router.ReflexRouter`,
  i.e. the same orchestrator + permission engine as a local user. A calendar signal
  gets no more power than a local one; nothing here bypasses a gate.
* DURABLE AT-MOST-ONCE — an event's UID is written to ``CalendarFiredRecord`` and
  committed BEFORE its rules fire, so a crash mid-fire drops the in-flight signal
  on restart rather than double-firing a world-triggered action.
* NEVER RAISE — a bad feed / malformed event is logged and skipped; one pass never
  brings the daemon down (mirrors the comm inbound poller + the reflex router).

Stdlib-only (``urllib``/``ssl`` + a tiny hand-rolled ICS parser) so it survives
the PyInstaller frozen build with no extra wheels.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import Engine, delete as sa_delete

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from .models import CalendarFiredRecord

log = get_logger("triggers.calendar")

#: URL text fetcher: ``(url) -> ics_text``. Production uses stdlib urllib; tests
#: inject a callable returning a canned feed.
HttpGet = Callable[[str], str]

#: The vault key holding the secret ICS calendar URL.
ICS_SECRET_KEY = "calendar_ics_url"

#: How long a fired-event cursor row is kept before pruning (bounds growth).
_RETENTION_DAYS = 30

#: Fetch timeout for the stdlib urllib path (seconds).
_FETCH_TIMEOUT = 15

#: Hard cap on the ICS body we read (bytes). The timeout bounds TIME, not SIZE;
#: without this a multi-GB (or hung-streaming) feed would be read fully into RAM
#: and OOM the daemon. 10 MB covers a very large real calendar with margin.
_MAX_ICS_BYTES = 10 * 1024 * 1024


class CalendarPoller:
    """Polls a secret ICS feed and fires ``calendar`` reflex rules for due events."""

    def __init__(
        self,
        platform: Any,
        reflex_router: Any,
        engine: Engine,
        *,
        http_get: HttpGet | None = None,
    ) -> None:
        self.platform = platform
        self.reflex_router = reflex_router
        self.engine = engine
        #: Injected text fetcher; when None the stdlib urllib path is used.
        self.http_get = http_get

    # -- gating ------------------------------------------------------------
    def enabled(self) -> bool:
        """True only when the feature flag is on AND a secret ICS URL is stored.

        Guards loop creation exactly like the comm inbound poller: with the flag
        off or no URL configured (the default + the test suite) nothing polls.
        """
        if not bool(getattr(self.platform.config, "calendar_trigger_enabled", False)):
            return False
        return bool(self.platform.secrets.get(ICS_SECRET_KEY))

    # -- one polling pass --------------------------------------------------
    async def poll_once(self) -> list[dict[str, Any]]:
        """Fetch the feed once and fire rules for events coming due.

        Returns a per-fired-event result list (for tests/observability). Never
        raises: a bad feed / event is logged and skipped.
        """
        if not self.enabled():
            return []

        url = self.platform.secrets.get(ICS_SECRET_KEY)
        if not url:
            return []
        try:
            # Fetch OFF the event loop — a slow/hanging feed must never stall the
            # daemon. ``to_thread`` of the (test) injected callable is deterministic.
            text = await asyncio.to_thread(self._fetch, url)
        except Exception:  # noqa: BLE001 — a fetch failure is never fatal
            log.exception("calendar trigger: fetch failed")
            return []
        if not text:
            return []
        try:
            events = _parse_vevents(text)
        except Exception:  # noqa: BLE001 — a malformed feed never brings us down
            log.exception("calendar trigger: parse failed")
            return []

        now = utcnow()
        lead = int(getattr(self.platform.config, "calendar_lead_minutes", 15) or 15)
        window_end = now + timedelta(minutes=lead)

        results: list[dict[str, Any]] = []
        for ev in events:
            uid = ev.get("uid")
            start = ev.get("start")
            if not uid or start is None:
                continue
            # Only events coming due within [now, now + lead].
            if not (now <= start <= window_end):
                continue
            # AT-MOST-ONCE: mark (insert + commit) BEFORE firing. A UID already
            # claimed (this pass' cursor, or a race) is skipped — never re-fired.
            if not self._claim(uid):
                continue
            try:
                fired = await self.reflex_router.on_calendar(
                    title=ev.get("summary", ""),
                    start=_iso(start),
                    description=ev.get("description", ""),
                )
            except Exception:  # noqa: BLE001 — one bad event never breaks the pass
                log.exception("calendar trigger: on_calendar failed for %r", uid)
                continue
            results.append(
                {
                    "uid": uid,
                    "title": ev.get("summary", ""),
                    "start": _iso(start),
                    "fired": len(fired or []),
                }
            )

        self._prune()
        return results

    # -- fetch -------------------------------------------------------------
    def _fetch(self, url: str) -> str:
        """Return the ICS text: injected fetcher if present, else stdlib urllib."""
        if self.http_get is not None:
            return self.http_get(url) or ""
        import ssl
        import urllib.request

        # Only fetch over http(s): urlopen also speaks file://, ftp://, … and the
        # URL is config — refuse a scheme that could read the local disk.
        if not url.lower().startswith(("http://", "https://")):
            log.warning("calendar trigger: refusing non-http(s) ICS url")
            return ""
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "IronJarvis"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT, context=ctx) as resp:
            # Read at most _MAX_ICS_BYTES (+1 to detect truncation) — an oversized
            # or hung-streaming feed must never OOM the daemon.
            raw = resp.read(_MAX_ICS_BYTES + 1)
        if len(raw) > _MAX_ICS_BYTES:
            log.warning("calendar trigger: ICS feed exceeds %d bytes — ignoring", _MAX_ICS_BYTES)
            return ""
        return raw.decode("utf-8", "replace")

    # -- durable cursor ----------------------------------------------------
    def _claim(self, uid: str) -> bool:
        """Insert the fired-cursor row for ``uid``; return True iff newly claimed.

        A pre-existing row (already fired, or a concurrent claim) returns False so
        the event is not re-fired. The insert + commit is the mark-before-fire step.
        """
        try:
            with session_scope(self.engine) as db:
                if db.get(CalendarFiredRecord, uid) is not None:
                    return False
                db.add(CalendarFiredRecord(event_uid=uid))
                db.commit()
                return True
        except Exception:  # noqa: BLE001 — a unique-key race / DB blip → don't fire
            log.exception("calendar trigger: could not claim event %r", uid)
            return False

    def _prune(self) -> None:
        """Delete cursor rows older than the retention window (bounds growth)."""
        cutoff = utcnow() - timedelta(days=_RETENTION_DAYS)
        try:
            with session_scope(self.engine) as db:
                db.execute(
                    sa_delete(CalendarFiredRecord).where(
                        CalendarFiredRecord.fired_at < cutoff
                    )
                )
                db.commit()
        except Exception:  # noqa: BLE001 — pruning must never fail a pass
            log.exception("calendar trigger: prune failed")


# --------------------------------------------------------------------------- #
# A tiny STDLIB iCalendar (RFC 5545) parser — just enough for VEVENT triggers.
# --------------------------------------------------------------------------- #
def _unfold(text: str) -> list[str]:
    """Undo RFC 5545 line folding: a line beginning with a space/tab continues
    the previous line. Normalises CRLF/CR to LF first."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(value: str) -> str:
    """Unescape the ICS text escapes (\\n, \\,, \\;, \\\\)."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out).strip()


def _tzid_of(params: str) -> str:
    """Extract the TZID parameter from a property name (``DTSTART;TZID=..``)."""
    for p in params.split(";")[1:]:
        if p.upper().startswith("TZID="):
            return p.split("=", 1)[1].strip()
    return ""


def _parse_dt(params: str, value: str) -> datetime | None:
    """Convert a DTSTART value to an aware UTC datetime.

    Handles the three common forms:
      * ``DTSTART:YYYYMMDDTHHMMSSZ``          (explicit UTC)
      * ``DTSTART;TZID=..:YYYYMMDDTHHMMSS``   (local — localized via stdlib
        zoneinfo, so an America/New_York event fires at the right wall-clock
        time; falls back to UTC only where no tz database is available)
      * ``DTSTART;VALUE=DATE:YYYYMMDD``       (all-day — midnight UTC)
    """
    value = value.strip()
    try:
        if "VALUE=DATE" in params.upper() or (len(value) == 8 and value.isdigit()):
            return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        if value.endswith("Z"):
            return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(
                tzinfo=timezone.utc
            )
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tzid = _tzid_of(params)
        if tzid:
            try:
                from zoneinfo import ZoneInfo

                return naive.replace(tzinfo=ZoneInfo(tzid)).astimezone(timezone.utc)
            except Exception:  # noqa: BLE001 — unknown zone / no tz db → treat as UTC
                pass
        return naive.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 — an unparseable DTSTART → skip the event
        return None


def _parse_vevents(text: str) -> list[dict[str, Any]]:
    """Split BEGIN:VEVENT..END:VEVENT blocks and read the fields we trigger on."""
    events: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in _unfold(text):
        line = raw.strip()
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            cur = {}
            continue
        if upper == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        name, _, value = line.partition(":")
        key = name.split(";", 1)[0].upper()
        if key == "UID":
            cur["uid"] = value.strip()
        elif key == "SUMMARY":
            cur["summary"] = _unescape(value)
        elif key == "DESCRIPTION":
            cur["description"] = _unescape(value)
        elif key == "LOCATION":
            cur["location"] = _unescape(value)
        elif key == "DTSTART":
            cur["start"] = _parse_dt(name, value)
    return events


def _iso(dt: datetime) -> str:
    """ISO-8601 string for the reflex ``{start}`` placeholder (never raises)."""
    try:
        return dt.isoformat()
    except Exception:  # noqa: BLE001
        return ""
