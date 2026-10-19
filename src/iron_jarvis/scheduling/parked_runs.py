"""Which scheduled workflow run is parked on the user (v1.358.0, integrate).

THE FAILURE THIS ANSWERS. Since v1.358.0 a scheduled workflow that parks on
an ``ask`` step records ``needs_you`` on its Schedules row ("Needs you",
"Your workflow “x” is waiting for your answer on the Workflows page.") and
the home line counts it. When the person answered and the run finished,
nothing told the row: it kept saying "Needs you" — and the home line "1
scheduled task needs a look" — until the next fire, while the workflow's
own run page said Done. Two surfaces, two verdicts for one run.

So the fire that parks remembers ``run id -> schedule`` here, and the
platform's ``workflow.completed`` handler asks :meth:`ParkedRuns.settle` for
the schedule the finished run belongs to, then restamps that row ONLY if it
still holds the parked verdict (a later fire has its own say). The map is a
small JSON file in the state home so a run answered after a restart still
settles its row; it is keyed by schedule name, so a newer park replaces an
older one and a row is never restamped by a run it no longer shows.

Deliberately NOT ``WorkflowRunRecord.started_by``: that column is who
started the run for Emergency stop (v1.352.0), and every door that starts a
run on its own writes ``""`` there — a rule's run of the same workflow would
settle a schedule's row.

Blocking file I/O: callers on an event loop go through ``asyncio.to_thread``
(the scheduler's fire) or run on the bus's worker thread (the handler).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

FILE_NAME = "schedule_parked_runs.json"


class ParkedRuns:
    """``{schedule name: {"run_id", "ref"}}``, persisted, thread-safe."""

    def __init__(self, home: Path | str | None) -> None:
        self._path = Path(home) / FILE_NAME if home else None
        self._lock = threading.Lock()
        self._memory: dict[str, dict[str, str]] = {}

    def _read(self) -> dict[str, dict[str, str]]:
        if self._path is None:
            return dict(self._memory)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): {"run_id": str(v.get("run_id") or ""), "ref": str(v.get("ref") or "")}
            for k, v in data.items()
            if isinstance(v, dict) and v.get("run_id")
        }

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        if self._path is None:
            self._memory = dict(data)
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            # Best effort: the row simply keeps "Needs you" until the next
            # fire, exactly the pre-v1.358.0 behaviour.
            pass

    def park(self, schedule: str, run_id: str, ref: str) -> None:
        """The fire of ``schedule`` parked workflow run ``run_id``."""
        if not schedule or not run_id:
            return
        with self._lock:
            data = self._read()
            data[schedule] = {"run_id": run_id, "ref": ref}
            self._write(data)

    def clear(self, schedule: str) -> None:
        """A newer fire of ``schedule`` did not park: forget the old run."""
        with self._lock:
            data = self._read()
            if data.pop(schedule, None) is not None:
                self._write(data)

    def settle(self, run_id: str) -> tuple[str, str] | None:
        """The finished run ``run_id``: ``(schedule, ref)`` it was parked for,
        removed from the map, or None when no schedule is waiting on it."""
        if not run_id:
            return None
        with self._lock:
            data = self._read()
            hit = next((name for name, v in data.items() if v.get("run_id") == run_id), None)
            if hit is None:
                return None
            ref = data.pop(hit).get("ref") or ""
            self._write(data)
            return hit, ref
