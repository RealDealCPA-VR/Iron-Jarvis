"""``calculate``: exact arithmetic for every figure an answer states (v1.465.0).

The live finding: a client memo whose table was right and whose prose was not
("gross travel $2,235.80" for a true $1,655.80, a total that matched nothing).
The table's numbers were copied from a tool; the prose numbers were worked out
by the model in its head. Language models add badly.

This tool hands the same engine the spreadsheet tools use to plain arithmetic:
no file, no workbook, no side effect. It takes Excel-style expressions, so a
model that can write a formula can check a sentence::

    calculate(expressions=[
        {"label": "travel", "expression": "=612.40*2+431"},
        {"label": "tax",    "expression": "=ROUND(travel*7.25%, 2)"},
        {"label": "total",  "expression": "=travel+tax"},
    ])

A label that is a plain word becomes a NAME later expressions can use, so a
chain of steps is worked out once, in order, with nothing retyped.
"""

from __future__ import annotations

import re
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from .excel_formula import SUPPORTED, FormulaError, Grid, evaluate_formula

#: At most this many expressions in one call.
MAX_EXPRESSIONS = 40
#: A label that can be a name. One shaped like a cell ("A1") is harmless: the
#: engine reads "A1" in an expression as a cell before it looks at any name.
_LABEL_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")


def shown(value: Any) -> str:
    """A result as it is reported: every digit that matters, thousands
    separators, and never a rounded-away difference."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        v = float(value)
        if v == int(v) and abs(v) < 1e15:
            return f"{int(v):,}"
        if round(v, 2) == round(v, 10):
            return f"{v:,.2f}"  # money reads as money: 1,655.80
        # The 15 significant digits Excel keeps, never the noise past them
        # (-1234567.891 is -1,234,567.891, not ...8910000001).
        text = f"{v:.15g}"
        if "e" in text or "E" in text:
            return text
        whole, _, frac = text.lstrip("-").partition(".")
        frac = frac[:10].rstrip("0")
        out = f"{int(whole):,}" + (f".{frac}" if frac else "")
        return ("-" if v < 0 and out.strip("0,.") else "") + out
    return repr(value) if isinstance(value, str) else str(value)


def run(items: list[Any]) -> list[dict[str, Any]]:
    """Work each expression out in order. Pure; never raises."""
    from .excel_recalc import tidy

    grid = Grid(None)
    out: list[dict[str, Any]] = []
    for i, item in enumerate(items[:MAX_EXPRESSIONS]):
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            expr = str(item.get("expression") or item.get("formula") or "").strip()
        else:
            label, expr = "", str(item or "").strip()
        row: dict[str, Any] = {"label": label or f"#{i + 1}", "expression": expr}
        if not expr:
            row["error"] = "no expression was given"
            out.append(row)
            continue
        try:
            value = tidy(evaluate_formula(expr, grid, ""))
        except FormulaError as exc:
            row["error"] = str(exc).split(" — ")[0]
        except Exception as exc:  # noqa: BLE001 - one odd expression never fails the call
            row["error"] = type(exc).__name__
        else:
            row["value"] = value
            if label and _LABEL_RX.match(label):
                grid.variables[label.lower()] = value
        out.append(row)
    return out


class CalculateTool(Tool):
    name = "calculate"
    reversibility = Reversibility.READONLY
    description = (
        "Work out arithmetic EXACTLY (the spreadsheet engine, no file needed). "
        "Use it for EVERY figure you state that you did not copy from a source: "
        "totals, differences, percentages, tax, interest, averages, per-unit "
        "amounts, dates. Never do arithmetic in your head. Pass Excel-style "
        "expressions: '=612.40*2+431', '=ROUND(1655.80*7.25%,2)', "
        "'=SUM(1333.60,1043.40,219.75)', '=PMT(6%/12,360,-250000)', "
        "'=EDATE(DATE(2026,1,31),1)'. Give an expression a one-word label and "
        "later expressions can use it by name (label 'travel', then "
        "'=travel*1.0725'). Report the results exactly as returned."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "expressions": {
                "type": "array",
                "description": "Expressions to work out, in order (at most 40).",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "expression": {"type": "string"},
                            },
                            "required": ["expression"],
                        },
                    ]
                },
            },
            "expression": {"type": "string", "description": "One expression (short form)."},
        },
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        items = args.get("expressions")
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            items = []
        single = str(args.get("expression") or "").strip()
        if single:
            items = [single, *items]
        if not items:
            return ToolResult(
                ok=False,
                error="pass expressions, for example ['=612.40*2+431', '=ROUND(1655.80*7.25%,2)']",
            )
        left_out = max(0, len(items) - MAX_EXPRESSIONS)
        rows = run(items)
        lines = []
        for r in rows:
            head = f"{r['label']}: {r['expression']}"
            lines.append(f"{head} = {shown(r['value'])}" if "value" in r
                         else f"{head} -> NOT WORKED OUT: {r['error']}")
        if left_out:
            lines.append(f"({left_out} more expression(s) were not worked out: at most "
                         f"{MAX_EXPRESSIONS} in one call)")
        failed = sum(1 for r in rows if "error" in r)
        if failed == len(rows):
            return ToolResult(
                ok=False,
                error="\n".join(lines) + f"\nFunctions it knows: {', '.join(SUPPORTED)}",
                data={"results": rows},
            )
        return ToolResult(ok=True, output="\n".join(lines),
                          data={"results": rows, "failed": failed})
