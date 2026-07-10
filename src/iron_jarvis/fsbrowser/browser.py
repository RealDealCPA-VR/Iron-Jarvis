"""On-demand directory browser (dashboard directory-tree panel).

Powers a directory-tree panel that lets the user browse the computer's folders
and pick a project directory to become a terminal's cwd. This is a *tree-on-
demand* lister: each call returns the contents of exactly one directory (a
single level), never a recursive walk, so a click on a huge or system folder
can never hang the UI.

Deliberately independent of ``filesearch.service`` (no shared imports) so the
panel keeps working regardless of the search subsystem. It mirrors that module's
``list_drives`` behaviour for roots but owns its own copy.

Safety: every returned path is absolute; unreadable children are skipped rather
than crashing a listing; a missing path raises ``FileNotFoundError``. The daemon
endpoint that exposes this should additionally apply the existing
``_fs_path_allowed`` guard (``IRONJARVIS_FS_ALLOWLIST``) for public deployments.
"""

from __future__ import annotations

import os
import stat
import string
from pathlib import Path
from typing import TypedDict

#: Hard cap on entries returned for a single directory, so listing a folder
#: with tens of thousands of children stays responsive.
MAX_ENTRIES = 2000

#: Marker file/dir -> project type, checked in this order (first match wins).
_PROJECT_MARKERS: tuple[tuple[str, str], ...] = (
    (".git", "git"),
    ("pyproject.toml", "python"),
    ("package.json", "node"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
)


class FsEntry(TypedDict):
    """A single directory child as surfaced to the dashboard panel."""

    name: str
    path: str  # absolute
    is_dir: bool
    is_project: str | None  # project type when the child is a project root
    size: int | None  # byte size for files; None for directories


def detect_project(path: Path) -> str | None:
    """Return a project type if ``path`` looks like a project root, else None.

    Recognises ``.git`` -> ``"git"``, ``pyproject.toml`` -> ``"python"``,
    ``package.json`` -> ``"node"``, ``Cargo.toml`` -> ``"rust"``,
    ``go.mod`` -> ``"go"``. Checked in that order; the first marker present wins.
    """
    p = Path(path)
    for marker, kind in _PROJECT_MARKERS:
        try:
            if (p / marker).exists():
                return kind
        except OSError:
            continue
    return None


def home() -> str:
    """Absolute path to the current user's home directory."""
    return str(Path.home())


def drives() -> list[dict]:
    """Available roots to seed the tree, as ``[{"path", "label"}, ...]``.

    On Windows: every existing drive letter ``C:\\`` .. ``Z:\\`` plus the user
    home. On POSIX: the filesystem root ``/`` plus the user home. Only roots
    that actually exist are included, so the current drive is always present.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _add(path: str, label: str) -> None:
        try:
            if not Path(path).exists():
                return
        except OSError:
            return
        key = str(Path(path))
        if key in seen:
            return
        seen.add(key)
        out.append({"path": path, "label": label})

    if os.name == "nt":
        for letter in string.ascii_uppercase[2:]:  # C .. Z
            _add(f"{letter}:\\", f"{letter}:")
        _add(home(), "Home")
    else:
        _add("/", "/")
        _add(home(), "Home")
    return out


def _is_hidden(entry: os.DirEntry) -> bool:
    """True for dot-prefixed names and (on Windows) FILE_ATTRIBUTE_HIDDEN."""
    if entry.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes
        except (OSError, AttributeError):
            return False
        if attrs & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0):
            return True
    return False


def list_dir(
    path: str | Path,
    *,
    show_hidden: bool = False,
    dirs_only: bool = False,
) -> dict:
    """List the immediate children of a single directory.

    Returns ``{"path": <abs>, "parent": <abs parent or None>, "entries": [...]}``
    where each entry is an :class:`FsEntry`. Directories sort before files, then
    by case-insensitive name. Hidden children (dot-prefixed or Windows-hidden)
    are skipped unless ``show_hidden``. With ``dirs_only`` files are omitted.

    Raises ``FileNotFoundError`` if ``path`` does not exist and
    ``NotADirectoryError`` if it exists but is not a directory. Children that
    cannot be stat'd (permissions, broken links) are skipped, never fatal; if
    the directory itself is unreadable an empty ``entries`` list is returned.
    """
    p = Path(path)
    try:
        p = p.resolve()
    except OSError as exc:  # pragma: no cover - exotic path errors
        raise FileNotFoundError(str(path)) from exc

    if not p.exists():
        raise FileNotFoundError(str(path))
    if not p.is_dir():
        raise NotADirectoryError(str(path))

    parent = p.parent
    parent_str = str(parent) if parent != p else None

    entries: list[FsEntry] = []
    try:
        scanner = os.scandir(p)
    except (PermissionError, OSError):
        return {"path": str(p), "parent": parent_str, "entries": entries, "truncated": False}

    with scanner:
        for entry in scanner:
            if len(entries) >= MAX_ENTRIES:
                break
            if not show_hidden and _is_hidden(entry):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if dirs_only and not is_dir:
                continue

            is_project = detect_project(Path(entry.path)) if is_dir else None
            size: int | None = None
            if not is_dir:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = None

            entries.append(
                FsEntry(
                    name=entry.name,
                    path=entry.path,
                    is_dir=is_dir,
                    is_project=is_project,
                    size=size,
                )
            )

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    # Honest cap: past MAX_ENTRIES the listing is partial (raw scandir order) —
    # say so instead of silently pretending the folder ends here.
    return {
        "path": str(p),
        "parent": parent_str,
        "entries": entries,
        "truncated": len(entries) >= MAX_ENTRIES,
    }


#: Directories skipped by the recursive file walk — heavy/noise trees that would
#: bury the files a user actually created with dependency/VCS churn.
_WALK_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv", ".next", "dist",
    "build", ".turbo", ".cache", ".idea", ".vscode", "target", ".pytest_cache",
    ".mypy_cache", ".gradle", "vendor", ".terraform",
})


def list_files_recursive(
    path: str, *, depth: int = 4, limit: int = 600, show_hidden: bool = False
) -> dict:
    """Every FILE under ``path`` (bounded), NEWEST FIRST — so files a CLI just
    created surface at the top. Walks at most ``depth`` levels, returns at most
    ``limit`` files, and skips heavy/noise dirs (node_modules/.git/…). Each file:
    ``{name, path, rel, size, mtime}``. Unreadable children are skipped."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"no such directory: {path}")
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {path}")
    depth = max(1, min(int(depth), 8))
    limit = max(1, min(int(limit), MAX_ENTRIES))

    files: list[dict] = []
    root_str = str(root)

    def _walk(d: str, level: int) -> None:
        if level > depth or len(files) >= limit:
            return
        try:
            it = list(os.scandir(d))
        except OSError:
            return
        for e in it:
            if len(files) >= limit:
                return
            name = e.name
            if not show_hidden and name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if name in _WALK_SKIP_DIRS:
                        continue
                    _walk(e.path, level + 1)
                elif e.is_file(follow_symlinks=False):
                    st = e.stat()
                    files.append(
                        {
                            "name": name,
                            "path": e.path,
                            "rel": os.path.relpath(e.path, root_str).replace("\\", "/"),
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                        }
                    )
            except OSError:
                continue

    _walk(root_str, 1)
    files.sort(key=lambda f: f["mtime"], reverse=True)  # newest created first
    truncated = len(files) >= limit
    return {"root": root_str, "files": files[:limit], "count": len(files), "truncated": truncated}
