"""Which folders File Search looks in (v1.445.0).

Before this, an empty ``search_roots`` made File Search, the agents'
``file_search`` tool AND "By meaning" look in ``config.project_root`` — which in
the packaged app is the app's own data folder (``%APPDATA%\\Iron Jarvis``:
Electron's caches, logs, the token file). A newcomer searching for their own
document got "No matches", and every chat turn's memory recall indexed log
lines.

THE RULE, read live on every search (a change on the File Search page or in
chat applies to the next search, no restart):

* **Name and content search** look in the user's own places:
  the chat files folder (``config.chat_files_dir`` — Documents\\Iron Jarvis
  unless the user moved it), the folder of every ACTIVE project, and every
  folder the user added (``search_roots``, which is "extra folders", as its
  label always said — it no longer REPLACES the defaults).
* **"By meaning"** (``search_semantic``) looks ONLY in folders the user added.
  It is not a per-query walk: it reads, extracts and embeds every file once
  (up to 20,000 files / 50,000 chunks) and the memory fabric asks it on EVERY
  chat turn and agent run, quoting short passages into the prompt. Pointing
  that at every project and the chat files folder by default would quietly
  read a firm's whole client archive into each turn's memory; the user opts a
  folder in by adding it.
* **Never** the app's own home/state folder, nor ``project_root`` (it is the
  app's data folder in a packaged run): neither is a default any more, and a
  folder inside the home is skipped even when added.
* Every folder must exist and pass :func:`fs_policy.fs_read_ok` (protected
  roots, the installation allowlist and Settings › Permissions' "Folders Jarvis
  may read"). A folder inside another kept folder is dropped, so nothing is
  walked twice.

Documents itself is NOT a default: a content search reads every file it walks
(PDF/Word/Excel extraction included), up to 20,000 files per query, and a
CPA's Documents folder is exactly where that takes minutes. The page offers it
as one press ("Also search Documents"), which adds it to ``search_roots``.

Cheap by design: config values are read live; the projects table is read at
most every :data:`CACHE_S` seconds (and whenever the config values change).
BLOCKING (stats + one small SQLite read): call it where the search runs, which
is always off the event loop (the route is a sync def; the tool uses
``asyncio.to_thread``).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Seconds the projects part of the answer is reused before it is read again.
CACHE_S = 5.0

#: The kinds a :class:`SearchFolder` can be (the page names them in words).
KIND_CHAT_FILES = "chat_files"
KIND_PROJECT = "project"
KIND_ADDED = "added"


@dataclass(frozen=True)
class SearchFolder:
    path: Path
    kind: str
    label: str


def _within(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except (TypeError, ValueError):
        return False


def _resolved(raw: Any) -> Path | None:
    if not isinstance(raw, (str, Path)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _usable(path: Path | None, home: Path | None) -> bool:
    """Exists, readable by the folder rules, and not the app's own state."""
    if path is None:
        return False
    try:
        if not path.exists():
            return False
    except OSError:
        return False
    if home is not None and _within(path, home):
        return False
    try:
        from ..core.fs_policy import fs_read_ok

        ok, _why = fs_read_ok(path)
    except Exception:  # noqa: BLE001 — a policy fault never widens a search
        return False
    return ok


def _home_of(config: Any) -> Path | None:
    try:
        return Path(config.home).resolve()
    except Exception:  # noqa: BLE001
        return None


def added_folders(config: Any) -> list[str]:
    """The ``search_roots`` setting as typed: strings only, blanks dropped."""
    raw = getattr(config, "search_roots", None) or []
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(r).strip() for r in raw if isinstance(r, (str, Path)) and str(r).strip()]


def _chat_files(config: Any) -> Path | None:
    try:
        return _resolved(config.chat_files_dir)
    except Exception:  # noqa: BLE001 — no Documents answer: no chat files default
        return None


def _project_folders(engine: Any) -> list[tuple[Path, str]]:
    if engine is None:
        return []
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import Project

        with session_scope(engine) as db:
            rows = [
                (p.root, p.name)
                for p in db.exec(select(Project))
                if (p.root or "").strip() and (p.status or "active") != "archived"
            ]
    except Exception:  # noqa: BLE001 — no table yet / a locked DB: no projects
        return []
    out: list[tuple[Path, str]] = []
    for root, name in rows:
        path = _resolved(root)
        if path is not None:
            out.append((path, name or path.name))
    return out


def _drop_nested(folders: list[SearchFolder]) -> list[SearchFolder]:
    """Keep the outermost of nested folders (first one wins on a tie)."""
    kept: list[SearchFolder] = []
    for f in folders:
        if any(_within(f.path, k.path) for k in kept):
            continue
        kept = [k for k in kept if not _within(k.path, f.path)]
        kept.append(f)
    # Keep the order the folders were offered in.
    order = {id(f): i for i, f in enumerate(folders)}
    return sorted(kept, key=lambda f: order[id(f)])


def search_folders(config: Any, engine: Any = None) -> list[SearchFolder]:
    """The folders name and content search look in (see the module doc)."""
    home = _home_of(config)
    offered: list[SearchFolder] = []
    chat = _chat_files(config)
    if _usable(chat, home):
        assert chat is not None
        offered.append(SearchFolder(chat, KIND_CHAT_FILES, "Iron Jarvis chat files"))
    for path, name in _project_folders(engine):
        if _usable(path, home):
            offered.append(SearchFolder(path, KIND_PROJECT, name))
    for raw in added_folders(config):
        path = _resolved(raw)
        if _usable(path, home):
            assert path is not None
            offered.append(SearchFolder(path, KIND_ADDED, path.name or str(path)))
    return _drop_nested(offered)


def meaning_folders(config: Any) -> list[Path]:
    """The folders "By meaning" indexes: the ones the user ADDED, nothing else."""
    home = _home_of(config)
    offered: list[SearchFolder] = []
    for raw in added_folders(config):
        path = _resolved(raw)
        if _usable(path, home):
            assert path is not None
            offered.append(SearchFolder(path, KIND_ADDED, path.name))
    return [f.path for f in _drop_nested(offered)]


class FolderPlan:
    """Live, cached providers for :class:`FileSearchService` (platform.py).

    ``plan.search()`` / ``plan.meaning()`` are the two callables the service
    takes. The answer is recomputed when the config values it reads change
    (so a saved setting applies to the next search) and at most every
    :data:`CACHE_S` seconds otherwise (a project added, archived or moved).
    """

    def __init__(self, config: Any, engine: Any = None, *, clock=time.monotonic) -> None:
        self.config = config
        self.engine = engine
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[Any, float, list[Path]]] = {}

    def _key(self) -> Any:
        cfg = self.config
        return (
            tuple(added_folders(cfg)),
            str(getattr(cfg, "chat_files_root", "") or ""),
            str(getattr(cfg, "home", "") or ""),
        )

    def _cached(self, which: str, build) -> list[Path]:
        key = self._key()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(which)
            if hit is not None and hit[0] == key and now - hit[1] < CACHE_S:
                return list(hit[2])
        value = build()
        with self._lock:
            self._cache[which] = (key, now, value)
        return list(value)

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()

    def search(self) -> list[Path]:
        return self._cached(
            "search", lambda: [f.path for f in search_folders(self.config, self.engine)]
        )

    def meaning(self) -> list[Path]:
        return self._cached("meaning", lambda: meaning_folders(self.config))
