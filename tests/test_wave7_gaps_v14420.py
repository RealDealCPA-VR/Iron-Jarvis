"""v1.442.0 (chat-first plan Wave 7, group A) — THE LAST SETUP GAPS.

The owner: "if the app offers something, a user can do all the setup for it
from chat — no extra buttons." Wave 7 proves it with the review's 30 setup
requests. Run through the REAL arming, ten still armed the wrong tool or were
told nothing true about what Iron Jarvis does not do:

1. "Connect my Gmail … and send it to my phone" — no destination tool for
   "to my phone", and no door to Gmail's one-time Google developer setup
   (app_connect saved Gmail, said "did not start", and left no button).
2. "Set up Slack so you can message me…" — no door to the Slack app the user
   makes on Slack's site, and the Slack APP (app_connect) armed beside the
   destination, with a "Connect Slack" door: the wrong Slack.
3. "Add my local Ollama model and make it my default" — model_server_add not
   offered (an add verb + the program's name counted only with an address).
8. "Schedule a weekly backup of my settings to my OneDrive folder" — the
   schedule tools and the OneDrive APP, never the "Backup copy folder"
   setting; and nothing said that backups run daily by themselves.
17. "Watch my Downloads folder and file new PDFs…" — nothing said that a
   watcher only SUGGESTS, nor offered the switch that turns watchers on.
18. "Create a workflow that drafts a reply to every new email…" — no rule
   tool, no email destination, nothing about who an email rule hears.
19. "Set a monthly spending limit for my builder agent" — the builder is
   built in; nothing said only an agent the user made can have a limit.
23. "Turn on voice so I can talk to you" — talking is a press, not a setting.
24. "Make Build › Code use Codex … in Full access for this repo" — no folder
   defaults tool, and the Full access line never fired.
26. "Set up a webhook so Zapier can start a task" — webhook_add offered as
   the way in, though nothing on the internet can reach Iron Jarvis.

Pinned here: each gap through the REAL arming both chat lanes call (and its
near-misses, both ways), each new line against the CODE that makes it true
(never its own wording), the door a saved-but-not-started app leaves through
the real stream lane, the reading server an email destination now takes, and
every new pattern linear in a child process with a deadline.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.connectors import app_names as AN
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import doors as D
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import records as R
from iron_jarvis.settings import setup_words as W
from iron_jarvis.settings.trigger import settings_ask
from iron_jarvis.tools.autoselect import select_ask_tools

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
SRC = ROOT / "src" / "iron_jarvis"

SETTINGS_WRITERS = ("config_set", "config_change", "config_change_protected")


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    home = tmp_path_factory.mktemp("wave7") / "home"
    return SimpleNamespace(platform=create_app(str(home)).state.platform)


def offered(d, text: str) -> set[str]:
    """What both chat lanes offer for *text* (the POST lane's armed set plus
    the stream lane's ask tier) — the arming table's own reading."""
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


def _line(item_id: str) -> str:
    return {**{s.id: s.line for s in P.STEPS}, **{n.id: n.line for n in P.NOT_OFFERED}}[item_id]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# =========================================================================== #
# 1. Gmail + "send it to my phone"
# =========================================================================== #
GMAIL = "Connect my Gmail and summarize my latest emails every morning at 8 and send it to my phone."


def test_send_it_to_my_phone_offers_the_destination_tools(d):
    got = offered(d, GMAIL)
    assert {"schedule_create", "app_connect", "channel_connect", "channel_toggle"} <= got
    for text in ("text me on my phone when the report is done", "send me the summary on my phone",
                 "I want alerts on my phone when a run fails"):
        assert "channel_connect" in offered(d, text), text
    # Never someone's photos, never a phone's own reminders.
    for text in ("send the photos to my phone", "set up reminders on my iphone", "my phone is broken",
                 "call my phone when you get a chance"):
        assert not ({"channel_connect", "channel_toggle"} & offered(d, text)), text


def test_connecting_gmail_names_its_google_setup_and_its_card(d):
    assert step(GMAIL) == "gmail_setup"
    assert step("set up gmail for me") == "gmail_setup"
    gmail = next(s for s in P.STEPS if s.id == "gmail_setup")
    assert gmail.href == D._APPS_FIX_HREF and gmail.door() == {
        "href": D._APPS_FIX_HREF, "label": "Open Gmail's card in Settings"}
    for text in ("connect my Gmail to Outlook on my phone", "add a Gmail filter for the newsletters",
                 "check my Gmail", "google it for me"):
        assert step(text) != "gmail_setup", text
    # The setup words themselves keep Google's own step (Drive's and Gmail's).
    assert step("I need a Gmail OAuth client") == "google_setup"


def test_the_gmail_line_is_what_its_card_and_app_connect_really_do():
    from iron_jarvis.connectors.catalog import get_connector

    gmail = get_connector("gmail")
    assert gmail.connect_via == "mcp" and "Google developer setup" in gmail.needs and gmail.docs_url
    line = _line("gmail_setup")
    assert P.DIRECTORY_PLACE in line and "program's own page on GitHub" in line and "one-time Google developer setup" in line
    # app_connect says it did not start while the setup is missing (its own
    # words, settings.records), so the line may say the tools are not there.
    src = _read("src/iron_jarvis/settings/records.py")
    assert 'f"Saved {conn.name}, but it did not start' in src


def test_a_saved_app_that_did_not_start_leaves_the_button_to_its_card():
    gmail = SimpleNamespace(ok=True, data={"app": "gmail", "not_started": True, "needs_setup": True})
    assert D.door_for("app_connect", gmail) == {"href": D._APPS_FIX_HREF, "label": "Finish setting up Gmail in Settings"}
    other = SimpleNamespace(ok=True, data={"app": "notion", "not_started": True, "needs_setup": False})
    assert D.door_for("app_connect", other) == {"href": D._APPS_FIX_HREF, "label": "See why Notion did not start"}
    # An app that started, a forged id, no data: no door.
    assert D.door_for("app_connect", SimpleNamespace(ok=True, data={"app": "gmail"})) is None
    assert D.door_for("app_connect", SimpleNamespace(ok=True, data={"app": "nope", "not_started": True})) is None
    assert D.door_for("app_connect", SimpleNamespace(ok=True, data=None)) is None


@pytest.mark.asyncio
async def test_the_stream_lane_leaves_gmails_door_after_app_connect_saves_it(tmp_path, monkeypatch):
    """The real stream lane: app_connect saves Gmail, its pack does not start
    (stubbed — nothing is launched), the reply carries ONE door to its card."""
    from tests.test_apps_destinations_v14350 import _app, _approve_all
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _done

    monkeypatch.setattr(R, "apply_app", lambda platform, cid, cfg, load=True: 0)
    app = _app(tmp_path, [("app_connect", {"app": "gmail"})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body(GMAIL), await _approve_all(app, seen))
    assert seen == ["app_connect"]
    finished = [x for ev, x in frames if ev == "tool_call" and x.get("status") == "finished"]
    assert any("did not start" in json.dumps(x) for x in finished)
    doors = _done(frames)["doors"]
    assert doors == [{"href": D._APPS_FIX_HREF, "label": "Finish setting up Gmail in Settings"}], doors


# =========================================================================== #
# 2. Slack as the place Jarvis messages the user
# =========================================================================== #
SLACK = "Set up Slack so you can message me when a long job finishes."


def test_slack_as_a_destination_is_never_the_slack_app(d):
    got = offered(d, SLACK)
    assert "channel_connect" in got and "app_connect" not in got
    assert AN.apps_named(SLACK) == [] and AN.own_data_apps(SLACK) == []
    assert AN.apps_named("message me on Slack when the export is done") == []
    # The user's own Slack is still the app (the v13610 table holds these).
    for text in ("did anyone message me on slack?", "remind me on slack tomorrow at 9",
                 "connect my Slack so you can read my channels and message me", "connect my Slack"):
        assert "slack" in AN.apps_named(text), text


def test_setting_up_slack_to_message_me_gets_the_slack_app_door():
    assert step(SLACK) == "slack_setup"
    assert step("connect Slack for my notifications") == "slack_setup"
    for text in ("message me on Slack when the export is done", "the Slack channel was noisy today",
                 "connect my Slack"):
        assert step(text) != "slack_setup", text


# =========================================================================== #
# 3. Ollama with no address
# =========================================================================== #
def test_adding_ollama_by_name_offers_model_server_add(d):
    got = offered(d, "Add my local Ollama model and make it my default.")
    assert {"model_server_add", "model_server_default", "config_set"} <= got
    assert "model_server_add" in offered(d, "add LM Studio")
    for text in ("add Ollama to the comparison sheet", "set up Ollama on my laptop",
                 "add a paragraph about Ollama to the README", "the Ollama server docs are confusing"):
        assert "model_server_add" not in offered(d, text), text


def test_the_tool_is_told_ollamas_own_address_the_app_already_uses():
    from iron_jarvis.daemon.routes.settings import OLLAMA_LOCAL_URL
    from iron_jarvis.settings.records_connections import ModelServerAddTool

    assert OLLAMA_LOCAL_URL == "http://localhost:11434"
    assert f"Ollama {OLLAMA_LOCAL_URL}" in ModelServerAddTool.description
    assert "LM Studio http://localhost:1234" in ModelServerAddTool.description


# =========================================================================== #
# 8. Iron Jarvis's own backup
# =========================================================================== #
BACKUP = "Schedule a weekly backup of my settings to my OneDrive folder."


def test_a_backup_of_jarvis_reaches_its_backup_setting_never_a_schedule_or_the_app(d):
    got = offered(d, BACKUP)
    assert set(SETTINGS_WRITERS) <= got
    assert not ({"schedule_create", "schedule_update", "app_connect"} & got), got
    assert AN.apps_named(BACKUP) == [] and AN.own_data_apps(BACKUP) == []
    assert "backup_mirror_dir" in settings_ask("set the backup copy folder to my OneDrive folder").keys
    # A schedule elsewhere in the message still counts.
    both = BACKUP + " Every Monday at 9 send me a digest."
    assert "schedule_create" in offered(d, both)
    # A folder SHARED in Dropbox is the app's; a user's own file is not Jarvis's backup.
    assert "dropbox" in AN.apps_named("share the dropbox folder with Ann")
    for text in ("we need a backup plan for the meeting", "make a backup of the report to my USB drive",
                 "copy the backup to the USB folder", "back up my client files to the NAS"):
        assert not W.jarvis_backup(text), text
        assert "jarvis_backup" not in lines(text), text


def test_the_backup_line_is_what_the_backup_loop_and_settings_do():
    assert "jarvis_backup" in lines(BACKUP)
    assert "jarvis_backup" in lines("Back up everything to my external drive automatically.")
    app_src = _read("src/iron_jarvis/daemon/app.py")
    assert 'os.environ.get("IRONJARVIS_AUTO_BACKUP_HOURS", "24")' in app_src
    assert 'os.environ.get("IRONJARVIS_AUTO_BACKUP_KEEP", "7")' in app_src
    from iron_jarvis.settings import schema

    keys = {s.key: s.label for s in schema.SETTINGS}
    assert keys["backup_mirror_dir"] == "Backup copy folder"
    assert keys["backup_mirror_media"] == "Copy media with backups"
    # No setting says how often (the line says so).
    assert not [k for k in keys if "backup" in k and re.search(r"hour|interval|often|every|keep", k)]
    home = _read("dashboard/components/settings/SettingsHome.tsx")
    assert 'id: "system-maintenance"' in home and 'label: "Maintenance"' in home
    assert "Back up now" in _read("dashboard/components/settings/MaintenanceCard.tsx")
    line = _line("jarvis_backup")
    assert "once a day (every 24 hours)" in line and "newest 7" in line
    assert '"Backup copy folder"' in line and '"Copy media with backups"' in line
    assert "Back up now in Settings › System › Maintenance" in line


# =========================================================================== #
# 17. A folder watcher
# =========================================================================== #
WATCH = "Watch my Downloads folder and file new PDFs into the right client folder."


def test_a_watched_folder_offers_the_watcher_and_its_switch(d):
    got = offered(d, WATCH)
    assert {"sentinel_add", "config_set"} <= got
    assert "watcher_suggests" in lines(WATCH)
    for text in ("watch the Downloads movie", "monitor my desktop usage", "watch my desktop wallpaper change"):
        assert "sentinel_add" not in offered(d, text), text
        assert "watcher_suggests" not in lines(text), text
    # Watching alone is a watcher, without the "it only suggests" line.
    assert "watcher_suggests" not in lines("watch my Downloads folder")


def test_the_watcher_line_is_what_a_watcher_really_does():
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings import schema

    src = _read("src/iron_jarvis/motivation/engine.py")
    i = src.index("def add_backlog")
    assert "it never spawns a session" in src[i:i + 1500]
    assert "Autonomy → Proposals" in _read("dashboard/app/sentinels/page.tsx")
    assert {s.key: s.label for s in schema.SETTINGS}["sentinels_enabled"] == "Folder watchers"
    assert Config.model_fields["sentinels_enabled"].default is False
    line = _line("watcher_suggests")
    assert '"Folder watchers"' in line and "Proposals list on the Autonomy page" in line
    assert "nothing runs until the user approves it" in line


# =========================================================================== #
# 18. A workflow on every new email
# =========================================================================== #
EMAIL_RULE = "Create a workflow that drafts a reply to every new email from a client."


def test_every_new_email_offers_the_rule_and_the_email_destination(d):
    got = offered(d, EMAIL_RULE)
    assert {"reflex_create", "channel_connect", "channel_toggle"} <= got
    assert step(EMAIL_RULE) == "email_rule_inbox"
    assert {"reflex_create"} <= offered(d, "build a rule that files every new invoice from a vendor")
    # A one-off chore is no rule.
    for text in ("reply to every new email in my inbox now", "summarize every new email from today"):
        assert "reflex_create" not in offered(d, text), text
        assert step(text) != "email_rule_inbox", text


def test_the_email_rule_line_is_what_inbound_mail_really_needs():
    # Two-way email needs its reading server (the Notifications form's own
    # check), and the inbox is read there.
    comm = _read("src/iron_jarvis/daemon/routes/comm.py")
    assert "Email two-way (inbound) needs an IMAP host" in comm
    assert 'host = cfg.get("imap_host") or cfg.get("host")' in _read("src/iron_jarvis/comm/channels.py")
    inbound = _read("src/iron_jarvis/comm/inbound.py")
    assert "FAIL-CLOSED allowlist. An unauthorized sender spawns NOTHING." in inbound
    i = inbound.index("FAIL-CLOSED allowlist")
    assert inbound.index("if not ch.is_authorized(msg.sender_id)", i) < inbound.index("reflex_router.on_signal")
    assert '"Allowed senders"' in _read("dashboard/components/settings/pages/NotificationsPage.tsx")
    rec = _read("src/iron_jarvis/settings/records.py")
    assert "has no allowlist yet — add who may message it on the Notifications page first" in rec
    rule = next(s for s in P.STEPS if s.id == "email_rule_inbox")
    assert rule.href == P.NOTIFICATIONS_HREF
    assert "Allowed senders list" in rule.line and "imap_host" in rule.line and "cannot fill" in rule.line


@pytest.mark.asyncio
async def test_an_email_destination_from_chat_can_take_its_reading_server(tmp_path, monkeypatch):
    from tests.test_apps_destinations_v14350 import _app, _approve_all
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body

    app = _app(tmp_path, [("channel_connect", {
        "name": "inbox", "type": "email", "host": "smtp.example.com", "from_addr": "me@example.com",
        "to_addr": "me@example.com", "imap_host": "imap.example.com", "imap_port": 993,
    })])
    p = app.state.platform
    seen: list[str] = []
    await _drive_stream(app, _body("add an email destination that reads my mail"), await _approve_all(app, seen))
    cfg = p.config.comm["channels"]["inbox"]
    assert cfg["imap_host"] == "imap.example.com" and cfg["imap_port"] == 993
    # Its inbox is read on THAT server, never on the sending one.
    from iron_jarvis.comm import channels as C

    hosts: list[tuple[str, int]] = []

    def fake_connect(host, port):
        hosts.append((host, port))
        raise OSError("no network in tests")

    monkeypatch.setattr(C, "_imap_connect", fake_connect)
    ch = C.EmailChannel(cfg, secret_resolver=lambda _k: "app-password-v14420")
    assert ch.poll(0) == ([], 0)
    assert hosts == [("imap.example.com", 993)]


@pytest.mark.asyncio
async def test_a_bad_reading_server_is_refused(tmp_path):
    from tests.test_apps_destinations_v14350 import _app, _approve_all, _finished, _text
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body

    app = _app(tmp_path, [("channel_connect", {
        "name": "inbox", "type": "email", "host": "smtp.example.com", "from_addr": "me@example.com",
        "to_addr": "me@example.com", "imap_host": "imap.example.com/../x",
    })])
    frames = await _drive_stream(app, _body("add an email destination"), await _approve_all(app, []))
    assert "must be a mail server name" in _text(_finished(frames, "channel_connect"))
    assert "inbox" not in (app.state.platform.config.comm.get("channels") or {})


# =========================================================================== #
# 19. A limit on a built-in agent
# =========================================================================== #
def test_a_limit_on_a_builtin_agent_is_said_plainly(d):
    text = "Set a monthly spending limit for my builder agent."
    assert "builtin_agent_limit" in lines(text)
    assert "agent_create" in offered(d, text)
    for t in ("the builder agent's spending report", "set a monthly budget for the house builder",
              "how much did the builder agent spend?"):
        assert "builtin_agent_limit" not in lines(t), t


def test_the_builtin_limit_line_is_what_the_agents_tools_do(tmp_path):
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.settings.records_projects import WORKS_LIKE, AgentCreateTool, resolve_agent

    assert set(W.BUILTIN_AGENT_NAMES) == {t.value for t in AgentType}
    assert "builder" in WORKS_LIKE
    props = AgentCreateTool.input_schema["properties"]
    assert {"monthly_limit_usd", "monthly_limit_tokens", "works_like"} <= set(props)
    platform = create_app(str(tmp_path)).state.platform
    name, why = resolve_agent(platform, "my builder agent")
    assert name == "" and "own agents have no monthly limit" in why and "working like the builder" in why
    _n, why_sup = resolve_agent(platform, "supervisor")
    assert "working like" not in why_sup and "monthly limit" in why_sup
    allowance = _read("src/iron_jarvis/agents/allowance.py")
    assert "A custom agent's monthly allowance" in allowance


# =========================================================================== #
# 23. Voice
# =========================================================================== #
def test_voice_is_said_to_be_a_press(d):
    text = "Turn on voice so I can talk to you."
    assert "voice_press" in lines(text)
    assert "config_set" in offered(d, text)  # read aloud is still a setting
    for t in ("talk to you tomorrow", "the voice of the letter should be warmer", "use my voice memo app",
              "can I talk to you about my taxes?", "start the presentation"):
        assert "voice_press" not in lines(t), t
    assert "voice_press" in lines("can I talk to you out loud instead of typing?")


def test_the_voice_line_names_the_chat_pages_own_controls():
    page = _read("dashboard/app/chat/page.tsx")
    assert '"Dictate your message"' in page and '"Voice chat"' in page
    assert "Speak, hear the replies, hands-free" in page
    from iron_jarvis.settings import schema

    assert {s.key: s.label for s in schema.SETTINGS}["device.read_aloud"] == "Read replies aloud"
    line = _line("voice_press")
    assert '"Voice chat"' in line and "microphone button" in line and '"Read replies aloud"' in line


# =========================================================================== #
# 24. Build › Code in Full access for this repo
# =========================================================================== #
def test_a_coding_views_defaults_for_a_repo_are_offered_and_full_access_is_said(d):
    text = "Make Build › Code use Codex on my work account in Full access for this repo."
    assert "build_defaults_set" in offered(d, text)
    assert "full_access_default" in lines(text)
    for t in ("give Sam full access to this repo", "make sure Sam has full access for the project folder"):
        assert "full_access_default" not in lines(t), t
    from iron_jarvis.settings.records_build import ACCOUNT_NOT_KEPT, BuildDefaultsSetTool

    assert "Full access are never a folder default" in BuildDefaultsSetTool.description
    assert "An account is not kept as a folder default" in ACCOUNT_NOT_KEPT


# =========================================================================== #
# 26. A web service calling in
# =========================================================================== #
ZAPIER = "Set up a webhook so Zapier can start a task."


def test_a_web_service_calling_in_is_never_offered_a_webhook_as_the_way(d):
    got = offered(d, ZAPIER)
    assert not ({"webhook_add", "webhook_update", "webhook_delete"} & got), got
    assert "internet_calls_in" in lines(ZAPIER)
    # Sending OUT to such a service is a webhook Iron Jarvis has.
    out = "add an outgoing webhook that sends my events to Zapier"
    assert "webhook_add" in offered(d, out) and "internet_calls_in" not in lines(out)
    for t in ("Zapier pricing", "is Zapier better than IFTTT?", "Zapier sent me an invoice",
              "send my events to Zapier with a webhook", "draft an email about Zapier"):
        assert "internet_calls_in" not in lines(t), t
    # A webhook a local program calls is untouched.
    assert "webhook_add" in offered(d, "add a webhook for new Stripe payments")


def test_the_calling_in_line_is_what_the_door_and_the_desktop_do():
    from iron_jarvis.daemon.auth import _is_exempt

    assert not _is_exempt("/webhooks/zap") and not _is_exempt("/webhooks/anything")
    main = _read("desktop/main.js")
    assert 'if (!on || ips.length === 0) return { host: "127.0.0.1", env: {}, lan: [] };' in main
    from iron_jarvis.settings import schema

    lan = {s.key: s.label for s in schema.SETTINGS}["lan_access"]
    line = _line("internet_calls_in")
    assert f'"{lan}"' in line and "private access token" in line and "outgoing webhook" in line


# =========================================================================== #
# the lines speak plainly and the table stays honest
# =========================================================================== #
NEW_LINES = ("gmail_setup", "email_rule_inbox", "jarvis_backup", "watcher_suggests", "builtin_agent_limit",
             "voice_press", "internet_calls_in")


def test_the_new_lines_speak_plainly_and_name_places_as_the_screen_does():
    banned = re.compile(r"\b(?:daemon|endpoint|oauth|mcp|pane|job|harness|connector|plug-in|mock|sentinels?)\b", re.I)
    for item in NEW_LINES:
        line = _line(item)
        # "Folder watchers (sentinels)" is the setting's own label.
        assert not banned.search(line.replace("(sentinels)", "")), item
        assert "plainly" in line, item
    # No line names a retired Settings row.
    home = _read("dashboard/components/settings/SettingsHome.tsx")
    for name in re.findall(r'formerly: "([^"]+)"', home):
        for item in NEW_LINES:
            assert name not in _line(item), (item, name)


def test_one_message_never_loses_a_not_offered_line_to_the_cap():
    text = (
        "Set up a webhook so Zapier can start a task, turn on voice so I can talk to you, "
        "set a monthly spending limit for my builder agent and back up my settings weekly."
    )
    got = lines(text)
    assert {"internet_calls_in", "voice_press", "builtin_agent_limit", "jarvis_backup"} <= got
    block = P.prompt_block(text)
    assert block.count("\n- ") == P.MAX_LINES
    for item in ("internet_calls_in", "voice_press", "builtin_agent_limit", "jarvis_backup"):
        assert _line(item) in block


# =========================================================================== #
# LINEAR TIME — pathological input, a child process, a deadline
# =========================================================================== #
_LINEAR = r"""
import sys, time
from iron_jarvis.settings import setup_words as W
from iron_jarvis.connectors import app_names as AN
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_connections as RC
from iron_jarvis.settings import records_build as RB
pieces = [
    "back up my settings to my ", "backup of all my ", "send it to my ", "every new email ", "watch my ",
    "the builder agent ", "set a monthly limit ", "turn on voice ", "talk to you out loud ", "so zapier can ",
    "let zapier ", "webhook from zapier ", "save it to my onedrive ", "you can message me ", "slack ",
    "connect my gmail ", "make build code use codex ", "in full access for this repo ", "add my local ollama ",
    "a ", " ", "x-", "'s ",
]
n = int(sys.argv[1])
t0 = time.perf_counter()
for piece in pieces:
    text = (piece * (n // len(piece) + 1))[:n]
    for pat in (*W.ALL_PATTERNS, AN._SYNCED_FOLDER_DEST, AN.ASKS_TO_MESSAGE_ME, AN._SLACK_CONTENT,
                RC._ADD_BRAND, RB._DEFAULTS_SET, R._CREATE_SENTINEL):
        pat.search(text); pat.search(text + "!")
    W.every_new_email(text); W.watch_and_act(text); W.builtin_agent_limit(text); W.jarvis_backup(text)
    W.without_backup_sentences(text)
    AN.blank_not_the_app(text[:4000]); AN.apps_named(text)
    P.page_steps(text); P.not_offered(text)
    R._record_tools_named(text[:4000])
print(time.perf_counter() - t0)
"""


def test_every_new_pattern_is_linear_on_pathological_input():
    """60,000 characters of each pattern's own words with no sentence end: a
    quadratic pattern takes minutes; these take well under the deadline. In a
    CHILD process, so a hang fails by name instead of hanging the suite."""
    run = subprocess.run(
        [sys.executable, "-c", _LINEAR, "60000"],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert float(run.stdout.strip()) < 90.0, run.stdout
