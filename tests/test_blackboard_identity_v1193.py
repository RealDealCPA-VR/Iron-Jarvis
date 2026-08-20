"""Blackboard identity (v1.193.0): address a teammate BY NAME, and a roster
that tells the truth about who is on the board.

Three defects are pinned here, each one a silent failure before this wave:

1. ``message_agent`` took a raw ``to_agent`` string and validated NOTHING, so a
   typo wrote a row addressed to an id that does not exist — unreadable by
   anyone, with no error and no bounce.
2. The only way to learn a teammate's run id was the roster, and the roster was
   built from who had POSTED. A teammate who had not spoken yet was invisible,
   so the model could name a colleague it had no way to address.
3. Reading "messages to me" matched the run id only, so a message addressed to
   the name never arrived.
"""

from __future__ import annotations

from iron_jarvis.blackboard import BlackboardStore
from iron_jarvis.blackboard.models import BlackboardKind
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.tools.base import ToolContext


def _department(platform, *child_types: AgentType) -> tuple[str, str, list[str]]:
    """A root run + one child run per given type. Returns (board_id, root_run_id,
    child_run_ids)."""
    with session_scope(platform.engine) as db:
        root = AgentRun(session_id="dept-root", parent_id=None)
        root.agent_type = AgentType.SUPERVISOR
        db.add(root)
        db.commit()
        db.refresh(root)
        children = []
        for index, agent_type in enumerate(child_types):
            child = AgentRun(session_id=f"child-{index}", parent_id=root.id)
            child.agent_type = agent_type
            db.add(child)
            children.append(child)
        db.commit()
        for child in children:
            db.refresh(child)
        return "dept-root", root.id, [c.id for c in children]


def _ctx(platform, run_id: str, session_id: str) -> ToolContext:
    return ToolContext(
        workspace=platform.config.workspaces_dir,
        session_id=session_id,
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


# --- (d) the roster tells the truth ---------------------------------------


def test_roster_lists_a_teammate_who_has_never_posted(platform):
    """The whole point: a silent teammate is still ADDRESSABLE. Nobody has
    posted anything at all here, so the old post-derived roster was empty."""
    board, root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    roster = {r["agent_run_id"]: r for r in platform.blackboard.roster(board)}

    assert set(roster) == {root_id, builder_id, researcher_id}
    assert roster[researcher_id]["handle"] == "researcher"
    assert roster[researcher_id]["posts"] == 0  # never spoke, still listed
    assert roster[builder_id]["handle"] == "builder"
    # Another department's board never borrows this team.
    assert platform.blackboard.roster("some-other-dept") == []


def test_roster_still_lists_authors_with_no_run_row(platform):
    """The chat seam (author "chat", no AgentRun) and any deleted run: the board
    must never show a message from an agent its roster denies exists."""
    platform.blackboard.post("orphan-board", "runA", "found X")
    platform.blackboard.post("orphan-board", "runA", "more")
    roster = {r["agent_run_id"]: r["posts"] for r in platform.blackboard.roster(
        "orphan-board"
    )}
    assert roster == {"runA": 2}


# --- (b) addressing by name, and the refusals -----------------------------


async def test_message_agent_addresses_a_teammate_by_name(platform):
    board, root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )

    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "researcher", "text": "pull the 1099 totals"},
        _ctx(platform, builder_id, "child-0"),
        platform.permissions,
    )
    assert res.ok, res.error
    # The NAME resolved to the precise run id, and both handles were stamped.
    assert res.data["to_agent"] == researcher_id
    assert res.data["to_name"] == "researcher"
    assert res.data["author_name"] == "builder"

    row = platform.blackboard.list(board)[0]
    assert row.to_agent == researcher_id
    assert row.to_name == "researcher"
    assert row.author_name == "builder"
    assert row.kind is BlackboardKind.MESSAGE

    # ...and the addressee actually receives it.
    read = await platform.registry.invoke(
        "blackboard_read",
        {"to_me": True},
        _ctx(platform, researcher_id, "child-1"),
        platform.permissions,
    )
    assert [r["text"] for r in read.data["records"]] == ["pull the 1099 totals"]


async def test_message_agent_refuses_an_unknown_name_and_lists_teammates(platform):
    board, root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )

    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "tax-reader", "text": "check this"},
        _ctx(platform, builder_id, "child-0"),
        platform.permissions,
    )
    assert not res.ok
    assert "tax-reader" in (res.error or "")
    # The refusal is USABLE: it names who could have been addressed.
    assert f"researcher={researcher_id}" in (res.error or "")
    assert f"supervisor={root_id}" in (res.error or "")
    # And the unreadable row was never written — that silent drop was the bug.
    assert platform.blackboard.list(board) == []


async def test_message_agent_refuses_an_ambiguous_name_with_the_run_ids(platform):
    board, root_id, (builder_a, builder_b) = _department(
        platform, AgentType.BUILDER, AgentType.BUILDER
    )

    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "builder", "text": "you, specifically"},
        _ctx(platform, root_id, "dept-root"),
        platform.permissions,
    )
    assert not res.ok
    assert "ambiguous" in (res.error or "").lower()
    # Both candidates are named so the model can disambiguate itself.
    assert builder_a in (res.error or "") and builder_b in (res.error or "")
    assert platform.blackboard.list(board) == []  # no silent pick


async def test_blackboard_post_refuses_an_unreadable_direction(platform):
    board, root_id, (builder_id, _researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    res = await platform.registry.invoke(
        "blackboard_post",
        {"text": "note", "to_agent": "nobody-here"},
        _ctx(platform, builder_id, "child-0"),
        platform.permissions,
    )
    assert not res.ok and "nobody-here" in (res.error or "")
    assert platform.blackboard.list(board) == []


async def test_message_agent_still_accepts_an_exact_run_id(platform):
    """Run ids remain the precise handle — name resolution is additive."""
    board, root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": researcher_id, "text": "by id"},
        _ctx(platform, builder_id, "child-0"),
        platform.permissions,
    )
    assert res.ok and res.data["to_agent"] == researcher_id
    assert res.data["to_name"] == "researcher"


# --- (c) reading what is addressed to me ----------------------------------


async def test_read_to_me_matches_by_name_without_cross_talk(platform):
    """A row addressed by NAME alone reaches its recipient — but a row carrying
    a run id belongs to THAT run only, even when a sibling shares its name."""
    board, root_id, (builder_a, builder_b) = _department(
        platform, AgentType.BUILDER, AgentType.BUILDER
    )
    store: BlackboardStore = platform.blackboard
    store.post(board, root_id, "for whichever builder", to_name="builder")
    store.post(board, root_id, "for builder A only", to_agent=builder_a,
               to_name="builder")

    read_a = await platform.registry.invoke(
        "blackboard_read", {"to_me": True},
        _ctx(platform, builder_a, "child-0"), platform.permissions,
    )
    read_b = await platform.registry.invoke(
        "blackboard_read", {"to_me": True},
        _ctx(platform, builder_b, "child-1"), platform.permissions,
    )
    assert [r["text"] for r in read_a.data["records"]] == [
        "for whichever builder",
        "for builder A only",
    ]
    # B sees the name-only row, NOT the one addressed to A's run id.
    assert [r["text"] for r in read_b.data["records"]] == ["for whichever builder"]


async def test_read_lists_silent_teammates_in_its_output(platform):
    """End to end through the tool the model actually calls: an empty board
    still tells the agent who it can talk to."""
    board, root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    read = await platform.registry.invoke(
        "blackboard_read", {}, _ctx(platform, builder_id, "child-0"),
        platform.permissions,
    )
    assert read.ok
    assert "researcher=" in read.output and researcher_id in read.output
    assert read.data["you_name"] == "builder"
    assert builder_id not in read.output  # you are not your own teammate


# --- who counts as a CANDIDATE for a name ---------------------------------


def _children(platform, root_id: str, *specs: tuple[AgentType, AgentState]) -> list[str]:
    """Extra children of ``root_id``, each with an explicit lifecycle state."""
    with session_scope(platform.engine) as db:
        made = []
        for index, (agent_type, state) in enumerate(specs):
            run = AgentRun(session_id=f"extra-{index}", parent_id=root_id)
            run.agent_type = agent_type
            run.state = state
            db.add(run)
            made.append(run)
        db.commit()
        for run in made:
            db.refresh(run)
        return [r.id for r in made]


async def test_a_namesake_never_counts_itself_as_the_ambiguity(platform):
    """Builder A saying "builder" means the OTHER builder. Counting the CALLER
    as a candidate refused a message that had exactly one possible recipient —
    and handed the model its own run id as an addressable teammate, so the retry
    wrote a self-addressed row nobody ever reads. That unreadable row is the
    exact defect this unit exists to remove."""
    board, root_id, (builder_a, builder_b) = _department(
        platform, AgentType.BUILDER, AgentType.BUILDER
    )
    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "builder", "text": "your half of the reconciliation"},
        _ctx(platform, builder_a, "child-0"),
        platform.permissions,
    )
    assert res.ok, res.error
    assert res.data["to_agent"] == builder_b  # not itself, not a refusal
    assert builder_a not in (res.error or "")
    # And it lands where B reads it.
    read = await platform.registry.invoke(
        "blackboard_read", {"to_me": True},
        _ctx(platform, builder_b, "child-1"), platform.permissions,
    )
    assert [r["text"] for r in read.data["records"]] == [
        "your half of the reconciliation"
    ]


async def test_finished_namesakes_do_not_bury_the_live_teammate(platform):
    """`delegate` BLOCKS, so a delegated child is COMPLETED by the time the
    parent resumes. Counting the graveyard as ambiguity meant the second
    delegation of any agent type killed name-addressing for that type for the
    rest of the session."""
    board, root_id, (live_researcher,) = _department(platform, AgentType.RESEARCHER)
    _children(
        platform,
        root_id,
        (AgentType.RESEARCHER, AgentState.COMPLETED),
        (AgentType.RESEARCHER, AgentState.COMPLETED),
    )
    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "researcher", "text": "take the next K-1"},
        _ctx(platform, root_id, "dept-root"),
        platform.permissions,
    )
    assert res.ok, res.error
    assert res.data["to_agent"] == live_researcher


async def test_a_finished_teammate_is_still_addressable_when_alone(platform):
    """Preferring the live ones is a TIEBREAK, not an exclusion: with nobody
    live under that name, the finished teammate still answers to it."""
    board, root_id, (builder_id,) = _department(platform, AgentType.BUILDER)
    with session_scope(platform.engine) as db:
        run = db.get(AgentRun, builder_id)
        run.state = AgentState.COMPLETED
        db.add(run)
        db.commit()
    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "builder", "text": "for the record"},
        _ctx(platform, root_id, "dept-root"),
        platform.permissions,
    )
    assert res.ok, res.error
    assert res.data["to_agent"] == builder_id


def test_the_chat_usage_ledger_is_not_a_roster(platform):
    """`daemon/chat_turn._persist_chat_usage` writes one AgentRun(session_id=
    "chat", parent_id=None) PER CHAT TURN as accounting. Seeding the permanent
    global "chat" board from those made hundreds of identically named phantom
    "builder" members — every name permanently ambiguous — and cost one nested
    department walk each. On that board the roster degrades to posters-only,
    exactly as it behaved before this release."""
    with session_scope(platform.engine) as db:
        for _ in range(5):
            row = AgentRun(session_id="chat", parent_id=None)
            row.agent_type = AgentType.BUILDER
            row.state = AgentState.COMPLETED
            db.add(row)
        db.commit()

    assert platform.blackboard.roster("chat") == []
    platform.blackboard.post("chat", "chat", "a note", author_name="chat")
    assert [r["agent_run_id"] for r in platform.blackboard.roster("chat")] == ["chat"]
    # A real department in its own session is untouched by the skip.
    board, root_id, (builder_id,) = _department(platform, AgentType.BUILDER)
    assert {r["agent_run_id"] for r in platform.blackboard.roster(board)} == {
        root_id, builder_id,
    }


# --- end to end through the REAL delegation path --------------------------


async def test_a_delegated_child_is_addressable_by_name_end_to_end(platform):
    """Not a seam: a supervisor runs, delegates a real sub-agent through the
    orchestrator, and can then address that child BY NAME on its own board —
    without the child having posted anything. Offline (mock provider)."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import get_agent_definition

    orch = Orchestrator(platform)
    root_session = await orch.create_session("lead the team", AgentType.SUPERVISOR)
    runtime = AgentRuntime(platform)
    root_run = await runtime.run(
        root_session, get_agent_definition(AgentType.SUPERVISOR)
    )
    child_session = await orch.create_session("do the sub-task", AgentType.RESEARCHER)
    await runtime.run(
        child_session,
        get_agent_definition(AgentType.RESEARCHER),
        parent_id=root_run.id,
    )

    board = platform.blackboard.board_id_for(root_session.id, root_run.id)
    assert board == root_session.id
    handles = {r["handle"] for r in platform.blackboard.roster(board)}
    assert handles == {"supervisor", "researcher"}

    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "researcher", "text": "cross-check the depreciation"},
        _ctx(platform, root_run.id, root_session.id),
        platform.permissions,
    )
    assert res.ok, res.error
    assert res.data["to_name"] == "researcher"
    assert platform.blackboard.list(board)[0].author_name == "supervisor"
