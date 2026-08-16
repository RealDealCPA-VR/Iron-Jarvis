"""Capability proposals (v1.178.0, P4) — the agent asks, the user approves.

THE MEASURED FAILURE THIS CLOSES. "Rename all files in this folder" ran four
times and renamed nothing, because the app had no rename verb — and the agent
never said so. It shelled out and wrote PyMuPDF scripts to re-read PDFs it had
already read successfully (25 ``shell`` calls, ledger ``run_ab82dea4bf8a``).
Five capabilities in a row then shipped without reaching the agent that needed
them. When the app lacks a verb, the agent must be able to SAY SO and ask.

The package, in four files:

* ``models.py`` — :class:`~iron_jarvis.capability.models.
  CapabilityProposalRecord`, the durable request. Registered in
  ``core.db._LATE_MODEL_MODULES`` so the table exists on a REAL install and not
  only on a fresh test DB (the v1.151.2 lesson).
* ``store.py``  — file / list / approve / reject, plus
  :func:`~iron_jarvis.capability.store.floor_violation`, the ONE rule set that
  keeps an approval from becoming the loophole the deny floor exists to close.
* ``tools.py``  — ``capability_propose``, the agent-facing tool. Permission
  ``allow``: filing a request is free and changes nothing.
* ``routes.py`` — the three HTTP reads/decisions, reached through
  :func:`register` (imported lazily, so a table registration at boot does not
  drag FastAPI in behind it).

THE INVARIANT, in one line: filing creates nothing, approving creates ONE thing
through ``tool_create``, and neither can grant a permission.
"""

from __future__ import annotations

from .models import (
    APPROVED,
    KIND_LABELS,
    KINDS,
    PENDING,
    REJECTED,
    STATUSES,
    CapabilityProposalRecord,
    normalize_name,
    signature_for,
)
from .store import (
    APPLY_SESSION_ID,
    ApplyResult,
    CapabilityProposalStore,
    floor_violation,
    parameter_violation,
    proposal_view,
)
from .tools import CapabilityProposeTool, capability_proposal_tools

#: The tool name, in ONE place: the permission default in ``core/config.py``,
#: the registration in ``platform.py`` and every test read the same string.
CAPABILITY_TOOL_NAME = CapabilityProposeTool.name


def register(app, d) -> None:
    """Mount the capability-request routes (see ``capability/routes.py``).

    Wired from ``daemon/app.py`` beside the other ``register`` calls, exactly
    the way the ``worklist`` package is::

        from ..capability import register as _register_capability
        _register_capability(app, d)

    The import is LAZY so this package's ``__init__`` — which boot runs for the
    table registration — stays free of FastAPI.
    """
    from .routes import register as _register

    _register(app, d)


__all__ = [
    "APPLY_SESSION_ID",
    "APPROVED",
    "CAPABILITY_TOOL_NAME",
    "KINDS",
    "KIND_LABELS",
    "PENDING",
    "REJECTED",
    "STATUSES",
    "ApplyResult",
    "CapabilityProposalRecord",
    "CapabilityProposalStore",
    "CapabilityProposeTool",
    "capability_proposal_tools",
    "floor_violation",
    "normalize_name",
    "parameter_violation",
    "proposal_view",
    "register",
    "signature_for",
]
