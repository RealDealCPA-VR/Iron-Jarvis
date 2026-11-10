"""Matching two lists of transactions (v1.469.0, documents/excel_reconcile).

The promises checked here:

* amounts are compared in whole cents, the way Excel shows them;
* every row is matched at most once, and the left-over rows ALWAYS explain
  the whole difference between the two totals (checked on random lists);
* the date pass finds as many pairs as can be found (checked against a
  brute-force answer on small lists);
* references, the date window, the tolerance, near misses, grouped deposits
  and opposite signs each behave as described;
* the tool reads two workbooks, writes only a NEW result file, and undoing
  removes it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.documents import excel_reconcile as R
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.autoselect import wants_excel_reconcile
from iron_jarvis.tools.base import ToolContext

D = dt.date


def _side(name: str, rows, *, dated=True, refs=False) -> R.Side:
    """rows: (cents, date|None, ref) tuples, numbered from sheet row 2."""
    side = R.Side(name=name, sheet=name, has_date=dated, has_ref=refs)
    for i, row in enumerate(rows):
        cents, date, ref = (list(row) + [None, ""])[:3]
        side.rows.append(R.Txn(row=i + 2, cents=cents, date=date, ref=R.norm_ref(ref)))
    return side


# -------------------------------------------------------------------- cents --


@pytest.mark.parametrize(
    ("cell", "cents"),
    [
        (12.5, 1250), (0.1 + 0.2, 30), (2.675, 268), (-2.675, -268), (1e15, 10 ** 17), (7, 700),
        # a half cent goes AWAY from zero (never to the even cent) ...
        (2.665, 267), (0.125, 13), (-0.125, -13),
        # ... and is judged on the 15 digits Excel shows: this sum is stored
        # as 99.99499999999999, which Excel shows, and rounds, as 99.995.
        (33.33 * 3 + 0.005, 10000),
        ("1,234.50", 123450), ("(45.00)", -4500), ("$12", 1200), ("15.00-", -1500), (" 3.999 ", 400),
        (None, None), ("", None), ("n/a", None), (True, None), (float("nan"), None), (float("inf"), None),
    ],
)
def test_an_amount_is_read_as_whole_cents(cell, cents):
    assert R.to_cents(cell) == cents


def test_money_and_references_read_the_way_people_write_them():
    assert R.money(123456) == "1,234.56" and R.money(-5) == "-0.05" and R.money(0) == "0.00"
    assert R.norm_ref(" #001002 ") == "1002" and R.norm_ref(1002.0) == "1002" and R.norm_ref("INV-7 a") == "inv7a"
    assert R.norm_ref(None) == "" and R.norm_ref("000") == "0"


# ----------------------------------------------------------------- matching --


def test_a_reference_wins_over_the_nearest_date():
    a = _side("Bank", [(-8000, D(2026, 10, 5), "1002")], refs=True)
    b = _side("Books", [(-8000, D(2026, 10, 5), "1009"), (-8000, D(2026, 9, 1), "#1002")], refs=True)
    res = R.match(a, b)
    assert [(m.a.row, m.b.row, m.how) for m in res.matches] == [(2, 3, "reference")]
    assert [t.row for t in res.only_b] == [2]


def test_dates_outside_the_window_are_not_matched_and_are_listed_as_near_misses():
    a = _side("Bank", [(30000, D(2026, 10, 20))])
    b = _side("Books", [(30000, D(2026, 10, 1))])
    res = R.match(a, b, days=5)
    assert res.matches == [] and len(res.only_a) == 1 and len(res.only_b) == 1
    assert [(x.row, y.row, gap) for x, y, gap in res.near] == [(2, 2, 19)]
    assert len(R.match(a, b, days=19).matches) == 1, "the window is inclusive"


def test_lists_with_no_dates_match_by_amount_in_row_order():
    a = _side("A", [(100,), (100,), (250,)], dated=False)
    b = _side("B", [(250,), (100,)], dated=False)
    res = R.match(a, b)
    assert sorted((m.a.row, m.b.row, m.how) for m in res.matches) == [(2, 3, "amount"), (4, 2, "amount")]
    assert [t.row for t in res.only_a] == [3] and res.only_b == []


def test_the_tolerance_matches_close_amounts_and_says_the_difference():
    a = _side("Bank", [(-9999, D(2026, 10, 28)), (5000, D(2026, 10, 2))])
    b = _side("Books", [(-10000, D(2026, 10, 27)), (5010, D(2026, 10, 2))])
    assert R.match(a, b).matches == [], "with no tolerance a cent is a difference"
    res = R.match(a, b, tolerance_cents=5)
    assert [(m.a.row, m.b.row, m.how, m.diff_cents) for m in res.matches] == [(2, 2, "tolerance", 1)]
    assert [t.row for t in res.only_a] == [3] and [t.row for t in res.only_b] == [3]


def test_a_deposit_of_several_items_is_offered_as_a_group_never_matched():
    a = _side("Bank", [(120000, D(2026, 10, 9))])
    b = _side("Books", [(70000, D(2026, 10, 8)), (50000, D(2026, 10, 8)), (1500, D(2026, 10, 8))])
    res = R.match(a, b)
    assert res.matches == []
    assert res.groups == [] , "three rows of that day sum to 1,215.00, not 1,200.00"
    b2 = _side("Books", [(70000, D(2026, 10, 8)), (50000, D(2026, 10, 8))])
    res = R.match(a, b2)
    assert res.matches == [] and len(res.only_a) == 1
    assert [(side, x.row, [r.row for r in rows]) for side, x, rows in res.groups] == [("a", 2, [2, 3])]


def test_opposite_signs_are_noticed_not_guessed():
    a = _side("Bank", [(c, D(2026, 10, i + 1)) for i, c in enumerate((1000, 2500, -400, 9900, 120))])
    b = _side("Books", [(-c, D(2026, 10, i + 1)) for i, c in enumerate((1000, 2500, -400, 9900, 120))])
    res = R.match(a, b)
    assert res.matches == [] and res.opposite_signs is True
    same = _side("Books", [(c, D(2026, 10, i + 1)) for i, c in enumerate((1000, 2500, -400, 9900, 120))])
    assert R.match(a, same).opposite_signs is False


def _brute_force_most_pairs(xs, ys, days):
    best = 0
    small, large = (xs, ys) if len(xs) <= len(ys) else (ys, xs)
    for k in range(len(small), 0, -1):
        if k <= best:
            break
        for chosen in itertools.permutations(range(len(large)), k):
            for subset in itertools.combinations(range(len(small)), k):
                if all(abs((small[i].date - large[j].date).days) <= days for i, j in zip(subset, chosen)):
                    best = max(best, k)
                    break
            if best == k:
                break
    return best


def test_the_date_pass_finds_as_many_pairs_as_can_be_found():
    rng = random.Random(1469)
    for _ in range(120):
        days = rng.randint(0, 4)
        xs = [R.Txn(row=i + 2, cents=500, date=D(2026, 10, 1) + dt.timedelta(days=rng.randint(0, 12))) for i in range(rng.randint(1, 5))]
        ys = [R.Txn(row=i + 2, cents=500, date=D(2026, 10, 1) + dt.timedelta(days=rng.randint(0, 12))) for i in range(rng.randint(1, 5))]
        got = R._walk(R._by_date(xs), R._by_date(ys), days)
        assert len(got) == _brute_force_most_pairs(xs, ys, days), (days, [t.date for t in xs], [t.date for t in ys])
        assert all(abs((a.date - b.date).days) <= days for a, b in got)


def test_the_left_over_rows_always_explain_the_whole_difference():
    """The promise the answer prints ("It ties") must hold whatever the lists."""
    rng = random.Random(20261110)
    amounts = [100, 250, 250, 999, 1000, 1001, 5000, -400, -400, 12345]
    for case in range(300):
        dated, refs = rng.random() < 0.8, rng.random() < 0.5

        def rows(n):
            return [(rng.choice(amounts), D(2026, 10, rng.randint(1, 28)) if dated else None,
                     str(rng.randint(1, 6)) if refs and rng.random() < 0.6 else "") for _ in range(n)]

        a, b = _side("A", rows(rng.randint(0, 25)), dated=dated, refs=refs), _side("B", rows(rng.randint(0, 25)), dated=dated, refs=refs)
        res = R.match(a, b, days=rng.randint(0, 6), tolerance_cents=rng.choice((0, 0, 1, 5)))
        t = R.totals(a, b, res)
        assert t["difference"] == t["explained"], case
        used_a, used_b = [m.a.row for m in res.matches], [m.b.row for m in res.matches]
        assert len(set(used_a)) == len(used_a) and len(set(used_b)) == len(used_b), "a row is matched at most once"
        assert sorted(used_a + [x.row for x in res.only_a]) == sorted(x.row for x in a.rows), "no row is lost or invented"
        assert sorted(used_b + [x.row for x in res.only_b]) == sorted(x.row for x in b.rows)
        for m in res.matches:
            assert (m.a.cents == m.b.cents) if m.how != "tolerance" else (0 < abs(m.a.cents - m.b.cents))


def test_many_rows_of_one_amount_are_matched_in_good_time():
    code = (
        "import time, datetime as dt\n"
        "from iron_jarvis.documents import excel_reconcile as R\n"
        "def side(n, shift):\n"
        "    s = R.Side(name='x', sheet='x', has_date=True)\n"
        "    s.rows = [R.Txn(row=i + 2, cents=1000, date=dt.date(2026, 1, 1) + dt.timedelta(days=(i + shift) % 300)) for i in range(n)]\n"
        "    return s\n"
        "t = time.perf_counter(); res = R.match(side(40000, 0), side(40000, 3), days=2)\n"
        "print(len(res.matches), round(time.perf_counter() - t, 2))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-600:]
    matched, seconds = out.stdout.split()
    assert int(matched) > 39000 and float(seconds) < 30


# --------------------------------------------------------------------- tool --


def _books(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bank"
    ws.append(["Statement for October"])
    ws.append([])
    ws.append(["Date", "Description", "Withdrawal", "Deposit", "Check"])
    for r in [(D(2026, 10, 1), "Deposit", None, 5000, None), (D(2026, 10, 3), "Check 1001", 250.10, None, 1001),
              (D(2026, 10, 5), "Check 1002", 80, None, 1002), (D(2026, 10, 7), "ACH Rent", 1500, None, None),
              (D(2026, 10, 9), "Deposit", None, 1200, None), (D(2026, 10, 9), "Bank fee", 15, None, None),
              (D(2026, 10, 20), "Deposit", None, 300, None), (None, "Ending balance note", None, None, None)]:
        ws.append(list(r))
    wb.save(tmp_path / "bank.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "GL"
    ws.append(["Date", "Memo", "Amount", "Ref"])
    for r in [(D(2026, 9, 30), "Client deposit", 5000, None), (D(2026, 10, 1), "Supplies", -250.10, "1001"),
              (D(2026, 10, 2), "Postage", -80, "#1002"), (D(2026, 10, 7), "Rent", -1500, None),
              (D(2026, 10, 8), "Client A", 700, None), (D(2026, 10, 8), "Client B", 500, None),
              (D(2026, 10, 15), "Software", "=-7*7", "1003"), (D(2026, 10, 1), "Old deposit", 300, None)]:
        ws.append(list(r))
    wb.save(tmp_path / "gl.xlsx")


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


BANK = {"path": "bank.xlsx", "name": "Bank", "money_in": "Deposit", "money_out": "Withdrawal", "date": "Date",
        "ref": "Check", "description": "Description", "header_row": 3}
GL = {"path": "gl.xlsx", "name": "Books", "amount": "Amount", "date": "Date", "ref": "Ref", "description": "Memo"}


def _run(ws: Path, **args):
    return asyncio.run(R.ExcelReconcileTool().execute({"a": dict(BANK), "b": dict(GL), **args}, _ctx(ws)))


def test_the_tool_matches_a_bank_statement_against_the_books(tmp_path):
    _books(tmp_path)
    res = _run(tmp_path)
    assert res.ok, res.error
    assert "Bank (Bank): 7 rows, total 4,654.90." in res.output and "Books (GL): 8 rows, total 4,620.90." in res.output
    assert "Matched: 4 pairs (2 by amount and reference; 2 by amount, dates within 5 day(s))." in res.output
    assert "minus only in Books (1,451.00). It ties." in res.output
    assert "row 9 | -15.00 | 2026-10-09 | Bank fee" in res.output, "a row is named by the sheet's own row number"
    assert "row 8 | -49.00 | 2026-10-15 | ref 1003 | Software" in res.output, "a formula with no saved result is worked out"
    assert "300.00: Bank row 10 2026-10-20 / Books row 9 2026-10-01 (19 days apart)" in res.output
    assert "Bank row 8 1,200.00 = Books rows 6, 7 on 2026-10-08" in res.output
    assert "1 row(s) of Bank hold no amount and were left out." in res.output
    assert res.data["ties"] is True and res.data["only_a_rows"] == [8, 9, 10] and res.data["difference"] == 34.0
    assert not list(tmp_path.glob("*rec*")), "nothing is written unless asked"
    assert R.ExcelReconcileTool.returns_untrusted_content


def test_the_result_workbook_is_new_complete_and_undoable(tmp_path):
    _books(tmp_path)
    tool = R.ExcelReconcileTool()
    args = {"a": dict(BANK), "b": dict(GL), "save_as": "out/October rec"}
    undo = asyncio.run(tool.capture_undo(args, _ctx(tmp_path)))
    assert undo and undo["kind"] == "file_delete"
    res = asyncio.run(tool.execute(args, _ctx(tmp_path)))
    out = tmp_path / "out" / "October rec.xlsx"
    assert res.ok and out.is_file() and res.data["abs_path"] == str(out.resolve())
    wb = load_workbook(out)
    assert wb.sheetnames == ["Summary", "Matched", "Only in Bank", "Only in Books"]
    summary = {row[0]: row[1:] for row in wb["Summary"].iter_rows(values_only=True)}
    assert summary["Total"] == (4654.9, 4620.9) and summary["Ties"][0] == "yes" and summary["Matched pairs"] == (4, 4)
    assert wb["Matched"].max_row == 5 and wb["Only in Bank"].max_row == 4 and wb["Only in Books"].max_row == 5
    assert [c.value for c in wb["Only in Bank"]["C"][1:]] == [1200, -15, 300]
    again = asyncio.run(tool.execute(args, _ctx(tmp_path)))
    assert not again.ok and "already exists" in again.error, "a result never replaces a file"
    assert asyncio.run(tool.capture_undo(args, _ctx(tmp_path))) is None
    back = asyncio.run(tool.revert(undo, _ctx(tmp_path)))
    assert back.ok and not out.exists()
    assert load_workbook(tmp_path / "gl.xlsx")["GL"]["C8"].value == "=-7*7", "the lists themselves are never touched"


def test_refusals_are_said_in_words(tmp_path):
    _books(tmp_path)
    assert "say which column holds the amount" in _run(tmp_path, b={"path": "gl.xlsx"}).error
    assert "no column 'Total'" in _run(tmp_path, b={**GL, "amount": "Total"}).error
    assert "no such workbook" in _run(tmp_path, b={**GL, "path": "missing.xlsx"}).error
    (tmp_path / "x.csv").write_text("a,b\n1,2\n")
    assert "reads .xlsx and .xlsm" in _run(tmp_path, b={**GL, "path": "x.csv"}).error
    assert "no row of sheet GL holds an amount" in _run(tmp_path, b={**GL, "amount": "Memo"}).error
    assert "needs the two lists" in asyncio.run(R.ExcelReconcileTool().execute({"a": dict(BANK)}, _ctx(tmp_path))).error
    assert "tolerance cannot be negative" in _run(tmp_path, tolerance=-1).error
    flipped = _run(tmp_path, b={**GL, "flip": True})
    assert flipped.ok and "Matched: 0 pairs." in flipped.output and "OPPOSITE signs" in flipped.output


def test_the_same_name_twice_is_told_apart(tmp_path):
    _books(tmp_path)
    res = asyncio.run(R.ExcelReconcileTool().execute({"a": {**GL, "name": "Ledger"}, "b": {**GL, "name": "Ledger"}}, _ctx(tmp_path)))
    assert res.ok and "Ledger (a)" in res.output and "Ledger (b)" in res.output and "Matched: 8 pairs" in res.output


# ------------------------------------------------------------------- arming --


@pytest.mark.parametrize(
    "text",
    [
        "reconcile the bank statement to the books",
        "match these deposits against the ledger",
        "which checks are still outstanding?",
        "tie out the sub-ledger to the control account",
        "what is on the statement but missing from the books?",
        "do a bank rec for October",
    ],
)
def test_matching_words_arm_it_only_beside_a_spreadsheet(text):
    assert wants_excel_reconcile(text, ["excel_read"]) is True
    assert wants_excel_reconcile(text, ["web_search"]) is False, "the words alone are everyday words"


@pytest.mark.parametrize("text", ["sum the amounts by month", "what does this workbook do?", "make the header bold", "they reconciled after the argument"[:0]])
def test_other_spreadsheet_talk_does_not_arm_it(text):
    assert wants_excel_reconcile(text, ["excel_read"]) is False


def test_the_words_are_linear_on_a_long_message():
    code = (
        "import time\n"
        "from iron_jarvis.tools.autoselect import wants_excel_reconcile\n"
        "t = time.perf_counter()\n"
        "for s in ('match ' * 20000, 'compare the statement ' * 6000, 'not in ' * 18000, 'a' * 128000):\n"
        "    wants_excel_reconcile(s, ['excel_read'])\n"
        "print(round(time.perf_counter() - t, 2))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-600:]
    assert float(out.stdout.strip()) < 10


def test_chat_arms_it_with_an_attached_spreadsheet(tmp_path):
    d = SimpleNamespace(platform=build_platform(str(tmp_path / "home")))
    body = ChatBody(messages=[{"role": "user", "content": "reconcile the bank statement in this spreadsheet to the ledger"}],
                    auto_tools=True, attachments=["C:/x/bank.xlsx", "C:/x/gl.xlsx"])
    assert "excel_reconcile" in CT._resolve_armed_tools(d, body)[0]
    plain = ChatBody(messages=[{"role": "user", "content": "match the tone of this email against our style guide"}], auto_tools=True)
    assert "excel_reconcile" not in CT._resolve_armed_tools(d, plain)[0]
    assert d.platform.permissions.mode_for("excel_reconcile").value == "allow"
