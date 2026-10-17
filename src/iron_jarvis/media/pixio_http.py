"""The Pixio HTTP helpers, LIFTED out of ``tools/pixio.py`` (v1.346.0).

Two callers speak this API — the agent-facing ``pixio_*`` tools and the
:class:`~iron_jarvis.media.pixio.PixioProvider` behind ``generate_media`` —
and they must share ONE transport, ONE error map and ONE key rule rather
than two copies that drift. ``tools/pixio.py`` re-exports every name under
its old spelling (``_BASE_URL``, ``_http_error``, …) so the avatar route and
the creative routes keep importing from where they always did.

Auth: every API call sends ``Authorization: Bearer <key>``. Output URLs are
PUBLIC CDN links and are downloaded with NO headers — the key never reaches
a third-party host (:func:`iron_jarvis.media.base.fetch_public`).

Testability: the transport is dependency-injected — ``http(method, url,
headers, json_body) -> response-like`` (``.status_code``, ``.content``,
``.json()``, optionally ``.headers``); the production default lazily imports
httpx, so this module imports clean without it.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import PurePosixPath
from typing import Any, Callable, NamedTuple
from urllib.parse import urlparse

from .base import HttpRequest

#: (url, headers, blob, filename, mime) -> response-ish — multipart uploads.
HttpUpload = Callable[[str, "dict[str, str]", bytes, str, str], Any]
#: () -> Pixio API key (or ``None`` when not configured).
KeyResolver = Callable[[], "str | None"]

BASE_URL = "https://beta.pixio.myapps.ai"
MISSING_KEY_ERROR = (
    "Pixio API key not configured — add a secret named 'pixio' (Secrets page) "
    "or set PIXIO_API_KEY"
)

#: A plausible file extension: dot + up to 8 alphanumerics (``.png``, ``.mp4``).
_EXT_RX = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def env_key_resolver() -> str | None:
    """Default key source — the ``PIXIO_API_KEY`` env var (vault closure wins in prod)."""
    return os.environ.get("PIXIO_API_KEY") or None


def default_http(
    method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
) -> Any:
    """Production transport — httpx imported lazily so import stays dependency-light."""
    import httpx

    # Generous read timeout: media downloads can be tens of MB.
    timeout = httpx.Timeout(120.0, connect=10.0)
    return httpx.request(
        method, url, headers=headers, json=json_body, timeout=timeout, follow_redirects=True
    )


def default_http_upload(
    url: str, headers: dict[str, str], blob: bytes, filename: str, mime: str
) -> Any:
    """Production multipart transport (uploads can be large — generous timeout)."""
    import httpx

    timeout = httpx.Timeout(300.0, connect=10.0)
    return httpx.request(
        "POST",
        url,
        headers=headers,
        files={"file": (filename, blob, mime or "application/octet-stream")},
        timeout=timeout,
        follow_redirects=True,
    )


def detail(payload: Any) -> str:
    """Best-effort human detail from an API body (error/message/detail, str or dict)."""
    if not isinstance(payload, dict):
        return ""
    for key in ("error", "message", "detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("message") or value.get("detail") or ""
        if value:
            return str(value)
    return ""


def http_error(status: int, payload: Any) -> str | None:
    """Map a Pixio HTTP status to one clear line (``None`` when 2xx). Honest errors
    beat fabricated output — each mapped line tells the agent what to DO next."""
    if 200 <= status < 300:
        return None
    text = detail(payload)
    suffix = f": {text}" if text else ""
    if status == 401:
        return "Pixio rejected the API key (401) — update the 'pixio' secret or PIXIO_API_KEY"
    if status == 402:
        return f"Pixio: insufficient credits (402) — top up the account before generating{suffix}"
    if status == 404:
        return f"Pixio: not found (404){suffix or ' — check the model/generation id'}"
    if status == 429:
        return (
            "Pixio concurrency limit — one generation at a time on default accounts; "
            "wait for the current one"
        )
    return f"Pixio API error {status}{suffix}"


def output_url(payload: dict[str, Any]) -> str:
    """Extract the result URL — ``outputUrl`` is canonical, ``outputs`` tolerated."""
    url = payload.get("outputUrl") or payload.get("output_url") or ""
    if not url:
        outputs = payload.get("outputs")
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, str):
                url = first
            elif isinstance(first, dict):
                url = first.get("url") or first.get("outputUrl") or ""
    return str(url or "")


def ext_from_url(url: str) -> str:
    """Guess a file extension from the URL path (``.bin`` when unguessable)."""
    try:
        suffix = PurePosixPath(urlparse(url).path).suffix
    except (ValueError, TypeError):
        return ".bin"
    return suffix if _EXT_RX.match(suffix or "") else ".bin"


def safe_name(generation_id: str) -> str:
    """Generation ids become filenames — strip anything filesystem-hostile."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", generation_id) or "generation"


class ApiAnswer(NamedTuple):
    status: int
    payload: Any
    headers: dict[str, str]


def _headers_of(resp: Any) -> dict[str, str]:
    raw = getattr(resp, "headers", None)
    if not raw:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in dict(raw).items()}
    except Exception:  # noqa: BLE001 — an odd headers object is not worth a crash
        return {}


def api_call_sync(
    http: HttpRequest,
    method: str,
    path: str,
    key: str,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> ApiAnswer:
    """One authenticated round-trip (SYNC — run it off the loop)."""
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    resp = http(method, BASE_URL + path, headers, json_body)
    status = int(getattr(resp, "status_code", 0) or 0)
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON error bodies happen
        payload = {}
    return ApiAnswer(
        status, payload if isinstance(payload, (dict, list)) else {}, _headers_of(resp)
    )


async def api_call(
    http: HttpRequest,
    method: str,
    path: str,
    key: str,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> ApiAnswer:
    """One authenticated round-trip → ``(status_code, parsed-json, headers)``.
    The transport is a SYNC callable (httpx by default), so it runs off the
    event loop — a slow Pixio round-trip must not freeze the daemon."""
    return await asyncio.to_thread(
        api_call_sync, http, method, path, key, json_body, extra_headers
    )


def pixio_publish(
    key: str,
    *,
    blob: bytes | None = None,
    filename: str = "",
    mime: str = "",
    url: str | None = None,
    endpoint: str = "media",
    http: HttpRequest | None = None,
    http_upload: HttpUpload | None = None,
) -> str:
    """Publish media to Pixio's public CDN → a CLEAN, PERMANENT, PUBLIC url
    (no signed query string) usable directly in generation params.

    ``POST /api/v1/images`` (images only) / ``POST /api/v1/media`` (any media).
    Two input modes: a local ``blob`` (multipart ``file=``) or a remote ``url``
    to mirror (JSON ``{"url": …}``). Both return ``{"url": "https://…"}``.
    SYNC (callers run it off the loop); raises ``RuntimeError`` with an honest
    message on any failure — never a fabricated URL.
    """
    path_part = "images" if endpoint == "images" else "media"
    target = f"{BASE_URL}/api/v1/{path_part}"
    auth = {"Authorization": f"Bearer {key}"}
    if url:
        resp = (http or default_http)(
            "POST", target, {**auth, "Accept": "application/json"}, {"url": url}
        )
    elif blob is not None:
        resp = (http_upload or default_http_upload)(
            target, auth, blob, filename or "file.bin", mime
        )
    else:
        raise RuntimeError("pixio_publish needs a blob or a url")
    status = int(getattr(resp, "status_code", 0) or 0)
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON error bodies happen
        payload = {}
    err = http_error(status, payload if isinstance(payload, dict) else {})
    if err:
        raise RuntimeError(err)
    public = str((payload or {}).get("url") or "") if isinstance(payload, dict) else ""
    if not public:
        raise RuntimeError("Pixio upload returned no url")
    return public
