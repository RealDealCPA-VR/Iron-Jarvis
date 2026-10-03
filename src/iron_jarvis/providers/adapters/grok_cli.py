"""Grok CLI adapter (§5 — CLI-session provider class).

Runs real inference through the Grok CLI's *on-disk session* — no xAI API key.
The bearer token the CLI keeps in ``~/.grok/auth.json`` calls the same proxy the
CLI itself uses (``cli-chat-proxy.grok.com/v1``), so a logged-in Grok CLI
doubles as a routable Iron Jarvis provider.

The wire shape was verified LIVE (2026-07-04) — do not "simplify" it blind:

* Endpoint: ``POST {base_url}/responses`` — the OpenAI **Responses** API shape,
  Server-Sent-Events stream (``stream: true``, ``store: false``).
* Required headers (the proxy 426s "version (none) is outdated" without the
  version one, and stalls without the identifier):
    - ``Authorization: Bearer <session key>``
    - ``x-grok-client-version: <cli version>``
    - ``x-grok-client-identifier: grok-shell``
    - ``User-Agent: grok-shell/<ver> (…)``
    - ``Accept: text/event-stream``
* Message items use a **plain-string** ``content`` (not a parts array).
* The stream ends with a ``response.completed`` event whose ``response.output``
  array carries the assistant ``message`` (``output_text`` parts) + any
  ``function_call`` items + ``usage``.

The credential is read fresh each call from ``cli_detect.grok_session()`` (the
CLI refreshes it in place). An expired session raises a clear, catchable error.
The async HTTP client is injectable so tests stay offline.

IRON-PROXY ACCOUNTS (v1.301.0, ``iron_proxy/accounts.py``): when Iron-Proxy is on
and has a Grok CLI account, each call reads the session from THAT account's
``GROK_HOME`` (never the process environment, which other calls share),
reports a limit/sign-in refusal (Iron-Proxy parks the account) or a success,
and retries with the next Grok account only while nothing has streamed. Off,
not running, or no account: exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

from ..cli_detect import GROK_PROXY_BASE, grok_session, grok_session_expired
from .base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ToolCall,
    provider_error_from_response,
)


class GrokCliAdapter(LLMAdapter):
    provider = "grok-cli"

    def capabilities(self) -> dict[str, Any]:
        # The Grok CLI proxy speaks the Responses API with function tools, so it
        # CAN drive the agent tool loop — but it carries no inline image path,
        # so vision is off (the router prefers an API adapter when images are
        # present).
        return {"provider": self.provider, "model": self.model, "tool_use": True, "vision": False}

    def __init__(
        self,
        model: str = "grok-build",
        *,
        session_provider: Callable[[], dict[str, Any] | None] | None = None,
        http: Any = None,
        max_tokens: int = 4096,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        #: How the current session (token/base_url/version/expiry) is resolved.
        #: Defaults to reading ``~/.grok`` fresh; injectable for tests.
        self._session_provider = session_provider or grok_session
        self._http = http
        self.max_tokens = max_tokens
        self._base_url = (base_url or GROK_PROXY_BASE).rstrip("/")

    # -- transport ----------------------------------------------------------
    def _client(self) -> Any:
        if self._http is None:
            import httpx  # lazy: keep import cost off the offline path

            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    # -- request shaping (Responses API, string content) --------------------
    @staticmethod
    def _to_input(system: str, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Map the history to Responses ``input`` items with string content.

        Tool results become ``function_call_output`` items; an assistant turn
        that called tools is replayed as its ``function_call`` items (the proxy
        is stateless with ``store: false`` — the full exchange is re-sent each
        step).
        """
        items: list[dict[str, Any]] = []
        if system:
            items.append({"type": "message", "role": "system", "content": system})
        for m in messages:
            if m.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.tool_call_id,
                        "output": m.content,
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                if m.content:
                    items.append(
                        {"type": "message", "role": "assistant", "content": m.content}
                    )
                for tc in m.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        }
                    )
            else:
                items.append(
                    {"type": "message", "role": m.role, "content": m.content}
                )
        return items

    @staticmethod
    def _to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Responses API uses a FLAT function-tool shape (no nested "function")."""
        return [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            }
            for t in tools
        ]

    # -- response parsing (SSE) ---------------------------------------------
    @staticmethod
    def _parse_sse(raw: str) -> LLMResponse:
        """Collect the final answer from the ``response.completed`` SSE event.

        That single event's ``response.output`` is the fully-accumulated result
        — equivalent to summing every delta.
        """
        completed: dict[str, Any] | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "response.completed":
                completed = event.get("response") or {}
        if completed is None:
            raise RuntimeError(
                "grok-cli: stream ended without response.completed: " + raw[:300]
            )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for item in completed.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        text_parts.append(part.get("text") or "")
            elif kind == "function_call":
                args_str = item.get("arguments") or ""
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    ToolCall(
                        id=item.get("call_id") or item.get("id") or "",
                        name=item.get("name", ""),
                        arguments=args,
                    )
                )
        usage = completed.get("usage") or {}
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else "stop",
            usage={
                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
            },
        )

    # -- the interface ------------------------------------------------------
    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (v1.203.0): accepted so callers can pass them
        # uniformly across adapters; IGNORED — subscription CLI backend, not
        # part of the openai-compat guided-decoding family this wave.
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        from ...iron_proxy import accounts

        # v1.301.0: the Iron-Proxy account to run as (None = the default
        # session, exactly as before). Blocking HTTP: off the loop.
        lease = await asyncio.to_thread(accounts.lease, self.provider)
        if lease is None:
            return await self._complete_once(system, messages, tools, None)
        tried: list[str] = []
        while True:
            started = time.monotonic()
            try:
                resp = await self._complete_once(system, messages, tools, lease)
            except Exception as exc:  # noqa: BLE001 — re-raised unless a retry is due
                lease = await accounts.retry_lease(lease, exc, _account_signal(exc), tried)
                continue
            await accounts.report_success_async(
                lease, resp.usage, int((time.monotonic() - started) * 1000), self.model
            )
            return resp

    async def _session(self, lease: Any) -> dict[str, Any] | None:
        """The session to call with: the default provider's, or — for an
        Iron-Proxy account — the one in THAT account's home (off the loop)."""
        if lease is None:
            return await asyncio.to_thread(self._session_provider)
        return await asyncio.to_thread(account_session, lease.home)

    async def _complete_once(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        lease: Any,
    ) -> LLMResponse:
        # Resolve the session off the loop (a small file read, but keep the
        # contract identical to token-refreshing adapters).
        session = await self._session(lease)
        if lease is not None and (not session or not session.get("token") or grok_session_expired(session)):
            raise _account_not_signed_in(lease)
        if not session or not session.get("token"):
            raise RuntimeError(
                "grok-cli: no Grok session found — run `grok login` "
                "(this provider uses the CLI's on-disk session, not an API key)."
            )
        if grok_session_expired(session):
            raise RuntimeError(
                "grok-cli: the Grok session has expired — re-run `grok login`."
            )
        token = session["token"]
        version = session.get("version") or "0.2.82"
        base_url = (session.get("base_url") or self._base_url).rstrip("/")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "x-grok-client-version": str(version),
            "x-grok-client-identifier": "grok-shell",
            "x-grok-model-override": self.model,
            "User-Agent": f"grok-shell/{version} (windows; x86_64)",
            "Accept": "text/event-stream",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "input": self._to_input(system, messages),
            "max_output_tokens": self.max_tokens,
            "store": False,  # the proxy keeps no server-side state
            "stream": True,  # the endpoint is SSE-only
        }
        if tools:
            body["tools"] = self._to_tools(tools)
            body["tool_choice"] = "auto"

        resp = await self._client().post(
            f"{base_url}/responses", headers=headers, json=body
        )
        # Fail LOUDLY on an HTTP error — a 426 (client too old), 401 (bad
        # token) or 5xx must raise so the router fails over, never a blank reply.
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            detail = _error_detail(resp)
            if status == 426:
                detail = (
                    f"{detail} (Iron Jarvis sent x-grok-client-version="
                    f"{version}; run `grok update` if the proxy rejects it)"
                )
            # Typed error so the router classifies transient (429/5xx) vs
            # permanent (401/426) by status and honours any Retry-After.
            raise _with_signal(provider_error_from_response("grok-cli", resp, detail), resp, detail)
        return self._parse_sse(getattr(resp, "text", "") or "")

    # -- streaming (FX-01) --------------------------------------------------
    async def stream(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (v1.203.0): accepted-ignored, as in complete().
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """Real token stream over the Responses SSE endpoint (FX-01).

        Emits ``{"type":"text","text": <delta>}`` for each
        ``response.output_text.delta`` event as it arrives.
        ``response.function_call_arguments.delta`` events are tool-argument
        fragments — they are re-aggregated by the terminal ``response.completed``
        event, so they ride inside the final response rather than as text frames.
        The closing ``{"type":"final","response": LLMResponse}`` is built by the
        SAME :meth:`_parse_sse` that :meth:`complete` uses, so it is identical.

        On ANY failure BEFORE the first frame — including the injected offline
        transport lacking a streaming surface — we degrade to the base
        (non-streaming) stream instead of fabricating output. A failure
        MID-stream re-raises honestly rather than re-running and double-emitting.

        With an Iron-Proxy account (v1.301.0) a limit/sign-in refusal before the
        first frame is reported and the next Grok account streams instead; any
        other early failure degrades exactly as above.
        """
        from ...iron_proxy import accounts

        lease = await asyncio.to_thread(accounts.lease, self.provider)
        tried: list[str] = []
        while True:
            started = False
            t0 = time.monotonic()
            try:
                async for frame in self._stream_sse(
                    system=system, messages=messages, tools=tools, lease=lease
                ):
                    started = True
                    if frame.get("type") == "final" and lease is not None:
                        await accounts.report_success_async(
                            lease, frame["response"].usage,
                            int((time.monotonic() - t0) * 1000), self.model,
                        )
                    yield frame
                return
            except Exception as exc:  # noqa: BLE001 — degrade to the honest non-streaming path
                if started:
                    if lease is not None:
                        # Text already streamed: report the limit, raise as before.
                        await accounts.retry_lease(
                            lease, exc, _account_signal(exc), tried, retry_ok=False
                        )
                    raise
                if lease is not None and _account_signal(exc) is not None:
                    lease = await accounts.retry_lease(lease, exc, _account_signal(exc), tried)
                    continue
            break
        async for frame in super().stream(
            system=system, messages=messages, tools=tools
        ):
            yield frame

    async def _stream_sse(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        lease: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session = await self._session(lease)
        if lease is not None and (not session or not session.get("token") or grok_session_expired(session)):
            raise _account_not_signed_in(lease)
        if not session or not session.get("token"):
            raise RuntimeError(
                "grok-cli: no Grok session found — run `grok login` "
                "(this provider uses the CLI's on-disk session, not an API key)."
            )
        if grok_session_expired(session):
            raise RuntimeError(
                "grok-cli: the Grok session has expired — re-run `grok login`."
            )
        token = session["token"]
        version = session.get("version") or "0.2.82"
        base_url = (session.get("base_url") or self._base_url).rstrip("/")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "x-grok-client-version": str(version),
            "x-grok-client-identifier": "grok-shell",
            "x-grok-model-override": self.model,
            "User-Agent": f"grok-shell/{version} (windows; x86_64)",
            "Accept": "text/event-stream",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "input": self._to_input(system, messages),
            "max_output_tokens": self.max_tokens,
            "store": False,
            "stream": True,
        }
        if tools:
            body["tools"] = self._to_tools(tools)
            body["tool_choice"] = "auto"

        raw_lines: list[str] = []
        async with self._client().stream(
            "POST", f"{base_url}/responses", headers=headers, json=body
        ) as resp:
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                # Drain the streamed body so .json()/.text is populated, then
                # raise a typed error (caught above -> non-streaming fallback).
                try:
                    await resp.aread()
                except Exception:  # noqa: BLE001
                    pass
                detail = _error_detail(resp)
                if status == 426:
                    detail = (
                        f"{detail} (Iron Jarvis sent x-grok-client-version="
                        f"{version}; run `grok update` if the proxy rejects it)"
                    )
                raise _with_signal(
                    provider_error_from_response("grok-cli", resp, detail), resp, detail
                )

            async for line in resp.aiter_lines():
                raw_lines.append(line)
                stripped = line.strip()
                if not stripped.startswith("data:"):
                    continue
                payload = stripped[len("data:") :].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "response.output_text.delta":
                    delta = event.get("delta") or ""
                    if delta:
                        yield {"type": "text", "text": delta}
                # response.function_call_arguments.delta carries tool-arg
                # fragments; they are re-aggregated by response.completed below.

        # Build the final aggregate with the SAME parser complete() uses, so the
        # response is byte-identical (raises if the stream lacked completed).
        final = self._parse_sse("\n".join(raw_lines))
        yield {"type": "final", "response": final}


# --------------------------------------------------------------------------- #
# Iron-Proxy accounts (v1.301.0)
# --------------------------------------------------------------------------- #
#: Statuses that may mean "this ACCOUNT is limited / signed out" — handed to
#: Iron-Proxy, whose own classifier decides. Never a 5xx/529 (an overload is
#: the provider's) and never a 403 (it may be a model/plan refusal, which is
#: not one account's limit and must keep its failover).
_LIMIT_STATUSES = (401, 429)

#: The response headers handed to Iron-Proxy (its xAI reset readers) — an
#: allow-list: never Authorization, cookies or anything else.
_EXPOSED_HEADERS = ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")


def _with_signal(exc: Exception, resp: Any, detail: str) -> Exception:
    """Attach what Iron-Proxy's detector reads (status, allow-listed headers,
    the proxy's words) to an HTTP error — read only when the call ran as an
    Iron-Proxy account. Never raises."""
    try:
        status = getattr(resp, "status_code", None)
        headers: dict[str, str] = {}
        raw = getattr(resp, "headers", None)
        if raw is not None:
            for name in _EXPOSED_HEADERS:
                value = raw.get(name)
                if value:
                    headers[name] = str(value)[:200]
        exc.account_signal = {"status": status, "headers": headers, "text": (detail or "")[:4000]}  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return exc


def _account_signal(exc: BaseException) -> dict[str, Any] | None:
    """The report for a failed attempt, or None when it is not a limit/auth signal."""
    signal = getattr(exc, "account_signal", None)
    if not isinstance(signal, dict):
        return None
    status = signal.get("status")
    if "status" in signal and status not in _LIMIT_STATUSES:
        return None
    return signal


class _AccountNotSignedIn(RuntimeError):
    pass


def _account_not_signed_in(lease: Any) -> Exception:
    """An Iron-Proxy account whose home holds no live session: reported as
    'not logged in' so Iron-Proxy marks THAT account for sign-in."""
    exc = _AccountNotSignedIn(
        f'grok-cli: the Grok account "{lease.title}" in Iron-Proxy is not logged in '
        "— sign it in on the Connections page (Iron-Proxy card)."
    )
    exc.account_signal = {"text": "not logged in: no Grok session in this account's home"}  # type: ignore[attr-defined]
    return exc


def account_session(home: str) -> dict[str, Any] | None:
    """The Grok session stored in ``home`` (an Iron-Proxy account's
    ``GROK_HOME``) — the same reading :func:`cli_detect.grok_session` does for
    the default home, without touching the process environment. Never raises."""
    from .. import cli_detect

    try:
        root = Path(home)
        entry = cli_detect._grok_session_entry(cli_detect._read_json(root / "auth.json"))
        if entry is None or not str(entry.get("key") or ""):
            return None
        version = cli_detect.GROK_MIN_VERSION
        ver = cli_detect._read_json(root / "version.json")
        if isinstance(ver, dict) and ver.get("version"):
            version = str(ver["version"])
        else:
            # The installed CLI's version (the default home's), else the floor.
            version = cli_detect._grok_client_version()
        return {
            "token": str(entry["key"]),
            "base_url": GROK_PROXY_BASE,
            "expires_at": entry.get("expires_at"),
            "version": version,
            "email": entry.get("email"),
        }
    except Exception:  # noqa: BLE001
        return None


def _error_detail(resp: Any) -> str:
    """Best-effort human-readable message from an HTTP error response body."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err)[:300]
            return str(err or data)[:300]
        return str(data)[:300]
    except Exception:  # noqa: BLE001
        return (getattr(resp, "text", "") or "")[:300]
