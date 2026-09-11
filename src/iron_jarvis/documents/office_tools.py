"""The office capability tools: edit a Word file, compare two documents, read
and fill PDF forms.

Each one follows this package's existing discipline rather than inventing its
own: sources are READ from anywhere through ``fs_read_ok``, outputs are WRITTEN
only inside the session workspace through ``safe_path``, every write is TX-01
undoable and reports the ABSOLUTE path it wrote (the v1.153.2 rule — a
workspace-relative name is a bare filename in the workspace root and the model
relays it verbatim), and every blocking step runs off the event loop.

The engines live beside this module (``docx_edit``, ``compare``, ``pdf_forms``);
this file is the agent-facing surface only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import (
    Reversibility,
    RiskClass,
    Tool,
    ToolContext,
    ToolResult,
    safe_path,
    unwritable_workspace_error,
)
from ..tools.undo import make_file_descriptor, revert_workspace_file, sha256_bytes
from .tools import _resolve_read_path


def _rel(target: Path, ctx: ToolContext) -> str:
    try:
        return str(target.resolve().relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
    except ValueError:  # in_place on a file the workspace holds by another route
        return str(target)


async def _capture_output_undo(target: Path, rel: str, ctx: ToolContext) -> "dict[str, Any] | None":
    """The inverse of writing *target*: restore its prior bytes, or delete it.

    One hop for the whole hook — the read, the sha256 AND the pre-image spill
    all block (``documents/tools.WriteDocumentTool.capture_undo``'s note)."""

    def _capture() -> "dict[str, Any] | None":
        if target.is_file():
            try:
                prior = target.read_bytes()
            except OSError:
                return None
            return make_file_descriptor(
                ctx.config.home, kind="file_restore", path=rel, mode="raw",
                prior_bytes=prior, pre_sha256=sha256_bytes(prior),
            )
        return make_file_descriptor(ctx.config.home, kind="file_delete", path=rel, mode="raw")

    return await asyncio.to_thread(_capture)


class DocxEditTool(Tool):
    name = "docx_edit"
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes / created file
    risk_class = RiskClass.PAGE_ACTION
    description = (
        "Edit an EXISTING Word document while keeping how it looks — its "
        "letterhead, fonts, styles, headers, footers and numbering all stay as "
        "they are (write_document would retype the file and lose them). "
        "Operations: `replace` (swap exact text; formatting kept even when the "
        "text is split across runs), `set_cell` (one table cell, 1-based table/"
        "row/column), `insert_after` (new paragraph(s) after the paragraph "
        "containing `anchor`), `set_header_footer`. Saves a copy named "
        "'<name> (edited).docx' in the workspace unless `in_place` is true, "
        "which is allowed only for a document already in the workspace. If any "
        "`replace` finds nothing the whole call FAILS and no file is written — "
        "never report such a call as done. Undoable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The .docx to edit (absolute or workspace-relative)."},
            "operations": {
                "type": "array",
                "description": (
                    "In order. {op:'replace', find, replace, count?('all'|n), "
                    "match_case?} | {op:'set_cell', table, row, column, value} | "
                    "{op:'insert_after', anchor, text, style?} | "
                    "{op:'set_header_footer', part:'header'|'footer', text, section?}"
                ),
                "items": {"type": "object"},
            },
            "output": {
                "type": "string",
                "description": "Optional workspace-relative name for the edited copy.",
            },
            "in_place": {
                "type": "boolean",
                "description": "Overwrite the document itself (workspace files only).",
            },
        },
        "required": ["path", "operations"],
    }

    def _paths(self, args: dict[str, Any], ctx: ToolContext) -> tuple[Path, Path]:
        source = _resolve_read_path(str(args.get("path") or ""), ctx)
        workspace = Path(ctx.workspace)
        if bool(args.get("in_place")):
            return source, safe_path(workspace, str(source))
        wanted = str(args.get("output") or "").strip() or f"{source.stem} (edited).docx"
        return source, safe_path(workspace, wanted)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        try:
            _source, target = self._paths(args, ctx)
        except Exception:  # noqa: BLE001 — execute reports the real reason
            return None
        return await _capture_output_undo(target, _rel(target, ctx), ctx)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .docx_edit import DocxEditError, edit_docx

        raw = args.get("operations")
        operations = [op for op in (raw or []) if isinstance(op, dict)]
        if not operations:
            return ToolResult(ok=False, error="pass at least one operation (replace, set_cell, insert_after, set_header_footer)")
        try:
            source, target = self._paths(args, ctx)
        except PermissionError:
            return ToolResult(
                ok=False,
                error=(
                    "in_place can only edit a document that is already in this "
                    "conversation's folder — leave it out and an edited copy is "
                    "saved there instead"
                ) if args.get("in_place") else "the output path escapes the workspace",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        allowed, reason = fs_read_ok(source)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        if not source.is_file():
            return ToolResult(ok=False, error=f"no such document: {args.get('path')}")
        if source.suffix.lower() != ".docx":
            extra = (
                " — convert it to .docx first with convert_document"
                if source.suffix.lower() == ".doc" else ""
            )
            return ToolResult(ok=False, error=f"docx_edit only edits .docx files, not {source.suffix or source.name}{extra}")
        if target.suffix.lower() != ".docx":
            return ToolResult(ok=False, error="the output name must end in .docx")
        existed = target.is_file()
        try:
            lines = await asyncio.to_thread(edit_docx, source, target, operations)
        except DocxEditError as exc:
            return ToolResult(ok=False, error=str(exc))
        except PermissionError as exc:
            return ToolResult(ok=False, error=unwritable_workspace_error(exc, ctx.workspace))
        except Exception as exc:  # noqa: BLE001 — a real file must never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        abs_path = str(target.resolve())
        return ToolResult(
            ok=True,
            output=f"edited {source.name}: " + "; ".join(lines) + f"\nSaved to: {abs_path}",
            data={"path": _rel(target, ctx), "abs_path": abs_path, "source": str(source), "applied": lines},
            created_paths=None if existed else [abs_path],
        )


class CompareDocumentsTool(Tool):
    name = "compare_documents"
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True  # both documents are third-party content
    description = (
        "Compare TWO documents and report exactly what differs — never from "
        "memory. Spreadsheets (.xlsx/.xls/.csv) are compared cell by cell per "
        "sheet; pass `label_column` (a header name or column letter) to match "
        "rows by their label so an inserted row does not read as everything "
        "changing. Other documents are compared paragraph by paragraph, with a "
        "table of the numbers that moved. `mode` overrides the choice: text, "
        "cells or figures. Reads only — write the report with write_document if "
        "the user wants it saved."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path_a": {"type": "string", "description": "The first (earlier) document."},
            "path_b": {"type": "string", "description": "The second (later) document."},
            "mode": {"type": "string", "enum": ["auto", "text", "cells", "figures"]},
            "label_column": {
                "type": "string",
                "description": "Spreadsheets: header name, column letter or 1-based index to match rows on.",
            },
        },
        "required": ["path_a", "path_b"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .compare import CompareError, compare_documents, render_report

        paths: list[Path] = []
        for key in ("path_a", "path_b"):
            p = _resolve_read_path(str(args.get(key) or ""), ctx)
            allowed, reason = fs_read_ok(p)
            if not allowed:
                return ToolResult(ok=False, error=reason)
            if not p.is_file():
                return ToolResult(ok=False, error=f"no such document: {args.get(key)}")
            paths.append(p)
        try:
            result = await asyncio.to_thread(
                compare_documents, paths[0], paths[1],
                str(args.get("mode") or "auto"),
                str(args.get("label_column") or "") or None,
            )
        except CompareError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, output=render_report(result), data=result)


class PdfFormFieldsTool(Tool):
    name = "pdf_form_fields"
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True
    description = (
        "List the fillable fields of a PDF form (W-9, 8879, an application): "
        "each field's name, type, current value and, where the form defines "
        "them, the allowed options. Read-only — pdf_form_fill does the filling. "
        "A PDF with no fields is a flat page and says so."
    )
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .pdf_forms import FormError, list_fields

        path = _resolve_read_path(str(args.get("path") or ""), ctx)
        allowed, reason = fs_read_ok(path)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such file: {args.get('path')}")
        try:
            fields = await asyncio.to_thread(list_fields, path)
        except FormError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if not fields:
            return ToolResult(
                ok=True,
                output=(
                    f"{path.name} has no fillable form fields — it is a flat PDF. "
                    "Nothing can be typed into it; fill it in a PDF editor, or ask "
                    "for the answers as a list."
                ),
                data={"path": str(path), "fields": []},
            )
        lines = [f"{len(fields)} field(s) in {path.name}:"]
        for f in fields:
            opts = f" [{', '.join(f['options'])}]" if f.get("options") else ""
            value = f" = {f['value']}" if f.get("value") else ""
            lines.append(f"- {f['name']} ({f['type']}){opts}{value}")
        return ToolResult(ok=True, output="\n".join(lines), data={"path": str(path), "fields": fields})


class PdfFormFillTool(Tool):
    name = "pdf_form_fill"
    reversibility = Reversibility.REVERSIBLE
    risk_class = RiskClass.PAGE_ACTION
    description = (
        "Fill a PDF form's fields into a COPY (the original is never changed): "
        "pass `values` as {field name: value}; checkboxes take yes/no or a "
        "state name. The copy is saved as '<name> (filled).pdf' in the "
        "workspace unless `output` says otherwise, then RE-OPENED to verify "
        "every value took — if any did not, the copy is deleted and the call "
        "fails. Use pdf_form_fields first to see the names. Undoable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The PDF form to fill (absolute or workspace-relative)."},
            "values": {"type": "object", "description": "{field name: value} — text, or yes/no for a checkbox."},
            "output": {"type": "string", "description": "Optional workspace-relative name for the filled copy."},
        },
        "required": ["path", "values"],
    }

    def _paths(self, args: dict[str, Any], ctx: ToolContext) -> tuple[Path, Path]:
        source = _resolve_read_path(str(args.get("path") or ""), ctx)
        wanted = str(args.get("output") or "").strip() or f"{source.stem} (filled).pdf"
        return source, safe_path(Path(ctx.workspace), wanted)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        try:
            _source, target = self._paths(args, ctx)
        except Exception:  # noqa: BLE001
            return None
        return await _capture_output_undo(target, _rel(target, ctx), ctx)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .pdf_forms import FormError, fill_form

        values = args.get("values")
        if not isinstance(values, dict) or not values:
            return ToolResult(ok=False, error="pass `values` as {field name: value} — nothing to fill")
        try:
            source, target = self._paths(args, ctx)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        allowed, reason = fs_read_ok(source)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        if not source.is_file():
            return ToolResult(ok=False, error=f"no such file: {args.get('path')}")
        if source.suffix.lower() != ".pdf":
            return ToolResult(ok=False, error=f"pdf_form_fill only fills PDFs, not {source.suffix or source.name}")
        if target.suffix.lower() != ".pdf":
            return ToolResult(ok=False, error="the output name must end in .pdf")
        try:
            same = target.resolve() == source.resolve()
        except OSError:
            same = False
        if same:
            return ToolResult(ok=False, error="the filled copy must be a different file from the form itself")
        existed = target.is_file()
        try:
            result = await asyncio.to_thread(fill_form, source, target, dict(values))
        except FormError as exc:
            return ToolResult(ok=False, error=str(exc))
        except PermissionError as exc:
            return ToolResult(ok=False, error=unwritable_workspace_error(exc, ctx.workspace))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        abs_path = str(target.resolve())
        filled = ", ".join(f"{k} = {v}" for k, v in list(result["values"].items())[:12])
        more = f" (+{len(result['values']) - 12} more)" if len(result["values"]) > 12 else ""
        empty = result.get("still_empty") or []
        tail = ""
        if empty:
            shown = ", ".join(empty[:8])
            tail = f"\nStill empty: {shown}" + (f" (+{len(empty) - 8} more)" if len(empty) > 8 else "")
        return ToolResult(
            ok=True,
            output=(
                f"filled {result['filled']} field(s) in a copy of {source.name}: "
                f"{filled}{more}\nSaved to: {abs_path}{tail}"
            ),
            data={
                "path": _rel(target, ctx), "abs_path": abs_path, "source": str(source),
                "filled": result["filled"], "values": result["values"],
                "still_empty": empty,
            },
            created_paths=None if existed else [abs_path],
        )


def office_tools() -> list[Tool]:
    """The four office capability tools (registered by ``document_tools``)."""
    return [DocxEditTool(), CompareDocumentsTool(), PdfFormFieldsTool(), PdfFormFillTool()]
