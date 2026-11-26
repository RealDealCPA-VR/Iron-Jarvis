"""What a made file SAYS, checked by rule (no model call, ever).

The app already checked that a required file exists, is not empty and opens
(``deliverables/check.check_file``, reused here). Nothing looked inside it: a
sheet full of ``#DIV/0!`` or a grand total that is not the sum of its rows
went out under "Done". These are the checks of the content, each a fact about
the file:

* **it opens** (``file_opens``): on disk, not empty, and its kind loads;
* **a spreadsheet has no error cells** (``sheet_no_error_cells``): no cell
  holds one of Excel's error values (``documents/lint._XL_ERRORS``), written
  as a value or saved as a formula's last result;
* **a spreadsheet's totals add up** (``sheet_totals``): see :func:`_totals`;
* **a text or Word document is not empty of text** (``document_not_empty``;
  a Word file through ``documents/lint.lint_docx``);
* **a PDF's pages** (``pdf_pages``): how many it has, and, only when the
  reply states a count for that file, whether it is that many.

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
most :data:`MAX_VISITED` cells are stepped over), and the whole reply's checks
share :data:`DEADLINE_S` (what is left when it runs out is ``not_checked``). BLOCKING: :func:`check_made_files` is the hop
off the event loop, and it waits :data:`HARD_WAIT_S` at most.

NOT READ: a workbook's formulas are never recalculated. A total written as a
plain number is compared with the cells it totals; a ``=SUM(...)`` total is
followed only when every cell it adds is a number this module can read (a
file written by openpyxl holds NO saved formula results, and a missing one is
never read as zero); anything else is ``not_checked``.
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
    DOC_TEXT, FAILED, FILE_OPENS, NOT_CHECKED, PASSED, PDF_PAGES, SHEET_ERRORS, SHEET_TOTALS, Check, file_name,
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
#: A cell that labels a total. English first; the plain word in the six other
#: languages the claim readings know.
_TOTAL_RX = re.compile(
    r"^(?:grand\s+|sub[\s-]?|overall\s+|sum\s+)?"
    r"(?:totals?|subtotals?|sum|totale|totaal|summe|gesamt(?:summe)?|suma|somme|somma)\b"
    r"|\b(?:sub)?totals?$",
    re.IGNORECASE,
)
#: A column whose numbers are not added up (a rate, a price, an average).
_NOT_ADDED_RX = re.compile(
    r"%|\b(?:percent(?:age)?|pct|rate|price|avg|average|mean|median|margin|ratio|share|unit|per)\b",
    re.IGNORECASE,
)
_REF = (
    r"(?:(?:'(?P<q{n}>[^']+)'|(?P<s{n}>[A-Za-z0-9_.]+))!)?"
    r"\$?(?P<c1{n}>[A-Za-z]{{1,3}})\$?(?P<r1{n}>\d{{1,7}})"
    r"(?::\$?(?P<c2{n}>[A-Za-z]{{1,3}})\$?(?P<r2{n}>\d{{1,7}}))?"
)
_REF_RX = re.compile(_REF.format(n=""))
_SUM_RX = re.compile(r"^=\s*SUM\s*\((?P<args>[^()]*)\)\s*$", re.IGNORECASE)
_PLUS_RX = re.compile(r"^=\s*(?P<args>[^()=*/^&<>-]+)$")


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


def _is_label(v: Any) -> bool:
    if not isinstance(v, str) or _is_formula(v):
        return False
    text = " ".join(v.split()).strip(" :").lower()
    return bool(text) and len(text) <= 60 and _TOTAL_RX.search(text) is not None


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


class _Book:
    """A workbook's filled cells as written (``formulas``) and as last saved
    by a spreadsheet program (``values``: None where nothing was saved)."""

    def __init__(self) -> None:
        self.formulas: dict[str, dict[tuple[int, int], Any]] = {}
        self.values: dict[str, dict[tuple[int, int], Any]] = {}
        self.percent: set[tuple[str, int, int]] = set()
        self.cut = False

    def number(self, sheet: str, row: int, col: int, depth: int = 0) -> "float | None":
        """A cell as a number to add: a number is itself, a blank or a word
        is 0 (as SUM reads them), a formula is its saved result, else what a
        simple sum of numbers comes to. None = it cannot be worked out here."""
        v = self.formulas.get(sheet, {}).get((row, col))
        if v is None or isinstance(v, bool):
            return 0.0
        if _is_number(v):
            return float(v)
        if _is_formula(v):
            saved = self.values.get(sheet, {}).get((row, col))
            if _is_number(saved):
                return float(saved)
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


def _load(path: Path) -> _Book:
    """Read a workbook twice, read-only and cell-capped: as written, and as
    last saved (formula results). BLOCKING."""
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


def _error_cells(book: _Book) -> list[str]:
    """Every cell that holds an Excel error, as "Summary!C7 holds #DIV/0!"."""
    errors = _errors()
    out: list[str] = []
    for sheet, cells in book.formulas.items():
        saved = book.values.get(sheet, {})
        for (row, col) in sorted(cells):
            v = cells[(row, col)]
            shown = v.strip() if isinstance(v, str) and v.strip() in errors else None
            if shown is None and _is_formula(v):
                got = saved.get((row, col))
                shown = got.strip() if isinstance(got, str) and got.strip() in errors else None
            if shown is not None:
                out.append(f"{_place(sheet, row, col)} holds {shown}")
    return out


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


def _totals(book: _Book) -> "tuple[int, list[str], list[str]]":
    """``(how many totals add up, the wrong ones, the ones that could not be
    worked out)``, each of the last two in plain words.

    A TOTAL is a number or a formula beside a cell labelled total, sum or
    subtotal:

    * the label sits at the START of a row (numbers to its right): each of
      those numbers should be the sum of the cells directly above it. The
      cells are the unbroken run of numbers above (one or two blank rows
      before the total are stepped over), up to a blank cell, a heading or
      the top. Where another total row sits in that run the total may add
      the rows, the subtotals or only the rows since the last subtotal: any
      of the three is accepted, and none is ``not_checked`` (never wrong by
      guess). A column whose heading or number format says rate, price,
      percent or average is not a total and is left out.
    * the label HEADS a column (numbers below it): each row's number should
      be the sum of the numbers to its left (or, for exactly two of them,
      their product: quantity times price). It counts as one total. A column
      that is neither is ``not_checked``, never wrong.

    A formula total is followed only when it is a plain sum of cells
    (:func:`parse_sum`); a saved result that differs from what its cells add
    up to is wrong."""
    good = 0
    wrong: list[str] = []
    unknown: list[str] = []
    seen_labels = 0
    for sheet, cells in book.formulas.items():
        saved = book.values.get(sheet, {})
        max_col = max((c for _r, c in cells), default=0)
        for (row, col) in sorted(k for k, v in cells.items() if _is_label(v)):
            seen_labels += 1
            if seen_labels > MAX_TOTALS:
                unknown.append(f"{_place(sheet, row, col)} and the totals after it were not read")
                return good, wrong, unknown
            right = [c for c in range(col + 1, max_col + 1)
                     if _is_number(cells.get((row, c))) or _is_formula(cells.get((row, c)))]
            if right:
                for c in right:
                    verdict = _row_total(book, sheet, cells, saved, row, c)
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


def _row_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int) -> "tuple[str, str] | None":
    """One number of a total row, judged. None = it is not a total to add."""
    place = _place(sheet, row, col)
    v = cells.get((row, col))
    if (sheet, row, col) in book.percent:
        return None
    if _is_formula(v):
        added = book.sum_of(sheet, v, (row, col))
        if added is None:
            if parse_sum(v) is None:
                return NOT_CHECKED, f"{place} is worked out by a formula this check cannot follow"
            return NOT_CHECKED, f"{place} adds cells this check cannot work out"
        last = saved.get((row, col))
        if _is_number(last) and not _same(float(last), added):
            return FAILED, f"{place} says {_num(float(last))} and the cells it totals add up to {_num(added)}"
        return PASSED, ""
    total = float(v)
    r = row - 1
    stepped = 0
    while r >= 1 and cells.get((r, col)) is None and stepped < 2:
        r -= 1
        stepped += 1
    plain: list[float] = []      # every row that is not itself a total
    recent: list[float] = []     # ... since the last total row above
    subtotals: list[float] = []
    heading = ""
    while r >= 1:
        above = cells.get((r, col))
        if above is None:
            break
        if isinstance(above, str) and not _is_formula(above):
            heading = above
            break
        got = book.number(sheet, r, col)
        if got is None:
            return NOT_CHECKED, f"{place} sits under a cell this check cannot work out"
        if _row_is_total(cells, r, col):
            subtotals.append(got)
        else:
            plain.append(got)
            if not subtotals:
                recent.append(got)
        r -= 1
    if _NOT_ADDED_RX.search(heading):
        return None
    if not plain and not subtotals:
        return NOT_CHECKED, f"{place} has no numbers above it to add up"
    if not subtotals:
        added = sum(plain)
        if _same(total, added):
            return PASSED, ""
        return FAILED, f"{place} says {_num(total)} and the cells above it add up to {_num(added)}"
    for candidate in (sum(recent) if recent else None, sum(plain) if plain else None, sum(subtotals)):
        if candidate is not None and _same(total, candidate):
            return PASSED, ""
    return NOT_CHECKED, f"{place} sits under other totals, and this check cannot tell which cells it adds"


def _column_total(book: _Book, sheet: str, cells: dict, saved: dict, row: int, col: int) -> "tuple[str, str] | None":
    """A column headed "Total", judged as ONE total. None = not a total."""
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
        if _is_formula(v):
            if book.sum_of(sheet, v, (r, col)) is None:
                return NOT_CHECKED, f"{place} is worked out by a formula this check cannot follow"
        else:
            left = [book.number(sheet, r, c) for c in range(1, col) if _is_number(cells.get((r, c)))
                    or _is_formula(cells.get((r, c)))]
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


def _sheet_checks(path: Path, name: str) -> list[Check]:
    book = _load(path)
    out: list[Check] = []
    errors = _error_cells(book)
    if errors:
        out.append(Check(SHEET_ERRORS, FAILED, _named(errors, "cells hold an error"), file=name))
    elif book.cut:
        out.append(Check(SHEET_ERRORS, NOT_CHECKED,
                         f"only its first {MAX_CELLS:,} cells were read, and none of those holds an error", file=name))
    else:
        out.append(Check(SHEET_ERRORS, PASSED, "no error cells", file=name))
    if book.cut:
        out.append(Check(SHEET_TOTALS, NOT_CHECKED, "it has too many cells to add its totals up here", file=name))
        return out
    good, wrong, unknown = _totals(book)
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
#: "report.pdf (3 pages)", "report.pdf, 3 pages", "report.pdf has 3 pages".
_AFTER_NAME_RX = re.compile(
    r"^[\s`*_\"')\]]*(?:[,(:—–-]\s*|\s+(?:has|is|with|contains|runs\s+to)\s+)(?:all\s+|just\s+|only\s+)?"
    + _COUNT + r"\s*-?\s*pages?\b",
    re.IGNORECASE,
)
#: "the 3-page report.pdf", "a three page PDF report.pdf".
_BEFORE_NAME_RX = re.compile(
    _COUNT + r"\s*-?\s*page\s+(?:(?:pdf|file|document|report)\s+)?[`*_\"'(\[]*$", re.IGNORECASE,
)


def stated_pages(reply: str, name: str) -> "int | None":
    """The page count a reply STATES for the file *name*, or None. Only a
    count tied to that file's own name is read ("report.pdf (3 pages)", "the
    3-page report.pdf"): "I read your 12-page contract and saved report.pdf"
    states none. Two different counts for the one file state none either."""
    text = str(reply or "")[:40_000]
    name = str(name or "")
    if not text or not name:
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


def _pdf_check(path: Path, name: str, reply: str) -> list[Check]:
    from pypdf import PdfReader

    pages = len(PdfReader(str(path)).pages)
    said = stated_pages(reply, name)
    if said is None:
        return [Check(PDF_PAGES, PASSED, f"has {_plural(pages, 'page')}", file=name)]
    if said == pages:
        return [Check(PDF_PAGES, PASSED, f"has the {_plural(pages, 'page')} the reply says", file=name)]
    return [Check(PDF_PAGES, FAILED, f"it has {_plural(pages, 'page')} and the reply says {said:,}", file=name)]


# --------------------------------------------------------------------------- #
# One file, then a reply's files
# --------------------------------------------------------------------------- #
def check_one(path: Any, reply: str = "", *, may_read: Callable[[Any], bool] = readable) -> list[Check]:
    """Every content check of ONE made file. A file that is no longer there
    (a scratch file the reply removed again) is checked by nothing and says
    nothing. BLOCKING; may raise (the caller turns that into
    ``not_checked``)."""
    name = file_name(path)
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
        said = why if why.startswith("it ") else f"it does not open ({why})"
        return [Check(FILE_OPENS, FAILED, said, file=name)]
    out = [Check(FILE_OPENS, PASSED, "opens", file=name)]
    try:
        if ext in _SHEET_EXTS:
            out += _sheet_checks(p, name)
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
            out += _pdf_check(p, name, reply)
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
    *deadline_s* lasts (read from *clock*); the rest are ``not_checked`` and
    say why."""
    todo = _unique(paths)
    out: list[Check] = []
    started = clock()
    for path in todo[:max(0, int(max_files))]:
        name = file_name(path)
        if clock() - started > deadline_s:
            out.append(Check(FILE_OPENS, NOT_CHECKED, OUT_OF_TIME, file=name))
            continue
        try:
            out += one(path, reply, may_read=may_read)
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
    bounded by the size caps, and every file reads ``not_checked``). Never
    raises (a cancel still does)."""
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
    return [Check(FILE_OPENS, NOT_CHECKED, why, file=file_name(p)) for p in todo[:max(1, int(max_files))]]


__all__ = [
    "BROKE", "DEADLINE_S", "HARD_WAIT_S", "MAX_BYTES", "MAX_CELLS", "MAX_FILES", "MAX_UNPACKED_BYTES",
    "MAX_VISITED", "OUT_OF_TIME", "check_files", "check_made_files", "check_one", "parse_sum", "readable", "stated_pages",
]
