"""A failed test runs once more in CI before the gate goes red (v1.445.1).

The user's decision: most red release gates were old timing tests on a busy
runner, each costing a version and a long wait. CI now retries a failed test
ONCE (pytest-rerunfailures ``--reruns 1``, vitest ``--retry=1``). The rules:

* a test that fails twice still fails the gate;
* a hang (pytest-timeout) is never retried;
* a local run is never retried (the flags live in the workflows only);
* every test that passed only on its retry is named on the run's page.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WF = _ROOT / ".github" / "workflows"
sys.path.insert(0, str(_ROOT / "scripts"))
try:
    import ci_flaky_report as R  # noqa: E402
finally:
    sys.path.pop(0)

# The plugin's real `-rR` block (pytest-rerunfailures 16.7, captured on this PC
# from a test that fails once then passes, and one that always fails).
_REAL = """\
=========================== rerun test summary info ===========================
RERUN test_flaky_once.py::test_always_red
FAILED test_flaky_once.py::test_always_red
RERUN test_flaky_once.py::test_passes_on_retry
PASSED test_flaky_once.py::test_passes_on_retry
1 failed, 2 passed, 2 rerun in 2.14s
"""


def _step(workflow: str, job: str, needle: str) -> str:
    wf = yaml.safe_load((_WF / workflow).read_bytes().decode("utf-8"))
    runs = [str(s.get("run", "")) for s in wf["jobs"][job]["steps"]]
    (run,) = [r for r in runs if needle in r]
    return run.replace("\r\n", "\n")


def test_both_workflows_retry_a_failed_python_test_once_and_never_a_hang():
    for workflow, job in (("release.yml", "python"), ("tests.yml", "backend")):
        run = _step(workflow, job, "uv run pytest -q")
        (cmd,) = [ln.strip() for ln in run.splitlines() if ln.strip().startswith("uv run pytest -q")]
        assert "--reruns 1 " in cmd, workflow
        assert '--rerun-except "pytest-timeout"' in cmd, f"{workflow}: a hang must never be retried"
        assert " -rR " in cmd, f"{workflow}: the report reads the plugin's RERUN lines"
        # pytest's own exit code decides; the report never can.
        assert "code=${PIPESTATUS[0]}" in run and run.rstrip().endswith('exit "$code"'), workflow
        report = run.index("scripts/ci_flaky_report.py pytest-out.txt")
        assert run.index("set -e", run.index("PIPESTATUS")) < report < run.rindex('exit "$code"'), workflow
        assert "ci_flaky_report.py pytest-out.txt \"shard ${{ matrix.shard }}\" || true" in run, workflow


def test_both_workflows_retry_a_failed_dashboard_test_once_and_list_it():
    for workflow, job in (("release.yml", "suite"), ("tests.yml", "dashboard")):
        run = _step(workflow, job, "pnpm test")
        # v1.446.1: each vitest part runs its share (`--shard=N/3`).
        assert run.strip() == (
            "pnpm test --retry=1 --reporter=default --reporter=github-actions --shard=${{ matrix.part }}/3"
        ), workflow


def test_a_local_run_is_never_retried():
    py = tomllib.loads((_ROOT / "pyproject.toml").read_bytes().decode("utf-8"))
    dev = " ".join(py["project"]["optional-dependencies"]["dev"])
    assert "pytest-rerunfailures" in dev
    opts = py["tool"]["pytest"]["ini_options"]
    assert "reruns" not in str(opts.get("addopts", "")) and "reruns" not in opts, opts
    config = (_ROOT / "dashboard" / "vitest.config.ts").read_bytes().decode("utf-8")
    assert "retry" not in config, "vitest's retry belongs to the CI command line only"


def test_the_report_names_only_the_tests_that_passed_on_retry():
    flaky, red = R.retried(_REAL)
    assert flaky == ["test_flaky_once.py::test_passes_on_retry"]
    assert red == ["test_flaky_once.py::test_always_red"]
    block = R.report(_REAL, "shard 3")
    assert "shard 3: 1 test(s) passed only on retry" in block and "test_passes_on_retry" in block
    assert "test_always_red" not in block, "a test red both times is the gate's failure, not a flake"
    assert R.report("120 passed in 3.1s\n", "shard 1") == ""
    # A PASSED line for a test that was never retried is not a flake.
    assert R.retried("PASSED a.py::t\n") == ([], [])


def test_the_report_writes_the_run_page_and_a_warning(tmp_path, capsys, monkeypatch):
    out = tmp_path / "pytest-out.txt"
    out.write_text(_REAL, encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert R.main([str(out), "shard 2"]) == 0
    assert "test_passes_on_retry" in summary.read_text(encoding="utf-8")
    printed = capsys.readouterr().out
    assert "::warning title=Passed only on retry (shard 2)::test_flaky_once.py::test_passes_on_retry" in printed
    # Nothing to say: nothing written, still exit 0; a missing file is not an error.
    summary.unlink()
    assert R.main([str(tmp_path / "absent.txt"), "shard 4"]) == 0 and not summary.exists()


_SAMPLE = '''\
import os, pathlib, time
MARK = pathlib.Path(os.environ["FLAKY_MARK"])
HUNG = pathlib.Path(os.environ["HUNG_MARK"])
def test_passes_on_retry():
    if not MARK.exists():
        MARK.write_text("x")
        assert False, "first try fails"
def test_always_red():
    assert False, "really broken"
def test_hangs():
    with HUNG.open("a") as f:
        f.write("x")
    time.sleep(60)
'''


def _real_run(tmp_path, select):
    (tmp_path / "pytest.ini").write_text("[pytest]\ntimeout = 3\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(_SAMPLE, encoding="utf-8")
    env = dict(os.environ, FLAKY_MARK=str(tmp_path / "mark"), HUNG_MARK=str(tmp_path / "hung"))
    env.pop("PYTEST_ADDOPTS", None)
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "-p", "no:xdist",
         "--reruns", "1", "--rerun-except", "pytest-timeout", "-rR", "-k", select, "test_sample.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    return res.returncode, res.stdout + res.stderr


def test_the_real_plugin_retries_once_and_keeps_a_real_failure_red(tmp_path):
    code, text = _real_run(tmp_path, "passes_on_retry or always_red")
    assert code == 1, "a test red twice must still fail the run:\n" + text
    flaky, red = R.retried(text)
    assert flaky == ["test_sample.py::test_passes_on_retry"], text
    assert red == ["test_sample.py::test_always_red"], text


def test_the_real_plugin_never_retries_a_hang(tmp_path):
    code, text = _real_run(tmp_path, "hangs")
    assert code != 0, text
    assert (tmp_path / "hung").read_text(encoding="utf-8") == "x", "the hang ran twice:\n" + text


