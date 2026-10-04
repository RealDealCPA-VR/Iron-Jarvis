"""The daemon's ONE Iron-Proxy service (v1.301.0): find it, start it, stop it.

Lives at ``platform.iron_proxy`` (built in ``build_platform``); the module
function :func:`current` hands the same object to code that has no platform in
reach (the provider adapters, through ``iron_proxy.accounts``).

Shape of the job:

* ``data_dir()`` — ``IRON_PROXY_DATA_DIR`` or ``~/.iron-proxy``, the directory
  the tray app and the ``iron-proxy`` CLI share.
* ``locate()`` — an Iron-Proxy that is ALREADY running (the tray app,
  ``iron-proxy serve``, or one we started): ``<data_dir>/proxy.json`` names a
  pid that is alive AND ``GET /iron/health`` answers at its url.
* ``start()`` — reuse ``locate()`` (``owned=False``); otherwise spawn the
  bundled single-file ``iron-proxy.mjs`` (``serve --port 0 --data-dir D``),
  read the real url/token back from proxy.json and wait for health
  (``owned=True``). Never raises: a failure is ONE plain sentence in
  ``status()["error"]``.
* ``stop()`` — only an OWNED child is killed (tree kill), and proxy.json is
  removed only while it still names that child. An Iron-Proxy the user started
  is never touched.
* ``client()`` — the cached :class:`IronProxyClient`, or ``None`` when the
  feature is off or nothing is running (the Connections routes). A transport
  failure drops the cache so the next call re-locates.
* ``lease_client()`` — what a call that runs AS an account uses: ``None`` only
  when the switch is off; otherwise the client, or :class:`IronProxyUnavailable`
  in one sentence (not answering / too old / token refused) — never a silent
  fall back to this PC's own login.
* ``has_usable_account(ij_provider)`` — CACHED (the watch loop and every
  account-changing route refresh it): provider availability reads it on the
  loop, so a signed-out default login does not hide a working account.
* The child this daemon started is recorded (pid + create time) in
  ``<home>/iron-proxy-owned.json``, so after a daemon crash it is still OURS
  to stop.

Every method here BLOCKS (file reads, a health round-trip, a spawn, a wait of
up to ~10 s). Callers on the event loop use ``asyncio.to_thread``; the
lifespan's :func:`watch` loop does exactly that.

The token from proxy.json stays inside this object and the client: never in
``status()``, a log line, or an error sentence.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .client import IronProxyClient

log = logging.getLogger(__name__)

#: How long ``start()`` waits for a spawned Iron-Proxy to answer health.
START_TIMEOUT_S = 10.0
#: Health probes are local and tiny; a proxy that takes longer is not healthy.
HEALTH_TIMEOUT_S = 2.0
#: The log a spawned Iron-Proxy writes to is rotated past this size.
_LOG_MAX_BYTES = 2 * 1024 * 1024
#: Never handed to the child: the daemon's own credentials (same list the
#: terminals manager strips from a pane, ``terminals.manager._DAEMON_ONLY_ENV``).
_DAEMON_ONLY_ENV = ("IRONJARVIS_TOKEN", "IRONJARVIS_MCP_TOKEN")

_CURRENT: "IronProxyService | None" = None


def current() -> "IronProxyService | None":
    """The live service (the one the daemon built), or ``None``. Never raises."""
    try:
        return _CURRENT
    except Exception:  # noqa: BLE001 — the adapters' door must never raise
        return None


def _set_current(svc: "IronProxyService | None") -> None:
    global _CURRENT
    _CURRENT = svc


def _pid_alive(pid: int) -> bool:
    try:
        import psutil

        return bool(psutil.pid_exists(int(pid)))
    except Exception:  # noqa: BLE001 — unknown is not alive
        return False


def _family(pid: int) -> set[int]:
    """``pid`` and every descendant. A ``.cmd`` shim or a venv launcher runs
    the real server as a CHILD of the process we spawned, so the pid it writes
    into proxy.json is not always the one ``Popen`` returned."""
    out = {int(pid)}
    try:
        import psutil

        for child in psutil.Process(int(pid)).children(recursive=True):
            out.add(int(child.pid))
    except Exception:  # noqa: BLE001 — a dead or unreadable process has no family
        pass
    return out


def _health(url: str, timeout: float = HEALTH_TIMEOUT_S) -> dict[str, Any] | None:
    """``GET <url>/iron/health`` → its JSON when it says ok, else ``None``.
    Unauthenticated by design (Iron-Proxy serves health without a token), so
    the token never travels on a probe."""
    try:
        resp = httpx.get(url.rstrip("/") + "/iron/health", timeout=timeout, trust_env=False)
        if resp.status_code != 200:
            return None
        doc = resp.json()
        return doc if isinstance(doc, dict) and doc.get("ok") else None
    except Exception:  # noqa: BLE001 — anything but a healthy answer is "not running"
        return None


def _read_descriptor(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    url, token, pid = doc.get("url"), doc.get("token"), doc.get("pid")
    if not (isinstance(url, str) and url and isinstance(token, str) and token):
        return None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    return {"url": url, "token": token, "pid": pid}


#: What a usable Iron-Proxy must advertise on ``GET /iron/health``
#: (``features``): the external-executor API (pick / signal / finished). A
#: pre-executor Iron-Proxy (the tray app at b7391ae) reports the same version
#: and answers ``/iron/pick`` 404, so the version cannot tell them apart.
REQUIRED_FEATURE = "executor-v1"

#: v1.302.0: ``GET /iron/pick?profileId=`` picks THAT account. An Iron-Proxy
#: without it IGNORES the parameter and answers the first free account, so a
#: Build pane asked to start on a chosen account checks this first.
PICK_PROFILE_FEATURE = "pick-profile"

#: Every health ``feature`` Iron Jarvis knows; the BUNDLED Iron-Proxy's own set
#: is read from the bundle text (``_bundle_features``).
KNOWN_FEATURES = (REQUIRED_FEATURE, PICK_PROFILE_FEATURE)

#: v1.303.3: an account that needs sign-in (or whose state is unknown) is
#: re-checked (``POST /iron/refresh {id}``) at most this often, per account.
REFRESH_EVERY_S = 10.0
#: How long ``recheck_signed_out`` waits by default. ``GET /iron-proxy``
#: passes 0 (the card polls every second during a sign-in; a result lands in
#: the snapshot and the next read); "Check again" waits on its own future.
REFRESH_WAIT_S = 3.0
#: One re-check runs the vendor CLI's own status command: seconds, not ms.
REFRESH_TIMEOUT_S = 30.0
#: States worth re-checking: a sign-in that may have happened since.
RECHECK_STATES = frozenset({"unauthenticated", "unknown"})
#: v1.303.3 review: an account is re-checked AUTOMATICALLY only while a sign-in
#: is in progress — a sign-in pane for it is open, or the Sign in route was
#: pressed within this window. Iron-Proxy's re-check trusts the LOCAL
#: credentials file (``claude auth status``), so re-checking an account a real
#: request just found signed out (a revoked token, a failed refresh: 401)
#: would flip it back to ready and lease it into the same 401 again.
SIGNIN_WINDOW_S = 180.0
#: A stale Iron-Jarvis-started proxy is replaced at most this many times per
#: :data:`REPLACE_WINDOW_S` (two Iron Jarvis copies sharing one data dir —
#: a source run and the installed app — would otherwise kill each other's).
REPLACE_LIMIT = 2
REPLACE_WINDOW_S = 600.0

#: Iron Jarvis provider id -> the Iron-Proxy provider whose CLI accounts it runs as.
JARVIS_TO_PROXY = {"claude-cli": "anthropic", "codex-cli": "openai", "grok-cli": "xai"}

NOT_ANSWERING = (
    "Iron-Proxy is on but not answering — Iron Jarvis will not use this PC's own "
    "login behind your back; turn Iron-Proxy off in Connections to use it."
)
OUTDATED = (
    "The Iron-Proxy running on this PC is older than this Iron Jarvis needs — "
    "update it, or close it so Iron Jarvis starts its own."
)
#: OUTDATED when the running proxy's process can be named (v1.303.3).
OUTDATED_NAMED = (
    "The Iron-Proxy running on this PC (process {pid}, {what}) is older than this "
    "Iron Jarvis needs and was not started by this Iron Jarvis — close that "
    "process (or update it), then turn Iron-Proxy off and on in Connections."
)
TOKEN_REFUSED = (
    "Iron-Proxy refused Iron Jarvis's access token; turn Iron-Proxy off and on again."
)

#: A lease that finds nothing running starts Iron-Proxy itself — but not more
#: often than this, so a broken bundle does not cost every call a 10 s wait.
_LEASE_START_COOLDOWN_S = 30.0
#: Process create times are floats; two reads of the same process agree to
#: well within this.
_CT_TOLERANCE_S = 0.01


class _StartProblem(Exception):
    """A start that cannot happen, as the one sentence the user will read."""


class IronProxyUnavailable(Exception):
    """Iron-Proxy is ON but cannot be used for this call. ``sentence`` is the
    one plain sentence for the user. Raised by :meth:`IronProxyService.lease_client`
    so a caller never falls back to this PC's own login behind the user's back."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


def _create_time(pid: int) -> float | None:
    try:
        import psutil

        return float(psutil.Process(int(pid)).create_time())
    except Exception:  # noqa: BLE001 — gone or unreadable
        return None


def _kill_pids(pids: list[int]) -> None:
    """Kill each pid and its descendants (psutil), then wait briefly."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return
    procs: list[Any] = []
    for pid in pids:
        try:
            p = psutil.Process(int(pid))
            procs.extend(p.children(recursive=True))
            procs.append(p)
        except Exception:  # noqa: BLE001 — already gone
            continue
    for p in procs:
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    try:
        psutil.wait_procs(procs, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _cmdline(pid: int) -> list[str]:
    """The process's command line, or ``[]`` when it cannot be read."""
    try:
        import psutil

        return [str(a) for a in psutil.Process(int(pid)).cmdline()]
    except Exception:  # noqa: BLE001 — gone or not ours to read: not a bundle
        return []


#: The executable an Iron Jarvis install keeps beside ``resources/``.
_IJ_EXE_NAMES = ("Iron Jarvis.exe", "Iron Jarvis", "iron-jarvis")


def _repo_bundle_dir() -> Path:
    """THIS repo's ``desktop/vendor/iron-proxy`` (a source run's bundle)."""
    return Path(__file__).resolve().parents[3] / "desktop" / "vendor" / "iron-proxy"


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _script_arg(cmdline: list[str]) -> str | None:
    """The first SCRIPT argument after the runtime (``node``, Electron): the
    first argument after ``argv[0]`` that is not a runtime flag."""
    for arg in list(cmdline or [])[1:]:
        a = str(arg).strip().strip('"')
        if a and not a.startswith("-"):
            return a
    return None


def iron_jarvis_owner(cmdline: list[str]) -> Path | None:
    """The Iron Jarvis whose bundle this command line runs — the folder of THIS
    repo (a source run) or of an install — or ``None``. The bundle must be the
    FIRST script argument after the runtime and live in this repo's
    ``desktop/vendor/iron-proxy``, or in an install's ``resources/iron-proxy``
    whose install folder holds the Iron Jarvis executable. Reads the disk."""
    script = _script_arg(cmdline)
    if not script:
        return None
    bundle = Path(script)
    if bundle.name.lower() != "iron-proxy.mjs":
        return None
    folder = bundle.parent
    try:
        if _norm(folder) == _norm(_repo_bundle_dir()):
            return _repo_bundle_dir().parents[2]
        if folder.name.lower() == "iron-proxy" and folder.parent.name.lower() == "resources":
            install = folder.parent.parent
            if any((install / name).is_file() for name in _IJ_EXE_NAMES):
                return install
    except OSError:
        return None
    return None


def is_iron_jarvis_bundle(cmdline: list[str]) -> bool:
    """Does this command line run the Iron-Proxy bundle an Iron Jarvis ships
    (:func:`iron_jarvis_owner`)?"""
    return iron_jarvis_owner(cmdline) is not None


def _another_iron_jarvis(where: Path | str) -> str:
    return (
        f"Another Iron Jarvis on this PC ({where}) keeps starting an older Iron-Proxy, so "
        "this one stopped replacing it — update or close that copy of Iron Jarvis."
    )


def _could_not_stop(pid: int) -> str:
    return (
        f"Iron Jarvis could not stop the older Iron-Proxy (pid {pid}) — close it, then "
        "press Turn on again."
    )


class IronProxyService:
    def __init__(self, config: Any, *, register: bool = True) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._url: str | None = None
        self._token: str | None = None
        self._pid: int | None = None
        self._owned = False
        self._proc: subprocess.Popen | None = None
        self._client: IronProxyClient | None = None
        self._error: str | None = None
        self._version: str | None = None
        self._outdated = False
        self._token_bad = False
        self._last_lease_start = 0.0
        #: Iron-Proxy provider -> "has an enabled CLI account not needing
        #: sign-in", from the last account read. Read by ``has_usable_account``
        #: (provider availability), which must never block.
        self._usable: dict[str, bool] = {}
        #: v1.302.0: the last ``(profiles, states)`` read, for the per-pane
        #: account chips (``cached_accounts``) — never a blocking call in a
        #: list route. Cleared wherever ``_usable`` is.
        self._accounts_cache: tuple[list[Any], dict[str, Any]] | None = None
        #: Iron-Proxy providers that had a CLI account at the last read (kept
        #: across a failed read, so an unanswering Iron-Proxy can still be
        #: named per provider on a new pane). Cleared only when turned off.
        self._providers_seen: set[str] = set()
        #: Health ``features`` from the last answer (``has_feature``).
        self._features: frozenset[str] = frozenset()
        # status() is read on the event loop: the bundle's presence and
        # version are read HERE (and refreshed off the loop on start/check),
        # never per status() call.
        self._bundled_cached = False
        self._bundle_version_cached: str | None = None
        #: v1.303.3: the health features the BUNDLED Iron-Proxy advertises
        #: (read from its text, cached by the file's size + mtime).
        self._bundle_features: frozenset[str] = frozenset()
        self._bundle_features_key: tuple[Any, ...] | None = None
        #: A located Iron-Proxy that lacks a bundled feature and is NOT one an
        #: Iron Jarvis started (the tray app, a user-run serve): said in
        #: ``status().error``, never stopped.
        self._stale_foreign = False
        #: Pids this daemon tried to stop as stale Iron-Jarvis-started proxies,
        #: the (bundle, version) pairs it replaced, when, and the pids it could
        #: not stop (their sentence). ``_stale_error`` = the sentence to show.
        self._replaced: set[int] = set()
        self._replaced_keys: set[tuple[str, str]] = set()
        self._replace_times: list[float] = []
        self._kill_failed: dict[int, str] = {}
        self._stale_error: str | None = None
        #: Profile id -> when the Sign in route opened a pane for it.
        self._signin_started: dict[str, float] = {}
        #: Guards the account snapshot's read-modify-write.
        self._snapshot_lock = threading.RLock()
        #: v1.303.3: per-account re-checks (``request_refresh``).
        self._refresh_lock = threading.Lock()
        self._refresh_at: dict[str, float] = {}
        self._refresh_futures: dict[str, concurrent.futures.Future] = {}
        self._refresh_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._refresh_bundle_facts()
        if register:
            _set_current(self)

    # ------------------------------------------------------------- config
    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "iron_proxy_enabled", False))

    def set_enabled(self, value: bool) -> None:
        """Flip the switch AND persist it (config.toml), like every setting."""
        from ..core.config import persist_config_values

        self._config.iron_proxy_enabled = bool(value)
        persist_config_values(self._config.home, {"iron_proxy_enabled": bool(value)})
        if not value:
            self._error = None
            self._usable = {}
            self._accounts_cache = None
            self._providers_seen = set()

    def data_dir(self) -> Path:
        override = os.environ.get("IRON_PROXY_DATA_DIR", "").strip()
        if override:
            return Path(override).expanduser()
        return Path.home() / ".iron-proxy"

    def log_path(self) -> Path:
        return Path(self._config.home) / "logs" / "iron-proxy.log"

    def _owned_record_path(self) -> Path:
        return Path(self._config.home) / "iron-proxy-owned.json"

    # ------------------------------------------------------------- bundle
    def _bundle_path(self) -> Path | None:
        env = os.environ.get("IRONJARVIS_IRON_PROXY_BUNDLE", "").strip()
        if env:
            return Path(env)
        if getattr(sys, "frozen", False):
            # A frozen daemon has no repo beside it: the packaged desktop
            # passes the bundle through the environment or there is none.
            return None
        repo = Path(__file__).resolve().parents[3]
        return repo / "desktop" / "vendor" / "iron-proxy" / "iron-proxy.mjs"

    def bundled(self) -> bool:
        """True when Iron Jarvis has an Iron-Proxy it could start itself
        (a live stat — call off the loop; ``status()`` reads the cached fact)."""
        try:
            p = self._bundle_path()
            return bool(p is not None and p.is_file())
        except OSError:
            return False

    def _refresh_bundle_facts(self) -> None:
        self._bundled_cached = self.bundled()
        self._bundle_version_cached = self._bundle_version()
        self._read_bundle_features()

    def _read_bundle_features(self) -> None:
        """Which :data:`KNOWN_FEATURES` the bundle advertises (its health
        answer is built from string literals in the file). Blocking; re-read
        only when the file's size or mtime changed."""
        try:
            path = self._bundle_path()
            if path is None or not path.is_file():
                self._bundle_features, self._bundle_features_key = frozenset(), None
                return
            st = path.stat()
            key = (str(path), st.st_size, st.st_mtime_ns)
            if key == self._bundle_features_key:
                return
            text = path.read_text(encoding="utf-8", errors="replace")
            self._bundle_features = frozenset(
                f for f in KNOWN_FEATURES if f'"{f}"' in text or f"'{f}'" in text
            )
            self._bundle_features_key = key
        except OSError:
            self._bundle_features, self._bundle_features_key = frozenset(), None

    def _command(self) -> tuple[list[str], dict[str, str]]:
        """``(argv prefix, extra child env)`` that runs the bundle, or raise a
        :class:`_StartProblem` saying why it cannot."""
        bundle = self._bundle_path()
        if bundle is None or not bundle.is_file():
            raise _StartProblem(
                "This copy of Iron Jarvis does not include Iron-Proxy, so it cannot start "
                "one itself; start Iron-Proxy yourself (the tray app or `iron-proxy serve`) "
                "and Iron Jarvis will use it."
            )
        node_env = os.environ.get("IRONJARVIS_IRON_PROXY_NODE", "").strip()
        node = node_env or shutil.which("node")
        if not node:
            raise _StartProblem(
                "Iron Jarvis could not find Node.js to run Iron-Proxy; install Node.js "
                "or start Iron-Proxy yourself and Iron Jarvis will use it."
            )
        extra: dict[str, str] = {}
        if Path(node).stem.lower() != "node":
            # Electron's own executable runs a script as plain Node only when
            # told to; for a real `node` the variable is meaningless.
            extra["ELECTRON_RUN_AS_NODE"] = "1"
        return [node, str(bundle)], extra

    def _bundle_version(self) -> str | None:
        """The version the vendoring script recorded beside the bundle
        (``SOURCE.txt``), when health does not report one. Blocking."""
        try:
            bundle = self._bundle_path()
            if bundle is None:
                return None
            text = (bundle.parent / "SOURCE.txt").read_text(encoding="utf-8")
        except OSError:
            return None
        m = re.search(r"(?im)^\s*\"?version\"?\s*[:=]\s*\"?([0-9][^\s\",]*)", text)
        return m.group(1) if m else None

    # ------------------------------------------------- the owned-child record
    def _write_owned_record(self, proc: subprocess.Popen, server_pid: int) -> None:
        """Remember the child we started (pid + process create time) under the
        daemon home, so a daemon that crashed and came back still knows that
        Iron-Proxy is ITS OWN to stop. Never raises."""
        try:
            rec = {
                "root_pid": int(proc.pid),
                "root_create_time": _create_time(proc.pid),
                "server_pid": int(server_pid),
                "server_create_time": _create_time(server_pid),
            }
            path = self._owned_record_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rec), encoding="utf-8")
        except Exception:  # noqa: BLE001 — bookkeeping never fails a start
            log.debug("iron-proxy owned record write failed", exc_info=True)

    def _read_owned_record(self) -> dict[str, Any] | None:
        try:
            doc = json.loads(self._owned_record_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def _clear_owned_record(self) -> None:
        try:
            self._owned_record_path().unlink()
        except OSError:
            pass

    @staticmethod
    def _same_process(pid: Any, recorded_ct: Any) -> bool:
        if not isinstance(pid, int) or not isinstance(recorded_ct, (int, float)):
            return False
        ct = _create_time(pid)
        return ct is not None and abs(ct - float(recorded_ct)) <= _CT_TOLERANCE_S

    def _recorded_as_ours(self, pid: int) -> bool:
        """A proxy whose pid AND create time match the record is the child an
        earlier run of this daemon started (pid reuse cannot fake both)."""
        rec = self._read_owned_record()
        if not rec:
            return False
        return rec.get("server_pid") == pid and self._same_process(
            pid, rec.get("server_create_time")
        )

    def _is_mine(self, pid: int) -> bool:
        if self._owned_alive() and pid in _family(self._proc.pid):  # type: ignore[union-attr]
            return True
        return self._recorded_as_ours(pid)

    # ------------------------------------------------------------- locate
    def locate(self) -> tuple[str, str, int] | None:
        """``(url, token, pid)`` of a running Iron-Proxy for :meth:`data_dir`,
        or ``None``. Blocking (a file read + one loopback health probe). The
        health answer is kept for :meth:`_adopt` (version, features)."""
        desc = _read_descriptor(self.data_dir() / "proxy.json")
        if desc is None or not _pid_alive(desc["pid"]):
            return None
        health = _health(desc["url"])
        if health is None:
            return None
        self._note_health(health)
        return desc["url"], desc["token"], desc["pid"]

    def _note_health(self, health: dict[str, Any]) -> None:
        if isinstance(health.get("version"), str):
            self._version = health["version"]
        features = health.get("features")
        self._outdated = not (isinstance(features, list) and REQUIRED_FEATURE in features)
        self._features = frozenset(
            str(f) for f in (features if isinstance(features, list) else []) if isinstance(f, str)
        )

    def _adopt(self, url: str, token: str, pid: int, *, owned: bool) -> None:
        if self._client is not None and (self._url, self._token) != (url, token):
            self._client.close()
            self._client = None
        if (self._url, self._token) != (url, token):
            self._token_bad = False
        self._url, self._token, self._pid, self._owned = url, token, pid, owned
        if self._client is None:
            self._client = IronProxyClient(
                url,
                token,
                on_unreachable=self._invalidate,
                refresh=self._token_refused,
                on_refused=self._token_refused_final,
            )
        self._error = None

    def _adopt_located(self, loc: tuple[str, str, int]) -> None:
        url, token, pid = loc
        mine = self._is_mine(pid)
        self._adopt(url, token, pid, owned=mine)
        if not mine:
            self._proc = None

    def _token_refused(self) -> tuple[str, str] | None:
        """Iron-Proxy refused our token: re-read proxy.json ONCE (the proxy may
        have restarted with a new token). Returns what it names now, or None.
        The client rebinds itself; the service only tracks the new identity."""
        if not self._lock.acquire(timeout=5.0):
            return None
        try:
            loc = self.locate()
            if loc is None:
                return None
            url, token, pid = loc
            if token != self._token:
                self._token_bad = False
            self._url, self._token, self._pid = url, token, pid
            self._owned = self._is_mine(pid)
            return url, token
        finally:
            self._lock.release()

    def _token_refused_final(self) -> None:
        """The re-read did not help: our token is refused for good."""
        self._token_bad = True

    def _forget(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._url = self._token = None
        self._pid = None
        self._owned = False
        self._outdated = False
        self._stale_foreign = False
        self._stale_error = None
        self._token_bad = False
        self._usable = {}
        self._accounts_cache = None
        self._features = frozenset()

    def _invalidate(self) -> None:
        """A call could not reach Iron-Proxy: drop the cached client so the
        next :meth:`client` re-locates. The owned child (if any) is kept — the
        re-locate recognises it by pid."""
        self._client = None
        self._url = self._token = None

    def _owned_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------- a stale proxy we started
    def _missing_features(self) -> frozenset[str]:
        """Features the bundled Iron-Proxy has and the running one lacks."""
        return self._bundle_features - self._features

    def _replace_if_stale(self, pid: int | None) -> bool:
        """v1.303.3 (a LIVE bug): the running Iron-Proxy (``pid``, health
        already noted) lacks a feature the bundle has. When its command line
        runs an Iron Jarvis bundle (:func:`iron_jarvis_owner`) — this or
        another Iron Jarvis started it, owned record or not — stop that tree
        and start the current bundle; True when it did. Never: our OWN child
        of this run (it IS the current bundle); a pid twice; the same (bundle,
        version) twice, or more than :data:`REPLACE_LIMIT` times in
        :data:`REPLACE_WINDOW_S` (another Iron Jarvis sharing the data dir
        keeps starting it — said in ``status().error``, naming it); a process
        that did not die (its descriptor is kept, said). Anyone else's proxy
        is never touched and reads as outdated. Caller holds the lock."""
        missing = self._missing_features()
        self._stale_foreign = False
        self._stale_error = None
        if not missing or pid is None:
            return False
        if self._owned_alive() and pid in _family(self._proc.pid):  # type: ignore[union-attr]
            return False
        cmdline = _cmdline(pid)
        owner = iron_jarvis_owner(cmdline)
        if owner is None:
            self._stale_foreign = True
            # Not ours, so never stopped — but say WHICH process, or the user
            # (the v1.303.2 report: an old source-run proxy the packaged app
            # cannot recognise) has no way to act on "close it".
            what = _script_arg(cmdline) or (cmdline[0] if cmdline else "")
            if what:
                self._stale_error = OUTDATED_NAMED.format(pid=pid, what=what)
            return False
        if pid in self._replaced:
            self._stale_error = self._kill_failed.get(pid) or _another_iron_jarvis(owner)
            return False
        now = time.monotonic()
        self._replace_times = [t for t in self._replace_times if now - t < REPLACE_WINDOW_S]
        key = (_norm(_script_arg(cmdline) or ""), str(self._version or ""))
        if key in self._replaced_keys or len(self._replace_times) >= REPLACE_LIMIT:
            self._stale_error = _another_iron_jarvis(owner)
            return False
        log.warning(
            "iron-proxy pid %s runs an older Iron Jarvis bundle (missing %s); restarting it",
            pid, ", ".join(sorted(missing)),
        )
        self._replaced.add(int(pid))
        self._replaced_keys.add(key)
        self._replace_times.append(now)
        _kill_pids([int(pid)])
        if _pid_alive(int(pid)):
            # Still running: keep its descriptor (it still serves) and say so.
            self._kill_failed[int(pid)] = _could_not_stop(int(pid))
            self._stale_error = self._kill_failed[int(pid)]
            return False
        descriptor = self.data_dir() / "proxy.json"
        desc = _read_descriptor(descriptor)
        if desc is not None and desc["pid"] == pid:
            try:
                descriptor.unlink()  # the killed process cannot remove it
            except OSError:
                pass
        if self._proc is not None and not self._owned_alive():
            self._proc = None
        self._clear_owned_record()
        self._forget()
        try:
            self._spawn()
        except _StartProblem as exc:
            self._error = str(exc)
        return True

    # -------------------------------------------------------------- start
    def start(self) -> dict[str, Any]:
        """Reuse a running Iron-Proxy or start the bundled one. Never raises;
        returns :meth:`status`. Blocking — up to ~:data:`START_TIMEOUT_S`."""
        with self._lock:
            self._start_guarded()
            self._refresh_bundle_facts()
            return self.status()

    def _start_guarded(self) -> None:
        try:
            self._start_locked()
        except _StartProblem as exc:
            self._error = str(exc)
        except Exception as exc:  # noqa: BLE001 — a start never raises to a route
            log.warning("iron-proxy start failed", exc_info=True)
            self._error = f"Iron-Proxy could not be started ({type(exc).__name__})."

    def _start_locked(self) -> None:
        if self._url:
            health = _health(self._url)
            if health is not None:
                self._note_health(health)
                self._replace_if_stale(self._pid)
                return  # already running and answering (or just replaced)
        loc = self.locate()
        if loc is not None:
            if not self._replace_if_stale(loc[2]):
                self._adopt_located(loc)
            return
        if self._proc is not None or self._owned:
            # Our child is there but not answering: it is not coming back.
            self._kill_owned()
        self._forget()
        self._spawn()

    def _spawn(self) -> None:
        prefix, extra = self._command()
        data_dir = self.data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if log_path.exists() and log_path.stat().st_size > _LOG_MAX_BYTES:
                os.replace(log_path, log_path.with_suffix(".log.1"))
        except OSError:
            pass
        env = dict(os.environ)
        for name in _DAEMON_ONLY_ENV:
            env.pop(name, None)
        env.update(extra)
        argv = [*prefix, "serve", "--port", "0", "--data-dir", str(data_dir)]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True  # so the tree kill reaches the group
        with open(log_path, "ab") as logf:
            logf.write(
                f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} Iron Jarvis starting Iron-Proxy\n".encode()
            )
            logf.flush()
            proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=subprocess.STDOUT,
                cwd=str(data_dir),
                env=env,
                **kwargs,
            )
        self._proc = proc
        deadline = time.monotonic() + START_TIMEOUT_S
        descriptor = data_dir / "proxy.json"
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                self._proc = None
                raise _StartProblem(
                    f"Iron-Proxy stopped right after starting (exit code {rc}); "
                    f"its log is at {log_path}."
                )
            desc = _read_descriptor(descriptor)
            if desc is not None and desc["pid"] in _family(proc.pid):
                health = _health(desc["url"])
                if health is not None:
                    self._note_health(health)
                    self._adopt(desc["url"], desc["token"], desc["pid"], owned=True)
                    self._write_owned_record(proc, desc["pid"])
                    log.info("iron-proxy started (pid %s) at %s", desc["pid"], desc["url"])
                    return
            time.sleep(0.1)
        self._kill_owned()
        raise _StartProblem(
            f"Iron-Proxy did not answer within {int(START_TIMEOUT_S)} seconds of starting; "
            f"its log is at {log_path}."
        )

    # --------------------------------------------------------------- stop
    def _kill_owned(self) -> None:
        proc = self._proc
        ours: set[int] = set()
        if self._pid is not None:
            ours.add(self._pid)
        if proc is not None:
            ours |= _family(proc.pid)
            try:
                from ..sandbox.native import _kill_tree

                _kill_tree(proc)
            except Exception:  # noqa: BLE001 — fall back to the plain kill
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 — a kill we cannot confirm is still done
                pass
        else:
            # A child an EARLIER run of this daemon started (crash orphan):
            # no Popen handle — kill by pid, but only processes the record
            # proves are that child (pid AND create time).
            rec = self._read_owned_record() or {}
            pids: list[int] = []
            if self._same_process(rec.get("root_pid"), rec.get("root_create_time")):
                pids.append(int(rec["root_pid"]))
            if self._same_process(rec.get("server_pid"), rec.get("server_create_time")):
                pids.append(int(rec["server_pid"]))
            ours |= set(pids)
            _kill_pids(pids)
        self._proc = None
        self._clear_owned_record()
        # The killed child cannot remove its own descriptor. Remove it only
        # while it still names OUR child — never one the tray app wrote since.
        descriptor = self.data_dir() / "proxy.json"
        desc = _read_descriptor(descriptor)
        if desc is not None and desc["pid"] in ours:
            try:
                descriptor.unlink()
            except OSError:
                pass

    def stop(self) -> None:
        """Stop an OWNED Iron-Proxy; forget one the user started (never kill
        it). Never raises."""
        with self._lock:
            try:
                if self._owned or self._proc is not None:
                    self._kill_owned()
            except Exception:  # noqa: BLE001 — shutdown never raises
                log.debug("iron-proxy stop failed", exc_info=True)
            finally:
                # The queued re-checks go FIRST: closing the client below
                # frees the busy workers, which would pick them up.
                pool, self._refresh_pool = self._refresh_pool, None
                if pool is not None:
                    pool.shutdown(wait=False, cancel_futures=True)
                with self._refresh_lock:
                    self._refresh_futures.clear()
                self._forget()

    # ------------------------------------------------------------- health
    def check(self) -> bool:
        """Is Iron-Proxy running and answering right now? Refreshes the cached
        state (re-locates after a crash or a restart). Blocking."""
        if not self.enabled:
            return False
        with self._lock:
            self._refresh_bundle_facts()
            if self._url:
                health = _health(self._url)
                if health is not None:
                    self._note_health(health)
                    if self._replace_if_stale(self._pid):
                        return self.running()
                    return True
            if self._proc is not None and self._proc.poll() is not None:
                rc = self._proc.returncode
                self._proc = None
                self._clear_owned_record()
                self._forget()
                self._error = (
                    f"Iron-Proxy (started by Iron Jarvis) stopped (exit code {rc}); "
                    f"its log is at {self.log_path()}."
                )
            loc = self.locate()
            if loc is None:
                self._invalidate()
                self._usable = {}
                self._accounts_cache = None
                if self._error is None:
                    self._error = "Iron-Proxy is not running."
                return False
            if self._replace_if_stale(loc[2]):
                return self.running()
            self._adopt_located(loc)
            return True

    def ensure_running(self) -> bool:
        """The watchdog's cycle: running already, or start it; then refresh the
        account snapshot provider availability reads. Raises
        ``RuntimeError(<the sentence>)`` when it is still not running (or not
        usable), so the loop's health row carries the reason."""
        if not self.check():
            st = self.start()
            if not st["running"]:
                raise RuntimeError(st.get("error") or "Iron-Proxy is not running.")
        self.refresh_accounts()
        if self._outdated:
            raise RuntimeError(OUTDATED)
        return True

    # --------------------------------------------- the account snapshot
    def note_accounts(self, profiles: Any, states: Any) -> None:
        """Record which Iron-Proxy providers have an enabled CLI account that
        does not need sign-in (from a profiles + states read already in hand)."""
        with self._snapshot_lock:
            self._note_accounts_locked(profiles, states)

    def _note_accounts_locked(self, profiles: Any, states: Any) -> None:
        usable: dict[str, bool] = {}
        st_map = states if isinstance(states, dict) else {}
        for p in profiles if isinstance(profiles, list) else []:
            if not isinstance(p, dict) or p.get("lane") != "cli" or not p.get("enabled", True):
                continue
            st = st_map.get(p.get("id")) or {}
            status = st.get("status") if isinstance(st, dict) else None
            if status in ("unauthenticated", "disabled"):
                continue
            usable[str(p.get("provider"))] = True
        self._usable = usable
        if isinstance(profiles, list) and isinstance(states, dict):
            self._accounts_cache = (
                [dict(p) for p in profiles if isinstance(p, dict)],
                {str(k): dict(v) for k, v in states.items() if isinstance(v, dict)},
            )
            self._providers_seen = {
                str(p.get("provider"))
                for p in profiles
                if isinstance(p, dict) and (p.get("lane") or "cli") == "cli"
            }

    def refresh_accounts(self) -> None:
        """Re-read the accounts for :meth:`has_usable_account`. Blocking; never
        raises (a failed read leaves the snapshot empty = "no account")."""
        c = self._client
        if c is None or not self.enabled:
            self._usable = {}
            self._accounts_cache = None
            return
        try:
            self.note_accounts(c.profiles(), c.states())
        except Exception:  # noqa: BLE001 — availability never breaks on a read
            self._usable = {}
            self._accounts_cache = None

    # ------------------------------------------- re-checking a sign-in
    def _pool(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._refresh_pool is None:
            self._refresh_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="iron-proxy-refresh"
            )
        return self._refresh_pool

    def request_refresh(
        self, profile_id: str, *, force: bool = False
    ) -> "concurrent.futures.Future | None":
        """v1.303.3: ask Iron-Proxy to re-check ONE account's sign-in
        (``POST /iron/refresh {id}`` — it runs the vendor CLI's own status
        command), in the background. NON-blocking: returns the future (its
        result = that account's new state dict, or None), an in-flight one
        shared, or ``None`` when Iron-Proxy is not in reach or the account was
        re-checked within :data:`REFRESH_EVERY_S` (``force`` skips that — the
        manual "Check again" and a sign-in pane that just logged in). The
        answer lands in the account snapshot (chips, availability)."""
        pid = str(profile_id or "")
        if not pid or not self.enabled:
            return None
        with self._refresh_lock:
            fut = self._refresh_futures.get(pid)
            if fut is not None and not fut.done():
                return fut
            now = time.monotonic()
            if not force and now - self._refresh_at.get(pid, float("-inf")) < REFRESH_EVERY_S:
                return None
            client = self._client
            if client is None:
                return None
            self._refresh_at[pid] = now
            fut = self._pool().submit(self._do_refresh, client, pid)
            self._refresh_futures[pid] = fut
            return fut

    def _do_refresh(self, client: IronProxyClient, pid: str) -> dict[str, Any] | None:
        states = client.refresh(pid, timeout=REFRESH_TIMEOUT_S)
        st = next(
            (
                dict(x) for x in (states if isinstance(states, list) else [])
                if isinstance(x, dict) and x.get("profileId") == pid
            ),
            None,
        )
        if st is not None:
            self._merge_state(pid, st)
        return st

    def _merge_state(self, pid: str, state: dict[str, Any]) -> None:
        """One account's new state into the cached snapshot (and with it the
        per-provider "usable" answer). Never raises."""
        try:
            with self._snapshot_lock:
                cache = self._accounts_cache
                if cache is None:
                    return
                profiles, states = cache
                merged = dict(states)
                merged[pid] = dict(state)
                self._note_accounts_locked(profiles, merged)
        except Exception:  # noqa: BLE001 — a snapshot update never breaks a check
            log.debug("iron-proxy state merge failed", exc_info=True)

    def note_signin_started(self, profile_id: str) -> None:
        """The Sign in route opened a pane for this account: re-check it
        automatically for :data:`SIGNIN_WINDOW_S` (``recheck_signed_out``)."""
        if profile_id:
            self._signin_started[str(profile_id)] = time.monotonic()

    def signing_in(self, open_panes: Any = ()) -> set[str]:
        """Accounts with a sign-in IN PROGRESS: an open sign-in pane for it
        (``open_panes``, the ids the caller found) or a Sign in press within
        :data:`SIGNIN_WINDOW_S`."""
        now = time.monotonic()
        recent = {
            pid for pid, at in list(self._signin_started.items())
            if now - at < SIGNIN_WINDOW_S
        }
        return recent | {str(x) for x in open_panes or () if x}

    def recheck_signed_out(
        self,
        profiles: Any,
        states: Any,
        wait_s: float | None = None,
        open_panes: Any = (),
    ) -> dict[str, dict[str, Any]]:
        """Re-check the enabled CLI accounts that need sign-in (or are
        unknown) AND have a sign-in in progress (:meth:`signing_in`) —
        throttled per account, waiting up to ``wait_s``. Any other account is
        re-checked only on demand (``POST …/check``): Iron-Proxy's re-check
        reads the local credentials, so it would flip an account a real 401
        just signed out back to ready. Off the loop; ``GET /iron-proxy`` only.
        Returns ``{id: new state}`` for the checks that finished in time."""
        st_map = states if isinstance(states, dict) else {}
        wanted = self.signing_in(open_panes)
        futures: dict[str, concurrent.futures.Future] = {}
        for p in profiles if isinstance(profiles, list) else []:
            if not isinstance(p, dict) or p.get("lane") != "cli" or not p.get("enabled", True):
                continue
            pid = str(p.get("id") or "")
            if pid not in wanted:
                continue
            st = st_map.get(pid) if isinstance(st_map.get(pid), dict) else {}
            if str(st.get("status") or "unknown") not in RECHECK_STATES:
                continue
            fut = self.request_refresh(pid)
            if fut is not None:
                futures[pid] = fut
        if futures:
            wait = REFRESH_WAIT_S if wait_s is None else wait_s
            if wait > 0:
                concurrent.futures.wait(list(futures.values()), timeout=wait)
        out: dict[str, dict[str, Any]] = {}
        for pid, fut in futures.items():
            if fut.done() and fut.exception() is None and isinstance(fut.result(), dict):
                out[pid] = fut.result()
        return out

    def has_usable_account(self, ij_provider: str) -> bool:
        """Does Iron-Proxy (on, running, current) have an enabled CLI account
        for this Iron Jarvis provider that does not need sign-in? CACHED — reads
        the last snapshot only; safe on the event loop (provider availability,
        ``/health``)."""
        try:
            proxy = JARVIS_TO_PROXY.get(ij_provider)
            if proxy is None or not self.running() or self._outdated or self._token_bad:
                return False
            return bool(self._usable.get(proxy))
        except Exception:  # noqa: BLE001 — availability never raises
            return False

    def cached_accounts(self) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """v1.302.0: the last ``(profiles, states)`` read (the watch loop, every
        account route and every pane resolution refresh it), or ``None`` when
        Iron-Proxy is off / not running / has not been read. CACHED — no
        network, safe on the event loop (the Build pane rows read it)."""
        try:
            if not self.running():
                return None
            return self._accounts_cache
        except Exception:  # noqa: BLE001 — a chip never breaks a list
            return None

    def providers_seen(self) -> set[str]:
        """Iron-Proxy providers that had a CLI account at the last good read."""
        return set(self._providers_seen)

    def has_feature(self, name: str) -> bool:
        """Did the last ``/iron/health`` answer advertise ``name``? CACHED."""
        return name in self._features

    # ------------------------------------------------------------- status
    def running(self) -> bool:
        if not self.enabled or not self._url:
            return False
        return (not self._owned) or self._proc is None or self._owned_alive()

    def status(self) -> dict[str, Any]:
        """``{enabled, running, owned, url, version, error, bundled}`` — cheap
        (no network, no disk: safe on the event loop), and never the token."""
        running = self.running()
        version = self._version if running else None
        if running and version is None and self._owned:
            version = self._bundle_version_cached
        if not self.enabled:
            error = None
        elif not running:
            error = self._error
        elif self._stale_error:
            error = self._stale_error
        elif self._outdated or self._stale_foreign:
            error = OUTDATED
        elif self._token_bad:
            error = TOKEN_REFUSED
        else:
            error = None
        return {
            "enabled": self.enabled,
            "running": running,
            "owned": bool(running and self._owned),
            "url": self._url if running else None,
            "version": version,
            "error": error,
            "bundled": self._bundled_cached,
            "data_dir": str(self.data_dir()),
        }

    def client(self) -> IronProxyClient | None:
        """The client, or ``None`` when off / not running — for the Connections
        routes, which manage accounts even on an older Iron-Proxy. Re-locates
        (blocking, one file read + one health probe) when the cache was
        dropped; never waits behind a start in progress. A CALL that runs as an
        account uses :meth:`lease_client` instead."""
        if not self.enabled:
            return None
        c = self._client
        if c is not None:
            return c
        if not self._lock.acquire(timeout=0.5):
            return None  # a start/stop is in progress: not running yet
        try:
            if self._client is None:
                loc = self.locate()
                if loc is not None:
                    self._adopt_located(loc)
            return self._client
        except Exception:  # noqa: BLE001 — the adapters' door must never raise
            log.debug("iron-proxy client lookup failed", exc_info=True)
            return None
        finally:
            self._lock.release()

    def lease_client(self, timeout_s: float = 15.0) -> IronProxyClient | None:
        """The client a call that runs AS an Iron-Proxy account uses.

        ``None`` ONLY when Iron-Proxy is turned off (then the call runs exactly
        as before, on this PC's own login). When it is on, this waits (blocking
        — callers are already off the loop) up to ``timeout_s`` for a start in
        progress, starts Iron-Proxy itself when nothing is running (at most
        once per :data:`_LEASE_START_COOLDOWN_S`), and raises
        :class:`IronProxyUnavailable` with one sentence when it still cannot be
        used: not answering, too old (no ``executor-v1``), or our token
        refused. Never falls back to the default login silently."""
        if not self.enabled:
            return None
        if not self._lock.acquire(timeout=max(0.0, float(timeout_s))):
            raise IronProxyUnavailable(NOT_ANSWERING)
        try:
            if not self.enabled:
                return None
            if self._client is None:
                loc = self.locate()
                if loc is not None:
                    self._adopt_located(loc)
                elif time.monotonic() - self._last_lease_start >= _LEASE_START_COOLDOWN_S:
                    self._last_lease_start = time.monotonic()
                    self._start_guarded()
            if self._client is None:
                raise IronProxyUnavailable(NOT_ANSWERING)
            if self._token_bad:
                loc = self.locate()
                if loc is not None and loc[1] != self._token:
                    self._adopt_located(loc)
            if self._token_bad:
                raise IronProxyUnavailable(TOKEN_REFUSED)
            if self._outdated:
                raise IronProxyUnavailable(OUTDATED)
            return self._client
        finally:
            self._lock.release()


async def watch(
    service: IronProxyService,
    on_tick: Callable[[bool, BaseException | None], None],
    *,
    clear: Callable[[], None] | None = None,
    interval: float = 30.0,
    max_interval: float = 600.0,
) -> None:
    """The lifespan loop: while the switch is on, keep Iron-Proxy running, keep
    the account snapshot fresh, and report EACH cycle's outcome (CLAUDE.md
    "Armed is not healthy"). The first cycle runs as soon as the loop is
    scheduled — after boot, off the event loop — so a slow or failing start
    never delays the daemon. Off: nothing runs and the health row is cleared."""
    delay = interval
    while True:
        if service.enabled:
            try:
                await asyncio.to_thread(service.ensure_running)
                on_tick(True, None)
                delay = interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed cycle is a row, not a crash
                on_tick(False, exc)
                delay = min(max_interval, delay * 2)
        else:
            if clear is not None:
                clear()
            delay = interval
        await asyncio.sleep(delay)
