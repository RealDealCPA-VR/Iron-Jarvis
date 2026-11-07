"""The suite runs ONCE per release (v1.444.1).

Every push to master is a version bump, and a bump starts Release, whose gate
runs the whole suite and publishes nothing unless it is green. Tests ran the
same suite again on the same commit, so it no longer runs on a master push.
Three things must stay true for that to lose nothing:

1. Tests has no push trigger (and is still runnable by hand on a branch).
2. Release's gate checks everything Tests checked.
3. The Stable channel, which promoted only a commit whose Tests run passed,
   reads Release's SUITE jobs as the verdict, so it still promotes, and still
   never promotes a commit whose suite did not pass.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WF = _ROOT / ".github" / "workflows"
_SCRIPTS = _ROOT / "scripts"


def _mod():
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import promote_stable as mod  # noqa: WPS433
    finally:
        sys.path.pop(0)
    return mod


def _yaml(name: str) -> dict:
    return yaml.safe_load((_WF / name).read_bytes().decode("utf-8"))


def _on(wf: dict) -> dict:
    return wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True


def _runs(wf: dict) -> str:
    return "\n".join(
        str(step.get("run", "")) for job in wf["jobs"].values() for step in job.get("steps", [])
    ).replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# 1 + 2. the workflows
# --------------------------------------------------------------------------- #


def test_tests_never_runs_on_a_push_and_can_still_be_run_by_hand():
    on = _on(_yaml("tests.yml"))
    assert "push" not in on, on
    assert "workflow_dispatch" in on, "CLAUDE.md's branch gate is `gh workflow run tests.yml --ref <branch>`"


def test_release_still_runs_the_suite_on_every_master_push():
    on = _on(_yaml("release.yml"))
    assert on["push"]["branches"] == ["master"], on


def test_release_checks_everything_tests_checked():
    tests, release = _yaml("tests.yml"), _yaml("release.yml")
    t, r = _runs(tests), _runs(release)
    # The same shard plan for the Python suite, and its collection check.
    n = len(release["jobs"]["python"]["strategy"]["matrix"]["shard"])  # 12 since v1.446.1
    assert f"scripts/ci_shards.py --shards {n} --index" in t and f"scripts/ci_shards.py --shards {n} --index" in r
    assert "--check-collected" in t and "--check-collected" in r
    assert tests["jobs"]["backend"]["strategy"]["matrix"]["shard"] == release["jobs"]["python"]["strategy"]["matrix"]["shard"]
    # Dashboard: vitest, the type check, and the production build (the installer's).
    for command in ("pnpm test", "npx tsc --noEmit"):
        assert command in t and command in r, command
    assert "pnpm build" in t
    installer = Path(_ROOT / "desktop" / "build-installer.ps1").read_bytes().decode("utf-8")
    assert "build-installer.ps1" in r and "pnpm build" in installer, "the installer build is Release's `pnpm build`"
    # The browser add-on's build, typecheck and pinned-id check.
    assert "node scripts/build.mjs" in t and "node scripts/build.mjs" in r
    # Every desktop file Tests syntax-checks, Release checks too.
    checked_by_tests = set(re.findall(r"node --check (\S+\.js)", t))
    release_loop = re.search(r"for f in (.*?); do", r, re.S).group(1)
    checked_by_release = {Path(f).name for f in re.findall(r"desktop/(\S+\.js)", release_loop)}
    assert checked_by_tests and checked_by_tests <= checked_by_release, (checked_by_tests, checked_by_release)
    # The gate: publish needs every test job.
    assert {"suite", "python", "windows-installer"} <= set(release["jobs"]["publish"]["needs"])


# --------------------------------------------------------------------------- #
# 3. Stable reads Release's suite jobs
# --------------------------------------------------------------------------- #

_DONE = {"status": "completed", "conclusion": "success", "created_at": "2026-11-07T01:00:00Z", "id": 7}


def _jobs(suite="success", shards=("success",) * 6, publish="success"):
    out = [{"name": "plan", "conclusion": "success"}, {"name": "windows-installer", "conclusion": "success"}]
    if suite is not None:
        out.append({"name": "suite", "conclusion": suite})
    out += [{"name": f"python ({i})", "conclusion": c} for i, c in enumerate(shards, 1)]
    out.append({"name": "publish", "conclusion": publish})
    return out


def test_a_release_run_is_judged_by_its_suite_jobs():
    v = _mod().release_suite_run
    assert v(_DONE, _jobs())["conclusion"] == "success"
    red = list(("success",) * 5) + ["failure"]
    assert v(_DONE, _jobs(shards=red))["conclusion"] == "failure"
    assert v(_DONE, _jobs(suite="failure"))["conclusion"] == "failure"
    timed = ["timed_out"] + ["success"] * 5
    assert v(_DONE, _jobs(shards=timed))["conclusion"] == "failure"
    assert v(_DONE, _jobs(shards=["cancelled"] + ["success"] * 5))["conclusion"] == "cancelled"


def test_a_green_suite_whose_publishing_failed_is_still_green():
    # v1.260.0's shape: the suite passed, the tag push was refused.
    run = dict(_DONE, conclusion="failure")
    assert _mod().release_suite_run(run, _jobs(publish="failure"))["conclusion"] == "success"


def test_a_run_whose_suite_did_not_run_is_no_verdict():
    v = _mod().release_suite_run
    # A version already public: plan says no, every other job is skipped, and
    # the RUN itself ends "success". That must never read as a green suite.
    skipped = _jobs(suite="skipped", shards=("skipped",) * 6, publish="skipped")
    assert v(_DONE, skipped)["conclusion"] == "skipped"
    assert v(_DONE, [{"name": "plan", "conclusion": "success"}])["conclusion"] == "skipped"
    assert v(_DONE, _jobs(suite=None))["conclusion"] == "skipped", "no `suite` job: half a gate is no verdict"
    assert v(_DONE, _jobs(shards=()))["conclusion"] == "skipped", "no python shard: half a gate is no verdict"
    # One shard skipped beside shards that ran is never green.
    assert v(_DONE, _jobs(shards=["skipped"] + ["success"] * 5))["conclusion"] != "success"


def test_a_release_run_still_going_is_left_as_it_is():
    run = {"status": "in_progress", "created_at": "2026-11-07T01:00:00Z"}
    out = _mod().release_suite_run(run, ())
    assert out == run and out is not run


class _FakeGh:
    """Stands in for the `gh api` calls, by path."""

    def __init__(self, tests_runs, release_runs, jobs):
        self.answers = {
            "tests.yml": {"workflow_runs": tests_runs},
            "release.yml": {"workflow_runs": release_runs},
        }
        self.jobs = jobs
        self.paths: list[str] = []

    def __call__(self, *args: str) -> str:
        path = args[1]
        self.paths.append(path)
        if "/actions/runs/" in path:
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            return json.dumps({"jobs": self.jobs[run_id]})
        for wf, answer in self.answers.items():
            if f"/workflows/{wf}/runs" in path:
                return json.dumps(answer)
        raise AssertionError(path)


def _github(fake):
    mod = _mod()
    gh = mod.GitHub("owner/repo")
    gh._gh = fake  # type: ignore[method-assign]
    return mod, gh


def test_a_commit_only_release_judged_is_green_through_the_real_reader():
    fake = _FakeGh([], [dict(_DONE, id=11)], {11: _jobs()})
    mod, gh = _github(fake)
    assert mod.tests_verdict(gh.tests_runs("abc")) == "success"
    assert any("/actions/runs/11/jobs" in p for p in fake.paths)


def test_a_red_release_suite_keeps_the_commit_off_stable():
    fake = _FakeGh([], [dict(_DONE, id=11, conclusion="failure")], {11: _jobs(suite="failure", publish="skipped")})
    mod, gh = _github(fake)
    assert mod.tests_verdict(gh.tests_runs("abc")) == "failure"


def test_a_skipped_release_run_does_not_hide_an_earlier_red_tests_run():
    tests_red = {"status": "completed", "conclusion": "failure", "created_at": "2026-11-07T01:00:00Z", "id": 3}
    later_skip = dict(_DONE, id=12, created_at="2026-11-07T05:00:00Z")
    fake = _FakeGh([tests_red], [later_skip], {12: _jobs(suite="skipped", shards=("skipped",) * 6, publish="skipped")})
    mod, gh = _github(fake)
    assert mod.tests_verdict(gh.tests_runs("abc")) == "failure"


def test_a_newer_tests_run_by_hand_still_overrules_a_green_release():
    rel = dict(_DONE, id=11, created_at="2026-11-07T01:00:00Z")
    tests_red = {"status": "completed", "conclusion": "failure", "created_at": "2026-11-07T09:00:00Z", "id": 4}
    mod, gh = _github(_FakeGh([tests_red], [rel], {11: _jobs()}))
    assert mod.tests_verdict(gh.tests_runs("abc")) == "failure"


def test_a_release_run_still_going_reads_as_running_and_asks_for_no_jobs():
    going = {"status": "in_progress", "created_at": "2026-11-07T01:00:00Z", "id": 13}
    fake = _FakeGh([], [going], {})
    mod, gh = _github(fake)
    assert mod.tests_verdict(gh.tests_runs("abc")) == "running"
    assert not any("/actions/runs/" in p for p in fake.paths)
