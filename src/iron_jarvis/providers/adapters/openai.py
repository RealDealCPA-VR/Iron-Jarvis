"""OpenAI adapter (§5 API-provider class).

Talks to the Chat Completions API (``/v1/chat/completions``) over raw ``httpx``
— no ``openai`` SDK dependency. The credential is resolved lazily at call time
from an explicit ``api_key`` or a ``credential()`` callable (so the Provider
Manager can hand it a closure over the Secrets Manager). The async HTTP client
is injectable so the test suite stays fully offline.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse, ToolCall

_ENDPOINT = "https://api.openai.com/v1/chat/completions"


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
        return self._parse(resp.json())
