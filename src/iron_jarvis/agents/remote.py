"""Remote agents — agents the user runs ELSEWHERE (§11/§12 extension).

A *remote agent* is not run by this daemon at all: it lives on another machine
the user controls — their own Hermes on a second PC, an OpenClaw instance, or
any OpenAI-compatible chat endpoint — and this platform simply hands it a task
over HTTP and relays the reply back. The user explicitly registers the endpoint,
so a LAN/localhost target is a FEATURE, not an SSRF risk: remote-agent calls
therefore do NOT pass through :func:`assert_safe_webhook_url` (which rejects
private addresses by default).

Three shapes are supported:

* ``openai-chat`` — POST ``{base_url}/chat/completions`` (or ``base_url`` as-is
  if it already ends in ``completions``) with an OpenAI ``chat/completions``
  body, ``Authorization: Bearer <secret>``, and parse
  ``choices[0].message.content``.
* ``openai-responses`` — POST ``{base_url}/responses`` with an OpenAI
  **Responses API** body (``input``, not ``messages``) and parse
  ``output_text`` / ``output[].content[].text``. A growing number of agent
  endpoints speak only this dialect; sending them a chat body earns a
  ``400 Missing 'input' field``, which is the live symptom that added it here.
* ``http-task`` — POST ``base_url`` with ``{"task": ...}`` (+ optional bearer)
  and accept a ``{"result": ...}`` or ``{"output": ...}`` reply.

A 4xx whose body complains about the *other* dialect's field is answered with
the fix ("this endpoint expects the Responses API — set kind to
openai-responses") rather than a bare status code: the shape mismatch is
recognisable, so the user should not have to guess it.

The credential is stored ONLY in the encrypted secrets vault (referenced by
``secret_name``); it is resolved at call time and NEVER logged.

A REMOTE AGENT IS A CONVERSATION, BOTH WAYS (v1.285.0). The user's question:
"what is stopping me from interacting with my remote agents the way I can with
Slack?" — the answer was this module: one POST per turn carrying only the task
text, no memory between calls, and no way for the remote to reach back. Now:

* OUTWARD, :meth:`RemoteAgentRegistry.run` takes ``history`` (the exchange so
  far as ``[{role, content}]``), a stable ``conversation_id`` (the panel room
  the remote sits in) and ``reply_to`` (its inbound address, below). The
  OpenAI dialects send real multi-turn ``messages`` / ``input``; ``http-task``
  adds ``conversation_id``, ``history`` and ``reply_to`` beside ``task`` — a
  bare ``{"task"}`` when none is given, so older endpoints see no change.
* ASYNC: a remote may answer ``202`` (or ``{"accepted": true}``) meaning "I
  have it and will message you back" — the round records that honestly
  instead of timing out on a long job.
* INWARD, a remote the user enabled for it (``inbound_enabled``) may POST
  ``{conversation_id, message, kind, files}`` to ``/agents/remote/{name}/inbound``
  with its OWN inbound token (vault key ``inbound_secret_name``; minted by
  :meth:`RemoteAgentRegistry.enable_inbound`, shown to the user once, rotated
  on re-enable). The daemon lands the message in that room — and, when the
  room belongs to a phone conversation, on the phone — so progress, questions,
  results and files arrive where the user is, unprompted.

ANOTHER IRON JARVIS IS A REMOTE TOO (v1.357.0, G61). A second computer running
Iron Jarvis turns on its task door (``agents/task_door.py``,
``POST /agents/task-door/task``) and this one registers it HERE, through the
same form, as an ``http-task`` remote: its door address and its door token.
Nothing about the wire changes except what the answer needs: when the remote's
address IS such a door (:func:`~iron_jarvis.agents.task_door.is_task_door_url`)
and message-back is on, ``run`` adds ``reply_token`` — this computer's inbound
token for that remote — beside ``reply_to``, so the other Iron Jarvis can post
its answer home (every other remote's body is byte-identical); ``test`` asks
the door a token-checked question instead of handing it a throwaway task; and a
door's refusal (off, wrong token, Emergency stop, too many) is said in words.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from . import remote_files as _remote_files
from ..tools.base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:  # avoid importing the heavy SQLAlchemy symbol at runtime
    from sqlalchemy import Engine

#: ``secret_resolver(name) -> str | None`` — resolves a vault secret name to its
#: plaintext value (wire ``platform.secrets.get``). Kept injectable for tests.
SecretResolver = Callable[[str], "str | None"]

#: The remote-agent transports.
KINDS = ("http-task", "openai-chat", "openai-responses")

#: Conversation rows sent outward per call (v1.285.0), and the per-row cap —
#: a remote's own transport window is unknown here, so a fixed, generous tail.
_HISTORY_ROWS = 80
_HISTORY_ROW_CHARS = 12_000

#: What a remote may post back (v1.285.0), and the per-message cap.
INBOUND_KINDS = ("message", "progress", "question", "done")
INBOUND_MESSAGE_CHARS = 12_000


def _history_messages(history: Any) -> list[dict[str, str]]:
    """``history`` rows → clean ``[{role, content}]`` (user/assistant only,
    blank rows dropped, a bounded tail). Never raises."""
    if not isinstance(history, list):
        return []
    out: list[dict[str, str]] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        role = "user" if str(row.get("role") or "") == "user" else "assistant"
        out.append({"role": role, "content": content[:_HISTORY_ROW_CHARS]})
    return out[-_HISTORY_ROWS:]


def _responses_text(data: Any) -> str:
    """Text out of an OpenAI **Responses API** reply.

    Servers vary: some return the convenience field ``output_text``, others only
    the structured ``output[]`` items. Both are read, and unknown item types are
    skipped rather than guessed at.
    """
    if not isinstance(data, dict):
        return ""
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    if isinstance(direct, list):  # some servers send a list of strings
        joined = "".join(p for p in direct if isinstance(p, str))
        if joined.strip():
            return joined
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") not in (None, "message"):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _dialect_hint(kind: str, body: str) -> str:
    """Turn a shape-mismatch 4xx into the setting that fixes it.

    ``Missing 'input' field`` from a chat-shaped POST means the endpoint speaks
    the Responses API, and vice versa. The user should not have to know that —
    the live report was exactly this error with correct credentials.
    """
    low = (body or "").lower()
    if kind == "openai-chat" and "input" in low and "missing" in low:
        return (
            "this endpoint expects the OpenAI Responses API — change this agent's "
            "kind to 'openai-responses' (it sends `input` instead of `messages`)"
        )
    if kind == "openai-responses" and "messages" in low and "missing" in low:
        return (
            "this endpoint expects OpenAI chat/completions — change this agent's "
            "kind to 'openai-chat' (it sends `messages` instead of `input`)"
        )
    if kind == "http-task" and ("input" in low or "messages" in low) and "missing" in low:
        return (
            "this looks like an OpenAI-compatible endpoint, not a plain task "
            "webhook — set kind to 'openai-responses' or 'openai-chat'"
        )
    return ""


#: v1.449.0 (readiness item 19): the fix a remote agent's Test answer gets,
#: said for the press that asked (a Test on the agent's card — never "Retry",
#: never "this chat") and for what a REMOTE has (a token on its card, a
#: dialect, a time limit).
REMOTE_TEST_FIX: dict[str, str] = {
    "timeout": (
        "Press Test again. If it keeps happening, check that the other computer "
        "is awake, or raise its time limit on this card (Edit)."
    ),
    "unreachable": (
        "Check its address, and that the other computer can be reached from this "
        "one, then press Test again."
    ),
    "refused": "Update its token on this card (Edit), then press Test again.",
    "http": "Press Test again in a moment. If it keeps failing, check the service behind that address.",
    "shape": (
        "Check its dialect on this card (Edit): the address answered, but not in "
        "the shape that dialect expects."
    ),
    "other": "Press Test again. If it keeps failing, check its address and token on this card.",
}


class _RemoteProbeFailure(Exception):
    """A Test's typed facts in the shape ``failure_words.friendly_error`` reads
    (a router refusal ``kind``, the ``failed_provider``, an HTTP
    ``status_code``), so the headline of a remote that timed out, could not be
    reached or answered an error is the SAME classifier every chat failure
    goes through — never a second wording (v1.449.0)."""

    def __init__(self, name: str, kind: str, status: "int | None") -> None:
        super().__init__(f"{name} test: {kind} {status or ''}".strip())
        self.kind = kind
        self.failed_provider = name
        self.status_code = status


def remote_test_words(name: str, probe: dict[str, Any]) -> dict[str, str]:
    """``{title, fix, technical}`` for a remote agent's Test (v1.449.0,
    readiness item 19), read off the TYPED facts :meth:`RemoteAgentRegistry.run`
    answers beside its unchanged ``detail`` (``kind``: timeout | unreachable |
    http | shape | door, ``status``) — never off the detail's text. The
    headline of a timeout, an unreachable address or an error status comes
    from ``failure_words.friendly_error``; the fix is said for a Test on this
    card (:data:`REMOTE_TEST_FIX`); ``technical`` is the probe's own text,
    masked, for a failed Test only (it folds under Details). A probe another
    Iron Jarvis's door already worded (``kind == "door"``) is the headline
    itself. ``detail`` stays as it was for a headless caller. Never raises."""
    from ..providers import failure_words as fw

    try:
        shown = " ".join(str(name or "").split()) or "the remote agent"
        head = fw.heading(shown)
        if probe.get("ok"):
            return {"title": f"{head} replied", "fix": "", "technical": ""}
        detail = str(probe.get("detail") or "")
        technical = fw.mask_failure_text(detail)[:400]
        kind = str(probe.get("kind") or "")
        status = probe.get("status")
        status = status if isinstance(status, int) and not isinstance(status, bool) else None
        if kind == "door" and detail:
            return {"title": fw.mask_failure_text(detail)[:400], "fix": "", "technical": ""}
        if kind in ("timeout", "unreachable"):
            words = fw.friendly_error(_RemoteProbeFailure(shown, kind, None), provider=shown)
            return {"title": str(words.get("title") or ""), "fix": REMOTE_TEST_FIX[kind], "technical": technical}
        if kind == "http":
            if status in (401, 403):
                return {"title": f"{head} refused the token", "fix": REMOTE_TEST_FIX["refused"], "technical": technical}
            words = fw.friendly_error(_RemoteProbeFailure(shown, "answered_error", status), provider=shown)
            return {"title": str(words.get("title") or ""), "fix": REMOTE_TEST_FIX["http"], "technical": technical}
        if kind == "shape":
            return {
                "title": f"{head} answered, but not in the shape Iron Jarvis expected",
                "fix": REMOTE_TEST_FIX["shape"],
                "technical": technical,
            }
        return {"title": f"{head} didn't pass the check", "fix": REMOTE_TEST_FIX["other"], "technical": technical}
    except Exception:  # noqa: BLE001 — words are never a new failure
        return {}


#: What a remote's echoed bearer token reads as in its error text.
TOKEN_HIDDEN = "[token hidden]"


def _without_token(text: str, token: str) -> str:
    """*text* with the bearer token THIS request carried taken out (review of
    v1.449.0). A remote that answers 401 with "invalid token <the token>" put
    the user's own credential into ``detail`` — shown raw on the card before,
    folded under Details now — and ``failure_words.mask_failure_text`` knows
    key shapes, not the word the user typed on the card. A token shorter than
    six characters is left (replacing every "t" would wreck the words; the
    mask's Bearer rule still covers the header form)."""
    if not text or not token or len(token) < 6:
        return text
    return text.replace(token, TOKEN_HIDDEN)


def _door_refusal(record: Any, status: int, body: str) -> str:
    """Plain words for another Iron Jarvis's task door turning a task away
    (v1.357.0, G61), or "" when the remote is not such a door (its old
    "remote returned HTTP …" wording stays). The door's own sentence (its
    JSON ``detail``) is quoted, prefixed with the remote's name, so the
    sender's room says WHICH computer refused and why."""
    try:
        from .task_door import is_task_door_url
    except Exception:  # noqa: BLE001
        return ""
    if not is_task_door_url(getattr(record, "base_url", "")):
        return ""
    name = str(getattr(record, "name", "") or "the other computer")
    detail = ""
    try:
        import json as _json

        parsed = _json.loads(body) if body else None
        if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
            detail = " ".join(parsed["detail"].split())[:400]
    except ValueError:
        detail = ""
    if status == 401:
        return (
            f"{name} turned the task away: its token does not match. Copy the token "
            f"again from that computer (Agents, Your team, New & manage, Another "
            f"computer running Jarvis) and paste it into Edit here."
        )
    if status == 429:
        return f"{name} turned the task away: too many tasks this minute. Try again shortly."
    if detail:
        return f"{name} turned the task away: {detail}"
    return ""


class RemoteAgentRecord(SQLModel, table=True):
    """A remote agent endpoint the user registered. The secret lives in the
    vault (``secret_name``), never here."""

    id: str = Field(default_factory=lambda: new_id("rem"), primary_key=True)
    name: str = Field(index=True, unique=True)
    base_url: str = ""
    kind: str = "http-task"  # one of KINDS
    secret_name: str | None = None  # vault key for the bearer token (nullable)
    model: str | None = None  # model id for openai-chat endpoints (nullable)
    enabled: bool = True
    timeout_s: int = 120
    created_at: datetime = Field(default_factory=utcnow)
    #: v1.285.0 — the remote may MESSAGE BACK (progress, questions, results,
    #: files) through ``POST /agents/remote/{name}/inbound`` with its own
    #: inbound token (vault key ``inbound_secret_name``). Off by default.
    #: ``inbound_url`` is the address the remote is told to post to — derived
    #: from the request that enabled it and editable, because a remote on
    #: another machine cannot reach this daemon's 127.0.0.1. Additive columns
    #: (auto-reconciled on boot).
    inbound_enabled: bool = False
    inbound_secret_name: str | None = None
    inbound_url: str = ""


class RemoteAgentRegistry:
    """CRUD + invocation for user-registered remote agents.

    Constructed cheaply from the shared engine; ensures its own table exists so
    it works even before ``init_db`` has seen this module.
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine
        # Self-heal: create the table if init_db hasn't (checkfirst = idempotent).
        try:
            RemoteAgentRecord.__table__.create(engine, checkfirst=True)
        except Exception:  # noqa: BLE001 — already exists / created concurrently
            pass

    # --- CRUD -------------------------------------------------------------

    def list(self) -> list[RemoteAgentRecord]:
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(RemoteAgentRecord)))
            for r in rows:
                db.expunge(r)
        return sorted(rows, key=lambda r: r.name)

    def get(self, name: str) -> RemoteAgentRecord | None:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is not None:
                db.expunge(row)
            return row

    def upsert(
        self,
        name: str,
        base_url: str,
        kind: str,
        *,
        secret_name: str | None = None,
        model: str | None = None,
        enabled: bool = True,
        timeout_s: int = 120,
    ) -> RemoteAgentRecord:
        """Create or update a remote agent (upsert by unique ``name``)."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is None:
                row = RemoteAgentRecord(name=name)
            row.base_url = base_url
            row.kind = kind
            row.secret_name = secret_name
            row.model = model
            row.enabled = enabled
            row.timeout_s = timeout_s
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row

    def update(
        self,
        name: str,
        **fields: Any,
    ) -> "RemoteAgentRecord | None":
        """PARTIAL update — only the fields passed are touched. None if absent.

        Separate from :meth:`upsert` because upsert assigns EVERY column, which
        is right for "register this agent" and wrong for "fix one thing". In
        particular ``row.secret_name = secret_name`` runs unconditionally there,
        so an edit that re-posted the form without re-typing the bearer token —
        which the UI cannot prefill, because the token is stored encrypted and
        never returned — would silently DROP the credential and leave a remote
        that had worked a moment ago failing to authenticate. That is worse than
        the "start from scratch" it was meant to save.

        Callers pass only what changed; ``secret_name`` is therefore omitted to
        keep the existing credential, and passed as ``None`` to clear it.
        """
        allowed = {
            "base_url", "kind", "secret_name", "model", "enabled", "timeout_s",
            "inbound_enabled", "inbound_secret_name", "inbound_url",
        }
        unknown = set(fields) - allowed
        if unknown:  # a typo'd key would silently no-op — refuse instead
            raise ValueError(f"unknown remote-agent field(s): {sorted(unknown)}")
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row

    def remove(self, name: str) -> bool:
        """Delete a remote agent by name; True if a row was removed."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True

    def set_enabled(self, name: str, enabled: bool) -> RemoteAgentRecord | None:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is None:
                return None
            row.enabled = enabled
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row

    # --- inbound: the remote may message back (v1.285.0) ------------------

    def enable_inbound(
        self, name: str, set_secret: Any, *, url: str = ""
    ) -> "tuple[RemoteAgentRecord, str] | None":
        """Mint the remote's INBOUND token, vault it, mark the record.

        Returns ``(record, plaintext token)`` — the token is shown to the user
        ONCE and lives nowhere but the vault afterwards. Re-enabling ROTATES it
        (the previous token stops working at once), which is also the recovery
        path for a token the user lost. ``url`` is what the remote is told to
        post to; empty keeps whatever the record already carries.
        """
        import secrets as _secrets

        token = _secrets.token_urlsafe(32)
        # A separator the remote-name rule (`^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`)
        # cannot produce: "remote_agent_inbound_hermes" would COLLIDE with the
        # OUTBOUND key of a remote named "inbound_hermes" and overwrite (or, on
        # disable, delete) a credential the user cannot retype.
        secret_name = f"remote_agent.inbound.{name}"
        set_secret(secret_name, token, kind="token")
        fields: dict[str, Any] = {"inbound_enabled": True, "inbound_secret_name": secret_name}
        if url:
            fields["inbound_url"] = url
        row = self.update(name, **fields)
        if row is None:
            return None
        return row, token

    def disable_inbound(self, name: str, delete_secret: Any = None) -> "RemoteAgentRecord | None":
        """Turn inbound off and forget the token (the vault entry too)."""
        row = self.get(name)
        if row is None:
            return None
        if row.inbound_secret_name and delete_secret is not None:
            try:
                delete_secret(row.inbound_secret_name)
            except Exception:  # noqa: BLE001 — an absent secret is fine
                pass
        return self.update(name, inbound_enabled=False, inbound_secret_name=None)

    @staticmethod
    def verify_inbound(record: Any, candidate: str | None, secret_resolver: SecretResolver) -> bool:
        """Constant-time check of a remote's inbound token. FAIL-CLOSED: inbound
        off, no vault entry, an empty candidate or a resolver error → False."""
        if record is None or not bool(getattr(record, "inbound_enabled", False)):
            return False
        secret_name = getattr(record, "inbound_secret_name", None)
        if not secret_name or not candidate:
            return False
        try:
            expected = secret_resolver(secret_name) or ""
        except Exception:  # noqa: BLE001 — a vault miss is a refusal
            return False
        if not expected:
            return False
        from ..daemon.auth import token_matches

        return token_matches(str(candidate), str(expected))

    # --- invocation -------------------------------------------------------

    async def run(
        self,
        record: RemoteAgentRecord,
        task: str,
        secret_resolver: SecretResolver,
        *,
        timeout_s: int | None = None,
        history: list[dict[str, Any]] | None = None,
        conversation_id: str = "",
        reply_to: str = "",
        reply_token: str = "",
        local_only: bool = False,
    ) -> dict[str, Any]:
        """Hand ``task`` to a remote agent and relay its reply.

        ``local_only`` (v1.357.0, G61): the chat this task comes from is local
        only, so the receiving Iron Jarvis must not hand it to a model off ITS
        network (it refuses in words instead). Sent as ``"local_only": true``
        on ``http-task`` only when set; the caller sets it only for another
        Iron Jarvis's task door.

        ``reply_token`` (v1.357.0, G61): the key the remote must present when
        it posts its answer to ``reply_to`` — this computer's inbound token
        for that remote. Sent ONLY beside a ``reply_to`` on ``http-task``; the
        caller passes it only to another Iron Jarvis's task door
        (``AgentThreads._speak_remote``), so every other remote's body is
        unchanged.

        Returns ``{ok, result, detail}`` — fail-closed with an honest ``detail``
        on timeout, a non-2xx status, or a reply that doesn't match the shape.
        The secret is resolved here and NEVER logged.

        v1.285.0 — A CONVERSATION: ``history`` (``[{role, content}]``, the
        exchange so far) rides as real prior turns on the OpenAI dialects and
        as ``history`` beside ``task`` on ``http-task``; ``conversation_id``
        and ``reply_to`` (the remote's inbound address, when enabled) ride on
        ``http-task`` so a stateful remote can keep the thread and message
        back. None of them is sent when empty — an older endpoint sees the
        v1.157.0 body byte for byte. A ``202`` (or ``{"accepted": true}`` with
        no result) answers ``{ok: True, accepted: True}``: the remote took the
        task and will report through its inbound endpoint.
        """
        import httpx

        # ``_history_messages`` is a MODULE function on purpose: older tests call
        # ``run`` unbound (``RemoteAgentRegistry.run(None, rec, ...)``), so this
        # method must not reach for ``self``.
        rows = _history_messages(history)
        timeout = timeout_s or record.timeout_s or 120
        token = ""
        if record.secret_name:
            try:
                token = (secret_resolver(record.secret_name) or "") if secret_resolver else ""
            except Exception:  # noqa: BLE001 — a vault miss just means no auth header
                token = ""
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if record.kind == "openai-chat":
            base = (record.base_url or "").rstrip("/")
            url = base if base.endswith("completions") else base + "/chat/completions"
            payload: dict[str, Any] = {
                "model": record.model or "",
                "messages": [*rows, {"role": "user", "content": task}],
            }
        elif record.kind == "openai-responses":
            base = (record.base_url or "").rstrip("/")
            url = base if base.endswith("responses") else base + "/responses"
            # The Responses API takes `input`, not `messages`. The plain-string
            # form is accepted by every implementation of it and avoids the
            # richer content-part shape, which servers disagree about — so it
            # stays the shape for a lone task; a CONVERSATION is the message
            # list form, which every implementation also takes.
            payload = {
                "model": record.model or "",
                "input": [*rows, {"role": "user", "content": task}] if rows else task,
            }
        else:  # http-task (default / unknown kind falls here)
            # v1.357.0 re-review: another Iron Jarvis's door is dialled at its
            # exact leaf (a trailing "/" met the install guard, never the
            # door); every other remote's address is used as typed.
            from .task_door import door_address

            url = door_address(record.base_url or "")
            payload = {"task": task}
            if conversation_id:
                payload["conversation_id"] = conversation_id
            if rows:
                payload["history"] = rows
            if reply_to:
                payload["reply_to"] = reply_to
                if reply_token:
                    payload["reply_token"] = reply_token
            if local_only:
                payload["local_only"] = True

        # v1.288.0 — ``timeout`` is a TOTAL deadline. httpx's ``timeout=`` is
        # per phase (the read timeout is the gap between chunks), so a remote
        # or a gateway trickling keep-alive bytes never tripped it and hung the
        # chat room — and, from the phone, the poller for every channel. Only
        # this scope expiring earns the deadline wording: a TimeoutError raised
        # INSIDE the call keeps its own message (the v1.228.0 lesson).
        deadline = asyncio.timeout(timeout)
        try:
            async with deadline:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except TimeoutError as exc:
            if deadline.expired():
                return {
                    "ok": False,
                    "result": "",
                    "detail": f"{record.name} did not answer within {timeout}s",
                    "kind": "timeout",
                }
            return {"ok": False, "result": "", "detail": f"request failed: {exc}", "kind": "unreachable"}
        except Exception as exc:  # noqa: BLE001 — timeout / connection / DNS
            return {"ok": False, "result": "", "detail": f"request failed: {exc}", "kind": "unreachable"}

        status = getattr(resp, "status_code", 0)
        if status == 202:
            # "I have it; I'll message you back" — honest, and only meaningful
            # when the remote CAN message back; the caller says which.
            return {"ok": True, "accepted": True, "result": "", "detail": "accepted", "files": []}
        if status // 100 != 2:
            snippet = ""
            body_text = ""
            try:
                # Review of v1.449.0: a body that echoes the token we sent is
                # never carried into ``detail`` (the card, the chat room, the
                # phone and the log all read it).
                body_text = _without_token((resp.text or "")[:2000], token)
                snippet = body_text[:200]
            except Exception:  # noqa: BLE001
                snippet = ""
            door_words = _door_refusal(record, status, body_text)
            if door_words:
                # v1.357.0 (G61): another Iron Jarvis said why, in words.
                return {"ok": False, "result": "", "detail": door_words, "kind": "door", "status": status}
            detail = f"remote returned HTTP {status}"
            if snippet:
                detail += f": {snippet}"
            hint = _dialect_hint(record.kind, snippet)
            if hint:
                detail += f" — {hint}"
            return {"ok": False, "result": "", "detail": detail, "kind": "http", "status": status}

        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 — not JSON
            return {"ok": False, "result": "", "detail": "remote returned a non-JSON body", "kind": "shape"}

        if (
            isinstance(data, dict)
            and data.get("accepted") is True
            and not str(data.get("result") or data.get("output") or "").strip()
        ):
            return {"ok": True, "accepted": True, "result": "", "detail": "accepted", "files": []}

        if record.kind == "openai-chat":
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = None
            if not isinstance(content, str) or not content.strip():
                return {
                    "ok": False,
                    "result": "",
                    "kind": "shape",
                    "detail": "remote reply had no choices[0].message.content",
                }
            return {
                "ok": True,
                "result": content,
                "detail": "ok",
                "files": _remote_files.parse_files(data),
            }

        if record.kind == "openai-responses":
            content = _responses_text(data)
            if not content.strip():
                return {
                    "ok": False,
                    "result": "",
                    "kind": "shape",
                    "detail": "remote reply had no output_text / output[].content[].text",
                }
            return {
                "ok": True,
                "result": content,
                "detail": "ok",
                "files": _remote_files.parse_files(data),
            }

        # http-task: accept {result} or {output}
        result = None
        if isinstance(data, dict):
            result = data.get("result")
            if result is None:
                result = data.get("output")
        if not isinstance(result, str):
            return {
                "ok": False,
                "result": "",
                "kind": "shape",
                "detail": "remote reply had no 'result' or 'output' string field",
            }
        return {
            "ok": True,
            "result": result,
            "detail": "ok",
            # v1.157.0: the reply may carry real files. Parsed here (bounded,
            # never decoded yet) and turned into bytes by the TOOL, which is
            # the only place that knows the workspace they may land in.
            "files": _remote_files.parse_files(data),
        }

    async def test(
        self, record: RemoteAgentRecord, secret_resolver: SecretResolver
    ) -> dict[str, Any]:
        """Cheap reachability probe: ask the remote to reply 'ok', short timeout.

        Another Iron Jarvis's task door (v1.357.0, G61) is ASKED, not handed
        work: a GET on the door with the token answers whether it would take
        a task — a throwaway "reply ok" task would start a real run there."""
        from .task_door import is_task_door_url

        if is_task_door_url(record.base_url):
            return await self._test_door(record, secret_resolver)
        probe = await self.run(
            record,
            "reply with the single word: ok",
            secret_resolver,
            timeout_s=min(record.timeout_s or 120, 15),
        )
        words = remote_test_words(record.name, probe)
        if probe.get("ok"):
            return {"ok": True, "detail": f"{record.name} replied", **words}
        return {"ok": False, "detail": probe.get("detail") or "probe failed", **words}

    async def _test_door(
        self, record: RemoteAgentRecord, secret_resolver: SecretResolver
    ) -> dict[str, Any]:
        """Ask another Iron Jarvis's task door whether it takes tasks from this
        computer (token checked, nothing started). Bounded; never raises."""
        import httpx

        from .task_door import door_address

        token = ""
        if record.secret_name:
            try:
                token = (secret_resolver(record.secret_name) or "") if secret_resolver else ""
            except Exception:  # noqa: BLE001
                token = ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        timeout = min(record.timeout_s or 120, 15)
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(door_address(record.base_url), headers=headers)
        except Exception:  # noqa: BLE001 — timeout / connection / DNS
            return {
                "ok": False,
                "detail": f"{record.name} could not be reached. Check its address, and "
                "that the other computer is open to this one (its card says how).",
            }
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            return {
                "ok": True,
                "detail": f"{record.name} is another computer running Iron Jarvis and takes tasks from here.",
            }
        try:
            body = (resp.text or "")[:2000]
        except Exception:  # noqa: BLE001
            body = ""
        words = _door_refusal(record, status, body)
        return {
            "ok": False,
            "detail": words or f"{record.name} answered, but did not say it takes tasks from here.",
        }


#: v1.420.0 (C6.3, G62): what a remote that ACCEPTED a job (a ``202`` or
#: ``{"accepted": true}`` with no result) has done, said for what it is — never
#: an empty success. The delegate tools answer it, the mission card says it,
#: the handoff room keeps it as a ``pending`` entry (v1.285.0's flag).
ACCEPTED_TAIL = "accepted the task and will report back"


def accepted_words(name: str) -> str:
    """"<name> accepted the task and will report back." (v1.420.0, G62)."""
    return f"{name} {ACCEPTED_TAIL}."


def accepted_no_reply_words(name: str) -> str:
    """The same when its answer CANNOT come back (message-back is off): said
    with the switch that fixes it, never "will report back"."""
    return (
        f"{name} accepted the task, but its answer cannot come back here: turn on "
        "Let it message back on its card to get it."
    )


async def open_handoff_room(
    platform: Any, record: Any, task: str, chat_thread_id: str = ""
) -> dict[str, Any]:
    """A room the remote can REPORT BACK into, for one handed-over job
    (v1.420.0, C6.3, G62). A remote that answers ``202`` delivers its report
    later through ``POST /agents/remote/{name}/inbound``, which only accepts a
    room the remote sits in — and a delegated job had no room, so the report
    could never land anywhere. Opened ONLY when the remote may message back
    (inbound on, an address known); the task is the room's first line.

    Returns ``{room_id, kwargs}`` — ``kwargs`` for
    :meth:`RemoteAgentRegistry.run` (``conversation_id``, ``reply_to`` and, for
    another Iron Jarvis's task door, ``reply_token`` / ``local_only``) — or
    ``{}`` when the remote cannot message back (the call is then sent exactly
    as before). Never raises: a room that cannot be opened sends the job
    without one.

    ``chat_thread_id`` (readiness 0j.5 item 10) is the chat the job was asked
    from — an agent run's ``options["chat_thread_id"]`` or a chat turn's own
    thread, both carried as ``ToolContext.thread_id``. The room records it as
    its ``home_chat_id`` so the remote's later report is appended to THAT chat
    by the inbound route; ``""`` keeps the old behaviour (the report stays in
    the room)."""
    from types import SimpleNamespace

    try:
        from .threads import AgentThreads

        reply_to = AgentThreads._inbound_url(record)
        if not reply_to:
            return {}
        name = str(getattr(record, "name", "") or "remote")
        threads = AgentThreads(platform.engine)
        participants = [
            {"key": f"remote:{name}", "source": "remote", "name": name, "role": "helper"}
        ]
        title = " ".join(str(task or "").split())[:60] or "a task"
        room = await asyncio.to_thread(
            threads.create,
            f"{name}: {title}",
            participants,
            "",
            str(chat_thread_id or "").strip(),
        )
        await asyncio.to_thread(
            threads._append,
            room.id,
            [{"who": "user", "role": "user", "source": "user",
              "content": str(task or ""), "at": utcnow().isoformat()}],
        )
        d = SimpleNamespace(platform=platform)
        kwargs: dict[str, Any] = {"conversation_id": room.id, "reply_to": reply_to}
        token = await AgentThreads._reply_token_for(record, d)
        if token:
            kwargs["reply_token"] = token
        if AgentThreads._local_only_for_door(record):
            kwargs["local_only"] = True
        return {"room_id": room.id, "kwargs": kwargs}
    except Exception:  # noqa: BLE001 — a job without a room is the old behaviour
        import logging

        logging.getLogger(__name__).warning(
            "could not open a report-back room for a remote job", exc_info=True
        )
        return {}


async def note_accepted_in_room(platform: Any, room_id: str, name: str) -> None:
    """The handoff room's ``pending`` line (v1.285.0's flag): the remote has
    the job. Never raises."""
    if not room_id:
        return
    try:
        from .threads import AgentThreads

        threads = AgentThreads(platform.engine)
        await asyncio.to_thread(
            threads._append,
            room_id,
            [{"who": f"remote:{name}", "role": "participant", "source": "remote",
              "content": accepted_words(name), "at": utcnow().isoformat(),
              "pending": True}],
        )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).debug("could not note an accepted job", exc_info=True)


class DelegateRemoteTool(Tool):
    """Delegate a task to a registered remote agent running elsewhere."""

    name = "delegate_remote"
    description = (
        "Hand a task to a REMOTE agent the user registered on another machine "
        "(their own Hermes/OpenClaw or any OpenAI-compatible endpoint) and "
        "return its reply. Args: agent (the registered remote agent's name) and "
        "task (the self-contained instruction)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "task": {"type": "string"},
        },
        "required": ["agent", "task"],
    }
    permission_key = "delegate_remote"
    #: The remote's reply is externally-sourced — fence it as untrusted content.
    returns_untrusted_content = True

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        agent = (args.get("agent") or "").strip()
        task = args.get("task") or ""
        if not agent:
            return ToolResult(ok=False, error="`agent` is required")
        registry = RemoteAgentRegistry(self.platform.engine)
        record = registry.get(agent)
        if record is None:
            return ToolResult(ok=False, error=f"unknown remote agent '{agent}'")
        if not record.enabled:
            return ToolResult(ok=False, error=f"remote agent '{agent}' is disabled")
        room = await open_handoff_room(
            self.platform, record, task, getattr(ctx, "thread_id", "") or ""
        )
        if room:
            res = await registry.run(record, task, self.platform.secrets.get, **room["kwargs"])
        else:
            res = await registry.run(record, task, self.platform.secrets.get)
        if res.get("ok") and res.get("accepted"):
            # v1.420.0 (C6.3, G62): the remote TOOK the job and will report
            # later — a distinct outcome, never an empty success.
            room_id = str(room.get("room_id") or "")
            await note_accepted_in_room(self.platform, room_id, agent)
            return ToolResult(
                ok=True,
                output=accepted_words(agent) if room_id else accepted_no_reply_words(agent),
                data={
                    "agent": agent,
                    "kind": record.kind,
                    "state": "accepted",
                    "conversation_id": room_id,
                    "reply_back": bool(room_id),
                },
            )
        if res.get("ok"):
            # FILES the remote sent back (v1.157.0). Written HERE because this
            # is the only place that knows the workspace they may land in, and
            # every name goes through safe_path so the workspace stays a hard
            # boundary for bytes chosen by another machine.
            saved, notes = await self._save_files(res.get("files") or [], record, ctx)
            output = res.get("result") or ""
            if saved:
                listed = "\n".join(f"- {p}" for p in saved)
                output += f"\n\nFiles returned by {agent}:\n{listed}"
            if notes:
                # Said out loud: "the remote sent 3 files and you got 2" is
                # exactly the thing a user must not discover later.
                refused = "\n".join(f"- {n}" for n in notes)
                output += f"\n\nNot saved:\n{refused}"
            return ToolResult(
                ok=True,
                output=output,
                data={
                    "agent": agent,
                    "kind": record.kind,
                    "documents": saved,
                    "skipped": notes,
                },
                created_paths=saved,
            )
        return ToolResult(
            ok=False,
            error=res.get("detail") or "remote agent call failed",
            data={"agent": agent, "kind": record.kind},
        )


    async def _save_files(self, entries, record, ctx) -> "tuple[list[str], list[str]]":
        """Land the remote's files in the workspace. Returns (absolute paths, refusals).

        Never raises: a delegation that produced a good answer must not fail
        because one attachment was malformed.
        """
        if not entries:
            return [], []
        import httpx

        from ..tools.base import safe_path
        from . import remote_files as rf

        async def _fetch(url: str) -> bytes:
            async with httpx.AsyncClient(timeout=min(record.timeout_s or 120, 60)) as c:
                r = await c.get(url)
                r.raise_for_status()
                blob = r.content
                if len(blob) > rf.MAX_FILE_BYTES:
                    raise ValueError("too large")
                return blob

        try:
            files, notes = await rf.collect(
                entries, base_url=record.base_url or "", fetch=_fetch
            )
        except Exception as exc:  # noqa: BLE001
            return [], [f"could not read the reply's files ({type(exc).__name__})"]

        saved: list[str] = []
        for name, blob in files:
            try:
                # safe_path is the second lock: even a name that survived
                # sanitising cannot resolve outside the workspace.
                target = rf.unique_path(Path(ctx.workspace), name)
                checked = safe_path(ctx.workspace, target.name)
                checked.parent.mkdir(parents=True, exist_ok=True)
                checked.write_bytes(blob)
                saved.append(str(checked.resolve()))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{name}: could not be written ({type(exc).__name__})")
        return saved, notes


def register_remote_agent_tool(platform) -> None:
    """Register the ``delegate_remote`` tool on the platform's tool registry.

    Coordinator wiring (platform.py, one line near the DelegateTool register):
        ``from .agents.remote import register_remote_agent_tool``
        ``register_remote_agent_tool(platform)``
    """
    platform.registry.register(DelegateRemoteTool(platform))
