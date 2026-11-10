"""A workbook MAP: what each sheet is, how it is laid out, and what looks off (v1.465.0).

``excel_profile`` used to answer with sheet names, sizes, the first row and one
sample row. That is enough to find a column; it is not enough to UNDERSTAND a
workbook: where the header really is, which columns are typed and which are
calculated, how the formulas are filled down, where the totals are and what
they come to, which sheets feed which, and what a careful reviewer would stop
at (a typed number in a column of formulas, a SUM that stops two rows short, a
typed total that does not equal the cells above it).

Everything here is DETERMINISTIC and read-only: structure from the file,
values from :mod:`excel_formula` (so a formula nobody saved a result for is
still worked out). The "looks like" line is a labelled guess from the sheet's
own words, never a model call. BLOCKING and bounded (:data:`MAX_ROWS`,
:data:`MAX_COLS`, :data:`DEADLINE_S`); what was not looked at is said.
"""

from __future__ import annotations

import datetime as _dt
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .excel_formula import (
    _ERROR_CODES,
    ExcelError,
    FormulaError,
    Grid,
    _col_letters,
    _col_num,
    _tokenize,
    for_reading,
)
from .excel_recalc import tidy, too_large

#: Rows and columns of each sheet that are studied.
MAX_ROWS = 5_000
MAX_COLS = 60
#: Wall-clock bound for the whole map.
DEADLINE_S = 20.0
#: At most this many notes per sheet under "worth a look".
MAX_CHECKS = 10
#: At most this many formulas per sheet are worked out to look for errors.
MAX_FORMULAS_CHECKED = 20_000

_REF_RX = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})$")
_TOTAL_FORMULA_RX = re.compile(r"^=\s*(SUM|SUBTOTAL)\s*\(", re.IGNORECASE)
_SUM_SAME_COL_RX = re.compile(
    r"^=\s*SUM\(\s*\$?([A-Za-z]{1,3})\$?([0-9]{1,7})\s*:\s*\$?([A-Za-z]{1,3})\$?([0-9]{1,7})\s*\)\s*$",
    re.IGNORECASE,
)
_TOTAL_LABEL_RX = re.compile(r"^\s*(?:grand\s+|sub)?totals?\b|^\s*net\s+(?:income|total)\b", re.IGNORECASE)
_NUMBER_TEXT_RX = re.compile(r"^\s*[-+(]?\$?\s?\d[\d,]*(?:\.\d+)?\)?%?\s*$")

#: (what the sheet's own words say, what it is called). First match wins.
_PURPOSES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rx, re.IGNORECASE), name)
    for rx, name in (
        # The words start with the sheet's own name: a sheet NAMED for its
        # settings is the inputs sheet whatever its rows mention.
        (r"^\s*(?:inputs?|assumptions?|settings|parameters|rates|lookups?|config)\b",
         "an inputs sheet (settings other sheets read)"),
        (r"\btrial\s+balance\b|\bdebits?\b.*\bcredits?\b", "a trial balance"),
        (r"\bbalance\s+sheet\b|\bliabilit(?:y|ies)\b.*\bequity\b|\btotal\s+assets\b", "a balance sheet"),
        (r"\bp\s?&\s?l\b|\bprofit\s+(?:and|&)\s+loss\b|\bincome\s+statement\b|\bnet\s+income\b|\bgross\s+profit\b",
         "an income statement"),
        (r"\bcash\s+flows?\b", "a cash flow statement"),
        (r"\bdepreciation\b|\bfixed\s+assets?\b|\buseful\s+life\b", "a depreciation schedule"),
        (r"\bamortization\b|\bprincipal\b.*\binterest\b|\bloan\b", "a loan schedule"),
        (r"\bbudget\b|\bforecast\b|\bactuals?\b.*\bvariance\b", "a budget"),
        (r"\binvoice\b|\bbill\s+to\b|\bamount\s+due\b", "an invoice"),
        (r"\baging\b|\breceivables?\b|\bpast\s+due\b", "a receivables or payables aging"),
        (r"\bpayroll\b|\bgross\s+pay\b|\bwages\b|\bwithholding\b", "a payroll sheet"),
        (r"\btimesheet\b|\bhours\s+worked\b|\bbillable\b", "a timesheet"),
        (r"\binventory\b|\bsku\b|\bon\s+hand\b|\breorder\b", "an inventory list"),
        (r"\bexpenses?\b|\breimbursements?\b|\breceipts?\b", "an expense list"),
        (r"\bledger\b|\bjournal\b|\btransactions?\b", "a transaction list"),
        (r"\bsales\b|\borders?\b|\bcustomers?\b|\bclients?\b", "a sales or client list"),
    )
)


def formula_pattern(formula: str, row: int, col: int) -> str:
    """The formula with each cell reference written RELATIVE to its own cell,
    so a formula filled down a column is one pattern (``R[0]C[-2]*R[0]C[-1]``)."""
    try:
        tokens = _tokenize(formula[1:] if formula.startswith("=") else formula)
    except FormulaError:
        return formula
    out: list[str] = []
    for kind, val in tokens:
        if kind == "cell":
            m = _REF_RX.match(val)
            if m is None:
                out.append(val)
                continue
            c, r = _col_num(m.group(2)), int(m.group(4))
            out.append(
                (f"R{r}" if m.group(3) else f"R[{r - row}]")
                + (f"C{c}" if m.group(1) else f"C[{c - col}]")
            )
        elif kind in ("func", "ident", "colrange"):
            out.append(val.upper())
        elif kind != "end":
            out.append(val)
    return "".join(out)


def _kind(cell: Any) -> str:
    v = cell.value
    if v is None:
        return ""
    if cell.data_type == "f":
        return "f"
    if cell.data_type == "e" or (isinstance(v, str) and v in _ERROR_CODES):
        return "e"
    if isinstance(v, bool):
        return "b"
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return "d"
    if isinstance(v, (int, float)):
        return "d" if getattr(cell, "is_date", False) else "n"
    return "s"


_KIND_WORD = {"n": "number", "s": "text", "d": "date", "f": "formula", "b": "yes/no", "e": "error"}


def _num(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        f = float(v)
        if round(f, 2) == round(f, 10):
            return f"{f:,.2f}"
        text = f"{f:,.6f}".rstrip("0")
        return text + "0" if text.endswith(".") else text
    return str(v)[:40]


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    """Sorted row numbers as inclusive runs."""
    out: list[tuple[int, int]] = []
    for r in sorted(rows):
        if out and r == out[-1][1] + 1:
            out[-1] = (out[-1][0], r)
        else:
            out.append((r, r))
    return out


def _ref(col: int, row: int) -> str:
    return f"{_col_letters(col)}{row}"


def _sheet_map(ws: Any, grid: Grid, deadline: float) -> dict[str, Any]:
    title = ws.title
    max_row, max_col = int(ws.max_row or 0), int(ws.max_column or 0)
    rows, cols = min(max_row, MAX_ROWS), min(max_col, MAX_COLS)
    out: dict[str, Any] = {
        "sheet": title, "rows": max_row, "cols": max_col,
        "state": str(getattr(ws, "sheet_state", "visible") or "visible"),
    }
    if rows < max_row or cols < max_col:
        out["studied"] = f"the first {rows} rows and {cols} columns"

    kinds: dict[tuple[int, int], str] = {}
    cells: dict[tuple[int, int], Any] = {}
    formats: dict[tuple[int, int], str] = {}
    for row in ws.iter_rows(min_row=1, max_row=max(rows, 1), min_col=1, max_col=max(cols, 1)):
        for cell in row:
            k = _kind(cell)
            if not k:
                continue
            key = (cell.row, cell.column)
            kinds[key] = k
            cells[key] = for_reading(cell.value) if k == "f" else cell.value
            fmt = str(cell.number_format or "")
            if fmt and fmt != "General":
                formats[key] = fmt

    # -- the header row: the first mostly-text row with data under it --------
    header_row = 0
    for r in range(1, min(rows, 15) + 1):
        here = [kinds[(r, c)] for c in range(1, cols + 1) if (r, c) in kinds]
        below = [kinds[(r + 1, c)] for c in range(1, cols + 1) if (r + 1, c) in kinds]
        if len(here) >= 2 and sum(1 for k in here if k == "s") / len(here) >= 0.8 and below:
            if any(k != "s" for k in below) or len(below) >= len(here):
                header_row = r
                break
    out["header_row"] = header_row or None
    first = [cells.get((header_row or 1, c)) for c in range(1, min(cols, 24) + 1)]
    out["headers"] = ["" if v is None else str(v).strip() for v in first]
    data_start = (header_row or 0) + 1
    second = [cells.get((data_start, c)) for c in range(1, min(cols, 24) + 1)]
    out["sample"] = ["" if v is None else str(v)[:40] for v in second]

    # -- columns: what each one holds ---------------------------------------
    columns: list[dict[str, Any]] = []
    by_col: dict[int, list[tuple[int, str]]] = {}
    for (r, c), k in kinds.items():
        if r >= data_start:
            by_col.setdefault(c, []).append((r, k))
    for c in sorted(by_col):
        tally = Counter(k for _, k in by_col[c])
        total = sum(tally.values())
        top, n = tally.most_common(1)[0]
        kind = _KIND_WORD.get(top, top) if n / total >= 0.8 else "mixed"
        fmts = Counter(formats[(r, c)] for r, _ in by_col[c] if (r, c) in formats)
        col: dict[str, Any] = {
            "column": _col_letters(c),
            "header": (str(cells.get((header_row, c), "")).strip() if header_row else ""),
            "kind": kind, "filled": total,
        }
        if fmts:
            col["format"] = fmts.most_common(1)[0][0]
        columns.append(col)
    out["columns"] = columns

    # -- formulas: patterns filled down, totals, what each comes to ---------
    formula_cells = sorted(k for k, v in kinds.items() if v == "f")
    patterns: dict[tuple[int, int], str] = {}
    for (r, c) in formula_cells:
        v = cells[(r, c)]
        patterns[(r, c)] = formula_pattern(v, r, c) if isinstance(v, str) else "(array formula)"

    values: dict[tuple[int, int], Any] = {}
    failed: dict[str, str] = {}
    unsaved = 0

    def value_of(r: int, c: int) -> Any:
        if (r, c) in values:
            return values[(r, c)]
        grid._reads = 0
        try:
            v = grid.get(title, c, r)
        except ExcelError as exc:
            v = exc.code  # said under "worth a look", not as a gap of ours
        except FormulaError as exc:
            v = None
            failed.setdefault(_ref(c, r), str(exc).split(" — ")[0][:120])
        except Exception as exc:  # noqa: BLE001
            v = None
            failed.setdefault(_ref(c, r), type(exc).__name__)
        values[(r, c)] = tidy(v)
        return values[(r, c)]

    groups: list[dict[str, Any]] = []
    dominant: dict[int, str] = {}
    for c in sorted({c for _, c in formula_cells}):
        col_cells = [(r, cc) for (r, cc) in formula_cells if cc == c]
        tally = Counter(patterns[k] for k in col_cells)
        top, n = tally.most_common(1)[0]
        if n >= 3 and n / len(col_cells) >= 0.6:
            dominant[c] = top
        for pattern, _count in tally.most_common():
            rows_here = [r for (r, _) in col_cells if patterns[(r, c)] == pattern]
            for a, b in _runs(rows_here):
                example = cells[(a, c)]
                groups.append({
                    "range": _ref(c, a) if a == b else f"{_ref(c, a)}:{_ref(c, b)}",
                    "formula": example if isinstance(example, str) else "(array formula)",
                    "cells": b - a + 1,
                })
    groups.sort(key=lambda g: (-g["cells"], g["range"]))
    out["formula_count"] = len(formula_cells)
    out["formula_groups"] = groups[:12]
    if len(groups) > 12:
        out["formula_groups_more"] = len(groups) - 12

    def label_for(r: int, c: int) -> str:
        for cc in range(1, c):
            if kinds.get((r, cc)) == "s":
                return str(cells[(r, cc)]).strip()[:40]
        return ""

    totals: list[dict[str, Any]] = []
    depends: set[str] = set()
    for (r, c) in formula_cells:
        f = cells[(r, c)]
        if not isinstance(f, str):
            continue
        for m in re.finditer(r"(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_.]*))!", f):
            name = (m.group(1) or m.group(2) or "").replace("''", "'")
            if name and name.lower() != title.lower():
                depends.add(name)
        try:
            if grid.saved(title, c, r) is None:
                unsaved += 1
        except Exception:  # noqa: BLE001
            pass
        if _TOTAL_FORMULA_RX.match(f) and len(totals) < 12 and time.monotonic() < deadline:
            totals.append({"cell": _ref(c, r), "label": label_for(r, c), "formula": f,
                           "value": value_of(r, c)})
    out["totals"] = totals
    out["uses_sheets"] = sorted(depends)
    out["formulas_unsaved"] = unsaved
    # Every formula is worked out once (bounded), so one that comes to an
    # error is found even when no total reads it.
    error_cells: list[tuple[int, int, str]] = []
    for (r, c) in formula_cells[:MAX_FORMULAS_CHECKED]:
        if time.monotonic() > deadline:
            break
        v = value_of(r, c)
        if isinstance(v, str) and v in _ERROR_CODES:
            error_cells.append((r, c, v))

    # -- worth a look --------------------------------------------------------
    checks: list[str] = []

    def note(text: str) -> None:
        if len(checks) < MAX_CHECKS:
            checks.append(text)

    saved_errors = [k for k, v in kinds.items() if v == "e"]
    for (r, c) in saved_errors[:3]:
        note(f"{_ref(c, r)} holds the error {cells[(r, c)]}")
    if len(saved_errors) > 3:
        note(f"{len(saved_errors) - 3} more cell(s) hold an error value")
    for (r, c, code) in error_cells[:3]:
        note(f"{_ref(c, r)} {cells[(r, c)]} comes to the error {code}")
    if len(error_cells) > 3:
        note(f"{len(error_cells) - 3} more formula(s) come to an error")

    for c, pattern in dominant.items():
        mine = [r for (r, cc) in formula_cells if cc == c and patterns[(r, c)] == pattern]
        lo, hi = min(mine), max(mine)
        example = cells[(mine[0], c)]
        typed = [r for r in range(lo, hi + 1) if kinds.get((r, c)) == "n"]
        for r in typed[:3]:
            note(f"{_ref(c, r)} is a typed number ({_num(cells[(r, c)])}) in a column of "
                 f"formulas ({example} filled down)")
        if len(typed) > 3:
            note(f"{len(typed) - 3} more typed number(s) sit in column {_col_letters(c)}'s formulas")
        odd = [r for (r, cc) in formula_cells
               if cc == c and lo < r < hi and patterns[(r, c)] != pattern
               and not _TOTAL_FORMULA_RX.match(str(cells[(r, c)]))]
        for r in odd[:3]:
            note(f"{_ref(c, r)} {cells[(r, c)]} does not follow its column's pattern "
                 f"({example} filled down)")

    for (r, c) in formula_cells:
        f = cells[(r, c)]
        m = _SUM_SAME_COL_RX.match(f) if isinstance(f, str) else None
        if not m or _col_num(m.group(1)) != c or _col_num(m.group(3)) != c:
            continue
        end = max(int(m.group(2)), int(m.group(4)))
        if r <= end:
            continue
        # The numbers that carry straight on below the range (a blank row
        # before the total is ordinary and ends the run).
        missed = []
        rr = end + 1
        while rr < r and kinds.get((rr, c)) in ("n", "f"):
            missed.append(rr)
            rr += 1
        if missed:
            a, b = missed[0], missed[-1]
            where = _ref(c, a) if a == b else f"{_ref(c, a)}:{_ref(c, b)}"
            note(f"{_ref(c, r)} {f} stops short: it leaves out {where}, just below its range")

    for (r, c), k in sorted(kinds.items()):
        if k != "n" or r < data_start or time.monotonic() > deadline:
            continue
        label = label_for(r, c)
        if not label or not _TOTAL_LABEL_RX.match(label):
            continue
        block: list[int] = []
        rr = r - 1
        while rr >= data_start and kinds.get((rr, c)) in ("n", "f"):
            block.append(rr)
            rr -= 1
        if len(block) < 2:
            continue
        if any(_TOTAL_LABEL_RX.match(label_for(b, c) or "") for b in block):
            continue  # subtotals inside the block: the plain sum is not the test
        if any(isinstance(cells[(b, c)], str) and _TOTAL_FORMULA_RX.match(cells[(b, c)]) for b in block):
            continue
        try:
            total = sum(float(value_of(b, c) if kinds[(b, c)] == "f" else cells[(b, c)]) for b in block)
        except (TypeError, ValueError):
            continue
        typed_total = float(cells[(r, c)])
        if abs(total - typed_total) > 0.005:
            note(f"{_ref(c, r)} ({label}) is typed as {_num(typed_total)}, but the "
                 f"{len(block)} numbers above it add up to {_num(tidy(total))}")

    for col in columns:
        if col["kind"] != "number":
            continue
        c = _col_num(col["column"])
        texts = [r for r, k in by_col[c] if k == "s" and _NUMBER_TEXT_RX.match(str(cells[(r, c)]))
                 and not _TOTAL_LABEL_RX.match(str(cells[(r, c)]))]
        if texts:
            note(f"{len(texts)} number(s) in column {col['column']} are stored as text "
                 f"(first: {_ref(c, texts[0])}), so sums skip them")

    out["checks"] = checks
    if failed:
        out["not_worked_out"] = dict(list(failed.items())[:5])

    # -- layout and a labelled guess at what it is --------------------------
    merged = len(getattr(getattr(ws, "merged_cells", None), "ranges", []) or [])
    if merged:
        out["merged_ranges"] = merged
    if getattr(ws, "freeze_panes", None):
        out["frozen_at"] = str(ws.freeze_panes)
    try:
        if ws.auto_filter and ws.auto_filter.ref:
            out["filter"] = str(ws.auto_filter.ref)
        if getattr(ws, "tables", None):
            out["tables"] = [f"{name} ({tbl.ref})" for name, tbl in list(ws.tables.items())[:6]]
        charts = len(getattr(ws, "_charts", []) or [])
        if charts:
            out["charts"] = charts
    except Exception:  # noqa: BLE001
        pass
    data_rows = max(0, rows - (header_row or 0))
    label_col = next((c for c in columns if c["column"] == "A"), None)
    if header_row and data_rows >= 3 and len(columns) >= 3:
        out["layout"] = f"a table: header on row {header_row}, {max_row - header_row} row(s) under it"
    elif label_col and label_col["kind"] == "text" and len(columns) <= 6:
        out["layout"] = "a statement: labels down column A, amounts beside them"
    elif kinds:
        out["layout"] = "free-form"
    else:
        out["layout"] = "empty"
    words = " ".join([title] + [str(v) for (r, c), v in cells.items()
                               if kinds[(r, c)] == "s" and (r <= (header_row or 1) + 1 or c == 1)][:400])
    for rx, name in _PURPOSES:
        if rx.search(words):
            out["looks_like"] = name
            break
    return out


def workbook_map(path: "str | Path") -> dict[str, Any]:
    """The map of one workbook. Raises only what opening the file raises."""
    p = Path(path)
    if too_large(p):
        raise ValueError("the workbook is too large to map in full")
    grid = Grid(p)
    try:
        fwb = grid._formulas()
        deadline = time.monotonic() + DEADLINE_S
        sheets = [_sheet_map(ws, grid, deadline) for ws in fwb.worksheets]
        out: dict[str, Any] = {"sheets": sheets}
        names: list[str] = []
        try:
            defined = fwb.defined_names
            for key in list(defined.keys() if hasattr(defined, "keys") else [])[:20]:
                dn = defined[key]
                names.append(f"{key} = {str(getattr(dn, 'attr_text', '') or '')[:60]}")
        except Exception:  # noqa: BLE001
            pass
        if names:
            out["names"] = names
        hidden = [s["sheet"] for s in sheets if s.get("state") != "visible"]
        if hidden:
            out["hidden_sheets"] = hidden
        try:
            links = len(getattr(fwb, "_external_links", []) or [])
            if links:
                out["external_links"] = links
        except Exception:  # noqa: BLE001
            pass
        out["formulas"] = sum(s.get("formula_count", 0) for s in sheets)
        out["formulas_unsaved"] = sum(s.get("formulas_unsaved", 0) for s in sheets)
        if time.monotonic() > deadline:
            out["cut_short"] = "the workbook is large: some totals and checks were not worked out"
        return out
    finally:
        grid.close()


def render(name: str, data: dict[str, Any]) -> str:
    """The map as compact text for a model: one headline per sheet (the line
    the old profile gave), then what is under it."""
    sheets = data.get("sheets") or []
    lines = [f"{name} — {len(sheets)} sheet(s)"]
    for s in sheets:
        heads = ", ".join(h for h in s.get("headers", []) if h) or "(no header row)"
        hidden = "" if s.get("state", "visible") == "visible" else " [HIDDEN]"
        lines.append(f"- {s['sheet']}{hidden}: {s['rows']}x{s['cols']} | headers: {heads}")
        about = [x for x in (
            f"looks like {s['looks_like']}" if s.get("looks_like") else "",
            s.get("layout", ""),
            f"only {s['studied']} were studied" if s.get("studied") else "",
        ) if x]
        if about:
            lines.append("    " + "; ".join(about))
        cols = s.get("columns") or []
        if cols:
            shown = [
                f"{c['column']} {c['header'] or '(no header)'}: {c['kind']}"
                + (f" [{c['format']}]" if c.get("format") else "")
                for c in cols[:14]
            ]
            more = f" (+{len(cols) - 14} more)" if len(cols) > 14 else ""
            lines.append("    columns: " + " | ".join(shown) + more)
        if s.get("formula_count"):
            parts = [
                f"{g['range']} {g['formula']}" + (f" (filled down {g['cells']} cells)" if g["cells"] > 1 else "")
                for g in s.get("formula_groups", [])[:8]
            ]
            more = s.get("formula_groups_more", 0) + max(0, len(s.get("formula_groups", [])) - 8)
            lines.append(f"    formulas ({s['formula_count']}): " + "; ".join(parts)
                         + (f"; and {more} more group(s)" if more else ""))
        for t in s.get("totals") or []:
            label = f" ({t['label']})" if t.get("label") else ""
            value = "not worked out" if t.get("value") is None else _num(t["value"])
            lines.append(f"    total {t['cell']}{label}: {t['formula']} = {value}")
        if s.get("uses_sheets"):
            lines.append("    reads from: " + ", ".join(s["uses_sheets"]))
        extras = [x for x in (
            f"{s['merged_ranges']} merged range(s)" if s.get("merged_ranges") else "",
            f"panes frozen at {s['frozen_at']}" if s.get("frozen_at") else "",
            f"filter on {s['filter']}" if s.get("filter") else "",
            ("table(s): " + ", ".join(s["tables"])) if s.get("tables") else "",
            f"{s['charts']} chart(s)" if s.get("charts") else "",
        ) if x]
        if extras:
            lines.append("    layout: " + "; ".join(extras))
        for c in s.get("checks") or []:
            lines.append(f"    WORTH A LOOK: {c}")
        if s.get("not_worked_out"):
            first = "; ".join(f"{k} ({v})" for k, v in list(s["not_worked_out"].items())[:3])
            lines.append(f"    not worked out here: {first}")
    tail = []
    if data.get("names"):
        tail.append("named ranges: " + "; ".join(data["names"][:8]))
    if data.get("hidden_sheets"):
        tail.append("hidden sheet(s): " + ", ".join(data["hidden_sheets"]))
    if data.get("external_links"):
        tail.append(f"{data['external_links']} link(s) to other workbooks (their values are not read)")
    if data.get("formulas_unsaved"):
        tail.append(f"{data['formulas_unsaved']} formula(s) have no saved result; Iron Jarvis works them out when it reads them")
    if data.get("cut_short"):
        tail.append(data["cut_short"])
    if tail:
        lines.append("workbook: " + " | ".join(tail))
    return "\n".join(lines)
