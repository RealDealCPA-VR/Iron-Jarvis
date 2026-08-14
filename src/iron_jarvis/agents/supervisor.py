"""Supervisor agent (§12 Multi-Agent Orchestration).

The Supervisor decomposes a task into subtasks and delegates each to a subagent
via the ``delegate`` tool. Subagents run with isolated context and return only
SUMMARIZED results; the supervisor is the single point of contact and produces
the final summary. Subagents never talk to the user directly.

v1.174.0 adds the WORKLIST PATTERN. The measured failure this addresses: a
26-file bulk job spent 12 steps, renamed nothing, and read three of the same
documents twice — because the only record of what had been inspected was the
transcript, and the step ceiling threw the transcript away. An agent that
surveys once, writes every unit of work down, and hands out CLAIMED chunks
turns "ran out of steps" into "got 14 of 26 done, here are the other 12", and
makes a re-run a no-op over the finished ones.

:func:`with_worklist` is deliberately general and does NOT live inside
:func:`supervisor_definition`: the traced failure was a BUILDER run, not a
supervisor one (``SessionCreate.agent_type`` defaults to ``"builder"``, and
``orchestrator.run_session`` sends every non-SUPERVISOR session to
``runtime.run`` with the canonical definition). Any definition that should be
able to finish a bulk job is wrapped with it, so its prompt and its roster can
never drift apart — a definition holding ``worklist_add`` with no instruction
to use it is the v1.142 hole half-fixed.
"""

from __future__ import annotations

from ..core.models import AgentRun, AgentType
from ..worklist import WORKLIST_TOOL_NAMES
from .delegate_tool import DelegateTool
from .runtime import AgentRuntime
from .types import AgentDefinition, get_agent_definition

SUPERVISOR_DEFINITION = AgentDefinition(
    type=AgentType.SUPERVISOR,
    system_prompt=(
        "You are the Supervisor agent in Iron Jarvis. Break the user's task into "
        "small, self-contained subtasks. For each subtask, call the `delegate` "
        "tool with an appropriate `agent_type` (e.g. 'builder', 'researcher', "
        "'reviewer') and a precise `task` describing exactly what the subagent "
        "must accomplish. Subagents run independently in isolated workspaces and "
        "return only a summary — they never contact the user, so all coordination "
        "flows through you. When every subtask is complete, reply with a single "
        "consolidated summary and no further tool calls."
    ),
    tools=["delegate"],
)

#: The phrase every worklist-carrying prompt contains. Used to make
#: :func:`with_worklist` idempotent, and it is what a test asserts against the
#: prompt the runtime actually hands the provider — a definition that carries
#: the four tools with no instruction to use them is the roster half of the
#: v1.142 hole fixed and the prompt half left open.
WORKLIST_MARKER = "SURVEY ONCE"

#: Appended to a bulk-capable agent's prompt. Written as a procedure rather
#: than advice because the failure it fixes was a coordination failure, not a
#: reasoning one: the model knew what to do with each file and still finished
#: nothing, having spent its budget rediscovering which files there were.
WORKLIST_PATTERN = (
    "\n\nWHEN THE TASK COVERS MANY ITEMS (every file in a folder, each row, all "
    "of a list), do NOT work from memory or from this conversation — the "
    "conversation is trimmed and the run can hit a step limit, and anything "
    "recorded only here is lost. Use the durable worklist:\n"
    "1. SURVEY ONCE. List the folder/collection a single time.\n"
    "2. `worklist_add` EVERY item in one call (use full file paths as keys). "
    "This is idempotent — on a resumed or repeated job it re-adds nothing, and "
    "a file a finished item already produced is never re-queued, so a second "
    "run over renamed files correctly finds nothing to do.\n"
    "3. Work in CHUNKS. Either `worklist_next` yourself, or `delegate` a "
    "subtask that tells the subagent to call `worklist_next` to claim its own "
    "chunk and `worklist_done` for each key it finishes. Claiming is what stops "
    "two subagents doing the same file — never assign items by naming them in "
    "the task text.\n"
    "4. Report EVERY item with `worklist_done`: status 'done' or 'failed', a "
    "one-line note, and `result_path` when the item produced or renamed a file. "
    "An item you could not read is 'failed' with the reason — never guess its "
    "content, and never mark it done because you ran out of time.\n"
    "5. Use `worklist_status` to decide whether you are finished. It counts the "
    "durable record, so trust it over your own recollection. When the run must "
    "stop early, say plainly how many items are done and how many remain — the "
    "worklist means the next run picks up exactly there."
)


def with_worklist(base: AgentDefinition) -> AgentDefinition:
    """A COPY of ``base`` carrying the worklist tools and the worklist pattern.

    Takes any definition, not just the supervisor's, because the measured
    failure was NOT a supervisor. The traced run — ``list_files`` → five failed
    ``shell`` calls → ``extract_pdf`` ×6 → ``read_document`` ×6, no ``delegate``
    anywhere — was a BUILDER: ``SessionCreate.agent_type`` defaults to
    ``"builder"``, the job form only sends ``"supervisor"`` for a TEAM target,
    and ``orchestrator.run_session`` sends everything except SUPERVISOR straight
    to ``runtime.run`` with the canonical definition. Giving a builder the four
    tools without the procedure fixes the roster half of the v1.142 hole and
    leaves the prompt half open: it holds tools nothing ever told it to use.
    Wrap the definition instead, so prompt and roster cannot drift apart.

    A NEW object every call. ``get_agent_definition`` hands back the shared
    module-level record, and appending to its prompt or its tool list in place
    would rewrite the definition for every later run in the process — the same
    shared-mutable trap that made ``specs()`` permanently rewrite tool schemas
    (v1.165.0). Copy, then extend.

    The worklist tools are appended rather than assumed present so this keeps
    working whichever way the roster in ``agents/types.py`` is written, and
    ``registry.specs`` silently drops any name the registry does not serve — so
    a platform built without the worklist advertises exactly what it has. The
    pattern is appended at most once, so wrapping an already-wrapped definition
    is a no-op rather than a doubled prompt.
    """
    tools = list(base.tools)
    for name in WORKLIST_TOOL_NAMES:
        if name not in tools:
            tools.append(name)
    prompt = base.system_prompt
    if WORKLIST_MARKER not in prompt:
        prompt += WORKLIST_PATTERN
    return AgentDefinition(
        type=base.type,
        system_prompt=prompt,
        tools=tools,
        permission_overrides=dict(base.permission_overrides),
    )


def supervisor_definition() -> AgentDefinition:
    """The canonical supervisor definition, plus the worklist pattern + tools."""
    return with_worklist(get_agent_definition(AgentType.SUPERVISOR))


async def run_supervised(platform, session) -> AgentRun:
    """Run a supervisor session, wiring the ``delegate`` tool on demand.

    Ensures ``DelegateTool`` is registered in the platform's tool registry (so
    the supervisor can spawn subagents) and then drives the standard agent
    runtime against :func:`supervisor_definition`.
    """
    if platform.registry.get("delegate") is None:
        platform.registry.register(DelegateTool(platform))
    # Single source of truth: the canonical SUPERVISOR definition in types.py
    # (so behavior is identical whether launched here or via /agents/{name}/spawn),
    # extended with the worklist pattern this launcher owns.
    return await AgentRuntime(platform).run(session, supervisor_definition())
