"""Polling filesystem watcher — dependency-light + injectable.

The filesystem Sentinel notices new/changed files by POLLING on a cadence: it
stats the configured path/glob and diffs the result against the last seen state.
This deliberately avoids the ``watchdog`` package (or any new dependency) — only
stdlib ``glob``/``os.stat`` are used. The scan function is injectable so tests
are fully deterministic (a fake scanner returns whatever mtimes the test wants).

A "change" is a file that is NEW (a path not in the seen state) or MODIFIED (a
path whose mtime is strictly newer than the seen mtime). Deletions are not
surfaced as work; they simply drop out of the seen state.
"""

from __future__ import annotations

import glob as _glob
import os
from pathlib import Path
from typing import Callable

from ..core.fs_policy import fs_path_allowed, is_protected_path

#: A scanner maps a (path, glob) watch spec to ``{filepath: mtime}``. The default
#: stats the real filesystem; tests inject their own for determinism.
Scanner = Callable[[str, "str | None"], "dict[str, float]"]

#: Cap the number of files a single scan will track, so a Sentinel pointed at a
#: huge tree cannot bloat ``last_state_json`` (and the proposal rationale).
_MAX_FILES = 5000

#: Glob metacharacters that mark a path as already being a pattern.
_GLOB_CHARS = "*?["


def default_scanner(path: str, pattern: str | None = None) -> dict[str, float]:
    """Stat-based scan returning ``{filepath: mtime}`` for the watch spec.

    Resolution rules (dependency-light, stdlib only):
      * ``pattern`` given     → glob ``pattern`` under ``path`` (recursive ``**``
        supported), e.g. path=``~/notes`` pattern=``**/*.md``.
      * ``path`` is a glob    → expand it directly (recursive), e.g. ``*.log``.
      * ``path`` is a dir     → its immediate file children.
      * ``path`` is a file    → just that file (if it exists).

    Only regular files are returned. Unreadable entries are skipped (never raise).
    """
    if not path:
        return {}
    base = Path(path).expanduser()
    if pattern:
        matches = base.glob(pattern)
    elif any(ch in path for ch in _GLOB_CHARS):
        matches = (Path(p) for p in _glob.glob(os.path.expanduser(path), recursive=True))
    elif base.is_dir():
        matches = base.iterdir()
    elif base.is_file():
        matches = [base]
    else:
        matches = []

    out: dict[str, float] = {}
    for p in matches:
        try:
            if not p.is_file():
                continue
            sp = str(p)
            # Same fs_policy every other reader honors: never watch protected
            # (secret/key) paths or anything outside IRONJARVIS_FS_ALLOWLIST.
            if is_protected_path(sp) or not fs_path_allowed(sp):
                continue
            out[sp] = p.stat().st_mtime
        except OSError:
            continue
        if len(out) >= _MAX_FILES:
            break
    return out


def diff_state(
    previous: dict[str, float], current: dict[str, float]
) -> list[dict]:
    """Return the NEW/MODIFIED files in ``current`` versus ``previous``.

    Each item is ``{"path": str, "mtime": float, "change": "new"|"modified"}``.
    A file is *new* when its path is absent from ``previous``; *modified* when its
    mtime is strictly newer. Unchanged files and deletions yield nothing.
    """
    changed: list[dict] = []
    for fp, mtime in current.items():
        prev = previous.get(fp)
        if prev is None:
            changed.append({"path": fp, "mtime": mtime, "change": "new"})
        elif mtime > prev:
            changed.append({"path": fp, "mtime": mtime, "change": "modified"})
    return changed
