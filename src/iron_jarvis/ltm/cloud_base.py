"""Shared base for cloud-drive long-term-memory connectors (§21 extension).

Google Drive, OneDrive (Microsoft Graph) and Dropbox all expose the same shape of
knowledge store: a searchable tree of files that the agent can RAG over and (for
most of them) append notes to. :class:`CloudDriveConnector` captures everything
that is identical across the three — auth, candidate download, text extraction,
chunking, embedding-based ranking and graceful degradation — leaving each provider
subclass to implement ONLY the three HTTP specifics (search / download / upload).

Design mirrors the other networked connectors in this package
(:mod:`iron_jarvis.ltm.notion`, :mod:`iron_jarvis.ltm.ssh`):

* The HTTP client is *injected* (``http``), so tests never open a socket. It is an
  ``httpx.Client``-shaped object exposing ``get``/``post``/``put`` that return a
  response with ``.content``, ``.json()`` and ``.raise_for_status()``. A real
  ``httpx.Client`` is created lazily only when none is injected.
* Auth is resolved lazily via ``token_resolver`` (a live OAuth *access* token —
  the integration layer wires this to ``connections.credential(provider)`` which
  auto-refreshes). A missing token degrades to ``[]`` on search and a clear error
  on append; nothing is ever stored on the connector.
* :class:`LTMConnector` is synchronous, so these connectors are synchronous too
  (they are driven from :class:`~iron_jarvis.ltm.manager.LongTermMemory`, itself
  called synchronously). A synchronous ``httpx.Client`` is therefore the correct,
  consistent choice — an async client cannot be driven from the sync ``search``
  contract without an event-loop bridge that deadlocks under a running loop.

Ranking follows :class:`~iron_jarvis.ltm.base.MarkdownDirConnector`: an injected
``embedder`` adds a cosine-similarity boost over lexical scoring; with no embedder
it falls back to pure lexical substring ranking. Large files are CHUNKED before
embedding, so a big PDF ranks on its most relevant passage rather than an averaged
blur. A single bad file (download error, unsupported binary, decode failure) is
skipped — never fatal — and every downloaded temp file is cleaned up.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..documents.readers import extract_text
from .base import LTMConnector, MarkdownDirConnector, _cosine, _snippet, slugify

#: Default chunk sizing — ~40 lines or ~1500 chars, whichever comes first.
DEFAULT_CHUNK_LINES = 40
DEFAULT_CHUNK_CHARS = 1500
#: How many candidate files to pull + download per search by default.
DEFAULT_MAX_FILES = 8
_SNIPPET_HEAD = 400


def chunk_text(
    text: str,
    max_lines: int = DEFAULT_CHUNK_LINES,
    max_chars: int = DEFAULT_CHUNK_CHARS,
) -> list[str]:
    """Split ``text`` into ~``max_lines``/``max_chars`` chunks for embedding.

    A chunk boundary is hit at either limit. An oversized single line (e.g. a PDF
    that extracts to one giant line) is windowed by characters so it still chunks.
    Empty/whitespace-only chunks are dropped.
    """
    if not text or not text.strip():
        return []
    max_lines = max(1, int(max_lines or DEFAULT_CHUNK_LINES))
    max_chars = max(1, int(max_chars or DEFAULT_CHUNK_CHARS))
    lines = text.splitlines() or [text]
    chunks: list[str] = []
    buf: list[str] = []
    buf_chars = 0

    def flush() -> None:
        nonlocal buf, buf_chars
        if buf:
            joined = "\n".join(buf).strip()
            if joined:
                chunks.append(joined)
            buf = []
            buf_chars = 0

    for line in lines:
        # A single line longer than the char budget is windowed on its own.
        while len(line) > max_chars:
            flush()
            head, line = line[:max_chars], line[max_chars:]
            if head.strip():
                chunks.append(head)
        buf.append(line)
        buf_chars += len(line) + 1
        if len(buf) >= max_lines or buf_chars >= max_chars:
            flush()
    flush()
    return chunks


def text_from_bytes(data: bytes, filename: str) -> str:
    """Extract plain text from downloaded ``data`` by staging a temp file.

    The temp file keeps ``filename``'s suffix so :func:`extract_text` dispatches to
    the right reader (pdf/docx/xlsx/pptx/csv/text). The temp file is always removed.
    Raises whatever :func:`extract_text` raises for unsupported/binary content — the
    caller is expected to catch and skip.
    """
    suffix = Path(filename or "").suffix or ".txt"
    fd, tmp = tempfile.mkstemp(prefix="ij-cloud-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return extract_text(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class CloudDriveConnector(LTMConnector):
    """Base connector for a cloud file store; subclasses supply the API specifics.

    Subclasses MUST override :attr:`provider` and the three hooks
    :meth:`_search_files`, :meth:`_download` and :meth:`_upload`.
    """

    #: Provider id used both as the default connector name AND as the credential
    #: key the integration layer passes to ``connections.credential(provider)``.
    provider = "cloud"
    name = "cloud"

    def __init__(
        self,
        token_resolver: Callable[[], str | None],
        http: Any = None,
        *,
        name: str | None = None,
        folder: str = "",
        max_files: int = DEFAULT_MAX_FILES,
        embedder: Any = None,
        chunk_lines: int = DEFAULT_CHUNK_LINES,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
    ) -> None:
        self.token_resolver = token_resolver
        self._http = http
        self.name = name or self.provider
        self.folder = folder or ""
        self.max_files = max(1, int(max_files or DEFAULT_MAX_FILES))
        self.embedder = embedder
        self.chunk_lines = int(chunk_lines or DEFAULT_CHUNK_LINES)
        self.chunk_chars = int(chunk_chars or DEFAULT_CHUNK_CHARS)

    # -- HTTP + auth ------------------------------------------------------
    def _client(self) -> Any:
        if self._http is None:
            import httpx  # lazy: keep the import off the offline/mock path

            self._http = httpx.Client(timeout=30.0, follow_redirects=True)
        return self._http

    def _auth_headers(self, token: str) -> dict[str, str]:
        """Bearer auth header. Providers may extend for per-endpoint needs."""
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _raise(resp: Any) -> None:
        rfs = getattr(resp, "raise_for_status", None)
        if callable(rfs):
            rfs()

    # -- provider hooks (override these) ----------------------------------
    def _search_files(
        self, query: str, headers: dict[str, str], limit: int
    ) -> list[dict[str, Any]]:
        """Return candidate file metas: ``{id, name, ref, ...}`` (provider fields)."""
        raise NotImplementedError

    def _download(self, meta: dict[str, Any], headers: dict[str, str]) -> bytes:
        """Return the raw bytes of the file described by ``meta``."""
        raise NotImplementedError

    def _upload(self, title: str, content: str, headers: dict[str, str]) -> str:
        """Upload ``title``.md with ``content``; return a ref (web link / id)."""
        raise NotImplementedError

    # -- LTMConnector -----------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        token = self.token_resolver()
        if not token:
            return []  # not connected -> empty, never a crash
        headers = self._auth_headers(token)
        try:
            candidates = self._search_files(query, headers, self.max_files)
        except Exception:  # noqa: BLE001 — a failed search yields no hits, not a crash
            return []

        q_emb = None
        if self.embedder is not None and query.strip():
            try:
                q_emb = self.embedder.embed(query)
            except Exception:  # noqa: BLE001 — embedder trouble -> lexical fallback
                q_emb = None

        scored: list[tuple[float, dict[str, Any]]] = []
        for meta in candidates[: self.max_files]:
            filename = str(meta.get("name") or meta.get("id") or "")
            try:
                data = self._download(meta, headers)
            except Exception:  # noqa: BLE001 — skip one bad file, keep going
                continue
            if not data:
                continue
            try:
                text = text_from_bytes(data, filename)
            except Exception:  # noqa: BLE001 — unsupported/binary/decode -> skip
                continue
            if not text.strip():
                continue
            ranked = self._rank_file(filename, text, query, q_emb)
            if ranked is None:
                continue
            score, snippet = ranked
            scored.append(
                (
                    score,
                    {
                        "title": filename,
                        "snippet": snippet,
                        "ref": str(meta.get("ref") or meta.get("id") or ""),
                        "source": self.name,
                    },
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in scored[:k]]

    def append(self, title: str, content: str) -> str:
        token = self.token_resolver()
        if not token:
            raise RuntimeError(
                f"{self.name}: no access token; connect the {self.provider} account "
                "to append notes."
            )
        headers = self._auth_headers(token)
        return self._upload(title, content, headers)

    # -- ranking ----------------------------------------------------------
    def _rank_file(
        self, title: str, text: str, query: str, q_emb: list[float] | None
    ) -> tuple[float, str] | None:
        """Score one file's text against ``query``; return ``(score, snippet)``.

        With an embedding available, the file is chunked and scored on its BEST
        chunk (cosine), plus a small lexical boost for filename/content matches;
        the snippet is drawn from that winning chunk. Without an embedder it degrades
        to pure lexical scoring (skipping non-matches, like the markdown connector).
        """
        chunks = chunk_text(text, self.chunk_lines, self.chunk_chars)
        if not chunks:
            return None
        if q_emb is not None:
            best_score = -2.0
            best_chunk = chunks[0]
            for chunk in chunks:
                try:
                    emb = self.embedder.embed(chunk)
                except Exception:  # noqa: BLE001 — a bad chunk embed just doesn't win
                    continue
                sim = _cosine(q_emb, emb)
                if sim > best_score:
                    best_score = sim
                    best_chunk = chunk
            lexical = MarkdownDirConnector._lexical_score(title, text, query)
            score = best_score * 10.0 + lexical
            return score, _snippet(best_chunk, query, _SNIPPET_HEAD)
        # Lexical-only fallback: the drive API already matched on the query, but we
        # still drop a candidate with zero textual overlap to keep hits relevant.
        lexical = MarkdownDirConnector._lexical_score(title, text, query)
        if lexical <= 0.0:
            return None
        return lexical, _snippet(text, query, _SNIPPET_HEAD)

    # -- small shared helpers for subclasses ------------------------------
    @staticmethod
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    def _note_name(self, title: str) -> str:
        """Filesystem-safe ``<slug>.md`` name for an appended note."""
        return f"{slugify(title)}.md"
