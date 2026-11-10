"""The live-Excel child (v1.466.0): talks to an Excel the USER has open.

WHY A CHILD PROCESS. A COM call into another program can wait forever (Excel is
showing a dialog box, or the user is typing in a cell), and a native call that
waits holds this interpreter. The v1.410.2 rule stands: nothing that can block
inside Windows runs in the daemon. ``documents/excel_live`` starts this module
as a short-lived process, hands it ONE JSON request on stdin, reads ONE JSON
answer from stdout, and kills the process at its deadline. This is the only
module that imports ``pythoncom`` / ``win32com``.

WHAT IT NEVER DOES. It never starts Excel (it only attaches to workbooks found
in Windows' table of running objects), never opens, saves or closes a
workbook, and never turns off Excel's events, screen updating or calculation.
The one switch it touches is ``DisplayAlerts``, for the moment a sheet is
deleted, and it puts it back in a ``finally``.

REQUESTS (``{"op": ...}``):

* ``look``     what is open, what is selected, and optionally a range's cells
* ``snapshot`` ``SaveCopyAs`` to a path the daemon chose (the workbook's own
               saved state and path are untouched; measured on Excel 16.0)
* ``plan``     check a list of edit steps and say what they would touch
* ``apply``    make the steps, ALL OR NOTHING, writing what each step replaced
               to an undo file BEFORE the step runs
* ``restore``  put back what one ``apply`` changed, leaving alone anything
               changed again since
* ``goto``     show a cell in Excel

Every answer is ``{"ok": bool, ...}``; a refusal carries ``error`` (one plain
sentence) and ``code``. The pure helpers at the top need no Excel and are what
the test suite exercises on a PC without it.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import re
import sys
import time
from typing import Any

# --------------------------------------------------------------- pure helpers --

#: Excel's error values arrive as these integers (0x800A0000 | n).
ERROR_NAMES = {
    2000: "#NULL!", 2007: "#DIV/0!", 2015: "#VALUE!", 2023: "#REF!", 2029: "#NAME?",
    2036: "#NUM!", 2042: "#N/A", 2043: "#GETTING_DATA", 2045: "#SPILL!", 2046: "#CONNECT!",
    2047: "#BLOCKED!", 2048: "#UNKNOWN!", 2049: "#FIELD!", 2050: "#CALC!",
}
MAX_OPS = 60
MAX_CELLS_PER_STEP = 50_000
MAX_LOOK_CELLS = 20_000
DEFAULT_LOOK_CELLS = 2_000
MAX_CHANGES_LISTED = 200
#: Format properties a ``set`` step may touch, and so must be able to put back.
FORMAT_KEYS = ("number_format", "bold", "italic", "fill", "font_color", "align", "wrap", "border", "font_size")
_ALIGN = {"left": -4131, "center": -4108, "right": -4152, "general": 1}
_ALIGN_BACK = {v: k for k, v in _ALIGN.items()}
_CELL_RX = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")
_COLS_RX = re.compile(r"^\$?([A-Za-z]{1,3})(?::\$?([A-Za-z]{1,3}))?$")
_ROWS_RX = re.compile(r"^\$?(\d{1,7})(?::\$?(\d{1,7}))?$")
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")


class Refusal(Exception):
    """A request this worker will not carry out, said in one plain sentence."""

    def __init__(self, words: str, code: str = "refused") -> None:
        super().__init__(words)
        self.words, self.code = words, code


def col_num(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letter(n: int) -> str:
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def parse_range(text: Any) -> "tuple[int, int, int, int]":
    """``"B2"`` / ``"B2:D9"`` / ``"B:D"`` / ``"3:7"`` as (row1, col1, row2,
    col2), 1-based; whole columns and rows use Excel's sheet limits."""
    raw = str(text or "").strip().replace("$", "")
    if "!" in raw:
        raise Refusal(f"give the sheet in 'sheet', not inside the range ({raw})", "bad_range")
    a, _, b = raw.partition(":")
    ma, mb = _CELL_RX.match(a), _CELL_RX.match(b or a)
    if ma and mb:
        r1, c1, r2, c2 = int(ma.group(2)), col_num(ma.group(1)), int(mb.group(2)), col_num(mb.group(1))
    else:
        mc, mr = _COLS_RX.match(raw), _ROWS_RX.match(raw)
        if mc:
            c1, c2 = col_num(mc.group(1)), col_num(mc.group(2) or mc.group(1))
            r1, r2 = 1, 1_048_576
        elif mr:
            r1, r2 = int(mr.group(1)), int(mr.group(2) or mr.group(1))
            c1, c2 = 1, 16_384
        else:
            raise Refusal(f"'{raw}' is not a cell or range like B2 or B2:D9", "bad_range")
    r1, r2, c1, c2 = min(r1, r2), max(r1, r2), min(c1, c2), max(c1, c2)
    if not (1 <= r1 <= r2 <= 1_048_576 and 1 <= c1 <= c2 <= 16_384):
        raise Refusal(f"'{raw}' is outside the sheet", "bad_range")
    return r1, c1, r2, c2


def a1(r1: int, c1: int, r2: "int | None" = None, c2: "int | None" = None) -> str:
    first = f"{col_letter(c1)}{r1}"
    if r2 is None or c2 is None or (r1 == r2 and c1 == c2):
        return first
    return f"{first}:{col_letter(c2)}{r2}"


def bgr(hex_colour: Any) -> int:
    """``"FFF2CC"`` (red, green, blue) as the number Excel stores."""
    s = str(hex_colour or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", s):
        raise Refusal(f"'{hex_colour}' is not a colour like FFF2CC", "bad_colour")
    return int(s[0:2], 16) + int(s[2:4], 16) * 256 + int(s[4:6], 16) * 65536


def plain(v: Any) -> Any:
    """One cell value as JSON: an error by its name, a date as ISO text, a
    whole number without its ``.0``."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):  # Excel hands numbers over as floats; an int is an error value
        return ERROR_NAMES.get(v & 0xFFFF, "#ERROR!") if v < 0 else v
    if isinstance(v, decimal.Decimal):
        v = float(v)
    if isinstance(v, float):
        return int(v) if v == int(v) and abs(v) < 1e15 else v
    if isinstance(v, _dt.datetime):
        if (v.hour, v.minute, v.second) == (0, 0, 0):
            return v.strftime("%Y-%m-%d")
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def grid(v: Any, rows: int, cols: int) -> "list[list[Any]]":
    """What Excel returns for a range (a bare value for one cell, a tuple of
    tuples otherwise) as a rows x cols list."""
    if rows == 1 and cols == 1 and not isinstance(v, tuple):
        return [[v]]
    out = [list(r) if isinstance(r, tuple) else [r] for r in (v or ())]
    return out


def is_error(v: Any) -> bool:
    return isinstance(v, str) and v.startswith("#") and v in ERROR_NAMES.values()


def sheet_name_problem(name: Any) -> str:
    s = str(name or "")
    if not s.strip() or len(s) > 31 or _SHEET_BAD.search(s) or s.startswith("'") or s.endswith("'"):
        return f"'{s}' cannot be a sheet name (1 to 31 characters, none of [ ] : * ? / \\)"
    return ""


def to_cell_text(v: Any) -> Any:
    """A value from the request as what is assigned to a cell."""
    if v is None:
        return ""
    if isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


# ------------------------------------------------------------------ attaching --

_BUSY = {-2147418111, -2147417846, -2146777998}
BUSY_WORDS = (
    "Excel is busy and did not take the request. Finish typing in the cell (press Enter or Esc) "
    "or close the dialog box that is open in Excel, then try again."
)


def _com():
    import pythoncom  # noqa: PLC0415
    import win32com.client as wc  # noqa: PLC0415

    return pythoncom, wc


def _is_busy(exc: BaseException) -> bool:
    return getattr(exc, "hresult", None) in _BUSY or (
        isinstance(getattr(exc, "excepinfo", None), tuple) and exc.excepinfo[5] in _BUSY  # type: ignore[index]
    )


def _excel_words(exc: BaseException) -> str:
    """Excel's own message from a COM error, else the error's name."""
    info = getattr(exc, "excepinfo", None)
    if isinstance(info, tuple) and len(info) > 2 and info[2]:
        return str(info[2]).strip()
    return type(exc).__name__


def _apps() -> "list[Any]":
    """Every running Excel that has registered itself, front-most first.
    Never starts one."""
    pythoncom, wc = _com()
    found: dict[int, Any] = {}

    def add(obj: Any) -> None:
        try:
            app = obj.Application
            if "excel" not in str(app.Name).lower():
                return
            found.setdefault(int(app.Hwnd), app)
        except Exception:  # noqa: BLE001 — not Excel, or gone
            return

    try:
        add(wc.GetActiveObject("Excel.Application"))
    except Exception:  # noqa: BLE001 — none running
        pass
    try:
        rot = pythoncom.GetRunningObjectTable()
        ctx = pythoncom.CreateBindCtx(0)
        for mk in rot:
            try:
                name = str(mk.GetDisplayName(ctx, None))
            except Exception:  # noqa: BLE001
                continue
            low = name.lower()
            looks = low.startswith("!{00024500") or re.search(r"\.(xl[a-z]{1,2}|csv)$", low) or ("\\" not in name and "." not in name and "{" not in name)
            if not looks:
                continue
            try:
                add(wc.Dispatch(rot.GetObject(mk).QueryInterface(pythoncom.IID_IDispatch)))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    order = _z_order()
    return [found[h] for h in sorted(found, key=lambda h: order.get(h, 1 << 30))]


def _z_order() -> "dict[int, int]":
    try:
        import win32gui  # noqa: PLC0415

        seen: list[int] = []
        win32gui.EnumWindows(lambda h, _: seen.append(h) or True, None)
        return {h: i for i, h in enumerate(seen)}
    except Exception:  # noqa: BLE001
        return {}


def _same_path(a: str, b: str) -> bool:
    import os  # noqa: PLC0415

    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _books(apps: "list[Any]") -> "list[tuple[Any, Any]]":
    out = []
    for app in apps:
        try:
            for wb in app.Workbooks:
                out.append((app, wb))
        except Exception:  # noqa: BLE001
            continue
    return out


def _find_book(apps: "list[Any]", ref: Any) -> "tuple[Any, Any]":
    """The workbook *ref* names (a full path, a file name, or a name without
    its extension); with no *ref*, the active workbook of the front-most Excel."""
    books = _books(apps)
    if not apps:
        raise Refusal("Excel is not open on this PC. Open the workbook in Excel first.", "no_excel")
    if not books:
        raise Refusal("Excel is open but no workbook is open in it.", "no_workbook")
    want = str(ref or "").strip()
    if not want:
        for app in apps:
            try:
                wb = app.ActiveWorkbook
                if wb is not None:
                    return app, wb
            except Exception:  # noqa: BLE001
                continue
        return books[0]
    hits = [(a, w) for a, w in books if _same_path(str(w.FullName), want)]
    if not hits:
        low = want.lower()
        hits = [(a, w) for a, w in books if str(w.Name).lower() == low]
    if not hits:
        hits = [(a, w) for a, w in books if str(w.Name).lower().rsplit(".", 1)[0] == low.rsplit(".", 1)[0]]
    if not hits:
        names = ", ".join(sorted(str(w.Name) for _, w in books)[:12])
        raise Refusal(f"No open workbook is called '{want}'. Open in Excel now: {names}.", "no_such_workbook")
    if len(hits) > 1:
        raise Refusal(f"More than one open workbook is called '{want}'. Give its full path.", "ambiguous_workbook")
    return hits[0]


def _sheet(wb: Any, name: Any) -> Any:
    want = str(name or "").strip()
    if not want:
        ws = wb.ActiveSheet
        try:
            ws.Range("A1")
            return ws
        except Exception:  # noqa: BLE001 — a chart sheet is in front
            return wb.Worksheets(1)
    for ws in wb.Worksheets:
        if str(ws.Name).lower() == want.lower():
            return ws
    names = ", ".join(str(s.Name) for s in wb.Worksheets)
    raise Refusal(f"'{wb.Name}' has no sheet called '{want}'. Its sheets: {names}.", "no_such_sheet")


def _rng(ws: Any, r1: int, c1: int, r2: int, c2: int) -> Any:
    return ws.Range(ws.Cells(r1, c1), ws.Cells(r2, c2))


def _addr(rng: Any) -> str:
    return str(rng.Address).replace("$", "")


def _used(ws: Any) -> "tuple[int, int, int, int]":
    u = ws.UsedRange
    r1, c1 = int(u.Row), int(u.Column)
    return r1, c1, r1 + int(u.Rows.Count) - 1, c1 + int(u.Columns.Count) - 1


def _clip_to_used(ws: Any, box: "tuple[int, int, int, int]") -> "tuple[int, int, int, int]":
    """A whole-column or whole-row range, cut down to where the sheet has
    anything (never larger than the range asked for)."""
    r1, c1, r2, c2 = box
    ur1, uc1, ur2, uc2 = _used(ws)
    if r2 >= 1_048_576:
        r2 = max(r1, min(r2, ur2))
    if c2 >= 16_384:
        c2 = max(c1, min(c2, uc2))
    return r1, c1, r2, c2


def _values(rng: Any, rows: int, cols: int) -> "list[list[Any]]":
    return [[plain(v) for v in row] for row in grid(rng.Value, rows, cols)]


def _formulas(rng: Any, rows: int, cols: int) -> "list[list[str]]":
    return [["" if f is None else str(f) for f in row] for row in grid(rng.Formula, rows, cols)]


def _calc_words(app: Any) -> str:
    try:
        return {-4105: "automatic", -4135: "manual", 2: "automatic except tables"}.get(int(app.Calculation), "automatic")
    except Exception:  # noqa: BLE001
        return "automatic"


def _error_cells(ws: Any, limit: int = 40) -> "list[str]":
    """Addresses of formula cells on *ws* that show an error (bounded)."""
    try:
        cells = ws.UsedRange.SpecialCells(-4123, 16)
    except Exception:  # noqa: BLE001 — Excel raises when there are none
        return []
    out: list[str] = []
    try:
        for c in cells:
            out.append(_addr(c))
            if len(out) >= limit:
                break
    except Exception:  # noqa: BLE001
        pass
    return out


# ----------------------------------------------------------------------- look --


def _book_info(app: Any, wb: Any, active_wb: Any) -> dict:
    info: dict[str, Any] = {"name": str(wb.Name), "path": str(wb.FullName), "saved": bool(wb.Saved), "read_only": bool(wb.ReadOnly)}
    try:
        info["autosave"] = bool(wb.AutoSaveOn)
    except Exception:  # noqa: BLE001
        info["autosave"] = False
    try:
        info["active"] = active_wb is not None and str(active_wb.FullName) == str(wb.FullName)
    except Exception:  # noqa: BLE001
        info["active"] = False
    sheets = []
    for i, ws in enumerate(wb.Worksheets):
        if i >= 60:
            break
        one: dict[str, Any] = {"name": str(ws.Name), "visible": int(ws.Visible) == -1}
        try:
            r1, c1, r2, c2 = _used(ws)
            one["used"] = a1(r1, c1, r2, c2)
            one["rows"], one["cols"] = r2 - r1 + 1, c2 - c1 + 1
            if bool(ws.ProtectContents):
                one["protected"] = True
            for key, coll in (("tables", ws.ListObjects), ("pivots", ws.PivotTables()), ("charts", ws.ChartObjects())):
                names = [str(x.Name) for x in coll][:20]
                if names:
                    one[key] = names
        except Exception:  # noqa: BLE001
            pass
        sheets.append(one)
    info["sheets"] = sheets
    return info


def op_look(req: dict) -> dict:
    apps = _apps()
    if not apps:
        return {"ok": True, "excel_open": False, "workbooks": []}
    out: dict[str, Any] = {"ok": True, "excel_open": True, "instances": len(apps), "workbooks": [], "protected_view": []}
    front = apps[0]
    try:
        active_wb = front.ActiveWorkbook
    except Exception:  # noqa: BLE001
        active_wb = None
    for app, wb in _books(apps)[:30]:
        out["workbooks"].append(_book_info(app, wb, active_wb))
    for app in apps:
        try:
            for w in app.ProtectedViewWindows:
                out["protected_view"].append(str(w.Caption))
        except Exception:  # noqa: BLE001
            continue
    if active_wb is not None:
        act: dict[str, Any] = {"workbook": str(active_wb.Name), "calculation": _calc_words(front)}
        try:
            act["sheet"] = str(front.ActiveSheet.Name)
            act["selection"] = _addr(front.Selection)
            act["cell"] = _addr(front.ActiveCell)
        except Exception:  # noqa: BLE001 — a chart or a shape is selected
            pass
        out["active"] = act
    want = req.get("range")
    if want or req.get("workbook") or req.get("sheet"):
        app, wb = _find_book(apps, req.get("workbook"))
        ws = _sheet(wb, req.get("sheet"))
        text = str(want or "").strip()
        if not text or text.lower() == "used":
            box = _used(ws)
        elif text.lower() == "selection":
            sel = app.Selection
            if str(sel.Worksheet.Name) != str(ws.Name) and not req.get("sheet"):
                ws = sel.Worksheet
            box = parse_range(_addr(sel.Areas(1)))
        else:
            box = _clip_to_used(ws, parse_range(text))
        r1, c1, r2, c2 = box
        cap = max(1, min(int(req.get("max_cells") or DEFAULT_LOOK_CELLS), MAX_LOOK_CELLS))
        cols = c2 - c1 + 1
        rows = r2 - r1 + 1
        keep = max(1, min(rows, cap // max(1, cols)))
        if cols > cap:
            cols, c2 = cap, c1 + cap - 1
        truncated = keep < rows
        r2 = r1 + keep - 1
        rng = _rng(ws, r1, c1, r2, c2)
        values = _values(rng, keep, cols)
        forms = _formulas(rng, keep, cols)
        fmap = {
            a1(r1 + i, c1 + j): f
            for i, row in enumerate(forms)
            for j, f in enumerate(row)
            if f.startswith("=")
        }
        out["range"] = {
            "workbook": str(wb.Name), "path": str(wb.FullName), "sheet": str(ws.Name),
            "address": a1(r1, c1, r2, c2), "rows": values, "formulas": fmap,
            "truncated": truncated, "total_rows": rows,
        }
    return out


def op_snapshot(req: dict) -> dict:
    apps = _apps()
    _app, wb = _find_book(apps, req.get("workbook"))
    path = str(req.get("path") or "")
    folder = str(req.get("dir") or "")
    if folder:
        # Named after the workbook, so a file tool reading the copy says the
        # workbook's own name. SaveCopyAs writes the workbook's own format.
        import os  # noqa: PLC0415

        name = re.sub(r'[<>:"/\\|?*]+', "_", str(wb.Name)) or "workbook"
        if not re.search(r"\.xl[a-z]{1,2}$", name.lower()):
            name += ".xlsx"
        path = os.path.join(folder, name)
    if not path:
        raise Refusal("no place was given for the copy", "bad_request")
    wb.SaveCopyAs(path)
    return {"ok": True, "path": path, "workbook": str(wb.Name), "source": str(wb.FullName), "saved": bool(wb.Saved)}


def op_goto(req: dict) -> dict:
    apps = _apps()
    app, wb = _find_book(apps, req.get("workbook"))
    ws = _sheet(wb, req.get("sheet"))
    r1, c1, r2, c2 = _clip_to_used(ws, parse_range(req.get("cell") or req.get("range") or "A1"))
    wb.Activate()
    app.Goto(_rng(ws, r1, c1, r2, c2), True)
    try:
        if bool(app.Visible):
            import win32gui  # noqa: PLC0415

            win32gui.SetForegroundWindow(int(app.Hwnd))
    except Exception:  # noqa: BLE001 — Windows may refuse to change the front window
        pass
    return {"ok": True, "workbook": str(wb.Name), "sheet": str(ws.Name), "address": a1(r1, c1, r2, c2)}


# ---------------------------------------------------------------- edit steps --


class _Deferred(Exception):
    """While only CHECKING: this step works on a sheet an earlier step of the
    same call makes, so it can be checked only once that sheet exists."""


class Cx:
    """One ``plan`` or ``apply`` in progress."""

    def __init__(self, app: Any, wb: Any, *, planning: bool, undo_path: str = "", default_sheet: str = "") -> None:
        self.app, self.wb, self.planning, self.undo_path = app, wb, planning, undo_path
        self.default_sheet = str(default_sheet or "").strip()
        self.steps: list[dict] = []
        self.changes: list[dict] = []
        self.changed_cells = 0
        self.overwrites = 0
        self.formulas_lost = 0
        self.destructive: list[str] = []
        self.touched: dict[str, None] = {}
        self.pending: set[str] = set()  # sheets an earlier step of this call makes (checking only)
        self.i = 0

    def sheet(self, name: Any) -> Any:
        name = str(name or "").strip() or self.default_sheet
        if not name:
            # A CHANGE never lands on "whatever sheet happens to be in front":
            # in a live trial a model that had just read Sales sorted the
            # Assumptions sheet, because that was the active one.
            names = ", ".join(str(s.Name) for s in self.wb.Worksheets)
            raise Refusal(f"say which sheet in 'sheet' (this workbook's sheets: {names})", "bad_step")
        if self.planning and name.lower() in self.pending:
            raise _Deferred(name)
        ws = _sheet(self.wb, name)
        self.touched[str(ws.Name)] = None
        return ws

    def undo(self, inverse: dict) -> None:
        """Write what this step is about to replace, BEFORE it happens."""
        self._write({"i": self.i, "inverse": inverse})

    def after(self, facts: dict) -> None:
        self._write({"i": self.i, "after": facts})

    def _write(self, row: dict) -> None:
        if self.planning or not self.undo_path:
            return
        with open(self.undo_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
            fh.flush()


def _need(op: dict, key: str) -> Any:
    v = op.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise Refusal(f"step '{op.get('op')}' needs '{key}'", "bad_step")
    return v


def _formats_of(op: dict) -> dict:
    return {k: op[k] for k in FORMAT_KEYS if k in op and op[k] is not None}


def _read_formats(rng: Any, rows: int, cols: int, keys: "list[str]") -> dict:
    """The current value of each format in *keys* over the range: one value
    when the whole range agrees, else a per-cell grid (bounded)."""
    out: dict[str, Any] = {}

    def one(target: Any, key: str) -> Any:
        if key == "number_format":
            return target.NumberFormat
        if key == "bold":
            return target.Font.Bold
        if key == "italic":
            return target.Font.Italic
        if key == "font_size":
            return target.Font.Size
        if key == "font_color":
            return target.Font.Color
        if key == "fill":
            v = target.Interior.Pattern
            return None if v is None else [target.Interior.Pattern, target.Interior.Color]
        if key == "align":
            return target.HorizontalAlignment
        if key == "wrap":
            return target.WrapText
        if key == "border":
            return [[b, target.Borders(b).LineStyle] for b in (7, 8, 9, 10, 11, 12)]
        return None

    def mixed(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, list):
            return any(x is None or (isinstance(x, list) and None in x) for x in v)
        return False

    for key in keys:
        v = one(rng, key)
        if not mixed(v) or rows * cols == 1:
            out[key] = {"all": v}
        elif rows * cols <= 5000 and key != "border":
            out[key] = {"cells": [[one(rng.Cells(i + 1, j + 1), key) for j in range(cols)] for i in range(rows)]}
        else:
            out[key] = {"lost": True}
    return out


def _write_format(target: Any, key: str, v: Any) -> None:
    if key == "number_format":
        target.NumberFormat = v
    elif key == "bold":
        target.Font.Bold = bool(v)
    elif key == "italic":
        target.Font.Italic = bool(v)
    elif key == "font_size":
        target.Font.Size = float(v)
    elif key == "font_color":
        target.Font.Color = v
    elif key == "fill":
        if not v or v[0] in (None, -4142):
            target.Interior.Pattern = -4142
        else:
            target.Interior.Pattern = v[0]
            target.Interior.Color = v[1]
    elif key == "align":
        target.HorizontalAlignment = v
    elif key == "wrap":
        target.WrapText = bool(v)
    elif key == "border":
        for b, style in v or []:
            if style is not None:
                target.Borders(b).LineStyle = style


def _apply_formats(rng: Any, fm: dict) -> None:
    for key, v in fm.items():
        if key == "fill":
            v = [-4142, 0] if str(v).strip().lower() in ("", "none") else [1, bgr(v)]
        elif key == "font_color":
            v = bgr(v)
        elif key == "align":
            if str(v).lower() not in _ALIGN:
                raise Refusal("align is left, center, right or general", "bad_step")
            v = _ALIGN[str(v).lower()]
        elif key == "border":
            style = str(v).lower()
            if style not in ("all", "outline", "bottom", "top", "none"):
                raise Refusal("border is all, outline, bottom, top or none", "bad_step")
            edges = {"all": (7, 8, 9, 10, 11, 12), "outline": (7, 8, 9, 10), "bottom": (9,), "top": (8,), "none": (7, 8, 9, 10, 11, 12)}[style]
            v = [[b, -4142 if style == "none" else 1] for b in edges]
            if style == "all" and (rng.Rows.Count == 1 or rng.Columns.Count == 1):
                v = [[b, s] for b, s in v if not (b == 12 and rng.Rows.Count == 1) and not (b == 11 and rng.Columns.Count == 1)]
        _write_format(rng, key, v)


def _restore_formats(rng: Any, rows: int, cols: int, prior: dict) -> None:
    for key, held in prior.items():
        if "all" in held:
            _write_format(rng, key, held["all"])
        elif "cells" in held:
            for i in range(rows):
                for j in range(cols):
                    _write_format(rng.Cells(i + 1, j + 1), key, held["cells"][i][j])


def _note_cell_changes(cx: Cx, ws: Any, r1: int, c1: int, before_v: list, before_f: list, after_v: list, after_f: list) -> None:
    name = str(ws.Name)
    for i, row in enumerate(after_f):
        for j, f in enumerate(row):
            bf, bv, av = before_f[i][j], before_v[i][j], after_v[i][j]
            if f == bf and av == bv:
                continue
            cx.changed_cells += 1
            if len(cx.changes) < MAX_CHANGES_LISTED:
                one: dict[str, Any] = {"sheet": name, "cell": a1(r1 + i, c1 + j), "before": bv, "after": av}
                if bf.startswith("="):
                    one["before_formula"] = bf
                if f.startswith("="):
                    one["after_formula"] = f
                cx.changes.append(one)


def _count_overwrites(cx: Cx, before_f: list, new: "list[list[Any]] | None") -> "tuple[int, int]":
    """How many cells that hold something get a DIFFERENT content, and how
    many of those are formulas replaced by something that is not a formula."""
    over = lost = 0
    for i, row in enumerate(before_f):
        for j, bf in enumerate(row):
            if bf == "":
                continue
            nv = None if new is None else new[i][j]
            ns = "" if nv is None else (str(nv) if not isinstance(nv, bool) else str(nv).upper())
            if isinstance(nv, float) and nv == int(nv):
                ns = str(int(nv))
            if ns == bf:
                continue
            over += 1
            if bf.startswith("=") and not ns.startswith("="):
                lost += 1
    cx.overwrites += over
    cx.formulas_lost += lost
    return over, lost


def h_set(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    box = parse_range(op.get("cell") or op.get("range") or _need(op, "cell"))
    fm = _formats_of(op)
    has_value = any(k in op for k in ("value", "formula", "values"))
    if not has_value and not fm:
        raise Refusal("a 'set' step needs a value, a formula, values or a format", "bad_step")
    if not has_value:
        box = _clip_to_used(ws, box)
    r1, c1, r2, c2 = box
    new: "list[list[Any]] | None" = None
    if "values" in op and op["values"] is not None:
        block = op["values"]
        if not isinstance(block, list) or not block or not all(isinstance(r, list) for r in block):
            raise Refusal("'values' is a list of rows, each a list of cells", "bad_step")
        width = max(len(r) for r in block)
        new = [[to_cell_text(r[j]) if j < len(r) else "" for j in range(width)] for r in block]
        r2, c2 = r1 + len(new) - 1, c1 + width - 1
    rows, cols = r2 - r1 + 1, c2 - c1 + 1
    if rows * cols > MAX_CELLS_PER_STEP:
        raise Refusal(f"one step may touch at most {MAX_CELLS_PER_STEP:,} cells ({a1(r1, c1, r2, c2)} is {rows * cols:,})", "too_large")
    rng = _rng(ws, r1, c1, r2, c2)
    where = f"{ws.Name}!{a1(r1, c1, r2, c2)}"
    info: dict[str, Any] = {"op": "set", "where": where, "cells": rows * cols}
    single: Any = None
    if has_value and new is None:
        if "formula" in op and op["formula"] is not None:
            single = str(op["formula"])
            single = single if single.startswith("=") else "=" + single
        else:
            single = to_cell_text(op.get("value"))
    before_f = _formulas(rng, rows, cols) if has_value else []
    if has_value:
        probe = new if new is not None else (None if rows * cols > 1 and isinstance(single, str) and single.startswith("=") else [[single] * cols for _ in range(rows)])
        over, lost = _count_overwrites(cx, before_f, probe) if probe is not None else (sum(1 for r in before_f for f in r if f), 0)
        if probe is None:
            cx.overwrites += over
        info["overwrites"], info["formulas_lost"] = over, lost
    info["summary"] = (
        f"write {rows * cols} cell(s) at {where}" if has_value else f"format {where}"
    ) + (f" ({', '.join(sorted(fm))})" if fm else "")
    if cx.planning:
        return info
    before_v = _values(rng, rows, cols) if has_value else []
    inverse: dict[str, Any] = {"kind": "cells", "sheet": str(ws.Name), "address": a1(r1, c1, r2, c2)}
    if has_value:
        inverse["formulas"] = before_f
    if fm:
        inverse["formats"] = _read_formats(rng, rows, cols, sorted(fm))
    cx.undo(inverse)
    if "number_format" in fm:  # before the value, so text typed as text stays text
        rng.NumberFormat = fm["number_format"]
    if has_value:
        if new is not None:
            rng.Formula = tuple(tuple(r) for r in new)
        elif isinstance(single, str) and single.startswith("="):
            rng.Formula = single
        else:
            rng.Value = single
    _apply_formats(rng, {k: v for k, v in fm.items() if k != "number_format"})
    if has_value:
        after_f = _formulas(rng, rows, cols)
        _note_cell_changes(cx, ws, r1, c1, before_v, before_f, _values(rng, rows, cols), after_f)
        cx.after({"formulas": after_f})
    else:
        cx.after({})
    return info


def h_clear(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    r1, c1, r2, c2 = _clip_to_used(ws, parse_range(_need(op, "range")))
    rows, cols = r2 - r1 + 1, c2 - c1 + 1
    if rows * cols > MAX_CELLS_PER_STEP:
        raise Refusal(f"one step may touch at most {MAX_CELLS_PER_STEP:,} cells", "too_large")
    rng = _rng(ws, r1, c1, r2, c2)
    before_f = _formulas(rng, rows, cols)
    over = sum(1 for r in before_f for f in r if f)
    cx.overwrites += over
    cx.formulas_lost += sum(1 for r in before_f for f in r if f.startswith("="))
    info = {"op": "clear", "where": f"{ws.Name}!{a1(r1, c1, r2, c2)}", "cells": rows * cols, "overwrites": over,
            "summary": f"clear the contents of {ws.Name}!{a1(r1, c1, r2, c2)} ({over} cell(s) hold something)"}
    if cx.planning:
        return info
    before_v = _values(rng, rows, cols)
    cx.undo({"kind": "cells", "sheet": str(ws.Name), "address": a1(r1, c1, r2, c2), "formulas": before_f})
    rng.ClearContents()
    after_f = _formulas(rng, rows, cols)
    _note_cell_changes(cx, ws, r1, c1, before_v, before_f, _values(rng, rows, cols), after_f)
    cx.after({"formulas": after_f})
    return info


def h_add_sheet(cx: Cx, op: dict) -> dict:
    name = str(_need(op, "name"))
    bad = sheet_name_problem(name)
    if bad:
        raise Refusal(bad, "bad_step")
    if any(str(s.Name).lower() == name.lower() for s in cx.wb.Sheets):
        raise Refusal(f"'{cx.wb.Name}' already has a sheet called '{name}'", "exists")
    info = {"op": "add_sheet", "where": name, "summary": f"add a sheet called '{name}'"}
    if cx.planning:
        cx.pending.add(name.lower())
        return info
    cx.undo({"kind": "sheet_added", "name": name})
    last = cx.wb.Sheets(cx.wb.Sheets.Count)
    was = cx.wb.ActiveSheet
    ws = cx.wb.Worksheets.Add(None, last)  # (Before, After): after the last sheet
    ws.Name = name
    try:
        was.Activate()  # adding a sheet shows it; put the user's sheet back in front
    except Exception:  # noqa: BLE001
        pass
    cx.touched[name] = None
    cx.after({})
    return info


def h_rename_sheet(cx: Cx, op: dict) -> dict:
    ws = _sheet(cx.wb, _need(op, "sheet"))
    to = str(_need(op, "to"))
    bad = sheet_name_problem(to)
    if bad:
        raise Refusal(bad, "bad_step")
    if any(str(s.Name).lower() == to.lower() for s in cx.wb.Sheets):
        raise Refusal(f"'{cx.wb.Name}' already has a sheet called '{to}'", "exists")
    frm = str(ws.Name)
    info = {"op": "rename_sheet", "where": frm, "summary": f"rename sheet '{frm}' to '{to}'"}
    if cx.planning:
        cx.pending.add(to.lower())
        return info
    cx.undo({"kind": "sheet_renamed", "from": frm, "to": to})
    ws.Name = to
    cx.after({})
    return info


def h_delete_sheet(cx: Cx, op: dict) -> dict:
    ws = _sheet(cx.wb, _need(op, "sheet"))
    name = str(ws.Name)
    if int(cx.wb.Worksheets.Count) <= 1:
        raise Refusal("a workbook must keep at least one sheet", "bad_step")
    cx.destructive.append(f"delete the sheet '{name}'")
    info = {"op": "delete_sheet", "where": name, "summary": f"delete the sheet '{name}'", "undoable": False}
    if cx.planning:
        return info
    cx.undo({"kind": "none", "what": f"the sheet '{name}' was deleted"})
    alerts = cx.app.DisplayAlerts
    try:
        cx.app.DisplayAlerts = False
        ws.Delete()
    finally:
        cx.app.DisplayAlerts = alerts
    cx.after({})
    return info


def _line_span(op: dict, rows: bool) -> "tuple[int, int]":
    at = _need(op, "at")
    count = int(op.get("count") or 1)
    if not 1 <= count <= 10_000:
        raise Refusal("count is between 1 and 10,000", "bad_step")
    if rows:
        if not str(at).strip().isdigit():
            raise Refusal("'at' is a row number such as 5", "bad_step")
        first = int(at)
    else:
        if not re.fullmatch(r"[A-Za-z]{1,3}", str(at).strip()):
            raise Refusal("'at' is a column letter such as C", "bad_step")
        first = col_num(str(at).strip())
    return first, count


def _lines(ws: Any, first: int, count: int, rows: bool) -> Any:
    if rows:
        return ws.Rows(f"{first}:{first + count - 1}")
    return ws.Columns(f"{col_letter(first)}:{col_letter(first + count - 1)}")


def _insert(cx: Cx, op: dict, rows: bool) -> dict:
    ws = cx.sheet(op.get("sheet"))
    first, count = _line_span(op, rows)
    what = "row" if rows else "column"
    label = f"{first}" if rows else col_letter(first)
    info = {"op": op["op"], "where": f"{ws.Name}!{label}", "summary": f"insert {count} {what}(s) at {what} {label} on {ws.Name}"}
    if cx.planning:
        return info
    cx.undo({"kind": "lines_inserted", "sheet": str(ws.Name), "first": first, "count": count, "rows": rows})
    _lines(ws, first, count, rows).Insert()
    cx.after({})
    return info


def _delete(cx: Cx, op: dict, rows: bool) -> dict:
    ws = cx.sheet(op.get("sheet"))
    first, count = _line_span(op, rows)
    what = "row" if rows else "column"
    label = f"{first}:{first + count - 1}" if rows else f"{col_letter(first)}:{col_letter(first + count - 1)}"
    held = int(cx.app.WorksheetFunction.CountA(_lines(ws, first, count, rows)))
    cx.destructive.append(f"delete {what}s {label} on {ws.Name} ({held} cell(s) hold something)")
    info = {"op": op["op"], "where": f"{ws.Name}!{label}", "summary": f"delete {what}s {label} on {ws.Name}", "undoable": False}
    if cx.planning:
        return info
    cx.undo({"kind": "none", "what": f"{what}s {label} on {ws.Name} were deleted"})
    _lines(ws, first, count, rows).Delete()
    cx.after({})
    return info


def _header_col(ws: Any, box: "tuple[int, int, int, int]", key: Any) -> int:
    """A column named by its letter or by the text in the range's first row."""
    r1, c1, _r2, c2 = box
    s = str(key or "").strip()
    heads = [str(v if v is not None else "").strip() for v in grid(_rng(ws, r1, c1, r1, c2).Value, 1, c2 - c1 + 1)[0]]
    for j, h in enumerate(heads):
        if h and h.lower() == s.lower():
            return c1 + j
    if re.fullmatch(r"[A-Za-z]{1,3}", s) and c1 <= col_num(s) <= c2:
        return col_num(s)
    raise Refusal(f"'{s}' is neither a column letter inside the range nor one of its headings ({', '.join(h for h in heads if h)[:200]})", "bad_step")


def h_sort(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    box = _clip_to_used(ws, parse_range(_need(op, "range")))
    r1, c1, r2, c2 = box
    rows, cols = r2 - r1 + 1, c2 - c1 + 1
    if rows * cols > MAX_CELLS_PER_STEP:
        raise Refusal(f"one step may sort at most {MAX_CELLS_PER_STEP:,} cells", "too_large")
    header = op.get("header", True) is not False
    keys = op.get("by")
    if isinstance(keys, (str, dict)):
        keys = [keys]
    if not isinstance(keys, list) or not keys:
        raise Refusal("a 'sort' step needs 'by': the column(s) to sort on", "bad_step")
    plan: list[tuple[int, int]] = []
    for k in keys[:8]:
        k = k if isinstance(k, dict) else {"column": k}
        col = _header_col(ws, box, k.get("column")) if header else col_num(str(k.get("column") or ""))
        plan.append((col, 2 if str(k.get("order") or "asc").lower().startswith("d") else 1))
    names = ", ".join(f"{col_letter(c)} {'descending' if o == 2 else 'ascending'}" for c, o in plan)
    info = {"op": "sort", "where": f"{ws.Name}!{a1(*box)}", "summary": f"sort {ws.Name}!{a1(*box)} by {names}"}
    if cx.planning:
        return info
    rng = _rng(ws, r1, c1, r2, c2)
    before_f = _formulas(rng, rows, cols)
    cx.undo({"kind": "cells", "sheet": str(ws.Name), "address": a1(*box), "formulas": before_f})
    srt = ws.Sort
    srt.SortFields.Clear()
    first_data = r1 + (1 if header else 0)
    for col, order in plan:
        srt.SortFields.Add(_rng(ws, first_data, col, r2, col), 0, order)
    srt.SetRange(rng)
    srt.Header = 1 if header else 2
    srt.Apply()
    cx.after({"formulas": _formulas(rng, rows, cols)})
    return info


def h_filter(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    box = _clip_to_used(ws, parse_range(_need(op, "range")))
    col = _header_col(ws, box, _need(op, "column"))
    field = col - box[1] + 1
    values = op.get("values")
    crit = op.get("criteria")
    if not values and crit is None:
        raise Refusal("a 'filter' step needs 'criteria' (such as >100) or 'values' (a list to keep)", "bad_step")
    info = {"op": "filter", "where": f"{ws.Name}!{a1(*box)}",
            "summary": f"filter {ws.Name}!{a1(*box)} on column {col_letter(col)}"}
    if cx.planning:
        return info
    cx.undo({"kind": "filter_set", "sheet": str(ws.Name), "address": a1(*box), "field": field, "had": bool(ws.AutoFilterMode)})
    rng = _rng(ws, *box)
    if values:
        rng.AutoFilter(field, tuple(str(v) for v in values), 7)
    else:
        rng.AutoFilter(field, str(crit))
    cx.after({})
    return info


def h_clear_filter(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    info = {"op": "clear_filter", "where": str(ws.Name), "summary": f"show every row on {ws.Name} again (remove the filter)"}
    if cx.planning:
        return info
    cx.undo({"kind": "none", "what": f"the filter on {ws.Name} was removed", "harmless": True})
    if bool(ws.AutoFilterMode):
        ws.AutoFilterMode = False
    cx.after({})
    return info


def h_autofit(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    cols = str(op.get("columns") or "").strip()
    if cols:
        _r1, c1, _r2, c2 = parse_range(cols)
    else:
        _r1, c1, _r2, c2 = _used(ws)
    width = op.get("width")
    span = f"{col_letter(c1)}:{col_letter(c2)}"
    info = {"op": op["op"], "where": f"{ws.Name}!{span}",
            "summary": (f"set the width of columns {span} on {ws.Name} to {width}" if width else f"fit the width of columns {span} on {ws.Name}")}
    if cx.planning:
        return info
    if c2 - c1 > 500:
        raise Refusal("at most 500 columns at a time", "too_large")
    cx.undo({"kind": "widths", "sheet": str(ws.Name), "first": c1,
             "widths": [float(ws.Columns(c).ColumnWidth) for c in range(c1, c2 + 1)]})
    target = ws.Columns(span)
    if width:
        target.ColumnWidth = float(width)
    else:
        target.AutoFit()
    cx.after({})
    return info


def h_freeze(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    cell = str(op.get("cell") or "").strip()
    r, c = (0, 0) if not cell or cell.lower() == "none" else parse_range(cell)[:2]
    info = {"op": "freeze", "where": str(ws.Name),
            "summary": f"unfreeze the rows and columns on {ws.Name}" if not r else f"freeze the rows above and columns left of {a1(r, c)} on {ws.Name}"}
    if cx.planning:
        return info
    cx.wb.Activate()
    ws.Activate()
    win = cx.app.ActiveWindow
    cx.undo({"kind": "freeze", "sheet": str(ws.Name), "frozen": bool(win.FreezePanes),
             "row": int(win.SplitRow), "col": int(win.SplitColumn)})
    win.FreezePanes = False
    if r:
        win.SplitRow, win.SplitColumn = r - 1, c - 1
        win.FreezePanes = True
    cx.after({})
    return info


_CF_OPS = {"between": 1, "not_between": 2, "equal": 3, "not_equal": 4, "greater": 5, "less": 6, "greater_equal": 7, "less_equal": 8}


def h_conditional_format(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    box = _clip_to_used(ws, parse_range(_need(op, "range")))
    rule = str(_need(op, "rule")).lower()
    known = set(_CF_OPS) | {"formula", "text_contains", "duplicates", "top", "bottom", "data_bar", "color_scale"}
    if rule not in known:
        raise Refusal(f"rule is one of: {', '.join(sorted(known))}", "bad_step")
    info = {"op": "conditional_format", "where": f"{ws.Name}!{a1(*box)}",
            "summary": f"add a colour rule ({rule.replace('_', ' ')}) to {ws.Name}!{a1(*box)}"}
    if cx.planning:
        return info
    rng = _rng(ws, *box)
    fcs = rng.FormatConditions
    cx.undo({"kind": "cf_added", "sheet": str(ws.Name), "address": a1(*box), "before": int(fcs.Count)})
    if rule in _CF_OPS:
        v1, v2 = _need(op, "value"), op.get("value2")
        fc = fcs.Add(1, _CF_OPS[rule], str(v1), str(v2)) if v2 is not None else fcs.Add(1, _CF_OPS[rule], str(v1))
    elif rule == "formula":
        f = str(_need(op, "formula"))
        fc = fcs.Add(2, 0, f if f.startswith("=") else "=" + f)
    elif rule == "text_contains":
        pythoncom, _ = _com()
        m = pythoncom.Missing
        fc = fcs.Add(9, m, m, m, str(_need(op, "value")), 0)
    elif rule == "duplicates":
        fc = fcs.AddUniqueValues()
        fc.DupeUnique = 1
    elif rule in ("top", "bottom"):
        fc = fcs.AddTop10()
        fc.TopBottom = 1 if rule == "top" else 0
        fc.Rank = int(op.get("value") or 10)
    elif rule == "data_bar":
        fc = fcs.AddDatabar()
    else:
        fc = fcs.AddColorScale(3)
    if rule not in ("data_bar", "color_scale"):
        fill, font = op.get("fill"), op.get("font_color")
        if not fill and not font and not op.get("bold"):
            fill = "FFC7CE"
        if fill:
            fc.Interior.Color = bgr(fill)
        if font:
            fc.Font.Color = bgr(font)
        if op.get("bold"):
            fc.Font.Bold = True
    cx.after({"count": int(rng.FormatConditions.Count)})
    return info


_VAL_TYPES = {"whole": 1, "decimal": 2, "list": 3, "date": 4}


def _validation_now(rng: Any) -> "dict | None":
    try:
        v = rng.Validation
        t = v.Type
        return {"type": int(t), "alert": int(v.AlertStyle), "operator": int(v.Operator), "f1": str(v.Formula1),
                "f2": str(v.Formula2), "error": str(v.ErrorMessage), "input": str(v.InputMessage)}
    except Exception:  # noqa: BLE001 — Excel raises when the range has no rule (or mixed rules)
        return None


def _validation_put(rng: Any, held: "dict | None") -> None:
    rng.Validation.Delete()
    if not held:
        return
    v = rng.Validation
    if held["type"] == 3:
        v.Add(3, held["alert"], 1, held["f1"])
        v.InCellDropdown = True
    elif held.get("f2"):
        v.Add(held["type"], held["alert"], held["operator"], held["f1"], held["f2"])
    else:
        v.Add(held["type"], held["alert"], held["operator"], held["f1"])
    if held.get("error"):
        v.ErrorMessage = held["error"]
    if held.get("input"):
        v.InputMessage = held["input"]


def h_validation(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    box = parse_range(_need(op, "range"))
    kind = str(op.get("type") or ("list" if op.get("list") or op.get("source") else "")).lower()
    if kind not in _VAL_TYPES:
        raise Refusal("a 'validation' step needs type: list, whole, decimal or date", "bad_step")
    if kind == "list":
        items = op.get("list")
        if items:
            if any("," in str(x) for x in items):
                raise Refusal("a list item cannot contain a comma; put the choices in cells and give 'source' instead", "bad_step")
            f1 = ",".join(str(x) for x in items)
            if len(f1) > 255:
                raise Refusal("the choices are too long for a typed list (255 characters); put them in cells and give 'source'", "bad_step")
        else:
            f1 = str(_need(op, "source"))
            f1 = f1 if f1.startswith("=") else "=" + f1
        new = {"type": 3, "alert": 1, "operator": 1, "f1": f1, "f2": ""}
        words = "a dropdown list"
    else:
        lo, hi = op.get("min"), op.get("max")
        if lo is None and hi is None:
            raise Refusal("give 'min', 'max' or both", "bad_step")
        if lo is not None and hi is not None:
            new = {"type": _VAL_TYPES[kind], "alert": 1, "operator": 1, "f1": str(lo), "f2": str(hi)}
        elif lo is not None:
            new = {"type": _VAL_TYPES[kind], "alert": 1, "operator": 7, "f1": str(lo), "f2": ""}
        else:
            new = {"type": _VAL_TYPES[kind], "alert": 1, "operator": 8, "f1": str(hi), "f2": ""}
        words = f"a {kind} number rule" if kind != "date" else "a date rule"
    new["error"] = str(op.get("message") or "")
    new["input"] = str(op.get("prompt") or "")
    info = {"op": "validation", "where": f"{ws.Name}!{a1(*box)}", "summary": f"add {words} to {ws.Name}!{a1(*box)}"}
    if cx.planning:
        return info
    rng = _rng(ws, *box)
    cx.undo({"kind": "validation_set", "sheet": str(ws.Name), "address": a1(*box), "prior": _validation_now(rng)})
    _validation_put(rng, new)
    cx.after({"now": _validation_now(rng)})
    return info


_CHART_TYPES = {"column": 51, "stacked_column": 52, "bar": 57, "stacked_bar": 58, "line": 4, "line_markers": 65,
                "pie": 5, "doughnut": -4120, "scatter": -4169, "area": 1}


def h_chart(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    box = _clip_to_used(ws, parse_range(_need(op, "data")))
    kind = str(op.get("type") or "column").lower()
    if kind not in _CHART_TYPES:
        raise Refusal(f"chart type is one of: {', '.join(sorted(_CHART_TYPES))}", "bad_step")
    name = str(op.get("name") or "").strip()
    if name and any(str(c.Name).lower() == name.lower() for c in ws.ChartObjects()):
        raise Refusal(f"{ws.Name} already has a chart called '{name}'", "exists")
    info = {"op": "chart", "where": f"{ws.Name}!{a1(*box)}", "summary": f"add a {kind.replace('_', ' ')} chart of {ws.Name}!{a1(*box)}"}
    if cx.planning:
        return info
    at = str(op.get("at") or "").strip()
    if at:
        ar, ac = parse_range(at)[:2]
    else:
        ar, ac = box[0], box[3] + 2
    anchor = ws.Cells(ar, ac)
    cx.undo({"kind": "chart_added", "sheet": str(ws.Name), "before": [str(c.Name) for c in ws.ChartObjects()]})
    shape = ws.Shapes.AddChart2(-1, _CHART_TYPES[kind], float(anchor.Left), float(anchor.Top), 480.0, 288.0)
    chart = shape.Chart
    chart.SetSourceData(_rng(ws, *box))
    if op.get("title"):
        chart.HasTitle = True
        chart.ChartTitle.Text = str(op["title"])
    if name:
        shape.Name = name
    cx.after({"name": str(shape.Name)})
    info["name"] = str(shape.Name)
    return info


def _chart_obj(ws: Any, name: Any) -> Any:
    want = str(name or "").strip().lower()
    objs = list(ws.ChartObjects())
    if not objs:
        raise Refusal(f"{ws.Name} has no chart", "no_chart")
    if not want and len(objs) == 1:
        return objs[0]
    for c in objs:
        if str(c.Name).lower() == want:
            return c
    raise Refusal(f"{ws.Name} has no chart called '{name}'. Its charts: {', '.join(str(c.Name) for c in objs)}.", "no_chart")


def _axis_title(chart: Any, axis: int) -> "str | None":
    try:
        ax = chart.Axes(axis)
        return str(ax.AxisTitle.Text) if bool(ax.HasTitle) else None
    except Exception:  # noqa: BLE001 — a pie chart has no axes
        return None


def _set_axis_title(chart: Any, axis: int, text: "str | None") -> None:
    try:
        ax = chart.Axes(axis)
    except Exception:  # noqa: BLE001
        return
    if text is None or text == "":
        ax.HasTitle = False
    else:
        ax.HasTitle = True
        ax.AxisTitle.Text = text


def _chart_state(chart: Any) -> dict:
    return {
        "type": int(chart.ChartType),
        "title": str(chart.ChartTitle.Text) if bool(chart.HasTitle) else None,
        "legend": bool(chart.HasLegend),
        "x_title": _axis_title(chart, 1), "y_title": _axis_title(chart, 2),
    }


def _chart_put(chart: Any, s: dict) -> None:
    if "type" in s:
        chart.ChartType = s["type"]
    if "title" in s:
        if s["title"] in (None, ""):
            chart.HasTitle = False
        else:
            chart.HasTitle = True
            chart.ChartTitle.Text = s["title"]
    if "legend" in s:
        chart.HasLegend = bool(s["legend"])
    if "x_title" in s:
        _set_axis_title(chart, 1, s["x_title"])
    if "y_title" in s:
        _set_axis_title(chart, 2, s["y_title"])


def h_chart_edit(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    obj = _chart_obj(ws, op.get("name"))
    new: dict[str, Any] = {}
    if op.get("type"):
        kind = str(op["type"]).lower()
        if kind not in _CHART_TYPES:
            raise Refusal(f"chart type is one of: {', '.join(sorted(_CHART_TYPES))}", "bad_step")
        new["type"] = _CHART_TYPES[kind]
    for key in ("title", "x_title", "y_title"):
        if key in op:
            new[key] = None if op[key] is None else str(op[key])
    if "legend" in op:
        new["legend"] = bool(op["legend"])
    if not new:
        raise Refusal("a 'chart_edit' step needs something to change: type, title, x_title, y_title or legend", "bad_step")
    info = {"op": "chart_edit", "where": f"{ws.Name}!{obj.Name}", "summary": f"change the chart '{obj.Name}' on {ws.Name} ({', '.join(sorted(new))})"}
    if cx.planning:
        return info
    chart = obj.Chart
    cx.undo({"kind": "chart_changed", "sheet": str(ws.Name), "name": str(obj.Name),
             "prior": {k: v for k, v in _chart_state(chart).items() if k in new}})
    _chart_put(chart, new)
    cx.after({})
    return info


_PIVOT_FUNCS = {"sum": -4157, "count": -4112, "average": -4106, "max": -4136, "min": -4139, "product": -4149}
_PIVOT_BACK = {v: k for k, v in _PIVOT_FUNCS.items()}


def _pivot_layout(pt: Any) -> dict:
    def names(coll: Any) -> "list[str]":
        out = []
        for f in coll:
            n = str(f.Name)
            if n.lower() not in ("data", "values"):
                out.append(n)
        return out

    values = []
    for f in pt.DataFields:
        values.append({"field": str(f.SourceName), "func": _PIVOT_BACK.get(int(f.Function), "sum"), "caption": str(f.Name)})
    return {"rows": names(pt.RowFields), "columns": names(pt.ColumnFields), "filters": names(pt.PageFields), "values": values}


def _pivot_fields(pt: Any) -> "list[str]":
    return [str(f.Name) for f in pt.PivotFields()]


def _pivot_check(have: "list[str]", layout: dict) -> dict:
    low = {h.lower(): h for h in have}

    def real(name: Any) -> str:
        key = str(name or "").strip().lower()
        if key not in low:
            raise Refusal(f"the data has no column called '{name}'. Its columns: {', '.join(have)[:300]}.", "bad_step")
        return low[key]

    out: dict[str, Any] = {k: [real(x) for x in (layout.get(k) or [])] for k in ("rows", "columns", "filters")}
    vals = []
    for v in layout.get("values") or []:
        v = v if isinstance(v, dict) else {"field": v}
        func = str(v.get("func") or "sum").lower()
        if func not in _PIVOT_FUNCS:
            raise Refusal(f"a value's func is one of: {', '.join(sorted(_PIVOT_FUNCS))}", "bad_step")
        vals.append({"field": real(v.get("field")), "func": func, "caption": str(v.get("caption") or "")})
    out["values"] = vals
    if not vals and not out["rows"] and not out["columns"]:
        raise Refusal("a pivot table needs at least rows, columns or values", "bad_step")
    return out


def _pivot_apply(pt: Any, layout: dict) -> None:
    pt.ManualUpdate = True
    try:
        for f in list(pt.DataFields):
            f.Orientation = 0
        for coll in (pt.RowFields, pt.ColumnFields, pt.PageFields):
            for f in list(coll):
                if str(f.Name).lower() not in ("data", "values"):
                    f.Orientation = 0
        for orient, key in ((3, "filters"), (1, "rows"), (2, "columns")):
            for pos, name in enumerate(layout.get(key) or [], start=1):
                f = pt.PivotFields(name)
                f.Orientation = orient
                f.Position = pos
        for v in layout.get("values") or []:
            caption = v.get("caption") or f"{v['func'].capitalize()} of {v['field']}"
            pt.AddDataField(pt.PivotFields(v["field"]), caption, _PIVOT_FUNCS[v["func"]])
    finally:
        pt.ManualUpdate = False


def _source_ref(cx: Cx, text: Any) -> "tuple[Any, tuple[int, int, int, int]]":
    raw = str(text or "").strip()
    sheet, _, rng = raw.rpartition("!")
    ws = cx.sheet(sheet.strip("'") or None)
    return ws, _clip_to_used(ws, parse_range(rng))


def h_pivot(cx: Cx, op: dict) -> dict:
    src_ws, src = _source_ref(cx, _need(op, "source"))
    r1, c1, _r2, c2 = src
    heads = [str(v if v is not None else "").strip() for v in grid(_rng(src_ws, r1, c1, r1, c2).Value, 1, c2 - c1 + 1)[0]]
    if any(not h for h in heads):
        raise Refusal(f"every column of the pivot's data needs a heading in row {r1} ({src_ws.Name}!{a1(r1, c1, r1, c2)})", "bad_step")
    layout = _pivot_check(heads, op)
    dest_name = str(op.get("dest_sheet") or "").strip()
    new_sheet = False
    if dest_name:
        exists = any(str(s.Name).lower() == dest_name.lower() for s in cx.wb.Worksheets)
        if not exists:
            bad = sheet_name_problem(dest_name)
            if bad:
                raise Refusal(bad, "bad_step")
            new_sheet = True
    else:
        base, n = "Pivot", 1
        taken = {str(s.Name).lower() for s in cx.wb.Sheets}
        dest_name = base
        while dest_name.lower() in taken:
            n += 1
            dest_name = f"{base} {n}"
        new_sheet = True
    dr, dc = parse_range(op.get("dest_cell") or "A3")[:2]
    name = str(op.get("name") or "").strip()
    info = {"op": "pivot", "where": f"{dest_name}!{a1(dr, dc)}",
            "summary": f"add a pivot table of {src_ws.Name}!{a1(*src)} on {'a new sheet ' if new_sheet else ''}'{dest_name}'"}
    if cx.planning:
        if new_sheet:
            cx.pending.add(dest_name.lower())
        elif dest_name.lower() not in cx.pending:
            dws = _sheet(cx.wb, dest_name)
            if int(cx.app.WorksheetFunction.CountA(_rng(dws, dr, dc, dr + 40, dc + 12))):
                raise Refusal(f"'{dest_name}' already holds something around {a1(dr, dc)}; give an empty place or leave dest_sheet out for a new sheet", "exists")
        return info
    cx.undo({"kind": "pivot_added", "sheet": dest_name, "new_sheet": new_sheet})
    if new_sheet:
        was = cx.wb.ActiveSheet
        dws = cx.wb.Worksheets.Add(None, cx.wb.Sheets(cx.wb.Sheets.Count))
        dws.Name = dest_name
        try:
            was.Activate()
        except Exception:  # noqa: BLE001
            pass
    else:
        dws = _sheet(cx.wb, dest_name)
    cx.touched[dest_name] = None
    cache = cx.wb.PivotCaches().Create(1, _rng(src_ws, *src))
    pt = cache.CreatePivotTable(dws.Cells(dr, dc), name) if name else cache.CreatePivotTable(dws.Cells(dr, dc))
    _pivot_apply(pt, layout)
    cx.after({"name": str(pt.Name)})
    info["name"] = str(pt.Name)
    return info


def _pivot_obj(ws: Any, name: Any) -> Any:
    pts = list(ws.PivotTables())
    want = str(name or "").strip().lower()
    if not pts:
        raise Refusal(f"{ws.Name} has no pivot table", "no_pivot")
    if not want and len(pts) == 1:
        return pts[0]
    for p in pts:
        if str(p.Name).lower() == want:
            return p
    raise Refusal(f"{ws.Name} has no pivot table called '{name}'. Its pivot tables: {', '.join(str(p.Name) for p in pts)}.", "no_pivot")


def h_pivot_edit(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    pt = _pivot_obj(ws, op.get("name"))
    now = _pivot_layout(pt)
    want = dict(now)
    for key in ("rows", "columns", "filters", "values"):
        if key in op and op[key] is not None:
            want[key] = op[key]
    layout = _pivot_check(_pivot_fields(pt), want)
    show = op.get("show")
    info = {"op": "pivot_edit", "where": f"{ws.Name}!{pt.Name}", "summary": f"change the pivot table '{pt.Name}' on {ws.Name}"}
    if show is not None:
        if not isinstance(show, dict) or not show:
            raise Refusal("'show' is {field: [items to keep]}", "bad_step")
        info["summary"] += f" and filter {', '.join(show)}"
    if cx.planning:
        return info
    prior_items: dict[str, list[str]] = {}
    for field in (show or {}):
        f = pt.PivotFields(str(field))
        prior_items[str(field)] = [str(i.Name) for i in f.PivotItems() if bool(i.Visible)]
    cx.undo({"kind": "pivot_changed", "sheet": str(ws.Name), "name": str(pt.Name), "layout": now, "items": prior_items})
    if layout != now:
        _pivot_apply(pt, layout)
    for field, keep in (show or {}).items():
        _pivot_show(pt, str(field), [str(k) for k in keep])
    if op.get("refresh", True):
        pt.RefreshTable()
    cx.after({})
    return info


def _pivot_show(pt: Any, field: str, keep: "list[str]") -> None:
    f = pt.PivotFields(field)
    items = list(f.PivotItems())
    names = {str(i.Name).lower() for i in items}
    wanted = {k.lower() for k in keep}
    if not wanted & names:
        raise Refusal(f"'{field}' has none of those items. It has: {', '.join(sorted(str(i.Name) for i in items))[:300]}.", "bad_step")
    for i in items:  # show first, then hide: Excel refuses to hide the last visible item
        if str(i.Name).lower() in wanted and not bool(i.Visible):
            i.Visible = True
    for i in items:
        if str(i.Name).lower() not in wanted and bool(i.Visible):
            i.Visible = False


def h_refresh(cx: Cx, op: dict) -> dict:
    info = {"op": "refresh", "where": str(cx.wb.Name), "summary": f"refresh every pivot table in {cx.wb.Name}"}
    if cx.planning:
        return info
    cx.undo({"kind": "none", "what": "pivot tables were refreshed", "harmless": True})
    n = 0
    for ws in cx.wb.Worksheets:
        for pt in ws.PivotTables():
            pt.RefreshTable()
            n += 1
    info["refreshed"] = n
    cx.after({})
    return info


def _name_now(wb: Any, name: str) -> "str | None":
    for n in wb.Names:
        if str(n.Name).lower() == name.lower():
            return str(n.RefersTo)
    return None


def h_name(cx: Cx, op: dict) -> dict:
    name = str(_need(op, "name")).strip()
    if not re.fullmatch(r"[A-Za-z_\\][A-Za-z0-9_.\\]{0,254}", name) or _CELL_RX.match(name):
        raise Refusal(f"'{name}' cannot be a name (letters, digits, _ and . only; it cannot look like a cell)", "bad_step")
    ref = str(_need(op, "refers_to")).strip()
    ref = ref if ref.startswith("=") else "=" + ref
    info = {"op": "name", "where": name, "summary": f"name {ref[1:]} as '{name}'"}
    if cx.planning:
        return info
    cx.undo({"kind": "name_set", "name": name, "prior": _name_now(cx.wb, name)})
    cx.wb.Names.Add(name, ref)
    cx.after({"refers_to": _name_now(cx.wb, name)})
    return info


def h_table(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    box = _clip_to_used(ws, parse_range(_need(op, "range")))
    name = str(op.get("name") or "").strip()
    info = {"op": "table", "where": f"{ws.Name}!{a1(*box)}", "summary": f"make {ws.Name}!{a1(*box)} a table"}
    if cx.planning:
        return info
    cx.undo({"kind": "table_added", "sheet": str(ws.Name), "before": [str(t.Name) for t in ws.ListObjects]})
    pythoncom, _ = _com()
    lo = ws.ListObjects.Add(1, _rng(ws, *box), pythoncom.Missing, 1)
    if name:
        lo.Name = name
    if op.get("style"):
        lo.TableStyle = str(op["style"])
    cx.after({"name": str(lo.Name)})
    info["name"] = str(lo.Name)
    return info


def _note_now(cell: Any) -> "str | None":
    try:
        c = cell.Comment
        return None if c is None else str(c.Text())
    except Exception:  # noqa: BLE001
        return None


def _note_put(cell: Any, text: "str | None") -> None:
    if _note_now(cell) is not None:
        cell.Comment.Delete()
    if text:
        cell.AddComment(text)


def h_note(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    r, c = parse_range(_need(op, "cell"))[:2]
    text = str(op.get("text") or "")
    info = {"op": "note", "where": f"{ws.Name}!{a1(r, c)}",
            "summary": (f"put a note on {ws.Name}!{a1(r, c)}" if text else f"remove the note on {ws.Name}!{a1(r, c)}")}
    if cx.planning:
        return info
    cell = ws.Cells(r, c)
    cx.undo({"kind": "note_set", "sheet": str(ws.Name), "cell": a1(r, c), "prior": _note_now(cell)})
    _note_put(cell, text)
    cx.after({"text": text})
    return info


_PRINT_KEYS = ("orientation", "fit_width", "fit_height", "print_area", "title_rows", "header", "footer")


def _print_now(ps: Any) -> dict:
    return {"Orientation": int(ps.Orientation), "Zoom": ps.Zoom, "FitToPagesWide": ps.FitToPagesWide,
            "FitToPagesTall": ps.FitToPagesTall, "PrintArea": str(ps.PrintArea), "PrintTitleRows": str(ps.PrintTitleRows),
            "CenterHeader": str(ps.CenterHeader), "CenterFooter": str(ps.CenterFooter)}


def _print_put(ps: Any, s: dict) -> None:
    for key in ("Orientation", "PrintArea", "PrintTitleRows", "CenterHeader", "CenterFooter"):
        if key in s:
            setattr(ps, key, s[key])
    if "Zoom" in s and s["Zoom"] not in (False, None):
        ps.Zoom = s["Zoom"]
    elif "FitToPagesWide" in s or "FitToPagesTall" in s:
        ps.Zoom = False
        ps.FitToPagesWide = s.get("FitToPagesWide") or False
        ps.FitToPagesTall = s.get("FitToPagesTall") or False


def h_print_setup(cx: Cx, op: dict) -> dict:
    ws = cx.sheet(op.get("sheet"))
    if not any(k in op for k in _PRINT_KEYS):
        raise Refusal(f"a 'print_setup' step needs one of: {', '.join(_PRINT_KEYS)}", "bad_step")
    new: dict[str, Any] = {}
    if "orientation" in op:
        o = str(op["orientation"]).lower()
        if o not in ("portrait", "landscape"):
            raise Refusal("orientation is portrait or landscape", "bad_step")
        new["Orientation"] = 2 if o == "landscape" else 1
    if "fit_width" in op or "fit_height" in op:
        new["Zoom"] = False
        new["FitToPagesWide"] = int(op["fit_width"]) if op.get("fit_width") else False
        new["FitToPagesTall"] = int(op["fit_height"]) if op.get("fit_height") else False
    if "print_area" in op:
        new["PrintArea"] = "" if not op["print_area"] else a1(*parse_range(op["print_area"]))
    if "title_rows" in op:
        if op["title_rows"]:
            t1, _c, t2, _c2 = parse_range(str(op["title_rows"]))
            new["PrintTitleRows"] = f"${t1}:${t2}"
        else:
            new["PrintTitleRows"] = ""
    if "header" in op:
        new["CenterHeader"] = str(op["header"] or "")
    if "footer" in op:
        new["CenterFooter"] = str(op["footer"] or "")
    info = {"op": "print_setup", "where": str(ws.Name), "summary": f"set how {ws.Name} prints ({', '.join(k for k in _PRINT_KEYS if k in op)})"}
    if cx.planning:
        return info
    ps = ws.PageSetup
    cx.undo({"kind": "print_set", "sheet": str(ws.Name), "prior": _print_now(ps)})
    _print_put(ps, new)
    cx.after({})
    return info


OPS = {
    "set": h_set, "format": h_set, "clear": h_clear,
    "add_sheet": h_add_sheet, "rename_sheet": h_rename_sheet, "delete_sheet": h_delete_sheet,
    "insert_rows": lambda cx, op: _insert(cx, op, True), "insert_columns": lambda cx, op: _insert(cx, op, False),
    "delete_rows": lambda cx, op: _delete(cx, op, True), "delete_columns": lambda cx, op: _delete(cx, op, False),
    "sort": h_sort, "filter": h_filter, "clear_filter": h_clear_filter,
    "autofit": h_autofit, "column_width": h_autofit, "freeze": h_freeze,
    "conditional_format": h_conditional_format, "validation": h_validation,
    "chart": h_chart, "chart_edit": h_chart_edit,
    "pivot": h_pivot, "pivot_edit": h_pivot_edit, "refresh": h_refresh,
    "name": h_name, "table": h_table, "note": h_note, "print_setup": h_print_setup,
}
#: Steps whose effect cannot be put back.
DESTRUCTIVE_OPS = ("delete_sheet", "delete_rows", "delete_columns")


def check_steps(ops: Any) -> "list[dict]":
    """The request's steps, or a :class:`Refusal` naming what is wrong. Pure."""
    if not isinstance(ops, list) or not ops:
        raise Refusal("give at least one step in 'ops'", "bad_request")
    if len(ops) > MAX_OPS:
        raise Refusal(f"at most {MAX_OPS} steps in one call", "too_many")
    for n, op in enumerate(ops, start=1):
        if not isinstance(op, dict) or str(op.get("op") or "") not in OPS:
            known = ", ".join(sorted(OPS))
            raise Refusal(f"step {n} has no known 'op'. The steps are: {known}.", "bad_step")
    return ops


_RANGE_KEYS = ("cell", "range", "data", "print_area", "columns")


def split_sheet(op: dict) -> dict:
    """``{"range": "Sales!A1:D49"}`` as ``{"sheet": "Sales", "range":
    "A1:D49"}``: the way a range is usually written. A step that names two
    different sheets is refused. Pure."""
    out = dict(op)
    for key in _RANGE_KEYS:
        v = out.get(key)
        if not isinstance(v, str) or "!" not in v:
            continue
        sheet, _, rest = v.rpartition("!")
        sheet = sheet.strip().strip("'").replace("''", "'")
        have = str(out.get("sheet") or "").strip()
        if have and have.lower() != sheet.lower():
            raise Refusal(f"'{key}' names the sheet '{sheet}' but 'sheet' says '{have}'", "bad_step")
        out[key], out["sheet"] = rest, sheet
    return out


def _guard_book(wb: Any) -> None:
    if bool(wb.ReadOnly):
        raise Refusal(f"'{wb.Name}' is open read-only in Excel, so it cannot be changed.", "read_only")


def _run_steps(cx: Cx, ops: "list[dict]") -> None:
    for n, op in enumerate(ops, start=1):
        cx.i = n
        try:
            info = OPS[str(op["op"])](cx, split_sheet(dict(op, op=str(op["op"]))))
        except _Deferred as d:
            info = {"op": str(op["op"]), "where": str(d), "summary": f"{str(op['op']).replace('_', ' ')} on the new sheet '{d}'"}
        except Refusal as r:
            raise Refusal(f"step {n} ({op.get('op')}): {r.words}", r.code) from None
        except Exception as exc:  # noqa: BLE001 — Excel's own refusal of the step
            if _is_busy(exc):
                raise
            raise Refusal(f"step {n} ({op.get('op')}): Excel refused it: {_excel_words(exc)}", "excel_refused") from None
        cx.steps.append(info)


def op_plan(req: dict) -> dict:
    ops = check_steps(req.get("ops"))
    app, wb = _find_book(_apps(), req.get("workbook"))
    _guard_book(wb)
    cx = Cx(app, wb, planning=True, default_sheet=req.get("sheet") or "")
    _run_steps(cx, ops)
    return {
        "ok": True, "workbook": str(wb.Name), "path": str(wb.FullName), "steps": cx.steps,
        "overwrites": cx.overwrites, "formulas_lost": cx.formulas_lost, "destructive": cx.destructive,
        "autosave": _autosave(wb), "calculation": _calc_words(app),
    }


def _autosave(wb: Any) -> bool:
    try:
        return bool(wb.AutoSaveOn)
    except Exception:  # noqa: BLE001
        return False


def op_apply(req: dict) -> dict:
    ops = check_steps(req.get("ops"))
    app, wb = _find_book(_apps(), req.get("workbook"))
    _guard_book(wb)
    undo_path = str(req.get("undo_path") or "")
    # Everything is checked before anything is changed.
    _run_steps(Cx(app, wb, planning=True, default_sheet=req.get("sheet") or ""), ops)
    copy = str(req.get("copy_path") or "")
    if copy and any(str(o.get("op")) in DESTRUCTIVE_OPS for o in ops):
        wb.SaveCopyAs(copy)  # what the workbook was, before a step that cannot be put back
    else:
        copy = ""
    cx = Cx(app, wb, planning=False, undo_path=undo_path, default_sheet=req.get("sheet") or "")
    before_errors = {str(ws.Name): set(_error_cells(ws)) for ws in wb.Worksheets}
    if undo_path:
        with open(undo_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"head": {"workbook": str(wb.Name), "path": str(wb.FullName), "steps": len(ops)}}) + "\n")
    try:
        _run_steps(cx, ops)
    except BaseException as exc:
        # All or nothing: put back what the finished steps changed.
        rolled = _restore_file(app, wb, undo_path, forgiving=True) if undo_path else {"left": ["no undo record was kept"]}
        words = exc.words if isinstance(exc, Refusal) else (BUSY_WORDS if _is_busy(exc) else f"Excel refused: {_excel_words(exc)}")
        done = len(cx.steps)
        if rolled.get("left"):
            return {"ok": False, "code": "partly_done", "partly": True, "done_steps": done,
                    "error": f"{words} The {done} step(s) before it were made and could not all be put back: {'; '.join(rolled['left'])[:400]}"}
        back = f" The {done} step(s) before it were put back." if done else ""
        return {"ok": False, "code": getattr(exc, "code", "excel_refused"), "unchanged": True, "error": f"{words}{back}"}
    new_errors: list[str] = []
    for ws in wb.Worksheets:
        name = str(ws.Name)
        for cell in _error_cells(ws):
            if cell not in before_errors.get(name, set()) and len(new_errors) < 20:
                new_errors.append(f"{name}!{cell}")
    return {
        "ok": True, "workbook": str(wb.Name), "path": str(wb.FullName), "steps": cx.steps,
        "changes": cx.changes, "changed_cells": cx.changed_cells, "new_errors": new_errors,
        "destructive": cx.destructive, "copy": copy, "autosave": _autosave(wb),
        "calculation": _calc_words(app), "saved": bool(wb.Saved),
    }


# -------------------------------------------------------------------- restore --


def read_undo(path: str) -> "tuple[dict, list[dict]]":
    """The undo file as (head, steps): each step ``{"i", "inverse", "after"}``,
    where a step with no ``after`` was cut off part-way. Pure."""
    head: dict = {}
    steps: dict[int, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a line cut off by a kill
            if "head" in row:
                head = row["head"]
            elif "inverse" in row:
                steps.setdefault(int(row["i"]), {"i": int(row["i"])})["inverse"] = row["inverse"]
            elif "after" in row:
                steps.setdefault(int(row["i"]), {"i": int(row["i"])})["after"] = row["after"]
    return head, [steps[k] for k in sorted(steps) if "inverse" in steps[k]]


def _undo_step(app: Any, wb: Any, step: dict, forgiving: bool) -> "tuple[int, list[str]]":
    """Put one step back. Returns (cells or things restored, what was left
    alone and why). ``forgiving`` (a rollback inside ``apply``) skips the
    "changed since" checks: nothing else can have changed in between."""
    inv, after = step["inverse"], step.get("after")
    kind = inv.get("kind")
    left: list[str] = []
    if kind == "none":
        if after is None:
            # The step was written down and then Excel refused it: it never
            # happened, so there is nothing to put back (and nothing lost).
            return 0, []
        return 0, ([] if inv.get("harmless") else [f"{inv.get('what')}: that cannot be put back"])
    if kind == "sheet_renamed":
        for s in wb.Sheets:
            if str(s.Name) == inv["to"]:
                s.Name = inv["from"]
                return 1, left
        return 0, [f"the sheet '{inv['to']}' is no longer there"]
    if kind == "sheet_added":
        for s in wb.Worksheets:
            if str(s.Name) == inv["name"]:
                if not forgiving and int(app.WorksheetFunction.CountA(s.Cells)) and after is not None:
                    return 0, [f"the sheet '{inv['name']}' has been filled in since, so it was kept"]
                alerts = app.DisplayAlerts
                try:
                    app.DisplayAlerts = False
                    s.Delete()
                finally:
                    app.DisplayAlerts = alerts
                return 1, left
        return 0, left
    if kind == "name_set":
        now = _name_now(wb, inv["name"])
        if not forgiving and after is not None and now != after.get("refers_to"):
            return 0, [f"the name '{inv['name']}' was changed since"]
        if inv.get("prior"):
            wb.Names.Add(inv["name"], inv["prior"])
        elif now is not None:
            wb.Names(inv["name"]).Delete()
        return 1, left
    try:
        ws = _sheet(wb, inv.get("sheet"))
    except Refusal:
        return 0, [f"the sheet '{inv.get('sheet')}' is no longer there"]
    if kind == "cells":
        r1, c1, r2, c2 = parse_range(inv["address"])
        rows, cols = r2 - r1 + 1, c2 - c1 + 1
        rng = _rng(ws, r1, c1, r2, c2)
        done = 0
        if "formulas" in inv:
            prior = inv["formulas"]
            now = _formulas(rng, rows, cols)
            want = (after or {}).get("formulas")
            if forgiving or want is None or now == want:
                rng.Formula = tuple(tuple(r) for r in prior)
                done = rows * cols
            else:
                skipped = []
                for i in range(rows):
                    for j in range(cols):
                        if now[i][j] == want[i][j]:
                            if now[i][j] != prior[i][j]:
                                ws.Cells(r1 + i, c1 + j).Formula = prior[i][j]
                            done += 1
                        else:
                            skipped.append(a1(r1 + i, c1 + j))
                if skipped:
                    shown = ", ".join(skipped[:8]) + (f" and {len(skipped) - 8} more" if len(skipped) > 8 else "")
                    left.append(f"{ws.Name}!{shown} changed again since, so {'it was' if len(skipped) == 1 else 'they were'} left as {'it is' if len(skipped) == 1 else 'they are'}")
        if "formats" in inv:
            _restore_formats(rng, rows, cols, inv["formats"])
            if any("lost" in v for v in inv["formats"].values()):
                left.append(f"some formatting on {ws.Name}!{inv['address']} was mixed and could not be put back exactly")
            done = done or rows * cols
        return done, left
    if kind == "lines_inserted":
        lines = _lines(ws, inv["first"], inv["count"], inv["rows"])
        if not forgiving and int(app.WorksheetFunction.CountA(lines)):
            what = "rows" if inv["rows"] else "columns"
            return 0, [f"the inserted {what} on {ws.Name} have been filled in since, so they were kept"]
        lines.Delete()
        return 1, left
    if kind == "filter_set":
        if not inv.get("had") and bool(ws.AutoFilterMode):
            ws.AutoFilterMode = False
        elif inv.get("had"):
            try:
                _rng(ws, *parse_range(inv["address"])).AutoFilter(inv["field"])
            except Exception:  # noqa: BLE001
                left.append(f"the filter on {ws.Name} could not be cleared")
        return 1, left
    if kind == "widths":
        for k, w in enumerate(inv["widths"]):
            ws.Columns(inv["first"] + k).ColumnWidth = w
        return 1, left
    if kind == "freeze":
        wb.Activate()
        ws.Activate()
        win = app.ActiveWindow
        win.FreezePanes = False
        win.SplitRow, win.SplitColumn = inv["row"], inv["col"]
        if inv["frozen"]:
            win.FreezePanes = True
        return 1, left
    if kind == "cf_added":
        fcs = _rng(ws, *parse_range(inv["address"])).FormatConditions
        now = int(fcs.Count)
        if not forgiving and after is not None and now != after.get("count"):
            return 0, [f"the colour rules on {ws.Name}!{inv['address']} were changed since"]
        for idx in range(now, inv["before"], -1):
            fcs.Item(idx).Delete()
        return 1, left
    if kind == "validation_set":
        rng = _rng(ws, *parse_range(inv["address"]))
        if not forgiving and after is not None and _validation_now(rng) != after.get("now"):
            return 0, [f"the rule on {ws.Name}!{inv['address']} was changed since"]
        _validation_put(rng, inv.get("prior"))
        return 1, left
    if kind in ("chart_added", "table_added"):
        name = (after or {}).get("name")
        coll = ws.ChartObjects() if kind == "chart_added" else ws.ListObjects
        for obj in list(coll):
            n = str(obj.Name)
            if (name and n == name) or (not name and n not in inv.get("before", [])):
                if kind == "chart_added":
                    obj.Delete()
                else:
                    obj.Unlist()
                return 1, left
        return 0, left
    if kind == "chart_changed":
        try:
            _chart_put(_chart_obj(ws, inv["name"]).Chart, inv["prior"])
        except Refusal:
            return 0, [f"the chart '{inv['name']}' is no longer there"]
        return 1, left
    if kind == "pivot_added":
        name = (after or {}).get("name")
        for pt in list(ws.PivotTables()):
            if not name or str(pt.Name) == name:
                pt.TableRange2.Clear()
        if inv.get("new_sheet") and not int(app.WorksheetFunction.CountA(ws.Cells)):
            alerts = app.DisplayAlerts
            try:
                app.DisplayAlerts = False
                ws.Delete()
            finally:
                app.DisplayAlerts = alerts
        return 1, left
    if kind == "pivot_changed":
        try:
            pt = _pivot_obj(ws, inv["name"])
        except Refusal:
            return 0, [f"the pivot table '{inv['name']}' is no longer there"]
        _pivot_apply(pt, inv["layout"])
        for field, keep in (inv.get("items") or {}).items():
            _pivot_show(pt, field, keep)
        return 1, left
    if kind == "note_set":
        r, c = parse_range(inv["cell"])[:2]
        cell = ws.Cells(r, c)
        if not forgiving and after is not None and (_note_now(cell) or "") != (after.get("text") or ""):
            return 0, [f"the note on {ws.Name}!{inv['cell']} was changed since"]
        _note_put(cell, inv.get("prior"))
        return 1, left
    if kind == "print_set":
        _print_put(ws.PageSetup, inv["prior"])
        return 1, left
    return 0, [f"an unknown kind of change ({kind})"]


def _restore_file(app: Any, wb: Any, path: str, *, forgiving: bool) -> dict:
    _head, steps = read_undo(path)
    restored = 0
    left: list[str] = []
    for step in reversed(steps):
        try:
            n, why = _undo_step(app, wb, step, forgiving)
        except Exception as exc:  # noqa: BLE001
            if _is_busy(exc):
                raise
            n, why = 0, [f"step {step['i']} could not be put back: Excel refused: {_excel_words(exc)}"]
        restored += n
        left.extend(why)
    return {"restored": restored, "left": left, "steps": len(steps)}


def op_restore(req: dict) -> dict:
    path = str(req.get("undo_path") or "")
    head, steps = read_undo(path)
    if not steps:
        raise Refusal("there is nothing recorded to put back", "nothing")
    apps = _apps()
    try:
        app, wb = _find_book(apps, head.get("path") or head.get("workbook"))
    except Refusal:
        app, wb = _find_book(apps, head.get("workbook"))
    _guard_book(wb)
    out = _restore_file(app, wb, path, forgiving=bool(req.get("forgiving")))
    return {"ok": True, "workbook": str(wb.Name), **out}


HANDLERS = {"look": op_look, "snapshot": op_snapshot, "goto": op_goto, "plan": op_plan, "apply": op_apply, "restore": op_restore}


def answer(request: Any) -> dict:
    """One request, one answer. Never raises."""
    if not isinstance(request, dict) or request.get("op") not in HANDLERS:
        return {"ok": False, "code": "bad_request", "error": "unknown request"}
    if sys.platform != "win32":
        return {"ok": False, "code": "not_windows", "error": "Working in an open Excel workbook needs Excel on Windows."}
    try:
        pythoncom, _ = _com()
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "no_bridge", "error": "This copy of Iron Jarvis cannot talk to Excel (its Windows bridge is missing)."}
    pythoncom.CoInitialize()
    try:
        last: "BaseException | None" = None
        for attempt in range(1 if request["op"] in ("apply", "restore") else 4):
            try:
                return HANDLERS[request["op"]](request)
            except Refusal as r:
                return {"ok": False, "code": r.code, "error": r.words}
            except Exception as exc:  # noqa: BLE001
                last = exc
                if not _is_busy(exc):
                    break
                time.sleep(0.5 * (attempt + 1))
        if last is not None and _is_busy(last):
            return {"ok": False, "code": "busy", "error": BUSY_WORDS}
        return {"ok": False, "code": "excel_refused", "error": f"Excel refused: {_excel_words(last) if last else 'unknown'}"}
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def main(argv: "list[str] | None" = None) -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8") or "null")
    except Exception:  # noqa: BLE001
        request = None
    out = answer(request)
    sys.stdout.write(json.dumps(out, ensure_ascii=True, default=str))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a child process
    raise SystemExit(main())
