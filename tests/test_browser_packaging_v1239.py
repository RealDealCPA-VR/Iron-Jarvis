"""The browser add-on SHIPS INSIDE THE INSTALLER — every link of that chain, pinned.

Ship 5 of the Browser capability (v1.239.0), decision D27: the built add-on is bundled
with Iron Jarvis so a user who has never seen a source checkout can point Chrome's
"Load unpacked" at a folder inside their own installation. Nothing here adds a
capability. It makes an existing one REACHABLE, and reachability is the thing this
repository has shipped broken before while every test was green.

WHY A SOURCE-INSPECTION TEST, AND WHAT IT IS AND IS NOT WORTH. A packaged installer
cannot be built inside the offline suite: it needs PyInstaller, a Next standalone
build, electron-builder and about half an hour. So the bundling itself is verified by
the first real build. What this file can do — and what nothing else does — is pin the
CHAIN, because the chain is made of links whose absence is INVISIBLE until a user
tries to load the add-on from an installed app:

  extensions/chrome/dist/     is gitignored, so it exists only if a build produced it
    -> scripts/build.mjs      the one entry point that produces and verifies it
    -> build-installer.ps1    calls it, so a LOCAL installer contains it too
    -> both CI workflows      call it, so the gate covers what the installer contains
    -> extraResources         names the folder and copies it to resources/browser-addon
    -> afterPack.js           INVENTORIES that folder in the integrity manifest
    -> main.js                resolves the folder in the frozen AND the dev layout

Break any one of those and the others still pass. Three of them fail with no error
message at all:

* A missing `dist/` gets Chrome's "Service worker registration failed. Status code:
  15", which names no file. The Your browser card then sits on "Waiting to pair"
  forever while both logs stay silent.
* A resource directory absent from afterPack's hard-coded inventory list is simply
  not recorded in install-manifest.json — so integrity.js cannot notice it missing at
  boot, which is the exact half-installed-bundle failure that file was written for
  (the v1.124.0 truncated update). The check passes, loudly, on a broken install.
* A CI-only extension build ships a LOCAL installer whose add-on folder holds a
  manifest.json and nothing else. The build is green; the product is hollow.

WHAT IS PINNED BY EXECUTION rather than by inspection: `test_build_wrapper_produces_a
_loadable_addon` really runs scripts/build.mjs and checks the folder it produced. It
skips where extensions/chrome/node_modules is absent — CI's backend job installs no
Node dependencies, and the dedicated `browser-addon` job in tests.yml runs the same
command for real on every push. The disk probe lives INSIDE the test body, never in a
parametrize list: a collection-time probe makes two xdist workers disagree about how
many tests exist and the whole suite never runs (v1.236.1).

Nothing here reads a constant twice. The service worker's filename comes from
manifest.json and is matched against the extraResources filter; the inventory list is
compared against the `to:` targets computed from package.json; the workflow steps are
read out of parsed YAML rather than grepped, so a step that moved into the wrong job
is caught.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ADDON_DIR = REPO / "extensions" / "chrome"
ADDON_MANIFEST = ADDON_DIR / "manifest.json"
BUILD_WRAPPER = ADDON_DIR / "scripts" / "build.mjs"
DESKTOP_PKG = REPO / "desktop" / "package.json"
AFTER_PACK = REPO / "desktop" / "afterPack.js"
INSTALLER_PS1 = REPO / "desktop" / "build-installer.ps1"
MAIN_JS = REPO / "desktop" / "main.js"
RELEASE_YML = REPO / ".github" / "workflows" / "release.yml"
TESTS_YML = REPO / ".github" / "workflows" / "tests.yml"

#: The resources-relative directory the installer bundles the add-on into. One name,
#: written once here, and every pin below derives from it rather than repeating it.
ADDON_RESOURCE_DIR = "browser-addon"


def read_text(path: Path) -> str:
    """Read a source file with CRLF normalised AT THE READER.

    This repository is developed and released on Windows and checked out with native
    line endings, so every pattern below would need an alternation for `\\r\\n` if the
    normalisation happened anywhere else. Doing it here keeps the patterns free of
    embedded newlines, which is the house rule.
    """
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def desktop_build() -> dict:
    return json.loads(read_text(DESKTOP_PKG))["build"]


def addon_manifest() -> dict:
    return json.loads(read_text(ADDON_MANIFEST))


def extra_resource_targets() -> list[str]:
    """Every `to:` directory electron-builder copies into resources/."""
    return [entry["to"] for entry in desktop_build()["extraResources"]]


def glob_matches(path: str, pattern: str) -> bool:
    """Match the way electron-builder's filters do, not the way `fnmatch` does.

    electron-builder runs its `filter` patterns through minimatch, where `**` spans
    path separators and a single `*` does not. `fnmatch` makes no such distinction —
    its `*` swallows `/` — so using it here would call a filter of `["*"]` a match for
    everything and quietly stop being able to fail. Translating by hand is a few lines
    and keeps the assertion honest about the tool that actually does the copying.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), path) is not None


def addon_fileset() -> dict:
    """The extraResources entry that bundles the add-on, or fail naming what is lost."""
    for entry in desktop_build()["extraResources"]:
        if entry.get("to") == ADDON_RESOURCE_DIR:
            return entry
    raise AssertionError(
        "desktop/package.json build.extraResources has no entry with "
        f'to: "{ADDON_RESOURCE_DIR}" — the installer would carry no browser add-on, '
        "so a user without a source checkout has nothing to load unpacked (D27)."
    )


# --------------------------------------------------------------------------
# extraResources names the add-on, and names enough of it to be loadable
# --------------------------------------------------------------------------


def test_extraresources_bundles_the_addon_folder() -> None:
    """The installer copies extensions/chrome into resources/browser-addon."""
    entry = addon_fileset()
    assert entry["from"].replace("\\", "/").endswith("extensions/chrome"), (
        f"the {ADDON_RESOURCE_DIR} resource is copied from {entry['from']!r}, which is "
        "not the add-on source folder."
    )


def test_the_bundled_filter_carries_the_service_worker_the_manifest_names() -> None:
    """The filter must match the bundle Chrome registers, whatever it is called.

    The filter is an explicit include list, so it silently drops anything it does not
    name — and manifest.json is matched by `manifest.json` no matter what, which means
    a filter that forgot `dist/**` still ships a folder that LOOKS like an add-on.
    Deriving the worker's path from the manifest (rather than writing "dist/background
    .js" a second time here) means a rename on either side fails this.
    """
    worker = addon_manifest()["background"]["service_worker"]
    patterns = addon_fileset()["filter"]
    assert any(glob_matches(worker, pattern) for pattern in patterns), (
        f"extraResources filter {patterns} does not match {worker!r}, the service "
        "worker manifest.json declares. Chrome would refuse the bundled folder with "
        '"Service worker registration failed. Status code: 15", naming no file.'
    )
    assert any(glob_matches("manifest.json", p) for p in patterns), (
        f"extraResources filter {patterns} does not match manifest.json — Chrome "
        "cannot load a folder with no manifest at all."
    )


# --------------------------------------------------------------------------
# afterPack inventories it (integrity.js's whole reason to exist)
# --------------------------------------------------------------------------


def _inventory_list() -> list[str]:
    """The hard-coded subdirectory list afterPack.js hands integrity.buildManifest."""
    source = read_text(AFTER_PACK)
    match = re.search(r"buildManifest\(resourcesDir, \[([^\]]*)\], version\)", source)
    assert match, (
        "could not find the integrity.buildManifest(...) call in desktop/afterPack.js "
        "— this pin can no longer tell which bundled directories are inventoried."
    )
    return re.findall(r'"([^"]+)"', match.group(1))


def test_every_bundled_resource_directory_is_inventoried() -> None:
    """Nothing extraResources ships may be absent from the integrity manifest.

    This is the pin that matters most in the file, and it is deliberately written
    against the WHOLE list rather than the add-on alone: the next bundled directory,
    whatever it is, gets the same protection for free. A directory missing here is not
    recorded in install-manifest.json, so verifyManifest cannot report it missing —
    a truncated install then boots, crash-loops, and passes its own integrity check.
    """
    inventoried = set(_inventory_list())
    bundled = set(extra_resource_targets())
    missing = sorted(bundled - inventoried)
    assert not missing, (
        f"desktop/afterPack.js does not inventory {missing} — those directories are "
        "shipped by extraResources but absent from install-manifest.json, so a "
        "half-extracted install of them verifies as healthy at boot (integrity.js)."
    )


def test_afterpack_refuses_an_addon_folder_with_no_bundle_in_it() -> None:
    """A build that skipped the add-on build must fail the BUILD, not the user.

    extensions/chrome/dist is gitignored, so `manifest.json` present + `dist/` empty is
    a reachable state on any machine that did not run the build. electron-builder would
    ship it happily. afterPack has to be the one to say no, and it has to check a file
    INSIDE dist rather than the folder's existence.
    """
    source = read_text(AFTER_PACK)
    worker = addon_manifest()["background"]["service_worker"]
    leaf = worker.rsplit("/", 1)[-1]
    guard = re.search(r"const addonDir = [^;]+;(.*?)console\.log", source, re.S)
    assert guard, (
        "desktop/afterPack.js no longer guards the bundled add-on folder — a package "
        "with an empty dist/ would be built and published without complaint."
    )
    body = guard.group(1)
    assert "throw new Error" in body, (
        "the add-on guard in desktop/afterPack.js warns instead of throwing; a warning "
        "in a CI log is not a gate, and the installer would still be published."
    )
    assert leaf in body, (
        f"the add-on guard does not check {leaf!r} — checking only manifest.json "
        "accepts exactly the folder a skipped build produces."
    )


# --------------------------------------------------------------------------
# The build actually runs: locally, in the release script, and in both gates
# --------------------------------------------------------------------------


def test_local_installer_build_builds_the_addon_unconditionally() -> None:
    """A CI-only build step ships a LOCAL installer with no add-on inside it.

    The script already has -SkipDaemon and -SkipDashboard, so "guarded by a switch" is
    a shape this file could plausibly have grown. It must not: the daemon and the
    dashboard leave large, obviously-present artefacts behind, while a stale or absent
    dist/ is indistinguishable from a fresh one to everything downstream.
    """
    source = read_text(INSTALLER_PS1)
    call = re.search(r"^.*node scripts/build\.mjs.*$", source, re.M)
    assert call, (
        "desktop/build-installer.ps1 never runs extensions/chrome/scripts/build.mjs — "
        "a local `pnpm run dist:full` would produce an installer whose browser-addon "
        "folder has no service worker in it."
    )
    assert "Invoke-Native" in call.group(0), (
        "the add-on build is not wrapped in Invoke-Native, so PowerShell's "
        "NativeCommandError handling (EAP=Stop wraps native stderr) can abort the "
        "script on ordinary build chatter, or hide a real non-zero exit code."
    )
    stage = source[source.index("# 3c)") : source.index("# 4) Package")]
    assert "$Skip" not in stage, (
        "the add-on build stage is behind a -Skip switch. Skipping it produces an "
        "installer that looks complete and carries an unloadable add-on."
    )


def _steps_running_the_addon_build(job: dict) -> list[dict]:
    return [
        step
        for step in job.get("steps", [])
        if "scripts/build.mjs" in str(step.get("run", ""))
        and str(step.get("working-directory", "")).replace("\\", "/") == "extensions/chrome"
    ]


def test_the_release_gate_builds_the_addon_inside_the_suite_job() -> None:
    """The gate covers what the installer contains, or it is theatre.

    release.yml says so in its own comments, and it learned it the hard way twice: the
    suite and the installer once ran as separate concurrent workflows and a red suite
    published to the user's auto-updating daily driver. Building the add-on only in the
    `windows-installer` job would repeat the mistake in miniature — the add-on would be
    built for the first time at publish time, after the gate had already said yes.
    """
    workflow = yaml.safe_load(read_text(RELEASE_YML))
    suite = workflow["jobs"]["suite"]
    steps = _steps_running_the_addon_build(suite)
    assert steps, (
        "the `suite` job of .github/workflows/release.yml never builds the browser "
        "add-on, so the release gate does not cover a component the installer ships."
    )
    # Every other step in this job carries the publish gate; an ungated one would
    # spend CI minutes on every no-bump push to master, which the gate exists to avoid.
    for step in steps:
        assert "steps.gate.outputs.publish" in str(step.get("if", "")), (
            f"step {step.get('name')!r} in the release `suite` job is missing the "
            "publish gate its siblings all carry."
        )
    # And it must not ONLY be there: the installer job runs build-installer.ps1, which
    # runs the same wrapper, so the bundled folder is built from verified sources.
    installer = workflow["jobs"]["windows-installer"]
    assert any(
        "build-installer.ps1" in str(step.get("run", ""))
        for step in installer.get("steps", [])
    ), "the installer job no longer runs build-installer.ps1"


def test_the_push_gate_builds_the_addon_too() -> None:
    """tests.yml runs the same command, so a PR is red before a release ever is."""
    workflow = yaml.safe_load(read_text(TESTS_YML))
    running = {
        name: job
        for name, job in workflow["jobs"].items()
        if _steps_running_the_addon_build(job)
    }
    assert running, (
        ".github/workflows/tests.yml never builds the browser add-on. Its dist/ is "
        "gitignored and no pytest bundles it, so a broken add-on build would reach "
        "master and fail for the first time inside the release."
    )


def test_the_build_wrapper_verifies_the_pinned_extension_id() -> None:
    """The id check has to be REACHED by the release, not merely to exist.

    verify-id.mjs is the thing that stops manifest.json's `key` drifting from
    identity.PINNED_EXTENSION_ID — and a drift there gets the real add-on refused at
    /browser/ws with close 1008 while nothing in either log names an id mismatch. Both
    workflows and the installer script invoke the add-on build and nothing else, so if
    the wrapper stopped running the verifier, the check would still pass in isolation
    and be run by nobody.
    """
    source = read_text(BUILD_WRAPPER)
    assert re.search(r'join\(HERE, "verify-id\.mjs"\)', source), (
        "extensions/chrome/scripts/build.mjs no longer runs verify-id.mjs, so no gate "
        "checks the pinned extension id any more — the daemon's origin guard would "
        "refuse the shipped add-on with close 1008 and name nothing."
    )
    assert re.search(r'\[TSC, "--noEmit"\]', source), (
        "build.mjs no longer typechecks. esbuild strips types without checking them, "
        "so nothing else in the release would fail on a type error."
    )


def test_build_wrapper_produces_a_loadable_addon() -> None:
    """Actually run the wrapper, and confirm the folder Chrome would load is complete.

    The only test in this file that EXECUTES rather than reads. It skips where the
    add-on's Node dependencies are absent (CI's backend job installs none; the
    `browser-addon` job in tests.yml runs this same command for real), and the disk
    probe is here in the body rather than in a parametrize list so collection stays
    identical across xdist workers.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH; the browser-addon CI job covers this")
    if not (ADDON_DIR / "node_modules" / "typescript").exists():
        pytest.skip(
            "extensions/chrome dependencies are not installed; the browser-addon job "
            "in .github/workflows/tests.yml runs this same command on every push"
        )

    result = subprocess.run(  # noqa: S603 - fixed argv, repo-local script
        [node, str(BUILD_WRAPPER)],
        cwd=ADDON_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "extensions/chrome/scripts/build.mjs failed, so the release could not produce "
        f"a loadable add-on:\n{result.stdout}\n{result.stderr}"
    )

    manifest = addon_manifest()
    named = [
        manifest["background"]["service_worker"],
        manifest["side_panel"]["default_path"],
    ]
    for path in named:
        built = ADDON_DIR / path
        assert built.is_file() and built.stat().st_size > 0, (
            f"{path} is missing or empty after a successful build — the bundled "
            "add-on folder would not register its service worker."
        )


# --------------------------------------------------------------------------
# The runtime resolver: the same folder, found in both layouts
# --------------------------------------------------------------------------


def test_main_js_resolves_the_addon_in_both_the_frozen_and_the_dev_layout() -> None:
    """A packaged install and a checkout keep the add-on in different places.

    Packaged, it is `resources/browser-addon`. In a checkout it is `extensions/chrome`,
    and RES_DIR cannot stand in for that: in an unpackaged Electron run
    `process.resourcesPath` points at Electron's OWN resources directory, so a resolver
    that used RES_DIR unconditionally would name a folder that exists, is not the
    add-on, and holds no manifest — and the Browser page would then confidently tell
    the user to load a path Chrome rejects. That is the failure this pin exists for,
    and nothing else in the repository can see it: both layouts resolve to a real
    string, and only one of them resolves to a real add-on.

    Deliberately shape-based, not name-based. It asks for ONE declaration that decides
    between the two layouts and does not care what that constant is called, because
    `desktop/main.js` belongs to another lane. What it will not accept is a resolver
    that knows only one of the two locations.
    """
    source = read_text(MAIN_JS)
    declarations = re.findall(r"const\s+\w+\s*=([^;]*);", source)
    candidates = [
        expr for expr in declarations if ADDON_RESOURCE_DIR in expr and "extensions" in expr
    ]
    assert candidates, (
        "desktop/main.js has no single declaration naming both "
        f'"{ADDON_RESOURCE_DIR}" (the folder the installer bundles) and the '
        '"extensions" checkout path. Expected the shape the daemon and dashboard '
        "paths already use, e.g. `const BROWSER_ADDON_DIR = IS_PACKAGED ? "
        f'path.join(RES_DIR, "{ADDON_RESOURCE_DIR}") : path.join(REPO_ROOT, '
        '"extensions", "chrome");`. Without both branches the Browser page cannot '
        "name a real on-disk add-on folder in both layouts, which is the whole of D27 "
        "for a packaged install."
    )
    expr = candidates[0]
    assert re.search(r"IS_PACKAGED|isPackaged", expr), (
        "the add-on path declaration names both locations but nothing chooses between "
        "them on whether the app is packaged, so one of the two is dead code."
    )
    assert "RES_DIR" in expr or "resourcesPath" in expr, (
        "the packaged half does not resolve against the app's resources directory, so "
        "the bundled add-on would be looked for somewhere extraResources never copied it."
    )
    assert "chrome" in expr, (
        'the dev half names "extensions" but not the "chrome" add-on folder inside it.'
    )
