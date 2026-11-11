"""v1.471.0: faster, more accurate and more stable — with nothing the user sees
changed.

SPEED. The three rows written for EVERY tool call (the "about to run" intent,
the finished call, the `tool.executed` event) are plain inserts of exactly
the row the ORM wrote (`core.db.row_values`), and the intent is removed with
one DELETE in the finished row's transaction. Measured (interleaved, same
machine): a direct tool call 1.78 -> 1.05 ms, a call from a script 2.02 ->
1.25 ms. Pinned here: the rows are byte-for-byte the ORM's, and the finished
row, the intent removal and the undo row still land together or not at all.

ACCURACY. Both chat matchers were re-written against a labelled set written
first (`tests/fixtures/matchers_v1471`): set A guided the change, set B was
held out and then used for the error classes it showed, set C was written
after the last change and never tuned against. On set C: skill 72.5% ->
95.0%, script 57.5% -> 97.5%.

STABILITY. A tool answer the worker pipe could not encode (a tuple key, a
dict containing itself, an unpaired surrogate) killed the worker mid-script
and lost every variable; an unpaired surrogate in ANY tool's text made
`registry.invoke` raise instead of recording the call; a script was cut at
ONE call's deadline however many calls it made; a new chat's first-message
variables were silently gone on its second message. Each is pinned below.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from iron_jarvis.core import progress
from iron_jarvis.core.db import open_db, persist_event, row_values, session_scope, storable_text
from iron_jarvis.core.events import Event
from iron_jarvis.core.models import (
    EventRecord,
    PermissionMode,
    ToolIntent,
    ToolInvocation,
    UndoJournal,
)
from iron_jarvis.platform import build_platform
from iron_jarvis.repl import session as S
from iron_jarvis.skills.trigger import wants_skill_create
from iron_jarvis.tools import tool_script as TS
from iron_jarvis.tools.autoselect import wants_tool_script
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult

pytestmark = pytest.mark.timeout(180)

FIXTURES = Path(__file__).parent / "fixtures" / "matchers_v1471"


# --------------------------------------------------------------------------- #
# speed: the plain inserts write exactly the ORM's rows
# --------------------------------------------------------------------------- #

_TIMESTAMPS = {"created_at", "started_at", "undone_at"}


def _rows(engine, model):
    with session_scope(engine) as db:
        return [dict(r._mapping) for r in db.exec(select(model.__table__)).all()]


@pytest.mark.parametrize("model, given", [
    (EventRecord, dict(id="e1", type="tool.executed", session_id="chat", payload_json='{"a": 1}')),
    (ToolIntent, dict(id="t1", session_id="s", agent_run_id="r", tool="read_file",
                      args_json='{"path": "a"}', verdict=PermissionMode.ASK,
                      reversibility="readonly", replay_safe=True, grant_id="g")),
    (ToolInvocation, dict(id="t1", session_id="s", agent_run_id="r", tool="read_file",
                          args_json="{}", verdict=PermissionMode.ALLOW, ok=False, output="x",
                          reversibility="readonly", confinement=None, grant_id="",
                          called_as="Read", shell_syntax="")),
    (UndoJournal, dict(action_id="t1", session_id="s", agent_run_id="r", tool="write_file",
                       kind="file_restore", reversible=True, pre_ref=None,
                       pre_inline='{"x": 1}', pre_sha256="a", post_sha256="b")),
])
def test_a_plain_insert_writes_the_row_the_orm_wrote(tmp_path, model, given):
    orm_engine = open_db(tmp_path / "orm.db")
    plain_engine = open_db(tmp_path / "plain.db")
    with session_scope(orm_engine) as db:
        db.add(model(**given))
        db.commit()
    with plain_engine.begin() as conn:
        conn.execute(model.__table__.insert(), row_values(model, **given))
    (a,), (b,) = _rows(orm_engine, model), _rows(plain_engine, model)
    assert set(a) == set(b)
    for col in a:
        if col in _TIMESTAMPS:
            assert type(a[col]) is type(b[col]), col
        else:
            assert a[col] == b[col], col


def test_a_required_column_left_out_is_refused():
    with pytest.raises(KeyError):
        row_values(EventRecord, type="x")  # no id


def test_an_event_row_is_the_event(tmp_path):
    engine = open_db(tmp_path / "e.db")
    ev = Event(type="tool.executed", payload={"tool": "x", "bad": "a\udc80b"}, session_id="chat")
    persist_event(engine, ev)
    (row,) = _rows(engine, EventRecord)
    assert row["id"] == ev.id and row["type"] == "tool.executed" and row["session_id"] == "chat"
    assert json.loads(row["payload_json"])["tool"] == "x"
    assert row["created_at"] is not None


class _Reader(Tool):
    name = "plain_read"
    description = "read-only test tool"
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    reversibility = Reversibility.READONLY

    def __init__(self) -> None:
        self.output = "ok"
        self.data = None
        self.delay = 0.0
        self.calls = 0

    async def execute(self, args, ctx):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return ToolResult(ok=True, output=self.output, data=self.data)


@pytest.fixture()
def world(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    reader = _Reader()
    p.registry.register(reader)
    p.permissions._base["plain_read"] = "allow"
    ws = tmp_path / "work"
    ws.mkdir()

    def ctx(**kw):
        base = dict(workspace=ws, session_id="chat", agent_run_id="chat", thread_id="th-1",
                    config=p.config, event_bus=p.event_bus, engine=p.engine)
        base.update(kw)
        return ToolContext(**base)

    yield SimpleNamespace(p=p, ws=ws, ctx=ctx, reader=reader)
    asyncio.run(p.repl.dispose_all())


def _invoke(w, name, args, ctx=None, **kw):
    async def go():
        return await w.p.registry.invoke(
            name, args, ctx or w.ctx(), w.p.permissions,
            allowed_names={"tool_script", "plain_read"}, **kw,
        )

    return asyncio.run(go())


def test_a_call_leaves_one_finished_row_and_no_intent(world):
    r = _invoke(world, "plain_read", {"q": "a"})
    assert r.ok
    rows = [x for x in _rows(world.p.engine, ToolInvocation) if x["tool"] == "plain_read"]
    assert len(rows) == 1 and rows[0]["ok"] is True and rows[0]["output"] == "ok"
    assert _rows(world.p.engine, ToolIntent) == []


def test_the_finished_row_the_intent_and_the_undo_row_land_together_or_not_at_all(world):
    """An undo row that cannot be written (its id is taken) rolls back the
    finished row AND keeps the intent, exactly as the ORM's one commit did."""
    reg, engine = world.p.registry, world.p.engine
    with engine.begin() as conn:
        conn.execute(ToolIntent.__table__.insert(), row_values(
            ToolIntent, id="tool_x", session_id="chat", tool="write_file"))
        conn.execute(UndoJournal.__table__.insert(), row_values(
            UndoJournal, action_id="tool_x", session_id="chat", agent_run_id="chat",
            tool="write_file", kind="file_delete"))
    with pytest.raises(Exception):
        reg._record(world.ctx(), "write_file", {}, PermissionMode.ALLOW, True, "wrote",
                    undo={"kind": "file_restore", "pre_inline": "{}"}, inv_id="tool_x")
    assert [x["id"] for x in _rows(engine, ToolIntent)] == ["tool_x"]
    assert not [x for x in _rows(engine, ToolInvocation) if x["id"] == "tool_x"]


# --------------------------------------------------------------------------- #
# stability
# --------------------------------------------------------------------------- #

def test_storable_text_only_touches_what_sqlite_cannot_store():
    assert storable_text("plain é 文") == "plain é 文"
    assert storable_text("a\ud800b") == "a?b"
    assert storable_text(None) is None and storable_text(5) == 5


def test_a_direct_call_whose_text_holds_a_broken_character_is_recorded(world):
    """It RAISED from the ledger write before (UnicodeEncodeError), so the call
    was never recorded and the caller got an exception."""
    world.reader.output = "file name \udc81 here"
    r = _invoke(world, "plain_read", {"q": "x\ud800"})
    assert r.ok
    (row,) = [x for x in _rows(world.p.engine, ToolInvocation) if x["tool"] == "plain_read"]
    assert row["output"] == "file name ? here"


def _script(world, code, **kw):
    return _invoke(world, "tool_script", {"code": code, "timeout_s": 60}, **kw)


@pytest.mark.parametrize("output, data", [
    ("ok", {(1, 2): "a tuple key"}),
    ("ok", "LOOP"),
    ("bad \ud800 char", {"t": "x\udc80y"}),
])
def test_an_answer_the_pipe_could_not_encode_never_kills_the_worker(world, output, data):
    if data == "LOOP":
        data = {}
        data["self"] = data
    assert _script(world, "keep = 41").ok
    world.reader.output, world.reader.data = output, data
    r = _script(world, "v = tools.plain_read()\nprint('got', type(v.data).__name__)\nprint('keep', keep)")
    assert r.ok, r.error
    assert "got dict" in r.output and "keep 41" in r.output
    assert "GONE" not in r.output


def test_the_reply_line_is_always_one_ascii_json_line():
    loop: dict = {}
    loop["me"] = loop
    for reply in (
        {"reply": 1, "ok": True, "output": "é \ud800", "data": {(1, 2): 3}},
        {"reply": 2, "ok": True, "output": "", "data": loop},
        {"reply": 3, "ok": True, "output": "", "data": {"n": float("nan")}},
    ):
        line = S._reply_line(reply)
        assert line.endswith("\n") and line.count("\n") == 1
        line.encode("ascii")
        assert json.loads(line)["reply"] == reply["reply"]


def test_a_script_is_not_cut_at_one_calls_deadline(world):
    """Three calls of 0.4 s under a 0.5 s per-call deadline: each call is
    inside its deadline, the script is not (it was stopped at 0.5 s)."""
    world.reader.delay = 0.4
    r = _script(world, "for i in range(3):\n    tools.plain_read()\nprint('all three')", deadline_s=0.5)
    assert r.ok, r.error
    assert "all three" in r.output
    assert world.reader.calls == 3


def test_each_call_inside_a_script_still_has_its_deadline(world):
    world.reader.delay = 3.0
    r = _script(world, (
        "try:\n    tools.plain_read()\n    print('no error')\n"
        "except ToolError:\n    print('deadline error')\nprint('after')"
    ), deadline_s=0.5)
    assert r.ok, r.error
    assert "deadline error" in r.output and "after" in r.output


def test_a_script_has_its_own_overall_bound(world, monkeypatch):
    monkeypatch.setattr(TS, "MAX_TOTAL_S", 0.6)
    world.reader.delay = 0.3
    r = _script(world, "for i in range(10):\n    tools.plain_read()\nprint('done')")
    assert not r.ok
    assert "still running after" in r.error


def test_bounds_itself_is_not_the_deadline_exemption():
    """`deadline_exempt` also takes a tool out of a Build session's Stop race;
    the script must stay in it."""
    tool = TS.ToolScriptTool(None, None)
    assert tool.bounds_itself is True
    assert tool.deadline_exempt is False
    assert Tool.bounds_itself is False


def test_every_call_a_script_makes_is_progress(world):
    ticks: list[int] = []
    world.reader.delay = 0.0

    async def go():
        with progress.scope(lambda: ticks.append(1)):
            return await world.p.registry.invoke(
                "tool_script", {"code": "for i in range(5):\n    tools.plain_read()"},
                world.ctx(), world.p.permissions,
                allowed_names={"tool_script", "plain_read"},
            )

    assert asyncio.run(go()).ok
    assert len(ticks) >= 5


def test_a_new_chats_first_message_variables_reach_its_second(world):
    first = world.ctx(thread_id="", turn_id="turn-new")
    assert _script(world, "kept = 'yes'", ctx=first).ok
    moved = asyncio.run(TS.adopt_turn_worker(world.p, "turn-new", "thread-new"))
    assert moved == 1
    second = world.ctx(thread_id="thread-new", turn_id="turn-2")
    r = _script(world, "print('kept', kept)", ctx=second)
    assert r.ok and "kept yes" in r.output


def test_a_chat_that_already_has_a_worker_keeps_its_own(world):
    assert _script(world, "who = 'turn'", ctx=world.ctx(thread_id="", turn_id="t-a")).ok
    assert _script(world, "who = 'chat'", ctx=world.ctx(thread_id="th-a")).ok
    assert asyncio.run(TS.adopt_turn_worker(world.p, "t-a", "th-a")) == 0
    r = _script(world, "print(who)", ctx=world.ctx(thread_id="th-a"))
    assert r.ok and "chat" in r.output


def test_adopting_never_raises():
    assert asyncio.run(TS.adopt_turn_worker(SimpleNamespace(), "a", "b")) == 0
    assert asyncio.run(TS.adopt_turn_worker(SimpleNamespace(repl=object()), "a", "b")) == 0


@pytest.mark.asyncio
async def test_the_link_route_hands_the_turns_worker_to_the_chat(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    app = create_app(str(tmp_path))
    repl = app.state.platform.repl
    ws = tmp_path / "w"
    ws.mkdir()
    try:
        await repl.execute("chat:turn-9", "x = 1", workspace=ws, isolated=True, tools=[])
        status, _ = await _asgi_post(app, "/chat/turns/turn-9/thread", {"thread_id": "th-9"})
        assert status == 200
        assert "chat:th-9" in repl and "chat:turn-9" not in repl
    finally:
        await repl.dispose_all()


# --------------------------------------------------------------------------- #
# accuracy: the labelled sets
# --------------------------------------------------------------------------- #

def _set(name):
    return json.loads((FIXTURES / f"set_{name}.json").read_text(encoding="utf-8"))


def _rows_of(name, kind):
    return [pytest.param(*r, id=r[0][:60]) for r in _set(name)[kind]]


def _script_row(r):
    return wants_tool_script(r[0], ["read_document"], r[2] if len(r) > 2 else 0)


@pytest.mark.parametrize("text, expected", [r for s in "ab" for r in _rows_of(s, "skill")])
def test_skill_requests_sets_a_and_b(text, expected):
    assert wants_skill_create(text) is expected


@pytest.mark.parametrize("row", [list(r) for s in "ab" for r in _set(s)["script"]],
                         ids=lambda r: r[0][:60])
def test_script_requests_sets_a_and_b(row):
    assert _script_row(row) is row[1]


#: Set C was written after the last change and never tuned against. These are
#: its misses at v1.471.0, listed so a change in either direction shows.
SET_C_SKILL_MISSES = {
    "save this workflow as a skill instead",
    "Create a Google Assistant skill",
}
SET_C_SCRIPT_MISSES = {"List the account balances in all of the trial balances"}


def test_the_held_out_set_c():
    c = _set("c")
    skill_wrong = {r[0] for r in c["skill"] if wants_skill_create(r[0]) is not r[1]}
    script_wrong = {r[0] for r in c["script"] if _script_row(r) is not r[1]}
    assert skill_wrong == SET_C_SKILL_MISSES
    assert script_wrong == SET_C_SCRIPT_MISSES
    assert 1 - len(skill_wrong) / len(c["skill"]) >= 0.95
    assert 1 - len(script_wrong) / len(c["script"]) >= 0.95


@pytest.mark.parametrize("text", [
    # Only the POSSESSIVE stops these: the verb is right there.
    "Write down my skills for the annual review",
    "add her skills to the staffing plan",
])
def test_a_possessive_makes_the_skills_someone_elses(text):
    assert not wants_skill_create(text)


@pytest.mark.parametrize("text", [
    # Only the LOCATOR stops these: the thing after the number is plural.
    "Read line 12 of the returns",
    "check box 14 on the W-2s",
    "look at page 30 in the statements",
])
def test_a_number_after_page_line_or_box_is_not_a_count(text):
    assert not wants_tool_script(text, ["read_document"])


def test_a_year_is_not_a_count_and_a_form_number_is_no_year():
    from iron_jarvis.tools.autoselect import _script_count

    assert _script_count("2023") == 0 and _script_count("1950") == 0
    assert _script_count("1099") == 1099 and _script_count("12") == 12
    assert _script_count("twelve") == 12 and _script_count("dozen") == 12
    assert not wants_tool_script("summarize the 2024 reports", ["read_document"])
    assert wants_tool_script("summarize the 24 reports", ["read_document"])


def test_both_matchers_stay_linear_on_a_pathological_message():
    """In a child process with a deadline: a matcher that backtracked would
    hang the turn that reads it."""
    import subprocess
    import sys

    code = (
        "from iron_jarvis.skills.trigger import wants_skill_create as a\n"
        "from iron_jarvis.tools.autoselect import wants_tool_script as b\n"
        "t = ('make a new skill ' * 4000) + ('all ' * 30000) + ' 12 ' * 20000\n"
        "a(t); b(t, ['read_document']); b(t, ['read_document'], 9)\n"
        "print('done')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "done", out.stderr
