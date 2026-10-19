"""Shared filesystem-access policy (security).

A single source of truth for *which local paths an agent tool may read*. The
daemon's HTTP file endpoints already consult :func:`fs_path_allowed`; the agent
tools (``read_document`` / ``extract_pdf`` / ``file_search``) must consult the
SAME policy, otherwise an agent can bypass ``IRONJARVIS_FS_ALLOWLIST`` and read
arbitrary host files (including the secrets/browser encryption keys).

Three layers:

* **Allowlist** (``IRONJARVIS_FS_ALLOWLIST``) — when set (a public/multi-user
  deployment), reads are confined to those roots. Unset (local single-user) →
  unrestricted, preserving the local UX.
* **The folders Jarvis may read** (v1.356.0, G81) — the ``readable_folders``
  SETTING (Settings › Permissions; ``Config.readable_folders``), read LIVE
  off the platform's config (bound by ``core.config.load_config`` through
  :func:`use_config`, held weakly). Empty (the default) = no limit, exactly
  the behaviour before it existed. When the user lists folders, reads are
  confined to them PLUS Jarvis's own working folders (its home — uploads,
  work folders, made files — and the chat files folder), because a limit
  that refused the user's own attachments would read as a broken app, not a
  safer one. When BOTH the variable and the setting are set, a path must
  satisfy BOTH: the setting narrows what an installation allows and can
  never widen it (a saved setting must not reopen a folder an operator's
  environment closed — including a setting saved BEFORE the variable was
  set). A refusal is a sentence, never the variable's name:
  :data:`READ_REFUSAL` when the setting refused (it says where to change
  it), :data:`READ_REFUSAL_INSTALL` when only the installation did.
* **Protected roots** — directories that are NEVER agent-readable on ANY
  deployment (the Fernet key dirs under ``.ironjarvis/secrets`` and
  ``.ironjarvis/browser``). The platform registers them at boot. No folder
  setting reopens them: they are checked first and stay refused inside a
  listed folder.
"""

from __future__ import annotations

import os
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Any

#: The refusal when the user's own folder setting refused a read (v1.356.0,
#: G81): what happened, and where to change it — never the environment
#: variable's name. The arrow is the app's own place glyph
#: (``failure_words.ACCOUNTS_PLACE`` = "Settings › Connections").
READ_REFUSAL = (
    "That folder isn't one Jarvis may read. Change the folders in Settings › Permissions."
)
#: The refusal when only the INSTALLATION's limit refused (the setting allows
#: it, or is empty): Settings cannot change that limit, so it is not offered.
READ_REFUSAL_INSTALL = "That folder isn't one Jarvis may read."
#: The longest folder list the setting keeps (a limit, not a crawl list).
MAX_READABLE_FOLDERS = 50

_ENV = "IRONJARVIS_FS_ALLOWLIST"

#: The live Config whose ``readable_folders`` narrow reads — held WEAKLY so a
#: test's platform that is gone stops speaking for the process. ``None`` (or a
#: dead reference) = no setting, i.e. no limit from this layer.
_CONFIG_REF: "weakref.ReferenceType[Any] | None" = None
#: (raw folders, home, chat files root) -> canonical roots. The canonical form
#: resolves paths and the Documents folder, so it is computed once per change
#: of the setting, never once per read check.
_ROOTS_CACHE: dict[tuple[Any, ...], list[Path]] = {}
_CACHE_LOCK = threading.Lock()

# Directories whose contents must never be returned to an agent, regardless of
# the allowlist (the encrypted-secret key material lives here). Populated by the
# platform via :func:`register_protected_root` at boot.
_PROTECTED_ROOTS: set[Path] = set()

#: Key files that are never agent-readable by NAME, as a belt-and-suspenders
#: layer independent of directory containment (covers .bak/.new/.tmp siblings).
_PROTECTED_NAME_PREFIXES = (".secrets.key", ".vault.key")


def _canonical(path: str | Path) -> Path:
    """Resolve a path to a canonical form, FIRST stripping Windows device /
    extended-length prefixes that otherwise bypass the protected-root check.

    ``\\\\?\\C:\\...`` resolves with an anchor of ``\\\\?\\C:\\`` (not ``C:\\``), so
    ``is_relative_to`` / string containment against a normal root silently returns
    False while ``open()`` still honors the path — a real protected-root bypass.
    Stripping ``\\\\?\\``, ``\\\\.\\`` and ``\\\\?\\UNC\\`` (and forward-slash
    variants) before ``resolve()`` makes the anchors line up again."""
    s = os.fspath(path)
    if os.name == "nt":
        t = s.replace("/", "\\")
        low = t.lower()
        if low.startswith("\\\\?\\unc\\"):
            t = "\\\\" + t[len("\\\\?\\UNC\\"):]
        elif low.startswith("\\\\?\\") or low.startswith("\\\\.\\"):
            t = t[4:]
        s = t
    return Path(s).resolve()


def _within(target: Path, root: Path) -> bool:
    """Containment of ``target`` within ``root`` — robust to path-spelling tricks.

    First a case-insensitive string-prefix check (fast path), then an INODE
    IDENTITY check: walk target's ancestors and compare ``(st_dev, st_ino)`` to
    ``root``. Two spellings of the same directory (drive-letter ``C:\\...`` vs a
    local admin share ``\\\\localhost\\C$\\...``, an 8.3 short name, or a symlink)
    have the same inode, so this catches containment even when the strings diverge.
    """
    t = os.path.normcase(str(target))
    r = os.path.normcase(str(root))
    if t == r or t.startswith(r + os.sep):
        return True
    # The string prefix only diverges from a drive-letter root for a Windows UNC /
    # admin-share spelling (\\host\C$\...) — resolve() normalizes everything else.
    # Restrict the (per-ancestor stat) identity walk to that case so normal reads
    # keep the cheap string check.
    if os.name != "nt" or not str(target).startswith("\\\\"):
        return False
    try:
        st = os.stat(root)
        root_id = (st.st_dev, st.st_ino)
    except OSError:
        return False  # root not statable → rely on the (failed) string check
    cur = target
    for _ in range(64):  # bounded ancestor walk
        try:
            s = os.stat(cur)
            if (s.st_dev, s.st_ino) == root_id:
                return True
        except OSError:
            pass
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return False


def register_protected_root(path: str | Path) -> None:
    """Mark *path* (and everything under it) as never agent-readable."""
    try:
        _PROTECTED_ROOTS.add(_canonical(path))
    except Exception:  # pragma: no cover - defensive
        pass


def protected_roots() -> list[Path]:
    """The registered protected roots, canonical.

    Exposed because the REPL worker enforces the same rule from INSIDE a child
    process (it cannot import this module — it is deliberately stdlib-only), so
    the parent has to hand it the roots. Policy stays here; only enforcement
    moves. Returning a copy keeps the registry itself append-only via
    :func:`register_protected_root`.
    """
    return sorted(_PROTECTED_ROOTS)


def use_config(config: Any) -> None:
    """Read the ``readable_folders`` setting LIVE from *config* (v1.356.0,
    G81). Called by ``core.config.load_config`` — the one place every
    platform's config is made — so the daemon, the CLI and every test
    platform bind the config they run on; the settings writer then changes
    that same object in place, and the next read check sees it. Held weakly:
    a config nobody holds any more stops limiting anything. ``None`` unbinds.
    """
    global _CONFIG_REF
    if config is None:
        _CONFIG_REF = None
        return
    try:
        _CONFIG_REF = weakref.ref(config)
    except TypeError:  # pragma: no cover - a config type with no weakref slot
        _CONFIG_REF = None


def _bound_config() -> Any:
    ref = _CONFIG_REF
    return ref() if ref is not None else None


def setting_folders() -> list[str]:
    """The folders the user listed in Settings › Permissions, as typed (blank
    entries dropped), or ``[]`` when there is no setting (no limit)."""
    cfg = _bound_config()
    raw = getattr(cfg, "readable_folders", None) if cfg is not None else None
    if not isinstance(raw, (list, tuple)):
        return []
    return [s.strip() for s in raw if isinstance(s, str) and s.strip()][:MAX_READABLE_FOLDERS]


def _env_roots() -> list[Path] | None:
    """Canonical ``IRONJARVIS_FS_ALLOWLIST`` roots; ``None`` = the variable is
    not set (no limit from it). A set variable whose folders all fail to
    resolve is ``[]`` — limited to nothing, as it always was."""
    allow = os.environ.get(_ENV, "").strip()
    if not allow:
        return None
    roots: list[Path] = []
    for raw in (r.strip() for r in allow.split(",") if r.strip()):
        try:
            roots.append(_canonical(raw))
        except Exception:  # pragma: no cover - defensive
            continue
    return roots


def _app_folders(cfg: Any) -> list[str]:
    """Jarvis's own working folders, readable whenever the setting is on: its
    home (uploads, work folders, made files — the protected roots inside it
    stay refused) and the chat files folder."""
    out: list[str] = []
    try:
        home = getattr(cfg, "home", None)
        if home:
            out.append(os.fspath(home))
    except Exception:  # noqa: BLE001 - defensive
        pass
    try:
        out.append(os.fspath(cfg.chat_files_dir))
    except Exception:  # noqa: BLE001 - no Documents folder resolvable
        pass
    return out


def _setting_roots() -> list[Path] | None:
    """Canonical roots the setting allows (the user's folders + Jarvis's own),
    or ``None`` when the setting is empty (no limit from it)."""
    folders = setting_folders()
    if not folders:
        return None
    cfg = _bound_config()
    key = (
        tuple(folders),
        os.fspath(getattr(cfg, "home", "") or ""),
        str(getattr(cfg, "chat_files_root", "") or ""),
    )
    with _CACHE_LOCK:
        hit = _ROOTS_CACHE.get(key)
    if hit is not None:
        return list(hit)
    roots: list[Path] = []
    for raw in [*folders, *_app_folders(cfg)]:
        try:
            p = _canonical(raw)
        except Exception:  # noqa: BLE001 - an unparseable entry limits to nothing
            continue
        if p not in roots:
            roots.append(p)
    with _CACHE_LOCK:
        if len(_ROOTS_CACHE) > 16:
            _ROOTS_CACHE.clear()
        _ROOTS_CACHE[key] = list(roots)
    return roots


def _within_any(target: Path, roots: list[Path]) -> bool:
    return any(_within(target, root) for root in roots)


#: What :func:`allowlist_roots` answers when the limits are on but no folder
#: satisfies both (the installation's folders and the setting's share none):
#: one folder that holds nothing, so a child enforcing "only inside these"
#: (the REPL) reads nothing too — an empty list would read as "no limit".
NOTHING_READABLE = Path(tempfile.gettempdir()) / "ironjarvis-no-readable-folders"


def allowlist_roots() -> list[Path]:
    """The canonical roots reads are confined to; empty means unrestricted.

    The installation's ``IRONJARVIS_FS_ALLOWLIST`` roots, the setting's roots
    (the user's folders + Jarvis's own working folders), or — when both are
    set — the folders that satisfy both (a root of one that sits inside a
    root of the other). The REPL hands this list to its child and the access
    summary shows it, so it says exactly what :func:`fs_path_allowed`
    enforces."""
    env = _env_roots()
    setting = _setting_roots()
    if env is None and setting is None:
        return []
    if setting is None:
        return list(env or [])
    if env is None:
        return list(setting)
    both: list[Path] = []
    for p in [*(s for s in setting if _within_any(s, env)), *(e for e in env if _within_any(e, setting))]:
        if p not in both:
            both.append(p)
    return both or [NOTHING_READABLE]


def _read_refusal(path: str | Path) -> str:
    """``""`` when the folder layers allow *path*, else the sentence that says
    which limit refused it. The protected layer is NOT checked here."""
    env = _env_roots()
    setting = _setting_roots()
    if env is None and setting is None:
        return ""
    try:
        target = _canonical(path)
    except Exception:
        return READ_REFUSAL_INSTALL if env is not None else READ_REFUSAL
    # The installation's limit first: when IT refuses, Settings cannot help
    # (a listed folder must sit inside it), so the sentence does not send the
    # user there.
    if env is not None and not _within_any(target, env):
        return READ_REFUSAL_INSTALL
    if setting is not None and not _within_any(target, setting):
        return READ_REFUSAL
    return ""


def is_protected_path(path: str | Path) -> bool:
    """True if *path* resolves inside any registered protected root (or is a key
    file by name), robust to Windows ``\\\\?\\`` device-prefix and case tricks."""
    try:
        target = _canonical(path)
    except Exception:
        return True  # un-resolvable → treat as protected (fail-closed)
    if any(target.name.startswith(p) for p in _PROTECTED_NAME_PREFIXES):
        return True
    if not _PROTECTED_ROOTS:
        return False
    return any(_within(target, root) for root in _PROTECTED_ROOTS)


def fs_path_allowed(path: str | Path) -> bool:
    """Do the folder limits allow reading *path*?

    ``IRONJARVIS_FS_ALLOWLIST`` (an installation's limit) and the
    ``readable_folders`` setting (the user's, v1.356.0 G81) each confine
    reads when set; both set = both must allow it. Neither set (the local
    default) → unrestricted. This is the folder layer ONLY; callers should
    also reject :func:`is_protected_path` paths.
    """
    return _read_refusal(path) == ""


def read_root_problem(root: str | Path, *, must_exist: bool = True) -> str | None:
    """Why *root* cannot be one of the folders Jarvis may read, or None
    (v1.356.0, G81) — the check the settings writer runs on every folder the
    user lists, before it is saved.

    The first three of :func:`root_problem`'s answers, asked for READING:
    absolute, a folder on this machine, not a protected folder and inside
    the installation's own limit when it has one. It never consults the
    setting it is validating (changing the folders must not be refused by
    the old ones) and never probes writability (a read-only share is a
    perfectly good folder to read). ``must_exist=False`` (an Undo putting a
    saved limit back) skips only "not a folder on this machine": a folder
    that went missing reads nothing, and refusing the restore would leave
    reads unlimited. BLOCKING (it stats the folder): call it off the event
    loop."""
    return root_problem(root, for_reading=True, _must_exist=must_exist)


def dir_writable(path: str | Path) -> bool:
    """Can this app create a file in *path* RIGHT NOW? (v1.228.0, audit T3)

    Probed, never inferred: ``os.access(W_OK)`` lies on Windows (it ignores
    ACLs), so the one honest answer is to create a file and delete it. ONE
    ``os.open(O_CREAT|O_EXCL)`` of a hidden ``.ij-probe-*`` name, unlinked
    right after. Any ``OSError`` (EACCES/EPERM, a read-only mount, a
    vanished folder) is "not writable".

    NOT ``tempfile.NamedTemporaryFile``/``mkstemp``: on Windows they catch
    ``PermissionError`` and RETRY up to ``TMP_MAX`` (10,000) times as long as
    ``os.access(dir, W_OK)`` says writable — which, per the lie above, it
    always does — so on a real RX-only folder the "probe" spun for minutes
    (measured on this task's first cut).

    BLOCKING: this touches the filesystem — a network share or an unhydrated
    OneDrive path stalls for as long as the OS takes — so every caller on the
    event loop goes through ``asyncio.to_thread`` (the v1.153.1 rule).
    """
    import secrets

    probe = os.path.join(os.fspath(path), f".ij-probe-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return False
    try:
        os.close(fd)
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass
    return True


def root_problem(
    root: str | Path, *, for_reading: bool = False, _must_exist: bool = True
) -> str | None:
    """Why a folder cannot be worked in DIRECTLY, or None when it can — THE
    one definition behind every "work in the user's own folder" door
    (v1.231.0, audit AE1/T4; it used to live in ``routes/projects`` as
    ``_root_problem`` while ``usable_workspace_root`` re-derived the same
    predicate as a bool, and ``Orchestrator.create_session`` — the seam every
    schedule, reflex, goal and phone door ends in — asked neither).

    Four honest answers, in the order a user would fix them: not absolute,
    not a folder on this machine, a folder the app's file policy refuses (a
    protected root such as the secrets/undo stores, or a path outside the
    ``IRONJARVIS_FS_ALLOWLIST``), or a folder this app cannot save a file in
    (v1.228.0, audit T3 — ``C:\\Users``, ``C:\\``, an RX-only share).
    BLOCKING (the writability probe creates a file): call it off the event
    loop — ``asyncio.to_thread`` at every route/lane seam.

    ``for_reading`` (v1.356.0, G81 — :func:`read_root_problem`): the same
    door for a folder the user lists in "Folders Jarvis may read": only the
    PROTECTED and the INSTALLATION's limits apply (never the setting being
    changed), and there is no writability probe.
    """
    p = Path(root)
    if not p.is_absolute():
        return "folder must be an absolute path"
    if not p.is_dir() and (_must_exist or not for_reading):
        return f"folder does not exist on this machine: {root}"
    if for_reading:
        if is_protected_path(str(p)):
            return f"that folder holds Iron Jarvis's own keys or records, which are never read: {root}"
        env = _env_roots()
        if env is not None:
            try:
                inside = _within_any(_canonical(p), env)
            except Exception:  # noqa: BLE001 - unresolvable = not inside
                inside = False
            if not inside:
                return f"this installation does not allow reading that folder: {root}"
        return None
    if not fs_path_allowed(str(p)) or is_protected_path(str(p)):
        return (
            f"folder is protected or outside the app's allowed file roots: {root} "
            "— pick a folder this app may read and write in"
        )
    if not dir_writable(p):
        return (
            f"folder is not writable by this app: {root} "
            "— pick a folder you can save files in"
        )
    return None


def usable_workspace_root(path: str | Path) -> bool:
    """May a session/turn run DIRECTLY in this folder? (v1.189.0)

    The one predicate behind every "work in the user's own folder" door —
    chat's workspace pick, ``POST /sessions``' ``workspace_root``, the dynamic
    spawn's, a project root. Absolute + an existing directory +
    allowlist-clean + not a protected root + WRITABLE (v1.228.0: a live chat
    bound to ``C:\\Users`` passed every check here and then failed its
    ``write_document`` naming a hidden ``.tmp`` file — a folder the app cannot
    save in is refused at the door, with the reason). One definition, because
    the measured failure mode of this area is two doors answering differently
    and a job losing its folder on the way through the second one.

    BLOCKING (the writability probe creates a file): call it off the event
    loop — ``asyncio.to_thread`` at every route/lane seam.
    """
    try:
        return root_problem(path) is None
    except Exception:  # noqa: BLE001 — an unparseable path is not a workspace
        return False


def fs_read_ok(path: str | Path) -> tuple[bool, str]:
    """Combined read gate for agent file tools.

    Returns ``(ok, reason)``. A path is readable only if it is not a protected
    root and satisfies the folder limits. ``reason`` is a user-facing error
    when not ``ok``: a folder-limit refusal is ONE plain sentence that says
    where to change it when the user's setting refused (:data:`READ_REFUSAL`)
    — it never names the environment variable (v1.356.0, G81).
    """
    if is_protected_path(path):
        return False, "path is a protected secrets/key directory and is not readable"
    why = _read_refusal(path)
    if why:
        return False, why
    return True, ""
