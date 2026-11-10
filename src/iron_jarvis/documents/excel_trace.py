"""How a number in a workbook is built, and what would change it (v1.467.0).

Four questions about one workbook, a saved file or (through a ``live:`` path)
the one open in Excel:

* **how**       which cells feed a cell, level by level, down to the typed
                inputs it rests on;
* **affects**   which cells use a cell, level by level;
* **what_if**   what some cells would come to if others held different
                values. NOTHING IS CHANGED: the workbook is worked out again
                in memory with the new values in place;
* **goal_seek** the value one input must take for a formula to reach a target.

The first two read the formulas' own references (the engine's tokenizer). The
last two work the formulas out with ``documents/excel_formula`` (the engine
checked against Excel's own answers). That engine does not know every Excel
function, so each what-if first works out the cells as they ARE and compares
with the results Excel saved: a difference, or a formula it cannot work out,
is said plainly and no figure is given for that cell.

Pure and bounded (nodes, formulas scanned, a deadline); BLOCKING, so the tool
runs it off the event loop.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult
from .excel_formula import ExcelError, FormulaError, Grid, _col_letters, _col_num, _tokenize, from_serial

MAX_DEPTH = 6
MAX_NODES = 120
MAX_RANGE_SHOWN = 4
#: With no cells named to watch, a what-if reports this many end results.
MAX_DEFAULT_WATCH = 12
MAX_FORMULAS_SCANNED = 60_000
MAX_DEPENDENTS = 200
MAX_WATCH = 40
MAX_SET = 20
DEADLINE_S = 25.0
GOAL_STEPS = 80

_CELL_RX = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]{1,7})$")
_REF_RX = re.compile(r"^(?:(?:'((?:[^']|'')+)'|([^!']+))!)?\$?([A-Za-z]{1,3})\$?([0-9]{1,7})$")


@dataclass(frozen=True)
class Ref:
    sheet: str
    c1: int
    r1: int
    c2: int
    r2: int

    @property
    def single(self) -> bool:
        return self.c1 == self.c2 and self.r1 == self.r2

    @property
    def cells(self) -> int:
        return (self.c2 - self.c1 + 1) * (self.r2 - self.r1 + 1)

    def label(self) -> str:
        first = f"{_col_letters(self.c1)}{self.r1}"
        if self.single:
            return f"{self.sheet}!{first}"
        return f"{self.sheet}!{first}:{_col_letters(self.c2)}{self.r2}"

    def holds(self, sheet: str, col: int, row: int) -> bool:
        return self.sheet.lower() == sheet.lower() and self.c1 <= col <= self.c2 and self.r1 <= row <= self.r2


def parse_cell(text: Any, default_sheet: str = "") -> "tuple[str, int, int]":
    """``"Monthly!B13"`` / ``"'My Sheet'!$B$13"`` / ``"B13"`` as (sheet, col,
    row). Raises ``ValueError`` in plain words."""
    raw = str(text or "").strip()
    m = _REF_RX.match(raw)
    if not m:
        raise ValueError(f"'{raw}' is not a cell like Sheet!B13")
    sheet = (m.group(1) or m.group(2) or default_sheet or "").replace("''", "'").strip()
    if not sheet:
        raise ValueError(f"say which sheet '{raw}' is on (write it as Sheet!{raw})")
    return sheet, _col_num(m.group(3)), int(m.group(4))


def refs_of(formula: str, sheet: str, grid: Grid) -> "list[Ref]":
    """The cells and ranges a formula reads, in the order written, each once.
    A defined name is followed to what it points at. Never raises."""
    try:
        tokens = _tokenize(formula[1:] if formula.startswith("=") else formula)
    except FormulaError:
        return []
    out: list[Ref] = []
    seen: set[Ref] = set()

    def add(ref: Ref) -> None:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)

    def canon(name: str) -> str:
        try:
            return grid.canon(name)
        except Exception:  # noqa: BLE001 - a sheet that is not there: keep the name as written
            return name

    i, cur = 0, sheet
    while i < len(tokens):
        kind, val = tokens[i]
        if kind == "sheet":
            name = val[:-1]
            if name.startswith("'"):
                name = name[1:-1].replace("''", "'")
            cur = canon(name)
            i += 1
            continue
        if kind == "cell":
            m = _CELL_RX.match(val)
            c1, r1 = _col_num(m.group(1)), int(m.group(2))  # type: ignore[union-attr]
            c2, r2 = c1, r1
            if i + 2 < len(tokens) and tokens[i + 1][1] == ":":
                j = i + 2
                if tokens[j][0] == "sheet":
                    j += 1
                if j < len(tokens) and tokens[j][0] == "cell":
                    mb = _CELL_RX.match(tokens[j][1])
                    c2, r2 = _col_num(mb.group(1)), int(mb.group(2))  # type: ignore[union-attr]
                    i = j
            add(Ref(cur, min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)))
        elif kind == "colrange":
            a, b = val.replace("$", "").split(":")
            try:
                last = max(1, grid.max_row(cur))
            except Exception:  # noqa: BLE001
                last = 1
            add(Ref(cur, min(_col_num(a), _col_num(b)), 1, max(_col_num(a), _col_num(b)), last))
        elif kind == "ident":
            try:
                dest = grid.defined(val)
            except Exception:  # noqa: BLE001
                dest = None
            if dest:
                parts = dest[1].split(":")
                ma, mb = _CELL_RX.match(parts[0]), _CELL_RX.match(parts[-1])
                if ma and mb:
                    add(Ref(canon(dest[0]), _col_num(ma.group(1)), int(ma.group(2)), _col_num(mb.group(1)), int(mb.group(2))))
        if kind != "sheet":
            cur = sheet
        i += 1
    return out


def show(v: Any) -> str:
    """A value for the answer: 15 significant digits, a date as a date."""
    if v is None or v == "":
        return "(empty)"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        f = float(v)
        if f == int(f) and abs(f) < 1e15:
            return f"{int(f):,}"
        return f"{float(f'{f:.15g}'):,}"
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10] if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.isoformat(sep=" ")[:19]
    return str(v)[:80]


def _value(grid: Grid, sheet: str, col: int, row: int) -> "tuple[Any, str]":
    """(value, why not) of a cell as the workbook has it."""
    grid._reads = 0
    try:
        return grid.get(sheet, col, row), ""
    except ExcelError as exc:
        return exc.code, ""
    except FormulaError as exc:
        return None, str(exc)


def _formula(grid: Grid, sheet: str, col: int, row: int) -> "str | None":
    try:
        return grid.formula(sheet, col, row)
    except Exception:  # noqa: BLE001
        return None


def _label_of(grid: Grid, sheet: str, col: int, row: int) -> str:
    """The words beside a cell that say what it is: the nearest text to its
    left on the row, else above it in the column (bounded)."""
    try:
        for c in range(col - 1, max(0, col - 6), -1):
            v = grid.saved(sheet, c, row)
            if isinstance(v, str) and v.strip() and not v.startswith("="):
                return v.strip()[:40]
        for r in range(row - 1, max(0, row - 30), -1):
            v = grid.saved(sheet, col, r)
            if isinstance(v, str) and v.strip() and not v.startswith("="):
                return v.strip()[:40]
            if v is None and _formula(grid, sheet, col, r) is None and r < row - 1:
                break
    except Exception:  # noqa: BLE001
        return ""
    return ""


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0
        self.cut = False
        self.deadline = time.monotonic() + DEADLINE_S

    def spend(self) -> bool:
        self.nodes += 1
        if self.nodes > MAX_NODES or time.monotonic() > self.deadline:
            self.cut = True
            return False
        return True


def how(grid: Grid, sheet: str, col: int, row: int, depth: int = 3) -> dict:
    """The cells that feed one cell, as indented lines, plus the typed inputs
    it rests on."""
    depth = max(1, min(int(depth or 3), MAX_DEPTH))
    sheet = grid.canon(sheet)
    budget = _Budget()
    lines: list[str] = []
    inputs: dict[str, str] = {}
    seen: set[tuple[str, int, int]] = set()

    def cell_line(s: str, c: int, r: int, level: int, quiet: bool = False) -> None:
        if not budget.spend():
            return
        value, why = _value(grid, s, c, r)
        f = _formula(grid, s, c, r)
        name = f"{s}!{_col_letters(c)}{r}"
        label = _label_of(grid, s, c, r)
        tag = f" ({label})" if label else ""
        pad = "  " * level
        if f is None:
            lines.append(f"{pad}{name}{tag} = {show(value)}  [typed in]")
            inputs.setdefault(name, f"{show(value)}{tag}")
            return
        shown = show(value) if not why else f"not worked out ({why})"
        lines.append(f"{pad}{name}{tag} = {shown}  [{f}]")
        key = (s.lower(), c, r)
        if key in seen:
            lines.append(f"{pad}  (shown above)")
            return
        seen.add(key)
        if level >= depth:
            if refs_of(f, s, grid) and not quiet:
                lines.append(f"{pad}  (goes deeper; ask with a larger depth, or about this cell)")
            return
        for ref in refs_of(f, s, grid):
            if ref.single:
                cell_line(ref.sheet, ref.c1, ref.r1, level + 1)
            else:
                range_lines(ref, level + 1)

    def range_lines(ref: Ref, level: int) -> None:
        if not budget.spend():
            return
        pad = "  " * level
        cells = [(c, r) for r in range(ref.r1, ref.r2 + 1) for c in range(ref.c1, ref.c2 + 1)]
        formulas = sum(1 for c, r in cells[:2000] if _formula(grid, ref.sheet, c, r) is not None)
        kind = "all typed in" if formulas == 0 else ("all formulas" if formulas == min(len(cells), 2000) else f"{formulas} formulas, the rest typed in")
        lines.append(f"{pad}{ref.label()}  [{ref.cells} cells, {kind}]")
        at_edge = level + 1 >= depth
        for c, r in cells[:MAX_RANGE_SHOWN]:
            cell_line(ref.sheet, c, r, level + 1, quiet=at_edge)
        if len(cells) > MAX_RANGE_SHOWN:
            lines.append(f"{pad}  (+{len(cells) - MAX_RANGE_SHOWN} more cells in {ref.label()})")
        if at_edge and formulas:
            lines.append(f"{pad}  (these go deeper; ask about one of them, or with a larger depth)")

    cell_line(sheet, col, row, 0)
    return {"lines": lines, "inputs": inputs, "cut": budget.cut}


def _all_formulas(grid: Grid) -> "tuple[list[tuple[str, int, int, str]], bool]":
    """Every formula cell of the workbook (bounded), and whether it was cut."""
    out: list[tuple[str, int, int, str]] = []
    wb = grid._formulas()
    for name in grid.sheetnames:
        try:
            ws = wb[name]
        except Exception:  # noqa: BLE001
            continue
        held = getattr(ws, "_cells", None)
        if not isinstance(held, dict):
            continue
        for (r, c), cell in held.items():
            v = cell.value
            if isinstance(v, str) and v.startswith("="):
                out.append((name, c, r, v))
                if len(out) >= MAX_FORMULAS_SCANNED:
                    return out, True
    return out, False


def affects(grid: Grid, sheet: str, col: int, row: int, depth: int = 3) -> dict:
    """The cells whose formulas use one cell, level by level."""
    depth = max(1, min(int(depth or 3), MAX_DEPTH))
    sheet = grid.canon(sheet)
    formulas, cut = _all_formulas(grid)
    parsed = [(s, c, r, f, refs_of(f, s, grid)) for s, c, r, f in formulas]
    lines: list[str] = []
    seen = {(sheet.lower(), col, row)}
    level_cells = [(sheet, col, row)]
    total = 0
    deadline = time.monotonic() + DEADLINE_S
    for level in range(1, depth + 1):
        nxt: list[tuple[str, int, int]] = []
        for s, c, r, f, refs in parsed:
            key = (s.lower(), c, r)
            if key in seen:
                continue
            if any(ref.holds(ts, tc, tr) for ref in refs for ts, tc, tr in level_cells):
                seen.add(key)
                nxt.append((s, c, r))
                total += 1
                if total <= MAX_DEPENDENTS:
                    value, why = _value(grid, s, c, r)
                    label = _label_of(grid, s, c, r)
                    tag = f" ({label})" if label else ""
                    lines.append(f"{'  ' * (level - 1)}{s}!{_col_letters(c)}{r}{tag} = {show(value) if not why else 'not worked out'}  [{f}]")
            if time.monotonic() > deadline:
                cut = True
                break
        if not nxt or cut and time.monotonic() > deadline:
            break
        level_cells = nxt
    if total > MAX_DEPENDENTS:
        lines.append(f"(+{total - MAX_DEPENDENTS} more cells)")
    return {"lines": lines, "count": total, "cut": cut, "scanned": len(formulas)}


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= max(1e-9, 1e-9 * max(abs(float(a)), abs(float(b))))
    if hasattr(b, "isoformat") and isinstance(a, (int, float)):
        try:
            return from_serial(float(a)).date() == b.date()
        except Exception:  # noqa: BLE001
            return False
    return a == b


def _typed(v: Any) -> Any:
    """A what-if value as a cell would hold it ("8%" is 0.08)."""
    if isinstance(v, str):
        t = v.strip().replace(",", "").replace("$", "")
        if re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)%", t):
            return float(t[:-1]) / 100.0
        if re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)", t):
            return float(t)
        if t.upper() in ("TRUE", "FALSE"):
            return t.upper() == "TRUE"
    return v


def what_if(grid: Grid, changes: "dict[str, Any]", watch: "list[str]", default_sheet: str = "") -> dict:
    """What the watched cells come to with other cells set to new values.
    Nothing in the workbook is changed."""
    if not changes:
        raise ValueError("give the cells to change in 'set', for example {\"Assumptions!B3\": 0.08}")
    if len(changes) > MAX_SET or len(watch) > MAX_WATCH:
        raise ValueError(f"at most {MAX_SET} cells to change and {MAX_WATCH} to watch")
    sets = []
    for where, value in changes.items():
        s, c, r = parse_cell(where, default_sheet)
        sets.append((grid.canon(s), c, r, _typed(value)))
    watched = []
    for where in watch:
        s, c, r = parse_cell(where, default_sheet)
        watched.append((grid.canon(s), c, r))
    more = 0
    if not watched:
        # Nothing named: report the END results the changed cells lead to
        # (cells no other formula uses), not every cell on the way.
        reached: list[tuple[str, int, int]] = []
        for s, c, r, _v in sets:
            for cell in _dependents_cells(grid, s, c, r, MAX_DEPTH):
                if cell not in reached:
                    reached.append(cell)
        if not reached:
            raise ValueError("nothing uses those cells, so nothing would change; name the cells to watch in 'watch'")
        ends = _end_results(grid, reached)
        watched = (ends or reached)[:MAX_DEFAULT_WATCH]
        more = len(reached) - len(watched)
    grid.fresh, grid.overrides = True, {}
    grid.reset()
    rows = []
    for s, c, r in watched:
        name = f"{s}!{_col_letters(c)}{r}"
        base, why = _value(grid, s, c, r)
        saved = grid.saved(s, c, r)
        row: dict[str, Any] = {"cell": name, "label": _label_of(grid, s, c, r), "formula": _formula(grid, s, c, r) or "", "now": base}
        if why:
            row["problem"] = f"Jarvis cannot work this cell out ({why}), so it gives no what-if for it"
        elif saved is not None and not _close(base, saved):
            row["problem"] = (f"Jarvis works this cell out as {show(base)} but Excel saved {show(saved)}, "
                              "so its what-if for this cell is not trusted")
        rows.append(row)
    was = []
    for s, c, r, v in sets:
        now, _why = _value(grid, s, c, r)
        was.append({"cell": f"{s}!{_col_letters(c)}{r}", "label": _label_of(grid, s, c, r), "now": now, "to": v,
                    "formula": _formula(grid, s, c, r) or ""})
    grid.overrides = {(s.lower(), c, r): v for s, c, r, v in sets}
    grid.reset()
    for row, (s, c, r) in zip(rows, watched):
        if "problem" in row:
            continue
        new, why = _value(grid, s, c, r)
        if why:
            row["problem"] = f"with the new values Jarvis cannot work this cell out ({why})"
            continue
        row["then"] = new
        if isinstance(new, (int, float)) and isinstance(row["now"], (int, float)) and not isinstance(new, bool):
            row["change"] = float(new) - float(row["now"])
    grid.overrides, grid.fresh = {}, False
    grid.reset()
    return {"set": was, "watch": rows, "more": more}


def _dependents_cells(grid: Grid, sheet: str, col: int, row: int, depth: int) -> "list[tuple[str, int, int]]":
    formulas, _cut = _all_formulas(grid)
    parsed = [(s, c, r, refs_of(f, s, grid)) for s, c, r, f in formulas]
    seen = {(sheet.lower(), col, row)}
    level = [(sheet, col, row)]
    out: list[tuple[str, int, int]] = []
    for _ in range(depth):
        nxt = []
        for s, c, r, refs in parsed:
            if (s.lower(), c, r) in seen:
                continue
            if any(ref.holds(ts, tc, tr) for ref in refs for ts, tc, tr in level):
                seen.add((s.lower(), c, r))
                nxt.append((s, c, r))
        out.extend(nxt)
        if not nxt:
            break
        level = nxt
    return out


def _end_results(grid: Grid, cells: "list[tuple[str, int, int]]") -> "list[tuple[str, int, int]]":
    """Those of *cells* that no formula in the workbook uses: the figures a
    chain of formulas ends in (bounded; [] when the workbook is too large to
    tell cheaply)."""
    formulas, cut = _all_formulas(grid)
    if cut or len(formulas) > 8000 or len(cells) > 3000:
        return []
    refs = [ref for s, _c, _r, f in formulas for ref in refs_of(f, s, grid)]
    return [cell for cell in cells if not any(ref.holds(*cell) for ref in refs)]


def goal_seek(grid: Grid, target: str, goal: float, change: str, low: "float | None" = None,
              high: "float | None" = None, default_sheet: str = "") -> dict:
    """The value *change* must hold for *target* to come to *goal*. Nothing
    in the workbook is changed."""
    ts, tc, tr = parse_cell(target, default_sheet)
    cs, cc, cr = parse_cell(change, default_sheet)
    ts, cs = grid.canon(ts), grid.canon(cs)
    if _formula(grid, ts, tc, tr) is None:
        raise ValueError(f"{ts}!{_col_letters(tc)}{tr} is typed in, not a formula, so no input can change it")
    if _formula(grid, cs, cc, cr) is not None:
        raise ValueError(f"{cs}!{_col_letters(cc)}{cr} is a formula; choose a typed-in cell to change")
    grid.fresh, grid.overrides = True, {}
    grid.reset()
    start, why = _value(grid, cs, cc, cr)
    base, why2 = _value(grid, ts, tc, tr)
    saved = grid.saved(ts, tc, tr)
    try:
        if why or why2:
            raise ValueError(f"Jarvis cannot work {ts}!{_col_letters(tc)}{tr} out ({why or why2}), so it cannot search for a value")
        if saved is not None and not _close(base, saved):
            raise ValueError(f"Jarvis works {ts}!{_col_letters(tc)}{tr} out as {show(base)} but Excel saved {show(saved)}, so its search is not trusted")
        if not isinstance(start, (int, float)) or isinstance(start, bool) or not isinstance(base, (int, float)):
            raise ValueError("both the cell to change and the target must hold numbers")

        def f(x: float) -> float:
            grid.overrides = {(cs.lower(), cc, cr): x}
            grid.reset()
            v, w = _value(grid, ts, tc, tr)
            if w or not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"with {show(x)} in {cs}!{_col_letters(cc)}{cr} the target cannot be worked out ({w or v})")
            return float(v) - float(goal)

        x0 = float(start)
        y0 = f(x0)
        if abs(y0) <= 1e-9 * max(1.0, abs(float(goal))):
            return {"answer": x0, "reaches": float(base), "start": x0, "steps": 0, "was": float(base)}
        lo, hi = low, high
        steps = 0
        if lo is None or hi is None:
            # Look outward from the present value for two points either side of the goal.
            span = max(abs(x0), 1.0)
            lo, hi = x0, x0
            ylo = yhi = y0
            for k in range(1, 40):
                steps += 2
                lo_try, hi_try = x0 - span * (2 ** k) / 4, x0 + span * (2 ** k) / 4
                ylo, yhi = f(lo_try), f(hi_try)
                if ylo * y0 <= 0:
                    lo, hi = lo_try, x0
                    yhi = y0
                    break
                if yhi * y0 <= 0:
                    lo, hi = x0, hi_try
                    ylo = y0
                    break
            else:
                raise ValueError("no value of that cell reaches the goal (tried far above and below its present value)")
        else:
            lo, hi = float(lo), float(hi)
            ylo, yhi = f(lo), f(hi)
            if ylo * yhi > 0:
                raise ValueError(f"between {show(lo)} and {show(hi)} the target never crosses {show(goal)}")
        for _ in range(GOAL_STEPS):
            steps += 1
            mid = (lo + hi) / 2
            ymid = f(mid)
            if abs(ymid) <= 1e-10 * max(1.0, abs(float(goal))) or abs(hi - lo) <= 1e-12 * max(1.0, abs(mid)):
                lo = hi = mid
                break
            if ylo * ymid <= 0:
                hi, yhi = mid, ymid
            else:
                lo, ylo = mid, ymid
        answer = (lo + hi) / 2
        reached = f(answer) + float(goal)
        return {"answer": answer, "reaches": reached, "start": x0, "steps": steps, "was": float(base)}
    finally:
        grid.overrides, grid.fresh = {}, False
        grid.reset()


# ----------------------------------------------------------------------- tool --


def _money(v: Any) -> str:
    return show(v)


class ExcelTraceTool(Tool):
    name = "excel_trace"
    description = (
        "Explain or test a number in a workbook WITHOUT changing anything. path is a saved workbook, or "
        "'live:<workbook name>' ('live:' alone for the one in front) for a workbook open in Excel. "
        "mode 'how' (default): the cells that feed 'cell', level by level, down to the typed-in inputs it "
        "rests on; use it to answer 'how is this calculated' and to find the cause of an error. "
        "mode 'affects': the cells whose formulas use 'cell'. "
        "mode 'what_if': set {\"Assumptions!B3\": 0.08, ...} and watch [\"Monthly!B13\", ...]: what the "
        "watched cells come to with those values (leave watch out to see what the changed cells feed). "
        "mode 'goal_seek': the value 'change' must hold for 'cell' to come to 'goal' (low/high optional). "
        "Write cells as Sheet!B13. In your answer cite cells the same way so the user can find them."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workbook path, or live:<name> for one open in Excel"},
            "mode": {"type": "string", "enum": ["how", "affects", "what_if", "goal_seek"]},
            "cell": {"type": "string", "description": "Sheet!B13 (how, affects, and the target of goal_seek)"},
            "sheet": {"type": "string", "description": "The sheet for cells written without one"},
            "depth": {"type": "integer", "description": "Levels to follow (default 3, at most 6)"},
            "set": {"type": "object", "description": "what_if: {cell: new value}"},
            "watch": {"type": "array", "items": {"type": "string"}, "description": "what_if: cells to report"},
            "goal": {"type": "number", "description": "goal_seek: the value the target should reach"},
            "change": {"type": "string", "description": "goal_seek: the typed-in cell to vary"},
            "low": {"type": "number"},
            "high": {"type": "number"},
        },
        "required": ["path"],
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .excel_tools import _open_error, _read_target

        path, ok, reason = await _read_target(str(args.get("path", "")), ctx)
        if not ok:
            return ToolResult(ok=False, error=reason)
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such workbook: {args.get('path')}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            return ToolResult(ok=False, error="excel_trace reads .xlsx and .xlsm workbooks")
        mode = str(args.get("mode") or "how").lower()

        def work() -> ToolResult:
            from .excel_recalc import too_large

            big = too_large(path)
            if big:
                return ToolResult(ok=False, error=f"{path.name}: {big}")
            try:
                grid = Grid(path)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, error=_open_error(exc, path))
            try:
                res = _run(grid, path, mode, args)
                if res.ok and str(args.get("path") or "").strip().lower().startswith("live:"):
                    # The workbook is open in Excel: say how to write a cell
                    # so the user can jump to it (documents/excel_live.py).
                    from .excel_live import link_hint

                    sheet, _, cell = _example_cell(res.data).partition("!")
                    return ToolResult(ok=True, output=res.output + "\n" + link_hint(path.name, sheet, cell), data=res.data)
                return res
            except (ValueError, FormulaError) as exc:
                return ToolResult(ok=False, error=str(exc))
            finally:
                grid.close()

        return await asyncio.to_thread(work)


def _example_cell(data: Any) -> str:
    """One cell this answer is about, as Sheet!B13 ("" when it names none)."""
    if not isinstance(data, dict):
        return ""
    for key in ("cell", "target"):
        if isinstance(data.get(key), str) and "!" in data[key]:
            return data[key]
    for row in data.get("watch") or []:
        if isinstance(row, dict) and "!" in str(row.get("cell") or ""):
            return str(row["cell"])
    return ""


def _run(grid: Grid, path: Path, mode: str, args: dict[str, Any]) -> ToolResult:
    default = str(args.get("sheet") or "").strip() or grid.active
    name = path.name
    if mode in ("how", "affects"):
        s, c, r = parse_cell(args.get("cell"), default)
        where = f"{grid.canon(s)}!{_col_letters(c)}{r}"
        if mode == "how":
            got = how(grid, s, c, r, int(args.get("depth") or 3))
            lines = [f"How {where} in {name} is built (each line: cell (what it is) = value [formula]; indented lines feed the one above):"]
            lines += got["lines"]
            if got["inputs"]:
                lines.append("")
                lines.append("typed-in inputs it rests on: " + "; ".join(f"{k} = {v}" for k, v in list(got["inputs"].items())[:30]))
            if got["cut"]:
                lines.append("(cut: the chain is larger than one answer shows; ask about a cell further down)")
            return ToolResult(ok=True, output="\n".join(lines), data={"cell": where, "inputs": got["inputs"], "cut": got["cut"]})
        got = affects(grid, s, c, r, int(args.get("depth") or 3))
        head = f"Cells in {name} that use {where} ({got['count']}; indented lines use the one above):"
        if not got["count"]:
            head = f"No formula in {name} uses {where}."
        lines = [head] + got["lines"]
        if got["cut"]:
            lines.append(f"(cut: only the first {got['scanned']:,} formulas were read)")
        return ToolResult(ok=True, output="\n".join(lines), data={"cell": where, "count": got["count"], "cut": got["cut"]})
    if mode == "what_if":
        changes = args.get("set")
        if not isinstance(changes, dict):
            raise ValueError("give the cells to change in 'set', for example {\"Assumptions!B3\": 0.08}")
        watch = [str(w) for w in (args.get("watch") or [])]
        got = what_if(grid, changes, watch, default)
        lines = [f"What if, in {name} (NOTHING WAS CHANGED; this is worked out on the side):"]
        for row in got["set"]:
            tag = f" ({row['label']})" if row["label"] else ""
            warn = "  NOTE: this cell holds a formula, which the what-if replaces" if row["formula"] else ""
            lines.append(f"  set {row['cell']}{tag}: {show(row['now'])} -> {show(row['to'])}{warn}")
        lines.append("then:")
        for row in got["watch"]:
            tag = f" ({row['label']})" if row["label"] else ""
            if "problem" in row:
                lines.append(f"  {row['cell']}{tag}: {row['problem']}")
                continue
            delta = f"  (change {row['change']:+,.2f})" if "change" in row else ""
            lines.append(f"  {row['cell']}{tag}: {show(row['now'])} -> {show(row['then'])}{delta}")
        if got.get("more"):
            lines.append(f"  (+{got['more']} more cells change on the way to these; name cells in 'watch' to see them)")
        return ToolResult(ok=True, output="\n".join(lines), data=got)
    if mode == "goal_seek":
        if args.get("goal") is None or not args.get("change") or not args.get("cell"):
            raise ValueError("goal_seek needs 'cell' (the formula), 'goal' (the value to reach) and 'change' (the typed-in cell to vary)")
        got = goal_seek(grid, str(args["cell"]), float(args["goal"]), str(args["change"]), args.get("low"), args.get("high"), default)
        ts, tc, tr = parse_cell(args["cell"], default)
        cs, cc, cr = parse_cell(args["change"], default)
        t, ch = f"{grid.canon(ts)}!{_col_letters(tc)}{tr}", f"{grid.canon(cs)}!{_col_letters(cc)}{cr}"
        lines = [
            f"Goal seek in {name} (NOTHING WAS CHANGED):",
            f"  for {t} to come to {show(args['goal'])}, {ch} must be {show(got['answer'])} (it is {show(got['start'])} now, and {t} is {show(got['was'])}).",
            f"  with that value {t} comes to {show(got['reaches'])}.",
        ]
        goal = float(args["goal"])
        miss = float(got["reaches"]) - goal
        exact = abs(miss) <= 1e-6 * max(1.0, abs(goal))
        if not exact:
            # A rounded figure on the way makes the target move in steps, so no
            # value lands on the goal exactly (Excel's own Goal Seek gives up
            # there). Say how close this is rather than call it the answer.
            lines.append(
                f"  NOT EXACT: {t} moves in steps (a rounded figure feeds it), so no value lands on "
                f"{show(goal)}; this is the closest, {show(abs(miss))} {'over' if miss > 0 else 'under'}."
            )
        return ToolResult(ok=True, output="\n".join(lines), data={"target": t, "change": ch, "exact": exact, **got})
    raise ValueError("mode is how, affects, what_if or goal_seek")
