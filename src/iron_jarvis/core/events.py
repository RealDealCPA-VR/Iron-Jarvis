"""Event Bus (§31).

In-process async pub/sub. The single coordination point for the platform:
the agent runtime, tools, and providers publish; observability/persistence
(sync handlers) and the dashboard WebSocket (async subscribers) consume.

Redis Streams / NATS adapters are a later concern — this interface is what they
would implement.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from .ids import new_uid, utcnow

logger = logging.getLogger("iron_jarvis.events")

#: Bound each subscriber's queue so a slow/stuck consumer cannot grow memory
#: without limit; on overflow the oldest event is dropped (see :meth:`publish`).
_SUBSCRIBER_QUEUE_MAX = 1000


class EventType:
    """Canonical event names (§31)."""

    SESSION_CREATED = "session.created"
    SESSION_COMPLETED = "session.completed"
    AGENT_STARTED = "agent.started"
    AGENT_STATE_CHANGED = "agent.state_changed"
    AGENT_COMPLETED = "agent.completed"
    TOOL_EXECUTED = "tool.executed"
    TOOL_DENIED = "tool.denied"
    ARTIFACT_GENERATED = "artifact.generated"
    MEMORY_UPDATED = "memory.updated"
    WORKFLOW_COMPLETED = "workflow.completed"
    REVIEW_REQUESTED = "review.requested"
    PROVIDER_FAILED = "provider.failed"
    PROVIDER_FAILOVER = "provider.failover"
    PROVIDER_DOWNGRADED = "provider.downgraded"
    WEBHOOK_RECEIVED = "webhook.received"
    # Two-way comm (inbound): an authorized message arrived on a channel and a
    # session was spawned for it; or a sender was refused (not on the allowlist).
    COMM_RECEIVED = "comm.received"
    COMM_REJECTED = "comm.rejected"
    # Reflex Loop (the ambient operator): an inbound signal (webhook / comm)
    # matched a rule and fired a bound action (workflow / remote agent / session).
    REFLEX_FIRED = "reflex.fired"
    SCHEDULE_FIRED = "schedule.fired"
    COMPUTERUSE_RUN_FINISHED = "computeruse.run_finished"
    # Motivation Layer (the pulse): a deliberation tick produced a candidate
    # action (proposed), or auto-executed one within governance (executed).
    AUTONOMY_PROPOSED = "autonomy.proposed"
    AUTONOMY_EXECUTED = "autonomy.executed"


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    id: str = field(default_factory=lambda: new_uid("evt"))
    ts: str = field(default_factory=lambda: utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "session_id": self.session_id,
            "ts": self.ts,
            "payload": self.payload,
        }


class EventBus:
    def __init__(self, history_size: int = 1000) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        # The loop each subscriber queue was created on, so a publish from a
        # FOREIGN thread/loop (e.g. the APScheduler thread running asyncio.run)
        # wakes the queue's owner loop thread-safely instead of corrupting it.
        self._queue_loops: dict[int, asyncio.AbstractEventLoop] = {}
        self._handlers: list[Callable[[Event], None]] = []
        self.history: deque[Event] = deque(maxlen=history_size)

    def add_handler(self, handler: Callable[[Event], None]) -> None:
        """Register a synchronous handler (logging, persistence)."""
        self._handlers.append(handler)

    async def publish(
        self,
        type: str,
        payload: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Event:
        event = Event(type=type, payload=payload or {}, session_id=session_id)
        self.history.append(event)
        # Deliver to subscribers (the dashboard WS) FIRST so a slow/blocking sync
        # handler (e.g. an offline Slack/webhook POST) can't withhold or reorder
        # the live feed — the subscriber enqueue is instant and non-blocking.
        # Deliver via the queue's OWNING loop so a publish from a foreign loop (the
        # scheduler thread) is thread-safe and actually wakes the waiter.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - publish is always awaited
            running = None
        for queue in list(self._subscribers):
            owner = self._queue_loops.get(id(queue))
            if owner is not None and owner is not running and owner.is_running():
                owner.call_soon_threadsafe(self._enqueue, queue, event)
            else:
                self._enqueue(queue, event)
        # THEN the sync handlers (persistence, logging, comm/webhook delivery),
        # which may do BLOCKING work. Run each off the event loop so a slow handler
        # can't freeze the daemon — sequentially so a single publish never fans out
        # concurrent SQLite writers. Awaiting keeps the contract "handlers have run
        # by the time publish returns".
        for handler in self._handlers:
            await self._dispatch(handler, event)
        return event

    @staticmethod
    def _enqueue(queue: "asyncio.Queue[Event]", event: Event) -> None:
        """Bounded put with drop-oldest on overflow."""
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # evict oldest
            except asyncio.QueueEmpty:  # pragma: no cover - race
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - race
                pass

    @staticmethod
    async def _dispatch(handler: Callable[[Event], None], event: Event) -> None:
        """Run one sync handler off the loop; a failure is logged, never raised."""
        try:
            await asyncio.to_thread(handler, event)
        except Exception:  # a bad consumer must not break publishing
            logger.warning(
                "event handler %r failed for %s", handler, event.type, exc_info=True
            )

    async def subscribe(self) -> AsyncIterator[Event]:
        """Async stream of events; used by the dashboard WebSocket."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
        self._subscribers.add(queue)
        try:
            self._queue_loops[id(queue)] = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - subscribe runs on a loop
            pass
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
            self._queue_loops.pop(id(queue), None)
