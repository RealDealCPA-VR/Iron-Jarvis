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

**IT OUTLIVES A RESTART OF IRON JARVIS (MC3, third fix plan).** The door keeps
sessions in memory and forgets them on every restart (every update) and after an
idle hour, then answers each later call "no MCP session" (HTTP 400, JSON-RPC
``-32600``; a daemon that follows the MCP spec more closely answers 404). The
coding tool's own client never starts a new session over stdio, so the bridge
does it: it keeps the tool's own ``initialize`` (and ``notifications/
initialized``), replays them with NO session id, through the same token check,
and repeats the refused call ONCE with the new id. The new session is rebuilt
by the daemon from that handshake and the pane's grant as it is now. A daemon
that does not answer at all (refused connection: it is restarting) is retried
for a few seconds with a backoff (:data:`RETRY_DELAYS_S`), then the tool reads a
plain sentence; a call that may have reached the daemon is never sent twice.

**ONLY TO THIS PC, NEVER THROUGH A PROXY (MC7).** The bridge carries the pane's
token, so the real transport ignores every proxy setting (environment and
Windows) and refuses an address that is not this computer.
"""

from __future__ import annotations

import io
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.parse
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

#: What the shim says when the daemon did not answer and the call may have
#: reached it (a read timed out, the connection dropped mid-answer). Never
#: repeated: a tool could run twice.
UNREACHABLE_MESSAGE = (
    "cannot reach Iron Jarvis at {url}: no answer came back, so it is not known "
    "whether the call was made. Check in Iron Jarvis before trying it again"
)

#: What it says when Iron Jarvis is not answering at all (nothing listening:
#: it is restarting or closed), after :data:`RETRY_DELAYS_S`.
NOT_ANSWERING_MESSAGE = (
    "cannot reach Iron Jarvis at {url}: it is not answering right now (it may be "
    "restarting), so the call was not made. Try again in a minute; if it keeps "
    "happening, open Iron Jarvis"
)

#: What it says for an address that is not this computer (MC7). The pane's
#: token is never sent there.
NOT_THIS_PC_MESSAGE = (
    "the address for Iron Jarvis's tools is not on this computer, so nothing was "
    "sent: start this coding tool again from a Build terminal in Iron Jarvis"
)

#: What it says when a new session was started but speaks another protocol
#: version than the one this coding tool agreed at its start (an update).
PROTOCOL_CHANGED_MESSAGE = (
    "Iron Jarvis was updated and its tools now need a fresh start of this coding "
    "tool: the call was not made. Start the coding tool again"
)

#: What it says when it was started with no token in the environment.
NO_TOKEN_MESSAGE = (
    f"{MCP_TOKEN_ENV} is not set: start this coding tool from a Build terminal in Iron "
    "Jarvis so the terminal token reaches it"
)

#: Waits between tries while Iron Jarvis is not answering (MC3): about 15 s in
#: all, the time a restart takes, then the plain sentence. Short: the coding
#: tool's model is waiting on this call.
RETRY_DELAYS_S: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0)

#: The id the bridge gives its own replayed ``initialize``; its answer is read
#: here and never written to the coding tool.
REINIT_ID_PREFIX = "ironjarvis-bridge-reinit-"


def is_loopback_url(url: str) -> bool:
    """Is ``url`` an http(s) address on THIS computer (127.x, ::1, localhost)?

    No name lookup: ``localhost`` is taken literally and any other name is
    refused, so a name that resolves elsewhere cannot carry the token away.
    """
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        if parts.scheme not in ("http", "https"):
            return False
        host = (parts.hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class HttpReply:
    """One HTTP response, reduced to what a relay needs."""

    status: int
    text: str
    #: Header names LOWERCASED, because that is how the repository's own client
    #: reads ``mcp-session-id`` and a plain dict is not case-insensitive.
    headers: dict[str, str] = field(default_factory=dict)
    #: For ``status == 0`` only. ``False``: the request certainly never left this
    #: PC (the connection was refused, or the address was refused here). ``None``:
    #: not known (it may have reached Iron Jarvis), so it is never repeated.
    sent: bool | None = None
    #: ``True`` when trying again in a moment may succeed (nothing listening).
    retryable: bool = False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is answered as it stands, never followed: following one would
    carry the token's headers to wherever the answer points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class UrllibPoster:
    """The real transport: ``urllib.request``, so the shim needs no third party.

    A network failure is returned as an :class:`HttpReply` with status 0 rather
    than raised, so the caller has exactly one shape to relay.

    NO PROXY, EVER (MC7): urllib's default opener honours ``HTTP_PROXY`` /
    ``ALL_PROXY`` and the Windows proxy settings even for ``127.0.0.1``, and
    builds that choice once per process. This opener has an empty
    ``ProxyHandler`` and follows no redirect, and a target that is not this
    computer is refused before anything is sent.
    """

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def post(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> HttpReply:
        if not is_loopback_url(url):
            return HttpReply(status=0, text=NOT_THIS_PC_MESSAGE, headers={}, sent=False)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=timeout) as response:
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
        except urllib.error.URLError as exc:
            # Raised while CONNECTING or sending; a refused connection means
            # nothing is listening, so the request never left this PC.
            refused = isinstance(getattr(exc, "reason", None), ConnectionRefusedError)
            return HttpReply(status=0, text=str(getattr(exc, "reason", exc)), headers={},
                             sent=False if refused else None, retryable=refused)
        except Exception as exc:  # noqa: BLE001 — a read timeout, a dropped answer
            return HttpReply(status=0, text=str(exc), headers={})


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


def _protocol_of(reply: HttpReply) -> str:
    """The ``protocolVersion`` an ``initialize`` answer agreed, or ``""``."""
    decoded = payload_from_body((reply.headers or {}).get("content-type", ""), reply.text)
    result = decoded.get("result") if isinstance(decoded, dict) else None
    if isinstance(result, dict):
        return str(result.get("protocolVersion") or "")
    return ""


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
        sleep: Callable[[float], None] | None = None,
        retry_delays: Iterable[float] | None = None,
    ) -> None:
        self.url = str(url or "")
        self.token = str(token or "")
        self.poster = poster if poster is not None else UrllibPoster()
        self.timeout = float(timeout)
        #: Injected so a test drives the backoff with no real wait.
        self.sleep = sleep if sleep is not None else time.sleep
        self.retry_delays = tuple(RETRY_DELAYS_S if retry_delays is None else retry_delays)
        #: Captured from the ``initialize`` response and sent on every later
        #: request. Without it the daemon answers "send initialize first" to a
        #: harness that DID, and the failure reads as a broken server.
        self.session_id = ""
        #: The coding tool's own handshake, kept to start a new session after
        #: Iron Jarvis forgot this one (MC3), and the protocol version it agreed.
        self._initialize: dict[str, Any] | None = None
        self._initialized: dict[str, Any] | None = None
        self._protocol_version = ""
        self._reinits = 0

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
        method = payload.get("method")
        if method == "initialize" and not is_notification:
            self._initialize = dict(payload)
            # The tool's own start of a session: it carries no old id.
            self.session_id = ""
        elif method == "notifications/initialized" and is_notification:
            self._initialized = dict(payload)
        sent_session = self.session_id
        reply = self._post(payload, retry=not is_notification)
        if is_notification:
            # The daemon answers a notification with an empty 202 and no body.
            # Even if it answered with one, a notification takes no reply.
            return None
        if method == "initialize":
            self._protocol_version = _protocol_of(reply)
        elif sent_session and self._initialize is not None and self._session_lost(reply):
            # MC3: Iron Jarvis restarted (or this session sat idle an hour) and
            # forgot it. Start a new one from the tool's own handshake, then
            # repeat THIS call once; never a loop.
            refusal = self._reinitialise()
            if refusal is not None:
                return self._relay(request_id, refusal)
            reply = self._post(payload, retry=True)
        return self._relay(request_id, reply)

    def _post(self, payload: dict[str, Any], *, retry: bool) -> HttpReply:
        """One POST, capturing a session id from the answer. ``retry``: while
        nothing is listening (the request certainly never left), try again
        after each of :attr:`retry_delays`; any other answer is final."""
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        delays = list(self.retry_delays) if retry else []
        while True:
            reply = self.poster.post(self.url, body, self._headers(), self.timeout)
            if not (reply.status == 0 and getattr(reply, "retryable", False) and delays):
                break
            self.sleep(delays.pop(0))
        session = (reply.headers or {}).get(SESSION_HEADER.lower(), "")
        if session:
            self.session_id = str(session).strip()
        return reply

    @staticmethod
    def _session_lost(reply: HttpReply) -> bool:
        """Did the daemon answer that it does not know the session we sent?

        Matched on the status and the JSON-RPC code, never the wording: 404 is
        the MCP spec's answer for an expired session id; this daemon's door
        answers 400 with ``-32600`` (``server.py``'s ``_door_refusal``)."""
        if reply.status == 404:
            return True
        if reply.status != 400:
            return False
        decoded = payload_from_body((reply.headers or {}).get("content-type", ""), reply.text)
        error = decoded.get("error") if isinstance(decoded, dict) else None
        return isinstance(error, dict) and error.get("code") == INVALID_REQUEST

    def _reinitialise(self) -> HttpReply | None:
        """Start a new session from the coding tool's own handshake (MC3).

        Returns ``None`` when a new session is open (``session_id`` set), or the
        answer to relay instead of repeating the call: a refusal at the token
        check (a closed terminal's token), Iron Jarvis not answering, or a new
        protocol version. Called only with a kept ``initialize``."""
        self._reinits += 1
        replay = dict(self._initialize or {})
        replay["id"] = f"{REINIT_ID_PREFIX}{self._reinits}"
        self.session_id = ""
        reply = self._post(replay, retry=True)
        decoded = payload_from_body((reply.headers or {}).get("content-type", ""), reply.text)
        if not (reply.status == 200 and self.session_id and isinstance(decoded, dict)
                and isinstance(decoded.get("result"), dict)):
            self.session_id = ""
            return reply
        version = _protocol_of(reply)
        if self._protocol_version and version and version != self._protocol_version:
            self.session_id = ""
            return HttpReply(status=0, text=PROTOCOL_CHANGED_MESSAGE, headers={}, sent=False)
        self._post(self._initialized or {"jsonrpc": "2.0", "method": "notifications/initialized"},
                   retry=False)
        return None

    def _relay(self, request_id: Any, reply: HttpReply) -> str:
        """The stdout line for one answer (an envelope, or an error naming why)."""
        if reply.status == 0:
            if getattr(reply, "sent", None) is False:
                if getattr(reply, "retryable", False):
                    message = NOT_ANSWERING_MESSAGE.format(url=self.url)
                else:
                    message = reply.text or NOT_ANSWERING_MESSAGE.format(url=self.url)
            else:
                message = UNREACHABLE_MESSAGE.format(url=self.url)
            return error_line(request_id, INTERNAL_ERROR, message)
        decoded = payload_from_body((reply.headers or {}).get("content-type", ""), reply.text)
        if isinstance(decoded, dict) and decoded.get("jsonrpc") == "2.0" and \
                str(decoded.get("id", "")).startswith(REINIT_ID_PREFIX):
            # The answer to the bridge's own replay: worded for the tool, under
            # the tool's own request id.
            error = decoded.get("error") if isinstance(decoded.get("error"), dict) else {}
            code = error.get("code")
            return error_line(request_id, code if isinstance(code, int) else INTERNAL_ERROR,
                              str(error.get("message") or decoded.get("detail") or
                                  f"HTTP {reply.status}"))
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
    if not is_loopback_url(url):
        # MC7: the pane's token is never sent off this computer.
        err.write(NOT_THIS_PC_MESSAGE + "\n")
        return 2
    factory = shim_factory if shim_factory is not None else StdioShim
    shim = factory(url, token)
    return shim.run(stdin if stdin is not None else _utf8_stdin(),
                    stdout if stdout is not None else _utf8_stdout())


def _utf8_stdin() -> TextIO:
    """``sys.stdin`` re-wrapped as UTF-8 (v1.291.0, io-05).

    MCP stdio is UTF-8 by spec and a Rust/Node harness writes raw UTF-8, but
    ``sys.stdin`` decodes with the LOCALE codec — cp1252 on Windows — so
    "José Muñoz" arrived as mojibake and the five bytes cp1252 leaves undefined
    (0x81 0x8D 0x8F 0x90 0x9D: Á, Í, Ï, Ð, Ý) raised straight out of the read
    loop and killed the shim. ``errors="replace"`` keeps a harness that wrongly
    writes cp1252 alive (U+FFFD instead of a dead pipe). ``newline="\\n"`` so
    the reader splits on the protocol's own delimiter; a stray ``\\r`` is
    stripped by ``handle_line``. Falls back to the stream itself where there is
    no byte buffer (a text stream injected by an embedding harness).
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin
    return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace", newline="\n")


def _utf8_stdout() -> TextIO:
    """``sys.stdout`` re-wrapped as UTF-8, line-buffered (same finding).

    ``newline="\\n"`` on purpose, never ``None``: the default would translate
    the trailing ``\\n`` to CRLF on Windows and a harness that splits on
    ``\\n`` would be handed a ``\\r`` at the end of every JSON line.
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        return sys.stdout
    return io.TextIOWrapper(
        buffer, encoding="utf-8", errors="replace", newline="\n", line_buffering=True
    )


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MCP_TOKEN_ENV",
    "MCP_URL_ENV",
    "NOT_ANSWERING_MESSAGE",
    "NOT_THIS_PC_MESSAGE",
    "NO_TOKEN_MESSAGE",
    "PROTOCOL_CHANGED_MESSAGE",
    "RETRY_DELAYS_S",
    "SESSION_HEADER",
    "UNREACHABLE_MESSAGE",
    "HttpReply",
    "StdioShim",
    "UrllibPoster",
    "error_line",
    "is_loopback_url",
    "main",
    "payload_from_body",
]
