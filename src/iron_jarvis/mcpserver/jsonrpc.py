"""JSON-RPC 2.0 envelopes for the SERVER half, mirroring ``mcp/client.py`` (plan 12.1).

:mod:`iron_jarvis.mcp.client` has spoken this wire since v1.x and has been proven
against real servers; it is therefore the specification here, not a second
opinion. Its ``_envelope`` builds what this module must parse, and its
``_extract_result`` parses what this module must build. The pairs:

===========================  ==========================================
``client._envelope``          :func:`parse_request` (the other end of it)
``client._extract_result``    :func:`result_response` / :func:`error_response`
``client.PROTOCOL_VERSION``   :data:`PROTOCOL_VERSION` (IMPORTED, not retyped)
``HttpTransport._parse_body`` :func:`sse_body`
``HttpTransport._base_headers`` :func:`accepts_json` / :func:`accepts_sse`
===========================  ==========================================

The protocol version is imported rather than re-declared because two constants
that must agree eventually will not, and the failure mode is a handshake that a
harness refuses with no useful message. ``tests/test_pane_tokens_v1238.py`` also
round-trips this module's responses through the client's own ``_extract_result``,
which is a stronger check than asserting a dict shape: if the two halves ever
disagree, a test that only inspected keys would stay green.

No dependency is added. There is no ``mcp`` package in this repository and none
is wanted: the wire is ~200 lines of envelopes, and writing it by hand keeps the
server testable in-process against the client that already exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from ..mcp.client import PROTOCOL_VERSION as _CLIENT_PROTOCOL_VERSION

#: The MCP revision this server speaks. Imported from the client so the two
#: halves cannot drift; re-exported so a caller need not import the client.
PROTOCOL_VERSION: str = _CLIENT_PROTOCOL_VERSION

#: What ``initialize`` reports about this server. The name is the app's, the
#: version is the real installed one — a harness that logs it should be able to
#: tell the user which Iron Jarvis answered.
SERVER_INFO: dict[str, str] = {"name": "iron-jarvis", "version": __version__}

#: Content types. Streamable HTTP allows either body; the client accepts both
#: (``Accept: application/json, text/event-stream``) and parses SSE by ``data:``
#: lines, so a server may answer with whichever it prefers.
JSON_CONTENT_TYPE = "application/json"
SSE_CONTENT_TYPE = "text/event-stream"

# --- JSON-RPC 2.0 error codes (the spec's reserved range) -------------------
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    """A JSON-RPC failure that knows its own envelope.

    Raised by :func:`parse_request` and by the server's method handlers, so a
    handler never has to build an error dict by hand — the id, which the handler
    may not even have parsed yet, is filled in at the one place that knows it.
    """

    def __init__(
        self, code: int, message: str, data: Any = None, request_id: Any = None
    ) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = data
        #: The id of the message that failed, when the parser got far enough to
        #: read one. JSON-RPC 2.0: "If there was an error in detecting the id in
        #: the Request object (e.g. Parse error/Invalid Request), it MUST be
        #: Null" — which means the converse is a requirement too. A client that
        #: sent ``{"id": 7, "params": [1,2]}`` and got back ``id: null`` cannot
        #: correlate the refusal with the call it made, so it waits out its own
        #: timeout on a question that was already answered.
        self.request_id = request_id

    def envelope(self, request_id: Any = None) -> dict[str, Any]:
        """This error as a complete JSON-RPC response object.

        ``request_id`` is the caller's override; when it passes ``None`` (the
        route does, because at parse time it has no parsed request to read an id
        off) the id this error CARRIES is used. That is how a malformed-params
        message with a perfectly readable id still comes back correlated.
        """
        return error_response(
            self.request_id if request_id is None else request_id,
            self.code,
            self.message,
            self.data,
        )


@dataclass(frozen=True)
class JsonRpcRequest:
    """One parsed JSON-RPC request or notification.

    ``id`` is ``None`` for a notification *and* for the (invalid) case of a
    literal ``"id": null``; :attr:`is_notification` is the flag to branch on,
    because "no response is expected" and "the id happens to be null" must not be
    decided by the same test — MCP's ``notifications/initialized`` carries no id
    at all and expects no body, and answering it would break the handshake the
    client performs at ``HttpTransport._handshake``.
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: Any = None
    is_notification: bool = False


def parse_request(payload: Any) -> JsonRpcRequest:
    """Parse one JSON-RPC message, raising :class:`JsonRpcError` on anything else.

    Deliberately strict about the envelope and lenient about nothing:

    * a non-object (a list — i.e. a batch — a string, ``None``) is
      ``INVALID_REQUEST``. Batches are not supported and saying so is better than
      half-processing the first element; the repository's own client never sends
      one.
    * ``jsonrpc`` must be exactly ``"2.0"``.
    * ``method`` must be a non-empty string.
    * ``params`` may be absent or ``null`` (an empty object then), and must be an
      object when present. A positional (list) ``params`` is refused rather than
      guessed at: MCP is object-params only, and coercing a list would hand a
      tool arguments it never received.
    """
    if not isinstance(payload, dict):
        raise JsonRpcError(
            INVALID_REQUEST,
            "expected one JSON-RPC 2.0 request object (batches are not supported)",
        )
    # Readable from here on: the payload IS an object, so any id it carries can
    # ride the refusal (see ``JsonRpcError.request_id``). A non-scalar id is
    # dropped — the spec allows only a string, a number or null, and echoing a
    # dict back would hand the client something it cannot match either.
    known_id = payload.get("id")
    if not isinstance(known_id, (str, int, float)) or isinstance(known_id, bool):
        known_id = None
    if payload.get("jsonrpc") != "2.0":
        raise JsonRpcError(INVALID_REQUEST, 'jsonrpc must be "2.0"', request_id=known_id)
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(
            INVALID_REQUEST, "method must be a non-empty string", request_id=known_id
        )
    raw_params = payload.get("params")
    if raw_params is None:
        params: dict[str, Any] = {}
    elif isinstance(raw_params, dict):
        params = dict(raw_params)
    else:
        raise JsonRpcError(
            INVALID_PARAMS, "params must be an object", request_id=known_id
        )
    return JsonRpcRequest(
        method=method,
        params=params,
        id=payload.get("id"),
        is_notification="id" not in payload,
    )


def parse_body(raw: str | bytes) -> JsonRpcRequest:
    """Decode a request body and parse it. ``PARSE_ERROR`` on invalid JSON."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise JsonRpcError(PARSE_ERROR, f"invalid JSON: {exc}") from exc
    return parse_request(payload)


def result_response(request_id: Any, result: Any) -> dict[str, Any]:
    """A successful JSON-RPC response.

    ``result`` is always present, even when empty: the client's
    ``_extract_result`` returns ``{}`` for a missing or non-dict result, so an
    omitted key would be indistinguishable from an empty answer and a caller
    could never tell "the tool returned nothing" from "the server forgot".
    """
    return {"jsonrpc": "2.0", "id": request_id, "result": result if result is not None else {}}


def error_response(
    request_id: Any, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    """A JSON-RPC error response the client's ``_extract_result`` will raise on.

    ``error`` is a ``{code, message}`` object — the shape that client formats as
    ``"<code>: <message>"``. ``data`` is added only when there is some, so a
    harness printing the envelope is not shown a null field.
    """
    error: dict[str, Any] = {"code": int(code), "message": str(message)}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def sse_body(payload: dict[str, Any], *, event: str = "message") -> str:
    """One JSON-RPC response framed as a Server-Sent Event.

    The client parses SSE by scanning for ``data:`` lines and JSON-decoding the
    first one that is not ``[DONE]``, so the payload must be a SINGLE line: JSON
    is serialised without newlines and the trailing blank line terminates the
    event. A pretty-printed body here would be silently unparseable at the other
    end — every ``data:`` line would carry a fragment and the client would raise
    "no JSON-RPC payload in SSE response".
    """
    line = json.dumps(payload, separators=(",", ":"))
    return f"event: {event}\ndata: {line}\n\n"


def _accept_values(accept: str) -> list[str]:
    """The media types of an Accept header, lowercased, parameters dropped."""
    out: list[str] = []
    for part in (accept or "").split(","):
        media = part.split(";", 1)[0].strip().lower()
        if media:
            out.append(media)
    return out


def accepts_json(accept: str) -> bool:
    """Whether this Accept header admits a JSON body (``*/*`` counts)."""
    values = _accept_values(accept)
    if not values:
        return True  # no preference stated
    return JSON_CONTENT_TYPE in values or "*/*" in values or "application/*" in values


def accepts_sse(accept: str) -> bool:
    """Whether this Accept header admits an SSE body (``*/*`` counts)."""
    values = _accept_values(accept)
    if not values:
        return False  # never stream at a client that did not ask for it
    return SSE_CONTENT_TYPE in values or "*/*" in values or "text/*" in values


__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JSON_CONTENT_TYPE",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "SERVER_INFO",
    "SSE_CONTENT_TYPE",
    "JsonRpcError",
    "JsonRpcRequest",
    "accepts_json",
    "accepts_sse",
    "error_response",
    "parse_body",
    "parse_request",
    "result_response",
    "sse_body",
]
