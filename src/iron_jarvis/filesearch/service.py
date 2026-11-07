"""Cross-root file search service (§18 extension, §22 retrieval).

``FileSearchService`` walks a set of *configured roots* and answers three kinds
of query:

* ``search_name``    — glob / substring match on file paths.
* ``search_content`` — regex match on file contents, reported as path + line.
* ``search_semantic``— cosine similarity over embedded file chunks (only when an
  embedder is injected; otherwise disabled and returns an empty list). Since
  v1.443.0 it indexes its OWN set of roots (``meaning_roots``; the platform
  passes only the folders the user added) and builds that index on a
  background thread, waiting at most :data:`SEMANTIC_WAIT_S` for it — a
  caller that is not answered in time gets ``[]`` with ``notes.indexing``
  set, never a frozen chat turn (see ``filesearch/defaults.py``).

Roots may be a list (fixed) or a zero-argument callable (read on every search,
so a saved setting applies to the next search without a restart).

Hard guarantees:

* **Never escapes the roots.** Every result is verified to resolve inside one of
  the configured roots, so a symlink (or a crafted path) cannot leak files from
  elsewhere on disk.
* **Respects ignore patterns.** Directories named in ``ignore`` (``.git``,
  ``node_modules`` …) are pruned during the walk.
* **Skips binary files silently; counts unreadable and oversized ones** — the
  binary blob is ignored (NUL sniff, after a UTF-16 BOM check so a PowerShell
  ``>`` log is text, v1.232.0), and every other hole in the answer is reported,
  never crash a search, and the ones we could not decode are COUNTED and handed
  back in :class:`SearchNotes` so the caller can say so out loud.
* **Never answers a broken query with an empty result.** An uncompilable regex
  raises :class:`BadSearchPattern`; it is not reported as "no matches".
"""

from __future__ import annotations

import fnmatch
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core._numpy import np  # one-thread BLAS (v1.333.0)

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

#: Most text chunks the semantic index holds (v1.333.0, D8). The index used to
#: keep every chunk's TEXT plus a float64 vector for the life of the daemon,
#: over an unbounded walk; it now keeps one normalised float32 row per chunk
#: and a (file, line) reference, reads snippets back only for the top hits,
#: and stops here, which the search's notes say out loud.
MAX_SEMANTIC_CHUNKS = 50_000

#: Seconds a "By meaning" search waits for the meaning index to be built
#: (v1.443.0). A small folder is built well inside it, so the answer is the
#: same as before; a big one keeps building in the background and the caller
#: is told "still reading your folders" instead of waiting minutes (the memory
#: fabric asks this on every chat turn and agent run).
SEMANTIC_WAIT_S = 5.0

#: Words a caller may show when "By meaning" has no local embedding model
#: (the embedder fell back to the offline word-hash one).
WORD_ONLY_NOTE = (
    "[No local embedding model is connected, so this matched words, not meaning. "
    "To search by meaning: install Ollama, run `ollama pull nomic-embed-text`, connect "
    "Ollama in Settings › Connections, then restart Iron Jarvis.]"
)

#: Words for a "By meaning" search with no folder to look in.
NO_MEANING_FOLDERS_NOTE = (
    "[By meaning searches only the folders the user added on the File Search page "
    "(none are added yet), so nothing was searched by meaning. Search by name or "
    "content to look in the chat files folder and the projects' folders.]"
)

#: Words for a "By meaning" search answered while its index is still being built.
INDEXING_NOTE = (
    "[Still reading the folders for meaning search; try again in a minute. Search by "
    "name or content meanwhile.]"
)


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

    ``unreadable`` counts files we genuinely TRIED and failed to turn into
    text. ``oversize`` (v1.232.0, audit T7) counts files over
    :data:`MAX_FILE_BYTES` — they used to be a silent exclusion, which made a
    2 MB log the search "did not find" indistinguishable from one that has
    no match. A binary blob (NUL sniff) stays silent: it never was a text
    file, and counting every .png would drown the signal this carries.
    """

    unreadable: int = 0
    oversize: int = 0
    #: Text files the semantic index could not hold (v1.333.0): past
    #: :data:`MAX_SEMANTIC_CHUNKS`, or past the walk cap when it was built.
    not_indexed: int = 0
    #: v1.443.0: "By meaning" had no folder to look in.
    no_meaning_folders: bool = False
    #: v1.443.0: the meaning index was still being built when the answer was due.
    indexing: bool = False
    #: v1.443.0: the embedder is the offline word-hash one (no local model).
    words_only: bool = False

    def note(self) -> str:
        """The line(s) to append to a tool's output, or ``""`` when clean."""
        parts: list[str] = []
        if self.unreadable:
            parts.append(
                f"[{self.unreadable} file(s) skipped — unreadable encoding or failed "
                f"extraction. This search did NOT cover them.]"
            )
        if self.oversize:
            parts.append(
                f"[{self.oversize} file(s) skipped as oversize (over "
                f"{MAX_FILE_BYTES // 1_000_000} MB each). This search did NOT cover "
                f"them; open one directly with read_file if it matters.]"
            )
        if self.not_indexed:
            parts.append(
                f"[The meaning index is full, so {self.not_indexed} file(s) or more "
                f"were not searched by meaning. Search by name or content to reach them.]"
            )
        if self.no_meaning_folders:
            parts.append(NO_MEANING_FOLDERS_NOTE)
        if self.indexing:
            parts.append(INDEXING_NOTE)
        if self.words_only:
            parts.append(WORD_ONLY_NOTE)
        return "\n".join(parts)


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
    home. Both then add the Documents folder (label ``"Documents"``, v1.443.0). Returns ``[{"path", "label"}, ...]`` and only ever includes roots that
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
    # v1.443.0: the user's Documents folder (the Known Folder, so OneDrive's
    # moved one), so the File Search page can offer "Also search Documents"
    # by its real path. Never raises; skipped when it is the home folder.
    try:
        from ..core.userdirs import documents_dir

        _add(str(documents_dir()), "Documents")
    except Exception:  # noqa: BLE001
        pass
    return drives


@dataclass
class _SemanticIndex:
    """What semantic search keeps between calls (v1.333.0): each chunk as a
    ``(file slot, first line)`` reference and one normalised float32 row."""

    paths: list[Path]
    refs: list[tuple[int, int]]
    matrix: "np.ndarray"
    not_indexed: int = 0
    #: v1.443.0: the meaning roots this index was built over.
    roots: tuple[Path, ...] = ()


class FileSearchService:
    """Search by name, content, or semantics across configured roots."""

    def __init__(
        self,
        roots: "list[Path] | Callable[[], list[Path]]",
        embedder=None,
        ignore: set[str] | None = None,
        *,
        meaning_roots: "list[Path] | Callable[[], list[Path]] | None" = None,
    ) -> None:
        # ``roots`` (name/content search) and ``meaning_roots`` ("By meaning")
        # may each be a fixed list or a callable read on every search
        # (v1.443.0). ``meaning_roots=None`` = the same as ``roots`` (every
        # caller before v1.443.0).
        self._roots_src = roots if callable(roots) else self._normalise(roots)
        self._meaning_src = (
            None
            if meaning_roots is None
            else meaning_roots if callable(meaning_roots) else self._normalise(meaning_roots)
        )
        self.embedder = embedder
        self.ignore: set[str] = set(ignore) if ignore is not None else set(DEFAULT_IGNORE)
        self._indexed: list[Path] = []  # cached text-file paths after index()
        #: The roots ``_indexed`` was walked over (v1.443.0).
        self._indexed_for: tuple[Path, ...] = ()
        self._indexed_pair: tuple[tuple[Path, ...], list[Path]] = ((), [])
        #: True when index() stopped at DEFAULT_MAX_WALK files (v1.333.0).
        self._index_capped = False
        #: The semantic index (v1.333.0): None until first needed.
        self._chunk_cache: _SemanticIndex | None = None
        #: v1.443.0: ONE build at a time, on its own thread.
        self._build_lock = threading.Lock()
        self._build_thread: threading.Thread | None = None
        self._build_error: BaseException | None = None

    @staticmethod
    def _normalise(roots) -> list[Path]:
        """Resolve + de-duplicate, order kept."""
        seen: list[Path] = []
        for r in roots or []:
            try:
                rp = Path(r).resolve()
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            if rp not in seen:
                seen.append(rp)
        return seen

    @property
    def roots(self) -> list[Path]:
        """The roots name and content search look in, read now."""
        src = self._roots_src
        if callable(src):
            try:
                return self._normalise(src())
            except Exception:  # noqa: BLE001 — a broken provider searches nothing
                return []
        return list(src)

    @property
    def meaning_roots(self) -> list[Path]:
        """The roots "By meaning" indexes, read now (v1.443.0)."""
        src = self._meaning_src
        if src is None:
            return self.roots
        if callable(src):
            try:
                return self._normalise(src())
            except Exception:  # noqa: BLE001
                return []
        return list(src)

    @property
    def words_only(self) -> bool:
        """True when the embedder is the offline word-hash one (no local model)."""
        return self.embedder is not None and getattr(self.embedder, "model", "") == "mock"

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
        """Files to scan: the cached index (only when it was walked over exactly
        the roots this search covers) else a walk."""
        if roots is None:
            roots = self.roots
            # One read of (roots, paths): the meaning index is built on its
            # own thread, and two separate reads could pair one walk's roots
            # with another walk's files.
            indexed_for, indexed_paths = self._indexed_pair
            if indexed_paths and indexed_for == tuple(roots):
                indexed = list(indexed_paths)
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
        ``notes.unreadable``, and one over the size cap bumps ``notes.oversize``
        (v1.232.0), so the caller can report the hole. Outside-the-roots and
        binary-by-NUL-sniff stay silent on purpose — see :class:`SearchNotes`.
        """
        if self._root_for(path, roots) is None:
            return None
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                if notes is not None:
                    notes.oversize += 1
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
        # UTF-16 BEFORE the NUL sniff (v1.232.0, audit T7): every other byte
        # of a UTF-16 file IS a NUL, so a log PowerShell 5.1 wrote with ``>``
        # read as binary and was invisible to search. The BOM says what it is.
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            try:
                return data.decode("utf-16")
            except UnicodeDecodeError:
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

    def search_words(
        self,
        words: list[str],
        roots: list[Path],
        limit: int = 8,
        max_walk: int = 5_000,
        deadline_s: float = 2.0,
    ) -> tuple[list[dict], bool]:
        """Files whose path under ``roots`` contains EVERY word (C-08).

        For "Smith 2024 summary": each word must appear in the path relative
        to its root, with ``/ \\ _ - .`` read as spaces, so ``Smith_2024
        summary.xlsx`` matches. Bounded twice — ``max_walk`` files and
        ``deadline_s`` seconds — and says so: returns ``(hits, partial)`` where
        ``partial`` means the cap or the deadline cut the walk short, so a
        caller never presents a cut-off list as complete. Office lock files
        (``~$…``) and the app's writability probes are skipped. BLOCKING:
        call it off the event loop.
        """
        import time

        want = [w.lower() for w in words if w and w.strip()]
        if not want:
            return [], False
        stop_at = time.monotonic() + max(0.0, float(deadline_s))
        hits: list[dict] = []
        walked = 0
        for root in self._effective_roots(roots):
            prefix = str(root)
            for path in self._iter_files([root], max(0, max_walk - walked)):
                walked += 1
                if walked % 200 == 0 and time.monotonic() > stop_at:
                    return hits, True
                name = path.name
                if name.startswith("~$") or name.startswith(".ij-probe"):
                    continue
                full = str(path)
                rel = (full[len(prefix):] if full.startswith(prefix) else name).lower()
                flat = re.sub(r"[\\/_\-.]+", " ", rel)
                if all(w in rel or w in flat for w in want):
                    hits.append({"path": full, "root": prefix})
                    if len(hits) >= limit:
                        return hits, False
            if walked >= max_walk:
                return hits, True
        return hits, False

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
        """Walk the MEANING roots and cache the set of readable text files.
        Returns the count.

        Bounded by :data:`DEFAULT_MAX_WALK` like every other walk here (v1.333.0;
        it was the one unbounded walk); a capped walk is remembered so a
        semantic search can say what it did not cover. BLOCKING."""
        roots = self.meaning_roots
        paths: list[Path] = []
        walked = 0
        for path in self._iter_files(roots, max_walk=DEFAULT_MAX_WALK):
            walked += 1
            if self._read_text(path, roots) is not None:
                paths.append(path)
        self._indexed = paths
        self._indexed_for = tuple(roots)
        self._indexed_pair = (self._indexed_for, paths)
        self._index_capped = walked >= DEFAULT_MAX_WALK
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
        roots = tuple(self.meaning_roots)
        if not self._indexed or self._indexed_for != roots:
            self.index()
            roots = self._indexed_for
        paths: list[Path] = []
        refs: list[tuple[int, int]] = []
        rows: list[np.ndarray] = []
        not_indexed = 0
        for n, path in enumerate(self._indexed):
            if len(refs) >= MAX_SEMANTIC_CHUNKS:
                not_indexed = len(self._indexed) - n
                break
            text = self._read_text(path, list(roots))
            if not text:
                continue
            slot = -1
            for start, chunk in self._chunks(text):
                if len(refs) >= MAX_SEMANTIC_CHUNKS:
                    break
                if slot < 0:
                    paths.append(path)
                    slot = len(paths) - 1
                vec = np.asarray(self.embedder.embed(chunk), dtype=np.float32)
                norm = float(np.linalg.norm(vec))
                rows.append(vec / norm if norm > 0.0 else vec)  # a zero row scores 0
                refs.append((slot, start))
        matrix = np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.float32)
        self._chunk_cache = _SemanticIndex(paths, refs, matrix, not_indexed, roots)

    def _build_in_background(self) -> None:
        try:
            self._build_chunk_cache()
            self._build_error = None
        except BaseException as exc:  # noqa: BLE001 — handed to the waiting caller
            self._build_error = exc

    def _ready_index(self, notes: SearchNotes | None) -> "_SemanticIndex | None":
        """The meaning index for the current meaning roots, building it on its
        own thread (one at a time) and waiting at most :data:`SEMANTIC_WAIT_S`.
        None = still building (``notes.indexing`` is set)."""
        want = tuple(self.meaning_roots)
        cache = self._chunk_cache
        if cache is not None and cache.roots == want:
            return cache
        with self._build_lock:
            thread = self._build_thread
            if thread is None or not thread.is_alive():
                cache = self._chunk_cache
                if cache is not None and cache.roots == want:
                    return cache
                self._build_error = None
                thread = threading.Thread(
                    target=self._build_in_background, name="filesearch-meaning-index", daemon=True
                )
                self._build_thread = thread
                thread.start()
        thread.join(SEMANTIC_WAIT_S)
        if thread.is_alive():
            if notes is not None:
                notes.indexing = True
            return None
        if self._build_error is not None:
            err, self._build_error = self._build_error, None
            raise err
        cache = self._chunk_cache
        if cache is None or cache.roots != tuple(self.meaning_roots):
            # The folders changed while it was built: the next search rebuilds.
            if notes is not None:
                notes.indexing = True
            return None
        return cache

    def _chunk_text(self, path: Path, start: int, roots: list[Path] | None = None) -> str:
        """Re-read one chunk's text for a hit (only the top hits pay this)."""
        text = self._read_text(path, roots) or ""
        lines = text.splitlines()[start - 1 : start - 1 + _CHUNK_LINES]
        return "\n".join(lines).strip()

    def search_semantic(
        self, query: str, k: int = 5, notes: SearchNotes | None = None
    ) -> list[dict]:
        """Cosine-similarity search over embedded file chunks (needs an embedder).

        One matrix product over normalised float32 rows (v1.333.0); ties keep
        file order, as the old per-chunk loop's stable sort did.

        v1.443.0: over :attr:`meaning_roots` only; ``[]`` with
        ``notes.no_meaning_folders`` when there are none, ``[]`` with
        ``notes.indexing`` while a big index is still being built, and
        ``notes.words_only`` when there is no local embedding model."""
        if self.embedder is None:
            return []
        if notes is not None and self.words_only:
            notes.words_only = True
        if not self.meaning_roots:
            if notes is not None:
                notes.no_meaning_folders = True
            return []
        index = self._ready_index(notes)
        if index is None:
            return []
        if notes is not None:
            notes.not_indexed += index.not_indexed + (1 if self._index_capped else 0)
        if not index.refs:
            return []
        qv = np.asarray(self.embedder.embed(query), dtype=np.float32)
        qn = float(np.linalg.norm(qv))
        scores = index.matrix @ (qv / qn) if qn > 0.0 else np.zeros(len(index.refs))
        order = np.argsort(-scores, kind="stable").tolist()[:k]
        out: list[dict] = []
        for i in order:
            slot, start = index.refs[i]
            path = index.paths[slot]
            out.append(
                {
                    "path": str(path),
                    "line": start,
                    "text": self._chunk_text(path, start, list(index.roots))[:200],
                    "score": float(scores[i]),
                }
            )
        return out

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
            return self.search_semantic(query, k=limit, notes=notes)
        return self.search_content(
            query, limit=limit, roots=roots, max_walk=max_walk, notes=notes
        )
