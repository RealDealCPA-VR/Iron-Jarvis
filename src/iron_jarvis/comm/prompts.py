"""Pending prompts — answer a parked workflow from your pocket (v1.137.0).

When a workflow run parks at an ask gate (v1.121's durable ``waiting`` state),
the engine publishes ``workflow.waiting``. :func:`handle_workflow_waiting`
(registered as an event-bus handler in ``daemon/app.py``) turns that into a
:class:`~.models.PendingPromptRecord` per REMOTE identity that can answer:
every ``(channel, sender)`` with an EXISTING comm thread on a chat-enabled,
credentialed channel gets the question appended to its thread and sent to the
phone. If no such identity exists, no prompt is registered — answering from
the phone requires an established conversation (the identity row IS the trust
anchor; it only exists for allowlisted senders who have already talked to
Iron Jarvis).

THE RESOLUTION RULE (implemented in ``comm/inbound.py``, pinned by tests):
while a prompt is open for an identity, a bare inbound message resolves it
ONLY when it is a pure integer (``str.isdecimal``): with options it must be
an in-range numbered pick (out-of-range stays chat); without options (every
workflow ask today) the integer ITSELF is the answer — "How many clients?"
→ "3" just works, which keeps the park alert's "reply with a number or
/answer" promise true. Anything non-integer stays a NORMAL chat turn — the
reply just gains a gentle "(A workflow is still waiting...)" reminder on the
outbound phone copy (never on the thread). Explicit ``/answer <text>`` always
resolves the newest open prompt, mid-conversation or not. Every resolution is
echoed back ("→ Answered ..."), so nothing is ever swallowed silently; free
text deliberately never resolves a gate.

Resolution goes through :func:`answer_parked_run` — the comm-side twin of
``POST /workflows/runs/{id}/answer``: the SAME atomic waiting→resuming
compare-and-set, so the first answer wins whichever surface it came from. A
lost claim (already answered from the desktop) gets an honest reply and the
prompt is marked superseded; a run that un-parked before any reply expires
its prompt on the next look.

MID-RUN TOOL APPROVALS (v1.200.0) are the second prompt kind: a session
paused on an ask-tier tool (``approval.requested``, agents/runtime.py
v1.189.0) registers an approve/deny prompt per identity via
:func:`handle_approval_requested`, and a phone reply of "approve"/"deny"
(or 1/2, or ``/answer``) resolves it through ``platform.approvals`` — the
SAME registry ``POST /chat/approvals/{id}`` (the dashboard bell) writes to.
Unlike workflow gates these prompts carry a hard deadline, so
``approval.resolved`` (published on every pause ending, timeout included)
expires them via :meth:`PendingPromptStore.expire_ref`.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from .base import split_message
from .models import CommIdentityRecord, PendingPromptRecord

log = get_logger("comm.prompts")

#: The engine publishes this as a raw string (workflows/engine.py, v1.121.0).
WAITING_EVENT = "workflow.waiting"

#: MID-RUN TOOL APPROVALS (v1.200.0) — the agent runtime publishes these when
#: a session pauses on an ask-tier tool (agents/runtime.py, v1.189.0) and
#: again when the pause ends (answered from any surface, or its ~300s timeout
#: denied honestly). The pause becomes a pending prompt too, so the phone can
#: answer it through the SAME registry the dashboard bell writes to
#: (``platform.approvals`` — one write path, first answer wins).
APPROVAL_EVENT = "approval.requested"
APPROVAL_RESOLVED_EVENT = "approval.resolved"

#: Prompt ``kind`` for a mid-run tool approval; ``ref_id`` is the approval id.
APPROVAL_KIND = "approval"

#: The two answers an approval takes — stored as the prompt's OPTIONS so the
#: existing numbered-pick path ("1"/"2") maps onto them for free.
APPROVAL_OPTIONS = ["approve", "deny"]

#: The BARE words that resolve an open approval prompt mid-chat. Deliberately
#: only these two — the alert's exact vocabulary. A looser set ("yes", "ok")
#: would let an ordinary chat reply grant a tool by accident; the synonyms
#: below are accepted only behind the EXPLICIT ``/answer`` command.
APPROVAL_WORDS = ("approve", "deny")

#: Answer word → registry decision. "once" is the deliberate ceiling for a
#: phone reply: granting "conversation" (the rest of the run) belongs on a
#: surface that can see what else the run is doing.
APPROVAL_DECISIONS: dict[str, str] = {
    "approve": "once",
    "allow": "once",
    "yes": "once",
    "y": "once",
    "deny": "deny",
    "no": "deny",
    "reject": "deny",
    "n": "deny",
}

#: Question text cap on the stored prompt (a runaway templated question must
#: not bloat the table or the phone copy).
_QUESTION_CAP = 500

#: Wire copy — pinned here so the poller, the command grammar, and the tests
#: all speak the same words.
NOTHING_WAITING_REPLY = "Nothing is waiting for an answer right now."
ALREADY_ANSWERED_REPLY = "Already answered from the desktop — the run is moving on."
ANSWER_USAGE_REPLY = "Usage: /answer <your answer>"
APPROVAL_GONE_REPLY = (
    "That request already timed out or was answered from the desktop — "
    "the agent has moved on."
)
APPROVAL_USAGE_REPLY = "Reply approve or deny (or 1 / 2)."

#: Prompt statuses the store accepts for :meth:`PendingPromptStore.expire`.
_EXPIRE_STATUSES = ("expired", "superseded")


def _snippet(text: Any, limit: int = 60) -> str:
    """One-line, whitespace-collapsed snippet for echoes and reminders."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def prompt_options(rec: PendingPromptRecord | None) -> list[str]:
    """The prompt's options as a list of strings; ``[]`` on anything odd."""
    try:
        data = json.loads(getattr(rec, "options_json", "") or "[]")
        return [str(o) for o in data] if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — a corrupt blob must not break resolution
        return []


def prompt_line(workflow: str, question: str, options: list[str] | None) -> str:
    """The line appended to the thread AND sent to the phone at registration.

    With options it enumerates them so a one-tap numbered reply works; without
    (every workflow ask today) it points at ``/answer`` (a bare pure-integer
    reply also resolves — see the module docstring — but the copy leads with
    the path that works for ANY answer). A bare free-text reply deliberately
    does NOT resolve, so the copy must not promise "reply here".
    """
    base = f"Workflow '{workflow}' is waiting: {question}"
    opts = [str(o) for o in options or []]
    if opts:
        listing = "\n".join(f"{i}. {o}" for i, o in enumerate(opts, 1))
        return f"{base}\n{listing}\nReply with a number, or /answer <text>."
    return f"{base} — reply with /answer <your answer>."


def approval_question(tool: str) -> str:
    """The stored prompt question for a mid-run tool approval."""
    return f"An agent is asking to use '{tool}'"


def approval_prompt_line(tool: str) -> str:
    """The line appended to the identity's THREAD at registration, so the
    desktop conversation shows the same pause the phone was alerted about.
    NOT sent to the phone by the handler — see
    :func:`handle_approval_requested` for why the notifier alert is the one
    phone copy."""
    return (
        f"⏸ An agent is asking to use '{tool}' — reply approve or deny "
        "(or 1 / 2), or answer from the dashboard bell."
    )


def approval_echo(decision: str) -> str:
    """The confirmation echoed back after a resolved approval — nothing is
    ever swallowed silently, same rule as :func:`answer_echo`."""
    if decision == "deny":
        return "→ Denied — the agent will carry on without that tool."
    return "→ Approved — the agent is going ahead."


def approval_reminder(question: str) -> str:
    """Outbound-copy suffix while an approval prompt is still open — the
    approval twin of :func:`pending_reminder` (a workflow's wording would
    misname the gate)."""
    return (
        f"\n\n(An agent is still waiting for permission: '{_snippet(question)}' "
        "— reply approve or deny.)"
    )


def answer_echo(question: str, run_label: str) -> str:
    """The confirmation echoed back (and onto the thread) after a resolved answer."""
    return (
        f"→ Answered '{_snippet(question)}' on run {run_label}. "
        "The workflow is resuming."
    )


def pending_reminder(question: str) -> str:
    """Suffix folded into the OUTBOUND phone copy of a normal chat reply while
    a prompt is still open — never appended to the thread itself."""
    return (
        f"\n\n(A workflow is still waiting: '{_snippet(question)}' — "
        "reply with a number or /answer <text>.)"
    )


class PendingPromptStore:
    """Durable pending prompts, one lock, never-raise reads (sibling of
    :class:`~.threads.CommThreadStore` — same engine, same discipline)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # Serializes register's supersede+insert (no DB unique constraint —
        # see CommIdentityRecord's rationale) against concurrent resolves.
        self._lock = threading.Lock()

    # -- writes (guarded; None/False on failure, never raise) ---------------
    def register(
        self,
        kind: str,
        ref_id: str,
        question: str,
        options: list[str] | None,
        channel: str,
        sender_id: str,
        thread_id: str,
    ) -> PendingPromptRecord | None:
        """Insert an open prompt, superseding any older OPEN prompt with the
        same ``(kind, ref_id, channel, sender_id)`` — re-notification for the
        same gate must not stack duplicate answerable rows."""
        try:
            with self._lock, session_scope(self.engine) as db:
                stale = db.exec(
                    select(PendingPromptRecord).where(
                        PendingPromptRecord.kind == kind,
                        PendingPromptRecord.ref_id == ref_id,
                        PendingPromptRecord.channel == channel,
                        PendingPromptRecord.sender_id == str(sender_id),
                        PendingPromptRecord.status == "open",
                    )
                ).all()
                for old in stale:
                    old.status = "superseded"
                    old.decided_at = utcnow()
                    db.add(old)
                rec = PendingPromptRecord(
                    kind=kind,
                    ref_id=str(ref_id),
                    question=str(question or "")[:_QUESTION_CAP],
                    options_json=json.dumps([str(o) for o in options or []]),
                    channel=channel,
                    sender_id=str(sender_id),
                    thread_id=str(thread_id or ""),
                    status="open",
                )
                db.add(rec)
                db.commit()
                db.refresh(rec)
                return rec
        except Exception:  # noqa: BLE001 — registration must never break the bus
            log.warning("pending prompt register failed for %r", ref_id, exc_info=True)
            return None

    def resolve(self, prompt_id: str, answer_text: str) -> bool:
        """Mark a prompt answered (status + ``decided_at``). ``answer_text``
        is accepted for the call-site's clarity; the answer itself lives on
        the workflow run (``User answered: ...``), not on this marker row."""
        return self._decide(prompt_id, "answered")

    def expire(self, prompt_id: str, status: str = "expired") -> bool:
        """Close a prompt without an answer: ``expired`` (the run un-parked by
        other means) or ``superseded`` (lost the claim race / replaced)."""
        if status not in _EXPIRE_STATUSES:
            status = "expired"
        return self._decide(prompt_id, status)

    def expire_ref(self, kind: str, ref_id: str, status: str = "expired") -> int:
        """Close EVERY open prompt for one gate, across all identities.

        The ``approval.resolved`` hygiene path (v1.200.0): the pause ended —
        answered from any surface, or the runtime's timeout denied it — so no
        identity may keep an answerable row for the dead gate (it would nag
        chat replies and invite answers into nothing). Returns how many
        prompts closed; 0 on failure (never raises)."""
        if status not in _EXPIRE_STATUSES:
            status = "expired"
        try:
            with self._lock, session_scope(self.engine) as db:
                rows = db.exec(
                    select(PendingPromptRecord).where(
                        PendingPromptRecord.kind == kind,
                        PendingPromptRecord.ref_id == str(ref_id),
                        PendingPromptRecord.status == "open",
                    )
                ).all()
                for rec in rows:
                    rec.status = status
                    rec.decided_at = utcnow()
                    db.add(rec)
                db.commit()
                return len(rows)
        except Exception:  # noqa: BLE001 — a marker write must never break comm
            log.warning("expire_ref failed for %s/%s", kind, ref_id, exc_info=True)
            return 0

    def _decide(self, prompt_id: str, status: str) -> bool:
        try:
            with self._lock, session_scope(self.engine) as db:
                rec = db.get(PendingPromptRecord, prompt_id)
                if rec is None or rec.status != "open":
                    return False
                rec.status = status
                rec.decided_at = utcnow()
                db.add(rec)
                db.commit()
                return True
        except Exception:  # noqa: BLE001 — a marker write must never break comm
            log.warning("pending prompt %s -> %s failed", prompt_id, status, exc_info=True)
            return False

    # -- reads (never raise) ------------------------------------------------
    def newest_open(self, channel: str, sender_id: str) -> PendingPromptRecord | None:
        """The newest OPEN prompt for this identity, or ``None``."""
        try:
            with session_scope(self.engine) as db:
                return db.exec(
                    select(PendingPromptRecord)
                    .where(
                        PendingPromptRecord.channel == channel,
                        PendingPromptRecord.sender_id == str(sender_id),
                        PendingPromptRecord.status == "open",
                    )
                    .order_by(PendingPromptRecord.created_at.desc())  # type: ignore[attr-defined]
                ).first()
        except Exception:  # noqa: BLE001
            log.warning("newest_open failed for %s/%s", channel, sender_id, exc_info=True)
            return None

    def open_count(self, channel: str, sender_id: str) -> int:
        try:
            with session_scope(self.engine) as db:
                rows = db.exec(
                    select(PendingPromptRecord).where(
                        PendingPromptRecord.channel == channel,
                        PendingPromptRecord.sender_id == str(sender_id),
                        PendingPromptRecord.status == "open",
                    )
                ).all()
            return len(rows)
        except Exception:  # noqa: BLE001
            return 0


# --------------------------------------------------------------------------- #
# the answer path — the comm-side twin of POST /workflows/runs/{id}/answer
# --------------------------------------------------------------------------- #
async def answer_parked_run(
    platform: Any, orchestrator: Any, spawn: Any, run_id: str, answer: str
) -> dict[str, Any]:
    """Atomically claim a ``waiting`` run and resume it with ``answer``.

    EXACTLY the HTTP route's semantics (daemon/routes/workflows.py): a
    compare-and-set ``waiting -> resuming`` decides the winner — first answer
    wins, whichever surface (chat card, Workflows page, phone) it came from —
    then ``WorkflowEngine.resume_after_answer`` drives the tail in the
    background (``spawn`` is the daemon's ``_spawn_bg``; without one the coro
    is returned for the caller/tests to drive). Returns ``{"ok": True,
    "run_name": ...}`` on a won claim, ``{"ok": False, "status": <current>}``
    when the run is gone or no longer waiting.
    """
    from sqlalchemy import update as _sql_update

    from ..workflows.engine import WorkflowEngine
    from ..workflows.models import WorkflowRunRecord

    answer = (answer or "").strip()
    if not answer:
        return {"ok": False, "status": "empty_answer"}
    def _claim():  # v1.226.0: the CAS write runs off the loop (parity with the route)
        with session_scope(platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                return None, {"ok": False, "status": "missing"}
            claimed = db.exec(
                _sql_update(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.id == run_id,
                    WorkflowRunRecord.status == "waiting",
                )
                .values(status="resuming")
            )
            db.commit()
            if getattr(claimed, "rowcount", 0) != 1:
                return None, {"ok": False, "status": rec.status}
            db.refresh(rec)
            return rec, None

    rec, refused = await asyncio.to_thread(_claim)
    if refused is not None:
        return refused
    engine = WorkflowEngine(platform, orchestrator)
    coro = engine.resume_after_answer(rec, answer)
    out: dict[str, Any] = {"ok": True, "run_name": rec.workflow_name or run_id}
    if spawn is not None:
        spawn(rec.id, coro)
    else:  # no background launcher wired — hand the resume to the caller
        out["resume"] = coro
    return out


# --------------------------------------------------------------------------- #
# registration — the workflow.waiting event-bus handler body
# --------------------------------------------------------------------------- #
def _event_field(event: Any, attr: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(attr, default)
    return getattr(event, attr, default)


def _identities(engine: Engine, channel: str) -> list[CommIdentityRecord]:
    try:
        with session_scope(engine) as db:
            return list(
                db.exec(
                    select(CommIdentityRecord).where(
                        CommIdentityRecord.channel == channel
                    )
                )
            )
    except Exception:  # noqa: BLE001
        return []


def _question_from_record(engine: Engine, run_id: str) -> str:
    """Fallback when the event payload lacks the question (it should not —
    the engine's park publish carries it — but a prompt without its question
    is useless, so fetch the parked run's own ``waiting_json``)."""
    try:
        from ..workflows.models import WorkflowRunRecord

        with session_scope(engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
        if rec is None:
            return ""
        waiting = json.loads(rec.waiting_json or "{}")
        return str(waiting.get("question") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _send_chunked_sync(ch: Any, body: str, *, chat_id: Any) -> bool:
    """Synchronous chunked send (bus handlers already run off the loop via
    ``to_thread``, so blocking here is fine). True iff every chunk reported ok."""
    try:
        limit = int(getattr(ch, "chunk_limit", 3500) or 3500)
        ok = True
        for chunk in split_message(body, limit):
            res = ch.send(chunk, chat_id=chat_id)
            ok = ok and bool((res or {}).get("ok"))
        return ok
    except Exception:  # noqa: BLE001
        return False


def handle_approval_requested(
    event: Any,
    *,
    store: PendingPromptStore,
    notifier: Any,
    thread_store: Any,
) -> list[dict[str, Any]]:
    """Register answerable prompts for a session paused on an ask-tier tool.

    The comm half of v1.189.0's mid-run approvals (v1.200.0): the SAME
    identity walk as :func:`handle_workflow_waiting` — every allowlisted
    identity on a chat-enabled, credentialed channel gets a durable prompt
    row (kind ``approval``, ``ref_id`` = the approval id, options
    approve/deny) plus the pause line on its desktop thread, so a phone reply
    of approve/deny (or 1/2, or ``/answer``) resolves the pause through the
    SAME registry the dashboard bell uses (``platform.approvals`` — one write
    path, first answer wins).

    DELIBERATELY NO PHONE SEND HERE — the inverse of the workflow
    arrangement (see the ``DEFAULT_ALERT_EVENTS`` note in ``notifier.py``):
    the workflow engine delivers its own park alert, so ``workflow.waiting``
    stays OUT of the default alert set and this module sends the answerable
    copy; nothing delivers an approval pause EXCEPT the notifier, so
    ``approval.requested`` IS a default alert, its line already says "reply
    here", and a second per-identity send from this handler would
    double-message the same phone. Fully guarded — never raises.
    """
    try:
        payload = _event_field(event, "payload", {}) or {}
        approval_id = str(payload.get("approval_id") or "").strip()
        if not approval_id:
            return []
        tool = str(payload.get("tool") or "").strip() or "a tool"
        question = approval_question(tool)

        out: list[dict[str, Any]] = []
        for name in notifier.channels():
            ch = notifier.get(name)
            if ch is None:
                continue
            try:
                if not (ch.chat_enabled() and ch.has_credentials()):
                    continue
            except Exception:  # noqa: BLE001 — a config quirk skips the channel
                continue
            for identity in _identities(store.engine, name):
                try:
                    # Fail-closed, exactly like the poller: a shrunk allowlist
                    # revokes the identity's right to answer.
                    if not ch.is_authorized(identity.sender_id):
                        continue
                    thread_id = str(identity.thread_id or "")
                    if thread_store is not None:
                        try:
                            thread_id = thread_store.resolve(
                                name, identity.sender_id, identity.display_name
                            ).id
                        except Exception:  # noqa: BLE001 — prompt still registers
                            log.warning(
                                "comm thread resolve failed during approval "
                                "prompt registration on %r",
                                name,
                                exc_info=True,
                            )
                    prompt = store.register(
                        APPROVAL_KIND,
                        approval_id,
                        question,
                        APPROVAL_OPTIONS,
                        name,
                        identity.sender_id,
                        thread_id,
                    )
                    if prompt is None:
                        continue
                    if thread_store is not None and thread_id:
                        try:
                            thread_store.append(
                                thread_id, "assistant", approval_prompt_line(tool)
                            )
                        except Exception:  # noqa: BLE001 — row registered anyway
                            log.warning(
                                "approval prompt line could not land on thread %s",
                                thread_id,
                                exc_info=True,
                            )
                    out.append(
                        {
                            "channel": name,
                            "sender": identity.sender_id,
                            "prompt_id": prompt.id,
                            "thread_id": thread_id,
                        }
                    )
                except Exception:  # noqa: BLE001 — one identity never skips the rest
                    log.warning(
                        "approval prompt registration failed for %s/%s",
                        name,
                        getattr(identity, "sender_id", "?"),
                        exc_info=True,
                    )
        return out
    except Exception:  # noqa: BLE001 — the event bus must survive any handler
        log.exception("approval.requested prompt registration failed")
        return []


def handle_workflow_waiting(
    event: Any,
    *,
    store: PendingPromptStore,
    notifier: Any,
    thread_store: Any,
    reply_prefix: str = "Iron Jarvis: ",
) -> list[dict[str, Any]]:
    """Register pending prompts for a just-parked workflow run.

    For every chat-enabled + credentialed channel, every EXISTING comm
    identity (still on the allowlist) gets: a superseding prompt row, the
    question appended to its thread, and a chunked send to the phone. No
    identities → no prompts (answering from a pocket requires an established
    conversation). Fully guarded — this runs on the event bus and must never
    break it; a failure for one identity never skips the rest.

    ALSO the dispatch point for the approval events (v1.200.0): this exact
    function is what ``daemon/app.py`` registers on the bus, so routing
    ``approval.requested`` / ``approval.resolved`` here means the shared
    daemon factory needs no new wiring per prompt kind. ``approval.requested``
    registers prompts (:func:`handle_approval_requested`); ``approval.resolved``
    — published on EVERY pause ending, dashboard answer and runtime timeout
    alike — expires every identity's open prompt for that gate, which is the
    timeout hygiene that keeps an approval prompt from lingering past its
    ~300s window.
    """
    try:
        etype = _event_field(event, "type")
        if etype == APPROVAL_EVENT:
            return handle_approval_requested(
                event, store=store, notifier=notifier, thread_store=thread_store
            )
        if etype == APPROVAL_RESOLVED_EVENT:
            payload = _event_field(event, "payload", {}) or {}
            approval_id = str(payload.get("approval_id") or "").strip()
            if approval_id:
                store.expire_ref(APPROVAL_KIND, approval_id)
            return []
        if etype != WAITING_EVENT:
            return []
        payload = _event_field(event, "payload", {}) or {}
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return []
        workflow = str(payload.get("workflow") or "").strip() or run_id
        question = str(payload.get("question") or "").strip()
        if not question:
            question = _question_from_record(store.engine, run_id) or (
                f"Workflow '{workflow}' is waiting for your answer."
            )
        raw_options = payload.get("options")
        options = [str(o) for o in raw_options] if isinstance(raw_options, list) else []

        out: list[dict[str, Any]] = []
        for name in notifier.channels():
            ch = notifier.get(name)
            if ch is None:
                continue
            try:
                if not (ch.chat_enabled() and ch.has_credentials()):
                    continue
            except Exception:  # noqa: BLE001 — a config quirk skips the channel
                continue
            for identity in _identities(store.engine, name):
                try:
                    # The allowlist can shrink after an identity was minted —
                    # fail-closed, exactly like the poller.
                    if not ch.is_authorized(identity.sender_id):
                        continue
                    # resolve() heals a dashboard-deleted thread (re-binds a
                    # fresh one) so the prompt's thread_id is always live.
                    thread_id = str(identity.thread_id or "")
                    if thread_store is not None:
                        try:
                            thread_id = thread_store.resolve(
                                name, identity.sender_id, identity.display_name
                            ).id
                        except Exception:  # noqa: BLE001 — prompt still registers
                            log.warning(
                                "comm thread resolve failed during prompt "
                                "registration on %r",
                                name,
                                exc_info=True,
                            )
                    prompt = store.register(
                        "workflow_ask",
                        run_id,
                        question,
                        options,
                        name,
                        identity.sender_id,
                        thread_id,
                    )
                    if prompt is None:
                        continue
                    line = prompt_line(workflow, prompt.question, options)
                    if thread_store is not None and thread_id:
                        try:
                            thread_store.append(thread_id, "assistant", line)
                        except Exception:  # noqa: BLE001 — send anyway
                            log.warning(
                                "prompt line could not land on thread %s",
                                thread_id,
                                exc_info=True,
                            )
                    sent = _send_chunked_sync(
                        ch, f"{reply_prefix}{line}", chat_id=identity.sender_id
                    )
                    out.append(
                        {
                            "channel": name,
                            "sender": identity.sender_id,
                            "prompt_id": prompt.id,
                            "thread_id": thread_id,
                            "sent": sent,
                        }
                    )
                except Exception:  # noqa: BLE001 — one identity never skips the rest
                    log.warning(
                        "pending prompt registration failed for %s/%s",
                        name,
                        getattr(identity, "sender_id", "?"),
                        exc_info=True,
                    )
        return out
    except Exception:  # noqa: BLE001 — the event bus must survive any handler
        log.exception("workflow.waiting prompt registration failed")
        return []
