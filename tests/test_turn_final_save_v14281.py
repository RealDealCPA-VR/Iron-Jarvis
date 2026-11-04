"""v1.428.1: a chat turn that has just ENDED still has its record to save.

The driver tells the page the turn is over (``_end_followers``) and only then
saves the turn's record. The lifespan's ``TurnLogs.shutdown`` used to wait only
for turns still RUNNING, so a stop in between left the record "running" and the
next boot's reconcile read the finished reply as cut off (the v1.428.0 release
gate: a refused turn replayed "stopped" words with no title). The save is held
on a gate here, so the order is the runner's on every machine.
"""

from __future__ import annotations

import asyncio
import json

from iron_jarvis.core.db import open_db, session_scope
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.daemon import durable_turns as dt


async def test_shutdown_waits_for_a_just_ended_turns_save(tmp_path, monkeypatch):
    engine = open_db(tmp_path / "ij.db")
    logs = dt.TurnLogs(engine)
    gate = asyncio.Event()
    real = dt.TurnLog.persist_now

    async def held(self, eng):
        if self.status != "running":
            await gate.wait()
        return await real(self, eng)

    monkeypatch.setattr(dt.TurnLog, "persist_now", held)

    async def frames():
        yield dt.sse("error", {"detail": "declined", "title": "Claude Code declined to answer"})

    lg = logs.start("turn-x", "th-x", frames())
    for _ in range(200):
        if lg.status != "running":
            break
        await asyncio.sleep(0.01)
    assert lg.status == "error"

    stop = asyncio.ensure_future(logs.shutdown())
    await asyncio.sleep(0.1)
    assert not stop.done(), "shutdown returned before the ended turn saved its record"
    gate.set()
    await asyncio.wait_for(stop, 5)

    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, "turn-x")
        assert row is not None and row.status == "error"
        frames_on_disk = json.loads(row.frames_json)
    errors = [json.loads(d) for ev, d in frames_on_disk if ev == "error"]
    assert errors == [{"detail": "declined", "title": "Claude Code declined to answer"}]
    # The next boot finds nothing to call cut off.
    assert dt.reconcile_interrupted_turns(engine) == 0
