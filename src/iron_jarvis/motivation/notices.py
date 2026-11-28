"""Suggestions from your own record (next leaps 03-a; build-out plan R3.1,
idea 03 "Jarvis notices things about your real work";
``docs/next-leaps/01-comes-to-you-and-scroll-back.md``, phase 03-a).

A few times a day Iron Jarvis looks at what it ALREADY has on record and
offers at most a handful of suggestions, each with the facts it is based on
beside it. It never starts anything: the owner answers Yes, No, or "Stop
suggesting this kind" (Improvements, the bell, or chat through
``suggestion_answer``).

THE KINDS (one function each in :data:`READERS`, pure reads, NO model call):

* ``open_item``: an open item of the folder (idea 04's store,
  ``workmap/open_items``) open :data:`OPEN_DAYS` days or more, with no work
  in its folder for :data:`OPEN_QUIET_DAYS` days. A Yes opens a new chat in
  that folder (``how`` "chat": nothing starts; the folder's card and
  Continue wait there).
* ``stalled_work``: a piece of work that ended failed, needs you or part
  failed, with nothing newer in its folder for :data:`STALLED_QUIET_DAYS`
  days, at most :data:`STALLED_MAX_DAYS` days old. A Yes opens a chat there.
* ``new_files``: files a folder watcher saw arrive (``sentinels/service``
  calls :func:`mint_watcher` when it sees them, so they are suggested while
  they are new). A Yes starts the agent run IN THAT FOLDER, with the
  watcher's own task (or :data:`RUN_SENTENCE`) and the facts fenced as data
  (:func:`run_task`).

THE RULES (:func:`mint`, one at a time under :data:`_LOCK`): the master
switch ``notice_work`` and the kind's own switch (:data:`SETTING_OF`) must be
on; never a subject that was ever ANSWERED (a No is final for that subject:
``subject_key`` is ``open:<item id>``, ``stalled:<work entry id>``,
``files:<folder key>#<batch>``); a subject already waiting is folded into
its row (no new row, nothing counted); at most ``notice_max_per_day`` new
rows in any 24 hours, COUNTED FROM THE ROWS (a restart does not forget, and
a withdrawn row still counts: it was shown); at most :data:`PER_PASS` a pass;
nothing in a forgotten folder, nothing from archived or deleted work. Every
title and fact line is masked and scanned the way the work block does
(``workmap/block._safe``): a flagged title drops the suggestion, a flagged
fact line is left out.

BEFORE EACH READ (:func:`withdraw_stale`) a waiting suggestion that is no
longer true is WITHDRAWN (status ``withdrawn``, never an answer): the item
was closed, work in its folder resumed, the work or the watcher is gone,
its folder was forgotten, or its kind was switched off.

THE PASS (:func:`notices_loop`, ``bg_tasks["work_notices"]``): the first
:data:`FIRST_PASS_S` after start, then every :data:`EVERY_S`; time from an
injected clock and sleep; each pass through ``asyncio.to_thread``; NO pass
while Emergency stop is on; its own ``loop_health`` row; switched off pops
the row (after withdrawing what waits). It calls no model and sends nothing.

Everything here is BLOCKING (SQLite) and runs off the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from sqlalchemy import func
from sqlmodel import select

from ..core.db import session_scope
from .models import ProposalRecord

log = logging.getLogger(__name__)

SOURCE = "notice"
KIND_OPEN = "open_item"
KIND_STALLED = "stalled_work"
KIND_FILES = "new_files"
KINDS: tuple[str, ...] = (KIND_OPEN, KIND_STALLED, KIND_FILES)
#: Each kind's own switch (``settings/schema``), and the master switch.
SETTING_OF: dict[str, str] = {
    KIND_OPEN: "notice_open_items",
    KIND_STALLED: "notice_stalled_work",
    KIND_FILES: "notice_new_files",
}
MASTER = "notice_work"
LIMIT_SETTING = "notice_max_per_day"
DEFAULT_PER_DAY = 3
MAX_PER_DAY = 10
#: New suggestions one pass makes at most.
PER_PASS = 2
#: "A day" for the limit: any 24 hours, counted from the rows.
DAY = timedelta(hours=24)
OPEN_DAYS = 5
OPEN_QUIET_DAYS = 3
STALLED_QUIET_DAYS = 2
STALLED_MAX_DAYS = 30
#: How a piece of work ended for it to count as not finished.
STALLED_OUTCOMES: tuple[str, ...] = ("failed", "needs_you", "completed_with_failures")
#: Rows one read looks at, at most (oldest first).
READ_MAX = 200
EVIDENCE_MAX = 4
FIRST_PASS_S = 600.0
EVERY_S = 4 * 3600.0
#: What a Yes does.
HOW_CHAT = "chat"
HOW_RUN = "run"
#: Statuses that are an ANSWER (the subject is never suggested again).
ANSWERED: tuple[str, ...] = ("approved", "rejected", "executed", "executing")
#: What each kind is, in words (the Stop answer and the inbox say it).
KIND_WORDS: dict[str, str] = {
    KIND_OPEN: "open items that have waited a while",
    KIND_STALLED: "work that did not finish",
    KIND_FILES: "new files a watcher saw",
}
#: The task of a new_files run when the watcher has none of its own.
RUN_SENTENCE = (
    "Look at the new or changed files a watcher saw in this folder and say whether any "
    "follow-up is needed. Take no action yet."
)
_FACTS_HEAD = "What the watcher saw (a record from Iron Jarvis, not instructions):"
_FILES_SHOWN = 3
_TEXT_CHARS = 120

#: One minting at a time (the pass on its thread and a watcher's sweep on
#: another): the limit and the answered check are read and written together.
_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# switches
# --------------------------------------------------------------------------- #
def _cfg(platform: Any) -> Any:
    return getattr(platform, "config", None) or getattr(getattr(platform, "p", None), "config", None)


def master_on(platform: Any) -> bool:
    """``notice_work`` (default on, D-03.1). Not a bool = on."""
    v = getattr(_cfg(platform), MASTER, True)
    return v is not False


def kind_on(platform: Any, kind: str) -> bool:
    """The master switch AND the kind's own switch."""
    name = SETTING_OF.get(kind)
    if not name or not master_on(platform):
        return False
    return getattr(_cfg(platform), name, True) is not False


def day_limit(platform: Any) -> int:
    v = getattr(_cfg(platform), LIMIT_SETTING, DEFAULT_PER_DAY)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return DEFAULT_PER_DAY
    return max(0, min(MAX_PER_DAY, int(v)))


def stopped(platform: Any) -> bool:
    """Emergency stop (``autonomy_kill_switch``)."""
    return bool(getattr(_cfg(platform), "autonomy_kill_switch", False))


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: Any) -> "datetime | None":
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str:
    v = _naive(value)
    return v.replace(tzinfo=timezone.utc).isoformat() if v else ""


def _safe(text: Any, cap: int = 200) -> str:
    """Masked and scanned like the work block (``block._safe``): "" when it
    reads as an instruction or cannot be masked."""
    from ..workmap import block as B

    return B._safe(B._clip(str(text or ""), cap)).strip()


def _day(when: Any) -> str:
    from ..workmap.block import date_words

    return date_words(_naive(when))


def _days(since: Any, now: datetime) -> int:
    then = _naive(since)
    return max(0, (now - then).days) if then else 0


def _fact(text: str, *, link: str = "", at: Any = None) -> "dict[str, str] | None":
    words = _safe(text, 200)
    if not words:
        return None
    return {"text": words, "link": link if link.startswith("/") and not link.startswith("//") else "",
            "at": _iso(at)}


def _facts(*facts: "dict[str, str] | None") -> list[dict[str, str]]:
    return [f for f in facts if f][:EVIDENCE_MAX]


def _forgotten_keys(db: Any) -> list[str]:
    from ..workmap.models import WorkForgotten

    return [str(k) for k in db.exec(select(WorkForgotten.folder_key)) if k]


def _is_forgotten(folder_key: str, forgotten: list[str]) -> bool:
    from ..workmap.index import covered

    return bool(folder_key) and any(covered(folder_key, f) for f in forgotten)


def _files_line(row: dict[str, Any]) -> str:
    from ..workmap import keys

    names = [keys.file_name(f["path"]) for f in (row.get("files") or [])][:_FILES_SHOWN]
    names = [n for n in names if n]
    if not names:
        return ""
    more = int(row.get("files_total") or len(names)) - len(names)
    return "Files: " + ", ".join(names) + (f" and {more} more" if more > 0 else "")


def _folder_name(folder: str) -> str:
    from ..workmap import keys

    return keys.folder_name(folder) or folder


def _latest_in(db: Any, folder_keys: set[str], *, besides: str = "") -> "datetime | None":
    """The newest work in those folders (``besides`` left out)."""
    from ..workmap.models import WorkEntry as W

    keys_ = sorted(k for k in folder_keys if k)
    if not keys_:
        return None
    q = select(func.max(W.updated_at)).where(W.folder_key.in_(keys_))  # type: ignore[attr-defined]
    if besides:
        q = q.where(W.id != besides)
    return _naive(db.exec(q).one())


def _live(engine: Any, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Rows whose SOURCE still exists, as they are now (deleted ones left
    out; RAISES when the sources cannot be asked)."""
    from ..workmap.lookup import as_it_is_now

    return {r["id"]: r for r in as_it_is_now(engine, rows)}


@dataclass
class Candidate:
    """One suggestion a reader found (not stored yet)."""

    kind: str
    subject_key: str
    folder: str
    title: str
    evidence: list[dict[str, str]] = field(default_factory=list)
    rationale: str = ""
    local_only: bool = False
    how: str = HOW_CHAT
    task: str = ""
    agent_type: str = "builder"
    risk: str = "low"
    producer: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    order: datetime = field(default_factory=lambda: datetime.min)


# --------------------------------------------------------------------------- #
# the readers (pure reads, no model call)
# --------------------------------------------------------------------------- #
def _refresh_record(platform: Any) -> None:
    """Bring the work record up to date (bounded) and carry recaps into open
    items, as every reader of the record does. A failure is logged: the
    readers below read what is there."""
    from ..workmap import open_items
    from ..workmap.index import work_map
    from ..workmap.lookup import TURN_REBUILD

    engine = platform.engine
    home = getattr(_cfg(platform), "home", None)
    try:
        wm = work_map(engine, home)
        if wm is not None:
            # Past the reader's throttle (a pass comes every 4 hours), still
            # bounded: at most TURN_REBUILD rows rebuilt inline.
            wm.refresh(force=True, budget=TURN_REBUILD)
    except Exception:  # noqa: BLE001
        log.debug("work notices: the work record could not be refreshed", exc_info=True)
    try:
        open_items.backfill(engine)
    except Exception:  # noqa: BLE001
        log.debug("work notices: open items could not be carried", exc_info=True)


def open_item_candidates(platform: Any, now: datetime) -> list[Candidate]:
    """``open_item``: open :data:`OPEN_DAYS` days or more, no work in its
    folder for :data:`OPEN_QUIET_DAYS` days. The same words in two
    conversations of one folder are ONE item (the oldest)."""
    from ..workmap import block as B
    from ..workmap.index import _row, link_for
    from ..workmap.models import OPEN_ITEM_OPEN, WorkEntry, WorkOpenItem

    engine = platform.engine
    out: list[Candidate] = []
    with session_scope(engine) as db:
        items = list(db.exec(
            select(WorkOpenItem)
            .where(WorkOpenItem.status == OPEN_ITEM_OPEN)
            .where(WorkOpenItem.opened_at <= now - timedelta(days=OPEN_DAYS))  # type: ignore[operator]
            .order_by(WorkOpenItem.opened_at, WorkOpenItem.id)  # type: ignore[arg-type]
            .limit(READ_MAX)
        ))
        if not items:
            return out
        forgotten = _forgotten_keys(db)
        entries = {}
        for e in db.exec(select(WorkEntry).where(WorkEntry.id.in_(sorted({i.entry_id for i in items})))):  # type: ignore[attr-defined]
            entries[e.id] = _row(e)
        latest: dict[str, "datetime | None"] = {}
        for it in items:
            row = entries.get(it.entry_id)
            if row is None or it.folder_key in latest:
                continue
            latest[it.folder_key] = _latest_in(db, {it.folder_key, row["folder_key"]})
        for it in items:
            db.expunge(it)
    live = _live(engine, list(entries.values()))
    seen: set[tuple[str, str]] = set()
    for it in items:
        twin = (it.folder_key, it.text_key or it.text)
        if twin in seen:
            continue
        seen.add(twin)
        row = live.get(it.entry_id)
        if row is None or row["archived"]:
            continue
        if _is_forgotten(row["folder_key"], forgotten) or _is_forgotten(it.folder_key, forgotten):
            continue
        last = latest.get(it.folder_key)
        if last is not None and last > now - timedelta(days=OPEN_QUIET_DAYS):
            continue
        text = _safe(it.text, _TEXT_CHARS)
        work = _safe(row["title"], B.TITLE_CHARS)
        folder = it.folder or row["folder"]
        days = _days(it.opened_at, now)
        title = _safe(f"“{text}” has been open for {days} days in {_folder_name(folder)}. "
                      "Pick it up?", 240) if text and work else ""
        if not title:
            continue  # a line or a title that reads as an instruction is never suggested
        out.append(Candidate(
            kind=KIND_OPEN, subject_key=f"open:{it.id}", folder=folder, title=title,
            evidence=_facts(
                _fact(f"From: {work} ({B.KIND_WORDS.get(row['kind'], 'work')}, "
                      f"{_day(row['updated'])})", link=link_for(row["kind"], row["ref"]), at=row["updated"]),
                _fact(f"Open since {_day(it.opened_at)}", at=it.opened_at),
                _fact(f"No work in {_folder_name(folder)} since {_day(last)}", at=last) if last else None,
                _fact(_files_line(row)) if _files_line(row) else None,
            ),
            rationale=f"An open item in {_folder_name(folder)} has waited {days} days.",
            local_only=bool(row["local_only"] or row["keep_home"] or it.local_only),
            how=HOW_CHAT, extra={"item_id": it.id, "entry_id": it.entry_id},
            order=_naive(it.opened_at) or datetime.min,
        ))
    return out


def stalled_candidates(platform: Any, now: datetime) -> list[Candidate]:
    """``stalled_work``: ended failed / needs you / part failed, nothing
    newer in its folder for :data:`STALLED_QUIET_DAYS` days, at most
    :data:`STALLED_MAX_DAYS` days old."""
    from ..workmap import block as B
    from ..workmap.index import _row, link_for
    from ..workmap.models import WorkEntry as W

    engine = platform.engine
    out: list[Candidate] = []
    with session_scope(engine) as db:
        rows = [_row(e) for e in db.exec(
            select(W)
            .where(W.outcome.in_(STALLED_OUTCOMES))  # type: ignore[attr-defined]
            .where(W.archived == False)  # noqa: E712
            .where(W.updated_at >= now - timedelta(days=STALLED_MAX_DAYS))  # type: ignore[operator]
            .where(W.updated_at <= now - timedelta(days=STALLED_QUIET_DAYS))  # type: ignore[operator]
            .order_by(W.updated_at, W.id)  # type: ignore[arg-type]
            .limit(READ_MAX)
        )]
        if not rows:
            return out
        forgotten = _forgotten_keys(db)
        newer: dict[str, "datetime | None"] = {
            r["id"]: _latest_in(db, {r["folder_key"]}, besides=r["id"]) for r in rows
        }
    live = _live(engine, rows)
    for r in rows:
        row = live.get(r["id"])
        if row is None or row["archived"]:
            continue
        if _is_forgotten(row["folder_key"], forgotten):
            continue
        ended_at = _naive(row["updated"])
        later = newer.get(r["id"])
        if later is not None and ended_at is not None and later >= ended_at:
            continue  # picked up again: newer work in that folder
        work = _safe(row["title"], B.TITLE_CHARS)
        ended = B.OUTCOME_WORDS.get(row["outcome"], "did not finish")
        folder = row["folder"]
        title = _safe(f"“{work}” {ended} on {_day(ended_at)} and was not picked up again. "
                      "Look at it?", 240) if work else ""
        if not title:
            continue
        note = _safe(row.get("note") or "", 160)
        out.append(Candidate(
            kind=KIND_STALLED, subject_key=f"stalled:{row['id']}", folder=folder, title=title,
            evidence=_facts(
                _fact(f"{B.KIND_WORDS.get(row['kind'], 'work').capitalize()} in {_folder_name(folder)}: {work}",
                      link=link_for(row["kind"], row["ref"]), at=row.get("started")),
                _fact(f"Ended: {ended}, {_day(ended_at)}", at=ended_at),
                _fact(f"Nothing newer in {_folder_name(folder)} since then"),
                _fact(f"It said: {note}") if note else (_fact(_files_line(row)) if _files_line(row) else None),
            ),
            rationale=f"Work in {_folder_name(folder)} {ended} and nothing newer happened there.",
            local_only=bool(row["local_only"] or row["keep_home"]),
            how=HOW_CHAT, extra={"entry_id": row["id"]},
            order=ended_at or datetime.min,
        ))
    return out


#: The record readers a pass asks, in order (``new_files`` comes from the
#: watcher's own sweep, :func:`mint_watcher`).
READERS: tuple[tuple[str, Callable[[Any, datetime], list[Candidate]]], ...] = (
    (KIND_OPEN, open_item_candidates),
    (KIND_STALLED, stalled_candidates),
)


def candidates(platform: Any, now: "datetime | None" = None) -> list[Candidate]:
    """Every suggestion the record supports now, kind by kind (a kind that is
    switched off is not read). Pure reads; RAISES when the record cannot be
    read (the pass's health row says so)."""
    when = _naive(now) or _now()
    out: list[Candidate] = []
    for kind, reader in READERS:
        if kind_on(platform, kind):
            out += reader(platform, when)
    return out


# --------------------------------------------------------------------------- #
# withdraw what is no longer true
# --------------------------------------------------------------------------- #
def _still_true(platform: Any, db: Any, p: ProposalRecord, forgotten: list[str]) -> bool:
    from ..workmap import keys
    from ..workmap.index import _row
    from ..workmap.models import OPEN_ITEM_OPEN, WorkEntry, WorkOpenItem

    if not kind_on(platform, p.kind):
        return False
    subject = p.subject_key or ""
    if p.folder and _is_forgotten(keys.folder_key(p.folder), forgotten):
        return False
    if p.kind == KIND_FILES:
        from ..sentinels.models import SentinelRecord

        wid = str(p.decoded_action().get("watcher") or "")
        w = db.get(SentinelRecord, wid) if wid else None
        return w is not None and bool(w.enabled)
    if p.kind == KIND_OPEN and subject.startswith("open:"):
        it = db.get(WorkOpenItem, subject[len("open:"):])
        if it is None or it.status != OPEN_ITEM_OPEN:
            return False
        e = db.get(WorkEntry, it.entry_id)
        if e is None or e.archived:
            return False
        rows = _live(platform.engine, [_row(e)])
        if not rows or next(iter(rows.values()))["archived"]:
            return False
        last = _latest_in(db, {it.folder_key, e.folder_key})
        made = _naive(p.created_at)
        return not (last is not None and made is not None and last > made)
    if p.kind == KIND_STALLED and subject.startswith("stalled:"):
        e = db.get(WorkEntry, subject[len("stalled:"):])
        if e is None or e.archived or (e.outcome or "") not in STALLED_OUTCOMES:
            return False
        rows = _live(platform.engine, [_row(e)])
        if not rows or next(iter(rows.values()))["archived"]:
            return False
        later = _latest_in(db, {e.folder_key}, besides=e.id)
        ended = _naive(e.updated_at)
        return not (later is not None and ended is not None and later >= ended)
    return False


def withdraw_stale(platform: Any, now: "datetime | None" = None) -> int:
    """Withdraw every WAITING suggestion from the record that is no longer
    true (see the module docstring). Returns how many. A row that cannot be
    checked is left as it is. RAISES when the rows cannot be read."""
    when = _naive(now) or _now()
    done = 0
    with session_scope(platform.engine) as db:
        rows = list(db.exec(
            select(ProposalRecord)
            .where(ProposalRecord.source == SOURCE)
            .where(ProposalRecord.status == "pending")
        ))
        forgotten = _forgotten_keys(db)
        for p in rows:
            try:
                if _still_true(platform, db, p, forgotten):
                    continue
            except Exception:  # noqa: BLE001 — unsure: leave it as it is
                log.debug("work notices: one suggestion could not be checked", exc_info=True)
                continue
            p.status = "withdrawn"
            p.decided_at = when
            db.add(p)
            done += 1
        if done:
            db.commit()
    return done


def withdraw_kind(platform: Any, kind: str, now: "datetime | None" = None) -> int:
    """Withdraw every waiting suggestion of ``kind`` (its Stop answer)."""
    when = _naive(now) or _now()
    with session_scope(platform.engine) as db:
        rows = list(db.exec(
            select(ProposalRecord)
            .where(ProposalRecord.source == SOURCE)
            .where(ProposalRecord.kind == kind)
            .where(ProposalRecord.status == "pending")
        ))
        for p in rows:
            p.status = "withdrawn"
            p.decided_at = when
            db.add(p)
        if rows:
            db.commit()
    return len(rows)


# --------------------------------------------------------------------------- #
# minting
# --------------------------------------------------------------------------- #
def made_today(engine: Any, now: "datetime | None" = None) -> int:
    """New suggestions from the record in the last 24 hours, counted from the
    ROWS (every status: a withdrawn or answered one was still shown)."""
    when = _naive(now) or _now()
    with session_scope(engine) as db:
        return int(db.exec(
            select(func.count()).select_from(ProposalRecord)
            .where(ProposalRecord.source == SOURCE)
            .where(ProposalRecord.created_at >= when - DAY)  # type: ignore[operator]
        ).one() or 0)


def answered(engine: Any, subject_key: str) -> bool:
    """Was this subject EVER answered (a No is final for that subject)?"""
    with session_scope(engine) as db:
        return db.exec(
            select(ProposalRecord.id)
            .where(ProposalRecord.subject_key == subject_key)
            .where(ProposalRecord.status.in_(ANSWERED))  # type: ignore[attr-defined]
        ).first() is not None


def _waiting(engine: Any, subject_key: str) -> "ProposalRecord | None":
    with session_scope(engine) as db:
        return db.exec(
            select(ProposalRecord)
            .where(ProposalRecord.subject_key == subject_key)
            .where(ProposalRecord.status == "pending")
            .where(ProposalRecord.source == SOURCE)
        ).first()


def mint(platform: Any, cand: Candidate, now: "datetime | None" = None, *,
         intent: Any = None) -> "tuple[ProposalRecord | None, bool]":
    """Store one candidate through ``intent.add_backlog(source="notice")``
    when the rules allow (module docstring). ``(row, new)``: the row it was
    folded into or made, and whether it is NEW (counted). ``(None, False)``
    when a rule holds it back. Never raises."""
    intent = intent if intent is not None else getattr(platform, "intent", None)
    if intent is None:
        return None, False
    when = _naive(now) or _now()
    try:
        with _LOCK:
            if not kind_on(platform, cand.kind):
                return None, False
            if answered(platform.engine, cand.subject_key):
                return None, False
            waiting = _waiting(platform.engine, cand.subject_key)
            if waiting is None and made_today(platform.engine, when) >= day_limit(platform):
                log.info("work notices: today's limit is reached; a %s suggestion waits", cand.kind)
                return None, False
            row = intent.add_backlog(
                title=cand.title, task=cand.task, risk=cand.risk, source=SOURCE,
                agent_type=cand.agent_type, rationale=cand.rationale, producer=cand.producer,
                kind=cand.kind, subject_key=cand.subject_key, folder=cand.folder,
                evidence=cand.evidence, local_only=cand.local_only,
                action_extra={"how": cand.how, **cand.extra},
            )
            return row, row is not None and waiting is None
    except Exception:  # noqa: BLE001 — a suggestion never breaks its caller
        log.warning("work notices: a %s suggestion could not be stored", cand.kind, exc_info=True)
        return None, False


def pass_once(platform: Any, now: "datetime | None" = None) -> dict[str, Any]:
    """ONE pass: withdraw what is no longer true, then read the record and
    store at most :data:`PER_PASS` new suggestions (oldest first, kind by
    kind), within the day's limit. Nothing under Emergency stop. BLOCKING.
    RAISES when the record cannot be read (the loop's health row)."""
    when = _naive(now) or _now()
    out: dict[str, Any] = {"withdrawn": 0, "made": 0, "ran": False}
    if stopped(platform):
        return out
    out["ran"] = True
    if master_on(platform):
        # The record as it is now (a deleted chat, a run that finished
        # since), so the withdraw step and the readers see the same truth.
        _refresh_record(platform)
    out["withdrawn"] = withdraw_stale(platform, when)
    if not master_on(platform):
        return out
    made = 0
    for cand in candidates(platform, when):
        if made >= PER_PASS:
            break
        row, new = mint(platform, cand, when)
        if row is not None and new:
            made += 1
    out["made"] = made
    return out


# --------------------------------------------------------------------------- #
# a watcher's new files (``sentinels/service._propose``)
# --------------------------------------------------------------------------- #
def mint_watcher(platform: Any, rec: Any, changed: list[dict], now: "datetime | None" = None, *,
                 intent: Any = None) -> "ProposalRecord | None":
    """A folder watcher saw files arrive: ONE ``new_files`` suggestion, the
    names and "since when" as its facts, through :func:`mint` (the switches,
    the day's limit). Changes seen while it still waits are folded into it.
    Nothing for a forgotten folder. Never raises."""
    from ..workmap import keys

    when = _naive(now) or _now()
    try:
        cfg = rec.decoded_config() if hasattr(rec, "decoded_config") else {}
        folder = str((cfg or {}).get("path") or "").strip()
        fkey = keys.folder_key(folder) if folder else f"watcher:{rec.id}"
        with session_scope(platform.engine) as db:
            if folder and _is_forgotten(fkey, _forgotten_keys(db)):
                return None
            waiting = db.exec(
                select(ProposalRecord)
                .where(ProposalRecord.source == SOURCE)
                .where(ProposalRecord.kind == KIND_FILES)
                .where(ProposalRecord.status == "pending")
                .where(ProposalRecord.subject_key.startswith(f"files:{fkey}#", autoescape=True))  # type: ignore[attr-defined]
            ).first()
            before = waiting.decoded_evidence() if waiting is not None else []
            subject = waiting.subject_key if waiting is not None else f"files:{fkey}#{when:%Y%m%d%H%M%S}"
        names_before: list[str] = []
        since = when
        for f in before:
            if f.get("role") == "since":
                try:
                    since = _naive(datetime.fromisoformat(str(f.get("at") or ""))) or since
                except ValueError:
                    pass
            if f.get("role") == "files" and isinstance(f.get("names"), list):
                names_before = [str(n) for n in f["names"] if n]
        names = [keys.file_name(c.get("path")) or str(c.get("path") or "") for c in changed or []]
        # Every name masked and scanned as it is kept (a name that reads as
        # an instruction is left out of the facts, never told).
        names = [m for m in (_safe(n, 120) for n in names) if m]
        all_names = list(dict.fromkeys([*names, *names_before]))  # newest first, once each
        shown = all_names[:_FILES_SHOWN]
        more = len(all_names) - len(shown)
        n = len(all_names)
        name = _safe(getattr(rec, "name", "") or "", 60) or "your watcher"
        where = _folder_name(folder) if folder else name
        title = _safe(f"Your watcher {name} saw {n} new or changed file{'' if n == 1 else 's'} in {where}", 200)
        if not title:
            return None
        files_fact = _fact("New or changed: " + ", ".join(shown) + (f" and {more} more" if more > 0 else ""))             if shown else None
        if files_fact is not None:
            files_fact.update({"role": "files", "names": all_names[:50]})  # type: ignore[dict-item]
        since_fact = _fact(f"Since {_day(since)}", at=since)
        if since_fact is not None:
            since_fact["role"] = "since"
        sample = ", ".join(str(c.get("path") or "") for c in (changed or [])[:5])
        extra = "" if len(changed or []) <= 5 else f" (+{len(changed) - 5} more)"
        rationale = _safe(f"Changed: {sample}{extra}.", 600) or f"Files changed in {where}."
        cand = Candidate(
            kind=KIND_FILES, subject_key=subject, folder=folder, title=title,
            evidence=_facts(
                files_fact,
                since_fact,
                _fact(f"Watched folder: {where}", link="/sentinels") if folder else None,
            ),
            rationale=rationale, local_only=False, how=HOW_RUN,
            task=str(getattr(rec, "task", "") or ""),
            agent_type=str(getattr(rec, "agent_type", "") or "builder"),
            risk=str(getattr(rec, "risk", "") or "low"),
            producer=str(getattr(rec, "name", "") or ""),
            extra={"watcher": str(rec.id)},
        )
        row, _new = mint(platform, cand, when, intent=intent)
        return row
    except Exception:  # noqa: BLE001 — a watcher's sweep never breaks on a suggestion
        log.warning("work notices: a watcher's suggestion could not be made", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# what a Yes does
# --------------------------------------------------------------------------- #
CHAT_DOOR = "/chat?new=1&folder="


def door_for(p: ProposalRecord) -> str:
    """A Yes to a ``chat`` suggestion: a NEW chat in its folder (the chat page
    sets the folder and sends nothing)."""
    folder = (p.folder or "").strip()
    return CHAT_DOOR + quote(folder, safe="") if folder else "/chat?new=1"


def run_task(p: ProposalRecord) -> str:
    """A Yes to a ``run`` suggestion: the watcher's own task (the owner's
    words) or :data:`RUN_SENTENCE`, then the facts FENCED as data."""
    from ..computeruse.safety import wrap_untrusted

    head = str(p.decoded_action().get("task") or "").strip() or RUN_SENTENCE
    facts = "\n".join(str(f.get("text") or "") for f in p.decoded_evidence() if f.get("text"))
    if not facts:
        return head
    return f"{head}\n\n{_FACTS_HEAD}\n{wrap_untrusted(facts)}"


# --------------------------------------------------------------------------- #
# the background pass
# --------------------------------------------------------------------------- #
async def notices_loop(
    platform: Any,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[Any]],
    on_tick: "Callable[[bool, BaseException | None], Any] | None" = None,
    on_off: "Callable[[], Any] | None" = None,
) -> None:
    """``bg_tasks['work_notices']``: the first pass :data:`FIRST_PASS_S`
    after start, then every :data:`EVERY_S`, each through
    ``asyncio.to_thread(pass_once, platform)``. Time comes only from
    ``clock`` and ``sleep``. Emergency stop: no pass (and no row change).
    Switched off: what waits is withdrawn and ``on_off`` pops the row. Never
    raises (a cancel ends it)."""
    due = clock() + FIRST_PASS_S
    while True:
        try:
            await sleep(max(0.0, due - clock()))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a broken sleep must not spin
            log.warning("work notices: the loop's sleep failed; it stops", exc_info=True)
            return
        if clock() < due:
            continue  # woke early (Windows timers): wait out the rest
        due = clock() + EVERY_S
        try:
            if stopped(platform):
                continue
            if not master_on(platform):
                await asyncio.to_thread(withdraw_stale, platform)
                if on_off is not None:
                    on_off()
                continue
            await asyncio.to_thread(pass_once, platform)
            if on_tick is not None:
                on_tick(True, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed pass is a row, not a crash
            log.debug("work notices: a pass failed", exc_info=True)
            try:
                if on_tick is not None:
                    on_tick(False, exc)
            except Exception:  # noqa: BLE001
                pass


def kind_words(kind: str) -> str:
    return KIND_WORDS.get(kind, "suggestions like this")


__all__ = [
    "ANSWERED", "Candidate", "DAY", "EVERY_S", "FIRST_PASS_S", "HOW_CHAT", "HOW_RUN", "KINDS", "KIND_FILES",
    "KIND_OPEN", "KIND_STALLED", "KIND_WORDS", "MASTER", "PER_PASS", "READERS", "RUN_SENTENCE", "SETTING_OF",
    "SOURCE", "answered", "candidates", "day_limit", "door_for", "kind_on", "kind_words", "made_today",
    "master_on", "mint", "mint_watcher", "notices_loop", "open_item_candidates", "pass_once", "run_task",
    "stalled_candidates", "withdraw_kind", "withdraw_stale",
]
