"""v1.370.0 ship-step pins (the Phase 7 review's open bench-integrity gap).

A graded copy under ``<bench root>/runs/<task>/grade/`` used to keep the
hidden check files after grading, so a LATER run's agent with a shell could
read the answer key from disk. ``grading.grade`` now takes them back out; the
agent's own work stays in the copy for inspection, and the grade is the same.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness_bench.catalog import HIDDEN_CANARY, load_tasks, materialize
from iron_jarvis.harness_bench.grading import grade
from tests.test_harness_bench_v1363 import apply_solution

TASKS = [t for t in load_tasks() if t.check_dir.is_dir()]


@pytest.mark.parametrize("task", TASKS[:4], ids=lambda t: t.id)
def test_a_graded_copy_keeps_the_work_and_no_hidden_check(task, tmp_path):
    work = materialize(task, tmp_path / "work")
    apply_solution(task.id, work)
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is True, (g.reason, g.output[-800:])
    graded = tmp_path / "grade"
    hidden = [p.relative_to(task.check_dir) for p in task.check_dir.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts]
    assert hidden, "this task has hidden check files to take back out"
    for rel in hidden:
        assert not (graded / rel).exists(), f"{rel} is still in the graded copy"
    left = [p for p in graded.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    assert left, "the agent's own work stays for inspection"
    for p in left:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        assert HIDDEN_CANARY not in text, f"{p.name} still carries the hidden check's marker"
