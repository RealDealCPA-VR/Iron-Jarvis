"""Notifier — routes messages to one or more communication channels.

Owns a set of named channels and a routing policy. Also adapts the platform
:class:`EventBus` to outbound alerts: :meth:`on_event` formats and sends a
message whenever a *subscribed* event type fires (e.g. ``review.requested``,
``workflow.completed``, ``provider.failed``) and ignores everything else.
"""

from __future__ import annotations

from typing import Any, Callable

from ..core.events import EventType
from .base import Channel

#: event types that, by default, raise an outbound alert.
DEFAULT_ALERT_EVENTS: frozenset[str] = frozenset(
    {
        EventType.REVIEW_REQUESTED,
        EventType.WORKFLOW_COMPLETED,
        EventType.PROVIDER_FAILED,
        EventType.SESSION_COMPLETED,
        EventType.AUTONOMY_EXECUTED,
        EventType.PROVIDER_FAILOVER,
    }
)


def _event_field(event: Any, attr: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(attr, default)
    return getattr(event, attr, default)


def format_event(event: Any) -> str:
    """Build a concise human-readable alert line from an event."""
    etype = _event_field(event, "type", "event")
    payload = _event_field(event, "payload", {}) or {}
    session_id = _event_field(event, "session_id")
    parts = [
        f"{k}={v}"
        for k, v in payload.items()
        if k != "content" and not isinstance(v, (dict, list))
    ]
    detail = f" — {', '.join(parts)}" if parts else ""
    suffix = f" (session {session_id})" if session_id else ""
    return f"Iron Jarvis: {etype}{detail}{suffix}"


class Notifier:
    def __init__(
        self,
        *,
        default_channel: str | None = None,
        event_types: set[str] | None = None,
        formatter: Callable[[Any], str] | None = None,
    ) -> None:
        self._channels: dict[str, Channel] = {}
        self.default_channel = default_channel
        self.event_types: set[str] = (
            set(event_types) if event_types is not None else set(DEFAULT_ALERT_EVENTS)
        )
        self._formatter = formatter or format_event

    # -- channel management ---------------------------------------------
    def add_channel(self, name: str, channel: Channel) -> None:
        self._channels[name] = channel
        if self.default_channel is None:
            self.default_channel = name

    def remove_channel(self, name: str) -> bool:
        """Drop a channel; returns whether it existed. Re-points the default."""
        existed = self._channels.pop(name, None) is not None
        if self.default_channel == name:
            self.default_channel = next(iter(sorted(self._channels)), None)
        return existed

    def get(self, name: str) -> Channel | None:
        return self._channels.get(name)

    def channels(self) -> list[str]:
        return sorted(self._channels)

    # -- routing ---------------------------------------------------------
    def _targets(self, channels: list[str] | None) -> list[str]:
        if channels:
            return list(channels)
        if self.default_channel and self.default_channel in self._channels:
            return [self.default_channel]
        return self.channels()

    def notify(
        self, message: str, channels: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Send ``message`` to ``channels`` (or the default/all) and report results."""
        results: dict[str, dict[str, Any]] = {}
        for name in self._targets(channels):
            channel = self._channels.get(name)
            if channel is None:
                results[name] = {"ok": False, "detail": f"unknown channel '{name}'"}
                continue
            try:
                results[name] = channel.send(message)
            except Exception as exc:  # a channel must never break the fan-out
                results[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return results

    # -- event bus adapter ----------------------------------------------
    def on_event(self, event: Any) -> dict[str, dict[str, Any]] | None:
        """EventBus handler: alert on subscribed event types, ignore the rest.

        Returns the per-channel results when it fired, ``None`` when ignored.
        Safe to register via ``event_bus.add_handler(notifier.on_event)``.
        """
        etype = _event_field(event, "type")
        if etype not in self.event_types:
            return None
        return self.notify(self._formatter(event))
