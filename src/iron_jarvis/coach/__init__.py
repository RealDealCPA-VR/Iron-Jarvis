"""The reflection coach (v1.297.0): a platform SERVICE that reads a custom
agent's recent runs off the ledger, names what kept going wrong, and — with a
REAL model only — proposes the smallest edit to that agent's instructions.

Three layers, each one honest on its own:

* :mod:`.signals` — per-run facts read ONLY from the ledger (no model):
  outcome, score, failed tools, denials, unanswered asks, steps vs the cap,
  thumbs-down feedback, duration, repeated reads, a later continuation.
* :mod:`.taxonomy` — FIXED categories with a deterministic classifier over
  those signals. The same runs always produce the same clusters.
* :mod:`.engine` — :class:`CoachEngine`: ``report`` (pure), ``propose`` (ONE
  router completion, never under the offline mock — a fabricated instruction
  edit would be read back as the agent's own voice on every later run),
  ``accept`` / ``decline`` / ``list`` / ``get``.

The coach never runs AS the agent and never coaches itself: there is no
"self" here — the target is always another record (``custom:<slug>``), a
builtin is refused, and nothing is written until a person accepts.
"""

from __future__ import annotations

from .engine import CoachEngine, StaleProposal
from .models import CoachProposalRecord

__all__ = ["CoachEngine", "CoachProposalRecord", "StaleProposal"]
