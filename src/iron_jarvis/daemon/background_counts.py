"""v1.455.0 (readiness Wave F, item 11): background work the orb and the home
line count as running.

Running now (``lib/runningNow.ts``) already lists a workflow run that has no
agent step and a media job (a picture, a clip) being made. The orb read only
session events and ``/health.replies``, and the home line only
``orchestrator._running`` plus coding replies, so beside a Running now list
showing a render the orb still said "Idle — nothing running" and the home line
said nothing: two answers to one question.

This is the ONE count both read, from memory only (``/health`` is polled
every 5 s by every window, so no database read here):

* ``workflows`` — workflow runs whose task is live. A run is launched through
  ``orchestrator.spawn_managed(<run id>, …)``, so its id (``wfrun…``) sits in
  ``orchestrator._running`` while it works; a run parked on the user's answer
  has no live task (its resume is a new spawn).
* ``media`` — media jobs this process holds in flight
  (``platform.media_jobs.live_ids()``).

Never raises: a count it cannot read is zero.
"""

from __future__ import annotations

#: The id prefix every workflow run record carries (``workflows.models``:
#: ``new_id("wfrun")``).
WORKFLOW_RUN_PREFIX = "wfrun"


def background_running(d) -> dict[str, int]:
    """``{"workflows": n, "media": n}`` — in memory, never raises."""
    out = {"workflows": 0, "media": 0}
    try:
        running = getattr(getattr(d, "orchestrator", None), "_running", None) or {}
        out["workflows"] = sum(
            1 for key in list(running) if isinstance(key, str) and key.startswith(WORKFLOW_RUN_PREFIX)
        )
    except Exception:  # noqa: BLE001 — a count we cannot read is zero
        pass
    try:
        jobs = getattr(getattr(d, "platform", None), "media_jobs", None)
        live = jobs.live_ids() if jobs is not None else []
        out["media"] = len(live or [])
    except Exception:  # noqa: BLE001
        pass
    return out
