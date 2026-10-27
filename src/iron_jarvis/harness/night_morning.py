"""NIGHT SHIFT: THE MORNING (v1.399.0, phase N2 of ``docs/NIGHT-SHIFT-PLAN.md``
— the user's decision 2 of 2026-10-26).

The user wakes up to ONE "Last night" summary of the tasks Iron Jarvis ran in
the night window (``harness/night``): in the bell, on the phone when one is
set up, and as a view in Coding sessions beside Tonight. Per task: its
branch, how it ended, its tests, the files it changed, its time, its usage
per account, what needs the user — failed and needs-you first.

This module is the durable part and the words:

* THE NIGHT (:func:`night_key`): the LOCAL date the night window OPENED for a
  moment — 01:30 on the 27th belongs to the night of the 26th. A task keeps
  the night it started in (``NightTaskRecord.night``).
* THE COMMIT MESSAGE (:func:`commit_message`): the task's own words — Iron
  Jarvis commits each ended task's work to its own branch the way the Commit
  button does (``routes/build_git.commit_for_night``; never a push, never a
  merge).
* THE FACTS (:func:`usage_by_account`, :func:`summary_row`): read off the
  records — the night task (its state, notes, running time, commit, the main
  folder guard), each reply line's finish check and usage, the branch's own
  numbers (``branches.branch_summary``). NEVER a model's words.
* THE SUMMARY (:class:`NightSummaryRecord`): ONE per night, keyed by the night
  — made when the night's last task ends or the window closes, whichever
  comes first (``routes/night.morning_pass``), announced ONCE
  (``build.night_summary``: the bell, the desktop app's tray, the phone),
  the newest :data:`KEEP_NIGHTS` kept. Its rows are a snapshot: a task's row
  survives the task record being pruned from the Tonight list.

Everything here that touches the database is BLOCKING (SQLite): async callers
use ``asyncio.to_thread``. Nothing here raises into the night loop.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import utcnow

log = logging.getLogger(__name__)

#: How many nights the Last night view keeps (the newest first).
KEEP_NIGHTS = 14
#: The order rows are shown in: what needs the user first.
STATE_ORDER = ("failed", "needs_you", "running", "done", "stopped")
#: A state in the user's words (the dashboard's twin is lib/nightShift's
#: ``nightStateWords``, the ONE status table).
STATE_WORDS = {
    "failed": "Failed",
    "needs_you": "Needs you",
    "running": "Still running",
    "done": "Done",
    "stopped": "Stopped",
}
#: The note kinds that are "what needs you" on a row (the main folder change
#: has its own line, never folded).
NEEDS_KINDS = ("declined", "unanswered", "low_trust", "account")
#: v1.399.0 review: the note kinds that say why a task STOPPED (its row says
#: the first one in words).
STOP_KINDS = ("emergency", "limit", "stopped", "restart")
#: At most this many task lines in the ONE phone message.
PHONE_LINES = 10
#: One phone line, at most this long.
PHONE_LINE_CHARS = 240
#: The bell's body, at most this long (its own clip is the same).
BODY_CHARS = 140
#: Where the summary is, in the app (the phone's last line).
PLACE = "Coding sessions › Last night"


# --------------------------------------------------------------------------- #
# the night
# --------------------------------------------------------------------------- #


def _hhmm(value: str, fallback: str) -> tuple[int, int]:
    from .night_window import clean_time

    s = clean_time(value) or fallback
    h, m = s.split(":")
    return int(h), int(m)


def night_key(at: datetime, start: str, end: str = "") -> str:
    """The night ``at`` (a LOCAL time) belongs to: the date the night window
    most recently OPENED at or before it — 23:30 on the 26th and 01:30 on the
    27th are both the night of the 26th (window 23:00 → 07:00). Pure."""
    h, m = _hhmm(start, "23:00")
    opened = at.replace(hour=h, minute=m, second=0, microsecond=0)
    if opened > at:
        opened -= timedelta(days=1)
    return opened.date().isoformat()


def _parse_iso(value: Any) -> "datetime | None":
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def task_night(task: dict[str, Any], start: str, end: str = "") -> str:
    """The night a task ran in: the one it kept when it started, else (a
    task an older Iron Jarvis started) the night of its ``started_at`` in
    this PC's local time. "" for a task that never started. Pure."""
    kept = str(task.get("night") or "").strip()
    if kept:
        return kept
    began = _parse_iso(task.get("started_at"))
    if began is None:
        return ""
    return night_key(began.astimezone(), start, end)


def night_label(key: str) -> str:
    """``"2026-10-26"`` → ``"Monday, October 26"`` (the view's heading). Pure."""
    try:
        day = datetime.strptime(str(key), "%Y-%m-%d")
    except ValueError:
        return str(key or "")
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}"


# --------------------------------------------------------------------------- #
# the commit (decision 2)
# --------------------------------------------------------------------------- #

#: A commit subject, at most this long (git's own advice is 72).
SUBJECT_CHARS = 72
#: The line every night commit's message ends with (how the task ended
#: follows) — v1.399.0 review: also how a settle that runs again after a
#: crash finds the commit it already made (``build_git._night_commit_on_branch``).
TRAILER = "Committed by Iron Jarvis when this night task ended"


_CONTROL_RX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def masked(text: str) -> str:
    """``text`` with every secret-shaped value masked — the shared mask and
    the shapes it does not know (``providers.failure_words.mask_failure_text``).
    v1.399.0 review: a night task's words reach a commit message, the summary
    and the notice (the bell, the tray, the phone, every webhook and the
    daemon log). Never raises."""
    try:
        from ..providers.failure_words import mask_failure_text

        return str(mask_failure_text(str(text or "")))
    except Exception:  # noqa: BLE001 — masking never breaks the morning
        return str(text or "")


def commit_message(words: str, state: str) -> str:
    """The commit message of a night task's work: the task's OWN words — its
    first line as the subject (cut at :data:`SUBJECT_CHARS`), the rest of its
    words as the body — and one line saying Iron Jarvis committed it when the
    task ended, and how it ended (a failed or needs-you task's work is kept
    too, so nothing is lost). Bounded to ``branches.MAX_MESSAGE_CHARS``.
    Pure."""
    from .branches import MAX_MESSAGE_CHARS

    # v1.399.0 review: the words are MASKED (a key the user pasted into a
    # night task's words must not land in a commit they may push), a lone CR
    # is a line break, and control characters git refuses (a NUL ends the
    # commit with an error) or a terminal would act on are dropped.
    text = masked(str(words or "").replace("\r\n", "\n").replace("\r", "\n"))
    text = _CONTROL_RX.sub("", text).strip()
    lines = text.split("\n")
    first = next((ln.strip() for ln in lines if ln.strip()), "") or "Night task"
    subject = first if len(first) <= SUBJECT_CHARS else first[: SUBJECT_CHARS - 1].rstrip() + "…"
    body = text if (len(text) > len(first) or subject != first) else ""
    how = STATE_WORDS.get(str(state or ""), "ended").lower()
    trailer = f"{TRAILER} ({how})."
    parts = [subject]
    if body:
        parts.append(body)
    parts.append(trailer)
    message = "\n\n".join(parts)
    if len(message) > MAX_MESSAGE_CHARS:
        room = MAX_MESSAGE_CHARS - len(subject) - len(trailer) - 8
        message = "\n\n".join([subject, body[: max(0, room)].rstrip() + "…", trailer])
    return message


def committed_words(state: str, branch: str) -> str:
    """What the row says about a commit of work that did not finish (a
    failed, needs-you or stopped task): nothing is lost. "" for a done task
    (its commit is its work)."""
    if state in ("failed", "needs_you", "stopped"):
        where = f" on {branch}" if branch else " on its branch"
        return f"Its work so far is committed{where}, so nothing is lost."
    return ""


NOTHING_CHANGED = "Nothing changed, so nothing was committed."
EMERGENCY_NOT_COMMITTED = (
    "Emergency stop was on, so Iron Jarvis did not commit its work; it is still in its branch folder."
)
TRY_ON_NOTE = "It was tried on several models, each on its own branch: open the session to keep one."


def commit_note_words(branch: str, sha: str) -> str:
    """The task's note when its work was committed (Tonight's "What
    happened")."""
    where = f" on {branch}" if branch else ""
    return f"Iron Jarvis committed its work{where} ({sha[:12]}). Nothing was pushed."


# --------------------------------------------------------------------------- #
# usage per account (each reply line's own receipt)
# --------------------------------------------------------------------------- #


def _num(v: Any) -> "float | None":
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def usage_by_account(messages: list[dict[str, Any]],
                     kind_of: "Callable[[str, str], str] | None" = None) -> list[dict[str, Any]]:
    """The usage of a task's replies, per (account, provider, model) — read
    off each reply line's own receipt (``usage``, and its helpers' — they run
    on the session's model and account) and its route (``route.provider``,
    ``route.model``, ``route.account.name``). ``kind`` says how it is paid:
    ``plan`` (a subscription: its money is a list-price equivalent), ``pay``
    (billed per use) or ``free`` (on this PC or the user's network) — the
    receipt's own ``list_price_equivalent`` decides first, else ``kind_of``.
    ``cost_usd`` only when some receipt knew it. Pure."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(key: tuple[str, str, str], usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        g = groups.setdefault(key, {
            "account": key[0], "provider": key[1], "model": key[2],
            "input_tokens": 0, "output_tokens": 0, "_cost": None, "_lpe": False,
        })
        g["input_tokens"] += int(_num(usage.get("input_tokens")) or 0)
        g["output_tokens"] += int(_num(usage.get("output_tokens")) or 0)
        cost = _num(usage.get("cost_usd"))
        if cost is not None:
            g["_cost"] = (g["_cost"] or 0.0) + cost
        if usage.get("list_price_equivalent") is True:
            g["_lpe"] = True

    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        route = m.get("route") if isinstance(m.get("route"), dict) else {}
        acct = route.get("account") if isinstance(route.get("account"), dict) else {}
        key = (str(acct.get("name") or "").strip(), str(route.get("provider") or "").strip(),
               str(route.get("model") or "").strip())
        add(key, m.get("usage"))
        for h in m.get("helpers") or []:
            if isinstance(h, dict):
                add(key, h.get("usage"))
    out: list[dict[str, Any]] = []
    for g in groups.values():
        if not (g["input_tokens"] or g["output_tokens"] or g["_cost"]):
            continue
        kind = "plan" if g["_lpe"] else ""
        if not kind and kind_of is not None:
            try:
                kind = str(kind_of(g["provider"], g["model"]) or "")
            except Exception:  # noqa: BLE001 — a word, never a crash
                kind = ""
        row: dict[str, Any] = {
            "account": g["account"], "provider": g["provider"], "model": g["model"],
            "input_tokens": g["input_tokens"], "output_tokens": g["output_tokens"],
            "kind": kind if kind in ("plan", "pay", "free") else "",
        }
        if g["_cost"] is not None:
            row["cost_usd"] = round(float(g["_cost"]), 6)
        out.append(row)
    return out


def usage_words(u: dict[str, Any]) -> str:
    """One usage row in words: "12,345 tokens · ≈$0.42 at list price (your
    plan)" / "· $0.42" / "· free". Pure."""
    tokens = int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
    bits = [f"{tokens:,} tokens"]
    cost = u.get("cost_usd")
    kind = u.get("kind")
    if kind == "free":
        bits.append("free")
    elif kind == "plan":
        bits.append(f"≈${float(cost):.2f} at list price (your plan)" if isinstance(cost, (int, float)) else "on your plan")
    elif isinstance(cost, (int, float)):
        bits.append(f"${float(cost):.2f}")
    return " · ".join(bits)


# --------------------------------------------------------------------------- #
# a row of the summary
# --------------------------------------------------------------------------- #


def _first_line(text: str, cap: int = 120) -> str:
    first = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    return first if len(first) <= cap else first[: cap - 1] + "…"


def summary_row(task: dict[str, Any]) -> dict[str, Any]:
    """ONE task's row in the summary, from its record (``night.view``): its
    state and notes, its running time, its branch and commit, and the facts
    the settle kept (``morning``). Pure."""
    m = task.get("morning") if isinstance(task.get("morning"), dict) else {}
    notes = [n for n in (task.get("notes") or []) if isinstance(n, dict)]
    state = str(task.get("state") or "")
    branch = str(task.get("branch") or "")
    commit = m.get("commit") if isinstance(m.get("commit"), dict) else None
    tests = str(m.get("tests") or "")
    left_out = [x for x in (m.get("left_out") or []) if isinstance(x, dict) and x.get("path")]
    row: dict[str, Any] = {
        "id": str(task.get("id") or ""),
        "thread_id": str(task.get("thread_id") or ""),
        # v1.399.0 review: every word the row carries from the task's words or
        # its notes is MASKED (the row reaches the notice, the phone, webhooks).
        "title": masked(_first_line(task.get("words") or "")) or "Night task",
        "folder": str(task.get("folder") or ""),
        "folder_name": os.path.basename(os.path.normpath(str(task.get("folder") or ""))) or str(task.get("folder") or ""),
        "branch": branch,
        "state": state,
        "tests": tests if tests in ("passed", "failed") else "",
        "tests_trusted": m.get("tests_trusted") is not False,
        "files": int(m.get("files") or 0),
        "added": int(m.get("added") or 0),
        "removed": int(m.get("removed") or 0),
        "seconds": round(float(task.get("ran_s") or 0.0), 3),
        "usage": [u for u in (m.get("usage") or []) if isinstance(u, dict)],
        "commit": ({"sha": str(commit.get("sha") or ""), "subject": str(commit.get("subject") or "")}
                   if commit and commit.get("sha") else None),
        "commit_note": masked(str(m.get("commit_note") or "")),
        "committed_words": committed_words(state, branch) if commit and commit.get("sha") else "",
        "needs": [masked(str(n.get("text") or "")) for n in notes
                  if n.get("kind") in NEEDS_KINDS and n.get("text")],
        "main_changed": bool(task.get("main_changed")),
        "main_changed_words": next((str(n.get("text") or "") for n in notes if n.get("kind") == "outside"), ""),
        "notes": [{"kind": str(n.get("kind") or ""), "text": masked(str(n.get("text") or ""))} for n in notes],
        "try_on": len(task.get("try_on") or []),
        # v1.399.0 review: what the night's commit left out (a secret, a huge
        # file, a dependency folder) — still in the branch folder, uncommitted.
        "left_out": [{"path": str(x.get("path")), "why": str(x.get("why") or "")} for x in left_out],
        # v1.399.0 review: the session was Local only — its words and branch
        # never go to the phone or any other destination off this PC.
        "local_only": m.get("local_only") is True,
        "started_at": task.get("started_at"),
        "ended_at": task.get("ended_at"),
    }
    if row["tests"] and not row["tests_trusted"]:
        row["needs"].append("Its tests ran, but the result is not trusted: open the session to see why.")
    if left_out:
        from .night_commit import left_out_words

        row["needs"].append(left_out_words(left_out, int(m.get("left_out_count") or len(left_out))))
    if state == "stopped":
        # v1.399.0 review: a stopped row says WHY in words (Emergency stop, its
        # time or step limit, a restart, the user's Cancel) — never only a
        # state word, with the reason folded away.
        why = next((masked(str(n.get("text") or "")) for n in notes
                    if n.get("kind") in STOP_KINDS and n.get("text")), "")
        if why and why not in row["needs"]:
            row["needs"].insert(0, why)
    if state == "failed":
        err = next((str(n.get("text") or "") for n in reversed(notes) if n.get("kind") == "error"), "")
        if err:
            row["failure"] = masked(_first_line(err, 300))
    return row


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Failed and needs-you first, then still running, done, stopped; inside
    a state, the order they started in. Pure."""
    def key(r: dict[str, Any]) -> tuple[int, str, str]:
        state = str(r.get("state") or "")
        rank = STATE_ORDER.index(state) if state in STATE_ORDER else len(STATE_ORDER)
        return (rank, str(r.get("started_at") or ""), str(r.get("id") or ""))

    return sorted(rows, key=key)


def counts_of(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {s: 0 for s in STATE_ORDER}
    for r in rows:
        s = str(r.get("state") or "")
        if s in out:
            out[s] += 1
    return out


def _count_words(counts: dict[str, int]) -> list[str]:
    words = {"failed": "failed", "needs_you": "needs you", "running": "still running", "done": "done",
             "stopped": "stopped"}
    return [f"{counts[s]} {words[s]}" for s in STATE_ORDER if counts.get(s)]


def notice_words(rows: list[dict[str, Any]]) -> dict[str, str]:
    """The ONE notice's title and body (the bell's row, the desktop app's
    tray notice and the phone message's first line say exactly these):
    "Last night: 1 failed, 1 needs you, 1 done" and the tasks in that order.
    Pure."""
    ordered = sort_rows(rows)
    counts = counts_of(ordered)
    parts = _count_words(counts)
    title = "Last night: " + (", ".join(parts) if parts else "no tasks ran")
    bits = [f"{STATE_WORDS.get(str(r.get('state')), 'Ended')}: {r.get('title') or 'Night task'}" for r in ordered]
    body = " · ".join(bits)
    if len(body) > BODY_CHARS:
        body = body[: BODY_CHARS - 1] + "…"
    return {"title": title, "body": body}


def _row_detail(r: dict[str, Any]) -> str:
    state = str(r.get("state") or "")
    bits: list[str] = []
    if r.get("main_changed"):
        bits.append("Your main folder changed while it ran")
    if state == "failed" and r.get("failure"):
        bits.append(str(r["failure"]))
    if state in ("needs_you", "stopped", "failed"):
        bits.extend(str(n) for n in (r.get("needs") or [])[:1])
    if r.get("tests") == "passed":
        bits.append("tests passed" + ("" if r.get("tests_trusted", True) else " (not trusted)"))
    elif r.get("tests") == "failed":
        bits.append("tests failed")
    files = int(r.get("files") or 0)
    if files:
        bits.append(f"{files} file{'s' if files != 1 else ''} changed")
    if r.get("committed_words"):
        bits.append("its work so far is committed")
    return "; ".join(b.rstrip(".") for b in bits if b)


#: v1.399.0 review: how a Local only task is named in anything that may
#: leave this PC (the notice: the phone, a webhook) — never its words or its
#: branch (which is made from its words).
LOCAL_ONLY_TITLE = "A Local only task"
LOCAL_ONLY_LINE = "open Iron Jarvis on this PC to see it"


def shared_row(r: dict[str, Any]) -> dict[str, Any]:
    """A row as the NOTICE may carry it (it reaches the phone, every webhook
    and the daemon log): its words masked; a Local only task's words, branch
    and notes left out entirely (v1.399.0 review — "Local only stays
    absolute"). Pure."""
    out = dict(r)
    out["title"] = masked(str(r.get("title") or "")) or "Night task"
    if r.get("failure"):
        out["failure"] = masked(str(r.get("failure")))
    out["needs"] = [masked(str(n)) for n in (r.get("needs") or [])]
    if r.get("local_only"):
        out.update(title=LOCAL_ONLY_TITLE, branch="", failure="", needs=[], notes=[], commit=None,
                   committed_words="", main_changed_words="", left_out=[])
    return out


def phone_line(r: dict[str, Any]) -> str:
    """One task's short line in the phone message: its state, title, branch
    and the one thing to know. Pure."""
    head = f"• {STATE_WORDS.get(str(r.get('state')), 'Ended')}: {r.get('title') or 'Night task'}"
    if r.get("local_only"):
        return f"{head}: {LOCAL_ONLY_LINE}."
    if r.get("branch"):
        head += f" ({r['branch']})"
    detail = _row_detail(r)
    line = f"{head}: {detail}." if detail else head
    return line if len(line) <= PHONE_LINE_CHARS else line[: PHONE_LINE_CHARS - 1] + "…"


def phone_lines(rows: list[dict[str, Any]]) -> list[str]:
    ordered = sort_rows(rows)
    lines = [phone_line(r) for r in ordered[:PHONE_LINES]]
    if len(ordered) > PHONE_LINES:
        lines.append(f"…and {len(ordered) - PHONE_LINES} more.")
    return lines


def phone_message(payload: dict[str, Any]) -> str:
    """The ONE phone message of a night (``comm/notifier.format_event``):
    the notice's title, a short line per task, and where to act. Pure."""
    title = str(payload.get("title") or "Last night's tasks ended")
    lines = [str(x) for x in (payload.get("lines") or []) if isinstance(x, str) and x.strip()]
    tail = f"Open {PLACE} in Iron Jarvis to push, open a pull request or throw a branch away."
    return "\n".join([title, *lines, tail])


def notice_payload(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``build.night_summary``'s payload: the night, its label, the words the
    bell / the tray / the phone say, the counts and the phone's task lines.
    Titles and branch names only — never a path, a command or an id.
    v1.399.0 review: every word masked, and a Local only task named only as
    one (:func:`shared_row`) — this payload reaches the phone, webhooks, the
    event record and the daemon log."""
    shared = [shared_row(r) for r in rows]
    words = notice_words(shared)
    counts = counts_of(shared)
    return {
        "night": key,
        "label": night_label(key),
        "title": words["title"],
        "body": masked(words["body"]),
        "counts": counts,
        "total": len(shared),
        "lines": [masked(x) for x in phone_lines(shared)],
    }


# --------------------------------------------------------------------------- #
# the summary record (a late model, ``core.db._LATE_MODEL_MODULES``)
# --------------------------------------------------------------------------- #


class NightSummaryRecord(SQLModel, table=True):
    """ONE night's Last night summary (v1.399.0)."""

    __tablename__ = "nightsummaryrecord"

    #: The night (``night_key``): the local date its window opened.
    night: str = Field(primary_key=True)
    #: The rows, a snapshot of the records (``summary_row``), refreshed when a
    #: task of that night ends later (one still running when it was made).
    rows_json: str = ""
    ready_at: datetime = Field(default_factory=utcnow)
    #: When its ONE notice was published (None: not yet / never for an old
    #: night found after an upgrade).
    notified_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)


_LOCK = threading.Lock()


def _rows_of(rec: Any) -> list[dict[str, Any]]:
    try:
        raw = json.loads(getattr(rec, "rows_json", "") or "[]")
    except (TypeError, ValueError):
        return []
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def rows_for_night(tasks: list[dict[str, Any]], key: str, start: str, end: str = "") -> list[dict[str, Any]]:
    """The rows of the tasks (``night.view``) that ran in night ``key``. Pure."""
    return sort_rows([summary_row(t) for t in tasks
                      if t.get("state") != "queued" and task_night(t, start, end) == key])


def has_summary(engine: Any, key: str) -> bool:
    try:
        with session_scope(engine) as db:
            return db.get(NightSummaryRecord, str(key)) is not None
    except Exception:  # noqa: BLE001
        return False


def make_summary(engine: Any, key: str, rows: list[dict[str, Any]]) -> bool:
    """Make night ``key``'s summary, ONCE: True when this call made it (the
    caller then announces it), False when it already existed. BLOCKING."""
    with _LOCK, session_scope(engine) as db:
        if db.get(NightSummaryRecord, str(key)) is not None:
            return False
        now = utcnow()
        db.add(NightSummaryRecord(night=str(key), rows_json=json.dumps(sort_rows(rows)), ready_at=now,
                                  updated_at=now))
        db.commit()
        return True


def refresh_summary(engine: Any, key: str, rows: list[dict[str, Any]]) -> bool:
    """Bring a made summary's rows up to date (a task of that night that ended
    after it was made). A row whose task record is gone keeps its snapshot.
    No second notice. BLOCKING; never raises."""
    try:
        with _LOCK, session_scope(engine) as db:
            rec = db.get(NightSummaryRecord, str(key))
            if rec is None:
                return False
            fresh = {str(r.get("id")): r for r in rows}
            kept = [r for r in _rows_of(rec) if str(r.get("id")) not in fresh]
            rec.rows_json = json.dumps(sort_rows([*kept, *fresh.values()]))
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()
            return True
    except Exception:  # noqa: BLE001
        log.warning("the night summary %s could not be refreshed", key, exc_info=True)
        return False


def claim_notice(engine: Any, key: str) -> bool:
    """Mark night ``key``'s notice as sent, as one compare-and-set: True for
    the ONE caller that may announce it. BLOCKING."""
    from sqlalchemy import update

    with session_scope(engine) as db:
        res = db.exec(
            update(NightSummaryRecord)  # type: ignore[call-overload]
            .where(NightSummaryRecord.night == str(key), NightSummaryRecord.notified_at.is_(None))  # type: ignore[union-attr]
            .values(notified_at=utcnow())
        )
        db.commit()
        return bool(getattr(res, "rowcount", 0))


def rows_differ(engine: Any, key: str, rows: list[dict[str, Any]]) -> bool:
    """Does night ``key``'s made summary hold a row whose task has a
    different state now, or lack a row of a task that ran that night
    (v1.399.0 review)? BLOCKING; never raises (False when unreadable)."""
    try:
        with session_scope(engine) as db:
            rec = db.get(NightSummaryRecord, str(key))
            if rec is None:
                return False
            stored = {str(r.get("id")): str(r.get("state") or "") for r in _rows_of(rec)}
    except Exception:  # noqa: BLE001
        return False
    return any(stored.get(str(r.get("id"))) != str(r.get("state") or "") for r in rows)


def release_notice(engine: Any, key: str) -> None:
    """Give a claimed notice back (v1.399.0 review: its publish failed, so the
    next pass announces it). BLOCKING; never raises."""
    from sqlalchemy import update

    try:
        with session_scope(engine) as db:
            db.exec(
                update(NightSummaryRecord)  # type: ignore[call-overload]
                .where(NightSummaryRecord.night == str(key))
                .values(notified_at=None)
            )
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("night %s's notice could not be given back", key, exc_info=True)


def get_summary(engine: Any, key: str) -> "dict[str, Any] | None":
    with session_scope(engine) as db:
        rec = db.get(NightSummaryRecord, str(key))
        return _summary_view(rec) if rec is not None else None


def _iso(moment: Any) -> "str | None":
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _summary_view(rec: Any) -> dict[str, Any]:
    rows = sort_rows(_rows_of(rec))
    return {
        "night": rec.night,
        "label": night_label(rec.night),
        "rows": rows,
        "counts": counts_of(rows),
        "words": notice_words(rows),
        "ready_at": _iso(rec.ready_at),
        "notified": rec.notified_at is not None,
    }


def list_summaries(engine: Any, limit: int = KEEP_NIGHTS) -> list[dict[str, Any]]:
    """The made summaries, newest night first. BLOCKING."""
    with session_scope(engine) as db:
        recs = db.exec(
            select(NightSummaryRecord).order_by(NightSummaryRecord.night.desc())  # type: ignore[attr-defined]
        ).all()
        return [_summary_view(r) for r in recs[: max(1, int(limit))]]


def prune(engine: Any, keep: int = KEEP_NIGHTS) -> int:
    """Keep the newest ``keep`` nights. Returns how many went. BLOCKING;
    never raises."""
    try:
        with _LOCK, session_scope(engine) as db:
            recs = db.exec(
                select(NightSummaryRecord).order_by(NightSummaryRecord.night.desc())  # type: ignore[attr-defined]
            ).all()
            gone = list(recs[keep:])
            for rec in gone:
                db.delete(rec)
            db.commit()
            return len(gone)
    except Exception:  # noqa: BLE001
        log.warning("old night summaries could not be pruned", exc_info=True)
        return 0


__all__ = [
    "EMERGENCY_NOT_COMMITTED", "KEEP_NIGHTS", "NOTHING_CHANGED", "NightSummaryRecord", "PLACE", "STATE_ORDER",
    "STATE_WORDS", "TRY_ON_NOTE", "claim_notice", "commit_message", "commit_note_words", "committed_words",
    "counts_of", "get_summary", "has_summary", "list_summaries", "make_summary", "night_key", "night_label",
    "notice_payload", "notice_words", "phone_line", "phone_lines", "phone_message", "prune",
    "refresh_summary", "rows_for_night", "sort_rows", "summary_row", "task_night", "usage_by_account",
    "usage_words",
]
