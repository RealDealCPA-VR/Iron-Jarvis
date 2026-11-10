"""A link to ANOTHER WORKBOOK is written only when Jarvis may read it (v1.469.1).

The owner's decision of 2026-11-10. Linking workbooks is everyday accounting,
so a link stays an ordinary formula (the first review pinned that and it still
holds: ``reaches_out`` says nothing about it). The one exception: the workbook
the link names is in a folder Jarvis may not read ("Folders Jarvis may read",
the installation's own limit, or one of Iron Jarvis's protected folders).
Excel would read that file itself and put its figures into this sheet, so a
note in a client's workbook could ask for another client's numbers.

* ``excel_worker.workbook_links`` only READS the links a formula holds.
* ``excel_formula.link_reason`` judges them; ``for_file`` (every formula
  written to a saved file) and ``excel_live.link_refusal`` (every step for the
  open workbook) refuse on its answer, before anything is changed.
* With no folder limit set, nothing changes at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from iron_jarvis import excel_worker as W
from iron_jarvis.core import fs_policy
from iron_jarvis.documents import excel_formula as XF
from iron_jarvis.documents import excel_live as L
from iron_jarvis.documents.excel_tools import ExcelEditTool
from iron_jarvis.tools.base import ToolContext

ENV = "IRONJARVIS_FS_ALLOWLIST"


@pytest.fixture(autouse=True)
def _no_limit_unless_a_test_sets_one(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    fs_policy.use_config(None)
    yield
    fs_policy.use_config(None)


@pytest.fixture()
def folders(tmp_path) -> "tuple[Path, Path]":
    allowed, other = tmp_path / "Clients" / "Smith", tmp_path / "Clients" / "Jones"
    allowed.mkdir(parents=True)
    other.mkdir(parents=True)
    return allowed, other


def _limit(monkeypatch, *roots: Path) -> None:
    monkeypatch.setenv(ENV, ",".join(str(r) for r in roots))


def _link(folder: Path, name: str = "Books.xlsx", sheet: str = "Summary") -> str:
    return f"='{folder}\\[{name}]{sheet}'!B4"


# ------------------------------------------------------------ reading links --

LINKS = [
    (r"='C:\Clients\Smith\[Smith 2025.xlsx]Summary'!B4", [("C:\\Clients\\Smith\\", "Smith 2025.xlsx", False)]),
    ("=[Budget.xlsx]Sheet1!A1", [("", "Budget.xlsx", False)]),
    ("=SUM([other.xlsm]Data!A1:A9)+1", [("", "other.xlsm", False)]),
    (r"='C:\Clients\book.xlsx'!Total", [("C:\\Clients\\", "book.xlsx", False)]),
    ("='book.xlsx'!Total", [("", "book.xlsx", False)]),
    ("=[1]Costs!C2", [("", "1", True)]),
    ("='[2]My Sheet'!$B$2+1", [("", "2", True)]),
    ("=VLOOKUP(A1,'[rates.csv]rates'!A:B,2,0)", [("", "rates.csv", False)]),
    (r"='..\Jones\[Books.xlsx]S'!A1", [("..\\Jones\\", "Books.xlsx", False)]),
    ("='//host/share/[b.xlsx]S'!A1", [("//host/share/", "b.xlsx", False)]),
    (r"=C:\dir\[b.xlsx]S!A1", [("C:\\dir\\", "b.xlsx", False)]),
    (r"='C:\it''s\[b.xlsx]S'!A1", [("C:\\it's\\", "b.xlsx", False)]),
    (r"='C:\a\[x.xlsx]S'!A1+'D:\b\[y.xlsx]T'!A1", [("C:\\a\\", "x.xlsx", False), ("D:\\b\\", "y.xlsx", False)]),
    ("+[Budget.xlsx]Sheet1!A1", [("", "Budget.xlsx", False)]),
]

NOT_LINKS = [
    "=SUM(Table1[Amount])",
    "=[@Amount]*2",
    "=Table1[[#This Row],[Amount]]+Table1[[#Totals],[Fee]]",
    "=SUM(Costs!C2:C9)",
    "='Q1 costs'!C2+'Q2 costs'!C2",
    "=SUM('Jan:Dec'!A1)",
    '=IF(A1="see [book.xlsx]Sheet1!A1","x","y")',
    "=\"'C:\\dir\\[b.xlsx]S'!A1\"",
    '=A1&".xlsx!"',
    "=Sheet1!A1",
    "=INDEX(Table1[1],2)",
    "=A1/B1",
    "see [book.xlsx]Sheet1!A1",
    "",
    None,
    12,
]


@pytest.mark.parametrize("formula,want", LINKS)
def test_the_links_a_formula_holds_are_read(formula, want):
    got = [(ln["dir"], ln["file"], ln["index"]) for ln in W.workbook_links(formula)]
    assert got == want


@pytest.mark.parametrize("entry", NOT_LINKS)
def test_a_table_column_a_sheet_or_quoted_text_is_not_a_link(entry):
    assert W.workbook_links(entry) == []


def test_a_link_is_still_no_outside_call_by_itself():
    """The first review's pin: linking workbooks is ordinary. The folder
    question is asked separately (link_reason), never by reaches_out."""
    for formula, _ in LINKS:
        if "//" in formula:
            continue  # a network spelling is an outside call of its own
        assert W.reaches_out(formula) == "", formula


def test_only_so_many_links_are_read():
    many = "=" + "+".join(f"[b{i}.xlsx]S!A1" for i in range(400))
    assert len(W.workbook_links(many)) == W.MAX_LINKS


def test_reading_links_is_linear():
    code = (
        "import time\n"
        "from iron_jarvis.excel_worker import workbook_links as w\n"
        "bad = ['=' + '[' * 128000, '=' + '[a.' * 42000, '=' + '[1]' * 42000, '=' + '.xl' * 42000,\n"
        "       '=' + '[' + 'a' * 128000, '=' + \"'\" * 128000, '=' + \"'[\" * 64000, '=' + '[a]b' * 32000,\n"
        "       '=' + 'x' * 127000 + '[b.xlsx]S!A1', '=' + chr(92) * 128000 + '[b.xlsx]S!A1']\n"
        "t = time.perf_counter()\n"
        "for b in bad:\n"
        "    w(b)\n"
        "print(round(time.perf_counter() - t, 2))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-600:]
    assert float(out.stdout.strip()) < 20


# ------------------------------------------------------------ judging links --


def test_with_no_folder_limit_every_link_is_ordinary(folders):
    allowed, other = folders
    for formula in (_link(allowed), _link(other), "=[Budget.xlsx]S!A1", "=[1]S!A1", r"='..\x\[b.xlsx]S'!A1"):
        assert XF.link_reason(formula) == "", formula
        assert XF.for_file(formula) == formula


def test_a_link_into_a_folder_jarvis_may_not_read_is_refused(monkeypatch, folders):
    allowed, other = folders
    _limit(monkeypatch, allowed)
    assert XF.link_reason(_link(allowed)) == ""
    assert XF.for_file(_link(allowed)) == _link(allowed)
    why = XF.link_reason(_link(other))
    assert why == "it links to Books.xlsx, a workbook in a folder Jarvis may not read"
    with pytest.raises(XF.OutsideFormula) as err:
        XF.for_file(_link(other))
    assert "not written from chat" in str(err.value) and "may not read" in str(err.value)
    assert str(other) not in str(err.value), "the refusal names the file, never the folder it is in"
    # a name defined in another workbook, and the second of two links
    assert "may not read" in XF.link_reason(f"='{other}\\Books.xlsx'!Total")
    assert "may not read" in XF.link_reason(f"={_link(allowed)[1:]}+{_link(other)[1:]}")


def test_the_users_own_setting_limits_links_too(folders, tmp_path):
    allowed, other = folders
    class Settings:  # the policy holds its config weakly, so a plain class
        readable_folders = [str(allowed)]
        home = str(tmp_path / "home")
        chat_files_dir = str(tmp_path / "chat")
        chat_files_root = ""

    cfg = Settings()
    fs_policy.use_config(cfg)
    assert fs_policy.read_limits_set() is True
    assert XF.link_reason(_link(allowed)) == ""
    assert "may not read" in XF.link_reason(_link(other))
    cfg.readable_folders = []
    assert fs_policy.read_limits_set() is False
    assert XF.link_reason(_link(other)) == ""


def test_a_link_into_a_protected_folder_is_refused_with_no_limit_set(monkeypatch, folders):
    _, other = folders
    monkeypatch.setattr(fs_policy, "_PROTECTED_ROOTS", {fs_policy._canonical(other)})
    assert "may not read" in XF.link_reason(_link(other))
    with pytest.raises(XF.OutsideFormula):
        XF.for_file(_link(other))


def test_a_link_whose_folder_cannot_be_worked_out_is_refused_only_under_a_limit(monkeypatch, folders):
    allowed, _ = folders
    unknown = ["=[1]Costs!C2", "='[2]My Sheet'!B2", r"='..\Jones\[Books.xlsx]S'!A1", r"='sub\[b.xlsx]S'!A1"]
    for formula in unknown:
        assert XF.link_reason(formula) == ""
    _limit(monkeypatch, allowed)
    for formula in unknown:
        assert XF.link_reason(formula) == XF.LINK_UNCHECKED, formula
    # a file name alone is a workbook beside the saved file: ordinary
    assert XF.link_reason("=[Budget.xlsx]Sheet1!A1") == ""
    assert XF.for_file("=[Budget.xlsx]Sheet1!A1") == "=[Budget.xlsx]Sheet1!A1"


def test_a_network_spelling_is_refused_by_its_text_before_anything_is_looked_up(monkeypatch):
    def boom(path):
        raise AssertionError(f"looked up {path}")

    monkeypatch.setattr(fs_policy, "fs_read_ok", boom)
    monkeypatch.setattr(fs_policy, "read_limits_set", lambda: (_ for _ in ()).throw(AssertionError("asked")))
    assert XF.link_reason("='//host/share/[b.xlsx]S'!A1") == XF.LINK_NETWORK
    assert XF.link_reason("='//host/share/b.xlsx'!Total") == XF.LINK_NETWORK


def test_a_check_that_fails_is_a_refusal(monkeypatch, folders):
    _, other = folders

    def boom(path):
        raise OSError("disk went away")

    monkeypatch.setattr(fs_policy, "fs_read_ok", boom)
    assert "may not read" in XF.link_reason(_link(other))
    monkeypatch.setattr(fs_policy, "read_limits_set", boom)
    assert XF.link_reason("=[1]S!A1") == XF.LINK_UNCHECKED


def test_a_file_name_alone_is_judged_by_where_that_workbook_is_open_from(monkeypatch, folders):
    allowed, other = folders
    _limit(monkeypatch, allowed)
    formula = "=[Jones.xlsx]Summary!B4"
    assert XF.link_reason(formula, open_books={"jones.xlsx": str(other / "Jones.xlsx")}) == \
        "it links to Jones.xlsx, a workbook in a folder Jarvis may not read"
    assert XF.link_reason(formula, open_books={"jones.xlsx": str(allowed / "Jones.xlsx")}) == ""
    assert XF.link_reason(formula, open_books={"smith.xlsx": str(other / "Smith.xlsx")}) == "", "not open: nothing to judge"
    assert XF.link_reason(formula, open_books={"jones.xlsx": ""}) == "", "never saved: no folder"
    assert XF.link_reason(formula, open_books={"jones.xlsx": "https://d.docs.live.net/x/Jones.xlsx"}) == ""
    assert "may not read" in XF.link_reason("='jones.xlsx'!Total", open_books={"jones.xlsx": str(other / "Jones.xlsx")})


# --------------------------------------------------------------- saved files --


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


def _book(folder: Path) -> Path:
    wb = Workbook()
    wb.active.title = "Data"
    wb.active["A1"] = 5
    path = folder / "b.xlsx"
    wb.save(path)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_edit_tool_writes_a_link_jarvis_may_read_and_refuses_one_it_may_not(monkeypatch, folders):
    allowed, other = folders
    book = _book(allowed)
    _limit(monkeypatch, allowed)
    before = _sha(book)
    res = asyncio.run(ExcelEditTool().execute(
        {"path": "b.xlsx", "edits": [{"sheet": "Data", "cell": "B1", "formula": _link(other)}]}, _ctx(allowed)))
    assert not res.ok and "may not read" in (res.error or "") and "Books.xlsx" in (res.error or "")
    assert _sha(book) == before, "a refused link leaves the file byte for byte as it was"
    # a text value that starts with "=" is a formula too
    res = asyncio.run(ExcelEditTool().execute(
        {"path": "b.xlsx", "edits": [{"sheet": "Data", "cell": "B1", "value": _link(other)}]}, _ctx(allowed)))
    assert not res.ok and _sha(book) == before
    res = asyncio.run(ExcelEditTool().execute(
        {"path": "b.xlsx", "edits": [{"sheet": "Data", "cell": "B1", "formula": _link(allowed, "Smith.xlsx")}]}, _ctx(allowed)))
    assert res.ok, res.error
    assert load_workbook(book)["Data"]["B1"].value == _link(allowed, "Smith.xlsx")


def test_a_colour_rule_cannot_read_another_workbook_jarvis_may_not_read(monkeypatch, folders):
    allowed, other = folders
    book = _book(allowed)
    _limit(monkeypatch, allowed)
    before = _sha(book)
    rule = {"op": "conditional_format", "range": "Data!A1:A9", "rule": "formula", "formula": "=A1>" + _link(other)[1:], "fill": "DDEBF7"}
    res = asyncio.run(ExcelEditTool().execute({"path": "b.xlsx", "features": [rule]}, _ctx(allowed)))
    assert not res.ok and "may not read" in (res.error or "") and "Nothing was changed" in (res.error or "")
    assert _sha(book) == before
    # a name that points into another workbook is refused too (for its own reason), and nothing is saved
    res = asyncio.run(ExcelEditTool().execute(
        {"path": "b.xlsx", "features": [{"op": "name", "name": "Theirs", "refers_to": _link(other)[1:]}]}, _ctx(allowed)))
    assert not res.ok and _sha(book) == before


# ---------------------------------------------------------- the open workbook --


class FakeExcel:
    def __init__(self, books: "dict[str, str] | None" = None) -> None:
        self.asked: list[dict] = []
        self.books = books or {}
        self.look_ok = True

    def __call__(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        op = request["op"]
        if op == "plan":
            return {"ok": True, "workbook": "Smith.xlsx", "path": "", "steps": [{"op": "set"}], "overwrites": 0,
                    "formulas_lost": 0, "destructive": []}
        if op == "look":
            if not self.look_ok:
                return {"ok": False, "code": "busy", "error": "Excel is busy."}
            return {"ok": True, "excel_open": True, "workbooks": [{"name": n, "path": p} for n, p in self.books.items()]}
        if op == "apply":
            Path(request["undo_path"]).write_text("{}\n", encoding="utf-8")
            return {"ok": True, "workbook": "Smith.xlsx", "path": "", "steps": [{"op": "set", "summary": "write 1 cell"}],
                    "changes": [{"sheet": "Data", "cell": "B1", "before": None, "after": 1}], "changed_cells": 1}
        raise AssertionError(op)

    def ops(self) -> "list[str]":
        return [r["op"] for r in self.asked]


def _live(monkeypatch, tmp_path, ops, books=None, look_ok=True):
    fake = FakeExcel(books)
    fake.look_ok = look_ok
    monkeypatch.setattr(L, "_ask_worker", fake)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    ctx = ToolContext(workspace=home / "ws", session_id="chat", agent_run_id="r", config=SimpleNamespace(home=home),
                      event_bus=None, engine=None)
    res = asyncio.run(L.ExcelLiveEditTool().execute({"workbook": "Smith.xlsx", "ops": ops}, ctx))
    return res, fake


def _set(**kw) -> "list[dict]":
    return [{"op": "set", "sheet": "Data", "cell": "B1", **kw}]


def test_steps_with_no_link_ask_excel_nothing_more(monkeypatch, tmp_path, folders):
    _limit(monkeypatch, folders[0])
    res, fake = _live(monkeypatch, tmp_path, _set(formula="=SUM(A1:A9)+Table1[[#Totals],[Fee]]"))
    assert res.ok, res.error
    assert fake.ops() == ["plan", "apply"]


def test_a_link_with_its_folder_is_judged_without_asking_excel(monkeypatch, tmp_path, folders):
    allowed, other = folders
    _limit(monkeypatch, allowed)
    res, fake = _live(monkeypatch, tmp_path, _set(formula=_link(other)))
    assert not res.ok and res.error.startswith(L.UNCHANGED_LEAD)
    assert "it links to Books.xlsx, a workbook in a folder Jarvis may not read" in res.error
    assert "apply" not in fake.ops() and "look" not in fake.ops()
    res, fake = _live(monkeypatch, tmp_path, _set(formula=_link(allowed)))
    assert res.ok and fake.ops() == ["plan", "apply"]


def test_a_formula_written_without_its_equals_sign_is_judged_too(monkeypatch, tmp_path, folders):
    allowed, other = folders
    _limit(monkeypatch, allowed)
    for ops in (_set(formula=_link(other)[1:]),
                [{"op": "name", "name": "Theirs", "refers_to": _link(other)[1:]}],
                [{"op": "validation", "range": "Data!A1:A9", "source": _link(other)[1:]}],
                [{"op": "set_range", "range": "Data!A1:B1", "values": [[1, _link(other)]]}]):
        res, fake = _live(monkeypatch, tmp_path, ops)
        assert not res.ok and "may not read" in res.error, ops
        assert "apply" not in fake.ops()


def test_text_that_only_mentions_a_link_is_written(monkeypatch, tmp_path, folders):
    allowed, other = folders
    _limit(monkeypatch, allowed)
    res, fake = _live(monkeypatch, tmp_path, _set(value=f"see {_link(other)[1:]}") + [
        {"op": "note", "cell": "Data!B1", "text": _link(other)[1:]}])
    assert res.ok, res.error
    assert fake.ops() == ["plan", "apply"]


def test_a_link_by_name_is_judged_by_where_excel_has_that_workbook_open_from(monkeypatch, tmp_path, folders):
    allowed, other = folders
    _limit(monkeypatch, allowed)
    ops = _set(formula="=[Jones.xlsx]Summary!B4")
    res, fake = _live(monkeypatch, tmp_path, ops, books={"Smith.xlsx": str(allowed / "Smith.xlsx"), "Jones.xlsx": str(other / "Jones.xlsx")})
    assert not res.ok and "it links to Jones.xlsx, a workbook in a folder Jarvis may not read" in res.error
    assert fake.ops() == ["plan", "look"]
    assert fake.asked[1] == {"op": "look"}, "names and places only: no range is read"
    res, fake = _live(monkeypatch, tmp_path, ops, books={"Jones.xlsx": str(allowed / "Jones.xlsx")})
    assert res.ok and fake.ops() == ["plan", "look", "apply"]
    res, fake = _live(monkeypatch, tmp_path, ops, books={"Smith.xlsx": str(allowed / "Smith.xlsx")})
    assert res.ok, "a workbook that is not open has no place to judge"


def test_when_excel_does_not_say_where_its_workbooks_are_a_limit_refuses(monkeypatch, tmp_path, folders):
    allowed, _ = folders
    ops = _set(formula="=[Jones.xlsx]Summary!B4")
    res, fake = _live(monkeypatch, tmp_path, ops, look_ok=False)
    assert res.ok, "no limit set: a link is ordinary"
    _limit(monkeypatch, allowed)
    res, fake = _live(monkeypatch, tmp_path, ops, look_ok=False)
    assert not res.ok and XF.LINK_UNCHECKED in res.error and "apply" not in fake.ops()


def test_indirect_is_refused_while_a_workbook_jarvis_may_not_read_is_open(monkeypatch, tmp_path, folders):
    """INDIRECT builds its reference from text, so no reader of links can see
    where it points. Measured in the real Excel: =INDIRECT("[Jones.xlsx]
    Summary!B4") shows the open workbook's figure."""
    allowed, other = folders
    _limit(monkeypatch, allowed)
    mixed = {"Smith.xlsx": str(allowed / "Smith.xlsx"), "Jones.xlsx": str(other / "Jones.xlsx"), "Book1": ""}
    for formula in ('=INDIRECT("[Jones.xlsx]Summary!B4")', "=indirect(E1)", "=SUM(A1, Indirect (E1))", "INDIRECT(E1)"):
        res, fake = _live(monkeypatch, tmp_path, _set(formula=formula), books=mixed)
        assert not res.ok and res.error.startswith(L.UNCHANGED_LEAD), formula
        assert "INDIRECT could read Jones.xlsx, open in Excel from a folder Jarvis may not read" in res.error
        assert fake.ops() == ["plan", "look"]
    # every open workbook is one Jarvis may read: INDIRECT is ordinary
    fine = {"Smith.xlsx": str(allowed / "Smith.xlsx"), "Partner.xlsx": str(allowed / "Partner.xlsx"), "Book1": ""}
    res, fake = _live(monkeypatch, tmp_path, _set(formula="=INDIRECT(E1)"), books=fine)
    assert res.ok and fake.ops() == ["plan", "look", "apply"]
    # Excel did not say what is open: not a pass
    res, fake = _live(monkeypatch, tmp_path, _set(formula="=INDIRECT(E1)"), books=fine, look_ok=False)
    assert not res.ok and XF.LINK_UNCHECKED in res.error and "apply" not in fake.ops()


def test_the_word_indirect_in_text_or_another_functions_name_is_not_a_call(monkeypatch, tmp_path, folders):
    allowed, other = folders
    _limit(monkeypatch, allowed)
    mixed = {"Jones.xlsx": str(other / "Jones.xlsx")}
    for formula in ('="see INDIRECT(E1)"', "=MYINDIRECT(E1)", "=A1+INDIRECTCOSTS", "=Indirect!A1"):
        res, fake = _live(monkeypatch, tmp_path, _set(formula=formula), books=mixed)
        assert res.ok and fake.ops() == ["plan", "apply"], formula
    assert L.uses_indirect("=INDIRECT(A1)") and not L.uses_indirect("=A1") and not L.uses_indirect('="INDIRECT("')


def test_with_no_folder_limit_indirect_asks_excel_nothing(monkeypatch, tmp_path, folders):
    _, other = folders
    res, fake = _live(monkeypatch, tmp_path, _set(formula='=INDIRECT("[Jones.xlsx]Summary!B4")'),
                      books={"Jones.xlsx": str(other / "Jones.xlsx")})
    assert res.ok and fake.ops() == ["plan", "apply"]


def test_reading_for_indirect_is_linear():
    code = (
        "import time\n"
        "from iron_jarvis.documents.excel_live import uses_indirect as u\n"
        "bad = ['=' + 'INDIRECT ' * 14000, '=' + '\"' * 128000, '=' + \"'\" * 128000, '=' + '(' * 128000,\n"
        "       '=' + 'INDIRECT' + ' ' * 127000 + 'x(']\n"
        "t = time.perf_counter()\n"
        "for b in bad:\n"
        "    u(b)\n"
        "print(round(time.perf_counter() - t, 2))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-600:]
    assert float(out.stdout.strip()) < 20


def test_with_no_limit_the_open_workbook_takes_a_link_as_before(monkeypatch, tmp_path, folders):
    _, other = folders
    res, fake = _live(monkeypatch, tmp_path, _set(formula=_link(other)))
    assert res.ok and fake.ops() == ["plan", "apply"]


def test_a_check_that_breaks_refuses_the_change(monkeypatch, tmp_path, folders):
    def boom(e):
        raise RuntimeError("x")

    monkeypatch.setattr(W, "workbook_links", boom)
    res, fake = _live(monkeypatch, tmp_path, _set(formula="=A1+1"))
    assert not res.ok and "could not check" in res.error and "apply" not in fake.ops()


def test_the_steps_formula_entries_are_bounded_and_read_by_field():
    ops = [{"op": "set", "cell": "B1", "value": "plain", "formula": "A1+1"},
           {"op": "set_range", "values": [["=A1", "text", 3, "-5"]]},
           {"op": "note", "text": "hello"}]
    got = L.formula_entries(ops)
    assert "=A1+1" in got and "=A1" in got and "-5" in got
    assert "=plain" not in got and "=text" not in got and "=hello" not in got
    big = [{"op": "set_range", "values": [["=A1"] * 100] * 100}]
    assert len(L.formula_entries(big)) == L.MAX_STEP_ENTRIES
