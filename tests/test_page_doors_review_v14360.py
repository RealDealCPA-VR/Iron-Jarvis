"""v1.436.0 (chat-first plan Wave 5, group J) — THE REVIEW's pins.

Every line ``daemon/page_doors`` hands the model is a claim the owner will
act on, so each one is pinned against the code that makes it true, not
against its own wording:

* the places are named the way the screen names them TODAY (a Settings row
  named like its page since v1.354.0 — "Accounts & keys" and "Apps & tools"
  are retired names), and the Iron-Proxy card by its own title;
* a CLI's Sign in button is promised only while that CLI is signed out (the
  Connections row draws it only then);
* "Full access" and "Don't ask" are the ONLY postures a folder refuses, and
  Auto — which runs routine actions without asking — is one it takes;
* LAN access opens the task door AND a remote agent's message back;
* only a destination with two-way chat takes the user's messages (Telegram,
  Slack, email — never Discord);
* no built-in tool controls the desktop (the REAL registry is read), and the
  line says "of its own" because an app the user connects could;
* the starter model is offered by a new chat's connect doors (the first-run
  wizard is gone), and Slack's bot token serves the Directory's Slack card too;
* a "not offered" line is never the line the cap cuts;
* the reviewer's own sentences, both ways (three sets, written before they
  were run), and every new pattern linear in a child process.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon import page_doors as P

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"


def _lines() -> list[tuple[str, str]]:
    return [(s.id, s.line) for s in P.STEPS] + [(n.id, n.line) for n in P.NOT_OFFERED]


def _line(item_id: str) -> str:
    return dict(_lines())[item_id]


# --------------------------------------------------------------------------- #
# the places are named the way the screen names them
# --------------------------------------------------------------------------- #


def test_no_line_names_a_settings_row_by_a_retired_name():
    src = (DASH / "components" / "settings" / "SettingsHome.tsx").read_text(encoding="utf-8")
    retired = re.findall(r'formerly: "([^"]+)"', src)
    assert "Accounts & keys" in retired and "Apps & tools" in retired  # anti-vacuity
    for item_id, line in _lines():
        for name in retired:
            assert name not in line, (item_id, name)
    for step in P.STEPS:
        for name in retired:
            assert name not in step.label, (step.id, name)


def test_the_places_are_the_ones_the_rest_of_the_daemon_names():
    from iron_jarvis.connectors.app_names import APPS_PLACE
    from iron_jarvis.providers.failure_words import ACCOUNTS_PLACE

    assert P.CONNECTIONS_PLACE == ACCOUNTS_PLACE
    assert P.DIRECTORY_PLACE == APPS_PLACE
    assert P.DIRECTORY_PLACE in _line("signin_microsoft")
    assert P.DIRECTORY_PLACE in _line("google_setup")


def test_the_accounts_card_is_named_by_its_own_title():
    src = (DASH / "components" / "connections" / "IronProxyCard.tsx").read_text(encoding="utf-8")
    title = re.search(r'export const CARD_TITLE = "([^"]+)";', src)
    assert title, "IronProxyCard.CARD_TITLE was read"
    assert P.ACCOUNTS_CARD == title.group(1)
    assert f'"{P.ACCOUNTS_CARD}"' in _line("accounts")
    # The card's own button words.
    assert "Add account" in src and "Add account" in _line("accounts")


# --------------------------------------------------------------------------- #
# a CLI's Sign in button is there only while it is signed out
# --------------------------------------------------------------------------- #


def test_a_cli_sign_in_button_is_promised_only_while_it_is_signed_out():
    src = (DASH / "components" / "settings" / "pages" / "ConnectionsPage.tsx").read_text(encoding="utf-8")
    # The row draws the press ONLY when the CLI is not available and signed out.
    assert re.search(r"available\s*\?\s*null\s*:\s*signedOut\s*&&\s*cli", src), "the row's gate moved"
    for step_id, name in (("signin_claude_code", "Claude Code"), ("signin_codex", "Codex"),
                          ("signin_grok", "Grok CLI")):
        line = _line(step_id)
        gate = line.find(f"while {name} is not signed in")
        assert gate >= 0, step_id
        assert gate < line.find("Sign in button"), step_id


# --------------------------------------------------------------------------- #
# the three things not offered, against the code that decides them
# --------------------------------------------------------------------------- #


def test_the_folder_default_line_matches_what_a_folder_may_default_to():
    from iron_jarvis.harness import folder_defaults as F

    line = _line("full_access_default")
    # The two refused: Don't ask (yolo) and a tool's Full access.
    assert "yolo" not in F.CODE_POSTURES and "full" not in F.TOOL_MODES
    # Auto IS a folder default, and Auto runs routine actions without asking:
    # the line must never say a new session cannot start without asking.
    assert "auto" in F.CODE_POSTURES and "auto" in F.TOOL_MODES
    assert "without asking" not in line
    assert "Auto" in line


def test_the_lan_line_names_both_doors_lan_access_opens():
    from iron_jarvis.daemon.auth import TASK_DOOR_PATH, _is_exempt

    assert _is_exempt(TASK_DOOR_PATH)
    assert _is_exempt("/agents/remote/hermes/inbound")
    line = _line("another_computer")
    assert "hand this one tasks" in line
    assert "remote agent" in line and "messages back" in line


def test_the_phone_line_names_only_destinations_that_take_messages():
    from iron_jarvis.comm import channels as C
    from iron_jarvis.comm import slack_socket

    # Telegram and email poll; Slack listens through Socket Mode; Discord has
    # neither.
    assert "poll" in C.TelegramChannel.__dict__ and "poll" in C.EmailChannel.__dict__
    assert "slack" in Path(slack_socket.__file__).read_text(encoding="utf-8").lower()
    assert "poll" not in C.DiscordChannel.__dict__
    line = _line("another_computer")
    assert "two-way chat" in line
    for name in ("Telegram", "Slack", "email"):
        assert name in line, name
    assert "Discord" not in line


#: Every tool name in the real registry that sounds like it clicks, types or
#: looks: each acts in a WEB PAGE (the add-on's tabs, a coding session's own
#: browser, the agent browser) — none on the desktop. A new one fails here, so
#: the desktop line is read again before it ships.
WEB_ONLY = {
    "browser_click", "browser_screenshot", "browser_type",
    "page_click", "page_screenshot", "page_type",
    "web_look", "computer_use_status",
}
#: v1.466.0: the one tool that "looks" at a desktop program. It reads the
#: workbook open in Excel through Excel itself (no mouse, no keyboard, no
#: screen), and the desktop line says that way in.
EXCEL_ITSELF = {"excel_live_look"}


def test_no_built_in_tool_controls_the_desktop(tmp_path):
    from iron_jarvis.daemon.app import create_app

    platform = create_app(str(tmp_path)).state.platform
    names = set(platform.registry.names())
    assert len(names) > 100  # anti-vacuity: the real registry was built
    sounds_like = {
        n for n in names
        if re.search(r"desktop|mouse|keyboard|cursor|screen|click|type|look|ui_|computer|window", n)
    }
    # Anti-vacuity: the net catches the web tools that are really there.
    assert {"browser_click", "page_type", "web_look"} <= sounds_like
    assert sounds_like <= WEB_ONLY | EXCEL_ITSELF, sounds_like - WEB_ONLY - EXCEL_ITSELF
    assert EXCEL_ITSELF <= names
    line = _line("desktop_control")
    # …and the line never claims more than that: an app the user connected
    # could, so "of its own", and the screenshot way in is said.
    assert "no way of its own" in line
    assert "an app the user connected" in line
    assert "paste a screenshot" in line
    # v1.466.0: the workbook open in Excel is reached through Excel itself,
    # and only when the turn holds the tools for it.
    assert "a workbook that is open in Excel" in line and "through Excel itself" in line
    assert "when the tools for the open workbook are in your list" in line
    assert "never by clicking or typing in Excel's window" in line


def test_the_local_model_line_names_the_place_that_offers_the_starter_model():
    starter = (DASH / "lib" / "starterModel.ts").read_text(encoding="utf-8")
    label = re.search(r'export const STARTER_MODEL_LABEL = "([^"]+)";', starter)
    assert label and label.group(1) == P.STARTER_MODEL_NAME
    chat = (DASH / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    assert P.CONNECT_DOORS_WORDS in chat
    assert "<ConnectDoors" in chat
    line = _line("local_model")
    assert P.STARTER_MODEL_NAME in line and P.CONNECT_DOORS_WORDS in line
    # The first-run wizard is no longer drawn anywhere but its own tests.
    assert "first-run" not in line


def test_the_slack_line_names_the_directory_card_that_takes_the_bot_token_too():
    from iron_jarvis.connectors.catalog import CATALOG

    slack = next(c for c in CATALOG if c.id == "slack")
    src = (ROOT / "src" / "iron_jarvis" / "connectors" / "catalog.py").read_text(encoding="utf-8")
    assert "SLACK_BOT_TOKEN" in src and slack.connect_via == "mcp"
    line = _line("slack_setup")
    assert "Notifications" in line and P.DIRECTORY_PLACE in line and "bot token" in line


# --------------------------------------------------------------------------- #
# the cap never cuts the line that stops the model pretending
# --------------------------------------------------------------------------- #


def test_a_not_offered_line_is_never_the_one_the_cap_cuts():
    text = ("sign in to Codex, pair my browser, download a local model and log in to OpenCode; "
            "and can you control my mouse")
    assert len(P.page_steps(text)) == 4  # four steps…
    block = P.prompt_block(text)
    assert block.count("\n- ") == P.MAX_LINES
    assert _line("desktop_control") in block  # …and the desktop line kept
    # The door's own step keeps its place first.
    assert block.startswith("\n\n" + P.BLOCK_HEADING + "\n- " + _line("signin_codex"))
    # Three not-offered and a step: the step keeps one line.
    many = ("sign in to Codex; can you control my mouse; open Jarvis from my laptop; "
            "make full access the default for this folder")
    lines = P.prompt_block(many).split("\n- ")[1:]
    assert lines[0] == _line("signin_codex") and len(lines) == 4


# --------------------------------------------------------------------------- #
# the reviewer's sentences, both ways
# --------------------------------------------------------------------------- #

REVIEW_DOORS: list[tuple[str, str]] = [
    ("Claude Code is asking me to log in again", "signin_claude_code"),
    ("Claude Code says Please run /login", "signin_claude_code"),
    ("Claude Code says invalid API key, please run /login", "signin_claude_code"),
    ("please sign me in to Claude Code", "signin_claude_code"),
    ("my Claude Code session says I'm signed out", "signin_claude_code"),
    ("set up Claude Code login", "signin_claude_code"),
    ("I need to sign in to codex with my chatgpt plan", "signin_codex"),
    ("my Codex login stopped working", "signin_codex"),
    ("Codex keeps saying I'm logged out", "signin_codex"),
    ("the Codex CLI wants me to log in", "signin_codex"),
    ("help me log into Codex on this PC", "signin_codex"),
    ("Grok says it needs me to log in", "signin_grok"),
    ("re-login to grok cli", "signin_grok"),
    ("Grok needs me to sign in again", "signin_grok"),
    ("set up a second Codex account for my personal plan", "accounts"),
    ("add my personal ChatGPT plan as another account", "accounts"),
    ("how do I connect a third Claude subscription", "accounts"),
    ("add a work Codex login", "accounts"),
    ("how do I log in to Microsoft 365 for Iron Jarvis", "signin_microsoft"),
    ("Microsoft 365 keeps asking me to sign in", "signin_microsoft"),
    ("our admin has to approve the Iron Jarvis app in Microsoft Entra", "vendor_approval"),
    ("the Microsoft sign-in says need admin approval", "vendor_approval"),
    ("Entra says the app needs consent", "vendor_approval"),
    ("how do I get my admin to approve Iron Jarvis in Office 365", "vendor_approval"),
    ("where do I get a Google client ID and secret for Drive", "google_setup"),
    ("set up the Google OAuth consent screen", "google_setup"),
    ("where's the OAuth client for Google Drive", "google_setup"),
    ("Gmail needs the Google Cloud console setup", "google_setup"),
    ("I'm stuck making the Google OAuth app", "google_setup"),
    ("I don't have a Slack app token", "slack_setup"),
    ("how do I make a Slack bot so I can message you", "slack_setup"),
    ("turn on socket mode in Slack", "slack_setup"),
    ("how do I create the Slack bot for notifications", "slack_setup"),
    ("I need the Slack xapp token", "slack_setup"),
    ("set up the Slack app manifest", "slack_setup"),
    ("install your Edge extension", "browser_addon"),
    ("the Chrome extension isn't connecting to Jarvis", "browser_addon"),
    ("re-pair the browser add-on", "browser_addon"),
    ("how do I pair the add-on with Iron Jarvis", "browser_addon"),
    ("install the Jarvis extension in Chrome", "browser_addon"),
    ("the browser add-on keeps disconnecting", "browser_addon"),
    ("download mistral 7b for offline use", "local_model"),
    ("what's the best ollama model to download for 32GB of RAM", "local_model"),
    ("get me a free local AI model", "local_model"),
    ("pull qwen2.5 for me", "local_model"),
    ("download an Ollama model", "local_model"),
    ("pull deepseek-r1 for local use", "local_model"),
]

REVIEW_LINES: list[tuple[str, str]] = [
    ("watch my screen while I work in QuickBooks", "desktop_control"),
    ("can you take control of my computer", "desktop_control"),
    ("can you click buttons on my screen for me", "desktop_control"),
    ("open QuickBooks and then click on Reports", "desktop_control"),
    ("can you operate my mouse while I'm away", "desktop_control"),
    ("start Word and type my letter into it", "desktop_control"),
    ("can Jarvis see my desktop", "desktop_control"),
    ("can you take a screenshot of my screen", "desktop_control"),
    ("can I use Iron Jarvis from my work laptop", "another_computer"),
    ("open the dashboard on my MacBook", "another_computer"),
    ("can I get to Jarvis from my home PC", "another_computer"),
    ("access Iron Jarvis remotely from the office", "another_computer"),
    ("set Full access as the default for my project folder", "full_access_default"),
    ("make Don't ask the default in this repo", "full_access_default"),
    ("use Full access by default for this folder", "full_access_default"),
    ("default this repo to Don't ask", "full_access_default"),
]

#: Everyday sentences (an office's words above all) that must name nothing.
REVIEW_NEAR_MISSES: list[str] = [
    "sign the engagement letter",
    "install the new printer driver",
    "download the bank statement PDF",
    "the Slack huddle is at 3",
    "my Outlook calendar is full",
    "the model year of my car",
    "pair my headphones",
    "add-on sale at the store",
    "log in to the IRS portal and download the transcript",
    "help the client sign into the QuickBooks Online portal",
    "the client needs admin approval to release the payment",
    "set the default payment terms for this project to net 30",
    "give my assistant full access to the shared folder",
    "move the desktop shortcut to the taskbar",
    "control my spending this month",
    "the screen on my phone is cracked",
    "download Excel for Mac",
    "pull the Q3 numbers from the model",
    "the Google review for our office",
    "add a client ID column to the Google Sheet",
    "update the client ID in my Gmail signature",
    "what's the client ID for Acme in Gmail?",
    "send the member ID to the team on Slack",
    "the codex of tax law",
    "Claude from accounting signed in late today",
    "please log in Claude's hours for the week",
    "log in with Claude's password to the client portal",
    "Outlook is asking me to sign in again on my laptop",
    "my Microsoft account password expired",
    "sign in to my Microsoft account on the new laptop",
    "how do I sign into Office 365 on my work computer",
    "remote control the TV",
    "connect my printer",
    "link my Chrome bookmarks to the spreadsheet",
    "connect my Chrome to the printer",
    "update the browser extension list in the IT policy",
    "I'll share my screen with the client tomorrow",
    "How do I use my keyboard to select a whole column in Excel?",
    "the pi chart in the report",
    "my grok of the situation is limited",
    "Slack me the client's app ID when you have it",
    "sign the contract and send it back",
    "my client logged in to the portal yesterday",
    "please log the call with Codex Inc.",
    "add a new account for the vendor Claude Hamilton",
    "the Chrome extension for Grammarly isn't working",
    "download the IRS model form 8879",
    "pull up my screen time report",
    "can you see my point about the deadline",
    "share the desktop folder with the team",
    "open Excel",
    "use the mouse pad",
    "I need admin approval in QuickBooks to post this entry",
    "the Microsoft Teams meeting needs approval from the partner",
    "sign in to the Slack workspace on my laptop",
    "my default folder for scans",
    "give full access to the client folder by default",
    "remote access to the office printer",
    "I logged in to Codex last week and it worked",
    "connect the edge of the frame",
    "Grok is great at summaries",
    "use my screen name in the email",
    "sign the 1040 for the client",
    "log in to the bank and pull last month's statement",
    "Codex Alimentarius standards",
    "my second account at Chase is overdrawn",
    "the Google Doc for the client's app ID list",
    "install the extension cord behind the desk",
    "download the trial balance from the model office",
    "control the screen door",
    "the remote desktop to the client's server keeps dropping",
    "pair the wine with dinner",
    "a different computer at the office needs QuickBooks",
]


@pytest.mark.parametrize("text,step", REVIEW_DOORS)
def test_a_reviewer_page_step_sentence_gets_its_door(text, step):
    got = P.page_step(text)
    assert got is not None and got.id == step, (text, got and got.id)


@pytest.mark.parametrize("text,item", REVIEW_LINES)
def test_a_reviewer_not_offered_sentence_gets_its_line(text, item):
    assert item in [n.id for n in P.not_offered(text)], text
    assert P.page_step(text) is None, text


@pytest.mark.parametrize("text", REVIEW_NEAR_MISSES)
def test_a_reviewer_everyday_sentence_names_nothing(text):
    assert P.page_step(text) is None, (text, P.page_step(text))
    assert P.not_offered(text) == [], (text, P.not_offered(text))
    assert P.prompt_block(text) == ""


def test_the_review_tables_are_big_enough():
    assert len(REVIEW_DOORS) + len(REVIEW_LINES) >= 40
    assert len(REVIEW_NEAR_MISSES) >= 40


# --------------------------------------------------------------------------- #
# linear time for the review's patterns, in a child process with a deadline
# --------------------------------------------------------------------------- #

_LINEAR_SCRIPT = r"""
import sys, time
from iron_jarvis.daemon import page_doors as P
pieces = [
    "you click on my ", "you ", "on my ", "me to log in ", "please run ", "sign me in to ",
    "pair the add-on ", "connect the ", "member id for ", "slack member ", "find my member ",
    "deepseek-r", "llama-x", "on my new laptop ", "the chrome extension ", "keeps disconnect",
    "google client id and ", "approve iron jarvis ", "claude code session ", "take control of my ",
    "jarvis use my ", ". use my ", "x" * 7,
]
n = int(sys.argv[1])
t0 = time.perf_counter()
for piece in pieces:
    text = (piece * (n // len(piece) + 1))[:n]
    for pat in P.ALL_PATTERNS:
        pat.search(text)
        pat.search(text + "!")
    P.page_steps(text); P.not_offered(text); P.prompt_block(text)
print(time.perf_counter() - t0)
"""


def test_the_reviews_patterns_are_linear_on_pathological_input():
    run = subprocess.run(
        [sys.executable, "-c", _LINEAR_SCRIPT, "60000"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert float(run.stdout.strip()) < 60.0, run.stdout
