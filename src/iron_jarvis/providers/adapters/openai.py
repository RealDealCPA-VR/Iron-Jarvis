"""OpenAI adapter (§5 API-provider class).

Talks to the Chat Completions API (``/v1/chat/completions``) over raw ``httpx``
— no ``openai`` SDK dependency. The credential is resolved lazily at call time
from an explicit ``api_key`` or a ``credential()`` callable (so the Provider
Manager can hand it a closure over the Secrets Manager). The async HTTP client
is injectable so the test suite stays fully offline.

CHATGPT-BACKEND MODE: a ChatGPT-account OAuth token (a JWT, not an ``sk-`` key)
is NOT accepted by api.openai.com. When the resolved credential is such a
token, requests route to the Codex backend instead —
``https://chatgpt.com/backend-api/codex/responses`` (Responses API shape, SSE
stream, ``chatgpt-account-id`` header from the token's JWT claim) — so a
subscription-only account (no API organization) still runs real inference,
billed to the ChatGPT plan. Only codex-capable models are served there;
incompatible models are mapped to a codex default.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any, Callable

from .base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ToolCall,
    provider_error_from_response,
)

_ENDPOINT = "https://api.openai.com/v1/chat/completions"

#: The Codex backend serving ChatGPT-subscription inference (Responses API).
_CHATGPT_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"

#: Models the Codex backend serves; anything else maps to the default below.
_CHATGPT_MODEL_PREFIXES = ("gpt-5", "codex")
#: OpenAI PROGRESSIVELY RETIRES model ids from the ChatGPT-account backend
#: (gpt-5-codex, gpt-5.1*, codex-mini-latest… all now 400 "not supported").
#: Verified live 2026-07: "gpt-5.5" is served. On a "model is not supported"
#: 400 we ladder through the candidates below and CACHE the winner, so the next
#: retirement degrades gracefully instead of bricking the provider.
_CHATGPT_DEFAULT_MODEL = "gpt-5.5"
_CHATGPT_FALLBACK_MODELS = ("gpt-5.5", "gpt-5.5-codex", "gpt-5.6", "gpt-5.4")
#: Cross-instance cache: model ids the backend has REJECTED this process-life,
#: and the last id that actually worked (adapters are rebuilt per request).
_CHATGPT_REJECTED: set[str] = set()
_CHATGPT_KNOWN_GOOD: list[str] = []


def _is_chatgpt_token(credential: str) -> bool:
    """True when the credential is a ChatGPT OAuth JWT (not an sk- API key)."""
    return not credential.startswith("sk-") and credential.count(".") == 2


def _jwt_claims(token: str) -> dict:
    """Decode a JWT payload WITHOUT verification (transport-trusted, local)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a malformed token just yields no claims
        return {}


def _chatgpt_account_id(token: str) -> str:
    """The ``chatgpt_account_id`` claim the Codex backend requires as a header."""
    claim = _jwt_claims(token).get("https://api.openai.com/auth")
    if isinstance(claim, dict):
        return str(claim.get("chatgpt_account_id") or "")
    return ""


def _error_detail(resp: Any) -> str:
    """Best-effort human-readable message from an HTTP error response body."""
    try:
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(err or data)[:300]
    except Exception:
        return (getattr(resp, "text", "") or "")[:300]


class OpenAIAdapter(LLMAdapter):
    provider = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        api_key: str | None = None,
        credential: Callable[[], str | None] | None = None,
        http: Any = None,
        max_tokens: int = 4096,
        base_url: str | None = None,
        provider_name: str | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._credential = credential
        self._http = http
        self.max_tokens = max_tokens
        #: Chat-completions endpoint — defaults to OpenAI's hosted API, but can be
        #: pointed at any OpenAI-compatible server (e.g. a local Ollama instance).
        self._endpoint = base_url or _ENDPOINT
        if provider_name:
            self.provider = provider_name

    # -- credential / transport --------------------------------------------
    def _resolve_key(self) -> str | None:
        """Resolve the API key, or None when none is configured.

        No longer raises: a custom ``base_url`` (e.g. a local Ollama server)
        needs no key. The hosted-OpenAI no-key case is enforced in ``complete``.
        """
        return self._api_key or (self._credential() if self._credential else None)

    def _client(self) -> Any:
        if self._http is None:
            import httpx  # lazy: keep import cost off the offline path

            self._http = httpx.AsyncClient(timeout=60.0)
        return self._http

    # -- request shaping ----------------------------------------------------
    @staticmethod
    def _to_openai_messages(
        system: str, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
            elif m.role == "user" and m.images:
                # Multimodal user turn: a text part followed by one image_url
                # part per attached image (base64 data: URL).
                content: list[dict[str, Any]] = [
                    {"type": "text", "text": m.content}
                ]
                for img in m.images:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{img['media_type']};base64,"
                                    f"{img['data_b64']}"
                                )
                            },
                        }
                    )
                out.append({"role": "user", "content": content})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            }
            for t in tools
        ]

    # -- response parsing ---------------------------------------------------
    @staticmethod
    def _parse(data: dict[str, Any]) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args_str = fn.get("arguments") or ""
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                ToolCall(id=raw.get("id", ""), name=fn.get("name", ""), arguments=args)
            )
        finish = "tool_use" if choice.get("finish_reason") == "tool_calls" else "stop"
        usage = data.get("usage") or {}
        usage_dict = {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        }
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage_dict,
        )

    # -- ChatGPT (Codex) backend shaping -------------------------------------

    @staticmethod
    def _to_responses_input(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Map the message history to Responses-API input items.

        Tool results become ``function_call_output`` items; an assistant turn
        that called tools is replayed as its ``function_call`` items (the
        backend is stateless with ``store: false`` — the full exchange must be
        re-sent each step).
        """
        items: list[dict[str, Any]] = []
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
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": m.content}],
                        }
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
            elif m.role == "assistant":
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": m.content}],
                    }
                )
            else:  # user (optionally multimodal)
                content: list[dict[str, Any]] = [
                    {"type": "input_text", "text": m.content}
                ]
                for img in m.images or []:
                    content.append(
                        {
                            "type": "input_image",
                            "image_url": (
                                f"data:{img['media_type']};base64,{img['data_b64']}"
                            ),
                        }
                    )
                items.append({"type": "message", "role": "user", "content": content})
        return items

    @staticmethod
    def _to_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    @staticmethod
    def _parse_sse(raw: str) -> LLMResponse:
        """Extract the final response from a Codex-backend SSE stream.

        The stream ends with a ``response.completed`` event whose ``response``
        object carries the full output array + usage — collecting that single
        event is equivalent to accumulating every delta.
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
                "openai (ChatGPT backend): stream ended without response.completed: "
                + raw[:300]
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

    async def _complete_chatgpt(
        self,
        *,
        token: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        account_id = _chatgpt_account_id(token)
        if not account_id:
            raise RuntimeError(
                "openai (ChatGPT backend): the OAuth token carries no "
                "chatgpt_account_id claim — reconnect on the Connections page, "
                "or use an API key."
            )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "Accept": "text/event-stream",
        }
        # Only codex-capable models are served by this backend — and OpenAI
        # retires ids over time, so build a LADDER: the requested model (if
        # plausibly served + not already known-rejected), the last known-good,
        # then the fallback candidates. First non-rejected 200 wins.
        ladder: list[str] = []
        if self.model.startswith(_CHATGPT_MODEL_PREFIXES):
            ladder.append(self.model)
        ladder.extend(_CHATGPT_KNOWN_GOOD)
        ladder.extend(_CHATGPT_FALLBACK_MODELS)
        seen: set[str] = set()
        candidates = [
            m for m in ladder
            if not (m in seen or seen.add(m)) and m not in _CHATGPT_REJECTED
        ] or [_CHATGPT_DEFAULT_MODEL]

        resp: Any = None
        status = 200
        last_err = ""
        for model in candidates:
            body: dict[str, Any] = {
                "model": model,
                "instructions": system or "",
                "input": self._to_responses_input(messages),
                "tools": self._to_responses_tools(tools),
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "store": False,  # required: the backend keeps no server-side state
                "stream": True,  # the endpoint is SSE-only
                "include": ["reasoning.encrypted_content"],
            }
            resp = await self._client().post(
                _CHATGPT_ENDPOINT, headers=headers, json=body
            )
            status = getattr(resp, "status_code", 200)
            detail = _error_detail(resp).lower() if status == 400 else ""
            if status == 400 and "instruction" in detail:
                # Some backend revisions validate the instructions field against
                # the official Codex prompt. Self-heal: retry once with empty
                # instructions and the system prompt as a developer message.
                body["instructions"] = ""
                body["input"] = [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": system}],
                    },
                    *self._to_responses_input(messages),
                ]
                resp = await self._client().post(
                    _CHATGPT_ENDPOINT, headers=headers, json=body
                )
                status = getattr(resp, "status_code", 200)
                detail = _error_detail(resp).lower() if status == 400 else ""
            if status == 400 and "model is not supported" in detail:
                # This id has been retired for ChatGPT accounts — remember and
                # try the next rung.
                _CHATGPT_REJECTED.add(model)
                last_err = _error_detail(resp)
                continue
            break  # success, or a non-model error worth surfacing as-is
        if status >= 400:
            # Typed error so the router classifies transient (429/5xx) vs
            # permanent (400/401) by STATUS, and honours any Retry-After.
            detail = _error_detail(resp) if resp is not None else last_err
            if resp is not None:
                raise provider_error_from_response("openai (ChatGPT backend)", resp, detail)
            raise ProviderError(
                f"openai (ChatGPT backend) API error {status}: {detail}",
                status_code=status,
            )
        if model not in _CHATGPT_KNOWN_GOOD:
            _CHATGPT_KNOWN_GOOD.insert(0, model)  # remember what works
        return self._parse_sse(getattr(resp, "text", "") or "")

    # -- the interface ------------------------------------------------------
    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        # Resolve the credential off the loop — for an OAuth provider this may
        # trigger a blocking token refresh that must not stall the event loop.
        key = await asyncio.to_thread(self._resolve_key)
        # A ChatGPT-account OAuth token can't call api.openai.com — route it to
        # the Codex backend (subscription-billed inference). Only for the real
        # OpenAI provider on the hosted endpoint (never Ollama/xAI base_urls).
        if (
            key
            and self.provider == "openai"
            and self._endpoint == _ENDPOINT
            and _is_chatgpt_token(key)
        ):
            return await self._complete_chatgpt(
                token=key, system=system, messages=messages, tools=tools
            )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif self._endpoint == _ENDPOINT:
            # The hosted OpenAI endpoint requires a key; a custom base_url
            # (e.g. a local Ollama server) authenticates without one, so we
            # only fail closed when targeting OpenAI itself.
            raise RuntimeError(
                "OpenAIAdapter: no API key (set api_key= or wire a credential())"
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_openai_messages(system, messages),
        }
        if tools:
            body["tools"] = self._to_openai_tools(tools)
        resp = await self._client().post(
            self._endpoint,
            headers=headers,
            json=body,
        )
        # Fail LOUDLY on an HTTP error instead of parsing it into a blank success:
        # a wrong key / bad model / rate-limit / expired token must raise so the
        # router emits provider.failed + falls back (never a silent empty reply).
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            # Typed error (status + Retry-After) so the router fails over on a
            # transient 429/5xx and raises honestly on a permanent 4xx — never a
            # blank successful reply. Transport/timeout errors propagate as their
            # native httpx types, which the router also classifies as transient.
            raise provider_error_from_response(self.provider, resp, _error_detail(resp))
        return self._parse(resp.json())

    # -- token streaming (FX-01) --------------------------------------------
    async def stream(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Real token streaming for every backend this adapter serves.

        Chat Completions (hosted OpenAI, Ollama, xAI, OpenRouter, any
        OpenAI-compatible ``base_url``) streams via ``client.stream`` with
        ``stream: true`` + ``stream_options.include_usage``; a ChatGPT-account
        OAuth token routes to the Codex Responses backend (:meth:`_stream_chatgpt`,
        keeping the model ladder). Yields ``{"type":"text", ...}`` deltas then one
        ``{"type":"final","response": LLMResponse}`` byte-identical to what
        :meth:`complete` returns for the same call. A real failure raises a typed
        error (as complete() does) — a stream NEVER fabricates output.
        """
        # Resolve off the loop — an OAuth provider may block on a token refresh.
        key = await asyncio.to_thread(self._resolve_key)
        # A ChatGPT-account OAuth token can't call api.openai.com — route it to
        # the Codex backend, exactly as complete() does (real OpenAI provider on
        # the hosted endpoint only, never an Ollama/xAI base_url).
        if (
            key
            and self.provider == "openai"
            and self._endpoint == _ENDPOINT
            and _is_chatgpt_token(key)
        ):
            async for frame in self._stream_chatgpt(
                token=key, system=system, messages=messages, tools=tools
            ):
                yield frame
            return
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif self._endpoint == _ENDPOINT:
            raise RuntimeError(
                "OpenAIAdapter: no API key (set api_key= or wire a credential())"
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_openai_messages(system, messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = self._to_openai_tools(tools)
        async with self._client().stream(
            "POST", self._endpoint, headers=headers, json=body
        ) as resp:
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                # A streamed response body isn't auto-read: load it, then raise a
                # typed error so the router fails over on a transient 429/5xx and
                # raises honestly on a permanent 4xx — never a blank reply.
                await resp.aread()
                raise provider_error_from_response(
                    self.provider, resp, _error_detail(resp)
                )
            async for frame in self._consume_chat_stream(resp):
                yield frame

    async def _consume_chat_stream(
        self, resp: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield text deltas from a chat/completions SSE response, then the final.

        Accumulates ``choices[0].delta.content`` (text) and
        ``choices[0].delta.tool_calls[]`` (by index) plus the trailing
        ``include_usage`` chunk, then rebuilds the non-streaming message shape and
        reuses :meth:`_parse` so the final ``LLMResponse`` is byte-identical to the
        non-streaming path.
        """
        text_parts: list[str] = []
        tool_accum: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        async for line in resp.aiter_lines():
            s = line.strip()
            if not s.startswith("data:"):
                continue
            payload = s[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            u = chunk.get("usage")
            if u:
                usage = u  # the include_usage chunk (choices == [])
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                text_parts.append(content)
                yield {"type": "text", "text": content}
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_accum.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
            if choices[0].get("finish_reason"):
                finish_reason = choices[0]["finish_reason"]
        # Reconstruct the aggregate message and route it through _parse so text,
        # tool calls, usage, and the finish-reason mapping match complete().
        message: dict[str, Any] = {"content": "".join(text_parts)}
        if tool_accum:
            message["tool_calls"] = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["args"]},
                }
                for _, slot in sorted(tool_accum.items())
            ]
        if finish_reason is None:
            finish_reason = "tool_calls" if tool_accum else "stop"
        data = {
            "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": usage,
        }
        yield {"type": "final", "response": self._parse(data)}

    async def _stream_chatgpt(
        self,
        *,
        token: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Token-stream the Codex (ChatGPT-subscription) backend.

        Uses the SAME model ladder + instructions self-heal as
        :meth:`_complete_chatgpt`, but streaming begins only AFTER a ladder rung
        returns 200. Text deltas come from ``response.output_text.delta`` events;
        the final ``LLMResponse`` is rebuilt from the trailing
        ``response.completed`` via :meth:`_parse_sse`, so it is identical to
        the non-streaming path.
        """
        account_id = _chatgpt_account_id(token)
        if not account_id:
            raise RuntimeError(
                "openai (ChatGPT backend): the OAuth token carries no "
                "chatgpt_account_id claim — reconnect on the Connections page, "
                "or use an API key."
            )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "Accept": "text/event-stream",
        }
        ladder: list[str] = []
        if self.model.startswith(_CHATGPT_MODEL_PREFIXES):
            ladder.append(self.model)
        ladder.extend(_CHATGPT_KNOWN_GOOD)
        ladder.extend(_CHATGPT_FALLBACK_MODELS)
        seen: set[str] = set()
        candidates = [
            m for m in ladder
            if not (m in seen or seen.add(m)) and m not in _CHATGPT_REJECTED
        ] or [_CHATGPT_DEFAULT_MODEL]

        base_input = self._to_responses_input(messages)
        tools_shaped = self._to_responses_tools(tools)
        last_err = ""
        for model in candidates:
            instructions: str = system or ""
            input_items: list[dict[str, Any]] = base_input
            healed = False
            while True:
                body: dict[str, Any] = {
                    "model": model,
                    "instructions": instructions,
                    "input": input_items,
                    "tools": tools_shaped,
                    "tool_choice": "auto",
                    "parallel_tool_calls": False,
                    "store": False,  # the backend keeps no server-side state
                    "stream": True,  # the endpoint is SSE-only
                    "include": ["reasoning.encrypted_content"],
                }
                async with self._client().stream(
                    "POST", _CHATGPT_ENDPOINT, headers=headers, json=body
                ) as resp:
                    status = getattr(resp, "status_code", 200)
                    if status >= 400:
                        await resp.aread()  # streamed error body isn't auto-read
                        detail = _error_detail(resp)
                        detail_l = detail.lower()
                        if (
                            status == 400
                            and "instruction" in detail_l
                            and not healed
                        ):
                            # The backend rejected the custom instructions field —
                            # retry this SAME rung once with the system prompt
                            # demoted to a developer message (mirrors complete()).
                            healed = True
                            instructions = ""
                            input_items = [
                                {
                                    "type": "message",
                                    "role": "developer",
                                    "content": [
                                        {"type": "input_text", "text": system}
                                    ],
                                },
                                *base_input,
                            ]
                            continue
                        if status == 400 and "model is not supported" in detail_l:
                            _CHATGPT_REJECTED.add(model)
                            last_err = detail
                            break  # advance to the next ladder rung
                        # Any other error surfaces honestly (typed for the router).
                        raise provider_error_from_response(
                            "openai (ChatGPT backend)", resp, detail
                        )
                    # 200 — this rung works; remember it, then stream it.
                    if model not in _CHATGPT_KNOWN_GOOD:
                        _CHATGPT_KNOWN_GOOD.insert(0, model)
                    async for frame in self._consume_responses_stream(resp):
                        yield frame
                    return
        # Every candidate was retired — raise a permanent (400) error honestly.
        raise ProviderError(
            "openai (ChatGPT backend) API error 400: "
            + (last_err or "all candidate models were rejected"),
            status_code=400,
        )

    async def _consume_responses_stream(
        self, resp: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield text deltas from a Codex Responses SSE stream, then the final.

        ``response.output_text.delta`` events stream incremental text; the whole
        raw stream is then re-parsed by :meth:`_parse_sse` (exactly what
        complete() does with the full body) so the final ``LLMResponse`` — text,
        tool calls, usage — is identical, and a stream that ends without a
        ``response.completed`` event raises honestly rather than fabricating.
        """
        raw_lines: list[str] = []
        async for line in resp.aiter_lines():
            raw_lines.append(line)
            s = line.strip()
            if not s.startswith("data:"):
                continue
            payload = s[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "response.output_text.delta":
                delta = event.get("delta")
                if delta:
                    yield {"type": "text", "text": delta}
        final = self._parse_sse("\n".join(raw_lines))
        yield {"type": "final", "response": final}
