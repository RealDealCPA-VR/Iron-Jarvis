"""Two-way comm: the durable inbound poller (remote command surface).

The notifier's channels only PUSH out. :class:`InboundPoller` adds the receive
leg: it long-polls every channel whose inbound is *explicitly* enabled, and for
each AUTHORIZED message spawns a normal supervised session via the orchestrator,
acks at once, and replies the summary back over the same channel when the
session lands (v1.291.0: the run lives in a tracked background task, so the
phone keeps being READ while its own job works — a job parked on an ask can
hear the phone's "approve", and "/status" / "/cancel" arrive mid-run).

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
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import AgentType, Session
from .base import Channel, ChannelAuthError, InboundMessage, split_message
from .models import InboundOffsetRecord
from .threads import ADDRESSEE_KEY
from .prompts import (
    ALREADY_ANSWERED_REPLY,
    ANSWER_USAGE_REPLY,
    APPROVAL_DECISIONS,
    APPROVAL_GONE_REPLY,
    APPROVAL_KIND,
    APPROVAL_USAGE_REPLY,
    APPROVAL_WORDS,
    NOTHING_WAITING_REPLY,
    answer_echo,
    approval_echo,
    approval_reminder,
    pending_reminder,
    prompt_options,
)

log = get_logger("comm.inbound")

#: Wire copy for the messaging surfaces (v1.136.0) — pinned here so the poller,
#: the desktop fan-out route, and the tests all speak the same words.
NEW_THREAD_REPLY = "Fresh start — next message begins a new conversation."
ESCALATE_ACK = "On it — this needs real work. I'll send the result here."
#: v1.291.0 (io-03): the chat-OFF one-shot lane's ack. It is a plain job,
#: not a chat turn that decided to escalate — "this needs real work" would
#: read as a non-sequitur there.
ONESHOT_ACK = "On it — I'll send the result here."
RATE_LIMIT_REPLY = (
    "Getting a lot of messages — pausing for a minute. Send that one again in a minute."
)
#: v1.231.0 (audit AE14): the honest side of at-most-once — what the chat
#: hears when the daemon restarted while its last message was being handled.
DROPPED_REPLY = "I was restarted while handling your last message — please resend it."
#: v1.291.0 (io-03): a phone-started job now runs OUTSIDE the poll pass, so
#: the inflight marker (dispatch only) no longer covers a restart mid-run.
#: The boot reconcile settles the row; this is what the phone hears about it
#: (``{task}`` = the first 80 characters of what was asked).
INTERRUPTED_REPLY = (
    "Your job was cut off by a restart: '{task}'. Send it again if you still want it."
)

#: Per-identity flood guard: more than this many handled chat turns inside the
#: rolling window gets an honest "pausing" reply instead of a model call (a
#: forwarded-message flood must not become a token bill — see the design doc).
RATE_MAX_TURNS = 8
RATE_WINDOW_SECONDS = 60.0

#: Escalation recap: how many thread-tail messages ride into the session task,
#: and the per-message char cap inside the recap block.
_RECAP_MESSAGES = 6
_RECAP_CHARS = 500

#: v1.285.0 — the phone lane's words for talking to a REMOTE agent.
BACK_TO_JARVIS_REPLY = "Back to Iron Jarvis — your next message is for me."
#: The tokens that end a remote conversation from the phone ("@jarvis").
JARVIS_MENTIONS = frozenset({"jarvis", "ironjarvis", "iron jarvis", "iron-jarvis"})
#: How much of the phone thread a remote is shown as conversation.
_REMOTE_CHAT_ROWS = 60


def _words_beyond_mentions(text: str) -> bool:
    """True when the message says something besides "@name" tokens — the
    part Iron Jarvis should answer after a hand-back. Never raises."""
    try:
        from ..agents.threads import _MENTION_RE

        return bool(_MENTION_RE.sub("", text or "").strip(" \t\r\n,:;-—"))
    except Exception:  # noqa: BLE001
        return bool((text or "").strip())


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
        #: v1.231.0 (audit AE8): per-channel ``{detail, at}`` of the LAST
        #: failed poll (a refused token, a transport blow-up), cleared by the
        #: next poll of that channel that comes back. ``GET /comm/channels``
        #: reads it onto the row as ``last_poll_error``; the lifespan loop
        #: ticks ``ok=False`` off the same verdict (:meth:`poll_verdict`).
        self.poll_errors: dict[str, dict[str, str]] = {}
        #: The Reflex router. When set, an authorized NON-command message that
        #: matches a ``comm`` reflex rule (keyword) fires that rule instead of a
        #: free-form session — so "any message mentioning X → run workflow Y".
        self.reflex_router = reflex_router
        #: v1.291.0 (io-03): the phone-started sessions still running, one
        #: task each (:meth:`_spawn_delivery`). The poll pass never awaits a
        #: session any more — it used to, so a job parked on an ask could not
        #: hear the phone's own "approve" until its 300 s clock ran out. The
        #: lifespan cancels these at shutdown (:meth:`cancel_background`).
        self._session_tasks: set[asyncio.Task] = set()
        #: One lock per (channel, sender): a message being handled and a
        #: finished job delivering its summary to the SAME chat never
        #: interleave (thread rows + chunked sends stay in order).
        self._identity_locks: dict[tuple[str, str], asyncio.Lock] = {}
        #: True once :meth:`cancel_background` ran (the lifespan's shutdown):
        #: a delivery task cancelled AFTER this re-arms the inflight marker so
        #: the next boot tells the phone (see :meth:`_deliver_session`). A
        #: desktop Cancel of the job cancels the same task, but with this
        #: False — that one must NOT re-arm the marker.
        self._shutting_down = False

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

    def _set_offset(
        self, channel: str, offset: int, *, inflight: InboundMessage | None = None
    ) -> None:
        """Persist the offset — and, when ``inflight`` is given, the update
        about to be handled and its chat, IN THE SAME WRITE (v1.231.0, AE14):
        a marker written in a second transaction could miss the crash the
        marker exists to record."""
        with session_scope(self.engine) as db:
            rec = db.get(InboundOffsetRecord, channel)
            if rec is None:
                rec = InboundOffsetRecord(channel=channel, offset=offset)
            else:
                rec.offset = offset
            if inflight is not None:
                uid = inflight.update_id
                rec.inflight_update_id = uid if isinstance(uid, int) else None
                chat = inflight.reply_to if inflight.reply_to is not None else inflight.sender_id
                rec.inflight_chat_id = str(chat or "")
            rec.updated_at = utcnow()
            db.merge(rec)
            db.commit()

    def _clear_inflight(self, channel: str) -> None:
        """Handling RETURNED (answered or failed in-process): nothing is in
        flight any more. Deliberately not reached on ``CancelledError`` — a
        shutdown mid-handling is exactly the case the marker must survive."""
        with session_scope(self.engine) as db:
            rec = db.get(InboundOffsetRecord, channel)
            if rec is None or (rec.inflight_update_id is None and not rec.inflight_chat_id):
                return
            rec.inflight_update_id = None
            rec.inflight_chat_id = ""
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()

    async def _recover_inflight(self, name: str, ch: Channel) -> dict[str, Any] | None:
        """The honest half of at-most-once (v1.231.0, audit AE14).

        If the last daemon died while handling an update on ``name``, the
        offset already confirmed it server-side, so it will never be polled
        again — the sender's message is GONE and nothing said so. Tell that
        chat to resend, publish ``comm.dropped`` (the durable trace on the
        timeline), and clear the marker. Returns the result row for the pass,
        or ``None`` when nothing was in flight.
        """
        with session_scope(self.engine) as db:
            rec = db.get(InboundOffsetRecord, name)
            if rec is None or (rec.inflight_update_id is None and not rec.inflight_chat_id):
                return None
            update_id, chat_id = rec.inflight_update_id, rec.inflight_chat_id
        self._clear_inflight(name)
        notified = False
        if chat_id:
            try:
                res = await asyncio.to_thread(
                    ch.send, f"{self.reply_prefix}{DROPPED_REPLY}", chat_id=chat_id
                )
                notified = bool((res or {}).get("ok"))
            except Exception:  # noqa: BLE001 — the trace below still lands
                log.exception("inbound: could not send the dropped-message notice on %r", name)
        log.warning(
            "inbound: update %s on channel %r was in flight at the last shutdown and "
            "was dropped (at-most-once); sender asked to resend",
            update_id,
            name,
        )
        await self._publish(
            EventType.COMM_DROPPED,
            {
                "channel": name,
                "update_id": update_id,
                "chat_id": chat_id,
                "notified": notified,
                "reason": "daemon restarted while handling the message",
            },
        )
        return {"channel": name, "status": "dropped", "update_id": update_id, "notified": notified}

    @staticmethod
    def poll_verdict(results: list[dict[str, Any]]) -> tuple[bool, Exception | None]:
        """What one pass means for the loop's health line (v1.231.0, AE8):
        ``(True, None)`` when no row is an error, else ``(False, exc)`` naming
        the first failed channel and its detail — the lifespan loop feeds it
        straight to ``_tick("inbound", ok, exc)``."""
        for row in results:
            if isinstance(row, dict) and row.get("status") == "error":
                detail = str(row.get("detail") or "poll failed")
                return False, RuntimeError(f"{row.get('channel') or 'channel'}: {detail}")
        return True, None

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

    # -- background sessions (v1.291.0, io-03) ------------------------------
    def _identity_lock(self, channel: str, sender_id: Any) -> asyncio.Lock:
        """The per-(channel, sender) lock — see ``_identity_locks``."""
        key = (channel, str(sender_id))
        lock = self._identity_locks.get(key)
        if lock is None:
            lock = self._identity_locks[key] = asyncio.Lock()
        return lock

    def _spawn_delivery(self, coro: Any) -> asyncio.Task:
        """Run ``coro`` (a :meth:`_deliver_session`) as a tracked task: held
        strongly until done, a crash logged (never silently dropped), and
        cancellable as a set at shutdown."""
        task = asyncio.create_task(coro)
        self._session_tasks.add(task)
        task.add_done_callback(self._session_task_done)
        return task

    def _session_task_done(self, task: asyncio.Task) -> None:
        self._session_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("phone-started session delivery failed", exc_info=exc)

    def resume_background(self) -> None:
        """The lifespan is (re)starting the poll: a shutdown flag left over from
        a previous stop must not make a desktop Cancel re-arm the marker."""
        self._shutting_down = False

    def cancel_background(self) -> int:
        """Cancel every still-running phone-started session (lifespan
        shutdown). The cancel unwinds ``run_session`` to CANCELLED exactly as
        cancelling the old inline await did. Returns how many were live.

        Sets ``_shutting_down`` BEFORE cancelling: each live delivery task
        then re-arms the inflight marker in its cancel handler, so the next
        boot's ``_recover_inflight`` sends :data:`DROPPED_REPLY` — the phone
        hears about a graceful restart (app update, ``ironjarvis stop``)
        exactly as it did when the inline await held the marker through
        the shutdown cancel."""
        self._shutting_down = True
        live = [t for t in self._session_tasks if not t.done()]
        for task in live:
            task.cancel()
        return len(live)

    async def drain(self) -> None:
        """Wait for every tracked session to land (tests + graceful stops)."""
        while self._session_tasks:
            await asyncio.gather(*list(self._session_tasks), return_exceptions=True)

    async def _deliver_session(
        self,
        name: str,
        ch: Channel,
        msg: InboundMessage,
        session: Any,
        *,
        dyn_def: Any = None,
        tid: str = "",
        display: str = "",
        chat_on: bool = False,
    ) -> None:
        """The half of a phone-started job that used to stall the poll: run
        the session, then put its summary on the thread and send it to the
        chat that asked. A crash becomes an honest "I hit a problem" reply
        (the desktop route's ``_finish`` shape) instead of a silent phone;
        the dynamic lane's ``_finalize_failed`` still settles the row first.
        A cancel passes through — and when it is the SHUTDOWN's cancel
        (``_shutting_down``), it first re-arms the at-most-once inflight
        marker for this message: ``run_session`` settles the row CANCELLED
        (no ``interrupted_at``, so the boot reconcile and
        :meth:`notify_interrupted` have nothing to say), and the next boot's
        ``_recover_inflight`` then sends :data:`DROPPED_REPLY` to this chat.
        A desktop Cancel of the job is the same ``CancelledError`` with the
        flag False: the user stopped it on purpose, nothing to re-arm."""
        try:
            if dyn_def is not None:
                done = await self._run_dynamic_session(session, dyn_def)
            else:
                done = await self.orchestrator.run_session(session.id)
            summary = (done.summary or "(no result)").strip()
        except asyncio.CancelledError:
            if self._shutting_down:
                try:
                    self._set_offset(name, self._get_offset(name), inflight=msg)
                except Exception:  # noqa: BLE001 — a shutdown never raises
                    log.exception(
                        "inbound: could not re-arm the inflight marker for %s on %r",
                        session.id, name,
                    )
            raise
        except Exception as exc:  # noqa: BLE001 — deliver, don't vanish
            log.exception("phone-started session %s failed on %r", session.id, name)
            summary = f"I hit a problem: {type(exc).__name__}: {exc}"
        async with self._identity_lock(name, msg.sender_id):
            if tid:
                self._safe_append(
                    tid, "assistant", summary,
                    channel=name, sender_id=msg.sender_id, display=display,
                )
            if chat_on:
                await self.send_chunked(ch, summary, chat_id=msg.reply_to)
            else:
                # The one-shot lane's historical wire shape: prefixed + capped.
                body = f"{self.reply_prefix}{summary}"[: self.max_reply_chars]
                await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)

    # -- restart mid-run (v1.291.0, io-03) ---------------------------------
    def notify_interrupted(self, *, since: Any) -> int:
        """Tell each phone whose job a restart cut off (boot, right after the
        session reconcile).

        Before v1.291.0 the inline await kept the inflight marker set for the
        whole run, so ``_recover_inflight`` told the chat to resend. The
        marker now covers dispatch only; ``reconcile_interrupted_sessions``
        settles the row as FAILED + ``interrupted_at`` and rings the desktop
        bell — nothing went back over the channel. This finds every
        ``comm:<name>`` session THIS boot stamped (``interrupted_at >=
        since``) and sends :data:`INTERRUPTED_REPLY` to the originating
        private chat (the single allowed sender's id; else the channel's
        configured chat — see :meth:`_deliver_interrupted_notice`) from a
        tracked task (:meth:`_spawn_delivery`), so boot never waits
        on the network and a shutdown cancels it like any delivery. The line
        also lands on the sender's thread when the channel is chat-enabled
        and has exactly one allowed sender. Never raises; returns how many
        notices were queued.
        """
        try:
            with session_scope(self.engine) as db:
                rows = [
                    (str(s.origin or "")[len("comm:"):], s.id, s.task or "")
                    for s in db.exec(
                        select(Session).where(
                            Session.origin.startswith("comm:"),  # type: ignore[union-attr]
                            Session.interrupted_at.is_not(None),  # type: ignore[union-attr]
                            Session.interrupted_at >= since,  # type: ignore[operator]
                        )
                    )
                ]
        except Exception:  # noqa: BLE001 — a boot step never raises
            log.exception("inbound: could not list the restart-interrupted phone jobs")
            return 0
        queued = 0
        for name, sid, task in rows:
            ch = self.notifier.get(name) if name else None
            if ch is None:
                continue
            self._spawn_delivery(self._deliver_interrupted_notice(name, ch, sid, task))
            queued += 1
        if queued:
            log.warning(
                "inbound: %d phone-started job(s) were cut off by the restart; "
                "telling the phone(s)",
                queued,
            )
        return queued

    async def _deliver_interrupted_notice(
        self, name: str, ch: Channel, session_id: str, task: str
    ) -> None:
        """One :data:`INTERRUPTED_REPLY` to the originating private chat when
        it is knowable — exactly one allowed sender, whose private chat id IS
        the sender id (the same fallback ``_set_offset`` uses) — else to the
        channel's configured chat (no ``chat_id``); then onto that single
        sender's thread when the channel is a chat surface. The explicit
        ``chat_id`` matters on an inbound-only Telegram channel (allowed
        senders, no ``chat_id`` configured): a bare send fails there with
        "config needs `chat_id`" and the phone would stay silent. Guarded end
        to end: a failed send is logged, never raised."""
        body = f"{self.reply_prefix}{INTERRUPTED_REPLY.format(task=task[:80])}"
        body = body[: self.max_reply_chars]
        senders = ch.allowed_senders()
        sender = next(iter(senders)) if len(senders) == 1 else ""
        async with self._identity_lock(name, sender or "*"):
            try:
                if sender:
                    res = await asyncio.to_thread(ch.send, body, chat_id=sender)
                else:
                    res = await asyncio.to_thread(ch.send, body)
                if not (res or {}).get("ok"):
                    log.warning(
                        "inbound: the restart notice for %s did not reach %r: %s",
                        session_id, name, (res or {}).get("detail"),
                    )
            except Exception:  # noqa: BLE001 — the thread line below still lands
                log.exception("inbound: could not send the restart notice on %r", name)
            if sender and self._chat_ready(ch):
                try:
                    tid = self.thread_store.resolve(name, sender, "").id
                except Exception:  # noqa: BLE001 — no thread, no line; the phone heard
                    log.warning("comm thread resolve failed on %r", name, exc_info=True)
                    return
                self._safe_append(
                    tid, "assistant", INTERRUPTED_REPLY.format(task=task[:80]),
                    channel=name, sender_id=sender,
                )

    async def send_chunked(
        self, ch: Channel, reply: str, *, chat_id: Any, prefix: str | None = None
    ) -> bool:
        """Send ``reply`` (prefixed) split on the channel's ``chunk_limit`` —
        the full-chat replacement for the one-shot ``[:max_reply_chars]``
        truncation (a long answer must ARRIVE, not get cut). True iff every
        chunk reported ok. ``prefix`` (v1.285.0) names another speaker —
        ``"hermes: "`` for a remote agent's line — instead of the assistant's
        own ``reply_prefix``; ``None`` keeps Iron Jarvis's."""
        limit = int(getattr(ch, "chunk_limit", 3500) or 3500)
        ok = True
        lead = self.reply_prefix if prefix is None else prefix
        for chunk in split_message(f"{lead}{reply}", limit):
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
        extra: dict[str, Any] | None = None,
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
        kw: dict[str, Any] = {"extra": extra} if extra else {}
        try:
            self.thread_store.append(thread_id, role, content, **kw)
            return thread_id
        except ValueError:
            if channel is None:
                return ""
            try:
                fresh = self.thread_store.resolve(channel, str(sender_id), display)
                self.thread_store.append(fresh.id, role, content, **kw)
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
            dropped = await self._recover_inflight(name, ch)
            if dropped is not None:
                results.append(dropped)
            offset = self._get_offset(name)
            try:
                # The poll is blocking HTTP — run it off the event loop so a
                # long-poll never stalls the daemon. ``to_thread`` of a synchronous
                # (test) transport is still deterministic.
                messages, next_offset = await asyncio.to_thread(
                    ch.poll, offset, timeout=self.poll_timeout
                )
            except ChannelAuthError as exc:
                # A refused credential is NOT an empty batch (v1.231.0, AE8):
                # record it where the row and the loop can read it.
                detail = str(exc)[:300]
                log.warning("inbound poll refused on channel %r: %s", name, detail)
                self.poll_errors[name] = {"detail": detail, "at": utcnow().isoformat()}
                results.append({"channel": name, "status": "error", "detail": detail})
                continue
            except Exception as exc:  # noqa: BLE001 — never let one channel kill the pass
                log.exception("inbound poll failed for channel %r", name)
                detail = f"{type(exc).__name__}: {exc}"[:300]
                self.poll_errors[name] = {"detail": detail, "at": utcnow().isoformat()}
                results.append({"channel": name, "status": "error", "detail": detail})
                continue
            self.poll_errors.pop(name, None)
            for msg in messages:
                # AT-MOST-ONCE on a remote COMMAND surface: persist the offset
                # BEFORE running, so a crash mid-handling drops the in-flight
                # message rather than re-running a remote-triggered action on
                # restart (duplicate side effects are worse than a dropped reply).
                # The in-flight marker rides the SAME write (AE14): a restart
                # that lands between here and the clear below finds it on boot
                # and tells the chat to resend — the drop stays, the silence goes.
                if isinstance(msg.update_id, int):
                    offset = max(offset, msg.update_id + 1)
                self._set_offset(name, offset, inflight=msg)
                try:
                    res = await self._handle(name, ch, msg)
                except Exception as exc:  # noqa: BLE001 — keep processing the batch
                    log.exception("inbound handling failed on channel %r", name)
                    res = {
                        "channel": name,
                        "status": "error",
                        "detail": f"handling failed: {type(exc).__name__}: {exc}"[:300],
                    }
                # Reached only when handling RETURNED — a CancelledError
                # (shutdown) skips this on purpose and leaves the marker.
                self._clear_inflight(name)
                results.append(res)
            # Some channels report a high-water offset even with no text messages
            # (e.g. only non-text updates); persist it so we don't refetch them.
            if next_offset > offset:
                self._set_offset(name, next_offset)
        return results

    async def _handle(
        self, name: str, ch: Channel, msg: InboundMessage
    ) -> dict[str, Any]:
        """Authorize, then (if allowed) act on the message and reply.

        Returns as soon as the message is DISPATCHED: a command/answer/chat
        turn is answered inline; a job (one-shot or escalated) is acked
        inline and runs in a tracked task (v1.291.0) whose summary lands
        later — the row carries the ``session_id`` and the ack's ``sent``.
        Serialized per (channel, sender) against that later delivery.
        """
        # Loop protection: never act on a bot's message (incl. our own echoes).
        if msg.is_bot:
            return {"channel": name, "status": "ignored_bot"}
        async with self._identity_lock(name, msg.sender_id):
            return await self._dispatch(name, ch, msg)

    async def _dispatch(
        self, name: str, ch: Channel, msg: InboundMessage
    ) -> dict[str, Any]:
        """The body of :meth:`_handle`, under its identity lock."""

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
                # elsewhere, or an approval outlived its answer window): this
                # integer was aimed at the dead gate — reply honestly instead
                # of misfiring a chat turn on "1".
                reply = (
                    APPROVAL_GONE_REPLY
                    if gone_status == "approval_gone"
                    else ALREADY_ANSWERED_REPLY
                    if gone_status in ("resuming", "running", "completed")
                    else NOTHING_WAITING_REPLY
                )
                return await self._answer_reply(
                    name, ch, msg, display, chat_on, text, reply, "answer_expired"
                )

        # MID-RUN APPROVALS (v1.200.0): while an APPROVAL prompt is open, a
        # bare "approve"/"deny" — the alert's exact vocabulary, nothing looser
        # (see APPROVAL_WORDS) — is the one-tap answer the alert promised.
        # Approval prompts ONLY: a workflow gate keeps the
        # free-text-never-resolves rule, so "approve" aimed at a workflow ask
        # stays a normal chat turn.
        if self.prompt_store is not None and low in APPROVAL_WORDS:
            prompt, gone_status = self._fresh_open_prompt_ex(name, msg.sender_id)
            if prompt is not None and getattr(prompt, "kind", "") == APPROVAL_KIND:
                return await self._resolve_prompt(
                    name, ch, msg, display, chat_on, prompt, low, text
                )
            if gone_status == "approval_gone":
                # Aimed at a pause that already ended (dashboard answer or the
                # runtime's timeout) — honesty beats a chat misfire on "deny".
                return await self._answer_reply(
                    name, ch, msg, display, chat_on, text,
                    APPROVAL_GONE_REPLY, "approval_expired",
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
            # v1.231.0 (audit AE6): a rule that MATCHED but could not start
            # (its workflow was deleted, its remote agent is gone) is answered
            # by name with the reason — it used to be filtered out here and
            # the message fell through to a free-form session, so the phone
            # got an unrelated agent answer instead of "that rule is broken".
            failed = [f for f in fired if not f.get("ok")]
            fired = [f for f in fired if f.get("ok")]
            if fired or failed:
                parts: list[str] = []
                if fired:
                    parts.append(
                        "Triggered: "
                        + "; ".join(
                            f"{f.get('kind', 'action')} {f.get('rule', '')}".strip()
                            for f in fired
                        )
                    )
                parts.extend(
                    f'Rule "{f.get("rule", "")}" could not start: {f.get("error") or "failed"}'
                    for f in failed
                )
                summary = " ".join(parts)
                if chat_on:
                    # The reflex exchange lands on the thread too.
                    self._append_exchange(name, msg, display, text, summary)
                body = f"{self.reply_prefix}{summary}"[: self.max_reply_chars]
                send_res = await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)
                return {
                    "channel": name,
                    "status": "reflex" if fired else "reflex_failed",
                    "fired": len(fired),
                    "failed": [
                        {"rule": f.get("rule", ""), "error": f.get("error") or "failed"}
                        for f in failed
                    ],
                    "sent": bool(send_res.get("ok")),
                }

        # FREE-FORM, full chat: a real conversational turn on the durable
        # thread (memory + skills + project spine via the injected turn
        # service), replying chunked to the channel's own size cap.
        if chat_on:
            return await self._handle_chat(name, ch, msg, text, display)

        # Per-identity flood guard (v1.291.0): now that a job no longer holds
        # the poll, one sender could stack up overlapping sessions — the
        # one-shot lane counts against the same budget the chat lane does.
        if not self.rate_ok(name, msg.sender_id):
            body = f"{self.reply_prefix}{RATE_LIMIT_REPLY}"[: self.max_reply_chars]
            send_res = await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)
            return {
                "channel": name,
                "status": "rate_limited",
                "sender": str(msg.sender_id),
                "sent": bool(send_res.get("ok")),
            }

        # Spawn a NORMAL supervised session (same orchestrator + permission
        # engine as a local user), ack, and let it run in a tracked task — the
        # summary follows when it lands (``_deliver_session``). Origin
        # ``comm:<channel>`` (v1.231.0, audit AE17): the runtime's ask
        # allowlist reads it, so a session the phone started may ask back
        # through the phone — which only works because the poll keeps reading
        # the phone while the job waits (v1.291.0, io-03).
        session = await self.orchestrator.create_session(
            text, self.agent_type, origin=f"comm:{name}"
        )
        await self._publish(
            EventType.COMM_RECEIVED,
            {"channel": name, "sender": msg.sender_id, "task": text},
            session_id=session.id,
        )
        # Safe to reply to the originating chat: we only reach here for the
        # sender's own private chat (the non-private guard above refused groups).
        body = f"{self.reply_prefix}{ONESHOT_ACK}"[: self.max_reply_chars]
        # The task is spawned BEFORE the ack's network round-trip: a shutdown
        # landing inside that send would otherwise leave an ACTIVE row with no
        # task AND an armed dispatch marker, and the next boot would tell the
        # phone twice. The summary cannot overtake the ack — the delivery
        # waits on the identity lock this pass holds.
        self._spawn_delivery(self._deliver_session(name, ch, msg, session))
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
        the poll loop. ``escalate: true`` sends an ack and starts the normal
        supervised session with a thread-tail recap in a tracked task that
        delivers the summary both to the phone and onto the thread when it
        lands (the desktop hears it via chat.thread_updated).
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
        # A REMOTE AGENT, ADDRESSED FROM THE PHONE (v1.285.0). "@hermes …" (or
        # a follow-up while the conversation is still with hermes) goes to the
        # remote as a conversation — not to Iron Jarvis — and its reply comes
        # back on this same thread and this same phone, named. "@jarvis" ends
        # it. A local agent named here still takes the Jarvis lane below.
        remote_name, back_to_jarvis = self._remote_addressee(thread, tid, text)
        if back_to_jarvis and _words_beyond_mentions(text):
            # "@jarvis what's my calendar?" — the hand-back happened (the
            # sticky is cleared); the question is Jarvis's to answer NOW, so
            # fall through to the ordinary turn instead of costing a round trip.
            back_to_jarvis = False
        if back_to_jarvis:
            # The user's line is already on the thread (above) — only the
            # answer lands here, then goes to the phone.
            if tid:
                self._safe_append(
                    tid, "assistant", BACK_TO_JARVIS_REPLY,
                    channel=name, sender_id=msg.sender_id, display=display,
                )
            sent = await self.send_chunked(ch, BACK_TO_JARVIS_REPLY, chat_id=msg.reply_to)
            return {"channel": name, "status": "remote_cleared", "thread_id": tid, "sent": sent}
        if remote_name:
            return await self._handle_remote_chat(
                name, ch, msg, thread, tid, text, display, remote_name
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
        sent = await self.send_chunked(ch, ESCALATE_ACK, chat_id=msg.reply_to)
        _spawn_kwargs: dict[str, Any] = {}
        if esc_provider:
            _spawn_kwargs["provider"] = esc_provider
        if esc_model:
            _spawn_kwargs["model"] = esc_model
        # The escalated session inherits the thread's project tag (the same
        # kwarg the dashboard passes when escalating desktop chat), so the
        # run gets the project's brief/knowledge/recent-activity spine.
        session = await self.orchestrator.create_session(
            task,
            agent_type,
            project_id=project_id or None,
            origin=f"comm:{name}",  # v1.231.0 (AE17): may ask back via the phone
            **_spawn_kwargs,
        )
        await self._publish(
            EventType.COMM_RECEIVED,
            {"channel": name, "sender": str(msg.sender_id), "task": text},
            session_id=session.id,
        )
        # v1.291.0 (io-03): the run — and the summary onto the thread + to the
        # phone when it lands — moves into a tracked task so this pass (and
        # the poll behind it) returns now and the phone keeps being read.
        self._spawn_delivery(
            self._deliver_session(
                name, ch, msg, session,
                dyn_def=dyn_def, tid=tid, display=display, chat_on=True,
            )
        )
        return {
            "channel": name,
            "status": "chat_escalated",
            "thread_id": tid,
            "session_id": session.id,
            "sent": sent,
        }

    # -- a remote agent from the phone (v1.285.0) ---------------------------

    def _remote_addressee(self, thread: Any, tid: str, text: str) -> tuple[str, bool]:
        """Who this phone message is for: ``(remote name, False)`` when a
        REMOTE agent is @-mentioned — or the conversation is still with one and
        the text names nobody — ``("", True)`` when "@jarvis" ends it, else
        ``("", False)`` for Iron Jarvis. A LOCAL agent named here is left to the
        Jarvis lane (the daemon's own escalation handles those). Never raises:
        a resolution problem is a plain Jarvis turn."""
        if self.platform is None or self.thread_store is None or not tid:
            return "", False
        try:
            from ..agents.roster import resolve_target
            from ..agents.threads import parse_mentions

            tokens = parse_mentions(text)
            # A REMOTE named in the text wins ("@hermes ask @jarvis about it"
            # is still for hermes); "@jarvis" without one ends the remote
            # conversation — and the rest of the sentence, if any, is Jarvis's
            # to answer (the caller falls through), never dropped.
            for token in tokens:
                if token in JARVIS_MENTIONS:
                    continue
                entry = resolve_target(self.platform, token, require_delegable=False)
                if entry is None:
                    continue
                if str(getattr(entry, "kind", "")) == "remote":
                    return str(entry.name).split(":", 1)[-1], False
                return "", False  # a local agent — the Jarvis lane's business
            if any(t in JARVIS_MENTIONS for t in tokens):
                sticky = self.thread_store.get_setup_value(tid, ADDRESSEE_KEY)
                self.thread_store.set_setup_value(tid, ADDRESSEE_KEY, "")
                return "", bool(sticky)
            sticky = self.thread_store.get_setup_value(tid, ADDRESSEE_KEY)
            if sticky.startswith("remote:"):
                return sticky.split(":", 1)[1], False
        except Exception:  # noqa: BLE001 — never let addressing sink a turn
            log.warning("remote addressee resolution failed", exc_info=True)
        return "", False

    async def _handle_remote_chat(
        self,
        name: str,
        ch: Channel,
        msg: InboundMessage,
        thread: Any,
        tid: str,
        text: str,
        display: str,
        remote_name: str,
    ) -> dict[str, Any]:
        """One turn WITH a remote agent from the phone (v1.285.0).

        The room is the same panel a desktop @-mention would use, bound to
        this phone thread (``AgentThreads.for_chat``), so the Agents page shows
        the exchange and the remote's inbound messages land on this thread. The
        remote is shown the phone conversation as rows (``history_rows``), its
        reply is appended attributed (``panelWho``) and sent to the phone
        named, and the conversation STAYS with it until "@jarvis".
        """
        from types import SimpleNamespace

        from ..agents.threads import AgentThreads, clean_participants

        who = f"remote:{remote_name}"
        try:
            threads = AgentThreads(self.engine)
            room = threads.for_chat(thread.id, title=text[:60])
            threads.add_participants(
                room.id,
                clean_participants(
                    [{"source": "remote", "name": remote_name, "role": "participant"}]
                ),
            )
            rows = self.thread_store.history_rows(tid, limit=_REMOTE_CHAT_ROWS) if tid else []
            # The user's line was appended above — it is the MESSAGE, not history.
            if rows and rows[-1].get("who") == "user" and rows[-1].get("content") == text:
                rows = rows[:-1]
            shim = SimpleNamespace(platform=self.platform)
            out = await threads.run_round(
                room.id, text, shim, directed=[remote_name], chat_history=rows
            )
            spoken = [e for e in out.get("entries", []) if e.get("who") != "user"]
            last = spoken[-1] if spoken else {}
            reply = str(last.get("content") or "").strip()
            failed = str(last.get("error") or "").strip()
            if not reply:
                reply = failed or f"{remote_name} did not answer."
            pending = bool(last.get("pending"))
        except Exception as exc:  # noqa: BLE001 — the phone still gets an answer
            log.exception("remote chat failed on %r for %r", name, remote_name)
            reply = f"{remote_name} couldn't answer: {type(exc).__name__}: {exc}"
            pending = False
        # The conversation is with the remote now — until "@jarvis".
        if tid:
            self.thread_store.set_setup_value(tid, ADDRESSEE_KEY, who)
            tid = self._safe_append(
                tid, "assistant", reply,
                channel=name, sender_id=msg.sender_id, display=display,
                extra={"panelWho": who, **({"panelKind": "pending"} if pending else {})},
            ) or tid
        sent = await self.send_chunked(
            ch, reply, chat_id=msg.reply_to, prefix=f"{remote_name}: "
        )
        await self._publish(
            EventType.COMM_RECEIVED,
            {"channel": name, "sender": str(msg.sender_id), "task": text, "remote": remote_name},
        )
        return {
            "channel": name,
            "status": "remote_chat",
            "thread_id": tid,
            "remote": remote_name,
            "pending": pending,
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

        try:
            run = await AgentRuntime(self.platform).run(session, definition)
        except Exception as exc:  # noqa: BLE001
            # v1.288.0 (agents-04): a provider crash used to escape to the
            # poller's guard with the session still ACTIVE and its run row
            # RUNNING until the next boot reconcile. Every other lane ends in
            # the orchestrator's failure finalizer, which settles both.
            await self.orchestrator._finalize_failed(session, exc)
            raise
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
            if getattr(prompt, "kind", "") == APPROVAL_KIND:
                # An approval pause has a HARD deadline (the runtime denies at
                # SESSION_APPROVAL_TIMEOUT_S) and approval.resolved normally
                # expires the row (prompts.handle_workflow_waiting dispatch);
                # this age check is the belt for a missed event, so a dead
                # gate is never nagged about or "answered" forever. The
                # registry's resolve() stays the real arbiter — a failed
                # resolve expires the prompt too (_resolve_approval_prompt).
                if self._approval_prompt_stale(prompt):
                    self.prompt_store.expire(prompt.id, status="expired")
                    return None, "approval_gone"
                return prompt, ""
            status = self._run_state(prompt.ref_id)
            if status != "waiting":
                self.prompt_store.expire(prompt.id, status="expired")
                return None, status
            return prompt, ""
        except Exception:  # noqa: BLE001 — a prompt lookup must never break a turn
            log.warning("pending prompt lookup failed on %r", channel, exc_info=True)
            return None, ""

    @staticmethod
    def _approval_ttl() -> float:
        """The runtime's approval answer window, imported lazily (comm must
        not pull the agents package at module load); its documented 300s as
        the fallback."""
        try:
            from ..agents.runtime import SESSION_APPROVAL_TIMEOUT_S

            return float(SESSION_APPROVAL_TIMEOUT_S)
        except Exception:  # noqa: BLE001
            return 300.0

    def _approval_prompt_stale(self, prompt: Any) -> bool:
        """Whether an approval prompt outlived the runtime's answer window.
        On any read quirk say fresh — the registry's resolve() is the real
        arbiter, and a flaky timestamp must not expire a live gate."""
        try:
            created = getattr(prompt, "created_at", None)
            if created is None:
                return False
            now = utcnow()
            if created.tzinfo is None:
                # SQLite round-trips naive; the store stamps UTC (core.ids).
                created = created.replace(tzinfo=now.tzinfo)
            return (now - created).total_seconds() > self._approval_ttl()
        except Exception:  # noqa: BLE001
            return False

    def _pending_reminder(self, channel: str, sender_id: Any) -> str:
        """The '(… still waiting …)' suffix for the outbound phone copy of a
        normal chat reply — '' when nothing fresh is open. Kind-aware: an
        approval gate must not be announced in a workflow's words."""
        prompt = self._fresh_open_prompt(channel, sender_id)
        if prompt is None:
            return ""
        if getattr(prompt, "kind", "") == APPROVAL_KIND:
            return approval_reminder(prompt.question)
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
        if getattr(prompt, "kind", "") == APPROVAL_KIND:
            # A mid-run approval resolves through platform.approvals, not the
            # workflow answer path — its ref_id is an approval id, which
            # answer_run would misread as a (missing) workflow run.
            return await self._resolve_approval_prompt(
                name, ch, msg, display, chat_on, prompt, answer, original_text
            )
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

    async def _resolve_approval_prompt(
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
        """Resolve a mid-run tool approval (v1.200.0) through the SAME
        registry the dashboard bell writes to (``platform.approvals``), so
        there is ONE write path and the first answer wins whichever surface
        it came from.

        A phone reply only ever grants ``"once"`` (see APPROVAL_DECISIONS):
        widening the rest of the run belongs on a surface that can see the
        run. A failed resolve means the pause already ended — answered
        elsewhere, or the runtime's timeout denied it — so the prompt is
        EXPIRED on the spot (timeout hygiene: an approval prompt must never
        linger past a failed resolve) and the reply says so honestly."""
        decision = APPROVAL_DECISIONS.get(str(answer or "").strip().lower())
        if decision is None:
            # /answer <free text> aimed at an approval gate: only the yes/no
            # vocabulary decides a permission — nothing resolves implicitly.
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                APPROVAL_USAGE_REPLY, "answer_usage",
            )
        approvals = getattr(self.platform, "approvals", None)
        if approvals is None:
            # Wired prompts without the registry is a misconfiguration — be
            # honest rather than pretending the gate opened (same posture as
            # the missing answer_run branch above).
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                "I can't deliver approvals right now — use the dashboard bell.",
                "answer_error",
            )
        try:
            ok = bool(approvals.resolve(prompt.ref_id, decision))
        except Exception as exc:  # noqa: BLE001 — the loop must reply, not die
            log.exception("approval resolve failed on %r", name)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                f"I hit a problem: {type(exc).__name__}: {exc}",
                "answer_error",
            )
        if ok:
            self.prompt_store.resolve(prompt.id, decision)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                approval_echo(decision), "approval_answered",
                # Another gate may still be open — surface it on the phone
                # copy, same rule as the workflow tail above.
                outbound_suffix=self._pending_reminder(name, msg.sender_id),
                prompt_id=prompt.id, approval_id=prompt.ref_id,
                decision=decision,
            )
        # resolve() said no: unknown, expired, or already answered. The
        # registry is the arbiter — close the row and say what happened.
        self.prompt_store.expire(prompt.id, status="expired")
        return await self._answer_reply(
            name, ch, msg, display, chat_on, original_text,
            APPROVAL_GONE_REPLY, "approval_expired",
            outbound_suffix=self._pending_reminder(name, msg.sender_id),
            prompt_id=prompt.id, approval_id=prompt.ref_id,
        )

    async def _publish(self, etype: str, payload: dict[str, Any], **kw: Any) -> None:
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(etype, payload, **kw)
        except Exception:  # noqa: BLE001 — the event bus must never block comm
            log.exception("failed to publish %s", etype)
