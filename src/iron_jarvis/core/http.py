"""One shared TLS context for the app's short-lived HTTP clients (v1.332.0).

Every ``httpx.Client()`` / ``httpx.AsyncClient()`` builds a fresh
``ssl.SSLContext`` when it is constructed (even for a plain ``http://`` URL,
because the transport is made up front) and loads the certificate store into
it. Measured on the daily driver: ``ssl.create_default_context()`` 11.1 ms,
``httpx.create_ssl_context()`` 7.7 ms, a bare ``httpx.Client()`` 11.3 ms and a
bare ``httpx.AsyncClient()`` 13.0 ms (each the mean of 20 builds). A model
catalog build asks several servers at once, and a probe or a one-shot call
paid that every time.

:func:`ssl_context` builds the context ONCE, exactly the way httpx builds its
own default (``httpx.create_ssl_context()``: certifi's store, and the
``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` overrides honoured), and :func:`client` /
:func:`async_client` hand it to httpx as ``verify``. An ``SSLContext`` is safe
to share across threads and event loops for making connections; nothing here
ever mutates it after the build (no client certificate is loaded into it, so
a caller that needs one passes its own ``verify``).

``httpx.Client`` / ``httpx.AsyncClient`` are looked up when called, not
imported by name, so a test that replaces them on the ``httpx`` module still
sees every client made here.
"""

from __future__ import annotations

import ssl
import threading
from typing import Any

_ctx: ssl.SSLContext | None = None
_lock = threading.Lock()


def ssl_context() -> ssl.SSLContext:
    """The shared verifying context, built on first use. Never rebuilt."""
    global _ctx
    ctx = _ctx
    if ctx is not None:
        return ctx
    with _lock:
        if _ctx is None:
            import httpx

            _ctx = httpx.create_ssl_context()
        return _ctx


def _with_context(kw: dict[str, Any]) -> dict[str, Any]:
    # A caller's own ``verify`` (False, a path, its own context) always wins.
    if "verify" not in kw:
        kw["verify"] = ssl_context()
    return kw


def client(**kw: Any) -> Any:
    """``httpx.Client(**kw)`` on the shared TLS context."""
    import httpx

    return httpx.Client(**_with_context(kw))


def async_client(**kw: Any) -> Any:
    """``httpx.AsyncClient(**kw)`` on the shared TLS context."""
    import httpx

    return httpx.AsyncClient(**_with_context(kw))


def get(url: str, **kw: Any) -> Any:
    """``httpx.get`` on the shared TLS context (one request, one client)."""
    import httpx

    return httpx.get(url, **_with_context(kw))
