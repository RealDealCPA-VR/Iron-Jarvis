"""v1.363.0 — the harness benchmark (IJ harness plan, Phase 0).

What is pinned here, each through the real code:

* THE TASKS: ~20 core ones, every category the plan names, plus a HARD tier
  of ten; each GRADEABLE BOTH WAYS — the untouched repo fails its hidden check
  and the reference solution (``tests/fixtures/harness_bench_solutions/<id>/``)
  passes it — and every hard task has a DECOY (a plausible near-miss in
  ``tests/fixtures/harness_bench_decoys/<id>/``) that passes the tests the
  agent can see and fails the hidden check.
* THE TRIPWIRE: a run never starts while the grader is in the agent's reach
  (the hidden files never reach the copy; a leaked one is refused by name,
  content, marker or paste — in the workspace or in the prompt).
* UNSCORED IS NOT ZERO: a check that cannot run is ``passed=None`` and leaves
  the pass rate's denominator.
* COMPLETION IS RECORDED: a stored result is reused only under a completion
  marker whose fingerprint (runner, provider, model, task content, harness,
  version) is this configuration's and whose workspace is unchanged.
* THE BUDGET LEDGER: every model call is entered; a record that disagrees with
  the ledger raises.
* THE JARVIS RUNNER: a SMOKE run of two tasks through Iron Jarvis's own
  streaming chat lane with a scripted model — real tools, real grading, the
  report — plus the "a broken check turns it red" contrast.
* THE CLI RUNNERS: ``claude`` and ``codex`` driven through the same code path
  against fake executables (``tests/fixtures/fake_*_bench_v1363.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness_bench import bench as bench_mod
from iron_jarvis.harness_bench.bench import (
    MARKER_NAME,
    RESULT_NAME,
    BenchRefused,
    Configuration,
    run_bench,
)
from iron_jarvis.harness_bench.catalog import (
    CATEGORIES,
    HIDDEN_CANARY,
    TIERS,
    TaskError,
    load_task,
    load_tasks,
    materialize,
    select_tasks,
)
from iron_jarvis.harness_bench.grading import grade
from iron_jarvis.harness_bench.ledger import BudgetLedger, BudgetMismatch, CallEntry
from iron_jarvis.harness_bench.report import combined_report, summarize
from iron_jarvis.harness.mode import CODING_TOOLS
from iron_jarvis.harness_bench.runners import (
    ClaudeRunner,
    CodexRunner,
    JarvisRunner,
    RunOutcome,
    parse_claude_stream,
    parse_codex_events,
    render_prompt,
)
from iron_jarvis.harness_bench.setup_step import prepare_workspace
from iron_jarvis.harness_bench.tripwire import HiddenCheckLeak, assert_context_clean, leaks
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

FIXTURES = Path(__file__).parent / "fixtures"
SOLUTIONS = FIXTURES / "harness_bench_solutions"
DECOYS = FIXTURES / "harness_bench_decoys"
TASKS = {t.id: t for t in load_tasks()}
HARD = sorted(t.id for t in TASKS.values() if t.tier == "hard")


def edge_installed() -> bool:
    """Whether the browser task's own hidden check finds Microsoft Edge here
    (its ``find_edge``, loaded from the check file)."""
    import importlib.util

    path = TASKS["frontier-inventory-filter"].check_dir / "test_hidden_inventory_page.py"
    spec = importlib.util.spec_from_file_location("_ij_page_check", path)
    module = importlib.util.module_from_spec(spec)
    # v1.400.0 review: compiled in memory only. With bytecode on (CI's
    # setting) exec_module wrote check/__pycache__/*.pyc into the task's own
    # folder — the hidden check, compiled, left in the source tree.
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return bool(module.find_edge())


def skip_if_no_browser(g) -> None:
    """The browser check exits 3 (unscored) when no browser can start. Only a
    machine with NO Edge installed may skip on it: with Edge here, exit 3 is a
    broken check or a browser that would not launch — red, never a quiet skip."""
    if g.passed is None and "exit code 3" in g.reason and not edge_installed():
        pytest.skip("no Microsoft Edge on this machine (the browser task is unscored here)")


def apply_solution(task_id: str, work: Path, *, root: Path = SOLUTIONS) -> None:
    """Lay the reference solution (or, with ``root=DECOYS``, the decoy) over
    ``work``: every ``<path>.sol`` file is written to ``<path>``, every
    ``<path>.delete`` removes ``<path>``; a ``_solve.py`` is then run there
    (and removed)."""
    sol = root / task_id
    for path in sorted(sol.rglob("*.sol")):
        dest = work / path.relative_to(sol).as_posix()[: -len(".sol")]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    for path in sorted(sol.rglob("*.delete")):
        gone = work / path.relative_to(sol).as_posix()[: -len(".delete")]
        assert gone.is_file(), gone
        gone.unlink()
    solve = work / "_solve.py"
    if solve.exists():
        result = procs.run([sys.executable, "_solve.py"], cwd=work, timeout=90)
        assert result.returncode == 0, procs.text(result.stderr, "utf-8")
        solve.unlink()


# --------------------------------------------------------------------------- #
# The tasks
# --------------------------------------------------------------------------- #


def test_the_task_set_covers_the_plan():
    core = [t for t in TASKS.values() if t.tier == "core"]
    assert 18 <= len(core) <= 24 and len(HARD) == 10
    assert {t.tier for t in TASKS.values()} == set(TIERS)
    for task in TASKS.values():
        toml = (task.path / "task.toml").read_text(encoding="utf-8")
        # A hard task says so in its task.toml; a core task may leave it out
        # (its content, and so its fingerprint, is unchanged since round 1).
        assert (f'tier = "{task.tier}"' in toml) or (task.tier == "core" and "tier" not in toml), task.id
    for task_id in HARD:
        assert (DECOYS / task_id).is_dir(), f"no decoy for {task_id}"
        size = sum(len(p.read_text(encoding="utf-8").splitlines())
                   for p in TASKS[task_id].repo_dir.rglob("*") if p.is_file())
        assert size <= 1500, (task_id, size)
    cats = [t.category for t in core]
    for category in CATEGORIES:
        assert category in cats, category
    assert cats.count("trap") >= 2 and cats.count("process") >= 1 and cats.count("shell") >= 1
    for task in TASKS.values():
        assert task.prompt and task.content_hash
        assert task.check or task.rubric
        if task.category == "explain":
            assert task.rubric.get("answer_file"), task.id
        for hidden in task.check_dir.rglob("*.py") if task.check_dir.is_dir() else []:
            assert HIDDEN_CANARY in hidden.read_text(encoding="utf-8"), hidden
        assert (SOLUTIONS / task.id).is_dir(), f"no reference solution for {task.id}"


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_every_task_is_gradeable_both_ways(task_id, tmp_path):
    # v1.383.0: the copy is SET UP as the bench sets it up (a frontier task's
    # generated tree, its repository in the middle of a merge); a task without
    # a setup step is exactly ``materialize``.
    task = TASKS[task_id]
    untouched = prepare_workspace(task, tmp_path / "untouched")
    g = grade(task, untouched, tmp_path / "grade-untouched")
    skip_if_no_browser(g)
    assert g.passed is False, (g.reason, g.output[-1500:])
    solved = prepare_workspace(task, tmp_path / "solved")
    apply_solution(task_id, solved)
    g = grade(task, solved, tmp_path / "grade-solved")
    skip_if_no_browser(g)
    assert g.passed is True, (g.reason, g.output[-1500:])


def test_a_migration_command_that_exits_1_for_rejected_files_still_passes(tmp_path):
    """Review round 3: SCHEMA.md says what ``python migrate.py <folder>`` DOES,
    not what it exits with — and the folder holds records that must be
    rejected, so ``sys.exit(1 if rejected else 0)`` is a reasonable reading.
    The hidden check failed it; only the command's effects decide."""
    task = TASKS["hard-idempotent-migration"]
    work = materialize(task, tmp_path / "w")
    apply_solution(task.id, work)
    src = (work / "migrate.py").read_text(encoding="utf-8")
    plain = 'if __name__ == "__main__":\n    migrate_folder(sys.argv[1])'
    assert src.count(plain) == 1
    (work / "migrate.py").write_text(
        src.replace(plain, 'if __name__ == "__main__":\n    sys.exit(1 if migrate_folder(sys.argv[1]) else 0)'),
        encoding="utf-8",
    )
    g = grade(task, work, tmp_path / "g")
    assert g.passed is True, (g.reason, g.output[-1500:])


@pytest.mark.parametrize("task_id", HARD)
def test_a_hard_tasks_near_miss_passes_what_the_agent_sees_and_fails_the_hidden_check(task_id, tmp_path):
    """What makes a hard task hard: a plausible wrong answer (the decoy) that
    the tests in the agent's own copy accept — when the task has any — and
    only the hidden check refuses."""
    task = TASKS[task_id]
    work = materialize(task, tmp_path / "decoy")
    apply_solution(task_id, work, root=DECOYS)
    if any(work.glob("test_*.py")):
        seen = procs.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
                         cwd=work, timeout=120)
        assert seen.returncode == 0, procs.text(seen.stderr, "utf-8")[-1500:]
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is False, (g.reason, g.output[-1500:])


def test_the_tier_chooses_the_tasks():
    tasks = list(TASKS.values())
    hard = [t.id for t in select_tasks(tasks, tier="hard", seed=5)]
    core = [t.id for t in select_tasks(tasks, tier="core", seed=5)]
    frontier = [t.id for t in select_tasks(tasks, tier="frontier", seed=5)]
    # v1.400.0: the team tier (tasks where splitting pays) is its own fourth tier.
    team = [t.id for t in select_tasks(tasks, tier="team", seed=5)]
    every = [t.id for t in select_tasks(tasks, tier="all", seed=5)]
    assert sorted(hard) == HARD
    assert core and frontier and not set(core) & set(hard) and not set(frontier) & (set(core) | set(hard))
    assert team and not set(team) & (set(core) | set(hard) | set(frontier))
    assert sorted(core + hard + frontier + team) == sorted(TASKS)
    assert every == [t.id for t in select_tasks(tasks, seed=5)] and sorted(every) == sorted(TASKS)
    assert hard == [i for i in every if i in set(hard)]
    picked = select_tasks(tasks, names=["bugfix-leap-year", HARD[0]], tier="hard")
    assert [t.id for t in picked] == [HARD[0]]
    with pytest.raises(TaskError, match="--tier must be one of core, hard, frontier, team, all"):
        select_tasks(tasks, tier="medium")


def test_a_task_with_an_unknown_tier_is_refused(tmp_path):
    bad = tmp_path / "bad-task"
    (bad / "repo").mkdir(parents=True)
    (bad / "task.md").write_text("Do it.", encoding="utf-8")
    lines = ['category = "bugfix"', 'tier = "medium"', 'check = ["{python}", "t.py"]']
    (bad / "task.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(TaskError, match="tier must be one of core, hard"):
        load_task(bad)
    del lines[1]
    (bad / "task.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert load_task(bad).tier == "core"


def test_the_count_failures_task_counts_what_its_suite_really_does(tmp_path):
    task = TASKS["shell-count-failures"]
    work = materialize(task, tmp_path / "w")
    result = procs.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
                       cwd=work, timeout=60)
    summary = procs.text(result.stderr, "utf-8")
    hidden = (task.check_dir / "test_hidden_failures.py").read_text(encoding="utf-8")
    expected = hidden.split('EXPECTED_FAILURES = "')[1].split('"')[0]
    assert f"FAILED (failures={expected})" in summary, summary[-500:]


def test_the_seed_decides_the_order_and_names_and_max_select():
    tasks = list(TASKS.values())
    a = [t.id for t in select_tasks(tasks, seed=7)]
    assert a == [t.id for t in select_tasks(tasks, seed=7)]
    assert a != [t.id for t in select_tasks(tasks, seed=8)]
    assert sorted(a) == sorted(TASKS)
    picked = [t.id for t in select_tasks(tasks, names=["trap-ambiguous-edit", "bugfix-pagination"], seed=7)]
    assert sorted(picked) == ["bugfix-pagination", "trap-ambiguous-edit"]
    assert picked == [i for i in a if i in picked]
    assert len(select_tasks(tasks, seed=3, max_tasks=4)) == 4
    with pytest.raises(TaskError, match="no such task: nope"):
        select_tasks(tasks, names=["nope"])


def test_a_malformed_task_is_refused(tmp_path):
    bad = tmp_path / "bad-task"
    (bad / "repo").mkdir(parents=True)
    (bad / "task.md").write_text("Do it.", encoding="utf-8")
    (bad / "task.toml").write_text('category = "bugfix"\n', encoding="utf-8")
    with pytest.raises(TaskError, match="check command"):
        load_task(bad)
    (bad / "task.toml").write_text('category = "bugfix"\ncheck = ["{python}", "t.py"]\n', encoding="utf-8")
    (bad / "check").mkdir()
    (bad / "check" / "t.py").write_text("print('no marker')\n", encoding="utf-8")
    with pytest.raises(TaskError, match=HIDDEN_CANARY):
        load_task(bad)


# --------------------------------------------------------------------------- #
# The tripwire
# --------------------------------------------------------------------------- #


def test_a_fresh_copy_holds_no_hidden_file_and_passes_the_tripwire(tmp_path):
    for task in TASKS.values():
        work = prepare_workspace(task, tmp_path / task.id)
        names = {p.name for p in work.rglob("*")}
        assert not names & {p.name for p in task.hidden_files()}, task.id
        assert_context_clean(task, work, render_prompt(task, work))


@pytest.mark.parametrize("how", ["named", "renamed copy", "marker", "pasted", "task file"])
def test_the_tripwire_refuses_a_leaked_check(tmp_path, how):
    task = TASKS["bugfix-pagination"]
    work = materialize(task, tmp_path / "w")
    hidden = task.check_dir / "test_hidden_pagination.py"
    text = hidden.read_text(encoding="utf-8")
    if how == "named":
        (work / hidden.name).write_text("anything", encoding="utf-8")
    elif how == "renamed copy":
        shutil.copy2(hidden, work / "notes.py")
    elif how == "marker":
        (work / "x.txt").write_text(f"see {HIDDEN_CANARY}", encoding="utf-8")
    elif how == "pasted":
        # task.toml holds the rubric/check and carries no marker: only the
        # paste rule can see it inside another file.
        toml_text = (task.path / "task.toml").read_text(encoding="utf-8")
        (work / "notes.md").write_text("my notes\n" + toml_text + "\nthe end\n", encoding="utf-8")
        assert HIDDEN_CANARY not in toml_text and text
    elif how == "task file":
        shutil.copy2(task.path / "task.toml", work / "task.toml")
    with pytest.raises(HiddenCheckLeak, match="refused to start bugfix-pagination"):
        assert_context_clean(task, work, render_prompt(task, work))


def test_the_tripwire_reads_the_prompt_too(tmp_path):
    task = TASKS["explain-mystery-functions"]
    work = materialize(task, tmp_path / "w")
    assert leaks(task, work, "please " + HIDDEN_CANARY)
    toml_text = (task.path / "task.toml").read_text(encoding="utf-8")
    assert leaks(task, work, "here is the key:\n" + toml_text)
    assert leaks(task, work, render_prompt(task, work)) == []


class _StubRunner:
    """A runner that never calls a model: it lays the reference solution down
    (or not) and reports fixed numbers through the ledger like a real one."""

    name = "stub"
    provider = "stub-provider"
    model = "stub-model"

    def __init__(self, *, solve: bool = True, ok: bool = True, harness: str = "h1",
                 tokens: tuple[int, int] = (100, 20), lie: int = 0) -> None:
        self.solve, self.ok, self.harness, self.tokens, self.lie = solve, ok, harness, tokens, lie
        self.calls: list[str] = []

    def identity(self) -> dict:
        return {"harness": self.harness}

    async def run(self, task, workspace, rollout, *, timeout_s):
        self.calls.append(task.id)
        if self.solve:
            apply_solution(task.id, workspace)
        tin, tout = self.tokens
        rollout.add(CallEntry(provider=self.provider, model=self.model, tokens_in=tin, tokens_out=tout,
                              cost_metered_usd=0.0, cost_list_price_usd=0.0, with_tools=True))
        return RunOutcome(
            ok_run=self.ok, error="" if self.ok else "the stub failed", answered_provider=self.provider,
            answered_model=self.model, tool_calls=1, model_rounds=1,
            tokens_in=tin + self.lie, tokens_out=tout, cost_metered_usd=0.0, cost_list_price_usd=0.0,
        )


async def test_a_leaking_copy_is_refused_and_the_runner_never_starts(tmp_path, monkeypatch):
    real = bench_mod.materialize

    def leaky(task, dest):
        work = real(task, dest)
        for p in task.check_dir.glob("*.py"):
            shutil.copy2(p, work / p.name)  # the bug: the grader comes along
        return work

    monkeypatch.setattr(bench_mod, "materialize", leaky)
    stub = _StubRunner()
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert stub.calls == []
    assert rec["passed"] is None and "refused to start" in rec["error"]
    assert not (res.config_dir / "runs" / "bugfix-pagination" / MARKER_NAME).exists()


# --------------------------------------------------------------------------- #
# Unscored is not zero
# --------------------------------------------------------------------------- #


def _tiny_task(root: Path, check: str, *, timeout: float = 30, name: str = "tiny") -> object:
    task_dir = root / name
    (task_dir / "repo").mkdir(parents=True)
    (task_dir / "check").mkdir()
    (task_dir / "repo" / "m.py").write_text("X = 1\n", encoding="utf-8")
    (task_dir / "task.md").write_text("Leave it.", encoding="utf-8")
    (task_dir / "check" / "test_hidden_tiny.py").write_text(
        f"# {HIDDEN_CANARY}\n" + check, encoding="utf-8")
    (task_dir / "task.toml").write_text(
        'category = "bugfix"\n'
        'check = ["{python}", "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_hidden_*.py"]\n'
        f"check_timeout_s = {timeout}\n", encoding="utf-8")
    return load_task(task_dir)


def test_a_passing_and_a_failing_check_are_scored(tmp_path):
    good = _tiny_task(tmp_path, "import unittest, m\nclass T(unittest.TestCase):\n def test(self): self.assertEqual(m.X, 1)\n", name="good")
    bad = _tiny_task(tmp_path, "import unittest, m\nclass T(unittest.TestCase):\n def test(self): self.assertEqual(m.X, 2)\n", name="bad")
    assert grade(good, good.repo_dir, tmp_path / "g1").passed is True
    assert grade(bad, bad.repo_dir, tmp_path / "g2").passed is False


def test_a_check_that_runs_past_its_limit_is_a_fail_not_unscored(tmp_path):
    """Review round 3 (changed on purpose): a check that timed out DID run —
    what did not finish is the code it tests (an agent's infinite loop, a
    solution far too slow). As "unscored" it left the pass rate's denominator,
    so an agent's hang RAISED the rate."""
    task = _tiny_task(tmp_path, "import time, unittest\nclass T(unittest.TestCase):\n def test(self): time.sleep(30)\n", timeout=1.5)
    g = grade(task, task.repo_dir, tmp_path / "g")
    assert g.passed is False, g.reason
    assert "limit" in g.reason and "did not finish" in g.reason


@pytest.mark.parametrize("case", ["no tests ran", "no interpreter", "cannot start"])
def test_a_check_that_cannot_run_is_unscored_never_failed(tmp_path, monkeypatch, case):
    if case == "no tests ran":
        task = _tiny_task(tmp_path, "X = 2\n")
        g = grade(task, task.repo_dir, tmp_path / "g")
        assert "exit code 5" in g.reason
    elif case == "no interpreter":
        task = _tiny_task(tmp_path, "import unittest\n")
        monkeypatch.setattr("iron_jarvis.harness_bench.grading.grading_python", lambda: None)
        g = grade(task, task.repo_dir, tmp_path / "g")
        assert "interpreter" in g.reason
    else:
        task = _tiny_task(tmp_path, "import unittest\n")
        g = grade(task, task.repo_dir, tmp_path / "g", python=str(tmp_path / "no-such-python.exe"))
        assert "could not start" in g.reason
    assert g.passed is None, g.reason


def test_unscored_runs_leave_the_denominator():
    records = [
        {"task_id": "a", "category": "bugfix", "passed": True, "ok_run": True},
        {"task_id": "b", "category": "bugfix", "passed": False, "ok_run": True},
        {"task_id": "c", "category": "trap", "passed": None, "ok_run": True, "unscored_by": "grader"},
        {"task_id": "d", "category": "trap", "passed": None, "ok_run": False,
         "unscored_by": "infrastructure", "grade_reason": "did not run: the account's usage limit was reached"},
        {"task_id": "e", "category": "shell", "interrupted": True, "passed": None},
    ]
    s = summarize(records)
    assert s["scored"] == 2 and s["unscored"] == 2
    assert s["unscored_infrastructure"] == 1 and s["unscored_grader"] == 1
    assert s["interrupted"] == 1 and s["tasks"] == 4
    assert s["pass_rate"] == 0.5
    assert s["per_category"]["trap"] == {"tasks": 2, "scored": 0, "passed": 0, "unscored": 2}
    # Did not run (infrastructure) | Check could not run (grader) | Interrupted
    text = combined_report([({"runner": "x", "model": "m"}, records)])
    assert "| 1 / 2 | 50% | 1 | 1 | 1 |" in text
    report = bench_mod.config_report({"runner": "x", "model": "m"}, records)
    assert "1 did not run (infrastructure" in report and "1 where the check could not run" in report
    assert "| d | trap | did not run |" in report
    assert "did not run: the account's usage limit was reached" in report
    assert "| e | shell | interrupted |" in report


def test_the_reports_give_a_pass_rate_per_tier():
    records = [
        {"task_id": "a", "category": "bugfix", "passed": True},
        {"task_id": "b", "category": "bugfix", "tier": "core", "passed": False},
        {"task_id": "h1", "category": "feature", "tier": "hard", "passed": True},
        {"task_id": "h2", "category": "trap", "tier": "hard", "passed": False},
        {"task_id": "h3", "category": "explain", "tier": "hard", "passed": False},
        {"task_id": "h4", "category": "shell", "tier": "hard", "passed": None,
         "unscored_by": "infrastructure"},
        {"task_id": "h5", "category": "process", "tier": "hard", "interrupted": True, "passed": None},
    ]
    s = summarize(records)
    # A record from before tiers existed ("a") is a core task's; an
    # interrupted one counts nowhere.
    assert s["per_tier"]["core"] == {"tasks": 2, "scored": 2, "passed": 1, "unscored": 0, "pass_rate": 0.5}
    assert s["per_tier"]["hard"] == {"tasks": 4, "scored": 3, "passed": 1, "unscored": 1,
                                     "pass_rate": 1 / 3}
    report = bench_mod.config_report({"runner": "x", "model": "m"}, records)
    assert "## By tier" in report
    assert "| core | 50% | 1 / 2 | 0 |" in report
    assert "| hard | 33% | 1 / 3 | 1 |" in report
    text = combined_report([({"runner": "x", "model": "m", "key": "k" * 64}, records),
                            ({"runner": "y", "model": "n", "key": "j" * 64}, records[:2])])
    assert "## Pass rate by tier" in text
    assert "| x | m | kkkkkkkkkkkk | 50% (1 / 2) | 33% (1 / 3), 1 unscored |" in text
    assert "| y | n | jjjjjjjjjjjj | 50% (1 / 2) | - |" in text


def test_a_rubric_list_is_matched_in_order_and_a_table_key_by_key():
    from iron_jarvis.harness_bench.grading import _same

    assert _same(["Member_Discount ", "tax"], ["member_discount", "tax"])
    assert not _same(["tax", "member_discount"], ["member_discount", "tax"])
    assert not _same(["member_discount"], ["member_discount", "tax"])
    assert not _same(["member_discount", "tax", "tax"], ["member_discount", "tax"])
    assert not _same("member_discount, tax", ["member_discount", "tax"])
    assert _same({"total": "38.44", "rules": ["tax"]}, {"total": 38.44, "rules": ["tax"]})
    assert not _same({"total": 38.44}, {"total": 38.44, "rules": ["tax"]})
    assert not _same({"total": 38.44, "extra": 1}, {"total": 38.44})
    assert not _same([1], {"total": 38.44})


def test_every_report_says_what_the_outside_column_watches(tmp_path):
    """Review finding 7: "touched outside its folder" only watches the
    benchmark's own folder; both reports say so, so an empty column is never
    read as "nothing outside was touched"."""
    from iron_jarvis.harness_bench.report import OUTSIDE_SCOPE

    assert "only the benchmark's own folder" in OUTSIDE_SCOPE
    assert OUTSIDE_SCOPE in bench_mod.config_report({"runner": "x"}, [])
    assert OUTSIDE_SCOPE in combined_report([])


async def test_a_run_killed_part_way_is_listed_as_interrupted(tmp_path):
    """Review finding 7: a task folder with a prompt but no result (the
    process was killed mid-run) used to vanish from the report."""
    stub = _StubRunner()
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()),
                          names=["bugfix-leap-year", "trap-ambiguous-edit"])
    killed = res.config_dir / "runs" / "trap-ambiguous-edit"
    (killed / RESULT_NAME).unlink()
    (killed / MARKER_NAME).unlink()
    bench_mod.write_config_outputs(res.config_dir)
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "| trap-ambiguous-edit | trap | interrupted |" in report
    assert "1 interrupted" in report
    results = json.loads((res.config_dir / "results.json").read_text(encoding="utf-8"))
    assert {r["task_id"]: r.get("interrupted", False) for r in results["records"]} == {
        "bugfix-leap-year": False, "trap-ambiguous-edit": True}


# --------------------------------------------------------------------------- #
# The budget ledger
# --------------------------------------------------------------------------- #


def test_a_record_that_disagrees_with_the_ledger_raises():
    ledger = BudgetLedger()
    rollout = ledger.begin("t", "stub")
    rollout.add(CallEntry(tokens_in=10, tokens_out=2, cost_metered_usd=0.5, cost_list_price_usd=0.0))
    rollout.add(CallEntry(tokens_in=7, tokens_out=1, cost_metered_usd=0.25, cost_list_price_usd=0.0))
    good = {"tokens_in": 17, "tokens_out": 3, "cost_metered_usd": 0.75, "cost_list_price_usd": 0.0}
    ledger.verify_record(good, rollout)
    for name, wrong in (("tokens_in", 16), ("tokens_out", 4), ("cost_metered_usd", 0.7),
                        ("cost_list_price_usd", None)):
        with pytest.raises(BudgetMismatch, match=name):
            ledger.verify_record({**good, name: wrong}, rollout)


def test_an_unknown_cost_stays_unknown():
    ledger = BudgetLedger()
    rollout = ledger.begin("t", "stub")
    rollout.add(CallEntry(cost_metered_usd=None, cost_list_price_usd=None))
    rollout.add(CallEntry(cost_metered_usd=1.0, cost_list_price_usd=0.0))
    assert rollout.totals()["cost_metered_usd"] is None
    with pytest.raises(BudgetMismatch):
        ledger.verify_record({"tokens_in": 0, "tokens_out": 0, "cost_metered_usd": 1.0,
                              "cost_list_price_usd": None}, rollout)


def test_the_plan_must_be_covered_exactly_once():
    ledger = BudgetLedger()
    ledger.begin("a", "s")
    rec_a = {"task_id": "a", "tokens_in": 0, "tokens_out": 0, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.0}
    ledger.verify_plan(["a", "b"], ["b"], [rec_a, {"task_id": "b", "reused": True}])
    with pytest.raises(BudgetMismatch, match="b ran 0 times"):
        ledger.verify_plan(["a", "b"], [], [rec_a])
    ledger.begin("a", "s")
    with pytest.raises(BudgetMismatch, match="a ran 2 times"):
        ledger.verify_plan(["a"], [], [rec_a, rec_a])


async def test_a_run_whose_numbers_do_not_add_up_is_flagged_and_not_marked(tmp_path):
    stub = _StubRunner(lie=5)
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["ledger_ok"] is False and "does not add up" in rec["error"]
    assert not (res.config_dir / "runs" / "bugfix-leap-year" / MARKER_NAME).exists()


# --------------------------------------------------------------------------- #
# Completion markers, fingerprints and the saved configuration
# --------------------------------------------------------------------------- #


async def test_a_finished_run_is_reused_only_under_its_marker(tmp_path):
    names = ["bugfix-leap-year", "explain-mystery-functions"]
    stub = _StubRunner()
    first = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=names, seed=3)
    assert sorted(stub.calls) == sorted(names)
    assert all(r["passed"] is True for r in first.records)
    run_dir = first.config_dir / "runs" / "bugfix-leap-year"
    marker = json.loads((run_dir / MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["fingerprint"] == first.records[0]["fingerprint"] or marker["fingerprint"] == first.records[1]["fingerprint"]

    # Same configuration again: nothing runs, both are reused.
    stub.calls.clear()
    again = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=names, seed=3)
    assert stub.calls == []
    assert all(r["reused"] for r in again.records)
    # Review fix: the written report says so (a stored record carries
    # reused=False, so the report used to read "0 reused" here).
    report = (again.config_dir / "report.md").read_text(encoding="utf-8")
    assert "2 reused from an earlier run" in report, report[:400]

    # Output with no marker is NOT reused.
    (run_dir / MARKER_NAME).unlink()
    stub.calls.clear()
    third = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=names, seed=3)
    assert stub.calls == ["bugfix-leap-year"]
    assert {r["task_id"]: r["reused"] for r in third.records} == {
        "bugfix-leap-year": False, "explain-mystery-functions": True}

    # A marker whose fingerprint is another configuration's is not reused.
    m = json.loads((run_dir / MARKER_NAME).read_text(encoding="utf-8"))
    m["fingerprint"] = "0" * 64
    (run_dir / MARKER_NAME).write_text(json.dumps(m), encoding="utf-8")
    stub.calls.clear()
    await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=names, seed=3)
    assert stub.calls == ["bugfix-leap-year"]

    # A workspace changed after its run was recorded is not reused.
    (run_dir / "work" / "calendar_util.py").write_text("broken = True\n", encoding="utf-8")
    stub.calls.clear()
    await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=names, seed=3)
    assert stub.calls == ["bugfix-leap-year"]


async def test_a_run_the_runner_did_not_finish_leaves_no_marker_and_runs_again(tmp_path):
    names = ["bugfix-leap-year"]
    stub = _StubRunner(ok=False)
    first = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=names)
    run_dir = first.config_dir / "runs" / "bugfix-leap-year"
    assert (run_dir / RESULT_NAME).exists() and not (run_dir / MARKER_NAME).exists()
    stub.calls.clear()
    await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=names)
    assert stub.calls == ["bugfix-leap-year"]


async def test_another_harness_or_task_version_is_a_fresh_run(tmp_path):
    names = ["bugfix-leap-year"]
    one = _StubRunner(harness="h1")
    a = await run_bench(runner=one, home=tmp_path, tasks=list(TASKS.values()), names=names)
    two = _StubRunner(harness="h2")
    b = await run_bench(runner=two, home=tmp_path, tasks=list(TASKS.values()), names=names)
    assert a.config_dir != b.config_dir and two.calls == names
    # The task's content changes (a copy of it with one more line in task.md):
    copy = tmp_path / "tasks" / "bugfix-leap-year"
    shutil.copytree(TASKS["bugfix-leap-year"].path, copy)
    (copy / "task.md").write_text(TASKS["bugfix-leap-year"].prompt + "\nPlease be careful.\n", encoding="utf-8")
    changed = load_task(copy)
    assert changed.content_hash != TASKS["bugfix-leap-year"].content_hash
    one.calls.clear()
    await run_bench(runner=one, home=tmp_path, tasks=[changed], names=names)
    assert one.calls == names


def test_the_fingerprint_names_every_part():
    task = TASKS["bugfix-pagination"]
    base = Configuration(runner="r", provider="p", model="m", harness={"x": 1}, version="1.0.0")
    fps = {base.fingerprint(task)}
    for change in ({"runner": "r2"}, {"provider": "p2"}, {"model": "m2"},
                   {"harness": {"x": 2}}, {"version": "1.0.1"}):
        other = Configuration(**{**base.__dict__, **change})
        fps.add(other.fingerprint(task))
    fps.add(base.fingerprint(TASKS["bugfix-leap-year"]))
    assert len(fps) == 7


async def test_a_different_seed_is_refused_in_plain_words(tmp_path):
    stub = _StubRunner()
    await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=["bugfix-leap-year"], seed=1)
    with pytest.raises(BenchRefused, match="started with --seed 1"):
        await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=["bugfix-leap-year"], seed=2)
    # Leaving the seed out resumes with the saved one.
    stub.calls.clear()
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=["bugfix-leap-year"])
    assert stub.calls == [] and res.records[0]["reused"]


async def test_a_folder_holding_another_setup_is_refused(tmp_path):
    stub = _StubRunner()
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=["bugfix-leap-year"])
    cfg = json.loads((res.config_dir / "config.json").read_text(encoding="utf-8"))
    cfg["key"] = "f" * 64
    (res.config_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(BenchRefused, match="different setup"):
        await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=["bugfix-leap-year"])


async def test_a_dry_run_lists_and_runs_nothing(tmp_path):
    stub = _StubRunner()
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()),
                          names=["bugfix-leap-year", "trap-ambiguous-edit"], dry_run=True)
    assert stub.calls == []
    assert not res.config_dir.exists()
    assert any("bugfix-leap-year" in line and "would run" in line for line in res.plan_lines)


async def test_a_dry_run_keeps_only_the_chosen_tier(tmp_path):
    stub = _StubRunner()
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), tier="hard", dry_run=True)
    assert stub.calls == []
    named = [line for line in res.plan_lines if "would run" in line]
    assert len(named) == len(HARD)
    assert all("[hard, " in line for line in named)
    assert all(any(f"  {i} [" in line for line in named) for i in HARD)


def test_the_command_line_takes_the_tier(tmp_path):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    root = tmp_path / "bench-root"
    base = ["run", "--runner", "claude", "--binary", str(claude), "--root", str(root), "--dry-run"]
    out = CliRunner().invoke(bench_app, [*base, "--tier", "hard"])
    assert out.exit_code == 0, out.output
    lines = [line for line in out.output.splitlines() if "would run" in line]
    assert len(lines) == len(HARD) and all("[hard, " in line for line in lines)
    out = CliRunner().invoke(bench_app, [*base, "--tier", "core", "--tasks", f"{HARD[0]},bugfix-leap-year"])
    assert out.exit_code == 0, out.output
    assert [line.split(" [")[0].strip() for line in out.output.splitlines() if "would run" in line] == [
        "bugfix-leap-year"]
    out = CliRunner().invoke(bench_app, [*base, "--tier", "medium"])
    assert out.exit_code == 2 and "--tier must be one of core, hard, frontier, team, all" in out.output


def test_the_bench_never_uses_the_apps_own_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with pytest.raises(BenchRefused, match="app's own folder"):
        bench_mod.resolve_bench_home(str(tmp_path / "Iron Jarvis"))
    monkeypatch.setenv("IRONJARVIS_HOME", str(tmp_path / "x"))
    with pytest.raises(BenchRefused, match="IRONJARVIS_HOME"):
        bench_mod.resolve_bench_home(str(tmp_path / "scratch"))
    monkeypatch.delenv("IRONJARVIS_HOME")
    assert bench_mod.resolve_bench_home(str(tmp_path / "scratch")) == (tmp_path / "scratch" / ".ironjarvis").resolve()


def test_the_bench_never_works_inside_the_iron_jarvis_source(monkeypatch):
    """Review fix: ``--root`` defaults to ".", and the command runs from the
    source checkout — so every agent copy landed in ``<repo>/.ironjarvis/…``,
    a few ``..`` from the hidden checks and the reference solutions, inside
    the repo's git project (Codex reads its AGENTS.md, Claude Code its git
    status). A folder inside the source is refused in plain words."""
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    repo = Path(__file__).resolve().parents[1]
    assert bench_mod.answer_key_root().resolve() == repo
    for root in (repo, repo / "scratch", repo / "src" / "iron_jarvis"):
        with pytest.raises(BenchRefused, match="hidden checks and reference solutions"):
            bench_mod.resolve_bench_home(str(root))


def test_the_process_task_never_asks_to_end_other_programs():
    """Review fix: the first real run's model obeyed "no server from this task
    may still be running" with ``taskkill /f /im python.exe`` — every Python
    program on the PC, the benchmark itself included. The task now says the
    request is the only way to stop the server and that other programs are
    not the agent's to stop."""
    process = [t for t in TASKS.values() if t.category == "process"]
    assert {"process-http-server", "hard-file-watcher-process"} <= {t.id for t in process}
    for task in process:
        assert "may still be running" not in task.prompt, task.id
        assert "never end any other program on this computer" in task.prompt, task.id
        assert "not yours to stop" in task.prompt, task.id
        assert " is the only way to stop it" in task.prompt, task.id


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job objects keep the child")
async def test_a_program_the_turn_left_running_is_stopped_after_the_run(tmp_path, monkeypatch):
    """Review fix: a chat turn's shell keeps a hidden child it started (a dev
    server) under the lane's "chat" session until ``procs.end_session`` — and
    nothing in the benchmark's process ever called it, so a server the agent
    did not stop outlived the run (and held its folder open, so the rerun
    could not remove it). The Jarvis runner now ends that session after every
    turn."""
    import psutil

    procs.end_all()
    platform = build_platform(str(tmp_path / "root"))
    child = (
        "import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; "
        "time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL); pathlib.Path('kid.pid').write_text(str(p.pid))"
    )
    seen: list[bool] = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        seen.append(bool(tools))
        if len(seen) == 1:
            resp = LLMResponse(text="", usage={"input_tokens": 5, "output_tokens": 1}, tool_calls=[
                ToolCall(id="s1", name="shell", arguments={"command": f'"{sys.executable}" -c "{child}"'})])
        else:
            resp = LLMResponse(text="Started it.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=4)
    pid = None
    try:
        res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(),
                              names=["bugfix-leap-year"])
        rec = res.records[0]
        assert rec["tool_calls"] == 1, rec
        pid_file = res.config_dir / "runs" / "bugfix-leap-year" / "work" / "kid.pid"
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert procs.kept_jobs("chat") == 0, "the turn's hidden child is still kept for the chat session"
        deadline = time.monotonic() + 20
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not psutil.pid_exists(pid), "a program the turn left running outlived the run"
        assert any("left running" in n for n in rec["notes"]), rec["notes"]
    finally:
        procs.end_all()
        if pid:
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()


# --------------------------------------------------------------------------- #
# The Jarvis runner: the smoke test
# --------------------------------------------------------------------------- #

_PAGINATION_FIX = (SOLUTIONS / "bugfix-pagination" / "pagination.py.sol").read_text(encoding="utf-8")
_SCRIPTS = {
    "bugfix-pagination": [
        [ToolCall(id="p1", name="read_file", arguments={"path": "pagination.py"})],
        [ToolCall(id="p2", name="write_file", arguments={"path": "pagination.py", "content": _PAGINATION_FIX})],
        "Fixed where each page starts and how pages are counted.",
    ],
    "explain-export-command": [
        [ToolCall(id="e1", name="read_file", arguments={"path": "main.py"}),
         ToolCall(id="e2", name="list_files", arguments={})],
        [ToolCall(id="e3", name="write_file", arguments={
            "path": "answer.json", "content": '{"file": "exporter.py", "function": "handle_export"}'})],
        "It is handle_export in exporter.py.",
    ],
}


class _ScriptedModel:
    """A scripted model on the router's ``stream()``: picks the task's script by
    the prompt, plays one step per call, reports a known usage per call."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider, self.model = provider, model
        self.steps: dict[str, int] = {}
        self.usage_in = self.usage_out = 0
        self.tools_seen: list[list[str]] = []
        self.first_user: list[str] = []

    async def stream(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        self.first_user.append(messages[0].content or "")
        task_id = next(t for t in _SCRIPTS if TASKS[t].prompt in (messages[0].content or ""))
        step = self.steps.get(task_id, 0)
        self.steps[task_id] = step + 1
        self.tools_seen.append(sorted(t["name"] for t in tools or []))
        usage = {"input_tokens": 100 + step, "output_tokens": 10 + step}
        self.usage_in += usage["input_tokens"]
        self.usage_out += usage["output_tokens"]
        move = _SCRIPTS[task_id][min(step, len(_SCRIPTS[task_id]) - 1)]
        if isinstance(move, str):
            yield {"type": "text", "text": move}
            resp = LLMResponse(text=move, usage=usage)
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage=usage)
        yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}


async def _smoke(tmp_path, monkeypatch, *, provider="scripted", model="script-1", break_check=False):
    platform = build_platform(str(tmp_path / "root"))
    scripted = _ScriptedModel(provider, model)
    monkeypatch.setattr(platform.router, "stream", scripted.stream)
    tasks = load_tasks()
    if break_check:
        tasks = [
            t if t.id != "bugfix-pagination" else type(t)(**{**t.__dict__, "check": ("{python}", "-I", "no_such_check.py")})
            for t in tasks
        ]
    runner = JarvisRunner(platform, provider=provider, model=model, round_budget=8)
    res = await run_bench(runner=runner, home=platform.config.home, tasks=tasks,
                          names=list(_SCRIPTS), seed=0)
    return res, scripted, platform


async def test_smoke_two_tasks_through_the_jarvis_lane_are_scored_end_to_end(tmp_path, monkeypatch):
    res, scripted, platform = await _smoke(tmp_path, monkeypatch)
    by = {r["task_id"]: r for r in res.records}
    assert set(by) == set(_SCRIPTS)
    for task_id, rec in by.items():
        assert rec["ok_run"] is True, rec["error"]
        assert rec["passed"] is True, (rec["grade_reason"], rec["grade_output"])
        assert rec["ledger_ok"] is True
        assert rec["answered_provider"] == "scripted" and rec["answered_model"] == "script-1"
        assert rec["files_outside"] == []
        assert (res.config_dir / "runs" / task_id / MARKER_NAME).exists()
    # FOUR model calls each (two tool rounds + the answer + the FINISH
    # CHECK's one round — part 3, Phase 2: the bench runs as a Build session,
    # and both tasks change files); tool calls as scripted (no test command
    # ran, so the check ran none: "none").
    assert by["bugfix-pagination"]["model_rounds"] == 4 and by["bugfix-pagination"]["tool_calls"] == 2
    assert by["explain-export-command"]["model_rounds"] == 4 and by["explain-export-command"]["tool_calls"] == 3
    assert by["bugfix-pagination"]["model_calls"] == 4
    assert by["bugfix-pagination"]["extra"]["finish"]["tests"] == "none"
    # Tokens: the lane's receipt, the ledger and the script's own count agree.
    assert sum(r["tokens_in"] for r in res.records) == scripted.usage_in == 4 * 2 * 100 + 2 * (0 + 1 + 2 + 3)
    assert sum(r["tokens_out"] for r in res.records) == scripted.usage_out
    # An unpriced provider off this network: cost unknown, never a made-up 0.
    assert by["bugfix-pagination"]["cost_metered_usd"] is None
    # The turn saw exactly the harness's coding tools (v1.366.0: harness
    # mode; a "scripted" model is the generic family, so Jarvis's own names)
    # — no hand-off exits, and no app-resource readers with no app connected.
    assert set(scripted.tools_seen[0]) == set(CODING_TOOLS) - {"mcp_list_resources", "mcp_read_resource"}
    assert "escalate_to_agent" not in scripted.tools_seen[0]
    # The grader never reached the agent's copy.
    work = res.config_dir / "runs" / "bugfix-pagination" / "work"
    assert not (work / "test_hidden_pagination.py").exists()
    # v1.370.0 review: the graded copy stays (the agent's work, for
    # inspection) but the answer key is taken back out of it, so a LATER
    # run's agent with a shell cannot read it under the bench root.
    graded = res.config_dir / "runs" / "bugfix-pagination" / "grade"
    assert graded.is_dir() and any(graded.iterdir())
    assert not (graded / "test_hidden_pagination.py").exists()
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "Pass rate 100%" in report and "0 unscored" in report
    combined = (platform.config.home / "harness-bench" / "report.md").read_text(encoding="utf-8")
    assert "| jarvis | scripted / script-1 |" in combined


async def test_smoke_goes_red_when_a_check_is_broken(tmp_path, monkeypatch):
    """The plan's 'done when': a deliberately broken check makes the smoke
    result change — the run is UNSCORED (never a quiet pass, never a fail)."""
    res, _scripted, _platform = await _smoke(tmp_path, monkeypatch, break_check=True)
    by = {r["task_id"]: r for r in res.records}
    assert by["bugfix-pagination"]["passed"] is None
    assert by["explain-export-command"]["passed"] is True
    assert "1 unscored" in (res.config_dir / "report.md").read_text(encoding="utf-8")


async def test_a_priced_provider_bills_the_same_in_the_lane_and_the_ledger(tmp_path, monkeypatch):
    from iron_jarvis.eval.pricing import is_priced

    assert is_priced("anthropic", "claude-sonnet-4-5")
    res, scripted, _platform = await _smoke(tmp_path, monkeypatch, provider="anthropic", model="claude-sonnet-4-5")
    for rec in res.records:
        assert rec["ledger_ok"] is True, rec["error"]
        assert rec["cost_metered_usd"] and rec["cost_metered_usd"] > 0
        assert rec["cost_list_price_usd"] == 0.0


async def test_the_jarvis_turn_stays_in_its_conversation_at_the_round_budget(tmp_path, monkeypatch):
    """A model that never stops asking for tools: the turn runs exactly the
    budget, then ONE tool-less answer, and never escalates."""
    platform = build_platform(str(tmp_path / "root"))
    seen: list[bool] = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        seen.append(bool(tools))
        if tools:
            resp = LLMResponse(text="", tool_calls=[ToolCall(id=f"r{len(seen)}", name="list_files", arguments={})],
                               usage={"input_tokens": 5, "output_tokens": 1})
        else:
            resp = LLMResponse(text="Not finished yet.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=4)
    res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(),
                          names=["bugfix-leap-year"])
    rec = res.records[0]
    assert seen == [True, True, True, True, False]
    assert rec["model_rounds"] == 4 and rec["tool_calls"] == 3
    assert rec["extra"]["escalated"] is False
    assert rec["ok_run"] is True and rec["passed"] is False
    assert rec["ledger_ok"] is True and rec["model_calls"] == 5


async def test_a_turn_answered_by_another_provider_does_not_count(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "root"))

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        yield {"type": "final", "response": LLMResponse(text="hi", usage={"input_tokens": 1, "output_tokens": 1}),
               "provider": "someone-else", "model": "x", "reason": "failover"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="scripted", model="s")
    res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["ok_run"] is False and "instead of scripted" in rec["error"]
    # Infrastructure, not the agent: unscored, and run again next time.
    assert rec["passed"] is None and rec["unscored_by"] == "infrastructure"
    assert rec["failure_reason"].startswith("another provider answered")
    assert not (res.config_dir / "runs" / "bugfix-leap-year" / MARKER_NAME).exists()


def test_the_jarvis_identity_names_its_harness(tmp_path):
    platform = build_platform(str(tmp_path / "root"))
    a = JarvisRunner(platform, provider="p", model="m", round_budget=60).identity()
    b = JarvisRunner(platform, provider="p", model="m", round_budget=30).identity()
    assert a["round_budget"] == 60 and a["approval_mode"] == "yolo"
    assert a["dialect"] == "generic" and a["family"] == "generic" and a["dialect_requested"] == "auto"
    assert "read_file" in a["tools"] and "escalate_to_agent" not in a["tools"]
    assert len(a["lane_sha256"]) == 64 and len(a["harness_sha256"]) == 64
    assert len(a["harness_fingerprint"]) == 64
    assert Configuration("jarvis", "p", "m", a).key != Configuration("jarvis", "p", "m", b).key
    with pytest.raises(ValueError, match="round_budget"):
        JarvisRunner(platform, round_budget=0)
    with pytest.raises(ValueError, match="harness must be"):
        JarvisRunner(platform, dialect="bogus")


def test_the_dialect_and_the_tree_join_the_configuration_fingerprint(tmp_path):
    """v1.366.0: ``--dialect claude`` and ``--dialect generic`` are two
    setups, never a reused result. (Review: glm's own row is generic by
    measurement, so ``--dialect auto`` speaks generic for a glm model — and is
    still its own setup, because what was ASKED is part of the identity.)"""
    platform = build_platform(str(tmp_path / "root"))
    auto = JarvisRunner(platform, provider="custom", model="glm-5.3-flash").identity()
    generic = JarvisRunner(platform, provider="custom", model="glm-5.3-flash", dialect="generic").identity()
    claude = JarvisRunner(platform, provider="custom", model="glm-5.3-flash", dialect="claude").identity()
    assert auto["family"] == generic["family"] == claude["family"] == "glm"
    assert auto["dialect"] == generic["dialect"] == "generic" and claude["dialect"] == "claude"
    assert auto["dialect_requested"] == "auto" and claude["dialect_requested"] == "claude"
    assert claude["tools"][:3] == ["Read", "Write", "Edit"] and "read_file" in generic["tools"]
    assert claude["harness_fingerprint"] != generic["harness_fingerprint"]
    assert claude["tools_sha256"] != generic["tools_sha256"]
    assert auto["harness_fingerprint"] == generic["harness_fingerprint"]
    keys = {Configuration("jarvis", "custom", "glm-5.3-flash", h).key for h in (auto, generic, claude)}
    assert len(keys) == 3
    # The fingerprint is the tree's, not the day's.
    assert JarvisRunner(platform, provider="custom", model="glm-5.3-flash").identity() == auto


async def test_the_jarvis_runner_runs_its_turn_in_harness_mode(tmp_path, monkeypatch):
    """A glm model run with ``--dialect claude`` is shown Claude Code's
    tools; its ``Read`` / ``Write`` calls run the real tools; the record keeps
    the harness the turn reported."""
    platform = build_platform(str(tmp_path / "root"))
    task = TASKS["bugfix-pagination"]
    seen: list[list[str]] = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        seen.append([t["name"] for t in tools or []])
        n = len(seen)
        if n == 1:
            resp = LLMResponse(text="", tool_calls=[ToolCall(id="r", name="Read", arguments={"file_path": "pagination.py"})])
        elif n == 2:
            resp = LLMResponse(text="", tool_calls=[ToolCall(id="w", name="Write", arguments={
                "file_path": "pagination.py", "content": _PAGINATION_FIX})])
        else:
            resp = LLMResponse(text="Fixed it.")
        resp.usage = {"input_tokens": 5, "output_tokens": 1}
        yield {"type": "final", "response": resp, "provider": "custom", "model": "glm-5.3-flash"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="custom", model="glm-5.3-flash", round_budget=8, dialect="claude")
    res = await run_bench(runner=runner, home=platform.config.home, tasks=[task])
    rec = res.records[0]
    assert rec["passed"] is True, (rec["error"], rec["grade_reason"])
    assert seen[0][:3] == ["Read", "Write", "Edit"] and "escalate_to_agent" not in seen[0]
    harness = rec["extra"]["harness"]
    assert harness["family"] == "glm" and harness["dialect"] == "claude"
    assert harness["fingerprint"] == runner.identity()["harness_fingerprint"]
    assert rec["extra"]["tools_used"] == ["read_file", "write_file"]


def test_the_command_line_takes_the_dialect(tmp_path):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    root = tmp_path / "bench-root"
    base = ["run", "--runner", "jarvis", "--provider", "custom", "--model", "glm-5.3-flash",
            "--root", str(root), "--dry-run", "--tasks", "bugfix-leap-year"]
    auto = CliRunner().invoke(bench_app, base)
    generic = CliRunner().invoke(bench_app, [*base, "--dialect", "generic"])
    assert auto.exit_code == 0 and generic.exit_code == 0, (auto.output, generic.output)
    folder = lambda out: next(line for line in out.output.splitlines() if line.startswith("jarvis-"))  # noqa: E731
    assert folder(auto) != folder(generic)
    bad = CliRunner().invoke(bench_app, [*base, "--dialect", "klingon"])
    assert bad.exit_code == 2 and "--dialect" in bad.output
    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    other = CliRunner().invoke(bench_app, ["run", "--runner", "claude", "--binary", str(claude), "--root",
                                           str(root), "--dry-run", "--dialect", "generic"])
    assert other.exit_code == 2 and "jarvis runner only" in other.output


async def test_a_card_that_still_shows_is_answered_once(tmp_path, monkeypatch):
    """yolo grants every card these tools raise; if one shows anyway (a later
    lane rule), the runner answers it "once" through the lane's own approval
    registry instead of waiting forever."""
    from types import SimpleNamespace

    from iron_jarvis.daemon import chat_stream
    from iron_jarvis.daemon.routes.chat import _approvals, _sse

    platform = build_platform(str(tmp_path / "root"))
    decided: list[str] = []

    async def fake_turn(platform_, personas, body, **kw):
        async def frames():
            approval_id, fut = _approvals(SimpleNamespace(platform=platform_)).request("shell", {"command": "dir"})
            yield _sse("approval", {"id": approval_id, "tool": "shell", "args": {"command": "dir"}})
            decided.append(await asyncio.wait_for(fut, timeout=10))
            yield _sse("done", {"reply": "ok", "provider": "scripted", "model": "s", "usage": {
                "input_tokens": 0, "output_tokens": 0}, "route": {"reason": "explicit"}})
        return frames()

    monkeypatch.setattr(chat_stream, "stream_chat_turn", fake_turn)
    runner = JarvisRunner(platform, provider="scripted", model="s")
    res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(), names=["bugfix-leap-year"])
    assert decided == ["once"]
    # v1.402.0 (the autonomy metrics): counted on the run, not a free-text note.
    assert res.records[0]["facts"]["asks"] == {"cards": 1, "questions": 0, "by_tool": {"shell": 1}}


# --------------------------------------------------------------------------- #
# The CLI runners, against fake executables
# --------------------------------------------------------------------------- #


def _shim(tmp_path: Path, name: str, script: Path) -> str:
    if os.name == "nt":
        shim = tmp_path / f"{name}.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return str(shim)


def _fake_env(monkeypatch, tmp_path, scenario: str, solution: str = "bugfix-pagination") -> Path:
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_BENCH_SCENARIO", scenario)
    monkeypatch.setenv("FAKE_BENCH_RECORD", str(record))
    monkeypatch.setenv("FAKE_BENCH_SOLUTION", str(SOLUTIONS / solution))
    monkeypatch.setenv("CLAUDECODE", "1")  # as if the bench ran inside a Claude Code session
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return record


async def test_the_claude_runner_runs_headless_in_the_copy(tmp_path, monkeypatch):
    record = _fake_env(monkeypatch, tmp_path, "solve")
    runner = ClaudeRunner(model="claude-sonnet-4-5", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["ok_run"] is True and rec["passed"] is True, (rec["error"], rec["grade_reason"])
    assert rec["tool_calls"] == 1  # one tool_use id, though its line came twice
    assert rec["model_rounds"] == 2
    assert rec["tokens_in"] == 120 + 30 + 400 and rec["tokens_cached"] == 400 and rec["tokens_out"] == 55
    assert rec["cost_list_price_usd"] == pytest.approx(0.0123) and rec["cost_metered_usd"] == 0.0
    assert rec["ledger_ok"] is True and rec["files_outside"] == []
    seen = json.loads(record.read_text(encoding="utf-8"))
    for flag in ("-p", "--safe-mode", "--verbose", "--no-session-persistence"):
        assert flag in seen["argv"]
    assert seen["argv"][seen["argv"].index("--output-format") + 1] == "stream-json"
    assert seen["argv"][seen["argv"].index("--permission-mode") + 1] == "bypassPermissions"
    assert seen["argv"][seen["argv"].index("--model") + 1] == "claude-sonnet-4-5"
    # v1.383.0 review: the agent works in a folder of its own OUTSIDE the
    # bench root; its work comes home to runs/<key>/work when the run ends.
    worked = Path(seen["cwd"]).resolve()
    assert worked.name == "work" and bench_mod.agent_runs_root().resolve() in worked.parents
    assert (tmp_path / "home").resolve() not in worked.parents
    assert (res.config_dir / "runs" / "bugfix-pagination" / "work" / "pagination.py").is_file()
    assert TASKS["bugfix-pagination"].prompt in seen["prompt"] and str(worked) in seen["prompt"]
    assert str(tmp_path / "home") not in seen["prompt"]
    # The parent Claude Code session's markers never reach the child.
    assert seen["env"]["CLAUDECODE"] is None and seen["env"]["CLAUDE_CODE_SESSION_ID"] is None
    assert res.config.harness["cli_version"].startswith("9.9.9")


async def test_a_stray_key_never_bills_or_reroutes_a_claude_run(tmp_path, monkeypatch):
    """Review finding 6: the claude child gets the app's own env guard
    (``claude_models.child_env``) — every ANTHROPIC_* variable, the Bedrock/
    Vertex/Foundry switches and CLAUDE_CODE_EXTRA_BODY removed — so a key or a
    base URL left in the shell can never bill or reroute a benchmark run, and
    its cost is always a list-price equivalent."""
    record = _fake_env(monkeypatch, tmp_path, "solve")
    for name, value in (("ANTHROPIC_API_KEY", "sk-test-not-real"), ("ANTHROPIC_AUTH_TOKEN", "tok"),
                        ("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/elsewhere"),
                        ("CLAUDE_CODE_USE_BEDROCK", "1"), ("CLAUDE_CODE_EXTRA_BODY", "{}")):
        monkeypatch.setenv(name, value)
    runner = ClaudeRunner(model="fake-model", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert all(value is None for value in seen["env"].values()), seen["env"]
    assert rec["cost_metered_usd"] == 0.0 and rec["cost_list_price_usd"] == pytest.approx(0.0123)
    assert res.config.harness["billing"] == "list_price"


@pytest.mark.parametrize("scenario", ["error", "dead"])
async def test_a_claude_run_that_did_not_finish_is_recorded_not_marked(tmp_path, monkeypatch, scenario):
    _fake_env(monkeypatch, tmp_path, scenario)
    runner = ClaudeRunner(model="fake-model", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["ok_run"] is False and rec["error"]
    assert rec["ledger_ok"] is True
    # Infrastructure: unscored, never a fail, and run again next time.
    assert rec["passed"] is None and rec["unscored_by"] == "infrastructure"
    if scenario == "dead":
        assert rec["tokens_in"] is None and rec["cost_list_price_usd"] is None
    assert not (res.config_dir / "runs" / "bugfix-pagination" / MARKER_NAME).exists()


async def test_a_hung_cli_is_stopped_at_the_time_limit(tmp_path, monkeypatch):
    _fake_env(monkeypatch, tmp_path, "hang")
    task = TASKS["bugfix-pagination"]
    quick = type(task)(**{**task.__dict__, "timeout_s": 3.0})
    runner = ClaudeRunner(model="fake-model", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    t0 = time.monotonic()
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=[quick], names=["bugfix-pagination"])
    assert time.monotonic() - t0 < 60
    rec = res.records[0]
    assert rec["ok_run"] is False and "time limit" in rec["error"]
    # The agent's own failure: graded (nothing was written, so it failed),
    # and a finished measurement that is reused, not run again.
    assert rec["failure_kind"] == "agent" and rec["passed"] is False
    assert (res.config_dir / "runs" / "bugfix-pagination" / MARKER_NAME).exists()


async def test_a_write_outside_the_copy_is_reported(tmp_path, monkeypatch):
    _fake_env(monkeypatch, tmp_path, "outside")
    runner = ClaudeRunner(model="fake-model", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    # (v1.383.0 review: the agent works in a folder of its own outside the
    # bench root, so "next to its folder" is that folder: ``../stray.txt``.)
    assert res.records[0]["files_outside"] == ["../stray.txt"]
    assert "outside its folder" in (res.config_dir / "report.md").read_text(encoding="utf-8")


async def test_the_codex_runner_runs_exec_in_the_copy(tmp_path, monkeypatch):
    record = _fake_env(monkeypatch, tmp_path, "solve")
    runner = CodexRunner(model="gpt-5", binary=_shim(tmp_path, "codex", FIXTURES / "fake_codex_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["ok_run"] is True and rec["passed"] is True, (rec["error"], rec["grade_reason"])
    assert rec["tool_calls"] == 2  # one command, one file change; reasoning is not a tool
    assert rec["model_rounds"] is None  # codex exec does not report model calls
    assert rec["tokens_in"] == 1000 and rec["tokens_cached"] == 200 and rec["tokens_out"] == 300
    assert rec["cost_metered_usd"] == 0.0 and rec["cost_list_price_usd"] > 0
    assert rec["ledger_ok"] is True
    seen = json.loads(record.read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert argv[0] == "exec" and argv[-1] == "-" and "--json" in argv and "--ephemeral" in argv
    # Round 3: Codex's own sandbox and approvals are OFF inside the disposable
    # copy (parity with Claude Code's bypassPermissions) — under
    # ``--sandbox workspace-write`` on Windows every command was refused.
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--sandbox" not in argv and "workspace-write" not in argv
    worked = Path(argv[argv.index("-C") + 1]).resolve()
    assert worked.name == "work" and bench_mod.agent_runs_root().resolve() in worked.parents
    assert (tmp_path / "home").resolve() not in worked.parents
    assert argv[argv.index("-m") + 1] == "gpt-5"
    assert seen["env"]["CLAUDECODE"] is None


async def test_a_codex_run_that_failed_is_recorded_with_its_words(tmp_path, monkeypatch):
    """Changed on purpose in review round 2 (finding 5): a usage-limit run is
    the ACCOUNT's failure, not the agent's — UNSCORED (out of the pass rate,
    "did not run" in the report) with Codex's own words kept."""
    _fake_env(monkeypatch, tmp_path, "fail")
    runner = CodexRunner(model="fake-model", binary=_shim(tmp_path, "codex", FIXTURES / "fake_codex_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["ok_run"] is False and "usage limit" in rec["error"]
    assert rec["passed"] is None and rec["unscored_by"] == "infrastructure"
    assert rec["failure_reason"] == "the account's usage limit was reached"
    assert rec["ledger_ok"] is True
    assert not (res.config_dir / "runs" / "bugfix-pagination" / MARKER_NAME).exists()
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "Pass rate n/a" in report and "1 did not run (infrastructure" in report
    assert "did not run: the account's usage limit was reached" in report


def test_the_parsers_read_the_real_shapes():
    claude = parse_claude_stream("\n".join([
        "not json",
        json.dumps({"type": "assistant", "message": {"id": "m", "content": [
            {"type": "text", "text": "x"}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}}),
        json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 9,
                    "result": "", "total_cost_usd": None, "usage": {"input_tokens": 3, "output_tokens": 4}}),
    ]))
    assert claude["tool_calls"] == 1 and claude["num_turns"] == 9 and claude["is_error"]
    assert claude["cost_list_price_usd"] is None and claude["cost_metered_usd"] is None
    codex = parse_codex_events("\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call"}}),
        json.dumps({"type": "item.completed", "item": {"type": "web_search"}}),
        json.dumps({"type": "item.completed", "item": {"type": "todo_list"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 9, "output_tokens": 1}}),
    ]), model="")
    assert codex["tool_calls"] == 2 and codex["turns"][0]["cost_list_price_usd"] is None


# --------------------------------------------------------------------------- #
# Review round 2: the guardrail, whose failure it was, and who pays for Codex
# --------------------------------------------------------------------------- #


def test_the_shared_prompt_carries_the_guardrail():
    from iron_jarvis.harness_bench.runners import GUARDRAIL, PROMPT_TEMPLATE

    assert GUARDRAIL in PROMPT_TEMPLATE
    for words in ("Work only inside this folder", "did not start yourself", "taskkill /im",
                  "pkill", "killall"):
        assert words in GUARDRAIL
    task = TASKS["bugfix-pagination"]
    assert GUARDRAIL in render_prompt(task, Path("C:/somewhere"))


async def test_every_runner_sends_the_guardrail(tmp_path, monkeypatch):
    """The prompt each runner actually SENDS carries it — Jarvis's model sees
    it in the user message, Claude Code and Codex read it on stdin."""
    from iron_jarvis.harness_bench.runners import GUARDRAIL

    res, scripted, _platform = await _smoke(tmp_path / "j", monkeypatch)
    assert scripted.first_user and all(GUARDRAIL in text for text in scripted.first_user)
    # The record proves it too: the task the model was sent hashes to the
    # run's own prompt.txt (the evidence a real run leaves behind).
    import hashlib

    for rec in res.records:
        prompt_file = res.config_dir / "runs" / rec["task_id"] / "prompt.txt"
        sent = hashlib.sha256(prompt_file.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        assert rec["extra"]["observed"]["first_user_sha256"] == sent
    for name, runner_cls in (("claude", ClaudeRunner), ("codex", CodexRunner)):
        record = _fake_env(monkeypatch, tmp_path / name, "solve")
        (tmp_path / name).mkdir(exist_ok=True)
        fake = FIXTURES / f"fake_{name}_bench_v1363.py"
        runner = runner_cls(model="fake-model", binary=_shim(tmp_path / name, name, fake))
        await run_bench(runner=runner, home=tmp_path / name / "home", tasks=list(TASKS.values()),
                        names=["bugfix-pagination"])
        assert GUARDRAIL in json.loads(record.read_text(encoding="utf-8"))["prompt"], name


# Each case, for each runner: the facts the runner reports -> whose failure.
# (runner, ok_run, facts, error text) -> (kind, words in the reason)
_CLASSIFY = [
    # infrastructure
    ("jarvis", False, {}, "Rate limited by the provider (429 Too Many Requests)", "infrastructure", "rate limit"),
    ("jarvis", False, {}, "the model server could not be reached (connection refused)", "infrastructure", "could not be reached"),
    ("jarvis", False, {"fix_kind": "sign_in"}, "Claude Code couldn't finish this answer", "infrastructure", "not signed in"),
    ("jarvis", False, {}, "Iron-Proxy: every account is parked", "infrastructure", "Iron-Proxy"),
    ("jarvis", False, {"finished": True, "demo_answered": True}, "the demo model answered", "infrastructure", "demo model"),
    ("jarvis", False, {"finished": True, "answered_by": "claude-cli instead of custom"}, "", "infrastructure", "another provider"),
    ("jarvis", False, {}, "the turn ended without a reply", "infrastructure", "before doing anything"),
    ("claude", False, {"cli_missing": True}, "Claude Code was not found on this PC", "infrastructure", "not installed"),
    ("claude", False, {"start_failed": True}, "Claude Code could not start ([WinError 5])", "infrastructure", "could not start"),
    ("claude", False, {"finished": False}, "Claude AI usage limit reached|1761062400", "infrastructure", "usage limit"),
    ("claude", False, {"finished": False}, "Invalid API key · Please run /login", "infrastructure", "not signed in"),
    ("claude", False, {"finished": False}, "API Error: 529 overloaded", "infrastructure", "overloaded"),
    ("claude", False, {"finished": False}, "Credit balance is too low", "infrastructure", "no credit"),
    ("claude", False, {}, "Claude Code ended without a result: exit code 3", "infrastructure", "before doing anything"),
    ("claude", False, {"finished": False, "acted": True}, "API Error: fetch failed", "infrastructure", "could not be reached"),
    # positive infrastructure words win even after the agent started work
    ("jarvis", False, {"acted": True}, "the model server could not be reached (connection refused)",
     "infrastructure", "could not be reached"),
    ("codex", False, {"cli_missing": True}, "Codex was not found on this PC", "infrastructure", "not installed"),
    ("codex", False, {"finished": False}, "You've hit your usage limit.", "infrastructure", "usage limit"),
    ("codex", False, {"finished": False}, "stream disconnected before completion: error sending request", "infrastructure", "could not be reached"),
    ("codex", False, {"finished": False}, "unexpected status 401 Unauthorized", "infrastructure", "not signed in"),
    # the agent's own
    ("jarvis", False, {"timed_out": True}, "stopped at the 900 s time limit", "agent", "time limit"),
    ("jarvis", True, {"finished": True, "cut_off": True}, "", "agent", "every round"),
    ("claude", False, {"timed_out": True}, "stopped at the 900 s time limit", "agent", "time limit"),
    ("claude", False, {"finished": False, "cut_off": True}, "error_max_turns", "agent", "every round"),
    ("codex", False, {"timed_out": True}, "stopped at the 900 s time limit", "agent", "time limit"),
    # Review round 2: a part-way stop with no infrastructure words is the
    # agent's (or its harness's) — the context it outgrew, a crash — GRADED.
    ("jarvis", False, {"acted": True}, "Error code: 400 - This model's maximum context length is 131072 tokens",
     "agent", "part-way"),
    ("jarvis", False, {"acted": True}, "TypeError: 'NoneType' object is not subscriptable", "agent", "part-way"),
    ("claude", False, {"finished": False, "acted": True}, "error_during_execution", "agent", "part-way"),
    ("codex", False, {"finished": False, "acted": True}, "Codex ran out of room in the model's context window",
     "agent", "part-way"),
    # "catalog in" / "design in" are not a sign-in (word-bounded)
    ("jarvis", False, {"acted": True}, "Open the catalog in Settings to pick another model", "agent", "part-way"),
    ("jarvis", False, {"acted": True}, "the tool design in this turn was rejected", "agent", "part-way"),
    # finished normally: the grader decides
    ("jarvis", True, {"finished": True}, "", "", ""),
    ("claude", True, {"finished": True}, "", "", ""),
    ("codex", True, {"finished": True}, "", "", ""),
    # round 3: Codex's own sandbox refused its commands — a setting, never the model
    ("codex", True, {"finished": True, "acted": True, "sandbox_refused": True}, "", "infrastructure", "sandbox refused"),
    ("codex", False, {"timed_out": True, "sandbox_refused": True}, "", "infrastructure", "sandbox refused"),
]


@pytest.mark.parametrize("runner,ok_run,facts,error,kind,words", _CLASSIFY)
def test_whose_failure_it_was(runner, ok_run, facts, error, kind, words):
    from iron_jarvis.harness_bench.failures import classify_failure

    got = classify_failure(runner, ok_run, facts, error)
    assert got.kind == kind, (got, error)
    assert words in got.reason, got.reason


def test_a_time_limit_hit_while_the_account_was_refused_is_still_the_agents():
    """Order matters: the runner's own facts decide before any words do."""
    from iron_jarvis.harness_bench.failures import classify_failure

    got = classify_failure("codex", False, {"timed_out": True}, "rate limit")
    assert got.kind == "agent"


@pytest.mark.parametrize("scenario,kind,words", [
    ("limit", "infrastructure", "usage limit"),
    ("auth", "infrastructure", "not signed in"),
    ("error", "infrastructure", "overloaded"),
    ("silent", "agent", "part-way"),
    ("dead", "infrastructure", "before doing anything"),
    ("max_turns", "agent", "every round"),
])
async def test_the_claude_runner_reports_the_facts_the_table_reads(tmp_path, monkeypatch, scenario, kind, words):
    _fake_env(monkeypatch, tmp_path, scenario)
    runner = ClaudeRunner(model="fake-model", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["failure_kind"] == kind and words in rec["failure_reason"], rec["failure_reason"]
    assert (rec["passed"] is None) == (kind == "infrastructure")


async def test_a_missing_cli_did_not_run(tmp_path, monkeypatch):
    for runner in (ClaudeRunner(binary=None), CodexRunner(binary=None)):
        runner.binary = None
        out = await runner.run(TASKS["bugfix-pagination"], tmp_path, BudgetLedger().begin("t", runner.name),
                               timeout_s=5)
        from iron_jarvis.harness_bench.failures import classify_failure

        got = classify_failure(runner.name, out.ok_run, out.facts, out.error)
        assert got.kind == "infrastructure" and "not installed" in got.reason


async def test_a_codex_sandbox_refusal_is_never_the_models_failure(tmp_path, monkeypatch):
    """Round 3, from the real baseline: under ``--sandbox workspace-write`` on
    Windows Codex's OWN commands were refused ("blocked by policy"), the model
    said it could not edit, and the row scored 0/3. Those ERROR lines now make
    the run infrastructure — unscored, run again — with the count kept."""
    _fake_env(monkeypatch, tmp_path, "sandbox")
    runner = CodexRunner(model="fake-model", binary=_shim(tmp_path, "codex", FIXTURES / "fake_codex_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["passed"] is None and rec["unscored_by"] == "infrastructure"
    assert rec["failure_reason"] == "Codex's sandbox refused its commands"
    assert rec["extra"]["refused_commands"] == 2
    assert rec["check_verdict"] is False  # what the check would have said: kept as evidence only
    assert not (res.config_dir / "runs" / "bugfix-leap-year" / MARKER_NAME).exists()


def test_only_codexs_own_error_lines_count_as_a_sandbox_refusal():
    from iron_jarvis.harness_bench.runners import codex_sandbox_refusals

    line = ('2026-10-21T13:02:11Z ERROR codex_core::tools::router: error=exec_command failed: '
            'CreateProcess { message: "Rejected(\\"`dir` rejected: blocked by policy\\")" }')
    assert codex_sandbox_refusals(line) == [line]
    # A task (or its prompt echo) that mentions a policy is not Codex's refusal.
    assert codex_sandbox_refusals("the change was blocked by policy review") == []
    assert codex_sandbox_refusals(line, prompt="look: " + line) == []
    assert codex_sandbox_refusals("ERROR codex_core: stream disconnected") == []


async def test_a_hung_codex_is_the_agents_time_limit(tmp_path, monkeypatch):
    _fake_env(monkeypatch, tmp_path, "hang")
    task = TASKS["bugfix-pagination"]
    quick = type(task)(**{**task.__dict__, "timeout_s": 3.0})
    runner = CodexRunner(model="fake-model", binary=_shim(tmp_path, "codex", FIXTURES / "fake_codex_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=[quick], names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["failure_kind"] == "agent" and rec["passed"] is False


async def test_the_jarvis_runner_reports_the_facts_the_table_reads(tmp_path, monkeypatch):
    """A lane error frame (the server could not be reached) and the demo model
    are infrastructure; a turn that used every round is the agent's."""
    platform = build_platform(str(tmp_path / "root"))

    async def unreachable(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        raise ConnectionError("the model server could not be reached (connection refused)")
        yield  # pragma: no cover

    monkeypatch.setattr(platform.router, "stream", unreachable)
    res = await run_bench(runner=JarvisRunner(platform, provider="scripted", model="s"),
                          home=platform.config.home, tasks=load_tasks(), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["unscored_by"] == "infrastructure" and "could not be reached" in rec["failure_reason"]

    async def demo(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        yield {"type": "final", "response": LLMResponse(text="Done.", usage={"input_tokens": 1, "output_tokens": 1}),
               "provider": "mock", "model": "mock", "reason": "mock"}

    platform2 = build_platform(str(tmp_path / "root2"))
    monkeypatch.setattr(platform2.router, "stream", demo)
    res = await run_bench(runner=JarvisRunner(platform2, provider="mock", model="mock"),
                          home=platform2.config.home, tasks=load_tasks(), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["unscored_by"] == "infrastructure" and "demo model" in rec["failure_reason"]


async def test_the_jarvis_turn_that_used_every_round_is_scored(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "root"))

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        resp = (LLMResponse(text="", tool_calls=[ToolCall(id=f"r{len(messages)}", name="list_files", arguments={})],
                            usage={"input_tokens": 1, "output_tokens": 1})
                if tools else LLMResponse(text="Not done.", usage={"input_tokens": 1, "output_tokens": 1}))
        yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}

    monkeypatch.setattr(platform.router, "stream", stream)
    res = await run_bench(runner=JarvisRunner(platform, provider="scripted", model="s", round_budget=3),
                          home=platform.config.home, tasks=load_tasks(), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["failure_kind"] == "agent" and rec["passed"] is False
    assert (res.config_dir / "runs" / "bugfix-leap-year" / MARKER_NAME).exists()


@pytest.mark.parametrize("login,api_key,billing", [
    ("chatgpt", "", "list_price"),
    ("api", "", "metered"),
    ("chatgpt", "sk-from-the-shell", "metered"),
])
async def test_who_pays_for_a_codex_run(tmp_path, monkeypatch, login, api_key, billing):
    """Review finding 6 (Codex half): an API-key login — said by the
    environment or by ``codex login status`` (no model call) — is METERED
    money; a ChatGPT login is a list-price equivalent. Only the word is kept:
    the status text (which can hold part of a key) is never stored."""
    _fake_env(monkeypatch, tmp_path, "solve")
    monkeypatch.setenv("FAKE_CODEX_LOGIN", login)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    if api_key:
        monkeypatch.setenv("OPENAI_API_KEY", api_key)
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CodexRunner(model="gpt-5", binary=_shim(tmp_path, "codex", FIXTURES / "fake_codex_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert res.config.harness["billing"] == billing
    if billing == "metered":
        assert rec["cost_metered_usd"] > 0 and rec["cost_list_price_usd"] == 0.0
    else:
        assert rec["cost_metered_usd"] == 0.0 and rec["cost_list_price_usd"] > 0
    stored = (res.config_dir / "runs" / "bugfix-pagination" / RESULT_NAME).read_text(encoding="utf-8")
    assert "sk-proj" not in stored and "sk-from-the-shell" not in stored
    assert "sk-proj" not in (res.config_dir / "config.json").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Review round 2 (reviewer): only POSITIVE evidence makes a run "did not run"
# --------------------------------------------------------------------------- #


async def test_a_jarvis_turn_that_dies_part_way_is_scored_not_dropped(tmp_path, monkeypatch):
    """The hole the "not finished ⇒ infrastructure" default left: a turn that
    did work and then hit an error with no infrastructure words (the context
    window it outgrew) was "did not run" — out of the pass rate, which only
    ever drops FAILURES that way. It is the agent's (its harness's) failure:
    graded, and a finished measurement."""
    platform = build_platform(str(tmp_path / "root"))
    calls: list[int] = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            resp = LLMResponse(text="", usage={"input_tokens": 3, "output_tokens": 1},
                               tool_calls=[ToolCall(id="l1", name="list_files", arguments={})])
            yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}
            return
        raise RuntimeError("Error code: 400 - This model's maximum context length is 131072 tokens")
        yield  # pragma: no cover

    monkeypatch.setattr(platform.router, "stream", stream)
    res = await run_bench(runner=JarvisRunner(platform, provider="scripted", model="s"),
                          home=platform.config.home, tasks=load_tasks(), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["facts"]["acted"] is True, rec["facts"]
    assert rec["failure_kind"] == "agent" and "part-way" in rec["failure_reason"], rec["failure_reason"]
    assert rec["passed"] is False and rec["unscored_by"] is None
    assert (res.config_dir / "runs" / "bugfix-leap-year" / MARKER_NAME).exists()
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "Pass rate 0%" in report and "1 stopped on their own" in report and "part-way" in report


async def test_a_jarvis_turn_stopped_at_its_time_limit_is_the_agents(tmp_path, monkeypatch):
    """End to end (the table alone could not see the runner forget to report
    the time limit): a turn still working at its deadline is graded."""
    platform = build_platform(str(tmp_path / "root"))

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        await asyncio.sleep(0.3)
        resp = LLMResponse(text="", usage={"input_tokens": 1, "output_tokens": 1},
                           tool_calls=[ToolCall(id=f"t{len(messages)}", name="list_files", arguments={})])
        yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}

    monkeypatch.setattr(platform.router, "stream", stream)
    task = TASKS["bugfix-leap-year"]
    quick = type(task)(**{**task.__dict__, "timeout_s": 1.5})
    res = await run_bench(runner=JarvisRunner(platform, provider="scripted", model="s"),
                          home=platform.config.home, tasks=[quick], names=["bugfix-leap-year"])
    rec = res.records[0]
    assert rec["facts"]["timed_out"] is True, rec["facts"]
    assert rec["failure_kind"] == "agent" and "time limit" in rec["failure_reason"]
    assert rec["passed"] is False and rec["unscored_by"] is None


async def test_a_claude_run_that_did_its_work_then_died_is_graded(tmp_path, monkeypatch):
    """``silent``: Claude Code wrote the fix, then died with no result line.
    It had its go — the folder is graded (here it passes) and marked."""
    _fake_env(monkeypatch, tmp_path, "silent")
    runner = ClaudeRunner(model="fake-model", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["failure_kind"] == "agent" and "part-way" in rec["failure_reason"]
    assert rec["passed"] is True and rec["ok_run"] is False
    assert (res.config_dir / "runs" / "bugfix-pagination" / MARKER_NAME).exists()


def test_the_parsers_keep_the_words_and_drop_what_was_not_an_ending():
    # Claude Code 2.1.306 writes an error_during_execution result with its
    # words in `errors` and NO `result` (read from its own result builder).
    claude = parse_claude_stream(json.dumps({
        "type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 0,
        "errors": ["API Error: 529 Overloaded"], "usage": {}}))
    assert claude["errors"] == ["API Error: 529 Overloaded"] and claude["reply"] == ""
    api_error_line = json.dumps({"type": "assistant", "isApiErrorMessage": True,
                                 "message": {"content": [{"type": "text", "text": "API Error"}]}})
    assert parse_claude_stream(api_error_line)["answered"] == 0
    # An error event a later completed turn outlived was not the run's end.
    codex = parse_codex_events("\n".join([
        json.dumps({"type": "error", "message": "Reconnecting... 1/5"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ]))
    assert codex["error"] == "" and codex["items"] == 1


async def test_a_codex_prompt_echoed_into_stderr_is_never_read_as_its_failure(tmp_path, monkeypatch):
    """``codex exec`` echoes the prompt into stderr (the v1.301.0 lesson). A
    task whose words say "credit balance" must not turn a part-way stop into
    "the account has no credit left" — only Codex's own ERROR lines count."""
    _fake_env(monkeypatch, tmp_path, "echo")
    base = TASKS["bugfix-pagination"]
    copy = tmp_path / "tasks" / base.id
    shutil.copytree(base.path, copy)
    (copy / "task.md").write_text(base.prompt + "\nThe customer's credit balance is not your concern.\n",
                                  encoding="utf-8")
    task = load_task(copy)
    runner = CodexRunner(model="fake-model", binary=_shim(tmp_path, "codex", FIXTURES / "fake_codex_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=[task], names=[base.id])
    rec = res.records[0]
    assert "credit" not in rec["failure_reason"], rec["failure_reason"]
    assert "credit balance" not in rec["error"], rec["error"]
    # Not a line of the prompt at all (its last words sit in any stderr tail).
    assert "Make the changes directly in the files there" not in rec["error"], rec["error"]
    assert rec["failure_kind"] == "agent" and "part-way" in rec["failure_reason"]


async def test_a_claude_answer_is_never_read_as_its_failure(tmp_path, monkeypatch):
    """A non-error result followed by a non-zero exit: the result text is the
    agent's ANSWER ("Fixed the retry on a 503.") — reading it for words made
    the run "the model service was overloaded" and dropped it from the rate."""
    _fake_env(monkeypatch, tmp_path, "exit1")
    runner = ClaudeRunner(model="fake-model", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert "503" not in rec["error"], rec["error"]
    assert rec["failure_kind"] == "agent" and rec["passed"] is True, (rec["failure_reason"], rec["passed"])
