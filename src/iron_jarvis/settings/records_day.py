"""Your day (next leaps 40-a, master plan R2.18, idea 40 "Rewind the day").

One view per day of every file, setting, record and memory note Iron Jarvis
changed, grouped by where the change came from (a chat, a run, a schedule, a
coding session, a settings page), each with its before and after, and what
cannot be undone (a message sent, a command run, money spent) listed FIRST.
The Activity page's "Your day" tab reads it (``GET /changes/day``), chat's
``changes_list`` with ``date`` reads the same rows, and many changes are put
back at once by ``routes.undo.undo_many`` (newest first, the run revert's
order) or chat's ``change_undo`` with ``action_ids`` / ``run``.

THE RULES IT KEEPS
* Never an Undo the journal cannot perform: a row whose before-copy is gone
  reads "can't be undone: no copy of how it was is kept any more"
  (``records_history.copy_kept``), a call that left this PC is never offered.
* Before and after is read on THIS PC, for the person's own screen
  (``GET /changes/{id}/before-after``, D-40.2), and never given to a model:
  chat's tools are told names only. A secret is named, never shown: a key's
  value is never read (only whether one is saved), a setting whose name looks
  like a secret is hidden, and every word and line passes through the shared
  mask (``detections.redact.mask``).
* A day is the LOCAL day of this PC (the dashboard and Iron Jarvis share it).
* Every function here is BLOCKING (SQLite, files): callers use
  ``asyncio.to_thread``. Every read is bounded (``DAY_ROWS``, ``CANNOT_ROWS``,
  ``TEXT_MAX``, ``DIFF_LINES``). Every pattern is linear.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from ..tools.base import Reversibility
from . import records_history as H

log = logging.getLogger("ironjarvis.settings")

#: The most journaled changes one day reads (the page says when there are more).
DAY_ROWS = 500
#: The most calls that cannot be undone one day reads.
CANNOT_ROWS = 400
#: A text file is shown as a line-by-line difference up to this size (D-40.2).
TEXT_MAX = 200 * 1024
#: The longest difference shown; the rest is said, not drawn.
DIFF_LINES = 1500
#: A memory note's text, each side, at most this many characters.
NOTE_CHARS = 20_000
#: The most files one before-and-after lists.
ITEMS_MAX = 50
#: The most changes the page puts back with one press (``POST /changes/undo``).
PAGE_MAX = 100
#: The most changes chat puts back by id with one card (``change_undo``).
CHAT_IDS_MAX = 50
#: How far from the time named ("the 3 pm run") a run may have started.
RUN_NEAR_MIN = 90

#: Calls that are no change of their own, kept out of "can't be undone": an
#: Undo (its row is the record), a helper's hand-off (the helper's own calls
#: are listed under it), the run's own worklist.
_NOT_A_CHANGE = frozenset({"change_undo", "config_undo"})

#: Where a source opens. One address per place.
CHAT_HREF = "/chat"
SETTINGS_HREF = "/settings"
CODE_HREF = "/chat?view=code"
IMPROVEMENTS_HREF = "/improvements"
#: The day view itself (the Activity page's tab, ``?view=day``).
DAY_HREF = "/activity?view=day"

_DATE_RX = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
#: "3 pm", "3:30pm", "15:00", "at 9 a.m." — bounded, linear.
_TIME_RX = re.compile(
    r"\b([01]?\d|2[0-3])(?::([0-5]\d))?\s{0,3}(am|pm|a\.m\.|p\.m\.)?(?![\w:])", re.IGNORECASE
)
_NOON_RX = re.compile(r"\b(noon|midday|midnight)\b", re.IGNORECASE)


# --------------------------------------------------------------------------
# days and clocks
# --------------------------------------------------------------------------


def today_local() -> date:
    return datetime.now().astimezone().date()


#: The first day a person may ask for (the ledger's "since ever", ``_EVER``).
#: 40-a review: this PC's clock cannot place a day near 1970 or year 9999
#: (``astimezone`` raised OSError [Errno 22] on Windows), so a day is
#: FIRST_DAY to today, and any other is refused in :data:`DAY_RULE`'s words.
FIRST_DAY = date(2000, 1, 1)
#: What a day may be, as a refusal says it.
DAY_RULE = "a day from 1 January 2000 to today, as YYYY-MM-DD (or today, or yesterday)"


def parse_day(value: Any) -> date | None:
    """A ``YYYY-MM-DD`` string (or a ``date``) as a date; "today" and
    "yesterday" too; None for anything else, and for a day before
    :data:`FIRST_DAY` or after today (40-a review). Pure but for the clock."""
    if isinstance(value, datetime):
        day: date | None = value.date()
    elif isinstance(value, date):
        day = value
    else:
        text = str(value or "").strip().lower()
        if text in ("", "today"):
            return today_local() if text == "today" else None
        if text == "yesterday":
            return today_local() - timedelta(days=1)
        m = _DATE_RX.match(text)
        if not m:
            return None
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return day if FIRST_DAY <= day <= today_local() else None


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The LOCAL day *day* as naive UTC ``[start, end)`` (the ledger's own
    form). A day that holds a clock change is 23 or 25 hours, as it should."""
    start = datetime.combine(day, dtime.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), dtime.min).astimezone()
    return (
        start.astimezone(timezone.utc).replace(tzinfo=None),
        end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _local(at: Any) -> datetime | None:
    if not isinstance(at, datetime):
        return None
    aware = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
    return aware.astimezone()


def clock(at: Any) -> str:
    """"3:02 pm" in this PC's time ("" when unknown). Pure."""
    t = _local(at)
    if t is None:
        return ""
    h12 = t.hour % 12 or 12
    return f"{h12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def day_words(day: date) -> str:
    """"Saturday 22 November"."""
    return f"{day:%A} {day.day} {day:%B}"


def _iso(at: Any) -> str | None:
    if not isinstance(at, datetime):
        return None
    aware = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
    return aware.isoformat()


# --------------------------------------------------------------------------
# where a change came from
# --------------------------------------------------------------------------


def _clip(text: Any, cap: int = 80) -> str:
    try:
        from ..history.common import title_of

        return title_of(str(text or ""))[:cap].strip()
    except Exception:  # noqa: BLE001 — fail closed: no name
        return ""


def _schedule_names(db: Any, sessions: dict[str, Any]) -> dict[str, str]:
    """A schedule's name for each run it started: the run's own report line
    (a schedule made in chat says its name there), else the schedule whose
    newest run it was. Blocking; never raises."""
    out: dict[str, str] = {}
    for sid, s in sessions.items():
        try:
            opts = json.loads(getattr(s, "options_json", "") or "{}")
        except (TypeError, ValueError):
            opts = {}
        if not isinstance(opts, dict):
            continue
        for value in opts.values():
            if isinstance(value, dict) and value.get("kind") == "schedule" and value.get("name"):
                out[sid] = str(value["name"])
                break
    missing = [sid for sid, s in sessions.items() if sid not in out and getattr(s, "origin", "") == "schedule"]
    if missing:
        try:
            from sqlmodel import select

            from ..scheduling.models import ScheduledTaskRecord

            for name, last in db.exec(
                select(ScheduledTaskRecord.name, ScheduledTaskRecord.last_session_id).where(
                    ScheduledTaskRecord.last_session_id.in_(missing)  # type: ignore[attr-defined]
                )
            ):
                if last and name:
                    out[str(last)] = str(name)
        except Exception:  # noqa: BLE001 — words only
            log.debug("your day: schedule names unread", exc_info=True)
    return out


def sources_for(db: Any, session_ids: Any, *, engine: Any = None) -> dict[str, dict[str, Any]]:
    """``{session_id: {kind, name, id, href, started, local_only}}`` — where
    each change came from, in the page's words: a chat (all chat replies are
    one source until replies are stamped, 40-b), a settings page, a coding
    session (its title), an agent run (its task's first words) or a
    schedule's run (the schedule's name). Names are masked. ``local_only``
    (40-a review): a run or coding session of Local only work
    (``records_history.local_only_sessions``, fail closed) — the person's
    page names it, a turn that may not recall Local only work is not told
    its name. Blocking; never raises."""
    from sqlmodel import select

    from ..core.models import ChatThreadRecord, Session

    ids = sorted({str(s or "") for s in (session_ids or [])})
    out: dict[str, dict[str, Any]] = {}
    build = {s: s[len("build:"):] for s in ids if s.startswith("build:")}
    plain = [s for s in ids if s and s not in ("chat", "settings", "improvements") and s not in build]
    titles: dict[str, str] = {}
    sessions: dict[str, Any] = {}
    try:
        local = H.local_only_sessions(db, engine if engine is not None else db.get_bind(), ids)
    except Exception:  # noqa: BLE001 — unknown: every run and coding session counts (fail closed)
        log.debug("your day: Local only sources unread", exc_info=True)
        local = {s for s in ids if s not in ("", "chat", "settings", "improvements")}
    try:
        if build:
            for tid, title in db.exec(
                select(ChatThreadRecord.id, ChatThreadRecord.title).where(
                    ChatThreadRecord.id.in_(sorted(set(build.values())))  # type: ignore[attr-defined]
                )
            ):
                titles[str(tid)] = str(title or "")
        if plain:
            for s in db.exec(select(Session).where(Session.id.in_(plain))):  # type: ignore[attr-defined]
                sessions[s.id] = s
    except Exception:  # noqa: BLE001 — names only; a source is still a source
        log.debug("your day: source names unread", exc_info=True)
    schedules = _schedule_names(db, sessions) if sessions else {}
    for sid in ids:
        if sid == "chat":
            out[sid] = {"kind": "chat", "name": "In chat", "id": sid, "href": CHAT_HREF, "started": None}
        elif sid == "settings":
            out[sid] = {"kind": "settings", "name": "On a settings page", "id": sid, "href": SETTINGS_HREF, "started": None}
        elif sid == "improvements":
            # The intelligence plan, release 10: an improved skill approved
            # in Improvements (or kept by the owner's own setting).
            out[sid] = {"kind": "other", "name": "In Improvements", "id": sid, "href": IMPROVEMENTS_HREF,
                        "started": None}
        elif sid in build:
            tid = build[sid]
            name = _clip(titles.get(tid, "")) or "A coding session"
            out[sid] = {
                "kind": "coding", "name": name, "id": sid,
                "href": f"{CODE_HREF}&session={quote(tid, safe='')}", "started": None,
            }
        elif sid in sessions:
            s = sessions[sid]
            started = _iso(getattr(s, "created_at", None))
            if sid in schedules:
                name = _clip(schedules[sid]) or "A schedule"
                out[sid] = {"kind": "schedule", "name": name, "id": sid,
                            "href": f"/sessions/{quote(sid, safe='')}", "started": started}
            else:
                name = _clip(getattr(s, "task", "")) or "An agent run"
                out[sid] = {"kind": "run", "name": name, "id": sid,
                            "href": f"/sessions/{quote(sid, safe='')}", "started": started}
        else:
            out[sid] = {"kind": "other", "name": "Iron Jarvis itself", "id": sid, "href": "", "started": None}
        out[sid]["local_only"] = sid in local
    return out


def source_words(source: dict[str, Any] | None) -> str:
    """"the run “Tidy the reports” (started 3:02 pm)", "chat", … Pure."""
    if not source:
        return ""
    kind, name = source.get("kind"), str(source.get("name") or "")
    start = clock(_parse_iso(source.get("started")))
    when = f" (started {start})" if start else ""
    if kind == "run":
        return f"the run “{name}”{when}"
    if kind == "schedule":
        return f"the schedule “{name}”{when}"
    if kind == "coding":
        return f"the coding session “{name}”"
    if kind == "chat":
        return "chat"
    if kind == "settings":
        return "a settings page"
    return name


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# what cannot be undone
# --------------------------------------------------------------------------


#: Built-in calls that declare no Undo yet change nothing of the person's,
#: kept out of "Can't be undone" (40-a review, G7): a read or a status check
#: (the ledger calls an undeclared tool irreversible), and Iron Jarvis's own
#: bookkeeping on this PC — its notebook, its team's notes and messages to
#: one another, work handed to a teammate (the teammate's own calls are
#: listed), a suggestion that waits for the person's yes, a look for models.
#: The Timeline still shows every call.
NOT_LISTED = frozenset({
    "blackboard_read", "pixio_status", "provider_rescan",
    "notebook", "blackboard_post", "message_agent", "assign_work",
    "memory_propose", "capability_propose",
})


def _skip_tools() -> frozenset[str]:
    try:
        from ..tools import rehearsal as _r

        return (_NOT_A_CHANGE | NOT_LISTED | _r.REHEARSAL_OWN_STATE | _r.REHEARSAL_HELPERS
                | frozenset(_r.REHEARSAL_READS))
    except Exception:  # noqa: BLE001
        return _NOT_A_CHANGE | NOT_LISTED


SENT_WHY = "it left this PC (a message sent)"
COMMAND_WHY = "a command ran on this PC, and what it did has no Undo"
KEEPS_NO_UNDO = "Iron Jarvis keeps no Undo for this kind of action"
#: 40-a review: a change whose before-copy could not be taken (the
#: registry's ``CAPTURE_FAILED_NOTE``, in the same words): it has no Undo.
NO_COPY_TAKEN_WHY = "its earlier version could not be kept"

#: Why each built-in that keeps no Undo cannot be undone (40-a review): an
#: explicit table, never a guess from the tool's name — a terminal, a
#: running program and a saved script stay on this PC.
_WHY: dict[str, str] = {
    "notify": SENT_WHY,
    "pane_send": "it was typed into a terminal on this PC, and what it did has no Undo",
    "process_write": "it was typed into a program running on this PC, and what it did has no Undo",
    "code_run": "a saved script ran on this PC, and what it did has no Undo",
    "generate_media": "it was made by a service outside this PC",
    "pixio_generate": "it was made by a service outside this PC",
    "pixio_upload": "it was sent to a service outside this PC",
    "integration_test": "it reached a service outside this PC",
    "delegate_remote": "it was sent to another computer",
    "web_action": "it acted on a web page",
}
_KIND_WHY = {
    "command": COMMAND_WHY,
    "browser": "it acted in your browser",
    "site": "it acted on a web page in its browser",
    "service": "it reached a service outside this PC",
}
#: The verbs that send: an app tool whose name STARTS with one left this
#: PC as a message ("send_message", "postMessage"; never "search_messages").
_SEND_VERBS = frozenset({"send", "post", "reply", "notify", "publish", "forward", "email", "message", "dm", "tweet"})
_WORD_RX = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _app_verb(tool: str) -> str:
    """The first word of a connected app's tool name ("send" for
    ``mcp__slack__send_message`` or ``mcp__x__sendMessage``); "" for a
    built-in. Pure; linear."""
    try:
        from ..agents.mission import _app_tool_parts

        parts = _app_tool_parts(str(tool or ""))
    except Exception:  # noqa: BLE001
        parts = None
    if parts is None:
        return ""
    words = _WORD_RX.findall(parts[1][:200])
    return words[0].lower() if words else ""


def cannot_why(tool: str) -> str:
    """Why a call that kept no Undo cannot be undone, in plain words: the
    built-in's own line (:data:`_WHY`), else by its kind (a command, the
    browser, a web service), else a connected app's tool whose name starts
    with a verb that sends; otherwise :data:`KEEPS_NO_UNDO`. Pure."""
    name = str(tool or "")
    if name in _WHY:
        return _WHY[name]
    try:
        from ..agents.mission import _step_kind

        kind = _step_kind(name)
    except Exception:  # noqa: BLE001
        kind = None
    if kind in _KIND_WHY:
        return _KIND_WHY[kind]
    if _app_verb(name) in _SEND_VERBS:
        return SENT_WHY
    return KEEPS_NO_UNDO


_ONE_RX = re.compile(r"^(\w+) 1 ([\w ]{1,40})$")


def _times(did: str, n: int) -> str:
    """"ran 1 command" said for *n* calls: "ran 37 commands"; otherwise
    "sent a message (3 times)". Pure."""
    if n <= 1:
        return did
    m = _ONE_RX.match(did)
    if m:
        return f"{m.group(1)} {n} {m.group(2)}s"
    return f"{did} ({n} times)"


def cannot_rows(
    db: Any, engine: Any, since: datetime, until: datetime | None, *, session_ids: Any = None
) -> tuple[list[dict[str, Any]], bool]:
    """The calls of ``[since, until)`` that have no Undo — what left this
    PC or ran, and what Iron Jarvis keeps no way back for
    (``reversibility == "irreversible"``, ok, not a rehearsal, not an Undo,
    not a read or Iron Jarvis's own bookkeeping: :data:`NOT_LISTED`), each
    with its true reason (:func:`cannot_why`), FOLDED by source, tool and
    reason ("ran 37 commands"), newest first: ``(rows, more)``. 40-a review:
    a change whose before-copy could not be taken (its ledger row carries
    the registry's ``CAPTURE_FAILED_NOTE`` and there is no Undo row) is here
    too, with :data:`NO_COPY_TAKEN_WHY`, so the day never leaves it off.
    Names and plain words only — never a call's arguments. Blocking;
    bounded (``CANNOT_ROWS``)."""
    from sqlalchemy import exists
    from sqlmodel import select

    from ..agents.mission import fallback_line
    from ..core.models import ToolInvocation, UndoJournal
    from ..tools.registry import CAPTURE_FAILED_NOTE

    def _bounded(stmt: Any) -> Any:
        stmt = stmt.where(ToolInvocation.created_at >= since)
        if until is not None:
            stmt = stmt.where(ToolInvocation.created_at < until)
        if session_ids is not None:
            stmt = stmt.where(ToolInvocation.session_id.in_(sorted(set(session_ids))))  # type: ignore[attr-defined]
        return stmt.order_by(ToolInvocation.created_at.desc()).limit(CANNOT_ROWS + 1)  # type: ignore[attr-defined]

    plain = _bounded(
        select(ToolInvocation)
        .where(ToolInvocation.ok == True)  # noqa: E712
        .where(ToolInvocation.undo_of == None)  # noqa: E711
        .where(ToolInvocation.rehearsed == False)  # noqa: E712
        .where(ToolInvocation.reversibility == Reversibility.IRREVERSIBLE.value)
        .where(ToolInvocation.tool.not_in(sorted(_skip_tools())))  # type: ignore[attr-defined]
    )
    no_copy = _bounded(
        select(ToolInvocation)
        .where(ToolInvocation.ok == True)  # noqa: E712
        .where(ToolInvocation.undo_of == None)  # noqa: E711
        .where(ToolInvocation.rehearsed == False)  # noqa: E712
        .where(ToolInvocation.reversibility == Reversibility.REVERSIBLE.value)
        .where(ToolInvocation.output.contains(CAPTURE_FAILED_NOTE, autoescape=True))  # type: ignore[union-attr]
        .where(~exists().where(UndoJournal.action_id == ToolInvocation.id))
    )
    lost = list(db.exec(no_copy).all())
    lost_ids = {inv.id for inv in lost}
    rows = sorted(
        list(db.exec(plain).all()) + lost,
        key=lambda inv: inv.created_at or datetime.min, reverse=True,
    )
    more = len(rows) > CANNOT_ROWS
    rows = rows[:CANNOT_ROWS]
    local = H.local_only_rows(db, engine, rows)
    folded: dict[tuple[str, str, str], dict[str, Any]] = {}
    for inv in rows:  # newest first: the first of a fold is its newest
        why = NO_COPY_TAKEN_WHY if inv.id in lost_ids else cannot_why(str(inv.tool or ""))
        key = (str(inv.session_id or ""), str(inv.tool or ""), why)
        row = folded.get(key)
        if row is None:
            did = fallback_line(str(inv.tool or ""), True)
            lt = _local(inv.created_at)
            folded[key] = {
                "id": inv.id, "ids": [inv.id], "tool": inv.tool, "did": did,
                "at": _iso(inv.created_at), "clock": clock(inv.created_at),
                "hour": lt.hour if lt is not None else None,
                "first_clock": clock(inv.created_at),
                "session_id": key[0], "state": "cannot", "why": why,
                "kind": "changed" if inv.id in lost_ids else ("sent" if why == SENT_WHY else "ran"),
                "local_work": inv.id in local,
            }
        else:
            row["ids"].append(inv.id)
            row["first_clock"] = clock(inv.created_at)
            row["local_work"] = row["local_work"] or inv.id in local
    out: list[dict[str, Any]] = []
    for row in folded.values():
        n = len(row["ids"])
        row["count"] = n
        row["what"] = _times(row.pop("did"), n)
        row["what_safe"] = row["what"] + " " + H.LOCAL_WORK_WORDS if row["local_work"] else row["what"]
        row["target"] = ""
        out.append(row)
    return out, more


# --------------------------------------------------------------------------
# what the history no longer holds (40-a review)
# --------------------------------------------------------------------------


def cleared_cutoff(home: Any, keep_days: Any, now: datetime | None = None) -> tuple[datetime | None, int | None]:
    """``(cutoff, days)``: before *cutoff* (naive UTC) the changes are no
    longer in the history, because the cleanup (``core.db.prune_events``)
    deletes a change's ledger and Undo rows together. The later of the
    "Keep activity history" setting (*keep_days*, the rule the run page's
    ``details_removed_after_days`` uses; ``days`` is then that number) and
    the newest cut-off a cleanup recorded (``core.db.history_cleared_before``:
    a cleanup run by hand, or a keep that was shorter once; ``days`` is then
    None). ``(None, None)`` when everything is kept.
    Blocking (one small read); never raises."""
    from ..core.db import history_cleared_before

    now = now or datetime.now(timezone.utc)
    try:
        days = max(0, int(keep_days or 0))
    except (TypeError, ValueError):
        days = 0
    by_setting = (now - timedelta(days=min(days, 365_000))).astimezone(timezone.utc).replace(tzinfo=None) if days else None
    by_cleanup = history_cleared_before(home)
    if by_cleanup is not None and (by_setting is None or by_cleanup > by_setting):
        return by_cleanup, None
    return by_setting, (days if by_setting is not None else None)


def _when_words(cut: datetime, day: date | None) -> str:
    """*cut* in this PC's time: "3:00 pm" inside *day*, else "Saturday 22
    November, 3:00 pm"."""
    lt = _local(cut)
    if lt is None:
        return ""
    if day is not None and lt.date() == day:
        return clock(cut)
    return f"{day_words(lt.date())}, {clock(cut)}"


def cleared_note(
    cutoff: datetime | None, days: int | None, since: datetime, until: datetime | None, *,
    day: date | None = None, listed_before: bool = False,
) -> dict[str, Any] | None:
    """What a view of ``[since, until)`` says when part or all of it is
    older than the history keeps (:func:`cleared_cutoff`): ``{days, before,
    whole, words}``, or None when all of it is kept. Its changes are GONE,
    not "none": a page or a reply that finds nothing there says this,
    never that nothing changed. ``listed_before``: some row older than the
    cut-off is still listed (the cleanup has not run since the keep was
    shortened), so the words say "may". Pure."""
    if cutoff is None or since >= cutoff:
        return None
    whole = until is not None and until <= cutoff
    if days:
        head = (f"Iron Jarvis keeps what it changed for {days} day{'s' if days != 1 else ''}, "
                "as the “Keep activity history” setting says.")
    else:
        head = f"A cleanup of the activity history removed what Iron Jarvis changed before {_when_words(cutoff, None)}."
    gone = "may no longer be listed" if listed_before else "is no longer listed"
    if whole:
        tail = ("This day is older, so " if day is not None else "This time is older, so ") + (
            f"some of what it changed then {gone}." if listed_before else f"what it changed then {gone}."
        )
    elif days or listed_before:
        tail = f"What it changed before {_when_words(cutoff, day)} {gone}."
    else:
        tail = ""  # the cleanup's own sentence already says it
    return {
        "days": days or None,
        "before": cutoff.replace(tzinfo=timezone.utc).isoformat(),
        "whole": bool(whole),
        "words": f"{head} {tail}".strip(),
    }


def _older_listed(rows: list[dict[str, Any]], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return False
    for r in rows:
        at = _parse_iso(r.get("at"))
        if at is not None and H._naive(at) < cutoff:
            return True
    return False


# --------------------------------------------------------------------------
# the day
# --------------------------------------------------------------------------


def _hour_of(row: dict[str, Any]) -> int | None:
    lt = _local(_parse_iso(row.get("at")))
    return lt.hour if lt is not None else None


def day_changes(
    engine: Any, day: date, *, home: Any = None, session_ids: Any = None, keep_days: Any = None
) -> dict[str, Any]:
    """The day *day* (local): ``{date, words, groups: [{source, rows}],
    cannot: [rows], count, more, cleared}``. ``groups`` are by where each
    change came from, the source with the newest change first, rows newest
    first, each with ``hour`` and ``clock`` (this PC's time) and
    ``has_before_after``. ``cannot`` is what left this PC or ran, listed
    first on the page. ``cleared`` (40-a review): when the day is wholly or
    partly older than the history keeps (*keep_days*, the "Keep activity
    history" setting, and the cleanup's own record), :func:`cleared_note`;
    the page then never says nothing changed. 40-a review: a call that
    failed part-way but kept its Undo (v1.504.0: the failure changed the
    file) is a change too, as in ``records_history.list_changes`` and the
    run's own Revert. Every name is kept (the person's own screen); a
    caller that tells a model uses :func:`for_model`. Blocking; bounded."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation, UndoJournal

    since, until = day_bounds(day)
    now = datetime.now(timezone.utc)
    with session_scope(engine) as db:
        stmt = (
            select(ToolInvocation, UndoJournal)
            .where(UndoJournal.action_id == ToolInvocation.id)
            .where(ToolInvocation.undo_of == None)  # noqa: E711
            .where(ToolInvocation.created_at >= since)
            .where(ToolInvocation.created_at < until)
        )
        if session_ids is not None:
            stmt = stmt.where(ToolInvocation.session_id.in_(sorted(set(session_ids))))  # type: ignore[attr-defined]
        pairs = db.exec(stmt.order_by(ToolInvocation.created_at.desc()).limit(DAY_ROWS + 1)).all()  # type: ignore[attr-defined]
        more = len(pairs) > DAY_ROWS
        pairs = [
            (inv, j) for inv, j in pairs[:DAY_ROWS]
            if (inv.reversibility or "").lower() != Reversibility.IRREVERSIBLE.value
        ]
        local = H.local_only_rows(db, engine, [inv for inv, _j in pairs])
        rows = []
        for inv, j in pairs:
            row = H.change_row(inv, j, now, local_work=inv.id in local, home=home)
            row["hour"] = _hour_of(row)
            row["clock"] = clock(inv.created_at)
            row["has_before_after"] = row["kind"] in ("file", "setting", "record", "memory")
            rows.append(row)
        cannot, more_cannot = cannot_rows(db, engine, since, until, session_ids=session_ids)
        sources = sources_for(db, {r["session_id"] for r in rows} | {c["session_id"] for c in cannot}, engine=engine)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = row["session_id"]
        g = groups.setdefault(sid, {"source": sources.get(sid) or {}, "rows": []})
        g["rows"].append(row)
    for c in cannot:
        c["source"] = sources.get(c["session_id"]) or {}
    cut, days = cleared_cutoff(home, keep_days, now)
    return {
        "date": day.isoformat(),
        "words": day_words(day),
        "today": day == today_local(),
        "groups": list(groups.values()),
        "cannot": cannot,
        "count": len(rows),
        "more": bool(more or more_cannot),
        "cleared": cleared_note(cut, days, since, until, day=day, listed_before=_older_listed(rows + cannot, cut)),
    }


def for_model(row: dict[str, Any], recall_local: bool) -> dict[str, Any]:
    """*row* (a change, or a can't-be-undone row) as a turn is told it."""
    return H._for_model(row, recall_local)


#: "Since ever" for a run's own changes (a run's ids are its own).
_EVER = datetime(2000, 1, 1)


def listing(
    engine: Any, *, hours: int = 24, kind: str = "any", limit: int = 20, day: date | None = None,
    session_ids: Any = None, home: Any = None, everything: bool = False, keep_days: Any = None,
    started: Any = None,
) -> dict[str, Any]:
    """What ``changes_list`` reads: ``{rows, total, cannot, cannot_count,
    cannot_more, sources, more, cleared}`` for the last *hours*, the day
    *day*, or (``everything``) every change of ``session_ids`` whenever it
    was made (*started*: when that run started, for ``cleared``). Rows keep
    every name; the tool tells the model through :func:`for_model`.
    ``cannot_count`` is how many calls in that time cannot be undone
    (``cannot_more``: more than that); ``cleared`` (40-a review) is
    :func:`cleared_note` when part of that time is older than the history
    keeps. Blocking; bounded."""
    from ..core.db import session_scope

    now = datetime.now(timezone.utc)
    if everything:
        rows, total = H.list_changes(engine, hours=24 * 365 * 30, kind=kind, limit=limit,
                                     session_ids=session_ids, home=home)
        since, until = _EVER, None
    else:
        rows, total = H.list_changes(engine, hours=hours, kind=kind, limit=limit, day=day,
                                     session_ids=session_ids, home=home)
        since, until = day_bounds(day) if day is not None else ((now - timedelta(hours=hours)).replace(tzinfo=None), None)
    for r in rows:
        r["clock"] = clock(_parse_iso(r.get("at")))
        r["hour"] = _hour_of(r)
    with session_scope(engine) as db:
        cannot, more = cannot_rows(db, engine, since, until, session_ids=session_ids)
        sources = sources_for(db, {r["session_id"] for r in rows} | {c["session_id"] for c in cannot}, engine=engine)
    cut, days = cleared_cutoff(home, keep_days, now)
    began = H._naive(_parse_iso(started)) if everything else since
    cleared = (
        cleared_note(cut, days, began, until, day=day, listed_before=_older_listed(rows + cannot, cut))
        if began is not None else None
    )
    return {"rows": rows, "total": total, "cannot": cannot[:20], "sources": sources,
            "cannot_count": sum(int(c.get("count") or 1) for c in cannot), "cannot_more": bool(more),
            "more": bool(more or len(cannot) > 20 or total > len(rows)), "cleared": cleared}


def many_targets(
    engine: Any, *, home: Any = None, action_ids: list[str] | None = None, run: str = "",
    day: date | None = None, recall_local: bool = False,
) -> dict[str, Any]:
    """What ONE many-at-once card names (D-40.1): ``{source, can, cannot,
    other, undone, problem}`` — ``can`` newest first (the order they are put
    back), ``cannot`` what left this PC or ran (said first), ``other`` a
    change that cannot be undone for another reason (its copy is gone, no
    such change), ``undone`` how many were already undone. By ids, or every
    change of one run and its helpers (``run``: its id or "the 3 pm run").
    ``problem`` is told to the model: with ``recall_local`` False a Local
    only run's name stays out of it. Blocking; bounded (``PAGE_MAX`` for a
    run, ``CHAT_IDS_MAX`` by id)."""
    from ..core.db import session_scope

    out: dict[str, Any] = {"source": None, "can": [], "cannot": [], "other": [], "undone": 0, "problem": ""}
    rows: list[dict[str, Any]] = []
    if run:
        source, session_ids, problem = resolve_run(engine, run, day, recall_local=recall_local)
        if problem:
            out["problem"] = problem
            return out
        out["source"] = source
        rows, total = H.list_changes(engine, hours=24 * 365 * 30, limit=PAGE_MAX + 1,
                                     session_ids=session_ids, home=home)
        with session_scope(engine) as db:
            cannot, _more = cannot_rows(db, engine, _EVER, None, session_ids=session_ids)
        out["cannot"] = cannot
        can_n = sum(1 for r in rows if r["state"] == "can")
        if can_n > PAGE_MAX or total > PAGE_MAX:
            out["problem"] = (
                f"That run made more than {PAGE_MAX} changes, too many for one card. Undo them on "
                "Activity › Your day in parts, or with Revert on the run's own page."
            )
            return out
    else:
        for aid in list(dict.fromkeys(str(a).strip() for a in (action_ids or []) if str(a).strip()))[:CHAT_IDS_MAX]:
            row = H.find_change(engine, aid, home=home)
            if row is None:
                out["other"].append({"id": aid, "target": "", "what": aid, "why": "there is no such change"})
                continue
            rows.append(row)
        rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    for r in rows:
        if r["state"] == "can":
            out["can"].append(r)
        elif r["state"] == "undone":
            out["undone"] += 1
        elif (r.get("why") or "").startswith("it left this PC"):
            out["cannot"].append(r)
        else:
            out["other"].append(r)
    return out


# --------------------------------------------------------------------------
# a run, named by its time
# --------------------------------------------------------------------------


def _time_named(words: str) -> tuple[int, int] | None:
    """The clock time in *words* ("3 pm", "15:00", "3:30pm", "noon"), as
    ``(hour, minute)``; a bare number with no am/pm and no minutes is not a
    time ("the 3 runs"). Pure; linear."""
    text = str(words or "")[:200]
    m = _NOON_RX.search(text)
    if m:
        return (0, 0) if m.group(1).lower() == "midnight" else (12, 0)
    for m in _TIME_RX.finditer(text):
        hour, minute, half = int(m.group(1)), m.group(2), (m.group(3) or "").lower().replace(".", "")
        if not half and minute is None:
            continue
        if half:
            if hour < 1 or hour > 12:
                continue
            hour = hour % 12 + (12 if half == "pm" else 0)
        return hour, int(minute or 0)
    return None


def run_family(db: Any, session_id: str) -> list[str]:
    """*session_id* and the runs of its helpers (``AgentRun.parent_id``), at
    most three levels and 50 runs. Blocking."""
    from sqlmodel import select

    from ..core.models import AgentRun

    family = [session_id]
    frontier = [session_id]
    for _depth in range(3):
        if not frontier or len(family) >= 50:
            break
        run_ids = [r for r in db.exec(select(AgentRun.id).where(AgentRun.session_id.in_(frontier)))]  # type: ignore[attr-defined]
        if not run_ids:
            break
        kids = [
            s for s in db.exec(select(AgentRun.session_id).where(AgentRun.parent_id.in_(run_ids)))  # type: ignore[union-attr]
            if s and s not in family
        ]
        frontier = list(dict.fromkeys(kids))[: 50 - len(family)]
        family.extend(frontier)
    return family


def _named_runs(db: Any, rows: list[Any]) -> list[tuple[str, Any]]:
    """The day's runs a person names by the time they started (40-a
    review): a helper's run (``AgentRun.parent_id``) is part of its lead's
    and never "the 3 pm run" itself, and a rehearsal changed nothing.
    ``rows`` are ``(id, created_at, options_json)``. Blocking."""
    from sqlmodel import select

    from ..core.models import AgentRun

    ids = [str(sid) for sid, _at, _o in rows]
    helpers: set[str] = set()
    if ids:
        helpers = {
            str(s) for s in db.exec(
                select(AgentRun.session_id)
                .where(AgentRun.session_id.in_(ids))  # type: ignore[attr-defined]
                .where(AgentRun.parent_id != None)  # noqa: E711
            ) if s
        }
    out: list[tuple[str, Any]] = []
    for sid, at, opts in rows:
        if str(sid) in helpers:
            continue
        try:
            o = json.loads(opts or "{}")
        except (TypeError, ValueError):
            o = {}
        if isinstance(o, dict) and o.get("rehearsal_id"):
            continue
        out.append((str(sid), at))
    return out


def resolve_run(
    engine: Any, ref: Any, day: date | None = None, *, recall_local: bool = False
) -> tuple[dict[str, Any] | None, list[str], str]:
    """``(source, session_ids, problem)`` for a run named by its id or by
    the time it started ("the 3 pm run": the run of *day*, default today,
    that started nearest that time, within ``RUN_NEAR_MIN`` minutes; never
    a helper's run or a rehearsal). ``session_ids`` is the run and its
    helpers. ``problem`` is the sentence when there is no such run (naming
    the day's runs); it is told to a model, so with ``recall_local`` False
    (the default: fail closed) a Local only run is "a Local only run",
    never its name (40-a review). Blocking."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import Session

    words = " ".join(str(ref or "").split())[:200]
    if not words:
        return None, [], "Say which run: its time (\"the 3 pm run\") or its id."
    with session_scope(engine) as db:
        if words.startswith("build:") or db.get(Session, words) is not None:
            src = sources_for(db, [words], engine=engine).get(words)
            return src, (run_family(db, words) if not words.startswith("build:") else [words]), ""
        if words.lower() in ("chat", "settings"):
            return None, [], "That is not one run. Name a run by its time, or the changes by their ids."
        named = _time_named(words)
        day = day or today_local()
        since, until = day_bounds(day)
        runs = _named_runs(db, db.exec(
            select(Session.id, Session.created_at, Session.options_json)
            .where(Session.created_at >= since)
            .where(Session.created_at < until)
            .order_by(Session.created_at)  # type: ignore[arg-type]
            .limit(500)
        ).all())
        if named is None:
            return None, [], _no_run_words(
                db, engine, runs, day, "Say when it started (\"the 3 pm run\") or give its id.", recall_local
            )
        want = datetime.combine(day, dtime(named[0], named[1])).astimezone()
        want_utc = want.astimezone(timezone.utc).replace(tzinfo=None)
        best, gap = None, None
        for sid, at in runs:
            if at is None:
                continue
            at = H._naive(at)
            d = abs((at - want_utc).total_seconds())
            if gap is None or d < gap:
                best, gap = sid, d
        if best is None or gap is None or gap > RUN_NEAR_MIN * 60:
            return None, [], _no_run_words(
                db, engine, runs, day, f"No run started near {clock(want)} on {day_words(day)}.", recall_local
            )
        src = sources_for(db, [best], engine=engine).get(best)
        return src, run_family(db, best), ""


#: A Local only run, as a turn that may not recall Local only work is told it.
LOCAL_RUN_WORDS = "a Local only run"


def _no_run_words(db: Any, engine: Any, runs: list[Any], day: date, head: str, recall_local: bool) -> str:
    """*head*, then the day's runs by name and time (the last eight) — a
    Local only run named only as :data:`LOCAL_RUN_WORDS` unless
    *recall_local* (the sentence is told to a model). Blocking."""
    if not runs:
        return f"{head} No run started on {day_words(day)}."
    srcs = sources_for(db, [sid for sid, _at in runs[-8:]], engine=engine)
    listed = ", ".join(
        (f"{LOCAL_RUN_WORDS} at {clock(at)}" if srcs[sid].get("local_only") and not recall_local
         else f"“{srcs[sid]['name']}” at {clock(at)}")
        for sid, at in runs[-8:] if sid in srcs
    )
    return f"{head} Runs that day: {listed}."


# --------------------------------------------------------------------------
# many at once: the card's words and the result's
# --------------------------------------------------------------------------

#: The longest name a card or a reply says (a longer one keeps its start
#: and its end, :func:`short_name`).
NAME_CHARS = 80
#: The longest reason a card says (every reason in this module is shorter).
WHY_CHARS = 120
#: The most things that cannot be undone a card lists by name ("and 5 more").
CARD_CANNOT = 20
#: The longest card (D-40.1: every change named). The card is NEVER cut
#: (40-a review: a cut list undid changes the card never named). Sized for
#: the run path's worst case, at most about 13,200 characters: ``PAGE_MAX`` names
#: of ``NAME_CHARS`` (undone or "Not undone", folded by reason),
#: ``CARD_CANNOT`` things that cannot be undone with names and reasons at
#: their caps, and the run's name (pinned by
#: ``test_the_card_fits_its_worst_case_whole``). A card that would still be
#: longer is not shown: the call is refused in :func:`too_many_to_name`'s
#: words and nothing is undone.
CARD_CHARS = 16_000

_NOUNS = {"file": ("file", "files"), "setting": ("setting", "settings"), "record": ("record", "records"),
          "memory": ("memory note", "memory notes")}


def _noun(kind: str, n: int) -> str:
    one, many = _NOUNS.get(kind, ("change", "changes"))
    return one if n == 1 else many


def short_name(name: Any, cap: int = NAME_CHARS) -> str:
    """*name* in at most *cap* characters: a longer one keeps its first
    third and its end ("Projects/2026/…/014 planting notes.txt"), the end
    being where a file's own name is. Pure."""
    text = str(name or "")
    if len(text) <= cap:
        return text
    head = cap // 3
    return text[:head] + "…" + text[len(text) - (cap - head - 1):]


def names_of(rows: list[dict[str, Any]], *, safe: bool = False) -> list[str]:
    """Each change by name, in order, a name repeated folded ("ledger.txt
    (3 changes)"); a long name said in :data:`NAME_CHARS`
    (:func:`short_name`). Names fold only when they are the SAME name, never
    because their short forms match (40-a review). ``safe``: the Local only
    names a model may not read are said as what was done. Pure."""
    seen: dict[str, int] = {}
    order: list[str] = []
    for r in rows:
        if safe and r.get("local_work"):
            name = str(r.get("what_safe") or r.get("what") or "")
        else:
            name = str(r.get("target") or "") or str(r.get("what") or "")
        if name not in seen:
            order.append(name)
            seen[name] = 0
        seen[name] += 1
    return [f"{short_name(n)} ({seen[n]} changes)" if seen[n] > 1 else short_name(n) for n in order]


def card_words(
    can: list[dict[str, Any]], cannot: list[dict[str, Any]], *, source: dict[str, Any] | None = None,
    undone: int = 0, other: list[dict[str, Any]] | None = None,
) -> str:
    """The ONE card for many changes (D-40.1): what cannot be undone FIRST,
    with why; then every change it will undo, by name; then what cannot be
    undone for another reason ("Not undone: a.txt, b.txt (why)." — one line
    per reason) and how many were already undone. NEVER cut (40-a review):
    a caller whose card is longer than :data:`CARD_CHARS` shows no card at
    all and undoes nothing (:func:`too_many_to_name`). Pure."""
    parts: list[str] = []
    if cannot:
        listed = "; ".join(
            f"{short_name(c.get('what'))} ({short_name(c.get('why'), WHY_CHARS)})" for c in cannot[:CARD_CANNOT]
        )
        more = f"; and {len(cannot) - CARD_CANNOT} more" if len(cannot) > CARD_CANNOT else ""
        parts.append(f"Can't be undone, so they stay as they are: {listed}{more}.")
    n = len(can)
    src = source_words(source)
    parts.append(
        f"Undo {'this change' if n == 1 else f'these {n} changes'}{f' from {src}' if src else ''}: "
        + ", ".join(names_of(can)) + "."
    )
    by_why: dict[str, list[dict[str, Any]]] = {}
    for r in other or []:
        by_why.setdefault(short_name(r.get("why") or "it could not be undone", WHY_CHARS), []).append(r)
    for why, rows in by_why.items():
        parts.append(f"Not undone: {', '.join(names_of(rows))} ({why}).")
    if undone:
        parts.append(f"{undone} {'was' if undone == 1 else 'were'} already undone.")
    return " ".join(parts)


def too_many_to_name(n: int) -> str:
    """The refusal when one card cannot name every change (never a cut
    card). Pure."""
    return (
        f"One card cannot name all {n} of these changes, so nothing was undone. Undo them on "
        "Activity › Your day in parts, or ask for fewer at once."
    )


def plain_skip(detail: Any) -> str:
    """Why one change was not put back, in plain words (the Undo's own
    refusal otherwise). Pure."""
    text = " ".join(str(detail or "").split())
    low = text.lower()
    if "changed since" in low or "newer change" in low:
        return "it was changed again since, and putting it back would lose that newer change"
    if "already undone" in low:
        return "it was already undone"
    if low == "unknown action":
        return "there is no such change"
    if "pre-image unavailable" in low or "no longer kept" in low:
        return H.NO_COPY_KEPT
    return text[:200] or "it could not be undone"


def put_back_words(reverted: list[dict[str, Any]], skipped: list[dict[str, Any]], *, safe: bool = False) -> str:
    """"12 files put back." then each change that was not, by name, with
    why. ``safe``: Local only names withheld (a model reads it). Pure."""
    n = len(reverted)
    kinds = Counter(str(r.get("kind") or "other") for r in reverted)
    if n == 0:
        head = "Nothing was put back."
    elif len(kinds) == 1:
        (kind,) = kinds
        head = f"{n} {_noun(kind, n)} put back."
    else:
        head = f"{n} changes put back: " + ", ".join(f"{c} {_noun(k, c)}" for k, c in kinds.most_common()) + "."
    tails = not_put_back_words(skipped, safe=safe)
    return f"{head} {tails}" if tails else head


def not_put_back_words(skipped: list[dict[str, Any]], *, safe: bool = False) -> str:
    """"Not put back: report.txt (why)." for each of *skipped* ("" for
    none). ``safe``: Local only names withheld. Pure."""
    tails = []
    for s in skipped:
        (name,) = names_of([s], safe=safe) or ["a change"]
        tails.append(f"Not put back: {name} ({s.get('reason') or 'it could not be undone'}).")
    return " ".join(tails)


# --------------------------------------------------------------------------
# before and after (D-40.2: on this PC only, never given to a model)
# --------------------------------------------------------------------------


def _mask_line(line: str) -> str:
    try:
        from ..detections.redact import mask

        return mask(line)
    except Exception:  # noqa: BLE001 — fail closed: nothing shown
        return ""


def _text_of(data: bytes | None) -> str | None:
    if data is None or len(data) > TEXT_MAX or b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _side(data: bytes | None, *, exists: bool, path: Path | None = None, kept: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": bool(exists), "kept": bool(kept)}
    if data is not None:
        out["size"] = len(data)
    elif path is not None and exists:
        try:
            out["size"] = path.stat().st_size
        except OSError:
            pass
    if path is not None and exists:
        try:
            out["modified"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            pass
    return out


def _read_now(path: Path | None) -> tuple[bytes | None, bool]:
    """``(bytes, exists)`` of the file as it is now: the bytes only when it
    is small enough to show and not under a protected folder. Blocking."""
    if path is None:
        return None, False
    try:
        from ..core.fs_policy import is_protected_path

        if is_protected_path(path):
            return None, path.exists()
    except Exception:  # noqa: BLE001 — unknown: shown as there, not read
        return None, path.exists()
    try:
        if not path.is_file():
            return None, path.exists()
        if path.stat().st_size > TEXT_MAX:
            return None, True
        return path.read_bytes(), True
    except OSError:
        return None, path.exists()


def _diff(before: str | None, now: str | None, name: str) -> tuple[list[str], bool]:
    """A line-by-line difference, each line masked; ``(lines, cut)``."""
    if before is None and now is None:
        return [], False
    lines = list(
        difflib.unified_diff(
            (before or "").splitlines(), (now or "").splitlines(),
            fromfile=f"{name} (before)", tofile=f"{name} (now)", lineterm="", n=3,
        )
    )
    cut = len(lines) > DIFF_LINES
    return [_mask_line(x[:2000]) for x in lines[:DIFF_LINES]], cut


def _file_item(
    name: str, before: bytes | None, before_exists: bool, before_kept: bool, path: Path | None,
    *, note_text: bool = False,
) -> dict[str, Any]:
    now_bytes, now_exists = _read_now(path)
    item: dict[str, Any] = {
        "name": name,
        "before": _side(before, exists=before_exists, kept=before_kept),
        "now": _side(now_bytes, exists=now_exists, path=path),
    }
    tb = _text_of(before) if before_exists else ""
    tn = _text_of(now_bytes) if now_exists else ""
    if (tb is None and before_exists) or (tn is None and now_exists):
        item["text"] = False  # not text, or too big: size and time only
        return item
    item["text"] = True
    item["diff"], item["cut"] = _diff(tb, tn, name)
    if note_text:
        item["before"]["text"] = _mask_line((tb or "")[:NOTE_CHARS]) if before_exists else ""
        item["now"]["text"] = _mask_line((tn or "")[:NOTE_CHARS]) if now_exists else ""
    return item


def _workspace(platform: Any, meta: dict[str, Any], session_ws: str, session_id: str) -> Path:
    """The folder the change's relative path was written against — the
    Undo's own order (``routes.undo.perform_undo``): the envelope's stamp,
    else the run's folder, else the old reconstruction."""
    raw = meta.get("workspace") if isinstance(meta, dict) else None
    if isinstance(raw, str) and raw and Path(raw).is_absolute():
        return Path(raw)
    return Path(session_ws) if session_ws else Path(platform.config.workspaces_dir) / session_id


def _in_workspace(ws: Path, rel: str) -> Path | None:
    try:
        from ..tools.base import safe_path

        return safe_path(ws, rel)
    except Exception:  # noqa: BLE001 — outside its folder: never read
        return None


def _value_words(key: str, value: Any) -> str:
    """A setting's value in words: an option's label, on / off, "not set";
    a secret-looking key hidden. Masked, bounded. Pure."""
    from ..core.config import is_secret_config_key

    if is_secret_config_key(key):
        return "hidden (it looks like a secret)"
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "on" if value else "off"
    try:
        from . import schema

        d = schema.get(key)
        for v, label in d.options:
            if v == value:
                return label
    except Exception:  # noqa: BLE001 — the value's own words
        pass
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return H._masked(text, 200) or "empty"


def _setting_label(key: str) -> str:
    try:
        from . import schema

        d = schema.get(key)
        return f"{d.label}: {schema.pattern_arg(d, key)}" if d.pattern else d.label
    except Exception:  # noqa: BLE001
        return key


def _record_words(platform: Any, record: str, name: str, snap: Any) -> str:
    """A record's snapshot in its card's words; "" when this kind has none."""
    from . import records as R

    describers: dict[str, Callable[[Any], Any]] = {
        "schedule": R._describe_schedule, "workflow": R._describe_workflow,
        "channel": R._describe_channel, "webhook": R._describe_webhook,
        "sentinel": R._describe_sentinel, "goal": R._describe_goal, "reflex": R._describe_reflex,
        "goal_contract": H._describe_contract,
    }
    try:
        from . import records_projects as RP

        describers["project"] = RP._describe_project
        describers["agent"] = RP._describe_agent
    except Exception:  # noqa: BLE001
        pass
    if record == "chat_local_only" and isinstance(snap, dict):
        return "Local only on" if snap.get("local_only") else "Local only off"
    fn = describers.get(record)
    if snap is None:
        return "not there"
    if fn is None:
        return "there"
    try:
        words = fn(snap)
    except Exception:  # noqa: BLE001
        words = None
    return H._masked(words, 300) if words else "there"


def before_after(platform: Any, action_id: str, *, current_setting: Callable[..., Any] | None = None) -> dict[str, Any] | None:
    """One change's before and after, by kind (D-40.2): a file's before-copy
    beside the file as it is now (a line-by-line difference for a text file
    up to ``TEXT_MAX``, else size and time), a setting's earlier value and
    its value now (a secret-looking one hidden), a key named and never shown,
    a record in its card's words, a memory note's text. None when there is
    no such change. For the person's screen only. Blocking; bounded."""
    from ..core.db import session_scope
    from ..core.models import Session, ToolInvocation, UndoJournal
    from ..tools import undo as U

    home = platform.config.home
    with session_scope(platform.engine) as db:
        inv = db.get(ToolInvocation, action_id)
        if inv is None or inv.undo_of is not None:
            return None
        journal = db.get(UndoJournal, action_id)
        session = db.get(Session, inv.session_id) if inv.session_id else None
        row = H.change_row(inv, journal, home=home)
        desc = (
            {"kind": journal.kind, "pre_ref": journal.pre_ref, "pre_inline": journal.pre_inline}
            if journal is not None else None
        )
        session_ws = getattr(session, "workspace_path", "") if session is not None else ""
        left = (inv.reversibility or "").lower() == Reversibility.IRREVERSIBLE.value
    out: dict[str, Any] = {"id": action_id, "kind": row["kind"], "what": row["what"], "state": row["state"],
                           "why": row["why"], "items": [], "note": ""}
    if desc is None or left:
        # A call that left this PC or ran is never shown as a change, even
        # when an old row kept a journal entry for it.
        out["note"] = "Iron Jarvis kept no copy of how it was, so there is no before to show."
        return out
    meta = U.read_envelope(desc)
    meta = meta if isinstance(meta, dict) else {}
    jkind = str(desc.get("kind") or "")
    ws = _workspace(platform, meta, str(session_ws or ""), str(inv.session_id or ""))
    items: list[dict[str, Any]] = []
    if jkind in ("file_restore", "file_delete") or (jkind.startswith("pdf") and isinstance(meta.get("path"), str)):
        rel = str(meta.get("path") or "")
        path = _in_workspace(ws, rel) if rel else None
        before, kept = None, True
        if jkind != "file_delete":
            try:
                before = U.resolve_prior_bytes(home, desc, meta)
            except (OSError, ValueError):
                before, kept = None, False
            if before is None:
                kept = False
        items.append(_file_item(Path(rel).name or rel, before, jkind != "file_delete", kept, path))
    elif jkind == "files_delete":
        for p in [str(x) for x in (meta.get("paths") or []) if isinstance(x, str)][:ITEMS_MAX]:
            items.append(_file_item(Path(p).name or p, None, False, True, _in_workspace(ws, p)))
    elif jkind == "file_rename":
        origin, rel = str(meta.get("rename_from") or ""), str(meta.get("path") or "")
        here = _in_workspace(ws, rel) if rel else None
        items.append({
            "name": Path(rel).name or rel, "text": False,
            "before": {"exists": True, "kept": True, "words": f"named {Path(origin).name or origin}"},
            "now": {"exists": bool(here and here.exists()), "kept": True,
                    "words": f"named {Path(rel).name or rel}" if here and here.exists() else "not there any more"},
        })
    elif jkind == U.PATCH_UNDO_KIND:
        for entry in U.patch_entries(meta)[:ITEMS_MAX]:
            rel = str(entry.get("path") or "")
            try:
                before = U.patch_entry_before(home, entry)
                kept = True
            except (OSError, ValueError):
                before, kept = None, False
            existed = bool(entry.get("existed", before is not None))
            items.append(_file_item(Path(rel).name or rel, before, existed, kept or not existed, _in_workspace(ws, rel)))
    elif jkind in ("memory_restore", "memory_delete_file"):
        raw = str(meta.get("path") or "")
        path = Path(raw) if raw and Path(raw).is_absolute() else None
        before, kept = None, True
        if jkind == "memory_restore":
            try:
                before = U.resolve_prior_bytes(home, desc, meta)
            except (OSError, ValueError):
                before = None
            kept = before is not None
        items.append(_file_item(path.name if path else "a memory note", before, jkind == "memory_restore", kept,
                                path, note_text=True))
    elif jkind == "skill_restore":
        # The intelligence plan, release 10: an improved skill's text before
        # the approval, beside its text now.
        raw = str(meta.get("path") or "")
        path = Path(raw) if raw and Path(raw).is_absolute() else None
        existed = meta.get("existed") is not False
        before, kept = None, True
        if existed:
            try:
                before = U.resolve_prior_bytes(home, desc, meta)
            except (OSError, ValueError):
                before = None
            kept = before is not None
        items.append(_file_item(f"the skill {meta.get('name') or ''}".strip(), before, existed, kept, path,
                                note_text=True))
    elif jkind == "setting_restore":
        prior = meta.get("prior") if isinstance(meta.get("prior"), dict) else {}
        device = str(meta.get("device_id") or "")
        for key in sorted(prior)[:ITEMS_MAX]:
            try:
                now_value = current_setting(key, device_id=device) if current_setting else getattr(platform.config, key, None)
            except Exception:  # noqa: BLE001 — unknown now
                now_value = None
            items.append({
                "name": _setting_label(key), "text": False,
                "before": {"exists": True, "kept": True, "words": _value_words(key, prior.get(key))},
                "now": {"exists": True, "kept": True, "words": _value_words(key, now_value)},
            })
    elif jkind == "secret_restore":
        name = str(meta.get("secret") or "a key")
        vault = str(meta.get("vault") or "")
        try:
            saved = bool(vault) and bool(platform.secrets.exists(vault))
        except Exception:  # noqa: BLE001
            saved = None
        items.append({
            "name": f"the key {name}", "text": False,
            "before": {"exists": True, "kept": True,
                       "words": "a value was saved (never shown)" if meta.get("had_prior") else "no value"},
            "now": {"exists": True, "kept": True,
                    "words": "not known" if saved is None else ("a value is saved (never shown)" if saved else "no value")},
        })
    elif jkind == "record_restore":
        from . import records as R

        record, name = str(meta.get("record") or ""), str(meta.get("name") or "")
        try:
            snap = R._SNAP[record](platform, name) if record in R._SNAP else None
            now_words = _record_words(platform, record, name, snap)
        except Exception:  # noqa: BLE001
            now_words = "not known"
        items.append({
            "name": H._target_words(jkind, desc.get("pre_inline")) or "a record", "text": False,
            "before": {"exists": True, "kept": True, "words": _record_words(platform, record, name, meta.get("prior"))},
            "now": {"exists": True, "kept": True, "words": now_words},
        })
    else:
        out["note"] = (
            "Iron Jarvis can put this back, but cannot show its before and after here."
            if row["state"] == "can" else "Iron Jarvis cannot show its before and after here."
        )
    out["items"] = items
    return out


__all__ = [
    "CARD_CHARS",
    "CHAT_IDS_MAX",
    "DAY_HREF",
    "DAY_RULE",
    "FIRST_DAY",
    "NO_COPY_TAKEN_WHY",
    "PAGE_MAX",
    "before_after",
    "cannot_rows",
    "cannot_why",
    "card_words",
    "clock",
    "day_bounds",
    "day_changes",
    "day_words",
    "for_model",
    "names_of",
    "not_put_back_words",
    "parse_day",
    "plain_skip",
    "put_back_words",
    "resolve_run",
    "run_family",
    "short_name",
    "source_words",
    "sources_for",
    "today_local",
    "too_many_to_name",
]
