"""Detections routes (v1.290.0): what the agents did that looks bad.

* ``GET /detections/rules`` — the packaged rule set (id, title, severity,
  description, category).
* ``GET /detections/findings?session_id=&hours=24`` — scan this app's tool
  ledger (one session, or everything in the last *hours*) and return the
  findings, newest first.

Token-guarded by the app's auth middleware like every other route. Ledger
reads and the scan are blocking, so both run through ``asyncio.to_thread``.

Also registers the POST-SESSION SCAN for agent sessions (``SESSION_COMPLETED``);
chat turns are scanned from the chat lanes' own end-of-turn point. Both go
through ``detections.bell`` — a daemon thread, never raising, one bell event
per high/critical (rule, session). ``session_id`` may be ``"chat:<run id>"``
(one chat turn) or ``"chat"`` (every chat row, grouped by turn).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from ...core.events import EventType
from ...detections import load_rules, scan
from ...detections.bell import schedule_scan, wait_for_scans
from ...detections.engine import Finding, parse_ts
from ...detections.ledger import events_for_session, recent_events

log = logging.getLogger("iron_jarvis.detections")

#: Kept for callers/tests that knew the earlier name.
wait_for_post_session_scans = wait_for_scans


def _newest_first(findings: list[Finding]) -> list[Finding]:
    def _key(f: Finding) -> float:
        ts = parse_ts(f.last_ts)
        return ts.timestamp() if ts else 0.0

    # scan() already orders by severity; a stable sort on time keeps that
    # order among findings with the same clock.
    return sorted(findings, key=_key, reverse=True)


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/detections/rules")
    async def detection_rules() -> dict[str, Any]:
        """The packaged detection rules: id, title, severity, description,
        category (plus kind and attribution)."""
        rules = await asyncio.to_thread(load_rules)
        return {"rules": [r.summary() for r in rules], "count": len(rules)}

    @app.get("/detections/findings")
    async def detection_findings(
        session_id: str | None = None,
        hours: float = Query(24.0, gt=0, le=24 * 90),
        limit: int = Query(5000, ge=1, le=20000),
    ) -> dict[str, Any]:
        """Scan the tool ledger and return findings, newest first.

        With ``session_id``: every ledgered call of that session (``hours`` is
        ignored). Without: the newest ``limit`` calls of the last ``hours``."""
        sid = (session_id or "").strip()
        engine = d.platform.engine

        def _run() -> tuple[list[dict], list[Finding]]:
            events = (
                events_for_session(engine, sid)
                if sid
                else recent_events(engine, since_hours=hours, limit=limit)
            )
            return events, _newest_first(scan(events))

        try:
            events, findings = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001 — say why, never a bare 500
            log.exception("detection scan failed")
            raise HTTPException(status_code=500, detail=f"detection scan failed: {exc}")
        return {
            "findings": [f.to_dict() for f in findings],
            "count": len(findings),
            "events_scanned": len(events),
            "session_id": sid or None,
            "hours": None if sid else hours,
        }

    platform = d.platform

    def _on_session_completed(event: Any) -> None:
        """SESSION_COMPLETED -> scan that session in a daemon thread."""
        try:
            if getattr(event, "type", None) != EventType.SESSION_COMPLETED:
                return
            sid = getattr(event, "session_id", None) or (
                (getattr(event, "payload", None) or {}).get("session_id")
            )
            if sid:
                schedule_scan(platform, str(sid))
        except Exception:  # noqa: BLE001 — the bus must survive any handler
            log.exception("detections session handler failed")

    platform.event_bus.add_handler(_on_session_completed)
