"""Has the Claude Code in this pane hit its ACCOUNT's limit? (v1.303.0)

A sibling of ``agent_state`` (which says WHAT the agent is doing): this says
whether the subscription account the pane runs on has run out, so the pane can
offer "Continue on the next account". Pure, a fold over the same ANSI-stripped
tail, run in the same cached step as the classifier (once per output change).

AN ALLOW-LIST OF ACCOUNT-LIMIT SHAPES, nothing broader. The shapes are the ones
this PC's real conversations recorded (``isApiErrorMessage`` + ``error:
"rate_limit"`` records) and the prefixes Claude Code's own classifier uses::

    You've hit your session limit · resets 3:45pm (America/New_York)
    You've hit your weekly limit · resets Sep 30, 2pm (America/New_York)
    You've reached your … limit …
    You're out of usage credits. Run /usage-credits to keep using Fable 5.
    Your org is out of usage credits …
    Usage limit reached[ again] · continuing automatically {when} · esc to cancel
    Fable limit reached · continuing on <model> uses usage credits …
    /usage-credits to continue now

NOT a limit, however it reads: "Context limit reached · /compact or /clear to
continue" (the context WINDOW), "Rate limit reached" on its own, "Subagent
nesting limit reached", a watch or device limit, a sentence that merely
mentions a limit — and NEVER a ``>`` prompt line (the user typed it) or a
``⏺``/``●`` answer line (the model wrote it).

The newest match in the last :data:`SCAN_LINES` lines wins; a LATER line showing
the agent answering or working again clears it.

The matched message is the ONLY text that may reach Iron-Proxy (the rest of a
pane holds the user's prompts — the v1.301.0 codex lesson). Iron-Proxy's own
CLI classifier misses two real shapes ("out of usage credits" matches none of
its limit words; "resets Sep 30, 2pm" gives it no time), so
:meth:`PaneLimit.signal_text` normalises the line, built only from the matched
message plus the reset we parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .agent_state import _WORKING

__all__ = ["PaneLimit", "SCAN_LINES", "detect_limit", "parse_reset"]

#: How many of the newest non-empty rendered lines are searched.
SCAN_LINES = 20

#: A limit line is never longer than this.
_LINE_CAP = 300

#: The frame a message may sit in: spaces, Claude Code's ⎿ result gutter,
#: box sides, a warning mark, bullets — an ALLOW-list, which is what keeps a
#: ">"/"❯"/"›" line (a prompt the user typed) and a "⏺"/"●" line (an answer the
#: model wrote) from ever being the hit.
#:
#: WHY ⎿ STAYS ALLOWED, with its cost: Claude Code prints the account limit
#: (an API error) under the ⎿ gutter — that is where this PC's real records
#: show it — but a TOOL's output also sits under ⎿, so a command whose output
#: contains "You've hit your session limit · …" (a log being read) reads as a
#: limit while it is the last thing on screen. The very next answer or working
#: line clears it, so the false cue lives only until the agent moves on, and
#: pressing Continue then still needs a free account and says what it did.
_LEAD = r"^[\s⎿│┃║⚠✗✘×*•·\-–—]*"

_APOS = r"(?:'|’)"

_SHAPES = re.compile(
    _LEAD
    + r"(?:"
    + rf"you{_APOS}ve (?:hit|reached) your (?P<your>[\w\-. ]{{0,30}}?)\s*limit\b"
    + rf"|(?P<credits>(?:you{_APOS}re|your org(?:anization)? is) out of usage credits)\b"
    + r"|(?P<reached>usage|session|weekly|daily|monthly|5-hour|five-hour|fable|opus|sonnet|haiku)"
    r"(?: [\d.]+)? limit reached(?: again)?\s*(?:·|$)"
    + r"|(?P<now>/usage-credits to continue now)\b"
    + r")",
    re.I,
)

#: The agent moved on: an answer line (Claude Code prints ⏺ / ● before one).
_ANSWER = re.compile(r"^\s*[⏺●]\s+\S")

_FRAME_HEAD = re.compile(r"^[^\w/]+")
_FRAME_TAIL = re.compile(r"[\s│┃║|╮╯╰╭─━]+$")


@dataclass(frozen=True)
class PaneLimit:
    """The limit message on screen, its kind and its reset when it says one."""

    line: str
    reset_words: str = ""
    #: session | weekly | daily | monthly | usage | credits | model
    kind: str = "usage"
    #: The reset as an aware UTC datetime, when the words parse.
    reset_at: datetime | None = None

    def signal_text(self, now: datetime | None = None) -> str:
        """What Iron-Proxy is told: the message itself, plus — only when its
        classifier would miss it — "out of credits" and the parsed reset as
        "try again in Nm" (its parser reads that before any "resets …")."""
        text = self.line
        if self.kind == "credits" and not re.search(r"out of (?:credits|quota)", text, re.I):
            text += " · out of credits"
        if self.reset_at is not None:
            now = now or datetime.now(timezone.utc)
            minutes = max(1, int(round((self.reset_at - now).total_seconds() / 60)))
            text += f" · try again in {minutes}m"
        return text


def _kind(m: re.Match[str]) -> str:
    if m.group("credits") or m.group("now"):
        return "credits"
    what = (m.group("your") or m.group("reached") or "").lower()
    for word, kind in (("session", "session"), ("5-hour", "session"), ("five-hour", "session"),
                       ("weekly", "weekly"), ("daily", "daily"), ("monthly", "monthly"),
                       ("usage", "usage")):
        if word in what:
            return kind
    return "model" if what.strip() else "usage"


def _rendered(tail: str) -> list[str]:
    out = [raw.rstrip()[:_LINE_CAP] for raw in tail.splitlines() if raw.strip()]
    return out[-SCAN_LINES:]


_AUTO = re.compile(r"^\s*continuing automatically\s+(?P<when>.+?)\s*$", re.I)


def _reset_words(line: str) -> str:
    """The reset words of a limit line, best effort: the "·" segment that says
    when ("resets 3:45pm (America/New_York)", "at 3pm" of "continuing
    automatically at 3pm"), never the cancel hint or a model switch."""
    for part in [p.strip() for p in line.split("·")][1:]:
        low = part.lower()
        if not part or "esc to cancel" in low or low.startswith("continuing on"):
            continue
        if "/usage-credits" in low or "/compact" in low:
            continue
        m = _AUTO.match(part)
        if m:
            return m.group("when").strip()
        if low.startswith("continuing automatically"):
            continue
        return part
    return ""


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_IN = re.compile(r"\bin\s+(?:(\d+)\s*h(?:ours?|rs?)?)?\s*(?:(\d+)\s*m(?:in(?:ute)?s?)?)?", re.I)
_CLOCK = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)
_ZONE = re.compile(r"\(([A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)\)")


def parse_reset(words: str, now: datetime | None = None) -> datetime | None:
    """The reset instant (aware, UTC) of words like "resets 3:45pm
    (America/New_York)", "resets Sep 30, 2pm (…)", "at 3pm", "in 2 hours".
    The zone in brackets is honoured; without one, this PC's local zone. None
    when nothing parses. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        text = str(words or "")
        m = _IN.search(text)
        if m and (m.group(1) or m.group(2)):
            return now + timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0))
        clock = _CLOCK.search(text)
        if not clock:
            return None
        tz = None
        z = _ZONE.search(text)
        if z:
            try:
                from zoneinfo import ZoneInfo

                tz = ZoneInfo(z.group(1))
            except Exception:  # noqa: BLE001 — unknown zone: this PC's own
                tz = None
        local_now = now.astimezone(tz) if tz else now.astimezone()
        hour = int(clock.group(1)) % 12 + (12 if clock.group(3).lower() == "pm" else 0)
        minute = int(clock.group(2) or 0)
        if hour > 23 or minute > 59:
            return None
        d = _DATE.search(text)
        if d:
            month, day = _MONTHS[d.group(1).lower()[:3]], int(d.group(2))
            when = local_now.replace(month=month, day=day, hour=hour, minute=minute,
                                     second=0, microsecond=0)
            if when < local_now - timedelta(days=1):
                # "Jan 2" read on Dec 30 is next year's; a date long past is a
                # stale message, not a reset (a weekly reset is <= 7 days out).
                when = when.replace(year=when.year + 1)
                if when - local_now > timedelta(days=8):
                    return None
        else:
            when = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if when <= local_now:
                when += timedelta(days=1)
        return when.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def detect_limit(tail: str, now: datetime | None = None) -> PaneLimit | None:
    """The live account-limit message in ``tail`` (ANSI-stripped), or None."""
    lines = _rendered(tail)
    hit: int | None = None
    match: re.Match[str] | None = None
    for i in range(len(lines) - 1, -1, -1):
        m = _SHAPES.search(lines[i])
        if m:
            hit, match = i, m
            break
    if hit is None or match is None:
        return None
    for later in lines[hit + 1 :]:
        if _ANSWER.search(later) or _WORKING.search(later):
            return None  # the agent is answering or working again
    line = _FRAME_TAIL.sub("", _FRAME_HEAD.sub("", lines[hit])).strip()
    words = _reset_words(line)
    return PaneLimit(
        line=line,
        reset_words=words,
        kind=_kind(match),
        reset_at=parse_reset(words, now) if words else None,
    )
