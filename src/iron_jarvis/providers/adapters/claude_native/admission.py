"""Request-scoped LOCAL ADMISSION RELAY for the Claude-subscription path.

Ported from NousResearch/hermes-plugin-claude-subscription-directsdk (MIT, commit
ef73726) — admission.py. Copyright (c) the original authors; see the MIT notice below.

What it is for. Claude Code, given ``ANTHROPIC_BASE_URL=<Admission.url>``, sends its
Messages request to this loopback HTTP server (127.0.0.1, ephemeral port, a random
per-request route token). The relay forwards ONLY THE FIRST request upstream (to
api.anthropic.com or the inherited base URL), streams the answer straight back,
captures the streamed assistant message incrementally (text, signed thinking, tool_use
with assembled arguments, usage, stop_reason) and REJECTS every later attempt locally.
Native recovery/retries would otherwise make extra upstream requests on the user's
subscription (CLI 2.1.288 on this machine reported ``num_turns`` 2 for a one-step tool
call).

It also moves the single message ``cache_control`` breakpoint off the per-request
context native appends (:func:`pin_message_breakpoint`), so the next tool round reads
the cached prefix instead of re-writing it (without it a follow-up step created 4,983
cache tokens and read 0).

Credentials are forwarded, never persisted: request headers (Authorization, x-api-key,
...) live only in memory for the one upstream call. Nothing here logs a header, a body
or the per-request route — the stdlib access log is silenced and the module logger
(``ironjarvis.claude_native``) only ever records counts, statuses and exception type
names.

Adaptations from the original, behaviour otherwise identical: imports, typing, module
logging, the constructor's first parameter is named ``upstream_base_url``, and
``close()`` is guarded so a second call is a no-op. Additions (v1.300.0 review): the
relay records whether a failure was the NETWORK's (``failure_network``), the upstream's
``Retry-After`` and error ``type`` (``error_type()``), so the transport can tell "the
connection dropped, try again" from "Anthropic refused" from "you are signed out"; and
an HTTPS upstream honours the standard proxy ENVIRONMENT (``HTTPS_PROXY`` / ``ALL_PROXY``
/ ``NO_PROXY``) through a CONNECT tunnel, as the Node CLI did before the relay stood
in front of it (:func:`proxy_for`).
"""

from __future__ import annotations

import base64
import codecs
import copy
import http.client
import ipaddress
import json
import logging
import re
import secrets
import socket
import ssl
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

log = logging.getLogger("ironjarvis.claude_native")


def proxy_for(target: Any) -> tuple[str, int, dict[str, str]] | None:
    """``(proxy host, proxy port, CONNECT headers)`` for an HTTPS upstream, else None.

    The standard proxy ENVIRONMENT only (``HTTPS_PROXY``, then ``ALL_PROXY``, upper or
    lower case; ``NO_PROXY`` exempts a host) — what the Node CLI honoured when it spoke
    to Anthropic itself. Deliberately not ``urllib.request.getproxies()``: on Windows
    that falls back to the system (registry) proxy, which the CLI never read, so the
    same machine would have routed differently. The relay runs IN the daemon, so the
    daemon's own ``os.environ`` is what counts (the desktop shell spawns the daemon
    with its full environment). A proxy URL with credentials becomes a
    ``Proxy-Authorization: Basic`` header on the CONNECT — never logged. A plain-HTTP
    upstream (a loopback test fixture) is never proxied."""
    if getattr(target, "scheme", "") != "https" or not getattr(target, "hostname", None):
        return None
    proxies = urllib.request.getproxies_environment()
    raw = (proxies.get("https") or proxies.get("all") or "").strip()
    if not raw:
        return None
    if urllib.request.proxy_bypass_environment(target.hostname, proxies):
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        host = None
    if not host:
        log.info("claude admission: ignoring a malformed HTTPS proxy setting")
        return None
    headers: dict[str, str] = {}
    if parts.username is not None:
        creds = f"{unquote(parts.username)}:{unquote(parts.password or '')}"
        headers["Proxy-Authorization"] = "Basic " + base64.b64encode(creds.encode("utf-8")).decode("ascii")
    return host, port, headers

#: Block types that must never carry the message breakpoint.
UNCACHEABLE = ("thinking", "redacted_thinking")

#: The sentinel message a locally refused (non-first) attempt receives — the single
#: source for it. The error SHAPE is the original's verbatim (invalid_request_error,
#: HTTP 400); only the text is ours (upstream used HERMES_MODEL_ADMISSION_CONSUMED).
CONSUMED_MESSAGE = "IRONJARVIS_MODEL_ADMISSION_CONSUMED"
_CONSUMED_BODY = (
    b'{"type":"error","error":{"type":"invalid_request_error","message":"'
    + CONSUMED_MESSAGE.encode("ascii")
    + b'"}}'
)


def _plain(block: Any) -> Any:
    return {k: v for k, v in block.items() if k != "cache_control"} if isinstance(block, dict) else block


def pin_message_breakpoint(payload: bytes | str, queried: list | None) -> bytes | str:
    """Keep the single message ``cache_control`` on content the next request replays unchanged.

    Native attaches per-request context (today's date, the account-email reminder, whatever a
    later CLI adds) to the turn it answers and puts the message breakpoint on or after it. The
    next request replays that turn without it, so the cached prefix never recurs and every
    tool round re-writes the whole history (upstream issue #14, second cause).

    What does recur is known without reading native's text: everything through the last
    assistant message, plus the leading blocks of the newest turn that equal the frame the
    host queried. The first block native added or changed ends that span. When that block is
    a tool_result (parallel calls, native's reminder on the last result), a breakpoint on the
    unchanged results before it measured no cache hit even though they replay byte-identical
    (upstream #33, cause unknown), so the span ends at the preceding assistant message.
    The breakpoint never moves later, content never changes, and any payload that does not
    parse forwards as is (the input object is returned unchanged)."""
    if not queried:
        return payload
    try:
        body = json.loads(payload)
        messages = body["messages"]
        blocks = [
            (i, j, b)
            for i, m in enumerate(messages)
            if isinstance(m.get("content"), list)
            for j, b in enumerate(m["content"])
        ]
        marked = [(i, j, b) for i, j, b in blocks if isinstance(b, dict) and "cache_control" in b]
        if len(marked) != 1:
            return payload
        last = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        stable = [(i, j, b) for i, j, b in blocks if i <= last]
        newest = messages[last + 1] if last + 1 < len(messages) else {}
        if newest.get("role") == "user" and isinstance(newest.get("content"), list):
            prefix = []
            for j, (sent, host) in enumerate(zip(newest["content"], queried)):
                if _plain(sent) != _plain(host):
                    break
                prefix.append((last + 1, j, sent))
            rest = newest["content"][len(prefix):]
            if not (rest and isinstance(rest[0], dict) and rest[0].get("type") == "tool_result"):
                stable += prefix
        target = next(
            (
                (i, j, b)
                for i, j, b in reversed(stable)
                if isinstance(b, dict) and b.get("type") not in UNCACHEABLE
            ),
            None,
        )
        i, j, block = marked[0]
        if target is None or (target[0], target[1]) >= (i, j):
            return payload
        target[2]["cache_control"] = block.pop("cache_control")
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return payload


class Capture:
    """Incremental SSE assembler for one native Messages stream."""

    def __init__(self) -> None:
        self.message: dict | None = None
        self.complete = False
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.arguments: dict[int, str] = {}

    def feed(self, chunk: bytes) -> None:
        self.pending += self.decoder.decode(chunk)
        while match := re.search(r"\r?\n\r?\n", self.pending):
            frame, self.pending = self.pending[: match.start()], self.pending[match.end():]
            data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
            if data:
                self.event(json.loads(data))

    def event(self, event: dict) -> None:
        handler = {
            "message_start": self._start,
            "content_block_start": self._block_start,
            "content_block_delta": self._block_delta,
            "content_block_stop": self._block_stop,
            "message_delta": self._delta,
            "message_stop": self._stop,
        }.get(event["type"])
        if handler:
            handler(event)

    def _start(self, event: dict) -> None:
        self.message = copy.deepcopy(event["message"])

    def _block_start(self, event: dict) -> None:
        self.message["content"].append(copy.deepcopy(event["content_block"]))

    def _block_delta(self, event: dict) -> None:
        delta = event["delta"]
        block = self.message["content"][event["index"]]
        field = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}.get(delta["type"])
        if field:
            block[field] = block.get(field, "") + delta[field]
        elif delta["type"] == "input_json_delta":
            index = event["index"]
            self.arguments[index] = self.arguments.get(index, "") + delta["partial_json"]
        elif delta["type"] == "citations_delta":
            block.setdefault("citations", []).append(copy.deepcopy(delta["citation"]))

    def _block_stop(self, event: dict) -> None:
        index = event["index"]
        if index in self.arguments:
            # A no-argument tool call streams one input_json_delta with an empty partial_json.
            raw = self.arguments.pop(index)
            self.message["content"][index]["input"] = json.loads(raw) if raw.strip() else {}

    def _delta(self, event: dict) -> None:
        self.message.update(event.get("delta", {}))
        self.message["usage"].update(event.get("usage", {}))

    def _stop(self, event: dict) -> None:
        self.complete = bool(self.message and self.message.get("stop_reason") and not self.arguments)


class Admission:
    """One loopback relay that admits exactly one upstream Messages request."""

    def __init__(self, upstream_base_url: str, timeout: float, queried: list | None = None) -> None:
        self.upstream = urlsplit(upstream_base_url)
        self.queried = queried
        host = self.upstream.hostname
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if (
            (self.upstream.scheme != "https" and not (self.upstream.scheme == "http" and local))
            or not host
            or self.upstream.username
            or self.upstream.password
            or self.upstream.query
            or self.upstream.fragment
        ):
            raise ValueError("Native upstream must be HTTPS or a loopback HTTP fixture")
        self.timeout = timeout
        self.lock = threading.Lock()
        self.sockets: set[socket.socket] = set()
        self.cancelled = False
        self.used = False
        self.denied = 0
        self.request_id: str | None = None
        self.status: int | None = None
        self.failure: str | None = None
        #: True when ``failure`` was the network's (OSError / HTTPException: a reset,
        #: a drop, a timeout, DNS, TLS, a refused proxy) — False for a local error.
        self.failure_network: bool | None = None
        self.retry_after: str | None = None
        #: True once the admitted request's whole answer has been relayed back.
        self.answered = False
        #: True once the ONE sign-in retry (see :meth:`_reauth_allowed`) was admitted.
        self.reauth = False
        self.capture = Capture()
        self.error_body = b""
        self._closed = False
        self.prefix = "/admit/" + secrets.token_urlsafe(32)
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.admission = self  # type: ignore[attr-defined]
        self.url = f"http://127.0.0.1:{self.server.server_port}" + self.prefix
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def error_text(self) -> str:
        """The upstream's own message for a non-200 answer, '' when none was captured."""
        text = self.error_body.decode("utf-8", errors="replace")
        try:
            message = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            return text
        return message if isinstance(message, str) else text

    def _reauth_allowed(self) -> bool:
        """Under ``lock``: may a SECOND request go upstream? Only once, and only
        after the first was answered with an authentication refusal (401, or a
        403 typed ``authentication_error``). The CLI answers such a refusal by
        refreshing its sign-in and sending the request again; that retry is the
        one that can succeed, and the refusal spent nothing — so admitting it
        cannot duplicate work. Every other retry stays refused (v1.300.0)."""
        if self.cancelled or not self.answered or self.reauth:
            return False
        return self.status == 401 or (self.status == 403 and self.error_type() == "authentication_error")

    def error_type(self) -> str:
        """The upstream's error ``type`` ('authentication_error', ...), '' when none."""
        try:
            kind = json.loads(self.error_body.decode("utf-8", errors="replace"))["error"]["type"]
        except (ValueError, KeyError, TypeError):
            return ""
        return kind if isinstance(kind, str) else ""

    def abort(self) -> None:
        """Refuse everything from now on and shut any active client/upstream socket."""
        with self.lock:
            self.cancelled = True
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Peer may have closed between the read and cancellation.

    def close(self) -> None:
        """Abort, stop the listener and release its port. Idempotent."""
        self.abort()
        if self._closed:
            return
        self._closed = True
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass  # Native authorization and the per-call route must never enter logs.

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        gate: Admission = self.server.admission  # type: ignore[attr-defined]
        path = urlsplit(self.path)
        if path.path != gate.prefix + "/v1/messages" or self.headers.get("Origin"):
            self.send_error(404)
            return
        with gate.lock:
            if gate.used and gate._reauth_allowed():
                # The sign-in retry: start the record over for the answer that counts.
                gate.reauth = True
                gate.answered = False
                gate.status = gate.failure = gate.failure_network = None
                gate.retry_after = gate.request_id = None
                gate.error_body = b""
                gate.capture = Capture()
                log.debug("claude admission: admitted the CLI's one retry after a sign-in refusal")
            elif gate.cancelled or gate.used:
                gate.denied += 1
                body = _CONSUMED_BODY
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                log.debug("claude admission: refused a non-first attempt locally (denied=%d)", gate.denied)
                return
            gate.used = True
            gate.sockets.add(self.connection)
        conn: http.client.HTTPConnection | None = None
        upstream_socket = None
        try:
            self.connection.settimeout(gate.timeout)
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            payload = pin_message_breakpoint(payload, gate.queried)
            target = gate.upstream
            if target.scheme == "https":
                proxy = proxy_for(target)
                context = ssl.create_default_context()
                if proxy is None:
                    conn = http.client.HTTPSConnection(
                        target.hostname, target.port, timeout=gate.timeout, context=context
                    )
                else:
                    # TLS to the upstream THROUGH a CONNECT tunnel: the certificate is
                    # still verified against the upstream's own hostname.
                    proxy_host, proxy_port, tunnel_headers = proxy
                    conn = http.client.HTTPSConnection(
                        proxy_host, proxy_port, timeout=gate.timeout, context=context
                    )
                    conn.set_tunnel(target.hostname, target.port or 443, headers=tunnel_headers or None)
                    log.debug("claude admission: upstream reached through an HTTPS proxy")
            else:
                conn = http.client.HTTPConnection(target.hostname, target.port, timeout=gate.timeout)
            conn.connect()
            upstream_socket = conn.sock
            with gate.lock:
                if gate.cancelled:
                    return
                gate.sockets.add(upstream_socket)
            # Request identity and payload remain native; only HTTP transfer encoding changes.
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower()
                not in (
                    "host",
                    "connection",
                    "content-length",
                    "transfer-encoding",
                    "proxy-authorization",
                    "proxy-connection",
                    "accept-encoding",
                )
            }
            headers["Accept-Encoding"] = "identity"
            route = target.path.rstrip("/") + "/v1/messages" + ("?" + path.query if path.query else "")
            conn.request("POST", route, payload, headers)
            del headers, payload
            response = conn.getresponse()
            gate.request_id = response.getheader("request-id") or response.getheader("x-request-id")
            gate.status = response.status
            gate.retry_after = response.getheader("retry-after")
            log.debug("claude admission: upstream answered status %s", response.status)
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ("connection", "transfer-encoding", "server", "date"):
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if response.status == 200:
                while True:
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    gate.capture.feed(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                # A refusal is small: read ALL of it, record it (the reason, bounded)
                # and mark it answered BEFORE the CLI sees a byte — the CLI may retry
                # the instant it has read the body, and the sign-in retry is decided
                # from this record (``_reauth_allowed``).
                data = b""
                while True:
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    data += chunk
                with gate.lock:
                    gate.error_body = data[:65536]
                    gate.answered = True
                self.wfile.write(data)
                self.wfile.flush()
            with gate.lock:
                gate.answered = True
        except (OSError, http.client.HTTPException, ValueError, KeyError, IndexError, TypeError) as exc:
            gate.failure = type(exc).__name__
            gate.failure_network = isinstance(exc, (OSError, http.client.HTTPException))
            log.debug("claude admission: relay failure %s", gate.failure)
        finally:
            with gate.lock:
                gate.sockets.discard(self.connection)
                gate.sockets.discard(upstream_socket)
            if conn:
                conn.close()
            self.close_connection = True


# --- Original licence (MIT), reproduced as the licence requires -----------------------
#
# MIT License
#
# Copyright (c) 2026 Nous Research and contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
