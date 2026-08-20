"""Roster truth (v1.193.0) — a track record BY NAME, and who is busy.

Two defects this pins:

1. ``AgentStatRecord`` was keyed on the builtin ``agent_type``, so the roster
   could only join stats for the eight builtins: every agent the USER created
   reported ``stats=None`` and the roster block a supervisor reads said
   "(no runs yet)" forever. A supervisor could pick the right ROLE, never the
   right AGENT. Outcomes are now keyed by the ROSTER NAME — identical to the
   type string for builtins, so their existing rows keep their history and
   nothing was migrated or orphaned.
2. The roster had no idea who was working. ``activity`` now surfaces the
   orchestrator's in-memory ``_running`` / ``_governed`` / ``_queued`` state
   into the prompt block, and degrades to "unknown" (never a guess, never a
   raise) when there is no orchestrator to ask.

Offline, deterministic, real platform + real DB.
"""

from __future__ import annotations

from types import SimpleNamespace

# Register the improvement tables on SQLModel.metadata BEFORE init_db.
import iron_jarvis.improvement.models  # noqa: F401

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.roster import (
    RosterEntry,
    build_roster,
    roster_block,
    session_roster_name,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.eval.models import Evaluation
from iron_jarvis.improvement.models import AgentStatRecord
from iron_jarvis.platform import build_platform


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


def _by_name(entries):
    return {e.name: e for e in entries}


def _seed_session(platform, *, score: float = 1.0, status=SessionStatus.COMPLETED) -> str:
    """A real, scored session row (score = 0.6*completion + 0.4*tool_success)."""
    sess = Session(task="t", agent_type=AgentType.BUILDER, status=status)
    sid = sess.id
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.add(
            Evaluation(
                session_id=sid,
                agent_run_id="r",
                completion=score,
                tool_success_rate=score,
            )
        )
        db.commit()
    return sid


# --- (1) stats by NAME ------------------------------------------------------


def test_dynamic_agent_earns_a_track_record_end_to_end(platform):
    """The whole path: a user-created agent runs, and the roster block a
    supervisor reads shows ITS record — not "(no runs yet)" forever."""
    platform.agents_registry.register(
        name="tax-reader",
        system_prompt="read tax documents",
        tools=["read_file"],
        base_type="builder",
        description="reads client tax documents",
    )
    entry = _by_name(build_roster(platform))["custom:tax-reader"]
    assert entry.stats is None  # nothing has run yet — honest
    assert "(no runs yet)" in entry.line()

    # Two real sessions attributed to that NAME (one clean, one failed).
    platform.improvement.record_outcome(
        _seed_session(platform, score=1.0), agent_name="custom:tax-reader"
    )
    platform.improvement.record_outcome(
        _seed_session(platform, score=0.0, status=SessionStatus.FAILED),
        agent_name="custom:tax-reader",
    )

    entry = _by_name(build_roster(platform))["custom:tax-reader"]
    assert entry.stats is not None, "a named agent must accumulate a history"
    assert entry.stats["sessions"] == 2
    assert entry.stats["success_rate"] == 0.5
    assert entry.line().endswith("(50% over 2 runs)")

    block = roster_block(platform)
    assert "custom:tax-reader" in block
    assert "50% over 2 runs" in block

    # The base type did NOT absorb the custom agent's runs (that is exactly the
    # mis-attribution this wave removes) — builder is still untouched.
    assert _by_name(build_roster(platform))["builder"].stats is None


def test_builtin_history_survives_the_rekey_no_orphan_row(platform):
    """Backward compatibility: rows written before this wave are keyed by the
    bare type string, which IS the builtin's roster name — so they keep
    accumulating in place instead of being stranded next to a new key."""
    with session_scope(platform.engine) as db:  # a pre-v1.193.0 row
        db.add(
            AgentStatRecord(
                agent_type="builder", session_count=5, score_sum=5.0, success_count=5
            )
        )
        db.commit()

    platform.improvement.record_outcome(_seed_session(platform, score=1.0))

    with session_scope(platform.engine) as db:
        rows = {r.agent_type: r for r in db.exec(select(AgentStatRecord))}
    assert list(rows) == ["builder"], "no orphan key may appear beside the old row"
    assert rows["builder"].session_count == 6  # the old history kept accruing
    entry = _by_name(build_roster(platform))["builder"]
    assert entry.stats["sessions"] == 6
    assert entry.line().endswith("(100% over 6 runs)")


def test_existing_install_self_heals_and_keeps_its_history(tmp_path):
    """The user's real DB: ``outcomerecord`` has no ``agent_name`` column and
    ``agentstatrecord`` holds bare-type rows. The additive column must be added
    by the house reconciler (never a bespoke migration), old rows must still
    READ (they come back NULL), and the builtin history must be untouched."""
    import sqlite3

    from iron_jarvis.core.db import open_db
    from iron_jarvis.improvement.models import OutcomeRecord

    path = str(tmp_path / "old.ironjarvis.db")
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE outcomerecord (id VARCHAR PRIMARY KEY, session_id VARCHAR, "
        "agent_type VARCHAR, score FLOAT, success BOOLEAN, lessons_applied VARCHAR, "
        "tools_used VARCHAR, created_at DATETIME)"
    )
    con.execute(
        "INSERT INTO outcomerecord VALUES "
        "('o1','s1','builder',1.0,1,'[]','[]','2026-01-01')"
    )
    con.execute(
        "CREATE TABLE agentstatrecord (agent_type VARCHAR PRIMARY KEY, "
        "session_count INTEGER, score_sum FLOAT, success_count INTEGER, "
        "recent_json VARCHAR, last_at DATETIME)"
    )
    con.execute("INSERT INTO agentstatrecord VALUES ('builder',7,7.0,7,'[1.0]',NULL)")
    con.commit()
    con.close()

    engine = open_db(path)
    with session_scope(engine) as db:
        old = db.exec(select(OutcomeRecord)).first()
        assert old.id == "o1"
        assert not old.agent_name  # NULL on a pre-v1.193.0 row, never a crash
        stat = db.get(AgentStatRecord, "builder")
    assert (stat.session_count, stat.score_sum) == (7, 7.0)  # history, not orphan


def test_remote_agent_history_via_the_session_less_door(platform):
    """A remote ask opens no Session, so it needs its own recording door —
    without one, ``remote:*`` shows "(no runs yet)" forever by construction."""
    platform.remote_agents = SimpleNamespace(
        list=lambda: [SimpleNamespace(name="hermes", kind="http-task", enabled=True)]
    )
    assert platform.improvement.record_agent_outcome("remote:hermes", success=True)
    assert platform.improvement.record_agent_outcome("remote:hermes", success=True)
    assert platform.improvement.record_agent_outcome("remote:hermes", success=False)

    entry = _by_name(build_roster(platform))["remote:hermes"]
    assert entry.stats["sessions"] == 3
    assert entry.line().endswith("(67% over 3 runs)")
    # Never raises, and refuses an empty name rather than minting a blank row.
    assert platform.improvement.record_agent_outcome("  ", success=True) is False


def test_stats_join_is_case_insensitive_and_survives_the_old_wire_key(platform):
    """The join reads the new "name" key but still understands a stats payload
    that only carries "agent_type" (an engine from before this wave)."""
    platform.agents_registry.register(
        name="Analyst", system_prompt="p", tools=[], base_type="builder"
    )
    platform.improvement = SimpleNamespace(
        stats=lambda: {
            "agents": [
                {"agent_type": "custom:analyst", "sessions": 4, "success_rate": 0.75},
                {"name": "builder", "sessions": 2, "success_rate": 1.0},
            ]
        }
    )
    entries = _by_name(build_roster(platform))
    assert entries["custom:Analyst"].stats["sessions"] == 4
    assert entries["builder"].stats["sessions"] == 2


def test_session_roster_name_is_the_one_predicate():
    """The single owner of session -> roster name (the roster READS it for
    liveness, the improvement engine WRITES stats with it)."""
    assert session_roster_name(
        SimpleNamespace(agent_type=AgentType.BUILDER, agent_name="custom:tax-reader")
    ) == "custom:tax-reader"
    # No name on the row: the honest answer is the builtin type it ran as.
    assert session_roster_name(SimpleNamespace(agent_type=AgentType.RESEARCHER)) == (
        "researcher"
    )
    assert session_roster_name(SimpleNamespace(agent_type=AgentType.BUILDER,
                                               agent_name="   ")) == "builder"
    # Never raises on an unreadable row.
    class _Radioactive:
        def __getattr__(self, item):
            raise RuntimeError("row is on fire")

    assert session_roster_name(_Radioactive()) == ""
    assert session_roster_name(None) == ""


# --- (2) liveness -----------------------------------------------------------


def test_busy_agent_is_marked_in_the_roster_block(platform):
    """A supervisor can see who is working instead of delegating blind."""
    platform.orchestrator = Orchestrator(platform)
    sid = _seed_session(platform, status=SessionStatus.ACTIVE)
    platform.orchestrator._running[sid] = object()  # what a live run looks like

    entries = _by_name(build_roster(platform))
    assert entries["builder"].activity == "busy"
    assert entries["researcher"].activity == "unknown"  # nothing claims it is free
    assert entries["builder"].as_dict()["activity"] == "busy"

    block = roster_block(platform)
    busy_line = [ln for ln in block.splitlines() if ln.startswith("- builder")][0]
    assert busy_line.endswith("(busy, no runs yet)")
    # the honesty rule holds THROUGH the new marker: a percentage still never
    # appears without its sample size
    platform.improvement.record_outcome(_seed_session(platform, score=1.0))
    line = _by_name(build_roster(platform))["builder"].line()
    assert line.endswith("(busy, 100% over 1 run)")


def test_queued_is_marked_and_a_running_run_wins_the_key(platform):
    platform.orchestrator = Orchestrator(platform)
    queued_id = _seed_session(platform, status=SessionStatus.QUEUED)
    platform.orchestrator._queued.append((queued_id, None, True))
    assert _by_name(build_roster(platform))["builder"].activity == "queued"
    assert "(queued, no runs yet)" in roster_block(platform)

    running_id = _seed_session(platform, status=SessionStatus.ACTIVE)
    platform.orchestrator._governed.add(running_id)  # a claimed concurrency slot
    assert _by_name(build_roster(platform))["builder"].activity == "busy"


def test_liveness_degrades_to_unknown_and_never_raises(platform):
    # No orchestrator attached (a bare platform, and every pure-fake test):
    assert all(e.activity == "unknown" for e in build_roster(platform))
    assert "(busy" not in roster_block(platform)

    # A poisoned orchestrator must cost the SIGNAL, never the roster.
    class _Poisoned:
        @property
        def _running(self):
            raise RuntimeError("orchestrator is on fire")

    platform.orchestrator = _Poisoned()
    entries = build_roster(platform)
    assert entries and all(e.activity == "unknown" for e in entries)
    assert roster_block(platform).startswith("# Who can take this work")

    # A live id we cannot resolve to a name is simply unknown, not a crash.
    platform.orchestrator = Orchestrator(platform)
    platform.orchestrator._running["session_that_does_not_exist"] = object()
    assert all(e.activity == "unknown" for e in build_roster(platform))


def test_busy_suffix_never_truncates_and_the_line_stays_clamped(platform):
    """The block's line budget is unchanged: the DESCRIPTION absorbs the longer
    suffix, so "(busy, 88% over 1234 runs)" can never become "(busy, 88% over 1…"."""
    long_desc = "an extremely long-winded description of this agent " * 4
    platform.agents_registry.register(
        name="verbose", system_prompt="p", tools=[], base_type="builder",
        description=long_desc,
    )
    platform.orchestrator = Orchestrator(platform)
    sid = _seed_session(platform, status=SessionStatus.ACTIVE)
    platform.orchestrator._running[sid] = object()
    with session_scope(platform.engine) as db:
        db.add(
            AgentStatRecord(
                agent_type="custom:verbose", session_count=1234, score_sum=1100.0,
                success_count=1086,
            )
        )
        db.commit()

    bullets = [ln for ln in roster_block(platform).splitlines() if ln.startswith("- ")]
    verbose = [ln for ln in bullets if ln.startswith("- custom:verbose")][0]
    assert verbose.endswith("(88% over 1234 runs)")  # run count intact
    assert "…" in verbose
    assert all(len(ln) <= 76 for ln in bullets)


def test_liveness_cap_drops_the_queue_tail_not_the_running_agents(platform):
    """``_LIVENESS_MAX`` truncates the id list, so the BUSY ids must lead it.

    With the queued ids first, a backlog longer than the cap evicted every
    actually-running id: each working agent reported not-busy while queued ones
    were marked — the signal inverted exactly when the queue mattered most.
    """
    from iron_jarvis.agents import roster as roster_mod

    platform.orchestrator = Orchestrator(platform)
    for _ in range(roster_mod._LIVENESS_MAX + 8):
        platform.orchestrator._queued.append(
            (_seed_session(platform, status=SessionStatus.QUEUED), None, True)
        )
    running = _seed_session(platform, status=SessionStatus.ACTIVE)
    platform.orchestrator._running[running] = object()

    assert _by_name(build_roster(platform))["builder"].activity == "busy"


# --- (3) the run is attributed to the teammate the SUPERVISOR NAMED ---------


def _stub_run(monkeypatch, result: str = "done"):
    """Replace the child's real LLM run with a completed stub (mock-free, fast).

    Everything AROUND it stays production code — the same create_session,
    delegation.started publish, session save and ``_post_run_learning`` hook the
    two handoff doors run on every child.
    """
    from iron_jarvis.agents import runtime as runtime_mod
    from iron_jarvis.core.models import AgentRun, AgentState

    async def _run(self, session, agent_def, parent_id=None):
        return AgentRun(
            session_id=session.id,
            state=AgentState.COMPLETED,
            provider=session.provider,
            model=session.model,
            result=result,
        )

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", _run)


def _ctx(platform, tmp_path, run_id="parent1"):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _agent_stats(platform) -> dict[str, int]:
    with session_scope(platform.engine) as db:
        return {r.agent_type: r.session_count for r in db.exec(select(AgentStatRecord))}


@pytest.fixture
def team(platform):
    """A platform with a user-created teammate and both handoff doors armed."""
    from iron_jarvis.agents.agent_tools import SpawnAgentTool
    from iron_jarvis.agents.delegate_tool import DelegateTool
    from iron_jarvis.tools.permissions import PermissionEngine

    platform.agents_registry.register(
        name="tax-reader",
        system_prompt="read tax documents",
        tools=["read_file"],
        base_type="builder",
        description="reads client tax documents",
    )
    platform.registry.register(DelegateTool(platform))
    platform.registry.register(SpawnAgentTool(platform, platform.agents_registry))
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, "delegate": "allow", "spawn_agent": "allow"}
    )
    platform.orchestrator = Orchestrator(platform)
    return platform


async def test_delegated_run_is_credited_to_the_custom_agent_not_its_base_type(
    team, tmp_path, monkeypatch
):
    """THE CAPABILITY, on the production path — no injected agent_name anywhere.

    Before this wave the run landed on ``builder`` and the roster block said
    "custom:tax-reader — … (no runs yet)" forever, so a supervisor could pick
    the right ROLE but never the right AGENT.
    """
    _stub_run(monkeypatch)
    res = await team.registry.invoke(
        "delegate",
        {"agent_type": "custom:tax-reader", "task": "read this K-1"},
        _ctx(team, tmp_path),
        team.permissions,
    )
    assert res.ok, res.error

    assert _agent_stats(team) == {"custom:tax-reader": 1}, (
        "the run must be credited to the teammate, never absorbed by its base type"
    )
    entries = _by_name(build_roster(team))
    assert entries["custom:tax-reader"].stats["sessions"] == 1
    assert entries["builder"].stats is None
    block = roster_block(team)
    assert "custom:tax-reader" in block and "over 1 run)" in block
    assert "custom:tax-reader — reads client tax documents (no runs yet)" not in block


async def test_spawn_and_delegate_write_ONE_key_for_the_same_teammate(
    team, tmp_path, monkeypatch
):
    """The two doors publish different strings for the same agent — ``delegate``
    the prefixed roster name, ``spawn_agent`` the bare slug it was called with.
    Unfolded, that splits one teammate's history across two keys."""
    _stub_run(monkeypatch)
    assert (
        await team.registry.invoke(
            "delegate",
            {"agent_type": "custom:tax-reader", "task": "a"},
            _ctx(team, tmp_path, "p1"),
            team.permissions,
        )
    ).ok
    assert (
        await team.registry.invoke(
            "spawn_agent",
            {"agent": "tax-reader", "task": "b"},
            _ctx(team, tmp_path, "p2"),
            team.permissions,
        )
    ).ok

    assert _agent_stats(team) == {"custom:tax-reader": 2}
    assert _by_name(build_roster(team))["custom:tax-reader"].stats["sessions"] == 2


async def test_a_builtin_delegation_still_lands_on_the_bare_type(
    team, tmp_path, monkeypatch
):
    """Backward compatibility on the live path: nothing gains a ``custom:``
    prefix it did not earn, so builtin history keeps accruing in place."""
    _stub_run(monkeypatch)
    assert (
        await team.registry.invoke(
            "delegate",
            {"agent_type": "researcher", "task": "look it up"},
            _ctx(team, tmp_path),
            team.permissions,
        )
    ).ok
    assert _agent_stats(team) == {"researcher": 1}


async def test_a_running_custom_agents_id_resolves_to_ITSELF_not_its_base_type(
    team, tmp_path, monkeypatch
):
    """NAME RESOLUTION for a live id — NOT end-to-end delegated-child liveness.

    Read the ``_running[session.id] = object()`` line below literally: this test
    HAND-INSERTS the child into a lane the production ``delegate`` path never
    enters. ``delegate``/``spawn_agent`` call ``AgentRuntime.run`` directly, so a
    real delegated child is in no ``_running``/``_governed``/``_queued`` set and
    the roster cannot see it at all — the blind spot is asserted below and
    documented in ``roster``'s LIVENESS BLIND SPOT section. What IS pinned here
    is the resolution rule that the orchestrator-managed and self-registered
    lanes DO exercise: a live session id belonging to a custom teammate must
    resolve to ``custom:tax-reader``, not to the ``builder`` it executes as —
    otherwise the marker is a false statement about builder and says nothing
    about the teammate a supervisor is choosing between.
    """
    seen: dict[str, str] = {}

    from iron_jarvis.agents import runtime as runtime_mod
    from iron_jarvis.core.models import AgentRun, AgentState

    async def _run(self, session, agent_def, parent_id=None):
        # THE BLIND SPOT, asserted rather than implied: mid-run, before anything
        # is hand-inserted, the delegated child is invisible to liveness.
        seen["before"] = _by_name(build_roster(team))["custom:tax-reader"].activity
        # INSIDE the child run: this is what the roster sees while it works —
        # once something puts the id in a lane the roster reads.
        team.orchestrator._running[session.id] = object()
        entries = _by_name(build_roster(team))
        seen["custom"] = entries["custom:tax-reader"].activity
        seen["builder"] = entries["builder"].activity
        seen["block"] = roster_block(team)
        team.orchestrator._running.pop(session.id, None)
        return AgentRun(
            session_id=session.id,
            state=AgentState.COMPLETED,
            provider=session.provider,
            model=session.model,
            result="done",
        )

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", _run)
    assert (
        await team.registry.invoke(
            "delegate",
            {"agent_type": "custom:tax-reader", "task": "read this K-1"},
            _ctx(team, tmp_path),
            team.permissions,
        )
    ).ok

    assert seen["before"] == "unknown", (
        "a delegated child enters no orchestrator lane — if this ever reports "
        "busy, the blind spot documented in roster.py has been closed and that "
        "docstring must be rewritten"
    )
    assert seen["custom"] == "busy"
    assert seen["builder"] == "unknown", "builder was never the one working"
    busy_lines = [ln for ln in seen["block"].splitlines() if "(busy" in ln]
    assert busy_lines and all(ln.startswith("- custom:tax-reader") for ln in busy_lines)


# --- (4) THE PRIMARY USER PATH: the Agents page's Run button ---------------


def _http(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    return TestClient(create_app(str(tmp_path)))


def test_the_agents_page_run_button_credits_the_users_own_agent(tmp_path):
    """``POST /agents/{name}/spawn`` is how a user runs THEIR OWN agent, and it
    publishes no ``delegation.started`` at all — so the ledger read could never
    reach it and the run was credited to the base type. A user running their own
    tax-reader left ``custom:tax-reader`` at "(no runs yet)" forever, which is
    the exact defect this attribution exists to remove.

    Fixed by an explicit ``Session.agent_name`` stamped at the door, so
    attribution no longer depends on an event being published at all.
    """
    with _http(tmp_path) as client:
        assert client.post(
            "/agents",
            json={
                "name": "tax-reader",
                "system_prompt": "read tax documents",
                "tools": ["read_file"],
                "description": "reads client tax documents",
            },
        ).status_code == 200
        r = client.post("/agents/tax-reader/spawn", json={"task": "read this K-1",
                                                          "wait": True})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        sid = r.json()["id"]

        platform = client.app.state.platform
        with session_scope(platform.engine) as db:
            row = db.get(Session, sid)
            assert row.agent_name == "custom:tax-reader", (
                "the door that RESOLVED the agent must stamp who ran"
            )
        assert _agent_stats(platform) == {"custom:tax-reader": 1}, (
            "the Run button's run must be credited to the user's own agent"
        )
        entries = _by_name(build_roster(platform))
        assert entries["custom:tax-reader"].stats["sessions"] == 1
        assert entries["builder"].stats is None  # the base type absorbed nothing
        assert "custom:tax-reader" in roster_block(platform)
        assert "(no runs yet)" not in [
            ln for ln in roster_block(platform).splitlines()
            if ln.startswith("- custom:tax-reader")
        ][0]


def test_spawning_a_BUILTIN_over_http_still_lands_on_the_bare_type(tmp_path):
    """The same door with no dynamic record behind it: a builtin's roster name
    IS its type string, so its history keeps accruing in place."""
    with _http(tmp_path) as client:
        r = client.post("/agents/reviewer/spawn", json={"task": "look", "wait": True})
        assert r.status_code == 200, r.text
        platform = client.app.state.platform
        with session_scope(platform.engine) as db:
            assert db.get(Session, r.json()["id"]).agent_name == "reviewer"
        assert _agent_stats(platform) == {"reviewer": 1}


def test_the_users_existing_db_gains_agent_name_and_old_rows_read_unattributed(
    tmp_path,
):
    """``Session.agent_name`` is ADDITIVE: the user's real ``ironjarvis.db``
    already has a ``session`` table, and ``create_all`` never adds a column to
    one. The house reconciler must add it (never a bespoke migration), and every
    row written before it existed must still READ — as unattributed, which
    resolves to exactly the pre-v1.193.0 answer."""
    import sqlite3

    from iron_jarvis.agents.roster import resolve_roster_name
    from iron_jarvis.core.db import open_db

    path = str(tmp_path / "old.ironjarvis.db")
    engine = open_db(path)
    with session_scope(engine) as db:
        db.add(Session(id="s_old", task="t", agent_type=AgentType.RESEARCHER))
        db.commit()
    engine.dispose()

    con = sqlite3.connect(path)  # rewind the schema to its pre-v1.193.0 shape
    con.execute("ALTER TABLE session DROP COLUMN agent_name")
    con.commit()
    assert "agent_name" not in {
        r[1] for r in con.execute('PRAGMA table_info("session")').fetchall()
    }
    con.close()

    engine = open_db(path)  # …and the reconciler puts it back
    with session_scope(engine) as db:
        row = db.get(Session, "s_old")
        assert not row.agent_name  # NULL on an old row, never a crash
        assert session_roster_name(row) == "researcher"
        assert resolve_roster_name(SimpleNamespace(), row) == "researcher"


# --- (5) a BUILTIN's run must never be credited to a same-named custom agent -


def test_a_dynamic_agent_may_shadow_a_builtin_name_without_stealing_its_runs(
    platform,
):
    """Nothing reserves the builtin names — ``register`` and ``POST /agents``
    both accept ``name="researcher"``. Folding a bare ``"researcher"`` into
    ``custom:researcher`` because such a record exists would credit the BUILTIN's
    run to the custom agent and mark the wrong entry busy: a FALSE track record,
    the one outcome this attribution must never produce."""
    from iron_jarvis.agents.roster import canonical_roster_name

    platform.agents_registry.register(
        name="researcher", system_prompt="p", tools=[], base_type="builder"
    )
    # The delegate door's string for the builtin is the bare, already-canonical
    # type value — it is NOT a bare slug awaiting a prefix.
    assert canonical_roster_name(platform, "researcher") == "researcher"
    assert canonical_roster_name(platform, "Researcher") == "Researcher"
    # …while the custom agent's own prefixed name is of course preserved.
    assert canonical_roster_name(platform, "custom:researcher") == "custom:researcher"


async def test_a_builtin_delegation_is_not_stolen_by_a_shadowing_custom_agent(
    team, tmp_path, monkeypatch
):
    """End to end on the production path: ``resolve_target`` matches builtins
    first, so delegating to ``researcher`` runs the BUILTIN — and the run must be
    measured under ``researcher``, leaving the shadowing record untouched."""
    _stub_run(monkeypatch)
    team.agents_registry.register(
        name="researcher", system_prompt="not the builtin", tools=[],
        base_type="builder", description="a shadowing impostor",
    )
    assert (
        await team.registry.invoke(
            "delegate",
            {"agent_type": "researcher", "task": "look it up"},
            _ctx(team, tmp_path),
            team.permissions,
        )
    ).ok
    assert _agent_stats(team) == {"researcher": 1}, (
        "the builtin's run must not be credited to the same-named custom agent"
    )
    entries = _by_name(build_roster(team))
    assert entries["researcher"].stats["sessions"] == 1
    assert entries["custom:researcher"].stats is None


def test_ledger_attribution_never_raises_and_never_guesses(platform):
    """The ledger read is a bonus: no engine, no rows, or a poisoned registry
    all degrade to what the session row alone can prove."""
    from iron_jarvis.agents.roster import (
        canonical_roster_name,
        ledger_roster_name,
        resolve_roster_name,
    )

    assert ledger_roster_name(platform, "") == ""
    assert ledger_roster_name(platform, "session_nope") == ""
    assert ledger_roster_name(SimpleNamespace(), "session_nope") == ""

    # A target string is only prefixed when a dynamic agent of that name EXISTS.
    assert canonical_roster_name(platform, "builder") == "builder"
    assert canonical_roster_name(platform, "remote:hermes") == "remote:hermes"
    platform.agents_registry.register(
        name="tax-reader", system_prompt="p", tools=[], base_type="builder"
    )
    assert canonical_roster_name(platform, "tax-reader") == "custom:tax-reader"
    assert canonical_roster_name(platform, "custom:tax-reader") == "custom:tax-reader"
    assert canonical_roster_name(platform, None) == ""

    # An explicit column (should one ever land) outranks the ledger.
    assert resolve_roster_name(
        platform, SimpleNamespace(id="s1", agent_type=AgentType.BUILDER,
                                 agent_name="custom:tax-reader")
    ) == "custom:tax-reader"
    assert resolve_roster_name(
        platform, SimpleNamespace(id="s1", agent_type=AgentType.RESEARCHER)
    ) == "researcher"

    class _Poisoned:
        def list(self):
            raise RuntimeError("registry is on fire")

    platform.agents_registry = _Poisoned()
    assert canonical_roster_name(platform, "tax-reader") == "tax-reader"


def test_entry_activity_defaults_to_unknown_for_existing_constructions():
    # The field is additive with a default — every pre-v1.193.0 six-arg
    # construction (there are several in the suite) still works and renders
    # exactly as it used to.
    e = RosterEntry("memory", "builtin", "curator", True, True, None)
    assert e.activity == "unknown"
    assert e.line() == "memory — curator (no runs yet)"
