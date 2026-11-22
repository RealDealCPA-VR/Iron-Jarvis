"""THE RESULT OF A CHANGE YOU ACCEPTED (18-a, build-out plan R2.8, idea 18
"Proof that a change helped"; ``docs/next-leaps/05-it-improves-itself.md``).

Every change the owner accepts from Improvements gets a before and an after.
About a week later its result shows in plain numbers, under the waiting
suggestions ("Results of changes you accepted"): "Since you accepted this on
12 March, research finished 9 of 10 tasks without help. Before, it was 6 of
10." Until there is enough work on both sides it says "Not enough work yet:
3 of 5 tasks since you accepted this on 12 March."

THE KINDS (18-a): ``coach`` (an agent's instructions, accepted from the
coach), ``skill`` (a new skill, approved from its draft), ``skill_refine``
(an improved skill), ``habit`` (a working habit for a team member). 18-c
registers ``lesson``, ``preference``, ``tool``, ``extension`` and
``self_fix`` here; ideas 19, 20 and 21 add theirs the same way.

THE RULES, each pinned in ``tests/test_next_leaps_18a_results.py``:

* ROWS ARE MADE BY READING THE STORES, never by a hook in each accept
  route: :func:`accepted_since` reads the accepted coach rows (their
  ``decided_at``), the approved skill drafts and the accepted working habits,
  ONE query per store that RAISES (the inbox's ``_pending`` rule). A store
  that cannot be read is NAMED (``unreadable``) and makes no row and touches
  none: never a result of zeros.
* WHEN: :func:`refresh` runs at the end of each night review pass
  (``improve/night_review``) and once a day in the daemon's daily tidy loop
  (``daemon/app._change_results_pass``) unless a refresh ran in the last 24
  hours (the review is off, or it did not reach the end of a pass). No
  model, nothing sent anywhere; bounded (:data:`MEASURED_MAX` changes,
  :data:`RUNS_MAX` runs, :data:`EVENTS_MAX` answers per read).
* THE NUMBERS come from the records only (``improve/metrics``: the split,
  the counts, the fixed thresholds and the words). A change whose record
  cannot be read keeps what it had; a new one is ``waiting`` with no
  numbers. A result that settled (a day after its after side's last day) is
  not measured again.
* SHOWN, NEVER ACTED ON. Nothing here undoes, removes or changes anything;
  a worse verdict changes nothing but its row (18-b offers the undo, and
  only the owner's press does it).
* :func:`page` is ``GET /improvements``'s ``results``: at most
  :data:`SHOWN`, newest first, of the changes accepted in the last
  :data:`LOOK_DAYS` days, each with its words built by code from the
  numbers.

Every function here is BLOCKING (SQLite): callers hop off the loop.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from . import metrics as M
from .models import ChangeResultRecord

log = logging.getLogger(__name__)

#: The part of Improvements, and its name wherever it cannot be read.
LABEL = "Results of changes you accepted"
OPEN_LABEL = "Open where it lives"
#: A change accepted this many days ago or less gets a row, and is shown.
LOOK_DAYS = 60
#: Changes measured in one refresh at most (the newest).
MEASURED_MAX = 60
#: Results shown at most.
SHOWN = 30
#: One read of agent runs, and of their answered questions, at most: past
#: that the read says nothing rather than a part of the truth.
RUNS_MAX = 5000
EVENTS_MAX = 50_000
USES_MAX = 20_000
#: Ids in one ``IN (...)``.
ID_CHUNK = 500
TITLE_CHARS = 200

KIND_COACH, KIND_SKILL, KIND_REFINE, KIND_HABIT = M.KIND_COACH, M.KIND_SKILL, M.KIND_REFINE, M.KIND_HABIT
KINDS = (KIND_COACH, KIND_SKILL, KIND_REFINE, KIND_HABIT)
#: What each kind counts.
METRIC = {KIND_COACH: M.HANDS_OFF, KIND_SKILL: M.ENDED_WELL, KIND_REFINE: M.ENDED_WELL, KIND_HABIT: M.TEAM_RECORD}


class Truncated(RuntimeError):
    """A read hit its bound: the numbers would be a part of the truth."""


@dataclass(frozen=True)
class Change:
    """One change the owner accepted. ``ref``: the suggestion's own id;
    ``key``: the store's own row (a habit's ``TeamNote`` id); ``target``: the
    roster name, the skill's name, the member's name; ``until``: when the
    next accepted change of the same thing landed; ``removed_at``: a habit
    taken back."""

    kind: str
    ref: str
    target: str
    accepted_at: datetime
    about: str = ""
    key: str = ""
    until: "datetime | None" = None
    removed_at: "datetime | None" = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.ref}"


class Accepted(list):
    """The changes found, newest first; ``unreadable`` names the kinds whose
    store could not be read (no change of theirs is in the list)."""

    unreadable: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# the stores (each ONE query that RAISES)
# --------------------------------------------------------------------------- #
def _coach_changes(engine: Any, since: datetime) -> list[Change]:
    from ..coach.models import ACCEPTED, KIND_TEAM_NOTE, CoachProposalRecord as R

    with session_scope(engine) as db:
        rows = db.exec(
            select(R.id, R.agent, R.decided_at)
            .where(R.status == ACCEPTED, R.kind != KIND_TEAM_NOTE)
            .where(R.decided_at >= since)  # type: ignore[operator]
        ).all()
    out = []
    for rid, agent, decided in rows:
        at = M.naive(decided)
        if at is not None and agent:
            out.append(Change(KIND_COACH, str(rid), str(agent), at))
    return out


def _skill_changes(engine: Any, since: datetime) -> list[Change]:
    from ..skills.learning_models import SkillProposalRecord as P

    with session_scope(engine) as db:
        rows = db.exec(
            select(P.id, P.kind, P.skill_name, P.decided_at)
            .where(P.status == "approved")
            .where(P.decided_at >= since)  # type: ignore[operator]
        ).all()
    out = []
    for pid, kind, name, decided in rows:
        at = M.naive(decided)
        if at is not None and name:
            out.append(Change(KIND_REFINE if str(kind) == "refine" else KIND_SKILL, str(pid), str(name), at))
    return out


def _habit_changes(engine: Any, since: datetime) -> list[Change]:
    from ..agents.team_notes import TeamNote as T

    with session_scope(engine) as db:
        rows = db.exec(
            select(T.id, T.member, T.text, T.proposal_id, T.accepted_at, T.removed_at)
            .where(T.accepted_at >= since)  # type: ignore[operator]
        ).all()
    out = []
    for nid, member, text, proposal, accepted, removed in rows:
        at = M.naive(accepted)
        if at is not None and member:
            out.append(Change(KIND_HABIT, str(proposal or nid), str(member), at, about=" ".join(str(text or "").split()),
                              key=str(nid), removed_at=M.naive(removed)))
    return out


#: ``(the kinds a store answers for, its reader)``. 18-c adds rows.
STORES: tuple[tuple[tuple[str, ...], Callable[[Any, datetime], list[Change]]], ...] = (
    ((KIND_COACH,), _coach_changes),
    ((KIND_SKILL, KIND_REFINE), _skill_changes),
    ((KIND_HABIT,), _habit_changes),
)

#: The kinds whose NEXT accepted change ends the after side of the one
#: before it (the same agent's instructions, the same skill).
_SAME_THING = {KIND_COACH: "coach", KIND_SKILL: "skill", KIND_REFINE: "skill"}


def _with_next(changes: list[Change]) -> list[Change]:
    """Each change with ``until``: the next accepted change of the same thing
    (a later coach change for the same agent, a later refine of the same
    skill)."""
    by_thing: dict[tuple[str, str], list[Change]] = {}
    for c in changes:
        family = _SAME_THING.get(c.kind)
        if family:
            by_thing.setdefault((family, c.target), []).append(c)
    nxt: dict[str, datetime] = {}
    for group in by_thing.values():
        group.sort(key=lambda c: c.accepted_at)
        for earlier, later in zip(group, group[1:]):
            nxt[earlier.id] = later.accepted_at
    return [
        Change(c.kind, c.ref, c.target, c.accepted_at, c.about, c.key, nxt.get(c.id), c.removed_at)
        for c in changes
    ]


def accepted_since(engine: Any, since: Any) -> Accepted:
    """Every change accepted at or after ``since``, newest first. A store
    that cannot be read is named in ``unreadable`` and adds nothing.
    BLOCKING; never raises."""
    floor = M.naive(since) or datetime(1970, 1, 1)
    found: list[Change] = []
    unreadable: list[str] = []
    for kinds, reader in STORES:
        try:
            found += reader(engine, floor)
        except Exception:  # noqa: BLE001 — one store never hides the others
            log.warning("change results: %s could not be read", "/".join(kinds), exc_info=True)
            unreadable += list(kinds)
    got = Accepted(sorted(_with_next(found), key=lambda c: (c.accepted_at, c.id), reverse=True))
    got.unreadable = tuple(unreadable)
    return got


# --------------------------------------------------------------------------- #
# the tasks on both sides (each measurer RAISES on a failed or cut-off read)
# --------------------------------------------------------------------------- #
def _chunks(ids: list[str]) -> list[list[str]]:
    return [ids[i:i + ID_CHUNK] for i in range(0, len(ids), ID_CHUNK)]


def _window(changes: list[Change]) -> tuple[datetime, datetime]:
    lo = min(c.accepted_at for c in changes) - timedelta(days=M.BEFORE_DAYS)
    hi = max(M.after_end(c.accepted_at, c.until)[0] for c in changes)
    return lo, hi


def _answered(db: Any, ids: list[str], since: datetime) -> dict[str, list[dict[str, Any]]]:
    """``{session id: [answered ask payloads]}``: the cards and questions the
    PERSON answered during those runs (``interrupt.answered`` with the app or
    the phone as its actor, ``eval.autonomy.PERSON_ACTORS``: the only answers
    ``autonomy.helped`` counts), by the run's own id in the payload. A call
    Auto or a grant let through is never read, so a run on Auto with many
    calls cannot fill the bound with answers that are no help."""
    from sqlalchemy import case, func

    from ..core.models import EventRecord as E
    from ..eval.autonomy import PERSON_ACTORS

    valid = func.json_valid(E.payload_json) == 1
    sid = case((valid, func.json_extract(E.payload_json, "$.session_id")), else_=None)
    actor = case((valid, func.json_extract(E.payload_json, "$.actor")), else_=None)
    out: dict[str, list[dict[str, Any]]] = {}
    seen = 0
    for chunk in _chunks(ids):
        rows = db.exec(
            select(E.payload_json)
            .where(E.type == "interrupt.answered")
            .where(E.created_at >= since)  # type: ignore[operator]
            .where(sid.in_(chunk))
            .where(actor.in_(PERSON_ACTORS))
            .limit(EVENTS_MAX + 1)
        ).all()
        seen += len(rows)
        if seen > EVENTS_MAX:
            raise Truncated("too many answered questions to read")
        for raw in rows:
            try:
                p = json.loads(raw or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(p, dict) and p.get("session_id"):
                if str(p.get("surface") or "") == "bench" or str(p.get("actor") or "") == "bench":
                    continue
                out.setdefault(str(p["session_id"]), []).append(p)
    return out


def coach_outcomes(engine: Any, targets: list[str], since: datetime, until: datetime) -> dict[str, list[M.Outcome]]:
    """``{roster name: [Outcome]}``: each FINISHED run of those agents that
    started in ``[since, until)``, as ``eval.autonomy`` counts an agent run
    (``agent_tasks(every_run=True)``: a Continue follow-up is a nudge on the
    run it continues), good = ended well with no help (``autonomy.helped``).
    RAISES on a failed read, and when a read hit its bound."""
    from ..core.models import Session, SessionStatus
    from ..eval import autonomy as A

    with session_scope(engine) as db:
        rows = db.exec(
            select(Session.id, Session.agent_name, Session.origin, Session.task, Session.outcome,
                   Session.created_at, Session.options_json)
            .where(Session.agent_name.in_(targets))  # type: ignore[attr-defined]
            .where(Session.created_at >= since, Session.created_at < until)  # type: ignore[operator]
            .where(Session.status.notin_([SessionStatus.ACTIVE, SessionStatus.QUEUED]))  # type: ignore[attr-defined]
            .order_by(Session.created_at)  # type: ignore[arg-type]
            .limit(RUNS_MAX + 1)
        ).all()
        if len(rows) > RUNS_MAX:
            raise Truncated("too many agent runs to read")
        answered = _answered(db, [str(r[0]) for r in rows], since)
    by_agent: dict[str, list[dict[str, Any]]] = {}
    started: dict[str, "datetime | None"] = {}
    for sid, agent, origin, task, outcome, created, options in rows:
        by_agent.setdefault(str(agent), []).append({
            "id": str(sid), "origin": origin, "task": task, "outcome": outcome, "created_at": created,
            "options_json": options,
        })
        started[str(sid)] = M.naive(created)
    out: dict[str, list[M.Outcome]] = {}
    for agent, runs in by_agent.items():
        tasks, by_session = A.agent_tasks(runs, every_run=True)
        for sid, asks in answered.items():
            task = by_session.get(sid)
            if task is not None:
                task.answered.extend(asks)
        # A task starts when its FIRST run did (``Task.key`` is that run's
        # id): the stored time itself, never a float's round trip.
        out[agent] = [
            M.Outcome(start, bool(t.success) and not A.helped(t))
            for t in tasks
            if (start := started.get(t.key)) is not None
        ]
    return out


def skill_outcomes(engine: Any, names: list[str], since: datetime, until: datetime) -> dict[str, list[M.Outcome]]:
    """``{skill name: [Outcome]}``: each task that loaded the skill
    (``SkillUseRecord``) and started in ``[since, until)`` (the run's own
    start; the use's record when the run is gone), good = it ended well by
    the skill learner's own record (``OutcomeRecord.success``, else the
    run's status: ``SkillLearningEngine._session_outcome``). RAISES on a
    failed read, and when a read hit its bound."""
    from sqlalchemy import func

    from ..core.models import Session, SessionStatus
    from ..improvement.models import OutcomeRecord as O
    from ..skills.learning_models import SkillUseRecord as U

    began = func.coalesce(Session.created_at, U.created_at)
    with session_scope(engine) as db:
        uses = db.exec(
            select(U.skill_name, U.session_id, began, Session.status)
            .select_from(U)
            .outerjoin(Session, Session.id == U.session_id)
            .where(U.skill_name.in_(names))  # type: ignore[attr-defined]
            .where(began >= since, began < until)
            .limit(USES_MAX + 1)
        ).all()
        if len(uses) > USES_MAX:
            raise Truncated("too many skill uses to read")
        ok: dict[str, bool] = {}
        for chunk in _chunks(sorted({str(u[1]) for u in uses})):
            for sid, success in db.exec(
                select(O.session_id, O.success).where(O.session_id.in_(chunk))  # type: ignore[attr-defined]
                .order_by(O.created_at)  # type: ignore[arg-type]
            ).all():
                ok.setdefault(str(sid), bool(success))
    out: dict[str, list[M.Outcome]] = {}
    for name, sid, at, status in uses:
        start = M.naive(at)
        if start is None:
            continue
        done = getattr(status, "value", status)
        good = ok[str(sid)] if str(sid) in ok else str(done or "") == SessionStatus.COMPLETED.value
        out.setdefault(str(name), []).append(M.Outcome(start, good))
    return out


def _measure_coach(engine: Any, changes: list[Change], now: datetime) -> dict[str, M.Counted]:
    lo, hi = _window(changes)
    tasks = coach_outcomes(engine, sorted({c.target for c in changes}), lo, hi)
    return {c.id: M.count_coach(tasks.get(c.target, []), c.accepted_at, until=c.until) for c in changes}


def _measure_skills(engine: Any, changes: list[Change], now: datetime) -> dict[str, M.Counted]:
    lo, hi = _window(changes)
    tasks = skill_outcomes(engine, sorted({c.target for c in changes}), lo, hi)
    count = {KIND_SKILL: M.count_skill, KIND_REFINE: M.count_refine}
    return {c.id: count[c.kind](tasks.get(c.target, []), c.accepted_at, until=c.until) for c in changes}


class _Note:
    """What ``team_measure.measures_for`` reads of a ``TeamNote``."""

    def __init__(self, c: Change) -> None:
        self.id, self.member, self.text = c.key, c.target, c.about
        self.accepted_at, self.removed_at = c.accepted_at, c.removed_at


def _measure_habits(engine: Any, changes: list[Change], now: datetime) -> dict[str, M.Counted]:
    """A habit whose measure cannot be read is left out (no numbers):
    ``team_measure`` answers nothing for it rather than zeros."""
    from ..agents import team_measure

    got = team_measure.measures_for(engine, [_Note(c) for c in changes], now=now)
    out: dict[str, M.Counted] = {}
    for c in changes:
        m = got.get(c.key)
        if m is not None:
            out[c.id] = M.habit_sides(m.view(), c.accepted_at, removed_at=c.removed_at, now=now)
    return out


#: ``(the kinds, the measurer)``: ``{change id: Counted}`` for the changes
#: of those kinds, RAISING when their record cannot be read.
MEASURERS: tuple[tuple[tuple[str, ...], Callable[[Any, list[Change], datetime], dict[str, M.Counted]]], ...] = (
    ((KIND_COACH,), _measure_coach),
    ((KIND_SKILL, KIND_REFINE), _measure_skills),
    ((KIND_HABIT,), _measure_habits),
)


# --------------------------------------------------------------------------- #
# the rows
# --------------------------------------------------------------------------- #
def _loads(text: Any) -> dict[str, Any]:
    try:
        got = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _settled(row: "ChangeResultRecord | None") -> bool:
    """Its numbers stopped moving: not measured again."""
    return (row is not None and row.state in (M.READY, M.NOT_ENOUGH)
            and _loads(row.after_json).get("final") is True)


def _rows(engine: Any, ids: list[str]) -> dict[str, ChangeResultRecord]:
    out: dict[str, ChangeResultRecord] = {}
    with session_scope(engine) as db:
        for chunk in _chunks(ids):
            for row in db.exec(select(ChangeResultRecord).where(ChangeResultRecord.id.in_(chunk))):  # type: ignore[attr-defined]
                db.expunge(row)
                out[row.id] = row
    return out


def _name(c_kind: str, target: str) -> str:
    """The thing's own name: ``custom:research`` -> ``research``."""
    if c_kind == KIND_COACH and ":" in target:
        return target.split(":", 1)[1]
    return target


def _write(engine: Any, changes: list[Change], measured: dict[str, M.Counted], existing: dict[str, ChangeResultRecord],
           now: datetime) -> int:
    """Make a row for each change that has none, and put the numbers of each
    one measured on its row. A change that was not measured keeps its row
    as it was; a new one is ``waiting`` with no numbers. Returns how many
    rows were made."""
    made = 0
    with session_scope(engine) as db:
        for c in changes:
            row = db.get(ChangeResultRecord, c.id)
            if row is None:
                row = ChangeResultRecord(id=c.id, kind=c.kind, ref=c.ref, target=c.target, about=c.about[:TITLE_CHARS],
                                         accepted_at=c.accepted_at, metric=METRIC.get(c.kind, ""), state=M.WAITING)
                made += 1
            counted = measured.get(c.id)
            if counted is None and c.id in existing:
                continue  # not measured this time: it keeps what it had
            if counted is not None:
                judged = M.judge(c.kind, counted, accepted_at=c.accepted_at, now=now, name=_name(c.kind, c.target))
                row.before_json = json.dumps(counted.before.view())
                row.after_json = json.dumps({**counted.after.view(), "end": counted.end.isoformat(),
                                             "ended": counted.ended, "final": judged.final})
                row.state, row.verdict = judged.state, judged.verdict
                row.checked_at = now
                row.about = c.about[:TITLE_CHARS]
            db.add(row)
        db.commit()
    return made


def refresh(engine: Any, *, now: Any = None) -> dict[str, Any]:
    """Make and measure the results (the module docstring): ``{made,
    measured, unreadable: [kinds]}``. ``unreadable`` names every kind whose
    store or record could not be read (``results`` when this table itself
    could not be). BLOCKING; never raises."""
    with _LOCK:  # the night pass and the daily pass never write the same row at once
        return _refresh(engine, now)


#: One refresh at a time in this process.
_LOCK = threading.Lock()


def _refresh(engine: Any, now: Any) -> dict[str, Any]:
    moment = M.naive(now) or M.naive(utcnow())
    assert moment is not None
    out: dict[str, Any] = {"made": 0, "measured": 0, "unreadable": []}
    found = accepted_since(engine, moment - timedelta(days=LOOK_DAYS))
    unreadable: list[str] = list(found.unreadable)
    changes = list(found)[:MEASURED_MAX]
    try:
        existing = _rows(engine, [c.id for c in changes])
    except Exception:  # noqa: BLE001
        log.warning("change results: the results could not be read", exc_info=True)
        out["unreadable"] = unreadable + ["results"]
        return out
    todo = [c for c in changes if not _settled(existing.get(c.id))]
    measured: dict[str, M.Counted] = {}
    for kinds, measurer in MEASURERS:
        mine = [c for c in todo if c.kind in kinds]
        if not mine:
            continue
        try:
            got = measurer(engine, mine, moment)
        except Exception:  # noqa: BLE001 — one kind never hides the others; no zeros
            log.warning("change results: the record of %s could not be read", "/".join(kinds), exc_info=True)
            unreadable += [k for k in kinds if any(c.kind == k for c in mine)]
            continue
        measured.update(got)
        # A habit team_measure could not read is named too.
        if any(c.id not in got for c in mine):
            unreadable += [k for k in kinds if any(c.kind == k and c.id not in got for c in mine)]
    try:
        out["made"] = _write(engine, changes, measured, existing, moment)
    except Exception:  # noqa: BLE001
        log.warning("change results: the results could not be saved", exc_info=True)
        out["unreadable"] = list(dict.fromkeys(unreadable + ["results"]))
        return out
    out["measured"] = len(measured)
    out["unreadable"] = list(dict.fromkeys(unreadable))
    if changes:
        log.info("change results: %d change(s), %d measured, %d new", len(changes), len(measured), out["made"])
    return out


def refreshed_within(engine: Any, *, hours: float = 24.0, now: Any = None) -> bool:
    """Was a result measured in the last ``hours``? (The daily pass leaves
    such a day alone.) BLOCKING; never raises (False when it cannot tell,
    so the daily pass runs)."""
    try:
        from sqlalchemy import func

        moment = M.naive(now) or M.naive(utcnow())
        assert moment is not None
        with session_scope(engine) as db:
            newest = db.exec(select(func.max(ChangeResultRecord.checked_at))).one()
        newest = newest[0] if isinstance(newest, tuple) else newest
        at = M.naive(newest)
        return at is not None and at >= moment - timedelta(hours=float(hours))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# what Improvements and chat show (BLOCKING reads)
# --------------------------------------------------------------------------- #
def _seg(value: Any) -> str:
    return quote(str(value or ""), safe="")


def _title(row: ChangeResultRecord) -> str:
    name = _name(row.kind, row.target)
    if row.kind == KIND_COACH:
        return f"New instructions for {name}"
    if row.kind == KIND_SKILL:
        return f"New skill: {name}"
    if row.kind == KIND_REFINE:
        return f"Updated skill: {name}"
    if row.kind == KIND_HABIT:
        text = " ".join(str(row.about or "").split())
        text = text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1].rstrip() + "…"
        return f"Working habit for {name}: {text}" if text else f"Working habit for {name}"
    return name


def _home(row: ChangeResultRecord) -> dict[str, str]:
    if row.kind == KIND_COACH:
        return {"href": f"/agents?view=team&agent={_seg(row.target)}", "label": OPEN_LABEL}
    if row.kind == KIND_HABIT:
        return {"href": f"/agents?view=team&agent={_seg('custom:' + row.target)}", "label": OPEN_LABEL}
    return {"href": "/skills", "label": OPEN_LABEL}


#: What a result that was never measured says (no numbers, never zeros).
NOT_MEASURED_YET = "Not measured yet. Iron Jarvis counts the work before and after once a day."


def result_view(row: ChangeResultRecord, now: Any = None) -> dict[str, Any]:
    """One result as ``GET /improvements`` carries it. Its words are built
    from its stored numbers (``metrics.judge``); a result never measured
    says so and has NO numbers."""
    moment = M.naive(now) or M.naive(utcnow())
    before = M.Side.from_view(_loads(row.before_json))
    after_raw = _loads(row.after_json)
    after = M.Side.from_view(after_raw)
    base = {
        "id": row.id, "kind": row.kind, "ref": row.ref, "target": row.target, "title": _title(row),
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else "",
        "checked_at": row.checked_at.isoformat() if row.checked_at else "",
        "state": row.state, "verdict": row.verdict, "home": _home(row),
    }
    if row.state == M.WAITING or before is None or after is None:
        return {**base, "state": M.WAITING, "verdict": "", "before": None, "after": None, "words": NOT_MEASURED_YET}
    end = M.naive(after_raw.get("end")) or M.after_end(M.naive(row.accepted_at) or moment)[0]
    counted = M.Counted(before, after, end, str(after_raw.get("ended") or ""), worse=row.verdict == M.WORSE)
    judged = M.judge(row.kind, counted, accepted_at=row.accepted_at, now=moment, name=_name(row.kind, row.target))
    two_sided = row.kind != KIND_SKILL
    return {
        **base,
        "before": before.view() if two_sided else None,
        "after": after.view(),
        "words": judged.words,
    }


def page(engine: Any, *, now: Any = None) -> list[dict[str, Any]]:
    """``GET /improvements``'s ``results``: at most :data:`SHOWN`, newest
    first, of the changes accepted in the last :data:`LOOK_DAYS` days. A row
    that cannot be shown is left out (and logged); the table that cannot be
    read RAISES (the inbox names it, never "no results")."""
    moment = M.naive(now) or M.naive(utcnow())
    assert moment is not None
    with session_scope(engine) as db:
        rows = list(db.exec(
            select(ChangeResultRecord)
            .where(ChangeResultRecord.accepted_at >= moment - timedelta(days=LOOK_DAYS))  # type: ignore[operator]
            .order_by(ChangeResultRecord.accepted_at.desc(), ChangeResultRecord.id)  # type: ignore[attr-defined]
            .limit(SHOWN)
        ))
        for row in rows:
            db.expunge(row)
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(result_view(row, moment))
        except Exception:  # noqa: BLE001 — one odd row never hides the rest
            log.warning("change results: one result could not be shown", exc_info=True)
    return out


__all__ = [
    "Accepted", "Change", "KINDS", "KIND_COACH", "KIND_HABIT", "KIND_REFINE", "KIND_SKILL", "LABEL", "LOOK_DAYS",
    "MEASURED_MAX", "MEASURERS", "NOT_MEASURED_YET", "SHOWN", "STORES", "Truncated", "accepted_since",
    "coach_outcomes", "page", "refresh", "refreshed_within", "result_view", "skill_outcomes",
]
