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
import re
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import AgentType, Session
from .base import (
    Channel,
    ChannelAuthError,
    FileNotTaken,
    InboundMessage,
    split_message,
    without_signoff,
)
from .models import InboundOffsetRecord
from .threads import ADDRESSEE_KEY
#: 10-a: the pending-prompt kind of "Use <service> for phone voice notes
#: too?" (its ``ref_id`` is the kept recording).
from .voice_note import ASK_KIND as _VOICE_ASK_KIND
from .prompts import (
    ALREADY_ANSWERED_REPLY,
    ANSWER_USAGE_REPLY,
    APPROVAL_BUTTONS,
    APPROVAL_DECISIONS,
    APPROVAL_GONE_REPLY,
    APPROVAL_KIND,
    APPROVAL_USAGE_REPLY,
    APPROVAL_WORDS,
    CODE_FOLDER_KIND,
    CODE_START_KIND,
    DONE_DATA,
    FILE_SEND_KIND,
    FILE_SEND_OPTIONS,
    NOTHING_WAITING_REPLY,
    PRESS_ANSWERED,
    PRESS_ELSEWHERE,
    answer_echo,
    approval_echo,
    approval_reminder,
    parse_button,
    pending_reminder,
    pressed_label,
    prompt_buttons,
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

#: R0.3: a sender is told at most this often that their files were not used,
#: so a burst of files (an e-mail poll's batch, several Slack uploads) is told
#: once — Telegram's notice is once per sender per poll for the same reason.
#: Since 10-a it bounds a receipt that took NOTHING (every file refused); a
#: receipt that kept a file or heard a voice note always goes out.
FILES_NOT_READ_EVERY_S = 60.0

#: 10-a: at most this many files are taken from one sender inside
#: RATE_WINDOW_SECONDS (four full messages): the turn cap counts turns, and a
#: burst of photo messages must not fill the disk before it bites.
FILES_PER_WINDOW = 20


def files_not_read_note(count: int) -> str:
    """The line added after the sender's words when files came with them that
    were not taken (R0.3 review; 10-a: too large, a kind not taken, past five,
    or with no download). The turn reads it, so the model says it has not
    seen those files instead of guessing what they hold, and the saved chat
    shows they were sent. Iron Jarvis's own words, never the files' names or
    contents."""
    n = max(1, int(count or 1))
    if n == 1:
        return "(This message came with a file that could not be taken, so it was not read.)"
    return f"(This message came with {n} files that could not be taken, so they were not read.)"


def files_only_line(names: list[str]) -> str:
    """The user's line when the phone sent files and no words (10-a): the turn
    needs words, and these are plainly not the sender's own."""
    return f"(Sent with no message: {', '.join(names)})"


def _bare(words: str) -> str:
    return re.sub(r"[\W_]+", "", str(words or "")).lower()


def _beyond_the_recording(words: str, recording: str) -> bool:
    """Has ``words`` anything to answer besides the recording's own name and
    what a mail app adds by itself (10-a)? A voice memo shared by e-mail
    carries its name as the subject ("New Recording 3" for New Recording
    3.m4a): that is no request. Any other words are, and they run."""
    name = {_bare(Path(recording).stem), _bare(recording)}
    return any(_bare(line) and _bare(line) not in name
               for line in without_signoff(words).splitlines())


@dataclass
class Taken:
    """What became of the files that came with one message (10-a)."""

    #: The files kept in the chat's folder.
    kept: list[Any] = field(default_factory=list)
    #: How many were not taken (refused, past five, no download).
    not_taken: int = 0
    #: The words heard in a voice note ("" when none).
    heard: str = ""
    #: The kept recording that was (or was to be) heard: its words ride the
    #: turn, never the recording itself.
    voice_path: str = ""
    #: The chat's folder (absolute), when a file was kept or it had one.
    folder: str = ""
    #: The saved chat the files belong to ("" with two-way chat off).
    thread_id: str = ""
    #: The receipt went out.
    sent: bool = False
    #: The message ends here (a question was asked, or nothing is left).
    stop: dict[str, Any] | None = None


@dataclass
class _PressHold:
    """10-b: an answer being resolved by a button press. ``_answer_reply``
    holds back the reply a changed button already says (``held``); the
    press's handler sends it after all when the button could not change."""

    held: list[str] = field(default_factory=list)


#: 10-b: set while a press resolves its prompt (``_handle_press``).
_PRESS_HOLD: ContextVar["_PressHold | None"] = ContextVar("ij_press_hold", default=None)
#: The answers whose reply the changed button says by itself.
_PRESS_QUIET = frozenset({"approval_answered", "approval_expired", "answer_superseded",
                          "file_sent", "file_gone",
                          # 12-a: "Starting" / "Not started" on the button say it.
                          "code_started", "code_not_now", "code_gone"})
#: The answers after which the buttons say what was decided.
_PRESS_DONE = frozenset({"approval_answered", "answered", "voice_answered", "voice_asked",
                         "file_sent", "file_send_failed", "file_kept",
                         "code_started", "code_queued", "code_not_now", "code_card"})
#: The answers after which the buttons say it was already answered.
_PRESS_GONE = frozenset({"approval_expired", "answer_superseded", "answer_expired", "file_gone",
                         "code_gone"})


def _with_files_note(text: str, count: int) -> str:
    """``text`` with :func:`files_not_read_note` after it when ``count`` > 0;
    ``text`` unchanged otherwise."""
    if not count:
        return text
    return f"{text}\n\n{files_not_read_note(count)}"


def _code_prompt_fresh(prompt: Any) -> bool:
    """12-a: a coding-work card (or folder pick) is answerable for
    ``phone_code.CARD_TTL_S`` after it was asked. Never raises (a doubt
    reads as stale: nothing starts from a card nobody can date)."""
    try:
        from ..harness.phone_code import CARD_TTL_S

        created = getattr(prompt, "created_at", None)
        if created is None:
            return False
        now = utcnow()
        if created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        return (now - created).total_seconds() <= CARD_TTL_S
    except Exception:  # noqa: BLE001
        return False


#: How long that question waits for "1" or "2".
_VOICE_ASK_TTL_S = 24 * 3600.0


def _voice_prompt_fresh(prompt: Any) -> bool:
    """The voice question is still answerable: under a day old and a
    recording it waits on (``ref_id``: their paths, one a line) still on this
    PC. Never raises (a doubt reads as stale)."""
    from .voice_note import waiting_paths

    try:
        if not any(Path(p).is_file() for p in waiting_paths(str(getattr(prompt, "ref_id", "") or ""))):
            return False
        created = getattr(prompt, "created_at", None)
        if created is None:
            return True
        now = utcnow()
        if created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        return (now - created).total_seconds() <= _VOICE_ASK_TTL_S
    except Exception:  # noqa: BLE001
        return False


def _not_taken(taken: "Taken | None") -> int:
    return int(taken.not_taken) if taken is not None else 0


def _run_task(text: str, taken: "Taken | None") -> str:
    """A run's task for a phone message (10-a): the words, the note for files
    not taken, and the kept files' absolute paths in a fenced data block
    (``comm/media.fenced_files``), so the run can open them and never reads
    them as instructions."""
    task = _with_files_note(text, _not_taken(taken))
    if taken is not None and taken.kept:
        from .media import fenced_files

        task = f"{task}\n\n{fenced_files([k.path for k in taken.kept])}"
    return task


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


def _sender_proven(msg: InboundMessage) -> bool:
    """False when the channel read the message but could not prove who sent it
    (``raw["sender_verified"] is False`` — the email channel: no aligned DKIM,
    DMARC or SPF pass in the receiving provider's Authentication-Results). Such
    a sender is refused even when the claimed address is on the allowlist."""
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    return raw.get("sender_verified") is not False


def _failure_words_for_phone(exc: HTTPException) -> str:
    """What the phone hears after "I hit a problem: " (v1.354.0 re-review,
    G28). A failed ANSWER carries the chat's plain words
    (``chat_turn.ChatTurnFailed.words``, from ``failure_words``): the phone
    gets the same sentence the chat line and a failed run's summary say —
    ``title. fix``, the join ``failure_words.plain_reason`` makes — never the
    server's raw text ("openrouter API error 401: …"), which the phone has no
    hover to fold away. Any other refusal (an unknown skill's 404, …) keeps
    its own ``detail``, exactly as before.

    v1.510.0 (second review, L4): a turn that failed AFTER its steps ran
    ends with the daemon's sentence of what it kept (``kept_note``, from
    ``chat_turn.failed_work_keys``: "Before it failed, this reply made
    notes.md. It is kept and can be undone on the Activity page."). Both
    phone doors (this poller and the desk reply into a phone chat) word a
    failure here. Never raises."""
    head = str(exc.detail)
    kept = ""
    try:
        words = getattr(exc, "words", None)
        if isinstance(words, dict):
            title = " ".join(str(words.get("title") or "").split())
            fix = " ".join(str(words.get("fix") or "").split())
            kept = " ".join(str(words.get("kept_note") or "").split())
            if title:
                head = f"{title}. {fix}" if fix else f"{title}."
    except Exception:  # noqa: BLE001 — wording only
        pass
    return f"{head} {kept}" if kept else head


def _run_problem(
    exc: BaseException, *, manager: Any = None, provider: str = "", started: bool = True
) -> str:
    """What the phone hears after "I hit a problem: " when a job it started
    FAILED (v1.378.0 review). A provider's SAFETY REFUSAL says the run's
    plain sentence (``failure_words.plain_reason``) — never the class name and
    the vendor's words ("ProviderRefusal: … stop_reason refusal …").

    Readiness 12A review (G28): EVERY failure does — the run's ONE rule
    (``failure_words.plain_reason``, what ``Orchestrator._finalize_failed``
    writes into the run's own summary), so the phone hears "Codex couldn't
    finish this task. Run it again. …" where it heard "ProviderError:
    codex-cli: CLI exited 1: error: unexpected status 400 …" — the phone has
    no hover to fold the raw text away (the run page keeps it, folded).
    *manager* / *provider* name the run's provider as the run's summary does.
    ``started`` (v1.420.0 review): False where NO run exists (a phone chat
    turn, a phone conversation with a remote) — the words then never say
    "open the run" (v1.388.0's rule for a failure before any run).
    Never raises."""
    try:
        from ..providers.failure_words import plain_reason, refusal_reason

        # A refusal first, as before: nothing of a JOB reached the phone, so
        # ``refusal_reason`` reads it as not partial ("The reply above is
        # incomplete" would be a lie here).
        return refusal_reason(exc, manager=manager) or plain_reason(
            exc, manager=manager, provider=provider, started=started
        )
    except Exception:  # noqa: BLE001 — wording only
        return "the task could not finish."


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
        #: R0.3: when each (channel, sender) was last told its files were not
        #: used. Written only for an allowlisted sender (after the guards in
        #: ``_dispatch``), so it is as small as the allowlists.
        self._files_told: dict[tuple[str, str], float] = {}
        #: 10-a: when each (channel, sender)'s recent files were taken
        #: (``FILES_PER_WINDOW``); allowlisted senders only, like the above.
        self._file_times: dict[tuple[str, str], deque[float]] = {}
        #: 10-a: the daemon's deps object, lent for phone voice notes
        #: (``_vosk_model_path``, ``_voice_backend``, ``_VOICE_MAX_BYTES``;
        #: ``comm/voice_note``). ``None``: a voice note is kept and the phone
        #: hears that it could not be heard here.
        self.voice_deps: Any = None
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
                "reason": "Iron Jarvis restarted while handling the message",
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
        """Wait for every tracked session to land (tests + graceful stops).
        Never spins on a task that finished just before (core/tasks)."""
        from ..core.tasks import drain_tasks

        await drain_tasks(self._session_tasks)

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
        ran = False
        try:
            if dyn_def is not None:
                done = await self._run_dynamic_session(session, dyn_def)
            else:
                done = await self.orchestrator.run_session(session.id)
            summary = (done.summary or "(no result)").strip()
            ran = str(getattr(getattr(done, "status", None), "value", getattr(done, "status", ""))) == "completed"
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
            summary = "I hit a problem: " + _run_problem(  # 12A review: the run's one rule
                exc,
                manager=getattr(getattr(self, "platform", None), "providers", None),
                provider=str(getattr(session, "provider", "") or ""),
            )
        # Readiness 0j.5 item 18: the reply NAMES the files the run wrote (or,
        # for a run that did not finish, the ones it kept) — names only,
        # never where on this PC they are; in Local only work by count
        # (10-b, D-10.3). Off the loop; never fails it.
        summary = await self._with_files_line(session.id, finished=ran, summary=summary, tid=tid)
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
            if ran:
                # 10-b: what the finished run made goes back after its words
                # (read off its own record, inside its folders, the phone's
                # caps), or the one question first. A run that did not
                # finish sends nothing: its words say which files it kept.
                from ..agents.reply_files import PHONE_CAPS, files_made

                engine = getattr(getattr(self, "platform", None), "engine", None)
                if engine is not None:
                    picked = await asyncio.to_thread(files_made, engine, str(session.id), PHONE_CAPS)
                    await self._files_back(name, ch, msg, picked, tid=tid,
                                           session_id=str(session.id), display=display)

    async def _with_files_line(self, session_id: str, *, finished: bool, summary: str, tid: str = "") -> str:
        """``summary`` with one line for the files the run wrote: " Files it
        wrote: a.md, b.xlsx." (a finished run) or " The files it wrote are
        kept: …" (one that did not finish), read off the run's ledger card
        (``agents/outcome``); unchanged when it wrote nothing, the card
        cannot be read, or the summary already says it. In Local only work
        (the chat's switch or the run's own, read strictly: a doubt is Local
        only) by count, never a name (10-b, D-10.3: the files question after
        it names none either), and a named line the run's own summary
        carries (the orchestrator's kept line) is put in counts too. Never
        raises (the summary as it was)."""
        try:
            from ..agents.outcome import kept_files_note, session_result, wrote_files_note
            from . import files_back

            engine = getattr(getattr(self, "platform", None), "engine", None)
            if engine is None:
                return summary
            result = await asyncio.to_thread(session_result, engine, str(session_id))
            line = (wrote_files_note(result) if finished else kept_files_note(result)).strip()
            if await asyncio.to_thread(files_back.work_is_local_only, engine, tid, str(session_id)):
                counted = files_back.local_only_files_note(result, finished=finished).strip()
                for named in {wrote_files_note(result).strip(), kept_files_note(result).strip()}:
                    if named and named in summary:
                        summary = summary.replace(named, counted)
                line = counted
            if not line or line in summary:
                return summary
            return f"{summary}\n\n{line}"
        except Exception:  # noqa: BLE001 — wording never fails a delivery
            return summary

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

    async def _say_receipt(
        self, name: str, ch: Channel, msg: InboundMessage, words: str, *, limited: bool
    ) -> bool:
        """Send the files' receipt to the sender (10-a; R0.3's sentence). True
        when it went out now; False when it failed, or when ``limited`` (it
        took nothing) and this sender was told less than
        ``FILES_NOT_READ_EVERY_S`` ago (a burst of refused files is told
        once). A receipt that kept a file or heard a voice note always goes.

        Called only from ``_dispatch`` after its guards. It is a reply about
        what the message carried, not a turn: no chat-thread line of its own
        (the user's line carries the files as chips), except when the message
        ends here on a voice note not heard yet (``_note_unheard`` puts both
        on the chat). Best-effort: a failed send is
        logged and never stops the message's words. File names are never
        logged."""
        if limited:
            key = (name, str(msg.sender_id))
            now = self._clock()
            last = self._files_told.get(key)
            if last is not None and now - last < FILES_NOT_READ_EVERY_S:
                return False
            self._files_told[key] = now
        try:
            return await self.send_chunked(ch, words, chat_id=msg.reply_to)
        except Exception:  # noqa: BLE001 — never lose the message over the receipt
            log.warning("inbound: could not send the files' receipt on %r", name, exc_info=True)
            return False

    async def _take_files(
        self, name: str, ch: Channel, msg: InboundMessage, *, chat_on: bool, words: str,
        hear_audio: bool,
    ) -> Taken:
        """Take the files that came with ``msg`` (10-a).

        Reached only from ``_dispatch`` AFTER the allowlist, the sender's
        proof and the private-chat guard: a stranger's, a bot's or a group's
        file is never downloaded. Each file is checked against the kinds
        taken and ``phone_file_max_mb`` BEFORE its download, fetched off the
        loop through the channel (which stops at the cap), kept in the
        chat's folder (``comm/media.keep_inbound``, off the loop) and
        recorded as ``comm.file_received {channel, kind, bytes}`` (never a
        name). At most five a message. A voice note is then heard
        (``comm/voice_note``): offline first, the chosen speech service only
        after a yes asked once, never off the network in a Local only chat.
        The receipt ("Saved photo.jpg in Documents\\Iron Jarvis\\... From
        your phone.", what was not taken and why, "I heard: ...") goes out
        before the answer. Never raises.

        Two questions, kept apart: ``words`` (all of the message's words, an
        e-mail's subject too) decides whether anything is left to answer, so
        words with a file that was not taken still run (R0.3);
        ``hear_audio`` (the sender wrote nothing of their own: an e-mail's
        body, without its automatic sign-off) decides whether an audio file
        is heard as a voice note."""
        from . import media
        from . import voice_note as VN

        out = Taken()
        cfg = getattr(self.platform, "config", None)
        cap = media.max_bytes(cfg)
        files = list(getattr(msg, "files", None) or [])
        lines: list[str] = []
        refused: list[str] = []
        sent_as: dict[str, str] = {}  # kept path -> the name the phone gave
        tid = ""
        resolved = False
        in_project = False
        for f in files[: media.PHONE_FILES_PER_MESSAGE]:
            why = media.refusal(f, cap) if cfg is not None else "files can't be kept here"
            if not why and not self._file_ok(name, msg.sender_id):
                why = "too many files came in the last minute. Send it again in a minute"
            if not why:
                try:
                    data = await asyncio.to_thread(ch.fetch_file, f, cap)
                    if chat_on and self.thread_store is not None and not resolved:
                        # The chat is found (or made) only once a file is in
                        # hand: a message whose files were all refused leaves
                        # no empty chat behind.
                        resolved = True
                        try:
                            thread = self.thread_store.resolve(
                                name, str(msg.sender_id), self._display(msg))
                            tid = thread.id
                            # A chat tagged to a project keeps its folder: the
                            # phone's files are not made its working folder.
                            in_project = bool(str(getattr(thread, "project_id", "") or ""))
                        except Exception:  # noqa: BLE001 — the file still goes to today's folder
                            log.warning("comm thread resolve failed on %r", name, exc_info=True)
                    # Every file of one message lands in the folder the first found.
                    kept = await asyncio.to_thread(
                        media.keep_inbound, self.platform, self.thread_store if tid else None,
                        tid, f.name, data, f.kind,
                        folder=out.folder or None, in_project=in_project,
                    )
                except FileNotTaken as exc:
                    why = exc.words
                except OSError:
                    why = "it could not be saved on this PC"
                except Exception as exc:  # noqa: BLE001 — a file never sinks the message
                    log.warning("inbound: a %s from %r could not be taken (%s)",
                                f.kind, name, type(exc).__name__)
                    why = "it could not be taken"
                else:
                    out.kept.append(kept)
                    sent_as[kept.path] = str(f.name or "")
                    out.folder = kept.folder
                    await self._publish(
                        EventType.COMM_FILE_RECEIVED,
                        {"channel": name, "kind": kept.kind, "bytes": kept.bytes},
                    )
                    continue
            refused.append(media.not_taken_line(f.name, why))
        extra = max(0, len(files) - media.PHONE_FILES_PER_MESSAGE)
        not_opened = int(getattr(msg, "files_not_read", 0) or 0)
        out.not_taken = len(refused) + extra + not_opened
        if out.kept:
            lines.append(await asyncio.to_thread(media.saved_line, out.kept, cfg))
        lines.extend(refused)
        if extra:
            lines.append(media.over_count_line(extra))
        if not_opened:
            lines.append(media.not_opened_line(not_opened))
        if out.not_taken:
            log.info("inbound: %d file(s) on channel %r were not taken", out.not_taken, name)
        # A VOICE NOTE becomes the sender's words (D-10.1); an audio file too
        # when the sender wrote nothing of their own (a voice memo sent as a
        # file; ``hear_audio`` reads an e-mail's body, not its subject).
        voice = next((k for k in out.kept if k.kind in VN.VOICE_KINDS), None) or (
            next((k for k in out.kept if k.kind == "audio"), None) if hear_audio else None
        )
        asked = False
        if voice is not None:
            # The recording is heard, not read: it never rides the turn as an
            # attachment (its words do), but it keeps its chip.
            out.voice_path = voice.path
            kept_in = await asyncio.to_thread(media.folder_words, voice.folder, cfg)
            heard = await self._hear(name, msg, voice, tid=tid, kept_in=kept_in)
            if heard.text:
                out.heard = heard.text
                lines.append(VN.heard_line(heard))
            elif heard.ask:
                waiting = self._ask_voice_service(name, msg, voice.path, heard.ask, tid)
                asked = waiting > 0
                lines.append(
                    VN.ask_words(heard.ask, heard.ask_label, waiting=waiting) if asked else
                    "I could not turn your voice note into words on this PC. "
                    f"The recording is kept in {kept_in}."
                )
            else:
                lines.append(heard.problem)
        if lines:
            out.sent = await self._say_receipt(
                name, ch, msg, "\n".join(lines),
                limited=not out.kept and not out.heard and not asked,
            )
        out.thread_id = tid
        if not out.heard:
            others = [k for k in out.kept if k.path != out.voice_path]
            if voice is not None and not others and not _beyond_the_recording(
                    words, sent_as.get(voice.path) or voice.name):
                # A voice note that was not heard, and nothing else: there is
                # nothing to answer until it is (the question, or the words).
                # A shared memo's e-mail subject that is only its name is no
                # request; any other words still run.
                # The phone chat on the PC still shows it: the recording as a
                # chip on the user's line, and what the phone was told (the
                # question, or why it was not heard) as the reply, so a later
                # "1" has its question above it and the recording is the
                # chat's file from now on.
                landed = self._note_unheard(name, msg, tid, out.kept, "\n".join(lines))
                out.stop = {"channel": name, "sent": out.sent, "thread_id": landed or tid,
                            "status": "voice_asked" if asked else "voice_not_heard"}
            elif not words and not out.kept:
                # No words and no file taken: the receipt was the answer.
                # Words (an e-mail's subject too) still run (R0.3).
                out.stop = {"channel": name, "status": "files_not_taken", "sent": out.sent}
        if not out.folder and tid:
            saved = self.thread_store.get_setup_value(tid, "workspace_dir")
            if saved:
                out.folder = saved
        return out

    def _note_unheard(
        self, name: str, msg: InboundMessage, tid: str, kept: list[Any], said: str
    ) -> str:
        """Put a voice note that was not heard (yet) on the phone chat (10-a):
        the user's line with the recording as a chip (``attachmentNames`` /
        ``attachmentPaths``, so ``attached_paths`` knows it), and ``said``,
        what the phone was told, as the reply. Only on a saved chat
        (``tid``: two-way chat on). Returns the chat it landed on ("" when
        none). Never raises."""
        if not tid or self.thread_store is None or not kept:
            return ""
        display = self._display(msg)
        chips = {"attachmentNames": [k.name for k in kept],
                 "attachmentPaths": [k.path for k in kept]}
        landed = self._safe_append(
            tid, "user", files_only_line([k.name for k in kept]),
            channel=name, sender_id=msg.sender_id, display=display, extra=chips,
        )
        if landed and said:
            self._safe_append(landed, "assistant", said,
                              channel=name, sender_id=msg.sender_id, display=display)
        return landed

    def _file_ok(self, channel: str, sender_id: Any) -> bool:
        """Take one more file from this sender? Records it and says True
        under ``FILES_PER_WINDOW`` files a ``RATE_WINDOW_SECONDS``, on the
        injected clock; False (recording nothing) past it."""
        now = self._clock()
        dq = self._file_times.setdefault((channel, str(sender_id)), deque())
        while dq and now - dq[0] >= RATE_WINDOW_SECONDS:
            dq.popleft()
        if len(dq) >= FILES_PER_WINDOW:
            return False
        dq.append(now)
        return True

    def _chat_files(
        self, tid: str, kept: list[Any], *, skip: str = "", in_project: bool = False
    ) -> dict[str, Any]:
        """``ChatBody`` fields for a phone chat's files (10-a), BLOCKING (a
        row read and a folder check): ``attachments`` (this message's kept
        files), ``workspace_dir`` (the chat's own folder once it has one,
        only when it sits under the chat files folder and exists) and
        ``thread_files`` (the files sent earlier in this chat). ``{}`` for a
        chat that never got a file. A chat tagged to a project keeps working
        in the project's folder (``in_project``): its files are still
        attachments, by their absolute paths."""
        from . import media

        out: dict[str, Any] = {}
        read = [k.path for k in kept if k.path != skip]
        if read:
            out["attachments"] = read
        if skip:
            # The recording itself is NAMED (its path, never extracted): its
            # words are the message, and "file this voice note" still finds it.
            out["thread_files"] = [skip]
        if not tid or self.thread_store is None:
            if kept:
                out["workspace_dir"] = kept[0].folder
            return out
        try:
            folder = self.thread_store.get_setup_value(tid, "workspace_dir")
            root = Path(self.platform.config.chat_files_dir)
            if (not in_project and folder and Path(folder).is_dir()
                    and media._inside(Path(folder), root)):
                out["workspace_dir"] = folder
            here = {k.path for k in kept}
            earlier = [p for p in self.thread_store.attached_paths(tid) if p not in here]
            if earlier:
                out["thread_files"] = earlier + out.get("thread_files", [])
        except Exception:  # noqa: BLE001 — the turn still runs on this message's files
            log.warning("inbound: the chat's files could not be read for %s", tid, exc_info=True)
        return out

    async def _hear(self, name: str, msg: InboundMessage, voice: Any, *, tid: str,
                    kept_in: str) -> Any:
        """Turn a kept voice note into words (``comm/voice_note``). The chat's
        Local only switch is read off the loop; an unreadable one counts as
        on. ``kept_in`` is the folder as the phone hears it. Never raises."""
        from . import voice_note as VN

        local_only = False
        if tid:
            try:
                from ..providers import egress as _egress

                local_only = await asyncio.to_thread(_egress.thread_local_only, self.engine, tid)
            except Exception:  # noqa: BLE001 — unreadable: the service is not reached
                local_only = True
        try:
            return await VN.transcribe_note(self.voice_deps, voice.path, local_only=local_only,
                                            kept_in=kept_in, mime=VN.mime_of(voice.name))
        except Exception:  # noqa: BLE001
            log.warning("inbound: a voice note on %r could not be heard", name, exc_info=True)
            return VN.Heard(problem="I could not turn your voice note into words. "
                                    f"The recording is kept in {kept_in}.")

    def _ask_voice_service(self, name: str, msg: InboundMessage, path: str, who: str,
                           tid: str) -> int:
        """Register the ONE question before a phone voice note goes to the
        chosen speech service (D-10.1): a pending prompt (Yes, No) on this
        sender's chat, answered by "1" / "2" like every phone question. An
        older unanswered one is closed first, and the recordings it waited on
        wait on this one (``ref_id``: their paths, oldest first, one a line,
        at most ``VN.MAX_WAITING``), so a yes hears every one. Returns how
        many recordings wait; 0 when no prompt store is wired or it failed
        (the phone then hears the note was not heard)."""
        from . import voice_note as VN

        if self.prompt_store is None:
            return 0
        try:
            older = self.prompt_store.expire_kind(VN.ASK_KIND, name, str(msg.sender_id))
            paths = [p for ref in older for p in VN.waiting_paths(ref) if p != path]
            paths = (paths + [path])[-VN.MAX_WAITING:]
            rec = self.prompt_store.register(
                VN.ASK_KIND, "\n".join(paths), VN.ask_question(who), ["Yes", "No"],
                name, str(msg.sender_id), tid,
            )
            return len(paths) if rec is not None else 0
        except Exception:  # noqa: BLE001
            log.warning("inbound: the voice question could not be registered on %r", name,
                        exc_info=True)
            return 0

    def _providers(self) -> Any:
        """The provider manager the failure words name a provider through
        (v1.420.0, C6.5), or None. Never raises."""
        return getattr(getattr(self, "platform", None), "providers", None)

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
            raw = msg.raw or {}
            # 10-b: a press names who pressed on the callback itself.
            m = raw.get("message") or raw.get("edited_message") or raw.get("callback_query") or {}
            frm = m.get("from") or {}
            name = " ".join(
                str(x) for x in (frm.get("first_name"), frm.get("last_name")) if x
            )
            return (name or str(frm.get("username") or "")).strip()[:120]
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def recap_task(history: "list[Any]", text: str) -> str:
        """The escalated-session task: a thread-tail recap block + the request.

        ``history`` is ChatBody-shaped ``[{role, content}]`` INCLUDING the
        current request as its last entry (exactly what the turn just ran on);
        the request is lifted out into ``Request:`` and the up-to-6 messages
        before it become the context block, each capped at 500 chars.

        Fourth plan G28 (DT1): also the task of a run the daemon starts for a
        chat reply that handed off with no window open
        (``daemon/chat_hand_off``) — the ONE recap helper, so an entry may be
        a chat's message as the body carries it (an object with ``role`` and
        ``content``) as well as a dict; anything else is left out.
        """

        def _get(m: Any, key: str) -> str:
            got = m.get(key) if isinstance(m, dict) else getattr(m, key, None)
            return str(got or "")

        tail = [m for m in list(history or [])[:-1] if _get(m, "content").strip()]
        tail = tail[-_RECAP_MESSAGES:]
        lines = [
            f"{'user' if _get(m, 'role') == 'user' else 'assistant'}: "
            f"{_get(m, 'content').strip()[:_RECAP_CHARS]}"
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
        # v1.333.0 (D9): a sender the allowlist refuses never mints a lock —
        # one lock per (channel, sender) is kept for the daemon's life, and a
        # spammed bot used to add one per stranger. `_dispatch` refuses it.
        if not ch.is_authorized(msg.sender_id) or not _sender_proven(msg):
            return await self._dispatch(name, ch, msg)
        async with self._identity_lock(name, msg.sender_id):
            return await self._dispatch(name, ch, msg)

    async def _dispatch(
        self, name: str, ch: Channel, msg: InboundMessage
    ) -> dict[str, Any]:
        """The body of :meth:`_handle`, under its identity lock."""

        # FAIL-CLOSED allowlist. An unauthorized sender spawns NOTHING.
        # Neither does one whose channel could not prove the sender (an email
        # whose From the receiving provider did not vouch for, v1.499.3): it
        # never reaches a command, the bare "approve", a rule or a session.
        if not ch.is_authorized(msg.sender_id) or not _sender_proven(msg):
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
        if (
            msg.reply_to is not None
            and str(msg.reply_to) != str(msg.sender_id)
            and not msg.own_channel
        ):
            log.warning("inbound: refusing non-private chat on channel %r", name)
            return {"channel": name, "status": "non_private", "sender": msg.sender_id}

        # 10-b: a BUTTON PRESS, read only here, after the allowlist, the
        # sender's proof and the private-chat guard: a stranger's press (or
        # one in a group) never resolves anything.
        if getattr(msg, "press", None) is not None:
            return await self._handle_press(name, ch, msg)

        text = (msg.text or "").strip()
        # FULL CHAT (v1.136.0): when this destination opted into chat
        # (``chat_enabled`` — implies inbound) AND the thread store + turn
        # service are wired, the conversation lives on a durable daemon-owned
        # thread: real chat turns with memory/skills/project spine, visible
        # live on the desktop. With chat OFF every path below stays
        # byte-equivalent to the one-shot behavior (pinned by tests).
        chat_on = self._chat_ready(ch)
        display = self._display(msg) if chat_on else ""

        # 10-a: PHOTOS, FILES AND VOICE NOTES. Taken HERE — after the
        # allowlist, the sender's proof and the private-chat guard, so a
        # stranger's, a bot's or a group's file is never downloaded — and
        # before anything else, so the receipt ("Saved photo.jpg in …", what
        # was not taken, "I heard: …") comes first. The words heard in a voice
        # note are the sender's words. A message with nothing left to answer
        # ends here (R0.3's "files_not_read" became "files_not_taken").
        taken: Taken | None = None
        if getattr(msg, "files", None) or getattr(msg, "files_not_read", 0):
            # The sender's OWN words decide whether a recording is heard: an
            # e-mail's body without its automatic sign-off, never its subject
            # (a shared voice memo's subject is the recording's name, and
            # almost every e-mail has one). Whether anything is left to answer
            # is read from ALL the words, the subject too (R0.3).
            own = getattr(msg, "own_words", None)
            mine = str(own).strip() if own is not None else text
            taken = await self._take_files(name, ch, msg, chat_on=chat_on, words=text,
                                           hear_audio=not mine)
            if taken.stop is not None:
                return taken.stop
            if taken.heard:
                text = f"{text}\n\n{taken.heard}" if text else taken.heard
        if not text and not (taken and taken.kept):
            return {"channel": name, "status": "empty"}
        if not text:
            # Files and no words: the turn still runs on them (a file alone
            # is a message, as on the desk), and its words say so.
            text = files_only_line([k.name for k in taken.kept])

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

        # NEXT LEAPS 12-a: "/code <folder>: <what to do>" starts coding work
        # (one card, Start and Not now). Handled HERE, beside "/answer": the
        # card is registered for THIS sender, whom only this door knows (the
        # command grammar sees text alone).
        if self.prompt_store is not None and self.platform is not None and (
            low == "/code" or low.startswith("/code ")
        ):
            return await self._handle_code_command(name, ch, msg, text, display, chat_on)

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
            prompt, gone_status = await self._open_prompt(name, msg.sender_id)
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
                    if gone_status in ("resuming", "running", "completed", "completed_with_failures")
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
            prompt, gone_status = await self._open_prompt(name, msg.sender_id)
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
                # Leap 05-a: through the start hub (the one entry for every
                # kind of start), which calls on_signal exactly as before.
                from ..triggers.starts import hub_for as _hub_for

                fired = await _hub_for(self.reflex_router).signal(
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
            return await self._handle_chat(name, ch, msg, text, display, taken=taken)

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
        # LOW TRUST (v1.298.0): a run the phone started may read and answer,
        # not change what the next run reads (``_comm_trust``).
        _trust, _trust_reason = self._comm_trust(name)
        from ..agents.orchestrator import EmergencyStopOn

        _stop_why = self._emergency_stop_reason(name)
        session = None
        if not _stop_why:
            try:
                # R0.3 review: the run is told files came and were not read,
                # so it never answers about files it did not see. 10-a: the
                # files that WERE kept are named in a fenced data block (their
                # absolute paths; never instructions), and the run works in
                # their folder so what it makes lands beside them.
                session = await self.orchestrator.create_session(
                    _run_task(text, taken), self.agent_type, origin=f"comm:{name}",
                    trust=_trust, trust_reason=_trust_reason,
                    **({"workspace_root": taken.folder} if taken and taken.kept else {}),
                )
            except EmergencyStopOn as exc:  # the switch flipped since the read
                _stop_why = str(exc)
        if session is None:
            # EMERGENCY STOP (v1.351.0, G13): the phone hears why, in the
            # switch's own words — never silence.
            body = f"{self.reply_prefix}{_stop_why}"[: self.max_reply_chars]
            send_res = await asyncio.to_thread(ch.send, body, chat_id=msg.reply_to)
            return {
                "channel": name,
                "status": "emergency_stop",
                "reason": _stop_why,
                "sent": bool(send_res.get("ok")),
            }
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
        self, name: str, ch: Channel, msg: InboundMessage, text: str, display: str,
        *, taken: "Taken | None" = None,
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

        # R0.3 review: when files came with the words, the saved line and so
        # the turn (and a remote agent, and an escalated run) say so after
        # them, in plain words: the model must not guess at files it never
        # saw. Who the message is for is still read from the words alone.
        said = _with_files_note(text, _not_taken(taken))
        # 10-a: the files that were kept ride the user's line as the desk's
        # own chip fields, so the phone chat on the PC shows them (absolute
        # paths stay on this PC; the phone heard names only).
        kept = list(taken.kept) if taken is not None else []
        chips = (
            {"attachmentNames": [k.name for k in kept], "attachmentPaths": [k.path for k in kept]}
            if kept else None
        )
        tid = self._safe_append(
            thread.id, "user", said,
            channel=name, sender_id=msg.sender_id, display=display, extra=chips,
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
                name, ch, msg, thread, tid, said, display, remote_name
            )
        # v1.506.0 (north-star 6.8): the WHOLE stored thread; the chat turn's
        # planner fits it to the model's window (never a fixed slice here).
        history = self.thread_store.history_body(tid) if tid else []
        if not history:
            # The append could not land (or the read hiccuped): the turn still
            # runs on the bare message — the phone gets its answer regardless.
            history = [{"role": "user", "content": said}]

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
        # v1.515.0 (second review L1): the turn is told WHICH saved chat it
        # answers, so its usage row is the chat's (``GET /chat/threads/{id}/
        # usage``), the chat reads running while it runs, and its notes are
        # keyed and recalled as ``chat:<id>`` (``memory/origin.chat_tag``),
        # never under the one "chat" key every phone conversation shared.
        # The lane only READS the id: this door still saves the reply itself
        # (below), and the plain lane keeps no page-owned turn record.
        # 10-a: the files from the phone enter through the ATTACHMENT SEAM (read
        # on this PC first, scanned and fenced as data, never the user's
        # words); the chat's folder is the turn's working folder, so what it
        # makes lands beside them; the chat's earlier files are named
        # (``thread_files``) so "now turn that into a memo" finds them.
        files_here = await asyncio.to_thread(
            self._chat_files, tid, kept, skip=(taken.voice_path if taken is not None else ""),
            in_project=bool(str(getattr(thread, "project_id", "") or "")),
        )
        body = ChatBody(messages=history, auto_tools=True, project_id=project_id, thread_id=tid or "",
                        **files_here)
        # NEXT LEAPS 12-a: the sender THIS door proved, for the chat's
        # ``code_start`` (it asks only them, and only from here); the cards it
        # asked go out after the reply.
        who = await self._asking_for(name, msg, display, True, tid=tid)
        from ..harness.phone_code import reset_asking, set_asking

        asking_token = set_asking(who)
        try:
            # LOW TRUST (v1.298.0): the lane drops its auto-armed memory
            # writers for a turn the phone started (``_chat_turn_kwargs``).
            # v1.378.0 review: a failure is worded for the PHONE (a safety
            # refusal's way out here is "/new", never Edit or a new-chat
            # press the phone does not have).
            from ..providers.failure_words import phone_surface

            with phone_surface():
                result = await self.chat_turn(
                    self.platform, self.personas, body, **self._chat_turn_kwargs(name)
                )
        except HTTPException as exc:
            reset_asking(asking_token)
            reply = f"I hit a problem: {_failure_words_for_phone(exc)}"
            if tid:
                tid = self._safe_append(
                    tid, "assistant", reply,
                    channel=name, sender_id=msg.sender_id, display=display,
                )
            sent = await self.send_chunked(ch, reply, chat_id=msg.reply_to)
            await self._send_asked_cards(name, ch, msg, who, tid=tid, display=display)
            return {"channel": name, "status": "chat_error", "thread_id": tid, "sent": sent}
        except Exception as exc:  # noqa: BLE001 — the loop must reply, not die
            reset_asking(asking_token)
            log.exception("chat turn failed on %r", name)
            # v1.420.0 (C6.5): the run's plain sentence, never a class name.
            reply = f"I hit a problem: {_run_problem(exc, manager=self._providers(), started=False)}"
            if tid:
                tid = self._safe_append(
                    tid, "assistant", reply,
                    channel=name, sender_id=msg.sender_id, display=display,
                )
            sent = await self.send_chunked(ch, reply, chat_id=msg.reply_to)
            await self._send_asked_cards(name, ch, msg, who, tid=tid, display=display)
            return {"channel": name, "status": "chat_error", "thread_id": tid, "sent": sent}
        reset_asking(asking_token)

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
            # v1.549.0 (idea 30-a): the saved chat keeps a figure's link to
            # where it was read; the phone gets the words only.
            from ..documents.sources import without_figure_links

            # 10-b: the files this turn made go back after its words: its own
            # record (``documents``) inside the folder its tools worked in,
            # within the phone's caps, or the one question first. The lane
            # answering the desk changes nothing: this door alone sends.
            picked = await asyncio.to_thread(
                self._turn_files, str(files_here.get("workspace_dir") or ""), project_id,
                result.get("documents"),
            )
            # A files question about to be asked takes in an open one: that
            # one is not announced first (10-b review).
            asks = await self._files_will_ask(name, ch, picked, tid=tid)
            # 12-a: a card this turn asked follows the reply; it is not
            # announced as "still waiting" first.
            outbound = without_figure_links(reply) + await self._reminder(
                name, msg.sender_id, skip_files=asks, skip_code=bool(who.cards))
            sent = await self.send_chunked(ch, outbound, chat_id=msg.reply_to)
            files = await self._files_back(name, ch, msg, picked, tid=tid, display=display)
            cards = await self._send_asked_cards(name, ch, msg, who, tid=tid, display=display)
            return {"channel": name, "status": "chat", "thread_id": tid, "sent": sent,
                    "files": files.get("files", "none"), "cards": cards}

        # ESCALATE: ack now, run the normal supervised session (same
        # orchestrator + permission engine — a remote sender gains no power),
        # then deliver the summary here AND onto the thread.
        # v1.139.0 informed delegation: a turn that NAMED who should take it
        # (``escalate_agent``) overrides the hard-coded supervisor default —
        # re-validated through the roster here; None keeps the default
        # byte-for-byte (see ``_escalate_plan``).
        task = self.recap_task(history, said)
        if kept:
            # 10-a: the escalated run is handed the kept files as data too.
            from .media import fenced_files

            task = f"{task}\n\n{fenced_files([k.path for k in kept])}"
        # THE DAY OFF / THE ALLOWANCE (v1.295.0): a paused custom agent is
        # ``healthy=False`` on the roster, so ``_escalate_plan`` would resolve
        # nobody and run the SUPERVISOR silently; an exhausted one would run.
        # Refuse in the agent's own words BEFORE anything is created, and tell
        # the phone — the ledger read runs off the loop.
        refusal = await asyncio.to_thread(self._escalate_refusal, result)
        if not refusal:
            # EMERGENCY STOP (v1.351.0, G13): refused BEFORE the "On it" ack,
            # in the same honest shape as the allowance refusal above.
            refusal = self._emergency_stop_reason(name)
        if refusal:
            if tid:
                tid = self._safe_append(
                    tid, "assistant", refusal,
                    channel=name, sender_id=msg.sender_id, display=display,
                ) or tid
            sent = await self.send_chunked(ch, refusal, chat_id=msg.reply_to)
            await self._send_asked_cards(name, ch, msg, who, tid=tid, display=display)
            return {
                "channel": name,
                "status": "chat_refused",
                "thread_id": tid,
                "reason": refusal,
                "sent": sent,
            }
        agent_type, dyn_def, esc_provider, esc_model = self._escalate_plan(result)
        if tid:
            tid = self._safe_append(
                tid, "assistant", ESCALATE_ACK,
                channel=name, sender_id=msg.sender_id, display=display,
            ) or tid
        sent = await self.send_chunked(ch, ESCALATE_ACK, chat_id=msg.reply_to)
        await self._send_asked_cards(name, ch, msg, who, tid=tid, display=display)
        _spawn_kwargs: dict[str, Any] = {}
        if esc_provider:
            _spawn_kwargs["provider"] = esc_provider
        if esc_model:
            _spawn_kwargs["model"] = esc_model
        if kept and taken is not None and taken.folder and not project_id:
            # 10-a: the run works in the chat's folder, beside the files (a
            # chat tagged to a project keeps the project's folder).
            _spawn_kwargs["workspace_root"] = taken.folder
        if dyn_def is not None:
            # WHO ran (v1.193.0 / v1.295.0): the phone is a door that KNOWS the
            # roster name, so it stamps it — the allowance ledger sums Session
            # rows by this name, and un-stamped rows credit the base type.
            try:
                from ..agents.roster import canonical_roster_name

                _spawn_kwargs["agent_name"] = canonical_roster_name(
                    self.platform, str((result or {}).get("escalate_agent") or "")
                )
            except Exception:  # noqa: BLE001 — attribution is a bonus, never a block
                pass
        # The escalated session inherits the thread's project tag (the same
        # kwarg the dashboard passes when escalating desktop chat), so the
        # run gets the project's brief/knowledge/recent-activity spine.
        # LOW TRUST (v1.298.0): the escalated run is still a run the phone
        # started — same posture as the one-shot door (``_comm_trust``).
        _trust, _trust_reason = self._comm_trust(name)
        from ..agents.orchestrator import EmergencyStopOn

        try:
            session = await self.orchestrator.create_session(
                task,
                agent_type,
                project_id=project_id or None,
                origin=f"comm:{name}",  # v1.231.0 (AE17): may ask back via the phone
                trust=_trust,
                trust_reason=_trust_reason,
                **_spawn_kwargs,
            )
        except EmergencyStopOn as exc:
            # The switch flipped between the read above and this start: the
            # ack already went, so the phone hears the refusal too.
            why = str(exc)
            if tid:
                tid = self._safe_append(
                    tid, "assistant", why,
                    channel=name, sender_id=msg.sender_id, display=display,
                ) or tid
            await self.send_chunked(ch, why, chat_id=msg.reply_to)
            return {
                "channel": name,
                "status": "chat_refused",
                "thread_id": tid,
                "reason": why,
                "sent": sent,
            }
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
            # Build-out R0.1 review: the rows older than the tail are counted,
            # so another Iron Jarvis says how many were left out, truly.
            rows, earlier = (
                self.thread_store.history_window(tid, limit=_REMOTE_CHAT_ROWS)
                if tid
                else ([], {})
            )
            # The user's line was appended above — it is the MESSAGE, not history.
            if rows and rows[-1].get("who") == "user" and rows[-1].get("content") == text:
                rows = rows[:-1]
            shim = SimpleNamespace(platform=self.platform)
            out = await threads.run_round(
                room.id,
                text,
                shim,
                directed=[remote_name],
                chat_history=rows,
                **({"chat_earlier": earlier} if earlier else {}),
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
            reply = f"{remote_name} couldn't answer: {_run_problem(exc, manager=self._providers(), started=False)}"
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

    def _escalate_refusal(self, result: dict[str, Any]) -> str:
        """The allowance door for a phone escalation (v1.295.0): ``""`` when
        the named agent (``custom:<slug>`` or a bare slug that IS a custom
        record) may take work, else ``agents.allowance.refusal_for``'s one
        sentence. Synchronous (a ledger read) — the caller hops to a thread.
        Never raises: a broken read refuses nothing, the normal path decides."""
        try:
            name = str((result or {}).get("escalate_agent") or "").strip()
            if not name or self.platform is None:
                return ""
            low = name.lower()
            if low.startswith("remote:"):
                return ""
            slug = (name.split(":", 1)[1] if low.startswith("custom:") else name).strip()
            registry = getattr(self.platform, "agents_registry", None)
            if registry is None or not slug:
                return ""
            rec = registry.get(slug)
            if rec is None:
                return ""
            from ..agents import allowance

            return allowance.refusal_for(rec, self.platform.engine)
        except Exception:  # noqa: BLE001 — the door must never break the phone
            return ""

    def _emergency_stop_reason(self, name: str) -> str:
        """EMERGENCY STOP (v1.351.0, G13): the sentence the phone hears when
        the switch refuses a ``comm:<name>`` run, or ``""``. Read BEFORE any
        ack is sent, so the phone never hears "On it" and then nothing —
        before this, ``create_session``'s refusal escaped to ``poll_once``'s
        generic handler and was only logged. Never raises."""
        stop = getattr(self.orchestrator, "emergency_stop_reason", None)
        if not callable(stop):
            return ""
        try:
            return str(stop(f"comm:{name}") or "")
        except Exception:  # noqa: BLE001 — an unreadable switch reads as off
            return ""

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
        # The run's spend lands on ITS row (v1.295.0), exactly as spawn_agent
        # and run_session stamp it — the allowance ledger sums Session tokens.
        session.input_tokens = run.input_tokens
        session.output_tokens = run.output_tokens
        # v1.300.0: and its DOLLARS — the runtime already added each step to
        # the session object it ran with; the run's figure covers a row
        # loaded separately. The larger of the two, never a sum (no double count).
        session.cost_usd = max(
            float(getattr(session, "cost_usd", 0.0) or 0.0),
            float(getattr(run, "cost_usd", 0.0) or 0.0),
        )
        session.finished_at = utcnow()
        self.orchestrator._save(session)
        # The allowance tail (v1.295.0): an exhausted custom agent is paused
        # here, so the next phone escalation is refused instead of run.
        try:
            await self.orchestrator._post_run_allowance(session)
        except Exception:  # noqa: BLE001 — the tail never breaks a delivery
            log.exception("allowance tail failed for phone session %s", session.id)
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

    async def _open_prompt(self, channel: str, sender_id: Any) -> tuple[Any, str]:
        """:meth:`_fresh_open_prompt_ex` OFF the event loop (10-b review):
        it reads rows and, for a voice or files question, stats the files on
        disk, which may sit on a network share or a cloud folder not yet on
        this PC. Every async door uses this, never the sync look."""
        return await asyncio.to_thread(self._fresh_open_prompt_ex, channel, sender_id)

    async def _reminder(self, channel: str, sender_id: Any, *, skip_files: bool = False,
                        skip_code: bool = False) -> str:
        """:meth:`_pending_reminder` off the event loop (see :meth:`_open_prompt`)."""
        return await asyncio.to_thread(self._pending_reminder, channel, sender_id, skip_files=skip_files,
                                       skip_code=skip_code)

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
            fresh, gone = self._prompt_fresh(prompt)
            return (prompt, "") if fresh else (None, gone)
        except Exception:  # noqa: BLE001 — a prompt lookup must never break a turn
            log.warning("pending prompt lookup failed on %r", channel, exc_info=True)
            return None, ""

    def _prompt_fresh(self, prompt: Any) -> tuple[bool, str]:
        """Is the open ``prompt`` still answerable? ``(True, "")``, or
        ``(False, <why>)`` after EXPIRING it (an approval past its window:
        "approval_gone"; a workflow run that un-parked: its status; a voice
        or files question past its day or whose files are gone: ""). Shared
        by the newest-open look and a button's press (10-b)."""
        kind = getattr(prompt, "kind", "")
        if kind == APPROVAL_KIND:
            # An approval pause has a HARD deadline (the runtime denies at
            # SESSION_APPROVAL_TIMEOUT_S) and approval.resolved normally
            # expires the row (prompts.handle_workflow_waiting dispatch);
            # this age check is the belt for a missed event, so a dead
            # gate is never nagged about or "answered" forever. The
            # registry's resolve() stays the real arbiter — a failed
            # resolve expires the prompt too (_resolve_approval_prompt).
            if self._approval_prompt_stale(prompt):
                self.prompt_store.expire(prompt.id, status="expired")
                return False, "approval_gone"
            return True, ""
        if kind == _VOICE_ASK_KIND:
            # 10-a: "use <service> for phone voice notes too?" waits for
            # the recording it is about; a day later, or once the
            # recording is gone, it is closed (a later "1" is chat).
            if not _voice_prompt_fresh(prompt):
                self.prompt_store.expire(prompt.id, status="expired")
                return False, ""
            return True, ""
        if kind == FILE_SEND_KIND:
            # 10-b: "send the files to your phone?" waits a day, and only
            # while one of its files is still on this PC.
            from . import files_back

            if not files_back.prompt_fresh(prompt):
                self.prompt_store.expire(prompt.id, status="expired")
                return False, ""
            return True, ""
        if kind in (CODE_START_KIND, CODE_FOLDER_KIND):
            # 12-a: a card (or a folder pick) waits an hour; after that a
            # "1" is chat, and nothing starts from an old card.
            if not _code_prompt_fresh(prompt):
                self.prompt_store.expire(prompt.id, status="expired")
                return False, ""
            return True, ""
        status = self._run_state(prompt.ref_id)
        if status != "waiting":
            self.prompt_store.expire(prompt.id, status="expired")
            return False, status
        return True, ""

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
        arbiter, and a flaky timestamp must not expire a live gate.

        v1.398.0 (night shift N1): a gate the approval registry still holds
        OPEN is never stale, whatever its age. A coding session's card has no
        expiry (``timeout_s`` 0) and reaches the phone five minutes after it
        was asked; the age check below (the runtime's 300 s) refused a phone
        answer that came more than five minutes after the question ARRIVED
        ("approval_gone") while the card was still waiting — a night task's
        card waits fifteen. The age check stays the belt for a gate whose
        end the registry no longer knows (a missed ``approval.resolved``)."""
        if self._approval_still_open(prompt):
            return False
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

    def _approval_still_open(self, prompt: Any) -> bool:
        """Does the approval registry (``platform.approvals``) still hold the
        prompt's gate open? False on any doubt (the age check then decides,
        as before). Never raises."""
        try:
            approvals = getattr(self.platform, "approvals", None)
            if approvals is None:
                return False
            ref = str(getattr(prompt, "ref_id", "") or "")
            return bool(ref) and ref in set(approvals.pending_ids())
        except Exception:  # noqa: BLE001
            return False

    def _pending_reminder(self, channel: str, sender_id: Any, *, skip_files: bool = False,
                          skip_code: bool = False) -> str:
        """The '(… still waiting …)' suffix for the outbound phone copy of a
        normal chat reply — '' when nothing fresh is open. Kind-aware: an
        approval gate must not be announced in a workflow's words.
        ``skip_files`` (10-b review): this reply's own files are about to be
        asked about, and that question takes in the open files question's
        files and closes it, so the open one is not announced first.
        ``skip_code`` (12-a): the card this reply's turn asked follows it."""
        prompt = self._fresh_open_prompt(channel, sender_id)
        if prompt is None:
            return ""
        if skip_files and getattr(prompt, "kind", "") == FILE_SEND_KIND:
            return ""
        if skip_code and getattr(prompt, "kind", "") in (CODE_START_KIND, CODE_FOLDER_KIND):
            return ""
        if getattr(prompt, "kind", "") == APPROVAL_KIND:
            return approval_reminder(prompt.question)
        if getattr(prompt, "kind", "") == _VOICE_ASK_KIND:
            return f"\n\n(Still waiting for your answer: {prompt.question} Reply 1 for yes or 2 for no.)"
        if getattr(prompt, "kind", "") == FILE_SEND_KIND:
            from . import files_back

            paths, _local, _left = files_back.read_ref(getattr(prompt, "ref_id", ""))
            return (f"\n\n(Still waiting for your answer: {prompt.question} "
                    f"{files_back.answer_hint(len(paths) or 1)})")
        if getattr(prompt, "kind", "") in (CODE_START_KIND, CODE_FOLDER_KIND):
            from ..harness.phone_code import answer_hint as _code_hint

            return f"\n\n(Still waiting for your answer: {prompt.question} {_code_hint(prompt.kind)})"
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
        hold = _PRESS_HOLD.get()
        if hold is not None and status in _PRESS_QUIET and not outbound_suffix:
            # 10-b: answered by a button press: the button turns into what
            # was decided, and that IS the reply (one notice per answer).
            # The press's handler sends this after all if the button could
            # not change.
            hold.held.append(reply)
            sent = True
        else:
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
        prompt, _gone = await self._open_prompt(name, msg.sender_id)
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
        if getattr(prompt, "kind", "") == _VOICE_ASK_KIND:
            return await self._resolve_voice_prompt(
                name, ch, msg, display, chat_on, prompt, answer, original_text
            )
        if getattr(prompt, "kind", "") == FILE_SEND_KIND:
            return await self._resolve_file_prompt(
                name, ch, msg, display, chat_on, prompt, answer, original_text
            )
        if getattr(prompt, "kind", "") in (CODE_START_KIND, CODE_FOLDER_KIND):
            return await self._resolve_code_prompt(
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
                f"I hit a problem: {_run_problem(exc, manager=self._providers())}",
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
                outbound_suffix=await self._reminder(name, msg.sender_id),
                prompt_id=prompt.id, run_id=prompt.ref_id,
            )
        # Claim lost: first answer wins, and it wasn't this one.
        self.prompt_store.expire(prompt.id, status="superseded")
        return await self._answer_reply(
            name, ch, msg, display, chat_on, original_text,
            ALREADY_ANSWERED_REPLY, "answer_superseded",
            outbound_suffix=await self._reminder(name, msg.sender_id),
            prompt_id=prompt.id, run_id=prompt.ref_id,
        )

    async def _resolve_voice_prompt(
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
        """The answer to "Use <service> for phone voice notes too?" (10-a,
        D-10.1). Yes or no is kept as ``phone_voice_service`` through THE
        settings writer (a ledger row with Undo; Settings › Voice notes from
        your phone). On yes the waiting recording goes to the service now
        (still never off the network in a Local only chat), what was heard is
        echoed, and the words are handled like a message the sender typed."""
        from . import media
        from . import voice_note as VN

        said = str(answer or "").strip().lower()
        decision = "yes" if said in VN.YES_WORDS else "no" if said in VN.NO_WORDS else ""
        if not decision:
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                "Reply 1 for yes or 2 for no.", "answer_usage",
            )
        # Every recording that waits on this answer, oldest first (a second
        # voice note sent before the answer waits too).
        paths = VN.waiting_paths(str(getattr(prompt, "ref_id", "") or ""))
        path = paths[-1] if paths else ""
        tid = str(getattr(prompt, "thread_id", "") or "")
        cfg = getattr(self.platform, "config", None)
        kept_in = await asyncio.to_thread(media.folder_words, Path(path).parent, cfg)
        where = "Settings, Voice notes from your phone"
        kept_words = ("The recording is kept in" if len(paths) <= 1
                      else f"The {len(paths)} recordings are kept in")
        if decision == "no":
            await asyncio.to_thread(self._keep_voice_choice, decision)
            self.prompt_store.resolve(prompt.id, decision)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                f"OK: phone voice notes stay on this PC ({where} changes it). "
                f"{kept_words} {kept_in}.",
                "voice_answered",
            )
        # D-10.1: the yes is for the service the question NAMED. When Settings
        # names another one now, it is not consent for that one: ask again
        # (the older question is closed), and keep nothing.
        svc = await VN.current_service(self.voice_deps)
        if svc is None:
            self.prompt_store.resolve(prompt.id, decision)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                f"No speech service is chosen in Settings any more. {kept_words} {kept_in}.",
                "voice_answered",
            )
        if VN.ask_question(svc.who) != str(getattr(prompt, "question", "") or ""):
            # This open question is closed by the new one, and its
            # recordings wait on that one.
            waiting = self._ask_voice_service(name, msg, path, svc.who, tid)
            if not waiting:
                self.prompt_store.resolve(prompt.id, decision)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                VN.changed_words(svc.who, svc.label, waiting=waiting) if waiting else
                "The speech service in Settings changed since I asked, so nothing was sent. "
                f"{kept_words} {kept_in}.",
                "voice_asked",
            )
        await asyncio.to_thread(self._keep_voice_choice, decision, svc.key)
        self.prompt_store.resolve(prompt.id, decision)
        local_only = False
        if tid:
            try:
                from ..providers import egress as _egress

                local_only = await asyncio.to_thread(_egress.thread_local_only, self.engine, tid)
            except Exception:  # noqa: BLE001 — unreadable: never off the network
                local_only = True
        # Every waiting recording, oldest first, in the order it was sent.
        heards = []
        for p in paths:
            heards.append(await VN.transcribe_with_service(
                self.voice_deps, p, local_only=local_only, kept_in=kept_in, mime=VN.mime_of(p),
                service=svc,
            ))
        head = f"OK: phone voice notes use {svc.who} from now on ({where} changes it)."
        said = [h.text for h in heards if h.text]
        if not said:
            problems = " ".join(dict.fromkeys(h.problem for h in heards if h.problem))
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                f"{head} {problems}".strip(), "voice_answered",
            )
        echo = "\n".join(VN.heard_line(h) if h.text else h.problem for h in heards)
        await self._answer_reply(
            name, ch, msg, display, chat_on, original_text,
            f"{head}\n{echo}", "voice_answered",
        )
        # The words heard are the sender's own: handled as if typed, with the
        # same guards (this sender, this chat) the answer itself passed.
        words = InboundMessage(
            sender_id=msg.sender_id, text="\n\n".join(said), update_id=None, reply_to=msg.reply_to,
            is_bot=False, raw=msg.raw, own_channel=msg.own_channel,
        )
        return await self._dispatch(name, ch, words)

    def _keep_voice_choice(self, decision: str, service: str = "") -> None:
        """Keep the phone's yes or no (BLOCKING): through the settings writer
        when the daemon has one (a ledger row with Undo), else on the config
        (a bare platform, as in tests). A yes is kept WITH the service it was
        asked for (``service``, ``voice_note.service_key``), written first: a
        yes is never left following whatever service comes next because that
        record could not be written. Never raises."""
        try:
            cfg = getattr(self.platform, "config", None)
            if decision == "yes" and cfg is not None:
                from ..core.config import persist_config_values

                cfg.phone_voice_service_for = service
                home = getattr(cfg, "home", None)
                if home is not None:
                    persist_config_values(home, {"phone_voice_service_for": service})
            writer = getattr(self.platform, "config_writer", None)
            if writer is not None:
                writer.apply({"phone_voice_service": decision}, actor="phone")
                return
            cfg = getattr(self.platform, "config", None)
            if cfg is not None:
                cfg.phone_voice_service = decision
        except Exception:  # noqa: BLE001 — the answer still stands for this recording
            log.warning("inbound: the phone's voice-note answer could not be kept", exc_info=True)

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
            # v1.402.0 (the autonomy metrics): the phone answered it.
            from ..core.approvals import resolve_as as _resolve_as

            ok = bool(_resolve_as(approvals, prompt.ref_id, decision, actor="phone"))
        except Exception as exc:  # noqa: BLE001 — the loop must reply, not die
            log.exception("approval resolve failed on %r", name)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                f"I hit a problem: {_run_problem(exc, manager=self._providers())}",
                "answer_error",
            )
        if ok:
            self.prompt_store.resolve(prompt.id, decision)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                approval_echo(decision), "approval_answered",
                # Another gate may still be open — surface it on the phone
                # copy, same rule as the workflow tail above.
                outbound_suffix=await self._reminder(name, msg.sender_id),
                prompt_id=prompt.id, approval_id=prompt.ref_id,
                decision=decision,
            )
        # resolve() said no: unknown, expired, or already answered. The
        # registry is the arbiter — close the row and say what happened.
        self.prompt_store.expire(prompt.id, status="expired")
        return await self._answer_reply(
            name, ch, msg, display, chat_on, original_text,
            APPROVAL_GONE_REPLY, "approval_expired",
            outbound_suffix=await self._reminder(name, msg.sender_id),
            prompt_id=prompt.id, approval_id=prompt.ref_id,
        )

    # -- 10-b: buttons -------------------------------------------------------
    def _pressed_prompt(self, channel: str, sender_id: Any, parsed: tuple[str, str, int]) -> tuple[Any, str]:
        """The open prompt a button names, for THIS presser, or ``(None,
        words)`` (BLOCKING: indexed reads). ``ij:p:<id>`` names one prompt,
        which must be open, on this destination and the presser's own;
        ``ij:a:<approval id>`` names an approval, answered through the
        presser's own open prompt for it. A stale one is expired here
        (:meth:`_prompt_fresh`). Never raises."""
        kind, ref, _n = parsed
        try:
            if kind == "p":
                rec = self.prompt_store.get(ref)
                if (rec is None or rec.status != "open" or rec.channel != channel
                        or str(rec.sender_id) != str(sender_id)):
                    return None, PRESS_ANSWERED
            else:
                rec = self.prompt_store.open_for_ref(APPROVAL_KIND, ref, channel, str(sender_id))
                if rec is None:
                    # Still waiting, but this chat holds no answerable prompt
                    # for it (it was asked before this sender talked here).
                    approvals = getattr(self.platform, "approvals", None)
                    waiting = approvals is not None and ref in set(approvals.pending_ids())
                    return None, PRESS_ELSEWHERE if waiting else PRESS_ANSWERED
            fresh, _gone = self._prompt_fresh(rec)
            return (rec, "") if fresh else (None, PRESS_ANSWERED)
        except Exception:  # noqa: BLE001 — a doubt resolves nothing
            log.warning("inbound: a pressed button could not be read on %r", channel, exc_info=True)
            return None, PRESS_ANSWERED

    def _say_on_press(self, ch: Channel, press: Any, label: str, *, edit: bool = True) -> bool:
        """``ch.answer_press``, never raising (BLOCKING). ``edit=False``
        answers the press only, leaving the buttons as they are."""
        try:
            return bool(ch.answer_press(press, label, edit=edit))
        except Exception:  # noqa: BLE001 — the answer stands; the button stays
            log.warning("inbound: a pressed button could not be answered", exc_info=True)
            return False

    async def _handle_press(self, name: str, ch: Channel, msg: InboundMessage) -> dict[str, Any]:
        """A button pressed under one of Iron Jarvis's own questions (10-b).

        Reached only from ``_dispatch`` AFTER the allowlist, the sender's
        proof and the private-chat guard. The press names a prompt (or an
        approval) and an option; it resolves through the SAME
        ``_resolve_prompt`` a typed "1" or "approve" does (one write path,
        first answer wins, an approval through ``platform.approvals``). The
        buttons then turn into what was decided ("Approved by you"), and for
        an approval or files sent that IS the reply; a press on an answered,
        expired or someone else's question resolves nothing and hears "That
        was already answered"."""
        press = msg.press
        chat_on = self._chat_ready(ch)
        display = self._display(msg) if chat_on else ""
        parsed = parse_button(getattr(press, "data", ""))
        prompt, why = None, PRESS_ANSWERED
        if parsed is not None and self.prompt_store is not None:
            prompt, why = await asyncio.to_thread(self._pressed_prompt, name, msg.sender_id, parsed)
        options = prompt_options(prompt) if prompt is not None else []
        n = parsed[2] if parsed is not None else 0
        if prompt is None or not 1 <= n <= len(options):
            # The answered button ("Approved by you") looks like a button and
            # gets tapped: that tap only hears it, the record of the decision
            # stays on the message. Any other stale press turns its buttons
            # into "That was already answered" (they no longer look live).
            done = str(getattr(press, "data", "") or "") == DONE_DATA
            await asyncio.to_thread(self._say_on_press, ch, press, why, edit=not done)
            return {"channel": name, "status": "press_stale", "sender": str(msg.sender_id)}
        kind = getattr(prompt, "kind", "")
        answer = options[n - 1]
        # The user's line on the phone chat is the button they pressed.
        said = APPROVAL_BUTTONS[n - 1] if kind == APPROVAL_KIND and n <= len(APPROVAL_BUTTONS) else answer
        hold = _PressHold() if kind in (APPROVAL_KIND, FILE_SEND_KIND, CODE_START_KIND) else None
        token = _PRESS_HOLD.set(hold)
        try:
            res = await self._resolve_prompt(name, ch, msg, display, chat_on, prompt, answer, said)
        finally:
            _PRESS_HOLD.reset(token)
        status = str(res.get("status") or "")
        label = str(res.get("press_label") or "") or (
            pressed_label(kind, answer) if status in _PRESS_DONE
            else PRESS_ANSWERED if status in _PRESS_GONE else "")
        changed = await asyncio.to_thread(self._say_on_press, ch, press, label) if label else False
        if hold is not None and hold.held and not changed:
            for text in hold.held:
                await self.send_chunked(ch, text, chat_id=msg.reply_to)
        return {**res, "pressed": True}

    # -- 10-b: finished files back to the phone ------------------------------
    def _turn_files(self, workspace_dir: str, project_id: str, documents: Any) -> Any:
        """The files a phone chat turn made that may go back (BLOCKING):
        its ``documents`` inside the folder its tools worked in, within the
        phone's caps (``agents/reply_files``)."""
        from ..agents.reply_files import PHONE_CAPS, pick
        from . import files_back

        docs = [str(p) for p in (documents or []) if str(p or "").strip()]
        if not docs:
            from ..agents.reply_files import Picked

            return Picked()
        return pick(docs, files_back.turn_folder(self.platform, workspace_dir, project_id), PHONE_CAPS)

    async def _files_will_ask(
        self, name: str, ch: Channel, picked: Any, *, tid: str = "", session_id: str = "",
    ) -> bool:
        """Will :meth:`_files_back` ask the files question for ``picked``?
        Read the way it decides (files to send, a destination that takes
        them, not "keep", and "ask" or a Local only chat). Never raises (a
        doubt: False, so an open question is still announced)."""
        from . import files_back

        try:
            if (not getattr(picked, "files", None) or not getattr(ch, "supports_files", False)
                    or self.platform is None or self.prompt_store is None):
                return False
            choice = files_back.consent(getattr(self.platform, "config", None), name)
            if choice == "keep":
                return False
            if choice == "ask":
                return True
            return bool(await asyncio.to_thread(
                files_back.work_is_local_only, getattr(self.platform, "engine", None), tid, session_id))
        except Exception:  # noqa: BLE001
            return False

    async def _files_back(
        self, name: str, ch: Channel, msg: InboundMessage, picked: Any, *, tid: str = "",
        session_id: str = "", display: str = "",
    ) -> dict[str, Any]:
        """After the answer's words: the files the work made go back to the
        chat that asked (10-b), or the one question first. Called with the
        identity lock held (the delivery's, or the message's). Never raises.

        * A Local only chat (D-10.3): nothing is sent; the question names
          how many files, their kind and size, never a name, every time.
        * Else the setting (``files_back.consent``): "keep" sends nothing;
          "ask" asks the first time on this destination; "send" sends now.
        """
        from . import files_back

        try:
            files = [str(p) for p in (getattr(picked, "files", None) or [])]
            left = list(getattr(picked, "left", None) or [])
            if not (files or left) or not getattr(ch, "supports_files", False) or self.platform is None:
                return {"files": "none"}
            local_only = await asyncio.to_thread(
                files_back.work_is_local_only, getattr(self.platform, "engine", None), tid, session_id)
            choice = files_back.consent(getattr(self.platform, "config", None), name)
            if choice == "keep":
                return {"files": "kept"}
            if not files:
                # Only files that may not go (outside the work's folder, past
                # the caps): named, with why (by count in a Local only chat).
                line = files_back.not_sent_line(left, [], files_back.destination_words(ch, name),
                                                local_only=local_only)
                if tid:
                    self._safe_append(tid, "assistant", line, channel=name, sender_id=msg.sender_id,
                                      display=display)
                await self.send_chunked(ch, line, chat_id=msg.reply_to)
                return {"files": "named"}
            if local_only or choice == "ask":
                asked = await self._ask_files_back(name, ch, msg, files, left, tid=tid,
                                                   local_only=local_only, display=display)
                return {"files": "asked" if asked else "not_asked"}
            went, _line = await self._send_files_now(name, ch, msg, files, left, tid=tid, display=display)
            return {"files": "sent", "sent": len(went)}
        except Exception:  # noqa: BLE001 — the answer already went; the files stay
            log.warning("inbound: the files could not go back on %r", name, exc_info=True)
            return {"files": "error"}

    async def _send_files_now(
        self, name: str, ch: Channel, msg: InboundMessage, files: list[str],
        left: list[tuple[str, str]], *, tid: str = "", display: str = "", local_only: bool = False,
        say: bool = True,
    ) -> tuple[list[str], str]:
        """Send ``files`` through the channel (off the loop), one
        ``comm.file_sent {channel, kind, bytes}`` each (never a name), then
        ONE line for what did not go (by name and why; by count in a Local
        only chat), on the phone and the phone chat (``say``; an answer's
        reply carries it instead). ``(the paths that went, line)``: what a
        reply says was sent is read from these, never from what was picked."""
        from . import files_back

        results = await asyncio.to_thread(ch.send_files, files, chat_id=msg.reply_to)
        sent: list[str] = []
        failed: list[tuple[str, str]] = []
        for path, res in zip(files, list(results or []) + [{}] * len(files)):
            if (res or {}).get("ok"):
                sent.append(path)
                try:
                    size = await asyncio.to_thread(lambda p=path: Path(p).stat().st_size)
                except OSError:
                    size = 0
                await self._publish(EventType.COMM_FILE_SENT, {
                    "channel": name, "kind": files_back.kind_word(path), "bytes": int(size)})
            else:
                failed.append((Path(path).name, str((res or {}).get("detail") or "it did not go")))
        line = files_back.not_sent_line(left, failed, files_back.destination_words(ch, name),
                                        local_only=local_only)
        if failed:
            log.info("inbound: %d of %d file(s) could not go back on %r", len(failed), len(files), name)
        if line and say:
            if tid:
                self._safe_append(tid, "assistant", line, channel=name, sender_id=msg.sender_id,
                                  display=display)
            await self.send_chunked(ch, line, chat_id=msg.reply_to)
        return sent, line

    async def _ask_files_back(
        self, name: str, ch: Channel, msg: InboundMessage, files: list[str],
        left: list[tuple[str, str]], *, tid: str, local_only: bool, display: str = "",
    ) -> bool:
        """The ONE question before files go (10-b): a ``file_send`` prompt
        (Send, Keep it on the PC) on this sender's chat, with buttons where
        the destination has them, and always "Reply 1 to send or 2 to keep it
        on the PC". An older unanswered one is closed and its files join this
        one. In a Local only chat it names no file. True when asked."""
        from . import files_back

        if self.prompt_store is None:
            return False
        sender = str(msg.sender_id)
        older = await asyncio.to_thread(self.prompt_store.expire_kind, FILE_SEND_KIND, name, sender)
        for ref in older:
            was, was_local, was_left = files_back.read_ref(ref)
            files = [p for p in was if p not in files] + files
            left = was_left + left
            local_only = local_only or was_local
        files = files[-files_back.MAX_WAITING:]
        # The question names what ONE answer can send: the first five, in the
        # order the Send reads them (it checks each again through the caps
        # and names the rest with why). A merged question waits on up to ten.
        from ..agents.reply_files import PHONE_CAPS

        going = files[:PHONE_CAPS.count]
        where = files_back.destination_words(ch, name)
        what = await asyncio.to_thread(files_back.what_words, going)
        question = (files_back.local_only_question(what, where, len(going)) if local_only
                    else files_back.first_time_question(what, where, len(going)))
        rec = await asyncio.to_thread(
            self.prompt_store.register, FILE_SEND_KIND,
            files_back.ref_for(files, local_only=local_only, left=left),
            question, FILE_SEND_OPTIONS, name, sender, tid,
        )
        if rec is None:
            return False
        if tid:
            self._safe_append(tid, "assistant", question, channel=name, sender_id=msg.sender_id,
                              display=display)
        # The typed answer rides WITH the buttons: Slack shows buttons
        # whenever its app token is set, but an app set up before
        # interactivity was in its manifest never gets a press back, and
        # "1" or "2" always works.
        asked = f"{question} {files_back.answer_hint(len(going))}"
        try:
            if getattr(ch, "supports_buttons", False):
                res = await asyncio.to_thread(ch.send, f"{self.reply_prefix}{asked}", chat_id=msg.reply_to,
                                              buttons=prompt_buttons(rec.id, FILE_SEND_OPTIONS))
                if not (res or {}).get("ok"):
                    await self.send_chunked(ch, asked, chat_id=msg.reply_to)
            else:
                await self.send_chunked(ch, asked, chat_id=msg.reply_to)
        except Exception:  # noqa: BLE001 — the question stays answerable by "1"
            log.warning("inbound: the files question could not be sent on %r", name, exc_info=True)
        return True

    async def _resolve_file_prompt(
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
        """The answer to "Send 1 spreadsheet (48 KB) to Telegram?" (10-b).
        The prompt is claimed FIRST (first answer wins: a second answer hears
        "That was already answered"). Send: the files go now (each checked
        again: still on this PC, within the caps), and the reply names what
        WENT; on the first-time question the yes is kept for THIS destination
        (``files_back.keep_choice``, the settings writer, Undo). Keep:
        nothing goes, and the first-time question's Keep is kept for THIS
        destination too. A Local only chat's question changes no setting: it
        asks every time (D-10.3)."""
        from ..agents.reply_files import PHONE_CAPS, pick
        from . import files_back

        decision = files_back.decision_of(answer)
        paths, local_only, left = files_back.read_ref(getattr(prompt, "ref_id", ""))
        if not decision:
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                files_back.answer_hint(len(paths) or 1), "answer_usage",
            )
        if not await asyncio.to_thread(self.prompt_store.resolve, prompt.id, decision):
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text, f"{PRESS_ANSWERED}.", "file_gone",
            )
        if decision == "keep":
            if not local_only:
                await asyncio.to_thread(files_back.keep_choice, self.platform, "keep", name)
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text,
                files_back.kept_reply(local_only, len(paths), files_back.destination_words(ch, name),
                                      getattr(self.platform, "config", None)),
                "file_kept",
            )
        if not local_only:
            await asyncio.to_thread(files_back.keep_choice, self.platform, "send", name)
        # Each file again: still on this PC and within the caps (it was
        # checked inside the work's folder when the question was asked).
        picked = await asyncio.to_thread(pick, paths, [Path(p).parent for p in paths], PHONE_CAPS)
        files = [str(p) for p in picked.files]
        where = files_back.destination_words(ch, name)
        sent: list[str] = []
        line = ""
        if files:
            # The line for what did not go rides this answer's reply (below).
            sent, line = await self._send_files_now(
                name, ch, msg, files, ([] if local_only else left) + picked.left,
                tid="", display=display, local_only=local_only, say=False)
        else:
            line = files_back.not_sent_line(picked.left, [], where, local_only=local_only)
        # Named from the files that WENT, never from what was picked.
        what = await asyncio.to_thread(files_back.what_words, sent) if sent else ""
        head = f"→ Sent {what} to {where}." if sent else ""
        if not sent and not line:
            line = f"Nothing could be sent to {where}: the files are no longer on this PC."
        reply = "\n".join(x for x in (head, line) if x)
        # The pressed button says what went too: "Sent 1 of 2" when an upload failed.
        extra = ({"press_label": "Not sent"} if not sent
                 else {"press_label": f"Sent {len(sent)} of {len(files)}"} if len(sent) < len(files)
                 else {})
        return await self._answer_reply(
            name, ch, msg, display, chat_on, original_text, reply,
            "file_sent" if sent and not line else "file_send_failed", **extra,
        )

    # -- 12-a: start coding work from the phone ------------------------------
    async def _asking_for(
        self, name: str, msg: InboundMessage, display: str, chat_on: bool, *, tid: str = "",
    ) -> Any:
        """The ASKING sender as this door proved it (``phone_code.Asking``):
        the destination, the sender, their phone chat on the PC (two-way chat
        on), where a reply goes, whether that chat is Local only (read
        STRICTLY: a failed read is Local only, ``files_back``) and the prompt
        store. Only this door makes one (after its allowlist, its proof and
        the private-chat guard). Never raises."""
        from ..harness.phone_code import Asking
        from . import files_back

        if not tid and chat_on and self.thread_store is not None:
            try:
                tid = (await asyncio.to_thread(self.thread_store.resolve, name, str(msg.sender_id), display)).id
            except Exception:  # noqa: BLE001 — no chat on the PC: the card still asks
                log.warning("inbound: the phone chat could not be read on %r", name, exc_info=True)
                tid = ""
        local = False
        if tid:
            local = bool(await asyncio.to_thread(
                files_back.work_is_local_only, getattr(self.platform, "engine", None), tid))
        reply_to = msg.reply_to if msg.reply_to is not None else msg.sender_id
        return Asking(channel=name, sender_id=str(msg.sender_id), thread_id=tid or "",
                      reply_to=str(reply_to), local_only=local, store=self.prompt_store)

    async def _send_card(self, ch: Channel, msg: InboundMessage, card: Any) -> bool:
        """One card (or folder pick) to the phone, with its buttons where the
        destination has them; the typed "Reply 1 …" rides WITH the buttons
        (10-b's rule: a Slack app without interactivity shows buttons no
        press comes back from). Never raises."""
        asked = f"{card.words}\n{card.hint}" if card.hint else card.words
        try:
            if getattr(ch, "supports_buttons", False) and card.prompt_id and card.options:
                res = await asyncio.to_thread(ch.send, f"{self.reply_prefix}{asked}", chat_id=msg.reply_to,
                                              buttons=prompt_buttons(card.prompt_id, list(card.options)))
                if (res or {}).get("ok"):
                    return True
            return await self.send_chunked(ch, asked, chat_id=msg.reply_to)
        except Exception:  # noqa: BLE001 — the card stays answerable by "1"
            log.warning("inbound: a coding-work card could not be sent", exc_info=True)
            return False

    async def _send_asked_cards(
        self, name: str, ch: Channel, msg: InboundMessage, who: Any, *, tid: str = "", display: str = "",
    ) -> int:
        """The cards the chat's ``code_start`` asked this turn, after the
        reply: onto the phone chat on the PC, then to the phone. Returns how
        many went."""
        sent = 0
        for card in list(getattr(who, "cards", None) or []):
            if tid:
                self._safe_append(tid, "assistant", card.words, channel=name, sender_id=msg.sender_id,
                                  display=display)
            if await self._send_card(ch, msg, card):
                sent += 1
        return sent

    async def _handle_code_command(
        self, name: str, ch: Channel, msg: InboundMessage, text: str, display: str, chat_on: bool,
    ) -> dict[str, Any]:
        """``/code <folder>: <what to do>`` (12-a): the card, a numbered pick
        when two used folders share the name, or the refusal in words (the
        user's folders named, or the check that failed). Nothing starts
        here: only the sender's Start does."""
        from ..harness import phone_code as PC

        parsed = PC.parse_code(text[len("/code"):])
        if parsed is None:
            usage = await asyncio.to_thread(PC.usage_words, self.platform.engine)
            return await self._answer_reply(name, ch, msg, display, chat_on, text, usage, "code_usage")
        folder, words, when = parsed
        who = await self._asking_for(name, msg, display, chat_on)
        card = await PC.from_words(self.platform, who, folder, words, when)
        if not card.ok:
            return await self._answer_reply(name, ch, msg, display, chat_on, text, card.words, "code_refused")
        tid = self._append_exchange(name, msg, display, text, card.words) if chat_on else ""
        sent = await self._send_card(ch, msg, card)
        return {"channel": name, "status": "code_card", "thread_id": tid, "sent": sent,
                "prompt_id": card.prompt_id, "kind": card.kind}

    async def _resolve_code_prompt(
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
        """The answer to a coding-work card or a folder pick (12-a). The
        prompt is CLAIMED FIRST (first answer wins; one Start makes one task,
        a second hears "That was already answered"). Not now starts nothing.
        Start runs every check again and makes ONE task with the card's own
        words (``phone_code.start_from_card``). A folder picked gets its
        card."""
        from ..harness import phone_code as PC

        kind = getattr(prompt, "kind", "")
        said = " ".join(str(answer or "").split())
        match = next((o for o in prompt_options(prompt) if o.casefold() == said.casefold()), None)
        if match is None:
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text, PC.answer_hint(kind), "answer_usage",
            )
        if not await asyncio.to_thread(self.prompt_store.resolve, prompt.id, match):
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text, f"{PRESS_ANSWERED}.", "code_gone",
            )
        who = await self._asking_for(name, msg, display, chat_on)
        if kind == CODE_FOLDER_KIND:
            card = await PC.after_pick(self.platform, who, prompt, match)
            if not card.ok:
                return await self._answer_reply(
                    name, ch, msg, display, chat_on, original_text, card.words, "code_refused",
                )
            tid = self._append_exchange(name, msg, display, original_text, card.words) if chat_on else ""
            sent = await self._send_card(ch, msg, card)
            return {"channel": name, "status": "code_card", "thread_id": tid, "sent": sent,
                    "prompt_id": card.prompt_id, "kind": card.kind}
        if match == PC.NOT_NOW:
            return await self._answer_reply(
                name, ch, msg, display, chat_on, original_text, PC.NOTHING_STARTED, "code_not_now",
            )
        reply, outcome = await PC.start_from_card(self.platform, who, prompt)
        status = {"started": "code_started", "queued": "code_queued"}.get(outcome, "code_refused")
        extra = ({"press_label": "Not started"} if status == "code_refused"
                 else {"press_label": "Queued"} if status == "code_queued" else {})
        return await self._answer_reply(
            name, ch, msg, display, chat_on, original_text, reply, status, **extra,
        )

    def _comm_trust(self, channel: str) -> tuple[str | None, str]:
        """The TRUST posture a session started from an inbound message gets
        (v1.298.0): ``("low", "started from an inbound <channel> message")``
        unless ``config.comm_trust`` (read via getattr; default ``"low"``)
        says ``"full"``. A phone message is an unattended door — nobody
        watched what the sender pasted — so what it starts may read and
        answer but not change memory, agents, skills or schedules."""
        # ``Config.comm_trust`` is a validated field ("low" | "full"); the
        # getattr default only covers a platform double with no config.
        cfg = getattr(self.platform, "config", None)
        setting = str(getattr(cfg, "comm_trust", "low") or "low").strip().lower()
        if setting == "full":
            return None, ""
        return "low", f"started from an inbound {channel} message"

    def _chat_turn_kwargs(self, channel: str) -> dict[str, Any]:
        """``{"trust": "low", "trust_reason": "started from an inbound
        <channel> message"}`` for the injected chat turn when the comm door
        is low — and ``{}`` when the injected callable cannot take it (a test
        fake with three positional parameters and no ``**kw``), so an older
        double keeps working while the real lanes get the posture."""
        trust, reason = self._comm_trust(channel)
        if trust is None:
            return {}
        fn = self.chat_turn
        try:
            import inspect

            params = inspect.signature(fn).parameters.values()
        except (TypeError, ValueError):
            return {}
        takes = any(
            p.kind is inspect.Parameter.VAR_KEYWORD or p.name == "trust"
            for p in params
        )
        return {"trust": trust, "trust_reason": reason} if takes else {}

    async def _publish(self, etype: str, payload: dict[str, Any], **kw: Any) -> None:
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(etype, payload, **kw)
        except Exception:  # noqa: BLE001 — the event bus must never block comm
            log.exception("failed to publish %s", etype)
