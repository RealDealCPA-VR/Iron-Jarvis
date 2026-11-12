"""v1.481.0: a task's deliverables are checked on disk, its write rules hold,
a failed step is a failed step, and the reply's last words come from the check.

THE LIVE CASE (2026-11-12, a plain chat, the chat's folder = the task's
``INPUT``): two of three required files never written, a helper script that
began ``import base`` written and never run, a stray ``INPUT\\OUTPUT\\``, and a
reply that called the task complete.

The ACCEPTANCE test drives the REAL chat lanes (both) with a scripted model
that is given a task with three required files and a helper script with a bad
import, and checks: the missing files are found, the model is asked once to
finish, the failure is in the note under the reply, the reply never ends on
"complete" without the note saying it is not, and nothing is written outside
the allowed output folder.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.deliverables import check as C
from iron_jarvis.deliverables import scope as S
from iron_jarvis.deliverables import steps as ST
from iron_jarvis.tools.base import ToolResult
from tests.test_script_power_lanes_v1473 import Model, _call, _post, _stream

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

# The user's own message from the live case, word for word.
LIVE_TASK = """The Q4 2025 budget-vs-actual workbook in `INPUT/` is broken. Read the controller's note, repair the workbook so it is correct and stays correct
when the data changes, and document every change.

## Success criteria (deliverables)
1. `OUTPUT/Q4_2025_Budget_vs_Actual_REPAIRED.xlsx` - same sheets (`Data`, `Summary`, `Dashboard`) and layout as the original, repaired.
   Summary and Dashboard figures must be live formulas, with no error values.
2. `OUTPUT/change_log.md` - every problem found and what you changed.
3. `OUTPUT/FINAL_REPORT.md`.

## Permissions
- READ `INPUT/`. Do **not** overwrite or modify the original workbook or the email.
- CREATE files only in `OUTPUT/`.

## Completion requirements
When finished, write `OUTPUT/FINAL_REPORT.md` containing:
- **Actions performed** - what you did, in order.
- **Output locations** - every file you created.
- **Unresolved exceptions** - anything you could not resolve, items needing human review or approval.
- **Verification** - how you checked your results and what the checks showed.

Be accurate: report partial completion honestly. Do not claim work you did not do.
"""

# The acceptance task: three required files, one output folder.
TASK = """Summarize the sales in `INPUT/`.

## Deliverables
1. `OUTPUT/summary.xlsx` - one row per department.
2. `OUTPUT/change_log.md` - what you did.
3. `OUTPUT/FINAL_REPORT.md` - actions, outputs, open issues, verification.

## Permissions
- READ `INPUT/`. Do not modify anything in it.
- CREATE files only in `OUTPUT/`.
"""


@pytest.fixture()
def task(tmp_path):
    root = tmp_path / "Sales_Task"
    inp = root / "INPUT"
    out = root / "OUTPUT"
    inp.mkdir(parents=True)
    out.mkdir()
    (inp / "sales.csv").write_text("Dept,Amount\nSales,10\nIT,5\n", encoding="utf-8")
    (inp / "note.txt").write_text("Please summarize.\n", encoding="utf-8")
    (root / "README.md").write_text(TASK, encoding="utf-8")
    return root


def _tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        out[rel] = "dir" if p.is_dir() else hashlib.sha1(p.read_bytes()).hexdigest()
    return out


# --------------------------------------------------------------------------- #
# Reading the user's message
# --------------------------------------------------------------------------- #
def test_the_live_message_gives_three_files_one_write_folder_and_the_task_folder(task):
    ws = task / "INPUT"
    scope = S.scope_for_text(LIVE_TASK, ws)
    assert scope is not None
    assert scope.rebase and scope.root == task, "`INPUT/` named while the chat is IN INPUT"
    assert [scope.rel(d.path) for d in scope.deliverables] == [
        os.path.join("OUTPUT", "Q4_2025_Budget_vs_Actual_REPAIRED.xlsx"),
        os.path.join("OUTPUT", "change_log.md"),
        os.path.join("OUTPUT", "FINAL_REPORT.md"),
    ]
    assert scope.write_roots == (task / "OUTPUT",) and scope.rule == "said"
    assert scope.read_only == (task / "INPUT",)
    assert scope.scratch is not None and not S._within(scope.scratch, task)


def test_a_chat_already_in_the_task_folder_is_not_moved(task):
    scope = S.scope_for_text(TASK, task)
    assert scope is not None and not scope.rebase and scope.root == task
    assert len(scope.deliverables) == 3


@pytest.mark.parametrize("text", [
    "write a summary of report.pdf",
    "what's in data.csv?",
    "can you read notes.txt and tell me what it says",
    "write a Node.js script that prints hello",
    "convert report.docx to pdf",
    "make sure config.json is valid",
    "Do not write `OUTPUT/x.md` yet, just plan it.",
    "fix the bug in `src/app.py`",
    "here is the log: Traceback (most recent call last): ValueError: x",
    "read `INPUT/` and tell me what is there",
])
def test_ordinary_messages_name_no_task(task, text):
    assert S.scope_for_text(text, task) is None, text


@pytest.mark.parametrize("text, want", [
    ("save the totals as totals.csv", ["totals.csv"]),
    ("export the rows to `out/rows.csv`", ["out/rows.csv"]),
    ("create `report.md` from notes.txt", ["report.md"]),
    ("Please write `OUTPUT/FINAL_REPORT.md` when you are done.", ["OUTPUT/FINAL_REPORT.md"]),
    ("## Outputs\n- `results/a.xlsx`\n- `results/b.md`", ["results/a.xlsx", "results/b.md"]),
])
def test_files_to_produce_are_read(task, text, want):
    scope = S.scope_for_text(text, task)
    assert scope is not None, text
    assert [d.text for d in scope.deliverables] == want


def test_one_output_folder_is_the_write_rule_when_none_is_said(task):
    scope = S.scope_for_text("## Outputs\n- `results/a.xlsx`\n- `results/b.md`", task)
    assert scope.write_roots == (task / "results",) and scope.rule == "output folder"
    plain = S.scope_for_text("save the totals as totals.csv", task)
    assert plain.write_roots == () and not plain.enforced, "no rule said: writes are unchanged"


def test_continue_goes_back_to_the_task_message(task):
    msgs = [{"role": "user", "content": TASK}, {"role": "assistant", "content": "Working."},
            {"role": "user", "content": "continue"}]
    scope = S.scope_for_turn(msgs, task / "INPUT")
    assert scope is not None and len(scope.deliverables) == 3
    other = [{"role": "user", "content": TASK}, {"role": "user", "content": "what is 2+2?"}]
    assert S.scope_for_turn(other, task / "INPUT") is None


# --------------------------------------------------------------------------- #
# Refusing a write
# --------------------------------------------------------------------------- #
def test_writes_outside_the_output_folder_are_refused_and_scratch_is_not(task):
    scope = S.scope_for_text(TASK, task / "INPUT")
    ws = scope.root

    def refuse(name, **args):
        return S.write_refusal(name, args, ws, scope)

    assert refuse("write_file", path="OUTPUT/change_log.md") == ""
    assert refuse("write_document", path="OUTPUT/summary.xlsx") == ""
    assert "read-only" in refuse("write_file", path="INPUT/notes.txt")
    assert "may write only in" in refuse("write_file", path="draft.md")
    assert "helper script" in refuse("write_file", path="OUTPUT/_write_md.py")
    assert refuse("write_file", path=str(scope.scratch / "_write_md.py")) == ""
    assert "read-only" in refuse("edit_file", path="INPUT/sales.csv")
    assert "read-only" in refuse("rename_file", path="INPUT/sales.csv", new_path="OUTPUT/s.csv")
    assert refuse("convert_document", source="INPUT/sales.csv", target="OUTPUT/sales.xlsx") == ""
    assert "read-only" in refuse("convert_document", source="INPUT/sales.csv", target="INPUT/sales.xlsx")
    # Commands: a redirect, a copy, a delete into a read-only folder.
    assert "read-only" in refuse("shell", command="echo hi > INPUT\\x.txt")
    assert "read-only" in refuse("shell", command='copy OUTPUT\\a.md "INPUT\\a.md"')
    assert "read-only" in refuse("shell", command="del INPUT\\sales.csv")
    assert "read-only" in refuse("shell", command="mkdir INPUT\\OUTPUT")
    assert refuse("shell", command="python script.py 2>&1 > nul") == ""
    assert refuse("shell", command="type INPUT\\sales.csv") == ""
    assert refuse("shell", command="dir INPUT OUTPUT") == ""


def test_a_task_with_no_write_rule_refuses_nothing(task):
    scope = S.scope_for_text("save the totals as totals.csv", task)
    assert S.write_refusal("write_file", {"path": "anywhere/else.md"}, task, scope) == ""


# --------------------------------------------------------------------------- #
# The check on disk
# --------------------------------------------------------------------------- #
def test_each_required_file_is_checked_for_what_it_is(tmp_path):
    import openpyxl

    good = tmp_path / "a.xlsx"
    wb = openpyxl.Workbook()
    wb.active["A1"] = 1
    wb.save(good)
    (tmp_path / "bad.xlsx").write_text("not a workbook", encoding="utf-8")
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    (tmp_path / "blank.md").write_text("  \n\n", encoding="utf-8")
    (tmp_path / "ok.md").write_text("# Report\n", encoding="utf-8")
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "u16.md").write_bytes("# hi".encode("utf-16"))
    assert C.check_file(good)[0] == "ok"
    assert C.check_file(tmp_path / "bad.xlsx")[0] == "unreadable"
    assert C.check_file(tmp_path / "empty.md")[0] == "empty"
    assert C.check_file(tmp_path / "blank.md")[0] == "unreadable"
    assert C.check_file(tmp_path / "ok.md")[0] == "ok"
    assert C.check_file(tmp_path / "bad.json")[0] == "unreadable"
    assert C.check_file(tmp_path / "u16.md")[0] == "ok", "PowerShell 5.1 writes UTF-16"
    assert C.check_file(tmp_path / "nope.md")[0] == "missing"


def test_a_stray_folder_in_the_input_is_seen(task):
    scope = S.scope_for_text(TASK, task / "INPUT")
    (task / "INPUT" / "OUTPUT").mkdir()
    for n in ("_a.py", "_b.py"):
        (task / "INPUT" / "OUTPUT" / n).write_text("x", encoding="utf-8")
    (task / "OUTPUT" / "fine.md").write_text("x", encoding="utf-8")
    changes = C.outside_changes(scope)
    assert changes == [os.path.join("INPUT", "OUTPUT") + os.sep + " (a new folder, 2 files)"], changes


# --------------------------------------------------------------------------- #
# Failed steps
# --------------------------------------------------------------------------- #
TRACE = (
    "Traceback (most recent call last):\n  File \"x.py\", line 1, in <module>\n"
    "    import base\nModuleNotFoundError: No module named 'base'\nNEED_SCRIPT\n"
)


def test_a_printed_traceback_is_a_failed_step_even_when_the_exit_was_zero():
    assert ST.error_line(TRACE) == "ModuleNotFoundError: No module named 'base'"
    note = ST.failed_step_note("shell", ToolResult(ok=True, output=TRACE))
    assert "FAILED" in note and "No module named 'base'" in note
    assert ST.failed_step_note("read_file", ToolResult(ok=True, output=TRACE)) == "", (
        "a file that holds a traceback is not a failed step"
    )
    assert ST.failed_step_note("shell", ToolResult(ok=True, output="all fine")) == ""
    assert ST.error_line("python: can't open file 'C:\\x\\_verify.py': [Errno 2]").startswith("can't open file")


def test_a_command_that_hid_its_exit_status_is_kept_as_failed():
    log = ST.StepLog()
    log.note("shell", {"command": "py x.py 2>&1 || echo NEED_SCRIPT"}, ToolResult(ok=True, output=TRACE))
    assert log.failed_steps() == [
        ("shell `py x.py 2>&1 || echo NEED_SCRIPT`", "ModuleNotFoundError: No module named 'base'")
    ]


def test_do_not_modify_makes_a_folder_read_only_anywhere_in_the_message(task):
    scope = S.scope_for_text("Write `OUTPUT/a.md` with the totals. Do not modify `INPUT/`.", task)
    assert scope is not None and scope.read_only == (task / "INPUT",), scope
    assert "read-only" in S.write_refusal("write_file", {"path": "INPUT/x.md"}, task, scope)


def test_the_step_log_keeps_failures_not_redone_and_scripts_never_run():
    log = ST.StepLog()
    log.note("write_file", {"path": "s/_build.py"}, ToolResult(ok=True, output="wrote"))
    log.note("write_file", {"path": "s/_write_md.py"}, ToolResult(ok=True, output="wrote"))
    log.note("shell", {"command": "py s/_build.py"}, ToolResult(ok=False, output=TRACE, error="exit 1"))
    assert log.never_run() == ["_write_md.py"]
    assert log.failed_steps()[0][1] == "ModuleNotFoundError: No module named 'base'"
    log.note("shell", {"command": "py s/_build.py"}, ToolResult(ok=True, output="saved"))
    assert log.failed_steps() == [], "done again and it worked"


# --------------------------------------------------------------------------- #
# Linear on hostile input (each read runs on the user's message or a tool's
# output; a regex that backtracks would freeze the daemon)
# --------------------------------------------------------------------------- #
_HOSTILE = r"""
import time, sys
from iron_jarvis.deliverables import scope as S, steps as ST
pieces = ["a." * 64000, "a/" * 64000, "`" + "a" * 120000, ">" * 128000,
          "copy " + "a " * 60000, "Error:" * 20000, "Traceback (most recent call last):\n" + "x" * 128000,
          "write " + "a.b.c.d" * 18000, "OUTPUT/" * 18000, "-" * 128000 + "x.md"]
t = time.monotonic()
for p in pieces:
    S._read(p); S.command_targets(p); ST.error_line(p)
print(round(time.monotonic() - t, 2))
"""


def test_the_readers_are_linear_on_hostile_input():
    out = subprocess.run([sys.executable, "-c", _HOSTILE], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert float(out.stdout.strip()) < 20, out.stdout


# --------------------------------------------------------------------------- #
# THE ACCEPTANCE TEST, through the real lanes
# --------------------------------------------------------------------------- #
@pytest.fixture()
def desk(tmp_path, task, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    # The shell runs natively (never in a Docker container that has no copy
    # of this folder): the rule tests that drive `shell` already follow.
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:9")
    app = create_app(str(tmp_path / "home"))
    return app, task


def _scratch(model: Model) -> Path:
    m = re.search(r"scratch folder (\S+) \(outside the task folder\)", model.systems[-1])
    assert m, "the scope block names the scratch folder"
    return Path(m.group(1))


def _body(task: Path, *, tools, approval="approve_for_me", turn="turn-deliv-1") -> dict:
    return {"messages": [{"role": "user", "content": TASK}], "tools": tools, "auto_tools": False,
            "approval_mode": approval, "workspace_dir": str(task / "INPUT"), "turn_id": turn}


BAD_SCRIPT = "import base\nopen('OUTPUT/change_log.md', 'w').write('# log')\n"
SUMMARY = '[["Dept", "Total"], ["Sales", 10], ["IT", 5]]'


def _failing_steps(recover: bool):
    steps = [
        _call(1, "write_document", path="OUTPUT/summary.xlsx", content=SUMMARY),
        _call(2, "write_file", path="OUTPUT/_write_md.py", content=BAD_SCRIPT),
        lambda m: _call(3, "write_file", path=str(_scratch(m) / "_write_md.py"), content=BAD_SCRIPT),
        lambda m: _call(4, "shell", command=f'"{sys.executable}" "{_scratch(m) / "_write_md.py"}"'),
        _call(5, "write_file", path="INPUT/notes.txt", content="scratch notes"),
        "The repair is complete and verified. All three files are written.",
    ]
    if recover:
        steps += [
            [_call(6, "write_file", path="OUTPUT/change_log.md", content="# Change log\n- summarized\n")[0],
             _call(7, "write_file", path="OUTPUT/FINAL_REPORT.md",
                   content="# Final report\n## Actions performed\n- summarized\n")[0]],
            "Now all three files are written.",
        ]
    else:
        steps += ["Done."]
    return steps


def _assert_nothing_outside(task: Path, before: dict, *, made: set):
    after = _tree(task)
    for rel, digest in before.items():
        assert after.get(rel) == digest, f"{rel} changed"
    new = {rel for rel in after if rel not in before}
    assert new == {os.path.join("OUTPUT", m) for m in made}, new
    assert not (task / "INPUT" / "OUTPUT").exists(), "the stray folder of the live case"


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_acceptance_three_files_a_bad_import_and_nothing_outside_output(desk, recover):
    app, task = desk
    before = _tree(task)
    model = Model(_failing_steps(recover))
    body = _body(task, tools=["write_document", "write_file", "shell"], turn=f"turn-acc-{recover}")
    _frames, _cards, done = await _stream(app, body, model, decision="once")
    reply = done["reply"]
    said = [t for texts in model.tool_texts for t in texts]

    # The scratch script ran and FAILED: the model was told, and the note says so.
    assert any("No module named 'base'" in t for t in said), said
    assert "ModuleNotFoundError: No module named 'base'" in reply, reply
    # The writes outside OUTPUT were refused, in words, before anything ran.
    assert any(t.startswith("NOTHING WAS WRITTEN.") and "helper script" in t for t in said), said
    assert any("read-only" in t for t in said), said
    assert "Writes Iron Jarvis refused" in reply
    # The model ended on "complete" with two files missing: asked ONCE to finish.
    assert model.steps == [], "every scripted step was asked for (the check went on once)"
    if recover:
        assert "All 3 required files are on disk and open." in reply, reply
        assert "NOT complete" not in reply
        _assert_nothing_outside(task, before, made={"summary.xlsx", "change_log.md", "FINAL_REPORT.md"})
    else:
        assert "The task is NOT complete: 2 of 3 required files are not done." in reply, reply
        assert os.path.join("OUTPUT", "change_log.md") + " (not on disk)" in reply
        assert os.path.join("OUTPUT", "FINAL_REPORT.md") + " (not on disk)" in reply
        _assert_nothing_outside(task, before, made={"summary.xlsx"})


@pytest.mark.asyncio
async def test_acceptance_in_the_post_lane(desk):
    app, task = desk
    before = _tree(task)
    model = Model([
        _call(1, "write_document", path="OUTPUT/summary.xlsx", content=SUMMARY),
        _call(2, "write_file", path="OUTPUT/_write_md.py", content=BAD_SCRIPT),
        _call(3, "write_file", path="INPUT/notes.txt", content="x"),
        "The task is complete.",
        "Done.",
    ])
    out = await _post(app, _body(task, tools=["write_document", "write_file"], turn="turn-acc-post"), model)
    reply = out["reply"]
    assert model.steps == [], "the check went on once"
    assert "The task is NOT complete: 2 of 3 required files are not done." in reply, reply
    assert "Writes Iron Jarvis refused" in reply
    _assert_nothing_outside(task, before, made={"summary.xlsx"})


@pytest.mark.asyncio
async def test_a_chat_with_no_task_is_unchanged(desk):
    app, task = desk
    model = Model([_call(1, "write_file", path="notes.md", content="hi"), "Saved notes.md."])
    body = {"messages": [{"role": "user", "content": "jot down hi in a file"}],
            "tools": ["write_file"], "auto_tools": False, "approval_mode": "approve_for_me",
            "workspace_dir": str(task / "INPUT"), "turn_id": "turn-plain"}
    out = await _post(app, body, model)
    assert "Deliverables check" not in out["reply"]
    assert (task / "INPUT" / "notes.md").exists(), "an ordinary chat writes where it always did"
    assert "This task's required files" not in model.systems[0]
