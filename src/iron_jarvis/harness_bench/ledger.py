"""The budget ledger: every model call and every rollout, counted once.

Harness² principle 6 — budgets are an identity, not a hope. Each task run is a
ROLLOUT; each model call inside it is a CALL entry, written at the moment the
runner observes it:

* Jarvis — the bench wraps the platform router for the run, so every
  ``stream()``/``complete()`` the turn makes is entered with the usage its
  final frame reported (:mod:`.runners`). The run RECORD's totals come from a
  different place — the lane's own ``done`` receipt — so the identity
  ``record == ledger`` checks that the turn billed every call it made.
* Claude Code / Codex — the CLI is one opaque rollout to us: its own report is
  the one source (Claude's ``result`` line; Codex's ``turn.completed`` usage),
  entered as one entry per reported turn (an unreported rollout is one entry
  with unknown usage). For these the per-run identity is bookkeeping; the
  meaningful identity is the plan's (:meth:`BudgetLedger.verify_plan`): every
  planned task is either a reused result or exactly one rollout, and the run
  records sum to the ledger.

REPEATS (v1.383.0): with ``--repeats N`` a task runs N times, each its own
rollout. A plan, a rollout and a record are keyed by RUN (:func:`run_key`):
the task id for repeat 1 — exactly the key a one-run plan always used — and
``<task id>.r<k>`` for repeat k ≥ 2.

Cost is kept as two numbers that are never added together (v1.300.0):
METERED money (an API bill) and the LIST-PRICE EQUIVALENT of work a
subscription already covers. A local model costs 0. An unknown price or token
count is ``None`` — never a fabricated 0 — and a total with any unknown part
is unknown.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

#: The record fields the identity compares.
TOTAL_FIELDS: tuple[str, ...] = (
    "tokens_in", "tokens_out", "cost_metered_usd", "cost_list_price_usd",
)


class BudgetMismatch(RuntimeError):
    """A record's totals differ from the ledger's, or the plan is not covered."""


def run_key(task_id: str, repeat: int = 1) -> str:
    """The key of one RUN of a task: the task id for repeat 1 (a one-run
    plan's key, unchanged), ``<task id>.r<k>`` for repeat k ≥ 2. Also the
    run's folder name under ``runs/``."""
    k = int(repeat or 1)
    return task_id if k <= 1 else f"{task_id}.r{k}"


def record_key(record: Mapping[str, Any]) -> str:
    """:func:`run_key` of a run record (a record with no ``repeat`` is repeat 1)."""
    return run_key(str(record.get("task_id") or ""), int(record.get("repeat") or 1))


@dataclass
class CallEntry:
    provider: str = ""
    model: str = ""
    tokens_in: int | None = 0
    tokens_out: int | None = 0
    tokens_cached: int | None = 0
    cost_metered_usd: float | None = 0.0
    cost_list_price_usd: float | None = 0.0
    #: The call returned its final answer (a call cut off part-way reports nothing).
    complete: bool = True
    #: The call offered tools (a tool round) — False for a tool-less final answer.
    with_tools: bool = False
    source: str = ""


def _add(a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    return a + b


def sum_fields(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The :data:`TOTAL_FIELDS` summed over ``rows``; ``None`` (unknown) wins."""
    out: dict[str, Any] = {
        "tokens_in": 0, "tokens_out": 0, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.0,
    }
    for row in rows:
        for name in TOTAL_FIELDS:
            out[name] = _add(out[name], row.get(name))
    return out


@dataclass
class Rollout:
    task_id: str
    runner: str
    calls: list[CallEntry] = field(default_factory=list)
    closed: bool = False

    def add(self, entry: CallEntry) -> CallEntry:
        if self.closed:
            raise BudgetMismatch(f"a call was entered for {self.task_id} after its rollout closed")
        self.calls.append(entry)
        return entry

    def totals(self) -> dict[str, Any]:
        out = sum_fields(asdict(c) for c in self.calls)
        out["model_calls"] = len(self.calls)
        return out


#: Money is compared at the precision the run record carries it (v1.370.4).
#: The chat lane reports a turn's cost rounded to the millionth of a dollar
#: (``routes/chat.py`` rounds ``usage.cost_usd`` to 6 places) while the ledger
#: adds each call's unrounded cost, so a record may sit up to half a millionth
#: away from its ledger — a live run read "the run says 0.065993, the ledger
#: says 0.0659935" and flagged an honest record. A sum of N records may drift
#: by N halves. A missing or doubled call is orders of magnitude larger.
MONEY_FIELDS: tuple[str, ...] = ("cost_metered_usd", "cost_list_price_usd")
MONEY_ROUNDING_USD = 5e-7


def _equal(a: Any, b: Any, *, abs_tol: float = 1e-9) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=abs_tol)
    return a == b


def differences(
    record: Mapping[str, Any], totals: Mapping[str, Any], *, records: int = 1
) -> list[str]:
    """Every field where ``record`` (the sum of ``records`` run records) and
    the ledger's ``totals`` disagree. Tokens must match exactly; money within
    the records' own rounding."""
    money_tol = MONEY_ROUNDING_USD * max(1, records) + 1e-9
    return [
        f"{name}: the run says {record.get(name)!r}, the ledger says {totals.get(name)!r}"
        for name in TOTAL_FIELDS
        if not _equal(
            record.get(name), totals.get(name),
            abs_tol=money_tol if name in MONEY_FIELDS else 1e-9,
        )
    ]


class BudgetLedger:
    """One per benchmark invocation (one configuration)."""

    def __init__(self) -> None:
        self.rollouts: list[Rollout] = []

    def begin(self, task_id: str, runner: str) -> Rollout:
        rollout = Rollout(task_id=task_id, runner=runner)
        self.rollouts.append(rollout)
        return rollout

    def verify_record(self, record: Mapping[str, Any], rollout: Rollout) -> None:
        """Raise :class:`BudgetMismatch` unless the run record's totals equal
        the ledger's for its rollout. Closes the rollout either way."""
        rollout.closed = True
        diff = differences(record, rollout.totals())
        if diff:
            raise BudgetMismatch(
                f"the budget does not add up for {rollout.task_id}: " + "; ".join(diff)
            )

    def verify_plan(
        self,
        planned: Iterable[str],
        reused: Iterable[str],
        records: Iterable[Mapping[str, Any]],
    ) -> None:
        """Every planned run (:func:`run_key`) is either reused or exactly one
        rollout, nothing ran that was not planned, and the executed records
        sum to the ledger."""
        planned_ids = list(planned)
        reused_ids = set(reused)
        ran = [r.task_id for r in self.rollouts]
        problems: list[str] = []
        for task_id in planned_ids:
            count = ran.count(task_id)
            if task_id in reused_ids:
                if count:
                    problems.append(f"{task_id} was both reused and run")
            elif count != 1:
                problems.append(f"{task_id} ran {count} times (planned once)")
        extra = sorted(set(ran) - set(planned_ids))
        if extra:
            problems.append("ran without being planned: " + ", ".join(extra))
        executed = [r for r in records if not r.get("reused")]
        if len(executed) != len(self.rollouts):
            problems.append(
                f"{len(executed)} run records for {len(self.rollouts)} rollouts in the ledger"
            )
        # A record already flagged by :meth:`verify_record` (``ledger_ok``
        # False) carries its own mismatch; the sum covers every other one.
        flagged = {record_key(r) for r in executed if r.get("ledger_ok") is False}
        grand = sum_fields(
            asdict(c) for r in self.rollouts if r.task_id not in flagged for c in r.calls
        )
        counted = [r for r in executed if record_key(r) not in flagged]
        problems += differences(sum_fields(counted), grand, records=len(counted))
        if problems:
            raise BudgetMismatch("the run plan does not add up: " + "; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollouts": [
                {"task_id": r.task_id, "runner": r.runner, "totals": r.totals(),
                 "calls": [asdict(c) for c in r.calls]}
                for r in self.rollouts
            ]
        }
