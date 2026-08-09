"""Context-window protection.

``plan_history`` (v1.146.0, chat) and ``plan_agent_transcript`` (v1.152.0,
agent runs) replace blind slicing with a real token budget against the window of
the model that will answer. Both are pure, offline and deterministic — see
``budget`` for the ladder, and ``agent_window`` for why an agent transcript
needs its own planner (tool pairs are indivisible; the task is the oldest
message, not the newest).

``compaction`` (v1.153.0) is the layer above: rather than DROPPING the oldest
content and leaving a near-empty deterministic recap, a model writes a real
structured summary which is then checked against the execution ledger and the
transcript before it may be shown. ``store`` caches one per covered prefix so it
is paid for once.
"""

from .agent_window import (  # noqa: F401
    TranscriptPlan,
    plan_agent_transcript,
)
from .budget import (  # noqa: F401
    DEFAULT_WINDOW,
    HistoryPlan,
    build_recap,
    estimate_tokens,
    output_reserve,
    plan_history,
)
from .compaction import (  # noqa: F401
    AUTO_AT,
    KEEP_RECENT,
    SUGGEST_AT,
    Compaction,
    compact_messages,
    level,
    prefix_key,
    pressure,
)

__all__ = [
    "AUTO_AT",
    "DEFAULT_WINDOW",
    "KEEP_RECENT",
    "SUGGEST_AT",
    "Compaction",
    "HistoryPlan",
    "TranscriptPlan",
    "build_recap",
    "compact_messages",
    "estimate_tokens",
    "level",
    "output_reserve",
    "plan_agent_transcript",
    "plan_history",
    "prefix_key",
    "pressure",
]
