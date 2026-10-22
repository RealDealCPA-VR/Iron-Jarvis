"""v1.370.0 review of the Harness² refine loop (``bench-harness run --refine``).

Each pin is a defect the review found, driven through the real benchmark and
its real grader with a scripted model (never a real one):

* a failed RUBRIC's reason quoted the hidden answer key into the evidence the
  improver reads ("no mention of '<required phrase>'", "file is 'main.py'"
  — which keys were wrong): a rubric failure now says only that the answer
  is wrong;
* an agent's own tool output that carries a hidden check (a shell can read
  one by its full path, or an earlier run's grading copy) was copied into
  the evidence as it was: every trajectory line carrying the grader is
  removed, and the finished tree goes through the tripwire before any
  proposer reads it (refused and removed otherwise);
* a PROBE whose own budget did not add up stopped the loop before the refine
  ledger, results and report were written — while the command line said
  "the results were written";
* the report never said that one run per task is a small sample, and
  "parallel" read as running at once (it proposes and tries one at a time);
* ``bench-harness adopt`` followed a link out of the project folder (a
  ``.ironjarvis`` junction, a dangling ``memory.md`` link) and created a
  folder that was not there.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iron_jarvis.harness_bench import refine as R
from iron_jarvis.harness_bench.bench import TRAJECTORY_NAME, BenchRefused
from iron_jarvis.harness_bench.catalog import HIDDEN_CANARY, load_tasks
from iron_jarvis.harness_bench.ledger import BudgetLedger, BudgetMismatch
from iron_jarvis.harness_bench.tripwire import HiddenCheckLeak, leaks
from tests.test_harness_refine_v1370 import NAMES, TASKS, A, _proposal_moves, _refine, _setup

ALL = load_tasks()
SECRET = "SECRET-ANSWER-PHRASE-7Q"


def _tree_text(folder: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.rglob("*")) if p.is_file())


# --------------------------------------------------------------------------- #
# the grader's material never reaches the evidence
# --------------------------------------------------------------------------- #


async def test_a_failed_rubric_never_shows_the_expected_answer_in_the_evidence(tmp_path, monkeypatch):
    platform, model = _setup(tmp_path, monkeypatch, {0: _proposal_moves(A)})
    model.careless_if = lambda system: True  # every run answers the explain task wrongly
    tasks = [
        t if t.id != "explain-export-command"
        else type(t)(**{**t.__dict__, "rubric": {**t.rubric, "required_phrases": [SECRET]}})
        for t in ALL
    ]
    res = await _refine(platform, k=1, tasks=tasks)
    ev = res.folder / "evidence" / "round-1"
    traj = (ev / "tasks" / "explain-export-command" / "current.md").read_text(encoding="utf-8")
    assert "Check: failed" in traj and R.RUBRIC_WRONG in traj
    text = _tree_text(ev)
    # Neither the required phrase nor which keys the answer key holds.
    assert SECRET not in text
    assert "file is" not in text and "function is" not in text
    # The proposer's own copy is the same tree.
    copy = res.folder / "candidates" / "c1" / "attempt-1" / "evidence"
    assert SECRET not in _tree_text(copy)


def test_the_evidence_reason_keeps_only_the_fixed_check_words():
    check = {"passed": False, "grade_reason": "the check failed",
             "grade_parts": [{"kind": "check", "passed": False, "reason": "the check failed"}]}
    assert R.evidence_grade_reason(check) == "the check failed"
    slow = "the check ran past its 60 s limit (the code it tests did not finish)"
    assert R.evidence_grade_reason({"passed": False, "grade_parts": [
        {"kind": "check", "passed": False, "reason": slow}]}) == slow
    wrong = {"passed": False, "grade_parts": [
        {"kind": "check", "passed": True, "reason": "the check passed"},
        {"kind": "rubric", "passed": False, "reason": f"the answer is wrong: no mention of '{SECRET}'"}]}
    assert R.evidence_grade_reason(wrong) == R.RUBRIC_WRONG
    missing = {"passed": False, "grade_parts": [
        {"kind": "rubric", "passed": False, "reason": "answer.json was not written"}]}
    assert R.evidence_grade_reason(missing) == "answer.json was not written"
    # A record from before the parts were kept: the free text is judged too.
    assert R.evidence_grade_reason({"passed": False, "grade_reason": f"no mention of '{SECRET}'"}) == R.RUBRIC_WRONG
    assert R.evidence_grade_reason({"passed": None, "grade_reason": "did not run: no model"}) == "did not run: no model"


def _leaky_trajectory(task) -> list[dict]:
    """What an agent's run would record had its shell printed a hidden check
    file: the whole file, the file with its first (marker) line cut, one
    distinctive line, and the file named in a listing and in a call's args."""
    steps: list[dict] = [{"kind": "call", "id": "s0", "tool": "shell", "args": {"command": "dir"}}]
    for i, path in enumerate(p for p in task.hidden_files() if task.check_dir in p.parents):
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        cut = "\n".join(text.splitlines()[1:])
        line = next(ln for ln in cut.splitlines() if len(ln.strip()) >= 30)
        steps += [
            {"kind": "result", "id": f"a{i}", "tool": "shell", "ok": True, "output": text},
            {"kind": "result", "id": f"b{i}", "tool": "shell", "ok": True, "output": cut},
            {"kind": "result", "id": f"c{i}", "tool": "shell", "ok": True,
             "output": f"Directory of grade\n{path.name}\n"},
            {"kind": "call", "id": f"d{i}", "tool": "write_file",
             "args": {"path": "notes.py", "content": "x = 1\n" + line + "\n"}},
            {"kind": "text", "text": "I found this: " + line.strip()},
        ]
    steps.append({"kind": "reply", "text": "Done."})
    return steps


def test_an_agent_output_carrying_a_hidden_check_is_scrubbed_from_every_tasks_evidence(tmp_path):
    """Built from EVERY real task's real hidden check: the evidence keeps
    nothing of it — no marker, no check file's name, none of its
    distinctive lines — and the tripwire finds nothing."""
    cfg = tmp_path / "cfg"
    records = []
    for task in ALL:
        run = cfg / "runs" / task.id
        run.mkdir(parents=True)
        (run / TRAJECTORY_NAME).write_text(json.dumps(_leaky_trajectory(task)), encoding="utf-8")
        records.append({"task_id": task.id, "passed": False, "grade_reason": "the check failed",
                        "grade_parts": [{"kind": "check", "passed": False, "reason": "the check failed"}]})
    probed = [R.Probed(label="current", layer_base=None, files={}, config_dir=cfg, records=records,
                       score=R.score_of(records))]
    ev = R.build_evidence(tmp_path / "evidence", ALL, probed, {"current": "# IJ harness\n"})
    text = _tree_text(ev)
    assert HIDDEN_CANARY not in text
    assert R.SCRUBBED_LINE in text
    for task in ALL:
        assert leaks(task, ev, "") == [], task.id
        marks = set(R.grader_marks([task]))
        distinctive = 0
        for path in task.hidden_files():
            if task.check_dir in path.parents:
                assert path.name not in text, (task.id, path.name)
                body = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
                for line in body.splitlines():
                    probe = line.strip()
                    if probe in marks:
                        distinctive += 1
                        assert probe not in text, (task.id, probe)
        if task.check_dir.is_dir():
            assert distinctive > 0, f"{task.id}: no line of its hidden check was checked"
    # What the agent did that is NOT the grader stays readable.
    assert "CALL shell" in text and "Done." in text


def test_an_evidence_tree_that_still_carries_the_grader_is_refused_and_removed(tmp_path, monkeypatch):
    """The last line of defence: whatever slips past the line scrub, the
    finished tree goes through the tripwire before a proposer may read it."""
    task = TASKS["bugfix-pagination"]
    cfg = tmp_path / "cfg"
    run = cfg / "runs" / task.id
    run.mkdir(parents=True)
    (run / TRAJECTORY_NAME).write_text(json.dumps([
        {"kind": "result", "id": "x", "tool": "shell", "ok": True, "output": f"{HIDDEN_CANARY} found"}]),
        encoding="utf-8")
    monkeypatch.setattr(R, "scrub", lambda text, marks: (text, 0))  # a scrub that missed it
    records = [{"task_id": task.id, "passed": True}]
    probed = [R.Probed(label="current", layer_base=None, files={}, config_dir=cfg, records=records,
                       score=R.score_of(records))]
    folder = tmp_path / "evidence"
    with pytest.raises(HiddenCheckLeak, match="carry the grader"):
        R.build_evidence(folder, [task], probed, {"current": ""})
    assert not folder.exists()


def test_the_command_line_says_a_stopped_refine_in_words(tmp_path, monkeypatch):
    from iron_jarvis.harness_bench.cli import bench_app

    async def leak(**kw):
        raise HiddenCheckLeak("the evidence for the improver would carry the grader (x), so it was not written")

    monkeypatch.setattr(R, "run_refine", leak)
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    out = CliRunner().invoke(bench_app, [
        "run", "--runner", "jarvis", "--provider", "scripted", "--model", "s1", "--refine",
        "--tasks", ",".join(NAMES), "--root", str(tmp_path / "scratch"),
    ])
    assert out.exit_code == 1 and "Stopped: the evidence for the improver would carry the grader" in out.output
    assert "Traceback" not in out.output


# --------------------------------------------------------------------------- #
# a probe's budget that does not add up still writes the refine ledger
# --------------------------------------------------------------------------- #


async def test_a_probe_whose_budget_does_not_add_up_writes_the_refine_ledger_then_raises(tmp_path, monkeypatch):
    platform, _ = _setup(tmp_path, monkeypatch, {0: _proposal_moves(A)})

    def off(self, planned, reused, records):
        raise BudgetMismatch("bugfix-pagination ran 2 times (planned once)")

    monkeypatch.setattr(BudgetLedger, "verify_plan", off)
    with pytest.raises(BudgetMismatch, match="a probe's budget does not add up"):
        await _refine(platform, k=1)
    folder = next((platform.config.home / "harness-bench").glob("refine-*"))
    saved = json.loads((folder / "ledger.json").read_text(encoding="utf-8"))["invocations"][-1]
    assert any("ran 2 times" in p for p in saved["problems"])
    assert (folder / "results.json").exists()
    assert "THE LEDGER DOES NOT ADD UP" in (folder / "report.md").read_text(encoding="utf-8")
    assert not (folder / "adopted").exists()


# --------------------------------------------------------------------------- #
# the report's honesty
# --------------------------------------------------------------------------- #


async def test_the_report_says_one_run_is_a_small_sample_and_parallel_runs_one_at_a_time(tmp_path, monkeypatch):
    platform, _ = _setup(tmp_path, monkeypatch, {0: _proposal_moves(A)})
    res = await _refine(platform, k=1)
    report = (res.folder / "report.md").read_text(encoding="utf-8")
    assert R.SMALL_SAMPLE in report and "small sample" in report
    assert R.MODE_WORDS["parallel"] in report and "not at the same time" in report
    from iron_jarvis.harness_bench.cli import bench_app

    raw = CliRunner().invoke(bench_app, ["run", "--help"]).output
    help_text = " ".join(re.sub(r"[─-╿]", " ", raw).split())  # the help's box lines
    assert "one candidate at a time" in help_text


# --------------------------------------------------------------------------- #
# adopt writes only where it is asked to
# --------------------------------------------------------------------------- #


def _layer(tmp_path) -> Path:
    src = tmp_path / "refine"
    R.write_layer(src / "adopted", {"memory.md": "notes\n"})
    return src


def test_adopt_refuses_a_folder_that_is_not_there(tmp_path):
    missing = tmp_path / "typo" / "project"
    with pytest.raises(BenchRefused, match="There is no folder"):
        R.adopt_into(_layer(tmp_path), missing)
    assert not (tmp_path / "typo").exists()


def test_adopt_refuses_a_harness_folder_that_leads_out_of_the_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        import _winapi as w

        w.CreateJunction(str(outside), str(project / ".ironjarvis"))
    except (ImportError, OSError):
        try:
            os.symlink(outside, project / ".ironjarvis", target_is_directory=True)
        except OSError:
            pytest.skip("no junction or symlink can be made here")
    with pytest.raises(BenchRefused, match="points outside the folder"):
        R.adopt_into(_layer(tmp_path), project)
    assert list(outside.rglob("*")) == []


def test_adopt_refuses_a_file_link_that_leads_out_of_the_project(tmp_path):
    project = tmp_path / "project"
    hdir = project / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    away = tmp_path / "away.md"
    try:
        os.symlink(away, hdir / "memory.md")  # dangling: exists() is False
    except OSError:
        pytest.skip("no symlink can be made here")
    with pytest.raises(BenchRefused, match="is a link"):
        R.adopt_into(_layer(tmp_path), project)
    assert not away.exists()


# --------------------------------------------------------------------------- #
# found correct: the proposer is confined to its own copy
# --------------------------------------------------------------------------- #


async def test_the_proposer_cannot_write_or_read_outside_its_own_copy(tmp_path, monkeypatch):
    """The proposer's file tools resolve every path under its attempt folder
    (links resolved too): a write up and out, or to an absolute path, and a
    read of a hidden check by its full path, are all refused — and the
    candidate's layer is only what it wrote into proposal/."""
    from iron_jarvis.providers.adapters.base import ToolCall

    outside = tmp_path / "outside.md"
    check = next(p for p in TASKS["bugfix-pagination"].hidden_files() if p.suffix == ".py")
    moves = [
        [ToolCall(id="o1", name="write_file", arguments={"path": "../../escape.md", "content": "x\n"})],
        [ToolCall(id="o2", name="write_file", arguments={"path": str(outside), "content": "x\n"})],
        [ToolCall(id="o3", name="read_file", arguments={"path": str(check)})],
        [ToolCall(id="o4", name="write_file", arguments={"path": "proposal/memory.md", "content": "Read first.\n"})],
        "Added a note.",
    ]
    platform, _ = _setup(tmp_path, monkeypatch, {0: moves})
    res = await _refine(platform, k=1)
    cdir = res.folder / "candidates" / "c1"
    assert not outside.exists() and not (cdir / "escape.md").exists() and not (res.folder / "escape.md").exists()
    assert res.candidates[0]["status"] == "valid" and res.candidates[0]["changed"] == ["memory.md"]
    # The read of the hidden check and both writes out were refused (the ledger).
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation

    with session_scope(platform.engine) as db:
        rows = [(r.tool, r.args_json, r.ok) for r in db.exec(select(ToolInvocation)).all()]
    out = [r for r in rows if "escape.md" in r[1] or "outside.md" in r[1] or check.name in r[1]]
    assert len(out) == 3 and not any(ok for _t, _a, ok in out), out
