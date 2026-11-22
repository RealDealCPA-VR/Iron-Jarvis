"""Next leaps 14-a (master plan R2.6): agent runs, helpers and terminal help
are told what came before (``docs/next-leaps/04-a-memory-that-compounds.md``,
idea 14, "Every worker starts with the full story").

A chat in a folder has been told "Earlier work on record" since v1.491.0
(``workmap.block.work_block``); an agent run, a helper and terminal help
started blind. Now:

* an AGENT RUN in a folder is told the same block, right after the client
  block, inside ``skip_memory`` and never for a task from another computer,
  before the planner prices the prompt (its count includes it), and its row
  keeps that it was (``Session.told_json``) so the run's page lists it among
  what the run was given;
* the run's OWN wall is the block's wall (``turn=``, ``run_client_wall``): a
  HELPER started inside a client's chat (or a chat with no client) is never
  told another client's work, even when its task names it, and its task (the
  model's words) is never the block's ``query``;
* a run with nothing on record sends a byte-identical prompt;
* TERMINAL HELP is told the block for the pane's live folder, under the rule
  the client block follows there;
* a NIGHT TASK's turn already carries the block (a coding-session turn goes
  through the chat lane): pinned, green from the start.

Made-up names and folders only (Hartleys, Smith).
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentType, ChatThreadRecord, Session
from iron_jarvis.workmap import block as B
from iron_jarvis.workmap import recap as R
from iron_jarvis.workmap.clients import wall
from iron_jarvis.workmap.models import RECAP_KEPT, WorkRecap

EUROS = "DECIDED: this project reports in euros"


# --------------------------------------------------------------------------- #
# the world: a real platform, a record, a model that only listens
# --------------------------------------------------------------------------- #
def _chat(engine, title, folder, *, hours_ago=1.0, docs=(), local_only=False) -> str:
    setup: dict = {"workspace_dir": str(folder)} if folder else {}
    if local_only:
        setup["local_only"] = True
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "done", "documents": [str(d) for d in docs]}]
    with session_scope(engine) as db:
        rec = ChatThreadRecord(title=title, messages_json=json.dumps(messages),
                               setup_json=json.dumps(setup) if setup else "",
                               updated_at=utcnow() - timedelta(hours=hours_ago))
        db.add(rec)
        db.commit()
        return rec.id


def _recap(engine, tid, text):
    with session_scope(engine) as db:
        rec = db.get(ChatThreadRecord, tid)
        db.add(WorkRecap(id=f"chat:{tid}", kind="chat", ref=tid, state=RECAP_KEPT, text=text,
                         source_updated=R.stamp_of(rec.updated_at)))
        db.commit()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.permissions import headless_ask_resolver

    p = build_platform(str(tmp_path / "home"), ask_resolver=headless_ask_resolver())
    s = SimpleNamespace(p=p, engine=p.engine, tmp=tmp_path, systems=[], planned=[])
    for name in ("Price lists", "Hartleys", "Smith", "Downloads", "Empty"):
        (tmp_path / name).mkdir()
    s.prices, s.hartleys, s.smith = tmp_path / "Price lists", tmp_path / "Hartleys", tmp_path / "Smith"
    s.downloads, s.empty = tmp_path / "Downloads", tmp_path / "Empty"

    real_get = p.providers.get

    def spy_get(provider, model=None):
        adapter = real_get(provider, model)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            s.systems.append(system)
            return await real_complete(system=system, messages=messages, tools=tools, **kw)

        adapter.complete = spy
        return adapter

    monkeypatch.setattr(p.providers, "get", spy_get)

    # The planner's own view of the prompt: what it prices.
    import iron_jarvis.context.agent_window as AW

    real_plan = AW.plan_agent_transcript

    def plan_spy(messages, *a, **kw):
        s.planned.append(kw.get("system_text", ""))
        return real_plan(messages, *a, **kw)

    monkeypatch.setattr(AW, "plan_agent_transcript", plan_spy)
    yield s


async def _run(world, task, folder=None, *, origin=None, options=None, parent_id=None, within=None,
               project_id=None):
    """One real agent run (the demo model answers "done" at once); its first
    system prompt and its row."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import get_agent_definition

    world.systems.clear()
    world.planned.clear()
    session = await Orchestrator(world.p).create_session(
        task, AgentType.BUILDER, workspace_root=str(folder) if folder else None,
        origin=origin, options=options, project_id=project_id,
    )
    runtime = AgentRuntime(world.p)
    if within is not None:  # started INSIDE a chat turn whose wall is ``within``
        with wall.turn_scope(within):
            await runtime.run(session, get_agent_definition(AgentType.BUILDER), parent_id=parent_id)
    else:
        await runtime.run(session, get_agent_definition(AgentType.BUILDER), parent_id=parent_id)
    assert world.systems, "the run never asked its model"
    with session_scope(world.engine) as db:
        row = db.get(Session, session.id)
        db.expunge(row)
    return world.systems[0], row


def _story(system: str) -> str:
    """The run's "Earlier work on record" block, or ""."""
    if B.HEAD not in system:
        return ""
    return system.split(B.HEAD, 1)[1].split("\n\n#", 1)[0]


# --------------------------------------------------------------------------- #
# 1. an agent run in a folder is told what was done there before
# --------------------------------------------------------------------------- #
async def test_a_run_in_a_folder_with_work_on_record_is_told_it_and_the_planner_counts_it(world):
    tid = _chat(world.engine, "Monday price list in euros", world.prices, hours_ago=48,
                docs=[world.prices / "prices.xlsx"])
    _recap(world.engine, tid, f"DONE: priced the spring list\n{EUROS}")
    _chat(world.engine, "Somewhere else entirely", world.downloads, hours_ago=1)
    system, row = await _run(world, "update the price list for Wednesday", world.prices)
    story = _story(system)
    assert story, system[-1500:]
    assert B.HERE_LEAD in story and "Monday price list in euros" in story and "prices.xlsx" in story
    # The preference said in Monday's chat, kept as a recap line, reaches
    # Wednesday's run in the same folder (the plan's "done when").
    assert EUROS in story and B.RECAP_NOTE in story
    assert "Somewhere else entirely" not in story  # another folder's work: not this folder's
    # Priced: the planner was handed the prompt WITH the block.
    assert world.planned and all(B.HEAD in t for t in world.planned)
    # ...and the row says so, for the run's page.
    from iron_jarvis.agents.runtime import told_parts

    assert told_parts(row) == ["work"]


async def test_the_block_comes_right_after_the_client_block_and_before_the_profile(world):
    from iron_jarvis.workmap.clients import store

    store.create(world.p, folder=str(world.hartleys), name="Hartleys")
    _chat(world.engine, "Hartleys March payroll", world.hartleys, hours_ago=5)
    system, _row = await _run(world, "check the payroll", world.hartleys)
    assert "Hartleys March payroll" in _story(system)
    assert system.index("# Client: Hartleys") < system.index(B.HEAD)


async def test_skip_memory_gets_no_story(world):
    _chat(world.engine, "Monday price list in euros", world.prices, hours_ago=2)
    system, row = await _run(world, "update the price list", world.prices, options={"skip_memory": True})
    assert B.HEAD not in system and "Monday price list" not in system
    from iron_jarvis.agents.runtime import told_parts

    assert told_parts(row) == []


async def test_a_task_from_another_computer_gets_no_story(world):
    _chat(world.engine, "Monday price list in euros", world.prices, hours_ago=2)
    system, _row = await _run(world, "update the price list", world.prices, origin="remote:desk")
    assert B.HEAD not in system and "Monday price list" not in system


async def test_a_run_with_nothing_on_record_sends_a_byte_identical_prompt(world, monkeypatch):
    """The hop runs, finds nothing, and the prompt is exactly the prompt of a
    build with no hop at all (ids aside: the run's own id is in its prompt)."""
    import iron_jarvis.agents.runtime as RT

    real = RT.run_work_block
    answered: list[str] = []

    async def spy(*a, **kw):
        got = await real(*a, **kw)
        answered.append(got)
        return got

    monkeypatch.setattr(RT, "run_work_block", spy)
    with_hop, row_a = await _run(world, "tidy the folder", world.empty)
    assert answered == [""], "the hop really ran, and found nothing"

    async def none(*a, **kw):
        return ""

    monkeypatch.setattr(RT, "run_work_block", none)
    without, row_b = await _run(world, "tidy the folder", world.empty)

    def plain(system: str, row) -> str:
        from iron_jarvis.core.models import AgentRun

        with session_scope(world.engine) as db:
            runs = [r.id for r in db.exec(__import__("sqlmodel").select(AgentRun).where(
                AgentRun.session_id == row.id))]
        for rid in runs:
            system = system.replace(rid, "<run>")
        return system.replace(row.id, "<session>")

    assert plain(with_hop, row_a) == plain(without, row_b)
    assert B.HEAD not in with_hop
    from iron_jarvis.agents.runtime import told_parts

    assert told_parts(row_a) == []


async def test_a_project_run_leaves_its_folder_to_the_projects_own_activity(world):
    from iron_jarvis.core.models import Project

    with session_scope(world.engine) as db:
        proj = Project(name="Prices", root=str(world.prices))
        db.add(proj)
        db.commit()
        pid = proj.id
    _chat(world.engine, "Monday price list in euros", world.prices, hours_ago=2)
    system, _row = await _run(world, "update the price list", world.prices, project_id=pid)
    assert B.HERE_LEAD not in system


async def test_local_only_work_is_not_told_to_a_run_that_leaves_this_network(world):
    _chat(world.engine, "Private payroll notes", world.prices, hours_ago=2, local_only=True)
    _chat(world.engine, "Monday price list in euros", world.prices, hours_ago=3)
    system, _row = await _run(world, "update the price list", world.prices)
    story = _story(system)
    assert "Monday price list in euros" in story and "Private payroll" not in story


# --------------------------------------------------------------------------- #
# 2. helpers: the run's own (narrowed) wall, never the model's words
# --------------------------------------------------------------------------- #
@pytest.fixture()
def clients(world):
    from iron_jarvis.workmap.clients import store

    store.create(world.p, folder=str(world.hartleys), name="Hartleys")
    store.create(world.p, folder=str(world.smith), name="Smith")
    _chat(world.engine, "Hartleys price list review", world.hartleys, hours_ago=3)
    _chat(world.engine, "Smith quarterly report", world.smith, hours_ago=4)
    return world


async def test_a_helper_in_a_clients_chat_is_never_told_a_second_clients_work(clients):
    chat_wall = wall.turn_wall(clients.engine, str(clients.hartleys), "check the price list")
    assert chat_wall.client is not None  # a chat in Hartleys' folder
    system, _row = await _run(clients, "Compare it with Smith's quarterly report", clients.hartleys,
                              within=chat_wall)
    story = _story(system)
    assert "Hartleys price list review" in story  # its own client's folder: told
    assert "Smith quarterly report" not in system and "Smith" not in story


async def test_a_helper_in_a_chat_with_no_client_is_not_told_a_client_its_task_names(clients):
    """The run's wall is NARROWED to the chat it was started in, and its task
    is the model's words: neither names Smith for the wall. The control: the
    owner starting the very same run is told Smith's work (the named path)."""
    owner, _row = await _run(clients, "Look at Smith's quarterly report", clients.downloads)
    assert "Smith quarterly report" in _story(owner), owner[-1500:]
    chat_wall = wall.turn_wall(clients.engine, str(clients.downloads), "help me with this folder")
    assert chat_wall.client is None and not chat_wall.named
    helper, _row = await _run(clients, "Look at Smith's quarterly report", clients.downloads,
                              within=chat_wall)
    assert "Smith quarterly report" not in helper


async def test_a_helper_keeps_a_client_the_owner_named_in_its_chat_and_no_other(clients):
    """The block's wall IS the run's own (narrowed) wall, not one built again
    from the helper's folder alone: the owner named Smith in the chat, so a
    helper on that very job is told Smith's work; a helper whose task does not
    name Smith is not (its named set is the chat's AND its own)."""
    chat_wall = wall.turn_wall(clients.engine, str(clients.downloads), "what did we send Smith last quarter?")
    assert chat_wall.client is None and chat_wall.named
    helper, _row = await _run(clients, "Find Smith's quarterly report", clients.downloads, within=chat_wall)
    assert "Smith quarterly report" in _story(helper), helper[-1500:]
    other, _row = await _run(clients, "Find the quarterly report", clients.downloads, within=chat_wall)
    assert "Smith quarterly report" not in other


async def test_a_delegated_child_is_never_told_its_parents_task_as_the_owners_words(clients):
    """A child (``parent_id``) started by the owner's own run: its task is the
    model's, so the "other folders" part stays empty even when it names work
    elsewhere; its own folder is still told."""
    _chat(clients.engine, "Downloads bank letter draft", clients.downloads, hours_ago=2)
    child, _row = await _run(clients, "Find the Downloads bank letter draft and reuse it",
                             clients.prices, parent_id="run_parent_x")
    # (Memory recall may still find that chat by the task's words: it is not
    # the work record, and it has its own wall.)
    assert B.ELSEWHERE_LEAD not in child and "bank letter draft" not in _story(child)
    owner, _row = await _run(clients, "Find the Downloads bank letter draft and reuse it", clients.prices)
    assert "Downloads bank letter draft" in _story(owner)  # the control


def test_the_story_spreads_down_and_leaves_out_the_work_a_run_is_part_of():
    from iron_jarvis.agents import runtime as RT

    parent = SimpleNamespace(id="session_parent", task="do the books", origin=None, options_json="{}")
    child = SimpleNamespace(id="session_child", task="model words", origin=None, options_json="{}")
    outer = RT.run_story(parent)
    assert outer.words == "do the books" and outer.skip == ("session_parent",)
    token = RT.RUN_STORY.set(outer)
    try:
        inner = RT.run_story(child)
    finally:
        RT.RUN_STORY.reset(token)
    assert inner.words == "" and inner.skip == ("session_child", "session_parent")
    # A run a chat handed off leaves that chat out too; a helper's origin, a
    # task from another computer and a parent run id mean nobody's words.
    handed = SimpleNamespace(id="session_h", task="t", origin="user_chat",
                             options_json=json.dumps({"chat_thread_id": "thread_9"}))
    assert RT.run_story(handed).skip == ("session_h", "thread_9")
    from iron_jarvis.agents.team import MISSION_MEMBER_ORIGIN
    from iron_jarvis.harness.helpers import HELPER_ORIGIN

    for origin in (HELPER_ORIGIN, MISSION_MEMBER_ORIGIN, "remote:desk"):
        assert RT.run_story(SimpleNamespace(id="s", task="t", origin=origin, options_json="{}")).words == ""
    assert RT.run_story(SimpleNamespace(id="s", task="t", origin=None, options_json="{}"), "run_1").words == ""
    with wall.turn_scope(wall.NO_CLIENT):  # started inside a chat turn
        assert RT.run_story(SimpleNamespace(id="s", task="t", origin=None, options_json="{}")).words == ""


async def test_a_child_is_never_told_the_run_it_is_part_of(clients):
    """The parent run's own row in the record (a run in a folder is work) is
    not earlier work to the child it delegated to."""
    from iron_jarvis.agents import runtime as RT

    from iron_jarvis.workmap.index import work_map

    _parent_system, parent = await _run(clients, "Sort the Hartleys invoices", clients.hartleys)
    work_map(clients.engine, clients.p.config.home).refresh(force=True)
    outer = RT.RunStory(words="", skip=(parent.id,))
    token = RT.RUN_STORY.set(outer)
    try:
        session = SimpleNamespace(id="session_child", task="x", project_id=None,
                                  workspace_path=str(clients.hartleys))
        told_with = await RT.run_work_block(clients.p, session, wall.turn_wall(
            clients.engine, str(clients.hartleys), ""))
    finally:
        RT.RUN_STORY.reset(token)
    alone = await RT.run_work_block(clients.p, session, wall.turn_wall(
        clients.engine, str(clients.hartleys), ""))
    assert "Sort the Hartleys invoices" in alone  # the control: it IS on record
    assert "Sort the Hartleys invoices" not in told_with
    assert "Hartleys price list review" in told_with


# --------------------------------------------------------------------------- #
# 2b. a run whose task nobody typed names no client (the independent review)
# --------------------------------------------------------------------------- #
def _nobodys_origins() -> list[str]:
    from iron_jarvis.harness.helpers import HELPER_ORIGIN

    # A helper started in the background (the CI reviewer: no chat turn, no
    # run around it), a reflex (its task is rendered from an outside
    # signal), autonomy (the model's planned task), a workflow's step.
    return [HELPER_ORIGIN, "reflex:inbox", "autonomy", "workflow:month end"]


@pytest.mark.parametrize("origin", _nobodys_origins())
async def test_a_run_nobody_typed_is_never_pointed_at_a_client_its_task_names(clients, origin):
    """Started OUTSIDE any chat turn or run, in a folder with no client, with
    a task naming Smith: told its own folder's work, never Smith's. The
    control: the owner starting the very same run is told Smith's work."""
    _chat(clients.engine, "Downloads bank letter draft", clients.downloads, hours_ago=2)
    task = "Look at Smith's quarterly report"
    owner, _row = await _run(clients, task, clients.downloads)
    assert "Smith quarterly report" in _story(owner), owner[-1500:]
    system, _row = await _run(clients, task, clients.downloads, origin=origin)
    story = _story(system)
    assert "Downloads bank letter draft" in story, system[-1500:]  # it IS told a story
    assert "Smith quarterly report" not in story and str(clients.smith) not in story


def test_only_a_task_the_owner_typed_counts_as_the_owners_words():
    from iron_jarvis.agents import runtime as RT
    from iron_jarvis.agents.team import MISSION_MEMBER_ORIGIN

    for origin in (None, "", "chat", "project", "project:p1", "job:mission", "job:agents",
                   "schedule:daily", "comm:slack"):
        assert RT.owner_typed_task(origin), origin
    for origin in (*_nobodys_origins(), "goal:g1", "assignment:a1", "self_dev", "memory-review",
                   MISSION_MEMBER_ORIGIN, "remote:desk", "a new kind of start"):
        assert not RT.owner_typed_task(origin), origin
        assert RT.run_story(SimpleNamespace(id="s", task="t", origin=origin, options_json="{}")).words == ""


def test_the_storys_wall_names_a_client_only_when_the_owners_words_did():
    from iron_jarvis.agents import runtime as RT
    from iron_jarvis.workmap.clients import TurnWall

    run_wall = TurnWall(named=frozenset({"c_smith"}), folder_key="downloads")
    # The owner's words: the run's own wall.
    assert RT.story_wall(RT.RunStory(words="Look at Smith"), run_wall).named == {"c_smith"}
    # Nobody's words, started by nothing: no client named; the rest is kept.
    got = RT.story_wall(RT.RunStory(), run_wall)
    assert got.named == frozenset() and got.folder_key == "downloads" and got.ok
    # Inside a chat turn: the run's wall is already narrowed to the turn's.
    with wall.turn_scope(TurnWall(named=frozenset({"c_smith"}))):
        assert RT.story_wall(RT.RunStory(), run_wall).named == {"c_smith"}
    # Inside another run: that run's STORY wall, never its task's wall.
    for parent_named, want in ((frozenset(), frozenset()), (frozenset({"c_smith"}), {"c_smith"})):
        token = RT.RUN_STORY.set(RT.RunStory(wall=TurnWall(named=parent_named)))
        try:
            assert RT.story_wall(RT.RunStory(), run_wall).named == want
        finally:
            RT.RUN_STORY.reset(token)


async def test_a_run_the_phone_started_is_told_titles_and_how_each_ended_only(world):
    """The Handbook's sentence: no file names, no recap lines."""
    tid = _chat(world.engine, "Monday price list in euros", world.prices, hours_ago=48,
                docs=[world.prices / "prices.xlsx"])
    _recap(world.engine, tid, f"DONE: priced the spring list\n{EUROS}")
    system, _row = await _run(world, "update the price list for Wednesday", world.prices,
                              origin="comm:slack")
    story = _story(system)
    assert "Monday price list in euros" in story, system[-1500:]
    assert "prices.xlsx" not in story and EUROS not in story and B.RECAP_NOTE not in story


# --------------------------------------------------------------------------- #
# 3. the run's page lists it among what the run was given
# --------------------------------------------------------------------------- #
def test_the_runs_page_reads_what_the_run_was_given(tmp_path):
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    folder = tmp_path / "Price lists"
    folder.mkdir()
    _chat(app.state.platform.engine, "Monday price list in euros", folder, hours_ago=2)
    r = client.post("/sessions", json={"task": "update the price list", "workspace_root": str(folder),
                                        "wait": True})
    assert r.status_code == 200, r.text
    sid = r.json().get("id") or r.json()["session"]["id"]
    page = client.get(f"/sessions/{sid}").json()["session"]
    assert page["told"] == ["work"]
    # A run in a folder with nothing on record: given nothing.
    empty = tmp_path / "Empty"
    empty.mkdir()
    r = client.post("/sessions", json={"task": "tidy", "workspace_root": str(empty), "wait": True})
    sid = r.json().get("id") or r.json()["session"]["id"]
    assert client.get(f"/sessions/{sid}").json()["session"]["told"] == []


def test_an_older_row_and_a_bad_value_read_as_given_nothing():
    from iron_jarvis.agents.runtime import told_parts

    assert told_parts(SimpleNamespace(told_json=None)) == []
    assert told_parts(SimpleNamespace()) == []
    assert told_parts(SimpleNamespace(told_json="not json")) == []
    assert told_parts(SimpleNamespace(told_json='{"work": 1}')) == []
    assert told_parts(SimpleNamespace(told_json='["secret", "work", "work"]')) == ["work"]


# --------------------------------------------------------------------------- #
# 4. terminal help: the pane's live folder, under the client block's rule
# --------------------------------------------------------------------------- #
def _fake_backend():
    from iron_jarvis.terminals.backend import FakeBackend

    return FakeBackend()


@pytest.fixture()
def panes(tmp_path, monkeypatch):
    import iron_jarvis.daemon.chat_turn as CT
    from iron_jarvis.daemon.app import create_app

    monkeypatch.setattr(CT, "GROUNDING_SOFT_S", 60.0)
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _fake_backend)
    app = create_app(str(tmp_path / "home"))
    s = SimpleNamespace(app=app, client=TestClient(app), platform=app.state.platform, tmp=tmp_path,
                        systems=[])
    s.engine = s.platform.engine
    for name in ("Price lists", "Hartleys", "Smith", "Empty"):
        (tmp_path / name).mkdir()
    s.prices, s.hartleys, s.smith = tmp_path / "Price lists", tmp_path / "Hartleys", tmp_path / "Smith"
    real_get = s.platform.providers.get

    def spy_get(provider, model=None):
        adapter = real_get(provider, model)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            s.systems.append(system)
            return await real_complete(system=system, messages=messages, tools=tools, **kw)

        adapter.complete = spy
        return adapter

    s.platform.providers.get = spy_get
    return s


def _assist(panes, term_id: str, prompt: str = "what happened here?") -> str:
    panes.systems.clear()
    r = panes.client.post(f"/terminals/{term_id}/ai", json={"prompt": prompt})
    assert r.status_code == 200, r.text
    return panes.systems[-1]


def _at(folder: object, typed: str = "") -> str:
    return f"PS {folder}> {typed}" if os.name == "nt" else f"pat@box:{folder}$ {typed}"


def test_terminal_help_is_told_the_panes_folder_work(panes):
    _chat(panes.engine, "Monday price list in euros", panes.prices, hours_ago=2)
    term = panes.client.post("/terminals", json={"cwd": str(panes.prices)}).json()
    system = _assist(panes, term["id"])
    assert B.HEAD in system and "Monday price list in euros" in system
    # A pane in a folder with nothing on record: nothing (byte-identical).
    other = panes.client.post("/terminals", json={"cwd": str(panes.tmp / "Empty")}).json()
    assert B.HEAD not in _assist(panes, other["id"])


def test_terminal_help_follows_the_client_blocks_rule_for_where_the_pane_is_now(panes):
    from iron_jarvis.workmap.clients import store

    store.create(panes.platform, folder=str(panes.hartleys), name="Hartleys")
    store.create(panes.platform, folder=str(panes.smith), name="Smith")
    _chat(panes.engine, "Hartleys price list review", panes.hartleys, hours_ago=3)
    _chat(panes.engine, "Smith quarterly report", panes.smith, hours_ago=4)
    term = panes.client.post("/terminals", json={"cwd": str(panes.hartleys)}).json()
    session = panes.platform.terminals.get(term["id"])
    # Just opened, where it started: Hartleys' own work, never Smith's (the
    # request names Smith; the wall is the pane's folder's).
    system = _assist(panes, term["id"], "compare with Smith's quarterly report")
    assert "Hartleys price list review" in system and "Smith quarterly report" not in system
    # Moved into Smith's folder (its prompt says so): another client than
    # where it started, so nothing is told.
    session._tail += (_at(panes.smith, "dir") + "\r\n" + _at(panes.smith)).encode("utf-8")
    system = _assist(panes, term["id"])
    assert B.HEAD not in system and "Smith quarterly report" not in system
    # A cd nobody can follow: the folder is not known, nothing is told.
    session._tail.clear()
    session._tail += "cd ..\r\n".encode("utf-8")
    assert B.HEAD not in _assist(panes, term["id"])


# --------------------------------------------------------------------------- #
# 5. a night task's turn already carries the block (a pin, green from the start)
# --------------------------------------------------------------------------- #
async def test_a_night_tasks_turn_is_told_the_folders_earlier_work(tmp_path, monkeypatch):
    import iron_jarvis.daemon.chat_turn as CT
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.routes import build as BR
    from iron_jarvis.harness import night as N
    from tests.test_build_sessions_v1367 import Script, new_session, until, wire

    monkeypatch.setattr(CT, "GROUNDING_SOFT_S", 60.0)
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    _chat(app.state.platform.engine, "Monday README rewrite", folder, hours_ago=20)
    script = wire(app, Script(["Done."]))
    sid = new_session(app, folder)["id"]
    lg = await BR.start_build_turn(app.state.d, sid, "tidy the README tonight", "nt-14a",
                                   started_by=N.BY_NIGHT)
    await until(lambda: lg.status != "running", 60)
    assert script.calls, "the night turn never asked its model"
    system = script.calls[0]["system"]
    assert B.HEAD in system and "Monday README rewrite" in system
