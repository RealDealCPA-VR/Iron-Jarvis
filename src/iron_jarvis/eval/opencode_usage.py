"""OpenCode usage (v1.94.0) — local-model work done IN OpenCode counts too.

The user's heaviest local-model usage often happens in OpenCode itself (its
own agent loop against the fleet), which never crosses this daemon's router —
so the Usage page under-reported local work by millions of tokens. OpenCode
records everything in its own SQLite store (``~/.local/share/opencode/
opencode.db``): the ``session`` table carries authoritative token aggregates
per session (per-message rows often settle to zero — the session row is the
truth).

We read that DB READ-ONLY at request time and merge live. Deliberately NOT an
import-into-our-ledger: session aggregates GROW while a session continues, so
copied rows would double-count or need fragile reconciliation — a live read is
always current and can't drift. Window filtering attributes a whole session to
its last-activity time (the finest grain the aggregates offer — stated, not
hidden). Everything degrades to ``{"available": False}`` on any problem; the
Usage endpoint must never break because OpenCode is absent, locked, or has a
newer schema.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Sessions read per call — far above any real store; bounds a pathological DB.
_MAX_SESSIONS = 5000


def opencode_db_path(config: Any = None) -> Path:
    """OpenCode's SQLite store: the ``opencode_data_dir`` config override
    (a directory or the .db file itself), else the standard location."""
    override = str(getattr(config, "opencode_data_dir", "") or "").strip()
    if override:
        p = Path(override)
        return p if p.suffix == ".db" else p / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def opencode_usage(
    db_path: "Path | str", since_days: int = 30, *, now_ms: "int | None" = None
) -> dict[str, Any]:
    """Token usage from OpenCode's own session store, shaped to merge with
    ``usage_summary``: ``{"available", "note", "totals", "by_model",
    "by_day"}``. Reasoning tokens count as output (they are generated);
    cache reads/writes ride separately in the totals for honesty."""
    out: dict[str, Any] = {
        "available": False,
        "note": "",
        "totals": {"input_tokens": 0, "output_tokens": 0,
                   "cache_tokens": 0, "cost_usd": 0.0, "runs": 0},
        "by_model": [],
        "by_day": [],
    }
    p = Path(db_path)
    if not p.is_file():
        out["note"] = "OpenCode store not found"
        return out
    try:
        days = max(0, int(since_days))
    except (TypeError, ValueError):
        days = 30
    cutoff_ms = (now_ms if now_ms is not None else int(time.time() * 1000))
    cutoff_ms -= days * 86_400_000
    try:
        db = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = db.execute(
                "SELECT model, tokens_input, tokens_output, tokens_reasoning,"
                " tokens_cache_read, tokens_cache_write, cost, time_updated"
                " FROM session WHERE time_updated >= ? "
                " ORDER BY time_updated DESC LIMIT ?",
                (cutoff_ms, _MAX_SESSIONS),
            ).fetchall()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — absent/locked/newer schema → honest no-op
        out["note"] = f"OpenCode store unreadable ({type(exc).__name__})"
        return out

    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    for model_json, tin, tout, treason, tcr, tcw, cost, tms in rows:
        try:
            m = json.loads(model_json) if model_json else {}
        except (TypeError, ValueError):
            m = {}
        provider = f"opencode/{m.get('providerID') or 'unknown'}"
        model = str(m.get("id") or "unknown")
        tin = int(tin or 0)
        gen = int(tout or 0) + int(treason or 0)
        cache = int(tcr or 0) + int(tcw or 0)
        cost = float(cost or 0.0)
        key = (provider, model)
        rec = by_model.setdefault(key, {
            "provider": provider, "model": model, "input_tokens": 0,
            "output_tokens": 0, "cost_usd": 0.0, "runs": 0,
        })
        rec["input_tokens"] += tin
        rec["output_tokens"] += gen
        rec["cost_usd"] += cost
        rec["runs"] += 1
        day = datetime.fromtimestamp(int(tms or 0) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        drec = by_day.setdefault(day, {
            "day": day, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
        })
        drec["input_tokens"] += tin
        drec["output_tokens"] += gen
        drec["cost_usd"] += cost
        out["totals"]["input_tokens"] += tin
        out["totals"]["output_tokens"] += gen
        out["totals"]["cache_tokens"] += cache
        out["totals"]["cost_usd"] += cost
        out["totals"]["runs"] += 1
    out["available"] = True
    out["note"] = (
        "sessions attributed to their last-activity day (OpenCode stores"
        " per-session aggregates)"
    )
    out["by_model"] = sorted(
        by_model.values(), key=lambda r: -(r["input_tokens"] + r["output_tokens"])
    )
    out["by_day"] = sorted(by_day.values(), key=lambda r: r["day"])
    return out
