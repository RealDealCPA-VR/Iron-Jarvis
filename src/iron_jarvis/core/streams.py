"""Ephemeral per-session token/tool stream hub (FX-01).

A DELIBERATELY separate channel from the :class:`~iron_jarvis.core.events.EventBus`.
The event bus persists every publish to SQLite, keeps a bounded history, and fans
out to every WebSocket subscriber with no session filter — all correct for
lifecycle events, but catastrophic at O(output-tokens): a DB row per token, real
events evicted from history, private chat/agent tokens broadcast to everyone.

So token deltas + live tool-call frames flow HERE instead: in-memory only, never
persisted, keyed by ``session_id``, and a no-op when nobody is subscribed (an
active run with no viewer pays a single set lookup per frame). An SSE endpoint
subscribes and forwards the frames to one browser; the run's :class:`RunSink`
publishes them as the perceive->act loop produces text + runs tools.

Frames are SSE-ready dicts ``{"event": <name>, "data": <json-able dict>}`` so the
endpoint can serialize with zero mapping. Producers must NEVER ``await`` while
publishing — :meth:`StreamHub.publish` is sync + non-blocking (drop-oldest on a
slow consumer), so token production is never back-pressured by a lagging client.
"""

from __future__ import annotations

import asyncio
from typing import Any

#: Bound each subscriber queue so a slow/stuck SSE consumer cannot grow memory
#: without limit; on overflow the OLDEST frame is dropped (mirrors EventBus).
_SUBSCRIBER_QUEUE_MAX = 2000


def _enqueue_drop_oldest(queue: "asyncio.Queue[dict[str, Any]]", frame: dict[str, Any]) -> None:
    """Bounded put with drop-oldest on overflow (a straight copy of the EventBus
    policy) — protects the run loop from a lagging stream reader."""
    try:
        queue.put_nowait(frame)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()  # evict oldest
        except asyncio.QueueEmpty:  # pragma: no cover - race
            pass
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:  # pragma: no cover - race
            pass


class StreamHub:
    """Fan-out of ephemeral per-session frames to zero-or-more SSE subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, session_id: str) -> "asyncio.Queue[dict[str, Any]]":
        """Register a subscriber for ``session_id``; the SSE endpoint drains the
        returned queue. Always pair with :meth:`unsubscribe` in a ``finally``."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
        self._subs.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: "asyncio.Queue[dict[str, Any]]") -> None:
        subs = self._subs.get(session_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(session_id, None)

    def has_subscribers(self, session_id: str) -> bool:
        return bool(self._subs.get(session_id))

    def publish(self, session_id: str, frame: dict[str, Any]) -> None:
        """SYNC, non-blocking. Deliver ``frame`` to every subscriber of
        ``session_id`` (no-op when there are none). NEVER await this."""
        for q in list(self._subs.get(session_id, ())):
            _enqueue_drop_oldest(q, frame)

    def sink(self, session_id: str, run_id: str) -> "RunSink":
        return RunSink(self, session_id, run_id)


class RunSink:
    """A per-run handle that formats SSE-ready frames and publishes them onto the
    hub. Cheap to create; all methods are sync + non-blocking. When no client is
    subscribed to the session, every call is a single set-lookup no-op."""

    def __init__(self, hub: StreamHub, session_id: str, run_id: str) -> None:
        self._hub = hub
        self._sid = session_id
        self._run = run_id

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        self._hub.publish(self._sid, {"event": event, "data": data})

    def meta(self, provider: str, model: str) -> None:
        self._emit("meta", {"provider": provider, "model": model})

    def token_delta(self, text: str) -> None:
        if text:
            self._emit("token", {"text": text})

    def tool_started(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        # ``args`` MUST already be redacted by the caller (tool.redact_args).
        self._emit(
            "tool_call",
            {"id": call_id, "name": name, "status": "started", "args": args},
        )

    def tool_finished(
        self, call_id: str, name: str, ok: bool, preview: str = ""
    ) -> None:
        self._emit(
            "tool_call",
            {
                "id": call_id,
                "name": name,
                "status": "finished",
                "ok": bool(ok),
                "output": preview,
            },
        )

    def step_end(self, step: int) -> None:
        self._emit("round", {"round": step})

    def reset(self, reason: str = "") -> None:
        """Tell the client to discard partial text (e.g. a pre-first-token
        failover swapped providers)."""
        self._emit("reset", {"reason": reason})

    def done(self, ok: bool, result: str = "") -> None:
        self._emit("done", {"ok": bool(ok), "reply": result})

    def error(self, detail: str) -> None:
        self._emit("error", {"detail": detail})
