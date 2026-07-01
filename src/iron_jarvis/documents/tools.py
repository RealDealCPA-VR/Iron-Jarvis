"""Document tools (§19).

Three tools that let agents work with a user's real files:

* ``read_document``  — extract text from ANY local path (reading the user's real
  files is the point), absolute or workspace-relative.
* ``write_document`` — create a document WITHIN the session workspace only.
* ``extract_pdf``    — read_document specialised to PDFs.

``document_tools()`` is a plain factory (no platform needed).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Tool, ToolContext, ToolResult, safe_path
from .readers import extract_text
from .writers import write_document

#: Cap on tool output to keep large documents from flooding the context window.
_MAX_OUTPUT = 16_000


def _resolve_read_path(raw: str, ctx: ToolContext) -> Path:
    """Absolute paths are used as-is; relative paths resolve under the workspace."""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (Path(ctx.workspace) / raw)


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_OUTPUT:
        return text, False
    note = f"\n\n... [truncated to {_MAX_OUTPUT} of {len(text)} characters]"
    return text[:_MAX_OUTPUT] + note, True


class ReadDocumentTool(Tool):
    name = "read_document"
    returns_untrusted_content = True  # a read file can carry planted instructions
    description = (
        "Extract text from a document of any type — PDF, Word (.docx), Excel "
        "(.xlsx), PowerPoint (.pptx), CSV, or plain text/code. May target ANY "
        "local path (absolute, or relative to the workspace)."
    )
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = _resolve_read_path(args["path"], ctx)
        allowed, reason = fs_read_ok(path)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        try:
            text = await asyncio.to_thread(extract_text, path)  # CPU-bound parse off the loop
        except Exception as exc:  # reading real files must never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        out, truncated = _truncate(text)
        return ToolResult(
            ok=True,
            output=out,
            data={"path": str(path), "chars": len(text), "truncated": truncated},
        )


class WriteDocumentTool(Tool):
    name = "write_document"
    description = (
        "Create a document inside the session workspace. The file type follows "
        "the path suffix (.docx/.xlsx/.pptx/.pdf/.csv/.txt/.md), or the optional "
        "`kind` override. `content` is a string (paragraphs/lines split on "
        "newline) or a list of rows (list[list]) for sheets/tables."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {
                "description": (
                    "A string (split into paragraphs/lines on newline) or a list "
                    "of rows for spreadsheet/CSV output."
                )
            },
            "kind": {
                "type": "string",
                "description": "Optional format override, e.g. 'pdf' or 'docx'.",
            },
        },
        "required": ["path", "content"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            target = safe_path(ctx.workspace, args["path"])
            out = write_document(target, args["content"], kind=args.get("kind"))
        except Exception as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        rel = str(out.relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
        size = out.stat().st_size
        return ToolResult(
            ok=True,
            output=f"wrote {size} bytes to {rel}",
            data={"path": rel, "bytes": size},
        )


class ExtractPdfTool(Tool):
    name = "extract_pdf"
    returns_untrusted_content = True  # a PDF can carry planted instructions
    description = "Extract the text of a PDF file (absolute or workspace-relative path)."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = _resolve_read_path(args["path"], ctx)
        if path.suffix.lower() != ".pdf":
            return ToolResult(ok=False, error=f"not a PDF file: {args['path']}")
        allowed, reason = fs_read_ok(path)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        try:
            text = await asyncio.to_thread(extract_text, path)  # CPU-bound parse off the loop
        except Exception as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        out, truncated = _truncate(text)
        return ToolResult(
            ok=True,
            output=out,
            data={"path": str(path), "chars": len(text), "truncated": truncated},
        )


def document_tools() -> list[Tool]:
    """Build the document tools (no platform dependency)."""
    return [ReadDocumentTool(), WriteDocumentTool(), ExtractPdfTool()]
