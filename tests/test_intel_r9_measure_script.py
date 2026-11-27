"""The intelligence plan, release 9: the measurement that waits for the
owner's yes (``scripts/measure_guided_rounds.py``). Nothing here talks to a
daemon or a model server: the task set, its checkers, the refusals and the
arithmetic are checked offline.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_guided_rounds.py"


def _script():
    spec = importlib.util.spec_from_file_location("measure_guided_rounds_r9", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # a dataclass needs its module registered
    spec.loader.exec_module(mod)
    return mod


M = _script()


def test_a_fresh_set_of_forty_made_up_tasks_from_a_seed():
    a = M.make_tasks(2026)
    assert len(a) == M.TASK_COUNT == 40
    assert [t.id for t in a] == [t.id for t in M.make_tasks(2026)]
    assert [t.files for t in a] != [t.files for t in M.make_tasks(2027)]
    assert {t.kind for t in a} == {"replace", "count", "list", "join", "first_line"}


def test_every_checker_fails_before_and_passes_after_the_reference_answer(tmp_path):
    for task in M.make_tasks(7):
        folder = tmp_path / task.id
        M.prepare(task, folder)
        assert M.check(task, folder) is False, task.id
        M.solve(task, folder)
        assert M.check(task, folder) is True, task.id


def test_it_never_measures_against_the_owners_live_app_or_without_a_yes(tmp_path):
    assert M.refuse_live("http://127.0.0.1:8787")
    assert M.refuse_live("http://127.0.0.1:8765")
    assert M.refuse_live("http://192.168.1.4:8899")
    assert M.refuse_live("http://127.0.0.1:8899") == ""
    out = tmp_path / "x.json"
    code = M.main(["run", "--daemon", "http://127.0.0.1:8899", "--token-file", "t", "--provider", "ollama",
                   "--model", "m", "--tasks", "s", "--work", str(tmp_path), "--out", str(out)])
    assert code == 2 and not out.exists()


def test_calls_are_counted_per_finished_task_only():
    rows = [
        {"id": "a", "finished": True, "model_calls": 4},
        {"id": "b", "finished": True, "model_calls": 2},
        {"id": "c", "finished": False, "model_calls": 30},
    ]
    s = M.summarize(rows)
    assert s == {"tasks": 3, "finished": 2, "calls_per_finished_task": 3.0, "unfinished": ["c"]}
    text = M.compare({"rows": rows}, {"rows": rows[:2]})
    assert "2/3 finished, 3.0" in text and "2/2 finished, 3.0" in text
