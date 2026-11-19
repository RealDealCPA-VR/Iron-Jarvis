"""The Linux release always finds the version it rehearses from, and finishes a half-done upload.

Fourth review, group 31 (LX2, LX3):

* LX2: the previous Linux version was looked up on ONE page of 40 releases,
  and "none found" was read as "this is the first Linux release ever", so a
  version could be published without the update rehearsal. Every page is read
  now, and "none found" while ``updates-linux`` names an older version is a
  failed check.
* LX3: any Linux file on the release meant "already published - leaving it",
  so an upload that stopped between the files stopped that version for good.
  A release's Linux state is now none, complete, partial or broken; a partial
  one is finished file by file, the update file last, and a file GitHub
  already has with other bytes is never replaced.

The decisions live in ``scripts/linux_release_plan.py`` (stdlib only, run by
the runner's own python3 from the WORKFLOW's commit, not the tag). These tests
feed it release lists shaped like GitHub's own pages.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "linux-release.yml"
SCRIPT = ROOT / "scripts" / "linux_release_plan.py"

sys.path.insert(0, str(ROOT / "scripts"))
import linux_release_plan as P  # noqa: E402

APP, DEB, UPD = "Iron-Jarvis-x86_64.AppImage", "Iron-Jarvis-amd64.deb", "latest-linux.yml"


def _text() -> str:
    return WF.read_text(encoding="utf-8").replace("\r\n", "\n")


def _doc() -> dict:
    return yaml.safe_load(_text())


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _steps(job: str) -> list[dict]:
    return _doc()["jobs"][job]["steps"]


def _run_text(job: str) -> str:
    return "\n".join(str(s.get("run", "")) for s in _steps(job))


# --------------------------------------------------------------------------- #
# releases shaped like GitHub's pages
# --------------------------------------------------------------------------- #


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _asset(name: str, data: bytes = b"x", state: str = "uploaded", digest: bool = True) -> dict:
    # The fields GitHub returns that the plan never reads ride along, as on a real page.
    a = {"url": f"https://api.example.invalid/assets/{name}", "id": 1, "name": name, "label": "",
         "content_type": "application/octet-stream", "state": state, "size": len(data),
         "download_count": 3}
    a["digest"] = _digest(data) if digest else None
    return a


def _release(ver: str, *, linux: bool = False, draft: bool = False, pre: bool = False, assets=None) -> dict:
    files = [_asset("Iron-Jarvis-Setup-%s.exe" % ver), _asset("latest.yml")]
    if linux:
        files += [_asset(APP), _asset(DEB), _asset(UPD)]
    if assets is not None:
        files = assets
    return {"url": "https://api.example.invalid/r", "id": 7, "tag_name": f"v{ver}", "name": f"v{ver}",
            "draft": draft, "prerelease": pre, "body": "notes", "assets": files}


def _pages(releases: list[dict], size: int = 100) -> str:
    """What ``gh api --paginate`` prints without --jq: one JSON array per page, back to back."""
    return "".join(json.dumps(releases[i:i + size]) for i in range(0, len(releases), size))


def _jq_lines(releases: list[dict]) -> str:
    """What the workflow's ``--paginate --jq '.[] | {...}'`` prints: one object per line."""
    keep = ("name", "state", "digest", "size")
    out = []
    for r in releases:
        out.append(json.dumps({"tag_name": r["tag_name"], "draft": r["draft"], "prerelease": r["prerelease"],
                               "assets": [{k: a.get(k) for k in keep} for a in r["assets"]]}))
    return "\n".join(out) + "\n"


def _many(n: int, linux_at: set[int]) -> list[dict]:
    """``n`` releases, newest first, 1.<n+100>.0 down to 1.101.0; the ones at the given
    1-based positions carry the Linux files."""
    return [_release(f"1.{100 + n - i}.0", linux=(i + 1) in linux_at) for i in range(n)]


# --------------------------------------------------------------------------- #
# LX2: every page, and "none found" after a Linux release is a failure
# --------------------------------------------------------------------------- #


def test_the_previous_linux_version_is_found_past_the_first_page():
    rel = _many(376, linux_at={201})          # page 3 of 4 (100 a page)
    version = rel[0]["tag_name"][1:]          # the newest
    want = rel[200]["tag_name"][1:]
    for text in (_pages(rel), _jq_lines(rel)):
        releases = P.read_releases(text)
        assert len(releases) == 376
        assert P.previous_linux_version(releases, version) == want
        assert P.plan_previous(releases, version, branch="1.300.0") == (want, False)
    # The workflow reads EVERY page, 100 at a time, never one page of 40.
    gate = _code(_run_text("plan"))
    assert 'gh api --paginate "repos/$GITHUB_REPOSITORY/releases?per_page=100"' in gate
    assert "per_page=40" not in _code(_text())


def test_a_dry_run_of_1_526_0_rehearses_from_1_525_0():
    """The real shape on 2026-11-18: 1.526.0, 1.524.0 and 1.523.0 have no Linux
    files, and updates-linux names 1.531.0 (newer, so it is not used)."""
    no_linux = {"1.526.0", "1.524.0", "1.523.0"}
    rel = [_release(f"1.{m}.0", linux=f"1.{m}.0" not in no_linux) for m in range(531, 516, -1)]
    rel += [_release(f"1.{m}.0") for m in range(516, 400, -1)]    # before Linux existed
    rel.insert(3, _release("1.525.7", linux=True, pre=True))        # never a prerelease
    rel.insert(0, _release("1.525.5", linux=True, draft=True))      # never a draft
    releases = P.read_releases(_pages(rel))
    assert P.plan_previous(releases, "1.526.0", branch="1.531.0") == ("1.525.0", False)
    # Only an OLDER version: 1.526.0 itself and newer ones are never the start.
    assert P.previous_linux_version(releases, "1.525.0") == "1.522.0"
    # The real v1.517.0: nothing older has the files and the branch names a newer one.
    assert P.plan_previous(releases, "1.517.0", branch="1.531.0") == (None, True)


def test_none_found_when_the_branch_names_an_older_version_is_a_failed_check():
    rel = _many(60, linux_at=set())
    releases = P.read_releases(_jq_lines(rel))
    with pytest.raises(P.PlanError, match=re.escape(
            "No earlier Linux version was found to rehearse from, but updates-linux names 1.120.0. "
            "Nothing is published without the rehearsal.")):
        P.plan_previous(releases, "1.150.0", branch="1.120.0")
    # No branch at all: Linux was never published, so this is the first.
    assert P.plan_previous(releases, "1.150.0", branch=None) == (None, True)
    # The branch names this very version or a newer one: the first that has the files.
    assert P.plan_previous(releases, "1.150.0", branch="1.150.0") == (None, True)


def test_a_list_that_lacks_the_version_itself_is_refused_as_cut_short():
    rel = _many(40, linux_at=set())
    with pytest.raises(P.PlanError, match="did not include v1.200.0"):
        P.plan_previous(P.read_releases(_jq_lines(rel)), "1.200.0", branch=None)


def test_the_branch_version_is_read_from_the_update_file():
    assert P.branch_version("version: 1.531.0\nfiles:\n  - url: x\n") == "1.531.0"
    assert P.branch_version("version: '1.531.0'\n") == "1.531.0"
    assert P.branch_version("") is None
    with pytest.raises(P.PlanError, match="names no version"):
        P.branch_version("files: []\n")


# --------------------------------------------------------------------------- #
# LX3: none, complete, partial, broken
# --------------------------------------------------------------------------- #


def test_a_release_is_none_complete_partial_or_broken():
    assert P.release_state(_release("1.540.0")).kind == "none"
    assert P.release_state(_release("1.540.0", linux=True)).kind == "complete"
    two = _release("1.540.0", assets=[_asset(APP), _asset(DEB)])
    st = P.release_state(two)
    assert st.kind == "partial" and st.present == [APP, DEB] and st.missing == [UPD]
    # An upload GitHub never finished taking in.
    half = _release("1.540.0", assets=[_asset(APP, state="starter")])
    st = P.release_state(half)
    assert st.kind == "broken" and st.bad == [APP]
    # A Linux file this workflow does not make (the old preview's name) is never built over.
    old = _release("1.480.0", assets=[_asset("Iron-Jarvis-1.480.0-linux-preview.AppImage")])
    assert P.release_state(old).kind == "broken"


def test_a_release_with_two_of_three_files_gets_the_third(tmp_path):
    app, deb, upd = b"the single file" * 50, b"the package" * 50, b"version: 1.540.0\n"
    for name, data in ((APP, app), (DEB, deb), (UPD, upd)):
        (tmp_path / name).write_bytes(data)
    rel = _release("1.540.0", assets=[_asset(APP, app), _asset(DEB, deb)])
    plan = P.attach_plan(rel, tmp_path)
    assert [(s.name, s.action) for s in plan] == [(APP, "keep"), (DEB, "keep"), (UPD, "upload")]
    # Nothing there yet: all three go up, the update file last.
    plan = P.attach_plan(_release("1.540.0"), tmp_path)
    assert [(s.name, s.action) for s in plan] == [(APP, "upload"), (DEB, "upload"), (UPD, "upload")]
    # Everything there from this build: nothing to upload, and the branch step still runs.
    full = _release("1.540.0", assets=[_asset(APP, app), _asset(DEB, deb), _asset(UPD, upd)])
    assert [s.action for s in P.attach_plan(full, tmp_path)] == ["keep", "keep", "keep"]


def test_files_from_another_build_are_never_replaced(tmp_path):
    for name in (APP, DEB, UPD):
        (tmp_path / name).write_bytes(b"this build's " + name.encode())
    rel = _release("1.540.0", assets=[_asset(APP, b"another build"), _asset(DEB, b"another build")])
    plan = P.attach_plan(rel, tmp_path)
    assert [(s.name, s.action) for s in plan] == [(APP, "refuse"), (DEB, "refuse"), (UPD, "upload")]
    # A half-uploaded asset is never "kept" or built over either.
    rel = _release("1.540.0", assets=[_asset(APP, (tmp_path / APP).read_bytes(), state="starter")])
    assert P.attach_plan(rel, tmp_path)[0].action == "refuse"


def test_a_file_github_gave_no_checksum_for_is_compared_by_its_bytes(tmp_path):
    data = b"the single file"
    (tmp_path / APP).write_bytes(data)
    (tmp_path / DEB).write_bytes(b"d")
    (tmp_path / UPD).write_bytes(b"u")
    rel = _release("1.540.0", assets=[_asset(APP, data, digest=False)])
    assert P.attach_plan(rel, tmp_path)[0].action == "refuse"          # cannot tell: never guessed
    fetched = tmp_path / "fetched"
    fetched.mkdir()
    (fetched / APP).write_bytes(data)
    assert P.attach_plan(rel, tmp_path, fetch=lambda name: fetched / name)[0].action == "keep"
    (fetched / APP).write_bytes(b"other")
    assert P.attach_plan(rel, tmp_path, fetch=lambda name: fetched / name)[0].action == "refuse"


# --------------------------------------------------------------------------- #
# the commands the workflow runs
# --------------------------------------------------------------------------- #


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def _outputs(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return out


def _files(tmp_path: Path, rel: list[dict], version: str, branch: str | None) -> tuple[Path, Path, Path]:
    (tmp_path / "releases.jsonl").write_text(_jq_lines(rel), encoding="utf-8")
    this = next(r for r in rel if r["tag_name"] == f"v{version}")
    (tmp_path / "release.json").write_text(json.dumps(this), encoding="utf-8")
    (tmp_path / "branch.yml").write_text("" if branch is None else f"version: {branch}\n", encoding="utf-8")
    return tmp_path / "releases.jsonl", tmp_path / "release.json", tmp_path / "branch.yml"


def _plan(tmp_path, rel, version, branch, attach) -> tuple[subprocess.CompletedProcess, dict]:
    rels, this, br = _files(tmp_path, rel, version, branch)
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    proc = _cli("plan", "--releases", str(rels), "--release", str(this), "--version", version,
                "--branch", str(br), "--attach", attach, "--out", str(out))
    return proc, _outputs(out)


def test_the_plan_command_says_where_the_rehearsal_starts(tmp_path):
    no_linux = {"1.526.0", "1.524.0", "1.523.0"}
    rel = [_release(f"1.{m}.0", linux=m >= 517 and f"1.{m}.0" not in no_linux) for m in range(531, 500, -1)]
    proc, out = _plan(tmp_path, rel, "1.526.0", "1.531.0", "false")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out == {"build": "true", "version": "1.526.0", "attach": "false", "state": "none",
                   "previous": "1.525.0", "first": "false"}
    assert "the Linux release of v1.526.0 (publish=false; Linux files: none; rehearse from: 1.525.0)" in proc.stdout
    # The real first one says so in its own words.
    proc, out = _plan(tmp_path, rel, "1.517.0", "1.531.0", "false")
    assert proc.returncode == 0 and out["previous"] == "" and out["first"] == "true"
    assert "rehearse from: nothing, this is the first version with the Linux files" in proc.stdout


def test_the_plan_command_fails_and_builds_nothing_when_the_rehearsal_would_be_skipped(tmp_path):
    rel = [_release(f"1.{m}.0") for m in range(540, 499, -1)]
    proc, out = _plan(tmp_path, rel, "1.540.0", "1.520.0", "false")
    assert proc.returncode == 1
    assert "::error::No earlier Linux version was found to rehearse from, but updates-linux names 1.520.0." in proc.stdout
    assert out.get("build") != "true"


def test_in_publish_mode_a_partial_release_stops_the_plan_with_what_to_do(tmp_path):
    rel = [_release("1.540.0", assets=[_asset(APP), _asset(DEB)]), _release("1.539.0", linux=True)]
    proc, out = _plan(tmp_path, rel, "1.540.0", "1.539.0", "true")
    assert proc.returncode == 1 and out.get("build") != "true"
    assert (f"v1.540.0 has only {APP}, {DEB} of the three Linux files. Re-run the failed jobs of the "
            "Linux run that uploaded them") in proc.stdout
    # A dry run builds anyway and only says what it found.
    proc, out = _plan(tmp_path, rel, "1.540.0", "1.539.0", "false")
    assert proc.returncode == 0 and out["build"] == "true" and out["state"] == "partial"


def test_in_publish_mode_a_complete_release_builds_nothing_unless_the_branch_lags(tmp_path):
    rel = [_release("1.540.0", linux=True), _release("1.539.0", linux=True)]
    proc, out = _plan(tmp_path, rel, "1.540.0", "1.540.0", "true")
    assert proc.returncode == 0 and out["build"] == "false" and out["state"] == "complete"
    proc, out = _plan(tmp_path, rel, "1.540.0", "1.539.0", "true")
    assert proc.returncode == 1 and out.get("build") != "true"
    assert ("v1.540.0 has all three Linux files but updates-linux still names 1.539.0: re-run the "
            "failed jobs of the Linux run that published it.") in proc.stdout


def test_the_attach_command_writes_only_what_to_upload_and_refuses_before_anything(tmp_path):
    built = tmp_path / "out"
    built.mkdir()
    app = b"the single file"
    for name, data in ((APP, app), (DEB, b"d"), (UPD, b"u")):
        (built / name).write_bytes(data)
    rel = tmp_path / "release.json"
    rel.write_text(json.dumps(_release("1.540.0", assets=[_asset(APP, app)])), encoding="utf-8")
    out = tmp_path / "attach.txt"
    proc = _cli("attach", "--release", str(rel), "--dir", str(built), "--version", "1.540.0", "--out", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out.read_text(encoding="utf-8").splitlines() == [f"keep {APP}", f"upload {DEB}", f"upload {UPD}"]
    # Other bytes already there: exit 1, and the list names nothing to upload.
    (built / APP).write_bytes(b"a rebuild")
    proc = _cli("attach", "--release", str(rel), "--dir", str(built), "--version", "1.540.0", "--out", str(out))
    assert proc.returncode == 1
    assert not [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.startswith("upload")]
    assert (f"::error::v1.540.0 already has {APP}, with other bytes than this build or never fully uploaded. "
            "Nothing was uploaded.") in proc.stdout
    # --report (the rehearsal's read-only step) says the same and never fails the job.
    proc = _cli("attach", "--release", str(rel), "--dir", str(built), "--version", "1.540.0", "--out", str(out), "--report")
    assert proc.returncode == 0 and f"refuse  {APP}" in proc.stdout
    # All three there from this build.
    (built / APP).write_bytes(app)
    rel.write_text(json.dumps(_release("1.540.0", assets=[
        _asset(APP, app), _asset(DEB, b"d"), _asset(UPD, b"u")])), encoding="utf-8")
    proc = _cli("attach", "--release", str(rel), "--dir", str(built), "--version", "1.540.0", "--out", str(out))
    assert proc.returncode == 0
    assert "v1.540.0 already has all three Linux files from this build - checking the update branch" in proc.stdout


# --------------------------------------------------------------------------- #
# the workflow calls it, from the workflow's own commit
# --------------------------------------------------------------------------- #


def _tooling(step: dict) -> bool:
    w = step.get("with") or {}
    return str(step.get("uses", "")).startswith("actions/checkout@") and w.get("path") == "tooling" \
        and w.get("ref") == "${{ github.sha }}" and w.get("sparse-checkout") == "scripts" \
        and w.get("persist-credentials") is False


def test_plan_rehearsal_and_publish_run_the_script_from_the_workflows_commit():
    plan = _steps("plan")
    assert _tooling(plan[0])
    assert "python3 tooling/scripts/linux_release_plan.py plan" in plan[1]["run"]
    for job in ("rehearsal", "publish"):
        steps = _steps(job)
        # The tag stays the first checkout (it cleans the folder; the tooling goes in after it).
        assert steps[0]["with"]["ref"] == "v${{ needs.plan.outputs.version }}", job
        assert _tooling(steps[1]), job
    outputs = _doc()["jobs"]["plan"]["outputs"]
    assert outputs["first"] == "${{ steps.gate.outputs.first }}"
    assert outputs["state"] == "${{ steps.gate.outputs.state }}"


def test_the_branch_is_read_and_only_a_404_means_there_is_none():
    gate = _code(_run_text("plan"))
    assert 'contents/latest-linux.yml?ref=updates-linux" \\\n' in gate
    assert '-H "Accept: application/vnd.github.raw" > branch.yml 2> branch.err' in gate
    assert 'elif grep -q "HTTP 404" branch.err; then' in gate and "else cat branch.err; exit 1; fi" in gate


def test_the_rehearsal_cannot_be_skipped_unless_this_is_the_first():
    steps = {s.get("name"): s for s in _steps("rehearsal")}
    guard = steps["No earlier Linux version to rehearse from"]
    assert guard["if"] == "needs.plan.outputs.previous == '' && needs.plan.outputs.first != 'true'"
    assert "exit 1" in guard["run"]
    first = steps["Nothing to rehearse from: the first version with the Linux files"]
    assert first["if"] == "needs.plan.outputs.previous == '' && needs.plan.outputs.first == 'true'"
    assert "This is the first version with the Linux files, so there is no earlier one to rehearse from." in first["run"]
    would = steps["What publish would do"]
    assert "linux_release_plan.py attach" in would["run"] and "--report" in would["run"]
    assert "--dir new" in would["run"] and "GITHUB_STEP_SUMMARY" in would["run"]


def test_publish_uploads_one_file_at_a_time_the_update_file_last_after_the_check():
    publish = _code(_run_text("publish"))
    check = publish.index("python3 tooling/scripts/linux_release_plan.py attach --release release.json --dir out")
    loop = publish.index('for f in "$APPIMAGE" "$DEB" latest-linux.yml; do\n'
                         '  if grep -qxF "upload $f" attach.txt; then\n'
                         '    gh release upload "v$VERSION" "out/$f" --repo "$GITHUB_REPOSITORY"')
    assert check < loop and publish.count("gh release upload") == 1
    assert "already has a Linux file - leaving it" not in publish
    assert not re.search(r"\.\(AppImage\|deb\)\$", publish), "the any-file early exit is gone"
    # The branch step is unchanged: it is the repair when the update file went up and the push did not.
    assert 'gh release download "v$VERSION" --repo "$GITHUB_REPOSITORY" --pattern latest-linux.yml' in publish
    assert "updates-linux already names $have, newer than $VERSION - leaving it" in publish


def test_the_script_names_nothing_windows_reads():
    src = _code(SCRIPT.read_text(encoding="utf-8"))
    assert not re.search(r"(?<![\w-])latest\.yml", src)
    for word in (".exe", "blockmap", "Setup", "--clobber", "delete", "updates-stable", "promote_stable"):
        assert word not in src, word
    assert set(re.findall(r"\bupdates(?:-[a-z]+)*\b", src)) <= {"updates-linux"}
    # stdlib only: the runner's python3 runs it with no install.
    imports = set(re.findall(r"^(?:from|import) (\w+)", src, re.M))
    assert imports <= {"__future__", "argparse", "dataclasses", "hashlib", "json", "re", "subprocess",
                       "sys", "tempfile", "pathlib", "typing"}, imports


# --------------------------------------------------------------------------- #
# LX4's step: the desktop's own parsers on the Linux gate
# --------------------------------------------------------------------------- #


def test_the_linux_gate_installs_the_desktop_parsers_and_runs_the_autostart_test():
    steps = _steps("python")
    names = [s.get("name") for s in steps]
    tools = steps[names.index("The desktop's own parsers, for the start-at-sign-in test")]
    run = tools["run"]
    assert "sudo apt-get install -y --no-install-recommends desktop-file-utils python3-gi gir1.2-glib-2.0" in run
    assert "desktop-file-validate --help" in run and "/usr/bin/python3 -c" in run
    assert "systemd-xdg-autostart-generator" in run
    real = steps[names.index("Start at sign-in, against the desktop's own parsers")]
    assert real["if"] == "matrix.shard == 1"
    assert "tests/test_fourth_review_linux_autostart_real_v1540.py" in real["run"]
    assert 'if [ ! -f "$t" ]; then' in real["run"]   # an older tag has no such test
    suite = names.index("Run this part of the suite on Linux")
    assert names.index("The desktop's own parsers, for the start-at-sign-in test") < suite
    assert names.index("Start at sign-in, against the desktop's own parsers") < suite
