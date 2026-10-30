"""OCR ON THIS PC — Windows' built-in text recognizer (C-05).

A scanned W-2 or a phone photo of a 1099 used to be readable only by sending
its pages to a VISION MODEL (:mod:`.ocr`). With no vision model among the
user's local models the job stopped there, and with a cloud one connected the
scan left the machine. This module reads the page ON THIS PC, offline, with
the OCR engine Windows 10/11 ships (``Windows.Media.Ocr``), and :mod:`.ocr`
tries it BEFORE the vision call: text layer → this → vision model only when
this came back empty or doubtful.

WHY THIS ENGINE (measured 2026-09-11 on fictional W-2 / 1099 forms — clean,
noisy, rotated, half-resolution): Windows OCR and RapidOCR (ONNX) found the
SAME values (W-2 10/10 on every variant, 1099 7/8 — both missed a masked
``***-**-4321``), but Windows OCR took 0.02–0.10 s a page against 0.6–1.7 s
(+1.9 s warm-up), adds 2.8 MB of bindings against ~130 MB (OpenCV alone is
112 MB), and needs no model files. Its one weakness is that it reports no
confidence scores, so :func:`_judge` decides "doubtful" from the text itself.

PRIVACY IS THE POINT: nothing here touches the network. The engine is part of
Windows; the page bytes go from this process to the OS and back.

NEVER RAISES. Every entry point returns ``None`` (engine missing, no OCR
language installed, a decode failure, the per-page deadline) and the caller
falls back to exactly the pre-C-05 behaviour — the same asymmetry rule as
:mod:`.pdf_classify`: an engine that is absent or broken may never make the
app read LESS of a document than before.

BLOCKING: every call waits on a short-lived worker PROCESS
(:mod:`iron_jarvis.ocr_worker`, v1.410.2 — a WinRT call holds the GIL while it
waits, and a stalled one froze the whole daemon), so these functions are only
ever called through ``asyncio.to_thread`` (the v1.153.1 rule); calling one on a
thread whose loop is running returns ``None`` instead of freezing it.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: What every note that carries locally-read text says. Parsed by
#: ``attachment_rag.ocr_pages_spent`` (local pages cost no vision call) and by
#: ``ocr.ocr_document`` (which cache record a result belongs in) — a CONTRACT.
LOCAL_NOTE = "read on this PC by machine OCR"
#: Added for pages whose text is mostly figures: machine OCR can misread a
#: digit, and on a tax form one digit is the whole point.
CHECK_FIGURES = "check the figures"
#: A local result that :func:`_judge` doubts and no vision model could improve
#: on. Kept (never read less) but NEVER cached, so a later run with a vision
#: model connected can do better.
LOW_CONFIDENCE = "low confidence"

#: Seconds one page may take before we give up on it and let the vision path
#: (or the honest note) take over. Measured 0.02–0.10 s on this machine.
PAGE_DEADLINE_S = 20.0

#: Tests switch the engine off for the whole offline suite (``conftest``) so the
#: existing vision-path tests stay deterministic on every runner; the tests for
#: this module switch it back on or inject a fake recognizer.
_FORCED_OFF = False

_AVAILABLE: "bool | None" = None
_AVAIL_LOCK = threading.Lock()
_SELF_TEST: "dict[str, Any] | None" = None


@dataclass
class OcrResult:
    """One locally recognized page."""

    text: str
    #: ``False`` when :func:`_judge` doubts the text (too little, or mostly
    #: symbols) — the caller then prefers a vision model if one is connected.
    confident: bool
    #: Mostly figures: the note adds :data:`CHECK_FIGURES`.
    number_heavy: bool
    seconds: float
    engine: str = "Windows OCR"


# ------------------------------------------------------------- switches ---


def local_enabled(config: Any = None) -> bool:
    """Is local OCR switched on? ``config.ocr_local`` (default on), the
    ``IRONJARVIS_OCR_LOCAL=0`` override, and the test-suite switch."""
    if _FORCED_OFF:
        return False
    env = str(os.environ.get("IRONJARVIS_OCR_LOCAL", "")).strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    value = getattr(config, "ocr_local", True)
    return True if value is None else bool(value)


#: v1.410.2 — THE ENGINE RUNS IN ITS OWN PROCESS (:mod:`iron_jarvis.ocr_worker`).
#: A WinRT call holds Python's GIL while it waits, and on 2026-10-30 a stalled
#: WinRT activation in the OCR self-test froze the whole daemon on every start
#: (the desktop's watchdog restarted it, and it froze again). Here nothing
#: touches WinRT: each request starts a short-lived child, hands it one JSON
#: request on stdin, and kills it when its deadline passes.
WORKER_SUBCOMMAND = "ocr-worker"
#: How long the child may take to start (import the bindings) on top of the
#: work itself. A frozen fast-path start is ~0.3 s; a cold source start ~2 s.
WORKER_START_S = 15.0
#: The whole "which OCR languages are installed" question.
LANGUAGES_TIMEOUT_S = 20.0


def _worker_argv() -> "list[str]":
    """How to start the worker: the frozen exe's hidden subcommand (served by
    ``packaging/ironjarvis_entry._fast_path``), else this interpreter."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_SUBCOMMAND]
    return [sys.executable, "-m", "iron_jarvis.ocr_worker"]


def _ask_worker(request: dict, timeout: float) -> "dict | None":
    """One request to a fresh worker; ``None`` on any failure or when the
    child runs past ``timeout`` (its whole process tree is killed then).
    BLOCKING — never on the event loop. Never raises."""
    import json  # noqa: PLC0415

    from ..core import procs  # noqa: PLC0415

    try:
        result = procs.run(
            _worker_argv(),
            timeout=timeout,
            input=json.dumps(request).encode("ascii"),
        )
    except Exception:  # noqa: BLE001 — a missing program, a refused spawn
        return None
    if result.timed_out or result.returncode != 0:
        return None
    try:
        out = json.loads(result.stdout.decode("ascii", errors="replace") or "null")
    except Exception:  # noqa: BLE001
        return None
    return out if isinstance(out, dict) else None


def languages() -> "list[str]":
    """OCR recognizer languages installed on this PC (``[]`` when none, no
    engine, or the worker did not answer). BLOCKING. Never raises."""
    if sys.platform != "win32":
        return []
    out = _ask_worker({"op": "languages"}, LANGUAGES_TIMEOUT_S)
    langs = (out or {}).get("languages")
    return [str(x) for x in langs] if isinstance(langs, list) else []


def available() -> bool:
    """The engine imports AND at least one OCR language is installed.
    Cached for the process; never raises."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    with _AVAIL_LOCK:
        if _AVAILABLE is None:
            _AVAILABLE = bool(languages())
    return _AVAILABLE


# ------------------------------------------------------------ recognition ---


def recognize_many(pngs: "list[bytes]", *, deadline_s: float = PAGE_DEADLINE_S) -> "list[str | None]":
    """Text Windows OCR reads off each PNG — one worker for the whole batch,
    ``None`` for a page that failed or ran past ``deadline_s``. The worker is
    killed after every page's deadline plus its start-up allowance. BLOCKING —
    call through ``asyncio.to_thread``; inside a running loop it refuses."""
    if sys.platform != "win32" or not pngs:
        return [None] * len(pngs)
    try:
        asyncio.get_running_loop()
        return [None] * len(pngs)  # never block a running event loop
    except RuntimeError:
        pass
    import base64  # noqa: PLC0415

    request = {
        "op": "recognize",
        "pngs": [base64.b64encode(p).decode("ascii") for p in pngs],
        "deadline_s": deadline_s,
    }
    out = _ask_worker(request, timeout=deadline_s * len(pngs) + WORKER_START_S)
    texts = (out or {}).get("texts")
    if not isinstance(texts, list) or len(texts) != len(pngs):
        return [None] * len(pngs)
    return [t if isinstance(t, str) else None for t in texts]


def recognize_png(png: bytes, *, deadline_s: float = PAGE_DEADLINE_S) -> "str | None":
    """Text Windows OCR reads off one PNG, ``None`` on any failure or when the
    deadline passes. BLOCKING — call through ``asyncio.to_thread``."""
    return recognize_many([png], deadline_s=deadline_s)[0]


def _to_png(data: bytes) -> "bytes | None":
    """Any image Pillow can open → an upright RGB PNG the engine accepts,
    within its size limit. ``None`` = undecodable."""
    from PIL import Image, ImageOps  # noqa: PLC0415

    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            rgb = im.convert("RGB")
            limit = 10000  # OcrEngine.MaxImageDimension on Windows 10/11
            if max(rgb.size) > limit:
                scale = limit / max(rgb.size)
                rgb = rgb.resize((int(rgb.width * scale), int(rgb.height * scale)))
            buf = io.BytesIO()
            rgb.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


_TOKEN = re.compile(r"\S+")
_NUMERIC = re.compile(r"^[\$\(]?[-+]?\d[\d,.\-/]*%?\)?$")


def _judge(text: str) -> "tuple[bool, bool]":
    """``(confident, number_heavy)`` for text the engine returned.

    Windows OCR reports no confidence, so doubt is read off the text itself:
    too little of it for a page, or mostly symbols (the signature of a photo
    of something that is not text, or a page read sideways)."""
    tokens = _TOKEN.findall(text or "")
    alnum = sum(ch.isalnum() for ch in text or "")
    if alnum < 20 or not tokens:
        return False, False
    junk = sum(
        1 for t in tokens if sum(ch.isalnum() for ch in t) < max(1, len(t) // 2)
    )
    confident = junk / len(tokens) <= 0.4
    digits = sum(ch.isdigit() for ch in text)
    numeric = sum(1 for t in tokens if _NUMERIC.match(t))
    number_heavy = digits >= 0.15 * alnum or numeric >= 8
    return confident, number_heavy


def local_ocr_many(
    blobs: "list[bytes]", *, deadline_s: float = PAGE_DEADLINE_S
) -> "list[OcrResult | None]":
    """Read page images (PNG/JPEG/…) on this PC — ONE worker process for all
    of them (v1.410.2). Each entry is ``None`` when the engine is unavailable,
    the image cannot be decoded, its deadline passes, or it came back with no
    text. BLOCKING. Never raises."""
    out: "list[OcrResult | None]" = [None] * len(blobs)
    try:
        if not blobs or not available():
            return out
        pngs = [_to_png(b) for b in blobs]
        live = [i for i, png in enumerate(pngs) if png is not None]
        if not live:
            return out
        t0 = time.perf_counter()
        texts = recognize_many([pngs[i] for i in live], deadline_s=deadline_s)  # type: ignore[misc]
        each = (time.perf_counter() - t0) / len(live)
        for i, text in zip(live, texts):
            if not text or not text.strip():
                continue
            confident, number_heavy = _judge(text)
            out[i] = OcrResult(text=text.strip(), confident=confident,
                               number_heavy=number_heavy, seconds=each)
    except Exception:  # noqa: BLE001
        return [None] * len(blobs)
    return out


def local_ocr_bytes(
    data: bytes, *, deadline_s: float = PAGE_DEADLINE_S
) -> "OcrResult | None":
    """Read one page image (PNG/JPEG/…) on this PC. ``None`` when the engine
    is unavailable, the image cannot be decoded, the deadline passes, or the
    page came back with no text at all. BLOCKING. Never raises."""
    return local_ocr_many([data], deadline_s=deadline_s)[0]


def local_ocr_image(path: "str | Path") -> "OcrResult | None":
    """THE CHAT HOOK: read an image FILE on this PC — for an attached photo the
    answering model cannot see. ``None`` = nothing read (engine missing, not an
    image, unreadable). BLOCKING (``asyncio.to_thread``). Never raises."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return local_ocr_bytes(data)


def read_pages(
    blobs: "list[bytes]", numbers: "list[int]", config: Any = None
) -> "dict[int, OcrResult]":
    """Read each harvested page image on this PC — ``{page number: result}``
    for the pages that produced text (C-05).

    BLOCKING (one worker process for all the pages), so :mod:`.ocr` calls it through a
    single ``asyncio.to_thread`` hop rather than one per page. Switched off,
    or with no engine here, it returns ``{}`` and every page takes the vision
    path exactly as before."""
    out: dict[int, OcrResult] = {}
    if not local_enabled(config) or not available():
        return out
    # v1.410.2: one worker process for every page of the document.
    for number, result in zip(numbers, local_ocr_many(list(blobs))):
        if result is not None:
            out[number] = result
    return out


def note_for(result: "OcrResult | None") -> str:
    """The words a caller shows beside locally read text."""
    if result is None:
        return ""
    bits = [LOCAL_NOTE]
    if not result.confident:
        bits.append(LOW_CONFIDENCE)
    if result.number_heavy or not result.confident:
        bits.append(CHECK_FIGURES)
    return " — ".join(bits)


# ------------------------------------------------------------ self-test ---


def self_test() -> "dict[str, Any]":
    """Render a known line and read it back — proof the engine WORKS here, not
    just that it imports (the frozen-build rule). Cached; never raises."""
    global _SELF_TEST
    if _SELF_TEST is not None:
        return _SELF_TEST
    out: dict[str, Any] = {"ok": False, "languages": languages(), "detail": ""}
    if sys.platform != "win32":
        out["detail"] = "Windows OCR exists only on Windows"
    elif not out["languages"]:
        out["detail"] = "no OCR language is installed (or the OCR bindings are missing)"
    else:
        try:
            from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

            im = Image.new("RGB", (900, 160), "white")
            try:
                font = ImageFont.truetype("arial.ttf", 56)
            except Exception:  # noqa: BLE001
                font = ImageFont.load_default()
            ImageDraw.Draw(im).text((30, 40), "IRON JARVIS 4821", fill="black", font=font)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            holder: dict[str, Any] = {}
            # A worker thread: this may be asked from inside the event loop.
            worker = threading.Thread(
                target=lambda: holder.update(text=recognize_png(buf.getvalue(), deadline_s=10)),
                daemon=True,
            )
            worker.start()
            worker.join(WORKER_START_S + 12)
            if worker.is_alive() or "text" not in holder or holder.get("text") is None:
                # The OCR worker did not answer in time: say so, and ask
                # again next time (a stalled Windows call is not "no OCR").
                out["detail"] = "Windows OCR did not answer"
                return out
            text = holder.get("text") or ""
            out["ok"] = "4821" in text
            out["detail"] = "read the test line" if out["ok"] else f"read {text!r} instead of the test line"
        except Exception as exc:  # noqa: BLE001
            out["detail"] = f"{type(exc).__name__}: {exc}"
    _SELF_TEST = out
    return out
