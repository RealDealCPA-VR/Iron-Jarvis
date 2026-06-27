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


def register_protected_root(path: str | Path) -> None:
    """Mark *path* (and everything under it) as never agent-readable."""
    try:
        _PROTECTED_ROOTS.add(Path(path).resolve())
    except Exception:  # pragma: no cover - defensive
        pass


def is_protected_path(path: str | Path) -> bool:
    """True if *path* resolves inside any registered protected root."""
    if not _PROTECTED_ROOTS:
        return False
    try:
        target = Path(path).resolve()
    except Exception:
        return True  # un-resolvable → treat as protected (fail-closed)
    for root in _PROTECTED_ROOTS:
        if target == root or target.is_relative_to(root):
            return True
    return False


def fs_path_allowed(path: str | Path) -> bool:
    """When ``IRONJARVIS_FS_ALLOWLIST`` is set, restrict reads to those roots.

    Unset (local) → unrestricted. This is the allowlist layer ONLY; callers
    should also reject :func:`is_protected_path` paths.
    """
    allow = os.environ.get("IRONJARVIS_FS_ALLOWLIST", "").strip()
    if not allow:
        return True
    try:
        target = Path(path).resolve()
    except Exception:
        return False
    for root in (r.strip() for r in allow.split(",") if r.strip()):
        try:
            rp = Path(root).resolve()
            if target == rp or target.is_relative_to(rp):
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
