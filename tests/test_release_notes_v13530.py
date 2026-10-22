"""v1.353.0 (G37): the release page says which file to take and what to press
at the Windows warning.

The latest release read "Iron Jarvis 1.351.0 — installer built and uploaded by
CI." above three files (the .exe, its .blockmap, latest.yml). The installer is
unsigned, so Windows says "Windows protected your PC" — and only the README
said which file to download and to press More info, then Run anyway. Someone
sent the latest-release link never sees the README.

`release.yml`'s `publish` job (the `windows-installer` job before v1.370.3) carries ONE notes text
(`env.RELEASE_NOTES`, `@VERSION@` replaced in bash) that BOTH the create step
(a new release) and the publish step (`gh release edit`, so a draft an earlier
run left behind gets it too) pass as `--notes`.

A workflow cannot run here, so the pins read the parsed workflow: the words a
person reads, and that both `gh` calls carry them. Where a real Git Bash is on
this machine, the substitution line itself is RUN through bash.
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
VERSION = "1.353.0"
SUBST = 'notes="${RELEASE_NOTES//@VERSION@/$ver}"'


def _text(path: Path) -> str:
    # Windows CI checks out with autocrlf: normalise at the reader.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _job() -> dict:
    # v1.370.3: the release is created and made public by the `publish` job
    # (the installer job only builds now, while the tests run).
    return yaml.safe_load(_text(RELEASE))["jobs"]["publish"]


def _step(name_start: str) -> dict:
    steps = [s for s in _job()["steps"] if str(s.get("name", "")).startswith(name_start)]
    assert len(steps) == 1, (name_start, [s.get("name") for s in _job()["steps"]])
    return steps[0]


def _notes_for(version: str) -> str:
    """What bash's ${RELEASE_NOTES//@VERSION@/$ver} yields (every occurrence)."""
    return _job()["env"]["RELEASE_NOTES"].replace("@VERSION@", version)


# --------------------------------------------------------------------- words


def test_the_notes_name_the_one_file_to_download_and_the_two_to_ignore():
    notes = _notes_for(VERSION)
    # The exact asset electron-builder publishes (latest.yml names it so).
    assert f"`Iron-Jarvis-Setup-{VERSION}.exe`" in notes
    assert "Which file to download" in notes
    assert "Ignore the `.blockmap` and `latest.yml` files" in notes
    assert "updater" in notes  # why they are there at all
    # No placeholder survives the substitution.
    assert "@VERSION@" not in notes


def test_the_notes_say_what_to_press_at_the_windows_warning():
    notes = _notes_for(VERSION)
    assert '"Windows protected your PC"' in notes
    # The two presses, in order — the same words as the README and Help.
    more, run = notes.index("**More info**"), notes.index("**Run anyway**")
    assert more < run
    assert "isn't code-signed yet" in notes
    assert "once per download" in notes


def test_the_notes_link_why_it_is_unsigned_and_tell_an_existing_user_to_do_nothing():
    notes = _notes_for(VERSION)
    link = re.search(r"\[Why it isn't signed yet\]\(([^)]+)\)", notes)
    assert link, notes
    assert link.group(1).endswith("/blob/master/docs/SIGNING.md")
    assert (ROOT / "docs" / "SIGNING.md").is_file()
    assert "Restart to update" in notes


def test_the_old_ci_only_body_is_gone():
    # Read the SCRIPTS (the comment above the job quotes the old words).
    scripts = "\n".join(str(s.get("run", "")) for s in _job()["steps"])
    assert "installer built and uploaded by CI" not in scripts
    assert "installer built and uploaded by CI" not in _job()["env"]["RELEASE_NOTES"]


# ---------------------------------------------------------- both gh calls


def test_a_new_release_is_created_with_the_notes():
    run = _step("Ensure the release tag + release exist")["run"]
    assert SUBST in run
    # The substitution comes BEFORE the create that uses it.
    assert run.index(SUBST) < run.index("gh release create")
    create = run[run.index("gh release create") :]
    assert '--notes "$notes"' in create


def test_publishing_passes_the_notes_again_so_an_old_draft_gets_them():
    run = _step("Publish the release (assets are fully uploaded)")["run"]
    assert SUBST in run
    edit = next(line for line in run.splitlines() if "gh release edit" in line)
    assert "--draft=false" in edit and "--latest" in edit
    assert '--notes "$notes"' in edit
    assert run.index(SUBST) < run.index("gh release edit")


def test_both_steps_read_the_job_level_text_not_a_copy():
    job = _job()
    assert "RELEASE_NOTES" in job["env"]
    for name in ("Ensure the release tag + release exist", "Publish the release (assets are fully uploaded)"):
        step = _step(name)
        # A step-level copy would drift from the job's text.
        assert "RELEASE_NOTES" not in (step.get("env") or {}), name
        assert step.get("shell") == "bash", name


# ------------------------------------------------- the real bash line, run


def _git_bash() -> str | None:
    """A real bash (Git Bash / Linux), never the WSL launcher in System32."""
    found = shutil.which("bash")
    if not found:
        return None
    if "system32" in found.lower().replace("/", "\\"):
        return None
    return found


@pytest.mark.skipif(_git_bash() is None, reason="no real bash on PATH")
@pytest.mark.parametrize(
    "step_name",
    ["Ensure the release tag + release exist", "Publish the release (assets are fully uploaded)"],
)
def test_each_steps_own_notes_line_run_by_bash_gives_the_versioned_notes(step_name):
    bash = _git_bash()
    # The line LIFTED from the step's script, not a copy in this file.
    line = next(
        ln.strip() for ln in _step(step_name)["run"].splitlines() if ln.strip().startswith("notes=")
    )
    env = dict(os.environ)
    env["RELEASE_NOTES"] = _job()["env"]["RELEASE_NOTES"]
    script = f'ver="{VERSION}"\n{line}\nprintf "%s" "$notes"'
    out = subprocess.run([bash, "-c", script], env=env, capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = out.stdout.decode("utf-8").replace("\r\n", "\n")
    assert got.strip() == _notes_for(VERSION).strip()
    assert f"Iron-Jarvis-Setup-{VERSION}.exe" in got
    assert "@VERSION@" not in got
