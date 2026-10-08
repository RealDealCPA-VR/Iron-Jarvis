"""v1.317.0: a remote teammate's two events in ONE clock tick make ONE card.

The full suite went red on ``test_a_remote_handoff_is_announced_and_shows_on_the_mission``
under load (passing alone): ``delegate`` writes the remote's
``delegation.started`` and ``delegation.completed`` within one 15.6 ms Windows
tick, and ``mission_view`` read events ``ORDER BY created_at DESC`` with no
tie-break — read completion-first, the completion opened an orphan card and the
start a second one ("2 == 1"). The v1.286.0 same-tick rule applies: ties break
on the SQLite rowid (insertion order). This drives the tie DETERMINISTICALLY by
writing both rows with the same timestamp.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from iron_jarvis.agents.mission import mission_view
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentType
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import EventRecord
from iron_jarvis.platform import build_platform

TICK = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)


def _write(engine, rows: list[tuple[str, str, dict]]) -> None:
    with session_scope(engine) as db:
        for rid, etype, data in rows:
            db.add(
                EventRecord(
                    id=rid, type=etype, session_id=data.pop("_sid"), payload_json=json.dumps(data), created_at=TICK
                )
            )
        db.commit()


def _remote_cards(tmp_path, ids: tuple[str, str]):
    p = build_platform(str(tmp_path / "home"))
    root = asyncio.run(
        Orchestrator(p).create_session("objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission")
    )
    start_id, done_id = ids
    _write(
        p.engine,
        [
            (start_id, EventType.DELEGATION_STARTED, {"_sid": root.id, "agent": "remote:hermes", "task": "summarise"}),
            (done_id, EventType.DELEGATION_COMPLETED, {"_sid": root.id, "agent": "remote:hermes", "ok": True, "result": "ok"}),
        ],
    )
    view = mission_view(p, root.id)
    return [m for m in view["members"] if m["kind"] == "remote"]


def test_same_tick_start_and_finish_are_one_finished_card(tmp_path):
    # Ids chosen so that NO accidental order (id, type) puts the start first:
    # only insertion order does.
    cards = _remote_cards(tmp_path, ("zz-start", "aa-done"))
    assert len(cards) == 1, cards
    assert cards[0]["status"] == "done"
    assert cards[0]["task"] == "summarise"


def test_anti_vacuity_the_other_id_order_also_gives_one_card(tmp_path):
    cards = _remote_cards(tmp_path, ("aa-start", "zz-done"))
    assert len(cards) == 1, cards
    assert cards[0]["status"] == "done"
