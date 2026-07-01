"""Built-in tools (§18). Workspace-scoped subset for the Phase 0–3 slice.

read_file / write_file / edit_file / list_files / grep operate strictly inside
the session workspace (§17 filesystem=workspace_only). shell is included but
defaults to permission ``ask`` and real isolation lands with the Sandbox Manager
(§16, Phase 4).
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from .base import Tool, ToolContext, ToolResult, safe_path


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a UTF-8 text file from the session workspace."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such file: {args['path']}")
        text = path.read_text(encoding="utf-8")
        return ToolResult(ok=True, output=text, data={"bytes": len(text)})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a UTF-8 text file in the session workspace."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args["content"]
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            ok=True,
            output=f"wrote {len(content)} bytes to {args['path']}",
            data={"path": args["path"], "bytes": len(content)},
        )


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace the first occurrence of `old` with `new` in a workspace file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        },
        "required": ["path", "old", "new"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such file: {args['path']}")
        text = path.read_text(encoding="utf-8")
        if args["old"] not in text:
            return ToolResult(ok=False, error="`old` text not found")
        path.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
        return ToolResult(ok=True, output=f"edited {args['path']}")


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files under a workspace directory (default: workspace root)."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        base = safe_path(ctx.workspace, args.get("path", "."))
        if not base.exists():
            return ToolResult(ok=False, error="no such directory")
        entries = sorted(
            str(p.relative_to(ctx.workspace.resolve())).replace("\\", "/")
            for p in base.rglob("*")
            if p.is_file()
        )
        return ToolResult(ok=True, output="\n".join(entries), data={"count": len(entries)})


class GrepTool(Tool):
    name = "grep"
    description = "Regex-search workspace files; returns matching path:line entries."
    input_schema = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        base = safe_path(ctx.workspace, args.get("path", "."))
        try:
            rx = re.compile(args["pattern"])
        except re.error as exc:
            return ToolResult(ok=False, error=f"bad regex: {exc}")
        hits: list[str] = []
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for fp in files:
            try:
                for i, line in enumerate(fp.read_text(encoding="utf-8").splitlines(), 1):
                    if rx.search(line):
                        rel = str(fp.relative_to(ctx.workspace.resolve())).replace("\\", "/")
                        hits.append(f"{rel}:{i}: {line.strip()}")
            except (UnicodeDecodeError, OSError):
                continue
        return ToolResult(ok=True, output="\n".join(hits), data={"matches": len(hits)})


class ShellTool(Tool):
    name = "shell"
    description = "Run a shell command in the workspace. (Sandboxing arrives in Phase 4.)"
    permission_key = "shell"  # defaults to 'ask' — fail-closed in headless mode
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        try:
            # Offload to a thread: subprocess.run blocks its OS thread for up to 60s,
            # and the tool runs on the daemon's single event loop — inline it would
            # freeze ALL requests, WS event delivery, and every other session.
            proc = await asyncio.to_thread(
                lambda: subprocess.run(
                    args["command"],
                    shell=True,
                    cwd=ctx.workspace,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error="command timed out")
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return ToolResult(
            ok=proc.returncode == 0,
            output=out.strip(),
            data={"returncode": proc.returncode},
            error=None if proc.returncode == 0 else f"exit {proc.returncode}",
        )


def default_registry():
    """Build a registry populated with the built-in tools."""
    from .registry import ToolRegistry

    registry = ToolRegistry()
    for tool_cls in (
        ReadFileTool,
        WriteFileTool,
        EditFileTool,
        ListFilesTool,
        GrepTool,
        ShellTool,
    ):
        registry.register(tool_cls())
    return registry
