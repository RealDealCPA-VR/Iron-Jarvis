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

A PANELIST IS THE AGENT IT CLAIMS TO BE (v1.193.0): a local speaker carries the
real system prompt of the agent behind the seat — ``types._DEFINITIONS`` for a
builtin, the registry's COMPOSED definition (identity anchor included) for a
dynamic one — plus :data:`PANEL_NO_TOOLS`, because a panelist speaks with
``tools=[]`` and a real agent prompt talks about tools it cannot reach here.

A round's WORTH OUTLIVES THE ROUND (v1.178.0): :meth:`AgentThreads.remember`
commits a panel to long-term memory the way ``POST /chat/threads/{id}/remember``
commits a chat — same distill/verbatim ladder, same honest-mock refusal, same
LTM front door — and defaults to a PREVIEW, so what a panel concluded is
reviewed before it becomes something the app quotes back later as fact.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel, select

from ..core.db import CONVERSATION_WRITE_LOCK, session_scope
from ..core.events import EventType
from ..core.ids import new_id, utcnow
from ..core.logging import get_logger
from ..memory import commit as _commit

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

#: THE PANELIST IS THE REAL AGENT, AND THE REAL AGENT HAS NO HANDS HERE
#: (v1.193.0). A local panelist now carries the SAME system prompt its agent
#: sessions run on (builtin: ``types._DEFINITIONS``; dynamic: the registry's
#: COMPOSED definition, identity anchor included) — before this, a "reviewer"
#: seat was a model told to sound like one, and a named custom agent was never
#: told who it was. But a panelist speaks through ``d._one_shot_complete``,
#: which calls the adapter with ``tools=[]``: those real prompts instruct the
#: agent to read files, run shell, delegate. Handing them to a tool-less
#: speaker without saying so invites it to CLAIM it acted, and a fabricated
#: action is the one thing this codebase refuses. So the identity arrives WITH
#: this correction, always last in :meth:`AgentThreads._system_for` so it is
#: the final word on what the prompt above it asked for.
PANEL_NO_TOOLS = (
    "IN THIS ROOM YOU HAVE NO TOOLS. The instructions above describe the tools "
    "you use in a normal working session — reading and writing files, running "
    "commands, delegating, searching memory. NONE of them are available in this "
    "panel and nothing you write here is executed. You ADVISE; you do not act. "
    "So never say or imply that you read, wrote, ran, delegated, checked or "
    "looked anything up while answering: say what you would do, what you would "
    "need, and what you already know. If the question can only be settled by "
    "actually running something, say so plainly and hand it back."
)

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
#:
#: v1.142.0: it IS that store's lock now. A round append also rewrites this
#: thread's history-search docs, and three separate locks around three
#: transactions that each take ``SearchIndex``' internal lock deadlock against
#: SQLite's single writer (66s, two lost writes, measured). See
#: ``core.db.CONVERSATION_WRITE_LOCK``.
_APPEND_LOCK = CONVERSATION_WRITE_LOCK


class AgentThreadRecord(SQLModel, table=True):
    """One persistent multi-agent conversation."""

    id: str = Field(default_factory=lambda: new_id("athr"), primary_key=True)
    title: str = ""
    #: The CHAT thread that started this panel (v1.150.0), when it began with an
    #: ``@mention`` in chat rather than on the Agents page. One panel per chat
    #: thread, so mentioning a new agent mid-conversation ADDS them to the same
    #: room instead of opening a second one where they cannot see what was
    #: already said. Empty = created on the Agents page. Additive column.
    chat_thread_id: str = Field(default="", index=True)
    #: JSON list of participants:
    #: {key, source, name, role, provider?, model?} — ``key`` is
    #: "<source>:<name>" and unique within the thread.
    participants_json: str = "[]"
    #: JSON list of messages: {who, role, source, content, at, error?} — ``who``
    #: is "user" or the participant key.
    messages_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def parse_mentions(text: str) -> list[str]:
    """The ``@names`` in *text*, lowercased, in order, deduplicated.

    Public because the CHAT lane needs the same answer the round does: chat
    resolves mentions against the ROSTER to decide who joins the panel, then
    ``run_round`` matches them against the panel's participants to decide who
    speaks. Two regexes would be two definitions of "is that a mention", and
    they would disagree on exactly the awkward inputs this one is careful
    about (``email@example.com``, ``@builder.``, ``@"Two Words"``).
    """
    out: list[str] = []
    for quoted, bare in _MENTION_RE.findall(text or ""):
        token = (quoted if quoted else bare.rstrip("._-")).strip().lower()
        if token and token not in out:
            out.append(token)
    return out


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


# -- committing a panel to long-term memory (v1.178.0) ----------------------- #
#
# WHAT IS SHARED WITH CHAT AND WHAT IS NOT. Both surfaces now call ONE ladder,
# :func:`memory.commit.distill_or_excerpt` — the budgets, the clip contract, the
# mock refusal, the failover hop, the one-shot call and the degrade-don't-refuse
# outcome. v1.178.0 could only share the BUDGETS (by importing them out of a
# route module — the layering upside down) because chat's ladder lived inline in
# its handler as a closure, with no symbol to call; v1.185.0 lifted it out.
# What is NOT shared is the transcript renderer and the distill prompt, because
# a panel's data shape forbids it — see :func:`panel_transcript` and
# :data:`PANEL_DISTILL_SYSTEM`. The write is still the same ``ltm.append`` front
# door.

#: Re-exported so this module's own callers (and its tests) keep one import
#: site for the clip contract. The definition lives with the ladder.
clip_with_marker = _commit.clip_with_marker


def _remember_budgets() -> tuple[int, int]:
    """(distill input budget, verbatim excerpt budget) in chars — the SHARED
    ones, so the two surfaces can never disagree about how much conversation a
    model is shown or how long an offline excerpt may be."""
    return _commit.REMEMBER_INPUT, _commit.REMEMBER_VERBATIM


#: Distill instruction for a PANEL. Deliberately not chat's prompt text: a round
#: table's value is WHO concluded what and where the panelists disagreed, and a
#: prompt that flattens N speakers into one voice throws away the only thing
#: this surface produces that chat cannot.
PANEL_DISTILL_SYSTEM = (
    "You distill multi-agent panel conversations into durable memory notes."
    " Extract ONLY what is worth remembering long-term: the decision the panel"
    " reached, the facts and figures established, unresolved disagreements, and"
    " open action items — as compact markdown bullets under short headings."
    " ATTRIBUTE: name the agent behind a claim or objection, because who said"
    " it is what makes a panel worth re-reading. Keep exact names, numbers,"
    " dates and identifiers as written. Skip pleasantries and restatement."
    " NEVER invent content that is not in the transcript, and never resolve a"
    " disagreement the panel left open; if the transcript notes an omitted"
    " middle, say the note covers the shared parts. No preamble, no sign-off."
)

_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")


def review_items(body: str, *, limit: int = 40, width: int = 300) -> list[str]:
    """What WOULD be committed, as a flat reviewable list.

    Suggest-don't-act needs the user to see the CLAIMS, not a wall of markdown —
    a preview whose only content is the finished note is a diff nobody reads. A
    heading becomes the prefix of the bullets under it so an item still says
    what it is about once it is out of its section. Falls back to plain lines
    when the body carries no bullets at all (the verbatim-excerpt path), because
    an empty review list would read as "there is nothing to commit".
    """
    def _scan(bullets_only: bool) -> list[str]:
        out: list[str] = []
        section = ""
        for raw in (body or "").splitlines():
            line = raw.strip()
            if not line or line == "---":
                continue
            heading = _HEADING_RE.match(line)
            if heading:
                section = heading.group(1).strip()
                continue
            bullet = _BULLET_RE.match(line)
            if bullet:
                text = bullet.group(1).strip()
            elif bullets_only:
                continue
            else:
                text = line
            out.append(f"{section}: {text}" if section else text)
            if len(out) >= limit:
                break
        return out

    items = _scan(True) or _scan(False)
    # TRUNCATION IS ALWAYS REPORTED (v1.178.0 review finding). Both caps used to
    # bite silently, in the one payload the user reads before an irreversible
    # write: a 41st claim simply vanished, and a long claim lost its tail
    # mid-sentence with nothing to say it had. A preview that quietly shows less
    # than what will land is worse than no preview — the user approves what they
    # were shown and something else is written. This is the same rule the file
    # walker and the OCR page cap already follow: cap, then SAY SO.
    clipped = [(i[: width - 1] + "…") if len(i) > width else i for i in items]
    if len(clipped) >= limit:
        clipped = clipped[:limit]
        clipped.append(
            f"[… only the first {limit} items are listed here — the full text "
            "below is what will be committed …]"
        )
    return clipped


def _panel_header(rec: Any, title: str, participants: list, msgs: list) -> str:
    """The provenance line every committed panel carries.

    ONE definition (v1.178.0): the preview and the commit must show the same
    header, and they are produced on two different code paths — the approved-
    content commit skips the ladder entirely. Two copies of this string would
    mean the note the user approved and the note that landed differed by their
    first line, which is exactly the mismatch this header exists to prevent.
    """
    stamp = ""
    try:
        stamp = rec.updated_at.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 — a missing stamp is not a failure
        stamp = ""
    roster = ", ".join(str(p.get("name") or "?") for p in participants)
    return (
        f"_Committed from the agent panel “{title}”"
        f" ({len(msgs)} messages; {roster or 'no participants on record'}"
        f"{', ' + stamp if stamp else ''})._\n\n"
    )


def panel_transcript(
    title: str, participants: list[dict], msgs: list[dict], updated_at: Any = None
) -> str:
    """The panel VERBATIM as markdown. Deterministic — no model in the loop.

    NOT ``routes.chat._share_transcript``, and that is a data-shape fact rather
    than a duplicate: chat's renderer has a speaker vocabulary two words wide
    ("You" / "Iron Jarvis") because a chat has two speakers. Flattening five
    panelists into "Iron Jarvis" would store a memory that cannot answer the one
    question worth asking of a round table later — who concluded what. Honest
    errors ride along as their own labelled line for the same reason the share
    renderer keeps its footnotes: a panelist that could NOT answer is part of
    how the conclusion was reached, and dropping it overstates the consensus.
    """
    # An entry's ``who`` is the participant KEY. Both the stored key and the
    # derived one are accepted: participants written before ``clean_participants``
    # existed carry no ``key``, and a label of "" would silently collapse every
    # such panelist onto one another.
    names: dict[str, str] = {}
    labels: list[str] = []
    for p in participants:
        label = f"{p.get('name') or '?'} ({p.get('role') or 'participant'})"
        labels.append(label)
        derived = participant_key(str(p.get("source") or ""), str(p.get("name") or ""))
        for k in (str(p.get("key") or ""), derived):
            if k and k != ":":
                names.setdefault(k, label)
    meta = ["Agent panel", f"{len(participants)} agents"]
    roster = ", ".join(labels)
    if roster:
        meta.append(roster)
    if updated_at is not None:
        try:
            meta.append(updated_at.strftime("%Y-%m-%d %H:%M UTC"))
        except Exception:  # noqa: BLE001 — a str timestamp still renders fine
            meta.append(str(updated_at))
    lines = [f"# {title}", "", "_" + " · ".join(meta) + "_", "", "---"]
    for m in msgs:
        who = str(m.get("who") or "user")
        label = "You" if who == "user" else names.get(who, who)
        lines += ["", f"### {label}", ""]
        content = str(m.get("content") or "").strip()
        error = str(m.get("error") or "").strip()
        lines.append(content or "_(no reply)_")
        if error:
            lines += ["", f"_{error}_"]
    return "\n".join(lines) + "\n"


def _load_round(rec: Any) -> tuple[list[dict], list[dict]]:
    """Parse a record's two JSON blobs, leniently. Runs in a worker thread —
    a 400-message thread is real CPU work and the daemon is one loop."""
    def _parse(blob: str) -> list[dict]:
        try:
            data = json.loads(blob or "[]")
        except Exception:  # noqa: BLE001 — a corrupt blob is an empty panel
            return []
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    return _parse(getattr(rec, "participants_json", "")), _parse(
        getattr(rec, "messages_json", "")
    )


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

    def for_chat(self, chat_thread_id: str, title: str = "") -> AgentThreadRecord:
        """The panel bound to a chat thread — created on first use (v1.150.0).

        Get-or-create, because "chat with several agents at once" means the room
        persists: turn 3's ``@builder`` must be able to see what ``@critic`` said
        in turn 2, which only works if it is the same thread.
        """
        chat_thread_id = (chat_thread_id or "").strip()
        with session_scope(self.engine) as db:
            if chat_thread_id:
                found = db.exec(
                    select(AgentThreadRecord).where(
                        AgentThreadRecord.chat_thread_id == chat_thread_id
                    )
                ).first()
                if found is not None:
                    return AgentThreadRecord(**found.model_dump())
        rec = AgentThreadRecord(
            title=(title or "").strip() or "Panel from chat",
            participants_json="[]",
            chat_thread_id=chat_thread_id,
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return AgentThreadRecord(**rec.model_dump())

    def add_participants(
        self, thread_id: str, participants: list[dict[str, str]]
    ) -> AgentThreadRecord | None:
        """Add agents that are not already in the panel; existing ones keep the
        role they joined with. Returns the updated record, or None if unknown.

        Under the shared conversation lock like every other participants/messages
        write — two mentions landing together must not clobber each other's
        additions (the read-modify-write is the whole risk here).
        """
        with _APPEND_LOCK:
            with session_scope(self.engine) as db:
                rec = db.get(AgentThreadRecord, thread_id)
                if rec is None:
                    return None
                current = json.loads(rec.participants_json or "[]")
                have = {p.get("key") for p in current}
                added = [p for p in participants if p.get("key") not in have]
                if added:
                    rec.participants_json = json.dumps(current + added)
                    rec.updated_at = utcnow()
                    db.add(rec)
                    db.commit()
                    db.refresh(rec)
                return AgentThreadRecord(**rec.model_dump())

    def list(self) -> list[AgentThreadRecord]:
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(AgentThreadRecord)))
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    def get(self, thread_id: str) -> AgentThreadRecord | None:
        with session_scope(self.engine) as db:
            return db.get(AgentThreadRecord, thread_id)

    def delete(self, thread_id: str) -> bool:
        """Delete a round table and its history-search docs together — the same
        "delete means deleted" rule ``DELETE /chat/threads/{id}`` follows."""
        with session_scope(self.engine) as db:
            rec = db.get(AgentThreadRecord, thread_id)
            if rec is None:
                return False
            db.delete(rec)
            try:
                from ..core.db import search_index

                index = search_index(self.engine)
                if index is not None:
                    index.drop_thread(thread_id, db=db)
            except Exception:  # noqa: BLE001 — a delete must always complete
                log.warning("history-search drop failed for agent thread %s",
                            thread_id, exc_info=True)
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

        v1.142.0: also re-indexes the thread for history search inside this
        same session and lock (before the commit), so what a panel of agents
        worked out is findable later. Round entries already carry ``at`` and
        use ``who``/``content``, all of which ``sync_thread`` reads leniently.
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
            self._index_thread(db, rec, msgs)
            db.commit()
            return len(msgs)

    def _index_thread(self, db: Any, rec: AgentThreadRecord, msgs: list) -> None:
        """History-search sync for a round table, in the caller's transaction.

        Rounds have no project binding (the Agents page is global), hence the
        empty ``project_id``. Never raises — an index failure must not sink a
        round that already cost real provider calls.
        """
        try:
            from ..core.db import search_index  # lazy: keeps the import graph flat

            index = search_index(self.engine)
            if index is None:
                return
            index.sync_thread(rec.id, "round", rec.title or "", "", msgs, db=db)
        except Exception:  # noqa: BLE001 — a round must never fail on search
            log.warning("history-search sync failed for agent thread %s",
                        getattr(rec, "id", "?"), exc_info=True)

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
        # Order is load-bearing: identity → seat → NO TOOLS. The base prompt is
        # the agent's real working prompt and it talks about tools; the
        # correction has to come after it, never before. See PANEL_NO_TOOLS.
        parts = [base_prompt.strip(), role_line, PANEL_NO_TOOLS]
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _builtin_prompt(name: str) -> str:
        """The REAL definition prompt behind a builtin panelist (v1.193.0).

        The round table used to synthesize "You are a {name} agent: answer with
        the judgement and focus of a {name}" — so the reviewer at the table was
        not the Reviewer, just a model told to sound like one. Panels are for
        HANDOFF AND TEAM SHAPE, which only means something if the seat carries
        the judgement its agent actually runs on.

        An unknown name keeps the old one-liner ON PURPOSE. ``types
        .get_agent_definition`` falls back to BUILDER for anything it does not
        know, and a seat named "designer" answering with the Builder's identity
        would be exactly the impersonation this method exists to end.
        """
        from ..core.models import AgentType
        from .types import _DEFINITIONS

        try:
            agent_type = AgentType(str(name or "").strip().lower())
        except ValueError:
            agent_type = None
        definition = _DEFINITIONS.get(agent_type) if agent_type is not None else None
        prompt = (getattr(definition, "system_prompt", "") or "").strip()
        if prompt:
            return prompt
        return (
            f"You are a {name} agent: answer with the judgement and "
            f"focus of a {name}."
        )

    @staticmethod
    def _dynamic_prompt(registry: Any, name: str, row: Any) -> str:
        """A dynamic panelist's COMPOSED prompt — identity anchor included.

        ``_speak_local`` read ``row.system_prompt`` raw, which bypasses
        :meth:`DynamicAgentRegistry.definition` — and the anchor ("You are
        {name}, a persistent named agent on this machine", v1.171.0) is applied
        at COMPOSITION time inside that method. So in the one room where agents
        address each other by name, a named agent was never told its own.

        Falls back to composing the anchor by hand when the registry has no
        usable ``definition`` (an injected or older registry): a panelist with
        no identity at all is the defect, and the fallback must not reintroduce
        it.
        """
        try:
            definition = registry.definition(name)
        except Exception:  # noqa: BLE001 — fall through to the hand-composed anchor
            definition = None
        prompt = (getattr(definition, "system_prompt", "") or "").strip()
        if prompt:
            return prompt
        from .dynamic import identity_anchor

        stored = (getattr(row, "system_prompt", "") or "").strip()
        anchor = identity_anchor(name)
        return f"{anchor}\n\n{stored}" if stored else anchor

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
            registry = d.platform.agents_registry
            row = registry.get(p["name"])
            if row is None:
                raise RuntimeError(f"dynamic agent {p['name']!r} no longer exists")
            # The COMPOSED definition, not the raw row — the identity anchor is
            # applied at composition time (v1.193.0). ``row`` is still read for
            # the pinned provider/model, which the definition does not carry.
            base_prompt = self._dynamic_prompt(registry, p["name"], row)
            provider = provider or row.provider or ""
            model = model or row.model or ""
        else:
            base_prompt = self._builtin_prompt(p["name"])
        provider = provider or d.platform.config.default_provider
        model = model or d.platform.config.default_model
        adapter = d.platform.providers.get(provider, model)
        from ..providers.adapters.base import LLMMessage

        # USER PROFILE (v1.144.0), "how" ONLY. A panel is worth reading because
        # the panelists differ, so they must NOT be told to write in the user's
        # voice or to adopt one another's character — but the user's language
        # and accessibility needs are not per-panelist preferences. See
        # profile.block.render's ``include`` docstring.
        system = self._system_for(p, others, base_prompt)
        try:
            from ..profile import profile_block

            _prefs = profile_block(d.platform, include=("how",))
            if _prefs:
                system += "\n\n" + _prefs
        except Exception:  # noqa: BLE001 — never break a round
            pass

        # LESSONS (v1.200.0): the round table WRITES into memory (remember +
        # the history index) but its panelists advised blind — every sibling
        # lane (chat_turn, agents/runtime) folds the accumulated lessons into
        # its system prompt and this one did not. Reuse the ONE renderer,
        # ``LearningEngine.apply_to_prompt`` (a second renderer would drift —
        # the remember-ladder lesson, v1.185.0): it is already bounded (top 8
        # lessons, terse rows) and returns the prompt UNCHANGED when nothing
        # has been learned, so no empty heading is ever injected. Lessons are
        # user-scope working knowledge, like the "how" slice above — they do
        # NOT erode panelist distinctness (profile stays include=("how",)).
        # No project knowledge here ON PURPOSE: agent threads carry no
        # project_id (the Agents page is global — see ``_index_thread``).
        learning = getattr(d.platform, "learning", None)
        if learning is not None:
            try:
                system = learning.apply_to_prompt(system)
            except Exception:  # noqa: BLE001 — never break a round
                pass

        # THE GUIDE AT THE TABLE (v1.224.0). Panelists have no tools, and the
        # Guide's whole value is looking things up — so the retrieval its
        # tools would have run is done for it here: the reference block for
        # the conversation's tail plus an app_search over the user's own
        # things, appended AFTER the no-tools rule so the model treats them as
        # material it already holds ("what you already know"), which is
        # exactly what they now are. Best-effort; the seat still answers
        # without them, and its prompt says to admit what it cannot see.
        if p["source"] == "builtin" and str(p["name"]).strip().lower() == "guide":
            system += await self._guide_material(transcript, d)

        resp, _p, _m = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=transcript or "(no messages yet)")],
        )
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("the model returned an empty reply")
        return text

    @staticmethod
    async def _guide_material(transcript: str, d: Any) -> str:
        """The Guide's looked-up material for one round: reference sections
        for the transcript's tail (the latest question dominates) and the
        user's matching things in this install. ``""`` when nothing could be
        gathered — never raises."""
        query = " ".join((transcript or "")[-600:].split())
        out = ""
        try:
            from ..guide import ground

            block = ground(d.platform, query)
            if block:
                out += "\n\n" + block
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..guide.tools import AppSearchTool

            res = await AppSearchTool(d.platform).execute({"query": query, "k": 8}, None)
            if res.ok and res.data and res.data.get("hits"):
                out += "\n\n# Matching things in this install (from app_search)\n" + res.output
        except Exception:  # noqa: BLE001
            pass
        return out

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

    # -- long-term memory -----------------------------------------------------

    async def remember(
        self,
        thread_id: str,
        d: Any,
        *,
        mode: str = "distill",
        source: str = "",
        provider: str = "",
        model: str = "",
        preview: bool = True,
        approved_content: str = "",
    ) -> dict[str, Any]:
        """Commit a panel to LONG-TERM MEMORY — the chat ``/remember`` ladder,
        for a round table (v1.178.0).

        A decision reached between agents used to die with the thread: rounds
        were persisted and searchable, but nothing ever crossed into the memory
        the app reads back on later turns. This is that crossing.

        ``mode`` distill = a one-shot distillation of what is worth remembering
        (see :data:`PANEL_DISTILL_SYSTEM`); full = the verbatim panel. HONEST
        MOCK RULE: a mock adapter would FABRICATE a memory of a real
        conversation, so distill hops to a real provider via
        ``d._failover_adapter("mock")`` and, with none connected, degrades to a
        verbatim excerpt and SAYS SO in ``note`` with ``distilled=False``. It
        degrades rather than refuses because memory must keep working offline —
        the same choice chat made, and the reason the ``note`` is not optional.

        SUGGEST-DON'T-ACT: ``preview`` defaults to TRUE. The default call WRITES
        NOTHING and returns ``items`` (the extracted claims, flat and readable)
        plus the exact ``content`` that would land; committing needs the
        explicit ``preview=False``. Chat's route commits on the first call
        because the user is remembering their OWN words; here the text was
        written by agents, and a note the user never read would be quoted back
        as fact by every later turn.

        Raises ``KeyError`` (unknown thread), ``ValueError`` (bad mode / empty
        thread / unknown memory source) or ``RuntimeError`` (the store refused
        the write) — the route maps them to 404/400/422, exactly like ``/say``.

        Every blocking step — the DB read, the JSON parse, the render, the LTM
        append (which writes files) — goes through ``asyncio.to_thread``.
        """
        mode = (mode or "distill").strip().lower()
        if mode not in ("distill", "full"):
            raise ValueError("mode must be 'distill' or 'full'")
        rec = await asyncio.to_thread(self.get, thread_id)
        if rec is None:
            raise KeyError(thread_id)
        participants, msgs = await asyncio.to_thread(_load_round, rec)
        if not msgs:
            raise ValueError("this thread has no messages to remember")

        ltm = d.platform.ltm
        src = (source or "").strip() or ltm.default_source()
        if not src or ltm.get(src) is None:
            raise ValueError(f"no such memory source: {src}")

        title = (getattr(rec, "title", "") or "").strip() or "Agent thread"

        # WHAT WAS APPROVED IS WHAT LANDS (v1.178.0, review finding). Without
        # this, `preview=False` re-ran the whole ladder — including a SECOND
        # distillation — so the text the user read and approved was not the text
        # that reached memory. A model asked twice does not answer twice the
        # same, which makes the preview a decoration rather than a decision: the
        # entire point of suggest-don't-act is that the thing shown IS the thing
        # done. Measured by the reviewer with a numbering adapter.
        #
        # So a commit may carry the previewed body back. It is written verbatim,
        # no second model call (cheaper AND honest), with the same header the
        # preview showed. Absent, the ladder runs as before — an older client, or
        # a caller that never previewed, is unchanged.
        approved = (approved_content or "").strip()
        if approved and not preview:
            # VERBATIM — the approved text already IS the whole note (review
            # finding). The preview returns `content` as header + body + the
            # thread reference, and that whole string is what comes back here.
            # Prepending the header again put it in TWICE, so the note that
            # landed differed from the note the user read — the exact mismatch
            # this branch exists to prevent, reintroduced by the fix for it.
            # (The test that should have caught it asserted `startswith(header)`,
            # which is trivially true of a doubled header; it now counts.)
            content = approved
            mem_title = f"Panel: {title}"
            out: dict[str, Any] = {
                "ok": True,
                "preview": False,
                "ref": "",
                "source": src,
                "mode": mode,
                # NOT re-derived: this text came back from a preview and was
                # written as approved, so claiming a fresh distillation here
                # would misreport how it was produced.
                "distilled": False,
                "title": mem_title,
                "messages": len(msgs),
                "participants": [str(p.get("name") or "?") for p in participants],
                "items": await asyncio.to_thread(review_items, approved),
                "content": content,
                "note": "committed the text you approved (no re-distillation)",
            }
            try:
                out["ref"] = await asyncio.to_thread(
                    ltm.append, mem_title, content, source=src
                )
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001 — an append failure is honest
                raise RuntimeError(f"could not write to '{src}': {exc}")
            return out

        transcript = await asyncio.to_thread(
            panel_transcript, title, participants, msgs, getattr(rec, "updated_at", None)
        )
        outcome = await _commit.distill_or_excerpt(
            d,
            transcript=transcript,
            mode=mode,
            system=PANEL_DISTILL_SYSTEM,
            subject="panel",
            provider=provider,
            model=model,
        )
        content_body = outcome.body
        distilled = outcome.distilled
        used_provider = outcome.provider
        note = outcome.note

        content = (
            _panel_header(rec, title, participants, msgs)
            + content_body
            + f"\n\nagent thread: {thread_id}"
        )
        mem_title = f"Panel: {title}"
        items = await asyncio.to_thread(review_items, content_body)

        out: dict[str, Any] = {
            "ok": True,
            "preview": preview,
            "ref": "",
            "source": src,
            "mode": mode,
            "distilled": distilled,
            "title": mem_title,
            "messages": len(msgs),
            "participants": [str(p.get("name") or "?") for p in participants],
            "items": items,
            "content": content,
        }
        if used_provider and distilled:
            out["provider"] = used_provider
        if note:
            out["note"] = note
        if preview:
            return out
        try:
            out["ref"] = await asyncio.to_thread(
                ltm.append, mem_title, content, source=src
            )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — an append failure is honest
            raise RuntimeError(f"could not write to '{src}': {exc}")
        return out
