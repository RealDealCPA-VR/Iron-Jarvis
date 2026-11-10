"""The review of excel_trace: ARRAY formulas under a what-if (v1.469.0).

Measured against the real Excel on the reviewer's PC (a workbook saved by a
hidden Excel of the script's own, then A2 changed from 2 to 5):

    C1  {=SUM(A1:A3*B1:B3)}   Excel 200   the first cut said 140
    D1  =C1*2                 Excel 400   the first cut said 280
    H2  (filled by H1 =A1:A2*2) Excel 10  the first cut said 4
    I1  =H2+1                 Excel 11    the first cut said 5

In Excel 365 a formula that works on a whole range at once is stored as an
array formula even when it was typed the ordinary way, so these are common.
The library hands such a cell over as an object, not as text; the engine read
it as a typed-in value, used the result Excel had saved, and so every figure
behind it stayed as it was, with no warning. "how" called it "typed in" and
"affects" never listed it.

Jarvis does not work array formulas out. It now says so and gives NO figure
for a cell that rests on one the change reaches; a cell the change does not
reach keeps its figure.
"""

from __future__ import annotations

import asyncio
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula

from iron_jarvis.documents import excel_trace as T
from iron_jarvis.documents.excel_formula import Grid
from iron_jarvis.tools.base import ToolContext

#: What Excel saved for each formula cell with A1:A3 = 1, 2, 3 and B1:B3 = 10, 20, 30.
SAVED = {"C1": 140, "D1": 280, "F1": 140, "I1": 5, "K1": 60, "L1": 120, "H1": 2}


def _with_saved_results(path: Path, values: dict) -> None:
    """Put a result beside each formula, the way Excel stores one (the
    library writes formulas with no result)."""
    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    name = "xl/worksheets/sheet1.xml"
    xml = parts[name].decode("utf-8")
    for cell, value in values.items():
        pattern = re.compile(r'(<c r="' + cell + r'"[^>]*>)(<f[^>]*>[^<]*</f>)<v\s*/>|(<c r="' + cell + r'"[^>]*>)(<f[^>]*>[^<]*</f>)<v></v>')
        xml, n = pattern.subn(lambda m: (m.group(1) or m.group(3)) + (m.group(2) or m.group(4)) + f"<v>{value}</v>", xml)
        assert n == 1, (cell, xml[:600])
    parts[name] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in parts.items():
            z.writestr(n, data)


@pytest.fixture()
def arrays(tmp_path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    for i, (a, b) in enumerate(((1, 10), (2, 20), (3, 30)), start=1):
        ws[f"A{i}"], ws[f"B{i}"] = a, b
    ws["C1"] = ArrayFormula("C1", "=SUM(A1:A3*B1:B3)")
    ws["D1"] = "=C1*2"
    ws["F1"] = "=SUMPRODUCT(A1:A3,B1:B3)"
    ws["H1"] = ArrayFormula("H1:H2", "=A1:A2*2")
    ws["H2"] = 4  # a cell the array formula in H1 fills: Excel stores it as a plain value
    ws["I1"] = "=H2+1"
    ws["K1"] = ArrayFormula("K1", "=SUM(B1:B3*1)")  # an array formula the change does not reach
    ws["L1"] = "=K1*2"
    path = tmp_path / "arrays.xlsx"
    wb.save(path)
    _with_saved_results(path, SAVED)
    grid = Grid(path)
    try:
        assert grid.saved("S", 3, 1) == 140 and grid.saved("S", 11, 1) == 60, "the fixture holds Excel's saved results"
    finally:
        grid.close()
    return path


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


def _run(path: Path, **args):
    return asyncio.run(T.ExcelTraceTool().execute({"path": path.name, **args}, _ctx(path.parent)))


def test_a_what_if_gives_no_figure_for_a_cell_that_rests_on_an_array_formula(arrays):
    res = _run(arrays, mode="what_if", set={"S!A2": 5}, watch=["S!C1", "S!D1", "S!F1", "S!H2", "S!I1"])
    assert res.ok, res.error
    rows = {r["cell"]: r for r in res.data["watch"]}
    for cell in ("S!C1", "S!D1", "S!H2", "S!I1"):
        assert "then" not in rows[cell], f"{cell}: a figure here would be the result Excel saved BEFORE the change"
        assert "array formula" in rows[cell]["problem"]
    assert rows["S!F1"]["then"] == 200, "an ordinary formula over the same cells is worked out (Excel: 200)"
    assert "S!D1" in res.output and "280 -> 280" not in res.output


def test_an_array_formula_the_change_does_not_reach_keeps_its_figure(arrays):
    res = _run(arrays, mode="what_if", set={"S!A2": 5}, watch=["S!K1", "S!L1"])
    assert res.ok, res.error
    assert {r["cell"]: (r["now"], r.get("then")) for r in res.data["watch"]} == {"S!K1": (60, 60), "S!L1": (120, 120)}
    res = _run(arrays, mode="what_if", set={"S!B2": 25}, watch=["S!L1"])
    assert "then" not in res.data["watch"][0] and "array formula" in res.data["watch"][0]["problem"]


def test_with_nothing_to_watch_the_cells_behind_an_array_formula_are_still_found(arrays):
    res = _run(arrays, mode="what_if", set={"S!A2": 5})
    assert res.ok, res.error
    watched = {r["cell"] for r in res.data["watch"]}
    assert watched == {"S!D1", "S!I1", "S!F1"}, (
        "the END results: D1 hangs on the array formula in C1, I1 on a cell H1 fills, so C1 and H1 are on the way"
    )
    rows = {r["cell"]: r for r in res.data["watch"]}
    assert "then" not in rows["S!D1"] and "then" not in rows["S!I1"] and rows["S!F1"]["then"] == 200


def test_goal_seek_does_not_search_through_an_array_formula(arrays):
    res = _run(arrays, mode="goal_seek", cell="S!D1", goal=400, change="S!A2")
    assert not res.ok and "array formula" in res.error
    res = _run(arrays, mode="goal_seek", cell="S!F1", goal=200, change="S!A2")
    assert res.ok and res.data["answer"] == pytest.approx(5)
    res = _run(arrays, mode="goal_seek", cell="S!F1", goal=200, change="S!H2")
    assert not res.ok and "array formula" in res.error, "a cell an array formula fills is not a typed-in cell to vary"


def test_how_and_affects_know_an_array_formula_is_a_formula(arrays):
    res = _run(arrays, mode="how", cell="S!D1")
    assert res.ok, res.error
    assert "S!C1 = 140  [{=SUM(A1:A3*B1:B3)}, an array formula]" in res.output
    assert "S!A1:A3" in res.output, "what the array formula reads is followed"
    assert "S!C1 = 140  [typed in]" not in res.output and "S!C1" not in res.data["inputs"]
    res = _run(arrays, mode="how", cell="S!I1")
    assert "S!H2 = 4  [filled by the array formula in S!H1]" in res.output and "S!H2" not in res.data["inputs"]
    res = _run(arrays, mode="affects", cell="S!A2", depth=3)
    assert res.ok, res.error
    for cell in ("S!C1", "S!D1", "S!H1", "S!I1", "S!F1"):
        assert f"{cell} " in res.output, f"{cell} uses A2, directly or through an array formula"
    assert "S!K1" not in res.output


def test_an_array_formula_that_may_read_cells_it_does_not_name_is_never_trusted(tmp_path):
    """OFFSET and INDIRECT read cells the formula's text does not name, so
    "the change does not reach it" cannot be told from the text."""
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    for i, a in enumerate((1, 2, 3), start=1):
        ws[f"A{i}"] = a
    ws["B1"] = 7
    ws["C1"] = ArrayFormula("C1", "=SUM(OFFSET(A1,0,0,3,1)*2)")  # reads A1:A3 without naming A2
    ws["D1"] = '=SUM(INDIRECT("A1:A3"))'  # an ordinary formula that does the same
    ws["E1"] = ArrayFormula("E1", "=SUM(D1*1)")  # an array formula behind it
    ws["F1"] = ArrayFormula("F1", "=SUM(B1*1)")  # names all it reads, and A2 is not among it
    path = tmp_path / "unseen.xlsx"
    wb.save(path)
    _with_saved_results(path, {"C1": 12, "D1": 6, "E1": 6, "F1": 7})
    res = _run(path, mode="what_if", set={"S!A2": 5}, watch=["S!C1", "S!E1", "S!F1"])
    assert res.ok, res.error
    rows = {r["cell"]: r for r in res.data["watch"]}
    assert "then" not in rows["S!C1"], "Excel: 18. The saved 12 is not the answer"
    assert "then" not in rows["S!E1"], "Excel: 9. It rests on a cell that reads A2 without naming it"
    assert rows["S!F1"]["then"] == 7


def test_a_what_if_does_not_replace_part_of_an_array_formula(arrays):
    res = _run(arrays, mode="what_if", set={"S!H2": 50}, watch=["S!I1"])
    assert not res.ok and "array formula" in res.error
    res = _run(arrays, mode="what_if", set={"S!H1": 50}, watch=["S!I1"])
    assert not res.ok and "array formula" in res.error, "H1 fills H2 as well"
    res = _run(arrays, mode="what_if", set={"S!C1": 1000}, watch=["S!D1"])
    assert res.ok and res.data["watch"][0]["then"] == 2000, "a one-cell array formula can be replaced whole"
    assert "holds a formula" in res.output


def test_the_grid_is_left_as_found_after_an_array_what_if(arrays):
    grid = Grid(arrays)
    try:
        T.what_if(grid, {"S!A2": 5}, ["S!D1"])
        assert grid.array_stale is None and grid.overrides == {} and grid.fresh is False
        with pytest.raises(ValueError):
            T.goal_seek(grid, "S!D1", 400, "S!A2")
        assert grid.array_stale is None and grid.overrides == {} and grid.fresh is False
        assert grid.get("S", 4, 1) == 280
    finally:
        grid.close()


def test_the_engine_reads_as_before_when_no_what_if_is_in_place(arrays):
    grid = Grid(arrays)
    try:
        assert grid.get("S", 3, 1) == 140 and grid.get("S", 4, 1) == 280 and grid.get("S", 8, 2) == 4
        assert grid.formula("S", 3, 1) is None, "the engine's own view of a formula is unchanged"
        assert grid.array_formula("S", 3, 1) == (3, 1, "=SUM(A1:A3*B1:B3)")
        assert grid.array_formula("S", 8, 2) == (8, 1, "=A1:A2*2") and grid.array_formula("S", 1, 1) is None
    finally:
        grid.close()
