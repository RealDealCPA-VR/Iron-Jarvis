"""Our transcript -> the Claude CLI's native stream-json frames (v1.300.0).

Pure: no processes, no files, no network. Ported in shape from NousResearch's
MIT-licensed claude-subscription-directsdk plugin (commit ef73726,
``directsdk.prepare_history`` / ``normalize_input_schema`` / ``request_body``),
re-expressed over Iron Jarvis's ``LLMMessage`` / ``ToolCall`` vocabulary.

THREE RULES this module exists to keep:

1. HISTORY IS REPLAYED, NOT FLATTENED. Every prior turn becomes its own
   frame; historical user frames are sent with ``shouldQuery: false`` (the
   transport waits for each zero-turn acknowledgment) and only the LAST
   user/tool-result frame queries. The model sees a real conversation, real
   ``tool_use`` / ``tool_result`` pairs and real images — not one prompt.

2. SIGNED THINKING SURVIVES ONLY WHEN NOTHING WAS EDITED. A step's native
   assistant messages ride ``LLMResponse.raw_blocks`` as ONE carrier
   (``{"type": CARRIER, "version": 1, "messages": [...], "projection":
   {...}}``). On replay, an assistant turn whose current text + tool calls
   still equal the projection replays the native messages verbatim; any edit
   (a lane that trimmed a call, compaction, a hook) rebuilds plain text +
   ``tool_use`` blocks, so a stale signature is never attached to content it
   did not sign. Foreign ``raw_blocks`` (the Anthropic API adapter's) are
   ignored here, as the API adapter ignores ours.

3. TOOL NAMES ARE MAPPED, PER REQUEST, BOTH WAYS. Our registry names may hold
   characters Anthropic refuses (``custom:<name>``) or exceed its length
   (``mcp__<server>__<tool>``). Each name maps to a safe ``[A-Za-z0-9_-]``
   id of at most 55 characters (so ``mcp__ij__`` + id fits 64), the map is
   built from THIS request's tool list, collisions get a short hash suffix,
   and a ``tool_use`` naming anything outside that inventory is an error.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import CARRIER

#: The MCP server name the CLI sees; its tools are ``mcp__ij__<id>``.
SERVER = "ij"
PREFIX = f"mcp__{SERVER}__"
#: 64 (Anthropic's tool-name limit) minus ``len(PREFIX)``.
MAX_ID = 64 - len(PREFIX)

_SAFE = re.compile(r"[A-Za-z0-9_-]+")
_UNSAFE_CHAR = re.compile(r"[^A-Za-z0-9_-]")
_BANNED_TOP_LEVEL = ("oneOf", "allOf", "anyOf")


class HistoryError(RuntimeError):
    """The transcript cannot be expressed as native frames (said in words)."""


class UnknownToolError(RuntimeError):
    """The model named a tool outside this request's inventory."""


# --------------------------------------------------------------------------- #
# Tool names
# --------------------------------------------------------------------------- #
def _hash8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def safe_id(name: str) -> str:
    """The default safe id for one registry name (no collision context).

    A name that is already safe and short enough is its own id; otherwise
    unsafe characters become ``_`` and an over-long result is cut and given
    a hash of the ORIGINAL name, so two long names sharing a prefix differ.
    """
    name = str(name or "")
    if name and _SAFE.fullmatch(name) and len(name) <= MAX_ID:
        return name
    cleaned = _UNSAFE_CHAR.sub("_", name) or "tool"
    if len(cleaned) <= MAX_ID:
        return cleaned
    return cleaned[: MAX_ID - 9] + "_" + _hash8(name)


@dataclass
class ToolNames:
    """This request's reversible registry-name <-> native-id map."""

    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, names: list[str]) -> "ToolNames":
        """Map every name. Names that are already safe claim themselves first,
        so a registry tool literally called ``custom_x`` keeps its own id and
        ``custom:x`` (which would clean to the same) takes the hash suffix —
        the outcome never depends on the order of the tool list."""
        out = cls()
        unique: list[str] = []
        for n in names:
            n = str(n)
            if n not in unique:
                unique.append(n)
        for n in unique:  # pass 1: already-safe names keep themselves
            if _SAFE.fullmatch(n) and len(n) <= MAX_ID:
                out._claim(n, n)
        for n in unique:  # pass 2: everything else, collision-proofed
            if n in out.forward:
                continue
            candidate = safe_id(n)
            if candidate in out.reverse:
                candidate = candidate[: MAX_ID - 9] + "_" + _hash8(n)
            bump = 1
            while candidate in out.reverse:  # a hash collision — vanishingly rare
                candidate = candidate[: MAX_ID - 9] + "_" + _hash8(f"{n}#{bump}")
                bump += 1
            out._claim(n, candidate)
        return out

    def _claim(self, name: str, ident: str) -> None:
        self.forward[name] = ident
        self.reverse[ident] = name

    def native(self, name: str) -> str:
        """The native name for a registry name (``mcp__ij__<id>``). A name not
        in this request (a tool armed on an EARLIER step) uses the default id,
        so a rebuilt historical ``tool_use`` is still well-formed."""
        return PREFIX + self.forward.get(name, safe_id(name))

    def ours(self, native_name: str) -> str:
        """The registry name for a native ``tool_use`` name, or raise."""
        native_name = str(native_name or "")
        if native_name.startswith(PREFIX):
            ident = native_name[len(PREFIX):]
            if ident in self.reverse:
                return self.reverse[ident]
        raise UnknownToolError(
            f"claude-cli: the model called a tool outside this request's inventory "
            f"({native_name[:80]!r}); nothing was run."
        )


# --------------------------------------------------------------------------- #
# Input schemas
# --------------------------------------------------------------------------- #
def _is_null(branch: Any) -> bool:
    return isinstance(branch, dict) and branch.get("type") == "null" and len(branch) == 1


_SCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas")
_SCHEMA_NODES = (
    "items", "additionalProperties", "not", "contains", "if", "then", "else",
    "anyOf", "oneOf", "allOf", "prefixItems", "propertyNames",
    "additionalItems", "unevaluatedProperties", "unevaluatedItems",
)


def strip_nullable_unions(node: Any) -> Any:
    """Remove the ``null`` branch of nullable unions, recursively.

    ``{"anyOf": [X, {"type": "null"}]}`` becomes ``X`` (merged with the
    node's own keys such as ``description``); ``{"type": ["string",
    "null"]}`` becomes ``{"type": "string"}``. A union that is not nullable
    is left exactly as the tool declared it. A local twin of the Hermes
    ``tools.schema_sanitizer.strip_nullable_unions(keep_nullable_hint=False)``
    the original imports. Never mutates its input.
    """
    if isinstance(node, list):
        return [strip_nullable_unions(x) for x in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {k: strip_nullable_unions(v) for k, v in value.items()}
        elif key in _SCHEMA_NODES:
            out[key] = strip_nullable_unions(value)
        else:
            out[key] = copy.deepcopy(value)
    kind = out.get("type")
    if isinstance(kind, list) and "null" in kind:
        rest = [t for t in kind if t != "null"]
        if rest:
            out["type"] = rest[0] if len(rest) == 1 else rest
    for key in ("anyOf", "oneOf"):
        branches = out.get(key)
        if not isinstance(branches, list) or not any(_is_null(b) for b in branches):
            continue
        rest = [b for b in branches if not _is_null(b)]
        if len(rest) == 1 and isinstance(rest[0], dict):
            merged = {k: v for k, v in out.items() if k != key}
            for k, v in rest[0].items():
                merged.setdefault(k, v)
            out = merged
        elif rest:
            out[key] = rest
    return out


def normalize_input_schema(schema: Any) -> dict[str, Any]:
    """The shape Anthropic's validator accepts for ``input_schema``.

    Their ``normalize_input_schema``: nullable unions lose the null branch;
    top-level ``oneOf``/``allOf``/``anyOf`` are dropped (advisory — the
    registry re-validates arguments before ``execute``); an object without
    ``properties`` gets ``{}``. A missing or non-dict schema is an empty
    object; a schema with no ``type`` is an object (the API requires it).
    """
    if not isinstance(schema, dict):
        schema = {"type": "object"}
    normalized = strip_nullable_unions(schema)
    if any(key in normalized for key in _BANNED_TOP_LEVEL):
        normalized = {k: v for k, v in normalized.items() if k not in _BANNED_TOP_LEVEL}
    normalized.setdefault("type", "object")
    if normalized.get("type") == "object" and not isinstance(normalized.get("properties"), dict):
        normalized = {**normalized, "properties": {}}
    return normalized


def build_tools(tools: list[dict[str, Any]]) -> tuple[list[dict], list[dict], ToolNames]:
    """``(manifest, native_tools, names)`` for this request's tools.

    The manifest (what the inert MCP server lists) and the native tool list
    (what ``CLAUDE_CODE_EXTRA_BODY`` advertises) carry the SAME normalized
    schema and description — they must never disagree.
    """
    names = ToolNames.build([str(t.get("name", "")) for t in tools or [] if t.get("name")])
    manifest: list[dict] = []
    native: list[dict] = []
    seen: set[str] = set()
    for tool in tools or []:
        name = str(tool.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        description = tool.get("description")
        description = description if isinstance(description, str) else ""
        schema = normalize_input_schema(tool.get("input_schema") or tool.get("parameters"))
        ident = names.forward[name]
        manifest.append({"name": ident, "description": description, "inputSchema": schema})
        native.append({"name": PREFIX + ident, "description": description, "input_schema": schema})
    return manifest, native, names


def request_body(native_tools: list[dict], *, effort: str = "") -> str:
    """The ``CLAUDE_CODE_EXTRA_BODY`` JSON (tools + optional effort)."""
    body: dict[str, Any] = {"tools": native_tools}
    if effort:
        body["output_config"] = {"effort": effort}
    return json.dumps(body, separators=(",", ":"), allow_nan=False)


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def projection(content: str | None, tool_calls: list[Any]) -> dict[str, Any]:
    """What an assistant turn LOOKS like to us: stripped text + calls.

    ``tool_calls`` are ``ToolCall`` objects or ``{"id","name","input"}``
    dicts (registry names, not native ones)."""
    calls = []
    for tc in tool_calls or []:
        if isinstance(tc, dict):
            calls.append({"id": tc.get("id"), "name": tc.get("name"), "input": tc.get("input")})
        else:
            calls.append({"id": tc.id, "name": tc.name, "input": tc.arguments})
    return {"content": (content or "").strip(), "tool_calls": calls}


def carrier(messages: list[dict], text: str, calls: list[dict]) -> dict[str, Any]:
    """The one ``raw_blocks`` entry a step writes."""
    return {
        "type": CARRIER,
        "version": 1,
        "messages": copy.deepcopy(messages),
        "projection": projection(text, calls),
    }


def replayable(message: Any) -> list[dict] | None:
    """The native messages to replay for an assistant turn, or None to rebuild.

    Exactly one carrier, version 1, and the turn's CURRENT projection equal
    to the carrier's — otherwise None (rebuild; never a stale signature)."""
    blocks = getattr(message, "raw_blocks", None) or []
    found = [b for b in blocks if isinstance(b, dict) and b.get("type") == CARRIER]
    if len(found) != 1:
        return None
    entry = found[0]
    natives = entry.get("messages")
    stored = entry.get("projection")
    if entry.get("version") != 1 or not isinstance(natives, list) or not natives:
        return None
    if not isinstance(stored, dict) or not all(isinstance(m, dict) for m in natives):
        return None
    expected = {
        "content": str(stored.get("content") or "").strip(),
        "tool_calls": list(stored.get("tool_calls") or []),
    }
    if projection(getattr(message, "content", ""), getattr(message, "tool_calls", [])) != expected:
        return None
    return natives


class _Ids:
    """tool_use ids, kept unique and API-safe across a REBUILT history.

    Older steps of this adapter used the id ``cli_0`` for every call, and
    other providers mint their own shapes; the API wants each ``tool_use`` id
    unique and ``[A-Za-z0-9_-]+``. A tool result refers to the id its own
    step assigned (the most recent assistant mapping of that id)."""

    def __init__(self) -> None:
        self.used: set[str] = set()
        self.current: dict[str, str] = {}
        self.n = 0

    def claim_native(self, ident: str) -> None:
        self.used.add(ident)
        self.current[ident] = ident

    def assign(self, original: str) -> str:
        original = str(original or "")
        if original and _SAFE.fullmatch(original) and original not in self.used:
            mapped = original
        else:
            self.n += 1
            mapped = f"toolu_ij{self.n}_{_hash8(original + '#' + str(self.n))}"
            while mapped in self.used:
                self.n += 1
                mapped = f"toolu_ij{self.n}_{_hash8(original + '#' + str(self.n))}"
        self.used.add(mapped)
        self.current[original] = mapped
        return mapped

    def result_for(self, original: str) -> str:
        original = str(original or "")
        if original in self.current:
            return self.current[original]
        return original if original and _SAFE.fullmatch(original) else "toolu_ij_orphan"


def _user_blocks(message: Any) -> list[dict]:
    blocks: list[dict] = []
    text = getattr(message, "content", "") or ""
    if text.strip():
        blocks.append({"type": "text", "text": text})
    for image in getattr(message, "images", None) or []:
        data = image.get("data_b64") if isinstance(image, dict) else None
        media = image.get("media_type") if isinstance(image, dict) else None
        if not isinstance(data, str) or not data or not isinstance(media, str) or not media:
            raise HistoryError("claude-cli: an attached image has no data or no media type")
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
    return blocks


def history_frames(system: str, messages: list[Any], names: ToolNames) -> tuple[str, list[dict]]:
    """``(system_prompt, frames)`` for the CLI.

    Raises :class:`HistoryError` when the transcript cannot be sent: it must
    END in a non-empty user message or tool result (the CLI cannot continue an
    assistant turn — no prefill, and no synthetic "continue" is invented)."""
    system_parts = [system] if (system or "").strip() else []
    frames: list[dict] = []
    ids = _Ids()
    for m in messages or []:
        role = getattr(m, "role", "")
        if role in ("system", "developer"):
            if (m.content or "").strip():
                system_parts.append(m.content)
            continue
        if role == "assistant":
            natives = replayable(m)
            if natives is not None:
                for native in natives:
                    for block in native.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            ids.claim_native(str(block.get("id", "")))
                    frames.append({"type": "assistant", "message": copy.deepcopy(native)})
                continue
            blocks: list[dict] = []
            if (m.content or "").strip():
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls or []:
                blocks.append({
                    "type": "tool_use",
                    "id": ids.assign(tc.id),
                    "name": names.native(tc.name),
                    "input": copy.deepcopy(tc.arguments if isinstance(tc.arguments, dict) else {}),
                })
            if not blocks:
                continue  # an empty assistant turn says nothing; the API refuses one
            frames.append({"type": "assistant", "message": {"role": "assistant", "content": blocks}})
            continue
        if role == "tool":
            blocks = [{
                "type": "tool_result",
                "tool_use_id": ids.result_for(m.tool_call_id),
                "content": m.content if isinstance(m.content, str) else str(m.content or ""),
            }]
        elif role == "user":
            blocks = _user_blocks(m)
        else:
            raise HistoryError(f"claude-cli: unsupported message role {role!r}")
        if frames and frames[-1]["type"] == "user":
            frames[-1]["message"]["content"].extend(blocks)
        elif blocks:
            frames.append({"type": "user", "message": {"role": "user", "content": blocks}})
    if not frames or frames[-1]["type"] != "user" or not frames[-1]["message"]["content"]:
        raise HistoryError(
            "claude-cli: the conversation must end with a non-empty user message or a "
            "tool result — the CLI cannot continue an assistant turn"
        )
    return "\n\n".join(system_parts), frames
