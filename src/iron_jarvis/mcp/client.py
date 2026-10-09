"""Minimal MCP (Model Context Protocol) client (§ external tool consumption).

Iron Jarvis is an MCP *client*: it consumes tools exposed by external MCP
*servers* (Gmail / Drive / GitHub / ...). This module speaks JSON-RPC 2.0 with
the two methods every MCP server implements:

* ``tools/list``  -> the server's tool catalogue (name / description / inputSchema)
* ``tools/call``  -> invoke one tool and return its ``content`` blocks

The wire protocol is **dependency-injected** through a *transport*: an object
exposing ``request(method, params) -> dict`` (sync or async) that owns the
JSON-RPC framing and returns the MCP *result* payload (or raises). This keeps
the client trivially testable — tests inject :class:`FakeTransport` with canned
responses and **never** spawn a process or open a socket. Two real transports
are provided for production: :class:`StdioTransport` (subprocess, line-delimited
JSON-RPC) and :class:`HttpTransport` (lazy ``httpx``, JSON or SSE body).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import queue
import subprocess
import threading
import time
from types import EllipsisType
from typing import Any, Callable

from ..core.logging import get_logger
from .interact import current_scope, handle_notification, plain_reply, serve_server_request

log = get_logger("mcp")

#: MCP protocol revision advertised in the ``initialize`` handshake.
PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "iron-jarvis", "version": "0"}


def client_capabilities() -> dict[str, Any]:
    """What this client tells a server it can do (v1.324.0), on BOTH
    transports: a pack may ask the user (``elicitation`` — ``{}`` is form mode
    per spec) and ask a model (``sampling``). Whether anyone ANSWERS is
    decided per call by ``mcp/interact`` (an attended stream turn only)."""
    return {"elicitation": {}, "sampling": {}}

#: How long :class:`StdioTransport` waits for ONE answer before it kills the
#: server (v1.291.0, io-02) — the default for a transport built with no
#: explicit ``request_timeout``, read at CONSTRUCTION time (so a test can pin
#: it). It bounds the callers that carry no deadline of their own (the LTM
#: brain driven from sync code) so a wedged server can never park a worker
#: thread on its pipe for ever. REGISTRY PACKS DO NOT USE IT: ``mcp/tools.
#: _build_transport`` passes ``None``, because those calls are bounded by the
#: registry's own deadline (``config.tool_call_timeout_s``, 600 s by default,
#: set in Settings) which aborts the call through :meth:`StdioTransport.abort`
#: — a transport floor shorter than that deadline would kill a slow-but-healthy
#: pack (a browser pack loading pages, a long repo search) at the floor and
#: silently override the deadline the user configured.
DEFAULT_REQUEST_TIMEOUT_S = 120.0

#: Grace given to a killed server before its pipes are closed under it.
_KILL_GRACE_S = 2.0

#: How many ``tools/list`` pages :meth:`MCPClient.list_tools` follows before it
#: stops (v1.322.0). A server whose ``nextCursor`` never runs out would
#: otherwise page for ever; past the cap the tools already listed are kept and
#: the stop is LOGGED with the count, never passed off as the whole catalogue.
MAX_TOOL_LIST_PAGES = 50

#: Seconds an :class:`HttpTransport` may spend ESTABLISHING a connection
#: (TCP + TLS) — short on purpose: an unreachable host says so quickly. The
#: READ timeout is separate and follows the caller's deadline (v1.322.0).
HTTP_CONNECT_TIMEOUT_S = 10.0

#: Slack added to the registry deadline when it becomes an HTTP read timeout
#: (v1.322.0): the registry's ``asyncio.timeout`` must fire FIRST, so the user
#: reads "did not finish within N s" (the deadline they set) rather than an
#: httpx timeout that raced it; the worker thread unwinds this long after.
_DEADLINE_GRACE_S = 5.0

#: JSON-RPC "method not found" — the answer to a server→client request this
#: client does not serve (v1.322.0).
_METHOD_NOT_FOUND = -32601


# --------------------------------------------------------------------------- #
# JSON-RPC helpers (shared by the real transports).
# --------------------------------------------------------------------------- #
def _envelope(request_id: int, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def _extract_result(response: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a JSON-RPC response, raising on a JSON-RPC ``error``."""
    if not isinstance(response, dict):
        raise MCPError(f"malformed JSON-RPC response: {response!r}")
    if response.get("error") is not None:
        err = response["error"]
        if isinstance(err, dict):
            raise MCPError(f"{err.get('code', '?')}: {err.get('message', err)}")
        raise MCPError(str(err))
    result = response.get("result")
    return result if isinstance(result, dict) else {}


# --------------------------------------------------------------------------- #
# Message routing (v1.322.0) — shared by the stdio and HTTP transports.
#
# Between sending a request and reading its answer a server may send OTHER
# messages: notifications (``notifications/progress``, ``notifications/message``
# logging) and even its own REQUESTS to the client (``ping``, ``roots/list``,
# ``sampling/createMessage``). Only the message that carries OUR id and no
# ``method`` is the answer. A server request is checked FIRST: servers number
# their requests in their own id space, so one can collide with ours.
# --------------------------------------------------------------------------- #
_RESPONSE, _SERVER_REQUEST, _SKIP = "response", "request", "skip"
#: A server notification (v1.324.0): no reply, handed to
#: ``interact.handle_notification`` (progress for the call in flight).
_NOTIFICATION = "notification"


def _route_message(msg: Any, expected_id: Any) -> str:
    """Classify one incoming JSON-RPC message against the id we are waiting on."""
    if not isinstance(msg, dict):
        return _SKIP
    if "method" in msg:
        # A request carries an id; a notification has none and takes no reply.
        return _SERVER_REQUEST if msg.get("id") is not None else _NOTIFICATION
    if "id" not in msg or msg["id"] is None:
        return _SKIP
    rid = msg["id"]
    if rid == expected_id or (
        isinstance(rid, str) and not isinstance(expected_id, str) and rid == str(expected_id)
    ):
        return _RESPONSE
    return _SKIP  # an answer to some other id (a stale or foreign response)


def _server_request_reply(msg: dict[str, Any]) -> dict[str, Any]:
    """The reply this client owes a server→client request WITHOUT an
    interaction scope (the v1.322.0 behaviour, kept as the fallback).

    ``ping`` is answered with the empty result the spec prescribes. Anything
    else is refused with -32601 so the server is never left WAITING on an
    answer that will not come (a server that awaits its request before it
    finishes ours would otherwise hang the call until the deadline). The
    transports now answer through ``interact.serve_server_request``, which
    adds elicitation and sampling (v1.324.0) and falls back to this for
    everything else.
    """
    return plain_reply(msg)


def _decode_sse_event(data: list[str]) -> list[Any]:
    """The JSON-RPC message(s) carried by one SSE event's ``data:`` lines.

    Per the SSE spec the lines of ONE event join with ``\\n`` into a single
    payload. A server that instead wrote one complete message per ``data:``
    line inside one event is tolerated by decoding the lines one by one. A
    JSON-RPC batch (a list) yields its members.
    """
    if not data:
        return []
    joined = "\n".join(data).strip()
    if not joined or joined == "[DONE]":
        return []
    decoded: list[Any] = []
    try:
        decoded.append(json.loads(joined))
    except json.JSONDecodeError:
        for piece in data:
            piece = piece.strip()
            if not piece or piece == "[DONE]":
                continue
            try:
                decoded.append(json.loads(piece))
            except json.JSONDecodeError:
                continue
    out: list[Any] = []
    for item in decoded:
        if isinstance(item, list):
            out.extend(item)
        else:
            out.append(item)
    return out


def _sse_messages(lines: Any) -> Any:
    """Yield every JSON-RPC message in a ``text/event-stream`` body, in order,
    as each event COMPLETES — so a streaming reader can act on a server
    request before the stream ends."""
    data: list[str] = []
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)).strip()
        if not line:  # a blank line ends the event
            yield from _decode_sse_event(data)
            data = []
            continue
        if line.startswith(":"):
            continue  # an SSE comment / keep-alive
        if line.startswith("data:"):
            chunk = line[len("data:"):]
            data.append(chunk[1:] if chunk.startswith(" ") else chunk)
        # event:/id:/retry: fields carry nothing this client needs.
    yield from _decode_sse_event(data)


def _call_read_timeout() -> "float | None | EllipsisType":
    """The read timeout the CURRENT call's registry deadline implies (v1.322.0).

    ``ToolRegistry.invoke`` publishes its ``asyncio.timeout`` scope in
    ``tools.registry._DEADLINE_SCOPE`` for exactly the duration of the tool's
    ``execute``. Returns:

    * ``...``  — no registry scope (a boot ``tools/list``, the LTM brain, a
      direct caller): the transport keeps its own default;
    * ``None`` — a scope with no deadline (the user set
      ``tool_call_timeout_s`` to 0): unbounded, exactly as the stdio path
      waits with ``request_timeout=None``;
    * seconds — what is LEFT of the deadline plus :data:`_DEADLINE_GRACE_S`,
      so the registry's own deadline fires first and reports it honestly.
    """
    try:
        from ..tools.registry import _DEADLINE_SCOPE
    except Exception:  # noqa: BLE001 — a client outside the app (scripts, tests)
        return ...
    scope = _DEADLINE_SCOPE.get()
    if scope is None:
        return ...
    when = scope.when()
    if when is None:
        return None
    try:
        now = asyncio.get_running_loop().time()
    except RuntimeError:  # pragma: no cover — always called from a coroutine
        return ...
    return max(0.0, float(when) - now) + _DEADLINE_GRACE_S


class MCPError(RuntimeError):
    """A protocol- or transport-level MCP failure."""


class MCPServerStopped(MCPError):
    """The server process is gone (it exited, was killed, or hung past its
    deadline). The transport has already forgotten it: the NEXT call respawns
    it. The failed call is never retried by the transport — a ``tools/call``
    may have had side effects before the server died (v1.291.0, io-06)."""


# --------------------------------------------------------------------------- #
# The client.
# --------------------------------------------------------------------------- #
class MCPClient:
    """Talk to a single external MCP server over an injected ``transport``.

    ``transport.request(method, params)`` may be synchronous or a coroutine; both
    are awaited transparently. It must return the JSON-RPC *result* object for the
    call (the real transports unwrap the envelope; :class:`FakeTransport` returns
    canned results directly).

    A SYNCHRONOUS transport runs on a worker thread (v1.291.0, io-02): both real
    transports block — a pipe ``readline`` or an ``httpx`` post — and the daemon
    has ONE event loop, so calling them inline froze chat, the dashboard and the
    phone for the whole duration of every pack call, and a hung pack read as
    "Daemon offline". Off the loop, ``registry.invoke``'s ``asyncio.timeout``
    deadline can actually fire; when it does (or the user cancels) the thread is
    still parked on the read, so a transport that exposes ``abort(token)`` is
    told to give up: it kills its server, which wakes the thread, and the next
    call restarts the server. Such a transport's ``request`` must accept the
    keyword ``cancel=`` (a ``threading.Event``) — that token is how ``abort``
    tells the in-flight call apart from one still waiting its turn.
    """

    def __init__(self, transport: Any, name: str = "server") -> None:
        self.transport = transport
        self.name = name
        #: Set by :meth:`close`. A closed stdio transport RESPAWNS its server
        #: on the next call, so ``mcp.tools.live_client`` never hands out a
        #: client that was closed (a replaced or discarded pack).
        self.closed = False
        # The transport answers the pack's own requests (v1.324.0) and the
        # card says WHICH app is asking: it needs the pack's name, which only
        # the client knows. Best-effort: a transport that takes no attribute
        # still works, it just reads as "mcp".
        try:
            transport.pack_name = name
        except Exception:  # noqa: BLE001 — __slots__, a read-only double
            pass

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        abort = getattr(self.transport, "abort", None)
        if inspect.iscoroutinefunction(getattr(self.transport, "request", None)):
            result = await self.transport.request(method, params)
        elif callable(abort):
            cancel = threading.Event()
            try:
                result = await asyncio.to_thread(
                    self.transport.request, method, params, cancel=cancel
                )
            except MCPServerStopped as exc:
                # Name the pack: the transport only knows its command line.
                raise MCPServerStopped(f"pack {self.name!r}: {exc}") from exc
            except asyncio.CancelledError:
                cancel.set()
                # Off the loop (a tree kill may spawn taskkill) and shielded so
                # a second cancel cannot abandon the kill half-way.
                try:
                    await asyncio.shield(asyncio.to_thread(abort, cancel))
                except asyncio.CancelledError:
                    pass
                raise
        else:
            kw: dict[str, Any] = {}
            # A transport that takes a per-call read timeout (HttpTransport)
            # is handed the one the registry deadline implies (v1.322.0).
            if getattr(self.transport, "accepts_call_timeout", False):
                read = _call_read_timeout()
                if read is not ...:
                    kw["timeout"] = read
            result = await asyncio.to_thread(self.transport.request, method, params, **kw)
            if inspect.isawaitable(result):
                result = await result
        return result if isinstance(result, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tool specs (raw MCP dicts: name/description/inputSchema).

        Follows ``nextCursor`` (v1.322.0): ``tools/list`` is PAGINATED in the
        spec, and reading only the first page silently hid every tool past it.
        The first request carries no cursor (``{}``), each later one sends the
        previous page's ``nextCursor``; paging stops when the cursor is absent,
        repeats, or :data:`MAX_TOOL_LIST_PAGES` is reached (logged — a capped
        list is never passed off as complete in the log).
        """
        return await self._paged("tools/list", "tools")

    async def _paged(
        self, method: str, key: str, *, absent_ok: bool = False
    ) -> list[dict[str, Any]]:
        """Every item of a PAGINATED list method (``tools/list``,
        ``prompts/list``, ``resources/list``), following ``nextCursor`` under
        one cap and one log rule. ``absent_ok``: a server that answers
        -32601 / "Method not found" simply has none (an empty list, not an
        error) — prompts and resources are optional capabilities."""
        items: list[dict[str, Any]] = []
        params: dict[str, Any] = {}
        seen: set[str] = set()
        for _page in range(MAX_TOOL_LIST_PAGES):
            try:
                result = await self._request(method, params)
            except MCPError as exc:
                if absent_ok and _is_method_not_found(exc):
                    return items
                raise
            batch = result.get(key, [])
            if isinstance(batch, list):
                items.extend(b for b in batch if isinstance(b, dict))
            cursor = result.get("nextCursor")
            if cursor is None or cursor == "":
                return items
            ckey = str(cursor)
            if ckey in seen:
                log.warning(
                    "pack %r: %s repeated cursor %r; stopped paging with %d %s",
                    self.name, method, ckey, len(items), key,
                )
                return items
            seen.add(ckey)
            params = {"cursor": cursor}
        log.warning(
            "pack %r: %s still had more pages after %d pages; stopped there "
            "with %d %s — any %s on later pages are NOT loaded",
            self.name, method, MAX_TOOL_LIST_PAGES, len(items), key, key,
        )
        return items

    async def list_prompts(self) -> list[dict[str, Any]]:
        """The pack's prompts (raw MCP dicts: name/title/description/arguments);
        ``[]`` when it has none or does not serve prompts at all (v1.324.0)."""
        return await self._paged("prompts/list", "prompts", absent_ok=True)

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """One prompt filled in with ``arguments`` (raw MCP result:
        ``{description?, messages: [{role, content}]}``)."""
        return await self._request(
            "prompts/get", {"name": name, "arguments": dict(arguments or {})}
        )

    async def list_resources(self) -> list[dict[str, Any]]:
        """The pack's resources (raw MCP dicts: uri/name/title/description/
        mimeType); ``[]`` when it has none or does not serve resources."""
        return await self._paged("resources/list", "resources", absent_ok=True)

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """One resource's contents (raw MCP result: ``{contents: [{uri,
        mimeType?, text | blob}]}``)."""
        return await self._request("resources/read", {"uri": uri})

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a remote tool; return the raw MCP result ({content, isError}).

        Inside an interaction scope (an attended stream turn, v1.324.0) the
        call carries ``_meta.progressToken`` = the model's tool-call id, so
        the pack's progress reaches that call's card. Without one it carries
        none — nobody would see the progress."""
        params: dict[str, Any] = {"name": name, "arguments": arguments or {}}
        scope = current_scope()
        if scope is not None and scope.call_id:
            params["_meta"] = {"progressToken": str(scope.call_id)}
        return await self._request("tools/call", params)

    def close(self) -> None:
        self.closed = True
        closer = getattr(self.transport, "close", None)
        if callable(closer):
            closer()


def _is_method_not_found(exc: BaseException) -> bool:
    """Whether a transport error is the server's -32601 / "Method not found"."""
    text = str(exc).strip().lower()
    return text.startswith(str(_METHOD_NOT_FOUND)) or "method not found" in text


# --------------------------------------------------------------------------- #
# FakeTransport — offline test double (also handy for demos).
# --------------------------------------------------------------------------- #
class FakeTransport:
    """A canned, in-memory transport. **Never** touches a process or socket.

    ``responses`` maps a JSON-RPC method (``"tools/list"`` / ``"tools/call"``) to
    either a static result dict or a ``callable(params) -> dict``. Methods listed
    in ``raise_on`` raise ``error`` (default :class:`MCPError`) — used to exercise
    the "bad server is skipped" path. Every call is recorded on ``calls``.
    """

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        raise_on: object = None,
        error: Exception | None = None,
    ) -> None:
        self.responses: dict[str, Any] = dict(responses or {})
        # raise_on may be a single method name, an iterable of names, or True (all).
        if raise_on is True:
            self.raise_on: object = True
        elif isinstance(raise_on, str):
            self.raise_on = {raise_on}
        elif raise_on:
            self.raise_on = set(raise_on)
        else:
            self.raise_on = set()
        self.error = error or MCPError("fake transport failure")
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, dict(params or {})))
        if self.raise_on is True or (
            isinstance(self.raise_on, set) and method in self.raise_on
        ):
            raise self.error
        canned = self.responses.get(method, {})
        if callable(canned):
            return canned(params or {})
        return canned


# --------------------------------------------------------------------------- #
# StdioTransport — real subprocess, line-delimited JSON-RPC. Lazy spawn.
# --------------------------------------------------------------------------- #
class StdioTransport:
    """Run an MCP server as a child process and exchange one JSON message per line.

    The process is spawned **lazily** on the first ``request`` (never at import or
    construction), so merely configuring a server is side-effect-free.

    Hardened in v1.291.0 (io-02 / io-05 / io-06), all in this one class because
    they are one transport:

    * **Calls arrive from worker threads** (``MCPClient`` offloads them), so a
      ``threading.Lock`` serialises ``request``: request ids and the single
      stdout reader must never interleave.
    * **Every read has a deadline.** A reader thread drains stdout into a
      ``queue.Queue`` (the shape ``repl/session.py`` uses) so the waiting side
      is an interruptible ``Queue.get(timeout=...)`` rather than an
      uncancellable ``readline()``. On expiry the server is KILLED — the thread
      must not stay parked owning the pipe — and the next call respawns it.
    * **The pipes are UTF-8.** MCP stdio is UTF-8 by spec and Node/Rust servers
      write raw UTF-8; ``text=True`` alone decodes with the locale codec
      (cp1252 on Windows), which crashes on five byte values and garbles the
      rest. ``errors="replace"`` so a server that wrongly emits cp1252 shows
      U+FFFD instead of killing the call.
    * **A dead server is respawned on the next call**, never retried within
      the failed one. EOF on stdout, a broken pipe on stdin or a ``poll()``
      that reports an exit all mark the server gone and raise
      :class:`MCPServerStopped` saying so.
    * **``close`` kills the process TREE**: ``npx``/``uvx`` are launchers whose
      real server is a grandchild (``cmd.exe`` → ``node``), and killing the
      launcher alone leaves the server holding the pipe. Windows: a
      kill-on-close Job (``terminals/backend``'s helper), ``taskkill /T /F``
      as the fallback; POSIX: its own session, so ``killpg`` reaches it all.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        request_timeout: float | None | EllipsisType = ...,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.cwd = cwd
        #: Seconds one answer may take before the server is killed. ``None``
        #: means no deadline (the caller carries its own and aborts through
        #: :meth:`abort`); left unset it is :data:`DEFAULT_REQUEST_TIMEOUT_S`
        #: as it stands NOW (not as it stood when this module was imported).
        self.request_timeout: float | None = (
            DEFAULT_REQUEST_TIMEOUT_S if request_timeout is ... else request_timeout
        )
        self._proc: subprocess.Popen | None = None
        self._out: "queue.Queue[str | None] | None" = None
        self._reader: threading.Thread | None = None
        #: Windows Job handle owning the server and everything it launched.
        self._job: int | None = None
        self._id = 0
        self._lock = threading.Lock()
        #: Guards the (proc, out, job, reader) swap in :meth:`close` so that
        #: exactly ONE closer receives the live tuple. ``abort`` (the cancelled
        #: caller's thread), the in-flight worker's own timeout/EOF path and a
        #: respawn can all close at once; a second kill on the same Windows
        #: Job handle would run ``TerminateJobObject`` + ``CloseHandle`` on an
        #: already-closed handle value that Windows may have recycled for
        #: ANOTHER Job (another pack's, a terminal pane's) — and kill that.
        self._state_lock = threading.Lock()
        #: The ``cancel`` token of the request currently on the wire, so
        #: :meth:`abort` kills only for the call that actually owns the server.
        self._inflight: threading.Event | None = None
        #: How many times a server process was spawned (diagnostics + pins).
        self.spawn_count = 0
        #: The pack's name, set by ``MCPClient`` (v1.324.0): the card that
        #: shows a pack's question says which app is asking.
        self.pack_name: str | None = None

    # -- lifecycle ----------------------------------------------------------
    @property
    def pid(self) -> int | None:
        """The live server's pid, or ``None`` when no server is running."""
        proc = self._proc
        return proc.pid if proc is not None else None

    def _ensure_started(self, cancel: threading.Event | None = None) -> None:
        """Spawn the server (or respawn one that exited). Lock held.

        ``cancel`` is the calling request's token. A cancel that lands WHILE
        the server is spawning finds ``_proc`` still ``None``, so
        :meth:`abort` has nothing to kill and never runs again; without a
        re-check here the worker would go on to handshake, send the cancelled
        request and (with no transport deadline — every registry pack) park
        in ``_read`` for ever holding ``_lock``, wedging the pack until the
        app restarted. So the fresh tuple is swapped in and the token checked
        under ONE ``_state_lock`` section: either ``abort``'s ``close()``
        sees the live tuple and kills it, or this check sees the token (set
        before ``abort`` is called) and kills the tuple itself.
        """
        if self._proc is not None:
            if self._proc.poll() is None:
                return
            # The server exited on its own (crash, update, OOM). Forget it and
            # start a fresh one — writing into the dead pipe raised
            # ``OSError(22)`` on every later call until the app restarted.
            log.warning("mcp stdio server %s exited (code %s); restarting it",
                        self.command, self._proc.returncode)
            # Through close(): the swap-first path, so an abort() landing during
            # this respawn can never kill the same tuple a second time.
            self.close()
        popen_kw: dict[str, Any] = {}
        if os.name != "nt":
            # Its own session, so ``killpg`` in ``_kill`` reaches the whole tree.
            popen_kw["start_new_session"] = True
        proc = subprocess.Popen(  # noqa: S603 — command comes from trusted config
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=self.env,
            cwd=self.cwd,
            **popen_kw,
        )
        self.spawn_count += 1
        job: int | None = None
        if os.name == "nt":
            handle = getattr(proc, "_handle", None)
            if handle is not None:
                from ..terminals.backend import _win_job_for

                job = _win_job_for(int(handle))
        out: "queue.Queue[str | None]" = queue.Queue()
        reader = threading.Thread(
            target=self._pump_stdout,
            args=(proc.stdout, out),
            name=f"mcp-stdio-{proc.pid}",
            daemon=True,
        )
        reader.start()
        with self._state_lock:  # one atomic tuple, never half of one to a closer
            self._proc, self._out, self._job, self._reader = proc, out, job, reader
            cancelled = cancel is not None and cancel.is_set()
        if cancelled:
            # The caller gave up during the spawn (abort() found nothing to
            # kill): kill the fresh server before it is ever spoken to.
            self.close()
            raise MCPError("the call was cancelled before it was sent")
        # MCP handshake: initialize (request), then the initialized notification.
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": client_capabilities(),
                "clientInfo": _CLIENT_INFO,
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    @staticmethod
    def _pump_stdout(stream: Any, out: "queue.Queue[str | None]") -> None:
        """Drain the server's stdout into a queue, forever, in its own thread."""
        try:
            if stream is not None:
                for line in stream:
                    out.put(line)
        except Exception:  # pragma: no cover - pipe torn down mid-read
            pass
        finally:
            out.put(None)  # EOF sentinel: the server is gone

    @staticmethod
    def _kill(
        proc: subprocess.Popen | None,
        out: "queue.Queue[str | None] | None",
        job: int | None,
        reader: threading.Thread | None,
    ) -> None:
        """Kill the server's whole process tree, then close its pipes. Never raises.

        Lock-free on purpose: :meth:`abort` and :meth:`close` run on a thread
        OTHER than the one parked (holding the lock) on the server's answer.
        The EOF sentinel pushed last is what wakes that thread immediately,
        whether or not a grandchild still holds the pipe open.
        """
        if proc is None:
            return
        alive = proc.poll() is None
        try:
            if proc.stdin is not None:
                proc.stdin.close()  # EOF: a well-behaved server exits on it
        except Exception:  # pragma: no cover - already closed
            pass
        if alive:
            killed = False
            if os.name == "nt":
                from ..terminals.backend import _tree_kill

                killed = _tree_kill(proc, job, alive)
            else:
                try:
                    import signal

                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    killed = True
                except Exception:  # noqa: BLE001 — not a group leader / gone
                    killed = False
            if not killed:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001 — already gone
                    pass
            try:
                proc.wait(timeout=_KILL_GRACE_S)
            except Exception:  # noqa: BLE001 — a straggler; the pipes close below
                pass
        elif job is not None and os.name == "nt":
            # The launcher exited but its job may still hold grandchildren.
            from ..terminals.backend import _win_terminate_job

            _win_terminate_job(job)
        # stdout is closed only once the reader thread has let go of it: a
        # ``BufferedReader.close`` contends for the buffer lock a parked
        # ``readline`` holds, so closing under a surviving grandchild would
        # hang THIS thread instead of freeing anything.
        if reader is not None:
            reader.join(timeout=_KILL_GRACE_S)
        if reader is None or not reader.is_alive():
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:  # pragma: no cover - already closed
                pass
        if out is not None:
            out.put(None)

    def close(self) -> None:
        """Forget the server, then kill its tree and close its pipes.

        The swap is atomic under ``_state_lock``: whoever wins holds the only
        reference to the live tuple; every concurrent closer sees ``None`` and
        returns. The kill itself runs OUTSIDE the lock (it can block for
        ``_KILL_GRACE_S`` on ``wait``/``join``).
        """
        with self._state_lock:
            proc, out, job, reader = self._proc, self._out, self._job, self._reader
            self._proc, self._out, self._job, self._reader = None, None, None, None
        if proc is None:
            return
        self._kill(proc, out, job, reader)

    def abort(self, cancel: threading.Event) -> None:
        """The caller of an in-flight request gave up (deadline / Cancel).

        Only the call that owns the server is aborted by killing it: a call
        still waiting for the lock just sees its token set and never sends.
        One whose server is still SPAWNING (``_proc`` is ``None``, so this
        ``close()`` is a no-op) is caught by the token re-check that
        ``_ensure_started`` and ``request`` run before anything is written.
        """
        if self._inflight is cancel:
            self.close()

    # -- io -----------------------------------------------------------------
    def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        try:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:  # BrokenPipe, EINVAL, closed file
            self.close()
            raise MCPServerStopped(
                f"the MCP server stopped ({type(exc).__name__}: {exc}); "
                "it will be restarted on the next call"
            ) from exc

    def _read(self, expected_id: int) -> dict[str, Any]:
        proc, out = self._proc, self._out
        assert proc is not None and out is not None
        timeout = self.request_timeout
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                item: str | None = None
                timed_out = True
            else:
                timed_out = False
                try:
                    item = out.get(timeout=remaining)
                except queue.Empty:
                    timed_out = True
                    item = None
            if timed_out:
                # A wedged server: kill it so nothing stays parked on its pipe
                # and the next call gets a fresh one.
                self.close()
                raise MCPServerStopped(
                    f"the MCP server did not answer within {float(timeout):g} s; "
                    "it was stopped and will be restarted on the next call"
                )
            if item is None:
                # EOF: the pipe closed before the exit was reaped, so give the
                # exit code a moment to land — it is the one clue in the error.
                try:
                    proc.wait(timeout=0.5)
                except Exception:  # noqa: BLE001 — a launcher still winding down
                    pass
                code = proc.returncode if proc.poll() is not None else None
                self.close()
                raise MCPServerStopped(
                    "the MCP server closed the connection"
                    + (f" (exit code {code})" if code is not None else "")
                    + "; it will be restarted on the next call"
                )
            line = item.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore non-JSON log noise on stdout
            route = _route_message(msg, expected_id)
            if route == _RESPONSE:
                return msg
            if route == _SERVER_REQUEST:
                # v1.322.0: a server→client request (``ping``, ``roots/list``
                # …) was DROPPED here, and a server that waits for its answer
                # before finishing ours hung the call. Answer on the same
                # pipe and keep reading for our own response. v1.324.0: the
                # answer comes from ``interact`` — an elicitation or sampling
                # request may wait for the USER, giving up when this call's
                # own cancel token is set (Stop / the registry deadline).
                started = time.monotonic()
                reply = serve_server_request(self._pack(), msg, self._inflight)
                if deadline is not None:
                    # Time spent waiting on the user is not the server being
                    # wedged: the transport floor does not run meanwhile.
                    deadline += time.monotonic() - started
                self._write(reply)
            elif route == _NOTIFICATION:
                handle_notification(self._pack(), msg)
            # Answers to other ids are skipped.

    def _pack(self) -> str:
        """The pack's name for ``interact`` (the client sets it)."""
        return self.pack_name or "mcp"

    def _rpc(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        self._id += 1
        rid = self._id
        self._write(_envelope(rid, method, params))
        return _extract_result(self._read(rid))

    # -- transport interface ------------------------------------------------
    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        # Wait for the lock in short slices so a caller that gave up while
        # queued behind another call never sends its request at all.
        while not self._lock.acquire(timeout=0.1):
            if cancel is not None and cancel.is_set():
                raise MCPError("the call was cancelled before it was sent")
        try:
            if cancel is not None and cancel.is_set():
                raise MCPError("the call was cancelled before it was sent")
            self._inflight = cancel
            self._ensure_started(cancel)
            if cancel is not None and cancel.is_set():
                # Cancelled during the spawn/handshake but after the spawn's
                # own check: a cancelled request is never written (it may
                # have side effects), and the server it owns goes with it,
                # exactly as abort() would have done had it caught it live.
                self.close()
                raise MCPError("the call was cancelled before it was sent")
            return self._rpc(method, params)
        finally:
            self._inflight = None
            self._lock.release()


# --------------------------------------------------------------------------- #
# HttpTransport — real HTTP / streamable-http (JSON or SSE). Lazy httpx.
# --------------------------------------------------------------------------- #
class HttpTransport:
    """POST JSON-RPC to an MCP HTTP endpoint. The ``httpx`` client is created
    lazily on first use so no socket/connection pool exists until a real call.

    v1.322.0:

    * **The answer is the message with OUR id.** A Streamable-HTTP server may
      stream ``notifications/progress``, ``notifications/message`` (logging)
      or its own requests BEFORE the response; taking the first ``data:``
      payload returned a notification, which unwrapped to ``{}`` and read as a
      successful call with empty output. A stream that ends without our id is
      an :class:`MCPError` naming the server.
    * **The body is READ AS IT STREAMS** (``client.stream``) so a server
      request inside the stream is answered while the server still waits:
      ``ping`` gets an empty result, anything else -32601, each POSTed back to
      the endpoint (with the session id) — the Streamable-HTTP way a client
      answers. A client object without ``stream`` (a minimal test double)
      falls back to reading the whole body, where server requests are still
      answered, but only after the stream has ended.
    * **The read timeout follows the caller's deadline.** ``timeout`` is only
      the DEFAULT for calls that carry no registry deadline (the LTM brain, a
      boot ``tools/list`` — itself bounded by the connect probe). A registry
      tool call passes ``timeout=`` per call (see :func:`_call_read_timeout`):
      what is left of ``config.tool_call_timeout_s`` plus a grace, or ``None``
      when the user disabled the deadline. It used to be a flat 30 s, so a
      remote pack call longer than that died with ``ReadTimeout`` under a
      600 s deadline. CONNECTING stays bounded by ``connect_timeout``.
      There is no ``abort``: a cancelled call's worker thread is NOT
      interrupted, it unwinds when its read timeout (deadline + grace) lapses.
    """

    #: ``MCPClient`` passes the per-call read timeout to transports with this.
    accepts_call_timeout = True

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = 30.0,
        connect_timeout: float = HTTP_CONNECT_TIMEOUT_S,
        client_factory: Callable[[], Any] | None = None,
        name: str | None = None,
    ) -> None:
        self.url = url
        self.headers = dict(headers or {})
        #: Default READ timeout (seconds; ``None`` = unbounded) for a call that
        #: carries no deadline of its own.
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        #: The pack's name, for error text. The URL is NOT used in errors: a
        #: pack URL can carry a key in its query string.
        self.name = name
        #: Set by ``MCPClient`` (v1.324.0) — the name a pack's question shows.
        self.pack_name: str | None = None
        self._client_factory = client_factory
        self._client: Any | None = None
        self._id = 0
        #: Serialises the handshake and id allocation: ``MCPClient`` runs
        #: calls on worker threads, and two first calls must not both
        #: handshake or share an id.
        self._lock = threading.Lock()
        # Streamable-HTTP session state: servers (FastMCP et al.) REQUIRE an
        # `initialize` handshake and echo an `Mcp-Session-Id` header that every
        # later request must carry — without it they 400 on tools/list. (The
        # stdio path always handshook; HTTP lacked it — live-hit 2026-07-21
        # against a real Obsidian-brain server.)
        self._session_id: "str | None" = None
        self._initialized = False

    @property
    def label(self) -> str:
        """How errors name this server: the pack name, else the URL's host."""
        if self.name:
            return f"pack {self.name!r}"
        try:
            from urllib.parse import urlparse

            host = urlparse(self.url).hostname or "?"
        except Exception:  # noqa: BLE001 — a label never raises
            host = "?"
        return f"the MCP server at {host}"

    def _timeout(self, read: float | None) -> Any:
        """An ``httpx.Timeout``: ``read`` for reading (and writing / pool),
        ``connect_timeout`` for establishing the connection."""
        import httpx  # lazy import — keeps module import cheap/offline

        return httpx.Timeout(read, connect=self.connect_timeout)

    def _ensure_client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                import httpx  # lazy import — keeps module import cheap/offline

                self._client = httpx.Client(timeout=self._timeout(self.timeout))
        return self._client

    def close(self) -> None:
        if self._client is not None:
            closer = getattr(self._client, "close", None)
            if callable(closer):
                closer()
            self._client = None

    @staticmethod
    def _is_sse(response: Any) -> bool:
        try:
            ctype = response.headers.get("content-type", "") or ""
        except Exception:  # pragma: no cover - defensive
            ctype = ""
        return "text/event-stream" in ctype

    @staticmethod
    def _pick(
        messages: Any,
        request_id: Any,
        on_request: Callable[[dict[str, Any]], None] | None,
        on_notification: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """The first message answering ``request_id`` (``None`` = the first
        RESPONSE of any id — the pre-v1.322.0 single-message callers);
        server requests go to ``on_request``; notifications go to
        ``on_notification`` (v1.324.0) and are otherwise skipped."""
        for msg in messages:
            if request_id is None:
                if isinstance(msg, dict) and "method" not in msg:
                    return msg
                if isinstance(msg, dict) and msg.get("id") is not None and on_request:
                    on_request(msg)
                elif isinstance(msg, dict) and on_notification is not None:
                    on_notification(msg)
                continue
            route = _route_message(msg, request_id)
            if route == _RESPONSE:
                return msg
            if route == _SERVER_REQUEST and on_request is not None:
                on_request(msg)
            elif route == _NOTIFICATION and on_notification is not None:
                on_notification(msg)
        return None

    @staticmethod
    def _parse_body(
        response: Any,
        request_id: Any = None,
        on_request: Callable[[dict[str, Any]], None] | None = None,
        label: str = "the MCP server",
        on_notification: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """A WHOLE (already read) response body → the JSON-RPC answer.

        SSE: the message whose ``id`` is ``request_id`` — notifications and
        other ids are skipped, server requests go to ``on_request``; none =
        :class:`MCPError`. ``application/json``: the object as sent (a batch
        list is searched for ``request_id``)."""
        if HttpTransport._is_sse(response):
            msg = HttpTransport._pick(
                _sse_messages(response.text.splitlines()), request_id, on_request,
                on_notification,
            )
            if msg is None:
                raise MCPError(
                    f"{label} sent no JSON-RPC answer"
                    + (f" to request {request_id}" if request_id is not None else "")
                    + " in its event stream"
                )
            return msg
        body = response.json()
        if isinstance(body, list):
            msg = HttpTransport._pick(body, request_id, on_request, on_notification)
            if msg is None:
                raise MCPError(f"{label} sent a batch without the answer to request {request_id}")
            return msg
        return body

    def _pack(self) -> str:
        """The pack's name for ``interact`` (the client sets it)."""
        return self.pack_name or self.name or "mcp"

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _capture_session(self, response: Any) -> None:
        sid = None
        try:
            sid = response.headers.get("mcp-session-id")
        except Exception:  # pragma: no cover — defensive
            sid = None
        if sid:
            self._session_id = sid

    def _answer(self, client: Any, msg: dict[str, Any]) -> None:
        """POST our reply to a server→client request back to the endpoint.
        Best-effort: a failed reply is logged, never fails OUR call (the
        server may still answer it). The reply comes from ``interact``
        (v1.324.0): an elicitation / sampling request may wait for the user;
        HTTP has no cancel token (``cancel=None``) — the turn's own end
        resolves the pending ask, and a closed loop gives up."""
        reply = serve_server_request(self._pack(), msg, None)
        try:
            response = client.post(
                self.url,
                json=reply,
                headers=self._base_headers(),
                timeout=self._timeout(self.connect_timeout),
            )
            status = getattr(response, "status_code", 200)
            if isinstance(status, int) and status >= 400:
                log.warning("%s refused our reply to its %r request (HTTP %s)",
                            self.label, msg.get("method"), status)
        except Exception as exc:  # noqa: BLE001 — best-effort, see above
            log.warning("could not answer %s's %r request: %s",
                        self.label, msg.get("method"), exc)

    def _exchange(
        self,
        client: Any,
        payload: dict[str, Any],
        read: float | None,
        *,
        capture_session: bool = False,
    ) -> dict[str, Any]:
        """POST one request; return the JSON-RPC message that answers it."""
        rid = payload["id"]
        headers = self._base_headers()
        timeout = self._timeout(read)

        def on_request(msg: dict[str, Any]) -> None:
            self._answer(client, msg)

        def on_notification(msg: dict[str, Any]) -> None:
            handle_notification(self._pack(), msg)

        stream = getattr(client, "stream", None)
        if callable(stream):
            with stream("POST", self.url, json=payload, headers=headers, timeout=timeout) as response:
                response.raise_for_status()
                if capture_session:
                    self._capture_session(response)
                if self._is_sse(response):
                    msg = self._pick(
                        _sse_messages(response.iter_lines()), rid, on_request, on_notification
                    )
                    if msg is None:
                        raise MCPError(
                            f"{self.label} ended its event stream without answering "
                            f"request {rid} ({payload.get('method')})"
                        )
                    return msg
                response.read()
                return self._parse_body(response, rid, on_request, self.label, on_notification)
        response = client.post(self.url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        if capture_session:
            self._capture_session(response)
        return self._parse_body(response, rid, on_request, self.label, on_notification)

    def _timed(self, fn: Callable[[], Any], read: float | None) -> Any:
        """Run ``fn``; an httpx timeout becomes an :class:`MCPError` that NAMES
        the server and the bound that was hit."""
        try:
            return fn()
        except Exception as exc:
            try:
                import httpx
            except ImportError:  # pragma: no cover — httpx is a dependency
                raise exc from None
            if isinstance(exc, httpx.ConnectTimeout):
                raise MCPError(
                    f"could not connect to {self.label} within {self.connect_timeout:g} s"
                ) from exc
            if isinstance(exc, httpx.TimeoutException):
                bound = "" if read is None else f" within {float(read):g} s"
                raise MCPError(
                    f"{self.label} did not respond{bound} ({type(exc).__name__})"
                ) from exc
            raise

    def _handshake(self, client: Any, read: float | None) -> None:
        """The streamable-HTTP session dance: initialize → capture the session
        id → notifications/initialized. Best-effort on the notification (some
        servers 202/204/405 it); the session id is the part that matters."""
        self._id += 1
        payload = _envelope(
            self._id,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": client_capabilities(),
                "clientInfo": {"name": "iron-jarvis", "version": "1.0"},
            },
        )
        msg = self._exchange(client, payload, read, capture_session=True)
        _extract_result(msg)  # surface protocol errors
        try:
            client.post(
                self.url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._base_headers(),
                timeout=self._timeout(self.connect_timeout),
            )
        except Exception:  # noqa: BLE001 — notification delivery is best-effort
            pass
        self._initialized = True

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None | EllipsisType = ...,
    ) -> dict[str, Any]:
        """One JSON-RPC call. ``timeout`` is this call's READ timeout
        (``...`` = the transport default, ``None`` = unbounded)."""
        read = self.timeout if timeout is ... else timeout
        client = self._ensure_client()
        with self._lock:
            if not self._initialized and method != "initialize":
                self._timed(lambda: self._handshake(client, read), read)
            self._id += 1
            payload = _envelope(self._id, method, params)
        return _extract_result(self._timed(lambda: self._exchange(client, payload, read), read))
