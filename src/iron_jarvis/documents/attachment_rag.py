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

SCANNED ATTACHMENTS (v1.174.0). "Only ever sees extracted TEXT" had a hole the
size of half a tax folder: an image-only PDF extracts to nothing, so a scanned
K-1 dropped into chat was chunked to "0 indexed sections" and the model was
handed silence about a document the user is looking at.
:func:`extract_for_rag_async` closes it — the same vision OCR the Documents
page and ``read_document`` already use, THROUGH THE SAME CACHE (frozen contract
5, ``ocr.ocr_document``, so a scan is transcribed once for the whole app and
never once per turn), off the event loop, bounded by a page cap, and HONEST:
when OCR is off, unwired, out of budget or refused (the mock guard), the caller
gets an empty text plus a note that SAYS SO. Nothing here ever invents a scan's
contents.
"""

from __future__ import annotations

import asyncio
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

#: The honest notes for a scan we could NOT read. Module constants because the
#: only thing worse than an unreadable attachment is an unreadable attachment
#: the model was not told about — the wording is asserted, not improvised.
#:
#: They are ASSEMBLED from parts because the note must match what actually
#: shipped. A hybrid page (a scan carrying a stamped header or a digital footer)
#: clears the scan detector while still handing over 1..79 real characters, and
#: a constant ending "so NOTHING in this file was read" then contradicts the
#: text printed right underneath it. :func:`honest_scan_note` swaps the tail —
#: and the PDF head, for a raster image that is not a PDF at all.
_PDF_HEAD = "scanned/image-only PDF — there is no text layer"
_IMAGE_HEAD = "image file — there is no text layer to read"
_NOTHING_READ = ", so NOTHING in this file was read"
_PARTIAL_READ = "; only the small text layer below was read"

OCR_DISABLED_NOTE = (
    f"{_PDF_HEAD}, and OCR is turned off (config ocr_enabled){_NOTHING_READ}"
)
OCR_NO_ROUTER_NOTE = (
    f"{_PDF_HEAD}, and no model is wired to transcribe it{_NOTHING_READ}"
)
OCR_BUDGET_NOTE = (
    f"{_PDF_HEAD}, and this turn's OCR page budget was already spent on earlier "
    f"attachments{_NOTHING_READ}; attach it on its own to have it transcribed"
)


def honest_scan_note(base: str, *, image: bool = False, text: str = "") -> str:
    """*base* rewritten to match what the caller is actually handing over.

    Two ways the constant above can lie, both measured:

    * *text* is non-empty — a hybrid page — and the note says NOTHING was read;
    * the attachment is a ``.bmp``/``.tif``, and the note calls it a PDF.
    """
    note = base.replace(_PDF_HEAD, _IMAGE_HEAD) if image else base
    if _NOTHING_READ in note and strip_page_marks(text).strip():
        note = note.replace(_NOTHING_READ, _PARTIAL_READ)
    return note


@dataclass
class Chunk:
    ref: str
    text: str


@dataclass
class Extraction:
    """Text for retrieval PLUS how it was obtained.

    ``note`` is never cosmetic: it is the ONLY place a caller learns that the
    text came from OCR (and how many pages), or that a real document reached
    the model as nothing at all."""

    text: str = ""
    note: str = ""
    ocr_used: bool = False
    #: Pages an OCR attempt actually spent (one vision call each) — the caller
    #: subtracts these from a per-turn budget so four scans cannot silently
    #: become forty vision calls.
    ocr_pages: int = 0


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


def strip_page_marks(text: str) -> str:
    """*text* without the ``[page N]`` scaffolding :func:`pdf_text_with_pages`
    adds — i.e. what the document actually SAYS."""
    return _PAGE_MARK.sub("", text or "")


def is_scan_candidate(path: "str | Path", text: str) -> bool:
    """Whether *path* is an image-only PDF, judged on its REAL content.

    Why this is not just ``looks_scanned_pdf``: the RAG extractor inserts a
    ``[page N]`` marker per page, so a 20-page scan arrives as ~180 chars of
    pure scaffolding and clears ``looks_scanned_pdf``'s 80-char "effectively
    empty" threshold — the longer the scan, the more certainly it was declared
    a normal text PDF and left unread. The markers are stripped first."""
    try:
        from .ocr import looks_scanned_pdf

        return bool(looks_scanned_pdf(Path(path), strip_page_marks(text)))
    except Exception:  # noqa: BLE001 — undetectable = not OCR-able; read on
        return False


#: ``ocr_pdf``'s success note carries "(N of M page(s) transcribed" — the only
#: place the ATTEMPTED page count survives the call. Coupled to ocr.py on
#: purpose and pinned by a test: the budget below is a SPEND, and a miss here
#: silently un-bounds it.
_OCR_COUNT = re.compile(r"\((\d+) of (\d+) page\(s\) transcribed")
#: ``ocr_document`` appends this when it served the transcription from the
#: contract-5 cache — i.e. when it made ZERO vision calls.
_OCR_CACHED = "cached — already transcribed earlier"
#: ``ocr_pdf``'s "the model returned no transcription" note: one vision call per
#: page was made and every response came back empty. THE case the turn budget
#: exists for, and the one where counting transcribed pages charges nothing.
_OCR_EMPTY_RESPONSES = "returned no transcription"


def ocr_pages_spent(note: str, text: str, cap: int) -> int:
    """Vision calls an OCR attempt actually COST, from what it reported.

    Counting ``[page N]`` markers charges only pages that came back with words
    on them, and ``ocr.py`` drops a page whose response is empty — a blank fax
    cover, a blank reverse side, a local VL model that answers with nothing. So
    4 scans × 10 blank pages measured as 40 vision calls charged 4, against a
    turn budget of 20. Charge what was ATTEMPTED instead, and charge a cache hit
    nothing at all — it is the whole point of contract 5 that it costs nothing.
    """
    note = note or ""
    if _OCR_CACHED in note:
        return 0
    hit = _OCR_COUNT.search(note)
    if hit:
        # Attempted = every page up to the cap that carried an image; the total
        # bounds it, and a transcript longer than that bound wins outright.
        return max(int(hit.group(1)), min(int(cap), int(hit.group(2))))
    if _OCR_EMPTY_RESPONSES in note:
        return max(1, int(cap))
    # Mock guard, a first-call provider fault, "nothing OCR could work on": one
    # call at most, and never zero — a budget that only counts successes can be
    # spent forever.
    return max(1, len(_PAGE_MARK.findall(text or "")))


async def extract_for_rag_async(
    path: "str | Path",
    *,
    router: object | None = None,
    ocr_enabled: bool = True,
    max_ocr_pages: "int | None" = None,
    ocr_budget: "int | None" = None,
    config: object | None = None,
) -> Extraction:
    """:func:`extract_for_rag` OFF THE EVENT LOOP, with the scanned-document OCR
    fallback wired in.

    The parse is CPU-bound and the PDF walk is filesystem-bound, so both go
    through ``asyncio.to_thread`` (the daemon is one loop — see CLAUDE.md).
    A scan we cannot transcribe returns ``text=""`` and a note that says why;
    it NEVER returns the marker scaffolding as if it were content, and never
    invents a line of it.

    THE TRANSCRIPTION GOES THROUGH THE CACHE (frozen contract 5). It calls
    :func:`ocr.ocr_document`, not ``ocr_pdf``, so a scan chat reads is written
    to ``<home>/ocr/`` and a scan the Documents page or ``read_document``
    already read is served from it — measured, the direct call re-paid six
    vision calls for the same 3-page PDF attached on two consecutive turns.
    That makes ``max_ocr_pages`` the PER-DOCUMENT cap and nothing else: it is
    half the cache key, so passing a shrinking per-turn remainder (7 pages this
    turn, 10 the next) would fragment the key and miss its own entries. The
    per-turn budget is a separate GATE, ``ocr_budget``.

    ``config`` is threaded through for the cache home and the pinned vision
    role; with none in scope the transcription still runs, just uncached."""
    p = Path(path)
    text = await asyncio.to_thread(extract_for_rag, p)
    from .ocr import MAX_OCR_PAGES, OCR_MARK, is_image, ocr_document

    image = is_image(p)
    if OCR_MARK in (text or ""):
        # The readers already served a cached transcription of these exact
        # bytes; it carries its own disclosure banner inline.
        return Extraction(text=text, ocr_used=True)
    if not image and not await asyncio.to_thread(is_scan_candidate, p, text):
        return Extraction(text=text)
    # From here nothing in `text` is content: a scan's is page scaffolding, and
    # an image's is "[image BMP 800x600, mode RGB]" — a size sentinel that a
    # fact-extraction model reads as the document. Hand back nothing rather
    # than something that reads like a very short document.
    if image or not strip_page_marks(text).strip():
        text = ""

    def _note(base: str) -> str:
        return honest_scan_note(base, image=image, text=text)

    if not ocr_enabled:
        return Extraction(text=text, note=_note(OCR_DISABLED_NOTE))
    if router is None:
        return Extraction(text=text, note=_note(OCR_NO_ROUTER_NOTE))
    try:
        cap = MAX_OCR_PAGES if max_ocr_pages is None else int(max_ocr_pages)
    except (TypeError, ValueError):
        cap = MAX_OCR_PAGES
    remaining = cap if ocr_budget is None else int(ocr_budget)
    if cap <= 0 or remaining <= 0:
        return Extraction(text=text, note=_note(OCR_BUDGET_NOTE))
    try:
        ocr_text, note = await ocr_document(p, router, config=config, max_pages=cap)
    except Exception as exc:  # noqa: BLE001 — OCR failure ≠ attachment failure
        return Extraction(
            text=text,
            note=(f"{'image file' if image else 'scanned PDF'} — OCR failed "
                  f"({type(exc).__name__}: {exc})"),
            ocr_pages=1,
        )
    spent = ocr_pages_spent(note, ocr_text, cap)
    if ocr_text:
        return Extraction(text=ocr_text, note=note, ocr_used=True, ocr_pages=spent)
    return Extraction(text=text, note=note, ocr_pages=spent)


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
              k: int = 6, char_budget: int = 2400, note: str = "") -> str:
    """An HONEST retrieval block for one oversized attachment: what the doc is,
    what was retrieved (with refs), what was not, and how to reach the rest.

    ``note`` (v1.174.0, optional) discloses HOW the text was obtained — e.g.
    "recovered via OCR (3 of 9 pages transcribed)". It rides its own line right
    under the header so a retrieval answer can never be mistaken for a reading
    of a document nobody could actually read. Omitted = the pre-v1.174.0 block,
    character for character."""
    chunks = chunk_text(text)
    capped = len(text) > 0 and (len(chunks) >= MAX_CHUNKS)
    top = retrieve(embedder, chunks, query, k=k)
    lines = [
        f"\n\n## Attached file: {name} — {len(text)} chars across "
        f"{len(chunks)} indexed section(s); showing the excerpts most relevant"
        " to the user's question (NOT the whole document)."
    ]
    if note:
        lines.append(f"\n[{note}]")
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
