"""Agent-facing learning tools (§19 tool interface).

Two thin tools over :class:`LearningEngine`, each constructed with the engine
injected:

* ``remember_preference`` — in a chat turn, keep the preference the user stated;
  anywhere else (v1.501.0, decision D2) suggest it: it waits in Improvements
  and reaches no prompt until the user keeps it. Permission default ``allow``.
* ``recall_lessons``      — let the agent consult the lessons it has accumulated
  (never task notes, v1.501.0). Permission default ``allow``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..core.events import EventType
from ..tools.base import Tool, ToolContext, ToolResult
from .engine import LearningEngine

log = logging.getLogger(__name__)

#: v1.499.0 (vision plan leap 3 Phase 3): in a confirmed client's folder, a
#: sentence whose SUBJECT is the client ("They want cash basis", "The client
#: prefers paper statements", "… for them") is that client's preference,
#: never one for every chat. Anchored and linear: one look at the start, and
#: one bounded phrase anywhere.
_CLIENT_SUBJECT = re.compile(
    r"\A\W{0,4}(?:they|their|them|he|she|his|her|the\s+client|this\s+client)\b"
    r"|\bfor\s+(?:them|the\s+client|this\s+client)\b",
    re.IGNORECASE,
)
#: The longest sentence the guard reads (a preference is at most 280).
_GUARD_CHARS = 2000


def _possessive(name: str) -> str:
    try:
        from ..settings.records_clients import possessive

        return possessive(name) or name
    except Exception:  # noqa: BLE001
        return name


def client_guard(engine: Any, text: Any) -> tuple[str, str]:
    """``(refusal, note)`` for a preference about to be kept for EVERY chat
    (v1.499.0). BLOCKING (the client map, cached); never raises.

    * The sentence names a confirmed or WAITING client (``names.named_in``):
      refused — one client's preference stays with that client.
    * In a confirmed client's folder (``wall.current().client``) a sentence
      whose subject is the client ("they …", "the client …", "… for them"):
      refused the same way.
    * In a client's folder anything else is kept, with the note "Kept for
      every chat. If this was only for Hartleys, say so."
    * The client record cannot be read: refused (fail closed — it could
      have been about a client)."""
    words = str(text or "")[:_GUARD_CHARS]
    try:
        from ..workmap import clients as C
        from ..workmap.clients import names as N
        from ..workmap.clients import store as S
        from ..workmap.clients import wall as W

        turn = W.current()
        if not getattr(turn, "ok", True):
            return ("Your clients could not be read, so this preference was not kept; try again in a moment.", "")
        try:
            cmap = S.load_map(engine)
        except C.ClientError:
            return ("Your clients could not be read, so this preference was not kept; try again in a moment.", "")
        table = {**dict(getattr(cmap, "names", {}) or {}), **dict(getattr(cmap, "waiting_names", {}) or {})}
        hit = sorted(N.named_in(words, table)) if table else []
        if hit:
            name = str(table[hit[0]][0])
            return (
                f"This is about {name}, a client. A preference for one client stays with that client: use "
                "client_preference_add.",
                "",
            )
        client = getattr(turn, "client", None)
        if client is None:
            return ("", "")
        name = str(getattr(client, "name", "") or "this client")
        if _CLIENT_SUBJECT.search(words):
            return (
                f"This chat is in {_possessive(name)} folder, and this reads as {_possessive(name)} own "
                "preference. A preference for one client stays with that client: use client_preference_add.",
                "",
            )
        return ("", f"Kept for every chat. If this was only for {name}, say so.")
    except Exception:  # noqa: BLE001 — fail closed: it could have been about a client
        log.warning("remember_preference: the client guard failed", exc_info=True)
        return ("Your clients could not be read, so this preference was not kept; try again in a moment.", "")


#: Said in the tool result when a preference carried a secret (v1.499.3).
SECRET_LEFT_OUT = (
    "A password, key or token in it was left out (kept as ***): a preference is "
    "read into every later chat, so secrets are never kept in one. Keep a secret "
    "in Settings, where it is stored locked."
)


#: The tool session id of a chat turn (``chat_turn.turn_session_id``): "chat",
#: or a coding session's ``build:<thread>``.
_CHAT_SESSION = "chat"
_CODING_SESSION_PREFIX = "build:"


def said_in_chat(ctx: ToolContext) -> bool:
    """Is this call part of a CHAT TURN (v1.501.0, decision D2)? Both chat
    lanes run their tools as ``session_id="chat"`` or a coding session's
    ``build:<thread>``; there the owner's own message is the turn, and chat
    arms this tool only for a stated lasting preference (the v1.279.0 brief
    and autoselect rule), so the preference is kept at once, exactly as
    before. Every other caller — an agent run, a schedule, a mission, a
    helper, a workflow step, another coding tool through the MCP server —
    only GUESSED it, so it waits for the owner."""
    sid = str(getattr(ctx, "session_id", "") or "")
    return sid == _CHAT_SESSION or sid.startswith(_CODING_SESSION_PREFIX)


def run_evidence(engine: Any, session_id: str) -> list[dict[str, Any]]:
    """The evidence a run's guessed preference carries: what the run was
    asked (its task), when, and the door to the run when it is a ``Session``
    row. BLOCKING (one indexed read); never raises."""
    from ..core.ids import utcnow
    from .preferences import QUOTE_CHARS, RUN_LINK, WHERE_RUN

    task, link = "", ""
    try:
        from ..core.db import session_scope
        from ..core.models import Session

        if engine is not None and session_id:
            with session_scope(engine) as db:
                row = db.get(Session, session_id)
                if row is not None:
                    task = str(getattr(row, "task", "") or "")
                    link = f"{RUN_LINK}{session_id}"
    except Exception:  # noqa: BLE001 — the suggestion still says it came from a run
        log.debug("remember_preference: the run could not be read", exc_info=True)
    quote = " ".join(task.split()) or "an agent run"
    if len(quote) > QUOTE_CHARS:
        quote = quote[: QUOTE_CHARS - 1].rstrip() + "…"
    ev: dict[str, Any] = {"quote": quote, "at": utcnow().isoformat(), "where": WHERE_RUN}
    if link:
        ev["link"] = link
    return [ev]


#: What the model is told for each way a guessed preference can end (v1.501.0).
_NOTICED_WORDS = {
    "suggested": (
        "suggested, waiting for the owner: \"{text}\" is listed in Improvements and on the Memory "
        "page, and is not used until the owner keeps it. If you mention it, say it is a suggestion "
        "for them to keep."
    ),
    "already_kept": "already kept: the owner's preferences already say this, so nothing new was suggested.",
    "already_waiting": "already suggested: a suggestion like this already waits for the owner in Improvements.",
    "declined_before": (
        "not suggested: the owner said \"not this\" to a preference like this before, so it is not asked again."
    ),
}
_NOTICED_FULL = (
    "Not suggested: {n} suggestions already wait for the owner in Improvements, so this one was not "
    "added. Mention it in your answer instead."
)


class RememberPreferenceTool(Tool):
    """Record (in a chat turn) or suggest (anywhere else) a lasting preference."""

    name = "remember_preference"
    description = (
        "Suggest a lasting preference about how the user likes things done "
        "(e.g. 'prefers concise bullet-point summaries'). Said by the user in a "
        "chat, it is kept at once. Noticed during other work, it is a suggestion: "
        "it waits in Improvements and is used only once the user keeps it."
    )
    permission_key = "remember_preference"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self, learning: LearningEngine) -> None:
        self.learning = learning

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # v1.499.0 (leap 3 Phase 3): a preference about ONE client is never
        # kept for every chat (it would be told in every other client's
        # chats). The guard runs off the loop (the client map is a read).
        refusal, note = await asyncio.to_thread(client_guard, getattr(self.learning, "engine", None), args.get("text"))
        if refusal:
            return ToolResult(ok=False, error=refusal)
        from .engine import without_secrets

        said = str(args.get("text") or "").strip()
        left_out = without_secrets(said) != said
        if not said_in_chat(ctx):
            return await self._suggest(said, ctx, left_out=left_out)
        record = self.learning.note_preference(said)
        await ctx.event_bus.publish(
            EventType.MEMORY_UPDATED,
            {"kind": "preference", "id": record.id, "weight": record.weight},
            session_id=ctx.session_id,
        )
        return ToolResult(
            ok=True,
            output=f"remembered preference: {record.text}"
            + (f" {SECRET_LEFT_OUT}" if left_out else "")
            + (f" {note}" if note else ""),
            data={
                "id": record.id,
                "weight": record.weight,
                "scope": record.scope,
                # v1.282.0: the sentence itself, for the receipt under the reply.
                "text": record.text,
            },
        )

    async def _suggest(self, said: str, ctx: ToolContext, *, left_out: bool) -> ToolResult:
        """v1.501.0 (decision D2): outside a chat turn the preference was
        GUESSED, so it waits for the owner as a suggestion with the run as
        evidence (``LearningEngine.suggest_preference``), off the loop. The
        result says so in plain words — never "remembered". (The client
        guard's "Kept for every chat …" note is for a KEPT preference; a
        suggestion is the owner's to decide, so it is not said.)"""
        from .preferences import MAX_OPEN, NOTICED_FULL, NOTICED_SUGGESTED, publish_suggested_noticed

        if not said:
            return ToolResult(ok=False, error="Write the preference as one plain sentence first; it was empty.")
        engine = getattr(self.learning, "engine", None)
        evidence = await asyncio.to_thread(run_evidence, engine, str(ctx.session_id or ""))
        record, outcome = await asyncio.to_thread(self.learning.suggest_preference, said, evidence=evidence)
        if outcome == NOTICED_FULL:
            return ToolResult(ok=False, error=_NOTICED_FULL.format(n=MAX_OPEN))
        tail = f" {SECRET_LEFT_OUT}" if left_out else ""
        if record is None:
            return ToolResult(
                ok=True,
                output=_NOTICED_WORDS.get(outcome, _NOTICED_WORDS["already_kept"]) + tail,
                data={"status": outcome, "text": ""},
            )
        await publish_suggested_noticed(ctx.event_bus, record, session_id=ctx.session_id)
        return ToolResult(
            ok=True,
            output=_NOTICED_WORDS[NOTICED_SUGGESTED].format(text=record.text) + tail,
            data={
                "id": record.id,
                "status": record.status,
                "weight": record.weight,
                "scope": record.scope,
                "text": record.text,
            },
        )


class RecallLessonsTool(Tool):
    """Recall the lessons + preferences learned about working with the user."""

    name = "recall_lessons"
    description = (
        "Recall the lessons and preferences learned about working with the user, "
        "ordered by importance."
    )
    permission_key = "recall_lessons"
    input_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["user", "project"]},
            "limit": {"type": "integer", "minimum": 1},
        },
    }

    def __init__(self, learning: LearningEngine) -> None:
        self.learning = learning

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .engine import _PROMPT_EXCLUDED_SOURCES

        scope = args.get("scope", "user")
        limit = int(args.get("limit", 12))
        # v1.501.0 (north-star plan 1.1): task notes (``reflection``) are left
        # out, as the prompt leaves them out. A note records one run's work —
        # often a client's — and carries no folder the client wall
        # (``workmap.clients.wall``) could judge, so recalling it into another
        # client's chat or run would cross that wall.
        from .preferences import told_lessons

        # v1.507.0 (north-star plan 7.1 / 7.3): a kept suggestion from what
        # went wrong in a chat or from low-scoring runs passes the client wall
        # by the chat or runs it came from. Off the loop (the wall reads the
        # client record and their folders; the context carries the turn).
        items = await asyncio.to_thread(
            lambda: told_lessons(
                getattr(self.learning, "engine", None),
                self.learning.lessons(scope=scope, limit=limit, exclude_sources=_PROMPT_EXCLUDED_SOURCES),
            )
        )
        results = [
            {
                "id": lesson.id,
                "text": lesson.text,
                "source": lesson.source,
                "weight": lesson.weight,
                "scope": lesson.scope,
            }
            for lesson in items
        ]
        output = "\n".join(
            f"- [{r['source']} w{r['weight']}] {r['text']}" for r in results
        )
        return ToolResult(
            ok=True,
            output=output,
            data={"lessons": results, "count": len(results)},
        )


def learning_tools(learning: LearningEngine) -> list[Tool]:
    """Build the learning tool pair bound to a single :class:`LearningEngine`."""
    return [RememberPreferenceTool(learning), RecallLessonsTool(learning)]
