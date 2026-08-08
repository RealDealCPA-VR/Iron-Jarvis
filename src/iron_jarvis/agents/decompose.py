"""Short-horizon decomposition (v1.132.0): plan → execute → verify → assemble.

Frontier models sustain the runtime's flat perceive→act loop; a local model
behind the prompted-tools scaffold (``tool_use_mode: "prompted"``) loses the
thread after a few steps. This module compensates: a plausibly multi-step task
is split into 2–8 small verifiable steps, each executed in a FRESH bounded
mini-loop (the runtime's own routing/streaming/tool machinery via the
``perceive_act`` seam — never a copy), gated by a per-step verifier, and
finally assembled into one honest answer. Everything reuses the SAME AgentRun
record, state transitions, event bus, and stream sink, so the dashboard sees a
normal run — plus additive ``plan.*`` events narrating the decomposition.

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
#: Per-step mini-loop budget (further capped by config.max_agent_steps): small
#: on purpose — the whole point is that each step is small enough to land.
MAX_MINI_LOOP_STEPS = 6
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


def is_plausibly_multi_step(task: str) -> bool:
    """Cheap, dumb, documented: a task engages decomposition when it is LONG
    (> :data:`MULTI_STEP_TASK_CHARS` chars) OR contains 2+ clauses that start
    with an imperative verb. No parsing, no model call — the planner itself is
    the real filter (it may still answer "no decomposition needed")."""
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
    return False


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
    local model) AND (c) the task is plausibly multi-step. Anything else keeps
    the flat loop byte-for-byte unchanged."""
    if not getattr(platform.config, "decompose_local_tasks", True):
        return False
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
_PLAN_SYSTEM = (
    "You are a task planner. Split the user's task into 2-8 SMALL, concrete, "
    "independently verifiable steps for a coding/office assistant with tools.\n"
    "Reply with ONLY a JSON object, no prose, exactly this shape:\n"
    '{"steps": [{"goal": "<one small concrete action>", '
    '"success_criteria": "<how to check it worked; name exact output file '
    'names when files should exist>", "tools": ["<subset of the available '
    'tool names this step needs>"]}]}\n'
    "Rules: each goal must be doable in a couple of tool calls; "
    '"success_criteria" and "tools" may be omitted; if the task is a single '
    'simple action that needs no decomposition, reply {"steps": []}.'
)


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
    always the fallback. Oversized plans are clipped to :data:`MAX_PLAN_STEPS`.
    ``llm`` = the "plan" role's resolved pair (None → the session's own)."""
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
            system=_PLAN_SYSTEM,
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
                system=_PLAN_SYSTEM,
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
    return steps[:MAX_PLAN_STEPS]


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
    SESSION's provider/model (``perceive_act`` is deliberately untouched)."""
    p = runtime.p
    workspace = Path(session.workspace_path)
    task_line = " ".join((session.task or "").split())[:MAX_TASK_ONELINER_CHARS]
    max_mini = max(1, min(MAX_MINI_LOOP_STEPS, p.config.max_agent_steps))
    results: list[StepResult] = []
    prior: list[str] = []
    for index, step in enumerate(plan):
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
                max_steps=max_mini,
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
            retried = True
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
    failed = [r for r in results if not r.ok]
    unverified = [r for r in results if r.ok and r.verified == "unverified"]
    lines = [f"Task: {session.task}", "", "Step results:"]
    for r in results:
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
            f"Step {r.index + 1} ({'done' if r.ok else 'FAILED'}): {r.goal} — {r.output[:200]}"
            for r in results
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
