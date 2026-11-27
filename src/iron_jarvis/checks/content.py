"""What a made file SAYS, checked by rule (no model call, ever).

The app already checked that a required file exists and is not empty
(``deliverables/check``). Nothing looked inside it: a sheet full of
``#DIV/0!`` or a grand total that is not the sum of its rows went out under
"Done". These are the checks of the content.

EVERY PHRASE MUST BE LITERALLY TRUE, the good ones too (``checks/record``
holds the four outcomes and the allowlist of what may be a "Problem"):

* **it opens** (``file_opens``). Said ONLY of a kind Iron Jarvis really
  opens here, and only after the reader the app's own tools use DID open
  it: openpyxl loaded the workbook (.xlsx, .xlsm), python-docx loaded the
  Word document (.docx), the PDF reader counted the PDF's pages, the text
  decoder read a text file whole. A file of any other kind (.xls, .doc, a
  picture, slides, a kind it does not know) gets NO row. ``failed``
  ("Problem") is that reader's own failure and says only that: "Iron Jarvis
  could not open “letter.docx”". Nothing here decides that a file is broken
  by looking for parts by their names (``word/document2.xml`` and
  ``xl/workbook2.xml`` are valid files). A file that is protected with a
  password is ``not_checked``, never a problem. THE DISK IS ASKED FIRST
  (:func:`_disk_gives`): a file it does not hand over (another program
  holds it, a permission, it went away) is ``not_checked`` for every kind,
  whatever a reader would have answered.
* **a spreadsheet's error cells** (``sheet_no_error_cells``):
  - a cell whose STORED type is an error (``t="e"`` in the file: an error
    value, or a formula's saved result of that type) is ``failed``, EVERY
    such cell: "C7 holds #DIV/0!" for one of the seven errors everybody
    knows, "C7 holds an error" for any other (never the cell's own text).
    A text cell that reads "#N/A" is text. A NUMBER under a date format
    that the reader calls "#VALUE!" (it cannot make a date of it) is no
    error cell: ``not_checked``, "B2 holds a date this check cannot read";
  - a workbook that lists a sheet the reader did not load is not read
    through: ``not_checked`` ("not all of its sheets could be read here"),
    and its formulas are not worked out;
  - a formula with NO saved result is worked out by the app's own evaluator
    (:func:`_work_out`). So is every formula of a workbook that says its
    saved results are not to be trusted (it asks for a full recalculation
    on load: a writer that saves a placeholder 0 for every formula does).
    One that comes to an error is ``look``: "D3 works out to an error" (the
    error is not named: the evaluator's can differ from Excel's). One that
    comes to ``#VALUE!`` (the evaluator is stricter than Excel about text
    in a cell) or that cannot be worked out is ``not_checked``, with the
    count;
  - a formula that names one of the newer functions MEASURED to open as
    ``#NAME?`` without Excel's prefix (:data:`_NAME_MEASURED`) is ``look``;
  - the good phrase is one of two, never a loose one: "no error cells" when
    every formula has a SAVED result the file stands by and none is an
    error; "its 3 formulas work out" when Iron Jarvis worked formulas out
    itself and every formula of the workbook has a result that is not an
    error.
* **a spreadsheet's totals** (``sheet_totals``): see :func:`_totals`. ONE
  shape is judged (:func:`_row_total`); "adds up" means exactly its
  arithmetic, compared in DECIMAL with no slack (:func:`_adds_up`); every
  other total gets NO row. A total is NEVER ``failed``. One that differs is
  said with both figures printed exactly (:func:`_differs`).
* **a document has text** (``document_not_empty``): for a DOCUMENT kind
  (text, Markdown, a table of text, Word, a workbook, a PDF). An empty one
  ("it is empty") and one in which no text was found ("Iron Jarvis found no
  text in it") are ``look``, never a problem. "Found no text" and "no
  filled cells" are said only when the app's own reader read the file and
  found none AND nothing else in the package holds text or cells: a Word
  file whose words sit where the reader does not read (a text box, another
  prefix) and a workbook whose cells the reader does not see (Strict Open
  XML) are ``not_checked``. Characters nobody sees (zero-width ones, a
  byte-order mark) are not text. A workbook whose only cells hold blank
  space says that ("its cells hold only blank space"). A file that is not
  a document kind (code, settings, data, a web page) is never asked for
  text, and an empty or blank one gets no row.
* **a PDF's pages** (``pdf_pages``): how many it has, said as a fact ("12
  pages") ONLY when the pages were walked to the end and are as many as
  the file states. Nothing is compared with what the reply says.

THE RULE BEHIND ALL OF IT: a phrase is said only where it is true by
construction for EVERY file. Where it would take a guess about what the
file's author meant, the answer is no row at all.

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
path (``Check.path_key``: a card matches a check to its file by it) and the
file's size and last-written time when it was checked (``Check.stamp``: a
row is shown only while its file still has them). Two
made files with the same name in two folders are told apart by the folder's
own name, kept in its own field (``Check.folder``). A CELL is named in plain
words: B4 on “Summary”, or "B4" alone in a workbook of one sheet. A name
somebody else chose (a sheet's, a file's) is shown quoted (``record.quoted``).

WORDS. Everything a check says can reach the line under a reply: plain
words only, never an exception's name or a package part's path. A FINDING
IS NEVER CUT: findings that do not fit the record's length are dropped
whole and counted (:func:`_named`).
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
from bisect import bisect_left, insort
from dataclasses import replace
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, Inexact, InvalidOperation, Overflow
from itertools import product as _signs
from pathlib import Path
from typing import Any, Callable, Iterable

from .record import (
    DETAIL_MAX, DOC_TEXT, FAILED, FILE_OPENS, LOOK, NOT_CHECKED, PASSED, PDF_PAGES, SHEET_ERRORS, SHEET_TOTALS,
    Check, file_name, file_stamp, path_key, quoted,
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
CELLS_NOT_READ = "its cells could not be read here"
EMPTY = "it is empty"
NO_TEXT = "Iron Jarvis found no text in it"
NO_CELLS = "it has no filled cells"
#: A workbook whose only cells hold blank space (the reader saw them).
ONLY_SPACES = "its cells hold only blank space"
#: The workbook lists a sheet the reader did not load (its part is missing).
SHEETS_NOT_READ = "not all of its sheets could be read here"
#: An Office file that is not a package but the older container: what a
#: file protected with a password is saved as. Said for certain only when
#: the container names its encrypted part; else as what it MAY be.
PROTECTED = "it is protected with a password"
MAY_BE_PROTECTED = "it may be protected with a password, or be an older kind of Office file"
_ENCRYPTED_PART = "EncryptedPackage".encode("utf-16-le")


def could_not_open(name: Any) -> str:
    """The one sentence of a file Iron Jarvis's reader failed to open: the
    literal fact, with the file's own name quoted. Never a reason (a guess
    about the file), never an exception's name."""
    return f"Iron Jarvis could not open {quoted(file_name(name)) or 'it'}"

_SHEET_EXTS = frozenset({".xlsx", ".xlsm"})
_WORD_EXTS = frozenset({".docx"})
_PDF_EXTS = frozenset({".pdf"})
#: The kinds that are a package of parts (a zip): the unpacked cap is theirs.
_PACKAGE_EXTS = _SHEET_EXTS | _WORD_EXTS
#: Text that is a DOCUMENT: it is asked for words.
_TEXT_DOC_EXTS = frozenset({".txt", ".md", ".markdown", ".csv", ".tsv"})
#: A table of text, and what one holds that is not text: blank space, its
#: separators and its quote marks.
_TABLE_EXTS = frozenset({".csv", ".tsv"})
_TABLE_BLANKS = " \t\r\n\f\v,;|\"'﻿"
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


#: Exact decimal arithmetic for a total: wide enough to hold the sum of any
#: cells a workbook can hold without rounding it; anything it would have to
#: round raises (and the total is then not judged).
_EXACT = Context(prec=1_000, traps=[Inexact, InvalidOperation, Overflow])
#: A figure with more digits than this is not printed (nobody reads it):
#: the finding then says only that the two differ.
MAX_FIGURE_DIGITS = 15


def _dec(value: Any) -> Decimal:
    """A typed number EXACTLY as its cell holds it, in decimal: a whole
    number as it is, any other by the shortest text that reads back as the
    same number (what a spreadsheet shows for it and writes to the file)."""
    return Decimal(value) if isinstance(value, int) else Decimal(repr(float(value)))


def _sum_exact(vals: "Iterable[Any]") -> "Decimal | None":
    """The cells added up exactly, in decimal (0.1 + 0.2 is 0.3; nothing is
    lost to a computer's own arithmetic, however many cells or however
    large). None when it cannot be held exactly."""
    try:
        total = Decimal(0)
        for v in vals:
            total = _EXACT.add(total, _dec(v))
        return total
    except ArithmeticError:
        return None


def _in_full(value: Decimal) -> str:
    """A figure in full: every digit it has, thousands grouped, never
    rounded and never with an exponent. "" for one too long to print
    (:data:`MAX_FIGURE_DIGITS`)."""
    if value == 0:
        return "0"
    value = value.normalize()
    if len(value.as_tuple().digits) > MAX_FIGURE_DIGITS or abs(value.adjusted()) > MAX_FIGURE_DIGITS:
        return ""
    return f"{value:,f}"


def _differs(place: str, total: Decimal, added: Decimal) -> str:
    """A typed total that is not the sum of its list, said with no verdict:
    both figures EXACTLY (the total as its cell holds it, the sum as the
    cells add up, no place rounded away), or, when one of them is too long
    to print, only that the two differ."""
    said, shown = _in_full(total), _in_full(added)
    if said and shown:
        return f"{place} says {said} and the cells above it add up to {shown}"
    return f"{place} differs from what the cells above it add up to"


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
    """The reader the app's own tools use failed to open the file. The only
    way a made file reads "Problem": "Iron Jarvis could not open “x.xlsx”"."""


class _Protected(Exception):
    """The file is the older Office container, not a package (what a file
    protected with a password is): ``not_checked``; ``str(exc)`` says why."""


class _NotRead(Exception):
    """The file was opened (or the disk refused it) and could not be read
    through: ``not_checked``, and "opens" is not said."""


def _never_late() -> bool:
    return False


def _disk_refused(exc: BaseException) -> bool:
    """Did the DISK refuse the file (another program holds it, a permission,
    it went away): an operating-system error with its own number? A reader
    that says "this is no workbook" with the same class of error (no
    number) is the reader's failure, not the disk's."""
    return isinstance(exc, OSError) and exc.errno is not None


#: A cell that labels a total: the WHOLE cell, lower-cased, a trailing colon
#: or period dropped. "Total revenue", "Total Wine & More" and "Totals by
#: region" are not labels: a figure beside them is not judged at all.
#: English first; the plain word in the six other languages the claim
#: readings know.
_TOTAL_WORDS = frozenset({
    "subtotal", "subtotals", "sub-total", "sub-totals", "sub total", "sub totals",
    "total", "totals", "grand total", "grand totals", "sum",
    "totale", "totaal", "summe", "gesamt", "gesamtsumme", "suma", "somme", "somma",
})
#: A word, anywhere in a cell of a list's row, that reads like a total of
#: other rows ("Subtotal", "Net amount", "Total before tax", "Gross pay"):
#: the list then holds a subtotal, and its total is not judged at all.
_SUBTOTAL_LIKE_RX = re.compile(
    r"\b(?:sub[\s-]?totals?|totals?|net|gross|sum|summe|gesamt\w*|totale|totaal|suma|somme|somma)\b",
    re.IGNORECASE,
)
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
    formula's own text) and the result a spreadsheet program last SAVED for
    each formula (``values``: formula cells only; absent where nothing was
    saved, and EMPTY for a workbook that says its saved results are not to
    be trusted, :func:`_recalculates`). ``worked`` is what the app's own
    evaluator came to for each formula it worked out (kept for the tests of
    the evaluator: no check reads it). ``errors`` are the cells whose TYPE
    is an error (an error value, or a formula's trusted saved result of
    that type), each with the error it holds; ``spaces`` the cells that
    hold only blank space (text to a formula, nothing to a reader);
    ``sheets`` every sheet's name."""

    def __init__(self) -> None:
        self.formulas: dict[str, dict[tuple[int, int], Any]] = {}
        self.values: dict[str, dict[tuple[int, int], Any]] = {}
        self.worked: dict[str, dict[tuple[int, int], Any]] = {}
        self.errors: dict[str, dict[tuple[int, int], str]] = {}
        self.percent: set[tuple[str, int, int]] = set()
        self.spaces: set[tuple[str, int, int]] = set()
        self.sheets: list[str] = []
        self.cut = False
        #: The first number cell that holds no number ("" = none).
        self.void = ""
        #: The first cell the reader calls an error ONLY because it is a
        #: number under a date format that is no date it can read ("" =
        #: none). The file stores a number there: it is no error cell.
        self.undated = ""
        #: The workbook lists a sheet the reader did not load.
        self.partial = False

    def place(self, sheet: str, row: int, col: int) -> str:
        """A cell in plain words: B4 on “Summary” (the sheet's name is
        somebody's words: quoted, ``record.quoted``), or "B4" alone when the
        workbook has one sheet. Never "Summary!B4"."""
        cell = f"{_col_letters(col)}{row}"
        return cell if len(self.sheets or self.formulas) <= 1 else f"{cell} on {quoted(sheet, 40)}"


def _errors() -> "frozenset[str]":
    from ..documents.lint import _XL_ERRORS

    return _XL_ERRORS


def _load(path: Path, late: Callable[[], bool] = _never_late) -> _Book:
    """Open a workbook with the reader the app's own tools use (openpyxl)
    and read it, ONCE: the same load answers "does it open", gives every
    filled cell as written and each formula's saved result. The deadline
    (*late*) is looked at on every row of the walk: a long sheet stops there
    (:class:`_OutOfTime`), never at the end of the file. BLOCKING.

    :class:`_DoesNotOpen` exactly when that reader fails to open it: no
    part of the package is looked for by its name here. :class:`_Protected`
    for the older Office container (a file protected with a password).
    A workbook that opened and whose cells could not be read through (a
    sheet of a kind the reader trips over) is :class:`_NotRead`."""
    from openpyxl import load_workbook

    why = _older_container(path)
    if why:
        raise _Protected(why)
    try:
        wb = load_workbook(str(path), read_only=True, data_only=False)
    except Exception as exc:  # noqa: BLE001 — the reader could not open it: the one "Problem"
        log.debug("a made workbook could not be opened", exc_info=True)
        if _disk_refused(exc):      # a lock, a permission: not the reader's failure
            raise _NotRead() from exc
        raise _DoesNotOpen() from exc
    try:
        trusted = not _recalculates(path)
        try:
            book = _read_once(wb, late, trusted)
        except (ImportError, AttributeError, TypeError):
            # The one-pass read leans on the library's own cell parser. If a
            # later version moves it, read the slower way (two passes): the
            # answers are the same.
            log.debug("the one-pass read of a workbook is unavailable: reading it twice", exc_info=True)
            try:
                book = _read_twice(wb, path, late, trusted)
            except _OutOfTime:
                raise
            except Exception as exc:  # noqa: BLE001
                raise _NotRead() from exc
        # The reader leaves out, without a word, a sheet the workbook lists
        # and whose part is missing. Then not every cell was read.
        book.partial = _sheets_listed(path) != len(book.sheets)
        return book
    finally:
        wb.close()


def _older_container(path: Path) -> str:
    """Why an Office file is not opened as a package: it is the older Office
    container, which is what a .docx or .xlsx protected with a password is
    saved as (recognised by the document readers' own guard,
    ``documents/readers._guard_office_encrypted``). "" for any other file.
    "Protected with a password" is said for certain only when the container
    names its encrypted part."""
    from ..documents.readers import _guard_office_encrypted

    try:
        _guard_office_encrypted(path)
    except ValueError:
        try:
            named = _ENCRYPTED_PART in path.read_bytes()    # under the size cap
        except OSError:
            named = False
        return PROTECTED if named else MAY_BE_PROTECTED
    return ""


#: A workbook that asks for every formula to be worked out again when it is
#: opened (``<calcPr fullCalcOnLoad="1">``). A writer that cannot work
#: formulas out says so this way and saves a placeholder (0) as each result.
_FULL_CALC_RX = re.compile(rb"<(?:\w+:)?calcPr\b[^>]*\bfullCalcOnLoad\s*=\s*[\"'](?:1|true)[\"']", re.IGNORECASE)


_REL_RX = re.compile(rb"<(?:[\w.-]+:)?Relationship\b([^>]*)>")
_REL_TYPE_RX = re.compile(rb"\bType\s*=\s*[\"']([^\"']*)[\"']")
_REL_TARGET_RX = re.compile(rb"\bTarget\s*=\s*[\"']([^\"']*)[\"']")


def _workbook_part(zf: Any) -> str:
    """The package's own workbook part: the one its relationships name as
    the office document (``xl/workbook.xml`` in most files; any name, in
    any folder, in others). Read as text (no XML parser is handed a file
    somebody else may have shaped). Raises when the package names none."""
    for rel in _REL_RX.finditer(zf.read("_rels/.rels")):
        kind, target = _REL_TYPE_RX.search(rel.group(1)), _REL_TARGET_RX.search(rel.group(1))
        if kind is not None and target is not None and kind.group(1).endswith(b"/officeDocument"):
            return target.group(1).decode("utf-8").lstrip("/")
    raise KeyError("the package names no office document")


def _recalculates(path: Path) -> bool:
    """Does the workbook say its saved results are NOT to be trusted (it
    asks for a full recalculation on load)? Read from the package's own
    workbook part, found the way a reader finds it (:func:`_workbook_part`:
    never by a name or a folder). True in any doubt (no such part, a
    package that cannot be read): a saved result is then never read as
    "saved", and the formula is worked out here."""
    try:
        with zipfile.ZipFile(path) as zf:
            return _FULL_CALC_RX.search(zf.read(_workbook_part(zf))) is not None
    except Exception:  # noqa: BLE001 — in doubt, the saved results are not trusted
        log.debug("a workbook's calculation setting could not be read", exc_info=True)
        return True


#: One sheet in the workbook part's own list (``<sheet name=... r:id=...>``,
#: under any prefix; ``<sheets>`` itself does not match).
_SHEET_LISTED_RX = re.compile(rb"<(?:[\w.-]+:)?sheet\s")


def _sheets_listed(path: Path) -> int:
    """How many sheets the workbook part lists. -1 when it cannot be told
    (which then equals no count of loaded sheets: in doubt, not every sheet
    is taken as read)."""
    try:
        with zipfile.ZipFile(path) as zf:
            return len(_SHEET_LISTED_RX.findall(zf.read(_workbook_part(zf))))
    except Exception:  # noqa: BLE001
        log.debug("a workbook's list of sheets could not be read", exc_info=True)
        return -1


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


def _read_once(wb: Any, late: Callable[[], bool], trusted: bool = True) -> _Book:
    """Every sheet's real cells in ONE pass over the file: the library's own
    cell parser, asked for the formula AND, beside it, the saved result
    (kept only when the workbook stands by its saved results: *trusted*)."""
    from openpyxl.styles.numbers import BUILTIN_FORMATS
    from openpyxl.worksheet._reader import VALUE_TAG, WorkSheetParser, _cast_number

    class _Both(WorkSheetParser):
        def parse_cell(self, element: Any) -> dict:
            cell = super().parse_cell(element)
            # An error cell is a cell whose STORED type is an error
            # (``t="e"``), read from the file's own element: never what the
            # reader makes of a cell of another type.
            stored_error = element.get("t") == "e"
            if cell["data_type"] == "f":
                cell["saved"] = _saved_result(element, VALUE_TAG, _cast_number)
                cell["saved_error"] = stored_error
            elif stored_error:
                cell["error"] = True
            elif cell["data_type"] == "e":
                # The reader's own mark for a NUMBER under a date format
                # that is outside the dates it can make (20261126 typed
                # under yyyy-mm-dd). The file stores a number: no error.
                cell["undated"] = True
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
        errors = book.errors.setdefault(ws.title, {})
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
                    if cell.get("undated"):
                        if not book.undated:
                            book.undated = book.place(ws.title, r, c)
                        continue    # neither the error the reader calls it nor a date this check can read
                    if cell.get("error"):
                        errors[(r, c)] = str(v or "").strip()   # a cell whose STORED type is an error
                    if not _keep(book, ws.title, cells, r, c, v):
                        continue
                    seen += 1
                    if cell["data_type"] == "f":
                        if trusted and cell.get("saved") is not None:
                            saved[(r, c)] = cell["saved"]
                            if cell.get("saved_error"):
                                errors[(r, c)] = str(cell["saved"]).strip()
                    elif _is_number(v) and cell["style_id"] and is_percent(cell["style_id"]):
                        book.percent.add((ws.title, r, c))
                if seen >= MAX_CELLS:
                    book.cut = True
                    break
        if book.cut:
            break
    return book


def _read_twice(wb: Any, path: Path, late: Callable[[], bool], trusted: bool = True) -> _Book:
    """The same book by the library's public reader: the cells as written
    from *wb*, the saved results (when the workbook stands by them:
    *trusted*) from a second, values-only load."""
    from openpyxl import load_workbook

    book = _Book()
    book.sheets = list(wb.sheetnames)
    seen = 0
    for ws in wb.worksheets:
        cells = book.formulas.setdefault(ws.title, {})
        book.values.setdefault(ws.title, {})
        errors = book.errors.setdefault(ws.title, {})
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
                if getattr(cell, "data_type", "") == "e":
                    if _maybe_undated(cell):
                        # This reader cannot say what the file STORES here:
                        # it reads "#VALUE!" for a stored error and for a
                        # number under a date format alike. No error cell.
                        if not book.undated:
                            book.undated = book.place(ws.title, r, c)
                        continue
                    errors[(r, c)] = str(v or "").strip()   # a cell whose TYPE is an error
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
    if not trusted:
        return book     # the workbook does not stand by its saved results: none is read
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
                        if getattr(cell, "data_type", "") == "e" and _maybe_undated(cell):
                            continue    # a saved NUMBER under a date format: not read as a saved result
                        book.values[ws.title][key] = cell.value
                        if getattr(cell, "data_type", "") == "e":
                            book.errors[ws.title][key] = str(cell.value).strip()
    finally:
        kept.close()
    return book


def _maybe_undated(cell: Any) -> bool:
    """May a cell the public reader calls an error be a NUMBER under a date
    format (the reader turns one it cannot make a date of into "#VALUE!")?
    True for "#VALUE!" under a date format, and when the format cannot be
    read (in doubt, it is not called an error cell)."""
    if str(getattr(cell, "value", "") or "").strip() != "#VALUE!":
        return False
    try:
        from openpyxl.styles.numbers import is_date_format

        return bool(is_date_format(cell.number_format))
    except Exception:  # noqa: BLE001
        return True


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
      error" (never which one; a result too large to hold is one, the
      evaluator's own rule); one that comes to ``#VALUE!`` or any other
      error, or one the evaluator cannot do, counts as not worked out.

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
        book.worked.setdefault(sheet, {})[(row, col)] = 0.0 if got is None else got
    return found, unworked


def _in_order(book: _Book, found: "Iterable[tuple[str, int, int, str]]") -> list[str]:
    order = {name: i for i, name in enumerate(book.formulas)}
    return [f"{book.place(sheet, row, col)} {words}"
            for sheet, row, col, words in sorted(found, key=lambda f: (order.get(f[0], 0), f[1], f[2]))]


def _error_cells(book: _Book) -> list[str]:
    """Every cell whose STORED type is an error ("C7 on Summary holds
    #DIV/0!"): an error value, or a formula's saved result of that type in
    a workbook that stands by its saved results. What the file itself
    holds, and nothing Iron Jarvis worked out; a TEXT cell that reads "#N/A"
    (typed as words, with or without spaces round it) is text, not an
    error. EVERY such cell is one, whatever it holds: the error is NAMED
    only when it is one of the seven everybody knows; any other (a newer
    program's "#SPILL!", another program's own code) reads "holds an
    error", and the cell's own text is never shown."""
    known = _errors()
    found = [(sheet, row, col, f"holds {code}" if code in known else "holds an error")
             for sheet, cells in book.errors.items() for (row, col), code in cells.items()]
    return _in_order(book, found)


def _decimals(value: float) -> int:
    """How many decimal places a number was written with (6 at most)."""
    text = repr(float(value))
    if "e" in text or "E" in text:
        return 6
    return min(6, len(text.partition(".")[2].rstrip("0")))


def _same(total: float, added: float) -> bool:
    """Does a written figure READ as this other one: equal to the cent (or
    to the last place it was written with), or the other sits at an exact
    half of its last place and it is that rounded either way? Loose on
    purpose, and used ONLY to ask whether some other reading explains a
    total that differs (:func:`_explained`): what it lets through is then
    not judged. It never says a total "adds up": that is :func:`_adds_up`,
    which is exact."""
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


def _adds_up(total: Any, added: "Decimal | None") -> bool:
    """Is a total EXACTLY the sum of its cells (*added*: :func:`_sum_exact`)?
    Compared in decimal, as the cells hold their numbers, with NO slack: a
    total one cent off 1,500 rows of fifty million is not "adds up". A
    total that matches only after rounding is not either (it is not
    judged: :func:`_rounder`)."""
    try:
        return added is not None and math.isfinite(total) and _dec(total) == added
    except (ArithmeticError, ValueError, TypeError):
        return False


def _totals(book: _Book, late: Callable[[], bool] = _never_late) -> "tuple[int, list[str]]":
    """``(how many totals add up, the ones worth a look in plain words)``.

    A TOTAL is a typed number or a formula in the row of a cell that is,
    WHOLE, a total word (:data:`_TOTAL_WORDS`), to that cell's right as far
    as the next label of the row. A figure beside "Total revenue" or a payee
    called "Total Wine & More" is not judged at all.

    ONE shape is judged, and "adds up" means exactly its arithmetic
    (:func:`_row_total`). Everything else gets NO row: not "adds up", not
    "worth a look", not "not checked". In particular nothing is said

    * of a column that holds more than one total (a subtotal and a total,
      two sections, two tables one above the other): which cells each of
      them adds is a guess;
    * of a column HEADED by a total word (``Jan | Feb | Total``): a column
      of line totals is not judged;
    * of a workbook with more than :data:`MAX_TOTALS` total labels.

    A total is NEVER "wrong" (``failed``): Iron Jarvis added it up itself,
    and that is not the same as the file being wrong."""
    good = 0
    look: list[str] = []
    if sum(1 for cells in book.formulas.values() for v in cells.values() if _is_label(v)) > MAX_TOTALS:
        return good, look
    for sheet, cells in book.formulas.items():
        saved = book.values.get(sheet, {})
        by_row: dict[int, list[int]] = {}
        for (r, c) in cells:
            by_row.setdefault(r, []).append(c)
        figures: list[tuple[int, int, int]] = []
        for (row, col) in sorted(k for k, v in cells.items() if _is_label(v)):
            if late():
                raise _OutOfTime()
            for c in sorted(c for c in by_row.get(row, ()) if c > col):
                x = cells.get((row, c))
                if _is_label(x):
                    break   # the next label of this row owns what follows it
                if _is_number(x) or _is_formula(x):
                    figures.append((row, col, c))
        in_column: dict[int, int] = {}
        for _row, _label_col, c in figures:
            in_column[c] = in_column.get(c, 0) + 1
        for row, label_col, c in figures:
            if late():      # each figure looks up its own column
                raise _OutOfTime()
            if in_column[c] != 1:
                continue    # another total in this column: which cells each adds is a guess
            verdict = _row_total(book, sheet, cells, saved, row, c, label_col, by_row)
            if verdict is None:
                continue
            kind, words = verdict
            if kind == PASSED:
                good += 1
            else:
                look.append(words)
    return good, look


def _list_above(cells: dict, row: int, col: int) -> "list[tuple[int, float]]":
    """The list a total stands under, top first, each ``(row, the typed
    number)``; ``[]`` when the cells above it are not the ONE shape:

    the unbroken run of cells DIRECTLY above the total, at least two, every
    one a plain TYPED number, ending right under a TEXT heading.

    A blank cell, a word ("TBD", "-"), a number typed as text, a date or a
    formula anywhere in it, a number above the heading, and the top of the
    sheet in place of a heading each leave the total not judged: a total is
    never said to add up over part of a list."""
    run: list[tuple[int, float]] = []
    r = row - 1
    while r >= 1 and len(run) < _SCAN_ROWS:
        v = cells.get((r, col))
        if not (_is_number(v) and math.isfinite(v)):
            break
        run.append((r, v))      # as the cell holds it (a whole number stays whole)
        r -= 1
    if len(run) < 2 or r < 1:
        return []
    heading = cells.get((r, col))
    over = cells.get((r - 1, col)) if r > 1 else None
    if not _is_heading(heading) or _is_number(over) or _is_formula(over) or _is_text_number(over):
        return []
    run.reverse()
    return run


def _has_subtotal(cells: dict, by_row: "dict[int, list[int]]", rows: "list[tuple[int, float]]") -> bool:
    """Does the list hold a subtotal of ANY kind? Then its total is not
    judged (a total over a subtotal adds some of its cells and not others,
    and which is a guess):

    * a word in any cell of one of its rows that reads like a total
      (:data:`_SUBTOTAL_LIKE_RX`: "Subtotal", "Net amount", "Total before
      tax", "Gross");
    * a row that equals the sum of two or more rows directly above it
      (100, 200, 300), whatever it is called."""
    for r, _x in rows:
        for c in by_row.get(r, ()):
            v = cells.get((r, c))
            if isinstance(v, str) and not _is_formula(v) and _SUBTOTAL_LIKE_RX.search(v):
                return True
    vals = [x for _r, x in rows]
    sums = [0.0]                # sums[j]: the first j rows added
    for x in vals:
        sums.append(sums[-1] + x)
    seen: list[float] = []      # sums[0 .. i-2], sorted: where a run of two or more rows above row i may start
    for i, x in enumerate(vals):
        if i < 2:
            continue
        insort(seen, sums[i - 2])
        want = sums[i] - x
        slack = 0.5 * 10 ** -max(2, _decimals(x)) + 1e-9
        at = bisect_left(seen, want - slack)
        if at < len(seen) and seen[at] <= want + slack:
            return True
    return False


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
                   label_col: int, total: float) -> bool:
    """Is there a plain reason the total of a list is NOT the sum of its
    rows? Then the difference is not judged (it is not "worth a look"):

    * the total's label is not the first filled cell of its row (a long
      table whose rows are "North | Orders", "North | Total");
    * a row's own label says it is not added, or is taken away (a rate, a
      price, an average; a discount, a deposit paid, expenses);
    * the top row of the list is a second heading row of numbers;
    * another reading of the rows gives the total (:func:`_explained`)."""
    if any((row, c) in cells for c in range(1, label_col)):
        return True
    for r, _x in rows:
        label = cells.get((r, label_col))
        if isinstance(label, str) and (_NOT_ADDED_RX.search(label) or _ROW_WORDS_RX.search(label)):
            return True
    if _numeric_heading_row(cells, by_row, rows[0][0], label_col):
        return True
    return _explained(total, [x for _r, x in rows])


def _row_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int, label_col: int,
               by_row: "dict[int, list[int]]") -> "tuple[str, str] | None":
    """One figure of a total row, judged. None = it is not judged, and
    NOTHING is said of it.

    THE ONE SHAPE (all of it, for a pass and for a look alike): the figure
    is not a percent and its column is not headed as a kind that is not
    added (:data:`_NOT_ADDED_RX`: a rate, a price, a balance, an invoice
    number); directly above it stands one list (:func:`_list_above`: two or
    more plain typed numbers, no gap, under a text heading); no row of that
    list is a subtotal of any kind (:func:`_has_subtotal`); and every row
    of it has a label in words in the total label's own column.

    ``passed`` ("adds up") means exactly its arithmetic: a typed figure
    EQUAL to the sum of ALL those cells (:func:`_adds_up`), or a plain
    ``=SUM`` of exactly those cells whose saved result, where the file
    holds one, is that sum.

    ``look``: a TYPED figure that differs, when it is not written rounder
    than its rows (:func:`_rounder`) and no other plain reading explains it
    (:func:`_other_reading`). Both figures, no verdict. NEVER ``failed``.

    Everything else: None."""
    if (sheet, row, col) in book.percent:
        return None
    rows = _list_above(cells, row, col)
    if not rows:
        return None
    if _NOT_ADDED_RX.search(str(cells.get((rows[0][0] - 1, col)))) or _has_subtotal(cells, by_row, rows):
        return None
    for r, _x in rows:
        label = cells.get((r, label_col))
        if not (isinstance(label, str) and label.strip() and not _is_formula(label)):
            return None     # a row with no label in words (a number, a date, nothing): not a list to judge
    vals = [float(x) for _r, x in rows]
    added = _sum_exact(x for _r, x in rows)
    if added is None:
        return None
    v = cells.get((row, col))
    if _is_formula(v):
        ref = _one_sum_range(v)
        if ref is None or (ref[0] and ref[0].lower() != sheet.lower()) or (ref[2], ref[4]) != (col, col) or (
                ref[1], ref[3]) != (rows[0][0], rows[-1][0]):
            return None     # not a plain SUM of exactly the list above it
        last = saved.get((row, col))
        if last is not None and not (_is_number(last) and _adds_up(last, added)):
            return None     # the file holds another result for it: nothing is said
        return PASSED, ""
    total = float(v)
    if not math.isfinite(total):
        return None
    if _adds_up(v, added):
        return PASSED, ""
    if _rounder(total, vals) or _other_reading(cells, by_row, [(r, float(x)) for r, x in rows], row, label_col,
                                               total):
        return None
    return LOOK, _differs(book.place(sheet, row, col), _dec(v), added)


def _named(items: list[str], rest_words: str, one_word: str) -> str:
    """Up to :data:`MAX_NAMED` findings in full and the rest COUNTED ("and
    2 more totals", "and 1 more total"). A finding is never cut: while the
    words would be longer than the record keeps (``record.DETAIL_MAX``), one
    finding fewer is named and one more is counted."""
    said = ""
    for n in range(min(MAX_NAMED, len(items)), 0, -1):
        rest = len(items) - n
        said = "; ".join(items[:n]) + (f"; and {rest:,} more {one_word if rest == 1 else rest_words}" if rest else "")
        if len(said) <= DETAIL_MAX:
            break
    return said


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
    except _DoesNotOpen:
        return [row(FILE_OPENS, FAILED, could_not_open(name))]
    except _Protected as exc:
        return [row(FILE_OPENS, NOT_CHECKED, str(exc))]
    except _NotRead:
        return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
    except _OutOfTime:
        return [row(FILE_OPENS, PASSED, "opens"), row(SHEET_ERRORS, NOT_CHECKED, OUT_OF_TIME)]
    out = [row(FILE_OPENS, PASSED, "opens")]
    if not any(book.formulas.values()) and not any(book.errors.values()):
        if book.partial:
            return out + [row(DOC_TEXT, NOT_CHECKED, SHEETS_NOT_READ)]
        if book.spaces and not (book.void or book.undated):
            # The reader SAW this workbook's cells, and each holds only
            # blank space: said as that, never "could not be read".
            return out + [row(DOC_TEXT, LOOK, ONLY_SPACES)]
        # The reader found no filled cell. "No filled cells" is said only
        # when nothing else in the package holds a cell either: a workbook
        # whose cells the reader does not see (Strict Open XML) is NOT empty.
        if _package_holds(path, _SHEET_PART_RX, _CELL_RX, late) is not False:
            return out + [row(DOC_TEXT, NOT_CHECKED, CELLS_NOT_READ)]
        # A document that is empty: worth a look (it may be what was asked
        # for), never a problem.
        return out + [row(DOC_TEXT, LOOK, NO_CELLS)]
    # WHAT THE FILE HOLDS: a cell whose type is an error is a problem. WHAT
    # IRON JARVIS WORKS OUT (a formula with no saved result the file stands
    # by, a function measured to open as #NAME?) is worth a look, never a
    # problem. The good phrase is one of two true ones: "no error cells"
    # when every formula has a SAVED result, "its N formulas work out" when
    # Iron Jarvis worked some out itself. A workbook read only in part is
    # not worked out (a formula over half a column would come to the wrong
    # number).
    held = _error_cells(book)
    odd = _not_a_number(book)
    timed_out = False
    try:
        found, unworked = ([], 0) if book.cut or book.partial else _work_out(book, late)
    except _OutOfTime:
        found, unworked, timed_out = [], 0, True
    if held:
        out.append(row(SHEET_ERRORS, FAILED, _named(held, "cells hold an error", "cell holds an error")))
    if found:
        out.append(row(SHEET_ERRORS, LOOK, _named(_in_order(book, found), "formulas", "formula")))
    if book.cut:
        if not held:
            out.append(row(SHEET_ERRORS, NOT_CHECKED, f"only its first {MAX_CELLS:,} cells were read"))
    elif timed_out:
        out.append(row(SHEET_ERRORS, NOT_CHECKED, OUT_OF_TIME))
    elif book.partial:
        out.append(row(SHEET_ERRORS, NOT_CHECKED, SHEETS_NOT_READ))
    elif odd:
        out.append(row(SHEET_ERRORS, NOT_CHECKED, f"{odd} holds a number this check cannot read"))
    elif book.undated:
        out.append(row(SHEET_ERRORS, NOT_CHECKED, f"{book.undated} holds a date this check cannot read"))
    elif unworked:
        # Only what Iron Jarvis did: a workbook that asks to be worked out
        # again HOLDS a (placeholder) result for the formula, so "it has no
        # saved result" would not be true of every file.
        out.append(row(SHEET_ERRORS, NOT_CHECKED, f"{_plural(unworked, 'formula')} could not be worked out here"))
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
        good, look = _totals(book, late)
    except _OutOfTime:
        out.append(row(SHEET_TOTALS, NOT_CHECKED, OUT_OF_TIME))
        return out
    if good:
        out.append(row(SHEET_TOTALS, PASSED, f"{_plural(good, 'total')} add{'s' if good == 1 else ''} up"))
    if look:
        # Iron Jarvis's own adding up: "worth a look", never a problem.
        out.append(row(SHEET_TOTALS, LOOK, _named(look, "totals", "total")))
    return out


# --------------------------------------------------------------------------- #
# What a package holds that the reader did not see
# --------------------------------------------------------------------------- #
#: A run of text in a part of a Word package, under ANY prefix (``<w:t>``,
#: ``<ns0:t>``, a drawing's ``<a:t>``) with something in it that is not
#: blank space.
_TEXT_RUN_RX = re.compile(rb"<(?:[\w.-]+:)?t(?:\s[^>]*)?>[^<]*[^\s<][^<]*<")
#: A cell's value, formula or inline text in a part of a workbook package,
#: under any prefix and in any namespace (a Strict Open XML sheet, a chart's
#: saved values).
_CELL_RX = re.compile(rb"<(?:[\w.-]+:)?(?:v|f|is)[\s>]")
#: The parts looked through: EVERY XML part of a Word package (a run of
#: text is ``<t>`` under some prefix wherever it sits; the document's
#: properties hold none, so a title there is not "text"), and every XML
#: part under ``xl/`` of a workbook.
_WORD_PART_RX = re.compile(r".*\.xml", re.IGNORECASE)
_SHEET_PART_RX = re.compile(r"xl/.*\.xml", re.IGNORECASE)


def _package_holds(path: Path, parts: "re.Pattern[str]", what: "re.Pattern[bytes]",
                   late: Callable[[], bool] = _never_late) -> "bool | None":
    """Does ANY of the package's *parts* hold *what* (a run of text, a
    cell)? The second look behind "found no text" and "no filled cells":
    the app's reader reads the usual places, and a file whose words or
    cells sit elsewhere is not empty. True = something is there; False =
    every part was read and none holds it; None = it could not be told (the
    package could not be read, or the deadline ran out): only False lets a
    file be called empty. BLOCKING; the package is under
    :data:`MAX_UNPACKED_BYTES`."""
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not parts.fullmatch(name):
                    continue
                if late():
                    return None
                if what.search(zf.read(name)):
                    return True
        return False
    except Exception:  # noqa: BLE001 — in doubt, nothing is called empty
        log.debug("a package's parts could not be looked through", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# A Word document, a PDF, a text file
# --------------------------------------------------------------------------- #
def _docx_checks(path: Path, name: str, folder: str = "", late: Callable[[], bool] = _never_late) -> list[Check]:
    """A Word document, through the reader the app's own tools use
    (``documents/readers._read_docx``: python-docx; the headers, the body's
    paragraphs and tables, the footers, the notes).

    "opens": that reader loaded it. A PROBLEM exactly when it could not,
    said as that fact. A file protected with a password is ``not_checked``.
    "has text": the reader read words. "Iron Jarvis found no text in it"
    (worth a look): the reader read none AND no part of the package holds a
    run of text. A document whose words sit where the reader does not read
    (a text box, a content control, another prefix) is ``not_checked``."""
    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path))

    why = _older_container(path)
    if why:
        return [row(FILE_OPENS, NOT_CHECKED, why)]
    # The reader's library is imported HERE, outside the try: a reader that
    # is not there is no fact about the file ("it could not be checked
    # here", ``check_one``'s own net), never "could not open".
    import docx

    from ..documents.readers import _nonempty_or_sentinel, _read_docx

    try:
        text = _read_docx(path)
    except Exception:  # noqa: BLE001 — did it not open, or did its text not read?
        log.debug("a made Word document could not be read", exc_info=True)
        try:
            docx.Document(str(path))
        except Exception as exc:  # noqa: BLE001 — the reader could not open it: the one "Problem"
            # This reader hides the disk's own refusal (it answers "not a
            # package" for a file it was not let read): the disk is asked
            # again before the reader is blamed.
            if _disk_refused(exc) or not _disk_gives(path):
                return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
            return [row(FILE_OPENS, FAILED, could_not_open(name))]
        return [row(FILE_OPENS, PASSED, "opens"), row(DOC_TEXT, NOT_CHECKED, TEXT_NOT_READ)]
    out = [row(FILE_OPENS, PASSED, "opens")]
    if text != _nonempty_or_sentinel("", kind="docx") and _has_words(text):
        return out + [row(DOC_TEXT, PASSED, "has text")]
    if _package_holds(path, _WORD_PART_RX, _TEXT_RUN_RX, late) is not False:
        return out + [row(DOC_TEXT, NOT_CHECKED, TEXT_NOT_READ)]
    return out + [row(DOC_TEXT, LOOK, NO_TEXT)]


def _pdf_checks(path: Path, name: str, folder: str = "", late: Callable[[], bool] = _never_late) -> list[Check]:
    """A PDF, through the reader the app's own tools use (pypdf). "opens":
    that reader counted its pages; the count is said as a fact ("12 pages")
    and compared with nothing. A PROBLEM exactly when the reader could not
    open it, said as that fact. A PDF protected with a password (the empty
    one does not open it, as ``documents/readers._read_pdf`` tries) is
    ``not_checked``. Text is looked for on the first
    :data:`MAX_PDF_TEXT_PAGES` pages while the deadline lasts: found is "has
    text"; none found on EVERY page is worth a look; a PDF read only in
    part says nothing about its text."""
    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path))

    from pypdf import PdfReader     # outside the try: a reader that is not there is no fact about the file

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:  # noqa: BLE001 — it could not be tried here: nothing is said of a password
                log.debug("a made PDF's lock could not be tried", exc_info=True)
                return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
            if not unlocked:
                return [row(FILE_OPENS, NOT_CHECKED, PROTECTED)]
        pages = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 — the reader could not open it: the one "Problem"
        log.debug("a made PDF could not be opened", exc_info=True)
        if _disk_refused(exc):      # a lock, a permission: not the reader's failure
            return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
        return [row(FILE_OPENS, FAILED, could_not_open(name))]
    out = [row(FILE_OPENS, PASSED, "opens")]
    # The count the file STATES is not the pages it holds: the pages are
    # walked (no text is read for it), and the number is said only when the
    # walk reaches its end and finds exactly that many. A file whose count
    # and pages disagree gets no pages row, and nothing is said of its text.
    try:
        walked = sum(1 for _page in reader.pages)
    except Exception:  # noqa: BLE001 — the walk broke: the count is not said
        log.debug("a made PDF's pages could not be walked", exc_info=True)
        walked = -1
    if walked != pages:
        return out
    if pages == 0:
        return out + [row(DOC_TEXT, LOOK, "it has no pages")]
    out.append(row(PDF_PAGES, PASSED, _plural(pages, "page")))
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


#: Characters that are in a file and that nobody sees: the byte-order mark
#: and the zero-width ones. A file of only these (and blank space) has no
#: text.
_NOT_SEEN = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060"))


def _has_words(text: str, blanks: "str | None" = None) -> bool:
    """Does *text* hold anything a person would see: something that is not
    blank space, not a character nobody sees (:data:`_NOT_SEEN`) and, for a
    table of text, not one of its separators (*blanks*)?"""
    return bool(str(text or "").translate(_NOT_SEEN).strip(blanks))


def _disk_gives(path: Path) -> bool:
    """Does the DISK hand the file over, all of it, right now? Asked before
    any reader is (and again before a reader that hides the disk's refusal
    is blamed): a file another program holds, one a permission keeps, one
    that went away is not read, and that is never the reader's failure nor
    a fact about what the file holds."""
    try:
        with open(path, "rb") as fh:
            while fh.read(1 << 20):
                pass
        return True
    except OSError:
        log.debug("the disk did not hand a made file over", exc_info=True)
        return False


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
    # A table of text that holds only its separators (",,,") has no text;
    # nor does a file of characters nobody sees (zero-width ones, a
    # byte-order mark).
    blank = not _has_words(text, _TABLE_BLANKS if ext in _TABLE_EXTS else None)
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

    stamp = ""

    def row(code: str, result: str, detail: str) -> Check:
        return Check(code, result, detail, file=name, folder=folder, path_key=path_key(path), stamp=stamp)

    if not may_read(path):
        return [row(FILE_OPENS, NOT_CHECKED, "it is outside the folders Iron Jarvis may read")]
    try:
        if not p.exists() or p.is_dir():
            return []
        size = p.stat().st_size
        # The file as it was when it was checked (its size and when it was
        # last written), read BEFORE it is opened: a row says what THIS
        # file held, and is not shown for a file that has changed since.
        stamp = file_stamp(p)
    except OSError:
        return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
    if size == 0:
        # An empty DOCUMENT is worth a look (it may be what was asked for).
        # An empty file of any other kind is what it was meant to be: no row.
        return [row(DOC_TEXT, LOOK, EMPTY)] if ext in _DOC_EXTS else []
    if size > MAX_BYTES:
        return [row(FILE_OPENS, NOT_CHECKED, f"it is too large to check here ({_size_words(size)})")]
    # THE DISK IS ASKED FIRST, before any reader: a file it does not hand
    # over (another program holds it, a permission, it went away) is "not
    # checked", for every kind. Some readers hide the disk's refusal and
    # answer "this is no document": that is never taken as the file's fault.
    if not _disk_gives(p):
        return [row(FILE_OPENS, NOT_CHECKED, NOT_READ)]
    # An Office file is a package: one that unpacks past the cap is not
    # opened by ANY reader below.
    if ext in _PACKAGE_EXTS and zipfile.is_zipfile(p) and _unpacked_too_large(p):
        return [row(FILE_OPENS, NOT_CHECKED, "it unpacks too large to check here")]
    try:
        if ext in _SHEET_EXTS:
            got = _sheet_checks(p, name, late, folder)
        elif ext in _WORD_EXTS:
            got = _docx_checks(p, name, folder, late)
        elif ext in _PDF_EXTS:
            got = _pdf_checks(p, name, folder, late)
        else:
            got = _text_checks(p, ext, name, folder)
    except Exception:  # noqa: BLE001 — a check that breaks is "not checked": never a pass, never "opens"
        log.debug("a made file's content check broke", exc_info=True)
        return [row(FILE_OPENS, NOT_CHECKED, BROKE)]
    return [replace(c, stamp=stamp) for c in got]


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
    "BROKE", "CELLS_NOT_READ", "DEADLINE_S", "HARD_WAIT_S", "MAX_BYTES", "MAX_CELLS", "MAX_FILES",
    "MAX_FORMULA_READS", "MAX_UNPACKED_BYTES", "MAX_WORKED", "MAY_BE_PROTECTED", "OUT_OF_TIME", "PROTECTED",
    "RUN_DEADLINE_S", "EMPTY", "NOT_READ", "NO_CELLS", "NO_TEXT", "OPENED_EXTS", "TEXT_NOT_READ",
    "check_files", "check_made_files", "check_one", "could_not_open", "names_for", "opened_kind", "parse_sum",
    "readable",
]
