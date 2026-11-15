"""Agent-facing learning tools (§19 tool interface).

Two thin tools over :class:`LearningEngine`, each constructed with the engine
injected:

* ``remember_preference`` — let the agent record a preference it inferred during
  a conversation, so future runs honour it. Permission default ``allow``.
* ``recall_lessons``      — let the agent consult the lessons it has accumulated.
  Permission default ``allow``.
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


class RememberPreferenceTool(Tool):
    """Record a durable user preference the agent inferred mid-conversation."""

    name = "remember_preference"
    description = (
        "Record a durable user preference you inferred (e.g. 'prefers concise "
        "bullet-point summaries') so every future run honours it."
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
        scope = args.get("scope", "user")
        limit = int(args.get("limit", 12))
        items = self.learning.lessons(scope=scope, limit=limit)
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
