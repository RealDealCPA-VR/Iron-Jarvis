"""What a made file SAYS, checked by rule (no model call, ever).

The app already checked that a required file exists, is not empty and opens
(``deliverables/check.check_file``, reused here for everything but a
workbook). Nothing looked inside it: a sheet full of ``#DIV/0!`` or a grand
total that is not the sum of its rows went out under "Done". These are the
checks of the content.

WHICH OUTCOME A CHECK MAY USE is fixed by what kind of knowledge it is
(``checks/record``): ``failed`` ("Problem") only for what the file itself
holds or is; ``look`` ("Worth a look") for what Iron Jarvis worked out by its
own arithmetic or read out of prose, said as plain figures with no verdict.

* **it opens** (``file_opens``): on disk, not empty, and its kind loads.
  ``failed`` when it does not open, and when a DOCUMENT kind is empty; an
  empty file of any other kind (``__init__.py``, ``.gitkeep``) says nothing.
* **a spreadsheet has no error cells** (``sheet_no_error_cells``):
  - a cell whose SAVED value is an error (a value, or a formula's saved
    result) is ``failed``: "C7 on Summary holds #DIV/0!";
  - a formula with NO saved result is worked out by the app's own evaluator
    (:func:`_work_out`, ``documents/excel_formula`` over the cells already
    read). One that comes to ``#DIV/0!``, ``#REF!``, ``#N/A`` or ``#NUM!`` is
    ``look``: "D3 on Summary works out to #DIV/0!". One that comes to
    ``#VALUE!`` (the evaluator is stricter than Excel about text in a cell)
    or that cannot be worked out is ``not_checked``, with the count;
  - a formula that names a newer function without the prefix Excel stores it
    with is ``look``: "A2 on Summary may show #NAME? when opened in Excel";
  - "no error cells" is said ONLY when none of the above happened.
* **a spreadsheet's totals** (``sheet_totals``): see :func:`_totals`. A total
  is NEVER ``failed``. A typed total that differs from a clean list above it
  and that nothing else explains is ``look``, with both figures.
* **a text or Word document is not empty of text** (``document_not_empty``;
  a Word file through ``documents/lint.lint_docx``).
* **a PDF's pages** (``pdf_pages``): how many it has, and, only when the
  reply states a count for that file plainly (:func:`stated_pages`), whether
  it is that many. A different count is ``look``, never ``failed``.

RULES OF THE HOUSE. A check never raises: one that breaks is ``not_checked``
with a plain reason, never ``passed``. Only the files the caller hands over
are opened, and the callers hand over the files their own tools wrote (both
chat lanes' ``made_docs``, a run's undo journal), never a path read from a
model's words. Every path goes through the file policy the other readers use
(``core/fs_policy.fs_read_ok``), and a network spelling is refused by its
text first (``tools/base._network_spelling``).

BOUNDED. At most :data:`MAX_FILES` files a reply, a file over
:data:`MAX_BYTES` (or a package that unpacks past :data:`MAX_UNPACKED_BYTES`)
is not opened by any reader here, a workbook is loaded ONCE (:func:`_load`:
the same walk answers "does it open", gives the cells as written and the
saved result of each formula) and its first :data:`MAX_CELLS` filled cells
are read, at most :data:`MAX_WORKED` formulas are worked out (each reading
at most :data:`MAX_FORMULA_READS` cells), and the whole reply's checks share
:data:`DEADLINE_S`, looked at between files AND inside a file (every row of
the cell walk, every formula, every total): what is left when it runs out is
``not_checked``. BLOCKING: :func:`check_made_files` is the hop off the event
loop, and it waits :data:`HARD_WAIT_S` at most.

A FILE'S NAME is all a check keeps of where it is. Two made files with the
same name in two folders are told apart by the folder's own name, kept in
its own field (``Check.folder``; shown "summary.xlsx (in 2023)"), never by a
path. A CELL is named in plain words: "B4 on Summary", or "B4" alone in a
workbook of one sheet.

WORDS. Everything a check says can reach the line under a reply: plain
words only, never an exception's name or a package part's path
(``record.open_words``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
import zipfile
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from itertools import product as _signs
from pathlib import Path
from typing import Any, Callable, Iterable

from .record import (
    DOC_TEXT, FAILED, FILE_OPENS, LOOK, NOT_CHECKED, PASSED, PDF_PAGES, SHEET_ERRORS, SHEET_TOTALS, Check,
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
#: One REPLY's content checks share this long, and its caller waits
#: :data:`HARD_WAIT_S` at most for the worker thread (``checks/turn`` holds
#: the reply's whole check, the record included, inside 3 s).
DEADLINE_S = 1.8
HARD_WAIT_S = 2.0
#: A finished RUN's files are checked when the run is settled, off the loop,
#: with nobody waiting on a reply: they get longer.
RUN_DEADLINE_S = 4.0
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
#: The DOCUMENT kinds: only for these is "it is empty" or "it has no text" a
#: problem. An empty ``__init__.py`` or ``.gitkeep``, and a web page that is
#: only a picture, are what they are meant to be: they get no row at all.
_DOC_EXTS = _SHEET_EXTS | frozenset({
    ".xls", ".ods", ".docx", ".doc", ".odt", ".rtf", ".pdf", ".pptx", ".txt", ".md", ".markdown", ".csv", ".tsv",
})


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _fmt(value: float, places: int) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.{places}f}"


def _num(value: float) -> str:
    """A number as a person reads it: 1,250 or 1,250.40."""
    return _fmt(value, 2)


def _figures(a: float, b: float) -> "tuple[str, str]":
    """Two figures as a person reads them, with enough decimal places to
    tell them apart: two unequal figures never print the same."""
    for places in (2, 3, 4, 5, 6):
        said = _fmt(a, places), _fmt(b, places)
        if said[0] != said[1] or a == b:
            return said
    return repr(float(a)), repr(float(b))


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


class _DoesNotOpen(Exception):
    """The workbook does not open; ``str(exc)`` is the plain reason."""


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
    r"|each|ytd|balance|running|cumulative)\b"
    r"|(?:\b(?:no|nos|number|num|id|ref|reference|code|year|yr|date)\b\.?|#)\s*$",
    re.IGNORECASE,
)
#: A ROW whose own label says it is taken away, or is a figure about the
#: list and not one of its amounts: a total over such a row is not the plain
#: sum of the rows, so a difference is not judged.
_ROW_WORDS_RX = re.compile(
    r"\b(?:discounts?|less|minus|deposits?|paid|payments?|refunds?|credits?|expenses?|costs?|withheld"
    r"|withholding|deductions?|rebates?|returns?|allowances?|opening|closing|count|largest|smallest|highest"
    r"|lowest|maximum|minimum|max|min)\b",
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
_SCAN_ROWS = 60_000
#: A total that may take rows away is tried with every choice of signs over
#: at most this many rows.
_SIGN_ROWS = 12
#: The errors a worked-out formula is said to come to. ``#VALUE!`` is not
#: one of them: the app's evaluator is stricter than Excel about text in a
#: cell it reads (a dash, a date typed as text), so that one is "not checked".
_LOOK_CODES = frozenset({"#DIV/0!", "#REF!", "#N/A", "#NUM!"})

NOT_ONE_LIST = "the rows above it could not be read as one list"
UNDER_TOTALS = "sits under other totals, and this check cannot tell which cells it adds"
ANOTHER_WAY = "may be worked out another way than adding the rows above it"
ROUNDER = "is written rounder than the rows above it, so it was not added up"


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
    """A workbook's filled cells as written (``formulas``: a value, or a
    formula's own text), the result a spreadsheet program last SAVED for each
    formula (``values``: formula cells only; absent where nothing was saved)
    and, for a formula with no saved result, what the app's own evaluator
    worked out (``worked``). ``spaces`` are the cells that hold only blank
    space (text to a formula, nothing to a reader); ``sheets`` every sheet's
    name."""

    def __init__(self) -> None:
        self.formulas: dict[str, dict[tuple[int, int], Any]] = {}
        self.values: dict[str, dict[tuple[int, int], Any]] = {}
        self.worked: dict[str, dict[tuple[int, int], Any]] = {}
        self.percent: set[tuple[str, int, int]] = set()
        self.spaces: set[tuple[str, int, int]] = set()
        self.sheets: list[str] = []
        self.cut = False
        self._label_cols: dict[str, dict[int, int]] = {}

    def total_row(self, sheet: str, row: int, before: int) -> bool:
        """Is *row* itself a total row: does a total word sit in it, left of
        column *before*? (Each sheet's labels are found once.)"""
        cols = self._label_cols.get(sheet)
        if cols is None:
            cols = {}
            for (r, c), v in self.formulas.get(sheet, {}).items():
                if c < cols.get(r, 1 << 30) and _is_label(v):
                    cols[r] = c
            self._label_cols[sheet] = cols
        return cols.get(row, 1 << 30) < before

    def place(self, sheet: str, row: int, col: int) -> str:
        """A cell in plain words: "B4 on Summary", or "B4" alone when the
        workbook has one sheet. Never "Summary!B4"."""
        cell = f"{_col_letters(col)}{row}"
        return cell if len(self.sheets or self.formulas) <= 1 else f"{cell} on {sheet}"

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
    """Open a workbook and read it, ONCE: the same load answers "does it
    open" (:class:`_DoesNotOpen` with the plain reason when it does not),
    gives every filled cell as written and each formula's saved result. The
    deadline (*late*) is looked at on every row of the walk: a long sheet
    stops there (:class:`_OutOfTime`), never at the end of the file.
    BLOCKING."""
    from openpyxl import load_workbook

    try:
        if not zipfile.is_zipfile(path):
            raise _DoesNotOpen(open_words("not a valid Office file"))
        with zipfile.ZipFile(path) as zf:
            if "xl/workbook.xml" not in set(zf.namelist()):
                raise _DoesNotOpen(open_words("its package has no workbook"))
        wb = load_workbook(str(path), read_only=True, data_only=False)
    except _DoesNotOpen:
        raise
    except Exception as exc:  # noqa: BLE001 — "does not open" is the answer, in plain words
        log.debug("a made workbook does not open", exc_info=True)
        raise _DoesNotOpen(open_words("")) from exc
    try:
        if not wb.sheetnames:
            raise _DoesNotOpen(open_words("the workbook has no sheets"))
        try:
            return _read_once(wb, late)
        except (ImportError, AttributeError, TypeError):
            # The one-pass read leans on the library's own cell parser. If a
            # later version moves it, read the slower way (two passes): the
            # answers are the same.
            log.debug("the one-pass read of a workbook is unavailable: reading it twice", exc_info=True)
            return _read_twice(wb, path, late)
    finally:
        wb.close()


def _saved_result(element: Any, value_tag: str, cast_number: Any) -> Any:
    """The result a spreadsheet program saved for a formula cell (its
    ``<v>``): a number, an error's text, a word, True/False; None for none."""
    raw = element.findtext(value_tag, None) or None
    if raw is None:
        return None
    kind = element.get("t", "n")
    if kind == "n":
        return cast_number(raw)
    if kind == "b":
        return bool(int(raw))
    return raw


def _keep(book: _Book, sheet: str, cells: dict, row: int, col: int, value: Any) -> bool:
    """Put one cell in the book as written. False for a cell that holds
    nothing (a blank, or only blank space, which is noted apart)."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        if value:
            book.spaces.add((sheet, row, col))
        return False
    cells[(row, col)] = value
    return True


def _read_once(wb: Any, late: Callable[[], bool]) -> _Book:
    """Every sheet's real cells in ONE pass over the file: the library's own
    cell parser, asked for the formula AND, beside it, the saved result."""
    from openpyxl.styles.numbers import BUILTIN_FORMATS
    from openpyxl.worksheet._reader import VALUE_TAG, WorkSheetParser, _cast_number

    class _Both(WorkSheetParser):
        def parse_cell(self, element: Any) -> dict:
            cell = super().parse_cell(element)
            if cell["data_type"] == "f":
                cell["saved"] = _saved_result(element, VALUE_TAG, _cast_number)
            return cell

    formats: dict[int, bool] = {}

    def is_percent(style_id: int) -> bool:
        if style_id not in formats:
            try:
                fmt_id = wb._cell_styles[style_id].numFmtId
                fmt = BUILTIN_FORMATS.get(fmt_id, "General") if fmt_id < 164 else wb._number_formats[fmt_id - 164]
                formats[style_id] = "%" in str(fmt or "")
            except Exception:  # noqa: BLE001 — a format is a hint only
                formats[style_id] = False
        return formats[style_id]

    book = _Book()
    book.sheets = list(wb.sheetnames)
    seen = 0
    for ws in wb.worksheets:
        cells = book.formulas.setdefault(ws.title, {})
        saved = book.values.setdefault(ws.title, {})
        with ws._get_source() as src:
            parser = _Both(src, ws._shared_strings, data_only=False, epoch=wb.epoch,
                           date_formats=wb._date_formats, timedelta_formats=wb._timedelta_formats)
            for _row_number, row in parser.parse():
                if late():
                    raise _OutOfTime()
                for cell in row:
                    v, r, c = cell["value"], cell["row"], cell["column"]
                    if not _keep(book, ws.title, cells, r, c, v):
                        continue
                    seen += 1
                    if cell["data_type"] == "f":
                        if cell.get("saved") is not None:
                            saved[(r, c)] = cell["saved"]
                    elif _is_number(v) and cell["style_id"] and is_percent(cell["style_id"]):
                        book.percent.add((ws.title, r, c))
                if seen >= MAX_CELLS:
                    book.cut = True
                    break
        if book.cut:
            break
    return book


def _read_twice(wb: Any, path: Path, late: Callable[[], bool]) -> _Book:
    """The same book by the library's public reader: the cells as written
    from *wb*, the saved results from a second, values-only load."""
    from openpyxl import load_workbook

    book = _Book()
    book.sheets = list(wb.sheetnames)
    seen = 0
    for ws in wb.worksheets:
        cells = book.formulas.setdefault(ws.title, {})
        book.values.setdefault(ws.title, {})
        try:
            ws.reset_dimensions()   # its real cells, never the size it declares
        except Exception:  # noqa: BLE001
            pass
        for row in ws.iter_rows():
            if late():
                raise _OutOfTime()
            for cell in row:
                r, c = getattr(cell, "row", None), getattr(cell, "column", None)
                if not isinstance(r, int) or not isinstance(c, int):
                    continue
                v = cell.value
                if not _keep(book, ws.title, cells, r, c, v):
                    continue
                seen += 1
                if _is_number(v):
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
    kept = load_workbook(str(path), read_only=True, data_only=True)
    try:
        for ws in kept.worksheets:
            want = {k for k, v in book.formulas.get(ws.title, {}).items() if not _is_number(v) and (
                _is_formula(v) or not isinstance(v, (str, bool)))}
            if not want:
                continue
            last = max(r for r, _c in want)
            try:
                ws.reset_dimensions()
            except Exception:  # noqa: BLE001
                pass
            for row in ws.iter_rows(max_row=last):
                if late():
                    raise _OutOfTime()
                for cell in row:
                    key = (getattr(cell, "row", None), getattr(cell, "column", None))
                    if key in want and cell.value is not None and not (
                            isinstance(cell.value, str) and not cell.value.strip()):
                        book.values[ws.title][key] = cell.value
    finally:
        kept.close()
    return book


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
            name = self.canon(sheet)
            v = book.formulas.get(name, {}).get((row, col))
            if v is None:
                # A cell that holds only blank space is TEXT to a formula
                # (arithmetic over it is an error in Excel), never an empty cell.
                return " " if (name, row, col) in book.spaces else None
            if isinstance(v, (int, float, bool)) or (isinstance(v, str) and not v.startswith("=")):
                return v
            if isinstance(v, str) or hasattr(v, "ref"):
                return book.values.get(name, {}).get((row, col))
            return v

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


def _plain_new_function(formula: str) -> bool:
    """Does this formula name a newer function (CONCAT, IFNA, DAYS, XLOOKUP,
    MAXIFS ...) WITHOUT the prefix Excel stores it with? Written plainly into
    a file, Excel opens such a cell as ``#NAME?``. The app's own writer adds
    the prefix (``excel_formula.for_file``); this is that function's own
    table and pattern, read without its side doors."""
    if "(" not in formula:
        return False
    from ..documents.excel_formula import _FOR_FILE_RX, _PREFIXED

    for m in _FOR_FILE_RX.finditer(formula):
        name = m.group(1)
        if name and name.upper() in _PREFIXED:
            return True
    return False


def _work_out(book: _Book, late: Callable[[], bool] = _never_late) -> "tuple[list[tuple[str, int, int, str]], int]":
    """What Iron Jarvis works out about the formulas itself. Returns
    ``(findings worth a look, as (sheet, row, col, words); how many formulas
    could not be worked out)``:

    * a formula that names a newer function without Excel's prefix:
      "may show #NAME? when opened in Excel" (whatever result was saved);
    * a formula with NO saved result, worked out by the app's own evaluator:
      one that comes to an error of :data:`_LOOK_CODES` "works out to
      #DIV/0!"; one that comes to ``#VALUE!`` or any other error, or that the
      evaluator cannot do, counts as not worked out. A result that was
      worked out is kept in ``book.worked``.

    Bounded: at most :data:`MAX_WORKED` formulas, while the deadline lasts;
    the rest count as not worked out. Raises only :class:`_OutOfTime`, when
    the deadline runs out while the function names are still being looked
    at (nothing can then be said about the formulas at all)."""
    every = [
        (sheet, row, col)
        for sheet, cells in book.formulas.items()
        for (row, col), v in sorted(cells.items(), key=lambda item: item[0])
        if _is_formula(v) or (not isinstance(v, (str, int, float, bool)) and hasattr(v, "ref"))
    ]
    if not every:
        return [], 0
    todo = [k for k in every if book.values.get(k[0], {}).get((k[1], k[2])) is None]
    found: list[tuple[str, int, int, str]] = []
    named: set[tuple[str, int, int]] = set()
    try:
        for sheet, row, col in every:
            if late():
                raise _OutOfTime()
            v = book.formulas[sheet][(row, col)]
            if isinstance(v, str) and _plain_new_function(v):
                named.add((sheet, row, col))
                found.append((sheet, row, col, "may show #NAME? when opened in Excel"))
    except _OutOfTime:
        raise
    except Exception:  # noqa: BLE001 — the table could not be read: nothing is called clean
        log.debug("a workbook's function names could not be looked at", exc_info=True)
        return found, max(1, len(todo))
    todo = [k for k in todo if k not in named]
    if not todo:
        return found, 0
    unworked = 0
    try:
        from ..documents.excel_formula import ExcelError

        grid = _grid(book, late)
    except Exception:  # noqa: BLE001 — no evaluator: nothing was worked out
        log.debug("the formula evaluator could not be set up", exc_info=True)
        return found, len(todo)
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
            code = str(exc.code)
            if code in _LOOK_CODES:
                found.append((sheet, row, col, f"works out to {code}"))
            else:
                unworked += 1   # #VALUE! and the like: the evaluator is stricter than Excel here
            continue
        except Exception:  # noqa: BLE001 — a formula this evaluator cannot do
            unworked += 1
            continue
        book.worked.setdefault(sheet, {})[(row, col)] = 0.0 if got is None else got
    return found, unworked


def _in_order(book: _Book, found: "Iterable[tuple[str, int, int, str]]") -> list[str]:
    order = {name: i for i, name in enumerate(book.formulas)}
    return [f"{book.place(sheet, row, col)} {words}"
            for sheet, row, col, words in sorted(found, key=lambda f: (order.get(f[0], 0), f[1], f[2]))]


def _error_cells(book: _Book) -> list[str]:
    """Every cell whose SAVED value is an Excel error ("C7 on Summary holds
    #DIV/0!"): written as a value, or saved as a formula's result. What the
    file itself holds, and nothing Iron Jarvis worked out."""
    errors = _errors()
    found: list[tuple[str, int, int, str]] = []
    for sheet, cells in book.formulas.items():
        saved = book.values.get(sheet, {})
        for (row, col), v in cells.items():
            shown = v.strip() if isinstance(v, str) and v.strip() in errors else None
            if shown is None and (_is_formula(v) or not isinstance(v, (str, int, float, bool))):
                got = saved.get((row, col))
                shown = got.strip() if isinstance(got, str) and got.strip() in errors else None
            if shown is not None:
                found.append((sheet, row, col, f"holds {shown}"))
    return _in_order(book, found)


def _decimals(value: float) -> int:
    """How many decimal places a number was written with (6 at most)."""
    text = repr(float(value))
    if "e" in text or "E" in text:
        return 6
    return min(6, len(text.partition(".")[2].rstrip("0")))


def _same(total: float, added: float) -> bool:
    """Is a written total the sum of its cells? Equal (to the cent, or to
    the last place the total was written with), or the sum sits at an EXACT
    half of the total's last place and the total is it rounded either way
    (12.5 + 8 is 21 to one hand and 20 to another: both add up). A total
    that matches only after any other rounding is not "the same": see
    :func:`_rounder`."""
    d = _decimals(total)
    if abs(total - added) <= 0.5 * 10 ** -max(2, d) + 1e-9:
        return True
    scaled = added * 10 ** d
    low = math.floor(scaled + 1e-9)
    if abs(scaled - low - 0.5) <= 1e-6:
        at = total * 10 ** d
        return abs(at - low) <= 1e-6 or abs(at - low - 1) <= 1e-6
    return False


def _rounder(total: float, vals: "list[float]") -> bool:
    """Is the total written COARSER than its rows and equal to them only
    after rounding (600.20 + 634.29 under a total of 1,234; three rows of
    33.335 under 100.02)? Such a total is neither "adds up" nor "worth a
    look": it is not judged."""
    # Rows no finer than the total need no test of their own: their sum is a
    # whole number of the total's last place, so it is the total or at least
    # one such place away from it, and rounding each row changes nothing.
    # (Its one caller hands over a clean list: two rows or more.)
    d = _decimals(total)
    if abs(total - sum(vals)) <= 0.5 * 10 ** -d + 1e-9:
        return True
    step = Decimal(1).scaleb(-d)
    try:
        for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN):
            each = sum(Decimal(repr(float(v))).quantize(step, rounding=mode) for v in vals)
            if abs(float(each) - total) <= 1e-9:
                return True
    except ArithmeticError:
        return False
    return False


def _explained(total: float, vals: "list[float]") -> bool:
    """Does a plain OTHER reading of the rows (top row first) give the total?
    Then it is not a sum that went wrong, and it is not judged:

    * the sum without the top row (a second heading row of numbers, a count
      above the list);
    * the product of the rows (quantity times price, months times payment);
    * some rows taken away (income less expenses, a deposit paid): any
      choice of signs, over at most :data:`_SIGN_ROWS` rows;
    * a figure that lies between the smallest and the largest of rows that
      all have one sign (an average, the last or a running value, the
      largest, a rank);
    * how many rows there are."""
    n = len(vals)
    if n < 1:
        return False
    if _same(total, float(n)):
        return True
    if n >= 2 and _same(total, sum(vals[1:])):
        return True
    if n >= 2:
        try:
            if _same(total, math.prod(vals)):
                return True
        except OverflowError:
            pass
    if (all(v >= 0 for v in vals) or all(v <= 0 for v in vals)) and min(vals) - 1e-9 <= total <= max(vals) + 1e-9:
        return True
    if 2 <= n <= _SIGN_ROWS:
        for signs in _signs((1.0, -1.0), repeat=n):
            if all(s > 0 for s in signs):
                continue
            if _same(total, sum(s * v for s, v in zip(signs, vals))):
                return True
    return False


def _totals(book: _Book, late: Callable[[], bool] = _never_late) -> "tuple[int, list[str], list[str]]":
    """``(how many totals add up, the ones worth a look, the ones that could
    not be worked out)``, each of the last two in plain words.

    A TOTAL is a number or a formula beside a cell that is, WHOLE, a total
    word (:data:`_TOTAL_WORDS`). A figure beside "Total revenue" or a payee
    called "Total Wine & More" is not judged at all.

    * the label sits at the START of a row (numbers to its right, as far as
      the next label of that row): :func:`_row_total` judges each.
    * the label HEADS a column (numbers below it): :func:`_column_total`
      judges the column as one total.

    A total is NEVER "wrong" (``failed``): Iron Jarvis added it up itself,
    and that is not the same as the file being wrong. A typed total that
    differs from a clean list (:func:`_clean`) and that no other plain
    reading explains (:func:`_other_reading`) is "worth a look", with both
    figures; every other difference is ``not_checked`` with the reason."""
    good = 0
    look: list[str] = []
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
                unknown.append(f"{book.place(sheet, row, col)} and the totals after it were not read")
                return good, look, unknown
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
                    verdict = _row_total(book, sheet, cells, saved, row, c, col, by_row)
                    if verdict is None:
                        continue
                    kind, words = verdict
                    if kind == PASSED:
                        good += 1
                    elif kind == LOOK:
                        look.append(words)
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
    return good, look, unknown


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
    stepped over (and counted: a total with a blank above it is never said
    to differ)."""
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
        if book.total_row(sheet, r, col):
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


def _clean(book: _Book, sheet: str, cells: dict, run: "list[tuple[int, Any]]", gap: int, heading: str,
           heading_row: int, col: int, label_col: int) -> bool:
    """The ONE shape in which a total that does not match is said to differ:
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
        if book.total_row(sheet, r, col):
            return False
    return True


def _numeric_heading_row(cells: dict, by_row: "dict[int, list[int]]", row: int, label_col: int) -> bool:
    """Is this row a SECOND heading row made of numbers (years, quarters:
    ``Item | 2023 | 2024`` under ``Category | Sales``)? At least two whole
    numbers to the right of the label, each larger than the one before, and
    either counting up by one or all of them years."""
    nums = [cells.get((row, c)) for c in sorted(c for c in by_row.get(row, ()) if c > label_col)]
    nums = [float(x) for x in nums if _is_number(x)]
    if len(nums) < 2 or not all(x.is_integer() for x in nums):
        return False
    steps = [b - a for a, b in zip(nums, nums[1:])]
    if not all(s > 0 for s in steps):
        return False
    return all(s == 1 for s in steps) or all(1900 <= x <= 2100 for x in nums)


def _other_reading(cells: dict, by_row: "dict[int, list[int]]", run: "list[tuple[int, Any]]", row: int,
                   label_col: int, total: float) -> bool:
    """Is there a plain reason the total of a clean list is NOT the sum of
    its rows? Then the difference is not judged (it is not "worth a look"):

    * the total's label is not the first filled cell of its row (a long
      table whose rows are "North | Orders", "North | Total");
    * a row's own label says it is not added, or is taken away (a rate, a
      price, an average; a discount, a deposit paid, expenses);
    * the top row of the list is a second heading row of numbers;
    * another reading of the same rows gives the total (:func:`_explained`)."""
    if any((row, c) in cells for c in range(1, label_col)):
        return True
    for r, _v in run:
        label = cells.get((r, label_col))
        if isinstance(label, str) and (_NOT_ADDED_RX.search(label) or _ROW_WORDS_RX.search(label)):
            return True
    if _numeric_heading_row(cells, by_row, run[-1][0], label_col):
        return True
    return _explained(total, [float(x) for _r, x in reversed(run)])


def _row_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int, label_col: int,
               by_row: "dict[int, list[int]] | None" = None) -> "tuple[str, str] | None":
    """One figure of a total row, judged. None = it is not a total to add (a
    percent, a column headed rate / price / balance / invoice no ...).

    ``passed``: the figure equals what the cells above it add up to (the
    unbroken run directly above, or every number up to the column's heading
    with blank cells and blank rows stepped over; with subtotals among them
    the rows, the subtotals or the rows since the last subtotal). A match is
    a pass even in a loose shape.

    ``look``: ONLY a typed figure over a clean list (:func:`_clean`) that is
    not written rounder than its rows (:func:`_rounder`) and that no other
    plain reading explains (:func:`_other_reading`): both figures, no
    verdict. NEVER ``failed``.

    ``not_checked``: every other difference, and a formula total that is not
    a plain ``=SUM`` of exactly the list above it (:func:`_formula_total`)."""
    place = book.place(sheet, row, col)
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
    if _clean(book, sheet, cells, run, gap, heading, heading_row, col, label_col):
        vals = [float(x) for _r, x in run]
        if _rounder(total, vals):
            return NOT_CHECKED, f"{place} {ROUNDER}"
        if _other_reading(cells, by_row or {}, run, row, label_col, total):
            return NOT_CHECKED, f"{place} {ANOTHER_WAY}"
        said, added = _figures(total, sum(vals))
        return LOOK, f"{place} says {said} and the cells above it add up to {added}"
    if any(x is not None and book.total_row(sheet, r, col) for r, x in above):
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
    and never ``look`` (a spreadsheet program works it out again when it
    opens the file)."""
    place = book.place(sheet, row, col)
    ref = _one_sum_range(cells.get((row, col)))
    if ref is None:
        return NOT_CHECKED, f"{place} is worked out by a formula this check cannot follow"
    block = run
    first_total = next((i for i, (r, _v) in enumerate(run) if book.total_row(sheet, r, col)), None)
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
        was, now = _figures(float(last), added)
        return NOT_CHECKED, (
            f"{place} has a saved result of {was} and its cells add up to {now}: "
            "a spreadsheet program works it out again when it opens the file"
        )
    return PASSED, ""


def _column_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int) -> "tuple[str, str] | None":
    """A column headed by a total word, judged as ONE total. None = not a
    total. Each row's figure should be the sum of the numbers to its left
    (or, for exactly two of them, their product: quantity times price); a
    formula counts only as a plain ``=SUM`` of exactly those cells. A column
    that is neither is ``not_checked``: it is never ``failed`` or ``look``."""
    if col < 3 or not any(cells.get((row, c)) is not None for c in range(1, col)):
        return None
    place = f"the {str(cells.get((row, col))).strip()!r} column" + ("" if len(book.sheets or book.formulas) <= 1
                                                                 else f" on {sheet}")
    judged = 0
    r = row + 1
    while (r, col) in cells and judged < 5_000:
        v = cells[(r, col)]
        if not (_is_number(v) or _is_formula(v)):
            break
        if book.total_row(sheet, r, col):
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


def _sheet_checks(path: Path, name: str, late: Callable[[], bool] = _never_late, folder: str = "") -> list[Check]:
    """Everything about a workbook from ONE load: whether it opens, its
    error cells and its totals. Where the reply's deadline runs out, what
    was not reached is ``not_checked`` and what was is kept."""
    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder)

    try:
        book = _load(path, late)
    except _DoesNotOpen as exc:
        return [row(FILE_OPENS, FAILED, str(exc) or open_words(""))]
    except _OutOfTime:
        return [row(FILE_OPENS, PASSED, "opens"), row(SHEET_ERRORS, NOT_CHECKED, OUT_OF_TIME)]
    out = [row(FILE_OPENS, PASSED, "opens")]
    # WHAT THE FILE HOLDS: a saved error value is a problem. WHAT IRON JARVIS
    # WORKS OUT (a formula with no saved result, a function Excel may not
    # know by that name) is worth a look, never a problem. "no error cells"
    # is said only when there is neither and every formula was worked out. A
    # workbook read only in part is not worked out (a formula over half a
    # column would come to the wrong number).
    held = _error_cells(book)
    timed_out = False
    try:
        found, unworked = ([], 0) if book.cut else _work_out(book, late)
    except _OutOfTime:
        found, unworked, timed_out = [], 0, True
    if held:
        out.append(row(SHEET_ERRORS, FAILED, _named(held, "cells hold an error")))
    if found:
        out.append(row(SHEET_ERRORS, LOOK, _named(_in_order(book, found), "formulas")))
    if book.cut:
        if not held:
            out.append(row(SHEET_ERRORS, NOT_CHECKED,
                           f"only its first {MAX_CELLS:,} cells were read, and none of those holds an error"))
    elif timed_out:
        out.append(row(SHEET_ERRORS, NOT_CHECKED, OUT_OF_TIME))
    elif unworked:
        out.append(row(
            SHEET_ERRORS, NOT_CHECKED,
            f"{_plural(unworked, 'formula')} {'has' if unworked == 1 else 'have'} no saved result and could not "
            "be worked out here",
        ))
    elif not held and not found:
        out.append(row(SHEET_ERRORS, PASSED, "no error cells"))
    if book.cut:
        out.append(row(SHEET_TOTALS, NOT_CHECKED, "it has too many cells to add its totals up here"))
        return out
    try:
        good, look, unknown = _totals(book, late)
    except _OutOfTime:
        out.append(row(SHEET_TOTALS, NOT_CHECKED, OUT_OF_TIME))
        return out
    if good:
        out.append(row(SHEET_TOTALS, PASSED, f"{_plural(good, 'total')} add{'s' if good == 1 else ''} up"))
    if look:
        # Iron Jarvis's own adding up: "worth a look", never a problem.
        out.append(row(SHEET_TOTALS, LOOK, _named(look, "totals")))
    if unknown:
        out.append(row(
            SHEET_TOTALS, NOT_CHECKED,
            f"{_plural(len(unknown), 'total')} could not be worked out by rule: "
            + _named(unknown[:1], "") + (f", and {len(unknown) - 1:,} more" if len(unknown) > 1 else ""),
        ))
    return out


# --------------------------------------------------------------------------- #
# A document, a PDF
# --------------------------------------------------------------------------- #
_TAG_RX = re.compile(r"<[^>]*>")


def _docx_check(path: Path, name: str, folder: str = "") -> list[Check]:
    from ..documents.lint import lint_docx

    codes = {f.get("code") for f in (lint_docx(path).get("findings") or [])}
    if "docx-unreadable" in codes:
        return [Check(DOC_TEXT, NOT_CHECKED, "its text could not be read", file=name, folder=folder)]
    if "docx-empty" in codes:
        return [Check(DOC_TEXT, FAILED, "it has no text", file=name, folder=folder)]
    return [Check(DOC_TEXT, PASSED, "has text", file=name, folder=folder)]


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
#: The count must END its phrase: "(3 pages)", "has 3 pages.", "is 3 pages;".
#: "has 2 pages of tables", "has 12 pages more than" and "2 pages per sheet"
#: say something else.
_PHRASE_END = r"\s*[`*_\"']*\s*(?:[).,;]|$)"
#: The four forms a page count is read in, and no other: after the name,
#: "report.pdf (3 pages)", "report.pdf has 3 pages", "report.pdf is 3 pages";
#: before it, "the 3-page report.pdf".
_AFTER_NAME_RX = re.compile(
    r"[\s`*_\"')\]]*(?:\(\s*|\s+(?:has|is)\s+(?:all\s+|just\s+|only\s+)?)" + _COUNT + r"\s*-?\s*pages?"
    + _PHRASE_END,
    re.IGNORECASE,
)
_BEFORE_NAME_RX = re.compile(r"(?<![A-Za-z0-9.])" + _COUNT + r"\s*-?\s*page\s+[`*_\"'(\[]*$", re.IGNORECASE)
#: A reply that says pages were taken out, put in, split, joined or made
#: fewer or more: a count in it may be the count BEFORE the change ("I
#: shortened the 40-page report.pdf to 12 pages"), so none is read. (Turning
#: a page keeps the count.)
_PAGE_CHANGE_RX = re.compile(
    r"\b(?:remov\w*|delet\w*|drop(?:s|ped|ping)?|cut(?:s|ting)?|trim\w*|split\w*|extract\w*|merg\w*|combin\w*"
    r"|append\w*|insert\w*|shorten\w*|shorter|condens\w*|reduc\w*|expand\w*|extend\w*|summar\w*|compress\w*"
    r"|original\w*|fewer|longer)\b"
    r"|\badd(?:s|ed|ing)?\b[^.\n]{0,60}\bpages?\b"
    r"|\bfirst\s+(?:\w+\s+)?pages?\b|\bper\s+sheet\b|\bmore\s+than\b|\bfewer\s+than\b",
    re.IGNORECASE,
)
#: A character that can be part of a file's name or its path.
_NAME_CHAR_RX = re.compile(r"[A-Za-z0-9_\-./\\]")


def _one_mention(text: str, name: str) -> int:
    """Where the reply names the file, when it names it exactly ONCE and as
    a name of its own (not inside a longer name or a path: "annual-report.pdf",
    "out/report.pdf"); -1 otherwise."""
    low, needle = text.lower(), name.lower()
    at = low.find(needle)
    if at < 0 or low.find(needle, at + 1) >= 0:
        return -1
    if at and _NAME_CHAR_RX.match(text[at - 1]):
        return -1
    after = text[at + len(name):at + len(name) + 2]
    if re.match(r"[A-Za-z0-9_\-/\\]|\.[A-Za-z0-9]", after):
        return -1
    return at


def stated_pages(reply: str, name: str) -> "int | None":
    """The page count a reply STATES for the file *name*, or None. A count
    is read only when all of this holds:

    * the reply has no word that changes pages (remove, shorten, condense,
      reduce, expand, summarise, merge, add a page, the first page, per
      sheet, more than ...): its count may be the one before the change;
    * the reply names the file exactly once, as a name of its own;
    * the count is tied to that name in one of four forms ("report.pdf (3
      pages)", "report.pdf has 3 pages", "report.pdf is 3 pages", "the
      3-page report.pdf") and, after the name, ENDS its phrase.

    "I read your 12-page contract and saved report.pdf" states none."""
    text = str(reply or "")[:40_000]
    name = str(name or "")
    if not text or not name or _PAGE_CHANGE_RX.search(text):
        return None
    at = _one_mention(text, name)
    if at < 0:
        return None
    found: set[int] = set()
    end = at + len(name)
    for m in (_AFTER_NAME_RX.match(text, end), _BEFORE_NAME_RX.search(text[max(0, at - 40):at])):
        if m is not None:
            said = m.group("n").lower()
            found.add(_WORD_NUMBERS[said] if said in _WORD_NUMBERS else int(said))
    return found.pop() if len(found) == 1 else None


def _pdf_check(path: Path, name: str, reply: str, folder: str = "") -> list[Check]:
    from pypdf import PdfReader

    pages = len(PdfReader(str(path)).pages)
    # Two made files of one name: a count in the reply could be either's.
    said = None if folder else stated_pages(reply, name)
    if said is None:
        return [Check(PDF_PAGES, PASSED, f"has {_plural(pages, 'page')}", file=name, folder=folder)]
    if said == pages:
        return [Check(PDF_PAGES, PASSED, f"has the {_plural(pages, 'page')} the reply says", file=name, folder=folder)]
    # Read out of the reply's prose: worth a look, never a problem.
    return [Check(PDF_PAGES, LOOK, f"it has {_plural(pages, 'page')} and the reply says {said:,}", file=name,
                  folder=folder)]


# --------------------------------------------------------------------------- #
# One file, then a reply's files
# --------------------------------------------------------------------------- #
def check_one(path: Any, reply: str = "", *, may_read: Callable[[Any], bool] = readable,
              name: str = "", folder: str = "", late: Callable[[], bool] = _never_late) -> list[Check]:
    """Every content check of ONE made file. A file that is no longer there
    (a scratch file the reply removed again) is checked by nothing and says
    nothing; so is an empty file that is not a document. *name* is the
    file's own name; *folder* its folder's name, only when two made files
    share one name; *late* answers "has the reply's deadline run out?" and is
    asked inside the file. BLOCKING; may raise (the caller turns that into
    ``not_checked``)."""
    name = name or file_name(path)

    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder)

    if not may_read(path):
        return [row(FILE_OPENS, NOT_CHECKED, "it is outside the folders Iron Jarvis may read")]
    p = Path(str(path))
    try:
        if not p.exists() or p.is_dir():
            return []
        size = p.stat().st_size
    except OSError:
        return [row(FILE_OPENS, NOT_CHECKED, "it could not be read")]
    ext = p.suffix.lower()
    if size == 0:
        # An empty DOCUMENT is a problem. An empty file of any other kind is
        # what it was meant to be (``__init__.py``, ``.gitkeep``): no row.
        return [row(FILE_OPENS, FAILED, "it is empty")] if ext in _DOC_EXTS else []
    if size > MAX_BYTES:
        return [row(FILE_OPENS, NOT_CHECKED, f"it is too large to check here ({_size_words(size)})")]
    # An Office file is a package: one that unpacks past the cap is not
    # opened by ANY reader below.
    if ext in _PACKAGE_EXTS and zipfile.is_zipfile(p) and _unpacked_too_large(p):
        return [row(FILE_OPENS, NOT_CHECKED, "it unpacks too large to check here")]
    if ext in _SHEET_EXTS:
        try:
            return _sheet_checks(p, name, late, folder)
        except Exception:  # noqa: BLE001 — a check that breaks is "not checked", never a pass
            log.debug("a made workbook's content check broke", exc_info=True)
            return [row(FILE_OPENS, PASSED, "opens"), row(SHEET_ERRORS, NOT_CHECKED, BROKE)]
    from ..deliverables.check import DONE, check_file

    status, why, _size = check_file(p)
    if status != DONE:
        if ext in _TEXT_EXTS and "blank space" in why:
            if ext not in _DOC_EXTS:
                return []       # a page of blank space is not a document that lost its text
            return [row(FILE_OPENS, PASSED, "opens"), row(DOC_TEXT, FAILED, "it has no text")]
        # Plain words only: the older check's own reason may name an
        # exception or a part of the package (``record.open_words``).
        return [row(FILE_OPENS, FAILED, open_words(why))]
    out = [row(FILE_OPENS, PASSED, "opens")]
    try:
        if ext == ".docx":
            out += _docx_check(p, name, folder)
        elif ext in _TEXT_EXTS:
            has = _text_has_words(p, ext)
            if has is None:
                out.append(row(DOC_TEXT, NOT_CHECKED, "its text could not be read"))
            elif has:
                out.append(row(DOC_TEXT, PASSED, "has text"))
            elif ext in _DOC_EXTS:
                out.append(row(DOC_TEXT, FAILED, "it has no text"))
            else:
                return []       # a web page that is only a picture: not a problem, and nothing to say
        elif ext == ".pdf":
            out += _pdf_check(p, name, reply, folder)
    except Exception:  # noqa: BLE001 — a check that breaks is "not checked", never a pass
        log.debug("a made file's content check broke", exc_info=True)
        out.append(row(PDF_PAGES if ext == ".pdf" else DOC_TEXT, NOT_CHECKED, BROKE))
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


def names_for(paths: "list[str]") -> "dict[str, tuple[str, str]]":
    """``{path: (the file's own name, its folder's name or "")}``. The folder
    is given ONLY for two files of the same name in two folders, so the
    record can tell them apart ("summary.xlsx (in 2023)"); two that would
    still read the same are numbered. One folder's own name, never a path."""
    bases = [file_name(p) for p in paths]
    out: dict[str, tuple[str, str]] = {}
    used: dict[tuple[str, str], int] = {}
    for path, base in zip(paths, bases):
        folder = ""
        if sum(1 for b in bases if b.lower() == base.lower()) > 1:
            folder = file_name(str(path).replace("\\", "/").rstrip("/").rpartition("/")[0]) or "another folder"
        key = (base.lower(), folder.lower())
        n = used[key] = used.get(key, 0) + 1
        out[path] = (base, folder if n == 1 else f"another folder, {n}")
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
        name, folder = names[path]
        if late():
            out.append(Check(FILE_OPENS, NOT_CHECKED, OUT_OF_TIME, file=name, folder=folder))
            continue
        try:
            out += one(path, reply, may_read=may_read, name=name, folder=folder, late=late)
        except Exception:  # noqa: BLE001 — a check that breaks is "not checked"
            log.debug("a made file could not be checked", exc_info=True)
            out.append(Check(FILE_OPENS, NOT_CHECKED, BROKE, file=name, folder=folder))
    rest = len(todo) - max(0, int(max_files))
    if rest > 0:
        out.append(Check(
            FILE_OPENS, NOT_CHECKED,
            f"{_plural(rest, 'more file')} {'was' if rest == 1 else 'were'} not checked: only the first "
            f"{max_files} are",
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
    return [Check(FILE_OPENS, NOT_CHECKED, why, file=names[p][0], folder=names[p][1])
            for p in todo[:max(1, int(max_files))]]


__all__ = [
    "ANOTHER_WAY", "BROKE", "DEADLINE_S", "HARD_WAIT_S", "MAX_BYTES", "MAX_CELLS", "MAX_FILES", "MAX_FORMULA_READS",
    "MAX_UNPACKED_BYTES", "MAX_WORKED", "NOT_ONE_LIST", "OUT_OF_TIME", "ROUNDER", "RUN_DEADLINE_S", "UNDER_TOTALS",
    "check_files", "check_made_files", "check_one", "names_for", "parse_sum", "readable", "stated_pages",
]
