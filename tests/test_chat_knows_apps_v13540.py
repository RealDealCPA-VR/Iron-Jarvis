"""v1.354.0 (G39) — chat knows the apps by name, and what is connected.

THE GAP (product-readiness audit, sections F/O/C): chat recognised an app
only through a short word list, so "check my Gmail", "open my inbox" and "is
github connected?" armed nothing; "search my Google Drive for the lease"
armed WEB search; the model was never told what is connected; and a
connected app's tools reached chat only through the "+" toggle.

ACCEPTANCE: with GitHub connected but not toggled, "list my open PRs on
GitHub" shows a GitHub approval card and answers; "search my Google Drive
for the lease" with Drive not set up says Drive is not connected and offers
to connect it instead of searching the web.

Every pin drives the REAL code: the catalog-built matcher, the real
autoselect, the real app factory's two chat lanes (only the model is
scripted), and a REAL local MCP child standing in for GitHub's package (only
its launch target changes — the v1.351.0 G15 fixture).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog
from iron_jarvis.connectors.app_names import apps_named, clis_named
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import unload_pack_tools
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.settings import records
from iron_jarvis.settings.records import record_tools_for
from iron_jarvis.tools.autoselect import select_auto_tools
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

FIXTURE = str(Path(__file__).parent / "fixtures" / "argv_mcp_server_v13510.py")
TOKEN = "ghp_LANE4A0123456789abcdefNEVERSHOWN"
SECTION = "# Apps named in this message"


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        event, data = None, None
        for line in block.strip().splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data if isinstance(data, dict) else {}))
    return out


def _tool_names(tools) -> list[str]:
    return [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools or []]


def _scripted(platform, monkeypatch, calls: list[tuple[str, dict]], final: str = "Done."):
    """ONE fake model for BOTH lanes: round n calls ``calls[n]``, then answers
    ``final``. Records each round's system prompt and the tools it was shown."""
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


# --------------------------------------------------------------------------- #
# The matcher: the catalog's own names, the words people use, never a guess.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, want",
    [
        ("list my open PRs on GitHub", ["github"]),
        ("search my Google Drive for the lease", ["google_drive"]),
        ("check my Gmail", ["gmail"]),
        ("open my inbox", ["gmail"]),
        ("is github connected?", ["github"]),
        ("search my Notion for the meeting notes", ["notion"]),
        ("post it to Slack", ["slack"]),
        ("my dropbox has the scans", ["dropbox"]),
        ("look in my OneDrive", ["onedrive"]),
        ("connect my Box account", ["box"]),
        ("set up sequential thinking", ["sequential_thinking"]),
        ("charge the customer in Stripe", ["stripe"]),
    ],
)
def test_the_matcher_finds_the_app_by_the_words_people_use(text, want):
    assert apps_named(text) == want


@pytest.mark.parametrize(
    "text",
    [
        "check my Outlook inbox",  # another mail service, not Gmail
        "what is my email address",
        "pause my inbox goal",  # a record of the user's, not the mailbox
        "I have a notion that this is wrong",
        "cut me some slack",
        "pick up the slack on the report",  # v1.354.0 review: the idiom
        "put it in a box",  # a generic name needs a connect verb
        "add memory about me",
        "the stripe on the flag",
        "what is 2 + 2",
    ],
)
def test_the_matcher_does_not_name_an_app_in_everyday_words(text):
    assert apps_named(text) == []


def test_claude_code_and_codex_are_named_as_sign_ins_not_apps():
    assert clis_named("is Claude Code signed in?") == ["claude-cli"]
    assert clis_named("use codex for this") == ["codex-cli"]
    assert apps_named("is Claude Code signed in?") == []


def test_a_named_app_arms_app_connect_and_a_question_still_arms_no_writer():
    assert record_tools_for("check my Gmail") == ["app_connect"]
    assert record_tools_for("open my inbox") == ["app_connect"]
    assert record_tools_for("is github connected?") == [], "a question is not consent to a writer"
    assert "app_connect" not in record_tools_for("pause my inbox goal")


# --------------------------------------------------------------------------- #
# "search my Google Drive" is not a web search.
# --------------------------------------------------------------------------- #
def test_the_web_rule_does_not_fire_when_its_only_signal_is_an_apps_name():
    for text in (
        "search my Google Drive for the lease",
        "search github for issues about login",
        "look up the invoice in my Dropbox",
        "search for the lease in my Google Drive",
    ):
        got = select_auto_tools(text)
        assert "web_search" not in got and "web_fetch" not in got, (text, got)
    # A real web word still makes it a web search, app named or not.
    for text in (
        "search the web and my Notion for the lease",
        "look up the latest news on GitHub Copilot",
        "search google for pizza near me",
        "search for the lease",
    ):
        assert "web_search" in select_auto_tools(text), text
    # v1.354.0 review: a search verb aimed at the WEB keeps the web even when
    # an app is named elsewhere as the destination (before the review these
    # armed no web tool at all — a regression on everyday requests).
    for text in (
        "search for flights to Paris and save them to my Notion",
        "look up the best laptops and post them in Slack",
        "browse for cheap hotels then email the list to my Gmail",
    ):
        assert "web_search" in select_auto_tools(text), text


# --------------------------------------------------------------------------- #
# ACCEPTANCE 2: Drive not set up -> said so, offered, never a web search.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["post", "stream"])
def test_drive_not_set_up_is_said_so_in_both_lanes_and_the_web_is_not_offered(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path)))
    seen = _scripted(client.app.state.platform, monkeypatch, [])
    body = {"messages": [{"role": "user", "content": "search my Google Drive for the lease"}], "auto_tools": True}
    r = client.post("/chat" if lane == "post" else "/chat/stream", json=body)
    assert r.status_code == 200, r.text
    section = _section(seen["systems"][0])
    assert "- Google Drive: not connected." in section
    assert "It needs a one-time Google developer setup first." in section
    assert "offer to connect it" in section and "Never search the web in its place." in section
    tools = seen["tools"][0]
    assert "web_search" not in tools and "web_fetch" not in tools, tools
    if lane == "stream":
        assert "app_connect" in tools, "the offer to connect is a tool the model can call"


def test_the_section_is_the_same_bytes_in_both_lanes(tmp_path, monkeypatch):
    sections = {}
    for lane in ("post", "stream"):
        client = TestClient(create_app(str(tmp_path / lane)))
        seen = _scripted(client.app.state.platform, monkeypatch, [])
        body = {"messages": [{"role": "user", "content": "search my Google Drive for the lease"}]}
        assert client.post("/chat" if lane == "post" else "/chat/stream", json=body).status_code == 200
        sections[lane] = _section(seen["systems"][0])
    assert sections["post"] == sections["stream"]


def test_a_message_that_names_no_app_adds_nothing_to_the_prompt(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    seen = _scripted(client.app.state.platform, monkeypatch, [])
    assert client.post("/chat/stream", json={"messages": [{"role": "user", "content": "what is 2 + 2"}]}).status_code == 200
    assert SECTION not in seen["systems"][0]


def test_the_drive_offer_ends_in_its_connect_door_with_the_setup_said(tmp_path, monkeypatch):
    """The model takes the offer: app_connect(google_drive) answers where it is
    connected and the one-time setup it needs, and the reply carries the
    "Connect Google Drive in Settings" door (v1.353.0 G41's door, reused)."""
    client = TestClient(create_app(str(tmp_path)))
    _scripted(client.app.state.platform, monkeypatch, [("app_connect", {"app": "google_drive"})],
              final="Google Drive isn't connected yet. Want to connect it?")
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "search my Google Drive for the lease"}],
        "auto_tools": True,
    })
    frames = _parse_sse(r.text)
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("name") == "app_connect" and d.get("status") == "finished"]
    assert finished and finished[0]["ok"] is False
    assert "It needs a one-time Google developer setup first." in finished[0]["output"]
    done = next(d for ev, d in frames if ev == "done")
    assert any(dr.get("label") == "Connect Google Drive in Settings" for dr in done.get("doors") or []), done.get("doors")
    assert "web_search" not in (done.get("tools_used") or [])


# --------------------------------------------------------------------------- #
# ACCEPTANCE 1: GitHub connected, NOT toggled -> its card, then the answer.
# --------------------------------------------------------------------------- #
@pytest.fixture
def local_github(monkeypatch):
    real = catalog.get_connector("github")
    # v1.355.0 (G44): the GitHub card is remote now; the local child makes it
    # a program this PC runs (transport stdio), only the launch target changes.
    swapped = dataclasses.replace(real, command=sys.executable, args=[FIXTURE], transport="stdio", url="", auth_field="")
    original = catalog.get_connector
    monkeypatch.setattr(catalog, "get_connector", lambda c: swapped if c == "github" else original(c))
    return swapped


@pytest.mark.timeout(120)
async def test_github_connected_but_not_toggled_shows_its_card_and_answers(tmp_path, monkeypatch, local_github):
    app = create_app(str(tmp_path))
    p = app.state.platform
    try:
        # Connect it the way chat does (app_connect + the secure card's save).
        await records.AppConnectTool(p).execute({"app": "github"}, None)
        status, raw = await _asgi_post(app, "/config/secret", {"name": "app.github", "value": TOKEN})
        assert status == 200 and json.loads(raw)["tools_loaded"] == 1, raw
        assert p.registry.mcp_names("github") == ["mcp__github__argv"]

        seen = _scripted(p, monkeypatch, [("mcp__github__argv", {})], final="You have no open PRs.")
        cards: list[dict] = []

        async def approve(data):
            cards.append(data)
            st, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
            assert st == 200

        # NO `connectors` on the body: the "+" toggle is off.
        frames = await _drive_stream(app, {
            "messages": [{"role": "user", "content": "list my open PRs on GitHub"}],
            "auto_tools": True,
        }, approve)

        # The model was told GitHub is connected and shown its tools …
        assert "- GitHub: connected (1 tool)." in _section(seen["systems"][0])
        assert "mcp__github__argv" in seen["tools"][0]
        # … not a pointless "connect GitHub" (it is connected already).
        assert "app_connect" not in seen["tools"][0]
        # The call SHOWED A CARD (visible, never granted) and then ran.
        assert [c["tool"] for c in cards] == ["mcp__github__argv"], cards
        finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
        assert finished and finished[0]["name"] == "mcp__github__argv" and finished[0]["ok"] is True
        done = next(d for ev, d in frames if ev == "done")
        assert done["reply"].startswith("You have no open PRs.")
        assert TOKEN not in json.dumps(frames)
    finally:
        unload_pack_tools(p.registry, "github")


@pytest.mark.timeout(120)
async def test_asking_to_connect_a_connected_app_keeps_app_connect(tmp_path, monkeypatch, local_github):
    app = create_app(str(tmp_path))
    p = app.state.platform
    try:
        await records.AppConnectTool(p).execute({"app": "github"}, None)
        await _asgi_post(app, "/config/secret", {"name": "app.github", "value": TOKEN})
        seen = _scripted(p, monkeypatch, [])

        async def no_card(data):
            raise AssertionError(f"no call was scripted, yet a card asked: {data}")

        await _drive_stream(app, {
            "messages": [{"role": "user", "content": "my GitHub token expired, reconnect it"}],
            "auto_tools": True,
        }, no_card)
        assert "app_connect" in seen["tools"][0], "a re-sign-in still has its door"
    finally:
        unload_pack_tools(p.registry, "github")


def test_a_low_trust_or_local_only_turn_arms_no_named_app_tools():
    g = SimpleNamespace(apps_named=["github"], apps_live={"github": ["mcp__github__a", "mcp__github__b"]})
    add, drop = chat_turn.named_app_ask_tools(g, "list my PRs on GitHub", [], [], low_trust=False, local_only=False)
    assert add == ["mcp__github__a", "mcp__github__b"] and drop is True
    for kw in ({"low_trust": True, "local_only": False}, {"low_trust": False, "local_only": True}):
        add, _ = chat_turn.named_app_ask_tools(g, "list my PRs on GitHub", [], [], **kw)
        assert add == [], kw
    # Already armed (the "+" toggle) is not armed twice; a connect word keeps app_connect.
    add, drop = chat_turn.named_app_ask_tools(g, "reconnect GitHub", ["mcp__github__a"], [], low_trust=False, local_only=False)
    assert add == ["mcp__github__b"] and drop is False
    # An app that is not connected keeps app_connect (it is the offer).
    g2 = SimpleNamespace(apps_named=["google_drive"], apps_live={})
    assert chat_turn.named_app_ask_tools(g2, "search my drive", [], [], low_trust=False, local_only=False) == ([], False)


# --------------------------------------------------------------------------- #
# Claude Code / Codex readiness, from the cached verdict (no probe).
# --------------------------------------------------------------------------- #
def test_claude_code_readiness_comes_from_the_cached_status(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    asked: list[str] = []

    def cached(name):
        asked.append(name)
        return {"installed": True, "signed_in": False, "detail": "not logged in"}

    monkeypatch.setattr(p.providers, "cli_login_status", cached)
    seen = _scripted(p, monkeypatch, [])
    assert client.post("/chat", json={"messages": [{"role": "user", "content": "is Claude Code signed in?"}]}).status_code == 200
    section = _section(seen["systems"][0])
    assert "- Claude Code: installed but not signed in" in section
    assert "claude-cli" not in section
    assert asked == ["claude-cli"]
