"""Claude Code's own "/" commands in a Build session (v1.379.0).

THE USER: "I have a lot of skills that IJ recognizes, but the / does not
recognize them for use. I want to be able to use the same / commands i do here
there in the IJ harness" — "here" being Claude Code. Its custom slash commands
are Markdown files, and this module finds the ones Claude Code itself offers
for the session's folder, READ-ONLY, with Claude Code's own naming:

* PERSONAL: ``<CLAUDE_CONFIG_DIR or ~/.claude>/commands/**/*.md``;
* PROJECT: ``<folder>/.claude/commands/**/*.md`` for the session's working
  folder and, for a session on its own branch, the folder the user picked
  (its worktree is a copy of the repository);
* PLUGINS: the ``commands/`` (or the manifest's ``commands`` key) of every
  plugin Claude Code would LOAD — an install recorded in
  ``plugins/installed_plugins.json``, a plugin synced from claude.ai
  (``plugins/synced/<account>/<name>``) or a plugin folder under
  ``~/.claude/skills`` — that ``enabledPlugins`` does not turn off. A plugin
  that is only listed in a marketplace CATALOG (``plugins/marketplaces/…``,
  available but never installed) or that Claude Code moved to its trash
  (``plugins/.trash``) is never offered.

Naming (Claude Code 2.1.307's docs and binary): a file's name without
``.md`` is the command; each subfolder adds ``<folder>:`` in front
(``frontend/component.md`` → ``frontend:component``); a plugin's commands are
``<plugin>:<command>``. A PERSONAL command wins over a PROJECT one of the same
name (Claude Code's "personal over project"). ``user-invocable: false`` hides
one. Frontmatter: ``description``, ``argument-hint``, ``arguments`` (named
placeholders), ``allowed-tools`` and ``model`` (read, never obeyed: the
session's own approval rules and its held model decide — see
:func:`expand`). A malformed header reads as no header, like Claude Code.

A plugin's SKILLS are offered under Claude Code's name for them too
(``<plugin>:<skill>``) — as an alias of the app's own registered skill of that
name, and only when the registry holds that very folder's copy.

Bounded everywhere (file size, how many, how deep), parsed files are cached by
(path, mtime, size), and nothing here writes, runs or follows anything out of
the folders it reads. BLOCKING (stats + small reads): the routes call it from
the threadpool or through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: A command file larger than this is not offered (Claude Code's own skills
#: are a few KB; a 64 KB prompt is already far past any real command).
MAX_COMMAND_BYTES = 64 * 1024
#: Command files looked at, at most, across every place (the rest are not
#: offered; ``discover`` says so in its note).
MAX_COMMANDS = 500
#: Subfolder levels under a ``commands`` folder that are read.
MAX_DEPTH = 5
#: Plugins looked at, at most.
MAX_PLUGINS = 60
#: A JSON settings / manifest file larger than this is not read.
MAX_JSON_BYTES = 2 * 1024 * 1024
#: An expanded command, at most (a turn's message is capped at 200,000).
MAX_EXPANDED_CHARS = 100_000
#: ``@`` names in one command looked up on disk, at most (the rest stay words).
MAX_AT_REFS = 32
#: Parsed command files kept in memory.
CACHE_SIZE = 2000

SOURCE_PERSONAL = "personal"
SOURCE_PROJECT = "project"
SOURCE_PLUGIN = "plugin"

#: One name segment: what a command file or folder may be called and still be
#: offered (Claude Code refuses control, private-use, direction and angle
#: characters; a plain name is all the menu needs).
_SEGMENT_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: The plugin trust note — a command or skill text that did not come from the
#: user (a project's or a plugin's) is scanned through ``core/promptguard``;
#: the user's own personal commands are their words and are not.
SCANNED_SOURCES = frozenset({SOURCE_PROJECT, SOURCE_PLUGIN})


class CommandRefused(ValueError):
    """A command that cannot run, in one plain sentence (``status`` for the route)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Command:
    """One custom command as Claude Code names it."""

    name: str
    description: str
    argument_hint: str
    source: str
    #: The plugin's display name (source "plugin"), else "".
    plugin: str
    path: str
    #: The plugin's own folder (source "plugin"), for ``${CLAUDE_PLUGIN_ROOT}``.
    plugin_root: str = ""
    arguments: tuple[str, ...] = ()
    #: Inline body (a plugin manifest's ``content`` entry), else "" = read ``path``.
    inline: str = ""

    def row(self) -> dict[str, Any]:
        """What the "/" menu lists — never the body."""
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
            "source": self.source,
            "plugin": self.plugin,
            "kind": "command",
        }


@dataclass(frozen=True)
class SkillAlias:
    """A plugin's skill under Claude Code's ``<plugin>:<skill>`` name."""

    name: str
    skill: str
    plugin: str
    description: str

    def row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": "",
            "source": SOURCE_PLUGIN,
            "plugin": self.plugin,
            "kind": "skill",
            "skill": self.skill,
        }


@dataclass
class Found:
    commands: list[Command] = field(default_factory=list)
    skills: list[SkillAlias] = field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------- #
# where Claude Code keeps things
# --------------------------------------------------------------------------- #


def config_dir() -> Path:
    """Claude Code's configuration folder: ``CLAUDE_CONFIG_DIR`` when set,
    else ``~/.claude`` (read live — a test points it at a scratch folder)."""
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def plugins_root() -> Path:
    """The plugins root: ``CLAUDE_CODE_PLUGIN_CACHE_DIR`` when set (Claude
    Code's own override), else ``<config>/plugins``."""
    raw = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR", "").strip()
    return Path(raw).expanduser() if raw else config_dir() / "plugins"


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _inside(child: Path, parent: Path) -> bool:
    try:
        c = child.resolve()
        p = parent.resolve()
    except OSError:
        return False
    return c == p or p in c.parents


# --------------------------------------------------------------------------- #
# one command file
# --------------------------------------------------------------------------- #

_CACHE: "OrderedDict[str, tuple[int, int, dict[str, Any], str]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """``(fields, body)``. A header that does not parse reads as NO header
    (Claude Code: "loads the skill body with empty metadata")."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    head = _quote_lossy("\n".join(lines[1:end]))
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    try:
        import yaml

        meta = yaml.safe_load(head) or {}
    except Exception:  # noqa: BLE001 — malformed YAML: the body, no metadata
        return {}, body
    return (meta if isinstance(meta, dict) else {}), body


#: Claude Code 2.1.307's own pre-pass over a header (its ``Wd``): a one-line
#: value holding one of these is QUOTED before the YAML read, so
#: ``argument-hint: [pr-number] [priority]`` is that text, not a YAML error.
_LOSSY_RX = re.compile(r"[{}\[\]*&#!|>%@`]|: ")
_HEADER_LINE_RX = re.compile(r"^([a-zA-Z_-]+):\s+(\S.*)$")


def _quote_lossy(head: str) -> str:
    """Claude Code's pre-pass: a value already quoted, or one that reads as a
    YAML list (``[a, b]``), is kept; any other value holding a YAML special
    character is wrapped in double quotes."""
    out: list[str] = []
    for line in head.split("\n"):
        m = _HEADER_LINE_RX.match(line)
        if m:
            key, value = m.groups()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                out.append(line)
                continue
            if value.startswith("[") and value.endswith("]"):
                try:
                    import yaml

                    if isinstance(yaml.safe_load(value), list):
                        out.append(line)
                        continue
                except Exception:  # noqa: BLE001 — not a list: quoted below
                    pass
            if _LOSSY_RX.search(value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                out.append(f'{key}: "{escaped}"')
                continue
        out.append(line)
    return "\n".join(out)


def _text_of(value: Any) -> str:
    """A header value as Claude Code shows it (JavaScript's ``String``: a list
    joins with ","), so ``argument-hint: [file]`` reads "file" there too."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_text_of(v) for v in value)
    return str(value)


def _read_file(path: Path) -> tuple[dict[str, Any], str] | None:
    """``(fields, body)`` of a command file, cached by (path, mtime, size);
    None when it is too large, unreadable or not UTF-8."""
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_size > MAX_COMMAND_BYTES:
        return None
    key = str(path)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            _CACHE.move_to_end(key)
            return hit[2], hit[3]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    text = text.replace("\r\n", "\n")
    if text.startswith("﻿"):
        text = text[1:]
    meta, body = _frontmatter(text)
    with _CACHE_LOCK:
        _CACHE[key] = (st.st_mtime_ns, st.st_size, meta, body)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return meta, body


def _flag(value: Any, default: bool) -> bool:
    """Claude Code's booleans: true/false, yes/no, on/off, 1/0."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    return default


def _one_line(value: Any, cap: int = 300) -> str:
    return " ".join(str(value or "").split())[:cap]


def _named_args(value: Any) -> tuple[str, ...]:
    """``arguments: [issue, branch]`` (or a space-separated string): the
    named placeholders, Claude Code's rule — no digit-only names, at most 64."""
    if isinstance(value, str):
        items: Iterable[Any] = value.split()
    elif isinstance(value, list):
        items = value
    else:
        return ()
    out: list[str] = []
    for item in items:
        name = str(item or "").strip()
        if name and not name.isdigit() and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name) and name not in out:
            out.append(name)
    return tuple(out[:64])


def _description_of(meta: dict[str, Any], body: str) -> str:
    desc = _one_line(_text_of(meta.get("description")))
    if desc:
        return desc
    for line in body.split("\n"):
        line = line.strip().lstrip("#").strip()
        if line:
            return _one_line(line, 160)
    return ""


def _command_from(path: Path, name: str, source: str, plugin: str = "", plugin_root: str = "",
                  extra: "dict[str, Any] | None" = None) -> Command | None:
    got = _read_file(path)
    if got is None:
        return None
    meta, body = got
    meta = {**meta, **(extra or {})}
    if not _flag(meta.get("user-invocable", True), True):
        return None
    return Command(
        name=name,
        description=_description_of(meta, body),
        argument_hint=_one_line(_text_of(meta.get("argument-hint") or meta.get("argumentHint")), 120),
        source=source,
        plugin=plugin,
        path=str(path),
        plugin_root=plugin_root,
        arguments=_named_args(meta.get("arguments")),
    )


def _walk(base: Path, budget: list[int]) -> list[tuple[Path, list[str]]]:
    """Every ``*.md`` under ``base`` (no deeper than :data:`MAX_DEPTH`), with
    its folder parts — sorted, links never followed out, dot folders skipped.
    ``budget`` is the shared count of files still allowed."""
    out: list[tuple[Path, list[str]]] = []
    if not base.is_dir():
        return out
    root = base

    def visit(folder: Path, parts: list[str]) -> None:
        if budget[0] <= 0 or len(parts) > MAX_DEPTH:
            return
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if budget[0] <= 0:
                return
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    if not _inside(entry, root):
                        continue  # a link out of the commands folder
                    if _SEGMENT_RX.match(entry.name):
                        visit(entry, [*parts, entry.name])
                elif entry.is_file() and entry.suffix.lower() == ".md":
                    if not _inside(entry, root):
                        continue
                    budget[0] -= 1
                    out.append((entry, parts))
            except OSError:
                continue

    visit(base, [])
    return out


def _from_folder(base: Path, source: str, budget: list[int], *, prefix: str = "", plugin: str = "",
                 plugin_root: str = "") -> list[Command]:
    out: list[Command] = []
    for path, parts in _walk(base, budget):
        stem = path.stem
        if not _SEGMENT_RX.match(stem):
            continue
        name = ":".join([*([prefix] if prefix else []), *parts, stem])
        cmd = _command_from(path, name, source, plugin, plugin_root)
        if cmd is not None:
            out.append(cmd)
    return out


# --------------------------------------------------------------------------- #
# plugins Claude Code would load
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    label: str
    root: Path
    manifest: dict[str, Any]


def _enabled_settings(folders: Iterable[str]) -> dict[str, bool]:
    """``enabledPlugins`` merged key by key, user < project < local (Claude
    Code's precedence; managed settings are not read here)."""
    files = [config_dir() / "settings.json"]
    for folder in folders:
        base = Path(folder) / ".claude"
        files += [base / "settings.json", base / "settings.local.json"]
    out: dict[str, bool] = {}
    for f in files:
        data = _read_json(f)
        plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
        if isinstance(plugins, dict):
            for key, value in plugins.items():
                if isinstance(key, str) and isinstance(value, bool):
                    out[key] = value
    return out


def _manifest(root: Path) -> dict[str, Any]:
    data = _read_json(root / ".claude-plugin" / "plugin.json")
    return data if isinstance(data, dict) else {}


def _on(pid: str, manifest: dict[str, Any], enabled: dict[str, bool]) -> bool:
    if pid in enabled:
        return enabled[pid]
    return _flag(manifest.get("defaultEnabled", True), True)


def _plugin(pid: str, entry_name: str, root: Path) -> Plugin | None:
    try:
        if not root.is_dir():
            return None
    except OSError:
        return None
    manifest = _manifest(root)
    name = str(manifest.get("name") or entry_name or root.name).strip()
    if not name or not all(_SEGMENT_RX.match(p) for p in name.split(":")) or ":" in name:
        return None
    label = _one_line(manifest.get("displayName"), 80) or name
    return Plugin(id=pid, name=name, label=label, root=root, manifest=manifest)


def _installed(folders: list[str]) -> list[tuple[str, str, Path]]:
    """``(id, entry name, install path)`` from ``installed_plugins.json`` —
    user and managed installs, and project/local installs of this folder."""
    data = _read_json(plugins_root() / "installed_plugins.json")
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return []
    from .sessions import folder_key

    keys = {folder_key(f) for f in folders if f}
    out: list[tuple[str, str, Path]] = []
    for pid, entries in plugins.items():
        if not isinstance(pid, str) or "@" not in pid:
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("installPath") or "").strip()
            if not path:
                continue
            scope = str(entry.get("scope") or "user").strip().lower()
            if scope in ("project", "local"):
                if folder_key(str(entry.get("projectPath") or "")) not in keys:
                    continue
            out.append((pid, pid.split("@", 1)[0], Path(path)))
            break
    return out


#: ``.claude.json`` is read for its signed-in account only (it can be large:
#: every project's history lives there).
MAX_ACCOUNT_FILE_BYTES = 32 * 1024 * 1024
_UUID_RX = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)


def _uuid(value: Any) -> str:
    text = str(value or "").strip()
    return text.lower() if _UUID_RX.match(text) else ""


def signed_in_bucket() -> str:
    """The synced-plugins folder Claude Code reads for the SIGNED-IN account
    (2.1.307: ``<organization>_<account>``, both lower case, the account
    ``unbound`` when it has none). The account comes from ``.claude.json``'s
    ``oauthAccount`` — in ``CLAUDE_CONFIG_DIR`` when set, else
    ``~/.claude.json``: the user's own sign-in. Claude Code also takes
    ``CLAUDE_CODE_ORGANIZATION_UUID`` / ``CLAUDE_CODE_ACCOUNT_UUID``, but in
    the daemon those can only be a PARENT Claude Code session's (a daemon
    started from one), never the user's — so they are not read here (the
    v1.303.1 rule for that session's markers). "" when no account can be read
    (then every account's folder is offered, as before). Read-only."""
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    path = (Path(raw).expanduser() / ".claude.json") if raw else (Path.home() / ".claude.json")
    oauth: Any = {}
    try:
        if path.is_file() and path.stat().st_size <= MAX_ACCOUNT_FILE_BYTES:
            data = json.loads(path.read_text(encoding="utf-8"))
            oauth = data.get("oauthAccount") if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        oauth = {}
    oauth = oauth if isinstance(oauth, dict) else {}
    org = _uuid(oauth.get("organizationUuid"))
    if not org:
        return ""
    account = _uuid(oauth.get("accountUuid")) or "unbound"
    return f"{org}_{account}"


def _synced() -> list[tuple[str, str, Path]]:
    """Plugins synced from claude.ai: ``plugins/synced/<account>/<name>/`` —
    v1.379.0 review: only the SIGNED-IN account's folder when the account is
    known (:func:`signed_in_bucket`; Claude Code reads only that one), every
    account's folder otherwise."""
    base = plugins_root() / "synced"
    out: list[tuple[str, str, Path]] = []
    try:
        buckets = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return out
    mine = signed_in_bucket()
    if mine:
        buckets = [b for b in buckets if b.name.lower() == mine]
    for bucket in buckets:
        try:
            dirs = sorted(p for p in bucket.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        for d in dirs:
            if (d / ".claude-plugin" / "plugin.json").is_file():
                out.append((f"{d.name}@synced", d.name, d))
    return out


def _skills_dir_plugins() -> list[tuple[str, str, Path]]:
    """Plugin folders saved under ``~/.claude/skills`` (``<name>@skills-dir``)."""
    base = config_dir() / "skills"
    out: list[tuple[str, str, Path]] = []
    try:
        dirs = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return out
    for d in dirs:
        if d.name == "synced":
            continue
        if (d / ".claude-plugin" / "plugin.json").is_file():
            name = str(_manifest(d).get("name") or d.name)
            out.append((f"{name}@skills-dir", name, d))
    return out


def enabled_plugins(folders: Iterable[str]) -> list[Plugin]:
    """The plugins Claude Code would load for these folders (see the module
    docstring). Never a catalog plugin, never one in the trash."""
    folders = [str(f) for f in folders if str(f or "").strip()]
    enabled = _enabled_settings(folders)
    root = plugins_root()
    blocked = [root / "marketplaces", root / ".trash"]
    seen: set[str] = set()
    out: list[Plugin] = []
    for pid, entry, path in [*_installed(folders), *_skills_dir_plugins(), *_synced()]:
        if len(out) >= MAX_PLUGINS:
            break
        if any(_inside(path, b) for b in blocked if b.exists()):
            continue
        plugin = _plugin(pid, entry, path)
        if plugin is None or not _on(pid, plugin.manifest, enabled):
            continue
        # Claude Code's name rule: one plugin per manifest name — an installed
        # one beats a skills-folder one beats a synced one (this order).
        if plugin.name in seen:
            continue
        seen.add(plugin.name)
        out.append(plugin)
    return out


def _plugin_commands(plugin: Plugin, budget: list[int]) -> list[Command]:
    spec = plugin.manifest.get("commands")
    prefix = plugin.name
    root = str(plugin.root)
    if spec is None:
        return _from_folder(plugin.root / "commands", SOURCE_PLUGIN, budget, prefix=prefix,
                            plugin=plugin.label, plugin_root=root)
    out: list[Command] = []
    if isinstance(spec, dict):
        for key, value in list(spec.items())[:MAX_COMMANDS]:
            if budget[0] <= 0:
                break
            if not isinstance(key, str) or not _SEGMENT_RX.match(key) or not isinstance(value, dict):
                continue
            extra = {k: v for k, v in (("description", value.get("description")),
                                       ("argument-hint", value.get("argumentHint")))
                     if v}
            name = f"{prefix}:{key}"
            budget[0] -= 1
            if isinstance(value.get("content"), str):
                body = value["content"][:MAX_COMMAND_BYTES]
                out.append(Command(
                    name=name, description=_one_line(extra.get("description")) or _description_of({}, body),
                    argument_hint=_one_line(extra.get("argument-hint"), 120), source=SOURCE_PLUGIN,
                    plugin=plugin.label, path="", plugin_root=root, inline=body,
                ))
            elif isinstance(value.get("source"), str):
                path = plugin.root / value["source"]
                if _inside(path, plugin.root) and path.is_file():
                    cmd = _command_from(path, name, SOURCE_PLUGIN, plugin.label, root, extra)
                    if cmd is not None:
                        out.append(cmd)
        return out
    paths = spec if isinstance(spec, list) else [spec]
    for raw in paths:
        if not isinstance(raw, str):
            continue
        path = plugin.root / raw
        if not _inside(path, plugin.root):
            continue
        if path.is_dir():
            out += _from_folder(path, SOURCE_PLUGIN, budget, prefix=prefix, plugin=plugin.label,
                                plugin_root=root)
        elif path.is_file() and path.suffix.lower() == ".md" and budget[0] > 0 and _SEGMENT_RX.match(path.stem):
            budget[0] -= 1
            cmd = _command_from(path, f"{prefix}:{path.stem}", SOURCE_PLUGIN, plugin.label, root)
            if cmd is not None:
                out.append(cmd)
    return out


def _plugin_skill_dirs(plugin: Plugin) -> list[Path]:
    """``skills/<name>/SKILL.md`` folders of a plugin (and the manifest's
    ``skills`` paths, which ADD to the default)."""
    bases = [plugin.root / "skills"]
    spec = plugin.manifest.get("skills")
    for raw in spec if isinstance(spec, list) else ([spec] if isinstance(spec, str) else []):
        if isinstance(raw, str):
            bases.append(plugin.root / raw)
    out: list[Path] = []
    for base in bases:
        if not _inside(base, plugin.root):
            continue
        if (base / "SKILL.md").is_file():
            out.append(base)
            continue
        try:
            for d in sorted(base.iterdir()):
                if d.is_dir() and not d.name.startswith(".") and (d / "SKILL.md").is_file():
                    out.append(d)
        except OSError:
            continue
    return out[:200]


def _same_dir(a: Any, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except (OSError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


def discover(folders: Iterable[str], *, registry: Any = None) -> Found:
    """Every command Claude Code would offer for ``folders`` (the session's
    working folder first), personal before project before plugins, each name
    once. ``registry`` (the app's skill registry) lets a plugin's skills be
    offered under Claude Code's ``<plugin>:<skill>`` names. Never raises."""
    found = Found()
    budget = [MAX_COMMANDS]
    try:
        folder_list = list(dict.fromkeys(str(f) for f in folders if str(f or "").strip()))
        seen: set[str] = set()

        def add(cmds: list[Command]) -> None:
            for c in cmds:
                key = c.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                found.commands.append(c)

        add(_from_folder(config_dir() / "commands", SOURCE_PERSONAL, budget))
        for folder in folder_list:
            add(_from_folder(Path(folder) / ".claude" / "commands", SOURCE_PROJECT, budget))
        for plugin in enabled_plugins(folder_list):
            add(_plugin_commands(plugin, budget))
            if registry is None:
                continue
            for d in _plugin_skill_dirs(plugin):
                got = _read_file(d / "SKILL.md")
                if got is None:
                    continue
                meta, _body = got
                skill_name = str(meta.get("name") or d.name).strip()
                if not skill_name or not _flag(meta.get("user-invocable", True), True):
                    continue
                try:
                    skill = registry.get(skill_name)
                except Exception:  # noqa: BLE001
                    skill = None
                if skill is None or not _same_dir(getattr(skill, "dir", ""), d):
                    continue  # the app would run a different copy: not offered
                alias = f"{plugin.name}:{skill_name}"
                if alias.lower() in seen:
                    continue
                seen.add(alias.lower())
                found.skills.append(SkillAlias(
                    name=alias, skill=skill_name, plugin=plugin.label,
                    description=_one_line(getattr(skill, "description", "")),
                ))
        if budget[0] <= 0:
            found.note = f"Only the first {MAX_COMMANDS} command files are offered."
    except Exception:  # noqa: BLE001 — a command file never breaks the menu
        log.warning("finding Claude Code's commands failed", exc_info=True)
    return found


def find(folders: Iterable[str], name: str) -> Command | None:
    """The command called ``name`` (case-insensitive), or None."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for c in discover(folders).commands:
        if c.name.lower() == wanted:
            return c
    return None


# --------------------------------------------------------------------------- #
# running one: Claude Code's expansion, without running anything
# --------------------------------------------------------------------------- #

#: ``!`command``` at the start of a line or after a blank, and a ```! block —
#: Claude Code's own two forms (2.1.307: ``(?<=^|\s)!`([^`]+)``` and
#: ``````!\s*\n?([\s\S]*?)\n?``````).
_BANG_INLINE_RX = re.compile(r"(?:^|(?<=\s))!`[^`]+`", re.M)
_BANG_BLOCK_RX = re.compile(r"```!\s*\n?[\s\S]*?\n?```")

#: The one sentence a command with shell lines gets: Iron Jarvis never runs
#: them; the model may, through its own shell tool (so the session's approval
#: rules and the git guard apply).
SHELL_LINES_NOTE = (
    "This command has shell lines (written as !`…` or in a ```! block). Iron Jarvis did not run "
    "them. Run each one yourself with your shell tool first, then use what it prints."
)

#: ``@path`` in a command's text: a file it wants read (not an e-mail address).
_AT_RX = re.compile(r"(?<![\w@`])@([^\s`'\"<>()\[\]{},;|]+)")


#: Placeholders a fill leaves alone and the words it put in (Claude Code's
#: own two private characters: U+FFFF keeps a ``$``, U+FFFE brackets a value
#: that came from the ARGUMENTS — read back by :func:`_marked_refs` and
#: :func:`_command_shell_lines` so the user's words never re-trigger a file or
#: a shell line, then removed).
_KEEP = "\uffff"
_SPAN = "\ufffe"


def _split_args(text: str) -> list[str]:
    """The arguments, one per word: whitespace splits, ``"…"`` and ``'…'``
    group words (Claude Code: "Indexed arguments use shell-style quoting").
    A BACKSLASH IS A LETTER — except right before a quote, which it keeps as
    a quote — because this is a Windows app and ``C:\\Users\\x`` must stay a
    path (a POSIX shell reading makes it ``C:Usersx``). An unclosed quote
    reads the line as plain words."""
    text = str(text or "")
    if not text.strip():
        return []
    out: list[str] = []
    cur: list[str] = []
    started = False
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'" and i + 1 < len(text) and text[i + 1] in "\"'":
            cur.append(text[i + 1])
            started = True
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = ""
            else:
                cur.append(ch)
        elif ch in "\"'":
            quote = ch
            started = True
        elif ch.isspace():
            if started:
                out.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(ch)
            started = True
        i += 1
    if quote:
        return text.split()
    if started:
        out.append("".join(cur))
    return out


def _fill(body: str, args: str, named: Iterable[str] = ()) -> str:
    """Claude Code 2.1.307's argument filling (its ``eBe``), every value that
    came from ``args`` bracketed by :data:`_SPAN`: ``\\$`` before a
    placeholder keeps a literal dollar; ``$name`` (a NAMED argument) is ""
    when its position has no word — and still counts as filled; ``$ARGUMENTS[N]``
    and ``$N`` are 0-BASED (``$0`` is the first word, ``$1`` the second — the
    docs' own table) and stay as written, unfilled, when there is no such
    word; ``$ARGUMENTS`` is the whole line as typed; when nothing was filled
    the words are added as ``ARGUMENTS: …``. A value's own ``$`` is never
    filled again."""
    words = _split_args(args)
    ordered = [n for n in dict.fromkeys(str(x) for x in named) if n and not n.isdigit() and len(n) <= 64][:64]
    names = sorted(ordered, key=len, reverse=True)
    alt = "|".join([r"\d", "ARGUMENTS", *[re.escape(n) + r"(?![\[\w])" for n in names]])
    text = str(body or "").replace(_KEEP, "\ufffd").replace(_SPAN, "\ufffd")
    text = re.sub(r"(?<!\\)\\\$(?=" + alt + ")", _KEEP, text)
    used = False

    def put(value: "str | None") -> str:
        nonlocal used
        used = True
        clean = str(value or "").replace(_KEEP, "\ufffd").replace(_SPAN, "\ufffd")
        return _SPAN + clean.replace("$", _KEEP) + _SPAN

    for name in names:
        index = ordered.index(name)
        value = words[index] if index < len(words) else ""
        text = re.sub(r"\$" + re.escape(name) + r"(?![\[\w])", lambda _m, v=value: put(v), text)

    def by_bracket(m: "re.Match[str]") -> str:
        i = int(m.group(1))
        return put(words[i]) if i < len(words) else _KEEP + m.group(0)[1:]

    def by_index(m: "re.Match[str]") -> str:
        i = int(m.group(1))
        return put(words[i]) if i < len(words) else m.group(0)

    text = re.sub(r"\$ARGUMENTS\[(\d+)\]", by_bracket, text)
    text = re.sub(r"\$(\d+)(?!\w)", by_index, text)
    if "$ARGUMENTS" in text:
        text = text.replace("$ARGUMENTS", put(str(args or "")))
    if not used and str(args or "").strip():
        text = f"{text}\n\nARGUMENTS: {put(str(args).strip())}"
    return text.replace(_KEEP, "$")


def _unmark(text: str) -> str:
    return text.replace(_SPAN, "")


def substitute(body: str, args: str, named: Iterable[str] = ()) -> str:
    """Claude Code's argument filling (:func:`_fill`), as plain text. Pure."""
    return _unmark(_fill(body, args, named))


def _spans(marked: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each bracketed value in ``marked``."""
    at = [i for i, ch in enumerate(marked) if ch == _SPAN]
    return list(zip(at[0::2], at[1::2]))


def _marked_refs(marked: str) -> list[str]:
    """The ``@path`` references whose ``@`` is the COMMAND's own text (its
    ``@$0`` reads the file the words name) — never one the user's words
    wrote: their words are words, as in any other message."""
    spans = _spans(marked)
    out: list[str] = []
    for m in _AT_RX.finditer(marked or ""):
        if any(a < m.start() < b for a, b in spans):
            continue
        raw = _unmark(m.group(1)).rstrip(".,:;!?")
        if raw and raw not in out:
            out.append(raw)
    return out


def _command_shell_lines(marked: str) -> bool:
    """Shell lines in the COMMAND's own text — a value the words filled in is
    read as plain letters, so a ``!`…``` the user typed is never one."""
    chars = list(marked)
    for a, b in _spans(marked):
        for i in range(a + 1, b):
            if not chars[i].isspace():
                chars[i] = "x"
    return has_shell_lines(_unmark("".join(chars)))


def at_paths(text: str) -> list[str]:
    """The ``@path`` references in a command's text, in order, each once."""
    out: list[str] = []
    for m in _AT_RX.finditer(text or ""):
        raw = m.group(1).rstrip(".,:;!?")
        if raw and raw not in out:
            out.append(raw)
    return out


def has_shell_lines(text: str) -> bool:
    return bool(_BANG_INLINE_RX.search(text or "") or _BANG_BLOCK_RX.search(text or ""))


@dataclass
class Expanded:
    name: str
    source: str
    plugin: str
    text: str
    #: Files the command named with ``@`` that passed the folder's checks.
    files: list[str] = field(default_factory=list)
    #: ``@`` references left as words (outside the folder, missing, past the
    #: read limit).
    left: list[str] = field(default_factory=list)
    shell_lines: bool = False
    scanned: bool = False
    #: Frontmatter the app read and did not obey (``allowed-tools``, ``model``).
    ignored: list[str] = field(default_factory=list)


def expand(cmd: Command, args: str, *, folder: str, read_room: int,
           event_bus: Any = None, session_id: "str | None" = None) -> Expanded:
    """The text ``cmd`` sends with ``args``, Claude Code's way — WITHOUT running
    anything: shell lines stay words plus :data:`SHELL_LINES_NOTE`; ``@path``
    references become the session's ``@`` files when they pass
    ``sessions.folder_files`` (inside ``folder``, readable, at most
    ``read_room`` of them), else stay as words; ``allowed-tools`` grants
    nothing and ``model`` is ignored (the session is held on its model). A
    project's or a plugin's command text is scanned through
    ``core/promptguard`` BEFORE the user's arguments go in (the arguments are
    the user's own words). Raises :class:`CommandRefused`. BLOCKING."""
    if cmd.inline:
        meta: dict[str, Any] = {}
        body = cmd.inline
    else:
        got = _read_file(Path(cmd.path))
        if got is None:
            raise CommandRefused(f"The command /{cmd.name} could not be read.", 409)
        meta, body = got
    body = body.strip()
    if not body:
        raise CommandRefused(f"The command /{cmd.name} has nothing to run: its file holds only a header.")
    ignored = [k for k in ("allowed-tools", "model") if meta.get(k)]
    scanned = cmd.source in SCANNED_SOURCES
    if scanned:
        from ..core.promptguard import guard

        label = f"plugin {cmd.plugin}" if cmd.source == SOURCE_PLUGIN else "this folder"
        body = guard(body, source=f"command /{cmd.name} ({label})", event_bus=event_bus,
                     session_id=session_id, cap=None)
    if folder:
        body = body.replace("${CLAUDE_PROJECT_DIR}", str(folder))
    if cmd.plugin_root:
        body = body.replace("${CLAUDE_PLUGIN_ROOT}", cmd.plugin_root.replace("\\", "/"))
    marked = _fill(body, args, cmd.arguments)
    text = _unmark(marked)
    if len(text) > MAX_EXPANDED_CHARS:
        raise CommandRefused(
            f"The command /{cmd.name} with these words is too long to send. Use shorter words.", 413,
        )
    # Review (v1.379.0): only the COMMAND's own text names files and shell
    # lines — a "@secret" or a "!`…`" in the words typed after the name is
    # the user's words, exactly as in any other message (Claude Code:
    # "argument values aren't re-expanded").
    shell = _command_shell_lines(marked)
    refs = _marked_refs(marked)
    if shell:
        text = f"{text}\n\n{SHELL_LINES_NOTE}"
    files: list[str] = []
    left: list[str] = []
    if folder:
        from .sessions import FolderFileRefused, folder_files

        for checked, ref in enumerate(refs):
            # Review: at most MAX_AT_REFS names are looked up on disk — a
            # repository's command file of thousands of "@x" is words past it.
            if len(files) >= max(0, read_room) or checked >= MAX_AT_REFS:
                left.append(ref)
                continue
            try:
                got_files = folder_files(folder, [ref])
            except FolderFileRefused:
                left.append(ref)
                continue
            for f in got_files:
                if f not in files:
                    files.append(f)
    else:
        left = refs
    return Expanded(
        name=cmd.name, source=cmd.source, plugin=cmd.plugin, text=text, files=files, left=left,
        shell_lines=shell, scanned=scanned, ignored=ignored,
    )


__all__ = [
    "Command",
    "CommandRefused",
    "Expanded",
    "Found",
    "MAX_COMMANDS",
    "MAX_COMMAND_BYTES",
    "SHELL_LINES_NOTE",
    "SkillAlias",
    "at_paths",
    "config_dir",
    "discover",
    "enabled_plugins",
    "expand",
    "find",
    "has_shell_lines",
    "plugins_root",
    "substitute",
]
