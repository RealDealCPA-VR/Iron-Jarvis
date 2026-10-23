"""v1.374.1: Emergency stop's words survive a stop that crosses loops.

A scheduled run lives on APScheduler's own thread under ``asyncio.run``, so
``Orchestrator.cancel_session`` (reached from ``stop_unattended``) takes its
cross-loop branch: it hands ``task.cancel`` to the run's own loop and stamps
the row itself. It used to hand the cancel over FIRST and stamp SECOND, while
the run's ``_finalize_cancelled`` POPS the stop reason. When the run's loop
won that race (v1.374.0's Tests gate, a loaded runner), the stamp found no
reason and saved "The agent was stopped before it finished." over the
Emergency stop sentence the finalizer had just written.

The race is forced here: the cancelling thread's read of the reason is held
back, so the run's loop always finalizes inside that window on the old order.
"""

from __future__ import annotations

import asyncio
import threading
import time

from iron_jarvis.agents.orchestrator import EMERGENCY_STOP_STOPPED, STOPPED_SUMMARY
from iron_jarvis.core.models import SessionStatus
from tests.test_schedule_outcomes_v13510 import _add_task, _app, _wait


class _SlowGet(dict):
    """The orchestrator's stop reasons, with ``get`` (the cross-loop stamp's
    read) held back. ``pop`` (the finalizer's read) is untouched."""

    def get(self, key, default=None):
        time.sleep(0.5)
        return super().get(key, default)


def _start_scheduled_run(app, client, monkeypatch):
    platform = app.state.platform
    orch = platform.orchestrator
    working = threading.Event()

    async def long_job(session, agent_def, *a, **kw):
        working.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(orch.runtime, "run", long_job)
    _add_task(client, "nightly")
    fire = threading.Thread(target=platform.scheduler._fire, args=("nightly",), daemon=True)
    fire.start()
    assert working.wait(15), "the scheduled run never started"
    return orch, fire


def test_emergency_stops_words_are_not_overwritten_by_the_cross_loop_stamp(tmp_path, monkeypatch):
    app, client = _app(tmp_path)
    with client:
        orch, fire = _start_scheduled_run(app, client, monkeypatch)
        orch._stop_reasons = _SlowGet(orch._stop_reasons)

        r = client.post("/autonomy/kill", json={"enabled": True})
        assert r.status_code == 200 and r.json()["stopped"] == 1, r.text
        fire.join(15)
        assert not fire.is_alive(), "the cancel reached the scheduler thread's loop"

        row = app.state.platform.scheduler.get("nightly")
        stopped = orch.get_session(row.last_session_id)
        assert stopped.status is SessionStatus.CANCELLED
        assert stopped.summary.startswith(EMERGENCY_STOP_STOPPED), stopped.summary
        assert not orch._stop_reasons, "the reason is consumed by the finalizer"


def test_a_plain_cancel_across_loops_still_says_the_run_was_stopped(tmp_path, monkeypatch):
    """Control: with no stop reason the same cross-loop path keeps the plain
    words (the v1.359.0 STOPPED_SUMMARY)."""
    app, client = _app(tmp_path)
    with client:
        orch, fire = _start_scheduled_run(app, client, monkeypatch)
        row = app.state.platform.scheduler.get("nightly")
        assert _wait(lambda: bool(app.state.platform.scheduler.get("nightly").last_session_id)
                     or bool(orch._running))
        sid = row.last_session_id or next(iter(orch._running))
        orch.cancel_session(sid)
        fire.join(15)
        assert not fire.is_alive()
        stopped = orch.get_session(sid)
        assert stopped.status is SessionStatus.CANCELLED
        assert stopped.summary.startswith(STOPPED_SUMMARY), stopped.summary
