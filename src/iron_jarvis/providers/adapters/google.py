"""Google Gemini adapter (§5 API-provider class).

Talks to the Generative Language API (``v1beta/models/{model}:generateContent``)
over raw ``httpx`` — no ``google-generativeai`` SDK dependency. The credential is
resolved lazily at call time from an explicit ``api_key`` or a ``credential()``
callable, and the async HTTP client is injectable so tests stay offline.
"""

from __future__ import annotations

import asyncio
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

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

#: ``candidates[].finishReason`` values that mean the model produced NO answer
#: because the request/response was REFUSED or filtered — not a completion.
#: ``STOP`` and ``MAX_TOKENS`` are normal endings and deliberately absent.
_REFUSAL_FINISH_REASONS: frozenset[str] = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "MALFORMED_FUNCTION_CALL",
        "LANGUAGE",
        "OTHER",
    }
)


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
    def _no_content_error(
        candidates: list[dict[str, Any]] | None,
        feedback: dict[str, Any] | None,
    ) -> ProviderError | None:
        """Typed error for a 200 body that carries NO usable candidate.

        Gemini answers HTTP 200 when it REFUSES: ``promptFeedback.blockReason``
        with ``candidates`` absent, or a candidate whose ``finishReason`` is
        SAFETY/RECITATION/... and whose ``content.parts`` is empty. Parsing that
        into ``text=""``/``finish_reason="stop"`` hands back a blank reply that
        reads as SUCCESS — the router records health, chat renders an empty
        bubble, and an agent step ends as if the model chose to say nothing,
        while the reason sat in the body and was discarded. Both the complete
        and the stream path raise this instead (lock-step).

        It is PERMANENT (``transient=False``, ``status_code=200`` so the
        router's status check can never read it as retryable): a content block
        is deterministic, so retry/failover would only replay it. Returns
        ``None`` when nothing in the body explains the emptiness — the caller
        then keeps the old blank-response behaviour rather than inventing a
        reason.
        """
        fb = feedback or {}
        cands = candidates or []
        block = str(fb.get("blockReason") or "").strip()
        finish = str((cands[0] if cands else {}).get("finishReason") or "").strip()
        # A real block is deterministic; a merely empty body may be transport
        # noise, and the stream path still degrades to non-streaming for it.
        blocked = True
        if block:
            why = f"blockReason={block}"
            note = str(fb.get("blockReasonMessage") or "").strip()
            if note:
                why = f"{why}: {note[:200]}"
        elif finish.upper() in _REFUSAL_FINISH_REASONS:
            why = f"finishReason={finish}"
        elif not cands:
            why = "the response carried no candidates"
            blocked = False
        else:
            return None
        err = ProviderError(
            f"google returned no usable content ({why})",
            status_code=200,
            transient=False,
        )
        err.blocked = blocked
        return err

    @staticmethod
    def _parse(data: dict[str, Any]) -> LLMResponse:
        candidates = data.get("candidates") or []
        candidate = candidates[0] if candidates else {}
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
        text = "".join(text_parts)
        if not text and not tool_calls:
            # No usable candidate: refuse honestly instead of returning a blank
            # reply that every layer above reads as a successful answer.
            err = GoogleAdapter._no_content_error(candidates, data.get("promptFeedback"))
            if err is not None:
                raise err
        finish = "tool_use" if tool_calls else "stop"
        meta = data.get("usageMetadata") or {}
        usage_dict = {
            "input_tokens": int(meta.get("promptTokenCount", 0) or 0),
            "output_tokens": int(meta.get("candidatesTokenCount", 0) or 0),
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
        # Guided-decoding knobs (v1.203.0): accepted so callers can pass them
        # uniformly across adapters; IGNORED — not wired into the Gemini wire
        # format this wave (openai-compat family only).
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
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
            # Typed error (status + Retry-After) so the router classifies
            # transient (429/5xx) vs permanent (4xx) by status, not by string.
            raise provider_error_from_response("google", resp, detail)
        return self._parse(resp.json())

    # -- streaming (FX-01) --------------------------------------------------
    def _stream_url(self) -> str:
        # SSE variant of generateContent: incremental GenerateContentResponse
        # chunks, one per ``data:`` line, terminated by the stream closing.
        return f"{_BASE}/{self.model}:streamGenerateContent?alt=sse"

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
    ) -> AsyncIterator[dict[str, Any]]:
        """Real token stream via ``:streamGenerateContent?alt=sse`` (FX-01).

        Yields ``{"type":"text","text": <delta>}`` for each incremental
        ``candidates[].content.parts[].text`` as it arrives; ``functionCall``
        parts (which Gemini emits whole) are held, and the trailing
        ``usageMetadata`` becomes the final usage. The closing
        ``{"type":"final","response": LLMResponse}`` is assembled to equal what
        :meth:`complete` returns.

        On ANY failure BEFORE the first frame — including the injected offline
        transport having no streaming surface — we degrade to the base
        (non-streaming) stream, so a hiccup honestly falls back to a single-shot
        completion instead of fabricating output. A failure MID-stream re-raises
        honestly rather than re-running and double-emitting the answer. The ONE
        exception to the degrade is a CONTENT BLOCK (``err.blocked``): a safety
        refusal is deterministic, so re-running it non-streaming would only
        spend a second call to be refused again — it re-raises immediately.
        """
        started = False
        try:
            async for frame in self._stream_sse(
                system=system, messages=messages, tools=tools
            ):
                started = True
                yield frame
            return
        except Exception as exc:  # noqa: BLE001 — degrade to the non-streaming path
            if started or getattr(exc, "blocked", False):
                raise
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
    ) -> AsyncIterator[dict[str, Any]]:
        key = await asyncio.to_thread(self._resolve_key)
        body: dict[str, Any] = {"contents": self._to_contents(messages)}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = self._to_tools(tools)
        if self._oauth:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
        else:
            headers = {
                "x-goog-api-key": key,
                "Content-Type": "application/json",
            }

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage_dict = {"input_tokens": 0, "output_tokens": 0}
        # Kept so a stream that produced nothing can say WHY (mirrors _parse).
        last_candidates: list[dict[str, Any]] = []
        prompt_feedback: dict[str, Any] = {}

        async with self._client().stream(
            "POST", self._stream_url(), headers=headers, json=body
        ) as resp:
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                # Drain the streamed body so .json()/.text is populated, then
                # raise a typed error (caught above -> non-streaming fallback).
                try:
                    await resp.aread()
                except Exception:  # noqa: BLE001
                    pass
                detail = ""
                try:
                    err = resp.json().get("error")
                    detail = str(
                        (err or {}).get("message") if isinstance(err, dict) else err
                    )[:300]
                except Exception:  # noqa: BLE001
                    detail = (getattr(resp, "text", "") or "")[:300]
                raise provider_error_from_response("google", resp, detail)

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                candidates = chunk.get("candidates") or []
                if candidates:
                    last_candidates = candidates
                fb = chunk.get("promptFeedback")
                if isinstance(fb, dict) and fb.get("blockReason"):
                    prompt_feedback = fb
                candidate = candidates[0] if candidates else {}
                for part in ((candidate.get("content") or {}).get("parts")) or []:
                    text = part.get("text")
                    if text:
                        text_parts.append(text)
                        yield {"type": "text", "text": text}
                    fc = part.get("functionCall")
                    if fc:
                        name = fc.get("name", "")
                        tool_calls.append(
                            ToolCall(
                                id=name,
                                name=name,
                                arguments=dict(fc.get("args") or {}),
                            )
                        )
                meta = chunk.get("usageMetadata")
                if meta:  # cumulative — the last chunk carries the final totals
                    usage_dict = {
                        "input_tokens": int(meta.get("promptTokenCount", 0) or 0),
                        "output_tokens": int(meta.get("candidatesTokenCount", 0) or 0),
                    }

        text = "".join(text_parts)
        if not text and not tool_calls:
            # Same refusal as _parse: a blocked stream emits no text frames, and
            # a final frame of text="" finish_reason="stop" is a blank reply
            # presented as success.
            err = self._no_content_error(last_candidates, prompt_feedback)
            if err is not None:
                raise err

        yield {
            "type": "final",
            "response": LLMResponse(
                text=text,
                tool_calls=tool_calls,
                finish_reason="tool_use" if tool_calls else "stop",
                usage=usage_dict,
            ),
        }
