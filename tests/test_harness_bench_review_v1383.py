"""v1.383.0 review — the benchmark's repeats and its exact model, attacked.

Pinned here, each through the real code:

* A MODEL NAMED "default" IS NO MODEL. ``--model default`` (or
  ``subscription`` / ``auto``, which Iron Jarvis's Claude adapter reads as
  "the CLI's own default") names no particular model, so a record of it could
  not say which one ran — exactly what this phase removed. It is refused
  before any task starts, like a missing model.
* TWO DATED SNAPSHOTS ARE TWO MODELS. ``model_matches`` read
  ``claude-3-5-sonnet-20240620`` and ``claude-3-5-sonnet-20241022`` as the
  same model (it dropped both dates). It also flagged the same model spelled
  two ways (``claude-sonnet-4.5`` / ``claude-sonnet-4-5``, an
  ``-latest`` alias and the snapshot it stands for) as different.
* A TASK ID THAT LOOKS LIKE A REPEAT is refused: ``foo.r2`` would share its
  run folder and ledger key with task ``foo``'s repeat 2.
* A REAL KILL PART-WAY THROUGH A REPEAT: the process killed while a repeat-2
  run is working leaves whole repeat-1 runs; the report lists the cut-off run
  as interrupted and never counts it; the next invocation reuses every finished
  run and reruns only the cut-off one.
* NOTHING AN AGENT STARTED OUTLIVES ITS RUN: a CLI agent that starts the
  price task's server in the background and never stops it — the server is
  gone once the run is recorded (checked by process id).
* NO COMPILED HIDDEN CHECK IS LEFT BEHIND. A check runs ``python -I``, and
  ``-I`` ignores ``PYTHONDONTWRITEBYTECODE``, so Python wrote
  ``__pycache__/test_hidden_*.pyc`` into the grading copy — the WHOLE answer
  key as constants (the 2,000-file task's 62 expected calls, every expected
  value) — and the v1.370.0 cleanup removed only the ``.py``. The copy sits at
  ``runs/<task>/grade/``, two folders from where a repeat of the same task (or
  the same task in another setup) is worked, so a later agent with a shell
  could read it. The cleanup now removes what was compiled from the hidden
  files too.
* THE TASK DATA IS NOT APP CODE: two source scans (raw process spawns, retired
  words) skip ``harness_bench/tasks/``. That is safe only while nothing there
  is importable as part of Iron Jarvis — no ``__init__.py`` makes the folder
  (or a task folder) a package, and no module outside it imports from it.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.harness_bench import bench as bench_mod
from iron_jarvis.harness_bench.bench import BenchRefused, run_bench
from iron_jarvis.harness_bench.catalog import TaskError, load_task
from iron_jarvis.harness_bench.report import model_matches
from iron_jarvis.harness_bench.runners import ClaudeRunner
from tests.test_harness_bench_v1363 import FIXTURES, SOLUTIONS, TASKS, _fake_env, _shim, _StubRunner

NAMES = ["bugfix-leap-year", "explain-mystery-functions"]


# --------------------------------------------------------------------------- #
# "default" is not a model
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("word", ["default", "Default", " subscription ", "auto", "DEFAULT"])
async def test_a_model_that_names_no_model_is_refused_before_any_task_starts(tmp_path, word):
    stub = _StubRunner()
    stub.model = word
    with pytest.raises(BenchRefused) as caught:
        await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES)
    assert str(caught.value) == bench_mod.MODEL_NOT_A_MODEL.format(model=word.strip())
    assert "--model" in str(caught.value)
    assert stub.calls == [] and not (tmp_path / "harness-bench").exists()


def test_the_command_line_refuses_model_default_for_every_runner(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    record = _fake_env(monkeypatch, tmp_path, "solve")
    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    root = str(tmp_path / "bench-root")
    for argv in (["--runner", "claude", "--binary", claude], ["--runner", "codex", "--binary", claude],
                 ["--runner", "jarvis", "--provider", "claude-cli"]):
        out = CliRunner().invoke(bench_app, ["run", *argv, "--model", "default", "--root", root,
                                             "--tasks", "bugfix-pagination"])
        assert out.exit_code == 2, out.output
        assert out.output.strip() == bench_mod.MODEL_NOT_A_MODEL.format(model="default"), out.output
    assert not record.exists(), "no task may start before the refusal"


# --------------------------------------------------------------------------- #
# model_matches
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("passed,reported,same", [
    # Two snapshots of one name are two models.
    ("claude-3-5-sonnet-20240620", "claude-3-5-sonnet-20241022", False),
    ("gpt-6-astra-2026-10-01", "gpt-6-astra-2026-11-15", False),
    ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20241022", True),
    # The same model spelled another way is the same model.
    ("anthropic/claude-sonnet-4.5", "claude-sonnet-4-5-20250929", True),
    ("claude-sonnet-4-5", "claude-sonnet-4.5", True),
    ("claude-3-5-sonnet-latest", "claude-3-5-sonnet-20241022", True),
    # …and what already held still holds.
    ("sonnet", "claude-sonnet-4-6", True),
    ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929", True),
    ("sonnet", "claude-opus-4-6", False),
    ("claude-sonnet-4-5", "claude-sonnet-4-6", False),
    ("claude-sonnet-4.5", "claude-sonnet-4-6", False),
    ("gpt-6-astra", "gpt-6-sol", False),
    ("gpt-4.1", "gpt-4-1106", False),
])
def test_a_reported_model_is_judged_by_its_name_and_its_snapshot(passed, reported, same):
    assert model_matches(passed, reported) is same


# --------------------------------------------------------------------------- #
# A task id that looks like a repeat
# --------------------------------------------------------------------------- #


def test_a_task_id_that_looks_like_a_repeat_is_refused(tmp_path):
    src = TASKS["bugfix-leap-year"].path
    import shutil

    for name, ok in (("leap.r2", False), ("leap.R10", False), ("leap.r", True), ("leap-r2", True)):
        dest = tmp_path / name
        shutil.copytree(src, dest)
        if ok:
            assert load_task(dest).id == name
        else:
            with pytest.raises(TaskError, match="repeat"):
                load_task(dest)


# --------------------------------------------------------------------------- #
# A real kill part-way through a repeat
# --------------------------------------------------------------------------- #

_CLI = "from iron_jarvis.harness_bench.cli import bench_app; bench_app()"


def _kill_tree(pid: int) -> None:
    import psutil

    with contextlib.suppress(psutil.NoSuchProcess):
        parent = psutil.Process(pid)
        for kid in parent.children(recursive=True):
            with contextlib.suppress(psutil.NoSuchProcess):
                kid.kill()
        parent.kill()


def test_a_run_killed_inside_a_repeat_resumes_without_rerunning_or_counting_unfinished(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("IRONJARVIS_HOME", "ANTHROPIC_API_KEY")}
    # The third call — repeat 2's first run — hangs (two tasks, repeat 1 first).
    env.update({"FAKE_BENCH_SCENARIO": "hang_at", "FAKE_BENCH_HANG_AT": "3",
                "FAKE_BENCH_COUNT": str(tmp_path / "calls.txt"),
                "FAKE_BENCH_SOLUTION": str(SOLUTIONS / "bugfix-pagination"), "PYTHONIOENCODING": "utf-8"})
    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    root = tmp_path / "bench-root"
    argv = [sys.executable, "-c", _CLI, "run", "--runner", "claude", "--binary", claude, "--model", "sonnet",
            "--effort", "low", "--root", str(root), "--tasks", "bugfix-pagination,bugfix-leap-year",
            "--repeats", "2"]
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=str(tmp_path))
    try:
        bench = root / ".ironjarvis" / "harness-bench"
        deadline = time.monotonic() + 120
        started_r2 = []
        while time.monotonic() < deadline and not started_r2:
            started_r2 = list(bench.glob("*/runs/*.r2/started.json")) if bench.is_dir() else []
            time.sleep(0.1)
        assert started_r2, "repeat 2 never started"
        time.sleep(1.0)  # the fake is now hanging inside repeat 2's first run
        assert proc.poll() is None, "the bench ended on its own"
    finally:
        _kill_tree(proc.pid)
        proc.wait(timeout=30)
    cfg_dir = started_r2[0].parents[2]
    runs = sorted(p.name for p in (cfg_dir / "runs").iterdir())
    finished = sorted(p.parent.name for p in (cfg_dir / "runs").glob("*/complete.json"))
    cut = started_r2[0].parent.name
    assert cut in runs and cut not in finished and len(finished) == 2, (runs, finished)
    assert all(not f.endswith(".r2") for f in finished)
    # The report made from what is on disk lists the cut-off run as
    # interrupted and counts only the finished ones.
    bench_mod.write_config_outputs(cfg_dir)
    records = json.loads((cfg_dir / "results.json").read_text(encoding="utf-8"))["records"]
    interrupted = [r for r in records if r.get("interrupted")]
    assert [(r["task_id"], r.get("repeat")) for r in interrupted] == [(cut.split(".r2")[0], 2)]
    report = (cfg_dir / "report.md").read_text(encoding="utf-8")
    # (the fake hands every task the pagination fix: pagination passes, leap
    # year fails — a real verdict either way.)
    assert "**Mean pass rate 50%, range 50%–50% over 2 repeats**" in report, report[:600]
    assert "| 1 | 50% | 1 / 2 | 0 | 0 | 0 |" in report
    assert "| 2 | n/a | 0 / 0 | 0 | 0 | 1 |" in report
    # Resume: every finished run is reused, the cut-off one (and the repeat-2
    # run that never started) run now — nothing finished runs again.
    env["FAKE_BENCH_SCENARIO"] = "solve"
    out = subprocess.run(argv, env=env, capture_output=True, cwd=str(tmp_path), timeout=300)
    text = out.stdout.decode("utf-8", "replace")
    assert out.returncode == 0, text + out.stderr.decode("utf-8", "replace")
    assert text.count(": reused (") == 2, text
    assert text.count("running...") == 2, text
    assert "2 of 4 scored runs passed across 2 repeats; mean pass rate 50%, range 50%–50%" in text, text
    records = json.loads((cfg_dir / "results.json").read_text(encoding="utf-8"))["records"]
    assert sorted((r["task_id"], r.get("repeat", 1)) for r in records) == sorted(
        (t, k) for t in ("bugfix-pagination", "bugfix-leap-year") for k in (1, 2))
    assert not any(r.get("interrupted") for r in records)


# --------------------------------------------------------------------------- #
# Nothing an agent started outlives its run
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform != "win32", reason="the runner's Windows Job ends the agent's tree")
async def test_a_server_a_cli_agent_left_running_is_gone_after_the_run(tmp_path, monkeypatch):
    import psutil

    _fake_env(monkeypatch, tmp_path, "leave_server")
    runner = ClaudeRunner(model="sonnet", effort="low",
                          binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    pid = None
    try:
        res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()),
                              names=["frontier-debug-price-server"])
        work = res.config_dir / "runs" / "frontier-debug-price-server" / "work"
        pid = int((work / "server.pid").read_text(encoding="utf-8"))
        assert "listening" in (work / "server.log").read_text(encoding="utf-8"), "the server never started"
        deadline = time.monotonic() + 20
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(pid), "a server the agent left running outlived the run"
        assert res.records[0]["passed"] is False  # it never fixed anything
    finally:
        if pid:
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()


# --------------------------------------------------------------------------- #
# The task data is not app code
# --------------------------------------------------------------------------- #


def test_the_task_data_the_source_scans_skip_is_not_importable_app_code():
    import ast

    pkg = Path(bench_mod.__file__).resolve().parents[1]  # src/iron_jarvis
    tasks = pkg / "harness_bench" / "tasks"
    assert tasks.is_dir() and any(tasks.rglob("*.py"))
    assert not (tasks / "__init__.py").exists(), "the task data became a package of the app"
    for task_dir in tasks.iterdir():
        if task_dir.is_dir():
            assert not (task_dir / "__init__.py").exists(), task_dir
    importers = []
    for path in pkg.rglob("*.py"):
        if tasks in path.parents:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any("harness_bench.tasks" in n for n in names) or (
                    isinstance(node, ast.ImportFrom) and node.module == "tasks" and node.level):
                importers.append(path.relative_to(pkg).as_posix())
    assert not importers, importers


# --------------------------------------------------------------------------- #
# No compiled hidden check is left behind
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("task_id", ["bugfix-leap-year", "frontier-retry-idempotency"])
def test_the_grading_copy_keeps_no_compiled_hidden_check(task_id, tmp_path):
    from iron_jarvis.harness_bench.grading import grade
    from iron_jarvis.harness_bench.setup_step import prepare_workspace
    from tests.test_harness_bench_v1363 import apply_solution

    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "work")
    apply_solution(task_id, work)
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is True, g.reason
    stems = {p.stem for p in task.check_dir.rglob("*.py")}
    assert stems
    left = [p.relative_to(tmp_path / "grade").as_posix() for p in (tmp_path / "grade").rglob("*")
            if p.is_file() and p.name.split(".")[0] in stems]
    assert not left, f"grader material left in the grading copy: {left}"
    # (the agent's own compiled files stay: only the grader's go)
    assert any((tmp_path / "grade").rglob("*.pyc"))
