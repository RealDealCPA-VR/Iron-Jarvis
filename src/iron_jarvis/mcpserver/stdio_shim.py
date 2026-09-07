"""stdio JSON-RPC on one side, ``POST /mcp`` with the pane token on the other (D17, D18).

Not every harness version can consume Streamable HTTP, and D18 forbids assuming a
fixed mechanism, so a launch recipe that detects a CLI which speaks only stdio
points it at this file instead. One line of JSON in on ``stdin``, one line of JSON
out on ``stdout``, and in between exactly one HTTP round trip to the daemon.

**IT RELAYS; IT DECIDES NOTHING.** No capability filter, no roster, no second
opinion about a token — the shim cannot widen a grant because it never reads one.
Every gate is where it was before this file existed, at ``/mcp``. The two things
it must get right are both about faithfulness:

* **A notification gets no reply.** ``notifications/initialized`` carries no
  ``id``; writing anything back for it breaks a harness's handshake, and it is
  the single easiest thing to get wrong in a relay loop.
* **A refusal arrives as a JSON-RPC error, not as silence.** ``/mcp`` answers a
  bad credential with an HTTP 401 whose body is ``{"detail": "..."}``, which is
  not a JSON-RPC envelope at all. A shim that only forwarded well-formed
  envelopes would leave the harness with a dead pipe and no reason; instead the
  status and the daemon's own sentence are wrapped into an error response, so the
  user reads "relaunch the harness from the Build pane" in their editor.

**STDLIB ONLY, AND NO ``iron_jarvis`` IMPORT AT RUNTIME.** This file is meant to
be copied next to a harness and run by whatever Python that harness has, which is
not necessarily the interpreter Iron Jarvis ships inside (the packaged daemon is
frozen and has no importable ``iron_jarvis`` on disk at all). That is why the two
environment-variable names and the SSE parse are spelled here rather than
imported from :mod:`iron_jarvis.browser.panetokens` and
``mcp/client.py:HttpTransport._parse_body``. Both duplications are DRIFT RISKS and
both are pinned by ``tests/test_mcp_server_v1238.py``, which asserts the names
against the real constants and the parser against the real client's behaviour on
the same body — a fallback ``try: import … except ImportError:`` was the first
cut and it is worse, because a pin cannot fail against a fallback.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, TextIO

#: The variables a launch recipe puts in the pane's child environment. Spelled
#: literally; ``iron_jarvis.browser.panetokens.MCP_URL_ENV`` / ``MCP_TOKEN_ENV``
#: are the definitions and the test pins these against them.
MCP_URL_ENV = "IRONJARVIS_MCP_URL"
MCP_TOKEN_ENV = "IRONJARVIS_MCP_TOKEN"

#: The session header, spelled here for the same reason.
SESSION_HEADER = "Mcp-Session-Id"

#: One round trip's ceiling. A tool call that reaches a wedged browser is bounded
#: by the daemon's own command timeout well inside this; the number exists so a
#: dead daemon ends the call rather than parking the harness for ever.
DEFAULT_TIMEOUT_S = 60.0

# JSON-RPC error codes used when the shim has to author an envelope itself.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
INTERNAL_ERROR = -32603

#: What the shim says when it cannot reach the daemon at all.
UNREACHABLE_MESSAGE = "cannot reach Iron Jarvis at {url}: {error}"

#: What it says when it was started with no token in the environment.
NO_TOKEN_MESSAGE = (
    f"{MCP_TOKEN_ENV} is not set: start this harness from a Build pane in Iron "
    "Jarvis so the pane token reaches it"
)


@dataclass
class HttpReply:
    """One HTTP response, reduced to what a relay needs."""

    status: int
    text: str
    #: Header names LOWERCASED, because that is how the repository's own client
    #: reads ``mcp-session-id`` and a plain dict is not case-insensitive.
    headers: dict[str, str] = field(default_factory=dict)


class UrllibPoster:
    """The real transport: ``urllib.request``, so the shim needs no third party.

    A network failure is returned as an :class:`HttpReply` with status 0 rather
    than raised, so the caller has exactly one shape to relay.
    """

    def post(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> HttpReply:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read() or b""
                return HttpReply(
                    status=int(getattr(response, "status", 200) or 200),
                    text=raw.decode("utf-8", "replace"),
                    headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:  # a 4xx/5xx still carries a body
            raw = exc.read() or b""
            return HttpReply(
                status=int(exc.code),
                text=raw.decode("utf-8", "replace"),
                headers={str(k).lower(): str(v) for k, v in (exc.headers or {}).items()},
            )
        except Exception as exc:  # noqa: BLE001 — URLError, socket timeout, DNS…
            return HttpReply(status=0, text=f"{type(exc).__name__}: {exc}", headers={})


def payload_from_body(content_type: str, text: str) -> dict[str, Any] | None:
    """Decode one JSON-RPC payload from a JSON **or** SSE body. ``None`` if absent.

    The SSE branch mirrors ``mcp/client.py:HttpTransport._parse_body`` exactly:
    scan for ``data:`` lines and JSON-decode the first that is not ``[DONE]``.
    That client is the specification for this wire in both directions, and the
    test drives both parsers over the same body so a divergence is caught rather
    than discovered by a harness.
    """
    if "text/event-stream" in (content_type or "").lower():
        for raw in (text or "").splitlines():
            line = raw.strip()
            if line.startswith("data:"):
                chunk = line[len("data:") :].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        decoded = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    return decoded if isinstance(decoded, dict) else None
        return None
    if not (text or "").strip():
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def error_line(request_id: Any, code: int, message: str) -> str:
    """One JSON-RPC error response, serialised to a single line."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": int(code), "message": str(message)},
        },
        separators=(",", ":"),
    )


class StdioShim:
    """Relay stdio JSON-RPC to ``POST /mcp``, carrying the pane token.

    Args:
        url: the daemon's ``/mcp`` endpoint.
        token: the pane capability token, sent as ``Authorization: Bearer``.
        poster: injected transport (``post(url, body, headers, timeout)``), so
            the tests drive the whole relay with no socket — the same shape
            ``mcp/client.py:FakeTransport`` has, and for the same reason.
        timeout: seconds per round trip.
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        poster: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.url = str(url or "")
        self.token = str(token or "")
        self.poster = poster if poster is not None else UrllibPoster()
        self.timeout = float(timeout)
        #: Captured from the ``initialize`` response and sent on every later
        #: request. Without it the daemon answers "send initialize first" to a
        #: harness that DID, and the failure reads as a broken server.
        self.session_id = ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Both, exactly as the repository's client advertises: the daemon may
            # answer with either body and this shim parses either.
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return headers

    def handle_line(self, line: str) -> str | None:
        """Relay ONE stdin line. Returns the stdout line, or ``None`` for silence.

        ``None`` means "write nothing", and it is returned for exactly two cases:
        a blank line, and a NOTIFICATION (a message with no ``id``). Everything
        else — including every failure — produces a line, because a harness
        waiting on stdout has no other way to learn that something went wrong.
        """
        text = (line or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return error_line(None, PARSE_ERROR, f"invalid JSON: {exc}")
        if not isinstance(payload, dict):
            return error_line(
                None, INVALID_REQUEST, "expected one JSON-RPC 2.0 request object"
            )
        request_id = payload.get("id")
        is_notification = "id" not in payload
        if not self.token:
            return None if is_notification else error_line(
                request_id, INVALID_REQUEST, NO_TOKEN_MESSAGE
            )
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        reply = self.poster.post(self.url, body, self._headers(), self.timeout)
        session = (reply.headers or {}).get(SESSION_HEADER.lower(), "")
        if session:
            self.session_id = str(session).strip()
        if is_notification:
            # The daemon answers a notification with an empty 202 and no body.
            # Even if it answered with one, a notification takes no reply.
            return None
        if reply.status == 0:
            return error_line(
                request_id,
                INTERNAL_ERROR,
                UNREACHABLE_MESSAGE.format(url=self.url, error=reply.text),
            )
        decoded = payload_from_body((reply.headers or {}).get("content-type", ""), reply.text)
        if isinstance(decoded, dict) and decoded.get("jsonrpc") == "2.0":
            # A real envelope — relayed BYTE-FAITHFULLY in content: re-serialised,
            # never rebuilt field by field, so a result this shim does not
            # understand still reaches the harness intact.
            return json.dumps(decoded, separators=(",", ":"))
        # Not an envelope: a 401/403/503 from the route, whose body is
        # {"detail": "..."}. Carry the daemon's own sentence, and the status, so
        # the user reads the remedy instead of watching a pipe die.
        detail = ""
        if isinstance(decoded, dict):
            detail = str(decoded.get("detail") or decoded.get("message") or "")
        detail = detail or (reply.text or "").strip() or "no response body"
        return error_line(request_id, INTERNAL_ERROR, f"HTTP {reply.status}: {detail}")

    def run(self, stdin: TextIO | Iterable[str], stdout: TextIO) -> int:
        """Pump every line of ``stdin`` through :meth:`handle_line`. Returns 0.

        Flushed per line: a harness reads one response before sending the next,
        so a buffered stdout is a deadlock, not a slow start.
        """
        for line in stdin:
            answer = self.handle_line(line)
            if answer is None:
                continue
            stdout.write(answer + "\n")
            stdout.flush()
        return 0


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    shim_factory: Callable[..., StdioShim] | None = None,
) -> int:
    """Entry point: read the URL and token from the environment and pump stdio.

    ``argv[1]`` may override the URL, which is what a recipe writes into a config
    file that cannot set environment variables. The TOKEN is never taken from a
    command line: an argv is visible in every process listing on the machine.

    Returns 2 when it was started with no token, after saying so on ``stderr`` —
    the harness's own log is where a launch problem has to appear.
    """
    args = list(argv if argv is not None else sys.argv)
    environ = dict(env if env is not None else os.environ)
    url = (args[1] if len(args) > 1 else "") or environ.get(MCP_URL_ENV, "")
    token = environ.get(MCP_TOKEN_ENV, "")
    err = stderr if stderr is not None else sys.stderr
    if not token:
        err.write(NO_TOKEN_MESSAGE + "\n")
        return 2
    if not url:
        err.write(f"{MCP_URL_ENV} is not set and no URL was given\n")
        return 2
    factory = shim_factory if shim_factory is not None else StdioShim
    shim = factory(url, token)
    return shim.run(stdin if stdin is not None else sys.stdin,
                    stdout if stdout is not None else sys.stdout)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MCP_TOKEN_ENV",
    "MCP_URL_ENV",
    "NO_TOKEN_MESSAGE",
    "SESSION_HEADER",
    "UNREACHABLE_MESSAGE",
    "HttpReply",
    "StdioShim",
    "UrllibPoster",
    "error_line",
    "main",
    "payload_from_body",
]
