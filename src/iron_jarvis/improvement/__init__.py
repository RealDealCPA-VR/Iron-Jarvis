"""ImprovementEngine — measured outcomes feed back into lessons + proposals.

Closes the loop the Evaluation Engine left open: session scores were produced but
consumed by nothing. This package records per-session OUTCOMES, weights lessons by
whether they actually helped, surfaces per-agent quality trends, runs on-demand
model reflection over low scorers, and turns recurring tool failures into
suggest-only proposals.

Importing this package registers its SQLModel tables on the shared metadata, so
``init_db`` creates them. Build one :class:`ImprovementEngine` on the platform.
"""

from __future__ import annotations

from .engine import ImprovementEngine
from .models import AgentStatRecord, LessonStatRecord, OutcomeRecord

__all__ = [
    "ImprovementEngine",
    "OutcomeRecord",
    "LessonStatRecord",
    "AgentStatRecord",
]
