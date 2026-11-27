"""What a made file SAYS, checked by rule (no model call, ever).

The app already checked that a required file exists and is not empty
(``deliverables/check``). Nothing looked inside it: a sheet full of
``#DIV/0!`` or a grand total that is not the sum of its rows went out under
"Done". These are the checks of the content.

EVERY PHRASE MUST BE LITERALLY TRUE, the good ones too (``checks/record``
holds the four outcomes and the allowlist of what may be a "Problem"):

* **it opens** (``file_opens``). Said ONLY of a kind Iron Jarvis really
  opens here, and only after it did: a workbook (.xlsx, .xlsm) its reader
  loaded, a Word document (.docx) its reader loaded, a PDF whose pages it
  counted, a text file whose whole text it read. A file of any other kind
  (.xls, .doc, a picture, slides, a kind it does not know) gets NO row.
  ``failed`` ("Problem") only for a fact about the file itself: a workbook
  or a Word document that is not a package or lacks its main part, a PDF
  that does not start as one or has no end (:func:`_pdf_checks`). A reader
  that fails for any other reason is ``not_checked``, and "opens" is not
  said.
* **a spreadsheet's error cells** (``sheet_no_error_cells``):
  - a cell whose SAVED value is an error (a value, or a formula's saved
    result) is ``failed``: "C7 holds #DIV/0!";
  - a formula with NO saved result is worked out by the app's own evaluator
    (:func:`_work_out`). One that comes to an error is ``look``: "D3 works
    out to an error" (the error is not named: the evaluator's can differ
    from Excel's). One that comes to ``#VALUE!`` (the evaluator is stricter
    than Excel about text in a cell) or that cannot be worked out is
    ``not_checked``, with the count;
  - a formula that names one of the newer functions MEASURED to open as
    ``#NAME?`` without Excel's prefix (:data:`_NAME_MEASURED`) is ``look``;
  - the good phrase is one of two, never a loose one: "no error cells" when
    every formula has a SAVED result and none is an error; "its 3 formulas
    work out" when Iron Jarvis worked formulas out itself and every formula
    of the workbook has a result that is not an error.
* **a spreadsheet's totals** (``sheet_totals``): see :func:`_totals`. A total
  is NEVER ``failed``. "adds up" is said only over the one shape of list
  (:func:`_list_above`), and only of the whole list.
* **a document has text** (``document_not_empty``): for a DOCUMENT kind
  (text, Markdown, a table of text, Word, a workbook, a PDF). An empty one
  ("it is empty") and one in which no text was found ("Iron Jarvis found no
  text in it") are ``look``, never a problem. A Word document's text is any
  word anywhere in its body, a text box, a content control, a header or a
  footer. A file that is not a document kind (code, settings, data, a web
  page) is never asked for text, and an empty or blank one gets no row.
* **a PDF's pages** (``pdf_pages``): how many it has, said as a fact ("12
  pages"). Nothing is compared with what the reply says.

RULES OF THE HOUSE. A check never raises: one that breaks is ``not_checked``
with a plain reason, never ``passed``. Only the files the caller hands over
are opened, and the callers hand over the files their own tools wrote (both
chat lanes' ``made_docs``, a run's undo journal), never a path read from a
model's words. Every path goes through the file policy the other readers use
(``core/fs_policy.fs_read_ok``), and a network spelling is refused by its
text first (``tools/base._network_spelling``).

BOUNDED. At most :data:`MAX_FILES` files a reply, a file over
:data:`MAX_BYTES` (or a package that unpacks past :data:`MAX_UNPACKED_BYTES`)
is not opened by any reader here and is ``not_checked``; a file under it is
read WHOLE before it is judged (never a cut piece). A workbook is loaded
ONCE (:func:`_load`) and its first :data:`MAX_CELLS` filled cells are read,
at most :data:`MAX_WORKED` formulas are worked out (each reading at most
:data:`MAX_FORMULA_READS` cells), and the whole reply's checks share
:data:`DEADLINE_S`, looked at between files AND inside a file (every row of
the cell walk, every formula, every total, every page): what is left when
it runs out is ``not_checked``. BLOCKING: :func:`check_made_files` is the
hop off the event loop, and it waits :data:`HARD_WAIT_S` at most.

A FILE'S NAME is all a check keeps of where it is, beside a digest of its
path (``Check.path_key``: a card matches a check to its file by it). Two
made files with the same name in two folders are told apart by the folder's
own name, kept in its own field (``Check.folder``). A CELL is named in plain
words: B4 on “Summary”, or "B4" alone in a workbook of one sheet. A name
somebody else chose (a sheet's, a file's) is shown quoted (``record.quoted``).

WORDS. Everything a check says can reach the line under a reply: plain
words only, never an exception's name or a package part's path
(``record.open_words``).
"""

from __future__ import annotations

import asyncio
import json
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
    file_name, open_words, path_key, quoted,
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
#: Total labels one workbook may hold, cells named in one finding.
MAX_TOTALS = 200
MAX_NAMED = 3
#: Pages of a PDF read for text at most.
MAX_PDF_TEXT_PAGES = 25
#: Formulas with no saved result that one workbook has worked out at most,
#: and the cells one of them may read on the way.
MAX_WORKED = 20_000
MAX_FORMULA_READS = 200_000

OUT_OF_TIME = "the check ran out of time"
BROKE = "it could not be checked here"
#: A reader of Iron Jarvis's own could not read the file. Not a fact about
#: the file (another program may open it): never a problem.
NOT_READ = "it could not be read here"
TEXT_NOT_READ = "its text could not be read here"
EMPTY = "it is empty"
NO_TEXT = "Iron Jarvis found no text in it"

_SHEET_EXTS = frozenset({".xlsx", ".xlsm"})
_WORD_EXTS = frozenset({".docx"})
_PDF_EXTS = frozenset({".pdf"})
#: The kinds that are a package of parts (a zip): the unpacked cap is theirs.
_PACKAGE_EXTS = _SHEET_EXTS | _WORD_EXTS
#: Text that is a DOCUMENT: it is asked for words.
_TEXT_DOC_EXTS = frozenset({".txt", ".md", ".markdown", ".csv", ".tsv"})
#: Text that is NOT a document (code, settings, data, a web page): read, so
#: "opens" is true of it, and never asked for words. One line break in an
#: ``__init__.py`` is what the file was meant to be.
_TEXT_OTHER_EXTS = frozenset({
    ".html", ".htm", ".xml", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".py", ".js",
    ".ts", ".tsx", ".jsx", ".css", ".sql", ".sh", ".ps1", ".bat",
})
_TEXT_EXTS = _TEXT_DOC_EXTS | _TEXT_OTHER_EXTS
#: EVERY kind Iron Jarvis really opens here. A file of any other kind (.xls,
#: .doc, a picture, slides, no extension at all) gets NO row: not "opens",
#: not "not checked".
OPENED_EXTS = _SHEET_EXTS | _WORD_EXTS | _PDF_EXTS | _TEXT_EXTS
#: The DOCUMENT kinds: an empty one, and one with no text, is worth a look.
_DOC_EXTS = _SHEET_EXTS | _WORD_EXTS | _PDF_EXTS | _TEXT_DOC_EXTS


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _fmt(value: float, places: int) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.{places}f}"


def _num(value: float) -> str:
    """A number as a person reads it: 1,250 or 1,250.40."""
    return _fmt(value, 2)


def _as_held(value: float) -> str:
    """A figure as the cell HOLDS it: every decimal place it was written
    with (0.0425 stays 0.0425, never 0.04), thousands grouped."""
    places = _decimals(value)
    return f"{int(round(value)):,}" if places == 0 else f"{value:,.{places}f}"


def _figures(total: float, added: float) -> "tuple[str, str]":
    """``(the total as its cell holds it, what the cells add up to)``. The
    sum is printed like money (two places when it is not whole) and with as
    many more as it takes to tell the two apart: two unequal figures never
    print as the same number."""
    said = _as_held(total)
    whole = abs(added - round(added)) < 1e-9
    for places in range(max(_decimals(total), 0 if whole else 2), 13):
        other = _fmt(added, places)
        if total == added or float(other.replace(",", "")) != float(said.replace(",", "")):
            return said, other
    return said, repr(float(added))


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
    """The file is NOT a valid file of its kind, as a fact read from the file
    itself (not a package, its main part missing); ``str(exc)`` is the plain
    reason. The only way a made file reads "Problem: it does not open"."""


class _NotRead(Exception):
    """Iron Jarvis's own reader could not read the file, for a reason that is
    not a fact about the file: ``not_checked``, and "opens" is not said."""


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
#: running balance, a growth, a return, a yield, a change, a variance, a
#: score or a rank anywhere in its heading; a number that names something
#: (an invoice number, an id, a year, a date) where the heading ENDS on the
#: word ("Year 1" is a column of amounts, "Tax year" is not).
_NOT_ADDED_RX = re.compile(
    r"%|\b(?:percent(?:age)?|pct|rates?|prices?|avg|average|mean|median|margins?|ratio|share|unit|per|hourly"
    r"|each|ytd|balance|running|cumulative|growth|returns?|yields?|change|variance|scores?|rank(?:ing)?)\b"
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
#: The errors a worked-out formula is said to come to ("works out to an
#: error": the error is NOT named, the evaluator's can differ from Excel's).
#: ``#VALUE!`` is not one of them: the app's evaluator is stricter than
#: Excel about text in a cell it reads (a dash, a date typed as text), so
#: that one is "not checked".
_LOOK_CODES = frozenset({"#DIV/0!", "#REF!", "#N/A", "#NUM!"})
WORKS_OUT_TO_ERROR = "works out to an error"
MAY_SHOW_NAME = "may show #NAME? when opened in Excel"
#: The newer functions MEASURED to open as ``#NAME?`` when a formula names
#: them without Excel's prefix (``documents/excel_formula._PREFIXED``'s own
#: comment: these five were written plainly, opened in Excel, and did).
#: Only these are said. Any other name on that table written without its
#: prefix was not measured: such a formula is "not worked out", no finding.
_NAME_MEASURED = frozenset({"XLOOKUP", "IFNA", "MAXIFS", "CONCAT", "DAYS"})

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
        #: The first number cell that holds no number ("" = none).
        self.void = ""
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
        """A cell in plain words: B4 on “Summary” (the sheet's name is
        somebody's words: quoted, ``record.quoted``), or "B4" alone when the
        workbook has one sheet. Never "Summary!B4"."""
        cell = f"{_col_letters(col)}{row}"
        return cell if len(self.sheets or self.formulas) <= 1 else f"{cell} on {quoted(sheet, 40)}"

    def known(self, sheet: str, row: int, col: int) -> "float | None":
        """A cell as a number Iron Jarvis KNOWS: a typed number, a formula's
        saved result, or what the evaluator worked out for a formula with no
        saved result. None for anything else: a blank, a word, a number that
        is not a number (NaN), and a formula nobody could work out (never a
        fallback figure, never 0)."""
        v = self.formulas.get(sheet, {}).get((row, col))
        if _is_number(v):
            return float(v) if math.isfinite(v) else None
        if _is_formula(v):
            for held in (self.values, self.worked):
                got = held.get(sheet, {}).get((row, col))
                if _is_number(got) and math.isfinite(got):
                    return float(got)
        return None


def _errors() -> "frozenset[str]":
    from ..documents.lint import _XL_ERRORS

    return _XL_ERRORS


def _load(path: Path, late: Callable[[], bool] = _never_late) -> _Book:
    """Open a workbook and read it, ONCE: the same load answers "does it
    open", gives every filled cell as written and each formula's saved
    result. The deadline (*late*) is looked at on every row of the walk: a
    long sheet stops there (:class:`_OutOfTime`), never at the end of the
    file. BLOCKING.

    :class:`_DoesNotOpen` ONLY for a fact about the file: it is not a package
    at all, or the package has no workbook part. Anything else the reader
    cannot do (a sheet of a kind it does not read, a part it trips over) is
    :class:`_NotRead`: Excel may open that file, so it is not called
    broken."""
    from openpyxl import load_workbook

    if not zipfile.is_zipfile(path):
        raise _DoesNotOpen(open_words("not a valid Office file"))
    try:
        with zipfile.ZipFile(path) as zf:
            has_workbook = "xl/workbook.xml" in set(zf.namelist())
    except Exception as exc:  # noqa: BLE001
        raise _NotRead() from exc
    if not has_workbook:
        raise _DoesNotOpen(open_words("its package has no workbook"))
    try:
        wb = load_workbook(str(path), read_only=True, data_only=False)
    except Exception as exc:  # noqa: BLE001 — the reader's failure, not a fact about the file
        log.debug("a made workbook could not be read", exc_info=True)
        raise _NotRead() from exc
    try:
        try:
            return _read_once(wb, late)
        except (ImportError, AttributeError, TypeError):
            # The one-pass read leans on the library's own cell parser. If a
            # later version moves it, read the slower way (two passes): the
            # answers are the same.
            log.debug("the one-pass read of a workbook is unavailable: reading it twice", exc_info=True)
            try:
                return _read_twice(wb, path, late)
            except _OutOfTime:
                raise
            except Exception as exc:  # noqa: BLE001
                raise _NotRead() from exc
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
            elif cell["data_type"] == "n" and cell["value"] is None and element.find(VALUE_TAG) is not None:
                # A number cell with a value element and nothing in it: how a
                # writer saves a number that is not one (NaN, infinity).
                cell["void"] = True
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
                    if cell.get("void") and not book.void:
                        book.void = book.place(ws.title, r, c)
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


def _plain_new_function(formula: str) -> str:
    """Does this formula name a newer function WITHOUT the prefix Excel
    stores it with? ``"measured"`` for one of the five measured to open as
    ``#NAME?`` (:data:`_NAME_MEASURED`), ``"other"`` for any other name on
    the writer's table (not measured: nothing is claimed about it), ``""``
    for none. The app's own writer adds the prefix
    (``excel_formula.for_file``); this is that function's own table and
    pattern, read without its side doors."""
    if "(" not in formula:
        return ""
    from ..documents.excel_formula import _FOR_FILE_RX, _PREFIXED

    kind = ""
    for m in _FOR_FILE_RX.finditer(formula):
        name = (m.group(1) or "").upper()
        if name in _NAME_MEASURED:
            return "measured"
        if name in _PREFIXED:
            kind = "other"
    return kind


def _work_out(book: _Book, late: Callable[[], bool] = _never_late) -> "tuple[list[tuple[str, int, int, str]], int]":
    """What Iron Jarvis works out about the formulas itself. Returns
    ``(findings worth a look, as (sheet, row, col, words); how many formulas
    could not be worked out)``:

    * a formula that names one of the MEASURED newer functions without
      Excel's prefix: "may show #NAME? when opened in Excel" (whatever
      result was saved). Another newer function without its prefix was not
      measured: with no saved result it counts as not worked out;
    * a formula with NO saved result, worked out by the app's own evaluator:
      one that comes to an error of :data:`_LOOK_CODES` "works out to an
      error" (never which one); one that comes to ``#VALUE!`` or any other
      error, a result that is not a finite number, or one the evaluator
      cannot do counts as not worked out. A result that was worked out is
      kept in ``book.worked``.

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
    doubted: set[tuple[str, int, int]] = set()
    try:
        for sheet, row, col in every:
            if late():
                raise _OutOfTime()
            v = book.formulas[sheet][(row, col)]
            kind = _plain_new_function(v) if isinstance(v, str) else ""
            if kind == "measured":
                named.add((sheet, row, col))
                found.append((sheet, row, col, MAY_SHOW_NAME))
            elif kind:
                doubted.add((sheet, row, col))
    except _OutOfTime:
        raise
    except Exception:  # noqa: BLE001 — the table could not be read: nothing is called clean
        log.debug("a workbook's function names could not be looked at", exc_info=True)
        return found, max(1, len(todo))
    # A newer function nobody measured, with no saved result: Excel may or
    # may not know it by that name, so the formula is not worked out here.
    unworked = sum(1 for k in todo if k in doubted)
    todo = [k for k in todo if k not in named and k not in doubted]
    if not todo:
        return found, unworked
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
                found.append((sheet, row, col, WORKS_OUT_TO_ERROR))
            else:
                unworked += 1   # #VALUE! and the like: the evaluator is stricter than Excel here
            continue
        except Exception:  # noqa: BLE001 — a formula this evaluator cannot do
            unworked += 1
            continue
        if isinstance(got, float) and not math.isfinite(got):
            unworked += 1   # not a number anybody can read: never a clean result
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
    * a figure that lies between the smallest and the largest of the rows,
      whatever their signs (an average, the last or a running value, the
      largest, a rank, a mean of temperatures);
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
    if min(vals) - 1e-9 <= total <= max(vals) + 1e-9:
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
    comes first. Used only to ask what KIND of column this is (a rate, a
    price: not added): the list a total is judged on is :func:`_list_above`."""
    out: list[tuple[int, Any]] = []
    r = row - 1
    while r >= 1 and len(out) < _SCAN_ROWS:
        v = cells.get((r, col))
        if _is_heading(v):
            return out, str(v), r
        out.append((r, v))
        r -= 1
    return out, "", 0


def _list_above(book: _Book, sheet: str, cells: dict, row: int, col: int) -> "tuple[list[tuple[int, float]], str]":
    """The list a total stands under: ``(its rows top first, each (row, the
    number Iron Jarvis knows), "")``, or ``([], why it cannot be read as one
    list)``.

    ONE shape, the same for a total that adds up and for one that differs:
    the unbroken run of cells DIRECTLY above the total, every one a number
    Iron Jarvis knows (:meth:`_Book.known`), ending right under a TEXT
    heading. A blank cell, a word ("TBD", "-"), a number typed as text or a
    date anywhere in it, a formula nobody could work out, and the top of the
    sheet in place of a heading each leave the total not judged: a total is
    never said to add up over part of a list."""
    place = book.place(sheet, row, col)
    run: list[tuple[int, float]] = []
    r = row - 1
    while r >= 1 and len(run) < _SCAN_ROWS:
        v = cells.get((r, col))
        if not (_is_number(v) or _is_formula(v)):
            break
        got = book.known(sheet, r, col)
        if got is None:
            return [], f"{place} sits under a cell this check could not work out"
        run.append((r, got))
        r -= 1
    if not run:
        return [], f"{place} has no numbers directly above it to add up"
    heading = cells.get((r, col)) if r >= 1 else None
    over = cells.get((r - 1, col)) if r > 1 else None
    if not _is_heading(heading) or _is_number(over) or _is_formula(over) or _is_text_number(over):
        # No text heading right above the list: a blank, a word in the middle
        # of a longer list of numbers, the top of the sheet.
        return [], f"{place} could not be added up: {NOT_ONE_LIST}"
    run.reverse()
    return run, ""


def _subtotal_rows(book: _Book, sheet: str, rows: "list[tuple[int, float]]", col: int) -> "tuple[set[int], set[int]]":
    """``(the rows that are LABELLED totals, the rows that are unlabelled
    subtotals)``, as indexes into *rows*. A row is an unlabelled subtotal
    ("Net amount", "Total before tax", "Goods") when it equals the sum of the
    two or more plain rows above it since the subtotal before it (or the
    heading)."""
    labelled = {i for i, (r, _x) in enumerate(rows) if book.total_row(sheet, r, col)}
    unlabelled: set[int] = set()
    group: list[float] = []
    for i, (_r, x) in enumerate(rows):
        if i in labelled:
            group = []
        elif len(group) >= 2 and _same(x, sum(group)):
            unlabelled.add(i)
            group = []
        else:
            group.append(x)
    return labelled, unlabelled


def _readings(rows: "list[tuple[int, float]]", subs: "set[int]") -> "tuple[float, float, float, float]":
    """What a list with subtotals at *subs* may add up to: ``(every plain
    row, the subtotals plus every row after the last one, the subtotals
    alone, the rows after the last subtotal)``. With no subtotal the four
    are the one sum."""
    vals = [x for _r, x in rows]
    plain = math.fsum(x for i, x in enumerate(vals) if i not in subs)
    if not subs:
        return plain, plain, plain, plain
    after = math.fsum(vals[max(subs) + 1:])
    alone = math.fsum(vals[i] for i in subs)
    return plain, alone + after, alone, after


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


def _other_reading(cells: dict, by_row: "dict[int, list[int]]", rows: "list[tuple[int, float]]", row: int,
                   label_col: int, total: float, lists: "Iterable[list[float]]") -> bool:
    """Is there a plain reason the total of a list is NOT the sum of its
    rows? Then the difference is not judged (it is not "worth a look"):

    * the total's label is not the first filled cell of its row (a long
      table whose rows are "North | Orders", "North | Total");
    * a row's own label says it is not added, or is taken away (a rate, a
      price, an average; a discount, a deposit paid, expenses);
    * the top row of the list is a second heading row of numbers;
    * another reading of the same rows gives the total (:func:`_explained`),
      tried on each of *lists* (every plain row; and, where the list has
      subtotals, the subtotals with the rows after the last one)."""
    if any((row, c) in cells for c in range(1, label_col)):
        return True
    for r, _x in rows:
        label = cells.get((r, label_col))
        if isinstance(label, str) and (_NOT_ADDED_RX.search(label) or _ROW_WORDS_RX.search(label)):
            return True
    if _numeric_heading_row(cells, by_row, rows[0][0], label_col):
        return True
    return any(_explained(total, list(vals)) for vals in lists if vals)


def _row_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int, label_col: int,
               by_row: "dict[int, list[int]] | None" = None) -> "tuple[str, str] | None":
    """One figure of a total row, judged. None = it is not a total to add (a
    percent, a column headed rate / price / balance / growth / invoice no ...).

    Every word of the answer must be literally true, the good one too. A
    total is judged ONLY over the one shape of list (:func:`_list_above`).

    ``passed`` ("adds up"): the figure equals the sum of ALL the plain rows
    of that list or, where the list has subtotals, the subtotals PLUS every
    row after the last one. A SUBTOTAL label may also be the rows since the
    subtotal before it, or the subtotals alone; a Total or a Grand total
    never (a total that forgot the tax row equals the subtotal: that is not
    "adds up"). An unlabelled subtotal (:func:`_subtotal_rows`) counts only
    when rows follow it.

    ``look``: a TYPED figure that fits none of that, over rows that are each
    a typed number with a text label (two or more), when the list has no
    unlabelled subtotal with rows after it, the figure is not written rounder than its rows
    (:func:`_rounder`) and no other plain reading explains it
    (:func:`_other_reading`). A figure that equals a PARTIAL sum (the
    subtotals alone, the rows after the last subtotal) is said at once: both
    figures, no verdict. NEVER ``failed``.

    ``not_checked``: everything else, and a formula total that is not a
    plain ``=SUM`` of exactly the list above it (:func:`_formula_total`)."""
    place = book.place(sheet, row, col)
    v = cells.get((row, col))
    if (sheet, row, col) in book.percent:
        return None
    _cells_above, heading, _heading_row = _above(cells, row, col)
    if heading and _NOT_ADDED_RX.search(heading):
        return None
    rows, why = _list_above(book, sheet, cells, row, col)
    if _is_formula(v):
        return _formula_total(book, sheet, cells, saved, row, col, label_col, rows, why)
    if not rows:
        return NOT_CHECKED, why
    total = float(v)
    if not math.isfinite(total):
        return NOT_CHECKED, f"{place} could not be added up: {NOT_ONE_LIST}"
    is_subtotal = _label_words(cells.get((row, label_col))) in _SUBTOTAL_WORDS
    labelled, unlabelled = _subtotal_rows(book, sheet, rows, col)
    every, with_subs, alone, after = _readings(rows, labelled)
    fits = [every, with_subs]
    both = labelled | unlabelled
    # An unlabelled subtotal is one only when rows FOLLOW it ("Net amount",
    # then the tax, then the total). A last row that happens to equal the
    # rows above it (100, 200, 300) is a row like any other.
    two_ways = bool(unlabelled) and max(both) < len(rows) - 1
    if two_ways:
        fits += list(_readings(rows, both)[:2])
    if is_subtotal:
        fits += [alone, after]
    if any(_same(total, x) for x in fits):
        return PASSED, ""
    # It differs. Said only over a list plain enough to judge: one that
    # reads two ways (with and without an unlabelled subtotal) is not.
    if two_ways:
        return NOT_CHECKED, f"{place} {ANOTHER_WAY}"
    for r, _x in rows:
        label = cells.get((r, label_col))
        if not _is_number(cells.get((r, col))) or not (
                isinstance(label, str) and label.strip() and not _is_formula(label)):
            return NOT_CHECKED, f"{place} could not be added up: {NOT_ONE_LIST}"
    if len(rows) < 2:
        return NOT_CHECKED, f"{place} could not be added up: {NOT_ONE_LIST}"
    said, added = _figures(total, with_subs)
    differs = f"{place} says {said} and the cells above it add up to {added}"
    if labelled and not is_subtotal and (_same(total, alone) or _same(total, after)):
        # A total that equals only PART of its list (the subtotal, with the
        # tax row left out): said at once, before any other reading is tried.
        return LOOK, differs
    plain = [x for i, (_r, x) in enumerate(rows) if i not in labelled]
    if _rounder(total, plain):
        return NOT_CHECKED, f"{place} {ROUNDER}"
    reduced = [x for i, (_r, x) in enumerate(rows) if i in labelled or i > max(labelled)] if labelled else []
    if _other_reading(cells, by_row or {}, rows, row, label_col, total, (plain, reduced)):
        return NOT_CHECKED, f"{place} {ANOTHER_WAY}"
    return LOOK, differs


def _formula_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int, label_col: int,
                   rows: "list[tuple[int, float]]", why: str) -> "tuple[str, str]":
    """A total written as a formula. It "adds up" ONLY when it is a plain
    ``=SUM`` of one range and that range is exactly the list above it
    (:func:`_list_above`; for a subtotal, the rows since the total row
    before it), every row of it a number Iron Jarvis knows. A range that
    leaves a row out or takes one in, any other formula, and a saved result
    that is not what the cells add up to are ``not_checked``; a formula total
    is never ``failed`` and never ``look`` (a spreadsheet program works it
    out again when it opens the file)."""
    place = book.place(sheet, row, col)
    ref = _one_sum_range(cells.get((row, col)))
    if ref is None:
        return NOT_CHECKED, f"{place} is worked out by a formula this check cannot follow"
    if not rows:
        return NOT_CHECKED, why
    block = rows
    labelled = [i for i, (r, _x) in enumerate(rows) if book.total_row(sheet, r, col)]
    if labelled:
        if _label_words(cells.get((row, label_col))) not in _SUBTOTAL_WORDS:
            return NOT_CHECKED, f"{place} {UNDER_TOTALS}"
        block = rows[max(labelled) + 1:]
        if not block:
            return NOT_CHECKED, f"{place} could not be added up: {NOT_ONE_LIST}"
    ref_sheet, r1, c1, r2, c2 = ref
    top, bottom = block[0][0], block[-1][0]
    letter = _col_letters(col)
    if (ref_sheet and ref_sheet.lower() != sheet.lower()) or (c1, c2) != (col, col) or (r1, r2) != (top, bottom):
        return NOT_CHECKED, (
            f"{place} adds {_col_letters(c1)}{r1} to {_col_letters(c2)}{r2}, and the list above it runs from "
            f"{letter}{top} to {letter}{bottom}"
        )
    added = math.fsum(x for _r, x in block)
    last = saved.get((row, col))
    if _is_number(last) and not _same(float(last), added):
        was, now = _figures(float(last), added)
        return NOT_CHECKED, (
            f"{place} has a saved result of {was} and its cells add up to {now}: "
            "a spreadsheet program works it out again when it opens the file"
        )
    return PASSED, ""


def _column_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int) -> "tuple[str, str] | None":
    """A column headed by a total word, judged as ONE total. None = nothing
    is said. Each row's figure must be the SUM of the numbers to its left; a
    formula counts only as a plain ``=SUM`` of exactly those cells. Nothing
    is said at all when a column to its left is headed as a kind that is not
    added (``Qty | Price | Total``: a price, a rate): such a row is a
    product or something else, and "adds up" would not be true of it. A
    column that is not the sums is ``not_checked``: never ``failed`` or
    ``look``."""
    if col < 3 or not any(cells.get((row, c)) is not None for c in range(1, col)):
        return None
    for c in range(1, col):
        head = cells.get((row, c))
        if isinstance(head, str) and not _is_formula(head) and _NOT_ADDED_RX.search(head):
            return None
    place = f"the {quoted(str(cells.get((row, col))).strip())} column" + (
        "" if len(book.sheets or book.formulas) <= 1 else f" on {quoted(sheet)}")
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
        if len(beside) < 2:
            return None  # nothing beside it to add: a column of figures, not of totals
        left = [book.known(sheet, r, c) for c in beside]
        mine = book.known(sheet, r, col)
        if mine is None or any(x is None for x in left):
            return NOT_CHECKED, f"{place} sits beside a cell this check could not work out"
        if not _same(mine, math.fsum(float(x) for x in left if x is not None)):
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


def _formula_counts(book: _Book) -> "tuple[int, int]":
    """``(how many formulas the workbook holds, how many of them have no
    saved result)``."""
    every = unsaved = 0
    for sheet, cells in book.formulas.items():
        saved = book.values.get(sheet, {})
        for key, v in cells.items():
            if _is_formula(v) or (not isinstance(v, (str, int, float, bool)) and hasattr(v, "ref")):
                every += 1
                if saved.get(key) is None:
                    unsaved += 1
    return every, unsaved


def _not_a_number(book: _Book) -> str:
    """The first cell that holds a number that is not one (NaN, infinity:
    read as such, or saved as a number cell with nothing in it), in plain
    words; "" for none."""
    if book.void:
        return book.void
    for sheet, cells in book.formulas.items():
        for (row, col), v in cells.items():
            if isinstance(v, float) and not math.isfinite(v):
                return book.place(sheet, row, col)
    return ""


def _sheet_checks(path: Path, name: str, late: Callable[[], bool] = _never_late, folder: str = "") -> list[Check]:
    """Everything about a workbook from ONE load: whether it opens, its
    error cells and its totals. Where the reply's deadline runs out, what
    was not reached is ``not_checked`` and what was is kept."""
    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path))

    try:
        book = _load(path, late)
    except _DoesNotOpen as exc:
        return [row(FILE_OPENS, FAILED, str(exc) or open_words(""))]
    except _NotRead:
        return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
    except _OutOfTime:
        return [row(FILE_OPENS, PASSED, "opens"), row(SHEET_ERRORS, NOT_CHECKED, OUT_OF_TIME)]
    out = [row(FILE_OPENS, PASSED, "opens")]
    if not any(book.formulas.values()):
        # A workbook with no filled cell: a document that is empty. Worth a
        # look (it may be what was asked for), never a problem.
        return out + [row(DOC_TEXT, LOOK, "it has no filled cells")]
    # WHAT THE FILE HOLDS: a saved error value is a problem. WHAT IRON JARVIS
    # WORKS OUT (a formula with no saved result, a function measured to open
    # as #NAME?) is worth a look, never a problem. The good phrase is one of
    # two true ones: "no error cells" when every formula has a SAVED result,
    # "its N formulas work out" when Iron Jarvis worked some out itself. A
    # workbook read only in part is not worked out (a formula over half a
    # column would come to the wrong number).
    held = _error_cells(book)
    odd = _not_a_number(book)
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
            out.append(row(SHEET_ERRORS, NOT_CHECKED, f"only its first {MAX_CELLS:,} cells were read"))
    elif timed_out:
        out.append(row(SHEET_ERRORS, NOT_CHECKED, OUT_OF_TIME))
    elif odd:
        out.append(row(SHEET_ERRORS, NOT_CHECKED, f"{odd} holds a number this check cannot read"))
    elif unworked:
        out.append(row(
            SHEET_ERRORS, NOT_CHECKED,
            f"{_plural(unworked, 'formula')} {'has' if unworked == 1 else 'have'} no saved result and could not "
            "be worked out here",
        ))
    elif not held and not found:
        formulas, unsaved = _formula_counts(book)
        if not unsaved:
            out.append(row(SHEET_ERRORS, PASSED, "no error cells"))
        else:
            out.append(row(SHEET_ERRORS, PASSED,
                           "its formula works out" if formulas == 1 else f"its {formulas:,} formulas work out"))
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
# A Word document, a PDF, a text file
# --------------------------------------------------------------------------- #
#: A run of text anywhere in a Word part: the body, a table, a text box, a
#: content control, a header, a footer.
_WORD_TEXT_RX = re.compile(rb"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")
_WORD_PART_RX = re.compile(r"word/(?:document|header\d*|footer\d*)\.xml")


def _docx_checks(path: Path, name: str, folder: str = "") -> list[Check]:
    """A Word document. A PROBLEM only for a fact about the file: it is not
    a package, or the package has no document part. "opens" only after Iron
    Jarvis's own reader loaded it. Its text is ANY word in the body, a text
    box, a content control, a header or a footer; none found is worth a
    look, never a problem."""
    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path))

    if not zipfile.is_zipfile(path):
        return [row(FILE_OPENS, FAILED, open_words("not a valid Office file"))]
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "word/document.xml" not in names:
                return [row(FILE_OPENS, FAILED, open_words("its package has no document"))]
            has_text = any(
                m.group(1).strip()
                for part in sorted(n for n in names if _WORD_PART_RX.fullmatch(n))
                for m in _WORD_TEXT_RX.finditer(zf.read(part))
            )
        import docx

        docx.Document(str(path))
    except Exception:  # noqa: BLE001 — the reader's failure, not a fact about the file
        log.debug("a made Word document could not be read", exc_info=True)
        return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
    return [row(FILE_OPENS, PASSED, "opens"),
            row(DOC_TEXT, PASSED, "has text") if has_text else row(DOC_TEXT, LOOK, NO_TEXT)]


def _pdf_checks(path: Path, name: str, folder: str = "", late: Callable[[], bool] = _never_late) -> list[Check]:
    """A PDF. A PROBLEM only for a fact about the file: it does not start as
    a PDF, or it has no end mark anywhere (it was cut off). "opens" only
    after Iron Jarvis's own reader counted its pages; the count is said as a
    fact ("12 pages") and compared with nothing. Text is looked for on the
    first :data:`MAX_PDF_TEXT_PAGES` pages while the deadline lasts: found is
    "has text"; none found on EVERY page is worth a look; a PDF read only in
    part says nothing about its text."""
    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path))

    data = path.read_bytes()        # the whole file (it is under the size cap)
    if b"%PDF-" not in data[:1024]:
        return [row(FILE_OPENS, FAILED, open_words("it does not start like a PDF file"))]
    if b"%%EOF" not in data:
        return [row(FILE_OPENS, FAILED, open_words("the PDF has no end marker (it was cut off)"))]
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return [row(FILE_OPENS, NOT_CHECKED, "it is locked with a password")]
        pages = len(reader.pages)
    except Exception:  # noqa: BLE001 — the reader's failure, not a fact about the file
        log.debug("a made PDF could not be read", exc_info=True)
        return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
    out = [row(FILE_OPENS, PASSED, "opens"), row(PDF_PAGES, PASSED, _plural(pages, "page"))]
    if pages == 0:
        return out[:1] + [row(DOC_TEXT, LOOK, "it has no pages")]
    try:
        read = 0
        for page in reader.pages:
            if read >= MAX_PDF_TEXT_PAGES or late():
                break
            read += 1
            if (page.extract_text() or "").strip():
                out.append(row(DOC_TEXT, PASSED, "has text"))
                return out
        if read == pages:
            out.append(row(DOC_TEXT, LOOK, NO_TEXT))
    except Exception:  # noqa: BLE001 — its text could not be read: nothing is said about it
        log.debug("a made PDF's text could not be read", exc_info=True)
    return out


def _decoded(data: bytes) -> "str | None":
    """A text file's WHOLE text, through the one decoder the document
    readers use (``documents/readers._decode_bytes``: UTF-8, UTF-16, then
    the Windows code page a file saved by an older program is in). None when
    what comes out is not text (it holds NUL characters: a binary file)."""
    from ..documents.readers import _decode_bytes

    text = _decode_bytes(data)
    return None if "\x00" in text else text


def _text_checks(path: Path, ext: str, name: str, folder: str = "") -> list[Check]:
    """A text file, read WHOLE (never a cut piece) and decoded. A DOCUMENT
    kind is asked for words; code, settings and data are not, and a blank
    one of those gets no row. A ``.json`` file that Iron Jarvis's parser
    cannot read (comments, one object a line) is ``not_checked``: never a
    problem."""
    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path))

    text = _decoded(path.read_bytes())
    if text is None:
        return [row(FILE_OPENS, NOT_CHECKED, TEXT_NOT_READ)]
    blank = not text.strip()
    if ext in _TEXT_DOC_EXTS:
        return [row(FILE_OPENS, PASSED, "opens"),
                row(DOC_TEXT, LOOK, NO_TEXT) if blank else row(DOC_TEXT, PASSED, "has text")]
    if blank:
        return []
    if ext == ".json":
        try:
            json.loads(text)
        except ValueError:
            return [row(FILE_OPENS, NOT_CHECKED, "it could not be read as JSON here")]
    return [row(FILE_OPENS, PASSED, "opens")]


# --------------------------------------------------------------------------- #
# One file, then a reply's files
# --------------------------------------------------------------------------- #
def check_one(path: Any, reply: str = "", *, may_read: Callable[[Any], bool] = readable,
              name: str = "", folder: str = "", late: Callable[[], bool] = _never_late) -> list[Check]:
    """Every content check of ONE made file. A file of a kind Iron Jarvis
    does not open here (:data:`OPENED_EXTS`) is checked by nothing and says
    nothing; so is a file that is no longer there, and an empty file that is
    not a document. *name* is the file's own name; *folder* its folder's
    name, only when two made files share one name; *late* answers "has the
    reply's deadline run out?" and is asked inside the file. *reply* is not
    read (nothing a reply says is compared with a file). BLOCKING; may raise
    (the caller turns that into ``not_checked``)."""
    name = name or file_name(path)
    p = Path(str(path))
    ext = p.suffix.lower()
    if ext not in OPENED_EXTS:
        return []       # nothing here opens this kind: no row, and never "opens"

    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path))

    if not may_read(path):
        return [row(FILE_OPENS, NOT_CHECKED, "it is outside the folders Iron Jarvis may read")]
    try:
        if not p.exists() or p.is_dir():
            return []
        size = p.stat().st_size
    except OSError:
        return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
    if size == 0:
        # An empty DOCUMENT is worth a look (it may be what was asked for).
        # An empty file of any other kind is what it was meant to be: no row.
        return [row(DOC_TEXT, LOOK, EMPTY)] if ext in _DOC_EXTS else []
    if size > MAX_BYTES:
        return [row(FILE_OPENS, NOT_CHECKED, f"it is too large to check here ({_size_words(size)})")]
    # An Office file is a package: one that unpacks past the cap is not
    # opened by ANY reader below.
    if ext in _PACKAGE_EXTS and zipfile.is_zipfile(p) and _unpacked_too_large(p):
        return [row(FILE_OPENS, NOT_CHECKED, "it unpacks too large to check here")]
    try:
        if ext in _SHEET_EXTS:
            return _sheet_checks(p, name, late, folder)
        if ext in _WORD_EXTS:
            return _docx_checks(p, name, folder)
        if ext in _PDF_EXTS:
            return _pdf_checks(p, name, folder, late)
        return _text_checks(p, ext, name, folder)
    except Exception:  # noqa: BLE001 — a check that breaks is "not checked": never a pass, never "opens"
        log.debug("a made file's content check broke", exc_info=True)
        return [row(FILE_OPENS, NOT_CHECKED, BROKE)]


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


def opened_kind(path: Any) -> bool:
    """Is this a kind of file Iron Jarvis really opens here
    (:data:`OPENED_EXTS`)? A file of any other kind is checked by nothing,
    and nothing is said about it: not on the line, not as "not checked"."""
    return os.path.splitext(str(path or ""))[1].lower() in OPENED_EXTS


def _opened_kinds(paths: "list[str]") -> "list[str]":
    return [p for p in paths if opened_kind(p)]


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
    todo = _opened_kinds(_unique(paths))
    names = names_for(todo)
    out: list[Check] = []
    started = clock()

    def late() -> bool:
        return clock() - started > deadline_s

    for path in todo[:max(0, int(max_files))]:
        name, folder = names[path]
        if late():
            out.append(Check(FILE_OPENS, NOT_CHECKED, OUT_OF_TIME, file=name, folder=folder,
                             path_key=path_key(path)))
            continue
        try:
            out += one(path, reply, may_read=may_read, name=name, folder=folder, late=late)
        except Exception:  # noqa: BLE001 — a check that breaks is "not checked"
            log.debug("a made file could not be checked", exc_info=True)
            out.append(Check(FILE_OPENS, NOT_CHECKED, BROKE, file=name, folder=folder, path_key=path_key(path)))
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
    todo = _opened_kinds(_unique(paths))
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
    return [Check(FILE_OPENS, NOT_CHECKED, why, file=names[p][0], folder=names[p][1], path_key=path_key(p))
            for p in todo[:max(1, int(max_files))]]


__all__ = [
    "ANOTHER_WAY", "BROKE", "DEADLINE_S", "HARD_WAIT_S", "MAX_BYTES", "MAX_CELLS", "MAX_FILES", "MAX_FORMULA_READS",
    "MAX_UNPACKED_BYTES", "MAX_WORKED", "NOT_ONE_LIST", "OUT_OF_TIME", "ROUNDER", "RUN_DEADLINE_S", "UNDER_TOTALS",
    "EMPTY", "NOT_READ", "NO_TEXT", "OPENED_EXTS", "TEXT_NOT_READ",
    "check_files", "check_made_files", "check_one", "names_for", "opened_kind", "parse_sum", "readable",
]
