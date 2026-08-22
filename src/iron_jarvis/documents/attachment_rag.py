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

THE ATTACHMENT IS A LIVE FILE, NOT A TEXT DUMP (v1.196.0). Everything above
turns an attachment into TEXT — which is the right answer for "what does this
say?" and the wrong one for "what is the total?" or "add a column". See
:func:`live_file_line`: the block now also hands over the file's ABSOLUTE path
and the verbs that apply to its TYPE, so a workbook can be queried and edited
instead of only read back as a flattened grid.

THE HANDOFF MUST BE TRUE IN BOTH DIRECTIONS, and the first cut of it was not:
naming a verb whose refusal the model will then have to relay is the same lie
as naming no verb at all, one step later. Two rules keep it honest, and both
came out of a measured refusal (see :data:`_DERIVED_TARGET` and
``chat_turn._resolve_armed_tools``): the line says where a change may LAND, and
every verb it names is a verb the turn actually ARMS.

READ ARMS ON TYPE; CHANGE NEEDS INTENT (v1.196.0, third cut — and this one is a
CONSENT repair, not a reachability one). The two rules above made "named" and
"armed" the same set, which was right, and then armed that whole set off the
attachment's SUFFIX alone. Measured on ``chat_turn._resolve_armed_tools``:

    "thanks!"             + client_fees.xlsx -> ... excel_edit, excel_apply_spec
    "thanks!"             + summary.docx     -> ... convert_document, write_document
    "summarize this"      + report.pdf       -> ... pdf_arrange, pdf_split
    "what does this say?" + notes.txt        -> ... convert_document, write_document

Four read-only requests, every one arming file MUTATORS — and an armed name is
not merely offered: chat passes the armed list as the turn's ``session_allow``,
so those tools would have run with NO approval card. Attaching a file is consent
to have it READ, never consent to have it rewritten.

So :class:`LiveVerbs` splits its arming key in two. ``read_tools`` still arm on
the type alone — that is the whole point of this wave (12 of 18 document tools
had never run once) and is not weakened by a single sentence. ``change_tools``
arm only through :func:`change_verbs_wanted`, which asks
``tools.autoselect.select_auto_tools`` — the module that ALREADY does
deterministic regex intent scoring, and already gets this right at its own layer
— rather than growing a second intent detector here. Two detectors for one rule
is the drift this file argues against three times already.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

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


# --------------------------------------------------------------------------- #
# THE LIVE-FILE HANDOFF (v1.196.0)
#
# MEASURED, not guessed. The event ledger of the user's install (482
# `tool.executed` rows, 2026-07-06..2026-08-20) says `read_document` ran 96
# times — the second most-used tool in the app — while ALL EIGHT `excel_*`
# tools ran ZERO times, across ~10 stored messages that mention .xlsx. That is
# not "Excel isn't wanted": it is a path that does not work. Three compounding
# defects, all of them in what the model was TOLD about the attachment:
#
# (1) NO PATH. The block named the file (`rag_block(p.name, ...)`) while the
#     turn's tools run in the GROUNDED PROJECT ROOT, and the upload physically
#     lives in `<home>/uploads`. Measured: with the project as the workspace,
#     `excel_read {"path": "client_fees.xlsx"}` -> FileNotFoundError and
#     `excel_edit` -> "no such workbook in the workspace". The model was told to
#     use a tool and handed a name that tool cannot open. Same lesson as
#     v1.153.2 ("a tool that writes a file says WHERE, ABSOLUTELY"), in the
#     other direction: a bare filename has burned this project before.
# (2) FRAMED AS A FALLBACK. "For OTHER PARTS ... use excel_profile/excel_query"
#     only speaks to a model that ran out of document. When the whole sheet fits
#     — the common case for a real workbook — it already has the text and has no
#     reason to call anything. A spreadsheet is not prose: the useful operations
#     are QUERY, PROFILE and EDIT on the live file, not re-reading a flattened
#     rendering of it.
# (3) THE MUTATING TOOLS WERE NEVER NAMED. `excel_edit`/`excel_apply_spec`
#     appeared nowhere, so a model looking at a text dump of a workbook could
#     not discover that it may CHANGE it.
# --------------------------------------------------------------------------- #

#: The TARGET clause for every DERIVED change verb (``in_place=False``).
#:
#: v1.196.0 REPAIR — this constant exists because the first cut of this table
#: shipped the exact lie the module was written to remove. The ``in_place``
#: verdict was reasoned about the SOURCE ("the tool reads ANY policy-allowed
#: source, so promising it is always true") and then used to skip the workspace
#: check entirely, so a ``.docx`` attached from ``<home>/uploads`` into a
#: project-grounded chat got an unqualified "Change it: convert_document,
#: write_document." next to an absolute path OUTSIDE the workspace. Measured on
#: exactly that block (see ``tests/test_attachment_handoff_v1196.py``):
#:
#:     write_document   {"path": <the path the block just named>}   -> ok=False
#:         PermissionError: ... escapes the session workspace
#:     convert_document {"source": <abs>, "target": <abs beside it>} -> ok=False
#:     convert_document {"source": <abs>, "target": "letter.pdf"}    -> ok=True
#:
#: The instruction that made this survivable ("write changes as a NEW file
#: there") lived ONLY in the in-place branch and was structurally unreachable
#: for every derived type — i.e. for ``.docx``/``.pptx``/``.txt``/``.md``/
#: ``.json``/``.csv``/``.bmp``, which by the same event ledger this wave is
#: built on is what ``read_document``'s 96 calls were mostly about.
#:
#: The wording is UNCONDITIONAL on purpose, and that is what makes it safe to
#: say without re-deciding per file: "reads this path, writes into the
#: workspace" is true whether or not the attachment happens to sit inside the
#: workspace, so it never has to claim "this path is refused" — which would
#: itself be false for an ungrounded chat, where the upload IS the workspace.
#: Same shape as the absolute-path rule for READS (v1.153.2, in the other
#: direction): hand over the ONE form that resolves in both workspaces.
#:
#: 21 tokens (``context.budget.estimate_tokens``), and it rides EVERY derived
#: attachment, so the wording was trimmed against the meter: the first draft
#: spelled the same fact out in 32. What could not be traded away is the second
#: half — naming the tools without saying how to address the target is what
#: produced the measured PermissionError in the first place.
_DERIVED_TARGET = (
    # "output", not "target": the arg is named `target` on convert_document and
    # `path`/`output`/`out_dir` on write_document/pdf_arrange/pdf_split, so the
    # sentence has to name the CONCEPT or it is wrong for three of the five.
    " (they write into your tool workspace: give the output a NEW relative name)"
)

#: Read/compute verbs, change verbs, whether the change is IN PLACE, and the
#: tool NAMES the two prose fields spell out.
#:
#: ONE table, keyed on the same lowercased suffix ``readers.extract_text``
#: dispatches on, and the families it does not spell out are imported FROM
#: ``readers`` (the ``batch.py`` / ``ocr.py`` precedent) rather than re-listed —
#: a second list here would drift the first time the reader learns a format.
#:
#: THE THIRD FIELD IS THE HONESTY BIT, and it is not cosmetic. Writes are
#: confined by ``tools.base.safe_path`` to the turn's workspace, but the tools
#: split cleanly in two:
#:   * IN PLACE (True) — the tool re-saves the SOURCE, so the source itself must
#:     sit inside the workspace: ``excel_edit``/``excel_apply_spec`` resolve
#:     their ``path`` through ``safe_path`` and refuse an absolute path outside
#:     it with "escapes the session workspace" (measured).
#:   * DERIVED (False) — the tool reads ANY policy-allowed source and writes a
#:     NEW file into the workspace: ``pdf_arrange``/``pdf_split``
#:     (``pdf_tools.py``: "Source PDF (absolute, or workspace-relative)"),
#:     ``convert_document``, ``write_document``, ``image_convert``/
#:     ``image_resize``. The SOURCE is reachable from anywhere; the TARGET is
#:     not, which is why every derived entry carries :data:`_DERIVED_TARGET`.
#:
#: THE LAST TWO FIELDS ARE THE ARMING KEY, and they are TWO because arming a
#: reader and arming a mutator are different questions with different answers.
#: The prompt line and the turn's ``tool_specs`` are still built from this ONE
#: table (``live_tool_names`` / ``change_verbs_wanted`` →
#: ``chat_turn._resolve_armed_tools``), so the block can no longer name a verb
#: that is missing from the model's tool list — the "prompt claims a runnable
#: tool the model cannot call" failure ``chat_turn._write_directive`` already
#: guards against in the other direction. A test asserts prose and names agree
#: and that every name is a REGISTERED tool.
#:
#:   * ``read_tools`` arm on the attachment's TYPE ALONE. That is this wave's
#:     whole point and its measured repair (the live ledger: ``read_document``
#:     ran 96 times, all eight ``excel_*`` tools ZERO, because the model was
#:     handed a bare filename it could not open). Every member is
#:     ``Reversibility.READONLY`` — asserted, not assumed — which is what makes
#:     "the user attached it, so they want it read" a complete argument.
#:   * ``change_tools`` arm only for a request that ASKS for a change
#:     (:func:`change_verbs_wanted`). Attaching a workbook is not consent to
#:     rewrite it, and in this app an armed name becomes a permission "allow"
#:     override for the turn, so the tool would run with no approval card.
#: THE CHANGE PROSE IS PER VERB, and that is a repair too. It used to be one
#: string ("excel_edit, excel_apply_spec"), which is fine while the whole set
#: arms together and WRONG the moment they do not: measured on
#: ``"update cell B2 to 500"`` + a workbook, the gate arms ``excel_edit`` only,
#: and a single-string change clause went on naming ``excel_apply_spec`` to a
#: model that could not call it. ``change_phrases`` holds one phrase per
#: ``change_tools`` entry, SAME ORDER, so :func:`change_prose` can render
#: exactly the armed subset; ``change_note`` is the trailing clause that
#: qualifies whichever of them survive (:data:`_DERIVED_TARGET`), and it is a
#: separate field because it belongs to the group, not to any one verb.
class LiveVerbs(NamedTuple):
    read: str
    change_phrases: tuple[str, ...]
    change_note: str
    in_place: bool
    read_tools: tuple[str, ...]
    change_tools: tuple[str, ...]

    @property
    def tools(self) -> tuple[str, ...]:
        """Every name the table holds for this type — what the PROSE covers.

        A property, not a field, so the two halves can never disagree with the
        whole. It is NOT the arming key any more and no caller should treat it
        as one: arming a change verb from here is exactly the consent widening
        the split exists to remove.
        """
        return self.read_tools + self.change_tools

    @property
    def change(self) -> str:
        """The FULL change clause — every phrase this type has.

        What the TABLE can say, not what a turn does say: a turn renders only
        the armed subset (:func:`change_prose`). Kept as a property so drift
        checks ("every armed name is named in the prose") have one string to
        scan, and so no caller can accidentally build the full clause by hand.
        """
        return change_prose(self, self.change_tools)


def change_prose(verbs: LiveVerbs, allow: "set[str] | frozenset[str] | tuple[str, ...] | list[str]") -> str:
    """*verbs*' change clause, restricted to the tools in *allow*.

    Empty when nothing is allowed — including the trailing note, which only
    exists to say where an armed verb's OUTPUT may land and has no addressee
    when none is armed.
    """
    keep = [
        phrase
        for phrase, tool in zip(verbs.change_phrases, verbs.change_tools)
        if tool in allow
    ]
    return (", ".join(keep) + verbs.change_note) if keep else ""


_WORKBOOK = LiveVerbs(
    "excel_profile, excel_query, excel_read",
    ("excel_edit", "excel_apply_spec"),
    "",
    True,
    ("excel_profile", "excel_query", "excel_read"),
    ("excel_edit", "excel_apply_spec"),
)
_GENERIC = LiveVerbs(
    "read_document",
    ("convert_document", "write_document"),
    _DERIVED_TARGET,
    False,
    ("read_document",),
    ("convert_document", "write_document"),
)

LIVE_VERBS: dict[str, LiveVerbs] = {
    ".xlsx": _WORKBOOK,
    ".xlsm": _WORKBOOK,
    ".csv": LiveVerbs(
        "read_document",
        ("convert_document (to .xlsx)", "write_document"),
        _DERIVED_TARGET,
        False,
        ("read_document",),
        ("convert_document", "write_document"),
    ),
    ".pdf": LiveVerbs(
        "read_document with page_range, extract_pdf",
        ("pdf_arrange", "pdf_split"),
        _DERIVED_TARGET,
        False,
        ("read_document", "extract_pdf"),
        ("pdf_arrange", "pdf_split"),
    ),
    ".docx": _GENERIC,
    ".pptx": LiveVerbs(
        "read_document with page_range",
        ("convert_document", "write_document"),
        _DERIVED_TARGET,
        False,
        ("read_document",),
        ("convert_document", "write_document"),
    ),
}

#: Raster images that took the DOCUMENT path (a ``.bmp`` is outside
#: ``_ATTACH_IMAGE_TYPES`` and is transcribed rather than sent to vision — see
#: ``_prepare_attachments``). Their verbs live in ``tools/images.py``.
_IMAGE_VERBS = LiveVerbs(
    "view_image, image_info",
    ("image_convert", "image_resize"),
    _DERIVED_TARGET,
    False,
    ("view_image", "image_info"),
    ("image_convert", "image_resize"),
)

#: The same entry with ``view_image`` withdrawn, for a turn that HAS NO VISION.
#: ``view_image`` routes through the very router the note printed one line above
#: ("no model is wired to transcribe it" / "connect a vision-capable model and
#: retry") just failed on, and returns ``images._NO_VISION_ERROR`` — so naming
#: it there told the model to retry the thing that cannot work. The withdrawal
#: is DISCLOSED rather than silent (the repo's central rule): the remaining
#: verbs are Pillow-local and genuinely run with no model at all.
#: (``read_tools``, not ``tools`` — the latter is a computed property now, and
#: ``_replace(tools=...)`` would raise. The withdrawal only ever concerned a
#: READ verb, so this is the field it always meant.)
_IMAGE_VERBS_BLIND = _IMAGE_VERBS._replace(
    read="image_info (view_image needs a vision model; none is connected)",
    read_tools=tuple(t for t in _IMAGE_VERBS.read_tools if t != "view_image"),
)


def live_verbs_for(suffix: str, *, vision: bool = True) -> "LiveVerbs | None":
    """``(read, change, in_place, tools)`` for *suffix*, or ``None``.

    ``None`` means "we have nothing TRUE to say about this type" — an archive,
    a binary blob, anything the readers do not claim. Naming ``read_document``
    for it would be the same class of lie this whole function exists to fix.

    ``vision=False`` = "this turn provably has no vision-capable route", which
    only changes the answer for image types (see :data:`_IMAGE_VERBS_BLIND`).
    """
    s = (suffix or "").lower()
    if s in LIVE_VERBS:
        return LIVE_VERBS[s]
    try:
        # Same-package private import, as ``batch.py``/``ocr.py`` already do:
        # ONE definition of which suffixes exist, in readers.py.
        from .readers import SUPPORTED_READ, _IMAGE_SUFFIXES
    except Exception:  # noqa: BLE001 — a describable file must never fail here
        return None
    if s in _IMAGE_SUFFIXES:  # checked FIRST: images are a subset of SUPPORTED_READ
        return _IMAGE_VERBS if vision else _IMAGE_VERBS_BLIND
    return _GENERIC if s in SUPPORTED_READ else None


def live_tool_names(suffix: str, *, kind: str = "all") -> list[str]:
    """The tools the live-file line names for *suffix*.

    ``kind="read"`` is THE TYPE-ALONE ARMING KEY — the half a turn may arm just
    because a file of this type is attached. ``kind="change"`` is the half that
    additionally needs intent (:func:`change_verbs_wanted`); ``"all"`` is what
    the PROSE covers and is for tests and drift checks, never for arming.

    Read off the same tuples the prose is read off, so "what the prompt
    promises" and "what the turn arms" cannot drift apart. Empty for a type we
    say nothing about. NEVER RAISES: arming is a best-effort improvement, and a
    turn must survive a file whose suffix nobody recognises.
    """
    try:
        verbs = live_verbs_for(suffix)
        if verbs is None:
            return []
        if kind == "read":
            return list(verbs.read_tools)
        if kind == "change":
            return list(verbs.change_tools)
        return list(verbs.tools)
    except Exception:  # noqa: BLE001
        return []


def change_verbs_wanted(
    suffix: str,
    request: str = "",
    *,
    attachments: "list[str] | None" = None,
    explicit: "set[str] | frozenset[str] | tuple[str, ...] | list[str]" = (),
    auto: bool = True,
) -> list[str]:
    """The CHANGE verbs for *suffix* that this turn is allowed to arm.

    THE CONSENT GATE, and the one place that decides it. Two ways in, both of
    them the user's own act:

    * the user picked the tool from chat's "+" menu (*explicit*) — the
      interactive consent the permission engine's session grant is built on;
    * Auto is on and the REQUEST asks for that change.

    "Asks for that change" is answered by ``tools.autoselect.select_auto_tools``
    and by nothing written here. That module is already the deterministic,
    offline, regex intent scorer for this app, it already gets this right at its
    own layer (measured: ``"update cell B2 to 500"`` scores ``excel_edit``;
    ``"can you take a look at this?"`` does not), and a second detector for one
    rule is the drift this repo keeps paying for. The gate is PER VERB rather
    than a boolean "some change was asked for": a request for a summary memo
    scores ``write_document``, and letting that arm ``excel_edit`` on an
    attached workbook would re-open the widening from the other side.

    THE SCORER IS RUN UNCAPPED. ``select_auto_tools``'s ``cap`` is a DISPLAY
    budget (6 slots in a tool list), not a verdict about intent, and truncation
    is ranked — so at ``cap=6`` a verb the sentence genuinely scores can be
    pushed off by unrelated tools. Measured: "apply the firm's standard layout
    to this spreadsheet" scores ``excel_edit`` AND ``excel_apply_spec``, but at
    ``cap=6`` ``excel_edit`` falls off the list. The gate asks "did the request
    score this verb", so it must see the whole ranking; the 6-tool cap still
    applies where it belongs, in ``chat_turn._resolve_armed_tools``.

    NEVER RAISES — same contract as :func:`live_tool_names`. A gate that threw
    would take the turn down with it, and a gate that failed OPEN would be the
    defect it exists to prevent, so the failure direction is CLOSED.
    """
    try:
        verbs = live_verbs_for(suffix)
    except Exception:  # noqa: BLE001
        return []
    if verbs is None or not verbs.change_tools:
        return []
    picked = set(explicit or ())
    wanted: set[str] = set()
    if auto:
        try:
            from ..tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools

            wanted = set(
                select_auto_tools(
                    request or "",
                    attachments=list(attachments or []),
                    # Every candidate comes from AUTO_SAFE_TOOLS, so this cap
                    # provably truncates nothing and stays right as that set
                    # grows — see "THE SCORER IS RUN UNCAPPED" above.
                    cap=len(AUTO_SAFE_TOOLS),
                )
            )
        except Exception:  # noqa: BLE001 — fail CLOSED
            wanted = set()
    return [t for t in verbs.change_tools if t in picked or t in wanted]


def _inside(child: str, parent: str) -> bool:
    """Whether *child* sits under *parent*, decided WITHOUT touching the disk.

    ``normcase`` because this runs on Windows against two paths that reach us
    from different places (``config.home`` vs. a project root the user typed),
    so a drive-letter or case difference is routine. Pure string work on
    purpose: this is called from the event loop, and ``Path.resolve()`` stats
    (the v1.153.1 rule).
    """
    try:
        c = os.path.normcase(os.path.abspath(child))
        p = os.path.normcase(os.path.abspath(parent))
    except Exception:  # noqa: BLE001
        return False
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


#: What the line says INSTEAD of naming change verbs, when this turn armed none.
#:
#: THE PROSE IS A SEPARATE QUESTION FROM THE ARMING, and it was decided rather
#: than defaulted. A verb named but not armed cannot be called: strict providers
#: constrain ``tool_use`` to the supplied tool list, so the only thing naming it
#: can produce is a model that TELLS THE USER the file is editable when nothing
#: armed the editor. That is the "prompt claims a runnable tool the model cannot
#: call" lie ``chat_turn._write_directive`` already refuses to tell, and this
#: module's own invariant ("every verb it names is a verb the turn actually
#: ARMS") is worth more kept TOTAL than kept with an exception — one test can
#: check a total rule, and "except when..." is where the next reader gets it
#: wrong.
#:
#: SO: NO TOOL NAME, BUT NOT SILENCE EITHER. Deleting the clause outright would
#: hide a real capability from the model — under-exposure being the exact
#: failure this whole wave exists to remove — so the FACT survives without the
#: names. What the model can act on is not the string ``excel_edit`` (it cannot
#: emit it) but the knowledge that a change is possible and what unlocks it.
#:
#: "only when" IS LOAD-BEARING and is a NECESSARY condition, deliberately not a
#: sufficient one. Asking for a change is required; it is not always enough,
#: because the shared scorer has measured gaps ("turn this into a pdf",
#: "add a column for the tax rate" score no change verb — see
#: ``tests/test_attachment_handoff_v1196.py``, which pins that the block REPORTS
#: the unarmed state in exactly those cases rather than pretending). A clause
#: promising "ask and it arms" would be the silent over-claim this repo bans.
#:
#: "Offer one; do not claim one" is the half that answers the USER-facing half
#: of the rule. The model is the only thing standing between this block and the
#: user, so "you cannot do it" without "you may say so" produces either silence
#: about a real capability or — the failure that matters — a reply reporting an
#: edit that never happened (CLAUDE.md's central rule, and the reason
#: ``_claimed_write_note`` exists).
#:
#: 29 tokens (``context.budget.estimate_tokens``), against 12 for the workbook
#: change clause it replaces and 31-37 for the derived ones — so it is roughly
#: free on a ``.xlsx`` and SAVES tokens on a ``.pdf``/``.docx``/``.csv``, where
#: the :data:`_DERIVED_TARGET` clause it drops has no addressee anyway. Metered
#: rather than asserted small: it rides EVERY read-only attachment turn and both
#: chat lanes budget history (CLAUDE.md: "History is BUDGETED, never sliced").
_CHANGE_UNARMED = (
    " No change tool is armed: they arm only when the request asks for a"
    " change. Offer one; do not claim one."
)


def live_file_line(
    path: "str | Path",
    *,
    workspace: "str | Path | None" = None,
    rendered: bool = True,
    vision: bool = True,
    remind: bool = True,
    change: "list[str] | tuple[str, ...] | bool | None" = None,
) -> str:
    """The one line that makes an attachment REACHABLE by the document tools.

    Three things, in a few dozen tokens, because this lands in the system prompt
    of every turn carrying an attachment and both chat lanes budget history
    (CLAUDE.md: "History is BUDGETED, never sliced"):

    * the ABSOLUTE path — the only form that resolves from BOTH tool
      workspaces (the grounded project root and the uploads fallback);
    * the verbs for its TYPE, including the MUTATING ones;
    * WHERE THE CHANGE CAN LAND. Both halves of that, because the tools split
      two ways and both halves were measured:
        - an IN-PLACE verb (``excel_edit``) re-saves the SOURCE, so it reaches
          the file only if the file is inside the workspace;
        - a DERIVED verb (``convert_document``/``write_document``/
          ``pdf_arrange``) reads any source but resolves its TARGET through
          ``safe_path``, so an absolute target outside the workspace is refused
          just as hard (:data:`_DERIVED_TARGET`).
      Either way the line says so BEFORE the model tries, instead of leaving it
      to relay a "escapes the session workspace" refusal — or, worse, to report
      an edit that never happened (the repo's central rule).

    ``rendered`` = "there is extracted text above this line"; the reminder that
    the text is a flattening and not the file is dropped when there is none.
    ``remind`` = "this block has not said that yet" — the sentence is a
    property of the BLOCK, not of the file, so a turn carrying six attachments
    pays for it once instead of six times (measured: ~11 tokens x N-1). The
    ``LIVE FILE`` marker still leads EVERY line, so the file/rendering
    distinction is never dropped, only its elaboration.
    ``vision`` = "a vision-capable route exists this turn"; see
    :data:`_IMAGE_VERBS_BLIND`.
    ``change`` = THE CHANGE VERBS THIS TURN ACTUALLY ARMED, from
    :func:`change_verbs_wanted`. It is a LIST rather than a flag because the
    gate is per verb and so the prose must be: ``"update cell B2 to 500"`` arms
    ``excel_edit`` and not ``excel_apply_spec``, and a clause naming both would
    promise a tool the model cannot call. An EMPTY list replaces the whole
    clause — verbs, target rule and confinement warning alike — with
    :data:`_CHANGE_UNARMED`; none of the three has an addressee when the model
    holds no tool that can write.

    ``None`` means "this caller did not consult the gate" and renders every
    change verb the type has. It exists for direct/descriptive use (docs, tests)
    and is deliberately NOT what either chat lane passes — a
    lane that let it default to ``None`` would be back to arming-by-file-type.
    ``tests/test_attachment_handoff_v1196.py`` asserts both lanes pass it.

    NEVER RAISES. An attachment we cannot describe still gets its excerpt.
    """
    try:
        p = Path(path)
        abs_path = str(p if p.is_absolute() else Path(os.path.abspath(p)))
        verbs = live_verbs_for(p.suffix, vision=vision)
        if verbs is None:
            # Honest floor: the path is still worth handing over (the user can
            # be told where it is), but no tool is promised.
            return f"\n(The file itself is on disk at {abs_path}.)"
        read_verbs, in_place = verbs.read, verbs.in_place
        # A BOOL IS ACCEPTED ON PURPOSE. This function never raises, so a caller
        # that passed `change=True` would otherwise hit `tuple(True)`, land in
        # the blanket `except` below and get an EMPTY handoff — the wave's own
        # defect, restored silently by a type slip. Caught by this file's own
        # tests doing exactly that. `True`/`None` = every change verb the type
        # has; `False` = none.
        if change is None or change is True:
            allow: tuple[str, ...] = verbs.change_tools
        elif change is False:
            allow = ()
        else:
            allow = tuple(change)
        change_verbs = change_prose(verbs, allow)
        if not verbs.change_tools:
            # Nothing true to say about changing this type at all.
            tail = ""
        elif not change_verbs:
            tail = _CHANGE_UNARMED
        elif not in_place or (
            workspace is not None and _inside(abs_path, str(workspace))
        ):
            # Reachable either way: a DERIVED write takes any source (and
            # `change_verbs` already carries where its OUTPUT must go), and an
            # in-place one whose source we VERIFIED is inside the workspace.
            tail = f" Change it: {change_verbs}."
        elif workspace is None:
            # Nobody told us where the tools run, so nothing is claimed either
            # way — the confinement is stated as the rule it is.
            tail = (
                f" Change it: {change_verbs} (in-place edits reach only files"
                " inside your tool workspace)."
            )
        else:
            tail = (
                f" In-place edits ({change_verbs}) reach ONLY your tool"
                " workspace and this file is outside it — write changes as a"
                " NEW file there and say so."
            )
        return (
            f"\n(LIVE FILE — {abs_path}. Work on it directly: {read_verbs}."
            + tail
            + (" The text above is a flat rendering, not the file.)"
               if rendered and remind else ")")
        )
    except Exception:  # noqa: BLE001 — a description must never break a turn
        return ""
