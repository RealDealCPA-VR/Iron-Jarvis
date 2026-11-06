"""v1.436.0 (chat-first plan Wave 5, group J, B3) — DOORS WHERE A PAGE IS
TRULY NEEDED, and PLAIN "NOT OFFERED" LINES.

Some steps cannot be done from chat: signing in an account (Claude Code,
Codex, Grok, Pi, OpenCode, an Iron-Proxy account, Microsoft 365), the
one-time Google or Slack developer setup, installing and pairing the browser
add-on, downloading a local model, a Microsoft 365 admin's approval. The
owner (a CPA, security first): "chat says so and gives ONE button to the
exact place". And for what the app does not offer at all (controlling the
Windows desktop, opening its screens from another computer, Full access as a
folder default): "chat says so plainly and never pretends".

Pinned here:

* THE TABLE, BOTH WAYS — every sentence that should name a step (its door)
  or a thing not offered (its line), and the everyday near-misses that must
  name nothing ("sign the letter", "download the PDF", "the model of my
  car", "I installed a new printer"…).
* EVERY HREF IS REAL TODAY — resolved against ``dashboard/app``, the
  relocated aliases (``lib/relocated.ts``), the Settings sections
  (``SettingsHome``), the Help page's guides (``routes/helpdocs``) and the
  Handbook's own headings; a sign-in door's ``press`` is one the dashboard
  has.
* BOTH REAL LANES (MIRROR NOTE): the door rides the stream lane's done frame
  and the POST lane's answer, the line rides the system prompt the router
  receives — and when nothing matches, the system prompt is byte-identical
  (the block is ""; the v1366 golden and the v1420 prompt-order pins run
  unchanged beside this file). A door never pushes out a door the turn
  earned, and a coding session's turn gets the line but no door.
* LINEAR TIME — every pattern over the user's words, on pathological input,
  in a child process with a deadline.

THE CONTRACT FIXTURE (``dashboard/__tests__/fixtures/page-doors-v1436.json``)
carries every door the table can give and the stream lane's real done frame;
``dashboard/__tests__/page-doors-v1436.test.tsx`` reads it through the real
decoder, DoorsStrip and the Help page's section rule. Regenerate with
``IJ_REGEN_PAGE_DOORS=1 uv run pytest tests/test_page_doors_v14360.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

import iron_jarvis.daemon.chat_turn as CT
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.doors import _APPS_FIX_HREF, MAX_DOORS, collect_doors
from iron_jarvis.daemon.routes.helpdocs import _DOCS
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from tests.test_connect_door_v13590 import _done, _stream

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
FIXTURE = DASH / "__tests__" / "fixtures" / "page-doors-v1436.json"
REGEN = os.environ.get("IJ_REGEN_PAGE_DOORS") == "1"

# --------------------------------------------------------------------------- #
# THE TABLE — both ways
# --------------------------------------------------------------------------- #

#: (sentence, the step whose door the reply carries)
DOOR_ROWS: list[tuple[str, str]] = [
    ("sign in to Claude Code", "signin_claude_code"),
    ("How do I log into Claude Code?", "signin_claude_code"),
    ("Claude Code keeps asking me to log in", "signin_claude_code"),
    ("my claude code login expired", "signin_claude_code"),
    ("can you sign in to Claude for me", "signin_claude_code"),
    ("re-authenticate claude code please", "signin_claude_code"),
    ("log in to Codex", "signin_codex"),
    ("Codex says I'm not signed in", "signin_codex"),
    ("the codex sign-in isn't working", "signin_codex"),
    ("sign into grok", "signin_grok"),
    ("Grok CLI is logged out again", "signin_grok"),
    ("how do I sign in to Pi?", "signin_pi"),
    ("Pi's login keeps failing", "signin_pi"),
    ("log in to OpenCode", "signin_opencode"),
    ("OpenCode says I need to sign in", "signin_opencode"),
    ("sign in my second Claude account", "accounts"),
    ("add another Codex account", "accounts"),
    ("add a Claude account for work", "accounts"),
    ("my iron-proxy account needs to sign in again", "accounts"),
    ("my ChatGPT account isn't signed in", "accounts"),
    ("log in to my work Claude account", "accounts"),
    ("sign in to Microsoft 365", "signin_microsoft"),
    ("How do I log in to my Office 365 account for you?", "signin_microsoft"),
    ("my M365 sign-in expired", "signin_microsoft"),
    ("can you sign in to my Outlook so you can read my mail", "signin_microsoft"),
    ("Microsoft 365 says I'm logged out", "signin_microsoft"),
    ("my IT admin needs to grant admin consent for Microsoft 365", "vendor_approval"),
    ("Outlook says it needs admin approval", "vendor_approval"),
    ("how do I register the app in Entra", "vendor_approval"),
    ("what is the app registration in Azure for", "vendor_approval"),
    ("Conditional Access in our Microsoft tenant blocks the sign-in", "vendor_approval"),
    ("help me with the Google developer setup for Drive", "google_setup"),
    ("where do I paste the Google client ID", "google_setup"),
    ("make the Google app for me", "google_setup"),
    ("I need a Gmail OAuth client", "google_setup"),
    ("how do I do the one-time Google setup", "google_setup"),
    ("I'm stuck on the Google Cloud console", "google_setup"),
    ("where do I put my Slack bot token", "slack_setup"),
    ("create a Slack app for me", "slack_setup"),
    ("how do I get the Slack app-level token", "slack_setup"),
    ("where is the slack manifest", "slack_setup"),
    ("what's my Slack member ID for you", "slack_setup"),
    ("install the browser add-on", "browser_addon"),
    ("pair my browser", "browser_addon"),
    ("connect my Chrome to you", "browser_addon"),
    ("how do I set up your Chrome extension", "browser_addon"),
    ("the browser add-on won't connect", "browser_addon"),
    ("install the Iron Jarvis extension in Edge", "browser_addon"),
    ("download a local model", "local_model"),
    ("can you install a good local model for me", "local_model"),
    ("ollama pull llama3.2", "local_model"),
    ("download llama 3.2", "local_model"),
    ("which model should I download", "local_model"),
    ("I want an offline model I can download", "local_model"),
    ("pull the qwen coder model", "local_model"),
    # v1.442.0 (chat-first plan Wave 7, group A; tests/test_wave7_gaps_v14420.py
    # pins each against the code that makes its line true).
    ("Connect my Gmail and summarize my latest emails every morning at 8.", "gmail_setup"),
    ("set up gmail for me", "gmail_setup"),
    ("Set up Slack so you can message me when a long job finishes.", "slack_setup"),
    ("connect Slack for my notifications", "slack_setup"),
    ("Create a workflow that drafts a reply to every new email from a client.", "email_rule_inbox"),
    ("whenever a new email comes in from a client, start an intake session", "email_rule_inbox"),
]

#: (sentence, the thing it asks for that Iron Jarvis does not offer)
LINE_ROWS: list[tuple[str, str]] = [
    ("can you control my mouse", "desktop_control"),
    ("take over my screen and click the button", "desktop_control"),
    ("can you see my screen?", "desktop_control"),
    ("move the cursor to the top left", "desktop_control"),
    ("open Excel and type the totals into cell B2", "desktop_control"),
    ("click on my desktop icon for QuickBooks", "desktop_control"),
    ("take a screenshot of my desktop", "desktop_control"),
    ("I want desktop automation", "desktop_control"),
    ("can I open Iron Jarvis from another computer", "another_computer"),
    ("access the dashboard from my laptop", "another_computer"),
    ("use you on my iPad", "another_computer"),
    ("can I reach the dashboard over my network", "another_computer"),
    ("open Jarvis remotely", "another_computer"),
    ("make full access the default for this folder", "full_access_default"),
    ("set the folder default to Don't ask", "full_access_default"),
    ("always start sessions in this repo with full access", "full_access_default"),
    # v1.442.0 (Wave 7 group A).
    ("Make Build \u203a Code use Codex on my work account in Full access for this repo.", "full_access_default"),
    ("Schedule a weekly backup of my settings to my OneDrive folder.", "jarvis_backup"),
    ("Back up everything to my external drive automatically.", "jarvis_backup"),
    ("Watch my Downloads folder and file new PDFs into the right client folder.", "watcher_suggests"),
    ("Set a monthly spending limit for my builder agent.", "builtin_agent_limit"),
    ("Turn on voice so I can talk to you.", "voice_press"),
    ("can I talk to you out loud instead of typing?", "voice_press"),
    ("Set up a webhook so Zapier can start a task.", "internet_calls_in"),
    ("let IFTTT trigger a run when I get home", "internet_calls_in"),
]

#: Everyday sentences that must name NOTHING (no door, no line).
NEAR_MISSES: list[str] = [
    "sign the letter",
    "I installed a new printer",
    "download the PDF",
    "the model of my car",
    "I signed in to my bank this morning",
    "please sign in Claude at reception",
    "log the expense in QuickBooks",
    "add the catalog in Claude Code",
    "I don't grok why I'm not signed in to my bank",
    "download my bank statement",
    "run the numbers on the business model",
    "download the financial model",
    "install a chrome extension for ad blocking",
    "connect my browser to the wifi",
    "pair my headphones",
    "add your extension number to the signature",
    "link the edge of the table",
    "approve the invoice",
    "the purchase order needs admin approval",
    "google the client id format for me",
    "install the Slack app on my laptop",
    "copy the shipping manifest into Slack",
    "my desktop is cluttered",
    "save the file to my desktop",
    "move the meeting to Friday",
    "control the budget",
    "control the screen brightness",
    "use my keyboard shortcuts",
    "move the computer to the other desk",
    "open the file on my other computer",
    "access my work computer remotely",
    "don't ask me again, just use the default folder",
    "set Sam's access to full access by default on the project folder",
    "give Sam full access to the folder by default",
    "the medieval codex was signed in 1340",
    "log in to my raspberry pi",
    "my raspberry pi login is broken",
    "can you tell me how to log in to Outlook on my phone",
    "what is pi to ten digits",
    "can you run Iron Jarvis on my laptop too",
    "talk to you from my phone",
    "how do I log in to Outlook on my phone",
    "windows control panel",
    "the mouse died",
    "use the desktop app",
    "sign in sheet for the seminar",
    "my login page for the website",
    "pull the latest model year data",
    "download the app on my phone",
    "the slack in the schedule",
    "pi day is coming",
    "Claude accounts for most of the cost",
    # v1.442.0 (Wave 7 group A).
    "connect my Gmail to Outlook on my phone",
    "add a Gmail filter for the newsletters",
    "the Slack channel was noisy today",
    "message me on Slack when the export is done",
    "reply to every new email in my inbox now",
    "we need a backup plan for the meeting",
    "make a backup of the report to my USB drive",
    "watch the Downloads movie",
    "monitor my desktop usage",
    "the builder agent's spending report",
    "set a monthly budget for the house builder",
    "talk to you tomorrow",
    "the voice of the letter should be warmer",
    "use my voice memo app",
    "Zapier pricing",
    "is Zapier better than IFTTT?",
    "send my events to Zapier with a webhook",
    "give Sam full access to this repo",
]


def test_the_table_is_big_enough_both_ways():
    assert len(DOOR_ROWS) + len(LINE_ROWS) >= 40
    assert len(NEAR_MISSES) >= 40
    # Every step and every not-offered thing has at least one row.
    assert {s.id for s in P.STEPS} == {sid for _, sid in DOOR_ROWS}
    assert {n.id for n in P.NOT_OFFERED} == {nid for _, nid in LINE_ROWS}


@pytest.mark.parametrize("text,step", DOOR_ROWS)
def test_a_page_step_sentence_gets_its_door(text, step):
    got = P.page_step(text)
    assert got is not None and got.id == step, (text, got)
    # …and its line, the first in the block.
    block = P.prompt_block(text)
    assert block.startswith("\n\n" + P.BLOCK_HEADING + "\n- " + got.line), block


@pytest.mark.parametrize("text,item", LINE_ROWS)
def test_a_not_offered_sentence_gets_its_line_and_no_door(text, item):
    assert item in [n.id for n in P.not_offered(text)], text
    line = next(n.line for n in P.NOT_OFFERED if n.id == item)
    assert "- " + line in P.prompt_block(text)
    assert P.page_step(text) is None, text


@pytest.mark.parametrize("text", NEAR_MISSES)
def test_an_everyday_sentence_names_nothing(text):
    assert P.page_step(text) is None, (text, P.page_step(text))
    assert P.not_offered(text) == [], (text, P.not_offered(text))
    assert P.prompt_block(text) == ""


def test_priority_an_account_before_this_pcs_own_login_and_the_admin_before_the_sign_in():
    # "my second Claude account" is an Iron-Proxy account, not this PC's own
    # Claude Code login — the accounts card, not the Claude Code press.
    assert P.page_step("sign in my second Claude Code account").id == "accounts"
    # A sentence BOTH rules read: the account (a link to the page that holds
    # every account AND the Claude Code row) wins over the press.
    both = "log in to my Claude Code account"
    assert [s.id for s in P.page_steps(both)][:2] == ["accounts", "signin_claude_code"]
    assert P.page_step(both).id == "accounts"
    # The admin's approval explains a sign-in Microsoft refuses.
    assert P.page_step("my Microsoft 365 sign-in needs admin consent").id == "vendor_approval"
    # Several steps: ONE door (the first by priority), every line (capped).
    text = "sign in to Codex, pair my browser and download a local model; can you control my mouse"
    assert P.page_step(text).id == "signin_codex"
    block = P.prompt_block(text)
    assert block.count("\n- ") == P.MAX_LINES == 4
    assert "Codex" in block and "browser add-on" in block and "Ollama" in block and "mouse" in block


def test_only_the_start_of_a_long_message_is_read():
    filler = "Here is the report. " * (P.SCAN_CHARS // 19 + 5)
    assert P.page_step(filler + "sign in to Claude Code") is None
    assert P.page_step("sign in to Claude Code. " + filler) is not None


def test_the_lines_speak_plainly():
    """Plain words only — never the engine's (a daemon, an endpoint, an OAuth
    client, MCP), never a retired word (pane, job, harness)."""
    banned = re.compile(r"\b(?:daemon|endpoint|oauth|mcp|pane|job|harness|connector|plug-in|mock)\b", re.I)
    for step in P.STEPS:
        assert not banned.search(step.line), step.id
        assert not banned.search(step.label), step.id
        # Says what Iron Jarvis cannot do itself, and to say so plainly.
        assert re.search(r"\b(?:cannot|does not)\b", step.line), step.id
        assert "plainly" in step.line, step.id
    for item in P.NOT_OFFERED:
        assert not banned.search(item.line), item.id
        assert "plainly" in item.line, item.id


# --------------------------------------------------------------------------- #
# EVERY HREF IS REAL TODAY
# --------------------------------------------------------------------------- #


def _app_routes() -> set[str]:
    return {p.name for p in (DASH / "app").iterdir() if (p / "page.tsx").exists()}


def _relocated() -> dict[str, str]:
    src = (DASH / "lib" / "relocated.ts").read_text(encoding="utf-8")
    return dict(re.findall(r'"(/[a-z]+)": \{ base: "/settings", extra: \{ section: "([a-z-]+)" \} \}', src))


def _settings_sections() -> set[str]:
    src = (DASH / "components" / "settings" / "SettingsHome.tsx").read_text(encoding="utf-8")
    return set(re.findall(r'^\s+id: "([a-z]+-[a-z-]+)",$', src, re.M))


def _handbook_slugs() -> list[str]:
    lines = (ROOT / "docs" / "HANDBOOK.md").read_text(encoding="utf-8").splitlines()
    return [P.heading_slug(ln.lstrip("#").strip()) for ln in lines if re.match(r"#{1,4} ", ln)]


def _resolves(href: str) -> bool:
    u = urlparse(href)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    seg = u.path.strip("/").split("/")[0]
    if seg not in _app_routes():
        return False
    relocated = _relocated()
    if u.path in relocated:
        return relocated[u.path] in _settings_sections()
    if u.path == "/settings":
        return q.get("section", "") in _settings_sections()
    if u.path == "/help":
        guide, section = q.get("guide", ""), q.get("section", "")
        if guide not in _DOCS or not (ROOT / "docs" / _DOCS[guide][1]).exists():
            return False
        if not section:
            return True
        if guide != "handbook":
            return False
        return any(s == section or s.startswith(section + "-") for s in _handbook_slugs())
    if u.path == "/terminals":
        build_app = (DASH / "lib" / "buildApp.ts").read_text(encoding="utf-8")
        return q.get("view") == "terminal" and 'if (v === "terminal") return "terminal";' in build_app
    return not q


def test_every_door_leads_to_a_real_place():
    assert _relocated(), "the relocated aliases were read"
    assert {"connections-accounts", "connections-apps", "connections-notifications", "connections-browser"} <= (
        _settings_sections()
    )
    for step in P.STEPS:
        assert _resolves(step.href), (step.id, step.href)
    # A broken address is caught (anti-vacuity).
    assert not _resolves("/help?guide=handbook&section=no-such-section-anywhere")
    assert not _resolves("/settings?section=connections-nothing")
    assert not _resolves("/nowhere")


def test_the_doors_reuse_the_places_other_doors_already_use():
    by_id = {s.id: s for s in P.STEPS}
    # The Directory (the Connect door's address) and the Browser page (a
    # browser refusal's fix door) — one address per place, so the two never
    # sit side by side.
    assert by_id["signin_microsoft"].href == _APPS_FIX_HREF
    from iron_jarvis.daemon.doors import _BROWSER_FIX_HREF

    assert by_id["browser_addon"].href == _BROWSER_FIX_HREF
    assert by_id["vendor_approval"].href == "/help?guide=handbook&section=" + P.M365_ADMIN_SECTION


def test_a_press_is_one_the_dashboard_has_and_the_guides_are_the_help_pages():
    presses = {s.press for s in P.STEPS if s.press}
    assert presses == set(P.SIGN_IN_PRESSES)
    src = (DASH / "lib" / "onboarding.ts").read_text(encoding="utf-8") + (
        DASH / "lib" / "cliSignIn.ts"
    ).read_text(encoding="utf-8")
    for p in P.SIGN_IN_PRESSES:
        assert f'provider: "{p}"' in src, p
    assert P.HELP_GUIDES == tuple(_DOCS)
    for s in P.STEPS:
        if s.press:
            assert s.href == P.ACCOUNTS_HREF  # the press's own page, if drawn as a link
            assert s.label.startswith("Sign in to ")


# --------------------------------------------------------------------------- #
# with_page_door — last, only with room, never twice to one place
# --------------------------------------------------------------------------- #


def test_the_door_is_added_last_and_never_pushes_out_an_earned_door():
    earned = [{"href": f"/x{i}", "label": f"x{i}"} for i in range(MAX_DOORS - 1)]
    out = P.with_page_door(earned, "pair my browser")
    assert out[:-1] == earned and out[-1] == {"href": "/computeruse", "label": "Set up the browser add-on"}
    full = [{"href": f"/x{i}", "label": f"x{i}"} for i in range(MAX_DOORS)]
    assert P.with_page_door(full, "pair my browser") == full
    # A raw list longer than the cap (None entries skipped) is judged as kept.
    assert P.with_page_door(full + [None], "pair my browser") == full + [None]
    # A door that already leads to the same place is not doubled.
    fix = [{"href": "/computeruse", "label": "Pair your browser"}]
    assert P.with_page_door(fix, "pair my browser") == fix
    assert P.with_page_door([], "the weather today") == []
    # A sign-in door carries its press.
    assert P.with_page_door([], "sign in to Codex") == [
        {"href": "/connections", "label": "Sign in to Codex", "press": "codex-cli"}
    ]
    assert collect_doors(P.with_page_door([], "sign in to Codex"))[0]["press"] == "codex-cli"


# --------------------------------------------------------------------------- #
# THE REAL LANES
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clean_turns(monkeypatch):
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


class _Stream:
    """The stream lane's model: answers in words, records the system prompt."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def __call__(self, *args: Any, **kw: Any):
        self.systems.append(str(kw.get("system") or ""))
        yield {"type": "text", "text": "Use the button below."}
        yield {"type": "final", "response": LLMResponse(text="Use the button below.", usage={}),
               "provider": "mock", "model": "mock"}


class _Post:
    default_provider = "mock"

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def complete(self, **kw: Any) -> RouteResult:
        self.systems.append(str(kw.get("system") or ""))
        return RouteResult(LLMResponse(text="Use the button below.", usage={}), "mock", "mock")


def _body(text: str, **extra: Any) -> dict:
    return {"messages": [{"role": "user", "content": text}], "tools": [],
            "approval_mode": "approve_for_me", "auto_tools": True, **extra}


def _stream_turn(app, text: str, **extra: Any) -> tuple[dict, str]:
    model = _Stream()
    app.state.platform.router.stream = model
    done = _done(_stream(app, _body(text, **extra)))
    return done, model.systems[0]


def _post_turn(app, text: str, **extra: Any) -> tuple[dict, str]:
    platform = app.state.platform
    model = _Post()
    platform.router = model
    res = asyncio.run(run_chat_turn(platform, {}, ChatBody(**_body(text, **extra))))
    return res, model.systems[0]


CLAUDE_DOOR = {"href": "/connections", "label": "Sign in to Claude Code", "press": "claude-cli"}


def test_the_stream_lane_carries_the_door_and_the_line(app):
    done, system = _stream_turn(app, "how do I sign in to Claude Code?")
    assert done["doors"] == [CLAUDE_DOOR]
    step = next(s for s in P.STEPS if s.id == "signin_claude_code")
    assert P.BLOCK_HEADING in system and step.line in system
    _check_fixture("stream_done", {k: done[k] for k in ("reply", "doors")})


def test_the_post_lane_carries_the_door_and_the_line(app):
    res, system = _post_turn(app, "how do I sign in to Claude Code?")
    assert res["doors"] == [CLAUDE_DOOR]
    step = next(s for s in P.STEPS if s.id == "signin_claude_code")
    assert P.BLOCK_HEADING in system and step.line in system


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_a_not_offered_request_gets_the_line_and_no_door(app, lane):
    turn = _stream_turn if lane == "stream" else _post_turn
    out, system = turn(app, "can you control my mouse and click Save in Excel?")
    line = next(n.line for n in P.NOT_OFFERED if n.id == "desktop_control")
    assert line in system
    assert out["doors"] == []


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_nothing_matched_leaves_the_system_prompt_byte_identical(app, monkeypatch, lane):
    turn = _stream_turn if lane == "stream" else _post_turn
    text = "what should I name the quarterly report?"
    assert P.prompt_block(text) == ""
    _, with_block = turn(app, text)
    monkeypatch.setattr(CT, "page_steps_block", lambda body: "")
    _, without = turn(app, text)
    assert P.BLOCK_HEADING not in with_block
    assert with_block == without


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_the_line_is_in_the_prompt_the_planner_prices(app, monkeypatch, lane):
    """The block joins the query tail BEFORE the history planner, so its
    cost is counted: the planner is handed it."""
    seen: list[str] = []
    real = CT._plan_context

    def spy(d, body, system, *a, **kw):
        seen.append(system)
        return real(d, body, system, *a, **kw)

    monkeypatch.setattr(CT, "_plan_context", spy)
    monkeypatch.setattr(chat_routes, "_plan_context", spy, raising=False)
    turn = _stream_turn if lane == "stream" else _post_turn
    turn(app, "pair my browser")
    assert seen and P.BLOCK_HEADING in seen[-1]


def test_the_door_never_pushes_out_the_connect_door_and_comes_after_it(app):
    done, _ = _stream_turn(app, "find my tax files in Google Drive and sign in to Claude Code")
    hrefs = [d["href"] for d in done["doors"]]
    assert hrefs == [_APPS_FIX_HREF, "/connections"], done["doors"]
    res, _ = _post_turn(app, "find my tax files in Google Drive and sign in to Claude Code")
    assert [d["href"] for d in res["doors"]] == hrefs


def test_the_microsoft_door_is_the_connect_doors_place_once(app):
    """'sign in to my Microsoft 365 so you can read my mail' with Microsoft
    365 not connected: the Connect door already leads to its card, so the
    page door is not added beside it."""
    done, system = _stream_turn(app, "sign in to Microsoft 365 so you can read my mail")
    assert [d["href"] for d in done["doors"]].count(_APPS_FIX_HREF) == 1
    assert "Sign in to Microsoft" in system


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_a_build_pane_chat_gets_the_line_but_no_door(app, lane):
    turn = _stream_turn if lane == "stream" else _post_turn
    out, system = turn(app, "sign in to Codex", pane_id="pane-1")
    assert out["doors"] == []
    assert "Signing in to Codex" in system


def test_a_coding_session_turn_gets_the_line_but_no_door(app, monkeypatch, tmp_path):
    from tests._fakes.harness_lane import body, no_finish_check, post, stream, wire

    ws = tmp_path / "ws"
    ws.mkdir()
    no_finish_check(ws)
    platform = app.state.platform
    model = wire(platform, monkeypatch, ["Done."])
    out = asyncio.run(stream(platform, body(ws, "how do I log in to Codex?")))
    assert out["done"]["doors"] == []
    assert "Signing in to Codex" in model.calls[0]["system"]
    res = asyncio.run(post(platform, body(ws, "how do I log in to Codex?")))
    assert res["doors"] == []


# --------------------------------------------------------------------------- #
# LINEAR TIME — pathological input, a child process, a deadline
# --------------------------------------------------------------------------- #

_LINEAR_SCRIPT = r"""
import sys, time
from iron_jarvis.daemon import page_doors as P
pieces = [
    "sign in ", "log in to my ", "a", "claude code ", "codex ", "the ", "my second claude ",
    "google client ", "slack bot ", "browser ", "download ", "local ", "control my ", "open jarvis ",
    "full access default folder ", "llama3 ", "x-", "'s ", "pi ", "outlook you ", "iron proxy ",
]
n = int(sys.argv[1])
t0 = time.perf_counter()
for piece in pieces:
    text = (piece * (n // len(piece) + 1))[:n]
    for pat in P.ALL_PATTERNS:
        pat.search(text)
        pat.search(text + "!")
    P.page_steps(text); P.not_offered(text)
print(time.perf_counter() - t0)
"""


def test_every_pattern_is_linear_on_pathological_input():
    """Each pattern over 60,000 characters of its own words, with no sentence
    end to stop a gap: a quadratic pattern takes minutes; these take well
    under the deadline. In a CHILD process, so a hang fails by name instead
    of hanging the suite."""
    run = subprocess.run(
        [sys.executable, "-c", _LINEAR_SCRIPT, "60000"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert float(run.stdout.strip()) < 60.0, run.stdout


# --------------------------------------------------------------------------- #
# THE CONTRACT FIXTURE
# --------------------------------------------------------------------------- #


def _fixture_steps() -> dict[str, Any]:
    return {
        "steps": [{"id": s.id, "door": s.door()} for s in P.STEPS],
        "presses": list(P.SIGN_IN_PRESSES),
        "guides": list(P.HELP_GUIDES),
        "handbook_section": P.M365_ADMIN_SECTION,
        "slug_cases": {t: P.heading_slug(t) for t in (
            "Microsoft 365, locked down for client data (v1.428.0)",
            "Browser — your own Chrome or Edge",
            "  What's new?? (v1.2) ",
            "Größe & Ünïcode",
        )},
    }


def _check_fixture(section: str, actual: Any) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if REGEN:
        data["_comment"] = (
            "v1.436.0 (Wave 5 group J): every page-step door the daemon can give and the stream "
            "lane's real done frame. Written by tests/test_page_doors_v14360.py "
            "(IJ_REGEN_PAGE_DOORS=1); read by dashboard/__tests__/page-doors-v1436.test.tsx."
        )
        data[section] = actual
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_bytes((json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
        return
    assert section in data, f"fixture has no {section!r}: regenerate it"
    assert data[section] == actual, f"the daemon's {section!r} no longer matches the dashboard's fixture"


def test_the_fixture_holds_every_door_the_daemon_can_give():
    _check_fixture("table", _fixture_steps())
