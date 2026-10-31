"""Start an ``npx -y`` / ``uvx`` pack's server DIRECTLY, without its launcher.

WHY (measured on the user's PC, v1.415.0). A stdio pack configured as
``npx -y <pkg>`` runs as ``cmd.exe`` → ``cmd.exe /c npx.CMD`` → ``node
npx-cli.js`` (~81 MB private, idle for the pack's whole life) → ``node
<server>`` (the real server, 26-47 MB). A ``uvx <pkg>`` pack runs as
``uvx.exe`` → ``uv.exe`` (~158 MB private, idle) → ``<pkg>.exe`` (a
trampoline) → ``python.exe``. With three npx packs and one uvx pack the
launchers held ~430 MB of the ~1.44 GB the whole app used. They do nothing
once the server is running: they only resolve WHICH files to run.

WHAT IT DOES. The launcher is asked — once, in the background, after a
pack has started the ordinary way — which files it runs; the answer is kept
(``<home>/mcp/direct-launch.json``) and every later start of that pack runs
those files itself (``node <bin entry> <args>``, or the uv environment's own
``Scripts\\<cmd>.exe <args>``). Nothing is computed by imitation: npx is
asked with ``npx -y --package=<spec> -- node -e …`` and uv with ``uvx --from
<spec> python -c …``, so the launcher resolves (and installs or upgrades)
exactly as it would to start the server, then prints where it put things and
exits.

FRESHNESS (measured). ``npx -y <pkg>`` with no version REVALIDATES the
package against the registry on EVERY start (npm 10.8.2, libnpmexec
``missingFromTree`` → ``pacote.manifest(..., preferOnline: true)``; the log
says ``GET 200 …/server-everything (cache revalidated)`` on each run) and
upgrades the copy in its cache when a newer one exists. ``uvx <pkg>``
re-resolves on every start too (the user's uv cache holds ~60 environments
for ``mcp-server-fetch``, one per day the dependencies moved). A direct start
skips that check, so the ask is repeated in the background at most once every
:data:`REFRESH_S` (a day) per pack — the next start after it runs what the
launcher would have run. A pack pinned to an exact version or a range (npx
does not revalidate those) is only asked when it has no answer yet.

NEVER A GUESS. Anything this module is not sure of starts the pack exactly as
before, through its launcher: no answer yet, an answer that no longer matches
the disk, a copy installed globally or in the working folder's own
``node_modules`` (npx would run THAT one), a version spec the cached copy does
not satisfy, a bin that is not plain JavaScript run by ``node``, an
``npx``/``uvx`` command line with any option other than ``-y``, an npm or uv
layout that is not what was measured, and every platform other than Windows
(where the launcher chain was measured). A direct start that does not answer
``initialize`` within :data:`DIRECT_INIT_TIMEOUT_S` falls back to the launcher
ONCE in the same start, and that pack is not started directly again for
:data:`FAILED_HOLD_S`. ``IRONJARVIS_MCP_DIRECT=off`` turns the whole thing
off.

What differs from a launcher start, on purpose: npm's script runner adds
``npm_*`` variables to the server's environment (``npm_lifecycle_event=npx``,
``npm_config_*``…); a direct start does not. ``PATH`` gets the same folders
in front that the launcher put there (read off its own answer).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.logging import get_logger

log = get_logger("mcp")

#: Set to ``off`` / ``0`` / ``false`` to start every pack through its launcher.
ENV_SWITCH = "IRONJARVIS_MCP_DIRECT"
#: How old an answer may get before a FRESH spec (no version / ``latest``)
#: asks its launcher again (in the background, after a start).
REFRESH_S = 24 * 3600.0
#: How long a pack whose direct start failed keeps starting via its launcher.
FAILED_HOLD_S = 24 * 3600.0
#: The bound on one background ask (an npx first install measured 9 s).
ASK_TIMEOUT_S = 120.0
#: How long a DIRECT start may take to answer ``initialize`` before the pack
#: is started through its launcher instead (a cached direct start of
#: server-everything answered in 0.6 s; through npx, 3.9-5.2 s).
DIRECT_INIT_TIMEOUT_S = 10.0
#: The marker the ask's one line of JSON starts with (npm prints warnings).
ASK_MARK = "IJASK"
#: The record file, under the state home.
RECORDS_NAME = "direct-launch.json"

_NPX_STEMS = {"npx"}
_UVX_STEMS = {"uvx"}
_LAUNCHER_SUFFIXES = {"", ".cmd", ".exe", ".bat"}
_YES = {"-y", "--yes", "--yes=true"}
_NPM_NAME = r"[A-Za-z0-9][A-Za-z0-9._~-]*"
_NPM_SPEC_RX = re.compile(
    rf"^(?P<name>(?:@{_NPM_NAME}/)?{_NPM_NAME})"
    r"(?:@(?P<ver>latest|\d+\.\d+\.\d+|[\^~]\d+\.\d+\.\d+))?$"
)
_SEMVER_RX = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_UV_SPEC_RX = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9._,-]+\])?"
    r"(?:@(?P<at>latest|\d[0-9A-Za-z.]*)|==(?P<eq>\d[0-9A-Za-z.]*))?$"
)
#: npm's cmd-shim for a bin run by ``node`` (npm 10, cmd-shim 6): the line
#: that runs the target, with NO extra arguments between program and file.
_SHIM_RUN_RX = re.compile(
    r'"%_prog%"\s+"%dp0%\\(?P<rel>[^"]+)"\s+%\*\s*$', re.MULTILINE
)
_SHIM_NODE_RX = re.compile(r'SET\s+"_prog=node"', re.IGNORECASE)
_JS_EXT = {".js", ".mjs", ".cjs"}

#: The npx ask: run under the same environment npx gives a bin, print where
#: things are. No ``|``, ``&``, ``<``, ``>``, ``^``, ``%`` or ``"``: npm hands
#: the command to cmd.exe.
_NPX_ASK_JS = (
    "const e=process.env;console.log('" + ASK_MARK + "'+JSON.stringify({"
    "node:process.execPath,path:e.PATH,cache:e.npm_config_cache,"
    "gp:e.npm_config_global_prefix,lp:e.npm_config_local_prefix}))"
)
_UV_ASK_PY = (
    "import sys,os,json;print('" + ASK_MARK + "'+json.dumps({'prefix':sys.prefix,"
    "'path':os.environ.get('PATH',''),'uv':os.environ.get('UV','')}))"
)


def enabled() -> bool:
    """Off only when the user says so (``IRONJARVIS_MCP_DIRECT=off``)."""
    raw = os.environ.get(ENV_SWITCH, "").strip().lower()
    return raw not in {"off", "0", "false", "no"}


def _norm(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


class Why(str):
    """Why a pack starts through its launcher. ``stale`` is True when the
    kept answer no longer matches the disk (the launcher is asked again after
    this start); a copy installed elsewhere, a hold or no answer is not."""

    stale: bool = False


def _stale(text: str) -> "Why":
    w = Why(text)
    w.stale = True
    return w


def npx_hash(spec: str) -> str:
    """npx's cache folder name for ONE package spec (libnpmexec: sha512 of
    the sorted, newline-joined specs, first 16 hex characters)."""
    return hashlib.sha512(spec.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# What the pack's command line says.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LaunchSpec:
    """A pack's ``npx -y <spec> …`` / ``uvx <spec> …`` command line, parsed."""

    kind: str  # "npx" | "uvx"
    launcher: str  # the resolved launcher path (npx.CMD, uvx.exe, uv.exe)
    launcher_args: tuple[str, ...]  # ("tool", "run") for `uv tool run`, else ()
    spec: str
    name: str  # npm package name / Python distribution name
    version: str  # "" (none), "latest", "1.2.3", "^1.2.3", "~1.2.3"
    server_args: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, str] | None = field(default=None, compare=False, repr=False)
    key: str = ""

    @property
    def fresh(self) -> bool:
        """The launcher re-checks for a newer version on every start, so the
        answer is asked again once it is a day old. npx only does that for a
        spec with no version or ``latest``; uv re-resolves every time."""
        if self.kind == "uvx":
            return True
        return self.version in ("", "latest")

    @property
    def launcher_name(self) -> str:
        return "npx" if self.kind == "npx" else "uvx"


def _env_view(env: dict[str, str] | None) -> dict[str, str]:
    base = env if env is not None else dict(os.environ)
    return {str(k): str(v) for k, v in base.items()}


#: The environment entries that can change what a launcher resolves.
_KEY_ENV_RX = re.compile(
    r"^(path|home|userprofile|appdata|localappdata|xdg_.*|npm_config_.*|uv_.*|"
    r"virtual_env|conda_prefix|python.*)$",
    re.IGNORECASE,
)


def _key_for(kind: str, launcher: str, launcher_args: tuple[str, ...], spec: str,
             cwd: str | None, env: dict[str, str]) -> str:
    """One pack's identity for its answer: launcher, spec, working folder and
    the environment entries that change resolution — HASHED (never the raw
    environment, which can hold keys)."""
    h = hashlib.sha256()
    for part in (kind, _norm(launcher), " ".join(launcher_args), spec, _norm(cwd or os.getcwd())):
        h.update(part.encode("utf-8", "replace") + b"\0")
    for k in sorted(k for k in env if _KEY_ENV_RX.match(k)):
        h.update(f"{k.upper()}={env[k]}".encode("utf-8", "replace") + b"\0")
    return h.hexdigest()[:32]


def parse(
    command: str,
    args: list[str] | tuple[str, ...] | None,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> LaunchSpec | None:
    """The pack's launcher command line parsed, or ``None`` when it is not an
    ``npx -y <spec> …`` / ``uvx <spec> …`` / ``uv tool run <spec> …`` line
    this module understands. ``command`` is the RESOLVED launcher path."""
    if os.name != "nt" or not command:
        return None
    stem, suffix = os.path.splitext(os.path.basename(command))
    stem, suffix = stem.lower(), suffix.lower()
    if suffix not in _LAUNCHER_SUFFIXES:
        return None
    argv = [str(a) for a in (args or [])]
    view = _env_view(env)
    if stem in _NPX_STEMS:
        i, saw_yes = 0, False
        while i < len(argv) and argv[i].startswith("-"):
            if argv[i] not in _YES:
                return None  # any other npx option: the launcher decides
            saw_yes = True
            i += 1
        if not saw_yes or i >= len(argv):
            return None  # without -y npx may ASK; never imitate a prompt
        spec = argv[i]
        m = _NPM_SPEC_RX.match(spec)
        if not m:
            return None
        return LaunchSpec(
            kind="npx", launcher=command, launcher_args=(), spec=spec,
            name=m.group("name"), version=m.group("ver") or "",
            server_args=tuple(argv[i + 1:]), cwd=cwd, env=env,
            key=_key_for("npx", command, (), spec, cwd, view),
        )
    launcher_args: tuple[str, ...] = ()
    if stem == "uv":
        if argv[:2] != ["tool", "run"]:
            return None
        launcher_args, argv = ("tool", "run"), argv[2:]
    elif stem not in _UVX_STEMS:
        return None
    if not argv or argv[0].startswith("-"):
        return None  # uvx options (--from, --python, …): the launcher decides
    spec = argv[0]
    m = _UV_SPEC_RX.match(spec)
    if not m:
        return None
    version = m.group("at") or m.group("eq") or ""
    return LaunchSpec(
        kind="uvx", launcher=command, launcher_args=launcher_args, spec=spec,
        name=m.group("name"), version=version, server_args=tuple(argv[1:]),
        cwd=cwd, env=env, key=_key_for("uvx", command, launcher_args, spec, cwd, view),
    )


# --------------------------------------------------------------------------- #
# The kept answers.
# --------------------------------------------------------------------------- #
_LOCK = threading.RLock()
_STATE_DIR: Path | None = None
_RECORDS: dict[str, dict[str, Any]] | None = None


def set_state_dir(home: str | os.PathLike[str] | None) -> None:
    """Where answers are kept (``<home>/mcp/direct-launch.json``). Set by
    ``build_platform``; unset, answers live in memory only and no background
    ask is ever made (a CLI one-shot, a unit test)."""
    global _STATE_DIR, _RECORDS
    with _LOCK:
        _STATE_DIR = Path(home) if home is not None else None
        _RECORDS = None


def _records_path() -> Path | None:
    return (_STATE_DIR / "mcp" / RECORDS_NAME) if _STATE_DIR is not None else None


def _load() -> dict[str, dict[str, Any]]:
    global _RECORDS
    with _LOCK:
        if _RECORDS is None:
            _RECORDS = {}
            path = _records_path()
            if path is not None:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        _RECORDS = {
                            str(k): v for k, v in data.items() if isinstance(v, dict)
                        }
                except FileNotFoundError:
                    pass
                except Exception as exc:  # noqa: BLE001 — a damaged file is no answer
                    log.warning("mcp direct-launch answers unreadable (%s); starting fresh", exc)
        return _RECORDS


def _save() -> None:
    path = _records_path()
    if path is None:
        return
    with _LOCK:
        data = dict(_load())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001 — memory still has the answer
            log.warning("mcp direct-launch answers not saved: %s", exc)


def record_for(spec: LaunchSpec) -> dict[str, Any] | None:
    with _LOCK:
        rec = _load().get(spec.key)
        return dict(rec) if rec is not None else None


def put_record(spec: LaunchSpec, rec: dict[str, Any]) -> None:
    """Keep an answer (tests and :func:`_ask` only)."""
    with _LOCK:
        _load()[spec.key] = dict(rec)
        _save()


def forget(spec: LaunchSpec | None = None) -> None:
    """Drop one pack's answer, or every answer (tests)."""
    global _RECORDS
    with _LOCK:
        if spec is None:
            _RECORDS = {}
        else:
            _load().pop(spec.key, None)
        _save()


# --------------------------------------------------------------------------- #
# Deciding whether a direct start is certain.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DirectPlan:
    """How a pack is started without its launcher."""

    spec: LaunchSpec
    argv: tuple[str, ...]
    target: str  # the entry file (npx) / console-script exe (uvx)
    version: str
    path_prepend: tuple[str, ...] = ()
    extra_env: tuple[tuple[str, str], ...] = ()

    def env_for(self, base: dict[str, str] | None) -> dict[str, str]:
        """The pack's own environment, with the folders the launcher puts in
        front of ``PATH`` and the variables it adds (uv's ``UV``)."""
        env = dict(base) if base is not None else dict(os.environ)
        if self.path_prepend:
            keys = [k for k in env if k.upper() == "PATH"] or ["PATH"]
            for k in keys:
                env[k] = os.pathsep.join([*self.path_prepend, env.get(k, "")]).rstrip(os.pathsep)
        for k, v in self.extra_env:
            env[k] = v
        return env

    def describe(self) -> str:
        exe = os.path.basename(self.argv[0])
        return f"{exe} {self.target}" if self.spec.kind == "npx" else self.target


def _parse_semver(text: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RX.match(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def satisfies(installed: str, wanted: str) -> bool:
    """Whether ``installed`` meets the npm version spec ``wanted`` — for the
    forms :func:`parse` accepts only ("", latest, x.y.z, ^x.y.z, ~x.y.z). A
    pre-release or unreadable installed version never satisfies a range."""
    if wanted in ("", "latest"):
        return True
    if wanted[0] not in "^~":
        return installed == wanted
    have, base = _parse_semver(installed), _parse_semver(wanted[1:])
    if have is None or base is None or have < base:
        return False
    if wanted[0] == "~":
        return have[:2] == base[:2]
    if base[0] > 0:
        return have[0] == base[0]
    if base[1] > 0:
        return have[:2] == base[:2]
    return have == base


def bin_name(manifest: dict[str, Any]) -> tuple[str, str] | None:
    """npm's own choice of the bin npx runs for a package (libnpmexec
    ``getBinFromManifest``): one bin (or several aliases of ONE file) → that
    one; else the bin named like the unscoped package; else none."""
    name = str(manifest.get("name") or "")
    bins = manifest.get("bin")
    unscoped = name.split("/", 1)[1] if name.startswith("@") and "/" in name else name
    if isinstance(bins, str):
        bins = {unscoped: bins}
    if not isinstance(bins, dict) or not bins:
        return None
    if len({str(v) for v in bins.values()}) == 1:
        key = next(iter(bins))
        return str(key), str(bins[key])
    if unscoped in bins:
        return unscoped, str(bins[unscoped])
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([_norm(path), _norm(root)]) == _norm(root)
    except ValueError:
        return False


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _local_copy(name: str, spec: str, lp: Path) -> str:
    """Why npx would run a copy in the working folder's own project instead
    of its cache, or ``""``. npx looks in its local prefix's tree (any depth
    for a spec with no version) and for a bin named like the spec."""
    nm = lp / "node_modules"
    if (nm / name).exists():
        return f"{name} is installed in {lp}"
    if "/" not in spec:
        for ext in ("", ".cmd"):
            if (nm / ".bin" / f"{spec}{ext}").exists():
                return f"{spec} is a command in {lp}"
    try:
        own = _read_json(lp / "package.json")
    except FileNotFoundError:
        own = None
    except Exception:  # noqa: BLE001 — a project file we cannot read: unsure
        return f"{lp / 'package.json'} could not be read"
    if isinstance(own, dict):
        if own.get("name") == name:
            return f"{lp} is the package itself"
        if isinstance(own.get("bin"), dict) and spec in own["bin"]:
            return f"{lp} has its own {spec} command"
    if nm.is_dir():
        try:
            lock = _read_json(nm / ".package-lock.json")
        except FileNotFoundError:
            return f"{nm} has packages and no list of them"
        except Exception:  # noqa: BLE001
            return f"{nm / '.package-lock.json'} could not be read"
        pkgs = lock.get("packages") if isinstance(lock, dict) else None
        if not isinstance(pkgs, dict):
            return f"{nm / '.package-lock.json'} is not npm's list"
        tail = f"node_modules/{name}"
        if any(str(k).replace("\\", "/").endswith(tail) for k in pkgs):
            return f"{name} is installed in {lp}"
    return ""


def _plan_npx(spec: LaunchSpec, rec: dict[str, Any]) -> "tuple[DirectPlan | None, str]":
    try:
        node = Path(str(rec["node"]))
        cache = Path(str(rec["cache"]))
        gp, lp = Path(str(rec["gp"])), Path(str(rec["lp"]))
        prepend = tuple(str(p) for p in rec.get("path_prepend") or [])
    except (KeyError, TypeError):
        return None, _stale("the kept answer is not complete")
    install = cache / "_npx" / npx_hash(spec.spec)
    if node.name.lower() != "node.exe" or not node.is_file():
        return None, _stale(f"{node} is gone")
    if not install.is_dir():
        return None, _stale("npx has no copy of the package in its cache")
    try:
        own = _read_json(install / "package.json")
    except Exception:  # noqa: BLE001
        return None, _stale("npx's copy has no readable package.json")
    deps = own.get("dependencies") if isinstance(own, dict) else None
    if not isinstance(deps, dict) or list(deps) != [spec.name]:
        return None, _stale("npx's copy is not the one package expected")
    pkg_dir = install / "node_modules" / spec.name
    try:
        manifest = _read_json(pkg_dir / "package.json")
    except Exception:  # noqa: BLE001
        return None, _stale("the cached package has no readable package.json")
    if not isinstance(manifest, dict) or manifest.get("name") != spec.name:
        return None, _stale("the cached package is not the one named")
    version = str(manifest.get("version") or "")
    if not satisfies(version, spec.version):
        return None, _stale(f"the cached copy is {version}, not {spec.version}")
    chosen = bin_name(manifest)
    if chosen is None:
        return None, "npm could not pick one command from the package"
    bname, bpath = chosen
    target = (pkg_dir / bpath).resolve(strict=False)
    if not _inside(target, pkg_dir) or not target.is_file():
        return None, _stale(f"the package's {bname} command file is missing")
    if target.suffix.lower() not in _JS_EXT:
        return None, f"the package's {bname} command is not a JavaScript file"
    bindir = install / "node_modules" / ".bin"
    if (bindir / "node.exe").exists():
        return None, "npx's copy brings its own node.exe"
    try:
        shim = (bindir / f"{bname}.cmd").read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None, _stale(f"npm's {bname}.cmd launcher is missing")
    run = _SHIM_RUN_RX.search(shim)
    if not run or not _SHIM_NODE_RX.search(shim):
        return None, f"npm's {bname}.cmd launcher is not the plain node form"
    if _norm((bindir / run.group("rel")).resolve(strict=False)) != _norm(target):
        return None, f"npm's {bname}.cmd launcher runs another file"
    if (gp / "node_modules" / spec.name).exists():
        return None, f"{spec.name} is also installed globally"
    if "/" not in spec.spec and any((gp / f"{spec.spec}{e}").exists() for e in ("", ".cmd")):
        return None, f"{spec.spec} is also a global command"
    local = _local_copy(spec.name, spec.spec, lp)
    if local:
        return None, local
    return DirectPlan(
        spec=spec,
        argv=(str(node), str(target), *spec.server_args),
        target=str(target),
        version=version,
        path_prepend=prepend,
    ), ""


def _uv_tool_dirs(env: dict[str, str]) -> list[Path]:
    up = {k.upper(): v for k, v in env.items()}
    out: list[Path] = []
    if up.get("UV_TOOL_DIR"):
        out.append(Path(up["UV_TOOL_DIR"]))
    if up.get("XDG_DATA_HOME"):
        out.append(Path(up["XDG_DATA_HOME"]) / "uv" / "tools")
    if up.get("APPDATA"):
        out.append(Path(up["APPDATA"]) / "uv" / "tools")
    return out


def _dist_versions(prefix: Path, name: str) -> list[str]:
    want = re.sub(r"[-_.]+", "_", name).lower()
    site = prefix / "Lib" / "site-packages"
    found: list[str] = []
    try:
        entries = list(site.iterdir())
    except OSError:
        return found
    for d in entries:
        n = d.name
        if not n.lower().endswith(".dist-info"):
            continue
        stem = n[: -len(".dist-info")]
        dist, _, ver = stem.rpartition("-")
        if re.sub(r"[-_.]+", "_", dist).lower() == want:
            found.append(ver)
    return found


def _plan_uvx(spec: LaunchSpec, rec: dict[str, Any]) -> "tuple[DirectPlan | None, str]":
    try:
        prefix = Path(str(rec["prefix"]))
        prepend = tuple(str(p) for p in rec.get("path_prepend") or [])
        uv = str(rec.get("uv") or "")
    except (KeyError, TypeError):
        return None, _stale("the kept answer is not complete")
    if not (prefix / "pyvenv.cfg").is_file():
        return None, _stale("uv's environment for the package is gone")
    exe = prefix / "Scripts" / f"{spec.name}.exe"
    if not exe.is_file():
        return None, _stale(f"uv's environment has no {spec.name}.exe")
    versions = _dist_versions(prefix, spec.name)
    if len(versions) != 1:
        return None, _stale(f"uv's environment does not hold exactly one {spec.name}")
    if spec.version and spec.version != "latest" and versions[0] != spec.version:
        return None, _stale(f"uv's environment holds {versions[0]}, not {spec.version}")
    view = _env_view(spec.env)
    for tools in _uv_tool_dirs(view):
        installed = tools / spec.name
        if installed.exists() and not _inside(prefix, installed):
            return None, f"{spec.name} is also installed as a uv tool"
    return DirectPlan(
        spec=spec,
        argv=(str(exe), *spec.server_args),
        target=str(exe),
        version=versions[0],
        path_prepend=prepend,
        extra_env=(("UV", uv),) if uv else (),
    ), ""


def plan(spec: LaunchSpec | None, *, now: float | None = None) -> "tuple[DirectPlan | None, Why]":
    """A direct start for ``spec`` when one is CERTAIN, else ``(None, why)``
    and the pack starts through its launcher. Cheap: file reads only. ``why``
    is ``""`` with a plan; ``why.stale`` says the kept answer no longer
    matches the disk."""
    found, why = _plan(spec, now=now)
    return found, why if isinstance(why, Why) else Why(why)


def _plan(spec: LaunchSpec | None, *, now: float | None = None) -> "tuple[DirectPlan | None, str]":
    if spec is None:
        return None, "not an npx or uvx pack"
    if not enabled():
        return None, "direct starts are turned off"
    rec = record_for(spec)
    if rec is None:
        return None, f"{spec.launcher_name} has not been asked yet"
    when = time.time() if now is None else now
    failed_at = float(rec.get("failed_at") or 0)
    if failed_at and when - failed_at < FAILED_HOLD_S:
        return None, "its direct start failed recently"
    if rec.get("kind") != spec.kind:
        return None, _stale("the kept answer is for another launcher")
    try:
        return (_plan_npx if spec.kind == "npx" else _plan_uvx)(spec, rec)
    except Exception as exc:  # noqa: BLE001 — never a guess: the launcher decides
        return None, f"the kept answer could not be checked ({type(exc).__name__})"


# --------------------------------------------------------------------------- #
# Asking the launcher (in the background).
# --------------------------------------------------------------------------- #
def ask_argv(spec: LaunchSpec) -> list[str]:
    if spec.kind == "npx":
        return [spec.launcher, "-y", f"--package={spec.spec}", "--", "node", "-e", _NPX_ASK_JS]
    return [spec.launcher, *spec.launcher_args, "--from", spec.spec, "python", "-c", _UV_ASK_PY]


def _path_list(value: str) -> list[str]:
    return [p for p in (value or "").split(os.pathsep) if p]


def _prepended(asked: list[str], ours: list[str]) -> list[str] | None:
    """The folders the launcher put in front of OUR ``PATH`` — or ``None``
    when its ``PATH`` does not end with ours (then it is not known)."""
    a = [_norm(p) for p in asked]
    o = [_norm(p) for p in ours]
    if not o:
        return list(asked)
    if len(a) >= len(o) and a[len(a) - len(o):] == o:
        return list(asked[: len(a) - len(o)])
    return None


def answer_from(spec: LaunchSpec, stdout: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The record an ask's output makes, or ``None`` when the output is not
    the one line expected (then nothing is kept)."""
    line = next(
        (ln.strip() for ln in (stdout or "").splitlines() if ln.strip().startswith(ASK_MARK)), ""
    )
    if not line:
        return None
    try:
        data = json.loads(line[len(ASK_MARK):])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ours = _path_list(next((v for k, v in _env_view(spec.env).items() if k.upper() == "PATH"), ""))
    prepend = _prepended(_path_list(str(data.get("path") or "")), ours)
    if prepend is None:
        return None
    rec: dict[str, Any] = {
        "kind": spec.kind,
        "spec": spec.spec,
        "asked_at": time.time() if now is None else now,
        "path_prepend": prepend,
    }
    if spec.kind == "npx":
        for want in ("node", "cache", "gp", "lp"):
            if not isinstance(data.get(want), str) or not data[want]:
                return None
            rec[want] = data[want]
        install = Path(rec["cache"]) / "_npx" / npx_hash(spec.spec) / "node_modules" / ".bin"
        # npx's own install folder comes FIRST in what it prepends: anything
        # else means it ran a copy from somewhere else (local / global).
        if not prepend or _norm(prepend[0]) != _norm(install):
            return None
    else:
        if not isinstance(data.get("prefix"), str) or not data["prefix"]:
            return None
        rec["prefix"] = data["prefix"]
        rec["uv"] = str(data.get("uv") or "")
    return rec


def _run_ask_default(argv: list[str], *, env: dict[str, str] | None, cwd: str | None,
                     timeout: float) -> tuple[int, str]:
    from ..core import procs

    res = procs.run(argv, timeout=timeout, env=env, cwd=cwd, background=True)
    if res.timed_out:
        return -1, ""
    return res.returncode, procs.text(res.stdout, "utf-8")


#: The ask runner (a seam for tests): ``(argv, env=, cwd=, timeout=) -> (rc, stdout)``.
ask_runner: Callable[..., tuple[int, str]] = _run_ask_default


def ask(spec: LaunchSpec) -> dict[str, Any] | None:
    """Ask the launcher where it runs the package from; keep the answer.
    Blocking (a background thread calls it); never raises."""
    try:
        rc, out = ask_runner(ask_argv(spec), env=spec.env, cwd=spec.cwd, timeout=ASK_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        log.info("mcp direct-launch: asking %s about %s failed: %s", spec.launcher_name, spec.spec, exc)
        return None
    if rc != 0:
        log.info("mcp direct-launch: %s exited %s while asked about %s", spec.launcher_name, rc, spec.spec)
        return None
    rec = answer_from(spec, out)
    if rec is None:
        log.info("mcp direct-launch: %s's answer about %s was not usable", spec.launcher_name, spec.spec)
        return None
    with _LOCK:
        old = _load().get(spec.key) or {}
        # A failed direct start keeps its hold until it runs out, unless the
        # launcher now answers with DIFFERENT files.
        if old.get("failed_at") and _same_target(old, rec):
            rec["failed_at"] = old["failed_at"]
            rec["failed_why"] = old.get("failed_why", "")
        _load()[spec.key] = rec
        _save()
    return rec


def _same_target(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("node", "cache", "gp", "lp") if a.get("kind") == "npx" else ("prefix",)
    return all(_norm(str(a.get(k, ""))) == _norm(str(b.get(k, ""))) for k in keys)


def note_direct_failed(spec: LaunchSpec, why: str, *, now: float | None = None) -> None:
    """A direct start did not answer: hold this pack on its launcher."""
    with _LOCK:
        rec = _load().get(spec.key)
        if rec is None:
            return
        rec["failed_at"] = time.time() if now is None else now
        rec["failed_why"] = (why or "")[:300]
        _save()


_QUEUE: "queue.Queue[LaunchSpec]" = queue.Queue(maxsize=64)
_PENDING: set[str] = set()
_WORKER: threading.Thread | None = None


def _worker() -> None:
    while True:
        spec = _QUEUE.get()
        try:
            ask(spec)
        finally:
            with _LOCK:
                _PENDING.discard(spec.key)
            _QUEUE.task_done()


def needs_ask(spec: LaunchSpec, *, now: float | None = None) -> bool:
    rec = record_for(spec)
    if rec is None:
        return True
    if not spec.fresh:
        return False
    when = time.time() if now is None else now
    return when - float(rec.get("asked_at") or 0) >= REFRESH_S


def note_started(spec: LaunchSpec | None, *, plan_failed: bool = False) -> bool:
    """A pack started (any way). Queue a background ask when it has no
    answer yet, its answer did not hold on disk (``plan_failed``), or it is a
    fresh spec whose answer is a day old. ONE ask runs at a time (each is an
    npm or uv process of 80-160 MB for a few seconds). Only with a state home
    (the daemon): a CLI one-shot or a unit test never asks. Returns whether
    an ask was queued."""
    global _WORKER
    if spec is None or not enabled() or _STATE_DIR is None:
        return False
    if not (plan_failed or needs_ask(spec)):
        return False
    with _LOCK:
        if spec.key in _PENDING:
            return False
        try:
            _QUEUE.put_nowait(spec)
        except queue.Full:
            return False
        _PENDING.add(spec.key)
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker, name="mcp-direct-ask", daemon=True)
            _WORKER.start()
    return True


def drain(timeout: float = 60.0) -> bool:
    """Wait until every queued ask has run (tests). True when they did."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _LOCK:
            if not _PENDING:
                return True
        time.sleep(0.05)
    return False


__all__ = [
    "ASK_TIMEOUT_S",
    "DIRECT_INIT_TIMEOUT_S",
    "DirectPlan",
    "ENV_SWITCH",
    "FAILED_HOLD_S",
    "LaunchSpec",
    "REFRESH_S",
    "answer_from",
    "ask",
    "ask_argv",
    "bin_name",
    "drain",
    "enabled",
    "forget",
    "note_direct_failed",
    "note_started",
    "npx_hash",
    "parse",
    "plan",
    "put_record",
    "record_for",
    "satisfies",
    "set_state_dir",
]
