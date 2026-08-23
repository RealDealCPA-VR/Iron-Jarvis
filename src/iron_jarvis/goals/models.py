"""Goal records — a STANDING INTENTION with a contract, a hard budget, a
verifier, and a lifecycle that survives restarts (G1, v1.208.0).

A :class:`GoalContractRecord` is not a task: a task runs once and ends; a goal stays
until its contract is verifiably met, its budget refuses further spend, its
breaker trips, or the user stops it. Each *iteration* is one ordinary agent
session (same permission engine, same ledger, same review posture) — the goal
adds bookkeeping around it, never power.

Trust posture, stated plainly:

* **The deny floor is NOT representable here.** ``allowed_grants_json`` holds
  tools pre-granted to every iteration's session, and a goal runs UNATTENDED —
  so :func:`grants_violation` refuses the host-touching floor tools
  (``shell`` / ``repl`` / ``browser_use`` / ``web_action`` / ``mcp_call``,
  :data:`~iron_jarvis.tools.permissions.DENY_FLOOR_TOOLS`) at WRITE time. The
  interactive per-session grant is the sanctioned way to arm one of those for
  one task; a durable record that re-grants it forever, headless, is exactly
  the loophole the floor exists to close. Checked again at spawn time
  (one rule set, two call sites — the capability-store pattern), because a row
  can reach the database from an older build or a hand edit.
* **A budget bound is absent only by explicit choice.** A missing key in
  ``budget_json`` means unlimited BY EXPLICIT ABSENCE — so a FRESH record must
  carry at least one bound, or ``unlimited: true`` set deliberately
  (:func:`budget_violation`). "I forgot to cap it" must not parse the same as
  "I chose not to cap it".
* **A ``checks`` verifier must actually check something.** ``kind:"checks"``
  with zero usable checks would auto-satisfy vacuously on the first completed
  session, so it is refused at write time (validated in ``store.py``, which
  reuses the workflows ``expect:`` coercion — one vocabulary, not two).

Registration: this table is created lazily by ``GoalStore._ensure_table``
(belt-and-braces), and the coordinator must ALSO add ``"..goals.models"`` to
``core.db._LATE_MODEL_MODULES`` so an EXISTING install's reconciler can see it
— the v1.151.2 lesson (a lazily-created table lands on every fresh test DB and
on no real install). This module stays import-light for exactly that reason
(the capability/routes lesson: nothing heavy behind a table registration).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: Lifecycle states. ``tripped`` is the circuit breaker's state (failures, not
#: intent); ``stopped`` is the user's "no more"; ``satisfied`` is the verifier's
#: verdict and only an explicit ``reopen`` resurrects it.
GOAL_STATES: tuple[str, ...] = (
    "active",
    "paused",
    "satisfied",
    "failed",
    "stopped",
    "tripped",
)

#: Valid transitions for :meth:`GoalStore.transition`. Deliberately narrow:
#: ``satisfied`` / ``failed`` / ``stopped`` have NO outgoing edges here, and
#: ``tripped`` can only be stopped — every resurrection (satisfied→active,
#: tripped→active, …) goes through the EXPLICIT :meth:`GoalStore.reopen`, which
#: also clears the breaker so a reopened goal does not instantly re-trip on
#: stale failures. "It quietly went active again" must be impossible.
GOAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"paused", "satisfied", "failed", "stopped", "tripped"}),
    "paused": frozenset({"active", "stopped"}),
    "tripped": frozenset({"stopped"}),
    "satisfied": frozenset(),
    "failed": frozenset(),
    "stopped": frozenset(),
}

#: Circuit breaker (the renderer-watchdog pattern): this many failed iterations
#: inside the window trips the goal instead of letting it burn budget retrying
#: a broken world state forever.
BREAKER_MAX_FAILURES = 3
BREAKER_WINDOW_S = 30 * 60

#: The recognized budget bounds and the ``spent_json`` counter each gates.
BUDGET_BOUNDS: tuple[tuple[str, str], ...] = (
    ("max_tokens", "tokens"),
    ("max_dollars", "dollars"),
    ("max_wallclock_s", "wallclock_s"),
)

#: Verifier kinds — the LADDER (G2, v1.209.0). The doer never grades itself
#: unlabeled:
#:
#: * "checks"      — tier 1: deterministic, ledger-evidenced expectations (the
#:   workflows verified-steps machinery). Shipped in G1, byte-identical since.
#: * "adversarial" — tier 2: optional checks run FIRST (all must pass exactly
#:   as tier 1); then a fresh-context one-shot judge is briefed to REFUTE
#:   satisfaction. Satisfied only when the checks pass AND the judge fails to
#:   refute. No real provider ⇒ the judge cannot run ⇒ the iteration records
#:   "verification pending" and does NOT satisfy (the honest-mock rule — a
#:   scripted mock verdict would be a fabricated one, and silently falling
#:   back to checks-only would quietly demote the tier the user chose).
#: * "judged"      — tier 3: the judge alone, same refute framing, same
#:   honest-mock refusal — and ``goal_view`` labels a satisfaction earned this
#:   way loudly (``verifier.judged_note``), because "a model said so" must
#:   never read like "the ledger proved it".
#: * "manual"      — only the user can declare it satisfied; the engine NEVER
#:   auto-satisfies a manual goal.
VERIFIER_KINDS: tuple[str, ...] = ("checks", "adversarial", "judged", "manual")

_ZERO_SPENT = {"tokens": 0, "dollars": 0.0, "wallclock_s": 0.0, "iterations": 0}


class GoalContractRecord(SQLModel, table=True):
    """One durable GOAL CONTRACT (see the module docstring).

    ``__tablename__`` is EXPLICIT because the natural default would collide:
    the Motivation Layer's ``motivation/models.py`` defines its own
    ``GoalRecord`` (table ``goalrecord``) — a different concept (a dial-based
    standing intent the deliberation loop reads), not this
    contract+budget+verifier record — and two SQLModel classes may not share
    one table. Hence the distinct class name AND the distinct table name. The
    id prefix stays ``goal_`` (ids never collide across tables; the prefix is
    for humans).
    """

    __tablename__ = "goalcontract"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: new_id("goal"), primary_key=True)
    name: str = ""
    #: The goal, stated CHECKABLY — this text is the first line of every
    #: iteration's task, so it must say what "done" looks like.
    contract_text: str = ""
    #: The builtin agent shape iterations run as (unknown values fall back to
    #: builder at spawn, the workflows convention).
    agent_type: str = "builder"
    #: Context spine: iterations run grounded in this project (None = ungrounded).
    project_id: str | None = Field(default=None, index=True)
    #: Cron string for the scheduler's ``kind="goal"`` dispatch, or "" =
    #: manual / continuous (run-now only). NOTHING in this package runs on its
    #: own timer — cadence always comes from outside (see ``engine.py``).
    schedule: str = ""
    #: JSON list of tool names pre-granted to every iteration session
    #: (``Session.allow_tools_json``). Deny-floor tools are refused at write
    #: AND spawn time — see the module docstring.
    allowed_grants_json: str = "[]"
    #: JSON ``{max_tokens?, max_dollars?, max_wallclock_s?, unlimited?}``.
    budget_json: str = "{}"
    #: JSON ``{tokens, dollars, wallclock_s, iterations}`` — accumulated across
    #: every iteration. Single-writer (the GoalEngine) by design.
    spent_json: str = json.dumps(_ZERO_SPENT)
    #: JSON ``{kind: "checks"|"manual", checks?: [expect-dicts]}`` where each
    #: check is a workflows ``expect:`` shape ({files?, summary_contains?}).
    verifier_json: str = json.dumps({"kind": "manual"})
    state: str = Field(default="active", index=True)
    #: JSON ``{failures: [iso...], last_reason?, tripped_at?}`` — failure
    #: timestamps pruned to the breaker window on write.
    breaker_json: str = json.dumps({"failures": []})
    #: JSON carry-forward: ``{last_session_id?, running_session_id?, iteration?,
    #: files?, remaining?, at?}`` — DETERMINISTIC (session id + ledger files +
    #: the session's recorded summary), never model-written in G1.
    checkpoint_json: str = "{}"
    #: TX-01 provenance of the RECORD itself (who created the goal): user_task |
    #: chat | api | "" = unattributed. Iteration sessions carry their own
    #: ``origin="goal:<id>"`` stamp.
    origin: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_run_at: datetime | None = None

    # -- never-raising decoded views ---------------------------------------
    # (the store's loads must not die on a hand-edited or half-written row)

    def decoded_grants(self) -> list[str]:
        vals = _loads(self.allowed_grants_json, [])
        if not isinstance(vals, list):
            return []
        return [str(v).strip() for v in vals if str(v).strip()]

    def decoded_budget(self) -> dict[str, Any]:
        val = _loads(self.budget_json, {})
        return val if isinstance(val, dict) else {}

    def decoded_spent(self) -> dict[str, Any]:
        val = _loads(self.spent_json, {})
        if not isinstance(val, dict):
            val = {}
        out = dict(_ZERO_SPENT)
        for key in out:
            try:
                out[key] = type(out[key])(val.get(key) or 0)
            except (TypeError, ValueError):
                pass
        return out

    def decoded_verifier(self) -> dict[str, Any]:
        val = _loads(self.verifier_json, {})
        if not isinstance(val, dict):
            return {"kind": "manual"}
        kind = str(val.get("kind") or "manual").strip().lower()
        if kind not in VERIFIER_KINDS:
            kind = "manual"
        checks = val.get("checks")
        return {"kind": kind, "checks": checks if isinstance(checks, list) else []}

    def decoded_breaker(self) -> dict[str, Any]:
        val = _loads(self.breaker_json, {})
        if not isinstance(val, dict):
            val = {}
        failures = val.get("failures")
        val["failures"] = [str(f) for f in failures] if isinstance(failures, list) else []
        return val

    def decoded_checkpoint(self) -> dict[str, Any]:
        val = _loads(self.checkpoint_json, {})
        return val if isinstance(val, dict) else {}


def _loads(raw: str, default):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# write-time validators (pure, importable — one rule set for store AND engine)
# ---------------------------------------------------------------------------


def grants_violation(grants: Any) -> str:
    """Why this ``allowed_grants`` value may never be stored, or ``""``.

    Returns the sentence to show, never a bare bool (a refusal without a "why"
    is what makes a caller retry the same thing). The floor set is imported,
    not copied — if the floor grows, this check grows with it.
    """
    from ..tools.permissions import DENY_FLOOR_TOOLS

    if grants is None:
        return ""
    if not isinstance(grants, (list, tuple)):
        return "allowed_grants must be a list of tool names"
    floor_hits = sorted(
        {str(g).strip() for g in grants if str(g).strip() in DENY_FLOOR_TOOLS}
    )
    if floor_hits:
        return (
            f"{', '.join(floor_hits)} cannot be pre-granted to a goal — "
            f"{', '.join(sorted(DENY_FLOOR_TOOLS))} are on the deny floor, and a "
            "goal runs unattended, so a durable grant here would be a standing "
            "headless bypass. Grant one of these per-session, interactively, "
            "or give the goal a narrow tool that does the one thing it needs."
        )
    return ""


def budget_violation(budget: Any) -> str:
    """Why this budget may never be stored on a FRESH record, or ``""``.

    The rule: at least one positive bound, OR ``unlimited: true`` set
    deliberately — and never both, because "unlimited but capped" is a
    contradiction someone will read the convenient half of.
    """
    if budget is None or not isinstance(budget, dict):
        return (
            "a goal needs a budget: set at least one of max_tokens / "
            "max_dollars / max_wallclock_s, or unlimited: true if no bound is "
            "genuinely intended"
        )
    bounds: list[str] = []
    for bound_key, _spent_key in BUDGET_BOUNDS:
        val = budget.get(bound_key)
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            return f"budget {bound_key} must be a positive number, got {val!r}"
        if num <= 0:
            return f"budget {bound_key} must be a positive number, got {val!r}"
        bounds.append(bound_key)
    unlimited = bool(budget.get("unlimited"))
    if unlimited and bounds:
        return (
            f"unlimited: true contradicts the bound(s) {', '.join(bounds)} — "
            "pick one: bounds that gate, or an explicit unlimited"
        )
    if not unlimited and not bounds:
        return (
            "a goal needs a budget: set at least one of max_tokens / "
            "max_dollars / max_wallclock_s, or unlimited: true if no bound is "
            "genuinely intended"
        )
    return ""


def budget_exceeded(budget: dict[str, Any], spent: dict[str, Any]) -> str:
    """The honest reason no further iteration may spawn, or ``""``.

    An ABSENT bound gates nothing (unlimited by explicit absence — write-time
    validation guaranteed the absence was chosen). ``>=`` on purpose: a bound
    is a ceiling, and spend AT the ceiling means the next iteration would
    exceed it. Wallclock is the ACCUMULATED across-iterations figure — the
    gate runs before spawn, so an iteration that starts under budget runs to
    completion even if it crosses mid-flight (stated, not hidden).
    """
    if not isinstance(budget, dict) or budget.get("unlimited"):
        return ""
    for bound_key, spent_key in BUDGET_BOUNDS:
        raw = budget.get(bound_key)
        if raw is None:
            continue
        try:
            bound = float(raw)
        except (TypeError, ValueError):
            continue  # write-time validation refuses these; a hand-edit gates nothing
        used = float(spent.get(spent_key) or 0)
        if used >= bound:
            return (
                f"budget exhausted: {spent_key} spent {used:g} has reached "
                f"{bound_key} {bound:g}"
            )
    return ""
