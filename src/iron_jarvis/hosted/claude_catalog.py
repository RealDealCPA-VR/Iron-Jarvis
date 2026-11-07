"""v1.447.1: Claude Code's ``/`` list for a folder, read BEFORE any Claude Code
session has run there — so after a restart the Code view's ``/`` menu shows
Claude Code's commands and the folder's own, not nothing.

WHY: the list is what Claude Code's ``initialize`` answers, kept per folder
(``claude.keep_commands``) only from a session's own process; after a restart
nothing was kept until a session opened in that folder.

HOW (MEASURED on claude 2.1.319, a scratch ``CLAUDE_CONFIG_DIR`` with a user
command, a user skill and a ``SessionStart`` hook, and a scratch folder with a
project command; no model call): ``claude -p --input-format stream-json
--output-format stream-json --verbose --strict-mcp-config --settings <file>``
fed ONE ``initialize`` control request with stdin closed at once answers it
(``commands``: 45 rows, the user's, the skill and the folder's among them) and
exits 0 in about a second. It writes no conversation (no ``projects/`` entry),
starts NONE of the user's MCP servers (``--strict-mcp-config`` with no
``--mcp-config``), and with ``{"disableAllHooks": true}`` in the settings file
runs NO hook — without it the user's ``SessionStart`` hook RAN on
``initialize`` (measured), so the file is always passed. Claude Code still
updates its own counters in its ``.claude.json`` as any start does.

WHEN: in the background (never on a request's path), one read at a time,
for a folder whose list is not known in this process yet; a folder whose
read failed waits :data:`RETRY_AFTER_FAIL_S`. This PC's own Claude Code login
is used (``process.build_env(None)``): a session on another account later
replaces the list with its own. The suite turns it off (``tests/conftest.py``);
a test that drives it turns it back on.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

#: The whole read may take this long (Claude Code answers in about a second).
PROBE_TIMEOUT_S = 30.0
#: The reading process's flags (ONLY its own — a session never gets them).
PROBE_ARGS: tuple[str, ...] = ("-p", "--input-format", "stream-json", "--output-format", "stream-json",
                               "--verbose", "--strict-mcp-config")
#: What the settings file says: no hook of the user's or the folder's runs.
PROBE_SETTINGS: dict[str, Any] = {"disableAllHooks": True}
#: The one request sent.
REQUEST_ID = "ij-commands"
#: The suite turns the background read off (tests/conftest.py).
AUTO_PROBE = True
#: After a read Claude Code did not answer, the same folder waits this long.
RETRY_AFTER_FAIL_S = 300.0

_LOCK = threading.Lock()
_RUNNING: set[str] = set()
#: folder key -> when its last read failed (monotonic seconds).
_FAILED_AT: dict[str, float] = {}
#: One read at a time for the whole process (each is a Claude Code start).
_ONE_AT_A_TIME = threading.Semaphore(1)


def reading(folder: str) -> bool:
    """Is a background read of ``folder``'s list under way?"""
    from .claude import folder_key

    with _LOCK:
        return folder_key(folder) in _RUNNING


def refresh_soon(home: Path, argv_fn: Callable[[], list[str] | None], folder: str) -> bool:
    """Start ONE background read of ``folder``'s list when none is known in
    this process. Cheap and never blocking (a dict look and, at most, a
    thread start; ``argv_fn`` — the PATH look for Claude Code — runs on the
    reading thread). True when a read is under way after this call."""
    from .claude import HARNESS, LAST_COMMANDS, commands_key, folder_key

    if not AUTO_PROBE or not folder:
        return False
    if LAST_COMMANDS.get(commands_key(HARNESS, folder)):
        return False
    key = folder_key(folder)
    with _LOCK:
        if key in _RUNNING:
            return True
        failed = _FAILED_AT.get(key)
        if failed is not None and time.monotonic() - failed < RETRY_AFTER_FAIL_S:
            return False
        _RUNNING.add(key)
    threading.Thread(target=_refresh, args=(Path(home), argv_fn, folder), name="claude-catalog",
                     daemon=True).start()
    return True


def _refresh(home: Path, argv_fn: Callable[[], list[str] | None], folder: str) -> None:
    from .claude import folder_key

    ok = False
    try:
        argv0 = argv_fn()
        if argv0:
            with _ONE_AT_A_TIME:
                ok = read_now(home, list(argv0), folder)
    except Exception:  # noqa: BLE001 — a background read never raises anywhere
        log.debug("could not read Claude Code's command list", exc_info=True)
    finally:
        key = folder_key(folder)
        with _LOCK:
            if ok:
                _FAILED_AT.pop(key, None)
            else:
                _FAILED_AT[key] = time.monotonic()
            _RUNNING.discard(key)


def settings_file(home: Path) -> Path:
    """BLOCKING: the settings file the read passes (written when missing or
    changed). A FILE, never JSON on the command line (a ``.cmd`` shim would
    not carry it — the v1.300.0 lesson)."""
    path = Path(home) / "hosted" / "claude-probe" / "settings.json"
    text = json.dumps(PROBE_SETTINGS)
    try:
        if path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def probe_env() -> dict[str, str]:
    """A Build terminal's environment on this PC's own login, with Claude
    Code's non-essential traffic off for the read."""
    from .process import build_env

    env = build_env(None)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def read_now(home: Path, argv0: list[str], folder: str) -> bool:
    """BLOCKING: read ``folder``'s list now and keep it (a session's own list,
    read meanwhile, wins). False when Claude Code did not answer."""
    from .claude import keep_commands

    if not Path(folder).is_dir():
        return False
    got = probe(argv0, probe_env(), Path(folder), settings_file(home))
    if got is None:
        return False
    return bool(keep_commands(folder, got, only_if_unknown=True))


def probe(argv0: list[str], env: dict[str, str], cwd: Path, settings: Path,
          timeout: float = PROBE_TIMEOUT_S) -> list[dict[str, Any]] | None:
    """BLOCKING: one short Claude Code run that answers ``initialize`` and
    exits at end of input (through ``core.procs.run``: its own kill-on-close
    Job, the tree killed at the deadline). The ``commands`` rows, or None."""
    from ..core import procs

    line = json.dumps({"type": "control_request", "request_id": REQUEST_ID,
                       "request": {"subtype": "initialize"}}).encode("utf-8") + b"\n"
    try:
        done = procs.run([*argv0, *PROBE_ARGS, "--settings", str(settings)], timeout=timeout, input=line,
                         cwd=str(cwd), env=env)
    except Exception:  # noqa: BLE001 — Windows could not start it, or it timed out
        log.debug("Claude Code's command list could not be read", exc_info=True)
        return None
    out = done.stdout if isinstance(done.stdout, (bytes, bytearray)) else str(done.stdout or "").encode("utf-8")
    for raw in out.splitlines():
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "control_response":
            continue
        resp = obj.get("response") if isinstance(obj.get("response"), dict) else {}
        if resp.get("request_id") != REQUEST_ID or resp.get("subtype") != "success":
            continue
        body = resp.get("response") if isinstance(resp.get("response"), dict) else {}
        cmds = body.get("commands")
        return cmds if isinstance(cmds, list) else None
    return None
