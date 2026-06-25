"""File search tool (§19 tool interface).

A thin tool over :class:`FileSearchService` exposing the three search modes to
the agent. ``filesearch_tools(service)`` builds it bound to a single service so
the platform can register it like the memory/skill tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .service import FileSearchService


class FileSearchTool(Tool):
    """Search configured roots by name (glob/substring), content (regex), or semantics."""

    name = "file_search"
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
        roots = [Path(root)] if root else None
        try:
            results = self.service.search(
                args["query"], mode=mode, limit=limit, roots=roots
            )
        except Exception as exc:  # never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        lines: list[str] = []
        for r in results:
            if "line" in r:  # content / semantic hit
                lines.append(f"{r['path']}:{r['line']}: {r.get('text', '')}")
            else:  # name hit
                lines.append(r["path"])
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"results": results, "count": len(results), "mode": mode},
        )


def filesearch_tools(service: FileSearchService) -> list[Tool]:
    """Build the file-search tool bound to a single ``FileSearchService``."""
    return [FileSearchTool(service)]
