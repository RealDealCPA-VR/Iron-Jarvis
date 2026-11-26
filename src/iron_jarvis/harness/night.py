"""NIGHT SHIFT: THE QUEUE AND SAFE UNATTENDED RUNS (v1.398.0, phase N1 of
``docs/NIGHT-SHIFT-PLAN.md`` — the user's decisions 1, 3 and 4 of 2026-10-26).

The user queues coding tasks in Coding sessions › **Tonight**. Inside the
night window (``harness/night_window``) the night loop (``daemon/routes/
night``) starts each one as a NEW coding session on its OWN branch, at most
``night_max_at_once`` at a time and never two in one folder. This module is
the durable part and the rules every night task runs under:

* :class:`NightTaskRecord` — one queued task (a late model,
  ``core.db._LATE_MODEL_MODULES``): the folder, the user's words, optionally
  a model, "the team" or "try on" models, its state (``queued`` →
  ``running`` → ``done`` | ``failed`` | ``needs_you`` | ``stopped``), its
  session and turn, and NOTES — what the morning needs to know (a step left
  for the user, low trust, the time limit, the environment its tests used).
* THE POSTURE (decision 4, :func:`posture_setup`): a night turn edits files
  and runs commands inside its own branch folder WITHOUT ASKING — "Approve
  for me" plus a grant for the command tools, so the folder's own checks and
  the project's checkers run too — and its reply is capped at the task's time
  limit (the existing ``setup.cap`` minutes). EVERYTHING ELSE ASKS
  (:data:`RUNS_UNASKED`, review: the first cut ran "Don't ask", which granted
  the web, the agent's browser and a folder rule's "ask" too). It is applied
  PER TURN, never stored on the session: the morning's own messages in that
  session run under the user's own posture again. The safety floors are not
  postures and still ask: a commit or push by the agent, a project's own
  command lines, a risky page click, spending, Local only.
* THE WAIT (decision 1, :class:`NightTurn`): a question in a night turn waits
  ``night_card_wait_minutes`` (the phone gets it meanwhile when a phone
  channel is set up, v1.392.0's rules), then THAT step is declined — a safety
  card exactly the same way, never allowed — the model is told in plain words
  ("left for the user: …") and carries on with the rest, and a ``declined``
  note keeps it for the morning. Armed as a ContextVar by
  ``routes/build.start_build_turn`` while the turn's driver task is created,
  like the account pin, so the stream lane's card wait, the model's own
  question (``ask_user``), an app's question and a helper's ask all see it.
  Every other session is untouched (no NightTurn = byte-identical).
* WHO STARTED A TURN (:func:`note_unattended_turn`): a coding-session turn
  Iron Jarvis started BY ITSELF — a night task, "Resume when ready", a move
  within an account group, a reply carried on after a restart — is listed
  here while it runs, so Emergency stop (``Orchestrator.stop_unattended``)
  stops every one of them; a turn a person started is never listed.

Everything here that touches the database is BLOCKING (SQLite): async callers
use ``asyncio.to_thread``. Nothing here raises into a turn.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from .reply_end import CUT_WORDS, cut_short

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
NEEDS_YOU = "needs_you"
STOPPED = "stopped"
STATES = (QUEUED, RUNNING, DONE, FAILED, NEEDS_YOU, STOPPED)
#: A state the task can no longer leave by itself.
ENDED = (DONE, FAILED, NEEDS_YOU, STOPPED)

#: The user's words for one task (a message's own limit is 200,000; a queued
#: task is a paragraph or a page).
MAX_WORDS = 20_000
#: Notes kept on one task (the newest win), each at most this long.
MAX_NOTES = 40
MAX_NOTE_CHARS = 400
#: How many tasks the list keeps (queued + running + ended), the oldest
#: ended ones dropped first.
MAX_TASKS = 200
#: Ended tasks the list still shows for this long (the morning reads them).
ENDED_SHOWN_DAYS = 3

#: The note kinds (the morning's sections, N2).
NOTE_KINDS = ("declined", "unanswered", "low_trust", "limit", "env", "error", "stopped", "emergency",
              "restart", "account", "tests", "info", "outside",
              # v1.399.0 (N2): what Iron Jarvis's own commit of the task's
              # work did (``harness/night_morning``).
              "commit",
              # v1.513.0 (second review T6): points of the request the task's
              # own finish check left open (``outcome_of``) — filed as "info"
              # before, so a "Needs you" row carried no reason. T7: a task
              # that ended without changing anything (``routes/night``).
              "unmet", "did_nothing",
              # Third plan NA1 (decision G10): a task tried on several models
              # whose attempts finished — the user chooses one
              # (``outcome_of`` from ``attempts.card_reading``).
              "attempts")

#: The command tools the night posture grants for the turn (decision 4):
#: "runs commands inside its own branch folder without asking" — the model's
#: own commands AND the folder's checks and the project's checkers (each of
#: which needs a grant wider than one call, ``finish.needs_a_person``).
COMMAND_TOOLS = ("shell", "process_start", "process_write")
#: The night turn's posture: "Approve for me" (decision 4's own words) — NOT
#: "Don't ask" (review): ``yolo`` granted EVERY ask the turn could meet,
#: including a folder rule's "ask" and every tool outside the coding set. The
#: posture is the base; :data:`RUNS_UNASKED` is the night's own allow-list on
#: top of it, and the safety floors still ask.
POSTURE = "approve_for_me"
#: THE NIGHT ALLOW-LIST (review of v1.398.0): the ONLY tools a night turn
#: runs WITHOUT ASKING — reading and editing files in its own branch folder,
#: its commands, its step list, reading a skill, and the helpers on its team
#: (a helper works with this turn's tools under these same rules,
#: ``chat_turn.helper_parent_now``). Every OTHER tool the turn holds asks —
#: the web (``web_fetch``, ``web_search``: the main way a page's words reach
#: the model, and a request out of the PC), the agent's browser (``page_*``),
#: a connected app's resources, anything an app adds — whatever its own tier
#: or the session's posture says, past every grant (tab, standing,
#: conversation), so nobody watching means it is declined after the card wait
#: and lands under "needs you". ``ask_user`` asks the user in its own way (its
#: own night wait, ``daemon/mcp_turn``).
RUNS_UNASKED = frozenset({
    "read_file", "glob", "grep", "list_files", "view_image",
    "write_file", "edit_file", "multi_edit", "apply_patch", "notebook_edit",
    *COMMAND_TOOLS, "process_read", "process_stop",
    "plan_update", "skill_search", "skill_load",
    "delegate", "spawn_agent", "list_agents",
    "ask_user",
})


def asks_at_night(tool: str) -> bool:
    """Does a call of ``tool`` (a REAL tool name) ask in a night turn,
    whatever the posture and the grants say? Pure."""
    return str(tool or "") not in RUNS_UNASKED


class NightTaskRecord(SQLModel, table=True):
    """One task queued for the night (v1.398.0)."""

    __tablename__ = "nighttaskrecord"

    id: str = Field(default_factory=lambda: new_id("night"), primary_key=True)
    folder: str = ""
    words: str = ""
    #: The model to run on ("" = the folder's / the app's default).
    provider: str = ""
    model: str = ""
    #: "The team": the session's model may hand pieces to the user's model
    #: team by role (v1.395.0).
    team: bool = False
    #: v1.424.0 (D6): the Iron-Proxy account the task's session runs on (""
    #: = its vendor's Default, as before). Checked when queued and again when
    #: it starts (``iron_proxy.choice``); additive — NULL on an older table
    #: reads as "".
    account: str = ""
    #: "Try on" (v1.397.0 attempts): ``[{provider, model}]``, 2 or 3.
    try_on_json: str = ""
    #: The order queued tasks start in (smallest first).
    position: int = Field(default=0, index=True)
    state: str = Field(default=QUEUED, index=True)
    #: The coding session (its thread id) and the first turn, once started.
    thread_id: str = Field(default="", index=True)
    turn_id: str = ""
    branch: str = ""
    #: ``[{kind, text, at}]``, newest last, bounded.
    notes_json: str = ""
    #: v1.398.0 review: the user's MAIN checkout as it was when the task
    #: started (``harness/night_guard.snapshot``: digests and refs, never
    #: file contents), compared when it ends.
    main_before_json: str = ""
    #: v1.398.0 review: the running time of the task's turns that ended, in
    #: seconds — its time limit is the TASK's, across every turn.
    ran_s: float = 0.0
    #: v1.399.0 (N2, the morning): the night this task ran in — the LOCAL
    #: date its night window opened (``night_morning.night_key``), written
    #: when it starts. "" (NULL on an upgraded table) on a task an older Iron
    #: Jarvis started: derived from ``started_at`` then.
    night: str = ""
    #: v1.399.0: the commit Iron Jarvis made of the task's work on its own
    #: branch when it ended (decision 2), "" when none.
    commit_sha: str = ""
    #: v1.399.0: what the morning summary reads about the task, written when
    #: it ends (``night_morning``): tests, files changed, usage per account,
    #: the commit — read off its records, never a model's words.
    morning_json: str = ""
    #: Idea 39 (39-a): the saved chat this was set up from; each result is
    #: written there as a REPORT (``agents/chat_delivery.report_to_chat``).
    #: "" = it reports nowhere (made on a page, or Stop reporting).
    #: Additive; NULL on an older table reads as "". Indexed (the plan;
    #: ``core.db._HOT_INDEXES`` gives an older table the same index).
    chat_thread_id: str = Field(default="", index=True)
    #: Next leaps 12-a (R2.5, D-12.1): started from the phone with Start — it
    #: starts NOW, inside the night shift's own limits (how many at once, one
    #: per folder, its time limit, the pay limit, Emergency stop), the night
    #: window or not. False = it waits for the window, as every task did.
    #: Additive; NULL on an older table reads as False.
    start_now: bool | None = Field(default=False)
    #: Who asked for it: "" (the Tonight list, a chat) or ``comm:<channel>``
    #: (the phone, ``harness/phone_code``). Additive.
    asked_from: str = ""
    #: Where its start and its ending are told (12-a): ``{channel, sender_id,
    #: thread_id, reply_to, name}`` of the phone chat that pressed Start.
    #: Never shown on a page. Additive.
    report_json: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    ended_at: datetime | None = None


def _naive(moment: Any) -> "datetime | None":
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _iso(moment: Any) -> "str | None":
    m = _naive(moment)
    return (m.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")) if m is not None else None


def _notes(row: Any) -> list[dict[str, Any]]:
    try:
        raw = json.loads(getattr(row, "notes_json", "") or "[]")
    except (TypeError, ValueError):
        return []
    return [n for n in raw if isinstance(n, dict)] if isinstance(raw, list) else []


def morning_of(row: Any) -> dict[str, Any]:
    """The task's morning facts (``morning_json``, v1.399.0), {} when none.
    Never raises."""
    try:
        raw = json.loads(getattr(row, "morning_json", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def try_on_of(row: Any) -> list[dict[str, str]]:
    try:
        raw = json.loads(getattr(row, "try_on_json", "") or "[]")
    except (TypeError, ValueError):
        return []
    out: list[dict[str, str]] = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, dict) and str(p.get("provider") or "").strip():
            out.append({"provider": str(p["provider"]).strip(), "model": str(p.get("model") or "").strip()})
    return out


def view(row: Any) -> dict[str, Any]:
    """One task as the routes answer it."""
    return {
        "id": row.id,
        "folder": row.folder,
        "words": row.words,
        "provider": row.provider,
        "model": row.model,
        "team": bool(row.team),
        # v1.424.0 (D6): the account it runs on ("" = its vendor's Default).
        "account": str(getattr(row, "account", "") or ""),
        "try_on": try_on_of(row),
        "position": int(row.position or 0),
        "state": row.state if row.state in STATES else QUEUED,
        "thread_id": row.thread_id or "",
        "turn_id": row.turn_id or "",
        "branch": row.branch or "",
        "notes": _notes(row),
        # v1.398.0 review: the user's main folder changed while it ran — the
        # one fact the morning must never miss (the list shows it first).
        "main_changed": any(n.get("kind") == "outside" for n in _notes(row)),
        "ran_s": round(float(getattr(row, "ran_s", 0.0) or 0.0), 3),
        # v1.399.0 (N2): the night it ran in, its commit, the morning's facts.
        "night": str(getattr(row, "night", "") or ""),
        "commit_sha": str(getattr(row, "commit_sha", "") or ""),
        "morning": morning_of(row),
        # Next leaps 12-a: started from the phone (the Tonight row says so)
        # and whether it starts now rather than in the night window. Where it
        # reports (``report_json``) stays off every page.
        "start_now": bool(getattr(row, "start_now", False) or False),
        "asked_from": str(getattr(row, "asked_from", "") or ""),
        "from_phone": str(getattr(row, "asked_from", "") or "").startswith("comm:"),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
    }


class NightRefused(ValueError):
    """A queue change refused in words (``status`` is the route's answer)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


NOT_FOUND = "There is no such night task."
RUNNING_EDIT = "This task is running. Cancel it first, or wait for it to end."
ENDED_EDIT = "This task has ended. Add it again to run it another night."
NO_WORDS = "Write what the task should do first."
TOO_LONG = f"A night task's words are at most {MAX_WORDS:,} characters."
FULL = f"The night list holds at most {MAX_TASKS} tasks. Remove some first."


def _queued_rows(db: Any) -> list[NightTaskRecord]:
    return list(db.exec(
        select(NightTaskRecord).where(NightTaskRecord.state == QUEUED)
        .order_by(NightTaskRecord.position, NightTaskRecord.created_at)  # type: ignore[arg-type]
    ).all())


def add_task(engine: Any, *, folder: str, words: str, provider: str = "", model: str = "",
             team: bool = False, try_on: "list[dict[str, str]] | None" = None,
             account: str = "", start_now: bool = False, asked_from: str = "",
             report_json: str = "") -> dict[str, Any]:
    """Queue one task LAST. The caller checked the folder and the models
    (the route). ``start_now`` / ``asked_from`` / ``report_json``: next leaps
    12-a, a task the phone's Start made (``harness/phone_code``). BLOCKING."""
    text = str(words or "").strip()
    if not text:
        raise NightRefused(NO_WORDS)
    if len(text) > MAX_WORDS:
        raise NightRefused(TOO_LONG, 413)
    with session_scope(engine) as db:
        total = len(db.exec(select(NightTaskRecord.id)).all())
        if total >= MAX_TASKS:
            _prune_ended(db, keep=MAX_TASKS - 1)
            total = len(db.exec(select(NightTaskRecord.id)).all())
            if total >= MAX_TASKS:
                raise NightRefused(FULL, 409)
        queued = _queued_rows(db)
        position = (max((r.position for r in queued), default=0) + 1) if queued else 1
        row = NightTaskRecord(
            folder=str(folder), words=text, provider=str(provider or ""), model=str(model or ""),
            team=bool(team), try_on_json=json.dumps(list(try_on or [])) if try_on else "",
            position=position, account=str(account or ""),
            start_now=bool(start_now), asked_from=str(asked_from or ""), report_json=str(report_json or ""),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return view(row)


def _prune_ended(db: Any, *, keep: int) -> None:
    rows = list(db.exec(
        select(NightTaskRecord).where(NightTaskRecord.state.in_(ENDED))  # type: ignore[attr-defined]
        .order_by(NightTaskRecord.ended_at)  # type: ignore[arg-type]
    ).all())
    total = len(db.exec(select(NightTaskRecord.id)).all())
    for row in rows:
        if total <= keep:
            break
        db.delete(row)
        total -= 1
    db.commit()


def get_task(engine: Any, task_id: str) -> "dict[str, Any] | None":
    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id or ""))
        return view(row) if row is not None else None


def list_tasks(engine: Any, *, now: Any = None) -> list[dict[str, Any]]:
    """Queued (in their order), then running, then the ended ones of the last
    :data:`ENDED_SHOWN_DAYS` days (newest first). BLOCKING."""
    cutoff = _naive((now or utcnow())) - timedelta(days=ENDED_SHOWN_DAYS)
    with session_scope(engine) as db:
        queued = _queued_rows(db)
        running = list(db.exec(
            select(NightTaskRecord).where(NightTaskRecord.state == RUNNING)
            .order_by(NightTaskRecord.started_at)  # type: ignore[arg-type]
        ).all())
        ended = list(db.exec(
            select(NightTaskRecord).where(NightTaskRecord.state.in_(ENDED))  # type: ignore[attr-defined]
            .order_by(NightTaskRecord.ended_at.desc())  # type: ignore[union-attr]
        ).all())
    shown = [r for r in ended if (_naive(r.ended_at) or cutoff) >= cutoff]
    return [view(r) for r in (*running, *queued, *shown)]


def rows_in(engine: Any, state: str) -> list[dict[str, Any]]:
    with session_scope(engine) as db:
        rows = db.exec(
            select(NightTaskRecord).where(NightTaskRecord.state == state)
            .order_by(NightTaskRecord.position, NightTaskRecord.created_at)  # type: ignore[arg-type]
        ).all()
        return [view(r) for r in rows]


def edit_task(engine: Any, task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """PARTIAL edit of a QUEUED task (``folder``, ``words``, ``provider``,
    ``model``, ``team``, ``try_on``, ``account``). BLOCKING."""
    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id or ""))
        if row is None:
            raise NightRefused(NOT_FOUND, 404)
        if row.state == RUNNING:
            raise NightRefused(RUNNING_EDIT, 409)
        if row.state != QUEUED:
            raise NightRefused(ENDED_EDIT, 409)
        if "words" in changes:
            text = str(changes["words"] or "").strip()
            if not text:
                raise NightRefused(NO_WORDS)
            if len(text) > MAX_WORDS:
                raise NightRefused(TOO_LONG, 413)
            row.words = text
        if "folder" in changes:
            row.folder = str(changes["folder"] or "")
        if "provider" in changes:
            row.provider = str(changes["provider"] or "")
        if "model" in changes:
            row.model = str(changes["model"] or "")
        if "team" in changes:
            row.team = bool(changes["team"])
        if "account" in changes:
            row.account = str(changes["account"] or "")
        if "try_on" in changes:
            picks = list(changes["try_on"] or [])
            row.try_on_json = json.dumps(picks) if picks else ""
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        return view(row)


def remove_task(engine: Any, task_id: str) -> None:
    """Remove a task that is not running. BLOCKING."""
    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id or ""))
        if row is None:
            raise NightRefused(NOT_FOUND, 404)
        if row.state == RUNNING:
            raise NightRefused(RUNNING_EDIT, 409)
        db.delete(row)
        db.commit()


def move_task(engine: Any, task_id: str, direction: str) -> list[dict[str, Any]]:
    """Move a QUEUED task one place up or down among the queued ones (the
    order they start in). Answers the queued list. BLOCKING."""
    if direction not in ("up", "down"):
        raise NightRefused("Move a task up or down.")
    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id or ""))
        if row is None:
            raise NightRefused(NOT_FOUND, 404)
        if row.state != QUEUED:
            raise NightRefused("Only a task that has not started yet can be moved.", 409)
        queued = _queued_rows(db)
        # Renumber 1..n first: positions from an older list may repeat.
        for n, r in enumerate(queued, start=1):
            r.position = n
        idx = next(i for i, r in enumerate(queued) if r.id == row.id)
        other = idx - 1 if direction == "up" else idx + 1
        if 0 <= other < len(queued):
            queued[idx].position, queued[other].position = queued[other].position, queued[idx].position
        for r in queued:
            db.add(r)
        db.commit()
        return [view(r) for r in _queued_rows(db)]


def mark_running(engine: Any, task_id: str, night: str = "") -> bool:
    """queued → running, as one compare-and-set (two passes never start one
    task twice). ``night`` (v1.399.0): the night it runs in. BLOCKING."""
    from sqlalchemy import update

    now = utcnow()
    values: dict[str, Any] = {"state": RUNNING, "started_at": now, "updated_at": now}
    if night:
        values["night"] = str(night)
    with session_scope(engine) as db:
        res = db.exec(
            update(NightTaskRecord)  # type: ignore[call-overload]
            .where(NightTaskRecord.id == str(task_id), NightTaskRecord.state == QUEUED)
            .values(**values)
        )
        db.commit()
        return bool(getattr(res, "rowcount", 0))


def set_session(engine: Any, task_id: str, *, thread_id: str = "", turn_id: str = "",
                branch: str = "") -> None:
    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id or ""))
        if row is None:
            return
        if thread_id:
            row.thread_id = thread_id
        if turn_id:
            row.turn_id = turn_id
        if branch:
            row.branch = branch
        row.updated_at = utcnow()
        db.add(row)
        db.commit()


def set_main_before(engine: Any, task_id: str, raw: str) -> None:
    """Keep the main checkout's state at the task's start. BLOCKING."""
    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id or ""))
        if row is None:
            return
        row.main_before_json = str(raw or "")
        db.add(row)
        db.commit()


def add_ran(engine: Any, task_id: str, seconds: float) -> None:
    """Add one ended turn's running time to the task (its limit is the
    task's). BLOCKING; never raises."""
    try:
        secs = max(0.0, float(seconds or 0.0))
    except (TypeError, ValueError):
        return
    try:
        with _RECORD_LOCK, session_scope(engine) as db:
            row = db.get(NightTaskRecord, str(task_id or ""))
            if row is None:
                return
            row.ran_s = float(row.ran_s or 0.0) + secs
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — the turn's end never fails over it
        log.warning("a night task's running time could not be kept", exc_info=True)


def set_morning(engine: Any, task_id: str, facts: dict[str, Any], *, commit_sha: str = "") -> None:
    """Keep the task's morning facts and its commit (v1.399.0). BLOCKING;
    never raises."""
    try:
        text = json.dumps(facts or {})
    except (TypeError, ValueError):
        return
    try:
        with _RECORD_LOCK, session_scope(engine) as db:
            row = db.get(NightTaskRecord, str(task_id or ""))
            if row is None:
                return
            row.morning_json = text
            if commit_sha:
                row.commit_sha = str(commit_sha)
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — the morning's facts never break the settle
        log.warning("a night task's morning facts could not be kept", exc_info=True)


def main_before(engine: Any, task_id: str) -> str:
    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id or ""))
        return str(getattr(row, "main_before_json", "") or "") if row is not None else ""


def add_note(engine: Any, task_id: str, kind: str, text: str) -> bool:
    """Keep one note on a task (the same text of the same kind once).
    BLOCKING; never raises."""
    words = " ".join(str(text or "").split())[:MAX_NOTE_CHARS]
    if not words:
        return False
    kind = kind if kind in NOTE_KINDS else "info"
    try:
        # v1.398.0 review: ONE writer at a time — each note is written from a
        # thread of its own (``NightTurn.note``), and two that read the list
        # before either wrote it lost one (two helpers' asks declined at the
        # same moment kept one "needs you" line of two: 20 at once kept 8).
        with _RECORD_LOCK, session_scope(engine) as db:
            row = db.get(NightTaskRecord, str(task_id or ""))
            if row is None:
                return False
            notes = _notes(row)
            if any(n.get("kind") == kind and n.get("text") == words for n in notes):
                return True
            notes.append({"kind": kind, "text": words, "at": _iso(utcnow())})
            row.notes_json = json.dumps(notes[-MAX_NOTES:])
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            return True
    except Exception:  # noqa: BLE001 — a note never breaks the task
        log.warning("a night task note could not be kept", exc_info=True)
        return False


#: Serializes the read-modify-write of a task's notes and running time.
_RECORD_LOCK = threading.Lock()


def finish_task(engine: Any, task_id: str, state: str, *, event_bus: Any = None) -> bool:
    """running → an ended state (only from running; a cancelled queued task
    goes through :func:`cancel_queued`). BLOCKING. Idea 39 (39-a): a task
    queued from a chat reports how it ended there (:func:`report_ended`)."""
    from sqlalchemy import update

    if state not in ENDED:
        return False
    now = utcnow()
    with session_scope(engine) as db:
        res = db.exec(
            update(NightTaskRecord)  # type: ignore[call-overload]
            .where(NightTaskRecord.id == str(task_id), NightTaskRecord.state == RUNNING)
            .values(state=state, ended_at=now, updated_at=now)
        )
        db.commit()
        done = bool(getattr(res, "rowcount", 0))
    if done:
        report_ended(engine, task_id, event_bus=event_bus)
    return done


#: Idea 39 (39-a): the verdict word on a night task's report.
_REPORT_WORDS = {DONE: "Done", NEEDS_YOU: "Needs you", FAILED: "Failed"}

#: Idea 39 (39-a, review): a Local only task's report in a chat that is not
#: Local only leaves out its branch and notes (that chat's next message may go
#: to a model off this PC), as the morning notice leaves them out.
LOCAL_ONLY_REPORT_WORDS = "It ran Local only, so its branch and notes stay in its coding session on this PC."


def report_checks(morning: Any) -> list[str]:
    """The morning facts' checks in words (idea 39, 39-a review): the test
    verdict as last seen (``finish_tests``; "No tests were checked." when the
    settle saw none) and the files the branch changed, when it says.
    ``[]`` when the settle kept no facts. Pure."""
    m = morning if isinstance(morning, dict) else {}
    if not m:
        return []
    out: list[str] = []
    tests = str(m.get("tests") or "")
    if tests == "passed":
        out.append("Its tests passed." if m.get("tests_trusted") is not False
                   else "Its tests passed, but the result is not trusted: open the session to see why.")
    elif tests == "failed":
        out.append("Its tests failed.")
    else:
        out.append("No tests were checked.")
    try:
        files = int(m.get("files") or 0)
    except (TypeError, ValueError):
        files = 0
    if files > 0:
        out.append(f"{files} file{'s' if files != 1 else ''} changed.")
    return out


def report_ended(engine: Any, task_id: str, *, event_bus: Any = None) -> dict[str, Any]:
    """Idea 39 (39-a): a night task queued from a chat writes ONE report there
    when it ends: how it ended, its checks (the morning facts' test verdict and
    files changed, review), its branch and commit, and its notes, read off its
    record (never a model's words). A Local only task keeps its branch and
    notes out of a chat that is not Local only, and its notice names it only
    as one (the morning notice's rule). A task the user stopped writes
    nothing. BLOCKING; never raises."""
    try:
        from ..agents import chat_delivery as _cd

        with session_scope(engine) as db:
            row = db.get(NightTaskRecord, str(task_id))
            if row is None:
                return {"ok": False, "why": "no such task"}
            task = view(row)
            chat = _cd.clean_chat_thread_id(getattr(row, "chat_thread_id", "") or "")
        if task.get("from_phone"):
            # Next leaps 12-a: a task the phone started tells ITS ending to
            # the phone chat that pressed Start (``phone_code.deliver``, read
            # from this record), and nowhere else: one notice per outcome.
            from .phone_code import STEP_ENDED, publish

            publish(event_bus, task["id"], STEP_ENDED)
            return {"ok": True, "why": "told the phone"}
        if not chat or task["state"] not in _REPORT_WORDS:
            return {"ok": False, "why": "nothing to report"}
        morning = task.get("morning") if isinstance(task.get("morning"), dict) else {}
        local_only = morning.get("local_only") is True
        private = False
        if local_only:
            from ..providers.egress import thread_local_only

            private = not thread_local_only(engine, chat)
        parts = [f"“{' '.join(task['words'].split())[:200]}” in {task['folder']}."]
        parts.extend(report_checks(morning))
        if private:
            parts.append(LOCAL_ONLY_REPORT_WORDS)
        else:
            if task["branch"]:
                parts.append(f"Its work is on the branch {task['branch']}"
                             + (f" (commit {task['commit_sha'][:10]})." if task["commit_sha"] else "."))
            for note in (task.get("notes") or [])[-5:]:
                text = " ".join(str(note.get("text") or "").split())
                if text:
                    parts.append(text)
        report = {
            "kind": "night", "name": " ".join(task["words"].split())[:60], "id": task["id"],
            "href": "/chat?view=code", "outcome": _REPORT_WORDS[task["state"]],
            "words": " ".join(parts), "key": f"night:{task['id']}",
        }
        if local_only:
            from .night_morning import LOCAL_ONLY_TITLE

            # The bell's notice may reach the phone: never a Local only
            # task's words (``night_morning.shared_row``).
            report["notice_name"] = LOCAL_ONLY_TITLE
        platform = type("_P", (), {"engine": engine, "event_bus": event_bus})()
        return _cd.report_to_chat(platform, chat, report)
    except Exception:  # noqa: BLE001 — the task's own record stands
        log.warning("night task %s could not report to its chat", task_id, exc_info=True)
        return {"ok": False, "why": "could not report"}


CANCELLED_BEFORE = "You cancelled it before it started."
CANCELLED_RUNNING = "You stopped it."


def cancel_queued(engine: Any, task_id: str) -> bool:
    """A queued task → stopped, with its note. False when it is not queued
    (it started meanwhile: the route stops its turn instead). BLOCKING."""
    from sqlalchemy import update

    now = utcnow()
    with session_scope(engine) as db:
        res = db.exec(
            update(NightTaskRecord)  # type: ignore[call-overload]
            .where(NightTaskRecord.id == str(task_id), NightTaskRecord.state == QUEUED)
            .values(state=STOPPED, ended_at=now, updated_at=now)
        )
        db.commit()
        done = bool(getattr(res, "rowcount", 0))
    if done:
        add_note(engine, task_id, "stopped", CANCELLED_BEFORE)
    return done


def running_task_for(engine: Any, thread_id: str) -> "dict[str, Any] | None":
    """The RUNNING night task of a coding session, or None. BLOCKING; never
    raises (None)."""
    if not thread_id:
        return None
    try:
        with session_scope(engine) as db:
            row = db.exec(
                select(NightTaskRecord).where(
                    NightTaskRecord.thread_id == str(thread_id), NightTaskRecord.state == RUNNING,
                )
            ).first()
            return view(row) if row is not None else None
    except Exception:  # noqa: BLE001 — no table yet / unreadable: not a night task
        return None


def has_note(task: dict[str, Any], kind: str) -> bool:
    return any(n.get("kind") == kind for n in task.get("notes") or [])


# --------------------------------------------------------------------------- #
# the window and the clock
# --------------------------------------------------------------------------- #


def _local_now() -> datetime:
    return datetime.now().astimezone()


#: The LOCAL clock the night loop reads (a seam: tests set a clock they
#: control). Returns an aware local datetime.
CLOCK = _local_now


def now_local() -> datetime:
    return CLOCK()


def window_of(config: Any) -> tuple[str, str]:
    from .night_window import clean_time

    start = clean_time(getattr(config, "night_window_start", "") or "") or "23:00"
    end = clean_time(getattr(config, "night_window_end", "") or "") or "07:00"
    return start, end


def window_open(config: Any, now: "datetime | None" = None) -> bool:
    from .night_window import in_window

    start, end = window_of(config)
    return in_window(now or now_local(), start, end)


def window_view(config: Any, now: "datetime | None" = None) -> dict[str, Any]:
    """The window as the list answers it: ``{start, end, open, words,
    next_start}``."""
    from .night_window import clock_words, next_start, window_words

    at = now or now_local()
    start, end = window_of(config)
    nxt = next_start(at, start, end)
    return {
        "start": start,
        "end": end,
        "start_words": clock_words(start),
        "end_words": clock_words(end),
        "open": window_open(config, at),
        "words": window_words(at, start, end),
        "next_start": nxt.isoformat() if nxt is not None else None,
    }


#: How long before the night window opens a QUEUED task keeps the PC awake
#: (v1.398.0 review): from 5 PM for the default 11 PM window.
AWAKE_BEFORE_S = 6 * 3600.0


def awake_needed(engine: Any, config: Any, now: "datetime | None" = None) -> bool:
    """Should the PC stay awake for night shift (``/system/activity``'s
    ``night_shift``, read by the desktop's sleep blocker)? A night task is
    RUNNING, or one is QUEUED while Emergency stop is off and the window is
    open or opens within :data:`AWAKE_BEFORE_S` (it starts on the loop's
    first pass inside it). BLOCKING; never raises."""
    try:
        with session_scope(engine) as db:
            running = db.exec(
                select(NightTaskRecord.id).where(NightTaskRecord.state == RUNNING)
            ).first()
            if running is not None:
                return True
            queued = db.exec(
                select(NightTaskRecord.id).where(NightTaskRecord.state == QUEUED)
            ).first()
            # Next leaps 12-a: a task the phone started waits for room NOW,
            # not for the window.
            now_task = db.exec(
                select(NightTaskRecord.id).where(
                    NightTaskRecord.state == QUEUED, NightTaskRecord.start_now == True,  # noqa: E712
                )
            ).first()
        if queued is None:
            return False
        from ..agents.orchestrator import emergency_stop_on

        if emergency_stop_on(config):
            return False
        if now_task is not None or window_open(config, now):
            return True
        # v1.398.0 review: the plan's own words — "keep-awake holds while a
        # night task is QUEUED or running". Holding only once the window was
        # open let a PC set to sleep after an idle half hour fall asleep at
        # 10:30 PM with tonight's tasks queued, and the night never came. It
        # holds from AWAKE_BEFORE_S before the window opens (not all day: a
        # task queued in the morning does not keep a laptop awake till night).
        from .night_window import next_start

        at = now or now_local()
        start, end = window_of(config)
        nxt = next_start(at, start, end)
        return nxt is not None and 0 <= (nxt - at).total_seconds() <= AWAKE_BEFORE_S
    except Exception:  # noqa: BLE001 — no table yet: nothing to keep awake for
        return False


# --------------------------------------------------------------------------- #
# the posture (decision 4)
# --------------------------------------------------------------------------- #


def task_minutes(config: Any) -> int:
    try:
        return max(5, min(1440, int(getattr(config, "night_task_minutes", 90) or 90)))
    except (TypeError, ValueError):
        return 90


def card_wait_seconds(config: Any) -> float:
    try:
        return 60.0 * max(1, min(240, int(getattr(config, "night_card_wait_minutes", 15) or 15)))
    except (TypeError, ValueError):
        return 900.0


def max_at_once(config: Any) -> int:
    try:
        return max(1, min(8, int(getattr(config, "night_max_at_once", 2) or 2)))
    except (TypeError, ValueError):
        return 2


def minutes_left(task: "dict[str, Any] | None", config: Any) -> float:
    """What is left of a night task's time limit, in minutes (v1.398.0
    review): the limit is the TASK's, across every turn it runs — its first
    turn, a carry-on after a restart, Resume when ready — never a fresh limit
    per turn. ``ran_s`` is the running time of its turns that ended."""
    try:
        ran = float((task or {}).get("ran_s") or 0.0)
    except (TypeError, ValueError):
        ran = 0.0
    return max(0.0, task_minutes(config) - ran / 60.0)


#: The record's note when a night task waiting on its account is stopped
#: because the window closed / its time is used up (v1.398.0 review).
WAIT_WINDOW_CLOSED = (
    "Its account reached a limit that did not reset before the night window closed, so it was "
    "stopped. Press Resume when ready in the session to carry it on."
)
WAIT_TIME_USED_UP = (
    "Its account reached a limit, and its time limit is used up, so it was stopped. Open the "
    "session to carry it on."
)
#: Next leaps 12-a review 4: the record's note when a task the phone started
#: is stopped because its account's limit did not reset (or will reset only
#: later) within what was left of its time limit when the wait began.
WAIT_PAST_LIMIT = (
    "Its account reached a limit that does not reset within this task's time limit, so it was "
    "stopped. Press Resume when ready in the session to carry it on."
)


def _instant_utc(value: Any) -> "datetime | None":
    """An ISO instant as naive UTC (a naive one is taken as UTC), else None."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return _naive(moment)


def wait_over(task: "dict[str, Any] | None", wait: Any, config: Any, now: Any = None) -> bool:
    """Has the account wait of a task the PHONE started run past its time
    limit, or will its account reset only after that (next leaps 12-a review
    4)? Its wait may last what was left of its time limit when the wait began
    (``wait.armed_at``; the task's own start when that is unreadable) — a
    night task's wait is bounded by the night window instead, and a phone
    task runs by day, so nothing else would end it: it held its slot and the
    PC awake until a weekly reset. False for every other task. Pure (``now``:
    naive UTC, default the wall clock)."""
    if not (task or {}).get("start_now"):
        return False
    wait = wait if isinstance(wait, dict) else {}
    armed = _instant_utc(wait.get("armed_at")) or _instant_utc((task or {}).get("started_at"))
    if armed is None:
        return True  # no start to count from: never an unbounded wait
    deadline = armed + timedelta(minutes=minutes_left(task, config))
    at = _instant_utc(now) or _naive(utcnow())
    if at >= deadline:
        return True
    reset = _instant_utc(wait.get("resets_at")) if wait.get("resets_at") else None
    return reset is not None and reset > deadline
#: The refusal of a night turn on a model billed per use with no room left
#: under the user's pay-per-use limit (v1.398.0 review).
PAY_USED_UP = (
    "This night task's model is billed per use, and your pay-per-use limit has no room left "
    "(Settings › Models › Model team), so it did not run."
)
#: The refusal of a night task's carry-on turn when its time is used up.
TIME_USED_UP = "This night task has used up its time limit, so it was not carried on."
#: The refusal of a night task's carry-on turn outside the night window.
WINDOW_CLOSED = (
    "The night window has closed, so this night task was not carried on by itself. Open the "
    "session to carry it on."
)


def posture_setup(setup: dict[str, Any], config: Any, task: "dict[str, Any] | None" = None) -> dict[str, Any]:
    """The session's setup AS A NIGHT TURN RUNS IT (a copy; nothing stored):
    "Approve for me" plus the command tools granted (decision 4) — the
    night's allow-list (:data:`RUNS_UNASKED`) is the stream lane's own rule
    on top — and the reply capped at what is LEFT of the task's minutes
    (:func:`minutes_left`; a cap the session already has that is SHORTER is
    kept)."""
    from .reply_cap import SETUP_KEY as CAP_KEY
    from .reply_cap import clean as clean_cap

    out = dict(setup or {})
    out["approval_mode"] = POSTURE
    granted = [str(g) for g in (out.get("granted_tools") or []) if isinstance(g, str)]
    for name in COMMAND_TOOLS:
        if name not in granted:
            granted.append(name)
    out["granted_tools"] = granted
    minutes = max(1, int(minutes_left(task, config))) if task is not None else task_minutes(config)
    cap = dict(clean_cap(out.get(CAP_KEY)) or {})
    if not cap.get("minutes") or int(cap["minutes"]) > minutes:
        cap["minutes"] = minutes
    out[CAP_KEY] = cap
    return out


# --------------------------------------------------------------------------- #
# the wait (decision 1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NightTurn:
    """The night task a turn belongs to, armed while its driver task is
    created (the task, its tool calls and its helpers copy it)."""

    task_id: str
    card_wait_s: float
    engine: Any = None
    #: v1.406.0 part 3 follow-ups: a turn with no night task (a fix of a pull
    #: request's checks) hears each note here — ``on_note(kind, text)``.
    on_note: Any = field(default=None, compare=False)

    def minutes(self) -> int:
        return max(1, int(round(self.card_wait_s / 60.0)))

    def wait_words(self) -> str:
        n = self.minutes()
        return f"{n} minute" if n == 1 else f"{n} minutes"

    def note(self, kind: str, text: str) -> None:
        """Keep a note on the task — off the caller's loop when there is one
        (a thread of its own), never raising."""
        if self.on_note is not None:
            with contextlib.suppress(Exception):
                self.on_note(kind, text)
        if self.engine is None:
            return

        def _write() -> None:
            add_note(self.engine, self.task_id, kind, text)

        try:
            t = threading.Thread(target=_write, name="night-note", daemon=True)
            t.start()
            _NOTE_THREADS.add(t)
            if len(_NOTE_THREADS) > 64:
                for done in [x for x in _NOTE_THREADS if not x.is_alive()]:
                    _NOTE_THREADS.discard(done)
        except RuntimeError:  # pragma: no cover — interpreter shutting down
            _write()

    def declined_words(self, what: str) -> str:
        """What the MODEL is told about a step nobody answered (the call's
        refusal)."""
        return (
            f"left for the user: {what} — nobody answered in {self.wait_words()} (this task runs "
            "at night with nobody watching), so this step was not done. Do not ask again; carry on "
            "with the rest of the task and say at the end what was left for the user."
        )

    def note_words(self, what: str) -> str:
        """What the night record keeps about it (the morning reads it)."""
        return f"Needs your OK: {what} (nobody answered in {self.wait_words()}, so it was skipped)."


_NOTE_THREADS: set[threading.Thread] = set()


def ran_later(engine: Any, task_id: str, started_at: Any) -> None:
    """A night turn ended: add its running time (from ``started_at``, the
    turn log's wall-clock start) to its task, on a thread of its own (the
    caller is the turn's done callback, on the loop). Never raises; waited
    for by :func:`flush_notes`."""
    start = _naive(started_at)
    if engine is None or start is None:
        return
    seconds = max(0.0, (_naive(utcnow()) - start).total_seconds())

    def _write() -> None:
        add_ran(engine, task_id, seconds)

    try:
        t = threading.Thread(target=_write, name="night-ran", daemon=True)
        t.start()
        _NOTE_THREADS.add(t)
    except RuntimeError:  # pragma: no cover — interpreter shutting down
        _write()


def flush_notes(timeout: float = 5.0) -> None:
    """Wait for notes being written (tests; shutdown). Never raises."""
    for t in list(_NOTE_THREADS):
        with contextlib.suppress(Exception):
            t.join(timeout)
        _NOTE_THREADS.discard(t)


NIGHT_TURN: "ContextVar[NightTurn | None]" = ContextVar("ij_night_turn", default=None)


def current() -> "NightTurn | None":
    return NIGHT_TURN.get()


def set_turn(turn: "NightTurn | None") -> "Token[NightTurn | None]":
    return NIGHT_TURN.set(turn)


def reset_turn(token: "Token[NightTurn | None]") -> None:
    NIGHT_TURN.reset(token)


def call_words(tool: str) -> str:
    """A tool call in the words the user reads (the card's own)."""
    try:
        from ..agents.mission import ASK_WORDS

        words = ASK_WORDS.get(str(tool or ""))
        if words:
            return words
    except Exception:  # noqa: BLE001
        pass
    return f"run {tool}" if tool else "a step"


def ask_timeout() -> "float | None":
    """The wait a HELPER's ask gets inside a night task (the runtime's own
    clock otherwise: None = not a night turn)."""
    turn = current()
    return turn.card_wait_s if turn is not None else None


# --------------------------------------------------------------------------- #
# turns Iron Jarvis started by itself (Emergency stop reaches them)
# --------------------------------------------------------------------------- #

#: Who started a coding-session turn (``start_build_turn(started_by=)``).
BY_PERSON = "person"
BY_NIGHT = "night"
BY_WAIT = "wait"
BY_RESTART = "restart"
#: v1.406.0 part 3: a fix of a pull request's failing checks
#: (``daemon/routes/ci_watch``) — Emergency stop covers it like the rest.
BY_CI = "ci"
#: Next leaps 12-a: the first turn of a task the phone's Start made
#: (``harness/phone_code``) — a night task's own first turn, by another door.
BY_PHONE = "phone"
#: Who starts a task's OWN first turn (never a carry-on of it).
TASK_STARTERS = (BY_NIGHT, BY_PHONE)


def may_run_now(task: "dict[str, Any] | None", config: Any, now: "datetime | None" = None) -> bool:
    """May this task start, or carry on by itself, now (next leaps 12-a,
    D-12.1)? A task the phone started with Start runs NOW, inside the night
    shift's other limits; every other task only inside the night window.
    Pure (the clock)."""
    if task is not None and task.get("start_now"):
        return True
    return window_open(config, now)

_UNATTENDED: dict[str, tuple[str, str]] = {}
_UNATTENDED_LOCK = threading.Lock()


def note_unattended_turn(turn_id: str, thread_id: str, by: str) -> None:
    if not turn_id or by == BY_PERSON:
        return
    with _UNATTENDED_LOCK:
        _UNATTENDED[str(turn_id)] = (str(thread_id or ""), str(by))


def forget_unattended_turn(turn_id: str) -> None:
    with _UNATTENDED_LOCK:
        _UNATTENDED.pop(str(turn_id or ""), None)


def unattended_turns() -> dict[str, tuple[str, str]]:
    with _UNATTENDED_LOCK:
        return dict(_UNATTENDED)


EMERGENCY_NOTE = "Emergency stop was turned on, so this task was stopped."


def stop_unattended_turns(engine: Any = None, *, note: str = EMERGENCY_NOTE) -> list[str]:
    """Stop every coding-session turn Iron Jarvis started by itself (a night
    task, a resume) — Emergency stop's half for coding sessions. Thread-safe
    (``TurnRegistry.stop`` wakes the turn's own loop); a night task's record
    keeps the note. Returns ``turn:<id>`` for each turn asked to stop.
    BLOCKING (the note); never raises."""
    from ..core.turns import STOP_BY_EMERGENCY, TURNS

    from .stall import running_steps, stop_step

    stopped: list[str] = []
    for turn_id, (thread_id, by) in unattended_turns().items():
        try:
            # Fourth plan G27 (CS6): Emergency stop names itself — the reply's
            # line and each step's row say so, never "stopped by you".
            if TURNS.stop(turn_id, by=STOP_BY_EMERGENCY):
                stopped.append(f"turn:{turn_id}")
            # v1.398.0 review: and its RUNNING STEPS, now — a turn's Stop
            # waits for the step in flight (v1.392.0's open line), so a hung
            # command kept running for its whole deadline (ten minutes by
            # default) after Emergency stop. Each step is cancelled the way
            # "Stop the step" does it (a command's tree is killed).
            for call_id in running_steps(turn_id):
                stop_step(turn_id, call_id, by=STOP_BY_EMERGENCY)
        except Exception:  # noqa: BLE001 — one turn never keeps the rest going
            log.warning("Emergency stop could not stop coding turn %s", turn_id, exc_info=True)
            continue
        if engine is not None and by in TASK_STARTERS and thread_id:
            task = running_task_for(engine, thread_id)
            if task is not None:
                add_note(engine, task["id"], "emergency", note)
    return stopped


# --------------------------------------------------------------------------- #
# how a task ended (read from its reply line — never a model's own summary)
# --------------------------------------------------------------------------- #


#: Fourth plan G27 (CS6): the note kind of a reply stopped by someone other
#: than the user (``stoppedBy`` on its line); anything else is "stopped".
_STOP_NOTE_KIND = {"time_limit": "limit", "emergency": "emergency"}


def _stopped_by_other(line: dict[str, Any]) -> "str | None":
    """Who stopped this reply when its line says so (``stoppedBy``) and it
    was not the user ("time_limit", "emergency", or "" = not recorded); None
    for a line that does not say, or says the user (read as before)."""
    if not line.get("interrupted") or not isinstance(line.get("stoppedBy"), str):
        return None
    from ..core.turns import STOP_BY_YOU, stop_author

    by = stop_author(line.get("stoppedBy"))
    return None if by == STOP_BY_YOU else by


def outcome_of(task: dict[str, Any], line: "dict[str, Any] | None") -> tuple[str, list[tuple[str, str]]]:
    """The ended state of a task whose turn is over, and the notes to keep,
    from the reply line ``recorded_turn`` wrote (``line``, None when there is
    none) and the task's own notes. Pure."""
    notes: list[tuple[str, str]] = []
    if task_has_stop(task):
        state = STOPPED
    elif line is None:
        state = STOPPED
        notes.append(("stopped", "The task ended without a reply."))
    elif (stopped_by := _stopped_by_other(line)) is not None:
        # Fourth plan G27 (CS6): a reply Iron Jarvis's time limit or
        # Emergency stop stopped (its line says who, ``stoppedBy``) is
        # STOPPED, never FAILED with "You stopped this reply" as the error.
        state = STOPPED
        kind = _STOP_NOTE_KIND.get(stopped_by, "stopped")
        if not has_note(task, kind):
            notes.append((kind, str(line.get("error") or "") or "The reply was stopped before it finished."))
    elif line.get("error"):
        if line.get("interrupted") and "restart" in str(line.get("error") or "").lower():
            state = STOPPED
            notes.append(("restart", "Stopped when Iron Jarvis restarted."))
        else:
            state = FAILED
            notes.append(("error", str(line.get("error") or "The reply failed.")))
    elif (cut := cut_short(line)):
        # Fourth review CS1 (decision D12): a reply that ended on its done
        # frame without finishing — cut off at the model's output limit, or
        # its answers cut off and never recovered — is FAILED, never "done".
        # Read BEFORE a limit, as the day notice and the attempts card read
        # it (R13). Its work is still committed (``routes/night``).
        state = FAILED
        notes.append(("error", CUT_WORDS.get(cut, CUT_WORDS["length"])))
    elif isinstance(line.get("capMet"), dict):
        state = STOPPED
        cap = line["capMet"]
        limit = cap.get("limit")
        if cap.get("kind") == "time" and limit:
            notes.append(("limit", f"Stopped at its {int(limit)}-minute limit; the reply says what it got done."))
        else:
            notes.append(("limit", "Stopped at its limit; the reply says what it got done."))
    elif line.get("interrupted"):
        state = STOPPED
        notes.append(("stopped", "The reply was stopped before it finished."))
    elif isinstance(line.get("stepLimit"), dict):
        state = STOPPED
        notes.append(("limit", "Stopped at its step limit; the reply says what it got done."))
    else:
        state = DONE
    low = has_note(task, "low_trust")
    if line is not None and line.get("trust") == "low":
        low = True
        if not has_note(task, "low_trust"):
            why = str(line.get("trustReason") or line.get("trustNote") or "").strip()
            notes.append(("low_trust", low_trust_note_words(why)))
    if line is not None and isinstance(line.get("accountLimit"), dict):
        notes.append(("account", "Its account reached a limit."))
        if state == DONE:
            # v1.398.0 review: a reply that ended on its account's limit did
            # not finish its work — never "done" (Resume when ready may be
            # off, or the night ended first).
            state = NEEDS_YOU
    # v1.398.0 review: a task that ran at LOW TRUST (it read something
    # flagged; its commands were taken away from then on) never reads "done" —
    # its work stopped short in a way the morning must look at.
    if state == DONE and (has_note(task, "declined") or has_note(task, "unanswered") or low):
        state = NEEDS_YOU
    # v1.503.0 (north-star 3.6): the task's OWN finish check is the record of
    # whether the work is done — failing tests, or points of the request it
    # left open, are never "done" (only an untrusted pass was downgraded,
    # by ``routes/night``). The tests' words are the route's own note.
    fin = line.get("finish") if isinstance(line, dict) else None
    unmet = finish_unmet(fin)
    if unmet:
        notes.append(("unmet", unmet_words(unmet)))
    elif finish_loop_unfinished(fin) and finish_tests(fin) != "failed":
        # v1.513.0 (second review T5): the check-again loop ended without
        # finishing (stuck, its round or step limit, tests it could not run
        # again) and no point and no first test run says why: the line under
        # the reply's own words.
        notes.append(("unmet", NOT_FINISHED_WORDS))
    if state == DONE and finish_needs_you(fin):
        state = NEEDS_YOU
    # Third plan NA1 (decision G10): a task tried on several models ends from
    # its attempts' card — the ONE reading (``attempts.card_reading``): "Needs
    # you: choose one" when at least one finished, "Failed" when none did;
    # never "done" (nobody but the user picks one). Its reply line carries
    # only the card and the summary, never the fields read above.
    reading = attempts_reading(line)
    if reading is not None and state in (DONE, NEEDS_YOU):
        if reading["state"] == FAILED:
            state = FAILED
            notes.append(("error", reading["words"]))
        else:
            state = NEEDS_YOU
            notes.append(("attempts", reading["words"]))
    return state, notes


def attempts_reading(line: Any) -> "dict[str, Any] | None":
    """The ONE reading of a reply line's attempts card
    (``attempts.card_reading``), None when the line carries none. Pure."""
    if not isinstance(line, dict):
        return None
    from . import attempts as A

    return A.card_reading(line.get(A.LINE_KEY))


def _picks_of(task: "dict[str, Any] | None") -> int:
    picks = (task or {}).get("try_on") or []
    return len(picks) if isinstance(picks, list) else 0


def task_weight(task: "dict[str, Any] | None", config: Any) -> int:
    """How many replies a night task runs AT ONCE (third plan NA7, decision
    G12): a task tried on several models runs one per model, at most "Night
    tasks at once" of them at a time (its other attempts wait their turn
    inside it, ``attempts.Plan.max_running``) — so it always fits a night
    that is otherwise free; any other task, one. What "Night tasks at once"
    counts. Pure."""
    return max(1, min(_picks_of(task), max_at_once(config)))


def task_rounds(task: "dict[str, Any] | None", config: Any) -> int:
    """How many turns of its time limit a night task may need back to back
    (third plan NA7): a task tried on more models than run at once runs them
    in rounds, each attempt with its own limit; any other task, one. Pure."""
    picks = _picks_of(task)
    if picks <= 1:
        return 1
    at_once = task_weight(task, config)
    return max(1, -(-picks // at_once))


def overrun_after_s(task: "dict[str, Any] | None", config: Any, grace_s: float) -> float:
    """How long a night task's turn may run before the night loop's backstop
    stops it (third plan NA7): what is left of its limit, once per round its
    attempts need (an attempt that waited its turn is never stopped for the
    time it waited), plus ``grace_s``. Pure."""
    return 60.0 * minutes_left(task, config) * task_rounds(task, config) + float(grace_s)


#: v1.513.0 (second review T5): the reason a task needs the user when its
#: check-again loop ended unfinished with no open point to count (the coding
#: session's own words, dashboard/lib/nextStep).
NOT_FINISHED_WORDS = "Jarvis's own check says this is not finished."


def _again_of(fin: dict[str, Any]) -> "dict[str, Any] | None":
    """The record's check-again block as a reply line keeps it
    (``finish.saved_again``: ``rounds``, ``ended``, ``open``), None when the
    loop was not in play or the block is unreadable."""
    if "again" not in fin:
        return None
    from .finish import saved_again

    return saved_again(fin.get("again"))


def finish_unmet(fin: Any) -> int:
    """How many points of the request a reply's finish record left open AT
    THE END; 0 for none or no readable record.

    v1.513.0 (second review T5): with a check-again block, its ``open`` (the
    points still open when the loop ended) — ``unmet`` is the FIRST answer's
    count by design (``harness/finish``), so a point the loop got fixed read
    as open, and one a later round answered "blocked" read as none. ``unmet``
    only when the loop was not in play."""
    if not isinstance(fin, dict):
        return 0
    again = _again_of(fin)
    if again is not None:
        still = again.get("open")
        return still if isinstance(still, int) and not isinstance(still, bool) and still > 0 else 0
    unmet = fin.get("unmet")
    return unmet if isinstance(unmet, int) and not isinstance(unmet, bool) and unmet > 0 else 0


def finish_loop_unfinished(fin: Any) -> bool:
    """Did the record's check-again loop end WITHOUT finishing the work
    (v1.513.0, second review T5)? Every ending but "done" (and "" — it never
    had to end): blocked, stuck, its round limit, its step limit, tests it
    could not run again, failures called outside the request. Pure; junk
    reads as no."""
    if not isinstance(fin, dict):
        return False
    again = _again_of(fin)
    if again is None:
        return False
    from .finish import AGAIN_DONE

    return again.get("ended") not in ("", AGAIN_DONE)


def finish_tests(fin: Any) -> str:
    """A reply's tests as they were LAST seen: "passed", "failed" or ""
    (v1.513.0, lead review of T5). The finish record keeps the first run in
    ``tests`` and a later run in ``after``: tests that failed first and
    passed after the fix are passing, and tests that passed first and
    failed in a later run are failing. The daemon read only the first
    verdict, so the morning said "Needs you: its tests failed" over a fix
    that had made them pass, and "Done" over a later failure. The same
    reading as the screen's (``dashboard/lib/stepWords`` ``testsFailing``).
    Pure; junk reads as ""."""
    if not isinstance(fin, dict):
        return ""
    after = fin.get("after")
    if after in ("passed", "failed"):
        return after
    first = fin.get("tests")
    return first if first in ("passed", "failed") else ""


def finish_needs_you(fin: Any) -> bool:
    """Does a reply's finish record say its work is NOT done (v1.503.0,
    north-star 3.6)? Its tests failed, points of the request are still open
    AT THE END, or (v1.513.0, second review T5) its check-again loop ended
    without finishing. The ONE reading the night shift's ending, the day's
    "Your coding session finished" notice and the pull-request auto-fix
    share. Pure; junk reads as no."""
    if not isinstance(fin, dict):
        return False
    return finish_tests(fin) == "failed" or finish_unmet(fin) > 0 or finish_loop_unfinished(fin)


def unmet_words(unmet: int) -> str:
    """The morning's note for open points — the coding session's own words
    (dashboard/lib/nextStep: "2 points of the request still need work.")."""
    n = int(unmet)
    return f"{n} point{'s' if n != 1 else ''} of the request still need{'s' if n == 1 else ''} work."


#: v1.513.0 (second review T7): the morning's reason when a night task ended
#: with nothing changed and nothing checked (the sense of the unattended
#: runs' ``did_nothing`` verdict, ``agents/outcome``).
DID_NOTHING_WORDS = "It ended without changing anything."


def ended_doing_nothing(state: str, fin: Any, *, changed: bool, try_on: bool = False) -> bool:
    """Does a night task that would end "done" end "needs you" instead,
    because it changed nothing (``changed`` False: nothing to commit and its
    branch carries nothing over its base) and no check of its passed
    (v1.513.0, second review T7)? A task that merely read a few files and
    ended, or said it could not find the code, was announced "Done". Whether
    a read-only ask's answer IS the result cannot be told reliably, so it is
    not guessed: "needs you" with the reply one press away is the honest
    state. A task tried on several models (``try_on``) keeps its work on its
    attempts' own branches, so its session's branch is empty by design:
    never this. Pure."""
    if state != DONE or changed or try_on:
        return False
    if finish_tests(fin) == "passed":
        return False
    return True


#: The trust reason a LATER turn of a night task that went to low trust
#: starts with (``routes/build.start_build_turn``).
LOW_TRUST_CARRIED = "this night task read something flagged earlier, so it stays at low trust"


def low_trust_note_words(reason: str = "") -> str:
    """The record's note when a night task's turn went to LOW TRUST."""
    why = " ".join(str(reason or "").split())
    return ("It ran at low trust after reading something flagged" + (f" ({why})" if why else "")
            + "; commands and page actions were not available after that, for the rest of the task.")


def task_has_stop(task: dict[str, Any]) -> bool:
    return has_note(task, "emergency") or any(
        n.get("kind") == "stopped" and n.get("text") == CANCELLED_RUNNING for n in task.get("notes") or []
    )


def last_reply_line(messages: list[dict[str, Any]], turn_ids: "set[str] | None" = None) -> "dict[str, Any] | None":
    """The newest assistant line (of ``turn_ids`` when given)."""
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        if turn_ids and str(m.get("turnId") or "") not in turn_ids:
            continue
        return m
    return None


__all__ = [
    "BY_CI", "BY_NIGHT", "BY_PERSON", "BY_PHONE", "BY_RESTART", "BY_WAIT", "COMMAND_TOOLS", "DONE", "ENDED",
    "FAILED", "TASK_STARTERS", "WAIT_PAST_LIMIT", "may_run_now", "wait_over",
    "NEEDS_YOU", "NIGHT_TURN", "NightRefused", "NightTaskRecord", "NightTurn", "POSTURE", "QUEUED",
    "RUNNING", "RUNS_UNASKED", "STATES", "asks_at_night", "STOPPED", "add_note", "add_task", "ask_timeout", "call_words", "cancel_queued",
    "card_wait_seconds", "current", "edit_task", "finish_needs_you", "finish_task", "finish_unmet",
    "flush_notes", "get_task", "unmet_words", "finish_loop_unfinished", "NOT_FINISHED_WORDS",
    "DID_NOTHING_WORDS", "ended_doing_nothing", "attempts_reading", "task_weight", "task_rounds", "overrun_after_s",
    "last_reply_line", "list_tasks", "mark_running", "max_at_once", "move_task", "note_unattended_turn",
    "morning_of", "outcome_of", "posture_setup", "remove_task", "reset_turn", "running_task_for",
    "set_morning", "set_session",
    "set_turn", "stop_unattended_turns", "task_minutes", "try_on_of", "unattended_turns", "view",
]
