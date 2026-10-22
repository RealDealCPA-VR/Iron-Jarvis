"""v1.370.3 — the release gate's Python suite runs as parallel shards.

The suite ran on ONE 4-core Windows runner, ~58 of a release's ~78 minutes.
``scripts/ci_shards.py`` splits it by test FILE across N runners. The one
thing it must never do is LOSE a test: a file in no shard is a test the gate
silently stopped running. These pin that it cannot.
"""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_shards", ROOT / "scripts" / "ci_shards.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)  # type: ignore[union-attr]


def test_discover_finds_every_test_file_pytest_would_and_nothing_else():
    found = S.discover()
    on_disk = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py"))
    assert found == on_disk
    assert "tests/test_ci_shards_v13703.py" in found
    assert len(found) > 800


def test_discover_follows_pytests_own_rules(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\nnorecursedirs = ["_review_*", "__pycache__"]\n',
        encoding="utf-8",
    )
    t = tmp_path / "tests"
    (t / "sub").mkdir(parents=True)
    (t / "_review_old").mkdir()
    for rel in ("test_a.py", "b_test.py", "helper.py", "sub/test_c.py", "_review_old/test_stale.py", "conftest.py"):
        (t / rel).write_text("", encoding="utf-8")
    assert S.discover(tmp_path) == ["tests/b_test.py", "tests/sub/test_c.py", "tests/test_a.py"]


@pytest.mark.parametrize("n", [1, 2, 3, 6, 8])
def test_plan_is_a_partition_every_file_exactly_once(n):
    rng = random.Random(n)
    files = [f"tests/test_{i:03}.py" for i in range(120)]
    durations = {f: rng.uniform(0.1, 60) for f in files[:100]}  # 20 never measured
    shards = S.plan(files, durations, n)
    assert len(shards) == n
    flat = [f for s in shards for f in s]
    assert sorted(flat) == sorted(files), "a file lost or doubled"
    assert len(flat) == len(set(flat))
    assert all(shards), "no shard is empty when there are more files than shards"


def test_plan_balances_by_measured_time_and_is_the_same_on_every_runner():
    files = [f"tests/test_{i}.py" for i in range(30)]
    durations = {f: float(i + 1) for i, f in enumerate(files)}
    a = S.plan(files, durations, 4)
    b = S.plan(list(reversed(files)), dict(reversed(list(durations.items()))), 4)
    assert a == b, "the plan depends only on the files and their timings"
    loads = [sum(durations[f] for f in s) for s in a]
    # Longest-first greedy: the spread is never more than the largest file.
    assert max(loads) - min(loads) <= max(durations.values())


def test_an_unmeasured_file_still_lands_in_a_shard(tmp_path):
    files = ["tests/test_new.py", "tests/test_old.py"]
    shards = S.plan(files, {"tests/test_old.py": 100.0}, 2)
    assert sorted(f for s in shards for f in s) == files


def test_an_empty_shard_is_refused_never_handed_to_pytest(monkeypatch, capsys):
    # pytest given NO paths runs the whole suite: an empty shard must fail.
    monkeypatch.setattr(S, "discover", lambda root=S.ROOT: ["tests/test_only.py"])
    assert S.main(["--shards", "3", "--index", "2"]) == 1
    assert "empty" in capsys.readouterr().err
    assert S.main(["--shards", "3", "--index", "1"]) == 0
    assert capsys.readouterr().out.strip() == "tests/test_only.py"


def test_every_shard_index_together_covers_the_real_suite(capsys):
    seen: list[str] = []
    for i in range(1, 7):
        assert S.main(["--shards", "6", "--index", str(i)]) == 0
        seen += capsys.readouterr().out.split()
    assert sorted(seen) == S.discover()
    assert len(seen) == len(set(seen))


def test_parse_durations_sums_setup_call_and_teardown_per_file():
    report = """
============================= slowest durations =============================
12.50s call     tests/test_a.py::test_one
1.25s setup    tests/test_a.py::test_one
0.25s teardown tests\\test_a.py::test_two
3.00s call     tests/test_b.py::TestX::test_y[case-1]
(1 durations < 0.005s hidden.)
"""
    assert S.parse_durations(report) == {"tests/test_a.py": 14.0, "tests/test_b.py": 3.0}


def test_check_collected_names_a_test_file_no_shard_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(S, "discover", lambda root=S.ROOT: ["tests/test_a.py"])
    report = tmp_path / "collected.txt"
    report.write_text("tests/test_a.py::test_x\ntests/test_hidden.py::test_y\n\n2 tests collected\n", encoding="utf-8")
    assert S.main(["--shards", "2", "--check-collected", str(report)]) == 1
    assert "tests/test_hidden.py" in capsys.readouterr().err
    report.write_text("tests/test_a.py::test_x\n1 test collected\n", encoding="utf-8")
    assert S.main(["--shards", "2", "--check-collected", str(report)]) == 0
    report.write_text("no tests ran\n", encoding="utf-8")
    assert S.main(["--shards", "2", "--check-collected", str(report)]) == 1


def test_the_timings_file_is_valid_and_names_real_files():
    data = json.loads((ROOT / "scripts" / "ci_test_durations.json").read_text(encoding="utf-8"))
    assert data and all(isinstance(v, (int, float)) and v >= 0 for v in data.values())
    real = set(S.discover())
    assert len(set(data) & real) >= 0.9 * len(real), "re-measure: most files have no timing"


@pytest.mark.parametrize("workflow,job", [("release.yml", "python"), ("tests.yml", "backend")])
def test_each_workflow_runs_as_many_shards_as_it_plans(workflow, job):
    """The matrix and the planner's --shards must agree, or a shard's files
    are never run (more planned than matrix entries) or a shard is empty."""
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    j = wf["jobs"][job]
    matrix = j["strategy"]["matrix"]["shard"]
    assert matrix == list(range(1, len(matrix) + 1))
    assert j["strategy"].get("fail-fast") is False, "one red shard must not hide the others' results"
    run = "\n".join(str(s.get("run", "")) for s in j["steps"])
    assert f"--shards {len(matrix)} --index ${{{{ matrix.shard }}}}" in run
    assert "--check-collected" in run, "one shard checks pytest collects nothing the plan missed"
