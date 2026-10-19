"""v1.359.0 lane 9C (G41 remainder) — NAMING AN APP THAT IS NOT CONNECTED
ALWAYS OFFERS ITS CONNECT DOOR.

Before this, the "Connect Google Drive in Settings" button under a reply
existed only when the MODEL chose to call ``app_connect`` (the grounding asks
it to "offer to connect it", and a model that offered in words left no
button). Now the turn itself reads the user's message with the apps matcher
(``connectors.app_names`` — the same matcher the "Apps named in this message"
section uses) and, for every catalog app the message names for the user's
OWN data that is NOT connected, the done frame / POST response carries that
app's Connect door through the existing doors mechanism (``daemon/doors``):
the Directory's address and the words the fix door has had since v1.353.0,
one door for every app named, each app once, never when ``app_connect``
already put its own card or answer up for that app. Research ABOUT an app
(the wave-8 research-vs-own-data rule — "look up the Stripe API docs")
offers nothing. Low-trust and local-only turns get the door too: it only
links.

The model here is the only double, and it NEVER calls ``app_connect``: the
door comes from the real lanes, the real grounding hop, the real catalog and
connection state of a fresh install.

THE CONTRACT FIXTURE: the done frame the REAL daemon sends for the acceptance
turn, read by ``dashboard/__tests__/connect-door-v13590.test.tsx`` through
the real stream decode and the real DoorsStrip. Regenerate with
``IJ_REGEN_CONNECT_DOOR=1 uv run pytest tests/test_connect_door_v13590.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.connectors.app_names import connect_offers, own_data_apps
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.doors import (
    MAX_DOORS,
    collect_doors,
    connect_label,
    failure_door_for,
    offered_app,
    with_connect_door,
)
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult

FIXTURE = (
    Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "connect-door-v13590.json"
)
REGEN = os.environ.get("IJ_REGEN_CONNECT_DOOR") == "1"

APPS_HREF = "/settings?section=connections-apps"
DRIVE_DOOR = {"href": APPS_HREF, "label": "Connect Google Drive in Settings"}
ACCEPTANCE = "find my tax files in Google Drive"


def _check_fixture(section: str, actual: Any) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if REGEN:
        data["_comment"] = (
            "v1.359.0 lane 9C: what the REAL daemon sends when the user names an app "
            "that is not connected and the model never calls app_connect. Written by "
            "tests/test_connect_door_v13590.py (IJ_REGEN_CONNECT_DOOR=1); read by "
            "dashboard/__tests__/connect-door-v13590.test.tsx."
        )
        data[section] = actual
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert section in data, f"fixture has no {section!r}: regenerate it"
    assert data[section] == actual, f"the daemon's {section!r} no longer matches the dashboard's fixture"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # A developer's own keys must never make the fresh install look connected.
    for var in ("PIXIO_API_KEY", "OPENAI_API_KEY", "IRONJARVIS_MEDIA_FAKE"):
        monkeypatch.delenv(var, raising=False)
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


# --------------------------------------------------------------------------- #
# harness — the model is the only double
# --------------------------------------------------------------------------- #


def _final(text: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls or []), usage={})


class _StreamModel:
    """The stream lane's model: plays ``rounds`` (tool-call lists); an empty
    list (or running out) answers in words only — it never calls a tool
    unless a round says so."""

    def __init__(self, rounds: list[list[ToolCall]] | None = None, text: str = "I can't see your Drive yet.") -> None:
        self.rounds = list(rounds or [])
        self.text = text
        self.n = 0
        self.tools: list[list[str]] = []
        self.called: list[str] = []

    async def __call__(self, *args: Any, **kw: Any):
        self.tools.append([t.get("name") for t in kw.get("tools") or []])
        i = self.n
        self.n += 1
        calls = self.rounds[i] if i < len(self.rounds) else []
        if calls:
            self.called += [c.name for c in calls]
            yield {"type": "final", "response": _final("", calls), "provider": "mock", "model": "mock"}
            return
        yield {"type": "text", "text": self.text}
        yield {"type": "final", "response": _final(self.text), "provider": "mock", "model": "mock"}


class _PostModel:
    """The POST lane's router double, same contract as :class:`_StreamModel`."""

    default_provider = "mock"

    def __init__(self, rounds: list[list[ToolCall]] | None = None, text: str = "I can't see your Drive yet.") -> None:
        self.rounds = list(rounds or [])
        self.text = text
        self.n = 0
        self.tools: list[list[str]] = []
        self.called: list[str] = []

    async def complete(self, **kw: Any) -> RouteResult:
        self.tools.append([t.get("name") for t in kw.get("tools") or []])
        i = self.n
        self.n += 1
        calls = self.rounds[i] if i < len(self.rounds) else []
        if calls:
            self.called += [c.name for c in calls]
            return RouteResult(_final("", calls), "mock", "mock")
        return RouteResult(_final(self.text), "mock", "mock")


def _frames(chunk: Any) -> list[tuple[str, dict]]:
    text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
    out = []
    for block in text.split("\n\n"):
        ev, data = "", None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data or {}))
    return out


def _stream(app, body: dict, *, answer: str = "once", cards: list | None = None, **kw: Any):
    platform = app.state.platform
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))

    async def go():
        gen = await chat_routes.stream_chat_turn(platform, {}, ChatBody(**body), **kw)
        seen = []
        async for chunk in gen:
            for ev, data in _frames(chunk):
                seen.append((ev, data))
                if ev == "approval":
                    if cards is not None:
                        cards.append(data)
                    approvals.resolve(data["id"], answer)
        return seen

    return asyncio.run(asyncio.wait_for(go(), 30))


def _done(frames) -> dict:
    return [d for ev, d in frames if ev == "done"][-1]


def _body(text: str, **extra: Any) -> dict:
    """A stream body as the chat page sends it (the Auto switch on)."""
    return {
        "messages": [{"role": "user", "content": text}],
        "tools": [], "approval_mode": "approve_for_me", "auto_tools": True, **extra,
    }


def _stream_doors(app, text: str, **kw: Any) -> tuple[list, _StreamModel]:
    model = _StreamModel()
    app.state.platform.router.stream = model
    frames = _stream(app, _body(text), **kw)
    assert model.called == [], "anti-vacuity: the model never called a tool"
    return _done(frames)["doors"], model


def _post_doors(app, text: str, **kw: Any) -> list:
    platform = app.state.platform
    model = _PostModel()
    platform.router = model
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    res = asyncio.run(run_chat_turn(platform, {}, body, **kw))
    assert model.called == [], "anti-vacuity: the model never called a tool"
    return res["doors"]


# =========================================================================== #
# 1. THE ACCEPTANCE: a fake model that never calls app_connect
# =========================================================================== #


def test_naming_drive_offers_its_connect_door_in_the_stream_lane(app):
    """'find my tax files in Google Drive' with Drive not connected: the
    reply ends with the Directory's Connect button although the model only
    answered in words."""
    model = _StreamModel()
    app.state.platform.router.stream = model
    frames = _stream(app, _body(ACCEPTANCE))
    done = _done(frames)
    assert model.called == []
    assert done["doors"] == [DRIVE_DOOR]
    assert "app_connect" not in done["tools_used"]
    # The SAME door (words and address) app_connect's own refusal leaves.
    from iron_jarvis.connectors.catalog import get_connector

    assert failure_door_for(
        "app_connect", {"app": "google_drive"}, SimpleNamespace(ok=False, data=None)
    ) == DRIVE_DOOR
    assert get_connector("google_drive").connect_via == "oauth"
    _check_fixture("stream_done", {k: done[k] for k in ("reply", "doors", "tools_used")})


def test_naming_drive_offers_its_connect_door_in_the_post_lane(app):
    """MIRROR NOTE (lock-step): the POST lane's response carries the same
    door."""
    assert _post_doors(app, ACCEPTANCE) == [DRIVE_DOOR]


LOCKSTEP = [
    ACCEPTANCE,
    "search my gmail for the invoice from acme",
    "post this summary to Slack",
    "copy the report from Dropbox to Google Drive",
    "look up the stripe api docs for refunds",
    "what can you do?",
]


@pytest.mark.parametrize("text", LOCKSTEP)
def test_both_lanes_offer_the_same_doors_for_the_same_message(app, text):
    stream, _ = _stream_doors(app, text)
    assert _post_doors(app, text) == stream


@pytest.mark.parametrize(
    "text, label",
    [
        ("search my gmail for the invoice from acme", "Connect Gmail in Settings"),
        ("post this summary to Slack", "Connect Slack in Settings"),
        ("create a GitHub issue for the login bug", "Connect GitHub in Settings"),
        ("look up the invoice in my Dropbox", "Connect Dropbox in Settings"),
        # The Directory card's own name (catalog.py), never a second spelling.
        ("what's on my calendar tomorrow?", "Connect Microsoft 365 (Outlook, Calendar, OneDrive) in Settings"),
        ("is my OneDrive connected?", "Connect OneDrive in Settings"),
    ],
)
def test_any_catalog_app_named_for_the_users_own_data_offers_its_door(app, text, label):
    doors, _ = _stream_doors(app, text)
    assert doors == [{"href": APPS_HREF, "label": label}]


def test_two_apps_named_share_one_door_each_named_once(app):
    """One address (the Directory), so one door naming both — never two links
    to the same place, and each app once."""
    doors, _ = _stream_doors(app, "copy the report from my Dropbox to my Google Drive, then my Dropbox again")
    assert doors == [{"href": APPS_HREF, "label": "Connect Google Drive and Dropbox in Settings"}]


# =========================================================================== #
# 2. RESEARCH ABOUT AN APP OFFERS NOTHING (wave 8's rule)
# =========================================================================== #

#: The wave-8 probe table's research rows (tests/test_chat_picks_tools_v13580)
#: plus the questions about an app itself.
RESEARCH = [
    "look up the stripe api docs for refunds",
    "search for notion templates for a CRM",
    "look up how to use github actions",
    "search for the dropbox desktop app download",
    "look up google maps api pricing",
    "search for brave search api pricing",
    "look up how to share a folder in my google drive",
    "look up how to archive a channel in slack",
    "look up reviews of notion",
    "search for alternatives to slack",
    "what's the latest news about github copilot",
    "find a good postgres tutorial online",
    "research stripe vs square fees",
    "what is Notion?",
    "does Notion have an API?",
    "is Dropbox better than Google Drive?",
    "tell me about Stripe",
    "search the web for Dropbox",
    # v1.359.0 review: the everyday comparison and price questions a new
    # user asks before choosing an app (each offered a door before the fix).
    "what's the difference between Dropbox and Google Drive",
    "should I use Dropbox or Google Drive?",
    "which is better, Notion or Obsidian?",
    "which one is cheaper, Dropbox or Box?",
    "how much does Dropbox cost?",
    "how much would Slack charge us for 10 seats",
    "who founded Dropbox",
    "who owns GitHub now?",
]

#: The wave-8 probe table's own-data rows plus G41's own phrasings.
OWN = [
    ("search my google drive for the lease", ["google_drive"]),
    ("search my gmail for the invoice from acme", ["gmail"]),
    ("look up the invoice in my Dropbox", ["dropbox"]),
    ("search for the lease in my Google Drive", ["google_drive"]),
    ("search github for issues about login", ["github"]),
    ("look up the refund in stripe", ["stripe"]),
    (ACCEPTANCE, ["google_drive"]),
    ("connect my Google Drive so you can find my tax files", ["google_drive"]),
    ("is my Google Drive connected?", ["google_drive"]),
    ("what is in my Dropbox?", ["dropbox"]),
    ("how do I connect Dropbox", ["dropbox"]),
    ("upload the latest report to Dropbox", ["dropbox"]),
    ("save this web page to Notion", ["notion"]),
    ("check my Outlook inbox", ["microsoft_365"]),
    # A possessive wins over a research word after it or a web word beside
    # it: "my Notion templates" are the user's, not Notion's.
    ("search my notion templates for the client one", ["notion"]),
    ("check my gmail online for the receipt", ["gmail"]),
    # v1.359.0 review: the comparison / price / company readings stay off the
    # user's own data — a possessive still wins, and a question about what is
    # IN the app is not a question about the app.
    ("should I use my Dropbox or my Google Drive for this?", ["google_drive", "dropbox"]),
    ("who created this file in my Dropbox?", ["dropbox"]),
    ("who created the folder in Dropbox?", ["dropbox"]),
    ("how much space is left in Dropbox?", ["dropbox"]),
    ("which folder in Dropbox has the best photos", ["dropbox"]),
    ("find the differences between the two leases in my Google Drive", ["google_drive"]),
]


@pytest.mark.parametrize("text", RESEARCH)
def test_research_about_an_app_is_never_an_offer(text):
    assert own_data_apps(text) == []


@pytest.mark.parametrize("text, ids", OWN)
def test_the_users_own_data_in_an_app_is_an_offer(text, ids):
    assert own_data_apps(text) == ids


@pytest.mark.parametrize(
    "text",
    ["look up the stripe api docs for refunds", "look up how to share a folder in my google drive", "what is Notion?"],
)
def test_research_offers_no_door_in_either_lane(app, text):
    stream, _ = _stream_doors(app, text)
    assert stream == []
    assert _post_doors(app, text) == []


def test_a_message_that_names_no_app_has_no_door(app):
    stream, _ = _stream_doors(app, "rewrite the email below so it is shorter: hi Bob, thanks")
    assert stream == []


# =========================================================================== #
# 3. ONLY "NOT CONNECTED" — never an app that is connected, off or signing in
# =========================================================================== #


def test_only_a_not_connected_row_is_an_offer():
    rows = [
        {"id": "google_drive", "name": "Google Drive", "state": "not_connected"},
        {"id": "github", "name": "GitHub", "state": "connected"},
        {"id": "slack", "name": "Slack", "state": "off"},
        {"id": "microsoft_365", "name": "Microsoft 365", "state": "signin"},
        {"id": "notion", "name": "Notion", "state": "not_working"},
        {"id": "claude-cli", "name": "Claude Code", "state": "signed_out"},
    ]
    text = "search my google drive, my github, my slack, my notion, my calendar and claude code"
    assert connect_offers(text, rows) == [{"id": "google_drive", "name": "Google Drive"}]
    # Research about the one not-connected app offers nothing.
    assert connect_offers("look up google drive api pricing", rows) == []
    # Never raises on junk.
    assert connect_offers(None, None) == []  # type: ignore[arg-type]
    assert connect_offers("my google drive", [None, "x", {"state": "not_connected"}]) == []  # type: ignore[list-item]


def test_a_connected_app_gets_no_door(app, monkeypatch):
    """The door is for an app that is NOT connected: a signed-in Drive (the
    Directory's own reading) offers nothing."""
    platform = app.state.platform
    real = platform.connections.status

    def _status():
        rows = list(real() or [])
        rows.append({"provider": "google_drive", "connected": True})
        return rows

    monkeypatch.setattr(platform.connections, "status", _status)
    doors, _ = _stream_doors(app, ACCEPTANCE)
    assert doors == []


# =========================================================================== #
# 4. NOT WHEN APP_CONNECT ALREADY OFFERED ITS OWN CARD
# =========================================================================== #


def test_pixio_named_offers_its_door_when_the_model_does_not_call_app_connect(app):
    """Anti-vacuity for the next test: the same message, a model that only
    answers in words, gets Pixio's Connect door."""
    doors, _ = _stream_doors(app, "connect my Pixio account")
    assert doors == [{"href": APPS_HREF, "label": "Connect Pixio in Settings"}]


def test_no_second_offer_when_app_connect_showed_its_own_key_card(app):
    """app_connect for Pixio RAN and put its secure key card up: that IS the
    offer, so the reply carries no second one (stream lane)."""
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "pixio"})], []])
    app.state.platform.router.stream = model
    frames = _stream(app, _body("connect my Pixio account"))
    done = _done(frames)
    assert "app_connect" in done["tools_used"], "anti-vacuity: app_connect ran"
    assert any(c.get("kind") == "secret" for c in done.get("config_cards") or []), done.get("config_cards")
    assert done["doors"] == []


def test_no_second_offer_when_app_connect_showed_its_own_key_card_post_lane(app):
    platform = app.state.platform
    platform.router = _PostModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "pixio"})], []])
    body = ChatBody(messages=[{"role": "user", "content": "connect my Pixio account"}], tools=["app_connect"])
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert "app_connect" in res["tools_used"], "anti-vacuity: app_connect ran"
    assert res["doors"] == []


def test_no_second_offer_when_the_user_declined_app_connects_card(app):
    """An MCP app's connect card was shown and the user said no: the card was
    the offer, and a declined card leaves no button (the v1.353.0 rule)."""
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "notion"})], []])
    app.state.platform.router.stream = model
    cards: list = []
    frames = _stream(app, _body("connect my Notion"), answer="deny", cards=cards)
    assert cards, "anti-vacuity: app_connect's card was shown"
    assert _done(frames)["doors"] == []


def test_a_drive_fix_door_and_another_named_app_are_one_door(app):
    """app_connect for Drive failed and left its fix door; the message also
    named Dropbox: ONE door names both, Drive once."""
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    app.state.platform.router.stream = model
    frames = _stream(app, _body("connect my Google Drive and my Dropbox"))
    assert "app_connect" in model.called
    assert _done(frames)["doors"] == [
        {"href": APPS_HREF, "label": "Connect Google Drive and Dropbox in Settings"}
    ]


def test_a_refused_app_connect_is_not_an_offer_so_the_door_stays(app):
    """app_connect not turned on for this chat (the registry refused it before
    anyone saw a card): the user still gets the door."""
    platform = app.state.platform
    platform.router = _PostModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    body = ChatBody(messages=[{"role": "user", "content": ACCEPTANCE}], tools=["read_file"])
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert "app_connect" not in platform.router.tools[0], "anti-vacuity: app_connect was not armed"
    assert res["doors"] == [DRIVE_DOOR]


# =========================================================================== #
# 5. LOW TRUST AND LOCAL ONLY STILL GET THE DOOR (it only links)
# =========================================================================== #


def test_a_low_trust_turn_still_gets_the_door(app):
    doors, _ = _stream_doors(app, ACCEPTANCE, trust="low", trust_reason="started from a channel")
    assert doors == [DRIVE_DOOR]


def test_a_low_trust_post_turn_still_gets_the_door(app):
    assert _post_doors(app, ACCEPTANCE, trust="low", trust_reason="started from a channel") == [DRIVE_DOOR]


def test_a_local_only_turn_still_gets_the_door(app):
    model = _StreamModel()
    app.state.platform.router.stream = model
    frames = _stream(app, _body(ACCEPTANCE, local_only=True))
    assert _done(frames)["doors"] == [DRIVE_DOOR]


# =========================================================================== #
# 6. THE DOOR HELPERS
# =========================================================================== #


def test_connect_label_words():
    assert connect_label(["Google Drive"]) == "Connect Google Drive in Settings"
    assert connect_label(["Dropbox", "Google Drive"]) == "Connect Dropbox and Google Drive in Settings"
    assert connect_label(["A", "B", "C"]) == "Connect A, B and C in Settings"
    assert connect_label(["A", "B", "C", "D", "E"]) == "Connect A, B and 3 more apps in Settings"
    assert connect_label([" Two\n lines "]) == "Connect Two lines in Settings"
    assert connect_label([]) == "Connect your apps in Settings"


def test_offered_app_reads_the_catalog_id_of_an_app_connect_call():
    assert offered_app("app_connect", {"app": "google_drive"}) == "google_drive"
    assert offered_app("app_connect", {"app": "Google Drive"}) == "google_drive"
    assert offered_app("app_connect", {"app": "gdrive"}) == "google_drive"
    assert offered_app("app_connect", {"app": "nothing-like-it"}) == ""
    assert offered_app("web_search", {"app": "google_drive"}) == ""
    assert offered_app("app_connect", None) == ""


def test_with_connect_door_adds_merges_skips_and_survives_the_cap():
    drive = {"id": "google_drive", "name": "Google Drive"}
    box = {"id": "dropbox", "name": "Dropbox"}
    wf = {"href": "/workflows", "label": "Open the canvas"}
    # adds after the turn's own doors
    assert collect_doors(with_connect_door([wf, None], [drive])) == [wf, DRIVE_DOOR]
    # nothing to offer: the entries come back as they were
    assert with_connect_door([wf], []) == [wf]
    # an app app_connect already offered is skipped
    assert collect_doors(with_connect_door([wf], [drive], ["google_drive"])) == [wf]
    # widened in place of the fix door, Drive named once
    out = collect_doors(with_connect_door([DRIVE_DOOR, wf], [drive, box], ["google_drive"]))
    assert out == [{"href": APPS_HREF, "label": "Connect Google Drive and Dropbox in Settings"}, wf]
    # always offered: past the cap it takes the last place
    many = [{"href": f"/p{i}", "label": str(i)} for i in range(MAX_DOORS)]
    capped = collect_doors(with_connect_door(many, [drive]))
    assert len(capped) == MAX_DOORS and capped[-1] == DRIVE_DOOR
    # never raises
    assert with_connect_door(None, [object()]) == []
