"""The Linux release is its OWN workflow, gated on Linux, and writes only its own things.

The owner's condition for everything Linux (2026-11-11, repeated 2026-11-16):
no effect on the Windows app or its release at all. So:

* ``release.yml`` knows nothing of Linux, and this workflow runs only AFTER a
  Release run has finished (or by hand).
* The one job that may write (``publish``) needs the whole Linux gate: the
  Python suite, the dashboard suite, both files built and started the way a
  person would, and the update rehearsal.
* It writes the two Linux files and ``latest-linux.yml`` on the release, one
  line in the notes and the branch ``updates-linux``. Never the installer, its
  blockmap, ``latest.yml``, ``updates`` or ``updates-stable``, and it never
  replaces a file.
* At most five of its jobs run at once, so a Windows release that starts
  meanwhile is not left waiting for machines.
"""

from __future__ import annotations

import base64
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
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
MERGE = ROOT / "scripts" / "linux_update_manifest.py"

sys.path.insert(0, str(ROOT / "scripts"))
import linux_update_manifest as M  # noqa: E402
import linux_search_request as SR  # noqa: E402


def _text() -> str:
    return WF.read_text(encoding="utf-8").replace("\r\n", "\n")


def _doc() -> dict:
    return yaml.safe_load(_text())


def _code(text: str) -> str:
    """The file without its comment lines (the comments NAME what is avoided)."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _steps(job: str) -> str:
    return "\n".join(str(s.get("run", "")) for s in _doc()["jobs"][job]["steps"])


# --------------------------------------------------------------------------- #
# when it runs, what may write
# --------------------------------------------------------------------------- #


def test_it_runs_only_after_a_release_run_or_by_hand():
    on = _doc()[True]  # YAML reads the key "on" as true
    assert set(on) == {"workflow_run", "workflow_dispatch"}
    assert on["workflow_run"] == {"workflows": ["Release"], "types": ["completed"]}
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert not (ROOT / ".github" / "workflows" / "linux-preview.yml").exists(), "two workflows would both attach files"


def test_only_publish_may_write_and_it_needs_the_whole_linux_gate():
    doc = _doc()
    assert doc["permissions"] == {"contents": "read"}
    jobs = doc["jobs"]
    assert set(jobs) == {"plan", "python", "dashboard", "build", "rehearsal", "publish"}
    for name, job in jobs.items():
        assert isinstance(job.get("timeout-minutes"), int), name
        if name == "publish":
            assert job["permissions"] == {"contents": "write"}
        else:
            assert "permissions" not in job, name
    assert jobs["publish"]["needs"] == ["plan", "python", "dashboard", "build", "rehearsal"]
    # No status function: a failed, cancelled or skipped gate job stops it.
    assert jobs["publish"]["if"] == "needs.plan.outputs.attach == 'true'"
    assert jobs["rehearsal"]["needs"] == ["plan", "build"]
    for name in ("python", "dashboard", "build", "rehearsal"):
        assert jobs[name]["if"] == "needs.plan.outputs.build == 'true'", name


def test_a_windows_release_is_never_left_waiting_for_machines():
    doc = _doc()
    py = doc["jobs"]["python"]["strategy"]
    assert py["matrix"]["shard"] == [1, 2, 3, 4, 5, 6] and py["max-parallel"] == 3 and py["fail-fast"] is False
    assert "strategy" not in doc["jobs"]["dashboard"] and "strategy" not in doc["jobs"]["build"]
    # python x3 + dashboard + build at the same time: five.
    assert doc["concurrency"] == {"group": "linux-release", "cancel-in-progress": False}
    assert all(job["runs-on"] == "ubuntu-latest" for job in doc["jobs"].values())


def test_everything_is_built_and_tested_from_the_published_tag():
    for name in ("python", "dashboard", "build", "rehearsal", "publish"):
        first = _doc()["jobs"][name]["steps"][0]
        assert first["uses"].startswith("actions/checkout@") and first["with"]["ref"] == "v${{ needs.plan.outputs.version }}", name


# --------------------------------------------------------------------------- #
# what Windows reads is never written
# --------------------------------------------------------------------------- #


def test_it_never_replaces_a_file_or_writes_what_windows_reads():
    code = _code(_text())
    assert "--clobber" not in code and "-f origin" not in code and "push -f" not in code and "--force" not in code
    assert not re.search(r"(?<![\w-])latest\.yml", code), "latest.yml is the Windows app's"
    assert ".exe" not in code and "blockmap" not in code and "Setup" not in code
    assert "gh release create" not in code and "gh release delete" not in code and "delete-asset" not in code
    # Every branch it names is Linux's own; the Windows ones are never spelled.
    assert re.findall(r"HEAD:(\S+)", code) == ["updates-linux"]
    assert set(re.findall(r"\bupdates(?:-[a-z]+)*\b", code.replace("apt-get update", ""))) == {"updates-linux"}
    publish = _steps("publish")
    assert publish.count("gh release upload") == 2 and publish.count("git push") == 0
    assert publish.count('push origin HEAD:updates-linux') == 1
    # Only the publish job touches the release or a branch.
    for name in ("plan", "python", "dashboard", "build", "rehearsal"):
        text = _steps(name)
        assert "gh release upload" not in text and "gh release edit" not in text and "push origin" not in text, name


def test_the_update_file_goes_up_last_and_an_older_version_never_overwrites_a_newer():
    publish = _steps("publish")
    files = publish.index('gh release upload "v$VERSION" "out/$APPIMAGE" "out/$DEB"')
    manifest = publish.index('gh release upload "v$VERSION" out/latest-linux.yml')
    assert files < manifest
    assert "already has a Linux file - leaving it" in publish
    assert "sort -V | tail -1" in publish and "leaving it" in publish
    assert "scripts/publish_update_manifest.py" in publish  # absolute addresses, refuses a wrong version


def test_the_release_workflow_knows_nothing_of_linux():
    rel = RELEASE.read_text(encoding="utf-8")
    for word in ("AppImage", "linux-release", "linux-preview", "latest-linux", "updates-linux", ".deb"):
        assert word not in rel, word


def test_stable_reads_only_the_three_windows_files():
    src = (ROOT / "scripts" / "promote_stable.py").read_text(encoding="utf-8")
    assert 'return ("latest.yml", f"Iron-Jarvis-Setup-{version}.exe", f"Iron-Jarvis-Setup-{version}.exe.blockmap")' in src


# --------------------------------------------------------------------------- #
# the gate itself
# --------------------------------------------------------------------------- #


def test_the_plan_wants_a_successful_release_run_and_a_published_version():
    code = _steps("plan")
    assert '"$RUN_CONCLUSION" != "success"' in code
    assert "--exclude-drafts --exclude-pre-releases" in code
    assert '"$sha" != "$RUN_SHA"' in code
    assert 'endswith(".AppImage") or endswith(".deb")' in code


def test_each_file_is_built_alone_proven_and_started_the_way_a_person_would():
    build = _steps("build")
    # One builder run per file, the single file first, and never published by the builder.
    assert build.count("pnpm exec electron-builder --linux") == 2
    assert build.index("electron-builder --linux AppImage") < build.index("electron-builder --linux deb")
    assert "--publish never" in build and "-c.toolsets.appimage=1.0.3" in build
    assert "test ! -e release/linux-unpacked/resources/package-type" in build
    assert 'test "$(cat release/linux-unpacked/resources/package-type)" = "deb"' in build
    assert "scripts/linux_update_manifest.py" in build
    # The frozen service is this version, and its search program runs.
    assert 'grep -q "\\"version\\": *\\"$VERSION\\"" health.json' in build
    assert "ironjarvis search-worker < req.json" in build
    # No helper flag anywhere: the earlier check hid two faults behind them.
    whole = _code(_text())
    assert "--no-sandbox" not in whole and "--appimage-extract-and-run" not in whole and "libfuse" not in whole
    assert 'bash scripts/linux_app_check.sh appimage "desktop/out/$APPIMAGE"' in build
    assert 'bash scripts/linux_app_check.sh deb "desktop/out/$DEB"' in build
    check = (ROOT / "scripts" / "linux_app_check.sh").read_text(encoding="utf-8")
    assert "--appimage-extract-and-run" not in _code(check) and "libfuse" not in _code(check)
    assert "xvfb-run -a \"${start[@]}\"" in check
    for said in ("the service never answered", "the pages did not answer", "left running after the app was asked to stop",
                 "runs with the safety box off"):
        assert said in check, said


def test_the_rehearsal_updates_the_previous_version_both_ways_from_this_machine_only():
    rehearsal = _steps("rehearsal")
    assert 'linux_update_rehearsal.sh "old/$APPIMAGE" new quit' in rehearsal
    assert 'linux_update_rehearsal.sh "old/$APPIMAGE" new restart' in rehearsal
    script = (ROOT / "scripts" / "linux_update_rehearsal.sh").read_text(encoding="utf-8")
    assert 'export IRONJARVIS_UPDATE_REHEARSAL_URL="http://127.0.0.1:$port"' in script
    assert 'python3 -m http.server "$port" --bind 127.0.0.1' in script
    # It passes on the FILE: the one the person started is now the new one.
    assert script.count('= "$target"') >= 3 and "sha256sum" in script
    assert 'grep -q "\\"version\\": *\\"$want\\""' in script


def test_the_search_request_is_one_the_app_hands_to_its_search_program(tmp_path):
    from iron_jarvis.harness import search as S

    req = SR.request(str(tmp_path))
    opts = S.GrepOptions(**req["opts"])
    assert req["kind"] == "grep" and set(req["limits"]) == {"deadline_s", "max_files", "max_file_bytes"}
    (tmp_path / "a.txt").write_text("alpha\nneedle here\nomega\n", encoding="utf-8")
    report = S.grep(tmp_path, tmp_path.resolve(), opts)
    assert report.matches == 1


# --------------------------------------------------------------------------- #
# one manifest for both files, proven against the files
# --------------------------------------------------------------------------- #


def _b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def _manifest(name: str, data: bytes, version: str = "1.517.0", blockmap: bool = False) -> str:
    lines = [f"version: {version}", "files:", f"  - url: {name}", f"    sha512: {_b64(data)}", f"    size: {len(data)}"]
    if blockmap:
        lines.append("    blockMapSize: 425676")
    lines += [f"path: {name}", f"sha512: {_b64(data)}", "releaseDate: '2026-11-16T23:03:56.025Z'"]
    return "\n".join(lines) + "\n"


@pytest.fixture()
def built(tmp_path):
    app, deb = b"the single file" * 100, b"the package" * 100
    (tmp_path / "Iron-Jarvis-x86_64.AppImage").write_bytes(app)
    (tmp_path / "Iron-Jarvis-amd64.deb").write_bytes(deb)
    return tmp_path, _manifest("Iron-Jarvis-x86_64.AppImage", app, blockmap=True), _manifest("Iron-Jarvis-amd64.deb", deb)


def test_the_two_manifests_become_one_that_names_both_files(built):
    folder, a, d = built
    out = M.merge([a, d], "1.517.0", folder)
    doc = yaml.safe_load(out)
    assert doc["version"] == "1.517.0"
    assert [f["url"] for f in doc["files"]] == ["Iron-Jarvis-x86_64.AppImage", "Iron-Jarvis-amd64.deb"]
    assert doc["files"][0]["blockMapSize"] == 425676 and "blockMapSize" not in doc["files"][1]
    # The single file is the main one, and every checksum is the builder's own.
    assert doc["path"] == "Iron-Jarvis-x86_64.AppImage" and doc["sha512"] == doc["files"][0]["sha512"]
    assert doc["files"][1]["sha512"] == _b64((folder / "Iron-Jarvis-amd64.deb").read_bytes())
    assert doc["releaseDate"] == "2026-11-16T23:03:56.025Z"
    # publish_update_manifest (the Windows release's own script, unchanged) reads it.
    sys.path.insert(0, str(ROOT / "scripts"))
    import publish_update_manifest as P

    absolute = P.rewrite(out, "RealDealCPA-VR/Iron-Jarvis", "1.517.0")
    base = "https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/download/v1.517.0"
    assert f"  - url: {base}/Iron-Jarvis-x86_64.AppImage" in absolute
    assert f"  - url: {base}/Iron-Jarvis-amd64.deb" in absolute
    assert f"path: {base}/Iron-Jarvis-x86_64.AppImage" in absolute


def test_a_manifest_that_does_not_describe_the_files_is_refused(built):
    folder, a, d = built
    cases = {
        "not the 1.518.0 being built": lambda: M.merge([a, d], "1.518.0", folder),
        "version must be X.Y.Z": lambda: M.merge([a, d], "1.517", folder),
        "listed twice": lambda: M.merge([a, a], "1.517.0", folder),
        "lists no files": lambda: M.merge(["version: 1.517.0\nfiles:\n"], "1.517.0", folder),
        "not a plain file name": lambda: M.merge([_manifest("../x.AppImage", b"x")], "1.517.0", folder),
    }
    for words, call in cases.items():
        with pytest.raises(M.ManifestError, match=re.escape(words)):
            call()
    # A file changed after the builder described it: the checksum, then the size.
    (folder / "Iron-Jarvis-amd64.deb").write_bytes(b"THE PACKAGE" * 100)
    with pytest.raises(M.ManifestError, match="sha512 is not this file's"):
        M.merge([a, d], "1.517.0", folder)
    (folder / "Iron-Jarvis-amd64.deb").unlink()
    with pytest.raises(M.ManifestError, match="is in a manifest but not in"):
        M.merge([a, d], "1.517.0", folder)


def test_the_merge_command_writes_the_file_and_fails_loudly(built, tmp_path):
    folder, a, d = built
    (tmp_path / "a.yml").write_text(a, encoding="utf-8")
    (tmp_path / "d.yml").write_text(d, encoding="utf-8")
    out = tmp_path / "out" / "latest-linux.yml"
    run = lambda version: subprocess.run(  # noqa: E731
        [sys.executable, str(MERGE), str(out), str(tmp_path / "a.yml"), str(tmp_path / "d.yml"),
         "--version", version, "--dir", str(folder)],
        capture_output=True, text=True, timeout=60,
    )
    ok = run("1.517.0")
    assert ok.returncode == 0 and "Iron-Jarvis-x86_64.AppImage, Iron-Jarvis-amd64.deb" in ok.stdout
    assert out.read_bytes().count(b"\r") == 0 and json.dumps(yaml.safe_load(out.read_text(encoding="utf-8")))
    bad = run("1.518.0")
    assert bad.returncode == 1 and "not the 1.518.0 being built" in bad.stderr
