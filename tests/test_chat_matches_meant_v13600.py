"""v1.360.0 lane 10B — CHAT OFFERS WHAT THE USER MEANT: the right app, the
right door, the right help (the final re-audit's regressions at v1.359.0).

(1) G41 regression: the model-independent Connect door read everyday and
    research sentences as the user's own data ("stripe the table rows"
    offered "Connect Stripe", "explain how postgres indexes work" offered
    Postgres, "the sentry stood guard at the gate in my story" offered Sentry,
    "Write a LinkedIn post about our GitHub launch" offered GitHub). The
    default is now NOT own: the door needs an own-data signal. Pinned by a
    side-by-side probe table (both directions, 20+ phrasings each way) and
    through BOTH real chat lanes.
(2) An Outlook (Microsoft 365) user who says "check my inbox" got "Connect
    Gmail" and "Gmail: not connected" while Microsoft 365's tools stayed
    unarmed. An everyday mail / calendar word now means the CONNECTED app of
    that kind; with none connected, mail is ONE door naming both choices and
    "my calendar" offers nothing (its only card is Microsoft 365, which the
    words did not name). Driven through the real app factory, with the real
    Microsoft 365 card loaded from its stand-in server.
(3) G38: the Guide's lookups armed for any how-to ("how do I connect my
    printer to wifi", "how do I send a wire transfer", "how do I install
    node"); now only for a question about Iron Jarvis. Probe table both ways.
(4) G21: a reply from the user's own model server (the custom endpoint)
    names the machine (``route.label`` = the label the user gave it), on both
    lanes; the registry's default label is not a name.

The model is the only double in the lane tests.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.connectors import app_names
from iron_jarvis.connectors.app_names import (
    apps_named,
    connect_offers,
    own_data_apps,
    resolve_kinds,
)
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.doors import with_connect_door
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import select_auto_tools

# The Microsoft 365 card's own harness: the pack's program is its stand-in
# server (it lists the REAL server's tool names). Fixtures are registered by
# import (the integrate tests' way).
from tests.test_microsoft_365_v13560 import (  # noqa: F401 — pytest fixtures
    _connect,
    _fresh_records,
    client,
    spawned,
)

ROOT = Path(__file__).resolve().parents[1]
APPS_HREF = "/settings?section=connections-apps"
SECTION = "# Apps named in this message"
MAIL_CHOICE = "Gmail or Microsoft 365 (Outlook, Calendar, OneDrive)"
MAIL_DOOR = {"href": APPS_HREF, "label": f"Connect {MAIL_CHOICE} in Settings"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("PIXIO_API_KEY", "OPENAI_API_KEY", "IRONJARVIS_MEDIA_FAKE"):
        monkeypatch.delenv(var, raising=False)
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "fresh"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


# --------------------------------------------------------------------------- #
# harness — the model is the only double; it never calls a tool
# --------------------------------------------------------------------------- #


def _final(text: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls or []), usage={})


class _Model:
    """Both lanes' model: answers in words, records each round's system
    prompt and the tools it was shown."""

    default_provider = "mock"

    def __init__(self, text: str = "I can't see that yet.") -> None:
        self.text = text
        self.systems: list[str] = []
        self.tools: list[list[str]] = []

    def _seen(self, kw: dict) -> None:
        self.systems.append(str(kw.get("system") or ""))
        self.tools.append([
            getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
            for t in kw.get("tools") or []
        ])

    async def stream(self, *args: Any, **kw: Any):
        self._seen(kw)
        yield {"type": "text", "text": self.text}
        yield {"type": "final", "response": _final(self.text), "provider": "mock", "model": "mock"}

    async def complete(self, **kw: Any) -> RouteResult:
        self._seen(kw)
        return RouteResult(_final(self.text), "mock", "mock")


def _frames(chunk: Any) -> list[tuple[str, dict]]:
    text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        ev, data = "", None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data or {}))
    return out


def _stream_turn(platform, text: str) -> tuple[dict, _Model]:
    """One real /chat/stream turn (the lane's own function), the chat page's
    body (Auto on). Returns the done frame and the model double."""
    model = _Model()
    platform.router.stream = model.stream
    body = ChatBody(messages=[{"role": "user", "content": text}], tools=[],
                    approval_mode="approve_for_me", auto_tools=True)

    async def go():
        gen = await chat_routes.stream_chat_turn(platform, {}, body)
        seen = []
        async for chunk in gen:
            seen += _frames(chunk)
        return seen

    frames = asyncio.run(asyncio.wait_for(go(), 60))
    return [d for ev, d in frames if ev == "done"][-1], model


def _post_turn(platform, text: str) -> tuple[dict, _Model]:
    """One real POST /chat turn (``run_chat_turn``)."""
    model = _Model()
    saved = platform.router
    platform.router = model
    try:
        body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
        res = asyncio.run(run_chat_turn(platform, {}, body))
    finally:
        platform.router = saved
    return res, model


def _section(system: str) -> str:
    at = system.find(SECTION)
    if at < 0:
        return ""
    end = system.find("\n\n", at + len(SECTION))
    return system[at:] if end < 0 else system[at:end]


# =========================================================================== #
# (1) G41 — THE CONNECT DOOR IS FOR THE USER'S OWN DATA ONLY
# =========================================================================== #

#: Sentences that name a catalog app but are NOT about the user's own data in
#: it — everyday words, research, writing about the app. Each must offer no
#: door. The first four are the final re-audit's own probes.
NOT_OWN = [
    "stripe the table rows",
    "explain how postgres indexes work",
    "the sentry stood guard at the gate in my story",
    "Write a LinkedIn post about our GitHub launch",
    "stripe every other row in the spreadsheet",
    "what does a sentry do in a medieval castle",
    "the night sentry walked the wall twice",
    "teach me postgres joins",
    "write a blog post about Notion for beginners",
    "our Slack launch went really well",
    "our Notion launch party is on Friday",
    "draft a tweet announcing our Stripe integration",
    "is Postgres good for analytics workloads?",
    "does Notion have an API?",
    "design a pricing page like Stripe's",
    "the gmail logo is red and white",
    "what's new in Notion this month",
    "describe how GitHub Actions caching works",
    "give me tips on writing a Slack bot",
    "summarize this article about Dropbox's layoffs",
    "compare Postgres and MySQL for a small shop",
    "how does Dropbox sync work",
    "write a story where a sentry guards a castle",
    "make the header stripe blue",
    "a sentry gun sits in the corner of the level",
    "who runs Sentry these days",
    "tell me more about Dropbox",
    "postgres is an object-relational database",
    # v1.360.0 review: a state word in a news sentence is not "is it
    # connected?" (the first cut offered the door for all three).
    "Slack set up a new pricing tier",
    "Stripe set up shop in Dublin last year",
    "GitHub connected millions of developers",
    # v1.360.0 fix round: "got <state word>" in a news sentence is not
    # "is it connected?" either.
    "Slack got connected to Teams in 2020",
    "GitHub got set up as a nonprofit",
    "Stripe got linked to the scandal",
    # v1.360.0 review (round 3): the topic rule (_TOPIC_RX) and the explain
    # rule (_EXPLAIN_RX) went unpinned — disabling either left every row
    # above green. Each pair below names the app right after "my"/"our" or
    # beside a thing kept in it, so ONLY that rule keeps it door-free.
    "write a poem about my Dropbox",
    "draft a blog post about our Slack",
    "explain what Stripe payouts are",
    "can you explain what GitHub issues are?",
]

#: Sentences about the user's OWN data in the app (or connecting it) — each
#: must offer that app's door when it is not connected.
OWN = [
    ("show me the Slack messages from today", ["slack"]),
    ("pull the latest invoices from Stripe", ["stripe"]),
    ("list the repos on GitHub", ["github"]),
    ("query the orders table in postgres", ["postgres"]),
    ("open Notion", ["notion"]),
    ("check Gmail for the receipt", ["gmail"]),
    ("any new Sentry errors today?", ["sentry"]),
    ("what's in my inbox", ["gmail"]),
    ("refund the last charge in Stripe", ["stripe"]),
    ("is github connected?", ["github"]),
    ("summarize my Notion meeting notes", ["notion"]),
    ("our Slack channel is noisy, summarize it", ["slack"]),
    ("find the lease in Dropbox", ["dropbox"]),
    ("save this to my Google Drive", ["google_drive"]),
    ("post the summary to Slack", ["slack"]),
    ("look through my Dropbox for the scans", ["dropbox"]),
    ("which Notion pages mention Q3?", ["notion"]),
    ("search Sentry for the login crash", ["sentry"]),
    ("can you check my email", ["gmail"]),
    ("upload the report to OneDrive", ["onedrive"]),
    ("connect GitHub", ["github"]),
    ("add the meeting to my calendar", ["microsoft_365"]),
    ("explain my Stripe payouts from last week", ["stripe"]),
    ("create a GitHub issue for the login bug", ["github"]),
    ("find my tax files in Google Drive", ["google_drive"]),
    ("search github for issues about login", ["github"]),
    # v1.360.0 review: every way of asking whether the app is connected.
    ("github connected?", ["github"]),
    ("why isn't slack connected", ["slack"]),
    ("has github been connected", ["github"]),
    ("Notion isn't connected yet, right?", ["notion"]),
    ("slack set up yet", ["slack"]),
    ("is github connected to my account", ["github"]),
    # v1.360.0 fix round: asked with "get" / "got".
    ("did slack get connected?", ["slack"]),
    ("did github get set up", ["github"]),
    ("has my notion got connected yet", ["notion"]),
    # v1.360.0 review (round 3): "my <app>" that IS what the user owns,
    # followed by a state, the end or a question mark (_PHRASE_END_RX) —
    # unpinned until now: disabling that rule left every row above green.
    ("my Slack is full of noise, help", ["slack"]),
    ("can you see my Google Drive?", ["google_drive"]),
]


@pytest.mark.parametrize("text", NOT_OWN)
def test_a_sentence_that_is_not_about_the_users_data_offers_no_door(text):
    """MUTATION: put back v1.359.0's default (``_for_own_data`` answering own
    when no research word is found) -> every row here is red."""
    assert apps_named(text), f"anti-vacuity: {text!r} names a catalog app"
    assert own_data_apps(text) == [], text
    rows = [{"id": cid, "name": cid, "state": "not_connected"} for cid in apps_named(text)]
    assert connect_offers(text, rows) == [], text


@pytest.mark.parametrize("text, ids", OWN)
def test_the_users_own_data_in_an_app_still_offers_its_door(text, ids):
    assert own_data_apps(text) == ids, text


def test_the_probe_table_is_wide_both_ways():
    assert len(NOT_OWN) >= 20 and len(OWN) >= 20


@pytest.mark.parametrize(
    "text",
    [
        "stripe the table rows",
        "explain how postgres indexes work",
        "the sentry stood guard at the gate in my story",
        "Write a LinkedIn post about our GitHub launch",
    ],
)
def test_the_re_audits_four_probes_get_no_door_in_either_lane(app, text):
    """THE ACCEPTANCE, as a new user meets it: a fresh install (nothing
    connected), a model that only answers in words — no Connect button under
    the reply, on the stream lane AND the POST lane."""
    platform = app.state.platform
    done, _ = _stream_turn(platform, text)
    assert done["doors"] == [], (text, done["doors"])
    res, _ = _post_turn(platform, text)
    assert res["doors"] == [], (text, res["doors"])


def test_the_users_own_data_still_gets_its_door_in_both_lanes(app):
    """Anti-vacuity for the test above: the same harness, an own-data
    sentence, the door is there on both lanes."""
    platform = app.state.platform
    want = [{"href": APPS_HREF, "label": "Connect Stripe in Settings"}]
    done, _ = _stream_turn(platform, "pull the latest invoices from Stripe")
    assert done["doors"] == want
    res, _ = _post_turn(platform, "pull the latest invoices from Stripe")
    assert res["doors"] == want


# =========================================================================== #
# (2) AN EVERYDAY WORD MEANS THE CONNECTED APP OF THAT KIND
# =========================================================================== #


@pytest.mark.parametrize(
    "text, live, named, choices",
    [
        # An Outlook user: the mailbox IS Microsoft 365.
        ("check my inbox", {"microsoft_365": ["t"]}, ["microsoft_365"], {}),
        ("what's in my email today", {"microsoft_365": ["t"]}, ["microsoft_365"], {}),
        # A Gmail user: Gmail, as before.
        ("check my inbox", {"gmail": ["t"]}, ["gmail"], {}),
        # Both saved: both are what "my inbox" can mean.
        ("check my inbox", {"gmail": ["t"], "microsoft_365": ["t"]}, ["gmail", "microsoft_365"], {}),
        # Neither: ONE choice naming both.
        ("check my inbox", {}, ["gmail", "microsoft_365"], {"mail": ["gmail", "microsoft_365"]}),
        # "my calendar" with Microsoft 365 saved: Microsoft 365.
        ("what's on my calendar tomorrow?", {"microsoft_365": ["t"]}, ["microsoft_365"], {}),
        # ... and with nothing saved: a choice with nothing to offer.
        ("what's on my calendar tomorrow?", {}, ["microsoft_365"], {"calendar": ["microsoft_365"]}),
        # A message that NAMES its app is never re-read.
        ("check my Gmail inbox", {"microsoft_365": ["t"]}, ["gmail"], {}),
        ("check my Outlook inbox", {"gmail": ["t"]}, ["microsoft_365"], {}),
        ("what's on my Outlook calendar", {}, ["microsoft_365"], {}),
        # Another mail service names no catalog app at all.
        ("check my yahoo inbox", {"microsoft_365": ["t"]}, [], {}),
    ],
)
def test_an_everyday_word_resolves_to_the_saved_app_of_its_kind(text, live, named, choices):
    """MUTATION: make ``resolve_kinds`` return *named* unchanged -> the
    Outlook rows read Gmail (red)."""
    got = resolve_kinds(text, apps_named(text), live)
    assert got == (named, choices), text


def test_with_no_mail_app_the_offer_names_both_choices_once():
    """The fold (``app_states`` choices) and the offer: one row, one door,
    both apps named, never Gmail alone."""
    rows = app_names._fold_choices(
        [
            {"id": "gmail", "name": "Gmail", "state": "not_connected", "tools": 0, "needs": "x", "note": ""},
            {"id": "microsoft_365", "name": "Microsoft 365 (Outlook, Calendar, OneDrive)",
             "state": "not_connected", "tools": 0, "needs": "", "note": ""},
        ],
        {"mail": ["gmail", "microsoft_365"]},
    )
    assert len(rows) == 1 and rows[0]["name"] == MAIL_CHOICE and rows[0]["choice"] is True
    offers = connect_offers("check my inbox", rows)
    assert offers == [{"id": "gmail", "name": MAIL_CHOICE, "ids": ["gmail", "microsoft_365"]}]
    assert with_connect_door([], offers) == [MAIL_DOOR]
    # app_connect already put up its own card for EITHER app: no second offer.
    assert with_connect_door([], offers, ["microsoft_365"]) == []
    section = app_names.apps_section(rows)
    assert f"- Email: no mail app is connected ({MAIL_CHOICE} can be)." in section
    assert "Gmail: not connected" not in section


def test_with_no_calendar_app_my_calendar_is_said_and_never_offered():
    rows = app_names._fold_choices(
        [{"id": "microsoft_365", "name": "Microsoft 365 (Outlook, Calendar, OneDrive)",
          "state": "not_connected", "tools": 0, "needs": "", "note": ""}],
        {"calendar": ["microsoft_365"]},
    )
    assert connect_offers("what's on my calendar tomorrow?", rows) == []
    section = app_names.apps_section(rows)
    assert "- Calendar: no calendar app is connected." in section
    assert "Microsoft 365" not in section


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_a_fresh_install_check_my_inbox_offers_both_mail_apps_once(app, lane):
    """No mail app connected: one door naming Gmail and Microsoft 365, and
    the model is told no mail app is connected — never "Gmail: not
    connected" (an Outlook user read it as the only choice)."""
    platform = app.state.platform
    out, model = (_stream_turn if lane == "stream" else _post_turn)(platform, "check my inbox")
    assert out["doors"] == [MAIL_DOOR], out["doors"]
    section = _section(model.systems[0])
    assert "- Email: no mail app is connected" in section, section
    assert "Gmail: not connected" not in section


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_a_fresh_install_my_calendar_offers_no_microsoft_365(app, lane):
    """"my calendar" does not say Outlook: no door, and the model is never
    told about Microsoft 365."""
    platform = app.state.platform
    out, model = (_stream_turn if lane == "stream" else _post_turn)(platform, "what's on my calendar tomorrow?")
    assert out["doors"] == [], out["doors"]
    section = _section(model.systems[0])
    assert "- Calendar: no calendar app is connected." in section, section
    assert "Microsoft 365" not in section


@pytest.mark.parametrize(
    "text, label",
    [
        ("check my Gmail", "Connect Gmail in Settings"),
        ("check my Outlook inbox", "Connect Microsoft 365 (Outlook, Calendar, OneDrive) in Settings"),
        ("what's on my Outlook calendar tomorrow?", "Connect Microsoft 365 (Outlook, Calendar, OneDrive) in Settings"),
    ],
)
def test_words_that_name_the_app_still_offer_that_app(app, text, label):
    done, _ = _stream_turn(app.state.platform, text)
    assert done["doors"] == [{"href": APPS_HREF, "label": label}]


@pytest.mark.timeout(180)
@pytest.mark.parametrize("lane", ["stream", "post"])
def test_an_outlook_user_who_says_check_my_inbox_gets_microsoft_365(client, lane):  # noqa: F811 — fixture
    """THE ACCEPTANCE: Microsoft 365 connected from its Directory card (its
    24 tools loaded from the stand-in server), Gmail not. "check my inbox":
    the model is told about Microsoft 365, never "Gmail: not connected"; no
    "Connect Gmail" door; and (stream lane) Microsoft 365's tools are armed.
    MUTATION: drop the ``resolve_kinds`` call in ``_gather_grounding`` ->
    the section says Gmail and the tools are missing (red)."""
    r = _connect(client, "common")
    assert r.status_code == 200 and r.json()["tools_loaded"] == 24, r.text
    platform = client.app.state.d.platform
    out, model = (_stream_turn if lane == "stream" else _post_turn)(platform, "check my inbox")
    section = _section(model.systems[0])
    assert "Microsoft 365 (Outlook, Calendar, OneDrive):" in section, section
    assert "Gmail" not in section, section
    assert not any("Gmail" in d.get("label", "") for d in out["doors"]), out["doors"]
    if lane == "stream":
        armed = model.tools[0]
        assert any(str(n).startswith("mcp__microsoft_365__") for n in armed), armed


# =========================================================================== #
# (3) G38 — THE GUIDE ANSWERS QUESTIONS ABOUT IRON JARVIS, NOT EVERY HOW-TO
# =========================================================================== #

GUIDE = {"guide_search", "app_search", "app_status"}

ABOUT_JARVIS = [
    "how do I connect Slack",
    "how do I set up a workflow",
    "how do I turn on local only",
    "how do I use Jarvis from my phone",
    "how do I add a skill",
    "how do I change the default model",
    "how do I schedule a daily report",
    "how do I find my missions",
    "how do I open the handbook",
    "how do I enable computer use here",
    "how do I add an agent",
    "how do I connect GitHub to Jarvis",
    "how do I install the add-on",
    "how do I pair the browser",
    "how do I set up a reflex",
    "how do I use the knowledge graph",
    "how do I change your voice",
    "how do I send you my notes",
    "how do I configure a webhook",
    "how do I disable approvals for this chat",
    "how do I add a tool pack",
    "how do I turn on emergency stop",
    # the v1.353.0 G38 rows, unchanged
    "how do I chat with you from my phone",
    "how do I connect gmail",
    "how do I schedule something",
    "How do I text you?",
    "how do I add a tool",
    "how do I install the browser add-on",
    "how do I pair my browser",
    "how do I send you a file",
    "how do I automate my month-end close",
    "how do I turn on computer use",
]

NOT_ABOUT_JARVIS = [
    "how do I connect my printer to wifi",
    "how do I send a wire transfer",
    "how do I install node",
    "how do I change a tire",
    "how do I open a pdf in python",
    "how do I set up a VPN",
    "how do I find my IP address",
    "how do I turn off autocorrect on my iPhone",
    "how do I enable dark mode in Windows",
    "how do I use pivot tables",
    "how do I disable cookies",
    "how do I configure nginx",
    "how do I add a printer",
    "how do I install python on windows",
    "how do I schedule a meeting with my team",
    "how do I automate excel with python",
    "how do I add a tool in excel",
    "how do I chat with customers on my website",
    "how do I send money to my brother",
    "how do I text a photo from my iPhone",
    "how do I install an add-on in firefox",
    "how do I connect two monitors",
]


@pytest.mark.parametrize("text", ABOUT_JARVIS)
def test_a_how_to_about_iron_jarvis_asks_the_guide(text):
    picked = set(select_auto_tools(text, cap=99))
    assert GUIDE <= picked, (text, sorted(picked))


@pytest.mark.parametrize("text", NOT_ABOUT_JARVIS)
def test_a_how_to_about_something_else_arms_no_guide_lookup(text):
    """MUTATION: drop the ``_guide_fires`` check in ``select_auto_tools`` ->
    every row here is red (they all armed the Guide at v1.359.0)."""
    assert not (GUIDE & set(select_auto_tools(text, cap=99))), text


def test_the_guide_probe_table_is_wide_both_ways():
    assert len(ABOUT_JARVIS) >= 20 and len(NOT_ABOUT_JARVIS) >= 20


# v1.360.0 review (lane 10B): the gate's first cut knew a dozen feature words
# and none of the Everything list's PAGES or the settings a first week asks
# about, so every question below — each of which armed the Guide from
# v1.224.0 to v1.359.0 — lost the Handbook. The acceptance names "a
# feature/page/setting word from the Handbook/Everything list".
ABOUT_JARVIS_PAGES = [
    "how do I open settings",
    "how do I find the settings",
    "how do I change the theme",
    "how do I add a project",
    "how do I set up a goal",
    "how do I change the model",
    "how do I add a model",
    "how do I change the default provider",
    "how do I add an api key",
    "how do I add my openai key",
    "how do I add a secret",
    "how do I add an MCP server",
    "how do I install a pack",
    "how do I open a new chat",
    "how do I use the terminal",
    "how do I use build mode",
    "how do I set up the fleet",
    "how do I install ollama",
    "how do I use voice",
    "how do I add a memory",
    "how do I use the knowledge base",
    "how do I turn off notifications",
    "how do I install the update",
    "how do I find my old sessions",
    "how do I use a template",
    "how do I connect claude code",
]

#: The everyday sense of those same words is not a question about the app.
#: Every row opens with one of the Guide rule's how-do-I verbs, so the GATE is
#: what refuses it (asserted below), never a rule that did not match at all.
NOT_ABOUT_JARVIS_EVERYDAY = [
    "how do I open a pack of gum",
    "how do I set up a model railway",
    "how do I add memory to my laptop",
    "how do I find my data usage",
    "how do I change my internet provider",
    "how do I set goals for the new year",
    "how do I change the model in pytorch",
    "how do I find new connections on linkedin",
    "how do I change my voice for a podcast",
    "how do I change the oil in my car",
    "how do I turn off notifications on my iphone",
    "how do I change the theme in vs code",
    "how do I open a terminal on mac",
    "how do I enable dark mode in Windows",
    "how do I find my tax return",
    "how do I set up a 401k",
    "how do I chat with customers on my website",
    "how do I install python on windows",
    "how do I connect two monitors",
    "how do I send money to my brother",
]


@pytest.mark.parametrize("text", ABOUT_JARVIS_PAGES)
def test_a_how_to_about_a_jarvis_page_or_setting_asks_the_guide(text):
    """MUTATION: drop the page/setting words from ``_ABOUT_JARVIS_FEATURE_RX``
    or the ``_page_word_is_the_object`` check (v1.360.0 fix round) -> the
    rows each one reads are red."""
    picked = set(select_auto_tools(text, cap=99))
    assert GUIDE <= picked, (text, sorted(picked))


@pytest.mark.parametrize("text", NOT_ABOUT_JARVIS_EVERYDAY)
def test_the_everyday_sense_of_a_page_word_arms_no_guide_lookup(text):
    """MUTATION: widen a page word to its bare form (``packs?``, ``memory``),
    drop the "model airplane" look-ahead or "pytorch" from the other-product
    list -> its everyday row is red."""
    from iron_jarvis.tools.autoselect import _GUIDE_RX

    m = _GUIDE_RX.search(text)
    assert m is not None and m.group("guide_howdo"), f"anti-vacuity: {text!r} reaches the gate"
    assert not (GUIDE & set(select_auto_tools(text, cap=99))), text


def test_the_page_word_tables_are_wide_both_ways():
    assert len(ABOUT_JARVIS_PAGES) >= 20 and len(NOT_ABOUT_JARVIS_EVERYDAY) >= 20


# v1.360.0 fix round (lane 10B): the review's page words matched ANYWHERE in
# the sentence, so the everyday sense of each still asked the Guide — 23 of 40
# probes below did ("find a project manager", "change the settings on my tv",
# "send updates to my clients", "use a resume template"). A page word with an
# everyday sense now counts only as the thing the how-do-I verb acts on, with
# nothing after it but the end, a page word, a short relative clause or a
# phrase about another page or a model.
NOT_ABOUT_JARVIS_PAGE_SENSE = [
    "how do I find a project manager",
    "how do I send a project proposal",
    "how do I find a session musician",
    "how do I find the theme of a poem",
    "how do I send updates to my clients",
    "how do I add updates to my linkedin profile",
    "how do I use a resume template",
    "how do I find a lease template",
    "how do I change my settings on my tv",
    "how do I change the settings on my camera",
    "how do I find the settings on my router",
    "how do I open a new chat on whatsapp",
    "how do I add a goal to my fitness app",
    "how do I send notifications to my team",
    "how do I add a model to my portfolio",
    "how do I schedule a dentist appointment",
    "how do I send my project to the client",
    "how do I install a terminal block",
    "how do I install new window panes",
    "how do I change my memory foam pillow",
    "how do I find my memories of childhood",
    "how do I install a fleet tracking system",
    "how do I add vehicles to my fleet",
    "how do I find where the terminal is at the airport",
    # "memory", "goal" and "setting" need an article before them.
    "how do I add memory",
    "how do I install more memory",
]

#: ...while the same words, said about the app, keep asking the Guide.
ABOUT_JARVIS_PAGE_SENSE = [
    "how do I change the model to claude",
    "how do I change the theme to dark",
    "how do I find the settings page",
    "how do I turn notifications off",
    "how do I add a memory to a project",
    "how do I set the fleet up",
    "how do I change which model answers",
    "how do I open settings and change the theme",
    "how do I find where the settings are",
    "how do I use different models for different chats",
    "how do I find my project folder",
    "how do I change the chat settings",
    "how do I use the dark theme",
    "how do I change the model you use",
    "how do I turn on notifications",
    "how do I find my chat history",
    "how do I set up a new project",
    "how do I add a template to a project",
    "how do I change my default model",
    "how do I use local models",
    "how do I find old chats",
    "how do I install updates",
]


@pytest.mark.parametrize("text", NOT_ABOUT_JARVIS_PAGE_SENSE)
def test_a_page_word_in_its_everyday_sense_arms_no_guide_lookup(text):
    """MUTATION: put the review's bare page words back into
    ``_ABOUT_JARVIS_FEATURE_RX`` (``projects?``, ``settings``, ``updates``,
    ``templates?``, ``fleet`` anywhere), or let ``_PAGE_TAIL_OK_RX`` accept
    any tail -> rows here are red. The dentist row is the "schedule a <word>
    appointment" look-ahead."""
    from iron_jarvis.tools.autoselect import _GUIDE_RX

    m = _GUIDE_RX.search(text)
    assert m is not None and m.group("guide_howdo"), f"anti-vacuity: {text!r} reaches the gate"
    assert not (GUIDE & set(select_auto_tools(text, cap=99))), text


@pytest.mark.parametrize("text", ABOUT_JARVIS_PAGE_SENSE)
def test_a_page_word_said_about_the_app_still_asks_the_guide(text):
    """MUTATION: drop the ``_page_word_is_the_object`` check, or the tail's
    "to <a page or a model>" / "page" / "and <verb>" branches -> rows here are
    red."""
    picked = set(select_auto_tools(text, cap=99))
    assert GUIDE <= picked, (text, sorted(picked))


def test_the_page_sense_tables_are_wide_both_ways():
    assert len(NOT_ABOUT_JARVIS_PAGE_SENSE) >= 20 and len(ABOUT_JARVIS_PAGE_SENSE) >= 20


# =========================================================================== #
# (4) G21 — A REPLY FROM THE USER'S OWN MODEL SERVER NAMES THE MACHINE
# =========================================================================== #


def test_the_seed_labels_are_the_registrys_and_the_dashboards():
    """The labels the fleet registry gives the two config slots are not a
    machine's name; the daemon and the dashboard hold the same two."""
    cfg = SimpleNamespace(ollama_base_url="http://localhost:11434", ollama_model="",
                          custom_base_url="http://10.0.0.5:4000/v1", custom_model="fleet")
    seeded = {n.id: n.label for n in FleetRegistry(cfg).seeded()}
    assert seeded == chat_turn.SEED_ENDPOINT_LABELS
    ts = (ROOT / "dashboard" / "lib" / "onboarding.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"SEED_ENDPOINT_LABELS[^=]*=\s*\{([^}]*)\}", ts)
    assert m, "the dashboard's twin is gone"
    pairs = dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", m.group(1)))
    assert pairs == chat_turn.SEED_ENDPOINT_LABELS


def _name_the_machine(platform, label: str) -> None:
    platform.config.custom_base_url = "http://10.1.2.3:4000/v1"
    platform.config.custom_model = "fleet"
    platform.config.fleet_nodes = [{
        "id": "custom", "label": label, "base_url": "http://10.1.2.3:4000/v1",
        "source": "config", "routable": True, "default_model": "fleet",
    }]


def test_route_label_names_the_custom_endpoints_machine(app):
    """MUTATION: drop the config-slot branch of ``route_label`` -> "" (red)."""
    platform = app.state.platform
    _name_the_machine(platform, "DGX  Spark")
    assert chat_turn.route_label(platform, "custom") == "DGX Spark"
    # The registry's own default label is not a name.
    _name_the_machine(platform, "Custom endpoint")
    assert chat_turn.route_label(platform, "custom") == ""
    # Off the fleet, and with no fleet at all: wording only, never a failure.
    assert chat_turn.route_label(platform, "anthropic") == ""
    assert chat_turn.route_label(SimpleNamespace(), "custom") == ""


class _ServerModel(LLMAdapter):
    """The user's own model server behind the "custom" endpoint, answering
    through the "fleet" alias (the user's daily route)."""

    provider = "custom"
    model = "fleet"

    def capabilities(self):
        return {"provider": "custom", "model": "fleet", "tool_use": True, "vision": False}

    async def complete(self, *, system, messages, tools):
        return LLMResponse(text="the server says hi")


def _wire_server(c: TestClient, monkeypatch) -> None:
    platform = c.app.state.platform
    real_get = platform.providers.get
    monkeypatch.setattr(platform.providers, "get",
                        lambda p, m=None: _ServerModel() if p == "custom" else real_get(p, m))
    real_avail = platform.providers.available
    monkeypatch.setattr(platform.providers, "available",
                        lambda n: True if n == "custom" else real_avail(n))


def _done_frame(text: str) -> dict:
    return [d for ev, d in _frames(text) if ev == "done"][-1]


def test_both_lanes_name_the_machine_that_answered(tmp_path, monkeypatch):
    """THE ACCEPTANCE: a reply through the custom endpoint the user named
    "DGX Spark" carries ``route.label`` = "DGX Spark" on the POST response
    AND the stream's done frame (MIRROR NOTE), so the receipt reads the
    machine — the name the mission screen and run page already use."""
    with TestClient(create_app(str(tmp_path / "srv"))) as c:
        _name_the_machine(c.app.state.platform, "DGX Spark")
        _wire_server(c, monkeypatch)
        body = {"messages": [{"role": "user", "content": "hi"}], "provider": "custom", "model": "fleet"}
        flat = c.post("/chat", json=body)
        assert flat.status_code == 200, flat.text
        streamed = c.post("/chat/stream", json=body)
        assert streamed.status_code == 200, streamed.text
        for route in (flat.json()["route"], _done_frame(streamed.text)["route"]):
            assert route["provider"] == "custom"
            assert route["label"] == "DGX Spark"
