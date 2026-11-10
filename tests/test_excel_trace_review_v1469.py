"""The review of excel_trace (v1.467.0, checked for v1.469.0).

Each test here was red on the first cut:

* a what-if value written as a DATE ("2025-06-01") was held as text, and text
  is larger than any number in Excel, so =IF(B1>DATE(2026,1,1),100,0) came
  back 100 where Excel gives 0; a value written as a formula was held as
  text too;
* goal seek gave up when a value on ONE side of the present one could not be
  worked out (a square root of a negative, a division by zero), though the
  answer was on the other side; and it had no time limit;
* a what-if with nothing to watch walked every formula against every reached
  cell (115 s on a 57,000-formula workbook, with no limit) and, past 3,000
  reached cells, showed the first twelve cells instead of the end results;
* "how" built a list of EVERY cell of a range (4.7 GB for B1:XFD3000; the test uses a smaller one).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from iron_jarvis.documents import excel_trace as T
from iron_jarvis.documents.excel_formula import Grid
from iron_jarvis.documents.excel_recalc import store_results
from iron_jarvis.tools.base import ToolContext


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


def _run(path: Path, **args):
    return asyncio.run(T.ExcelTraceTool().execute({"path": path.name, **args}, _ctx(path.parent)))


def _save(tmp_path: Path, name: str, cells: dict, title: str = "In", stored: bool = True) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for where, value in cells.items():
        ws[where] = value
    path = tmp_path / name
    wb.save(path)
    if stored:
        store_results(path)
    return path


# ------------------------------------------------------------------- what if --


@pytest.fixture()
def dates(tmp_path):
    return _save(tmp_path, "d.xlsx", {
        "A1": "Start", "B1": dt.date(2026, 6, 1),
        "A2": "Late?", "B2": "=IF(B1>DATE(2026,1,1),100,0)",
        "A3": "Days left", "B3": "=DATE(2026,12,31)-B1",
        "A4": "Size", "B4": '=IF(B5<2000,"small","big")',
        "A5": "Base", "B5": 10,
    })


@pytest.mark.parametrize("typed", ["2025-06-01", "6/1/2025", "2025-06-01T00:00:00"])
def test_a_date_written_as_text_is_held_as_the_date_a_cell_would_hold(dates, typed):
    res = _run(dates, mode="what_if", set={"In!B1": typed}, watch=["In!B2", "In!B3"])
    assert res.ok, res.error
    rows = {r["cell"]: r for r in res.data["watch"]}
    assert rows["In!B2"]["then"] == 0, "Excel compares a date, not a text that is larger than every number"
    assert rows["In!B3"]["then"] == 578
    assert "set In!B1 (Start): 2026-06-01 -> 2025-06-01" in res.output, "a date reads as a date both ways"


def test_a_number_written_with_an_exponent_is_a_number(dates):
    res = _run(dates, mode="what_if", set={"In!B5": "1e3"}, watch=["In!B4"])
    assert res.ok and res.data["watch"][0]["then"] == "small"


@pytest.mark.parametrize("value", ["=B4*1.1", " =1+1"])
def test_a_formula_is_not_a_what_if_value(dates, value):
    res = _run(dates, mode="what_if", set={"In!B5": value}, watch=["In!B4"])
    assert not res.ok and "not a formula" in res.error


# ----------------------------------------------------------------- goal seek --


@pytest.fixture()
def curves(tmp_path):
    return _save(tmp_path, "g.xlsx", {"A1": "x", "B1": 4, "A2": "root", "B2": "=SQRT(B1)*10", "A3": "each", "B3": "=1000/B1",
                                      "A4": "never", "B4": "=IF(B1=4,1,SQRT(-ABS(B1)-1))"}, title="S")


def test_goal_seek_looks_on_the_side_that_can_be_worked_out(curves):
    res = _run(curves, mode="goal_seek", cell="S!B2", goal=100, change="S!B1")
    assert res.ok, res.error
    assert res.data["exact"] and res.data["answer"] == pytest.approx(100, rel=1e-6)
    res = _run(curves, mode="goal_seek", cell="S!B3", goal=10, change="S!B1")
    assert res.ok, res.error
    assert res.data["exact"] and res.data["answer"] == pytest.approx(100, rel=1e-6)


def test_goal_seek_says_so_when_no_side_can_be_searched(curves):
    grid = Grid(curves)
    try:
        with pytest.raises(ValueError) as said:
            T.goal_seek(grid, "S!B4", 5, "S!B1")
        assert "no value of that cell reaches the goal" in str(said.value) and "cannot be worked out" in str(said.value)
    finally:
        grid.close()


def test_goal_seek_stops_at_its_time_limit(curves, monkeypatch):
    monkeypatch.setattr(T, "DEADLINE_S", -1.0)
    res = _run(curves, mode="goal_seek", cell="S!B2", goal=100, change="S!B1")
    assert not res.ok and "too long" in res.error


# ------------------------------------------------------- what the cells feed --


def _chain(tmp_path: Path, n: int) -> Path:
    cells: dict = {"A1": "rate", "B1": 0.1}
    for r in range(2, n + 2):
        cells[f"A{r}"] = r
        cells[f"B{r}"] = f"=A{r}*$B$1"
        cells[f"C{r}"] = f"=B{r}+A{r}"
    cells[f"C{n + 3}"] = f"=SUM(C2:C{n + 1})"
    cells[f"D{n + 3}"] = f"=C{n + 3}*2"
    return _save(tmp_path, "chain.xlsx", cells, title="M", stored=False)


def test_a_what_if_with_nothing_to_watch_reports_the_end_result_of_a_large_sheet(tmp_path):
    """1,700 rows: 3,402 cells are reached. The first cut gave up telling the
    end results past 3,000 and showed the first twelve cells it met."""
    n = 1700
    path = _chain(tmp_path, n)
    res = _run(path, mode="what_if", set={"M!B1": 0.2})
    assert res.ok, res.error
    assert [r["cell"] for r in res.data["watch"]] == [f"M!D{n + 3}"], "the one figure nothing else uses"
    base = sum(r * 0.1 + r for r in range(2, n + 2)) * 2
    then = sum(r * 0.2 + r for r in range(2, n + 2)) * 2
    row = res.data["watch"][0]
    assert row["now"] == pytest.approx(base) and row["then"] == pytest.approx(then)
    assert f"+{2 * n + 1} more cells" in res.output


def test_following_the_cells_is_cut_at_its_time_limit_and_says_so(tmp_path, monkeypatch):
    path = _chain(tmp_path, 60)
    monkeypatch.setattr(T, "DEADLINE_S", -1.0)
    res = _run(path, mode="what_if", set={"M!B1": 0.2}, watch=["M!C63"])
    assert res.ok, "cells named to watch need no following"
    res = _run(path, mode="what_if", set={"M!B1": 0.2})
    assert not res.ok and "too large to follow" in res.error and "name the cells to watch" in res.error
    res = _run(path, mode="affects", cell="M!B1")
    assert res.ok and "took too long to follow to the end, so the list is not complete" in res.output


def _brute(parsed, start, depth):
    """The first cut's own way: every formula against every cell of a level."""
    seen = {(start[0].lower(), start[1], start[2])}
    level, out = [start], []
    for _ in range(depth):
        nxt = []
        for s, c, r, _f, refs in parsed:
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


def test_the_quick_way_to_follow_cells_finds_exactly_what_the_slow_way_finds(tmp_path):
    rng = random.Random(1469)
    for case in range(12):
        wb = Workbook()
        a = wb.active
        a.title = "A"
        b = wb.create_sheet("B b")
        for ws in (a, b):
            for r in range(1, 13):
                for c in range(1, 7):
                    roll = rng.random()
                    if roll < 0.35:
                        ws.cell(row=r, column=c, value=rng.randint(1, 9))
                    elif roll < 0.8:
                        other = "'B b'!" if rng.random() < 0.3 else ("a!" if rng.random() < 0.2 else "")
                        c1, r1 = rng.randint(1, 6), rng.randint(1, 12)
                        if rng.random() < 0.5:
                            ref = f"{other}{'ABCDEF'[c1 - 1]}{r1}"
                        else:
                            c2, r2 = rng.randint(c1, 6), rng.randint(r1, 12)
                            ref = f"{other}{'ABCDEF'[c1 - 1]}{r1}:{'ABCDEF'[c2 - 1]}{r2}"
                        ws.cell(row=r, column=c, value=f"=SUM({ref})" if rng.random() < 0.8 else f"=SUM({ref},C:D)")
        path = tmp_path / f"r{case}.xlsx"
        wb.save(path)
        grid = Grid(path)
        try:
            parsed, _cut = T._parsed_formulas(grid)
            for start in (("A", 1, 1), ("B b", 3, 4), ("A", 6, 12), ("a", 2, 2)):
                start = (grid.canon(start[0]), start[1], start[2])
                for depth in (1, 2, 6):
                    quick, cut = T._dependents_cells(parsed, [start], depth, float("inf"))
                    assert not cut and quick == _brute(parsed, start, depth), (case, start, depth)
                reached, _ = T._dependents_cells(parsed, [start], 6, float("inf"))
                ends = T._end_results(parsed, reached, float("inf"))
                refs = [ref for _s, _c, _r, _f, refs_ in parsed for ref in refs_]
                assert ends == [cell for cell in reached if not any(ref.holds(*cell) for ref in refs)], (case, start)
        finally:
            grid.close()


# ----------------------------------------------------------------------- how --


def test_how_never_lists_every_cell_of_a_huge_range(tmp_path):
    cells = {f"A{r}": r for r in range(1, 400)}
    cells["E1"] = "=SUM(B1:XFD300)"
    cells["F1"] = "=COUNT(A1:A399)"
    path = _save(tmp_path, "wide.xlsx", cells, title="W", stored=False)
    grid = Grid(path)
    try:
        tracemalloc.start()
        got = T.how(grid, "W", 5, 1)
        _now, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 150 * 1024 * 1024, f"{peak / 1e6:.0f} MB to describe one range"
        text = "\n".join(got["lines"])
        assert "W!B1:XFD300  [4914900 cells, 2 formulas, the rest typed in among the first 2,000]" in text
        assert "(+4914896 more cells in W!B1:XFD300)" in text
        small = "\n".join(T.how(grid, "W", 6, 1)["lines"])
        assert "W!A1:A399  [399 cells, all typed in]" in small
    finally:
        grid.close()


# ------------------------------------- the first cut's promises with no pin --


def test_how_stops_at_its_node_limit_on_a_wide_tree(tmp_path):
    """Six cells feed each cell, six levels down: 55,987 cells in all. (The
    first cut's own test was a narrow chain, which the depth alone bounds;
    with the node limit taken out it still passed.)"""
    cells: dict = {}
    for level in range(1, 8):
        for col in "ABCDEF":
            if level == 7:
                cells[f"{col}{level}"] = level
            else:
                cells[f"{col}{level}"] = "=" + "+".join(f"{c}{level + 1}" for c in "ABCDEF")
    path = _save(tmp_path, "tree.xlsx", cells, title="S")
    grid = Grid(path)
    try:
        got = T.how(grid, "S", 1, 1, depth=6)
        assert got["cut"] is True
        assert len(got["lines"]) <= 3 * T.MAX_NODES
    finally:
        grid.close()
    res = _run(path, mode="how", cell="S!A1", depth=6)
    assert res.ok and "(cut: the chain is larger than one answer shows" in res.output


def test_a_what_if_and_a_goal_seek_leave_the_grid_as_they_found_it(dates):
    grid = Grid(dates)
    try:
        before = grid.get("In", 2, 2)
        T.what_if(grid, {"In!B1": "2025-06-01"}, ["In!B2"])
        assert grid.overrides == {} and grid.fresh is False
        assert grid.get("In", 2, 2) == before, "the next read is the workbook's own figure again"
        T.goal_seek(grid, "In!B3", 100, "In!B1")
        assert grid.overrides == {} and grid.fresh is False
    finally:
        grid.close()
