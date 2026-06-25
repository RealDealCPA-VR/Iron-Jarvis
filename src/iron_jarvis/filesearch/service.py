"""Cross-root file search service (§18 extension, §22 retrieval).

``FileSearchService`` walks a set of *configured roots* and answers three kinds
of query:

* ``search_name``    — glob / substring match on file paths.
* ``search_content`` — regex match on file contents, reported as path + line.
* ``search_semantic``— cosine similarity over embedded file chunks (only when an
  embedder is injected; otherwise disabled and returns an empty list).

Hard guarantees:

* **Never escapes the roots.** Every result is verified to resolve inside one of
  the configured roots, so a symlink (or a crafted path) cannot leak files from
  elsewhere on disk.
* **Respects ignore patterns.** Directories named in ``ignore`` (``.git``,
  ``node_modules`` …) are pruned during the walk.
* **Skips unreadable / binary / oversized files gracefully** — they are ignored,
  never crash a search.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

import numpy as np

#: Directory names pruned from every walk by default.
DEFAULT_IGNORE: frozenset[str] = frozenset(
    {".git", "node_modules", ".venv", ".ironjarvis", "__pycache__", ".next", "dist", "build"}
)

#: Files larger than this are treated as non-text and skipped (1 MiB).
MAX_FILE_BYTES = 1_000_000

#: Lines per chunk when embedding files for semantic search.
_CHUNK_LINES = 40

#: Default cap on the number of files visited per search (keeps a walk of a huge
#: drive like ``C:\`` responsive rather than open-ended).
DEFAULT_MAX_WALK = 20_000


def list_drives() -> list[dict]:
    """Enumerate the local roots a user may target with a search.

    On Windows: every existing drive letter ``C:\\`` .. ``Z:\\`` (discovered via
    ``psutil.disk_partitions`` when available, then probed directly as a
    fallback) plus the user's home directory. On POSIX: the filesystem root and
    home. Returns ``[{"path", "label"}, ...]`` and only ever includes roots that
    actually exist, so the current drive is always present.
    """
    drives: list[dict] = []
    seen: set[str] = set()

    def _add(path: str, label: str) -> None:
        try:
            p = Path(path)
            exists = p.exists()
        except OSError:
            return
        if not exists:
            return
        key = str(p)
        if key in seen:
            return
        seen.add(key)
        drives.append({"path": path, "label": label})

    if os.name == "nt":
        try:
            import psutil

            for part in psutil.disk_partitions(all=False):
                mp = part.mountpoint  # e.g. "C:\\"
                _add(mp, mp.rstrip("\\/") or mp)
        except Exception:  # noqa: BLE001 — psutil missing/refusing must not crash
            pass
        # Probe drive letters directly as a fallback / for completeness.
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            _add(f"{letter}:\\", f"{letter}:")
        _add(str(Path.home()), "Home")
    else:
        _add("/", "/")
        _add(str(Path.home()), "Home")
    return drives


class FileSearchService:
    """Search by name, content, or semantics across configured roots."""

    def __init__(
        self,
        roots: list[Path],
        embedder=None,
        ignore: set[str] | None = None,
    ) -> None:
        # Resolve + de-duplicate roots; keep only the existing ones.
        seen: list[Path] = []
        for r in roots:
            rp = Path(r).resolve()
            if rp not in seen:
                seen.append(rp)
        self.roots: list[Path] = seen
        self.embedder = embedder
        self.ignore: set[str] = set(ignore) if ignore is not None else set(DEFAULT_IGNORE)
        self._indexed: list[Path] = []  # cached text-file paths after index()
        self._chunk_cache: list[tuple[Path, int, str, np.ndarray]] | None = None

    # -- root resolution ----------------------------------------------------

    def _effective_roots(self, roots: list[Path] | None) -> list[Path]:
        """Resolve+de-dupe a per-call ``roots`` override, else the configured roots."""
        if roots is None:
            return self.roots
        seen: list[Path] = []
        for r in roots:
            rp = Path(r).resolve()
            if rp not in seen:
                seen.append(rp)
        return seen

    # -- root containment ---------------------------------------------------

    def _root_for(
        self, path: Path, roots: list[Path] | None = None
    ) -> Path | None:
        """Return the root (configured, or the override) that contains ``path``."""
        candidate_roots = self.roots if roots is None else roots
        try:
            rp = path.resolve()
        except OSError:
            return None
        for root in candidate_roots:
            if rp == root or rp.is_relative_to(root):
                return root
        return None

    # -- walking ------------------------------------------------------------

    def _iter_files(
        self,
        roots: list[Path] | None = None,
        max_walk: int | None = None,
    ):
        """Yield files under ``roots`` (or the configured roots), pruning ignores.

        Stops after ``max_walk`` files have been yielded (when set), so a walk of
        a huge drive stays bounded.
        """
        walk_roots = self.roots if roots is None else roots
        count = 0
        for root in walk_roots:
            if root.is_file():
                yield root
                count += 1
                if max_walk is not None and count >= max_walk:
                    return
                continue
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                # Prune ignored dirs in-place so os.walk does not descend.
                dirnames[:] = [d for d in dirnames if d not in self.ignore]
                for fn in filenames:
                    yield Path(dirpath) / fn
                    count += 1
                    if max_walk is not None and count >= max_walk:
                        return

    def _candidate_files(
        self,
        roots: list[Path] | None = None,
        max_walk: int | None = None,
    ):
        """Files to scan: the cached index (only for configured roots) else a walk."""
        if roots is None and self._indexed:
            indexed = list(self._indexed)
            return indexed if max_walk is None else indexed[:max_walk]
        return self._iter_files(roots, max_walk)

    # -- reading ------------------------------------------------------------

    def _read_text(
        self, path: Path, roots: list[Path] | None = None
    ) -> str | None:
        """Return decoded text, or None if outside roots / oversized / binary / unreadable."""
        if self._root_for(path, roots) is None:
            return None
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return None
        except OSError:
            return None
        try:
            data = path.read_bytes()
        except (OSError, PermissionError, ValueError):
            return None
        if b"\x00" in data:  # cheap binary sniff
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    # -- helpers ------------------------------------------------------------

    def _rel(self, path: Path, root: Path) -> str:
        try:
            return str(path.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            return path.name

    @staticmethod
    def _matches_globs(name: str, rel: str, globs: list[str]) -> bool:
        return any(
            fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g) for g in globs
        )

    # -- name search --------------------------------------------------------

    def search_name(
        self,
        pattern: str,
        limit: int = 50,
        roots: list[Path] | None = None,
        max_walk: int = DEFAULT_MAX_WALK,
    ) -> list[dict]:
        """Glob/substring match on file paths. Returns ``{path, root}`` dicts."""
        eff_roots = self._effective_roots(roots)
        pat_lower = pattern.lower()
        results: list[dict] = []
        for path in self._iter_files(eff_roots, max_walk):
            root = self._root_for(path, eff_roots)
            if root is None:
                continue
            name = path.name
            rel = self._rel(path, root)
            if (
                fnmatch.fnmatch(name, pattern)
                or fnmatch.fnmatch(rel, pattern)
                or pat_lower in rel.lower()
            ):
                results.append({"path": str(path), "root": str(root)})
                if len(results) >= limit:
                    break
        return results

    # -- content search -----------------------------------------------------

    def search_content(
        self,
        regex: str,
        limit: int = 50,
        globs: list[str] | None = None,
        roots: list[Path] | None = None,
        max_walk: int = DEFAULT_MAX_WALK,
    ) -> list[dict]:
        """Regex-search file contents. Returns ``{path, line, text}`` dicts."""
        try:
            rx = re.compile(regex)
        except re.error:
            return []
        eff_roots = self._effective_roots(roots)
        results: list[dict] = []
        for path in self._candidate_files(roots, max_walk):
            root = self._root_for(path, eff_roots)
            if root is None:
                continue
            if globs and not self._matches_globs(path.name, self._rel(path, root), globs):
                continue
            text = self._read_text(path, eff_roots)
            if text is None:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    results.append({"path": str(path), "line": i, "text": line.strip()})
                    if len(results) >= limit:
                        return results
        return results

    # -- index --------------------------------------------------------------

    def index(self) -> int:
        """Walk roots and cache the set of readable text files. Returns the count."""
        paths: list[Path] = []
        for path in self._iter_files():
            if self._read_text(path) is not None:
                paths.append(path)
        self._indexed = paths
        self._chunk_cache = None  # invalidate semantic cache
        return len(paths)

    # -- semantic search ----------------------------------------------------

    def _chunks(self, text: str):
        lines = text.splitlines()
        for i in range(0, len(lines), _CHUNK_LINES):
            block = lines[i : i + _CHUNK_LINES]
            joined = "\n".join(block).strip()
            if joined:
                yield i + 1, joined

    def _build_chunk_cache(self) -> None:
        if not self._indexed:
            self.index()
        cache: list[tuple[Path, int, str, np.ndarray]] = []
        for path in self._indexed:
            text = self._read_text(path)
            if not text:
                continue
            for start, chunk in self._chunks(text):
                vec = np.asarray(self.embedder.embed(chunk), dtype=np.float64)
                cache.append((path, start, chunk, vec))
        self._chunk_cache = cache

    def search_semantic(self, query: str, k: int = 5) -> list[dict]:
        """Cosine-similarity search over embedded file chunks (needs an embedder)."""
        if self.embedder is None:
            return []
        if self._chunk_cache is None:
            self._build_chunk_cache()
        assert self._chunk_cache is not None
        qv = np.asarray(self.embedder.embed(query), dtype=np.float64)
        qn = float(np.linalg.norm(qv))
        scored: list[dict] = []
        for path, start, chunk, vec in self._chunk_cache:
            denom = qn * float(np.linalg.norm(vec))
            score = float(qv @ vec / denom) if denom > 0.0 else 0.0
            scored.append(
                {
                    "path": str(path),
                    "line": start,
                    "text": chunk[:200],
                    "score": score,
                }
            )
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]

    # -- dispatcher ---------------------------------------------------------

    def search(
        self,
        query: str,
        mode: str = "content",
        limit: int = 50,
        roots: list[Path] | None = None,
        max_walk: int = DEFAULT_MAX_WALK,
    ) -> list[dict]:
        """Dispatch to name / content / semantic search by ``mode``.

        ``roots`` overrides the configured roots for this call only (a bounded
        walk capped at ``max_walk`` files), letting a search target an arbitrary
        local drive while still never escaping the provided root.
        """
        if mode == "name":
            return self.search_name(query, limit=limit, roots=roots, max_walk=max_walk)
        if mode == "semantic":
            return self.search_semantic(query, k=limit)
        return self.search_content(query, limit=limit, roots=roots, max_walk=max_walk)
