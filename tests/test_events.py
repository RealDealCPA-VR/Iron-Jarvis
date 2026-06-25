from __future__ import annotations

import asyncio

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import EventRecord


async def test_handler_history_and_persistence(platform):
    seen: list[str] = []
    platform.event_bus.add_handler(lambda ev: seen.append(ev.type))

    await platform.event_bus.publish(
        EventType.SESSION_CREATED, {"a": 1}, session_id="s1"
    )

    assert EventType.SESSION_CREATED in seen
    assert platform.event_bus.history[-1].session_id == "s1"

    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(EventRecord)))
    assert any(r.type == EventType.SESSION_CREATED for r in rows)


async def test_async_subscribe_stream(platform):
    bus = platform.event_bus
    agen = bus.subscribe()
    task = asyncio.create_task(agen.__anext__())
    await asyncio.sleep(0.02)  # let the subscriber register its queue

    await bus.publish(EventType.AGENT_STARTED, {"x": 1})
    event = await asyncio.wait_for(task, timeout=1.0)

    assert event.type == EventType.AGENT_STARTED
    await agen.aclose()
