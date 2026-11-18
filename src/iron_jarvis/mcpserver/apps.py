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

THE BRIDGE (v1.538.0). The entry names ``<home>/bridge/ironjarvis-bridge``,
a copy of the shipped program refreshed at every start (:func:`ensure_bridge`),
so an update or Quit never cuts a connected app; with no checked copy it
names the daemon's own ``mcp-stdio`` as before.

THE OTHER APP'S CONFIG is changed only by the owner's press (and, once, by
the start-up move to the bridge, :func:`migrate_entries`, decision D7), only
by adding or removing Iron Jarvis's own entry (named :data:`SERVER_NAME`),
and safely:

* Claude desktop: ``claude_desktop_config.json`` (``mcpServers``), read as
  JSON and written back whole (atomically) with every other key as it was.
* Codex: ``config.toml``, where Iron Jarvis adds ONE marked block at the end
  and removes its own tables from that block (v1.538.0, OT6: anything else
  written inside the markers stays); every other byte stays as it was. The
  new text must parse as TOML before it is written, and a removal must parse
  as the file minus Iron Jarvis's entry, or nothing is written.
* A copy of the file as it was is kept in ``<home>/backups/other-apps/``
  before every change (the newest 3 per file, OT10).
* A busy file is tried 3 times over about 3 s (C-OT-1).
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
import time
from dataclasses import dataclass
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

#: v1.538.0 (MS2, MS3): the small program a connected app starts. It lives in
#: ``<home>/bridge/``, OUTSIDE the install folder an update must replace (and,
#: on a Linux AppImage, outside the temporary mount), and its file name is
#: never the daemon's (the installer and the desktop's sweep kill
#: ``ironjarvis.exe`` by name). Built from ``stdio_shim.py`` alone.
BRIDGE_STEM = "ironjarvis-bridge"
#: One line beside the program: its content id (:func:`bridge_content_id`).
BRIDGE_ID_FILE = "bridge-id.txt"
#: What ``ironjarvis-bridge --check`` prints (and exits 0): no network, no key.
BRIDGE_OK_LINE = "ironjarvis-bridge ok"
#: Tests and a dev run: the folder holding the shipped program and its id.
BRIDGE_DIR_ENV = "IRONJARVIS_BRIDGE_DIR"
#: The most one ``--check`` run may take (Defender may scan a new program).
BRIDGE_CHECK_TIMEOUT_S = 20.0
#: ``os.replace`` of a just-written program may meet a scanner holding it.
SWAP_TRIES = 5
SWAP_PAUSE_S = 0.5
#: C-OT-1: another app's settings file busy for a moment is tried 3 times.
WRITE_TRIES = 3
WRITE_PAUSE_S = 1.5
#: OT10 (D15): the newest copies of another app's settings file kept.
KEEP_COPIES = 3

#: The waits above, injected (tests pass a recorder; never a real sleep).
_sleep = time.sleep


def _utcnow() -> datetime:
    """The clock the kept copies are stamped with (tests inject it)."""
    return datetime.now(timezone.utc)

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
#: OT4: Disconnect has already cut the key when the other app's file cannot be
#: changed, so "nothing was changed" would be false. ``{why}`` is the reason
#: the change failed (kept on :class:`AppConnectError`).
CUT_OFF_ENTRY_LEFT = (
    "{label} can no longer reach Iron Jarvis, but its settings still list it ({why}). "
    "Close {label}, then press Remove.")
CUT_OFF_CLI_LEFT = (
    "Claude Code can no longer reach Iron Jarvis, but Claude Code did not take it off its list ({why}). "
    "Press Remove to try again.")
#: OT4: Connect again ran ``claude mcp remove`` and then ``add`` failed.
READD_FAILED = (
    "Claude Code took Iron Jarvis off its list but did not add it back ({why}). "
    "Press Connect Claude Code again.")
#: OT6: removing Iron Jarvis's own Codex tables would not give back the file
#: minus those tables, so nothing is written (a copy is kept first).
CODEX_CANNOT_UNDO = (
    "Codex's settings changed in a way Iron Jarvis cannot safely undo, so its file was left as it is. "
    "Remove \"iron-jarvis\" in Codex's settings yourself; a copy of the file is in Iron Jarvis's "
    "backups folder.")
CUT_OFF_CODEX_CANNOT_UNDO = (
    "Codex can no longer reach Iron Jarvis, but its settings still list it: they changed in a way "
    "Iron Jarvis cannot safely undo, so its file was left as it is. Remove \"iron-jarvis\" in Codex's "
    "settings yourself; a copy of the file is in Iron Jarvis's backups folder.")

#: MS5: what each app needs after Connect (the route adds it after
#: "<label> is connected.").
AFTER_CONNECT = {
    APP_CLAUDE: "Restart Claude desktop to start using it.",
    APP_CODEX: "New Codex chats use it; restart Codex if it is open.",
    APP_CLAUDE_CODE: "New Claude Code sessions use it; one that is open now needs a restart.",
}


class AppConnectError(Exception):
    """A connect or disconnect that failed, in a plain sentence. ``why`` keeps
    the plain reason it was raised with (OT4), so a caller can re-word it."""

    def __init__(self, message: str, *, why: str = "", whole: bool = False) -> None:
        super().__init__(message)
        self.why = why or message
        #: The sentence already says everything (OT6): never wrapped again.
        self.whole = whole


def _os_why(exc: OSError) -> str:
    """The plain reason a file could not be read or written."""
    if getattr(exc, "winerror", None) in (32, 33):  # sharing / lock violation
        return "the file is in use"
    return exc.strerror or "it is locked"


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


def launch_argv(url: str, home: Any = None) -> tuple[str, list[str]]:
    """``(command, args)`` that start Iron Jarvis's bridge.

    v1.538.0 (MS2, MS3): the checked copy in ``<home>/bridge/`` when there is
    one (it outlives updates and the AppImage mount). Otherwise, the way back:
    the packaged app runs its own hidden ``mcp-stdio`` command; a source
    checkout runs the module (``terminals/recipes.CodexRecipe._shim_launch``'s
    two shapes)."""
    if home is not None:
        bridge = installed_bridge(home)
        if bridge is not None:
            return str(bridge), [url]
    if getattr(sys, "frozen", False):
        return sys.executable, ["mcp-stdio", url]
    return sys.executable, ["-m", "iron_jarvis.mcpserver.stdio_shim", url]


# --------------------------------------------------------------------------- #
# the bridge program (v1.538.0, MS2 / MS3)
# --------------------------------------------------------------------------- #
def bridge_content_id(source_bytes: bytes) -> str:
    """The bridge's content id: the lowercase sha256 of ``stdio_shim.py``'s
    bytes with CRLF line endings made LF. The build writes it beside the
    program (``bridge-id.txt``); a re-signed program with the same source has
    the same id, so it is never copied again for nothing."""
    return hashlib.sha256(bytes(source_bytes).replace(b"\r\n", b"\n")).hexdigest()


def _exe_suffix() -> str:
    return ".exe" if sys.platform == "win32" else ""


def bridge_name() -> str:
    """``ironjarvis-bridge.exe`` (Windows) or ``ironjarvis-bridge``."""
    return BRIDGE_STEM + _exe_suffix()


def bridge_path(home: Any) -> Path:
    """Where the connected apps' bridge lives: ``<home>/bridge/<name>``."""
    return Path(home) / "bridge" / bridge_name()


def shipped_bridge_dir() -> "Path | None":
    """The folder the install ships the bridge in, or None (a source run:
    the entry stays the module, as before). ``IRONJARVIS_BRIDGE_DIR`` first;
    frozen, the daemon is ``<resources>/daemon/ironjarvis(.exe)``, so it is
    ``<resources>/bridge``."""
    env = (os.environ.get(BRIDGE_DIR_ENV) or "").strip()
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "bridge"
    return None


def _read_id(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, ValueError):
        return ""
    return lines[0].strip().lower() if lines else ""


def installed_bridge(home: Any) -> "Path | None":
    """The checked copy in ``<home>/bridge/``, or None. Its id file is written
    only after a copy answered ``--check`` and was swapped in."""
    dest = bridge_path(home)
    if dest.is_file() and _read_id(dest.parent / BRIDGE_ID_FILE):
        return dest
    return None


def _bridge_answers(path: Path) -> bool:
    """Does the program at ``path`` answer ``--check`` (exit 0, the one ok
    line)? Through the bounded process helper; never raises."""
    from ..core import procs

    try:
        res = procs.run([str(path), "--check"], timeout=BRIDGE_CHECK_TIMEOUT_S)
    except (OSError, ValueError):
        return False
    if getattr(res, "timed_out", False) or int(res.returncode) != 0:
        return False
    out = (res.stdout or b"").decode("utf-8", "replace").strip()
    return out == BRIDGE_OK_LINE


@dataclass(frozen=True)
class BridgeState:
    """What :func:`ensure_bridge` found or did."""

    #: A checked copy is in ``<home>/bridge/`` (the entries name it).
    installed: bool
    #: Its path ("" when there is none).
    path: str
    #: Its content id ("" when there is none).
    content_id: str
    #: This call swapped a new copy in.
    changed: bool = False
    #: Why nothing was swapped in, in plain words (the log, the doctor).
    note: str = ""


_bridge_lock = threading.Lock()


def _sweep_old(folder: Path, name: str) -> None:
    """Old copies renamed aside while they ran, and a ``.new`` a stopped run
    left: removed when free (a copy still running stays until a later start)."""
    try:
        items = list(folder.iterdir())
    except OSError:
        return
    for item in items:
        if item.name.startswith(BRIDGE_STEM + ".old-") or item.name == name + ".new":
            try:
                item.unlink()
            except OSError:
                pass


def _aside_name(folder: Path) -> Path:
    n = 1
    while (folder / f"{BRIDGE_STEM}.old-{n}{_exe_suffix()}").exists():
        n += 1
    return folder / f"{BRIDGE_STEM}.old-{n}{_exe_suffix()}"


def ensure_bridge(home: Any) -> BridgeState:
    """Copy the shipped bridge to ``<home>/bridge/`` when its content id
    differs from the installed one, and only once the new copy answered
    ``--check``. BLOCKING (a file copy and one bounded child run): the daemon
    calls it off the event loop. Never raises.

    The new copy is written as ``<name>.new`` and checked first; a copy that
    fails is removed and the installed one (if any) stays, so with no checked
    bridge the entries keep naming the daemon's own ``mcp-stdio`` (the way
    back). On Windows the installed copy may be RUNNING (a connected app
    started it): a running program may be renamed, not overwritten, so it is
    renamed aside first and removed once free. The swap is tried again a few
    times, because a scanner may hold the just-written file."""
    home = Path(home)
    dest = bridge_path(home)
    folder = dest.parent
    with _bridge_lock:
        _sweep_old(folder, dest.name)
        have = _read_id(folder / BRIDGE_ID_FILE) if dest.is_file() else ""

        def state(changed: bool = False, note: str = "") -> BridgeState:
            cid = _read_id(folder / BRIDGE_ID_FILE) if dest.is_file() else ""
            return BridgeState(bool(cid), str(dest) if cid else "", cid, changed, note)

        src_dir = shipped_bridge_dir()
        if src_dir is None:
            return state(note="Iron Jarvis runs from its source, so there is no bridge program to copy.")
        src = src_dir / bridge_name()
        want = _read_id(src_dir / BRIDGE_ID_FILE)
        if not src.is_file() or not want:
            logger.warning("the connector bridge is missing from %s", src_dir)
            return state(note="The bridge program is missing from this install.")
        if want == have:
            return state()
        new = folder / (dest.name + ".new")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, new)
            if sys.platform != "win32":
                os.chmod(new, 0o755)
        except OSError as exc:
            logger.warning("the connector bridge could not be copied: %s", _os_why(exc))
            _unlink_quietly(new)
            return state(note="The bridge program could not be copied.")
        if not _bridge_answers(new):
            logger.warning("the new connector bridge did not answer its check; the current one stays")
            _unlink_quietly(new)
            return state(note="The new bridge program did not answer its check, so it was not used.")
        aside: "Path | None" = None
        if sys.platform == "win32" and dest.exists():
            try:
                aside = _aside_name(folder)
                os.replace(dest, aside)
            except OSError as exc:
                logger.warning("the running connector bridge could not be moved aside: %s", _os_why(exc))
                _unlink_quietly(new)
                return state(note="The bridge program in use could not be replaced.")
        swapped = False
        for attempt in range(SWAP_TRIES):
            try:
                os.replace(new, dest)
                swapped = True
                break
            except OSError:
                if attempt + 1 < SWAP_TRIES:
                    _sleep(SWAP_PAUSE_S)
        if not swapped:
            if aside is not None:
                try:
                    os.replace(aside, dest)
                except OSError:
                    pass
            _unlink_quietly(new)
            logger.warning("the connector bridge could not be swapped in")
            return state(note="The bridge program in use could not be replaced.")
        try:
            _atomic_write(folder / BRIDGE_ID_FILE, want + "\n")
        except OSError as exc:
            logger.warning("the connector bridge's id could not be written: %s", _os_why(exc))
        if aside is not None:
            _unlink_quietly(aside)  # still running: a later start removes it
        logger.info("the connector bridge was updated")
        return state(changed=True)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def migrate_entries(home: Any, url: "str | None" = None) -> list[str]:
    """D7: at start, once a checked bridge is installed, each connected app
    whose OWN entry names another program is moved to the bridge (same key
    file; a copy of its settings kept first), and its row asks for one restart
    (``first_used_at`` is cleared). Under the Connect lock, so a press cannot
    race it. Returns the apps moved. BLOCKING; never raises."""
    if url is None:
        from ..platform import own_mcp_url

        url = own_mcp_url()
    return AppConnections(home).migrate_entries(url)


# --------------------------------------------------------------------------- #
# the keys
# --------------------------------------------------------------------------- #
_locks_guard = threading.Lock()
_home_locks: dict[str, tuple[threading.Lock, threading.Lock]] = {}


def _locks_for(home: Path) -> tuple[threading.Lock, threading.Lock]:
    """One pair of locks per home folder, shared by every
    :class:`AppConnections` on it (the daemon's own and the start-up
    migration's)."""
    try:
        key = os.path.normcase(str(home.resolve()))
    except OSError:
        key = os.path.normcase(str(home))
    with _locks_guard:
        pair = _home_locks.get(key)
        if pair is None:
            pair = _home_locks[key] = (threading.Lock(), threading.Lock())
        return pair


class AppConnections:
    """The connected apps' keys (digests on disk, the key in its own file).

    v1.538.0 (MS7): two locks. ``_lock`` covers ONLY the state file and the
    key files (read on every call, so it is never held long); ``_change_lock``
    serialises Connect, Disconnect and the start-up move (D7), and is held
    across the change to the other app's settings and the ``claude mcp`` runs,
    so a slow Connect never holds up a connected app's calls."""

    def __init__(self, home: Any) -> None:
        self.home = Path(home)
        self._lock, self._change_lock = _locks_for(self.home)

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
                # C-OT-2: kept, or the next press's write-back would lose it.
                if isinstance(row.get("first_used_at"), str) and row["first_used_at"]:
                    out[app]["first_used_at"] = row["first_used_at"]
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
        if not state[match].get("first_used_at"):
            self.note_first_use(match, sha256=state[match]["sha256"])
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

    def note_first_use(self, app: str, *, sha256: str = "") -> None:
        """C-OT-2: the app's key was used for the first time since Connect
        (or since the start-up move): written ONCE per key, never per call.
        ``resolve`` calls it, so every request through ``POST /mcp`` counts.
        ``sha256``: only for that key (a press may have replaced it since)."""
        with self._lock:
            state = self._read_state()
            row = state.get(app)
            if row is None or row.get("first_used_at") or (sha256 and row["sha256"] != sha256):
                return
            row["first_used_at"] = _now()
            try:
                self._write_state(state)
            except OSError as exc:
                logger.warning("could not note the first use of %s's key: %s", LABELS.get(app, app), _os_why(exc))

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
        with self._change_lock:
            ensure_bridge(self.home)  # a press before the start-up step ran
            command, args = launch_argv(url, self.home)
            entry = {"command": command, "args": args, "env": {TOKEN_FILE_ENV: str(key_path)}}
            # The other app's file first: a refusal there leaves no key behind.
            # Outside ``_lock``: Claude Code's own command may take a while.
            _add_entry(app, entry, self.home)
            with self._lock:
                _atomic_write(key_path, token)
                state = self._read_state()
                # A new key: no first use yet (the row says so).
                state[app] = {"sha256": _digest(token), "minted_at": _now()}
                self._write_state(state)
        logger.info("connected %s to Iron Jarvis", LABELS[app])
        return self.status_of(app)

    def disconnect(self, app: str) -> dict[str, Any]:
        """Remove Iron Jarvis from the app's connectors and end its key.
        Also the row's Remove (OT4): with no key left, it only removes our own
        entry."""
        if app not in APPS:
            raise AppConnectError(UNKNOWN_APP)
        with self._change_lock:
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
                try:
                    _remove_entry(app, self.home)
                except AppConnectError as exc:
                    # OT4: the key is gone, so "nothing was changed" is false.
                    if exc.whole and app == APP_CODEX:
                        raise AppConnectError(CUT_OFF_CODEX_CANNOT_UNDO, why=exc.why) from exc
                    if app == APP_CLAUDE_CODE:
                        raise AppConnectError(CUT_OFF_CLI_LEFT.format(why=exc.why), why=exc.why) from exc
                    raise AppConnectError(
                        CUT_OFF_ENTRY_LEFT.format(label=LABELS[app], why=exc.why), why=exc.why) from exc
        logger.info("disconnected %s from Iron Jarvis", LABELS[app])
        return self.status_of(app)

    def migrate_entries(self, url: str) -> list[str]:
        """See the module function :func:`migrate_entries` (D7)."""
        bridge = installed_bridge(self.home)
        if bridge is None:
            return []  # the way back: nothing moves before a checked copy
        moved: list[str] = []
        with self._change_lock:
            for app in sorted(self.connected()):
                try:
                    if not installed(app):
                        continue
                    entry = _our_entry(app)
                    if entry is None or not _stale(entry, bridge):
                        continue
                    new = {"command": str(bridge), "args": [url],
                           "env": {TOKEN_FILE_ENV: str(self.key_path(app))}}
                    _add_entry(app, new, self.home)
                except (AppConnectError, OSError) as exc:
                    # Claude Code's ``add`` failing after its ``remove`` leaves
                    # the row "entry_missing": it says Connect again.
                    why = exc.why if isinstance(exc, AppConnectError) else _os_why(exc)
                    logger.warning("could not move %s to the connector bridge: %s", LABELS[app], why)
                    continue
                with self._lock:
                    state = self._read_state()
                    if app in state:
                        state[app].pop("first_used_at", None)  # the row asks for one restart
                        self._write_state(state)
                moved.append(app)
        if moved:
            logger.info("moved %d connected app(s) to the connector bridge", len(moved))
        return moved

    def status_of(self, app: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
        present = False
        try:
            present = _entry_present(app)
        except AppConnectError:
            present = False
        connected = app in state and present
        stale = False
        if connected:
            entry = _our_entry(app)
            stale = entry is not None and _stale(entry, Path(launch_argv("", self.home)[0]))
        return {
            "app": app,
            "label": LABELS[app],
            "installed": installed(app),
            "connected": connected,
            # Connected here but the app's own list lost the entry (the owner
            # removed it there, or reinstalled the app): Connect puts it back.
            "entry_missing": app in state and not present,
            # OT4: our entry is still in the app's list but its key is gone
            # (a Disconnect that could not change the file): Remove finishes it.
            "entry_left": app not in state and present,
            # MS2 / MS3: the entry names another program than the current one
            # (an older install's, or a mount that is gone).
            "entry_stale": stale,
            # C-OT-2: connected, and the app has not used its key yet.
            "waiting_for_app": connected and not state[app].get("first_used_at"),
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


def _write_other(path: Path, text: str) -> None:
    """C-OT-1: write ANOTHER app's settings file, tried :data:`WRITE_TRIES`
    times over about 3 s when it is busy (the app writing its own settings at
    that moment). Iron Jarvis's own files go through :func:`_atomic_write`."""
    for attempt in range(WRITE_TRIES):
        try:
            _atomic_write(path, text)
            return
        except OSError:
            if attempt + 1 >= WRITE_TRIES:
                raise
            _sleep(WRITE_PAUSE_S)


_COPY_STAMP = re.compile(r"\d{8}-\d{6}")


def _keep_copy(path: Path, home: Path) -> None:
    """The file as it was, before Iron Jarvis changes it. OT10 (D15): only
    the newest :data:`KEEP_COPIES` copies of that file are kept (Iron Jarvis's
    own copies in its own folder; they may hold the other app's tokens)."""
    if not path.is_file():
        return
    stamp = _utcnow().strftime("%Y%m%d-%H%M%S")
    folder = home / "backups" / "other-apps"
    prefix, suffix = f"{path.parent.name}-", f"-{path.name}"
    dest = folder / f"{prefix}{stamp}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    try:
        mine = sorted(
            p for p in folder.iterdir()
            if p.name.startswith(prefix) and p.name.endswith(suffix)
            and _COPY_STAMP.fullmatch(p.name[len(prefix):len(p.name) - len(suffix)])
        )
    except OSError:
        return
    for old in mine[:-KEEP_COPIES]:
        _unlink_quietly(old)


def _ours(entry: Any) -> bool:
    env = entry.get("env") if isinstance(entry, dict) else None
    return isinstance(env, dict) and TOKEN_FILE_ENV in env


def _read_claude(app_label: str) -> tuple[dict[str, Any], str]:
    path = claude_config_path()
    try:
        raw = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        why = _os_why(exc)
        raise AppConnectError(UNREADABLE.format(label=app_label, why=why), why=why) from exc
    if not raw.strip():
        return {}, raw
    try:
        data = json.loads(raw)
    except ValueError as exc:
        why = "it is not valid JSON"
        raise AppConnectError(UNREADABLE.format(label=app_label, why=why), why=why) from exc
    if not isinstance(data, dict):
        why = "it is not a settings object"
        raise AppConnectError(UNREADABLE.format(label=app_label, why=why), why=why)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        why = "its list of tools is not a list of names"
        raise AppConnectError(UNREADABLE.format(label=app_label, why=why), why=why)
    return data, raw


def _codex_text() -> str:
    path = codex_config_path()
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        why = _os_why(exc)
        raise AppConnectError(UNREADABLE.format(label=LABELS[APP_CODEX], why=why), why=why) from exc


#: The block with the one blank line Connect puts before it, so removing it
#: gives back the file exactly as it was.
_BLOCK = re.compile(r"\n?" + re.escape(CODEX_BEGIN) + r".*?" + re.escape(CODEX_END) + r"\n?", re.S)


#: A table header naming Iron Jarvis's own Codex entry or one of its
#: sub-tables (bare or quoted name).
_OUR_HEADER = re.compile(
    r"^\[\s*mcp_servers\s*\.\s*(?:iron-jarvis|\"iron-jarvis\"|'iron-jarvis')\s*(?:\.[^\]]*)?\]\s*(?:#.*)?$")
_ANY_HEADER = re.compile(r"^\[\[?[^\[\]]+\]\]?\s*(?:#.*)?$")


def _without_block(text: str) -> str:
    """The Codex file without Iron Jarvis's marked block, removed BY TABLE
    (OT6): the markers and Iron Jarvis's own tables go; any other table Codex
    (or the owner) wrote inside the markers stays where it was. When
    everything inside was ours, the one blank line Connect put before the
    block goes too, so the file comes back byte for byte. Line endings are
    kept as they are. A begin marker with no end marker is left alone."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].rstrip("\r\n") != CODEX_BEGIN:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        kept: list[str] = []
        ours = False  # lines before any header belong to the table above: kept
        while j < len(lines) and lines[j].rstrip("\r\n") != CODEX_END:
            s = lines[j].strip()
            if _ANY_HEADER.match(s):
                ours = bool(_OUR_HEADER.match(s))
            if not ours:
                kept.append(lines[j])
            j += 1
        if j >= len(lines):  # no end marker: not a block Iron Jarvis wrote
            out.extend(lines[i:])
            break
        if not kept and out and out[-1] in ("\n", "\r\n"):
            out.pop()
        out.extend(kept)  # each ends with its own line ending (END follows)
        i = j + 1
    return "".join(out)


def _codex_minus_ours(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    servers = out.get("mcp_servers")
    if isinstance(servers, dict):
        servers = {k: v for k, v in servers.items() if k != SERVER_NAME}
        if servers:
            out["mcp_servers"] = servers
        else:
            out.pop("mcp_servers", None)
    return out


def _codex_strip(text: str, home: Path) -> str:
    """:func:`_without_block`, proved (OT6): the result must parse as the
    original minus Iron Jarvis's own entry (an emptied ``mcp_servers`` counts
    as absent). If not, a copy of the file is kept and nothing is written."""
    import tomllib

    new = _without_block(text)
    if new == text:
        return new
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        why = "it is not valid TOML"
        raise AppConnectError(UNREADABLE.format(label=LABELS[APP_CODEX], why=why), why=why) from exc
    try:
        after = tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        after = None
    if after is None or _codex_minus_ours(after) != _codex_minus_ours(before):
        try:
            _keep_copy(codex_config_path(), home)
        except OSError:
            pass
        raise AppConnectError(CODEX_CANNOT_UNDO, why="it changed in a way Iron Jarvis cannot safely undo",
                              whole=True)
    return new


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


def _our_entry(app: str) -> "dict[str, Any] | None":
    """Iron Jarvis's own entry in the app's list (``command``, ``args``,
    ``env``), or None (none, not ours, or the file cannot be read)."""
    try:
        if app == APP_CLAUDE:
            data, _raw = _read_claude(LABELS[app])
            entry = (data.get("mcpServers") or {}).get(SERVER_NAME)
        elif app == APP_CODEX:
            import tomllib

            text = _codex_text()
            if not _BLOCK.search(text):
                return None
            servers = tomllib.loads(text).get("mcp_servers")
            entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
        elif app == APP_CLAUDE_CODE:
            entry = _claude_code_entry()
        else:
            return None
    except (AppConnectError, ValueError):
        return None
    return entry if _ours(entry) else None


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _stale(entry: dict[str, Any], current: Path) -> bool:
    """MS2 / MS3: the entry names another program than ``current`` (an older
    install's daemon, a mount that is gone), or a program that is not there."""
    command = str(entry.get("command") or "")
    if not command:
        return True
    return not _same_file(command, str(current)) or not Path(command).is_file()


def _cli_change(argv_tail: list[str]) -> None:
    exe = claude_cli()
    if exe is None:
        raise AppConnectError(NOT_INSTALLED.format(label=LABELS[APP_CLAUDE_CODE]))
    code, words = run_cli([*exe, *argv_tail])
    if code != 0:
        why = (words.splitlines() or ["it said nothing"])[-1][:200]
        raise AppConnectError(CLI_FAILED.format(why=why), why=why)
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
        try:
            _cli_change(["mcp", "add", "--scope", "user", *env, SERVER_NAME, "--", entry["command"], *entry["args"]])
        except AppConnectError as exc:
            if current is None:
                raise
            # OT4: the remove went through, so "nothing was changed" is false.
            raise AppConnectError(READD_FAILED.format(why=exc.why), why=exc.why) from exc
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
            _write_other(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            why = _os_why(exc)
            raise AppConnectError(NOT_WRITTEN.format(label=label, why=why), why=why) from exc
        return
    import tomllib

    text = _codex_text()
    if _codex_has_other(text):
        raise AppConnectError(NOT_ANOTHER.format(label=label))
    base = _codex_strip(text, home)
    if base and not base.endswith("\n"):
        base += "\n"
    new = base + ("\n" if base.strip() else "") + _codex_block(entry)
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as exc:
        why = "the result would not be valid TOML"
        raise AppConnectError(NOT_WRITTEN.format(label=label, why=why), why=why) from exc
    path = codex_config_path()
    try:
        _keep_copy(path, home)
        _write_other(path, new)
    except OSError as exc:
        why = _os_why(exc)
        raise AppConnectError(NOT_WRITTEN.format(label=label, why=why), why=why) from exc


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
            _write_other(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            why = _os_why(exc)
            raise AppConnectError(NOT_WRITTEN.format(label=label, why=why), why=why) from exc
        return
    text = _codex_text()
    if not _BLOCK.search(text):
        return
    new = _codex_strip(text, home)
    path = codex_config_path()
    try:
        _keep_copy(path, home)
        _write_other(path, new)
    except OSError as exc:
        why = _os_why(exc)
        raise AppConnectError(NOT_WRITTEN.format(label=label, why=why), why=why) from exc


__all__ = [
    "APPS", "APP_CLAUDE", "APP_CLAUDE_CODE", "APP_CODEX", "AppConnectError", "claude_cli", "claude_code_config_path", "run_cli", "AppConnections", "CAPABILITY", "LABELS",
    "PANE_PREFIX", "SERVER_NAME", "TOKEN_FILE_ENV", "claude_config_path", "codex_config_path",
    "config_path", "installed", "launch_argv",
    "AFTER_CONNECT", "BRIDGE_DIR_ENV", "BRIDGE_ID_FILE", "BRIDGE_OK_LINE", "BridgeState", "bridge_content_id",
    "bridge_name", "bridge_path", "ensure_bridge", "installed_bridge", "migrate_entries", "shipped_bridge_dir",
]
