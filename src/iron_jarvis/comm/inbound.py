"""Two-way comm: the durable inbound poller (remote command surface).

The notifier's channels only PUSH out. :class:`InboundPoller` adds the receive
leg: it long-polls every channel whose inbound is *explicitly* enabled, and for
each AUTHORIZED message spawns a normal supervised session via the orchestrator,
awaits it, and replies the summary back over the same channel.

SECURITY (this drives the machine from a phone, so it is hardened by design):

* OFF BY DEFAULT / OPT-IN — :meth:`enabled` is True only when at least one
  channel has ``inbound_enabled = true`` *and* its credentials resolve. With no
  channels configured (the default + the test suite) the daemon never creates
  the loop: zero polling, zero network.
* SENDER ALLOWLIST, FAIL-CLOSED — a message is processed only when
  ``channel.is_authorized(sender_id)`` (an empty/missing allowlist authorizes
  nobody). An unauthorized sender NEVER spawns a session.
* NORMAL GATES — sessions run through the same orchestrator + permission engine
  as a local user, so a remote sender gets no extra power (dangerous tools still
  fail-closed under the headless ask-resolver).
* LOOP PROTECTION — the bot's own / other bots' messages are ignored.
* DURABLE OFFSET — the last-seen offset is persisted per channel so a restart
  resumes without reprocessing.

Mirrors the daemon's auto-backup / autonomy loops: the loop body sleeps, never
blocks boot, and is cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import Engine

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import AgentType
from .base import Channel, InboundMessage
from .models import InboundOffsetRecord

log = get_logger("comm.inbound")


class InboundPoller:
    """Polls inbound-enabled channels and runs supervised sessions for replies."""

    def __init__(
        self,
        notifier: Any,
        orchestrator: Any,
        engine: Engine,
        *,
        event_bus: Any = None,
        poll_timeout: int = 0,
        agent_type: AgentType = AgentType.SUPERVISOR,
        reply_prefix: str = "Iron Jarvis: ",
        max_reply_chars: int = 3500,
        command_interpreter: Any = None,
        reflex_router: Any = None,
    ) -> None:
        self.notifier = notifier
        self.orchestrator = orchestrator
        self.engine = engine
        self.event_bus = event_bus
        self.poll_timeout = poll_timeout
        self.agent_type = agent_type
        self.reply_prefix = reply_prefix
        self.max_reply_chars = max_reply_chars
        #: The Reflex command grammar (``/status``, ``/run`` …). When set, an
        #: authorized message that starts with ``/`` is handled as a fast,
        #: deterministic command instead of spawning a full agent session.
        self.command_interpreter = command_interpreter
        #: The Reflex router. When set, an authorized NON-command message that
        #: matches a ``comm`` reflex rule (keyword) fires that rule instead of a
        #: free-form session — so "any message mentioning X → run workflow Y".
        self.reflex_router = reflex_router

    # -- discovery ---------------------------------------------------------
    def inbound_channels(self) -> list[tuple[str, Channel]]:
        """``(name, channel)`` for every channel that is opted-in AND credentialed.

        Uses the notifier's public API only. A channel toggled on but missing
        its token is skipped (so it is not polled with no credentials).
        """
        out: list[tuple[str, Channel]] = []
        for name in self.notifier.channels():
            ch = self.notifier.get(name)
            if ch is None or not ch.inbound_enabled():
                continue
            if not ch.has_credentials():
                continue
            out.append((name, ch))
        return out

    def enabled(self) -> bool:
        """True iff any channel is configured for inbound (guards loop creation)."""
        return bool(self.inbound_channels())

    # -- durable offset ----------------------------------------------------
    def _get_offset(self, channel: str) -> int:
        with session_scope(self.engine) as db:
            rec = db.get(InboundOffsetRecord, channel)
            return rec.offset if rec is not None else 0

    def _set_offset(self, channel: str, offset: int) -> None:
        with session_scope(self.engine) as db:
            rec = db.get(InboundOffsetRecord, channel)
            if rec is None:
                rec = InboundOffsetRecord(channel=channel, offset=offset)
            else:
                rec.offset = offset
            rec.updated_at = utcnow()
            db.merge(rec)
            db.commit()

    # -- one polling pass --------------------------------------------------
    async def poll_once(self) -> list[dict[str, Any]]:
        """Poll every inbound channel once and handle each message.

        Returns a per-message result list (for tests/observability). Never
        raises: a single bad channel/message is logged and skipped.
        """
        results: list[dict[str, Any]] = []
        for name, ch in self.inbound_channels():
            offset = self._get_offset(name)
            try:
                # The poll is blocking HTTP — run it off the event loop so a
                # long-poll never stalls the daemon. ``to_thread`` of a synchronous
                # (test) transport is still deterministic.
                messages, next_offset = await asyncio.to_thread(
                    ch.poll, offset, timeout=self.poll_timeout
                )
            except Exception:  # noqa: BLE001 — never let one channel kill the pass
                log.exception("inbound poll failed for channel %r", name)
                continue
            for msg in messages:
                # AT-MOST-ONCE on a remote COMMAND surface: persist the offset
                # BEFORE running, so a crash mid-handling drops the in-flight
                # message rather than re-running a remote-triggered action on
                # restart (duplicate side effects are worse than a dropped reply).
                if isinstance(msg.update_id, int):
                    offset = max(offset, msg.update_id + 1)
                    self._set_offset(name, offset)
                try:
                    res = await self._handle(name, ch, msg)
                except Exception:  # noqa: BLE001 — keep processing the batch
                    log.exception("inbound handling failed on channel %r", name)
                    res = {"channel": name, "status": "error"}
                results.append(res)
            # Some channels report a high-water offset even with no text messages
            # (e.g. only non-text updates); persist it so we don't refetch them.
            if next_offset > offset:
                self._set_offset(name, next_offset)
        return results

    async def _handle(
        self, name: str, ch: Channel, msg: InboundMessage
    ) -> dict[str, Any]:
        """Authorize, then (if allowed) run a supervised session + reply."""
        # Loop protection: never act on a bot's message (incl. our own echoes).
        if msg.is_bot:
            return {"channel": name, "status": "ignored_bot"}

        # FAIL-CLOSED allowlist. An unauthorized sender spawns NOTHING.
        if not ch.is_authorized(msg.sender_id):
            log.warning(
                "inbound: rejected unauthorized sender %r on channel %r",
                msg.sender_id,
                name,
            )
            await self._publish(
                EventType.COMM_REJECTED,
                {"channel": name, "sender": msg.sender_id},
            )
            return {"channel": name, "status": "unauthorized", "sender": msg.sender_id}

        # PRIVATE-CHAT ONLY: in a group the originating chat.id != the sender's id,
        # and replying there would broadcast the session output to non-allowlisted
        # members. Refuse anything that isn't the sender's own 1:1 chat.
        if msg.reply_to is not None and str(msg.reply_to) != str(msg.sender_id):
            log.warning("inbound: refusing non-private chat on channel %r", name)
            return {"channel": name, "status": "non_private", "sender": msg.sender_id}

        text = (msg.text or "").strip()
        if not text:
            return {"channel": name, "status": "empty"}

        # COMMAND GRAMMAR: an authorized "/command" is a fast, deterministic
        # operation (status / run a workflow / cancel / ask a remote agent),
        # replied immediately — no agent session spun up. Non-command text falls
        # through to the normal session path below.
        if self.command_interpreter is not None and text.startswith("/"):
            reply = await self.command_interpreter.interpret(text)
            if reply is not None:
                body = f"{self.reply_prefix}{reply}"[: self.max_reply_chars]
                send_res = await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)
                await self._publish(
                    EventType.COMM_RECEIVED,
                    {"channel": name, "sender": msg.sender_id, "command": text},
                )
                return {
                    "channel": name,
                    "status": "command",
                    "command": text.split()[0],
                    "sent": bool(send_res.get("ok")),
                }

        # REFLEX: a non-command message that matches a keyword rule fires that
        # rule (run a workflow / remote agent / session) instead of a free-form
        # chat — the ambient-operator path for "mention X → do Y". The channel's
        # `reflex_source` scopes matching (email channel → "email" rules, Slack →
        # "slack", generic chat → "comm"), so CX-05's per-source rules just work.
        if self.reflex_router is not None:
            source = getattr(ch, "reflex_source", "comm")
            try:
                fired = await self.reflex_router.on_signal(
                    source,
                    {
                        "text": text[:2000],
                        "body": text[:2000],
                        "sender": str(msg.sender_id)[:200],
                        "from": str(msg.sender_id)[:200],
                        "slug": "",
                    },
                )
            except Exception:  # noqa: BLE001 — a reflex must never break comm
                fired = []
            fired = [f for f in fired if f.get("ok")]
            if fired:
                summary = "; ".join(
                    f"{f.get('kind', 'action')} {f.get('rule', '')}".strip() for f in fired
                )
                body = f"{self.reply_prefix}Triggered: {summary}"[: self.max_reply_chars]
                send_res = await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)
                return {
                    "channel": name,
                    "status": "reflex",
                    "fired": len(fired),
                    "sent": bool(send_res.get("ok")),
                }

        # Spawn a NORMAL supervised session (same orchestrator + permission
        # engine as a local user) and await its result.
        session = await self.orchestrator.create_session(text, self.agent_type)
        await self._publish(
            EventType.COMM_RECEIVED,
            {"channel": name, "sender": msg.sender_id, "task": text},
            session_id=session.id,
        )
        session = await self.orchestrator.run_session(session.id)

        reply = (session.summary or "(no result)").strip()
        body = f"{self.reply_prefix}{reply}"[: self.max_reply_chars]
        # Safe to reply to the originating chat: we only reach here for the
        # sender's own private chat (the non-private guard above refused groups).
        send_res = await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)
        return {
            "channel": name,
            "status": "handled",
            "session_id": session.id,
            "sent": bool(send_res.get("ok")),
        }

    async def _publish(self, etype: str, payload: dict[str, Any], **kw: Any) -> None:
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(etype, payload, **kw)
        except Exception:  # noqa: BLE001 — the event bus must never block comm
            log.exception("failed to publish %s", etype)
