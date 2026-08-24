"""Pi usage (v1.213.0) — work done in the Pi coding agent CLI counts too.

The user runs ``@earendil-works/pi-coding-agent`` in Build terminals; its token
use never crosses this daemon's router, so the Usage page could not see it. Pi
records everything in its own session store (``~/.pi/agent/sessions/``): one
directory per working folder, holding ``<timestamp>_<uuid>.jsonl`` session
files whose ``type == "message"`` records carry ``message.usage`` on assistant
turns — per-MESSAGE aggregates with Pi's own cost figures.

We read that store READ-ONLY at request time and merge live, same as the
OpenCode fold (see ``opencode_usage``): deliberately NOT an import-into-our-
ledger, because open sessions keep growing and copied rows would double-count.
Unlike OpenCode's per-session aggregates, Pi's records are per message with
their own timestamps, so day bucketing is per MESSAGE (UTC, matching
opencode_usage's convention). Everything degrades to ``{"available": False}``
on any problem; the Usage endpoint must never break because Pi is absent or on
a newer schema.

BOUNDED SCAN: the route handlers are sync-def (threadpool) but a session tree
can be big, so files are prefiltered by the days window BEFORE opening
(filename timestamp prefix — Pi names files ``2026-08-23T16-24-59-051Z_<uuid>``
— falling back to mtime) and a hard file cap applies, reported honestly in the
note when it bites.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Session files read per call — far above any real store; bounds a
#: pathological tree. When the cap bites, the note says so.
_MAX_FILES = 2000

_DAY_MS = 86_400_000


def pi_sessions_root(config: Any = None) -> Path:
    """Pi's session store: the ``pi_sessions_dir`` config override (a
    directory), else the standard ``~/.pi/agent/sessions`` location."""
    override = str(getattr(config, "pi_sessions_dir", "") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".pi" / "agent" / "sessions"


def _filename_start_ms(path: Path) -> "int | None":
    """Session START from Pi's filename convention
    (``2026-08-23T16-24-59-051Z_<uuid>.jsonl``), or None if it doesn't parse."""
    stem = path.name.split("_", 1)[0]
    if stem.endswith("Z"):
        stem = stem[:-1]
    try:
        dt = datetime.strptime(stem, "%Y-%m-%dT%H-%M-%S-%f")
    except ValueError:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _record_ms(ts: Any) -> "int | None":
    """ms epoch from a record's ISO timestamp (``2026-08-23T16:27:47.545Z``)."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def pi_usage(
    root: "Path | str", since_days: int = 30, *, now_ms: "int | None" = None
) -> dict[str, Any]:
    """Token usage from Pi's own session store, shaped to merge with
    ``usage_summary``: ``{"available", "note", "totals", "by_model",
    "by_day"}``. Reasoning tokens count as output (they are generated); cache
    reads/writes ride separately in the totals for honesty — both matching the
    OpenCode fold. A "run" is one SESSION with at least one usage-bearing
    message (per model: one session in which that model spoke), matching
    OpenCode's one-run-per-session semantics."""
    out: dict[str, Any] = {
        "available": False,
        "note": "",
        "totals": {"input_tokens": 0, "output_tokens": 0,
                   "cache_tokens": 0, "cost_usd": 0.0, "runs": 0},
        "by_model": [],
        "by_day": [],
    }
    p = Path(root)
    if not p.is_dir():
        out["note"] = "Pi session store not found"
        return out
    try:
        days = max(0, int(since_days))
    except (TypeError, ValueError):
        days = 30
    cutoff_ms = (now_ms if now_ms is not None else int(time.time() * 1000))
    cutoff_ms -= days * _DAY_MS

    # Prefilter FILES by the window before opening any: a file is in scope when
    # its session start (filename) OR its last write (mtime) is inside the
    # window — a session started before the window can still have activity in
    # it. Newest first, hard-capped.
    candidates: list[tuple[int, Path]] = []
    try:
        files = [f for f in p.glob("*/*.jsonl") if f.is_file()]
        files += [f for f in p.glob("*.jsonl") if f.is_file()]
    except OSError:
        out["note"] = "Pi session store unreadable"
        return out
    for f in files:
        start_ms = _filename_start_ms(f)
        try:
            mtime_ms = int(f.stat().st_mtime * 1000)
        except OSError:
            mtime_ms = 0
        recency = max(start_ms or 0, mtime_ms)
        if recency >= cutoff_ms:
            candidates.append((recency, f))
    candidates.sort(key=lambda t: -t[0])
    truncated = len(candidates) > _MAX_FILES
    candidates = candidates[:_MAX_FILES]

    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    totals = out["totals"]
    for recency, f in candidates:
        # Fallback day for records with no parseable timestamp: the file's own
        # window anchor. The file IS in the window, so the tokens still count.
        fallback_day = datetime.fromtimestamp(
            recency / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d")
        keys_this_session: set[tuple[str, str]] = set()
        session_has_usage = False
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except (TypeError, ValueError):
                        continue  # skip unparseable lines, keep reading
                    if not isinstance(rec, dict) or rec.get("type") != "message":
                        continue
                    msg = rec.get("message")
                    if not isinstance(msg, dict):
                        continue
                    u = msg.get("usage")
                    if not isinstance(u, dict):
                        continue  # session/model_change/user turns carry none
                    try:
                        tin = int(u.get("input") or 0)
                        gen = int(u.get("output") or 0) + int(u.get("reasoning") or 0)
                        cache = int(u.get("cacheRead") or 0) + int(u.get("cacheWrite") or 0)
                        cost_rec = u.get("cost")
                        cost = float(
                            (cost_rec or {}).get("total") or 0.0
                        ) if isinstance(cost_rec, dict) else float(cost_rec or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if tin <= 0 and gen <= 0 and cache <= 0 and cost <= 0:
                        continue  # not usage-bearing
                    ts_ms = _record_ms(rec.get("timestamp"))
                    if ts_ms is not None and ts_ms < cutoff_ms:
                        continue  # message itself predates the window
                    day = (
                        datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                        if ts_ms is not None
                        else fallback_day
                    )
                    provider = f"pi/{msg.get('provider') or 'unknown'}"
                    model = str(msg.get("model") or "unknown")
                    key = (provider, model)
                    session_has_usage = True
                    keys_this_session.add(key)
                    rec_m = by_model.setdefault(key, {
                        "provider": provider, "model": model, "input_tokens": 0,
                        "output_tokens": 0, "cost_usd": 0.0, "runs": 0,
                    })
                    rec_m["input_tokens"] += tin
                    rec_m["output_tokens"] += gen
                    rec_m["cost_usd"] += cost
                    drec = by_day.setdefault(day, {
                        "day": day, "input_tokens": 0, "output_tokens": 0,
                        "cost_usd": 0.0,
                    })
                    drec["input_tokens"] += tin
                    drec["output_tokens"] += gen
                    drec["cost_usd"] += cost
                    totals["input_tokens"] += tin
                    totals["output_tokens"] += gen
                    totals["cache_tokens"] += cache
                    totals["cost_usd"] += cost
        except OSError:
            continue  # a file we cannot read must not break the rollup
        if session_has_usage:
            totals["runs"] += 1
            for key in keys_this_session:
                by_model[key]["runs"] += 1

    out["available"] = True
    out["note"] = (
        "Pi's own recorded usage and costs (the user's own Pi accounts);"
        " per-message records bucketed by their UTC day"
    )
    if truncated:
        out["note"] += (
            f"; scan capped at the {_MAX_FILES} most recent session files —"
            " older sessions in the window are not counted"
        )
    out["by_model"] = sorted(
        by_model.values(), key=lambda r: -(r["input_tokens"] + r["output_tokens"])
    )
    out["by_day"] = sorted(by_day.values(), key=lambda r: r["day"])
    return out
