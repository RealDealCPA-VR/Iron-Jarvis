"""OCR for SCANNED documents — image-only PDFs and raster images (v1.174.0).

A scanned PDF — a death certificate, a signed engagement letter, a W-2 photo —
extracts to EMPTY text: ``extract_text`` reads the PDF's text layer and an
image-only page has none. A ``.png``/``.jpg`` is worse than empty: it extracts
to ``"[image PNG 800x600, mode RGB]"``, a size note a fact-extraction model
will happily treat as the document's contents. This module recovers the real
text by pulling each page's embedded scan image (``pypdf`` ``page.images`` — no
PDF rasterizer needed; a scanned page is one big embedded image), or the image
file itself, and transcribing it with the current vision-capable model through
the router.

WHY THIS MODULE GREW (v1.174.0): on a real job — "rename all files in this
folder to something more appropriate" over 26 tax documents — ELEVEN of the 22
PDFs were image-only scans. ``extract_pdf`` returned silence, so the agent
retried each one with ``read_document``, burned its whole step budget
compensating for a missing capability, and renamed nothing. OCR now reaches
every document path through :func:`ocr_if_unreadable`, and every transcription
is CACHED by content hash (:func:`ocr_document`) so no scan is ever paid for
twice — one page took >180s live, and that folder holds eleven of them.

Honest by construction:

* the result NAMES the method ("recovered via OCR") and the page cap;
* the offline mock must never fabricate a legal document's contents — a
  route that resolves to the mock is treated as "no transcription" with a
  clear note, never as text;
* with no vision-capable provider the caller gets an explanation instead of
  empty silence;
* a FAILED transcription is never cached — only real recovered text is, so a
  transient provider outage can never freeze into a permanent empty answer.

Bounded by construction: one vision call PER PAGE, capped by
``config.ocr_max_pages`` (default 10, hard ceiling 50), skippable entirely with
``config.ocr_enabled = false``, and every blocking step (hashing, PDF parsing,
JPEG re-encoding, cache IO) runs through ``asyncio.to_thread`` — the v1.153.1
hard rule.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .readers import _IMAGE_SUFFIXES as IMAGE_SUFFIXES

#: Pages transcribed at most per document — each page is one vision call.
MAX_OCR_PAGES = 10
#: Hard ceiling on the configurable cap: a 400-page scan must not become 400
#: vision calls because a config file said so.
MAX_OCR_PAGES_CEILING = 50
#: A PDF whose ENTIRE text layer is under this many characters is effectively
#: image-only (real text PDFs clear this on the first line of page one).
_SCANNED_TEXT_THRESHOLD = 80
#: Mirror view_image's provider payload cap (base64 inflates ~33%).
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: The phrase every successful transcription note carries. It is a CONTRACT,
#: not decoration: :func:`needs_ocr` looks for it to tell "this text came out of
#: a scan we already transcribed" from "this scan still needs transcribing", so
#: a cached transcript can never trigger a second (paid) OCR pass.
OCR_MARK = "text recovered via OCR"

_OCR_SYSTEM = "You transcribe scanned documents verbatim."
_OCR_PROMPT = (
    "This is a scanned document page. Transcribe ALL text on it verbatim as "
    "plain text, reading order top to bottom. Preserve line breaks so the "
    "layout stays readable. Output ONLY the transcription — no commentary, "
    "no summaries."
)

_MOCK_NOTE = (
    "scanned document — only the offline mock model is connected, and "
    "fabricated OCR is worse than none; connect a vision-capable model and retry"
)
_DISABLED_NOTE = (
    "scanned document — OCR is turned off (settings: ocr_enabled = false), so "
    "this file's text was NOT recovered; nothing here was read"
)


# --------------------------------------------------------------- settings ---


def ocr_settings(config: Any) -> "tuple[bool, int]":
    """``(enabled, max_pages)`` from a live Config — tolerant of ``None``.

    Tools receive ``ctx.config`` which is ``None`` in plenty of unit tests and
    in the bare ``document_tools()`` factory, so every lookup falls back to the
    module defaults rather than raising. The page cap is clamped to
    ``1..MAX_OCR_PAGES_CEILING``: this number is a SPEND (one vision call per
    page), so neither 0 nor 400 may come out of a config file.
    """
    enabled = getattr(config, "ocr_enabled", True)
    enabled = True if enabled is None else bool(enabled)
    try:
        pages = int(getattr(config, "ocr_max_pages", MAX_OCR_PAGES) or MAX_OCR_PAGES)
    except (TypeError, ValueError):
        pages = MAX_OCR_PAGES
    return enabled, max(1, min(pages, MAX_OCR_PAGES_CEILING))


def ocr_home(config: Any) -> "Path | None":
    """The state home the OCR cache lives under, or ``None`` when unknown."""
    home = getattr(config, "home", None)
    return Path(home) if home else None


# ------------------------------------------------------------- detection ---


def is_image(path: Path) -> bool:
    """True for a raster image file — it has no text layer, ever."""
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def _text_body(extracted_text: str) -> str:
    """The document's OWN text, with the reader's METADATA removed.

    ``extract_text`` can prepend a ``[NOTE: ...]`` line (a file whose extension
    lies about its contents) and returns a sentinel sentence for a PDF with no
    text layer. Both are the reader talking ABOUT the file, not text FROM it —
    counting them toward the "does this have a text layer?" threshold made a
    mislabeled scan look readable, which is precisely backwards.
    """
    from .readers import SCANNED_PDF_SENTINEL

    body = "\n".join(
        line
        for line in (extracted_text or "").splitlines()
        if not line.startswith("[NOTE:")
    )
    return body.replace(SCANNED_PDF_SENTINEL, "").strip()


def is_pdf_file(path: Path) -> bool:
    """PDF by suffix — or by CONTENT for a file whose extension lies.

    The acceptance folder holds a 71 KB PDF named ``...2025.xlsx``. Keying OCR
    on the suffix alone would leave exactly that file — a mislabeled scan — the
    one document in the folder nobody could read.
    """
    from .readers import _OOXML_SUFFIXES, _PDF_MAGIC, _head_bytes

    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return True
    if suffix in _OOXML_SUFFIXES:
        return _head_bytes(path, 5) == _PDF_MAGIC
    return False


def looks_scanned_pdf(path: Path, extracted_text: str) -> bool:
    """True when *path* is a PDF whose text layer is effectively empty AND
    whose first page carries an embedded image (the scan). Both signals are
    required: a short-but-real digital PDF ("Invoice #1") has little text but
    no page image, and must never be mislabeled as scanned."""
    path = Path(path)
    if not is_pdf_file(path):
        return False
    if len(_text_body(extracted_text)) >= _SCANNED_TEXT_THRESHOLD:
        return False
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        first = reader.pages[0] if reader.pages else None
        return bool(first is not None and list(first.images))
    except Exception:  # noqa: BLE001 — unreadable structure = not OCR-able anyway
        return False


def needs_ocr(path: Path, extracted_text: str) -> bool:
    """THE classifier every document path shares: would OCR add anything here?

    True for a raster image (never has a text layer) and for a PDF that
    :func:`looks_scanned_pdf` recognises. Text already carrying :data:`OCR_MARK`
    is a transcript we produced earlier — transcribing it again would spend a
    second round of vision calls for the same bytes, so it is False. Blocking
    (it parses the PDF), so callers run it off the event loop.
    """
    if OCR_MARK in (extracted_text or ""):
        return False
    path = Path(path)
    if is_image(path):
        return True
    return looks_scanned_pdf(path, extracted_text)


# ------------------------------------------------- cache (FROZEN CONTRACT 5) ---
#
# Keyed by (sha256 of the file BYTES, page cap) and persisted under
# <home>/ocr/, so the same scan is never transcribed twice — across sessions,
# across re-runs, across tools. Content-addressed rather than path-addressed on
# purpose: the acceptance job RENAMES the files it reads, and a path key would
# miss every one of them on the second pass.

#: Cache record schema version — a bump invalidates old records by construction.
_CACHE_VERSION = 1

#: Cache roots seen this process, most recent first. :func:`lookup_cached_text`
#: is SYNCHRONOUS (it serves ``extract_text``, which has no config in scope) and
#: needs somewhere to look. This list can only be non-empty once a real OCR call
#: has STORED or SERVED a transcription with a known home — which is also the
#: only way a cache entry can exist — so it is a memo of work already done,
#: never ambient configuration. A MISS deliberately does not arm it: arming on
#: an attempt made every later synchronous ``extract_text`` of an image or scan
#: pay a full-file sha256 plus a directory glob to discover the same nothing.
_CACHE_ROOTS: list[Path] = []
_MAX_CACHE_ROOTS = 4
#: This list is written from ``asyncio.to_thread`` workers AND from FastAPI's
#: sync-endpoint threadpool (``extract_text`` serves ``/documents/preview``,
#: project knowledge ingest, filesearch), so it is genuinely concurrent. The old
#: check-then-``remove`` let two threads both see a root and the loser raise
#: ``ValueError: list.remove(x): x not in list`` — which escaped ``store_cached``
#: and surfaced as "OCR fallback failed" on a transcription that had SUCCEEDED.
_CACHE_LOCK = threading.Lock()

#: Appended to a note when the transcription came out of the contract-5 cache —
#: i.e. when ZERO vision calls were made. Both lanes that can serve a cache hit
#: (:func:`ocr_document` and the readers' synchronous ``_cached_ocr_text``) use
#: THIS constant, so a cached read can never claim to be a fresh transcription
#: in one lane and disclose itself in the other. ``attachment_rag`` parses for
#: this exact phrase to charge a hit nothing.
CACHED_NOTE_SUFFIX = " [cached — already transcribed earlier]"


def cache_dir(home: "str | Path") -> Path:
    return Path(home) / "ocr"


def remember_cache_root(root: Path) -> None:
    """Record a cache root for the synchronous lookup (most recent first).

    REBUILT under a lock rather than mutated in place: a check-then-``remove``
    across two threads is a race whose loser raises, and nothing here is worth
    failing a successful transcription over.
    """
    global _CACHE_ROOTS

    root = Path(root)
    with _CACHE_LOCK:
        _CACHE_ROOTS = [root, *(r for r in _CACHE_ROOTS if r != root)][
            :_MAX_CACHE_ROOTS
        ]


def cache_roots() -> "list[Path]":
    """A stable snapshot of the remembered roots (never the live list)."""
    with _CACHE_LOCK:
        return list(_CACHE_ROOTS)


def file_digest(path: "str | Path") -> str:
    """Chunked sha256 of the file's bytes — the cache key's first half."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_name(digest: str, max_pages: int) -> str:
    return f"{digest}.p{int(max_pages)}.json"


def load_cached(home: "str | Path", digest: str, max_pages: int) -> "dict[str, Any] | None":
    """A prior transcription of these exact bytes at this exact page cap.

    A missing/corrupt/foreign-version record simply means "not cached" — the
    cache may never be the reason a document fails to read.
    """
    root = cache_dir(home)
    record_path = root / _record_name(digest, max_pages)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(record, dict)
        or record.get("version") != _CACHE_VERSION
        or not isinstance(record.get("text"), str)
        or not record["text"].strip()
    ):
        return None
    # Only a HIT arms the synchronous lookup: this root demonstrably holds
    # transcriptions, so the hashing the sync path pays can actually pay off.
    remember_cache_root(root)
    return record


def store_cached(
    home: "str | Path", digest: str, max_pages: int, text: str, note: str
) -> None:
    """Persist ONE successful transcription (sibling temp + ``os.replace``, the
    writers' atomic convention). Callers only reach here with real text — a
    failure is never cached, or a provider outage would freeze into a permanent
    "this scan is empty"."""
    if not (text or "").strip():
        return
    root = cache_dir(home)
    root.mkdir(parents=True, exist_ok=True)
    remember_cache_root(root)
    record_path = root / _record_name(digest, max_pages)
    tmp = record_path.with_name(f".{record_path.name}.tmp-{os.getpid()}")
    record = {
        "version": _CACHE_VERSION,
        "sha256": digest,
        "max_pages": int(max_pages),
        "text": text,
        "note": note,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, record_path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def lookup_cached_text(path: "str | Path") -> "tuple[str, str] | None":
    """``(text, note)`` from ANY earlier OCR of this exact file, or ``None``.

    Synchronous and config-free so ``extract_text`` — and therefore every caller
    that never learned about OCR, including ``read_file``'s office redirect —
    can serve a scan that some other path already transcribed. The widest page
    cap wins (it is the most complete transcript of the same bytes).
    """
    roots = cache_roots()
    if not roots:
        return None  # nothing has ever been transcribed: skip the hashing
    try:
        digest = file_digest(path)
    except OSError:
        return None
    best: "tuple[int, dict[str, Any]] | None" = None
    for root in roots:
        try:
            candidates = list(root.glob(f"{digest}.p*.json"))
        except OSError:  # pragma: no cover — an unreadable cache dir is a miss
            continue
        for candidate in candidates:
            try:
                record = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                not isinstance(record, dict)
                or record.get("version") != _CACHE_VERSION
                or not isinstance(record.get("text"), str)
                or not record["text"].strip()
            ):
                continue
            pages = int(record.get("max_pages") or 0)
            if best is None or pages > best[0]:
                best = (pages, record)
    if best is None:
        return None
    return best[1]["text"], str(best[1].get("note") or "")


# ------------------------------------------------------------ image harvest ---


def _encode_jpeg(data: bytes) -> "bytes | None":
    """Re-encode arbitrary image bytes as JPEG under the vision payload cap,
    halving the resolution until it fits. ``None`` = undecodable."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
            while True:
                buf = io.BytesIO()
                rgb.save(buf, format="JPEG", quality=85)
                blob = buf.getvalue()
                if len(blob) <= _MAX_IMAGE_BYTES or min(rgb.size) < 512:
                    return blob
                rgb = rgb.resize((rgb.width // 2, rgb.height // 2))
    except Exception:  # noqa: BLE001 — an undecodable image is skipped
        return None


def pdf_page_scan_images(
    path: Path, *, max_pages: int = MAX_OCR_PAGES
) -> "tuple[list[bytes], int]":
    """The LARGEST embedded image per page (a scan page is one big image),
    re-encoded as JPEG under the vision payload cap. Returns ``(blobs,
    total_pages)``; an empty list = nothing OCR could work on (vector-only,
    encrypted, or no embedded images)."""
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
        blob = _encode_jpeg(best)
        if blob is not None:
            blobs.append(blob)
    return blobs, total


def image_scan_blob(path: Path) -> "bytes | None":
    """The image FILE itself as a vision-ready JPEG, or ``None``."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return _encode_jpeg(data)


# ------------------------------------------------------------ transcription ---


def _vision_route_kwargs(router: Any, config: Any) -> "dict[str, Any]":
    """``model_roles["vision"]`` resolved to router kwargs, or ``{}``.

    OCR used to pass ``task_class="ocr"`` and nothing else, so a user who had
    pinned a vision model saw ``view_image`` use the pin while OCR quietly did
    not — the two vision consumers disagreeing about which model does vision.
    Same defensive shape as :mod:`.batch`: resolution failures fall back to the
    router's own default route, never to an exception.
    """
    if config is None:
        return {}
    try:
        from ..providers.roles import resolve_role  # lazy: no import cycle

        llm = resolve_role(
            config,
            getattr(router, "manager", None),
            "vision",
            fallback_provider=None,
            fallback_model=None,
        )
        if getattr(llm, "applied", False):
            return {"provider": llm.provider, "model": llm.model}
    except Exception:  # noqa: BLE001 — a role miss must never break OCR
        return {}
    return {}


async def _transcribe(
    blobs: "list[bytes]",
    router: Any,
    *,
    route_kwargs: "dict[str, Any]",
    label_pages: bool,
) -> "tuple[list[str], str]":
    """Transcribe each blob with one vision call. Returns ``(pages, fatal_note)``
    — a non-empty ``fatal_note`` means NOTHING may be reported as text (the mock
    guard, or a first-call failure). A failure AFTER some pages succeeded keeps
    what was transcribed; the caller's note discloses the page count."""
    from ..providers.adapters.base import LLMMessage

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
                system=_OCR_SYSTEM,
                messages=[msg],
                tools=[],
                task_class="ocr",
                **route_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — a provider fault ends OCR honestly
            if pages:
                break  # keep what was already transcribed; the note discloses
            return [], (
                "scanned document — OCR needs a vision-capable model and the "
                f"current one failed ({type(exc).__name__}: {exc}); connect a "
                "vision model (Anthropic/Google, or a local llava/qwen-VL) and "
                "retry"
            )
        # NEVER let the offline mock fabricate a legal document's contents.
        if getattr(route, "provider", "") == "mock":
            return [], _MOCK_NOTE
        text = (route.response.text or "").strip()
        if text:
            pages.append(f"[page {i}]\n{text}" if label_pages else text)
    return pages, ""


async def ocr_pdf(
    path: Path, router: Any, *, max_pages: int = MAX_OCR_PAGES, config: Any = None
) -> "tuple[str, str]":
    """Transcribe up to *max_pages* scanned pages via the router's vision path.

    Returns ``(text, note)``. ``text == ""`` means nothing was recovered and
    the note says why — the caller shows the note either way, so the user
    always learns HOW their text was (or wasn't) produced."""
    # pypdf parsing + Pillow re-encoding are CPU-bound and were running on the
    # event loop: a 10-page scan froze every request in the app (v1.153.1).
    blobs, total = await asyncio.to_thread(
        pdf_page_scan_images, Path(path), max_pages=max_pages
    )
    if not blobs:
        return "", (
            "scanned/image-only PDF with no readable embedded page images — "
            "there is no text layer, and nothing OCR could work on"
        )
    pages, fatal = await _transcribe(
        blobs,
        router,
        route_kwargs=_vision_route_kwargs(router, config),
        label_pages=True,
    )
    if fatal:
        return "", fatal
    if not pages:
        return "", (
            "scanned PDF — the current model returned no transcription; it may "
            "not support vision (connect a vision-capable model and retry)"
        )
    capped = total > len(blobs)
    note = (
        f"scanned PDF — {OCR_MARK} ({len(pages)} of {total} page(s) transcribed"
        + (f"; only the first {max_pages} pages are attempted" if capped else "")
        + ")"
    )
    return "\n\n".join(pages), note


async def ocr_image(
    path: Path, router: Any, *, config: Any = None
) -> "tuple[str, str]":
    """Transcribe a raster image file (a photographed/scanned page).

    Without this, EVERY document path served a ``.png`` scan as
    ``"[image PNG 800x600, mode RGB]"`` — and ``batch_documents`` fed that
    string to the extraction model as the document's content, which is worse
    than empty: it is an invitation to invent."""
    blob = await asyncio.to_thread(image_scan_blob, Path(path))
    if blob is None:
        return "", (
            "image file — the image could not be decoded, so there was nothing "
            "OCR could work on"
        )
    pages, fatal = await _transcribe(
        [blob],
        router,
        route_kwargs=_vision_route_kwargs(router, config),
        label_pages=False,
    )
    if fatal:
        return "", fatal
    if not pages:
        return "", (
            "image file — the current model returned no transcription; it may "
            "not support vision (connect a vision-capable model and retry)"
        )
    return pages[0], f"image file — {OCR_MARK} (vision transcription)"


# ------------------------------------------------------------- entry points ---


async def ocr_document(
    path: "str | Path",
    router: Any,
    *,
    config: Any = None,
    max_pages: "int | None" = None,
    home: "str | Path | None" = None,
) -> "tuple[str, str]":
    """Transcribe a scanned PDF or image, THROUGH THE CACHE (contract 5).

    The cache is keyed by (sha256 of the file bytes, page cap) under
    ``<home>/ocr/`` — so re-running the same job, or reading the same scan from
    a second tool, costs nothing. Only successful transcriptions are stored.
    With no home in scope (a bare factory, a unit test) the cache is simply
    skipped and the transcription runs as normal.
    """
    p = Path(path)
    enabled, cap = ocr_settings(config)
    if max_pages is not None:
        try:
            cap = max(1, min(int(max_pages), MAX_OCR_PAGES_CEILING))
        except (TypeError, ValueError):
            pass
    if not enabled:
        return "", _DISABLED_NOTE
    root_home = Path(home) if home is not None else ocr_home(config)
    digest: "str | None" = None
    if root_home is not None:
        try:
            digest = await asyncio.to_thread(file_digest, p)
        except OSError:
            digest = None
    if digest is not None and root_home is not None:
        cached = await asyncio.to_thread(load_cached, root_home, digest, cap)
        if cached is not None:
            note = str(cached.get("note") or f"scanned document — {OCR_MARK}")
            return cached["text"], f"{note}{CACHED_NOTE_SUFFIX}"
    if is_image(p):
        text, note = await ocr_image(p, router, config=config)
    elif is_pdf_file(p):  # by content, so a mislabeled scan is reachable
        text, note = await ocr_pdf(p, router, max_pages=cap, config=config)
    else:
        return "", (
            f"OCR applies to PDFs and images only — {p.suffix or p.name!r} is "
            "neither, so nothing was transcribed"
        )
    if text and digest is not None and root_home is not None:
        try:
            await asyncio.to_thread(store_cached, root_home, digest, cap, text, note)
        except Exception:  # noqa: BLE001 — see below
            # ANY failure to cache is swallowed, not just OSError. A successful
            # transcription must never be turned into "OCR fallback failed
            # (ValueError: ...)" by the bookkeeping that was supposed to make
            # the NEXT read free — the text in hand is the answer either way.
            pass
    return text, note


async def ocr_if_unreadable(
    path: "str | Path",
    extracted_text: str,
    router_resolver: Any,
    *,
    config: Any = None,
) -> "tuple[str, str]":
    """THE shared reach point: ``(text, note)`` for any document path.

    Returns ``extracted_text`` unchanged with an EMPTY note when the file has a
    real text layer or no router is wired — so every caller can route through
    this unconditionally and behave exactly as before when there is nothing to
    recover. Never raises: an OCR fault becomes a note, because failing to
    transcribe a scan must not turn a successful read into a failed one.
    """
    if router_resolver is None:
        return extracted_text, ""
    p = Path(path)
    try:
        # needs_ocr parses the PDF — off the event loop, like every other
        # filesystem/CPU step in this module.
        wanted = await asyncio.to_thread(needs_ocr, p, extracted_text)
    except Exception:  # noqa: BLE001 — a detection fault is simply "no OCR"
        return extracted_text, ""
    if not wanted:
        return extracted_text, ""
    try:
        router = router_resolver() if callable(router_resolver) else router_resolver
        text, note = await ocr_document(p, router, config=config)
    except Exception as exc:  # noqa: BLE001 — OCR failure ≠ read failure
        return extracted_text, (
            f"scanned document — OCR fallback failed ({type(exc).__name__}: {exc})"
        )
    return (text or extracted_text), note
