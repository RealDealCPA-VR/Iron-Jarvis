"""Compare two documents exactly (C-07).

"What changed between last year's return and this year's" used to be answered
by a model that read both files and described the differences from memory —
the one way to miss a changed figure. This module computes the difference:

* ``text``    — a paragraph-level diff of the two documents' extracted text,
  plus a figures table for the numbers inside paragraphs that changed;
* ``cells``   — spreadsheets cell by cell, per sheet (or matched row by row on a
  label column, so an inserted row does not make every later row "changed");
* ``figures`` — every labelled number in both documents, side by side, with the
  ones that moved.

Read-only by construction: it opens files and returns data. Every list is capped
and a cap that bites is REPORTED (``truncated``), never silent.
"""

from __future__ import annotations

import csv
import difflib
import io
import re
from pathlib import Path
from typing import Any

#: Changes returned per comparison before the list is cut (and says so).
MAX_CHANGES = 400
#: Rows × columns examined per sheet in cell mode.
MAX_ROWS = 5000
MAX_COLS = 200

SHEET_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xls", ".csv"})
MODES = ("auto", "text", "cells", "figures")

_NUM_RX = re.compile(r"\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?")


class CompareError(ValueError):
    """A comparison that could not be made (unreadable file, bad column)."""


# ------------------------------------------------------------------ numbers ---


def _to_number(token: str) -> float | None:
    t = token.strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace("$", "").replace(",", "").rstrip("%")
    if t.startswith("-"):
        neg, t = True, t[1:]
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _fmt_delta(before: str, after: str) -> str:
    a, b = _to_number(before), _to_number(after)
    if a is None or b is None:
        return ""
    d = b - a
    if d == 0:
        return "0"
    txt = f"{d:+,.2f}".rstrip("0").rstrip(".")
    return txt


def _numbers(line: str) -> list[str]:
    return [m.group(0) for m in _NUM_RX.finditer(line) if any(c.isdigit() for c in m.group(0))]


def _label(line: str) -> str:
    """The words in front of a line's first number — "Rental income: $12,000"
    is labelled "Rental income". Lowercased for matching; empty when the line
    starts with its number."""
    m = _NUM_RX.search(line)
    head = line[: m.start()] if m else line
    head = re.sub(r"[\s:=\-–—.]+$", "", head).strip()
    return head[:80]


# --------------------------------------------------------------------- text ---


def _paragraphs(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _figures_between(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Numbers that moved between paired paragraphs, position by position."""
    out: list[dict[str, str]] = []
    for before, after in pairs:
        nb, na = _numbers(before), _numbers(after)
        if not nb and not na:
            continue
        where = _label(before) or _label(after) or before[:60]
        for i in range(max(len(nb), len(na))):
            b = nb[i] if i < len(nb) else ""
            a = na[i] if i < len(na) else ""
            if b != a:
                out.append({"where": where, "before": b, "after": a, "change": _fmt_delta(b, a)})
    return out


def compare_text(text_a: str, text_b: str) -> dict[str, Any]:
    pa, pb = _paragraphs(text_a), _paragraphs(text_b)
    changes: list[dict[str, Any]] = []
    pairs: list[tuple[str, str]] = []
    counts = {"changed": 0, "removed": 0, "added": 0}
    sm = difflib.SequenceMatcher(None, pa, pb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            before, after = pa[i1:i2], pb[j1:j2]
            counts["changed"] += max(len(before), len(after))
            changes.append({"kind": "changed", "before": before, "after": after})
            pairs.extend(zip(before, after))
        elif tag == "delete":
            counts["removed"] += i2 - i1
            changes.append({"kind": "removed", "before": pa[i1:i2], "after": []})
        else:
            counts["added"] += j2 - j1
            changes.append({"kind": "added", "before": [], "after": pb[j1:j2]})
    truncated = len(changes) > MAX_CHANGES
    return {
        "mode": "text",
        "identical": not changes,
        "counts": counts,
        "changes": changes[:MAX_CHANGES],
        "figures": _figures_between(pairs)[:MAX_CHANGES],
        "truncated": truncated,
    }


def compare_figures(text_a: str, text_b: str) -> dict[str, Any]:
    """Every labelled number in both documents, matched by its label."""

    def table(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in _paragraphs(text):
            nums = _numbers(line)
            label = _label(line)
            if nums and label:
                out.setdefault(label.casefold(), f"{label}\x00{nums[0]}")
        return out

    ta, tb = table(text_a), table(text_b)
    rows: list[dict[str, str]] = []
    for key in list(ta) + [k for k in tb if k not in ta]:
        la, va = (ta[key].split("\x00") if key in ta else ("", ""))
        lb, vb = (tb[key].split("\x00") if key in tb else ("", ""))
        if va == vb:
            continue
        rows.append({
            "where": la or lb,
            "before": va or "(not in the first document)",
            "after": vb or "(not in the second document)",
            "change": _fmt_delta(va, vb) if va and vb else "",
        })
    return {
        "mode": "figures",
        "identical": not rows,
        "counts": {"changed": len(rows)},
        "changes": [],
        "figures": rows[:MAX_CHANGES],
        "truncated": len(rows) > MAX_CHANGES,
    }


# -------------------------------------------------------------------- cells ---


def _load_sheets(path: Path) -> dict[str, list[list[Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        from .readers import _decode_bytes

        text = _decode_bytes(path.read_bytes())
        return {path.stem: [list(r) for r in csv.reader(io.StringIO(text, newline=""))]}
    if suffix == ".xls":
        from .legacy import read_xls_sheets

        return read_xls_sheets(path)
    from openpyxl import load_workbook

    from .readers import _guard_office_encrypted

    _guard_office_encrypted(path)
    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        out: dict[str, list[list[Any]]] = {}
        for ws in wb.worksheets:
            rows: list[list[Any]] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS:
                    break
                rows.append(list(row[:MAX_COLS]))
            out[ws.title] = rows
        return out
    finally:
        wb.close()


def _norm(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    n = _to_number(s) if s and _NUM_RX.fullmatch(s) else None
    return n if n is not None else s


def _show(v: Any) -> str:
    if v is None or v == "":
        return "(empty)"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _label_index(header: list[Any], label_column: str) -> int:
    lc = str(label_column).strip()
    if lc.isdigit():
        return int(lc) - 1
    if re.fullmatch(r"[A-Za-z]{1,3}", lc):
        n = 0
        for ch in lc.upper():
            n = n * 26 + (ord(ch) - 64)
        # A letter that is ALSO a header name ("ID") — prefer the header.
        names = [str(h).strip().casefold() for h in header]
        if lc.casefold() in names:
            return names.index(lc.casefold())
        return n - 1
    names = [str(h).strip().casefold() for h in header]
    if lc.casefold() in names:
        return names.index(lc.casefold())
    raise CompareError(
        f"no column named \"{label_column}\" — the header row has: "
        + ", ".join(str(h) for h in header if str(h).strip())[:300]
    )


def _diff_positional(rows_a: list[list[Any]], rows_b: list[list[Any]], sheet: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in range(max(len(rows_a), len(rows_b))):
        ra = rows_a[r] if r < len(rows_a) else []
        rb = rows_b[r] if r < len(rows_b) else []
        for c in range(max(len(ra), len(rb))):
            va = ra[c] if c < len(ra) else None
            vb = rb[c] if c < len(rb) else None
            if _norm(va) != _norm(vb):
                out.append({
                    "kind": "cell", "sheet": sheet, "cell": f"{_col_letter(c)}{r + 1}",
                    "before": _show(va), "after": _show(vb),
                    "change": _fmt_delta(_show(va), _show(vb)) if _norm(va) != "" and _norm(vb) != "" else "",
                })
                if len(out) > MAX_CHANGES:
                    return out
    return out


def _diff_by_label(rows_a: list[list[Any]], rows_b: list[list[Any]], sheet: str, label_column: str) -> list[dict[str, Any]]:
    if not rows_a or not rows_b:
        return _diff_positional(rows_a, rows_b, sheet)
    header_a, header_b = rows_a[0], rows_b[0]
    ia = _label_index(header_a, label_column)
    ib = _label_index(header_b, label_column)

    def keyed(rows: list[list[Any]], idx: int) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {}
        for row in rows[1:]:
            key = str(_show(row[idx] if idx < len(row) else "")).strip()
            if key and key != "(empty)":
                out.setdefault(key, row)
        return out

    ka, kb = keyed(rows_a, ia), keyed(rows_b, ib)
    names_b = {str(h).strip().casefold(): j for j, h in enumerate(header_b)}
    out: list[dict[str, Any]] = []
    for key, row_a in ka.items():
        if key not in kb:
            out.append({"kind": "row_removed", "sheet": sheet, "row": key})
            continue
        row_b = kb[key]
        for j, h in enumerate(header_a):
            if j == ia:
                continue
            name = str(h).strip()
            jb = names_b.get(name.casefold(), j)
            va = row_a[j] if j < len(row_a) else None
            vb = row_b[jb] if jb < len(row_b) else None
            if _norm(va) != _norm(vb):
                out.append({
                    "kind": "cell", "sheet": sheet, "row": key, "column": name or _col_letter(j),
                    "before": _show(va), "after": _show(vb),
                    "change": _fmt_delta(_show(va), _show(vb)) if _norm(va) != "" and _norm(vb) != "" else "",
                })
        if len(out) > MAX_CHANGES:
            return out
    for key in kb:
        if key not in ka:
            out.append({"kind": "row_added", "sheet": sheet, "row": key})
    return out


def compare_sheets(path_a: Path, path_b: Path, label_column: str | None = None) -> dict[str, Any]:
    sa, sb = _load_sheets(path_a), _load_sheets(path_b)
    changes: list[dict[str, Any]] = []
    only_a = [s for s in sa if s not in sb]
    only_b = [s for s in sb if s not in sa]
    common = [s for s in sa if s in sb]
    # Two single-sheet files with different sheet names (a CSV against a
    # workbook, "2024" against "2025") are still one sheet against one sheet.
    if not common and len(sa) == 1 and len(sb) == 1:
        (na, ra), (nb, rb) = next(iter(sa.items())), next(iter(sb.items()))
        pairs = [(f"{na} / {nb}", ra, rb)]
        only_a, only_b = [], []
    else:
        pairs = [(s, sa[s], sb[s]) for s in common]
    for name, rows_a, rows_b in pairs:
        if label_column:
            changes.extend(_diff_by_label(rows_a, rows_b, name, label_column))
        else:
            changes.extend(_diff_positional(rows_a, rows_b, name))
        if len(changes) > MAX_CHANGES:
            break
    truncated = len(changes) > MAX_CHANGES
    return {
        "mode": "cells",
        "identical": not changes and not only_a and not only_b,
        "counts": {
            "cells": sum(1 for c in changes if c["kind"] == "cell"),
            "rows_added": sum(1 for c in changes if c["kind"] == "row_added"),
            "rows_removed": sum(1 for c in changes if c["kind"] == "row_removed"),
        },
        "sheets_only_in_first": only_a,
        "sheets_only_in_second": only_b,
        "changes": changes[:MAX_CHANGES],
        "figures": [],
        "truncated": truncated,
    }


# -------------------------------------------------------------------- entry ---


def compare_documents(
    path_a: Path, path_b: Path, mode: str = "auto", label_column: str | None = None
) -> dict[str, Any]:
    """Compare two files; see the module docstring for the modes."""
    from .readers import extract_text

    mode = (mode or "auto").strip().lower()
    if mode not in MODES:
        raise CompareError(f"unknown mode \"{mode}\" — use one of {', '.join(MODES)}")
    both_sheets = path_a.suffix.lower() in SHEET_SUFFIXES and path_b.suffix.lower() in SHEET_SUFFIXES
    if mode == "auto":
        mode = "cells" if both_sheets else "text"
    if mode == "cells":
        if not both_sheets:
            raise CompareError("cell-by-cell comparison needs two spreadsheets (.xlsx, .xls or .csv)")
        result = compare_sheets(path_a, path_b, label_column)
    else:
        text_a = extract_text(path_a)
        text_b = extract_text(path_b)
        result = compare_figures(text_a, text_b) if mode == "figures" else compare_text(text_a, text_b)
    result["first"] = str(path_a)
    result["second"] = str(path_b)
    return result


def render_report(result: dict[str, Any], *, max_lines: int = 250) -> str:
    """The plain-text answer the model reads (and can relay)."""
    a, b = Path(result.get("first", "")).name, Path(result.get("second", "")).name
    lines: list[str] = []
    if result.get("identical"):
        return f"{a} and {b} have no differences ({result['mode']} comparison)."
    counts = result.get("counts", {})
    if result["mode"] == "text":
        lines.append(
            f"{a} → {b}: {counts.get('changed', 0)} paragraph(s) changed, "
            f"{counts.get('removed', 0)} removed, {counts.get('added', 0)} added."
        )
        for ch in result["changes"]:
            for t in ch["before"]:
                lines.append(f"- {t}")
            for t in ch["after"]:
                lines.append(f"+ {t}")
            lines.append("")
    elif result["mode"] == "cells":
        lines.append(
            f"{a} → {b}: {counts.get('cells', 0)} cell(s) changed, "
            f"{counts.get('rows_added', 0)} row(s) added, {counts.get('rows_removed', 0)} removed."
        )
        if result.get("sheets_only_in_first"):
            lines.append("Sheets only in the first: " + ", ".join(result["sheets_only_in_first"]))
        if result.get("sheets_only_in_second"):
            lines.append("Sheets only in the second: " + ", ".join(result["sheets_only_in_second"]))
        for ch in result["changes"]:
            if ch["kind"] == "cell":
                where = ch.get("cell") or f"{ch.get('row')} / {ch.get('column')}"
                delta = f" ({ch['change']})" if ch.get("change") else ""
                lines.append(f"{ch['sheet']}!{where}: {ch['before']} → {ch['after']}{delta}")
            elif ch["kind"] == "row_added":
                lines.append(f"{ch['sheet']}: row \"{ch['row']}\" only in the second")
            else:
                lines.append(f"{ch['sheet']}: row \"{ch['row']}\" only in the first")
    if result.get("figures"):
        lines.append("")
        lines.append("Figures that changed:")
        for f in result["figures"]:
            delta = f" ({f['change']})" if f.get("change") else ""
            lines.append(f"{f['where']}: {f['before']} → {f['after']}{delta}")
    if result.get("truncated"):
        lines.append(f"[only the first {MAX_CHANGES} differences are listed — there are more]")
    if len(lines) > max_lines:
        extra = len(lines) - max_lines
        lines = lines[:max_lines] + [f"[... {extra} more line(s) not shown]"]
    return "\n".join(lines).strip()
