"""Intelligence plan, release 5: "What did you check?" answers from the record.

``checks_list`` is one read-only tool. Called through the real registry and
its gate, it reads the checks of the chat it was called in and no other
chat's; it changes nothing and checks nothing again. It carries the house
rules of a new tool: plain words everywhere, read-only on the ledger, armed
by its words, found by the lookup, allowed without a card, and refused to a
task another computer started.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import mission
from iron_jarvis.capability_search import index as CI
from iron_jarvis.checks import record as R
from iron_jarvis.checks import tool as CT
from iron_jarvis.checks.record import Check, CheckRecord
from iron_jarvis.checks.tool import ChecksListTool
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.core.trust import REMOTE_RUN_DENY, REMOTE_RUN_KEEP, remote_run_keeps
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools import autoselect
from iron_jarvis.tools.base import Reversibility, ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_wave3_chat_lanes_v1311 import _post_turn, _scripted_router

SHEET = [
    Check(R.FILE_OPENS, R.PASSED, "opens", file="summary.xlsx"),
    Check(R.SHEET_ERRORS, R.FAILED, "C7 on Summary holds #DIV/0!", file="summary.xlsx"),
    Check(R.SHEET_TOTALS, R.PASSED, "2 totals add up", file="summary.xlsx"),
    Check(R.REQUIRED_FILE, R.PASSED, "it is on disk (12 KB)", file="summary.xlsx"),
]
OTHER = [Check(R.FILE_OPENS, R.FAILED, "it is empty", file="payroll-private.xlsx")]


def _ctx(platform, tmp_path, **kw) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, agent_run_id="r-1", config=platform.config, event_bus=platform.event_bus,
        engine=platform.engine, **{"session_id": "chat", **kw},
    )


async def _ask(platform, ctx, **args):
    return await platform.registry.invoke(CT.TOOL_NAME, args, ctx, PermissionEngine(dict(platform.config.permissions)))


def _count(engine) -> int:
    with session_scope(engine) as db:
        return len(list(db.exec(select(CheckRecord))))


# --------------------------------------------------------------------------- #
# Its own chat only
# --------------------------------------------------------------------------- #
async def test_it_answers_from_the_record_for_the_chat_it_was_asked_in(platform, tmp_path):
    R.write(platform.engine, SHEET, thread_id="chat_mine", turn_id="turn_1")
    R.write(platform.engine, OTHER, thread_id="chat_other", turn_id="turn_2")
    before = _count(platform.engine)

    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_mine", turn_id="turn_9"))
    assert res.ok, res.error
    assert "Checked by Iron Jarvis: the spreadsheet opens, 2 totals add up." in res.output
    assert "- “summary.xlsx”: C7 on Summary holds #DIV/0! (Problem, by Iron Jarvis)" in res.output
    assert "- Required file “summary.xlsx”: it is on disk (12 KB) (Checked, by Iron Jarvis)" in res.output
    # Who checked is never "by rule" (the product has a Rules page), and nothing says a reply's claims were checked.
    assert "by rule" not in res.output and "matches what it did" not in res.output and "in its record" not in res.output
    assert [c["check"] for c in res.data["checks"]] == [R.SHEET_ERRORS, R.FILE_OPENS, R.SHEET_TOTALS, R.REQUIRED_FILE]
    assert "payroll-private" not in res.output, "another chat's check was read"
    assert [c["label"] for c in res.data["checks"]][0] == "“summary.xlsx”"
    assert all("payroll" not in str(c) for c in res.data["checks"])

    theirs = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_other"))
    assert "“payroll-private.xlsx”: it is empty" in theirs.output and "summary.xlsx" not in theirs.output
    # It reads; it never writes or checks again.
    assert _count(platform.engine) == before


async def test_what_is_worth_a_look_is_answered_in_its_own_words_never_as_a_problem(platform, tmp_path):
    """"Did you check the totals?": what Iron Jarvis added up itself and
    found different is said as worth a look, with both figures, between the
    problems and what was not checked."""
    R.write(platform.engine, [
        Check(R.FILE_OPENS, R.PASSED, "opens", file="summary.xlsx"),
        Check(R.SHEET_TOTALS, R.NOT_CHECKED, "the check ran out of time", file="summary.xlsx"),
        Check(R.SHEET_TOTALS, R.FAILED, "B4 says 650 and the cells above it add up to 600", file="summary.xlsx"),
        Check(R.SHEET_ERRORS, R.FAILED, "C7 holds #DIV/0!", file="summary.xlsx"),
    ], thread_id="chat_look", turn_id="turn_look")
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_look"))
    assert res.ok, res.error
    lines = res.output.splitlines()
    assert lines[1].endswith(
        "Checked by Iron Jarvis: the spreadsheet opens. Problem in the spreadsheet: C7 holds #DIV/0!. "
        "Worth a look: B4 says 650 and the cells above it add up to 600. "
        "Not checked: the spreadsheet (the check ran out of time).")
    assert lines[2:6] == [
        "- “summary.xlsx”: C7 holds #DIV/0! (Problem, by Iron Jarvis)",
        "- “summary.xlsx”: B4 says 650 and the cells above it add up to 600 (Worth a look, by Iron Jarvis)",
        "- “summary.xlsx”: the check ran out of time (Not checked, by Iron Jarvis)",
        "- “summary.xlsx”: opens (Checked, by Iron Jarvis)",
    ]
    assert [c["result"] for c in res.data["checks"]] == ["failed", "look", "not_checked", "passed"]


async def test_a_chat_with_no_checks_says_so_and_an_unsaved_chat_reads_only_its_own_reply(platform, tmp_path):
    R.write(platform.engine, OTHER, thread_id="chat_other", turn_id="turn_2")
    R.write(platform.engine, SHEET, turn_id="turn_unsaved")          # a new chat: no chat id yet
    R.write(platform.engine, OTHER, session_id="some_run")            # a run's rows: no reply id at all
    empty = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_new"))
    assert empty.ok and empty.output == CT.NOTHING and empty.data == {"checks": [], "more": 0, "older": False}
    # No chat id and no reply id: nothing, never every row with no chat.
    assert (await _ask(platform, _ctx(platform, tmp_path))).output == CT.NOTHING
    mine = await _ask(platform, _ctx(platform, tmp_path, turn_id="turn_unsaved"))
    assert "summary.xlsx" in mine.output and "payroll" not in mine.output
    # What the tool says of itself when there is nothing: no word about a reply's claims being checked.
    assert CT.NOTHING == (
        "Iron Jarvis's record holds no check for this chat yet. When a reply makes or changes a file, Iron Jarvis "
        "looks at what that file holds when the reply ends.")
    assert "it says it saved" not in ChecksListTool().description and "in its record" not in ChecksListTool().description


async def test_an_unsaved_chat_reads_only_rows_that_have_no_chat_yet(platform, tmp_path):
    """Decision 11. A turn's id is the caller's own choice. A chat that is
    not saved yet asked "what did you check?" under an id a SAVED chat had
    used, and was handed that chat's file names. It reads only the rows of
    that id that have no chat yet (its own reply's)."""
    R.write(platform.engine, OTHER, thread_id="chat_other", turn_id="one-id")       # another chat, the same id
    asked = await _ask(platform, _ctx(platform, tmp_path, turn_id="one-id"))
    assert asked.ok and asked.output == CT.NOTHING and "payroll" not in asked.output
    R.write(platform.engine, SHEET[:1], turn_id="one-id")                           # its own reply's row: no chat yet
    mine = await _ask(platform, _ctx(platform, tmp_path, turn_id="one-id"))
    assert "“summary.xlsx”: opens" in mine.output and "payroll" not in mine.output
    assert [c["label"] for c in mine.data["checks"]] == ["“summary.xlsx”"]
    # ... and the saved chat still reads its own, and only its own.
    theirs = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_other", turn_id="one-id"))
    assert "payroll-private" in theirs.output and "summary.xlsx" not in theirs.output


async def test_an_unsaved_local_only_replys_rows_are_read_only_by_a_reply_that_is_local_only(platform, tmp_path):
    """A new chat's first reply, sent Local only, wrote its rows kept home.
    The same turn asked again with Local only off reads none of them."""
    from iron_jarvis.providers import egress

    R.write(platform.engine, SHEET[:1], turn_id="turn_new_home", kept_home=True)
    with egress.local_only_scope(True):
        inside = await _ask(platform, _ctx(platform, tmp_path, turn_id="turn_new_home"))
    assert "summary.xlsx" in inside.output
    outside = await _ask(platform, _ctx(platform, tmp_path, turn_id="turn_new_home"))
    assert outside.output == CT.NOTHING


async def test_a_run_is_read_only_when_it_was_started_from_the_calling_chat(platform, tmp_path):
    """S5: a run with NO chat on its rows was read from any chat (and this
    test said so, with a file called payroll-private.xlsx). A run is answered
    for only when the record says it belongs to the calling chat; every other
    run gets the same refusal, which says where to look."""
    step = [Check(R.STEP_CHECK, R.NOT_CHECKED, "its check did not run", by="Sonnet 4.5", step="2")]
    R.write(platform.engine, step, session_id="run_mine", thread_id="chat_mine")
    R.write(platform.engine, OTHER, session_id="run_theirs", thread_id="chat_other")
    R.write(platform.engine, OTHER, session_id="run_nobody")
    ctx = _ctx(platform, tmp_path, thread_id="chat_mine")

    mine = await _ask(platform, ctx, run="run_mine")
    assert "- Step 2: its check did not run (Not checked, by Sonnet 4.5)" in mine.output
    for other in ("run_theirs", "run_nobody", "no_such_run"):
        got = await _ask(platform, ctx, run=other)
        assert not got.ok and got.error == CT.OTHER_RUN, (other, got.output, got.error)
        assert "payroll" not in (got.output or "") + (got.error or "") + str(got.data or ""), other
    assert CT.OTHER_RUN == ("That run was not started from this chat, so its checks are not read here. "
                            "Open that run to see what was checked.")
    # A chat not saved yet has no id: it owns no run, not even one that has none either.
    unsaved = _ctx(platform, tmp_path, turn_id="turn_new")
    for other in ("run_nobody", "run_mine"):
        got = await _ask(platform, unsaved, run=other)
        assert not got.ok and got.error == CT.OTHER_RUN and "payroll" not in str(got.data or "")
    # The chat's own list holds its run's checks too (the run was started from it).
    assert "Step 2" in (await _ask(platform, ctx)).output


async def test_the_newest_replies_are_answered_for_whole_and_older_ones_are_said_to_be_left_out(platform, tmp_path):
    """N4: the answer took a chat's newest 60 ROWS, so an older reply's
    checks fell out from the middle of that reply with nothing said."""
    eng = platform.engine
    for n in range(8):          # eight replies of ten checks each, oldest first
        R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file=f"reply{n}-file{i}.txt") for i in range(10)],
                thread_id="chat_long", turn_id=f"turn_{n}")
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_long"))
    lines = res.output.splitlines()
    assert lines[0] == "What Iron Jarvis checked (its own record, newest reply first):"
    heads = [i for i, text in enumerate(lines) if text.startswith("A reply (")]
    assert len(heads) == 6, "whole replies only: six of ten checks fit sixty"
    # Newest reply first, and every reply that is answered for is whole.
    for at, n in zip(heads, (7, 6, 5, 4, 3, 2)):
        block = "\n".join(lines[at:at + 11])
        assert all(f"reply{n}-file{i}.txt" in block for i in range(10)), (n, block)
    assert "reply1-" not in res.output and "reply0-" not in res.output
    assert lines[-1] == CT.OLDER == "Older checks in this chat are not listed here."
    assert res.data["older"] is True
    # A short chat says nothing about older checks.
    R.write(eng, SHEET, thread_id="chat_short", turn_id="turn_s")
    short = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_short"))
    assert CT.OLDER not in short.output and short.data["older"] is False


def test_whole_replies_newest_first_and_a_read_that_hit_its_limit_drops_its_partial_oldest():
    def row(turn, i):
        return {"turn_id": turn, "session_id": "", "run_id": "", "file": f"{turn}-{i}", "check": R.FILE_OPENS,
                "result": R.PASSED, "detail": "opens", "at": ""}

    newest_first = [row("c", 1), row("c", 0), row("b", 1), row("b", 0), row("a", 1), row("a", 0)]
    kept, older = CT.newest_replies(newest_first, limit=4, read_limit=100)
    assert [r["file"] for r in kept] == ["c-0", "c-1", "b-0", "b-1"] and older is True
    kept, older = CT.newest_replies(newest_first, limit=60, read_limit=100)
    assert [r["file"] for r in kept] == ["c-0", "c-1", "b-0", "b-1", "a-0", "a-1"] and older is False
    # The newest reply is always whole, even past the limit.
    kept, older = CT.newest_replies(newest_first, limit=1, read_limit=100)
    assert [r["file"] for r in kept] == ["c-0", "c-1"] and older is True
    # The read itself stopped at its limit: its oldest reply may be half there, so it is left out and said.
    kept, older = CT.newest_replies(newest_first[:5], limit=60, read_limit=5)
    assert [r["file"] for r in kept] == ["c-0", "c-1", "b-0", "b-1"] and older is True
    assert CT.newest_replies([], limit=60) == ([], False)


async def test_called_inside_a_run_it_reads_that_runs_checks(platform, tmp_path):
    R.write(platform.engine, SHEET, session_id="session_run")
    R.write(platform.engine, OTHER, thread_id="chat_other", turn_id="turn_2")
    res = await _ask(platform, _ctx(platform, tmp_path, session_id="session_run"))
    assert "summary.xlsx" in res.output and "payroll" not in res.output
    assert res.output.splitlines()[1].startswith("A run (")


# --------------------------------------------------------------------------- #
# A new tool's house rules
# --------------------------------------------------------------------------- #
async def test_it_is_read_only_allowed_without_a_card_and_recorded_as_a_read(platform, tmp_path):
    tool = platform.registry.get(CT.TOOL_NAME)
    assert tool is not None and tool.reversibility is Reversibility.READONLY
    assert platform.config.permissions[CT.TOOL_NAME] == "allow"
    assert set(tool.input_schema["properties"]) == {"run"}, "an argument could name another chat"
    R.write(platform.engine, SHEET, thread_id="chat_mine", turn_id="turn_1")
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_mine", session_id="chat"))
    assert res.ok
    with session_scope(platform.engine) as db:
        (inv,) = [i for i in db.exec(select(ToolInvocation)) if i.tool == CT.TOOL_NAME]
    assert inv.ok and (inv.reversibility or "").lower() == Reversibility.READONLY.value


def test_it_reads_in_plain_words_everywhere():
    assert mission.card_words(CT.TOOL_NAME) == "look up what Iron Jarvis checked"
    assert mission.step_line_words(CT.TOOL_NAME) == (
        "Looked up what Iron Jarvis checked", "Looking up what Iron Jarvis checked")
    from iron_jarvis.daemon.chat_turn import _READS_ONLY_TOOLS

    assert CT.TOOL_NAME in _READS_ONLY_TOOLS


def _arms(said: str) -> bool:
    return any(CT.TOOL_NAME in tools and rx.search(said) for rx, tools in autoselect._RULES)


#: THE HELD TABLE, written before the rule was tightened (the third review:
#: "did you check the weather for tomorrow?" armed it). A question about
#: Iron Jarvis's checks of its WORK arms the tool; a request to do something
#: now, and a question about anything else somebody checked, does not.
SHOULD_ARM = [
    "what did you check",
    "What did you check?",
    "what have you checked so far",
    "what did Jarvis check",
    "what was checked",
    "what has been checked on this file",
    "which files did you check",
    "did you check the totals?",
    "did you check the numbers",
    "did you actually check the spreadsheet",
    "have you checked the formulas",
    "did you double-check the figures",
    "did you check the grand total",
    "Did you actually check that it adds up",
    "did you check if the file opens",
    "did you check it?",
    "How do I know the spreadsheet is right?",
    "how do you know it is correct",
    "how do we know the totals add up",
    "how do I know that the report is accurate",
    "what could you not check?",
    "what did you not check",
    "what was not checked",
]
SHOULD_NOT_ARM = [
    # The third review's own.
    "did you check the weather for tomorrow?",
    "did it check out ok at the hotel",
    "have you checked the mail",
    "what did you check in to the repo",
    "how do we know the new hire is right for us",
    # A request to do something now.
    "check my email",
    "can you check the contract for typos",
    "please check whether the server is up",
    "please check the totals again",
    "I need to check the totals",
    "write a check for the rent",
    # Something else somebody checked, or knows.
    "did you check with Ann about lunch",
    "did you check the calendar for Friday",
    "what did you check out from the library",
    "have you checked my inbox",
    "did you check the news",
    "did you check on the kids",
    "did you check the price of flights",
    "did it check the box",
    "did you check that restaurant",
    "how do I know the restaurant is right for the party",
    "how do I know it is installed",
    "how do you know that",
    # Right FOR somebody is not right; a thing that is not the work; somebody else's check.
    "how do you know it is right for us",
    "how do I know the address is right",
    "how do we know the new hire is correct",
    "did he check the report",
    "did they check the figures at the bank",
    "what should I check before filing",
    "what did we decide about the fee",
]


@pytest.mark.parametrize("said", SHOULD_ARM)
def test_plain_words_arm_it(said):
    assert CT.TOOL_NAME in autoselect.AUTO_SAFE_TOOLS
    assert _arms(said), said


@pytest.mark.parametrize("said", SHOULD_NOT_ARM)
def test_a_request_to_do_something_now_or_a_question_about_something_else_does_not_arm_it(said):
    assert not _arms(said), said


def test_the_held_table_is_two_lists_with_nothing_in_both():
    assert len(SHOULD_ARM) >= 20 and len(SHOULD_NOT_ARM) >= 20
    assert not set(s.lower() for s in SHOULD_ARM) & set(s.lower() for s in SHOULD_NOT_ARM)


@pytest.mark.parametrize("query", [
    "what did you check", "did the numbers add up", "what could you not check",
    # Found only through its everyday phrasings (its own description has none of these words).
    "who looked over this work", "show me the proof it is right",
])
def test_the_lookup_finds_it_by_plain_words(platform, query):
    assert CT.TOOL_NAME in [e.id for e in CI.search(platform, query, 5)]


def test_a_task_from_another_computer_may_not_call_it():
    """Decided: NOT on the keep list. A run another computer's task started
    posts its answer back to that computer, and this tool reads this
    computer's record of checks (file names, findings)."""
    assert CT.TOOL_NAME not in REMOTE_RUN_KEEP
    assert CT.TOOL_NAME not in REMOTE_RUN_DENY          # refused by the keep list itself
    assert remote_run_keeps(CT.TOOL_NAME) is False


# --------------------------------------------------------------------------- #
# Asked in a chat, through the real lane
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ("post", "stream"))
def test_asked_in_a_chat_the_model_is_handed_that_chats_checks(tmp_path, monkeypatch, lane):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        platform = client.app.state.platform
        R.write(platform.engine, SHEET, thread_id="chat_mine", turn_id="turn_1")
        R.write(platform.engine, OTHER, thread_id="chat_other", turn_id="turn_2")
        seen = _scripted_router(platform, monkeypatch, [[ToolCall(id="c1", name=CT.TOOL_NAME, arguments={})]],
                                "I checked that it opens and that the totals add up; one cell holds an error.")
        body, _ = _post_turn(client, lane, {
            "messages": [{"role": "user", "content": "What did you check?"}],
            "thread_id": "chat_mine", "turn_id": "turn_ask",
            # Nothing is ticked: the words alone arm it (Auto tools, as the page sends it).
            "auto_tools": True,
        })
        assert body["auto_armed"] == [CT.TOOL_NAME], body.get("auto_armed")
        assert CT.TOOL_NAME in body["tools_used"], body.get("reply")
        given = "\n".join(str(getattr(m, "content", "")) for m in seen["messages"][-1])
        assert "C7 on Summary holds #DIV/0!" in given and "payroll-private" not in given
        # A question about the checks made no file: no line under this reply.
        assert "checked" not in body
