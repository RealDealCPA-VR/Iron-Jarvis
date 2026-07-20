"""OCR fallback for SCANNED PDFs (image-only pages, no text layer).

A scanned PDF — a death certificate, a signed engagement letter, a W-2 photo —
extracts to EMPTY text: ``extract_text`` reads the PDF's text layer and an
image-only page has none. The office answer used to be silence ("no
extractable text"). This module recovers the text by pulling each page's
embedded scan image (``pypdf`` ``page.images`` — no PDF rasterizer needed;
a scanned page is one big embedded image) and transcribing it with the
current vision-capable model through the router.

Honest by construction:

* the result NAMES the method ("recovered via OCR") and the page cap;
* the offline mock must never fabricate a legal document's contents — a
  route that resolves to the mock is treated as "no transcription" with a
  clear note, never as text;
* with no vision-capable provider the caller gets an explanation instead of
  empty silence.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

#: Pages transcribed at most per document — each page is one vision call.
MAX_OCR_PAGES = 10
#: A PDF whose ENTIRE text layer is under this many characters is effectively
#: image-only (real text PDFs clear this on the first line of page one).
_SCANNED_TEXT_THRESHOLD = 80
#: Mirror view_image's provider payload cap (base64 inflates ~33%).
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

_OCR_SYSTEM = "You transcribe scanned documents verbatim."
_OCR_PROMPT = (
    "This is a scanned document page. Transcribe ALL text on it verbatim as "
    "plain text, reading order top to bottom. Preserve line breaks so the "
    "layout stays readable. Output ONLY the transcription — no commentary, "
    "no summaries."
)


def looks_scanned_pdf(path: Path, extracted_text: str) -> bool:
    """True when *path* is a PDF whose text layer is effectively empty AND
    whose first page carries an embedded image (the scan). Both signals are
    required: a short-but-real digital PDF ("Invoice #1") has little text but
    no page image, and must never be mislabeled as scanned."""
    if path.suffix.lower() != ".pdf":
        return False
    if len((extracted_text or "").strip()) >= _SCANNED_TEXT_THRESHOLD:
        return False
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        first = reader.pages[0] if reader.pages else None
        return bool(first is not None and list(first.images))
    except Exception:  # noqa: BLE001 — unreadable structure = not OCR-able anyway
        return False


def pdf_page_scan_images(
    path: Path, *, max_pages: int = MAX_OCR_PAGES
) -> "tuple[list[bytes], int]":
    """The LARGEST embedded image per page (a scan page is one big image),
    re-encoded as JPEG under the vision payload cap. Returns ``(blobs,
    total_pages)``; an empty list = nothing OCR could work on (vector-only,
    encrypted, or no embedded images)."""
    from PIL import Image
    from pypdf import PdfReader

    blobs: list[bytes] = []
    reader = PdfReader(str(path))
    total = len(reader.pages)
    for page in reader.pages[:max_pages]:
        best: bytes | None = None
        try:
            page_images = list(page.images)
        except Exception:  # noqa: BLE001 — a malformed page is skipped, not fatal
            page_images = []
        for img in page_images:
            data = getattr(img, "data", None)
            if data and (best is None or len(data) > len(best)):
                best = data
        if not best:
            continue
        try:
            with Image.open(io.BytesIO(best)) as im:
                rgb = im.convert("RGB")
                while True:
                    buf = io.BytesIO()
                    rgb.save(buf, format="JPEG", quality=85)
                    blob = buf.getvalue()
                    if len(blob) <= _MAX_IMAGE_BYTES or min(rgb.size) < 512:
                        break
                    rgb = rgb.resize((rgb.width // 2, rgb.height // 2))
            blobs.append(blob)
        except Exception:  # noqa: BLE001 — an undecodable image is skipped
            continue
    return blobs, total


async def ocr_pdf(
    path: Path, router: Any, *, max_pages: int = MAX_OCR_PAGES
) -> "tuple[str, str]":
    """Transcribe up to *max_pages* scanned pages via the router's vision path.

    Returns ``(text, note)``. ``text == ""`` means nothing was recovered and
    the note says why — the caller shows the note either way, so the user
    always learns HOW their text was (or wasn't) produced."""
    from ..providers.adapters.base import LLMMessage

    blobs, total = pdf_page_scan_images(path, max_pages=max_pages)
    if not blobs:
        return "", (
            "scanned/image-only PDF with no readable embedded page images — "
            "there is no text layer, and nothing OCR could work on"
        )
    pages: list[str] = []
    for i, blob in enumerate(blobs, start=1):
        msg = LLMMessage(
            role="user",
            content=_OCR_PROMPT,
            images=[
                {
                    "data_b64": base64.b64encode(blob).decode("ascii"),
                    "media_type": "image/jpeg",
                }
            ],
        )
        try:
            route = await router.complete(
                system=_OCR_SYSTEM, messages=[msg], tools=[], task_class="ocr"
            )
        except Exception as exc:  # noqa: BLE001 — a provider fault ends OCR honestly
            if pages:
                break  # keep what was already transcribed; the note discloses
            return "", (
                "scanned PDF — OCR needs a vision-capable model and the current "
                f"one failed ({type(exc).__name__}: {exc}); connect a vision "
                "model (Anthropic/Google, or a local llava/qwen-VL) and retry"
            )
        # NEVER let the offline mock fabricate a legal document's contents.
        if getattr(route, "provider", "") == "mock":
            return "", (
                "scanned PDF — only the offline mock model is connected, and "
                "fabricated OCR is worse than none; connect a vision-capable "
                "model and retry"
            )
        text = (route.response.text or "").strip()
        if text:
            pages.append(f"[page {i}]\n{text}")
    if not pages:
        return "", (
            "scanned PDF — the current model returned no transcription; it may "
            "not support vision (connect a vision-capable model and retry)"
        )
    capped = total > len(blobs)
    note = (
        f"scanned PDF — text recovered via OCR ({len(pages)} of {total} "
        f"page(s) transcribed"
        + (f"; only the first {max_pages} pages are attempted" if capped else "")
        + ")"
    )
    return "\n\n".join(pages), note
