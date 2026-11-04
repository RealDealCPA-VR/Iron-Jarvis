"""v1.429.0: Pi's model list and its own ``/`` commands, read BEFORE any Pi
session has run — so a Pi session in Build › Code offers what Pi's own
``/model`` picker offers from the first look, not "Pi's default" alone.

WHY (measured on the user's live install, read-only): Iron Jarvis kept Pi's
list only from a Pi process the Code view had started (v1.411.0's rule,
``hosted/pi_models.json``), and that file did not exist there — no Pi session
had run — so the model menu held one row and the ``/`` menu nothing.

HOW (measured with the real pi 1.3.0 on a scratch ``PI_CODING_AGENT_DIR``):
``pi --mode rpc --no-session --offline --no-mcp`` given three commands on
stdin (``get_state``, ``get_commands``, ``get_available_models``) answers all
three and exits by itself at end of input, in under a second; it writes no
session file, makes no network call (``--offline``) and starts none of the
user's MCP servers (``--no-mcp``). No model is called. The user's own
extensions, templates and skills load, as they do whenever Pi starts. It runs
in an empty folder of Iron Jarvis's own, so no project's ``.pi`` is read —
a session's own process later lists that project's commands too.

WHEN: in the background (never on a request's path), one read at a time,
when nothing was read yet, when the last read is older than
:data:`CATALOG_TTL_S`, or when one of Pi's own files that decide the list
changed (``models.json``, ``auth.json`` after a ``/login``, ``settings.json``,
``models-store.json``) — a stat of four files, never a parse. The suite turns
it off (``tests/conftest.py``); a test that drives it turns it back on.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import pi as P

log = logging.getLogger(__name__)

#: A read older than this is read again (in the background).
CATALOG_TTL_S = 600.0
#: The whole read may take this long (Pi answers in about a second).
PROBE_TIMEOUT_S = 20.0
#: The flags of the reading process (and ONLY of it — a session never gets them).
PROBE_ARGS: tuple[str, ...] = ("--mode", "rpc", "--no-session", "--offline", "--no-mcp")
#: Pi's own files whose change means a new list (a stat each, never a read).
WATCHED: tuple[str, ...] = ("models.json", "auth.json", "settings.json", "models-store.json")
#: The suite turns the background read off (tests/conftest.py).
AUTO_PROBE = True

_LOCK = threading.Lock()
_RUNNING: set[str] = set()


def watch_signature(agent: Path | None = None) -> list[list[Any]]:
    """BLOCKING (four stats): what Pi's list depends on, as ``[name, size,
    mtime_ns]`` rows (a missing file reads ``[name, -1, 0]``)."""
    agent = agent or P.agent_dir()
    out: list[list[Any]] = []
    for name in WATCHED:
        try:
            st = (agent / name).stat()
            out.append([name, int(st.st_size), int(st.st_mtime_ns)])
        except OSError:
            out.append([name, -1, 0])
    return out


def is_stale(doc: dict[str, Any], *, now: float | None = None, watch: list[list[Any]] | None = None) -> bool:
    """Is the kept read missing, old, or about files that changed since?"""
    at = doc.get("fetched_at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return True
    if (now if now is not None else time.time()) - float(at) > CATALOG_TTL_S:
        return True
    if watch is not None and doc.get("watch") != watch:
        return True
    return False


def reading(path: Path) -> bool:
    """Is a background read of ``path`` under way?"""
    with _LOCK:
        return str(path) in _RUNNING


def refresh_soon(path: Path, home: Path, argv0: list[str]) -> bool:
    """BLOCKING-light (four stats): start ONE background read of Pi's list
    when the kept one is missing, old or about changed files. True when a
    read is under way after this call (the menu says "Reading…")."""
    if not AUTO_PROBE:
        return False
    key = str(path)
    with _LOCK:
        if key in _RUNNING:
            return True
    doc = P._read_doc(path)
    if not is_stale(doc, watch=watch_signature()):
        return False
    with _LOCK:
        if key in _RUNNING:
            return True
        _RUNNING.add(key)
    threading.Thread(target=_refresh, args=(path, home, list(argv0)), name="pi-catalog", daemon=True).start()
    return True


def _refresh(path: Path, home: Path, argv0: list[str]) -> None:
    try:
        read_now(path, home, argv0)
    except Exception:  # noqa: BLE001 — a background read never raises anywhere
        log.debug("could not read Pi's model list", exc_info=True)
    finally:
        with _LOCK:
            _RUNNING.discard(str(path))


def read_now(path: Path, home: Path, argv0: list[str]) -> bool:
    """BLOCKING: read Pi's list now (``argv0`` starts Pi) and keep it. False
    when Pi did not answer (what was kept stays)."""
    from .process import build_env

    cwd = home / "hosted" / "pi-probe"
    cwd.mkdir(parents=True, exist_ok=True)
    got = probe(argv0, P.child_env(build_env(None)), cwd)
    if got is None:
        return False
    models = P.model_rows(got.get("models"))
    commands = P.command_rows(got.get("commands"))
    state = got.get("state") if isinstance(got.get("state"), dict) else {}
    model = state.get("model") if isinstance(state.get("model"), dict) else None
    if model:
        P.note_default(model, state.get("thinkingLevel"))
    P.save_catalog(
        path, models=models, commands=commands,
        default=(dict(P.LAST_DEFAULT) if model else {}),
        fetched_at=time.time(), watch=watch_signature(),
    )
    P.LAST_MODELS[:] = models
    from .claude import LAST_COMMANDS

    if commands and not LAST_COMMANDS.get(P.HARNESS):
        # A session's own list (with its project's commands) wins over this.
        LAST_COMMANDS[P.HARNESS] = list(commands)
    if not model:
        P.LAST_DEFAULT.clear()
    return True


def probe(argv0: list[str], env: dict[str, str], cwd: Path, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any] | None:
    """BLOCKING: one short Pi run that answers ``get_state``,
    ``get_commands`` and ``get_available_models`` and exits at end of input
    (through ``core.procs.run``: its own kill-on-close Job, the tree killed
    at the deadline). None when it did not answer all three."""
    from ..core import procs

    asks = [("s", "get_state"), ("c", "get_commands"), ("m", "get_available_models")]
    text = b"".join(json.dumps({"id": f"ij-catalog-{i}", "type": t}).encode("utf-8") + b"\n" for i, t in asks)
    try:
        done = procs.run([*argv0, *PROBE_ARGS], timeout=timeout, input=text, cwd=str(cwd), env=env)
    except Exception:  # noqa: BLE001 — Windows could not start it, or it timed out
        log.debug("Pi's model list could not be read", exc_info=True)
        return None
    answers: dict[str, Any] = {}
    out = done.stdout if isinstance(done.stdout, (bytes, bytearray)) else str(done.stdout or "").encode("utf-8")
    for raw in out.splitlines():
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "response" and obj.get("success") is not False:
            answers[str(obj.get("id") or "")] = obj.get("data")
    if not all(f"ij-catalog-{i}" in answers for i, _ in asks):
        return None
    state = answers["ij-catalog-s"]
    commands = answers["ij-catalog-c"]
    models = answers["ij-catalog-m"]
    return {
        "state": state if isinstance(state, dict) else {},
        "commands": commands.get("commands") if isinstance(commands, dict) else [],
        "models": models.get("models") if isinstance(models, dict) else [],
    }
