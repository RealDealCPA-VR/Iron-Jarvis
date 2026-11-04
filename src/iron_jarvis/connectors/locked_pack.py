"""An app's program installed from a LOCK Iron Jarvis ships (v1.428.0).

WHY: ``npx -y <package>`` asks npm for the newest release of the package AND
the newest allowed version of every package it depends on, at every start.
For an app that reads client mail, that means new third-party code could run
on this PC on any day without anyone choosing it. A locked package is
installed ONCE from ``connectors/locked/<key>/package-lock.json`` (shipped with
the app): every one of its packages is pinned by version and its sha512, which
``npm ci`` checks before anything is written. A changed lock installs into a
new folder; nothing is ever updated in place.

HOW (``ensure_installed``, BLOCKING — call it off the loop):

* ``npm ci --omit=optional --omit=dev --ignore-scripts`` in a temp folder
  beside the final one — NO package's install step runs;
* then ``npm rebuild <native>`` runs the install step of the ONE package that
  needs it (``keytar``: it keeps the sign-in's encryption key in Windows
  Credential Manager instead of in a file beside the sign-in);
* the pinned package's own ``package.json`` must say the pinned version; then a
  marker naming the lock's hash is written and the folder is moved into place.

WHERE: ``<userData>/mcp-packages/<key>-<lock hash>`` — beside the state home
like the animated backgrounds (``backgrounds.store.folder_for``), so a backup
never carries it; a dev or test home keeps it in ``<home>/mcp-packages``,
which ``create_backup`` leaves out.

The app then runs as ``node <entry>`` with that folder as its working folder
(a package that reads a ``.env`` from its working folder reads only ours).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FOLDER = "mcp-packages"
MARKER = ".ij-installed"
#: How long one install may take (``npm ci`` + the one native rebuild).
INSTALL_TIMEOUT_S = 300.0

NO_NODE = (
    "Node.js isn't installed on this PC, so this app's program can't run. Install Node.js "
    "(the LTS version from nodejs.org), then connect again."
)


@dataclass(frozen=True)
class LockedPackage:
    """One app program Iron Jarvis installs from its own lock."""

    key: str
    #: The npm package the app runs, and the version the lock pins.
    package: str
    version: str
    #: The program, relative to the install folder.
    entry: str
    #: The ONE package whose install step may run (``npm rebuild``), or "".
    native: str = ""
    #: v1.428.0 review: the native file that install step produces is NOT
    #: covered by the lock's sha512 (keytar's ``prebuild-install`` downloads it
    #: from GitHub with no checksum). Per platform: ``(relative path, sha256)``
    #: of the exact file Iron Jarvis checked. Another file is refused; on a
    #: platform not listed the step does not run at all (the package then keeps
    #: its key in a file beside the sign-in).
    native_files: tuple[tuple[str, str, str], ...] = ()

    @property
    def lock_dir(self) -> Path:
        return Path(__file__).parent / "locked" / self.key


MS365 = LockedPackage(
    key="ms365",
    package="@softeria/ms-365-mcp-server",
    version="0.170.2",
    entry="node_modules/@softeria/ms-365-mcp-server/dist/index.js",
    native="keytar",
    # keytar 7.9.0, napi-v3, win32-x64: the file inside GitHub's release
    # asset keytar-v7.9.0-napi-v3-win32-x64.tar.gz (221,340 bytes, the size
    # GitHub lists; it publishes no checksum for it).
    native_files=(
        ("win32-x64", "node_modules/keytar/build/Release/keytar.node",
         "90e35de89ab5e5f9290e4ff1bbadcf221a82b2aa0d9b922187dc980adff3c831"),
    ),
)

PACKAGES: dict[str, LockedPackage] = {MS365.key: MS365}

_LOCK = threading.Lock()


class LockedInstallError(RuntimeError):
    """The install could not finish; ``str()`` is one plain sentence."""


def lock_hash(pkg: LockedPackage) -> str:
    """The first 12 hex of sha256 over the shipped ``package.json`` and
    ``package-lock.json``: a changed lock is a new folder."""
    h = hashlib.sha256()
    for name in ("package.json", "package-lock.json"):
        h.update(name.encode("utf-8") + b"\0")
        h.update((pkg.lock_dir / name).read_bytes())
    return h.hexdigest()[:12]


def install_root(home: str | os.PathLike[str]) -> Path:
    """``<userData>/mcp-packages`` beside the state home (a ``logs`` folder
    there marks the desktop's userData), else ``<home>/mcp-packages``."""
    home_path = Path(home)
    if (home_path.parent / "logs").is_dir():
        return home_path.parent / FOLDER
    return home_path / FOLDER


def install_dir(pkg: LockedPackage, home: str | os.PathLike[str]) -> Path:
    return install_root(home) / f"{pkg.key}-{lock_hash(pkg)}"


def entry_path(pkg: LockedPackage, home: str | os.PathLike[str]) -> Path:
    return install_dir(pkg, home) / pkg.entry


def node_program() -> str:
    """The ``node`` this PC runs (absolute), or "" when none is on PATH."""
    return shutil.which("node") or ""


def npm_program() -> str:
    return shutil.which("npm") or ""


def installed(pkg: LockedPackage, home: str | os.PathLike[str]) -> bool:
    """Is the locked install in place: its marker names this lock and the
    program is there? File reads only."""
    folder = install_dir(pkg, home)
    try:
        marker = (folder / MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return marker == lock_hash(pkg) and (folder / pkg.entry).is_file()


def platform_key() -> str:
    """``win32-x64`` and the like: the platform a native file is checked for."""
    import platform as _platform
    import sys

    machine = (_platform.machine() or "").lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return f"{sys.platform}-{arch}"


def native_file(pkg: LockedPackage) -> tuple[str, str] | None:
    """``(relative path, sha256)`` checked for this platform, or None."""
    key = platform_key()
    for plat, rel, digest in pkg.native_files:
        if plat == key:
            return rel, digest
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


#: v1.428.0 review: settings the program reads from its environment that Iron
#: Jarvis does not choose — a daemon started with any of them must not hand
#: them on (an OAuth token that skips the account lock, a command that stores
#: the sign-in, extra permissions, Node options that load code).
_STRIPPED_PREFIXES = ("MS365_MCP_",)
_STRIPPED_NAMES = frozenset({"ENABLED_TOOLS", "READ_ONLY", "NODE_OPTIONS", "NODE_PATH"})


def clean_env(base: "dict[str, str] | os._Environ[str]") -> dict[str, str]:
    """``base`` without the settings a locked program must only get from Iron
    Jarvis (case-insensitive: Windows keeps one variable per name)."""
    out: dict[str, str] = {}
    for key, value in dict(base).items():
        upper = str(key).upper()
        if upper in _STRIPPED_NAMES or upper.startswith(_STRIPPED_PREFIXES):
            continue
        out[str(key)] = str(value)
    return out


def signin_dir(pkg: LockedPackage, home: str | os.PathLike[str]) -> Path:
    """Where the program keeps ITS sign-in (``MS365_MCP_TOKEN_CACHE_PATH`` /
    ``MS365_MCP_SELECTED_ACCOUNT_PATH``): beside the installs, never the
    package's shared ``%APPDATA%`` folder another copy of it would read. Kept
    across a new lock (it is not inside an install folder)."""
    return install_root(home) / "_signin" / pkg.key


def signin_dir_for_cwd(pkg: LockedPackage, cwd: str) -> Path:
    """The same folder, from a saved row's working folder (the install)."""
    return Path(cwd).parent / "_signin" / pkg.key


#: v1.428.0 review: the names and package words that mean "this is a locked
#: app's program". A pack added any OTHER way (a pasted config, an import from
#: Claude Desktop, the npm door, a row from before v1.428.0) that runs one is
#: refused at the add door and not loaded at boot: it would run the program at
#: npm's newest, with every tool and on any model — around the lock entirely.
RESERVED: dict[str, tuple[str, tuple[str, ...]]] = {
    # key: (the app's name in words, words in a command line that run it)
    "ms365": ("Microsoft 365", ("ms-365-mcp-server",)),
}
RESERVED_NAMES: dict[str, str] = {"microsoft_365": "ms365"}


def locked_elsewhere_words(app: str) -> str:
    return (
        f"{app} is set up only from its card in Settings › Connections, where it is "
        "locked down (read and draft only, one account, your own models only) — "
        "not added as an extension."
    )


def unlocked_copy(cfg: Any) -> str:
    """The app's name when a pack row (or an add body, read the same way) runs
    a LOCKED app's program without being that app's locked row — "" otherwise.
    Reads the name, the command, every argument, a package and a URL."""
    get = cfg.get if isinstance(cfg, dict) else (lambda k, d=None: getattr(cfg, k, d))
    if get("locked"):
        return ""
    name = str(get("name") or "").strip().lower()
    key = RESERVED_NAMES.get(name)
    if key:
        return RESERVED[key][0]
    text = " ".join(
        str(x)
        for x in [get("command") or "", *list(get("args") or []), get("package") or "", get("url") or ""]
    ).lower()
    for app, words in RESERVED.values():
        if any(w in text for w in words):
            return app
    return ""


def installed_version(folder: Path, pkg: LockedPackage) -> str:
    """The version the package's own ``package.json`` says, "" when unread."""
    try:
        data = json.loads(
            (folder / "node_modules" / Path(pkg.package) / "package.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    return str(data.get("version") or "") if isinstance(data, dict) else ""


def _tail(raw: bytes) -> str:
    """npm's last three lines, masked, at most 300 characters."""
    from ..detections.redact import mask

    lines = [ln.strip() for ln in (raw or b"").decode("utf-8", "replace").splitlines() if ln.strip()]
    return mask(" | ".join(lines[-3:]))[:300]


def _npm(argv: list[str], cwd: Path) -> None:
    from ..core import procs

    result = procs.run(argv, timeout=INSTALL_TIMEOUT_S, cwd=str(cwd))
    if result.timed_out:
        raise LockedInstallError(
            f"Installing the app's program took longer than {int(INSTALL_TIMEOUT_S)} seconds, "
            "so it was stopped. Check this PC's internet connection, then connect again."
        )
    if result.returncode != 0:
        detail = _tail(result.stderr or result.stdout)
        raise LockedInstallError(
            "npm could not install the app's program"
            + (f": {detail}" if detail else ".")
        )


def ensure_installed(pkg: LockedPackage, home: str | os.PathLike[str]) -> Path:
    """The installed program's path, installing it from the lock first when it
    is not in place. BLOCKING (npm runs for seconds). Raises
    :class:`LockedInstallError` in plain words; a failed install leaves no
    half-made folder behind."""
    with _LOCK:
        final = install_dir(pkg, home)
        if installed(pkg, home):
            return final / pkg.entry
        npm = npm_program()
        if not npm or not node_program():
            raise LockedInstallError(NO_NODE)
        root = install_root(home)
        root.mkdir(parents=True, exist_ok=True)
        temp = root / f".{pkg.key}-{uuid.uuid4().hex[:8]}.tmp"
        try:
            temp.mkdir()
            for name in ("package.json", "package-lock.json"):
                shutil.copyfile(pkg.lock_dir / name, temp / name)
            _npm([npm, "ci", "--omit=optional", "--omit=dev", "--ignore-scripts",
                  "--no-audit", "--no-fund"], temp)
            checked = native_file(pkg)
            if pkg.native and checked is not None:
                _npm([npm, "rebuild", pkg.native], temp)
                rel, digest = checked
                built = temp / rel
                if not built.is_file() or _sha256(built) != digest:
                    raise LockedInstallError(
                        f"{pkg.native}'s native file is not the one Iron Jarvis checked, so "
                        "nothing was installed."
                    )
            got = installed_version(temp, pkg)
            if got != pkg.version:
                raise LockedInstallError(
                    f"npm installed {pkg.package} {got or '(unknown version)'}, not the "
                    f"{pkg.version} Iron Jarvis locked, so it was not used."
                )
            if not (temp / pkg.entry).is_file():
                raise LockedInstallError("npm finished, but the app's program is not where it should be.")
            (temp / MARKER).write_text(lock_hash(pkg), encoding="utf-8")
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            os.replace(temp, final)
        except LockedInstallError:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        except OSError as exc:
            shutil.rmtree(temp, ignore_errors=True)
            raise LockedInstallError(f"The app's program could not be installed: {exc.strerror or exc}.") from None
        log.info("installed %s %s from Iron Jarvis's lock", pkg.package, pkg.version)
        return final / pkg.entry
