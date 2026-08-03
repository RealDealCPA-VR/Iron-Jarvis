"""Prompted tool-calling scaffold for text-only completers (§6, v1.131.0).

A local model (fleet/custom endpoint, subscription CLI) that cannot emit native
``tool_calls`` declares ``tool_use: False`` and was therefore barred from every
agentic run by the router's capability routing. :class:`PromptedToolsAdapter`
wraps ANY inner :class:`LLMAdapter` and closes that gap in-band:

* tools are rendered into the system prompt with a STRICT output contract
  (one fenced ``tool_call`` JSON block per turn, plain text to answer);
* the inner reply is parsed back into a structured :class:`ToolCall`;
* a malformed-but-tool-intent reply gets up to two REPAIR rounds — the raw
  reply plus the precise validation error are appended and the inner model is
  re-asked. Repairs exhausted → the raw text is returned as a plain answer.
  The wrapper never fabricates a call and never invents an answer, so the
  honest-failure contract (CLAUDE.md) holds: everything the caller sees came
  from the real inner model.

The wrapper reports the inner's capabilities with ``tool_use: True`` and
``tool_use_mode: "prompted"`` so callers can tell scaffolded tool use from
native. Streaming stays the base default (complete + one chunk): stream-parsing
a fence mid-flight would leak half a tool call as user-visible text.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .base import LLMAdapter, LLMMessage, LLMResponse, ToolCall

#: Above this many tools the rendered section switches to name+first-sentence
#: only — an agent run can arm dozens of tools and a small local model loses
#: the contract in a wall of JSON schemas long before it loses the tool names.
COMPACT_THRESHOLD = 24

#: Per-tool clip bounds (full mode) — keep the rendered section prompt-sized.
_DESC_CLIP = 300
_SCHEMA_CLIP = 600

#: Malformed replies get at most this many repair rounds before the wrapper
#: degrades to returning the raw text as a plain answer.
MAX_REPAIRS = 2

#: The fence OPENING only (case-insensitive, tolerant of ``` tool_call`` /
#: ``Tool_Call`` variants small models emit). The payload is NOT captured by
#: regex: a non-greedy ``(.*?)``` `` match truncates at the first ``` INSIDE the
#: JSON (a write_file whose content contains a markdown fence — the canonical
#: agent action) — instead :func:`_find_fence` json-decodes forward from the
#: opening, which is immune to embedded backticks.
_FENCE_OPEN_RE = re.compile(r"```[ \t]*tool_call\b[ \t]*\r?\n?", re.IGNORECASE)

#: Zero-width/BOM characters some local-model tokenizers leak around output;
#: stripped before the bare-JSON check (``str.strip()`` does NOT remove them).
_ZERO_WIDTH = "﻿​‌‍⁠"

_JSON_DECODER = json.JSONDecoder()

_CONTRACT = (
    "## Tool calling (prompted mode)\n"
    "You can use the tools listed below. To call a tool, reply with ONLY a\n"
    "fenced block in exactly this form — no other text before or after it:\n"
    "```tool_call\n"
    '{"name": "<tool name>", "arguments": { ... }}\n'
    "```\n"
    "Rules: ONE tool call per reply; \"arguments\" must be a JSON object\n"
    "matching the tool's parameters. After a tool result comes back you may\n"
    "call another tool or answer. To answer the user directly, reply with\n"
    "plain text and NO tool_call block.\n"
    "\n"
    "Example — user: \"What's in notes.txt?\" — you reply:\n"
    "```tool_call\n"
    '{"name": "read_file", "arguments": {"path": "notes.txt"}}\n'
    "```\n"
)


def _clip(s: str, limit: int) -> str:
    s = s or ""
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _first_sentence(s: str) -> str:
    s = (s or "").strip().split("\n", 1)[0]
    dot = s.find(". ")
    if dot != -1:
        s = s[: dot + 1]
    return _clip(s, 140)


def _spec_fields(spec: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Name/description/params from either spec shape — the registry emits
    Anthropic-style ``{name, description, input_schema}``; tolerate the
    OpenAI-style ``{"function": {...}}`` nesting so a pass-through caller
    doesn't render blank tools."""
    fn = spec.get("function")
    if isinstance(fn, dict):
        spec = fn
    params = spec.get("input_schema") or spec.get("parameters") or {}
    if not isinstance(params, dict):
        params = {}
    return str(spec.get("name") or ""), str(spec.get("description") or ""), params


def render_tools_section(tools: list[dict[str, Any]]) -> str:
    """The system-prompt section: contract + few-shot + bounded tool list."""
    compact = len(tools) > COMPACT_THRESHOLD
    lines = [_CONTRACT, "### Available tools"]
    for spec in tools:
        name, desc, params = _spec_fields(spec)
        if not name:
            continue
        if compact:
            lines.append(f"- {name} — {_first_sentence(desc)}")
        else:
            entry = f"- {name} — {_clip(desc.strip(), _DESC_CLIP)}"
            if params:
                try:
                    schema = json.dumps(params, separators=(",", ":"))
                except (TypeError, ValueError):
                    schema = ""
                if schema and schema != "{}":
                    entry += f"\n  Parameters (JSON Schema): {_clip(schema, _SCHEMA_CLIP)}"
            lines.append(entry)
    return "\n".join(lines)


def convert_tool_turns(messages: list[LLMMessage]) -> list[LLMMessage]:
    """Replay a tool-loop transcript in words a text-only model understands.

    ``role="tool"`` results become plain user turns ('Tool "x" returned: ...'),
    and an assistant turn that carried structured ``tool_calls`` is re-rendered
    as the fenced block it would have written under the contract — otherwise the
    model sees an empty assistant turn followed by an unexplained tool result.
    Builds NEW message objects; the caller's transcript is never mutated (it is
    persisted and may be replayed to a native adapter after a failover).
    """
    out: list[LLMMessage] = []
    for m in messages:
        if m.role == "tool":
            name = m.name or m.tool_call_id or "tool"
            out.append(
                LLMMessage(role="user", content=f'Tool "{name}" returned:\n{m.content}')
            )
        elif m.role == "assistant" and m.tool_calls:
            parts = [m.content] if m.content else []
            for tc in m.tool_calls:
                try:
                    args = json.dumps(tc.arguments, separators=(",", ":"))
                except (TypeError, ValueError):
                    args = "{}"
                parts.append(
                    f'```tool_call\n{{"name": {json.dumps(tc.name)}, "arguments": {args}}}\n```'
                )
            out.append(LLMMessage(role="assistant", content="\n".join(parts)))
        else:
            out.append(m)
    return out


def _find_fence(
    raw: str, pos: int = 0
) -> tuple[int, int | None, bool, Any, str] | None:
    """Locate + decode the next ``tool_call`` fence at/after ``pos``.

    Returns ``None`` when no opening fence exists, else
    ``(start, span_end, parsed, payload, json_error)``:

    * ``parsed=True`` — the payload json-decoded; ``span_end`` covers through
      the closing fence (or the JSON's own end when the model forgot the
      closing ``` — the value is complete, so we accept it);
    * ``parsed=False, span_end=None`` — opened but the payload never decodes
      AND no closing ``` exists (a truncated reply: tool INTENT, not answer);
    * ``parsed=False, span_end=int`` — a closed block whose payload is not
      valid JSON; ``json_error`` carries the decoder's message.

    Decoding uses ``raw_decode`` FORWARD from the opening — a single O(n) pass
    that is immune to ``` fences embedded inside the JSON string values (the
    non-greedy-regex approach truncated a write_file whose content contained a
    markdown code block, failing a fully contract-compliant reply).
    """
    m = _FENCE_OPEN_RE.search(raw, pos)
    if m is None:
        return None
    idx = m.end()
    while idx < len(raw) and (raw[idx].isspace() or raw[idx] in _ZERO_WIDTH):
        idx += 1
    try:
        payload, jend = _JSON_DECODER.raw_decode(raw, idx)
    except ValueError as exc:
        close = raw.find("```", idx)
        if close == -1:
            return m.start(), None, False, None, str(exc)
        return m.start(), close + 3, False, None, str(exc)
    close = raw.find("```", jend)
    span_end = close + 3 if close != -1 else jend
    return m.start(), span_end, True, payload, ""


def _strip_fences(raw: str, first: tuple[int, int]) -> str:
    """The reply text with the parsed fence (and any further complete
    ``tool_call`` blocks — a model that emitted two despite the ONE rule)
    removed; the surviving prose is the user-visible text."""
    out = raw[: first[0]] + raw[first[1] :]
    while True:
        nxt = _find_fence(out)
        if nxt is None or nxt[1] is None:
            break
        out = out[: nxt[0]] + out[nxt[1] :]
    return out.strip()


def _parse_reply(
    text: str, known: set[str]
) -> tuple[ToolCall | None, str, str | None]:
    """Parse an inner reply against the contract.

    Returns ``(call, cleaned_text, error)`` — exactly one of ``call``/``error``
    is set unless the reply is a plain answer (both ``None``: no tool intent).
    ``error`` is the precise validation message fed back on a repair round.
    """
    raw = text or ""
    fence = _find_fence(raw)
    payload: Any = None
    span: tuple[int, int] | None = None
    if fence is not None:
        start, span_end, parsed, payload, jerr = fence
        if not parsed:
            if span_end is None:
                # Opened-but-never-completed fence is tool INTENT, not an answer.
                return None, raw, "the tool_call block is missing its closing ``` fence"
            return None, raw, f"the tool_call block is not valid JSON ({jerr})"
        span = (start, span_end)
        if not isinstance(payload, dict):
            return None, raw, "the tool_call payload must be a JSON object"
    else:
        # Bare-JSON fallback: models drop the fence but keep the shape. Only a
        # top-level object with EXACTLY the contract keys counts — anything
        # else is a plain answer that merely happens to be JSON. Zero-width/BOM
        # tokenizer leakage is stripped first (str.strip() doesn't cover it).
        stripped = raw.strip().strip(_ZERO_WIDTH).strip()
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except (TypeError, ValueError):
                obj = None
            if isinstance(obj, dict) and set(obj.keys()) == {"name", "arguments"}:
                payload = obj
        if not isinstance(payload, dict):
            return None, raw, None  # plain answer
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return None, raw, '"name" must be a string naming one of the available tools'
    if known and name not in known:
        # Tolerate a wrong-CASE name when it is unambiguous — a repair round
        # for `Read_File` vs `read_file` just burns tokens.
        ci = [k for k in known if k.lower() == name.lower()]
        if len(ci) == 1:
            name = ci[0]
        else:
            return (
                None,
                raw,
                f'unknown tool "{name}" — available tools: {", ".join(sorted(known))}',
            )
    args = payload.get("arguments", {})
    if args is None:
        args = {}
    if isinstance(args, str):
        # Double-encoded arguments ("arguments": "{\"path\": ...}") — a common
        # small-model slip; accept it when the string decodes to an object.
        try:
            decoded = json.loads(args)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict):
            args = decoded
    if not isinstance(args, dict):
        return (
            None,
            raw,
            f'"arguments" must be a JSON object, got {type(args).__name__}',
        )
    cleaned = _strip_fences(raw, span) if span is not None else ""
    call = ToolCall(id=f"ptc_{uuid.uuid4().hex[:12]}", name=name, arguments=args)
    return call, cleaned, None


class PromptedToolsAdapter(LLMAdapter):
    """Wrap a text-only :class:`LLMAdapter` so it can drive the agent tool loop."""

    def __init__(self, inner: LLMAdapter, *, max_repairs: int = MAX_REPAIRS) -> None:
        self.inner = inner
        self._max_repairs = max(0, int(max_repairs))

    # The wrapper IS the inner model as far as routing identity goes — the
    # router's per-provider health/dedup/events key off these two fields.
    @property
    def provider(self) -> str:  # type: ignore[override]
        return getattr(self.inner, "provider", "")

    @property
    def model(self) -> str:  # type: ignore[override]
        return getattr(self.inner, "model", "")

    def capabilities(self) -> dict[str, Any]:
        try:
            caps = dict(self.inner.capabilities() or {})
        except Exception:  # noqa: BLE001 — a bare fake without caps still wraps
            caps = {"provider": self.provider, "model": self.model}
        caps["tool_use"] = True
        caps["tool_use_mode"] = "prompted"
        return caps

    # stream() is deliberately NOT overridden: the base default (complete + one
    # chunk) applies. Stream-parsing the fence would either leak a half-written
    # tool_call block as user-visible text or require buffering the whole reply
    # anyway — which is exactly what the default does.

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        if not tools:
            # No tools in play → the wrapper is invisible (system untouched,
            # transcript untouched). Keeps the no-tools path byte-identical.
            return await self.inner.complete(system=system, messages=messages, tools=tools)

        section = render_tools_section(tools)
        scaffold_system = f"{system}\n\n{section}" if system else section
        known = {n for n in (_spec_fields(t)[0] for t in tools) if n}
        convo = convert_tool_turns(messages)
        usage = {"input_tokens": 0, "output_tokens": 0}

        resp: LLMResponse | None = None
        for round_no in range(self._max_repairs + 1):
            # Inner gets tools=[] — it is a text-only completer; the tools live
            # in the system section above.
            resp = await self.inner.complete(
                system=scaffold_system, messages=convo, tools=[]
            )
            for k in usage:
                try:
                    usage[k] += int((resp.usage or {}).get(k, 0))
                except (TypeError, ValueError):
                    pass
            call, cleaned, error = _parse_reply(resp.text, known)
            if call is not None:
                return LLMResponse(
                    text=cleaned,
                    tool_calls=[call],
                    finish_reason="tool_use",
                    usage=usage,
                )
            if error is None:
                # Plain answer — pass the inner's text through untouched.
                return LLMResponse(
                    text=resp.text, finish_reason=resp.finish_reason, usage=usage
                )
            if round_no >= self._max_repairs:
                break
            # REPAIR: feed the raw reply + the precise error back and re-ask.
            # New list — the caller's message objects are never mutated.
            convo = convo + [
                LLMMessage(role="assistant", content=resp.text),
                LLMMessage(
                    role="user",
                    content=(
                        f"Your tool call was invalid: {error}.\n"
                        "To call a tool, reply with ONLY a fenced block:\n"
                        "```tool_call\n"
                        '{"name": "<tool name>", "arguments": { ... }}\n'
                        "```\n"
                        "using one of the available tools — or answer in plain "
                        "text with no tool_call block."
                    ),
                ),
            ]
        # Repairs exhausted: honest degradation — the model's own words, as a
        # plain answer. Never fabricate a call it didn't manage to make.
        assert resp is not None
        return LLMResponse(text=resp.text, finish_reason="stop", usage=usage)
