"""Readiness 12B (G39): from the PHONE, a connected app is said honestly.

THE GAP (0d.4, re-verified on v1.386.2): the phone's chat turn runs the POST
lane, which never ask-arms, at LOW trust. The model was told "- GitHub:
connected (1 tool)." and given none of GitHub's tools, so "list my open PRs on
GitHub" from the phone got a model that believed it could look — and could
not.

THE DECISION (Option B, with the evidence in the report): the phone keeps no
app tools, and says so plainly — in the model's prompt and in the answer.

* The POST lane cannot show a card, so every armed tool there is GRANTED
  (``chat_turn.run_chat_turn``: armed → ``overrides[name] = "allow"`` +
  ``session_allow``). Arming even a "read-only" pack tool on the phone would
  run it with no person's OK, while the desktop shows a card for every call —
  a remote sender would gain power the PC does not hand out.
* Low trust already refuses this exact class: ``rest_call`` (reading the
  user's records from a saved service with their saved key) is in
  ``LOW_TRUST_DENY`` reads included, and ``named_app_ask_tools`` arms no
  named app's tools in a low-trust turn.
* "Read-only" is the extension's own ``readOnlyHint`` — the extension's say,
  not ours.
* The phone cannot answer a card for its own chat turn: the poller awaits the
  turn INLINE under the sender's lock (``InboundPoller._handle``), so a turn
  waiting for "approve" would wait on the very poll that must read it.

So: the apps section tells the model the app's tools cannot be used from this
conversation, only from Chat on the PC; and when the message named the app
for the user's own data, a fixed note under the reply tells the user how. A
desktop chat is byte-for-byte unchanged; low trust's denies are unchanged.

Every pin drives the REAL inbound phone path (``InboundPoller.poll_once`` →
``run_chat_turn`` under ``phone_surface``), the real app factory, a REAL local
MCP child standing in for GitHub (the v1.351.0 fixture), and a real
Telegram channel whose HTTP transport is a recorder. Only the model is
scripted.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.comm import TelegramChannel
from iron_jarvis.connectors import catalog
from iron_jarvis.connectors.app_names import apps_section, phone_apps_note, phone_unreachable
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.mcp.tools import unload_pack_tools
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.failure_words import phone_surface
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.settings import records
from tests.test_chat_approvals_v1187 import _asgi_post

#: Readiness 12B review: no pin of this lane ever leaves this machine — the
#: guard refuses and NAMES any host a missed stand-in would have dialled
#: (tests/_fakes/no_network.py).
pytestmark = pytest.mark.usefixtures("no_network")

FIXTURE = str(Path(__file__).parent / "fixtures" / "argv_mcp_server_v13510.py")
TOKEN = "ghp_PHONE12B0123456789abcdefNEVERSHOWN"
SENDER = 4242
SECTION = "# Apps named in this message"
#: Every line the phone gets wears the sender's name (``InboundPoller.send_chunked``).
PREFIX = "Iron Jarvis: "
PHONE_LINE = (
    "- GitHub: connected (1 tool), but its tools can't be used in this conversation with the"
    " phone — only from Chat in Iron Jarvis on the PC."
)
NOTE = (
    "\n\n_Note: I can't use GitHub from this conversation with your phone. To have me use it,"
    " ask in Chat in Iron Jarvis on your PC._"
)


class FakeTelegram:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        offset = int(params.get("offset", 0) or 0)
        return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]}

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(dict(payload))
        return {"status_code": 200}

    def texts(self) -> list[str]:
        return [str(s.get("text")) for s in self.sent]


def _update(uid: int, text: str) -> dict[str, Any]:
    return {
        "update_id": uid,
        "message": {
            "text": text,
            "from": {"id": SENDER, "is_bot": False, "first_name": "V"},
            "chat": {"id": SENDER},
        },
    }


def _phone(platform, fake: FakeTelegram) -> None:
    platform.notifier.add_channel(
        "tg",
        TelegramChannel(
            {"token_secret": "tg", "inbound_enabled": True, "chat_enabled": True,
             "allowed_senders": [SENDER], "chat_id": SENDER},
            http_post=fake.post, http_get=fake.get,
            secret_resolver=lambda n: "BOT" if n == "tg" else None,
        ),
    )


def _tool_names(tools) -> list[str]:
    return [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools or []]


def _scripted(platform, monkeypatch, calls: list[tuple[str, dict]], final: str):
    seen: dict = {"systems": [], "tools": [], "n": 0}

    def _next() -> LLMResponse:
        n = seen["n"]
        seen["n"] += 1
        if n < len(calls):
            name, args = calls[n]
            return LLMResponse(text="", tool_calls=[ToolCall(id=f"c{n}", name=name, arguments=args)])
        return LLMResponse(text=final, tool_calls=[])

    async def fake_complete(*, system, messages, tools, **kw):
        seen["systems"].append(system)
        seen["tools"].append(_tool_names(tools))
        return RouteResult(_next(), "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        seen["systems"].append(system)
        seen["tools"].append(_tool_names(tools))
        resp = _next()
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return seen


def _section(system: str) -> str:
    at = system.find(SECTION)
    assert at >= 0, "the named-apps section never reached the prompt"
    end = system.find("\n\n", at + len(SECTION))
    return system[at:] if end < 0 else system[at:end]


@pytest.fixture
def local_github(monkeypatch):
    real = catalog.get_connector("github")
    swapped = dataclasses.replace(real, command=sys.executable, args=[FIXTURE], transport="stdio", url="", auth_field="")
    original = catalog.get_connector
    monkeypatch.setattr(catalog, "get_connector", lambda c: swapped if c == "github" else original(c))
    return swapped


async def _connected_github(tmp_path):
    """A real app with GitHub connected the way chat connects it."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    await records.AppConnectTool(p).execute({"app": "github"}, None)
    status, raw = await _asgi_post(app, "/config/secret", {"name": "app.github", "value": TOKEN})
    assert status == 200 and json.loads(raw)["tools_loaded"] == 1, raw
    assert p.registry.mcp_names("github") == ["mcp__github__argv"]
    return app, p


# --------------------------------------------------------------------------- #
# The acceptance: from the phone, the truth — in the prompt and in the answer.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
async def test_from_the_phone_a_connected_app_is_said_plainly_and_never_armed(tmp_path, monkeypatch, local_github):
    app, p = await _connected_github(tmp_path)
    try:
        fake = FakeTelegram()
        _phone(p, fake)
        seen = _scripted(p, monkeypatch, [], final="I can't check your pull requests from here.")
        fake.updates.append(_update(1, "list my open PRs on GitHub"))
        res = await app.state.inbound_poller.poll_once()
        assert [r.get("status") for r in res] == ["chat"], res

        # The model was told the truth about THIS conversation …
        section = _section(seen["systems"][0])
        assert PHONE_LINE in section, section
        assert "a line under your reply" in section, section
        # … and given none of GitHub's tools (nothing is granted from here).
        assert not [t for t in seen["tools"][0] if t.startswith("mcp__github__")], seen["tools"][0]
        # The phone hears it in plain words, under the model's own answer.
        [sent] = fake.texts()
        assert sent == PREFIX + "I can't check your pull requests from here." + NOTE, sent
        # … and the conversation keeps it (the desktop shows the same line).
        store = app.state.comm_thread_store
        thread = store.resolve("tg", str(SENDER), "V")
        assert store.history_body(thread.id)[-1]["content"].endswith(NOTE)
        assert TOKEN not in json.dumps(fake.sent)
    finally:
        unload_pack_tools(p.registry, "github")


@pytest.mark.timeout(120)
async def test_a_desktop_reply_into_the_phone_thread_says_it_too(tmp_path, monkeypatch, local_github):
    """The other door that answers the phone (``POST /comm/threads/{id}/send``,
    the desktop composer of a phone conversation) runs the same lane under
    the same surface: same line, same note — the reply is delivered there."""
    app, p = await _connected_github(tmp_path)
    try:
        fake = FakeTelegram()
        _phone(p, fake)
        thread = app.state.comm_thread_store.resolve("tg", str(SENDER), "V")
        seen = _scripted(p, monkeypatch, [], final="Not from here.")
        status, raw = await _asgi_post(app, f"/comm/threads/{thread.id}/send", {"text": "list my open PRs on GitHub"})
        assert status == 200, raw
        assert PHONE_LINE in _section(seen["systems"][0])
        assert json.loads(raw)["reply"] == "Not from here." + NOTE
        assert fake.texts() == [PREFIX + "Not from here." + NOTE]
    finally:
        unload_pack_tools(p.registry, "github")


@pytest.mark.timeout(120)
async def test_a_question_about_the_app_itself_from_the_phone_gets_no_note(tmp_path, monkeypatch, local_github):
    """Research about GitHub (not the user's own data in it) needs none of
    its tools: the line still says what this conversation cannot do, and the
    reply carries no note (the matcher's own-data rule, the Connect door's)."""
    app, p = await _connected_github(tmp_path)
    try:
        fake = FakeTelegram()
        _phone(p, fake)
        seen = _scripted(p, monkeypatch, [], final="GitHub shipped a new code review.")
        fake.updates.append(_update(1, "what's new in github this week?"))
        await app.state.inbound_poller.poll_once()
        section = _section(seen["systems"][0])
        assert PHONE_LINE in section and "a line under your reply" not in section, section
        assert fake.texts() == [PREFIX + "GitHub shipped a new code review."]
    finally:
        unload_pack_tools(p.registry, "github")


# --------------------------------------------------------------------------- #
# Nothing else changed: the desktop chat, low trust, an escalation.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
@pytest.mark.parametrize("lane", ["post", "stream"])
async def test_the_desktop_chat_is_byte_for_byte_what_it_was(tmp_path, monkeypatch, local_github, lane):
    app, p = await _connected_github(tmp_path)
    try:
        seen = _scripted(p, monkeypatch, [], final="Here are your PRs.")
        body = {"messages": [{"role": "user", "content": "list my open PRs on GitHub"}], "auto_tools": True}
        if lane == "post":
            status, raw = await _asgi_post(app, "/chat", body)
            assert status == 200, raw
            assert NOTE not in json.loads(raw)["reply"]
        else:
            from tests.test_chat_approvals_v1187 import _drive_stream

            async def no_card(data):  # the scripted model calls nothing
                raise AssertionError(data)

            await _drive_stream(app, body, no_card)
            # The stream lane still SHOWS the app's tools (each call cards).
            assert "mcp__github__argv" in seen["tools"][0]
        section = _section(seen["systems"][0])
        assert "- GitHub: connected (1 tool)." in section, section
        assert "phone" not in section, section
    finally:
        unload_pack_tools(p.registry, "github")


async def test_an_escalating_phone_turn_carries_no_note(tmp_path, monkeypatch, local_github):
    """A turn that hands the work to an agent is answered by the agent's run
    (which can ask the phone before each use of the app) — its reply is not
    sent, and it carries no note."""
    app, p = await _connected_github(tmp_path)
    try:
        _scripted(p, monkeypatch, [("escalate_to_agent", {"reason": "needs GitHub"})], final="")
        body = ChatBody(messages=[{"role": "user", "content": "list my open PRs on GitHub"}], auto_tools=True)
        with phone_surface():
            out = await run_chat_turn(p, {}, body, trust="low", trust_reason="started from an inbound tg message")
        assert out["escalate"] is True
        assert NOTE not in out["reply"]
    finally:
        unload_pack_tools(p.registry, "github")


def test_low_trusts_denies_are_unchanged_and_hold_no_app_tool():
    """The decision widens nothing: no pack tool joined the deny list or left
    it, and the one service-reading tool stays kept away."""
    assert "rest_call" in LOW_TRUST_DENY and "app_connect" in LOW_TRUST_DENY
    assert not [t for t in LOW_TRUST_DENY if t.startswith("mcp__")]


# --------------------------------------------------------------------------- #
# The words, read directly.
# --------------------------------------------------------------------------- #
ROWS = [
    {"id": "github", "name": "GitHub", "state": "connected", "tools": 3, "needs": "", "note": ""},
    {"id": "gmail", "name": "Gmail", "state": "connected", "tools": 2, "needs": "", "note": ""},
    {"id": "google_drive", "name": "Google Drive", "state": "connected", "tools": 0, "needs": "", "note": ""},
    {"id": "notion", "name": "Notion", "state": "not_connected", "tools": 0, "needs": "", "note": ""},
]


def test_the_section_is_unchanged_off_the_phone():
    assert apps_section(ROWS) == apps_section(ROWS, phone_own=None)
    assert "phone" not in apps_section(ROWS)


def test_only_an_app_whose_tools_this_conversation_lacks_gets_the_phone_line():
    text = apps_section(ROWS, phone_own=["github"])
    lines = text.splitlines()
    gh = next(ln for ln in lines if ln.startswith("- GitHub:"))
    gm = next(ln for ln in lines if ln.startswith("- Gmail:"))
    dr = next(ln for ln in lines if ln.startswith("- Google Drive:"))
    no = next(ln for ln in lines if ln.startswith("- Notion:"))
    assert "can't be used in this conversation with the phone" in gh and "a line under your reply" in gh
    assert "can't be used in this conversation with the phone" in gm and "a line under your reply" not in gm
    # A drive signed in through the browser reaches chat through a memory
    # base — it is read on the phone too, so its line is not changed.
    assert dr == "- Google Drive: connected."
    assert no == next(ln for ln in apps_section(ROWS).splitlines() if ln.startswith("- Notion:"))


def test_phone_unreachable_is_the_own_data_rule_for_apps_with_tools():
    assert phone_unreachable("list my open PRs on GitHub", ROWS) == [{"id": "github", "name": "GitHub"}]
    assert phone_unreachable("check my gmail and my github", ROWS) == [
        {"id": "github", "name": "GitHub"}, {"id": "gmail", "name": "Gmail"},
    ]
    assert phone_unreachable("what's new in github this week?", ROWS) == []
    # A drive (no tools) or an app that is not connected is never one.
    assert phone_unreachable("search my google drive for the lease", ROWS) == []
    assert phone_unreachable("search my notion", ROWS) == []
    assert phone_unreachable(None, None) == []  # type: ignore[arg-type]


def test_the_note_names_each_app_once():
    assert phone_apps_note([]) == ""
    assert phone_apps_note(["GitHub"]) == NOTE
    assert phone_apps_note(["GitHub", "Gmail", "GitHub"]) == (
        "\n\n_Note: I can't use GitHub and Gmail from this conversation with your phone. To have"
        " me use them, ask in Chat in Iron Jarvis on your PC._"
    )
