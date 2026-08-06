"""Agent threads — conversations organized BY AGENT, with cross-source panels.

The Agents page's unit of work is a THREAD: a persistent conversation whose
participants are agents from any approved source — built-in agent types,
user-created dynamic agents, and registered remote agents — each carrying a
ROLE assigned when it joins ("lead", "critic", "researcher", or free text).

A user message triggers one speaking ROUND: every participant answers in
panel order, each seeing the full transcript so far INCLUDING the answers of
the agents before it in the round — that ordering is what makes it a
conversation between agents rather than N parallel answers. A participant
whose provider fails contributes an honest error entry, never a fabricated
reply, and never sinks the rest of the round.

Rounds are LIVE (v1.140.0): every entry is persisted atomically AS IT LANDS
(the user turn first, then each speaker), and each persisted entry publishes
:data:`EventType.AGENT_THREAD_UPDATED` best-effort so an open Agents-page
thread renders the round while it unfolds instead of after it. Rounds are
DIRECTED with @-mentions (see :meth:`AgentThreads.run_round`), and a remote
participant whose registration is disabled or gone is skipped with an honest
entry instead of a doomed network call.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import new_id, utcnow
from ..core.logging import get_logger

log = get_logger("agents.threads")

#: Preset roles offered at setup (free text is equally valid). Kept short and
#: functional — the role is injected into the agent's system prompt.
ROLE_PRESETS = ("lead", "researcher", "critic", "builder", "reviewer", "scribe")

#: Participant sources. "builtin" = a core AgentType; "dynamic" = a user-created
#: agent (its persona + preferred model apply); "remote" = a registered remote
#: agent reached over HTTP. All three speak in the same thread.
SOURCES = ("builtin", "dynamic", "remote")

_MAX_MESSAGES = 400  # per thread; oldest trimmed (matches chat's cap spirit)
_TRANSCRIPT_CHARS = 24_000  # context handed to each speaker, newest kept

#: An @-mention in the user's message: ``@"quoted name"`` (for names with
#: spaces) or a bare token of letters/digits/``._-`` — so ``@hermes-mac-mini``
#: works as-is. Two naturalness guards: the ``@`` must not be glued to the
#: tail of a word (``planner@critic`` / ``email@example.com`` are addresses,
#: not mentions — the lookbehind rejects a preceding letter/digit/``._-``),
#: and bare tokens are trimmed of trailing ``.``/``_``/``-`` so a sentence-
#: ending ``@builder.`` still targets ``builder`` (quoted names match
#: verbatim). Matching is case-insensitive against each participant's name,
#: role, and the name part of its key (see :meth:`AgentThreads.run_round`).
_MENTION_RE = re.compile(
    r'(?<![A-Za-z0-9._-])@(?:"([^"]+)"|([A-Za-z0-9][A-Za-z0-9._-]*))'
)

#: Serializes the ``messages_json`` read-modify-write. MODULE scope on purpose:
#: routes construct a fresh :class:`AgentThreads` per request, so an instance
#: lock would guard nothing — this is what keeps two simultaneous ``/say``
#: rounds on one thread appending whole entries instead of interleaving a
#: corrupt JSON blob (same pattern as ``comm/threads.py``'s store lock).
_APPEND_LOCK = threading.Lock()


class AgentThreadRecord(SQLModel, table=True):
    """One persistent multi-agent conversation."""

    id: str = Field(default_factory=lambda: new_id("athr"), primary_key=True)
    title: str = ""
    #: JSON list of participants:
    #: {key, source, name, role, provider?, model?} — ``key`` is
    #: "<source>:<name>" and unique within the thread.
    participants_json: str = "[]"
    #: JSON list of messages: {who, role, source, content, at, error?} — ``who``
    #: is "user" or the participant key.
    messages_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def participant_key(source: str, name: str) -> str:
    return f"{source}:{name}"


def clean_participants(raw: Any) -> list[dict[str, str]]:
    """Validate a participants payload; raises ValueError with a plain reason."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("at least one participant agent is required")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each participant must be an object")
        source = str(item.get("source") or "").strip()
        name = str(item.get("name") or "").strip()
        role = str(item.get("role") or "").strip()
        if source not in SOURCES:
            raise ValueError(f"unknown agent source {source!r} (one of {SOURCES})")
        if not name:
            raise ValueError("participant name is required")
        key = participant_key(source, name)
        if key in seen:
            raise ValueError(f"{name} is already in this thread")
        seen.add(key)
        out.append(
            {
                "key": key,
                "source": source,
                "name": name,
                "role": role or "participant",
                "provider": str(item.get("provider") or "").strip(),
                "model": str(item.get("model") or "").strip(),
            }
        )
    return out


class AgentThreads:
    """Store + the speaking-round engine."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        try:
            AgentThreadRecord.__table__.create(engine, checkfirst=True)
        except Exception:  # noqa: BLE001 — exists / created concurrently
            pass

    # -- CRUD ----------------------------------------------------------------

    def create(self, title: str, participants: list[dict[str, str]]) -> AgentThreadRecord:
        rec = AgentThreadRecord(
            title=(title or "").strip() or "Agent thread",
            participants_json=json.dumps(participants),
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return rec

    def list(self) -> list[AgentThreadRecord]:
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(AgentThreadRecord)))
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    def get(self, thread_id: str) -> AgentThreadRecord | None:
        with session_scope(self.engine) as db:
            return db.get(AgentThreadRecord, thread_id)

    def delete(self, thread_id: str) -> bool:
        with session_scope(self.engine) as db:
            rec = db.get(AgentThreadRecord, thread_id)
            if rec is None:
                return False
            db.delete(rec)
            db.commit()
        return True

    def update_participants(
        self, thread_id: str, participants: list[dict[str, str]]
    ) -> AgentThreadRecord | None:
        with session_scope(self.engine) as db:
            rec = db.get(AgentThreadRecord, thread_id)
            if rec is None:
                return None
            rec.participants_json = json.dumps(participants)
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return rec

    def _append(self, thread_id: str, entries: list[dict[str, Any]]) -> int:
        """Atomic append; returns the thread's NEW total message count.

        One read→extend→commit inside one session under the module lock —
        ``run_round`` calls this once PER ENTRY so a watching client sees the
        round unfold, and two concurrent rounds on the same thread interleave
        whole entries instead of corrupting the blob. Returns 0 when the
        thread vanished mid-round (deleted underneath us — nothing to keep).
        """
        with _APPEND_LOCK, session_scope(self.engine) as db:
            rec = db.get(AgentThreadRecord, thread_id)
            if rec is None:
                return 0
            msgs = json.loads(rec.messages_json or "[]")
            msgs.extend(entries)
            msgs = msgs[-_MAX_MESSAGES:]
            rec.messages_json = json.dumps(msgs)
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()
            return len(msgs)

    # -- the speaking round ---------------------------------------------------

    @staticmethod
    def _transcript(messages: list[dict[str, Any]], participants: list[dict]) -> str:
        """The conversation so far, as plain labelled turns (newest kept)."""
        names = {p["key"]: f"{p['name']} ({p['role']})" for p in participants}
        lines = []
        for m in messages:
            who = m.get("who") or "user"
            label = "User" if who == "user" else names.get(who, who)
            content = str(m.get("content") or "")
            if content:
                lines.append(f"{label}: {content}")
        text = "\n\n".join(lines)
        return text[-_TRANSCRIPT_CHARS:]

    @staticmethod
    def _system_for(p: dict[str, str], others: list[dict[str, str]], base_prompt: str) -> str:
        panel = ", ".join(f"{o['name']} ({o['role']})" for o in others) or "nobody else"
        role_line = (
            f"You are {p['name']}, the {p['role']} in a panel conversation with "
            f"{panel} and the user. Speak AS your role: contribute what a "
            f"{p['role']} should, respond to the other panelists by name when you "
            "agree or disagree, and keep it under ~200 words. Never speak for "
            "the others or fabricate their views."
        )
        return f"{base_prompt.strip()}\n\n{role_line}" if base_prompt.strip() else role_line

    @staticmethod
    def _mentioned(user_message: str, participants: list[dict]) -> list[dict]:
        """Participants the user @-mentioned, in PANEL order (never mention
        order). A mention token matches a participant when it equals — case-
        insensitively — the participant's name, its role, or the name part of
        its key ("<source>:<name>"). ``[]`` when no token matched anyone.

        Bare tokens shed trailing ``.``/``_``/``-`` before matching (sentence
        punctuation — ``@builder.`` means builder); quoted tokens are taken
        verbatim, so a name that really ends in punctuation is still
        addressable as ``@"name."``. A mid-word ``@`` (an email address, an
        identifier) never produces a token at all — see :data:`_MENTION_RE`.
        """
        tokens = [
            (quoted if quoted else bare.rstrip("._-")).strip().lower()
            for quoted, bare in _MENTION_RE.findall(user_message or "")
        ]
        tokens = [t for t in tokens if t]
        if not tokens:
            return []
        selected: list[dict] = []
        for p in participants:
            aliases = {
                str(p.get("name") or "").lower(),
                str(p.get("role") or "").lower(),
                str(p.get("key") or "").split(":", 1)[-1].lower(),
            }
            aliases.discard("")
            if any(t in aliases for t in tokens):
                selected.append(p)
        return selected

    @staticmethod
    async def _publish_updated(thread_id: str, who: str, entries: int, d: Any) -> None:
        """Best-effort AGENT_THREAD_UPDATED after each persisted entry — a bus
        failure is logged and swallowed; it must never break the round."""
        try:
            await d.platform.event_bus.publish(
                EventType.AGENT_THREAD_UPDATED,
                {"thread_id": thread_id, "who": who, "entries": entries},
            )
        except Exception:  # noqa: BLE001 — an event must never sink a round
            log.warning("agent_thread.updated publish failed", exc_info=True)

    async def run_round(self, thread_id: str, user_message: str, d: Any) -> dict[str, Any]:
        """One round: persist the user turn, then each speaker in panel order.

        LIVE: each entry is persisted atomically as it lands (not batched at
        the end) and publishes AGENT_THREAD_UPDATED {thread_id, who, entries}
        best-effort, so an open thread renders the round mid-flight.

        DIRECTED: @-mentions in the user message pick who speaks. A mention
        (``@planner``, ``@critic``, ``@hermes-mac-mini``, ``@"Quoted Name"``)
        matches a participant by name, role, or its key's name part, case-
        insensitively; trailing sentence punctuation is forgiven
        (``@builder.`` targets builder) and a mid-word ``@`` is never a
        mention (``planner@critic.io`` in the message directs nobody). If at
        least one participant is mentioned, ONLY the
        mentioned ones speak this round — in panel order, regardless of
        mention order; mentions matching nobody are simply ignored. Zero
        mentions (or none matching anyone) → everyone speaks, exactly as
        before. The message text stays verbatim in the transcript either way.

        A remote participant whose registration is disabled or missing is
        SKIPPED with an honest error entry instead of a doomed network call;
        an enabled remote that fails keeps the normal honest-error path.

        Returns {"entries": [the new entries], "spoke": [keys that spoke —
        honest errors included], "skipped": [keys skipped as offline]}.
        Provider failures become honest error entries; the round always
        completes.
        """
        rec = self.get(thread_id)
        if rec is None:
            raise KeyError(thread_id)
        participants = json.loads(rec.participants_json or "[]")
        if not participants:
            raise ValueError("this thread has no participant agents")
        messages = json.loads(rec.messages_json or "[]")

        new_entries: list[dict[str, Any]] = []
        spoke: list[str] = []
        skipped: list[str] = []
        if (user_message or "").strip():
            user_entry = {
                "who": "user",
                "content": user_message.strip(),
                "at": utcnow().isoformat(),
            }
            messages.append(user_entry)
            new_entries.append(user_entry)
            count = self._append(thread_id, [user_entry])
            await self._publish_updated(thread_id, "user", count, d)

        speakers = self._mentioned(user_message or "", participants) or participants

        for p in speakers:
            others = [o for o in participants if o["key"] != p["key"]]
            transcript = self._transcript(messages, participants)
            entry: dict[str, Any] = {
                "who": p["key"],
                "role": p["role"],
                "source": p["source"],
                "at": utcnow().isoformat(),
            }
            remote_record = None
            if p["source"] == "remote":
                remote_record = self._remote_record(p["name"], d)
                if remote_record is None or not remote_record.enabled:
                    # Offline fast-path: an honest skip beats a doomed call.
                    state = "disabled" if remote_record is not None else "not registered"
                    entry["content"] = ""
                    entry["error"] = f"{p['name']} is offline ({state}) — skipped."
                    skipped.append(p["key"])
                    messages.append(entry)
                    new_entries.append(entry)
                    count = self._append(thread_id, [entry])
                    await self._publish_updated(thread_id, p["key"], count, d)
                    continue
            try:
                if p["source"] == "remote":
                    reply = await self._speak_remote(p, transcript, d, record=remote_record)
                else:
                    reply = await self._speak_local(p, others, transcript, d)
                entry["content"] = reply
            except Exception as exc:  # noqa: BLE001 — honest error, round continues
                entry["content"] = ""
                entry["error"] = f"{p['name']} couldn't answer: {str(exc)[:300]}"
            spoke.append(p["key"])
            messages.append(entry)
            new_entries.append(entry)
            count = self._append(thread_id, [entry])
            await self._publish_updated(thread_id, p["key"], count, d)

        return {"entries": new_entries, "spoke": spoke, "skipped": skipped}

    async def _speak_local(
        self, p: dict[str, str], others: list[dict], transcript: str, d: Any
    ) -> str:
        """A builtin/dynamic participant answers via the one-shot LLM path
        (retry + cross-provider failover — the same path terminal assist uses)."""
        base_prompt = ""
        provider = p.get("provider") or ""
        model = p.get("model") or ""
        if p["source"] == "dynamic":
            row = d.platform.agents_registry.get(p["name"])
            if row is None:
                raise RuntimeError(f"dynamic agent {p['name']!r} no longer exists")
            base_prompt = row.system_prompt or ""
            provider = provider or row.provider or ""
            model = model or row.model or ""
        else:
            base_prompt = (
                f"You are a {p['name']} agent: answer with the judgement and "
                f"focus of a {p['name']}."
            )
        provider = provider or d.platform.config.default_provider
        model = model or d.platform.config.default_model
        adapter = d.platform.providers.get(provider, model)
        from ..providers.adapters.base import LLMMessage

        resp, _p, _m = await d._one_shot_complete(
            provider,
            adapter,
            system=self._system_for(p, others, base_prompt),
            messages=[LLMMessage(role="user", content=transcript or "(no messages yet)")],
        )
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("the model returned an empty reply")
        return text

    @staticmethod
    def _remote_record(name: str, d: Any) -> Any:
        """The remote agent's registration row, or None (missing / DB hiccup).

        Loaded ONCE per speaker in ``run_round`` for the offline check, then
        handed to ``_speak_remote`` so the row isn't fetched twice.
        """
        from .remote import RemoteAgentRegistry

        try:
            return RemoteAgentRegistry(d.platform.engine).get(name)
        except Exception:  # noqa: BLE001 — unreadable registry == offline
            return None

    async def _speak_remote(
        self, p: dict[str, str], transcript: str, d: Any, record: Any = None
    ) -> str:
        """A remote participant answers over its registered transport."""
        from .remote import RemoteAgentRegistry

        registry = RemoteAgentRegistry(d.platform.engine)
        if record is None:
            record = registry.get(p["name"])
        if record is None:
            raise RuntimeError(f"remote agent {p['name']!r} is not registered")
        task = (
            f"You are {p['name']}, the {p['role']} on a panel. Read the "
            f"conversation and contribute your {p['role']} perspective (under "
            f"~200 words):\n\n{transcript or '(no messages yet)'}"
        )
        out = await registry.run(record, task, d.platform.secrets.get)
        if not out.get("ok"):
            raise RuntimeError(out.get("detail") or "remote agent failed")
        return str(out.get("result") or "").strip()
