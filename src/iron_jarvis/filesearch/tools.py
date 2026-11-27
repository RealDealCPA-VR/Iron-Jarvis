"""File search tool (§19 tool interface).

A thin tool over :class:`FileSearchService` exposing the three search modes to
the agent. ``filesearch_tools(service)`` builds it bound to a single service so
the platform can register it like the memory/skill tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_path_allowed, fs_read_ok, is_protected_path
from ..tools.base import Tool, ToolContext, ToolResult
from .service import BadSearchPattern, FileSearchService, SearchNotes


#: What ``file_search`` says when it was asked for a search by meaning while
#: it was armed only because it stayed for the chat (release 7).
STAYED_BY_WORDS = (
    "[Searched by the words of the query, not by meaning, in this reply (to have a search by "
    "meaning, the user can ask for one in a message).]"
)


class FileSearchTool(Tool):
    """Search configured roots by name (glob/substring), content (regex), or semantics."""

    name = "file_search"
    returns_untrusted_content = True  # matched file text can carry planted instructions
    description = (
        "Search the user's files (broader than the workspace grep). With no "
        "'root' it looks in the Iron Jarvis chat files folder, every active "
        "project's folder and the folders the user added on the File Search "
        "page: mode 'name' (glob/substring on paths), 'content' (regex, "
        "default), or 'semantic' (by meaning; only the folders the user added). "
        "Respects ignore patterns; stays within roots. Pass an optional 'root' (e.g. a drive like 'C:\\\\' from "
        "list_drives) to target an arbitrary local root with a bounded walk."
    )
    permission_key = "file_search"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "mode": {"type": "string", "enum": ["name", "content", "semantic"]},
            "limit": {"type": "integer", "minimum": 1},
            "root": {"type": "string"},
        },
        "required": ["query"],
    }

    def __init__(self, service: FileSearchService) -> None:
        self.service = service

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mode = args.get("mode", "content")
        limit = int(args.get("limit", 50))
        root = args.get("root")
        query = args["query"]
        # The intelligence plan, release 7: a search BY MEANING asks a model
        # (one call for the query, and one a file chunk while the index is
        # built). Armed on a chat reply ONLY because it stayed
        # (``ctx.stayed_only``), the tool does no such new work: it searches
        # by the words of the query, and says so in one sentence.
        by_words = False
        if mode == "semantic" and self.name in (getattr(ctx, "stayed_only", None) or ()):
            import re

            words = [re.escape(w) for w in re.findall(r"\w{3,}", str(query or ""))][:8]
            mode, query, by_words = "content", "|".join(words) or re.escape(str(query or "")), True
        # A caller-supplied root must satisfy the same FS policy as the HTTP
        # endpoints: never a protected secrets/key dir, and inside the allowlist
        # when one is configured. Otherwise an agent could search arbitrary roots.
        if root is not None:
            # v1.356.0 integrate (G81): the read gate's own sentence (it names
            # Settings › Permissions when the user's folders refused, and never
            # the environment variable).
            ok, why = fs_read_ok(root)
            if not ok:
                return ToolResult(ok=False, error=why)
        roots = [Path(root)] if root else None
        # What the search could not cover. Reported below rather than dropped —
        # a skipped file is a hole in the answer, and this codebase's rule is that
        # a hole is always said out loud (see ListFilesTool's truncation note).
        notes = SearchNotes()
        try:
            # Offload the os.walk + byte reads over up to 20k files to a thread so a
            # content search doesn't freeze the daemon's single event loop.
            import asyncio

            results = await asyncio.to_thread(
                self.service.search,
                query,
                mode=mode,
                limit=limit,
                roots=roots,
                notes=notes,
            )
        except BadSearchPattern as exc:
            # GREP'S SHAPE (`tools/builtins.GrepTool` -> "bad regex: ..."). This
            # used to come back ok=True / count=0 / output="", which the model
            # reads as "that text is nowhere on your disk" — a confident wrong
            # answer for the everyday literals 'read_file(', 'C:\\Users', 'a[b'.
            # The escaped form is NAMED, never silently substituted.
            return ToolResult(
                ok=False,
                error=(
                    f"bad regex: {exc.reason} — mode 'content' treats the query as "
                    f"a REGULAR EXPRESSION. Nothing was searched. To match it "
                    f"literally, re-run with query: {exc.literal}"
                ),
            )
        except Exception as exc:  # never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        # Filter individual hits through the policy too: a configured default
        # root (e.g. the project) may itself contain a protected/excluded path.
        results = [
            r
            for r in results
            if not is_protected_path(r.get("path", "")) and fs_path_allowed(r.get("path", ""))
        ]

        lines: list[str] = []
        for r in results:
            if "line" in r:  # content / semantic hit
                lines.append(f"{r['path']}:{r['line']}: {r.get('text', '')}")
            else:  # name hit
                lines.append(r["path"])
        out = "\n".join(lines)
        skipped = notes.note()
        if skipped:
            out = f"{out}\n\n{skipped}" if out else skipped
        if by_words:
            out = (f"{out}\n\n" if out else "") + STAYED_BY_WORDS
        # Next leaps 16-a: the FIRST time a by-meaning search matched words in
        # a reply that can draw the card (``ctx.chat_page``: the stream lane,
        # for the dashboard's chat page only), the offer is shown under this
        # reply, once ever (memory/meaning.mark_offered). Never in a Build
        # pane, a Build session, the sidebar, an agent run or the POST lane,
        # which draw no card: Improvements still holds the offer.
        offer = False
        if mode == "semantic" and notes.words_only and getattr(ctx, "chat_page", False) is True:
            from types import SimpleNamespace

            from ..memory import meaning

            offer = await asyncio.to_thread(
                meaning.mark_offered, SimpleNamespace(config=ctx.config, embedder=self.service.embedder)
            )
            if offer:
                out += (
                    "\n[A card under this reply offers to turn on search by meaning, with what it costs "
                    "on this PC. The user answers there; do not ask them in words.]"
                )
        return ToolResult(
            ok=True,
            output=out,
            data={
                "results": results,
                "count": len(results),
                "mode": mode,
                "skipped_unreadable": notes.unreadable,
                # v1.232.0 (audit T7): files over the size cap, counted the
                # way undecodable ones are instead of dropped silently.
                "skipped_oversize": notes.oversize,
                # v1.445.0: why a "By meaning" answer is empty or word-based.
                "no_meaning_folders": notes.no_meaning_folders,
                "still_indexing": notes.indexing,
                "words_only": notes.words_only,
                # 16-a: this reply carries the search-by-meaning offer.
                "meaning_offer": offer,
            },
        )


def filesearch_tools(service: FileSearchService) -> list[Tool]:
    """Build the file-search tool bound to a single ``FileSearchService``."""
    return [FileSearchTool(service)]
