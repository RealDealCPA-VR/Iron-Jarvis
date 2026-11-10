"""Excel formula engine: parse and COMPUTE formulas without Excel.

openpyxl reads formulas but never computes them, and a formula a program wrote
has no saved result until real Excel opens the file. So "write a formula and
know what it comes to", "add up a column of formulas" and "check this total"
all need an evaluator of our own.

v1.90.0 built a small one for totals (SUM, AVERAGE, IF…). v1.465.0 rebuilt it
so a whole workbook can be worked out:

* RECALCULATION. :class:`Grid` answers a cell with its saved value, and when a
  formula cell has none it computes the formula, following references through
  other formulas and other sheets, remembering each result, and refusing a
  circular reference in words. (Before, such a cell read as blank, which
  arithmetic then treated as 0: a silently wrong number.)
* LAZY BRANCHES. The parser builds the formula first and evaluates after, so
  ``IF`` evaluates only the branch it takes and ``IFERROR`` can catch.
* THE FUNCTIONS OFFICE WORK USES (:data:`SUPPORTED`): conditional sums and
  counts, lookups, rounding, dates, text, and the loan, investment and
  depreciation functions.
* EXCEL'S OWN ARITHMETIC where it differs from Python's: ``ROUND`` rounds a
  half AWAY from zero (Python rounds to even: ``round(2.5)`` is 2, Excel's is
  3), blank cells are 0 in arithmetic, text is skipped by ``SUM`` over a
  range, comparisons of text ignore case, dates are day numbers.
* NAMES. A defined name ("TaxRate") resolves to its cell or range. Whole
  columns ("A:A") work, bounded by the sheet's used rows.

Anything outside the subset raises :class:`FormulaError` naming what it could
not do: an honest "I can't work that one out" beats a silently wrong number.
Every loop is bounded (:data:`_MAX_RANGE_CELLS`, :data:`_MAX_READS`,
:data:`_MAX_DEPTH`).
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

#: One range may hold at most this many cells.
_MAX_RANGE_CELLS = 200_000
#: One evaluation (a formula and everything it pulls in) may read this many.
_MAX_READS = 3_000_000
#: How deep a chain of formulas referring to formulas may go.
_MAX_DEPTH = 120

_EPOCH = _dt.datetime(1899, 12, 30)


class FormulaError(ValueError):
    """A formula this engine cannot parse or work out; the message is for the user."""


class ExcelError(FormulaError):
    """An Excel error VALUE (``#DIV/0!``, ``#N/A``…) a formula produced."""

    def __init__(self, code: str, why: str = "") -> None:
        super().__init__(f"{code}{': ' + why if why else ''}")
        self.code = code


_TOKEN_RX = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<sheet>(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)
  | (?P<func>[A-Za-z_][A-Za-z0-9_.]*)(?=\()
  | (?P<colrange>\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3})(?![A-Za-z0-9_(])
  | (?P<cell>\$?[A-Za-z]{1,3}\$?[0-9]{1,7})(?![A-Za-z0-9_])
  | (?P<number>[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<error>\#(?:N/A|REF!|DIV/0!|VALUE!|NAME\?|NUM!|NULL!))
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op><>|<=|>=|[-+*/^%&=<>(),:])
    """,
    re.VERBOSE,
)

_CELL_RX = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]{1,7})$")


def _col_num(letters: str) -> int:
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


def to_serial(v: Any) -> float:
    """A date or time as Excel's day number."""
    if isinstance(v, _dt.datetime):
        delta = v - _EPOCH
        return delta.days + delta.seconds / 86400.0
    if isinstance(v, _dt.date):
        return float((_dt.datetime(v.year, v.month, v.day) - _EPOCH).days)
    if isinstance(v, _dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second) / 86400.0
    if isinstance(v, _dt.timedelta):
        return v.total_seconds() / 86400.0
    raise FormulaError("not a date")


def from_serial(n: float) -> _dt.datetime:
    try:
        return _EPOCH + _dt.timedelta(days=float(n))
    except (OverflowError, ValueError):
        raise ExcelError("#NUM!", "not a valid date") from None


def _norm(v: Any) -> Any:
    """A cell value as the engine computes with it."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int):
        return float(v)
    if isinstance(v, float):
        return v
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return to_serial(v)
    return str(v)


class Grid:
    """The cells of one workbook, COMPUTED where no result was saved.

    ``get(sheet, col, row)`` answers the saved value; for a formula cell with
    none it works the formula out (memoised). ``computed`` counts the cells it
    had to work out, ``failed`` maps ``Sheet!A1`` to why one could not be."""

    def __init__(self, path: "str | Path | None") -> None:
        self._path = path
        self._fwb: Any = None
        self._memo: dict[tuple[str, int, int], Any] = {}
        self._stack: list[tuple[str, int, int]] = []
        self._reads = 0
        self.computed = 0
        self.failed: dict[str, str] = {}
        #: Names a caller defined (the calculator's labels), lower-cased.
        self.variables: dict[str, Any] = {}
        #: WHAT IF (v1.467.0, documents/excel_trace.py). ``overrides`` maps
        #: ``(sheet lower-cased, col, row)`` to a value used INSTEAD of the
        #: cell; ``fresh`` makes every formula cell be worked out even when
        #: Excel saved a result for it (a saved result does not know about an
        #: override). Both off: ``get`` behaves exactly as before.
        self.overrides: dict[tuple[str, int, int], Any] = {}
        self.fresh = False
        if path is None:  # the calculator: no workbook behind the formula
            self._wb = None
            self.sheetnames: list[str] = []
            self.active = ""
            return
        from openpyxl import load_workbook

        self._wb = load_workbook(str(path), data_only=True, read_only=False)
        self.sheetnames = list(self._wb.sheetnames)
        self.active = self._wb.active.title

    # -- raw access ---------------------------------------------------------
    def _sheet(self, wb: Any, sheet: str) -> Any:
        if wb is None:
            raise FormulaError("there is no workbook here, so a cell cannot be read")
        try:
            return wb[sheet]
        except KeyError:
            # Excel matches sheet names without regard to case.
            for name in wb.sheetnames:
                if name.lower() == sheet.lower():
                    return wb[name]
            raise FormulaError(f"no sheet named {sheet!r}") from None

    def _formulas(self) -> Any:
        if self._fwb is None and self._path is not None:
            from openpyxl import load_workbook

            self._fwb = load_workbook(str(self._path), data_only=False, read_only=False)
        return self._fwb

    @staticmethod
    def _peek(ws: Any, row: int, col: int) -> Any:
        """A cell's value WITHOUT creating the cell. Asking the library for a
        cell that is not there makes one, so reading a far-away blank (=Z99999)
        grew the sheet, and every whole-column range after it walked 99,999
        rows."""
        held = getattr(ws, "_cells", None)
        if isinstance(held, dict):
            cell = held.get((row, col))
            return None if cell is None else cell.value
        return ws.cell(row=row, column=col).value

    def saved(self, sheet: str, col: int, row: int) -> Any:
        """The value Excel saved in the cell (None for a formula never saved)."""
        return self._peek(self._sheet(self._wb, sheet), row, col)

    def formula(self, sheet: str, col: int, row: int) -> "str | None":
        """The cell's formula text, or None when it holds a plain value."""
        v = self._peek(self._sheet(self._formulas(), sheet), row, col)
        return v if isinstance(v, str) and v.startswith("=") else None

    def max_row(self, sheet: str) -> int:
        last = int(self._sheet(self._wb, sheet).max_row or 1)
        if self.overrides:
            # A what-if may set a cell BELOW the data ("add an expense of 500"
            # in the first empty row). A whole-column range (B:B) is read as
            # far as this, so without the set cell =SUM(B:B) did not change.
            name = self.canon(sheet).lower()
            last = max([last] + [r for (s, _c, r) in self.overrides if s == name])
        return last

    def max_col(self, sheet: str) -> int:
        last = int(self._sheet(self._wb, sheet).max_column or 1)
        if self.overrides:
            name = self.canon(sheet).lower()
            last = max([last] + [c for (s, c, _r) in self.overrides if s == name])
        return last

    def defined(self, name: str) -> "tuple[str, str] | None":
        """``(sheet, "A1" or "A1:B9")`` a defined name points at, or None."""
        wb = self._wb
        if wb is None:
            return None
        try:
            names = wb.defined_names
            dn = names.get(name) if hasattr(names, "get") else None
            if dn is None:
                for key in list(names.keys() if hasattr(names, "keys") else []):
                    if str(key).lower() == name.lower():
                        dn = names[key]
                        break
            if dn is None:
                return None
            for title, coord in dn.destinations:
                return str(title), str(coord).replace("$", "")
        except Exception:  # noqa: BLE001 - an odd name is simply not resolved
            return None
        return None

    def canon(self, sheet: str) -> str:
        """The sheet's own name, whatever case it was written in."""
        return str(self._sheet(self._wb, sheet).title)

    def reset(self) -> None:
        """Forget every worked-out value (after ``overrides`` changed)."""
        self._memo.clear()
        self._stack.clear()
        self.failed.clear()
        self._reads = 0

    # -- computed access ----------------------------------------------------
    def get(self, sheet: str, col: int, row: int) -> Any:
        self._reads += 1
        if self._reads > _MAX_READS:
            raise FormulaError("this formula reads too many cells to work out here")
        if self.overrides:
            held = self.overrides.get((self.canon(sheet).lower(), col, row), _NO_OVERRIDE)
            if held is not _NO_OVERRIDE:
                return _norm(held)
        v = None if (self.fresh and self._is_formula(sheet, col, row)) else self.saved(sheet, col, row)
        if v is not None:
            if isinstance(v, str) and v.startswith("#") and v in _ERROR_CODES:
                raise ExcelError(v, f"saved in {sheet}!{_col_letters(col)}{row}")
            return _norm(v)
        key = (sheet, col, row)
        if key in self._memo:
            got = self._memo[key]
            if isinstance(got, FormulaError):
                raise got
            return got
        try:
            f = self.formula(sheet, col, row)
        except FormulaError:
            raise
        except Exception:  # noqa: BLE001 - no formula view: the cell is blank
            f = None
        if f is None:
            return None
        where = f"{sheet}!{_col_letters(col)}{row}"
        if key in self._stack:
            err = FormulaError(f"circular reference at {where}")
            self._memo[key] = err
            raise err
        if len(self._stack) >= _MAX_DEPTH:
            raise FormulaError(f"the chain of formulas behind {where} is too deep to work out here")
        self._stack.append(key)
        try:
            value = _evaluate(f, self, sheet)
        except FormulaError as exc:
            self._memo[key] = exc
            self.failed.setdefault(where, str(exc))
            raise
        finally:
            self._stack.pop()
        self._memo[key] = value
        self.computed += 1
        return value

    def _is_formula(self, sheet: str, col: int, row: int) -> bool:
        try:
            return self.formula(sheet, col, row) is not None
        except Exception:  # noqa: BLE001 - no formula view: read it as a value
            return False

    def close(self) -> None:
        for wb in (self._wb, self._fwb):
            try:
                if wb is not None:
                    wb.close()
            except Exception:  # noqa: BLE001
                pass


_ERROR_CODES = frozenset({"#N/A", "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!"})
_NO_OVERRIDE = object()


def _tokenize(src: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RX.match(src, pos)
        if m is None:
            raise FormulaError(f"cannot read the formula at {src[pos:pos + 12]!r}")
        pos = m.end()
        kind = m.lastgroup or ""
        if kind != "ws":
            tokens.append((kind, m.group(kind)))
    tokens.append(("end", ""))
    return tokens


class _Range:
    """A rectangle of cells; meaningful inside the functions that take one."""

    def __init__(self, grid: Grid, sheet: str, c1: int, r1: int, c2: int, r2: int) -> None:
        self.grid, self.sheet = grid, sheet
        self.c1, self.c2 = min(c1, c2), max(c1, c2)
        self.r1, self.r2 = min(r1, r2), max(r1, r2)
        if self.width * self.height > _MAX_RANGE_CELLS:
            raise FormulaError("range too large to evaluate")

    @property
    def width(self) -> int:
        return self.c2 - self.c1 + 1

    @property
    def height(self) -> int:
        return self.r2 - self.r1 + 1

    def cell(self, r: int, c: int) -> Any:
        """1-based within the range."""
        if not (1 <= r <= self.height and 1 <= c <= self.width):
            raise ExcelError("#REF!", "outside the range")
        return self.grid.get(self.sheet, self.c1 + c - 1, self.r1 + r - 1)

    def values(self) -> list[Any]:
        return [
            self.grid.get(self.sheet, c, r)
            for r in range(self.r1, self.r2 + 1)
            for c in range(self.c1, self.c2 + 1)
        ]

    def rows(self) -> list[list[Any]]:
        return [
            [self.grid.get(self.sheet, c, r) for c in range(self.c1, self.c2 + 1)]
            for r in range(self.r1, self.r2 + 1)
        ]


Thunk = Callable[[], Any]


class _Parser:
    """Recursive descent over the tokens. Every method returns a THUNK (a
    function that computes the value), so nothing is evaluated while parsing."""

    def __init__(self, tokens: list[tuple[str, str]], grid: Grid, sheet: str) -> None:
        self.tokens = tokens
        self.pos = 0
        self.grid = grid
        self.sheet = sheet

    def _peek(self) -> tuple[str, str]:
        return self.tokens[self.pos]

    def _take(self) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, value: str) -> None:
        kind, val = self._take()
        if val != value:
            raise FormulaError(f"expected {value!r} but saw {val or 'the end of the formula'!r}")

    def parse(self) -> Thunk:
        node = self._comparison()
        if self._peek()[0] != "end":
            raise FormulaError(f"unexpected {self._peek()[1]!r} in the formula")
        return node

    def _comparison(self) -> Thunk:
        left = self._concat()
        while self._peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self._take()[1]
            right = self._concat()
            left = (lambda a, b, o: lambda: _compare(o, a(), b()))(left, right, op)
        return left

    def _concat(self) -> Thunk:
        left = self._additive()
        while self._peek()[1] == "&":
            self._take()
            right = self._additive()
            left = (lambda a, b: lambda: _as_text(a()) + _as_text(b()))(left, right)
        return left

    def _additive(self) -> Thunk:
        left = self._multiplicative()
        while self._peek()[1] in ("+", "-"):
            op = self._take()[1]
            right = self._multiplicative()
            if op == "+":
                left = (lambda a, b: lambda: _as_num(a()) + _as_num(b()))(left, right)
            else:
                left = (lambda a, b: lambda: _as_num(a()) - _as_num(b()))(left, right)
        return left

    def _multiplicative(self) -> Thunk:
        left = self._power()
        while self._peek()[1] in ("*", "/"):
            op = self._take()[1]
            right = self._power()
            if op == "*":
                left = (lambda a, b: lambda: _as_num(a()) * _as_num(b()))(left, right)
            else:
                left = (lambda a, b: lambda: _divide(_as_num(a()), _as_num(b())))(left, right)
        return left

    def _unary(self) -> Thunk:
        if self._peek()[1] == "-":
            self._take()
            inner = self._unary()
            return lambda: -_as_num(inner())
        if self._peek()[1] == "+":
            self._take()
            return self._unary()
        return self._percent()

    def _power(self) -> Thunk:
        # EXCEL'S ORDER, measured against Excel itself (v1.465.0): a leading
        # minus binds TIGHTER than ^ (=-2^2 is 4, where most languages say -4)
        # and ^ runs LEFT to right (=2^3^2 is 64, not 512).
        left = self._unary()
        while self._peek()[1] == "^":
            self._take()
            exp = self._unary()
            left = (lambda a, b: lambda: _power(_as_num(a()), _as_num(b())))(left, exp)
        return left

    def _percent(self) -> Thunk:
        node = self._primary()
        while self._peek()[1] == "%":
            self._take()
            node = (lambda a: lambda: _as_num(a()) / 100.0)(node)
        return node

    def _primary(self) -> Thunk:
        kind, val = self._take()
        if kind == "number":
            n = float(val)
            return lambda: n
        if kind == "string":
            s = val[1:-1].replace('""', '"')
            return lambda: s
        if kind == "error":
            return lambda: _raise(ExcelError(val))
        if val == "(":
            inner = self._comparison()
            self._expect(")")
            return inner
        if kind == "sheet":
            name = val[:-1]
            if name.startswith("'"):
                name = name[1:-1].replace("''", "'")
            ck, cv = self._take()
            if ck == "colrange":
                return self._colrange(name, cv)
            if ck != "cell":
                raise FormulaError(f"expected a cell after {name!r}!")
            return self._ref(name, cv)
        if kind == "colrange":
            return self._colrange(self.sheet, val)
        if kind == "cell":
            return self._ref(self.sheet, val)
        if kind == "func":
            self._take()  # (
            args: list[Thunk] = []
            if self._peek()[1] != ")":
                args.append(self._argument())
                while self._peek()[1] == ",":
                    self._take()
                    args.append(self._argument())
            self._expect(")")
            name = val.upper()
            if name.startswith("_XLFN."):
                name = name[6:]
            return lambda: _call(name, args, self.grid)
        if kind == "ident":
            upper = val.upper()
            if upper == "TRUE":
                return lambda: True
            if upper == "FALSE":
                return lambda: False
            if val.lower() in self.grid.variables:
                held = self.grid.variables[val.lower()]
                return lambda: held
            target = self.grid.defined(val)
            if target is None:
                raise FormulaError(
                    f"{val!r} is not a cell, a function or a name this workbook defines"
                )
            sheet, coord = target
            return self._named(sheet, coord)
        raise FormulaError(f"unexpected {val or 'end of formula'!r}")

    def _argument(self) -> Thunk:
        # An omitted argument ("IF(A1,,0)") is a blank.
        if self._peek()[1] in (",", ")"):
            return lambda: None
        return self._comparison()

    def _named(self, sheet: str, coord: str) -> Thunk:
        parts = coord.split(":")
        ma = _CELL_RX.match(parts[0])
        if ma is None:
            raise FormulaError(f"the name points at {coord!r}, which is not a cell or a range")
        c1, r1 = _col_num(ma.group(1)), int(ma.group(2))
        if len(parts) == 1:
            grid = self.grid
            return lambda: grid.get(sheet, c1, r1)
        mb = _CELL_RX.match(parts[1])
        if mb is None:
            raise FormulaError(f"the name points at {coord!r}, which is not a cell or a range")
        rng = _Range(self.grid, sheet, c1, r1, _col_num(mb.group(1)), int(mb.group(2)))
        return lambda: rng

    def _colrange(self, sheet: str, text: str) -> Thunk:
        a, b = text.replace("$", "").split(":")
        grid = self.grid

        def build() -> _Range:
            return _Range(grid, sheet, _col_num(a), 1, _col_num(b), max(1, grid.max_row(sheet)))

        return build

    def _ref(self, sheet: str, first: str) -> Thunk:
        """A cell's value, or a range when a ':' follows."""
        ma = _CELL_RX.match(first)
        assert ma is not None  # the tokenizer only produces valid cells
        c1, r1 = _col_num(ma.group(1)), int(ma.group(2))
        grid = self.grid
        if self._peek()[1] == ":":
            self._take()
            nk, nv = self._take()
            if nk == "sheet":  # tolerate Sheet1!A1:Sheet1!B2 (same sheet only)
                nk, nv = self._take()
            if nk != "cell":
                raise FormulaError("expected a cell after ':' in a range")
            mb = _CELL_RX.match(nv)
            assert mb is not None
            rng = _Range(grid, sheet, c1, r1, _col_num(mb.group(1)), int(mb.group(2)))
            return lambda: rng
        return lambda: grid.get(sheet, c1, r1)


def _raise(exc: Exception) -> Any:
    raise exc


# ----------------------------------------------------------------- values ----


def _as_num(v: Any) -> float:
    if isinstance(v, _Range):
        if v.width == 1 and v.height == 1:
            return _as_num(v.cell(1, 1))
        raise FormulaError("a range can only be used inside SUM/AVERAGE/… functions")
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v).strip()
    try:
        if text.endswith("%"):
            return float(text[:-1].replace(",", "")) / 100.0
        return float(text.replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        raise ExcelError("#VALUE!", f"cannot use text {text[:40]!r} as a number") from None


def _as_text(v: Any) -> str:
    if isinstance(v, _Range):
        if v.width == 1 and v.height == 1:
            return _as_text(v.cell(1, 1))
        raise FormulaError("a range cannot be used as text")
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return _number_text(v)
    return str(v)


def _number_text(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(round(v, 10)).rstrip("0").rstrip(".") if "e" not in repr(v) else repr(v)


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low == "true":
            return True
        if low == "false":
            return False
        raise ExcelError("#VALUE!", f"cannot use text {v[:40]!r} as true or false")
    return _as_num(v) != 0


def _scalar(v: Any) -> Any:
    if isinstance(v, _Range):
        if v.width == 1 and v.height == 1:
            return v.cell(1, 1)
        raise FormulaError("a range was given where one value is needed")
    return v


def _divide(a: float, b: float) -> float:
    if b == 0:
        raise ExcelError("#DIV/0!", "division by zero")
    return a / b


def _power(a: float, b: float) -> float:
    try:
        out = a ** b
    except (OverflowError, ZeroDivisionError):
        raise ExcelError("#NUM!", "the power is out of range") from None
    if isinstance(out, complex):
        raise ExcelError("#NUM!", "a negative number cannot take that power")
    return float(out)


def _rank(v: Any) -> tuple[int, Any]:
    """Excel's order across kinds: numbers, then text, then TRUE/FALSE."""
    v = _scalar(v)
    if isinstance(v, bool):
        return (2, v)
    if isinstance(v, str):
        return (1, v.lower())
    # Excel compares numbers at the 15 significant digits it keeps, so
    # 0.1+0.2=0.3 is TRUE there. (Measured against Excel.)
    return (0, float(f"{_as_num(v):.15g}"))


def _compare(op: str, a: Any, b: Any) -> bool:
    a, b = _scalar(a), _scalar(b)
    # A blank compares as 0 to a number and as "" to text.
    if a is None:
        a = "" if isinstance(b, str) else 0.0
    if b is None:
        b = "" if isinstance(a, str) else 0.0
    ka, kb = _rank(a), _rank(b)
    if op == "=":
        return ka == kb
    if op == "<>":
        return ka != kb
    return {"<": ka < kb, ">": ka > kb, "<=": ka <= kb, ">=": ka >= kb}[op]


def excel_round(value: float, digits: int = 0, mode: str = ROUND_HALF_UP) -> float:
    """ROUND as Excel does it: a half goes AWAY from zero, and the number is
    read as the decimal a person sees (2.675 rounds to 2.68, not 2.67)."""
    try:
        # Excel keeps 15 significant digits, and rounds what those say:
        # 750*7.25% is 54.37499999999999 in binary and 54.375 to Excel, so
        # ROUND(...,2) is 54.38 there. (Measured against Excel.)
        d = Decimal(f"{float(value):.15g}")
        quantum = Decimal(1).scaleb(-int(digits))
        return float(d.quantize(quantum, rounding=mode))
    except (InvalidOperation, ValueError, OverflowError):
        raise ExcelError("#NUM!", "cannot round that number") from None


def _flat(args: list[Any]) -> list[Any]:
    out: list[Any] = []
    for a in args:
        if isinstance(a, _Range):
            out.extend(a.values())
        else:
            out.append(a)
    return out


def _numeric_items(args: list[Any]) -> list[float]:
    """The NUMERIC items of the arguments, Excel-style: inside a range, text,
    blanks and TRUE/FALSE are skipped; a literal argument must be a number."""
    out: list[float] = []
    for a in args:
        if isinstance(a, _Range):
            for v in a.values():
                if isinstance(v, bool) or v is None or isinstance(v, str):
                    continue
                if isinstance(v, (int, float)):
                    out.append(float(v))
        elif a is not None:
            out.append(_as_num(a))
    return out


def _criterion(crit: Any) -> Callable[[Any], bool]:
    """A SUMIF/COUNTIF criterion as a test: ">=100", "<>x", "west*", 5."""
    crit = _scalar(crit)
    op, want = "=", crit
    if isinstance(crit, str):
        m = re.match(r"^(<>|<=|>=|=|<|>)(.*)$", crit, re.S)
        if m:
            op, want = m.group(1), m.group(2)
        try:
            want = float(str(want).replace(",", "")) if str(want).strip() != "" else want
        except ValueError:
            pass
    if isinstance(want, bool):
        return lambda v: (v is want) if op == "=" else (v is not want)
    if isinstance(want, (int, float)):
        target = float(want)

        def num_test(v: Any) -> bool:
            if isinstance(v, bool) or v is None or isinstance(v, str):
                if isinstance(v, str):
                    try:
                        v = float(v.replace(",", ""))
                    except ValueError:
                        return op == "<>"
                else:
                    return op == "<>"
            return {"=": v == target, "<>": v != target, "<": v < target,
                    ">": v > target, "<=": v <= target, ">=": v >= target}[op]

        return num_test
    text = str(want if want is not None else "").lower()
    if op in ("=", "<>") and ("*" in text or "?" in text):
        pattern = re.compile(
            "^" + "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in text) + "$",
            re.S,
        )
        return lambda v: bool(pattern.match(_as_text(v).lower())) is (op == "=")

    def text_test(v: Any) -> bool:
        s = "" if v is None else _as_text(v).lower()
        return {"=": s == text, "<>": s != text, "<": s < text,
                ">": s > text, "<=": s <= text, ">=": s >= text}[op]

    return text_test


def _need_range(v: Any, what: str) -> _Range:
    if not isinstance(v, _Range):
        raise FormulaError(f"{what} needs a range of cells")
    return v


def _ifs_mask(pairs: list[Any]) -> list[bool]:
    if len(pairs) % 2:
        raise FormulaError("the ranges and criteria must come in pairs")
    mask: "list[bool] | None" = None
    for i in range(0, len(pairs), 2):
        values = _need_range(pairs[i], "a criteria range").values()
        test = _criterion(pairs[i + 1])
        hits = [test(v) for v in values]
        if mask is None:
            mask = hits
        elif len(hits) != len(mask):
            raise ExcelError("#VALUE!", "the ranges are not the same size")
        else:
            mask = [a and b for a, b in zip(mask, hits)]
    return mask or []


def _date_of(v: Any) -> _dt.datetime:
    return from_serial(_as_num(v))


def _add_months(d: _dt.datetime, months: int) -> tuple[int, int]:
    total = d.year * 12 + (d.month - 1) + months
    return total // 12, total % 12 + 1


def _month_end(year: int, month: int) -> int:
    nxt = _dt.date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - _dt.timedelta(days=1)).day


def _irr(flows: list[float], guess: float) -> float:
    if not any(f > 0 for f in flows) or not any(f < 0 for f in flows):
        raise ExcelError("#NUM!", "IRR needs at least one payment and one receipt")

    def npv(rate: float) -> float:
        return sum(f / (1 + rate) ** i for i, f in enumerate(flows))

    rate = guess
    for _ in range(60):  # Newton, as Excel does
        if rate <= -1:
            break
        value = npv(rate)
        slope = sum(-i * f / (1 + rate) ** (i + 1) for i, f in enumerate(flows))
        if slope == 0:
            break
        nxt = rate - value / slope
        if abs(nxt - rate) < 1e-12:
            return nxt
        rate = nxt
    lo, hi = -0.999999, 10.0  # then bisection
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        raise ExcelError("#NUM!", "IRR did not settle on an answer")
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-12 or hi - lo < 1e-14:
            return mid
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def _text_format(value: Any, fmt: str) -> str:
    f = fmt.strip()
    low = f.lower()
    if any(k in low for k in ("yy", "dd", "mmm")) or low in ("m/d/yyyy", "mm/dd/yyyy"):
        d = _date_of(value)
        out = low
        for token, piece in (("yyyy", f"{d.year:04d}"), ("mmmm", d.strftime("%B")), ("mmm", d.strftime("%b")),
                             ("mm", f"{d.month:02d}"), ("dd", f"{d.day:02d}"), ("yy", f"{d.year % 100:02d}")):
            out = out.replace(token, piece)
        out = re.sub(r"(?<![0-9A-Za-z])m(?![0-9A-Za-z])", str(d.month), out)
        return re.sub(r"(?<![0-9A-Za-z])d(?![0-9A-Za-z])", str(d.day), out)
    m = re.fullmatch(r"(\$?)(#,##0|0)(?:\.(0+))?(%?)", f)
    if not m:
        raise FormulaError(f"TEXT with the format {fmt!r} is not supported here")
    num = _as_num(value) * (100 if m.group(4) else 1)
    digits = len(m.group(3) or "")
    rounded = excel_round(num, digits)
    body = f"{rounded:,.{digits}f}" if m.group(2).startswith("#") else f"{rounded:.{digits}f}"
    if body.startswith("-"):
        return "-" + m.group(1) + body[1:] + m.group(4)
    return m.group(1) + body + m.group(4)


#: Every function this engine works out.
SUPPORTED: tuple[str, ...] = (
    "ABS", "AND", "AVERAGE", "AVERAGEIF", "AVERAGEIFS", "CEILING", "CONCAT", "CONCATENATE",
    "COUNT", "COUNTA", "COUNTBLANK", "COUNTIF", "COUNTIFS", "DATE", "DAY", "DAYS", "DDB",
    "EDATE", "EOMONTH", "FALSE", "FLOOR", "FV", "HLOOKUP", "IF", "IFERROR", "IFNA", "INDEX",
    "INT", "IRR", "ISBLANK", "ISERROR", "ISNA", "ISNUMBER", "ISTEXT", "LARGE", "LEFT", "LEN",
    "LOWER", "MATCH", "MAX", "MAXIFS", "MEDIAN", "MID", "MIN", "MINIFS", "MOD", "MONTH", "NA",
    "NOT", "NPV", "OR", "PMT", "POWER", "PRODUCT", "PROPER", "PV", "RIGHT", "ROUND",
    "ROUNDDOWN", "ROUNDUP", "SIGN", "SLN", "SMALL", "SQRT", "SUBTOTAL", "SUM", "SUMIF",
    "SUMIFS", "SUMPRODUCT", "SYD", "TEXT", "TODAY", "TRIM", "TRUE", "TRUNC", "UPPER", "VALUE",
    "VLOOKUP", "XLOOKUP", "YEAR",
)

_LAZY = frozenset({"IF", "IFERROR", "IFNA", "AND", "OR", "ISERROR", "ISNA"})


def _call(name: str, thunks: list[Thunk], grid: Grid) -> Any:  # noqa: C901 - one flat table
    if name in _LAZY:
        return _call_lazy(name, thunks)
    args = [t() for t in thunks]
    n = len(args)

    def need(lo: int, hi: "int | None" = None) -> None:
        top = lo if hi is None else hi
        if not (lo <= n <= top):
            span = str(lo) if lo == top else f"{lo} to {top}"
            raise FormulaError(f"{name} takes {span} argument(s), not {n}")

    if name == "SUM":
        return math.fsum(_numeric_items(args))
    if name == "PRODUCT":
        out = 1.0
        for v in _numeric_items(args):
            out *= v
        return out
    if name == "AVERAGE":
        items = _numeric_items(args)
        if not items:
            raise ExcelError("#DIV/0!", "AVERAGE of no numeric values")
        return math.fsum(items) / len(items)
    if name == "MEDIAN":
        items = sorted(_numeric_items(args))
        if not items:
            raise ExcelError("#NUM!", "MEDIAN of no numeric values")
        mid = len(items) // 2
        return items[mid] if len(items) % 2 else (items[mid - 1] + items[mid]) / 2
    if name == "MIN":
        items = _numeric_items(args)
        return min(items) if items else 0.0
    if name == "MAX":
        items = _numeric_items(args)
        return max(items) if items else 0.0
    if name in ("LARGE", "SMALL"):
        need(2)
        items = sorted(_numeric_items(args[:1]), reverse=name == "LARGE")
        k = int(_as_num(args[1]))
        if not 1 <= k <= len(items):
            raise ExcelError("#NUM!", f"{name} has no item number {k}")
        return items[k - 1]
    if name == "COUNT":
        return float(len(_numeric_items(args)))
    if name == "COUNTA":
        return float(sum(1 for v in _flat(args) if v is not None and v != ""))
    if name == "COUNTBLANK":
        need(1)
        return float(sum(1 for v in _need_range(args[0], name).values() if v is None or v == ""))
    if name == "ABS":
        need(1)
        return abs(_as_num(args[0]))
    if name == "SIGN":
        need(1)
        x = _as_num(args[0])
        return float((x > 0) - (x < 0))
    if name == "SQRT":
        need(1)
        x = _as_num(args[0])
        if x < 0:
            raise ExcelError("#NUM!", "square root of a negative number")
        return math.sqrt(x)
    if name == "POWER":
        need(2)
        return _power(_as_num(args[0]), _as_num(args[1]))
    if name == "MOD":
        need(2)
        a, b = _as_num(args[0]), _as_num(args[1])
        if b == 0:
            raise ExcelError("#DIV/0!", "MOD by zero")
        return a - b * math.floor(a / b)
    if name == "INT":
        need(1)
        return float(math.floor(_as_num(args[0])))
    if name in ("ROUND", "ROUNDUP", "ROUNDDOWN", "TRUNC"):
        need(1, 2)
        digits = int(_as_num(args[1])) if n == 2 else 0
        mode = {"ROUND": ROUND_HALF_UP, "ROUNDUP": ROUND_UP}.get(name, ROUND_DOWN)
        return excel_round(_as_num(args[0]), digits, mode)
    if name in ("CEILING", "FLOOR"):
        need(1, 2)
        x = _as_num(args[0])
        step = _as_num(args[1]) if n == 2 else 1.0
        if step == 0:
            return 0.0
        q = Decimal(repr(x)) / Decimal(repr(step))
        whole = q.to_integral_value(rounding="ROUND_CEILING" if name == "CEILING" else "ROUND_FLOOR")
        return float(whole * Decimal(repr(step)))
    if name == "NOT":
        need(1)
        return not _as_bool(_scalar(args[0]))
    if name == "TRUE":
        return True
    if name == "FALSE":
        return False
    if name == "NA":
        raise ExcelError("#N/A")
    if name == "SUBTOTAL":
        if n < 2:
            raise FormulaError("SUBTOTAL takes (function_num, range…)")
        fn = int(_as_num(args[0])) % 100
        table = {1: "AVERAGE", 2: "COUNT", 3: "COUNTA", 4: "MAX", 5: "MIN", 6: "PRODUCT", 9: "SUM"}
        if fn not in table:
            raise FormulaError(f"SUBTOTAL({fn}, …) is not supported (9 = sum, 1 = average)")
        rest = args[1:]
        return _call(table[fn], [(lambda v: lambda: v)(a) for a in rest], grid)
    if name == "SUMPRODUCT":
        if not args:
            raise FormulaError("SUMPRODUCT needs at least one range")
        cols = [_need_range(a, name).values() for a in args]
        if len({len(c) for c in cols}) != 1:
            raise ExcelError("#VALUE!", "the ranges are not the same size")
        total = []
        for row in zip(*cols):
            p = 1.0
            for v in row:
                p *= float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
            total.append(p)
        return math.fsum(total)
    if name in ("SUMIF", "AVERAGEIF"):
        need(2, 3)
        crit_range = _need_range(args[0], name)
        values = (_need_range(args[2], name) if n == 3 else crit_range).values()
        test = _criterion(args[1])
        picked = [float(v) for c, v in zip(crit_range.values(), values)
                  if test(c) and isinstance(v, (int, float)) and not isinstance(v, bool)]
        if name == "SUMIF":
            return math.fsum(picked)
        if not picked:
            raise ExcelError("#DIV/0!", "no cells matched")
        return math.fsum(picked) / len(picked)
    if name == "COUNTIF":
        need(2)
        test = _criterion(args[1])
        return float(sum(1 for v in _need_range(args[0], name).values() if test(v)))
    if name == "COUNTIFS":
        return float(sum(_ifs_mask(args)))
    if name in ("SUMIFS", "AVERAGEIFS", "MAXIFS", "MINIFS"):
        if n < 3:
            raise FormulaError(f"{name} takes a range, then pairs of range and criterion")
        values = _need_range(args[0], name).values()
        mask = _ifs_mask(args[1:])
        if len(mask) != len(values):
            raise ExcelError("#VALUE!", "the ranges are not the same size")
        picked = [float(v) for v, hit in zip(values, mask)
                  if hit and isinstance(v, (int, float)) and not isinstance(v, bool)]
        if name == "SUMIFS":
            return math.fsum(picked)
        if name == "AVERAGEIFS":
            if not picked:
                raise ExcelError("#DIV/0!", "no cells matched")
            return math.fsum(picked) / len(picked)
        return (max(picked) if name == "MAXIFS" else min(picked)) if picked else 0.0
    if name in ("VLOOKUP", "HLOOKUP"):
        need(3, 4)
        rng = _need_range(args[1], name)
        table = rng.rows()
        if name == "HLOOKUP":
            table = [list(col) for col in zip(*table)]
        index = int(_as_num(args[2]))
        if index < 1 or index > len(table[0]):
            raise ExcelError("#REF!", f"{name} asks for column {index} of a {len(table[0])}-wide table")
        approx = True if n < 4 or args[3] is None else _as_bool(_scalar(args[3]))
        pos = _match([row[0] for row in table], _scalar(args[0]), 1 if approx else 0)
        return table[pos][index - 1]
    if name == "MATCH":
        need(2, 3)
        kind = 1 if n < 3 or args[2] is None else int(_as_num(args[2]))
        return float(_match(_need_range(args[1], name).values(), _scalar(args[0]), kind) + 1)
    if name == "INDEX":
        need(2, 3)
        rng = _need_range(args[0], name)
        r = int(_as_num(args[1])) if args[1] is not None else 0
        c = int(_as_num(args[2])) if n == 3 and args[2] is not None else 0
        if rng.height == 1 and n == 2:
            r, c = 1, r
        return rng.cell(r or 1, c or 1)
    if name == "XLOOKUP":
        need(3, 4)
        keys = _need_range(args[1], name).values()
        out = _need_range(args[2], name).values()
        if len(keys) != len(out):
            raise ExcelError("#VALUE!", "the lookup and return ranges are not the same size")
        try:
            return out[_match(keys, _scalar(args[0]), 0)]
        except ExcelError:
            if n == 4:
                return _scalar(args[3])
            raise
    if name in ("CONCAT", "CONCATENATE"):
        return "".join(_as_text(v) for v in _flat(args))
    if name in ("LEFT", "RIGHT"):
        need(1, 2)
        text = _as_text(args[0])
        count = int(_as_num(args[1])) if n == 2 else 1
        if count < 0:
            raise ExcelError("#VALUE!", f"{name} cannot take a negative count")
        return text[:count] if name == "LEFT" else (text[-count:] if count else "")
    if name == "MID":
        need(3)
        start, count = int(_as_num(args[1])), int(_as_num(args[2]))
        if start < 1 or count < 0:
            raise ExcelError("#VALUE!", "MID starts at 1 and takes a count of 0 or more")
        return _as_text(args[0])[start - 1:start - 1 + count]
    if name == "LEN":
        need(1)
        return float(len(_as_text(args[0])))
    if name == "TRIM":
        need(1)
        return " ".join(_as_text(args[0]).split())
    if name in ("UPPER", "LOWER", "PROPER"):
        need(1)
        text = _as_text(args[0])
        return text.upper() if name == "UPPER" else text.lower() if name == "LOWER" else text.title()
    if name == "VALUE":
        need(1)
        return _as_num(_scalar(args[0]))
    if name == "TEXT":
        need(2)
        return _text_format(_scalar(args[0]), _as_text(args[1]))
    if name in ("ISBLANK", "ISNUMBER", "ISTEXT"):
        need(1)
        v = _scalar(args[0])
        if name == "ISBLANK":
            return v is None
        if name == "ISTEXT":
            return isinstance(v, str)
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if name == "TODAY":
        need(0)
        return to_serial(_dt.date.today())
    if name == "DATE":
        need(3)
        y, m, d = int(_as_num(args[0])), int(_as_num(args[1])), int(_as_num(args[2]))
        year, month = _add_months(_dt.datetime(y if y >= 1900 else y + 1900, 1, 1), m - 1)
        try:
            return to_serial(_dt.datetime(year, month, 1) + _dt.timedelta(days=d - 1))
        except (ValueError, OverflowError):
            raise ExcelError("#NUM!", "not a valid date") from None
    if name in ("YEAR", "MONTH", "DAY"):
        need(1)
        d = _date_of(_scalar(args[0]))
        return float({"YEAR": d.year, "MONTH": d.month, "DAY": d.day}[name])
    if name == "DAYS":
        need(2)
        return float(math.floor(_as_num(args[0])) - math.floor(_as_num(args[1])))
    if name in ("EDATE", "EOMONTH"):
        need(2)
        start = _date_of(_scalar(args[0]))
        year, month = _add_months(start, int(_as_num(args[1])))
        last = _month_end(year, month)
        day = last if name == "EOMONTH" else min(start.day, last)
        return to_serial(_dt.datetime(year, month, day))
    if name in ("PMT", "FV", "PV"):
        need(3, 5)
        rate, nper, third = _as_num(args[0]), _as_num(args[1]), _as_num(args[2])
        fourth = _as_num(args[3]) if n >= 4 and args[3] is not None else 0.0
        due = 1.0 if n == 5 and _as_num(args[4]) != 0 else 0.0
        if nper == 0 and name == "PMT":
            raise ExcelError("#NUM!", "PMT needs at least one period")
        if rate == 0:
            if name == "PMT":
                return -(third + fourth) / nper
            return -(fourth + third * nper)
        growth = (1 + rate) ** nper
        annuity = (growth - 1) / rate * (1 + rate * due)
        if name == "PMT":  # third = pv, fourth = fv
            return -(third * growth + fourth) / annuity
        if name == "FV":  # third = pmt, fourth = pv
            return -(fourth * growth + third * annuity)
        return -(fourth + third * annuity) / growth  # PV: third = pmt, fourth = fv
    if name == "NPV":
        if n < 2:
            raise FormulaError("NPV takes a rate and at least one value")
        rate = _as_num(args[0])
        flows = _numeric_items(args[1:])
        return math.fsum(f / (1 + rate) ** (i + 1) for i, f in enumerate(flows))
    if name == "IRR":
        need(1, 2)
        return _irr(_numeric_items(args[:1]), _as_num(args[1]) if n == 2 else 0.1)
    if name == "SLN":
        need(3)
        return _divide(_as_num(args[0]) - _as_num(args[1]), _as_num(args[2]))
    if name == "SYD":
        need(4)
        cost, salvage, life, per = (_as_num(a) for a in args)
        return (cost - salvage) * (life - per + 1) * 2 / (life * (life + 1))
    if name == "DDB":
        need(4, 5)
        cost, salvage, life, period = (_as_num(a) for a in args[:4])
        factor = _as_num(args[4]) if n == 5 else 2.0
        if life <= 0 or period < 1 or period > life:
            raise ExcelError("#NUM!", "DDB needs a period between 1 and the life")
        book, dep = cost, 0.0
        for _ in range(int(math.ceil(period))):
            dep = min(book * factor / life, max(0.0, book - salvage))
            book -= dep
        return dep
    raise FormulaError(
        f"function {name} is not supported by the checker — it cannot be worked out here. "
        f"Supported: {', '.join(SUPPORTED)}"
    )


def _call_lazy(name: str, thunks: list[Thunk]) -> Any:
    n = len(thunks)
    if name == "IF":
        if n not in (2, 3):
            raise FormulaError("IF takes (condition, then, else)")
        if _as_bool(_scalar(thunks[0]())):
            return _scalar(thunks[1]())
        return _scalar(thunks[2]()) if n == 3 else False
    if name in ("IFERROR", "IFNA"):
        if n != 2:
            raise FormulaError(f"{name} takes (value, value if it fails)")
        try:
            return _scalar(thunks[0]())
        except ExcelError as exc:
            if name == "IFNA" and exc.code != "#N/A":
                raise
            return _scalar(thunks[1]())
    if name in ("ISERROR", "ISNA"):
        if n != 1:
            raise FormulaError(f"{name} takes one value")
        try:
            _scalar(thunks[0]())
        except ExcelError as exc:
            return name == "ISERROR" or exc.code == "#N/A"
        return False
    if name in ("AND", "OR"):
        if not n:
            raise FormulaError(f"{name} needs at least one condition")
        seen: list[bool] = []
        for t in thunks:
            for v in _flat([t()]):
                if v is None or isinstance(v, str):
                    continue
                seen.append(_as_bool(v))
        if not seen:
            raise ExcelError("#VALUE!", f"{name} was given nothing to test")
        return all(seen) if name == "AND" else any(seen)
    raise FormulaError(f"function {name} is not supported by the checker")


def _match(keys: list[Any], want: Any, kind: int) -> int:
    """0-based position of ``want`` in ``keys`` (0 = exact, 1 = the largest not
    above it in an ascending list, -1 = the smallest not below it)."""
    if kind == 0:
        if isinstance(want, str) and ("*" in want or "?" in want):
            test = _criterion(want)
            for i, k in enumerate(keys):
                if isinstance(k, str) and test(k):
                    return i
        else:
            target = _rank(want)
            for i, k in enumerate(keys):
                if k is not None and _rank(k) == target:
                    return i
        raise ExcelError("#N/A", f"{_as_text(want)!r} was not found")
    target = _rank(want)
    best = -1
    for i, k in enumerate(keys):
        if k is None:
            continue
        rk = _rank(k)
        if rk[0] != target[0]:
            continue
        if kind > 0 and rk <= target:
            best = i
        elif kind < 0 and rk >= target:
            best = i
    if best < 0:
        raise ExcelError("#N/A", f"nothing fits {_as_text(want)!r}")
    return best


def _evaluate(formula: str, grid: Grid, sheet: str) -> Any:
    src = (formula or "").strip()
    if src.startswith("="):
        src = src[1:]
    if not src:
        raise FormulaError("empty formula")
    if len(src) > 8192:
        raise FormulaError("the formula is too long to work out here")
    try:
        return _Parser(_tokenize(src), grid, sheet).parse()()
    except RecursionError:
        raise FormulaError("the formula is nested too deeply to work out here") from None
    except (OverflowError, ZeroDivisionError):
        raise ExcelError("#NUM!", "the number is out of range") from None


def evaluate_formula(formula: str, grid: Grid, sheet: str) -> Any:
    """Work out *formula* (with or without a leading '=') against *grid*,
    resolving bare cell references on *sheet*. Raises :class:`FormulaError`
    with an honest message for anything it cannot do."""
    result = _evaluate(formula, grid, sheet)
    if isinstance(result, _Range):
        if result.width == 1 and result.height == 1:
            return result.cell(1, 1)
        raise FormulaError("a bare range is not a value — wrap it in SUM(...)")
    return result


#: Functions newer than the file format's first version. Excel stores them
#: with a prefix; a formula written WITHOUT it opens as #NAME? (measured:
#: =XLOOKUP, =IFNA, =MAXIFS, =CONCAT and =DAYS written plainly all did).
_PREFIXED: dict[str, str] = {
    **{name: "_xlfn." for name in (
        "IFNA", "IFS", "SWITCH", "XOR", "MAXIFS", "MINIFS", "XLOOKUP", "XMATCH", "CONCAT",
        "TEXTJOIN", "DAYS", "ISOWEEKNUM", "NUMBERVALUE", "ISFORMULA", "FORMULATEXT",
        "SHEET", "SHEETS", "UNIQUE", "SEQUENCE", "RANDARRAY", "SORTBY", "LET", "LAMBDA",
        "TEXTBEFORE", "TEXTAFTER", "TEXTSPLIT", "VSTACK", "HSTACK", "TAKE", "DROP",
        "STDEV.S", "STDEV.P", "VAR.S", "VAR.P", "PERCENTILE.INC", "PERCENTILE.EXC",
        "QUARTILE.INC", "QUARTILE.EXC", "RANK.EQ", "RANK.AVG", "MODE.SNGL", "MODE.MULT",
        "COVARIANCE.P", "COVARIANCE.S", "CEILING.MATH", "FLOOR.MATH", "NORM.DIST",
        "NORM.INV", "NORM.S.DIST", "NORM.S.INV", "T.TEST", "F.TEST", "AGGREGATE",
        "NETWORKDAYS.INTL", "WORKDAY.INTL", "ARRAYTOTEXT", "VALUETOTEXT",
    )},
    "FILTER": "_xlfn._xlws.", "SORT": "_xlfn._xlws.",
}
_FOR_FILE_RX = re.compile(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9.]*)(?=\s*\()')
_PREFIX_RX = re.compile(r"_xlfn\.(?:_xlws\.)?", re.IGNORECASE)


class OutsideFormula(ValueError):
    """A formula that would make Excel reach OUTSIDE the workbook when the
    file is opened (v1.469.0): =WEBSERVICE(...&A1) sends a cell to a web
    address, a DDE link runs a program, a link to a network share reads it.
    A workbook's text is someone else's words and a model may follow them,
    so such a formula is never written from chat; the user types it in Excel
    themselves. The rule is the open workbook's own
    (``excel_worker.reaches_out``), so a saved file and an open one agree."""


def outside_reason(formula: Any) -> str:
    """Why *formula* would reach outside the workbook, or ""."""
    from ..excel_worker import reaches_out

    return reaches_out(formula) if isinstance(formula, str) else ""


def for_file(formula: Any) -> Any:
    """*formula* as Excel must find it in a file: newer functions carry their
    prefix. Text inside quotes is never touched; a formula already prefixed is
    left alone; anything that is not a formula is returned as it came.

    EVERY place that writes a formula into a file goes through here, so this
    is also where a formula that would reach outside the workbook is refused
    (:class:`OutsideFormula`)."""
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    why = outside_reason(formula)
    if why:
        raise OutsideFormula(f"that formula is not written from chat: {why}. The user can type it in Excel themselves.")

    def put(m: "re.Match[str]") -> str:
        name = m.group(1)
        if name is None:
            return m.group(0)
        prefix = _PREFIXED.get(name.upper())
        return f"{prefix}{name}" if prefix else name

    return _FOR_FILE_RX.sub(put, formula)


def for_reading(formula: Any) -> Any:
    """*formula* as a person types it: the file's prefixes removed."""
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    return _PREFIX_RX.sub("", formula)


def calculate(expression: str) -> Any:
    """Work out an expression that needs no workbook: ``=ROUND(612.40*2+431, 2)``."""
    return evaluate_formula(expression, Grid(None), "")
