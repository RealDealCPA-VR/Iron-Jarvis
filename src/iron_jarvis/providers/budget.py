"""Per-run spend ceiling for the subscription CLIs (v1.295.0, the job card).

A custom agent with a monthly dollar allowance should not be able to blow past
it inside ONE run: the ledger is written when a run ENDS, so the allowance door
(``agents.allowance.refusal_for``) can only refuse the NEXT run. The Claude CLI
takes ``--max-budget-usd`` per invocation, which closes that gap for the one
provider that offers it — and this contextvar is how the run tells the adapter
what is left.

``AgentRuntime.run`` sets it for the duration of a ``custom:<slug>`` run whose
record carries ``allowance_usd > 0`` (what is LEFT this month, not the whole
allowance) and resets it in a ``finally``. ``subprocess_cli.ClaudeCliAdapter
._argv`` reads it; the Codex adapter never does (no such flag). A contextvar
rather than an adapter attribute because the adapter is SHARED across every
concurrent run and chat turn — a value set on it would leak into the next
caller's argv.

``0.0`` means "no ceiling" (the default for every chat turn and builtin run).
"""

from __future__ import annotations

from contextvars import ContextVar, Token

RUN_BUDGET_USD: ContextVar[float] = ContextVar("iron_jarvis_run_budget_usd", default=0.0)


def set_run_budget(usd: float) -> Token:
    """Arm a ceiling for the current context; returns the token for
    :func:`reset_run_budget`. A negative or unparseable value arms nothing."""
    try:
        value = float(usd or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return RUN_BUDGET_USD.set(value if value > 0 else 0.0)


def reset_run_budget(token: Token) -> None:
    """Restore the previous value (``finally`` in the runtime)."""
    try:
        RUN_BUDGET_USD.reset(token)
    except (ValueError, LookupError):  # a token from another context — leave it
        RUN_BUDGET_USD.set(0.0)


def run_budget() -> float:
    """The ceiling in USD for the current context; ``0.0`` = none."""
    try:
        return float(RUN_BUDGET_USD.get() or 0.0)
    except (TypeError, ValueError):
        return 0.0
