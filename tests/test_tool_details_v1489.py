"""A tool asks the USER for a detail only they have, on a card (v1.489.0).

Seen live, side by side on the practice set: "I have Ollama running on the old
machine in the storeroom, could you add it" found the right tool
(`model_server_add`) and then ended on a question in words, because the tool
cannot be called without the server's address. Six tries, no call. With the
address left to a question card the same two requests called the tool in five
of six tries.

What is pinned here:

* the model is shown a spec that lets it leave the detail out, and the tool's
  own schema is untouched;
* in the REAL stream lane the question card comes BEFORE the approval card,
  the answer is in the call the approval card shows, and the change lands;
* Decline is an answer: no approval card, nothing changed, and the model is
  told not to ask again in words;
* a chat that cannot show the card asks nothing and tells the model to ask in
  words; a call that is refused anyway asks nothing;
* a detail is never a secret.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon import mcp_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.secrets import is_secret_key
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_build as RB
from iron_jarvis.settings import records_connections as RC
from iron_jarvis.settings import records_tools as RT
from iron_jarvis.tools import details as D
from iron_jarvis.tools.base import Tool, ToolResult
from iron_jarvis.tools.registry import ToolRegistry
from tests.test_chat_turn_stop_v1241 import _asgi_post, _drive_stream
from tests.test_records_connections_v14380 import _ModelServer, _node_ids
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

TURN = "turn-details"


@pytest.fixture
def server():
    s = _ModelServer()
    yield s
    s.close()


def _app(tmp_path, specs):
    app = create_app(str(tmp_path / "ij"))
    app.state.platform.router.stream = _calls_stream(specs)
    return app


async def _watchdog(app, seconds=20.0):
    """A broken lane must fail the test, never hang it (a question card has
    no clock by design): stop the turn late and decline what is parked."""
    await asyncio.sleep(seconds)
    await _asgi_post(app, f"/chat/turns/{TURN}/stop")
    while True:
        await asyncio.sleep(1.0)
        for ask in list(mcp_turn.PENDING.values()):
            ask.resolve("decline" if ask.kind == "elicitation" else "denied")


async def _run(app, text, *, answer=None, decision="once", cards=True, **extra):
    """Drive one real stream turn. ``answer``: the dict typed on the question
    card (None = Decline). ``decision``: the approval card's answer. Returns
    ``(frames, order)`` where *order* lists the question and approval cards
    in the order they were shown."""
    order: list[tuple[str, dict]] = []

    async def on_frame(ev, data):
        if ev == "mcp_elicitation":
            order.append(("question", data))
            body = {"action": "accept", "content": answer} if answer is not None else {"action": "decline"}
            status, _ = await _asgi_post(app, f"/chat/mcp/elicitations/{data['id']}", body)
            assert status == 200
        elif ev == "approval":
            order.append(("approval", data))
            status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": decision})
            assert status == 200

    dog = asyncio.create_task(_watchdog(app))
    try:
        frames = await _drive_stream(app, _body(text, turn_id=TURN, mcp_cards=cards, **extra), on_frame)
    finally:
        dog.cancel()
    return frames, order


def _finished(frames, tool):
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished" and d.get("name") == tool]


# =========================================================================== #
# What the model is shown
# =========================================================================== #
def _tool(cls):
    t = cls.__new__(cls)
    t.platform = None
    return t


def test_the_model_may_leave_the_detail_out_and_the_tools_own_schema_is_untouched():
    before = copy.deepcopy(RC.ModelServerAddTool.input_schema)
    spec = _tool(RC.ModelServerAddTool).spec()
    assert "address" not in (spec["input_schema"].get("required") or [])
    assert "address" in spec["input_schema"]["properties"], "it can still be given"
    assert "call this WITHOUT it" in spec["description"] and "asked for it on a card" in spec["description"]
    assert "never guess it" in spec["description"]
    # The real requirement stays where the registry checks it.
    assert RC.ModelServerAddTool.input_schema == before
    assert RC.ModelServerAddTool.input_schema["required"] == ["address"]


def test_only_the_details_stop_being_required():
    spec = _tool(RT.WebServiceAddTool).spec()
    assert spec["input_schema"]["required"] == ["name"]
    spec = _tool(R.ChannelConnectTool).spec()
    assert spec["input_schema"]["required"] == ["name", "type"]


def test_a_tool_with_no_user_details_has_exactly_its_old_spec():
    class Plain(Tool):
        name = "plain"
        description = "A plain tool."
        input_schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

        async def execute(self, args, ctx):  # pragma: no cover - never run
            return ToolResult(ok=True)

    spec = Plain().spec()
    assert spec == {"name": "plain", "description": "A plain tool.", "input_schema": Plain.input_schema}
    assert spec["input_schema"] is Plain.input_schema


def _detail_tools(app):
    reg = app.state.platform.registry
    return [reg.get(n) for n in reg.names() if getattr(reg.get(n), "user_details", None)]


def test_every_detail_is_a_plain_text_argument_of_its_tool_and_never_a_secret(tmp_path):
    app = create_app(str(tmp_path / "ij"))
    tools = _detail_tools(app)
    names = {t.name for t in tools}
    assert {"model_server_add", "channel_connect", "memory_base_add", "night_task_add",
            "mcp_server_add", "web_service_add", "build_defaults_set", "build_team_set"} <= names
    for t in tools:
        props = t.input_schema.get("properties") or {}
        for key, q in t.user_details.items():
            # (The settings tools' `value` takes any type: a pick from a
            # setting's fixed choices, tests/test_setting_pick_card_v1489.)
            settings_value = t.name.startswith("config_") and key == "value"
            assert key in props and (settings_value or props[key].get("type") == "string"), (t.name, key)
            assert not is_secret_key(key), (t.name, key)
            assert q.get("title") and q.get("what"), (t.name, key)
            words = " ".join(str(v) for v in q.values()).lower()
            for banned in ("password", "token", "api key", "secret"):
                assert banned not in words, (t.name, key, banned)
        # The card these questions make is one the chat can draw.
        from iron_jarvis.mcp.interact import elicitation_fields

        fields, reason = elicitation_fields(D.ask_params(t, dict(t.user_details)))
        assert not reason and len(fields) == len(t.user_details), (t.name, reason)


# =========================================================================== #
# Which details a call leaves out
# =========================================================================== #
def test_a_destination_asks_only_what_its_kind_needs():
    t = _tool(R.ChannelConnectTool)
    assert list(t.missing_details({"name": "tg", "type": "telegram"})) == ["chat_id"]
    assert list(t.missing_details({"name": "tg", "type": "telegram", "chat_id": "123"})) == []
    assert list(t.missing_details({"name": "s", "type": "slack"})) == ["channel"]
    assert list(t.missing_details({"name": "d", "type": "discord"})) == []
    assert list(t.missing_details({"name": "m", "type": "email", "host": "smtp.example.com"})) == ["from_addr", "to_addr"]
    # No kind yet: the tool says which kinds there are; nothing is asked.
    assert list(t.missing_details({"name": "x"})) == []


def test_a_memory_base_asks_where_it_is_for_its_kind():
    t = _tool(RC.MemoryBaseAddTool)
    assert list(t.missing_details({"kind": "folder", "name": "Shop Notes"})) == ["folder"]
    assert list(t.missing_details({"kind": "notion", "name": "n"})) == ["database"]
    assert list(t.missing_details({"kind": "folder", "name": "n", "folder": "D:\\vault"})) == []
    assert list(t.missing_details({"kind": "other", "name": "n"})) == []


def test_a_folder_is_asked_for_only_when_the_user_has_to_place_it(tmp_path, monkeypatch):
    t = _tool(RB.NightTaskAddTool)
    ctx = SimpleNamespace(project_id="")
    monkeypatch.setattr(RB, "_project_root", lambda platform, pid: "D:\\code\\shop" if pid == "p1" else "")
    # No folder named, no project folder: ask.
    assert list(t.missing_details({"words": "fix it"}, ctx)) == ["folder"]
    # The chat's project has a folder: it is used, nothing is asked.
    assert list(t.missing_details({"words": "fix it"}, SimpleNamespace(project_id="p1"))) == []
    # A name that is not a full location: ask where it is.
    assert list(t.missing_details({"words": "fix it", "folder": "Webshop"}, ctx)) == ["folder"]
    # A full location: nothing. A network spelling: the tool's own refusal, no card.
    full = "D:\\code\\shop" if RB.os.name == "nt" else "/code/shop"
    assert list(t.missing_details({"words": "fix it", "folder": full}, ctx)) == []
    assert list(t.missing_details({"words": "fix it", "folder": "\\\\host\\share\\x"}, ctx)) == []
    # With no chat to look at, nothing is asked.
    assert list(t.missing_details({"words": "fix it"}, None)) == []
    # The folder tools share the rule; a tool that names no folder asks nothing.
    assert list(_tool(RB.BuildTeamSetTool).missing_details({"workers": ["a"]}, ctx)) == ["folder"]
    assert list(_tool(RB.NightTaskCancelTool).missing_details({}, ctx)) == []


# =========================================================================== #
# fill_missing, directly
# =========================================================================== #
class _Reg:
    def __init__(self, *tools):
        self._t = {t.name: t for t in tools}

    def get(self, name):
        return self._t.get(name)


def _call(name, args, cid="c1"):
    return SimpleNamespace(id=cid, name=name, arguments=args)


def _elicit(answer, seen=None):
    async def elicit(call_id, asker, params):
        if seen is not None:
            seen.append((call_id, asker, params))
        return answer

    return elicit


@pytest.mark.asyncio
async def test_an_accepted_answer_goes_into_the_call():
    tool = _tool(RC.ModelServerAddTool)
    call = _call("model_server_add", {"name": "Closet box"})
    seen: list = []
    out = await D.fill_missing(_Reg(tool), [call], {"model_server_add"},
                               _elicit({"action": "accept", "content": {"address": "  http://192.168.1.20:1234 "}}, seen))
    assert out == {}
    assert call.arguments == {"name": "Closet box", "address": "http://192.168.1.20:1234"}
    (cid, asker, params), = seen
    assert cid == "c1" and asker == "Iron Jarvis"
    assert params["message"] == tool.user_details_lead
    assert list(params["requestedSchema"]["properties"]) == ["address"]


@pytest.mark.asyncio
async def test_decline_answers_the_call_and_changes_nothing():
    tool = _tool(RC.ModelServerAddTool)
    call = _call("model_server_add", {"name": "Closet box"})
    out = await D.fill_missing(_Reg(tool), [call], {"model_server_add"}, _elicit({"action": "decline"}))
    assert call.arguments == {"name": "Closet box"}
    sentence = out[id(call)]
    assert "the server's address" in sentence and "nothing was changed" in sentence
    assert "Do not ask for it again in words" in sentence


@pytest.mark.asyncio
async def test_a_card_that_was_cut_off_is_not_called_a_decline():
    tool = _tool(RC.ModelServerAddTool)
    call = _call("model_server_add", {})
    out = await D.fill_missing(_Reg(tool), [call], {"model_server_add"}, _elicit({"action": "cancel"}))
    assert "could not be asked" in out[id(call)] and "did not give" not in out[id(call)]


@pytest.mark.asyncio
async def test_an_empty_or_odd_answer_is_no_answer():
    tool = _tool(RC.ModelServerAddTool)
    for content in ({"address": "   "}, {"address": 7}, {"address": ["http://a:1"]}, {"address": {"a": 1}}, {}, {"other": "x"}):
        call = _call("model_server_add", {})
        out = await D.fill_missing(_Reg(tool), [call], {"model_server_add"},
                                   _elicit({"action": "accept", "content": content}))
        assert id(call) in out and "address" not in call.arguments, content


@pytest.mark.asyncio
async def test_nothing_is_asked_for_a_call_that_has_it_is_not_in_the_reply_or_is_refused():
    tool = _tool(RC.ModelServerAddTool)
    seen: list = []
    ask = _elicit({"action": "decline"}, seen)
    has = _call("model_server_add", {"address": "http://x:1"})
    assert await D.fill_missing(_Reg(tool), [has], {"model_server_add"}, ask) == {}
    out_of_reply = _call("model_server_add", {})
    assert await D.fill_missing(_Reg(tool), [out_of_reply], {"other"}, ask) == {}
    refused = _call("model_server_add", {})
    assert await D.fill_missing(_Reg(tool), [refused], {"model_server_add"}, ask, refused=lambda c, t: True) == {}
    unknown = _call("nope", {})
    assert await D.fill_missing(_Reg(tool), [unknown], {"nope"}, ask) == {}
    assert seen == []


@pytest.mark.asyncio
async def test_a_fault_while_asking_never_stops_the_turn():
    tool = _tool(RC.ModelServerAddTool)

    async def boom(call_id, asker, params):
        raise RuntimeError("card broke")

    call = _call("model_server_add", {})
    assert await D.fill_missing(_Reg(tool), [call], {"model_server_add"}, boom) == {}
    assert call.arguments == {}


@pytest.mark.asyncio
async def test_each_call_of_a_round_gets_its_own_card_in_order():
    tool = _tool(RC.ModelServerAddTool)
    a, b = _call("model_server_add", {}, "a"), _call("model_server_add", {}, "b")
    answers = iter([{"action": "accept", "content": {"address": "http://a:1"}}, {"action": "decline"}])
    order: list = []

    async def elicit(call_id, asker, params):
        order.append(call_id)
        return next(answers)

    out = await D.fill_missing(_Reg(tool), [a, b], {"model_server_add"}, elicit)
    assert order == ["a", "b"]
    assert a.arguments == {"address": "http://a:1"} and list(out) == [id(b)]


# =========================================================================== #
# A chat that cannot show the card: the model is told to ask in words
# =========================================================================== #
def test_the_registry_says_to_ask_in_words_when_only_a_user_detail_is_missing():
    tool = _tool(RC.ModelServerAddTool)
    said = ToolRegistry._argument_shape_error(tool, {"name": "x"})
    assert "still needs the server's address" in said
    assert "cannot show the user a question card" in said and "Ask the user for it in words" in said
    assert "missing required" not in said
    # A missing argument that is NOT a user detail keeps the plain sentence.
    said = ToolRegistry._argument_shape_error(_tool(RT.WebServiceAddTool), {})
    assert said.startswith("missing required: name, address")
    # A wrong type beside it keeps the plain sentence too.
    said = ToolRegistry._argument_shape_error(tool, {"name": 5})
    assert "missing required: address" in said and "wrong type" in said


# =========================================================================== #
# The real stream lane
# =========================================================================== #
@pytest.mark.asyncio
async def test_the_question_card_comes_first_and_the_approval_card_shows_the_whole_call(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"name": "Closet box"})])
    p = app.state.platform
    frames, order = await _run(app, "Add the AI box in my closet as a model server", answer={"address": server.url})
    assert [k for k, _ in order] == ["question", "approval"], order
    q = order[0][1]
    assert q["pack"] == "Iron Jarvis" and q["call_id"] == "c0"
    assert [f["name"] for f in q["fields"]] == ["address"] and q["fields"][0]["required"] is True
    assert q["fields"][0]["title"] == "The server's address"
    assert "address" in q["message"]
    card = order[1][1]
    assert card["tool"] == "model_server_add"
    assert card["args"]["address"] == server.url, "the approval card shows the address the user typed"
    assert "on this PC or your own network" in str(card), "and says where it is"
    # The change landed, with the address from the card.
    assert [n for n in _node_ids(p) if n == "closet-box"] == ["closet-box"]
    assert p.fleet.get("closet-box").base_url == server.url
    done = _finished(frames, "model_server_add")
    assert len(done) == 1 and done[0]["ok"] is True
    kinds = [ev for ev, _ in frames]
    assert kinds.index("mcp_elicitation") < kinds.index("mcp_resolved") < kinds.index("approval")


@pytest.mark.asyncio
async def test_decline_shows_no_approval_card_and_adds_nothing(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"name": "Closet box"})])
    p = app.state.platform
    before = _node_ids(p)
    frames, order = await _run(app, "Add the AI box in my closet as a model server", answer=None)
    assert [k for k, _ in order] == ["question"], "a declined question is the end of it: no approval card"
    assert _node_ids(p) == before
    done = _finished(frames, "model_server_add")
    assert len(done) == 1 and done[0]["ok"] is False
    assert "Do not ask for it again in words" in str(done[0])
    assert server.seen == [], "nothing was sent to any server"
    assert "model_server_add" not in (_done(frames).get("tools_used") or [])


@pytest.mark.asyncio
async def test_an_approval_denied_after_the_answer_adds_nothing(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"name": "Closet box"})])
    p = app.state.platform
    before = _node_ids(p)
    _, order = await _run(app, "Add the AI box in my closet as a model server",
                          answer={"address": server.url}, decision="deny")
    assert [k for k, _ in order] == ["question", "approval"]
    assert _node_ids(p) == before


@pytest.mark.asyncio
async def test_a_chat_that_cannot_show_the_card_asks_nothing_and_says_to_ask_in_words(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"name": "Closet box"})])
    p = app.state.platform
    before = _node_ids(p)
    frames, order = await _run(app, "Add the AI box in my closet as a model server", cards=False)
    assert "question" not in [k for k, _ in order]
    assert _node_ids(p) == before
    done = _finished(frames, "model_server_add")
    assert done and done[-1]["ok"] is False and "Ask the user for it in words" in str(done[-1])


@pytest.mark.asyncio
async def test_a_call_that_gives_the_address_shows_no_question(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"name": "Closet box", "address": server.url})])
    _, order = await _run(app, "Add my model server at " + server.url)
    assert [k for k, _ in order] == ["approval"]


@pytest.mark.asyncio
async def test_a_tool_set_to_never_run_asks_no_question(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"name": "Closet box"})])
    p = app.state.platform
    before = dict(p.permissions._base)
    p.permissions._base["model_server_add"] = "deny"
    try:
        frames, order = await _run(app, "Add the AI box in my closet as a model server")
    finally:
        p.permissions._base.clear()
        p.permissions._base.update(before)
    assert order == [], "a call that is refused anyway must not put a question to the user"
    assert not any(d.get("ok") for d in _finished(frames, "model_server_add"))


@pytest.mark.asyncio
async def test_a_bad_answer_is_refused_by_the_tools_own_check_after_the_card(tmp_path):
    # What the user types is checked exactly like what the model types: an
    # address with a password in it never gets an approval card.
    app = _app(tmp_path, [("model_server_add", {"name": "Closet box"})])
    p = app.state.platform
    before = _node_ids(p)
    frames, order = await _run(app, "Add the AI box in my closet as a model server",
                               answer={"address": "http://user:hunter2@192.168.1.20:1234"})
    assert [k for k, _ in order] == ["question"]
    assert _node_ids(p) == before
    done = _finished(frames, "model_server_add")
    assert done and done[-1]["ok"] is False
    assert "hunter2" not in str([d for ev, d in frames if ev != "mcp_elicitation"])
