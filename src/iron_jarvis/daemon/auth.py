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

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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
