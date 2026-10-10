"""Adapter for a fleet node that speaks the ANTHROPIC Messages API (v1.329.0).

A saved custom endpoint used to be an OpenAI-compatible adapter, always. The
"Fetch available models" button could already LIST an Anthropic-compatible
server's models (v1.329.0 S4), so a user could pick a model that then never
answered: the chat went out as ``POST /v1/chat/completions`` with a Bearer
key. A node now carries ``protocol`` (``openai`` | ``anthropic``) and
:func:`iron_jarvis.fleet.adapter.adapter_for` picks this class for
``anthropic``.

What it speaks: ``POST {base}/v1/messages`` with ``x-api-key`` +
``anthropic-version``, the system prompt in ``system``, tools as Anthropic
tool definitions, tool calls as ``tool_use`` blocks and their results as
``tool_result`` blocks, and real streaming over SSE (``content_block_delta``
text / tool-input / thinking deltas, ``message_delta`` usage and stop reason,
``message_stop``).

Deliberately written over plain httpx rather than on the vendor SDK used by
``providers/adapters/anthropic.py``, for three reasons that each matter here:

* that adapter falls back to ``ANTHROPIC_API_KEY`` from the environment, and
  a user's real Anthropic key must never be sent to a server they typed in;
* redirects are NOT followed (the probe's rule in routes/connections.py), so
  the key only ever travels to the host the user entered;
* the router's liveness pre-probe reads ``_endpoint`` and ``_client()`` off
  the adapter (``ModelRouter._local_liveness``), which this class provides
  in the same shape as the OpenAI adapter.

Errors are typed the OpenAI adapter's way: an HTTP error is a
:class:`ProviderError` with its status and ``Retry-After``; a transport
failure or timeout stays its native httpx type (the router classifies those
as unreachable / timeout / interrupted for a local node and REFUSES, naming
the node — it never moves the conversation to another provider); an answer
that is not JSON, or not the Messages shape, is a ``ProviderError`` that says
so. The key is never logged, and it is scrubbed from any error text the
server echoes back.

Capability honesty is the same as :class:`FleetAdapter`: tool use and vision
are whatever the node RECORD asserts (``POST /fleet/nodes/{id}/verify`` sets
them after a live probe), never assumed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlsplit

from ..core.jsonish import loads_object, unwrap_arguments
from ..providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ToolCall,
    provider_error_from_response,
)
from .models import FleetNode

#: The Messages API version header (the one stable value Anthropic documents;
#: the model listing in routes/connections.py sends the same one).
ANTHROPIC_VERSION = "2023-06-01"

#: Same request bound as the OpenAI adapter's client (60 s): a local box that
#: is cold-loading a big model gets the router's own retry ladder, not a hang.
_TIMEOUT_S = 60.0

#: Error text is cut here, like the OpenAI adapter's ``_error_detail``.
_DETAIL_MAX = 300

#: An Anthropic ACCOUNT sign-in token. The raw Messages API is key-only, and a
#: subscription is used through the logged-in CLI, never by sending its token
#: (the v1.46.0 compliance decision; the same refusal as AnthropicAdapter).
_OAUTH_TOKEN_PREFIX = "sk-ant-oat"

#: What an in-stream ``error`` event's type means, as an HTTP status, so the
#: router classifies it the same way as the matching HTTP answer.
_STREAM_ERROR_STATUS = {
    "overloaded_error": 529,
    "rate_limit_error": 429,
    "api_error": 500,
    "timeout_error": 504,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "invalid_request_error": 400,
}


def _new_client() -> Any:
    """The HTTP client: 60 s, and redirects NOT followed (the key stays with
    the host the user typed). A module function so a test can hand in a fake
    transport without touching the network."""
    import httpx

    return httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False)


def messages_base(base_url: str) -> str:
    """The ``.../v1`` base for a node URL: a bare host, a ``/v1`` base, or a
    pasted ``/v1/messages`` / ``/v1/models`` URL all land on the same base."""
    u = (base_url or "").strip().rstrip("/")
    for suffix in ("/messages", "/models"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    return u if u.endswith("/v1") else f"{u}/v1"


def address_problem(base_url: str) -> str:
    """Why *base_url* cannot be called, in one sentence, or ``""``. The same
    rule the "Fetch available models" probe applies: http(s) with a host."""
    try:
        parts = urlsplit((base_url or "").strip())
        host = parts.hostname
    except ValueError:
        return "That is not a web address this app can open."
    if parts.scheme.lower() not in ("http", "https") or not host:
        return "The endpoint address must start with http:// or https://."
    return ""


def _finish(stop_reason: Any) -> str:
    """The provider-neutral finish reason (the same mapping as the Anthropic
    API adapter): ``tool_use``, ``max_tokens`` when the answer was cut for
    output tokens or the context window, else ``stop``."""
    if stop_reason == "tool_use":
        return "tool_use"
    if stop_reason in ("max_tokens", "model_context_window_exceeded"):
        return "max_tokens"
    return "stop"


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage(raw: Any, into: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map a Messages ``usage`` object onto the internal shape.

    ``input_tokens`` internally is the WHOLE prompt (base.py LLMResponse), and
    the Messages API counts cache reads and writes OUTSIDE its own
    ``input_tokens``, so they are added in and also kept as the optional
    keys. A later ``message_delta`` usage only ever updates what it carries."""
    out: dict[str, Any] = dict(into or {"input_tokens": 0, "output_tokens": 0})
    if not isinstance(raw, dict):
        return out
    base = out.get("_base_input", out.get("input_tokens", 0))
    if "input_tokens" in raw:
        base = _int(raw.get("input_tokens"))
    reads = out.get("cache_read_input_tokens", 0)
    writes = out.get("cache_creation_input_tokens", 0)
    if "cache_read_input_tokens" in raw and raw.get("cache_read_input_tokens") is not None:
        reads = _int(raw.get("cache_read_input_tokens"))
        out["cache_read_input_tokens"] = reads
    if "cache_creation_input_tokens" in raw and raw.get("cache_creation_input_tokens") is not None:
        writes = _int(raw.get("cache_creation_input_tokens"))
        out["cache_creation_input_tokens"] = writes
    out["_base_input"] = base
    out["input_tokens"] = base + reads + writes
    if "output_tokens" in raw:
        out["output_tokens"] = _int(raw.get("output_tokens"))
    return out


def _public_usage(usage: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in usage.items() if not k.startswith("_")}


def _tool_input(raw: Any) -> dict[str, Any]:
    """A tool call's arguments as a dict. A streamed ``input_json_delta`` is
    ALMOST JSON from a local model often enough that it goes through the one
    recovery ladder (core/jsonish), and the ``{"arguments": …}`` envelope is
    peeled as at the OpenAI adapter's parse sites."""
    if isinstance(raw, dict):
        obj: Any = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = loads_object(raw)
    else:
        obj = {}
    obj = unwrap_arguments(obj)
    return obj if isinstance(obj, dict) else {}


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _push(out: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    """Append a turn, MERGING it into the previous one when the role repeats
    (two tool results in a row, a user line right after them). Strict
    servers refuse two user turns in a row; the hosted API merges them, so
    merging here changes nothing there."""
    if not blocks:
        return
    if out and out[-1]["role"] == role:
        prev = out[-1]["content"]
        if isinstance(prev, str):
            prev = [_text_block(prev)] if prev else []
        out[-1]["content"] = prev + blocks
        return
    out.append({"role": role, "content": blocks})


def to_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Internal messages -> Messages API turns.

    Always REBUILT from text + tool calls: ``raw_blocks`` written by the hosted
    Anthropic adapter (thinking blocks with signatures) are never replayed to
    another server, which cannot verify them. Empty text blocks are dropped
    (the API refuses them), and an assistant turn with nothing in it is left
    out rather than sent empty."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            _push(
                out,
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content or "",
                    }
                ],
            )
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append(_text_block(m.content))
            for tc in m.tool_calls or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments if isinstance(tc.arguments, dict) else {},
                    }
                )
            _push(out, "assistant", blocks)
        else:
            blocks = [_text_block(m.content)] if m.content else []
            for img in m.images or []:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.get("media_type", "image/png"),
                            "data": img.get("data_b64", ""),
                        },
                    }
                )
            _push(out, "user", blocks)
    return out


def to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Internal tool specs are already Anthropic-shaped (name, description,
    input_schema); only those three keys go out."""
    out: list[dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        out.append(
            {
                "name": t["name"],
                "description": t.get("description", "") or "",
                "input_schema": t.get("input_schema") or {"type": "object", "properties": {}},
            }
        )
    return out


class AnthropicFleetAdapter(LLMAdapter):
    """One fleet node over the Anthropic Messages API."""

    def __init__(
        self,
        *,
        node: FleetNode,
        model: str | None = None,
        credential: Callable[[], str | None] | None = None,
        http: Any = None,
        max_tokens: int = 4096,
    ) -> None:
        self.node = node
        self.model = model or node.default_model or "default"
        self.provider = f"fleet-{node.id}"
        self.max_tokens = max_tokens
        self._credential = credential
        self._http = http
        self._base = messages_base(node.base_url)
        #: Read by the router's liveness pre-probe: ``{base}/models`` is what
        #: ``ModelRouter._models_url`` makes of it (any answer = alive).
        self._endpoint = self._base

    # -- capability honesty (same contract as FleetAdapter) ------------------
    def capabilities(self) -> dict[str, Any]:
        caps = super().capabilities()
        caps["tool_use"] = bool(self.node.tool_use)
        caps["vision"] = bool(self.node.vision)
        return caps

    # -- transport ------------------------------------------------------------
    def _client(self) -> Any:
        if self._http is None:
            self._http = _new_client()
        return self._http

    def _key(self) -> str:
        if self._credential is None:
            return ""
        try:
            return (self._credential() or "").strip()
        except Exception:  # noqa: BLE001 — a vault fault is "no key", never a crash
            return ""

    def _scrub(self, text: str, key: str) -> str:
        """Error text with the key taken out, in case the server echoes it."""
        out = text or ""
        if key and len(key) >= 4:
            out = out.replace(key, "[key hidden]")
        return out[:_DETAIL_MAX]

    def _headers(self, key: str) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if key:
            headers["x-api-key"] = key
        return headers

    def _preflight(self, key: str) -> None:
        """Refusals that need no request: a bad address, an account token."""
        problem = address_problem(self.node.base_url)
        if problem:
            raise ProviderError(f"{self.provider}: {problem}", transient=False)
        if key.startswith(_OAUTH_TOKEN_PREFIX):
            raise ProviderError(
                f"{self.provider}: an Anthropic account sign-in token cannot be used "
                "as an endpoint key. Use an API key instead.",
                transient=False,
            )

    def _body(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": to_messages(messages),
        }
        if system:
            body["system"] = system
        tool_defs = to_tools(tools)
        if tool_defs:
            body["tools"] = tool_defs
        if stream:
            body["stream"] = True
        return body

    def _error_detail(self, resp: Any, key: str) -> str:
        """The server's own words for an error answer: the Messages shape
        ``{"type": "error", "error": {"type", "message"}}`` or the OpenAI
        shape ``{"error": {"message"}}``, else the raw text."""
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return self._scrub(getattr(resp, "text", "") or "", key)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            text = str(err.get("message") or err.get("type") or err)
        else:
            text = str(err or data)
        return self._scrub(text, key)

    def _http_error(self, resp: Any, key: str) -> ProviderError:
        status = int(getattr(resp, "status_code", 0) or 0)
        if 300 <= status < 400:
            return ProviderError(
                f"{self.provider} answered with a redirect (http {status}). It was not "
                "followed, so the key stays with the address you entered. Use the "
                "address it redirects to.",
                status_code=status,
                transient=False,
            )
        return provider_error_from_response(self.provider, resp, self._error_detail(resp, key))

    def _shape_error(self, why: str, status: int | None = 200) -> ProviderError:
        return ProviderError(
            f"{self.provider} answered, but {why}. Check that the address is an "
            "Anthropic-compatible server.",
            status_code=status,
            transient=False,
        )

    def _parse(self, data: Any, status: int | None = 200) -> LLMResponse:
        if not isinstance(data, dict):
            raise self._shape_error("not with a Messages API reply", status)
        if data.get("type") == "error" or (
            "content" not in data and isinstance(data.get("error"), dict)
        ):
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            code = _STREAM_ERROR_STATUS.get(str(err.get("type") or ""), status)
            raise ProviderError(
                f"{self.provider} API error {code}: {str(err.get('message') or err)[:_DETAIL_MAX]}",
                status_code=code,
            )
        content = data.get("content")
        if not isinstance(content, list):
            raise self._shape_error("not with a Messages API reply", status)
        text: list[str] = []
        thinking: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                text.append(block["text"])
            elif kind == "thinking" and isinstance(block.get("thinking"), str):
                thinking.append(block["thinking"])
            elif kind == "tool_use" and block.get("name"):
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"toolu_{len(calls)}"),
                        name=str(block["name"]),
                        arguments=_tool_input(block.get("input")),
                    )
                )
        finish = _finish(data.get("stop_reason"))
        if calls and finish == "stop":
            finish = "tool_use"
        return LLMResponse(
            text="".join(text),
            tool_calls=calls,
            finish_reason=finish,
            usage=_public_usage(_usage(data.get("usage"))),
            thinking="\n\n".join(thinking),
        )

    # -- the interface --------------------------------------------------------
    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs and the reasoning level are accepted so callers
        # can pass them uniformly; NOT sent (no Messages-API spelling is
        # offered for a custom server).
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        key = await asyncio.to_thread(self._key)
        self._preflight(key)
        body = self._body(system, messages, tools, stream=False)
        resp = await self._client().post(
            f"{self._base}/messages", headers=self._headers(key), json=body
        )
        status = int(getattr(resp, "status_code", 200) or 200)
        if status >= 300:
            raise self._http_error(resp, key)
        try:
            data = resp.json()
        except ValueError:
            raise self._shape_error("not with JSON", status) from None
        return self._parse(data, status)

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
        """Real streaming over SSE: ``text`` and ``thinking`` frames as they
        arrive, then one ``final`` frame whose response matches what
        :meth:`complete` returns for the same answer. A server that ignores
        ``stream: true`` and answers one JSON body is parsed and degraded to a
        single chunk, honestly. A stream that ends before the reply is
        finished raises (the router's "dropped mid-answer" wording), never a
        short reply that reads as complete."""
        import httpx

        key = await asyncio.to_thread(self._key)
        self._preflight(key)
        body = self._body(system, messages, tools, stream=True)
        headers = self._headers(key)
        headers["accept"] = "text/event-stream"
        async with self._client().stream(
            "POST", f"{self._base}/messages", headers=headers, json=body
        ) as resp:
            status = int(getattr(resp, "status_code", 200) or 200)
            if status >= 300:
                await resp.aread()
                raise self._http_error(resp, key)

            text: list[str] = []
            # Per block, so the final joins blocks the way complete() does.
            thinking_by_block: dict[int, list[str]] = {}
            blocks: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}
            stop_reason: Any = None
            saw_sse = False
            finished = False
            raw_tail: list[str] = []
            async for line in resp.aiter_lines():
                s = line.strip()
                if not s.startswith("data:"):
                    if s and not saw_sse and not s.startswith(("event:", ":")) and len(raw_tail) < 4000:
                        raw_tail.append(line)
                    continue
                saw_sse = True
                payload = s[len("data:") :].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "message_start":
                    msg = event.get("message") or {}
                    usage = _usage(msg.get("usage") if isinstance(msg, dict) else None, usage)
                elif kind == "content_block_start":
                    idx = _int(event.get("index"))
                    blk = event.get("content_block") or {}
                    if not isinstance(blk, dict):
                        blk = {}
                    blocks[idx] = {
                        "type": blk.get("type"),
                        "id": blk.get("id") or "",
                        "name": blk.get("name") or "",
                        "input": blk.get("input") if isinstance(blk.get("input"), dict) else {},
                        "json": "",
                    }
                    first = blk.get("text") if blk.get("type") == "text" else None
                    if isinstance(first, str) and first:
                        text.append(first)
                        yield {"type": "text", "text": first}
                elif kind == "content_block_delta":
                    idx = _int(event.get("index"))
                    delta = event.get("delta") or {}
                    if not isinstance(delta, dict):
                        continue
                    dkind = delta.get("type")
                    if dkind == "text_delta":
                        piece = delta.get("text")
                        if isinstance(piece, str) and piece:
                            text.append(piece)
                            yield {"type": "text", "text": piece}
                    elif dkind == "thinking_delta":
                        piece = delta.get("thinking")
                        if isinstance(piece, str) and piece:
                            thinking_by_block.setdefault(idx, []).append(piece)
                            yield {"type": "thinking", "text": piece}
                    elif dkind == "input_json_delta":
                        slot = blocks.setdefault(
                            idx,
                            {"type": "tool_use", "id": "", "name": "", "input": {}, "json": ""},
                        )
                        piece = delta.get("partial_json")
                        if isinstance(piece, str):
                            slot["json"] += piece
                elif kind == "message_delta":
                    delta = event.get("delta") or {}
                    if isinstance(delta, dict) and delta.get("stop_reason"):
                        stop_reason = delta.get("stop_reason")
                    usage = _usage(event.get("usage"), usage)
                elif kind == "message_stop":
                    finished = True
                elif kind == "error":
                    err = event.get("error") or {}
                    if not isinstance(err, dict):
                        err = {}
                    code = _STREAM_ERROR_STATUS.get(str(err.get("type") or ""), 500)
                    raise ProviderError(
                        f"{self.provider} API error {code}: "
                        f"{self._scrub(str(err.get('message') or err.get('type') or 'error'), key)}",
                        status_code=code,
                    )

            if not saw_sse:
                # The server ignored stream:true and answered one body.
                if not raw_tail:
                    raise self._shape_error("with an empty reply", status)
                try:
                    data = json.loads("\n".join(raw_tail))
                except ValueError:
                    raise self._shape_error("not with JSON", status) from None
                final = self._parse(data, status)
                if final.thinking:
                    yield {"type": "thinking", "text": final.thinking}
                if final.text:
                    yield {"type": "text", "text": final.text}
                yield {"type": "final", "response": final}
                return

            if not finished and stop_reason is None:
                raise httpx.RemoteProtocolError(
                    f"{self.provider} stopped sending before the reply was finished"
                )

        calls: list[ToolCall] = []
        for idx in sorted(blocks):
            slot = blocks[idx]
            if slot.get("type") != "tool_use" or not slot.get("name"):
                continue
            args = _tool_input(slot["json"]) if slot["json"].strip() else _tool_input(slot["input"])
            calls.append(
                ToolCall(id=str(slot.get("id") or f"toolu_{idx}"), name=str(slot["name"]), arguments=args)
            )
        finish = _finish(stop_reason)
        if calls and finish == "stop":
            finish = "tool_use"
        yield {
            "type": "final",
            "response": LLMResponse(
                text="".join(text),
                tool_calls=calls,
                finish_reason=finish,
                usage=_public_usage(usage),
                thinking="\n\n".join(
                    "".join(thinking_by_block[i]) for i in sorted(thinking_by_block)
                ),
            ),
        }
