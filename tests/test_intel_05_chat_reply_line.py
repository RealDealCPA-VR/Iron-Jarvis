"""Intelligence plan, release 5: the line under a chat reply that made a file.

Both chat lanes, end to end through the real app and the real tool registry:
a scripted model writes a real workbook with ``write_document`` (openpyxl),
and the answer that comes back (the POST body, the stream's done frame, which
is what the page saves) carries ``checked``: one line built by the daemon
from the one record of checks, and the list a press opens.

* a workbook with an error cell: the line names the cell, in the warn tone;
* totals that add up are counted, a wrong one is named with both figures;
* both lanes give the same line for the same reply;
* a reply that made no file has no line and writes no content row;
* the line is what the record holds (a row changed in the record changes it);
* the reply the daemon writes home, and a saved chat reopened, keep the line;
* the files are opened off the event loop, and only the files the reply's own
  tools wrote: never a path the model wrote in its answer.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.checks import content, record as R
from iron_jarvis.checks.record import CheckRecord
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.durable_turns import reply_line
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_wave3_chat_lanes_v1311 import _post_turn, _scripted_router

LANES = ("post", "stream")

ERROR_SHEET = [["Item", "Amount"], ["Rent", 1200], ["Food", 300], ["Total", 1500], ["Share", "#DIV/0!"]]
GOOD_SHEET = [["Item", "Q1", "Q2"], ["Rent", 1200, 1250], ["Food", 300, 290], ["Total", 1500, 1540]]
WRONG_SHEET = [["Item", "Amount"], ["Rent", 1200], ["Food", 300], ["Total", 1700]]


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as c:
        yield c


def _rows(platform) -> list[CheckRecord]:
    with session_scope(platform.engine) as db:
        return list(db.exec(select(CheckRecord).order_by(CheckRecord.created_at, CheckRecord.id)))


def _write_turn(client, monkeypatch, lane, rows, *, final="Done: I saved summary.xlsx.", name="summary.xlsx",
                **extra):
    platform = client.app.state.platform
    _scripted_router(platform, monkeypatch,
                     [[ToolCall(id="c1", name="write_document", arguments={"path": name, "content": rows})]],
                     final)
    body, frames = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "make me a summary sheet of this"}],
        "tools": ["write_document"],
        "approval_mode": "yolo",
        **extra,
    })
    assert body["documents"], f"the scripted write did not land: {body.get('reply')}"
    return body, frames


# --------------------------------------------------------------------------- #
# The plan's own "done when"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_a_spreadsheet_with_an_error_cell_fails_and_the_line_says_which_cell(client, monkeypatch, lane):
    body, _ = _write_turn(client, monkeypatch, lane, ERROR_SHEET)
    checked = body["checked"]
    assert checked["line"] == (
        "Checked by Iron Jarvis: the spreadsheet opens, 1 total adds up. "
        "Problem in the spreadsheet: Sheet!B5 holds #DIV/0!."
    ), checked["line"]
    assert checked["tone"] == "warn"
    failed = [c for c in checked["checks"] if c["result"] == "failed"]
    assert len(failed) == 1 and failed[0]["label"] == "summary.xlsx" and failed[0]["who"] == "by rule"
    assert checked["checks"][0] == failed[0], "the problem is not first in the list"
    # The record holds the same finding, under this reply and no other owner.
    rows = [r for r in _rows(client.app.state.platform) if r.check == R.SHEET_ERRORS]
    assert [(r.result, r.file, r.by) for r in rows] == [(R.FAILED, "summary.xlsx", "rule")]
    assert rows[0].detail in checked["line"] and rows[0].session_id == ""


@pytest.mark.parametrize("lane", LANES)
def test_totals_that_add_up_are_counted_and_pressing_the_line_lists_three_checks(client, monkeypatch, lane):
    """The plan's do-nothing check: ask for a summary sheet; the line is
    there, and pressing it lists three checks of the file."""
    body, _ = _write_turn(client, monkeypatch, lane, GOOD_SHEET)
    checked = body["checked"]
    assert checked["line"] == "Checked by Iron Jarvis: the spreadsheet opens, no error cells, 2 totals add up."
    assert checked["tone"] == "quiet" and checked["more"] == 0
    of_the_file = [(c["said"], c["result"], c["who"]) for c in checked["checks"] if c["label"] == "summary.xlsx"]
    assert of_the_file == [
        ("opens", "passed", "by rule"), ("no error cells", "passed", "by rule"), ("2 totals add up", "passed", "by rule"),
    ]
    # The check-again loop's own look at the reply is in the same record and
    # list, after the three checks of the file.
    assert [(c["label"], c["said"]) for c in checked["checks"]][3:] == [
        ("The reply", "what it says matches what it did")]


@pytest.mark.parametrize("lane", LANES)
def test_a_wrong_total_is_named_with_both_figures(client, monkeypatch, lane):
    body, _ = _write_turn(client, monkeypatch, lane, WRONG_SHEET)
    line = body["checked"]["line"]
    assert "B4 says 1,700 and the cells above it add up to 1,500" in line, line
    assert body["checked"]["tone"] == "warn"


def test_both_lanes_give_the_same_line_for_the_same_reply(tmp_path, monkeypatch):
    got = {}
    for lane in LANES:
        with TestClient(create_app(str(tmp_path / lane))) as c:
            body, _ = _write_turn(c, monkeypatch, lane, ERROR_SHEET)
            got[lane] = body["checked"]
    assert got["post"] == got["stream"]
    assert got["post"]["line"].startswith("Checked by Iron Jarvis:")


# --------------------------------------------------------------------------- #
# No file, no line
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_a_reply_that_made_no_file_has_no_line_and_writes_no_content_row(client, monkeypatch, lane):
    platform = client.app.state.platform
    _scripted_router(platform, monkeypatch, [], "The capital of France is Paris.")
    opened: list[str] = []
    real = content.check_files
    monkeypatch.setattr(content, "check_files", lambda *a, **kw: (opened.append("x"), real(*a, **kw))[1])
    # A reply with a file tool in hand that wrote nothing.
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "capital of France?"}],
        "tools": ["write_document"], "approval_mode": "yolo",
    })
    assert "checked" not in body
    assert body["documents"] == []
    assert opened == [], "a reply that made no file had files checked"
    rows = _rows(platform)
    assert not [r for r in rows if r.check in R.CONTENT_CHECKS]
    # The reply's own check still wrote its one row to the one record.
    assert [(r.check, r.result, r.detail) for r in rows] == [(R.CLAIMS, R.PASSED, "what it says matches what it did")]


@pytest.mark.parametrize("lane", LANES)
def test_a_file_the_answer_only_talks_about_is_never_opened(client, monkeypatch, lane, tmp_path):
    """Only the files this reply's own tools wrote are checked. A path the
    model WROTE in its answer is model text: it is never opened."""
    other = tmp_path / "private.xlsx"
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["secret", "#REF!"])
    wb.save(other)
    asked: list[str] = []
    real = content.check_files

    def watched(paths, reply="", **kw):
        asked.extend(str(p) for p in paths)
        return real(paths, reply, **kw)

    monkeypatch.setattr(content, "check_files", watched)
    body, _ = _write_turn(client, monkeypatch, lane, GOOD_SHEET,
                          final=f"Done. I saved summary.xlsx and also checked {other}.")
    assert asked == [str(p) for p in body["documents"]], "something other than the reply's own files was opened"
    assert str(other) not in asked
    assert "#REF!" not in body["checked"]["line"]


# --------------------------------------------------------------------------- #
# From the record, and kept
# --------------------------------------------------------------------------- #
def test_the_line_is_read_from_the_record(client, monkeypatch):
    """The line is built from the rows the record HOLDS for this reply: a row
    the existing checks wrote is in it, and with the record's read taken away
    there is no line at all."""
    from iron_jarvis.checks import turn as T

    body, _ = _write_turn(client, monkeypatch, "post", GOOD_SHEET, turn_id="turn_from_record")
    kept = R.for_turn(client.app.state.platform.engine, "turn_from_record")
    assert R.payload(kept) == body["checked"]

    monkeypatch.setattr(T.record, "for_turn", lambda engine, turn: [])
    again, _ = _write_turn(client, monkeypatch, "post", GOOD_SHEET, turn_id="turn_no_read", name="second.xlsx")
    assert "checked" not in again, "a line was built without reading the record"


def test_the_reply_the_daemon_writes_home_and_a_reopened_chat_keep_the_line(client, monkeypatch):
    body, frames = _write_turn(client, monkeypatch, "stream", ERROR_SHEET, turn_id="turn_keep")
    # What the daemon itself writes into the chat when no page follows the turn.
    line = reply_line("turn_keep", [[e, d] for e, d in frames], "done")
    assert line["checked"] == body["checked"]
    # A reply with no line is written home exactly as before (no key).
    plain = reply_line("t2", [["done", {"reply": "hello"}]], "done")
    assert "checked" not in plain
    assert "checked" not in reply_line("t3", [["done", {"reply": "x", "checked": {"line": "  "}}]], "done")

    # What the page saves on the reply round-trips, so a reopened chat has it.
    saved = client.put("/chat/threads/new", json={"title": "t", "messages": [
        {"role": "user", "content": "make me a summary sheet", "turnId": "turn_keep"},
        {"role": "assistant", "content": body["reply"], "turnId": "turn_keep", "checked": body["checked"]},
    ]})
    assert saved.status_code == 200, saved.text
    again = client.get(f"/chat/threads/{saved.json()['id']}").json()
    assert again["messages"][-1]["checked"] == body["checked"]


def test_a_new_chats_first_replys_checks_follow_the_chat_when_the_page_names_it(client, monkeypatch):
    _write_turn(client, monkeypatch, "post", GOOD_SHEET, turn_id="turn_first")
    engine = client.app.state.platform.engine
    assert R.for_thread(engine, "thread_named") == []
    r = client.post("/chat/turns/turn_first/thread", json={"thread_id": "thread_named"})
    assert r.status_code == 200, r.text
    homed = R.for_thread(engine, "thread_named")
    assert {row["turn_id"] for row in homed} == {"turn_first"} and len(homed) >= 4


def test_a_reply_in_a_saved_chat_writes_its_rows_under_that_chat(client, monkeypatch):
    _write_turn(client, monkeypatch, "stream", GOOD_SHEET, turn_id="turn_saved", thread_id="thread_saved")
    rows = _rows(client.app.state.platform)
    assert rows and {(r.thread_id, r.turn_id) for r in rows} == {("thread_saved", "turn_saved")}


# --------------------------------------------------------------------------- #
# Off the loop, bounded, never a second model call
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_the_files_are_opened_off_the_event_loop_and_no_model_is_asked_again(client, monkeypatch, lane):
    seen: list[bool] = []
    real = content.check_files

    def watched(paths, reply="", **kw):
        try:
            asyncio.get_running_loop()
            seen.append(True)            # on a loop: the daemon would be blocked
        except RuntimeError:
            seen.append(False)
        return real(paths, reply, **kw)

    monkeypatch.setattr(content, "check_files", watched)
    platform = client.app.state.platform
    calls = _scripted_router(
        platform, monkeypatch,
        [[ToolCall(id="c1", name="write_document", arguments={"path": "summary.xlsx", "content": ERROR_SHEET})]],
        "Done: I saved summary.xlsx.",
    )
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "make me a summary sheet of this"}],
        "tools": ["write_document"], "approval_mode": "yolo",
    })
    assert seen == [False], "the made file was opened on the event loop (or not once)"
    assert body["checked"]["tone"] == "warn"
    # A failed content check never sends the model back: the write, then the answer.
    assert calls["n"] == 2, f"the model was called {calls['n']} times"


def test_a_check_that_is_too_slow_reads_not_checked_and_the_reply_still_ends(client, monkeypatch):
    release = threading.Event()

    def stuck(paths, reply="", **kw):
        release.wait(30)
        return []

    monkeypatch.setattr(content, "check_files", stuck)
    monkeypatch.setattr(content, "HARD_WAIT_S", 0.05)
    try:
        body, _ = _write_turn(client, monkeypatch, "post", GOOD_SHEET)
    finally:
        release.set()
    assert body["checked"]["line"] == "Not checked: the spreadsheet (the check ran out of time)."
    assert body["checked"]["tone"] == "quiet"
    assert body["reply"].startswith("Done: I saved summary.xlsx.")


def test_a_check_that_raises_never_costs_the_reply(client, monkeypatch):
    def broken(paths, reply="", **kw):
        raise RuntimeError("the checker broke")

    monkeypatch.setattr(content, "check_files", broken)
    body, _ = _write_turn(client, monkeypatch, "stream", GOOD_SHEET)
    assert body["reply"].startswith("Done: I saved summary.xlsx.")
    assert body["checked"]["line"] == "Not checked: the spreadsheet (it could not be checked here)."
    assert all(c["result"] != "passed" for c in body["checked"]["checks"] if c["label"] == "summary.xlsx")
