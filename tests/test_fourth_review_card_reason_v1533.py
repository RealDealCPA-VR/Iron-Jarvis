"""Fourth fix plan, group 25 (PG-A and C-PG-2): a card that asks every time
says the TRUE reason.

THE DEFECT. The stream lane set ``once_reason`` on a once-only card only when
the tool's own check named one. A Local only card, a locked app's card, and a
card whose check named no reason (a live-Excel deletion, the plan door) all
went out with none, and the card's fallback line is "This chat is local only,
so this asks every time." — so a locked app's card, or one asked for its own
reason, said "local only" in a chat that is not Local only. The frame's
``forced`` was only an argument to the ``why`` sentence, never a field.

THE RULE. Every once-only card frame carries ``once_reason``: the check's own
reason first (as since v1.381.0, also under Local only), else ``local_only``,
``looked_up``, ``night``, ``locked``, else ``once``. Only a Local only card
says ``local_only``. The registry files the same kind (``once_kind``).

Proved through the real route (``/chat/stream``), the real registry, the real
approvals and each real tool's own check; only the model (and a locked app's
transport) are stand-ins.
"""

from __future__ import annotations

import pytest

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core import auto_approve as A
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.tools.base import SpendCheck, Tool, ToolContext, ToolResult
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _calls_stream

APP = "mailbox"
LOCKED_TOOL = f"mcp__{APP}__create_draft"
NAV = {"url": "https://example.com/"}
EXCEL_LIKE = "sheet_rows_delete"


class _Client:
    """A locked app's transport: never reached (every card is denied)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(self, name, args):  # pragma: no cover - denied first
        self.calls.append(name)
        return {"content": [{"type": "text", "text": "answered"}]}


class _OwnCheckNoReason(Tool):
    """Shaped like the live-Excel deletion: its own check asks every time
    (``once_only``) with words of its own and NO reason."""

    name = EXCEL_LIKE
    description = "delete rows from the report open in a spreadsheet"
    input_schema = {"type": "object", "properties": {"rows": {"type": "string"}}}

    def __init__(self) -> None:
        self.ran = 0

    async def spend_check(self, args, ctx):
        return SpendCheck(ask=True, once_only=True, irreversible=True,
                          words="Delete rows 2 to 9 of the report open in the spreadsheet")

    async def execute(self, args, ctx):  # pragma: no cover - denied first
        self.ran += 1
        return ToolResult(ok=True, output="deleted")


def _app(tmp_path, specs):
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream(specs)
    p.outbound_webhooks.allow_internal = True
    p.outbound_webhooks.http_post = lambda *a, **k: None
    p.config.browser_access = "interactive"
    return app


async def _cards_for(app, body: dict, decision: str = "deny") -> list[dict]:
    seen: list[dict] = []
    approvals = chat_routes._approvals(type("D", (), {"platform": app.state.platform})())

    async def decide(data):
        card = dict(data)
        # The registry's own mark for the same card (fourth review BS1).
        card["_kind"] = approvals.once_kind(data["id"])
        seen.append(card)
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": decision})
        assert status == 200

    await _drive_stream(app, body, decide)
    return seen


def _body(text: str, tools: list[str], *, mode: str = "approve_for_me", **extra) -> dict:
    return {"messages": [{"role": "user", "content": text}], "auto_tools": True,
            "approval_mode": mode, "tools": tools, **extra}


def _not_local_only_line(card: dict) -> None:
    """A card outside Local only never carries the Local only reason, and
    always carries one (so the screen never falls back to the Local only
    line)."""
    reason = card.get("once_reason")
    assert isinstance(reason, str) and reason, f"a once-only card went out with no reason: {card}"
    assert reason != "local_only", card
    assert card.get("_kind") == reason, (card.get("_kind"), reason)


# --------------------------------------------------------------------------- #
# Local only
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_local_only_card_carries_local_only(tmp_path):
    app = _app(tmp_path, [("browser_navigate", dict(NAV))])
    cards = await _cards_for(app, _body("open the page", ["browser_navigate"], mode="yolo", local_only=True))
    assert [c["tool"] for c in cards] == ["browser_navigate"], cards
    card = cards[0]
    assert card.get("once_only") is True, card
    assert card.get("once_reason") == "local_only", card
    assert card.get("_kind") == "local_only"


@pytest.mark.asyncio
async def test_control_outside_local_only_the_same_call_has_no_once_only_card(tmp_path):
    app = _app(tmp_path, [("browser_navigate", dict(NAV))])
    cards = await _cards_for(app, _body("open the page", ["browser_navigate"], mode="yolo"))
    assert all(c.get("once_only") is not True for c in cards), cards
    assert all("once_reason" not in c for c in cards), cards


# --------------------------------------------------------------------------- #
# Outside Local only: each card says its own reason, never "local_only"
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_new_outgoing_webhook_card_carries_address(tmp_path):
    app = _app(tmp_path, [("webhook_add", {
        "slug": "deploys", "direction": "outbound",
        "target_url": "https://hooks.example.test/one", "event_types": ["session.completed"],
    })])
    cards = await _cards_for(app, _body("send deploys to my hook", ["webhook_add"],
                                        granted_tools=["webhook_add"]))
    assert [c["tool"] for c in cards] == ["webhook_add"], cards
    assert cards[0].get("once_only") is True, cards[0]
    assert cards[0].get("once_reason") == "address", cards[0]
    _not_local_only_line(cards[0])


@pytest.mark.asyncio
async def test_a_locked_apps_card_carries_locked(tmp_path):
    app = _app(tmp_path, [(LOCKED_TOOL, {})])
    p = app.state.platform
    client = _Client()
    tool = MCPRemoteTool.from_spec(client, APP, {
        "name": "create_draft", "inputSchema": {"type": "object", "properties": {}}, "annotations": {},
    })
    tool.locked = True  # what mcp/tools.mcp_tools sets for a locked row
    assert tool.local_only is False  # NOT kept on the user's own models
    p.registry.register(tool, mcp=True)
    cards = await _cards_for(app, {
        "messages": [{"role": "user", "content": "draft a reply"}], "approval_mode": "approve_for_me",
        "connectors": [APP],
    })
    assert [c["tool"] for c in cards] == [LOCKED_TOOL], cards
    assert cards[0].get("once_only") is True, cards[0]
    assert cards[0].get("once_reason") == "locked", cards[0]
    assert cards[0].get("why") == A.ASK_RULES["locked"], cards[0].get("why")
    _not_local_only_line(cards[0])
    assert client.calls == []


@pytest.mark.asyncio
async def test_an_apply_changes_card_carries_its_reason(tmp_path):
    app = _app(tmp_path, [("apply_changes", {
        "changes": [{"tool": "write_file", "args": {"path": "notes.txt", "content": "hello"}}],
    })])
    cards = await _cards_for(app, _body("write the note", ["apply_changes"]))
    assert [c["tool"] for c in cards] == ["apply_changes"], cards
    assert cards[0].get("once_only") is True, cards[0]
    assert cards[0].get("once_reason") == "batch", cards[0]
    _not_local_only_line(cards[0])


@pytest.mark.asyncio
async def test_a_check_that_asks_every_time_with_no_reason_carries_once(tmp_path):
    app = _app(tmp_path, [(EXCEL_LIKE, {"rows": "2-9"})])
    tool = _OwnCheckNoReason()
    app.state.platform.registry.register(tool)
    cards = await _cards_for(app, _body("delete those rows", [EXCEL_LIKE]))
    assert [c["tool"] for c in cards] == [EXCEL_LIKE], cards
    assert cards[0].get("once_only") is True, cards[0]
    assert cards[0].get("once_reason") == "once", cards[0]
    _not_local_only_line(cards[0])
    assert tool.ran == 0


@pytest.mark.asyncio
async def test_under_local_only_a_checks_own_reason_still_wins(tmp_path):
    """v1.381.0 kept: under Local only a check's own reason is the one sent."""
    app = _app(tmp_path, [("apply_changes", {
        "changes": [{"tool": "write_file", "args": {"path": "notes.txt", "content": "hello"}}],
    })])
    cards = await _cards_for(app, _body("write the note", ["apply_changes"], local_only=True))
    assert [c["tool"] for c in cards] == ["apply_changes"], cards
    assert cards[0].get("once_reason") == "batch", cards[0]


# --------------------------------------------------------------------------- #
# The words a card's `why` and the POST lane read for the new reasons
# --------------------------------------------------------------------------- #
def test_the_new_reasons_have_words():
    for reason in ("address", "extension"):
        words = A.ONCE_REASON_WHY.get(reason, "")
        assert words and "local only" not in words.lower(), reason
        assert A.card_why(once_reason=reason) == words
        assert "tool server" not in words  # VOCABULARY.md: "extension"


@pytest.mark.asyncio
async def test_adding_an_extension_asks_for_the_extension_reason(tmp_path):
    app = create_app(str(tmp_path))
    tool = app.state.platform.registry.get("mcp_server_add")
    assert tool is not None
    ctx = ToolContext(workspace=tmp_path, session_id="t", agent_run_id="t",
                      config=app.state.platform.config, event_bus=app.state.platform.event_bus,
                      engine=app.state.platform.engine)
    check = await tool.spend_check({"config": "https://tools.example.test/mcp", "name": "lookup"}, ctx)
    assert check is not None and check.ask and check.once_only, check
    assert check.reason == "extension", check
