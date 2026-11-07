"""The gate runs as more, smaller jobs at once (v1.445.2).

A release waited for its slowest job: six Python shards of ~17-23 min and ONE
vitest job of ~18 min. Now twelve Python shards and three vitest parts run at
the same time, so each is about half as long. Nothing may be lost on the way:
every Python file is still in exactly one shard (scripts/ci_shards.py and its
own pins), every vitest part builds the add-on its runtime test executes, and
the checks that run once (type check, production build, desktop files) still
run, in part 1.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _yaml(name: str) -> dict:
    return yaml.safe_load((_WF / name).read_bytes().decode("utf-8"))


def _steps(wf: dict, job: str) -> list[dict]:
    return wf["jobs"][job]["steps"]


def test_both_workflows_run_twelve_python_shards():
    for name, job in (("release.yml", "python"), ("tests.yml", "backend")):
        j = _yaml(name)["jobs"][job]
        assert j["strategy"]["matrix"]["shard"] == list(range(1, 13)), name
        run = "\n".join(str(s.get("run", "")) for s in j["steps"])
        assert "--shards 12 --index ${{ matrix.shard }}" in run and "--shards 12 --check-collected" in run, name


def test_both_workflows_run_vitest_in_three_parts_with_everything_once():
    for name, job, once in (
        ("release.yml", "suite", ("npx tsc --noEmit", "node --check")),
        ("tests.yml", "dashboard", ("npx tsc --noEmit", "pnpm build")),
    ):
        wf = _yaml(name)
        j = wf["jobs"][job]
        parts = j["strategy"]["matrix"]["part"]
        assert parts == [1, 2, 3] and j["strategy"]["fail-fast"] is False, name
        steps = _steps(wf, job)
        (vitest,) = [s for s in steps if "pnpm test" in str(s.get("run", ""))]
        assert f"--shard=${{{{ matrix.part }}}}/{len(parts)}" in vitest["run"], name
        assert "if" not in vitest, f"{name}: every part runs its share of vitest"
        # The side-panel runtime test executes the built add-on: every part builds it, before vitest.
        build = [i for i, s in enumerate(steps) if "node scripts/build.mjs" in str(s.get("run", ""))]
        assert build and "if" not in steps[build[0]] and build[0] < steps.index(vitest), name
        for command in once:
            (step,) = [s for s in steps if command in str(s.get("run", ""))]
            assert step.get("if") == "matrix.part == 1", (name, command)


def test_publish_still_needs_every_part_and_every_shard():
    needs = _yaml("release.yml")["jobs"]["publish"]["needs"]
    assert {"plan", "suite", "python", "windows-installer"} <= set(needs)
