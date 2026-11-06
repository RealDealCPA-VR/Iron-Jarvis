"""v1.442.0 (chat-first plan Wave 7, group A) — the independent review.

Each pin below was RED on the builder's commit (480cf68e) and is green after
the review's fix; the review's own words for what it found:

1. ``connectors.app_names`` read the "not the app" cases with a WHOLE-TEXT
   scan per app-name match: quadratic. 128,000 characters of ordinary prose
   mentioning OneDrive and Slack took ~7.6 s in ``record_tools_for`` (master:
   0.07 s), on a worker thread that holds the interpreter lock — the daemon
   froze for that long. It is linear now (one scan per text, bisect per hit).
2. Slack read as a notification destination on ANY "you can tell me / update
   me / alert me" in the sentence, so "Can you tell me who posted in Slack
   today?" no longer named the user's own Slack app. Only Slack as the PLACE
   Jarvis messages the user counts now.
3. A drive's synced folder blanked the app even when the same sentence SHARES
   it ("…and share it with Ann" is the app's); "saved … to my OneDrive folder"
   (past tense) was missed.
4. "Delete the webhook from Zapier" was read as Zapier calling in: no webhook
   tool, and an untrue-for-the-request "cannot reach" line.
5. Near-misses the new shapes armed a writer or gave a line for (each pinned
   against master's arming where it said nothing): a phone NUMBER / carrier,
   a desktop support queue or a desktop APP watched, "the backup of my
   settings FILE", "I always reply to…", "the microphone in Zoom", Ollama
   SUPPORT in an app / LM Studio for a relative / Ollama connected to another
   program, a Gmail ACCOUNT for someone else, Gmail's spam filter; and two
   older settings near-misses ("my voice memo app", "add Ollama to the
   comparison sheet").
6. ``channel_toggle`` turned two-way on for an email destination with no
   reading server (the Notifications page refuses that — routes/comm), and the
   inbox line told the model to set ``imap_host`` with channel_connect, which
   refuses a destination that already exists. The SMTP ``host`` took any text
   from chat, a ``user:password@`` included.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.connectors import app_names as AN
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import records as R
from iron_jarvis.tools.autoselect import select_ask_tools

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_WRITERS = {"config_set", "config_change", "config_change_protected"}


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    home = tmp_path_factory.mktemp("wave7review") / "home"
    return SimpleNamespace(platform=create_app(str(home)).state.platform)


def offered(d, text: str) -> set[str]:
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    ask = [t for t in select_ask_tools(text) if t not in armed]
    ask += CT.settings_ask_tools(d, text, armed, ask)
    return set(armed) | set(ask)


def lines(text: str) -> set[str]:
    return {n.id for n in P.not_offered(text)}


def step(text: str) -> str:
    s = P.page_step(text)
    return s.id if s else ""


# --------------------------------------------------------------------------- #
# 1. linear time on long ordinary text — a child process with a deadline
# --------------------------------------------------------------------------- #
_LONG = r"""
import sys, time
from iron_jarvis.connectors import app_names as AN
from iron_jarvis.settings import records as R
n = int(sys.argv[1])
prose = "We moved the client files into OneDrive last week and the Slack channel was busy. "
texts = [(p * (n // len(p) + 1))[:n] for p in (
    prose, "save it to my onedrive ", "slack ", "you can message me on slack ", "onedrive ",
    "back up my settings to my dropbox folder ", "share the google drive folder ",
)]
t0 = time.perf_counter()
for t in texts:
    AN.blank_not_the_app(t)
R.record_tools_for(texts[0])
print(time.perf_counter() - t0)
"""


def test_the_not_the_app_reading_is_linear_on_long_text():
    run = subprocess.run(
        [sys.executable, "-c", _LONG, "128000"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    # master's record_tools_for on the same prose: 0.07 s; the quadratic
    # reading took minutes on these texts.
    assert float(run.stdout.strip().splitlines()[-1]) < 10.0, run.stdout


# --------------------------------------------------------------------------- #
# 2. Slack: only as the PLACE Jarvis messages the user
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "Can you tell me who posted in Slack today?",
    "Could you update me on the ops channel in Slack?",
    "Can you tell me what is new in Slack?",
    "can you alert me when someone mentions me in slack?",
    "Can you message me the link to the Slack thread?",
])
def test_the_users_own_slack_is_still_the_app(text):
    assert "slack" in AN.apps_named(text), text


@pytest.mark.parametrize("text", [
    "Set up Slack so you can message me when a long job finishes.",
    "message me on Slack when the export is done",
    "Set up Slack for my alerts.",
    "Notify me via Slack when the backup fails.",
])
def test_slack_as_where_jarvis_messages_me_is_not_the_app(d, text):
    assert AN.apps_named(text) == [], text
    assert "app_connect" not in offered(d, text), text


# --------------------------------------------------------------------------- #
# 3. a drive's synced folder
# --------------------------------------------------------------------------- #
def test_sharing_the_synced_folder_is_the_drive_app():
    assert "google_drive" in AN.apps_named("Save it to my Google Drive folder named Taxes and share it with Ann.")
    assert AN.apps_named("Save it to my Google Drive folder named Taxes.") == []


def test_a_past_tense_save_to_the_synced_folder_is_not_the_app(d):
    text = "I saved the report to my OneDrive folder yesterday."
    assert AN.apps_named(text) == []
    assert "app_connect" not in offered(d, text)


# --------------------------------------------------------------------------- #
# 4. changing the webhook a service uses is not the service calling in
# --------------------------------------------------------------------------- #
def test_deleting_the_zapier_webhook_offers_the_webhook_tools(d):
    for text in ("Delete the webhook from Zapier.", "Turn off the webhook from Zapier."):
        assert "internet_calls_in" not in lines(text), text
    assert "webhook_delete" in offered(d, "Delete the webhook from Zapier.")
    # The service calling in still gets its line and no webhook tool.
    text = "Set up a webhook so Zapier can start a task."
    assert "internet_calls_in" in lines(text)
    assert not ({"webhook_add", "webhook_update", "webhook_delete"} & offered(d, text))


# --------------------------------------------------------------------------- #
# 5. near-misses, both ways
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "Send the summary to my phone number on the client's form.",
    "Text it to my phone carrier's support line.",
])
def test_a_phone_number_or_carrier_is_no_destination(d, text):
    assert not ({"channel_connect", "channel_toggle"} & offered(d, text)), text


def test_email_as_where_jarvis_messages_me_offers_the_destination(d):
    assert "channel_connect" in offered(d, "Please set up email so you can message me at work.")
    assert "channel_connect" in offered(d, "send it to my phone")


@pytest.mark.parametrize("text", [
    "We watch the desktop support queue every Monday.",
    "Watch my desktop app for crashes and tell me.",
])
def test_a_desktop_that_is_not_a_folder_is_not_watched(d, text):
    got = offered(d, text)
    assert "sentinel_add" not in got and not (SETTINGS_WRITERS & got), (text, got)


def test_a_desktop_folder_is_still_watched(d):
    assert "sentinel_add" in offered(d, "Watch my desktop folder and file new scans.")
    assert "watcher_suggests" in lines("Watch my desktop folder and file new scans.")


def test_a_settings_file_backup_is_not_jarvis(d):
    text = "Make sure the backup of my settings file is in git."
    assert "jarvis_backup" not in lines(text)
    assert not (SETTINGS_WRITERS & offered(d, text))
    assert "jarvis_backup" in lines("Back up my settings to my OneDrive folder.")


def test_a_habit_is_no_standing_rule(d):
    text = "I always reply to every new email from a client within a day."
    assert "reflex_create" not in offered(d, text) and step(text) != "email_rule_inbox"
    assert "reflex_create" in offered(d, "Always draft a reply to every new email from a client.")


def test_another_programs_microphone_is_no_voice_press():
    assert "voice_press" not in lines("Use the microphone in Zoom.")
    assert "voice_press" in lines("Turn on the microphone so I can talk to you.")
    assert "voice_press" in lines("use the mic")


@pytest.mark.parametrize("text", [
    "Add Ollama support to our Python app.",
    "Set up LM Studio for my brother.",
    "Connect Ollama to Open WebUI.",
])
def test_a_model_program_somewhere_else_is_no_model_server(d, text):
    assert "model_server_add" not in offered(d, text), text


@pytest.mark.parametrize("text", [
    "add LM Studio", "Connect my local Ollama.", "Add my local Ollama model and make it my default.",
    "connect Ollama to Jarvis",
])
def test_a_model_program_added_here_is_still_a_model_server(d, text):
    assert "model_server_add" in offered(d, text), text


@pytest.mark.parametrize("text", ["Set up a Gmail account for my new assistant.", "Turn on Gmail's spam filter."])
def test_a_new_gmail_account_or_a_gmail_setting_is_no_gmail_step(text):
    assert step(text) != "gmail_setup", text


def test_connecting_my_gmail_is_still_the_gmail_step():
    for text in ("Connect my Gmail account please.", "set up gmail for me",
                 "Connect my Gmail and summarize my latest emails every morning at 8 and send it to my phone."):
        assert step(text) == "gmail_setup", text


@pytest.mark.parametrize("text", ["Use my voice memo app to record the meeting.", "Add Ollama to the comparison sheet."])
def test_older_settings_near_misses_arm_no_settings_writer(d, text):
    assert not (SETTINGS_WRITERS & offered(d, text)), text


def test_the_settings_requests_still_reach_the_writers(d):
    for text in ("Turn on voice so I can talk to you.", "Read replies aloud.", "Make Gemini my default model.",
                 "Add my local Ollama model and make it my default."):
        assert "config_set" in offered(d, text), text


# --------------------------------------------------------------------------- #
# 6. an email destination's two-way and its servers
# --------------------------------------------------------------------------- #
def _ctx(p, tmp_path):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                       event_bus=p.event_bus, engine=p.engine)


@pytest.mark.asyncio
async def test_two_way_email_needs_its_reading_server_as_the_page_says(tmp_path):

    p = create_app(str(tmp_path / "home")).state.platform
    base = {"type": "email", "host": "smtp.example.com", "from_addr": "me@example.com",
            "to_addr": "me@example.com", "username": "me@example.com",
            "password_secret": "channel_inbox_token", "allowed_senders": ["client@example.com"]}
    R.apply_channel(p, "inbox", dict(base))
    tool = R.ChannelToggleTool(p)
    ctx = _ctx(p, tmp_path)
    res = await tool.execute({"name": "inbox", "mode": "two_way"}, ctx)
    assert not res.ok and "IMAP" in res.error and "Notifications" in res.error
    assert not p.config.comm["channels"]["inbox"].get("inbound_enabled")
    R.apply_channel(p, "inbox", {**base, "imap_host": "imap.example.com"})
    res = await tool.execute({"name": "inbox", "mode": "two_way"}, ctx)
    assert res.ok, res.error
    assert p.config.comm["channels"]["inbox"]["inbound_enabled"] is True


def test_the_inbox_line_says_where_an_existing_destination_gets_its_reading_server():
    rule = next(s for s in P.STEPS if s.id == "email_rule_inbox")
    assert "a destination that already exists" in rule.line and "Notifications" in rule.line
    # channel_connect really refuses a name that exists (the line's reason).
    src = (ROOT / "src/iron_jarvis/settings/records.py").read_text(encoding="utf-8")
    assert 'error=f"A channel called {name!r} already exists."' in src


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["me:secret@smtp.example.com", r"\\server\share", "smtp.example.com/x", "smtp .x"])
async def test_a_mail_server_from_chat_is_a_server_name(tmp_path, host):

    p = create_app(str(tmp_path / "home")).state.platform
    tool = R.ChannelConnectTool(p)
    res = await tool.execute({"name": "inbox", "type": "email", "host": host, "from_addr": "me@example.com",
                              "to_addr": "me@example.com"}, _ctx(p, tmp_path))
    assert not res.ok and "mail server name" in res.error, res
    assert "inbox" not in (p.config.comm.get("channels") or {})
    assert "secret" not in res.error


def test_a_mail_server_that_carries_a_password_is_never_shown_or_carded(tmp_path):
    import asyncio


    p = create_app(str(tmp_path / "home")).state.platform
    tool = R.ChannelConnectTool(p)
    args = {"name": "inbox", "type": "email", "host": "me:hunter2pw@smtp.example.com",
            "imap_host": "me:hunter2pw@imap.example.com", "from_addr": "me@example.com", "to_addr": "me@example.com"}
    shown = tool.redact_args(args)
    assert "hunter2pw" not in repr(shown)
    check = asyncio.run(tool.spend_check(args, _ctx(p, tmp_path)))
    assert check is not None and check.ask is False  # refused by execute, so no Allow that cannot run
    # A server name is shown as it is.
    assert tool.redact_args({**args, "host": "smtp.example.com", "imap_host": "imap.example.com"})["host"] == "smtp.example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [0, 70000, "abc"])
async def test_a_reading_port_out_of_range_is_refused(tmp_path, port):

    p = create_app(str(tmp_path / "home")).state.platform
    res = await R.ChannelConnectTool(p).execute(
        {"name": "inbox", "type": "email", "host": "smtp.example.com", "from_addr": "me@example.com",
         "to_addr": "me@example.com", "imap_host": "imap.example.com", "imap_port": port},
        _ctx(p, tmp_path),
    )
    assert not res.ok and "imap_port" in res.error
    assert "inbox" not in (p.config.comm.get("channels") or {})


_PATHOLOGICAL = r"""
import sys, time
from iron_jarvis.connectors import app_names as AN
from iron_jarvis.settings import setup_words as W
from iron_jarvis.settings import records_connections as RC
from iron_jarvis.settings import trigger as T
from iron_jarvis.daemon import page_doors as P
pieces = [
    "message me on slack ", "set up slack so ", "and message me on ", "you can tell me ", "alerts to my slack ",
    "save it to my onedrive folder and ", "share ", "set up email so you can ", "delete the webhook ",
    "add ollama to ", "add ollama for my ", "set up my gmail's ", "use the microphone in ", "to my phone number ",
    "i always ", "watch my desktop ", "backup of my settings ", "add ollama to the ", "a-", "' ",
]
n = int(sys.argv[1])
t0 = time.perf_counter()
for piece in pieces:
    text = (piece * (n // len(piece) + 1))[:n]
    for pat in (AN._SLACK_PLACE, AN._SLACK_CONTENT, AN._DRIVE_APP_ACTS, AN._SYNCED_FOLDER_DEST, RC._ADD_BRAND,
                P._GMAIL_CONNECT, *W.ALL_PATTERNS):
        pat.search(text); pat.search(text + "!")
    AN.blank_not_the_app(text); W.internet_calls_in(text); W.every_new_arrival(text)
    T.settings_ask(text)
print(time.perf_counter() - t0)
"""


def test_the_reviews_patterns_are_linear_on_pathological_input():
    run = subprocess.run(
        [sys.executable, "-c", _PATHOLOGICAL, "60000"],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert float(run.stdout.strip().splitlines()[-1]) < 30.0, run.stdout


def test_an_outgoing_webhook_for_a_service_stays_offered(d):
    """The builder's own exception, pinned (it survived a mutation): asking
    for an OUTGOING webhook keeps webhook_add even when the same message says
    the service should reach in."""
    text = "Add an outgoing webhook so Zapier can reach you."
    assert "webhook_add" in offered(d, text)
