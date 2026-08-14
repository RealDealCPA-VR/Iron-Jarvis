"""Short-horizon decomposition (v1.132.0): plan → execute → verify → assemble.

Frontier models sustain the runtime's flat perceive→act loop; a local model
behind the prompted-tools scaffold (``tool_use_mode: "prompted"``) loses the
thread after a few steps. This module compensates: a plausibly multi-step task
(since v1.174.0 that includes a BULK task — one action repeated over a whole
folder) is split into 2–8 small verifiable steps — more when the session
carries a raised step budget (Contract 4) — each executed in a FRESH bounded
mini-loop (the runtime's own routing/streaming/tool machinery via the
``perceive_act`` seam — never a copy), gated by a per-step verifier, and
finally assembled into one honest answer. Everything reuses the SAME AgentRun
record, state transitions, event bus, and stream sink, so the dashboard sees a
normal run — plus additive ``plan.*`` events narrating the decomposition.

WHERE THIS LANE DOES AND DOES NOT RUN — read this before crediting it with
fixing a real failure. :func:`should_decompose` still requires a resolved
``tool_use_mode == "prompted"`` (or the opt-in ``decompose_all_tasks``), so a
provider advertising NATIVE tool use never enters this module. The v1.174.0
run this wave was built on (session ``session_8d66af4dc17b``, provider
``fleet-custom``, which carries ``tool_use = true``) is exactly such a session:
everything below — the bulk signal, :data:`MAX_PLAN_STEPS_CEILING`, the
templated planner prompt, :func:`mini_loop_budget` — was unreachable for it.
The flat loop owns that trace; what this module owns is the short-horizon
local lane, and the honesty of saying so.

One-shot calls (plan / judge / assemble) go through ``platform.router.complete``
— the agents-layer path the runtime itself uses — which already carries the
same transient-retry + cross-provider-failover spine the daemon's
``_one_shot_complete`` wraps around raw adapters.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.events import EventType
from ..providers.adapters.base import LLMMessage
from ..providers.roles import RoleResolution, resolve_role

#: Plan size bounds: fewer than 2 steps means "no decomposition needed" (the
#: flat loop handles it); more than 8 is clipped — a short-horizon model can't
#: hold a longer plan's running context anyway.
MIN_PLAN_STEPS = 2
MAX_PLAN_STEPS = 8
#: Ceiling on the plan cap when THIS session was given a bigger step budget
#: (v1.174.0). A raised budget must not be silently ignored — but a plan longer
#: than this is beyond what a short-horizon model can hold, which is the very
#: condition decomposition exists for.
MAX_PLAN_STEPS_CEILING = 20
#: Session steps assumed per plan step when scaling the plan cap: a step costs
#: at least a couple of mini-loop rounds plus its verify gate.
PLAN_STEP_COST = 3
#: Per-step mini-loop budget (further capped by the session's resolved step
#: budget): small on purpose — the whole point is that each step is small
#: enough to land.
MAX_MINI_LOOP_STEPS = 6
#: Share of a RAISED session budget one mini-loop may spend (v1.174.0). A user
#: who asks for 60 steps on a bulk job means each step may do more work, not
#: that more 6-step steps get planned; the divisor keeps one step from eating
#: the whole run.
MINI_LOOP_BUDGET_SHARE = 4
#: Hard ceiling on the derived mini-loop budget, however large the session
#: budget gets — past this a "small verifiable step" is just the flat loop
#: again, wearing a plan's clothes.
MAX_MINI_LOOP_CEILING = 24
#: Cap on the running prior-step context fed into each mini-loop, so late steps
#: don't re-inflate the very context burden decomposition exists to remove.
MAX_PRIOR_CONTEXT_CHARS = 2000
#: The original task rides along as a one-liner (orientation, not payload).
MAX_TASK_ONELINER_CHARS = 300
#: Heuristic threshold: a task longer than this is presumed multi-step.
MULTI_STEP_TASK_CHARS = 200

# ------------------------------------------------------------------ heuristic
#: DELIBERATELY DUMB imperative-verb list for the engage heuristic. It only
#: has to be roughly right: a false negative just keeps the flat loop, a false
#: positive costs one planning call that may still decline (< 2 steps).
_IMPERATIVE_VERBS = frozenset(
    {
        "add", "analyze", "build", "check", "compare", "compile", "convert",
        "copy", "create", "delete", "download", "draft", "edit", "email",
        "extract", "find", "fix", "generate", "install", "list", "make",
        "move", "open", "read", "rename", "run", "save", "search", "send",
        "summarize", "test", "update", "verify", "write",
    }
)

#: Clause boundaries: sentence punctuation, newlines, or an explicit sequencing
#: connective. Splitting on bare "and" is intentional — "read X and summarize
#: it" is two actions; "milk and eggs" yields a non-imperative fragment that
#: simply doesn't count.
_CLAUSE_SPLIT_RE = re.compile(r"[.;\n]+|,?\s+(?:and\s+then|then|and)\s+")

# ---------------------------------------------------------------- bulk signal
# v1.174.0. The shape: "Rename all files in this folder to a name that is more
# appropriate given the content in the file." — 96 characters, ONE imperative
# clause. It misses the > 200-char test and the 2-imperative test, so a task
# like it engaged nothing at all. A task that says "all files / every document
# / the whole folder" is not one action; it is one action REPEATED over a
# collection, which is the shape decomposition (and, above it, a worklist)
# exists to carry.
#
# HONEST SCOPE (measured, not assumed). That sentence is what the USER typed,
# and it arrives verbatim only from chat or a direct POST /sessions. The run
# this wave was built on came through POST /projects/{id}/task, which WRAPS it:
# the string actually stored on the session is 487 characters, so
# `is_plausibly_multi_step` was already True for it via the length branch
# before this signal existed. The bulk signal is therefore NOT what would have
# saved that run (nothing in this module would have — see the module docstring:
# its provider serves tools natively). It is here because the same job posted
# from a surface that does not wrap it would otherwise still read as "simple".
#
# The rule, deliberately as dumb as the two above it: a WHOLE-COLLECTION SCOPE
# plus an ACTION VERB. False positives cost one planning call that may still
# decline; false negatives cost a run.
_BULK_QUANTIFIERS = frozenset({"all", "every", "each"})

#: Nouns that name a collection the user works through one item at a time.
_COLLECTION_NOUNS = frozenset(
    {
        "file", "files", "document", "documents", "doc", "docs", "pdf", "pdfs",
        "folder", "folders", "directory", "directories", "subfolder",
        "subfolders", "item", "items", "page", "pages", "image", "images",
        "photo", "photos", "scan", "scans", "invoice", "invoices", "receipt",
        "receipts", "statement", "statements", "record", "records", "entry",
        "entries", "attachment", "attachments", "email", "emails", "message",
        "messages", "row", "rows", "sheet", "sheets", "form", "forms",
        "spreadsheet", "spreadsheets", "report", "reports", "note", "notes",
        "contract", "contracts", "return", "returns", "photo", "screenshot",
        "screenshots",
    }
)

#: Words allowed BETWEEN the quantifier and the noun ("all of the files").
_BULK_FILLERS = frozenset(
    {
        "of", "the", "these", "those", "my", "our", "their", "its", "his",
        "her", "your", "remaining", "other", "new", "old", "existing",
        "single", "individual",
    }
)

#: Verbs that act on a FOLDER as a whole. Deliberately narrow: "rename" and
#: "read" are NOT here, because "rename the file in this folder" is one action
#: on one file — the quantifier branch is what catches "rename ALL files".
_FOLDER_VERBS = frozenset(
    {
        "organize", "organise", "sort", "categorize", "categorise", "classify",
        "index", "catalog", "catalogue", "tidy", "declutter", "process",
        "batch", "dedupe", "deduplicate", "consolidate", "triage", "inventory",
        "audit", "clean",
    }
)

#: A folder/directory the task points at ("this folder", "the directory").
_FOLDER_SCOPE_RE = re.compile(
    r"\b(?:this|that|these|those|the|my|our|entire|whole)\s+"
    r"(?:entire\s+|whole\s+|current\s+)?"
    r"(?:folder|directory|dir|drive)\b"
)

_WORD_RE = re.compile(r"[a-z]+")


def _has_bulk_quantifier(words: list[str]) -> bool:
    """"all files", "every document", "each of the receipts" — a quantifier
    followed (across at most two filler words) by a collection noun. Bare "all"
    does not count: "give it all you've got" is not a bulk job."""
    for i, word in enumerate(words):
        if word not in _BULK_QUANTIFIERS:
            continue
        for candidate in words[i + 1 : i + 4]:
            if candidate in _COLLECTION_NOUNS:
                return True
            if candidate not in _BULK_FILLERS:
                break
    return False


def is_bulk_task(task: str) -> bool:
    """One action REPEATED over a collection (v1.174.0).

    True when the task names a whole-collection scope AND an action verb:

    * quantifier + collection noun — "rename ALL FILES in this folder",
      "summarize EVERY DOCUMENT", "each of the receipts"; or
    * a folder/directory reference paired with either a PLURAL collection noun
      ("rename the files in this folder") or a folder-level verb ("organize
      this folder", "process the directory").

    Pure and cheap — no parsing, no model call, same contract as
    :func:`is_plausibly_multi_step`, which calls it. Exported because the
    supervisor/worklist path wants the same signal (a bulk job is exactly the
    one that should survey once and work a list)."""
    text = (task or "").lower()
    if not text.strip():
        return False
    words = _WORD_RE.findall(text)
    if not words:
        return False
    word_set = set(words)
    verbs = (_IMPERATIVE_VERBS | _FOLDER_VERBS) & word_set
    if not verbs:
        return False
    if _has_bulk_quantifier(words):
        return True
    if _FOLDER_SCOPE_RE.search(text):
        # A folder plus a PLURAL collection noun, or a folder-level verb.
        plural = any(w.endswith("s") and w in _COLLECTION_NOUNS for w in word_set)
        return plural or bool(_FOLDER_VERBS & word_set)
    return False


def is_plausibly_multi_step(task: str) -> bool:
    """Cheap, dumb, documented: a task engages decomposition when it is LONG
    (> :data:`MULTI_STEP_TASK_CHARS` chars), contains 2+ clauses that start
    with an imperative verb, OR is a BULK task (v1.174.0 —
    :func:`is_bulk_task`: one action repeated over a collection). No parsing,
    no model call — the planner itself is the real filter (it may still answer
    "no decomposition needed"). The bulk signal feeds in HERE, the one place
    the other two live, so it engages through exactly the same gates in
    :func:`should_decompose` and inherits both config flags unchanged."""
    text = (task or "").strip()
    if len(text) > MULTI_STEP_TASK_CHARS:
        return True
    clauses = 0
    for seg in _CLAUSE_SPLIT_RE.split(text.lower()):
        # Tolerate list bullets/numbering ("1. write ...", "- read ...").
        first = seg.strip().lstrip("-*0123456789.) ").split(" ", 1)[0]
        if first in _IMPERATIVE_VERBS:
            clauses += 1
            if clauses >= 2:
                return True
    return is_bulk_task(text)


# ------------------------------------------------------------ step budgeting
#: The shipped ``config.max_agent_steps`` value, used ONLY when a stub or
#: legacy config carries nothing parseable (a real ``Config`` always does) —
#: resolving a budget must never raise inside the planner.
DEFAULT_MAX_AGENT_STEPS = 12


def explicit_max_steps(session) -> int | None:
    """Was a budget SET on this session — and what was it (Contract 4)?

    ``None`` = left at the configured default. Tolerates a stub/legacy session
    object with no such attribute — a missing column reads as "not set", never
    as zero. Deliberately stricter than :func:`session_step_budget`: a ``bool``
    is not a budget here, because this answer SIZES things (the plan cap, the
    mini-loop base) and a ``True`` sized to 1 would plan a one-step job. No
    stored session can carry one anyway — the HTTP boundary 422s a boolean
    (``schemas._clean_max_steps``) and ``orchestrator.normalize_max_steps``
    maps it to ``None`` — so this only ever guards hand-built objects."""
    raw = getattr(session, "max_steps", None)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        steps = int(raw)
    except (TypeError, ValueError):
        return None
    return steps if steps > 0 else None


def session_step_budget(config, session) -> int:
    """``session.max_steps or config.max_agent_steps`` — Contract 4's ONE
    resolution, and deliberately NOT a second implementation of it.

    v1.174.0 first shipped this arithmetic TWICE (here and in
    :func:`iron_jarvis.agents.runtime.resolve_max_steps`) and the two copies
    already disagreed — on a boolean, and on a zeroed ``max_agent_steps`` —
    which is exactly the drift the frozen contract existed to prevent. This now
    DELEGATES to the runtime's function, so "how long is this run" has one
    answer by construction: the flat lane and the decomposed lane cannot drift
    apart because there is only one piece of arithmetic left.

    The import is function-local because ``runtime`` imports THIS module at
    module scope; by call time it is fully initialised. The fallback covers a
    stub/legacy config only (see :data:`DEFAULT_MAX_AGENT_STEPS`) — a real
    config parses, and then this returns the runtime's number verbatim,
    including a genuine ``0`` (``mini_loop_budget``'s own ``max(1, …)`` floor
    keeps a mini-loop from being handed zero rounds, exactly as the
    pre-v1.174.0 ``max(1, min(6, max_agent_steps))`` did)."""
    from .runtime import resolve_max_steps

    try:
        return int(resolve_max_steps(session, config))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_MAX_AGENT_STEPS


def plan_step_cap(session) -> int:
    """How many plan steps survive the clip.

    :data:`MAX_PLAN_STEPS` (8) unless this session was given a LARGER explicit
    budget, in which case the cap grows with it up to
    :data:`MAX_PLAN_STEPS_CEILING`. Keyed on the EXPLICIT per-session budget,
    not on the resolved one: a user who raised ``config.max_agent_steps``
    globally in an earlier version must keep getting the plans they have been
    getting, so absent-param behavior stays byte-identical."""
    explicit = explicit_max_steps(session)
    if explicit is None:
        return MAX_PLAN_STEPS
    return max(MAX_PLAN_STEPS, min(MAX_PLAN_STEPS_CEILING, explicit // PLAN_STEP_COST))


def mini_loop_budget(config, session) -> int:
    """Rounds ONE step's mini-loop may spend — a PER-STAGE figure.

    With no per-session budget this is exactly the pre-v1.174.0
    ``max(1, min(6, config.max_agent_steps))``. With one, the mini-loop grows
    with the budget (a share of it, capped) so a raised budget actually reaches
    the loop that does the work, and never exceeds the session's total budget
    on any single step.

    Per step is not per run: a 20-step plan × 24 rounds is 480 model rounds for
    a session whose "Max steps" box says 200, and the flat lane treats that same
    number as a hard ceiling. So when a budget was TYPED, the aggregate is
    enforced where the rounds are actually spent — :func:`execute_plan` measures
    real consumption off ``run.steps`` and stops the plan once that budget is
    gone, reporting the unattempted steps honestly rather than overrunning
    quietly. With no per-session budget nothing is capped in aggregate, because
    ``config.max_agent_steps`` is a per-loop default rather than a promise about
    a whole plan — and that is also what keeps this lane byte-identical."""
    budget = session_step_budget(config, session)
    explicit = explicit_max_steps(session)
    if explicit is None:
        base = MAX_MINI_LOOP_STEPS
    else:
        base = min(
            MAX_MINI_LOOP_CEILING,
            max(MAX_MINI_LOOP_STEPS, explicit // MINI_LOOP_BUDGET_SHARE),
        )
    return max(1, min(base, budget))


# ------------------------------------------------------------- engage decision
def resolved_tool_mode(platform, provider: str | None, model: str | None) -> str | None:
    """The ``tool_use_mode`` the ROUTER would resolve for this session's pick.

    Mirrors the router's v1.131.0 wrap decision by applying the SAME
    ``wrap_prompted_tools`` seam to the manager's adapter, so this check and the
    actual routing can never disagree. Defensive throughout: any resolution
    failure returns ``None`` (→ the flat loop, the safe default). "auto" and
    "mock" are excluded — auto picks per-request, mock is the offline stub —
    and so is a strict model pin (the router deliberately offers tools RAW to a
    pinned pick, no wrap, so prompted mode never applies there)."""
    name = provider or getattr(platform.router, "default_provider", "")
    if not name or name in ("auto", "mock"):
        return None
    if getattr(platform.config, "strict_model_pin", False):
        return None
    try:
        if not platform.providers.available(name):
            return None
        from ..providers.router import wrap_prompted_tools

        adapter = platform.providers.get(name, model)
        caps = wrap_prompted_tools(adapter).capabilities() or {}
        return caps.get("tool_use_mode")
    except Exception:  # noqa: BLE001 — an unresolvable adapter → flat loop
        return None


def should_decompose(platform, session) -> bool:
    """Engage when (a) ``config.decompose_local_tasks`` is on AND (b) the
    resolved adapter serves tools via the prompted scaffold (a short-horizon
    local model) AND (c) the task is plausibly multi-step — OR (v1.166.0)
    when ``config.decompose_all_tasks`` is on AND (c) holds, regardless of
    the resolved ``tool_use_mode`` (the user opting into plan → execute →
    verify → assemble on EVERY provider). Both flags at their defaults keep
    the flat loop byte-for-byte unchanged.

    THE PROMPTED-MODE GATE IS THE REACH LIMIT of this whole module, and
    v1.174.0 did not widen it: a provider that advertises native ``tool_use``
    (the user's ``fleet-custom`` fleet node does) resolves to ``None`` here and
    runs flat, bulk task or not. Widening that is a product decision — "engage
    decomposition for a BULK job on every provider" — not something a signal
    change is allowed to do on its own, so it is a coordinator question, not a
    silent extra branch."""
    if getattr(platform.config, "decompose_all_tasks", False) and is_plausibly_multi_step(
        session.task
    ):
        return True
    if not getattr(platform.config, "decompose_local_tasks", True):
        return False
    # THE COORDINATOR'S ANSWER to the question this docstring poses (v1.174.0):
    # a BULK job decomposes on EVERY provider, native tool-use included. The
    # prompted-mode gate exists because a short-horizon model cannot hold a
    # long plan in its head — but "rename all 26 files in this folder" defeats
    # a flat 12-step loop no matter how good the tool calling is, and that is
    # the measured failure this wave was built from (12 steps, 0 renames). A
    # bulk job is precisely the shape decomposition was written for: one plan,
    # a fresh mini-budget per step, verification between them.
    if is_bulk_task(session.task):
        return True
    if resolved_tool_mode(platform, session.provider, session.model) != "prompted":
        return False
    return is_plausibly_multi_step(session.task)


# ------------------------------------------------------------------ plan model
@dataclass
class PlanStep:
    goal: str
    success_criteria: str = ""
    tools: list[str] = field(default_factory=list)


@dataclass
class StepResult:
    index: int
    goal: str
    ok: bool
    output: str = ""
    reason: str = ""  # verification-failure reason ("" when ok)
    verified: str = ""  # "files" | "model" | "none" | "unverified"
    retried: bool = False
    #: False = the step never RAN (the session's step budget was spent by
    #: earlier steps, v1.174.0). Distinct from ``ok=False``, which means it ran
    #: and failed its gate — reporting "FAILED verification" for work nobody
    #: attempted is the same class of lie as claiming it passed.
    attempted: bool = True


# ------------------------------------------------------------------- one-shots
async def _one_shot(
    runtime,
    run,
    session,
    *,
    system: str,
    messages: list[LLMMessage],
    task_class: str,
    llm: "RoleResolution | None" = None,
) -> str:
    """One completion through the router (its retry/failover spine included),
    with the tokens folded into the run's aggregate + a best-effort
    ``llm.completed`` audit event — the same accounting the loop rounds get, so
    plan/judge/assemble calls aren't invisible on the Usage timeline.

    ``llm`` (v1.135.0 step-aware routing) is the pre-resolved role pair from
    :func:`run_decomposed`; ``None`` keeps the session's own provider/model
    exactly as before. Either way the call goes THROUGH the router — role
    resolution never bypasses failover/health/prompted-wrap semantics."""
    provider = llm.provider if llm is not None else session.provider
    model = llm.model if llm is not None else session.model
    route = await runtime.p.router.complete(
        provider=provider,
        model=model,
        system=system,
        messages=messages,
        tools=[],
        session_id=session.id,
        task_class=task_class,
    )
    resp = route.response
    usage = getattr(resp, "usage", None) or {}
    step_in = int(usage.get("input_tokens", 0) or 0)
    step_out = int(usage.get("output_tokens", 0) or 0)
    run.input_tokens += step_in
    run.output_tokens += step_out
    try:
        from ..eval.pricing import cost_for

        payload = {
            "run_id": run.id,
            "step": run.steps,
            "provider": route.provider,
            "model": route.model,
            "input_tokens": step_in,
            "output_tokens": step_out,
            "cost_usd": cost_for(route.provider, route.model, step_in, step_out),
            "task_class": task_class,
        }
        # Step-aware routing audit (v1.135.0): ADDITIVE payload key on the
        # existing event, only when a role mapping actually changed the pair —
        # the dormant path publishes byte-for-byte the same payload as before.
        if llm is not None and llm.applied:
            payload["role"] = llm.role
        await runtime.p.event_bus.publish(
            EventType.LLM_COMPLETED, payload, session_id=session.id
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        pass
    return resp.text or ""


def _extract_json(text: str) -> tuple[Any | None, str | None]:
    """Parse the first JSON value out of a model reply: tolerate a ```json
    fence or surrounding prose (forward raw_decode from the first brace), but
    never invent structure. Returns ``(value, error)``."""
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw), None
    except (ValueError, TypeError):
        pass
    start = raw.find("{")
    if start < 0:
        return None, "the reply contains no JSON object"
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
        return value, None
    except ValueError as exc:
        return None, f"the reply is not valid JSON ({exc})"


# ------------------------------------------------------------------- planning
_PLAN_SYSTEM_TEMPLATE = (
    "You are a task planner. Split the user's task into {min}-{max} SMALL, "
    "concrete, independently verifiable steps for a coding/office assistant "
    "with tools.\n"
    "Reply with ONLY a JSON object, no prose, exactly this shape:\n"
    '{{"steps": [{{"goal": "<one small concrete action>", '
    '"success_criteria": "<how to check it worked; name exact output file '
    'names when files should exist>", "tools": ["<subset of the available '
    'tool names this step needs>"]}}]}}\n'
    "Rules: each goal must be doable in a couple of tool calls; "
    '"success_criteria" and "tools" may be omitted; if the task is a single '
    'simple action that needs no decomposition, reply {{"steps": []}}.'
)


def _plan_system(cap: int = MAX_PLAN_STEPS) -> str:
    """The planner's system prompt, stating the ACTUAL step cap.

    A hardcoded "2-8" would make a raised :func:`plan_step_cap` inert: the clip
    can only ever SHORTEN a plan, so a model told "at most 8" never produces
    the 12 steps a big job was given room for."""
    return _PLAN_SYSTEM_TEMPLATE.format(min=MIN_PLAN_STEPS, max=cap)


#: The default-cap prompt (unchanged text for a session with no explicit budget).
_PLAN_SYSTEM = _plan_system()


def _parse_plan(text: str) -> tuple[list[PlanStep] | None, str | None]:
    """Strict plan-contract validation. Returns ``(steps, error)`` — ``error``
    is the exact message fed back for the single repair round."""
    value, err = _extract_json(text)
    if err is not None:
        return None, err
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        return None, 'the JSON must be an object with a "steps" array'
    steps: list[PlanStep] = []
    for i, item in enumerate(value["steps"]):
        if not isinstance(item, dict):
            return None, f"steps[{i}] must be an object"
        goal = item.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            return None, f'steps[{i}] needs a non-empty string "goal"'
        criteria = item.get("success_criteria", "")
        if criteria is not None and not isinstance(criteria, str):
            return None, f'steps[{i}].success_criteria must be a string'
        tools = item.get("tools", [])
        if tools is None:
            tools = []
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            return None, f'steps[{i}].tools must be an array of tool-name strings'
        steps.append(
            PlanStep(goal=goal.strip(), success_criteria=(criteria or "").strip(), tools=tools)
        )
    return steps, None


async def plan_task(
    runtime, run, session, agent_def, *, llm: "RoleResolution | None" = None
) -> list[PlanStep] | None:
    """One-shot plan with ONE parse-repair round (the validation error is fed
    back verbatim with the failed reply). Returns ``None`` — "no decomposition
    needed" — for a degenerate plan (0 or 1 steps), an unrepairable reply, or a
    planner call that errors: planning must never break a run, the flat loop is
    always the fallback. Oversized plans are clipped to :func:`plan_step_cap`
    (:data:`MAX_PLAN_STEPS`, raised for a session with a bigger step budget).
    ``llm`` = the "plan" role's resolved pair (None → the session's own)."""
    cap = plan_step_cap(session)
    plan_system = _plan_system(cap)
    user = (
        f"Task:\n{session.task}\n\n"
        f"Available tools: {', '.join(agent_def.tools)}"
    )
    messages = [LLMMessage(role="user", content=user)]
    try:
        text = await _one_shot(
            runtime,
            run,
            session,
            system=plan_system,
            messages=messages,
            task_class="plan",
            llm=llm,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a failed planner → flat loop, honestly
        return None
    steps, err = _parse_plan(text)
    if err is not None:
        messages = messages + [
            LLMMessage(role="assistant", content=text),
            LLMMessage(
                role="user",
                content=(
                    f"Your reply was invalid: {err}. Reply again with ONLY the "
                    'JSON object {"steps": [...]} described before — no prose.'
                ),
            ),
        ]
        try:
            text = await _one_shot(
                runtime,
                run,
                session,
                system=plan_system,
                messages=messages,
                task_class="plan",
                llm=llm,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return None
        steps, err = _parse_plan(text)
        if err is not None:
            return None
    if steps is None or len(steps) < MIN_PLAN_STEPS:
        return None  # degenerate → "no decomposition needed"
    return steps[:cap]


# ------------------------------------------------------------------ verifying
#: File-looking tokens in success criteria. The extension must come from a
#: known document/code set so "3.14", "example.com", or "v1.132" never read as
#: files (a false deterministic FAIL would burn the step's one retry).
_KNOWN_EXTENSIONS = (
    "md|txt|py|js|ts|tsx|json|csv|tsv|toml|yaml|yml|html|css|xml|"
    "docx|doc|xlsx|xls|pptx|ppt|pdf|png|jpg|jpeg|gif|svg|zip|log|sh|ps1|bat"
)
_FILENAME_RE = re.compile(rf"[\w][\w.\-\\/]*\.(?:{_KNOWN_EXTENSIONS})\b", re.IGNORECASE)

_JUDGE_SYSTEM = (
    "You are a strict verifier. Given a step's success criteria and the "
    "worker's output, decide whether the output satisfies the criteria.\n"
    'Reply with ONLY this JSON object, no prose: {"pass": true/false, '
    '"reason": "<one short sentence>"}'
)


def criteria_files(criteria: str) -> list[str]:
    """The file names a success criterion says the workspace should contain.

    URL paths are NOT workspace files: a criterion like "download
    https://example.com/data.csv" would otherwise extract
    ``example.com/data.csv`` — a path that can never exist in the workspace —
    and force a false deterministic FAIL that burns the step's one retry. A
    token sitting right after a ``://`` scheme, or opening with a bare
    ``www.`` domain, is skipped."""
    text = criteria or ""
    out: list[str] = []
    for m in _FILENAME_RE.finditer(text):
        token = m.group(0)
        if text[max(0, m.start() - 3) : m.start()] == "://" or token.lower().startswith("www."):
            continue
        out.append(token)
    return out


async def verify_step(
    runtime,
    run,
    session,
    workspace: Path,
    step: PlanStep,
    output: str,
    *,
    llm: "RoleResolution | None" = None,
) -> tuple[bool, str, str]:
    """The per-step gate. Returns ``(ok, reason, method)``.
    ``llm`` = the "judge" role's resolved pair (None → the session's own).

    (a) DETERMINISTIC first: when the criteria name file(s), existence in the
    workspace IS the gate — cheap and unfoolable, no model call. (b) Otherwise
    a one-shot model judge with the strict ``{"pass", "reason"}`` contract + 1
    repair. An unrepairable judge reply passes the step as "unverified" rather
    than failing it — failing possibly-good work on a broken VERIFIER reply
    would burn the retry for nothing; the honest signal is recorded instead."""
    criteria = (step.success_criteria or "").strip()
    if not criteria:
        return True, "", "none"  # nothing declared → nothing to gate on
    files = criteria_files(criteria)
    if files:
        missing = [f for f in files if not (workspace / f).exists()]
        if missing:
            return (
                False,
                "expected file(s) not found in the workspace: " + ", ".join(missing),
                "files",
            )
        return True, "", "files"
    user = (
        f"Step goal: {step.goal}\n"
        f"Success criteria: {criteria}\n\n"
        f"Worker output:\n{(output or '(no output)')[:4000]}\n\n"
        "Does the output satisfy the criteria?"
    )
    messages = [LLMMessage(role="user", content=user)]
    for round_ in (0, 1):
        try:
            text = await _one_shot(
                runtime,
                run,
                session,
                system=_JUDGE_SYSTEM,
                messages=messages,
                task_class="verify",
                llm=llm,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a dead judge must not fail good work
            return True, "verifier call failed — step not independently verified", "unverified"
        value, err = _extract_json(text)
        if err is None and isinstance(value, dict) and isinstance(value.get("pass"), bool):
            reason = str(value.get("reason") or "")
            return bool(value["pass"]), ("" if value["pass"] else reason or "criteria not met"), "model"
        if round_ == 0:
            problem = err or 'missing boolean "pass"'
            messages = messages + [
                LLMMessage(role="assistant", content=text),
                LLMMessage(
                    role="user",
                    content=(
                        f"Your reply was invalid: {problem}. "
                        'Reply again with ONLY {"pass": true/false, "reason": "..."}.'
                    ),
                ),
            ]
    return True, "verifier reply unparseable — step not independently verified", "unverified"


# ------------------------------------------------------------------ executing
def _render_prior(prior: list[str]) -> str:
    """The compact running context of prior step results, clipped from the
    FRONT so the most recent steps survive the budget."""
    joined = "\n".join(prior)
    if len(joined) > MAX_PRIOR_CONTEXT_CHARS:
        joined = "[...earlier steps trimmed]\n" + joined[-MAX_PRIOR_CONTEXT_CHARS:]
    return joined


def _run_steps(run) -> int:
    """Model rounds this run has spent so far, defensively.

    ``run.steps`` is incremented once per model round INSIDE
    ``runtime.perceive_act`` — the plan/judge/assemble one-shots deliberately do
    not touch it — so a diff across a mini-loop is real consumption, never an
    assumed charge. A stub run object without the attribute reads as 0."""
    try:
        return int(getattr(run, "steps", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _step_prompt(
    task_line: str, prior: list[str], index: int, total: int, step: PlanStep, retry_reason: str
) -> str:
    lines: list[str] = []
    if retry_reason:
        # The verifier's reason is PREPENDED on the one retry so the model reads
        # what to fix before anything else.
        lines.append(f"Your previous attempt failed verification: {retry_reason}")
        lines.append("Fix that in this attempt.\n")
    lines.append(f"Overall task: {task_line}")
    if prior:
        lines.append("\nResults of prior steps:\n" + _render_prior(prior))
    lines.append(f"\nYour CURRENT step ({index + 1} of {total}): {step.goal}")
    if step.success_criteria:
        lines.append(f"Success criteria: {step.success_criteria}")
    lines.append(
        "Do ONLY this step, then reply with a short summary of what you did."
    )
    return "\n".join(lines)


async def execute_plan(
    runtime,
    run,
    session,
    agent_def,
    plan: list[PlanStep],
    *,
    system_prompt: str,
    tool_specs: list[dict[str, Any]],
    session_allow: set[str],
    sink,
    judge_llm: "RoleResolution | None" = None,
) -> list[StepResult]:
    """Execute each step in a FRESH bounded mini-loop through the runtime's
    ``perceive_act`` seam (same routing/streaming/tool machinery, same run
    record + sink). A verify-gate failure earns ONE retry with the verifier's
    reason prepended; a second failure is recorded and the REMAINING steps
    still run (kept simple by design — later steps see the failure in their
    prior-step context and the final answer surfaces it honestly).

    ``judge_llm`` (v1.135.0) reroutes ONLY the verify gate's judge one-shots;
    the mini-loops themselves are the tool-using lane and always keep the
    SESSION's provider/model (``perceive_act`` is deliberately untouched).

    A budget the USER typed is spent HERE, and it is spent ONCE: each step is
    granted at most what the run has left, and when nothing is left the
    remaining steps are recorded as NOT ATTEMPTED instead of quietly running the
    plan past the number the user typed. A session with no explicit budget is
    unchanged from v1.132.0 — per-stage rounds, no aggregate ceiling."""
    p = runtime.p
    workspace = Path(session.workspace_path)
    task_line = " ".join((session.task or "").split())[:MAX_TASK_ONELINER_CHARS]
    # Contract 4 (v1.174.0): the mini-loop spends the SESSION's budget, not the
    # global default — a job given 60 steps must not run 6-round steps.
    max_mini = mini_loop_budget(p.config, session)
    # ...and when the user TYPED a budget, the PLAN AS A WHOLE spends it too. A
    # per-step cap alone let a 20-step plan spend up to 20x24 rounds for a
    # session whose "Max steps" box said 200, while the flat lane treats that
    # same number as a hard ceiling — one label meaning two different things
    # depending on which lane a session landed in. Consumption is MEASURED (see
    # :func:`_run_steps`), never assumed, so a step that finishes in one round
    # is charged one round.
    #
    # Keyed on the EXPLICIT budget: with no per-session budget there is no
    # aggregate ceiling and this lane behaves exactly as it did pre-v1.174.0
    # (per-stage rounds against the global default) — the wave's additive rule.
    # `config.max_agent_steps` is a per-loop default, never a promise about a
    # whole plan, and retro-fitting one would silently shorten every existing
    # local-model plan.
    total_budget = explicit_max_steps(session)
    steps_at_start = _run_steps(run)

    def _left() -> int | None:
        """Rounds left of the session's budget, or ``None`` for "no ceiling"."""
        if total_budget is None:
            return None
        return total_budget - (_run_steps(run) - steps_at_start)

    results: list[StepResult] = []
    prior: list[str] = []
    for index, step in enumerate(plan):
        step_left = _left()
        if step_left is not None and step_left <= 0:
            # Honest degradation: say what did not happen, in the same shape the
            # rest of the plan reports. `attempted=False` keeps assemble from
            # calling unattempted work "FAILED verification".
            reason = (
                f"not attempted — the session's {total_budget}-step budget was "
                f"spent by step {index}"
            )
            results.append(
                StepResult(
                    index=index,
                    goal=step.goal,
                    ok=False,
                    output="",
                    reason=reason,
                    verified="none",
                    attempted=False,
                )
            )
            prior.append(f"Step {index + 1} (NOT ATTEMPTED): {step.goal}")
            await p.event_bus.publish(
                EventType.PLAN_STEP_COMPLETED,
                # ADDITIVE key: an attempted step's payload is byte-identical
                # to pre-v1.174.0, so no consumer has to learn a new shape.
                {"run_id": run.id, "index": index, "ok": False, "attempted": False},
                session_id=session.id,
            )
            continue
        await p.event_bus.publish(
            EventType.PLAN_STEP_STARTED,
            {"run_id": run.id, "index": index, "goal": step.goal},
            session_id=session.id,
        )
        # v1.149.0: the same fact, said to the ONE browser watching this run —
        # "step 2 of 4: read the ledger" instead of an anonymous spinner.
        if sink is not None:
            sink.phase(
                "running", f"step {index + 1} of {len(plan)}: {step.goal[:80]}"
            )
        # The step's hinted tool subset is honored only when EVERY hinted name
        # is in the agent's own set (a subset can narrow, never widen) and it
        # resolves to at least one real spec; otherwise the full set applies.
        step_specs = tool_specs
        hinted = [t for t in (step.tools or []) if t]
        if hinted and all(t in agent_def.tools for t in hinted):
            subset = p.registry.specs(hinted)
            if subset:
                step_specs = subset
        ok, reason, method = True, "", "none"
        output = ""
        retried = False
        retry_reason = ""
        for attempt in (0, 1):
            left = _left()
            if attempt == 1 and left is not None and left <= 0:
                # The retry is real work; without budget for it, say so rather
                # than spending a round the run does not have.
                reason = (
                    f"{reason} (no step budget left to retry — the session's "
                    f"{total_budget}-step budget was spent)"
                )
                break
            # Set only once the retry actually RUNS: a step that was denied its
            # retry for want of budget did not retry.
            retried = retried or attempt == 1
            messages = [
                LLMMessage(
                    role="user",
                    content=_step_prompt(
                        task_line, prior, index, len(plan), step, retry_reason
                    ),
                )
            ]
            finished, text = await runtime.perceive_act(
                run,
                session,
                agent_def,
                system_prompt=system_prompt,
                messages=messages,
                tool_specs=step_specs,
                session_allow=session_allow,
                sink=sink,
                max_steps=(max_mini if left is None else max(1, min(max_mini, left))),
            )
            # A mini-loop that spends its budget is a step OUTCOME (verify will
            # judge it), never a whole-run failure like the flat loop's budget.
            output = text if finished else (
                "(step stopped: mini-loop budget reached before a final answer)"
            )
            if sink is not None:
                sink.phase("verifying", f"checking step {index + 1}")
            ok, reason, method = await verify_step(
                runtime, run, session, workspace, step, output, llm=judge_llm
            )
            if ok or attempt == 1:
                break
            retry_reason = reason
        results.append(
            StepResult(
                index=index,
                goal=step.goal,
                ok=ok,
                output=output,
                reason=("" if ok else reason),
                verified=method,
                retried=retried,
            )
        )
        prior.append(
            f"Step {index + 1} ({'done' if ok else 'FAILED'}): {step.goal} -> "
            + " ".join((output or "(no output)").split())[:400]
        )
        await p.event_bus.publish(
            EventType.PLAN_STEP_COMPLETED,
            {"run_id": run.id, "index": index, "ok": ok},
            session_id=session.id,
        )
    return results


# ------------------------------------------------------------------ assembling
_ASSEMBLE_SYSTEM = (
    "You are finishing a task that was executed as a sequence of steps. "
    "Write the final answer for the user: synthesize the step results into one "
    "clear, concise response. Be HONEST — if any step failed, say so plainly "
    "and describe what did and did not get done. Never claim success for a "
    "failed step."
)


async def assemble(
    runtime,
    run,
    session,
    results: list[StepResult],
    *,
    llm: "RoleResolution | None" = None,
) -> str:
    """Final one-shot synthesizing the step results. NEVER fabricates success:
    when any step failed, a deterministic failure note is appended by CODE —
    honesty is not delegated to the model (it is also instructed to be honest,
    but the note guarantees it regardless of what the model writes). A failed
    or empty assemble call degrades to a plain deterministic summary.
    ``llm`` = the "synthesize" role's resolved pair (None → the session's own)."""
    skipped = [r for r in results if not getattr(r, "attempted", True)]
    failed = [r for r in results if not r.ok and getattr(r, "attempted", True)]
    unverified = [r for r in results if r.ok and r.verified == "unverified"]
    lines = [f"Task: {session.task}", "", "Step results:"]
    for r in results:
        if not getattr(r, "attempted", True):
            # Never say "FAILED" about work that was never attempted.
            lines.append(f"{r.index + 1}. [NOT ATTEMPTED — {r.reason}] {r.goal}")
            continue
        if not r.ok:
            status = f"FAILED ({r.reason})"
        elif r.verified == "unverified":
            status = "OK — not independently verified"
        else:
            status = "OK"
        lines.append(f"{r.index + 1}. [{status}] {r.goal}\n   Output: {r.output[:600]}")
    try:
        text = await _one_shot(
            runtime,
            run,
            session,
            system=_ASSEMBLE_SYSTEM,
            messages=[LLMMessage(role="user", content="\n".join(lines))],
            task_class="assemble",
            llm=llm,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — degrade to the deterministic summary
        text = ""
    if not text.strip():
        text = "\n".join(
            f"Step {r.index + 1} ("
            + (
                "NOT ATTEMPTED"
                if not getattr(r, "attempted", True)
                else ("done" if r.ok else "FAILED")
            )
            + f"): {r.goal} — {r.output[:200]}"
            for r in results
        )
    if skipped:
        # The run ran out of room. Saying which steps never happened is the
        # whole difference between an honest partial result and a summary that
        # reads as if the plan completed.
        text += (
            "\n\nNote — the run reached its step budget: the following step(s) "
            "were NEVER ATTEMPTED and remain undone:\n"
            + "\n".join(f"- step {r.index + 1}: {r.goal}" for r in skipped)
        )
    if failed:
        text += "\n\nNote — the following step(s) FAILED verification:\n" + "\n".join(
            f"- step {r.index + 1}: {r.goal} ({r.reason})" for r in failed
        )
    if unverified:
        # A step that passed only because the VERIFIER was unavailable or
        # unparseable must not read like a verified pass — say so plainly.
        text += (
            "\n\nNote — the following step(s) completed but could NOT be "
            "independently verified (verifier unavailable):\n"
            + "\n".join(f"- step {r.index + 1}: {r.goal}" for r in unverified)
        )
    return text


# ---------------------------------------------------------------- entry point
async def run_decomposed(
    runtime,
    run,
    session,
    agent_def,
    *,
    system_prompt: str,
    tool_specs: list[dict[str, Any]],
    session_allow: set[str],
    sink,
) -> str | None:
    """The decomposed path: plan, then execute + verify each step, then
    assemble. Returns the final answer text, or ``None`` when the planner
    declines (degenerate/unparseable plan) — the caller then runs the flat
    loop unchanged. An error DURING execution propagates exactly as a flat-loop
    error would (the orchestrator's failure handling applies either way)."""
    # Step-aware routing (v1.135.0): resolve each one-shot ROLE exactly once
    # per run — plan/judge/synthesize may name a stronger local model while the
    # mini-loops (the tool-using lane) keep the session's own provider. All
    # fallbacks are the session pair, so with model_roles empty every
    # resolution is a no-op and this path is byte-for-byte unchanged. getattr:
    # bare stub platforms (tests) without config/providers stay dormant too.
    p = runtime.p
    cfg = getattr(p, "config", None)
    provs = getattr(p, "providers", None)
    plan_llm = resolve_role(
        cfg, provs, "plan",
        fallback_provider=session.provider, fallback_model=session.model,
    )
    judge_llm = resolve_role(
        cfg, provs, "judge",
        fallback_provider=session.provider, fallback_model=session.model,
    )
    synth_llm = resolve_role(
        cfg, provs, "synthesize",
        fallback_provider=session.provider, fallback_model=session.model,
    )
    # PHASES (v1.149.0): this pipeline always knew whether it was planning or
    # executing — the client had no way to hear it, so a run that was genuinely
    # thinking showed the same spinner as a run that was stuck. The plan.* EVENTS
    # below are unchanged (the Activity feed consumes them); these frames go to
    # the ONE browser watching this session.
    if sink is not None:
        sink.phase("planning", "working out the steps")
    plan = await plan_task(runtime, run, session, agent_def, llm=plan_llm)
    if plan is None:
        return None
    await runtime.p.event_bus.publish(
        EventType.PLAN_CREATED,
        {"run_id": run.id, "steps": [s.goal for s in plan]},
        session_id=session.id,
    )
    if sink is not None:
        sink.phase(
            "running",
            f"{len(plan)} step{'s' if len(plan) != 1 else ''} planned",
        )
    results = await execute_plan(
        runtime,
        run,
        session,
        agent_def,
        plan,
        system_prompt=system_prompt,
        tool_specs=tool_specs,
        session_allow=session_allow,
        sink=sink,
        judge_llm=judge_llm,
    )
    if sink is not None:
        sink.phase("assembling", "writing up what happened")
    return await assemble(runtime, run, session, results, llm=synth_llm)
