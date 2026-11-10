"""How a number is built, and what would change it (v1.467.0).

Runs anywhere (no Excel): the workbooks are written with the library and their
results stored by ``excel_recalc``. That the what-if and goal-seek figures
equal EXCEL's own is checked against the real Excel in
``tests/test_excel_trace_real_v1467.py``.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.workbook.defined_name import DefinedName

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.documents import excel_live as L
from iron_jarvis.documents import excel_trace as T
from iron_jarvis.documents.excel_formula import Grid
from iron_jarvis.documents.excel_recalc import store_results
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.autoselect import live_excel_companions, wants_excel_trace
from iron_jarvis.tools.base import ToolContext

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _budget(path: Path) -> Path:
    wb = Workbook()
    a = wb.active
    a.title = "Assumptions"
    for row in (("Assumption", "Value"), ("Starting revenue", 42000), ("Monthly growth", 0.05), ("Cost share", 0.62), ("Tax rate", 0.21)):
        a.append(row)
    m = wb.create_sheet("Monthly")
    m.append(("Month", "Revenue", "Costs", "Tax", "Net"))
    for i, name in enumerate(MONTHS, start=2):
        rev = "=Assumptions!$B$2" if i == 2 else f"=B{i - 1}*(1+Growth)"
        m.append((name, rev, f"=B{i}*Assumptions!$B$4", f"=(B{i}-C{i})*Assumptions!$B$5", f"=B{i}-C{i}-D{i}"))
    m.append(("Total", "=SUM(B2:B13)", "=SUM(C2:C13)", "=SUM(D2:D13)", "=SUM(E2:E13)"))
    s = wb.create_sheet("My Notes")
    s["A1"], s["B1"] = "Best month", "=MAX(Monthly!E:E)"
    wb.defined_names["Growth"] = DefinedName("Growth", attr_text="Assumptions!$B$3")
    wb.save(path)
    store_results(path)
    return path


def _net_total(growth: float, start: float = 42000, cost: float = 0.62, tax: float = 0.21) -> float:
    return sum(start * (1 + growth) ** k * (1 - cost) * (1 - tax) for k in range(12))


@pytest.fixture()
def book(tmp_path):
    return _budget(tmp_path / "budget.xlsx")


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


def _run(ws: Path, **args):
    return asyncio.run(T.ExcelTraceTool().execute({"path": "budget.xlsx", **args}, _ctx(ws)))


# ------------------------------------------------------------------ the engine --


def test_the_engine_reads_as_before_until_asked_for_a_what_if(book):
    grid = Grid(book)
    try:
        saved = grid.get("Monthly", 2, 13)
        assert grid.computed == 0, "a saved result is read, not worked out"
        grid.fresh = True
        grid.reset()
        assert abs(grid.get("Monthly", 2, 13) - saved) < 1e-9 and grid.computed > 0
        grid.overrides = {("assumptions", 2, 3): 0.08}
        grid.reset()
        assert abs(grid.get("monthly", 2, 13) - 42000 * 1.08 ** 11) < 1e-6
        assert grid.get("ASSUMPTIONS", 2, 3) == 0.08, "the override answers whatever case the sheet is written in"
        grid.overrides, grid.fresh = {}, False
        grid.reset()
        assert grid.get("Monthly", 2, 13) == saved
    finally:
        grid.close()


def test_a_formulas_references_are_read_in_order_each_once(book):
    grid = Grid(book)
    try:
        refs = [r.label() for r in T.refs_of("=B12*(1+Growth)+SUM(Monthly!E2:E13)+'My Notes'!$B$1+B12+assumptions!b2", "Monthly", grid)]
        assert refs == ["Monthly!B12", "Assumptions!B3", "Monthly!E2:E13", "My Notes!B1", "Assumptions!B2"]
        cols = T.refs_of("=MAX(Monthly!E:E)", "My Notes", grid)
        assert cols[0].label() == "Monthly!E1:E14" and cols[0].cells == 14
        assert T.refs_of('="B2 is text"&"A1:B9"', "Monthly", grid) == []
        assert T.refs_of("=((", "Monthly", grid) == []
    finally:
        grid.close()


@pytest.mark.parametrize(
    "text, want",
    [("Monthly!B13", ("Monthly", 2, 13)), ("'My Notes'!$B$1", ("My Notes", 2, 1)), ("b13", ("Dflt", 2, 13)), ("Monthly!$E$14", ("Monthly", 5, 14))],
)
def test_a_cell_is_read_with_or_without_its_sheet(text, want):
    assert T.parse_cell(text, "Dflt") == want


@pytest.mark.parametrize("text", ["", "Monthly!", "B", "Monthly!B13:C14", "13B"])
def test_a_bad_cell_is_refused_in_words(text):
    with pytest.raises(ValueError):
        T.parse_cell(text, "Dflt")


def test_a_cell_with_no_sheet_and_no_default_says_so():
    with pytest.raises(ValueError, match="say which sheet"):
        T.parse_cell("B13")


# ----------------------------------------------------------------------- how --


def test_how_follows_a_number_down_to_its_typed_inputs(book, tmp_path):
    res = _run(tmp_path, cell="Monthly!B4", depth=6)
    assert res.ok, res.error
    lines = res.output.splitlines()
    assert lines[1].startswith("Monthly!B4 (Mar) = 46,305  [=B3*(1+Growth)]")
    assert "  Monthly!B3 (Feb) = 44,100  [=B2*(1+Growth)]" in lines
    assert "      Assumptions!B2 (Starting revenue) = 42,000  [typed in]" in lines
    assert "(shown above)" not in res.output or res.output.count("Assumptions!B3") >= 1
    assert res.data["inputs"] == {"Assumptions!B2": "42,000 (Starting revenue)", "Assumptions!B3": "0.05 (Monthly growth)"}
    assert "typed-in inputs it rests on: " in res.output


def test_how_stops_at_its_depth_and_says_there_is_more(book, tmp_path):
    res = _run(tmp_path, cell="Monthly!E14", depth=2)
    assert "Monthly!E2:E13  [12 cells, all formulas]" in res.output
    assert "(+8 more cells in Monthly!E2:E13)" in res.output
    assert res.output.count("these go deeper") == 1, "said once for the range, not once per cell"
    assert "Assumptions!B4" not in res.output


def test_how_is_bounded_on_a_long_chain(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"] = 1
    for r in range(2, 400):
        ws[f"A{r}"] = f"=A{r - 1}+B{r - 1}+C{r - 1}"
        ws[f"B{r - 1}"], ws[f"C{r - 1}"] = 1, 2
    wb.save(tmp_path / "budget.xlsx")
    store_results(tmp_path / "budget.xlsx")
    res = _run(tmp_path, cell="S!A399", depth=6)
    assert res.ok and len(res.output.splitlines()) < 260 and "goes deeper" in res.output


# ------------------------------------------------------------------- affects --


def test_affects_lists_what_uses_a_cell_level_by_level(book, tmp_path):
    res = _run(tmp_path, mode="affects", cell="Assumptions!B3", depth=1)
    assert res.ok and res.data["count"] == 11
    assert "Monthly!B3 (Feb) = 44,100  [=B2*(1+Growth)]" in res.output and "Monthly!B2" not in res.output.split("\n", 1)[1]
    deep = _run(tmp_path, mode="affects", cell="Assumptions!B3", depth=6)
    assert deep.data["count"] > 40 and "My Notes!B1 (Best month)" in deep.output
    none = _run(tmp_path, mode="affects", cell="My Notes!B1")
    assert none.ok and none.output.startswith("No formula in budget.xlsx uses My Notes!B1.")


# ------------------------------------------------------------------- what if --


def test_what_if_gives_the_figures_the_formulas_come_to(book, tmp_path):
    before = book.read_bytes()
    res = _run(tmp_path, mode="what_if", set={"Assumptions!B3": "8%"}, watch=["Monthly!B13", "Monthly!E14"])
    assert res.ok, res.error
    rows = {r["cell"]: r for r in res.data["watch"]}
    assert abs(rows["Monthly!B13"]["then"] - 42000 * 1.08 ** 11) < 1e-6
    assert abs(rows["Monthly!E14"]["then"] - _net_total(0.08)) < 1e-6
    assert abs(rows["Monthly!E14"]["change"] - (_net_total(0.08) - _net_total(0.05))) < 1e-6
    assert "NOTHING WAS CHANGED" in res.output and "set Assumptions!B3 (Monthly growth): 0.05 -> 0.08" in res.output
    assert book.read_bytes() == before, "the workbook is not written"
    assert load_workbook(book)["Assumptions"]["B3"].value == 0.05


def test_what_if_with_nothing_to_watch_reports_the_end_results(book, tmp_path):
    res = _run(tmp_path, mode="what_if", set={"Assumptions!B4": 0.5})
    cells = [r["cell"] for r in res.data["watch"]]
    # The figures nothing else uses. Monthly!E14 is not one: the note's
    # MAX(Monthly!E:E) reads it.
    assert cells == ["Monthly!C14", "Monthly!D14", "My Notes!B1"]
    assert res.data["more"] == 37 and "(+37 more cells change on the way to these" in res.output


def test_several_cells_can_be_set_at_once_and_a_formula_cell_is_flagged(book, tmp_path):
    res = _run(tmp_path, mode="what_if", set={"Assumptions!B2": 50000, "Monthly!B13": 0}, watch=["Monthly!B14"])
    want = sum(50000 * 1.05 ** k for k in range(11))
    assert abs(res.data["watch"][0]["then"] - want) < 1e-6
    assert "NOTE: this cell holds a formula, which the what-if replaces" in res.output


def test_a_cell_jarvis_cannot_work_out_gets_no_figure(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"], ws["A2"], ws["A3"] = 5, "=INDIRECT(\"A1\")*2", "=A1*3"
    wb.save(tmp_path / "budget.xlsx")
    res = _run(tmp_path, mode="what_if", set={"S!A1": 9}, watch=["S!A2", "S!A3"])
    rows = {r["cell"]: r for r in res.data["watch"]}
    assert "problem" in rows["S!A2"] and "then" not in rows["S!A2"] and "INDIRECT" in rows["S!A2"]["problem"]
    assert rows["S!A3"]["then"] == 27


def test_a_cell_whose_saved_result_differs_from_jarvis_is_not_trusted(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"], ws["A2"] = 5, "=A1*2"
    wb.save(tmp_path / "budget.xlsx")
    store_results(tmp_path / "budget.xlsx")
    # Excel "saved" 11 where the formula comes to 10 (a stale or volatile result).
    import zipfile

    p = tmp_path / "budget.xlsx"
    with zipfile.ZipFile(p) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    sheet = next(n for n in parts if n.startswith("xl/worksheets/sheet"))
    assert b"<v>10</v>" in parts[sheet]
    parts[sheet] = parts[sheet].replace(b"<v>10</v>", b"<v>11</v>")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in parts.items():
            z.writestr(n, data)
    res = _run(tmp_path, mode="what_if", set={"S!A1": 9}, watch=["S!A2"])
    row = res.data["watch"][0]
    assert "then" not in row and "Excel saved 11" in row["problem"] and "not trusted" in row["problem"]


def test_what_if_refuses_what_it_cannot_answer(book, tmp_path):
    assert "give the cells to change" in _run(tmp_path, mode="what_if").error
    assert "nothing uses those cells" in _run(tmp_path, mode="what_if", set={"My Notes!A1": 3}).error
    assert "no sheet named" in _run(tmp_path, mode="what_if", set={"Nope!A1": 3}, watch=["Monthly!B2"]).error.lower()


# ----------------------------------------------------------------- goal seek --


def test_goal_seek_finds_the_input_that_reaches_the_target(book, tmp_path):
    res = _run(tmp_path, mode="goal_seek", cell="Monthly!E14", goal=300000, change="Assumptions!B3")
    assert res.ok, res.error
    g = res.data["answer"]
    assert abs(_net_total(g) - 300000) < 0.01, g
    assert abs(res.data["reaches"] - 300000) < 0.01 and res.data["start"] == 0.05
    assert "NOTHING WAS CHANGED" in res.output and "Assumptions!B3 must be" in res.output
    down = _run(tmp_path, mode="goal_seek", cell="Monthly!E14", goal=150000, change="Assumptions!B2")
    assert abs(_net_total(0.05, start=down.data["answer"]) - 150000) < 0.01


def test_goal_seek_refuses_what_has_no_answer(book, tmp_path):
    assert "is a formula; choose a typed-in cell" in _run(tmp_path, mode="goal_seek", cell="Monthly!E14", goal=1, change="Monthly!B5").error
    assert "typed in, not a formula" in _run(tmp_path, mode="goal_seek", cell="Assumptions!B2", goal=1, change="Assumptions!B3").error
    assert "never crosses" in _run(tmp_path, mode="goal_seek", cell="Monthly!E14", goal=10 ** 12, change="Assumptions!B3", low=0, high=0.1).error
    assert "goal_seek needs" in _run(tmp_path, mode="goal_seek", cell="Monthly!E14").error
    grid = Grid(book)
    try:
        assert grid.fresh is False and grid.overrides == {}
    finally:
        grid.close()


# ---------------------------------------------------------------------- tool --


def test_the_tool_reads_and_never_writes(book, tmp_path):
    tool = T.ExcelTraceTool()
    assert tool.reversibility.value == "readonly" and tool.returns_untrusted_content
    assert not _run(tmp_path, path="missing.xlsx", cell="A!A1").ok
    (tmp_path / "notes.txt").write_text("x")
    assert "reads .xlsx and .xlsm" in _run(tmp_path, path="notes.txt", cell="A!A1").error
    assert "mode is how, affects, what_if or goal_seek" in _run(tmp_path, mode="guess", cell="Monthly!B2").error


def test_a_workbook_too_large_to_load_is_refused(book, tmp_path, monkeypatch):
    monkeypatch.setattr("iron_jarvis.documents.excel_recalc.too_large", lambda p: "the workbook is too large to recalculate here")
    res = _run(tmp_path, cell="Monthly!B2")
    assert not res.ok and "too large" in res.error


def test_the_open_workbook_is_traced_through_a_live_path(book, tmp_path, monkeypatch):
    import shutil

    def fake(request, timeout):
        assert request["op"] == "snapshot" and request["workbook"] == "Client Budget.xlsx"
        out = Path(request["dir"]) / "Client Budget.xlsx"
        shutil.copy(book, out)
        return {"ok": True, "path": str(out), "workbook": "Client Budget.xlsx", "source": "Book1", "saved": False}

    monkeypatch.setattr(L, "_ask_worker", fake)
    res = _run(tmp_path, path="live:Client Budget.xlsx", mode="what_if", set={"Assumptions!B3": 0.08}, watch=["Monthly!B13"])
    assert res.ok and "in Client Budget.xlsx" in res.output and "97,928.8378762937" in res.output


# -------------------------------------------------------------------- arming --

TRACE = [
    "how is the December revenue calculated?", "where does this total come from", "what feeds the net income line",
    "what would revenue need to be to break even", "what if growth were 8%", "if I raise the price by 10% what happens to margin",
    "trace cell E14 for me", "why is E3 showing #REF", "which cells depend on the tax rate? what uses B5",
    "do a goal seek on the margin", "why does the total not tie to the ledger",
]
NOT_TRACE = [
    "summarize this workbook", "add a total row", "format the header bold", "what is in column B",
    "sort by amount", "make a chart of sales",
]


@pytest.mark.parametrize("text", TRACE)
def test_a_question_about_how_a_figure_is_built_arms_the_trace_beside_a_spreadsheet_tool(text):
    assert wants_excel_trace(text, ["excel_profile"]) and wants_excel_trace(text, ["excel_live_look"])
    assert not wants_excel_trace(text, ["read_file", "web_search"]), "never on its own: these are everyday words"


@pytest.mark.parametrize("text", NOT_TRACE)
def test_other_spreadsheet_work_does_not_arm_it(text):
    assert not wants_excel_trace(text, ["excel_profile", "excel_edit"])


def test_the_companions_of_the_look_tool():
    assert live_excel_companions("what if growth were 8%, and what is the total by region?") == ["excel_trace", "excel_query"]
    assert live_excel_companions("how many rows have Amount over 5000") == ["excel_query"]
    assert live_excel_companions("make the header bold") == []


def test_the_arming_words_are_linear_on_a_long_message():
    code = (
        "import time\n"
        "from iron_jarvis.tools.autoselect import wants_excel_trace, live_excel_companions, wants_live_excel\n"
        "t = time.perf_counter()\n"
        "for s in ('how is ' * 20000, 'what would ' * 12000, 'why is ' * 20000, 'where does x ' * 10000, 'if i ' * 26000, 'a' * 128000):\n"
        "    wants_excel_trace(s, ['excel_read']); live_excel_companions(s); wants_live_excel(s, True)\n"
        "print(round(time.perf_counter() - t, 2))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-600:]
    assert float(out.stdout.strip()) < 10


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    return SimpleNamespace(platform=build_platform(str(tmp_path_factory.mktemp("trace") / "home")))


def _armed(d, text, **kw):
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True, **kw)
    return list(CT._resolve_armed_tools(d, body)[0])


def test_chat_arms_the_trace_with_a_spreadsheet_and_with_the_open_workbook(d, monkeypatch):
    armed = _armed(d, "how is the total in this spreadsheet calculated?", attachments=["C:/x/budget.xlsx"])
    assert "excel_trace" in armed
    assert "excel_trace" not in _armed(d, "where does this error come from in my code?")
    monkeypatch.setenv(L.SEEN_ENV, "1")
    armed = _armed(d, "what if the growth rate in this sheet were 8%, and what is the total by region?")
    assert {"excel_live_look", "excel_trace", "excel_query"} <= set(armed)
    assert d.platform.permissions.mode_for("excel_trace").value == "allow"
    body = ChatBody(messages=[{"role": "user", "content": "what if growth were 8% in the open workbook"}], auto_tools=False)
    assert "excel_trace" not in CT._resolve_armed_tools(d, body)[0]


# ------------------------------------------------------- jump to the cell --


def test_a_cell_link_is_written_percent_encoded_and_reads_back():
    link = L.cell_link("Budget 2026.xlsx", "Q1 Sales", "B13")
    assert link == "#excel/Budget%202026.xlsx/Q1%20Sales/B13"
    hint = L.link_hint("Budget 2026.xlsx", "Q1 Sales", "$B$13")
    assert "[Q1 Sales!B13](#excel/Budget%202026.xlsx/Q1%20Sales/B13)" in hint and "shows that cell in Excel" in hint


@pytest.mark.parametrize(
    ("parts", "want"),
    [
        (("Budget.xlsx", "Data", "b4"), ("Budget.xlsx", "Data", "B4")),
        (("", "", "$A$1:$C$9"), ("", "", "A1:C9")),
        (("Budget.xlsx", "Data", "B"), None),            # not a cell
        (("Budget.xlsx", "Data", "B4; DROP"), None),
        (("Budget.xlsx", "a/b", "B4"), None),            # no sheet is called that
        (("Budget.xlsx", "x" * 32, "B4"), None),
        (("b\x00ook", "Data", "B4"), None),
        (("x" * 261, "Data", "B4"), None),
    ],
)
def test_only_a_workbook_a_sheet_and_a_cell_are_passed_on(parts, want):
    assert L.clean_cell_target(*parts) == want


def test_the_dashboard_reads_the_same_link_shape():
    ts = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "excelCellLink.ts").read_text(encoding="utf-8")
    assert f'EXCEL_CELL_PREFIX = "{L.CELL_LINK_PREFIX}"' in ts
    assert L.CELL_RX.pattern in ts, "the two cell patterns must stay the same"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    asked: list[dict] = []
    answer = {"ok": True, "workbook": "Budget.xlsx", "sheet": "Data", "address": "B4"}

    def fake(request, timeout):
        asked.append(request)
        return dict(answer)

    monkeypatch.setattr(L, "_ask_worker", fake)
    with TestClient(create_app(str(tmp_path / "home"))) as c:
        yield SimpleNamespace(http=c, asked=asked, answer=answer)


def test_the_route_shows_a_cell_and_passes_on_only_clean_parts(client):
    r = client.http.post("/excel/live/goto", json={"workbook": "Budget.xlsx", "sheet": "Data", "cell": "$b$4"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "workbook": "Budget.xlsx", "sheet": "Data", "address": "B4"}
    assert client.asked == [{"op": "goto", "workbook": "Budget.xlsx", "sheet": "Data", "cell": "B4"}]


def test_the_route_refuses_what_is_not_a_cell_before_excel_is_asked(client):
    for body in ({"workbook": "B.xlsx", "sheet": "Data", "cell": "=CMD()"}, {"workbook": "B.xlsx", "sheet": "a:b", "cell": "A1"}, {}):
        r = client.http.post("/excel/live/goto", json=body)
        assert r.status_code == 400 and "not a cell" in r.json()["detail"], r.text
    assert client.asked == []


def test_the_route_says_excels_own_refusal(client):
    client.answer.clear()
    client.answer.update({"ok": False, "code": "no_such_workbook", "error": "No open workbook is called 'Budget.xlsx'."})
    r = client.http.post("/excel/live/goto", json={"workbook": "Budget.xlsx", "sheet": "Data", "cell": "B4"})
    assert r.status_code == 409 and r.json()["detail"] == "No open workbook is called 'Budget.xlsx'."


def _look_fake(asked, goto_answer):
    def fake(request, timeout):
        asked.append(request)
        if request["op"] == "goto":
            return dict(goto_answer)
        return {"ok": True, "excel_open": True, "workbooks": [], "active": {},
                "range": {"workbook": "Budget.xlsx", "path": "", "sheet": "Data", "address": "A1:B2",
                          "rows": [["a", 1], ["b", 2]], "formulas": {}, "truncated": False, "total_rows": 2}}

    return fake


def test_a_look_says_how_to_link_a_cell_and_moves_excel_only_when_asked(tmp_path, monkeypatch):
    asked: list[dict] = []
    monkeypatch.setattr(L, "_ask_worker", _look_fake(asked, {"ok": True, "workbook": "Budget.xlsx", "sheet": "Data", "address": "A1:B2"}))
    tool = L.ExcelLiveLookTool()
    res = asyncio.run(tool.execute({"range": "A1:B2"}, _ctx(tmp_path)))
    assert res.ok and "[Data!A1](#excel/Budget.xlsx/Data/A1)" in res.output
    assert [r["op"] for r in asked] == ["look"], "a plain look never moves the user's Excel"
    res = asyncio.run(tool.execute({"range": "A1:B2", "show": True}, _ctx(tmp_path)))
    assert res.ok and "Excel now shows Data!A1:B2 selected." in res.output and res.data["shown"] is True
    assert asked[-1] == {"op": "goto", "workbook": "Budget.xlsx", "sheet": "Data", "cell": "A1:B2"}


def test_a_look_that_could_not_move_excel_says_so(tmp_path, monkeypatch):
    asked: list[dict] = []
    monkeypatch.setattr(L, "_ask_worker", _look_fake(asked, {"ok": False, "error": "the sheet 'Data' is hidden in Excel, so there is nothing to show. Unhide it first."}))
    res = asyncio.run(L.ExcelLiveLookTool().execute({"range": "A1:B2", "show": True}, _ctx(tmp_path)))
    assert res.ok and "Excel was NOT moved to that range: the sheet 'Data' is hidden" in res.output and res.data["shown"] is False


def test_a_trace_of_the_open_workbook_says_how_to_link_and_a_file_does_not(book, tmp_path, monkeypatch):
    import shutil

    def fake(request, timeout):
        out = Path(request["dir"]) / "Client Budget.xlsx"
        shutil.copy(book, out)
        return {"ok": True, "path": str(out), "workbook": "Client Budget.xlsx", "source": "Book1", "saved": False}

    monkeypatch.setattr(L, "_ask_worker", fake)
    live = _run(tmp_path, path="live:Client Budget.xlsx", cell="Monthly!E14")
    assert live.ok, live.error
    assert "[Monthly!E14](#excel/Client%20Budget.xlsx/Monthly/E14)" in live.output
    assert "#excel/" not in _run(tmp_path, cell="Monthly!E14").output, "a closed file has no cell to jump to"


def test_goal_seek_says_when_no_value_lands_on_the_goal(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "P"
    ws["A1"], ws["A2"] = 24.5, 1200
    ws["A3"] = "=ROUND(A1*0.95,2)*A2"
    wb.save(tmp_path / "step.xlsx")
    res = _run(tmp_path, path="step.xlsx", mode="goal_seek", cell="P!A3", goal=30000.5, change="P!A1")
    assert res.ok, res.error
    assert res.data["exact"] is False and "NOT EXACT" in res.output and "the closest" in res.output
    assert abs(res.data["reaches"] - 30000.5) <= 12
    smooth = _run(tmp_path, path="step.xlsx", mode="goal_seek", cell="P!A3", goal=30000, change="P!A2")
    assert smooth.data["exact"] is True and "NOT EXACT" not in smooth.output


@pytest.mark.parametrize(
    "text",
    [
        # the first live trial's own words: a cell is written Calc!B8 and a
        # file Pricing.xlsx, and the patterns stopped at "!" and "."
        r"In the workbook C:\work\Pricing.xlsx, how is the profit in Calc!B8 worked out?",
        "And what would the profit be if the price were 31.99 instead?",
        "what would the fixed costs (Inputs!B6) have to be for the profit in Calc!B8 to come to exactly 20,000?",
        "why is Calc!B8 showing #REF! after the change?",
        "where does the 4,620.90 in Summary!C4 come from?",
    ],
)
def test_a_question_that_names_a_cell_or_a_file_still_arms_the_trace(text):
    assert wants_excel_trace(text, ["excel_read"]) is True
    assert wants_excel_trace(text, []) is False


def test_goal_seek_gives_the_round_figure_when_one_lands_on_the_goal(tmp_path):
    """The first live trial read "-4,885.28000051156" where the true value is
    -4,885.28: the search stops a hair away, and the roundest value that still
    lands on the goal is the one to show."""
    wb = Workbook()
    ws = wb.active
    ws.title = "P"
    ws["A1"], ws["A2"], ws["A3"] = 1250, 41.5, 12114.72
    ws["A4"] = "=A1*A2-A3-37000"
    wb.save(tmp_path / "round.xlsx")
    res = _run(tmp_path, path="round.xlsx", mode="goal_seek", cell="P!A4", goal=20000, change="P!A3")
    assert res.ok, res.error
    assert res.data["answer"] == -5125.0
    assert res.data["exact"] is True and "NOT EXACT" not in res.output
    assert "P!A3 must be -5,125 " in res.output
    cents = _run(tmp_path, path="round.xlsx", mode="goal_seek", cell="P!A4", goal=2760.28, change="P!A3")
    assert cents.data["answer"] == 12114.72 or abs(cents.data["answer"] - 12114.72) < 1e-9
    assert "12,114.72 " in cents.output
    # a value with no round form keeps its digits (1/3 is not rounded to 0.33)
    ws["A4"] = "=A3*3"
    wb.save(tmp_path / "third.xlsx")
    third = _run(tmp_path, path="third.xlsx", mode="goal_seek", cell="P!A4", goal=1, change="P!A3")
    assert abs(third.data["answer"] - 1 / 3) < 1e-9
