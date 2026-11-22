"""THE RESULT OF A CHANGE YOU ACCEPTED, IN NUMBERS (18-a, build-out plan
R2.8, idea 18 "Proof that a change helped"). PURE: no database, no model, no
clock of its own (``now`` is always passed in).

``improve/results`` reads the records and hands each change's tasks here;
this module splits them at the moment the change was accepted, counts each
side and says, in words built from the numbers alone, what happened. One
counter per kind:

* :func:`count_coach` (an agent's instructions, ``hands_off``): the agent's
  own runs, as ``eval.autonomy`` counts an agent run (a Continue follow-up is
  a nudge on the run it continues, never a task of its own); a task is GOOD
  when it ended well (``outcome == "completed"``) and the person did not
  have to help (``autonomy.helped``: no nudge, no answered question or card,
  no retry);
* :func:`count_skill` (a new skill) and :func:`count_refine` (an improved
  skill), ``ended_well``: the tasks that loaded the skill, GOOD when the
  task ended well by the skill learner's own record (its ``OutcomeRecord``,
  else the run's status);
* :func:`habit_sides` (a working habit, ``team_record``):
  ``agents/team_measure.measure`` exactly as Settings › Model team shows it.

THE RULES, FIXED HERE BEFORE ANY RECORD IS READ (the held-out rule: they are
never moved after reading which changes they flag):

* A task belongs to the side its own START is on: a run that began before
  the accept counts BEFORE, however late it finished.
* AFTER: from the accept to :data:`AFTER_MAX` tasks or :data:`AFTER_DAYS`
  days, whichever comes first, and never past the next accepted change of
  the same thing (``until``: from then on the work ran under that one). A
  NEW skill has no before side to match, so it counts EVERY task in those
  days (no :data:`AFTER_MAX`), and its words say which days they were:
  "since" only while the two weeks are still running.
* BEFORE: the SAME number of tasks just before the accept, at most
  :data:`BEFORE_DAYS` days back.
* Under :data:`MIN_TASKS` on either side there are NO verdict and no "before
  and after" sentence: only "Not enough work yet" (D-18.1).
* VERDICT: the share of good tasks fell by :data:`SHIFT` (20 points) or more
  is ``worse``, rose by 20 or more is ``better``, anything between is
  ``same``. Compared EXACTLY with ``fractions.Fraction`` (``0.6 - 0.4`` is
  ``0.19999999999999996`` in floating point; a fall of exactly 20 points is
  worse: the ``agents/team_measure`` lesson). A new skill has no before, so
  no verdict; a habit says only ``worse`` (``team_measure.worse``).
* The words say "since" and "before", never "because": the work itself may
  have got easier.

A verdict is SHOWN, never acted on. Nothing here (or anywhere in 18-a)
undoes a change; 18-b offers the undo, and only the owner's press does it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Iterable

#: Fewer tasks than this on a side: "Not enough work yet" (D-18.1).
MIN_TASKS = 5
#: The after side ends at this many tasks…
AFTER_MAX = 10
#: …or this many days after the accept (D-18.1), whichever comes first.
AFTER_DAYS = 14
#: The before side reaches back at most this far.
BEFORE_DAYS = 30
#: A day after the after side's last day its numbers stop moving (a run
#: that started inside it and was still running at a check has ended by
#: then), so the row is not measured again.
SETTLE_DAYS = 1
#: The share that must move for a verdict: 20 points, exactly.
SHIFT = Fraction(1, 5)

# The states of a result (``ChangeResultRecord.state``).
WAITING, NOT_ENOUGH, READY, NOT_MEASURED = "waiting", "not_enough", "ready", "not_measured"
# Its verdicts ("" = no comparison).
BETTER, SAME, WORSE = "better", "same", "worse"
# What is counted.
HANDS_OFF, ENDED_WELL, TEAM_RECORD = "hands_off", "ended_well", "team_record"
# Why the after side ended (``Counted.ended``).
ENDED_DAYS, ENDED_NEXT, ENDED_REMOVED = "days", "next", "removed"

KIND_COACH, KIND_SKILL, KIND_REFINE, KIND_HABIT = "coach", "skill", "skill_refine", "habit"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


def naive(at: Any) -> "datetime | None":
    """A stamp as SQLite keeps it (naive UTC); ISO text is read too; None
    when unreadable."""
    if isinstance(at, str):
        text = at.strip()
        if not text:
            return None
        try:
            at = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(at, datetime):
        return None
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def day_words(at: Any, now: Any = None) -> str:
    """"12 March" in this PC's own time (the year too when it is not this
    year's). English month names, whatever the PC's locale."""
    when = naive(at)
    if when is None:
        return ""
    local = when.replace(tzinfo=timezone.utc).astimezone()
    ref = naive(now)
    this_year = ref.replace(tzinfo=timezone.utc).astimezone().year if ref is not None else local.year
    text = f"{local.day} {_MONTHS[local.month - 1]}"
    return text if local.year == this_year else f"{text} {local.year}"


# --------------------------------------------------------------------------- #
# one task, one side
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Outcome:
    """One task: when it STARTED (naive UTC) and whether it counts as good."""

    start: datetime
    good: bool


@dataclass(frozen=True)
class Side:
    """One side of an accept: ``n`` tasks, ``good`` of them good (None for a
    habit, whose record is several numbers: ``detail`` is the Model team's
    own side view), and the side's numbers in words."""

    n: int = 0
    good: "int | None" = 0
    words: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def enough(self) -> bool:
        return self.n >= MIN_TASKS

    @property
    def share(self) -> "Fraction | None":
        if not self.n or self.good is None:
            return None
        return Fraction(self.good, self.n)

    def view(self) -> dict[str, Any]:
        out: dict[str, Any] = {"n": self.n, "good": self.good, "words": self.words}
        if self.detail:
            out["detail"] = dict(self.detail)
        return out

    @classmethod
    def from_view(cls, raw: Any) -> "Side | None":
        """A stored side, or None when there is none (never measured: no
        numbers, never zeros)."""
        if not isinstance(raw, dict) or not isinstance(raw.get("n"), int) or isinstance(raw.get("n"), bool):
            return None
        good = raw.get("good")
        if good is not None and (not isinstance(good, int) or isinstance(good, bool)):
            return None
        detail = raw.get("detail")
        return cls(n=max(0, raw["n"]), good=good, words=str(raw.get("words") or ""),
                   detail=dict(detail) if isinstance(detail, dict) else {})


@dataclass(frozen=True)
class Counted:
    """A change's two sides and where its after side ends (``end``) and why
    (``ended``: :data:`ENDED_DAYS`, :data:`ENDED_NEXT`, :data:`ENDED_REMOVED`)."""

    before: Side
    after: Side
    end: datetime
    ended: str
    #: A habit only: ``team_measure``'s own flag (its rule, not this one's).
    worse: bool = False


def after_end(accepted_at: datetime, until: "datetime | None" = None) -> tuple[datetime, str]:
    """Where the after side ends: :data:`AFTER_DAYS` after the accept, or the
    next accepted change of the same thing when that came sooner."""
    end = accepted_at + timedelta(days=AFTER_DAYS)
    nxt = naive(until)
    if nxt is not None and accepted_at <= nxt < end:
        return nxt, ENDED_NEXT
    return end, ENDED_DAYS


def split(tasks: Iterable[Outcome], accepted_at: Any, *, until: Any = None,
          cap: "int | None" = AFTER_MAX) -> tuple[list[Outcome], list[Outcome], datetime, str]:
    """``(before, after, end, ended)`` by the rules in the module docstring.
    A task whose start is unreadable is on neither side. ``cap``: the after
    side's most tasks (None: every task until ``end``, a new skill's)."""
    accepted = naive(accepted_at)
    if accepted is None:
        raise ValueError("a change with no time it was accepted cannot be split")
    end, ended = after_end(accepted, naive(until))
    known = sorted((t for t in tasks if isinstance(t.start, datetime)), key=lambda t: t.start)
    after = [t for t in known if accepted <= t.start < end]
    if cap is not None:
        after = after[:cap]
    floor = accepted - timedelta(days=BEFORE_DAYS)
    earlier = [t for t in known if floor <= t.start < accepted]
    before = earlier[-len(after):] if after else []
    return before, after, end, ended


def _side(tasks: list[Outcome], phrase: str) -> Side:
    good = sum(1 for t in tasks if t.good)
    return Side(n=len(tasks), good=good, words=phrase.format(good=good, n=len(tasks)))


def count_coach(tasks: Iterable[Outcome], accepted_at: Any, *, until: Any = None) -> Counted:
    """An agent's instructions: its runs, good = ended well with no help."""
    before, after, end, ended = split(tasks, accepted_at, until=until)
    phrase = "{good} of {n} tasks without help"
    return Counted(_side(before, phrase), _side(after, phrase), end, ended)


def count_skill(tasks: Iterable[Outcome], accepted_at: Any, *, until: Any = None) -> Counted:
    """A NEW skill: EVERY task that loaded it in its after side's days (it
    had no before, so there is no number of tasks to match)."""
    _before, after, end, ended = split(tasks, accepted_at, until=until, cap=None)
    return Counted(Side(), _side(after, "used in {n} tasks; {good} ended well"), end, ended)


def count_refine(tasks: Iterable[Outcome], accepted_at: Any, *, until: Any = None) -> Counted:
    """An IMPROVED skill: the tasks that loaded it, before and after."""
    before, after, end, ended = split(tasks, accepted_at, until=until)
    phrase = "{good} of {n} tasks ended well"
    return Counted(_side(before, phrase), _side(after, phrase), end, ended)


def _team_side(raw: Any) -> Side:
    view = raw if isinstance(raw, dict) else {}
    pieces = view.get("pieces")
    n = pieces if isinstance(pieces, int) and not isinstance(pieces, bool) else 0
    return Side(n=max(0, n), good=None, words=f"{n} pieces of work", detail=dict(view))


def habit_sides(measure_view: dict[str, Any], accepted_at: Any, *, removed_at: Any = None,
                now: Any = None) -> Counted:
    """A working habit: ``team_measure.Measure.view()`` as Settings › Model
    team carries it. Its after side runs to its removal, or to now."""
    end = naive(removed_at) or naive(now) or naive(accepted_at) or datetime(1970, 1, 1)
    return Counted(_team_side(measure_view.get("before")), _team_side(measure_view.get("after")), end,
                   ENDED_REMOVED if naive(removed_at) is not None else "",
                   worse=measure_view.get("worse") is True)


# --------------------------------------------------------------------------- #
# the judgement: state, verdict and the words (from the numbers alone)
# --------------------------------------------------------------------------- #
def verdict(before: Side, after: Side) -> str:
    """``better`` | ``same`` | ``worse`` by the fixed rule, or "" when either
    side has under :data:`MIN_TASKS` tasks or no share."""
    if not (before.enough and after.enough):
        return ""
    b, a = before.share, after.share
    if b is None or a is None:
        return ""
    if b - a >= SHIFT:
        return WORSE
    if a - b >= SHIFT:
        return BETTER
    return SAME


@dataclass(frozen=True)
class Judged:
    state: str
    verdict: str
    words: str
    #: The after side can take no more work (its window is over).
    over: bool
    #: Its numbers stop moving: it is not measured again.
    final: bool


def _pair(raw: Any, key: str) -> "tuple[int, int] | None":
    if not isinstance(raw, dict):
        return None
    of, x = raw.get("of"), raw.get(key)
    if not isinstance(of, int) or not isinstance(x, int) or isinstance(of, bool) or of <= 0:
        return None
    return min(max(0, x), of), of


def _per(raw: Any) -> "float | None":
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None


def _num(v: float) -> str:
    text = f"{round(v, 2):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def habit_numbers(before: Side, after: Side) -> str:
    """The Model team's own line, part by part, each only when both sides
    have it: "first-pass approval 2 of 6 → 5 of 7 · confirmed findings per
    piece 0.5 → 0.2 · checks passed 3 of 5 → 6 of 6" (``lib/modelTeam.
    measureWords``)."""
    b, a = before.detail, after.detail
    parts: list[str] = []
    bf, af = _pair(b.get("first_pass"), "approved"), _pair(a.get("first_pass"), "approved")
    if bf and af:
        parts.append(f"first-pass approval {bf[0]} of {bf[1]} → {af[0]} of {af[1]}")
    bp, ap = _per(b.get("confirmed_per_piece")), _per(a.get("confirmed_per_piece"))
    if bp is not None and ap is not None:
        parts.append(f"confirmed findings per piece {_num(bp)} → {_num(ap)}")
    bc, ac = _pair(b.get("checks"), "passed"), _pair(a.get("checks"), "passed")
    if bc and ac:
        parts.append(f"checks passed {bc[0]} of {bc[1]} → {ac[0]} of {ac[1]}")
    return " · ".join(parts)


def judge(kind: str, counted: Counted, *, accepted_at: Any, now: Any, name: str = "") -> Judged:
    """The state, the verdict and the ONE sentence for a counted change. The
    words are built from the numbers alone (never a model's), and say
    "since" and "before", never "because"."""
    moment = naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    day = day_words(accepted_at, moment)
    b, a = counted.before, counted.after
    if kind == KIND_HABIT:
        removed = counted.ended == ENDED_REMOVED
        over = removed
        final = removed and moment >= counted.end + timedelta(days=SETTLE_DAYS)
        gone = day_words(counted.end, moment) if removed else ""
        if not a.enough:
            words = (f"Not enough work to tell: {a.n} of {MIN_TASKS} pieces of work before you removed it "
                     f"on {gone}." if removed else
                     f"Not enough work yet: {a.n} of {MIN_TASKS} pieces of work since you accepted this on {day}.")
            return Judged(NOT_ENOUGH, "", words, over, final)
        if not b.enough:
            return Judged(NOT_ENOUGH, "", f"Not enough earlier work to compare: {b.n} of {MIN_TASKS} pieces of "
                          f"work before you accepted this on {day}.", over, final)
        numbers = habit_numbers(b, a)
        head = f"From {day} until you removed it on {gone}" if removed else f"Since you accepted this on {day}"
        words = (f"{head}: {numbers}." if numbers else
                 f"{head}, no piece of work was judged the same way before and after.")
        return Judged(READY, WORSE if counted.worse else "", words, over, final)

    over = moment >= counted.end
    final = moment >= counted.end + timedelta(days=SETTLE_DAYS)
    if not a.enough:
        if not over:
            words = f"Not enough work yet: {a.n} of {MIN_TASKS} tasks since you accepted this on {day}."
        elif counted.ended == ENDED_NEXT:
            words = (f"Not enough work to tell: {a.n} of {MIN_TASKS} tasks between your accept on {day} and the "
                     "next change to it.")
        else:
            words = (f"Not enough work to tell: {a.n} of {MIN_TASKS} tasks in the two weeks after you accepted "
                     f"this on {day}.")
        return Judged(NOT_ENOUGH, "", words, over, final)
    if kind == KIND_SKILL:
        # Every use in the after side's days, and the words say which days:
        # "since" only while they are still running.
        if not over:
            span = f"since you accepted it on {day}"
        elif counted.ended == ENDED_NEXT:
            span = f"between your accept on {day} and the next change to it"
        else:
            span = f"in the two weeks after you accepted it on {day}"
        return Judged(READY, "", f"Used in {a.n} tasks {span}; {a.good} ended well.", over, final)
    if not b.enough:
        return Judged(NOT_ENOUGH, "", f"Not enough earlier work to compare: {b.n} of {MIN_TASKS} tasks in the "
                      f"{BEFORE_DAYS} days before you accepted this on {day}.", over, final)
    if kind == KIND_REFINE:
        words = (f"Since you accepted this on {day}, tasks that used {name} ended well {a.good} of {a.n} times. "
                 f"Before, it was {b.good} of {b.n}.")
    else:
        words = (f"Since you accepted this on {day}, {name} finished {a.good} of {a.n} tasks without help. "
                 f"Before, it was {b.good} of {b.n}.")
    return Judged(READY, verdict(b, a), words, over, final)


__all__ = [
    "AFTER_DAYS", "AFTER_MAX", "BEFORE_DAYS", "BETTER", "Counted", "ENDED_DAYS", "ENDED_NEXT", "ENDED_REMOVED",
    "ENDED_WELL", "HANDS_OFF", "Judged", "KIND_COACH", "KIND_HABIT", "KIND_REFINE", "KIND_SKILL", "MIN_TASKS",
    "NOT_ENOUGH", "NOT_MEASURED", "Outcome", "READY", "SAME", "SETTLE_DAYS", "SHIFT", "Side", "TEAM_RECORD",
    "WAITING", "WORSE", "after_end", "count_coach", "count_refine", "count_skill", "day_words", "habit_numbers",
    "habit_sides", "judge", "naive", "split", "verdict",
]
