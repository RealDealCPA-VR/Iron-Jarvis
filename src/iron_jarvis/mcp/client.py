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

log = get_logger("mcp")

#: MCP protocol revision advertised in the ``initialize`` handshake.
PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "iron-jarvis", "version": "0"}

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
            result = await asyncio.to_thread(self.transport.request, method, params)
            if inspect.isawaitable(result):
                result = await result
        return result if isinstance(result, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tool specs (raw MCP dicts: name/description/inputSchema)."""
        result = await self._request("tools/list", {})
        tools = result.get("tools", [])
        return list(tools) if isinstance(tools, list) else []

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a remote tool; return the raw MCP result ({content, isError})."""
        return await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )

    def close(self) -> None:
        closer = getattr(self.transport, "close", None)
        if callable(closer):
            closer()


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
                "capabilities": {},
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
            # Skip notifications / responses to other ids.
            if isinstance(msg, dict) and msg.get("id") == expected_id:
                return msg

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
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: Any | None = None
        self._id = 0
        # Streamable-HTTP session state: servers (FastMCP et al.) REQUIRE an
        # `initialize` handshake and echo an `Mcp-Session-Id` header that every
        # later request must carry — without it they 400 on tools/list. (The
        # stdio path always handshook; HTTP lacked it — live-hit 2026-07-21
        # against a real Obsidian-brain server.)
        self._session_id: "str | None" = None
        self._initialized = False

    def _ensure_client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                import httpx  # lazy import — keeps module import cheap/offline

                self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            closer = getattr(self._client, "close", None)
            if callable(closer):
                closer()
            self._client = None

    @staticmethod
    def _parse_body(response: Any) -> dict[str, Any]:
        ctype = ""
        try:
            ctype = response.headers.get("content-type", "")
        except Exception:  # pragma: no cover - defensive
            ctype = ""
        if "text/event-stream" in ctype:
            # Server-Sent Events: the JSON-RPC payload rides on ``data:`` lines.
            for raw in response.text.splitlines():
                line = raw.strip()
                if line.startswith("data:"):
                    chunk = line[len("data:"):].strip()
                    if chunk and chunk != "[DONE]":
                        try:
                            return json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
            raise MCPError("no JSON-RPC payload in SSE response")
        return response.json()

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _handshake(self, client: Any) -> None:
        """The streamable-HTTP session dance: initialize → capture the session
        id → notifications/initialized. Best-effort on the notification (some
        servers 202/204/405 it); the session id is the part that matters."""
        self._id += 1
        payload = _envelope(
            self._id,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "iron-jarvis", "version": "1.0"},
            },
        )
        response = client.post(self.url, json=payload, headers=self._base_headers())
        response.raise_for_status()
        sid = None
        try:
            sid = response.headers.get("mcp-session-id")
        except Exception:  # pragma: no cover — defensive
            sid = None
        if sid:
            self._session_id = sid
        _extract_result(self._parse_body(response))  # surface protocol errors
        try:
            client.post(
                self.url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._base_headers(),
            )
        except Exception:  # noqa: BLE001 — notification delivery is best-effort
            pass
        self._initialized = True

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._ensure_client()
        if not self._initialized and method != "initialize":
            self._handshake(client)
        self._id += 1
        payload = _envelope(self._id, method, params)
        response = client.post(self.url, json=payload, headers=self._base_headers())
        response.raise_for_status()
        return _extract_result(self._parse_body(response))
