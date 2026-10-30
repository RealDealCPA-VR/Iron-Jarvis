"""Windows OCR in its OWN process (v1.410.2) — the ONLY module that touches WinRT.

WHY A SEPARATE PROCESS (a user bug, 2026-10-30): after an update the daemon
stopped answering on every start, and the freeze trace (logs/daemon-freeze.txt)
showed the same two threads each time: a Build terminal being restored was
parked inside Windows' ``CreateProcessW`` starting the Store PowerShell, and
the OCR self-test (``GET /onboarding`` → the doctor's ``check_local_ocr``) was
parked at the first WinRT object it creates (``InMemoryRandomAccessStream()``).
A WinRT call made through the ``winrt`` bindings HOLDS PYTHON'S GIL while it
waits, so when Windows stalled that activation every thread in the daemon —
the event loop, the freeze watcher's re-arm, ``/health`` — stopped, and the
desktop's watchdog killed and restarted it, over and over.

So no WinRT code runs in the daemon at all. :mod:`.documents.local_ocr` starts
this module as a short-lived child (``ironjarvis.exe ocr-worker`` when frozen,
served by ``packaging/ironjarvis_entry._fast_path`` without the CLI import;
``python -m iron_jarvis.ocr_worker`` from source), hands it ONE JSON request on
stdin, and kills it when its deadline passes. A stalled Windows call can then
cost one OCR answer, never the app. ``tests/test_ocr_isolation_v14102.py``
fails if any other module imports ``winrt`` or this one.

Protocol: stdin = one JSON object, stdout = one JSON object (ASCII).
* ``{"op": "languages"}`` → ``{"languages": [tag, ...]}``
* ``{"op": "recognize", "pngs": [base64, ...], "deadline_s": n}`` →
  ``{"texts": [str | null, ...]}`` (null = that page failed or ran past n).
Imports stay stdlib + winrt: this runs from the frozen exe's fast path.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from typing import Any


def _engine() -> Any:
    from winrt.windows.media.ocr import OcrEngine  # noqa: PLC0415

    return OcrEngine


def languages_here() -> "list[str]":
    try:
        return [str(lang.language_tag) for lang in _engine().available_recognizer_languages]
    except Exception:  # noqa: BLE001
        return []


async def _recognize_async(png: bytes) -> str:
    from winrt.windows.graphics.imaging import BitmapDecoder  # noqa: PLC0415
    from winrt.windows.storage.streams import (  # noqa: PLC0415
        DataWriter,
        InMemoryRandomAccessStream,
    )

    engine_cls = _engine()
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = engine_cls.try_create_from_user_profile_languages()
    if engine is None:
        # The user's display language has no OCR pack; use one that does.
        from winrt.windows.globalization import Language  # noqa: PLC0415

        for tag in languages_here():
            engine = engine_cls.try_create_from_language(Language(tag))
            if engine is not None:
                break
    if engine is None:
        return ""
    result = await engine.recognize_async(bitmap)
    return "\n".join(str(line.text) for line in result.lines)


def recognize_here(png: bytes, deadline_s: float) -> "str | None":
    try:
        return asyncio.run(asyncio.wait_for(_recognize_async(png), timeout=deadline_s))
    except Exception:  # noqa: BLE001 — TimeoutError included
        return None


def answer(request: Any) -> dict:
    op = request.get("op") if isinstance(request, dict) else None
    if op == "languages":
        return {"languages": languages_here()}
    if op == "recognize":
        try:
            deadline = float(request.get("deadline_s") or 20.0)
        except (TypeError, ValueError):
            deadline = 20.0
        texts: list[str | None] = []
        for item in request.get("pngs") or []:
            try:
                png = base64.b64decode(item)
            except Exception:  # noqa: BLE001
                texts.append(None)
                continue
            texts.append(recognize_here(png, deadline))
        return {"texts": texts}
    return {"error": "unknown request"}


def main(argv: "list[str] | None" = None) -> int:
    del argv
    try:
        request = json.loads(sys.stdin.buffer.read() or b"{}")
    except Exception:  # noqa: BLE001
        request = {}
    sys.stdout.buffer.write(json.dumps(answer(request)).encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
