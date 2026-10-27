"""v1.400.6 — the browser add-on wears the app's own icon.

Before this, ``extensions/chrome/manifest.json`` named no icon at all, so Chrome and
Edge drew a grey letter "I" for "Iron Jarvis" in the toolbar, the extensions menu,
the side panel's header and the extensions page. Now every one of those draws the
user's "iU" tile — the same picture as the desktop app (v1.380.0).

What is pinned, and why each is a claim about the PRODUCT:

* the manifest names 16/32/48/128 in ``icons`` and 16/32 in ``action.default_icon``,
  all under ``dist/`` — the only part of the add-on folder the installer ships
  (``desktop/package.json`` extraResources filter);
* each committed icon IS the app icon at that size (pixel-for-pixel what
  ``scripts/make_addon_icons.py`` makes from ``desktop/assets/icon.png``, and the
  same pixels as the matching frame of ``icon.ico`` that Windows shows), so a new
  app icon cannot leave the add-on wearing the old one;
* the bundler really copies them into ``dist/icons`` (run, not read), and the two
  pages that open in a tab point at a file that is there;
* a folder missing an icon is NOT called ready by the doctor: Chrome refuses the
  whole unpacked folder over one missing icon ("Could not load icon"), which is the
  same total, silent failure the doctor's add-on row exists to name. The release
  build's verifier (``extensions/chrome/scripts/build.mjs``) checks the same files;
  that was mutation-checked by hand (a deleted ``icon-48.png`` fails the build at
  "verify", naming ``manifest.json icons["48"]``).

Source reads normalise CRLF at the reader.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome"
APP_ICO = ROOT / "desktop" / "assets" / "icon.ico"
SIZES = (16, 32, 48, 128)

#: The doctor MODULE (``iron_jarvis.onboarding`` re-exports a ``doctor`` FUNCTION).
doctor_mod = import_module("iron_jarvis.onboarding.doctor")


def _manifest() -> dict:
    return json.loads((ADDON / "manifest.json").read_text(encoding="utf-8"))


def _icons_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return import_module("make_addon_icons")
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def _named_icons(manifest: dict) -> list[str]:
    files = list(manifest.get("icons", {}).values())
    files += [f for f in manifest.get("action", {}).get("default_icon", {}).values() if f not in files]
    return files


def test_the_manifest_names_the_icons_chrome_and_edge_draw():
    manifest = _manifest()
    icons = manifest.get("icons")
    assert isinstance(icons, dict), "manifest.json names no icons; the browser draws a grey letter"
    assert sorted(int(s) for s in icons) == list(SIZES), icons
    for size, path in icons.items():
        assert path == f"dist/icons/icon-{size}.png", (
            f"icons[{size}] is {path!r}; only dist/** ships in the installer, so an "
            "icon outside dist/ would be missing on the user's PC and Chrome would "
            "refuse the whole folder"
        )
    action = manifest["action"]
    assert action["default_title"] == "Iron Jarvis", "the toolbar button lost its title"
    assert action.get("default_icon") == {"16": icons["16"], "32": icons["32"]}, (
        "the toolbar button must draw the same icon at 1x and 2x"
    )


def test_each_icon_is_the_app_icon_at_that_size():
    script = _icons_script()
    assert script.SIZES == SIZES
    assert script.stale() == [], (
        "the add-on's icons are not what scripts/make_addon_icons.py makes from "
        "desktop/assets/icon.png — run `uv run python scripts/make_addon_icons.py`"
    )
    ico = Image.open(APP_ICO)
    for size in SIZES:
        with Image.open(ADDON / "src" / "icons" / f"icon-{size}.png") as im:
            assert im.size == (size, size) and im.mode == "RGBA", (size, im.size, im.mode)
            icon = im.copy()
        # The tile's rounded corners are clear; its middle is the opaque navy tile.
        assert icon.getpixel((0, 0))[3] == 0, f"icon-{size}: the corner is not transparent"
        r, g, b, a = icon.getpixel((size // 2, size // 2))
        assert a == 255 and max(r, g, b) < 90, (size, (r, g, b, a))
        # The same pixels Windows shows for the app at this size.
        ico.size = (size, size)
        frame = ico.copy().convert("RGBA")
        worst = max(hi for _, hi in ImageChops.difference(icon, frame).getextrema())
        assert worst <= 2, f"icon-{size} differs from icon.ico's {size}px frame by {worst}"


def test_the_bundler_copies_every_icon_into_dist(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH; the add-on CI job builds the add-on")
    if not (ADDON / "node_modules" / "esbuild").exists():
        pytest.skip("extensions/chrome dependencies are not installed")
    out = tmp_path / "dist"
    env = {**os.environ, "IJ_ADDON_OUT": str(out)}
    result = subprocess.run(  # noqa: S603 - fixed argv, repo-local script
        [node, str(ADDON / "esbuild.config.mjs")],
        cwd=ADDON, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for rel in _named_icons(_manifest()):
        built = tmp_path / rel
        assert built.is_file(), f"{rel} is not in the built add-on; Chrome would refuse it"
        assert built.read_bytes() == (ADDON / "src" / "icons" / built.name).read_bytes()
    # The two pages that open in a TAB point their tab icon at a file that is there.
    for page in ("setup.html", "mic.html"):
        text = (out / page).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert '<link rel="icon" type="image/png" sizes="32x32" href="icons/icon-32.png" />' in text, page
        assert (out / "icons" / "icon-32.png").is_file()


def _real_addon(root: Path) -> Path:
    """An add-on folder with the REAL manifest and every file it names."""
    folder = root / "browser-addon"
    folder.mkdir(parents=True)
    shutil.copyfile(ADDON / "manifest.json", folder / "manifest.json")
    for rel in doctor_mod._addon_build_missing(folder):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(b"<!doctype html>" if rel.endswith(".html") else b"built")
    return folder


def test_the_doctor_wants_every_icon_the_manifest_names(tmp_path):
    folder = _real_addon(tmp_path)
    assert doctor_mod._addon_build_missing(folder) == []
    for rel in _named_icons(_manifest()):
        assert (folder / rel).is_file(), f"the doctor does not ask for {rel}"


def test_an_addon_missing_an_icon_is_not_called_ready(tmp_path, monkeypatch):
    folder = _real_addon(tmp_path)
    (folder / "dist" / "icons" / "icon-48.png").unlink()
    monkeypatch.setenv(doctor_mod.BROWSER_ADDON_ENV, str(folder))

    row = doctor_mod.check_browser_addon()

    assert row["ok"] is False, f"a folder Chrome refuses is called ready: {row}"
    assert "dist/icons/icon-48.png" in row["detail"], row["detail"]
    assert row["fix"], "an unloadable add-on is reported with no remedy"


def test_a_single_file_default_icon_is_checked_too(tmp_path):
    """``action.default_icon`` may be one file for every size; Chrome loads it too."""
    folder = tmp_path / "addon"
    folder.mkdir()
    manifest = _manifest()
    manifest["action"]["default_icon"] = "dist/icons/toolbar.png"
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "dist/icons/toolbar.png" in doctor_mod._addon_build_missing(folder)
