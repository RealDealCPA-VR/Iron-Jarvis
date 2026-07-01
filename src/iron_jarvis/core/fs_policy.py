"""Shared filesystem-access policy (security).

A single source of truth for *which local paths an agent tool may read*. The
daemon's HTTP file endpoints already consult :func:`fs_path_allowed`; the agent
tools (``read_document`` / ``extract_pdf`` / ``file_search``) must consult the
SAME policy, otherwise an agent can bypass ``IRONJARVIS_FS_ALLOWLIST`` and read
arbitrary host files (including the secrets/browser encryption keys).

Two layers:

* **Allowlist** (``IRONJARVIS_FS_ALLOWLIST``) — when set (a public/multi-user
  deployment), reads are confined to those roots. Unset (local single-user) →
  unrestricted, preserving the local UX.
* **Protected roots** — directories that are NEVER agent-readable on ANY
  deployment (the Fernet key dirs under ``.ironjarvis/secrets`` and
  ``.ironjarvis/browser``). The platform registers them at boot.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    """When ``IRONJARVIS_FS_ALLOWLIST`` is set, restrict reads to those roots.

    Unset (local) → unrestricted. This is the allowlist layer ONLY; callers
    should also reject :func:`is_protected_path` paths.
    """
    allow = os.environ.get("IRONJARVIS_FS_ALLOWLIST", "").strip()
    if not allow:
        return True
    try:
        target = _canonical(path)
    except Exception:
        return False
    for root in (r.strip() for r in allow.split(",") if r.strip()):
        try:
            if _within(target, _canonical(root)):
                return True
        except Exception:
            continue
    return False


def fs_read_ok(path: str | Path) -> tuple[bool, str]:
    """Combined read gate for agent file tools.

    Returns ``(ok, reason)``. A path is readable only if it is not a protected
    root and satisfies the allowlist. ``reason`` is a user-facing error when
    not ``ok``.
    """
    if is_protected_path(path):
        return False, "path is a protected secrets/key directory and is not readable"
    if not fs_path_allowed(path):
        return False, "path is outside IRONJARVIS_FS_ALLOWLIST"
    return True, ""
