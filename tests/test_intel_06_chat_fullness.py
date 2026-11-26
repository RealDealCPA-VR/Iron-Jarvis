"""The intelligence plan, release 6: "Why is this chat getting full?" and the
list behind the chat's fullness meter read ONE record.

Every chat reply saves the size of each part of what it was sent on its own
usage row. Two readers of that record:

* ``GET /chat/threads/{id}/filling``: the list the meter opens, "What is
  filling this chat", in the coding session's drop-down shape with a plain
  chat's words;
* ``chat_fullness``: one read-only tool that answers the question in chat,
  for the chat it is asked in and no other.

Both say sizes only (there is no text to say: none is kept), every size as a
share with "about", and a reuse figure the model did not report as
"did not report", never 0%. A chat that holds more than the room is said in
the SAME words by both, with each part's share of what the chat holds (never
a share above 100). The tool carries the house rules of a new tool.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import mission
from iron_jarvis.capability_search import index as CI
from iron_jarvis.context import breakdown as B
from iron_jarvis.context import filling as F
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, ToolInvocation
from iron_jarvis.core.trust import REMOTE_RUN_DENY, REMOTE_RUN_KEEP, remote_run_keeps
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import reply_facts
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools import autoselect
from iron_jarvis.tools.base import Reversibility, ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_wave3_chat_lanes_v1311 import _post_turn, _scripted_router

WINDOW = 100_000
T0 = datetime(2026, 11, 20, 9, 0, 0)
#: The page's copy of the route's REAL answer (dashboard/__tests__/
#: intel-06-chat-filling.test.tsx reads it): written here with
#: ``IJ_REGEN_INTEL_06=1``, compared on every other run.
FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "intel-06-chat-filling.json"

#: A chat that is 43% full: mostly its messages and an attached file.
MINE = {
    "v": 1, "window": WINDOW, "window_known": True, "used": 43_000, "percent": 43, "left_out": 3,
    "parts": {"messages": 30_000, "attachments": 8_000, "memory": 2_000, "system": 3_000},
    "tools": {"builtin": [2, 900], "extensions": {"ledger": [12, 5_100], "mail": [3, 600]}},
    "cache_input": 50_000,
}
#: A chat that holds MORE than the room its model has (a small local model).
BIG = {
    "v": 1, "window": 6_000, "window_known": True, "used": 8_400, "percent": 140, "left_out": 22,
    "parts": {"messages": 8_000, "system": 400},
    "tools": {"builtin": [2, 300], "extensions": {"ledger": [12, 420]}},
}
#: A chat in a room that is the safe size (the model's own limit is not known).
SMALL = {"v": 1, "window": 32_000, "window_known": False, "used": 16_500, "percent": 52,
         "parts": {"messages": 16_100, "system": 400}}
#: Another chat, with a part the first one does not have.
OTHER = {
    "v": 1, "window": WINDOW, "window_known": True, "used": 91_000, "percent": 91,
    "parts": {"messages": 1_000, "skills": 90_000},
}


def _save(engine, thread: str, record: dict | None, *, minutes: int = 0, cache_read: int | None = 31_000,
          tools: list[str] | None = None, session: str = "chat", label: str = "") -> None:
    with session_scope(engine) as db:
        db.add(AgentRun(
            # More was sent than the calls that reported a reuse figure cover
            # (``cache_input`` in the record): the share is read against those.
            session_id=session, thread_id=thread, state=AgentState.COMPLETED, input_tokens=80_000,
            output_tokens=10, created_at=T0 + timedelta(minutes=minutes), result=label,
            parts_json=json.dumps(record) if record is not None else None,
            cache_read_input_tokens=cache_read,
            tools_sent_json=json.dumps(tools if tools is not None else
                                       ["read_file", "list_files", "mcp__ledger__post", "mcp__mail__send"]),
        ))
        db.commit()


def _ctx(platform, tmp_path, **kw) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, agent_run_id="r-1", config=platform.config, event_bus=platform.event_bus,
        engine=platform.engine, **{"session_id": "chat", **kw},
    )


async def _ask(platform, ctx, **args):
    return await platform.registry.invoke(F.TOOL_NAME, args, ctx, PermissionEngine(dict(platform.config.permissions)))


@pytest.fixture(autouse=True)
def _no_reply_running():
    """Outside a reply there are no facts of a reply in flight."""
    reply_facts._FACTS.set(None)
    yield
    reply_facts._FACTS.set(None)


# --------------------------------------------------------------------------- #
# The list the meter opens
# --------------------------------------------------------------------------- #
def test_the_route_answers_the_newest_replys_record_in_a_plain_chats_words(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        engine = client.app.state.platform.engine
        _save(engine, "chat_mine", {**MINE, "percent": 12, "used": 12_000,
                                    "parts": {"messages": 9_000, "system": 3_000}}, minutes=0)
        _save(engine, "chat_mine", MINE, minutes=5)                     # the newest reply
        _save(engine, "chat_mine", None, minutes=9, label="A summary of the conversation")
        _save(engine, "chat_other", OTHER, minutes=7)

        body = client.get("/chat/threads/chat_mine/filling").json()
        assert body["found"] is True and body["thread_id"] == "chat_mine"
        assert body["left_out"] == 3 and body["window_known"] is True
        r = body["reading"]
        assert (r["percent"], r["used"], r["window"], r["source"]) == (43, 43_000, WINDOW, "app")
        # The rows, in plain words, in the list's order; a part the reply did not have is not there.
        assert [(p["label"], p["tokens"], p["kind"]) for p in r["parts"]] == [
            ("Your messages and the replies", 30_000, "used"),
            ("Files you attached", 8_000, "used"),
            ("What Iron Jarvis remembered", 2_000, "used"),
            ("Iron Jarvis's own instructions", 3_000, "used"),
            ("Room left", 57_000, "free"),
        ]
        assert "Skills" not in json.dumps(r), "another chat's part was read"
        # The tools: listed apart (sent with each message), by where they came from.
        assert r["extra"] == [{"key": "tools", "label": "Tools it was given", "tokens": 6_600}]
        (tools,) = r["lists"]
        assert (tools["label"], tools["count"]) == ("Where they came from", 3)
        assert [(i["name"], i["detail"], i["tokens"]) for i in tools["items"]] == [
            ("Iron Jarvis's own tools", "2 tools", 900), ("ledger", "12 tools", 5_100), ("mail", "3 tools", 600),
        ]
        assert r["tool_names"] == ["read_file", "list_files"], "only Iron Jarvis's own tools are named one by one"
        # No engine word is in any word the page will show.
        said = " ".join([p["label"] for p in r["parts"]] + [e["label"] for e in r["extra"]]
                        + [tools["label"]] + [i["name"] + " " + i["detail"] for i in tools["items"]])
        for word in ("token", "context", "prompt", "cache", "system"):
            assert word not in said.lower(), word


def test_the_pages_contract_fixture_is_the_routes_real_answer(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        engine = client.app.state.platform.engine
        _save(engine, "chat_mine", MINE, minutes=5)
        _save(engine, "chat_small", SMALL, tools=[], minutes=6)
        _save(engine, "chat_big", BIG, minutes=7)
        payload = {
            "filled": client.get("/chat/threads/chat_mine/filling").json(),
            "no_tools_safe_size": client.get("/chat/threads/chat_small/filling").json(),
            "over_full": client.get("/chat/threads/chat_big/filling").json(),
            "none": client.get("/chat/threads/chat_new/filling").json(),
            # ONE RECORD, ONE SET OF WORDS: what ``chat_fullness`` says for
            # the SAME saved replies (``filling.answer``, line by line). The
            # page's test reads the list beside these lines.
            "said": {
                name: F.answer(None, F.newest_row(engine, thread)).splitlines()
                for name, thread in (("filled", "chat_mine"), ("no_tools_safe_size", "chat_small"),
                                     ("over_full", "chat_big"))
            },
        }
    if os.environ.get("IJ_REGEN_INTEL_06") == "1":
        FIXTURE.write_bytes((json.dumps(payload, indent=2, ensure_ascii=False) + chr(10)).encode("utf-8"))
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == payload, (
        "the route's answer changed: regenerate the page's fixture with IJ_REGEN_INTEL_06=1")
    assert payload["no_tools_safe_size"]["window_known"] is False
    assert "extra" not in payload["no_tools_safe_size"]["reading"], "a reply sent no tools has no tools row"
    # Only the chat that holds more than the room is marked so.
    assert payload["over_full"]["reading"]["over"] is True
    assert "over" not in payload["filled"]["reading"] and "over" not in payload["no_tools_safe_size"]["reading"]


def test_a_chat_with_no_record_yet_is_not_found_and_no_other_chat_is_read(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        engine = client.app.state.platform.engine
        _save(engine, "chat_other", OTHER)
        _save(engine, "chat_old", None)                       # a reply from before this release
        _save(engine, "chat_run", MINE, session="session_x")  # an agent run's row is not a chat reply
        for thread in ("chat_new", "chat_old", "chat_run"):
            body = client.get(f"/chat/threads/{thread}/filling").json()
            assert body == {"thread_id": thread, "found": False, "reading": None}, thread


def test_a_record_with_no_known_room_has_no_list():
    """No room, no share: the list is not drawn from sizes alone."""
    assert B.chat_reading({"v": 1, "parts": {"messages": 10}, "used": 10}) is None
    assert B.chat_reading({"v": 99, "window": 10, "parts": {"messages": 1}}) is None   # an unknown shape
    assert B.chat_reading("not json") is None and B.chat_reading(None) is None
    assert B.decode_record(json.dumps(MINE))["parts"] == MINE["parts"]


# --------------------------------------------------------------------------- #
# The question in chat: its own chat only
# --------------------------------------------------------------------------- #
async def test_it_answers_for_the_chat_it_was_asked_in_and_no_other(platform, tmp_path):
    _save(platform.engine, "chat_mine", MINE)
    _save(platform.engine, "chat_other", OTHER, minutes=3)
    before = _rows(platform.engine)

    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_mine"))
    assert res.ok, res.error
    out = res.output
    assert "This chat fills about 43% of what this model can read at once." in out
    assert "- Your messages and the replies: about 30%" in out
    assert "- Files you attached: about 8%" in out
    assert "- What Iron Jarvis remembered: about 2%" in out
    assert "- Iron Jarvis's own instructions: about 3%" in out
    assert "- At least 3 earlier messages were left out of the reply so it fits." in out
    assert ("Tools it was given, sent with each message and not counted in that figure: 17 tools, about 7%: "
            "Iron Jarvis's own tools (2), the ledger extension (12), the mail extension (3).") in out
    # The share reused is read against the input of the calls that reported one.
    assert "- The model reused about 62% of what was sent." in out
    assert "model server" not in out, "a model server is a machine on your own network (VOCABULARY.md)"
    assert F.ESTIMATE_NOTE in out
    assert "Skills" not in out and "91%" not in out, "another chat's record was read"
    assert res.data == {"found": True, "percent": 43, "parts": MINE["parts"]}
    for word in ("token", "context window", "prompt", "cache"):
        assert word not in out.lower(), word

    theirs = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_other"))
    assert "- Skills: about 90%" in theirs.output and "Files you attached" not in theirs.output
    # It reads; it writes nothing.
    assert _rows(platform.engine) == before


def _rows(engine) -> int:
    with session_scope(engine) as db:
        return len([r for r in db.exec(select(AgentRun)) if r.parts_json is not None])


async def test_a_reuse_figure_the_server_did_not_report_is_said_so_never_zero(platform, tmp_path):
    _save(platform.engine, "chat_mine", MINE, cache_read=None)
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_mine"))
    assert f"- {F.NOT_REPORTED}" in res.output
    assert "reused about" not in res.output
    assert F.reused_percent(900, None) is None and F.reused_percent(0, 5) is None
    assert F.reused_percent(900, 0) == 0, "a reported zero is a figure"


async def test_a_chat_bigger_than_the_room_is_said_so_and_no_share_is_ever_above_100(platform, tmp_path):
    """The chat holds more than the room: said in place of a percent, each
    part as its share of what the chat HOLDS (they add up to 100), the room
    left as none, and once that the oldest messages are left out."""
    _save(platform.engine, "chat_big", BIG)
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_big"))
    out = res.output.splitlines()
    assert out[1] == "This chat holds more than this model can read at once."
    assert out[2:6] == [
        "- Your messages and the replies: about 95%",        # 8,000 of the 8,400 it holds
        "- Iron Jarvis's own instructions: about 5%",
        "- Room left: none",
        "- Iron Jarvis leaves the oldest messages out of each reply so it fits (at least 22 in the newest "
        "reply). Each share here is of everything this chat holds.",
    ]
    # The tools are sent beside the chat: their share is of the room, and says so here.
    assert out[6] == ("- Tools it was given, sent with each message and not counted in that figure: 14 tools, "
                      "about 12% of the room: Iron Jarvis's own tools (2), the ledger extension (12).")
    assert res.output.count("left out") == 0 and res.output.count("leaves the oldest messages out") == 1
    for n in _percents(res.output):
        assert n <= 100, res.output
    assert "140" not in res.output and "133" not in res.output


def _percents(text: str) -> list[int]:
    import re

    return [int(n) for n in re.findall(r"(\d+)\s*%", text)]


@pytest.mark.parametrize("record,first", [
    # The saved percent is over 100.
    ({**BIG, "used": 5_900}, "This chat holds more than this model can read at once."),
    # The parts add up to more than the room although the saved percent does not say so.
    ({**BIG, "percent": 98}, "This chat holds more than this model can read at once."),
    # A room that is the safe size says that too.
    ({**BIG, "window_known": False},
     "This chat holds more than Iron Jarvis sends this model at once. This model's own limit is not known, "
     "so a safe size is used."),
])
def test_over_the_room_is_one_decision_for_the_list_and_the_answer(record, first):
    assert B.over_room(record) is True
    assert B.chat_reading(record)["over"] is True
    said = F.answer(None, {"record": B.decode_record(record), "tool_names": [], "input_tokens": 0,
                           "cache_read": None}).splitlines()
    assert said[1] == first
    assert all(n <= 100 for n in _percents("\n".join(said))), said
    # With no count saved, the sentence stands without one.
    bare = F.answer(None, {"record": B.decode_record({**record, "left_out": 0}), "tool_names": [],
                           "input_tokens": 0, "cache_read": None})
    assert ("- Iron Jarvis leaves the oldest messages out of each reply so it fits. "
            "Each share here is of everything this chat holds.") in bare.splitlines()
    assert "at least" not in bare.lower()


def test_a_chat_inside_its_room_is_not_over_and_keeps_its_percent():
    assert B.over_room(MINE) is False and "over" not in B.chat_reading(MINE)
    assert B.over_room({**MINE, "percent": 100, "used": WINDOW}) is False      # exactly full is not over
    assert B.over_room({"v": 1, "parts": {"messages": 9}, "used": 9}) is False  # no room named
    assert F.fill_words(43, True) == "This chat fills about 43% of what this model can read at once."


def test_tools_bigger_than_the_whole_room_are_said_in_words_never_as_a_percent_over_100():
    record = {**MINE, "tools": {"builtin": [40, 130_000], "extensions": {}}}
    line = F._tool_line(B.decode_record(record))
    assert "40 tools, more than the room:" in line and "%" not in line
    assert F._share(5, 0) == "" and F._share(900, 100_000) == "less than 1%"


async def test_no_record_and_no_chat_says_so_and_never_reads_every_chat(platform, tmp_path):
    _save(platform.engine, "chat_other", OTHER)
    _save(platform.engine, "", MINE)                               # an unsaved chat's reply: no chat id
    empty = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_new"))
    assert empty.ok and empty.output == F.NOTHING
    assert empty.data == {"found": False, "percent": None, "parts": {}}
    # No chat id: nothing, never the newest row of all chats.
    assert (await _ask(platform, _ctx(platform, tmp_path))).output == F.NOTHING


async def test_called_inside_an_agent_run_it_says_this_is_not_a_chat_and_reads_nothing(platform, tmp_path):
    _save(platform.engine, "chat_mine", MINE)
    res = await _ask(platform, _ctx(platform, tmp_path, session_id="session_run", thread_id="chat_mine"))
    assert res.ok and res.output == F.NOT_A_CHAT
    assert res.data == {"found": False, "percent": None, "parts": {}}
    assert "43%" not in res.output and "Your messages" not in res.output, "a chat's saved record was read"
    # The run's own facts are its PROMPT's sizes: never said as "this chat",
    # whatever session the call names.
    with reply_facts.scope() as facts:
        facts.note_parts({"v": 1, "window": WINDOW, "window_known": True, "used": 61_000, "percent": 61,
                          "parts": {"messages": 1_000, "system": 60_000}})
        for session in ("session_run", "chat"):
            inside = await _ask(platform, _ctx(platform, tmp_path, session_id=session, thread_id="chat_mine"))
            assert inside.output == F.NOT_A_CHAT, session
            assert "61%" not in inside.output and "This chat fills" not in inside.output
    # A coding session's reply is a chat's reply: it is answered.
    code = await _ask(platform, _ctx(platform, tmp_path, session_id="build:abc", thread_id="chat_mine"))
    assert "This chat fills about 43%" in code.output


async def test_the_saved_record_is_read_off_the_event_loop(platform, tmp_path, monkeypatch):
    _save(platform.engine, "chat_mine", MINE)
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = F.newest_row

    def spy(*a, **k):
        seen.append(threading.get_ident())
        return real(*a, **k)

    monkeypatch.setattr(F, "newest_row", spy)
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_mine"))
    assert "about 43%" in res.output
    assert seen and all(t != loop_thread for t in seen), "the database was read on the event loop"


async def test_the_reply_being_written_is_read_from_its_own_facts(platform, tmp_path):
    """Asked in a chat, the reply that asks has not saved its row yet: its
    own sizes (the very numbers its row will hold) are read from the reply's
    facts, and the reply before it from the saved record."""
    _save(platform.engine, "chat_mine", MINE)
    facts = reply_facts.begin()
    facts.note_parts({"v": 1, "window": WINDOW, "window_known": False, "used": 55_000, "percent": 55,
                      "parts": {"messages": 50_000, "system": 5_000}})
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_mine"))
    out = res.output.splitlines()
    assert out[0] == "What is filling this chat (Iron Jarvis's own record of this reply, as it began):"
    # The gauge's own sentence for a room that is the safe size (the page says the same).
    assert out[1] == ("This chat fills about 55% of what Iron Jarvis sends this model at once. This model's "
                      "own limit is not known, so a safe size is used.")
    assert "- Your messages and the replies: about 50%" in out
    assert "From the reply before this one:" in out
    assert any(line.startswith("- Tools it was given") for line in out)
    assert res.data["percent"] == 55
    # With no saved reply, this reply's own facts are the whole answer.
    alone = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_first"))
    assert "about 55%" in alone.output and "From the reply before" not in alone.output


# --------------------------------------------------------------------------- #
# A new tool's house rules
# --------------------------------------------------------------------------- #
async def test_it_is_read_only_allowed_without_a_card_takes_no_argument_and_is_recorded_as_a_read(platform,
                                                                                                tmp_path):
    tool = platform.registry.get(F.TOOL_NAME)
    assert tool is not None and tool.reversibility is Reversibility.READONLY
    assert type(tool).__module__ == "iron_jarvis.tools.chat_fullness"
    assert platform.config.permissions[F.TOOL_NAME] == "allow"
    assert tool.input_schema["properties"] == {}, "an argument could name another chat"
    _save(platform.engine, "chat_mine", MINE)
    res = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_mine"), thread_id="chat_other", run="x")
    assert res.ok and "Skills" not in res.output, "a made-up argument reached another chat"
    with session_scope(platform.engine) as db:
        (inv,) = [i for i in db.exec(select(ToolInvocation)) if i.tool == F.TOOL_NAME]
    assert inv.ok and (inv.reversibility or "").lower() == Reversibility.READONLY.value


def test_the_context_package_does_not_import_the_tools_package():
    """The providers import ``context`` (the size planner), and the tools
    import the providers: a tool that lived in ``context`` would tie the two
    into one import loop. The tool is in ``tools/``; ``context`` keeps the
    readers and the words."""
    import ast

    for path in sorted(Path(F.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:          # what the module imports when it loads
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("tools"), (path.name, node.module)


def test_it_reads_in_plain_words_everywhere():
    assert mission.card_words(F.TOOL_NAME) == "look up what is filling this chat"
    assert mission.step_line_words(F.TOOL_NAME) == (
        "Looked up what is filling this chat", "Looking up what is filling this chat")
    from iron_jarvis.daemon.chat_turn import _READS_ONLY_TOOLS

    assert F.TOOL_NAME in _READS_ONLY_TOOLS


def _arms(said: str) -> bool:
    return any(F.TOOL_NAME in tools and rx.search(said) for rx, tools in autoselect._RULES)


#: THE HELD TABLE (written before the rule was tightened; review finding 8).
#: The rule needs the CHAT (or conversation, or thread) as the thing that is
#: full or filling: full, filling up, fullness, running out of room or space,
#: how much room is left in it. Never "long", and never another thing that
#: has "chat" in its name (a chat room, a chat server).
HELD_ARM = [
    "Why is this chat getting full?",
    "What is filling this chat?",
    "what's using up this conversation",
    "what is taking up all the room in this chat",
    "How full is this chat?",
    "how much room is left in this chat",
    "what is this chat full of",
    "why is this conversation filling up so fast",
    "is this chat running out of room",
    "this chat is running out of space, why?",
    "how much space is left in this conversation",
    "why is the chat almost full",
    "what is filling up this thread",
    "what's the fullness of this chat",
    "Why has this chat gotten so full?",
    "how full is my conversation right now",
    "is this chat almost full?",
]
HELD_NOT = [
    # Other things that are full.
    "my disk is getting full",
    "why is my inbox so full",
    "fill in this form for me",
    "how full is the moon tonight",
    "what is filling the schedule this week",
    "write a full report on this chat app market",
    "how much space is left on this drive",
    # "Long" is not "full" (the reviewer's three, and the plain one).
    "why is the meeting so long",
    "why is my chat so long to load",
    "why is this conversation so long winded, be brief",
    "why is this conversation so long",
    # Another thing with "chat" in its name.
    "how full is the chat room at the venue",
    "is the chat room full tonight",
    "how much room is left in the chat room for the event",
    "is the chat server full",
    "what is filling up the chat queue in Slack",
    # A chat that is full OF something is not a question about its room.
    "the group chat is full of spam, mute it",
    "why is this chat full of typos",
    # "Full" as "whole".
    "summarize this chat in full",
    "give me the full conversation as a file",
]


#: CHECKED ON FRESH SENTENCES, three times. Each set was written after the
#: rule's last change and run ONCE before anything was changed for it:
#: set 1 scored 9 of 10 and 12 of 12 (the miss, "why did this thread fill up
#: so quickly", was fixed); set 2, written before that fix was read back,
#: scored 7 of 8 and 9 of 10 ("how come my conversation filled up already"
#: and "is the chat full screen now" were fixed); set 3 scored 7 of 8 and
#: 10 of 10 and NOTHING was changed for it: its one miss is listed apart
#: below, so the last figure stays a figure the rule was never fitted to.
FRESH_ARM = [
    # set 1
    "why is our chat getting so full",
    "what's eating up the space in this conversation",
    "how full is the conversation",
    "is my chat nearly full",
    "why did this thread fill up so quickly",
    "how much room is free in this thread",
    "this conversation is running out of room, what's taking it",
    "tell me the fullness of this conversation",
    "what has been filling up my chat",
    "is this conversation getting full",
    # set 2
    "how come my conversation filled up already",
    "why has the thread gotten full",
    "what is using up all the space in my chat",
    "how full is this thread",
    "is our conversation too full to keep going",
    "how much space is used in this chat",
    "my chat runs out of room quickly, why",
    "what's the fullness of my thread",
    # set 3 (never fitted to)
    "why is my chat filling up",
    "what is eating up this chat",
    "how full is our thread",
    "is the conversation already full",
    "how much room is used in my conversation",
    "our chat is running out of space",
    "what is the conversation filled up with",
]
FRESH_NOT = [
    # set 1
    "why is the chat window so small",
    "how full is the thread pool on the server",
    "is the conversation history backed up",
    "the chat list is full of old chats, archive them",
    "why is this chat so slow",
    "how much room is left in the hotel for the conference",
    "what is taking up space on my C drive",
    "is the chat app full screen",
    "fill up this chat with test messages",
    "why is my calendar so full this week",
    "what is filling up my inbox",
    "tell me the full name of this chat's project",
    # set 2
    "why is the conversation log so big",
    "how full is the chat channel in Teams",
    "is the thread count full on this CPU",
    "why is this chat window full of ads",
    "what is taking up the room in the garage",
    "how much space is left in the conversation tab's sidebar",
    "is the chat full screen now",
    "why has my chat been so slow today",
    "this thread is full of great ideas, save them",
    "what is this chat about",
    # set 3 (never fitted to)
    "why is the chat bot so slow",
    "how full is my calendar tomorrow",
    "is the thread list full",
    "what is using up my battery",
    "why is this conversation going in circles",
    "how much space is free in the conversation group's drive",
    "is this chat private",
    "make this chat full width",
    "why is the chat sidebar taking up so much room",
    "the conversation was full of jargon, simplify it",
]
#: Set 3's one miss, left as it is (fixing it would fit the rule to the set
#: that measures it). Chat still reaches the tool for it through the lookup.
FRESH_MISSED = ["how come this chat is so full"]


@pytest.mark.parametrize("said", HELD_ARM + FRESH_ARM)
def test_plain_words_arm_it(said):
    assert F.TOOL_NAME in autoselect.AUTO_SAFE_TOOLS
    assert _arms(said), said


@pytest.mark.parametrize("said", HELD_NOT + FRESH_NOT)
def test_other_things_that_are_full_or_long_do_not_arm_it(said):
    assert not _arms(said), said


def test_the_fresh_sets_one_miss_is_written_down_and_the_lookup_still_finds_the_tool(platform):
    for said in FRESH_MISSED:
        assert not _arms(said), f"{said!r} arms now: move it to FRESH_ARM"
        assert F.TOOL_NAME in [e.id for e in CI.search(platform, said, 5)], said


def test_an_agent_runs_task_words_never_arm_it(platform):
    """A hand-off's task can be the very question. Through the real arming
    path a run does not get the tool: inside a run it would describe the
    run's own prompt as "this chat"."""
    from iron_jarvis.agents.runtime import _CHAT_ONLY_AUTO, arm_for_task

    assert _CHAT_ONLY_AUTO == {F.TOOL_NAME}
    for task in ("Why is this chat getting full?", "what is filling this chat and how full is this chat"):
        assert _arms(task), "chat itself would arm it for these words"
        armed = arm_for_task(platform, task, ["read_file"])
        assert F.TOOL_NAME not in armed, armed
    # The control: the same path still arms what a run may use from its words
    # (the read-only list of saved workflows).
    assert "workflow_list" in arm_for_task(platform, "what workflows do I have saved?", ["read_file"])
    # A definition that lists it by name keeps it: a roster is consent.
    assert arm_for_task(platform, "Why is this chat getting full?", ["read_file", F.TOOL_NAME]) == [
        "read_file", F.TOOL_NAME]


@pytest.mark.parametrize("query", [
    "why is this chat getting full", "what is filling this chat", "how full is this chat",
    # Found only through its everyday phrasings.
    "is this conversation too long", "what takes up the most room in this chat",
])
def test_the_lookup_finds_it_by_plain_words(platform, query):
    assert F.TOOL_NAME in [e.id for e in CI.search(platform, query, 5)]


def test_a_task_from_another_computer_may_not_call_it():
    """Decided: NOT on the keep list. A run another computer's task started
    posts its answer back to that computer, and this tool reads this
    computer's record (the names of its extensions, how full a chat is)."""
    assert F.TOOL_NAME not in REMOTE_RUN_KEEP
    assert F.TOOL_NAME not in REMOTE_RUN_DENY          # refused by the keep list itself
    assert remote_run_keeps(F.TOOL_NAME) is False


# --------------------------------------------------------------------------- #
# Asked in a chat, through the real lane
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ("post", "stream"))
def test_asked_in_a_chat_the_model_is_handed_that_chats_own_record(tmp_path, monkeypatch, lane):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        platform = client.app.state.platform
        _save(platform.engine, "chat_mine", MINE)
        _save(platform.engine, "chat_other", OTHER, minutes=4)
        seen = _scripted_router(platform, monkeypatch, [[ToolCall(id="c1", name=F.TOOL_NAME, arguments={})]],
                                "Mostly your messages and the file you attached.")
        body, _ = _post_turn(client, lane, {
            "messages": [{"role": "user", "content": "Why is this chat getting full?"}],
            "thread_id": "chat_mine", "turn_id": "turn_ask",
            # Nothing is ticked: the words alone arm it (Auto tools, as the page sends it).
            "auto_tools": True,
        })
        assert body["auto_armed"] == [F.TOOL_NAME], body.get("auto_armed")
        assert F.TOOL_NAME in body["tools_used"], body.get("reply")
        given = "\n".join(str(getattr(m, "content", "")) for m in seen["messages"][-1])
        # This reply's own sizes (measured before its first model call)...
        assert "What is filling this chat (Iron Jarvis's own record of this reply, as it began):" in given
        assert "- Your messages and the replies:" in given
        # ...and the reply before it, from this chat's saved record only.
        assert "the ledger extension (12)" in given and "about 62%" in given
        assert "Skills: about 90%" not in given
        # The reply that asked saved its own record too, with the tool it was given.
        rows = [r for r in _all(platform.engine) if r.thread_id == "chat_mine" and r.created_at > T0 + timedelta(days=1)]
        assert len(rows) == 1 and F.TOOL_NAME in json.loads(rows[0].tools_sent_json)
        assert json.loads(rows[0].parts_json)["parts"]["messages"] > 0


def _all(engine) -> list[AgentRun]:
    with session_scope(engine) as db:
        return [r for r in db.exec(select(AgentRun)) if r.session_id == "chat"]
