"""Excel parity tools: structured reads + IN-PLACE edits of real workbooks.

What made "Claude works with Excel" magic is not the model — it's the pair of
capabilities these tools provide: reading a workbook as STRUCTURE (sheets,
cells, formulas, ranges — not a text dump) and editing an EXISTING workbook
in place (set cells, write formulas, add sheets) while everything untouched
keeps its formatting. Any tool-capable model — local or frontier — drives
them identically, so a verified local endpoint gets full parity.

Honesty & safety:
* reads reach any policy-allowed path; edits are WORKSPACE-confined (the
  project folder in practice) via ``safe_path`` like every write tool;
* edits are TX-01 reversible (prior bytes captured — undo restores the exact
  workbook);
* openpyxl preserves cell formatting/formulas it doesn't touch, but exotic
  content (macros beyond .xlsm passthrough, some chart types) can be
  simplified — the tool says so rather than pretending otherwise.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult, safe_path
from ..tools.undo import make_file_descriptor, revert_workspace_file, sha256_bytes

_MAX_CELLS = 4000  # structured-read cap: keeps a huge sheet from flooding context


def _resolve_read_path(raw: str, ctx: ToolContext) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else Path(ctx.workspace) / raw


def _read_workbook(path: Path, sheet: "str | None", cell_range: "str | None",
                   include_formulas: bool) -> dict[str, Any]:
    from openpyxl import load_workbook
    from openpyxl.utils import range_boundaries

    wb = load_workbook(str(path), data_only=not include_formulas)
    out: dict[str, Any] = {"sheets": wb.sheetnames}
    if sheet is None and cell_range is None:
        # Overview mode: names + dimensions per sheet.
        out["overview"] = [
            {"sheet": ws.title, "rows": ws.max_row, "cols": ws.max_column}
            for ws in wb.worksheets
        ]
        return out
    ws = wb[sheet] if sheet else wb.active
    out["sheet"] = ws.title
    if cell_range:
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    else:
        min_col, min_row, max_col, max_row = 1, 1, ws.max_column, ws.max_row
    cells = 0
    rows: list[list[Any]] = []
    for row in ws.iter_rows(min_row=min_row, max_row=max_row,
                            min_col=min_col, max_col=max_col):
        r: list[Any] = []
        for c in row:
            v = c.value
            r.append(str(v) if v is not None and not isinstance(v, (int, float, bool)) else v)
            cells += 1
        rows.append(r)
        if cells >= _MAX_CELLS:
            out["truncated"] = True
            break
    out["rows"] = rows
    return out


class ExcelReadTool(Tool):
    name = "excel_read"
    reversibility = Reversibility.READONLY
    returns_untrusted_content = True  # spreadsheet text can carry planted instructions
    description = (
        "Read an Excel workbook as STRUCTURE, not text: with only a path you "
        "get the sheet list + dimensions; add `sheet` and/or `range` (e.g. "
        "'A1:D20') for cell values as rows. Set include_formulas=true to see "
        "formulas instead of computed values. Any policy-allowed path."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "sheet": {"type": "string", "description": "Sheet name (default: active)"},
            "range": {"type": "string", "description": "A1-style range, e.g. B2:F30"},
            "include_formulas": {"type": "boolean"},
        },
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = _resolve_read_path(str(args.get("path", "")), ctx)
        ok, reason = fs_read_ok(str(path))
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {path.name}")
        try:
            data = await asyncio.to_thread(
                _read_workbook, path, args.get("sheet"), args.get("range"),
                bool(args.get("include_formulas")),
            )
        except Exception as exc:  # noqa: BLE001 — real files must not crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        import json

        return ToolResult(ok=True, output=json.dumps(data, ensure_ascii=False), data=data)


def _apply_edits(path: Path, sheet: "str | None", edits: list[dict[str, Any]],
                 add_sheets: list[str]) -> dict[str, Any]:
    from openpyxl import load_workbook

    wb = load_workbook(str(path))  # keep_vba not needed for .xlsx; .xlsm noted
    for name in add_sheets:
        if name not in wb.sheetnames:
            wb.create_sheet(title=name)
    applied = 0
    for e in edits:
        target = wb[str(e.get("sheet"))] if e.get("sheet") else (
            wb[sheet] if sheet else wb.active
        )
        cell = str(e.get("cell", "")).strip()
        if not cell:
            raise ValueError("every edit needs a cell (e.g. 'B2')")
        if "formula" in e:
            target[cell] = str(e["formula"])
        else:
            target[cell] = e.get("value")
        applied += 1
    wb.save(str(path))
    return {"applied": applied, "sheets": wb.sheetnames}


class ExcelEditTool(Tool):
    name = "excel_edit"
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior workbook bytes captured
    description = (
        "Edit an EXISTING Excel workbook IN PLACE inside the workspace: set "
        "cell values and formulas ({cell:'B2', value:…} or {cell:'C3', "
        "formula:'=SUM(A1:A10)'}, optional per-edit sheet), and add sheets. "
        "Untouched cells keep their formatting/formulas. Undoable. Note: "
        "exotic content (some charts, macros) may be simplified by the "
        "editor — mention it when the workbook looks complex."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative workbook path"},
            "sheet": {"type": "string", "description": "Default sheet for edits"},
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cell": {"type": "string"},
                        "value": {},
                        "formula": {"type": "string"},
                        "sheet": {"type": "string"},
                    },
                    "required": ["cell"],
                },
            },
            "add_sheets": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["path"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:
            return None
        if not target.is_file():
            return None
        try:
            prior = target.read_bytes()
        except OSError:
            return None
        return make_file_descriptor(
            ctx.config.home, kind="file_restore", path=args["path"],
            mode="raw", prior_bytes=prior, pre_sha256=sha256_bytes(prior),
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=str(exc))
        if not target.is_file():
            return ToolResult(
                ok=False,
                error=f"no such workbook in the workspace: {args.get('path')} "
                      "(use write_document to create one first)",
            )
        if target.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {target.name}")
        edits = [e for e in (args.get("edits") or []) if isinstance(e, dict)]
        add_sheets = [str(s) for s in (args.get("add_sheets") or []) if str(s).strip()]
        if not edits and not add_sheets:
            return ToolResult(ok=False, error="nothing to do — pass edits and/or add_sheets")
        try:
            result = await asyncio.to_thread(
                _apply_edits, target, args.get("sheet"), edits, add_sheets
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        rel = str(target.relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
        return ToolResult(
            ok=True,
            output=f"applied {result['applied']} edit(s) to {rel} "
                   f"(sheets: {', '.join(result['sheets'])})",
            data={"path": rel, **result},
        )


def excel_tools() -> list[Tool]:
    return [ExcelReadTool(), ExcelEditTool()]
