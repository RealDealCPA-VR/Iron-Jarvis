"""Attachment RAG — analyze BIG attached documents on small context windows.

Before this module, a chat attachment was extracted and HEAD-CLIPPED to a fixed
budget: page 1 of a 200-page PDF reached the model, the rest silently didn't
(beyond the truncation marker). That defeats document analysis — especially on
local models with small windows.

Now an attachment that exceeds the inline budget is chunked, embedded through
the platform's shared embedder (the persistent :class:`CachingEmbedder`, so a
re-asked document costs nothing), and the turn is grounded on the top-k chunks
relevant to the QUESTION, each carrying a location ref (``p.12`` for PDFs,
``part 7`` otherwise) so answers can cite where they came from.

Scoring is HYBRID — cosine over the embedder plus a lexical term-overlap
bonus — so retrieval stays sane offline (the deterministic MockEmbedder) and
sharpens when a real local embedder (Ollama ``nomic-embed-text``) is wired.
Everything is bounded and honest: caps carry explicit markers, and the block
tells the model how to reach unretrieved parts (``read_document`` with
``page_range``).

Works for any policy-allowed path — local disk, a network share, or a tailnet
folder — because it only ever sees extracted TEXT from the normal readers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Chunking geometry (chars). ~1600 chars ≈ 400 tokens — small enough that a
#: handful of chunks fits an 8k local window next to the conversation.
CHUNK_CHARS = 1600
CHUNK_OVERLAP = 200
#: Embedding cap per document — beyond this, later chunks are dropped with an
#: explicit marker (embedding thousands of chunks per TURN would stall chat).
MAX_CHUNKS = 240

_PAGE_MARK = re.compile(r"\[page (\d+)\]")
_WORD = re.compile(r"[a-z0-9]{2,}")


@dataclass
class Chunk:
    ref: str
    text: str


def pdf_text_with_pages(path: "str | Path") -> str:
    """PDF text with ``[page N]`` markers between pages, so chunk refs are real
    page numbers. Raises like the normal reader on unreadable files."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:  # noqa: BLE001
            unlocked = 0
        if not unlocked:
            raise ValueError("PDF is password-protected")
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        parts.append(f"[page {i}]\n" + (page.extract_text() or ""))
    return "\n".join(parts)


def extract_for_rag(path: "str | Path") -> str:
    """Full text of *path* for retrieval: PDFs page-marked, everything else via
    the standard reader (which handles docx/xlsx/pptx/csv/txt/encodings)."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        try:
            return pdf_text_with_pages(p)
        except Exception:  # noqa: BLE001 — fall back to the plain reader below
            pass
    from .readers import extract_text

    return extract_text(p)


def chunk_text(text: str, *, chunk_chars: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    """Split *text* into overlapping chunks, tracking the current PDF page from
    ``[page N]`` markers when present (else ``part N`` refs)."""
    text = text or ""
    if not text.strip():
        return []
    chunks: list[Chunk] = []
    pos = 0
    part = 0
    while pos < len(text) and len(chunks) < MAX_CHUNKS:
        piece = text[pos: pos + chunk_chars]
        part += 1
        # The ref names the page the chunk STARTS on: the last marker at or
        # before the chunk start, else the first marker inside it (a chunk
        # that opens on a page boundary), else a neutral part number.
        before = _PAGE_MARK.findall(text[: pos + 1])
        inside = _PAGE_MARK.findall(piece)
        if before:
            ref = f"p.{before[-1]}"
        elif inside:
            ref = f"p.{inside[0]}"
        else:
            ref = f"part {part}"
        chunks.append(Chunk(ref=ref, text=piece))
        if pos + chunk_chars >= len(text):
            break
        pos += chunk_chars - overlap
    return chunks


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower()))


def _cosine(u: list[float], v: list[float]) -> float:
    if not u or not v or len(u) != len(v):
        return 0.0
    du = sum(x * x for x in u) ** 0.5
    dv = sum(x * x for x in v) ** 0.5
    if du == 0.0 or dv == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(u, v)) / (du * dv)


def retrieve(embedder, chunks: list[Chunk], query: str, k: int = 6) -> list[Chunk]:
    """Top-*k* chunks for *query*: 0.7·cosine + 0.3·lexical term overlap.
    A failing embedder degrades to pure lexical rather than erroring."""
    if not chunks:
        return []
    qtok = _tokens(query)
    qvec: list[float] = []
    if embedder is not None:
        try:
            qvec = list(embedder.embed((query or "")[:2000]))
        except Exception:  # noqa: BLE001 — lexical-only is still useful
            qvec = []
    scored: list[tuple[float, int]] = []
    for i, ch in enumerate(chunks):
        lex = (len(qtok & _tokens(ch.text)) / len(qtok)) if qtok else 0.0
        cos = 0.0
        if qvec:
            try:
                cos = _cosine(qvec, list(embedder.embed(ch.text[:2000])))
            except Exception:  # noqa: BLE001
                cos = 0.0
        scored.append((0.7 * cos + 0.3 * lex, i))
    scored.sort(key=lambda t: (-t[0], t[1]))
    # SCORE order, not document order: the render budget is consumed top-down,
    # so the most relevant chunk must come first — in document order a run of
    # weakly-matching early chunks would eat the budget before the real hit.
    return [chunks[i] for _s, i in scored[: max(1, k)]]


def rag_block(name: str, text: str, query: str, embedder, *,
              k: int = 6, char_budget: int = 2400) -> str:
    """An HONEST retrieval block for one oversized attachment: what the doc is,
    what was retrieved (with refs), what was not, and how to reach the rest."""
    chunks = chunk_text(text)
    capped = len(text) > 0 and (len(chunks) >= MAX_CHUNKS)
    top = retrieve(embedder, chunks, query, k=k)
    lines = [
        f"\n\n## Attached file: {name} — {len(text)} chars across "
        f"{len(chunks)} indexed section(s); showing the excerpts most relevant"
        " to the user's question (NOT the whole document)."
    ]
    used = 0
    shown = 0
    for ch in top:
        body = ch.text.strip()
        room = char_budget - used
        if room <= 80:
            break
        if len(body) > room:
            body = body[:room] + " […]"
        lines.append(f"\n[{ch.ref}] {body}")
        used += len(body)
        shown += 1
    if shown == 0:
        lines.append("\n(no relevant excerpt found for this question)")
    lines.append(
        f"\n(Retrieved {shown} of {len(chunks)} sections"
        + ("; the index covers only the first part of a very large file"
           if capped else "")
        + ". For other parts, use read_document with page_range, or"
        " excel_profile/excel_query for spreadsheets.)"
    )
    return "".join(lines)
