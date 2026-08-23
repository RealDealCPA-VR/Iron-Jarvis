"""Guided decoding — the strict_json rung of the tool ladder (v1.203.0, C2).

Ported from the user's own IronCore project (``ironcore/core/guided.py``,
SPEC §6 / CONTRACTS §2 there), adapted to Iron Jarvis's provider vocabulary
(``adapters.base``: LLMMessage/LLMResponse/ToolCall, Anthropic-style tool
specs). The middle rung between native function-calling and the fenced
prompted-tools contract: when the capability envelope says a model cannot
hold the native rung but CAN hold strict_json, the request *constrains the
server's generation* to a JSON schema so the model emits a guaranteed
well-formed tool call — one object, one call:

    {"tool": "read_file", "args": {"path": "src/app.py"}}

and finishes the turn with the ``done`` pseudo-tool:

    {"tool": "done", "args": {"message": "Read the file and fixed the bug."}}

The ``done`` pseudo-tool exists because ``response_format`` forces JSON on
EVERY turn — a fully constrained model can never emit free text to stop, so
without ``done`` the loop could never end.

Three pure helpers (the IronCore port) plus the wrapper that uses them:

- :func:`tool_call_response_format` builds the OpenAI *structured outputs*
  object (``{"type": "json_schema", ...}``) whose schema pins output to one
  call — a ``tool`` enum of every tool NAME plus ``"done"``, and an ``args``
  object. The enum makes a malformed tool name impossible on any backend
  that honours the constraint.
- :func:`render_guided_system_fragment` is the system-prompt text that
  teaches the model the object shape (the schema carries only names, so the
  prose still carries the tool docs), few-shot.
- :func:`parse_guided_tool_call` decodes the constrained reply into a
  :class:`GuidedParse` — EXCLUSIVE three-way: a real call, a ``done``
  finish, or (for a server that ignored ``response_format``) a precise,
  model-facing repair string. It NEVER raises; malformed output is
  repairable data.
- :class:`GuidedToolsAdapter` is the envelope-gated UPGRADE of the fenced
  :class:`~..adapters.prompted_tools.PromptedToolsAdapter`: guided fragment
  in the system prompt, ``response_format`` on the call, NO native tools
  param, ONE repair round, then an honest LADDER-DOWN to the fenced contract
  it subclasses. The JSON scaffold is protocol, not prose — it is suppressed
  from everything the caller sees (a successful parse returns ``text=""``
  with the structured call, exactly the shape the native path returns, so
  downstream cannot tell the rungs apart).

Engagement lives in ``providers/router.py`` (the v1.131.0 wrap seam), gated
by :func:`profile_supports_guided` — measured + CURRENT-generation profiles
whose ladder selects ``strict_json``, nothing else. The generation gate is
the Wave-A reviewer's binding note: Wave A scored strict_json trials on the
bare prompt (no ``response_format`` existed yet), so a stored score that
predates constrained decoding must NOT arm the real rung — absence of
``is_current_generation`` reads as not-current, fail-closed.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any

from .adapters.base import LLMAdapter, LLMMessage, LLMResponse, ToolCall

# The spec-shape reader is deliberately SHARED with the fenced wrapper (it
# tolerates both the registry's Anthropic-style {name, description,
# input_schema} and the OpenAI-style {"function": {...}} nesting) — two
# readers would drift on exactly the malformed specs they exist to tolerate.
from .adapters.prompted_tools import PromptedToolsAdapter, _spec_fields

__all__ = [
    "DONE",
    "GUIDED_REPAIRS",
    "GuidedParse",
    "GuidedToolsAdapter",
    "parse_guided_tool_call",
    "profile_supports_guided",
    "render_guided_system_fragment",
    "tool_call_response_format",
]

#: The pseudo-tool that lets a fully-constrained model stop. Because
#: ``response_format`` forces JSON on every turn the model cannot emit free
#: text to finish, so ``{"tool":"done","args":{"message":...}}`` ends the turn.
DONE = "done"

#: Guided replies get exactly ONE repair round (the error fed back verbatim)
#: before the wrapper ladders down to the fenced prompted-tools contract —
#: which then runs its OWN bounded repairs. A server that ignores
#: ``response_format`` twice is not going to start honouring it on round 3.
GUIDED_REPAIRS = 1

#: Per-tool description clip for the rendered catalog — same bound the fenced
#: contract uses, for the same reason (a wall of prose loses a small model).
_DESC_CLIP = 300

#: Appended to every parser error so the repair round always shows the model a
#: concrete, copyable template of both a call and a finish.
_HINT = (
    'emit exactly one JSON object like {"tool":"read_file","args":{"path":"x"}} '
    'or, to finish, {"tool":"done","args":{"message":"<summary>"}}'
)


@dataclass
class GuidedParse:
    """Outcome of parsing one guided (strict_json) model reply.

    EXCLUSIVE three-way: exactly one of ``call`` / ``done`` / ``error`` is
    meaningful —

    - ``call``: the parsed tool call, or ``None`` when the model finished
      (``done``) or the body was unusable.
    - ``done``: ``True`` when the model emitted the ``done`` pseudo-tool to
      end the turn; ``call`` is ``None`` in that case.
    - ``message``: the ``done`` summary shown to the user; ``""`` otherwise.
    - ``text``: the raw model reply, preserved verbatim (even on error).
    - ``error``: a precise, model-facing repair message when the body was not
      a usable tool-call object; ``None`` on a clean parse (call *or* done).
    """

    call: ToolCall | None = None
    done: bool = False
    message: str = ""
    text: str = ""
    error: str | None = None


def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
    """Unique tool names, in order, tolerant of both spec shapes and of junk
    entries (mirrors the fenced wrapper's guards via ``_spec_fields``)."""
    names: list[str] = []
    for spec in tools:
        if not isinstance(spec, dict):
            continue
        name = _spec_fields(spec)[0]
        if name and name not in names:
            names.append(name)
    return names


def tool_call_response_format(tools: list[dict[str, Any]]) -> dict:
    """The OpenAI structured-outputs ``response_format`` object constraining a
    reply to exactly one tool call.

    The schema's ``tool`` enum is every tool name plus ``"done"`` — the model
    can only ever name a real tool or finish. Names are DATA inside a JSON
    schema (never string-interpolated), so a hostile tool name cannot escape
    the enum. Empty ``tools`` yields an enum of just ``["done"]``.
    """
    names = _tool_names(tools)
    enum = [*names, DONE] if DONE not in names else list(names)
    schema = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": enum},
            "args": {"type": "object"},
        },
        "required": ["tool", "args"],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "iron_jarvis_tool_call",
            "strict": True,
            "schema": schema,
        },
    }


def _render_tool(spec: dict[str, Any]) -> str:
    """One compact catalog entry — name, clipped description, args line."""
    name, desc, params = _spec_fields(spec)
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    rendered: list[str] = []
    if isinstance(props, dict):
        for arg_name, arg_schema in props.items():
            arg_type = "any"
            if isinstance(arg_schema, dict):
                arg_type = arg_schema.get("type", "any")
            tag = f"{arg_name}: {arg_type}"
            if arg_name in required:
                tag += ", required"
            rendered.append(tag)
    args_line = "; ".join(rendered) if rendered else "none"
    desc = (desc or "").strip()
    if len(desc) > _DESC_CLIP:
        desc = desc[: _DESC_CLIP - 1] + "…"
    header = f"- `{name}` - {desc}" if desc else f"- `{name}`"
    return f"{header}\n    args: {args_line}"


def render_guided_system_fragment(tools: list[dict[str, Any]]) -> str:
    """System-prompt text that teaches a model the guided JSON protocol.

    Three parts, kept compact: a one-paragraph rule, a rendered tool catalog
    (name + description + params — the schema carries only NAMES, so the
    prose must carry the docs), and two worked examples — one real call and
    one ``done`` finish — because few-shot beats instructions at this model
    scale. ASCII-safe.
    """
    parts: list[str] = [
        "# Using tools (guided JSON)\n"
        "Reply with EXACTLY ONE JSON object and nothing else: no prose, no code "
        'fences. To call a tool, emit `{"tool": "<name>", "args": {<arguments>}}` '
        "naming one tool from the list below. You will receive the result as a "
        "message; read it, then emit the next object. Use `{}` for a tool "
        "that takes no arguments, and call one tool at a time. When the task is "
        'complete, finish by emitting `{"tool": "done", "args": {"message": '
        '"<short summary>"}}`, which ends the turn and shows your summary.',
        "## Tools you can call",
    ]
    if tools:
        parts.extend(_render_tool(spec) for spec in tools if isinstance(spec, dict))
    else:
        parts.append("(no tools are available this turn)")
    parts.append("## Examples")
    parts.append(
        "A tool call - read a file:\n"
        '{"tool": "read_file", "args": {"path": "src/app.py"}}'
    )
    parts.append(
        "Finishing the turn once the work is done:\n"
        '{"tool": "done", "args": {"message": "Read the file and fixed the bug."}}'
    )
    return "\n\n".join(parts)


def _try_load(candidate: str) -> object | None:
    """``json.loads`` that returns ``None`` instead of raising."""
    try:
        return json.loads(candidate)
    except ValueError:  # json.JSONDecodeError is a ValueError subclass
        return None


def _load_object(text: str) -> dict | None:
    """Best-effort decode of a model reply to a JSON object; ``None`` on
    failure (never raises). A bare load first (the constrained path), then a
    first-``{`` to last-``}`` slice so a single object wrapped in stray prose
    (a server that ignored ``response_format``) is still recovered. Multiple
    objects or genuinely malformed input yield ``None`` — a clean error, not
    a wrong guess."""
    stripped = (text or "").strip()
    obj = _try_load(stripped)
    if isinstance(obj, dict):
        return obj
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        obj = _try_load(stripped[start : end + 1])
        if isinstance(obj, dict):
            return obj
    return None


def _call_id(text: str) -> str:
    """Deterministic call id derived from the raw reply — same reply, same id
    (test- and replay-stable), distinct replies almost surely distinct (the
    fenced wrapper's uuid ids stay unique-per-call; this rung trades that for
    determinism, per the IronCore port)."""
    digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:10]
    return f"gd-{digest}"


def parse_guided_tool_call(text: str, known: "set[str] | None" = None) -> GuidedParse:
    """Decode one guided (strict_json) reply. NEVER raises.

    With ``response_format`` in force the server emits pure JSON, so a bare
    ``json.loads`` is the common path; the prose-slice fallback tolerates a
    server that ignored the constraint. A ``done`` object finishes the turn;
    any other well-formed ``{"tool", "args"}`` object becomes a
    :class:`ToolCall`; anything else becomes a precise, repairable ``error``
    string — never an exception. ``known`` (when given) rejects a tool name
    the schema's enum should have made impossible — the belt for a backend
    that ignored the braces — with the same unambiguous-case tolerance the
    fenced parser applies (a repair round for ``Read_File`` vs ``read_file``
    just burns tokens).
    """
    text = text or ""
    payload = _load_object(text)
    if payload is None:
        return GuidedParse(
            text=text,
            error=f"your reply was not a valid tool-call JSON object; {_HINT}",
        )

    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return GuidedParse(
            text=text,
            error=(
                'your JSON object must carry a string "tool" naming one listed '
                f'tool or "done"; {_HINT}'
            ),
        )

    if tool == DONE:
        args = payload.get("args")
        message = args.get("message", "") if isinstance(args, dict) else ""
        if not isinstance(message, str):
            message = str(message)
        return GuidedParse(done=True, message=message, text=text)

    if known and tool not in known:
        ci = [k for k in known if k.lower() == tool.lower()]
        if len(ci) == 1:
            tool = ci[0]
        else:
            return GuidedParse(
                text=text,
                error=(
                    f'unknown tool "{tool}" — available tools: '
                    f'{", ".join(sorted(known))}; {_HINT}'
                ),
            )

    args = payload.get("args")
    if not isinstance(args, dict):
        return GuidedParse(
            text=text,
            error=(
                'your JSON object must carry an "args" object (use {} for no '
                f"arguments); {_HINT}"
            ),
        )

    return GuidedParse(call=ToolCall(id=_call_id(text), name=tool, arguments=args), text=text)


# --------------------------------------------------------------------------- #
# Engagement gate + the upgraded wrapper
# --------------------------------------------------------------------------- #


def profile_supports_guided(profile: Any) -> bool:
    """May this envelope arm the strict_json rung? Fail-closed on every leg.

    Three conditions, all required:

    1. MEASURED (``is_measured()`` — probed/partial/tuned WITH a stamp): a
       default, seeded, trusted, or probe_failed profile carries no evidence
       for the rung. Trusted also selects ``native`` anyway — frontier stays
       byte-identical by two independent gates.
    2. CURRENT GENERATION (``is_current_generation()``) — the Wave-A
       reviewer's binding note: Wave A scored strict_json on the bare prompt,
       before constrained decoding existed, so a stored score from an older
       probe generation is evidence about a DIFFERENT mechanism. The method
       lands with the C1/C4 generation plumbing; until it exists, absence
       reads as NOT current (getattr-guard), so no stale profile can ever arm
       the rung.
    3. The mechanical ladder itself selects ``strict_json`` (native < its bar,
       strict_json >= its bar).

    Any exception answers ``False`` — the envelope may never break routing.
    """
    try:
        if profile is None or not bool(profile.is_measured()):
            return False
        current = getattr(profile, "is_current_generation", None)
        if not callable(current) or not bool(current()):
            return False
        return profile.select_tool_protocol() == "strict_json"
    except Exception:  # noqa: BLE001 — the envelope must never break routing
        return False


def _accepts_response_format(adapter: Any) -> bool:
    """Can ``adapter.complete`` take the additive ``response_format`` kwarg
    (the C1 openai-compat extension)? Judged by signature, not by try/except —
    a ``TypeError`` raised INSIDE a legacy adapter must not read as "doesn't
    take the kwarg". Unreadable signatures answer ``False`` (ladder down)."""
    fn = getattr(adapter, "complete", None)
    if fn is None:
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "response_format" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def convert_guided_turns(messages: list[LLMMessage]) -> list[LLMMessage]:
    """Replay a tool-loop transcript in the GUIDED protocol's own words.

    ``role="tool"`` results become plain user turns (same wording as the
    fenced converter, so a transcript that laddered between rungs still
    reads consistently), and an assistant turn that carried structured
    ``tool_calls`` is re-rendered as the guided object it would have emitted
    under this contract — otherwise the model sees an empty assistant turn
    followed by an unexplained tool result. Builds NEW message objects; the
    caller's transcript is never mutated (it is persisted and may be replayed
    to a native adapter after a failover).
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
                args: Any = tc.arguments
                try:
                    json.dumps(args)
                except (TypeError, ValueError):
                    args = {}
                parts.append(
                    json.dumps({"tool": tc.name, "args": args}, separators=(",", ":"))
                )
            out.append(LLMMessage(role="assistant", content="\n".join(parts)))
        else:
            out.append(m)
    return out


class GuidedToolsAdapter(PromptedToolsAdapter):
    """The strict_json UPGRADE of the fenced prompted-tools wrapper.

    Same seam, same routing identity, same ``RouteResult`` reason
    ("prompted-tools" — user-configured automation, quiet): the inner model
    is still a completer the router chose, but the tool contract is enforced
    by the SERVER (``response_format`` json_schema) instead of by prose alone.
    Subclassing :class:`PromptedToolsAdapter` buys three guarantees at once:
    the router's idempotence check (``isinstance(..., PromptedToolsAdapter)``)
    still prevents double-wrapping, ``capabilities()`` still reports
    ``tool_use_mode: "prompted"`` (agents/decompose.py keys its engage
    decision on that string, and this rung is an upgrade of the same class of
    scaffolded tool use, not a new capability), and the LADDER-DOWN is simply
    ``super().complete`` — the proven fenced path with its own repairs and
    honest degradation.

    Honesty contract: a successful parse synthesizes EXACTLY the shape the
    native path returns (structured ``tool_calls``, ``finish_reason
    "tool_use"``, ``text=""``) — the JSON scaffold is protocol, not prose,
    and never appears in user-visible text. A parse failure gets ONE repair
    round with the precise error fed back; a second failure ladders down to
    the fenced contract rather than pretending the constraint held. The
    wrapper never fabricates a call and never invents an answer. Streaming
    stays the base default (complete + one chunk), like the fenced wrapper
    and for the same reason.
    """

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (the C1 contract): declared like every other
        # adapter so a caller can pass them uniformly without knowing the
        # class. The honest semantics for a wrapper that IS a response_format
        # user, stated plainly:
        # * ``response_format`` — SUPERSEDED by the guided tool-call schema
        #   while the rung is engaged: the rung exists to enforce ITS schema,
        #   and two body-level constraints cannot both hold.
        # * ``tool_choice`` — ignored on the guided rounds: the inner is
        #   called with ``tools=[]`` (the tool surface lives in the schema +
        #   fragment), so there is nothing for it to steer.
        # * ``extra_body`` — NOT forwarded on the guided rounds: the
        #   openai-compat family merges extra_body LAST (its keys WIN
        #   clashes), so forwarding it would let a caller silently replace
        #   the very constraint this wrapper just promised was in force.
        # On the LADDER-DOWN all three are forwarded to the fenced fallback,
        # whose own contract accepts and deliberately drops them (it speaks a
        # text protocol; a forwarded constraint would fight its scaffold).
        # The no-tools passthrough mirrors the fenced wrapper byte-for-byte:
        # accepted, not forwarded this wave.
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
    ) -> LLMResponse:
        if not tools:
            # No tools in play → the wrapper is invisible (system untouched,
            # transcript untouched) — same rule as the fenced wrapper.
            return await self.inner.complete(system=system, messages=messages, tools=tools)
        if not _accepts_response_format(self.inner):
            # No constrained decoding possible on this adapter → the rung is
            # not real here. Honest ladder-down to the fenced contract, not a
            # guided prompt whose "guarantee" nothing enforces.
            return await super().complete(
                system=system,
                messages=messages,
                tools=tools,
                response_format=response_format,
                tool_choice=tool_choice,
                extra_body=extra_body,
            )

        fragment = render_guided_system_fragment(tools)
        guided_system = f"{system}\n\n{fragment}" if system else fragment
        # The rung's own constraint — supersedes any caller-supplied
        # response_format for as long as the rung is engaged (see above).
        guided_format = tool_call_response_format(tools)
        known = set(_tool_names(tools))
        convo = convert_guided_turns(messages)
        usage = {"input_tokens": 0, "output_tokens": 0}

        for round_no in range(GUIDED_REPAIRS + 1):
            # Inner gets tools=[] — the tool surface lives in the schema + the
            # system fragment; offering native specs alongside the constraint
            # would race two contracts against each other.
            resp = await self.inner.complete(
                system=guided_system,
                messages=convo,
                tools=[],
                response_format=guided_format,
            )
            for k in usage:
                try:
                    usage[k] += int((resp.usage or {}).get(k, 0))
                except (TypeError, ValueError):
                    pass
            parsed = parse_guided_tool_call(resp.text, known=known)
            if parsed.done:
                # The done pseudo-tool IS the plain answer of this protocol —
                # only its message is user-visible, never the JSON envelope.
                return LLMResponse(text=parsed.message, finish_reason="stop", usage=usage)
            if parsed.call is not None:
                # Indistinguishable from the native path downstream: struct
                # call, tool_use finish, and NO scaffold text.
                return LLMResponse(
                    text="",
                    tool_calls=[parsed.call],
                    finish_reason="tool_use",
                    usage=usage,
                )
            if round_no >= GUIDED_REPAIRS:
                break
            # ONE repair: the raw reply + the precise error, then re-ask under
            # the same constraint. New list — caller's messages never mutated.
            convo = convo + [
                LLMMessage(role="assistant", content=resp.text),
                LLMMessage(
                    role="user",
                    content=f"Your guided tool call was invalid: {parsed.error}",
                ),
            ]

        # LADDER-DOWN (honest): the constraint did not hold twice — run the
        # EXISTING fenced prompted-tools path on the ORIGINAL request, the
        # caller's knobs forwarded per ITS contract (accepted, deliberately
        # dropped). Its own repair loop and plain-text degradation apply
        # unchanged. The guided rounds' tokens were really billed, so they
        # merge into the accounting.
        fallback = await super().complete(
            system=system,
            messages=messages,
            tools=tools,
            response_format=response_format,
            tool_choice=tool_choice,
            extra_body=extra_body,
        )
        for k in ("input_tokens", "output_tokens"):
            try:
                fallback.usage[k] = int(fallback.usage.get(k, 0)) + usage[k]
            except (TypeError, ValueError):
                pass
        return fallback
