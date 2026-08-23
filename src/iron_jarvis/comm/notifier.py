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
#:
#: ``workflow.waiting`` is deliberately NOT here: the workflow engine already
#: delivers the parked-run question to every destination itself at park time
#: (``WorkflowEngine._deliver``, v1.121.0), and the pending-prompt handler
#: (``comm/prompts.py``, v1.137.0) additionally sends chat-enabled identities
#: the answerable copy — adding it to the default alert set would triple-send
#: the same question. :func:`format_event` still knows the type so a user who
#: EXPLICITLY subscribes it (``comm.event_types`` in config) gets a
#: phone-friendly line instead of the generic key=value dump.
#:
#: ``approval.requested`` IS here (v1.200.0), and the asymmetry with
#: ``workflow.waiting`` is deliberate: nothing else delivers an approval pause
#: — the runtime only publishes the event — so without a default subscription
#: the phone stayed silent for the whole 300s answer window, at exactly the
#: moment the two-way channel exists for (CONNECT-AUDIT-2026-08-22 §3). The
#: pending-prompt handler (``comm/prompts.py::handle_approval_requested``)
#: registers the answerable row but sends NO copy of its own, so this alert is
#: the ONE phone message. ``approval.resolved`` is deliberately NOT here:
#: answering is not news — the user (or the timeout) just acted, and echoing
#: every decision back would turn the alert channel into a log.
#: Goal NEWS (v1.209.0): a standing goal SATISFYING itself, TRIPPING its
#: breaker, or REFUSING to run is exactly the news an unattended-autonomy
#: channel exists for — each one is a decision the user may want to act on
#: (celebrate/stop, investigate, raise the budget). ``goal.iteration_started``
#: and ``goal.iteration_completed`` are deliberately NOT here — they are
#: routine heartbeats (a daily goal would buzz the phone twice a morning
#: forever), the same reasoning that keeps ``approval.resolved`` out below:
#: routine motion is a log, not news. The digest (``goals/digest.py``) is
#: where the routine record lives. Plain strings mirroring goals/engine.py's
#: constants (importing the engine would drag its sqlmodel chain into this
#: light module); tests/test_goal_digest_v1209.py pins them against the
#: engine's constants so they cannot drift.
GOAL_SATISFIED_EVENT = "goal.satisfied"
GOAL_TRIPPED_EVENT = "goal.tripped"
GOAL_ITERATION_REFUSED_EVENT = "goal.iteration_refused"

DEFAULT_ALERT_EVENTS: frozenset[str] = frozenset(
    {
        EventType.REVIEW_REQUESTED,
        EventType.WORKFLOW_COMPLETED,
        EventType.PROVIDER_FAILED,
        EventType.SESSION_COMPLETED,
        EventType.AUTONOMY_EXECUTED,
        EventType.PROVIDER_FAILOVER,
        EventType.SKILL_PROPOSAL_CREATED,
        EventType.APPROVAL_REQUESTED,
        GOAL_SATISFIED_EVENT,
        GOAL_TRIPPED_EVENT,
        GOAL_ITERATION_REFUSED_EVENT,
    }
)


def _goal_label(payload: dict) -> str:
    """The goal's NAME for a phone line, best recorded truth first.

    The engine's satisfied/tripped/refused payloads carry ``name`` since
    v1.209.0 (all five publish sites), so the name is the normal case. The
    fallbacks stay for the records that predate it and for defensive parsing
    of a hand-fed event: a v1.208.0 EventRecord replayed through a formatter
    has no ``name``, and an id names the exact goal, uglily — better than
    inventing one."""
    return (
        str(payload.get("name") or "").strip()
        or str(payload.get("goal_id") or "").strip()
        or "a goal"
    )


def _event_field(event: Any, attr: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(attr, default)
    return getattr(event, attr, default)


def format_event(event: Any) -> str:
    """Build a concise human-readable alert line from an event."""
    etype = _event_field(event, "type", "event")
    payload = _event_field(event, "payload", {}) or {}
    if etype == "workflow.waiting":
        # Phone-friendly park alert (v1.137.0): carry the question and point
        # at the answer paths a pocket actually has.
        wf = str(payload.get("workflow") or payload.get("run_id") or "a workflow")
        q = str(payload.get("question") or "").strip()
        detail = f": {q}" if q else ""
        return (
            f"Workflow '{wf}' needs you{detail} — reply with a number or "
            "/answer <text> from a chat-enabled destination."
        )
    if etype == EventType.APPROVAL_REQUESTED:
        # Phone-friendly pause alert (v1.200.0): name the tool and BOTH answer
        # paths a pocket actually has — the dashboard bell, or replying right
        # here (comm/prompts.py registers the answerable prompt that makes
        # "reply here" true on chat-enabled destinations).
        tool = str(payload.get("tool") or "").strip() or "a tool"
        return (
            f"⏸ An agent is asking to use {tool} — approve from the "
            "dashboard bell, or reply here: approve / deny."
        )
    if etype == GOAL_SATISFIED_EVENT:
        # House voice (v1.209.0): the verifier's checks all held — say so
        # plainly; the digest carries the evidence trail.
        return f"✅ Goal satisfied: {_goal_label(payload)}"
    if etype == GOAL_TRIPPED_EVENT:
        reason = str(payload.get("reason") or "").strip()
        return f"🛑 Goal breaker tripped: {_goal_label(payload)}" + (
            f" — {reason}" if reason else ""
        )
    if etype == GOAL_ITERATION_REFUSED_EVENT:
        # A refusal names its reason (budget exhausted / tripped / paused /
        # already running) — the one line that tells the user WHY the goal
        # went quiet instead of letting silence read as progress.
        reason = str(payload.get("reason") or "").strip()
        return f"⏸ Goal run refused: {_goal_label(payload)}" + (
            f" — {reason}" if reason else ""
        )
    if etype == EventType.SKILL_PROPOSAL_CREATED:
        name = str(payload.get("skill_name") or "").strip() or "a new skill"
        if payload.get("auto"):
            # auto=True: the explicit auto-approve setting already wrote the
            # skill to disk — "review it" would point at an empty review queue.
            return f"New skill added automatically: {name} — see it on the Skills page"
        return f"New skill suggested: {name} — review it on the Skills page"
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
        # v1.118.0: fan out to EVERY channel whose per-registration config
        # allows this event type — not just the default. A channel config may
        # carry ``events: [...]`` to narrow what it receives (the Notifications
        # page's per-destination checkboxes); absent/empty means everything,
        # which is exactly the old behaviour for existing channels. Before this,
        # notify(None-targets) meant auto-alerts only ever reached the DEFAULT
        # channel — a second connected destination silently got nothing.
        targets = [
            name
            for name, ch in sorted(self._channels.items())
            if not (getattr(ch, "config", None) or {}).get("events")
            or etype in (ch.config.get("events") or [])
        ]
        if not targets:
            return {}
        return self.notify(self._formatter(event), targets)
