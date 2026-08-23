"""Agent Runtime + lifecycle (§11, §13).

Runs a single agent's perceive→act loop: ask the model (via the router) for the
next action, execute any tool calls (gated by the permission engine), feed
results back, and repeat until the model finalizes or the step budget is spent.
Lifecycle state transitions are persisted and emitted on the event bus.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentRun, AgentState, AgentType, PermissionMode, Session
from ..envelope.profile import CapabilityProfile
from ..providers.adapters.base import LLMMessage
from ..tools.base import ToolContext
from . import decompose as _decompose
from .types import AgentDefinition

#: Published (string event type, the ``envelope.probe_started`` convention from
#: daemon/routes/envelope.py) when a run's loop actually BENT under a measured
#: capability envelope — ONCE per run, after the run's bends have RESOLVED
#: (deciding to decompose is not decomposing: the planner may decline), payload
#: ``{provider, model, adaptations, source}`` tagged with the session id.
#: NEVER emitted for trusted or unmeasured profiles, and never for a run whose
#: bends all evaporated: adaptation is narrated, never silent — and a run that
#: did not adapt must not narrate one.
ENVELOPE_ADAPTED = "envelope.adapted"

#: How long a PAUSED run holds for a mid-run approval before denying honestly
#: (v1.189.0). Longer than chat's (the user may be on another page — the event
#: reaches the bell and the chat thread), still bounded: a run must never hang
#: forever on a question nobody will answer.
SESSION_APPROVAL_TIMEOUT_S = 300.0

_TERMINAL = {AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}


def is_direct_workspace(config, workspace_path: str | Path | None) -> bool:
    """True when a session runs DIRECTLY in a user-chosen folder (a project's
    in-folder task, created with ``workspace_root=...``), not in a disposable
    dir under ``workspaces_dir``.

    The honest signal: ``create_session`` only ever places a session OUTSIDE the
    managed ``workspaces_dir`` when the caller passed ``workspace_root``, so the
    stored ``Session.workspace_path`` alone answers it — no guessed flag. Shared
    by the runtime (environment prompt) and the orchestrator (rerun) so both
    always agree on which kind of workspace a session has."""
    if not workspace_path:
        return False
    try:
        ws = Path(workspace_path).resolve()
        managed = Path(config.workspaces_dir).resolve()
    except (OSError, ValueError):  # unresolvable path -> the safe default
        return False
    return ws != managed and managed not in ws.parents


#: Cap on tool output fed into the MODEL CONTEXT (the full output still lands in the
#: DB transcript). Without it, a large read/shell/grep result is re-sent on every
#: subsequent step of the loop — O(n^2) token growth at full input price.
_MAX_TOOL_CONTEXT_CHARS = 16000

#: REPEATED-FAILURE BREAKER (contract 2, v1.174.0).
#:
#: The evidence run made 18 tool calls in 12 steps and renamed nothing: five
#: shell calls that each said only "exit 1", then the same PDFs read twice by
#: two different tools. A model with no new information repeats itself, and the
#: loop had nothing that noticed.
#:
#: Scoped to the CALL — the tool plus its arguments — not to the tool. That
#: distinction is the whole safety of the mechanism: an agent legitimately reads
#: several paths that turn out not to exist, and a breaker that disabled
#: `read_file` after three consecutive misses would end the run instead of
#: saving it. What is never legitimate is issuing the IDENTICAL failing call a
#: third time.
_BREAKER_NOTE_AT = 2    # ...failures of the same call earn an honest note
_BREAKER_REFUSE_AT = 3  # ...and the next identical attempt is refused


#: Cap on tools armed FOR THE TASK on top of a definition's static roster
#: (v1.178.0). Matches chat's total-armed cap (`_MAX_ARMED_TOOLS`), but here it
#: bounds ADDITIONS only — see :func:`arm_for_task` for why it is the point.
_AUTO_ARM_CAP = 6

#: The `AUTO_SAFE_TOOLS` members that CREATE OR MODIFY content — files,
#: workbooks, the long-term stores. That frozenset was curated for CHAT, where
#: arming is an interactive per-turn act the user performs with the Auto toggle
#: and can see: `daemon/chat_turn.py` says so in as many words ("the Auto toggle
#: in the UI is the user's standing consent for exactly that set") and goes on to
#: pass the armed names as `session_allow`. An agent run has no toggle, no one
#: watching, and its DEFINITION is its capability contract — so arming here must
#: widen a run's VOCABULARY, never its TIER.
_WRITE_TIER: frozenset[str] = frozenset(
    {
        "write_file",
        "write_document",
        "excel_edit",
        # v1.196.0, LOCK-STEP WITH `tools/autoselect.AUTO_SAFE_TOOLS`. This name
        # used to live only in `_ROSTER_WRITERS` below, and the reason was
        # ARCHAEOLOGY rather than tier: `excel_apply_spec` was not auto-armable,
        # so it could never be *gained* from a task and only ever needed to be
        # RECOGNISED on a roster. It is armable now, and this set is what stops a
        # READ-ONLY definition (REVIEWER, SUPERVISOR) from gaining a writer off
        # its task text — so the two edits are one edit. Landing the autoselect
        # half alone armed `excel_apply_spec` onto the reviewer roster for
        # "review the workbook and apply the firm's standard layout to it" while
        # `set(armed) & _WRITE_TIER` stayed EMPTY: the gate saw nothing, and
        # every assertion in `tests/test_agent_auto_arm_v1178.py` is phrased as
        # "no member of `_WRITE_TIER`", so the whole file stayed green.
        #
        # It belongs here on its merits, not merely for symmetry: it re-saves an
        # EXISTING workbook in place (`safe_path(ctx.workspace, args["path"])`),
        # is `Reversibility.REVERSIBLE` with a real pre-image
        # (`capture_undo` spills the prior bytes + sha256), and defaults to
        # permission "allow" — the same three facts that put `excel_edit` here.
        "excel_apply_spec",
        "redact_pii",
        "pdf_arrange",
        "pdf_split",
        "ltm_append",
        "remember_preference",
    }
)

#: A definition holding one of these is already a WRITING agent, so the tier is
#: nothing new and the gate below is a no-op. Every built-in except REVIEWER and
#: SUPERVISOR qualifies — both of those are deliberately read-only (see the
#: reviewer's own "DELIBERATELY NO `mcp:*`" note in `agents/types.py`, which
#: reasons about exactly this blast radius) — so the gate costs the feature
#: nothing on the six types that do the work.
#: (``excel_apply_spec`` moved UP into `_WRITE_TIER` in v1.196.0; the union
#: keeps it here, so a definition carrying it is still recognised as a writing
#: agent and this gate stays a no-op for it exactly as before.)
_ROSTER_WRITERS: frozenset[str] = _WRITE_TIER | {
    "edit_file",
    "rename_file",
    "memory_write",
}


def arm_for_task(
    platform,
    task: str,
    roster: list[str],
    *,
    cap: int = _AUTO_ARM_CAP,
    max_tools: int | None = None,
    adaptations: "list[str] | None" = None,
):
    """*roster* plus up to *cap* capability-selected tools for THIS task.

    ``max_tools`` (v1.202.0, Wave B1) is the CAPABILITY ENVELOPE's arming
    budget — ``CapabilityProfile.max_tools()`` for the model this run will
    answer with. It takes the same contract ``tools/autoselect
    .select_auto_tools`` gives its own ``max_tools`` parameter: ``None`` (a
    trusted or unmeasured profile — the helper itself answers None for both)
    leaves the arming byte-identical to v1.201.0, and an int bounds the run's
    TOTAL tool count while the roster is CONSENT and is never dropped — a
    definition's explicit grant survives however weak the model measured, so
    only the auto ADDITIONS shrink (to ``max_tools - len(roster)``, floored at
    zero). The budget is applied here as a slice of the selector's ranked
    output rather than passed down as its ``max_tools`` argument, for two
    reasons: the slice lands AFTER the registry-presence filter (so a capped
    run still gets tools this install actually serves, instead of paying its
    tiny budget on ghost names — ``select_auto_tools``'s own doc pins the cap
    as "the SAME ranked slice", so the order is identical either way), and the
    slice is the one place that can HONESTLY report whether the cap bent
    anything: when it drops at least one selected tool, ``"tool_cap:<n>"`` is
    appended to *adaptations* (caller-owned list; the runtime folds it into
    the single ``envelope.adapted`` event). A cap that dropped nothing bent
    nothing and reports nothing.

    FIVE RELEASES IN A ROW FAILED THE SAME WAY: the tool the run needed was not
    on the definition's roster, so it did not exist — `rename_file` (v1.177.2),
    the worklist (v1.177.0), `view_image` (v1.174.0), `workflow_list`
    (v1.172.0), `history_search` (v1.142.0). Each was repaired by editing
    `agents/types.py` afterwards, which only ever fixes the case that already
    burned. Chat has not had this problem: it reads the request and arms what it
    needs every turn (`tools/autoselect.select_auto_tools`, called from
    `daemon/chat_turn.py`). The agent lane resolved its roster at DEFINITION
    time and never looked at the task text at all. This is that same reading,
    applied once at run start.

    ADDITIVE BY CONSTRUCTION. The roster rides at the front unchanged and only
    ever gains names; `exclude` keeps a tool it already grants from being
    re-listed. Candidates come exclusively from `autoselect.AUTO_SAFE_TOOLS`
    (that module enforces it), so no run can pick up `shell`, `edit_file`,
    `browse`, `web_action` or `mcp_call` this way — arming those stays a
    definition-time/consent decision. That frozenset is not the whole safety
    argument though: it also carries nine tools that WRITE (see `_WRITE_TIER`),
    which is a per-turn consent decision in chat and would be an unwitnessed
    capability grant here, so a definition holding no writer never gains one.

    THE CAP IS THE POINT, not a token nicety. The default provider on this
    machine is a LOCAL model, and the evidence run behind v1.174.0 shows the
    failure mode: five `shell` calls where `read_file` was sitting right there.
    Every extra schema in the prompt is another wrong door, so a run gets the
    few tools its task actually argues for, never the whole safe set.

    Offline: `select_auto_tools` is pure regex scoring over the task string —
    no model call, no I/O — and returns ``[]`` for a task with no signal, which
    leaves the armed list byte-identical to the roster that shipped before.

    NOT CHEAP ANY MORE, AND IT IS OFFLOADED (v1.196.0). This docstring used to
    end "nothing to offload", and that stopped being true when the scorer gained
    the imperative-position test in front of fourteen rules: a task string with a
    long run of whitespace measured ~200ms, and `Runner.run` is `async`, so that
    was the whole daemon parked. The caller hops this to a worker thread — the
    same treatment both chat lanes give their own scorer calls. Once per RUN
    rather than per turn, so the frequency is low; the cost when it lands is not.
    """
    # An EMPTY roster is not a roster to widen: a definition that grants no
    # tools is a text-only run, and arming file/document tools onto it would be
    # a capability grant nobody asked for. A DYNAMIC agent record whose tools
    # list is empty means "not specified" and is resolved to its base type's
    # roster before a definition ever reaches the runtime, so what arrives here
    # empty is a deliberate zero.
    if not roster or cap <= 0:
        return roster
    skip = set(roster)
    if not (skip & _ROSTER_WRITERS):
        # TIER GATE. MEASURED on the reviewer roster (13 read-only tools, no
        # writer of any kind): "review the draft report and save a corrected
        # version as a docx" armed `write_document` + `write_file`, "fix the
        # formulas in the sheet" armed `excel_edit`, "redact the pii" armed
        # `redact_pii`, "merge these pdfs" armed `pdf_arrange`/`pdf_split` —
        # and every one of those defaults to permission "allow"
        # (`core/config.default_permissions`), so nothing downstream would have
        # stopped the write. Reading a task is not consent to author files with
        # an agent the user chose *because* it only reads.
        #
        # Excluded BEFORE the selector rather than filtered after it, so the cap
        # still fills with `cap` tools this run may actually use instead of
        # quietly returning fewer.
        skip |= _WRITE_TIER
    try:
        from ..tools.autoselect import select_auto_tools

        extra = [
            name
            for name in select_auto_tools(task or "", exclude=skip, cap=cap)
            # Drop anything this install does not actually serve. `registry
            # .specs` already filters by `t.name in allow`, so an unknown name
            # cannot produce a spec — this keeps the returned LIST honest too,
            # and mirrors chat's `if d.platform.registry.get(t)` filter.
            if platform.registry.get(name) is not None
        ]
    except Exception:  # noqa: BLE001 — arming is an optimisation; a failure
        # must leave the run with exactly the roster it had before.
        return roster
    if max_tools is not None and extra:
        # THE ENVELOPE CAP (v1.202.0, B1). Roster = consent, never dropped —
        # even when the roster alone already exceeds the budget the additions
        # simply go to zero. Recorded ONLY when a tool was actually dropped:
        # "adapted" must mean the loop bent, not that a budget existed.
        room = max(0, max_tools - len(roster))
        if len(extra) > room:
            extra = extra[:room]
            if adaptations is not None:
                adaptations.append(f"tool_cap:{max_tools}")
    return [*roster, *extra] if extra else roster


def resolve_run_envelope(platform, session) -> tuple[str, str, CapabilityProfile]:
    """The capability envelope consulted for THIS run (v1.202.0, Wave B).

    Returns ``(provider, model, profile)``. Never raises: every resolution
    failure answers the default floor profile, which is UNMEASURED and so
    bends nothing (``max_tools()`` -> None, and the decompose consult in
    :func:`should_decompose_enveloped` requires ``is_measured()``).

    HOW THE RUNTIME KNOWS ITS MODEL. ``orchestrator.create_session`` stamps
    every session with a provider and a model at creation time (falling back
    to ``config.default_provider`` / ``config.default_model`` when the caller
    picked nothing), so on the production path ``session.model`` is the
    answer and the consult simply reads it. BE HONEST ABOUT THE EDGE: for a
    session created without an explicit model on a NON-default provider, the
    stamped model is the CONFIG default, while the model that actually
    answers is only truly resolved inside ``router.complete()`` (the
    provider's factory decides — model-aware factories honor the stamp,
    legacy ones serve their own default). The consult follows the stamp — the
    same "consult with the default provider+model from config" resolution the
    integration plan prescribes for the default route — and for stub sessions
    that carry NO model at all it falls back to the adapter's advertised
    ``capabilities()["model"]`` (the same defensive resolution
    ``decompose.resolved_tool_mode`` performs at this exact point in the
    flow), then to the config default. A wrong guess is safe in both
    directions: an unknown (provider, model) pair loads the unmeasured floor
    and the loop stays byte-identical, and it cannot borrow a weak profile
    measured for a different model unless the ids actually collide. Trusted
    providers skip the adapter probe entirely — their profile ignores the
    model's identity by construction, so there is nothing to resolve."""
    provider = str(getattr(session, "provider", "") or "").strip()
    if not provider:
        try:
            provider = str(getattr(platform.router, "default_provider", "") or "")
        except Exception:  # noqa: BLE001 — a router stub without the property
            provider = ""
        if not provider:
            provider = str(getattr(platform.config, "default_provider", "") or "")
    model = str(getattr(session, "model", "") or "").strip()
    manager = getattr(platform, "providers", None)
    try:
        trusted = bool(manager is not None and manager.is_trusted_provider(provider))
    except Exception:  # noqa: BLE001
        trusted = False
    if not model and not trusted and provider and provider != "auto":
        try:
            if manager is not None and manager.available(provider):
                caps = manager.get(provider, None).capabilities() or {}
                model = str(caps.get("model") or "").strip()
        except Exception:  # noqa: BLE001 — an unresolvable adapter → default
            model = ""
    if not model:
        model = str(getattr(platform.config, "default_model", "") or "")
    try:
        if manager is not None:
            return provider, model, manager.capability_profile(provider, model)
    except Exception:  # noqa: BLE001 — capability_profile never raises, but a
        pass  # test stub standing in for the manager may not carry it at all
    return provider, model, CapabilityProfile(model_id=model, provider=provider)


def should_decompose_enveloped(platform, session, profile) -> tuple[bool, bool]:
    """The decompose gate with the capability envelope consulted (Wave B2).

    Returns ``(engage, envelope_caused)``. Wraps — never edits —
    ``decompose.should_decompose``, so every direct consumer of that gate
    keeps its exact v1.201.0 contract; this is the runtime's own call-site
    layer, and the runtime is the only production caller of the gate.

    PRECEDENCE (the documented contract):

    1. Today's gate speaks first, unchanged: ``decompose_all_tasks``, the
       ``decompose_local_tasks`` flag, the bulk-task reason, the
       prompted-mode reason. When IT engages, ``envelope_caused`` is False —
       the run would have decomposed with no envelope at all, nothing bent,
       and no ``envelope.adapted`` event may claim otherwise.
    2. ``decompose_local_tasks`` is the GLOBAL override in BOTH directions.
       False = NEVER: the envelope reason is silenced along with the bulk and
       prompted ones (the one standing exception is the explicit
       ``decompose_all_tasks`` opt-in, which already outranks the flag inside
       step 1 today — preserved byte-identically). True (the default) =
       the envelope reason below is PERMITTED, never forced.
    3. The envelope reason: a MEASURED (probed/partial/tuned with a
       ``probed_at`` stamp), untrusted profile whose ``needs_decomposition()``
       answers True routes a plausibly multi-step task through the decomposed
       lane even where today's gate runs flat — e.g. a weak model behind a
       NATIVE tool-use endpoint, the exact reach limit ``should_decompose``'s
       own docstring records. The ``is_measured()`` gate is load-bearing: an
       unmeasured untrusted floor profile answers ``needs_decomposition() ==
       True`` by conservative construction, and consulting it raw would flip
       every unprobed local provider into the decomposed lane on day one —
       the envelope only ever bends on EVIDENCE (the same rule
       ``max_tools()`` enforces internally). Trusted profiles never reach the
       consult: frontier sees zero change. A simple task stays flat on every
       profile — plan/verify on a one-liner spends a planner round to learn
       there is nothing to plan, the same reason the prompted reason requires
       ``is_plausibly_multi_step``. Note one deliberate divergence: unlike
       the prompted reason, the envelope reason fires under
       ``strict_model_pin`` too — the pin is a statement about MODEL CHOICE
       (never substitute), decomposition substitutes nothing, and the flag in
       step 2 remains the way to force the flat loop.

    This is also what finally routes the SUPERVISOR through decompose.py's
    plan/verify engine: ``run_supervised`` drives ``AgentRuntime.run``, which
    consults this gate with no agent-type branch, so a supervisor session
    whose measured envelope demands decomposition takes the same lane every
    other type does (its ``delegate``/worklist specs ride along unchanged).
    """
    if _decompose.should_decompose(platform, session):
        return True, False
    if not getattr(platform.config, "decompose_local_tasks", True):
        return False, False
    try:
        if (
            not profile.is_trusted()
            and profile.is_measured()
            and profile.needs_decomposition()
            and _decompose.is_plausibly_multi_step(getattr(session, "task", "") or "")
        ):
            return True, True
    except Exception:  # noqa: BLE001 — the envelope must never break the gate
        return False, False
    return False, False


#: Bounds for the `# Team` block (v1.193.0). A department is a TEAM, not a
#: directory: a deep delegation tree can hold dozens of runs, and listing them
#: all would spend more prompt budget than the block earns back. The roster is
#: already bounded at the store (`_MAX_ROSTER_RUNS`); this is the prompt's own
#: ceiling, and the overflow is REPORTED rather than silently dropped.
_TEAM_LIST_CAP = 10
#: Distinct sender names named in the mail line before it collapses to "+N more".
_TEAM_SENDERS_CAP = 4


def board_tool_names() -> frozenset[str]:
    """The blackboard tool names, taken from the tool CLASSES that serve them.

    The gate below and the instruction inside the block must both name the same
    tools the registry actually holds — a `# Team` block telling an agent to
    call `blackboard_read` when the tool has been renamed is worse than no block
    at all. Reading the names off the classes makes that drift impossible.
    """
    from ..blackboard.tools import (
        BlackboardPostTool,
        BlackboardReadTool,
        MessageAgentTool,
    )

    return frozenset(
        {BlackboardPostTool.name, BlackboardReadTool.name, MessageAgentTool.name}
    )


def holds_board_tools(tool_specs) -> bool:
    """True when THIS run is actually carrying a blackboard tool.

    WHO SEES THE TEAM BLOCK is derived from the specs the model is about to be
    offered, never from the agent type. `_COLLAB_TOOLS` is on every builtin
    definition (builder, reviewer, researcher, memory, maintainer, automation —
    not just the two coordinator types that get `roster_block`), so an agent-type
    allowlist here would repeat the exact defect this unit exists to close: six
    agent types carrying `blackboard_post`/`blackboard_read`/`message_agent`
    while being told nothing about any teammate existing. Deriving from the
    specs also means the block cannot outlive the capability — a definition that
    drops the tools, or an install whose registry never registered them, renders
    nothing.
    """
    try:
        wanted = board_tool_names()
        return any(str(s.get("name") or "") in wanted for s in tool_specs)
    except Exception:  # noqa: BLE001 — an unreadable spec list is just "no"
        return False


def teammates_block(platform, session_id: str, agent_run_id: str) -> str:
    """The `# Team` block: who I am, who is with me, and whether I have mail.

    THE BLACKBOARD WAS NEVER IN A PROMPT. An agent learned a board existed only
    from a tool description, was never told its own run id (so it could not tell
    a teammate how to reach it), and a directed message sat unread forever
    unless the recipient spontaneously polled. This is the missing half of the
    substrate: presence + an unread signal, stated once where the model reads.

    SYNCHRONOUS ON PURPOSE, and therefore only ever called through
    `asyncio.to_thread` — it does three SQLite reads (the department walk, the
    roster, the directed rows) and a wave-1 reviewer measured `roster()` alone
    at up to 47ms in a pathological tree. On the daemon's single event loop that
    is 47ms of every request in the app.

    ONCE PER RUN, at prompt assembly — not per step. That is what keeps it cheap
    (one read per run, not one per model call), and it also makes "unread"
    exactly true rather than approximately true: a run that has not started has
    read NOTHING, so every message addressed to it is genuinely unread. There is
    no read-receipt anywhere in the app, so any per-step count would be a number
    we cannot honestly define. HONEST LIMIT: mail that arrives mid-run is not
    announced here; `blackboard_read` is how a running agent checks again.

    A SOLO RUN RENDERS NOTHING. No teammates and no mail means no board worth
    mentioning, and a lone agent must not grow a "Team" section advertising a
    feature it is not using — the same rule the dashboard's TeamTree follows
    ("a solo session must not grow an empty Team box").

    Bounded and NEVER RAISES, exactly like `roster_block` / `memory_index_block`
    beside it: a failure omits the block, it never fails the run.
    """
    try:
        store = getattr(platform, "blackboard", None)
        if store is None or not agent_run_id:
            return ""
        # `resolve_board_id` is the ONE department walk (shared with the
        # worklist). Never re-derive it here.
        board_id = store.board_id_for(session_id, agent_run_id)
        roster = store.roster(board_id)
        mine = next(
            (e for e in roster if e.get("agent_run_id") == agent_run_id), None
        )
        my_name = str((mine or {}).get("handle") or "") or store.name_for(agent_run_id)
        teammates = [e for e in roster if e.get("agent_run_id") != agent_run_id]
        # "Addressed to me" is the same predicate `blackboard_read(to_me=true)`
        # uses — the run id, or my name on a row carrying no id — so the count
        # here and what the tool returns can never disagree. A row I wrote to
        # myself is not mail.
        mail = [
            r
            for r in store.list(
                board_id,
                to_agent=agent_run_id,
                to_name=my_name or None,
            )
            if getattr(r, "author", "") != agent_run_id
        ]
        if not teammates and not mail:
            return ""
        lines = [
            "# Team",
            f"You are `{my_name or 'agent'}` and your agent run id is "
            f"`{agent_run_id}`, on department board `{board_id}`. Teammates "
            "reach you by that name or that run id.",
        ]
        if teammates:
            shown = teammates[:_TEAM_LIST_CAP]
            lines.append(
                "On this board with you (address one by NAME — or by run id when "
                "two share a name):"
            )
            for t in shown:
                state = str(t.get("state") or "")
                lines.append(
                    f"- {t.get('handle') or 'agent'} (run id "
                    f"{t.get('agent_run_id')}{', ' + state if state else ''})"
                )
            if len(teammates) > len(shown):
                lines.append(
                    f"- ...and {len(teammates) - len(shown)} more — "
                    "blackboard_read lists the full roster."
                )
        if mail:
            senders: list[str] = []
            for r in mail:
                who = str(getattr(r, "author_name", "") or "") or str(
                    getattr(r, "author", "") or ""
                )
                if who and who not in senders:
                    senders.append(who)
            named = ", ".join(senders[:_TEAM_SENDERS_CAP])
            if len(senders) > _TEAM_SENDERS_CAP:
                named += f" +{len(senders) - _TEAM_SENDERS_CAP} more"
            read_tool = "blackboard_read"
            try:
                from ..blackboard.tools import BlackboardReadTool

                read_tool = BlackboardReadTool.name
            except Exception:  # noqa: BLE001
                pass
            lines.append(
                f"YOU HAVE MAIL: {len(mail)} message(s) on this board are "
                f"addressed to you{f' (from {named})' if named else ''}. Call "
                f"`{read_tool}` with to_me=true and read them before you start."
            )
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — presence must never break a run
        return ""


def call_signature(name: str, arguments: dict | None) -> str:
    """A canonical identity for "the same call again".

    Argument ORDER must not create a new identity (a model re-emitting the same
    JSON object with keys shuffled is repeating itself), hence sort_keys; and an
    unserialisable argument degrades to its repr rather than raising inside the
    tool loop.
    """
    try:
        canon = json.dumps(arguments or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canon = repr(arguments)
    return f"{name}({canon[:2000]})"


def resolve_max_steps(session, config) -> int:
    """This run's step budget: the session's own, else the configured default.

    Contract 4 (v1.174.0). ``getattr`` rather than ``session.max_steps`` because
    the column is optional and every caller that never sets it — every existing
    one — must land on exactly today's number. A non-positive or unparseable
    value means "unset", never "zero steps": a run that can take no action at
    all is not a budget, it is a broken session.
    """
    raw = getattr(session, "max_steps", None)
    try:
        wanted = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        wanted = 0
    return wanted if wanted > 0 else int(config.max_agent_steps)


def _effective(messages, system_prompt: str, summary: str, covers: int):
    """Apply an existing compaction WITHOUT touching the caller's list.

    ``messages[0]`` (the task) always rides at the front verbatim; *covers*
    counts messages after it that the summary has absorbed. Returns
    ``(messages, system)`` for this step only — the loop keeps appending to the
    real list, which stays the run's full history and what gets persisted.
    """
    if not summary or covers <= 0 or not messages:
        return messages, system_prompt
    head, tail = messages[:1], messages[1 + covers :]
    return [*head, *tail], f"{system_prompt}\n\n{summary}"


class AgentRuntime:
    def __init__(self, platform) -> None:
        self.p = platform

    async def _maybe_compact(
        self,
        session,
        *,
        messages,
        system_prompt: str,
        summary: str,
        covers: int,
        futile: bool,
        window,
        sink,
    ):
        """Compact this run's older steps when the window is nearly full.

        Returns the (possibly unchanged) ``(summary, covers)``. Auto-only and
        never asks: there is no human attached to a running agent, and the
        alternative at this fill level is a step that silently loses the
        beginning of the run.

        The summary is checked against this session's EXECUTION LEDGER before it
        is allowed anywhere near the prompt — ``outcome.session_result`` derives
        what the run actually did from ``ToolInvocation`` + ``UndoJournal``, so a
        model claiming a file it never wrote has that claim removed rather than
        fed back to itself as history.

        Every failure path returns the input unchanged: no real model, nothing
        big enough to cover, a provider error, or a summary that survived
        verification empty. The deterministic recap then handles overflow
        exactly as it did in v1.152.0.
        """
        from ..context import compaction as _C

        if futile:
            return summary, covers, futile
        try:
            from ..daemon.chat_turn import _compaction_enabled, _compaction_thresholds
            from ..context.budget import estimate_tokens

            deps = SimpleNamespace(platform=self.p)
            if not _compaction_enabled(deps):
                return summary, covers, futile
            _suggest, auto_at = _compaction_thresholds(deps)

            eff_messages, eff_system = _effective(
                messages, system_prompt, summary, covers
            )
            raw = estimate_tokens(eff_system) + sum(
                estimate_tokens(getattr(m, "content", "") or "") + 4
                for m in eff_messages
            )
            if _C.pressure(raw, int(window or 0)) < auto_at:
                return summary, covers, futile

            pairs, new_covers = _C.agent_coverage(messages, covered=covers)
            if not pairs or new_covers <= covers:
                return summary, covers, futile

            complete = self.p_compaction_complete()
            if complete is None:
                return summary, covers, futile

            paths, tools = _C.ledger_facts(self.p.engine, session.id)
            out = await _C.compact_messages(
                pairs,
                complete=complete,
                ledger_paths=paths,
                ledger_tools=tools,
                trigger="auto",
                # The summary being REPLACED rides along: coverage always starts
                # from the beginning, so without this the new summary would
                # silently drop everything the old one said.
                prior=summary,
            )
            if not out.ok:
                return summary, covers, futile

            # FUTILITY GUARD. When the task alone dominates the window, covering
            # every step still leaves pressure above the ceiling — and the next
            # few steps would each buy another useless model call. Measure the
            # result; if it did not get us under, take this one and stop trying.
            # The planner's trim-and-clip path then handles the overflow, which
            # is honest about what it is doing.
            after_messages, after_system = _effective(
                messages, system_prompt, out.summary, new_covers
            )
            after_raw = estimate_tokens(after_system) + sum(
                estimate_tokens(getattr(m, "content", "") or "") + 4
                for m in after_messages
            )
            futile = _C.pressure(after_raw, int(window or 0)) >= auto_at

            if sink:
                sink.phase(
                    "running",
                    f"compacted {new_covers - covers} earlier message(s) to fit "
                    f"the context window",
                )
            await self.p.event_bus.publish(
                EventType.CONTEXT_COMPACTED,
                {
                    "run_id": getattr(session, "id", ""),
                    "covers": new_covers,
                    "stripped": out.stripped,
                    "trigger": "auto",
                    "provider": out.provider,
                    "model": out.model,
                },
                session_id=session.id,
            )
            return out.summary, new_covers, futile
        except Exception:  # noqa: BLE001 — compaction is an optimisation; a
            # failure must leave the run exactly as it was.
            return summary, covers, futile

    def p_compaction_complete(self):
        """The one-shot completion callable, or None when only the mock exists.

        The daemon builds it (``d._compaction_complete``); the runtime reaches
        it through the platform so a bare ``AgentRuntime`` in a unit test simply
        gets None and skips compaction.
        """
        factory = getattr(self.p, "_compaction_complete", None)
        if factory is None:
            return None
        try:
            return factory()
        except Exception:  # noqa: BLE001
            return None

    def _save(self, run: AgentRun) -> None:
        with session_scope(self.p.engine) as db:
            db.merge(run)
            db.commit()

    def _project_context(self, session: Session) -> str:
        """The context-spine block for a project-tagged session: the project's
        brief + the last few sibling sessions' outcomes (bounded)."""
        from sqlmodel import select

        from ..core.models import Project

        with session_scope(self.p.engine) as db:
            project = db.get(Project, session.project_id)
            if project is None:
                return ""
            siblings = list(
                db.exec(
                    select(Session)
                    .where(
                        Session.project_id == session.project_id,
                        Session.id != session.id,
                    )
                    .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
                    .limit(5)
                )
            )
        lines = [
            "\n\n# Project context",
            f"You are working within the user's project: {project.name}",
        ]
        if getattr(project, "instructions", "").strip():
            lines.append(
                "Project instructions (follow these):\n"
                + project.instructions.strip()[:2000]
            )
        if project.brief.strip():
            lines.append(f"Project brief: {project.brief.strip()[:2000]}")
        if project.root.strip():
            lines.append(f"Project folder: {project.root.strip()}")
        # Project KNOWLEDGE — the whole base when small, else the parts relevant
        # to this task (cosine over the stored vectors). Best-effort.
        try:
            from ..projects.knowledge import ground as _ground

            know = _ground(self.p, session.project_id, session.task)
            if know.strip():
                lines.append("Project knowledge (reference material):\n" + know)
        except Exception:  # noqa: BLE001 — grounding must never break a run
            pass
        recent = [
            f"- [{s.status.value}] {s.task[:80]}: {(s.summary or '(no summary)')[:160]}"
            for s in siblings
        ]
        if recent:
            lines.append("Recent activity in this project (newest first):")
            lines.extend(recent)
        lines.append(
            "Use this context when relevant; stay consistent with prior work in the project."
        )
        return "\n".join(lines)

    async def _set_state(
        self, run: AgentRun, state: AgentState, session_id: str
    ) -> None:
        prev = run.state
        run.state = state
        if state in _TERMINAL:
            run.finished_at = utcnow()
        self._save(run)
        await self.p.event_bus.publish(
            EventType.AGENT_STATE_CHANGED,
            {"run_id": run.id, "from": prev.value, "to": state.value},
            session_id=session_id,
        )

    async def _route_stream(self, **kwargs):
        """FX-01 token-stream passthrough for the perceive->act loop.

        Yields the router's streaming frames -- ``{"type": "text", "text": <delta>}``
        deltas followed by a terminal ``{"type": "final", "response": LLMResponse,
        "provider": str, "model": str}``. When the router has no ``stream`` method
        yet (it is added by the FX-01 central wiring), degrade GRACEFULLY to a single
        text chunk over ``complete()`` so the non-streaming path is exactly preserved
        and the offline suite stays green regardless of merge order. Same kwargs as
        ``router.complete``.
        """
        streamer = getattr(self.p.router, "stream", None)
        if streamer is not None:
            async for ev in streamer(**kwargs):
                yield ev
            return
        route = await self.p.router.complete(**kwargs)
        if route.response.text:
            yield {"type": "text", "text": route.response.text}
        yield {
            "type": "final",
            "response": route.response,
            "provider": route.provider,
            "model": route.model,
        }

    async def run(
        self,
        session: Session,
        agent_def: AgentDefinition,
        parent_id: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            session_id=session.id,
            parent_id=parent_id,
            agent_type=agent_def.type,
            provider=session.provider,
            model=session.model,
            state=AgentState.CREATED,
        )
        self._save(run)
        # FX-01 side-channel: resolve the ephemeral per-run stream sink (token
        # deltas + live tool frames -> SSE). A no-op when no browser is subscribed,
        # and absent entirely when the platform exposes no stream hub.
        hub = getattr(self.p, "streams", None)
        sink = hub.sink(session.id, run.id) if hub is not None else None

        await self._set_state(run, AgentState.INITIALIZING, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_STARTED,
            {"agent": agent_def.type.value, "run_id": run.id, "task": session.task},
            session_id=session.id,
        )
        await self._set_state(run, AgentState.RUNNING, session.id)

        workspace = Path(session.workspace_path)
        # Per-session tool grant (bundle-approved up front): these perm_keys are
        # treated as allowed for THIS session, so an "ask" tool the user opted
        # into doesn't fail-close in the daemon. Never lifts a hard "deny".
        session_allow: set[str] = set()
        try:
            raw = json.loads(getattr(session, "allow_tools_json", "") or "[]")
            if isinstance(raw, list):
                session_allow = {str(t) for t in raw if t}
        except (ValueError, TypeError):
            session_allow = set()
        messages: list[LLMMessage] = [LLMMessage(role="user", content=session.task)]
        # ON-DEMAND `delegate` wiring (v1.166.0): the planner now carries the
        # delegate tool too, and only `run_supervised` used to register it — a
        # delegate-carrying definition driven straight through this runtime
        # would otherwise advertise a tool the registry cannot serve. Same
        # construction as supervisor.py; lazy import to avoid an
        # agents-package cycle at module load.
        if "delegate" in agent_def.tools and self.p.registry.get("delegate") is None:
            from .delegate_tool import DelegateTool

            self.p.registry.register(DelegateTool(self.p))
        # CAPABILITY ARMING (v1.178.0): the roster is fixed at authoring time,
        # what this task needs is not. Build a NEW list — the built-in
        # definitions are module-level singletons (`agents/types._DEFINITIONS`),
        # so appending to `agent_def.tools` in place would rewrite that type's
        # roster for the life of the process (the `_spec_with_store_as`
        # deep-copy lesson). Both lanes below consume this one `tool_specs`, so
        # the decomposed run is armed identically to the flat one.
        # OFF THE EVENT LOOP (v1.196.0): `arm_for_task` runs the same CPU-bound
        # regex scorer both chat lanes hop to a thread for. See its docstring.
        # CAPABILITY ENVELOPE (v1.202.0, Wave B): the run's measured profile is
        # resolved ONCE here and consulted twice — the arming cap below and the
        # decompose gate further down — with every bend collected into
        # `adaptations` for the single `envelope.adapted` event. Trusted and
        # unmeasured profiles bend NOTHING by construction (`max_tools()`
        # answers None, the decompose consult requires `is_measured()`), so
        # every pre-envelope run is byte-identical. The list is filled inside
        # the worker thread and read only after `to_thread` returns — no race.
        env_provider, env_model, env_profile = resolve_run_envelope(self.p, session)
        adaptations: list[str] = []
        tool_specs = self.p.registry.specs(
            await asyncio.to_thread(
                arm_for_task,
                self.p,
                session.task,
                agent_def.tools,
                max_tools=env_profile.max_tools(),
                adaptations=adaptations,
            )
        )

        system_prompt = agent_def.system_prompt
        # Auto-inject any configured default skills (§23) into the prompt.
        default_skills = getattr(self.p.config, "default_skills", None)
        if default_skills:
            try:
                system_prompt = self.p.skills.inject(system_prompt, default_skills)
            except Exception:
                pass
        # Self-correction: fold accumulated lessons + user preferences into the
        # system prompt so every run is a little smarter than the last.
        learning = getattr(self.p, "learning", None)
        if learning is not None:
            try:
                system_prompt = learning.apply_to_prompt(system_prompt)
            except Exception:  # never block a run on the learning layer
                pass
        # THE IDENTITY SPINE (v1.144.0) — the fix for "it communicates
        # differently depending on which model/surface answered". Until now the
        # persona + the user's preferences reached CHAT ONLY: an agent run
        # started from agent_def.system_prompt (the ROLE) and knew nothing about
        # who it was writing for or how they read. Both sections land here, in
        # the same order and with the same text chat uses, so the user reads one
        # Iron Jarvis whether a 14B local model or a cloud model took the work.
        # Bounded + never-raising, exactly like the injections below it.
        try:
            from ..personas.voice import voice_section
            from ..profile import profile_block

            system_prompt += voice_section(self.p)
            _profile = profile_block(self.p)
            if _profile:
                system_prompt += "\n\n" + _profile
        except Exception:  # noqa: BLE001 — identity must never break a run
            pass
        # MEMORY AWARENESS (v1.141.0): every agent type sees a compact index
        # of WHAT memory exists (bases + graph size + recent note TITLES —
        # never content) so it reaches for `recall` instead of assuming
        # ignorance. All types get it — unlike the roster below, it is cheap
        # (local reads only, no network/embedder) and short (≤ ~700 chars).
        # Same precedent as skills + learning: bounded, best-effort, never
        # blocks a run.
        try:
            from ..memory.index_block import memory_index_block

            _mem_index = memory_index_block(self.p, project_id=session.project_id)
            if _mem_index:
                system_prompt += "\n\n" + _mem_index
        except Exception:  # noqa: BLE001 — awareness must never break a run
            pass
        # CAPABILITY ROSTER (v1.139.0): ONLY the agent types that make
        # delegation choices see the roster — the supervisor picks `delegate`
        # targets, the planner assigns work to agents; a builder/reviewer run
        # would just carry the noise. A dynamic agent BASED on one of these
        # types inherits the injection (agent_def.type is its base type).
        # Same precedent as the skills + learning injections above: bounded,
        # best-effort, never blocks a run.
        if agent_def.type in (AgentType.SUPERVISOR, AgentType.PLANNER):
            try:
                from .roster import roster_block

                _roster = roster_block(self.p)
                if _roster:
                    system_prompt += "\n\n" + _roster
            except Exception:  # noqa: BLE001 — the roster must never break a run
                pass
        # TEAMMATES + MAIL (v1.193.0): the roster block above says which agent
        # TYPES exist to delegate to; this says who is on MY board right now,
        # what MY own run id is (an agent was never told it, so it could not
        # tell a teammate how to reach it), and whether a directed message is
        # sitting unread. Gated on the run's own tool specs, not on agent type —
        # every builtin carries the collab tools, so a type allowlist would
        # recreate the defect. Off the event loop: three SQLite reads, once per
        # run, and this is the ONE asyncio loop the whole daemon shares.
        if holds_board_tools(tool_specs):
            try:
                _team = await asyncio.to_thread(
                    teammates_block, self.p, session.id, run.id
                )
                if _team:
                    system_prompt += "\n\n" + _team
            except Exception:  # noqa: BLE001 — presence must never break a run
                pass
        # CONTEXT SPINE: a session tagged into a project carries the project's
        # brief + recent activity, so chat/terminals/workflows share one thread
        # of "what the user is working on". Bounded; never blocks a run.
        if session.project_id:
            try:
                system_prompt += self._project_context(session)
            except Exception:  # noqa: BLE001 — the spine must never break a run
                pass
        # ENVIRONMENT: agents kept mistaking the scratch workspace for the
        # user's real files ("list my Downloads" -> listing an empty sandbox,
        # burning tokens). Spell out the split + the real home directory.
        # An in-folder project session is the OPPOSITE case — its file tools
        # operate directly in the user's own folder, and the scratch line here
        # contradicted the task text ("you are working directly inside the
        # project folder") — so state whichever is actually true.
        try:
            if is_direct_workspace(self.p.config, session.workspace_path):
                system_prompt += (
                    "\n\n# Environment\n"
                    f"- The user's real home directory: {Path.home()}\n"
                    "- Your file tools (read_file/write_file/list_files) operate "
                    f"directly in the project folder at {workspace} — these ARE "
                    "the user's real files; treat them as real data, not a "
                    "scratch sandbox.\n"
                    "- For the user's OTHER folders/files (Downloads, Documents, "
                    "...) use list_folder / read_document / convert_document "
                    "with ABSOLUTE paths (reads are policy-gated).\n"
                )
            else:
                system_prompt += (
                    "\n\n# Environment\n"
                    f"- The user's real home directory: {Path.home()}\n"
                    "- Your file tools (read_file/write_file/list_files) operate in a "
                    "SCRATCH workspace — it is NOT where the user's files live.\n"
                    "- For the user's REAL folders/files (Downloads, Documents, ...) "
                    "use list_folder / read_document / convert_document with "
                    "ABSOLUTE paths (reads are policy-gated).\n"
                )
        except Exception:  # noqa: BLE001
            pass
        # MEMORY FABRIC: fold in the most relevant snippets from every store
        # (files, notes, memory graph, this project's knowledge, lessons, past
        # sessions) so the run starts already grounded in what the user knows —
        # no explicit `recall` call needed. Bounded, best-effort, never blocks.
        fabric = getattr(self.p, "fabric", None)
        if fabric is not None:
            try:
                grounding = fabric.ground(session.task, project_id=session.project_id)
                if grounding:
                    system_prompt += grounding
            except Exception:  # noqa: BLE001 — grounding must never break a run
                pass

        # v1.132.0 SHORT-HORIZON DECOMPOSITION: a local model served through the
        # prompted-tools scaffold loses the thread over a long flat loop, so a
        # plausibly multi-step task is split into plan → execute → verify →
        # assemble (agents/decompose.py) — reusing this SAME run record, state
        # transitions, event bus, and sink, so the dashboard sees a normal run.
        # Native tool-callers, simple tasks, and the flag-off case keep the flat
        # loop byte-for-byte unchanged; a planner that declines (degenerate/
        # unparseable plan) falls back to it too.
        final_text: str | None = None
        engage_decomposed, envelope_caused = should_decompose_enveloped(
            self.p, session, env_profile
        )
        if engage_decomposed:
            final_text = await _decompose.run_decomposed(
                self,
                run,
                session,
                agent_def,
                system_prompt=system_prompt,
                tool_specs=tool_specs,
                session_allow=session_allow,
                sink=sink,
            )
        # B4 (v1.202.0): the loop actually BENT under a measured envelope — say
        # so, ONCE per run. Published HERE, after the decompose decision has
        # RESOLVED, because deciding to decompose is not decomposing: the
        # planner may DECLINE (degenerate/unparseable plan -> run_decomposed
        # returns None -> the flat loop below runs unchanged), and an event
        # that fired before the planner spoke would permanently claim a
        # decomposition that never happened — the reviewer's confirmed Wave-B
        # defect. "decomposed" is appended only when the envelope caused the
        # engagement AND the lane actually produced the run's answer; the
        # arm-time bend (tool_cap) was realized at arming and rides along
        # either way; a run with ZERO realized bends publishes NOTHING. A
        # later wave adding a mid-run bend (the strict_json rung) must keep
        # the once-per-run contract — collect into `adaptations`, publish at
        # the single point where every bend of that run has resolved. Belt
        # and braces on "NEVER for trusted/unmeasured": `adaptations` can
        # only be non-empty for a measured, untrusted profile (both producers
        # gate on it), and the guard re-asserts it.
        if envelope_caused and final_text is not None:
            adaptations.append("decomposed")
        if adaptations and env_profile.is_measured() and not env_profile.is_trusted():
            try:
                await self.p.event_bus.publish(
                    ENVELOPE_ADAPTED,
                    {
                        "provider": env_provider,
                        "model": env_model,
                        "adaptations": list(adaptations),
                        "source": env_profile.source,
                    },
                    session_id=session.id,
                )
            except Exception:  # noqa: BLE001 — narration must never break a run
                pass
        if final_text is None:
            # The FLAT loop has no planning stage, so it says so plainly rather
            # than leaving the client on a phase-less spinner (v1.149.0). Every
            # run now reports a phase; a surface that shows one is never left
            # guessing which lane it got.
            if sink:
                sink.phase("running", "working the task")
            finished, final_text = await self.perceive_act(
                run,
                session,
                agent_def,
                system_prompt=system_prompt,
                messages=messages,
                tool_specs=tool_specs,
                session_allow=session_allow,
                sink=sink,
                max_steps=resolve_max_steps(session, self.p.config),
            )
            if not finished:
                run.result = "stopped: reached max steps before completion"
                await self._set_state(run, AgentState.FAILED, session.id)
                await self.p.event_bus.publish(
                    EventType.AGENT_COMPLETED,
                    {"run_id": run.id, "ok": False, "result": run.result},
                    session_id=session.id,
                )
                if sink:
                    sink.done(ok=False, result=run.result)
                return run

        run.result = final_text or "(no final message)"
        await self._set_state(run, AgentState.COMPLETED, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_COMPLETED,
            {"run_id": run.id, "ok": True, "result": run.result},
            session_id=session.id,
        )
        if sink:
            sink.done(ok=True, result=run.result)
        return run

    async def _pause_for_approval(
        self,
        session: Session,
        tc,
        agent_def: AgentDefinition,
        session_allow: set[str],
    ) -> tuple[str, set[str]]:
        """PAUSE this run on an ask-tier call and let the USER answer
        (v1.189.0) — the session half of chat's v1.187.0 mid-turn ask.

        Returns ``(deny_reason, grant_extra)``: ``("", set())`` means proceed
        (nothing needed asking, or the user granted it), a non-empty reason
        means the user (or the clock) refused and ``invoke`` must record that
        refusal through its ``deny_reason`` seam.

        THE MEASURED FAILURE THIS CLOSES: session_a63b0a4f, the rename
        acceptance job escalated from chat. `shell` hit the headless resolver
        three times ("nothing here could ask"), the blocked agent filed a
        capability request for a tool it already had, and the user found THAT
        on the Tools page while staring at the chat where the work was
        happening. The runtime now asks the human through the SAME registry
        and the SAME answer route as chat — the pause is published as
        ``approval.requested`` tagged with this session's id, so the chat page
        renders the same card under the escalated turn.

        WHO PAUSES: only runs whose ORIGIN ASSERTS a watching human — a chat
        escalation ("chat"), an Agents-page job ("job:…"), a Projects task
        ("project:<project id>", stamped by ``routes/projects.py`` — this
        branch was DEAD until v1.192.0 because nothing produced a
        project-prefixed origin, so in-folder Projects tasks were denied
        headlessly). An ALLOWLIST, not a denylist, and the first cut got
        this backwards: treating "unattributed" as "somebody is watching"
        parked every origin-less session — headless API callers and the
        entire offline test suite included — for five silent minutes per
        ask. Presence is a fact a caller states, never a default; an
        unattributed run keeps the instant honest denial, whose message
        already names ``allow_tools`` as the up-front grant path.

        'conversation' widens ``session_allow`` IN PLACE (never rebinds — the
        v1.187.0 generator-scoping lesson) so the rest of the run is covered;
        'once' covers exactly this call via ``grant_extra``.
        """
        approvals = getattr(self.p, "approvals", None)
        if approvals is None:  # bare-platform tests: no registry, no pause
            return "", set()
        origin = getattr(session, "origin", None) or ""
        if not origin.startswith(("chat", "job", "project", "user")):
            return "", set()
        tool = self.p.registry.get(tc.name)
        perm = tool.perm_key() if tool is not None else tc.name
        mode = self.p.permissions.mode_for(perm, agent_def.permission_overrides)
        if (
            mode is not PermissionMode.ASK
            or perm in session_allow
            or tc.name in session_allow
        ):
            return "", set()
        safe = tool.redact_args(tc.arguments) if tool is not None else tc.arguments
        approval_id, fut = approvals.request(tc.name, safe)
        await self.p.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {
                "approval_id": approval_id,
                "tool": tc.name,
                "args": safe,
                "timeout_s": int(SESSION_APPROVAL_TIMEOUT_S),
            },
            session_id=session.id,
        )
        decision = "timeout"
        try:
            decision = await asyncio.wait_for(
                fut, timeout=SESSION_APPROVAL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            decision = "timeout"
        finally:
            approvals.pop(approval_id)
        await self.p.event_bus.publish(
            EventType.APPROVAL_RESOLVED,
            {"approval_id": approval_id, "tool": tc.name, "decision": decision},
            session_id=session.id,
        )
        if decision == "conversation":
            session_allow.update({tc.name, perm})
            return "", set()
        if decision == "once":
            return "", {tc.name, perm}
        if decision == "deny":
            return "the user declined this call when asked", set()
        return (
            "the approval request timed out with no answer — ask the user to"
            " re-run, or grant the tool up front with allow_tools",
            set(),
        )

    async def perceive_act(
        self,
        run: AgentRun,
        session: Session,
        agent_def: AgentDefinition,
        *,
        system_prompt: str,
        messages: list[LLMMessage],
        tool_specs: list,
        session_allow: set[str],
        sink,
        max_steps: int,
        breaker_state: "dict[str, Any] | None" = None,
    ) -> tuple[bool, str]:
        """The per-round route → tool-execute body of the perceive→act loop —
        extracted (v1.132.0) as THE reusable seam so the decomposition
        mini-loops run the exact same routing/streaming/tool machinery as the
        flat loop, never a copy. Returns ``(finished, final_text)``:
        ``finished=False`` means ``max_steps`` rounds were spent before the
        model finalized (the caller decides whether that fails the run — the
        flat path — or just this step — a decomposition mini-loop).
        ``run.steps`` ACCUMULATES across calls, so a decomposed run's rounds
        all count on the one AgentRun record."""
        workspace = Path(session.workspace_path)
        # One persisted context.trimmed event per run, not one per step: a
        # 12-step run over budget would otherwise bury the timeline in twelve
        # identical notices.
        trim_reported = False
        # REPEATED-FAILURE BREAKER STATE (contract 2, v1.174.0). Per CALL
        # signature: how many times in a row it has failed, and — once armed —
        # the reason the next identical attempt is refused with. A success
        # clears the streak, so a flaky call that eventually works is never
        # punished for its history.
        #
        # SCOPE IS THE CALLER'S CHOICE (v1.177.0). This method is invoked ONCE
        # for a flat run but once PER STEP (and again per retry) for a decomposed
        # one, so state owned here reset at every boundary — and the breaker
        # stopped working on exactly the bulk jobs it was written for. A caller
        # that spans several invocations passes one dict and the streaks survive;
        # `None` keeps the old per-invocation scope, so the flat lane is
        # byte-identical.
        _bstate = breaker_state if breaker_state is not None else {}
        fail_streaks: dict[str, int] = _bstate.setdefault("fail_streaks", {})
        broken_calls: dict[str, str] = _bstate.setdefault("broken_calls", {})
        # COMPACTION STATE (v1.153.0). An agent loop has no one to ask mid-run,
        # so unlike chat it never offers the choice — it compacts on its own at
        # the ceiling and reports it. `_cpt_covers` counts messages consumed
        # AFTER index 0: the task is never covered, because a run whose goal
        # survives only as a paraphrase can drift off what it was asked to do.
        _cpt_summary, _cpt_covers, _cpt_futile = "", 0, False
        for _ in range(max_steps):
            # CONTEXT BUDGET (v1.152.0). The transcript grows by an assistant
            # turn plus every tool result on every step, and until now nothing
            # counted the tokens: the only guards were a 16k-char cap per tool
            # result and the step ceiling, which on a 32k local model is tens of
            # thousands of tokens before the system prompt is even added. Chat
            # got this in v1.146.0; this is where context actually gets big.
            #
            # `step_messages` / `step_system` are what THIS call sends; the loop
            # keeps appending to the real `messages` list, so the run's own
            # history (and the DB transcript) is never rewritten by trimming.
            step_messages, step_system = messages, system_prompt
            try:
                from ..context.agent_window import plan_agent_transcript
                from ..daemon.chat_turn import _context_window

                _win = _context_window(
                    SimpleNamespace(platform=self.p),
                    session.provider,
                    session.model,
                )
                # COMPACTION (v1.153.0) runs BEFORE the budget planner: a summary
                # it produces joins the system prompt and shortens the history,
                # both of which the planner then has to price. Everything below
                # works on EFFECTIVE values — the real `messages` list is the
                # run's own history and what gets persisted, and is never
                # rewritten.
                _cpt_summary, _cpt_covers, _cpt_futile = await self._maybe_compact(
                    session,
                    messages=messages,
                    system_prompt=system_prompt,
                    summary=_cpt_summary,
                    covers=_cpt_covers,
                    futile=_cpt_futile,
                    window=_win,
                    sink=sink,
                )
                _eff_messages, _eff_system = _effective(
                    messages, system_prompt, _cpt_summary, _cpt_covers
                )

                _plan = plan_agent_transcript(
                    _eff_messages,
                    window=_win,
                    system_text=_eff_system,
                )
                step_messages, step_system = _plan.messages, _eff_system
                if _plan.recap:
                    # The recap goes in the SYSTEM prompt, not the transcript —
                    # injecting it as a turn would put words in the model's own
                    # mouth that it never said.
                    step_system = f"{_eff_system}\n\n{_plan.recap}"
                if _plan.changed:
                    if _plan.clipped_task:
                        # Say this plainly: the agent is now working from a
                        # TRUNCATED goal, which is a result the user must be
                        # able to distrust. This model is too small for the job.
                        note = (
                            "the task is larger than this model's context "
                            "window and had to be cut — use a bigger model"
                        )
                    elif _plan.dropped_blocks:
                        note = (
                            f"trimmed context to fit ({_plan.dropped_blocks} "
                            f"earlier step(s) condensed)"
                        )
                    else:
                        note = "trimmed older tool output to fit"
                    if sink:  # live, for whoever is watching the run now
                        sink.phase("running", note)
                    if not trim_reported:  # persisted, for whoever reads it later
                        trim_reported = True
                        await self.p.event_bus.publish(
                            EventType.CONTEXT_TRIMMED,
                            {
                                "run_id": run.id,
                                "window": _plan.window,
                                "dropped_blocks": _plan.dropped_blocks,
                                "tools_trimmed": _plan.tools_trimmed,
                                "clipped_task": _plan.clipped_task,
                                "detail": note,
                            },
                            session_id=session.id,
                        )
            except Exception:  # noqa: BLE001 — a budgeting fault must never
                # break a run; the untrimmed transcript is what shipped before.
                step_messages, step_system = messages, system_prompt
            # FX-01: consume the router as a TOKEN STREAM. Text deltas are pushed to
            # the SSE sink the moment they arrive; the terminal ``final`` frame
            # carries the SAME aggregate LLMResponse (+ resolved provider/model) that
            # complete() would have returned, so everything downstream (usage
            # accounting, the tool loop, the persisted result) stays byte-identical.
            route_resp = None
            async for ev in self._route_stream(
                provider=session.provider,
                model=session.model,
                system=step_system,
                messages=step_messages,
                tools=tool_specs,
                session_id=session.id,
                # Task class for the (opt-in) self-tuning router: the agent type.
                task_class=agent_def.type.value,
            ):
                if ev.get("type") == "text":
                    if sink:
                        sink.token_delta(ev["text"])
                elif ev.get("type") == "final":
                    route_resp = ev
            resp = route_resp["response"]
            run.steps += 1
            run.provider, run.model = route_resp["provider"], route_resp["model"]
            if sink and run.steps == 1:
                sink.meta(run.provider, run.model)
            usage = getattr(resp, "usage", None) or {}
            step_in = int(usage.get("input_tokens", 0) or 0)
            step_out = int(usage.get("output_tokens", 0) or 0)
            run.input_tokens += step_in
            run.output_tokens += step_out
            # Persist the step count BEFORE the tools run (v1.174.0). It used to
            # be saved only at the END of a step, so for the whole of step N the
            # stored record still said N-1 — and anything reading the ledger
            # mid-step (the read cache's "already read at step N" note, a live
            # dashboard) quoted a step that had already passed. One extra merge
            # per step buys a number that is true while the step is happening.
            self._save(run)
            # TX-01 audit: one persisted event PER LLM call so every token is
            # individually replayable on the timeline (the per-run aggregate lives
            # on AgentRun). Best-effort — never let telemetry break a run.
            try:
                from ..eval.pricing import cost_for

                await self.p.event_bus.publish(
                    EventType.LLM_COMPLETED,
                    {
                        "run_id": run.id,
                        "step": run.steps,
                        "provider": run.provider,
                        "model": run.model,
                        "input_tokens": step_in,
                        "output_tokens": step_out,
                        "cost_usd": cost_for(
                            run.provider, run.model, step_in, step_out
                        ),
                        "task_class": agent_def.type.value,
                    },
                    session_id=session.id,
                )
            except Exception:  # noqa: BLE001 — telemetry must never break the loop
                pass

            if not resp.wants_tools:
                self._save(run)
                return True, resp.text

            messages.append(
                LLMMessage(
                    role="assistant", content=resp.text, tool_calls=resp.tool_calls
                )
            )
            ctx = ToolContext(
                workspace=workspace,
                session_id=session.id,
                agent_run_id=run.id,
                config=self.p.config,
                event_bus=self.p.event_bus,
                engine=self.p.engine,
            )

            # Run the turn's tool calls as a TEAM: gather them concurrently so
            # multiple delegate/blackboard calls execute at once. registry.invoke
            # opens its own session_scope per call (no shared Session across the
            # coroutines), records its own ToolInvocation, and publishes its own
            # event — so concurrency is safe. ``return_exceptions=True`` isolates a
            # failure/denial in one call so it never cancels its siblings. Results
            # are then appended in the ORIGINAL call order (the model maps tool
            # results to calls positionally), keeping behavior deterministic.
            # A call the breaker has armed is refused through the registry's
            # `deny_reason` seam rather than short-circuited here, so the
            # refusal is RECORDED as a ToolInvocation and published like any
            # other denial — `agents/outcome` derives what a run did from that
            # ledger, and a refusal that never reached it would make the run's
            # own history disagree with the transcript.
            async def _invoke(tc):
                deny_reason = broken_calls.get(
                    call_signature(tc.name, tc.arguments), ""
                )
                # The breaker's refusal is NOT a permission denial (v1.174.0
                # review): labelling it so makes the model tell the user it
                # lacks permission. A USER's refusal from the approval pause
                # below IS one — that is exactly what happened.
                deny_label = "refused"
                grant_extra: set[str] = set()
                if not deny_reason:
                    # MID-RUN APPROVAL (v1.189.0): an ask-tier call PAUSES for
                    # the user instead of dying on the headless resolver — the
                    # session half of chat's v1.187.0 ask. Per CALL, inside the
                    # gather, so parallel asks each get their own card.
                    deny_reason, grant_extra = await self._pause_for_approval(
                        session, tc, agent_def, session_allow
                    )
                    if deny_reason:
                        deny_label = "permission denied"
                return await self.p.registry.invoke(
                    tc.name,
                    tc.arguments,
                    ctx,
                    self.p.permissions,
                    agent_def.permission_overrides,
                    session_allow=(
                        (session_allow | grant_extra)
                        if grant_extra
                        else session_allow
                    ),
                    deny_reason=deny_reason,
                    deny_label=deny_label,
                )

            # FX-01: announce each tool call BEFORE the fan-out, with args redacted
            # (tool.redact_args) so no secret ever reaches the wire. Emitted HERE —
            # not inside the gathered coroutines — so frame order is deterministic.
            if sink:
                for tc in resp.tool_calls:
                    tool = self.p.registry.get(tc.name)
                    safe = tool.redact_args(tc.arguments) if tool else tc.arguments
                    sink.tool_started(tc.id, tc.name, safe)

            results = await asyncio.gather(
                *(_invoke(tc) for tc in resp.tool_calls),
                return_exceptions=True,
            )
            for tc, result in zip(resp.tool_calls, results):
                if isinstance(result, asyncio.CancelledError):
                    # Cooperative cancellation (user stopped the run) must still
                    # unwind — never swallow it into a tool result.
                    raise result
                if isinstance(result, BaseException):
                    # registry.invoke already traps tool exceptions, so this only
                    # fires for an error OUTSIDE the tool (e.g. the event bus); turn
                    # it into this call's error result without aborting the siblings.
                    content = f"{type(result).__name__}: {result}"
                else:
                    content = result.output if result.ok else (result.error or "error")
                    # Fence externally-sourced tool output (documents/PDF/notes/
                    # memory/file-search/MCP) as untrusted DATA and scan it for
                    # prompt-injection — consistent with web_search/browse — so a
                    # planted file can't inject instructions into the model context.
                    #
                    # The FAILURE path is fenced too (v1.98.1). It used to be gated
                    # on ``result.ok``, which held only while every such tool wrote
                    # its own error string ("read denied: ..."). MCP breaks that:
                    # an ``isError`` response returns the REMOTE text verbatim as
                    # ``.error``, so gating on ok let attacker-authored content skip
                    # the scan entirely. Both chat loops already fence unconditionally
                    # — this aligns the runtime with them.
                    tool = self.p.registry.get(tc.name)
                    if getattr(tool, "returns_untrusted_content", False):
                        from ..computeruse.safety import (
                            detect_injection,
                            wrap_untrusted,
                        )

                        inj = detect_injection(content)
                        content = wrap_untrusted(
                            f"[content withheld — suspected {inj['category']}: {inj['reason']}]"
                            if inj["flagged"]
                            else content
                        )
                if len(content) > _MAX_TOOL_CONTEXT_CHARS:
                    dropped = len(content) - _MAX_TOOL_CONTEXT_CHARS
                    content = (
                        content[:_MAX_TOOL_CONTEXT_CHARS]
                        + f"\n[... truncated {dropped} chars — full output in the transcript]"
                    )

                # THE BREAKER (contract 2). Bookkeeping runs AFTER the truncation
                # above so its note can never be the thing that gets cut — the
                # note is the only part of this result the model must not miss.
                call_ok = (
                    result.ok if not isinstance(result, BaseException) else False
                )
                signature = call_signature(tc.name, tc.arguments)
                if call_ok:
                    fail_streaks.pop(signature, None)
                    broken_calls.pop(signature, None)
                else:
                    streak = fail_streaks.get(signature, 0) + 1
                    fail_streaks[signature] = streak
                    if streak >= _BREAKER_NOTE_AT:
                        last = " ".join(str(content).split())[:200]
                        broken_calls[signature] = (
                            f"repeated-failure breaker — `{tc.name}` has now "
                            f"failed {streak} times in a row with these exact "
                            f"arguments, so this call is refused for the rest "
                            f"of the run. Last failure: {last}. Change the "
                            f"arguments, use a different tool, or say plainly "
                            f"what is blocking you — do not send it again."
                        )
                    if streak == _BREAKER_NOTE_AT:
                        content += (
                            f"\n\n[repeat — this is failure {streak} in a row "
                            f"for `{tc.name}` with these exact arguments. A "
                            f"{_BREAKER_REFUSE_AT}rd identical call will be "
                            f"refused. Read the error above and change the "
                            f"arguments, use a different tool, or report what "
                            f"is blocking you.]"
                        )
                if sink:
                    sink.tool_finished(
                        tc.id,
                        tc.name,
                        ok=call_ok,
                        preview=str(content)[:500],
                    )
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=content,
                    )
                )
            self._save(run)
            if sink:
                sink.step_end(run.steps)
        # Round budget spent before the model finalized — the CALLER owns what
        # that means (whole-run failure vs. a single step's outcome).
        return False, ""
