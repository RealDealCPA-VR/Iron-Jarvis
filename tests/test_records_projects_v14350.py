"""v1.435.0 (chat-first plan Wave 3 group G): projects and the user's own
agents from chat — ``settings.records_projects``.

"If the app can do something, a request in plain words in chat does all of
the setup." Projects were page-only, and an agent could only be made through
the Automation agent's ``create_agent`` (no model, no team roles, no monthly
limit). Eight tools close that: ``project_create`` / ``project_update`` /
``project_archive`` / ``project_focus`` and ``agent_create`` /
``agent_update`` / ``agent_pause`` / ``agent_delete``.

Every behaviour test drives the REAL stream lane (``/chat/stream``): the
sentence must ARM the tool (the registry refuses an unarmed name), the card
must appear (ask tier), "Allow once" runs it through the real
``registry.invoke``, and the card's Undo (``GET /config/changes/{id}`` →
``POST /undo/{id}``) puts the record back — compared against the FULL row read
straight from its table, never through the snapshot under test. The arming
table is pinned both ways through the very functions both lanes call.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_projects as RP
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body
from tests.test_settings_records_v1321 import _app, _approve_all, _card

P = frozenset(RP.PROJECT_TOOLS)
A = frozenset(RP.AGENT_TOOLS)
MINE = P | A


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _project_rows(p) -> list[dict]:
    from iron_jarvis.core.models import Project

    with session_scope(p.engine) as db:
        return sorted((r.model_dump() for r in db.exec(select(Project))), key=lambda r: r["id"])


def _project(p, name: str) -> dict | None:
    rows = [r for r in _project_rows(p) if r["name"] == name]
    return rows[0] if rows else None


def _agent_row(p, name: str) -> dict | None:
    from iron_jarvis.agents.dynamic_models import DynamicAgentRecord

    with session_scope(p.engine) as db:
        row = db.exec(select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)).first()
        return row.model_dump() if row is not None else None


async def _undo(app, card) -> dict:
    client = TestClient(app)
    look = client.get(f"/config/changes/{card['change_id']}")
    assert look.status_code == 200, look.text
    r = client.post(f"/undo/{look.json()['action_id']}")
    assert r.status_code == 200, r.text
    return r.json()


def _finished(frames, tool):
    return [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("tool", d.get("name")) == tool
    ]


def _folder(tmp_path, *parts):
    f = tmp_path.joinpath("clients", *parts)
    f.mkdir(parents=True, exist_ok=True)
    return f


async def _deny(app, seen):
    async def deny(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    return deny


# --------------------------------------------------------------------------- #
# the arming table, both ways
# --------------------------------------------------------------------------- #

PROJECT_REQUESTS = [
    r"Make a project for the Johnson audit in D:\Clients\Johnson",
    r"Make a project for the Johnson audit in D:\Clients\Johnson and remember that I want workpapers in Excel.",
    "Create a new project called Smith 2026",
    "create a project named Acme",
    "please make a new project for the Acme engagement",
    "Can you create a project for the Smith tax return?",
    "I want a new project for the Lee estate",
    "I'd like a new project for the Garcia books",
    "start a new project for the Patel review",
    "set up a project for the Brown audit",
    "add a project for the Smith tax return",
    "New project: Johnson audit",
    "new project called Lopez",
    "rename the Johnson project to Johnson 2026",
    "Rename the Intercom project",
    "change the name of the Smith project to Smith LLC",
    r"change the Smith project's folder to D:\Clients\Smith",
    "set the default model for the Johnson project to Sonnet",
    "update the description of the Smith project",
    r"move the Johnson project to D:\Clients\J2",
    "archive the Johnson project",
    "unarchive the Smith project",
    "reopen the Smith project",
    "bring back the Lee project",
    "switch to the Smith project",
    "go back to the Johnson project",
    "focus on the Garcia project",
    "make Smith the active project",
    "clear the focused project",
    "stop focusing on the Smith project",
    "jarvis, archive the Patel project",
]
AGENT_REQUESTS = [
    "Create an agent called reviewer on GPT that only reads",
    "Create an agent named reviewer that only reads files and uses GPT.",
    "create an agent called bookkeeper that uses Opus",
    "make a new agent called tax checker",
    "hire an agent to review returns",
    "I need an agent that drafts client letters",
    "new agent called intake",
    "give the builder a $20 monthly limit",
    "give the bookkeeper agent a $20 monthly limit",
    "Set a monthly spending limit for my builder agent.",
    "change the bookkeeper agent's model to Sonnet",
    "switch the intake agent to GPT",
    "make the reviewer agent use Claude",
    "put the tax agent on the model team",
    "pause the bookkeeper agent",
    "resume the bookkeeper agent",
    "give the intake agent a day off",
    "turn off the bookkeeper agent",
    "delete the bookkeeper agent",
    "remove the intake agent",
    "fire the reviewer agent",
    "get rid of the tax checker agent",
    "please delete my bookkeeper agent",
    "update the bookkeeper agent's instructions",
    "set the reviewer to Opus",
    "raise the bookkeeper agent's monthly budget to $50",
    "let the reviewer agent only read",
    "change the instructions of the intake agent",
    # v1.484.1: the job card (never_use_tools, approvals).
    "change what the insurance helper agent is allowed to do",
    "change the insurance-helper agent so it asks before every action",
    "change the permissions of the bookkeeper agent",
    "turn on the bookkeeper agent again",
    "disable the intake agent",
]
#: Everyday sentences, near misses and questions: none of the eight.
NEAR_MISSES = [
    "my project is due Friday",
    "the agent at the bank said they would call back",
    "I'm a project manager",
    "a project manager called me",
    "project the revenue for next year",
    "create a project plan for the Johnson audit",
    "make a project timeline for the Smith engagement",
    "write a project proposal",
    "create a new React project",
    "make a new python project in my repo",
    "create a project in Asana for the audit",
    "create a new project in Jira",
    "set up a project budget",
    "switch to the project view",
    "go to the project page",
    "rename the project folder on my desktop",
    "archive the project files",
    "archive the project emails",
    "focus on the project deadline",
    "the client wants to start a new project with us",
    "I started a new project at work",
    "start the project kickoff meeting",
    "move the Q3 project to next week",
    "my insurance agent sent the policy",
    "add a registered agent for the LLC",
    "find an enrolled agent near me",
    "call the travel agent",
    "the IRS agent wants the documents",
    "create a user agent string",
    "build an agent in LangChain",
    "create an agent in Copilot Studio",
    "make sure the agent reads the whole file",
    "make sure the agent uses the right model for this",
    "ask the reviewer agent to check my return",
    "remove the agent from the Smith project team",
    "add the bookkeeper agent to the Smith project",
    "set a budget of $50,000 for the builder",
    "the builder gave us a quote",
    "an agent-based model of the economy",
    "my real estate agent called",
    "my insurance agent asks first before quoting",
    "our estate agent is not allowed to show the flat on Sundays",
    "tell the letting agent his approvals are late",
    "change my insurance agent to someone cheaper",
    "draft an email to the agent",
    # A PERSON who is an agent, in a request (only the human-agent guard
    # holds these).
    "hire a travel agent for the conference trip",
    "fire my insurance agent",
    "remove my real estate agent's number from the list",
    # Questions arm no writer (records.record_tools_for's question guard).
    "how do I create a new agent?",
    "what projects do I have?",
    "should I make a project for this?",
    "what is the focused project?",
    "is the bookkeeper agent paused?",
    "can I archive a project?",
    # An imperative typed with a question mark and no "can you" is a question
    # (records.is_question's honest cost): only that guard holds these.
    "archive the Johnson project?",
    "delete the intake agent?",
    "switch to the Smith project?",
]


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("arming") / "home"))
    return SimpleNamespace(platform=app.state.platform)


def _lanes(d, text):
    """``(post_armed, stream_ask)`` — the POST lane's whole arming and the
    stream lane's ask tier, through the functions the lanes call."""
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    from iron_jarvis.tools.autoselect import select_ask_tools

    ask = [t for t in select_ask_tools(text) if t not in armed]
    ask += CT.settings_ask_tools(d, text, armed, ask)
    return armed, ask


def test_the_table_is_big_enough():
    assert len(PROJECT_REQUESTS) + len(AGENT_REQUESTS) >= 30
    assert len(PROJECT_REQUESTS) >= 30 and len(AGENT_REQUESTS) >= 30
    assert len(NEAR_MISSES) >= 30


def test_a_request_about_a_project_offers_the_project_tools_in_the_stream_lane_only(d):
    bad = []
    for text in PROJECT_REQUESTS:
        armed, ask = _lanes(d, text)
        if not P <= set(ask):
            bad.append((text, "stream", sorted(set(ask) & MINE)))
        if set(armed) & MINE:
            bad.append((text, "post", sorted(set(armed) & MINE)))
    assert bad == []


def test_a_request_about_an_agent_offers_the_agent_tools_in_the_stream_lane_only(d):
    bad = []
    for text in AGENT_REQUESTS:
        armed, ask = _lanes(d, text)
        if not A <= set(ask):
            bad.append((text, "stream", sorted(set(ask) & MINE)))
        if set(armed) & MINE:
            bad.append((text, "post", sorted(set(armed) & MINE)))
    assert bad == []


def test_an_everyday_sentence_or_a_question_arms_none_of_them(d):
    bad = []
    for text in NEAR_MISSES:
        armed, ask = _lanes(d, text)
        stray = (set(armed) | set(ask)) & MINE
        if stray:
            bad.append((text, sorted(stray)))
    assert bad == []


def test_a_project_request_offers_no_agent_tool_and_the_other_way_round():
    for text in PROJECT_REQUESTS:
        assert not set(RP.projects_tools_for(text)) & A, text
    for text in AGENT_REQUESTS:
        assert not set(RP.projects_tools_for(text)) & P, text


def test_record_tools_for_makes_one_call_into_the_module(monkeypatch):
    calls = []
    monkeypatch.setattr(RP, "projects_tools_for", lambda t: calls.append(t) or ["project_create"])
    assert "project_create" in R.record_tools_for("anything at all")
    assert calls == ["anything at all"]
    # ...and its question guard still drops every writer it returns.
    assert R.record_tools_for("what should I do?") == []


def test_the_matchers_are_linear_on_pathological_text():
    """A child process with a deadline (a hang detector, never a speed bar):
    every input below is 40k+ characters built to make a backtracking rule
    quadratic. The arming read runs on the event loop's thread pool per turn,
    so a slow one would stall the turn."""
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.settings.records_projects import projects_tools_for
        N = 40_000
        cases = [
            "make " + "\\n" * N + "a project",
            "make a " + "x " * N + "project",
            "change the " + "a" * N + " agent's model",
            "give " + " " * N + "the builder a monthly limit",
            ("and " + " " * 50) * 2000 + "agent",
            "project " * N,
            "agent " * N,
            "rename the " + "project " * N,
            "set " + "the agent " * N,
            "remove " + "x" * N + " agent from",
            "i'd like " * 5000 + "a project",
            "please " * N + "make a project",
        ]
        t0 = time.perf_counter()
        for c in cases:
            projects_tools_for(c)
        print(round(time.perf_counter() - t0, 2))
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


# --------------------------------------------------------------------------- #
# projects, through the real stream lane
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_project_create_asks_makes_it_focuses_the_first_and_undo_takes_it_away(tmp_path):
    folder = _folder(tmp_path, "Johnson")
    app = _app(tmp_path / "home", [("project_create", {"name": "Johnson audit", "folder": str(folder), "description": "FY2026 audit"})])
    p = app.state.platform
    assert _project_rows(p) == [] and not p.config.active_project_id
    seen: list[str] = []
    frames = await _drive_stream(app, _body(rf"Make a project for the Johnson audit in {folder}"), await _approve_all(app, seen))
    assert seen == ["project_create"], "making a project asks first"
    row = _project(p, "Johnson audit")
    assert row is not None and row["root"] == str(folder) and row["brief"] == "FY2026 audit"
    assert p.config.active_project_id == row["id"], "the first project becomes the focused one (the route's rule)"
    card = _card(frames, "Project created")
    assert str(folder) in card["new"] and "focused" in card["new"]
    out = await _undo(app, card)
    assert "removed the project" in out["output"]
    assert _project_rows(p) == []
    assert not p.config.active_project_id, "the focus the create gave is taken back"


@pytest.mark.asyncio
async def test_undo_of_a_made_project_with_work_in_it_archives_it_instead(tmp_path):
    from iron_jarvis.core.models import ProjectKnowledge

    app = _app(tmp_path / "home", [("project_create", {"name": "Smith"})])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("create a project named Smith"), await _approve_all(app, seen))
    row = _project(p, "Smith")
    with session_scope(p.engine) as db:
        db.add(ProjectKnowledge(project_id=row["id"], name="note", text="client prefers PDF"))
        db.commit()
    out = await _undo(app, _card(frames, "Project created"))
    assert "archived the project" in out["output"] and "kept" in out["output"]
    after = _project(p, "Smith")
    assert after is not None and after["status"] == "archived"
    assert p.config.active_project_id != row["id"]


@pytest.mark.asyncio
async def test_a_folder_the_route_refuses_makes_nothing_and_says_why_plainly(tmp_path):
    missing = tmp_path / "nope" / "Johnson"
    app = _app(tmp_path / "home", [("project_create", {"name": "Johnson", "folder": str(missing)}), ("project_create", {"name": "Lee", "folder": "relative\\path"})])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("Make a project for the Johnson audit"), await _approve_all(app, seen))
    assert seen == ["project_create", "project_create"]
    assert _project_rows(p) == []
    errors = [f.get("output") or f.get("error") or "" for f in _finished(frames, "project_create")]
    assert any("does not exist on this machine" in e for e in errors), errors
    assert any("full location" in e for e in errors), errors
    for e in errors:
        assert e[:1].isupper() and e.rstrip().endswith("."), e


@pytest.mark.asyncio
async def test_project_update_renames_points_and_undo_restores_every_column(tmp_path):
    old, new = _folder(tmp_path, "Old"), _folder(tmp_path, "New")
    app = _app(
        tmp_path / "home",
        [("project_update", {"project": "johnson AUDIT", "new_name": "Johnson 2026", "folder": str(new), "description": "re-scoped"})],
    )
    p = app.state.platform
    c = TestClient(app)
    pid = c.post("/projects", json={"name": "Johnson audit", "root": str(old), "brief": "first"}).json()["id"]
    before = _project_rows(p)
    seen: list[str] = []
    frames = await _drive_stream(app, _body("rename the Johnson audit project to Johnson 2026"), await _approve_all(app, seen))
    assert seen == ["project_update"]
    now = _project(p, "Johnson 2026")
    assert now and now["id"] == pid and now["root"] == str(new) and now["brief"] == "re-scoped"
    card = _card(frames, "Project changed")
    assert "Johnson 2026" in card["new"] and str(old) in card["old"]
    await _undo(app, card)
    assert _project_rows(p) == before


@pytest.mark.asyncio
async def test_project_update_refuses_an_unknown_model_service_in_words(tmp_path):
    app = _app(tmp_path / "home", [("project_update", {"project": "Smith", "provider": "not-a-service", "model": "x"})])
    p = app.state.platform
    TestClient(app).post("/projects", json={"name": "Smith"})
    before = _project_rows(p)
    frames = await _drive_stream(app, _body("set the default model for the Smith project to x"), await _approve_all(app, []))
    err = _finished(frames, "project_update")[0]
    words = err.get("output") or err.get("error") or ""
    assert "no model service called" in words and "provider" not in words.lower(), words
    assert _project_rows(p) == before


@pytest.mark.asyncio
async def test_archiving_the_focused_project_takes_the_focus_and_undo_gives_both_back(tmp_path):
    app = _app(tmp_path / "home", [("project_archive", {"project": "Patel"})])
    p = app.state.platform
    pid = TestClient(app).post("/projects", json={"name": "Patel"}).json()["id"]
    assert p.config.active_project_id == pid
    before = _project_rows(p)
    seen: list[str] = []
    frames = await _drive_stream(app, _body("archive the Patel project"), await _approve_all(app, seen))
    assert seen == ["project_archive"]
    assert _project(p, "Patel")["status"] == "archived"
    assert not p.config.active_project_id, "the route's rule: archiving the focused project takes the focus"
    card = _card(frames, "Project archived")
    assert card["old"] == "in use, focused" and card["new"] == "archived"
    await _undo(app, card)
    assert _project_rows(p) == before
    assert p.config.active_project_id == pid, "Undo focuses it again"


@pytest.mark.asyncio
async def test_bringing_back_an_archived_project(tmp_path):
    app = _app(tmp_path / "home", [("project_archive", {"project": "Lee", "archive": False})])
    p = app.state.platform
    c = TestClient(app)
    pid = c.post("/projects", json={"name": "Lee"}).json()["id"]
    c.patch(f"/projects/{pid}", json={"status": "archived"})
    before = _project_rows(p)
    frames = await _drive_stream(app, _body("bring back the Lee project"), await _approve_all(app, []))
    assert _project(p, "Lee")["status"] == "active"
    await _undo(app, _card(frames, "Project brought back"))
    assert _project_rows(p) == before


@pytest.mark.asyncio
async def test_project_focus_goes_through_the_settings_writers_check_and_has_an_undo(tmp_path):
    app = _app(
        tmp_path / "home",
        [("project_focus", {"project": "Gone"}), ("project_focus", {"project": "Smith"})],
    )
    p = app.state.platform
    c = TestClient(app)
    first = c.post("/projects", json={"name": "Johnson"}).json()["id"]
    smith = c.post("/projects", json={"name": "Smith"}).json()["id"]
    gone = c.post("/projects", json={"name": "Gone"}).json()["id"]
    c.patch(f"/projects/{gone}", json={"status": "archived"})
    assert p.config.active_project_id == first
    seen: list[str] = []
    frames = await _drive_stream(app, _body("switch to the Smith project"), await _approve_all(app, seen))
    assert seen == ["project_focus", "project_focus"]
    refused = _finished(frames, "project_focus")[0]
    words = refused.get("output") or refused.get("error") or ""
    assert "archived" in words and "unarchive it first" in words, words
    assert p.config.active_project_id == smith
    card = _card(frames, "Focused project changed")
    assert card["old"] == "“Johnson”" and card["new"] == "“Smith”"
    await _undo(app, card)
    assert p.config.active_project_id == first


def test_the_writers_check_is_the_one_focus_rule():
    """No second copy of the focus rule: ``project_focus`` asks the writer, and
    a writer that refuses (its ``_project_problem``) is what the tool says."""
    src = open(RP.__file__, encoding="utf-8").read()
    assert "_project_problem" not in src and 'status != "active"' not in src
    assert 'writer.apply, {"active_project_id": pid}' in src


# --------------------------------------------------------------------------- #
# agents, through the real stream lane
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_agent_create_with_model_roles_and_limit_asks_and_undo_removes_it(tmp_path):
    args = {
        "name": "reviewer two",
        "instructions": "Read the workpapers and report problems.",
        "works_like": "reviewer",
        "provider": "openai",
        "model": "gpt-6",
        "roles": ["review"],
        "monthly_limit_usd": 20,
    }
    app = _app(tmp_path / "home", [("agent_create", args)])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("Create an agent called reviewer two on GPT that only reads"), await _approve_all(app, seen))
    assert seen == ["agent_create"]
    row = _agent_row(p, "reviewer two")
    assert row is not None
    assert (row["provider"], row["model"], row["base_type"], row["allowance_usd"]) == ("openai", "gpt-6", "reviewer", 20.0)
    assert json.loads(row["roles_json"]) == ["review"]
    card = _card(frames, "Agent created")
    assert "gpt-6" in card["new"] and "review" in card["new"] and "$20" in card["new"]
    out = await _undo(app, card)
    assert "removed the agent" in out["output"]
    assert _agent_row(p, "reviewer two") is None


@pytest.mark.asyncio
async def test_agent_create_refuses_a_taken_name_and_an_unknown_model_service(tmp_path):
    app = _app(
        tmp_path / "home",
        [("agent_create", {"name": "intake"}), ("agent_create", {"name": "other", "provider": "nope"})],
    )
    p = app.state.platform
    TestClient(app).post("/agents", json={"name": "intake", "system_prompt": "x"})
    before = _agent_row(p, "intake")
    frames = await _drive_stream(app, _body("make a new agent called intake"), await _approve_all(app, []))
    errs = [f.get("output") or f.get("error") or "" for f in _finished(frames, "agent_create")]
    assert any("already an agent called" in e for e in errs), errs
    assert any("no model service called" in e for e in errs), errs
    for e in errs:
        assert "PATCH" not in e and "provider" not in e.lower(), e
    assert _agent_row(p, "intake") == before and _agent_row(p, "other") is None


@pytest.mark.asyncio
async def test_agent_update_is_partial_and_undo_restores_every_column(tmp_path):
    app = _app(
        tmp_path / "home",
        [("agent_update", {"agent": "Bookkeeper", "monthly_limit_usd": 50, "provider": "openai", "model": "gpt-6"})],
    )
    p = app.state.platform
    TestClient(app).post(
        "/agents",
        json={"name": "bookkeeper", "system_prompt": "Keep the books.", "skills": ["tax"], "allowance_tokens": 9000, "reports_to": "builder"},
    )
    before = _agent_row(p, "bookkeeper")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("raise the bookkeeper agent's monthly budget to $50"), await _approve_all(app, seen))
    assert seen == ["agent_update"]
    now = _agent_row(p, "bookkeeper")
    assert (now["allowance_usd"], now["provider"], now["model"]) == (50.0, "openai", "gpt-6")
    # PARTIAL: what the call did not name is kept.
    for key in ("system_prompt", "skills_json", "allowance_tokens", "reports_to", "base_type"):
        assert now[key] == before[key], key
    card = _card(frames, "Agent changed")
    assert "$50" in card["new"] and "gpt-6" in card["new"]
    await _undo(app, card)
    assert _agent_row(p, "bookkeeper") == before


@pytest.mark.asyncio
async def test_agent_pause_and_undo_puts_back_the_day_off_exactly(tmp_path):
    app = _app(tmp_path / "home", [("agent_pause", {"agent": "intake", "reason": "client is away"})])
    p = app.state.platform
    TestClient(app).post("/agents", json={"name": "intake", "system_prompt": "x"})
    before = _agent_row(p, "intake")
    frames = await _drive_stream(app, _body("pause the intake agent"), await _approve_all(app, []))
    now = _agent_row(p, "intake")
    assert now["paused_reason"] == "client is away" and now["paused_at"] is not None
    card = _card(frames, "Agent paused")
    assert card["old"] == "working" and "client is away" in card["new"]
    await _undo(app, card)
    assert _agent_row(p, "intake") == before
    assert p.agents_registry.get("intake").paused_reason == "", "the registry's copy follows the row"


@pytest.mark.asyncio
async def test_agent_delete_moves_the_folder_to_the_trash_and_undo_brings_all_of_it_back(tmp_path):
    app = _app(tmp_path / "home", [("agent_delete", {"agent": "bookkeeper"})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "bookkeeper", "system_prompt": "v1", "allowance_usd": 5})
    c.patch("/agents/bookkeeper", json={"system_prompt": "v2"})  # leaves a revision
    files = p.agents_registry.files
    files.write_notes("bookkeeper", "- remember the Smith rules\n")
    folder = files.folder("bookkeeper")
    revisions = sorted(x.name for x in (folder / "revisions").iterdir())
    assert revisions, "the edit left an earlier version"
    before = _agent_row(p, "bookkeeper")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("delete the bookkeeper agent"), await _approve_all(app, seen))
    assert seen == ["agent_delete"]
    assert _agent_row(p, "bookkeeper") is None and not folder.exists()
    assert len(RP._trash_folders(p, "bookkeeper")) == 1
    card = _card(frames, "Agent removed")
    out = await _undo(app, card)
    assert "with its folder" in out["output"]
    assert _agent_row(p, "bookkeeper") == before, "the same id, created time and every field"
    assert files.read_notes("bookkeeper") == "- remember the Smith rules\n"
    assert sorted(x.name for x in (folder / "revisions").iterdir()) == revisions
    assert RP._trash_folders(p, "bookkeeper") == set(), "moved back, not copied"
    assert p.agents_registry.get("bookkeeper") is not None


@pytest.mark.asyncio
async def test_agent_delete_undo_says_what_did_not_come_back_when_the_trash_was_emptied(tmp_path):
    import shutil

    app = _app(tmp_path / "home", [("agent_delete", {"agent": "intake"})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "intake", "system_prompt": "Sort the mail."})
    p.agents_registry.files.write_notes("intake", "note one\n")
    before = _agent_row(p, "intake")
    frames = await _drive_stream(app, _body("remove the intake agent"), await _approve_all(app, []))
    for t in RP._trash_folders(p, "intake"):
        shutil.rmtree(t)  # the user emptied the trash
    out = await _undo(app, _card(frames, "Agent removed"))
    assert "earlier versions of its instructions did not come back" in out["output"]
    assert _agent_row(p, "intake") == before
    files = p.agents_registry.files
    assert files.read_notes("intake") == "note one\n"
    assert (files.read_instructions("intake") or "").strip().endswith("Sort the mail.")


@pytest.mark.asyncio
async def test_a_built_in_agent_or_an_unknown_one_is_refused_in_words(tmp_path):
    app = _app(
        tmp_path / "home",
        [("agent_pause", {"agent": "the builder"}), ("agent_delete", {"agent": "ghost"})],
    )
    TestClient(app).post("/agents", json={"name": "intake", "system_prompt": "x"})
    frames = await _drive_stream(app, _body("pause the builder agent, then delete the ghost agent"), await _approve_all(app, []))
    pause = _finished(frames, "agent_pause")[0]
    gone = _finished(frames, "agent_delete")[0]
    assert "one of Iron Jarvis's own agents" in (pause.get("output") or pause.get("error") or "")
    words = gone.get("output") or gone.get("error") or ""
    assert "no agent “ghost”" in words and "“intake”" in words, words


@pytest.mark.asyncio
async def test_deny_on_the_card_changes_nothing(tmp_path):
    app = _app(tmp_path / "home", [("agent_delete", {"agent": "intake"}), ("project_archive", {"project": "Lee"})])
    p = app.state.platform
    c = TestClient(app)
    c.post("/agents", json={"name": "intake", "system_prompt": "x"})
    c.post("/projects", json={"name": "Lee"})
    agents, projects = _agent_row(p, "intake"), _project_rows(p)
    seen: list[str] = []
    await _drive_stream(app, _body("delete the intake agent and archive the Lee project"), await _deny(app, seen))
    assert seen == ["agent_delete", "project_archive"]
    assert _agent_row(p, "intake") == agents and _project_rows(p) == projects


@pytest.mark.asyncio
async def test_dont_ask_runs_without_a_card_like_every_ask_tier_tool(tmp_path):
    app = _app(tmp_path / "home", [("project_create", {"name": "Brown"})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("set up a project for the Brown audit", "yolo"), never)
    assert _project(app.state.platform, "Brown") is not None


def test_the_eight_are_ask_tier_kept_from_low_trust_and_off_every_roster(tmp_path):
    from iron_jarvis.agents.types import _DEFINITIONS
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    for name in MINE:
        assert p.registry.get(name) is not None, name
        assert R.RECORD_TOOL_TIERS[name] == "ask", name
        assert p.permissions.authorize(name, {}).allowed is False, name
        assert name in LOW_TRUST_DENY, name
        assert name in R.RECORD_CARD_TOOLS, name
        for definition in _DEFINITIONS.values():
            assert name not in definition.tools, (name, definition.type)


def test_the_route_bodies_are_the_tools_bodies():
    """Never a second copy of a rule: the routes and the tools call the same
    lifted functions."""
    import inspect

    from iron_jarvis.daemon.routes import agents as AR
    from iron_jarvis.daemon.routes import projects as PR

    proj = inspect.getsource(PR.register)
    assert "create_project_record(d, body)" in proj and "update_project_record(d, project_id, body)" in proj
    agents = inspect.getsource(AR.register)
    for call in ("create_custom_agent(d.platform, body)", "update_custom_agent(d.platform, name, body)",
                 "pause_custom_agent(d.platform, name, body.reason)", "resume_custom_agent(d.platform, name)",
                 "delete_custom_agent(d.platform, name)"):
        assert call in agents, call
    src = open(RP.__file__, encoding="utf-8").read()
    for call in ("create_project_record", "update_project_record", "delete_project_record", "create_custom_agent",
                 "update_custom_agent", "pause_custom_agent", "resume_custom_agent", "delete_custom_agent"):
        assert call in src, call
    # The folder door and the employee-field rules live in the routes only.
    assert "root_problem(" not in src and "employee_fields(" not in src and "_validate_root(" not in src
