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
import time
from collections import deque
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import Engine

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import AgentType
from .base import Channel, InboundMessage, split_message
from .models import InboundOffsetRecord
from .prompts import (
    ALREADY_ANSWERED_REPLY,
    ANSWER_USAGE_REPLY,
    NOTHING_WAITING_REPLY,
    answer_echo,
    pending_reminder,
    prompt_options,
)

log = get_logger("comm.inbound")

#: Wire copy for the messaging surfaces (v1.136.0) — pinned here so the poller,
#: the desktop fan-out route, and the tests all speak the same words.
NEW_THREAD_REPLY = "Fresh start — next message begins a new conversation."
ESCALATE_ACK = "On it — this needs real work. I'll send the result here."
RATE_LIMIT_REPLY = "Getting a lot of messages — pausing for a minute."

#: Per-identity flood guard: more than this many handled chat turns inside the
#: rolling window gets an honest "pausing" reply instead of a model call (a
#: forwarded-message flood must not become a token bill — see the design doc).
RATE_MAX_TURNS = 8
RATE_WINDOW_SECONDS = 60.0

#: Escalation recap: how many thread-tail messages ride into the session task,
#: and the per-message char cap inside the recap block.
_RECAP_MESSAGES = 6
_RECAP_CHARS = 500


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
        thread_store: Any = None,
        chat_turn: Callable[..., Any] | None = None,
        personas: dict[str, Any] | None = None,
        platform: Any = None,
        clock: Callable[[], float] | None = None,
        prompt_store: Any = None,
        answer_run: Callable[..., Any] | None = None,
    ) -> None:
        self.notifier = notifier
        self.orchestrator = orchestrator
        self.engine = engine
        self.event_bus = event_bus
        self.poll_timeout = poll_timeout
        self.agent_type = agent_type
        self.reply_prefix = reply_prefix
        self.max_reply_chars = max_reply_chars
        #: FULL CHAT (v1.136.0) — all four are optional so every existing
        #: construction keeps its one-shot behavior byte-for-byte. When a
        #: channel has ``chat_enabled`` AND ``thread_store`` is wired, free-form
        #: messages become real chat turns on a durable daemon-owned thread:
        #: ``chat_turn`` is the injected turn service (production passes
        #: ``daemon.chat_turn.run_chat_turn``; tests pass a fake async
        #: callable), ``personas`` the builtin-persona defaults dict, and
        #: ``platform`` the Platform the turn runs against.
        self.thread_store = thread_store
        self.chat_turn = chat_turn
        self.personas = personas if personas is not None else {}
        self.platform = platform
        #: Injectable monotonic clock for the per-identity rate cap
        #: (deterministic tests); production uses ``time.monotonic``.
        self._clock: Callable[[], float] = clock or time.monotonic
        self._turn_times: dict[tuple[str, str], deque[float]] = {}
        #: PENDING PROMPTS (v1.137.0) — both optional so every existing
        #: construction keeps its behavior byte-for-byte. ``prompt_store`` is
        #: the :class:`~.prompts.PendingPromptStore`; ``answer_run`` the
        #: injected atomic-claim answer path ``(run_id, answer) -> awaitable
        #: {"ok": bool, ...}`` (production passes a partial of
        #: ``prompts.answer_parked_run`` — the HTTP route's exact semantics;
        #: tests pass a fake). See ``comm/prompts.py`` for the resolution rule.
        self.prompt_store = prompt_store
        self.answer_run = answer_run
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

    # -- full-chat plumbing (v1.136.0) -------------------------------------
    def _chat_ready(self, ch: Channel) -> bool:
        """Full chat only when the channel opted in AND the store + turn
        service are wired (a chat-enabled channel on a poller without the
        v1.136.0 plumbing falls back to the legacy one-shot — fail-open to
        the OLD behavior, never to a crash)."""
        try:
            return (
                bool(ch.chat_enabled())
                and self.thread_store is not None
                and self.chat_turn is not None
            )
        except Exception:  # noqa: BLE001 — a config quirk must never break _handle
            return False

    def rate_ok(self, channel: str, sender_id: Any) -> bool:
        """Per-identity flood guard, shared by the poller AND the desktop
        fan-out route (both count against the SAME identity budget).

        Records the turn and returns True when under the cap; returns False
        (recording nothing) once more than :data:`RATE_MAX_TURNS` handled
        turns landed inside the rolling :data:`RATE_WINDOW_SECONDS`.
        """
        now = self._clock()
        key = (channel, str(sender_id))
        dq = self._turn_times.setdefault(key, deque())
        while dq and now - dq[0] >= RATE_WINDOW_SECONDS:
            dq.popleft()
        if len(dq) >= RATE_MAX_TURNS:
            return False
        dq.append(now)
        return True

    async def send_chunked(self, ch: Channel, reply: str, *, chat_id: Any) -> bool:
        """Send ``reply`` (prefixed) split on the channel's ``chunk_limit`` —
        the full-chat replacement for the one-shot ``[:max_reply_chars]``
        truncation (a long answer must ARRIVE, not get cut). True iff every
        chunk reported ok."""
        limit = int(getattr(ch, "chunk_limit", 3500) or 3500)
        ok = True
        for chunk in split_message(f"{self.reply_prefix}{reply}", limit):
            res = await asyncio.to_thread(ch.send, chunk, chat_id=chat_id)
            ok = ok and bool(res.get("ok"))
        return ok

    def _safe_append(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        channel: str | None = None,
        sender_id: Any = None,
        display: str = "",
    ) -> str:
        """Append that never raises into the per-message pipeline.

        A ``ValueError`` means the thread vanished mid-flight (dashboard
        delete racing the turn): re-resolve ONCE (which heals — mints a fresh
        thread and re-binds the identity) and retry, then give up honestly.
        Returns the thread id the message actually landed on, or ``""`` when
        it could not land (callers keep going — a lost transcript line must
        not lose the phone its reply).
        """
        if self.thread_store is None:
            return ""
        try:
            self.thread_store.append(thread_id, role, content)
            return thread_id
        except ValueError:
            if channel is None:
                return ""
            try:
                fresh = self.thread_store.resolve(channel, str(sender_id), display)
                self.thread_store.append(fresh.id, role, content)
                return fresh.id
            except Exception:  # noqa: BLE001
                log.warning(
                    "comm thread append could not land after re-resolve (%s)",
                    thread_id,
                    exc_info=True,
                )
                return ""
        except Exception:  # noqa: BLE001 — store trouble must not drop the reply
            log.warning("comm thread append failed (%s)", thread_id, exc_info=True)
            return ""

    def _append_exchange(
        self,
        name: str,
        msg: InboundMessage,
        display: str,
        user_text: str,
        assistant_text: str,
    ) -> str:
        """resolve → append user → append assistant, never raising. Returns
        the thread id the exchange landed on ("" when nothing landed)."""
        if self.thread_store is None:
            return ""
        try:
            thread = self.thread_store.resolve(name, str(msg.sender_id), display)
        except Exception:  # noqa: BLE001 — store trouble must not break the reply
            log.warning("comm thread resolve failed on %r", name, exc_info=True)
            return ""
        tid = self._safe_append(
            thread.id, "user", user_text,
            channel=name, sender_id=msg.sender_id, display=display,
        )
        if tid:
            self._safe_append(
                tid, "assistant", assistant_text,
                channel=name, sender_id=msg.sender_id, display=display,
            )
        return tid

    @staticmethod
    def _display(msg: InboundMessage) -> str:
        """Best-effort human display name from the raw update (Telegram shape);
        empty string when unknown — the store then labels by sender id."""
        try:
            m = (msg.raw or {}).get("message") or (msg.raw or {}).get("edited_message") or {}
            frm = m.get("from") or {}
            name = " ".join(
                str(x) for x in (frm.get("first_name"), frm.get("last_name")) if x
            )
            return (name or str(frm.get("username") or "")).strip()[:120]
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def recap_task(history: list[dict[str, str]], text: str) -> str:
        """The escalated-session task: a thread-tail recap block + the request.

        ``history`` is ChatBody-shaped ``[{role, content}]`` INCLUDING the
        current request as its last entry (exactly what the turn just ran on);
        the request is lifted out into ``Request:`` and the up-to-6 messages
        before it become the context block, each capped at 500 chars.
        """
        tail = [m for m in history[:-1] if str(m.get("content") or "").strip()]
        tail = tail[-_RECAP_MESSAGES:]
        lines = [
            f"{'user' if m.get('role') == 'user' else 'assistant'}: "
            f"{str(m.get('content') or '').strip()[:_RECAP_CHARS]}"
            for m in tail
        ]
        if not lines:
            return text
        return (
            "Context from our recent conversation:\n"
            + "\n".join(lines)
            + f"\n\nRequest: {text}"
        )

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

        # FULL CHAT (v1.136.0): when this destination opted into chat
        # (``chat_enabled`` — implies inbound) AND the thread store + turn
        # service are wired, the conversation lives on a durable daemon-owned
        # thread: real chat turns with memory/skills/project spine, visible
        # live on the desktop. With chat OFF every path below stays
        # byte-equivalent to the one-shot behavior (pinned by tests).
        chat_on = self._chat_ready(ch)
        display = self._display(msg) if chat_on else ""

        # "/new" — the chat-only thread reset, handled BEFORE the command
        # grammar (which does not know it). Append the exchange to the OLD
        # thread FIRST so the desktop sees the handoff, then retire the
        # binding so the next message mints a fresh thread.
        if chat_on and text.lower() == "/new":
            tid = self._append_exchange(name, msg, display, text, NEW_THREAD_REPLY)
            try:
                self.thread_store.retire(name, str(msg.sender_id))
            except Exception:  # noqa: BLE001 — never lose the reply over a retire
                log.warning("comm thread retire failed on %r", name, exc_info=True)
            sent = await self.send_chunked(ch, NEW_THREAD_REPLY, chat_id=msg.reply_to)
            return {
                "channel": name,
                "status": "new_thread",
                "thread_id": tid,
                "sent": sent,
            }

        # PENDING PROMPTS (v1.137.0): "/answer" is identity-bound, so it is
        # handled HERE — where (channel, sender) is known — BEFORE the command
        # grammar, which would otherwise call it an unknown command. Works on
        # any inbound channel; prompts only ever exist for identities that
        # earned one (chat-enabled channel + established thread + allowlist).
        low = text.lower()
        if self.prompt_store is not None and (
            low == "/answer" or low.startswith("/answer ")
        ):
            return await self._handle_answer_command(name, ch, msg, text, display, chat_on)

        # COMMAND GRAMMAR: an authorized "/command" is a fast, deterministic
        # operation (status / run a workflow / cancel / ask a remote agent),
        # replied immediately — no agent session spun up. Non-command text falls
        # through to the normal session path below.
        if self.command_interpreter is not None and text.startswith("/"):
            reply = await self.command_interpreter.interpret(text)
            if reply is not None:
                if chat_on:
                    # The desktop sees the command exchange too — cheap, honest.
                    self._append_exchange(name, msg, display, text, reply)
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

        # PENDING PROMPTS: a bare PURE-INTEGER message while a fresh prompt is
        # open is the one-tap answer path. With options it must be an in-range
        # numbered pick (out-of-range falls through to chat — the reminder
        # re-points); WITHOUT options (every workflow ask today) the integer
        # itself is the answer ("How many clients?" → "3") — this keeps the
        # park alert's "reply with a number or /answer" promise true. It sits
        # after the command grammar (commands always work) and BEFORE reflex,
        # so a keyword rule can never steal "1" from an open gate. isdecimal
        # (not isdigit) — "²" passes isdigit but crashes int().
        if self.prompt_store is not None and text.isdecimal():
            prompt, gone_status = self._fresh_open_prompt_ex(name, msg.sender_id)
            if prompt is not None:
                options = prompt_options(prompt)
                pick = int(text)
                if options and 1 <= pick <= len(options):
                    return await self._resolve_prompt(
                        name, ch, msg, display, chat_on, prompt, options[pick - 1], text
                    )
                if not options:
                    return await self._resolve_prompt(
                        name, ch, msg, display, chat_on, prompt, text, text
                    )
            elif gone_status:
                # The newest prompt just expired ON THIS LOOK (the run un-parked
                # elsewhere): this integer was aimed at the dead gate — reply
                # honestly instead of misfiring a chat turn on "1".
                reply = (
                    ALREADY_ANSWERED_REPLY
                    if gone_status in ("resuming", "running", "completed")
                    else NOTHING_WAITING_REPLY
                )
                return await self._answer_reply(
                    name, ch, msg, display, chat_on, text, reply, "answer_expired"
                )

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
                if chat_on:
                    # The reflex-fired exchange lands on the thread too.
                    self._append_exchange(name, msg, display, text, f"Triggered: {summary}")
                body = f"{self.reply_prefix}Triggered: {summary}"[: self.max_reply_chars]
                send_res = await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)
                return {
                    "channel": name,
                    "status": "reflex",
                    "fired": len(fired),
                    "sent": bool(send_res.get("ok")),
                }

        # FREE-FORM, full chat: a real conversational turn on the durable
        # thread (memory + skills + project spine via the injected turn
        # service), replying chunked to the channel's own size cap.
        if chat_on:
            return await self._handle_chat(name, ch, msg, text, display)

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

    async def _handle_chat(
        self, name: str, ch: Channel, msg: InboundMessage, text: str, display: str
    ) -> dict[str, Any]:
        """One FULL-CHAT turn for an authorized free-form message.

        resolve → rate cap → append user → history → chat_turn → append reply
        → chunked send. ``HTTPException`` from the turn service (404 unknown
        skill / 400 / 502 provider) becomes an HONEST reply, never a crash of
        the poll loop. ``escalate: true`` sends an ack, runs the normal
        supervised session with a thread-tail recap, and delivers the summary
        both to the phone and onto the thread (the desktop hears it via
        chat.thread_updated).
        """
        # ALWAYS re-resolve per message — it heals a dashboard-deleted thread.
        try:
            thread = self.thread_store.resolve(name, str(msg.sender_id), display)
        except Exception:  # noqa: BLE001 — store trouble gets an honest reply
            log.exception("comm thread resolve failed on %r", name)
            sent = await self.send_chunked(
                ch,
                "I hit a problem: could not open our conversation thread.",
                chat_id=msg.reply_to,
            )
            return {"channel": name, "status": "chat_error", "sent": sent}

        # Per-identity flood guard — an honest pause instead of a token bill.
        if not self.rate_ok(name, msg.sender_id):
            sent = await self.send_chunked(ch, RATE_LIMIT_REPLY, chat_id=msg.reply_to)
            return {
                "channel": name,
                "status": "rate_limited",
                "sender": str(msg.sender_id),
                "sent": sent,
            }

        tid = self._safe_append(
            thread.id, "user", text,
            channel=name, sender_id=msg.sender_id, display=display,
        )
        history = self.thread_store.history_body(tid, limit=30) if tid else []
        if not history:
            # The append could not land (or the read hiccuped): the turn still
            # runs on the bare message — the phone gets its answer regardless.
            history = [{"role": "user", "content": text}]

        await self._publish(
            EventType.COMM_RECEIVED,
            {"channel": name, "sender": str(msg.sender_id), "task": text},
        )

        # Lazy import: comm must stay importable without pulling the daemon
        # package at module-load time (schemas is pydantic-only, but the
        # dependency direction stays visible + deferred here).
        from ..daemon.schemas import ChatBody

        # PROJECT REACH (v1.141.0): a comm thread the user tagged into a
        # project from the dashboard carries that project into every phone
        # turn — the same context spine desktop chat gets. "" = untagged
        # (unchanged behavior). Best-effort: a weird thread row costs the
        # tag, never the turn.
        project_id = str(getattr(thread, "project_id", "") or "")
        body = ChatBody(messages=history, auto_tools=True, project_id=project_id)
        try:
            result = await self.chat_turn(self.platform, self.personas, body)
        except HTTPException as exc:
            reply = f"I hit a problem: {exc.detail}"
            if tid:
                tid = self._safe_append(
                    tid, "assistant", reply,
                    channel=name, sender_id=msg.sender_id, display=display,
                )
            sent = await self.send_chunked(ch, reply, chat_id=msg.reply_to)
            return {"channel": name, "status": "chat_error", "thread_id": tid, "sent": sent}
        except Exception as exc:  # noqa: BLE001 — the loop must reply, not die
            log.exception("chat turn failed on %r", name)
            reply = f"I hit a problem: {type(exc).__name__}: {exc}"
            if tid:
                tid = self._safe_append(
                    tid, "assistant", reply,
                    channel=name, sender_id=msg.sender_id, display=display,
                )
            sent = await self.send_chunked(ch, reply, chat_id=msg.reply_to)
            return {"channel": name, "status": "chat_error", "thread_id": tid, "sent": sent}

        if not result.get("escalate"):
            reply = str(result.get("reply") or "").strip() or "(no reply)"
            if tid:
                tid = self._safe_append(
                    tid, "assistant", reply,
                    channel=name, sender_id=msg.sender_id, display=display,
                ) or tid
            # PENDING PROMPTS: a free-form message never resolves an open gate
            # (see comm/prompts.py) — instead the OUTBOUND copy carries a
            # gentle reminder. The thread keeps the clean reply only.
            outbound = reply + self._pending_reminder(name, msg.sender_id)
            sent = await self.send_chunked(ch, outbound, chat_id=msg.reply_to)
            return {"channel": name, "status": "chat", "thread_id": tid, "sent": sent}

        # ESCALATE: ack now, run the normal supervised session (same
        # orchestrator + permission engine — a remote sender gains no power),
        # then deliver the summary here AND onto the thread.
        # v1.139.0 informed delegation: a turn that NAMED who should take it
        # (``escalate_agent``) overrides the hard-coded supervisor default —
        # re-validated through the roster here; None keeps the default
        # byte-for-byte (see ``_escalate_plan``).
        task = self.recap_task(history, text)
        agent_type, dyn_def, esc_provider, esc_model = self._escalate_plan(result)
        if tid:
            tid = self._safe_append(
                tid, "assistant", ESCALATE_ACK,
                channel=name, sender_id=msg.sender_id, display=display,
            ) or tid
        await self.send_chunked(ch, ESCALATE_ACK, chat_id=msg.reply_to)
        _spawn_kwargs: dict[str, Any] = {}
        if esc_provider:
            _spawn_kwargs["provider"] = esc_provider
        if esc_model:
            _spawn_kwargs["model"] = esc_model
        # The escalated session inherits the thread's project tag (the same
        # kwarg the dashboard passes when escalating desktop chat), so the
        # run gets the project's brief/knowledge/recent-activity spine.
        session = await self.orchestrator.create_session(
            task, agent_type, project_id=project_id or None, **_spawn_kwargs
        )
        await self._publish(
            EventType.COMM_RECEIVED,
            {"channel": name, "sender": str(msg.sender_id), "task": text},
            session_id=session.id,
        )
        if dyn_def is not None:
            session = await self._run_dynamic_session(session, dyn_def)
        else:
            session = await self.orchestrator.run_session(session.id)
        summary = (session.summary or "(no result)").strip()
        if tid:
            tid = self._safe_append(
                tid, "assistant", summary,
                channel=name, sender_id=msg.sender_id, display=display,
            ) or tid
        sent = await self.send_chunked(ch, summary, chat_id=msg.reply_to)
        return {
            "channel": name,
            "status": "chat_escalated",
            "thread_id": tid,
            "session_id": session.id,
            "sent": sent,
        }

    # -- informed escalation (v1.139.0) ------------------------------------
    def _escalate_plan(self, result: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
        """WHO takes the escalated session, as ``(agent_type,
        dynamic_definition | None, provider | None, model | None)``.

        The default — ``(self.agent_type, None, None, None)`` — is the
        long-standing hard-coded supervisor, returned byte-for-byte whenever
        the turn named nobody, the name does not RE-validate through the
        roster right now (the value crossed a dict boundary, and health can
        change between the turn and the spawn), or the target cannot run as a
        comm session:

        * builtin → that specialist type runs the session directly;
        * dynamic ("custom:<slug>") → its stored definition runs through the
          agent runtime (the same path POST /agents/{name}/spawn uses),
          honoring the record's pinned provider/model;
        * remote ("remote:<name>") → stays on the supervisor default — a
          remote ask returns bare text, not the supervised session the comm
          reply contract is built on, and the supervisor reaches remotes
          itself via delegation.

        Never raises.
        """
        default = (self.agent_type, None, None, None)
        name = str((result or {}).get("escalate_agent") or "").strip()
        if not name or self.platform is None:
            return default
        try:
            from ..agents.roster import resolve_target

            entry = resolve_target(self.platform, name)
        except Exception:  # noqa: BLE001 — roster trouble keeps the default
            return default
        if entry is None:
            return default
        if entry.kind == "builtin":
            try:
                return (AgentType(entry.name), None, None, None)
            except ValueError:
                return default
        if entry.kind == "dynamic":
            try:
                slug = entry.name.split(":", 1)[-1]
                registry = getattr(self.platform, "agents_registry", None)
                definition = (
                    registry.definition(slug) if registry is not None else None
                )
                if definition is None:
                    return default
                rec = registry.get(slug)
                provider = (rec.provider or None) if rec is not None else None
                model = (rec.model or None) if rec is not None else None
                return (definition.type, definition, provider, model)
            except Exception:  # noqa: BLE001 — a broken record keeps the default
                return default
        return default

    async def _run_dynamic_session(self, session: Any, definition: Any) -> Any:
        """Run an escalated session on a DYNAMIC agent's stored definition —
        the same runtime path POST /agents/{name}/spawn uses (``run_session``
        only knows builtin definitions), with the same status reflection."""
        from ..agents.runtime import AgentRuntime
        from ..core.models import AgentState, SessionStatus

        run = await AgentRuntime(self.platform).run(session, definition)
        session.status = (
            SessionStatus.COMPLETED
            if run.state is AgentState.COMPLETED
            else SessionStatus.FAILED
        )
        session.summary = run.result
        session.finished_at = utcnow()
        self.orchestrator._save(session)
        return session

    # -- pending prompts (v1.137.0) ----------------------------------------
    def _run_state(self, run_id: str) -> str:
        """The prompt's run status ("missing" when gone). On a read failure
        say "waiting" — the atomic claim is the real arbiter; a flaky read
        must not expire a live gate."""
        try:
            from ..workflows.models import WorkflowRunRecord

            with session_scope(self.engine) as db:
                rec = db.get(WorkflowRunRecord, run_id)
            return rec.status if rec is not None else "missing"
        except Exception:  # noqa: BLE001
            return "waiting"

    def _fresh_open_prompt(self, channel: str, sender_id: Any) -> Any:
        """The identity's newest open prompt, or None (see the _ex variant)."""
        return self._fresh_open_prompt_ex(channel, sender_id)[0]

    def _fresh_open_prompt_ex(self, channel: str, sender_id: Any) -> tuple[Any, str]:
        """The identity's newest open prompt, EXPIRING it first when its run
        un-parked by other means (answered from the desktop, cancelled) — the
        phone must never resolve, or be nagged about, a dead gate.

        Returns ``(prompt, "")`` when fresh, ``(None, <run status>)`` when the
        newest prompt just expired on this look (so callers can reply honestly
        about WHY the gate is gone), ``(None, "")`` when nothing was open at
        all. Never raises."""
        if self.prompt_store is None:
            return None, ""
        try:
            prompt = self.prompt_store.newest_open(channel, str(sender_id))
            if prompt is None:
                return None, ""
            status = self._run_state(prompt.ref_id)
            if status != "waiting":
                self.prompt_store.expire(prompt.id, status="expired")
                return None, status
            return prompt, ""
        except Exception:  # noqa: BLE001 — a prompt lookup must never break a turn
            log.warning("pending prompt lookup failed on %r", channel, exc_info=True)
            return None, ""

    def _pending_reminder(self, channel: str, sender_id: Any) -> str:
        """The '(A workflow is still waiting…)' suffix for the outbound phone
        copy of a normal chat reply — '' when nothing fresh is open."""
        prompt = self._fresh_open_prompt(channel, sender_id)
        if prompt is None:
            return ""
        return pending_reminder(prompt.question)

    async def _answer_reply(
        self,
        name: str,
        ch: Channel,
        msg: InboundMessage,
        display: str,
        chat_on: bool,
        user_text: str,
        reply: str,
        status: str,
        outbound_suffix: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        """Append the exchange (best-effort, chat-enabled channels only — a
        command-only channel must not start minting desktop threads) + send
        the reply — the shared tail of every answer-path outcome.

        ``outbound_suffix`` rides the PHONE copy only (the still-waiting
        reminder after resolving one of several open prompts) — never the
        thread, same rule as the chat-reply reminder."""
        tid = (
            self._append_exchange(name, msg, display, user_text, reply)
            if chat_on
            else ""
        )
        sent = await self.send_chunked(
            ch, reply + outbound_suffix, chat_id=msg.reply_to
        )
        return {
            "channel": name,
            "status": status,
            "thread_id": tid,
            "sent": sent,
            **extra,
        }

    async def _handle_answer_command(
        self,
        name: str,
        ch: Channel,
        msg: InboundMessage,
        text: str,
        display: str,
        chat_on: bool,
    ) -> dict[str, Any]:
        """Explicit ``/answer <text>`` — resolves the identity's newest open
        prompt, mid-conversation or not. Honest replies for nothing-waiting
        and for an empty answer. A numeric argument maps to the prompt's
        options exactly like a bare numbered pick."""
        answer = text[len("/answer"):].strip()
        prompt = self._fresh_open_prompt(name, msg.sender_id)
        if prompt is None:
            return await self._answer_reply(
                name, ch, msg, display, chat_on, text,
                NOTHING_WAITING_REPLY, "answer_none",
            )
        if not answer:
            return await self._answer_reply(
                name, ch, msg, display, chat_on, text,
                ANSWER_USAGE_REPLY, "answer_usage",
            )
        options = prompt_options(prompt)
        if options and answer.isdecimal() and 1 <= int(answer) <= len(options):
            answer = options[int(answer) - 1]
        return await self._resolve_prompt(
            name, ch, msg, display, chat_on, prompt, answer, text
        )

    async def _resolve_prompt(
        self,
        name: str,
        ch: Channel,
        msg: InboundMessage,
        display: str,
        chat_on: bool,
        prompt: Any,
        answer: str,
        original_text: str,
    ) -> dict[str, Any]:
        """Resolve ``prompt`` with ``answer`` via the injected atomic-claim
        answer path (the HTTP route's exact first-answer-wins semantics). A
        won claim marks the prompt answered and echoes what happened; a lost
        claim (answered/cancelled elsewhere) gets the honest already-answered
        reply and marks the prompt superseded."""
        if self.answer_run is None:
            # Wired prompts without an answer path is a misconfiguration —
            # be honest rather than pretending the gate opened.
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                "I can't deliver answers right now — use the Workflows page.",
                "answer_error",
            )
        try:
            res = await self.answer_run(prompt.ref_id, answer)
        except Exception as exc:  # noqa: BLE001 — the loop must reply, not die
            log.exception("pending prompt answer failed on %r", name)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                f"I hit a problem: {type(exc).__name__}: {exc}",
                "answer_error",
            )
        if (res or {}).get("ok"):
            self.prompt_store.resolve(prompt.id, answer)
            echo = answer_echo(prompt.question, str(res.get("run_name") or prompt.ref_id))
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text, echo, "answered",
                # ANOTHER run may still be parked (back-to-back gates): now
                # that this prompt is closed, surface the next-open one on the
                # phone copy so it is not orphaned until the next chat reply.
                outbound_suffix=self._pending_reminder(name, msg.sender_id),
                prompt_id=prompt.id, run_id=prompt.ref_id,
            )
        # Claim lost: first answer wins, and it wasn't this one.
        self.prompt_store.expire(prompt.id, status="superseded")
        return await self._answer_reply(
            name, ch, msg, display, chat_on, original_text,
            ALREADY_ANSWERED_REPLY, "answer_superseded",
            outbound_suffix=self._pending_reminder(name, msg.sender_id),
            prompt_id=prompt.id, run_id=prompt.ref_id,
        )

    async def _publish(self, etype: str, payload: dict[str, Any], **kw: Any) -> None:
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(etype, payload, **kw)
        except Exception:  # noqa: BLE001 — the event bus must never block comm
            log.exception("failed to publish %s", etype)
