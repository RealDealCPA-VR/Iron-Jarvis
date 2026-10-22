"""What the Codex app-server is told, and how its answers read (pure, no I/O).

Verified against codex-cli 0.157.0 (tag ``rust-v0.157.0``) on 2026-10-21:
the schema from ``codex app-server generate-json-schema --experimental`` and
the source (``core/src/tools/spec_plan.rs``, ``core/src/tools/handlers/
dynamic.rs``, ``app-server/src/dynamic_tools.rs``, ``features/src/lib.rs``,
``core/src/config/mod.rs``), plus a live ``initialize`` + ``thread/start``
(no turn, so no model call) that answered ``environments: []`` and listed the
user's one MCP server as ``runtimeStatus: "disabled"`` with no tools.

THREE RULES this module keeps:

1. CODEX EXECUTES NOTHING. A thread is started with NO environment
   (``environments: []`` — ``exec_command``, ``write_stdin``, ``apply_patch``,
   ``view_image`` and ``request_permissions`` all require one) and with every
   other built-in that acts switched off by config (:data:`BUILTINS_OFF`).
   The user's MCP servers are disabled by name (:func:`thread_config`). What
   is left cannot act: ``exec``/``wait`` (code mode, only for a model whose
   catalog entry demands it) run raw JavaScript in a V8 isolate "no Node, no
   file system, no network access" whose only way out is a nested tool — and
   Iron Jarvis's tools are kept top-level (``direct_only_tool_namespaces``),
   so nothing is nested.

2. TOOL NAMES ARE MAPPED, PER THREAD, BOTH WAYS. Registry names may hold
   characters a function name cannot (``custom:<name>``) or exceed 64
   characters (``mcp__<server>__<tool>``); a name equal to one of Codex's own
   is renamed so it can never collide (code mode REMOVES a tool named
   ``exec``/``wait``). A call naming anything outside the map is refused.

3. HISTORY CROSSES AS TEXT, AND SAYS SO. A new thread has no memory of the
   conversation, so everything before the current ask is replayed as ONE
   labelled text item; the tool calls in it already ran and are marked so.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..base import ProviderError

PROVIDER = "codex-cli"
BINARY = "codex"

#: How the transport's own errors BEGIN (the provider prefix lets
#: ``failure_words`` name Codex; the ``kind`` stamped by :func:`typed` says
#: what happened).
TIMEOUT_PREFIX = f"{PROVIDER}: Codex timed out"
STOPPED_PREFIX = f"{PROVIDER}: the Codex app-server stopped"
INCOMPLETE_PREFIX = f"{PROVIDER}: incomplete answer"

#: Thread config overrides (``thread/start`` ``config``) that switch OFF every
#: Codex built-in tool that acts, each checked in the 0.157.0 source. With no
#: environment the shell / patch / image-view tools are already absent; the
#: switches below make that true even where an environment would exist.
BUILTINS_OFF: dict[str, Any] = {
    # exec_command + write_stdin (spec_plan.add_shell_tools).
    "features.shell_tool": False,
    "features.unified_exec": False,
    # view_image.
    "features.view_image": False,
    # image_gen.imagegen.
    "features.image_generation": False,
    # memories.* (ON in this PC's ~/.codex/config.toml).
    "features.memories": False,
    # get_goal / create_goal / update_goal.
    "features.goals": False,
    # ChatGPT apps (the codex_apps MCP server), plugins (their MCP servers and
    # skills) and plugin-install suggestions (tool_suggest needs all three).
    "features.apps": False,
    "features.plugins": False,
    "features.tool_suggest": False,
    # Browser and computer use.
    "features.browser_use": False,
    "features.computer_use": False,
    "features.in_app_browser": False,
    # The user's hooks run commands on events.
    "features.hooks": False,
    # clock.sleep.
    "features.sleep_tool": False,
    # Multi-agent v1 and v2 (collaboration.*). A model whose catalog says v2
    # gets it unless [agents] enabled = false (config.multi_agent_version_override).
    "features.multi_agent": False,
    "features.multi_agent_v2": False,
    "agents.enabled": False,
    # Skills that install MCP dependencies, and skill search.
    "features.skill_mcp_dependency_install": False,
    "features.skill_search": False,
    # Hosted web_search and the standalone web.run.
    "web_search": "disabled",
    # request_user_input (Plan mode) and update_plan.
    "tools.experimental_request_user_input.enabled": False,
    "tools.update_plan.enabled": False,
    # The notify program the user's Codex runs after each turn.
    "notify": [],
    # Code mode is never switched ON for a model that does not demand it; for
    # a code_mode_only model (gpt-5.6-*, gpt-6-*) Iron Jarvis's tools stay
    # TOP-LEVEL ("functions" is the namespace of a plain function tool), so
    # the isolate has nothing nested to call.
    "features.code_mode": {"enabled": False, "direct_only_tool_namespaces": ["functions"]},
    # No project AGENTS.md (there is no environment to read one from anyway)
    # and no entries in the user's Codex history file.
    "project_doc_max_bytes": 0,
    "history.persistence": "none",
}

#: The safety posture ``thread/start`` is asked for, and must ANSWER with
#: (checked by the session before any turn starts).
SANDBOX = "read-only"
APPROVAL_POLICY = "untrusted"

#: Codex's own tool names (0.157.0) and the code-mode pair. A registry tool
#: with one of these names is offered as ``ij_<name>``.
RESERVED_NAMES = frozenset({
    "exec", "wait", "exec_command", "write_stdin", "apply_patch", "view_image",
    "update_plan", "request_user_input", "request_user_input_async",
    "send_message_to_user_async", "tool_search", "web_search", "list_mcp_resources",
    "list_mcp_resource_templates", "read_mcp_resource", "spawn_agent", "send_input",
    "resume_agent", "wait_agent", "close_agent", "get_goal", "create_goal", "update_goal",
    "request_permissions", "get_context_remaining", "new_context", "wait_for_environment",
    "list_available_plugins_to_install", "request_plugin_install", "local_shell",
})

#: OpenAI's function-name limit.
MAX_NAME = 64
_SAFE = re.compile(r"[A-Za-z0-9_-]+")
_UNSAFE_CHAR = re.compile(r"[^A-Za-z0-9_-]")

#: Thread items that mean a Codex built-in RAN (it should never happen with
#: :data:`BUILTINS_OFF`; seeing one is logged and reported on the response).
BUILTIN_ITEM_TYPES = frozenset({
    "commandExecution", "fileChange", "mcpToolCall", "collabAgentToolCall",
    "webSearch", "imageView", "imageGeneration",
})

#: Server requests that ask the client to approve a Codex built-in. Each is
#: DENIED (the decision each request's schema takes). The two legacy v1
#: requests answer with ``ReviewDecision``, whose deny is a STRUCT variant in
#: 0.157.0 (``Denied { rejection }``, protocol/src/protocol.rs) — the bare
#: string ``"denied"`` does not deserialize as one.
_REJECTION = "Iron Jarvis runs every tool itself"
APPROVAL_DENIALS: dict[str, dict[str, Any]] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "execCommandApproval": {"decision": {"denied": {"rejection": _REJECTION}}},
    "applyPatchApproval": {"decision": {"denied": {"rejection": _REJECTION}}},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
}

REPLAY_HEADER = (
    "[Earlier in this conversation, replayed by Iron Jarvis as text because this is a new "
    "Codex session. Tool calls shown here already ran and their results are included; "
    "nothing below runs again.]"
)
CONTINUE_NOTE = (
    "Continue the task from where the conversation above stops: the latest tool results "
    "are at its end."
)


# --------------------------------------------------------------------------- #
# Tool names
# --------------------------------------------------------------------------- #
def _hash8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def safe_name(name: str) -> str:
    """The default Codex-side name for one registry name (no collision context)."""
    name = str(name or "")
    if name in RESERVED_NAMES:
        return f"ij_{name}"
    if name and _SAFE.fullmatch(name) and len(name) <= MAX_NAME:
        return name
    cleaned = _UNSAFE_CHAR.sub("_", name) or "tool"
    if cleaned in RESERVED_NAMES:
        cleaned = f"ij_{cleaned}"
    if len(cleaned) <= MAX_NAME:
        return cleaned
    return cleaned[: MAX_NAME - 9] + "_" + _hash8(name)


@dataclass
class ToolNames:
    """This thread's reversible registry-name <-> Codex-name map."""

    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, names: list[str]) -> "ToolNames":
        """Already-safe names claim themselves first, so the outcome never
        depends on the order of the tool list (``custom_x`` keeps its name and
        ``custom:x`` takes a hash suffix)."""
        out = cls()
        unique: list[str] = []
        for n in names:
            n = str(n)
            if n and n not in unique:
                unique.append(n)
        for n in unique:
            if n not in RESERVED_NAMES and _SAFE.fullmatch(n) and len(n) <= MAX_NAME:
                out._claim(n, n)
        for n in unique:
            if n in out.forward:
                continue
            candidate = safe_name(n)
            bump = 0
            while candidate in out.reverse:
                bump += 1
                candidate = candidate[: MAX_NAME - 9] + "_" + _hash8(f"{n}#{bump}")
            out._claim(n, candidate)
        return out

    def _claim(self, name: str, codex_name: str) -> None:
        self.forward[name] = codex_name
        self.reverse[codex_name] = name

    def codex(self, name: str) -> str:
        return self.forward.get(name) or safe_name(name)

    def ours(self, codex_name: str) -> str | None:
        """The registry name for a Codex-side name, or None (not offered)."""
        return self.reverse.get(str(codex_name or ""))


def _schema(tool: dict[str, Any]) -> dict[str, Any]:
    from ..claude_native.frames import normalize_input_schema

    return normalize_input_schema(tool.get("input_schema") or tool.get("parameters"))


def dynamic_tools(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], ToolNames]:
    """``(DynamicToolSpec list, names)`` for this thread's tools (plain
    ``function`` specs, never a namespace: a namespace needs the provider's
    namespace capability and code mode would nest it)."""
    names = ToolNames.build([str(t.get("name", "")) for t in tools or [] if t.get("name")])
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in tools or []:
        name = str(tool.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        description = tool.get("description")
        description = description if isinstance(description, str) and description.strip() else name
        specs.append({
            "type": "function",
            "name": names.forward[name],
            "description": description,
            "inputSchema": _schema(tool),
        })
    return specs, names


# --------------------------------------------------------------------------- #
# Thread + turn requests
# --------------------------------------------------------------------------- #
def thread_config(mcp_servers: list[str]) -> dict[str, Any]:
    """:data:`BUILTINS_OFF` plus every configured MCP server disabled BY NAME
    (a nested value, so a server name with a dot cannot break the path; the
    override layer is merged into the user's table, ``enabled`` only)."""
    config: dict[str, Any] = json.loads(json.dumps(BUILTINS_OFF))
    servers = {str(n): {"enabled": False} for n in mcp_servers if str(n).strip()}
    if servers:
        config["mcp_servers"] = servers
    return config


def thread_start_params(
    *,
    base_instructions: str,
    specs: list[dict[str, Any]],
    cwd: str,
    model: str | None,
    mcp_servers: list[str],
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "ephemeral": True,
        "cwd": cwd,
        "environments": [],
        "sandbox": SANDBOX,
        "approvalPolicy": APPROVAL_POLICY,
        "dynamicTools": specs,
        "config": thread_config(mcp_servers),
    }
    if base_instructions.strip():
        params["baseInstructions"] = base_instructions
    if model:
        params["model"] = model
    return params


def start_problem(result: Any) -> str:
    """'' when ``thread/start``'s answer confirms the posture we asked for,
    else the plain reason it does not (a version that ignored a field we rely
    on must never run a turn with Codex's own tools live)."""
    if not isinstance(result, dict):
        return "thread/start returned no thread"
    thread = result.get("thread")
    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
        return "thread/start returned no thread id"
    if thread.get("environments") != []:
        return "Codex did not confirm a thread with no environment (its own tools could run)"
    if thread.get("ephemeral") is not True:
        return "Codex did not confirm an ephemeral thread"
    sandbox = result.get("sandbox")
    if not isinstance(sandbox, dict) or sandbox.get("type") != "readOnly":
        return "Codex did not confirm a read-only sandbox"
    if result.get("approvalPolicy") != APPROVAL_POLICY:
        return "Codex did not confirm approvals on request"
    return ""


def mcp_problem(result: Any) -> str:
    """'' when no MCP server offers a tool on the thread, else the reason."""
    rows = (result or {}).get("data") if isinstance(result, dict) else None
    for row in rows or []:
        if isinstance(row, dict) and row.get("tools"):
            return f"the MCP server {str(row.get('name') or '?')[:60]!r} still offers tools"
    return ""


def turn_start_params(thread_id: str, items: list[dict[str, Any]], effort: str) -> dict[str, Any]:
    params: dict[str, Any] = {"threadId": thread_id, "input": items}
    if effort:
        params["effort"] = effort
    return params


class HistoryError(RuntimeError):
    """The transcript cannot be expressed as a turn (said in words)."""


def _args_text(args: Any) -> str:
    try:
        return json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return "{}"


def _render(messages: list[Any], names: ToolNames) -> str:
    parts: list[str] = []
    for m in messages:
        role = getattr(m, "role", "")
        content = getattr(m, "content", "") or ""
        if role == "user":
            text = f"User:\n{content}" if content.strip() else "User:"
            if getattr(m, "images", None):
                text += f"\n[{len(m.images)} image(s) were attached here; not replayed]"
            parts.append(text)
        elif role == "assistant":
            lines = [f"Assistant:\n{content}"] if content.strip() else ["Assistant:"]
            for tc in getattr(m, "tool_calls", None) or []:
                lines.append(
                    f"(called tool `{names.codex(tc.name)}` with arguments {_args_text(tc.arguments)})"
                )
            parts.append("\n".join(lines))
        elif role == "tool":
            label = names.codex(getattr(m, "name", "") or "") if getattr(m, "name", None) else "a tool"
            note = (f"\n[{len(m.images)} picture(s) were attached to this result; not replayed]"
                    if getattr(m, "images", None) else "")
            parts.append(f"Tool result from `{label}`:\n{content}{note}")
    return "\n\n".join(parts)


def _user_items(message: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    content = getattr(message, "content", "") or ""
    if content.strip():
        items.append({"type": "text", "text": content})
    for image in getattr(message, "images", None) or []:
        data = image.get("data_b64") if isinstance(image, dict) else None
        media = image.get("media_type") if isinstance(image, dict) else None
        if not isinstance(data, str) or not data or not isinstance(media, str) or not media:
            raise HistoryError(f"{PROVIDER}: an attached image has no data or no media type")
        items.append({"type": "image", "url": f"data:{media};base64,{data}"})
    return items


def turn_input(system: str, messages: list[Any], names: ToolNames) -> tuple[str, list[dict], bool]:
    """``(base_instructions, input items, replayed)`` for a NEW thread.

    The system prompt (and any ``system``/``developer`` messages) become the
    thread's base instructions. The trailing user message(s) are the turn's
    input; everything before them is replayed as ONE labelled text item
    (``replayed`` True). A conversation that ends in tool results (a resume
    that missed) gets :data:`CONTINUE_NOTE` as its ask."""
    system_parts = [system] if (system or "").strip() else []
    convo: list[Any] = []
    for m in messages or []:
        role = getattr(m, "role", "")
        if role in ("system", "developer"):
            if (getattr(m, "content", "") or "").strip():
                system_parts.append(m.content)
            continue
        if role not in ("user", "assistant", "tool"):
            raise HistoryError(f"{PROVIDER}: unsupported message role {role!r}")
        convo.append(m)
    k = len(convo)
    while k > 0 and getattr(convo[k - 1], "role", "") == "user":
        k -= 1
    prior, current = convo[:k], convo[k:]
    items: list[dict[str, Any]] = []
    if prior:
        items.append({"type": "text", "text": REPLAY_HEADER + "\n\n" + _render(prior, names)})
    asked = [item for m in current for item in _user_items(m)]
    if not asked:
        if not prior:
            raise HistoryError(f"{PROVIDER}: the conversation has nothing to ask")
        asked = [{"type": "text", "text": CONTINUE_NOTE}]
    items.extend(asked)
    return "\n\n".join(system_parts), items, bool(prior)


# --------------------------------------------------------------------------- #
# Resume: which calls a conversation is answering
# --------------------------------------------------------------------------- #
def answered_calls(messages: list[Any]) -> tuple[list[str], dict[str, str]] | None:
    """``(call ids, {call id: result text})`` when the conversation ENDS with
    the tool results of its last assistant turn's calls (every call answered,
    nothing after them), else None."""
    msgs = list(messages or [])
    i = len(msgs)
    results: dict[str, str] = {}
    while i > 0 and getattr(msgs[i - 1], "role", "") == "tool":
        i -= 1
    tail = msgs[i:]
    if not tail or i == 0:
        return None
    assistant = msgs[i - 1]
    if getattr(assistant, "role", "") != "assistant":
        return None
    ids = [str(tc.id) for tc in getattr(assistant, "tool_calls", None) or [] if getattr(tc, "id", "")]
    if not ids or len(set(ids)) != len(ids):
        return None
    for m in tail:
        cid = str(getattr(m, "tool_call_id", "") or "")
        content = getattr(m, "content", "")
        results[cid] = content if isinstance(content, str) else str(content or "")
    if set(results) != set(ids):
        return None
    return ids, results


def last_call_ids(messages: list[Any]) -> list[str]:
    """The call ids of the conversation's LAST assistant turn that asked for
    tools (anywhere before the end), or []: a parked session waiting on them
    can never be resumed once the conversation moved past them."""
    for m in reversed(list(messages or [])):
        if getattr(m, "role", "") == "assistant" and getattr(m, "tool_calls", None):
            return [str(tc.id) for tc in m.tool_calls if getattr(tc, "id", "")]
    return []


def tool_response(text: str, images: "list[dict[str, Any]] | None" = None) -> dict[str, Any]:
    """The ``DynamicToolCallResponse`` for one result. ``success`` stays True:
    the lane's words (a refusal, an error) are the result the model reads.

    v1.371.0: a picture the tool handed back (``page_screenshot``) rides the
    same response as an ``inputImage`` item — Codex 0.157.0's
    ``DynamicToolCallOutputContentItem`` (``codex app-server
    generate-json-schema``) takes ``{"type": "inputImage", "imageUrl": <data
    URL>}`` — so the model sees the picture, not a description of it."""
    items: list[dict[str, Any]] = [
        {"type": "inputText", "text": text if text else "(the tool returned nothing)"}
    ]
    for image in images or []:
        data = image.get("data_b64") if isinstance(image, dict) else None
        media = image.get("media_type") if isinstance(image, dict) else None
        if isinstance(data, str) and data and isinstance(media, str) and media.startswith("image/"):
            items.append({"type": "inputImage", "imageUrl": f"data:{media};base64,{data}"})
    return {"contentItems": items, "success": True}


def unseen_pictures_note(messages: list[Any]) -> str:
    """The system line for the TEXT-ONLY fallback (``codex exec``) when the
    user's messages carry pictures (v1.371.0 review). ``capabilities()`` now
    says vision for the native path, so the router keeps a picture on Codex —
    and when the app-server's feature check then fails, the fallback flattens
    the transcript to text and the pictures vanish without a word (a picture
    sent alone leaves NO user line at all). "" when there is none."""
    n = sum(
        len(getattr(m, "images", None) or [])
        for m in messages or []
        if getattr(m, "role", "") == "user"
    )
    if not n:
        return ""
    return (
        f"\n\n[The user attached {n} picture(s) to this conversation, but this Codex "
        "connection is text-only and cannot show them to you. Say so plainly; do not "
        "describe or guess what they show.]"
    )


def answered_images(messages: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """``{call id: pictures}`` for the trailing tool results (v1.371.0) —
    the pictures :func:`answered_calls` leaves out of its text map."""
    out: dict[str, list[dict[str, Any]]] = {}
    for m in reversed(list(messages or [])):
        if getattr(m, "role", "") != "tool":
            break
        images = list(getattr(m, "images", None) or [])
        if images:
            out[str(getattr(m, "tool_call_id", "") or "")] = images
    return out


# --------------------------------------------------------------------------- #
# Usage and rate limits
# --------------------------------------------------------------------------- #
_USAGE_KEYS = ("inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens",
               "reasoningOutputTokens", "totalTokens")


def _num(src: Any, key: str) -> int:
    value = src.get(key) if isinstance(src, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def usage_between(start: Any, end: Any) -> dict[str, Any]:
    """This call's usage: the thread's running TOTAL at the end minus at the
    start (Codex reports ``total`` cumulatively per thread). Codex's
    ``inputTokens`` already includes the cached part, so it is the TOTAL
    prompt (``LLMResponse.usage``'s contract); the cache buckets are parts.

    WHEN IT ARRIVES (measured on 0.157.0, and said in its source): Codex
    sends a round's token count only after that round's tools are answered,
    so the tokens of a round that asked for tools are reported during the
    NEXT adapter call. Each call reports exactly what Codex reported while it
    ran; a turn's total is exact (a call that only asked for tools may read
    0/0)."""
    def delta(key: str) -> int:
        return max(0, _num(end, key) - _num(start, key))

    usage: dict[str, Any] = {"input_tokens": delta("inputTokens"), "output_tokens": delta("outputTokens")}
    if isinstance(end, dict) and ("cachedInputTokens" in end or "cacheWriteInputTokens" in end):
        usage["cache_read_input_tokens"] = delta("cachedInputTokens")
        usage["cache_creation_input_tokens"] = delta("cacheWriteInputTokens")
    return usage


def _window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("usedPercent"), (int, float)):
        return None
    out: dict[str, Any] = {"usedPercent": int(raw["usedPercent"])}
    for key in ("windowDurationMins", "resetsAt"):
        value = raw.get(key)
        out[key] = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return out


def rate_limit_snapshot(raw: Any) -> dict[str, Any] | None:
    """Codex's account rate-limit snapshot (``RateLimitSnapshot``) kept in its
    own field names (Phase 5 reads ``planType`` and the windows), plus when
    Iron Jarvis saw it. None for anything that is not one."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {
        "planType": raw.get("planType") if isinstance(raw.get("planType"), str) else None,
        "limitId": raw.get("limitId") if isinstance(raw.get("limitId"), str) else None,
        "rateLimitReachedType": raw.get("rateLimitReachedType"),
        "primary": _window(raw.get("primary")),
        "secondary": _window(raw.get("secondary")),
        "observedAt": int(time.time()),
    }
    credits = raw.get("credits")
    if isinstance(credits, dict):
        out["credits"] = {k: credits.get(k) for k in ("hasCredits", "unlimited", "balance")}
    if isinstance(raw.get("spendControlReached"), bool):
        out["spendControlReached"] = raw["spendControlReached"]
    if out["primary"] is None and out["secondary"] is None and out["planType"] is None:
        return None
    return out


def limit_retry_after(snapshot: Any, now: float | None = None) -> float | None:
    """Seconds until the latest reset of a window the snapshot says is used up
    (or of every window when it says a limit was reached), else None."""
    if not isinstance(snapshot, dict):
        return None
    now = time.time() if now is None else now
    windows = [w for w in (snapshot.get("primary"), snapshot.get("secondary")) if isinstance(w, dict)]
    reached = bool(snapshot.get("rateLimitReachedType"))
    resets = [w["resetsAt"] for w in windows
              if isinstance(w.get("resetsAt"), int) and (reached or w.get("usedPercent", 0) >= 100)]
    if not resets:
        return None
    return max(0.0, float(max(resets)) - now)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
def typed(err: ProviderError, kind: str, *, partial: bool = False) -> ProviderError:
    """Stamp a transport error with what it IS (the router's refusal
    vocabulary, read by ``failure_words``): ``kind`` "timeout" |
    "interrupted", ``failed_provider``, ``partial``. Never raises."""
    try:
        err.kind = kind  # type: ignore[attr-defined]
        err.failed_provider = PROVIDER  # type: ignore[attr-defined]
        if partial:
            err.partial = True  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - wording only
        pass
    return err


def _error_info(error: Any) -> tuple[str, int | None]:
    """``(variant, http status)`` of a ``TurnError.codexErrorInfo``."""
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    if isinstance(info, str):
        return info, None
    if isinstance(info, dict) and len(info) == 1:
        (variant, body), = info.items()
        status = body.get("httpStatusCode") if isinstance(body, dict) else None
        return str(variant), status if isinstance(status, int) and not isinstance(status, bool) else None
    return "", None


def _said(error: Any) -> str:
    if not isinstance(error, dict):
        return ""
    words = str(error.get("message") or "").strip()
    more = error.get("additionalDetails")
    if isinstance(more, str) and more.strip() and more.strip() not in words:
        words = f"{words} ({more.strip()})" if words else more.strip()
    return words[:600]


def is_max_tokens(error: Any) -> bool:
    """Codex's words for an answer cut at the output limit ("Incomplete
    response returned, reason: max_output_tokens") — a truncated answer, not
    a failure."""
    return "max_output_tokens" in _said(error)


def signed_out_text(said: str) -> str:
    from ...cli_auth import SIGN_IN_FIX

    return f"{PROVIDER}: {SIGN_IN_FIX[BINARY]} (Codex said: {(said or 'unauthorized')[:120]})"


@dataclass
class TurnFailure:
    """What a failed turn means for the caller and for Iron-Proxy."""

    error: Exception
    #: The Iron-Proxy signal (``{"status", "text"}``) when this is ONE
    #: account's limit or refused sign-in, else None.
    signal: dict[str, Any] | None = None
    sign_in: bool = False


def turn_failure(
    error: Any, *, emitted: bool, rate_limits: Any = None, now: float | None = None
) -> TurnFailure:
    """The typed error for a turn Codex ended as ``failed``. Never an empty
    message. Statuses follow ``base.TRANSIENT_STATUS`` (429/5xx retry or fail
    over, 4xx are permanent); a usage or rate limit carries ``retry_after``
    from the snapshot's reset."""
    variant, status = _error_info(error)
    said = _said(error) or "no reason given"
    lowered = said.lower()
    if variant == "unauthorized" or status == 401 or (status == 403 and "auth" in lowered):
        return TurnFailure(
            ProviderError(signed_out_text(said), status_code=401),
            signal={"status": 401, "text": said},
            sign_in=True,
        )
    if variant in ("usageLimitExceeded", "rateLimitExceeded") or status == 429:
        what = "usage limit" if variant == "usageLimitExceeded" else "rate limit"
        return TurnFailure(
            ProviderError(
                f"{PROVIDER}: this ChatGPT account hit its Codex {what} — try again after it "
                f"resets. Codex said: {said}",
                status_code=429,
                retry_after=limit_retry_after(rate_limits, now),
            ),
            signal={"status": 429, "text": said},
        )
    if variant == "contextWindowExceeded":
        return TurnFailure(ProviderError(
            f"{PROVIDER}: the conversation exceeds the model's context window. Codex said: {said}",
            status_code=400, transient=False,
        ))
    if variant == "serverOverloaded" or variant == "internalServerError" or (
        status is not None and status >= 500
    ):
        code = status if status is not None else (503 if variant == "serverOverloaded" else 500)
        return TurnFailure(ProviderError(
            f"{PROVIDER}: OpenAI is busy or briefly unavailable (HTTP {code}) — try again shortly. "
            f"Codex said: {said}",
            status_code=code,
        ))
    if variant in ("httpConnectionFailed", "responseStreamConnectionFailed",
                   "responseStreamDisconnected", "responseTooManyFailedAttempts"):
        if status is not None and status != 200:
            return TurnFailure(ProviderError(
                f"{PROVIDER}: OpenAI refused the request (HTTP {status}). Codex said: {said}",
                status_code=status,
            ))
        if emitted:
            return TurnFailure(typed(ProviderError(f"{INCOMPLETE_PREFIX}: {said}"),
                                     "interrupted", partial=True))
        return TurnFailure(typed(ProviderError(
            f"{PROVIDER}: the connection to OpenAI dropped before an answer arrived — try again. "
            f"Codex said: {said}", transient=True), "interrupted"))
    if variant == "badRequest" or (status is not None and 400 <= status < 500):
        return TurnFailure(ProviderError(
            f"{PROVIDER}: OpenAI refused the request (HTTP {status or 400}). Codex said: {said}",
            status_code=status or 400,
        ))
    return TurnFailure(ProviderError(f"{PROVIDER}: Codex could not finish the answer. Codex said: {said}"))
