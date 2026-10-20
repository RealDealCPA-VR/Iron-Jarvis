"""Which step ENDED a workflow run, and which failed steps it went on past
(v1.361.0, lane 11A "one notice per outcome, and none lost").

Two readers need the same answer, so it lives in ONE place:

* the engine stamps ``failed_session`` on ``workflow.completed`` — the run
  (session) of the step that ended a failed run, "" when that step had no
  run of its own (a tool, notify or ask step, or an expectation on one).
  The dashboard's bell and the desktop app's tray watcher let ONLY that
  step's notice stand in for the workflow's: an earlier attempt that failed
  and was retried, or a step the run let go on past, is another outcome, so
  the workflow's own failure is still said (v1.360.0 folded the workflow's
  notice into ANY covered step's and lost it);
* a schedule that fired the workflow says the step and its own words on
  every destination (``platform._dispatch_scheduled``): a step with
  ``on_failure: skip`` that failed makes the fire ``partial`` and names the
  step, and a halted run names the step that stopped it — inside a schedule
  fire the schedule's line is the ONE line, so it must carry them.

Pure and never raises: it runs inside the engine's finalize and inside the
scheduler's fire, where an exception would lose the very record it reads.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

#: (step name, that step's output dict)
StepFailure = tuple[str, dict]


def step_failures(
    step_names: Iterable[Any], outputs: Mapping[str, Any] | None
) -> tuple[StepFailure | None, list[StepFailure]]:
    """``(halted, skipped)`` read off a run's outputs, in step order.

    ``halted`` — the FIRST failed step no ``on_failure`` handled (the one that
    ended the run), or None. ``skipped`` — every failed step the run went on
    past (``on_failure: skip`` marks its output ``handled``)."""
    halted: StepFailure | None = None
    skipped: list[StepFailure] = []
    try:
        outs = outputs if isinstance(outputs, Mapping) else {}
        for raw in step_names or []:
            name = str(raw or "")
            out = outs.get(name)
            if not isinstance(out, dict) or out.get("status") != "failed":
                continue
            if out.get("handled"):
                skipped.append((name, out))
            elif halted is None:
                halted = (name, out)
    except Exception:  # noqa: BLE001 — a reading rule never drops a record
        return None, []
    return halted, skipped


def ended_by_session(step_names: Iterable[Any], outputs: Mapping[str, Any] | None) -> str:
    """The run (session id) of the step that ended a failed run — "" when
    that step had no run of its own, or nothing ended the run."""
    halted, _ = step_failures(step_names, outputs)
    if halted is None:
        return ""
    return str(halted[1].get("session_id") or "")


def _loads(text: Any, kind: type) -> Any:
    try:
        value = json.loads(text or "")
    except (TypeError, ValueError):
        return kind()
    return value if isinstance(value, kind) else kind()


def record_failures(record: Any) -> tuple[StepFailure | None, list[StepFailure]]:
    """:func:`step_failures` for a ``WorkflowRunRecord`` (its ``steps_json``
    gives the order, ``outputs_json`` the outputs). An unreadable record
    reads as nothing failed."""
    try:
        steps = _loads(getattr(record, "steps_json", ""), list)
        names = [
            s.get("name") for s in steps if isinstance(s, dict) and s.get("name")
        ]
        outputs = _loads(getattr(record, "outputs_json", ""), dict)
        return step_failures(names, outputs)
    except Exception:  # noqa: BLE001
        return None, []


def record_sessions(record: Any) -> list[str]:
    """The runs (session ids) a ``WorkflowRunRecord`` started, in order."""
    try:
        ids = _loads(getattr(record, "session_ids_json", ""), list)
        return [str(s) for s in ids if isinstance(s, str) and s]
    except Exception:  # noqa: BLE001
        return []
