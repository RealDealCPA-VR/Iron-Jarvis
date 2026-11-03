"""ONE Claude Code process across a coding reply's steps (D4, v1.427.0).

The owner's decision of 2026-11-03 (``docs/CLAUDE-LIVE-SESSION.md``): every step
of a Chat › Code reply used to start a new ``claude`` (about 0.5 s) and replay
the whole conversation into it, which also moved Claude's cached prefix. Inside
an OPEN turn scope (``providers.turn_scope``: the lanes open one for a coding
turn with a finish check) a claude-cli call now keeps its process between steps.

How (measured on the real CLI 2.1.316 by the coordinator, see the doc):

* The process starts as :func:`transport.run` starts it, except ``--max-turns``
  is large (:data:`LIVE_MAX_TURNS`: the relay, not the CLI, ends a step), stdin
  stays open and ``API_TIMEOUT_MS`` is an hour (a held request waited 330 s and
  was answered correctly).
* When the model asks for tools, Claude Code denies them itself
  (``--permission-mode dontAsk``) and at once POSTs its follow-up, carrying the
  denials as ``tool_result`` blocks. The relay (:class:`LiveRoute`, a route on
  the ONE shared listener) HOLDS that request; the call returns the tool calls
  to the lane exactly as today; the process waits (``parked_tools``).
* The lane's next call RESUMES it only when its conversation is exactly what
  the process holds plus ONE user turn whose leading ``tool_result`` blocks
  answer exactly the held calls (text or pictures may follow): the relay puts
  the REAL results in place of the denials (and the trailing blocks after them)
  and lets the held request go.
* When the model ends with text the CLI ends its query and waits on stdin
  (``parked_done``); the next call whose conversation is that plus one new user
  turn (the finish check's note) is written to stdin as a new message.
* Claude Code sends its own history on every request, the denials included, so
  EVERY replacement is applied again on EVERY later request. Claude Code's own
  ``cache_control`` placement is left alone (no :func:`admission.pin_message_
  breakpoint` here): each request read the earlier prefix from cache.
* ``result.total_cost_usd`` covers the whole query (several requests), so a
  live round reports usage only, never ``cost_usd``.

Anything else is a MISS and the call starts a new live session (a new process
that replays the conversation), after the parked session of the same
conversation (:func:`transport._chain_key`) has been killed: one conversation
never has two live CLIs. A miss is never an error. Every session dies with its
turn scope, a parked one nobody resumes within :data:`PARKED_IDLE_TIMEOUT_S`
is killed by a reaper, at most :data:`MAX_LIVE_SESSIONS` live at once (the
oldest parked one is killed to make room; none parked = this call runs today's
path), a Stop kills the tree (the v1.287.0 discipline), and the held request is
aborted (its socket shut) whenever its session dies.

Outside an open scope — plain chat, agents, schedules, the injected ``runner``
test seam, a call with a ``--max-budget-usd`` — the adapter never comes here:
:func:`applies` is False and :func:`transport.run` runs byte for byte as
before. ``IRONJARVIS_CLAUDE_LIVE=off`` turns it off everywhere.

Nothing blocking runs on the loop: the spawn, stdin writes, the frame
comparisons and every kill hop to a worker thread.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import queue
import _socket
import secrets
import shutil
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from ... import turn_scope as _scope
from . import admission as A
from . import transport as T

log = logging.getLogger("ironjarvis.claude_native")

#: ``off`` (or 0/false/no) turns live sessions off everywhere (the suite's
#: default: older fakes do not speak this protocol; tests/conftest.py).
ENV_SWITCH = "IRONJARVIS_CLAUDE_LIVE"
#: ``--max-turns`` of a live process: the relay ends a step, not the CLI. A
#: query reaching it ends at the tool boundary (handled: the session is ended).
LIVE_MAX_TURNS = 1000
#: Claude Code's own request timeout in a live child: a held follow-up waits
#: for the lane's tools (measured: held 330 s, then answered).
API_TIMEOUT_MS = "3600000"
#: A parked session nobody resumed (an attended approval card can take many
#: minutes; a reaped session only costs a replay). Read at call time.
PARKED_IDLE_TIMEOUT_S = 1800.0
#: Live sessions at once (one ``claude`` process each). Read at call time.
MAX_LIVE_SESSIONS = 4
#: After the relay held a follow-up: how long to wait for the CLI's own denial
#: line (written before its POST) so no line of this step is left for the next.
HELD_SETTLE_S = 2.0
#: A stdin write to a parked CLI (it is idle on stdin; a stuck write is a dead CLI).
WRITE_TIMEOUT_S = 30.0
#: Why recent sessions ended, by conversation (for the trace's miss reason).
_MAX_TOMBS = 64

_EOF = object()
_EMPTY = object()


def _off() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip().lower() in ("off", "0", "false", "no")


def applies(call: T.NativeCall, runner: Any = None) -> bool:
    """Is this call one for a live session? Only the REAL process path, inside an
    OPEN turn scope, with no budget ceiling (``--max-budget-usd`` caps ONE
    invocation; a kept process would carry it across steps)."""
    if runner is not None or _off():
        return False
    if call.budget_usd and call.budget_usd > 0:
        return False
    if not call.manifest:
        # Review: a call that offers NO tools is a one-shot inside the turn (the
        # Auto judge, the mid-reply summary, the language rewrite, the
        # final-answer nudge): it can never park on a tool step and is never
        # continued, so a live session only kept its `claude` alive for the
        # rest of the reply and, past the cap, pushed the lead's kept process
        # out. Today's path: the CLI answers and exits.
        return False
    return _scope.current() is not None


def _in_helper() -> bool:
    """A helper agent's run inherits its parent's scope; its own answer is its own
    and is never kept parked after it ends (the codex precedent, v1.386.0)."""
    try:
        from ....harness.helpers import HELPER_PARENT

        return HELPER_PARENT.get() is not None
    except Exception:  # noqa: BLE001 — no helpers module: nothing to exclude
        return False


# --------------------------------------------------------------------------- #
# The relay route
# --------------------------------------------------------------------------- #
class _Held:
    """Posted to the session when the relay holds the CLI's follow-up."""

    def __init__(self, ids: frozenset) -> None:
        self.ids = ids


class _Request(A.RequestRecord):
    """One forwarded request of a live session (the readers' record)."""

    def __init__(self) -> None:
        self._new_record()
        self.expect_set = False


class _Hold:
    def __init__(self, ids: frozenset) -> None:
        self.ids = ids
        self.event = threading.Event()
        self.filled = False


def follow_up_ids(body: bytes | str) -> frozenset | None:
    """The tool_result ids of a request's LAST message when it is a user turn
    that carries tool results, else None."""
    try:
        messages = json.loads(body)["messages"]
        last = messages[-1]
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    if not isinstance(last, dict) or last.get("role") != "user":
        return None
    content = last.get("content")
    if not isinstance(content, list):
        return None
    ids = [b.get("tool_use_id") for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
    if not ids or not all(isinstance(i, str) and i for i in ids):
        return None
    return frozenset(ids)


def apply_replacements(body: dict, replace: dict[str, Any], extras: dict[frozenset, list]) -> bool:
    """In place: every ``tool_result`` whose id we answered gets OUR content (its
    ``is_error`` dropped; its own ``cache_control`` and every other block kept), and
    a user turn whose tool_result ids are a batch we answered with trailing blocks
    gets those blocks right after its last tool_result. True when anything changed."""
    changed = False
    for message in body.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        ids: list[str] = []
        last = -1
        for j, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                ids.append(tid)
                last = j
                if tid in replace:
                    block["content"] = copy.deepcopy(replace[tid])
                    block.pop("is_error", None)
                    changed = True
        if last >= 0:
            more = extras.get(frozenset(ids))
            if more:
                content[last + 1:last + 1] = copy.deepcopy(more)
                changed = True
    return changed


class LiveRoute:
    """A live session's route on the ONE relay listener (``admission.listener``).

    Admits the first request, then one request per stdin message the session
    writes (:meth:`admit_one`); HOLDS the CLI's follow-up after a tool batch (the
    last message is a user turn whose tool_result ids are exactly the ids the
    previous answer's ``tool_use`` blocks carried) until :meth:`fill` or abort;
    refuses everything else locally with the CONSUMED body, never holding a side
    request; keeps the one sign-in retry per request. Every request it forwards
    gets every replacement (:func:`apply_replacements`) and its own record."""

    def __init__(self, upstream_base_url: str, timeout: float, notify: Callable[[Any], None]) -> None:
        self.upstream = A.checked_upstream(upstream_base_url)
        self.timeout = timeout
        self.notify = notify
        self.lock = threading.Lock()
        self.sockets: set[socket.socket] = set()
        self.cancelled = False
        #: Fresh requests that may go upstream (the first; one per stdin message).
        self.allow = 1
        #: The ids the next follow-up must answer to be held (set when an answer
        #: that asks for tools is complete, before its last byte reaches the CLI).
        self.expect: frozenset | None = None
        self.held: _Hold | None = None
        self.replace: dict[str, Any] = {}
        self.extras: dict[frozenset, list] = {}
        self.records: list[_Request] = []
        #: Refusals while no request had a record yet.
        self.denied = 0
        self._closed = False
        self._active = 0
        self._idle = threading.Condition(self.lock)
        self.prefix = "/admit/" + secrets.token_urlsafe(32)
        self.server = A.listener()
        self.url = f"http://127.0.0.1:{self.server.server_port}" + self.prefix
        self.server.register(self.prefix, self)

    # -- what the session reads / does -------------------------------------
    def record_count(self) -> int:
        with self.lock:
            return len(self.records)

    def last_record(self, since: int) -> _Request | None:
        with self.lock:
            fresh = self.records[since:]
            return fresh[-1] if fresh else None

    def admit_one(self) -> None:
        with self.lock:
            self.allow += 1

    def fill(self, results: dict[str, Any], extras: list) -> bool:
        """Release the held follow-up with the REAL results. False when nothing is
        held for exactly these ids (the session died, or another call's batch)."""
        with self.lock:
            hold = self.held
            if self.cancelled or hold is None or frozenset(results) != hold.ids:
                return False
            self.replace.update(copy.deepcopy(results))
            if extras:
                self.extras[hold.ids] = copy.deepcopy(extras)
            hold.filled = True
            self.held = None
        hold.event.set()
        return True

    def rewrite(self, raw: bytes) -> bytes:
        """The payload of a request we forward: every replacement applied."""
        with self.lock:
            replace = dict(self.replace)
            extras = dict(self.extras)
        if not replace and not extras:
            return raw
        body = json.loads(raw)
        if not apply_replacements(body, replace, extras):
            return raw
        try:
            return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except UnicodeEncodeError:
            # Review: a lone UTF-16 surrogate in OUR result (a JavaScript MCP server
            # that cut an emoji in half, a Windows file name that is not valid
            # UTF-16) has no UTF-8 form; escaped (``\\udXXX``, as the CLI's own
            # JSON.stringify writes it on today's path) it is sent, never a
            # failed round.
            return json.dumps(body, separators=(",", ":")).encode("ascii")

    def _after_feed(self, rec: _Request) -> None:
        """An answer that asks for tools is complete: its follow-up is the one to
        hold. Runs on the relay thread BEFORE the last chunk reaches the CLI."""
        if rec.expect_set or not rec.capture.complete:
            return
        rec.expect_set = True
        message = rec.capture.message or {}
        if message.get("stop_reason") != "tool_use":
            return
        ids = [
            b.get("id") for b in message.get("content") or []
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if ids and all(isinstance(i, str) and i for i in ids):
            with self.lock:
                self.expect = frozenset(ids)

    # -- the relay side -----------------------------------------------------
    def _deny(self, h: Any, rec: _Request | None) -> None:
        """Under ``lock``: refuse locally (the existing CONSUMED body)."""
        if rec is not None:
            rec.denied += 1
        else:
            self.denied += 1
        h._refuse()
        log.debug("claude live: refused a request locally")

    def handle(self, h: Any, path: Any) -> None:
        try:
            h.connection.settimeout(self.timeout)
        except OSError:
            pass
        # Read the body BEFORE deciding (the v1.420.0 rule: never reply over
        # unread bytes).
        body_in, read_error = h._read_body()
        ids = follow_up_ids(body_in) if read_error is None else None
        hold: _Hold | None = None
        with self.lock:
            rec = self.records[-1] if self.records else None
            if self.cancelled:
                self._deny(h, rec)
                return
            if rec is not None and rec.used and rec._auth_refused():
                rec._restart_record()
                log.debug("claude live: admitted the CLI's one retry after a sign-in refusal")
            elif self.expect is not None and ids is not None and ids == self.expect:
                self.expect = None
                hold = _Hold(ids)
                self.held = hold
                self.sockets.add(h.connection)
            elif self.allow > 0:
                self.allow -= 1
                rec = _Request()
                self.records.append(rec)
            else:
                self._deny(h, rec)
                return
            if hold is None:
                rec.used = True  # type: ignore[union-attr]
                self.sockets.add(h.connection)
                rec.stamps = {"request": time.perf_counter()}  # type: ignore[union-attr]
        if hold is not None:
            self.notify(_Held(hold.ids))
            hold.event.wait()
            with self.lock:
                if self.cancelled or not hold.filled:
                    self.sockets.discard(h.connection)
                    try:
                        h.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    h.close_connection = True
                    return
                rec = _Request()
                rec.used = True
                rec.stamps = {"request": time.perf_counter()}
                self.records.append(rec)
        h._relay(self, rec, body_in, read_error, path, self.rewrite, self._after_feed)

    def _enter(self) -> None:
        with self.lock:
            self._active += 1

    def _leave(self) -> None:
        with self.lock:
            self._active -= 1
            self._idle.notify_all()

    def abort(self) -> None:
        """Refuse everything from now on, shut AND close every socket (the held one
        too) and wake a held request so its handler returns.

        Closed, not only shut: on Windows a ``shutdown`` does not wake a thread
        blocked reading that socket (measured: a relay thread waiting for the
        upstream's answer stayed blocked, and :meth:`close` then waited its whole
        10 s bound on every Stop). The handle is closed at the socket layer
        (``_socket.socket.close``: the makefile ``http.client`` holds would defer
        the ordinary close), so that read fails at once; every later use of the
        object raises ``OSError``, which the relay already handles."""
        with self.lock:
            self.cancelled = True
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    _socket.socket.close(sock)
                except OSError:
                    pass
            hold, self.held = self.held, None
        if hold is not None:
            hold.event.set()

    def close(self) -> None:
        self.abort()
        if self._closed:
            return
        self._closed = True
        self.server.unregister(self.prefix)
        with self.lock:
            self._idle.wait_for(lambda: self._active == 0, timeout=10.0)


# --------------------------------------------------------------------------- #
# One live session
# --------------------------------------------------------------------------- #
class _Channel:
    """A thread-safe queue plus the waker of the loop reading it now (a
    session's reader threads and its relay post here)."""

    def __init__(self) -> None:
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._waker: tuple[asyncio.AbstractEventLoop, asyncio.Event] | None = None

    def post(self, item: Any) -> None:
        self._q.put(item)
        with self._lock:
            waker = self._waker
        if waker is not None:
            loop, event = waker
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass  # that loop is gone; the next bind finds the item queued

    def bind(self) -> asyncio.Event:
        event = asyncio.Event()
        with self._lock:
            self._waker = (asyncio.get_running_loop(), event)
        return event

    def get_nowait(self) -> Any:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return _EMPTY


@dataclass(frozen=True)
class Facts:
    """What a kept process was started with; a call that differs is a miss."""

    system: str
    tools: str
    model: str
    effort: str
    account: tuple
    process: tuple

    @classmethod
    def of(cls, call: T.NativeCall) -> "Facts":
        return cls(
            system=call.system,
            tools=call.body,
            model=call.model_arg or "",
            effort=call.effort or "",
            account=(
                tuple(sorted((call.account_env or {}).items())) if call.account_env is not None else None,
                tuple(call.account_unset or ()),
            ),
            process=(call.exe, call.max_output_tokens, T.upstream_of(call.base_env)),
        )


class _LiveResources(T._Resources):
    """``transport._Resources`` with a :class:`LiveRoute` in place of the one-shot
    admission (``kill`` aborts it, ``close`` closes it)."""

    def prepare_live(self, call: T.NativeCall, env: dict[str, str], notify: Callable[[Any], None]) -> dict[str, str]:
        root = self.write_files(call)
        route = LiveRoute(T.upstream_of(call.base_env), call.idle_timeout_s, notify)
        self.clock.mark("relay_bound")
        with self.lock:
            self.admission = route  # type: ignore[assignment]
            cancelled = self.cancelled
        if cancelled:
            route.close()
            shutil.rmtree(root, ignore_errors=True)
            raise asyncio.CancelledError()
        env = T.loopback_unproxied(dict(env))
        env["ANTHROPIC_BASE_URL"] = route.url
        env["API_TIMEOUT_MS"] = API_TIMEOUT_MS
        self.argv = live_argv(call, root)
        return env


def live_argv(call: T.NativeCall, root: Any) -> list[str]:
    """``transport.build_argv`` with a large ``--max-turns``."""
    argv = T.build_argv(call, root)
    i = argv.index("--max-turns")
    argv[i + 1] = str(LIVE_MAX_TURNS)
    return argv


class LiveSession:
    """One kept ``claude`` process: its resources, its route, what it holds."""

    def __init__(self, call: T.NativeCall, facts: Facts, scope: Any, *, helper: bool) -> None:
        self.id = uuid.uuid4().hex
        self.res = _LiveResources()
        self.channel = _Channel()
        self.facts = facts
        self.scope = scope
        self.helper = helper
        self.chain = T._chain_key(call)
        #: new | streaming | parked_tools | parked_done | closed
        self.state = "new"
        #: The frames the CLI now holds (unmarked, as ``history_frames`` builds
        #: them): every frame written plus each answer's assistant message.
        self.known: list[dict] = []
        self.held_ids: frozenset | None = None
        self.done_key: str | None = None
        self.parked_at = 0.0
        self.stdin_open = True
        #: True once the CURRENT call's round runs on it (its own ``finally`` then
        #: owns it); cleared when a call claims it.
        self.driven = False

    @property
    def route(self) -> LiveRoute:
        return self.res.admission  # type: ignore[return-value]

    @property
    def pid(self) -> int | None:
        proc = self.res.proc
        return proc.pid if proc is not None else None

    def alive(self) -> bool:
        proc = self.res.proc
        try:
            return proc is not None and proc.poll() is None and not self.res.cancelled
        except Exception:  # noqa: BLE001
            return False

    def start_readers(self) -> None:
        proc = self.res.proc
        res = self.res
        post = self.channel.post

        def read_stdout() -> None:
            try:
                for raw in proc.stdout:  # type: ignore[union-attr]
                    item = T._parse_line(raw)
                    if item is not None:
                        post(item)
            except Exception as exc:  # noqa: BLE001 — a dead pipe ends the stream
                post(T._Unreadable(f"claude-cli: lost the CLI's output ({type(exc).__name__})"))
            finally:
                try:
                    proc.wait()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass
                res.exit_at = time.perf_counter()
                post(_EOF)

        def read_stderr() -> None:
            try:
                for chunk in iter(lambda: proc.stderr.read1(4096), b""):  # type: ignore[union-attr]
                    res.stderr_tail.append(chunk)
            except Exception:  # noqa: BLE001
                pass

        for target in (read_stdout, read_stderr):
            t = threading.Thread(target=target, daemon=True, name="claude-live-reader")
            t.start()
            res.threads.append(t)

    async def receive(self, t: T._Timeouts, settle: float | None = None) -> Any:
        """The next line of the CLI (or a relay notice, or ``_EOF``). Past the
        call's idle/total bound: the transport's timeout error. ``settle``: return
        ``_EMPTY`` after that many seconds instead."""
        wait, is_total = t.window()
        if settle is not None and settle < wait:
            wait, is_total = max(0.0, settle), None
        event = self.channel.bind()
        deadline = time.monotonic() + wait
        while True:
            event.clear()
            item = self.channel.get_nowait()
            if item is not _EMPTY:
                return item
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if is_total is None:
                    return _EMPTY
                raise t.error(is_total)
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except TimeoutError:
                if is_total is None:
                    return _EMPTY
                raise t.error(is_total) from None

    async def write(self, frame: dict, bound: float, t: T._Timeouts | None = None) -> bool:
        """False when the CLI stopped reading (it exited, or — without ``t`` — a
        write stuck). With ``t`` a stuck write is the call's timeout error, as in
        :func:`transport.run`."""
        if not self.stdin_open:
            return False
        data = T._encode(frame)
        proc = self.res.proc

        def work() -> None:
            proc.stdin.write(data)  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]

        is_total = False
        if t is not None:
            bound, is_total = t.window()
        try:
            await asyncio.wait_for(asyncio.to_thread(work), timeout=max(0.05, bound))
        except TimeoutError:
            self.stdin_open = False
            if t is not None:
                raise t.error(is_total) from None
            return False
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            self.stdin_open = False
            return False
        return True

    def close_stdin(self) -> None:
        """BLOCKING: the CLI exits on EOF (a released helper session)."""
        self.stdin_open = False
        proc = self.res.proc
        try:
            if proc is not None and proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass

    async def returncode(self) -> int:
        proc = self.res.proc
        code = proc.poll()  # type: ignore[union-attr]
        if code is None:
            code = await asyncio.to_thread(proc.wait, 5)  # type: ignore[union-attr]
        return int(code)

    def leftovers_ok(self) -> bool:
        """BLOCKING-free: drop what a parked CLI said since its step ended (status,
        rate-limit, the denial) — False when it said something only a dead or
        confused CLI says (it exited, answered, errored)."""
        while True:
            item = self.channel.get_nowait()
            if item is _EMPTY:
                return True
            if item is _EOF or isinstance(item, (T._Unreadable, _Held)):
                return False
            if isinstance(item, dict) and item.get("type") in ("assistant", "result", "stream_event"):
                return False


# --------------------------------------------------------------------------- #
# The pool
# --------------------------------------------------------------------------- #
class _Pool:
    """Live sessions, indexed by the held ids (``parked_tools``) and the done key
    (``parked_done``)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.sessions: dict[str, LiveSession] = {}
        self.by_ids: dict[frozenset, str] = {}
        self.by_done: dict[str, str] = {}
        self.tombs: dict[tuple, str] = {}
        self._reaper: threading.Thread | None = None

    # under the lock --------------------------------------------------------
    def _unindex(self, s: LiveSession) -> None:
        if s.held_ids is not None and self.by_ids.get(s.held_ids) == s.id:
            del self.by_ids[s.held_ids]
        if s.done_key is not None and self.by_done.get(s.done_key) == s.id:
            del self.by_done[s.done_key]

    def _forget(self, s: LiveSession, why: str) -> None:
        self.sessions.pop(s.id, None)
        self._unindex(s)
        s.state = "closed"
        if why:
            # Read only by a later call of the SAME scope (a new turn's first
            # call is a fresh replay by design, whatever ended the last one).
            key = (getattr(s.scope, "id", ""), s.chain)
            self.tombs.pop(key, None)
            self.tombs[key] = why
            while len(self.tombs) > _MAX_TOMBS:
                self.tombs.pop(next(iter(self.tombs)))

    # API --------------------------------------------------------------------
    def admit(self, s: LiveSession) -> tuple[bool, list[LiveSession]]:
        """Register a new streaming session under the cap: ``(admitted, victims)``
        — victims are parked sessions forgotten to make room (the caller kills
        them); not admitted when every live one is streaming."""
        cap = max(1, int(MAX_LIVE_SESSIONS))
        victims: list[LiveSession] = []
        with self.lock:
            while len(self.sessions) >= cap:
                parked = sorted(
                    (x for x in self.sessions.values() if x.state in ("parked_tools", "parked_done")),
                    key=lambda x: x.parked_at,
                )
                if not parked:
                    return False, victims
                victim = parked[0]
                self._forget(victim, "cap")
                victims.append(victim)
            s.state = "streaming"
            self.sessions[s.id] = s
        return True, victims

    def park(self, s: LiveSession, state: str, key: Any) -> bool:
        """``parked_tools`` under its held ids, or ``parked_done`` under its done
        key. False when it was forgotten meanwhile, or its scope has closed."""
        with self.lock:
            if s.id not in self.sessions or s.res.cancelled or s.scope.closed:
                return False
            self._unindex(s)
            s.state = state
            s.parked_at = time.monotonic()
            if state == "parked_tools":
                s.held_ids, s.done_key = key, None
                self.by_ids[key] = s.id
            else:
                s.held_ids, s.done_key = None, key
                self.by_done[key] = s.id
            self._ensure_reaper()
            self.cond.notify_all()
        return True

    def lookup_ids(self, ids: frozenset) -> LiveSession | None:
        with self.lock:
            sid = self.by_ids.get(ids)
            return self.sessions.get(sid) if sid is not None else None

    def lookup_done(self, key: str) -> LiveSession | None:
        with self.lock:
            sid = self.by_done.get(key)
            return self.sessions.get(sid) if sid is not None else None

    def parked_of(self, chain: str, scope: Any) -> list[LiveSession]:
        with self.lock:
            return [
                x for x in self.sessions.values()
                if x.chain == chain and x.scope is scope and x.state in ("parked_tools", "parked_done")
            ]

    def claim(self, s: LiveSession) -> str:
        """The parked state ``s`` was in, now ``streaming`` — '' when it is no
        longer parked (reaped, killed, claimed)."""
        with self.lock:
            if s.id not in self.sessions or s.state not in ("parked_tools", "parked_done"):
                return ""
            was = s.state
            self._unindex(s)
            s.state = "streaming"
            s.driven = False  # this call's round has not started yet
            return was

    def forget(self, s: LiveSession, why: str = "", *, parked_only: bool = False) -> bool:
        """Forget ``s`` (True when it was in the pool and the caller must kill it)."""
        with self.lock:
            if s.id not in self.sessions:
                return False
            if parked_only and s.state not in ("parked_tools", "parked_done"):
                return False
            self._forget(s, why)
            self.cond.notify_all()
            return True

    def take_tomb(self, chain: str, scope: Any) -> str:
        with self.lock:
            return self.tombs.pop((getattr(scope, "id", ""), chain), "")

    def live(self) -> list[LiveSession]:
        with self.lock:
            return list(self.sessions.values())

    def stop_all(self) -> int:
        """BLOCKING: stop every live session and its tree (the daemon's shutdown)."""
        with self.lock:
            every = list(self.sessions.values())
            for s in every:
                self._forget(s, "")
            self.tombs.clear()
            self.cond.notify_all()
        for s in every:
            try:
                s.res.close()
            except Exception:  # noqa: BLE001 — shutdown never raises
                log.debug("claude live: stopping a session at shutdown failed", exc_info=True)
        return len(every)

    # the reaper -------------------------------------------------------------
    def _ensure_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="claude-live-reaper")
        self._reaper.start()

    def reap_once(self, now: float | None = None) -> list[LiveSession]:
        """Forget every parked session past the idle limit and return them (the
        caller kills them outside the lock)."""
        now = time.monotonic() if now is None else now
        limit = float(PARKED_IDLE_TIMEOUT_S)
        with self.lock:
            expired = [
                s for s in self.sessions.values()
                if s.state in ("parked_tools", "parked_done") and now - s.parked_at >= limit
            ]
            for s in expired:
                self._forget(s, "reaped")
        return expired

    def _reap_loop(self) -> None:
        while True:
            for s in self.reap_once():
                log.info("claude-cli: stopping a kept Claude Code process nobody resumed (idle)")
                try:
                    s.res.close()
                except Exception:  # noqa: BLE001
                    pass
            with self.lock:
                parked = [s for s in self.sessions.values() if s.state in ("parked_tools", "parked_done")]
                if not parked:
                    self._reaper = None
                    return
                soonest = min(s.parked_at for s in parked) + float(PARKED_IDLE_TIMEOUT_S)
                self.cond.wait(timeout=max(0.05, min(30.0, soonest - time.monotonic())))


POOL = _Pool()


def live_sessions() -> list[LiveSession]:
    return POOL.live()


def stop_all() -> int:
    """BLOCKING: every kept Claude Code process goes (the daemon's shutdown)."""
    return POOL.stop_all()


# --------------------------------------------------------------------------- #
# Resume decision
# --------------------------------------------------------------------------- #
def done_key(frames: list[dict], facts: Facts, scope: Any) -> str:
    raw = json.dumps(
        [frames, [facts.system, facts.tools, facts.model, facts.effort, facts.account, facts.process],
         getattr(scope, "id", "")],
        sort_keys=True, default=str,
    )
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def _leading_results(frame: Any) -> tuple[list[str], int]:
    """``(ids, n)``: the ids of a user frame's LEADING tool_result blocks."""
    try:
        content = frame["message"]["content"]
    except (KeyError, TypeError):
        return [], 0
    if not isinstance(content, list):
        return [], 0
    ids: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            ids.append(block.get("tool_use_id"))
        else:
            break
    return ids, len(ids)


def _remainder_fits(frame: Any, state: str, held: frozenset | None) -> bool:
    if not isinstance(frame, dict) or frame.get("type") != "user":
        return False
    content = (frame.get("message") or {}).get("content") if isinstance(frame.get("message"), dict) else None
    if not isinstance(content, list) or not content:
        return False
    if state == "parked_tools":
        ids, n = _leading_results(frame)
        if len(ids) != len(set(ids)) or held is None or set(ids) != set(held):
            return False
        rest = content[n:]
        return all(isinstance(b, dict) and b.get("type") in ("text", "image") for b in rest)
    return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def why_not(s: LiveSession, call: T.NativeCall, facts: Facts, scope: Any) -> str:
    """'' when ``call`` may continue ``s``, else the miss reason (a word)."""
    if s.scope is not scope:
        return "scope"
    # The effort rides the extra body too: read it (and the model) first so the
    # reason names what the caller changed.
    for name in ("system", "model", "effort", "account", "tools", "process"):
        if getattr(s.facts, name) != getattr(facts, name):
            return name
    if not s.alive():
        return "dead"
    n = len(s.known)
    if len(call.frames) <= n or call.frames[:n] != s.known:
        return "edited"
    if len(call.frames) - n != 1:
        return "remainder"
    if not _remainder_fits(call.frames[-1], s.state, s.held_ids):
        return "remainder"
    return ""


def split_results(frame: dict) -> tuple[dict[str, Any], list]:
    """``({tool_use_id: content}, trailing blocks)`` of a resumed tools turn."""
    content = frame["message"]["content"]
    _, n = _leading_results(frame)
    results = {b["tool_use_id"]: copy.deepcopy(b.get("content")) for b in content[:n]}
    return results, copy.deepcopy(content[n:])


def _find(call: T.NativeCall, facts: Facts, scope: Any) -> tuple[LiveSession | None, str, str, list[LiveSession]]:
    """BLOCKING (a worker thread: it compares and hashes whole conversations).
    ``(session, its parked state, miss reason, stale sessions to kill)``."""
    chain = T._chain_key(call)
    candidates: list[LiveSession] = []
    if call.frames:
        ids, _ = _leading_results(call.frames[-1])
        if ids and len(ids) == len(set(ids)):
            found = POOL.lookup_ids(frozenset(ids))
            if found is not None:
                candidates.append(found)
        if not candidates:
            found = POOL.lookup_done(done_key(call.frames[:-1], facts, scope))
            if found is not None:
                candidates.append(found)
    for x in POOL.parked_of(chain, scope):
        if x not in candidates:
            candidates.append(x)
    reason = ""
    for x in candidates:
        why = why_not(x, call, facts, scope)
        if not why and x.leftovers_ok():
            was = POOL.claim(x)
            if was:
                return x, was, "", []
            why = "gone"
        reason = reason or why or "dead"
    stale = [x for x in candidates if x.scope is scope and POOL.forget(x, "", parked_only=True)]
    if not reason:
        reason = POOL.take_tomb(chain, scope) or "fresh"
    return None, "", reason, stale


# --------------------------------------------------------------------------- #
# A round
# --------------------------------------------------------------------------- #
def _mark_relay(clock: T._Clock, rec: Any) -> None:
    if T.TRACE is None or rec is None:
        return
    stamps = dict(getattr(rec, "stamps", None) or {})
    for name in ("request", "connected", "headers", "first_byte", "last_byte"):
        if name in stamps:
            clock.mark("relay_" + name, at=stamps[name],
                       **({"reused": bool(stamps.get("reused"))} if name == "connected" else {}))


async def _close(s: LiveSession) -> None:
    """Kill a session now (shielded; a cancel landing here is re-raised after)."""
    await T._teardown(s.res)


def _scope_closed(s: LiveSession) -> None:
    """The turn scope closed (run off the loop): a parked session dies now; a
    streaming one is never parked after its round (``_Pool.park``)."""
    if POOL.forget(s, "", parked_only=True):
        s.res.close()


async def _drive(
    s: LiveSession,
    call: T.NativeCall,
    attempt: T.Attempt | None,
    clock: T._Clock,
    mode: str,
    *,
    first: int,
    presented: list[dict],
) -> AsyncIterator[dict]:
    """One round on ``s`` (mode ``new`` | ``tools`` | ``done``): stream ``text`` /
    ``thinking`` frames exactly as :func:`transport.run` does, then the final. It
    ends when the relay holds the next follow-up (a tool step: the session parks)
    or the CLI's query ends (``result``). Any error kills the session and raises
    what ``transport.run`` would."""
    started = time.monotonic()
    timeouts = T._Timeouts(call, started)
    probe = call.account_env is None
    got = T.Collected()
    s.driven = True
    kept = False
    final_sent = False
    end = ""  # tools | done | boundary | error; '' = the CLI exited
    rec: Any = None
    try:
        if mode == "new":
            env = T.with_account(T.child_env(call.base_env, max_output_tokens=call.max_output_tokens), call)
            child = await asyncio.to_thread(s.res.prepare_live, call, env, s.channel.post)
            # A today's-path call of this conversation may still be exiting.
            await T._after_previous(s.res.chain)
            await asyncio.to_thread(s.res.spawn, child)
            s.start_readers()
            early: list[dict] = []
            acked = 0
            for frame in T.marked_frames(call.frames):
                if not await s.write(frame, 0.0, timeouts):
                    break  # the CLI stopped reading — read what it said below
                if frame.get("shouldQuery") is False:
                    while True:
                        event = await s.receive(timeouts)
                        if isinstance(event, T._Unreadable):
                            raise RuntimeError(str(event))
                        if event is _EOF:
                            raise T._diagnose(early, await s.returncode(), s.res.stderr_text(),
                                              "the CLI exited before acknowledging the conversation history",
                                              probe=probe)
                        if not isinstance(event, dict):
                            continue
                        early.append(event)
                        if event.get("type") == "system" and event.get("subtype") == "init":
                            clock.mark("init")
                        if event.get("type") == "result":
                            if event.get("num_turns") != 0 or event.get("is_error"):
                                raise T._diagnose(
                                    early, None, s.res.stderr_text(),
                                    "this Claude Code version did not acknowledge history replay "
                                    "(expected a zero-turn result) — update Claude Code",
                                    probe=probe,
                                )
                            acked += 1
                            break
            clock.mark("history_acked", frames=acked)

        held: frozenset | None = None
        held_at = 0.0
        #: Every tool the CLI has denied in this step (v1.427.0 fix: Claude Code
        #: denies EAGERLY, one line per tool, the moment that tool's block is
        #: complete — while the model may still be streaming the rest).
        denial: frozenset | None = None
        #: When this loop first saw the step's answer COMPLETE at the relay
        #: (message_stop captured). Before that the model is still writing.
        complete_at: float | None = None
        settled = False
        final_event: dict | None = None
        while True:
            # The CLI writes its denial lines BEFORE it POSTs the follow-up, and the
            # relay sees that POST ~30 ms later. Whichever of the two arrives
            # first, the other is given HELD_SETTLE_S: a hold whose denial lines
            # never all show ends the step anyway; a denial whose follow-up never
            # reaches the relay (a CLI that no longer behaves as measured) ends
            # the step UNHELD — the answer still goes back, the process does not
            # park, and the next call replays to a new one. The unheld clock
            # starts only once the ANSWER IS COMPLETE at the relay: an eager
            # denial of the first tool while the model is still streaming a long
            # second one (a 900-word note) is not a missing follow-up — the
            # normal idle timeout applies until then (measured on 2.1.316).
            if complete_at is None:
                now_rec = s.route.last_record(first)
                if now_rec is not None and now_rec.capture.complete:
                    complete_at = time.monotonic()
            settle = None
            if held is not None:
                settle = HELD_SETTLE_S - (time.monotonic() - held_at)
            elif denial is not None and complete_at is not None:
                settle = HELD_SETTLE_S - (time.monotonic() - complete_at)
            if settle is not None and settle <= 0:
                end = "tools" if held is not None else "unheld"
                settled = True
                break
            event = await s.receive(timeouts, settle)
            if event is _EMPTY:
                end = "tools" if held is not None else "unheld"
                settled = True
                break
            if event is _EOF:
                break
            if isinstance(event, T._Unreadable):
                raise RuntimeError(str(event))
            if isinstance(event, _Held):
                held, held_at = event.ids, time.monotonic()
                if denial == held:
                    end = "tools"
                    break
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                clock.mark("init")
            elif kind == "assistant":
                found = T._native_error_of(event)
                if found:
                    got.native_error, got.native_error_code = found
                elif isinstance(event.get("message"), dict):
                    got.assistants.append(event["message"])
            elif kind == "user":
                content = (event.get("message") or {}).get("content") if isinstance(event.get("message"), dict) else None
                ids = [
                    b.get("tool_use_id") for b in content or []
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                ] if isinstance(content, list) else []
                if ids:
                    denial = (denial or frozenset()) | frozenset(ids)
                    if held is not None and denial == held:
                        end = "tools"
                        break
            elif kind == "result":
                clock.mark("result")
                final_event = dict(event)
                # The CLI's cost covers the whole QUERY (several requests): a
                # live round never reports it.
                final_event.pop("total_cost_usd", None)
                if final_event.get("subtype") == "success" and not final_event.get("is_error"):
                    end = "done"
                elif final_event.get("subtype") == "error_max_turns":
                    end = "boundary"
                else:
                    end = "error"
                break
            elif kind == "stream_event":
                native = event.get("event") if isinstance(event.get("event"), dict) else {}
                if native.get("type") == "message_stop":
                    got.stopped = True
                delta = native.get("delta") if isinstance(native.get("delta"), dict) else {}
                piece = delta.get("text")
                if delta.get("type") == "text_delta" and isinstance(piece, str) and piece:
                    if not got.emitted:
                        clock.mark("first_text")
                    got.emitted += piece
                    yield {"type": "text", "text": piece}
                elif delta.get("type") == "thinking_delta":
                    thought = delta.get("thinking")
                    if isinstance(thought, str) and thought:
                        yield {"type": "thinking", "text": thought}

        rec = s.route.last_record(first)
        _mark_relay(clock, rec)
        if end in ("tools", "unheld"):
            # The step ended at the relay, not at a `result`: the answer is the
            # relay's capture (assemble reads it), success for the reading.
            got.results = [{"type": "result", "subtype": "success", "is_error": False, "num_turns": 1}]
            returncode = 0
            if end == "unheld":
                clock.mark("live_unheld")
                log.info("claude-cli: Claude Code did not send its follow-up after a tool step; "
                         "the next step starts a new process")
        elif end in ("done", "boundary", "error"):
            got.results = [final_event]  # type: ignore[list-item]
            returncode = 0 if end == "done" else 1
        else:  # the CLI exited: what transport.run reads after an exit
            returncode = await s.returncode()
            clock.mark("exited", at=s.res.exit_at, code=returncode)
        response, remainder = T.assemble(
            got, returncode=returncode, admission=rec, names=call.names,
            stderr=s.res.stderr_text(), probe=probe,
        )
        response.usage.pop("cost_usd", None)
        message = rec.capture.message if (rec is not None and rec.used and rec.capture.complete) else None
        if end in ("tools", "done") and message is not None:
            s.known = presented + [{"type": "assistant", "message": copy.deepcopy(message)}]
            if end == "tools":
                ids = frozenset(c.id for c in response.tool_calls)
                kept = held is not None and ids == held and POOL.park(s, "parked_tools", held)
            elif not s.helper:
                key = await asyncio.to_thread(done_key, s.known, s.facts, s.scope)
                kept = POOL.park(s, "parked_done", key)
            if kept:
                clock.mark("live_parked", tools=len(held or ()), waited=1 if settled else 0)
        if remainder:
            got.emitted += remainder
            yield {"type": "text", "text": remainder}
        clock.mark("final_released")
        final_sent = True
        yield {"type": "final", "response": response}
    finally:
        if attempt is not None:
            done_rec = rec
            if done_rec is None and s.res.admission is not None:
                done_rec = s.route.last_record(first)
            T._record_attempt(attempt, done_rec, got)
        if not kept:
            POOL.forget(s)
            if final_sent and end in ("done", "boundary") and s.alive():
                # A clean end nobody will continue (a helper's answer, a closed
                # scope, the turn cap): the CLI exits on stdin EOF, in the background.
                T._teardown_later(s.res, 0, before=s.close_stdin)
            else:
                await _close(s)


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #
def _close_quietly(sessions: list[LiveSession]) -> None:
    """BLOCKING: close each session, whatever any one of them raises."""
    for x in sessions:
        try:
            x.res.close()
        except Exception:  # noqa: BLE001 — the rest still close
            log.debug("claude live: a teardown failed", exc_info=True)


async def _close_all(sessions: list[LiveSession]) -> None:
    """Kill sessions now, shielded (the ``transport._teardown`` shape): a cancel
    landing here makes the kills short and is re-raised once they are done."""
    if not sessions:
        return
    again = False
    task = asyncio.ensure_future(asyncio.to_thread(_close_quietly, list(sessions)))
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            again = True
            for x in sessions:
                x.res.kill()
    if again:
        raise asyncio.CancelledError()


def _discard_found(fut: "asyncio.Future") -> None:
    """A call cancelled while :func:`_find` ran: the thread may still have claimed
    a session (and forgotten stale ones) — they die, off the loop."""
    if fut.cancelled() or fut.exception() is not None:
        return
    found, _, _, stale = fut.result()
    gone = list(stale)
    if found is not None and POOL.forget(found):
        gone.append(found)
    if gone:
        threading.Thread(target=_close_quietly, args=(gone,), daemon=True,
                         name="claude-live-discard").start()


async def run(call: T.NativeCall, *, attempt: T.Attempt | None = None) -> AsyncIterator[dict]:
    """Drive one call inside an open turn scope: continue the kept process of this
    conversation when the call is exactly its next step, else start a new live
    session (or, when :data:`MAX_LIVE_SESSIONS` are all streaming, today's path).
    Yields what :func:`transport.run` yields; the consumer MUST ``aclose()`` it
    when it stops early (the adapter does), as for ``transport.run``."""
    scope = _scope.current()
    inner: Any = None
    #: A session this call claimed or registered: until its round starts (and
    #: its own ``finally`` owns it), a cancel or an error here must end it.
    owned: LiveSession | None = None
    try:
        if scope is None:
            inner = T.run(call, attempt=attempt)
        else:
            clock = T._Clock()
            facts = Facts.of(call)
            fut = asyncio.ensure_future(asyncio.to_thread(_find, call, facts, scope))
            try:
                s, was, reason, stale = await asyncio.shield(fut)
            except asyncio.CancelledError:
                fut.add_done_callback(_discard_found)
                raise
            owned = s
            await _close_all(stale)
            if s is not None:
                first = s.route.record_count()
                frame = call.frames[-1]
                if was == "parked_tools":
                    results, extras = split_results(frame)
                    ok = s.route.fill(results, extras)
                    mode = "tools"
                else:
                    s.route.admit_one()
                    ok = await s.write(frame, WRITE_TIMEOUT_S)
                    mode = "done"
                if ok:
                    clock.mark("live_resume", reason=mode)
                    log.debug("claude-cli: live session reused (%s)", mode)
                    inner = _drive(s, call, attempt, clock, mode, first=first,
                                   presented=list(s.known) + [frame])
                else:
                    POOL.forget(s)
                    owned = None
                    await _close_all([s])
                    reason = "dead"
            if inner is None:
                if reason != "fresh":
                    clock.mark("live_miss", reason=reason)
                    # The reason WORD only (a fixed vocabulary): never a path, a
                    # body or an id. INFO: the benchmark's log counts the misses.
                    log.info("claude-cli: live session not reused (%s); starting a new process", reason)
                s = LiveSession(call, facts, scope, helper=_in_helper())
                s.res.clock = clock  # one call id in the trace for this round
                admitted, victims = POOL.admit(s)
                if admitted:
                    owned = s
                await _close_all(victims)
                if not admitted:
                    clock.mark("live_full")
                    log.debug("claude-cli: every kept live session is busy; this step runs one-shot")
                    inner = T.run(call, attempt=attempt)
                elif not scope.on_close(lambda s=s: _scope_closed(s)):
                    POOL.forget(s)
                    owned = None
                    inner = T.run(call, attempt=attempt)
                else:
                    clock.mark("live_new", reason=reason)
                    log.debug("claude-cli: new live session (%s)", reason)
                    inner = _drive(s, call, attempt, clock, "new", first=0, presented=list(call.frames))
        async for out in inner:
            yield out
    finally:
        if inner is not None:
            # Close the round's generator HERE (a walk-away lands on this
            # generator's yield; the round's own `finally` kills or parks).
            await inner.aclose()
        if owned is not None and not owned.driven:
            POOL.forget(owned)
            await _close_all([owned])


__all__ = [
    "API_TIMEOUT_MS", "ENV_SWITCH", "LIVE_MAX_TURNS", "LiveRoute", "LiveSession",
    "MAX_LIVE_SESSIONS", "PARKED_IDLE_TIMEOUT_S", "POOL", "applies", "live_sessions",
    "run", "stop_all",
]
