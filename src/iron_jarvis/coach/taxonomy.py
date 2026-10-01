"""The FIXED failure taxonomy and its deterministic classifier (v1.297.0).

The categories are closed on purpose: a coach that invents a new label per
run cannot be compared across weeks, and a proposal that cites a category the
user has seen before is one they can judge. Each rule reads the signal dict
:func:`iron_jarvis.coach.signals.run_signals` produces — nothing else — so
the same runs always give the same clusters.

A cluster needs ≥ 2 evidence tuples (two runs) to count, except
``tool-misuse`` and ``human-correction``, where one run is already a fact
worth saying.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

#: category -> one-line definition (the UI shows these verbatim).
CATEGORIES: dict[str, str] = {
    "verifier-miss": (
        "the run reported itself done (completed / high score) but the user "
        "rated it down, or it completed with failures nobody checked"
    ),
    "avoidable-rework": "the same tool failed two or more times in one run",
    "tool-misuse": (
        "a call was denied, or a tool failed on a missing required argument, "
        "an unknown tool or one that was not armed"
    ),
    "late-escalation": (
        "the run needed the user (needs_you) only after spending 60% or more "
        "of its step budget"
    ),
    "scope-creep": "the run used its whole step budget and still was not finished",
    "instruction-miss": (
        "an ask went unanswered, or the run failed without using a single tool"
    ),
    "stale-context": "the run re-read the same file three or more times",
    "human-correction": "the user had to /continue the run afterwards",
}

#: Categories where ONE run is enough evidence.
SINGLE_EVIDENCE = frozenset({"tool-misuse", "human-correction"})
MIN_EVIDENCE = 2

#: Per-category weight of one evidence tuple (the proposal cites the heaviest
#: clusters first).
WEIGHTS: dict[str, float] = {
    "tool-misuse": 1.5,
    "verifier-miss": 1.3,
    "human-correction": 1.2,
    "avoidable-rework": 1.0,
    "instruction-miss": 1.0,
    "scope-creep": 0.9,
    "late-escalation": 0.8,
    "stale-context": 0.6,
}

HIGH_SCORE = 0.7
LATE_FRACTION = 0.6
STALE_READS = 3
MISUSE_MARKERS = ("missing required", "unknown tool", "not armed")


@dataclass
class Cluster:
    category: str
    count: int = 0
    evidence: list[dict[str, str]] = field(default_factory=list)
    weight: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _failed(sig: dict) -> list[dict]:
    return [f for f in (sig.get("tools_failed") or []) if isinstance(f, dict)]


# -- one rule per category: signal -> quote or None ------------------------


def _verifier_miss(sig: dict) -> str | None:
    outcome = sig.get("outcome")
    if outcome == "completed_with_failures":
        names = ", ".join(f.get("tool", "?") for f in _failed(sig)[:3]) or "a tool"
        return f"completed with failures ({names}) and reported itself done"
    looked_done = outcome == "completed" or _f(sig.get("score")) >= HIGH_SCORE
    if looked_done and _i(sig.get("down_feedback")) > 0:
        score = sig.get("score")
        shown = f"scored {score:.2f}" if isinstance(score, (int, float)) else "read as completed"
        return f"{shown} but the user rated it down"
    return None


def _avoidable_rework(sig: dict) -> str | None:
    for f in _failed(sig):
        if _i(f.get("count")) >= 2:
            err = f.get("first_error") or "no error text"
            return f"{f.get('tool')} failed {f.get('count')} times: {err}"
    return None


def _tool_misuse(sig: dict) -> str | None:
    if _i(sig.get("denials")) > 0:
        return f"{sig.get('denials')} call(s) denied"
    for f in _failed(sig):
        err = str(f.get("first_error") or "")
        low = err.lower()
        if any(m in low for m in MISUSE_MARKERS):
            return f"{f.get('tool')}: {err}"
    return None


def _late_escalation(sig: dict) -> str | None:
    cap = _i(sig.get("max_steps"))
    steps = _i(sig.get("steps"))
    if sig.get("outcome") == "needs_you" and cap > 0 and steps >= LATE_FRACTION * cap:
        return f"asked for you only at step {steps} of {cap}"
    return None


def _finished(sig: dict) -> bool:
    return sig.get("status") == "completed" and sig.get("outcome") == "completed"


def _scope_creep(sig: dict) -> str | None:
    cap = _i(sig.get("max_steps"))
    steps = _i(sig.get("steps"))
    if cap > 0 and steps >= cap and not _finished(sig):
        return f"used all {cap} steps and was not finished ({sig.get('outcome') or sig.get('status') or 'no verdict'})"
    return None


def _instruction_miss(sig: dict) -> str | None:
    asks = _i(sig.get("unanswered_asks"))
    if asks > 0:
        return f"{asks} ask(s) went unanswered"
    if sig.get("status") == "failed" and not (sig.get("tools_used") or []):
        summary = str(sig.get("summary") or "").strip()
        return "failed without using a tool" + (f": {summary[:80]}" if summary else "")
    return None


def _stale_context(sig: dict) -> str | None:
    reads = _i(sig.get("read_repeats"))
    if reads >= STALE_READS:
        return f"re-read the same file {reads} times"
    return None


def _human_correction(sig: dict) -> str | None:
    if sig.get("continued"):
        return "the user continued the run afterwards"
    return None


RULES: dict[str, Callable[[dict], str | None]] = {
    "verifier-miss": _verifier_miss,
    "avoidable-rework": _avoidable_rework,
    "tool-misuse": _tool_misuse,
    "late-escalation": _late_escalation,
    "scope-creep": _scope_creep,
    "instruction-miss": _instruction_miss,
    "stale-context": _stale_context,
    "human-correction": _human_correction,
}
assert set(RULES) == set(CATEGORIES)


def clusters(signals: list[dict]) -> list[Cluster]:
    """Classify ``signals`` (one dict per run) into the fixed categories.

    Deterministic: evidence keeps the input order, clusters sort by weight
    then by category name, and a category below its evidence floor is not
    returned at all."""
    found: dict[str, Cluster] = {}
    for sig in signals or []:
        if not isinstance(sig, dict):
            continue
        sid = str(sig.get("session_id") or "")
        for category, rule in RULES.items():
            try:
                quote = rule(sig)
            except Exception:  # noqa: BLE001 — one odd signal must not hide the rest
                quote = None
            if not quote:
                continue
            cluster = found.setdefault(category, Cluster(category=category))
            cluster.evidence.append({"session_id": sid, "quote": quote})
    out: list[Cluster] = []
    for category, cluster in found.items():
        floor = 1 if category in SINGLE_EVIDENCE else MIN_EVIDENCE
        if len(cluster.evidence) < floor:
            continue
        cluster.count = len(cluster.evidence)
        cluster.weight = round(cluster.count * WEIGHTS.get(category, 1.0), 2)
        out.append(cluster)
    out.sort(key=lambda c: (-c.weight, c.category))
    return out
