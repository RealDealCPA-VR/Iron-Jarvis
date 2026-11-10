"""Live Excel (v1.466.0): work in a workbook the user has OPEN in Excel.

The file tools (``excel_read``, ``excel_edit``…) work on a saved file. These
work on the workbook as it is in Excel right now, unsaved changes included,
and Excel itself does the calculating.

Every question to Excel is ONE short-lived child (``iron_jarvis.excel_worker``,
killed at its deadline): a call into another program can wait forever, and
nothing that can wait forever runs in the daemon (the v1.410.2 rule).

What a change leaves behind, under ``<home>/excel-live/``:

* ``undo/<change id>.jsonl``  what each step replaced, written by the child
  BEFORE the step runs. The chat's Undo puts it back, leaving alone any cell
  changed again since.
* ``copies/``                 the workbook as it was, saved before a step that
  cannot be put back (deleting rows, columns or a sheet).
* ``snap/``                   a copy made for reading (the workbook map, a
  ``live:`` path handed to a file tool). Removed after use; swept after ten
  minutes.

All three hold the client's data, so the folder is left out of backups and
swept by age (:func:`sweep`).

Iron Jarvis never saves the workbook: the user does. Excel's own Ctrl+Z
history is cleared by a change made from outside (Excel's rule for every
program that drives it), which is why Jarvis keeps its own Undo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from ..tools.base import Reversibility, RiskClass, SpendCheck, Tool, ToolContext, ToolResult

log = logging.getLogger("iron_jarvis.documents.excel_live")

WORKER_SUBCOMMAND = "excel-worker"
DIRNAME = "excel-live"
#: Bounds on one question to Excel (seconds), on top of the child's own start.
WORKER_START_S = 15.0
LOOK_S, PLAN_S, APPLY_S, RESTORE_S, SNAPSHOT_S = 30.0, 30.0, 150.0, 150.0, 90.0
#: A change replacing more cells that hold data than this, or any formula with
#: a plain value, is shown on a card first even when edits are already allowed.
OVERWRITE_CARD_CELLS = 10
UNDO_KEEP_DAYS, COPY_KEEP_DAYS, SNAP_KEEP_S = 30, 14, 600
UNDO_KIND = "excel_live_restore"
LIVE_PREFIX = "live:"
NOT_WINDOWS = "Working in an open Excel workbook needs Excel on Windows."
NO_ANSWER = (
    "Excel did not answer in time. If a dialog box is open in Excel, or a cell is being "
    "typed in, finish that and try again."
)
CLEARS_CTRL_Z = "Excel's own Ctrl+Z history is cleared by a change made from outside; use Undo here instead."

EDIT_OPS = (
    "set", "format", "clear", "add_sheet", "rename_sheet", "delete_sheet", "insert_rows", "insert_columns",
    "delete_rows", "delete_columns", "sort", "filter", "clear_filter", "autofit", "column_width", "freeze",
    "conditional_format", "validation", "chart", "chart_edit", "pivot", "pivot_edit", "refresh", "name",
    "table", "note", "print_setup",
)


# ------------------------------------------------------------------ the child --


def _worker_argv() -> "list[str]":
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_SUBCOMMAND]
    return [sys.executable, "-m", "iron_jarvis.excel_worker"]


def _ask_worker(request: dict, timeout: float) -> dict:
    """One request to a fresh child. BLOCKING; never raises. A child that
    does not answer is an ordinary refusal, in words."""
    if sys.platform != "win32":
        return {"ok": False, "code": "not_windows", "error": NOT_WINDOWS}
    import os  # noqa: PLC0415

    if os.environ.get(OFF_ENV) == "off":
        return {"ok": False, "code": "off", "error": "Working in an open Excel workbook is turned off here."}
    from ..core import procs  # noqa: PLC0415

    try:
        result = procs.run(_worker_argv(), timeout=timeout + WORKER_START_S, input=json.dumps(request).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — a refused spawn
        return {"ok": False, "code": "no_child", "error": f"Iron Jarvis could not start its Excel helper ({type(exc).__name__})."}
    if result.timed_out:
        return {"ok": False, "code": "no_answer", "error": NO_ANSWER, "timed_out": True}
    try:
        out = json.loads((result.stdout or b"").decode("utf-8", errors="replace") or "null")
    except Exception:  # noqa: BLE001
        out = None
    if not isinstance(out, dict):
        return {"ok": False, "code": "no_answer", "error": "Iron Jarvis's Excel helper stopped without an answer."}
    return out


async def ask(request: dict, timeout: float) -> dict:
    return await asyncio.to_thread(_ask_worker, request, timeout)


_RUNNING: "tuple[float, bool]" = (0.0, False)


def excel_running() -> bool:
    """Whether an Excel process is running on this PC (remembered 3 s).
    BLOCKING (a process listing); never raises."""
    global _RUNNING
    now = time.monotonic()
    if now - _RUNNING[0] < 3.0:
        return _RUNNING[1]
    found = False
    if sys.platform == "win32":
        try:
            import psutil  # noqa: PLC0415

            for p in psutil.process_iter(["name"]):
                if str(p.info.get("name") or "").lower() == "excel.exe":
                    found = True
                    break
        except Exception:  # noqa: BLE001
            found = False
    _RUNNING = (now, found)
    return found


#: "off" = never start the child (the test suite: a test must not look into
#: the developer's own Excel). The tests of the real Excel take it away.
OFF_ENV = "IRONJARVIS_EXCEL_LIVE"
#: Forces :func:`excel_seen` ("0" / "1"). The test suite sets "0": what a
#: chat arms must not depend on whether the developer has Excel open.
SEEN_ENV = "IRONJARVIS_EXCEL_SEEN"
SEEN_TTL_S = 5.0
_SEEN: dict = {"at": 0.0, "value": False, "thread": None}


def excel_seen() -> bool:
    """Whether Excel is running, WITHOUT waiting: the last answer, refreshed in
    the background when it is older than a few seconds (the first call waits
    at most 80 ms for its answer). Safe on the event loop."""
    import os  # noqa: PLC0415
    import threading  # noqa: PLC0415

    forced = os.environ.get(SEEN_ENV)
    if forced in ("0", "1"):
        return forced == "1"
    if sys.platform != "win32":
        return False
    now = time.monotonic()
    t = _SEEN["thread"]
    if now - _SEEN["at"] > SEEN_TTL_S and (t is None or not t.is_alive()):
        first = _SEEN["at"] == 0.0

        def look() -> None:
            _SEEN["value"] = excel_running()
            _SEEN["at"] = time.monotonic()

        t = threading.Thread(target=look, name="excel-seen", daemon=True)
        _SEEN["thread"] = t
        t.start()
        if first:
            t.join(0.08)
    return bool(_SEEN["value"])


# -------------------------------------------------------------------- folders --


def live_dir(home: "str | Path", part: str) -> Path:
    d = Path(home) / DIRNAME / part
    d.mkdir(parents=True, exist_ok=True)
    return d


def sweep(home: "str | Path") -> None:
    """Remove what has aged out. BLOCKING; never raises."""
    now = time.time()
    for part, keep_s in (("undo", UNDO_KEEP_DAYS * 86400), ("copies", COPY_KEEP_DAYS * 86400), ("snap", SNAP_KEEP_S)):
        try:
            for f in (Path(home) / DIRNAME / part).iterdir():
                try:
                    if now - f.stat().st_mtime <= keep_s:
                        continue
                    if f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                    else:
                        f.unlink()
                except OSError:
                    continue
        except OSError:
            continue


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._-]+", "_", name)[:60] or "workbook"


def _read_refusal(path: str) -> str:
    """Why Jarvis may not read this open workbook, or "". Only a saved local
    file is judged: an unsaved Book1 or a OneDrive address has no folder."""
    try:
        p = Path(path)
        if "://" in path or not p.is_absolute():
            return ""
        from ..core.fs_policy import fs_read_ok  # noqa: PLC0415

        ok, why = fs_read_ok(p)
        return "" if ok else why
    except Exception:  # noqa: BLE001
        return ""


def snapshot(home: "str | Path", workbook: str) -> "tuple[Path | None, dict]":
    """A copy of the open workbook as it is now, for reading. BLOCKING.
    Returns (path, the child's answer); the caller removes the file."""
    sweep(home)
    folder = live_dir(home, "snap") / new_id("snap")
    folder.mkdir(parents=True, exist_ok=True)
    out = _ask_worker({"op": "snapshot", "workbook": workbook, "dir": str(folder)}, SNAPSHOT_S)
    path = Path(str(out.get("path") or folder / "missing"))
    if not out.get("ok") or not path.is_file() or path.parent != folder:
        shutil.rmtree(folder, ignore_errors=True)
        return None, out if not out.get("ok") else {"ok": False, "error": NO_ANSWER}
    bad = _read_refusal(str(out.get("source") or ""))
    if bad:
        _unlink(path)
        return None, {"ok": False, "code": "not_readable", "error": bad}
    return path, out


def _unlink(path: "Path | None") -> None:
    """Remove a reading copy and the folder made for it."""
    if path is not None:
        shutil.rmtree(path.parent, ignore_errors=True)


def is_live_copy(path: "str | Path") -> bool:
    """Whether *path* is a reading copy this module made (its source was
    already checked against the folders Jarvis may read)."""
    p = Path(path)
    return p.parent.parent.name == "snap" and p.parent.parent.parent.name == DIRNAME


async def live_path(raw: Any, ctx: ToolContext) -> "tuple[str, str]":
    """``live:Budget.xlsx`` (or ``live:`` for the workbook in front) as the
    path of a fresh copy a FILE tool can read. Returns (path, "") or
    ("", why not). Any other path comes back unchanged. The copy is swept
    after ten minutes."""
    text = str(raw or "")
    if not text.lower().startswith(LIVE_PREFIX):
        return text, ""
    path, out = await asyncio.to_thread(snapshot, ctx.config.home, text[len(LIVE_PREFIX):].strip())
    if path is None:
        return "", str(out.get("error") or NO_ANSWER)
    return str(path), ""


# ------------------------------------------------------------------ rendering --


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return f"{v:.15g}"
    return str(v).replace("\t", " ").replace("\n", " ")[:120]


def _col_letter(n: int) -> str:
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _split_a1(cell: str) -> "tuple[int, int]":
    m = re.match(r"^([A-Z]+)(\d+)$", cell)
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


def group_formulas(formulas: "dict[str, str]", limit: int = 80) -> "list[str]":
    """Formula lines, with a formula filled down a column shown once
    (``E2:E6: =C2*D2 (the same down the column)``)."""
    from .excel_map import formula_pattern  # noqa: PLC0415

    cells = sorted((_split_a1(c)[1], _split_a1(c)[0], c, f) for c, f in formulas.items())
    lines: list[str] = []
    i = 0
    while i < len(cells):
        col, row, cell, f = cells[i]
        pat = formula_pattern(f, row, col)
        j = i
        while j + 1 < len(cells) and cells[j + 1][0] == col and cells[j + 1][1] == cells[j][1] + 1 and \
                formula_pattern(cells[j + 1][3], cells[j + 1][1], col) == pat:
            j += 1
        if j > i:
            lines.append(f"{cell}:{cells[j][2]}: {f} (the same down the column)")
        else:
            lines.append(f"{cell}: {f}")
        i = j + 1
    if len(lines) > limit:
        lines = lines[:limit] + [f"(+{len(lines) - limit} more formula line(s))"]
    return lines


def render_look(out: dict) -> str:
    if not out.get("excel_open"):
        return "Excel is not open on this PC (or no workbook is open in it)."
    lines: list[str] = []
    for b in out.get("workbooks") or []:
        tags = []
        if b.get("active"):
            tags.append("in front")
        if not b.get("saved"):
            tags.append("has unsaved changes")
        if b.get("read_only"):
            tags.append("read-only")
        if b.get("autosave"):
            tags.append("AutoSave on")
        lines.append(f"open workbook: {b['name']}" + (f" ({', '.join(tags)})" if tags else "") + f"  [{b.get('path', '')}]")
        for s in b.get("sheets") or []:
            extra = []
            for key, word in (("tables", "tables"), ("pivots", "pivot tables"), ("charts", "charts")):
                if s.get(key):
                    extra.append(f"{word}: {', '.join(s[key])}")
            if s.get("protected"):
                extra.append("protected")
            if not s.get("visible", True):
                extra.append("hidden")
            used = s.get("used") or "empty"
            lines.append(f"  sheet {s['name']}: {used}" + (f" ({'; '.join(extra)})" if extra else ""))
    for name in out.get("protected_view") or []:
        lines.append(f"open in Protected View (cannot be read or changed until the user presses Enable Editing): {name}")
    act = out.get("active") or {}
    if act:
        sel = f"{act.get('sheet', '')}!{act.get('selection', '')}" if act.get("selection") else "nothing a cell range"
        lines.append(f"selected now: {sel} in {act.get('workbook', '')} (active cell {act.get('cell', '?')}); calculation: {act.get('calculation', 'automatic')}")
    rng = out.get("range")
    if rng:
        r1, c1 = _split_a1(rng["address"].split(":")[0])
        rows = rng.get("rows") or []
        width = max((len(r) for r in rows), default=0)
        lines.append("")
        lines.append(f"{rng['sheet']}!{rng['address']} of {rng['workbook']} (the values Excel holds now; a cell's format may show them rounded):")
        lines.append("\t" + "\t".join(_col_letter(c1 + j) for j in range(width)))
        for i, row in enumerate(rows):
            lines.append(f"{r1 + i}\t" + "\t".join(_cell_text(v) for v in row))
        if rng.get("truncated"):
            lines.append(f"(cut: {len(rows)} of {rng.get('total_rows')} rows shown; ask for a smaller range to see the rest)")
        fl = group_formulas(rng.get("formulas") or {})
        if fl:
            lines.append("formulas:")
            lines.extend("  " + x for x in fl)
        lines.append(link_hint(str(rng.get("workbook") or ""), str(rng.get("sheet") or ""), str(rng["address"]).split(":")[0]))
    return "\n".join(lines)


def _val(v: Any) -> str:
    return "(empty)" if v in (None, "") else _cell_text(v)


def render_changes(out: dict, limit: int = 40) -> "list[str]":
    lines: list[str] = []
    for c in (out.get("changes") or [])[:limit]:
        tail = f"  [{c['after_formula']}]" if c.get("after_formula") else ""
        was = f" (was {c['before_formula']})" if c.get("before_formula") else ""
        lines.append(f"  {c['sheet']}!{c['cell']}: {_val(c.get('before'))} -> {_val(c.get('after'))}{tail}{was}")
    total = int(out.get("changed_cells") or 0)
    if total > limit:
        lines.append(f"  (+{total - limit} more changed cell(s))")
    return lines


def change_summary(out: dict) -> str:
    """One short phrase for the chat's change card."""
    n = int(out.get("changed_cells") or 0)
    steps = out.get("steps") or []
    sheets: list[str] = []
    for c in out.get("changes") or []:
        if c["sheet"] not in sheets:
            sheets.append(c["sheet"])
    other = [s for s in steps if s.get("op") not in ("set", "format", "clear")]
    parts = []
    if n:
        parts.append(f"{n} cell{'s' if n != 1 else ''}" + (f" on {', '.join(sheets[:3])}" if sheets else ""))
    if other:
        parts.append("; ".join(str(s.get("summary") or s.get("op")) for s in other[:2]) + (f" (+{len(other) - 2} more)" if len(other) > 2 else ""))
    if not parts:
        parts.append(f"{len(steps)} step{'s' if len(steps) != 1 else ''}")
    return "; ".join(parts)[:200]


# ---------------------------------------------------------------------- tools --


class ExcelLiveLookTool(Tool):
    name = "excel_live_look"
    description = (
        "Look at Excel as it is open on the user's PC RIGHT NOW (unsaved changes included): which workbooks "
        "are open, their sheets, what is selected, and the cells of a range with their formulas. Use this, "
        "not excel_read, whenever the user talks about a workbook they have open, 'this sheet', 'the "
        "selected cells' or 'the cell I am on'. With no 'range' it lists what is open and selected. "
        "range: A1:F40, 'selection' or 'used'. map: true returns the whole workbook's map instead (what "
        "each sheet is for, its formulas filled down, totals, and mistakes worth a look). The file tools "
        "(excel_query, excel_formula_check, excel_read…) also read an open workbook when their path is "
        "'live:<workbook name>'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "workbook": {"type": "string", "description": "File name or full path of an open workbook (default: the one in front)"},
            "sheet": {"type": "string", "description": "Sheet name (default: the active sheet)"},
            "range": {"type": "string", "description": "A1:F40, 'selection' or 'used'"},
            "map": {"type": "boolean", "description": "Return the workbook map instead of cells"},
            "max_cells": {"type": "integer", "description": "Most cells to return (default 2000, at most 20000)"},
            "show": {"type": "boolean", "description": "Also move Excel to this range so the user sees it selected. Only when the user asks to be shown or taken to it."},
        },
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if args.get("map"):
            return await self._map(args, ctx)
        req = {"op": "look", **{k: args[k] for k in ("workbook", "sheet", "range", "max_cells") if args.get(k) not in (None, "")}}
        out = await ask(req, LOOK_S)
        if not out.get("ok"):
            return ToolResult(ok=False, error=str(out.get("error") or NO_ANSWER))
        rng = out.get("range")
        if rng:
            bad = await asyncio.to_thread(_read_refusal, str(rng.get("path") or ""))
            if bad:
                return ToolResult(ok=False, error=bad)
        text = render_look(out)
        data = {k: out[k] for k in ("excel_open", "workbooks", "active", "range", "protected_view") if k in out}
        if args.get("show") and rng:
            # v1.467.0: the user asked to be taken there. Selecting a range
            # changes nothing in the workbook; a refusal is said, not hidden.
            moved = await goto(str(rng.get("workbook") or ""), str(rng.get("sheet") or ""), str(rng.get("address") or "A1"))
            if moved.get("ok"):
                text += f"\nExcel now shows {moved.get('sheet')}!{moved.get('address')} selected."
            else:
                text += f"\nExcel was NOT moved to that range: {moved.get('error') or NO_ANSWER}"
            data["shown"] = bool(moved.get("ok"))
        return ToolResult(ok=True, output=text, data=data)

    async def _map(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        def work() -> ToolResult:
            path, out = snapshot(ctx.config.home, str(args.get("workbook") or ""))
            if path is None:
                return ToolResult(ok=False, error=str(out.get("error") or NO_ANSWER))
            try:
                from .excel_map import render, workbook_map  # noqa: PLC0415

                name = str(out.get("workbook") or "workbook")
                try:
                    data = workbook_map(path)
                except ValueError as exc:
                    return ToolResult(ok=False, error=f"{name}: {exc}. Ask for a range with excel_live_look instead.")
                saved = "" if out.get("saved") else " It has changes that are not saved yet; this is the workbook as it is on screen."
                return ToolResult(ok=True, output=f"{name} as it is open in Excel now.{saved}\n" + render(name, data), data={"workbook": name, "map": data})
            finally:
                _unlink(path)

        return await asyncio.to_thread(work)


_EDIT_HELP = (
    "Change a workbook that is OPEN in Excel on the user's PC. Excel recalculates and the answer lists each "
    "changed cell, before and after, plus any formula that now shows an error. The steps in 'ops' are made "
    "in order and ALL OR NOTHING: if one fails, the ones before it are put back. The user can undo the whole "
    "call from the chat. Nothing is saved: the user saves. Look first (excel_live_look) so you know the "
    "layout. Every step that works on a sheet must say which: give 'sheet' on the step, write the range as "
    "Sheet!A1:D9, or give one 'sheet' for the whole call. Steps (each {op, ...}):\n"
    "set {cell, value | formula | values:[[row],[row]], number_format?, bold?, italic?, fill?, font_color?, "
    "align?, wrap?, border?, font_size?} - cell may be a range; one formula over a range fills down/across "
    "with references adjusted; with only formats it formats the range. Colours are hex like FFF2CC.\n"
    "clear {range}; add_sheet {name}; rename_sheet {sheet, to}; insert_rows {at: 5, count}; "
    "insert_columns {at: 'C', count}; sort {range, by: [{column: heading or letter, order: asc|desc}], "
    "header?}; filter {range, column, criteria: '>100' | values: [...]}; clear_filter; autofit {columns?}; "
    "column_width {columns: 'A:C', width}; freeze {cell: 'B2' | 'none'}.\n"
    "conditional_format {range, rule: greater|less|between|equal|not_equal|greater_equal|less_equal|"
    "text_contains|formula|duplicates|top|bottom|data_bar|color_scale, value?, value2?, formula?, fill?, "
    "font_color?, bold?}; validation {range, list: [...] | source: 'Sheet!A1:A9' | type: whole|decimal|date "
    "with min/max, message?}.\n"
    "chart {data: 'A1:C13', type: column|bar|line|pie|scatter|area|stacked_column|stacked_bar|line_markers|"
    "doughnut, title?, at?, name?}; chart_edit {name?, type?, title?, x_title?, y_title?, legend?}.\n"
    "pivot {source: 'Data!A1:F200', rows: [...], columns?: [...], values: [{field, func: sum|count|average|"
    "max|min}], filters?: [...], dest_sheet? (leave out for a new sheet), dest_cell?, name?}; pivot_edit {sheet, name?, rows?, columns?, "
    "values?, filters?, show?: {field: [items to keep]}}; refresh.\n"
    "name {name, refers_to: 'Data!$B$2:$B$50'}; table {range, name?, style?}; note {cell, text}; "
    "print_setup {orientation?, fit_width?, fit_height?, print_area?, title_rows?, header?, footer?}.\n"
    "delete_rows {at, count}, delete_columns {at, count} and delete_sheet {sheet} CANNOT be undone: the "
    "user is always asked first, and a copy of the workbook as it was is kept."
)


class ExcelLiveEditTool(Tool):
    name = "excel_live_edit"
    description = _EDIT_HELP
    input_schema = {
        "type": "object",
        "properties": {
            "workbook": {"type": "string", "description": "File name or full path of an open workbook (default: the one in front)"},
            "sheet": {"type": "string", "description": "The sheet for every step that does not name its own"},
            "ops": {
                "type": "array",
                "description": "The steps, in order (at most 60)",
                "items": {
                    "type": "object",
                    "properties": {"op": {"type": "string", "enum": list(EDIT_OPS)}, "sheet": {"type": "string"}},
                    "required": ["op"],
                },
            },
        },
        "required": ["ops"],
    }
    reversibility = Reversibility.REVERSIBLE
    returns_untrusted_content = True  # its answer quotes what the cells held

    def __init__(self) -> None:
        # capture_undo -> execute hand-off: the registry calls both with the
        # SAME args object (the pdf_tools pattern).
        self._pending: dict[int, dict[str, str]] = {}
        self._plans: dict[str, tuple[float, dict]] = {}

    async def _plan(self, args: dict[str, Any]) -> dict:
        key = json.dumps([args.get("workbook"), args.get("sheet"), args.get("ops")], sort_keys=True, default=str)
        held = self._plans.get(key)
        if held and time.monotonic() - held[0] < 20:
            return held[1]
        out = await ask({"op": "plan", "workbook": args.get("workbook") or "", "sheet": args.get("sheet") or "",
                         "ops": args.get("ops")}, PLAN_S)
        if len(self._plans) > 32:
            self._plans.clear()
        self._plans[key] = (time.monotonic(), out)
        return out

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> "SpendCheck | None":
        """Two cards that are shown even when edits are already allowed: a
        step that cannot be put back (Allow once only, every time), and a
        change that replaces data (more than a few cells, or a formula with a
        plain value). Everything else follows the ordinary approval."""
        if not isinstance(args.get("ops"), list) or not args["ops"]:
            return None
        out = await self._plan(args)
        if not out.get("ok"):
            return None  # execute says why, in the same words
        name = str(out.get("workbook") or "the workbook")
        gone = [str(x) for x in out.get("destructive") or []]
        if gone:
            return SpendCheck(
                ask=True, once_only=True,
                words=(f"In {name}, open in Excel: {'; '.join(gone)[:300]}. This cannot be undone here; "
                       "a copy of the workbook as it is now is kept first."),
            )
        over, lost = int(out.get("overwrites") or 0), int(out.get("formulas_lost") or 0)
        if lost or over > OVERWRITE_CARD_CELLS:
            what = f"replaces {over} cell{'s' if over != 1 else ''} that hold{'s' if over == 1 else ''} data"
            if lost:
                what += f", {lost} of them formula{'s' if lost != 1 else ''} replaced by plain values"
            return SpendCheck(ask=True, words=f"In {name}, open in Excel: this {what}. It can be undone from the chat.", dont_ask_skips=True)
        return None

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        change_id = new_id("xl")
        path = await asyncio.to_thread(lambda: live_dir(ctx.config.home, "undo") / f"{change_id}.jsonl")
        self._pending[id(args)] = {"change_id": change_id, "undo_path": str(path)}
        if len(self._pending) > 64:
            self._pending.pop(next(iter(self._pending)))
        return {"kind": UNDO_KIND, "pre_ref": str(path), "pre_inline": json.dumps({"change_id": change_id})}

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        words, ok = await restore(undo)
        return ToolResult(ok=ok, output=words if ok else "", error=None if ok else words)

    @staticmethod
    def _unchanged(ctx: ToolContext, why: str) -> ToolResult:
        """A refusal that left the workbook exactly as it was, said FIRST: in
        a live trial a model read "Excel refused it: ..." and told the user
        the rows had been deleted."""
        note_turn(ctx, False)
        return ToolResult(ok=False, error=f"{UNCHANGED_LEAD} {why}")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        held = self._pending.pop(id(args), None)
        if held is None:
            cid = new_id("xl")
            held = {"change_id": cid, "undo_path": str(await asyncio.to_thread(lambda: live_dir(ctx.config.home, "undo") / f"{cid}.jsonl"))}
        ops = args.get("ops")
        if not isinstance(ops, list) or not ops:
            return ToolResult(ok=False, error="give the steps to make in 'ops'")
        await asyncio.to_thread(sweep, ctx.config.home)
        plan = await self._plan(args)
        if not plan.get("ok"):
            return self._unchanged(ctx, str(plan.get("error") or NO_ANSWER))
        bad = await asyncio.to_thread(_read_refusal, str(plan.get("path") or ""))
        if bad:
            return self._unchanged(ctx, bad)
        name = str(plan.get("workbook") or "workbook")
        copy = ""
        if plan.get("destructive"):
            stamp = time.strftime("%Y-%m-%d %H%M%S")
            copy = str(await asyncio.to_thread(lambda: live_dir(ctx.config.home, "copies") / f"{stamp} {_safe_name(name)}"))
            if not copy.lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb")):
                copy += ".xlsx"
        self._plans.clear()
        out = await ask({"op": "apply", "workbook": plan.get("path") or args.get("workbook") or "", "ops": ops,
                         "sheet": args.get("sheet") or "", "undo_path": held["undo_path"], "copy_path": copy}, APPLY_S)
        if out.get("timed_out"):
            # The child was stopped part-way: put back what it had done.
            words, _ok = await restore({"pre_ref": held["undo_path"]}, forgiving=True)
            note_turn(ctx, False)
            return ToolResult(ok=False, error=f"{NO_ANSWER} The change was stopped part-way. {words}")
        if not out.get("ok"):
            if out.get("partly"):
                note_turn(ctx, True)  # something DID change and could not all be put back
                return ToolResult(ok=False, error=str(out.get("error") or NO_ANSWER))
            return self._unchanged(ctx, str(out.get("error") or NO_ANSWER))
        note_turn(ctx, True)
        lines = [f"Changed {name}, open in Excel ({change_summary(out)}). It is NOT saved: the user saves it in Excel."]
        if out.get("autosave"):
            lines[0] = f"Changed {name}, open in Excel ({change_summary(out)}). AutoSave is on, so Excel has already saved this to its cloud copy."
        for n, s in enumerate(out.get("steps") or [], start=1):
            lines.append(f"{n}. {s.get('summary') or s.get('op')}")
        changed = render_changes(out)
        if changed:
            lines.append("changed cells (before -> after):")
            lines.extend(changed)
        if out.get("new_errors"):
            lines.append("WARNING: these formulas show an error now and did not before: " + ", ".join(out["new_errors"])
                         + ". Look at them (excel_live_look) and fix or undo the change.")
        if str(out.get("calculation")) == "manual":
            lines.append("NOTE: this workbook's calculation is set to manual, so the values above may be stale until the user presses F9.")
        if out.get("destructive"):
            lines.append("THE DELETION CANNOT BE UNDONE, from the chat or with Ctrl+Z. Do not tell the user it can."
                         + (f" The workbook as it was before is kept as a copy at {out['copy']}." if out.get("copy") else ""))
        else:
            lines.append("The user can undo this whole call from the chat (Undo on the change line). " + CLEARS_CTRL_Z)
        first = next(iter(out.get("changes") or []), None)
        if first:
            lines.append(link_hint(name, str(first.get("sheet") or ""), str(first.get("cell") or "")))
        card = {"title": "Excel changed", "key": f"excel:{name}", "label": name, "old": None,
                "new": change_summary(out), "change_id": held["change_id"]}
        data = {
            "workbook": name, "path": out.get("path"), "changed_cells": out.get("changed_cells"),
            "changes": (out.get("changes") or [])[:60], "new_errors": out.get("new_errors") or [],
            "steps": [str(s.get("summary") or s.get("op")) for s in out.get("steps") or []],
            "copy": out.get("copy") or "", "excel_change": card,
        }
        return ToolResult(ok=True, output="\n".join(lines), data=data)


UNCHANGED_LEAD = "NOTHING WAS CHANGED in the workbook."
#: What the reply is given when every change this turn tried in Excel failed.
UNCHANGED_NOTE = "\n\n_Note: nothing was changed in the open workbook this turn._"
_TURNS: "dict[str, bool]" = {}


def note_turn(ctx: Any, changed: bool) -> None:
    """Remember, per chat turn, whether a change reached Excel (bounded)."""
    turn = str(getattr(ctx, "turn_id", "") or "")
    if not turn:
        return
    _TURNS[turn] = bool(_TURNS.get(turn)) or changed
    while len(_TURNS) > 256:
        _TURNS.pop(next(iter(_TURNS)))


def unchanged_note(turn_id: Any, tools_used: Any) -> str:
    """The note for a reply whose turn TRIED to change the open workbook and
    never did (refused by Excel, by the user, or by a check). Decided from
    what ran, never from the reply's words. MIRROR NOTE (lock-step): both
    chat lanes append this beside the other honesty notes. Never raises."""
    try:
        turn = str(turn_id or "")
        if not turn or turn not in _TURNS or _TURNS[turn] or "excel_live_edit" in (tools_used or []):
            return ""
        return UNCHANGED_NOTE
    except Exception:  # noqa: BLE001
        return ""


async def restore(undo: dict[str, Any], *, forgiving: bool = False) -> "tuple[str, bool]":
    """Put one live-Excel change back. Returns (words, whether anything was
    put back or there was nothing left to do). Never raises."""
    path = str(undo.get("pre_ref") or "")
    if not path or not await asyncio.to_thread(lambda: Path(path).is_file()):
        return "The record of that change is no longer kept, so it cannot be undone.", False
    out = await ask({"op": "restore", "undo_path": path, "forgiving": forgiving}, RESTORE_S)
    if not out.get("ok"):
        return str(out.get("error") or NO_ANSWER), False
    left = [str(x) for x in out.get("left") or []]
    n = int(out.get("restored") or 0)
    name = str(out.get("workbook") or "the workbook")
    if n == 0 and left:
        return f"Nothing in {name} was put back: " + "; ".join(left)[:500], False
    words = f"{name} is back to how it was before that change."
    if left:
        words = f"Put back most of that change in {name}. Left as it is: " + "; ".join(left)[:500]
    return words, True


async def goto(workbook: str, sheet: str, cell: str) -> dict:
    return await ask({"op": "goto", "workbook": workbook, "sheet": sheet, "cell": cell}, LOOK_S)


async def now() -> dict:
    """What is in front in Excel: ``{open, workbook, sheet, selection, cell,
    unsaved}``, names only. Never raises; Excel not running (or no workbook
    open, or Excel busy) is ``{"open": False}``, with ``busy`` words when
    Excel is there but taking no requests."""
    if not await asyncio.to_thread(excel_running):
        return {"open": False}
    out = await ask({"op": "look"}, LOOK_S)
    if not out.get("ok"):
        return {"open": False, "busy": str(out.get("error") or NO_ANSWER)[:300]}
    act = out.get("active") or {}
    if not out.get("excel_open") or not act.get("workbook"):
        return {"open": False}
    book = next((b for b in out.get("workbooks") or [] if b.get("name") == act.get("workbook")), {})
    return {
        "open": True,
        "workbook": str(act.get("workbook") or "")[:260],
        "sheet": str(act.get("sheet") or "")[:31],
        "selection": str(act.get("selection") or "")[:60],
        "cell": str(act.get("cell") or "")[:20],
        "unsaved": not book.get("saved", True),
        "others": max(0, len(out.get("workbooks") or []) - 1),
    }


#: A CELL THE USER CAN JUMP TO (v1.467.0). A reply names a cell as a markdown
#: link whose address is only a fragment, "#excel/<workbook>/<sheet>/<cell>"
#: (each part percent-encoded). The chat draws it as a press that asks
#: POST /excel/live/goto, which selects the cell in Excel and brings Excel
#: forward. It reads and changes nothing. dashboard/lib/excelCellLink.ts is
#: the twin of CELL_LINK_PREFIX and CELL_RX (pinned equal).
CELL_LINK_PREFIX = "#excel/"
CELL_RX = re.compile(r"^\$?[A-Za-z]{1,3}\$?[0-9]{1,7}(?::\$?[A-Za-z]{1,3}\$?[0-9]{1,7})?$")
_SHEET_FORBIDDEN = set("/\\?*[]:")


def cell_link(workbook: str, sheet: str, cell: str) -> str:
    from urllib.parse import quote  # noqa: PLC0415

    return CELL_LINK_PREFIX + "/".join(quote(str(x), safe="") for x in (workbook, sheet, cell))


def link_hint(workbook: str, sheet: str = "", cell: str = "") -> str:
    """One line for a tool's answer: how to write a cell so the user can
    jump to it."""
    sheet, cell = sheet or "Sheet1", (cell or "A1").replace("$", "")
    return (
        f"To let the user jump to a cell, write it as a link like [{sheet}!{cell}]({cell_link(workbook, sheet, cell)}) "
        "(a space in a name is %20): clicking it shows that cell in Excel."
    )


def clean_cell_target(workbook: Any, sheet: Any, cell: Any) -> "tuple[str, str, str] | None":
    """The three parts of a jump as the child may be asked for them, or None
    when they are not a workbook name, a sheet name and a cell."""
    wb, sh, ce = (str(x or "").strip() for x in (workbook, sheet, cell))
    if len(wb) > 260 or len(sh) > 31 or not CELL_RX.match(ce):
        return None
    if any(ord(ch) < 32 for ch in wb + sh) or _SHEET_FORBIDDEN.intersection(sh):
        return None
    return wb, sh, ce.replace("$", "").upper()


def excel_live_tools() -> "list[Tool]":
    return [ExcelLiveLookTool(), ExcelLiveEditTool()]
