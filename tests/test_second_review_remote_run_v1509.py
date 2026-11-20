"""Second review, other computers (v1.509.0): a task from another computer is not the owner's.

F1 (decision E1). A run a task from ANOTHER COMPUTER started (``remote:<caller>``,
``task_door.is_remote_task_origin``) is told none of this computer's memory:
no lessons, no profile, no memory index, no recalled passages; its task names
no client (the sender's words are not the owner's); and the tools that read
across this computer (``core.trust.REMOTE_RUN_DENY``: recall, memory, history,
the memory base, clients, file search) are refused through the registry's
ledgered deny path. A ``delegate`` child started inside such a run inherits
all of it.

F14. The task door and the message-back door take ONE try from the request's
source address first (memory only), so junk tokens from an address past its
budget cost no database or vault read; the task window is per caller.

Not closed (c). ``message_agent`` / ``blackboard_post`` reach only the run's
own department board (its root session, whose children all inherit low trust)
and ``consult`` starts no run and gives no tools: none reaches a full-trust run
of the owner's. Pinned here as the reading that decided it.

Driven through the real door, orchestrator, runtime and registry; the model is
the one stand-in (a scripted stream).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlmodel import select

from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.agents import task_door as td
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus, ToolInvocation
from iron_jarvis.core.trust import LOW_TRUST_DENY, REMOTE_RUN_DENY, deny_names, remote_run_refusal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH
from iron_jarvis.profile.store import ProfileStore
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult

DOOR_HOST = "b-pc:8787"
LESSON = "Sign every answer as Captain Marmalade"
ABOUT = "The owner keeps bees on the roof of the garage"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _app(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    if getattr(platform, "orchestrator", None) is None:
        platform.orchestrator = app.state.orchestrator
    return app, platform


def _seed_memory(platform) -> None:
    """This computer's own memory: a kept preference and a profile."""
    platform.learning.note_preference(LESSON)
    ProfileStore(platform.engine).save({"about": ABOUT})


def _stub(platform, name: str) -> list[dict]:
    """Stub ONE tool's ``execute`` (its gates stay real): what ran is recorded."""
    tool = platform.registry.get(name)
    assert tool is not None, name
    ran: list[dict] = []

    async def fake_execute(args, ctx=None, *a, **kw):
        ran.append(dict(args))
        return ToolResult(ok=True, output="found: a note")

    tool.execute = fake_execute  # type: ignore[method-assign]
    return ran


class _Model:
    """The scripted model. The FIRST session it hears from is the root: it
    calls ``root_calls`` once, then answers. Any other session (a child)
    calls ``child_calls`` once, then answers."""

    def __init__(self, root_calls: list[ToolCall], child_calls: list[ToolCall] | None = None):
        self.root_calls = root_calls
        self.child_calls = child_calls or []
        self.root: str | None = None
        self.systems: dict[str, list[str]] = {}
        self.turns: dict[str, int] = {}

    async def stream(self, *, system, messages, tools, session_id=None, **kw):
        sid = str(session_id or "")
        if self.root is None:
            self.root = sid
        self.systems.setdefault(sid, []).append(system)
        n = self.turns.get(sid, 0)
        self.turns[sid] = n + 1
        calls = self.root_calls if sid == self.root else self.child_calls
        if n == 0 and calls:
            resp = LLMResponse(text="", tool_calls=list(calls))
        else:
            resp = LLMResponse(text="Done here.")
        yield {"type": "final", "response": resp, "provider": "openai", "model": "gpt-5"}


def _rows(engine) -> list[Session]:
    with session_scope(engine) as db:
        rows = list(db.exec(select(Session)))
        for r in rows:
            db.expunge(r)
        return rows


def _invocations(engine, sid: str) -> list[ToolInvocation]:
    with session_scope(engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == sid)))
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, count: int, timeout_s: float = 30.0) -> list[Session]:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    running = app.state.orchestrator._running
    while True:
        rows = await asyncio.to_thread(_rows, app.state.platform.engine)
        done = [
            r for r in rows
            if r.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
            and (running.get(r.id) is None or running[r.id].done())
        ]
        if len(done) >= count or loop.time() > end:
            break
        await asyncio.sleep(0.05)
    await td.drain_replies()
    return await asyncio.to_thread(_rows, app.state.platform.engine)


async def _send_task(app, task: str, *, source: str = "10.0.0.7") -> httpx.Response:
    _state, token = td.door_for(app.state.platform).enable(caller="laptop")
    transport = httpx.ASGITransport(app=app, client=(source, 50000))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{DOOR_HOST}") as c:
        return await c.post(
            TASK_DOOR_PATH, json={"task": task}, headers={"Authorization": f"Bearer {token}"}
        )


# --------------------------------------------------------------------------- #
# F1 — none of this computer's memory, and the cross-computer readers refused
# --------------------------------------------------------------------------- #


async def test_a_task_from_another_computer_is_told_none_of_this_computers_memory(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app, platform = _app(tmp_path)
    _seed_memory(platform)
    ran = _stub(platform, "recall")

    # CONTROL: the owner's own run on this computer IS told the memory, and
    # its recall runs — so the seed reaches prompts and the tool works.
    own = _Model([ToolCall(id="c1", name="recall", arguments={"query": "bees"})])
    platform.router.stream = own.stream
    s = await app.state.orchestrator.create_session("look up my notes", AgentType.SUPERVISOR)
    await app.state.orchestrator.run_session(s.id)
    system = own.systems[s.id][0]
    assert LESSON in system and ABOUT in system
    assert ran == [{"query": "bees"}]
    ran.clear()

    # THE TASK FROM ANOTHER COMPUTER, through the real door.
    remote = _Model([ToolCall(id="c1", name="recall", arguments={"query": "bees"})])
    platform.router.stream = remote.stream
    r = await _send_task(app, "look up their notes")
    assert r.status_code == 202, r.text
    rows = await _settled(app, count=2)
    (row,) = [x for x in rows if td.is_remote_task_origin(x.origin)]
    assert json.loads(row.options_json)["skip_memory"] is True
    system = remote.systems[row.id][0]
    assert LESSON not in system, "a lesson of this computer reached another computer's task"
    assert ABOUT not in system, "the owner's profile reached another computer's task"
    # the recall was REFUSED through the registry (ledgered), never run
    assert ran == []
    refused = [i for i in _invocations(platform.engine, row.id) if i.tool == "recall"]
    assert len(refused) == 1 and refused[0].ok is False
    assert refused[0].output == remote_run_refusal("recall")


async def test_a_helper_started_inside_a_remote_task_inherits_all_of_it(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app, platform = _app(tmp_path)
    _seed_memory(platform)
    ran = _stub(platform, "memory_search")
    model = _Model(
        [ToolCall(id="d1", name="delegate", arguments={"agent_type": "researcher", "task": "dig"})],
        [ToolCall(id="c1", name="memory_search", arguments={"query": "bees"})],
    )
    platform.router.stream = model.stream
    r = await _send_task(app, "find out about the roof")
    assert r.status_code == 202, r.text
    rows = await _settled(app, count=2)
    (parent,) = [x for x in rows if td.is_remote_task_origin(x.origin)]
    children = [x for x in rows if x.id != parent.id]
    assert len(children) == 1, [x.origin for x in rows]
    child = children[0]
    assert child.trust == "low"
    system = model.systems[child.id][0]
    assert LESSON not in system and ABOUT not in system
    assert ran == [], "a helper of another computer's task searched this computer's memory"
    refused = [i for i in _invocations(platform.engine, child.id) if i.tool == "memory_search"]
    assert len(refused) == 1 and refused[0].output == remote_run_refusal("memory_search")


async def test_another_computers_task_names_no_client(tmp_path):
    """The client wall: the sender's words never count as the owner's."""
    from iron_jarvis.workmap.clients import store as client_store

    app, platform = _app(tmp_path)
    folder = tmp_path / "Clients" / "Hartleys"
    folder.mkdir(parents=True)
    cid = client_store.create(platform, folder=str(folder), name="Hartleys")["client"]["id"]
    task = "Tell me everything about Hartleys"
    mine = Session(task=task, agent_type=AgentType.SUPERVISOR, origin="")
    theirs = Session(task=task, agent_type=AgentType.SUPERVISOR, origin=td.remote_origin("laptop"))
    assert cid in (await runtime_mod.run_client_wall(platform, mine)).named
    wall = await runtime_mod.run_client_wall(platform, theirs)
    assert wall.ok and wall.named == frozenset()


def test_the_cross_computer_readers_are_one_set_of_real_tools(tmp_path):
    _app_, platform = _app(tmp_path)
    for name in REMOTE_RUN_DENY:
        assert platform.registry.get(name) is not None, name
    # kept apart from the low-trust list: a phone message's run keeps them
    assert not (REMOTE_RUN_DENY & LOW_TRUST_DENY)
    assert not (REMOTE_RUN_DENY & deny_names(False))
    assert REMOTE_RUN_DENY <= deny_names(False, True)
    # v1.547.0 (next leaps 13-a): this computer's record of earlier work.
    assert "work_history" in REMOTE_RUN_DENY


def test_remote_runs_spread_down_through_the_context():
    child = Session(task="x", agent_type=AgentType.BUILDER, origin="")
    assert not runtime_mod.run_is_remote(child)
    token = runtime_mod.REMOTE_RUN.set(True)
    try:
        assert runtime_mod.run_is_remote(child)
        assert runtime_mod.run_skips_memory(child)
    finally:
        runtime_mod.REMOTE_RUN.reset(token)


# --------------------------------------------------------------------------- #
# Not closed (c): the department board and consult
# --------------------------------------------------------------------------- #


def test_the_board_of_a_remote_task_is_its_own_department(tmp_path):
    """``message_agent`` / ``blackboard_post`` write to the board of the
    run's ROOT session (``blackboard.store.resolve_board_id``). A remote task
    is a root with no parent, so its board is its own; every run under it
    inherits low trust. No full-trust run of the owner's reads that board."""
    from iron_jarvis.blackboard.store import resolve_board_id
    from iron_jarvis.core.models import AgentRun

    app, platform = _app(tmp_path)
    with session_scope(platform.engine) as db:
        owner = Session(task="mine", agent_type=AgentType.SUPERVISOR)
        theirs = Session(task="theirs", agent_type=AgentType.SUPERVISOR,
                         origin=td.remote_origin("laptop"), trust="low")
        db.add(owner)
        db.add(theirs)
        db.commit()
        root_run = AgentRun(session_id=theirs.id, agent_type=AgentType.SUPERVISOR)
        db.add(root_run)
        db.commit()
        child_session = Session(task="dig", agent_type=AgentType.RESEARCHER, trust="low")
        db.add(child_session)
        db.commit()
        child_run = AgentRun(session_id=child_session.id, agent_type=AgentType.RESEARCHER,
                             parent_id=root_run.id)
        db.add(child_run)
        db.commit()
        ids = (owner.id, theirs.id, root_run.id, child_session.id, child_run.id)
    owner_id, theirs_id, root_run_id, child_sid, child_run_id = ids
    assert resolve_board_id(platform.engine, theirs_id, root_run_id) == theirs_id
    assert resolve_board_id(platform.engine, child_sid, child_run_id) == theirs_id
    assert theirs_id != owner_id


# --------------------------------------------------------------------------- #
# F14 — a source address takes one try first; the task window is per caller
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_junk_tokens_past_an_addresss_budget_read_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    monkeypatch.setattr(td, "ADDRESS_TRIES_MAX", 3)
    app, platform = _app(tmp_path)
    clock = _Clock()
    platform._task_door = td.TaskDoor(platform.config.home, platform.secrets, clock=clock)
    td.door_for(platform).enable(caller="laptop")
    reads: list[str] = []
    real_get = platform.secrets.get

    def counting_get(name, *a, **kw):
        reads.append(str(name))
        return real_get(name, *a, **kw)

    monkeypatch.setattr(platform.secrets, "get", counting_get)

    async def post(source: str) -> int:
        transport = httpx.ASGITransport(app=app, client=(source, 50000))
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{DOOR_HOST}") as c:
            r = await c.post(TASK_DOOR_PATH, json={"task": "x"},
                             headers={"Authorization": "Bearer junk-token-value"})
            return r.status_code

    assert [await post("10.0.0.66") for _ in range(3)] == [401, 401, 401]
    before = len(reads)
    assert before > 0, "the token was checked against the vault"
    # past the budget: refused BEFORE any vault (or database) read
    assert await post("10.0.0.66") == 429
    assert await post("::ffff:10.0.0.66") == 429, "an IPv4-mapped spelling is the same address"
    assert len(reads) == before
    # another address keeps its own budget
    assert await post("10.0.0.67") == 401
    # the window rolls on the injected clock
    clock.now += td.ADDRESS_WINDOW_S + 1
    assert await post("10.0.0.66") == 401


async def test_the_message_back_door_takes_the_address_try_first(tmp_path, monkeypatch):
    from iron_jarvis.agents.remote import RemoteAgentRegistry

    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    monkeypatch.setattr(td, "INBOUND_ADDRESS_TRIES_MAX", 2)
    app, platform = _app(tmp_path)
    platform._task_door = td.TaskDoor(platform.config.home, platform.secrets, clock=_Clock())
    looked: list[str] = []
    real = RemoteAgentRegistry.get

    def counting(self, name):
        looked.append(name)
        return real(self, name)

    monkeypatch.setattr(RemoteAgentRegistry, "get", counting)
    transport = httpx.ASGITransport(app=app, client=("10.0.0.66", 50000))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{DOOR_HOST}") as c:
        codes = [
            (await c.post("/agents/remote/desk/inbound", json={"conversation_id": "x", "message": "y"},
                          headers={"Authorization": "Bearer junk-token-value"})).status_code
            for _ in range(4)
        ]
    assert codes == [403, 403, 429, 429]
    assert looked.count("desk") == 2, "a refused address still read the database"


def test_the_task_window_is_per_caller(monkeypatch, tmp_path):
    monkeypatch.setattr(td, "TASK_RATE_MAX", 2)
    clock = _Clock()
    door = td.TaskDoor(tmp_path, secrets=None, clock=clock)
    assert [door.rate_ok("desk") for _ in range(3)] == [True, True, False]
    # another computer is not held by the first one's budget
    assert door.rate_ok("laptop") is True
    clock.now += td.TASK_RATE_WINDOW_S + 1
    assert door.rate_ok("desk") is True


# --------------------------------------------------------------------------- #
# Lead review of F1: a keep list, and reads confined to the run's own folder
# --------------------------------------------------------------------------- #


def test_the_keep_list_names_real_tools_and_none_that_is_kept_away(tmp_path):
    from iron_jarvis.core.trust import REMOTE_RUN_KEEP, remote_run_keeps

    _app_, platform = _app(tmp_path)
    for name in REMOTE_RUN_KEEP:
        assert platform.registry.get(name) is not None, f"{name} is not a registered tool"
    assert not (REMOTE_RUN_KEEP & REMOTE_RUN_DENY)
    assert not (REMOTE_RUN_KEEP & LOW_TRUST_DENY)
    # every OTHER registered tool is kept away: the browser, the terminals,
    # connected apps, settings, and whatever is added tomorrow
    for name in ("browser_read_page", "pane_read", "mcp_read_resource", "config_list",
                 "downloads_list", "excel_live_look", "skill_load", "tool_script"):
        assert platform.registry.get(name) is not None, name
        assert not remote_run_keeps(name), name
    assert not remote_run_keeps("a_tool_added_tomorrow")


@pytest.mark.parametrize("tool", ["browser_read_page", "pane_read", "config_list", "mcp_read_resource"])
async def test_a_tool_off_the_keep_list_is_refused_for_another_computers_task(
    tmp_path, monkeypatch, tool
):
    """Through the real door, runtime and registry: an allow-tier reader of
    what this computer holds, which no deny list named, never runs."""
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app, platform = _app(tmp_path)
    ran = _stub(platform, tool)
    remote = _Model([ToolCall(id="c1", name=tool, arguments={})])
    platform.router.stream = remote.stream
    r = await _send_task(app, "look at what is open on that computer")
    assert r.status_code == 202, r.text
    rows = await _settled(app, count=1)
    (row,) = [x for x in rows if td.is_remote_task_origin(x.origin)]
    assert ran == [], f"{tool} ran for another computer's task"
    refused = [i for i in _invocations(platform.engine, row.id) if i.tool == tool]
    assert len(refused) == 1 and refused[0].ok is False
    assert refused[0].output == remote_run_refusal(tool)


async def test_another_computers_task_cannot_list_or_read_outside_its_own_folder(
    tmp_path, monkeypatch
):
    """``read_document`` takes an absolute path anywhere on this computer.
    The run started here reads the file (the control); the task from another
    computer is refused it. (``list_folder`` is not on a Supervisor's roster:
    it and the other readers are driven directly in the test below.)"""
    from iron_jarvis.core.fs_policy import CONFINED_REFUSAL

    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app, platform = _app(tmp_path / "home")
    outside = tmp_path / "papers kept here"
    outside.mkdir()
    (outside / "letter.txt").write_text("a private letter", encoding="utf-8")
    calls = [
        ToolCall(id="c2", name="read_document", arguments={"path": str(outside / "letter.txt")}),
    ]

    own = _Model(list(calls))
    platform.router.stream = own.stream
    s = await app.state.orchestrator.create_session("list my papers", AgentType.SUPERVISOR)
    await app.state.orchestrator.run_session(s.id)
    mine = {i.tool: i for i in _invocations(platform.engine, s.id)}
    assert mine["read_document"].ok and "a private letter" in mine["read_document"].output

    remote = _Model(list(calls))
    platform.router.stream = remote.stream
    r = await _send_task(app, "list their papers")
    assert r.status_code == 202, r.text
    rows = await _settled(app, count=2)
    (row,) = [x for x in rows if td.is_remote_task_origin(x.origin)]
    theirs = {i.tool: i for i in _invocations(platform.engine, row.id)}
    got = theirs["read_document"]
    assert got.ok is False, "read_document read a file of this computer for another one"
    assert CONFINED_REFUSAL in got.output, got.output
    assert "private letter" not in got.output


@pytest.mark.parametrize(
    "tool,args",
    [
        ("list_folder", {"path": "{folder}"}),
        ("read_document", {"path": "{txt}"}),
        ("extract_pdf", {"path": "{pdf}"}),
        ("excel_read", {"path": "{xlsx}"}),
        ("excel_query", {"path": "{xlsx}", "op": "sum", "column": "amount"}),
        ("excel_profile", {"path": "{xlsx}"}),
    ],
)
async def test_every_kept_reader_that_takes_a_path_obeys_the_confine(tmp_path, tool, args):
    """Each reader on the keep list that accepts an absolute path is driven
    at a file OUTSIDE the confined folder and must refuse without reading
    it. A reader that fails this leaves the keep list."""
    from iron_jarvis.core import fs_policy
    from iron_jarvis.tools.base import ToolContext

    _app_, platform = _app(tmp_path / "home")
    own = tmp_path / "run folder"
    own.mkdir()
    outside = tmp_path / "papers kept here"
    outside.mkdir()
    # A REAL file of each reader's own kind, so only the confine can refuse.
    import openpyxl
    from fpdf import FPDF

    txt = outside / "ledger.txt"
    txt.write_text("private-row 42", encoding="utf-8")
    xlsx = outside / "ledger.xlsx"
    book = openpyxl.Workbook()
    book.active.title = "Sheet"
    book.active.append(["name", "amount"])
    book.active.append(["private-row", 42])
    book.save(xlsx)
    pdf = outside / "ledger.pdf"
    page = FPDF()
    page.add_page()
    page.set_font("Helvetica", size=12)
    page.cell(0, 10, "private-row 42")
    page.output(str(pdf))
    filled = {
        k: v.format(folder=str(outside), txt=str(txt), pdf=str(pdf), xlsx=str(xlsx))
        for k, v in args.items()
    }
    # CONTROL: with no confine the same call reads it, so a refusal below is
    # the confine's and not a file the reader could not open anyway.
    free = await platform.registry.get(tool).execute(dict(filled), ToolContext(
        workspace=own, session_id="s0", agent_run_id="r0", config=platform.config,
        event_bus=None, engine=platform.engine,
    ))
    assert free.ok, f"{tool} cannot read its own kind of file here: {free.error}"
    ctx = ToolContext(
        workspace=own, session_id="s1", agent_run_id="r1", config=platform.config,
        event_bus=None, engine=platform.engine,
    )
    instance = platform.registry.get(tool)
    assert instance is not None, tool
    token = fs_policy.confine_reads([own])
    try:
        assert fs_policy.reads_confined()
        result = await instance.execute(filled, ctx)
    finally:
        fs_policy.release_reads(token)
    said = f"{result.output or ''} {result.error or ''}"
    assert result.ok is False, f"{tool} read a file outside the confined folder: {said[:200]}"
    assert "private-row" not in said
    assert not fs_policy.reads_confined(), "the confine outlived its scope"


def test_a_helpers_folder_is_added_to_the_confine_never_swapped_for_it(tmp_path):
    from iron_jarvis.core import fs_policy

    parent, child, other = tmp_path / "parent", tmp_path / "child", tmp_path / "other"
    for folder in (parent, child, other):
        folder.mkdir()
    assert fs_policy.fs_read_ok(str(other))[0], "no confine: readable as before"
    outer = fs_policy.confine_reads([parent])
    try:
        assert fs_policy.fs_read_ok(str(parent / "a.txt"))[0]
        assert not fs_policy.fs_read_ok(str(child))[0]
        inner = fs_policy.confine_reads([child])
        try:
            assert fs_policy.fs_read_ok(str(child / "b.txt"))[0]
            assert fs_policy.fs_read_ok(str(parent / "a.txt"))[0]
            ok, why = fs_policy.fs_read_ok(str(other))
            assert not ok and why == fs_policy.CONFINED_REFUSAL
        finally:
            fs_policy.release_reads(inner)
        assert not fs_policy.fs_read_ok(str(child))[0]
    finally:
        fs_policy.release_reads(outer)
    assert fs_policy.fs_read_ok(str(other))[0]
    # an unreadable list confines to NOTHING, never to everything
    token = fs_policy.confine_reads([""])
    try:
        assert not fs_policy.fs_read_ok(str(other))[0]
    finally:
        fs_policy.release_reads(token)
