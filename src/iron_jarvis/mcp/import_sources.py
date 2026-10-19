"""The config files other apps keep their MCP servers in (v1.343.0, lane I).

``GET /mcp/import/sources`` lists what Claude Code, Claude Desktop, Codex,
Cursor and VS Code already have, read-only, so the user can pick packs
instead of retyping them. Every path comes from the environment and the
known folders (``CLAUDE_CONFIG_DIR``, ``CODEX_HOME``, ``APPDATA``,
``USERPROFILE``/``HOME`` through :func:`pathlib.Path.home`) or from the
platform (the active project's root); nothing here takes a path from a
request, so a browser can never make the daemon read an arbitrary file.

v1.354.0 (G50): the files current tools really use — VS Code's
``User/mcp.json`` and a project's ``.vscode/mcp.json`` (VS Code moved its
servers out of ``settings.json``), a project's ``.cursor/mcp.json``,
Windsurf's ``~/.codeium/windsurf/mcp_config.json``, Gemini CLI's
``~/.gemini/settings.json`` and the servers of the Claude Code PLUGINS the
user installed (``<Claude config>/plugins/installed_plugins.json`` → each
plugin's ``.mcp.json`` and its manifest's ``mcpServers``). A project-only
source is listed only while a project is active. A server an app has
switched off arrives with ``disabled`` set (see ``PackSpec.disabled``).

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

from .import_config import FILE_CAP_BYTES, PackSpec, parse_file, safe_name, unique_names

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
    answers the path (``None`` when the environment has no such place).

    v1.354.0 (G50): ``project`` marks a file inside the active project's
    folder — with no project active there is no file to name, so the row is
    left out of the scan. ``read`` replaces :func:`parse_file` for a source
    that is more than one file (the Claude Code plugins): it is handed the
    resolved path and answers ``(specs, readable, note)``, never raising."""

    source: str
    dialect: str
    label: str
    resolve: Callable[[Any], Path | None]
    project: bool = False
    read: Callable[[Path], tuple[list[PackSpec], bool, str]] | None = None


def _p(path: Path | None, *parts: str) -> Path | None:
    return path.joinpath(*parts) if path is not None else None


def _in_project(*parts: str) -> Callable[[Any], Path | None]:
    """A resolver for a file inside the active project's folder."""

    def _resolve(platform: Any) -> Path | None:
        root = active_project_root(platform)
        return Path(root).joinpath(*parts) if root else None

    return _resolve


def _project_mcp_json(platform: Any) -> Path | None:
    return _in_project(".mcp.json")(platform)


# --------------------------------------------------------------------------- #
# Claude Code plugins (v1.354.0, G50)
# --------------------------------------------------------------------------- #
#: At most this many installed plugins are read (each is a folder or two).
PLUGINS_MAX = 100


def claude_plugins_file() -> Path | None:
    """Claude Code's record of the plugins it installed:
    ``<Claude config>/plugins/installed_plugins.json`` — or
    ``$CLAUDE_CODE_PLUGIN_CACHE_DIR/installed_plugins.json`` when that
    override is set (read off claude 2.1.303: the plugins folder is the
    override, else ``<config dir>/plugins``)."""
    override = _env_dir("CLAUDE_CODE_PLUGIN_CACHE_DIR")
    if override is not None:
        return override / "installed_plugins.json"
    return _p(claude_config_dir(), "plugins", "installed_plugins.json")


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """A small JSON object off the disk (read-only, size-capped), or None."""
    try:
        if not path.is_file():
            return None
        with open(path, "rb") as fh:
            data = fh.read(FILE_CAP_BYTES + 1)
    except OSError:
        return None
    if len(data) > FILE_CAP_BYTES:
        return None
    try:
        from ..core.jsonish import loads_object

        obj = loads_object(data.decode("utf-8", errors="replace").lstrip("﻿"))
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def _enabled_plugins() -> dict[str, bool]:
    """``enabledPlugins`` from Claude Code's user ``settings.json``: plugin
    id → on/off. A plugin the map does not name is left ON (installed)."""
    path = _p(claude_config_dir(), "settings.json")
    obj = _read_json_object(path) if path is not None else None
    raw = obj.get("enabledPlugins") if isinstance(obj, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, bool)}


def _install_root(records: Any) -> Path | None:
    """The plugin's folder from its installed record: the v1 shape is one
    dict, the v2 shape a list of per-scope dicts (the first with a path)."""
    for rec in records if isinstance(records, list) else [records]:
        if isinstance(rec, dict) and isinstance(rec.get("installPath"), str) and rec["installPath"].strip():
            return Path(rec["installPath"].strip())
    return None


def _inside(root: Path, rel: str) -> Path | None:
    """``root/rel`` when it stays inside ``root`` (a manifest names files
    RELATIVE to its plugin; a path that climbs out is not read)."""
    try:
        base = root.resolve()
        target = (root / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


_PLUGIN_ROOT_MARKS: tuple[str, ...] = ("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT")


def _with_plugin_root(spec: PackSpec, root: Path) -> PackSpec:
    """Claude Code expands ``${CLAUDE_PLUGIN_ROOT}`` to the plugin's folder
    when it starts a plugin's server; an imported pack starts from here,
    so the folder is written in."""
    folder = str(root)

    def _sub(text: str) -> str:
        for mark in _PLUGIN_ROOT_MARKS:
            text = text.replace(mark, folder)
        return text

    spec.command = _sub(spec.command)
    spec.args = [_sub(a) for a in spec.args]
    spec.cwd = _sub(spec.cwd)
    spec.env = {k: _sub(v) for k, v in spec.env.items()}
    # v1.355.0 integrate: a value with a placeholder the user is asked for is
    # filled from its TEMPLATE (mcp/import_config.fill_placeholders), so the
    # folder is written into the template too.
    for fill in getattr(spec, "fills", None) or []:
        fill["template"] = _sub(str(fill.get("template") or ""))
    return spec


def _plugin_specs(root: Path) -> list[PackSpec]:
    """Every server one plugin ships: its ``.mcp.json`` plus the manifest's
    ``mcpServers`` (an inline map, a relative path to a JSON file, or a list
    of those — a bundle file is not read)."""
    from .import_config import _parse_text

    specs: list[PackSpec] = []
    seen: set[str] = set()

    def _add_file(path: Path | None) -> None:
        if path is None:
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        found, _readable, _note = parse_file(path, source="claude-code")
        specs.extend(found)

    _add_file(root / ".mcp.json")
    manifest = _read_json_object(root / ".claude-plugin" / "plugin.json")
    declared = manifest.get("mcpServers") if isinstance(manifest, dict) else None
    for item in declared if isinstance(declared, list) else [declared]:
        if isinstance(item, str) and item.strip().lower().endswith(".json"):
            _add_file(_inside(root, item.strip()))
        elif isinstance(item, dict) and item:
            import json as _json

            inline, _ok = _parse_text(
                _json.dumps({"mcpServers": item}),
                source="claude-code",
                origin=str(root / ".claude-plugin" / "plugin.json"),
                kind_hint="json",
            )
            specs.extend(inline)
    return [_with_plugin_root(s, root) for s in specs]


#: v1.354.0 (G50, re-review): Claude Desktop never writes ``"disabled"`` on a
#: server's entry. Its own on/off switches ("Search and tools") are saved as
#: DISABLED TOOL keys in this file beside its config, shaped
#: ``{"v": 3, "owners": {<account>: ["local:<server>:<tool>", ...]}}``
#: (Claude Desktop 2.42304's persistDisabledToolKeys; read on the user's own
#: PC: quickbooks-desktop had all 150 of its tools listed).
CLAUDE_DESKTOP_TOGGLES = "mcp-user-tool-toggles.json"


def claude_desktop_switched_off(config_path: Path) -> set[str]:
    """Server names Claude Desktop has tools switched OFF for, read-only from
    the toggles file next to its config (every account's list joined). The
    file names tools, not servers, so "some tools off" and "all tools off"
    look alike here: both are reported, and the import door leaves such a
    server UNTICKED with "turned off in Claude Desktop" — the user ticks it
    if they want it anyway. A missing, oversized or unreadable file is
    ``set()``. Never raises."""
    try:
        obj = _read_json_object(config_path.parent / CLAUDE_DESKTOP_TOGGLES)
    except Exception:  # noqa: BLE001
        return set()
    owners = obj.get("owners") if isinstance(obj, dict) else None
    if not isinstance(owners, dict):
        return set()
    off: set[str] = set()
    for keys in owners.values():
        if not isinstance(keys, list):
            continue
        for key in keys:
            parts = str(key or "").split(":", 2)
            if len(parts) == 3 and parts[0] == "local" and parts[1]:
                off.add(parts[1])
    return off


def read_claude_desktop(path: Path) -> tuple[list[PackSpec], bool, str]:
    """Claude Desktop's config (the ordinary parse) with its own switches
    applied: a server it has tools switched off for arrives ``disabled``."""
    specs, readable, note = parse_file(path, source="claude-desktop")
    if specs:
        off = claude_desktop_switched_off(path)
        if off:
            off_safe = {safe_name(n) for n in off}
            for spec in specs:
                if spec.name in off or safe_name(spec.name) in off_safe:
                    spec.disabled = True
    return specs, readable, note


def read_claude_plugins(path: Path) -> tuple[list[PackSpec], bool, str]:
    """The servers of every Claude Code plugin the user INSTALLED (never the
    whole marketplace catalogue the plugins folder also holds), each with
    its own file as ``origin``; a plugin switched off in ``enabledPlugins``
    gives disabled specs. A missing record is ``([], False, "")`` like any
    missing file; a broken one names the file. Never raises."""
    try:
        if not path.is_file():
            return ([], False, "")
    except OSError:
        return ([], False, "")
    doc = _read_json_object(path)
    if doc is None:
        return ([], False, f"{path.name} could not be read as a config file")
    plugins = doc.get("plugins")
    if not isinstance(plugins, dict):
        return ([], True, "")
    enabled = _enabled_plugins()
    specs: list[PackSpec] = []
    for count, (plugin_id, records) in enumerate(plugins.items()):
        if count >= PLUGINS_MAX:
            break
        root = _install_root(records)
        if root is None:
            continue
        try:
            found = _plugin_specs(root)
        except Exception:  # noqa: BLE001 — one broken plugin never hides the rest
            continue
        off = enabled.get(str(plugin_id)) is False
        for spec in found:
            if off:
                spec.disabled = True
            specs.append(spec)
    return (unique_names(specs), True, "")


KNOWN_SOURCES: tuple[SourceDef, ...] = (
    SourceDef("claude-code", "claude-code", "Claude Code", lambda _p_: claude_json_path()),
    SourceDef(
        "claude-code-settings",
        "claude-code",
        "Claude Code settings",
        lambda _p_: _p(claude_config_dir(), "settings.json"),
    ),
    SourceDef("claude-code-project", "claude-code", "Claude Code (this project)", _project_mcp_json, project=True),
    # v1.354.0 (G50): the plugins the user installed in Claude Code.
    SourceDef(
        "claude-code-plugins",
        "claude-code",
        "Claude Code plugins",
        lambda _p_: claude_plugins_file(),
        read=read_claude_plugins,
    ),
    SourceDef(
        "claude-desktop",
        "claude-desktop",
        "Claude Desktop",
        lambda _p_: _p(app_data_dir(), "Claude", "claude_desktop_config.json"),
        read=read_claude_desktop,
    ),
    SourceDef("codex", "codex", "Codex", lambda _p_: _p(codex_home(), "config.toml")),
    SourceDef("cursor", "cursor", "Cursor", lambda _p_: _p(_home(), ".cursor", "mcp.json")),
    SourceDef("cursor-project", "cursor", "Cursor (this project)", _in_project(".cursor", "mcp.json"), project=True),
    # v1.354.0 (G50): current VS Code keeps its servers in mcp.json (user
    # and per-project); settings.json is the older place, still read.
    SourceDef(
        "vscode-mcp",
        "vscode",
        "VS Code",
        lambda _p_: _p(app_data_dir(), "Code", "User", "mcp.json"),
    ),
    SourceDef("vscode-project", "vscode", "VS Code (this project)", _in_project(".vscode", "mcp.json"), project=True),
    SourceDef(
        "vscode",
        "vscode",
        "VS Code settings",
        lambda _p_: _p(app_data_dir(), "Code", "User", "settings.json"),
    ),
    SourceDef(
        "windsurf",
        "windsurf",
        "Windsurf",
        lambda _p_: _p(_home(), ".codeium", "windsurf", "mcp_config.json"),
    ),
    SourceDef("gemini-cli", "gemini-cli", "Gemini CLI", lambda _p_: _p(_home(), ".gemini", "settings.json")),
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
        if defn.read is not None:
            specs, readable, note = defn.read(Path(origin))
        else:
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
    :data:`KNOWN_SOURCES` entry, in that order; a project row is left out
    when no project is active (there is no file to name). Never raises."""
    out: list[SourceScan] = []
    for defn in KNOWN_SOURCES:
        try:
            item = _scan_one(defn, platform)
        except Exception:  # noqa: BLE001
            item = SourceScan(source=defn.source, label=defn.label, origin="")
        if defn.project and not item.origin:
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
