"""Matching two lists of transactions (v1.469.0): a bank statement against
the books, a sub-ledger against its control account, one export against
another.

A model matching 400 rows by reading them gets some wrong and cannot say
which. This does the matching in code: every amount is compared in whole
cents, every row is matched at most once, and the answer says exactly what
matched, what is left on each side, and proves that the leftovers explain the
whole difference between the two lists.

HOW ROWS ARE MATCHED (in this order; a row matched in one pass is out of the
next):

1. **Same amount and same reference** (a cheque number, an invoice number),
   when both lists have a reference column. References are compared without
   case, spaces or punctuation.
2. **Same amount, dates within the window** (``date_window_days``, default
   5). Inside one amount the two lists are walked in date order, which
   matches as many rows as can be matched.
3. **Same amount, no dates to go by**: only when a list has no date column at
   all. In row order.
4. **Within the tolerance** (``tolerance``, default 0): amounts that differ
   by no more than that, dates within the window. Each is listed with its
   difference.

Rows with the same amount on dates OUTSIDE the window are never matched; they
are listed as near misses with the gap in days. One row that equals the SUM
of several unmatched rows of one day on the other side (a deposit of several
cheques) is listed as a possible group, never counted as matched: that is a
judgement for a person.

Pure functions over rows; the tool at the bottom reads the two tables (a file
or, through a ``live:`` path, the workbook open in Excel) off the loop.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult

MAX_ROWS = 50_000
SHOWN = 40
MAX_GROUPS = 20
MAX_NEAR = 20


@dataclass
class Txn:
    row: int  # the sheet's own row number
    cents: int
    date: "dt.date | None" = None
    ref: str = ""
    text: str = ""


@dataclass
class Side:
    name: str
    sheet: str
    rows: "list[Txn]" = field(default_factory=list)
    no_amount: int = 0
    truncated: bool = False
    has_date: bool = False
    has_ref: bool = False
    not_worked_out: int = 0


def to_cents(v: Any) -> "int | None":
    """A cell as whole cents, or None when it holds no amount. Rounded the
    way Excel shows it (15 significant digits, half away from zero); text
    like "1,234.50", "(45.00)" and "$12" is read as the number it spells."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        text = f"{v:.15g}"
    else:
        text = str(v).strip().replace(",", "").replace("$", "").replace(" ", "")
        if not text:
            return None
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        if text.endswith("-"):
            text = "-" + text[:-1]
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return int(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(int(cents)), 100)
    return f"{sign}{whole:,}.{part:02d}"


_REF_STRIP = re.compile(r"[^0-9a-z]+")


def norm_ref(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    text = _REF_STRIP.sub("", str(v).strip().lower())
    return text.lstrip("0") or ("0" if text else "")


def as_date(v: Any) -> "dt.date | None":
    from .excel_tools import _as_date

    return _as_date(v)


# ------------------------------------------------------------------ matching --


@dataclass
class Match:
    a: Txn
    b: Txn
    how: str  # reference | date | amount | tolerance
    diff_cents: int = 0
    days: "int | None" = None


@dataclass
class Result:
    matches: "list[Match]" = field(default_factory=list)
    only_a: "list[Txn]" = field(default_factory=list)
    only_b: "list[Txn]" = field(default_factory=list)
    near: "list[tuple[Txn, Txn, int]]" = field(default_factory=list)  # same amount, days apart
    groups: "list[tuple[str, Txn, list[Txn]]]" = field(default_factory=list)  # side of the single row
    ambiguous: int = 0
    opposite_signs: bool = False


def _gap(a: Txn, b: Txn) -> "int | None":
    if a.date is None or b.date is None:
        return None
    return abs((a.date - b.date).days)


def _by_date(rows: "list[Txn]") -> "list[Txn]":
    return sorted(rows, key=lambda t: (t.date or dt.date.min, t.row))


def _walk(a_rows: "list[Txn]", b_rows: "list[Txn]", days: int) -> "list[tuple[Txn, Txn]]":
    """Rows of ONE amount, both dated: the most pairs whose dates are within
    *days*. Both lists in date order, one pass (the standard greedy, which is
    optimal for points on a line)."""
    out: list[tuple[Txn, Txn]] = []
    i = j = 0
    while i < len(a_rows) and j < len(b_rows):
        a, b = a_rows[i], b_rows[j]
        delta = (a.date - b.date).days  # type: ignore[operator]
        if abs(delta) <= days:
            out.append((a, b))
            i += 1
            j += 1
        elif delta < 0:
            i += 1
        else:
            j += 1
    return out


def match(a: Side, b: Side, *, days: int = 5, tolerance_cents: int = 0) -> Result:
    res = Result()
    taken_a: set[int] = set()
    taken_b: set[int] = set()

    def take(x: Txn, y: Txn, how: str) -> None:
        taken_a.add(x.row)
        taken_b.add(y.row)
        res.matches.append(Match(x, y, how, x.cents - y.cents, _gap(x, y)))

    def left(rows: "list[Txn]", taken: "set[int]") -> "list[Txn]":
        return [t for t in rows if t.row not in taken]

    def buckets(rows: "list[Txn]") -> "dict[int, list[Txn]]":
        out: dict[int, list[Txn]] = {}
        for t in rows:
            out.setdefault(t.cents, []).append(t)
        return out

    # 1. same amount, same reference
    if a.has_ref and b.has_ref:
        by: dict[tuple[int, str], list[Txn]] = {}
        for t in b.rows:
            if t.ref:
                by.setdefault((t.cents, t.ref), []).append(t)
        for x in a.rows:
            if not x.ref:
                continue
            cands = [y for y in by.get((x.cents, x.ref), []) if y.row not in taken_b]
            if not cands:
                continue
            cands.sort(key=lambda y: (_gap(x, y) if _gap(x, y) is not None else 10 ** 9, y.row))
            take(x, cands[0], "reference")

    # 2. same amount, dates within the window  /  3. same amount, no dates
    dated = a.has_date and b.has_date
    ba, bb = buckets(left(a.rows, taken_a)), buckets(left(b.rows, taken_b))
    for cents, xs in ba.items():
        ys = bb.get(cents)
        if not ys:
            continue
        if dated:
            xd, yd = _by_date([t for t in xs if t.date]), _by_date([t for t in ys if t.date])
            pairs = _walk(xd, yd, days)
            if len(xd) > 1 and len(yd) > 1 and pairs:
                res.ambiguous += len(pairs)
            for x, y in pairs:
                take(x, y, "date")
        else:
            n = min(len(xs), len(ys))
            if len(xs) > 1 and len(ys) > 1:
                res.ambiguous += n
            for x, y in zip(xs[:n], ys[:n]):
                take(x, y, "amount")

    # 4. within the tolerance
    if tolerance_cents > 0:
        rest_b = sorted(left(b.rows, taken_b), key=lambda t: (t.cents, t.row))
        for x in left(a.rows, taken_a):
            best: "tuple[int, int, int, Txn] | None" = None
            for y in rest_b:
                if y.row in taken_b:
                    continue
                d = abs(x.cents - y.cents)
                if d > tolerance_cents or d == 0:
                    continue
                gap = _gap(x, y)
                if dated and (gap is None or gap > days):
                    continue
                key = (d, gap if gap is not None else 0, y.row, y)
                if best is None or key[:3] < best[:3]:
                    best = key
            if best is not None:
                take(x, best[3], "tolerance")

    res.only_a, res.only_b = left(a.rows, taken_a), left(b.rows, taken_b)

    # near misses: the same amount, dates outside the window
    if dated:
        rest = buckets(res.only_b)
        for x in res.only_a:
            for y in rest.get(x.cents, []):
                gap = _gap(x, y)
                if gap is not None and len(res.near) < MAX_NEAR:
                    res.near.append((x, y, gap))
                    break

    # possible groups: one row = the sum of several unmatched rows of one day
    def groups(single: "list[Txn]", many: "list[Txn]", side: str) -> None:
        by_day: dict[dt.date, list[Txn]] = {}
        for t in many:
            if t.date:
                by_day.setdefault(t.date, []).append(t)
        sums: dict[int, list[tuple[dt.date, list[Txn]]]] = {}
        for day, rows in by_day.items():
            if len(rows) >= 2:
                sums.setdefault(sum(t.cents for t in rows), []).append((day, rows))
        for x in single:
            for day, rows in sums.get(x.cents, []):
                if x.date is None or abs((x.date - day).days) <= days:
                    if len(res.groups) < MAX_GROUPS:
                        res.groups.append((side, x, rows))
                    break

    if dated:
        groups(res.only_a, res.only_b, "a")
        groups(res.only_b, res.only_a, "b")

    # the two lists may simply use opposite signs
    small = min(len(a.rows), len(b.rows))
    if small >= 4 and len(res.matches) * 5 < small:
        flipped = buckets([Txn(t.row, -t.cents) for t in b.rows])
        would = sum(min(len(xs), len(flipped.get(c, []))) for c, xs in buckets(a.rows).items())
        if would >= 3 * max(1, len(res.matches)) and would * 2 >= small:
            res.opposite_signs = True
    return res


# ------------------------------------------------------------------- reading --


def _spec_columns(spec: dict) -> "list[str]":
    return [k for k in ("amount", "money_in", "money_out", "date", "ref", "description") if spec.get(k)]


def read_side(path: Path, spec: dict, name: str) -> Side:
    """One list as transactions. BLOCKING (it loads the workbook)."""
    from .excel_tools import _SCAN_ROW_CAP, _col_index, _load_table_computed

    if not spec.get("amount") and not (spec.get("money_in") or spec.get("money_out")):
        raise ValueError(f"{name}: say which column holds the amount ('amount'), or the two columns 'money_in' and 'money_out'")
    title, headers, rows, truncated, recalc = _load_table_computed(path, spec.get("sheet") or None)
    first = 2
    header_row = int(spec.get("header_row") or 1)
    if header_row > 1:
        matrix = [headers] + rows
        if header_row > len(matrix):
            raise ValueError(f"{name}: the sheet {title} has no row {header_row} to read headings from")
        headers = ["" if v is None else str(v).strip() for v in matrix[header_row - 1]]
        rows = matrix[header_row:]
        first = header_row + 1

    def col(key: str) -> "int | None":
        return _col_index(str(spec[key]), headers) if spec.get(key) else None

    try:
        ia, iin, iout = col("amount"), col("money_in"), col("money_out")
        idate, iref, itext = col("date"), col("ref"), col("description")
    except ValueError as exc:
        raise ValueError(f"{name}, sheet {title}: {exc}") from None
    flip = -1 if spec.get("flip") else 1
    side = Side(name=name, sheet=title, truncated=truncated or len(rows) >= _SCAN_ROW_CAP,
                has_date=idate is not None, has_ref=iref is not None,
                not_worked_out=(len(recalc.failed) + recalc.skipped) if recalc is not None else 0)

    def cell(row: list, i: "int | None") -> Any:
        return row[i] if i is not None and i < len(row) else None

    for n, row in enumerate(rows):
        if all(v in (None, "") for v in row):
            continue
        if ia is not None:
            cents = to_cents(cell(row, ia))
        else:
            cin, cout = to_cents(cell(row, iin)), to_cents(cell(row, iout))
            cents = None if cin is None and cout is None else (cin or 0) - (cout or 0)
        if cents is None:
            side.no_amount += 1
            continue
        text = cell(row, itext)
        side.rows.append(Txn(row=first + n, cents=flip * cents, date=as_date(cell(row, idate)),
                             ref=norm_ref(cell(row, iref)), text="" if text is None else str(text).strip()[:80]))
    return side


# ----------------------------------------------------------------- reporting --


def _line(t: Txn) -> str:
    bits = [f"row {t.row}", money(t.cents)]
    if t.date:
        bits.append(t.date.isoformat())
    if t.ref:
        bits.append(f"ref {t.ref}")
    if t.text:
        bits.append(t.text)
    return "  " + " | ".join(bits)


def totals(a: Side, b: Side, res: Result) -> dict:
    ta, tb = sum(t.cents for t in a.rows), sum(t.cents for t in b.rows)
    oa, ob = sum(t.cents for t in res.only_a), sum(t.cents for t in res.only_b)
    tol = sum(m.diff_cents for m in res.matches)
    return {
        "a_rows": len(a.rows), "a_total": ta, "b_rows": len(b.rows), "b_total": tb,
        "matched": len(res.matches), "matched_total_a": sum(m.a.cents for m in res.matches),
        "only_a": len(res.only_a), "only_a_total": oa, "only_b": len(res.only_b), "only_b_total": ob,
        "tolerance_total": tol, "difference": ta - tb, "explained": oa - ob + tol,
    }


def render(a: Side, b: Side, res: Result, t: dict, *, days: int, tolerance_cents: int) -> str:
    A, B = a.name, b.name
    how: dict[str, int] = {}
    for m in res.matches:
        how[m.how] = how.get(m.how, 0) + 1
    words = {"reference": "by amount and reference", "date": f"by amount, dates within {days} day(s)",
             "amount": "by amount (no dates to go by)", "tolerance": f"within {money(tolerance_cents)}"}
    lines = [
        f"{A} ({a.sheet}): {t['a_rows']} rows, total {money(t['a_total'])}.   {B} ({b.sheet}): {t['b_rows']} rows, total {money(t['b_total'])}.",
        f"Matched: {t['matched']} pairs" + (" (" + "; ".join(f"{n} {words[k]}" for k, n in how.items()) + ")" if how else "") + ".",
        f"Only in {A}: {t['only_a']} rows, total {money(t['only_a_total'])}.   Only in {B}: {t['only_b']} rows, total {money(t['only_b_total'])}.",
    ]
    diff, expl = t["difference"], t["explained"]
    proof = f"The difference between the two totals is {money(diff)} = only in {A} ({money(t['only_a_total'])}) minus only in {B} ({money(t['only_b_total'])})"
    if t["tolerance_total"]:
        proof += f" plus {money(t['tolerance_total'])} of differences inside matched pairs"
    lines.append(proof + (". It ties." if diff == expl else f". IT DOES NOT TIE: {money(diff - expl)} is unexplained."))
    if res.opposite_signs:
        lines.append(f"NOTE: the two lists look like they use OPPOSITE signs (few amounts match as they are, most would with one side's sign turned). Ask again with flip: true on one side.")
    if res.ambiguous:
        lines.append(f"NOTE: {res.ambiguous} of the matched pairs are among rows that repeat the same amount; the totals are right, which row pairs with which is by date order.")
    for side in (a, b):
        if side.no_amount:
            lines.append(f"NOTE: {side.no_amount} row(s) of {side.name} hold no amount and were left out.")
        if side.truncated:
            lines.append(f"NOTE: only the first {MAX_ROWS:,} rows of {side.name} were read.")
        if side.not_worked_out:
            lines.append(f"NOTE: {side.not_worked_out} formula cell(s) of {side.name} could not be worked out and read as empty.")
    for label, rows in ((f"Only in {A}", res.only_a), (f"Only in {B}", res.only_b)):
        if rows:
            lines.append(f"{label}:")
            lines.extend(_line(x) for x in rows[:SHOWN])
            if len(rows) > SHOWN:
                lines.append(f"  (+{len(rows) - SHOWN} more)")
    tol_rows = [m for m in res.matches if m.how == "tolerance"]
    if tol_rows:
        lines.append("Matched within the tolerance (the amounts differ):")
        lines.extend(f"  {A} row {m.a.row} {money(m.a.cents)} / {B} row {m.b.row} {money(m.b.cents)} (difference {money(m.diff_cents)})" for m in tol_rows[:SHOWN])
    if res.near:
        lines.append(f"Same amount but more than {days} day(s) apart (NOT matched):")
        lines.extend(f"  {money(x.cents)}: {A} row {x.row} {x.date} / {B} row {y.row} {y.date} ({gap} days apart)" for x, y, gap in res.near)
    if res.groups:
        lines.append("Possible groups (one row equals several rows of one day on the other side; NOT counted as matched):")
        for side, x, rows in res.groups:
            one, many = (A, B) if side == "a" else (B, A)
            lines.append(f"  {one} row {x.row} {money(x.cents)} = {many} rows {', '.join(str(r.row) for r in rows)} on {rows[0].date}")
    return "\n".join(lines)


def write_result(path: Path, a: Side, b: Side, res: Result, t: dict) -> None:
    """The whole result as a workbook: Summary, Matched, Only in A, Only in B."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    rows = [
        ("", a.name, b.name),
        ("Sheet", a.sheet, b.sheet),
        ("Rows", t["a_rows"], t["b_rows"]),
        ("Total", t["a_total"] / 100, t["b_total"] / 100),
        ("Matched pairs", t["matched"], t["matched"]),
        ("Left over (rows)", t["only_a"], t["only_b"]),
        ("Left over (total)", t["only_a_total"] / 100, t["only_b_total"] / 100),
        ("Difference between the totals", t["difference"] / 100, ""),
        ("Explained by the left-over rows", t["explained"] / 100, ""),
        ("Ties", "yes" if t["difference"] == t["explained"] else "NO", ""),
    ]
    for r in rows:
        ws.append(list(r))
    for c in ws[1]:
        c.font = Font(bold=True)

    def money_fmt(sheet: Any, cols: "tuple[str, ...]") -> None:
        for letter in cols:
            for c in sheet[letter][1:]:
                c.number_format = "#,##0.00"
        for c in sheet[1]:
            c.font = Font(bold=True)

    money_fmt(ws, ("B", "C"))
    m = wb.create_sheet("Matched")
    m.append([f"{a.name} row", f"{a.name} date", f"{a.name} amount", f"{a.name} ref", f"{a.name} description",
              f"{b.name} row", f"{b.name} date", f"{b.name} amount", f"{b.name} ref", f"{b.name} description",
              "Matched by", "Difference", "Days apart"])
    for x in res.matches:
        m.append([x.a.row, x.a.date, x.a.cents / 100, x.a.ref, x.a.text, x.b.row, x.b.date, x.b.cents / 100, x.b.ref,
                  x.b.text, x.how, x.diff_cents / 100, x.days])
    money_fmt(m, ("C", "H", "L"))
    for title, rows_ in ((f"Only in {a.name}"[:31], res.only_a), (f"Only in {b.name}"[:31], res.only_b)):
        s = wb.create_sheet(title)
        s.append(["Row", "Date", "Amount", "Ref", "Description"])
        for x in rows_:
            s.append([x.row, x.date, x.cents / 100, x.ref, x.text])
        money_fmt(s, ("C",))
    wb.save(str(path))
    wb.close()


# ---------------------------------------------------------------------- tool --

_SIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Workbook path, or live:<name> for one open in Excel"},
        "sheet": {"type": "string"},
        "name": {"type": "string", "description": "What to call this list in the answer, e.g. Bank or Books"},
        "amount": {"type": "string", "description": "Column with the amount (heading or letter)"},
        "money_in": {"type": "string", "description": "Instead of amount: the column of money in (deposits, debits)"},
        "money_out": {"type": "string", "description": "Instead of amount: the column of money out (withdrawals, credits)"},
        "date": {"type": "string", "description": "Date column"},
        "ref": {"type": "string", "description": "Reference column (cheque or invoice number)"},
        "description": {"type": "string", "description": "A text column to show beside each row"},
        "header_row": {"type": "integer", "description": "The row that holds the headings (default 1)"},
        "flip": {"type": "boolean", "description": "Turn this list's signs (when the two lists use opposite signs)"},
    },
    "required": ["path"],
}


class ExcelReconcileTool(Tool):
    name = "excel_reconcile"
    description = (
        "Match two lists of transactions row by row: a bank statement against the books, a sub-ledger "
        "against its control account, two exports. Use this instead of matching rows yourself. Amounts are "
        "compared in whole cents and each row is matched once: first by amount and reference, then by "
        "amount with dates within date_window_days, then (if tolerance is given) amounts that differ by "
        "no more than that. The answer gives the totals, what matched, every row left over on each side, "
        "and proves the left-over rows explain the whole difference. Each list is {path, sheet?, amount "
        "| money_in + money_out, date?, ref?, description?, header_row?, flip?}; columns by heading or "
        "letter. save_as writes the full result (Summary, Matched, Only in each) as a new .xlsx in the "
        "workspace. Nothing in either list is changed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "a": _SIDE_SCHEMA,
            "b": _SIDE_SCHEMA,
            "date_window_days": {"type": "integer", "description": "How many days apart two dates may be (default 5)"},
            "tolerance": {"type": "number", "description": "How far two amounts may differ and still match (default 0)"},
            "save_as": {"type": "string", "description": "Workspace-relative .xlsx to write the full result to (must not exist yet)"},
        },
        "required": ["a", "b"],
    }
    reversibility = Reversibility.REVERSIBLE
    returns_untrusted_content = True

    def _target(self, args: dict, ctx: ToolContext) -> "Path | None":
        from ..tools.base import safe_path

        raw = str(args.get("save_as") or "").strip()
        if not raw:
            return None
        if not raw.lower().endswith(".xlsx"):
            raw += ".xlsx"
        return safe_path(ctx.workspace, raw)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        """The only thing this writes is the new result file: undo deletes it."""
        from ..tools.undo import make_file_descriptor

        def work() -> "dict[str, Any] | None":
            try:
                target = self._target(args, ctx)
                if target is None or target.exists():
                    return None
                rel = str(target.relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
            except Exception:  # noqa: BLE001
                return None
            return make_file_descriptor(ctx.config.home, kind="file_delete", path=rel, mode="raw")

        return await asyncio.to_thread(work)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..tools.undo import revert_workspace_file

        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .excel_tools import _open_error, _read_target

        sa, sb = args.get("a"), args.get("b")
        if not isinstance(sa, dict) or not isinstance(sb, dict):
            return ToolResult(ok=False, error="excel_reconcile needs the two lists as 'a' and 'b', each with a path and its amount column")
        try:
            days = max(0, int(args.get("date_window_days") if args.get("date_window_days") is not None else 5))
            tol = to_cents(args.get("tolerance") or 0) or 0
            target = self._target(args, ctx)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=str(exc))
        if tol < 0:
            return ToolResult(ok=False, error="tolerance cannot be negative")
        paths = []
        for spec in (sa, sb):
            path, ok, reason = await _read_target(str(spec.get("path", "")), ctx)
            if not ok:
                return ToolResult(ok=False, error=reason if str(spec.get("path", "")).lower().startswith("live:") else f"read denied: {reason}")
            if not path.is_file():
                return ToolResult(ok=False, error=f"no such workbook: {spec.get('path')}")
            if path.suffix.lower() not in (".xlsx", ".xlsm"):
                return ToolResult(ok=False, error=f"excel_reconcile reads .xlsx and .xlsm workbooks (convert {path.name} first)")
            paths.append(path)

        def work() -> ToolResult:
            if target is not None and target.exists():
                return ToolResult(ok=False, error=f"{target.name} already exists in the workspace; give save_as another name")
            sides = []
            for path, spec, default in ((paths[0], sa, "A"), (paths[1], sb, "B")):
                name = str(spec.get("name") or default).strip()[:24] or default
                try:
                    sides.append(read_side(path, spec, name))
                except ValueError as exc:
                    return ToolResult(ok=False, error=str(exc))
                except Exception as exc:  # noqa: BLE001
                    return ToolResult(ok=False, error=_open_error(exc, path))
            a, b = sides
            if a.name == b.name:
                a.name, b.name = f"{a.name} (a)", f"{b.name} (b)"
            if not a.rows or not b.rows:
                empty = a if not a.rows else b
                return ToolResult(ok=False, error=f"{empty.name}: no row of sheet {empty.sheet} holds an amount in that column (check the column, or header_row)")
            res = match(a, b, days=days, tolerance_cents=tol)
            t = totals(a, b, res)
            text = render(a, b, res, t, days=days, tolerance_cents=tol)
            data: dict[str, Any] = {
                **{k: (v / 100 if k.endswith(("total", "total_a")) or k in ("difference", "explained") else v) for k, v in t.items()},
                "ties": t["difference"] == t["explained"],
                "only_a_rows": [x.row for x in res.only_a][:500], "only_b_rows": [x.row for x in res.only_b][:500],
                "opposite_signs": res.opposite_signs,
            }
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                write_result(target, a, b, res, t)
                text += f"\nThe full result is saved to: {target.resolve()}"
                data["abs_path"] = str(target.resolve())
                data["path"] = str(target.relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
            return ToolResult(ok=True, output=text, data=data)

        return await asyncio.to_thread(work)
