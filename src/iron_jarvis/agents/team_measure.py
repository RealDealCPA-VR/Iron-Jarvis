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
  the hour before it, never with a month of other work).

Per side (:class:`Side`):

* PIECES — the member's ``code`` / ``test`` pieces (``team_record.
  TeamRoleRun``) in the window that a check or a review judged (a piece
  nothing judged says nothing about how it was done);
* FIRST-PASS APPROVAL — ``team_findings.member_findings(since=, until=)``:
  the reviewer's verdict on the member's EARLIEST judged piece of each reply
  (``approved`` of ``of`` replies) — the member card's own definition, reused;
* CONFIRMED FINDINGS PER PIECE — the findings the lead was ASKED to answer
  and that were confirmed fixed (``findings_for``'s ``confirmed``: asked rows
  only), over the side's pieces;
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
no ``measure`` key) — never a comparison of zeros.
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
#: At most this many habits are measured in one read (each is six small
#: queries); the rest carry no measure.
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
            now: "datetime | None" = None) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """``((before_since, before_until), (after_since, after_until))`` (naive
    UTC). AFTER runs from the acceptance to its removal (or now); BEFORE is
    the same span just before the acceptance, at most :data:`BEFORE_CAP_DAYS`
    — and ends a microsecond before it, so a piece at the very moment of
    acceptance is counted once (after)."""
    start = _naive(accepted_at) or _naive(datetime.now(timezone.utc))
    end = _naive(removed_at) or _naive(now) or _naive(datetime.now(timezone.utc)) or start
    if end < start:
        end = start
    span = min(end - start, timedelta(days=BEFORE_CAP_DAYS))
    return (start - span, start - timedelta(microseconds=1)), (start, end)


def _pieces(engine: Any, member: str, since: datetime, until: datetime) -> tuple[int, int, int]:
    """``(pieces, checks_of, checks_passed)`` — the member's code / test
    pieces in ``[since, until]`` that a check or a review judged. RAISES on a
    failed read (the caller makes the measure absent)."""
    from sqlmodel import select

    from ..core.db import session_scope
    from .team_record import CHECK_FAILED, CHECK_PASSED, TeamRoleRun

    with session_scope(engine) as db:
        rows = db.exec(
            select(TeamRoleRun.check_result, TeamRoleRun.review)
            .where(TeamRoleRun.member == member)
            .where(TeamRoleRun.role.in_(PIECE_ROLES))  # type: ignore[attr-defined]
            .where(TeamRoleRun.created_at >= since)
            .where(TeamRoleRun.created_at <= until)
        ).all()
    pieces = checks_of = passed = 0
    for check, review in rows:
        check, review = str(check or ""), str(review or "")
        if not (check or review):
            continue
        pieces += 1
        if check in (CHECK_PASSED, CHECK_FAILED):
            checks_of += 1
            passed += check == CHECK_PASSED
    return pieces, checks_of, passed


def side(engine: Any, member: str, since: datetime, until: datetime) -> Side:
    """The member's record over ``[since, until]``. RAISES on a failed read."""
    from .team_findings import member_findings

    if until < since:
        return Side()  # an empty window (a habit accepted this very moment)
    pieces, checks_of, passed = _pieces(engine, member, since, until)
    nums = member_findings(engine, member, since=since, until=until, strict=True)
    fp = nums.get("first_pass") or {}
    return Side(
        pieces=pieces,
        first_approved=int(fp.get("approved") or 0),
        first_of=int(fp.get("of") or 0),
        confirmed=int(nums.get("confirmed") or 0),
        checks_passed=passed,
        checks_of=checks_of,
    )


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
            now: "datetime | None" = None) -> "Measure | None":
    """One habit's before and after (the module docstring), or None when it
    cannot be read — never zeros for an unreadable record. BLOCKING; never
    raises."""
    name = str(member or "").strip()
    start = _when(accepted_at)
    if engine is None or not name or start is None:
        return None
    gone = _when(removed_at) if removed_at else None
    try:
        (b_lo, b_hi), (a_lo, a_hi) = windows(start, gone, now=now)
        return Measure(before=side(engine, name, b_lo, b_hi), after=side(engine, name, a_lo, a_hi))
    except Exception:  # noqa: BLE001 — absent, never zeros
        log.debug("a working habit's measure could not be read", exc_info=True)
        return None


def attach(engine: Any, notes: "dict[str, dict[str, Any]]", *, now: "datetime | None" = None) -> int:
    """Put a ``measure`` on each accepted habit of ``notes``
    (``team_notes.notes_for``'s answer: ``{member: {accepted: [note_view…]}}``)
    — IN PLACE. A habit whose measure cannot be read gets NO ``measure`` key.
    Reads; never removes, never writes. Returns how many were measured.
    BLOCKING; never raises."""
    done = 0
    try:
        for member, mine in (notes or {}).items():
            for view in (mine or {}).get("accepted") or []:
                if not isinstance(view, dict):
                    continue
                if done >= MAX_MEASURED:
                    return done
                got = measure(engine, str(member), view.get("accepted_at"), view.get("removed_at"), now=now)
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
    for note in notes or []:
        got = measure(engine, getattr(note, "member", ""), getattr(note, "accepted_at", None),
                      getattr(note, "removed_at", None), now=now)
        if got is not None:
            out[str(getattr(note, "id", "") or "")] = got
    return out


__all__ = [
    "BEFORE_CAP_DAYS", "CHECK_FALL", "FINDINGS_RISE_MIN", "FINDINGS_RISE_RATIO", "FIRST_PASS_FALL",
    "MAX_MEASURED", "MIN_PIECES", "Measure", "NOT_ENOUGH", "PIECE_ROLES", "Side", "attach", "measure",
    "measures_for", "side", "windows", "worse",
]
