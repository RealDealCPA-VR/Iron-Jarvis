"""v1.357.0 (G84) review pins — two defects the lane's reviewer found.

1. The built-in EXAMPLE hookup (``rest_api``, "Generic REST API") is always
   registered, so its id words "REST API" ask-armed ``rest_call`` for ANY
   message that said them ("How do I build a REST API in Flask?") on an
   install where the user had saved no hookup at all — the coordinator's rule
   is "when the user names a CONFIGURED hookup".
2. The tool's spec lists each hookup as ``"<display name> (<id>)"``; a model
   that copied that exact line got "No saved web service is called ..." —
   AFTER the user had already approved the card.

Both pins drive the real code: the real app factory, the real stream chat
lane (only the model scripted), the real registry gate, and the lane's fake
CRM (a real HTTP server on this machine).
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.integrations.hookups import hookups_named
from tests import test_rest_call_v13570 as _lane
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_rest_call_v13570 import ASK, IID, NAME, _add_hookup, _ctx, _scripted

#: The lane's fake CRM fixture (a real HTTP server on this machine).
crm = _lane.crm

DEV_QUESTION = "How do I build a REST API in Flask?"


async def _no_card(data):
    raise AssertionError(f"no card expected: {data}")


@pytest.mark.timeout(120)
async def test_saying_rest_api_arms_nothing_when_no_hookup_is_saved(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    # The example hookup is registered on every install (the trap).
    assert p.integrations.get_spec("rest_api") is not None
    assert hookups_named(p.integrations, DEV_QUESTION) == []
    assert hookups_named(p.integrations, "write a rest-api client for me") == []
    seen = _scripted(p, monkeypatch, [])
    await _drive_stream(app, {"messages": [{"role": "user", "content": DEV_QUESTION}], "auto_tools": True}, _no_card)
    assert "rest_call" not in seen["tools"][0], "a developer's question armed the hookup tool"
    # Control: once the user saves a hookup and names it, the same lane arms it.
    await _add_hookup(app, crm.base)
    seen = _scripted(p, monkeypatch, [])
    await _drive_stream(app, {"messages": [{"role": "user", "content": ASK}], "auto_tools": True}, _no_card)
    assert "rest_call" in seen["tools"][0]


@pytest.mark.timeout(120)
async def test_the_name_the_spec_lists_is_the_name_the_tool_finds(tmp_path, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    tool = p.registry.get("rest_call")
    listed = f"{NAME} ({IID})"
    assert listed in tool.spec()["description"], "the spec lists the hookup in this form"
    ran = await p.registry.invoke(
        "rest_call",
        {"service": listed, "path": "/contacts/42"},
        _ctx(p, tmp_path),
        p.permissions,
        session_allow={"rest_call"},
    )
    assert ran.ok is True, ran.error
    assert "Ada Lovelace" in ran.output
    assert [(m, pth) for m, pth, _a, _b in crm.seen] == [("GET", "/api/contacts/42")]
    # A listed line naming an id nobody saved is still refused, in words.
    res = await tool.execute({"service": "Initech (initech)", "path": "/x"}, _ctx(p, tmp_path))
    assert res.ok is False and "No saved web service" in res.error
