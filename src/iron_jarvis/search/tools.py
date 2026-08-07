"""Agent-facing ``history_search`` — ranked search over past conversations.

The model could always be HANDED context (grounding pushes snippets in); this
is the PULL: "what did we decide about the S-corp election in March" becomes one
tool call against :class:`~iron_jarvis.search.index.SearchIndex` instead of a
confident "I don't have access to our earlier conversations".

Two deliberate design points:

* **No natural-language date parser.** ``after``/``before`` are ISO strings the
  CALLER supplies — the model already knows today's date from its prompt and is
  far better at "in March" → ``2026-03-01``/``2026-03-31`` than a regex table
  would be, and a parser here would be a second, silently-diverging notion of
  what "last week" means. The tool description states this explicitly so the
  model converts before calling.
* **``returns_untrusted_content = True``** (the ``recall`` precedent): every
  line this tool returns is text a human — or a web page a human pasted, or an
  inbound message from a stranger's phone — once put into a conversation. It is
  DATA, not instruction, and the agent runtime fences + injection-scans it
  before the model reads it.

Read-only end to end: ``Reversibility.READONLY`` (nothing to undo) and the
default permission is ``allow`` (``core/config.py::default_permissions``), the
same tier as ``recall``/``ltm_search``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from .index import MAX_LIMIT, SearchIndex
from .models import SEARCH_KINDS

#: How a hit's timestamp is rendered in the ranked line. The YEAR is included
#: deliberately (the spec's example line showed "Mar 12"): history spans years,
#: "Mar 12" alone cannot answer "when did we decide this", and a
#: current-year-only suffix would make the output depend on the wall clock.
_STAMP_FORMAT = "%b %d, %Y"

#: Human labels for the four kinds of history, so a ranked line reads as English
#: instead of leaking the index's internal enum.
_KIND_LABEL = {
    "chat": "chat",
    "comm": "message",
    "round": "round table",
    "session": "agent session",
}


def _stamp(at: Any) -> str:
    """``"Mar 12, 2026"`` for a datetime, ``""`` when the row has no usable
    timestamp (backfilled rows can predate ``at`` stamping)."""
    if isinstance(at, datetime):
        try:
            return at.strftime(_STAMP_FORMAT)
        except (ValueError, OSError):  # pragma: no cover — exotic platform dates
            return ""
    return ""


def _int(value: Any, default: int) -> int:
    """Lenient int coercion — a model that sends ``"5"`` (or nonsense) must get a
    search, not a type error."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _kinds(value: Any) -> "list[str] | None":
    """``kind`` as the model actually sends it → what the index wants.

    The schema says one string from the enum, and models mostly comply — but
    "restrict to chat AND round" is a real request, and a model expressing it as
    ``["chat", "round"]`` or ``"chat,round"`` would otherwise be stringified into
    one nonsense kind that matches NOTHING. A search that silently returns zero
    is the single worst failure this tool can have (it reads as "we never
    discussed that"), so both forms are accepted — the same leniency
    ``GET /search/history`` already gives the palette. An unknown kind is still
    passed through and still matches nothing: a filter the caller asked for is
    honoured, never quietly dropped.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [str(v).strip() for v in value]
    else:
        parts = [p.strip() for p in str(value).split(",")]
    kept = [p for p in parts if p]
    return kept or None


class HistorySearchTool(Tool):
    """Ranked full-text search across every conversation Iron Jarvis kept."""

    name = "history_search"
    #: Planted content: everything returned here was once typed into a
    #: conversation by a human, a web page, or an inbound message.
    returns_untrusted_content = True
    reversibility = Reversibility.READONLY
    permission_key = "history_search"
    description = (
        "Search EVERY past conversation and get the matching excerpts back, "
        "ranked by relevance: chat threads, messaging threads, Agents "
        "round-tables, and finished agent sessions. Use this whenever the user "
        "refers to something from before — \"what did we decide about X\", "
        "\"find the thread where we discussed Y\", \"when did we talk about Z\" "
        "— instead of guessing or saying you don't remember. "
        "DATES: this tool does NOT parse natural language dates. YOU convert "
        "the user's words into ISO dates and pass them: \"in March\" -> "
        "after=\"2026-03-01\", before=\"2026-03-31\"; \"last week\" -> the two "
        "ISO dates that bound it. Both bounds are inclusive; omit them to "
        "search all of history. Read-only — it never changes a conversation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for — the words the conversation "
                "would have used. Quoted \"phrases\" and OR are supported.",
            },
            "kind": {
                "type": "string",
                "enum": list(SEARCH_KINDS),
                "description": "Optional: restrict to one kind of history — "
                "chat (chat threads), comm (messaging threads), round "
                "(Agents round-tables), session (finished agent runs).",
            },
            "project_id": {
                "type": "string",
                "description": "Optional: only history tagged to this project.",
            },
            "after": {
                "type": "string",
                "description": "Optional INCLUSIVE lower bound as an ISO date "
                "or datetime (e.g. \"2026-03-01\"). You convert the user's "
                "words (\"in March\", \"since last Tuesday\") into this.",
            },
            "before": {
                "type": "string",
                "description": "Optional INCLUSIVE upper bound as an ISO date "
                "or datetime (e.g. \"2026-03-31\").",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "description": f"How many hits to return (default 20, max {MAX_LIMIT}).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, index: SearchIndex) -> None:
        self.index = index

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(
                ok=False, error="history_search: 'query' is required"
            )
        try:
            hits = self.index.search(
                query,
                kinds=_kinds(args.get("kind")),
                project_id=(str(args.get("project_id") or "").strip() or None),
                after=(args.get("after") or None),
                before=(args.get("before") or None),
                limit=_int(args.get("limit"), 20),
            )
            mode = self.index.mode
        except Exception as exc:  # noqa: BLE001 — a search must never crash a run
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        lines: list[str] = []
        for i, hit in enumerate(hits, start=1):
            stamp = _stamp(hit.at)
            label = _KIND_LABEL.get(hit.kind, hit.kind or "history")
            tag = f"[{label} · {stamp}]" if stamp else f"[{label}]"
            head = (hit.title or hit.ref or label).strip()
            lines.append(f"{i}. {tag} {head}: {hit.snippet}")
        data = {
            "hits": [h.as_dict() for h in hits],
            "mode": mode,
            "count": len(hits),
        }
        if not hits:
            # Honest empty — a search that found nothing SUCCEEDED. Saying so
            # plainly is what stops a model narrating a remembered answer.
            return ToolResult(
                ok=True,
                output=(
                    "No past conversation matched that search "
                    "(nothing indexed matches those words/filters)."
                ),
                data=data,
            )
        return ToolResult(ok=True, output="\n".join(lines), data=data)


def history_search_tools(index: SearchIndex) -> list[Tool]:
    """Build the history-search tool bound to the shared :class:`SearchIndex`."""
    return [HistorySearchTool(index)]
