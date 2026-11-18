"""Other apps connected to Iron Jarvis (v1.528.0): Claude desktop and Codex.

Claude desktop keeps its Chat-tab conversations on claude.ai, so Iron Jarvis
cannot read them from this PC. Instead the owner CONNECTS the app: one press
adds Iron Jarvis to that app's own list of connectors, and from then on the
app's model records each chat it has (``chat_record``) and can look up the
owner's work (``work_search``). Codex gets the same connector; its chats are
already read from its own files, so a Codex report only adds a summary.

THE KEY. Each connected app holds its OWN key: 32 random bytes, accepted at
``POST /mcp`` and nowhere else, opening ONE box (``record``: the two tools
above, ``mcpserver/server.CAPABILITY_TOOLS``) and nothing more: no browser,
no memory tools, no files, no shell. Unlike a Build pane's key (memory only,
it dies with its terminal) an app's key must outlive restarts, because the
app keeps running the bridge long after: so its DIGEST is kept in
``<home>/secrets/app-connections.json`` and the key itself in
``<home>/secrets/app-<app>.key``, where the bridge reads it
(``IRONJARVIS_MCP_TOKEN_FILE``). The key never appears in the other app's
config, in a command line, a log line, an event or an answer. Disconnect
deletes both, and the key stops working at once.

THE OTHER APP'S CONFIG is changed only by the owner's press, only by adding
or removing Iron Jarvis's own entry (named :data:`SERVER_NAME`), and safely:

* Claude desktop: ``claude_desktop_config.json`` (``mcpServers``), read as
  JSON and written back whole (atomically) with every other key as it was.
* Codex: ``config.toml``, where Iron Jarvis adds ONE marked block at the end
  and removes exactly that block; every other byte stays as it was. The new
  text must parse as TOML before it is written.
* A copy of the file as it was is kept in ``<home>/backups/other-apps/``
  before every change.
* A connector of the same name that Iron Jarvis did not write is never
  overwritten or removed (the press is refused and says so).

TESTS never touch the developer's own apps: ``tests/conftest.py`` points
:data:`TEST_ROOT` at an empty folder for the whole session, and Codex's home
is already isolated there (``CODEX_HOME``).

BLOCKING (file reads and writes): a route calls these off the event loop.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..core.logging import get_logger

logger = get_logger(__name__)

APP_CLAUDE = "claude-desktop"
APP_CODEX = "codex"
#: v1.530.0: Claude Code itself (a terminal, and Claude desktop's Code tab,
#: which reads Claude Code's own settings, not Claude desktop's). Its settings
#: are changed ONLY through its own command (``claude mcp add`` / ``remove``):
#: Claude Code rewrites its settings file all the time, so Iron Jarvis only
#: READS it, to check the entry.
APP_CLAUDE_CODE = "claude-code"
APPS: tuple[str, ...] = (APP_CLAUDE, APP_CODEX, APP_CLAUDE_CODE)
LABELS = {APP_CLAUDE: "Claude desktop", APP_CODEX: "Codex", APP_CLAUDE_CODE: "Claude Code"}
#: The most one ``claude mcp`` command may take.
CLI_TIMEOUT_S = 30.0
#: The connector's name in the other app's own list.
SERVER_NAME = "iron-jarvis"
#: The box an app's key opens (``mcpserver/server.CAPABILITY_TOOLS``).
CAPABILITY = "record"
#: The grant's pane id: ``app:<app>`` (``reported.SESSION_PREFIX`` reads it
#: back off ``mcp:app:<app>``).
PANE_PREFIX = "app:"
TOKEN_FILE_ENV = "IRONJARVIS_MCP_TOKEN_FILE"
TOKEN_BYTES = 32

#: Set by ``tests/conftest.py``: every other app's file is looked for under
#: this folder instead of the real one.
TEST_ROOT: str | None = None

CODEX_BEGIN = "# >>> Iron Jarvis connector (added by Iron Jarvis; Disconnect in Iron Jarvis removes it) >>>"
CODEX_END = "# <<< Iron Jarvis connector <<<"

NOT_INSTALLED = "{label} is not set up on this PC, so there is nothing to connect."
NOT_ANOTHER = "{label} already has a tool named \"iron-jarvis\" that Iron Jarvis did not add, so it was left as it is."
UNREADABLE = "{label}'s settings file could not be read, so nothing was changed: {why}"
NOT_WRITTEN = "{label}'s settings file could not be changed, so nothing was changed: {why}"
UNKNOWN_APP = "Iron Jarvis connects to Claude desktop, Claude Code or Codex."
CLI_FAILED = "Claude Code did not take the change, so nothing was changed: {why}"
NO_CLI_IN_TESTS = "A test never runs the real claude program."


class AppConnectError(Exception):
    """A connect or disconnect that changed nothing, in a plain sentence."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# where the other apps keep their settings
# --------------------------------------------------------------------------- #
def claude_config_path() -> Path:
    """Claude desktop's ``claude_desktop_config.json`` on this system."""
    if TEST_ROOT:
        return Path(TEST_ROOT) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "Claude" / "claude_desktop_config.json"


def codex_config_path() -> Path:
    """Codex's ``config.toml`` (``CODEX_HOME``, else ``~/.codex``)."""
    home = (os.environ.get("CODEX_HOME") or "").strip()
    if home:
        return Path(home) / "config.toml"
    if TEST_ROOT:
        return Path(TEST_ROOT) / ".codex" / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def claude_code_config_path() -> Path:
    """Claude Code's own settings file (``.claude.json`` in
    ``CLAUDE_CONFIG_DIR``, else in the home folder). READ only."""
    if TEST_ROOT:
        return Path(TEST_ROOT) / ".claude.json"
    home = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(home) / ".claude.json" if home else Path.home() / ".claude.json"


def config_path(app: str) -> Path:
    if app == APP_CLAUDE:
        return claude_config_path()
    if app == APP_CODEX:
        return codex_config_path()
    if app == APP_CLAUDE_CODE:
        return claude_code_config_path()
    raise AppConnectError(UNKNOWN_APP)


def claude_cli() -> "list[str] | None":
    """The argv prefix that starts Claude Code, or None (not installed).
    Through the Code view's own lookup, the one chokepoint tests replace."""
    from ..hosted.process import binary_for

    return binary_for("claude-code")


def run_cli(argv: list[str]) -> tuple[int, str]:
    """Run one ``claude`` command through the bounded process helper:
    ``(exit code, its words)``. Tests replace this; with ``TEST_ROOT`` set
    the real one refuses (a test never runs the real program)."""
    if TEST_ROOT:
        raise AppConnectError(NO_CLI_IN_TESTS)
    from ..core import procs

    res = procs.run(argv, timeout=CLI_TIMEOUT_S)
    if getattr(res, "timed_out", False):
        return 124, "it did not answer in time"
    words = (res.stdout or b"").decode("utf-8", "replace") + (res.stderr or b"").decode("utf-8", "replace")
    return int(res.returncode), words.strip()


def installed(app: str) -> bool:
    """Is the app set up on this PC? (Its settings folder exists; for Claude
    Code, its program is installed.)"""
    if app == APP_CLAUDE_CODE:
        return claude_cli() is not None
    try:
        return config_path(app).parent.is_dir()
    except AppConnectError:
        return False


def launch_argv(url: str) -> tuple[str, list[str]]:
    """``(command, args)`` that start Iron Jarvis's bridge: the packaged app
    runs its own hidden ``mcp-stdio`` command; a source checkout runs the
    module (``terminals/recipes.CodexRecipe._shim_launch``'s two shapes)."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["mcp-stdio", url]
    return sys.executable, ["-m", "iron_jarvis.mcpserver.stdio_shim", url]


# --------------------------------------------------------------------------- #
# the keys
# --------------------------------------------------------------------------- #
class AppConnections:
    """The connected apps' keys (digests on disk, the key in its own file)."""

    def __init__(self, home: Any) -> None:
        self.home = Path(home)
        self._lock = threading.Lock()

    # files --------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.home / "secrets" / "app-connections.json"

    def key_path(self, app: str) -> Path:
        return self.home / "secrets" / f"app-{app}.key"

    def _read_state(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out: dict[str, dict[str, str]] = {}
        for app, row in (data.items() if isinstance(data, dict) else []):
            if app in APPS and isinstance(row, dict) and isinstance(row.get("sha256"), str):
                out[app] = {"sha256": row["sha256"], "minted_at": str(row.get("minted_at") or "")}
        return out

    def _write_state(self, state: dict[str, dict[str, str]]) -> None:
        _atomic_write(self._state_path, json.dumps(state, indent=2, sort_keys=True))

    # the door -----------------------------------------------------------
    def resolve(self, token: str) -> Any:
        """The grant a connected app's key names, or None (constant time)."""
        from ..browser.panetokens import PaneGrant

        if not token:
            return None
        digest = _digest(token).encode("ascii")
        with self._lock:
            state = self._read_state()
        match = ""
        for app, row in state.items():
            if hmac.compare_digest(row["sha256"].encode("ascii"), digest):
                match = app
        if not match:
            return None
        try:
            minted = datetime.fromisoformat(state[match]["minted_at"])
        except ValueError:
            minted = datetime.now(timezone.utc)
        return PaneGrant(
            pane_id=PANE_PREFIX + match,
            capabilities=MappingProxyType({CAPABILITY: True}),
            minted_at=minted,
            live=True,
        )

    def has_pane(self, pane_id: str) -> bool:
        app = str(pane_id or "")[len(PANE_PREFIX):] if str(pane_id or "").startswith(PANE_PREFIX) else ""
        with self._lock:
            return bool(app) and app in self._read_state()

    def connected(self) -> set[str]:
        with self._lock:
            return set(self._read_state())

    # connect / disconnect ------------------------------------------------
    def connect(self, app: str, url: str) -> dict[str, Any]:
        """Mint the app's key and add Iron Jarvis to the app's connectors.
        A second press replaces the key (the app must be restarted)."""
        if app not in APPS:
            raise AppConnectError(UNKNOWN_APP)
        if not installed(app):
            raise AppConnectError(NOT_INSTALLED.format(label=LABELS[app]))
        token = secrets.token_urlsafe(TOKEN_BYTES)
        key_path = self.key_path(app)
        command, args = launch_argv(url)
        entry = {"command": command, "args": args, "env": {TOKEN_FILE_ENV: str(key_path)}}
        with self._lock:
            # The other app's file first: a refusal there leaves no key behind.
            _add_entry(app, entry, self.home)
            _atomic_write(key_path, token)
            state = self._read_state()
            state[app] = {"sha256": _digest(token), "minted_at": _now()}
            self._write_state(state)
        logger.info("connected %s to Iron Jarvis", LABELS[app])
        return self.status_of(app)

    def disconnect(self, app: str) -> dict[str, Any]:
        """Remove Iron Jarvis from the app's connectors and end its key."""
        if app not in APPS:
            raise AppConnectError(UNKNOWN_APP)
        with self._lock:
            state = self._read_state()
            state.pop(app, None)
            # The key goes first: whatever happens to the other file, the
            # app can no longer reach Iron Jarvis.
            self._write_state(state)
            try:
                self.key_path(app).unlink()
            except FileNotFoundError:
                pass
            if installed(app):
                _remove_entry(app, self.home)
        logger.info("disconnected %s from Iron Jarvis", LABELS[app])
        return self.status_of(app)

    def status_of(self, app: str) -> dict[str, Any]:
        state = self.connected()
        present = False
        try:
            present = _entry_present(app)
        except AppConnectError:
            present = False
        return {
            "app": app,
            "label": LABELS[app],
            "installed": installed(app),
            "connected": app in state and present,
            # Connected here but the app's own list lost the entry (the owner
            # removed it there, or reinstalled the app): Connect puts it back.
            "entry_missing": app in state and not present,
        }

    def status(self) -> list[dict[str, Any]]:
        return [self.status_of(app) for app in APPS]


# --------------------------------------------------------------------------- #
# the other apps' files
# --------------------------------------------------------------------------- #
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _keep_copy(path: Path, home: Path) -> None:
    """The file as it was, before Iron Jarvis changes it."""
    if not path.is_file():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = home / "backups" / "other-apps" / f"{path.parent.name}-{stamp}-{path.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)


def _ours(entry: Any) -> bool:
    env = entry.get("env") if isinstance(entry, dict) else None
    return isinstance(env, dict) and TOKEN_FILE_ENV in env


def _read_claude(app_label: str) -> tuple[dict[str, Any], str]:
    path = claude_config_path()
    try:
        raw = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        raise AppConnectError(UNREADABLE.format(label=app_label, why=exc.strerror or "it is locked")) from exc
    if not raw.strip():
        return {}, raw
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AppConnectError(UNREADABLE.format(label=app_label, why="it is not valid JSON")) from exc
    if not isinstance(data, dict):
        raise AppConnectError(UNREADABLE.format(label=app_label, why="it is not a settings object"))
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise AppConnectError(UNREADABLE.format(label=app_label, why="its list of tools is not a list of names"))
    return data, raw


def _codex_text() -> str:
    path = codex_config_path()
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        raise AppConnectError(UNREADABLE.format(label=LABELS[APP_CODEX], why=exc.strerror or "it is locked")) from exc


#: The block with the one blank line Connect puts before it, so removing it
#: gives back the file exactly as it was.
_BLOCK = re.compile(r"\n?" + re.escape(CODEX_BEGIN) + r".*?" + re.escape(CODEX_END) + r"\n?", re.S)


def _without_block(text: str) -> str:
    return _BLOCK.sub("", text)


def _codex_block(entry: dict[str, Any]) -> str:
    import tomli_w

    table = tomli_w.dumps({"mcp_servers": {SERVER_NAME: entry}})
    return f"{CODEX_BEGIN}\n{table.strip()}\n{CODEX_END}\n"


def _codex_has_other(text: str) -> bool:
    import tomllib

    try:
        data = tomllib.loads(_without_block(text))
    except tomllib.TOMLDecodeError as exc:
        raise AppConnectError(UNREADABLE.format(label=LABELS[APP_CODEX], why="it is not valid TOML")) from exc
    servers = data.get("mcp_servers")
    return isinstance(servers, dict) and SERVER_NAME in servers


_cc_cache: dict[str, Any] = {}


def _claude_code_entry() -> Any:
    """Claude Code's own ``mcpServers.iron-jarvis`` entry (user scope), READ
    from its settings file (cached per size and modified time: the file is
    large and rewritten often). None when there is none or it cannot be read."""
    path = claude_code_config_path()
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _cc_cache.get("key") != key:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        _cc_cache.update(key=key, entry=(servers or {}).get(SERVER_NAME) if isinstance(servers, dict) else None)
    return _cc_cache.get("entry")


def _entry_present(app: str) -> bool:
    if app == APP_CLAUDE:
        data, _raw = _read_claude(LABELS[app])
        return _ours((data.get("mcpServers") or {}).get(SERVER_NAME))
    if app == APP_CODEX:
        return bool(_BLOCK.search(_codex_text()))
    if app == APP_CLAUDE_CODE:
        return _ours(_claude_code_entry())
    raise AppConnectError(UNKNOWN_APP)


def _cli_change(argv_tail: list[str]) -> None:
    exe = claude_cli()
    if exe is None:
        raise AppConnectError(NOT_INSTALLED.format(label=LABELS[APP_CLAUDE_CODE]))
    code, words = run_cli([*exe, *argv_tail])
    if code != 0:
        raise AppConnectError(CLI_FAILED.format(why=(words.splitlines() or ["it said nothing"])[-1][:200]))
    _cc_cache.clear()


def _add_entry(app: str, entry: dict[str, Any], home: Path) -> None:
    label = LABELS[app]
    if app == APP_CLAUDE_CODE:
        current = _claude_code_entry()
        if current is not None and not _ours(current):
            raise AppConnectError(NOT_ANOTHER.format(label=label))
        path = claude_code_config_path()
        if path.is_file():
            _keep_copy(path, home)
        if current is not None:  # ours from an earlier Connect: replaced
            _cli_change(["mcp", "remove", "--scope", "user", SERVER_NAME])
        env = [f"--env={k}={v}" for k, v in entry["env"].items()]
        _cli_change(["mcp", "add", "--scope", "user", *env, SERVER_NAME, "--", entry["command"], *entry["args"]])
        return
    if app == APP_CLAUDE:
        data, _raw = _read_claude(label)
        servers = dict(data.get("mcpServers") or {})
        if SERVER_NAME in servers and not _ours(servers[SERVER_NAME]):
            raise AppConnectError(NOT_ANOTHER.format(label=label))
        servers[SERVER_NAME] = entry
        data["mcpServers"] = servers
        path = claude_config_path()
        try:
            _keep_copy(path, home)
            _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise AppConnectError(NOT_WRITTEN.format(label=label, why=exc.strerror or "it is locked")) from exc
        return
    import tomllib

    text = _codex_text()
    if _codex_has_other(text):
        raise AppConnectError(NOT_ANOTHER.format(label=label))
    base = _without_block(text)
    if base and not base.endswith("\n"):
        base += "\n"
    new = base + ("\n" if base.strip() else "") + _codex_block(entry)
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as exc:
        raise AppConnectError(NOT_WRITTEN.format(label=label, why="the result would not be valid TOML")) from exc
    path = codex_config_path()
    try:
        _keep_copy(path, home)
        _atomic_write(path, new)
    except OSError as exc:
        raise AppConnectError(NOT_WRITTEN.format(label=label, why=exc.strerror or "it is locked")) from exc


def _remove_entry(app: str, home: Path) -> None:
    label = LABELS[app]
    if app == APP_CLAUDE_CODE:
        if not _ours(_claude_code_entry()):
            return  # not ours (or gone): left as it is
        path = claude_code_config_path()
        if path.is_file():
            _keep_copy(path, home)
        _cli_change(["mcp", "remove", "--scope", "user", SERVER_NAME])
        return
    if app == APP_CLAUDE:
        data, _raw = _read_claude(label)
        servers = dict(data.get("mcpServers") or {})
        if not _ours(servers.get(SERVER_NAME)):
            return  # not ours (or gone): left as it is
        servers.pop(SERVER_NAME, None)
        if servers:
            data["mcpServers"] = servers
        else:
            # Connect may have made the list; an empty one goes with our entry.
            data.pop("mcpServers", None)
        path = claude_config_path()
        try:
            _keep_copy(path, home)
            _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise AppConnectError(NOT_WRITTEN.format(label=label, why=exc.strerror or "it is locked")) from exc
        return
    text = _codex_text()
    if not _BLOCK.search(text):
        return
    new = _without_block(text)
    path = codex_config_path()
    try:
        _keep_copy(path, home)
        _atomic_write(path, new)
    except OSError as exc:
        raise AppConnectError(NOT_WRITTEN.format(label=label, why=exc.strerror or "it is locked")) from exc


__all__ = [
    "APPS", "APP_CLAUDE", "APP_CLAUDE_CODE", "APP_CODEX", "AppConnectError", "claude_cli", "claude_code_config_path", "run_cli", "AppConnections", "CAPABILITY", "LABELS",
    "PANE_PREFIX", "SERVER_NAME", "TOKEN_FILE_ENV", "claude_config_path", "codex_config_path",
    "config_path", "installed", "launch_argv",
]
