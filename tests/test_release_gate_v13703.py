"""v1.370.3 — the release pipeline runs its tests and builds its installer at
the same time, and ONLY the publish job can make anything public.

The user asked for faster releases "as long as it is safe for my computer".
What keeps it safe is one rule this file pins from every side: nothing reaches
the user's auto-updating app unless the WHOLE suite passed. The suite now runs
as parallel shards and the installer is built alongside it, so the rule moved
from "the installer job needs the suite" to "the publish job needs every test
job AND the build, and no other job can publish".
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
#: Commands that make something public or move a release ref.
PUBLISHING = re.compile(r"gh release (create|upload|edit)|git push|--publish always|-Publish\b")


def _wf() -> dict:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8").replace("\r\n", "\n"))


def _runs(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job.get("steps", []))


def test_publish_needs_every_test_job_and_the_build():
    jobs = _wf()["jobs"]
    pub = jobs["publish"]
    assert set(pub["needs"]) >= {"plan", "suite", "python", "windows-installer"}
    cond = str(pub["if"])
    # A status function would override the implicit success() that keeps a
    # red shard from publishing.
    assert not re.search(r"\b(always|failure|cancelled)\(\)", cond), cond
    assert "needs.plan.outputs.publish == 'true'" in cond
    assert "needs.plan.outputs.dry_run != 'true'" in cond


def test_no_job_but_publish_can_make_anything_public():
    for name, job in _wf()["jobs"].items():
        hits = PUBLISHING.findall(_runs(job))
        if name == "publish":
            assert hits, "the publish job publishes"
        else:
            assert not hits, f"job {name!r} runs a publishing command: {hits}"


def test_the_installer_job_builds_without_publishing():
    job = _wf()["jobs"]["windows-installer"]
    build = [s for s in job["steps"] if "build-installer.ps1" in str(s.get("run", ""))]
    assert len(build) == 1 and "-Publish" not in build[0]["run"]
    # ... and electron-builder itself is told never to publish there.
    script = (ROOT / "desktop" / "build-installer.ps1").read_text(encoding="utf-8")
    assert "--publish never" in script
    assert "pnpm dist }" not in script, "pnpm dist leaves publishing to electron-builder's CI default"
    # Its files are kept privately for the publish job, and checked first.
    uses = [str(s.get("uses", "")) for s in job["steps"]]
    assert any(u.startswith("actions/upload-artifact@") for u in uses)
    assert "latest.yml does not name" in _runs(job) and "sha512" in _runs(job)


def test_the_test_jobs_and_the_build_all_start_from_the_plan():
    jobs = _wf()["jobs"]
    for name in ("suite", "python", "windows-installer"):
        assert jobs[name]["needs"] == "plan", name
        assert "needs.plan.outputs.publish == 'true'" in str(jobs[name]["if"]), name


def test_the_release_goes_public_only_after_every_asset_is_on_it():
    steps = [s["name"] for s in _wf()["jobs"]["publish"]["steps"] if "name" in s]
    order = [
        "Ensure the release tag + release exist",
        "Attach the installer files to the draft release",
        "Publish the release (assets are fully uploaded)",
        "Publish the update manifest to the updates branch",
    ]
    assert [n for n in steps if n in order] == order
    attach = next(s for s in _wf()["jobs"]["publish"]["steps"] if s.get("name") == order[1])["run"]
    assert "is not on the release" in attach


# --------------------------------------------- the plan's gate, run for real


def _gate_script() -> str:
    step = next(s for s in _wf()["jobs"]["plan"]["steps"] if s.get("id") == "gate")
    return step["run"]


def _bash() -> str | None:
    return shutil.which("bash")


def _run_gate(tmp_path: Path, *, ref_type: str, ref_name: str, dry: str = "", released: str = "") -> dict:
    fake = tmp_path / "bin"
    fake.mkdir(parents=True)
    # A fake `gh`: "false" = a published release (isDraft false), "" = none.
    (fake / "gh").write_text(f'#!/usr/bin/env bash\necho "{released}"\n', encoding="utf-8")
    os.chmod(fake / "gh", 0o755)
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REF_TYPE": ref_type,
        "GITHUB_REF_NAME": ref_name,
        "GITHUB_REPOSITORY": "owner/repo",
        "DRY_RUN": dry,
        "LC_ALL": "C.UTF-8",
    }
    work = tmp_path / "repo"
    work.mkdir()
    (work / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.2.3"\n', encoding="utf-8")
    proc = subprocess.run([_bash(), "-eo", "pipefail", "-c", _gate_script()], cwd=work, env=env,
                          capture_output=True, text=True, timeout=60)
    got = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
    got["_code"] = proc.returncode
    got["_err"] = proc.stdout + proc.stderr
    return got


needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@needs_bash
def test_gate_master_with_an_unreleased_version_publishes(tmp_path):
    g = _run_gate(tmp_path, ref_type="branch", ref_name="master")
    assert g["_code"] == 0, g["_err"]
    assert g["publish"] == "true" and g["dry_run"] == "false" and g["version"] == "1.2.3"


@needs_bash
def test_gate_master_with_a_published_version_skips(tmp_path):
    g = _run_gate(tmp_path, ref_type="branch", ref_name="master", released="false")
    assert g["_code"] == 0 and g["publish"] == "false"


@needs_bash
def test_gate_another_branch_never_publishes_for_real(tmp_path):
    g = _run_gate(tmp_path, ref_type="branch", ref_name="feature-x")
    assert g["_code"] == 0 and g["publish"] == "false"


@needs_bash
def test_gate_a_dry_run_on_a_branch_builds_and_tests_but_is_marked_dry(tmp_path):
    g = _run_gate(tmp_path, ref_type="branch", ref_name="feature-x", dry="true")
    assert g["_code"] == 0 and g["publish"] == "true" and g["dry_run"] == "true"


@needs_bash
def test_gate_a_tag_must_name_the_version_the_code_carries(tmp_path):
    ok = _run_gate(tmp_path / "a", ref_type="tag", ref_name="v1.2.3")
    assert ok["_code"] == 0 and ok["publish"] == "true"
    bad = _run_gate(tmp_path / "b", ref_type="tag", ref_name="v9.9.9")
    assert bad["_code"] != 0 and "does not match" in bad["_err"]
    assert bad.get("publish") != "true"


# ------------------------------------- the installer check, run for real (review)


def _check_script() -> str:
    job = _wf()["jobs"]["windows-installer"]
    step = next(s for s in job["steps"] if s.get("name") == "Check the installer files before they are kept")
    return step["run"].replace("${{ needs.plan.outputs.version }}", "1.2.3")


def _built(tmp_path: Path, *, payload: bytes = b"installer bytes") -> Path:
    """The folder electron-builder really leaves with `--publish never` (the first
    dry run's `ls`): productName with SPACES, latest.yml naming the dashed name."""
    import base64
    import hashlib

    rel = tmp_path / "desktop" / "release"
    rel.mkdir(parents=True)
    (rel / "Iron Jarvis Setup 1.2.3.exe").write_bytes(payload)
    (rel / "Iron Jarvis Setup 1.2.3.exe.blockmap").write_bytes(b"blockmap")
    sha = base64.b64encode(hashlib.sha512(payload).digest()).decode()
    (rel / "latest.yml").write_text(
        "version: 1.2.3\nfiles:\n  - url: Iron-Jarvis-Setup-1.2.3.exe\n"
        f"    sha512: {sha}\n    size: {len(payload)}\npath: Iron-Jarvis-Setup-1.2.3.exe\n"
        f"sha512: {sha}\nreleaseDate: '2026-10-22T12:47:00.000Z'\n",
        encoding="utf-8",
    )
    return rel


def _run_check(cwd: Path) -> subprocess.CompletedProcess:
    import sys

    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run([_bash(), "-eo", "pipefail", "-c", _check_script()], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


@needs_bash
def test_the_installer_check_keeps_what_electron_builder_really_writes(tmp_path):
    rel = _built(tmp_path)
    proc = _run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    names = sorted(p.name for p in rel.iterdir())
    assert names == ["Iron-Jarvis-Setup-1.2.3.exe", "Iron-Jarvis-Setup-1.2.3.exe.blockmap", "latest.yml"]


@needs_bash
def test_the_installer_check_refuses_an_installer_latest_yml_does_not_describe(tmp_path):
    rel = _built(tmp_path)
    (rel / "Iron Jarvis Setup 1.2.3.exe").write_bytes(b"some other build")
    proc = _run_check(tmp_path)
    assert proc.returncode != 0 and "sha512 is not this installer" in proc.stdout + proc.stderr
