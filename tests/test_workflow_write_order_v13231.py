"""Workflow record writes land in the order the run asked (v1.323.1).

The v1.323.0 Tests gate went red on
``test_resume_after_a_parallel_crash_does_not_redeliver_the_finished_member``
with ``KeyError: 'Work'``: every step had run, ``workflow.completed`` had
fired, and the record still had no ``Work``. Each step's write runs on a
worker thread, and a thread held up by SQLite's busy back-off can commit
after a NEWER write: the crashed run's late ``{Tell}`` landed after the
resume's final write and put the older outputs back. Resume reads that
record, so the AE4 promise (a finished member is never re-run) depends on it.

``WorkflowEngine._persist`` stamps every write on the loop; ``_update_record``
drops ``outputs``/``session_ids`` from a write older than one already
committed for the run (its other fields still apply), under one lock.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import asyncio
import contextlib
import json
import re
from pathlib import Path

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.workflows import engine as engine_mod
from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine
from iron_jarvis.workflows.models import WorkflowRunRecord, reconcile_interrupted_runs

ENGINE_SRC = Path(engine_mod.__file__)


def _parallel_def() -> WorkflowDef:
    return WorkflowDef(
        name="par",
        steps=[
            Step(name="Tell", kind="notify", message="msg-A", group="g"),
            Step(name="Work", kind="agent", task="do it", group="g"),
            Step(name="After", kind="notify", message="msg-C"),
        ],
    )


def _row(platform, run_id) -> WorkflowRunRecord:
    with session_scope(platform.engine) as db:
        return db.get(WorkflowRunRecord, run_id)


def _outputs(platform, run_id) -> dict:
    return json.loads(_row(platform, run_id).outputs_json or "{}")


def _completing(orch):
    async def run(session_id, definition=None):
        s = orch.get_session(session_id)
        s.status = SessionStatus.COMPLETED
        s.summary = "agent done"
        s.finished_at = utcnow()
        orch._save(s)
        return s

    return run


async def _crash_after_tell(platform, orch, engine) -> str:
    """Start the parallel def, park ``Work`` forever, wait until ``Tell`` is
    on the record, then cancel the driver (the process-death proxy)."""
    started = asyncio.Event()

    async def hang(session_id, definition=None):
        started.set()
        await asyncio.Event().wait()

    orch.run_session = hang
    wf = _parallel_def()
    rec = engine.create_record(wf)
    driver = asyncio.create_task(engine.run_record(rec, wf))
    await asyncio.wait_for(started.wait(), 5)
    for _ in range(250):
        if _outputs(platform, rec.id).get("Tell", {}).get("status") == "completed":
            break
        await asyncio.sleep(0.02)
    driver.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await driver
    return rec.id


async def test_a_late_write_from_the_crashed_run_cannot_wipe_the_resumed_record(tmp_path):
    """CI's exact failure, made deterministic: a write the crashed run asked
    for BEFORE the resume commits AFTER it. It must not put ``Work`` back to
    missing, and must not shrink ``session_ids``."""
    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)
    engine = WorkflowEngine(platform, orch)
    run_id = await _crash_after_tell(platform, orch, engine)
    # The crashed run's last write was stamped now — before any resume write.
    late_stamp = next(engine_mod._WRITE_SEQ)
    late_outputs = {"Tell": _outputs(platform, run_id)["Tell"]}

    assert reconcile_interrupted_runs(platform.engine) == 1
    orch.run_session = _completing(orch)
    resumed = await engine.resume_interrupted(_row(platform, run_id))
    assert resumed.status == "completed"
    sessions_before = json.loads(_row(platform, run_id).session_ids_json)

    # ... and only now does the crashed run's thread get the database.
    engine._update_record(
        run_id, _seq=late_stamp, outputs=late_outputs, session_ids=[]
    )

    outs = _outputs(platform, run_id)
    assert outs["Work"]["status"] == "completed", outs
    assert outs["After"]["status"] == "completed", outs
    assert json.loads(_row(platform, run_id).session_ids_json) == sessions_before


async def test_an_older_write_keeps_its_other_fields(tmp_path):
    """Only the two growing snapshots are dropped from a stale write — a
    status or a note it carries still lands (nothing newer wrote them)."""
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform, Orchestrator(platform))
    rec = engine.create_record(_parallel_def())
    older = next(engine_mod._WRITE_SEQ)
    newer = next(engine_mod._WRITE_SEQ)

    engine._update_record(
        rec.id, _seq=newer, outputs={"Tell": {"status": "completed"},
                                     "Work": {"status": "completed"}},
        session_ids=["s1", "s2"],
    )
    engine._update_record(
        rec.id, _seq=older, outputs={"Tell": {"status": "completed"}},
        session_ids=["s1"], status="waiting", notes=["kept"],
    )

    row = _row(platform, rec.id)
    assert set(json.loads(row.outputs_json)) == {"Tell", "Work"}
    assert json.loads(row.session_ids_json) == ["s1", "s2"]
    assert row.status == "waiting"
    assert json.loads(row.notes_json) == ["kept"]


async def test_an_unstamped_write_always_applies(tmp_path):
    """A synchronous caller (the workflow tool's refused launch) has no stamp
    and must never be mistaken for a stale write."""
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform, Orchestrator(platform))
    rec = engine.create_record(_parallel_def())
    engine._update_record(
        rec.id, _seq=next(engine_mod._WRITE_SEQ), outputs={"Tell": {"status": "completed"}}
    )
    engine._update_record(
        rec.id, status="failed", outputs={"__launch__": {"status": "failed"}}
    )
    row = _row(platform, rec.id)
    assert json.loads(row.outputs_json) == {"__launch__": {"status": "failed"}}
    assert row.status == "failed"


async def test_persist_stamps_in_the_order_the_run_asked(tmp_path):
    """The stamp is taken on the loop, so a later ``_persist`` always wins,
    whichever thread reaches the database first."""
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform, Orchestrator(platform))
    rec = engine.create_record(_parallel_def())
    stamps: list[int] = []
    real = engine._update_record

    def spy(run_id, _seq=None, **fields):
        stamps.append(_seq)
        return real(run_id, _seq=_seq, **fields)

    engine._update_record = spy
    await engine._persist(rec.id, outputs={"Tell": {"status": "completed"}})
    await engine._persist(rec.id, outputs={"Tell": {"status": "completed"},
                                           "Work": {"status": "completed"}})
    assert len(stamps) == 2 and None not in stamps and stamps[0] < stamps[1]
    assert set(_outputs(platform, rec.id)) == {"Tell", "Work"}


def test_every_engine_write_goes_through_persist():
    """A bare ``to_thread(self._update_record …)`` would be an unstamped
    write from the async path — the shape this release removed."""
    src = ENGINE_SRC.read_text(encoding="utf-8").replace("\r\n", "\n")
    bare = re.findall(r"to_thread\(\s*self\._update_record,[^)]*", src)
    # The one allowed hop is _persist's own, which passes the stamp.
    assert len(bare) == 1 and "_seq=seq" in bare[0], bare
    assert src.count("self._persist(") >= 13
