"""The config files other apps keep their MCP servers in (v1.343.0, lane I).

``GET /mcp/import/sources`` lists what Claude Code, Claude Desktop, Codex,
Cursor and VS Code already have, read-only, so the user can pick packs
instead of retyping them. Every path comes from the environment and the
known folders (``CLAUDE_CONFIG_DIR``, ``CODEX_HOME``, ``APPDATA``,
``USERPROFILE``/``HOME`` through :func:`pathlib.Path.home`) or from the
platform (the active project's root); nothing here takes a path from a
request, so a browser can never make the daemon read an arbitrary file.

:func:`scan` reads each file through :func:`mcp.import_config.parse_file`
(``open(..., "rb")`` only, size-capped, never raises) and answers the
contract rows with NO values: a missing file is ``readable: False`` with an
empty note; a malformed one is ``readable: False`` with a one-line note that
names the file and never its contents. Iron Jarvis's own server entries are
dropped by the parser. The specs ride the scan in memory for the route to
turn into preview rows; :func:`read_source` is the apply door's half.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .import_config import PackSpec, parse_file

#: Marks of Iron Jarvis's own server, repeated here only for the doc reader;
#: the parser (``import_config._OWN_SERVER_MARKS``) is what drops them.
OWN_SERVER_MARKS: tuple[str, ...] = ("ironjarvis", "mcp-stdio", "claude-inert-mcp")


# --------------------------------------------------------------------------- #
# Where things live
# --------------------------------------------------------------------------- #
def _home() -> Path | None:
    """The user's home (``USERPROFILE`` on Windows, ``HOME`` elsewhere —
    :func:`Path.home` reads the right one), or None when there is none."""
    try:
        return Path.home()
    except (RuntimeError, KeyError, OSError):
        return None


def _env_dir(name: str) -> Path | None:
    raw = (os.environ.get(name) or "").strip()
    return Path(raw) if raw else None


def claude_config_dir() -> Path | None:
    """``CLAUDE_CONFIG_DIR`` when set, else ``~/.claude``."""
    override = _env_dir("CLAUDE_CONFIG_DIR")
    if override is not None:
        return override
    home = _home()
    return home / ".claude" if home is not None else None


def claude_json_path() -> Path | None:
    """Claude Code's user file: ``~/.claude.json``, or
    ``$CLAUDE_CONFIG_DIR/.claude.json`` when the override is set (measured
    in v1.302.0: with the override set, Claude Code reads the file INSIDE
    that folder, not the real home's)."""
    override = _env_dir("CLAUDE_CONFIG_DIR")
    if override is not None:
        return override / ".claude.json"
    home = _home()
    return home / ".claude.json" if home is not None else None


def codex_home() -> Path | None:
    """``CODEX_HOME`` when set, else ``~/.codex``."""
    override = _env_dir("CODEX_HOME")
    if override is not None:
        return override
    home = _home()
    return home / ".codex" if home is not None else None


def app_data_dir() -> Path | None:
    """Where per-user app configs live: ``%APPDATA%`` on Windows; on macOS
    ``~/Library/Application Support``; elsewhere ``$XDG_CONFIG_HOME`` or
    ``~/.config``. Claude Desktop and VS Code both keep their files there."""
    appdata = _env_dir("APPDATA")
    if appdata is not None:
        return appdata
    home = _home()
    if home is None:
        return None
    if sys.platform == "win32":
        return home / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    xdg = _env_dir("XDG_CONFIG_HOME")
    return xdg if xdg is not None else home / ".config"


def active_project_root(platform: Any) -> str:
    """The active project's folder (``config.active_project_id`` → the
    Project row's ``root``), or ``""``. Never raises: no platform, no
    engine, no active project or a broken store all answer ``""``."""
    try:
        config = getattr(platform, "config", None)
        project_id = str(getattr(config, "active_project_id", "") or "")
        engine = getattr(platform, "engine", None)
        if not project_id or engine is None:
            return ""
        from ..core.db import session_scope
        from ..core.models import Project

        with session_scope(engine) as db:
            row = db.get(Project, project_id)
            if row is None:
                return ""
            if (getattr(row, "status", "active") or "active") == "archived":
                return ""
            return str(getattr(row, "root", "") or "")
    except Exception:  # noqa: BLE001 — a scan never raises
        return ""


# --------------------------------------------------------------------------- #
# The sources
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceDef:
    """One known config file: a UNIQUE ``source`` key (the dashboard keys
    its groups and posts it back on apply), the dialect the parser reads it
    as (``PackSpec.source``), a label in plain words, and a resolver that
    answers the path (``None`` when the environment has no such place)."""

    source: str
    dialect: str
    label: str
    resolve: Callable[[Any], Path | None]


def _p(path: Path | None, *parts: str) -> Path | None:
    return path.joinpath(*parts) if path is not None else None


def _project_mcp_json(platform: Any) -> Path | None:
    root = active_project_root(platform)
    return Path(root) / ".mcp.json" if root else None


KNOWN_SOURCES: tuple[SourceDef, ...] = (
    SourceDef("claude-code", "claude-code", "Claude Code", lambda _p_: claude_json_path()),
    SourceDef(
        "claude-code-settings",
        "claude-code",
        "Claude Code settings",
        lambda _p_: _p(claude_config_dir(), "settings.json"),
    ),
    SourceDef("claude-code-project", "claude-code", "Claude Code (this project)", _project_mcp_json),
    SourceDef(
        "claude-desktop",
        "claude-desktop",
        "Claude Desktop",
        lambda _p_: _p(app_data_dir(), "Claude", "claude_desktop_config.json"),
    ),
    SourceDef("codex", "codex", "Codex", lambda _p_: _p(codex_home(), "config.toml")),
    SourceDef("cursor", "cursor", "Cursor", lambda _p_: _p(_home(), ".cursor", "mcp.json")),
    SourceDef(
        "vscode",
        "vscode",
        "VS Code",
        lambda _p_: _p(app_data_dir(), "Code", "User", "settings.json"),
    ),
)


@dataclass
class SourceScan:
    """One scanned file. ``specs`` carry values IN MEMORY for the apply door;
    :meth:`row` is what a route may answer (no values, no specs)."""

    source: str
    label: str
    origin: str
    readable: bool = False
    note: str = ""
    specs: list[PackSpec] = field(default_factory=list)

    def row(self, servers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "source": self.source,
            "label": self.label,
            "origin": self.origin,
            "readable": bool(self.readable),
            "note": self.note or "",
            "servers": list(servers or []),
        }


def _resolve(defn: SourceDef, platform: Any) -> str:
    try:
        path = defn.resolve(platform)
    except Exception:  # noqa: BLE001 — a resolver fault is "no such place"
        return ""
    if path is None:
        return ""
    try:
        return str(path)
    except Exception:  # noqa: BLE001
        return ""


def _scan_one(defn: SourceDef, platform: Any) -> SourceScan:
    origin = _resolve(defn, platform)
    if not origin:
        # No such place on this machine (no APPDATA, no home): an unreadable
        # row with nothing to say, like a missing file.
        return SourceScan(source=defn.source, label=defn.label, origin="")
    try:
        specs, readable, note = parse_file(origin, source=defn.dialect)
    except Exception:  # noqa: BLE001 — parse_file never raises; belt and braces
        specs, readable, note = [], False, f"{Path(origin).name} could not be read"
    # The parser drops Iron Jarvis's own server entries; a config that
    # ONLY listed us reads as readable and empty, which is the truth.
    return SourceScan(
        source=defn.source,
        label=defn.label,
        origin=origin,
        readable=bool(readable),
        note=str(note or "").splitlines()[0] if note else "",
        specs=list(specs),
    )


def scan(platform: Any) -> list[SourceScan]:
    """Read every known source READ-ONLY. One :class:`SourceScan` per
    :data:`KNOWN_SOURCES` entry, in that order; the project row is left out
    when no project is active (there is no file to name). Never raises."""
    out: list[SourceScan] = []
    for defn in KNOWN_SOURCES:
        try:
            item = _scan_one(defn, platform)
        except Exception:  # noqa: BLE001
            item = SourceScan(source=defn.source, label=defn.label, origin="")
        if defn.source == "claude-code-project" and not item.origin:
            continue
        out.append(item)
    return out


def read_source(platform: Any, source: str) -> SourceScan | None:
    """The apply door's half: re-read ONE known source by its ``source`` key
    (the key a scan row carried). An unknown key answers ``None`` — a
    request can only name a file this module already knows how to find,
    never a path of its own."""
    key = str(source or "").strip()
    if not key:
        return None
    for defn in KNOWN_SOURCES:
        if defn.source == key:
            try:
                return _scan_one(defn, platform)
            except Exception:  # noqa: BLE001
                return SourceScan(source=defn.source, label=defn.label, origin="")
    return None
