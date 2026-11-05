"""Wave 3 group G review: projects and the user's own agents from chat.

The eight tools' Undo must put back what THIS change did and nothing else:
never the user's later edits, never a project or agent removed or remade
since, never a project that work was filed in. A folder spelled as a network
share is refused by its TEXT before anything touches the disk (the v1.379.0
SMB lesson). The safety doors (low trust, the POST lane, "Ask before every
action") keep these tools behind a card or away entirely. Every behaviour test
drives the REAL stream lane and ``registry.invoke`` (the builder's helpers).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_projects as RP
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_records_projects_v14350 import (
    _agent_row,
    _finished,
    _project,
    _project_rows,
)
from tests.test_settings_chat_tools_v1321 import _body
from tests.test_settings_records_v1321 import _app, _approve_all, _card

P = frozenset(RP.PROJECT_TOOLS)
A = frozenset(RP.AGENT_TOOLS)


def _undo_answer(app, card):
    """The Undo's raw answer (status, json) — it may honestly refuse."""
    client = TestClient(app)
    look = client.get(f"/config/changes/{card['change_id']}")
    assert look.status_code == 200, look.text
    r = client.post(f"/undo/{look.json()['action_id']}")
    return r.status_code, r.json()


# --------------------------------------------------------------------------- #
# Undo puts back only what THIS change did
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_undo_of_a_rename_keeps_what_the_user_changed_on_the_page_since(tmp_path):
    app = _app(tmp_path / "home", [("project_update", {"project": "Johnson", "new_name": "Johnson 2026"})])
    p = app.state.platform
    c = TestClient(app)
    pid = c.post("/projects", json={"name": "Johnson", "brief": "first"}).json()["id"]
    frames = await _drive_stream(app, _body("rename the Johnson project to Johnson 2026"), await _approve_all(app, []))
    assert _project(p, "Johnson 2026") is not None
    # Later, on the Projects page: instructions and a description of their own.
    c.patch(f"/projects/{pid}", json={"instructions": "Always cite the workpaper.", "brief": "re-scoped"})
    status, out = _undo_answer(app, _card(frames, "Project changed"))
    assert status == 200, out
    row = _project(p, "Johnson")
    assert row is not None, "the rename is undone"
    assert row["instructions"] == "Always cite the workpaper.", "the user's later instructions are kept"
    assert row["brief"] == "re-scoped", "the user's later description is kept"


@pytest.mark.asyncio
async def test_undo_refuses_when_the_same_field_was_changed_again_since(tmp_path):
    app = _app(tmp_path / "home", [("project_update", {"project": "Johnson", "new_name": "Johnson 2026"})])
    p = app.state.platform
    c = TestClient(app)
    pid = c.post("/projects", json={"name": "Johnson"}).json()["id"]
    frames = await _drive_stream(app, _body("rename the Johnson project to Johnson 2026"), await _approve_all(app, []))
    c.patch(f"/projects/{pid}", json={"name": "Johnson LLC"})
    before = _project_rows(p)
    status, out = _undo_answer(app, _card(frames, "Project changed"))
    assert status == 409, out
    words = str(out.get("detail") or "")
    assert "changed again" in words and "name" in words, words
    assert _project_rows(p) == before, "nothing is put back over the user's newer name"


@pytest.mark.asyncio
async def test_undo_of_a_change_to_a_project_removed_since_never_brings_it_back(tmp_path):
    app = _app(tmp_path / "home", [("project_update", {"project": "Lee", "description": "estate"})])
    p = app.state.platform
    c = TestClient(app)
    pid = c.post("/projects", json={"name": "Lee"}).json()["id"]
    frames = await _drive_stream(app, _body("update the description of the Lee project"), await _approve_all(app, []))
    c.delete(f"/projects/{pid}")
    status, out = _undo_answer(app, _card(frames, "Project changed"))
    assert status == 409, out
    assert "removed" in str(out.get("detail") or "")
    assert _project_rows(p) == [], "a project the user removed stays removed"


@pytest.mark.asyncio
async def test_undo_of_a_rename_never_refocuses_a_project_the_user_unfocused(tmp_path):
    app = _app(tmp_path / "home", [("project_update", {"project": "Patel", "new_name": "Patel 2026"})])
    p = app.state.platform
    c = TestClient(app)
    pid = c.post("/projects", json={"name": "Patel"}).json()["id"]
    assert p.config.active_project_id == pid
    frames = await _drive_stream(app, _body("rename the Patel project to Patel 2026"), await _approve_all(app, []))
    c.post("/projects/deactivate")
    assert not p.config.active_project_id
    status, out = _undo_answer(app, _card(frames, "Project changed"))
    assert status == 200, out
    assert _project(p, "Patel") is not None
    assert not p.config.active_project_id, "the rename never took the focus, so its Undo gives none back"


@pytest.mark.asyncio
async def test_undo_of_a_made_project_with_a_schedule_or_team_since_archives_it(tmp_path):
    app = _app(tmp_path / "home", [("project_create", {"name": "Brown"})])
    p = app.state.platform
    frames = await _drive_stream(app, _body("set up a project for the Brown audit"), await _approve_all(app, []))
    pid = _project(p, "Brown")["id"]
    # Later: a task schedule filed in the project (it SPAWNS work tagged to it).
    p.scheduler.add_task("brown-nightly", "0 2 * * *", kind="task", payload={"task": "check", "project_id": pid})
    status, out = _undo_answer(app, _card(frames, "Project created"))
    assert status == 200, out
    assert "archived" in out["output"], out
    row = _project(p, "Brown")
    assert row is not None and row["status"] == "archived", "work filed in it keeps the project"
    assert p.scheduler.get("brown-nightly").decoded_payload().get("project_id") == pid, "the schedule keeps its project"


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["goal", "workflow"])
async def test_undo_of_a_made_project_with_a_goal_or_pinned_workflow_since_archives_it(tmp_path, bound):
    """Any table that carries the project's id counts as work filed in it
    (the first cut asked only chats, runs and knowledge)."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.goals.models import GoalContractRecord
    from iron_jarvis.workflows.store import WorkflowStore

    app = _app(tmp_path / "home", [("project_create", {"name": "Brown"})])
    p = app.state.platform
    frames = await _drive_stream(app, _body("set up a project for the Brown audit"), await _approve_all(app, []))
    pid = _project(p, "Brown")["id"]
    if bound == "goal":
        with session_scope(p.engine) as db:
            db.add(GoalContractRecord(name="close", contract_text="close the books", project_id=pid))
            db.commit()
    else:
        WorkflowStore(p.engine).save("brown-intake", [{"id": "a", "type": "agent", "task": "x"}], project_id=pid)
    status, out = _undo_answer(app, _card(frames, "Project created"))
    assert status == 200, out
    assert "archived" in out["output"], out
    assert _project(p, "Brown")["status"] == "archived"


@pytest.mark.asyncio
async def test_undo_of_a_made_project_renamed_since_archives_it_instead_of_removing(tmp_path):
    app = _app(tmp_path / "home", [("project_create", {"name": "Garcia"})])
    p = app.state.platform
    frames = await _drive_stream(app, _body("create a project named Garcia"), await _approve_all(app, []))
    pid = _project(p, "Garcia")["id"]
    TestClient(app).patch(f"/projects/{pid}", json={"instructions": "Books close on the 5th."})
    status, out = _undo_answer(app, _card(frames, "Project created"))
    assert status == 200, out
    row = _project(p, "Garcia")
    assert row is not None and row["status"] == "archived" and row["instructions"] == "Books close on the 5th."


@pytest.mark.asyncio
async def test_undo_of_an_agent_change_keeps_the_users_later_instructions(tmp_path):
    app = _app(tmp_path / "home", [("agent_update", {"agent": "bookkeeper", "provider": "openai", "model": "gpt-6"})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "bookkeeper", "system_prompt": "Keep the books."})
    frames = await _drive_stream(app, _body("switch the bookkeeper agent to GPT"), await _approve_all(app, []))
    c.patch("/agents/bookkeeper", json={"system_prompt": "Keep the books. Reconcile monthly."})
    status, out = _undo_answer(app, _card(frames, "Agent changed"))
    assert status == 200, out
    row = _agent_row(p, "bookkeeper")
    assert (row["provider"], row["model"]) == ("", ""), "the model change is undone"
    assert row["system_prompt"] == "Keep the books. Reconcile monthly.", "the user's later instructions are kept"
    files = p.agents_registry.files
    assert "Reconcile monthly." in (files.read_instructions("bookkeeper") or "")


@pytest.mark.asyncio
async def test_undo_of_a_removal_never_overwrites_an_agent_made_since_with_that_name(tmp_path):
    app = _app(tmp_path / "home", [("agent_delete", {"agent": "intake"})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "intake", "system_prompt": "old intake", "allowance_usd": 5})
    frames = await _drive_stream(app, _body("delete the intake agent"), await _approve_all(app, []))
    c.post("/agents", json={"name": "intake", "system_prompt": "the NEW intake", "allowance_usd": 40})
    newer = _agent_row(p, "intake")
    status, out = _undo_answer(app, _card(frames, "Agent removed"))
    assert status == 409, out
    assert "made since" in str(out.get("detail") or ""), out
    assert _agent_row(p, "intake") == newer, "the user's new agent is untouched"
    assert "the NEW intake" in (p.agents_registry.files.read_instructions("intake") or "")


@pytest.mark.asyncio
async def test_undo_of_a_made_agent_edited_since_refuses_rather_than_remove_the_edits(tmp_path):
    app = _app(tmp_path / "home", [("agent_create", {"name": "drafter", "instructions": "Draft letters."})])
    p = app.state.platform
    frames = await _drive_stream(app, _body("make a new agent called drafter"), await _approve_all(app, []))
    TestClient(app).patch("/agents/drafter", json={"allowance_usd": 30, "system_prompt": "Draft engagement letters."})
    edited = _agent_row(p, "drafter")
    status, out = _undo_answer(app, _card(frames, "Agent created"))
    assert status == 409, out
    assert "changed" in str(out.get("detail") or ""), out
    assert _agent_row(p, "drafter") == edited


@pytest.mark.asyncio
async def test_undo_of_a_pause_the_user_already_lifted_changes_nothing_else(tmp_path):
    app = _app(tmp_path / "home", [("agent_pause", {"agent": "intake", "reason": "away"})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "intake", "system_prompt": "x"})
    frames = await _drive_stream(app, _body("pause the intake agent"), await _approve_all(app, []))
    c.post("/agents/intake/resume")
    c.patch("/agents/intake", json={"allowance_usd": 12})
    later = _agent_row(p, "intake")
    status, out = _undo_answer(app, _card(frames, "Agent paused"))
    assert status == 200, out
    assert "already" in out["output"], out
    assert _agent_row(p, "intake") == later, "the user's resume and new limit are kept"


@pytest.mark.asyncio
async def test_undo_of_a_change_to_an_agent_removed_since_never_brings_it_back(tmp_path):
    app = _app(tmp_path / "home", [("agent_update", {"agent": "intake", "monthly_limit_usd": 9})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "intake", "system_prompt": "x"})
    frames = await _drive_stream(app, _body("give the intake agent a $9 monthly limit"), await _approve_all(app, []))
    c.delete("/agents/intake")
    status, out = _undo_answer(app, _card(frames, "Agent changed"))
    assert status == 409, out
    assert _agent_row(p, "intake") is None


@pytest.mark.asyncio
async def test_a_change_that_also_lifted_an_allowance_pause_says_so(tmp_path):
    """The route lifts an allowance auto-pause when the limit is raised; the
    card and the answer must say the agent is working again (and Undo puts
    the pause back)."""
    from iron_jarvis.daemon.routes.agents import AUTO_PAUSE_PREFIX

    app = _app(tmp_path / "home", [("agent_update", {"agent": "intake", "monthly_limit_usd": 50})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "intake", "system_prompt": "x", "allowance_usd": 1})
    p.agents_registry.set_paused("intake", f"{AUTO_PAUSE_PREFIX} ($1.00 of $1.00)")
    paused = _agent_row(p, "intake")
    frames = await _drive_stream(app, _body("raise the intake agent's monthly budget to $50"), await _approve_all(app, []))
    assert not _agent_row(p, "intake")["paused_reason"]
    card = _card(frames, "Agent changed")
    assert "paused" in card["old"] and "working" in card["new"], card
    status, out = _undo_answer(app, card)
    assert status == 200, out
    assert _agent_row(p, "intake") == paused


# --------------------------------------------------------------------------- #
# folders
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("folder", [r"\\ij-review-nohost.invalid\share\Johnson", "//ij-review-nohost.invalid/share"])
async def test_a_network_share_folder_is_refused_by_its_text_before_any_disk_touch(tmp_path, monkeypatch, folder):
    touched: list[str] = []
    real_is_dir = pathlib.Path.is_dir

    def spy_is_dir(self, *a, **kw):
        s = str(self).replace("/", "\\")
        if s.startswith("\\\\"):
            touched.append(str(self))
            return False
        return real_is_dir(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "is_dir", spy_is_dir)
    app = _app(
        tmp_path / "home",
        [("project_create", {"name": "Johnson", "folder": folder}), ("project_update", {"project": "Lee", "folder": folder})],
    )
    p = app.state.platform
    TestClient(app).post("/projects", json={"name": "Lee"})
    before = _project_rows(p)
    frames = await _drive_stream(app, _body("Make a project for the Johnson audit"), await _approve_all(app, []))
    errs = [f.get("output") or f.get("error") or "" for t in ("project_create", "project_update") for f in _finished(frames, t)]
    assert len(errs) == 2 and all("network" in e for e in errs), errs
    assert touched == [], "the share was never looked at"
    assert _project_rows(p) == before


@pytest.mark.asyncio
async def test_a_protected_folder_is_refused_and_nothing_is_made(tmp_path):
    app = _app(tmp_path / "home", [])
    p = app.state.platform
    keys = p.config.home / "secrets"
    keys.mkdir(parents=True, exist_ok=True)
    from tests.test_settings_chat_tools_v1321 import _calls_stream

    p.router.stream = _calls_stream([("project_create", {"name": "Keys", "folder": str(keys)})])
    frames = await _drive_stream(app, _body("make a project called Keys"), await _approve_all(app, []))
    err = _finished(frames, "project_create")[0]
    words = err.get("output") or err.get("error") or ""
    assert err.get("ok") is False and "protected" in words, words
    assert _project_rows(p) == []


# --------------------------------------------------------------------------- #
# the safety doors
# --------------------------------------------------------------------------- #


def _stream(app, body: dict, **kw):
    import asyncio
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import chat as chat_routes
    from iron_jarvis.daemon.schemas import ChatBody
    from tests.test_generate_media_tool_v1346 import _frames

    platform = app.state.platform
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    cards: list[dict] = []

    async def go():
        gen = await chat_routes.stream_chat_turn(platform, {}, ChatBody(**body), **kw)
        seen = []
        async for chunk in gen:
            for ev, data in _frames(chunk):
                seen.append((ev, data))
                if ev == "approval":
                    cards.append(data)
                    approvals.resolve(data["id"], "once")
        return seen

    return asyncio.run(asyncio.wait_for(go(), 60)), cards


def test_a_low_trust_turn_never_reaches_them(tmp_path):
    app = _app(tmp_path / "home", [("agent_delete", {"agent": "intake"}), ("project_create", {"name": "X"})])
    p = app.state.platform
    TestClient(app).post("/agents", json={"name": "intake", "system_prompt": "x"})
    frames, cards = _stream(app, _body("delete the intake agent and make a project called X"), trust="low", trust_reason="from the phone")
    assert cards == []
    assert _agent_row(p, "intake") is not None and _project_rows(p) == []
    for tool in ("agent_delete", "project_create"):
        assert all(f.get("ok") is False for f in _finished(frames, tool)), tool


def test_always_ask_cards_every_one(tmp_path):
    app = _app(tmp_path / "home", [("project_create", {"name": "Y"})])
    frames, cards = _stream(app, _body("make a project called Y", "always_ask"))
    assert [c["tool"] for c in cards] == ["project_create"]


@pytest.mark.asyncio
async def test_the_post_lane_never_runs_them(tmp_path):
    """The POST lane (the phone, a raw API caller with auto tools) can show no
    card, so it arms no ask-tier tool: a model that names one anyway is
    refused by the registry and nothing changes."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
    from iron_jarvis.providers.router import RouteResult

    app = _app(tmp_path / "home", [])
    p = app.state.platform
    TestClient(app).post("/agents", json={"name": "intake", "system_prompt": "x"})
    moves = [[ToolCall(id="c0", name="agent_delete", arguments={"agent": "intake"})],
             [ToolCall(id="c1", name="project_create", arguments={"name": "Z"})]]

    async def complete(**kw):
        if moves and kw.get("tools") is not None:
            return RouteResult(LLMResponse(text="", tool_calls=moves.pop(0), usage={}), "mock", "mock")
        return RouteResult(LLMResponse(text="Done.", usage={}), "mock", "mock")

    p.router.complete = complete
    body = ChatBody(
        messages=[{"role": "user", "content": "delete the intake agent and make a project called Z"}], auto_tools=True
    )
    await run_chat_turn(p, {}, body)
    assert _agent_row(p, "intake") is not None and _project_rows(p) == []


# --------------------------------------------------------------------------- #
# arming
# --------------------------------------------------------------------------- #

#: The review's own 42 sentences (none in the builder's table), measured
#: through ``records.record_tools_for`` — what the stream lane's
#: ``settings_ask_tools`` reads for these eight. Before the review's fixes:
#: 20/22 requests and 32/40 near misses right; after: 22/22 and 38/40. The two
#: near misses still offered are honestly ambiguous and are NOT pinned ("Start
#: a project to clean the garage", "Hire an agent to sell my house" — a person
#: might mean either; the tools are only OFFERED, each still asks on a card).
REVIEW_REQUESTS = [
    ("Please create a project called Henderson Trust", P),
    ("Make me a new project for the Ortiz 1040", P),
    ("Set up a new project named Q4 close", P),
    ("Archive the Henderson project, we're done with it", P),
    ("Can you rename the Ortiz project to Ortiz 2025 return", P),
    ("Switch over to the Q4 close project", P),
    (r"Point the Henderson project at D:\Clients\Henderson", P),
    ("Change the default model for the Q4 close project to GPT", P),
    ("Unarchive the Baker project please", P),
    ("I need a project for the Wilson estate", P),
    ("Stop focusing on any project", P),
    ("Make an agent named letter writer that drafts engagement letters", A),
    ("Create a reviewer agent that only reads my files", A),
    ("Pause the letter writer agent for now", A),
    ("Delete the old intake agent", A),
    ("Give the letter writer agent a $10 a month cap", A),
    ("Switch the bookkeeper agent over to Claude Sonnet", A),
    ("Remove the agent called intake", A),
    ("Turn the bookkeeper agent back on", A),
    ("Retire the tax checker agent", A),
    ("Add a new agent for client intake", A),
    ("Hire a bookkeeping agent", A),
]
REVIEW_NEAR_MISSES = [
    "Our project manager left the firm",
    "Send the project plan to Mark",
    "Write a project charter for the ERP rollout",
    "Create a project status report for this week",
    "Make a new Next.js project",
    "Start a new Flask project in my repo",
    "The agent at the bank wants my ID",
    "Email my real estate agent the closing docs",
    "Our IRS revenue agent scheduled an audit",
    "Make a list of projects I worked on this year",
    "Projected revenue for 2027 looks fine",
    "Archive these emails about the project",
    "Create an agent-based simulation in Python",
    "Remove the agent's signature from the letter",
    "Update the project timeline in the spreadsheet",
    "My travel agent booked the flight, add it to my calendar",
    "Delete the agent notes from yesterday's meeting",
    "Focus on the project budget numbers first",
    "Switch to the project tab",
    "Give the builder a call about the kitchen",
    "Help me plan a home project",
    "Create a new project in Visual Studio",
    "Open a project file in Excel",
    "Set up a meeting with the project team",
    "Add a project code to the invoice",
    "Make a project plan document for the Johnson audit",
    "Ask the project managers for their hours",
    "Our agent at the bank approved the wire",
    "Remove the bank agent from the CC line",
    "Pause the agent conversation and summarize it",
    "Move the project deadline to Friday",
    "Rename the project spreadsheet to final",
    "Change the agent's tone in this email draft to friendlier",
    "Make the project summary shorter",
    "Create a new coding project for a CLI tool",
    "Set up a new Python project with uv",
    "Switch to the agent view",
    "Delete the agent string from the logs",
]


def test_the_review_table_is_new_and_big_enough():
    from tests.test_records_projects_v14350 import AGENT_REQUESTS, NEAR_MISSES, PROJECT_REQUESTS

    builder = {t.casefold() for t in (*PROJECT_REQUESTS, *AGENT_REQUESTS, *NEAR_MISSES)}
    mine = [t for t, _ in REVIEW_REQUESTS] + REVIEW_NEAR_MISSES
    assert len(REVIEW_REQUESTS) >= 20 and len(REVIEW_NEAR_MISSES) >= 20
    assert not [t for t in mine if t.casefold() in builder]


def test_review_arming_rows():
    bad = []
    for text, fam in REVIEW_REQUESTS:
        got = set(R.record_tools_for(text)) & (P | A)
        if got != fam:
            bad.append((text, sorted(got)))
    for text in REVIEW_NEAR_MISSES:
        got = set(R.record_tools_for(text)) & (P | A)
        if got:
            bad.append((text, sorted(got)))
    assert bad == []


def test_the_rules_grow_linearly_on_pathological_text():
    """A RATIO, never a speed bar (the builder's own pin only waits 60 s for
    40k characters, which a quadratic rule can pass): every input below is
    timed at n and 4n characters in a child process with a deadline, best of
    three; a linear matcher grows about 4x, a quadratic one about 16x."""
    code = textwrap.dedent(
        """
        import json, time
        from iron_jarvis.settings.records_projects import projects_tools_for

        def cases(n):
            return [
                "point the " + "x " * n + "project at D:\\\\",
                "turn the " + "x " * n + "agent back on",
                "make a " + "list of " * n + "projects",
                "remove the agent" + "'s" * n,
                "delete the " + "a " * n + "agent notes",
                "make " + "x" * n,
                "make a " + "x" * n + " projectx",
                "change " + "a" * n + " agent's model",
                "give the builder " * (n // 8) + "a monthly limit",
                "a'b " * n + "project",
                "agent\\n" * n,
                "make " + "a-b " * n + "the active project",
                "stop focusing on " + "x " * n + "projects",
                "remove the agent from " + "x " * n + "team",
            ]

        def total(n):
            best = None
            for _ in range(3):
                t0 = time.perf_counter()
                for c in cases(n):
                    projects_tools_for(c)
                dt = time.perf_counter() - t0
                best = dt if best is None else min(best, dt)
            return best

        total(2_000)  # warm the compiled patterns
        print(json.dumps({"small": total(20_000), "big": total(80_000)}))
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    t = json.loads(out.stdout.strip().splitlines()[-1])
    assert t["big"] < max(t["small"], 0.005) * 10, t
