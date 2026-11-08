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
from typing import Callable, NamedTuple

#: How long one answer is served before a background refresh is started.
TTL_S = 20.0


class OllamaState(NamedTuple):
    """What the probe found (v1.449.0, readiness Wave C item 21). Three
    facts, because the first-run door says three different things:
    ``installed`` (Ollama's program is on this PC — step 1 is done, so the
    model download may start), ``running`` (its server answered, even with
    no model — "running but has no model yet"), ``has_model`` (answered
    with a model to use — the one press can choose it; this is what
    :func:`seen` always meant)."""

    installed: bool
    running: bool
    has_model: bool


NOT_SEEN = OllamaState(False, False, False)

_lock = threading.Lock()
#: (monotonic time of the answer, the answer) — None until first asked.
_answer: tuple[float, OllamaState] | None = None
_refreshing = False


def _detect(configured_url: str | None) -> OllamaState:
    """The probe (never raises; a fault reads as nothing seen).

    ``detect_ollama`` is asked first — the one probe every surface shares,
    and the seam the suite patches. It lists installed models; when it lists
    one, Ollama is installed and (``available``) running. When it lists NONE
    it cannot tell "not installed" from "running with no model yet", so only
    then the two facts it is built from are read directly: the program on
    this PC and ``GET /api/tags`` (its miss memo makes a repeated miss free;
    a hit is one more small loopback request, in the rare no-model case)."""
    try:
        from . import cli_detect

        models = cli_detect.detect_ollama(configured_url)
        if models:
            running = any(m.available for m in models)
            # Installed = its program is on this PC. A server answering with
            # no program found (OLLAMA_HOST naming one) is running, not
            # installed; the door ungreys its download step on either.
            installed = any(m.exec_path for m in models)
            return OllamaState(bool(installed), bool(running), bool(running))
        exec_path = cli_detect._ollama_binary()
        tags = None
        if cli_detect._ollama_worth_probing(exec_path, configured_url):
            tags = cli_detect._probe_ollama_tags(cli_detect.OLLAMA_API)
        return OllamaState(bool(exec_path), tags is not None, False)
    except Exception:  # noqa: BLE001 — a probe fault reads as "not seen"
        return NOT_SEEN


def _as_state(value: "bool | OllamaState") -> OllamaState:
    if isinstance(value, OllamaState):
        return value
    # A bare True is the old contract: answered with a model (so installed
    # and running too); a bare False says only that no model answered.
    return OllamaState(True, True, True) if value else NOT_SEEN


def record(value: "bool | OllamaState") -> None:
    """Remember an answer someone else just probed (Re-detect's own scan)."""
    global _answer
    with _lock:
        _answer = (time.monotonic(), _as_state(value))


def probe_now(configured_url: str | None = None) -> bool:
    """Ask now (blocking, ≤ the probe's timeout), remember and return whether
    Ollama answered with a model (the old contract; :func:`state` has the
    whole answer)."""
    found = _detect(configured_url)
    record(found)
    return found.has_model


def seen(
    configured_url: str | None = None,
    *,
    spawn: Callable[[Callable[[], None]], None] | None = None,
) -> bool:
    """The last answer (False before the first lands), without blocking:
    did Ollama answer on this PC with a model to use?

    A stale or missing answer starts ONE background refresh; ``spawn`` lets a
    test run it inline."""
    return state(configured_url, spawn=spawn).has_model


def state(
    configured_url: str | None = None,
    *,
    spawn: Callable[[Callable[[], None]], None] | None = None,
) -> OllamaState:
    """The last whole answer (:class:`OllamaState`; nothing seen before the
    first lands), without blocking — :func:`seen`'s three facts. A stale or
    missing answer starts ONE background refresh."""
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
    return answer[1] if answer is not None else NOT_SEEN


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="ollama-seen", daemon=True).start()


def forget() -> None:
    """Drop the answer, so the next read asks again (tests). A refresh still
    in flight may land after this; it only records what it saw."""
    global _answer, _refreshing
    with _lock:
        _answer = None
        _refreshing = False
