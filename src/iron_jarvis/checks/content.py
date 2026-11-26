"""What a made file SAYS, checked by rule (no model call, ever).

The app already checked that a required file exists, is not empty and opens
(``deliverables/check.check_file``, reused here). Nothing looked inside it: a
sheet full of ``#DIV/0!`` or a grand total that is not the sum of its rows
went out under "Done". These are the checks of the content, each a fact about
the file:

* **it opens** (``file_opens``): on disk, not empty, and its kind loads;
* **a spreadsheet has no error cells** (``sheet_no_error_cells``): no cell
  holds one of Excel's error values (``documents/lint._XL_ERRORS``), written
  as a value or saved as a formula's last result, and no formula with NO
  saved result works out to one (:func:`_work_out`: the app's own evaluator,
  ``documents/excel_formula``, over the cells already read). "no error
  cells" is said ONLY when every filled cell holds a value or a formula that
  was worked out without an error; a formula that could not be worked out
  makes the check ``not_checked`` with the count;
* **a spreadsheet's totals add up** (``sheet_totals``): see :func:`_totals`.
  A WRONG total is said only in one unambiguous shape (:func:`_clean`); a
  mismatch in any other shape is ``not_checked``;
* **a text or Word document is not empty of text** (``document_not_empty``;
  a Word file through ``documents/lint.lint_docx``);
* **a PDF's pages** (``pdf_pages``): how many it has, and, only when the
  reply states a count for that file in one of four plain forms and says
  nothing about changing pages (:func:`stated_pages`), whether it is that
  many.

RULES OF THE HOUSE. A check never raises: one that breaks is ``not_checked``
with a plain reason, never ``passed``. Only the files the caller hands over
are opened, and the callers hand over the files their own tools wrote (both
chat lanes' ``made_docs``, a run's undo journal), never a path read from a
model's words. Every path goes through the file policy the other readers use
(``core/fs_policy.fs_read_ok``), and a network spelling is refused by its
text first (``tools/base._network_spelling``).

BOUNDED. At most :data:`MAX_FILES` files a reply, a file over
:data:`MAX_BYTES` (or a package that unpacks past :data:`MAX_UNPACKED_BYTES`)
is not opened by any reader here, a workbook's first :data:`MAX_CELLS` filled
cells are read (by its real cells, never by the size its sheets declare; at
most :data:`MAX_VISITED` cells are stepped over), at most :data:`MAX_WORKED`
formulas are worked out (each reading at most :data:`MAX_FORMULA_READS`
cells), and the whole reply's checks share :data:`DEADLINE_S`, looked at
between files AND inside a file (every row of the cell walk, every formula,
every total): what is left when it runs out is ``not_checked``. BLOCKING:
:func:`check_made_files` is the hop off the event loop, and it waits
:data:`HARD_WAIT_S` at most.

A FILE'S NAME is all a check keeps of where it is. Two made files with the
same name in two folders are told apart by the folder's own name beside it
("summary.xlsx (in 2023)": ``record.apart``), never by a path.

WORDS. Everything a check says can reach the line under a reply: plain
words only, never an exception's name or a package part's path
(``record.open_words``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .record import (
    DOC_TEXT, FAILED, FILE_OPENS, NOT_CHECKED, PASSED, PDF_PAGES, SHEET_ERRORS, SHEET_TOTALS, Check, apart,
    file_name, open_words,
)

log = logging.getLogger(__name__)

#: At most this many files of one reply (or one run) are checked.
MAX_FILES = 6
#: A file past this size is not opened for its content.
MAX_BYTES = 8 * 1024 * 1024
#: An Office file whose package unpacks past this is not opened.
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
#: A workbook's filled cells read at most.
MAX_CELLS = 200_000
#: ... and the cells stepped over on the way (the blanks between two
#: far-apart cells of one row count here).
MAX_VISITED = 2_000_000
#: One reply's content checks share this long.
DEADLINE_S = 4.0
#: The caller waits this long at most for the worker thread.
HARD_WAIT_S = DEADLINE_S + 2.0
#: Cells one SUM may add, total labels one workbook may hold, cells named.
MAX_SUM_CELLS = 20_000
MAX_TOTALS = 200
MAX_NAMED = 3
_SUM_DEPTH = 4
#: Formulas with no saved result that one workbook has worked out at most,
#: and the cells one of them may read on the way.
MAX_WORKED = 20_000
MAX_FORMULA_READS = 200_000

OUT_OF_TIME = "the check ran out of time"
BROKE = "it could not be checked here"

_SHEET_EXTS = frozenset({".xlsx", ".xlsm"})
#: The kinds that are a package of parts (a zip): the unpacked cap is theirs.
_PACKAGE_EXTS = frozenset({".xlsx", ".xlsm", ".docx", ".pptx", ".odt", ".ods"})
_TEXT_EXTS = frozenset({".txt", ".md", ".markdown", ".rtf", ".html", ".htm", ".csv", ".tsv"})
_HTML_EXTS = frozenset({".html", ".htm"})


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _num(value: float) -> str:
    """A number as a person reads it: 1,250 or 1,250.40."""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def _size_words(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, size // 1024):,} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def readable(path: Any) -> bool:
    """May Iron Jarvis read this file? The file policy every other reader
    uses; a network spelling is refused by its text, before anything is
    resolved. False on any fault."""
    try:
        from ..core.fs_policy import fs_read_ok
        from ..tools.base import _network_spelling

        text = str(path or "")
        if not text.strip() or _network_spelling(text):
            return False
        ok, _why = fs_read_ok(text)
        return bool(ok)
    except Exception:  # noqa: BLE001 — in doubt, the file is not opened
        log.debug("a made file's read permission could not be decided", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# A spreadsheet
# --------------------------------------------------------------------------- #
class _OutOfTime(Exception):
    """The reply's deadline ran out inside a file (the cell walk, the
    formulas, the totals): what was not reached is ``not_checked``."""


def _never_late() -> bool:
    return False


#: A cell that labels a subtotal, and one that labels any total: the WHOLE
#: cell, lower-cased, a trailing colon or period dropped. "Total revenue",
#: "Total Wine & More" and "Totals by region" are not labels: a figure
#: beside them is not judged at all. English first; the plain word in the
#: six other languages the claim readings know.
_SUBTOTAL_WORDS = frozenset({"subtotal", "subtotals", "sub-total", "sub-totals", "sub total", "sub totals"})
_TOTAL_WORDS = _SUBTOTAL_WORDS | frozenset({
    "total", "totals", "grand total", "grand totals", "sum",
    "totale", "totaal", "summe", "gesamt", "gesamtsumme", "suma", "somme", "somma",
})
#: A column whose numbers are not added up: a rate, a price, an average, a
#: running balance anywhere in its heading; a number that names something
#: (an invoice number, an id, a year, a date) where the heading ENDS on the
#: word ("Year 1" is a column of amounts, "Tax year" is not).
_NOT_ADDED_RX = re.compile(
    r"%|\b(?:percent(?:age)?|pct|rates?|prices?|avg|average|mean|median|margins?|ratio|share|unit|per|hourly"
    r"|balance|running|cumulative)\b"
    r"|(?:\b(?:no|nos|number|num|id|ref|reference|code|year|yr|date)\b\.?|#)\s*$",
    re.IGNORECASE,
)
#: Text that is a number someone typed as text ("200", "$1,200", "(45.10)").
_TEXT_NUMBER_RX = re.compile(r"^\(?\s*[-+]?\s*[$€£¥]?\s*[-+]?\d[\d,. ]*\s*%?\s*\)?$")
_REF = (
    r"(?:(?:'(?P<q{n}>[^']+)'|(?P<s{n}>[A-Za-z0-9_.]+))!)?"
    r"\$?(?P<c1{n}>[A-Za-z]{{1,3}})\$?(?P<r1{n}>\d{{1,7}})"
    r"(?::\$?(?P<c2{n}>[A-Za-z]{{1,3}})\$?(?P<r2{n}>\d{{1,7}}))?"
)
_REF_RX = re.compile(_REF.format(n=""))
_SUM_RX = re.compile(r"^=\s*SUM\s*\((?P<args>[^()]*)\)\s*$", re.IGNORECASE)
_PLUS_RX = re.compile(r"^=\s*(?P<args>[^()=*/^&<>-]+)$")
#: How far up a total looks for its column's heading.
_SCAN_ROWS = 5_000

NOT_ONE_LIST = "the rows above it could not be read as one list"
UNDER_TOTALS = "sits under other totals, and this check cannot tell which cells it adds"


def _col_number(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def _col_letters(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_formula(v: Any) -> bool:
    return isinstance(v, str) and v.lstrip().startswith("=")


def _label_words(v: Any) -> str:
    """A cell's whole text as a label is compared: lower-cased, spaces
    folded, a trailing colon or period dropped. "" for anything else."""
    if not isinstance(v, str) or _is_formula(v):
        return ""
    return " ".join(v.split()).lower().rstrip(":. ")


def _is_label(v: Any) -> bool:
    """Is this cell, WHOLE, a total word?"""
    return _label_words(v) in _TOTAL_WORDS


def _is_text_number(v: Any) -> bool:
    return isinstance(v, str) and not _is_formula(v) and _TEXT_NUMBER_RX.match(v.strip()) is not None


def _is_heading(v: Any) -> bool:
    """Words that can head a column: text that is not a formula, not a
    number typed as text and not an error value."""
    return (isinstance(v, str) and bool(v.strip()) and not _is_formula(v) and not _is_text_number(v)
            and v.strip() not in _errors())


def parse_sum(formula: str) -> "list[tuple[str, int, int, int, int]] | None":
    """The cells a simple total formula adds: ``=SUM(B2:B5)``,
    ``=SUM(B2:B5,D2:D5)``, ``=B2+B3+B4``, each as ``(sheet or "", first row,
    first column, last row, last column)``. None for anything else (another
    function, a whole column, arithmetic other than adding)."""
    text = str(formula or "").strip()
    m = _SUM_RX.match(text)
    sep = r"[,;]"
    if m is None:
        m = _PLUS_RX.match(text)
        sep = r"\+"
    if m is None:
        return None
    out: list[tuple[str, int, int, int, int]] = []
    for part in re.split(sep, m.group("args")):
        ref = _REF_RX.fullmatch(part.strip())
        if ref is None:
            return None
        if sep == r"\+" and ref.group("c2") is not None:
            return None  # a range is not a number to add with "+"
        r1, c1 = int(ref.group("r1")), _col_number(ref.group("c1"))
        r2 = int(ref.group("r2")) if ref.group("r2") else r1
        c2 = _col_number(ref.group("c2")) if ref.group("c2") else c1
        out.append((ref.group("q") or ref.group("s") or "", min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)))
    return out or None


def _one_sum_range(formula: Any) -> "tuple[str, int, int, int, int] | None":
    """The ONE range of a plain ``=SUM(B2:B5)``; None for any other formula
    (two ranges, ``=B2+B3``, another function)."""
    text = str(formula or "").strip()
    if _SUM_RX.match(text) is None:
        return None
    refs = parse_sum(text)
    return refs[0] if refs is not None and len(refs) == 1 else None


class _Book:
    """A workbook's filled cells as written (``formulas``), as last saved by
    a spreadsheet program (``values``: None where nothing was saved) and, for
    a formula with no saved result, as the app's own evaluator worked it out
    (``worked``)."""

    def __init__(self) -> None:
        self.formulas: dict[str, dict[tuple[int, int], Any]] = {}
        self.values: dict[str, dict[tuple[int, int], Any]] = {}
        self.worked: dict[str, dict[tuple[int, int], Any]] = {}
        self.percent: set[tuple[str, int, int]] = set()
        self.cut = False

    def number(self, sheet: str, row: int, col: int, depth: int = 0) -> "float | None":
        """A cell as a number to add: a number is itself, a blank or a word
        is 0 (as SUM reads them), a formula is its saved result, else what
        the evaluator worked out, else what a simple sum of numbers comes to.
        None = it cannot be worked out here."""
        v = self.formulas.get(sheet, {}).get((row, col))
        if v is None or isinstance(v, bool):
            return 0.0
        if _is_number(v):
            return float(v)
        if _is_formula(v):
            saved = self.values.get(sheet, {}).get((row, col))
            if _is_number(saved):
                return float(saved)
            worked = self.worked.get(sheet, {}).get((row, col))
            if _is_number(worked):
                return float(worked)
            return self.sum_of(sheet, v, (row, col), depth + 1)
        if isinstance(v, str):
            return None if v.strip() in _errors() else 0.0
        return None

    def sum_of(self, sheet: str, formula: str, at: tuple[int, int], depth: int = 0) -> "float | None":
        """What a simple total formula adds up to, or None."""
        refs = parse_sum(formula)
        if refs is None or depth > _SUM_DEPTH:
            return None
        total, cells = 0.0, 0
        for ref_sheet, r1, c1, r2, c2 in refs:
            name = ref_sheet or sheet
            if name not in self.formulas:
                return None
            cells += (r2 - r1 + 1) * (c2 - c1 + 1)
            if cells > MAX_SUM_CELLS:
                return None
            if name == sheet and r1 <= at[0] <= r2 and c1 <= at[1] <= c2:
                return None  # it adds itself
            for (row, col) in [k for k in self.formulas[name] if r1 <= k[0] <= r2 and c1 <= k[1] <= c2]:
                got = self.number(name, row, col, depth)
                if got is None:
                    return None
                total += got
        return total


def _errors() -> "frozenset[str]":
    from ..documents.lint import _XL_ERRORS

    return _XL_ERRORS


def _load(path: Path, late: Callable[[], bool] = _never_late) -> _Book:
    """Read a workbook twice, read-only and cell-capped: as written, and as
    last saved (formula results). The deadline (*late*) is looked at on every
    row of the walk: a long sheet stops there (:class:`_OutOfTime`), never at
    the end of the file. BLOCKING."""
    from openpyxl import load_workbook

    book = _Book()
    for data_only, target in ((False, book.formulas), (True, book.values)):
        wb = load_workbook(str(path), read_only=True, data_only=data_only)
        try:
            seen = visited = 0
            for ws in wb.worksheets:
                cells = target.setdefault(ws.title, {})
                # A sheet may DECLARE any size ("A1:XFD1048576"); read as
                # declared, every row is padded out to it. Its real cells
                # are read instead, and the padding between two far-apart
                # cells of one row is counted against its own cap.
                try:
                    ws.reset_dimensions()
                except Exception:  # noqa: BLE001 — an older reader: the caps below still hold
                    pass
                for row in ws.iter_rows():
                    if late():
                        raise _OutOfTime()
                    visited += len(row)
                    if visited > MAX_VISITED:
                        book.cut = True
                        break
                    for cell in row:
                        v = cell.value
                        if v is None or (isinstance(v, str) and not v.strip()):
                            continue
                        r, c = getattr(cell, "row", None), getattr(cell, "column", None)
                        if not isinstance(r, int) or not isinstance(c, int):
                            continue
                        cells[(r, c)] = v
                        seen += 1
                        if not data_only and _is_number(v):
                            try:
                                if "%" in str(cell.number_format or ""):
                                    book.percent.add((ws.title, r, c))
                            except Exception:  # noqa: BLE001 — a format is a hint only
                                pass
                    if seen >= MAX_CELLS:
                        book.cut = True
                        break
                if book.cut:
                    break
        finally:
            wb.close()
    return book


def _place(sheet: str, row: int, col: int) -> str:
    return f"{sheet}!{_col_letters(col)}{row}"


def _grid(book: _Book, late: Callable[[], bool]) -> Any:
    """The app's own formula evaluator (``documents/excel_formula.Grid``)
    over the cells ALREADY read: no second load of the file. A formula may
    read at most :data:`MAX_FORMULA_READS` cells, and gives up when the
    reply's deadline runs out."""
    from ..documents.excel_formula import FormulaError, Grid

    class _BookGrid(Grid):
        def __init__(self) -> None:
            super().__init__(None)
            self.sheetnames = list(book.formulas)
            self.active = self.sheetnames[0] if self.sheetnames else ""
            self._lower = {name.lower(): name for name in self.sheetnames}
            self._extent: dict[str, tuple[int, int]] = {}

        def canon(self, sheet: str) -> str:
            name = self._lower.get(str(sheet).lower())
            if name is None:
                raise FormulaError(f"no sheet named {sheet!r}")
            return name

        def saved(self, sheet: str, col: int, row: int) -> Any:
            return book.values.get(self.canon(sheet), {}).get((row, col))

        def formula(self, sheet: str, col: int, row: int) -> "str | None":
            v = book.formulas.get(self.canon(sheet), {}).get((row, col))
            return v if isinstance(v, str) and v.startswith("=") else None

        def _size(self, sheet: str) -> tuple[int, int]:
            name = self.canon(sheet)
            if name not in self._extent:
                keys = book.formulas.get(name, {})
                self._extent[name] = (max((r for r, _c in keys), default=1), max((c for _r, c in keys), default=1))
            return self._extent[name]

        def max_row(self, sheet: str) -> int:
            return self._size(sheet)[0]

        def max_col(self, sheet: str) -> int:
            return self._size(sheet)[1]

        def defined(self, name: str) -> None:
            return None  # a defined name is not followed here: the formula is "not worked out"

        def _array_cells(self, sheet: str) -> dict:
            return {}

        def get(self, sheet: str, col: int, row: int) -> Any:
            if self._reads >= MAX_FORMULA_READS or (self._reads % 2_000 == 1_999 and late()):
                raise FormulaError("this formula reads too many cells to work out here")
            return super().get(sheet, col, row)

        def close(self) -> None:
            return None

    return _BookGrid()


def _work_out(book: _Book, late: Callable[[], bool] = _never_late) -> "tuple[list[tuple[str, int, int, str]], int]":
    """Every formula cell with NO saved result, worked out by the app's own
    evaluator (the one the writer warns with). Returns ``(the ones whose
    result is an error, as (sheet, row, col, error); how many could not be
    worked out)``. A result that was worked out is kept in ``book.worked``.
    Bounded: at most :data:`MAX_WORKED` formulas, while the deadline lasts;
    the rest count as not worked out. Never raises."""
    todo = [
        (sheet, row, col)
        for sheet, cells in book.formulas.items()
        for (row, col), v in sorted(cells.items())
        if (_is_formula(v) or not isinstance(v, (str, int, float, bool)) and hasattr(v, "ref"))
        and book.values.get(sheet, {}).get((row, col)) is None
    ]
    if not todo:
        return [], 0
    errors: list[tuple[str, int, int, str]] = []
    unworked = 0
    try:
        from ..documents.excel_formula import ExcelError

        grid = _grid(book, late)
    except Exception:  # noqa: BLE001 — no evaluator: nothing was worked out
        log.debug("the formula evaluator could not be set up", exc_info=True)
        return [], len(todo)
    for i, (sheet, row, col) in enumerate(todo):
        if i >= MAX_WORKED or late():
            unworked += len(todo) - i
            break
        v = book.formulas[sheet][(row, col)]
        if not isinstance(v, str):
            unworked += 1   # an array formula: only a spreadsheet program works it out
            continue
        grid._reads = 0     # the read budget is per formula
        try:
            got = grid.get(sheet, col, row)
        except ExcelError as exc:
            errors.append((sheet, row, col, str(exc.code)))
            continue
        except Exception:  # noqa: BLE001 — a formula this evaluator cannot do
            unworked += 1
            continue
        book.worked.setdefault(sheet, {})[(row, col)] = 0.0 if got is None else got
    return errors, unworked


def _error_cells(book: _Book, worked: "Iterable[tuple[str, int, int, str]]" = ()) -> list[str]:
    """Every cell that holds an Excel error ("Summary!C7 holds #DIV/0!": a
    value, or a formula's saved result), and every formula with no saved
    result that works out to one ("Summary!D3 works out to #DIV/0!")."""
    errors = _errors()
    order = {name: i for i, name in enumerate(book.formulas)}
    found: list[tuple[int, int, int, str]] = []
    for sheet, cells in book.formulas.items():
        saved = book.values.get(sheet, {})
        for (row, col) in cells:
            v = cells[(row, col)]
            shown = v.strip() if isinstance(v, str) and v.strip() in errors else None
            if shown is None and _is_formula(v):
                got = saved.get((row, col))
                shown = got.strip() if isinstance(got, str) and got.strip() in errors else None
            if shown is not None:
                found.append((order[sheet], row, col, f"{_place(sheet, row, col)} holds {shown}"))
    for sheet, row, col, code in worked:
        found.append((order.get(sheet, 0), row, col, f"{_place(sheet, row, col)} works out to {code}"))
    return [words for _s, _r, _c, words in sorted(found)]


def _same(total: float, added: float) -> bool:
    """Is a written total the sum of its cells? Equal to the cent, or equal
    once the sum is rounded the way the total was written (a total of 100
    over cells that add up to 100.4 is a rounded total, not a wrong one)."""
    if abs(total - added) <= 0.005:
        return True
    text = repr(float(total))
    decimals = min(6, len(text.partition(".")[2].rstrip("0"))) if "e" not in text else 6
    return abs(round(added, decimals) - total) <= 10 ** -(decimals + 3) + 1e-9


def _row_is_total(cells: dict[tuple[int, int], Any], row: int, before: int) -> bool:
    return any(_is_label(cells.get((row, c))) for c in range(1, before))


def _totals(book: _Book, late: Callable[[], bool] = _never_late) -> "tuple[int, list[str], list[str]]":
    """``(how many totals add up, the wrong ones, the ones that could not be
    worked out)``, each of the last two in plain words.

    A TOTAL is a number or a formula beside a cell that is, WHOLE, a total
    word (:data:`_TOTAL_WORDS`). A figure beside "Total revenue" or a payee
    called "Total Wine & More" is not judged at all.

    * the label sits at the START of a row (numbers to its right, as far as
      the next label of that row): :func:`_row_total` judges each.
    * the label HEADS a column (numbers below it): :func:`_column_total`
      judges the column as one total; it is never ``failed``.

    A WRONG total needs an unambiguous shape (:func:`_clean`); a mismatch in
    any other shape is ``not_checked`` with the reason, never ``failed``."""
    good = 0
    wrong: list[str] = []
    unknown: list[str] = []
    seen_labels = 0
    for sheet, cells in book.formulas.items():
        saved = book.values.get(sheet, {})
        by_row: dict[int, list[int]] = {}
        for (r, c) in cells:
            by_row.setdefault(r, []).append(c)
        for (row, col) in sorted(k for k, v in cells.items() if _is_label(v)):
            if late():
                raise _OutOfTime()
            seen_labels += 1
            if seen_labels > MAX_TOTALS:
                unknown.append(f"{_place(sheet, row, col)} and the totals after it were not read")
                return good, wrong, unknown
            right: list[int] = []
            for c in sorted(c for c in by_row.get(row, ()) if c > col):
                x = cells.get((row, c))
                if _is_label(x):
                    break   # the next label of this row owns what follows it
                if _is_number(x) or _is_formula(x):
                    right.append(c)
            if right:
                for n, c in enumerate(right):
                    if n and late():        # a wide total row: each figure looks up its own column
                        raise _OutOfTime()
                    verdict = _row_total(book, sheet, cells, saved, row, c, col)
                    if verdict is None:
                        continue
                    kind, words = verdict
                    if kind == PASSED:
                        good += 1
                    elif kind == FAILED:
                        wrong.append(words)
                    else:
                        unknown.append(words)
                continue
            verdict = _column_total(book, sheet, cells, saved, row, col)
            if verdict is None:
                continue
            kind, words = verdict
            if kind == PASSED:
                good += 1
            else:
                unknown.append(words)
    return good, wrong, unknown


def _above(cells: dict, row: int, col: int) -> "tuple[list[tuple[int, Any]], str, int]":
    """The cells above ``(row, col)``, nearest first, up to the column's
    heading: ``([(row, value or None for a blank)], the heading's text, its
    row)``. The heading is the first text above that is not a number typed
    as text; ``("", 0)`` when the top of the sheet (or :data:`_SCAN_ROWS`)
    comes first."""
    out: list[tuple[int, Any]] = []
    r = row - 1
    while r >= 1 and len(out) < _SCAN_ROWS:
        v = cells.get((r, col))
        if _is_heading(v):
            return out, str(v), r
        out.append((r, v))
        r -= 1
    return out, "", 0


def _near(above: "list[tuple[int, Any]]") -> "tuple[list[tuple[int, Any]], int]":
    """``(the unbroken run of filled cells nearest above, how many blank
    cells were stepped over before it)``. Blank rows before a total are
    stepped over (and counted: a total with a blank above it is never called
    wrong)."""
    gap = 0
    while gap < len(above) and above[gap][1] is None:
        gap += 1
    run: list[tuple[int, Any]] = []
    for r, v in above[gap:]:
        if v is None:
            break
        run.append((r, v))
    return run, gap


def _sums(book: _Book, sheet: str, cells: dict, part: "list[tuple[int, Any]]", col: int) -> list[float]:
    """What the cells of *part* (nearest first) may add up to. With no total
    row among them: their sum. With subtotals among them a total may add the
    rows, the subtotals, or only the rows since the last subtotal: all
    three. ``[]`` when a cell cannot be read as a number to add (a number
    typed as text, a date, a formula with no value)."""
    plain: list[float] = []      # every row that is not itself a total
    recent: list[float] = []     # ... since the last total row above
    subtotals: list[float] = []
    for r, v in part:
        if v is None:
            continue
        if not (_is_number(v) or _is_formula(v)):
            return []
        got = book.number(sheet, r, col)
        if got is None:
            return []
        if _row_is_total(cells, r, col):
            subtotals.append(got)
        else:
            plain.append(got)
            if not subtotals:
                recent.append(got)
    if not plain and not subtotals:
        return []
    if not subtotals:
        return [sum(plain)]
    return [sum(x) for x in (recent, plain, subtotals) if x]


def _under_heading(cells: dict, run: "list[tuple[int, Any]]", heading: str, heading_row: int, col: int) -> bool:
    """Does *run* end right under a TEXT heading that really heads the list?
    (Not the top of the sheet, not a blank, and not a word sitting in the
    middle of a longer list of numbers: "-" for an empty amount.)"""
    if not run or not heading or run[-1][0] != heading_row + 1:
        return False
    over = cells.get((heading_row - 1, col)) if heading_row > 1 else None
    return not (_is_number(over) or _is_formula(over) or _is_text_number(over))


def _clean(cells: dict, run: "list[tuple[int, Any]]", gap: int, heading: str, heading_row: int,
           col: int, label_col: int) -> bool:
    """The ONE shape in which a total that does not match is called WRONG:
    the list sits directly above the total (no blank between) and directly
    under a text heading; it has at least two rows; every row has a text
    label in the total's own label column and a real number (not a formula,
    not a number typed as text) in this column; none of its rows is itself a
    total."""
    if gap or len(run) < 2 or not _under_heading(cells, run, heading, heading_row, col):
        return False
    for r, v in run:
        label = cells.get((r, label_col))
        if not _is_number(v) or not (isinstance(label, str) and label.strip() and not _is_formula(label)):
            return False
        if _row_is_total(cells, r, col):
            return False
    return True


def _row_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int,
               label_col: int) -> "tuple[str, str] | None":
    """One figure of a total row, judged. None = it is not a total to add (a
    percent, a column headed rate / price / balance / invoice no ...).

    ``passed``: the figure equals what the cells above it add up to (the
    unbroken run directly above, or every number up to the column's heading
    with blank cells and blank rows stepped over; with subtotals among them
    the rows, the subtotals or the rows since the last subtotal). A match is
    a pass even in a loose shape.

    ``failed``: ONLY in the clean shape (:func:`_clean`), with both figures.

    ``not_checked``: every other mismatch, and a formula total that is not a
    plain ``=SUM`` of exactly the list above it (:func:`_formula_total`)."""
    place = _place(sheet, row, col)
    v = cells.get((row, col))
    if (sheet, row, col) in book.percent:
        return None
    above, heading, heading_row = _above(cells, row, col)
    if heading and _NOT_ADDED_RX.search(heading):
        return None
    run, gap = _near(above)
    if _is_formula(v):
        return _formula_total(book, sheet, cells, saved, row, col, label_col, run, heading, heading_row)
    if not any(_is_number(x) or _is_formula(x) for _r, x in above):
        return NOT_CHECKED, f"{place} has no numbers above it to add up"
    total = float(v)
    for part in (run, [(r, x) for r, x in above if x is not None]):
        if part and any(_same(total, candidate) for candidate in _sums(book, sheet, cells, part, col)):
            return PASSED, ""
    if _clean(cells, run, gap, heading, heading_row, col, label_col):
        added = sum(float(x) for _r, x in run)
        return FAILED, f"{place} says {_num(total)} and the cells above it add up to {_num(added)}"
    if any(x is not None and _row_is_total(cells, r, col) for r, x in above):
        return NOT_CHECKED, f"{place} {UNDER_TOTALS}"
    return NOT_CHECKED, f"{place} could not be added up: {NOT_ONE_LIST}"


def _formula_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int, label_col: int,
                   run: "list[tuple[int, Any]]", heading: str, heading_row: int) -> "tuple[str, str]":
    """A total written as a formula. It "adds up" ONLY when it is a plain
    ``=SUM`` of one range and that range is exactly the list above it: the
    unbroken run of numbers under the column's text heading (for a subtotal,
    the run since the total row before it). A range that leaves a row out or
    takes one in, any other formula, and a saved result that is not what the
    cells add up to are ``not_checked``; a formula total is never ``failed``
    (a spreadsheet program works it out again when it opens the file)."""
    place = _place(sheet, row, col)
    ref = _one_sum_range(cells.get((row, col)))
    if ref is None:
        return NOT_CHECKED, f"{place} is worked out by a formula this check cannot follow"
    block = run
    first_total = next((i for i, (r, _v) in enumerate(run) if _row_is_total(cells, r, col)), None)
    if first_total is not None:
        if _label_words(cells.get((row, label_col))) not in _SUBTOTAL_WORDS:
            return NOT_CHECKED, f"{place} {UNDER_TOTALS}"
        block = run[:first_total]
    elif not _under_heading(cells, run, heading, heading_row, col):
        return NOT_CHECKED, f"{place} could not be added up: {NOT_ONE_LIST}"
    if not block or not all(_is_number(v) or _is_formula(v) for _r, v in block):
        return NOT_CHECKED, f"{place} could not be added up: {NOT_ONE_LIST}"
    numbers = [book.number(sheet, r, col) for r, _v in block]
    if any(n is None for n in numbers):
        return NOT_CHECKED, f"{place} adds cells this check cannot work out"
    ref_sheet, r1, c1, r2, c2 = ref
    top, bottom = block[-1][0], block[0][0]
    letter = _col_letters(col)
    if (ref_sheet and ref_sheet.lower() != sheet.lower()) or (c1, c2) != (col, col) or (r1, r2) != (top, bottom):
        return NOT_CHECKED, (
            f"{place} adds {_col_letters(c1)}{r1} to {_col_letters(c2)}{r2}, and the list above it runs from "
            f"{letter}{top} to {letter}{bottom}"
        )
    added = sum(float(n) for n in numbers if n is not None)
    last = saved.get((row, col))
    if _is_number(last) and not _same(float(last), added):
        return NOT_CHECKED, (
            f"{place} has a saved result of {_num(float(last))} and its cells add up to {_num(added)}: "
            "a spreadsheet program works it out again when it opens the file"
        )
    return PASSED, ""


def _column_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int) -> "tuple[str, str] | None":
    """A column headed by a total word, judged as ONE total. None = not a
    total. Each row's figure should be the sum of the numbers to its left
    (or, for exactly two of them, their product: quantity times price); a
    formula counts only as a plain ``=SUM`` of exactly those cells. A column
    that is neither is ``not_checked``: it is never ``failed``."""
    if col < 3 or not any(cells.get((row, c)) is not None for c in range(1, col)):
        return None
    place = f"the {str(cells.get((row, col))).strip()!r} column of {sheet}"
    judged = 0
    r = row + 1
    while (r, col) in cells and judged < 5_000:
        v = cells[(r, col)]
        if not (_is_number(v) or _is_formula(v)):
            break
        if _row_is_total(cells, r, col):
            r += 1
            continue
        beside = [c for c in range(1, col) if _is_number(cells.get((r, c))) or _is_formula(cells.get((r, c)))]
        if _is_formula(v):
            ref = _one_sum_range(v)
            if (ref is None or len(beside) < 2 or (ref[0] and ref[0].lower() != sheet.lower())
                    or (ref[1], ref[3]) != (r, r) or beside != list(range(ref[2], ref[4] + 1))):
                return NOT_CHECKED, f"{place} is worked out by a formula this check cannot follow"
        else:
            left = [book.number(sheet, r, c) for c in beside]
            if len(left) < 2:
                return None  # nothing beside it to add: a column of figures, not of totals
            if any(x is None for x in left):
                return NOT_CHECKED, f"{place} sits beside a cell this check cannot work out"
            nums = [float(x) for x in left if x is not None]
            if not _same(float(v), sum(nums)) and not (len(nums) == 2 and _same(float(v), nums[0] * nums[1])):
                return NOT_CHECKED, f"{place} is not the sum of the numbers beside it"
        judged += 1
        r += 1
    return (PASSED, "") if judged else None


def _named(items: list[str], rest_words: str) -> str:
    shown = items[:MAX_NAMED]
    rest = len(items) - len(shown)
    return "; ".join(shown) + (f"; and {rest:,} more {rest_words}" if rest > 0 else "")


def _unpacked_too_large(path: Path) -> bool:
    with zipfile.ZipFile(path) as zf:
        return sum(int(i.file_size) for i in zf.infolist()) > MAX_UNPACKED_BYTES


def _sheet_checks(path: Path, name: str, late: Callable[[], bool] = _never_late) -> list[Check]:
    """A workbook's error cells and totals. Where the reply's deadline runs
    out, what was not reached is ``not_checked`` and what was is kept."""
    try:
        book = _load(path, late)
    except _OutOfTime:
        return [Check(SHEET_ERRORS, NOT_CHECKED, OUT_OF_TIME, file=name)]
    out: list[Check] = []
    # A formula with NO saved result is worked out here (the app's own
    # evaluator): "no error cells" is said only when every filled cell holds
    # a value or a formula that was worked out without an error. A workbook
    # read only in part is not worked out (a formula over half a column
    # would come to the wrong number).
    worked, unworked = ([], 0) if book.cut else _work_out(book, late)
    errors = _error_cells(book, worked)
    if errors:
        out.append(Check(SHEET_ERRORS, FAILED, _named(errors, "cells hold an error"), file=name))
    if book.cut:
        if not errors:
            out.append(Check(SHEET_ERRORS, NOT_CHECKED,
                             f"only its first {MAX_CELLS:,} cells were read, and none of those holds an error",
                             file=name))
    elif unworked:
        out.append(Check(
            SHEET_ERRORS, NOT_CHECKED,
            f"{_plural(unworked, 'formula')} {'has' if unworked == 1 else 'have'} no saved result and could not "
            "be worked out here",
            file=name,
        ))
    elif not errors:
        out.append(Check(SHEET_ERRORS, PASSED, "no error cells", file=name))
    if book.cut:
        out.append(Check(SHEET_TOTALS, NOT_CHECKED, "it has too many cells to add its totals up here", file=name))
        return out
    try:
        good, wrong, unknown = _totals(book, late)
    except _OutOfTime:
        out.append(Check(SHEET_TOTALS, NOT_CHECKED, OUT_OF_TIME, file=name))
        return out
    if good:
        out.append(Check(SHEET_TOTALS, PASSED, f"{_plural(good, 'total')} add{'s' if good == 1 else ''} up", file=name))
    if wrong:
        out.append(Check(SHEET_TOTALS, FAILED, _named(wrong, "totals are wrong"), file=name))
    if unknown:
        out.append(Check(
            SHEET_TOTALS, NOT_CHECKED,
            f"{_plural(len(unknown), 'total')} could not be worked out by rule: "
            + _named(unknown[:1], "") + (f", and {len(unknown) - 1:,} more" if len(unknown) > 1 else ""),
            file=name,
        ))
    return out


# --------------------------------------------------------------------------- #
# A document, a PDF
# --------------------------------------------------------------------------- #
_TAG_RX = re.compile(r"<[^>]*>")


def _docx_check(path: Path, name: str) -> list[Check]:
    from ..documents.lint import lint_docx

    codes ={f.get("code") for f in (lint_docx(path).get("findings") or [])}
    if "docx-unreadable" in codes:
        return [Check(DOC_TEXT, NOT_CHECKED, "its text could not be read", file=name)]
    if "docx-empty" in codes:
        return [Check(DOC_TEXT, FAILED, "it has no text", file=name)]
    return [Check(DOC_TEXT, PASSED, "has text", file=name)]


def _text_has_words(path: Path, ext: str) -> "bool | None":
    """Does a text file hold any words? None = it is not text Iron Jarvis
    can read."""
    from ..deliverables.check import _decode

    with open(path, "rb") as fh:
        data = fh.read(MAX_BYTES)
    text = _decode(data)
    if text is None:
        return None
    if ext in _HTML_EXTS:
        text = _TAG_RX.sub(" ", text)
    return bool(text.strip())


_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
_COUNT = r"(?P<n>\d{1,4}|" + "|".join(_WORD_NUMBERS) + r")"
#: The four forms a page count is read in, and no other: after the name,
#: "report.pdf (3 pages)", "report.pdf has 3 pages", "report.pdf is 3 pages";
#: before it, "the 3-page report.pdf".
_AFTER_NAME_RX = re.compile(
    r"^[\s`*_\"')\]]*(?:\(\s*|\s+(?:has|is)\s+(?:all\s+|just\s+|only\s+)?)" + _COUNT + r"\s*-?\s*pages?\b",
    re.IGNORECASE,
)
_BEFORE_NAME_RX = re.compile(_COUNT + r"\s*-?\s*page\s+[`*_\"'(\[]*$", re.IGNORECASE)
#: A reply that says pages were taken out, put in, split or joined: a count
#: in it may be the count BEFORE the change ("I removed page 3 from the
#: 5-page contract.pdf"), so none is read. (Turning a page keeps the count.)
_PAGE_CHANGE_RX = re.compile(
    r"\b(?:remov\w*|delet\w*|drop(?:s|ped|ping)?|cut(?:s|ting)?|trim\w*|split\w*|extract\w*|merg\w*|combin\w*"
    r"|append\w*|insert\w*|shorter|fewer|longer)\b"
    r"|\badd(?:s|ed|ing)?\b[^.\n]{0,60}\bpages?\b",
    re.IGNORECASE,
)


def stated_pages(reply: str, name: str) -> "int | None":
    """The page count a reply STATES for the file *name*, or None. Only a
    count tied to that file's own name, in one of four forms, is read
    ("report.pdf (3 pages)", "report.pdf has 3 pages", "report.pdf is 3
    pages", "the 3-page report.pdf"): "I read your 12-page contract and saved
    report.pdf" states none. Two different counts for the one file state
    none either, and a reply with a word that changes pages (remove, delete,
    split, merge, add a page, shorter ...) states none at all: its count may
    be the one before the change."""
    text = str(reply or "")[:40_000]
    name = str(name or "")
    if not text or not name or _PAGE_CHANGE_RX.search(text):
        return None
    found: set[int] = set()
    lowered, needle = text.lower(), name.lower()
    start = 0
    while True:
        at = lowered.find(needle, start)
        if at < 0:
            break
        start = at + len(needle)
        for m in (_AFTER_NAME_RX.match(text[start:start + 60]), _BEFORE_NAME_RX.search(text[max(0, at - 40):at])):
            if m is not None:
                said = m.group("n").lower()
                found.add(_WORD_NUMBERS[said] if said in _WORD_NUMBERS else int(said))
    return found.pop() if len(found) == 1 else None


def _pdf_check(path: Path, name: str, reply: str, said_name: str = "") -> list[Check]:
    from pypdf import PdfReader

    pages = len(PdfReader(str(path)).pages)
    said = stated_pages(reply, said_name or name)
    if said is None:
        return [Check(PDF_PAGES, PASSED, f"has {_plural(pages, 'page')}", file=name)]
    if said == pages:
        return [Check(PDF_PAGES, PASSED, f"has the {_plural(pages, 'page')} the reply says", file=name)]
    return [Check(PDF_PAGES, FAILED, f"it has {_plural(pages, 'page')} and the reply says {said:,}", file=name)]


# --------------------------------------------------------------------------- #
# One file, then a reply's files
# --------------------------------------------------------------------------- #
def check_one(path: Any, reply: str = "", *, may_read: Callable[[Any], bool] = readable,
              name: str = "", late: Callable[[], bool] = _never_late) -> list[Check]:
    """Every content check of ONE made file. A file that is no longer there
    (a scratch file the reply removed again) is checked by nothing and says
    nothing. *name* is what the file is called in the record (its own name;
    with its folder's name beside it when two made files share one); *late*
    answers "has the reply's deadline run out?" and is asked inside the
    file. BLOCKING; may raise (the caller turns that into ``not_checked``)."""
    base = file_name(path)
    name = name or base
    if not may_read(path):
        return [Check(FILE_OPENS, NOT_CHECKED, "it is outside the folders Iron Jarvis may read", file=name)]
    p = Path(str(path))
    try:
        if not p.exists() or p.is_dir():
            return []
        size = p.stat().st_size
    except OSError:
        return [Check(FILE_OPENS, NOT_CHECKED, "it could not be read", file=name)]
    if size == 0:
        return [Check(FILE_OPENS, FAILED, "it is empty", file=name)]
    if size > MAX_BYTES:
        return [Check(FILE_OPENS, NOT_CHECKED, f"it is too large to check here ({_size_words(size)})", file=name)]
    from ..deliverables.check import DONE, check_file

    ext = p.suffix.lower()
    # An Office file is a package: one that unpacks past the cap is not
    # opened by ANY reader below (the "opens" look loads a workbook too).
    if ext in _PACKAGE_EXTS and zipfile.is_zipfile(p) and _unpacked_too_large(p):
        return [Check(FILE_OPENS, NOT_CHECKED, "it unpacks too large to check here", file=name)]
    status, why, _size = check_file(p)
    if status != DONE:
        if ext in _TEXT_EXTS and "blank space" in why:
            return [Check(FILE_OPENS, PASSED, "opens", file=name), Check(DOC_TEXT, FAILED, "it has no text", file=name)]
        # Plain words only: the older check's own reason may name an
        # exception or a part of the package (``record.open_words``).
        return [Check(FILE_OPENS, FAILED, open_words(why), file=name)]
    out = [Check(FILE_OPENS, PASSED, "opens", file=name)]
    try:
        if ext in _SHEET_EXTS:
            out += _sheet_checks(p, name, late)
        elif ext == ".docx":
            out += _docx_check(p, name)
        elif ext in _TEXT_EXTS:
            has = _text_has_words(p, ext)
            if has is None:
                out.append(Check(DOC_TEXT, NOT_CHECKED, "its text could not be read", file=name))
            else:
                out.append(Check(DOC_TEXT, PASSED, "has text", file=name) if has
                           else Check(DOC_TEXT, FAILED, "it has no text", file=name))
        elif ext == ".pdf":
            out += _pdf_check(p, name, reply, base)
    except Exception:  # noqa: BLE001 — a check that breaks is "not checked", never a pass
        log.debug("a made file's content check broke", exc_info=True)
        code = SHEET_ERRORS if ext in _SHEET_EXTS else PDF_PAGES if ext == ".pdf" else DOC_TEXT
        out.append(Check(code, NOT_CHECKED, BROKE, file=name))
    return out


def _unique(paths: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in paths or ():
        text = str(p or "").strip()
        key = os.path.normcase(os.path.normpath(text)) if text else ""
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def names_for(paths: "list[str]") -> "dict[str, str]":
    """What each made file is called in the record: its own name, and for two
    files of the same name in two folders the folder's own name beside it
    ("summary.xlsx (in 2023)", ``record.apart``); two that would still read
    the same are numbered. Never a path."""
    bases = [file_name(p) for p in paths]
    out: dict[str, str] = {}
    used: dict[str, int] = {}
    for path, base in zip(paths, bases):
        label = base
        if sum(1 for b in bases if b.lower() == base.lower()) > 1:
            label = apart(base, file_name(str(path).replace("\\", "/").rstrip("/").rpartition("/")[0]))
        n = used[label.lower()] = used.get(label.lower(), 0) + 1
        out[path] = label if n == 1 else apart(base, f"another folder, {n}")
    return out


def check_files(
    paths: Iterable[Any],
    reply: str = "",
    *,
    clock: Callable[[], float] = time.monotonic,
    deadline_s: float = DEADLINE_S,
    max_files: int = MAX_FILES,
    may_read: Callable[[Any], bool] = readable,
    one: Callable[..., list[Check]] = check_one,
) -> list[Check]:
    """Every content check of the files a reply (or a run) made. BLOCKING;
    never raises. The first *max_files* are checked, in order, while
    *deadline_s* lasts (read from *clock*, between files and inside each);
    the rest are ``not_checked`` and say why."""
    todo = _unique(paths)
    names = names_for(todo)
    out: list[Check] = []
    started = clock()

    def late() -> bool:
        return clock() - started > deadline_s

    for path in todo[:max(0, int(max_files))]:
        name = names[path]
        if late():
            out.append(Check(FILE_OPENS, NOT_CHECKED, OUT_OF_TIME, file=name))
            continue
        try:
            out += one(path, reply, may_read=may_read, name=name, late=late)
        except Exception:  # noqa: BLE001 — a check that breaks is "not checked"
            log.debug("a made file could not be checked", exc_info=True)
            out.append(Check(FILE_OPENS, NOT_CHECKED, BROKE, file=name))
    rest = len(todo) - max(0, int(max_files))
    if rest > 0:
        out.append(Check(
            FILE_OPENS, NOT_CHECKED,
            f"{_plural(rest, 'more file')} {'was' if rest == 1 else 'were'} not checked: only the first "
            f"{max_files} of a reply are",
        ))
    return out


async def check_made_files(
    paths: Iterable[Any],
    reply: str = "",
    *,
    clock: Callable[[], float] = time.monotonic,
    deadline_s: float = DEADLINE_S,
    hard_wait_s: "float | None" = None,
    max_files: int = MAX_FILES,
    worker: "Callable[..., list[Check]] | None" = None,
) -> list[Check]:
    """:func:`check_files` off the event loop, waited for at most
    *hard_wait_s* (a file that takes longer leaves its thread to finish,
    bounded by the size caps and by the deadline it looks at inside the
    file, and every file reads ``not_checked``). Never raises (a cancel
    still does)."""
    todo = _unique(paths)
    if not todo:
        return []
    wait = HARD_WAIT_S if hard_wait_s is None else float(hard_wait_s)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(worker or check_files, todo, reply, clock=clock, deadline_s=deadline_s,
                              max_files=max_files),
            wait,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        why = OUT_OF_TIME
    except Exception:  # noqa: BLE001
        log.debug("a reply's made files could not be checked", exc_info=True)
        why = BROKE
    names = names_for(todo)
    return [Check(FILE_OPENS, NOT_CHECKED, why, file=names[p]) for p in todo[:max(1, int(max_files))]]


__all__ = [
    "BROKE", "DEADLINE_S", "HARD_WAIT_S", "MAX_BYTES", "MAX_CELLS", "MAX_FILES", "MAX_FORMULA_READS",
    "MAX_UNPACKED_BYTES", "MAX_VISITED", "MAX_WORKED", "NOT_ONE_LIST", "OUT_OF_TIME", "UNDER_TOTALS", "check_files",
    "check_made_files", "check_one", "names_for", "parse_sum", "readable", "stated_pages",
]
