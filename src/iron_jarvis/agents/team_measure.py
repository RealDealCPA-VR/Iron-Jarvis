"""TEAM NOTES, Phase 3 — DOES A WORKING HABIT HELP? (v1.423.0, the
2026-11-01 audit's "The owner's idea: Team notes", Wave 3 lane E).

The owner's design: "Each member's record is split at the moment a note was
accepted: first-pass approval, confirmed findings per piece, check pass rate.
… No numbers are shown under 5 pieces, and a note that makes things worse is
flagged for removal, never removed automatically."

For ONE accepted habit (``agents/team_notes.TeamNote``: its member, when it
was accepted, when it was removed) this module reads the member's record on
both sides of the acceptance (:func:`measure`):

* AFTER — from the acceptance to its removal (or now);
* BEFORE — the SAME span of time just before the acceptance, at most
  :data:`BEFORE_CAP_DAYS` days (a habit accepted an hour ago is compared with
  the hour before it, never with a month of other work) — and (v1.423.0
  review) never reaching back into a time the SAME habit was in force: a
  habit removed and accepted again is compared with the time it was not.

WHICH SIDE A PIECE IS ON (v1.423.0 review). A member's habits are read ONCE
per reply, when its FIRST team piece of that reply STARTS
(``team_notes.habits_for_run``'s cache — and accepting a habit does not drop
it): so a whole REPLY belongs to the side its member's first piece STARTED
on (that piece's own session's ``created_at``; its record's ``created_at`` —
when it ENDED — when the session row is gone). A reply that began before the
acceptance ran without the habit to its end, and its fix after a "changes
needed" is a SECOND pass: counting either AFTER made a habit look better
than it was. A finding goes with its reply. A piece of no reply (``turn``
"") is placed by its own start. With no reply across the boundary the
numbers are exactly the member card's (pinned). REMOVING a habit is the
other way round: it DOES drop that cache (``team_notes.forget_member``), so
a piece that STARTED after the removal read the habits afresh — without
this one — and is no part of the after side, whatever reply it belongs to
(nor is a finding on such a piece).

Per side (:class:`Side`):

* PIECES — the member's ``code`` / ``test`` pieces (``team_record.
  TeamRoleRun``) in the window that a check or a review judged (a piece
  nothing judged says nothing about how it was done);
* FIRST-PASS APPROVAL — the member card's own definition
  (``team_findings.findings_for``): the reviewer's verdict on the member's
  EARLIEST judged piece of each reply (``approved`` of ``of`` replies), over
  the replies on this side;
* CONFIRMED FINDINGS PER PIECE — the findings the lead was ASKED to answer
  and that were confirmed fixed (``findings_for``'s rule: asked rows only,
  status ``fixed``), over the side's pieces;
* CHECK PASS RATE — of the pieces the finish check judged
  (``TeamRoleRun.check_result``), how many passed.

A side with fewer than :data:`MIN_PIECES` pieces has NO numbers — only "not
enough work yet" (:data:`NOT_ENOUGH`): a handful of pieces says nothing about
a habit, and a number shown there would be read as a verdict.

THE FLAG (:func:`worse`): True only when BOTH sides have enough pieces AND
one of —

* first-pass approval fell by at least :data:`FIRST_PASS_FALL` (20 points);
* confirmed findings per piece rose by at least :data:`FINDINGS_RISE_RATIO`
  (half again) AND by at least :data:`FINDINGS_RISE_MIN` (half a finding per
  piece) — a rise from 0.1 to 0.2 doubles but is noise;
* the check pass rate fell by at least :data:`CHECK_FALL` (20 points).

The rates are compared EXACTLY (``fractions.Fraction``): ``0.6 - 0.4`` is
``0.19999999999999996`` in floating point, and a fall of exactly 20 points
must flag. THE THRESHOLDS ARE PHASE 7's TO TUNE from the benchmark (the bench
runs the team tier with team notes on and off — ``[planner] team_notes``).

The flag is SHOWN, never acted on: ``GET /team`` carries each accepted
habit's ``measure`` and Settings › Model team says "This habit may be making
things worse — consider removing it" beside the habit's own two-press Remove.
NOTHING in Iron Jarvis removes a habit because of a measure — removing it is
the owner's press (``team_notes.remove``), pinned.

Every reader here is BLOCKING (SQLite) and never raises; the route reads it
off the loop (``GET /team`` is a sync route, run in the threadpool). A
measure that cannot be read is ABSENT (:func:`attach` leaves the habit with
no ``measure`` key) — never a comparison of zeros. A habit costs two
queries (its pieces and its findings, both sides at once), and
:func:`attach` TRIES at most :data:`MAX_MEASURED` habits (v1.423.0 review:
it capped the ones it measured, so an unreadable record was retried for
every habit).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Iterable

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# the numbers (Phase 7's to tune)
# --------------------------------------------------------------------------- #

#: A side with fewer pieces than this has no numbers ("not enough work yet").
MIN_PIECES = 5
#: The BEFORE side is the after side's span, at most this many days.
BEFORE_CAP_DAYS = 30
#: Worse: first-pass approval fell by at least this much (20 points)…
FIRST_PASS_FALL = Fraction(1, 5)
#: …or the check pass rate fell by at least this much (20 points)…
CHECK_FALL = Fraction(1, 5)
#: …or confirmed findings per piece rose to at least this times before AND by
#: at least :data:`FINDINGS_RISE_MIN`.
FINDINGS_RISE_RATIO = Fraction(3, 2)
FINDINGS_RISE_MIN = Fraction(1, 2)
#: At most this many habits are TRIED in one read (each is two queries,
#: read or not); the rest carry no measure.
MAX_MEASURED = 60
#: The roles a piece is counted for (the member card's first-pass rule).
PIECE_ROLES = ("code", "test")

NOT_ENOUGH = "not enough work yet"


def _naive(at: "datetime | None") -> "datetime | None":
    """A stamp as SQLite keeps it (naive UTC)."""
    if at is None:
        return None
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def _when(value: Any) -> "datetime | None":
    """A stamp from a record (a datetime) or a view (its ISO text), naive UTC;
    None when unreadable."""
    if isinstance(value, datetime):
        return _naive(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _naive(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# one side of an acceptance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Side:
    """The member's record over one window."""

    pieces: int = 0
    #: First-pass approval: replies whose first judged piece was approved, of
    #: the replies a review judged.
    first_approved: int = 0
    first_of: int = 0
    #: Confirmed findings (asked, confirmed fixed) in the window.
    confirmed: int = 0
    #: Pieces the finish check judged, and how many of them passed.
    checks_passed: int = 0
    checks_of: int = 0

    @property
    def enough(self) -> bool:
        return self.pieces >= MIN_PIECES

    @property
    def first_pass_rate(self) -> "Fraction | None":
        return Fraction(self.first_approved, self.first_of) if self.first_of else None

    @property
    def findings_per_piece(self) -> "Fraction | None":
        return Fraction(self.confirmed, self.pieces) if self.pieces else None

    @property
    def check_rate(self) -> "Fraction | None":
        return Fraction(self.checks_passed, self.checks_of) if self.checks_of else None

    def view(self) -> dict[str, Any]:
        """The side as ``GET /team`` carries it. Under :data:`MIN_PIECES` NO
        numbers — only how much work there was and the words."""
        if not self.enough:
            return {"enough": False, "pieces": self.pieces, "words": NOT_ENOUGH}
        per = self.findings_per_piece
        return {
            "enough": True,
            "pieces": self.pieces,
            "first_pass": {"approved": self.first_approved, "of": self.first_of} if self.first_of else None,
            "confirmed": self.confirmed,
            "confirmed_per_piece": round(float(per), 2) if per is not None else None,
            "checks": {"passed": self.checks_passed, "of": self.checks_of} if self.checks_of else None,
        }


def worse(before: Side, after: Side) -> bool:
    """Did the member's work get WORSE since the habit (the module docstring)?
    False whenever either side lacks enough pieces."""
    if not (before.enough and after.enough):
        return False
    b, a = before.first_pass_rate, after.first_pass_rate
    if b is not None and a is not None and b - a >= FIRST_PASS_FALL:
        return True
    b, a = before.findings_per_piece, after.findings_per_piece
    if b is not None and a is not None and a >= b * FINDINGS_RISE_RATIO and a - b >= FINDINGS_RISE_MIN:
        return True
    b, a = before.check_rate, after.check_rate
    if b is not None and a is not None and b - a >= CHECK_FALL:
        return True
    return False


def windows(accepted_at: datetime, removed_at: "datetime | None" = None, *,
            now: "datetime | None" = None,
            not_before: "datetime | None" = None) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """``((before_since, before_until), (after_since, after_until))`` (naive
    UTC). AFTER runs from the acceptance to its removal (or now); BEFORE is
    the same span just before the acceptance, at most :data:`BEFORE_CAP_DAYS`
    — and ends a microsecond before it, so a piece at the very moment of
    acceptance is counted once (after). v1.423.0 review: ``not_before`` —
    BEFORE never starts earlier (the end of an earlier copy of the SAME
    habit: :func:`not_before_for`); at the acceptance, BEFORE is empty."""
    start = _naive(accepted_at) or _naive(datetime.now(timezone.utc))
    end = _naive(removed_at) or _naive(now) or _naive(datetime.now(timezone.utc)) or start
    if end < start:
        end = start
    span = min(end - start, timedelta(days=BEFORE_CAP_DAYS))
    lo = start - span
    floor = _naive(not_before)
    if floor is not None and floor > lo:
        lo = min(floor, start)
    return (lo, start - timedelta(microseconds=1)), (start, end)


def _replies(member: str) -> Any:
    """The member's REPLIES and when each began (v1.423.0 review): ``(scope,
    turn) -> MIN(start)`` over every team piece of the member in that reply
    (any role — the habits are read at the member's first piece, whatever
    its role), a piece's start being its own session's ``created_at`` (its
    record's when the session row is gone). A subquery the two readers join."""
    from sqlalchemy import func
    from sqlmodel import select

    from ..core.models import Session as RunSession
    from .team_record import TeamRoleRun

    began = func.coalesce(RunSession.created_at, TeamRoleRun.created_at)
    return (
        select(TeamRoleRun.scope.label("rs"), TeamRoleRun.turn.label("rt"), func.min(began).label("first"))
        .select_from(TeamRoleRun)
        .outerjoin(RunSession, RunSession.id == TeamRoleRun.session_id)
        .where(TeamRoleRun.member == member)
        .where(TeamRoleRun.turn != "")
        .group_by(TeamRoleRun.scope, TeamRoleRun.turn)
        .subquery()
    )


def _pieces(engine: Any, member: str, since: datetime,
            until: datetime) -> list[tuple[str, str, str, str, Any, Any]]:
    """``[(scope, turn, review, check_result, placed_at, started_at)]`` — the
    member's code / test pieces whose REPLY began in ``[since, until]`` (a
    piece of no reply: by its own start), and each piece's OWN start, in the
    order they were recorded (the rowid — the member card's first-pass
    order). RAISES on a failed read (the caller makes the measure absent)."""
    from sqlalchemy import and_, func, literal_column
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import Session as RunSession
    from .team_record import TeamRoleRun

    replies = _replies(member)
    own = func.coalesce(RunSession.created_at, TeamRoleRun.created_at)
    placed = func.coalesce(replies.c.first, own)
    with session_scope(engine) as db:
        rows = db.exec(
            select(TeamRoleRun.scope, TeamRoleRun.turn, TeamRoleRun.review, TeamRoleRun.check_result,
                   placed.label("placed"), own.label("own"))
            .select_from(TeamRoleRun)
            .outerjoin(RunSession, RunSession.id == TeamRoleRun.session_id)
            .outerjoin(replies, and_(replies.c.rs == TeamRoleRun.scope, replies.c.rt == TeamRoleRun.turn))
            .where(TeamRoleRun.member == member)
            .where(TeamRoleRun.role.in_(PIECE_ROLES))  # type: ignore[attr-defined]
            .where(placed >= since)
            .where(placed <= until)
            .order_by(literal_column("teamrolerun.rowid"))  # type: ignore[arg-type]
        ).all()
    return [(str(sc or ""), str(tu or ""), str(rv or ""), str(ck or ""), _when(at), _when(began))
            for sc, tu, rv, ck, at, began in rows]


def _confirmed(engine: Any, member: str, since: datetime, until: datetime) -> list[tuple[Any, Any]]:
    """``[(placed_at, started_at)]`` for each of the member's CONFIRMED
    findings (asked, ``fixed``) placed in ``[since, until]``: its reply's
    start (a finding goes with its reply), else its own ``created_at``; and
    when the piece it is about STARTED (its worker session's ``created_at``,
    else the finding's). RAISES on a failed read."""
    from sqlalchemy import and_, func
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import Session as RunSession
    from .team_findings import FIXED, TeamFinding

    replies = _replies(member)
    placed = func.coalesce(replies.c.first, TeamFinding.created_at)
    own = func.coalesce(RunSession.created_at, TeamFinding.created_at)
    with session_scope(engine) as db:
        rows = db.exec(
            select(placed.label("placed"), own.label("own"))
            .select_from(TeamFinding)
            .outerjoin(RunSession, RunSession.id == TeamFinding.worker_session)
            .outerjoin(replies, and_(replies.c.rs == TeamFinding.scope, replies.c.rt == TeamFinding.turn))
            .where(TeamFinding.member == member)
            .where(TeamFinding.asked == True)  # noqa: E712 — SQL, not Python
            .where(TeamFinding.status == FIXED)
            .where(placed >= since)
            .where(placed <= until)
        ).all()
    return [(_when(at), _when(began)) for at, began in rows]


def _side_of(pieces: list[tuple[str, str, str, str, Any, Any]], confirmed: list[tuple[Any, Any]],
             since: datetime, until: datetime, *, removed: "datetime | None" = None) -> Side:
    """One side from what :func:`_pieces` / :func:`_confirmed` read (pure).
    ``removed`` (the after side of a removed habit): a piece — or a finding
    on a piece — that STARTED after it is left out (the module docstring)."""
    from .team_record import CHECK_FAILED, CHECK_PASSED, REVIEW_GOOD

    if until < since:
        return Side()  # an empty window (a habit accepted this very moment)
    count = checks_of = passed = 0
    first: dict[tuple[str, str], str] = {}
    for scope, turn, review, check, at, began in pieces:
        if at is None or not (since <= at <= until):
            continue
        if removed is not None and began is not None and began > removed:
            continue
        if review:
            first.setdefault((scope, turn), review)
        if not (check or review):
            continue
        count += 1
        if check in (CHECK_PASSED, CHECK_FAILED):
            checks_of += 1
            passed += check == CHECK_PASSED
    return Side(
        pieces=count,
        first_approved=sum(1 for v in first.values() if v == REVIEW_GOOD),
        first_of=len(first),
        confirmed=sum(
            1 for at, began in confirmed
            if at is not None and since <= at <= until
            and not (removed is not None and began is not None and began > removed)
        ),
        checks_passed=passed,
        checks_of=checks_of,
    )


def side(engine: Any, member: str, since: datetime, until: datetime) -> Side:
    """The member's record over ``[since, until]``. RAISES on a failed read."""
    if until < since:
        return Side()  # an empty window (a habit accepted this very moment)
    return _side_of(_pieces(engine, member, since, until), _confirmed(engine, member, since, until),
                    since, until)


@dataclass(frozen=True)
class Measure:
    before: Side
    after: Side

    @property
    def worse(self) -> bool:
        return worse(self.before, self.after)

    def view(self) -> dict[str, Any]:
        return {"before": self.before.view(), "after": self.after.view(), "worse": self.worse}


def measure(engine: Any, member: str, accepted_at: Any, removed_at: Any = None, *,
            now: "datetime | None" = None, not_before: Any = None) -> "Measure | None":
    """One habit's before and after (the module docstring), or None when it
    cannot be read — never zeros for an unreadable record. ``not_before``:
    :func:`windows`. Two queries (both sides read at once). BLOCKING; never
    raises."""
    name = str(member or "").strip()
    start = _when(accepted_at)
    if engine is None or not name or start is None:
        return None
    gone = _when(removed_at) if removed_at else None
    try:
        (b_lo, b_hi), (a_lo, a_hi) = windows(start, gone, now=now, not_before=_when(not_before))
        lo, hi = min(b_lo, a_lo), max(b_hi, a_hi)
        pieces, confirmed = _pieces(engine, name, lo, hi), _confirmed(engine, name, lo, hi)
        return Measure(before=_side_of(pieces, confirmed, b_lo, b_hi),
                       after=_side_of(pieces, confirmed, a_lo, a_hi, removed=gone))
    except Exception:  # noqa: BLE001 — absent, never zeros
        log.debug("a working habit's measure could not be read", exc_info=True)
        return None



def _copies(engine: Any, members: Iterable[str]) -> "dict[str, list[tuple[str, datetime, datetime | None, str]]]":
    """``{member: [(id, accepted_at, removed_at, text)]}`` — every habit the
    members were ever given (removed ones too). RAISES on a failed read."""
    from sqlmodel import select

    from ..core.db import session_scope
    from .team_notes import TeamNote

    names = [str(m) for m in members if str(m or "")]
    out: dict[str, list[tuple[str, datetime, datetime | None, str]]] = {n: [] for n in names}
    if not names:
        return out
    with session_scope(engine) as db:
        rows = db.exec(
            select(TeamNote.id, TeamNote.member, TeamNote.accepted_at, TeamNote.removed_at, TeamNote.text)
            .where(TeamNote.member.in_(names))  # type: ignore[attr-defined]
        ).all()
    for nid, member, accepted, removed, text in rows:
        at = _when(accepted)
        if at is not None and str(member) in out:
            out[str(member)].append((str(nid), at, _when(removed), str(text or "")))
    return out


def not_before_for(view: dict[str, Any],
                   copies: "list[tuple[str, datetime, datetime | None, str]]") -> "datetime | None":
    """v1.423.0 review: where a habit's BEFORE side may reach back to — the
    latest end of an EARLIER copy of the same habit
    (``team_notes.same_habit``: removed, then accepted again from new
    findings): its removal, or the new acceptance itself while that copy was
    never removed (then there is no "before"). None when the habit was never
    given before."""
    from .team_notes import same_habit

    start = _when(view.get("accepted_at"))
    if start is None:
        return None
    text, me = str(view.get("text") or ""), str(view.get("id") or "")
    floor: "datetime | None" = None
    for nid, accepted, removed, other in copies:
        if nid == me or accepted >= start or not same_habit(text, other):
            continue
        end = start if removed is None or removed > start else removed
        if floor is None or end > floor:
            floor = end
    return floor


def attach(engine: Any, notes: "dict[str, dict[str, Any]]", *, now: "datetime | None" = None) -> int:
    """Put a ``measure`` on each accepted habit of ``notes``
    (``team_notes.notes_for``'s answer: ``{member: {accepted: [note_view…]}}``)
    — IN PLACE. A habit whose measure cannot be read gets NO ``measure`` key.
    Reads; never removes, never writes. Returns how many were measured.
    BLOCKING; never raises."""
    done = tried = 0
    try:
        if engine is None or not notes:
            return 0
        # v1.423.0 review: every habit the members were ever given — a habit
        # accepted AGAIN is compared with the time it was not in force. When
        # that cannot be read, no "before" can be trusted: no measure.
        copies = _copies(engine, notes.keys())
        for member, mine in notes.items():
            for view in (mine or {}).get("accepted") or []:
                if not isinstance(view, dict):
                    continue
                if tried >= MAX_MEASURED:
                    return done
                tried += 1  # v1.423.0 review: bounded by what is TRIED
                got = measure(engine, str(member), view.get("accepted_at"), view.get("removed_at"), now=now,
                              not_before=not_before_for(view, copies.get(str(member)) or []))
                if got is not None:
                    view["measure"] = got.view()
                    done += 1
    except Exception:  # noqa: BLE001 — a measure never breaks the team view
        log.debug("working habits' measures could not be attached", exc_info=True)
    return done


def measures_for(engine: Any, notes: Iterable[Any], *, now: "datetime | None" = None) -> dict[str, Any]:
    """``{note id: Measure}`` for ``TeamNote`` rows (an unreadable one is left
    out). BLOCKING; never raises."""
    out: dict[str, Any] = {}
    notes = list(notes or [])
    try:
        copies = _copies(engine, {str(getattr(n, "member", "") or "") for n in notes})
    except Exception:  # noqa: BLE001 — no "before" can be trusted: none measured
        log.debug("working habits could not be read", exc_info=True)
        return out
    for note in notes:
        member = str(getattr(note, "member", "") or "")
        view = {"id": getattr(note, "id", ""), "text": getattr(note, "text", ""),
                "accepted_at": getattr(note, "accepted_at", None)}
        got = measure(engine, member, view["accepted_at"], getattr(note, "removed_at", None), now=now,
                      not_before=not_before_for(view, copies.get(member) or []))
        if got is not None:
            out[str(getattr(note, "id", "") or "")] = got
    return out


__all__ = [
    "BEFORE_CAP_DAYS", "CHECK_FALL", "FINDINGS_RISE_MIN", "FINDINGS_RISE_RATIO", "FIRST_PASS_FALL",
    "MAX_MEASURED", "MIN_PIECES", "Measure", "NOT_ENOUGH", "PIECE_ROLES", "Side", "attach", "measure",
    "measures_for", "not_before_for", "side", "windows", "worse",
]
