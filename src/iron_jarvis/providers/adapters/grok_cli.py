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
"""

from __future__ import annotations

import asyncio
import json
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
    ) -> LLMResponse:
        # Resolve the session off the loop (a small file read, but keep the
        # contract identical to token-refreshing adapters).
        session = await asyncio.to_thread(self._session_provider)
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
            raise provider_error_from_response("grok-cli", resp, detail)
        return self._parse_sse(getattr(resp, "text", "") or "")


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
