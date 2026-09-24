"""The detection engine (v1.290.0): events in, findings out. Pure.

``scan(events, rules)`` groups findings ONE PER (rule, session): a session that
read ``.env`` forty times is one finding with forty in ``count`` and the first
few events as evidence, not forty bell rows.

Correlation rules match an ORDERED chain of steps inside one session: step 2
must come after step 1, and every step must land within ``window`` seconds of
the first. When every event of a session carries a parseable ``ts`` the
session is ordered by it; otherwise the stream order counts, and the window
and order are still judged on the clock for every link whose two ends both
carry one.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .redact import mask_event
from .rules import SEVERITY_RANK, Rule

#: How many evidence events a finding keeps (the count keeps the rest).
MAX_EVIDENCE = 10


@dataclass
class Finding:
    rule_id: str
    title: str
    severity: str
    reason: str
    session_id: str
    source: str
    events: list[dict] = field(default_factory=list)
    category: str = ""
    description: str = ""
    count: int = 1
    first_ts: str | None = None
    last_ts: str | None = None

    def to_dict(self, *, text: bool = True) -> dict[str, Any]:
        """JSON-ready, with every secret-looking value in the evidence masked
        (``redact.mask``). ``text=False`` also drops the tool output."""
        events = [mask_event(e) for e in self.events]
        if not text:
            for e in events:
                e.pop("text", None)
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
            "reason": self.reason,
            "session_id": self.session_id,
            "source": self.source,
            "count": self.count,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "events": events,
        }


def parse_ts(value: Any) -> datetime | None:
    """ISO-8601 -> aware UTC datetime; naive values are read as UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _session_order(events: list[dict]) -> tuple[list[dict], list[datetime | None], bool]:
    """Order one session's events; say whether the clock can be trusted."""
    stamps = [parse_ts(e.get("ts")) for e in events]
    if events and all(s is not None for s in stamps):
        order = sorted(range(len(events)), key=lambda i: stamps[i])  # stable
        return [events[i] for i in order], [stamps[i] for i in order], True
    return list(events), stamps, False


def _correlate(rule: Rule, events: list[dict]) -> list[list[dict]]:
    """Every non-overlapping chain of the rule's steps, in order, in window.

    LINEAR-ish, not quadratic (v1.290.0 review: 5000 unmatched ``.env`` reads
    cost 16 s when each one rescanned the rest of the session). Each step's
    matching indices are computed ONCE; a chain then looks its next step up
    by bisection. A step with no match anywhere ends the rule at once.

    The window and the order are judged on the clock wherever BOTH ends of a
    link carry one; the session is sorted by the clock only when every event
    has one (otherwise stream order stands, and a link with a missing stamp
    is judged by stream order alone)."""
    corr = rule.correlation
    assert corr is not None
    ordered, stamps, timed = _session_order(events)
    steps = corr.steps
    # One pass per step: index -> matched kinds, for the events it matches.
    matched: list[dict[int, frozenset[str]]] = []
    for step in steps:
        hits: dict[int, frozenset[str]] = {}
        for i, ev in enumerate(ordered):
            kinds = step.matcher.matched_kinds(ev)
            if kinds is not None:
                hits[i] = kinds
        if not hits:
            return []  # a step that never matches: no chain is possible
        matched.append(hits)
    positions = [sorted(h) for h in matched]

    def _next(k: int, after: int, start: int) -> int | None:
        cand = positions[k]
        start_ts = stamps[start]
        prev_ts = stamps[after]
        want = matched[0][start]
        for j in range(bisect_right(cand, after), len(cand)):
            c = cand[j]
            ts = stamps[c]
            if start_ts is not None and ts is not None:
                if (ts - start_ts).total_seconds() > corr.window_s:
                    if timed:
                        return None  # sorted by clock: nothing later fits
                    continue
            if not timed and prev_ts is not None and ts is not None and ts < prev_ts:
                continue  # later in the stream, earlier on the clock
            if corr.same_kind and not (matched[k][c] & want):
                continue
            return c
        return None

    chains: list[list[dict]] = []
    floor = 0
    for start in positions[0]:
        if start < floor:
            continue
        chain = [start]
        for k in range(1, len(steps)):
            nxt = _next(k, chain[-1], start)
            if nxt is None:
                chain = []
                break
            chain.append(nxt)
        if chain:
            chains.append([ordered[i] for i in chain])
            floor = chain[-1] + 1  # the next chain starts after this one ends
    return chains


def _finding(rule: Rule, session_id: str, hits: list[dict], count: int) -> Finding:
    stamps = [s for s in (parse_ts(e.get("ts")) for e in hits) if s is not None]
    source = next((str(e.get("source")) for e in hits if e.get("source")), "")
    return Finding(
        rule_id=rule.id,
        title=rule.title,
        severity=rule.severity,
        reason=rule.reason,
        session_id=session_id,
        source=source,
        events=[dict(e) for e in hits[:MAX_EVIDENCE]],
        category=rule.category,
        description=rule.description,
        count=count,
        first_ts=min(stamps).isoformat() if stamps else None,
        last_ts=max(stamps).isoformat() if stamps else None,
    )


def scan(events: Iterable[dict], rules: list[Rule] | None = None) -> list[Finding]:
    """Run *rules* (default: the packaged set) over *events*.

    Events without an ``action`` are ignored. Findings come back most severe
    first, then newest first."""
    if rules is None:
        from .rules import load_rules

        rules = load_rules()
    by_session: dict[str, list[dict]] = {}
    for ev in events:
        if not isinstance(ev, dict) or not ev.get("action"):
            continue
        by_session.setdefault(str(ev.get("session_id") or ""), []).append(ev)

    findings: list[Finding] = []
    for rule in rules:
        for session_id, session_events in by_session.items():
            if rule.correlation is not None:
                chains = _correlate(rule, session_events)
                if chains:
                    evidence = [e for chain in chains for e in chain]
                    findings.append(_finding(rule, session_id, evidence, len(chains)))
            elif rule.matcher is not None:
                hits = [e for e in session_events if rule.matcher.matches(e)]
                if hits:
                    findings.append(_finding(rule, session_id, hits, len(hits)))

    def _key(f: Finding) -> tuple:
        last = parse_ts(f.last_ts)
        return (-SEVERITY_RANK.get(f.severity, 0), -(last.timestamp() if last else 0.0))

    findings.sort(key=_key)
    return findings
