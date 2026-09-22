"""suite-02 (v1.286.0): a hang in the gate is a FAILURE that names its test.

Nothing bounded a test or a CI job. ~30 tests await an event with no bound and
14 join() with none, so a regression that stops such an event from firing hung
the run instead of failing it: locally with no clue which test (two full
attempts lost on 2026-09-21), and on CI for GitHub's 6-hour job default, during
which no update could ship.

Now pytest-timeout is in the dev extra with a per-test `timeout` in pyproject,
and every job in both workflows carries `timeout-minutes`.

The last test drives the REAL pytest, from the repo root, against the REAL
pyproject config, in the CI shape (xdist): a hung async test must end as one
failure naming its node id while its siblings still pass.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_carries_pytest_timeout_and_a_per_test_ceiling():
    data = _pyproject()
    dev = data["project"]["optional-dependencies"]["dev"]
    assert any(d.replace(" ", "").startswith("pytest-timeout") for d in dev), dev
    timeout = data["tool"]["pytest"]["ini_options"].get("timeout")
    # A ceiling on a hang, not a speed test: generous, but well under the CI
    # job bound so the TEST is named before the job is killed.
    assert isinstance(timeout, int) and 60 <= timeout <= 900, timeout
    # The lockfile CI installs from (`uv sync --extra dev`) carries it too.
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "pytest-timeout"' in lock
    assert '{ name = "pytest-timeout", marker = "extra == \'dev\'"' in lock


def _jobs(name: str) -> dict:
    wf = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return wf["jobs"]


def test_every_job_in_both_workflows_carries_timeout_minutes():
    expected = {
        "tests.yml": {"backend", "dashboard", "browser-addon", "desktop"},
        "release.yml": {"suite", "windows-installer"},
    }
    for name, must_have in expected.items():
        jobs = _jobs(name)
        # Anti-vacuity: the parse really found the jobs we know exist.
        assert must_have <= set(jobs), (name, sorted(jobs))
        for job, spec in jobs.items():
            minutes = spec.get("timeout-minutes")
            # GitHub's default is 360; a bound at or above it bounds nothing.
            assert isinstance(minutes, int) and 0 < minutes < 360, (name, job, minutes)


def test_the_per_test_ceiling_fires_before_the_ci_job_bound():
    timeout_s = _pyproject()["tool"]["pytest"]["ini_options"]["timeout"]
    for name, job in (("tests.yml", "backend"), ("release.yml", "suite")):
        assert timeout_s < _jobs(name)[job]["timeout-minutes"] * 60, (name, job)


_SCRATCH_TESTS = '''
import asyncio
import pytest


def test_quick_sibling():
    assert True


def test_ini_timeout_is_read_from_pyproject(request):
    # Unknown ini keys raise here, so this passes only when the plugin is
    # loaded AND pyproject's value reaches it.
    assert int(request.config.getini("timeout")) == {timeout}


@pytest.mark.timeout(5)
async def test_waits_for_an_answer_nobody_gives():
    await asyncio.Event().wait()
'''


def test_a_hung_async_test_fails_by_name_under_xdist_and_the_run_ends():
    timeout_s = _pyproject()["tool"]["pytest"]["ini_options"]["timeout"]
    scratch = ROOT / "tests" / f"_review_pin_tmp_{uuid.uuid4().hex[:8]}"
    rel = f"tests/{scratch.name}/test_hang_pin.py"
    try:
        scratch.mkdir()
        (scratch / "test_hang_pin.py").write_text(
            _SCRATCH_TESTS.replace("{timeout}", str(timeout_s)), encoding="utf-8"
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "--no-header",
             "-p", "no:cacheprovider", "-n", "2", rel],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            out, _ = proc.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            # Without the guard the run never ends; kill the whole tree (the
            # xdist workers are children) and fail with what it printed.
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True)
            else:
                proc.kill()
            out, _ = proc.communicate()
            raise AssertionError("the hung test was never stopped:\n" + out[-2000:])
        out = out.replace("\\", "/")
        assert proc.returncode == 1, out[-3000:]
        # The hang is named by its node id, not buried in asyncio frames.
        assert f"{rel}::test_waits_for_an_answer_nobody_gives" in out, out[-3000:]
        # A fresh worker took over and the rest of the run completed.
        assert "1 failed, 2 passed" in out, out[-3000:]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
