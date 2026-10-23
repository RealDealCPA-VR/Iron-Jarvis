"""Anthropic adapter (§5 API-provider class).

Default model ``claude-opus-4-8``. Not exercised by the offline test suite; it
runs only when ANTHROPIC_API_KEY is set. When extending this, consult the
`claude-api` skill for current model ids, params, and tool-use shapes.
"""

from __future__ import annotations

import asyncio
import os
import threading
import weakref
from collections.abc import AsyncIterator
from typing import Any

from .base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ProviderRefusal,
    ToolCall,
    parse_retry_after,
)
from ..reasoning import ADAPTIVE, anthropic_thinking_mode, budget_tokens, normalize_level


def _anthropic_provider_error(exc: Exception) -> ProviderError:
    """Convert an Anthropic SDK exception into a typed :class:`ProviderError`.

    The SDK raises ``APIStatusError`` (carrying ``.status_code`` + ``.response``)
    for HTTP errors and ``APITimeoutError`` / ``APIConnectionError`` for
    transport failures. We read the status + ``Retry-After`` where present and
    mark timeouts/connection drops transient by TYPE — so the router fails over
    on a 429/overload/timeout and raises honestly on a 400/401. Kept attribute-
    driven (no hard SDK import) since this path only runs with a real key.
    """
    if isinstance(exc, ProviderError):
        return exc
    status = getattr(exc, "status_code", None)
    retry_after = None
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None:
        try:
            retry_after = parse_retry_after(headers.get("retry-after"))
        except Exception:  # noqa: BLE001
            retry_after = None
    transient: bool | None = None
    if status is None:
        # No HTTP status → a transport-layer failure. Anthropic names these
        # APITimeoutError / APIConnectionError; both are transient.
        name = type(exc).__name__
        if "Timeout" in name or "Connection" in name:
            transient = True
    return ProviderError(
        str(exc) or f"anthropic error: {type(exc).__name__}",
        status_code=status,
        retry_after=retry_after,
        transient=transient,
    )


def _raw_blocks(content: Any) -> list[dict[str, Any]]:
    """The response's content blocks as plain dicts, for verbatim replay
    (v1.263.0). ``exclude_none`` because the SDK models carry every optional
    field as ``None`` and the API refuses some of them echoed back."""
    out: list[dict[str, Any]] = []
    for block in content or []:
        if hasattr(block, "model_dump"):
            out.append(block.model_dump(exclude_none=True))
        elif isinstance(block, dict):
            out.append({k: v for k, v in block.items() if v is not None})
    return out


def _finish(stop_reason: Any) -> str:
    """The provider-neutral finish reason (v1.323.0): ``tool_use``, ``max_tokens``
    when Anthropic stopped the answer for running out of output tokens (or of
    context window), else ``stop``. Shared by complete() and stream()."""
    if stop_reason == "tool_use":
        return "tool_use"
    if stop_reason in ("max_tokens", "model_context_window_exceeded"):
        return "max_tokens"
    return "stop"


def _refusal(message: Any, *, provider: str, emitted: bool = False) -> ProviderRefusal | None:
    """The typed refusal for a Messages API answer whose ``stop_reason`` is
    ``"refusal"`` (v1.378.0), else None. ``stop_details`` (``{type, category,
    explanation}``, ``category`` may be null) is read as the SDK hands it —
    an object or a dict. ``partial`` when text had already streamed or the
    declined message carries some. Shared by complete() and stream()."""
    if getattr(message, "stop_reason", None) != "refusal":
        return None
    details = getattr(message, "stop_details", None)

    def field(name: str) -> str:
        value = details.get(name) if isinstance(details, dict) else getattr(details, name, None)
        return value if isinstance(value, str) else ""

    category, explanation = field("category"), field("explanation")
    text = "".join(
        str(getattr(b, "text", "") or "")
        for b in (getattr(message, "content", None) or [])
        if getattr(b, "type", None) == "text"
    )
    said = f"{provider}: Anthropic's safety check declined to answer (stop_reason refusal"
    said += f", category {category})" if category else ")"
    if explanation:
        said += f": {explanation[:300]}"
    return ProviderRefusal(
        said,
        provider=provider,
        category=category,
        explanation=explanation,
        partial=bool(emitted or text.strip()),
        whole_conversation=True,
    )


def _thinking_text(content: Any) -> str:
    """The ``thinking`` blocks' text, joined (v1.323.0) — display-only. A
    ``redacted_thinking`` block carries no readable text and adds nothing."""
    parts: list[str] = []
    for block in content or []:
        if getattr(block, "type", None) == "thinking":
            text = getattr(block, "thinking", "")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n\n".join(parts)


#: The ``raw_blocks`` entry the claude-cli adapter writes (v1.300.0,
#: ``claude_native.CARRIER``). Spelled here, not imported, so this adapter
#: never loads the CLI transport.
_CLI_CARRIER = "claude_cli_native"
#: v1.355.0: the Gemini adapter's carrier (``google.GEMINI_CARRIER``), spelled
#: here for the same reason. Gemini parts are not Anthropic content blocks.
_FOREIGN_CARRIERS = frozenset({_CLI_CARRIER, "gemini_native"})


def _own_blocks(m: LLMMessage) -> list[dict[str, Any]]:
    """The raw blocks THIS adapter may replay: everything except the
    claude-cli carrier (v1.300.0). A conversation that moves from claude-cli
    to the API must never replay ``mcp__ij__``-named native blocks or their
    signatures here; with only a carrier, the turn is rebuilt from text +
    tool_calls like any other provider's."""
    return [
        b for b in (getattr(m, "raw_blocks", None) or [])
        if not (isinstance(b, dict) and b.get("type") in _FOREIGN_CARRIERS)
    ]


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
        #: v1.332.0: one SDK client per event loop, reused while the key is
        #: the same — id(loop) -> (weakref to the loop, key, client). See
        #: :meth:`_client_for_loop`.
        self._loop_clients: dict[int, tuple[Any, str, Any]] = {}
        self._loop_clients_lock = threading.Lock()

    def _key(self) -> str | None:
        if self._api_key:
            return self._api_key
        if self._credential is not None:
            key = self._credential()
            if key:
                return key
        return os.environ.get("ANTHROPIC_API_KEY")

    def _checked_key(self) -> str:
        """The key this call will use, refused when it must never reach the
        raw API. Blocking (the credential may refresh): call it off the loop."""
        key = self._key()
        if not key:
            raise RuntimeError(
                "No Anthropic credential — connect it on the Connections page."
            )
        # The raw Messages API is API-KEY-ONLY. A Claude Pro/Max subscription is
        # used the sanctioned way — inherited from the logged-in `claude` CLI
        # (providers/adapters/subprocess_cli.py), never by sending an account
        # OAuth token (`sk-ant-oat...`) to this endpoint. Reject one outright so a
        # stray token can never reach the raw API.
        if key.startswith("sk-ant-oat"):
            raise RuntimeError(
                "Anthropic OAuth account tokens are not used for the raw API. "
                "Connect an API key (sk-ant-…), or use your logged-in Claude CLI "
                "(inherited automatically)."
            )
        return key

    @staticmethod
    def _build(key: str):
        from anthropic import AsyncAnthropic  # lazy import

        # A 60s request timeout (matching the OpenAI/Google adapters) so a slow or
        # half-open connection trips the router's PROVIDER_FAILED fallback promptly
        # instead of hanging a session on the SDK's ~600s default.
        return AsyncAnthropic(api_key=key, timeout=60.0)

    def _client(self):
        """A NEW client for the current key (no reuse). The calls themselves go
        through :meth:`_session_client`; tests replace this seam with
        ``adapter._client = lambda: fake``, and that replacement still wins."""
        return self._build(self._checked_key())

    def _client_for_loop(self, loop: asyncio.AbstractEventLoop):
        """The client for ``loop``, built once and reused (v1.332.0). Blocking —
        run it off the loop, as the old per-call build was.

        Every complete/stream call used to build a fresh ``AsyncAnthropic`` —
        a new httpx connection pool and a new TLS handshake on every tool round
        of a turn — and never closed it. The adapter lives as long as the
        manager's cache, so it now keeps one client per (event loop, key). A
        client is NEVER handed to a loop other than the one it was built on: an
        httpx pool's connections belong to their loop, and the daemon has more
        than one (a schedule fire runs under ``asyncio.run`` on the scheduler's
        thread). A new key builds a new client; the one it replaces is dropped,
        not closed, because a call still running on it must finish. A loop that
        has closed or been collected takes its client out of the table."""
        key = self._checked_key()
        with self._loop_clients_lock:
            for lid, (ref, _k, _c) in list(self._loop_clients.items()):
                held = ref()
                if held is None or held.is_closed():
                    del self._loop_clients[lid]
            hit = self._loop_clients.get(id(loop))
            if hit is not None and hit[0]() is loop and hit[1] == key:
                return hit[2]
            client = self._build(key)
            self._loop_clients[id(loop)] = (weakref.ref(loop), key, client)
            return client

    async def _session_client(self):
        """The client for this call, built off the loop (credential resolution
        may trigger a blocking OAuth token refresh). A replaced ``_client``
        (the tests' seam, or a subclass's) is called exactly as before."""
        seam = self._client
        # Compared with the function as DEFINED (``_DEFAULT_CLIENT``), not with
        # ``AnthropicAdapter._client`` read now: a seam patched onto the CLASS
        # would otherwise read as the default and be skipped.
        if getattr(seam, "__func__", None) is not _DEFAULT_CLIENT:
            return await asyncio.to_thread(seam)
        return await asyncio.to_thread(self._client_for_loop, asyncio.get_running_loop())

    @staticmethod
    def _to_anthropic_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                # v1.371.0: a picture the tool handed back (page_screenshot)
                # rides inside the tool result, beside its text.
                pictures = [
                    {"type": "image", "source": {"type": "base64", "media_type": img["media_type"],
                                                 "data": img["data_b64"]}}
                    for img in (m.images or [])
                    if isinstance(img, dict) and str(img.get("media_type") or "").startswith("image/")
                    and img.get("data_b64")
                ]
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": ([{"type": "text", "text": m.content or "(no output)"}, *pictures]
                                            if pictures else m.content),
                            }
                        ],
                    }
                )
            elif m.role == "assistant" and _own_blocks(m):
                # v1.263.0: an assistant turn produced with extended thinking
                # is replayed VERBATIM — thinking blocks (with signatures) ahead
                # of the tool_use they preceded — or the API refuses the
                # follow-up call of a tool loop. Written by this adapter only.
                out.append(
                    {"role": "assistant", "content": [dict(b) for b in _own_blocks(m)]}
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
                # v1.371.0 review: a picture sent ALONE (the chat page and the
                # IJ harness both allow it) has no words, and the Messages API
                # refuses an empty text block ("text content blocks must be
                # non-empty") — the picture goes by itself.
                img_blocks: list[dict[str, Any]] = (
                    [{"type": "text", "text": m.content}] if (m.content or "").strip() else []
                )
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
        # Guided-decoding knobs (v1.203.0): accepted so callers can pass them
        # uniformly across adapters; IGNORED — not wired into the Anthropic
        # Messages wire format this wave (openai-compat family only).
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        # Off the loop (credential resolution may refresh), reused per loop.
        client = await self._session_client()
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
        thinking_kw, max_tokens = self._thinking(reasoning)
        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_param,
                messages=anthropic_messages,
                tools=tool_defs,
                **thinking_kw,
            )
        except Exception as exc:  # noqa: BLE001 — typed for the router's classifier
            raise _anthropic_provider_error(exc) from exc
        # v1.378.0: a safety refusal is a typed error, never an empty reply.
        # MIRROR NOTE (lock-step): stream() below.
        refused = _refusal(resp, provider=self.provider)
        if refused is not None:
            raise refused
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        finish = _finish(resp.stop_reason)
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
            raw_blocks=_raw_blocks(resp.content) if thinking_kw else [],
            thinking=_thinking_text(resp.content),
        )

    def _thinking(self, reasoning: str) -> tuple[dict[str, Any], int]:
        """The extended-thinking kwargs for a level, and the max_tokens that
        makes room for them (v1.263.0). ``""`` → nothing, the old max_tokens:
        byte-identical to every call before the level existed."""
        level = normalize_level(reasoning)
        if not level:
            return {}, self.max_tokens
        budget = budget_tokens(level)
        # The budget must fit INSIDE max_tokens with room for the answer.
        room = max(self.max_tokens, budget + 4096)
        if anthropic_thinking_mode(self.model) == ADAPTIVE:
            # v1.330.0: Claude 4.6 and later (Fable, Mythos, Opus 4.7+, Sonnet
            # 5+, Haiku 5.5) take the level as `output_config.effort` beside
            # `thinking: {type: "adaptive"}`; 4.7 and later answer a budget
            # with a 400. Anthropic's docs, read 2026-10-10
            # (platform.claude.com/docs/en/build-with-claude/extended-thinking):
            # "Claude 4.7 and later models do not support it and reject
            # requests that use it, returning a 400 error." / "remove
            # `budget_tokens`, set `thinking: {type: "adaptive"}`, and control
            # reasoning depth with `output_config: {effort: ...}`". The three
            # words are the API's own (low / medium / high), so the receipt's
            # level is exactly what was sent. `display: "summarized"` keeps
            # the thinking text the chat shows: these models default to
            # "omitted" (.../thinking: "`display` works in both modes").
            # `max_tokens` keeps the same per-level room, because thinking
            # still counts toward it ("set it high enough to leave room for
            # both the reasoning and the answer"). No sampling parameter is
            # sent (they return a 400 on these models), and tool_choice is
            # never forced here.
            return (
                {
                    "thinking": {"type": "adaptive", "display": "summarized"},
                    "output_config": {"effort": level},
                },
                room,
            )
        return (
            {"thinking": {"type": "enabled", "budget_tokens": budget}},
            room,
        )

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
        """Real token streaming (FX-01). Builds the SAME request as :meth:`complete`
        — verbatim tool/system/message construction + prompt-cache breakpoints — but
        calls ``client.messages.stream(...)``, yielding incremental ``{"type":"text",
        ...}`` deltas then a single ``{"type":"final","response": LLMResponse}`` whose
        ``response`` is byte-identical to what :meth:`complete` returns for the same
        call. Errors are wrapped in the same :func:`_anthropic_provider_error` so the
        router classifies transient/permanent exactly as on the non-streaming path.
        """
        # Off the loop (credential resolution may refresh), reused per loop.
        client = await self._session_client()
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
        thinking_kw, max_tokens = self._thinking(reasoning)
        try:
            # The SDK's streaming helper accumulates state for us. The events
            # are read the way the SDK's own `text_stream` reads them (a
            # `content_block_delta` whose delta is `text_delta` -> answer text)
            # and, since v1.323.0, a `thinking_delta` becomes a `thinking`
            # frame — display-only, never answer text, so it still never
            # reaches the reply. `get_final_message()` returns the SAME Message
            # object messages.create() would have — so the aggregate below is
            # identical to complete().
            streamed = False
            async with client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system_param,
                messages=anthropic_messages,
                tools=tool_defs,
                **thinking_kw,
            ) as s:
                async for event in s:
                    if getattr(event, "type", None) != "content_block_delta":
                        continue
                    delta = getattr(event, "delta", None)
                    kind = getattr(delta, "type", None)
                    if kind == "text_delta":
                        piece = getattr(delta, "text", "")
                        if piece:
                            streamed = True
                            yield {"type": "text", "text": piece}
                    elif kind == "thinking_delta":
                        piece = getattr(delta, "thinking", "")
                        if piece:
                            yield {"type": "thinking", "text": piece}
                final = await s.get_final_message()
        except Exception as exc:  # noqa: BLE001 — typed for the router's classifier
            raise _anthropic_provider_error(exc) from exc
        # v1.378.0: a safety refusal is a typed error — after text streamed it
        # is `partial` (the text stays on the user's screen). MIRROR NOTE
        # (lock-step): complete() above.
        refused = _refusal(final, provider=self.provider, emitted=streamed)
        if refused is not None:
            raise refused
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in final.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        finish = _finish(final.stop_reason)
        usage = getattr(final, "usage", None)
        usage_dict = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        yield {
            "type": "final",
            "response": LLMResponse(
                text="".join(text_parts),
                tool_calls=tool_calls,
                finish_reason=finish,
                usage=usage_dict,
                raw_blocks=_raw_blocks(final.content) if thinking_kw else [],
                thinking=_thinking_text(final.content),
            ),
        }


#: v1.332.0: ``_client`` as defined here. :meth:`AnthropicAdapter._session_client`
#: reuses a client per loop only while ``_client`` is still this function;
#: any replacement (on the instance, on the class, in a subclass) is called.
_DEFAULT_CLIENT = AnthropicAdapter._client
