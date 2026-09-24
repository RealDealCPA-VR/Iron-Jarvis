"""The post-session / post-turn scan that puts findings on the bell (v1.290.0).

Two doors call :func:`schedule_scan`:

* ``routes/detections.py`` — a ``SESSION_COMPLETED`` bus handler, for agent
  sessions (only the orchestrator publishes that event);
* ``daemon/chat_turn._persist_chat_usage`` — the ONE point both chat lanes
  pass at the end of every turn that reached a model; the scope is that turn,
  ``"chat:<AgentRun id>"`` (see :mod:`.ledger`).

The scan runs in a daemon THREAD: the bus awaits its sync handlers and a chat
turn must never wait on a detection, so the caller only starts the thread.
Nothing here raises.

BOUNDED: at most ``MAX_RUNNING`` scans work at once (a semaphore inside
:func:`scan_and_publish`) and at most ``MAX_WAITING`` more wait for a slot;
a scan scheduled beyond that is DROPPED and logged (``dropped_scans()``), so a
burst of a hundred finishing sessions cannot pile up a hundred threads.

Each high/critical finding is published ONCE as ``detection.finding`` whose
evidence carries only action / tool / ref / path / ts / source — never a
command, url or tool output (see ``BELL_EVIDENCE_KEYS``). A (rule_id,
session_id) pair is remembered only AFTER its publish succeeded (a failed
publish is retried by the next scan of that scope), and the check and the
publish sit under one lock so two scans of one scope cannot both publish.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from ..core.events import EventType
from .engine import Finding, scan
from .ledger import CHAT_TURN_PREFIX, events_for_session

log = logging.getLogger("iron_jarvis.detections")

#: Severities that reach the bell.
BELL_SEVERITIES = frozenset({"high", "critical"})
#: Longest path an evidence event carries on the bell.
BELL_FIELD_CAP = 500
#: Scans working at once, and scans allowed to wait for a slot.
MAX_RUNNING = 2
MAX_WAITING = 32

_SLOTS = threading.BoundedSemaphore(MAX_RUNNING)
_IN_FLIGHT = 0
_DROPPED = 0
_FLIGHT_LOCK = threading.Lock()

_PUBLISHED: set[tuple[str, str]] = set()
_PUBLISH_LOCK = threading.Lock()
_WORKERS: list[threading.Thread] = []
_WORKERS_LOCK = threading.Lock()


#: The ONLY evidence keys the bell's event carries. A command, url or tool
#: output can hold a credential (the very thing some rules catch being sent)
#: and this payload is persisted to EventRecord, pushed over the WebSocket and
#: logged — so none of them ride it. The card fetches the (masked) evidence
#: from GET /detections/findings.
BELL_EVIDENCE_KEYS = ("action", "tool", "ref", "path", "ts", "source")


def bell_payload(finding: Finding) -> dict[str, Any]:
    """The finding as the bell carries it: evidence reduced to where and what
    kind, the path masked and cut short — never a command, url or output."""
    payload = finding.to_dict(text=False)
    events = []
    for ev in payload["events"]:
        slim = {k: ev[k] for k in BELL_EVIDENCE_KEYS if k in ev}
        path = slim.get("path")
        if isinstance(path, str) and len(path) > BELL_FIELD_CAP:
            slim["path"] = path[:BELL_FIELD_CAP] + "…"
        events.append(slim)
    payload["events"] = events
    return payload


def scan_and_publish(bus: Any, engine: Any, scope: str) -> list[dict]:
    """Scan one finished session / chat turn and publish its high/critical
    findings. Returns the payloads published (possibly none). Never raises.
    Waits for one of ``MAX_RUNNING`` slots first."""
    published: list[dict] = []
    with _SLOTS:
        try:
            findings = scan(events_for_session(engine, scope))
        except Exception:  # noqa: BLE001 — a scan must never surface
            log.exception("post-session detection scan failed for %s", scope)
            return published
    for finding in findings:
        if finding.severity not in BELL_SEVERITIES:
            continue
        key = (finding.rule_id, scope)
        payload = bell_payload(finding)
        with _PUBLISH_LOCK:
            if key in _PUBLISHED:
                continue
            try:
                asyncio.run(
                    bus.publish(EventType.DETECTION_FINDING, payload, session_id=scope)
                )
            except Exception:  # noqa: BLE001 — publishing is best-effort
                log.exception("could not publish detection %s for %s", key[0], scope)
                continue
            _PUBLISHED.add(key)
        published.append(payload)
    return published


def _run(bus: Any, engine: Any, scope: str) -> None:
    global _IN_FLIGHT
    try:
        scan_and_publish(bus, engine, scope)
    finally:
        with _FLIGHT_LOCK:
            _IN_FLIGHT -= 1


def dropped_scans() -> int:
    """How many scans were refused because the queue was full (process)."""
    return _DROPPED


def schedule_scan(platform: Any, scope: str) -> bool:
    """Start the scan of *scope* in a daemon thread and return at once.
    False when nothing was started (no scope, queue full, or an error)."""
    global _IN_FLIGHT, _DROPPED
    try:
        if not scope:
            return False
        bus, engine = platform.event_bus, platform.engine
        with _FLIGHT_LOCK:
            if _IN_FLIGHT >= MAX_RUNNING + MAX_WAITING:
                _DROPPED += 1
                log.warning(
                    "detection scan of %s dropped: %d scans already queued",
                    scope, _IN_FLIGHT,
                )
                return False
            _IN_FLIGHT += 1
        worker = threading.Thread(
            target=_run,
            args=(bus, engine, str(scope)),
            name=f"detections-{scope}",
            daemon=True,
        )
        with _WORKERS_LOCK:
            _WORKERS[:] = [t for t in _WORKERS if t.is_alive()]
            _WORKERS.append(worker)
        try:
            worker.start()
        except Exception:
            with _FLIGHT_LOCK:
                _IN_FLIGHT -= 1
            raise
        return True
    except Exception:  # noqa: BLE001 — never break the caller
        log.exception("could not schedule a detection scan for %s", scope)
        return False


def schedule_chat_turn_scan(platform: Any, run_id: str) -> None:
    """The chat lanes' door: scan the turn whose ledger run row is *run_id*."""
    if run_id:
        schedule_scan(platform, CHAT_TURN_PREFIX + str(run_id))


def wait_for_scans(timeout: float = 10.0) -> bool:
    """Join every scan started so far. True when all finished."""
    with _WORKERS_LOCK:
        workers = list(_WORKERS)
    for t in workers:
        if t.ident is not None:
            t.join(timeout)
    with _WORKERS_LOCK:
        _WORKERS[:] = [t for t in _WORKERS if t.is_alive()]
        return not _WORKERS
