"""Fleet — the user's own inference hardware, observed honestly.

Re-exports the data shapes and the Prometheus parser only. ``probes``,
``registry`` and ``sampler`` are deliberately NOT imported here: they import
from ``fleet.models`` themselves, and pulling them in at package-import time
would create a cycle (and drag ``httpx`` into every consumer that only wanted a
type). Import those modules directly.
"""

from .models import (  # noqa: F401
    FleetNode,
    ModelEntry,
    NodeKind,
    NodeMetrics,
    NodeRates,
    NodeSnapshot,
    NodeStatus,
)
from .prometheus import (  # noqa: F401
    Sample,
    first,
    index,
    label_map,
    parse_text,
    sum_by,
)

__all__ = [
    "FleetNode",
    "ModelEntry",
    "NodeKind",
    "NodeMetrics",
    "NodeRates",
    "NodeSnapshot",
    "NodeStatus",
    "Sample",
    "first",
    "index",
    "label_map",
    "parse_text",
    "sum_by",
]
