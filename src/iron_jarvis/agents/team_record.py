"""THE MODEL TEAM'S RECORD per (member, role) (v1.395.0, the long-jobs plan's
Phase 6, decision 7).

Decision 7 asks the router to try a LOCAL member first when its record for the
role is good — "good" measured by the finish check and the reviewer, not just
"it finished". This module is that record, kept in the app's own database as
ONE ROW PER PIECE OF WORK a member was handed for a role (a late model,
``core.db._LATE_MODEL_MODULES``):

* ``finished`` — the helper's run completed (the door writes the row when the
  run settles: a crash or a refusal is ``False``; a run the USER stopped writes
  no row — stopping it was not the member's failure);
* ``check`` — the coding reply's FINISH CHECK, credited to every helper of
  that reply that changed files (``harness/finish.FinishCheck.record`` →
  :func:`credit_check`, reading the reply's helpers through
  ``finish._helper_sessions``, the check's own gathering): ``passed`` /
  ``failed``; "" when nothing checked it (no tests found, not run, or the work
  was not in a coding session);
* ``review`` — a REVIEWER member's verdict: a helper handed the ``review``
  role is asked to end its report with one line, ``VERDICT: approved`` or
  ``VERDICT: changes needed`` (:data:`VERDICT_ASK`); that verdict is credited
  to the ``code`` / ``test`` pieces of the same work (the same caller) since
  the previous review (:func:`credit_review`). That is all "the reviewer's
  verdict" means here — kept simple on purpose; a report with no verdict line
  credits nothing.

A piece SUCCEEDED when it finished and nothing that checked it said no
(:func:`succeeded`): a passed check is good, a failed check or a "changes
needed" review is bad, no check is "finished only".

THE JUDGEMENT (:func:`role_record`), over the member's newest
:data:`RECORD_WINDOW` pieces for that role:

* ``good`` — at least :data:`MIN_RUNS` pieces and a success rate of at least
  :data:`GOOD_RATE`;
* ``unknown`` — fewer than :data:`MIN_RUNS` pieces ("not yet known": the
  router tries a local member first, checks, then moves up);
* ``not_good`` — a low rate, OR the newest :data:`FAIL_STREAK` pieces all
  failed: a member that fails a role twice in a row stops getting it first
  until it passes again (its next success ends the streak).

The constants are Phase 7's to tune from the benchmark. Every function here is
BLOCKING (SQLite) and never raises: the router and the doors call it off the
loop (``asyncio.to_thread``); a record that cannot be read reads as "no record
yet", never as a reason to refuse work.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

log = logging.getLogger(__name__)

#: Pieces a member needs for a role before its record is judged at all.
MIN_RUNS = 3
#: The success rate a judged record must reach to be "good".
GOOD_RATE = 0.7
#: Failures in a row (newest first) that stop a member getting a role first.
FAIL_STREAK = 2
#: How many of the newest pieces per (member, role) the judgement reads.
RECORD_WINDOW = 20
#: A piece that failed this many times on ONE member goes one step up
#: (``model_team.route_role``, decision 7 / the plan's "fails its check twice").
PIECE_FAILS = 2

CHECK_PASSED, CHECK_FAILED = "passed", "failed"
REVIEW_GOOD, REVIEW_BAD = "good", "bad"

GOOD, UNKNOWN, NOT_GOOD = "good", "unknown", "not_good"

#: What a reviewer member is asked to end its report with (the ONE place the
#: verdict line is spelled; :func:`verdict_of` reads it back).
VERDICT_ASK = (
    "End your report with exactly one line that gives your verdict on the change: "
    "\"VERDICT: approved\" or \"VERDICT: changes needed\"."
)
_VERDICT_RX = re.compile(
    r"^\s*\**\s*verdict\s*\**\s*:\s*\**\s*(approved|changes needed)\b", re.IGNORECASE | re.MULTILINE
)


class TeamRoleRun(SQLModel, table=True):
    """One piece of work a model team member was handed for a role."""

    __tablename__ = "teamrolerun"

    id: str = Field(default_factory=lambda: new_id("trr"), primary_key=True)
    #: The member's name (``DynamicAgentRecord.name``, never "custom:").
    member: str = Field(default="", index=True)
    #: A role id (``model_team.ROLE_IDS``).
    role: str = Field(default="", index=True)
    #: The helper's own session (what the finish check credits by).
    session_id: str = Field(default="", index=True)
    #: The work the piece belongs to: the caller's session (``build:<id>`` for
    #: a coding session, the coordinator's session for a mission).
    scope: str = Field(default="", index=True)
    #: The piece itself (``model_team.piece_key``): the planner's ``piece``
    #: name, else its task, normalised.
    piece: str = ""
    finished: bool = False
    #: ``passed`` / ``failed`` / "" (nothing checked it). (Not named ``check``:
    #: that is an SQL keyword.)
    check_result: str = ""
    #: ``good`` / ``bad`` / "" (no reviewer's verdict).
    review: str = ""
    created_at: datetime = Field(default_factory=utcnow)


def succeeded(finished: bool, check: str, review: str) -> bool:
    """One piece's outcome: it finished and nothing that checked it said no."""
    return bool(finished) and check != CHECK_FAILED and review != REVIEW_BAD


@dataclass(frozen=True)
class RoleRecord:
    """A member's record for one role (the newest :data:`RECORD_WINDOW`)."""

    runs: int = 0
    passed: int = 0
    #: Failures in a row, newest first.
    streak: int = 0

    @property
    def rate(self) -> float:
        return (self.passed / self.runs) if self.runs else 0.0

    @property
    def status(self) -> str:
        if self.streak >= FAIL_STREAK:
            return NOT_GOOD
        if self.runs < MIN_RUNS:
            return UNKNOWN
        return GOOD if self.rate >= GOOD_RATE else NOT_GOOD

    def words(self) -> str:
        """The record in plain words (``GET /team``, the router's reasons)."""
        if self.runs == 0:
            return "no record yet"
        tally = f"{self.passed} of {self.runs} passed"
        if self.streak >= FAIL_STREAK:
            return f"failed the last {self.streak} in a row ({tally})"
        if self.runs < MIN_RUNS:
            return f"too few to judge yet ({tally})"
        return f"good ({tally})" if self.status == GOOD else f"not good ({tally})"

    def view(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "passed": self.passed,
            "streak": self.streak,
            "status": self.status,
            "words": self.words(),
        }


def _record_of(rows: Iterable[tuple[bool, str, str]]) -> RoleRecord:
    """``rows`` newest first: ``(finished, check, review)``."""
    runs = passed = streak = 0
    counting = True
    for finished, check, review in rows:
        if runs >= RECORD_WINDOW:
            break
        ok = succeeded(finished, check, review)
        runs += 1
        passed += 1 if ok else 0
        if counting and not ok:
            streak += 1
        elif ok:
            counting = False
    return RoleRecord(runs=runs, passed=passed, streak=streak)


def records_for(engine: Any, members: Iterable[str], role: str = "") -> dict[str, dict[str, RoleRecord]]:
    """``{member: {role: RoleRecord}}`` for ``members`` (one query); only the
    given ``role`` when one is named. Newest first by INSERTION order (the
    SQLite rowid — the v1.286.0 rule: two rows in one 15.6 ms tick of Windows'
    clock still order). BLOCKING; never raises (an unreadable record is no
    record)."""
    names = [str(m) for m in members if str(m or "").strip()]
    if engine is None or not names:
        return {}
    try:
        from sqlalchemy import literal_column
        from sqlmodel import select

        from ..core.db import session_scope

        with session_scope(engine) as db:
            q = select(TeamRoleRun).where(TeamRoleRun.member.in_(names))  # type: ignore[attr-defined]
            if role:
                q = q.where(TeamRoleRun.role == role)
            q = q.order_by(literal_column("teamrolerun.rowid").desc())  # type: ignore[arg-type]
            plain = [(r.member, r.role, bool(r.finished), str(r.check_result or ""), str(r.review or ""))
                     for r in db.exec(q)]
    except Exception:  # noqa: BLE001
        log.debug("team record read failed", exc_info=True)
        return {}
    grouped: dict[tuple[str, str], list[tuple[bool, str, str]]] = {}
    for member, rl, finished, check, review in plain:
        grouped.setdefault((member, rl), []).append((finished, check, review))
    out: dict[str, dict[str, RoleRecord]] = {}
    for (member, rl), seq in grouped.items():
        out.setdefault(member, {})[rl] = _record_of(seq)
    return out


def role_record(engine: Any, member: str, role: str) -> RoleRecord:
    """One member's record for one role ("no record yet" when unreadable)."""
    return records_for(engine, [member], role).get(member, {}).get(role, RoleRecord())


def note_run(engine: Any, *, member: str, role: str, session_id: str, scope: str,
             piece: str = "", finished: bool) -> "str | None":
    """Write one piece's row when its run settles. Returns its id, or None
    when it could not be written. BLOCKING; never raises."""
    if engine is None or not member or not role:
        return None
    try:
        from ..core.db import session_scope

        row = TeamRoleRun(
            member=str(member), role=str(role), session_id=str(session_id or ""),
            scope=str(scope or ""), piece=str(piece or "")[:400], finished=bool(finished),
        )
        with session_scope(engine) as db:
            db.add(row)
            db.commit()
            return row.id
    except Exception:  # noqa: BLE001
        log.warning("model team record could not be written for %s", member, exc_info=True)
        return None


def credit_check(engine: Any, session_ids: Iterable[str], verdict: str) -> int:
    """The finish check's verdict (``passed`` / ``failed``) on the pieces run
    in ``session_ids`` (the reply's helpers that changed files). Returns how
    many rows it set. BLOCKING; never raises."""
    if verdict not in (CHECK_PASSED, CHECK_FAILED):
        return 0
    ids = [str(s) for s in session_ids if str(s or "").strip()]
    if engine is None or not ids:
        return 0
    try:
        from sqlmodel import select

        from ..core.db import session_scope

        n = 0
        with session_scope(engine) as db:
            for row in db.exec(select(TeamRoleRun).where(TeamRoleRun.session_id.in_(ids))):  # type: ignore[attr-defined]
                row.check_result = verdict
                db.add(row)
                n += 1
            db.commit()
        return n
    except Exception:  # noqa: BLE001
        log.warning("model team check credit failed", exc_info=True)
        return 0


def verdict_of(report: Any) -> str:
    """``good`` / ``bad`` from a reviewer's report's LAST verdict line, else
    "" (no line, no verdict — nothing is guessed)."""
    found = _VERDICT_RX.findall(str(report or ""))
    if not found:
        return ""
    return REVIEW_GOOD if found[-1].strip().lower() == "approved" else REVIEW_BAD


def credit_review(engine: Any, *, scope: str, review_row: "str | None", verdict: str) -> int:
    """A reviewer's ``verdict`` on the ``code`` / ``test`` pieces of the same
    work (``scope``) written after the previous review piece there and before
    this one (``review_row``), that no review judged yet. BLOCKING; never
    raises."""
    if verdict not in (REVIEW_GOOD, REVIEW_BAD) or engine is None or not scope or not review_row:
        return 0
    try:
        from sqlalchemy import text

        from ..core.db import session_scope

        with session_scope(engine) as db:
            me = db.exec(text(  # type: ignore[call-overload]
                "SELECT rowid FROM teamrolerun WHERE id = :id").bindparams(id=review_row)).first()
            if me is None:
                return 0
            mine = int(me[0])
            prev = db.exec(text(  # type: ignore[call-overload]
                "SELECT MAX(rowid) FROM teamrolerun WHERE scope = :scope AND role = 'review' "
                "AND rowid < :mine").bindparams(scope=scope, mine=mine)).first()
            floor = int(prev[0]) if prev is not None and prev[0] is not None else 0
            res = db.exec(text(  # type: ignore[call-overload]
                "UPDATE teamrolerun SET review = :v WHERE scope = :scope AND role IN ('code', 'test') "
                "AND review = '' AND rowid > :floor AND rowid < :mine"
            ).bindparams(v=verdict, scope=scope, floor=floor, mine=mine))
            db.commit()
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001
        log.warning("model team review credit failed", exc_info=True)
        return 0


def piece_failures(engine: Any, scope: str, role: str, piece: str) -> list[tuple[str, int]]:
    """``[(member, failures)]`` for one piece of one work, in the order its
    members FIRST took it (oldest first). Empty when there is no piece key or
    nothing was recorded. BLOCKING; never raises."""
    if engine is None or not scope or not role or not piece:
        return []
    try:
        from sqlalchemy import text

        from ..core.db import session_scope

        with session_scope(engine) as db:
            rows = db.exec(text(  # type: ignore[call-overload]
                "SELECT member, finished, check_result, review FROM teamrolerun WHERE scope = :scope "
                "AND role = :role AND piece = :piece ORDER BY rowid"
            ).bindparams(scope=scope, role=role, piece=piece[:400])).all()
    except Exception:  # noqa: BLE001
        log.debug("model team piece read failed", exc_info=True)
        return []
    order: list[str] = []
    fails: dict[str, int] = {}
    for member, finished, check, review in rows:
        name = str(member or "")
        if name not in fails:
            order.append(name)
            fails[name] = 0
        if not succeeded(bool(finished), str(check or ""), str(review or "")):
            fails[name] += 1
    return [(m, fails[m]) for m in order]


__all__ = [
    "CHECK_FAILED", "CHECK_PASSED", "FAIL_STREAK", "GOOD", "GOOD_RATE", "MIN_RUNS", "NOT_GOOD",
    "PIECE_FAILS", "RECORD_WINDOW", "REVIEW_BAD", "REVIEW_GOOD", "RoleRecord", "TeamRoleRun",
    "UNKNOWN", "VERDICT_ASK", "credit_check", "credit_review", "note_run", "piece_failures",
    "records_for", "role_record", "succeeded", "verdict_of",
]
