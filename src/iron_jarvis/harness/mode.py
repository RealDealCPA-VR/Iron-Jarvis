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
    # v1.371.0: look at the user's dev server (harness/page_shot).
    "page_screenshot",
    # v1.376.0 (part 2, Phase 5): the agent's own browser (harness/page_act,
    # harness/page_browser) — open the dev server, click, type, read the
    # console and the failed requests.
    "page_open", "page_click", "page_type", "page_read",
    # v1.372.0 (part 2, Phase 1): helper agents and the user's skill library
    # (harness/helpers). Helpers inherit the session's folder, model, account,
    # Local only, trust and approval posture; at most 3 run at once.
    "delegate", "spawn_agent", "list_agents",
    "skill_search", "skill_load",
)
_MCP_RESOURCE_TOOLS = frozenset({"mcp_list_resources", "mcp_read_resource"})
#: v1.372.0: the HELPER DOORS. Starting a helper changes nothing by itself:
#: the helper works with THIS turn's tools, folder, model, account, Local only,
#: trust and approval posture (``harness/helpers``), and every ask-tier step it
#: takes still asks, on its own row. So a harness turn arms them GRANTED in
#: every posture — Claude Code's Agent/Task and Codex's spawn_agent need no
#: approval either; a card per helper was a click per helper (the live check
#: parked a "search with two helpers" turn on the first one). The user's
#: permission table can still deny them, and a folder rule "ask" keeps a card.
#: STREAM LANE ONLY: the POST lane can show no card, so a helper started there
#: could park on its own ask with nobody to answer; it keeps the plain tier.
HELPER_DOORS = frozenset({"delegate", "spawn_agent"})

#: v1.367.0 (Phase 4): a Build session in PLAN mode arms ONLY these — tools
#: that change nothing on this PC (they read files, search, look at an image
#: or the web, read the output of a program the session already started, or
#: keep the step list) — plus ``exit_plan_mode`` (the stream lane: the user
#: approves the plan on its card) and ``ask_user`` (a client that draws the
#: cards). No editor, no command, no app tool: those wait for the user's
#: approval of the plan.
PLAN_MODE_TOOLS: tuple[str, ...] = (
    "read_file", "glob", "grep", "list_files",
    "process_read", "plan_update", "view_image",
    "web_fetch", "web_search",
    "mcp_list_resources", "mcp_read_resource",
    # v1.371.0: looking at a page changes nothing on this PC.
    "page_screenshot",
    # v1.376.0: opening and reading a page in the agent's browser look; a
    # click or typing acts, so those wait for the plan's approval.
    "page_open", "page_read",
    # v1.372.0: a skill is text to read. No helper in plan mode: a helper can
    # change files, and nothing may change before the plan is approved.
    "skill_search", "skill_load",
)
#: The plan-mode door and the question tool (harness/session_tools).
EXIT_PLAN_TOOL = "exit_plan_mode"
ASK_USER_TOOL = "ask_user"
#: Tools the stream lane cards ONCE-ONLY in every posture (auto-approve
#: included): Allow once / Deny, no "for this chat", no "Always", and no
#: grant of any width skips the card — the v1.350.0 approve_plan rules. The
#: plan is the user's to approve, every time.
ONCE_ONLY_TOOLS: frozenset[str] = frozenset({EXIT_PLAN_TOOL})

#: A harness turn's tool rounds when the caller set none — the plan's Phase 4
#: decision (60), measured by the Phase 0 benchmark. (The benchmark and any
#: harness caller with no budget of its own; a BUILD reply has its own rule,
#: :data:`BUILD_SAFETY_ROUNDS`.)
HARNESS_DEFAULT_ROUNDS = 60

#: THE SAFETY STOP of a Build reply with NO step limit (v1.384.0, the
#: default since the user's 117-minute reply hit 60 rounds and ended on
#: "nothing is built yet"). Like Claude Code, a Build reply works until the
#: model ends its turn or the user presses Stop; this is only the last guard
#: against a model stuck in a loop the repeated-call breaker cannot see (one
#: that reads a different file every round). 1,000 is the agent run's own
#: step ceiling (`max_steps`, 1..1000), ~16x the reply that started this
#: (60 rounds, 117 minutes): at the 2-6 minutes a round took there it is
#: 1.5 to 4 DAYS of work, so no real task meets it, while it still bounds
#: what a runaway loop can spend of the user's plan (the in-turn transcript is
#: fitted to the model's window every round, so memory and context stay
#: bounded meanwhile). ``chat_turn.MAX_TURN_ROUNDS`` equals it (pinned).
BUILD_SAFETY_ROUNDS = 1000

#: What a Build reply cut at its step limit is told to write instead of
#: another tool call (v1.384.0). The old office wording ("write your final
#: answer") made a reply that had only read and searched say "Nothing is
#: built yet" — true, and no way to carry on. The reply line gets a one-press
#: "Keep going" (the screen's), so the model writes a progress report, never
#: a verdict. ``{limit}`` is the number of steps.
OUT_OF_STEPS_BUILD_INSTRUCTION = (
    "You have reached this reply's step limit ({limit} steps), so no more tools can run "
    "in this reply. The user can press Keep going to let you carry on from exactly here. "
    "Write a short progress report now, from the results above: start with one sentence "
    "saying you reached the step limit; then what you have done so far (the files you "
    "changed and where, the commands you ran and what they showed, what you found out); "
    "then the next steps you will take when the user says keep going. Never say that "
    "nothing was done: if you only read and searched, say what you read and what you "
    "learned from it."
)

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
    plan_mode: bool = False,
    ask_user: bool = False,
) -> ToolPlan:
    """The harness turn's tools. Every filter the lanes apply later (the
    ceiling, low trust, Local only) is applied here too, so what the system
    prompt names is exactly what the model can call; the lanes' own filters
    then find nothing more to drop.

    ``plan_mode`` (v1.367.0): only :data:`PLAN_MODE_TOOLS` (no app tool from
    ``extra`` — an app's tool may change things), plus ``exit_plan_mode`` in
    the stream lane. ``ask_user``: the stream lane's client draws the
    question card, so ``ask_user`` joins. The POST lane (``stream`` False)
    gets neither: it can ask nobody."""
    from ..core.trust import LOW_TRUST_DENY
    from ..providers import egress

    have_apps = _has_app_tools(registry)
    candidates: list[str] = []
    base: list[str] = list(PLAN_MODE_TOOLS if plan_mode else CODING_TOOLS)
    if plan_mode and stream:
        base.append(EXIT_PLAN_TOOL)
    if ask_user and stream:
        base.append(ASK_USER_TOOL)
    if plan_mode:
        extra = ()
    for name in [*base, *extra]:
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
    consented = set(extra) - set(CODING_TOOLS) - set(base)
    for name in candidates:
        tool = registry.get(name)
        key = tool.perm_key() if tool is not None else name
        mode = getattr(permissions.mode_for(key, {}), "value", "ask")
        if mode == "deny":
            continue
        if plugins.rule_for(name) == "ask":
            ask.append(name)
        elif mode == "allow" or name in consented or (stream and name in HELPER_DOORS):
            # A connector the user toggled on for the chat is armed (granted)
            # exactly as the chat lanes arm it on every other turn; a helper
            # door is granted for the reason at HELPER_DOORS.
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


def runtime_note(registry: Any, name: str, *, shell_description: str = "") -> str:
    """The real tool's own description, as the runtime facts a dialect's
    command tool carries — minus the sentence that steers the model to
    Jarvis's own tool names (a dialect shows other names). "" when the tool
    is not registered. ``shell_description`` (v1.377.0): a BUILD SESSION's
    shell runs with the user's own PATH (``harness/command_env``), so its
    facts are those words, not the registered (scrubbed) ones."""
    tool = registry.get(name) if name else None
    if tool is None:
        return ""
    text = str(getattr(tool, "description", "") or "").strip()
    if shell_description and name == "shell":
        text = shell_description.strip()
    if not text:
        return ""
    keep = [s for s in re.split(r"(?<=[.!?])\s+", text) if "Prefer the built-in tools" not in s]
    return " ".join(keep).strip()


def model_specs(registry: Any, plan: ToolPlan, *, shell_description: str = "") -> list[dict[str, Any]]:
    """What the model is shown: the dialect's tools, then the pass-through
    real tools as the registry describes them. ``shell_description``
    (v1.377.0, a Build session): the shell's facts there — the user's own
    PATH and the tools found on it — for every dialect's command tool AND the
    pass-through ``shell``; "" = the registered words, exactly as before."""
    out = []
    for tool in plan.dialect_tools:
        spec = tool.spec()
        note = runtime_note(registry, tool.runtime_from, shell_description=shell_description)
        if note:
            spec = {**spec, "description": spec["description"] + "\n\nHow commands run here: " + note}
        out.append(spec)
    if plan.passthrough:
        for s in registry.specs(list(plan.passthrough)):
            if shell_description and s.get("name") == "shell":
                s = {**s, "description": shell_description}
            out.append(_strip_store_as(s))
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
        # v1.367.0: a once-only tool (the plan door) waits for the user even
        # under auto-approve — the sentence must not promise otherwise.
        once = set(ONCE_ONLY_TOOLS)
        always = [t.name for t in plan.dialect_tools if t.targets and t.targets[0] in once and t.name in shown]
        always += [n for n in plan.passthrough if n in once and n in shown]
        free = [n for n in shown if n not in always]
        parts = []
        if free:
            parts.append("The user chose auto-approve, so " + ", ".join(free) + " run without pausing.")
        if always:
            parts.append(", ".join(always) + " always waits for the user's approval.")
        return " ".join(parts)
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
        #: v1.367.0: a Build session's plan-mode turn (set by :func:`build`).
        self.plan_mode = False
        self.nudged = False
        self.text_call_kind = ""
        #: call id -> the name the model used, for calls that were translated
        self._called_as: dict[str, str] = {}
        #: call id -> the dialect tool the model called (for its own words)
        self._called_tool: dict[str, _dialects.DialectTool] = {}
        #: v1.377.0: a Build session's shell facts (the user's own PATH and
        #: the tools found on it — ``harness/command_env``); "" elsewhere.
        self.shell_description = ""

    # -- the prompt --------------------------------------------------------- #

    def block(self) -> str:
        """The system-prompt section (with today's date, which is NOT part of
        the fingerprint)."""
        date = f"\n\nToday's date is {self.today}." if self.today else ""
        # v1.371.0: the page the user's Browser pane shows (harness/preview),
        # named only when page_screenshot is in this turn's gate. Like the
        # date, it changes per turn and is NOT part of the fingerprint.
        from .preview import prompt_line
        # v1.373.0 (part 2, Phase 2): a skill from the session's own folder,
        # picked with "/" for this message (harness/picked_skill) — its
        # playbook, in the chat's "/skill" words. Per message, so it is NOT
        # part of the fingerprint either.
        from .picked_skill import prompt_section
        # v1.374.0 (part 2, Phase 3): the summary standing in for the
        # session's older turns (harness/compact — Compact), which the turn's
        # history then leaves out. Per message, so not in the fingerprint.
        from .compact import prompt_section as summary_section
        # v1.375.0 (part 2, Phase 4): the session's own git branch and
        # worktree (harness/branches) — "" for a session that works in its
        # folder directly, so its prompt is exactly as before.
        from .branches import prompt_line as branch_line

        return (
            "\n\n" + self.rendered + date + prompt_line(self.plan.gate) + prompt_section()
            + summary_section() + branch_line()
        )

    @staticmethod
    def system_without_exits(system: str, exit_lines: str) -> str:
        """The prompt with the chat's escalate / workflow lines swapped for
        :data:`HARNESS_EXIT_LINES` (a harness turn never escalates)."""
        return system.replace(exit_lines, HARNESS_EXIT_LINES, 1)

    # -- tools -------------------------------------------------------------- #

    def specs(self, registry: Any) -> list[dict[str, Any]]:
        return model_specs(registry, self.plan, shell_description=self.shell_description)

    def translate_calls(self, calls: list[Any]) -> list[Any]:
        """The model's calls mapped to the real tools (same ids). A name the
        dialect does not know passes through untouched — the registry decides
        it (``unknown tool`` / ``not armed``, ledgered)."""
        from ..providers.adapters.base import ToolCall

        out: list[Any] = []
        armed = self.plan.gate
        for call in calls or []:
            t = _dialects.translate(self.plan.dialect, call.name, call.arguments, armed)
            folder_skill = self._folder_skill(t)
            if folder_skill is not None:
                # v1.372.0: a SKILL OF THIS FOLDER (`.ironjarvis/harness/
                # skills/<name>`) asked for by name (Claude Code's `Skill`,
                # or skill_load) is read from its SKILL.md — the library
                # loader knows only the user's skill library.
                t = _dialects.Translated(real="read_file", args={"path": folder_skill},
                                         called_as=call.name)
            if t.real != call.name or t.args is not call.arguments:
                self._called_as[call.id] = call.name
                tool = self.plan.dialect.lookup(call.name)
                if tool is not None:
                    self._called_tool[call.id] = tool
                out.append(ToolCall(id=call.id, name=t.real, arguments=t.args))
            else:
                out.append(call)
        return out

    def _folder_skill(self, t: "_dialects.Translated") -> "str | None":
        """The SKILL.md path of the folder skill a ``skill_load`` call names,
        when the folder's layer lists one by that name and ``read_file`` is
        in the turn's gate; else None (the library loader answers)."""
        if t.real != "skill_load" or "read_file" not in self.plan.gate:
            return None
        name = str((t.args or {}).get("name") or "").strip().lower()
        if not name or not self.layer.used:
            return None
        for skill in self.tree.skills:
            if str(getattr(skill, "name", "")).lower() == name:
                return str(getattr(skill, "rel", "")) or None
        return None

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
        a round is left to call it for real. Never on a turn that offered no
        tool (a text-only model): "call the tool itself" would name a tool it
        does not have, and the extra round could only repeat the answer."""
        if self.nudged or has_calls or round_index >= rounds - 1 or not self.plan.offered_names:
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
        outcome. ``run_check(command, timeout_s)`` is the lane's command call
        through the registry (``shell``, or ``process_start`` under the GPT
        dialect — ``chat_turn.check_carrier``, v1.367.0), answering
        ``(status, text)`` — status ``ran`` /
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
            if status == "refused" and not {"shell", "process_start"} & self.plan.gate:
                # The POST lane (and a ceiling without either) gives the model
                # no command tool: "run it yourself" would send it to a tool it
                # does not have (review of v1.366.0). Codex's dialect reaches
                # process_start, not the shell, so it keeps the sentence below.
                parts.append(
                    f"Check not run (this turn cannot run commands): `{check.command}` checks this "
                    "edit; tell the user to run it."
                )
            elif status == "refused":
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
        if self.plan_mode:
            # v1.367.0 (additive, only in plan mode): the turn ran read-only.
            out["mode"] = "plan"
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
    plan_mode: bool = False,
    ask_user: bool = False,
    library: Iterable[str] = (),
    shell_description: str = "",
) -> HarnessTurn:
    """Plan the tools, compose the tree and render it. Pure apart from the
    registry / permission reads (no I/O): the project layer is read by the
    caller, off the loop (:func:`components.read_project_layer`).

    ``plan_mode`` / ``ask_user`` (v1.367.0, a Build session's turn): see
    :func:`plan_tools`; plan mode also renders the tree's MODE component
    ("plan only: propose, do not change files"), naming the tools in the
    model's own dialect."""
    family, dialect = _dialects.resolve(requested, provider, model)
    plugins = layer.plugins if layer.used else _components.Plugins()
    plan = plan_tools(
        registry, permissions, dialect, family=family, stream=stream,
        low_trust=low_trust, local_only=local_only, tool_ceiling=tool_ceiling,
        extra=extra, plugins=plugins, plan_mode=plan_mode, ask_user=ask_user,
    )
    section = dialect_section(plan, ask_words=ask_sentence(
        plan, approval_mode=approval_mode, local_only=local_only, registry=registry,
    ) if stream else "")
    mode_text = ""
    if plan_mode:
        mode_text = _components.plan_mode_text(
            exit_name=_shown_name(plan, EXIT_PLAN_TOOL),
            plan_name=_shown_name(plan, "plan_update"),
        )
    tree = _components.compose(
        dialect_section=section, dialect_id=dialect.id, project=layer, rule_files=rule_files,
        mode=mode_text, library=library,
    )
    rendered = _components.render(tree)
    if today is None:
        today = _dt.date.today().isoformat()
    turn = HarnessTurn(
        requested=requested, plan=plan, layer=layer, tree=tree, rendered=rendered, today=today,
    )
    turn.plan_mode = bool(plan_mode)
    # v1.377.0: a Build session's commands run with the user's own PATH; the
    # caller worked out the words off the loop (``harness/command_env``).
    turn.shell_description = str(shell_description or "")
    return turn


def _shown_name(plan: ToolPlan, real: str) -> str:
    """The name the model is SHOWN for the real tool ``real`` this turn
    (its dialect's, or its own), or "" when the turn does not offer it."""
    for tool in plan.dialect_tools:
        if tool.targets and tool.targets[0] == real:
            return tool.name
    return real if real in plan.passthrough else ""


def dumps_specs(specs: list[dict[str, Any]]) -> str:
    """The specs as canonical JSON (the bench's identity hashes it)."""
    return json.dumps(specs, sort_keys=True, default=str)
