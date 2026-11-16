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
import re
from datetime import date, datetime, time as dtime, timedelta, timezone, tzinfo
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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.platform = platform
        self.reflex_router = reflex_router
        self.engine = engine
        #: Injected text fetcher; when None the stdlib urllib path is used.
        self.http_get = http_get
        #: Injected clock (aware UTC); tests pin "now" through it.
        self.clock = clock

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

        now = self.clock() if self.clock is not None else utcnow()
        lead = int(getattr(self.platform.config, "calendar_lead_minutes", 15) or 15)
        window_end = now + timedelta(minutes=lead)

        results: list[dict[str, Any]] = []
        # Only occurrences coming due within [now, now + lead]; a repeating
        # event contributes each of its occurrences (v1.499.3).
        for key, ev, start in _due_occurrences(events, now, window_end):
            uid = ev.get("uid")
            # AT-MOST-ONCE: mark (insert + commit) BEFORE firing. A key already
            # claimed (this pass' cursor, or a race) is skipped — never re-fired.
            # A single event's key is its UID; a repeat's is UID + occurrence.
            # v1.505.0 (north-star 5.4): the mark is written off the loop and
            # awaited, so it still lands before the fire.
            if not await asyncio.to_thread(self._claim, key):
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

        await asyncio.to_thread(self._prune)  # v1.505.0: a delete, off the loop
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
            return p.split("=", 1)[1].strip().strip('"')
    return ""


#: Used only when tzlocal's own Windows table cannot be imported: Outlook and
#: Microsoft 365 feeds name zones the Windows way ("Eastern Standard Time").
_WINDOWS_ZONES_FALLBACK = {
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "FLE Standard Time": "Europe/Kiev",
    "Russian Standard Time": "Europe/Moscow",
    "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Singapore Standard Time": "Asia/Singapore",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
    "UTC": "Etc/UTC",
}

#: Each skip reason is logged once per process (a feed is polled every pass).
_SKIP_LOGGED: set[str] = set()


def _log_skip_once(key: str, message: str, *args: Any) -> None:
    if key in _SKIP_LOGGED:
        return
    if len(_SKIP_LOGGED) < 1000:
        _SKIP_LOGGED.add(key)
    log.warning(message, *args)


def _zone_for(tzid: str) -> tzinfo | None:
    """The zone a TZID names: an IANA key, or a Windows name mapped to one."""
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001 — not an IANA key; try the Windows names
        pass
    try:
        from tzlocal.windows_tz import win_tz
    except Exception:  # noqa: BLE001 — tzlocal missing: the short table
        win_tz = _WINDOWS_ZONES_FALLBACK
    iana = win_tz.get(tzid) or _WINDOWS_ZONES_FALLBACK.get(tzid)
    if not iana:
        return None
    try:
        return ZoneInfo(iana)
    except Exception:  # noqa: BLE001
        return None


def _local_parts(params: str, value: str) -> tuple[datetime, tzinfo, bool] | None:
    """``(naive wall clock, zone, all_day)`` for a DTSTART-style value, or None
    when it cannot be read. A zone that cannot be named is logged and is None:
    the old guess of UTC fired a 10:00 Eastern meeting five hours early."""
    value = value.strip()
    try:
        if "VALUE=DATE" in params.upper() or (len(value) == 8 and value.isdigit()):
            return datetime.strptime(value[:8], "%Y%m%d"), timezone.utc, True
        if value.endswith("Z"):
            return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S"), timezone.utc, False
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except Exception:  # noqa: BLE001 — an unparseable value
        return None
    tzid = _tzid_of(params)
    if not tzid:
        return naive, timezone.utc, False  # a "floating" time: read as UTC, as before
    zone = _zone_for(tzid)
    if zone is None:
        _log_skip_once(
            "tz:" + tzid,
            "calendar trigger: an event uses the time zone %r, which this app "
            "does not know, so it was skipped rather than fired at a wrong time",
            tzid,
        )
        return None
    return naive, zone, False


def _parse_dt(params: str, value: str) -> datetime | None:
    """Convert a DTSTART value to an aware UTC datetime.

    Handles the three common forms:
      * ``DTSTART:YYYYMMDDTHHMMSSZ``          (explicit UTC)
      * ``DTSTART;TZID=..:YYYYMMDDTHHMMSS``   (local — localized via stdlib
        zoneinfo, so an America/New_York event fires at the right wall-clock
        time; a Windows zone name from Outlook / Microsoft 365 is mapped to
        its IANA key; a zone that cannot be named gives None: skipped, logged)
      * ``DTSTART;VALUE=DATE:YYYYMMDD``       (all-day — midnight UTC)
    """
    parts = _local_parts(params, value)
    if parts is None:
        return None
    naive, zone, _all_day = parts
    try:
        return naive.replace(tzinfo=zone).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001 — an unconvertible DTSTART → skip the event
        return None


def _split_property(line: str) -> tuple[str, str] | None:
    """``NAME;PARAMS`` and the value: the first colon OUTSIDE double quotes
    (Outlook writes ``TZID="(UTC-05:00) Eastern Time (US & Canada)"``)."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            return line[:i], line[i + 1 :]
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
        if cur is None:
            continue
        split = _split_property(line)
        if split is None:
            continue
        name, value = split
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
            cur["_dtstart"] = (name, value.strip())
        elif key == "RRULE":
            cur["rrule"] = value.strip()
        elif key == "EXDATE":
            cur.setdefault("_exdates", []).append((name, value.strip()))
        elif key == "RECURRENCE-ID":
            cur["recurrence_id"] = _parse_dt(name, value)
        elif key in ("RDATE", "EXRULE"):
            cur["_unsupported"] = key
    return events


# --------------------------------------------------------------------------- #
# Repeating events (RRULE): the common shapes, expanded for the look-ahead.
# --------------------------------------------------------------------------- #
_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_RRULE_PARTS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL", "WKST"}
_BYDAY_RE = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
#: Periods walked for one event before it is given up on (bounds a pass).
_MAX_PERIODS = 20_000


class RuleNotUnderstood(ValueError):
    """An RRULE part this parser does not handle; the event is skipped."""


def _parse_rrule(text: str) -> dict[str, Any]:
    parts: dict[str, str] = {}
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise RuleNotUnderstood(f"cannot read {chunk!r}")
        k, v = chunk.split("=", 1)
        k, v = k.strip().upper(), v.strip().upper()
        if k not in _RRULE_PARTS:
            raise RuleNotUnderstood(f"{k} is not supported")
        parts[k] = v
    freq = parts.get("FREQ")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY"):
        raise RuleNotUnderstood(f"FREQ={freq} is not supported")
    try:
        interval = int(parts.get("INTERVAL", "1"))
        count = int(parts["COUNT"]) if "COUNT" in parts else None
    except ValueError as exc:
        raise RuleNotUnderstood(str(exc)) from exc
    if interval < 1 or (count is not None and count < 1):
        raise RuleNotUnderstood("INTERVAL and COUNT must be positive")
    if count is not None and "UNTIL" in parts:
        raise RuleNotUnderstood("COUNT and UNTIL together")
    byday: list[tuple[int | None, int]] = []
    for item in filter(None, parts.get("BYDAY", "").split(",")):
        m = _BYDAY_RE.match(item.strip())
        if not m:
            raise RuleNotUnderstood(f"BYDAY={item} is not supported")
        ordinal = int(m.group(1)) if m.group(1) else None
        if ordinal is not None and (freq != "MONTHLY" or ordinal == 0 or abs(ordinal) > 5):
            raise RuleNotUnderstood(f"BYDAY={item} is not supported with {freq}")
        byday.append((ordinal, _WEEKDAYS[m.group(2)]))
    bymonthday: list[int] = []
    for item in filter(None, parts.get("BYMONTHDAY", "").split(",")):
        try:
            day = int(item)
        except ValueError as exc:
            raise RuleNotUnderstood(f"BYMONTHDAY={item}") from exc
        if freq != "MONTHLY" or day == 0 or abs(day) > 31:
            raise RuleNotUnderstood(f"BYMONTHDAY={item} is not supported with {freq}")
        bymonthday.append(day)
    if byday and bymonthday:
        raise RuleNotUnderstood("BYDAY with BYMONTHDAY")
    wkst = parts.get("WKST", "MO")
    if wkst not in _WEEKDAYS:
        raise RuleNotUnderstood(f"WKST={wkst}")
    return {
        "freq": freq,
        "interval": interval,
        "count": count,
        "until": parts.get("UNTIL"),
        "byday": byday,
        "bymonthday": bymonthday,
        "wkst": _WEEKDAYS[wkst],
    }


def _month_days(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def _period(rule: dict[str, Any], start: datetime, p: int) -> tuple[date, list[date]]:
    """``(first day of period p, the candidate days in it, in order)``."""
    freq, step, t0 = rule["freq"], rule["interval"], start.date()
    if freq == "DAILY":
        d = t0 + timedelta(days=p * step)
        if rule["byday"] and d.weekday() not in {wd for _o, wd in rule["byday"]}:
            return d, []
        return d, [d]
    if freq == "WEEKLY":
        week0 = t0 - timedelta(days=(t0.weekday() - rule["wkst"]) % 7)
        ws = week0 + timedelta(days=7 * step * p)
        wds = {wd for _o, wd in rule["byday"]} or {t0.weekday()}
        return ws, sorted(ws + timedelta(days=(wd - rule["wkst"]) % 7) for wd in wds)
    # MONTHLY
    idx = t0.year * 12 + (t0.month - 1) + p * step
    y, m0 = divmod(idx, 12)
    m = m0 + 1
    first, ndays = date(y, m, 1), _month_days(y, m)
    out: set[date] = set()
    if rule["bymonthday"]:
        for day in rule["bymonthday"]:
            dd = day if day > 0 else ndays + day + 1
            if 1 <= dd <= ndays:
                out.add(date(y, m, dd))
    elif rule["byday"]:
        for ordinal, wd in rule["byday"]:
            hits = [
                date(y, m, d) for d in range(1, ndays + 1) if date(y, m, d).weekday() == wd
            ]
            if ordinal is None:
                out.update(hits)
            elif 0 < ordinal <= len(hits):
                out.add(hits[ordinal - 1])
            elif ordinal < 0 and -ordinal <= len(hits):
                out.add(hits[ordinal])
    elif t0.day <= ndays:
        out.add(date(y, m, t0.day))
    return first, sorted(out)


def _first_period(rule: dict[str, Any], start: datetime, local_from: datetime) -> int:
    """A period index at or before ``local_from`` (a COUNT rule starts at 0)."""
    if rule["count"] is not None or local_from <= start:
        return 0
    step = rule["interval"]
    if rule["freq"] == "DAILY":
        n = (local_from.date() - start.date()).days // step
    elif rule["freq"] == "WEEKLY":
        n = (local_from.date() - start.date()).days // (7 * step)
    else:
        months = (local_from.year - start.year) * 12 + local_from.month - start.month
        n = months // step
    return max(0, n - 1)


def _until_utc(raw: str | None, zone: tzinfo) -> datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.strptime(raw[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        if len(raw) == 8 and raw.isdigit():
            day = datetime.strptime(raw, "%Y%m%d").date()
            return (
                datetime.combine(day, dtime(23, 59, 59))
                .replace(tzinfo=zone)
                .astimezone(timezone.utc)
            )
        return (
            datetime.strptime(raw, "%Y%m%dT%H%M%S")
            .replace(tzinfo=zone)
            .astimezone(timezone.utc)
        )
    except ValueError as exc:
        raise RuleNotUnderstood(f"UNTIL={raw}") from exc


def _expand(
    ev: dict[str, Any], window_start: datetime, window_end: datetime
) -> list[datetime]:
    """The UTC starts of ``ev``'s repeats inside [window_start, window_end].

    Raises :class:`RuleNotUnderstood` for anything outside the simple shapes
    (FREQ=DAILY/WEEKLY/MONTHLY with INTERVAL, BYDAY, BYMONTHDAY, COUNT, UNTIL,
    WKST; EXDATE). Walked in the event's own zone, so a 09:00 standup stays at
    09:00 across a daylight-saving change.
    """
    if ev.get("_unsupported"):
        raise RuleNotUnderstood(f"{ev['_unsupported']} is not supported")
    params, value = ev["_dtstart"]
    parts = _local_parts(params, value)
    if parts is None:
        raise RuleNotUnderstood("its start cannot be read")
    start, zone, _all_day = parts
    rule = _parse_rrule(ev["rrule"])
    until = _until_utc(rule["until"], zone)

    ex_utc: set[datetime] = set()
    ex_days: set[date] = set()
    for ex_params, ex_value in ev.get("_exdates", []):
        for item in filter(None, (v.strip() for v in ex_value.split(","))):
            got = _local_parts(ex_params, item)
            if got is None:
                raise RuleNotUnderstood(f"EXDATE {item} cannot be read")
            naive, ex_zone, ex_all_day = got
            if ex_all_day:
                ex_days.add(naive.date())
            else:
                ex_utc.add(naive.replace(tzinfo=ex_zone).astimezone(timezone.utc))

    local_from = window_start.astimezone(zone).replace(tzinfo=None)
    first_p = _first_period(rule, start, local_from)
    seen = 0
    out: list[datetime] = []
    for p in range(first_p, first_p + _MAX_PERIODS):
        first, days = _period(rule, start, p)
        floor = datetime.combine(first, dtime(0, 0)).replace(tzinfo=zone)
        if floor.astimezone(timezone.utc) > window_end:
            return out
        for day in days:
            local = datetime.combine(day, start.time())
            if local < start:
                continue
            seen += 1
            if rule["count"] is not None and seen > rule["count"]:
                return out
            occ = local.replace(tzinfo=zone).astimezone(timezone.utc)
            if until is not None and occ > until:
                return out
            if occ > window_end:
                return out
            if occ in ex_utc or day in ex_days:
                continue
            if occ >= window_start:
                out.append(occ)
    raise RuleNotUnderstood("it repeats too many times to walk")


def _due_occurrences(
    events: list[dict[str, Any]], window_start: datetime, window_end: datetime
) -> list[tuple[str, dict[str, Any], datetime]]:
    """``(claim key, event, start)`` for every occurrence due in the window.

    A single event's key is its UID (as before); a repeat's occurrence is keyed
    UID + its start, and a moved occurrence (RECURRENCE-ID) by UID + the start
    it replaces, so the master's copy of it never fires as well. An event whose
    repeat cannot be understood is skipped and logged in words, never fired at
    a guessed time. Never raises.
    """
    moved: dict[str, set[datetime]] = {}
    for ev in events:
        if ev.get("uid") and ev.get("recurrence_id") is not None:
            moved.setdefault(ev["uid"], set()).add(ev["recurrence_id"])

    due: list[tuple[str, dict[str, Any], datetime]] = []
    for ev in events:
        uid = ev.get("uid")
        start = ev.get("start")
        if not uid or start is None:
            continue
        rid = ev.get("recurrence_id")
        if rid is not None:
            if window_start <= start <= window_end:
                due.append((f"{uid}@{rid:%Y%m%dT%H%M%SZ}", ev, start))
            continue
        if not ev.get("rrule"):
            if window_start <= start <= window_end:
                due.append((uid, ev, start))
            continue
        try:
            occurrences = _expand(ev, window_start, window_end)
        except Exception as exc:  # noqa: BLE001 — RuleNotUnderstood or a bad value
            _log_skip_once(
                "rrule:" + uid,
                "calendar trigger: the repeating event with id %r was skipped because its "
                "repeat could not be understood (%s)",
                uid,
                exc,
            )
            continue
        for occ in occurrences:
            if occ in moved.get(uid, ()):
                continue
            due.append((f"{uid}@{occ:%Y%m%dT%H%M%SZ}", ev, occ))
    return due


def _iso(dt: datetime) -> str:
    """ISO-8601 string for the reflex ``{start}`` placeholder (never raises)."""
    try:
        return dt.isoformat()
    except Exception:  # noqa: BLE001
        return ""
