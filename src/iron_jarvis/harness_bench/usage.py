"""What a subscription's usage windows said during a benchmark run (v1.396.0,
the long-jobs plan's Phase 7: MEASURED).

Two vendors tell an account's usage AFTER a call, in the call's own answer
(``LLMResponse.extra``):

* Claude Code — the relay's allow-listed ``anthropic-ratelimit-unified-*``
  response headers (``extra["ratelimit_headers"]``, v1.368.0): a 5-hour and a
  weekly window, each a UTILIZATION (0..1) and a RESET instant;
* Codex — the account's rate-limit snapshot (``extra["rate_limits"]``,
  v1.365.0): ``primary`` / ``secondary`` windows with ``usedPercent``,
  ``windowDurationMins`` and ``resetsAt`` (unix seconds).

:func:`readings_from` reads both exactly the way Iron-Proxy's own parser does
(``desktop/vendor/iron-proxy/iron-proxy.mjs``: ``windowsFromHeaders`` /
``limitsFromRateLimits`` — the percent rounded to a tenth, a value past 150
or below 0 dropped), so the benchmark and the account's card read one number
the same way. Nothing is GUESSED: a call whose answer carried no reading adds
none; a window with no reading is "not known", never 0%.

:func:`account_summary` keeps, per ACCOUNT and window, the FIRST and LAST
reading a run saw (and whether the window reset inside the run);
:func:`measured_span` turns the runs of one benchmark setup into the percent
of a window used across them — only inside ONE window period: a reset inside
the span (the reset instant moved, or the percent went down) makes it "not
comparable", said in words.

Honest limits, said in every report: a reading arrives AFTER its call, so the
span's first reading already includes the first call (its own share is not
in the difference); and the window is the ACCOUNT's, so any other use of the
same account in that time (another app, another session) is in it too.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

#: Iron-Proxy's own bound on a believable percent (``MAX_USED_PERCENT``).
MAX_PERCENT = 150.0
#: Claude's two windows (Iron-Proxy's ``CLAUDE_USAGE_WINDOWS``).
CLAUDE_WINDOWS: tuple[tuple[str, int], ...] = (("5h", 300), ("7d", 10080))
CLAUDE_HEADER = "anthropic-ratelimit-unified-"
#: Two readings whose reset instants differ by more than this are in two
#: DIFFERENT window periods. A vendor states a window's end to the second, but
#: a snapshot computed from "resets in N seconds" can drift by a few.
SAME_WINDOW_S = 120.0
#: A percent that goes DOWN by more than this between two readings means the
#: window reset (or rolled) in between (the readings are rounded to a tenth).
DROP_POINTS = 0.05
#: v1.396.0 review: when a Codex snapshot's own ``observedAt`` is believed
#: (Iron-Proxy's ``OBSERVED_MAX_AGE_MS`` / ``OBSERVED_MAX_SKEW_MS``).
OBSERVED_MAX_AGE_S = 30 * 24 * 3600.0
OBSERVED_MAX_SKEW_S = 60.0

#: The vendor a provider's usage is counted under in the report.
VENDOR_OF_PROVIDER: dict[str, str] = {
    "claude-cli": "claude", "anthropic": "claude",
    "codex-cli": "codex",
}
VENDOR_WORDS: dict[str, str] = {"claude": "Claude", "codex": "Codex", "local": "Local", "other": "Other"}
#: The account name a call on this PC's own sign-in is recorded under (no
#: Iron-Proxy account was in play).
THIS_PC = "this-pc"
THIS_PC_WORDS = "this PC's own sign-in"
#: Said for an account whose calls carried no reading at all.
NOT_KNOWN = "not known"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def iso(seconds: float | None) -> str | None:
    """Unix seconds → ``YYYY-MM-DDTHH:MM:SSZ`` (UTC), or None."""
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def seconds_of(text: Any) -> float | None:
    """An ISO-8601 instant (or unix seconds) → unix seconds, or None."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip()
    if not raw:
        return None
    try:
        if raw.replace(".", "", 1).isdigit():
            return float(raw)
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _header_instant(value: Any) -> float | None:
    """Iron-Proxy's ``headerInstantMs``: 9-10 digit unix seconds, else a date."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    head = raw.split(".", 1)[0]
    if head.isdigit() and 9 <= len(head) <= 10:
        try:
            return float(raw)
        except ValueError:
            return None
    return seconds_of(raw)


def _unix_seconds(value: Any) -> float | None:
    """Iron-Proxy's ``unixSecondsToIso`` range check: 1e9 <= n < 1e11."""
    n = _num(value)
    if n is None or not (1e9 <= n < 1e11):
        return None
    return n


def window_words(window: str, mins: Any = None) -> str:
    """``5-hour`` / ``weekly`` / ``<n>-minute``: a window in plain words."""
    m = _num(mins)
    if m == 300:
        return "5-hour"
    if m == 10080:
        return "weekly"
    if m is not None and m > 0:
        return f"{int(m)}-minute" if m % 60 else f"{int(m // 60)}-hour"
    return {"5h": "5-hour", "7d": "weekly"}.get(str(window), str(window or "?"))


def readings_from(extra: Any, *, at: float) -> list[dict[str, Any]]:
    """The usage-window readings ONE call's answer carried (``extra``), each
    ``{window, window_mins, percent, resets_at, at}`` (``resets_at`` ISO or
    None; ``at`` the bench's own clock when the answer arrived). Never
    raises; nothing that cannot be read is invented."""
    out: list[dict[str, Any]] = []
    if not isinstance(extra, Mapping):
        return out
    try:
        headers = extra.get("ratelimit_headers")
        if isinstance(headers, Mapping):
            h = {str(k).strip().lower(): v for k, v in headers.items()}
            for kind, mins in CLAUDE_WINDOWS:
                used = _num(h.get(f"{CLAUDE_HEADER}{kind}-utilization"))
                if used is None or used < 0:
                    continue
                percent = round(used * 100, 1)
                if percent > MAX_PERCENT:
                    continue
                out.append({
                    "window": kind, "window_mins": mins, "percent": percent,
                    "resets_at": iso(_header_instant(h.get(f"{CLAUDE_HEADER}{kind}-reset"))),
                    "at": float(at),
                })
        limits = extra.get("rate_limits")
        if isinstance(limits, Mapping):
            # v1.396.0 review: a Codex snapshot is the one its SESSION last
            # read (``account/rateLimits/read`` at start, then the updates
            # Codex sends) — a parked session's older copy can ride an answer
            # that arrives after a newer session's. Stamped with the time it
            # was SEEN (``observedAt``, Iron-Proxy's own bound: at most 30
            # days old, at most a minute ahead), an older copy sorts before
            # the newer reading instead of reading as the window going down.
            seen = _num(limits.get("observedAt"))
            when = seen if seen is not None and at - OBSERVED_MAX_AGE_S <= seen <= at + OBSERVED_MAX_SKEW_S \
                else float(at)
            for kind in ("primary", "secondary"):
                win = limits.get(kind)
                if not isinstance(win, Mapping):
                    continue
                used = _num(win.get("usedPercent"))
                if used is None or used < 0 or used > MAX_PERCENT:
                    continue
                mins = _num(win.get("windowDurationMins"))
                out.append({
                    "window": kind, "window_mins": int(round(mins)) if mins and mins > 0 else None,
                    "percent": round(used, 1),
                    "resets_at": iso(_unix_seconds(win.get("resetsAt"))),
                    "at": when,
                })
    except Exception:  # noqa: BLE001 — a reading that cannot be read is no reading
        return out
    return out


def vendor_of(provider: str, *, on_network: bool | None = None) -> str:
    """``claude`` / ``codex`` / ``local`` / ``other`` — the column a part's
    usage is counted in. ``on_network`` True wins (a model on this PC or the
    user's own network)."""
    if on_network:
        return "local"
    return VENDOR_OF_PROVIDER.get(str(provider or "").strip().lower(), "other")


def account_key(provider: str, account: str) -> str:
    return f"{provider}:{account or 'unknown'}"


def account_words(account: str, name: str = "") -> str:
    """How a report names an account: its Iron-Proxy title when known, this
    PC's own sign-in, or "an account Iron-Proxy picked" when the call named
    none."""
    if account == THIS_PC:
        return THIS_PC_WORDS
    if not account:
        return "an account not known (Iron-Proxy picked it per call)"
    return name or account


def _reset_between(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    """Why two readings (in time order) are NOT in one window period, or ""."""
    ra, rb = seconds_of(a.get("resets_at")), seconds_of(b.get("resets_at"))
    if ra is None or rb is None:
        return "a reading did not say when its window resets"
    if abs(ra - rb) > SAME_WINDOW_S:
        return f"the window reset in between (it reset at {iso(ra)}, then at {iso(rb)})"
    pa, pb = _num(a.get("percent")), _num(b.get("percent"))
    if pa is not None and pb is not None and pb < pa - DROP_POINTS:
        return f"a reading went down ({pa:g}% then {pb:g}%), so the window reset or rolled in between"
    return ""


def _ordered(readings: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows = [r for r in readings if isinstance(r, Mapping)]
    return [r for _i, r in sorted(enumerate(rows), key=lambda p: (float(p[1].get("at") or 0.0), p[0]))]


def window_summary(readings: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """One run's readings of one window → ``{window, window_mins, readings,
    first, last, one_period, why}`` (``first``/``last``: ``{at, percent,
    resets_at}``; ``one_period`` False — with ``why`` in words — when the
    readings show the window reset inside the run, or cannot show it did not);
    None when there was no reading."""
    rows = _ordered(readings)
    if not rows:
        return None
    why = ""
    for a, b in zip(rows, rows[1:]):
        why = _reset_between(a, b)
        if why:
            break
    if not why and rows[0].get("resets_at") is None:
        why = "a reading did not say when its window resets"

    def point(r: Mapping[str, Any]) -> dict[str, Any]:
        # ``at`` for reading, ``t`` (unix seconds, unrounded) to put the
        # runs of a span in order.
        return {"at": iso(_num(r.get("at"))), "t": _num(r.get("at")), "percent": r.get("percent"),
                "resets_at": r.get("resets_at")}

    first, last = rows[0], rows[-1]
    return {
        "window": str(first.get("window") or ""), "window_mins": first.get("window_mins"),
        "readings": len(rows), "first": point(first), "last": point(last),
        "one_period": not why, "why": why,
    }


def account_summary(calls: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Per ACCOUNT of one run: ``{provider, vendor, account, name, calls,
    windows: {window: window_summary}}`` from the run's ledger entries (each
    a ``ledger.CallEntry`` with ``provider``, ``account``, ``account_name``
    and ``readings``). Only subscription accounts (Claude, Codex) are kept:
    nothing else reports a window. An account whose calls carried no reading
    has ``windows == {}`` — the report reads it as "not known"."""
    out: dict[str, dict[str, Any]] = {}
    gathered: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for c in calls:
        provider = str(getattr(c, "provider", "") or "")
        vendor = vendor_of(provider)
        if vendor not in ("claude", "codex") or not getattr(c, "complete", False):
            continue
        account = str(getattr(c, "account", "") or "")
        key = account_key(provider, account)
        row = out.setdefault(key, {
            "provider": provider, "vendor": vendor, "account": account,
            "name": str(getattr(c, "account_name", "") or "") or account_words(account),
            "calls": 0, "windows": {},
        })
        row["calls"] += 1
        for reading in getattr(c, "readings", None) or ():
            if isinstance(reading, Mapping) and reading.get("window"):
                gathered.setdefault(key, {}).setdefault(str(reading["window"]), []).append(reading)
    for key, windows in gathered.items():
        for window, readings in windows.items():
            summary = window_summary(readings)
            if summary is not None:
                out[key]["windows"][window] = summary
    return out


def measured_span(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The percent of ONE window of ONE account used across a setup's runs:
    ``runs`` are that window's per-run :func:`window_summary` entries.

    ``{"state": "measured", "points", "first", "last", "readings", "runs"}``
    when every reading sits inside one window period; ``{"state":
    "not_comparable", "why"}`` when the window reset inside the span (the
    reset instant moved, a reading went down, or a reading said no reset
    instant); ``{"state": "not_known"}`` with no reading at all."""
    rows = [r for r in runs if isinstance(r, Mapping) and r.get("first") and r.get("last")]
    if not rows:
        return {"state": "not_known"}
    def started(r: Mapping[str, Any]) -> float:
        first = r.get("first") or {}
        t = _num(first.get("t"))
        return t if t is not None else (seconds_of(first.get("at")) or 0.0)

    rows.sort(key=started)
    for r in rows:
        if r.get("one_period") is False:
            return {"state": "not_comparable", "why": str(r.get("why") or "the window reset during a run")}
    points: list[Mapping[str, Any]] = []
    for r in rows:
        points += [r["first"], r["last"]]
    for a, b in zip(points, points[1:]):
        why = _reset_between(a, b)
        if why:
            return {"state": "not_comparable", "why": why}
    if points[0].get("resets_at") is None:
        return {"state": "not_comparable", "why": "a reading did not say when its window resets"}
    first, last = points[0], points[-1]
    used = (_num(last.get("percent")) or 0.0) - (_num(first.get("percent")) or 0.0)
    return {
        "state": "measured", "points": round(used, 1), "first": dict(first), "last": dict(last),
        "readings": sum(int(r.get("readings") or 0) for r in rows), "runs": len(rows),
    }
