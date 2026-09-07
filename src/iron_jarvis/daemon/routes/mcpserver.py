"""``/mcp`` — Streamable HTTP for an external Build harness (plan 12.1, 12.3).

The second of the two routes in this app that refuse the install bearer token.
``/browser/ws`` accepts a browser pairing token and nothing else; ``/mcp``
accepts a **pane capability token** and nothing else, and refuses the install
bearer, a pairing token and no token at all — each by its own explicit check in
:func:`~iron_jarvis.browser.panetokens.authorize_mcp`, each with its own 401
sentence, and each with its own test (D17A, and the nine cross-rejections in plan
12.3's table).

This module is a TRANSPORT and deliberately little else. Every protocol decision
lives in :mod:`iron_jarvis.mcpserver.jsonrpc` (envelopes), ``session.py`` (the
``Mcp-Session-Id`` lifecycle) and ``server.py`` (the four methods, the capability
filter and the one call into ``ToolRegistry.invoke``). What is genuinely this
file's:

* **Reading the credential** off ``Authorization: Bearer`` or ``?token=``. Both,
  because the stdio shim and a CLI configured with a URL cannot always set a
  header, and the query form is the one every MCP client can express. Neither
  form is logged.
* **Getting the blocking check off the event loop.** ``authorize_mcp`` consults
  ``PairingStore.verify``, which is SQLite; on the loop that is the v1.153.1
  failure shape, which the user experiences as "Daemon offline". One
  ``asyncio.to_thread`` hop, at the door.
* **Choosing the body shape.** Streamable HTTP allows JSON or SSE and the client
  advertises both (``Accept: application/json, text/event-stream``); a client that
  asks only for SSE gets SSE, parsed by ``data:`` lines exactly as
  ``mcp/client.py:HttpTransport._parse_body`` does.
* **Putting the session id on the RESPONSE HEADER** of ``initialize``. The
  repository's own client reads ``mcp-session-id`` off the headers and nowhere
  else, so a body-only id would leave every later request session-less.

``GET /mcp`` exists and answers 405: Streamable HTTP's GET opens a
server→client SSE stream, and this server has nothing to push (it emits no
``tools/list_changed``; ``initialize`` says ``listChanged: false``). It still
authenticates first, so an unauthorised probe cannot tell a wrong credential from
an unsupported verb. ``DELETE /mcp`` ends one session.

Moved-into-routes convention: closure-local state is reached through ``d``. The
pane-token store and the session registry are coordinator additions to
``platform.py``; every access here is guarded, so the daemon boots and this route
refuses honestly while those fields do not yet exist — it never 500s.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ...browser.panetokens import PaneTokenRefused, authorize_mcp
from ...core.logging import get_logger
from ...mcpserver.jsonrpc import (
    INTERNAL_ERROR,
    JSON_CONTENT_TYPE,
    METHOD_NOT_FOUND,
    SSE_CONTENT_TYPE,
    JsonRpcError,
    accepts_json,
    accepts_sse,
    error_response,
    parse_body,
    sse_body,
)
from ...mcpserver.server import SUPPORTED_METHODS, dispatch
from ...mcpserver.session import SESSION_HEADER, session_id_from_headers

logger = get_logger(__name__)

#: What a request that reaches this route before the coordinator has wired the
#: store is told. 503, not 401: refusing a credential we never looked at would
#: send a harness to check its token, and the token is not the problem.
NOT_READY_MESSAGE = (
    "the Iron Jarvis MCP server is not ready on this install; restart Iron "
    "Jarvis and relaunch the harness from the Build pane"
)

#: The 405 sentence for ``GET /mcp``. Names what to do instead, because a bare
#: 405 reads to a harness author as "wrong URL".
NO_STREAM_MESSAGE = (
    "this server has no server-initiated stream; POST JSON-RPC to /mcp instead"
)


def _bearer(request: Request) -> str:
    """The candidate credential: ``Authorization: Bearer <t>`` or ``?token=``.

    Never logged, at any level. The value is a live capability credential, and a
    DEBUG line would put it in the user's diagnostics bundle for ever (the
    pairing lesson).
    """
    header = request.headers.get("authorization") or ""
    if header[:7].lower() == "bearer ":
        candidate = header[7:].strip()
        if candidate:
            return candidate
    return (request.query_params.get("token") or "").strip()


def _tokens(d) -> Any:
    """The :class:`~iron_jarvis.browser.panetokens.PaneTokenStore`, or ``None``.

    Looked for on ``d.platform`` first (where ``registry``, ``terminals`` and
    ``browser`` live) and then on ``d`` itself, because the wiring is another
    lane's edit and this route must not be the reason a boot fails. It is a
    lookup, never a policy: with no store, nothing is authorised at all.
    """
    for holder in (getattr(d, "platform", None), d):
        store = getattr(holder, "pane_tokens", None)
        if store is not None:
            return store
    return None


def _sessions(d) -> Any:
    """The :class:`~iron_jarvis.mcpserver.session.McpSessionRegistry`, or ``None``."""
    for holder in (getattr(d, "platform", None), d):
        registry = getattr(holder, "mcp_sessions", None)
        if registry is not None:
            return registry
    return None


def _pairing_verify(d) -> Any:
    """``PairingStore.verify``, or ``None`` when no browser runtime exists yet.

    Passed into ``authorize_mcp`` rather than imported by it, so the pairing
    cross-rejection is exercised against the REAL store in the app and against a
    lambda in a test. Blocking (SQLite) — which is why the caller hops threads.
    """
    try:
        pairing = getattr(getattr(getattr(d, "platform", None), "browser", None), "pairing", None)
    except Exception:  # noqa: BLE001 — a stand-in platform with an exploding property
        return None
    verify = getattr(pairing, "verify", None)
    return verify if callable(verify) else None


def _envelope_response(
    payload: dict[str, Any], status_code: int, accept: str, *, session_id: str = ""
) -> Response:
    """Serialise one JSON-RPC envelope as JSON or SSE, per the caller's Accept.

    ``detail`` is added beside the JSON-RPC ``error`` on a failure so the message
    is readable by BOTH audiences: a harness parsing JSON-RPC reads ``error``, and
    every HTTP-shaped reader in this app (``lib/api.ts``'s ``flattenDetail``, a
    ``curl`` in a bug report) reads ``detail``. Two spellings of one sentence,
    never two sentences.
    """
    body = dict(payload)
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        body["detail"] = str(error["message"])
    headers = {SESSION_HEADER: session_id} if session_id else None
    if not accepts_json(accept) and accepts_sse(accept):
        return Response(
            content=sse_body(body),
            status_code=status_code,
            media_type=SSE_CONTENT_TYPE,
            headers=headers,
        )
    return JSONResponse(content=body, status_code=status_code, headers=headers)


def register(app: FastAPI, d) -> None:
    """Wire ``GET``/``POST``/``DELETE /mcp`` onto ``app``."""

    async def _authorize(request: Request):
        """The one door. Returns a grant, or a ``Response`` that refuses.

        Order matters and is the contract: no store is 503 (nothing was checked),
        then ``authorize_mcp``'s own four checks, each 401 with its own sentence.
        """
        tokens = _tokens(d)
        sessions = _sessions(d)
        if tokens is None or sessions is None:
            logger.warning("/mcp refused a request: the pane token store is not wired")
            return None, _envelope_response(
                error_response(None, INTERNAL_ERROR, NOT_READY_MESSAGE),
                503,
                request.headers.get("accept", ""),
            )
        candidate = _bearer(request)
        try:
            # BLOCKING: `authorize_mcp` runs `PairingStore.verify` (SQLite).
            grant = await asyncio.to_thread(
                authorize_mcp,
                candidate,
                tokens=tokens,
                pairing_verify=_pairing_verify(d),
            )
        except PaneTokenRefused as exc:
            return None, JSONResponse(
                content={"detail": exc.message, "reason": exc.reason},
                status_code=exc.status_code,
            )
        return grant, None

    @app.post("/mcp")
    async def mcp_post(request: Request) -> Response:  # noqa: D401 - route
        """One JSON-RPC request in, one response (or nothing, for a notification).

        A notification — ``notifications/initialized``, which carries no ``id`` —
        gets an empty 202 and no body. Answering it would break the handshake the
        repository's own client performs.
        """
        grant, refusal = await _authorize(request)
        if refusal is not None:
            return refusal
        accept = request.headers.get("accept", "")
        raw = await request.body()
        try:
            parsed = parse_body(raw)
        except JsonRpcError as exc:
            return _envelope_response(exc.envelope(None), 400, accept)
        session_id = session_id_from_headers(request.headers)
        try:
            reply = await dispatch(
                parsed,
                d=d,
                grant=grant,
                sessions=_sessions(d),
                session_id=session_id,
            )
        except JsonRpcError as exc:
            return _envelope_response(exc.envelope(parsed.id), 500, accept)
        except Exception as exc:  # noqa: BLE001 — a tool crash is not a 500 page
            # NAMED, not a traceback: the harness relays this to its own model,
            # and `iron_jarvis.daemon.auth.unhandled_error_response` would give it
            # an opaque `err_` id instead of something it can act on.
            logger.exception("/mcp failed handling %s", parsed.method)
            return _envelope_response(
                error_response(
                    parsed.id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
                ),
                500,
                accept,
            )
        if reply.payload is None:
            return Response(status_code=reply.status_code)
        return _envelope_response(
            reply.payload, reply.status_code, accept, session_id=reply.session_id
        )

    @app.get("/mcp")
    async def mcp_get(request: Request) -> Response:  # noqa: D401 - route
        """405. Authenticated first, so a probe learns nothing from the verb.

        Streamable HTTP's GET opens a server→client SSE stream. This server pushes
        nothing (``initialize`` reports ``listChanged: false``), and a stream that
        never sends is worse than an honest refusal: a harness would hold it open
        and wait.
        """
        grant, refusal = await _authorize(request)
        if refusal is not None:
            return refusal
        return _envelope_response(
            error_response(None, METHOD_NOT_FOUND, NO_STREAM_MESSAGE,
                           {"supported": list(SUPPORTED_METHODS)}),
            405,
            request.headers.get("accept", ""),
        )

    @app.delete("/mcp")
    async def mcp_delete(request: Request) -> Response:  # noqa: D401 - route
        """End the session named by ``Mcp-Session-Id``.

        Bound to the pane the TOKEN resolved to, so one harness cannot close
        another pane's session by guessing its id. Unknown ids answer 200 with
        ``closed: false`` rather than 404 — a client tidying up after a session
        that already expired has done nothing wrong, and a 404 would tell an
        unauthorised caller which ids exist.
        """
        grant, refusal = await _authorize(request)
        if refusal is not None:
            return refusal
        session_id = session_id_from_headers(request.headers)
        closed = bool(_sessions(d).close(session_id, pane_id=grant.pane_id))
        return JSONResponse(content={"closed": closed}, media_type=JSON_CONTENT_TYPE)
