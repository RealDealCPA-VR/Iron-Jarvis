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
import re
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult, safe_path
from ..tools.undo import make_file_descriptor, revert_workspace_file, sha256_bytes

_MAX_CELLS = 4000  # structured-read cap: keeps a huge sheet from flooding context


def _resolve_read_path(raw: str, ctx: ToolContext) -> Path:
    from ..core.model_cwd import remap

    p = Path(remap(raw, ctx.workspace))  # v1.465.0: the model's scratch folder
    return p if p.is_absolute() else Path(ctx.workspace) / raw


async def _read_target(raw: str, ctx: ToolContext) -> "tuple[Path, bool, str]":
    """The workbook a read tool was pointed at, and whether it may be read.

    ``live:<workbook name>`` (v1.466.0) is a workbook OPEN in Excel: the tool
    reads a fresh copy of it as it is on screen (``documents/excel_live``),
    whose source was already checked against the folders Jarvis may read."""
    if raw.strip().lower().startswith("live:"):
        from .excel_live import live_path

        got, why = await live_path(raw.strip(), ctx)
        return (Path(got), True, "") if got else (Path(raw), False, why)
    path = _resolve_read_path(raw, ctx)
    ok, reason = fs_read_ok(str(path))
    return path, ok, reason


def _open_error(exc: Exception, path: Path) -> str:
    """An honest message for a workbook openpyxl could not open.

    THE LIVE FAILURE (v1.205.0): ``excel_profile`` on a user's tax-document
    folder surfaced a raw ``BadZipFile: File is not a zip file`` — which tells
    an agent nothing it can act on. A .xlsx is a zip container, so BadZipFile's
    real-world meaning is a legacy .xls (or another format) wearing an .xlsx
    name — the suffix gate above this already passed, so only the BYTES can be
    wrong. Wording matches the documented known limit ("Legacy Office formats
    (.doc/.xls/.ppt/.odt) must be converted before preview/extraction",
    docs/TODO.md) and readers.py's ``_LEGACY_HINTS``. Every other exception is
    reported exactly as before.
    """
    from zipfile import BadZipFile

    if isinstance(exc, BadZipFile):
        return (
            f"{path.name} is not a valid .xlsx — likely a legacy .xls or a "
            "renamed file; convert it to .xlsx first (legacy Office formats "
            "need conversion)"
        )
    return f"{type(exc).__name__}: {exc}"


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
    if not include_formulas and any(v is None for r in rows for v in r):
        # v1.465.0: a formula no program saved a result for read as a blank
        # (null), which a model then reported as "no total". It is worked out.
        wb.close()
        if uncached_formulas(path, ws.title):
            from .excel_recalc import fill_rows

            res = fill_rows(path, ws.title, rows, first_row=min_row, first_col=min_col)
            if res.filled:
                out["worked_out"] = res.filled
                out["worked_out_note"] = (
                    "these formula cells had no saved result; Iron Jarvis worked them out"
                )
            # Only the cells this read asked for are named.
            inside = {}
            for where, why in res.failed.items():
                col, row = _cell_cr(where.rsplit("!", 1)[-1])
                if min_row <= row <= max_row and min_col <= col <= max_col:
                    inside[where] = why
            if inside:
                out["not_worked_out"] = dict(list(inside.items())[:20])
                out["not_worked_out_count"] = len(inside)
    return out


class ExcelReadTool(Tool):
    name = "excel_read"
    reversibility = Reversibility.READONLY
    returns_untrusted_content = True  # spreadsheet text can carry planted instructions
    description = (
        "Read an Excel workbook as STRUCTURE, not text: with only a path you "
        "get the sheet list + dimensions; add `sheet` and/or `range` (e.g. "
        "'A1:D20') for cell values as rows. Set include_formulas=true to see "
        "formulas instead of computed values. A formula with no saved result is "
        "worked out for you (`worked_out`); one that cannot be is named in "
        "`not_worked_out` and reads as null. Any policy-allowed path."
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
        path, ok, reason = await _read_target(str(args.get("path", "")), ctx)
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if path.suffix.lower() not in (".xlsx", ".xlsm", ".xls"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {path.name}")
        try:
            if path.suffix.lower() == ".xls":
                # C-10: a legacy workbook reads through xlrd. Values only —
                # the format stores no formula text — and the payload says so
                # (`legacy_xls`) rather than returning silently different data.
                from .legacy import read_xls_workbook

                data = await asyncio.to_thread(
                    read_xls_workbook, path, args.get("sheet"), args.get("range"),
                )
            else:
                data = await asyncio.to_thread(
                    _read_workbook, path, args.get("sheet"), args.get("range"),
                    bool(args.get("include_formulas")),
                )
        except Exception as exc:  # noqa: BLE001 — real files must not crash the runtime
            return ToolResult(ok=False, error=_open_error(exc, path))
        import json

        return ToolResult(ok=True, output=json.dumps(data, ensure_ascii=False), data=data)


def _apply_edits(path: Path, sheet: "str | None", edits: list[dict[str, Any]],
                 add_sheets: list[str]) -> dict[str, Any]:
    from openpyxl import load_workbook

    # keep_vba MUST track the extension: this saves back over the SAME path, and
    # openpyxl drops the vbaProject part when keep_vba is False — a .xlsm saved
    # that way loses every macro and Excel often refuses to open it at all.
    wb = load_workbook(str(path), keep_vba=path.suffix.lower() == ".xlsm")
    for name in add_sheets:
        if name not in wb.sheetnames:
            wb.create_sheet(title=name)
    applied = 0
    written: list[tuple[str, str, str]] = []  # (sheet, cell, formula)
    for e in edits:
        target = wb[str(e.get("sheet"))] if e.get("sheet") else (
            wb[sheet] if sheet else wb.active
        )
        cell = str(e.get("cell", "")).strip()
        if not cell:
            raise ValueError("every edit needs a cell (e.g. 'B2')")
        if "formula" in e:
            text = str(e["formula"]).strip()
            if text and not text.startswith("="):
                text = "=" + text
            from .excel_formula import for_file

            target[cell] = for_file(text)
            written.append((target.title, cell.upper().replace("$", ""), text))
        elif "value" in e or not any(k in e for k in _FORMAT_KEYS):
            target[cell] = e.get("value")
        # else: a formatting-only edit (it may name a range) changes no value
        _apply_cell_format(target, cell, e)
        applied += 1
    wb.save(str(path))
    formulas = _count_formulas(wb)
    sheets = list(wb.sheetnames)
    wb.close()
    out: dict[str, Any] = {"applied": applied, "sheets": sheets, "formulas": formulas}
    if formulas:
        # v1.465.0: saving from here drops every result Excel had worked out.
        # They are worked out again and saved in the file, and each formula
        # this edit wrote is answered with what it comes to.
        from .excel_recalc import store_results, summary_note

        res = store_results(path)
        results = []
        for title, cell, text in written:
            col, row = _cell_cr(cell)
            key = (title, row, col)
            entry: dict[str, Any] = {"sheet": title, "cell": cell, "formula": text}
            if key in res.values:
                entry["value"] = res.values[key]
            else:
                entry["error"] = res.failed.get(f"{title}!{cell}", "could not be worked out here")
            results.append(entry)
        out["results"] = results
        out["recalc"] = {"worked_out": res.computed, "saved_in_file": res.stored,
                         "not_worked_out": len(res.failed) + res.skipped}
        out["note"] = summary_note(res, stored=True)
    return out


_FORMAT_KEYS = ("number_format", "bold", "italic", "fill", "font_color", "align", "wrap")


def _apply_cell_format(ws: Any, cell: str, edit: dict[str, Any]) -> None:
    """Formatting an edit asked for (v1.465.0). A range ("A1:D1") is allowed
    for formatting-only edits; an unknown value is ignored, never an error."""
    if not any(k in edit for k in _FORMAT_KEYS):
        return
    from openpyxl.styles import Alignment, Font, PatternFill

    target = ws[cell]
    cells = [c for row in target for c in row] if isinstance(target, tuple) else [target]
    for c in cells[:20_000]:
        if edit.get("number_format"):
            c.number_format = str(edit["number_format"])
        if "bold" in edit or "italic" in edit or edit.get("font_color"):
            f = c.font
            color = str(edit["font_color"]).lstrip("#").upper() if edit.get("font_color") else None
            c.font = Font(
                name=f.name, size=f.size,
                bold=bool(edit["bold"]) if "bold" in edit else f.bold,
                italic=bool(edit["italic"]) if "italic" in edit else f.italic,
                underline=f.underline, strike=f.strike,
                color=("FF" + color if color and len(color) == 6 else f.color),
            )
        if edit.get("fill"):
            color = str(edit["fill"]).lstrip("#").upper()
            if re.fullmatch(r"[0-9A-F]{6}", color):
                c.fill = PatternFill("solid", fgColor="FF" + color)
        if edit.get("align") or "wrap" in edit:
            a = c.alignment
            horiz = str(edit.get("align") or "").lower()
            c.alignment = Alignment(
                horizontal=horiz if horiz in ("left", "center", "right") else a.horizontal,
                vertical=a.vertical,
                wrap_text=bool(edit["wrap"]) if "wrap" in edit else a.wrap_text,
            )


#: Said after an edit of a workbook that holds formulas (v1.464.0). Saving a
#: workbook from here drops every result Excel had worked out; until Excel
#: opens and saves it again a total reads as blank, and a later question about
#: it would be answered from nothing.
FORMULAS_UNSAVED_NOTE = (
    "Note: this workbook has {n} formula cell(s). Their results are worked out by "
    "Excel, so they read as blank until the workbook is opened and saved in Excel. "
    "Do not report a total from those cells before then; work it out from the "
    "input cells instead."
)

#: Said by a query that met formula cells with no saved result (v1.464.0).
FORMULAS_LEFT_OUT_NOTE = (
    "\nWARNING: {n} cell(s) on this sheet hold a formula that has no saved result "
    "and that Iron Jarvis could not work out. They were LEFT OUT, so this answer "
    "is incomplete. Open and save the workbook in Excel, or work the figure out "
    "from the input columns."
)

#: Said by a query that worked formula results out itself (v1.465.0).
FORMULAS_WORKED_OUT_NOTE = (
    "\nNote: {n} formula cell(s) on this sheet had no saved result; Iron Jarvis "
    "worked them out and included them."
)


def _count_formulas(wb: Any, limit: int = 200_000) -> int:
    """How many cells of an open workbook hold a formula. Bounded; never raises."""
    count = seen = 0
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    seen += 1
                    if cell.data_type == "f":
                        count += 1
                if seen > limit:
                    return count
    except Exception:  # noqa: BLE001 - a note must never fail the edit
        return count
    return count


def uncached_formulas(path: Path, sheet: "str | None", max_rows: int = 50_000) -> int:
    """How many cells of one sheet hold a formula whose result was never saved.

    BLOCKING (two read-only passes over the sheet, walked row by row in step).
    Never raises: cannot tell is 0."""
    try:
        from openpyxl import load_workbook

        from .excel_recalc import has_formulas

        if not has_formulas(path):
            return 0  # v1.465.0: no formula anywhere, so neither pass is paid

        values = load_workbook(str(path), data_only=True, read_only=True)
        formulas = load_workbook(str(path), data_only=False, read_only=True)
        try:
            vs = values[sheet] if sheet else values.active
            fs = formulas[vs.title]
            count = 0
            pairs = zip(vs.iter_rows(values_only=True), fs.iter_rows(values_only=True))
            for i, (vrow, frow) in enumerate(pairs):
                if i >= max_rows:
                    break
                for v, f in zip(vrow, frow):
                    if v is None and isinstance(f, str) and f.startswith("="):
                        count += 1
            return count
        finally:
            values.close()
            formulas.close()
    except Exception:  # noqa: BLE001
        return 0


class ExcelEditTool(Tool):
    name = "excel_edit"
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior workbook bytes captured
    description = (
        "Edit an EXISTING Excel workbook IN PLACE inside the workspace: set "
        "cell values and formulas ({cell:'B2', value:…} or {cell:'C3', "
        "formula:'=SUM(A1:A10)'}, optional per-edit sheet), format cells "
        "(number_format like '#,##0.00' or '0.0%' or 'yyyy-mm-dd', bold, "
        "italic, fill and font_color as hex, align, wrap; a formatting-only "
        "edit may name a range like 'A1:D1'), and add sheets. Every formula "
        "you write is WORKED OUT and its result is returned and saved in the "
        "file: read the results and report those numbers, never your own "
        "arithmetic. Untouched cells keep their formatting/formulas. Undoable. "
        "Note: exotic content (some charts, macros) may be simplified by the "
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
                        "number_format": {"type": "string"},
                        "bold": {"type": "boolean"},
                        "italic": {"type": "boolean"},
                        "fill": {"type": "string", "description": "Hex colour, e.g. FFF2CC"},
                        "font_color": {"type": "string", "description": "Hex colour"},
                        "align": {"type": "string", "enum": ["left", "center", "right"]},
                        "wrap": {"type": "boolean"},
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

        # OFF THE EVENT LOOP — the same hop `tools/builtins.py:369-402` applies
        # to write_file's hook, copied here. `registry.invoke` awaits
        # capture_undo BEFORE execute, so the full read of a client workbook,
        # its sha256, AND the pre-image spill (anything over INLINE_MAX_BYTES —
        # 8 KB — is written to <home>/undo/, i.e. every real workbook) all ran
        # on the daemon's one loop, and then execute read the same file again.
        def _capture() -> "dict[str, Any] | None":
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

        return await asyncio.to_thread(_capture)

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
            return ToolResult(ok=False, error=_open_error(exc, target))
        rel = str(target.relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
        # ABSOLUTE, like write_document and redact_pii (the v1.153.2 rule): a
        # workspace-relative name IS a bare filename when the workbook sits in
        # the workspace root, and the model relays it to the user verbatim.
        _abs = str(target.resolve())
        lines = [
            f"applied {result['applied']} edit(s) to {rel} "
            f"(sheets: {', '.join(result['sheets'])})",
            f"Saved to: {_abs}",
        ]
        for r in (result.get("results") or [])[:40]:
            shown = (_shown(r["value"]) if "value" in r
                     else f"NOT WORKED OUT ({r['error']})")
            lines.append(f"{r['sheet']}!{r['cell']} {r['formula']} = {shown}")
        if result.get("note"):
            lines.append(result["note"])
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"path": rel, "abs_path": _abs, **result},
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


def _shown(v: Any) -> str:
    """A worked-out result as it is reported back."""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return _fmt_num(float(v))
    return repr(v) if isinstance(v, str) else str(v)


def _load_table_computed(path: Path, sheet: "str | None") -> tuple[Any, ...]:
    """:func:`_load_table`, with formula cells nobody saved a result for worked
    out (v1.465.0). Returns ``(title, headers, rows, truncated, recalc)``;
    ``recalc`` is None when every formula already had its result."""
    title, headers, rows, truncated = _load_table(path, sheet)
    if not uncached_formulas(path, title):
        return title, headers, rows, truncated, None
    from .excel_recalc import fill_rows

    matrix = [list(headers)] + rows
    res = fill_rows(path, title, matrix, first_row=1, first_col=1)
    if res.why_not and not res.computed:
        # Too large to work out: every unsaved formula is LEFT OUT, and said.
        res.skipped = uncached_formulas(path, title)
    return title, headers, matrix[1:], truncated, res


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


def _as_date(v: Any) -> "Any | None":
    """A calendar date from a cell or a typed value, or None (v1.465.0).

    A date cell arrives as a ``datetime``; a filter value arrives as text
    ("2026-02-01", "2/1/2026"). Compared as text they never matched: every
    date filter answered 0 rows."""
    import datetime as _dt

    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if not isinstance(v, str):
        return None
    text = v.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _fmt_num(v: float) -> str:
    """A figure as it is reported: two decimals, unless that would round a
    real difference away (0.0419 was reported as 0.04, 0.004 as 0.00). Then
    up to six decimals, trailing zeros dropped."""
    if round(v, 2) == round(v, 10):
        return f"{v:,.2f}"
    text = f"{v:,.6f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _row_matches(row: list[Any], cond: dict[str, Any], headers: list[str]) -> bool:
    idx = _col_index(str(cond.get("column", "")), headers)
    cell = row[idx] if idx < len(row) else None
    op = str(cond.get("op", "eq")).lower()
    want = cond.get("value")
    import datetime as _dt

    if isinstance(cell, (_dt.date, _dt.datetime)) and op != "contains":
        da, db = _as_date(cell), _as_date(want)
        if da is None or db is None:
            return False
        return {"eq": da == db, "ne": da != db, "gt": da > db, "lt": da < db,
                "ge": da >= db, "le": da <= db}.get(op, False)
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
        "UNDERSTAND an Excel workbook before you touch it: for every sheet, "
        "what it looks like (an income statement, a budget, a data table), "
        "where its header row is, what each column holds and how it is "
        "formatted, how its formulas are filled down, its totals and what "
        "they come to, which sheets it reads from, named ranges, hidden "
        "sheets, and anything WORTH A LOOK (a typed number in a column of "
        "formulas, a SUM that stops short, a typed total that does not equal "
        "the cells above it, numbers stored as text). A compact map, not a "
        "data dump. Call this FIRST, then excel_query for figures. Any "
        "policy-allowed path (local, network share, or tailnet folder)."
    )
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path, ok, reason = await _read_target(str(args.get("path", "")), ctx)
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

        # v1.465.0: the full map first (structure, formulas, totals, checks).
        # A workbook too large or too odd for it gets the quick profile below,
        # exactly as before, and says that is what it is.
        try:
            from .excel_map import render, workbook_map

            mapped = await asyncio.to_thread(workbook_map, path)
        except Exception:  # noqa: BLE001 - the quick profile is the fallback
            mapped = None
        if mapped is not None:
            return ToolResult(ok=True, output=render(path.name, mapped), data=mapped)
        try:
            data = await asyncio.to_thread(_profile)
        except Exception as exc:  # noqa: BLE001 — real files must not crash the runtime
            return ToolResult(ok=False, error=_open_error(exc, path))
        data["quick"] = True
        lines = [f"{path.name} — {len(data['sheets'])} sheet(s) (quick view: sizes and "
                 "headers only)"]
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
        path, ok, reason = await _read_target(str(args.get("path", "")), ctx)
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {path.name}")
        op = str(args.get("op", "")).lower()
        limit = max(1, min(int(args.get("limit") or 20), 200))
        try:
            title, headers, rows, truncated, recalc = await asyncio.to_thread(
                _load_table_computed, path, args.get("sheet")
            )
            # v1.465.0: formulas with no saved result are worked out before
            # anything is added up; only the ones that could not be are missing.
            missing = (len(recalc.failed) + recalc.skipped) if recalc else 0
            worked = recalc.computed if recalc else 0
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
                if worked:
                    out += FORMULAS_WORKED_OUT_NOTE.format(n=worked)
                if missing:
                    out += FORMULAS_LEFT_OUT_NOTE.format(n=missing)
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
                # v1.465.0: every group is counted even when only some are
                # listed. The list used to stop at 20 with no word, ranked so
                # that the most NEGATIVE group (a refund) was the first to go.
                # Groups are now ranked by size either way, the cut is said,
                # and the total of ALL groups is always given.
                if agg == "count":
                    everything = sorted(counts.items(), key=lambda kv: -kv[1])
                    ranked = everything[:limit]
                    result = [{"group": k, "count": v} for k, v in ranked]
                    body = "\n".join(f"- {k}: {v}" for k, v in ranked)
                    rest = everything[limit:]
                    tail = (f"\n(+{len(rest)} more group(s) not listed, {sum(v for _, v in rest)} row(s))"
                            if rest else "")
                    tail += f"\nAll {len(everything)} group(s): {sum(counts.values())} row(s)"
                    all_groups = len(everything)
                else:
                    scored = {
                        k: (sum(v) if agg == "sum" else sum(v) / len(v))
                        for k, v in groups.items() if v
                    }
                    everything2 = sorted(scored.items(), key=lambda kv: -abs(kv[1]))
                    ranked2 = everything2[:limit]
                    result = [{"group": k, agg: v, "count": counts.get(k, 0)}
                              for k, v in ranked2]
                    body = "\n".join(f"- {k}: {_fmt_num(v)}" for k, v in ranked2)
                    rest2 = everything2[limit:]
                    tail = ""
                    if rest2 and agg == "sum":
                        tail = (f"\n(+{len(rest2)} more group(s) not listed, together "
                                f"{_fmt_num(sum(v for _, v in rest2))})")
                    elif rest2:
                        tail = f"\n(+{len(rest2)} more group(s) not listed)"
                    if agg == "sum":
                        tail += (f"\nTotal of all {len(everything2)} group(s): "
                                 f"{_fmt_num(sum(scored.values()))}")
                    all_groups = len(everything2)
                out = f"{title}: {agg} by {args.get('group_by')} ({len(rows)} rows)\n{body}{tail}"
                if worked:
                    out += FORMULAS_WORKED_OUT_NOTE.format(n=worked)
                if missing:
                    out += FORMULAS_LEFT_OUT_NOTE.format(n=missing)
                return ToolResult(ok=True, output=out, data={
                    "sheet": title, "groups": result, "rows_scanned": len(rows),
                    "groups_total": all_groups,
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
                          f" in the {len(rows)} row(s) considered"
                          + (FORMULAS_WORKED_OUT_NOTE.format(n=worked) if worked else "")
                          + (FORMULAS_LEFT_OUT_NOTE.format(n=missing) if missing else ""),
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
                f"{title}: {op.upper()} of {args.get('column')} = {_fmt_num(value)} "
                f"({len(rows)} row(s) considered{extra}){trunc}"
            )
            if worked:
                out += FORMULAS_WORKED_OUT_NOTE.format(n=worked)
            if missing:
                out += FORMULAS_LEFT_OUT_NOTE.format(n=missing)
            return ToolResult(ok=True, output=out, data={
                "sheet": title, "op": op, "column": args.get("column"),
                "value": value, "rows_considered": len(rows),
                "numeric_values": len(nums), "scan_truncated": truncated,
            })
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — real files must not crash the runtime
            return ToolResult(ok=False, error=_open_error(exc, path))


# --------------------------------------------------------------------------- #
# Formula intelligence (v1.90.0): derive → CHECK → reproduce → keep current.
# The evaluator (documents/excel_formula.py) computes a formula against the
# real sheet so a written formula is VALIDATED, not assumed; sheet specs
# capture formulas + formatting for faithful reproduction; the accounts diff
# keeps label-anchored formulas honest when rows come and go.
# --------------------------------------------------------------------------- #


class ExcelFormulaCheckTool(Tool):
    name = "excel_formula_check"
    reversibility = Reversibility.READONLY
    returns_untrusted_content = True  # sheet text can carry planted instructions
    description = (
        "VALIDATE a formula against the real workbook before trusting or "
        "writing it: computes the formula (SUM/AVERAGE/MIN/MAX/COUNT/ABS/"
        "ROUND/IF/SUBTOTAL, cell refs + ranges + arithmetic) over the sheet's "
        "actual values and, when `expected` or `expect_cell` is given, "
        "compares within `tolerance`. Pass `cell` instead of `formula` to "
        "check the formula already stored in that cell. Workflow: derive a "
        "formula → check it here → only then excel_edit it in. Any "
        "policy-allowed path."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "sheet": {"type": "string", "description": "Sheet name (default: active)"},
            "formula": {"type": "string", "description": "Formula to evaluate, e.g. '=SUM(B2:B9)'"},
            "cell": {"type": "string", "description": "Check the formula stored IN this cell"},
            "expected": {"type": "number", "description": "Expected numeric result"},
            "expect_cell": {"type": "string", "description": "Cell whose stored value the result should equal"},
            "tolerance": {"type": "number", "description": "Comparison tolerance (default 0.01)"},
        },
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .excel_formula import FormulaError, Grid, evaluate_formula

        path, ok, reason = await _read_target(str(args.get("path", "")), ctx)
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {path.name}")

        def _check() -> dict[str, Any]:
            grid = Grid(path)
            try:
                sheet = str(args.get("sheet") or "").strip() or grid.active
                formula = str(args.get("formula") or "").strip()
                cell = str(args.get("cell") or "").strip()
                if not formula and not cell:
                    raise FormulaError("pass a formula or a cell to check")
                cached = None
                if cell and not formula:
                    from openpyxl import load_workbook

                    fwb = load_workbook(str(path), data_only=False, read_only=True)
                    raw = fwb[sheet][cell].value if sheet in fwb.sheetnames else None
                    fwb.close()
                    if not (isinstance(raw, str) and raw.startswith("=")):
                        raise FormulaError(f"cell {cell} holds no formula (value: {raw!r})")
                    formula = raw
                    # The value Excel SAVED, never the engine's own answer: a
                    # formula checked against itself always matches.
                    cached = grid.saved(sheet, *_cell_cr(cell))
                computed = evaluate_formula(formula, grid, sheet)
                out: dict[str, Any] = {
                    "formula": formula, "sheet": sheet, "computed": computed,
                }
                tol = float(args.get("tolerance") or 0.01)
                target = None
                target_src = ""
                if args.get("expected") is not None:
                    target, target_src = float(args["expected"]), "expected"
                elif str(args.get("expect_cell") or "").strip():
                    ec = str(args["expect_cell"]).strip()
                    target = grid.get(sheet, *_cell_cr(ec))
                    target_src = f"cell {ec}"
                elif cached is not None:
                    target, target_src = cached, f"cached value of {cell}"
                if target is not None:
                    try:
                        delta = abs(float(computed) - float(target))
                    except (TypeError, ValueError):
                        raise FormulaError(
                            f"cannot compare {computed!r} to {target!r} numerically"
                        )
                    out.update(target=float(target), target_source=target_src,
                               delta=delta, match=delta <= tol, tolerance=tol)
                elif cell and cached is None:
                    out["note"] = (
                        "no cached value to compare against (the file was not "
                        "saved by Excel) — pass expected= or expect_cell="
                    )
                return out
            finally:
                grid.close()

        try:
            data = await asyncio.to_thread(_check)
        except FormulaError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — real files must not crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        comp = data["computed"]
        comp_s = f"{comp:,.2f}" if isinstance(comp, (int, float)) else repr(comp)
        if "match" in data:
            verdict = (
                f"MATCH (Δ {data['delta']:.4f} ≤ {data['tolerance']})"
                if data["match"]
                else f"MISMATCH — computed {comp_s} vs {data['target']:,.2f} "
                     f"({data['target_source']}, Δ {data['delta']:.4f})"
            )
            out = f"{data['formula']} on {data['sheet']} = {comp_s} → {verdict}"
        else:
            out = f"{data['formula']} on {data['sheet']} = {comp_s}"
            if data.get("note"):
                out += f" ({data['note']})"
        return ToolResult(ok=True, output=out, data=data)


def _cell_cr(ref: str) -> tuple[int, int]:
    """A1 → (col, row) for Grid.get."""
    import re as _re

    m = _re.match(r"^\$?([A-Za-z]{1,3})\$?([0-9]{1,7})$", ref.strip())
    if m is None:
        raise ValueError(f"not a cell reference: {ref!r}")
    from openpyxl.utils import column_index_from_string

    return column_index_from_string(m.group(1).upper()), int(m.group(2))


class ExcelSheetSpecTool(Tool):
    name = "excel_sheet_spec"
    reversibility = Reversibility.READONLY
    returns_untrusted_content = True
    description = (
        "Capture a sheet's REUSABLE STRUCTURE as a spec: every formula cell "
        "(with its cached value when the file was saved by Excel), non-default "
        "formatting (number formats, bold, fills), column widths, merges, and "
        "the account labels in `label_column`. Feed the spec to "
        "excel_apply_spec to reproduce the sheet elsewhere, and save the "
        "workflow as a skill (skill_create) for future reuse."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "sheet": {"type": "string", "description": "Sheet name (default: active)"},
            "label_column": {"type": "string", "description": "Account-label column (default A)"},
        },
        "required": ["path"],
    }

    _MAX_SPEC_CELLS = 400

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path, ok, reason = await _read_target(str(args.get("path", "")), ctx)
        if not ok:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"not an Excel workbook: {path.name}")

        def _capture() -> dict[str, Any]:
            from openpyxl import load_workbook
            from openpyxl.utils import get_column_letter, column_index_from_string

            fwb = load_workbook(str(path), data_only=False)
            vwb = load_workbook(str(path), data_only=True)
            want = str(args.get("sheet") or "").strip()
            fws = fwb[want] if want and want in fwb.sheetnames else fwb.active
            vws = vwb[fws.title]
            label_col = str(args.get("label_column") or "A").strip().upper()
            li = column_index_from_string(label_col)
            cells: dict[str, dict[str, Any]] = {}
            truncated = False
            for row in fws.iter_rows():
                for c in row:
                    entry: dict[str, Any] = {}
                    if isinstance(c.value, str) and c.value.startswith("="):
                        entry["formula"] = c.value
                        cached = vws.cell(row=c.row, column=c.column).value
                        if isinstance(cached, (int, float)) and not isinstance(cached, bool):
                            entry["value"] = float(cached)
                    if c.number_format and c.number_format != "General":
                        entry["number_format"] = c.number_format
                    if c.font is not None and bool(c.font.bold):
                        entry["bold"] = True
                    fill = c.fill
                    if (
                        fill is not None and fill.patternType == "solid"
                        and getattr(fill.fgColor, "rgb", None)
                        and fill.fgColor.rgb != "00000000"
                    ):
                        entry["fill"] = str(fill.fgColor.rgb)
                    if entry:
                        if len(cells) >= self._MAX_SPEC_CELLS:
                            truncated = True
                            break
                        cells[c.coordinate] = entry
                if truncated:
                    break
            labels: dict[str, str] = {}
            for r in range(1, min(fws.max_row, 2000) + 1):
                v = fws.cell(row=r, column=li).value
                if v is not None and str(v).strip() and not str(v).startswith("="):
                    labels[str(r)] = str(v).strip()
            widths = {
                letter: dim.width
                for letter, dim in fws.column_dimensions.items()
                if dim.width is not None
            }
            merges = [str(m) for m in fws.merged_cells.ranges]
            spec = {
                "sheet": fws.title, "label_column": label_col, "labels": labels,
                "cells": cells, "column_widths": widths, "merges": merges,
                "truncated": truncated,
            }
            fwb.close()
            vwb.close()
            return spec

        try:
            spec = await asyncio.to_thread(_capture)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        formulas = sum(1 for c in spec["cells"].values() if "formula" in c)
        out = (
            f"spec of {spec['sheet']}: {formulas} formula cell(s), "
            f"{len(spec['cells'])} styled/formula cell(s), "
            f"{len(spec['labels'])} label(s) in column {spec['label_column']}, "
            f"{len(spec['merges'])} merge(s)"
            + (" [cell capture capped at 400]" if spec["truncated"] else "")
        )
        return ToolResult(ok=True, output=out, data=spec)


class ExcelApplySpecTool(Tool):
    name = "excel_apply_spec"
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes captured
    description = (
        "REPRODUCE a captured sheet spec (excel_sheet_spec) onto a workbook in "
        "the workspace: writes the formulas, number formats, bold/fills, "
        "column widths and merges, then VALIDATES by computing every written "
        "formula against the target's real values. With "
        "verify_against_source=true it also compares each computed result to "
        "the spec's recorded values — proving the reproduction matches the "
        "original. The spec may also carry beauty `options` (theme "
        "professional/minimal/warm, autosize, freeze_header, banded, "
        "number_formats) and `charts` ([{type: bar/line/pie, title, "
        "data_range, categories_range, anchor}]) — the engine applies them; "
        "invalid charts are skipped with warnings, never a failed write. "
        "Creates the file/sheet when missing. Undoable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative target workbook"},
            "sheet": {"type": "string", "description": "Target sheet (default: the spec's)"},
            "spec": {"type": "object", "description": "A spec from excel_sheet_spec"},
            "verify_against_source": {
                "type": "boolean",
                "description": "Also compare computed formulas to the spec's recorded values",
            },
            "tolerance": {"type": "number", "description": "Comparison tolerance (default 0.01)"},
        },
        "required": ["path", "spec"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:  # noqa: BLE001
            return None

        # Offloaded for the same reason ExcelEditTool's hook is (see there):
        # read + sha256 + pre-image spill of a whole workbook, awaited before
        # execute, on the daemon's single event loop.
        def _capture() -> "dict[str, Any] | None":
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

        return await asyncio.to_thread(_capture)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .excel_formula import FormulaError, Grid, evaluate_formula

        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=str(exc))
        spec = args.get("spec")
        if not isinstance(spec, dict) or not isinstance(spec.get("cells"), dict):
            return ToolResult(ok=False, error="spec must be an excel_sheet_spec object")
        if target.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error=f"target must be a workbook: {target.name}")

        def _apply() -> dict[str, Any]:
            from openpyxl import Workbook, load_workbook
            from openpyxl.styles import Font, PatternFill

            sheet_name = (
                str(args.get("sheet") or "").strip()
                or str(spec.get("sheet") or "").strip() or "Sheet1"
            )
            if target.is_file():
                # Same in-place save hazard as _apply_edits: without keep_vba an
                # existing .xlsm comes back macro-free (and often unopenable).
                wb = load_workbook(str(target), keep_vba=target.suffix.lower() == ".xlsm")
            else:
                wb = Workbook()
                wb.active.title = sheet_name
            ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb.create_sheet(sheet_name)
            applied = {"formulas": 0, "formats": 0}
            formula_cells: list[str] = []
            for ref, entry in spec["cells"].items():
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get("formula"), str):
                    from .excel_formula import for_file

                    ws[ref] = for_file(entry["formula"])
                    applied["formulas"] += 1
                    formula_cells.append(ref)
                styled = False
                if entry.get("number_format"):
                    ws[ref].number_format = str(entry["number_format"])
                    styled = True
                if entry.get("bold"):
                    ws[ref].font = Font(bold=True)
                    styled = True
                if entry.get("fill"):
                    rgb = str(entry["fill"])
                    ws[ref].fill = PatternFill(
                        start_color=rgb, end_color=rgb, fill_type="solid"
                    )
                    styled = True
                if styled:
                    applied["formats"] += 1
            for letter, width in (spec.get("column_widths") or {}).items():
                try:
                    ws.column_dimensions[str(letter)].width = float(width)
                except (TypeError, ValueError):
                    pass
            for rng in spec.get("merges") or []:
                try:
                    ws.merge_cells(str(rng))
                except Exception:  # noqa: BLE001 — a bad merge must not abort the rest
                    pass

            # BEAUTY (v1.134.0): a spec MAY carry declarative "options"
            # (theme/autosize/freeze_header/banded/number_formats) and
            # "charts" — applied via the SAME engine helpers write_document
            # uses, with invalid charts skipped into `warnings`. Legacy specs
            # carry neither key, so this block is a no-op for them and the
            # historical behavior is untouched.
            from .themes import get_theme
            from .writers import xlsx_add_charts, xlsx_apply_beauty

            warnings: list[str] = []
            charts_added = 0
            if isinstance(spec.get("charts"), list):
                charts_added = xlsx_add_charts(ws, spec["charts"], warnings)
            beauty = spec.get("options")
            if isinstance(beauty, dict) and beauty:
                theme = get_theme(str(beauty.get("theme") or ""))
                if beauty.get("theme") and theme is None:
                    warnings.append(f"unknown theme {beauty['theme']!r}")
                xlsx_apply_beauty(ws, theme, beauty, warnings)
            wb.save(str(target))
            wb.close()
            if formula_cells:
                from .excel_recalc import store_results

                store_results(target)  # v1.465.0: results kept in the file

            # VALIDATE: compute every written formula against the saved target.
            tol = float(args.get("tolerance") or 0.01)
            verify = bool(args.get("verify_against_source"))
            grid = Grid(target)
            checks: list[dict[str, Any]] = []
            failed = 0
            try:
                for ref in formula_cells:
                    entry = spec["cells"][ref]
                    row: dict[str, Any] = {"cell": ref, "formula": entry["formula"]}
                    try:
                        computed = evaluate_formula(entry["formula"], grid, sheet_name)
                        row["computed"] = computed
                        if verify and isinstance(entry.get("value"), (int, float)):
                            delta = abs(float(computed) - float(entry["value"]))
                            row.update(source_value=float(entry["value"]),
                                       delta=delta, match=delta <= tol)
                            if not row["match"]:
                                failed += 1
                    except FormulaError as exc:
                        row["error"] = str(exc)  # honest: couldn't verify this one
                    checks.append(row)
            finally:
                grid.close()
            return {"sheet": sheet_name, "applied": applied,
                    "validation": checks, "failed": failed, "verified": verify,
                    "charts_added": charts_added, "beauty_warnings": warnings}

        try:
            data = await asyncio.to_thread(_apply)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        rel = str(target.relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
        head = (
            f"VALIDATION FAILED — {data['failed']} formula(s) do not match the source"
            if data["failed"]
            else "reproduced + validated"
        )
        out = (
            f"{head}: {data['applied']['formulas']} formula(s) + "
            f"{data['applied']['formats']} formatted cell(s) on {data['sheet']} of {rel}"
        )
        # Beauty extras surface only when the spec asked for them — a legacy
        # spec's output line reads exactly as it always has.
        if data["charts_added"]:
            out += f" + {data['charts_added']} chart(s)"
        if data["beauty_warnings"]:
            out += "\n" + "\n".join(f"warning: {w}" for w in data["beauty_warnings"])
        # ABSOLUTE too (the v1.153.2 rule) — same reason as excel_edit above.
        _abs = str(target.resolve())
        out += f"\nSaved to: {_abs}"
        return ToolResult(ok=True, output=out, data={"path": rel, "abs_path": _abs, **data})


class ExcelAccountsDiffTool(Tool):
    name = "excel_accounts_diff"
    reversibility = Reversibility.READONLY
    returns_untrusted_content = True
    description = (
        "Compare the ACCOUNT LABELS of two sheets (or two versions of a "
        "statement): which accounts were added, removed, or moved to a "
        "different row. Use it when a financial statement changed to see "
        "exactly which label-anchored ranges a working formula must absorb — "
        "then rebuild the formula from the new rows and prove it with "
        "excel_formula_check. Any policy-allowed paths."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path_a": {"type": "string", "description": "The ORIGINAL workbook"},
            "sheet_a": {"type": "string"},
            "path_b": {"type": "string", "description": "The CURRENT workbook (default: path_a)"},
            "sheet_b": {"type": "string"},
            "label_column": {"type": "string", "description": "Account-label column (default A)"},
        },
        "required": ["path_a"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pa = _resolve_read_path(str(args.get("path_a", "")), ctx)
        pb = _resolve_read_path(str(args.get("path_b") or args.get("path_a", "")), ctx)
        for p in (pa, pb):
            ok, reason = fs_read_ok(str(p))
            if not ok:
                return ToolResult(ok=False, error=f"read denied: {reason}")
            if p.suffix.lower() not in (".xlsx", ".xlsm"):
                return ToolResult(ok=False, error=f"not an Excel workbook: {p.name}")

        def _labels(path: Path, sheet: "str | None") -> dict[str, int]:
            from openpyxl import load_workbook
            from openpyxl.utils import column_index_from_string

            wb = load_workbook(str(path), data_only=True, read_only=True)
            ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
            li = column_index_from_string(
                str(args.get("label_column") or "A").strip().upper()
            )
            out: dict[str, int] = {}
            for r, row in enumerate(ws.iter_rows(min_col=li, max_col=li,
                                                 values_only=True), start=1):
                if r > 5000:
                    break
                v = row[0]
                label = str(v).strip() if v is not None else ""
                if label and label not in out:  # first occurrence anchors
                    out[label] = r
            wb.close()
            return out

        try:
            a = await asyncio.to_thread(_labels, pa, args.get("sheet_a"))
            b = await asyncio.to_thread(
                _labels, pb, args.get("sheet_b") or args.get("sheet_a")
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        added = sorted((lbl, r) for lbl, r in b.items() if lbl not in a)
        removed = sorted((lbl, r) for lbl, r in a.items() if lbl not in b)
        moved = sorted(
            (lbl, a[lbl], b[lbl]) for lbl in a if lbl in b and a[lbl] != b[lbl]
        )
        lines = [
            f"accounts: {len(a)} before, {len(b)} now — "
            f"{len(added)} added, {len(removed)} removed, {len(moved)} moved"
        ]
        lines += [f"+ {lbl} (row {r})" for lbl, r in added[:20]]
        lines += [f"- {lbl} (was row {r})" for lbl, r in removed[:20]]
        lines += [f"~ {lbl}: row {r1} → {r2}" for lbl, r1, r2 in moved[:20]]
        if added or removed or moved:
            lines.append(
                "Update label-anchored ranges to the NEW rows, then prove the "
                "updated formula with excel_formula_check before writing it."
            )
        return ToolResult(ok=True, output="\n".join(lines), data={
            "added": [{"label": lbl, "row": r} for lbl, r in added],
            "removed": [{"label": lbl, "row": r} for lbl, r in removed],
            "moved": [{"label": lbl, "from": r1, "to": r2} for lbl, r1, r2 in moved],
            "labels_now": b,
        })


def excel_tools() -> list[Tool]:
    return [
        ExcelReadTool(), ExcelEditTool(), ExcelProfileTool(), ExcelQueryTool(),
        ExcelFormulaCheckTool(), ExcelSheetSpecTool(), ExcelApplySpecTool(),
        ExcelAccountsDiffTool(),
    ]
