"""v1.239.0 — the add-on folder reaches the card as an ABSOLUTE PATH (D27, Ship 5).

The Ship 5 review found the last step of D27 broken, in three places at once: two
shipped user guides say the Browser page "names the exact folder on this machine
and gives you a button to copy it", and the card named the bare folder NAME
(``browser-addon``) and then pointed at the Overview's setup checks for the real
path — a block that renders nothing while the doctor is healthy, and
``browser_addon`` is RECOMMENDED, so a missing add-on does not make the doctor
unhealthy either. A user who ran the installer was sent looking for a folder
nobody had told them the location of, and Chrome's *Load unpacked* picker cannot
resolve a folder name.

The daemon knew the answer the whole time: ``desktop/main.js`` exports
``IRONJARVIS_BROWSER_ADDON_DIR`` and ``onboarding.doctor.browser_addon_dir()``
resolves it for the ``browser_addon`` row. Nothing carried it to the card. It is
now a field on ``GET /browser/status``, and this file drives that over the REAL
app — ``create_app``, install auth on, the real route — because the claim is about
what a user's install answers, and a status dict built by hand in a test would
have kept passing through the entire defect.

What is pinned here, and why each case can fail:

* **The field carries the resolved folder, absolutely.** Driven with the env
  override the desktop supervisor really sets, and asserted against
  ``str(folder)`` — not against a name, and not against "contains".
* **ONE resolver.** The route's answer is compared to ``doctor.browser_addon_dir()``
  AND to the folder the doctor's own ``browser_addon`` row names in its detail. A
  second implementation of "where is the add-on" is the one-definition failure this
  project keeps paying for; if the route ever grows its own, these two disagree.
* **Unresolvable is ``""``, never a guess.** Driven through the frozen layout with
  an empty ``resources`` — a packaged install whose add-on did not ship — because
  that is the install the honest fallback exists for.
* **The route still never fails.** The field is present when there is no browser
  runtime at all, and a resolver that RAISES degrades to ``""`` with a 200 rather
  than turning the card's poll into "daemon offline".

No assertion here measures elapsed time, and every stand-in takes ``*args, **kw``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.identity import PINNED_EXTENSION_KEY
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import browser as browser_routes
from iron_jarvis.onboarding import doctor as _doctor_pkg_attr  # noqa: F401  (see below)
from iron_jarvis.onboarding.doctor import (
    BROWSER_ADDON_ENV,
    browser_addon_dir,
    check_browser_addon,
)

# NOTE for the next reader: ``iron_jarvis.onboarding``'s package __init__ rebinds
# the name ``doctor`` from the MODULE to the ``doctor()`` FUNCTION, so
# ``from ...onboarding import doctor`` hands back a callable with no
# ``browser_addon_dir`` on it. That is exactly the trap the route's first cut fell
# into: the lookup raised AttributeError, the defensive ``except`` swallowed it,
# and the field was silently "" on every install. Import the function.

INSTALL_BEARER = "addon-path-install-bearer"
LOOPBACK_ORIGIN = "http://127.0.0.1:8788"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {INSTALL_BEARER}", "Origin": LOOPBACK_ORIGIN}


def _addon(root: Path, *, name: str = "browser-addon") -> Path:
    """A built add-on folder in the shape electron-builder ships."""
    folder = root / name
    (folder / "dist").mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "Iron Jarvis",
                "version": "1.239.0",
                "key": PINNED_EXTENSION_KEY,
                "background": {"service_worker": "dist/background.js", "type": "module"},
                "action": {"default_popup": "dist/popup.html"},
            }
        ),
        encoding="utf-8",
    )
    (folder / "dist" / "background.js").write_text("// built", encoding="utf-8")
    (folder / "dist" / "popup.html").write_text("<!doctype html>", encoding="utf-8")
    return folder


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The REAL app, with the install bearer the desktop app uses."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_BEARER)
    app = create_app(str(tmp_path / "home"))
    with TestClient(app) as c:
        yield c


def _status(client: TestClient) -> dict:
    r = client.get("/browser/status", headers=_headers())
    assert r.status_code == 200, r.text
    return r.json()


def test_status_carries_the_absolute_addon_folder(client, monkeypatch, tmp_path):
    """The field is the folder itself, character for character — not its name."""
    folder = _addon(tmp_path / "install" / "resources")
    monkeypatch.setenv(BROWSER_ADDON_ENV, str(folder))

    reported = _status(client)["addon_dir"]

    assert reported == str(folder), "the card must be able to paste this into Chrome"
    assert Path(reported).is_absolute(), reported
    assert reported != folder.name, "a folder NAME is not something a file picker resolves"
    assert (Path(reported) / "manifest.json").is_file(), "and it must be the folder Chrome loads"


def test_the_route_and_the_doctor_name_the_SAME_folder(client, monkeypatch, tmp_path):
    """One resolver. The card and the setup checks cannot send a user two places."""
    folder = _addon(tmp_path / "install" / "resources")
    monkeypatch.setenv(BROWSER_ADDON_ENV, str(folder))

    reported = _status(client)["addon_dir"]
    row = check_browser_addon()

    assert reported == str(browser_addon_dir())
    assert reported in row["detail"], (
        "the doctor's browser_addon row prints the folder it found; the status route "
        "must not have found a different one"
    )


def test_a_second_install_reports_its_own_folder(client, monkeypatch, tmp_path):
    """Nothing is baked in: a different install answers a different path."""
    first = _addon(tmp_path / "install-a" / "resources")
    monkeypatch.setenv(BROWSER_ADDON_ENV, str(first))
    assert _status(client)["addon_dir"] == str(first)

    second = _addon(tmp_path / "install-b" / "resources")
    monkeypatch.setenv(BROWSER_ADDON_ENV, str(second))
    assert _status(client)["addon_dir"] == str(second)


def test_a_packaged_install_with_no_addon_reports_an_empty_string(
    client, monkeypatch, tmp_path
):
    """The case the honest fallback exists for: shipped without the add-on.

    Driven through the FROZEN layout the resolver really walks — ``resources`` is
    the executable's grandparent — with nothing in it. The route must say "I do not
    know" rather than hand back a folder name the card would dress up as a path.
    """
    monkeypatch.delenv(BROWSER_ADDON_ENV, raising=False)
    resources = tmp_path / "install" / "resources"
    (resources / "daemon").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(resources / "daemon" / "ironjarvis.exe"))

    assert browser_addon_dir() is None, "the layout under test must really be add-on-less"
    assert _status(client)["addon_dir"] == ""


def test_the_field_is_there_when_there_is_no_browser_runtime_at_all(
    client, monkeypatch, tmp_path
):
    """The half-built install still needs the folder — that is how it gets fixed."""
    folder = _addon(tmp_path / "install" / "resources")
    monkeypatch.setenv(BROWSER_ADDON_ENV, str(folder))
    monkeypatch.setattr(client.app.state.platform, "browser", None, raising=False)

    body = _status(client)
    assert body["connected"] is False
    assert body["addon_dir"] == str(folder)


def test_a_resolver_that_raises_degrades_to_empty_and_still_answers_200(
    client, monkeypatch
):
    """``GET /browser/status`` is documented never to fail, and the card polls it."""

    def boom(*args, **kw):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(browser_routes, "browser_addon_dir", boom)

    body = _status(client)
    assert body["addon_dir"] == ""
    assert body["connected"] is False
