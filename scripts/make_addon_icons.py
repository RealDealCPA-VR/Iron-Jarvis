"""Make the browser add-on's icons from the app icon (v1.400.6).

The add-on is "Iron Jarvis" in the browser's toolbar, its extensions menu, the
side panel's header and the extensions page, and each of those draws the icon
``manifest.json`` names. They wear the SAME picture as the desktop app: the
user's "iU" tile, ``desktop/assets/icon.png`` (itself cropped from
``icon-source.png``, v1.380.0).

Each size is ``icon.png`` resized with LANCZOS, which is exactly how Pillow made
the frames of ``desktop/assets/icon.ico`` — so the 16 px toolbar icon is
pixel-for-pixel the 16 px icon Windows shows for the app. Files are committed
under ``extensions/chrome/src/icons/`` and copied into ``dist/icons/`` by the
add-on's bundler (``esbuild.config.mjs``); only ``dist/**`` ships in the
installer.

``tests/test_browser_addon_icon_v14006.py`` fails when a committed icon no
longer matches what this script would make, so a new app icon cannot leave the
add-on wearing the old one.

Usage (from the repo root):
    uv run python scripts/make_addon_icons.py           # write the icons
    uv run python scripts/make_addon_icons.py --check   # exit 1 when stale
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
APP_ICON = ROOT / "desktop" / "assets" / "icon.png"
OUT_DIR = ROOT / "extensions" / "chrome" / "src" / "icons"
#: What Chrome and Edge ask for: 16 (toolbar, menu, side panel header),
#: 32 (the same at 2x), 48 (the extensions page), 128 (install and store).
SIZES = (16, 32, 48, 128)


def icon_name(size: int) -> str:
    return f"icon-{size}.png"


def make(size: int) -> Image.Image:
    """The add-on's icon at *size* px, made from the app icon."""
    with Image.open(APP_ICON) as base:
        return base.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def stale() -> list[str]:
    """The icons that are missing or differ from what :func:`make` gives."""
    out: list[str] = []
    for size in SIZES:
        path = OUT_DIR / icon_name(size)
        if not path.is_file():
            out.append(f"{path.name} is missing")
            continue
        with Image.open(path) as have:
            got = have.convert("RGBA")
        want = make(size)
        if got.size != want.size or ImageChops.difference(got, want).getbbox() is not None:
            out.append(f"{path.name} differs from the app icon")
    return out


def main(argv: list[str]) -> int:
    if "--check" in argv:
        problems = stale()
        for line in problems:
            print(line)
        return 1 if problems else 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        make(size).save(OUT_DIR / icon_name(size), optimize=True)
    print(f"wrote {len(SIZES)} icons into {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
