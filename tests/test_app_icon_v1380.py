"""v1.380.0 — the app's own icon (the user's "iU" tile) on the desktop
shortcut, the taskbar, the tray and the installer.

The user pasted the artwork; `desktop/assets/icon-source.png` keeps it as
given. `icon.png` is that tile cropped to its rounded square with the corners
made transparent (256 px), and `icon.ico` carries every size Windows draws
(16–256), so the shortcut, the taskbar button and the tray each get a size
made for them instead of one picture scaled down.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "desktop" / "assets"
MAIN_JS = ROOT / "desktop" / "main.js"


def _main_js() -> str:
    return MAIN_JS.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_ico_carries_every_size_windows_draws():
    ico = Image.open(ASSETS / "icon.ico")
    sizes = set(ico.info.get("sizes", set()))
    for s in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        assert (s, s) in sizes, f"icon.ico is missing {s}x{s}: {sorted(sizes)}"


def test_the_png_is_the_tile_with_transparent_corners():
    im = Image.open(ASSETS / "icon.png")
    assert im.size == (256, 256) and im.mode == "RGBA"
    alpha = im.getchannel("A")
    # Outside the rounded corners: clear. The middle: the tile, fully opaque.
    for xy in ((0, 0), (255, 0), (0, 255), (255, 255), (4, 4)):
        assert alpha.getpixel(xy) == 0, f"corner {xy} is not transparent"
    for xy in ((128, 128), (128, 4), (4, 128), (251, 128), (128, 251)):
        assert alpha.getpixel(xy) == 255, f"{xy} should be inside the tile"
    # The tile is dark (the user's navy), not the old crimson placeholder.
    r, g, b, _ = im.getpixel((30, 128))
    assert max(r, g, b) < 80 and b >= r, (r, g, b)


def test_the_source_artwork_is_kept():
    assert (ASSETS / "icon-source.png").stat().st_size > 10_000


def test_the_installer_and_the_exe_take_the_ico():
    pkg = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    icon = pkg["build"]["win"]["icon"]
    assert icon == "assets/icon.ico"
    assert (ROOT / "desktop" / icon).exists()
    # assets/** ships with the app (the window and the tray read it at run time).
    assert "assets/**" in pkg["build"]["files"]


def test_every_window_with_an_icon_uses_the_window_icon():
    src = _main_js()
    assert re.search(r'function windowIcon\(\)[\s\S]{0,300}?"icon\.ico"[\s\S]{0,200}?win32', src), (
        "windowIcon() must prefer the .ico on Windows"
    )
    icons = re.findall(r"^\s*icon:\s*(.+?),\s*$", src, flags=re.M)
    assert icons, "no window sets an icon"
    assert all(v == "windowIcon()" for v in icons), icons
    # The tray still prefers the .ico (crispest in the notification area).
    tray = src[src.index("function createTray()"):]
    assert '"icon.ico"' in tray[:600]
