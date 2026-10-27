"""v1.383.0 review — the frontier tasks' hidden checks hold against MORE than
their author's one decoy.

Each frontier task shipped with one pinned decoy (a near-miss that passes the
tests the agent can see and fails the hidden check). The review wrote more:
careless but plausible hand-ins, each breaking a rule the task's own documents
state, several of which PASSED the hidden check as first shipped (the check
was too weak there) — and correct solutions written another way than the
reference, some of which FAILED it (the check was tied to the reference's
insides). Pinned here, for every one, through the real ``grade``:

* every folder under ``tests/fixtures/harness_bench_decoys_more/<task>/<name>/``
  passes the visible tests (when the task has any) and FAILS the hidden check;
* every folder under ``tests/fixtures/harness_bench_alternatives/<task>/<name>/``
  PASSES the hidden check.

A folder is in the reference-solution format (``<path>.sol`` replaces
``<path>``, ``<path>.delete`` removes it, a ``_solve.py`` runs and goes) and
says in its ``NOTES.md`` which documented rule it breaks (or keeps).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness_bench.grading import grade
from iron_jarvis.harness_bench.setup_step import prepare_workspace
from tests.test_harness_bench_v1363 import FIXTURES, TASKS, apply_solution, skip_if_no_browser

MORE_DECOYS = FIXTURES / "harness_bench_decoys_more"
ALTERNATIVES = FIXTURES / "harness_bench_alternatives"


def _cases(root: Path) -> list[tuple[str, str]]:
    if not root.is_dir():
        return []
    return sorted(
        (task_dir.name, case.name)
        for task_dir in root.iterdir() if task_dir.is_dir()
        for case in task_dir.iterdir() if case.is_dir()
    )


DECOY_CASES = _cases(MORE_DECOYS)
ALT_CASES = _cases(ALTERNATIVES)


def _visible_tests_pass(work: Path) -> None:
    seen = procs.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
                     cwd=work, timeout=300)
    assert seen.returncode == 0, procs.text(seen.stderr, "utf-8")[-2000:]


def test_the_review_cases_are_real_and_named():
    """Not vacuous: there are review decoys, every case names a frontier task
    and says what it gets wrong."""
    assert DECOY_CASES, "no review decoys found"
    for root, cases in ((MORE_DECOYS, DECOY_CASES), (ALTERNATIVES, ALT_CASES)):
        for task_id, name in cases:
            # v1.400.0: the team tier's alternatives and extra decoys live here too.
            assert task_id in TASKS and TASKS[task_id].tier in ("frontier", "team"), task_id
            folder = root / task_id / name
            assert (folder / "NOTES.md").is_file(), folder
            assert any(folder.rglob("*.sol")) or (folder / "_solve.py").is_file(), folder


@pytest.mark.parametrize("task_id,name", DECOY_CASES)
def test_a_review_decoy_passes_what_the_agent_sees_and_fails_the_hidden_check(task_id, name, tmp_path):
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "work")
    apply_solution(name, work, root=MORE_DECOYS / task_id)
    if any(work.glob("test_*.py")):
        _visible_tests_pass(work)
    g = grade(task, work, tmp_path / "grade")
    skip_if_no_browser(g)
    assert g.passed is False, (g.reason, g.output[-2000:])


@pytest.mark.parametrize("task_id,name", ALT_CASES)
def test_a_correct_solution_written_another_way_passes(task_id, name, tmp_path):
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "work")
    apply_solution(name, work, root=ALTERNATIVES / task_id)
    g = grade(task, work, tmp_path / "grade")
    skip_if_no_browser(g)
    assert g.passed is True, (g.reason, g.output[-2000:])
