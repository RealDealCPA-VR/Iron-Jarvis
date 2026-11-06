"""v1.441.0: OpenCode's own model list, read BEFORE any OpenCode session has
run — so Build › Code's model menu offers what OpenCode's own picker offers
from the first look, not "OpenCode's default" alone (until v1.441.0 the list
came only from an OpenCode process the Code view had started:
``configOptions.model``, ``hosted/opencode_models.json``).

HOW (MEASURED on opencode 1.18.18, this PC, in a SCRATCH home — USERPROFILE,
HOME and every XDG_* folder pointed at a scratch folder, a config naming a
stand-in provider on 127.0.0.1, no model call):

* ``opencode models --verbose`` prints every model OpenCode can use, one
  block each: a line ``<provider>/<model>`` and then the model as JSON
  (``{"id", "providerID", "name", "variants": {<level>: {...}}, "capabilities":
  {...}, "limit": {...}}``). ``variants`` are the effort levels the model
  takes (``{}``: none). It answered in about two seconds, exit 0, and starts
  no session.
* It reads the same config files and sign-ins an ACP session does (plus
  OpenCode's built-in free models). With ``OPENCODE_DISABLE_MODELS_FETCH=1``
  (set for THIS reading process only) it reads OpenCode's own models cache and
  never fetches models.dev; a session's own OpenCode still refreshes that
  cache the way it always does. That is the ONLY network turned off: like any
  OpenCode run, ``opencode models`` loads the plugins the user's OpenCode
  config names, so it may download and install a plugin the config lists.

WHEN: in the background (never on a request's path), one read at a time
(single-flight per file), when nothing was read yet, when the last read is
older than :data:`CATALOG_TTL_S`, or when one of OpenCode's own files that
decide the list changed (its global config files, ``auth.json`` after
``opencode auth login``, its models cache) — stats only, never a parse. A read
OpenCode did not answer waits :data:`RETRY_AFTER_FAIL_S` before the next. The
suite turns it off (``tests/conftest.py``); a test that drives it turns it on.

The list it keeps is the set of models; an OpenCode process's own list
(``configOptions.model``) still adds what only a PROJECT's config names, and
lends its labels (``"OpenCode Zen/Big Pickle"``) where both name a model.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from .events import clip

log = logging.getLogger(__name__)

#: A read older than this is read again (in the background).
CATALOG_TTL_S = 600.0
#: The whole read may take this long (OpenCode answered in about two seconds).
PROBE_TIMEOUT_S = 30.0
#: The arguments of the reading process (and ONLY of it).
PROBE_ARGS: tuple[str, ...] = ("models", "--verbose")
#: The suite turns the background read off (tests/conftest.py).
AUTO_PROBE = True
#: After a read OpenCode did not answer, the next one waits this long.
RETRY_AFTER_FAIL_S = 300.0
#: The most models kept (OpenCode's own picker groups hundreds by provider).
MAX_MODELS = 300

_LOCK = threading.Lock()
_RUNNING: set[str] = set()
#: path -> when its last read failed (monotonic seconds).
_FAILED_AT: dict[str, float] = {}


def watched_files(environ: Any = None) -> list[Path]:
    """The files OpenCode's list depends on: its global config files (the same
    list the permission reader uses), its sign-ins and its models cache."""
    from . import opencode as O

    env = os.environ if environ is None else environ
    files = [p for p in O.config_files("", env) if p.name in ("opencode.json", "opencode.jsonc", "config.json")]
    files.append(O.data_dir(env) / "auth.json")
    xdg = str(env.get("XDG_CACHE_HOME") or "").strip()
    cache = Path(os.path.expanduser(xdg)) if xdg else Path.home() / ".cache"
    files.append(cache / "opencode" / "models.json")
    out: list[Path] = []
    for f in files:
        if f not in out:
            out.append(f)
    return out


def watch_signature(environ: Any = None) -> list[list[Any]]:
    """BLOCKING (a stat each): ``[path, size, mtime_ns]`` rows (a missing file
    reads ``[path, -1, 0]``)."""
    out: list[list[Any]] = []
    for path in watched_files(environ):
        try:
            st = path.stat()
            out.append([str(path), int(st.st_size), int(st.st_mtime_ns)])
        except OSError:
            out.append([str(path), -1, 0])
    return out


def read_doc(path: Path | None) -> dict[str, Any]:
    """BLOCKING-light: the kept read (``{}`` when none or unreadable)."""
    if path is None:
        return {}
    try:
        if path.is_file() and path.stat().st_size < 4_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def cached_rows(path: Path | None) -> list[dict[str, Any]]:
    """The models the last background read kept, as the composer's rows."""
    out: list[dict[str, Any]] = []
    for r in read_doc(path).get("models") or []:
        if not (isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]):
            continue
        row: dict[str, Any] = {"id": clip(r["id"], 120), "label": clip(r.get("label") or r["id"], 120)}
        if isinstance(r.get("efforts"), list):
            row["efforts"] = [str(e) for e in r["efforts"] if isinstance(e, str)][:12]
        out.append(row)
        if len(out) >= MAX_MODELS:
            break
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
    """BLOCKING-light (stats only): start ONE background read of OpenCode's
    list when the kept one is missing, old or about changed files. True when
    a read is under way after this call."""
    if not AUTO_PROBE:
        return False
    key = str(path)
    with _LOCK:
        if key in _RUNNING:
            return True
        failed = _FAILED_AT.get(key)
    if failed is not None and time.monotonic() - failed < RETRY_AFTER_FAIL_S:
        return False
    if not is_stale(read_doc(path), watch=watch_signature()):
        return False
    with _LOCK:
        if key in _RUNNING:
            return True
        _RUNNING.add(key)
    threading.Thread(target=_refresh, args=(path, home, list(argv0)), name="opencode-catalog",
                     daemon=True).start()
    return True


def _refresh(path: Path, home: Path, argv0: list[str]) -> None:
    ok = False
    try:
        ok = read_now(path, home, argv0)
    except Exception:  # noqa: BLE001 — a background read never raises anywhere
        log.debug("could not read OpenCode's model list", exc_info=True)
    finally:
        with _LOCK:
            if ok:
                _FAILED_AT.pop(str(path), None)
            else:
                _FAILED_AT[str(path)] = time.monotonic()
            _RUNNING.discard(str(path))


def parse_verbose(text: str) -> list[dict[str, Any]]:
    """``opencode models --verbose`` -> rows ``{id, label, efforts}``: each
    block is a ``<provider>/<model>`` line, then the model as JSON from a line
    ``{`` to a line ``}``. A block that does not read is left out."""
    rows: list[dict[str, Any]] = []
    head = ""
    buf: list[str] | None = None
    for line in str(text or "").splitlines():
        if buf is None:
            stripped = line.strip()
            if stripped == "{":
                buf = [line]
            elif stripped and "/" in stripped and " " not in stripped:
                head = stripped
            continue
        buf.append(line)
        if line.rstrip() == "}":
            try:
                model = json.loads("\n".join(buf))
            except ValueError:
                model = None
            buf = None
            if not (isinstance(model, dict) and head):
                head = ""
                continue
            row: dict[str, Any] = {"id": clip(head, 120), "label": clip(model.get("name") or head, 120)}
            variants = model.get("variants")
            if isinstance(variants, dict):
                row["efforts"] = [clip(k, 40) for k in variants if isinstance(k, str) and k][:12]
            rows.append(row)
            head = ""
            if len(rows) >= MAX_MODELS:
                break
    return rows


def probe(argv0: list[str], env: dict[str, str], cwd: Path,
          timeout: float = PROBE_TIMEOUT_S) -> list[dict[str, Any]] | None:
    """BLOCKING: one ``opencode models --verbose`` run (through
    ``core.procs.run``: its own kill-on-close Job, the tree killed at the
    deadline). None when it did not answer."""
    from ..core import procs

    try:
        done = procs.run([*argv0, *PROBE_ARGS], timeout=timeout, cwd=str(cwd), env=env)
    except Exception:  # noqa: BLE001 — Windows could not start it, or it timed out
        log.debug("OpenCode's model list could not be read", exc_info=True)
        return None
    if getattr(done, "returncode", 1) != 0:
        return None
    out = done.stdout
    text = out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out or "")
    rows = parse_verbose(text)
    return rows or None


def probe_env(base: dict[str, str]) -> dict[str, str]:
    """The reading process's environment: the user's own, with OpenCode told
    not to fetch models.dev (it reads its own cache) and no question tool."""
    env = dict(base)
    env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
    env.pop("OPENCODE_PERMISSION", None)
    env.pop("OPENCODE_ENABLE_QUESTION_TOOL", None)
    return env


def read_now(path: Path, home: Path, argv0: list[str]) -> bool:
    """BLOCKING: read OpenCode's list now (``argv0`` starts OpenCode) and keep
    it. False when OpenCode did not answer (what was kept stays)."""
    from .process import build_env

    cwd = home / "hosted" / "opencode-probe"
    cwd.mkdir(parents=True, exist_ok=True)
    rows = probe(argv0, probe_env(build_env(None)), cwd)
    if rows is None:
        return False
    save(path, rows, watch=watch_signature())
    return True


def save(path: Path, rows: list[dict[str, Any]], *, watch: list[list[Any]], now: float | None = None) -> None:
    """BLOCKING: keep a read (temp + replace)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(json.dumps({"models": rows[:MAX_MODELS], "fetched_at": now if now is not None else time.time(),
                                    "watch": watch}, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        log.debug("could not keep OpenCode's model list", exc_info=True)


def merged(read: list[dict[str, Any]], listed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The menu's rows: every model the background read found (with an
    OpenCode process's label for it when that process listed it too, and its
    effort levels when the read had none), then what only a process listed
    (a project's own provider)."""
    if not read:
        return list(listed)
    by_id = {str(r.get("id")): r for r in listed if isinstance(r, dict)}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in read:
        rid = str(r.get("id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        row = dict(r)
        other = by_id.get(rid)
        if other is not None:
            row["label"] = other.get("label") or row.get("label")
            if "efforts" not in row and isinstance(other.get("efforts"), list):
                row["efforts"] = list(other["efforts"])
        out.append(row)
    for r in listed:
        rid = str(r.get("id") or "")
        if rid and rid not in seen:
            seen.add(rid)
            out.append(dict(r))
    return out[:MAX_MODELS]
