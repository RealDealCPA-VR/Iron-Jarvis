"""Google Gemini adapter (§5 API-provider class).

Talks to the Generative Language API (``v1beta/models/{model}:generateContent``)
over raw ``httpx`` — no ``google-generativeai`` SDK dependency. The credential is
resolved lazily at call time from an explicit ``api_key`` or a ``credential()``
callable, and the async HTTP client is injectable so tests stay offline.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
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
from ..reasoning import budget_tokens

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


def _thinking_config(body: dict[str, Any], reasoning: str) -> None:
    """Gemini's spelling of the reasoning level (v1.263.0): a thinking budget
    under ``generationConfig``. ``""`` adds nothing — byte-identical body."""
    budget = budget_tokens(reasoning)
    if not budget:
        return
    cfg = body.setdefault("generationConfig", {})
    # v1.323.0: `includeThoughts` is deliberately NOT sent — the v1.263.0 pin
    # (tests/test_reasoning_level_v1263.py) holds this object byte-for-byte.
    # A part that nevertheless comes back flagged `thought: true` is routed to
    # thinking, never to the answer (`_is_thought`).
    cfg["thinkingConfig"] = {"thinkingBudget": budget}


def _is_thought(part: Any) -> bool:
    """A Gemini part that is the model's THOUGHT, not its answer (v1.323.0)."""
    return isinstance(part, dict) and part.get("thought") is True


#: The ``raw_blocks`` entry this adapter writes for a model turn that asked
#: for tools and carried a thought signature (v1.355.0): Gemini's OWN parts,
#: verbatim, plus the projection (text + calls) they were written for.
#: Gemini 3 answers 400 when a ``functionCall`` of the current turn comes back
#: without the ``thoughtSignature`` it was sent with (Vertex AI "Thought
#: signatures", read 2026-10-19: "If a required thought signature is not
#: returned when using Gemini 3 models, the model will return a 400 error";
#: the Gemini API thinking guide: "You MUST always resend all thought blocks
#: exactly as they were received"), and the rebuilt turn dropped it -- so
#: every tool loop failed at its second request. Same shape as the claude-cli
#: carrier: replayed verbatim ONLY while the turn's current text + calls still
#: equal the projection, so a signature never rides content it did not sign.
#: Other adapters never replay it (anthropic.py's ``_own_blocks``).
GEMINI_CARRIER = "gemini_native"

#: Google's documented value for a ``functionCall`` part the API did not
#: write -- "transferring history from a model that does not include thought
#: signatures" (Vertex AI "Thought signatures", read 2026-10-19; "a last
#: resort"). Put on the FIRST call of a REBUILT model turn (parallel calls:
#: "only the first functionCall part will contain the thought_signature")
#: for a Gemini 3+ model only: another provider's turn after a failover, a
#: resumed run, an edited turn.
SKIP_SIGNATURE = "skip_thought_signature_validator"

_GEMINI_MAJOR = re.compile(r"gemini-(\d+)")


def _signs_function_calls(model: str) -> bool:
    """True for a Gemini 3+ model id (the family that validates signatures)."""
    m = _GEMINI_MAJOR.search((model or "").lower())
    return bool(m) and int(m.group(1)) >= 3


def _signature(part: Any) -> str | None:
    """The thought signature a part carries (REST spells it
    ``thoughtSignature``; one Google example writes ``thought_signature``)."""
    if not isinstance(part, dict):
        return None
    sig = part.get("thoughtSignature") or part.get("thought_signature")
    return sig if isinstance(sig, str) and sig else None


def _projection(text: str, calls: list[ToolCall]) -> str:
    """What a model turn says, canonical: its text and its calls in order."""
    return json.dumps(
        {
            "content": (text or "").strip(),
            "tool_calls": [{"name": c.name, "args": c.arguments} for c in calls],
        },
        sort_keys=True,
        default=str,
    )


def _carrier(parts: list[dict[str, Any]], text: str, calls: list[ToolCall]) -> list[dict[str, Any]]:
    """The ``raw_blocks`` for a response: ONE carrier when it asked for tools
    and a part carries a signature, else ``[]`` (nothing to keep)."""
    if not calls or not any(_signature(p) for p in parts):
        return []
    return [
        {
            "type": GEMINI_CARRIER,
            "version": 1,
            "parts": copy.deepcopy(parts),
            "projection": _projection(text, calls),
        }
    ]


def _replayable(message: LLMMessage) -> list[dict[str, Any]] | None:
    """The native parts to replay for a model turn, or None to rebuild:
    exactly one carrier, version 1, whose projection still equals the turn."""
    found = [
        b for b in (getattr(message, "raw_blocks", None) or [])
        if isinstance(b, dict) and b.get("type") == GEMINI_CARRIER
    ]
    if len(found) != 1:
        return None
    entry = found[0]
    parts = entry.get("parts")
    if entry.get("version") != 1 or not isinstance(parts, list) or not parts:
        return None
    if not all(isinstance(p, dict) for p in parts):
        return None
    if entry.get("projection") != _projection(message.content, message.tool_calls):
        return None
    return copy.deepcopy(parts)


def _fold_stream_part(raw: list[dict[str, Any]], part: dict[str, Any]) -> None:
    """Add one STREAMED part to the turn's native parts: a ``functionCall``
    part is kept whole (signature and all); text deltas join the text part
    they continue (a thought and the answer kept apart), and a signature that
    arrives on any of them -- or alone on an empty part -- stays on it."""
    if part.get("functionCall"):
        raw.append(copy.deepcopy(part))
        return
    text = part.get("text")
    text = text if isinstance(text, str) else ""
    sig = _signature(part)
    if not text and not sig:
        return
    thought = _is_thought(part)
    last = raw[-1] if raw else None
    if last is None or "functionCall" in last or bool(last.get("thought")) != thought:
        last = {"text": ""}
        if thought:
            last["thought"] = True
        raw.append(last)
    last["text"] = str(last.get("text") or "") + text
    if sig:
        last["thoughtSignature"] = sig


def _finish(finish_reason: Any, has_calls: bool) -> str:
    """The provider-neutral finish reason (v1.323.0): a function call is
    ``tool_use`` (MAX_TOKENS included — a call is a complete answer), a
    ``MAX_TOKENS`` stop with no call is ``max_tokens``, everything else
    ``stop``."""
    if has_calls:
        return "tool_use"
    if str(finish_reason or "").upper() == "MAX_TOKENS":
        return "max_tokens"
    return "stop"


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
    def _to_contents(messages: list[LLMMessage], model: str = "") -> list[dict[str, Any]]:
        """The ``contents`` of a request. ``model`` (v1.355.0) decides whether a
        REBUILT tool-calling turn needs Google's skip placeholder (Gemini 3+)."""
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
                # v1.355.0: the model's own parts, signatures included, when
                # the turn is still what it wrote (see GEMINI_CARRIER).
                native = _replayable(m)
                if native is not None:
                    contents.append({"role": "model", "parts": native})
                    continue
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                for i, tc in enumerate(m.tool_calls):
                    call: dict[str, Any] = {
                        "functionCall": {"name": tc.name, "args": tc.arguments}
                    }
                    if i == 0 and _signs_function_calls(model):
                        call["thoughtSignature"] = SKIP_SIGNATURE
                    parts.append(call)
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
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts:
            if _is_thought(part):
                # v1.323.0: a thought is display-only — never the answer.
                if isinstance(part.get("text"), str) and part["text"]:
                    thinking_parts.append(part["text"])
                continue
            if "text" in part and part["text"] is not None:
                text_parts.append(part["text"])
            fc = part.get("functionCall")
            if fc:
                name = fc.get("name", "")
                tool_calls.append(
                    ToolCall(id=name, name=name, arguments=dict(fc.get("args") or {}))
                )
        text = "".join(text_parts)
        raw_parts = [p for p in parts if isinstance(p, dict)]
        if not text and not tool_calls:
            # No usable candidate: refuse honestly instead of returning a blank
            # reply that every layer above reads as a successful answer.
            err = GoogleAdapter._no_content_error(candidates, data.get("promptFeedback"))
            if err is not None:
                raise err
        finish = _finish(candidate.get("finishReason"), bool(tool_calls))
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
            thinking="".join(thinking_parts),
            # v1.355.0: the parts verbatim, so the next round of a tool loop
            # sends each thought signature back (both chat lanes and the
            # agent runtime copy raw_blocks onto the assistant turn).
            raw_blocks=_carrier(raw_parts, text, tool_calls),
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
        reasoning: str = "",
    ) -> LLMResponse:
        # Resolve the credential off the event loop: an OAuth credential() may do
        # a blocking (up to 30s) httpx refresh, which must not stall the loop.
        key = await asyncio.to_thread(self._resolve_key)
        body: dict[str, Any] = {"contents": self._to_contents(messages, self.model)}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = self._to_tools(tools)
        _thinking_config(body, reasoning)
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
        reasoning: str = "",
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
                system=system, messages=messages, tools=tools, reasoning=reasoning
            ):
                started = True
                yield frame
            return
        except Exception as exc:  # noqa: BLE001 — degrade to the non-streaming path
            if started or getattr(exc, "blocked", False):
                raise
        async for frame in super().stream(
            system=system, messages=messages, tools=tools,
            **({"reasoning": reasoning} if reasoning else {}),
        ):
            yield frame

    async def _stream_sse(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        reasoning: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        key = await asyncio.to_thread(self._resolve_key)
        body: dict[str, Any] = {"contents": self._to_contents(messages, self.model)}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = self._to_tools(tools)
        _thinking_config(body, reasoning)
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
        thinking_parts: list[str] = []
        #: v1.355.0: the turn's native parts as they stream (see _fold_stream_part).
        raw_parts: list[dict[str, Any]] = []
        last_finish: Any = None
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
                if candidate.get("finishReason"):
                    last_finish = candidate.get("finishReason")
                for part in ((candidate.get("content") or {}).get("parts")) or []:
                    if isinstance(part, dict):
                        _fold_stream_part(raw_parts, part)
                    text = part.get("text")
                    if _is_thought(part):
                        # v1.323.0: a thought streams as `thinking` — never
                        # answer text, never in the final reply.
                        if isinstance(text, str) and text:
                            thinking_parts.append(text)
                            yield {"type": "thinking", "text": text}
                        continue
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
                finish_reason=_finish(last_finish, bool(tool_calls)),
                usage=usage_dict,
                thinking="".join(thinking_parts),
                raw_blocks=_carrier(raw_parts, text, tool_calls),
            ),
        }
