"""The Linux preview is built AFTER a release and can only add one file.

The owner's condition for a Linux build (2026-11-11): it must not affect the
Windows app in any way. So it is its own workflow: release.yml is untouched,
nothing here can block or fail the Windows installer, and the only job that may
write to the repository adds an AppImage to an already published release
without replacing anything, and never writes what the Windows app or the
Stable channel read (the installer, its blockmap, latest.yml, the updates
branch).
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "linux-preview.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return WF.read_text(encoding="utf-8").replace("\r\n", "\n")


def _doc() -> dict:
    return yaml.safe_load(_text())


def _code(text: str) -> str:
    """The file without its comment lines (the comments NAME what is avoided)."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_it_runs_only_after_a_release_run_or_by_hand():
    on = _doc()[True]  # YAML reads the key "on" as true
    assert set(on) == {"workflow_run", "workflow_dispatch"}
    assert on["workflow_run"] == {"workflows": ["Release"], "types": ["completed"]}
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True


def test_only_the_attach_job_may_write_and_every_job_is_bounded():
    doc = _doc()
    assert doc["permissions"] == {"contents": "read"}
    jobs = doc["jobs"]
    assert set(jobs) == {"plan", "build", "attach"}
    for name, job in jobs.items():
        assert isinstance(job.get("timeout-minutes"), int), name
        if name == "attach":
            assert job["permissions"] == {"contents": "write"}
        else:
            assert "permissions" not in job, name
    assert jobs["attach"]["needs"] == ["plan", "build"]
    assert jobs["attach"]["if"] == "needs.plan.outputs.attach == 'true'"


def test_it_never_replaces_a_file_or_writes_what_windows_reads():
    code = _code(_text())
    assert "--clobber" not in code
    assert "latest.yml" not in code
    assert "updates" not in code.replace("apt-get update", "")
    # The one mention is the note's own words ("take the .exe above").
    assert code.count(".exe") == 1 and "take the \`.exe\` above" in code
    assert "git push" not in code and "gh release create" not in code and "gh release delete" not in code
    assert code.count("gh release upload") == 1
    # Built from the published tag, never from a branch.
    assert "ref: v${{ needs.plan.outputs.version }}" in code
    assert "--publish never" in code


def test_the_gate_wants_a_successful_release_run_and_a_published_version():
    code = _code(_text())
    assert '"$RUN_CONCLUSION" != "success"' in code
    assert "--exclude-drafts --exclude-pre-releases" in code
    assert '"$sha" != "$RUN_SHA"' in code
    assert 'endswith(".AppImage")' in code


def test_the_release_workflow_knows_nothing_of_it():
    rel = RELEASE.read_text(encoding="utf-8")
    assert "AppImage" not in rel and "linux-preview" not in rel


def test_stable_reads_only_the_three_windows_files():
    src = (ROOT / "scripts" / "promote_stable.py").read_text(encoding="utf-8")
    assert 'return ("latest.yml", f"Iron-Jarvis-Setup-{version}.exe", f"Iron-Jarvis-Setup-{version}.exe.blockmap")' in src
