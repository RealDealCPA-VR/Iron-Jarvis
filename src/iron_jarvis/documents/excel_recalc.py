"""Recalculation: formula results Excel never saved, worked out and KEPT (v1.465.0).

A workbook a program wrote (this app included) holds formulas with no results:
only Excel computes them, when a person opens and saves the file. Until then
every other reader — a preview, another program, this app's own tools — sees a
blank where the total should be.

Two jobs, both built on :mod:`excel_formula`:

* :func:`compute_missing` works out every formula cell that has no saved
  result, for the tools that READ a workbook (a sum over a column of formulas
  is then the real sum, not the sum of what happened to be saved).
* :func:`store_results` does the same after this app WRITES a workbook and puts
  each result into the file beside its formula, so the file reads correctly
  everywhere. It only touches a file whose workbook part says "recalculate
  everything when opened" (what the spreadsheet library writes), so Excel
  still computes every formula itself and a stored number can never mask it.

Both are BLOCKING (call them off the event loop), bounded by a file size, a
formula count and a deadline, and never raise: what could not be worked out is
counted and named, never guessed.
"""

from __future__ import annotations

import math
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .excel_formula import ExcelError, FormulaError, Grid, _col_letters, from_serial, to_serial

#: A workbook larger than this is not recalculated (it is loaded whole, twice).
MAX_FILE_BYTES = 40 * 1024 * 1024
#: At most this many formula cells are worked out in one pass.
MAX_FORMULAS = 100_000
#: Wall-clock bound for one pass.
DEADLINE_S = 25.0
#: A sheet part bigger than this (uncompressed) is not rewritten.
MAX_SHEET_XML_BYTES = 150 * 1024 * 1024


@dataclass
class Recalc:
    """What one pass did. ``values`` maps ``(sheet title, row, column)`` to the
    result as a reader should see it (a date cell as an ISO date)."""

    found: int = 0  # formula cells with no saved result
    values: dict[tuple[str, int, int], Any] = field(default_factory=dict)
    raw: dict[tuple[str, int, int], Any] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: int = 0  # not attempted (a bound was reached)
    filled: int = 0  # results put into a caller's rows (fill_rows)
    stored: int = 0  # results written into the file
    why_not: str = ""  # why nothing could be done at all

    @property
    def computed(self) -> int:
        return len(self.values)

    def problems(self, limit: int = 3) -> str:
        """The first few cells that could not be worked out, in words."""
        parts = [f"{ref} ({why})" for ref, why in list(self.failed.items())[:limit]]
        more = len(self.failed) - len(parts)
        if more > 0:
            parts.append(f"and {more} more")
        return "; ".join(parts)


def tidy(v: Any) -> Any:
    """A computed number as Excel shows it: 15 significant digits, and a whole
    number as a whole number (0.1+0.2 reads 0.3, never 0.30000000000000004)."""
    if isinstance(v, bool) or not isinstance(v, float):
        return v
    if math.isnan(v) or math.isinf(v):
        return v
    out = float(f"{v:.15g}")
    return int(out) if out == int(out) and abs(out) < 1e15 else out


def _is_date_format(fmt: Any) -> bool:
    try:
        from openpyxl.styles.numbers import is_date_format

        return bool(is_date_format(str(fmt or "")))
    except Exception:  # noqa: BLE001
        return False


def compute_missing(
    path: "str | Path",
    sheets: "list[str] | None" = None,
    *,
    max_formulas: int = MAX_FORMULAS,
    deadline_s: float = DEADLINE_S,
) -> Recalc:
    """Work out every formula cell of *path* that has no saved result."""
    out = Recalc()
    p = Path(path)
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            out.why_not = "the workbook is too large to recalculate here"
            return out
    except OSError as exc:
        out.why_not = f"the workbook could not be read ({type(exc).__name__})"
        return out
    grid: "Grid | None" = None
    try:
        grid = Grid(p)
        fwb = grid._formulas()
        wanted = {s.lower() for s in sheets} if sheets else None
        started = time.monotonic()
        for ws in fwb.worksheets:
            if wanted is not None and ws.title.lower() not in wanted:
                continue
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if v is None or cell.data_type != "f":
                        continue
                    r, c = cell.row, cell.column
                    try:
                        if grid.saved(ws.title, c, r) is not None:
                            continue
                    except Exception:  # noqa: BLE001
                        continue
                    out.found += 1
                    where = f"{ws.title}!{_col_letters(c)}{r}"
                    if not isinstance(v, str):
                        out.failed[where] = "an array formula, which only Excel works out"
                        continue
                    if out.computed + len(out.failed) >= max_formulas or (
                        time.monotonic() - started > deadline_s
                    ):
                        out.skipped += 1
                        continue
                    grid._reads = 0  # the read budget is per formula
                    try:
                        value = grid.get(ws.title, c, r)
                    except ExcelError as exc:
                        out.failed[where] = f"its result is the error {exc.code}"
                        continue
                    except FormulaError as exc:
                        out.failed[where] = str(exc).split(" — ")[0][:160]
                        continue
                    except Exception as exc:  # noqa: BLE001 - one odd cell never fails the pass
                        out.failed[where] = type(exc).__name__
                        continue
                    if value is None:
                        value = 0.0  # a formula that points at a blank shows 0
                    key = (ws.title, r, c)
                    out.raw[key] = value
                    shown = tidy(value)
                    if isinstance(value, float) and _is_date_format(cell.number_format):
                        try:
                            d = from_serial(value)
                            shown = d.date().isoformat() if value == int(value) else d.isoformat(sep=" ")
                        except FormulaError:
                            pass
                    out.values[key] = shown
    except Exception as exc:  # noqa: BLE001
        out.why_not = f"the workbook could not be recalculated ({type(exc).__name__})"
    finally:
        if grid is not None:
            grid.close()
    return out


def fill_rows(
    path: "str | Path",
    title: str,
    rows: list[list[Any]],
    *,
    first_row: int = 1,
    first_col: int = 1,
) -> Recalc:
    """Put the worked-out results of sheet *title* into *rows* (a matrix whose
    top-left cell is ``(first_row, first_col)``), wherever it holds a blank."""
    res = compute_missing(path, [title])
    for (sheet, r, c), value in res.values.items():
        ri, ci = r - first_row, c - first_col
        if 0 <= ri < len(rows) and ci >= 0:
            row = rows[ri]
            if ci < len(row) and row[ci] is None:
                row[ci] = value
                res.filled += 1
            elif ci >= len(row) and ci < 16_384:
                row.extend([None] * (ci - len(row)))
                row.append(value)
                res.filled += 1
    return res


# ------------------------------------------------------------- storing ------

_CELL_XML = re.compile(
    r'<c r="([A-Z]{1,3}[0-9]{1,7})"((?:\s+[A-Za-z:]+="[^"]*")*)>'
    r"(<f(?:\s[^>]*)?>[^<]*</f>|<f(?:\s[^>]*)?/>)"
    r"(?:<v\s*/>|<v></v>)</c>"
)
_BAD_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _value_xml(value: Any) -> "tuple[str, str] | None":
    """``(the cell's type attribute, its <v> element)`` for a result, or None
    when it cannot be stored safely."""
    if isinstance(value, bool):
        return ' t="b"', f"<v>{1 if value else 0}</v>"
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return "", f"<v>{int(f) if f == int(f) and abs(f) < 1e15 else repr(f)}</v>"
    if isinstance(value, str):
        if len(value) > 32_000 or _BAD_TEXT.search(value):
            return None
        keep = ' xml:space="preserve"' if value != value.strip() else ""
        return ' t="str"', f"<v{keep}>{_xml_text(value)}</v>"
    return None


def _sheet_parts(z: zipfile.ZipFile) -> dict[str, str]:
    """Sheet title → the archive member that holds it."""
    from xml.etree import ElementTree as ET

    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    pkg = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    targets = {}
    for node in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(f"{pkg}Relationship"):
        target = node.get("Target", "")
        targets[node.get("Id", "")] = target.lstrip("/") if target.startswith("/") else "xl/" + target
    out = {}
    for node in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{main}sheet"):
        part = targets.get(node.get(f"{rel}id", ""))
        if part:
            out[node.get("name", "")] = part
    return out


def store_results(path: "str | Path") -> Recalc:
    """Work out every unsaved formula of *path* and write each result into the
    file beside its formula. The file is replaced only after the new copy was
    read back and every stored value checked; on any doubt it is left as it was."""
    p = Path(path)
    res = compute_missing(p)
    if not res.raw:
        return res
    # The copy keeps the workbook's own extension: the library opens a file
    # by its name, and a ".tmp" is refused before it is read.
    tmp = p.with_name(f".recalc-{os.getpid()}-{p.name}")
    try:
        by_part: dict[str, dict[str, Any]] = {}
        with zipfile.ZipFile(p) as zin:
            workbook_xml = zin.read("xl/workbook.xml").decode("utf-8", "replace")
            if 'fullCalcOnLoad="1"' not in workbook_xml:
                # Excel would trust a stored number here; only its own are safe.
                return res
            parts = _sheet_parts(zin)
            for (title, r, c), value in res.raw.items():
                part = parts.get(title)
                if part:
                    by_part.setdefault(part, {})[f"{_col_letters(c)}{r}"] = value
            stored = 0
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    wanted = by_part.get(item.filename)
                    if wanted and item.file_size <= MAX_SHEET_XML_BYTES:
                        count = 0

                        def put(m: "re.Match[str]") -> str:
                            nonlocal count
                            ref, attrs, formula = m.group(1), m.group(2), m.group(3)
                            if ref not in wanted or re.search(r'\st="', attrs):
                                return m.group(0)
                            made = _value_xml(wanted[ref])
                            if made is None:
                                return m.group(0)
                            count += 1
                            return f'<c r="{ref}"{attrs}{made[0]}>{formula}{made[1]}</c>'

                        text = _CELL_XML.sub(put, data.decode("utf-8"))
                        stored += count
                        data = text.encode("utf-8")
                    zout.writestr(item, data)
        if stored and _stored_reads_back(tmp, res):
            os.replace(tmp, p)
            res.stored = stored
    except Exception:  # noqa: BLE001 - the workbook as written stays as it was
        res.stored = 0
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return res


def _stored_reads_back(tmp: Path, res: Recalc, sample: int = 5_000) -> bool:
    """True when the rewritten copy opens and holds the values put into it."""
    from openpyxl import load_workbook

    values = load_workbook(str(tmp), data_only=True)
    formulas = load_workbook(str(tmp), data_only=False)
    try:
        for i, ((title, r, c), want) in enumerate(res.raw.items()):
            if i >= sample:
                break
            if formulas[title].cell(row=r, column=c).data_type != "f":
                return False
            got = values[title].cell(row=r, column=c).value
            if got is None:
                continue  # a result that could not be stored stays blank
            if isinstance(want, bool) or isinstance(want, str):
                if got != want:
                    return False
                continue
            if not isinstance(got, (int, float)):
                # A cell formatted as a date reads back as a date.
                try:
                    got = to_serial(got)
                except FormulaError:
                    return False
            if not math.isclose(
                float(got), float(want), rel_tol=1e-12, abs_tol=1e-12
            ):
                return False
        return True
    finally:
        values.close()
        formulas.close()


def summary_note(res: Recalc, *, stored: bool) -> str:
    """One or two sentences about a pass, for a tool's answer ("" = nothing to say)."""
    lines: list[str] = []
    if stored and res.stored:
        lines.append(
            f"Worked out {res.stored} formula result(s) and saved them in the file, so "
            "they read correctly in any program. Excel works every formula out again "
            "itself when it opens the workbook."
        )
    elif not stored and res.computed:
        lines.append(
            f"Note: {res.computed} formula cell(s) had no saved result (the workbook was "
            "written by a program and not saved by Excel since); Iron Jarvis worked "
            "them out itself."
        )
    left = len(res.failed) + res.skipped + (res.computed - res.stored if stored else 0)
    if stored and res.computed and not res.stored:
        left = len(res.failed) + res.skipped
        lines.append(
            f"{res.computed} formula result(s) were worked out but could not be saved "
            "in the file; they show once the workbook is opened in Excel."
        )
    if res.failed or res.skipped:
        n = len(res.failed) + res.skipped
        detail = f": {res.problems()}" if res.failed else ""
        lines.append(
            f"WARNING: {n} formula cell(s) could not be worked out here{detail}. They "
            "read as blank until the workbook is opened and saved in Excel, so do not "
            "report a figure that depends on them."
        )
    elif res.why_not and res.found == 0:
        pass
    return "\n".join(lines)
