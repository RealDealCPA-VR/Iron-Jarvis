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

v1.420.0 (C3.1, the per-round fixed cost): the LISTENER, the TLS context and the
upstream CONNECTION are no longer made per call. ONE loopback listener per daemon
process (:func:`listener`) serves every call; each call is still its own
:class:`Admission`, registered under its own secret route token, which is the
admission key (an unknown or closed route is a 404 and reaches nothing). ONE ``ssl``
context (:func:`tls_context`) and a small keep-alive pool of upstream connections
(:data:`POOL`) mean the TCP + TLS handshake to Anthropic is paid once, not on every
tool round (measured on this PC: 64-84 ms a round). Every admission rule is
unchanged: the first request only, the one sign-in retry, the proxy handling, the
cache breakpoint pin.
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
import select
import socket
import ssl
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

#: Response headers kept for the answered attempt (v1.301.0, Iron-Proxy accounts):
#: exactly the names Iron-Proxy's limit detector reads to know when a parked account
#: is free again. An ALLOW-list by design — Authorization, cookies and every other
#: header never leave the relay's handler, and these are kept in memory, never logged.
EXPOSED_HEADERS = ("retry-after",)
EXPOSED_HEADER_PREFIXES = ("anthropic-ratelimit-",)


def exposed_headers(pairs: Any) -> dict[str, str]:
    """``{lower-case name: value}`` for the allow-listed response headers only."""
    out: dict[str, str] = {}
    for key, value in pairs or ():
        name = str(key).strip().lower()
        if name in EXPOSED_HEADERS or name.startswith(EXPOSED_HEADER_PREFIXES):
            out[name] = str(value)[:200]
    return out

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


# --------------------------------------------------------------------------- shared parts
#: How long an unused upstream connection is kept for the next call: below any
#: server keep-alive timeout we know of. A connection the server already closed is
#: caught by :func:`_still_open` before it is used.
POOL_IDLE_S = 30.0
#: The most idle upstream connections kept per upstream (concurrent sessions).
POOL_MAX = 4
#: The read timeout for a request to a route that is unknown or closed.
_UNKNOWN_ROUTE_TIMEOUT_S = 30.0

_TLS_CONTEXT: ssl.SSLContext | None = None
_TLS_LOCK = threading.Lock()


def tls_context() -> ssl.SSLContext:
    """THE certificate-verifying context every upstream connection uses (made once)."""
    global _TLS_CONTEXT
    with _TLS_LOCK:
        if _TLS_CONTEXT is None:
            _TLS_CONTEXT = ssl.create_default_context()
        return _TLS_CONTEXT


def _still_open(conn: Any) -> bool:
    """An idle keep-alive connection has nothing to read: a readable socket is one
    the server closed (or that holds bytes nobody asked for), so it is not reused."""
    sock = getattr(conn, "sock", None)
    if sock is None:
        return False
    try:
        if isinstance(sock, ssl.SSLSocket) and sock.pending():
            return False
        readable, _, _ = select.select([sock], [], [], 0)
        return not readable
    except (OSError, ValueError, TypeError):
        return False


def _closed_under_request(exc: BaseException) -> bool:
    """True when a KEPT connection failed the way a connection the server already
    closed fails: a reset/abort/broken pipe on the send, or a close with no status
    line (``RemoteDisconnected``, a ``ConnectionResetError``), or a TLS EOF. A
    timeout (``TimeoutError`` is not a ``ConnectionError``) or a garbled status
    line is NOT: the server had the request."""
    return isinstance(exc, (ConnectionError, ssl.SSLEOFError, ssl.SSLZeroReturnError))


def _close_quietly(conn: Any) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


class UpstreamPool:
    """Idle upstream connections, keyed by where they go. A connection serves ONE
    request at a time (it is taken out while in use) and comes back only when its
    response was read to the end and the server did not ask to close."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.idle: dict[tuple, list[tuple[Any, float]]] = {}

    def take(self, key: tuple) -> Any | None:
        now = time.monotonic()
        stale: list[Any] = []
        found = None
        with self.lock:
            kept = self.idle.get(key) or []
            while kept:
                conn, since = kept.pop()
                if now - since <= POOL_IDLE_S and _still_open(conn):
                    found = conn
                    break
                stale.append(conn)
            if not kept:
                self.idle.pop(key, None)
        for conn in stale:
            _close_quietly(conn)
        return found

    def give(self, key: tuple, conn: Any) -> None:
        with self.lock:
            kept = self.idle.setdefault(key, [])
            if len(kept) < POOL_MAX:
                kept.append((conn, time.monotonic()))
                return
        _close_quietly(conn)

    def clear(self) -> None:
        with self.lock:
            idle, self.idle = self.idle, {}
        for kept in idle.values():
            for conn, _ in kept:
                _close_quietly(conn)


#: The process's upstream connection pool.
POOL = UpstreamPool()


class RelayServer(ThreadingHTTPServer):
    """THE loopback listener: each request goes to the :class:`Admission` registered
    under its secret path. One thread per request, so sessions stream side by side."""

    daemon_threads = True
    #: Review (v1.420.0): the stdlib default (SO_REUSEADDR) lets another process
    #: bind the SAME 127.0.0.1 port on Windows and take connections meant for it
    #: — and this one port now lives as long as the daemon and carries every
    #: call's sign-in header. Bound EXCLUSIVELY instead.
    allow_reuse_address = False

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.routes: dict[str, "Admission"] = {}
        self.routes_lock = threading.Lock()

    def register(self, prefix: str, gate: "Admission") -> None:
        with self.routes_lock:
            self.routes[prefix] = gate

    def unregister(self, prefix: str) -> None:
        with self.routes_lock:
            self.routes.pop(prefix, None)

    def route(self, prefix: str) -> "Admission | None":
        with self.routes_lock:
            return self.routes.get(prefix)


_LISTENER: RelayServer | None = None
_LISTENER_LOCK = threading.Lock()


def listener() -> RelayServer:
    """The process's one relay listener, started on first use (a daemon thread)."""
    global _LISTENER
    with _LISTENER_LOCK:
        if _LISTENER is None:
            server = RelayServer()
            threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.25},
                daemon=True, name="claude-relay",
            ).start()
            _LISTENER = server
        return _LISTENER


class Admission:
    """One call's admission: exactly one upstream Messages request (v1.420.0: a route
    on the shared :func:`listener`, no longer a listener of its own)."""

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
        #: The answered attempt's allow-listed response headers (:func:`exposed_headers`).
        self.headers: dict[str, str] = {}
        #: True once the admitted request's whole answer has been relayed back.
        self.answered = False
        #: True once the ONE sign-in retry (see :meth:`_reauth_allowed`) was admitted.
        self.reauth = False
        self.capture = Capture()
        self.error_body = b""
        #: ``time.perf_counter`` stamps of the admitted request (v1.420.0, the
        #: per-phase trace): request, connected, headers, first_byte,
        #: last_byte, plus ``reused`` (1.0 when a kept connection was used).
        self.stamps: dict[str, float] = {}
        self._closed = False
        #: Requests of this admission still being handled (close() waits for them).
        self._active = 0
        self._idle = threading.Condition(self.lock)
        self.prefix = "/admit/" + secrets.token_urlsafe(32)
        self.server = listener()
        self.url = f"http://127.0.0.1:{self.server.server_port}" + self.prefix
        self.server.register(self.prefix, self)

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
        """Abort, take this call's route off the shared listener (a later request to
        it is a 404 that reaches nothing) and wait for its requests still being
        handled. Idempotent."""
        self.abort()
        if self._closed:
            return
        self._closed = True
        self.server.unregister(self.prefix)
        with self.lock:
            # abort() shut every socket of this call, so a handler still running
            # ends at once; the bound only guards a wedged write.
            self._idle.wait_for(lambda: self._active == 0, timeout=10.0)

    def _enter(self) -> None:
        with self.lock:
            self._active += 1

    def _leave(self) -> None:
        with self.lock:
            self._active -= 1
            self._idle.notify_all()


#: The most a REFUSED request's body is read before the refusal (a wrong route
#: or a browser Origin is answered without trusting its Content-Length).
_REFUSED_BODY_CAP = 1 << 20


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass  # Native authorization and the per-call route must never enter logs.

    def _read_body(self, cap: int | None = None) -> tuple[bytes, BaseException | None]:
        """The request body (``Content-Length`` bytes), read BEFORE any reply.

        Windows resets a connection that is closed with unread bytes in its
        receive buffer (WinError 10053/10054 on the client) — a local refusal
        sent without reading the body raced the client's read of it. Over
        ``cap`` the body is left unread (a refusal's peer is not trusted)."""
        try:
            length = int(self.headers["Content-Length"])
            if length < 0 or (cap is not None and length > cap):
                return b"", None
            return self.rfile.read(length), None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return b"", exc

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        path = urlsplit(self.path)
        gate: Admission | None = None
        if path.path.endswith("/v1/messages") and not self.headers.get("Origin"):
            gate = self.server.route(path.path[: -len("/v1/messages")])  # type: ignore[attr-defined]
        if gate is None:
            try:
                self.connection.settimeout(_UNKNOWN_ROUTE_TIMEOUT_S)
            except OSError:
                pass
            self._read_body(_REFUSED_BODY_CAP)
            self.send_error(404)
            return
        gate._enter()
        try:
            self._admit(gate, path)
        finally:
            gate._leave()

    def _upstream(self, gate: "Admission", reuse: bool) -> tuple[Any, tuple, bool]:
        """``(connection, pool key, reused)`` for the upstream — a kept one when
        ``reuse`` and the pool has one still open, else a new, connected one."""
        target = gate.upstream
        proxy = proxy_for(target) if target.scheme == "https" else None
        key = (
            target.scheme, target.hostname, target.port,
            None if proxy is None else (proxy[0], proxy[1], tuple(sorted(proxy[2].items()))),
        )
        if reuse:
            kept = POOL.take(key)
            if kept is not None:
                try:
                    kept.timeout = gate.timeout
                    kept.sock.settimeout(gate.timeout)
                    return kept, key, True
                except (OSError, AttributeError):
                    _close_quietly(kept)
        if target.scheme == "https":
            context = tls_context()
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
        return conn, key, False

    def _admit(self, gate: "Admission", path: Any) -> None:
        # Read the body BEFORE deciding (and before gate.lock): a refusal must
        # never reply over unread bytes, and the admitted request uses it as is.
        try:
            self.connection.settimeout(gate.timeout)
        except OSError:
            pass
        body_in, read_error = self._read_body()
        with gate.lock:
            if gate.used and gate._reauth_allowed():
                # The sign-in retry: start the record over for the answer that counts.
                gate.reauth = True
                gate.answered = False
                gate.status = gate.failure = gate.failure_network = None
                gate.retry_after = gate.request_id = None
                gate.headers = {}
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
            gate.stamps = {"request": time.perf_counter()}
        conn: http.client.HTTPConnection | None = None
        upstream_socket = None
        try:
            if read_error is not None:
                raise read_error
            payload = pin_message_breakpoint(body_in, gate.queried)
            target = gate.upstream
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
            for attempt in (1, 2):
                conn, pool_key, reused = self._upstream(gate, reuse=attempt == 1)
                gate.stamps["connected"] = time.perf_counter()
                gate.stamps["reused"] = 1.0 if reused else 0.0
                upstream_socket = conn.sock
                with gate.lock:
                    if gate.cancelled:
                        return
                    gate.sockets.add(upstream_socket)
                try:
                    conn.request("POST", route, payload, headers)
                    response = conn.getresponse()
                    break
                except (OSError, http.client.HTTPException) as exc:
                    # A KEPT connection the server closed between the open-check and
                    # the request (its keep-alive ran out): nothing was answered, so
                    # the server never took the request. Once, on a new connection --
                    # the same request the router's own retry would send, without a
                    # new CLI. A new connection that fails is the failure. ONLY the
                    # closed-connection shapes (review, v1.420.0): a TIMEOUT waiting
                    # for the answer or a garbled answer means the server READ the
                    # whole request and may be running it on the user's account, so
                    # it is never sent a second time.
                    with gate.lock:
                        gate.sockets.discard(upstream_socket)
                        cancelled = gate.cancelled
                    _close_quietly(conn)
                    conn = None
                    if not reused or cancelled or not _closed_under_request(exc):
                        raise
                    log.debug("claude admission: a kept upstream connection was closed; using a new one")
            del headers, payload, body_in
            gate.stamps["headers"] = time.perf_counter()
            gate.request_id = response.getheader("request-id") or response.getheader("x-request-id")
            gate.status = response.status
            gate.retry_after = response.getheader("retry-after")
            gate.headers = exposed_headers(response.getheaders())
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
                    gate.stamps.setdefault("first_byte", time.perf_counter())
                    gate.capture.feed(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                gate.stamps["last_byte"] = time.perf_counter()
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
                gate.sockets.discard(upstream_socket)
                keep = not gate.cancelled and response.isclosed() and not response.will_close
            if keep:
                # Read to the end and the server keeps the connection open: the
                # next call reuses it (no new TCP + TLS handshake).
                POOL.give(pool_key, conn)
                conn = None
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
