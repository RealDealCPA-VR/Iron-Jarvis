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
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow

#: Preset roles offered at setup (free text is equally valid). Kept short and
#: functional — the role is injected into the agent's system prompt.
ROLE_PRESETS = ("lead", "researcher", "critic", "builder", "reviewer", "scribe")

#: Participant sources. "builtin" = a core AgentType; "dynamic" = a user-created
#: agent (its persona + preferred model apply); "remote" = a registered remote
#: agent reached over HTTP. All three speak in the same thread.
SOURCES = ("builtin", "dynamic", "remote")

_MAX_MESSAGES = 400  # per thread; oldest trimmed (matches chat's cap spirit)
_TRANSCRIPT_CHARS = 24_000  # context handed to each speaker, newest kept


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

    def _append(self, thread_id: str, entries: list[dict[str, Any]]) -> None:
        with session_scope(self.engine) as db:
            rec = db.get(AgentThreadRecord, thread_id)
            if rec is None:
                return
            msgs = json.loads(rec.messages_json or "[]")
            msgs.extend(entries)
            rec.messages_json = json.dumps(msgs[-_MAX_MESSAGES:])
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()

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

    async def run_round(self, thread_id: str, user_message: str, d: Any) -> dict[str, Any]:
        """One round: persist the user turn, then each participant speaks in
        panel order. Returns the new entries. Provider failures become honest
        error entries; the round always completes."""
        rec = self.get(thread_id)
        if rec is None:
            raise KeyError(thread_id)
        participants = json.loads(rec.participants_json or "[]")
        if not participants:
            raise ValueError("this thread has no participant agents")
        messages = json.loads(rec.messages_json or "[]")

        new_entries: list[dict[str, Any]] = []
        if (user_message or "").strip():
            user_entry = {
                "who": "user",
                "content": user_message.strip(),
                "at": utcnow().isoformat(),
            }
            messages.append(user_entry)
            new_entries.append(user_entry)

        for p in participants:
            others = [o for o in participants if o["key"] != p["key"]]
            transcript = self._transcript(messages, participants)
            entry: dict[str, Any] = {
                "who": p["key"],
                "role": p["role"],
                "source": p["source"],
                "at": utcnow().isoformat(),
            }
            try:
                if p["source"] == "remote":
                    reply = await self._speak_remote(p, transcript, d)
                else:
                    reply = await self._speak_local(p, others, transcript, d)
                entry["content"] = reply
            except Exception as exc:  # noqa: BLE001 — honest error, round continues
                entry["content"] = ""
                entry["error"] = f"{p['name']} couldn't answer: {str(exc)[:300]}"
            messages.append(entry)
            new_entries.append(entry)

        self._append(thread_id, new_entries)
        return {"entries": new_entries}

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

    async def _speak_remote(self, p: dict[str, str], transcript: str, d: Any) -> str:
        """A remote participant answers over its registered transport."""
        from .remote import RemoteAgentRegistry

        registry = RemoteAgentRegistry(d.platform.engine)
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
