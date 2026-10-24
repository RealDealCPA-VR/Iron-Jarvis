"""v1.383.0 — the benchmark's ``frontier`` tier and task SETUP steps (IJ harness
part 3, Phase 1).

Pinned here, each through the real code:

* THE CORE AND HARD TASKS DID NOT CHANGE: their content hashes are the ones
  v1.382.1 shipped, so every earlier record stays comparable.
* THE FRONTIER TIER: about twelve tasks, each with a hidden check, a reference
  solution and a DECOY (``tests/fixtures/harness_bench_decoys/<id>/``) that
  passes the tests the agent can see and fails the hidden check. (That the
  untouched repo fails and the solution passes is pinned for every task by
  ``test_every_task_is_gradeable_both_ways`` in the v1363 file, on the SET UP
  copy.)
* A SETUP STEP makes its tree deterministically (same seed, same tree), with
  the task's own seed, offline, before the tripwire looks; it is hidden like
  the check; a setup that fails is "did not run", never a graded fail.
* THE BROWSER CHECK is unscored — never a pass or a fail — when no browser can
  start.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness_bench.bench import MARKER_NAME, RESULT_NAME, run_bench
from iron_jarvis.harness_bench.catalog import HIDDEN_CANARY, TaskError, load_task, tree_hash
from iron_jarvis.harness_bench.failures import classify_failure
from iron_jarvis.harness_bench.grading import grade
from iron_jarvis.harness_bench.runners import render_prompt
from iron_jarvis.harness_bench.setup_step import SetupFailed, prepare_workspace, run_setup, setup_env
from iron_jarvis.harness_bench.tripwire import HiddenCheckLeak, assert_context_clean
from tests.test_harness_bench_v1363 import DECOYS, SOLUTIONS, TASKS, _StubRunner, apply_solution

FRONTIER = sorted(t.id for t in TASKS.values() if t.tier == "frontier")
SETUP_TASKS = sorted(t.id for t in TASKS.values() if t.setup_script)

#: The content hash of every core and hard task, as v1.382.1 shipped them.
#: Phase 1 adds a tier; it never edits a task an earlier record measured.
CORE_AND_HARD_HASHES = {
    "bugfix-csv-quoting": "1bb56c2e85eb578b5cdbdec276437e84d0ef3de90c218195e49dee054be6bdf0",
    "bugfix-leap-year": "4f03b385929fd657e0d3b0b0df4b52cf9b864ce79c16dae3c609fa70f8214d1d",
    "bugfix-pagination": "054198d26f644bd182c1b549f048223ebce29c864d200163af47526bff31f067",
    "bugfix-shared-cart": "32612852ec4429d66a017b2d74e7cd77355ebe62a75b4e94441594dfae0d650b",
    "explain-export-command": "70892955e62b69e7e80d6b812e3aab22076d9ddbd7705f3ba0f6ff473a83d03b",
    "explain-mystery-functions": "74eaa12bf027c73647f06dcfe6f73db9f34397d5c38b0b762052f0bcf165cb61",
    "explain-retry-default": "d59e105dfccadf760ec9cae42bf774d05535da72e74614a2483a28238d62fb04",
    "feature-lru-cache": "443afceb8e88ebee71e49c6f415fb78968cfa48d07108f6d2a8e7e77393c0def",
    "feature-settings-file": "120848e8beb1e3e0309aaa80a753053ed0b3b5592310f08499838bf60ef693a2",
    "feature-slugify": "470402592d9c0abde7ab63e70d8403df1af3a6deb0448f76a39f742dce4dfb5b",
    "feature-word-count": "6f91ea5e4e773ff9d601c66866e3b00b5c18e767c1d37e6c90191fcf285a6d49",
    "hard-cross-module-bug": "bcaef7af070a5c7de93213149981d9edfacdb6c70f7dd983dc44ce8340c0e5dc",
    "hard-explain-trace": "69be49e83797962bf7f5d22957ee95d5cce8baaa520bd00a986eab935ba66a1a",
    "hard-expression-evaluator": "8de809d54799f969ea37d578fa1f40a237b25aed62749571337eac00f8a8c517",
    "hard-file-watcher-process": "dad118ac555b970a17c6f4e79a6419621e1a08ea0e5a4533630f1a5f924dfa5c",
    "hard-idempotent-migration": "ac05d0f0270f70483fe5ace9d90ca4a90cc9382a16b2e726dd09b22bc076b525",
    "hard-markdown-exporter": "506288d2fbca1ab11f1aa95e63aeb05add268e0b88e214c730675b02a615c34f",
    "hard-performance": "f2a626f1eb2c8297ad00e4192b01c377ed53be1de08052e50e7e96362b72fa60",
    "hard-refactor-module": "4d9bc59dba93db2968977d746c5c5b88dd49492ea34ed6a28d3dc3bc114f4a86",
    "hard-regression-trap": "bbead06120162a6bfacd497ab9f4b2565f72d39b6df7c63341bfd3096fb61fb9",
    "hard-scheduler-ordering": "32464065cc79a8aa40d4a76209a5fe4ef90e5dad64acaa1aec5e041a3f3d5af7",
    "process-http-server": "d3a98dda1a0902232b6ed882cb6fec3d4af57ba6cd8af7255787ce91f23837b9",
    "refactor-rename-function": "9e110eb0fb4c576e00e1c454c6923e426474462e95775aab157c681577d1dd40",
    "refactor-split-module": "9d9b8b44d9a9d4d5326c23383d17618a983d31eba11d1177573afde838b9b8e6",
    "refactor-tax-constant": "f0b6e3bfb76f594a4ba92447b5646c850edec25962495fad9a60893bb554a750",
    "shell-count-failures": "1256c873476682bc1cf43ac5fb41675698ebee0b6cfe65af4f3675711be0f0be",
    "shell-manifest": "77d4000577bf9555fd92967931170a162e5d76ec47f68cd0cf731213a5a683d1",
    "trap-ambiguous-edit": "3a6b163e712f43966f7c310b7af10c3ce1e7673ceeba43efe18ac17f16850378",
    "trap-keep-the-tests": "7564a3c75b6cc598081b05366f00aea7ed3e27261bb8da7be119b67365ef59e4",
    "trap-run-the-tests": "e4e77b4ce5dcfc24a73275517dd0e9dbc3d3c08529f025663200b489d3391ebc",
}


def no_browser(g) -> bool:
    """The browser check said it could not start a browser (exit 3)."""
    return g.passed is None and "exit code 3" in g.reason


def _visible_tests_pass(work: Path) -> None:
    seen = procs.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
                     cwd=work, timeout=300)
    assert seen.returncode == 0, procs.text(seen.stderr, "utf-8")[-2000:]


# --------------------------------------------------------------------------- #
# The tiers
# --------------------------------------------------------------------------- #


def test_the_core_and_hard_tasks_did_not_change():
    got = {t.id: t.content_hash for t in TASKS.values() if t.tier in ("core", "hard")}
    assert got == CORE_AND_HARD_HASHES


def test_the_frontier_tier_holds_about_twelve_big_tasks():
    assert 10 <= len(FRONTIER) <= 14, FRONTIER
    for task_id in FRONTIER:
        task = TASKS[task_id]
        assert 'tier = "frontier"' in (task.path / "task.toml").read_text(encoding="utf-8"), task_id
        assert task.timeout_s >= 1200 and 0 < task.check_timeout_s <= 300, task_id
        assert (SOLUTIONS / task_id).is_dir() and (DECOYS / task_id).is_dir(), task_id
        committed = sum(1 for p in task.repo_dir.rglob("*") if p.is_file())
        assert committed <= 200, (task_id, committed)
        for hidden in task.hidden_files():
            if hidden.name != "task.toml":
                assert HIDDEN_CANARY in hidden.read_text(encoding="utf-8", errors="replace"), hidden
    # The kinds of work the plan names, each at least once.
    kinds = {
        "a search across a generated repository of ~2,000 files": "frontier-legacy-callers",
        "a merge in progress, without losing either side": "frontier-merge-in-progress",
        "a page checked in a browser": "frontier-inventory-filter",
        "a project that will not build": "frontier-broken-build",
        "a program debugged while it runs": "frontier-debug-price-server",
        "long work across several modules with its own tests": "frontier-multicurrency-ledger",
        "an obvious fix that breaks something only a careful reading shows": "frontier-retry-idempotency",
    }
    for words, task_id in kinds.items():
        assert task_id in FRONTIER, words
    assert {TASKS[i].category for i in FRONTIER} >= {"feature", "refactor", "bugfix", "explain", "shell",
                                                     "trap", "process"}
    assert SETUP_TASKS == ["frontier-legacy-callers", "frontier-merge-in-progress"]


@pytest.mark.parametrize("task_id", FRONTIER)
def test_a_frontier_decoy_passes_what_the_agent_sees_and_fails_the_hidden_check(task_id, tmp_path):
    """What makes a frontier task worth its place: the near-miss a careless
    agent would hand in passes every test in its own copy, and only the hidden
    check refuses it."""
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "decoy")
    apply_solution(task_id, work, root=DECOYS)
    if any(work.rglob("test_*.py")):
        _visible_tests_pass(work)
    g = grade(task, work, tmp_path / "grade")
    if no_browser(g):
        pytest.skip("no browser could start on this machine (the check is unscored there)")
    assert g.passed is False, (g.reason, g.output[-2000:])


# --------------------------------------------------------------------------- #
# Setup steps
# --------------------------------------------------------------------------- #


def _git(work: Path, *args: str) -> str:
    env = {k: v for k, v in setup_env().items()}
    return subprocess.run(["git", *args], cwd=work, env=env, capture_output=True, text=True).stdout.strip()


def _files_outside_git(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def test_the_generated_repository_is_the_same_tree_for_the_same_seed(tmp_path):
    task = TASKS["frontier-legacy-callers"]
    a = prepare_workspace(task, tmp_path / "a")
    b = prepare_workspace(task, tmp_path / "b")
    files = sum(1 for p in a.rglob("*") if p.is_file())
    assert 1800 <= files <= 2200, files
    assert tree_hash(a) == tree_hash(b)
    assert all(b"\r\n" not in p.read_bytes() for p in list(a.rglob("*.py"))[:200])
    # The seed is really the tree's: another seed is another tree.
    other = prepare_workspace(replace(task, setup_seed=task.setup_seed + 1), tmp_path / "c")
    assert tree_hash(other) != tree_hash(a)
    assert_context_clean(task, a, render_prompt(task, a))


@pytest.mark.skipif(shutil.which("git") is None, reason="the merge task's setup needs git")
def test_the_merge_task_starts_mid_merge_with_the_same_history_every_time(tmp_path):
    task = TASKS["frontier-merge-in-progress"]
    a = prepare_workspace(task, tmp_path / "a")
    b = prepare_workspace(task, tmp_path / "b")
    assert _files_outside_git(a) == _files_outside_git(b)
    heads = (_git(a, "rev-parse", "HEAD"), _git(a, "rev-parse", "MERGE_HEAD"))
    assert heads == (_git(b, "rev-parse", "HEAD"), _git(b, "rev-parse", "MERGE_HEAD"))
    # The very commits the hidden check expects as the merge's parents.
    check = (task.check_dir / "test_hidden_merge.py").read_text(encoding="utf-8")
    assert heads == (re.search(r'MAIN_TIP = "(\w+)"', check).group(1),
                     re.search(r'FEATURE_TIP = "(\w+)"', check).group(1))
    unmerged = sorted(set(_git(a, "diff", "--name-only", "--diff-filter=U").split()))
    assert unmerged == ["CHANGELOG.md", "README.md", "pricing.py", "test_pricing.py"]
    assert "<<<<<<< HEAD" in (a / "pricing.py").read_text(encoding="utf-8")
    # A user's global git settings (an autocrlf, a signing key, hooks) never
    # reach the repository the agent gets: the step runs with them switched off
    # and pins its own.
    assert _git(a, "config", "--local", "commit.gpgsign") == "false"
    assert _git(a, "config", "--local", "core.autocrlf") == "false"
    assert_context_clean(task, a, render_prompt(task, a))


def _setup_task(root: Path, script: str, *, seed: int = 7, timeout: float = 60, toml_extra: str = "") -> object:
    task_dir = root / "setup-task"
    (task_dir / "repo").mkdir(parents=True)
    (task_dir / "check").mkdir()
    (task_dir / "setup").mkdir()
    (task_dir / "repo" / "m.py").write_text("X = 1\n", encoding="utf-8")
    (task_dir / "task.md").write_text("Leave it.", encoding="utf-8")
    (task_dir / "check" / "test_hidden_setup.py").write_text(
        f"# {HIDDEN_CANARY}\nimport unittest, m\n"
        "class T(unittest.TestCase):\n def test(self): self.assertEqual(m.X, 1)\n", encoding="utf-8")
    (task_dir / "setup" / "make_it.py").write_text(f"# {HIDDEN_CANARY}\n" + script, encoding="utf-8")
    (task_dir / "task.toml").write_text(
        'category = "bugfix"\n'
        'check = ["{python}", "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_hidden_*.py"]\n'
        f'{toml_extra}\n[setup]\nscript = "make_it.py"\nseed = {seed}\ntimeout_s = {timeout}\n',
        encoding="utf-8")
    return load_task(task_dir)


async def test_the_setup_step_gets_the_tasks_seed_never_the_benchs(tmp_path):
    task = _setup_task(tmp_path, "import sys, pathlib\npathlib.Path(sys.argv[1], 'seed.txt').write_text(sys.argv[2])\n")
    assert (task.setup_script, task.setup_seed) == ("make_it.py", 7)
    stub = _StubRunner(solve=False)
    res = await run_bench(runner=stub, home=tmp_path / "home", tasks=[task], seed=99)
    work = res.config_dir / "runs" / "setup-task" / "work"
    assert (work / "seed.txt").read_text() == "7"
    assert stub.calls == ["setup-task"] and res.records[0]["passed"] is True


async def test_a_failing_setup_is_did_not_run_and_the_runner_never_starts(tmp_path):
    task = _setup_task(tmp_path, "import sys\nprint('the archive is corrupt', file=sys.stderr)\nsys.exit(3)\n")
    stub = _StubRunner()
    res = await run_bench(runner=stub, home=tmp_path / "home", tasks=[task])
    rec = res.records[0]
    assert stub.calls == []
    assert rec["passed"] is None and rec["unscored_by"] == "infrastructure"
    assert rec["failure_reason"].startswith("the task's setup step failed (exit code 3)")
    assert "the archive is corrupt" in rec["failure_reason"]
    assert rec.get("ledger_ok") is not False and rec["tokens_in"] == 0 and rec["model_calls"] == 0
    assert not (res.config_dir / "runs" / "setup-task" / MARKER_NAME).exists()
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "1 did not run (infrastructure" in report and "Pass rate n/a" in report
    # Run again next time (no marker): the step is tried again.
    await run_bench(runner=stub, home=tmp_path / "home", tasks=[task])
    assert stub.calls == []
    assert json.loads((res.config_dir / "runs" / "setup-task" / RESULT_NAME).read_text())["passed"] is None


def test_a_setup_that_runs_past_its_limit_is_stopped(tmp_path):
    task = _setup_task(tmp_path, "import time\ntime.sleep(60)\n", timeout=1)
    work = tmp_path / "w"
    shutil.copytree(task.repo_dir, work)
    t0 = time.monotonic()
    with pytest.raises(SetupFailed, match="ran past its 1 s limit"):
        run_setup(task, work)
    assert time.monotonic() - t0 < 30


def test_setup_failed_is_infrastructure_in_the_one_classifier():
    got = classify_failure("claude", False, {"setup_failed": "the task's setup step failed (exit code 2)"}, "")
    assert got.kind == "infrastructure" and "setup step failed" in got.reason


async def test_a_setup_that_leaks_itself_into_the_copy_is_refused_by_the_tripwire(tmp_path):
    task = _setup_task(tmp_path, "import shutil, sys\nshutil.copy(__file__, sys.argv[1] + '/helper.py')\n")
    assert task.setup_dir / "make_it.py" in task.hidden_files()
    stub = _StubRunner()
    res = await run_bench(runner=stub, home=tmp_path / "home", tasks=[task])
    rec = res.records[0]
    assert stub.calls == [] and rec["passed"] is None
    assert "refused to start setup-task" in rec["error"]
    work = res.config_dir / "runs" / "setup-task" / "work"
    with pytest.raises(HiddenCheckLeak):
        assert_context_clean(task, work, render_prompt(task, work))


def test_a_malformed_setup_is_refused(tmp_path):
    with pytest.raises(TaskError, match="needs a \\[setup\\] table"):
        task = _setup_task(tmp_path / "a", "pass\n")
        toml = task.path / "task.toml"
        toml.write_text(toml.read_text(encoding="utf-8").split("[setup]")[0], encoding="utf-8")
        load_task(task.path)
    for n, (bad, words) in enumerate((('script = "../repo/m.py"', "must name a file"),
                                      ('script = "missing.py"', "must name a file"),
                                      ('script = "make_it.py"\nseed = "x"', "must be numbers"))):
        task_dir = tmp_path / f"b{n}"
        task = _setup_task(task_dir, "pass\n")
        toml = task.path / "task.toml"
        toml.write_text(toml.read_text(encoding="utf-8").split("[setup]")[0] + f"[setup]\n{bad}\n", encoding="utf-8")
        with pytest.raises(TaskError, match=words):
            load_task(task.path)
    task = _setup_task(tmp_path / "c", "pass\n")
    (task.setup_dir / "make_it.py").write_text("print('no marker')\n", encoding="utf-8")
    with pytest.raises(TaskError, match=HIDDEN_CANARY):
        load_task(task.path)


def test_the_setup_environment_switches_off_the_users_python_and_git_settings(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "C:/elsewhere")
    monkeypatch.setenv("GIT_DIR", "C:/another/.git")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    env = setup_env()
    assert not any(k.upper().startswith("PYTHONPATH") for k in env)
    assert "GIT_DIR" not in env and "GIT_CONFIG_COUNT" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_TERMINAL_PROMPT"] == "0"


# --------------------------------------------------------------------------- #
# The browser check
# --------------------------------------------------------------------------- #


def test_the_page_check_is_unscored_when_no_browser_can_start(tmp_path, monkeypatch):
    """No Microsoft Edge on the machine: the check exits 3 — unscored, never a
    pass (it would reward a page nobody saw) and never a fail."""
    task = TASKS["frontier-inventory-filter"]
    work = prepare_workspace(task, tmp_path / "w")
    apply_solution(task.id, work)
    nowhere = str(tmp_path / "nowhere")
    for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(name, nowhere)
    monkeypatch.setenv("PATH", os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep)
                                               if "edge" not in p.lower()))
    g = grade(task, work, tmp_path / "g")
    assert g.passed is None and "exit code 3" in g.reason, (g.reason, g.output[-500:])
    assert "no Microsoft Edge was found" in g.output


def test_the_legacy_callers_answer_key_is_what_a_real_resolver_finds(tmp_path):
    """The hidden list was recorded by the generator as it wrote each call; the
    reference solution resolves the calls from the code's imports alone. They
    agree exactly (62 calls), and the name-only decoy finds others."""
    task = TASKS["frontier-legacy-callers"]
    check = (task.check_dir / "test_hidden_callers.py").read_text(encoding="utf-8")
    expected = re.findall(r'^    "(src/[^"]+:\d+)",$', check, re.M)
    work = prepare_workspace(task, tmp_path / "w")
    apply_solution(task.id, work)
    answer = json.loads((work / "answer.json").read_text(encoding="utf-8"))
    assert answer["calls"] == expected and answer["count"] == len(expected) == 62
    decoy = prepare_workspace(task, tmp_path / "d")
    apply_solution(task.id, decoy, root=DECOYS)
    got = json.loads((decoy / "answer.json").read_text(encoding="utf-8"))["calls"]
    assert set(got) - set(expected) and set(expected) - set(got)
