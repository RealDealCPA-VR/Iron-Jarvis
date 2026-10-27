"""v1.400.0 — the benchmark's ``team`` tier: tasks where SPLITTING PAYS.

The first live team benchmark (2026-10-26, the twelve frontier tasks, three
repeats) handed ZERO pieces to a member: each frontier task took about a
minute, and doing the work was always quicker than writing a helper a brief.
The team tier holds tasks made of several independent pieces a planner can
hand out from a written brief: a sixty-module package to read, three modules
from one specification, fifteen failing tests from four unrelated bugs, an
eight-file change to apply and review, a rename across code, command line,
environment and docs, five functions to port from a JavaScript reference.

Pinned here, each through the real code (``load_tasks``, ``prepare_workspace``,
``grade``):

* every untouched repo FAILS its hidden check, every reference solution
  PASSES, every decoy passes the tests the agent can see and FAILS, every
  correct alternative PASSES and every extra decoy FAILS;
* where a task's pieces live in separate files, the solution with any ONE
  piece swapped for the decoy's FAILS — each piece is graded on its own;
* the checks never reach the network and start no process;
* the team tier's content hashes are pinned, and the core, hard and frontier
  tiers' hashes and task lists are unchanged;
* ``--tier team`` selects exactly the team tasks, ``--tier all`` includes them;
* the answer keys are honest: the audit list is what an ``ast`` resolver finds,
  the patch applies cleanly and its own tests pass WITH its bug, and (where
  ``node`` is installed) the port's expected values are what the JavaScript
  reference really returns.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness_bench.catalog import HIDDEN_CANARY, TIERS, select_tasks
from iron_jarvis.harness_bench.grading import grade, prepare_grading_copy
from iron_jarvis.harness_bench.setup_step import prepare_workspace
from tests.test_harness_bench_frontier_v1383 import CORE_AND_HARD_HASHES
from tests.test_harness_bench_v1363 import DECOYS, FIXTURES, SOLUTIONS, TASKS, _shim, apply_solution

TEAM = sorted(t.id for t in TASKS.values() if t.tier == "team")
ALTERNATIVES = FIXTURES / "harness_bench_alternatives"
MORE_DECOYS = FIXTURES / "harness_bench_decoys_more"

#: The content hash of every team task as v1.400.0 ships them.
TEAM_HASHES = {
    "team-audit-events": "bf925deebf9666c400b561c7a98d80731eb9516a7ca71f40d0d1a0f044cd3c5c",
    "team-port-js": "311ff0d540c7d98ce27bae1f95b1835108dbf7b0b1440978f435224332dd4d6e",
    "team-rename-option": "9bffa25f46e8e31dc64c8a9a79c59a7c31299608e90607fd7b5031f5c4c6cdce",
    "team-review-patch": "567ecd37d75b7e5739484b91e7f031bd594c8326659bde61ad7bdec6dc9c16cf",
    "team-spec-modules": "503797e1eb625facf4c3c6d47c3ec3bd1c1df0668b01372deac20a83f4779d30",
    "team-test-triage": "ba36744c65721af0550d4fde3441546b1031fc1b2aa12bf91ad9f0dfa81f25b8",
}
#: The frontier tier as v1.383.0 shipped it (v1.400.0 adds a tier; it edits none).
FRONTIER_HASHES = {
    "frontier-broken-build": "a3aaed1d3596115952589f699fa78db4d0200e71747c5e26261473dc123e052d",
    "frontier-config-injection": "50dcb282320f0bed31fc6e133a66e543519a22f8515a6d0bd2cfb0b3c58936cb",
    "frontier-csv-encodings": "ca1baa1b61a674c2ade69e284dd1c77104c0dbc1811c2b055c8a18d8bac46b72",
    "frontier-debug-price-server": "213568ed72925b5e21689af2d46cdf80d15e1974950b1e9ae039dc69b9e86643",
    "frontier-flaky-test-order": "3292db8708abae22da2249700585c75cca07b0b5f45362d8f560585a74c5c440",
    "frontier-inventory-filter": "8016e5b62ea7975261b294b59c7422ba4013e65272fff9dc870e10bbd27e1d91",
    "frontier-legacy-callers": "aaab71427052a8172494146a326422c89e66bb7285d8acaaae6f31923652dca0",
    "frontier-merge-in-progress": "8889dc224c9aacfb9d108cc4efb5bb268898b827e7ddc3ba8b37835626dae156",
    "frontier-monthly-recurrence": "69c61e8de95cb741828063cce5768f8b775b16cf05d8ce7872bc7011c6a3e200",
    "frontier-multicurrency-ledger": "c781426f1551756edc34aef3dfbbecbc768e0b01e1192e4aa3a1ac8b51a53b23",
    "frontier-release-ready": "115f05b455d3f7055a483b30829a9c242a16ec3b985fce576fbd555b1affb39c",
    "frontier-retry-idempotency": "1c28c576f6066490b7f1d38b7540c24b4bb9a0309176ee3a0877c0578b281189",
}
#: The tasks of each earlier tier, by id: a fourth tier changes none of them.
EARLIER_TIERS = {
    "core": [
        "bugfix-csv-quoting", "bugfix-leap-year", "bugfix-pagination", "bugfix-shared-cart",
        "explain-export-command", "explain-mystery-functions", "explain-retry-default", "feature-lru-cache",
        "feature-settings-file", "feature-slugify", "feature-word-count", "process-http-server",
        "refactor-rename-function", "refactor-split-module", "refactor-tax-constant", "shell-count-failures",
        "shell-manifest", "trap-ambiguous-edit", "trap-keep-the-tests", "trap-run-the-tests",
    ],
    "hard": [
        "hard-cross-module-bug", "hard-explain-trace", "hard-expression-evaluator", "hard-file-watcher-process",
        "hard-idempotent-migration", "hard-markdown-exporter", "hard-performance", "hard-refactor-module",
        "hard-regression-trap", "hard-scheduler-ordering",
    ],
    "frontier": sorted(FRONTIER_HASHES),
}
#: Tasks whose pieces are separate files: swapping any ONE of the solution's
#: files for the decoy's must fail the hidden check.
PIECEWISE = ["team-port-js", "team-spec-modules", "team-test-triage"]


def _cases(root: Path) -> list[tuple[str, str]]:
    return sorted(
        (task_id, case.name)
        for task_id in TEAM if (root / task_id).is_dir()
        for case in (root / task_id).iterdir() if case.is_dir()
    )


ALT_CASES = _cases(ALTERNATIVES)
MORE_CASES = _cases(MORE_DECOYS)


def _visible_tests_pass(work: Path) -> None:
    seen = procs.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
                     cwd=work, timeout=300)
    assert seen.returncode == 0, procs.text(seen.stderr, "utf-8")[-2000:]


# --------------------------------------------------------------------------- #
# The tier
# --------------------------------------------------------------------------- #


def test_the_team_tier_holds_six_tasks_of_independent_pieces():
    assert "team" in TIERS and TIERS[:3] == ("core", "hard", "frontier")
    assert TEAM == sorted(TEAM_HASHES), TEAM
    kinds = {
        "read across a sixty-module package": ("team-audit-events", "explain"),
        "three modules from one specification": ("team-spec-modules", "feature"),
        "fifteen failing tests from unrelated bugs": ("team-test-triage", "bugfix"),
        "a change reviewed and fixed": ("team-review-patch", "trap"),
        "a rename across code and docs": ("team-rename-option", "refactor"),
        "functions ported from another language": ("team-port-js", "feature"),
    }
    for words, (task_id, category) in kinds.items():
        assert TASKS[task_id].category == category, words
    for task_id in TEAM:
        task = TASKS[task_id]
        assert 'tier = "team"' in (task.path / "task.toml").read_text(encoding="utf-8"), task_id
        assert task.timeout_s >= 1200 and 0 < task.check_timeout_s <= 120, task_id
        assert not task.setup_script, task_id
        assert (SOLUTIONS / task_id).is_dir() and (DECOYS / task_id).is_dir(), task_id
        assert any(t == task_id for t, _ in ALT_CASES), f"no correct alternative for {task_id}"
        committed = [p for p in task.repo_dir.rglob("*") if p.is_file()]
        assert len(committed) <= 200, (task_id, len(committed))
        for hidden in task.hidden_files():
            if hidden.name != "task.toml":
                assert HIDDEN_CANARY in hidden.read_text(encoding="utf-8"), hidden
        # Honest: the prompt says that more will be checked than the visible tests show.
        assert "checked" in task.prompt, task_id


def test_the_team_tasks_did_not_change():
    assert {t: TASKS[t].content_hash for t in TEAM} == TEAM_HASHES


def test_the_earlier_tiers_did_not_change():
    for tier, ids in EARLIER_TIERS.items():
        assert sorted(t.id for t in TASKS.values() if t.tier == tier) == ids, tier
    assert {t.id: t.content_hash for t in TASKS.values() if t.tier in ("core", "hard")} == CORE_AND_HARD_HASHES
    assert {t.id: t.content_hash for t in TASKS.values() if t.tier == "frontier"} == FRONTIER_HASHES


def test_the_tier_selects_the_team_tasks():
    tasks = list(TASKS.values())
    team = [t.id for t in select_tasks(tasks, tier="team", seed=3)]
    every = [t.id for t in select_tasks(tasks, tier="all", seed=3)]
    assert sorted(team) == TEAM
    assert team == [i for i in every if i in set(TEAM)]
    assert sorted(every) == sorted(TASKS)
    for tier in ("core", "hard", "frontier"):
        assert not set(team) & {t.id for t in select_tasks(tasks, tier=tier)}, tier


def test_the_command_line_takes_tier_team(tmp_path):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    root = tmp_path / "bench-root"
    out = CliRunner().invoke(bench_app, ["run", "--runner", "claude", "--binary", str(claude), "--root", str(root),
                                         "--dry-run", "--tier", "team"])
    assert out.exit_code == 0, out.output
    lines = [line for line in out.output.splitlines() if "would run" in line]
    assert sorted(line.split(" [")[0].strip() for line in lines) == TEAM
    assert all("[team, " in line for line in lines)


# --------------------------------------------------------------------------- #
# Graded both ways, and each piece on its own
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("task_id", TEAM)
def test_the_untouched_repo_fails_and_the_solution_passes(task_id, tmp_path):
    task = TASKS[task_id]
    g = grade(task, prepare_workspace(task, tmp_path / "untouched"), tmp_path / "g1")
    assert g.passed is False, (g.reason, g.output[-1500:])
    solved = prepare_workspace(task, tmp_path / "solved")
    apply_solution(task_id, solved)
    started = time.monotonic()
    g = grade(task, solved, tmp_path / "g2")
    assert g.passed is True, (g.reason, g.output[-1500:])
    # A hang detector, not a speed bar: each check takes about a second.
    assert time.monotonic() - started < task.check_timeout_s


@pytest.mark.parametrize("task_id", TEAM)
def test_the_decoy_passes_what_the_agent_sees_and_fails_the_hidden_check(task_id, tmp_path):
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "decoy")
    apply_solution(task_id, work, root=DECOYS)
    if any(work.glob("test_*.py")):
        _visible_tests_pass(work)
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is False, (g.reason, g.output[-2000:])


@pytest.mark.parametrize("task_id,name", ALT_CASES)
def test_a_correct_solution_written_another_way_passes(task_id, name, tmp_path):
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "work")
    apply_solution(name, work, root=ALTERNATIVES / task_id)
    assert (ALTERNATIVES / task_id / name / "NOTES.md").is_file()
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is True, (g.reason, g.output[-2000:])


@pytest.mark.parametrize("task_id,name", MORE_CASES)
def test_an_extra_decoy_fails(task_id, name, tmp_path):
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "work")
    apply_solution(name, work, root=MORE_DECOYS / task_id)
    assert (MORE_DECOYS / task_id / name / "NOTES.md").is_file()
    if any(work.glob("test_*.py")):
        _visible_tests_pass(work)
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is False, (g.reason, g.output[-2000:])


def _piece_cases() -> list[tuple[str, str]]:
    return [
        (task_id, path.relative_to(DECOYS / task_id).as_posix()[: -len(".sol")])
        for task_id in PIECEWISE
        for path in sorted((DECOYS / task_id).rglob("*.sol"))
    ]


@pytest.mark.parametrize("task_id,piece", _piece_cases())
def test_each_piece_is_graded_on_its_own(task_id, piece, tmp_path):
    """The solution with ONE piece (one module) swapped for the decoy's fails:
    a team that does four pieces well and one lazily does not pass."""
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "work")
    apply_solution(task_id, work)
    (work / piece).write_bytes((DECOYS / task_id / f"{piece}.sol").read_bytes())
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is False, (piece, g.reason)


# --------------------------------------------------------------------------- #
# The checks stay off the network
# --------------------------------------------------------------------------- #

GUARD = r'''
import json, os, socket, subprocess, sys, unittest

attempts = []


def refuse(kind):
    def call(*args, **kwargs):
        attempts.append([kind, repr(args)[:200]])
        raise OSError(kind + " refused by the bench pin")
    return call


socket.getaddrinfo = refuse("getaddrinfo")
socket.gethostbyname = refuse("gethostbyname")
socket.gethostbyname_ex = refuse("gethostbyname_ex")
socket.create_connection = refuse("create_connection")
socket.socket.connect = refuse("connect")
socket.socket.connect_ex = refuse("connect_ex")
os.system = refuse("os.system")
real_init = subprocess.Popen.__init__


def popen(self, *args, **kwargs):
    attempts.append(["subprocess", repr(args)[:200]])
    return real_init(self, *args, **kwargs)


subprocess.Popen.__init__ = popen
report, pattern = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.getcwd())
run = unittest.main(module=None, argv=["guard", "discover", "-s", ".", "-p", pattern], exit=False)
with open(report, "w", encoding="utf-8") as fh:
    json.dump({"attempts": attempts, "ok": run.result.wasSuccessful(), "ran": run.result.testsRun}, fh)
'''


@pytest.mark.parametrize("task_id", TEAM)
def test_the_check_never_reaches_the_network(task_id, tmp_path):
    """The check is run exactly as the bench runs it, on the solved copy, with
    every way out to the network (and every new process) refused and recorded:
    nothing is tried, and the check still passes."""
    task = TASKS[task_id]
    assert list(task.check[:6]) == ["{python}", "-I", "-m", "unittest", "discover", "-s"], task.check
    pattern = task.check[-1]
    for hidden in task.check_dir.rglob("*.py"):
        text = hidden.read_text(encoding="utf-8")
        for module in ("socket", "urllib", "http", "ssl", "requests", "subprocess", "asyncio"):
            assert f"import {module}" not in text and f"from {module}" not in text, (hidden.name, module)
    work = prepare_workspace(task, tmp_path / "work")
    apply_solution(task_id, work)
    graded = prepare_grading_copy(task, work, tmp_path / "grade")
    guard = tmp_path / "guard.py"
    guard.write_text(GUARD, encoding="utf-8")
    report = tmp_path / "report.json"
    result = procs.run([sys.executable, "-I", str(guard), str(report), pattern], cwd=graded, timeout=300)
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert seen["attempts"] == [], seen["attempts"]
    assert seen["ok"] is True and seen["ran"] > 0, procs.text(result.stderr, "utf-8")[-2000:]


# --------------------------------------------------------------------------- #
# Honest answer keys
# --------------------------------------------------------------------------- #


def _load_check(task_id: str, name: str):
    path = TASKS[task_id].check_dir / name
    spec = importlib.util.spec_from_file_location(f"_ij_{task_id.replace('-', '_')}_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_audit_answer_key_is_what_a_real_resolver_finds(tmp_path):
    """The hidden list was recorded by the generator as it wrote each call; the
    reference solution resolves the calls from the code alone with ast. They
    agree exactly, and the text-search decoy finds others."""
    task = TASKS["team-audit-events"]
    expected = set(_load_check(task.id, "test_hidden_audit_events.py").EXPECTED)
    assert len(expected) >= 100
    work = prepare_workspace(task, tmp_path / "w")
    apply_solution(task.id, work)
    found = {(w["file"], w["function"], w["event"])
             for w in json.loads((work / "answer.json").read_text(encoding="utf-8"))["writes"]}
    assert found == expected
    decoy = prepare_workspace(task, tmp_path / "d")
    apply_solution(task.id, decoy, root=DECOYS)
    got = {(w["file"], w["function"], w["event"])
           for w in json.loads((decoy / "answer.json").read_text(encoding="utf-8"))["writes"]}
    assert got - expected and expected - got


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to apply the patch")
def test_the_patch_applies_cleanly_and_its_own_tests_pass_with_its_bug(tmp_path):
    """The incoming change is a real patch: ``git apply`` takes it as written,
    the result is exactly the pinned decoy (the change as sent, bug included),
    and every test — the patch's own included — passes with the bug in."""
    task = TASKS["team-review-patch"]
    work = prepare_workspace(task, tmp_path / "w")
    # A checkout with autocrlf has CRLF in the patch AND in its targets; the
    # patch is about content, so compare and apply on LF (as an LF checkout is).
    for path in work.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    patch = "incoming/0001-Add-loyalty-tiers.patch"
    applied = procs.run(["git", "apply", patch], cwd=work, timeout=60)
    assert applied.returncode == 0, procs.text(applied.stderr, "utf-8")
    for sol in sorted((DECOYS / task.id).rglob("*.sol")):
        rel = sol.relative_to(DECOYS / task.id).as_posix()[: -len(".sol")]
        assert (work / rel).read_bytes().replace(b"\r\n", b"\n") == sol.read_bytes().replace(b"\r\n", b"\n"), rel
    _visible_tests_pass(work)


NODE_HARNESS = r'''
const fs = require("fs");
const s = require(process.argv[2]);
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const v = (x) => (x === "@inf" ? Infinity : x === "@nan" ? NaN : x);
const call = (f) => { try { return { ok: f() }; } catch (e) { return { error: e.constructor.name }; } };
const out = {
  format_bytes: cases.format_bytes.map(([b, opts]) => call(() => s.formatBytes(v(b), opts))),
  parse_query: cases.parse_query.map((q) => call(() => s.parseQuery(q))),
  natural_compare: cases.natural_compare.map(([a, b]) => call(() => s.naturalCompare(a, b))),
  natural_sort: cases.natural_sort.map((l) => call(() => s.naturalSort(l))),
  wrap_text: cases.wrap_text.map(([t, w]) => call(() => s.wrapText(t, w))),
  title_case: cases.title_case.map((t) => call(() => s.titleCase(t))),
};
fs.writeFileSync(process.argv[4], JSON.stringify(out));
'''


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the JavaScript reference")
def test_the_port_expectations_are_what_the_javascript_reference_returns(tmp_path):
    """Every expected value in the port's hidden check is what
    reference/strutil.js itself returns for that input (run here with node;
    the check, and the agent, never run it)."""
    task = TASKS["team-port-js"]
    check = _load_check(task.id, "test_hidden_port.py")

    def marker(x):
        if isinstance(x, float) and x != x:
            return "@nan"
        if x == float("inf"):
            return "@inf"
        return x

    def outcome(expected):
        if isinstance(expected, str) and expected.startswith("raises "):
            return {"error": {"raises TypeError": "TypeError", "raises ValueError": "RangeError"}[expected]}
        return {"ok": expected}

    cases = {
        "format_bytes": [[marker(value), kwargs] for value, kwargs, _ in check.FORMAT_BYTES],
        "parse_query": [q for q, _ in check.PARSE_QUERY],
        "natural_compare": [list(pair) for pair, _ in check.NATURAL_COMPARE],
        "natural_sort": [items for items, _ in check.NATURAL_SORT],
        "wrap_text": [list(args) for args, _ in check.WRAP_TEXT],
        "title_case": [text for text, _ in check.TITLE_CASE],
    }
    want = {
        "format_bytes": [outcome(e) for _, _, e in check.FORMAT_BYTES],
        "parse_query": [outcome(e) for _, e in check.PARSE_QUERY],
        "natural_compare": [outcome(e) for _, e in check.NATURAL_COMPARE],
        "natural_sort": [outcome(e) for _, e in check.NATURAL_SORT],
        "wrap_text": [outcome(e) for _, e in check.WRAP_TEXT],
        "title_case": [outcome(e) for _, e in check.TITLE_CASE],
    }
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    (tmp_path / "harness.js").write_text(NODE_HARNESS, encoding="utf-8")
    ran = procs.run(["node", str(tmp_path / "harness.js"), str(task.repo_dir / "reference" / "strutil.js"),
                     str(tmp_path / "cases.json"), str(tmp_path / "got.json")], timeout=120)
    assert ran.returncode == 0, procs.text(ran.stderr, "utf-8")
    got = json.loads((tmp_path / "got.json").read_text(encoding="utf-8"))
    for name in want:
        assert len(got[name]) == len(want[name]), name
        for i, (g, w) in enumerate(zip(got[name], want[name])):
            assert g == w, (name, cases[name][i], g, w)
