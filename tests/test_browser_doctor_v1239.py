"""v1.239.0 — the browser diagnostics, and whether they tell the truth (D25).

Ship 5 adds no capability. It adds the sentence a user reads when the browser
half of Iron Jarvis is not working, which means every claim in this file is about
HONESTY rather than behaviour:

* **The doctor covers what D25 lists** — service initialisation, the add-on build,
  the WebSocket route, pairing state, connection state, the pinned add-on id and
  the ``browser_access`` setting — split across two checks by what each one needs.
  ``check_browser_addon`` reads only the disk, so the offline CLI ``doctor`` (which
  has no platform) still answers "your add-on was never built"; ``check_browser_bridge``
  needs the running install and is appended by ``runtime_checks``.
* **RECOMMENDED, never REQUIRED, and never alarmist.** ``browser_access`` ships
  ``off`` and most installs never pair a browser, so "not paired", "not connected"
  and "access off" are STATE and are reported with ``ok`` true. A doctor that
  flagged a fresh install as broken teaches the user to ignore the doctor, and
  then the row that matters is ignored too. The pins here assert both directions:
  the honest rows stay green, and the genuinely-broken ones go red.
* **Unknown is neither.** A fact the bridge check could not READ is not the falsy
  default of the variable it failed to fill: a runtime whose reads all raise must
  not say "no browser is connected" (a claim about the user's browser derived from
  an exception) and must not render green with no remedy.
* **Evidence, not restatement.** The served-route row is derived from the app's own
  table, so one pin registers the real routes on an app that genuinely lacks
  ``/browser/ws`` — every other test either registers a complete app or hands the
  answer in, and both survive substituting the declared tuple for the read.
* **It never raises.** Driven against a platform with no ``browser`` attribute, a
  platform whose every property raises, and a half-built one — because the doctor
  is the thing a user runs when something is already wrong, and a doctor that
  crashes on a broken install is a doctor for installs that are fine.
* **``POST /browser/test`` is read-only, structurally.** Ship 1 pinned that the one
  method it sends is in ``READ_METHODS``. That pins the CURRENT edit; this file
  pins the next one, by driving a service whose ``active_tab`` sends a CLICK and
  asserting that nothing reaches the socket. Test is a button a worried user
  presses twice, and the browser on the other side is their real, signed-in one.
* **``GET /browser/status`` carries ``last_error``, and RETIRES it.** The Ship 1
  review found the field never cleared, so one routine ``TAB_NOT_FOUND`` left a
  permanent amber "Last problem" over a browser that works. Both halves are driven
  here over the real route: a real transport fault appears, and a successful round
  trip removes it.

Everything that is a claim about the PRODUCT is driven through ``create_app`` with
``IRONJARVIS_TOKEN`` set, over the real routes and a real peer on the real
``/browser/ws`` socket. Ship 4 shipped a route no real install could reach because
every test built a bare app; a doctor row is exactly the kind of claim that would
pass forever against a hand-built dict.

No assertion here measures elapsed time, every spy takes ``*args, **kw``, and the
source pins normalise CRLF at the reader.
"""

from __future__ import annotations

import base64
import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.identity import PINNED_EXTENSION_KEY, pinned_extension_id
from iron_jarvis.browser.service import BrowserRuntime
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import browser as browser_routes
from iron_jarvis.onboarding.doctor import (
    RECOMMENDED,
    REQUIRED,
    browser_addon_dir,
    check_browser,
    check_browser_addon,
    check_browser_bridge,
    doctor,
)

from ._fakes.browser_peer import BrowserPeer, FakeTab, ScriptedBrowser

#: The doctor MODULE, not the ``doctor`` function ``iron_jarvis.onboarding``
#: re-exports under the same name — ``from ... import doctor`` binds the function,
#: and monkeypatching an attribute on it would silently patch nothing.
doctor_mod = import_module("iron_jarvis.onboarding.doctor")

INSTALL_BEARER = "doctor-install-bearer"
LOOPBACK_ORIGIN = "http://127.0.0.1:8788"

#: A second, valid public key that is NOT the pinned one, so the id it derives to
#: differs. Any decodable base64 works: the derivation hashes the DER bytes.
OTHER_KEY = base64.b64encode(b"a different add-on's public key").decode("ascii")


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {INSTALL_BEARER}", "Origin": LOOPBACK_ORIGIN}


def _addon(root: Path, *, built: bool = True, key: str = PINNED_EXTENSION_KEY,
           name: str = "browser-addon", manifest_text: str | None = None) -> Path:
    """An add-on folder in the shape electron-builder ships, built or not."""
    folder = root / name
    (folder / "dist").mkdir(parents=True, exist_ok=True)
    if manifest_text is None:
        manifest_text = json.dumps(
            {
                "manifest_version": 3,
                "name": "Iron Jarvis",
                "version": "1.239.0",
                "key": key,
                "background": {"service_worker": "dist/background.js", "type": "module"},
                "side_panel": {"default_path": "dist/sidepanel.html"},
                "action": {"default_title": "Iron Jarvis"},
            }
        )
    (folder / "manifest.json").write_text(manifest_text, encoding="utf-8")
    if built:
        for rel in _built_files():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(
                "<!doctype html>" if rel.endswith(".html") else "// built", encoding="utf-8"
            )
    return folder


def _built_files() -> tuple[str, ...]:
    """Every file a BUILT add-on must carry, in the doctor's own terms.

    The manifest above names two; the other two are loaded at run time and named in
    no manifest field at all (``BROWSER_ADDON_RUNTIME_FILES``). Reading the second
    pair from the doctor rather than repeating it here is deliberate: this helper is
    what every "a built add-on is ready" claim below stands on, and a helper holding
    its own list would keep answering "built" for a folder the shipped check calls
    unbuilt.
    """
    return ("dist/background.js", "dist/sidepanel.html") + doctor_mod.BROWSER_ADDON_RUNTIME_FILES


def _point_at(monkeypatch: pytest.MonkeyPatch, folder: Path | str) -> None:
    monkeypatch.setenv(doctor_mod.BROWSER_ADDON_ENV, str(folder))


def _serve_nothing(bridge: "_Bridge", monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this install's route table read as empty, both ways it is recorded.

    ``register`` writes the served set onto the PLATFORM it registered for (so the
    doctor answers about the app serving this install, not about whichever app this
    process built last) and keeps the module global as the fallback for a caller
    with no platform. A test that patched only one of the two would be asserting
    against a value the check does not read.
    """
    monkeypatch.setattr(browser_routes, "_SERVED", frozenset())
    monkeypatch.setattr(bridge.platform, browser_routes.SERVED_ATTR, frozenset())


class _Bridge:
    """The REAL app, with install auth on, and a real add-on peer on demand.

    ``create_app`` because every claim below is about what a user's install
    reports: the doctor route, the browser routes and the platform's own
    ``BrowserRuntime`` are all the shipped ones, and the only stand-in is the
    Chrome that is not running.
    """

    def __init__(self, tmp_path: Path, *, access: str = "interactive") -> None:
        self.app = create_app(str(tmp_path))
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.platform = self.app.state.platform
        self.peer: BrowserPeer | None = None
        if access:
            r = self.client.put(
                "/settings", json={"values": {"browser_access": access}}, headers=_headers()
            )
            assert r.status_code == 200, r.text

    def close(self) -> None:
        if self.peer is not None:
            self.peer.close()
            self.peer = None
        self.client.__exit__(None, None, None)

    def connect_browser(self) -> BrowserPeer:
        page = ScriptedBrowser(
            [FakeTab(id=7, title="Example Domain", url="https://example.com", active=True)]
        )
        peer = BrowserPeer(self.client, page=page, headers=_headers())
        peer.__enter__()
        peer.pair()
        peer.expect_ready()
        self.peer = peer
        assert self.platform.browser.connected, "the peer paired but is not connected"
        return peer

    def doctor_rows(self) -> dict[str, dict]:
        r = self.client.get("/doctor", headers=_headers())
        assert r.status_code == 200, r.text
        return {row["name"]: row for row in r.json()["checks"]}

    def status(self) -> dict:
        r = self.client.get("/browser/status", headers=_headers())
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture()
def install_auth(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install-bearer auth ON — the shipped desktop configuration."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_BEARER)
    return INSTALL_BEARER


@pytest.fixture()
def bridge(install_auth, tmp_path):
    b = _Bridge(tmp_path)
    try:
        yield b
    finally:
        b.close()


# --------------------------------------------------------------------------- #
# 1. check_browser_addon: the add-on ships, is BUILT, and carries the pinned id
# --------------------------------------------------------------------------- #


def test_a_built_addon_is_reported_ready_and_names_the_folder(tmp_path, monkeypatch):
    """The row has to answer "which folder do I load?", because that is the step."""
    folder = _addon(tmp_path)
    _point_at(monkeypatch, folder)

    row = check_browser_addon()

    assert row["ok"] is True, row
    assert row["name"] == "browser_addon"
    assert row["level"] == RECOMMENDED, (
        "even the passing row must be RECOMMENDED: a REQUIRED browser row makes the "
        "whole doctor say 'broken' the moment the add-on is absent"
    )
    assert str(folder) in row["detail"], "the row does not name the folder to load"
    assert pinned_extension_id() in row["detail"]


def test_an_unbuilt_addon_is_reported_as_unloadable_and_names_the_missing_file(
    tmp_path, monkeypatch
):
    """THE Ship 5 failure. ``extensions/chrome/dist`` is gitignored and produced by
    the release, so a build that skipped that stage ships a folder whose manifest
    points at a service worker that is not there. Chrome refuses to register it and
    blames the add-on; without this row nothing in Iron Jarvis says a word."""
    folder = _addon(tmp_path, built=False)
    _point_at(monkeypatch, folder)

    row = check_browser_addon()

    assert row["ok"] is False, row
    assert "dist/background.js" in row["detail"], (
        "the row does not name what is missing, so the user cannot act on it"
    )
    assert "not built" in row["detail"]
    assert "pairing can never start" in row["detail"], (
        "the row says it failed but not what is LOST (check_guide_docs house style)"
    )
    assert "pnpm" in row["fix"] and "extensions/chrome" in row["fix"]


def test_an_addon_missing_the_runtime_loaded_content_script_is_not_reported_ready(
    tmp_path, monkeypatch
):
    """The build hole this check could not see. ``manifest.json`` names the service
    worker and the side panel and NOTHING else -- there is no ``content_scripts`` block by
    design -- so a ``dist/`` that lost ``content.js`` used to be "built and ready to
    load" while every ``read_page`` failed with a bare injection error, which is
    precisely the silently-blamed add-on this row exists to end.

    Driven for BOTH runtime-loaded files, off the doctor's own list, so a third one
    added later is covered without anyone remembering this test.
    """
    for rel in doctor_mod.BROWSER_ADDON_RUNTIME_FILES:
        folder = _addon(tmp_path / rel.replace("/", "_"))
        (folder / rel).unlink()
        _point_at(monkeypatch, folder)

        row = check_browser_addon()

        assert row["ok"] is False, f"{rel} is missing and the add-on is called ready: {row}"
        assert rel in row["detail"], f"the row does not name {rel}"
        assert "not built" in row["detail"]
        assert row["fix"], "an unloadable add-on is reported with no remedy"


def test_the_runtime_loaded_files_are_the_ones_the_addon_sources_name():
    """The doctor's list and the build's list are ONE list, held apart by a test.

    A packaged install ships ``manifest.json`` + ``dist/**`` and no ``src/``, so the
    check cannot read these names at run time the way
    ``extensions/chrome/scripts/build.mjs`` does. What keeps the copy honest is this
    pin: it reads the two constants out of the add-on's own sources with the SAME
    regexes the build verifier uses, so a rename fails here as well as there instead
    of quietly widening the blind spot again.

    CRLF is normalised at the reader -- this repository is checked out with native
    line endings on Windows and with LF on CI.
    """
    import re

    addon = Path(__file__).resolve().parents[1] / "extensions" / "chrome"
    sources = {
        "src/background/tabs.ts": r'CONTENT_SCRIPT_FILE = "([^"]+)"',
        "src/background/hostperms.ts": r'SETUP_PAGE = "([^"]+)"',
    }
    named: list[str] = []
    for rel, pattern in sources.items():
        text = (addon / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        match = re.search(pattern, text)
        assert match, f"{rel} no longer names its bundle the way the build reads it"
        named.append(match.group(1))

    assert sorted(doctor_mod.BROWSER_ADDON_RUNTIME_FILES) == sorted(named), (
        "the doctor checks for files the add-on no longer loads, or misses one it does"
    )


def test_an_addon_whose_key_drifted_is_reported_with_both_ids(tmp_path, monkeypatch):
    """D27A's total, silent failure: the add-on loads, connects, and is refused at
    /browser/ws with 1008 by the origin allowlist, while the card waits to pair
    forever and the daemon log names nothing."""
    folder = _addon(tmp_path, key=OTHER_KEY)
    _point_at(monkeypatch, folder)

    row = check_browser_addon()

    assert row["ok"] is False, row
    assert pinned_extension_id() in row["detail"], "the row does not name the id the daemon admits"
    assert "/browser/ws" in row["detail"]
    assert "wait to pair forever" in row["detail"]


def test_an_addon_with_no_key_is_refused_rather_than_trusted(tmp_path, monkeypatch):
    """No ``key`` means Chrome invents an id per machine — the exact thing D27A
    forbids ("do not leave extension ID stability to developer machine state")."""
    manifest = json.dumps(
        {
            "manifest_version": 3,
            "background": {"service_worker": "dist/background.js"},
            "side_panel": {"default_path": "dist/sidepanel.html"},
        }
    )
    folder = _addon(tmp_path, manifest_text=manifest)
    _point_at(monkeypatch, folder)

    row = check_browser_addon()

    assert row["ok"] is False, row
    assert "random id" in row["detail"]


def test_an_overridden_pinned_id_is_named_as_an_override(tmp_path, monkeypatch):
    """The pinned id is configurable, and an override left in the environment by an
    earlier experiment narrows the origin allowlist to an add-on the user is not
    running: every connection refused with 1008, and nothing anywhere saying why.
    So when the id in force is not the built-in one, the row says where it came
    from -- otherwise the remedy ("reinstall") is aimed at the wrong thing."""
    from iron_jarvis.browser.identity import EXTENSION_ID_ENV

    _point_at(monkeypatch, _addon(tmp_path))
    monkeypatch.setenv(EXTENSION_ID_ENV, "a" * 32)

    row = check_browser_addon()

    assert row["ok"] is False, row
    assert "a" * 32 in row["detail"], "the row does not name the id actually in force"
    assert EXTENSION_ID_ENV in row["detail"], (
        "the row blames the add-on for an id that came from the environment"
    )


def test_a_missing_addon_folder_is_named_as_missing_not_as_unbuilt(tmp_path, monkeypatch):
    """The two failures have different remedies, so they must not share a sentence."""
    monkeypatch.setenv(doctor_mod.BROWSER_ADDON_ENV, str(tmp_path / "nowhere"))
    monkeypatch.setattr(doctor_mod, "browser_addon_dir", lambda: None)

    row = check_browser_addon()

    assert row["ok"] is False, row
    assert "not in this install" in row["detail"]
    assert "Reinstall" in row["fix"]


def test_the_addon_check_never_raises_on_a_broken_layout(tmp_path, monkeypatch):
    """Garbage on disk is a diagnosis, not a traceback: a corrupt manifest, a
    manifest that is not an object, and a key that is not a public key at all."""
    for label, text in (
        ("truncated", "{not json"),
        ("not an object", "[]"),
        ("unusable key", json.dumps({"key": "%%%%", "background": {"service_worker": "d.js"}})),
    ):
        folder = _addon(tmp_path / label, manifest_text=text)
        _point_at(monkeypatch, folder)
        row = check_browser_addon()
        assert row["ok"] is False, (label, row)
        assert row["level"] == RECOMMENDED, label
        assert isinstance(row["detail"], str) and row["detail"], label


def test_the_addon_row_is_recommended_so_a_missing_addon_is_not_a_broken_install(
    tmp_path, monkeypatch
):
    """RECOMMENDED, never REQUIRED — driven through the whole ``doctor()``, because
    the level only matters through the summary it feeds. A user who has never
    loaded the add-on must not be told their install is broken."""
    _point_at(monkeypatch, _addon(tmp_path, built=False))

    result = doctor()

    row = next(c for c in result["checks"] if c["name"] == "browser_addon")
    assert row["ok"] is False, "the fixture is unbuilt; this test proves nothing if it passes"
    assert row["level"] == RECOMMENDED
    assert result["ok"] is True, (
        "an add-on that was never built made the whole doctor report a broken install"
    )
    assert not any(
        c["name"].startswith("browser") and c.get("level") == REQUIRED for c in result["checks"]
    ), "a browser row is REQUIRED; a machine with no browser add-on is not broken"


def test_the_addon_check_does_not_collide_with_the_chrome_check(tmp_path, monkeypatch):
    """``check_browser`` answers "is Chrome installed"; this one answers "did the
    add-on ship". Two questions, two rows, two names — a collision would silently
    replace one answer with the other in every rendering of the doctor."""
    _point_at(monkeypatch, _addon(tmp_path))

    names = [c["name"] for c in doctor()["checks"]]

    assert check_browser()["name"] == "browser"
    assert check_browser_addon()["name"] == "browser_addon"
    assert names.count("browser") == 1 and names.count("browser_addon") == 1
    assert check_browser_addon in doctor_mod.CHECKS, "the check is defined but never runs"


# --------------------------------------------------------------------------- #
# 2. browser_addon_dir: the frozen layout and the dev layout
# --------------------------------------------------------------------------- #


def test_the_resolver_finds_the_addon_beside_the_frozen_daemon(tmp_path, monkeypatch):
    """PACKAGED. electron-builder puts the add-on under ``resources`` beside the
    frozen daemon; the daemon's only landmark is its own executable path."""
    resources = tmp_path / "resources"
    (resources / "daemon").mkdir(parents=True)
    folder = _addon(resources)
    monkeypatch.delenv(doctor_mod.BROWSER_ADDON_ENV, raising=False)
    monkeypatch.setattr(doctor_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        doctor_mod.sys, "executable", str(resources / "daemon" / "ironjarvis.exe")
    )

    assert browser_addon_dir() == folder


def test_the_resolver_finds_a_renamed_resource_folder_by_its_manifest(tmp_path, monkeypatch):
    """The folder is identified by its ``manifest.json``, not by the name someone
    chose in ``desktop/package.json`` — a renamed extraResource must not make the
    doctor announce a missing add-on that is sitting right there."""
    resources = tmp_path / "resources"
    (resources / "daemon").mkdir(parents=True)
    folder = _addon(resources, name="chrome-add-on")
    monkeypatch.delenv(doctor_mod.BROWSER_ADDON_ENV, raising=False)
    monkeypatch.setattr(doctor_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        doctor_mod.sys, "executable", str(resources / "daemon" / "ironjarvis.exe")
    )

    assert browser_addon_dir() == folder


def test_a_frozen_install_with_no_addon_resolves_to_nothing(tmp_path, monkeypatch):
    """The half-installed bundle: resources exist, the add-on does not."""
    resources = tmp_path / "resources"
    (resources / "daemon").mkdir(parents=True)
    monkeypatch.delenv(doctor_mod.BROWSER_ADDON_ENV, raising=False)
    monkeypatch.setattr(doctor_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        doctor_mod.sys, "executable", str(resources / "daemon" / "ironjarvis.exe")
    )

    assert browser_addon_dir() is None


def test_the_dev_layout_resolves_to_the_repository_folder(monkeypatch):
    """DEV. The folder the add-on README and the Your browser card both name."""
    monkeypatch.delenv(doctor_mod.BROWSER_ADDON_ENV, raising=False)
    monkeypatch.setattr(doctor_mod.sys, "frozen", False, raising=False)

    folder = browser_addon_dir()

    assert folder is not None
    assert folder.name == "chrome" and folder.parent.name == "extensions"
    assert (folder / "manifest.json").is_file()


def test_an_override_that_names_nothing_falls_back_instead_of_failing(tmp_path, monkeypatch):
    """A stale env var must not make a working install report a missing add-on."""
    monkeypatch.setenv(doctor_mod.BROWSER_ADDON_ENV, str(tmp_path / "gone"))
    monkeypatch.setattr(doctor_mod.sys, "frozen", False, raising=False)

    folder = browser_addon_dir()

    assert folder is not None and folder.name == "chrome"


# --------------------------------------------------------------------------- #
# 3. check_browser_bridge: the live half, and what it refuses to call broken
# --------------------------------------------------------------------------- #


class _Exploding:
    """A platform whose every attribute raises — a boot that failed halfway."""

    @property
    def browser(self) -> Any:
        raise RuntimeError("platform is still building")


class _AngryRuntime:
    """A browser runtime that raises on everything the check reads."""

    @property
    def backend(self) -> Any:
        raise RuntimeError("no transport")

    @property
    def pairing(self) -> Any:
        raise RuntimeError("no store")

    def access(self) -> str:
        raise RuntimeError("no config")


def test_the_bridge_check_never_raises_on_a_platform_that_is_broken(bridge):
    """Three shapes, all real: no ``browser`` attribute at all (the field the
    coordinator added does not exist in this build), a platform mid-boot whose
    property raises, and a runtime that answers every question with an exception.
    ``getattr(..., None)`` only swallows AttributeError, so the middle one is the
    case a careful-looking check still crashes on."""
    for label, platform in (
        ("no attribute", SimpleNamespace()),
        ("mid-boot", _Exploding()),
        ("angry runtime", SimpleNamespace(browser=_AngryRuntime())),
        ("null runtime", SimpleNamespace(browser=None)),
    ):
        row = check_browser_bridge(platform)
        assert row["name"] == "browser_bridge", label
        assert row["level"] == RECOMMENDED, label
        assert isinstance(row["detail"], str) and row["detail"], label


def test_a_missing_browser_service_is_reported_as_the_capability_being_gone():
    """Service initialisation (D25). No runtime means every ``browser_*`` tool is
    absent and the card can pair nothing — the row has to say that, because the
    only other symptom is a model that claims it cannot see your browser."""
    row = check_browser_bridge(SimpleNamespace(browser=None))

    assert row["ok"] is False
    assert "did not initialise" in row["detail"]
    assert "every browser tool is missing" in row["detail"]


def test_unregistered_routes_are_reported_rather_than_assumed(bridge, monkeypatch):
    """The WebSocket route (D25), answered from what was ACTUALLY registered on the
    app. Asserting the constant tuple instead would report a served socket in a
    build where the ``_routes.browser.register`` call had been deleted — the row
    would be green precisely when the add-on has nowhere to connect."""
    _serve_nothing(bridge, monkeypatch)

    row = check_browser_bridge(bridge.platform)

    assert row["ok"] is False, row
    assert "/browser/ws" in row["detail"]
    assert "nowhere to connect" in row["detail"]


def test_a_fresh_install_with_no_browser_is_reported_but_not_called_broken(bridge):
    """The honesty pin. ``browser_access`` ships ``off`` and most installs never
    pair anything, so these states are named in the detail with ``ok`` TRUE. A row
    that went amber here would go amber on every install that does not use the
    feature, and a user who learns to ignore one row ignores them all."""
    r = bridge.client.put(
        "/settings", json={"values": {"browser_access": "off"}}, headers=_headers()
    )
    assert r.status_code == 200, r.text

    row = check_browser_bridge(bridge.platform)

    assert row["ok"] is True, row
    assert row["level"] == RECOMMENDED
    assert "browser access is off" in row["detail"], "the setting is not reported at all"
    assert "no browser is paired" in row["detail"], "pairing state is not reported"
    assert "no browser is connected" in row["detail"], "connection state is not reported"
    assert "/browser/ws" in row["detail"], "the socket's path is not reported"


def test_a_paired_and_connected_browser_is_reported_as_such(bridge):
    """Driven over the real socket: a real pairing record and a real connection."""
    bridge.connect_browser()

    row = check_browser_bridge(bridge.platform)

    assert row["ok"] is True, row
    assert "a browser is paired" in row["detail"]
    assert "a browser is connected" in row["detail"]
    assert "browser access is interactive" in row["detail"]


def test_a_paired_browser_that_went_wrong_is_the_case_that_goes_amber(bridge):
    """The one state where the user BELIEVES they have a working browser and does
    not: the pairing survives, the socket is gone, and the transport is holding an
    unretired fault. Everything else is reported without alarm."""
    bridge.connect_browser()
    bridge.peer.close()
    bridge.peer = None
    bridge.platform.browser.backend.last_error = "active_tab timed out after 15s"

    row = check_browser_bridge(bridge.platform)

    assert row["ok"] is False, row
    assert row["level"] == RECOMMENDED, "an unreachable browser is still not a broken install"
    assert "active_tab timed out" in row["detail"], "the row hides what actually went wrong"
    assert "Forget" in row["fix"]


def test_a_bridge_nobody_could_read_is_not_reported_green_or_as_disconnected(bridge):
    """Unknown is not a negative, and it is not ok either.

    A runtime whose every question raises (an unreadable config store is the real
    one -- ``access()`` reads config) used to render GREEN, with no remedy, and with
    the sentence "no browser is connected" in it: a claim about the user's browser
    derived from an exception, because ``view`` was ``{}`` and the falsy default was
    read as a fact. The pairing branch already stayed silent in the same situation,
    so the asymmetry was never deliberate.

    The runtime is the install's real one with its three reads broken, and the row is
    read through the real ``/doctor`` route, so this is the row a user would see.
    """
    runtime = bridge.platform.browser

    class _Unreadable(type(runtime)):  # type: ignore[misc]
        def access(self) -> str:
            raise RuntimeError("no config")

        @property
        def pairing(self) -> Any:
            raise RuntimeError("no store")

        @property
        def backend(self) -> Any:
            raise RuntimeError("no transport")

    original = runtime.__class__
    runtime.__class__ = _Unreadable
    try:
        row = bridge.doctor_rows()["browser_bridge"]
    finally:
        runtime.__class__ = original

    assert "no browser is connected" not in row["detail"], (
        "the row states a connection fact it read nothing to support"
    )
    assert "a browser is connected" not in row["detail"]
    assert "the transport could not report its state" in row["detail"]
    assert row["ok"] is False, f"a bridge nobody could read renders green: {row}"
    assert row["fix"], "a red row with no remedy leaves the user nowhere to go"
    assert row["level"] == RECOMMENDED, "an unreadable browser is not a broken install"


class _PartialApp:
    """An app whose route table is genuinely missing one path.

    Not a monkeypatch of the served set: the property under test is that
    ``register`` DERIVES what it reports from the app's own routes, and a test that
    hands the answer in cannot tell a derivation from a constant. So this records
    every decorated path except the one it was told to drop, exactly as a build
    where a ``register`` call was deleted or a route renamed would.
    """

    def __init__(self, missing: str) -> None:
        self._missing = missing
        self.routes: list[Any] = []

    def _record(self, path: str):
        def decorate(fn):
            if path != self._missing:
                self.routes.append(SimpleNamespace(path=path))
            return fn

        return decorate

    def websocket(self, path: str, *args: Any, **kw: Any):
        return self._record(path)

    def get(self, path: str, *args: Any, **kw: Any):
        return self._record(path)

    def post(self, path: str, *args: Any, **kw: Any):
        return self._record(path)


def test_a_path_that_never_landed_is_reported_missing_by_the_row(bridge, monkeypatch):
    """``served_paths()`` is EVIDENCE, and this is the pin that can tell.

    Every other test here either registers a complete app (so the derivation and the
    declared tuple agree) or patches the served set directly (so the derivation never
    runs). Both stay green if ``register`` is changed to
    ``_SERVED = frozenset(SERVED_PATHS)`` -- which is the exact regression the row
    exists to catch, and would have the doctor promise a served socket in a build
    where the add-on has nowhere to connect.

    So: register the real routes on an app that genuinely does not carry
    ``/browser/ws``, against its own platform, and read the row for THAT platform.
    """
    monkeypatch.setattr(browser_routes, "_SERVED", browser_routes._SERVED)  # restored after
    partial = _PartialApp(missing=browser_routes.WS_PATH)
    platform = SimpleNamespace(browser=bridge.platform.browser)

    browser_routes.register(partial, SimpleNamespace(platform=platform))

    assert browser_routes.WS_PATH not in browser_routes.served_paths(platform), (
        "the socket was never registered and served_paths() claims it anyway"
    )
    row = check_browser_bridge(platform)
    assert row["ok"] is False, row
    assert browser_routes.WS_PATH in row["detail"]
    assert "nowhere to connect" in row["detail"]
    assert browser_routes.WS_PATH in browser_routes.served_paths(bridge.platform), (
        "a second app in this process rewrote the real install's answer"
    )


# --------------------------------------------------------------------------- #
# 4. The rows a real install renders
# --------------------------------------------------------------------------- #


def test_the_doctor_route_of_a_real_install_carries_both_browser_rows(bridge):
    """GET /doctor on the app the user runs, with the install bearer set. A row
    that only appears when a test calls the function directly is a row no user
    ever sees."""
    rows = bridge.doctor_rows()

    assert "browser_addon" in rows, "the add-on row never reaches the doctor route"
    assert "browser_bridge" in rows, "the bridge row never reaches the doctor route"
    assert rows["browser_bridge"]["level"] == RECOMMENDED
    assert rows["browser_addon"]["level"] == RECOMMENDED
    assert "/browser/ws" in rows["browser_bridge"]["detail"], (
        "the running app's own routes are not being read"
    )


def test_the_browser_rows_never_gate_the_doctors_verdict(bridge, monkeypatch):
    """Both rows red at once, on a real install: the summary still says ok."""
    monkeypatch.setattr(doctor_mod, "browser_addon_dir", lambda: None)
    _serve_nothing(bridge, monkeypatch)

    result = doctor(bridge.platform)
    rows = {c["name"]: c for c in result["checks"]}

    assert rows["browser_addon"]["ok"] is False
    assert rows["browser_bridge"]["ok"] is False
    assert result["ok"] is True, "a browser problem reported the whole install as broken"


def test_the_registered_paths_are_read_from_the_app_not_from_the_tuple(bridge):
    """``served_paths()`` is evidence, not a restatement: every declared path is
    matched against the app's own route table."""
    served = browser_routes.served_paths()

    assert set(browser_routes.SERVED_PATHS) == set(served), sorted(
        set(browser_routes.SERVED_PATHS) - set(served)
    )
    live = {getattr(route, "path", "") for route in bridge.app.routes}
    assert set(served) <= live, "served_paths() names a path the app does not serve"


# --------------------------------------------------------------------------- #
# 5. POST /browser/test is read-only — for the NEXT edit as well
# --------------------------------------------------------------------------- #


def test_test_sends_only_a_read_method_over_the_real_socket(bridge):
    """What actually crossed the wire, from a real peer on the real route."""
    peer = bridge.connect_browser()

    body = bridge.client.post("/browser/test", headers=_headers()).json()

    assert body["ok"] is True, body
    assert [method for method, _ in peer.commands] == [browser_routes.TEST_METHOD]
    assert set(method for method, _ in peer.commands) <= set(P.READ_METHODS), peer.commands
    assert body["active_tab"]["title"] == "Example Domain"
    assert isinstance(body["round_trip_ms"], int)


def test_a_mutating_round_trip_is_refused_before_it_reaches_the_browser(bridge):
    """THE hardening pin. Ship 1 asserted the method the route sends TODAY; this
    drives a service whose ``active_tab`` sends a CLICK — the shape a careless
    later edit takes — and asserts the frame never leaves the daemon.

    The service is subclassed rather than mocked so everything else on the path is
    the real thing: the real access gate, the real transport, the real peer. What
    the route contributes is the read-only view, and without it this click lands in
    the user's signed-in browser and the diagnostic reports success.
    """
    peer = bridge.connect_browser()

    class _MutatingRuntime(BrowserRuntime):
        async def active_tab(self) -> dict[str, Any] | None:
            self.require("read_only")
            return await self.backend.command(P.METHOD_CLICK, {"element_id": "e1"})

    bridge.platform.browser.__class__ = _MutatingRuntime
    try:
        body = bridge.client.post("/browser/test", headers=_headers()).json()
    finally:
        bridge.platform.browser.__class__ = BrowserRuntime

    assert body["ok"] is False, body
    assert peer.commands == [], (
        f"a diagnostic sent {peer.commands} to the user's real browser"
    )
    assert P.METHOD_CLICK in body["detail"], "the refusal does not name what it refused"
    assert "read-only" in body["detail"]


def test_a_service_that_cannot_be_diagnosed_read_only_is_refused_not_called(bridge):
    """Fail closed. A runtime carrying no class-level ``active_tab`` cannot be bound
    to the read-only view, so it is not driven at all — calling the instance's own
    attribute instead would be exactly the unguarded path the view exists to close,
    and the route would be back to trusting whatever it was handed."""
    peer = bridge.connect_browser()

    class _Bare(BrowserRuntime):
        active_tab = None  # type: ignore[assignment]

    bridge.platform.browser.__class__ = _Bare
    try:
        body = bridge.client.post("/browser/test", headers=_headers()).json()
    finally:
        bridge.platform.browser.__class__ = BrowserRuntime

    assert body["ok"] is False, body
    assert "read-only" in body["detail"]
    assert peer.commands == [], f"an unguarded path sent {peer.commands}"


def test_a_round_trip_that_delegates_is_still_read_only(bridge):
    """The view is total, and one level deep was not enough.

    A forwarding ``__getattr__`` hands back the attribute off the REAL runtime, so a
    method reached THROUGH the view is bound to the real runtime and its
    ``self.backend`` is the real transport. The guarantee then held only for a direct
    ``self.backend.command`` inside ``active_tab`` itself -- and the likeliest next
    edit is the other shape: ``active_tab`` delegating to a sibling (the real
    ``resolve_page_tab`` is one) that talks to the browser itself.

    Driven over the real socket with a real peer, so what is asserted is what
    crossed the wire.
    """
    peer = bridge.connect_browser()

    class _DelegatingRuntime(BrowserRuntime):
        async def active_tab(self) -> dict[str, Any] | None:
            return await self.peek()

        async def peek(self) -> dict[str, Any]:
            self.require("read_only")
            return await self.backend.command(P.METHOD_CLICK, {"element_id": "e1"})

    bridge.platform.browser.__class__ = _DelegatingRuntime
    try:
        body = bridge.client.post("/browser/test", headers=_headers()).json()
    finally:
        bridge.platform.browser.__class__ = BrowserRuntime

    assert peer.commands == [], (
        f"a delegated diagnostic sent {peer.commands} to the user's real browser"
    )
    assert body["ok"] is False, body
    assert P.METHOD_CLICK in body["detail"], "the refusal does not name what it refused"


def test_a_directive_never_leaves_the_daemon_from_test(bridge):
    """``command`` is not the only way to a frame. ``directive`` sends one too, and
    ``disconnect`` would drop the very browser Test was pressed to measure -- so the
    view refuses every directive rather than gating one call and leaving its sibling
    wide open."""
    peer = bridge.connect_browser()

    class _DirectiveRuntime(BrowserRuntime):
        async def active_tab(self) -> dict[str, Any] | None:
            self.require("read_only")
            return await self.backend.directive(P.DIRECTIVE_DISCONNECT)

    bridge.platform.browser.__class__ = _DirectiveRuntime
    try:
        body = bridge.client.post("/browser/test", headers=_headers()).json()
    finally:
        bridge.platform.browser.__class__ = BrowserRuntime

    assert body["ok"] is False, body
    assert P.DIRECTIVE_DISCONNECT in body["detail"]
    assert bridge.platform.browser.connected, (
        "a diagnostic disconnected the browser it was called to test"
    )
    assert peer.commands == []


def test_the_view_refuses_a_transport_attribute_no_read_path_uses(bridge):
    """The refusal is an ALLOWLIST, so the seam added NEXT is closed by default.

    ``_await_response`` is the shared frame-sending core behind both ``command`` and
    ``directive``; ``deliver_pairing`` and ``release`` reach the socket too, and
    ``connection`` hands out the socket itself. A denylist would admit each new one
    by default. Note the refusal is a ``BrowserError`` and not an
    ``AttributeError``: ``BrowserRuntime.connected`` is a
    ``getattr(self.backend, "connected", False)``, so an AttributeError-shaped
    refusal would be swallowed into a plausible ``False``.
    """
    view = browser_routes._ReadOnlyTransport(bridge.platform.browser.backend)

    for name in ("_await_response", "deliver_pairing", "release", "connection"):
        with pytest.raises(BrowserError) as caught:
            getattr(view, name)
        assert name in str(caught.value), "the refusal does not name what it refused"

    assert view.status() is not None, "the cached transport view is a read"
    assert view.connected is bridge.platform.browser.backend.connected


async def test_the_read_only_view_refuses_every_method_outside_the_read_set():
    """The view's own rule, over the whole protocol: every READ method passes
    through and every LOCAL_UI and PAGE_ACTION method is refused. Driven over the
    protocol's own lists rather than a sample, so a method added to either later is
    covered without anyone remembering this file."""

    class _Recorder:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def command(self, method, *args, **kw):  # a spy takes *args/**kw
            self.sent.append(method)
            return {}

    recorder = _Recorder()
    view = browser_routes._ReadOnlyTransport(recorder)
    refused: list[str] = []
    for method in P.ALL_METHODS:
        try:
            await view.command(method)
        except BrowserError as exc:
            assert exc.code == BrowserErrorCode.EXTENSION_ERROR.value
            refused.append(method)

    assert set(recorder.sent) == set(P.READ_METHODS)
    assert set(refused) == set(P.LOCAL_UI_METHODS) | set(P.PAGE_ACTION_METHODS)
    assert view.sent == recorder.sent, "the view does not record what it let through"


# --------------------------------------------------------------------------- #
# 6. GET /browser/status carries last_error, and RETIRES it
# --------------------------------------------------------------------------- #


def test_status_reports_the_transports_last_problem(bridge):
    """The field the card renders as an amber "Last problem". Ship 1's review found
    it never cleared; both halves are driven here, over the real route."""
    bridge.connect_browser()
    assert bridge.status()["last_error"] is None

    bridge.platform.browser.backend.last_error = "your browser sent an unknown frame type 'x'"

    assert bridge.status()["last_error"] == "your browser sent an unknown frame type 'x'"


def test_a_successful_round_trip_retires_the_last_problem(bridge):
    """One routine fault used to leave a permanent problem banner over a browser
    that works. A delivered answer means the last thing that went wrong is no
    longer true, so Test — the button the user presses to find out — clears it."""
    bridge.connect_browser()
    bridge.platform.browser.backend.last_error = "active_tab timed out after 15s"
    assert bridge.status()["last_error"]

    body = bridge.client.post("/browser/test", headers=_headers()).json()

    assert body["ok"] is True, body
    assert bridge.status()["last_error"] is None, (
        "a successful round trip left the amber banner up over a working browser"
    )


def test_a_disconnected_install_still_answers_status_with_the_field(install_auth, tmp_path):
    """``GET /browser/status`` never fails, and the shape does not change with the
    state: the card reads ``last_error`` whether or not a browser was ever paired."""
    b = _Bridge(tmp_path, access="")
    try:
        body = b.status()
    finally:
        b.close()

    assert body["connected"] is False
    assert "last_error" in body and body["last_error"] is None
