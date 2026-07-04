"""Document tools (§19).

Four tools that let agents work with a user's real files:

* ``read_document``     — extract text from ANY local path (reading the user's
  real files is the point), absolute or workspace-relative.
* ``write_document``    — create a document WITHIN the session workspace only.
* ``extract_pdf``       — read_document specialised to PDFs.
* ``convert_document``  — read any supported source and re-write it as any
  supported target format (csv<->xlsx keep real rows/cells).

``document_tools()`` is a plain factory (no platform needed).
"""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Tool, ToolContext, ToolResult, safe_path
from .pdf_markdown import MARKITDOWN_SUFFIXES, document_to_markdown
from .readers import SUPPORTED_READ, extract_text
from .writers import SUPPORTED_WRITE, write_document

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


class ListFolderTool(Tool):
    name = "list_folder"
    description = (
        "List a REAL folder anywhere on this machine (e.g. the user's Downloads "
        "or Documents) — name, size, modified time per entry, biggest first. Use "
        "ABSOLUTE paths for the user's actual files (the session workspace is "
        "only a scratch area). Reads are policy-gated."
    )
    permission_key = "list_folder"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute folder path (or workspace-relative)"},
            "limit": {"type": "number", "description": "Max entries (default 200)"},
        },
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        folder = _resolve_read_path(str(args.get("path", "")), ctx)
        ok, reason = fs_read_ok(str(folder))
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if not folder.is_dir():
            return ToolResult(ok=False, error=f"not a folder: {folder}")
        limit = max(1, min(int(args.get("limit") or 200), 1000))
        entries: list[tuple[str, bool, int, float]] = []
        try:
            for p in folder.iterdir():
                try:
                    st = p.stat()
                    entries.append((p.name, p.is_dir(), st.st_size, st.st_mtime))
                except OSError:
                    continue
        except OSError as exc:
            return ToolResult(ok=False, error=f"could not list {folder}: {exc}")
        entries.sort(key=lambda e: e[2], reverse=True)  # biggest first
        shown = entries[:limit]
        from datetime import datetime

        lines = [
            f"{'DIR  ' if is_dir else ''}{name}  —  {size:,} bytes  —  "
            f"{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}"
            for name, is_dir, size, mtime in shown
        ]
        header = f"{folder} — {len(entries)} entries" + (
            f" (showing {len(shown)})" if len(shown) < len(entries) else ""
        )
        body = header + ("\n" + "\n".join(lines) if lines else "\n(empty)")
        return ToolResult(ok=True, output=body, data={"path": str(folder), "total": len(entries)})


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
        "the path suffix (.docx/.xlsx/.pptx/.pdf/.csv/.html/.txt/.md), or the "
        "optional `kind` override. String content is markdown-aware: '# ' "
        "headings, -/1. lists, **bold**/*italic*, ``` code fences, | pipe | "
        "tables and '---' rules become REAL headings, lists, tables and slides "
        "in .docx/.pdf/.pptx/.html (plain text still works everywhere). A list "
        "of rows (list[list]) writes spreadsheet/CSV rows, and .xlsx also "
        "accepts {'sheets': {name: rows}} for a multi-sheet workbook."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {
                "description": (
                    "A string (markdown renders as rich formatting; plain text "
                    "becomes paragraphs/lines), a list of rows for "
                    "spreadsheet/CSV output, or {'sheets': {name: rows}} for a "
                    "multi-sheet .xlsx."
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


#: Formats where a conversion must preserve real rows/cells, not flattened text.
_TABULAR = {".csv", ".xlsx"}


def _load_for_conversion(source: Path, src_suffix: str, tgt_suffix: str) -> Any:
    """Read ``source`` as the richest content shape the target can accept."""
    if src_suffix in _TABULAR and tgt_suffix in _TABULAR:
        if src_suffix == ".csv":  # real csv parsing, not text lines
            with open(source, newline="", encoding="utf-8", errors="replace") as f:
                return [list(row) for row in csv.reader(f)]
        return _xlsx_content(source, keep_sheets=(tgt_suffix == ".xlsx"))
    # PDF/office/HTML -> Markdown: preserve real structure (headings, lists,
    # tables) instead of flattening to plain text. Written verbatim into the
    # .md by write_document's text writer. Falls back to extract_text inside
    # document_to_markdown if markitdown can't handle the source.
    if tgt_suffix == ".md" and src_suffix in MARKITDOWN_SUFFIXES:
        return document_to_markdown(source)
    return extract_text(source)


def _xlsx_content(source: Path, *, keep_sheets: bool) -> Any:
    """Re-extract real rows from a workbook via openpyxl (not flattened text)."""
    from openpyxl import load_workbook

    # xlsx->xlsx keeps formulas (data_only=False); xlsx->csv wants cached values.
    wb = load_workbook(
        filename=str(source), read_only=True, data_only=not keep_sheets
    )
    try:
        sheets = {
            ws.title: [
                ["" if c is None else c for c in row]
                for row in ws.iter_rows(values_only=True)
            ]
            for ws in wb.worksheets
        }
    finally:
        wb.close()
    if keep_sheets and len(sheets) > 1:
        return {"sheets": sheets}
    return [row for rows in sheets.values() for row in rows]


class ConvertDocumentTool(Tool):
    name = "convert_document"
    description = (
        "Convert a document from one format to another: read any supported "
        "source (PDF, .docx, .xlsx, .pptx, .csv, text/code) and re-write it as "
        "any supported target (.docx/.xlsx/.pptx/.pdf/.csv/.html/.txt/.md). "
        "csv<->xlsx conversions preserve real rows and cells; other sources "
        "are extracted to text and rendered with markdown-aware formatting. "
        "The source may be ANY local path (absolute or workspace-relative); "
        "the target is created inside the session workspace."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Path of the document to read.",
            },
            "target": {
                "type": "string",
                "description": (
                    "Path of the document to create; its suffix picks the "
                    "output format."
                ),
            },
        },
        "required": ["source", "target"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        source = _resolve_read_path(args["source"], ctx)
        src_suffix = source.suffix.lower()
        if src_suffix not in SUPPORTED_READ:
            return ToolResult(
                ok=False,
                error=(
                    f"cannot read {src_suffix or source.name!r} — supported "
                    f"source formats: {', '.join(sorted(SUPPORTED_READ))}"
                ),
            )
        allowed, reason = fs_read_ok(source)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        try:
            target = safe_path(ctx.workspace, args["target"])
        except Exception as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        tgt_suffix = target.suffix.lower()
        if tgt_suffix not in SUPPORTED_WRITE:
            return ToolResult(
                ok=False,
                error=(
                    f"cannot write {tgt_suffix or target.name!r} — supported "
                    f"target formats: {', '.join(sorted(SUPPORTED_WRITE))}"
                ),
            )
        try:
            content = await asyncio.to_thread(  # CPU-bound parse off the loop
                _load_for_conversion, source, src_suffix, tgt_suffix
            )
            out = await asyncio.to_thread(write_document, target, content)
        except Exception as exc:  # converting real files must never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        rel = str(out.relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
        size = out.stat().st_size
        return ToolResult(
            ok=True,
            output=f"converted {source.name} -> {rel} ({size} bytes)",
            data={"source": str(source), "path": rel, "bytes": size},
        )


def document_tools() -> list[Tool]:
    """Build the document tools (no platform dependency)."""
    return [
        ReadDocumentTool(),
        WriteDocumentTool(),
        ExtractPdfTool(),
        ConvertDocumentTool(),
        ListFolderTool(),
    ]
