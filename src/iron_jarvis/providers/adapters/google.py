"""Google Gemini adapter (§5 API-provider class).

Talks to the Generative Language API (``v1beta/models/{model}:generateContent``)
over raw ``httpx`` — no ``google-generativeai`` SDK dependency. The credential is
resolved lazily at call time from an explicit ``api_key`` or a ``credential()``
callable, and the async HTTP client is injectable so tests stay offline.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse, ToolCall

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GoogleAdapter(LLMAdapter):
    provider = "google"

    def __init__(
        self,
        model: str = "gemini-1.5-flash",
        *,
        api_key: str | None = None,
        credential: Callable[[], str | None] | None = None,
        http: Any = None,
        oauth: bool = False,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._credential = credential
        self._http = http
        #: True when the credential is an OAuth access token (sent as a Bearer
        #: token); False for a true ``api_key`` connection (sent as x-goog-api-key).
        self._oauth = oauth

    # -- credential / transport --------------------------------------------
    def _resolve_key(self) -> str:
        key = self._api_key or (self._credential() if self._credential else None)
        if not key:
            raise RuntimeError(
                "GoogleAdapter: no API key (set api_key= or wire a credential())"
            )
        return key

    def _client(self) -> Any:
        if self._http is None:
            import httpx  # lazy

            self._http = httpx.AsyncClient(timeout=60.0)
        return self._http

    def _url(self) -> str:
        return f"{_BASE}/{self.model}:generateContent"

    # -- request shaping ----------------------------------------------------
    @staticmethod
    def _to_contents(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.name or "",
                                    "response": {"result": m.content},
                                }
                            }
                        ],
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    parts.append(
                        {"functionCall": {"name": tc.name, "args": tc.arguments}}
                    )
                contents.append({"role": "model", "parts": parts})
            else:
                role = "model" if m.role == "assistant" else "user"
                parts = [{"text": m.content}]
                if m.role == "user" and m.images:
                    # Multimodal user turn: append an inline_data part per image
                    # alongside the text part.
                    for img in m.images:
                        parts.append(
                            {
                                "inline_data": {
                                    "mime_type": img["media_type"],
                                    "data": img["data_b64"],
                                }
                            }
                        )
                contents.append({"role": role, "parts": parts})
        return contents

    @staticmethod
    def _to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "function_declarations": [
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    }
                    for t in tools
                ]
            }
        ]

    # -- response parsing ---------------------------------------------------
    @staticmethod
    def _parse(data: dict[str, Any]) -> LLMResponse:
        candidate = (data.get("candidates") or [{}])[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts:
            if "text" in part and part["text"] is not None:
                text_parts.append(part["text"])
            fc = part.get("functionCall")
            if fc:
                name = fc.get("name", "")
                tool_calls.append(
                    ToolCall(id=name, name=name, arguments=dict(fc.get("args") or {}))
                )
        finish = "tool_use" if tool_calls else "stop"
        meta = data.get("usageMetadata") or {}
        usage_dict = {
            "input_tokens": int(meta.get("promptTokenCount", 0) or 0),
            "output_tokens": int(meta.get("candidatesTokenCount", 0) or 0),
        }
        return LLMResponse(
            text="".join(text_parts),
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
        # Resolve the credential off the event loop: an OAuth credential() may do
        # a blocking (up to 30s) httpx refresh, which must not stall the loop.
        key = await asyncio.to_thread(self._resolve_key)
        body: dict[str, Any] = {"contents": self._to_contents(messages)}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = self._to_tools(tools)
        if self._oauth:
            # An OAuth access token authorizes via the standard Bearer header;
            # sent as x-goog-api-key it is rejected (401) and we silently mock.
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
        else:
            headers = {
                "x-goog-api-key": key,
                "Content-Type": "application/json",
            }
        resp = await self._client().post(self._url(), headers=headers, json=body)
        # Fail loudly on an HTTP error so the router falls back / surfaces it,
        # rather than parsing the error body into a blank successful reply.
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            detail = ""
            try:
                err = resp.json().get("error")
                detail = str((err or {}).get("message") if isinstance(err, dict) else err)[:300]
            except Exception:
                detail = (getattr(resp, "text", "") or "")[:300]
            raise RuntimeError(f"google API error {status}: {detail}")
        return self._parse(resp.json())
