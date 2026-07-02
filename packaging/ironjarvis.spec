# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — freeze the Iron Jarvis daemon into a STANDALONE Windows
onedir application (`ironjarvis.exe`) that runs with NO Python/uv installed.

Build:
    pyinstaller packaging/ironjarvis.spec --noconfirm \
        --distpath packaging/dist --workpath packaging/build

Output: packaging/dist/ironjarvis/ironjarvis.exe  (+ _internal/ bundle)

Run the daemon (offline, mock provider):
    packaging/dist/ironjarvis/ironjarvis.exe serve --port 8799 --root <state_dir>
"""

import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)

# SPECPATH is injected by PyInstaller and points at this file's directory, so
# the build works regardless of the current working directory.
ENTRY = os.path.join(SPECPATH, "ironjarvis_entry.py")
SRC = os.path.join(SPECPATH, os.pardir, "src")

hiddenimports: list[str] = []
datas: list = []
binaries: list = []


def _collect(pkg: str) -> None:
    """collect_all() a package, tolerating ones that aren't installed."""
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:  # pragma: no cover - build-time only
        print(f"[ironjarvis.spec] skip collect_all({pkg!r}): {exc}")
        return
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# --- The whole iron_jarvis package -----------------------------------------
# platform.py registers every SQLModel table by importing its package at module
# load; pull in EVERY submodule so nothing is missed by static analysis.
hiddenimports += collect_submodules("iron_jarvis")
# Bundled non-Python data (skills/builtin/*/SKILL.md, py.typed).
datas += collect_data_files("iron_jarvis")

# --- Core runtime stack (collect everything: submodules + data + metadata) --
for pkg in (
    "fastapi",
    "starlette",
    "uvicorn",
    "sqlmodel",
    "sqlalchemy",
    "pydantic",
    "pydantic_core",
    "cryptography",
    "apscheduler",      # cron scheduler — started at daemon boot (lifespan)
    "anyio",
    "click",
    "typer",
    "rich",
    "httpx",
    "httpcore",
    "h11",
    "tomli_w",
    "yaml",             # pyyaml — skills frontmatter
):
    _collect(pkg)

# numpy (MockEmbedder) — binaries are handled by PyInstaller's built-in hook;
# make sure every submodule is importable too.
hiddenimports += collect_submodules("numpy")
hiddenimports += ["greenlet"]  # SQLAlchemy's optional C-accel dependency

# anthropic SDK — the headline Claude provider (AnthropicAdapter imports it).
# Bundled so a user's Claude key works in the standalone app with no rebuild.
# (OpenAI/Google adapters use raw httpx — no SDK needed; openai stays excluded.)
_collect("anthropic")

# --- uvicorn[standard]: protocol/lifespan impls loaded dynamically by name --
hiddenimports += [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
]
hiddenimports += collect_submodules("websockets")
hiddenimports += ["httptools"]  # optional fast HTTP parser (uvicorn standard)

# --- Document libraries (lazily imported in iron_jarvis.documents) ----------
# Included so doc read/write tools work out of the box. They add modest size;
# drop any you don't need from this tuple to shrink the bundle.
for pkg in ("pypdf", "docx", "openpyxl", "pptx", "fpdf", "PIL"):
    _collect(pkg)

# --- Windows terminals (PTY) — lazily `import winpty` at runtime -------------
if os.name == "nt":
    hiddenimports += collect_submodules("winpty")
    # CRITICAL: pywinpty's ConPTY backend spawns the shell inside a pseudo-
    # console HOSTED BY OpenConsole.exe; the legacy backend needs
    # winpty-agent.exe. collect_submodules/collect_all pull in winpty's .py +
    # .dll + .pyd but NOT these .exe helpers, so a frozen daemon creates a
    # pseudoconsole with no host and EVERY shell dies instantly (~1s) →
    # the dashboard terminal shows "session closed" and no work can be done.
    # Bundle the .exe helpers next to winpty's DLLs (its package dir, which is
    # _internal/winpty/ at runtime — where pywinpty looks for them).
    try:
        import winpty as _wp

        _wpdir = os.path.dirname(_wp.__file__)
        for _f in ("OpenConsole.exe", "winpty-agent.exe", "winpty.dll", "conpty.dll"):
            _p = os.path.join(_wpdir, _f)
            if os.path.exists(_p):
                datas.append((_p, "winpty"))
                print(f"[ironjarvis.spec] bundled winpty helper: {_f}")
    except Exception as exc:  # noqa: BLE001
        print(f"[ironjarvis.spec] WARN could not bundle winpty helpers: {exc}")

# --- Excludes: heavy OPTIONAL deps the daemon must boot WITHOUT --------------
# All are imported lazily (only when a real key / opt-in feature is used), so
# excluding them keeps the daemon booting offline on the mock provider.
#   playwright -> computer-use only        docker -> docker sandbox only
#   anthropic / openai -> real LLM key only (mock is the default provider)
# Re-include any of these by deleting it here and rebuilding.
excludes = [
    "playwright",
    "docker",
    "openai",
    # Dev / notebook / GUI toolchains never used by the daemon.
    "tkinter",
    "matplotlib",
    "pytest",
    "IPython",
    "notebook",
]

a = Analysis(
    [ENTRY],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir: binaries live in _internal/
    name="ironjarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,            # the daemon logs to stdout/stderr
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ironjarvis",
)
