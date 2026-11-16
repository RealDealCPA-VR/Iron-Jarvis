"""One plain line for a failure a route reports (v1.506.0, north-star fix plan 8.1).

The daemon handed raw Python exception text to the screen in about 170 error
details: ``detail=f"cannot redact: {exc}"``, ``f"{type(exc).__name__}: {exc}"``,
``f"undo failed: {type(exc).__name__}: {exc}"``. The dashboard rewords only a
500 (``dashboard/lib/pressFailure.ts``); a 4xx detail is shown as it stands, so
a person read "KeyError: 'path'" or "[Errno 13] Permission denied: 'C:\\…'".

:func:`plain_failure` is the ONE rule every route's ``except`` uses:

* a sentence the code itself wrote for the user is KEPT — an ``HTTPException``
  another layer raised (its ``detail``), an exception class defined in this
  package (a ``…Refused`` / ``…Error`` that carries our words), or a plain
  ``ValueError`` / ``RuntimeError`` / ``PermissionError`` (or another
  ``OSError`` with no ``errno``) raised from this package's own code
  (``raise ValueError("no such skill")``);
* anything else — a bare ``Exception``, an ``OSError`` the system raised, a
  ``KeyError``, a ``TypeError``, a library's error, a builtin's message such
  as ``invalid literal for int()`` — becomes the caller's plain ``what`` line
  ("That file could not be redacted."), and the raw text goes to the LOG
  (masked: a secret is never a log line), never to the screen.

A kept sentence still has to read as one: a stack, a JSON body, an
``[Errno …]`` or a ``SomeError: …`` prefix is engine text whoever raised it.
There is no ``technical`` field: the dashboard folds raw text under
"Details" only for a 500's envelope (``lib/pressFailure``), and a 4xx detail
is a string its readers show as it stands. Never raises; never empty.

``tests/test_north_star_plain_failures_v1506.py`` holds the rule and SCANS
``daemon/routes/`` so no detail interpolates a caught exception outside this
helper (with an allow list for the few sites whose text is our own).
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

#: Exception types whose MESSAGE is ours when this package's own code raised
#: them. Exact types only: a subclass (``json.JSONDecodeError``,
#: ``UnicodeDecodeError``, ``binascii.Error``, pydantic's ``ValidationError``)
#: is a library's words.
_OWN_WHEN_RAISED_HERE = (ValueError, RuntimeError)

#: What a builtin says when OUR line called it (the traceback ends in our
#: frame, but the words are Python's): never a sentence for a person.
_BUILTIN_WORDS = re.compile(
    r"invalid literal for|could not convert string|isoformat string|values to unpack|"
    r"substring not found|not in list|math domain error|embedded null|"
    r"object is not|object has no attribute|unexpected keyword|positional argument|"
    r"dictionary changed size|codec can't|unhashable type|is not subscriptable|"
    r"must be (?:str|int|real number|a string)|NoneType",
    re.I,
)

#: Engine text, whoever raised it: a stack, an errno, a JSON or repr body, a
#: ``SomeError:`` prefix, an object address.
_ENGINE_WORDS = re.compile(
    r"Traceback|\[Errno|\[WinError|object at 0x|^\s*[A-Za-z_.]*(?:Error|Exception)\s*:|"
    r"\{['\"]|^<|internal error \[err_",
)


def _raised_here(exc: BaseException) -> bool:
    """Did this package's own code raise *exc* (its innermost frame is ours)?

    Reads the code object's file name, which a frozen build keeps as a
    relative ``iron_jarvis/...`` path, so it works without source files."""
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    name = str(tb.tb_frame.f_code.co_filename or "").replace("\\", "/")
    return "/iron_jarvis/" in f"/{name}"


def _reads_as_a_sentence(text: str) -> bool:
    return bool(text) and len(text) <= 600 and "\n" not in text.strip() and not _ENGINE_WORDS.search(text)


def own_sentence(exc: BaseException | None) -> str:
    """The sentence *exc* carries when the code wrote it for the user, else "".

    Exposed for the few routes that need to know whether a failure is
    already worded (``plain_failure`` is the one most callers want)."""
    if exc is None:
        return ""
    try:
        try:
            from fastapi import HTTPException
        except Exception:  # noqa: BLE001 — fastapi always ships with the daemon
            HTTPException = None  # type: ignore[assignment]
        if HTTPException is not None and isinstance(exc, HTTPException):
            detail = exc.detail
            return detail.strip() if isinstance(detail, str) else ""
        text = str(exc).strip()
        if not _reads_as_a_sentence(text):
            return ""
        cls = type(exc)
        if str(getattr(cls, "__module__", "")).startswith("iron_jarvis"):
            return text
        if not _raised_here(exc):
            return ""
        if cls in _OWN_WHEN_RAISED_HERE:
            return "" if _BUILTIN_WORDS.search(text) else text
        if isinstance(exc, OSError) and getattr(exc, "errno", None) is None:
            # ``raise PermissionError("Outside the folders Iron Jarvis may use.")``
            # — an OSError the SYSTEM raised always carries an errno.
            return text
        return ""
    except Exception:  # noqa: BLE001 — the rule must never become the failure
        return ""


def _log_raw(what: str, exc: BaseException) -> None:
    try:
        from ..providers.failure_words import mask_failure_text

        raw = mask_failure_text(f"{type(exc).__name__}: {exc}")
    except Exception:  # noqa: BLE001
        raw = type(exc).__name__
    try:
        log.warning("%s (%s)", what, raw[:2000])
    except Exception:  # noqa: BLE001 — logging must never break the answer
        pass


def plain_failure(what: str, exc: BaseException | None) -> str:
    """The words a route's error detail carries for *exc*.

    *what* is the plain line for this place, a whole sentence in the app's
    words ("That file could not be redacted."). Our own sentence is kept;
    anything else is *what*, with the raw text logged (masked). Never raises;
    never empty."""
    fallback = str(what or "").strip() or "Iron Jarvis hit a problem doing that. Try again."
    mine = own_sentence(exc)
    if mine:
        return mine
    if exc is not None:
        _log_raw(fallback, exc)
    return fallback


__all__ = ["own_sentence", "plain_failure"]
