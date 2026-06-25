"""Evaluation Engine + Observability (SPEC §29, §30).

Importing this package registers the ``Evaluation`` SQLModel table in
``SQLModel.metadata``, so it must be imported before ``init_db`` runs.
"""

from __future__ import annotations

from .evaluation import Evaluator
from .models import Evaluation
from .observability import Observability

__all__ = ["Evaluation", "Evaluator", "Observability"]
