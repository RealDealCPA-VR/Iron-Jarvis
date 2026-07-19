"""Built-in tools (§18). Workspace-scoped subset for the Phase 0–3 slice.

read_file / write_file / edit_file / list_files / grep operate strictly inside
the session workspace (§17 filesystem=workspace_only). shell is included but
defaults to permission ``ask`` and real isolation lands with the Sandbox Manager
(§16, Phase 4).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .base import Reversibility, Tool, ToolContext, ToolResult, safe_path
from .undo import (
    make_file_descriptor,
    revert_workspace_file,
    sha256_bytes,
)


def _text_sha(content: str) -> str:
    """Newline-invariant hash of text content — matches ``sha256_target(mode=text)``."""
    return sha256_bytes(content.encode("utf-8"))


#: Formats that are NOT plain text but that ``read_document`` handles fully.
#: Pointing at the right tool matters more than it looks: a raw
#: ``UnicodeDecodeError`` tells a model nothing it can act on, and a model with
#: no actionable error INVENTS one — a live report had the assistant announce
#: that .docx files were "blocked by the filter" (there is no such filter; the
#: documents read perfectly). An error that names the next step ends that.
_DOC_EXTENSIONS = {
    ".docx": "Word", ".doc": "Word", ".pdf": "PDF", ".xlsx": "Excel",
    ".xls": "Excel", ".pptx": "PowerPoint", ".ppt": "PowerPoint", ".odt": "OpenDocument",
    ".rtf": "Rich Text",
}


def _use_read_document(path: Path, why: str) -> ToolResult:
    kind = _DOC_EXTENSIONS.get(path.suffix.lower(), "binary")
    return ToolResult(
        ok=False,
        error=(
            f"{path.name} is a {kind} file, not UTF-8 text ({why}). "
            f"Use the read_document tool for it — it extracts text from Word, "
            f"PDF, Excel, PowerPoint and CSV. Do not retry read_file on this path."
        ),
    )


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a UTF-8 TEXT file (code, .md, .txt, .json) from the session "
        "workspace. For Word/PDF/Excel/PowerPoint use read_document instead."
    )
    reversibility = Reversibility.READONLY  # a read has no side effect to undo
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such file: {args['path']}")
        # Refuse a known document format BEFORE reading it: the extension is
        # certain knowledge, and the redirect is more useful than 13KB of
        # mojibake or a decode traceback.
        if path.suffix.lower() in _DOC_EXTENSIONS:
            return _use_read_document(path, "read_file only handles plain text")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return _use_read_document(path, f"not valid UTF-8 at byte {exc.start}")
        except OSError as exc:  # permissions, a vanished file, a locked handle
            return ToolResult(ok=False, error=f"could not read {path.name}: {exc}")
        return ToolResult(ok=True, output=text, data={"bytes": len(text)})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a UTF-8 text file in the session workspace."
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes are captured
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Snapshot the inverse of the write: prior bytes when overwriting an
        existing file (``file_restore``), or a delete of the path we are about to
        CREATE (``file_delete``). ``post_sha256`` is the newline-invariant hash of
        the content we will write, so a later external edit is detected on undo."""
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:
            return None
        post = _text_sha(args["content"])
        if target.is_file():
            try:
                prior = target.read_text(encoding="utf-8").encode("utf-8")
                mode = "text"
            except (UnicodeDecodeError, OSError):
                prior = target.read_bytes()
                mode, post = "raw", None  # can't predict text-write bytes for binary
            return make_file_descriptor(
                ctx.config.home,
                kind="file_restore",
                path=args["path"],
                mode=mode,
                prior_bytes=prior,
                pre_sha256=sha256_bytes(prior),
                post_sha256=post,
            )
        return make_file_descriptor(
            ctx.config.home,
            kind="file_delete",
            path=args["path"],
            mode="text",
            post_sha256=post,
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

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
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes are captured
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        },
        "required": ["path", "old", "new"],
    }

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Snapshot the pre-edit text. ``post_sha256`` is the hash of the exact
        text ``execute`` will produce (first-occurrence replace), so a concurrent
        edit is caught on undo. No-op when the edit won't apply (file missing / old
        text absent) — nothing will change, so there is nothing to undo."""
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:
            return None
        if not target.is_file():
            return None
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None
        if args["old"] not in text:
            return None
        new_text = text.replace(args["old"], args["new"], 1)
        return make_file_descriptor(
            ctx.config.home,
            kind="file_restore",
            path=args["path"],
            mode="text",
            prior_bytes=text.encode("utf-8"),
            pre_sha256=_text_sha(text),
            post_sha256=_text_sha(new_text),
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

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
    reversibility = Reversibility.READONLY  # a listing has no side effect
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
    reversibility = Reversibility.READONLY  # a search has no side effect
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
