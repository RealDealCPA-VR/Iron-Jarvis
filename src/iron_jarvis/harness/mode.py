"""HARNESS MODE for a chat turn (v1.366.0, IJ harness Phase 3).

One explicit, keyword-only switch on the two chat lanes —
``run_chat_turn(..., harness=)`` and ``stream_chat_turn(..., harness=)`` /
``chat_stream(..., harness=)`` — never a ``ChatBody`` field, so no HTTP client
can turn it on. Omitted (``None``), every lane is byte-identical to before.
MIRROR NOTE (lock-step): both lanes call this module at the same seams.

In harness mode a turn:

* ARMS THE CODING TOOL SET (:data:`CODING_TOOLS`) — the Phase 1 tools plus
  the readers, the shell, the process tools, the web tools (not in a
  local-only chat) and the app-resource readers (only when an app is
  connected) — past the chat lanes' six-named-tool cap, ONLY here. Each tool
  keeps its own permission tier: an allow-tier tool is armed (granted, like
  any armed tool); an ask-tier tool (``shell``, ``process_start``,
  ``process_write``) is ask-armed in the stream lane (it cards unless the
  user chose auto-approve) and NOT armed in the POST lane, which can ask
  nobody (the documented asymmetry of the two lanes).
* SPEAKS THE MODEL FAMILY'S DIALECT (:mod:`.dialects`): the model is shown
  the tool names and shapes it was trained on; its calls are translated to the
  real tool BEFORE ``registry.invoke`` (:meth:`HarnessTurn.translate_calls`),
  so the armed-set gate, approvals, the ledger, undo, trust and Local only all
  see the REAL tool. The ledger row also records the name the model used
  (``ToolContext.called_as`` → ``ToolInvocation.called_as``); the result goes
  back to the model under the name it called.
* RENDERS THE COMPONENT TREE (:mod:`.components`) into the system prompt at
  the Tools seam, BEFORE the history planner runs (its cost is priced).
* NEVER ESCALATES: no hand-off exits are offered, the roster and the exit
  lines leave the prompt, a hallucinated ``escalate_to_agent`` is refused like
  any unarmed name, and the turn ends in chat at its last round
  (``stay_in_chat``). Default rounds: :data:`HARNESS_DEFAULT_ROUNDS`.
* NUDGES ONCE when a reply writes a tool call as TEXT
  (:func:`text_tool_call`) instead of calling the tool.
* CARRIES ITS FINGERPRINT on the route object: ``route.harness = {family,
  dialect, fingerprint, layers, nudged[, note]}`` — LAST, additive.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import fnmatch
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from . import components as _components
from . import dialects as _dialects

#: The tools a harness turn arms (real Jarvis names), in the order the model
#: sees the pass-through ones. The two MCP resource readers join only when a
#: connected app has registered tools; the web tools never in a local-only
#: chat.
CODING_TOOLS: tuple[str, ...] = (
    "read_file", "write_file", "edit_file", "multi_edit", "apply_patch",
    "glob", "grep", "list_files",
    "shell", "process_start", "process_read", "process_write", "process_stop",
    "plan_update", "notebook_edit", "view_image",
    "web_fetch", "web_search",
    "mcp_list_resources", "mcp_read_resource",
)
_MCP_RESOURCE_TOOLS = frozenset({"mcp_list_resources", "mcp_read_resource"})

#: A harness turn's tool rounds when the caller set none — the plan's Phase 4
#: decision (60), measured by the Phase 0 benchmark.
HARNESS_DEFAULT_ROUNDS = 60

#: The exit lines a harness turn is told in place of the chat's escalate /
#: workflow lines (``chat_turn._ENV_EXIT_LINES``).
HARNESS_EXIT_LINES = (
    "- Work through the task here, in this conversation, with your tools until it "
    "is done. There is no other agent to hand it to and no mode to switch."
)

#: The ONE corrective line a turn gets when its reply wrote a tool call as text.
TEXT_TOOL_CALL_NUDGE = "Your last reply wrote a tool call as text; call the tool itself."

#: Real tools whose successful call changed a file (plugin checks follow them).
EDIT_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "apply_patch", "notebook_edit"})


def turn_harness(value: Any) -> str | None:
    """The validated ``harness=`` value: ``None`` (off) for None / False;
    ``"auto"`` for True / "auto" (the family's own dialect); a dialect id
    ("claude", "gpt", "qwen", "generic") to force one. Anything else is a
    caller's mistake — ``ValueError`` before anything runs."""
    if value is None or value is False:
        return None
    if value is True:
        return "auto"
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "auto" or v in _dialects.DIALECTS:
            return v
    raise ValueError(
        "harness must be True, 'auto' or one of "
        + ", ".join(sorted(_dialects.DIALECTS))
        + f"; got {value!r}"
    )


# --------------------------------------------------------------------------- #
# the plan: which real tools, which model-facing tools — pure
# --------------------------------------------------------------------------- #


@dataclass
class ToolPlan:
    family: str
    dialect: _dialects.Dialect
    #: Real allow-tier tools: armed (granted) — the lanes' ``armed``.
    armed: list[str]
    #: Real ask-tier tools: the stream lane's ``ask_armed`` ([] in POST).
    ask: list[str]
    dialect_tools: list[_dialects.DialectTool]
    passthrough: list[str]
    #: Tools a folder rule denied (the lane narrows its overrides with them).
    denied: list[str] = field(default_factory=list)

    @property
    def offered_names(self) -> list[str]:
        return [t.name for t in self.dialect_tools] + list(self.passthrough)

    @property
    def gate(self) -> set[str]:
        """The real tools this turn may run — the registry's allowed_names."""
        return set(self.armed) | set(self.ask)


def _has_app_tools(registry: Any) -> bool:
    try:
        return any(str(n).startswith("mcp__") for n in registry.names())
    except Exception:  # noqa: BLE001
        return False


def plan_tools(
    registry: Any,
    permissions: Any,
    dialect: _dialects.Dialect,
    *,
    family: str,
    stream: bool,
    low_trust: bool = False,
    local_only: bool = False,
    tool_ceiling: "Iterable[str] | None" = None,
    extra: Iterable[str] = (),
    plugins: _components.Plugins = _components.Plugins(),
) -> ToolPlan:
    """The harness turn's tools. Every filter the lanes apply later (the
    ceiling, low trust, Local only) is applied here too, so what the system
    prompt names is exactly what the model can call; the lanes' own filters
    then find nothing more to drop."""
    from ..core.trust import LOW_TRUST_DENY
    from ..providers import egress

    have_apps = _has_app_tools(registry)
    candidates: list[str] = []
    for name in [*CODING_TOOLS, *extra]:
        if name in candidates or registry.get(name) is None:
            continue
        if name in _MCP_RESOURCE_TOOLS and not have_apps:
            continue
        candidates.append(name)
    if local_only:
        candidates = [n for n in candidates if n not in egress.INTERNET_TOOLS]
        if not stream:
            # The POST lane can ask nobody, so a tool that must ask in a
            # local-only turn is not armed there at all (lock-step with
            # run_chat_turn's own local-only rule).
            away = egress.ask_first_tools(registry, candidates)
            candidates = [n for n in candidates if n not in away]
    if low_trust:
        candidates = [n for n in candidates if n not in LOW_TRUST_DENY]
    if tool_ceiling is not None:
        ceiling = set(tool_ceiling)
        candidates = [n for n in candidates if n in ceiling]
    denied = [n for n in candidates if plugins.rule_for(n) == "deny"]
    candidates = [n for n in candidates if n not in denied]
    allow: list[str] = []
    ask: list[str] = []
    consented = set(extra) - set(CODING_TOOLS)
    for name in candidates:
        tool = registry.get(name)
        key = tool.perm_key() if tool is not None else name
        mode = getattr(permissions.mode_for(key, {}), "value", "ask")
        if mode == "deny":
            continue
        if plugins.rule_for(name) == "ask":
            ask.append(name)
        elif mode == "allow" or name in consented:
            # A connector the user toggled on for the chat is armed (granted)
            # exactly as the chat lanes arm it on every other turn.
            allow.append(name)
        else:
            ask.append(name)
    if not stream:
        ask = []
    shown, passthrough = _dialects.offered_tools(dialect, [*allow, *ask])
    reach = _dialects.reachable(shown, passthrough, [*allow, *ask])
    return ToolPlan(
        family=family,
        dialect=dialect,
        armed=[n for n in allow if n in reach],
        ask=[n for n in ask if n in reach],
        dialect_tools=shown,
        passthrough=passthrough,
        denied=denied,
    )


def _strip_store_as(spec: dict[str, Any]) -> dict[str, Any]:
    """A pass-through spec without ``_store_as`` (it needs the ``repl`` tool,
    which a harness turn does not arm)."""
    schema = spec.get("input_schema")
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or "_store_as" not in props:
        return spec
    new_props = {k: v for k, v in props.items() if k != "_store_as"}
    return {**spec, "input_schema": {**schema, "properties": new_props}}


def runtime_note(registry: Any, name: str) -> str:
    """The real tool's own description, as the runtime facts a dialect's
    command tool carries — minus the sentence that steers the model to
    Jarvis's own tool names (a dialect shows other names). "" when the tool
    is not registered."""
    tool = registry.get(name) if name else None
    text = str(getattr(tool, "description", "") or "").strip()
    if not text:
        return ""
    keep = [s for s in re.split(r"(?<=[.!?])\s+", text) if "Prefer the built-in tools" not in s]
    return " ".join(keep).strip()


def model_specs(registry: Any, plan: ToolPlan) -> list[dict[str, Any]]:
    """What the model is shown: the dialect's tools, then the pass-through
    real tools as the registry describes them."""
    out = []
    for tool in plan.dialect_tools:
        spec = tool.spec()
        note = runtime_note(registry, tool.runtime_from)
        if note:
            spec = {**spec, "description": spec["description"] + "\n\nHow commands run here: " + note}
        out.append(spec)
    if plan.passthrough:
        out += [_strip_store_as(s) for s in registry.specs(list(plan.passthrough))]
    return out


def dialect_section(plan: ToolPlan, *, ask_words: str = "") -> str:
    """The family layer's part of the tool guidance: the dialect's prompt
    with the offered names, and the ask sentence when any tool pauses."""
    names = plan.offered_names
    if not names:
        return "You have no tools in this turn: answer in text."
    text = plan.dialect.prompt.format(tools=", ".join(names))
    return (text + ("\n" + ask_words if ask_words else "")).strip()


def ask_sentence(plan: ToolPlan, *, approval_mode: str, local_only: bool, registry: Any) -> str:
    """Which shown tools pause for the user (stream lane only)."""
    if not plan.ask:
        return ""
    asking = set(plan.ask)
    shown = [t.name for t in plan.dialect_tools if t.targets and set(t.targets) & asking]
    shown += [n for n in plan.passthrough if n in asking]
    if not shown:
        return ""
    if approval_mode == "yolo" and not local_only:
        return (
            "The user chose auto-approve, so " + ", ".join(shown)
            + " run without pausing."
        )
    return (
        "Calls to " + ", ".join(shown) + " pause while the user approves them; if they "
        "decline, do not repeat the same call."
    )


# --------------------------------------------------------------------------- #
# text tool calls (D)
# --------------------------------------------------------------------------- #

_TAG_CALL_RX = re.compile(r"<\s*tool_call\s*>|<\s*function_calls\s*>|<\s*function\s*=", re.IGNORECASE)
_INVOKE_RX = re.compile(r"<\s*invoke\s+name\s*=\s*[\"']?([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_JSON_CALL_RX = re.compile(
    r"\"(?:name|tool|tool_name|function)\"\s*:\s*\"([A-Za-z0-9_.\-]+)\"\s*,\s*"
    r"\"(?:arguments|parameters|input|args)\"\s*:",
)


def text_tool_call(text: str, offered: Iterable[str]) -> str:
    """The kind of tool call a reply WROTE AS TEXT, or "" for none:
    ``tag`` — GLM / Qwen / Hermes ``<tool_call>…</tool_call>`` (and
    ``<function_calls>`` / ``<function=…>``); ``invoke`` — a Claude-style
    ``<invoke name="…">``; ``json`` — a ``{"name": …, "arguments": …}`` blob
    naming a tool this turn offered. Never raises."""
    try:
        body = str(text or "")
        if not body.strip():
            return ""
        if _TAG_CALL_RX.search(body):
            return "tag"
        if _INVOKE_RX.search(body):
            return "invoke"
        names = set(offered)
        for m in _JSON_CALL_RX.finditer(body):
            if m.group(1) in names:
                return "json"
    except Exception:  # noqa: BLE001
        return ""
    return ""


# --------------------------------------------------------------------------- #
# the per-turn object the lanes hold
# --------------------------------------------------------------------------- #


class HarnessTurn:
    """Everything a harness-mode turn needs, built once before the planner."""

    def __init__(
        self,
        *,
        requested: str,
        plan: ToolPlan,
        layer: _components.ProjectLayer,
        tree: _components.ComponentTree,
        rendered: str,
        today: str = "",
    ) -> None:
        self.requested = requested
        self.plan = plan
        self.layer = layer
        self.tree = tree
        self.rendered = rendered
        self.fingerprint = _components.fingerprint(rendered, plan.dialect.id)
        self.today = today
        self.nudged = False
        self.text_call_kind = ""
        #: call id -> the name the model used, for calls that were translated
        self._called_as: dict[str, str] = {}
        #: call id -> the dialect tool the model called (for its own words)
        self._called_tool: dict[str, _dialects.DialectTool] = {}

    # -- the prompt --------------------------------------------------------- #

    def block(self) -> str:
        """The system-prompt section (with today's date, which is NOT part of
        the fingerprint)."""
        date = f"\n\nToday's date is {self.today}." if self.today else ""
        return "\n\n" + self.rendered + date

    @staticmethod
    def system_without_exits(system: str, exit_lines: str) -> str:
        """The prompt with the chat's escalate / workflow lines swapped for
        :data:`HARNESS_EXIT_LINES` (a harness turn never escalates)."""
        return system.replace(exit_lines, HARNESS_EXIT_LINES, 1)

    # -- tools -------------------------------------------------------------- #

    def specs(self, registry: Any) -> list[dict[str, Any]]:
        return model_specs(registry, self.plan)

    def translate_calls(self, calls: list[Any]) -> list[Any]:
        """The model's calls mapped to the real tools (same ids). A name the
        dialect does not know passes through untouched — the registry decides
        it (``unknown tool`` / ``not armed``, ledgered)."""
        from ..providers.adapters.base import ToolCall

        out: list[Any] = []
        armed = self.plan.gate
        for call in calls or []:
            t = _dialects.translate(self.plan.dialect, call.name, call.arguments, armed)
            if t.real != call.name or t.args is not call.arguments:
                self._called_as[call.id] = call.name
                tool = self.plan.dialect.lookup(call.name)
                if tool is not None:
                    self._called_tool[call.id] = tool
                out.append(ToolCall(id=call.id, name=t.real, arguments=t.args))
            else:
                out.append(call)
        return out

    def called_as(self, call: Any) -> str:
        """The name the model used for ``call`` (its real name when nothing
        translated)."""
        return self._called_as.get(getattr(call, "id", ""), getattr(call, "name", ""))

    def ctx_for(self, ctx: Any, call: Any) -> Any:
        """``ctx`` carrying the model's name for a translated call, so the
        ledger row records it (``ToolInvocation.called_as``)."""
        name = self._called_as.get(getattr(call, "id", ""))
        if not name or name == getattr(call, "name", "") or ctx is None:
            return ctx
        try:
            return dataclasses.replace(ctx, called_as=name)
        except Exception:  # noqa: BLE001 — a test double that is no dataclass
            return ctx

    def model_text(self, call: Any, content: str) -> str:
        """The result as the model reads it. A required-argument refusal for a
        translated call is reworded in the dialect's own names (the registry's
        words name the REAL tool's arguments, which the model never saw)."""
        tool = self._called_tool.get(getattr(call, "id", ""))
        text = str(content)
        if tool is None or not text.startswith("missing required"):
            return text
        required = list((tool.input_schema or {}).get("required") or [])
        return (
            f"{text}\n(You called {tool.name}; it needs "
            + ", ".join(required) + ".)"
        )

    def narrow(self, overrides: dict[str, str], registry: Any, permissions: Any) -> None:
        """A folder's rules written into the lane's permission overrides —
        the EXISTING mechanism, so the engine and the card predicates read
        them like any other override. ``ask`` makes an allow-tier tool ask
        (the stream lane cards it; auto-approve still grants it one call at a
        time, as for every ask-tier tool); ``deny`` refuses (the tool is not
        offered either). Only ever NARROWS: ``deny`` always lands, and ``ask``
        never lands on a key that is already denied (an ``ask`` override over
        a denied base policy would RAISE it). In place, like the low-trust
        narrowing."""
        rules = list(self.tree.plugins.rules) if self.layer.used else []
        for name, action in rules:
            tool = registry.get(name)
            for key in sorted({name, tool.perm_key() if tool is not None else name}):
                if action == "deny":
                    overrides[key] = "deny"
                    continue
                current = getattr(permissions.mode_for(key, overrides), "value", "ask")
                if current != "deny":
                    overrides[key] = "ask"

    # -- text tool calls ---------------------------------------------------- #

    def wants_nudge(self, reply: str, has_calls: bool, round_index: int, rounds: int) -> bool:
        """True ONCE per turn: the reply called no tool, wrote one as text, and
        a round is left to call it for real."""
        if self.nudged or has_calls or round_index >= rounds - 1:
            return False
        names = set(self.plan.offered_names) | self.plan.gate
        kind = text_tool_call(reply, names)
        if not kind:
            return False
        self.nudged = True
        self.text_call_kind = kind
        return True

    # -- plugin checks ------------------------------------------------------ #

    def checks_for(self, real_name: str, changed: Iterable[str]) -> list[_components.Check]:
        """The folder's checks that follow a successful edit of ``changed``."""
        if real_name not in EDIT_TOOLS or not self.tree.plugins.checks:
            return []
        paths = [str(p).replace("\\", "/") for p in changed if p]
        if not paths:
            return []
        out = []
        for check in self.tree.plugins.checks:
            if not check.files or any(
                fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(p.rsplit("/", 1)[-1], pat)
                for p in paths for pat in check.files
            ):
                out.append(check)
        return out

    async def after_edit(
        self,
        real_name: str,
        changed: Iterable[str],
        run_check: "Callable[[str, int], Awaitable[tuple[str, str]]]",
        *,
        may_run: bool,
        why_not: str = "",
    ) -> str:
        """The text appended to an edit's result: each matching check's
        outcome. ``run_check(command, timeout_s)`` is the lane's ``shell`` call
        through the registry, answering ``(status, text)`` — status ``ran`` /
        ``failed`` / ``refused``. ``may_run`` False (low trust, Local only)
        runs nothing and says so."""
        checks = self.checks_for(real_name, changed)
        if not checks:
            return ""
        parts: list[str] = []
        for check in checks:
            if not may_run:
                parts.append(
                    f"Check not run ({why_not or 'not allowed in this turn'}): run `{check.command}` "
                    "yourself if it is needed."
                )
                continue
            status, text = await run_check(check.command, check.timeout_s)
            if status == "refused":
                parts.append(
                    f"Check not run (running it needs the user's approval): run `{check.command}` "
                    "yourself with your command tool."
                )
            else:
                parts.append(f"Check `{check.command}` ({'passed' if status == 'ran' else 'failed'}):\n{text}")
        return "\n\n" + "\n\n".join(parts)

    # -- the receipt -------------------------------------------------------- #

    def route_info(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "family": self.plan.family,
            "dialect": self.plan.dialect.id,
            "fingerprint": self.fingerprint,
            "layers": list(self.tree.layers),
            "nudged": bool(self.nudged),
        }
        if self.layer.note:
            out["note"] = self.layer.note
        return out


def build(
    *,
    registry: Any,
    permissions: Any,
    requested: str,
    provider: str,
    model: str,
    stream: bool,
    layer: _components.ProjectLayer = _components.EMPTY_LAYER,
    rule_files: Iterable[str] = (),
    low_trust: bool = False,
    local_only: bool = False,
    tool_ceiling: "Iterable[str] | None" = None,
    extra: Iterable[str] = (),
    approval_mode: str = "",
    today: "str | None" = None,
) -> HarnessTurn:
    """Plan the tools, compose the tree and render it. Pure apart from the
    registry / permission reads (no I/O): the project layer is read by the
    caller, off the loop (:func:`components.read_project_layer`)."""
    family, dialect = _dialects.resolve(requested, provider, model)
    plugins = layer.plugins if layer.used else _components.Plugins()
    plan = plan_tools(
        registry, permissions, dialect, family=family, stream=stream,
        low_trust=low_trust, local_only=local_only, tool_ceiling=tool_ceiling,
        extra=extra, plugins=plugins,
    )
    section = dialect_section(plan, ask_words=ask_sentence(
        plan, approval_mode=approval_mode, local_only=local_only, registry=registry,
    ) if stream else "")
    tree = _components.compose(
        dialect_section=section, dialect_id=dialect.id, project=layer, rule_files=rule_files,
    )
    rendered = _components.render(tree)
    if today is None:
        today = _dt.date.today().isoformat()
    return HarnessTurn(
        requested=requested, plan=plan, layer=layer, tree=tree, rendered=rendered, today=today,
    )


def dumps_specs(specs: list[dict[str, Any]]) -> str:
    """The specs as canonical JSON (the bench's identity hashes it)."""
    return json.dumps(specs, sort_keys=True, default=str)
