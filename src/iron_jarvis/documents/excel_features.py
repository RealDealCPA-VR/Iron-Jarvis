"""Excel's own features in a SAVED workbook (v1.469.0): colour rules,
dropdown lists and number limits, frozen headings, column widths, named
ranges, tables, cell notes and how a sheet prints.

``excel_edit`` takes them as ``features``: a list of steps with the SAME
names and fields the live tool uses (``documents/excel_live._EDIT_HELP``), so
a model learns one vocabulary for a workbook that is open and one that is
not.

WHAT IS LEFT TO EXCEL, and said so. Inserting or deleting rows and columns,
sorting, renaming a sheet, pivot tables and charts are refused for a saved
file: done from outside Excel they would leave every formula that points at
the moved cells pointing at the wrong ones (nothing here adjusts
references), and a wrong formula in a client's workbook is worse than a
refusal. The refusal says to open the workbook in Excel and ask again,
where Excel itself does the work.

Pure over an openpyxl workbook: no file is opened or saved here.
"""

from __future__ import annotations

import re
from typing import Any

#: Steps this file applies.
FEATURE_OPS = (
    "conditional_format", "validation", "freeze", "column_width", "autofit",
    "name", "table", "note", "print_setup",
)
#: Steps only Excel itself can make safely.
NEEDS_EXCEL = (
    "insert_rows", "insert_columns", "delete_rows", "delete_columns", "sort", "filter", "clear_filter",
    "rename_sheet", "delete_sheet", "pivot", "pivot_edit", "chart", "chart_edit", "refresh",
)
NEEDS_EXCEL_WORDS = (
    "'{op}' is not done on a saved file: it would leave formulas pointing at the wrong cells. "
    "Open the workbook in Excel and ask again; Jarvis then has Excel itself do it."
)
MAX_FEATURES = 60


class FeatureRefused(ValueError):
    """A feature step that cannot be made, in words for the model. Raised
    before the workbook is saved, so the file is as it was."""

_RANGE_RX = re.compile(r"^\$?[A-Za-z]{1,3}\$?[0-9]{1,7}(?::\$?[A-Za-z]{1,3}\$?[0-9]{1,7})?$")
_COLS_RX = re.compile(r"^\$?([A-Za-z]{1,3})(?::\$?([A-Za-z]{1,3}))?$")
_HEX_RX = re.compile(r"^[0-9A-Fa-f]{6}$")
_NAME_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,254}$")


def _hex(v: Any, what: str) -> str:
    text = str(v or "").strip().lstrip("#")
    if not _HEX_RX.match(text):
        raise ValueError(f"{what} is a hex colour like FFF2CC")
    return text.upper()


def _split_sheet(text: str) -> "tuple[str, str]":
    """``Sales!A1:B9`` or ``'Q1 Sales'!A1`` as (sheet, range); ("", text) otherwise."""
    if "!" not in text:
        return "", text
    sheet, _, rng = text.rpartition("!")
    sheet = sheet.strip()
    if len(sheet) >= 2 and sheet[0] == sheet[-1] == "'":
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, rng


def _sheet_and_range(wb: Any, step: dict, default: "str | None", key: str = "range") -> "tuple[Any, str]":
    raw = str(step.get(key) or step.get("cell") or "").strip()
    if not raw:
        raise ValueError(f"'{step.get('op')}' needs '{key}'")
    named, rng = _split_sheet(raw)
    rng = rng.replace("$", "").upper()
    if not _RANGE_RX.match(rng):
        raise ValueError(f"'{raw}' is not a cell or a range like A1:D9")
    return _sheet(wb, named or step.get("sheet") or default), rng


def _sheet(wb: Any, name: Any) -> Any:
    want = str(name or "").strip()
    if not want:
        raise ValueError(f"say which sheet in 'sheet' (this workbook's sheets: {', '.join(wb.sheetnames)})")
    for title in wb.sheetnames:
        if title.lower() == want.lower():
            return wb[title]
    raise ValueError(f"no sheet called '{want}' (this workbook's sheets: {', '.join(wb.sheetnames)})")


# ------------------------------------------------------------------ the steps --


def _conditional_format(wb: Any, step: dict, default: "str | None") -> str:
    from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule, FormulaRule, Rule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.styles.differential import DifferentialStyle

    ws, rng = _sheet_and_range(wb, step, default)
    rule = str(step.get("rule") or "").strip().lower()
    fill = PatternFill(start_color=_hex(step["fill"], "fill"), end_color=_hex(step["fill"], "fill"), fill_type="solid") if step.get("fill") else None
    font = None
    if step.get("font_color") or step.get("bold"):
        font = Font(color=_hex(step["font_color"], "font_color") if step.get("font_color") else None, bold=bool(step.get("bold")) or None)
    if rule in ("data_bar", "color_scale"):
        if rule == "data_bar":
            made: Any = DataBarRule(start_type="min", end_type="max", color=_hex(step.get("fill") or "638EC6", "fill"))
        else:
            made = ColorScaleRule(start_type="min", start_color="F8696B", mid_type="percentile", mid_value=50,
                                  mid_color="FFEB84", end_type="max", end_color="63BE7B")
        ws.conditional_formatting.add(rng, made)
        return f"colour rule ({rule.replace('_', ' ')}) on {ws.title}!{rng}"
    if fill is None and font is None:
        raise ValueError("a colour rule needs 'fill' and/or 'font_color' (hex) so a matching cell looks different")
    ops = {"greater": "greaterThan", "less": "lessThan", "between": "between", "equal": "equal", "not_equal": "notEqual",
           "greater_equal": "greaterThanOrEqual", "less_equal": "lessThanOrEqual"}

    def operand(v: Any) -> str:
        if isinstance(v, bool) or v is None:
            raise ValueError(f"rule '{rule}' needs 'value'")
        if isinstance(v, (int, float)):
            return f"{v:.15g}"
        text = str(v).strip()
        if text.startswith("="):
            from .excel_formula import for_file

            return for_file(text)[1:]  # refuses a formula that reaches outside
        try:
            return f"{float(text.replace(',', '')):.15g}"
        except ValueError:
            return '"' + text.replace('"', '""') + '"'

    if rule in ops:
        formulas = [operand(step.get("value"))]
        if rule == "between":
            formulas.append(operand(step.get("value2")))
        ws.conditional_formatting.add(rng, CellIsRule(operator=ops[rule], formula=formulas, fill=fill, font=font))
        shown = " and ".join(formulas)
        return f"colour rule ({rule.replace('_', ' ')} {shown}) on {ws.title}!{rng}"
    first = rng.split(":")[0]
    if rule == "text_contains":
        text = str(step.get("value") or "")
        if not text:
            raise ValueError("rule 'text_contains' needs 'value' (the text to look for)")
        quoted = text.replace('"', '""')
        made = Rule(type="containsText", operator="containsText", text=text, dxf=DifferentialStyle(fill=fill, font=font),
                    formula=[f'NOT(ISERROR(SEARCH("{quoted}",{first})))'])
        ws.conditional_formatting.add(rng, made)
        return f"colour rule (text contains '{text}') on {ws.title}!{rng}"
    if rule == "formula":
        text = str(step.get("formula") or "").strip().lstrip("=")
        if not text:
            raise ValueError("rule 'formula' needs 'formula'")
        from .excel_formula import for_file

        ws.conditional_formatting.add(rng, FormulaRule(formula=[for_file("=" + text)[1:]], fill=fill, font=font))
        return f"colour rule (formula ={text}) on {ws.title}!{rng}"
    if rule == "duplicates":
        ws.conditional_formatting.add(rng, Rule(type="duplicateValues", dxf=DifferentialStyle(fill=fill, font=font)))
        return f"colour rule (duplicates) on {ws.title}!{rng}"
    if rule in ("top", "bottom"):
        try:
            rank = int(step.get("value") or 10)
        except (TypeError, ValueError):
            raise ValueError(f"rule '{rule}' takes a whole number in 'value' (how many)") from None
        if not 1 <= rank <= 1000:
            raise ValueError(f"rule '{rule}': 'value' is between 1 and 1000")
        ws.conditional_formatting.add(rng, Rule(type="top10", rank=rank, bottom=rule == "bottom" or None,
                                                dxf=DifferentialStyle(fill=fill, font=font)))
        return f"colour rule ({rule} {rank}) on {ws.title}!{rng}"
    raise ValueError("rule is one of greater, less, between, equal, not_equal, greater_equal, less_equal, "
                     "text_contains, formula, duplicates, top, bottom, data_bar, color_scale")


def _validation(wb: Any, step: dict, default: "str | None") -> str:
    from openpyxl.worksheet.datavalidation import DataValidation

    ws, rng = _sheet_and_range(wb, step, default)
    message = str(step.get("message") or "").strip()[:255]
    if step.get("list") is not None:
        items = [str(x).strip() for x in step["list"] if str(x).strip()] if isinstance(step["list"], list) else []
        if not items:
            raise ValueError("'list' is the choices, like [\"Open\", \"Closed\"]")
        if any("," in x for x in items):
            raise ValueError("a choice in 'list' cannot contain a comma; put the choices in cells and give 'source'")
        joined = ",".join(items)
        if len(joined) > 255:
            raise ValueError("the choices are too long for a typed list (255 characters); put them in cells and give 'source'")
        dv = DataValidation(type="list", formula1='"' + joined.replace('"', '""') + '"', allow_blank=True)
        what = f"dropdown ({len(items)} choices)"
    elif step.get("source"):
        named, src = _split_sheet(str(step["source"]).strip())
        src = src.replace("$", "").upper()
        if not _RANGE_RX.match(src):
            raise ValueError("'source' is a range like Lists!A2:A9")
        src_ws = _sheet(wb, named) if named else ws
        a, _, b = src.partition(":")
        fixed = ":".join("$" + re.sub(r"(\d+)", r"$\1", part, count=1) for part in ((a, b) if b else (a,)))
        title = src_ws.title.replace("'", "''")
        dv = DataValidation(type="list", formula1=f"'{title}'!{fixed}", allow_blank=True)
        what = f"dropdown (choices from {src_ws.title}!{src})"
    else:
        kind = str(step.get("type") or "").strip().lower()
        if kind not in ("whole", "decimal", "date"):
            raise ValueError("validation needs 'list', 'source', or 'type' whole|decimal|date with 'min' and/or 'max'")
        lo, hi = step.get("min"), step.get("max")
        if lo is None and hi is None:
            raise ValueError(f"validation type '{kind}' needs 'min' and/or 'max'")

        def lim(v: Any) -> str:
            if kind == "date":
                from .excel_tools import _as_date

                d = _as_date(v)
                if d is None:
                    raise ValueError(f"'{v}' is not a date like 2026-12-31")
                return f"DATE({d.year},{d.month},{d.day})"
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"'{v}' is not a number")
            return f"{v:.15g}"

        if lo is not None and hi is not None:
            dv = DataValidation(type=kind, operator="between", formula1=lim(lo), formula2=lim(hi), allow_blank=True)
            what = f"only {kind} values between {lo} and {hi}"
        elif lo is not None:
            dv = DataValidation(type=kind, operator="greaterThanOrEqual", formula1=lim(lo), allow_blank=True)
            what = f"only {kind} values of at least {lo}"
        else:
            dv = DataValidation(type=kind, operator="lessThanOrEqual", formula1=lim(hi), allow_blank=True)
            what = f"only {kind} values of at most {hi}"
    dv.showErrorMessage = True
    if message:
        dv.error, dv.errorTitle = message, "Not allowed here"
        dv.prompt, dv.showInputMessage = message, True
    dv.add(rng)
    ws.add_data_validation(dv)
    return f"{what} on {ws.title}!{rng}"


def _freeze(wb: Any, step: dict, default: "str | None") -> str:
    ws = _sheet(wb, step.get("sheet") or default)
    cell = str(step.get("cell") or "").strip().replace("$", "").upper()
    if cell in ("NONE", "A1", ""):
        if not cell:
            raise ValueError("freeze needs 'cell' (the first cell that still scrolls, like B2) or 'none'")
        ws.freeze_panes = None
        return f"nothing frozen on {ws.title}"
    if not _RANGE_RX.match(cell) or ":" in cell:
        raise ValueError("freeze takes one cell like B2, or 'none'")
    ws.freeze_panes = cell
    return f"rows above and columns left of {cell} stay in view on {ws.title}"


def _columns(step: dict) -> "list[str]":
    from openpyxl.utils import column_index_from_string, get_column_letter

    m = _COLS_RX.match(str(step.get("columns") or "").strip())
    if not m:
        raise ValueError("'columns' is a column or a span like A:C")
    a = column_index_from_string(m.group(1).upper())
    b = column_index_from_string((m.group(2) or m.group(1)).upper())
    if b < a or b - a > 500:
        raise ValueError("'columns' runs left to right, at most 500 columns")
    return [get_column_letter(i) for i in range(a, b + 1)]


def _column_width(wb: Any, step: dict, default: "str | None") -> str:
    ws = _sheet(wb, step.get("sheet") or default)
    width = step.get("width")
    if isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < float(width) <= 255:
        raise ValueError("'width' is a number of characters between 1 and 255")
    cols = _columns(step)
    for letter in cols:
        ws.column_dimensions[letter].width = float(width)
    return f"columns {cols[0]}:{cols[-1]} of {ws.title} set {float(width):g} wide"


def _autofit(wb: Any, step: dict, default: "str | None") -> str:
    """Widths from what the cells hold (a saved file has no Excel to measure
    its fonts, so this is by character count, said as such)."""
    from openpyxl.utils import get_column_letter

    ws = _sheet(wb, step.get("sheet") or default)
    cols = _columns(step) if step.get("columns") else [get_column_letter(i) for i in range(1, min(ws.max_column, 200) + 1)]
    longest: dict[str, int] = {c: 0 for c in cols}
    wanted = set(cols)
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 5000)):
        for c in row:
            letter = getattr(c, "column_letter", None)
            if letter in wanted and c.value is not None:
                text = str(c.value)
                if isinstance(c.value, float):
                    text = f"{c.value:,.2f}"
                if isinstance(c.value, str) and c.value.startswith("="):
                    text = "0" * 10
                longest[letter] = max(longest[letter], max((len(p) for p in text.split("\n")), default=0))
    for letter, n in longest.items():
        ws.column_dimensions[letter].width = float(min(60, max(8, n + 2)))
    return f"columns {cols[0]}:{cols[-1]} of {ws.title} sized to their contents (by the length of what they hold)"


def _name(wb: Any, step: dict, default: "str | None") -> str:
    from openpyxl.workbook.defined_name import DefinedName

    name = str(step.get("name") or "").strip()
    if not _NAME_RX.match(name) or _RANGE_RX.match(name):
        raise ValueError("'name' starts with a letter or underscore, has no spaces, and is not a cell address")
    raw = str(step.get("refers_to") or "").strip().lstrip("=")
    named, rng = _split_sheet(raw)
    rng = rng.replace("$", "").upper()
    if not named or not _RANGE_RX.match(rng):
        raise ValueError("'refers_to' is a sheet and a range, like Data!B2:B50")
    ws = _sheet(wb, named)
    a, _, b = rng.partition(":")
    fixed = ":".join("$" + re.sub(r"(\d+)", r"$\1", part, count=1) for part in ((a, b) if b else (a,)))
    title = ws.title.replace("'", "''")
    existed = name in wb.defined_names
    if existed:
        del wb.defined_names[name]
    wb.defined_names[name] = DefinedName(name, attr_text=f"'{title}'!{fixed}")
    return f"name {name} {'now' if existed else ''} means {ws.title}!{rng}".replace("  ", " ")


def _table(wb: Any, step: dict, default: "str | None") -> str:
    from openpyxl.worksheet.table import Table, TableStyleInfo

    ws, rng = _sheet_and_range(wb, step, default)
    if ":" not in rng:
        raise ValueError("a table needs a range with its heading row, like A1:D20")
    taken = {t.lower() for s in wb.worksheets for t in s.tables}
    name = str(step.get("name") or "").strip()
    if name:
        if not _NAME_RX.match(name) or _RANGE_RX.match(name):
            raise ValueError("a table's name starts with a letter or underscore and has no spaces")
        if name.lower() in taken:
            raise ValueError(f"this workbook already has a table called {name}")
    else:
        n = 1
        while f"table{n}" in taken:
            n += 1
        name = f"Table{n}"
    from openpyxl.utils.cell import range_boundaries

    c1, r1, c2, r2 = range_boundaries(rng)
    for t in ws.tables.values():
        tc1, tr1, tc2, tr2 = range_boundaries(t.ref)
        if not (c2 < tc1 or tc2 < c1 or r2 < tr1 or tr2 < r1):
            raise ValueError(f"{rng} overlaps the table {t.displayName} ({t.ref})")
    heads = [ws.cell(row=r1, column=c).value for c in range(c1, c2 + 1)]
    if any(h in (None, "") for h in heads) or len({str(h).lower() for h in heads}) != len(heads):
        raise ValueError("a table's first row must hold a different, non-empty heading in every column")
    for c in range(c1, c2 + 1):  # Excel wants a table's headings to be text
        cell = ws.cell(row=r1, column=c)
        if not isinstance(cell.value, str):
            cell.value = str(cell.value)
    table = Table(displayName=name, ref=rng)
    table.tableStyleInfo = TableStyleInfo(name=str(step.get("style") or "TableStyleMedium2"), showRowStripes=True)
    ws.add_table(table)
    return f"table {name} on {ws.title}!{rng}"


def _note(wb: Any, step: dict, default: "str | None") -> str:
    from openpyxl.comments import Comment

    ws, rng = _sheet_and_range(wb, step, default, key="cell")
    if ":" in rng:
        raise ValueError("a note goes on one cell")
    text = str(step.get("text") or "").strip()
    if not text:
        ws[rng].comment = None
        return f"note removed from {ws.title}!{rng}"
    ws[rng].comment = Comment(text[:2000], "Iron Jarvis")
    return f"note on {ws.title}!{rng}"


def _print_setup(wb: Any, step: dict, default: "str | None") -> str:
    ws = _sheet(wb, step.get("sheet") or default)
    did: list[str] = []
    if step.get("orientation"):
        o = str(step["orientation"]).strip().lower()
        if o not in ("portrait", "landscape"):
            raise ValueError("'orientation' is portrait or landscape")
        ws.page_setup.orientation = o
        did.append(o)
    if step.get("fit_width") is not None or step.get("fit_height") is not None:
        for key in ("fit_width", "fit_height"):
            v = step.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100):
                raise ValueError(f"'{key}' is a whole number of pages (0 = as many as needed)")
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = int(step.get("fit_width") if step.get("fit_width") is not None else 1)
        ws.page_setup.fitToHeight = int(step.get("fit_height") if step.get("fit_height") is not None else 0)
        did.append(f"fits {ws.page_setup.fitToWidth} page(s) wide")
    if step.get("print_area"):
        area = str(step["print_area"]).replace("$", "").upper()
        if not _RANGE_RX.match(area):
            raise ValueError("'print_area' is a range like A1:H40")
        ws.print_area = area
        did.append(f"prints {area}")
    if step.get("title_rows"):
        m = re.match(r"^\$?(\d{1,7})(?::\$?(\d{1,7}))?$", str(step["title_rows"]).strip())
        if not m:
            raise ValueError("'title_rows' is a row or rows like 1 or 1:2")
        ws.print_title_rows = f"{m.group(1)}:{m.group(2) or m.group(1)}"
        did.append(f"rows {ws.print_title_rows} repeat on every page")
    for key in ("header", "footer"):
        if step.get(key) is not None:
            getattr(ws, f"odd{key.capitalize()}").center.text = str(step[key])[:200]
            did.append(f"{key} set")
    if not did:
        raise ValueError("print_setup needs at least one of orientation, fit_width, fit_height, print_area, title_rows, header, footer")
    return f"printing of {ws.title}: " + ", ".join(did)


_STEPS = {
    "conditional_format": _conditional_format, "validation": _validation, "freeze": _freeze,
    "column_width": _column_width, "autofit": _autofit, "name": _name, "table": _table, "note": _note,
    "print_setup": _print_setup,
}


def check_features(features: Any) -> "list[dict]":
    """The steps as a list of dicts, or FeatureRefused in words. Checked BEFORE the
    workbook is opened, so a step only Excel can make costs nothing."""
    if not isinstance(features, list) or not all(isinstance(f, dict) for f in features):
        raise FeatureRefused("'features' is a list of steps, each {op, ...}")
    if len(features) > MAX_FEATURES:
        raise FeatureRefused(f"at most {MAX_FEATURES} feature steps in one call")
    for n, step in enumerate(features, start=1):
        op = str(step.get("op") or "").strip().lower()
        if op in NEEDS_EXCEL:
            raise FeatureRefused(f"step {n}: " + NEEDS_EXCEL_WORDS.format(op=op))
        if op not in _STEPS:
            raise FeatureRefused(f"step {n}: unknown step '{op or '(none)'}'. Steps for a saved file: {', '.join(FEATURE_OPS)}")
    return features


def apply_features(wb: Any, default_sheet: "str | None", features: "list[dict]") -> "list[str]":
    """Make every step on the open workbook; one line per step. A step that
    fails raises FeatureRefused naming its number, and the caller does not save."""
    done: list[str] = []
    for n, step in enumerate(check_features(features), start=1):
        op = str(step.get("op") or "").strip().lower()
        try:
            done.append(_STEPS[op](wb, step, default_sheet))
        except ValueError as exc:  # an OutsideFormula too: it is a ValueError
            raise FeatureRefused(f"step {n} ({op}): {exc}") from None
        except KeyError as exc:
            raise FeatureRefused(f"step {n} ({op}): missing {exc}") from None
    return done
