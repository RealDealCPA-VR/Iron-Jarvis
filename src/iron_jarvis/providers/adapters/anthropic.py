"""Anthropic adapter (§5 API-provider class).

Default model ``claude-opus-4-8``. Not exercised by the offline test suite; it
runs only when ANTHROPIC_API_KEY is set. When extending this, consult the
`claude-api` skill for current model ids, params, and tool-use shapes.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .base import LLMAdapter, LLMMessage, LLMResponse, ToolCall


class AnthropicAdapter(LLMAdapter):
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-4-8",
        max_tokens: int = 4096,
        *,
        api_key: str | None = None,
        credential=None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key
        self._credential = credential  # Callable[[], str | None] | None

    def _key(self) -> str | None:
        if self._api_key:
            return self._api_key
        if self._credential is not None:
            key = self._credential()
            if key:
                return key
        return os.environ.get("ANTHROPIC_API_KEY")

    def _client(self):
        key = self._key()
        if not key:
            raise RuntimeError(
                "No Anthropic credential — connect it on the Connections page."
            )
        from anthropic import AsyncAnthropic  # lazy import

        # An OAuth ACCOUNT token (Claude Pro/Max via the Claude Code login flow)
        # is an `sk-ant-oat...` Bearer token that calls the Messages API with the
        # oauth beta header — never as an x-api-key. A normal API key
        # (`sk-ant-api...`) uses x-api-key as before.
        # A 60s request timeout (matching the OpenAI/Google adapters) so a slow or
        # half-open connection trips the router's PROVIDER_FAILED fallback promptly
        # instead of hanging a session on the SDK's ~600s default.
        if key.startswith("sk-ant-oat"):
            return AsyncAnthropic(
                auth_token=key,
                default_headers={"anthropic-beta": "oauth-2025-04-20"},
                timeout=60.0,
            )
        return AsyncAnthropic(api_key=key, timeout=60.0)

    @staticmethod
    def _to_anthropic_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
            elif m.role == "user" and m.images:
                # Multimodal user turn: a text block followed by one image block
                # per attached image (base64 source).
                img_blocks: list[dict[str, Any]] = [
                    {"type": "text", "text": m.content}
                ]
                for img in m.images:
                    img_blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img["media_type"],
                                "data": img["data_b64"],
                            },
                        }
                    )
                out.append({"role": "user", "content": img_blocks})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        # Build the client off the loop — credential resolution may trigger a
        # blocking OAuth token refresh that must not stall the event loop.
        client = await asyncio.to_thread(self._client)
        tool_defs: list[dict[str, Any]] = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools
        ]
        # Prompt caching: mark the STABLE prefix (tool schemas + system prompt) with a
        # cache breakpoint so Anthropic bills it at the ~10% cache-read rate on every
        # step after the first, instead of re-billing the full ~5k-token prefix each
        # turn of a multi-step agent loop. Cache_control on a too-small prefix is a
        # silent no-op, so this is always safe.
        system_param: Any = system or ""
        if system:
            system_param = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if tool_defs:
            tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}
        anthropic_messages = self._to_anthropic_messages(messages)
        # Message-level cache breakpoint: mark the LAST content block so the whole
        # growing conversation prefix (system + tools + all prior turns) bills at the
        # ~10% cache-read rate on the next step instead of re-billing in full. The
        # system/tools breakpoints above only cover the FIXED prefix; this is what
        # stops a multi-step loop re-paying for the entire history every step. Only
        # for a real conversation (2+ messages) — a lone first message has no prior
        # prefix to reuse, and adding it there would just alter the request shape.
        # Fresh dicts each call, so this never accumulates across requests.
        if len(anthropic_messages) > 1:
            last = anthropic_messages[-1]
            content = last.get("content")
            if isinstance(content, str):
                last["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(content, list) and content:
                content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
        resp = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_param,
            messages=anthropic_messages,
            tools=tool_defs,
        )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        finish = "tool_use" if resp.stop_reason == "tool_use" else "stop"
        usage = getattr(resp, "usage", None)
        usage_dict = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage_dict,
        )
