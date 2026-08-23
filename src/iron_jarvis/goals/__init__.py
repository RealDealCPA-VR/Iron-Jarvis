"""Goal CONTRACTS — standing intentions with a contract, a hard budget, a
verifier, and a lifecycle that survives restarts (G1, v1.208.0).

"Goal contracts" on purpose: the Motivation Layer (``motivation/``) already
has lightweight ``GoalRecord`` intent rows with an autonomy dial. This module
is the CONTRACTED kind — checkable contract text, hard budget, deterministic
verifier, circuit breaker — and its class/table names
(``GoalContractRecord`` / ``goalcontract``) stay distinct so the two never
collide in one metadata or one conversation.

The package, in three files:

* ``models.py`` — :class:`~iron_jarvis.goals.models.GoalContractRecord` (the durable
  row) plus the pure write-time validators (deny-floor grants, explicit
  budget, budget gate). Registered in ``core.db._LATE_MODEL_MODULES`` by the
  coordinator so the table exists on a REAL install and not only on a fresh
  test DB (the v1.151.2 lesson); ``GoalStore`` also ensures it belt-and-braces.
* ``store.py`` — CRUD, guarded state transitions (resurrection only through
  the explicit ``reopen``), spend accumulation, the circuit breaker's record.
* ``engine.py`` — :class:`~iron_jarvis.goals.engine.GoalEngine`, the ONE
  door that runs an iteration. Nothing in this package runs on its own timer:
  cadence comes from the scheduler (``kind="goal"`` dispatch) or a manual
  "run now" route, and boot calls ``GoalEngine.rehydrate()`` after the
  orchestrator's session reconciliation.

Kept import-light on purpose: ``models`` must load behind a table
registration without dragging the orchestrator or FastAPI in (the
capability-package lesson), so the heavy pieces import lazily inside
``engine``/``store``.
"""

from .models import GoalContractRecord  # noqa: F401 — registers the table on import
from .store import GoalStore, goal_view  # noqa: F401
