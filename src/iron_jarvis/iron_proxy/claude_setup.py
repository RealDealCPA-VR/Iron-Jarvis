"""A second Claude account starts with the OWNER's Claude Code setup (v1.424.0, D4).

THE GAP (audit D4): an Iron-Proxy account runs Claude Code with
``CLAUDE_CONFIG_DIR`` set to the account's own home (``accounts.make_lease``),
and Claude Code then reads EVERYTHING from that folder — so a Build terminal or
a Build › Code session on a second account had none of the user's MCP servers,
skills, commands, agents or hooks. The account this PC is signed in to (an
ADOPTED ``~/.claude``, v1.302.0) sets no variable and was always fine.

MEASURED (2026-11-02, this PC, ``claude`` 2.1.316, no model call). Every run was
``claude -p --input-format stream-json --output-format stream-json --verbose
--include-hook-events`` with ``HOME``/``USERPROFILE``/``APPDATA``/
``LOCALAPPDATA`` pointed at a SCRATCH "owner" folder and ``CLAUDE_CONFIG_DIR``
at a SCRATCH "account" folder (never a real home, never a home Iron-Proxy
holds), sending the stream-json control requests ``initialize`` (its answer
lists ``commands`` — skills included — and ``agents``) and ``mcp_status`` (each
server's name and status), then closing stdin. The owner had a user-scope MCP
server in ``.claude.json`` (a stdio stand-in that writes the argv and env it
received to a file), a ``SessionStart`` hook in ``.claude/settings.json`` (writes
a marker file), a skill, a command, an agent, and an installed plugin:

* owner, no ``CLAUDE_CONFIG_DIR``: everything listed, the server connected, the
  hook fired.
* second account (``CLAUDE_CONFIG_DIR`` = the scratch account): NOTHING of the
  owner's — the gap, reproduced.
* ``--mcp-config <file>`` + ``--settings <file>``: the server connects with its
  NAME unchanged, ``${VAR}`` references in ``args`` AND ``env`` expanded from
  the child's environment; the settings file's hook fires. Flags only reach a
  process Iron Jarvis starts itself, never a ``claude`` the user TYPES into a
  Build terminal — so they were not used.
* ``--add-dir <folder>`` whose ``.claude/{skills,commands,agents}`` are
  junctions to the owner's: listed with their own names — but an added
  directory is a WORKING directory (file tools write there with fewer asks),
  a widening the owner never chose. Not used.
* junctions INSIDE the account home: listed with their own names — but they
  write into a folder Iron-Proxy owns, and a ``Remove-Item -Recurse`` (Windows
  PowerShell 5.1) or another tool that follows junctions on that folder would
  delete the OWNER's real skills. Not used.
* ``--plugin-dir <installed plugin>``: its commands and skills listed
  (``plugin:command``).
* ``CLAUDE_CODE_PLUGIN_DIRS`` (an ENVIRONMENT variable, read from the binary:
  ``split`` on the path delimiter, absolute local paths only) naming a plugin
  folder of Iron Jarvis's own that holds ``.mcp.json``, ``hooks/hooks.json`` and
  junctions ``skills``/``commands``/``agents`` to the owner's folders (the
  shipped layout links each SKILL folder on its own inside a real ``skills``
  folder — the real-CLI pin measures that layout too), plus an
  installed plugin's folder after an ``;``: EVERYTHING listed, under the
  plugin's name — ``my:owner-cmd``, ``my:owner-skill``, agent
  ``my:owner-agent``, server ``plugin:my:owner-mcp`` connected, the hook fired,
  the installed plugin's ``ownplug:*`` too. ``${VAR}`` was expanded in a stdio
  server's ``args`` and ``env`` AND an http server's ``url`` (the query came
  through) and ``headers`` (a local listener received ``Authorization`` equal to
  the variable). The owner's ``skills/synced/<account>/…`` (claude.ai's own,
  per account) and ``skills/.trash/…`` were NOT loaded through the junction.
  Claude Code wrote only its own ``plugins/data/my-inline`` into the account.
* a ``${VAR}`` that is not set stays LITERAL (the server got the text
  ``${IJ_…}``, never another value); a plugin folder that does not exist is
  skipped and Claude Code starts normally.
* from the binary: when the managed policy sets ``disableSideloadFlags``,
  Claude Code REFUSES TO START while ``CLAUDE_CODE_PLUGIN_DIRS`` is set
  ("Unset CLAUDE_CODE_PLUGIN_DIRS … to run without those folders"); managed
  policy comes from ``C:\\Program Files\\ClaudeCode\\managed-settings.json``
  (+ its drop-in folder), ``HKLM``/``HKCU\\SOFTWARE\\Policies\\ClaudeCode``
  ``Settings``, ``CLAUDE_CODE_MANAGED_SETTINGS_PATH`` and the organization's
  server-managed settings cached as ``<account>/remote-settings.json``.

THE MECHANISM, chosen from those: the ONE environment variable. When a Build
terminal or a Build › Code process starts on a non-default Claude account
(``terminals.manager._with_pane_env`` — the one place both get their
environment), and this feature is on (``Config.accounts_share_claude_setup``,
read live), the child environment gains ``CLAUDE_CODE_PLUGIN_DIRS`` naming
Iron Jarvis's own plugin folder ``my`` (plus the owner's installed, enabled
user-scope plugins and plugins kept in the skills folder). It works for a
``claude`` the user types as much as for one Iron Jarvis starts, and writes
NOTHING into the account's home. The price is the names: Claude Code shows a
plugin's things under its name, so the owner's ``/pixio-story`` is
``/my:pixio-story`` and the server ``spark-worker`` is ``plugin:my:spark-worker``
on the other accounts (the card says so).

WHAT CROSSES, WHAT NEVER DOES. From the owner's config folder
(``CLAUDE_CONFIG_DIR`` as the app itself runs, else ``~/.claude``): the
user-scope ``mcpServers`` of ``.claude.json``, the ``hooks`` of
``settings.json`` (none when it says ``disableAllHooks``), each skill folder
(one link each: never ``synced`` — claude.ai's skills of the owner's ACCOUNT —
never a dot folder such as ``.trash``), the ``commands`` and ``agents`` folders,
and the installed plugins ``enabledPlugins`` does not turn off. NEVER: credentials (``.credentials.json``, OAuth), the
``projects`` / trust / history of ``.claude.json``, local-scope servers,
permissions, the settings ``env`` block, plugins synced from claude.ai (they are
the owner's ACCOUNT's), a marketplace catalog.

SECRETS. ``.mcp.json`` holds NO value of the owner's ``env``, ``headers``,
``args`` or ``url``: each becomes ``${IJ_MY_SETUP_<hash>}`` and the value rides
the child's ENVIRONMENT (memory only — a terminal's snapshot records the
account's home, never its environment; the code host keeps no environment on
disk). A value that is itself a reference (``${GITHUB_TOKEN}``) stays one. The
name is a hash of the server's WHOLE definition and the field, so a terminal
started before the owner changed a server never fills the new file with an old
value: it gets the literal reference (measured) and that server does not start
until a new terminal. A server with any other field that looks like a secret
(``detections.redact.mask`` would change it) is left out; so is a hook whose
definition looks like one. What was left out is said on the card.

THE FOLDER (``<userData>/claude-setup`` beside the state home, like the
downloaded backgrounds; a dev or test home keeps it at ``<home>/claude-setup``
and ``create_backup`` / the storage report leave it out — Python's ``os.walk``
and ``rglob`` follow a junction): Iron Jarvis's files are written only when
their bytes change, with the sha256 kept in ``state.json``; a file whose bytes
are not the ones Iron Jarvis wrote (a hand edit) is never overwritten or
removed; a junction is replaced only when ``state.json`` says Iron Jarvis made
it, a real folder in its place is left alone; nothing is ever deleted
recursively (a junction is removed with ``os.rmdir``, which never touches its
target). Turning the setting off, or an owner setup that became empty, takes
Iron Jarvis's files and junctions away, so a terminal started earlier finds no
folder (measured: Claude Code starts normally) — off is today exactly for the
next start.

Chat's Claude Code (``providers/adapters/claude_native``) is untouched: it runs
with ``--setting-sources ""``, ``--strict-mcp-config``,
``--disable-slash-commands`` and ``--tools ""`` on EVERY account, the one this
PC is signed in to included, so there is no owner setup to match.

Every function here BLOCKS (stats, small reads, writes) and never raises into
its caller: a failure is logged once and the environment is returned as it was.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("ironjarvis.iron_proxy")

#: The plugin's name — what Claude Code puts in front of everything it brings
#: (``/my:pixio-story``, ``plugin:my:spark-worker``).
PLUGIN_NAME = "my"
#: Iron Jarvis's folder (beside the state home, or in it for a dev/test home).
FOLDER = "claude-setup"
#: The plugin root inside the folder.
PLUGIN_DIR = PLUGIN_NAME
STATE_FILE = "state.json"
#: Claude Code's own variable (2.1.316): plugin folders, ``os.pathsep``-separated.
PLUGIN_DIRS_ENV = "CLAUDE_CODE_PLUGIN_DIRS"
#: The variables that carry the owner's values into the child.
PLACEHOLDER_PREFIX = "IJ_MY_SETUP_"
#: TESTS / Iron Jarvis's own reads only: the owner's config folder (its
#: ``.claude.json`` read from inside it). The suite points it at an empty folder.
OWNER_DIR_ENV = "IRONJARVIS_CLAUDE_OWNER_DIR"
#: The setting (``Config`` field and schema row).
SETTING = "accounts_share_claude_setup"

#: The Iron-Proxy provider whose accounts are Claude Code's.
PROVIDER = "anthropic"
HOME_VAR = "CLAUDE_CONFIG_DIR"

#: Bounds: servers copied, total characters of values put in the environment
#: (a Windows environment block holds 32,767), JSON files read.
MAX_SERVERS = 50
MAX_ENV_CHARS = 16_000
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_POLICY_BYTES = 1024 * 1024
#: Files looked at when counting commands / agents (a count, not a bound on use).
MAX_COUNT = 2000

_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
_NOTED: set[str] = set()
_NOTED_LOCK = threading.Lock()


def _note(where: str, exc: BaseException) -> None:
    """Log a failure ONCE per place (type only — never a path, value or token)."""
    with _NOTED_LOCK:
        if where in _NOTED:
            return
        _NOTED.add(where)
    log.info("claude setup: %s failed (%s) — carrying on without it", where, type(exc).__name__)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _key(path: str | Path) -> str:
    raw = str(path)
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    try:
        return os.path.normcase(os.path.realpath(os.path.expanduser(raw)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(raw))


def _is_network(path: str | Path) -> bool:
    raw = str(path).replace("/", "\\")
    if raw.startswith("\\\\?\\UNC\\"):
        return True
    return raw.startswith("\\\\") and not raw.startswith("\\\\?\\")


def _mask(text: str) -> str:
    from ..detections.redact import mask

    return mask(text)


def _secretish(value: Any) -> bool:
    """Would ``detections.redact.mask`` change this value's JSON?"""
    try:
        dump = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return True
    return _mask(dump) != dump


_JSON_CACHE: dict[str, tuple[int, int, Any]] = {}
_JSON_LOCK = threading.Lock()


def _read_json(path: Path, cap: int = MAX_JSON_BYTES) -> Any:
    """Parsed JSON or None; cached by (mtime, size). Never raises."""
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_size > cap or not path.is_file():
        return None
    k = str(path)
    with _JSON_LOCK:
        hit = _JSON_CACHE.get(k)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        data = None
    with _JSON_LOCK:
        if len(_JSON_CACHE) > 32:
            _JSON_CACHE.clear()
        _JSON_CACHE[k] = (st.st_mtime_ns, st.st_size, data)
    return data


# --------------------------------------------------------------------------- #
# the owner's setup
# --------------------------------------------------------------------------- #
def owner_paths() -> tuple[Path, Path]:
    """``(config folder, .claude.json)`` of the setup Claude Code uses with the
    app's OWN environment — the account this PC is signed in to: the test
    override, else ``CLAUDE_CONFIG_DIR`` (then ``.claude.json`` lives inside it,
    measured v1.343.0), else ``~/.claude`` and ``~/.claude.json``."""
    raw = os.environ.get(OWNER_DIR_ENV, "").strip()
    if raw:
        d = Path(raw).expanduser()
        return d, d / ".claude.json"
    raw = os.environ.get(HOME_VAR, "").strip()
    if raw:
        d = Path(raw).expanduser()
        return d, d / ".claude.json"
    home = Path.home()
    return home / ".claude", home / ".claude.json"


@dataclass
class OwnerSetup:
    """What a second account is given (see the module docstring)."""

    #: name -> definition as written to ``.mcp.json`` (values are references).
    servers: dict[str, Any] = field(default_factory=dict)
    #: placeholder variable -> the owner's value (the child's environment only).
    env: dict[str, str] = field(default_factory=dict)
    hooks: dict[str, Any] | None = None
    hook_count: int = 0
    #: The owner's SKILL folders, one link each (never ``synced``, a dot
    #: folder, or a plugin kept in the skills folder — see :func:`_skills`).
    skills: list[Path] = field(default_factory=list)
    #: The owner's ``commands`` / ``agents`` folders, linked whole (None =
    #: nothing there; neither ever holds anything of one account's).
    commands: Path | None = None
    agents: Path | None = None
    command_count: int = 0
    agent_count: int = 0

    @property
    def skill_count(self) -> int:
        return len(self.skills)
    #: Absolute local plugin folders (installed + skills-folder plugins).
    plugins: list[str] = field(default_factory=list)
    #: Plain words for what was left out.
    left_out: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.servers or self.hooks or self.skills or self.commands or self.agents or self.plugins)

    @property
    def plugin_needed(self) -> bool:
        """Does Iron Jarvis's own ``my`` plugin have anything to carry?"""
        return bool(self.servers or self.hooks or self.skills or self.commands or self.agents)


def _placeholder(server_key: str, where: str) -> str:
    digest = hashlib.sha256((server_key + "\0" + where).encode("utf-8")).hexdigest()[:16].upper()
    return PLACEHOLDER_PREFIX + digest


def _server(name: str, spec: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str], str]:
    """``(definition with references, {variable: value}, why left out)``."""
    try:
        whole = json.dumps({name: spec}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None, {}, "could not be read"
    key = hashlib.sha256(whole.encode("utf-8")).hexdigest()
    values: dict[str, str] = {}

    def hold(where: str, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if "${" in value and _mask(value) == value:
            return value  # the owner's own reference: Claude Code expands it as before
        var = _placeholder(key, where)
        values[var] = value
        return "${" + var + "}"

    out: dict[str, Any] = {}
    for k, v in spec.items():
        if not isinstance(k, str):
            return None, {}, "could not be read"
        if k in ("env", "headers") and isinstance(v, Mapping):
            if not all(isinstance(ek, str) for ek in v):
                return None, {}, "could not be read"
            out[k] = {ek: hold(f"{k}.{ek}", ev) for ek, ev in v.items()}
        elif k == "args" and isinstance(v, list):
            out[k] = [hold(f"args.{i}", a) for i, a in enumerate(v)]
        elif k == "url" and isinstance(v, str):
            out[k] = hold("url", v)
        else:
            if _secretish(v):
                return None, {}, "holds a value that looks like a key in a place Iron Jarvis does not copy"
            out[k] = v
    return out, values, ""


def _servers(raw: Any, setup: OwnerSetup) -> None:
    if not isinstance(raw, Mapping):
        return
    used = 0
    for name, spec in raw.items():
        if len(setup.servers) >= MAX_SERVERS:
            setup.left_out.append(f"MCP servers after the first {MAX_SERVERS}")
            break
        if not isinstance(name, str) or not _NAME_RX.match(name) or not isinstance(spec, Mapping):
            setup.left_out.append("an MCP server whose name or definition could not be read")
            continue
        out, values, why = _server(name, spec)
        if out is None:
            setup.left_out.append(f"the MCP server {name} ({why})")
            continue
        if any("\0" in v for v in values.values()):
            # An environment value cannot hold a NUL: the program would not start.
            setup.left_out.append(f"the MCP server {name} (a value Windows cannot pass on)")
            continue
        size = sum(len(k) + len(v) + 2 for k, v in values.items())
        if used + size > MAX_ENV_CHARS:
            setup.left_out.append(f"the MCP server {name} (its settings are too long to pass on)")
            continue
        used += size
        setup.servers[name] = out
        setup.env.update(values)


def _hooks(settings: Any, setup: OwnerSetup) -> None:
    if not isinstance(settings, Mapping) or settings.get("disableAllHooks") is True:
        return
    raw = settings.get("hooks")
    if not isinstance(raw, Mapping):
        return
    out: dict[str, Any] = {}
    left = 0
    for event, groups in raw.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, Mapping) or not isinstance(group.get("hooks"), list):
                continue
            rest = {k: v for k, v in group.items() if k != "hooks"}
            if _secretish(rest):
                left += len(group["hooks"])
                continue
            kept = []
            for hook in group["hooks"]:
                if not isinstance(hook, Mapping):
                    continue
                if _secretish(dict(hook)):
                    left += 1
                    continue
                kept.append(dict(hook))
            if kept:
                kept_groups.append({**rest, "hooks": kept})
                setup.hook_count += len(kept)
        if kept_groups:
            out[event] = kept_groups
    if left:
        setup.left_out.append(
            "1 hook that looks like it holds a key" if left == 1
            else f"{left} hooks that look like they hold a key"
        )
    setup.hooks = out or None


def _count_md(base: Path, depth: int) -> int:
    n = 0
    stack: list[tuple[Path, int]] = [(base, 0)]
    while stack and n < MAX_COUNT:
        folder, d = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir() and d < depth:
                    stack.append((Path(e.path), d + 1))
                elif e.is_file() and e.name.lower().endswith(".md"):
                    n += 1
            except OSError:
                continue
    return n


#: The skills folder's own sub-folders that are never a skill of the owner's:
#: claude.ai's synced skills are the owner's ACCOUNT's (``synced/<org>_<account>``).
_NOT_SKILLS = frozenset({"synced"})


def _skills(cfg: Path, setup: OwnerSetup) -> None:
    """Each skill folder of the owner's, ONE LINK EACH (v1.424.0 review: the
    whole folder linked would carry ``synced/<account>/…`` — claude.ai's skills of
    the owner's ACCOUNT — into another account the day Claude Code reads one
    level deeper; per-skill links make that Iron Jarvis's guarantee, not a
    measured habit). Never a dot folder (``.trash``), never ``synced``; a plugin
    kept here is passed on as a plugin."""
    base = cfg / "skills"
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return
    for e in entries:
        if e.name.startswith(".") or e.name.lower() in _NOT_SKILLS or not _NAME_RX.match(e.name):
            continue
        try:
            if not e.is_dir():
                continue
            if (Path(e.path) / ".claude-plugin" / "plugin.json").is_file():
                # A plugin kept in the skills folder (``<name>@skills-dir``):
                # Claude Code loads it as a plugin, so it is passed as one.
                setup.plugins.append(e.path)
            elif (Path(e.path) / "SKILL.md").is_file() and len(setup.skills) < MAX_COUNT:
                setup.skills.append(Path(e.path))
        except OSError:
            continue


def _installed_plugins(cfg: Path, settings: Any, setup: OwnerSetup) -> None:
    """User-scope installs ``enabledPlugins`` does not turn off — never one in
    the plugins trash or a marketplace catalog, never a synced one."""
    raw = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR", "").strip() if not os.environ.get(OWNER_DIR_ENV) else ""
    root = Path(raw).expanduser() if raw else cfg / "plugins"
    data = _read_json(root / "installed_plugins.json")
    plugins = data.get("plugins") if isinstance(data, Mapping) else None
    if not isinstance(plugins, Mapping):
        return
    enabled = settings.get("enabledPlugins") if isinstance(settings, Mapping) else None
    enabled = enabled if isinstance(enabled, Mapping) else {}
    blocked = [_key(root / "marketplaces"), _key(root / ".trash"), _key(root / "synced")]
    for pid, entries in plugins.items():
        if not isinstance(pid, str) or "@" not in pid:
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("scope") or "user").strip().lower() != "user":
                continue
            path = str(entry.get("installPath") or "").strip()
            if not path or _is_network(path) or not os.path.isabs(path) or not os.path.isdir(path):
                continue
            k = _key(path)
            if any(k == b or k.startswith(b + os.sep) for b in blocked):
                continue
            manifest = _read_json(Path(path) / ".claude-plugin" / "plugin.json")
            manifest = manifest if isinstance(manifest, Mapping) else {}
            on = enabled.get(pid)
            if on is None:
                on = manifest.get("defaultEnabled", True) is not False
            if on is not True:
                continue
            if str(manifest.get("name") or pid.split("@", 1)[0]) == PLUGIN_NAME:
                setup.left_out.append(f"the plugin {pid} (it has the same name Iron Jarvis uses)")
                continue
            setup.plugins.append(path)
            break


def read_owner() -> OwnerSetup:
    """BLOCKING: the owner's setup, built fresh (JSON parses are cached by
    mtime + size). Never raises — an unreadable part is simply absent."""
    setup = OwnerSetup()
    try:
        cfg, cfg_json = owner_paths()
        claude_json = _read_json(cfg_json)
        if isinstance(claude_json, Mapping):
            _servers(claude_json.get("mcpServers"), setup)
        settings = _read_json(cfg / "settings.json")
        _hooks(settings, setup)
        _skills(cfg, setup)
        cmds = cfg / "commands"
        if cmds.is_dir():
            n = _count_md(cmds, 5)
            if n:
                setup.commands, setup.command_count = cmds, n
        agents = cfg / "agents"
        if agents.is_dir():
            n = _count_md(agents, 5)
            if n:
                setup.agents, setup.agent_count = agents, n
        _installed_plugins(cfg, settings, setup)
        # A junction cannot lead to a network folder (and a link to one would be
        # an SMB connection at every Claude Code start): left out, said so.
        if any(_is_network(p) for p in setup.skills):
            setup.skills = []
            setup.left_out.append("your skills (they are on a network folder)")
        for kind in ("commands", "agents"):
            p = getattr(setup, kind)
            if p is not None and _is_network(p):
                setattr(setup, kind, None)
                setup.left_out.append(f"your {kind} (they are on a network folder)")
        seen: set[str] = set()
        plugins = []
        for p in setup.plugins:
            k = _key(p)
            if k not in seen:
                seen.add(k)
                plugins.append(p)
        setup.plugins = plugins
    except Exception as exc:  # noqa: BLE001 — never into the caller
        _note("read", exc)
    return setup


# --------------------------------------------------------------------------- #
# the account's organization may forbid added plugins
# --------------------------------------------------------------------------- #
def _forbids(data: Any) -> bool:
    if not isinstance(data, Mapping):
        return False
    if data.get("disableSideloadFlags") is True:
        return True
    inner = data.get("settings")
    return isinstance(inner, Mapping) and inner.get("disableSideloadFlags") is True


def _registry_forbids() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Policies\ClaudeCode") as k:
                value, _kind = winreg.QueryValueEx(k, "Settings")
        except OSError:
            continue
        try:
            if isinstance(value, str) and len(value) <= MAX_POLICY_BYTES and _forbids(json.loads(value)):
                return True
        except ValueError:
            continue
    return False


def managed_dir() -> Path:
    """Claude Code's machine-wide managed settings folder (read from the
    binary: ``C:\\Program Files\\ClaudeCode`` on Windows)."""
    if sys.platform == "win32":
        return Path("C:\\Program Files\\ClaudeCode")
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    return Path("/etc/claude-code")


def sideload_forbidden(account_home: str, env: Mapping[str, str] | None = None) -> bool:
    """BLOCKING: does a managed policy Claude Code reads for this account say
    ``disableSideloadFlags``? (Then ``CLAUDE_CODE_PLUGIN_DIRS`` would stop
    Claude Code from starting at all — measured in the binary.) Never raises;
    a policy that cannot be read counts as not forbidding."""
    try:
        paths = [Path(account_home) / "remote-settings.json", managed_dir() / "managed-settings.json"]
        override = str((env or {}).get("CLAUDE_CODE_MANAGED_SETTINGS_PATH") or "").strip()
        if override:
            paths.append(Path(override))
        drop_in = managed_dir() / "managed-settings.d"
        try:
            paths += sorted(p for p in drop_in.iterdir() if p.suffix.lower() == ".json")[:50]
        except OSError:
            pass
        for p in paths:
            if _forbids(_read_json(p, MAX_POLICY_BYTES)):
                return True
        return _registry_forbids()
    except Exception as exc:  # noqa: BLE001
        _note("policy", exc)
        return False


# --------------------------------------------------------------------------- #
# Iron Jarvis's folder
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()


def folder_for(home: str | os.PathLike[str]) -> Path:
    """``<userData>/claude-setup`` beside the state home when the desktop's
    ``logs`` folder is there (the backgrounds' rule), else ``<home>/claude-setup``."""
    home_path = Path(home)
    if (home_path.parent / "logs").is_dir():
        return home_path.parent / FOLDER
    return home_path / FOLDER


def _is_link(path: Path) -> bool:
    try:
        return os.path.islink(path) or bool(getattr(os.path, "isjunction", lambda _p: False)(path))
    except OSError:
        return False


def _link_target(path: Path) -> str:
    try:
        return os.readlink(path)
    except (OSError, ValueError):
        return ""


def _make_link(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


def _drop_link(link: Path) -> None:
    """Remove the LINK itself — never what it points at (``os.rmdir`` on a
    junction or a folder symlink removes the link; never a recursive delete)."""
    try:
        os.rmdir(link)
    except OSError:
        os.unlink(link)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except (FileNotFoundError, IsADirectoryError, PermissionError, NotADirectoryError):
        return None


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.ij-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _load_state(folder: Path) -> dict[str, Any]:
    data = _read(folder / STATE_FILE)
    try:
        doc = json.loads(data.decode("utf-8")) if data else {}
    except (ValueError, UnicodeDecodeError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["files"] = doc.get("files") if isinstance(doc.get("files"), dict) else {}
    doc["links"] = doc.get("links") if isinstance(doc.get("links"), dict) else {}
    return doc


def _save_state(folder: Path, doc: dict[str, Any]) -> None:
    _write(folder / STATE_FILE, json.dumps(doc, indent=2, sort_keys=True).encode("utf-8"))


def _wanted(setup: OwnerSetup) -> tuple[dict[str, bytes | None], dict[str, Path | None]]:
    plugin = {
        "name": PLUGIN_NAME,
        "version": "1.0.0",
        "description": "Your own Claude Code setup, added by Iron Jarvis on your other accounts.",
    }
    files: dict[str, bytes | None] = {
        ".claude-plugin/plugin.json": json.dumps(plugin, indent=2).encode("utf-8"),
        ".mcp.json": json.dumps({"mcpServers": setup.servers}, indent=2, sort_keys=True).encode("utf-8")
        if setup.servers else None,
        "hooks/hooks.json": json.dumps({"hooks": setup.hooks}, indent=2, sort_keys=True).encode("utf-8")
        if setup.hooks else None,
    }
    links: dict[str, Path | None] = {f"skills/{p.name}": p for p in setup.skills}
    links.update({"commands": setup.commands, "agents": setup.agents})
    return files, links


def _sync(
    folder: Path, files: dict[str, bytes | None], links: dict[str, Path | None], *, write: bool = True
) -> list[str]:
    """Make Iron Jarvis's files and links in ``folder/my`` what ``files`` and
    ``links`` say (None = not there), by the module docstring's rules. Returns
    plain words for what was left alone. ``write=False``: only the words (the
    card's read; nothing on disk changes)."""
    root = folder / PLUGIN_DIR
    doc = _load_state(folder)
    said: list[str] = []
    # A link Iron Jarvis made that is no longer wanted (a skill the owner
    # removed, everything when retiring) goes — removals first, so a link is
    # never made inside one that is about to go.
    links = {**{rel: None for rel in doc["links"] if rel not in links}, **links}
    links = dict(sorted(links.items(), key=lambda kv: kv[1] is not None))
    for rel, data in files.items():
        path = root / rel
        cur = _read(path)
        mine = doc["files"].get(rel)
        if _is_link(path):
            said.append(f"{rel} in Iron Jarvis's folder is a link, so it was left alone")
            continue
        if cur is not None and cur != data and (mine is None or _sha(cur) != mine):
            # Not the bytes Iron Jarvis wrote (or never wrote): a hand edit.
            said.append(f"{rel} in Iron Jarvis's folder was changed by hand, so it was left as it is")
            doc["files"].pop(rel, None)
            continue
        if not write:
            continue
        if data is None:
            if cur is not None:
                path.unlink()
            doc["files"].pop(rel, None)
            continue
        if cur != data:
            _write(path, data)
        doc["files"][rel] = _sha(data)
    # The skills folder is Iron Jarvis's own REAL folder; if something made it a
    # link, a skill's link would land in whatever it points at — never.
    skills_blocked = _is_link(root / "skills")
    if skills_blocked and any(rel.startswith("skills/") for rel in links):
        said.append("skills in Iron Jarvis's folder is a link Iron Jarvis did not make, so it was left alone")
    for rel, target in links.items():
        path = root / rel
        if skills_blocked and rel.startswith("skills/"):
            continue
        if os.path.lexists(path):
            if not _is_link(path):
                said.append(f"{rel} in Iron Jarvis's folder is a real folder, so it was left alone")
                doc["links"].pop(rel, None)
                continue
            if target is not None and _key(_link_target(path)) == _key(target):
                doc["links"][rel] = _key(target)
                continue
            if rel not in doc["links"]:
                said.append(f"{rel} in Iron Jarvis's folder is a link Iron Jarvis did not make, so it was left alone")
                continue
            if not write:
                continue
            _drop_link(path)
            doc["links"].pop(rel, None)
        if target is None or not write:
            doc["links"].pop(rel, None)
            continue
        _make_link(target, path)
        doc["links"][rel] = _key(target)
    if not write:
        return said
    # Empty folders go with the last file in them (``os.rmdir`` removes only an
    # EMPTY folder: anything someone put there keeps its folder).
    leaving = [root / "hooks", root / "skills"]
    if not files.get(".claude-plugin/plugin.json"):
        leaving += [root / ".claude-plugin", root]
    for sub in leaving:
        if _is_link(sub):
            continue  # never ``os.rmdir`` a link here: that is not an empty folder of ours
        try:
            os.rmdir(sub)
        except OSError:
            pass
    _save_state(folder, doc)
    return said


def folder_words(home: str | os.PathLike[str], setup: OwnerSetup) -> list[str]:
    """READ-ONLY: what the next start would leave alone in Iron Jarvis's
    folder (a hand edit, a link it did not make). Never raises."""
    try:
        folder = folder_for(home)
        if not folder.exists() or not setup.plugin_needed:
            return []
        files, links = _wanted(setup)
        return _sync(folder, files, links, write=False)
    except Exception as exc:  # noqa: BLE001
        _note("inspect", exc)
        return []


def sync(home: str | os.PathLike[str], setup: OwnerSetup) -> tuple[Path | None, list[str]]:
    """BLOCKING: Iron Jarvis's ``my`` plugin brought up to ``setup``. Returns
    ``(plugin folder or None when there is nothing to carry, words)``."""
    folder = folder_for(home)
    with _LOCK:
        if not setup.plugin_needed:
            return None, _retire_locked(folder)
        files, links = _wanted(setup)
        said = _sync(folder, files, links)
        root = folder / PLUGIN_DIR
        return (root if (root / ".claude-plugin" / "plugin.json").is_file() else None), said


def _retire_locked(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    files = {rel: None for rel in (".claude-plugin/plugin.json", ".mcp.json", "hooks/hooks.json")}
    # Every link Iron Jarvis made goes (``_sync`` adds each one it recorded).
    return _sync(folder, files, {})


def retire(home: str | os.PathLike[str]) -> list[str]:
    """BLOCKING: take Iron Jarvis's files and links away (the setting is off).
    A hand-edited file or a link Iron Jarvis did not make stays."""
    try:
        with _LOCK:
            return _retire_locked(folder_for(home))
    except Exception as exc:  # noqa: BLE001
        _note("retire", exc)
        return []


# --------------------------------------------------------------------------- #
# the one door: a child's environment on a Claude account
# --------------------------------------------------------------------------- #
def _config() -> Any:
    from . import service

    svc = service.current()
    return getattr(svc, "config", None) if svc is not None else None


def is_on(config: Any) -> bool:
    return bool(getattr(config, SETTING, True))


def account_home(accounts: Any) -> str:
    """The non-default Claude account home ``accounts`` (a
    ``pane_accounts.PaneAccounts``) SETS, else ""."""
    for lease in list(getattr(accounts, "leases", None) or []):
        if getattr(lease, "provider", "") != PROVIDER:
            continue
        for k, v in (getattr(lease, "env_set", None) or {}).items():
            if str(k).upper() == HOME_VAR and str(v or "").strip():
                return str(v)
    return ""


def _env_get(env: Mapping[str, str], name: str) -> tuple[str | None, str]:
    for k, v in env.items():
        if str(k).upper() == name:
            return k, str(v)
    return None, ""


def apply_for_accounts(env: dict[str, str], accounts: Any, *, config: Any = None) -> dict[str, str]:
    """BLOCKING: ``env`` (a child's environment with ``accounts`` already
    applied) plus the owner's setup when it runs Claude Code on a NON-DEFAULT
    account and the setting is on. Mutates and returns ``env``; never raises —
    anything that goes wrong leaves ``env`` exactly as it came."""
    home_dir = account_home(accounts)
    if not home_dir:
        return env
    try:
        cfg = config if config is not None else _config()
        if cfg is None or not getattr(cfg, "home", None):
            return env
        if not is_on(cfg):
            retire(cfg.home)
            return env
        owner_cfg, _json = owner_paths()
        if _key(home_dir) == _key(owner_cfg):
            return env  # the owner's own folder: Claude Code reads it already
        setup = read_owner()
        if setup.empty:
            retire(cfg.home)
            return env
        if sideload_forbidden(home_dir, env):
            # The account's organization forbids added plugins: with the
            # variable set Claude Code would not start at all.
            return env
        root, _said = sync(cfg.home, setup)
        dirs = ([str(root)] if root is not None else []) + list(setup.plugins)
        if not dirs:
            return env
        key, existing = _env_get(env, PLUGIN_DIRS_ENV)
        merged: list[str] = []
        seen: set[str] = set()
        for d in dirs + [p for p in existing.split(os.pathsep) if p.strip()]:
            k = _key(d)
            if k not in seen:
                seen.add(k)
                merged.append(d)
        if key is not None and key != PLUGIN_DIRS_ENV:
            env.pop(key, None)
        env[PLUGIN_DIRS_ENV] = os.pathsep.join(merged)
        if root is not None:
            env.update(setup.env)
        return env
    except Exception as exc:  # noqa: BLE001 — a terminal must start whatever happens here
        _note("apply", exc)
        return env


# --------------------------------------------------------------------------- #
# the card's line
# --------------------------------------------------------------------------- #
def _plural(n: int, one: str) -> str:
    return f"{n} {one}" + ("" if n == 1 else "s")


def card_line(config: Any) -> dict[str, Any]:
    """BLOCKING, read-only: ``{on, words}`` for the Iron-Proxy card's Claude
    group. Never raises."""
    try:
        on = is_on(config)
        if not on:
            return {
                "on": False,
                "words": "Claude Code on your other accounts starts without your MCP servers, skills "
                "and commands (turned off in Settings › Connections).",
            }
        setup = read_owner()
        if setup.empty:
            return {
                "on": True,
                "words": "Claude Code on your other accounts gets your MCP servers, skills and commands "
                "once you have some — none were found on this PC's own login.",
            }
        parts = []
        if setup.servers:
            parts.append(_plural(len(setup.servers), "MCP server"))
        if setup.skill_count:
            parts.append(_plural(setup.skill_count, "skill"))
        if setup.command_count:
            parts.append(_plural(setup.command_count, "command"))
        if setup.agent_count:
            parts.append(_plural(setup.agent_count, "agent"))
        if setup.hook_count:
            parts.append(_plural(setup.hook_count, "hook"))
        if setup.plugins:
            parts.append(_plural(len(setup.plugins), "plugin"))
        listed = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
        words = (
            f"Claude Code on your other accounts uses your MCP servers, skills and commands ({listed}); "
            f"they show with “{PLUGIN_NAME}:” in front, like /{PLUGIN_NAME}:<name>. "
            "Turn this off in Settings › Connections."
        )
        if setup.left_out:
            words += " Left out: " + "; ".join(setup.left_out[:5]) + "."
        home = getattr(config, "home", None)
        said = folder_words(home, setup) if home else []
        if said:
            text = "; ".join(said[:3])
            words += " " + text[:1].upper() + text[1:] + "."
        return {"on": True, "words": words}
    except Exception as exc:  # noqa: BLE001
        _note("card", exc)
        return {"on": is_on(config), "words": ""}
