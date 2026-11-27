"""IT OFFERS TO RUN BY ITSELF (next leaps 19-b, build-out plan R4.37, idea 19).

When the three times a task was asked for in chat (19-a's repeat, a skill
draft written from those chats) fell on a RHYTHM (``learning/repeats.
rhythm``: the same weekday in consecutive weeks, or the same day of the
month), the night review makes ONE offer beside the skill draft:
"Run this by itself each Monday at 9am?". It waits in Improvements under
"Tasks Iron Jarvis could run on a schedule".

THE RULES (each pinned in ``tests/test_next_leaps_19b_offer.py``):

* NOTHING IS MADE OR RUN UNASKED (the plan's calm rule for R4.37): the night
  review only writes this row (no model, no schedule, no rehearsal). The
  schedule is made only when the owner presses Accept (two presses: it
  starts work by itself), and a rehearsal runs only when they press
  Rehearse (06-a's own rehearsal, ``tools/rehearsal_runs`` kind ``offer``;
  never a second one).
* THE SCHEDULE'S WORDS ARE THE OWNER'S: ``words`` is the owner's own request
  from the newest of the tasks, read from its chat (masked), never a model's
  words: a schedule's words are read as the owner's on every run. The skill
  draft rides the schedule's ``skills`` knob (only once it is a skill), never
  its words.
* THE RUN USES WHAT THE CHATS USED: the newest task's provider, model AND
  account (19-a's evidence), never a vendor's Default or another account. An
  offer is never made without a model. An account gone or turned off is
  refused at Accept and at Rehearse (``scheduling/checks``, the same rule as
  every door that saves a schedule) and again at each fire (the platform's
  ``pin_for``): the run does not happen and says why.
* LOCAL ONLY STAYS LOCAL ONLY: when any of the tasks was in a Local only
  chat or answered by a model on the owner's PC or network (19-a's
  ``_home_if_here``; unsure = home), the schedule's payload carries
  ``local_only`` and EVERY fire, and the rehearsal, runs Local only
  (``platform._dispatch_scheduled``), whatever becomes of the chat it
  reports to (the newest of the chats, ``chat_thread_id``, idea 39).
* A DELETED CHAT takes its offer with it (``drop_thread``), and an offer is
  made only from tasks still in their chats, read again (``repeats.reread``):
  19-a's draft keeps none of the owner's words.
* DECLINE IS FINAL for that task: the row is ``declined``, and no offer is
  made again for its skill draft or for a request like it.
* ONE ADD-TIME RULE: Accept saves through ``scheduling/checks.check_payload``,
  the rules ``POST /schedules`` applies.

``kind`` is ``task`` (a schedule that asks a model to do the task again each
time). The intelligence plan's release 11 adds its stronger form beside it
(fixed steps with no model, when the steps were the same each time).

Every function here is BLOCKING (SQLite): async callers hop it off the loop.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow

log = logging.getLogger(__name__)

WAITING = "waiting"
ACCEPTED = "accepted"
DECLINED = "declined"
STATUSES = (WAITING, ACCEPTED, DECLINED)
#: 19-b: a schedule that asks a model to do the task each time.
KIND_TASK = "task"

#: Chat skill drafts looked at in one pass, and offers made at most.
LOOK = 20
OFFERS_MAX = 2
#: A schedule's name, at most (the Schedules page's own bound is wider).
NAME_MAX = 60


class ScheduleOfferRecord(SQLModel, table=True):
    """One offer to run a repeated task by itself on its rhythm."""

    __tablename__ = "scheduleoffer"

    id: str = Field(default_factory=lambda: new_id("offer"), primary_key=True)
    #: The name the schedule gets (the skill draft's name).
    title: str = ""
    #: The owner's own request, as 19-a kept it (masked): the schedule's task.
    words: str = ""
    #: The schedule's crontab (this PC's zone), and the rhythm it came from
    #: (``{"kind", "hour", "weekday", "day"}``, ``learning/repeats.Rhythm``).
    cadence: str = ""
    rhythm_json: str = "{}"
    #: The folder the chats worked in ("" = none, or not one folder).
    folder: str = ""
    skill_name: str = ""
    #: The skill draft this offer sits beside (one offer per draft).
    proposal_id: str = Field(default="", index=True)
    #: The draft's evidence (19-a's ``evidence_of``), carried whole.
    evidence_json: str = "{}"
    provider: str = ""
    model: str = ""
    account: str = ""
    #: True when any of the chats was Local only.
    local_only: bool = False
    #: The newest of the chats: the schedule reports there (idea 39).
    chat_thread_id: str = ""
    kind: str = KIND_TASK
    #: The newest rehearsal of this offer ("" = never rehearsed).
    rehearsal_id: str = ""
    #: The schedule Accept made.
    schedule_name: str = ""
    status: str = Field(default=WAITING, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None


class OfferRefused(Exception):
    """An answer that cannot be done, in words for a person; ``status`` is
    the HTTP code the route answers with."""

    def __init__(self, words: str, status: int = 409) -> None:
        super().__init__(words)
        self.words = words
        self.status = status


# --------------------------------------------------------------------------- #
# reading one
# --------------------------------------------------------------------------- #
def get(engine: Any, offer_id: str) -> "ScheduleOfferRecord | None":
    with session_scope(engine) as db:
        row = db.get(ScheduleOfferRecord, str(offer_id or ""))
        if row is not None:
            db.expunge(row)
        return row


def rhythm_of(row: Any) -> Any:
    """The offer's :class:`learning.repeats.Rhythm`, or None when its record
    cannot be read."""
    from ..learning.repeats import Rhythm

    try:
        got = json.loads(getattr(row, "rhythm_json", "") or "{}")
        if not isinstance(got, dict):
            return None
        return Rhythm(kind=str(got["kind"]), hour=int(got["hour"]), weekday=int(got.get("weekday", -1)),
                      day=int(got.get("day", 0)))
    except (KeyError, TypeError, ValueError):
        return None


def evidence(row: Any) -> dict[str, Any]:
    from ..learning.repeats import load_evidence

    return load_evidence(getattr(row, "evidence_json", ""))


def newest_day(row: Any) -> str:
    """"17 November": the day of the newest chat (whose model, account and
    chat the schedule uses). "" when the evidence holds none."""
    from ..learning.repeats import day_of, days_words

    units = [u for u in evidence(row).get("units") or [] if isinstance(u, dict)]
    if not units:
        return ""
    try:
        return days_words([day_of(float(units[0].get("start") or 0.0)).isoformat()])
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


# --------------------------------------------------------------------------- #
# making offers (the night review's ``schedule_offer`` row; no model)
# --------------------------------------------------------------------------- #
def _chat_folders(engine: Any, thread_ids: list[str]) -> dict[str, str]:
    from ..core.models import ChatThreadRecord as T
    from ..harness.sessions import folder_of

    if not thread_ids:
        return {}
    with session_scope(engine) as db:
        rows = list(db.exec(select(T.id, T.setup_json).where(T.id.in_(thread_ids))))  # type: ignore[attr-defined]
    return {str(tid): folder_of(SimpleNamespace(setup_json=setup or "")) for tid, setup in rows}


def _scheduled_already(engine: Any, words: str) -> bool:
    """A schedule's task already says this (19-a's ``ALREADY_SAVED``)."""
    from ..templates import ALREADY_SAVED, similarity, task_tokens
    from .models import ScheduledTaskRecord

    mine = task_tokens(words)
    with session_scope(engine) as db:
        payloads = [str(p or "") for p in db.exec(select(ScheduledTaskRecord.payload_json))]
    for raw in payloads:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        task = payload.get("task") if isinstance(payload, dict) else None
        if isinstance(task, str) and similarity(mine, task_tokens(task)) >= ALREADY_SAVED:
            return True
    return False


def _declined_like(engine: Any, words: str) -> bool:
    """The owner declined an offer for a request like this one (Decline is
    final for that task)."""
    from ..templates import SAME_TASK, similarity, task_tokens

    mine = task_tokens(words)
    with session_scope(engine) as db:
        said = [str(w or "") for w in db.exec(
            select(ScheduleOfferRecord.words).where(ScheduleOfferRecord.status == DECLINED)
        )]
    return any(similarity(mine, task_tokens(w)) >= SAME_TASK for w in said)


def offer_for(platform: Any, prop: Any, *, tz: Any = None) -> "tuple[ScheduleOfferRecord | None, str]":
    """The offer a chat skill draft ``prop`` would get, and why not when it
    gets none (no row is written here). The draft keeps none of the owner's
    words (19-a), so its tasks are READ AGAIN from their chats
    (``repeats.reread``): a deleted chat's task is not counted, a task whose
    reply now reads low trust ends it, and the words are the newest task's
    own line from its chat. RAISES when a table cannot be read."""
    from ..learning import repeats as R

    engine = platform.engine
    got = R.load_evidence(getattr(prop, "evidence_json", ""))
    if not [u for u in got.get("units") or [] if isinstance(u, dict)]:
        return None, "its record holds no tasks"
    found = R.reread(engine, got)
    if found is None:
        raise RuntimeError("the chats of a skill draft could not be read")
    units = sorted((u for u, _lines in found), key=lambda u: -u.start)
    if len(units) < R.MIN_UNITS:
        return None, "fewer than three of its tasks are still in their chats"
    if any(u.low_trust or u.remote for u in units):
        return None, "one of its tasks read something from outside"
    rh = R.rhythm([u.start for u in units], tz=tz)
    if rh is None:
        return None, "the times it was asked for fell on no rhythm"
    newest = units[0]
    words = R._masked(" ".join(newest.words.split()))[:2000]
    if not words:
        return None, "the request's words were not kept"
    provider, model = newest.provider.strip(), newest.model.strip()
    if not provider or not model:
        # Never the vendor's Default in place of the model the chats used.
        return None, "the model the chats used is not on record"
    # A task in a Local only chat, or one a model on the owner's own PC or
    # network answered, keeps the schedule's runs home (19-a's rule).
    local_only = any(R._home_if_here(platform, u).local_only for u in units)
    if _scheduled_already(engine, words):
        return None, "a schedule already does this"
    if _declined_like(engine, words):
        return None, "you declined to run this by itself before"
    folders = _chat_folders(engine, list(dict.fromkeys(u.ref for u in units)))
    seen = {folders.get(u.ref, "") for u in units}
    folder = seen.pop() if len(seen) == 1 else ""
    name = str(getattr(prop, "skill_name", "") or "").strip()
    return ScheduleOfferRecord(
        title=name[:NAME_MAX] or "repeated-task", words=words, cadence=rh.cron,
        rhythm_json=json.dumps({"kind": rh.kind, "hour": rh.hour, "weekday": rh.weekday, "day": rh.day}),
        folder=folder, skill_name=name, proposal_id=str(prop.id),
        # The tasks just read, with none of the owner's words (19-a's draft rule).
        evidence_json=json.dumps(R.evidence_of(R.group_of(units), words=False)),
        provider=provider, model=model, account=newest.account, local_only=local_only,
        chat_thread_id=newest.ref, kind=KIND_TASK,
    ), ""


def make_offers(platform: Any, *, tz: Any = None) -> list[ScheduleOfferRecord]:
    """The night review's step (no model, no schedule, no rehearsal): one
    offer for each chat skill draft (waiting or approved) that has none yet
    and whose tasks fell on a rhythm, at most :data:`OFFERS_MAX`. Returns
    the offers made. BLOCKING; RAISES when a table cannot be read."""
    from ..skills.learning_models import SOURCE_CHAT, SkillProposalRecord as P

    engine = platform.engine
    with session_scope(engine) as db:
        offered = select(ScheduleOfferRecord.proposal_id)
        props = list(db.exec(
            select(P).where(P.source == SOURCE_CHAT, P.status.in_(["pending", "approved"]),  # type: ignore[attr-defined]
                            P.id.not_in(offered))  # type: ignore[attr-defined]
            .order_by(P.created_at.desc()).limit(LOOK)  # type: ignore[attr-defined]
        ))
        for p in props:
            db.expunge(p)
    made: list[ScheduleOfferRecord] = []
    for prop in props:
        if len(made) >= OFFERS_MAX:
            break
        offer, why = offer_for(platform, prop, tz=tz)
        if offer is None:
            log.debug("schedule offers: none for %s: %s", prop.id, why)
            continue
        with session_scope(engine) as db:
            db.add(offer)
            db.commit()
            db.refresh(offer)
            db.expunge(offer)
        made.append(offer)
    return made


def drop_thread(db: Any, thread_id: str) -> int:
    """A chat was DELETED (``routes/chat.delete_thread``, inside its own
    transaction, beside 19-a's ``repeats.drop_thread``): an offer still
    waiting that was made from it goes, its words with it; a declined one
    keeps no words (its draft still gets no new offer). A schedule already
    made is the owner's and stays. How many offers changed. RAISES."""
    from ..learning.repeats import load_evidence

    tid = str(thread_id or "")
    changed = 0
    for row in list(db.exec(select(ScheduleOfferRecord).where(ScheduleOfferRecord.status != ACCEPTED))):
        threads = {str(u.get("thread") or "") for u in load_evidence(row.evidence_json).get("units") or []
                   if isinstance(u, dict)}
        if tid not in threads and row.chat_thread_id != tid:
            continue
        if row.status == WAITING:
            db.delete(row)
        else:
            row.words = ""
            db.add(row)
        changed += 1
    return changed


# --------------------------------------------------------------------------- #
# what Accept and Rehearse run
# --------------------------------------------------------------------------- #
def _skill_kept(platform: Any, name: str) -> bool:
    skills = getattr(platform, "skills", None)
    if skills is None or not name:
        return False
    try:
        return skills.get(name) is not None
    except Exception:  # noqa: BLE001 — unreadable: the schedule goes without it
        return False


def payload_of(platform: Any, row: ScheduleOfferRecord) -> dict[str, Any]:
    """The schedule's payload: the owner's words, the chats' provider, model
    and account, the skill once it is one, the chats' folder."""
    payload: dict[str, Any] = {"task": row.words, "provider": row.provider, "model": row.model}
    if row.account:
        payload["account"] = row.account
    if _skill_kept(platform, row.skill_name):
        payload["skills"] = [row.skill_name]
    if row.folder:
        payload["workspace_root"] = row.folder
    if row.local_only:
        # Every fire runs Local only (``platform._dispatch_scheduled``),
        # whatever becomes of the chat it reports to.
        payload["local_only"] = True
    return payload


def schedule_name(platform: Any, row: ScheduleOfferRecord) -> str:
    """A name no schedule has: the offer's title, then "-2", "-3", …"""
    import re

    base = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(row.title or "")).strip()[:NAME_MAX] or "repeated-task"
    sched = getattr(platform, "scheduler", None)
    if sched is None or sched.get(base) is None:
        return base
    for n in range(2, 100):
        tail = f"-{n}"
        name = base[: NAME_MAX - len(tail)] + tail
        if sched.get(name) is None:
            return name
    return f"{base[: NAME_MAX - 13]}-{new_id('s')[-12:]}"


def task_of(platform: Any, row: ScheduleOfferRecord) -> Any:
    """The schedule Accept would save, NOT saved: what Rehearse runs through
    the schedule's own door (``platform.rehearse_scheduled``)."""
    from .models import ScheduledTaskRecord

    return ScheduledTaskRecord(
        id=row.id, name=schedule_name(platform, row), cron=row.cadence, trigger_type="cron", kind="task",
        payload_json=json.dumps(payload_of(platform, row)), enabled=False,
        chat_thread_id=row.chat_thread_id if chat_exists(platform.engine, row.chat_thread_id) else "",
    )


def check(platform: Any, row: ScheduleOfferRecord, name: str, payload: dict[str, Any]) -> None:
    """The add-time rules every door that saves a schedule applies. RAISES
    :class:`OfferRefused` in words."""
    from .checks import ScheduleRefused, check_payload

    try:
        check_payload(platform, name, "task", payload)
    except ScheduleRefused as exc:
        raise OfferRefused(exc.detail, exc.status_code) from None


def _claim(engine: Any, offer_id: str, status: str) -> bool:
    """Answer the offer once: ``waiting`` -> ``status``. False when it was
    already answered (or is gone)."""
    from sqlalchemy import update

    with session_scope(engine) as db:
        res = db.exec(
            update(ScheduleOfferRecord)  # type: ignore[call-overload]
            .where(ScheduleOfferRecord.id == offer_id, ScheduleOfferRecord.status == WAITING)
            .values(status=status, decided_at=utcnow())
        )
        db.commit()
        return bool(getattr(res, "rowcount", 0))


def _waiting(engine: Any, offer_id: str) -> ScheduleOfferRecord:
    row = get(engine, offer_id)
    if row is None:
        raise OfferRefused("There is no such offer.", 404)
    if row.status != WAITING:
        raise OfferRefused(
            "This offer was already accepted." if row.status == ACCEPTED else "This offer was already declined.",
            409,
        )
    return row


def accept(platform: Any, offer_id: str) -> dict[str, Any]:
    """Accept: the schedule is made, switched on, from this offer. RAISES
    :class:`OfferRefused` (the offer stays waiting) when it cannot be."""
    engine = platform.engine
    row = _waiting(engine, offer_id)
    rh = rhythm_of(row)
    if rh is None or not row.cadence:
        raise OfferRefused("This offer's timing could not be read, so nothing was made.", 409)
    sched = getattr(platform, "scheduler", None)
    if sched is None:
        raise OfferRefused("Schedules are not running in this copy of Iron Jarvis.", 409)
    name = schedule_name(platform, row)
    payload = payload_of(platform, row)
    check(platform, row, name, payload)
    if not _claim(engine, row.id, ACCEPTED):
        raise OfferRefused("This offer was already answered.", 409)
    try:
        # It reports to the newest chat while that chat is there (idea 39).
        chat = row.chat_thread_id if chat_exists(engine, row.chat_thread_id) else ""
        rec = sched.add_task(name, row.cadence, kind="task", payload=payload, enabled=True,
                             chat_thread_id=chat)
    except Exception as exc:  # noqa: BLE001 — the offer waits again, and says why
        _reopen(engine, row.id)
        from ..core.failures import plain_failure

        raise OfferRefused(plain_failure("That schedule could not be saved.", exc), 400) from None
    with session_scope(engine) as db:
        got = db.get(ScheduleOfferRecord, row.id)
        if got is not None:
            got.schedule_name = rec.name
            db.add(got)
            db.commit()
    return {"ok": True, "schedule": rec.name, "note": accepted_note(rec.name, rh.words, "skills" in payload,
                                                                    row.skill_name)}


def accepted_note(name: str, when: str, with_skill: bool, skill: str) -> str:
    """What the page says after Accept: the schedule as it was saved."""
    tail = f" It uses the skill “{skill}”." if with_skill and skill else ""
    return f"Made the schedule “{name}”: it runs by itself {when}.{tail} Schedules can pause or remove it."


def _reopen(engine: Any, offer_id: str) -> None:
    with session_scope(engine) as db:
        got = db.get(ScheduleOfferRecord, offer_id)
        if got is not None and got.status == ACCEPTED and not got.schedule_name:
            got.status, got.decided_at = WAITING, None
            db.add(got)
            db.commit()


def decline(engine: Any, offer_id: str) -> dict[str, Any]:
    """Decline: final for this task (no offer for it again)."""
    _waiting(engine, offer_id)
    if not _claim(engine, offer_id, DECLINED):
        raise OfferRefused("This offer was already answered.", 409)
    return {"ok": True, "note": DECLINED_NOTE}


DECLINED_NOTE = "Iron Jarvis won't offer to run this by itself again."


def chat_exists(engine: Any, thread_id: str) -> bool:
    from ..core.models import ChatThreadRecord as T

    tid = str(thread_id or "").strip()
    if not tid:
        return False
    with session_scope(engine) as db:
        return db.exec(select(T.id).where(T.id == tid)).first() is not None


def offer_view(platform: Any, row: ScheduleOfferRecord) -> dict[str, Any]:
    """What Improvements shows of one offer (``inbox/collect.offer_item``),
    every line read from the record now. BLOCKING."""
    rh = rhythm_of(row)
    units = [u for u in evidence(row).get("units") or [] if isinstance(u, dict)]
    return {
        "id": row.id, "title": row.title, "words": row.words, "skill_name": row.skill_name,
        "skill_kept": _skill_kept(platform, row.skill_name),
        "when": rh.words if rh is not None else "",
        "why": (f"The {len(units)} times you asked for this in chat were each {rh.on_days_words}."
                if rh is not None and units else ""),
        "evidence": evidence(row), "newest_day": newest_day(row), "account": row.account,
        "reports_to_chat": chat_exists(platform.engine, row.chat_thread_id),
        "rehearsal_id": row.rehearsal_id, "created_at": row.created_at, "local_only": row.local_only,
    }


def set_rehearsal(engine: Any, offer_id: str, rehearsal_id: str) -> None:
    with session_scope(engine) as db:
        got = db.get(ScheduleOfferRecord, offer_id)
        if got is not None:
            got.rehearsal_id = str(rehearsal_id or "")
            db.add(got)
            db.commit()


__all__ = [
    "ACCEPTED", "DECLINED", "DECLINED_NOTE", "KIND_TASK", "OFFERS_MAX", "OfferRefused", "ScheduleOfferRecord",
    "WAITING", "accept", "accepted_note", "check", "decline", "drop_thread", "evidence", "get",
    "make_offers", "newest_day", "offer_for", "payload_of", "rhythm_of", "schedule_name", "set_rehearsal",
    "task_of",
]
