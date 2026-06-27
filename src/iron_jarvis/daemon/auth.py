"""Optional bearer-token auth for the daemon (public-deployment hardening).

Local-first by default: with no ``IRONJARVIS_TOKEN`` set, every request is
allowed (zero-config local dev). When ``IRONJARVIS_TOKEN`` is set to a
non-empty value, every request must present that token — either as an
``Authorization: Bearer <token>`` header or a ``?token=<token>`` query
parameter (the query form lets a browser open the OAuth callback / WebSocket
URL where setting a header is awkward).

Dependency-free: only Starlette (already pulled in by FastAPI) is used, so this
imports cleanly in the container even without git/docker on the PATH.

Wiring (done in ``daemon/app.py``, not here):

    from .auth import TokenAuthMiddleware
    app.add_middleware(TokenAuthMiddleware)   # after CORS

Note: ``BaseHTTPMiddleware`` only sees HTTP requests, so the ``/events``
WebSocket is NOT covered by this middleware — guard it separately if you expose
it publicly.
"""

from __future__ import annotations

import hmac
import os
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# --- Host / Origin guard (anti drive-by RCE + DNS rebinding) ----------------
# The local daemon is RCE-by-design (agents run tools/shell, /terminals spawns a
# PTY). A loopback bind is NOT enough: any website the user visits can fetch
# http://127.0.0.1:8787 (and open its WebSockets, which CORS does not cover). We
# reject (a) requests whose Host header is not loopback (defeats DNS rebinding,
# which uses an attacker hostname that resolves to 127.0.0.1) and (b) cross-
# origin BROWSER requests from untrusted Origins. Browsers cannot forge Origin,
# and only locally-served pages carry a loopback Origin, so loopback origins are
# trusted; CLI/server requests carry no Origin and pass. Covers HTTP + WebSocket.

# "testserver" is Starlette's TestClient default Host; a real browser/attacker
# can never send it (it sends the real loopback Host), so allowing it is safe.
_LOOPBACK_HOSTS = frozenset(
    {"127.0.0.1", "localhost", "::1", "[::1]", "testserver", ""}
)


def _host_label(host: str) -> str:
    """Host header without the port: '127.0.0.1:8787' -> '127.0.0.1'."""
    h = (host or "").strip()
    if h.startswith("["):  # bracketed IPv6 literal, e.g. [::1]:8787
        return h.split("]", 1)[0] + "]"
    return h.split(":", 1)[0]


def _host_ok(host: str) -> bool:
    label = _host_label(host).lower()
    if label in _LOOPBACK_HOSTS:
        return True
    allow = (os.environ.get("IRONJARVIS_HOST_ALLOWLIST") or "").strip()
    if not allow:  # default: loopback only (local daily driver)
        return False
    return label in {a.strip().lower() for a in allow.split(",") if a.strip()}


def _origin_ok(origin: str) -> bool:
    o = (origin or "").strip().rstrip("/")
    if not o:
        return True  # no Origin (CLI / server / top-level nav) -> not a CSRF vector
    try:
        host = (urlparse(o).hostname or "").lower()
    except Exception:
        return False
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True  # a browser can only send a loopback Origin from a local page
    cfg = (os.environ.get("IRONJARVIS_CORS_ORIGINS") or "").strip()
    allowed = {c.strip().rstrip("/") for c in cfg.split(",") if c.strip()}
    return o in allowed


class HostOriginGuardMiddleware:
    """Pure-ASGI guard covering HTTP AND WebSocket (BaseHTTPMiddleware can't see
    WS). Add it OUTERMOST (last add_middleware) so a bad Host/Origin is rejected
    before anything else runs."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") in ("http", "websocket"):
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in (scope.get("headers") or [])
            }
            if not _host_ok(headers.get("host", "")):
                return await self._reject(scope, receive, send, "host not allowed")
            if not _origin_ok(headers.get("origin", "")):
                return await self._reject(scope, receive, send, "origin not allowed")
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send, detail: str) -> None:
        if scope.get("type") == "websocket":
            try:
                await receive()  # consume the connect before closing
            except Exception:
                pass
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)

# Paths that must work without a token even when auth is enabled:
#   - health/liveness probes (load balancers, `ironjarvis status`)
#   - the interactive API docs and their schema
# OAuth provider redirects hit /oauth/{provider}/callback and are matched
# dynamically in `_is_exempt` (the provider segment is variable).
_EXEMPT_EXACT = frozenset(
    {"/health", "/docs", "/openapi.json", "/redoc"}
)

_TOKEN_ENV = "IRONJARVIS_TOKEN"


def _configured_token() -> str:
    """The active token, or ``""`` when auth is disabled."""
    return (os.environ.get(_TOKEN_ENV) or "").strip()


def auth_enabled() -> bool:
    """True when a non-empty ``IRONJARVIS_TOKEN`` is configured."""
    return bool(_configured_token())


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    # The OAuth provider redirect comes from the provider's browser with no
    # Authorization header; allow /oauth/<provider>/callback through.
    if path.startswith("/oauth/") and path.endswith("/callback"):
        return True
    return False


def _present_token(request: Request) -> str | None:
    """Extract a candidate token from the header or query string."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    qp = request.query_params.get("token")
    if qp:
        return qp
    return None


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Require a bearer token on every request when one is configured.

    The env var is read per-request (not at construction) so tests and live
    reconfiguration both work without rebuilding the app.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        token = _configured_token()
        if not token:  # auth disabled -> wide open (local dev)
            return await call_next(request)

        if _is_exempt(request.url.path):
            return await call_next(request)

        candidate = _present_token(request)
        if candidate is not None and hmac.compare_digest(candidate, token):
            return await call_next(request)

        return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
