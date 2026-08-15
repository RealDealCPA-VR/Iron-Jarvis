"""Per-PAGE scan routing for PDFs (v1.176.0).

THE BLINDNESS THIS FIXES, reproduced before it was written. v1.174.0 taught
every document path to OCR a scan, but it asks ONE question of the WHOLE file:
:func:`~iron_jarvis.documents.ocr.looks_scanned_pdf` returns False as soon as
the document's total text layer clears 80 characters, and it only checks page
ONE for an embedded image. So the shape that actually lands on this desk — a
20-page 1120-S that is native text with a SCANNED K-1 (or 8879, or engagement
letter) stapled in at page 12 — fails both tests at once. Measured on a built
fixture:

    extracted text chars : 3178
    needs_ocr            : False        <- the scanned page is INVISIBLE
    pdf-inspector        : mixed, pages_needing_ocr=[2], in 1.5 ms

No error, no note: the model simply answers about a return whose K-1 it never
saw. That is v1.174.0's own bug one level down — "part of this file is a scan"
rather than "this file is a scan".

``pdf-inspector`` (MIT, Firecrawl) answers exactly that question and nothing
else: a Rust classifier over the content streams, no model and no network, that
returns a type plus the 0-indexed pages whose content is image-only. This
module is the seam. It exists so the rest of the app never imports the library
directly and never has to think about it being absent.

THE CONTRACT — the library is an OPTIONAL ACCELERANT, never a dependency the
document pipeline's correctness rests on:

* every entry point returns ``None`` instead of raising, for ANY reason (not
  installed, unreadable file, encrypted PDF, a panic in the extension);
* ``None`` means "I have nothing to add", and the caller falls back to the
  v1.174.0 whole-document heuristic — which is strictly better than nothing and
  is what shipped for a year;
* a classification is only ever allowed to ADD pages to the OCR plan. It can
  say "also read page 12"; it can never say "skip the OCR you were going to
  do". A wrong classifier must not be able to make the app read LESS of a
  client's document than it does today.

Blocking (it parses the file), so callers run it through ``asyncio.to_thread``
— the v1.153.1 rule. It is fast (single-digit ms on ordinary files) but speed
is not a licence to sit on the event loop: page count is unbounded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: PDF types the library reports. ``mixed`` is the one that matters here — it is
#: precisely the case the whole-document heuristic cannot see.
TYPE_TEXT = "text_based"
TYPE_SCANNED = "scanned"
TYPE_IMAGE = "image_based"
TYPE_MIXED = "mixed"

#: Types whose pages we trust as genuinely needing OCR.
_SCAN_TYPES = frozenset({TYPE_SCANNED, TYPE_IMAGE, TYPE_MIXED})

#: Refuse to route more pages than this from one document. The OCR page cap
#: bounds what is SPENT; this bounds what a malformed/hostile file can make us
#: hold in memory while planning.
_MAX_ROUTED_PAGES = 10_000


@dataclass(frozen=True)
class PdfScanPlan:
    """What the classifier saw. ``ocr_pages`` is 0-indexed and ascending."""

    pdf_type: str
    confidence: float
    page_count: int
    ocr_pages: tuple[int, ...]

    @property
    def is_mixed(self) -> bool:
        """Some pages carry real text and some are scans — the case the
        whole-document heuristic is blind to."""
        return self.pdf_type == TYPE_MIXED

    @property
    def wholly_scanned(self) -> bool:
        """Every page is a scan (what v1.174.0 already handled)."""
        return self.pdf_type in {TYPE_SCANNED, TYPE_IMAGE}


def available() -> bool:
    """Whether the classifier can be used at all on this install.

    Cheap and honest: used by diagnostics so a packaged build that dropped the
    native extension can SAY so, rather than silently degrading to the old
    whole-document behaviour forever (the pikepdf lesson).
    """
    try:
        import pdf_inspector  # noqa: F401
    except Exception:  # noqa: BLE001 — a broken extension is "not available"
        return False
    return True


def classify(path: "str | Path") -> "PdfScanPlan | None":
    """Classify *path*, or ``None`` when the classifier has nothing to offer.

    NEVER raises. A PDF this cannot parse is one the caller handles exactly as
    it did before this module existed.
    """
    try:
        import pdf_inspector
    except Exception:  # noqa: BLE001 — not installed / native ext missing
        return None
    try:
        result = pdf_inspector.classify_pdf(str(path))
    except Exception as exc:  # noqa: BLE001 — encrypted, malformed, or a panic
        # DEBUG, not WARNING: an unparseable PDF is an ordinary event on a real
        # document folder, and the caller degrades cleanly. A warning per file
        # would train the operator to ignore the log.
        logger.debug("pdf classification failed for %s: %s", path, exc)
        return None
    try:
        pdf_type = str(getattr(result, "pdf_type", "") or "").strip().lower()
        page_count = int(getattr(result, "page_count", 0) or 0)
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        raw_pages = list(getattr(result, "pages_needing_ocr", None) or [])[
            :_MAX_ROUTED_PAGES
        ]
    except Exception:  # noqa: BLE001 — an unexpected result shape is a miss
        return None
    # Keep only sane, in-range page indices. A classifier that returns page 900
    # of a 3-page file is wrong about something; drop the entry rather than let
    # it become an out-of-range read downstream.
    pages = sorted(
        {
            p
            for p in (_as_index(v) for v in raw_pages)
            if p is not None and (page_count <= 0 or p < page_count)
        }
    )
    return PdfScanPlan(
        pdf_type=pdf_type,
        confidence=confidence,
        page_count=page_count,
        ocr_pages=tuple(pages),
    )


def _as_index(value: object) -> "int | None":
    try:
        idx = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return idx if idx >= 0 else None


def scan_pages(path: "str | Path") -> "tuple[int, ...] | None":
    """The 0-indexed pages of *path* that need OCR, or ``None`` when unknown.

    ``()`` (empty tuple) is a real answer meaning "this file is fully readable
    text" — distinct from ``None``, "I could not tell". Callers must keep those
    apart: only ``None`` may fall back to the old heuristic, and only ``()``
    means the classifier positively cleared the file.

    Pages are reported only for types that genuinely indicate scans. A
    ``text_based`` verdict never contributes pages even if the library listed
    some, because for a text PDF those entries mean "this page had an encoding
    oddity", not "this page is a picture" — spending vision calls on them would
    be the expensive half of a false positive.
    """
    plan = classify(path)
    if plan is None:
        return None
    if plan.pdf_type not in _SCAN_TYPES:
        return ()
    return plan.ocr_pages
