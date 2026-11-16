"""v1.503.0 (north-star fix plan 3.9) — file claims are checked BY NAME.

``chat_turn._claimed_write_names`` (the reading behind the note under a reply
and the check-again loop, ``daemon/until_done``) used to say "nothing claimed
falsely" as soon as ANY file-changing tool succeeded this reply. "I saved
notes.md and budget.xlsx" passed when only notes.md was written.

Now, when the reply holds a complete record of what it wrote (both lanes keep
every path a successful file tool reported: ``made_docs``), each claimed name
is compared with that record: a saved name in neither the record nor the
folder is marked, and so is a changed name no tool of this reply changed.
Kept as before, on purpose, where there is no record to compare with: a
command, code, an app's tool or a user-made tool ran (it may have written
anything), a tool that changes files without naming them ran, or a file
tool succeeded without saying which file it wrote.
"""

# ruff: noqa: F811 — `desk` is a fixture imported from another test file
from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import until_done as U
from iron_jarvis.tools.base import ToolResult
from tests.test_script_power_lanes_v1473 import (  # noqa: F401 — `desk` is the fixture
    Model,
    _body,
    _call,
    _post,
    _stream,
    desk,
)

pytestmark = pytest.mark.timeout(300)

TWO = "I saved notes.md and budget.xlsx in your folder."


def names(reply, used, ws: Path, written, **kw):
    return CT._claimed_write_names(reply, used, written=written, workspace=str(ws), **kw)


# --------------------------------------------------------------------------- #
# The reading.
# --------------------------------------------------------------------------- #


def test_a_second_claimed_file_that_was_never_written_is_marked(tmp_path):
    (tmp_path / "notes.md").write_text("n", encoding="utf-8")
    saved, changed = names(TWO, ["write_file"], tmp_path, [str(tmp_path / "notes.md")])
    assert saved == ["budget.xlsx"] and changed == []


def test_a_real_save_of_both_is_never_marked(tmp_path):
    for n in ("notes.md", "budget.xlsx"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    written = [str(tmp_path / "notes.md"), str(tmp_path / "budget.xlsx")]
    assert names(TWO, ["write_file", "write_document"], tmp_path, written) == ([], [])


def test_a_claimed_name_already_in_the_folder_is_not_marked(tmp_path):
    """Not in the record, but on disk: it may be an earlier reply's file — the
    conservative side keeps quiet."""
    (tmp_path / "notes.md").write_text("n", encoding="utf-8")
    (tmp_path / "budget.xlsx").write_text("b", encoding="utf-8")
    assert names(TWO, ["write_file"], tmp_path, [str(tmp_path / "notes.md")]) == ([], [])


def test_a_claimed_name_in_a_subfolder_that_exists_is_not_marked(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "budget.xlsx").write_text("b", encoding="utf-8")
    (tmp_path / "notes.md").write_text("n", encoding="utf-8")
    reply = "I saved notes.md and reports/budget.xlsx."
    assert names(reply, ["write_file"], tmp_path, [str(tmp_path / "notes.md")]) == ([], [])


def test_the_record_matches_by_file_name_whatever_its_folder(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "budget.xlsx").write_text("b", encoding="utf-8")
    reply = "I saved budget.xlsx to the out folder."
    assert names(reply, ["write_document"], tmp_path, [str(out / "budget.xlsx")]) == ([], [])


def test_a_changed_name_no_tool_changed_is_marked_and_a_real_change_is_not(tmp_path):
    for n in ("notes.md", "budget.xlsx"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    reply = "I updated notes.md and I updated budget.xlsx."
    saved, changed = names(reply, ["edit_file"], tmp_path, [str(tmp_path / "notes.md")])
    assert saved == [] and changed == ["budget.xlsx"], (saved, changed)
    both = [str(tmp_path / "notes.md"), str(tmp_path / "budget.xlsx")]
    assert names(reply, ["edit_file"], tmp_path, both) == ([], [])


def test_no_record_keeps_the_old_conservative_answer(tmp_path):
    (tmp_path / "notes.md").write_text("n", encoding="utf-8")
    # No record handed over (an older caller, the orchestrator): as before.
    assert CT._claimed_write_names(TWO, ["write_file"]) == ([], [])
    # A command ran: it may have written budget.xlsx itself.
    assert names(TWO, ["write_file", "shell"], tmp_path, [str(tmp_path / "notes.md")]) == ([], [])
    # A tool that changes files without naming them ran.
    assert names(TWO, ["write_file", "notebook"], tmp_path, [str(tmp_path / "notes.md")]) == ([], [])
    # An app's tool ran.
    assert names(TWO, ["write_file", "mcp__drive__upload"], tmp_path, [str(tmp_path / "notes.md")]) == ([], [])


def test_with_no_file_tool_the_reading_is_unchanged(tmp_path):
    """No file-changing tool succeeded: every claimed name is marked, as it
    always was (no folder is looked in)."""
    (tmp_path / "budget.xlsx").write_text("b", encoding="utf-8")
    assert names(TWO, ["read_file"], tmp_path, [])[0] == ["notes.md", "budget.xlsx"]


def test_an_offer_a_question_a_denial_and_the_users_own_file_are_never_claims(tmp_path):
    (tmp_path / "notes.md").write_text("n", encoding="utf-8")
    w = [str(tmp_path / "notes.md")]
    for reply in (
        "I saved notes.md. I can also save it as budget.xlsx if you like.",
        "I saved notes.md. Should I create budget.xlsx too?",
        "I saved notes.md. I did not create budget.xlsx.",
        "I saved notes.md, using the figures you put in budget.xlsx.",
    ):
        assert names(reply, ["write_file"], tmp_path, w) == ([], []), reply


def test_the_record_must_be_whole(tmp_path):
    """A file tool that succeeded without naming its file leaves the record
    incomplete: the lanes then hand no record over (``write_record_gap``)."""
    assert CT.write_record_gap("write_file", ToolResult(ok=True, output="", data={})) is True
    assert CT.write_record_gap("rename_file", ToolResult(ok=True, output="", data={"path": "a.md"})) is True
    assert CT.write_record_gap("write_document", ToolResult(ok=True, output="", data={"path": "a.md"})) is False
    assert CT.write_record_gap(
        "write_file", ToolResult(ok=True, output="", data={"path": "a.md"}, created_paths=[str(tmp_path / "a.md")])
    ) is False
    assert CT.write_record_gap(
        "write_file", ToolResult(ok=True, output="", created_paths=["a.md"])  # a relative name names nothing
    ) is True
    assert CT.write_record_gap(
        "repl", ToolResult(ok=True, output="", created_paths=[str(tmp_path / "x.csv")])
    ) is False
    assert CT.write_record_gap("read_file", ToolResult(ok=True, output="", data={})) is False


def test_the_note_under_the_reply_names_only_the_missing_file(tmp_path):
    (tmp_path / "notes.md").write_text("n", encoding="utf-8")
    note = CT._claimed_write_note(
        TWO, ["write_file"], written=[str(tmp_path / "notes.md")], workspace=str(tmp_path),
    )
    assert "`budget.xlsx`" in note and "`notes.md`" not in note, note
    assert "nothing was written to disk" not in note, note  # notes.md WAS written
    assert "no document-writing tool ran" not in note, note


def test_the_check_again_loop_reads_the_same_record(tmp_path):
    (tmp_path / "notes.md").write_text("n", encoding="utf-8")
    facts = U.Facts(
        reply=TWO, tools_used=["write_file"], armed={"write_file"}, ran_writer=False,
        written=[str(tmp_path / "notes.md")], workspace=str(tmp_path),
    )
    probs = U.find_problems(facts)
    assert [p.key for p in probs] == ["claimed_file:budget.xlsx"], probs
    assert "`budget.xlsx` is saved, but no tool wrote it" in probs[0].words
    (tmp_path / "budget.xlsx").write_text("b", encoding="utf-8")
    facts.written.append(str(tmp_path / "budget.xlsx"))
    assert U.find_problems(facts) == []


def test_the_loop_says_a_changed_file_was_not_changed_without_a_false_reason(tmp_path):
    for n in ("notes.md", "budget.xlsx"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    facts = U.Facts(
        reply="I updated notes.md and I updated budget.xlsx.", tools_used=["edit_file"],
        armed={"edit_file"}, ran_writer=False, written=[str(tmp_path / "notes.md")],
        workspace=str(tmp_path),
    )
    (p,) = U.find_problems(facts)
    assert p.key == "changed_file:budget.xlsx"
    assert "no tool that changes files succeeded" not in p.words, p.words  # edit_file did


# --------------------------------------------------------------------------- #
# Both lanes, the real registry: write notes.md, claim notes.md and budget.xlsx.
# --------------------------------------------------------------------------- #


LANES = pytest.mark.parametrize("lane", ["stream", "post"])


async def _run(lane, app, body, model):
    if lane == "stream":
        _frames, _cards, done = await _stream(app, body, model)
        return done
    return await _post(app, body, model)


class _Seen(Model):
    """The scripted model, also keeping the user messages it was sent."""

    def _see(self, system, messages, tools) -> None:
        super()._see(system, messages, tools)
        self.users = [str(getattr(m, "content", "") or "") for m in messages or []
                      if getattr(m, "role", "") == "user"]


@LANES
async def test_lane_a_claim_beyond_what_was_written_is_sent_back_then_noted(desk, lane):
    """The loop sends the reply back naming budget.xlsx (the model keeps
    claiming it), and the note under the finished reply names it too —
    never notes.md, which was written."""
    app, ws = desk
    model = _Seen([_call(1, "write_file", path="notes.md", content="the notes"), TWO, TWO, TWO])
    body = _body(ws, "jot these notes down", tools=["write_file"], auto_tools=False)
    done = await _run(lane, app, body, model)
    assert "write_file" in done["tools_used"], done["tools_used"]
    assert (ws / "notes.md").is_file() and not (ws / "budget.xlsx").exists()
    notes = [u for u in model.users if u.startswith(U.NOTE_HEAD)]
    assert notes and "`budget.xlsx` is saved, but no tool wrote it" in notes[0], model.users
    assert "notes.md" not in notes[0], notes[0]
    tail = done["reply"].split("_Note:", 1)[-1]
    assert "`budget.xlsx`" in tail and "`notes.md`" not in tail, done["reply"]


@LANES
async def test_lane_a_true_claim_of_what_was_written_gets_no_note(desk, lane):
    app, ws = desk
    model = Model([_call(1, "write_file", path="notes.md", content="the notes"), "I saved notes.md."])
    body = _body(ws, "jot these notes down", tools=["write_file"], auto_tools=False)
    done = await _run(lane, app, body, model)
    assert "write_file" in done["tools_used"], done["tools_used"]
    assert "_Note:" not in done["reply"], done["reply"]


def test_both_lanes_hand_the_record_to_the_note_and_the_loop():
    """MIRROR NOTE (lock-step): the two lanes, read as source."""
    root = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"
    for f in (root / "chat_turn.py", root / "routes" / "chat.py"):
        src = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert src.count("written=None if _write_gap else made_docs") == 2, f
        assert src.count("write_record_gap(tc.name, result)") == 1, f
