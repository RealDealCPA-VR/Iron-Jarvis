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


# --------------------------------------------------------------------------- #
# Engine-computed analysis (v1.89.0): profile + query. Local models hallucinate
# most on ARITHMETIC — so numbers must come from the engine, never the model.
# Both tools return compact text (headers + exact figures), sized for small
# context windows. Pure openpyxl row iteration — no pandas dependency.
# --------------------------------------------------------------------------- #

_SCAN_ROW_CAP = 50_000  # rows scanned per query — bounded, with an honest flag


def _cell_str(v: Any) -> str:
    return "" if v is None else str(v)


def _load_table(path: Path, sheet: "str | None") -> tuple[str, list[str], list[list[Any]], bool]:
    """(sheet_title, headers, data_rows, truncated) — headers = first row."""
    from openpyxl import load_workbook

    wb = load_workbook(str(path), data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb.active
    headers: list[str] = []
    rows: list[list[Any]] = []
    truncated = False
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [_cell_str(v).strip() for v in row]
            continue
        if len(rows) >= _SCAN_ROW_CAP:
            truncated = True
            break
        rows.append(list(row))
    title = ws.title
    wb.close()
    return title, headers, rows, truncated


def _col_index(column: str, headers: list[str]) -> int:
    """Resolve a column by HEADER NAME (case-insensitive) or Excel letter."""
    want = (column or "").strip()
    if not want:
        raise ValueError("a column is required (header name or letter like 'B')")
    lowered = [h.lower() for h in headers]
    if want.lower() in lowered:
        return lowered.index(want.lower())
    if want.isalpha() and len(want) <= 3:  # Excel letter(s)
        from openpyxl.utils import column_index_from_string

        return column_index_from_string(want.upper()) - 1
    raise ValueError(
        f"no column {want!r} — headers are: {', '.join(h for h in headers if h) or '(none)'}"
    )


def _as_number(v: Any) -> "float | None":
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


_WHERE_OPS = ("eq", "ne", "contains", "gt", "lt", "ge", "le")


def _row_matches(row: list[Any], cond: dict[str, Any], headers: list[str]) -> bool:
    idx = _col_index(str(cond.get("column", "")), headers)
    cell = row[idx] if idx < len(row) else None
    op = str(cond.get("op", "eq")).lower()
    want = cond.get("value")
    if op in ("gt", "lt", "ge", "le"):
        a, b = _as_number(cell), _as_number(want)
        if a is None or b is None:
            return False
        return {"gt": a > b, "lt": a < b, "ge": a >= b, "le": a <= b}[op]
    a_s, b_s = _cell_str(cell).strip().lower(), _cell_str(want).strip().lower()
    if op == "eq":
        return a_s == b_s
    if op == "ne":
        return a_s != b_s
    if op == "contains":
        return b_s in a_s
    raise ValueError(f"unknown where op {op!r} — use one of {_WHERE_OPS}")


class ExcelProfileTool(Tool):
    name = "excel_profile"
    reversibility = Reversibility.READONLY
    returns_untrusted_content = True  # sheet text can carry planted instructions
    description = (
        "Orient in an Excel workbook CHEAPLY: every sheet's name, row/column "
        "counts, headers, and one sample row — a compact map, not a data dump. "
        "Call this FIRST, then excel_query for figures. Any policy-allowed "
        "path (local, network share, or tailnet folder)."
    )
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = _resolve_read_path(str(args.get("path", "")), ctx)
        ok, reason = fs_read_ok(str(path))
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {path.name}")

        def _profile() -> dict[str, Any]:
            from openpyxl import load_workbook

            wb = load_workbook(str(path), data_only=True, read_only=True)
            sheets = []
            for ws in wb.worksheets:
                first = next(ws.iter_rows(values_only=True, max_row=1), tuple())
                second = next(
                    ws.iter_rows(values_only=True, min_row=2, max_row=2), tuple()
                )
                sheets.append({
                    "sheet": ws.title,
                    "rows": ws.max_row,
                    "cols": ws.max_column,
                    "headers": [_cell_str(v).strip() for v in first[:24]],
                    "sample": [_cell_str(v)[:40] for v in second[:24]],
                })
            wb.close()
            return {"sheets": sheets}

        try:
            data = await asyncio.to_thread(_profile)
        except Exception as exc:  # noqa: BLE001 — real files must not crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        lines = [f"{path.name} — {len(data['sheets'])} sheet(s)"]
        for s in data["sheets"]:
            heads = ", ".join(h for h in s["headers"] if h) or "(no header row)"
            lines.append(f"- {s['sheet']}: {s['rows']}x{s['cols']} | headers: {heads}")
        return ToolResult(ok=True, output="\n".join(lines), data=data)


class ExcelQueryTool(Tool):
    name = "excel_query"
    reversibility = Reversibility.READONLY
    returns_untrusted_content = True  # spreadsheet text can carry planted instructions
    description = (
        "Compute over an Excel sheet with the ENGINE — exact numbers, never "
        "mental math: op sum/avg/min/max/count on a column, group (group_by + "
        "agg per group), or filter (matching rows). Columns by header name or "
        "letter; optional `where` conditions (eq/ne/contains/gt/lt/ge/le) "
        "combine with AND. ALWAYS use this for figures — report its results "
        "exactly. Any policy-allowed path."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "sheet": {"type": "string", "description": "Sheet name (default: active)"},
            "op": {
                "type": "string",
                "enum": ["sum", "avg", "min", "max", "count", "group", "filter"],
            },
            "column": {"type": "string", "description": "Target column (aggregates)"},
            "group_by": {"type": "string", "description": "Grouping column (op=group)"},
            "agg": {
                "type": "string",
                "enum": ["sum", "avg", "count"],
                "description": "Per-group aggregate (op=group, default sum)",
            },
            "where": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "op": {"type": "string", "enum": list(_WHERE_OPS)},
                        "value": {},
                    },
                    "required": ["column"],
                },
            },
            "limit": {"type": "integer", "description": "Rows/groups returned (default 20)"},
        },
        "required": ["path", "op"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = _resolve_read_path(str(args.get("path", "")), ctx)
        ok, reason = fs_read_ok(str(path))
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {path.name}")
        op = str(args.get("op", "")).lower()
        limit = max(1, min(int(args.get("limit") or 20), 50))
        try:
            title, headers, rows, truncated = await asyncio.to_thread(
                _load_table, path, args.get("sheet")
            )
            conds = [c for c in (args.get("where") or []) if isinstance(c, dict)]
            if conds:
                rows = [r for r in rows if all(_row_matches(r, c, headers) for c in conds)]

            if op == "filter":
                shown = rows[:limit]
                head = " | ".join(h or "·" for h in headers)
                body = "\n".join(
                    " | ".join(_cell_str(v)[:40] for v in r) for r in shown
                )
                note = f" (showing {len(shown)} of {len(rows)} matching rows)"
                out = f"{title}: {len(rows)} matching row(s){note}\n{head}\n{body}"
                return ToolResult(ok=True, output=out, data={
                    "sheet": title, "matches": len(rows), "headers": headers,
                    "rows": [[_cell_str(v) for v in r] for r in shown],
                    "scan_truncated": truncated,
                })

            if op == "group":
                gi = _col_index(str(args.get("group_by", "")), headers)
                agg = str(args.get("agg") or "sum").lower()
                ci = _col_index(str(args.get("column", "")), headers) if agg != "count" else -1
                groups: dict[str, list[float]] = {}
                counts: dict[str, int] = {}
                for r in rows:
                    key = _cell_str(r[gi] if gi < len(r) else None).strip() or "(blank)"
                    counts[key] = counts.get(key, 0) + 1
                    if ci >= 0:
                        n = _as_number(r[ci] if ci < len(r) else None)
                        if n is not None:
                            groups.setdefault(key, []).append(n)
                if agg == "count":
                    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
                    result = [{"group": k, "count": v} for k, v in ranked]
                    body = "\n".join(f"- {k}: {v}" for k, v in ranked)
                else:
                    scored = {
                        k: (sum(v) if agg == "sum" else sum(v) / len(v))
                        for k, v in groups.items() if v
                    }
                    ranked2 = sorted(scored.items(), key=lambda kv: -kv[1])[:limit]
                    result = [{"group": k, agg: v, "count": counts.get(k, 0)}
                              for k, v in ranked2]
                    body = "\n".join(f"- {k}: {v:,.2f}" for k, v in ranked2)
                out = f"{title}: {agg} by {args.get('group_by')} ({len(rows)} rows)\n{body}"
                return ToolResult(ok=True, output=out, data={
                    "sheet": title, "groups": result, "rows_scanned": len(rows),
                    "scan_truncated": truncated,
                })

            # Column aggregates: sum / avg / min / max / count.
            ci = _col_index(str(args.get("column", "")), headers)
            nums = [n for r in rows
                    if (n := _as_number(r[ci] if ci < len(r) else None)) is not None]
            nonempty = sum(1 for r in rows if _cell_str(r[ci] if ci < len(r) else None).strip())
            if op == "count":
                value: float = float(nonempty)
            elif not nums:
                return ToolResult(
                    ok=False,
                    error=f"column {args.get('column')!r} has no numeric values"
                          f" in the {len(rows)} row(s) considered",
                )
            elif op == "sum":
                value = sum(nums)
            elif op == "avg":
                value = sum(nums) / len(nums)
            elif op == "min":
                value = min(nums)
            elif op == "max":
                value = max(nums)
            else:
                return ToolResult(ok=False, error=f"unknown op {op!r}")
            skipped = nonempty - len(nums) if op != "count" else 0
            extra = f", {skipped} non-numeric skipped" if skipped > 0 else ""
            trunc = " [scan capped — figures cover the first 50k rows]" if truncated else ""
            out = (
                f"{title}: {op.upper()} of {args.get('column')} = {value:,.2f} "
                f"({len(rows)} row(s) considered{extra}){trunc}"
            )
            return ToolResult(ok=True, output=out, data={
                "sheet": title, "op": op, "column": args.get("column"),
                "value": value, "rows_considered": len(rows),
                "numeric_values": len(nums), "scan_truncated": truncated,
            })
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — real files must not crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")


def excel_tools() -> list[Tool]:
    return [ExcelReadTool(), ExcelEditTool(), ExcelProfileTool(), ExcelQueryTool()]
