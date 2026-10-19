"""Gemini takes an API key (v1.355.0, G46).

The Google (Gemini) card used to offer only an account login, and that login
first needs an OAuth client the user registers in Google Cloud Console. The
key most Gemini users actually have -- one from Google AI Studio -- could not
be pasted, although the adapter has always been able to send one
(``adapters/google.py``: ``x-goog-api-key`` vs ``Authorization: Bearer``).

This module is the ONE place that tells the two Google credentials apart, so
the adapter's header (``providers/manager.py``) and the Connections Test
(``connections/probe.py``) can never disagree about which one they hold:

* an AI Studio / Google Cloud **API key** is sent as ``x-goog-api-key`` (the
  header Google's own REST example uses for every key). It comes in TWO
  shapes: an older "standard" key starts ``AIza``; an "auth key" starts
  ``AQ.`` -- and since May 28, 2026 every NEW key AI Studio makes is an auth
  key (ai.google.dev/gemini-api/docs/api-key, read 2026-10-19; Google staff on
  the developer forum, 2026-07-27: "AI Studio will now only generate
  Authentication Key (AQ) keys"). A rule that knew only ``AIza`` sent a new
  user's key as a Bearer token and the Test read it at tokeninfo -- both
  refuse it -- so a reviewer added ``AQ.`` (v1.355.0 review);
* an **OAuth access token** from the account login (``ya29.…``) is sent as a
  Bearer token -- and so is anything else, which keeps every pre-v1.355.0
  Google connection (all of them OAuth) byte-identical.

``probe_google_key`` is the Test for a key: one cheap, read-only GET of the
model list. A key cannot be checked at the OAuth ``tokeninfo`` endpoint the
login's Test uses -- that answers 400 for every key, so a good key would read
as broken.
"""

from __future__ import annotations

from typing import Any, Callable

#: How a Google API key starts: an auth key (``AQ.``, every new AI Studio key
#: since 2026-05-28) or an older standard key (``AIza``). An OAuth access
#: token (``ya29.``) starts with neither.
GOOGLE_KEY_PREFIXES: tuple[str, ...] = ("AQ.", "AIza")

#: Where a new user gets a Gemini key -- the card's "Get one at" link.
AI_STUDIO_KEY_URL = "https://aistudio.google.com/apikey"

#: A cheap, read-only, key-authenticated request: the model list (no tokens).
GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_TIMEOUT_SECONDS = 12.0


def is_google_api_key(credential: Any) -> bool:
    """True when ``credential`` is a Google API key (sent as
    ``x-goog-api-key``); False for an OAuth access token or anything else
    (sent as a Bearer token, exactly as before v1.355.0). Never raises."""
    if not isinstance(credential, str):
        return False
    return credential.strip().startswith(GOOGLE_KEY_PREFIXES)


def probe_google_key(
    key: str,
    *,
    get: "Callable[..., Any] | None" = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Check a Gemini API key with one read-only request. ``(ok, detail)`` in
    the words :func:`iron_jarvis.connections.probe.live_probe` uses for every
    other provider. ``get`` is injectable for tests (``httpx.get``'s shape).

    Google answers a bad key with a **400** ("API key not valid"), not a 401,
    so that 400 reads as a refused key here; any other 400 keeps Google's own
    words. The key rides the header only -- never the URL, which a log or an
    error could carry."""
    if get is None:
        import httpx  # lazy: only when a real probe runs

        get = httpx.get
    try:
        resp = get(
            GOOGLE_MODELS_URL,
            headers={"x-goog-api-key": (key or "").strip()},
            params={"pageSize": "1"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — a probe fault is a red Test, never a crash
        # The class name only: an exception's text can carry the request.
        return False, f"could not reach google: {type(exc).__name__}"

    status = int(getattr(resp, "status_code", 0) or 0)
    if status == 200:
        return True, "google reachable — credential accepted"
    text = (getattr(resp, "text", "") or "").strip().replace("\n", " ")
    if status in (401, 403) or (status == 400 and _says_bad_key(text)):
        return False, (
            f"google rejected the key ({status}) — the key is invalid, revoked, "
            "or not enabled for the Gemini API; get a new one at "
            "aistudio.google.com/apikey"
        )
    if status == 429:
        return True, "google reachable (rate-limited, but credential is valid)"
    return False, f"google probe returned HTTP {status}: {text[:200]}"


def _says_bad_key(text: str) -> bool:
    """Google's own words for a key it will not accept."""
    low = text.lower()
    return "api_key_invalid" in low or "api key not valid" in low or "api key expired" in low
