"""v1.170.0 P3 — reflex pins + the workflow tool surface.

Four seams, all offline:

  * PIN FIX — a webhook/comm-triggered run of a SAVED workflow resolves
    through ``WorkflowStore.load_def`` (the one stored-record -> def seam), so
    the def's project pin now rides the run instead of being silently dropped
    (the same workflow ran grounded from the dashboard but ungrounded from a
    signal). The ``/run`` phone command takes the same path.
  * /status HONESTY — a parked (``waiting``) or just-answered (``resuming``)
    run counts as live work; before, a run waiting on the person reading
    /status was invisible to them.
  * ``workflow_list`` — READ-ONLY discovery (name/description/step count/pin),
    auto-armable (AUTO_SAFE_TOOLS) and allow-by-default, with an explicit
    user-configured mode always winning.
  * ``workflow_run`` — name-only run through the engine (load_def, honest
    unknown-name error naming what exists, contract-2 result data, optional
    contract-5 ``inputs``), fail-closed at "ask".
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

# Register the workflow tables on SQLModel.metadata BEFORE any platform is
# built (build_platform -> init_db creates the tables). Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401
import iron_jarvis.workflows.store  # noqa: F401 — registers WorkflowPinRecord

from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import PermissionMode, Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.reflex.router import ReflexRouter
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools
from iron_jarvis.tools.base import Reversibility, ToolContext
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore

_NOTIFY_STEPS = [{"name": "Tell", "kind": "notify", "message": "fired"}]


def _add_project(platform, project_id: str, name: str, root: str = "") -> None:
    with session_scope(platform.engine) as db:
        db.add(Project(id=project_id, name=name, root=root))
        db.commit()


def _ctx(platform, tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id="r",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _run_record(platform, run_id: str) -> WorkflowRunRecord:
    with session_scope(platform.engine) as db:
        return db.get(WorkflowRunRecord, run_id)


def _run_count(platform) -> int:
    with session_scope(platform.engine) as db:
        from sqlmodel import select

        return len(list(db.exec(select(WorkflowRunRecord))))


async def _wait_terminal(platform, run_id: str) -> str:
    status = "running"
    for _ in range(300):
        status = _run_record(platform, run_id).status
        if status not in ("running", "resuming"):
            break
        await asyncio.sleep(0.02)
    return status


def _mock_channel(platform) -> MockChannel:
    return next(
        ch
        for ch in platform.notifier._channels.values()
        if isinstance(ch, MockChannel)
    )


# --------------------------------------------------------------------------- #
# 1. PIN FIX — a signal-triggered run of a saved def keeps its project pin.
# --------------------------------------------------------------------------- #
async def test_reflex_webhook_run_carries_saved_pin(tmp_path):
    platform = build_platform(str(tmp_path))
    _add_project(platform, "proj_pin", "Client Work")
    WorkflowStore(platform.engine).save(
        "pinned-hook", _NOTIFY_STEPS, project_id="proj_pin"
    )
    router = ReflexRouter(platform, Orchestrator(platform), spawn_bg=None)
    router.store.add(
        name="hook", source="webhook", match="gh", action="workflow",
        target="pinned-hook",
    )

    fired = await router.on_webhook("gh", {"text": "payload"})

    assert fired and fired[0]["ok"] is True
    assert fired[0]["workflow"] == "pinned-hook"
    run_id = fired[0]["run_id"]
    # create_record is synchronous — the pin is on the record already. Before
    # the load_def fix the router built the def by hand and this was None.
    assert _run_record(platform, run_id).project_id == "proj_pin"
    # And the run still completes (the launch path is untouched).
    assert await _wait_terminal(platform, run_id) == "completed"


async def test_reflex_trigger_injection_still_rides_the_pinned_run(tmp_path):
    # The {{Trigger}} injection (v1.122.0) must survive the load_def rewrite —
    # signal payload AND pin on the same run.
    platform = build_platform(str(tmp_path))
    _add_project(platform, "proj_pin", "Client Work")
    mock = _mock_channel(platform)
    WorkflowStore(platform.engine).save(
        "announce",
        [{"name": "Tell", "kind": "notify", "message": "Got: {{Trigger}}"}],
        project_id="proj_pin",
    )
    router = ReflexRouter(platform, Orchestrator(platform), spawn_bg=None)
    router.store.add(
        name="hook", source="webhook", match="gh", action="workflow",
        target="announce",
    )

    fired = await router.on_webhook("gh", {"text": "hello from github"})
    run_id = fired[0]["run_id"]
    assert await _wait_terminal(platform, run_id) == "completed"

    rec = _run_record(platform, run_id)
    assert rec.project_id == "proj_pin"
    outs = json.loads(rec.outputs_json)
    assert outs["Trigger"]["summary"] == "hello from github"
    assert any("Got: hello from github" in m for m in mock.sent)


def test_run_command_carries_saved_pin(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        ci = client.app.state.command_interpreter
        _add_project(p, "proj_cmd", "Phone Ops")
        WorkflowStore(p.engine).save(
            "pinned-cmd", _NOTIFY_STEPS, project_id="proj_cmd"
        )

        reply = asyncio.run(ci.interpret("/run pinned-cmd"))

        assert "pinned-cmd" in reply
        with session_scope(p.engine) as db:
            from sqlmodel import select

            rec = db.exec(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.workflow_name == "pinned-cmd"
                )
            ).first()
        assert rec is not None
        assert rec.project_id == "proj_cmd"


# --------------------------------------------------------------------------- #
# 2. /status — waiting + resuming are LIVE work (and total stays total).
# --------------------------------------------------------------------------- #
def _seed_runs(p, statuses) -> None:
    with session_scope(p.engine) as db:
        for st in statuses:
            db.add(WorkflowRunRecord(workflow_name=f"w-{st}", status=st))
        db.commit()


def test_status_counts_parked_runs_as_live(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        ci = client.app.state.command_interpreter
        _seed_runs(
            p,
            ("running", "waiting", "resuming", "cancelling", "completed", "failed"),
        )

        status = asyncio.run(ci.interpret("/status"))

        # running + waiting + resuming + cancelling = 4 live of 6 total; the
        # one parked run is called out to the person it is waiting ON.
        assert "Workflows: 4 live, 6 total" in status
        assert "(1 waiting on you)" in status


def test_status_without_parked_runs_stays_quiet(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        ci = client.app.state.command_interpreter
        _seed_runs(p, ("running", "completed"))

        status = asyncio.run(ci.interpret("/status"))

        assert "Workflows: 1 live, 2 total" in status
        assert "waiting on you" not in status


# --------------------------------------------------------------------------- #
# 3. REGISTRATION + PERMISSION TIERS.
# --------------------------------------------------------------------------- #
def test_workflow_tools_registered_with_correct_tiers(tmp_path):
    platform = build_platform(str(tmp_path))
    names = platform.registry.names()
    assert "workflow_create" in names
    assert "workflow_list" in names
    assert "workflow_run" in names

    lister = platform.registry.get("workflow_list")
    assert lister.reversibility is Reversibility.READONLY
    runner = platform.registry.get("workflow_run")
    # Runs spawn agent steps with real side effects — NEVER declared readonly.
    assert runner.reversibility is not Reversibility.READONLY

    # workflow_list: read-only tier, allow-by-default (a headless "ask" is a
    # DENY, which would blind every agent/scheduled run).
    assert platform.permissions.mode_for("workflow_list") is PermissionMode.ALLOW
    # DISPLAY PARITY: the engine's _base is a construction-time COPY, so the
    # default must ALSO land on config.permissions — `ironjarvis tools` and
    # GET /settings read that mapping, and seeding only the engine would show
    # "ask" while the engine enforces "allow" (display-vs-enforcement drift).
    assert platform.config.permissions.get("workflow_list") == "allow"
    # workflow_run: deliberately NO default entry — fail-closed to "ask",
    # on BOTH copies (no entry to display, "ask" resolution to enforce).
    assert platform.permissions.mode_for("workflow_run") is PermissionMode.ASK
    assert "workflow_run" not in platform.config.permissions


def test_workflow_list_user_configured_deny_wins(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        '[permissions]\nworkflow_list = "deny"\n', encoding="utf-8"
    )
    platform = build_platform(str(tmp_path))
    # setdefault semantics: the declared default NEVER overrides the user —
    # on the enforcing engine AND on the displayed config mapping alike.
    assert platform.permissions.mode_for("workflow_list") is PermissionMode.DENY
    assert platform.config.permissions.get("workflow_list") == "deny"


# --------------------------------------------------------------------------- #
# 4. workflow_list — honest discovery.
# --------------------------------------------------------------------------- #
async def test_workflow_list_reports_defs_pins_and_empty_state(tmp_path):
    platform = build_platform(str(tmp_path))
    tool = platform.registry.get("workflow_list")
    ctx = _ctx(platform, tmp_path)

    empty = await tool.execute({}, ctx)
    assert empty.ok
    assert empty.data == {"workflows": [], "count": 0}
    assert "No saved workflows" in empty.output

    _add_project(platform, "proj_a", "Client Work")
    store = WorkflowStore(platform.engine)
    store.save("plain", _NOTIFY_STEPS, description="unpinned one")
    store.save(
        "pinned",
        _NOTIFY_STEPS + [{"name": "More", "kind": "notify", "message": "m"}],
        project_id="proj_a",
    )
    store.save("dangling", _NOTIFY_STEPS, project_id="proj_gone")

    res = await tool.execute({}, ctx)

    assert res.ok
    assert res.data["count"] == 3
    by_name = {e["name"]: e for e in res.data["workflows"]}
    assert by_name["plain"]["project"] is None
    assert by_name["plain"]["project_id"] is None
    assert by_name["plain"]["steps"] == 1
    assert by_name["plain"]["description"] == "unpinned one"
    # The pin surfaces as the project's NAME (what the user calls it).
    assert by_name["pinned"]["project"] == "Client Work"
    assert by_name["pinned"]["project_id"] == "proj_a"
    assert by_name["pinned"]["steps"] == 2
    # A dangling pin (project deleted) falls back to the raw id — it must not
    # masquerade as unpinned.
    assert by_name["dangling"]["project"] == "proj_gone"
    assert "pinned" in res.output and "Client Work" in res.output


async def test_workflow_list_output_is_bounded_and_reports_truncation(tmp_path):
    # Defs are agent-mintable and never pruned — the human-readable text must
    # not dump a whole catalog into the model context. data stays COMPLETE
    # (that is the _store_as/repl escape hatch), and the truncation is
    # REPORTED (the repo rule: a silently short listing reads as complete).
    platform = build_platform(str(tmp_path))
    tool = platform.registry.get("workflow_list")
    store = WorkflowStore(platform.engine)
    total = tool.OUTPUT_CAP + 5
    for i in range(total):
        store.save(f"wf-{i:03d}", _NOTIFY_STEPS)

    res = await tool.execute({}, _ctx(platform, tmp_path))

    assert res.ok
    assert res.data["count"] == total
    assert len(res.data["workflows"]) == total  # data carries EVERYTHING
    assert res.output.count("•") == tool.OUTPUT_CAP  # text carries the cap
    assert "(+5 more — data carries the full list)" in res.output
    # And at/below the cap there is no truncation note at all.
    for i in range(5):
        store.remove(f"wf-{i:03d}")
    exact = await tool.execute({}, _ctx(platform, tmp_path))
    assert exact.data["count"] == tool.OUTPUT_CAP
    assert exact.output.count("•") == tool.OUTPUT_CAP
    assert "more — data carries" not in exact.output


# --------------------------------------------------------------------------- #
# 5. workflow_run — name-only run, contract 2 data, honest errors, inputs.
# --------------------------------------------------------------------------- #
async def test_workflow_run_unknown_name_names_known_workflows(tmp_path):
    platform = build_platform(str(tmp_path))
    tool = platform.registry.get("workflow_run")
    ctx = _ctx(platform, tmp_path)
    WorkflowStore(platform.engine).save("nightly", _NOTIFY_STEPS)

    res = await tool.execute({"name": "nope"}, ctx)

    assert res.ok is False
    assert "no saved workflow 'nope'" in res.error
    assert "nightly" in res.error  # the honest hint: what DOES exist
    assert _run_count(platform) == 0  # nothing started

    missing = await tool.execute({"name": ""}, ctx)
    assert missing.ok is False and "name is required" in missing.error

    # With nothing saved at all, point at workflow_create instead of listing.
    WorkflowStore(platform.engine).remove("nightly")
    bare = await tool.execute({"name": "nope"}, ctx)
    assert bare.ok is False and "workflow_create" in bare.error


async def test_workflow_run_by_name_reports_contract_and_keeps_pin(tmp_path):
    platform = build_platform(str(tmp_path))
    _add_project(platform, "proj_t", "Tool Runs")
    WorkflowStore(platform.engine).save(
        "pinned-tool", _NOTIFY_STEPS, project_id="proj_t"
    )
    tool = platform.registry.get("workflow_run")

    res = await tool.execute({"name": "pinned-tool"}, _ctx(platform, tmp_path))

    assert res.ok
    # Contract 2: exactly these three fields — the chat lanes key off them.
    assert set(res.data) == {"run_id", "workflow", "status"}
    assert res.data["workflow"] == "pinned-tool"
    assert res.data["status"] == "running"
    run_id = res.data["run_id"]
    rec = _run_record(platform, run_id)
    assert rec is not None
    assert rec.project_id == "proj_t"  # load_def — the pin rides the run
    assert run_id in res.output or "pinned-tool" in res.output
    # The background launch actually drives the run to completion.
    assert await _wait_terminal(platform, run_id) == "completed"


async def test_workflow_run_inputs_seed_and_resolve_templates(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    WorkflowStore(platform.engine).save(
        "greet",
        [{"name": "Tell", "kind": "notify", "message": "Hi {{Client}}"}],
    )
    tool = platform.registry.get("workflow_run")

    res = await tool.execute(
        {"name": "greet", "inputs": {"Client": "Acme", "n": 5}},
        _ctx(platform, tmp_path),
    )

    assert res.ok
    run_id = res.data["run_id"]
    assert await _wait_terminal(platform, run_id) == "completed"
    outs = json.loads(_run_record(platform, run_id).outputs_json)
    assert outs["Client"] == {
        "status": "completed", "summary": "Acme", "kind": "input",
    }
    # Non-string values are stringified at the tool boundary (JSON-shaped).
    assert outs["n"]["summary"] == "5"
    # And templating resolved the seeded value inside the step.
    assert any("Hi Acme" in m for m in mock.sent)


async def test_workflow_run_input_step_collision_is_honest_error(tmp_path):
    platform = build_platform(str(tmp_path))
    WorkflowStore(platform.engine).save("collide", _NOTIFY_STEPS)
    tool = platform.registry.get("workflow_run")

    res = await tool.execute(
        {"name": "collide", "inputs": {"Tell": "x"}}, _ctx(platform, tmp_path)
    )

    assert res.ok is False
    assert "collides" in res.error
    assert _run_count(platform) == 0  # the honest error created NO run


async def test_workflow_run_prefers_the_managed_spawner(tmp_path):
    # With the daemon's orchestrator attached, the run must launch through
    # spawn_managed (registered for cancellation + graceful shutdown), keyed
    # by the RUN id — the same path POST /workflows/run takes.
    platform = build_platform(str(tmp_path))
    WorkflowStore(platform.engine).save("managed", _NOTIFY_STEPS)
    spawned: list[tuple[str, object]] = []
    sentinel_task = object()  # a REAL spawner returns the task, never None

    def _spawn(sid, coro):
        spawned.append((sid, coro))
        return sentinel_task

    platform.orchestrator = SimpleNamespace(spawn_managed=_spawn)
    tool = platform.registry.get("workflow_run")

    res = await tool.execute({"name": "managed"}, _ctx(platform, tmp_path))

    assert res.ok
    assert len(spawned) == 1
    sid, coro = spawned[0]
    assert sid == res.data["run_id"]
    coro.close()  # never started — close so nothing leaks


async def test_workflow_run_rejects_malformed_inputs_honestly(tmp_path):
    # A non-dict `inputs` (the model passing "Client=Acme" or a JSON string)
    # must be an HONEST error — before, it was silently dropped and the run
    # started WITHOUT inputs, leaving every {{name}} template unresolved.
    platform = build_platform(str(tmp_path))
    WorkflowStore(platform.engine).save("greet2", _NOTIFY_STEPS)
    tool = platform.registry.get("workflow_run")
    ctx = _ctx(platform, tmp_path)

    for bad in ("Client=Acme", '{"Client": "Acme"}', ["Client"], 7, True):
        res = await tool.execute({"name": "greet2", "inputs": bad}, ctx)
        assert res.ok is False, f"inputs={bad!r} must be refused"
        assert "inputs must be an object" in res.error
    assert _run_count(platform) == 0  # the honest error started NOTHING

    # Empty dict still equals absent (byte-identical pre-v1.170.0 launch).
    ok = await tool.execute({"name": "greet2", "inputs": {}}, ctx)
    assert ok.ok
    assert await _wait_terminal(platform, ok.data["run_id"]) == "completed"


async def test_workflow_run_refused_spawn_fails_the_record_honestly(tmp_path):
    # Daemon draining: spawn_managed closes the coroutine and returns None.
    # The record was already persisted "running" — the tool must flip it to
    # "failed" (unfinished statuses are never pruned) and answer ok=False,
    # never claim a run started that will never execute.
    platform = build_platform(str(tmp_path))
    WorkflowStore(platform.engine).save("drained", _NOTIFY_STEPS)

    def _refuse(sid, coro):
        coro.close()  # what the real drain path does before returning None
        return None

    platform.orchestrator = SimpleNamespace(spawn_managed=_refuse)
    tool = platform.registry.get("workflow_run")

    res = await tool.execute({"name": "drained"}, _ctx(platform, tmp_path))

    assert res.ok is False
    assert "shutting down" in res.error
    assert "not started" in res.error
    rec = _run_record(
        platform,
        next(
            r.id
            for r in _all_runs(platform)
            if r.workflow_name == "drained"
        ),
    )
    assert rec.status == "failed"
    assert rec.finished_at is not None  # settled, not a forever-spinning row
    outs = json.loads(rec.outputs_json)
    assert outs["__launch__"]["status"] == "failed"
    assert "not started" in outs["__launch__"]["summary"]


def _all_runs(platform) -> list[WorkflowRunRecord]:
    with session_scope(platform.engine) as db:
        from sqlmodel import select

        return list(db.exec(select(WorkflowRunRecord)))


# --------------------------------------------------------------------------- #
# 6. AUTOSELECT — only the read-only lister is auto-armable.
# --------------------------------------------------------------------------- #
def test_autoselect_arms_workflow_list_only():
    assert "workflow_list" in AUTO_SAFE_TOOLS
    # Starting a run is consent-gated: NEVER auto-armable.
    assert "workflow_run" not in AUTO_SAFE_TOOLS

    armed = select_auto_tools("run my month-end workflow")
    assert "workflow_list" in armed
    assert "workflow_run" not in armed
    assert "workflow_list" in select_auto_tools("what workflows do I have?")
    # Plain conversation stays tool-free.
    assert "workflow_list" not in select_auto_tools("how are you today?")
