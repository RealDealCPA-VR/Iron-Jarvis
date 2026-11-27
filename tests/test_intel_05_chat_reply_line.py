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
        "Problem in the spreadsheet: B5 holds #DIV/0!."
    ), checked["line"]
    assert checked["tone"] == "warn"
    failed = [c for c in checked["checks"] if c["result"] == "failed"]
    assert len(failed) == 1 and failed[0]["label"] == "“summary.xlsx”" and failed[0]["who"] == "by Iron Jarvis"
    assert failed[0]["check"] == R.SHEET_ERRORS
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
    of_the_file = [(c["said"], c["result"], c["who"]) for c in checked["checks"] if c["label"] == "“summary.xlsx”"]
    assert of_the_file == [
        ("opens", "passed", "by Iron Jarvis"), ("no error cells", "passed", "by Iron Jarvis"),
        ("2 totals add up", "passed", "by Iron Jarvis"),
    ]
    # Nothing else: the reply says it saved summary.xlsx (a step did), and NO row says its claims were checked.
    assert len(checked["checks"]) == 3 and "The reply" not in [c["label"] for c in checked["checks"]]


@pytest.mark.parametrize("lane", LANES)
def test_a_reply_that_says_it_saved_a_file_nothing_wrote_is_never_told_its_claims_are_in_the_record(
        client, monkeypatch, lane):
    """Decision 1, through the real lane (the reviewer's run): one file
    written, a command run, and the reply says it saved TWO files. The line's
    list read "The reply: the files it says it saved or changed ... are in
    Iron Jarvis's record (Checked)". There is no such row."""
    platform = client.app.state.platform
    _scripted_router(platform, monkeypatch, [
        [ToolCall(id="c1", name="write_document", arguments={"path": "summary.xlsx", "content": GOOD_SHEET})],
        [ToolCall(id="c2", name="shell", arguments={"command": "echo hello"})],
    ], "Done: I saved summary.xlsx and I also saved backup-copy.docx to your folder.")
    body, _ = _post_turn(client, lane, {"messages": [{"role": "user", "content": "make me a summary sheet of this"}],
                                        "tools": ["write_document", "shell"], "approval_mode": "yolo"})
    assert body["documents"], body.get("reply")
    checked = body["checked"]
    assert checked["line"] == "Checked by Iron Jarvis: the spreadsheet opens, no error cells, 2 totals add up."
    passed = [(c["label"], c["said"]) for c in checked["checks"] if c["result"] == "passed"]
    assert passed == [("“summary.xlsx”", "opens"), ("“summary.xlsx”", "no error cells"), ("“summary.xlsx”", "2 totals add up")]
    said = " ".join(c["said"] for c in checked["checks"])
    assert "record" not in said and "backup-copy" not in said
    assert not [r for r in _rows(platform) if r.check == R.CLAIMS and r.result == R.PASSED]


@pytest.mark.parametrize("lane", LANES)
def test_a_word_file_the_apps_own_tool_just_edited_is_never_a_problem_for_the_name_of_its_main_part(
        client, monkeypatch, lane):
    """Decision 2, through the real lane (the reviewer's run): a .docx whose
    main part is ``word/document2.xml`` (Word Online saves it so), changed by
    the app's own ``docx_edit``. The line read "a problem in the document: it
    does not open: a part of the file is missing"."""
    import zipfile
    from pathlib import Path

    platform = client.app.state.platform
    _scripted_router(platform, monkeypatch, [[ToolCall(id="c1", name="write_document", arguments={
        "path": "letter.docx", "content": "Dear Sam,\n\nThe meeting is on Monday.\n"})]], "Done: I saved letter.docx.")
    body, _ = _post_turn(client, lane, {"messages": [{"role": "user", "content": "write the letter"}],
                                        "tools": ["write_document"], "approval_mode": "yolo"})
    path = Path(body["documents"][0])
    other = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(other, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename in ("[Content_Types].xml", "_rels/.rels"):
                data = data.replace(b"word/document.xml", b"word/document2.xml")
            zout.writestr({"word/document.xml": "word/document2.xml",
                           "word/_rels/document.xml.rels": "word/_rels/document2.xml.rels"}.get(info.filename, info.filename), data)
    other.replace(path)
    _scripted_router(platform, monkeypatch, [[ToolCall(id="c2", name="docx_edit", arguments={
        "path": "letter.docx", "operations": [{"op": "replace", "find": "Monday", "replace": "Tuesday"}]})]],
        "Done: I changed Monday to Tuesday in letter.docx.")
    body, _ = _post_turn(client, lane, {"messages": [{"role": "user", "content": "change Monday to Tuesday in letter.docx"}],
                                        "tools": ["docx_edit"], "approval_mode": "yolo"})
    assert body["checked"]["line"] == "Checked by Iron Jarvis: the document opens, has text.", body["checked"]["line"]
    assert body["checked"]["tone"] == "quiet"


@pytest.mark.parametrize("lane", LANES)
def test_a_local_only_chats_own_reply_keeps_its_checks_home_after_the_chat_is_switched_off(
        client, monkeypatch, lane):
    """Decision 7. PRIVACY, through the real lane and the real tool. A reply
    made a file in a chat that is Local only: its rows hold the file's name,
    a sheet's name and figures. The chat was then switched off Local only,
    and "what did you check?" handed them to the (now cloud) model. A reply's
    rows are marked kept home when its chat is Local only at the write, and
    read like a kept-home run's: only while the chat is Local only itself."""
    import json
    import types

    from iron_jarvis.checks.tool import NOTHING, ChecksListTool
    from iron_jarvis.core.models import ChatThreadRecord
    from iron_jarvis.providers.egress import set_thread_local_only, thread_local_only

    platform = client.app.state.platform
    engine = platform.engine
    home, plain = f"chat_home_{lane}", f"chat_plain_{lane}"
    with session_scope(engine) as db:
        db.add(ChatThreadRecord(id=home, title="home", setup_json=json.dumps({"local_only": True})))
        db.add(ChatThreadRecord(id=plain, title="plain", setup_json="{}"))
        db.commit()

    def asked(chat: str) -> str:
        ctx = types.SimpleNamespace(engine=engine, thread_id=chat, session_id="chat", turn_id="turn_asking")
        got = asyncio.run(ChecksListTool().execute({}, ctx))
        assert got.ok
        return got.output

    # A reply in the Local only chat: the page gets its line, and the rows are kept home.
    body, _ = _write_turn(client, monkeypatch, lane, WRONG_SHEET, name="client-payroll.xlsx",
                          final="Done: I saved client-payroll.xlsx.", thread_id=home, turn_id=f"turn_home_{lane}")
    assert "B4 says 1,700" in body["checked"]["line"], body["checked"]["line"]
    mine = [r for r in _rows(platform) if r.turn_id == f"turn_home_{lane}"]
    assert mine and {(r.thread_id, r.kept_home) for r in mine} == {(home, True)}
    assert "client-payroll.xlsx" in asked(home) and "1,700" in asked(home)
    # Switched OFF Local only: the rows are still the chat's own, and no reader hands them over.
    assert set_thread_local_only(engine, home, False) is False and not thread_local_only(engine, home)
    assert asked(home) == NOTHING
    # Switched back ON: they are read again.
    assert set_thread_local_only(engine, home, True) is True
    assert "client-payroll.xlsx" in asked(home)

    # THE OTHER DIRECTION: a reply in a chat that is not Local only is not kept home, whatever the chat is later.
    _write_turn(client, monkeypatch, lane, WRONG_SHEET, name="plan.xlsx", final="Done: I saved plan.xlsx.",
                thread_id=plain, turn_id=f"turn_plain_{lane}")
    theirs = [r for r in _rows(platform) if r.turn_id == f"turn_plain_{lane}"]
    assert theirs and {(r.thread_id, r.kept_home) for r in theirs} == {(plain, False)}
    assert "plan.xlsx" in asked(plain)
    assert set_thread_local_only(engine, plain, True) is True
    assert "plan.xlsx" in asked(plain)
    assert set_thread_local_only(engine, plain, False) is False
    assert "plan.xlsx" in asked(plain)

    # A NEW chat (not saved yet) whose reply is sent Local only: the turn's own scope keeps its rows home too.
    _write_turn(client, monkeypatch, lane, WRONG_SHEET, name="fresh.xlsx", final="Done: I saved fresh.xlsx.",
                local_only=True, turn_id=f"turn_fresh_{lane}")
    fresh = [r for r in _rows(platform) if r.turn_id == f"turn_fresh_{lane}"]
    assert fresh and {(r.thread_id, r.kept_home) for r in fresh} == {("", True)}


@pytest.mark.parametrize("lane", LANES)
def test_a_total_that_differs_is_worth_a_look_with_both_figures_never_a_problem(client, monkeypatch, lane):
    """Iron Jarvis added the list up itself and got another figure. It says
    both, in the warning colour, under its own word: it is not saying the
    file is wrong."""
    body, _ = _write_turn(client, monkeypatch, lane, WRONG_SHEET)
    checked = body["checked"]
    assert checked["line"] == (
        "Checked by Iron Jarvis: the spreadsheet opens, no error cells. "
        "Worth a look: B4 says 1,700 and the cells above it add up to 1,500."
    ), checked["line"]
    assert checked["tone"] == "warn" and "Problem" not in checked["line"]
    first = checked["checks"][0]
    assert (first["label"], first["said"], first["result"], first["result_words"], first["who"]) == (
        "“summary.xlsx”", "B4 says 1,700 and the cells above it add up to 1,500", "look", "Worth a look",
        "by Iron Jarvis")
    assert not [c for c in checked["checks"] if c["result"] == "failed"]
    rows = [r for r in _rows(client.app.state.platform) if r.check == R.SHEET_TOTALS]
    assert [(r.result, r.detail) for r in rows] == [(R.LOOK, "B4 says 1,700 and the cells above it add up to 1,500")]


#: The reviewer's rows (B2): the app's own writer warns that D3 is #DIV/0!
#: and saves no result for it. The line used to read "no error cells".
DIVIDES = [["Name", "Amount", "Parts", "Each"], ["A", 100, 4, "=B2/C2"], ["B", 50, 0, "=B3/C3"]]
#: ... and a formula the app's evaluator cannot work out at all.
UNWORKED = [["Name", "Amount"], ["A", 100], ["B", "=NOSUCHFUNCTION(B2)"]]


@pytest.mark.parametrize("lane", LANES)
def test_a_formula_the_writer_saved_no_result_for_is_never_called_no_error_cells(client, monkeypatch, lane):
    """Through the real ``write_document`` tool: D3 is ``=B3/C3`` over a
    zero. The file holds no saved result for it; Iron Jarvis works it out
    and says what it comes to, as worth a look (the file holds no error)."""
    body, _ = _write_turn(client, monkeypatch, lane, DIVIDES)
    checked = body["checked"]
    assert checked["line"] == (
        "Checked by Iron Jarvis: the spreadsheet opens. Worth a look: D3 works out to an error."
    ), checked["line"]
    assert checked["tone"] == "warn" and "no error cells" not in checked["line"]
    assert [(c["said"], c["result"]) for c in checked["checks"]][0] == ("D3 works out to an error", "look")
    rows = [r for r in _rows(client.app.state.platform) if r.check == R.SHEET_ERRORS]
    assert [(r.result, r.detail) for r in rows] == [(R.LOOK, "D3 works out to an error")]


@pytest.mark.parametrize("lane", LANES)
def test_a_formula_nobody_could_work_out_reads_not_checked_never_no_error_cells(client, monkeypatch, lane):
    body, _ = _write_turn(client, monkeypatch, lane, UNWORKED)
    assert body["checked"]["line"] == (
        "Checked by Iron Jarvis: the spreadsheet opens. Not checked: the spreadsheet "
        "(1 formula could not be worked out here)."
    ), body["checked"]["line"]
    assert body["checked"]["tone"] == "quiet"
    assert not [c for c in body["checked"]["checks"] if c["said"] == "no error cells"]


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
    # The answer names nothing it saved, changed or sent: nothing is said to have been checked.
    assert rows == []


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

    # (Read back by the ids of the rows the reply's own write made: ``record.by_ids``.)
    monkeypatch.setattr(T.record, "by_ids", lambda engine, ids: [])
    again, _ = _write_turn(client, monkeypatch, "post", GOOD_SHEET, turn_id="turn_no_read", name="second.xlsx")
    assert "checked" not in again, "a line was built without reading the record"


def test_a_reply_the_daemon_writes_into_the_chat_while_the_page_is_away_keeps_the_line(client, monkeypatch):
    """B3: the daemon's own write into a saved chat (``write_home`` ->
    ``append_to_chat_thread``) dropped ``checked`` with every key it did not
    know, so a reply that finished while the page was closed lost its line.
    Through the real write, into a real saved chat, read back."""
    from iron_jarvis.daemon.durable_turns import HOME_WRITTEN, write_home

    body, frames = _write_turn(client, monkeypatch, "stream", ERROR_SHEET, turn_id="turn_keep")
    assert body["checked"]["tone"] == "warn"
    saved = client.put("/chat/threads/new", json={"title": "t", "messages": [
        {"role": "user", "content": "make me a summary sheet"}]})
    assert saved.status_code == 200, saved.text
    thread = saved.json()["id"]
    engine = client.app.state.platform.engine
    assert write_home(engine, "turn_keep", thread, [[e, d] for e, d in frames], "done") == HOME_WRITTEN
    kept = client.get(f"/chat/threads/{thread}").json()["messages"][-1]
    assert (kept["role"], kept["savedBy"], kept["turnId"]) == ("assistant", "daemon", "turn_keep")
    assert kept["checked"] == body["checked"], "the saved reply lost (or changed) its line"
    assert "B5 holds #DIV/0!" in kept["checked"]["line"]

    # By whitelist: what is not the line's own shape is not written into the chat.
    dirty = {**body["checked"], "html": "<script>x</script>", "line": body["checked"]["line"] + " " + "z" * 5000,
             "checks": [*body["checked"]["checks"], {"label": "x", "said": "y", "result": "wonderful"}]}
    other = client.put("/chat/threads/new", json={"title": "t2", "messages": [{"role": "user", "content": "q"}]}).json()["id"]
    assert write_home(engine, "turn_dirty", other, [["done", {"reply": "ok", "checked": dirty}]], "done") == HOME_WRITTEN
    cleaned = client.get(f"/chat/threads/{other}").json()["messages"][-1]["checked"]
    assert set(cleaned) == {"line", "tone", "checks", "more", "more_words"} and len(cleaned["line"]) == R.LINE_MAX
    assert cleaned["checks"] == body["checked"]["checks"]
    # ... and the chat's own writer cleans it too, whoever hands it the line (a line given whole, not
    # built from frames, goes straight to it).
    given = client.put("/chat/threads/new", json={"title": "t4", "messages": [{"role": "user", "content": "q"}]}).json()["id"]
    whole = {"role": "assistant", "content": "ok", "turnId": "turn_given", "savedBy": "daemon", "checked": dirty}
    assert write_home(engine, "turn_given", given, [], "done", line=whole) == HOME_WRITTEN
    kept_given = client.get(f"/chat/threads/{given}").json()["messages"][-1]["checked"]
    assert kept_given == cleaned, "the chat's own writer saved a line it was handed as it came"
    blank = {"role": "assistant", "content": "ok", "turnId": "turn_given2", "savedBy": "daemon", "checked": {"line": " "}}
    assert write_home(engine, "turn_given2", given, [], "done", line=blank) == HOME_WRITTEN
    assert "checked" not in client.get(f"/chat/threads/{given}").json()["messages"][-1]
    # A reply with no line is written home exactly as before (no key).
    third = client.put("/chat/threads/new", json={"title": "t3", "messages": [{"role": "user", "content": "q"}]}).json()["id"]
    for turn, done in (("turn_plain", {"reply": "hello"}), ("turn_blank", {"reply": "x", "checked": {"line": "  "}}),
                       ("turn_junk", {"reply": "y", "checked": "Checked by Iron Jarvis: everything"})):
        assert write_home(engine, turn, third, [["done", done]], "done") == HOME_WRITTEN
    assert all("checked" not in m for m in client.get(f"/chat/threads/{third}").json()["messages"])


def test_the_line_the_daemon_would_write_and_the_one_the_page_saves_are_the_same(client, monkeypatch):
    body, frames = _write_turn(client, monkeypatch, "stream", ERROR_SHEET, turn_id="turn_same")
    line = reply_line("turn_same", [[e, d] for e, d in frames], "done")
    assert line["checked"] == body["checked"]
    assert "checked" not in reply_line("t2", [["done", {"reply": "hello"}]], "done")
    assert "checked" not in reply_line("t3", [["done", {"reply": "x", "checked": {"line": "  "}}]], "done")
    # What the page saves on the reply round-trips, so a reopened chat has it.
    saved = client.put("/chat/threads/new", json={"title": "t", "messages": [
        {"role": "user", "content": "make me a summary sheet", "turnId": "turn_same"},
        {"role": "assistant", "content": body["reply"], "turnId": "turn_same", "checked": body["checked"]},
    ]})
    assert saved.status_code == 200, saved.text
    again = client.get(f"/chat/threads/{saved.json()['id']}").json()
    assert again["messages"][-1]["checked"] == body["checked"]


# --------------------------------------------------------------------------- #
# S4: a new chat's first reply's rows reach the chat, whenever the page names it
# --------------------------------------------------------------------------- #
def _owners(platform, turn) -> set:
    return {(r.thread_id, r.turn_id) for r in _rows(platform) if r.turn_id == turn}


@pytest.mark.parametrize("lane", LANES)
def test_a_chat_named_before_its_first_reply_ends_gets_that_replys_rows(client, monkeypatch, lane):
    """The page saved the new chat and linked the turn while the reply was
    still on its way. The POST lane filed every row under the turn and none
    under the chat: "What did you check?" then found nothing, and deleting
    the chat left the rows behind."""
    platform = client.app.state.platform
    turn = f"turn_pre_{lane}"
    r = client.post(f"/chat/turns/{turn}/thread", json={"thread_id": f"thread_pre_{lane}"})
    assert r.status_code == 200, r.text
    body, _ = _write_turn(client, monkeypatch, lane, GOOD_SHEET, turn_id=turn)
    assert body["checked"]["line"].startswith("Checked by Iron Jarvis:")
    assert _owners(platform, turn) == {(f"thread_pre_{lane}", turn)}, "rows were left with no chat"
    assert len(R.for_thread(platform.engine, f"thread_pre_{lane}")) == 3


@pytest.mark.parametrize("lane", LANES)
def test_a_chat_named_while_the_rows_are_being_written_gets_them(client, monkeypatch, lane):
    """The link lands between the reply's look for its chat and the end of
    its write (the route's own homing ran on an empty record): the reply
    looks again after the write and homes its rows itself."""
    platform = client.app.state.platform
    turn, thread = f"turn_mid_{lane}", f"thread_mid_{lane}"
    real = R.write
    linked: list[int] = []

    def link_then_write(engine, checks, **owner):
        if owner.get("turn_id") == turn and not linked:
            # On the record's own worker thread, as the page's request would arrive.
            r = client.post(f"/chat/turns/{turn}/thread", json={"thread_id": thread})
            linked.append(r.status_code)
            assert owner.get("thread_id") == "", "the reply already knew its chat: this is not the late link"
        return real(engine, checks, **owner)

    monkeypatch.setattr(R, "write", link_then_write)
    body, _ = _write_turn(client, monkeypatch, lane, GOOD_SHEET, turn_id=turn)
    assert linked == [200], "the link was not made during the write"
    assert body["checked"]["line"].startswith("Checked by Iron Jarvis:")
    assert _owners(platform, turn) == {(thread, turn)}, "rows written while the chat was being named have no chat"


@pytest.mark.parametrize("lane", LANES)
def test_a_chat_named_after_its_first_reply_ended_gets_that_replys_rows(client, monkeypatch, lane):
    platform = client.app.state.platform
    turn, thread = f"turn_first_{lane}", f"thread_named_{lane}"
    _write_turn(client, monkeypatch, lane, GOOD_SHEET, turn_id=turn)
    assert R.for_thread(platform.engine, thread) == [] and _owners(platform, turn) == {("", turn)}
    r = client.post(f"/chat/turns/{turn}/thread", json={"thread_id": thread})
    assert r.status_code == 200, r.text
    homed = R.for_thread(platform.engine, thread)
    assert {row["turn_id"] for row in homed} == {turn} and len(homed) == 3
    assert _owners(platform, turn) == {(thread, turn)}


def test_the_link_route_homes_the_rows_after_it_has_made_the_link_readable():
    """The other half of the late link: a reply still writing reads the link
    (``egress.linked_thread``), and the route homes the rows already written.
    If the route homed FIRST, a row written between its homing and its link
    would be seen by neither."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "routes" / "chat.py").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    route = source[source.index("async def link_chat_turn("):]
    route = route[:route.index("\n    @app.")]
    assert route.index("link_turn_holds(turn_id, tid)") < route.index("_checks.home_turn, d.platform.engine, turn_id, tid")


async def test_a_link_cancelled_while_the_rows_are_homed_has_already_kept_its_local_only_holds(tmp_path, monkeypatch):
    """PRIVACY. ``link_turn_holds`` hands a new chat's pending Local-only
    holds over ONCE. The homing of the checks was awaited between taking
    them and writing them: a request cancelled at that await (the page
    closed, the app stopping) lost the hold, and the chat that read a
    mailbox kept on the user's own models was no longer held Local only.
    Driven: the request is cancelled exactly while the homing runs."""
    import asyncio
    import threading

    from iron_jarvis.core.models import ChatThreadRecord
    from iron_jarvis.providers import egress
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        db.add(ChatThreadRecord(id="thread_hold", title="t", messages_json="[]"))
        db.commit()
    turn = "turn_hold_cancel"
    assert egress.note_pending_hold(turn, "ms365") == "", "the turn was already linked"
    reached, release = threading.Event(), threading.Event()
    real = R.home_turn

    def slow_home(*a, **kw):
        reached.set()
        release.wait(20)
        return real(*a, **kw)

    monkeypatch.setattr(R, "home_turn", slow_home)
    request = asyncio.create_task(_asgi_post(app, f"/chat/turns/{turn}/thread", {"thread_id": "thread_hold"}))
    try:
        assert await asyncio.to_thread(reached.wait, 20), "the link never reached the homing of the checks"
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        release.set()
    assert egress.thread_holds(engine, "thread_hold") == ["ms365"], (
        "a cancelled link lost the chat's Local-only hold")
    assert egress.pending_holds_of(turn) == [], "the hold was taken and (had it been lost) could not be taken again"


async def test_a_link_cancelled_while_the_rows_are_homed_has_already_switched_the_chat_to_local_only(
        tmp_path, monkeypatch):
    """PRIVACY, the chat's OWN switch. A new chat's first turn that turned
    Local only on from chat leaves the choice waiting until the chat is
    linked; it is taken ONCE. The homing of the checks is awaited only after
    that choice is on the chat, so a request cancelled at the homing cannot
    lose it."""
    import asyncio
    import threading

    from iron_jarvis.core.models import ChatThreadRecord
    from iron_jarvis.providers import egress
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        db.add(ChatThreadRecord(id="thread_switch", title="t", messages_json="[]"))
        db.commit()
    turn = "turn_switch_cancel"
    assert egress.note_pending_local_only(turn, True) == "", "the turn was already linked"
    assert egress.thread_local_only(engine, "thread_switch") is False
    reached, release = threading.Event(), threading.Event()
    seen: dict = {}
    real = R.home_turn

    def slow_home(*a, **kw):
        seen["local_only"] = egress.thread_local_only(engine, "thread_switch")
        reached.set()
        release.wait(20)
        return real(*a, **kw)

    monkeypatch.setattr(R, "home_turn", slow_home)
    request = asyncio.create_task(_asgi_post(app, f"/chat/turns/{turn}/thread", {"thread_id": "thread_switch"}))
    try:
        assert await asyncio.to_thread(reached.wait, 20), "the link never reached the homing of the checks"
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        release.set()
    assert seen["local_only"] is True, "the checks were homed before the chat's own Local only switch was written"
    assert egress.thread_local_only(engine, "thread_switch") is True, "a cancelled link lost the chat's Local only switch"
    assert egress.take_pending_local_only(turn) is None


# --------------------------------------------------------------------------- #
# S6: a hand-off and a coding reply get no plain-chat line from this path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_a_reply_that_hands_off_to_an_agent_run_gets_no_line_and_writes_no_row(client, monkeypatch, lane):
    """The run it hands off to checks the files (when it is settled); the
    reply itself claims nothing, even though its own tool wrote a file."""
    platform = client.app.state.platform
    _scripted_router(platform, monkeypatch, [
        [ToolCall(id="c1", name="write_document", arguments={"path": "summary.xlsx", "content": ERROR_SHEET})],
        [ToolCall(id="c2", name="escalate_to_agent", arguments={"reason": "this needs several more steps"})],
    ], "handing off")
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "make me a summary sheet of this"}],
        "tools": ["write_document"], "approval_mode": "yolo", "turn_id": f"turn_esc_{lane}",
    })
    assert body["escalate"] is True and body["documents"], "the scripted reply did not write a file and hand off"
    assert "checked" not in body, "a reply that handed off shows a line of its own"
    assert [r for r in _rows(platform) if r.turn_id == f"turn_esc_{lane}"] == []


@pytest.mark.parametrize("lane", LANES)
async def test_a_coding_reply_gets_no_plain_chat_line_and_no_check_of_what_its_file_says(tmp_path, monkeypatch, lane):
    """A coding session has its own line (its finish check: tests, a review).
    The plain-chat line, and the rule checks of what a made file says, are
    not added to it."""
    import json as _json

    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from iron_jarvis.platform import build_platform
    from tests.test_build_sessions_v1367 import CLAUDE, Script
    from tests.test_wave3_chat_lanes_v1311 import _parse_sse

    work = tmp_path / "work"
    work.mkdir()
    platform = build_platform(str(tmp_path / "home"))
    script = Script([[ToolCall(id="w", name="Write", arguments={"file_path": "notes.md", "content": "hello\n"})]])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    body = ChatBody(messages=[ChatMessageBody(role="user", content="write it")], provider=CLAUDE[0], model=CLAUDE[1],
                    workspace_dir=str(work), approval_mode="yolo", turn_id=f"turn_code_{lane}")
    kw = {"harness": True, "session_id": "build:lane"}
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
        raw = "".join([chunk if isinstance(chunk, str) else chunk.decode("utf-8") async for chunk in gen])
        out = [d for e, d in _parse_sse(raw) if e == "done"][-1]
    else:
        out = await run_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    assert (work / "notes.md").read_text(encoding="utf-8") == "hello\n", "the scripted coding reply wrote no file"
    assert [str(d).replace("\\", "/").rsplit("/", 1)[-1] for d in out["documents"]] == ["notes.md"], _json.dumps(out)[:400]
    assert "checked" not in out, "a coding reply carries the plain-chat line"
    assert not [r for r in _rows(platform) if r.check in R.CONTENT_CHECKS], "a coding reply's file got the rule checks"


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
    assert all(c["result"] != "passed" for c in body["checked"]["checks"] if c["label"] == "“summary.xlsx”")


# --------------------------------------------------------------------------- #
# The fifth review, through both lanes and the app's own tools
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_a_date_typed_as_a_number_under_a_date_format_is_never_a_problem(client, monkeypatch, lane):
    """B1. ``write_document`` makes the sheet and ``excel_edit`` (which
    offers ``number_format``) puts 20261126 in under yyyy-mm-dd. The file
    stores a number; the line read "Problem: B2 holds #VALUE!"."""
    import json
    import zipfile

    platform = client.app.state.platform
    _scripted_router(platform, monkeypatch, [
        [ToolCall(id="c1", name="write_document", arguments={
            "path": "invoices.xlsx", "content": [["Invoice", "Date", "Amount"], ["A-1", "", 100], ["A-2", "", 250]]})],
        [ToolCall(id="c2", name="excel_edit", arguments={"path": "invoices.xlsx", "edits": [
            {"cell": "B2", "value": 20261126, "number_format": "yyyy-mm-dd"},
            {"cell": "B3", "value": 20261127, "number_format": "yyyy-mm-dd"}]})],
    ], "Done: I saved invoices.xlsx with the two invoices and their dates.")
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "make me a sheet of these two invoices with their dates"}],
        "tools": ["write_document", "excel_edit"], "approval_mode": "yolo"})
    (doc,) = body["documents"]
    with zipfile.ZipFile(doc) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml")
    assert b"<v>20261126</v>" in sheet and b't="e"' not in sheet, "the tool stored something else: read the test again"
    checked = body["checked"]
    assert checked["line"] == ("Checked by Iron Jarvis: the spreadsheet opens. "
                               "Not checked: the spreadsheet (B2 holds a date this check cannot read).")
    assert checked["tone"] == "quiet" and "failed" not in [c["result"] for c in checked["checks"]]
    assert "#VALUE!" not in json.dumps(checked)


def test_two_new_chats_that_send_one_turn_id_get_their_own_lines(client, monkeypatch):
    """B6, through the POST lane: chat A (new, Local only) made
    client-payroll.xlsx; chat B (new, the same turn id) got A's file name
    and figures on its line."""
    import json

    a, _ = _write_turn(client, monkeypatch, "post", WRONG_SHEET, name="client-payroll.xlsx",
                       final="Done: I saved client-payroll.xlsx.", local_only=True, turn_id="same-turn")
    b, _ = _write_turn(client, monkeypatch, "post", GOOD_SHEET, name="plan.xlsx", final="Done: I saved plan.xlsx.",
                       turn_id="same-turn")
    assert "1,700" in a["checked"]["line"]
    said = json.dumps(b["checked"], ensure_ascii=False)
    assert "client-payroll" not in said and "1,700" not in said, said
    assert b["checked"]["line"].startswith("Checked by Iron Jarvis: the spreadsheet opens, no error cells")
    assert {c["label"] for c in b["checked"]["checks"]} == {"“plan.xlsx”"}
    rows = _rows(client.app.state.platform)
    assert {(r.file, r.kept_home) for r in rows if r.file} == {("client-payroll.xlsx", True), ("plan.xlsx", False)}
