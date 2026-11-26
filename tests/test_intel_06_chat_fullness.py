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
share with "about", and a reuse figure the model server did not report as
"did not report", never 0%. The tool carries the house rules of a new tool.
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
        _save(engine, "chat_small", {"v": 1, "window": 32_000, "window_known": False, "used": 16_500,
                                     "percent": 52, "parts": {"messages": 16_100, "system": 400}},
              tools=[], minutes=6)
        payload = {
            "filled": client.get("/chat/threads/chat_mine/filling").json(),
            "no_tools_safe_size": client.get("/chat/threads/chat_small/filling").json(),
            "none": client.get("/chat/threads/chat_new/filling").json(),
        }
    if os.environ.get("IJ_REGEN_INTEL_06") == "1":
        FIXTURE.write_bytes((json.dumps(payload, indent=2, ensure_ascii=False) + chr(10)).encode("utf-8"))
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == payload, (
        "the route's answer changed: regenerate the page's fixture with IJ_REGEN_INTEL_06=1")
    assert payload["no_tools_safe_size"]["window_known"] is False
    assert "extra" not in payload["no_tools_safe_size"]["reading"], "a reply sent no tools has no tools row"


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
    assert "- The model server reused about 62% of what was sent." in out
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


async def test_a_chat_bigger_than_the_room_is_said_so_never_as_a_percent_over_100(platform, tmp_path):
    _save(platform.engine, "chat_big", {"v": 1, "window": 6_000, "window_known": True, "used": 8_400,
                                        "percent": 140, "left_out": 22,
                                        "parts": {"messages": 8_000, "system": 400}})
    out = (await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_big"))).output
    assert ("This chat holds more than what this model can read at once, so earlier messages are left out "
            "of each reply.") in out
    assert "140%" not in out
    assert "- Your messages and the replies: about 133%" in out
    assert "- At least 22 earlier messages were left out of the reply so it fits." in out


async def test_no_record_and_no_chat_says_so_and_never_reads_every_chat(platform, tmp_path):
    _save(platform.engine, "chat_other", OTHER)
    _save(platform.engine, "", MINE)                               # an unsaved chat's reply: no chat id
    empty = await _ask(platform, _ctx(platform, tmp_path, thread_id="chat_new"))
    assert empty.ok and empty.output == F.NOTHING
    assert empty.data == {"found": False, "percent": None, "parts": {}}
    # No chat id: nothing, never the newest row of all chats.
    assert (await _ask(platform, _ctx(platform, tmp_path))).output == F.NOTHING


async def test_called_inside_an_agent_run_it_never_reads_a_chats_saved_record(platform, tmp_path):
    _save(platform.engine, "chat_mine", MINE)
    res = await _ask(platform, _ctx(platform, tmp_path, session_id="session_run", thread_id="chat_mine"))
    assert res.output == F.NOTHING


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
    assert out[1] == ("This chat fills about 55% of the safe size Iron Jarvis uses for this model "
                      "(its own limit is not known).")
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


@pytest.mark.parametrize("said", [
    "Why is this chat getting full?",
    "why is this conversation so long",
    "What is filling this chat?",
    "what's using up this conversation",
    "what is taking up all the room in this chat",
    "How full is this chat?",
    "how much room is left in this chat",
    "what is this chat full of",
])
def test_plain_words_arm_it(said):
    assert F.TOOL_NAME in autoselect.AUTO_SAFE_TOOLS
    assert any(F.TOOL_NAME in tools and rx.search(said) for rx, tools in autoselect._RULES), said


@pytest.mark.parametrize("said", [
    "my disk is getting full",
    "why is my inbox so full",
    "fill in this form for me",
    "how full is the moon tonight",
    "what is filling the schedule this week",
    "write a full report on this chat app market",
    "why is the meeting so long",
])
def test_other_things_that_are_full_do_not_arm_it(said):
    assert not any(F.TOOL_NAME in tools and rx.search(said) for rx, tools in autoselect._RULES), said


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
