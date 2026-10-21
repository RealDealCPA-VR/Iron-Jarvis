"""``CodexNativeAdapter`` — codex-cli with REAL tool calls (IJ harness phase 2).

THE BRIDGE. Codex runs its own agent loop and asks the client to run a tool
(``item/tool/call``, then waits). Iron Jarvis's lanes call an adapter, get
tool calls back, run them through ``registry.invoke`` and call again with the
results. So one Codex TURN spans several adapter calls:

1. A call that is not a resume starts a NEW session (process + ephemeral
   thread, Iron Jarvis's tools offered as dynamic tools, Codex's own tools
   off), sends the turn, and streams until Codex asks for tools or ends the
   turn.
2. When Codex asks for tools, the call RETURNS them (``finish_reason
   "tool_use"``, the Codex call ids as ``ToolCall.id``) and the session is
   PARKED with Codex still waiting on those requests.
3. The next call whose conversation ends with the results of exactly those
   call ids CLAIMS the session, answers each waiting request with its
   ``role="tool"`` text, and streams on — to the next tool request or the end
   of the turn (the session is then closed).

A call that cannot resume (a daemon restart, an edited last turn, a process
that died, the idle reaper, a different account after a limit) is simply a
new session: the conversation so far is replayed as labelled TEXT, never a
crash. Concurrent conversations never cross: a session is keyed by the call
ids it waits on, which Codex mints per call.

IRON-PROXY (v1.301.0): a new session runs as the account Iron-Proxy picks
(its ``CODEX_HOME`` via ``Lease.apply``) and a resumed one stays on the
account it started on; a limit or a refused sign-in is reported and, while
nothing has reached the caller, retried on the next account (a new session,
history as text) — never another provider.

THE ENVIRONMENT mirrors the text adapter (``subprocess_cli``): the daemon's
own environment passes through unchanged (plus the account's home). Checked
in the 0.157.0 source: ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL`` do not
reroute a Codex model call (the base URL is the ``openai_base_url`` config
key; the env var feeds only Codex's network-proxy credential broker), while
``CODEX_API_KEY`` / ``CODEX_ACCESS_TOKEN`` are Codex's OWN ways to sign in and
take precedence over the ChatGPT login, exactly as for ``codex exec``. When
either is set, its NAME is logged once.

FALLBACK: a Codex that does not offer the app-server shapes (no app-server,
``initialize`` refused, a ``thread/start`` that does not confirm "no
environment, read-only, ephemeral", an MCP server still offering tools) is
said ONCE in the log and this call runs through today's text-only ``codex
exec`` adapter (wrapped in the prompted-tools scaffold when it carries tools,
exactly what the router would do); later calls report ``tool_use: False`` so
the router wraps them itself and the route says ``prompted-tools``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from ..base import LLMAdapter, LLMMessage, LLMResponse, ProviderError, ToolCall
from . import protocol as P
from . import session as S

log = logging.getLogger("ironjarvis.codex_native")

#: Model ids that mean "Codex's own default" (no ``model`` on thread/start).
PLACEHOLDER_MODELS = ("", "subscription", "default", "auto")

#: Codex's own sign-in environment variables (named once in the log, never valued).
_AUTH_ENV = ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
_ENV_NOTED = False
_ENV_LOCK = threading.Lock()

#: The latest account rate-limit snapshot Codex reported, per account
#: ("default" = this PC's own login, else the Iron-Proxy profile id). A side
#: channel for Phase 5 beside ``LLMResponse.extra["rate_limits"]``.
_LATEST_LIMITS: dict[str, dict[str, Any]] = {}
_LIMITS_LOCK = threading.Lock()


def latest_rate_limits() -> dict[str, dict[str, Any]]:
    """A copy of the latest snapshot per account."""
    with _LIMITS_LOCK:
        return json.loads(json.dumps(_LATEST_LIMITS))


def _account_of(lease: Any) -> str:
    return str(getattr(lease, "profile_id", "") or "default") if lease is not None else "default"


def _remember_limits(s: S.CodexSession, snapshot: dict[str, Any]) -> None:
    s.rate_limits = snapshot
    with _LIMITS_LOCK:
        _LATEST_LIMITS[_account_of(s.lease)] = dict(snapshot)


_NOTED_REASONS: set[str] = set()


def _first_time(reason: str) -> bool:
    """True the first time this process meets ``reason`` (said once)."""
    with _ENV_LOCK:
        if reason in _NOTED_REASONS:
            return False
        _NOTED_REASONS.add(reason)
        return True


def _note_env(env: Any) -> None:
    global _ENV_NOTED
    names = [k for k in _AUTH_ENV if str((env or {}).get(k, "") or "").strip()]
    if not names:
        return
    with _ENV_LOCK:
        if _ENV_NOTED:
            return
        _ENV_NOTED = True
    log.info(
        "codex-cli: %s is set in the environment; Codex uses it ahead of the ChatGPT sign-in, "
        "as `codex exec` does (the environment passes through unchanged)",
        ", ".join(names),
    )


class _Unsupported(Exception):
    """This Codex cannot run the native path (``cache``: a fact about the
    binary, kept as the verdict; else a fact about this start only)."""

    def __init__(self, reason: str, *, cache: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cache = cache


class _Missed(Exception):
    """A claimed session could not be resumed before anything streamed."""


@dataclass
class _Attempt:
    """What one attempt did, for Iron-Proxy (the claude transport's shape)."""

    emitted: bool = False
    signal: dict[str, Any] | None = None


@dataclass
class _Segment:
    parked: bool = False
    done: bool = False


async def _teardown(s: S.CodexSession, *, graceful: bool = False) -> None:
    """Stop a session that is not parked. A turn that ENDED closes in the
    background (stdin closed, the measured exit is ~0.1 s, the tree killed
    after :data:`session.EXIT_GRACE_S` at worst) so the answer is never held
    for it. Anything else — an error, a cancel, a walk-away — kills the tree
    NOW, shielded: a second cancel must not abandon the kill half-way, and a
    cancel that lands here is re-raised once the process is gone."""
    S.POOL.forget(s)
    if graceful:
        threading.Thread(
            target=s.kill, kwargs={"graceful": True}, daemon=True, name="codex-session-close"
        ).start()
        return
    again = False
    task = asyncio.ensure_future(asyncio.to_thread(s.kill, graceful=graceful))
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            again = True
    if again:
        raise asyncio.CancelledError()


async def _teardown_all(sessions: list[S.CodexSession]) -> None:
    for s in sessions:
        await _teardown(s)


def _alive(s: S.CodexSession) -> bool:
    proc = s.proc
    try:
        return proc is not None and proc.poll() is None and not s.killed
    except Exception:  # noqa: BLE001
        return False


def _which_cli(binary: str) -> str | None:
    from ..subprocess_cli import _which_cli as resolve

    return resolve(binary)


class CodexNativeAdapter(LLMAdapter):
    """codex-cli over the user's own ``codex app-server``: native tool calls,
    streaming text and reasoning, token usage and the account's rate limits.
    Iron Jarvis executes every tool."""

    provider = P.PROVIDER

    def __init__(
        self,
        *,
        model: str = "subscription",
        which: Callable[[str], str | None] = _which_cli,
        runner: Callable[..., tuple[int, str, str]] | None = None,
        idle_timeout_s: float | None = None,
    ) -> None:
        self.model = model
        self._which = which
        #: Only the text-only FALLBACK uses an injected runner (test doubles).
        self._runner = runner
        self._idle_timeout_s = idle_timeout_s

    def capabilities(self) -> dict[str, Any]:
        # Optimistic until a start has said otherwise: the verdict is cached
        # per codex binary; once negative the router wraps the prompted-tools
        # scaffold itself (and the route says so).
        v = S.verdict()
        native = v is None or v.ok or v.expired()
        caps: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "tool_use": native,
            "vision": False,
        }
        if native:
            caps["tool_use_mode"] = "native"
        return caps

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        final: LLMResponse | None = None
        agen = self._frames(system, messages, tools, reasoning)
        try:
            async for frame in agen:
                if frame.get("type") == "final":
                    final = frame["response"]
        finally:
            await agen.aclose()
        if final is None:  # pragma: no cover — every path ends in final or raises
            raise RuntimeError(f"{P.PROVIDER}: Codex ended without an answer")
        return final

    async def stream(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        agen = self._frames(system, messages, tools, reasoning)
        try:
            async for frame in agen:
                yield frame
        finally:
            await agen.aclose()

    # ------------------------------------------------------------------ #
    # the call
    # ------------------------------------------------------------------ #
    async def _frames(
        self, system: str, messages: list[LLMMessage], tools: list[dict[str, Any]], reasoning: str
    ) -> AsyncIterator[dict[str, Any]]:
        from ....iron_proxy import accounts

        exe = await asyncio.to_thread(self._which, P.BINARY)
        if not exe:
            raise RuntimeError(f"{P.PROVIDER}: the 'codex' CLI is not installed/on PATH")
        key = await asyncio.to_thread(S.exe_key, exe)
        known = S.verdict_for(key)
        if known is not None and not known.ok:
            async for frame in self._fallback(system, messages, tools, reasoning):
                yield frame
            return

        tried: list[str] = []
        lease: Any = _UNSET
        answered = P.answered_calls(messages)
        if answered is None:
            # The conversation moved past calls a parked session waits on (an
            # exit tool, a final-answer nudge, an edit): stop it now.
            await _teardown_all(S.POOL.release_calls(P.last_call_ids(messages)))
        else:
            ids, results = answered
            claimed, stale = S.POOL.claim(ids)
            await _teardown_all(stale)
            if claimed is not None and not _alive(claimed):
                await _teardown(claimed)
                claimed = None
            if claimed is not None:
                attempt = _Attempt()
                try:
                    async for frame in self._run_resume(claimed, results, attempt):
                        yield frame
                    return
                except _Missed:
                    log.info("codex-cli: the live Codex session could not continue; starting a new one")
                except Exception as exc:  # noqa: BLE001 — sorted below (CancelledError is not caught)
                    if claimed.lease is None:
                        raise
                    lease = await accounts.retry_lease(
                        claimed.lease, exc, attempt.signal, tried, retry_ok=not attempt.emitted
                    )

        if lease is _UNSET:
            # v1.301.0: the Iron-Proxy account to run as (None = this PC's own
            # login). Blocking HTTP: off the loop.
            lease = await asyncio.to_thread(accounts.lease, P.PROVIDER)
        while True:
            attempt = _Attempt()
            try:
                async for frame in self._run_new(exe, key, lease, system, messages, tools, reasoning, attempt):
                    yield frame
                return
            except _Unsupported as exc:
                if exc.cache:
                    S.set_verdict(key, False, exc.reason)
                elif _first_time(exc.reason):
                    log.warning(
                        "codex-cli: native tool calls are not safe on this start (%s) — calls like "
                        "it use the text-only `codex exec` path", exc.reason,
                    )
                async for frame in self._fallback(system, messages, tools, reasoning):
                    yield frame
                return
            except Exception as exc:  # noqa: BLE001 — sorted below
                if lease is None:
                    raise
                lease = await accounts.retry_lease(
                    lease, exc, attempt.signal, tried, retry_ok=not attempt.emitted
                )

    async def _fallback(
        self, system: str, messages: list[LLMMessage], tools: list[dict[str, Any]], reasoning: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Today's text-only ``codex exec`` path, unchanged; with tools, the
        prompted-tools scaffold the router itself applies to a text-only
        adapter (v1.131.0)."""
        from ..prompted_tools import PromptedToolsAdapter
        from ..subprocess_cli import make_codex_cli

        kw: dict[str, Any] = {"model": self.model, "which": self._which}
        if self._runner is not None:
            kw["runner"] = self._runner
        adapter: LLMAdapter = make_codex_cli(**kw)
        if tools:
            adapter = PromptedToolsAdapter(adapter)
        extra = {"reasoning": reasoning} if reasoning else {}
        agen = adapter.stream(system=system, messages=messages, tools=tools, **extra)
        try:
            async for frame in agen:
                yield frame
        finally:
            await agen.aclose()

    # ------------------------------------------------------------------ #
    # a new session
    # ------------------------------------------------------------------ #
    async def _run_new(
        self,
        exe: str,
        key: tuple,
        lease: Any,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        reasoning: str,
        attempt: _Attempt,
    ) -> AsyncIterator[dict[str, Any]]:
        env = lease.apply(dict(os.environ)) if lease is not None else None
        _note_env(env if env is not None else os.environ)
        specs, names = P.dynamic_tools(tools)
        try:
            base, items, _replayed = P.turn_input(system, messages, names)
        except P.HistoryError as exc:
            raise RuntimeError(str(exc)) from None
        s = S.CodexSession(exe, env, lease=lease)
        s.names = names
        try:
            victims = S.POOL.admit(s)
        except S.PoolFull as exc:
            # Iron Jarvis's own cap, not an outage: never retried, never moved
            # to another provider (that is the user's privacy decision).
            full = ProviderError(f"{P.PROVIDER}: {exc}", transient=False)
            full.no_failover = True  # type: ignore[attr-defined]
            raise full from None
        await _teardown_all(victims)
        seg = _Segment()
        try:
            await self._start(s, base, specs, items, reasoning, key)
            async for frame in self._segment(s, attempt, seg):
                yield frame
        finally:
            if not seg.parked:
                await _teardown(s, graceful=seg.done)

    async def _start(
        self,
        s: S.CodexSession,
        base: str,
        specs: list[dict[str, Any]],
        items: list[dict[str, Any]],
        reasoning: str,
        key: tuple,
    ) -> None:
        """initialize -> config/read -> thread/start -> mcpServerStatus/list ->
        turn/start. Everything before turn/start makes NO model call: it is
        also the feature check (the verdict is recorded before the turn)."""
        from .... import __version__

        timeout = float(S.START_TIMEOUT_S)
        try:
            await asyncio.to_thread(s.spawn)
        except FileNotFoundError:
            raise RuntimeError(f"{P.PROVIDER}: the 'codex' CLI is not installed/on PATH") from None
        except OSError as exc:
            raise RuntimeError(f"{P.PROVIDER}: could not start Codex ({type(exc).__name__}: {exc})") from None
        try:
            init = await s.request(
                "initialize",
                {"clientInfo": {"name": "iron-jarvis", "title": "Iron Jarvis", "version": __version__},
                 "capabilities": {"experimentalApi": True}},
                timeout,
            )
        except S.SessionGone:
            said = s.stderr_text().strip().splitlines()
            raise _Unsupported(
                "`codex app-server` exited at start" + (f": {said[-1][:200]}" if said else "")
            ) from None
        except TimeoutError:
            raise P.typed(ProviderError(
                f"{P.TIMEOUT_PREFIX} — `codex app-server` did not answer in {timeout:.0f}s",
                transient=True), "timeout") from None
        if "error" in init:
            raise _Unsupported(f"initialize was refused: {_rpc_words(init)}")
        result = init.get("result")
        if not isinstance(result, dict) or not result.get("userAgent"):
            raise _Unsupported("initialize answered in an unknown shape")
        mcp_servers: list[str] = []
        try:
            await s.notify("initialized")
            cfg = await s.request("config/read", {"includeLayers": False}, timeout)
            servers = ((cfg.get("result") or {}).get("config") or {}).get("mcp_servers")
            if isinstance(servers, dict):
                mcp_servers = [str(n) for n in servers]
            # The account's limits, answered whenever they arrive (no model call).
            await s.side_request("account/rateLimits/read", None, "rate_limits")
        except (S.SessionGone, TimeoutError):
            raise _Unsupported("config/read did not answer") from None
        model = None if str(self.model or "").strip().lower() in PLACEHOLDER_MODELS else self.model
        params = P.thread_start_params(
            base_instructions=base, specs=specs, cwd=s.cwd, model=model, mcp_servers=mcp_servers
        )
        try:
            started = await s.request("thread/start", params, timeout)
        except (S.SessionGone, TimeoutError):
            raise _Unsupported("thread/start did not answer") from None
        if "error" in started:
            words = _rpc_words(started)
            if _SHAPE_WORDS.search(words):
                raise _Unsupported(f"thread/start refused the request: {words}")
            raise ProviderError(f"{P.PROVIDER}: Codex would not start a conversation. Codex said: {words}")
        res = started.get("result")
        problem = P.start_problem(res)
        if problem:
            raise _Unsupported(problem)
        s.thread_id = res["thread"]["id"]
        s.served_model = str(res.get("model") or res["thread"].get("model") or "")
        try:
            listed = await s.request(
                "mcpServerStatus/list", {"threadId": s.thread_id, "detail": "toolsAndAuthOnly"}, timeout
            )
        except (S.SessionGone, TimeoutError):
            raise _Unsupported("mcpServerStatus/list did not answer") from None
        if "error" in listed:
            raise _Unsupported(f"could not confirm the MCP servers are off: {_rpc_words(listed)}")
        problem = P.mcp_problem(listed.get("result"))
        if problem:
            raise _Unsupported(problem, cache=False)
        S.set_verdict(key, True)
        effort = reasoning if reasoning in ("low", "medium", "high") else ""
        try:
            turn = await s.request("turn/start", P.turn_start_params(s.thread_id, items, effort), timeout)
        except S.SessionGone:
            raise await self._gone(s, emitted=False) from None
        except TimeoutError:
            raise P.typed(ProviderError(
                f"{P.TIMEOUT_PREFIX} — turn/start did not answer in {timeout:.0f}s", transient=True),
                "timeout") from None
        if "error" in turn:
            raise ProviderError(f"{P.PROVIDER}: Codex would not start the turn. Codex said: {_rpc_words(turn)}")
        tid = ((turn.get("result") or {}).get("turn") or {}).get("id")
        if isinstance(tid, str):
            s.turn_id = tid

    # ------------------------------------------------------------------ #
    # a resumed session
    # ------------------------------------------------------------------ #
    async def _run_resume(
        self, s: S.CodexSession, results: dict[str, str], attempt: _Attempt
    ) -> AsyncIterator[dict[str, Any]]:
        seg = _Segment()
        try:
            try:
                for cid, rid in list(s.pending.items()):
                    await s.respond(rid, P.tool_response(results[cid]))
            except S.SessionGone:
                raise _Missed() from None
            s.pending.clear()
            async for frame in self._segment(s, attempt, seg):
                yield frame
        finally:
            if not seg.parked:
                await _teardown(s, graceful=seg.done)

    # ------------------------------------------------------------------ #
    # one segment: until Codex asks for tools or the turn ends
    # ------------------------------------------------------------------ #
    async def _gone(self, s: S.CodexSession, *, emitted: bool, attempt: _Attempt | None = None) -> Exception:
        """The error for an app-server that stopped. Its last stderr lines
        decide whether it was a refused sign-in (read after its stderr reader
        has drained — off the loop, bounded)."""
        await asyncio.to_thread(s.drain_stderr, 1.0)
        said = s.stderr_text().strip()
        if said:
            log.info("codex-cli: the app-server stopped; its last output: %s", said[-400:])
        from ...cli_auth import is_sign_in_refusal, note_cli_failure

        if is_sign_in_refusal(said):
            line = said.splitlines()[-1]
            err = ProviderError(P.signed_out_text(line), status_code=401)
            if attempt is not None:
                attempt.signal = {"status": 401, "text": line}
            if s.lease is None:
                note_cli_failure(P.BINARY, str(err))
            return err
        if emitted:
            return P.typed(ProviderError(f"{P.INCOMPLETE_PREFIX}: the Codex app-server stopped mid-answer"),
                           "interrupted", partial=True)
        return P.typed(ProviderError(f"{P.STOPPED_PREFIX} before it answered — try again", transient=True),
                       "interrupted")

    async def _segment(
        self, s: S.CodexSession, attempt: _Attempt, seg: _Segment
    ) -> AsyncIterator[dict[str, Any]]:
        started = time.monotonic()
        idle = float(self._idle_timeout_s or S.DEFAULT_IDLE_TIMEOUT_S)
        deadline = started + float(S.TOTAL_TIMEOUT_S)
        start_total = dict(s.usage_total) if isinstance(s.usage_total, dict) else None
        text = ""
        thinking: list[str] = []
        item_text: dict[str, str] = {}
        last_item: Any = None
        calls: list[ToolCall] = []
        settle_until: float | None = None
        turn_error: Any = None
        finish = "stop"
        ended = False
        while True:
            now = time.monotonic()
            if settle_until is not None:
                wait = settle_until - now
                if wait <= 0:
                    break
            else:
                wait = min(idle, deadline - now)
                if wait <= 0:
                    raise P.typed(ProviderError(
                        f"{P.TIMEOUT_PREFIX} after {S.TOTAL_TIMEOUT_S:.0f}s", transient=True), "timeout")
            try:
                ev = await s.next_event(wait)
            except TimeoutError:
                if settle_until is not None:
                    break
                if deadline - time.monotonic() <= 0:
                    raise P.typed(ProviderError(
                        f"{P.TIMEOUT_PREFIX} after {S.TOTAL_TIMEOUT_S:.0f}s", transient=True),
                        "timeout") from None
                raise P.typed(ProviderError(
                    f"{P.TIMEOUT_PREFIX} — no output for {idle:.0f}s", transient=True), "timeout") from None
            if S.is_eof(ev):
                raise await self._gone(s, emitted=attempt.emitted, attempt=attempt)
            if isinstance(ev, S.Bad):
                log.debug("codex-cli: skipped a stdout line that is not JSON: %r", ev.line[:120])
                continue
            if S.is_response(ev):
                self._side_answer(s, ev)
                continue
            if S.is_server_request(ev):
                try:
                    call = await self._server_request(s, ev)
                except S.SessionGone:
                    raise await self._gone(s, emitted=attempt.emitted, attempt=attempt) from None
                if call is not None:
                    calls.append(call)
                    settle_until = time.monotonic() + float(S.SETTLE_S)
                continue
            if not S.is_notification(ev):
                continue
            method = ev["method"]
            p = ev.get("params") if isinstance(ev.get("params"), dict) else {}
            if method == "item/agentMessage/delta":
                piece, item = p.get("delta"), p.get("itemId")
                if isinstance(piece, str) and piece:
                    if last_item is not None and item != last_item and text and not text.endswith("\n\n"):
                        sep = "\n" if text.endswith("\n") else "\n\n"
                        text += sep
                        yield {"type": "text", "text": sep}
                    last_item = item
                    item_text[str(item)] = item_text.get(str(item), "") + piece
                    text += piece
                    attempt.emitted = True
                    yield {"type": "text", "text": piece}
            elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                piece = p.get("delta")
                if isinstance(piece, str) and piece:
                    thinking.append(piece)
                    yield {"type": "thinking", "text": piece}
            elif method == "item/reasoning/summaryPartAdded":
                if thinking and not thinking[-1].endswith("\n\n"):
                    thinking.append("\n\n")
                    yield {"type": "thinking", "text": "\n\n"}
            elif method == "item/completed":
                item = p.get("item") if isinstance(p.get("item"), dict) else {}
                kind = item.get("type")
                if kind == "agentMessage":
                    full, iid = item.get("text"), str(item.get("id"))
                    streamed = item_text.get(iid, "")
                    if isinstance(full, str) and full != streamed and full.startswith(streamed):
                        rest = full[len(streamed):]
                        if not streamed and text and not text.endswith("\n\n"):
                            rest = ("\n" if text.endswith("\n") else "\n\n") + rest
                        item_text[iid] = full
                        last_item = item.get("id")
                        text += rest
                        attempt.emitted = True
                        yield {"type": "text", "text": rest}
                    elif isinstance(full, str) and full != streamed:
                        log.info("codex-cli: an answer's final text differs from what streamed; "
                                 "the streamed text stands")
                elif kind in P.BUILTIN_ITEM_TYPES:
                    s.builtin_items.append(str(kind))
                    log.warning("codex-cli: Codex ran one of its own tools (%s) — it should be off", kind)
            elif method == "thread/tokenUsage/updated":
                usage = p.get("tokenUsage") if isinstance(p.get("tokenUsage"), dict) else {}
                if isinstance(usage.get("total"), dict) and p.get("threadId") in (None, s.thread_id):
                    s.usage_total = usage["total"]
                if settle_until is not None:
                    settle_until = min(settle_until, time.monotonic() + 0.02)
            elif method == "account/rateLimits/updated":
                snapshot = P.rate_limit_snapshot(p.get("rateLimits"))
                if snapshot is not None:
                    _remember_limits(s, snapshot)
            elif method == "error":
                if not p.get("willRetry"):
                    turn_error = p.get("error")
            elif method == "turn/started":
                tid = (p.get("turn") or {}).get("id") if isinstance(p.get("turn"), dict) else None
                if isinstance(tid, str):
                    s.turn_id = tid
            elif method == "turn/completed":
                if p.get("threadId") not in (None, s.thread_id):
                    continue
                turn = p.get("turn") if isinstance(p.get("turn"), dict) else {}
                status = turn.get("status")
                if status == "completed":
                    ended = True
                    break
                if status == "interrupted":
                    raise P.typed(ProviderError(
                        f"{P.PROVIDER}: Codex stopped the turn before it finished",
                        transient=not attempt.emitted), "interrupted", partial=attempt.emitted)
                err = turn.get("error") or turn_error
                if P.is_max_tokens(err):
                    finish = "max_tokens"
                    ended = True
                    break
                failure = P.turn_failure(err, emitted=attempt.emitted, rate_limits=s.rate_limits)
                attempt.signal = failure.signal
                if failure.sign_in and s.lease is None:
                    from ...cli_auth import note_cli_failure

                    note_cli_failure(P.BINARY, str(failure.error))
                raise failure.error

        if ended and calls:
            # The turn ended while calls were still waiting: Codex cancelled
            # them ("cancelled before receiving a response"); none is handed
            # to the lane, which would otherwise run a tool nobody waits for.
            log.info("codex-cli: the turn ended with %d tool call(s) unanswered; dropped", len(calls))
            calls = []
            s.pending.clear()
        usage = P.usage_between(start_total, s.usage_total)
        cost = _list_price(s.served_model or self.model, usage)
        if cost is not None:
            usage["cost_usd"] = cost
        extra: dict[str, Any] = {"codex": {"thread": s.thread_id, "model": s.served_model}}
        if s.rate_limits is not None:
            extra["rate_limits"] = dict(s.rate_limits)
        if s.refused_builtins or s.builtin_items:
            extra["codex_builtins"] = {"denied": list(s.refused_builtins), "ran": list(s.builtin_items)}
        response = LLMResponse(
            text=text,
            tool_calls=list(calls),
            finish_reason="tool_use" if calls else finish,
            usage=usage,
            thinking="".join(thinking).strip(),
            extra=extra,
        )
        if calls:
            if not S.POOL.park(s):
                raise await self._gone(s, emitted=attempt.emitted)
            seg.parked = True
        else:
            seg.done = True
        if s.lease is not None:
            from ....iron_proxy import accounts

            await accounts.report_success_async(
                s.lease, usage, int((time.monotonic() - started) * 1000), s.served_model or self.model
            )
        yield {"type": "final", "response": response}

    def _side_answer(self, s: S.CodexSession, ev: dict[str, Any]) -> None:
        purpose = s.side_requests.pop(ev.get("id"), None)
        if purpose == "rate_limits" and isinstance(ev.get("result"), dict):
            snapshot = P.rate_limit_snapshot(ev["result"].get("rateLimits"))
            if snapshot is not None:
                _remember_limits(s, snapshot)

    async def _server_request(self, s: S.CodexSession, ev: dict[str, Any]) -> ToolCall | None:
        """Answer (or hold) one request Codex sent us. Returns the ToolCall to
        hand the lane for a dynamic tool call; None when answered here."""
        method, rid = ev["method"], ev["id"]
        p = ev.get("params") if isinstance(ev.get("params"), dict) else {}
        if method == "item/tool/call":
            tool, cid = p.get("tool"), p.get("callId")
            namespace = p.get("namespace")
            name = s.names.ours(tool) if s.names is not None and namespace in (None, "", "functions") else None
            if p.get("threadId") != s.thread_id or name is None or not isinstance(cid, str) or not cid:
                await s.respond(rid, {
                    "contentItems": [{"type": "inputText", "text": (
                        f"Iron Jarvis does not offer a tool named {str(tool)[:80]!r} in this "
                        "conversation; nothing was run.")}],
                    "success": False,
                })
                return None
            args = p.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            s.pending[cid] = rid
            return ToolCall(id=cid, name=name, arguments=args if isinstance(args, dict) else {})
        if method in P.APPROVAL_DENIALS:
            s.refused_builtins.append(method)
            log.warning(
                "codex-cli: Codex asked to run one of its own tools (%s) and was refused — "
                "Iron Jarvis runs every tool itself", method,
            )
            await s.respond(rid, P.APPROVAL_DENIALS[method])
            return None
        if method == "item/tool/requestUserInput":
            await s.respond(rid, {"answers": {}})
            return None
        if method == "mcpServer/elicitation/request":
            await s.respond(rid, {"action": "decline"})
            return None
        if method == "currentTime/read":
            await s.respond(rid, {"currentTimeAt": int(time.time())})
            return None
        await s.respond_error(rid, -32601, f"{method} is not supported by Iron Jarvis")
        return None


_UNSET = object()

#: Words in a ``thread/start`` refusal that mean "this Codex does not know a
#: field or capability we send" (a version question, not the user's config).
#: Deliberately narrow: a refusal about something else (a model id this
#: account cannot use) is the user's to see in Codex's words, never a reason
#: to stop using the native path.
_SHAPE_WORDS = re.compile(
    r"experimental|dynamicTools|dynamic tool|unknown field|unknown variant|environments",
    re.IGNORECASE,
)


def _rpc_words(answer: dict[str, Any]) -> str:
    err = answer.get("error") if isinstance(answer.get("error"), dict) else {}
    words = str(err.get("message") or err or "no reason given").strip()
    return words[:400] or "no reason given"


def _list_price(model: str, usage: dict[str, Any]) -> float | None:
    """The list-price EQUIVALENT of this call (``eval.pricing``: codex-cli is
    priced on OpenAI's rows and flagged as an equivalent, never a charge)."""
    try:
        from ....eval import pricing

        if not pricing.is_priced(P.PROVIDER, model):
            return None
        return pricing.cost_for_usage(P.PROVIDER, model, usage)
    except Exception:  # noqa: BLE001 — cost analytics never fail a call
        return None


def make_codex_native(**kw: Any) -> CodexNativeAdapter:
    return CodexNativeAdapter(**kw)
