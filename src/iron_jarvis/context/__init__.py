"""Context-window protection (v1.146.0).

``plan_history`` replaces the blind ``messages[-30:]`` slice with a real token
budget against the window of the model that will answer the turn. Pure and
offline — see ``budget`` for the ladder and why the recap is deterministic.
"""

from .budget import (  # noqa: F401
    DEFAULT_WINDOW,
    HistoryPlan,
    build_recap,
    estimate_tokens,
    output_reserve,
    plan_history,
)

__all__ = [
    "DEFAULT_WINDOW",
    "HistoryPlan",
    "build_recap",
    "estimate_tokens",
    "output_reserve",
    "plan_history",
]
