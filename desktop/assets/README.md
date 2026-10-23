# Iron Jarvis Desktop — assets

The application icon (v1.380.0): the "iU" tile the user chose.

- `icon-source.png` — the artwork exactly as the user gave it (a dark rounded
  tile on a dark background). Keep it: every other file is made from it.
- `icon.png` — the tile cropped to its rounded square, corners made
  transparent, 256×256 RGBA.
- `icon.ico` — the same at 16, 20, 24, 32, 40, 48, 64, 96, 128 and 256 px, so
  Windows draws each place (tray, taskbar, desktop shortcut, installer) from
  a size made for it.

They are referenced by:

- `package.json` → `build.win.icon` (`icon.ico`): the app's exe, its desktop
  and Start-menu shortcuts, and the NSIS installer/uninstaller.
- `main.js` → `windowIcon()` (the `.ico` on Windows, else the `.png`) for the
  main window and pop-out windows — the taskbar button.
- `main.js` → `createTray()` (the `.ico`, else the `.png`) for the tray.

To change the icon: replace `icon-source.png` and remake `icon.png` and
`icon.ico` from it (crop to the tile, round the corners with a transparent
outside, then save the sizes above). `tests/test_app_icon_v1380.py` checks the
sizes, the transparent corners and every reference.

Windows keeps its own icon cache: after an update installs, a desktop
shortcut can show the old icon until Windows refreshes it (signing out and in
does it).
