"""Next leaps 13-b (master plan R2.10): by date, by client, with links you can
press (``docs/next-leaps/04-a-memory-that-compounds.md``, idea 13).

``work_history`` gains ``after`` / ``before`` (ISO dates the MODEL supplies:
the owner's own days, no date parser for words), ``kinds`` and ``client``,
read through the ONE reader (``workmap/lookup.lookup``) and a new reader on
the record (``WorkMap.between``, one read on ``updated_at``):

* the idea's own example: "what did we do for Baker in March?" answers
  "3 pieces of work in March 2026, for Baker", each a row whose link opens
  ITS chat;
* ``client`` is honoured only for the turn's own client or one the OWNER's
  own message named; a client named only by the model gets
  ``NAME_THE_CLIENT`` and nothing, the same words for a name that is no
  client at all; the wall still decides every row of an honoured client;
* status words (finished, failed, stopped, needs you, still open), the Open
  count from the kept recap, the Open LINE only where the recap rule allows;
* the rows reach the screen only through the tool's own result: the step
  frame's ``data.rows`` (``chat_turn.tool_frame_data``) and a reply written
  home (``durable_turns.reply_line``);
* ``work_search`` (another app) passes the same dates with its wall
  unchanged: every owned row is still refused.

Made-up names and folders only (Baker, Hartleys, Smith, Jones). Folders are
TEXT here, as in ``tests/test_next_leaps_13a_work_history.py``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import AgentRun, ChatThreadRecord, Session, SessionStatus
from iron_jarvis.providers import failure_words as FW
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.workmap import block as B
from iron_jarvis.workmap import keys
from iron_jarvis.workmap import lookup as L
from iron_jarvis.workmap import recap as R
from iron_jarvis.workmap import tools as WT
from iron_jarvis.workmap.clients import ClientRoot, Waiting, names, store, wall
from iron_jarvis.workmap.index import work_map
from iron_jarvis.workmap.models import RECAP_KEPT, WorkRecap

BK_ID = "client_00000000000d"  # Baker
H_ID = "client_00000000000a"  # Hartleys
S_ID = "client_00000000000b"  # Smith
J_ID = "client_00000000000c"  # Jones (waiting)

BAKER = r"D:\Clients\Baker"
BAKER_OTHER = r"D:\Work\B-77"  # Baker's too: a folder that does not carry the name
H = r"D:\Clients\Hartleys"
S = r"D:\Clients\Smith"
JONES = r"D:\Work\Jones"
DOWNLOADS = r"C:\Users\Owner\Downloads"
DOCS = r"C:\Users\Owner\Documents\Templates"

MARCH = {"after": "2026-03-01", "before": "2026-03-31"}


def _k(folder: str) -> str:
    return keys.folder_key(keys.home_folder(folder))


def make_map() -> store.ClientMap:
    confirmed = [(BK_ID, "Baker", [BAKER, BAKER_OTHER]), (H_ID, "Hartleys", [H]), (S_ID, "Smith", [S])]
    roots = tuple(ClientRoot(cid, name, _k(f), f) for cid, name, folders in confirmed for f in folders)
    return store.ClientMap(
        version=1, confirmed=roots, waiting=(Waiting(J_ID, "Jones", (_k(JONES),)),),
        names={cid: (name, names.signature(name), ()) for cid, name, _f in confirmed},
        waiting_names={J_ID: ("Jones", names.signature("Jones"), ())},
    )


@pytest.fixture()
def scene(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "record.db")
    init_db(engine)
    home = tmp_path / "home"
    home.mkdir()
    cmap = make_map()
    monkeypatch.setattr(store, "load_map", lambda engine: cmap)
    # The owner's days in UTC unless a case says otherwise (one seam:
    # ``block._local_zone``, read by the dates and by the bounds).
    monkeypatch.setattr(B, "_local_zone", lambda: timezone.utc)
    s = SimpleNamespace(engine=engine, home=home, cmap=cmap, tmp=tmp_path)
    s.platform = SimpleNamespace(engine=engine, config=SimpleNamespace(home=home))
    s.wm = work_map(engine, home)
    s.tool = WT.WorkHistoryTool(s.platform)
    yield s
    engine.dispose()


def _chat(scene, title, folder, *, at, docs=()) -> str:
    setup = {"workspace_dir": folder} if folder else {}
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "done", "documents": list(docs)}]
    with session_scope(scene.engine) as db:
        rec = ChatThreadRecord(title=title, messages_json=json.dumps(messages),
                               setup_json=json.dumps(setup) if setup else "", updated_at=at)
        db.add(rec)
        db.commit()
        return rec.id


def _run(scene, task, folder, *, at, status=SessionStatus.COMPLETED) -> str:
    with session_scope(scene.engine) as db:
        s = Session(task=task, summary="All done.", status=status, origin="chat",
                    workspace_path=folder, created_at=at, finished_at=at, options_json="{}")
        db.add(s)
        db.commit()
        db.refresh(s)
        db.add(AgentRun(session_id=s.id, parent_id=None))
        db.commit()
        return s.id


def _recap(scene, tid, text):
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, tid)
        db.add(WorkRecap(id=f"chat:{tid}", kind="chat", ref=tid, state=RECAP_KEPT, text=text,
                         source_updated=R.stamp_of(rec.updated_at)))
        db.commit()


def _at(month: int, day: int, hour: int = 15) -> datetime:
    return datetime(2026, month, day, hour, 0)


async def _ask(scene, *, folder="", words="", args=None):
    """One ``work_history`` call inside the wall of a turn in ``folder``
    whose OWNER said ``words``."""
    scene.wm.refresh(force=True)
    with wall.turn_scope(wall.turn_wall(scene.engine, folder, words)):
        return await scene.tool.execute(dict(args or {}), SimpleNamespace(thread_id="", session_id="chat"))


def _titles(result) -> list[str]:
    return [r["title"] for r in (result.data or {}).get("rows", [])]


@pytest.fixture()
def march(scene):
    """Three pieces of Baker's work in March (one in a Baker folder that
    does not carry the name), Baker's work either side of March, and other
    people's March work."""
    scene.ids = {
        "Baker payroll setup": _chat(scene, "Baker payroll setup", BAKER, at=_at(3, 4),
                                     docs=[BAKER + r"\payroll.xlsx"]),
        "Baker sales tax filing": _chat(scene, "Baker sales tax filing", BAKER + r"\2026", at=_at(3, 17)),
        "Bank feed review": _chat(scene, "Bank feed review", BAKER_OTHER, at=_at(3, 29)),
        "Baker January close": _chat(scene, "Baker January close", BAKER, at=_at(2, 20)),
        "Baker April close": _chat(scene, "Baker April close", BAKER, at=_at(4, 2)),
        "Price list update": _chat(scene, "Price list update", DOCS, at=_at(3, 10),
                                   docs=[DOCS + r"\prices.xlsx"]),
        "Smith quarterly report": _chat(scene, "Smith quarterly report", S, at=_at(3, 12)),
    }
    scene.wm.refresh(force=True)
    return scene


BAKER_MARCH = ["Bank feed review", "Baker sales tax filing", "Baker payroll setup"]


# --------------------------------------------------------------------------- #
# 1. the idea's own example
# --------------------------------------------------------------------------- #
async def test_the_ideas_own_example_three_pieces_in_march_each_opening_its_chat(march):
    got = await _ask(march, folder="", words="what did we do for Baker in March?",
                     args={"client": "Baker", **MARCH})
    assert got.ok, got.error
    assert _titles(got) == BAKER_MARCH  # newest first, nothing from February or April
    lines = got.output.splitlines()
    assert lines[0] == WT.LEAD and lines[1] == "3 pieces of work in March 2026, for Baker:"
    for row in got.data["rows"]:
        assert row["link"] == "/chat?thread=" + march.ids[row["title"]], row
    assert "Price list update" not in got.output and "Smith" not in got.output


async def test_a_date_range_alone_looks_everywhere_the_wall_allows(march):
    got = await _ask(march, folder=DOCS, words="what did we do in March?", args=dict(MARCH))
    assert _titles(got) == ["Price list update"]  # not only this folder's, and no client's
    assert got.output.splitlines()[1] == "1 piece of work in March 2026:"
    # Baker's three and Smith's one matched and were kept back, counted.
    assert got.data["held_back"] == 4
    assert WT.left_out_words(4, in_client=False) in got.output


async def test_a_date_is_the_owners_own_day(scene, monkeypatch):
    monkeypatch.setattr(B, "_local_zone", lambda: timezone(timedelta(hours=-5)))
    _chat(scene, "Late February work", DOCS, at=datetime(2026, 3, 1, 2, 30))  # Feb 28, 21:30 there
    _chat(scene, "Last evening of March", DOCS, at=datetime(2026, 4, 1, 3, 0))  # Mar 31, 22:00 there
    march = await _ask(scene, folder=DOCS, words="what did we do in March?", args=dict(MARCH))
    assert _titles(march) == ["Last evening of March"]
    feb = await _ask(scene, folder=DOCS, words="and in February?",
                     args={"after": "2026-02-01", "before": "2026-02-28"})
    assert _titles(feb) == ["Late February work"]
    assert feb.output.splitlines()[1] == "1 piece of work in February 2026:"


def test_the_bounds_are_the_owners_days_and_the_period_is_said_in_words(monkeypatch):
    monkeypatch.setattr(B, "_local_zone", lambda: timezone(timedelta(hours=-5)))
    first, day = L.bound("2026-03-01")
    assert first == datetime(2026, 3, 1, 5, 0) and day.isoformat() == "2026-03-01"
    last, _ = L.bound("2026-03-31", end=True)
    assert last == datetime(2026, 4, 1, 4, 59, 59, 999999)
    assert L.bound("") == (None, None)
    with pytest.raises(ValueError):
        L.bound("in March")
    d = datetime(2026, 3, 1).date()
    assert WT.period_words(d, datetime(2026, 3, 31).date()) == "in March 2026"
    assert WT.period_words(d, d) == "on Mar 1, 2026"
    assert WT.period_words(d, datetime(2026, 3, 15).date()) == "from Mar 1, 2026 to Mar 15, 2026"
    assert WT.period_words(datetime(2026, 1, 1).date(), datetime(2026, 12, 31).date()) == "in 2026"
    assert WT.period_words(d, None) == "since Mar 1, 2026" and WT.period_words(None, d) == "up to Mar 1, 2026"


async def test_nothing_in_the_range_says_so_for_that_range(march):
    got = await _ask(march, folder="", words="what did we do for Baker in June?",
                     args={"client": "Baker", "after": "2026-06-01", "before": "2026-06-30"})
    assert got.ok and _titles(got) == []
    assert got.output == "Nothing on Iron Jarvis's record of earlier work in June 2026, for Baker."


def test_between_is_one_read_by_date_kind_and_folder(march):
    wm = march.wm
    first, _ = L.bound("2026-03-01")
    last, _ = L.bound("2026-03-31", end=True)
    titles = [r["title"] for r in wm.between(first, last)]
    assert titles == ["Bank feed review", "Baker sales tax filing", "Smith quarterly report",
                      "Price list update", "Baker payroll setup"]
    assert [r["title"] for r in wm.between(first, last, [BAKER])] == ["Baker sales tax filing",
                                                                       "Baker payroll setup"]
    assert wm.between(first, last, []) == [] and wm.between(first, last, kinds=["run"]) == []
    assert [r["title"] for r in wm.between(None, first)] == ["Baker January close"]


# --------------------------------------------------------------------------- #
# 2. a client, only when the owner's own words allow it
# --------------------------------------------------------------------------- #
async def test_a_client_named_only_by_the_model_returns_nothing(march):
    got = await _ask(march, folder="", words="what did we do in March?", args={"client": "Baker", **MARCH})
    assert got.ok and _titles(got) == [] and got.output == WT.NAME_THE_CLIENT
    assert got.data["client_refused"] is True and got.data["held_back"] == 0
    # A name that is no client, and a name still waiting for the owner's
    # answer, are answered in the same words: nothing says which is a client.
    for name in ("Zorro", "Jones", "baker account"):
        other = await _ask(march, folder="", words="what did we do in March?", args={"client": name, **MARCH})
        assert other.output == WT.NAME_THE_CLIENT, name
    # In another client's own chat too.
    smiths = await _ask(march, folder=S, words="what did we do in March?", args={"client": "Baker", **MARCH})
    assert smiths.output == WT.NAME_THE_CLIENT


async def test_a_client_named_by_the_owner_in_any_case_and_spelling(march):
    got = await _ask(march, folder=DOWNLOADS, words="Anything for the Baker account in March?",
                     args={"client": "the baker account", **MARCH})
    assert _titles(got) == BAKER_MARCH


async def test_the_wall_still_decides_every_row_of_an_honoured_client(march):
    """In Smith's own chat the owner names Baker: the client is honoured,
    and the wall keeps every Baker row out of Smith's chat, counted."""
    got = await _ask(march, folder=S, words="what did we do for Baker in March?",
                     args={"client": "Baker", **MARCH})
    assert _titles(got) == [] and got.data["held_back"] == 3
    assert got.output == WT.left_out_words(3, in_client=True)
    assert "Baker" not in got.output


async def test_the_wall_is_what_keeps_them_out(march, monkeypatch):
    """The control for the case above: with the wall forced open, the same
    call returns Baker's three."""
    monkeypatch.setattr(wall, "may_tell", lambda *a, **k: True)
    got = await _ask(march, folder=S, words="what did we do for Baker in March?",
                     args={"client": "Baker", **MARCH})
    assert _titles(got) == BAKER_MARCH


async def test_the_chats_own_client_needs_no_naming(march):
    got = await _ask(march, folder=BAKER, words="what did we do here in March?",
                     args={"client": "Baker", **MARCH})
    assert _titles(got) == BAKER_MARCH


async def test_the_query_narrows_inside_the_clients_work(march):
    got = await _ask(march, folder="", words="what did we do for Baker on the sales tax?",
                     args={"client": "Baker", "query": "Baker sales"})
    assert _titles(got) == ["Baker sales tax filing"]
    # The client's own name alone chooses nothing more: all of Baker's work.
    every = await _ask(march, folder="", words="what did we do for Baker?",
                       args={"client": "Baker", "query": "Baker"})
    assert set(_titles(every)) == {*BAKER_MARCH, "Baker January close", "Baker April close"}


async def test_on_the_phone_a_named_client_comes_with_titles_dates_and_status_only(march):
    with FW.phone_surface():
        got = await _ask(march, folder="", words="what did we do for Baker in March?",
                         args={"client": "Baker", **MARCH})
    assert _titles(got) == BAKER_MARCH and WT.PHONE_NOTE in got.output
    assert "payroll.xlsx" not in got.output and BAKER not in got.output
    assert all(r["link"] == "" and r["paths"] == [] and r["decisions"] == "" for r in got.data["rows"])
    assert WT.screen_rows(got) == []  # nothing to draw


# --------------------------------------------------------------------------- #
# 3. how each piece stands, and where its decisions are read
# --------------------------------------------------------------------------- #
async def test_status_words_and_the_open_count(scene):
    open_chat = _chat(scene, "Engagement letters", DOCS, at=_at(3, 5))
    _recap(scene, open_chat, "DONE: sent two letters\nOPEN: the third letter waits on the bank")
    _run(scene, "Reconcile the March bank feed", DOCS, at=_at(3, 6))
    _run(scene, "Import the old invoices", DOCS, at=_at(3, 7), status=SessionStatus.FAILED)
    here = await _ask(scene, folder=DOCS, words="what did we do in March?", args=dict(MARCH))
    by = {r["title"]: r for r in here.data["rows"]}
    assert by["Engagement letters"]["status"] == WT.STILL_OPEN and by["Engagement letters"]["open"] == 1
    assert by["Reconcile the March bank feed"]["status"] == "finished"
    assert by["Import the old invoices"]["status"] == "failed"
    # In its own folder the Open line itself is told ...
    assert "the third letter waits on the bank" in here.output
    line = next(ln for ln in here.output.splitlines() if "Engagement letters" in ln)
    assert WT.STILL_OPEN in line
    # ... from another folder only that it is still open, never the line.
    there = await _ask(scene, folder=DOWNLOADS, words="what did we do in March?", args=dict(MARCH))
    row = next(r for r in there.data["rows"] if r["title"] == "Engagement letters")
    assert row["status"] == WT.STILL_OPEN and row["open"] == 1 and not row["has_recap"]
    assert "the third letter waits on the bank" not in there.output
    assert by["Engagement letters"]["decisions"] == "/memory?work=chat%3A" + open_chat
    assert by["Reconcile the March bank feed"]["decisions"] == ""  # no recap: nothing to read


async def test_a_clients_piece_opens_its_decisions_on_the_clients_page(march):
    _recap(march, march.ids["Baker payroll setup"], "DECIDED: payroll runs on Fridays")
    got = await _ask(march, folder="", words="what did we do for Baker in March?",
                     args={"client": "Baker", **MARCH})
    row = next(r for r in got.data["rows"] if r["title"] == "Baker payroll setup")
    assert row["decisions"] == "/clients/" + BK_ID
    assert row["paths"] == [BAKER + r"\payroll.xlsx"] and row["files"] == ["payroll.xlsx"]


async def test_kinds_narrow_to_agent_runs(scene):
    _chat(scene, "A chat in March", DOCS, at=_at(3, 5))
    _run(scene, "Reconcile the March bank feed", DOCS, at=_at(3, 6))
    got = await _ask(scene, folder=DOCS, words="which agent runs did we do in March?",
                     args={"kinds": ["agent_run"], **MARCH})
    assert _titles(got) == ["Reconcile the March bank feed"]
    assert got.data["rows"][0]["link"].startswith("/sessions/")


@pytest.mark.parametrize("args, words", [
    ({"after": "March"}, "BAD_DATE"),
    ({"before": "31/03/2026"}, "BAD_DATE"),
    ({"after": "2026-04-01", "before": "2026-03-01"}, "BAD_RANGE"),
    ({"kinds": ["emails"]}, "BAD_KIND"),
])
async def test_a_filter_it_cannot_read_is_refused_in_plain_words(scene, args, words):
    got = await _ask(scene, folder=DOCS, words="what did we do?", args=args)
    assert got.ok is False and got.error == getattr(WT, words)


# --------------------------------------------------------------------------- #
# 4. the rows reach the screen only through the tool's own result
# --------------------------------------------------------------------------- #
async def test_the_step_frame_carries_the_rows_and_nothing_else_changes(march):
    from iron_jarvis.daemon import chat_turn as CT

    got = await _ask(march, folder="", words="what did we do for Baker in March?",
                     args={"client": "Baker", **MARCH})
    framed = CT.tool_frame_data("work_history", got, True)
    rows = framed["data"]["rows"]
    assert [r["title"] for r in rows] == BAKER_MARCH
    assert {r["link"] for r in rows} == {"/chat?thread=" + march.ids[t] for t in BAKER_MARCH}
    assert set(rows[0]) == {"id", "kind", "title", "when", "status", "open", "link", "files", "paths",
                            "files_total", "decisions", "times"}
    # Every other step's frame, a call that did not run, and nothing found: as before.
    assert CT.tool_frame_data("history_search", got, True) == {}
    assert CT.tool_frame_data("work_history", got, False) == {}
    empty = await _ask(march, folder="", words="what did we do in March?", args={"client": "Baker", **MARCH})
    assert CT.tool_frame_data("work_history", empty, True) == {}


def test_a_reply_written_home_keeps_its_rows():
    from iron_jarvis.daemon import durable_turns as DT

    row = {"id": "chat:t1", "kind": "chat", "title": "Baker payroll setup", "link": "/chat?thread=t1"}
    frames = [
        ("tool_call", json.dumps({"id": "c1", "name": "work_history", "status": "started"})),
        ("tool_call", json.dumps({"id": "c1", "name": "work_history", "status": "finished", "ok": True,
                                  "data": {"rows": [row]}})),
        ("tool_call", json.dumps({"id": "c2", "name": "work_history", "status": "finished", "ok": False,
                                  "data": {"rows": [{**row, "id": "chat:t2"}]}})),
        ("token", json.dumps({"text": "Three pieces of work."})),
        ("done", json.dumps({"reply": "Three pieces of work."})),
    ]
    line = DT.reply_line("turn-1", frames, "done")
    assert line["workRows"] == [row]
    # Without such a step the line is what it was.
    plain = DT.reply_line("turn-1", frames[3:], "done")
    assert "workRows" not in plain
    from iron_jarvis.daemon.chat_append import LINE_KEYS

    assert "workRows" in LINE_KEYS


# --------------------------------------------------------------------------- #
# 5. another app: the same dates, the same wall
# --------------------------------------------------------------------------- #
def test_the_outside_wall_still_refuses_every_owned_row_with_dates_set(march, monkeypatch):
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.workmap import running
    from iron_jarvis.workmap.connector_tools import LOOKUP_EMPTY, WorkSearchTool

    monkeypatch.setattr(running, "running_block", lambda platform: "")
    # Work under a name still WAITING for the owner's answer: only the wall of
    # another app refuses it (the owner's own turn is told it).
    _chat(march, "Jones invoice follow up", DOWNLOADS, at=_at(3, 20))
    march.wm.refresh(force=True)
    tool = WorkSearchTool(march.platform)
    ctx = ToolContext(workspace=march.tmp, session_id="mcp:app:claude-desktop", agent_run_id="",
                      config=None, event_bus=None, engine=march.engine)

    def ask(args):
        return asyncio.run(tool.execute(args, ctx))

    for args in ({**MARCH}, {"folder": BAKER, **MARCH}, {"query": "Baker payroll", **MARCH},
                 {"folder": BAKER_OTHER, "query": "Baker", **MARCH}):
        text = ask(args).output
        for title in (*BAKER_MARCH, "Smith quarterly report", "Jones invoice follow up"):
            assert title not in text, (args, title)
    assert "Jones invoice follow up" not in ask({"query": "Jones invoice", **MARCH}).output
    # The control: the owner's own turn, naming nobody, is told the Jones row.
    with wall.turn_scope(wall.turn_wall(march.engine, "", "what did we do in March?")):
        mine = asyncio.run(march.tool.execute(dict(MARCH), SimpleNamespace(thread_id="", session_id="chat")))
    assert "Jones invoice follow up" in _titles(mine)
    anywhere = ask(dict(MARCH)).output
    assert "Earlier work in March 2026:" in anywhere and "Price list update" in anywhere
    assert "Baker April close" not in anywhere
    assert ask({"folder": BAKER, **MARCH}).output.endswith(LOOKUP_EMPTY)
    bad = ask({"after": "last March"})
    assert bad.ok is False and bad.error == WT.BAD_DATE
    # The control: the same dates asked by the OWNER, who named Baker, list them.
    with wall.turn_scope(wall.turn_wall(march.engine, "", "what did we do for Baker in March?")):
        owner = asyncio.run(march.tool.execute({"client": "Baker", **MARCH}, SimpleNamespace(
            thread_id="", session_id="chat")))
    assert _titles(owner) == BAKER_MARCH


# --------------------------------------------------------------------------- #
# 6. through the real stream lane, and the Memory page's piece
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app_scene(tmp_path, monkeypatch):
    import iron_jarvis.daemon.chat_turn as CT
    from iron_jarvis.daemon.app import create_app

    monkeypatch.setattr(CT, "GROUNDING_SOFT_S", 60.0)
    app = create_app(str(tmp_path / "home"))
    s = SimpleNamespace(app=app, client=TestClient(app), platform=app.state.platform, tmp=tmp_path)
    s.engine = s.platform.engine
    s.baker = tmp_path / "Baker"
    s.baker.mkdir()
    s.ids = {t: _chat(s, t, str(s.baker), at=_at(3, d)) for t, d in
             (("Baker payroll setup", 4), ("Baker sales tax filing", 17), ("Baker bank feed", 29))}
    _chat(s, "Baker April close", str(s.baker), at=_at(4, 2))
    return s


def _frames(text: str) -> list[tuple[str, dict]]:
    out, event = [], ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event:
            try:
                out.append((event, json.loads(line[5:].strip())))
            except ValueError:
                pass
    return out


def test_a_real_stream_turn_draws_three_march_rows_that_open_their_chats(app_scene, monkeypatch):
    from iron_jarvis.workmap.clients import store as store_mod

    store_mod.create(app_scene.platform, folder=str(app_scene.baker), name="Baker")
    calls = [ToolCall("c1", "work_history", {"client": "Baker", **MARCH})]
    n = {"i": 0}
    told: list[str] = []

    async def fake_stream(*, system, messages, tools=None, **kw):
        for m in messages:
            if (m.get("role") if isinstance(m, dict) else getattr(m, "role", "")) == "tool":
                told.append(str(m.get("content") if isinstance(m, dict) else m.content))
        first = n["i"] == 0
        n["i"] += 1
        resp = (LLMResponse(text="", tool_calls=calls, usage={"input_tokens": 3, "output_tokens": 1}) if first
                else LLMResponse(text="Three pieces of work in March.", usage={"input_tokens": 3, "output_tokens": 1}))
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    async def fake_complete(*, system, messages, tools=None, **kw):
        from iron_jarvis.providers.router import RouteResult

        return RouteResult(LLMResponse(text="ok", usage={"input_tokens": 1, "output_tokens": 1}), "mock", "mock")

    monkeypatch.setattr(app_scene.platform.router, "stream", fake_stream)
    monkeypatch.setattr(app_scene.platform.router, "complete", fake_complete)
    body = {"messages": [{"role": "user", "content": "what did we do for Baker in March?"}],
            "auto_tools": True}
    r = app_scene.client.post("/chat/stream", json=body)
    assert r.status_code == 200, r.text
    assert any("3 pieces of work in March 2026, for Baker:" in t for t in told), told
    finished = [d for e, d in _frames(r.text)
                if e == "tool_call" and d.get("name") == "work_history" and d.get("status") == "finished"]
    assert finished, "no finished work_history step"
    rows = finished[0]["data"]["rows"]
    assert [row["title"] for row in rows] == ["Baker bank feed", "Baker sales tax filing", "Baker payroll setup"]
    for row in rows:
        assert row["link"] == "/chat?thread=" + app_scene.ids[row["title"]]
        # ... and that link's chat is the piece: its own thread, by id.
        got = app_scene.client.get(f"/chat/threads/{row['link'].split('=', 1)[1]}")
        assert got.status_code == 200 and got.json().get("title") == row["title"]


def test_the_memory_page_finds_a_piece_by_its_id(app_scene):
    wm = work_map(app_scene.engine)
    wm.refresh(force=True)
    pid = "chat:" + app_scene.ids["Baker payroll setup"]
    got = app_scene.client.get("/work-record/piece", params={"id": pid})
    assert got.status_code == 200 and got.json()["folder"] == str(app_scene.baker)
    assert got.json()["name"] == "Baker"
    gone = app_scene.client.get("/work-record/piece", params={"id": "chat:nothing-here"})
    assert gone.status_code == 404 and gone.json()["detail"] == "That piece of work is not on record any more."
    assert app_scene.client.get("/work-record/piece").status_code == 400


def test_the_13a_answers_are_unchanged_without_a_filter(march):
    """No filter: the 13-a reading (the turn's own folder; no count line)."""
    got = asyncio.run(_ask(march, folder=BAKER, words="what did we do here?"))
    assert got.output.splitlines()[1].startswith("- [chat")
    assert "Baker April close" in _titles(got) and "Bank feed review" not in _titles(got)
