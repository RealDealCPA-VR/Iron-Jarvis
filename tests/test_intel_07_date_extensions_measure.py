"""The intelligence plan, release 7: today's date in every kind of work, an
extension's tools that hold still, and the numbers read back from the app's
own records.

* ONE helper (``core/today``) says the date to a plain chat reply (beside the
  newest message), an agent run (so a mission's steps, a team's members and a
  workflow's model step), a split task's plan and a coding session: the date
  only, in the owner's local time, from an injected clock;
* the tools of an extension switched on for a chat stay in the chat's list,
  in their place, across a restart, while the extension is still switched on;
  its own setting still decides every call;
* ``scripts/measure_tool_definitions.py --split`` prints, read-only, the
  share reused on the second and later replies and the time to the first
  word, before a moment and from it on.

Real app, real router, real registry, a stand-in model. Nothing here sleeps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from iron_jarvis.context import prefix as P
from iron_jarvis.core import today as today_mod
from iron_jarvis.core.db import open_db, session_scope
from iron_jarvis.daemon import chat_kept, chat_turn
from iron_jarvis.mcp import reach
from tests.test_intel_06_agent_run_used import PLAIN, _run, _write, clock  # noqa: F401  (clock: a fixture)
from tests.test_intel_06_measure_script import NOW_ARG, M, _digest, _record, _row
from tests.test_intel_07_stable_prefix import Chat, once_of
from tests.test_mcp_app_reach_v1577 import APP, SimpleD, _connect

DAY = "2031-02-03"
LINE = f"Today's date is {DAY}."


@pytest.fixture(autouse=True)
def _the_date(monkeypatch):
    monkeypatch.setattr(today_mod, "now", lambda: datetime(2031, 2, 3, 23, 59))


# --------------------------------------------------------------------------- #
# The one helper
# --------------------------------------------------------------------------- #
def test_the_date_is_the_owners_local_day_and_never_the_time(monkeypatch):
    assert today_mod.today_iso() == DAY and today_mod.today_line() == LINE
    assert today_mod.today_block() == "\n\n" + LINE
    # The LOCAL day: one minute before midnight in a zone behind UTC is still today.
    behind = timezone(-(datetime(2031, 2, 4, 5) - datetime(2031, 2, 3, 23)))
    monkeypatch.setattr(today_mod, "now", lambda: datetime(2031, 2, 3, 23, 59, tzinfo=behind))
    assert today_mod.today_iso() == DAY
    # A minute later it is tomorrow; nothing in the line says the time.
    monkeypatch.setattr(today_mod, "now", lambda: datetime(2031, 2, 4, 0, 0, 30))
    assert today_mod.today_line() == "Today's date is 2031-02-04."
    # A clock that breaks costs the line, never the reply.
    monkeypatch.setattr(today_mod, "now", lambda: 1 / 0)
    assert today_mod.today_iso() == "" and today_mod.today_line() == "" and today_mod.today_block() == ""
    assert today_mod.today_line("2030-01-01") == "Today's date is 2030-01-01."


def test_the_real_clock_answers_in_the_pcs_own_time_zone():
    now = today_mod._local_now()
    assert now.tzinfo is not None and abs((now - datetime.now(timezone.utc)).total_seconds()) < 60


# --------------------------------------------------------------------------- #
# Every kind of work
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ("/chat", "/chat/stream"))
def test_a_chat_open_across_midnight_carries_the_new_date_and_rereads_nothing(tmp_path, lane, monkeypatch):
    chat = Chat(tmp_path / "home", lane)
    chat.say("hello there, good morning")
    before = chat.say("what is due this week?")
    assert LINE in once_of(before)
    monkeypatch.setattr(today_mod, "now", lambda: datetime(2031, 2, 4, 0, 5))
    after = chat.say("and next week?")
    assert "Today's date is 2031-02-04." in once_of(after) and LINE not in once_of(after)
    # The date rides beside the newest message, so nothing sent before it moved.
    assert after["system"] == before["system"] and after["tools"] == before["tools"]
    assert after["messages"][:2] == before["messages"][:2]
    assert after["messages"][2] == ("user", "what is due this week?")
    assert "Today's date" not in after["system"]


async def test_an_agent_run_knows_todays_date(platform, orchestrator, clock):  # noqa: F811
    _row_, _steps, inner, _session = await _run(platform, orchestrator, clock, [_write(PLAIN), "Done."], usage=PLAIN)
    system = inner.calls[0]["system"]
    env = system[system.index("# Environment"):]
    assert f"- {LINE}\n" in env.split("\n\n")[0], "the date is in the run's Environment section"
    assert all(c["system"].count(LINE) == 1 for c in inner.calls), "once per run, the same on every step"


async def test_a_split_tasks_plan_is_made_knowing_the_date(platform, orchestrator, clock):  # noqa: F811
    from tests.test_intel_04_not_checked_on_the_result_line import PLAN, TASK, WRITE_CALL

    script = [PLAN, WRITE_CALL, "Wrote hello.txt with the greeting.",
              "A greeting file named hello.txt was written.", '{"pass": true, "reason": "it says so"}',
              "hello.txt was written and the work was summarized."]
    _r, _s, inner, _ = await _run(platform, orchestrator, clock, script, task=TASK, provider="local-r7", tool_use=False)
    plan_call = inner.calls[0]
    assert plan_call["messages"][-1][1].endswith("\n\n" + LINE), plan_call["messages"][-1][1][-200:]


@pytest.mark.parametrize("lane", ("stream", "post"))
async def test_a_coding_session_says_the_date_from_the_same_clock(tmp_path, monkeypatch, lane):
    from iron_jarvis.platform import build_platform
    from tests._fakes.harness_lane import body, no_finish_check, post, stream, wire

    platform = build_platform(str(tmp_path / "home"))  # noqa: F811
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("A = 1\n", encoding="utf-8")
    no_finish_check(ws)
    model = wire(platform, monkeypatch, ["A is 1."])
    await (stream(platform, body(ws, "what is A?")) if lane == "stream" else post(platform, body(ws, "what is A?")))
    system = model.calls[0]["system"]
    assert system.count(LINE) == 1, "a coding session keeps its own line, in its instructions"
    said = "".join(str(getattr(m, "content", "") or "") for m in model.calls[0]["messages"])
    assert P.ONCE_TOP not in said, "a coding session's messages are not touched"


# --------------------------------------------------------------------------- #
# An extension's tools hold still
# --------------------------------------------------------------------------- #
def _app_names(call) -> list[str]:
    return [n for n in call["names"] if n.startswith(f"mcp__{APP}__")]


@pytest.mark.parametrize("lane", ("/chat", "/chat/stream"))
def test_a_switched_on_extensions_tools_stay_in_place_across_a_restart(tmp_path, lane):
    chat = Chat(tmp_path / "home", lane)
    _connect(chat.app.state.platform)
    first = chat.say("record a customer payment against the invoice", connectors=[APP])
    mine = _app_names(first)
    assert len(mine) == chat_turn._MAX_CONNECTOR_TOOLS, "a large extension: the words picked a capped set"
    reach.RECENT.clear()
    chat.open()                      # a restart: nothing in memory survives
    _connect(chat.app.state.platform)
    second = chat.say("now list every thing we have on file", connectors=[APP])
    assert _app_names(second) == mine, "the extension's tools changed with the message"
    assert second["tools"] == first["tools"], "the tool list is not the bytes the reply before sent"
    assert reach.FINDER in second["names"], "a tool with no room is still found with the extension's own finder"
    # Switched off for the chat: gone on the very next reply.
    third = chat.say("thanks, that is all for now")
    assert _app_names(third) == []
    # Switched on again later: its tools are picked by the message's words
    # again, and the ones the chat was sent before keep their old order.
    fourth = _app_names(chat.say("and the invoice again please", connectors=[APP]))
    assert len(fourth) == chat_turn._MAX_CONNECTOR_TOOLS
    again = [n for n in fourth if n in mine]
    assert again and again == [n for n in mine if n in fourth]


def test_a_kept_extension_tool_joins_at_the_tier_its_extension_has_in_this_reply(platform):  # noqa: F811
    _connect(platform, quarantined=("record_payment",))
    d = SimpleD(platform)
    pay, bill, inv = (f"mcp__{APP}__{n}" for n in ("record_payment", "enter_bill", "get_invoice"))
    kept = [bill, pay, "mcp__gone__tool", f"mcp__{APP}__no_such_tool", "read_file"]
    # Its extension is not part of this reply: nothing joins.
    assert reach.widen(d, None, ["read_file"], [], [], can_ask=True, kept=kept) == (["read_file"], [], [])
    # Its extension is switched on (granted): the kept tool joins as that
    # switch would have armed it; a held-back one joins asking.
    armed, auto, ask = reach.widen(d, None, [inv], [], [], can_ask=True, kept=kept)
    assert bill in armed and bill in auto and "mcp__gone__tool" not in armed
    assert f"mcp__{APP}__no_such_tool" not in armed + ask and "read_file" not in armed
    # Its extension is only ASKED in this reply: it joins asking where someone
    # can be asked, and not at all where nobody can.
    armed, _a, ask = reach.widen(d, None, [], [], [inv], can_ask=True, kept=kept)
    assert bill in ask and bill not in armed
    armed, _a, ask = reach.widen(d, None, [], [], [inv], can_ask=False, kept=kept)
    assert bill not in armed and bill not in ask and pay not in armed
    # Nothing joins a reply at low trust.
    assert reach.widen(d, None, [inv], [], [], can_ask=True, low_trust=True, kept=kept) == ([inv], [], [])
    # And a held-back tool still asks before each use, whatever list it is on.
    from iron_jarvis.tools.permissions import asks_each_use

    assert asks_each_use(platform.registry.get(pay))


def test_keep_conn_keeps_only_what_is_still_switched_on_and_registered(platform):  # noqa: F811
    from iron_jarvis.daemon import chat_prefix as CP

    _connect(platform)
    d = SimpleD(platform)
    a, b, c, x = (f"mcp__{APP}__{n}" for n in ("get_invoice", "find_customer", "enter_bill", "no_such_tool"))
    kept = chat_kept.Kept(tools=[c, x, "mcp__other__t", a, "read_file"], held={c, x, "mcp__other__t", a, "read_file"})
    turn = CP.StableTurn(active=True, kept=kept)
    assert turn.keep_conn(d, [], 24) == [], "the extension is switched off: nothing of it stays"
    assert turn.keep_conn(d, [b, a], 24) == [c, a], "kept first, in their order; never more than the words picked"
    assert turn.keep_conn(d, [b, a, f"mcp__{APP}__apply_credit"], 24) == [c, a, b]
    assert turn.keep_conn(d, [b], 1) == [c], "the cap holds"
    assert CP.StableTurn(active=True, kept=kept, low_trust=True).keep_conn(d, [b], 24) == [b]
    assert CP.StableTurn(active=False, kept=kept).keep_conn(d, [b], 24) == [b]
    not_held = chat_kept.Kept(tools=[c, a], held=set())
    assert CP.StableTurn(active=True, kept=not_held).keep_conn(d, [b], 24) == [b]
    # A tool that now asks before each use (held back) lost its standing
    # approval: it does not stay; the words of a message may still pick it.
    platform.registry.get(c).quarantined = True
    assert turn.keep_conn(d, [b, a], 24) == [a, b]
    assert turn.keep_conn(d, [c, b], 24) == [a, c], "picked by this message's own words: it is the words' pick"


# --------------------------------------------------------------------------- #
# The numbers, read back
# --------------------------------------------------------------------------- #
@pytest.fixture()
def two_weeks(tmp_path) -> Path:
    """One chat before the new version and one after, on the real schema."""
    path = tmp_path / "home" / "ironjarvis.db"
    path.parent.mkdir()
    engine = open_db(str(path))
    with session_scope(engine) as db:
        # Before (48 to 46 hours ago): little is reused, the first word is slow.
        for i, (read, first_ms) in enumerate(((0, 2_000), (600, 9_000), (800, 11_000))):
            _row(db, thread="chat_before", hours_ago=48 - i, record=_record(100 * (i + 1), cache_input=6_000),
                 cache_read=read, first_ms=first_ms)
        # After (6 to 4 hours ago): most is reused, the first word is quick.
        for i, (read, first_ms) in enumerate(((0, 2_000), (5_400, 3_000), (5_700, 2_000))):
            _row(db, thread="chat_after", hours_ago=6 - i, record=_record(100 * (i + 1), cache_input=6_000),
                 cache_read=read, first_ms=first_ms)
        # A reply whose provider reported nothing is not a zero.
        _row(db, thread="chat_after", hours_ago=3, record=_record(400), cache_read=None)
        db.commit()
    engine.dispose()
    return path


def test_the_script_reads_reuse_and_the_first_word_before_and_after(two_weeks, capsys):
    before = _digest(two_weeks)
    code = M.main(["--db", str(two_weeks), "--now", NOW_ARG, "--split", "2026-11-25T12:00:00"])
    out = capsys.readouterr().out
    assert code == 0 and "Do replies start sooner" in out and "split at 2026-11-25 12:00:00 UTC" in out
    lines = out[out.index("Do replies start sooner"):].splitlines()
    summary = [ln.strip() for ln in lines if ln.strip().startswith("second and later replies")]
    # Replies 2 and 3 of each chat: (600 + 800) of 12,000 before; (5,400 + 5,700) of 12,000 after.
    assert summary[0] == "second and later replies (2): reused 11.7%, first word after 10.0 s"
    assert summary[1] == "second and later replies (3): reused 92.5%, first word after 2.5 s"
    assert "before: 3 replies in 1 chats" in out and "from it on: 4 replies in 1 chats" in out
    # By reply number, with the reply nobody reported on left out of the share.
    table = [ln.split() for ln in lines if ln.split()[:1] in (["1"], ["2"], ["3"], ["4"])]
    assert ["2", "1", "6,000", "10.0%", "9.0"] in table and ["2", "1", "6,000", "90.0%", "3.0"] in table
    assert ["4", "1", "6,000", "not", "reported", "-"] in table
    assert _digest(two_weeks) == before, "it wrote to the database"
    assert sorted(p.name for p in two_weeks.parent.iterdir()) == ["ironjarvis.db"], "it left a file beside it"


def test_a_bad_split_ends_in_one_sentence_before_anything_is_opened(two_weeks, capsys):
    before = _digest(two_weeks)
    code = M.main(["--db", str(two_weeks), "--now", NOW_ARG, "--split", "next tuesday"])
    out, err = capsys.readouterr()
    assert code == 2 and out == ""
    assert err.strip() == M.BAD_SPLIT.format(value="next tuesday") and "Traceback" not in err
    assert _digest(two_weeks) == before
    assert sorted(p.name for p in two_weeks.parent.iterdir()) == ["ironjarvis.db"]


def test_the_split_report_keeps_the_scripts_own_rules(two_weeks, capsys):
    """One reader for both reports: a labelled row is not a reply, and a
    record of a shape the script does not know is left out and counted,
    never read as zeros."""
    engine = open_db(str(two_weeks))
    with session_scope(engine) as db:
        _row(db, thread="chat_after", hours_ago=2, record=_record(900, cache_input=6_000), cache_read=5_999,
             first_ms=99_000, label="A summary of the conversation")
        newer = _record(50, cache_input=6_000)
        newer["v"] = 99
        _row(db, thread="chat_after", hours_ago=1, record=newer, cache_read=1, first_ms=88_000)
        db.commit()
    engine.dispose()
    code = M.main(["--db", str(two_weeks), "--now", NOW_ARG, "--split", "2026-11-25T12:00:00"])
    out = capsys.readouterr().out
    assert code == 0
    report = out[out.index("Do replies start sooner"):]
    assert "from it on: 4 replies in 1 chats" in report, "a labelled row or an unknown record was read as a reply"
    assert "second and later replies (3): reused 92.5%, first word after 2.5 s" in report
    assert report.count("1 record of a shape this script does not know") == 1


def test_without_a_split_the_script_prints_what_it_always_did(two_weeks, capsys):
    M.main(["--db", str(two_weeks), "--now", NOW_ARG])
    assert "Do replies start sooner" not in capsys.readouterr().out
    M.main(["--db", str(two_weeks), "--now", NOW_ARG, "--days", "1", "--split", "2026-11-25T12:00:00+00:00"])
    out = capsys.readouterr().out
    assert "before: 0 replies in 0 chats" in out and "from it on: 4 replies in 1 chats" in out
