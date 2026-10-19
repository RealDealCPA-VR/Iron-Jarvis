"""Is Ollama answering on this PC, with a model to use? (v1.353.0, G34)

ONE cached answer for every surface that must say so before the user has
connected anything. A running Ollama used to be listed in the composer
(``/models`` reads :func:`cli_detect.detect_ollama`) while /health, the
connect doors and the one-press route never named it, and picking it was
refused because ``ollama_base_url`` was still unset. /health now carries
``detected`` on its ollama row from here, and ``POST /onboarding/use-model``
saves this PC's address when the user presses for Ollama and this says it
answers.

The probe is :func:`cli_detect.detect_ollama` — an HTTP ``GET /api/tags``
with a 1.5 s ceiling, asked only when Ollama is installed (or
``OLLAMA_HOST`` / the setting names this PC). No process is spawned.

:func:`seen` NEVER blocks: it returns the last answer and, when that is
older than :data:`TTL_S`, starts ONE background refresh (the
``cli_auth.CliAuthProbe`` shape) — /health is polled every few seconds and a
closed localhost port can take the whole timeout to refuse on Windows.
:func:`probe_now` blocks (≤ the probe's timeout): it is for the press, which
runs on the threadpool and needs a fresh answer before it saves anything.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

#: How long one answer is served before a background refresh is started.
TTL_S = 20.0

_lock = threading.Lock()
#: (monotonic time of the answer, the answer) — None until first asked.
_answer: tuple[float, bool] | None = None
_refreshing = False


def _detect(configured_url: str | None) -> bool:
    """True when Ollama answered with at least one model (never raises)."""
    try:
        from .cli_detect import detect_ollama

        return any(m.available for m in detect_ollama(configured_url))
    except Exception:  # noqa: BLE001 — a probe fault reads as "not seen"
        return False


def record(value: bool) -> None:
    """Remember an answer someone else just probed (Re-detect's own scan)."""
    global _answer
    with _lock:
        _answer = (time.monotonic(), bool(value))


def probe_now(configured_url: str | None = None) -> bool:
    """Ask now (blocking, ≤ the probe's timeout), remember and return it."""
    value = _detect(configured_url)
    record(value)
    return value


def seen(
    configured_url: str | None = None,
    *,
    spawn: Callable[[Callable[[], None]], None] | None = None,
) -> bool:
    """The last answer (False before the first lands), without blocking.

    A stale or missing answer starts ONE background refresh; ``spawn`` lets a
    test run it inline."""
    global _refreshing
    now = time.monotonic()
    with _lock:
        answer = _answer
        stale = answer is None or now - answer[0] >= TTL_S
        start = stale and not _refreshing
        if start:
            _refreshing = True
    if start:

        def _run() -> None:
            global _refreshing
            try:
                probe_now(configured_url)
            finally:
                with _lock:
                    _refreshing = False

        try:
            (spawn or _thread)(_run)
        except Exception:  # noqa: BLE001 — no thread: the next read tries again
            with _lock:
                _refreshing = False
    return bool(answer[1]) if answer is not None else False


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="ollama-seen", daemon=True).start()


def forget() -> None:
    """Drop the answer, so the next read asks again (tests). A refresh still
    in flight may land after this; it only records what it saw."""
    global _answer, _refreshing
    with _lock:
        _answer = None
        _refreshing = False
