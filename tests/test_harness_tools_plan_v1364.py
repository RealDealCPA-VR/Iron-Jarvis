"""v1.364.0 (IJ harness Phase 1): ``plan_update`` (Claude Code's TodoWrite /
Codex's update_plan) and ``notebook_edit`` (Claude Code's NotebookEdit).

``plan_update`` takes the WHOLE list every time, exactly one step in
progress until all are completed, writes nothing, and its result's ``data``
carries the plan (what the chat's step detail and the Phase 6 plan pane read).

``notebook_edit`` replaces, inserts or deletes one cell by index or id,
writes valid JSON in the notebook's own indent, clears a replaced code
cell's stale outputs (and says so), and is undoable.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness.tools import NotebookEditTool, PlanUpdateTool
from iron_jarvis.tools.base import Reversibility, ToolContext


def _ctx(ws: Path, home: Path | None = None) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=SimpleNamespace(home=home or ws.parent), event_bus=None, engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


# --------------------------------------------------------------------------- #
# plan_update
# --------------------------------------------------------------------------- #


async def test_the_plan_rides_the_result_data_and_reads_as_a_checklist(ws):
    plan = [
        {"step": "Read the failing test", "status": "completed"},
        {"step": "Fix the parser", "status": "in_progress"},
        {"step": "Run the suite", "status": "pending"},
    ]
    res = await PlanUpdateTool().execute({"plan": plan, "explanation": "found the bug"}, _ctx(ws))
    assert res.ok
    assert res.output == (
        "Plan updated (1 of 3 done):\n[x] Read the failing test\n[>] Fix the parser\n"
        "[ ] Run the suite\nWhy: found the bug"
    )
    assert res.data == {"plan": plan, "explanation": "found the bug", "done": 1, "total": 3}
    assert list(ws.iterdir()) == [], "the plan writes nothing"
    assert PlanUpdateTool.reversibility is Reversibility.READONLY


async def test_all_done_needs_no_step_in_progress(ws):
    res = await PlanUpdateTool().execute(
        {"plan": [{"step": "a", "status": "completed"}, {"step": "b", "status": "completed"}]}, _ctx(ws)
    )
    assert res.ok and res.output.startswith("Plan updated (2 of 2 done):")


@pytest.mark.parametrize(
    "plan, words",
    [
        ([{"step": "a", "status": "pending"}], "exactly one step must be in_progress"),
        ([{"step": "a", "status": "in_progress"}, {"step": "b", "status": "in_progress"}], "(this one has 2)"),
        ([{"step": "a", "status": "done"}], "use pending, in_progress or completed"),
        ([{"step": "", "status": "pending"}], "step 1 has no `step` text"),
        ([], "`plan` is empty"),
        ("not a list", "`plan` must be a list"),
        ([{"step": f"s{i}", "status": "completed"} for i in range(51)], "at most 50"),
    ],
)
async def test_a_bad_plan_is_refused_in_words(ws, plan, words):
    """MUTATION: drop the exactly-one-in-progress rule -> rows 1 and 2 pass -> red."""
    res = await PlanUpdateTool().execute({"plan": plan}, _ctx(ws))
    assert res.ok is False and words in res.error


async def test_claude_codes_todo_spelling_and_a_json_string_are_accepted(ws):
    todos = json.dumps([{"content": "Write the test", "status": "in_progress"}])
    res = await PlanUpdateTool().execute({"todos": todos}, _ctx(ws))
    assert res.ok and res.data["plan"] == [{"step": "Write the test", "status": "in_progress"}]


# --------------------------------------------------------------------------- #
# notebook_edit
# --------------------------------------------------------------------------- #


def _nb(ws: Path, cells: list[dict], minor: int = 5) -> Path:
    path = ws / "n.ipynb"
    nb = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": minor}
    path.write_bytes((json.dumps(nb, indent=1) + "\n").encode("utf-8"))
    return path


def _cells(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cells"]


async def test_replace_clears_stale_outputs_and_says_so(ws):
    path = _nb(ws, [{"cell_type": "code", "id": "c1", "metadata": {}, "source": ["print(1)\n"],
                     "outputs": [{"output_type": "stream", "text": ["1\n"]}], "execution_count": 4}])
    res = await NotebookEditTool().execute({"path": "n.ipynb", "cell_index": 0, "new_source": "print(2)\nprint(3)"}, _ctx(ws))
    assert res.ok, res.error
    assert res.output == "replaced cell 0 (its old outputs were cleared) in n.ipynb\nCheck: the file parses."
    cell = _cells(path)[0]
    assert cell["source"] == ["print(2)\n", "print(3)"]
    assert cell["outputs"] == [] and cell["execution_count"] is None and cell["id"] == "c1"
    assert path.read_text(encoding="utf-8").startswith('{\n "cells": [\n  {'), "the notebook's own indent"


async def test_insert_after_an_id_and_at_an_index_and_delete(ws):
    path = _nb(ws, [
        {"cell_type": "markdown", "id": "m1", "metadata": {}, "source": ["# A"]},
        {"cell_type": "markdown", "id": "m2", "metadata": {}, "source": ["# B"]},
    ])
    tool = NotebookEditTool()
    after = await tool.execute(
        {"path": "n.ipynb", "edit_mode": "insert", "cell_id": "m1", "new_source": "x = 1", "cell_type": "code"}, _ctx(ws)
    )
    assert after.ok and after.output.startswith("inserted a code cell at 1")
    cells = _cells(path)
    assert [c.get("id") for c in cells][0::2] == ["m1", "m2"]
    new = cells[1]
    assert new["cell_type"] == "code" and new["outputs"] == [] and new["execution_count"] is None
    assert len(new["id"]) == 8, "nbformat 4.5 cells carry an id"
    first = await tool.execute(
        {"path": "n.ipynb", "edit_mode": "insert", "cell_index": 0, "new_source": "# Top", "cell_type": "markdown"}, _ctx(ws)
    )
    assert first.ok and _cells(path)[0]["source"] == ["# Top"]
    gone = await tool.execute({"path": "n.ipynb", "edit_mode": "delete", "cell_id": "m2"}, _ctx(ws))
    assert gone.ok and "m2" not in [c.get("id") for c in _cells(path)]


@pytest.mark.parametrize(
    "args, words",
    [
        ({"cell_index": 5, "new_source": "x"}, "cell_index 5 is out of range"),
        ({"cell_id": "nope", "new_source": "x"}, "no cell with id 'nope'"),
        ({"new_source": "x"}, "give `cell_index` (0-based) or `cell_id`"),
        ({"cell_index": 0}, "`new_source` is required"),
        ({"cell_index": 0, "new_source": "x", "edit_mode": "rewrite"}, "edit_mode must be replace, insert or delete"),
        ({"cell_index": 0, "new_source": "x", "cell_type": "sql"}, "cell_type must be code, markdown or raw"),
    ],
)
async def test_bad_cell_arguments_are_refused_and_nothing_changes(ws, args, words):
    path = _nb(ws, [{"cell_type": "code", "id": "c1", "metadata": {}, "source": [], "outputs": [], "execution_count": None}])
    before = path.read_bytes()
    res = await NotebookEditTool().execute({"path": "n.ipynb", **args}, _ctx(ws))
    assert res.ok is False and words in res.error
    assert path.read_bytes() == before


async def test_a_broken_notebook_and_a_non_notebook_are_refused(ws):
    (ws / "bad.ipynb").write_text("{not json", encoding="utf-8")
    bad = await NotebookEditTool().execute({"path": "bad.ipynb", "cell_index": 0, "new_source": "x"}, _ctx(ws))
    assert bad.ok is False and "is not a valid notebook" in bad.error
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    other = await NotebookEditTool().execute({"path": "a.py", "cell_index": 0, "new_source": "x"}, _ctx(ws))
    assert other.ok is False and "is not a .ipynb notebook" in other.error


async def test_the_notebook_edit_is_undoable(tmp_path, ws):
    home = tmp_path / "home"
    home.mkdir()
    path = _nb(ws, [{"cell_type": "markdown", "id": "m1", "metadata": {}, "source": ["# A"]}])
    before = path.read_bytes()
    tool = NotebookEditTool()
    args = {"path": "n.ipynb", "cell_index": 0, "new_source": "# B"}
    undo = await tool.capture_undo(args, _ctx(ws, home))
    assert undo is not None and undo["kind"] == "file_restore"
    assert (await tool.execute(args, _ctx(ws, home))).ok
    assert path.read_bytes() != before
    assert (await tool.revert(undo, _ctx(ws, home))).ok
    assert path.read_bytes() == before
