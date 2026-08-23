"""The `# Team` prompt block (v1.193.0): tell every agent who it is, who is
with it, and that it has mail.

Three verified facts made the blackboard dead weight before this wave:

(a) The board was NEVER injected into any prompt. `runtime.run` assembled
    skills, lessons, voice+profile, the memory index, the capability roster,
    project context, environment and the memory fabric — and nothing about the
    department. An agent learned a board existed only from a tool description,
    and a directed message sat unread unless the recipient spontaneously polled.
(b) The capability roster block is injected for SUPERVISOR and PLANNER only,
    while `_COLLAB_TOOLS` is on EVERY builtin definition — six agent types
    carried `blackboard_post`/`blackboard_read`/`message_agent` while being told
    nothing about any teammate existing.
(c) An agent was never told its OWN run id, so it could not tell a teammate how
    to reach it even if it wanted to.
"""

from __future__ import annotations

import threading

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import (
    AgentRuntime,
    holds_board_tools,
    teammates_block,
)
from iron_jarvis.agents.types import AgentDefinition, get_agent_definition
from iron_jarvis.blackboard.models import BlackboardKind
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.providers.adapters.base import LLMResponse


def _run_row(platform, session_id: str, agent_type: AgentType, parent_id=None) -> str:
    """Persist one AgentRun and return its id."""
    with session_scope(platform.engine) as db:
        row = AgentRun(session_id=session_id, parent_id=parent_id)
        row.agent_type = agent_type
        row.state = AgentState.RUNNING
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _capture(platform, seen: dict):
    """Stand in for the router's token stream, recording the SYSTEM PROMPT each
    call was handed and finalizing immediately."""

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        seen.setdefault("system", []).append(system)
        seen.setdefault("tools", []).append([s["name"] for s in tools])
        yield {
            "type": "final",
            "response": LLMResponse(text="done", tool_calls=[], usage={}),
            "provider": "mock",
            "model": "mock",
        }

    platform.router.stream = fake_stream
    return seen


# =============================================================================
# 1. A SOLO RUN RENDERS NOTHING
# =============================================================================
async def test_a_solo_run_grows_no_team_section(platform):
    """No teammates, no mail, no board worth mentioning — the same rule the
    dashboard's TeamTree follows. A lone agent must not carry a "Team" heading
    advertising a feature it is not using."""
    seen = _capture(platform, {})
    sess = await Orchestrator(platform).create_session(
        "say hello", AgentType.BUILDER
    )
    run = await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.BUILDER))

    assert run.state is AgentState.COMPLETED
    assert "# Team" not in seen["system"][0]
    # ...and the block agrees when asked directly.
    assert teammates_block(platform, sess.id, run.id) == ""


# =============================================================================
# 2. WHO I AM + WHO IS WITH ME, at the real seam
# =============================================================================
async def test_a_run_with_teammates_names_them_and_states_my_own_identity(platform):
    """A BUILDER — not a coordinator type, so `roster_block` never reaches it —
    joins a department that already has two teammates. Its system prompt must
    name them, name ITSELF, and carry its own run id (fact (c): an agent could
    not be reached back because it was never told its id)."""
    root = _run_row(platform, "dept-root", AgentType.SUPERVISOR)
    mate = _run_row(platform, "child-0", AgentType.RESEARCHER, parent_id=root)

    seen = _capture(platform, {})
    sess = await Orchestrator(platform).create_session(
        "write the summary", AgentType.BUILDER
    )
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.BUILDER), parent_id=root
    )
    system = seen["system"][0]

    assert "# Team" in system
    # WHO I AM — my name and the run id a teammate addresses me by.
    assert "builder" in system and run.id in system
    # WHO IS WITH ME — the silent teammates, by NAME and by id.
    assert "researcher" in system and mate in system
    assert "supervisor" in system and root in system
    # No mail on this board, so no mail line: a permanent "0 unread" is noise
    # that costs prompt budget on every single run.
    assert "YOU HAVE MAIL" not in system


async def test_the_block_lands_before_the_context_planner(platform, monkeypatch):
    """BUDGET RULE: a system-prompt addition made AFTER the planner runs is
    invisible to the token budget. The planner must be handed a system text that
    already contains the block."""
    import iron_jarvis.context.agent_window as _win

    root = _run_row(platform, "dept-root", AgentType.SUPERVISOR)
    _run_row(platform, "child-0", AgentType.RESEARCHER, parent_id=root)

    priced: list[str] = []
    real = _win.plan_agent_transcript

    # **kw so the spy survives additive planner-signature growth — v1.203.0
    # added `chars_per_token` and a fixed-signature spy TypeError'd into
    # "the planner never ran" (the same shape as the v1.202.0 arming-spy
    # break; a spy must never pin a signature it doesn't assert on).
    def spy(messages, *, window, system_text="", **kw):
        priced.append(system_text)
        return real(messages, window=window, system_text=system_text, **kw)

    monkeypatch.setattr(_win, "plan_agent_transcript", spy)
    _capture(platform, {})
    sess = await Orchestrator(platform).create_session(
        "write the summary", AgentType.BUILDER
    )
    await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.BUILDER), parent_id=root
    )

    assert priced, "the planner never ran"
    assert "# Team" in priced[0], "the block's tokens were never priced"


# =============================================================================
# 3. WHETHER I HAVE MAIL
# =============================================================================
def test_a_directed_unread_message_is_announced_with_its_sender(platform):
    """The unread signal. Before this, a directed message sat unread forever
    unless the recipient happened to poll — there was no unread signal anywhere
    in the app."""
    root = _run_row(platform, "dept-root", AgentType.SUPERVISOR)
    me = _run_row(platform, "child-0", AgentType.BUILDER, parent_id=root)

    block = teammates_block(platform, "child-0", me)
    assert "YOU HAVE MAIL" not in block  # nothing addressed to me yet

    platform.blackboard.post(
        "dept-root",
        root,
        "use the v2 endpoint, not v1",
        kind=BlackboardKind.MESSAGE,
        to_agent=me,
        author_name="supervisor",
        to_name="builder",
    )
    block = teammates_block(platform, "child-0", me)

    assert "YOU HAVE MAIL: 1 message(s)" in block
    assert "supervisor" in block
    assert "blackboard_read" in block and "to_me=true" in block


def test_mail_addressed_to_my_NAME_counts_and_my_own_note_does_not(platform):
    """Two halves of an honest count: the unread predicate is the same one
    `blackboard_read(to_me=true)` uses (run id OR my name on a row carrying no
    id), and a row I wrote MYSELF is not mail I have to read."""
    root = _run_row(platform, "dept-root", AgentType.SUPERVISOR)
    me = _run_row(platform, "child-0", AgentType.BUILDER, parent_id=root)

    platform.blackboard.post(
        "dept-root", root, "by name only", kind=BlackboardKind.MESSAGE,
        to_agent=None, author_name="supervisor", to_name="builder",
    )
    platform.blackboard.post(
        "dept-root", me, "note to self", kind=BlackboardKind.MESSAGE,
        to_agent=me, author_name="builder", to_name="builder",
    )
    block = teammates_block(platform, "child-0", me)

    assert "YOU HAVE MAIL: 1 message(s)" in block


# =============================================================================
# 4. WHO SEES IT — derived from the tools, never from the agent type
# =============================================================================
def test_the_gate_reads_the_specs_not_the_agent_type(platform):
    """Fact (b): `_COLLAB_TOOLS` is on every builtin, so an agent-type allowlist
    (what `roster_block` uses) would leave six types holding board tools and
    knowing nothing. The gate reads the specs the model is about to be offered,
    so the block and the capability cannot drift apart."""
    for agent_type in (
        AgentType.BUILDER,
        AgentType.REVIEWER,
        AgentType.RESEARCHER,
        AgentType.MEMORY,
        AgentType.MAINTAINER,
        AgentType.AUTOMATION,
    ):
        specs = platform.registry.specs(get_agent_definition(agent_type).tools)
        assert holds_board_tools(specs), agent_type.value

    assert not holds_board_tools([{"name": "read_file"}, {"name": "shell"}])
    assert not holds_board_tools([])


async def test_a_definition_without_board_tools_gets_no_block(platform):
    """The other side of the gate at the real seam: an agent that cannot post or
    read must not be told about a board it has no way to touch."""
    root = _run_row(platform, "dept-root", AgentType.SUPERVISOR)
    _run_row(platform, "child-0", AgentType.RESEARCHER, parent_id=root)

    seen = _capture(platform, {})
    definition = get_agent_definition(AgentType.BUILDER)
    stripped = AgentDefinition(
        type=definition.type,
        system_prompt=definition.system_prompt,
        tools=[
            t
            for t in definition.tools
            if not t.startswith("blackboard_") and t != "message_agent"
        ],
        permission_overrides=dict(definition.permission_overrides),
    )
    sess = await Orchestrator(platform).create_session(
        "write the summary", AgentType.BUILDER
    )
    await AgentRuntime(platform).run(sess, stripped, parent_id=root)

    assert "# Team" not in seen["system"][0]


# =============================================================================
# 5. IT MUST NOT RUN ON THE EVENT LOOP
# =============================================================================
async def test_presence_is_computed_off_the_event_loop(platform, monkeypatch):
    """Three SQLite reads (department walk + roster + directed rows), and a
    wave-1 reviewer measured `roster()` alone at up to 47ms in a pathological
    tree. The daemon is ONE asyncio loop — a sync DB read in prompt assembly
    blocks every request in the app."""
    import iron_jarvis.agents.runtime as _rt

    root = _run_row(platform, "dept-root", AgentType.SUPERVISOR)
    _run_row(platform, "child-0", AgentType.RESEARCHER, parent_id=root)

    where: list[str] = []
    real = _rt.teammates_block

    def spy(platform_, session_id, agent_run_id):
        where.append(threading.current_thread().name)
        return real(platform_, session_id, agent_run_id)

    monkeypatch.setattr(_rt, "teammates_block", spy)
    _capture(platform, {})
    sess = await Orchestrator(platform).create_session(
        "write the summary", AgentType.BUILDER
    )
    await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.BUILDER), parent_id=root
    )

    assert where, "the block was never assembled"
    assert where[0] != threading.main_thread().name


def test_the_block_never_raises(platform):
    """Same convention as `roster_block` / `memory_index_block` beside it: a
    failure omits the block, it never fails the run."""

    class Exploding:
        engine = platform.engine

        def board_id_for(self, *a, **k):
            raise RuntimeError("boom")

    broken = type("P", (), {"blackboard": Exploding()})()
    assert teammates_block(broken, "s", "r") == ""
    # No store at all (a bare platform in a unit test).
    assert teammates_block(type("P", (), {})(), "s", "r") == ""
