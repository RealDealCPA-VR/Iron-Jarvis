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
  never crash a search, and the ones we could not decode are COUNTED and handed
  back in :class:`SearchNotes` so the caller can say so out loud.
* **Never answers a broken query with an empty result.** An uncompilable regex
  raises :class:`BadSearchPattern`; it is not reported as "no matches".
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
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


class BadSearchPattern(ValueError):
    """A content-search regex that never compiled — RAISED, never answered ``[]``.

    ``search_content`` used to swallow ``re.error`` and return an empty list, so
    a caller could not tell "that pattern is invalid" from "this text is nowhere
    in your files" — and the model then told the user the text does not exist.
    That is the same failure the v1.153.1 truncation rule exists to prevent
    ("a silently short listing reads as complete"), here in the one tool meant to
    reach OUTSIDE the workspace. The sibling ``grep`` has always had this right
    (``tools/builtins.GrepTool`` returns ``bad regex: ...``); this brings the
    broader search onto the same contract.

    ``literal`` is OFFERED, never applied: silently falling back to an escaped
    literal search would answer a different question than the one asked, which is
    the same class of bug as the empty list it replaces.

    **The ``ValueError`` base is load-bearing, not decoration.** The other two
    callers of ``search_content`` are ``GET /filesearch`` and the ``file-search``
    CLI, and neither can be edited from here. ``daemon/app.py`` registers a
    ``ValueError`` handler that answers **400** with the message, and Starlette
    walks ``type(exc).__mro__`` to find it — so the dashboard's search box gets a
    readable "bad regex" instead of a 500, which this project's history says the
    UI reads as "daemon offline". Re-basing this on ``Exception`` would turn a
    typo in the search box into an outage-shaped error.
    """

    def __init__(self, pattern: str, exc: re.error) -> None:
        self.pattern = pattern
        self.reason = str(exc)
        self.literal = re.escape(pattern)
        super().__init__(f"bad regex: {exc}")


@dataclass
class SearchNotes:
    """What a search could NOT cover, carried back to the caller.

    A file we failed to decode or extract is a HOLE in the answer, and a hole the
    caller cannot see is indistinguishable from a genuine miss — the cp1252 CSV
    that Excel/QuickBooks/Lacerte exported simply is not in the results. Passing
    one of these into ``search``/``search_content`` opts the caller into being
    told; the count then rides back out in the tool's own output note, next to
    grep's truncation note, rather than in a second reporting mechanism.

    Only files we genuinely TRIED and failed to turn into text are counted. A
    binary blob (NUL sniff) and an oversized file are deliberate, well-understood
    exclusions — counting them would put a scary note on every search of a real
    folder and drown the signal this exists to carry.
    """

    unreadable: int = 0

    def note(self) -> str:
        """The one line to append to a tool's output, or ``""`` when clean."""
        if not self.unreadable:
            return ""
        return (
            f"[{self.unreadable} file(s) skipped — unreadable encoding or failed "
            f"extraction. This search did NOT cover them.]"
        )


#: Bound ONCE on first use to ``documents/readers._decode_bytes`` — this project's
#: single text decoder (utf-8-sig → strict cp1252 → charset-normalizer → latin-1),
#: written precisely so "cp1252/latin-1 office exports survive instead of turning
#: into replacement characters". Both search paths hard-coded ``decode("utf-8")``
#: and dropped everything else on the floor.
#:
#: LAZY AND CACHED for a measured reason: importing it pulls in the whole
#: ``iron_jarvis.documents`` package (writers, markitdown, tools), and this runs
#: once per file across a walk of up to ``DEFAULT_MAX_WALK`` files. The lazy
#: private import mirrors ``documents/redact.py``'s.
_DECODER: Callable[[bytes], str] | None = None


def _decode_text(data: bytes) -> str:
    """Decode arbitrary text bytes with the project's shared decoder."""
    global _DECODER
    if _DECODER is None:
        from ..documents.readers import _decode_bytes

        _DECODER = _decode_bytes
    return _DECODER(data)


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
        self,
        path: Path,
        roots: list[Path] | None = None,
        notes: SearchNotes | None = None,
    ) -> str | None:
        """Return decoded text, or None if outside roots / oversized / binary / unreadable.

        Every ``None`` that means "we tried to read this file and could not" bumps
        ``notes.unreadable`` so the caller can report the hole. The three that mean
        "this was never a text file to begin with" (outside the roots, over the
        size cap, binary by NUL sniff) stay silent on purpose — see
        :class:`SearchNotes`.
        """
        if self._root_for(path, roots) is None:
            return None
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return None
        except OSError:
            return None
        # Office / PDF documents: extract their text so content search reaches
        # inside PDFs, Word, Excel, and PowerPoint instead of skipping them.
        if path.suffix.lower() in {".pdf", ".docx", ".xlsx", ".pptx"}:
            try:
                from ..documents import extract_text

                return extract_text(path)
            except Exception:
                # An encrypted, corrupt or truncated document. It carries text we
                # simply could not reach — the user's search did not cover it.
                if notes is not None:
                    notes.unreadable += 1
                return None
        try:
            data = path.read_bytes()
        except (OSError, PermissionError, ValueError):
            # A locked handle or a denied ACL is a hole in the answer too.
            if notes is not None:
                notes.unreadable += 1
            return None
        if b"\x00" in data:  # cheap binary sniff
            return None
        try:
            # NOT ``decode("utf-8")``. That silently dropped every cp1252/latin-1
            # file — an Excel/QuickBooks/Lacerte CSV with a curly apostrophe or an
            # accented client name was invisible to search with no signal at all.
            return _decode_text(data)
        except Exception:  # noqa: BLE001 — the decoder is total; this covers a
            # failed import of the documents package, which must degrade to a
            # REPORTED skip rather than an unexplained miss.
            if notes is not None:
                notes.unreadable += 1
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
        notes: SearchNotes | None = None,
    ) -> list[dict]:
        """Regex-search file contents. Returns ``{path, line, text}`` dicts.

        Raises :class:`BadSearchPattern` for a regex that does not compile, and
        counts undecodable files into ``notes`` when one is supplied.
        """
        try:
            rx = re.compile(regex)
        except re.error as exc:
            # NOT ``return []``. See BadSearchPattern: an empty list here is a
            # confident wrong answer, and 'read_file(' / 'C:\\Users' / 'a[b' are
            # exactly the literals a model reaches for.
            raise BadSearchPattern(regex, exc) from exc
        eff_roots = self._effective_roots(roots)
        results: list[dict] = []
        for path in self._candidate_files(roots, max_walk):
            root = self._root_for(path, eff_roots)
            if root is None:
                continue
            if globs and not self._matches_globs(path.name, self._rel(path, root), globs):
                continue
            text = self._read_text(path, eff_roots, notes)
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
        notes: SearchNotes | None = None,
    ) -> list[dict]:
        """Dispatch to name / content / semantic search by ``mode``.

        ``roots`` overrides the configured roots for this call only (a bounded
        walk capped at ``max_walk`` files), letting a search target an arbitrary
        local drive while still never escaping the provided root.

        ``notes`` collects what the search could not cover (content mode only —
        a name search never opens a file, so it has nothing to skip).
        """
        if mode == "name":
            return self.search_name(query, limit=limit, roots=roots, max_walk=max_walk)
        if mode == "semantic":
            return self.search_semantic(query, k=limit)
        return self.search_content(
            query, limit=limit, roots=roots, max_walk=max_walk, notes=notes
        )
