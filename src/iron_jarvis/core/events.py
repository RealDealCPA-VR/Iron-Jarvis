"""Event Bus (§31).

In-process async pub/sub. The single coordination point for the platform:
the agent runtime, tools, and providers publish; observability/persistence
(sync handlers) and the dashboard WebSocket (async subscribers) consume.

Redis Streams / NATS adapters are a later concern — this interface is what they
would implement.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from .ids import new_id, utcnow


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


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    id: str = field(default_factory=lambda: new_id("evt"))
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
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:  # a bad consumer must not break publishing
                pass
        for queue in list(self._subscribers):
            queue.put_nowait(event)
        return event

    async def subscribe(self) -> AsyncIterator[Event]:
        """Async stream of events; used by the dashboard WebSocket."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
