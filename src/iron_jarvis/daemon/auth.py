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
from starlette.requests import ClientDisconnect, Request
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


def _max_body_bytes() -> int:
    """Global request-body ceiling (default 256 MB); override IRONJARVIS_MAX_BODY_MB."""
    try:
        mb = int(os.environ.get("IRONJARVIS_MAX_BODY_MB", "256"))
    except ValueError:
        mb = 256
    return max(1, mb) * 1024 * 1024


class BodyLimitMiddleware:
    """Pure-ASGI guard: reject an HTTP request whose body exceeds the cap (413),
    so an oversized JSON/base64 body (e.g. to /documents/write or
    /documents/upload) can't OOM/fill-disk the daemon.

    TWO layers, because neither is sufficient alone:

    (a) the ``content-length`` pre-check refuses BEFORE a single byte is
        buffered — the cheapest possible refusal, and the property this class
        was written for, so it stays first;
    (b) a wrapper around ``receive`` counts bytes as they actually stream in.
        An HTTP/1.1 request using ``Transfer-Encoding: chunked`` carries NO
        content-length, so (a) found nothing, ``break``ed, and the body passed
        through UNLIMITED. Measured end to end against a real uvicorn with the
        cap at 1 MB — the SAME valid 2 MB JSON body: with ``Content-Length`` ->
        413, chunked -> **200 OK, accepted and written to disk** (v1.195.0).

    The counter never buffers: it adds ``len(body)`` and hands the message on
    unchanged, so the inner app still does its own reading and an under-cap
    request (including a multi-chunk one, and one with no body at all) is
    byte-for-byte unaffected.

    Only ``http`` scopes are wrapped — a websocket has no request body and
    lifespan carries no bytes, so both are passed straight through, the same
    scope-type discipline ``HostOriginGuardMiddleware`` above uses.
    """

    def __init__(self, app) -> None:
        self.app = app
        # Read ONCE at construction (unchanged): the cap is process-wide config,
        # and re-reading os.environ on every request is per-request work for a
        # value that cannot legitimately change mid-run.
        self.max_bytes = _max_body_bytes()

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        await self._too_large(scope, send)
                        return
                except ValueError:
                    pass
                break

        seen = 0
        stop = False  # body cut short: stop pulling bytes off the wire
        refused = False  # WE answered 413; the inner app's response must not escape
        refusal_sent = False  # the 413 actually reached the transport
        started = False  # the inner app already sent http.response.start

        async def counting_receive():
            nonlocal seen, stop, refused, refusal_sent
            if stop:
                # Never touch the real receive again once we've cut the body off:
                # it would block waiting for bytes we just said we will not read.
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body") or b"")
                if seen > self.max_bytes:
                    stop = True
                    if not started:
                        # `refused` must be set BEFORE the send: if _too_large
                        # gets the response START out and then fails, the inner
                        # app must still be barred from sending a second one.
                        # The cost of that ordering is that a failed refusal
                        # would look identical to a successful one, so
                        # _too_large REPORTS its own failure (see there) and
                        # tells us with `refusal_sent` whether the client was
                        # actually answered.
                        refused = True
                        refusal_sent = await self._too_large(scope, send)
                    else:
                        # A response was already in flight, so we cannot retract
                        # it with a 413 — but the truncation must not be silent
                        # (this codebase's central rule; see the _walk_files
                        # truncation note in CLAUDE.md).
                        import logging

                        logging.getLogger("iron_jarvis.daemon").warning(
                            "request body exceeded %s bytes after the response had "
                            "already started; body truncated on %s %s",
                            self.max_bytes,
                            scope.get("method", "?"),
                            scope.get("path", "?"),
                        )
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal started
            if refused:
                # We already sent the 413. A SECOND http.response.start on one
                # scope is an ASGI protocol error, so whatever the inner app
                # produces after the cut (usually a ClientDisconnect 500) is
                # dropped here rather than written to the wire.
                return
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception as exc:
            # Cutting the body short makes Starlette raise ClientDisconnect, and
            # a route parsing a truncated body can raise anything. Once we have
            # answered 413 that exception is OUR doing, so it must not reach
            # ServerErrorMiddleware and be logged/served as a 500.
            if not refused:
                raise
            import logging

            # The swallow is deliberate but never silent. The EXPECTED case (we
            # refused, the app then hit our http.disconnect) is DEBUG: it happens
            # on every single refusal, and a WARNING there would re-create the
            # log-amplification this class is supposed to prevent. The case that
            # actually costs the caller an answer — the 413 never made it out —
            # is already reported at WARNING inside _too_large, which is the only
            # place that sees it in the real stack (ErrorEnvelopeMiddleware sits
            # INSIDE us and catches such an exception before it can reach here).
            logging.getLogger("iron_jarvis.daemon").debug(
                "swallowed %s after refusing %s %s with 413 (refusal delivered: %s)",
                type(exc).__name__,
                scope.get("method", "?"),
                scope.get("path", "?"),
                refusal_sent,
            )

    async def _too_large(self, scope, send) -> bool:
        """Send the 413. Returns True iff it actually reached the transport.

        A failure here means the caller gets NO response at all, which is the
        one thing this codebase never lets happen quietly (same rule as the
        truncation note in ``_walk_files``), so it is logged with the method and
        path rather than raised: the exception would unwind through the inner
        app, where ``ErrorEnvelopeMiddleware`` would catch it and produce a 500
        that ``guarded_send`` then drops — reported nowhere.
        """

        async def _closed():  # Response.__call__ never reads it; ASGI wants one
            return {"type": "http.disconnect"}

        try:
            await JSONResponse(
                {"detail": f"request body too large (limit {self.max_bytes} bytes)"},
                status_code=413,
            )(scope, _closed, send)
        except Exception as exc:  # noqa: BLE001 — transport gone mid-refusal
            import logging

            logging.getLogger("iron_jarvis.daemon").warning(
                "could not deliver the 413 refusal on %s %s (%s: %s); the client "
                "received NO response",
                scope.get("method", "?"),
                scope.get("path", "?"),
                type(exc).__name__,
                exc,
            )
            return False
        return True


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
    # Slack Events API receiver: Slack cannot carry our bearer token — its
    # REQUEST SIGNATURE is the auth (verified fail-closed in the handler with
    # the channel's signing secret; no secret configured = 403).
    if path.startswith("/comm/slack/events/"):
        return True
    return False


def token_matches(candidate: str | None, token: str) -> bool:
    """Constant-time token compare that CANNOT RAISE on client input.

    ``hmac.compare_digest`` refuses non-ASCII ``str`` arguments with
    ``TypeError`` ("comparing strings with non-ASCII characters is not
    supported"), and ``candidate`` comes straight off the wire (an
    ``Authorization`` header or ``?token=``). Passing it in raw meant
    ``Authorization: Bearer café`` raised INSIDE the middleware, which sits
    inside CORSMiddleware — but CORS only wraps ``send`` and catches nothing, so
    the TypeError was served by Starlette's outermost ``ServerErrorMiddleware``:
    a 500 with NO ``access-control-allow-origin``, which ``lib/api.ts`` maps to
    status 0 and every page renders as "daemon offline". An attacker (or a
    mistyped accented token) could trigger that at will. Comparing BYTES makes
    every candidate comparable, so a wrong token is a plain 401 again.

    Both sides are UTF-8 encoded (not stripped/ASCII-filtered) so the comparison
    stays constant-time over the encoded forms and a legitimately non-ASCII
    configured token still matches itself.
    """
    if candidate is None:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), token.encode("utf-8"))


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
        if token_matches(candidate, token):
            return await call_next(request)

        return JSONResponse({"detail": "missing or invalid token"}, status_code=401)


class ErrorEnvelopeMiddleware(BaseHTTPMiddleware):
    """Turn an unhandled exception into a JSON 500 *inside* the CORS layer.

    THE BUG THIS FIXES (v1.151.3), which cost two rounds of the wrong
    diagnosis: FastAPI's ``@app.exception_handler(Exception)`` is served by
    Starlette's ``ServerErrorMiddleware``, which is the OUTERMOST middleware —
    outside ``CORSMiddleware``. So a 500 went back to the browser with NO
    ``access-control-allow-origin`` header. The browser then refuses to let the
    page read the response at all, ``fetch`` rejects, and ``lib/api.ts`` maps
    that to ``ApiError(status=0)`` — which every page renders as "daemon
    offline".

    The result: EVERY unhandled server error in the whole app told the user
    their daemon was down. Measured directly — a 200 carries the CORS header, a
    500 carries none. It sent the reporter of the @mention failure looking at
    connectivity while the daemon was up and 500ing on a missing column.

    Being ordinary middleware, this sits INSIDE the CORS layer (it is added
    FIRST, and ``add_middleware`` stacks outermost-last), so the response it
    produces gets decorated on the way out and the browser can read the real
    detail. ``ServerErrorMiddleware`` stays as the backstop for anything raised
    outside the middleware chain.
    """

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        try:
            return await call_next(request)
        except ClientDisconnect:
            # NOT a server error, so it must not be logged as one. The request
            # body stream ended early: either the peer really went away, or WE
            # ended it on purpose — BodyLimitMiddleware answers 413 and then
            # feeds the route an ``http.disconnect``, which is exactly what
            # Starlette raises this for. Logging that with ``.exception()``
            # wrote an ERROR plus a ~50-line traceback saying "unhandled error
            # on POST /documents/upload" for a request we handled deliberately —
            # the same wrong-diagnosis trap this class's docstring was written
            # about, and worse: it turned the DoS guard into a log-amplifier
            # (~2 KB of ERROR per refused upload, where before there was none).
            # One INFO line, no traceback: reported, never silent, never a false
            # alarm. The response below is a formality — by definition nobody is
            # reading it (and after a 413 the guard drops it outright).
            import logging

            logging.getLogger("iron_jarvis.daemon").info(
                "client disconnected during %s %s (request body ended early)",
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=499,  # nginx's "client closed request"; never a 5xx
                content={"detail": "client disconnected"},
            )
        except Exception as exc:  # noqa: BLE001 — this IS the catch-all
            # Same shape app.py's handler produces, so clients see one contract.
            import logging

            logging.getLogger("iron_jarvis.daemon").exception(
                "unhandled error on %s %s", request.method, request.url.path
            )
            return JSONResponse(
                status_code=500,
                content={"detail": f"internal error: {type(exc).__name__}: {exc}"},
            )
