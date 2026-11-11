"""v1.470.1: the Windows installer is signed when the six signing values are set.

Nothing here can reach Microsoft's signing service, so the pins cover what the
repo decides: when a build signs, with which settings, and that a signed
release is CHECKED before it can be published.

* `desktop/builder-signed.js` is run under node with a made-up environment.
* `desktop/build-installer.ps1` and the release workflow are read as text /
  parsed YAML (a workflow cannot run here).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
PUBLISHER = "RealDealCPA.Ai LLC"
SIX = [
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "IJ_SIGN_ENDPOINT",
    "IJ_SIGN_ACCOUNT",
    "IJ_SIGN_PROFILE",
]
# Made-up values: none is a real id or secret.
FAKE = {
    "AZURE_TENANT_ID": "tenant-for-tests",
    "AZURE_CLIENT_ID": "client-for-tests",
    "AZURE_CLIENT_SECRET": "not-a-real-secret",
    "IJ_SIGN_ENDPOINT": "https://eus.codesigning.azure.net/",
    "IJ_SIGN_ACCOUNT": "acct",
    "IJ_SIGN_PROFILE": "profile",
}


def _text(path: Path) -> str:
    # Windows CI checks out with autocrlf: normalise at the reader.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _node_config(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    env = {k: v for k, v in os.environ.items() if k not in SIX and k != "IJ_SIGN_PUBLISHER"}
    env.update(env_extra)
    return subprocess.run(
        [node, "-e", "process.stdout.write(JSON.stringify(require('./builder-signed.js')))"],
        cwd=DESKTOP,
        env=env,
        capture_output=True,
        timeout=60,
    )


# ------------------------------------------------------- builder-signed.js


def test_the_signed_config_exports_nothing_but_build_settings():
    # electron-builder refuses a config with ANY property it does not know,
    # non-enumerable ones included (the first signed dry run died on one).
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    env = {**os.environ, **FAKE}
    out = subprocess.run(
        [node, "-e", "process.stdout.write(JSON.stringify(Object.getOwnPropertyNames(require('./builder-signed.js'))))"],
        cwd=DESKTOP, env=env, capture_output=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    base = json.loads(_text(DESKTOP / "package.json"))["build"]
    assert sorted(json.loads(out.stdout.decode("utf-8"))) == sorted(base)


def test_the_signed_config_is_the_normal_build_plus_the_signing_settings():
    out = _node_config(FAKE)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    got = json.loads(out.stdout.decode("utf-8"))
    base = json.loads(_text(DESKTOP / "package.json"))["build"]
    sign = got["win"].pop("azureSignOptions")
    # Everything else is package.json's build block, unchanged.
    assert got == base
    assert sign == {
        "publisherName": PUBLISHER,
        "endpoint": FAKE["IJ_SIGN_ENDPOINT"],
        "codeSigningAccountName": "acct",
        "certificateProfileName": "profile",
    }
    # The three sign-in values never enter the config (Microsoft's module
    # reads them from the environment).
    assert "not-a-real-secret" not in out.stdout.decode("utf-8")
    assert "tenant-for-tests" not in out.stdout.decode("utf-8")


@pytest.mark.parametrize("gone", SIX)
def test_a_missing_value_stops_the_signed_build_and_names_it(gone):
    env = {k: v for k, v in FAKE.items() if k != gone}
    out = _node_config(env)
    assert out.returncode != 0
    assert gone in out.stderr.decode("utf-8", "replace")


@pytest.mark.parametrize(
    "bad",
    ["http://eus.codesigning.azure.net/", "https://example.com/", "https://codesigning.azure.net.example.com/"],
)
def test_the_signing_address_must_be_microsofts(bad):
    out = _node_config({**FAKE, "IJ_SIGN_ENDPOINT": bad})
    assert out.returncode != 0
    assert "IJ_SIGN_ENDPOINT" in out.stderr.decode("utf-8", "replace")


def test_the_everyday_build_config_has_no_signing_in_it():
    # package.json alone must stay an unsigned build: local builds and CI
    # before the secrets exist read only this.
    win = json.loads(_text(DESKTOP / "package.json"))["build"]["win"]
    assert "azureSignOptions" not in win
    assert "sign" not in win and "signtoolOptions" not in win
    # The signed config is not packed into the app.
    assert "builder-signed.js" not in json.loads(_text(DESKTOP / "package.json"))["build"]["files"]
    assert not (DESKTOP / "sign.js").exists()


# ------------------------------------------------------ build-installer.ps1


def test_the_build_script_signs_on_all_six_and_stops_on_some():
    ps = _text(DESKTOP / "build-installer.ps1")
    for name in SIX:
        assert f'"{name}"' in ps, name
    # all present -> the signed config; some -> stop; none -> unsigned.
    assert '$ConfigArgs = @("--config", "builder-signed.js")' in ps
    assert "$SignMissing.Count -eq 0" in ps
    assert "$SignMissing.Count -lt $SignNames.Count" in ps
    assert "signing is half set up" in ps
    # Both electron-builder calls carry the choice; neither publishes by
    # itself unless asked.
    assert "electron-builder --win --publish never @ConfigArgs" in ps
    assert "electron-builder --win --publish always @ConfigArgs" in ps


# --------------------------------------------------------- release workflow


def _jobs() -> dict:
    return yaml.safe_load(_text(RELEASE))["jobs"]


def _installer_step(name: str) -> dict:
    steps = [s for s in _jobs()["windows-installer"]["steps"] if s.get("name") == name]
    assert len(steps) == 1, name
    return steps[0]


def test_only_the_build_step_sees_the_signing_secrets():
    build = _installer_step("Build the installer (not published)")
    assert set(build["env"]) == set(SIX)
    for name in SIX:
        assert build["env"][name] == "${{ secrets.%s }}" % name
    # No other step of any job is handed the client secret.
    for job_name, job in _jobs().items():
        assert "AZURE_CLIENT_SECRET" not in json.dumps(job.get("env") or {}), job_name
        for step in job.get("steps", []):
            if job_name == "windows-installer" and step.get("name") == build["name"]:
                continue
            assert "AZURE_CLIENT_SECRET" not in json.dumps(step.get("env") or {}), step.get("name")
            assert "secrets.AZURE_CLIENT_SECRET" not in str(step.get("run", "")), step.get("name")


def test_a_signed_build_is_checked_by_windows_before_it_is_kept():
    names = [s.get("name") for s in _jobs()["windows-installer"]["steps"]]
    build = names.index("Build the installer (not published)")
    check = names.index("Check the installer's signature")
    keep = names.index("Keep the installer files for the publish job")
    assert build < check < keep
    step = _installer_step("Check the installer's signature")
    assert step["id"] == "signature"
    assert step["env"]["PUBLISHER"] == PUBLISHER
    run = step["run"]
    assert "Get-AuthenticodeSignature" in run
    assert '$sig.Status -ne "Valid"' in run
    assert "$who -ne $env:PUBLISHER" in run
    assert "TimeStamperCertificate" in run
    # The installer, the app and the daemon are each checked.
    assert "*Setup*.exe" in run
    assert "win-unpacked/Iron Jarvis.exe" in run
    assert "win-unpacked/resources/daemon/ironjarvis.exe" in run
    # ... and the signed daemon must still start.
    assert "/health" in run and "the SIGNED daemon did not serve /health" in run
    # "signed=true" is written only after every check (it is the last line).
    assert run.strip().splitlines()[-1].strip() == '"signed=true" >> $env:GITHUB_OUTPUT'
    assert _jobs()["windows-installer"]["outputs"] == {"signed": "${{ steps.signature.outputs.signed }}"}


def test_an_unsigned_build_can_be_forbidden_once_signing_has_shipped():
    step = _installer_step("Check the installer's signature")
    assert step["env"]["REQUIRE_SIGNED"] == "${{ vars.IJ_REQUIRE_SIGNED }}"
    run = step["run"]
    unsigned = run[run.index('$env:SIGNING_ON -ne "true"') :]
    assert unsigned.index('$env:REQUIRE_SIGNED -eq "true"') < unsigned.index('"signed=false"')
    assert "refusing to build an unsigned release" in unsigned


def test_the_release_page_says_signed_only_for_a_signed_build():
    job = _jobs()["publish"]
    assert job["env"]["INSTALLER_SIGNED"] == "${{ needs.windows-installer.outputs.signed }}"
    signed, unsigned = job["env"]["RELEASE_NOTES_SIGNED"], job["env"]["RELEASE_NOTES"]
    assert f"Signed by {PUBLISHER}" in signed
    assert "isn't code-signed" not in signed
    assert "isn't code-signed yet" in unsigned
    assert PUBLISHER not in unsigned
    # Both say which file to take, with the same placeholder.
    for notes in (signed, unsigned):
        assert "`Iron-Jarvis-Setup-@VERSION@.exe`" in notes
        assert "/blob/master/docs/SIGNING.md" in notes
    switch = 'if [ "${INSTALLER_SIGNED:-}" = "true" ]; then RELEASE_NOTES="$RELEASE_NOTES_SIGNED"; fi'
    subst = 'notes="${RELEASE_NOTES//@VERSION@/$ver}"'
    for name in ("Ensure the release tag + release exist", "Publish the release (assets are fully uploaded)"):
        run = next(s for s in job["steps"] if str(s.get("name", "")).startswith(name))["run"]
        assert run.index(switch) < run.index(subst), name


def _git_bash() -> str | None:
    found = shutil.which("bash")
    if not found or "system32" in found.lower().replace("/", "\\"):
        return None
    return found


@pytest.mark.skipif(_git_bash() is None, reason="no real bash on PATH")
@pytest.mark.parametrize("signed", ["true", "false", ""])
def test_the_notes_switch_run_by_bash(signed):
    job = _jobs()["publish"]
    run = next(s for s in job["steps"] if str(s.get("name", "")).startswith("Ensure the release tag"))["run"]
    lines = [ln.strip() for ln in run.splitlines()]
    picked = [ln for ln in lines if ln.startswith("if [ \"${INSTALLER_SIGNED") or ln.startswith("notes=")]
    assert len(picked) == 2
    env = dict(os.environ)
    env["RELEASE_NOTES"] = job["env"]["RELEASE_NOTES"]
    env["RELEASE_NOTES_SIGNED"] = job["env"]["RELEASE_NOTES_SIGNED"]
    env["INSTALLER_SIGNED"] = signed
    script = 'ver="9.9.9"\n' + "\n".join(picked) + '\nprintf "%s" "$notes"'
    out = subprocess.run([_git_bash(), "-c", script], env=env, capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = out.stdout.decode("utf-8")
    assert "Iron-Jarvis-Setup-9.9.9.exe" in got and "@VERSION@" not in got
    assert (f"Signed by {PUBLISHER}" in got) == (signed == "true")
    assert ("isn't code-signed yet" in got) == (signed != "true")


def test_the_signing_page_names_the_secrets_and_the_lock():
    doc = _text(ROOT / "docs" / "SIGNING.md")
    for name in SIX:
        assert f"`{name}`" in doc, name
    assert "IJ_REQUIRE_SIGNED" in doc
    assert PUBLISHER in doc
