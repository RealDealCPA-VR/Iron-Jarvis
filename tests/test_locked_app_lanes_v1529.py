"""Owner decision 8 after the third fix plan, in the LANES.

A locked app promises "Jarvis asks you before each use of it". Until v1.529.0
the lanes asked only because every locked app was also kept on the user's own
models (the chat's Local only card). Here the app is locked and NOT kept on
the user's own models, and switched on for the chat (its tools armed by
name). Each lane must still ask before EACH use:

* the stream lane (the Chat page) shows a once-only card for every call, in
  Ask first and in Don't ask; a "for this conversation" answer counts once;
* the POST lane (an API caller, the phone) cannot ask, so it refuses in words;
* an agent run handed off from chat (its grant list holds the app's names and
  ``mcp_call``) pauses on every call; "Allow for this run" counts once; a run
  nobody can answer refuses.

Proved through the REAL lanes, registry, gate, approvals and ledger: only the
model and the app's transport are doubles (the tool's own ``execute`` runs).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import select

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core import auto_approve as A
from iron_jarvis.core.approvals import flush_interrupt_notes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.models import AgentType
from tests.test_third_plan_g17_gate_runs import _cards, _rows, _steps, rt  # noqa: F401

APP = "mailbox"
LREAD = f"mcp__{APP}__list_mail"  # read-only (readOnlyHint)
LDRAFT = f"mcp__{APP}__create_draft"  # changes something
WHY = A.ASK_RULES["locked"]


class _Client:
    """The app's transport: the one double besides the model."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(self, name, args):
        self.calls.append(name)
        return {"content": [{"type": "text", "text": f"{name} answered"}]}


def _locked_tools(platform) -> _Client:
    client = _Client()
    for remote, ann in (("list_mail", {"readOnlyHint": True}), ("create_draft", {})):
        spec = {"name": remote, "inputSchema": {"type": "object", "properties": {}},
                "annotations": ann}
        tool = MCPRemoteTool.from_spec(client, APP, spec)
        tool.locked = True  # what mcp/tools.mcp_tools sets for a locked row
        assert tool.local_only is False  # NOT kept on the user's own models
        platform.registry.register(tool, mcp=True)
    return client


@pytest.fixture()
def lenv(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform
    from tests.test_third_plan_g17_gate import Model

    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    client = _locked_tools(platform)

    def wire(moves):
        model = Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)

    return SimpleNamespace(platform=platform, client=client, wire=wire)


def _body(mode: str = "approve_for_me", **kw):
    """A chat on a cloud model, Local only OFF, the locked app switched on."""
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody

    return ChatBody(
        messages=[ChatMessageBody(role="user", content="draft a reply to my mail")],
        provider="anthropic", model="claude-sonnet-4-5", approval_mode=mode,
        connectors=[APP], **kw,
    )


def _calls(name: str, cid: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name=name, arguments={})]


def _ledger(platform, name: str) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)) if r.tool == name]
        for r in rows:
            db.expunge(r)
        return rows


# --------------------------------------------------------------------------- #
# The stream lane (the Chat page).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [LREAD, LDRAFT])
async def test_stream_lane_asks_before_each_use_with_the_app_switched_on(lenv, name):
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_calls(name, "m1"), _calls(name, "m2")])
    out = await _stream(lenv.platform, _body(), ["once", "deny"])
    assert [c["tool"] for c in out["cards"]] == [name, name], "a locked app's tool ran with no card"
    assert lenv.client.calls == [name.rsplit("__", 1)[1]], "Allow once ran exactly one call"
    for card in out["cards"]:
        assert card.get("once_only") is True, card
        assert card.get("can_always") is False, card
        assert card.get("quarantined") is False, "a locked app's tool is not 'new in its app'"
        assert card.get("why") == WHY, card.get("why")


async def test_stream_lane_dont_ask_still_asks(lenv):
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_calls(LDRAFT, "m1")])
    out = await _stream(lenv.platform, _body("yolo"), ["deny"])
    assert [c["tool"] for c in out["cards"]] == [LDRAFT]
    assert lenv.client.calls == []


async def test_stream_lane_a_conversation_answer_counts_once(lenv):
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_calls(LDRAFT, "m1"), _calls(LDRAFT, "m2")])
    out = await _stream(lenv.platform, _body(), ["conversation", "deny"])
    assert [c["tool"] for c in out["cards"]] == [LDRAFT, LDRAFT]
    assert lenv.client.calls == ["create_draft"]


async def test_stream_lane_a_conversation_grant_from_the_page_lifts_nothing(lenv):
    """The page's ``granted_tools`` (an earlier "for this conversation")
    never covers a locked app's next use."""
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_calls(LDRAFT, "m1")])
    body = _body(granted_tools=[LDRAFT, "mcp_call"])
    out = await _stream(lenv.platform, body, ["deny"])
    assert [c["tool"] for c in out["cards"]] == [LDRAFT]
    assert lenv.client.calls == []


# --------------------------------------------------------------------------- #
# The POST lane (cannot ask).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [LREAD, LDRAFT])
async def test_post_lane_refuses_in_words(lenv, name):
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    lenv.wire([_calls(name, "m1")])
    body = _body(granted_tools=[name, "mcp_call"])  # even a page grant lifts nothing
    await run_chat_turn(lenv.platform, BUILTIN_PERSONAS, body)
    await flush_interrupt_notes()
    assert lenv.client.calls == [], "a locked app's tool ran with nobody asked"
    (row,) = _ledger(lenv.platform, name)
    assert row.ok is False
    assert WHY in (row.output or ""), row.output


# --------------------------------------------------------------------------- #
# An agent run handed off from chat.
# --------------------------------------------------------------------------- #
HANDED = [LREAD, LDRAFT, "mcp_call"]


async def _run(rt, steps, answers=(), *, origin="chat", allow=HANDED, approval_mode=None):
    """A run handed the app's names as a chat hand-off does, with the locked
    app's tools on its roster, run to the end with the given card answers."""
    rt.platform.router.stream = _steps(steps)
    rt.answers[:] = list(answers)
    session = await rt.orch.create_session(
        "draft a reply to my mail", AgentType.BUILDER, origin=origin,
        allow_tools=list(allow), approval_mode=approval_mode,
    )
    await rt.runtime.run(
        session, AgentDefinition(type=AgentType.BUILDER, system_prompt="x", tools=[LREAD, LDRAFT]),
    )
    await flush_interrupt_notes()
    return session


async def test_a_run_from_chat_pauses_on_a_locked_apps_tool_it_was_handed(rt):  # noqa: F811
    client = _locked_tools(rt.platform)
    s = await _run(rt, [_calls(LDRAFT, "m1")], ["deny"], allow=HANDED)
    assert _cards(rt, s.id) == [LDRAFT], "a locked app's tool ran in a run with no card"
    req = [p["payload"] for p in rt.published if p["type"] == "approval.requested"][0]
    assert req["can_always"] is False and req["quarantined"] is False
    assert client.calls == []
    assert [r.ok for r in _rows(rt, s.id) if r.tool == LDRAFT] == [False]


async def test_a_run_names_why_a_locked_apps_card_asks(rt):  # noqa: F811
    """v1.533.0 (fourth review PG-A, lead's review): an agent run's card for
    a locked app's tool carries ``once_reason: "locked"`` on the event and the
    registry entry, so no screen falls back to "This chat is local only"
    (the stream lane's frame does the same since group 25)."""
    _locked_tools(rt.platform)
    s = await _run(rt, [_calls(LDRAFT, "m1")], ["deny"], allow=HANDED)
    assert _cards(rt, s.id) == [LDRAFT]
    req = [p["payload"] for p in rt.published if p["type"] == "approval.requested"][0]
    assert req.get("once_only") is True and req.get("once_reason") == "locked", req


async def test_a_run_allow_once_then_the_next_call_asks_again(rt):  # noqa: F811
    client = _locked_tools(rt.platform)
    s = await _run(rt, [_calls(LDRAFT, "m1"), _calls(LDRAFT, "m2")], ["once", "deny"], allow=HANDED)
    assert _cards(rt, s.id) == [LDRAFT, LDRAFT]
    assert client.calls == ["create_draft"]


async def test_a_run_allow_for_this_run_counts_once(rt):  # noqa: F811
    client = _locked_tools(rt.platform)
    s = await _run(
        rt, [_calls(LDRAFT, "m1"), _calls(LDRAFT, "m2")], ["conversation", "deny"], allow=HANDED,
    )
    assert _cards(rt, s.id) == [LDRAFT, LDRAFT], "'for this run' covered the next use"
    assert client.calls == ["create_draft"]


@pytest.mark.parametrize("mode", ["auto", "accept_edits"])
async def test_a_run_auto_and_accept_edits_never_pass_it(rt, mode):  # noqa: F811
    client = _locked_tools(rt.platform)
    s = await _run(rt, [_calls(LREAD, "m1")], ["deny"], allow=HANDED, approval_mode=mode)
    assert _cards(rt, s.id) == [LREAD]
    assert client.calls == []


async def test_a_run_nobody_can_answer_refuses_it(rt):  # noqa: F811
    client = _locked_tools(rt.platform)
    s = await _run(rt, [_calls(LDRAFT, "m1")], allow=HANDED, origin=None)
    assert _cards(rt, s.id) == []
    assert client.calls == []
    (row,) = [r for r in _rows(rt, s.id) if r.tool == LDRAFT]
    assert row.ok is False and row.output
