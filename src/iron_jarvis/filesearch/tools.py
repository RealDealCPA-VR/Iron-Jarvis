"""File search tool (§19 tool interface).

A thin tool over :class:`FileSearchService` exposing the three search modes to
the agent. ``filesearch_tools(service)`` builds it bound to a single service so
the platform can register it like the memory/skill tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_path_allowed, is_protected_path
from ..tools.base import Tool, ToolContext, ToolResult
from .service import BadSearchPattern, FileSearchService, SearchNotes


class FileSearchTool(Tool):
    """Search configured roots by name (glob/substring), content (regex), or semantics."""

    name = "file_search"
    returns_untrusted_content = True  # matched file text can carry planted instructions
    description = (
        "Search across configured roots (broader than the workspace grep): "
        "mode 'name' (glob/substring on paths), 'content' (regex, default), or "
        "'semantic' (similarity, if enabled). Respects ignore patterns; stays "
        "within roots. Pass an optional 'root' (e.g. a drive like 'C:\\\\' from "
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
        # A caller-supplied root must satisfy the same FS policy as the HTTP
        # endpoints: never a protected secrets/key dir, and inside the allowlist
        # when one is configured. Otherwise an agent could search arbitrary roots.
        if root is not None:
            if is_protected_path(root) or not fs_path_allowed(root):
                return ToolResult(
                    ok=False,
                    error="root is protected or outside IRONJARVIS_FS_ALLOWLIST",
                )
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
                args["query"],
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
        return ToolResult(
            ok=True,
            output=out,
            data={
                "results": results,
                "count": len(results),
                "mode": mode,
                "skipped_unreadable": notes.unreadable,
            },
        )


def filesearch_tools(service: FileSearchService) -> list[Tool]:
    """Build the file-search tool bound to a single ``FileSearchService``."""
    return [FileSearchTool(service)]
