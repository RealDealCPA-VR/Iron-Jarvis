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

from . import closing_offer as _closing_offer
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

#: The registry's two refusals of a tool the reply does not have (part 3,
#: Phase 3): ``unknown tool '<name>'`` (no such tool) and ``not armed: …``
#: (a real tool this reply was not given) — ``tools/registry.invoke``'s own
#: words, matched on their prefix like ``chat_turn.refusal_label`` does.
_NO_SUCH_TOOL_RX = re.compile(r"^(?:unknown tool '|not armed:)")

#: Real tools whose successful call changed a file (plugin checks follow them).
EDIT_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "apply_patch", "notebook_edit"})

#: v1.425.0 (Wave 5, C5): what a windowed ``read_file`` leaves free of the
#: turn's limit for one tool result (``HarnessTurn.tool_message_chars``):
#: room for the window's own note and anything the lane appends, so the
#: lane never cuts a window again (``chat_turn.tool_message_text``).
READ_NOTE_ROOM = 400


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
    lead: bool = False,
    asked: Iterable[str] = (),
) -> ToolPlan:
    """The harness turn's tools. Every filter the lanes apply later (the
    ceiling, low trust, Local only) is applied here too, so what the system
    prompt names is exactly what the model can call; the lanes' own filters
    then find nothing more to drop.

    ``lead`` (v1.403.0, a coding session WITH A TEAM): the turn's model is
    the team LEAD — every tool that changes a file or runs something is left
    out (``harness/team.lead_tools``, the one rule), after every other filter,
    so the dialect shows exactly what the lead holds.

    ``plan_mode`` (v1.367.0): only :data:`PLAN_MODE_TOOLS` (no app tool from
    ``extra`` — an app's tool may change things), plus ``exit_plan_mode`` in
    the stream lane. ``ask_user``: the stream lane's client draws the
    question card, so ``ask_user`` joins. The POST lane (``stream`` False)
    gets neither: it can ask nobody.

    ``asked`` (v1.434.0, chat-first plan Wave 2 group E): tools the
    MESSAGE asked for — the settings tools for a request about one of the
    app's settings (``chat_turn.harness_asked_tools``). Unlike ``extra``
    (a connector the user switched on, so granted) each keeps its OWN tier:
    an allow-tier one is armed, an ask-tier one is ask-armed (the stream
    lane cards it; the POST lane drops it). Every other filter applies."""
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
    for name in [*base, *extra, *asked]:
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
    if lead:
        from .team import lead_tools

        candidates = lead_tools(candidates, registry)
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


#: The real tools a helper tool starts (``delegate`` waits for its report;
#: ``spawn_agent`` too, here). A dialect tool that reaches either of them is a
#: helper tool (Claude Code's ``Agent``, Codex's ``spawn_agent``).
HELPER_TARGETS = frozenset({"delegate", "spawn_agent"})

#: v1.425.0 (Wave 5, C5): a tool name as a registered description writes one
#: (``read_document``, ``image_resize``) — the shape :func:`unreachable_cut`
#: looks for.
_TOOL_WORD_RX = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
#: v1.425.0 review: never after "e.g." / "i.e." — page_open's "use it for
#: the user's dev server, e.g. http://localhost:5173/cart, then page_click
#: ..." was split there and lost its second half mid-sentence.
_SENTENCE_SPLIT_RX = re.compile(r"((?<=[.!?])(?<!e\.g\.)(?<!i\.e\.)\s+)")
#: Tools a description names only as WHERE something came from ("a dynamic
#: agent created with ``create_agent``"), never as one to call — the sentence
#: stays (it carries the tool's own arguments).
_NAMED_AS_ORIGIN = frozenset({"create_agent"})
#: The parts a sentence may lose before the sentence itself does: a
#: parenthesis (``(usable with message_agent)``) and a trailing ", which …"
#: clause (``, which you can use with message_agent``).
_PAREN_RX = re.compile(r"\s*\([^()]*\)")
_WHICH_RX = re.compile(r",\s*which\b[^.;!?]*")


def unreachable_cut(text: str, reachable: "Iterable[str]", registered: "Iterable[str]") -> str:
    """``text`` without what names a REGISTERED tool this turn cannot reach
    (v1.425.0, Wave 5 C5). A pass-through tool is shown with its registered
    description, written for every lane: ``read_file``'s pointed at
    ``read_document``, ``view_image``'s at ``image_resize``, the shell's at
    ``rename_file`` / ``read_document`` / ``write_document``, ``delegate``'s
    and ``spawn_agent``'s at ``message_agent`` — none of them in a coding
    session, so the model was steered to tools it could not call (a call
    then met "not armed"). The smallest part goes: a parenthesis or a
    ", which …" clause that names one, else the whole sentence; a sentence
    naming only tools the turn has (or none) is kept as written.

    v1.425.0 review: the FIRST sentence — what the tool IS — is never
    dropped (only its parenthesis / "which" clause may go): a low-trust
    ``process_read`` ("Read what a program started with process_start
    printed ...") and ``process_stop`` were left as "``wait_ms`` ..." and
    "Returns its last unread output", and plan mode's ``page_open`` lost
    its purpose. Never raises."""
    try:
        body = str(text or "")
        known = {str(n) for n in registered}
        have = {str(n) for n in reachable} | _NAMED_AS_ORIGIN

        def _cant(part: str) -> bool:
            return any(w in known and w not in have for w in _TOOL_WORD_RX.findall(part))

        if not body or not known or not _cant(body):
            return body
        parts = _SENTENCE_SPLIT_RX.split(body)
        out: list[str] = []
        for i in range(0, len(parts), 2):
            sentence = parts[i]
            gap = parts[i + 1] if i + 1 < len(parts) else ""
            if _cant(sentence):
                sentence = _PAREN_RX.sub(lambda m: "" if _cant(m.group(0)) else m.group(0), sentence)
                sentence = _WHICH_RX.sub(lambda m: "" if _cant(m.group(0)) else m.group(0), sentence)
                if _cant(sentence) and i > 0:
                    continue
            out.append(sentence + gap)
        return "".join(out).strip()
    except Exception:  # noqa: BLE001 — a description is never worth a turn
        return str(text or "")


#: v1.425.0 (Wave 5, C5): the dialect command tools whose words promise a
#: program that keeps running (``run_in_background``, ``is_background``,
#: Codex's session ids) — :func:`model_specs` says so when this turn cannot
#: start one (``process_start`` is not armed: low trust keeps it away).
_KEEPS_RUNNING_WORDS = {
    "Bash": (
        "run_in_background is not available: a command runs until it ends or its timeout "
        "(2 minutes when you give none)"
    ),
    "run_shell_command": (
        "is_background is not available: a command runs until it ends or its timeout "
        "(2 minutes when you give none)"
    ),
    "exec_command": (
        "a command runs until it ends, for at most 2 minutes, and no session id is returned"
    ),
}
NO_BACKGROUND_NOTE = (
    "\n\nIn this reply {what}; this reply cannot start a program that keeps running."
)


def _team_first_lead(spec: dict[str, Any], roles: tuple[str, ...]) -> dict[str, Any]:
    """``spec`` with the team-first sentence in front of its description
    (v1.400.0), in the tool's own argument words; unchanged when the tool takes
    no role-like argument."""
    from ..agents.model_team import team_first_tool_sentence

    sentence = team_first_tool_sentence(spec.get("input_schema"), roles)
    if not sentence:
        return spec
    return {**spec, "description": sentence + " " + str(spec.get("description") or "")}


def _team_lead(spec: dict[str, Any], session_team: Any) -> dict[str, Any]:
    """``spec`` with the coding-session team's sentence in front of its
    description (v1.403.0), naming the session's workers in the tool's own
    argument words; unchanged for a tool that takes none of them."""
    from .team import tool_sentence

    sentence = tool_sentence(spec.get("input_schema"), session_team)
    if not sentence:
        return spec
    return {**spec, "description": sentence + " " + str(spec.get("description") or "")}


def model_specs(
    registry: Any, plan: ToolPlan, *, shell_description: str = "", bash_description: str = "",
    team_first: tuple[str, ...] = (), session_team: Any = None,
) -> list[dict[str, Any]]:
    """What the model is shown: the dialect's tools, then the pass-through
    real tools as the registry describes them. ``shell_description``
    (v1.377.0, a Build session): the shell's facts there — the user's own
    PATH and the tools found on it — for every dialect's command tool AND the
    pass-through ``shell``; "" = the registered words, exactly as before.
    ``bash_description`` (v1.386.0): the facts of the shell a BASH-WRITTEN
    command tool (Claude Code's ``Bash``) really runs in on Windows — Git for
    Windows' bash, or cmd.exe with what differs from bash
    (``command_env.bash_shell_description``); "" (another OS, a dialect
    without one) = ``shell_description`` / the registered words, as before.
    ``team_first`` (v1.400.0): the roles the turn's LISTED team members hold
    when team-first is on and the team is shown — every helper tool's
    description then LEADS with one sentence naming them in that tool's own
    argument words; () (off, or no team) = every description exactly as
    before.

    v1.425.0 (Wave 5, C5): a command tool whose words promise a program that
    keeps running says it cannot in a reply where ``process_start`` is not
    armed (:data:`NO_BACKGROUND_NOTE`); and a pass-through tool's registered
    description loses every sentence that names a tool this turn cannot
    reach (:func:`unreachable_cut`)."""
    out = []
    gate = set(plan.gate)
    for tool in plan.dialect_tools:
        spec = tool.spec()
        if tool.name in _KEEPS_RUNNING_WORDS and "process_start" not in gate:
            spec = {**spec, "description": spec["description"] + NO_BACKGROUND_NOTE.format(
                what=_KEEPS_RUNNING_WORDS[tool.name],
            )}
        words = shell_description
        if bash_description and tool.shell_syntax == "bash":
            words = bash_description
        note = runtime_note(registry, tool.runtime_from, shell_description=words)
        if note:
            spec = {**spec, "description": spec["description"] + "\n\nHow commands run here: " + note}
        if session_team is not None and HELPER_TARGETS & set(tool.targets):
            # v1.403.0: a coding session WITH A TEAM — its own workers named
            # (never the whole model team's team-first sentence too).
            spec = _team_lead(spec, session_team)
        elif team_first and HELPER_TARGETS & set(tool.targets):
            spec = _team_first_lead(spec, team_first)
        out.append(spec)
    if plan.passthrough:
        from ..tools.builtins import multi_read_spec

        try:
            registered = list(registry.names())
        except Exception:  # noqa: BLE001 — a registry double without names()
            registered = []
        reachable = gate | set(plan.offered_names)
        for s in registry.specs(list(plan.passthrough)):
            if shell_description and s.get("name") == "shell":
                s = {**s, "description": shell_description}
            if isinstance(s.get("description"), str):
                s = {**s, "description": unreachable_cut(s["description"], reachable, registered)}
            # Part 3, Phase 3: Jarvis's OWN read_file, wherever a harness turn
            # shows it under its own name (the generic dialect, and Codex's,
            # which has no read tool of its own), reads several files in one
            # call (`paths`). A vendor-shaped reader (Claude Code's `Read`,
            # Qwen Code's `read_file`) keeps its vendor's one-file shape: none
            # of those vendors has a multi-file read to map.
            s = multi_read_spec(s)
            if session_team is not None and s.get("name") in HELPER_TARGETS:
                s = _team_lead(s, session_team)
            elif team_first and s.get("name") in HELPER_TARGETS:
                s = _team_first_lead(s, team_first)
            out.append(_strip_store_as(s))
    return out


def dialect_section(plan: ToolPlan, *, ask_words: str = "") -> str:
    """The family layer's part of the tool guidance: the dialect's prompt
    with the offered names, and the ask sentence when any tool pauses."""
    names = plan.offered_names
    if not names:
        return "You have no tools in this turn: answer in text."
    text = plan.dialect.prompt.format(tools=", ".join(names))
    # v1.425.0 (C5): the dialect's advice, only the sentences this turn's
    # tools make true (``dialects.dialect_hints``).
    hints = _dialects.dialect_hints(plan.dialect, names, plan.gate)
    if hints:
        text = text + " " + hints
    return (text + ("\n" + ask_words if ask_words else "")).strip()


def _night_sentence(plan: ToolPlan, local_only: bool) -> str:
    """v1.398.0 review (night shift N1): what a NIGHT task's model is told —
    the truth of the night's allow-list (``night.RUNS_UNASKED``), never the
    day posture's words. A shown tool asks when any real tool it can run
    asks at night; under Local only a command tool still asks (its own
    card)."""
    from . import night as _night
    from ..providers import egress as _egress

    def _asks(real: str) -> bool:
        return _night.asks_at_night(real) or (local_only and real in _egress.RUNS_COMMANDS_TOOLS)

    gate = set(plan.armed) | set(plan.ask)
    free: list[str] = []
    asking: list[str] = []
    for t in plan.dialect_tools:
        real = [x for x in t.targets if x in gate] or list(t.targets)
        (asking if any(_asks(x) for x in real) else free).append(t.name)
    for n in plan.passthrough:
        (asking if _asks(n) else free).append(n)
    once = [t.name for t in plan.dialect_tools if t.targets and t.targets[0] in ONCE_ONLY_TOOLS]
    once += [n for n in plan.passthrough if n in ONCE_ONLY_TOOLS]
    asking = [n for n in asking if n not in once]
    parts = ["This task runs at night with nobody watching."]
    if free:
        parts.append(", ".join(free) + " run without asking, inside this task's own folder.")
    if asking or once:
        parts.append(
            "Calls to " + ", ".join([*asking, *once]) + " wait for an answer that may not come; "
            "after the wait the step is declined: do not repeat it, carry on without it and say at "
            "the end what was left for the user."
        )
    parts.append(
        "A commit or push of your own, a project's own command lines and anything that spends "
        "money always wait for the user, and are declined the same way."
    )
    return " ".join(parts)


#: v1.425.0 (Wave 5, C5): THE APPROVAL SENTENCE, true for each posture. The
#: old one said "Calls to Bash pause while the user approves them" in every
#: posture but "Don't ask" — while a command that only reads (ls, git status)
#: has run without a card under Ask first, Accept edits and Auto since
#: v1.417.0, a session grant ("Allow for this session") ends the cards for
#: that tool, Auto lets routine calls through, "Ask before every action"
#: also cards file edits and the web, and "Don't ask" still cards a few
#: calls every time (it said they all "run without pausing"). Each piece
#: below names what the stream lane's card rules really do
#: (``routes/chat.py``'s posture branch, ``core/auto_approve``).
_DECLINE_WORDS = "if they decline, do not repeat the same call."
#: What may run without a card although its tool asks (:func:`_unasked`):
#: a command on the read-only list (``core/auto_approve.read_only_command``,
#: v1.417.0 — never under low trust, never in a Local only chat), a look at
#: the page the user's Browser pane shows (``page_shot`` / ``page_open``'s
#: own spend check), a call of a tool the user allowed for this session.
_READ_ONLY_COMMAND_WORDS = "a command that only reads (ls, git status, cat, grep ...)"
_PREVIEW_PAGE_WORDS = "a look at the page the user's Browser pane shows"
_SESSION_GRANT_WORDS = "a call of a tool the user already allowed for this session"
_COMMAND_REAL = frozenset({"shell", "process_start"})
_PAGE_REAL = frozenset({"page_screenshot", "page_open"})


def _unasked(asking: "set[str]", *, commands: bool) -> str:
    """``"X, Y and Z may run without asking"`` for the ask-tier tools in
    ``asking``; ``commands`` False drops the read-only command exception
    (low trust, "Ask before every action")."""
    parts: list[str] = []
    if commands and asking & _COMMAND_REAL:
        parts.append(_READ_ONLY_COMMAND_WORDS)
    if asking & _PAGE_REAL:
        parts.append(_PREVIEW_PAGE_WORDS)
    parts.append(_SESSION_GRANT_WORDS)
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return joined + " may run without asking"
#: v1.425.0 review: the tools that run a command (the git guard and a
#: project's command file read every one of them — ``harness/git_guard``,
#: ``harness/command_shell``).
_COMMAND_RUNNERS = frozenset({"shell", "process_start", "process_write"})


def _yolo_still_asks(gate: "set[str]") -> str:
    """"Don't ask": the calls that still card, every time, in every posture
    — only the ones this turn's tools can make (v1.425.0 review: the list
    said "a git commit or push" while the git guard also cards a change on
    GitHub, git that throws away work not committed yet and a write to git's
    settings (``harness/git_guard``), and left out a click on a button that
    reads as deleting, sending or paying (``page_act``'s once-only check))."""
    parts: list[str] = []
    if gate & _COMMAND_RUNNERS:
        parts.append(
            "git on this session's own branch that commits, pushes or changes something on "
            "GitHub, throws away work not committed yet or changes git's settings"
        )
        parts.append("a command a project's own command file asked for")
    if gate & _PAGE_REAL:
        parts.append(
            "opening a local page that is neither the page the user's Browser pane shows nor one "
            "already allowed"
        )
    if "page_click" in gate:
        parts.append("a click on a button that reads as deleting, sending or paying for something")
    parts.append("anything that costs money")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", or " + parts[-1]


def _reaches_past_pc(registry: Any, name: str) -> bool:
    """A Local only chat asks about this tool's every call
    (``egress.asks_first`` with the scope on: the tool reaches past this PC).
    Never raises (a lookup that fails: does not)."""
    try:
        from ..providers import egress

        tool = registry.get(name)
        return tool is not None and bool(egress.reaches_outside(tool, registry))
    except Exception:  # noqa: BLE001
        return False


#: Every posture but "Don't ask": an edit of a file that steers git, the
#: editor, CI or Iron Jarvis itself cards once, every time
#: (``core/auto_approve.guarded_edit``, v1.407.2) — named only when the turn
#: offers a file editor.
_GUARDED_EDITS_WORDS = (
    "An edit of a file that steers git, the editor, CI or Iron Jarvis itself (.git, "
    ".gitattributes, .github/workflows, .vscode/settings.json and tasks.json, .ironjarvis, "
    ".claude, AGENTS.md, CLAUDE.md) waits for the user's approval every time."
)


def _shown_for(plan: ToolPlan, real: "set[str]") -> list[str]:
    """The names the model is shown for the real tools ``real`` (a dialect
    tool once, by its own name; a pass-through tool by its name)."""
    shown = [t.name for t in plan.dialect_tools if t.targets and set(t.targets) & real]
    shown += [n for n in plan.passthrough if n in real]
    return list(dict.fromkeys(shown))


def ask_sentence(
    plan: ToolPlan, *, approval_mode: str, local_only: bool, registry: Any, low_trust: bool = False,
) -> str:
    """Which shown tools pause for the user (stream lane only).

    v1.398.0 review: a NIGHT task's turn (``night.current()``) is told the
    night's own rule (:func:`_night_sentence`), whatever the posture.

    v1.425.0 (Wave 5, C5): one sentence per posture, each true of the
    stream lane's card rules — see :data:`_DECLINE_WORDS` and the pieces
    beside it. ``low_trust`` (the turn starts low): the read-only exception
    is not offered (``auto_approve`` keeps every card then).

    v1.425.0 review: (a) Auto in a LOW-TRUST turn passes nothing by rule or
    check (``auto_approve.classify`` answers ASK "low_trust" first), so it
    reads as Ask first without the read-only list; (b) Auto's "never
    routine" list names what its rules READ — deleting, overwriting, sending
    or paying, and an EDIT outside the folder (a command reading outside it
    is judged, and a session grant lets even a deleting command through, so
    nothing "always pauses" by rule); (c) a Local only chat names EVERY tool
    of the turn that reaches past this PC as asking every time (the agent's
    browser clicks and typing too, allow-tier elsewhere), and the rest of the
    asking tools — an edit under "Ask before every action" — read as their
    posture says (a session grant covers them); (d) "Don't ask"'s list is
    :func:`_yolo_still_asks`."""
    from . import night as _night

    if _night.current() is not None:
        return _night_sentence(plan, local_only)
    mode = str(approval_mode or "")
    gate = set(plan.gate)
    asking = set(plan.ask)
    if mode == "always_ask":
        # "Ask before every action" cards file edits and the web too
        # (``chat_turn.STRICT_ASK_TOOLS``), allow-tier as they are.
        from ..daemon.chat_turn import STRICT_ASK_TOOLS

        asking |= gate & set(STRICT_ASK_TOOLS)
    guarded = "" if mode == "yolo" or not (gate & EDIT_TOOLS) else _GUARDED_EDITS_WORDS
    lead = ""
    if local_only:
        # A Local only chat cards every call that reaches outside this PC —
        # a command, a page, a click or typed entry in the agent's browser —
        # every time, past every grant and the read-only list
        # (``egress.asks_first``, v1.332.0 / v1.371.0 / v1.376.0), whatever
        # the tool's tier.
        every = {n for n in gate | asking if _reaches_past_pc(registry, n)}
        every_shown = _shown_for(plan, every)
        if every_shown:
            lead = (
                f"Calls to {', '.join(every_shown)} pause while the user approves them every time: "
                "this chat is kept on this PC and each of these reaches past it, so no earlier OK "
                "covers one"
            )
        asking -= every
    shown = _shown_for(plan, asking)
    rest = ""
    if not shown:
        rest = ""
    elif mode == "yolo":
        # v1.367.0: a once-only tool (the plan door) waits for the user even
        # under auto-approve — the sentence must not promise otherwise.
        once = set(ONCE_ONLY_TOOLS)
        always = [t.name for t in plan.dialect_tools if t.targets and t.targets[0] in once and t.name in shown]
        always += [n for n in plan.passthrough if n in once and n in shown]
        free = [n for n in shown if n not in always]
        parts = []
        if free:
            parts.append(
                "The user chose not to be asked, so " + ", ".join(free) + " run without pausing, "
                "except a few calls that still wait for the user's answer every time ("
                + _yolo_still_asks(gate) + "); " + _DECLINE_WORDS
            )
        if always:
            parts.append(", ".join(always) + " always waits for the user's approval.")
        rest = " ".join(parts)
    elif mode == "auto" and not low_trust:
        # "may", as under Ask first: a page's own check decides a page call
        # whatever a session grant says (``page_open`` / ``page_screenshot``:
        # the Browser pane's page runs unasked, another local page always
        # asks), so "unless the user allowed that tool" was a promise the
        # lane does not keep either way.
        page = _PREVIEW_PAGE_WORDS + ", " if asking & _PAGE_REAL else ""
        rest = (
            f"Calls to {', '.join(shown)} pause while the user approves them; {page}a call of a "
            "tool the user already allowed for this session, or one Iron Jarvis's rules or a quick "
            "check find routine for what the user asked, may run without asking (a call its rules "
            "read as deleting, overwriting, sending or paying for something, or one that edits a "
            f"file outside this folder, is never found routine); {_DECLINE_WORDS}"
        )
    else:
        # Ask first (the default) and Accept edits (which adds only file
        # edits inside the folder — allow-tier already, or a folder rule's);
        # "Ask before every action" and a low-trust turn keep every command's
        # card (no read-only list). A Local only chat's commands and pages
        # were taken out above (no read-only list, no page exception there).
        exceptions = _unasked(asking, commands=not low_trust and mode != "always_ask")
        rest = (
            f"Calls to {', '.join(shown)} pause while the user approves them; {exceptions}; "
            f"{_DECLINE_WORDS}"
        )
    if lead:
        lead += (". " if rest else "; " + _DECLINE_WORDS)
    return " ".join(p for p in (lead.strip(), rest, guarded) if p).strip()


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


#: v1.421.0 (A5): where a call written as text NAMES its tool — GLM's
#: ``<tool_call>name …``, ``<invoke name="…">``, ``<function=…>`` and a JSON
#: ``"name": "…"`` (Qwen / Hermes put one inside ``<tool_call>``). The shapes
#: ``providers/reply_budget._LEAK_NAME_RES`` reads a half-written call by.
_CALL_NAME_RXS = (
    re.compile(r"<\s*tool_call\s*>\s*([A-Za-z_][A-Za-z0-9_.\-]{0,63})", re.IGNORECASE),
    re.compile(r"<\s*invoke\s+name\s*=\s*[\"']?([A-Za-z0-9_.\-]{1,64})", re.IGNORECASE),
    re.compile(r"<\s*function\s*=\s*[\"']?([A-Za-z0-9_.\-]{1,64})", re.IGNORECASE),
    re.compile(r"\"(?:name|tool|tool_name|function)\"\s*:\s*\"([A-Za-z0-9_.\-]{1,64})\""),
)

#: v1.421.0 review (A5): a reply's CODE — a fenced block (``` or ~~~, closed
#: by the same fence or left open to the end of the reply) and an inline code
#: span. A call that sits only there, beside the report's own prose, is a
#: QUOTATION (a reviewer reading a test fixture, a teammate documenting the
#: format), not the model's call.
_FENCE_RX = re.compile(
    r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n(?P<body>.*?)(?:^[ \t]{0,3}(?P=fence)[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_TICKS_RX = re.compile(r"`+")


def _blank_inline_code(text: str) -> str:
    """``text`` with every inline code span blanked, by CommonMark's rule: a
    backtick run opens a span the NEXT run of the same length on its line
    closes. Paired by index, never by a backtracking regex — a reply streams
    up to 200,000 characters on the event loop, and backtick runs of many
    lengths made the regex this replaced take half a minute."""
    import bisect

    out = list(text)
    offset = 0
    for line in text.split("\n"):
        runs = [(m.start(), m.end()) for m in _TICKS_RX.finditer(line)]
        if len(runs) > 1:
            by_len: dict[int, list[int]] = {}
            for k, (a, b) in enumerate(runs):
                by_len.setdefault(b - a, []).append(k)
            i = 0
            while i < len(runs):
                a, b = runs[i]
                same = by_len[b - a]
                p = bisect.bisect_right(same, i)
                if p < len(same):
                    j = same[p]
                    start, end = offset + a, offset + runs[j][1]
                    out[start:end] = " " * (end - start)
                    i = j + 1
                else:
                    i += 1
        offset += len(line) + 1
    return "".join(out)


def _call_text(body: str) -> tuple[str, list[str]]:
    """``body`` with every code region blanked (same length, so nothing
    shifts), and the bodies of the fenced blocks that END the reply — the one
    fence a call the model meant to make may sit in ("Let me look." then a
    ```json call: a model with no tool parser stops right after its call)."""
    chars = list(body)
    ending: list[str] = []
    for m in _FENCE_RX.finditer(body):
        if not body[m.end():].strip():
            ending.append(m.group("body") or "")
        chars[m.start():m.end()] = " " * (m.end() - m.start())
    return _blank_inline_code("".join(chars)), ending


def named_text_tool_call(text: str, offered: Iterable[str]) -> str:
    """The NAME of an OFFERED tool a reply wrote a call to AS TEXT, or ""
    (v1.421.0, A5 — the agent runtime's reading). Stricter than
    :func:`text_tool_call`, whose tag shapes match whatever they name: an
    agent run's answer IS its report (a reviewer's verdict, a teammate's
    result), and a report that quotes the ``<tool_call>`` format — a review
    of tool-call parsing code, notes on a model's quirks — must not be told
    to "call the tool itself". So the call must name a tool the step was
    offered, the rule ``reply_budget.cut_off_round`` applies to a leaked
    half call.

    v1.421.0 review: and it must not be QUOTED. A report that quotes a real
    call (a fixture naming ``list_files``, which a reviewer is offered) in a
    code block or inline code was told to call it, and the reply to that note
    REPLACED the report as the run's answer. A call is read from the reply's
    bare text, or from a fenced block that ENDS the reply; one only inside
    inline code, or inside a fence the report goes on after, is not a call.
    Never raises."""
    try:
        names = {str(n) for n in (offered or ()) if n}
        if not names or not text_tool_call(text, names):
            return ""
        bare, ending = _call_text(str(text or ""))
        for body in (bare, *ending):
            for rx in _CALL_NAME_RXS:
                for m in rx.finditer(body):
                    if m.group(1) in names:
                        return m.group(1)
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
        # Part 3, Phase 2: a folder that changed its finish check's switch
        # (``finish.toml``) is a different harness; the default adds nothing.
        _finish = getattr(tree, "finish", None)
        _mark = _finish.mark() if hasattr(_finish, "mark") else ""
        self.fingerprint = _components.fingerprint(rendered, plan.dialect.id, _mark)
        self.today = today
        #: v1.367.0: a Build session's plan-mode turn (set by :func:`build`).
        self.plan_mode = False
        self.nudged = False
        self.text_call_kind = ""
        #: v1.402.0 (part 4): this reply's CLOSING-OFFER nudge (``harness/
        #: closing_offer``) — a reply that ends "Want me to …?" with work
        #: left is told once to do it. Read by both chat lanes at the same
        #: seam as :meth:`wants_nudge`.
        self.closing_offer = _closing_offer.ClosingOffer()
        #: call id -> the name the model used, for calls that were translated
        self._called_as: dict[str, str] = {}
        #: call id -> the dialect tool the model called (for its own words)
        self._called_tool: dict[str, _dialects.DialectTool] = {}
        #: call id -> the shell language of a call the HARNESS makes itself
        #: that re-runs a bash-written command (v1.386.0 review: the finish
        #: check's re-run of a ``Bash`` command)
        self._marked_syntax: dict[str, str] = {}
        #: v1.425.0 (Wave 5, C4): call id -> the ``(provider, model)`` that
        #: served the round, for each IMAGE READ (a dialect's reader on a
        #: picture, Codex's ``view_image``) — :meth:`ctx_for` puts it on the
        #: call's context so ``view_image`` hands the picture back when that
        #: model takes one (:meth:`translate_calls`).
        self._picture_reads: dict[str, tuple[str, str]] = {}
        #: v1.377.0: a Build session's shell facts (the user's own PATH and
        #: the tools found on it — ``harness/command_env``); "" elsewhere.
        self.shell_description = ""
        #: v1.386.0: the facts of the shell a bash-written command tool
        #: (Claude Code's ``Bash``) runs in on Windows (``command_env.
        #: bash_shell_description``); "" elsewhere.
        self.bash_description = ""
        #: Part 3, Phase 3: the session folder's PROJECT MAP for this turn
        #: (``harness/project_map.MapResult``; None = no map — not a Build
        #: session's turn, or no folder). Per turn, like the date: rendered
        #: by :meth:`block`, never part of the fingerprint.
        self.project_map: Any = None
        #: Part 3, Phase 3: the most characters ONE tool result the model
        #: reads may hold in this turn (``chat_turn.tool_message_text``);
        #: None = the lanes' old fixed cut.
        self.tool_message_chars: "int | None" = None
        #: Part 3, Phase 3: the project's OWN checkers found for this turn
        #: (``harness/checkers.ProjectCheckers``; None = none run).
        self.checkers: Any = None
        #: v1.394.0 (the long-jobs plan's Phase 5): the user's MODEL TEAM —
        #: the members a helper may run on, each on its own model (``agents/
        #: model_team.team_block``) — set only when this turn can start a
        #: helper. Per turn (usage moves), never part of the fingerprint;
        #: "" (a byte-identical prompt) when there is no team.
        self.team_section: str = ""
        #: v1.400.0 (TEAM-FIRST, ``Config.team_first``): the roles the listed
        #: team members hold, when the setting is on AND the team section is
        #: shown — the helper tool's description then leads with the team
        #: (:func:`model_specs`). () = off, or no team: every tool spec is
        #: exactly as before. Per turn, never part of the fingerprint.
        self.team_first: tuple[str, ...] = ()
        #: v1.403.0 (the coding-session team, ``harness/team``): the turn's
        #: :class:`~iron_jarvis.harness.team.TeamTurn` when this coding
        #: session has a team (None: solo — every other turn). Its ``plan``
        #: above is then the LEAD's (no tool that changes a file or runs
        #: something) and :attr:`full_plan` the whole coding set, which the
        #: workers get — and the lead too once no worker could take a piece
        #: (:meth:`unlock_lead`).
        self.team: Any = None
        self.full_plan: "ToolPlan | None" = None
        #: v1.417.0 (skill parity, ``skills/native.listing``): the skills
        #: this turn's model may load, listed up front in the words of the
        #: harness its dialect speaks (Claude Code's "The following skills
        #: are available…", Codex's list with each SKILL.md's location) —
        #: set only when ``skill_load`` is in the turn's gate. Per turn,
        #: never part of the fingerprint; "" (a byte-identical prompt) when
        #: there is none.
        self.skill_listing: str = ""
        #: v1.421.0 (Wave 1, A3): the STANDARD this reply's HELPERS are judged
        #: by (``harness/standard.HelperStandard`` — the folder's rule files,
        #: the guardrails, the accepted lessons, the matching brief, the
        #: checks), built once with the turn of a Build session that can
        #: start a helper and has a folder; None for every other turn (its
        #: helpers' prompts are exactly as before). Never in the LEAD's
        #: prompt and never part of the fingerprint.
        self.helper_standard: Any = None

    # -- the team lead ------------------------------------------------------ #

    def unlock_lead(self) -> tuple[list[str], list[str]]:
        """v1.403.0: the lead gets the whole coding set for the REST of this
        reply (no worker could take a piece). Answers ``(armed, ask)`` — the
        real tools it gains, by tier (the lanes add them to their own lists
        in place, ``chat_turn.apply_team_unlock``). ``([], [])`` when it holds
        them already."""
        full = self.full_plan
        if full is None or full is self.plan:
            return [], []
        have_armed = set(self.plan.armed)
        have_ask = set(self.plan.ask)
        gained_armed = [n for n in full.armed if n not in have_armed]
        gained_ask = [n for n in full.ask if n not in have_ask]
        self.plan = full
        return gained_armed, gained_ask

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

        # Part 3, Phase 3: the session folder's PROJECT MAP (harness/
        # project_map) — per turn and per folder, so not in the fingerprint;
        # LAST, so the stable part of the prompt above it is unchanged.
        project_map = ""
        _map_text = str(getattr(self.project_map, "text", "") or "")
        if _map_text:
            project_map = "\n\n" + _map_text
        # v1.394.0: the model team the turn's helpers may run on ("" = none).
        team = ("\n\n" + self.team_section) if self.team_section else ""
        # v1.417.0: the skills the model may load, listed up front ("" = none).
        skills = ("\n\n" + self.skill_listing) if self.skill_listing else ""
        return (
            "\n\n" + self.rendered + skills + date + prompt_line(self.plan.gate) + prompt_section()
            + summary_section() + branch_line() + team + project_map
        )

    @staticmethod
    def system_without_exits(system: str, exit_lines: str) -> str:
        """The prompt with the chat's escalate / workflow lines swapped for
        :data:`HARNESS_EXIT_LINES` (a harness turn never escalates)."""
        return system.replace(exit_lines, HARNESS_EXIT_LINES, 1)

    # -- tools -------------------------------------------------------------- #

    def specs(self, registry: Any) -> list[dict[str, Any]]:
        return model_specs(
            registry, self.plan, shell_description=self.shell_description,
            bash_description=self.bash_description,
            # (only with a team SHOWN: the sentence points at its list)
            team_first=self.team_first if self.team_section else (),
            # v1.403.0: a coding session's team names its own workers.
            session_team=getattr(self.team, "team", None) if self.team is not None else None,
        )

    def translate_calls(
        self, calls: list[Any], *, serving: "tuple[str, str] | None" = None,
    ) -> list[Any]:
        """The model's calls mapped to the real tools (same ids). A name the
        dialect does not know passes through untouched — the registry decides
        it (``unknown tool`` / ``not armed``, ledgered).

        ``serving`` (v1.425.0, Wave 5 C4): the ``(provider, model)`` that
        served the round these calls came from — both lanes pass it (MIRROR
        NOTE, lock-step). Each IMAGE READ (a dialect reader on a picture —
        Claude Code's ``Read``, Qwen Code's ``read_file`` — or Codex's
        ``view_image``) is remembered with it, and :meth:`ctx_for` hands it
        to ``view_image``, which then returns the PICTURE when that model
        takes one in a tool result. Omitted: no call is an image read (a
        caller that does not know the round's model keeps the description).

        v1.425.0 (C5): a WINDOWED ``read_file`` (a line range, or Claude
        Code's numbered ``Read``) is given this turn's limit for one tool
        result (:data:`READ_NOTE_ROOM` under :attr:`tool_message_chars`), so
        the window stops at a WHOLE line and its own note says exactly which
        lines came back — instead of the lane cutting the middle out of a
        window whose note then claimed lines the model never saw."""
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
            if (
                serving is not None and t.real == "view_image"
                and self.plan.dialect.lookup(call.name) is not None
            ):
                self._picture_reads[call.id] = (str(serving[0] or ""), str(serving[1] or ""))
            capped = self._read_window_capped(t)
            if capped is not None:
                t = _dialects.Translated(real=t.real, args=capped, called_as=t.called_as)
            if t.real != call.name or t.args is not call.arguments:
                self._called_as[call.id] = call.name
                tool = self.plan.dialect.lookup(call.name)
                if tool is not None:
                    self._called_tool[call.id] = tool
                out.append(ToolCall(id=call.id, name=t.real, arguments=t.args))
            else:
                out.append(call)
        return out

    def _read_window_capped(self, t: "_dialects.Translated") -> "dict[str, Any] | None":
        """v1.425.0 (C5): a copy of a windowed ``read_file`` call's arguments
        with ``max_chars`` = this turn's limit for one tool result less
        :data:`READ_NOTE_ROOM`; None for any other call (unchanged) — a plain
        whole-file read, a read of several files (``paths``: its own
        bound, ``READ_MANY_MAX_CHARS``), a turn with no limit set, or a
        model that sent its own ``max_chars`` (it keeps it, never more than
        the limit)."""
        if t.real != "read_file" or not isinstance(t.args, dict):
            return None
        cap = self.tool_message_chars
        if not isinstance(cap, int) or cap <= 0 or t.args.get("paths") is not None:
            return None
        from ..tools.builtins import _WINDOW_KEYS

        if not any(t.args.get(k) is not None for k in _WINDOW_KEYS):
            return None
        room = max(1, cap - READ_NOTE_ROOM)
        own = t.args.get("max_chars")
        if isinstance(own, int) and not isinstance(own, bool) and 0 < own <= room:
            return None
        return {**t.args, "max_chars": room}

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

    def mark_called_as(self, call_id: str, name: str, *, shell_syntax: str = "") -> None:
        """Part 3, Phase 2: a call the HARNESS makes itself (the finish
        check's test run, ``harness/finish``) is ledgered under ``name``
        (``ToolInvocation.called_as``) — through :meth:`ctx_for`, the same
        path a translated call takes. ``shell_syntax`` (v1.386.0 review): the
        language the command is in when it re-runs a command the MODEL wrote
        in bash (Claude Code's ``Bash``) — so it runs in the shell the model's
        own run did (Git for Windows' bash), never cmd.exe. "" = the
        platform's own shell (a folder's own check: the user's words)."""
        if call_id and name:
            self._called_as[str(call_id)] = str(name)
            if shell_syntax:
                self._marked_syntax[str(call_id)] = str(shell_syntax)

    def shell_syntax_of(self, call: Any) -> str:
        """The shell language ``call``'s command was written in (v1.386.0):
        "bash" for a call of a bash-written command tool (Claude Code's
        ``Bash``) that became ``shell`` / ``process_start`` — what
        :meth:`ctx_for` puts on its context — else ""."""
        cid = getattr(call, "id", "")
        if getattr(call, "name", "") not in _dialects.SHELL_TARGETS:
            return ""
        marked = self._marked_syntax.get(cid)
        if marked:
            return marked
        tool = self._called_tool.get(cid)
        return str(getattr(tool, "shell_syntax", "") or "") if tool is not None else ""

    def shell_syntax_called(self, name: str) -> str:
        """The shell language of the dialect tool the model calls ``name``
        (v1.386.0 review: a ledger row's ``called_as`` — a carried-on reply's
        earlier commands), else ""."""
        for tool in getattr(self.plan, "dialect_tools", ()) or ():
            if getattr(tool, "name", "") == name or name in (getattr(tool, "aliases", ()) or ()):
                return str(getattr(tool, "shell_syntax", "") or "")
        return ""

    def ctx_for(self, ctx: Any, call: Any) -> Any:
        """``ctx`` carrying the model's name for a translated call, so the
        ledger row records it (``ToolInvocation.called_as``). v1.386.0: and,
        for a call of a BASH-WRITTEN command tool (Claude Code's ``Bash``)
        that became ``shell`` / ``process_start``, the language its command
        is in (``ToolContext.shell_syntax`` = "bash"), so it runs in Git for
        Windows' bash on Windows — decided by the dialect tool the model
        called, never by an argument it sent.

        v1.425.0 (Wave 5, C4): and, for an IMAGE READ, the round's serving
        ``(provider, model)`` (``ToolContext.picture_read`` — see
        :meth:`translate_calls`); Codex's ``view_image`` keeps its own name,
        so this applies whether or not the name changed."""
        cid = getattr(call, "id", "")
        if ctx is None:
            return ctx
        out = ctx
        name = self._called_as.get(cid)
        if name and name != getattr(call, "name", ""):
            try:
                out = dataclasses.replace(ctx, called_as=name)
            except Exception:  # noqa: BLE001 — a test double that is no dataclass
                return ctx
            syntax = self.shell_syntax_of(call)
            if syntax:
                try:
                    out = dataclasses.replace(out, shell_syntax=syntax)
                except Exception:  # noqa: BLE001 — an older context: its own shell
                    pass
        serving = self._picture_reads.get(cid)
        if serving is not None and getattr(call, "name", "") == "view_image":
            try:
                out = dataclasses.replace(out, picture_read=serving)
            except Exception:  # noqa: BLE001 — an older context: the description
                pass
        return out

    def model_text(self, call: Any, content: str) -> str:
        """The result as the model reads it. A required-argument refusal for a
        translated call is reworded in the dialect's own names (the registry's
        words name the REAL tool's arguments, which the model never saw).

        Part 3, Phase 3: a call of a tool this reply does NOT have (the
        registry's "unknown tool" / "not armed" refusal — e.g. Claude calling
        ``python``) is answered with the names it CAN call, as the model was
        shown them, so its next call is one that runs."""
        tool = self._called_tool.get(getattr(call, "id", ""))
        text = str(content)
        if _NO_SUCH_TOOL_RX.match(text):
            names = list(self.plan.offered_names)
            if names:
                return f"{text}\n(The tools you can call in this reply: {', '.join(names)}.)"
            return text
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
        if self.project_map is not None:
            # Part 3, Phase 3 (additive, only when a map was built): its size,
            # the share it was given and whether anything was left out.
            try:
                out["map"] = self.project_map.receipt()
            except Exception:  # noqa: BLE001 — a receipt never breaks a turn
                pass
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
    bash_description: str = "",
    lead: bool = False,
    asked: Iterable[str] = (),
) -> HarnessTurn:
    """Plan the tools, compose the tree and render it. Pure apart from the
    registry / permission reads (no I/O): the project layer is read by the
    caller, off the loop (:func:`components.read_project_layer`).

    ``plan_mode`` / ``ask_user`` (v1.367.0, a Build session's turn): see
    :func:`plan_tools`; plan mode also renders the tree's MODE component
    ("plan only: propose, do not change files"), naming the tools in the
    model's own dialect.

    ``lead`` (v1.403.0, a coding session WITH A TEAM, act mode): the turn's
    plan is the LEAD's (:func:`plan_tools` ``lead``) and ``full_plan`` the
    whole coding set — ONLY when the whole set can start a helper (a lead
    with no way to hand work out is never locked: the turn then runs as a
    solo turn would, ``full_plan`` None)."""
    family, dialect = _dialects.resolve(requested, provider, model)
    plugins = layer.plugins if layer.used else _components.Plugins()
    plan = plan_tools(
        registry, permissions, dialect, family=family, stream=stream,
        low_trust=low_trust, local_only=local_only, tool_ceiling=tool_ceiling,
        extra=extra, plugins=plugins, plan_mode=plan_mode, ask_user=ask_user, asked=asked,
    )
    full_plan: "ToolPlan | None" = None
    if lead and not plan_mode and plan.gate & HELPER_TARGETS:
        full_plan = plan
        plan = plan_tools(
            registry, permissions, dialect, family=family, stream=stream,
            low_trust=low_trust, local_only=local_only, tool_ceiling=tool_ceiling,
            extra=extra, plugins=plugins, plan_mode=plan_mode, ask_user=ask_user, lead=True,
            asked=asked,
        )
    section = dialect_section(plan, ask_words=ask_sentence(
        plan, approval_mode=approval_mode, local_only=local_only, registry=registry,
        low_trust=low_trust,
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
        # v1.421.0 (A7): the folder's helper briefs are handed out when this
        # turn's gate holds a helper door (a team lead's does), else followed
        # by the model itself, as before.
        helpers=bool(plan.gate & HELPER_TARGETS),
    )
    rendered = _components.render(tree)
    if today is None:
        today = _dt.date.today().isoformat()
    turn = HarnessTurn(
        requested=requested, plan=plan, layer=layer, tree=tree, rendered=rendered, today=today,
    )
    turn.plan_mode = bool(plan_mode)
    # v1.403.0: the whole coding set behind a team lead's plan (None: solo).
    turn.full_plan = full_plan
    # v1.377.0: a Build session's commands run with the user's own PATH; the
    # caller worked out the words off the loop (``harness/command_env``).
    turn.shell_description = str(shell_description or "")
    # v1.386.0: what a bash-written command tool really runs in on Windows.
    turn.bash_description = str(bash_description or "")
    return turn


def speaks_bash(requested: str, provider: str, model: str) -> bool:
    """Will a turn with this dialect choice and serving pair show a command
    tool whose model writes bash (v1.386.0: Claude Code's ``Bash``)? Pure."""
    try:
        _family, dialect = _dialects.resolve(requested, provider, model)
    except Exception:  # noqa: BLE001 — an unknown dialect is the build's error
        return False
    return _dialects.speaks_bash(dialect)


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
