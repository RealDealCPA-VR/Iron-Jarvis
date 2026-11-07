"""v1.434.0 (chat-first plan, Wave 2 group E): THE ARMING TABLE.

"If a user asks for something that is an option with this app, they should be
able to do all the configuration needed for that task right in chat." The
reviewers measured ~180 plain requests at v1.432.0 (their scripts, seeded
here): 29 of 57 requests about a setting armed nothing, "switch updates to the
stable channel" armed the NOTIFICATION channel tools, asking to CREATE a
schedule / goal / webhook / watcher offered only edit and delete, "run the
triage workflow now" could not run it, "whenever a new email comes in…"
offered no rule tool, and "rename the Intercom project" armed Code Lab.

Every row runs through the REAL arming functions both chat lanes call:
``chat_turn._resolve_armed_tools`` (the POST lane's whole arming, and the
stream lane's ``armed``) and ``chat_turn.settings_ask_tools`` with
``autoselect.select_ask_tools`` (the stream lane's ask tier), on a REAL
platform's registry. A row says what MUST be offered and which writer
FAMILIES may be (anything else is an unrelated writer). Then the seams are
driven end to end: the stream lane cards a create tool, the build-turn route
arms the settings tools in a coding session, Auto tools off arms the reader.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import records as R
from iron_jarvis.settings.trigger import settings_ask
from iron_jarvis.tools.autoselect import select_ask_tools

#: A request to CHANGE a setting: the reader, the allow-tier writer (both
#: lanes) and the two ask-tier writers (the stream lane's card).
SET = ("config_list", "config_set", "config_change", "config_change_protected")
#: Only the reader.
READ = ("config_list",)

FAMILIES: dict[str, frozenset[str]] = {
    "settings": frozenset({"config_set", "config_change", "config_change_protected", "config_secret"}),
    "schedule": frozenset({"schedule_create", "schedule_update", "schedule_delete", "workflow_schedule"}),
    "workflow": frozenset(
        {"workflow_create", "workflow_update", "workflow_delete", "workflow_schedule", "workflow_run"}
    ),
    # v1.435.0 (Wave 3 group H, moved on purpose): a destination's removal
    # and alerts, an app's switch / disconnect / import.
    "channel": frozenset({"channel_toggle", "channel_connect", "channel_remove", "channel_alerts"}),
    "app": frozenset({"app_connect", "app_switch", "app_disconnect", "app_import"}),
    "webhook": frozenset({"webhook_add", "webhook_update", "webhook_delete"}),
    "sentinel": frozenset({"sentinel_add", "sentinel_update", "sentinel_delete"}),
    "goal": frozenset({"goal_add", "goal_update", "goal_delete"}),
    "reflex": frozenset({"reflex_create", "reflex_update", "reflex_delete"}),
    "grant": frozenset({"grant_revoke"}),
    "undo": frozenset({"config_undo"}),
}
WRITERS = frozenset().union(*FAMILIES.values())

S, SC, WF, CH, AP, WH, SE, GO, RE, GR, UN = (
    "settings", "schedule", "workflow", "channel", "app", "webhook", "sentinel", "goal", "reflex",
    "grant", "undo",
)

# ---------------------------------------------------------------------------
# The 57 measured requests about a setting (ij-review-settings/phrases*.py).
# ---------------------------------------------------------------------------
SETTINGS_ROWS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("turn on dark mode", SET, (S,)),
    ("use the Daylight theme", SET, (S,)),
    ("switch to the Liquid Glass theme", SET, (S,)),
    ("keep my PC awake while you work", SET, (S,)),
    ("switch updates to the stable channel", SET, (S,)),
    ("put me on stable updates", SET, (S,)),
    ("stop asking me before running commands", SET, (S,)),
    ("don't ask me for approval anymore", SET, (S,)),
    ("let other computers on my network use Jarvis", SET, (S,)),
    # No row of its own (Local only is a per-chat switch): the reader, so
    # the model can say where it lives.
    ("make new chats start in local only", READ, (S,)),
    ("change my default model to Sonnet", SET, (S,)),
    ("use Sonnet by default", SET, (S,)),
    ("set the night shift to start at 10pm", SET, (S,)),
    ("start night tasks at 22:00", SET, (S,)),
    ("turn off follow-up suggestions", SET, (S,)),
    ("stop suggesting follow-up questions", SET, (S,)),
    ("read your replies out loud", SET, (S,)),
    ("make your answers shorter", SET, (S,)),
    # A lasting preference about the user: remember_preference's job.
    ("from now on call me Victor", ("remember_preference",), (S,)),
    ("turn on the emergency stop", SET, (S,)),
    ("stop everything you're doing in the background", SET, (S,)),
    ("don't let any background job run", SET, (S,)),
    ("only let Jarvis read my Documents folder", SET, (S,)),
    ("back up to my D drive too", SET, (S,)),
    (r"copy my backups to D:\Backups", SET, (S,)),
    ("pause the folder watchers", SET, (S, SE)),
    ("never run the shell tool", SET, (S,)),
    ("use Docker for running commands", SET, (S,)),
    ("set the max steps per run to 30", SET, (S,)),
    ("share my profile with Claude Code", SET, (S,)),
    ("turn on Iron-Proxy", SET, (S,)),
    ("use several accounts", SET, (S,)),
    ("let jarvis act in my browser", SET, (S,)),
    ("stop sending me Slack notifications", SET, (S, CH, AP)),
    ("change my persona to Concise", SET, (S,)),
    ("talk to me like a pirate by default", SET, (S,)),
    ("use my Ollama server at http://192.168.1.5:11434", SET, (S,)),
    ("don't fall back to the cloud if my local model fails", SET, (S,)),
    ("open files in Cursor", SET, (S,)),
    ("forget the theme, go back to the default", SET, (S,)),
    # A QUESTION arms only the reader (the question guard).
    ("what is my default model?", READ, ()),
    ("make the app animated background aurora", READ, (S,)),
    ("turn on simple mode", READ, (S,)),
    ("turn off web search for chats", SET, (S,)),
    ("disable the file search tool", SET, (S,)),
    ("enable reading files in my Documents folder only", SET, (S,)),
    (r"set the backup folder to D:\Backups and look up what is in it", SET, (S,)),
    ("change my default model to the one in this doc", SET, (S,)),
    ("switch off follow-up questions", SET, (S,)),
    ("turn off read aloud", SET, (S,)),
    ("set approvals to don't ask", SET, (S,)),
    ("make chats ask before every action", SET, (S,)),
    ("turn on keep awake", SET, (S,)),
    ("set update channel to stable", SET, (S,)),
    ("set the theme to Arc Cyan", SET, (S,)),
    ("enable lan access", SET, (S,)),
    ("turn on local only for new chats", READ, (S,)),
]

# ---------------------------------------------------------------------------
# The measured record requests (ij-review-records/arm*.py). An empty "offer"
# is a request no tool in this wave serves (projects, agents, skills — the
# plan's B2): it must arm no unrelated writer.
# ---------------------------------------------------------------------------
RECORD_ROWS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (r"add a project for the Smith tax return in D:\Clients\Smith", (), ()),
    ("rename the Intercom project", (), ()),
    ("delete the Intercom project", (), ()),
    ("create an agent called bookkeeper that uses Opus", (), ()),
    ("delete the bookkeeper agent", (), ()),
    ("pause the bookkeeper agent", (), ()),
    ("make a schedule that runs every weekday at 8am", ("schedule_create",), (SC,)),
    ("delete my nightly-brief schedule", ("schedule_delete",), (SC,)),
    ("add my LM Studio server at 192.168.1.20:1234", SET, (S,)),
    ("add a second Claude account", (), (S,)),
    ("put the work and personal accounts in separate groups", SET, (S,)),
    ("add a Slack destination for notifications", ("channel_connect",), (CH, AP, S)),
    ("turn off the GitHub app", ("app_connect",), (AP, S)),
    ("connect my GitHub", ("app_connect", "config_secret"), (AP, S)),
    ("create a workflow that summarizes my inbox every Friday", ("schedule_create",), (WF, SC, AP)),
    ("add a goal to file the Smith return by April 15", ("goal_add",), (GO,)),
    ("add a sentinel that watches https://irs.gov for changes", ("sentinel_add",), (SE, S)),
    ("add a webhook for new Stripe payments", ("webhook_add",), (WH, AP)),
    # v1.442.0 (Wave 7, moved on purpose): a rule on new EMAIL is heard only
    # through an email destination with two-way on, so its tools come too.
    ("when a client email arrives, start an intake session", ("reflex_create",), (RE, CH)),
    ("add a remote agent called hermes at http://192.168.1.5:9000", (), ()),
    ("save this as a skill called month-end close", (), ()),
    ("forget that preference about short answers", (), (S,)),
    ("from now on keep answers short", ("remember_preference",), (S,)),
    ("delete the lesson about formatting", (), ()),
    ("create a persona called Accountant that is formal", (), (S,)),
    ("switch to dark mode", SET, (S,)),
    ("set my default model to Opus", SET, (S,)),
    (r"add a memory base pointing at my Obsidian vault D:\Vault", READ, (S,)),
    (r"queue a night task to refactor the parser in D:\code\app", (), ()),
    (r"open a new terminal in D:\code\app", (), ()),
    (r"start a coding session in D:\code\app", (), ()),
    ("assign the bookkeeper agent to reconcile the bank account", (), ()),
    ("revoke the always allow for shell", ("grant_revoke",), (GR, S)),
    ("save my OpenAI API key", ("config_secret",), (S,)),
    ("make a backup now", (), ()),
    ("remove the Slack channel", ("channel_toggle", "channel_connect"), (CH, AP, S)),
    ("add Codex to my model team as a reviewer", (), (S,)),
    ("add a prompt template for client engagement letters", (), ()),
    ("change the Brain profile to Notion", SET, (S, AP)),
    ("turn on night shift", READ, (S,)),
    (r"set the folder default model for D:\code\app to Sonnet", (), (S,)),
    ("update my profile: my name is Victor and I prefer formal tone", (), (S,)),
    ("delete the sentinel called irs-watch", ("sentinel_delete",), (SE, S)),
    ("turn off the reflex rule for client emails", ("reflex_update",), (RE,)),
    ("unarchive my last chat", (), ()),
    # arm2.py
    ("whenever a new email comes in from a client, start an intake session", ("reflex_create",), (RE, CH)),
    ("make a rule: when a webhook fires, run the triage workflow", ("reflex_create",), (RE, WH, WF)),
    ("every Monday at 9 send me a summary of last week's sessions", ("schedule_create",), (SC,)),
    ("remind me every morning at 7 to check the bank feeds", ("schedule_create",), (SC,)),
    ("pause the nightly brief", ("schedule_update",), (SC,)),
    ("stop the morning digest from running", (), (SC,)),
    ("rename my nightly-brief workflow to evening-brief", ("workflow_update",), (WF, SC)),
    ("delete the triage workflow", ("workflow_delete",), (WF,)),
    ("run the triage workflow now", ("workflow_run",), (WF,)),
    ("set up Telegram so I get alerts on my phone", ("channel_connect",), (CH, S)),
    ("stop sending me notifications on Slack", ("channel_toggle",), (CH, S, AP)),
    ("add an email destination for alerts to me@x.com", ("channel_connect",), (CH, S)),
    ("use the account groups so work and personal never mix", SET, (S,)),
    ("set the custom endpoint URL to http://192.168.1.20:1234/v1", SET, (S,)),
    ("change my memory profile to Notion", SET, (S, AP)),
    ("set my brain profile to the work wiki", SET, (S,)),
    ("change my answer length to short", SET, (S,)),
    ("my name is Victor, add that to my profile", (), (S,)),
    ("pin the month-end skill", (), ()),
    ("archive the old tax skill", (), ()),
    ("make a tool that runs pdftotext on a file", (), ()),
    ("create a custom tool to ping my server", (), ()),
    ("delete the ping tool", (), (S,)),
    ("add the hermes remote agent", (), ()),
    (r"set the backup copy folder to E:\Backups", SET, (S,)),
    ("make this chat part of the Smith project", (), ()),
    ("switch to the Smith project", (), ()),
    ("archive this chat", (), ()),
    ("rename this chat to Smith 2025", (), ()),
    ("stop watching irs.gov", ("sentinel_update",), (SE,)),
    ("pause the bookkeeper", (), ()),
    ("let the bookkeeper use Sonnet instead", (), (S,)),
    ("give the reviewer agent access to the web", (), ()),
    ("set the default model to claude-opus-5-5", SET, (S,)),
    ("use my local model by default", SET, (S,)),
    ("change the theme to Daylight", SET, (S,)),
    ("turn off the emergency stop", SET, (S,)),
    ("add a lesson: always cite the IRS publication", (), ()),
    ("forget what you learned about formatting", (), ()),
    # arm3.py
    ("build me a web app for tracking invoices", (), ()),
    ("queue a night task to refactor the parser", (), ()),
    ("every weekday at 8am send me a digest", ("schedule_create",), (SC,)),
    ("each morning at 7 check the bank feeds", ("schedule_create",), (SC,)),
    ("every 2 hours check my inbox", ("schedule_create",), (SC, AP)),
    ("turn on the GitHub app", ("app_connect",), (AP, S)),
    ("disconnect Notion", ("app_connect",), (AP, S)),
    ("remove the Sentry connector", ("app_connect",), (AP, S)),
    ("stop the bookkeeping goal", ("goal_update",), (GO,)),
    ("delete the triage workflow and its schedule", ("workflow_delete", "schedule_delete"), (WF, SC)),
    # The plan's own must-arm phrasings (B1.3 / B1.4).
    # "my inbox" names the mail app, so its Connect door may come too.
    ("set a goal to keep my inbox at zero", ("goal_add",), (GO, AP)),
    ("add a sentinel that watches irs.gov", ("sentinel_add",), (SE, S)),
    (r"create a watcher for D:\Downloads", ("sentinel_add",), (SE, S)),
    ("add a webhook for when a session finishes", ("webhook_add",), (WH,)),
    ("every time a client email arrives, start an intake session", ("reflex_create",), (RE, CH)),
    ("whenever a new email comes in, start an intake session", ("reflex_create",), (RE, CH)),
]

# ---------------------------------------------------------------------------
# The measured setup scenarios (ij-review-scenarios/measure.py, variants.py).
# ---------------------------------------------------------------------------
SETUP_ROWS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    # v1.442.0 (Wave 7 group A, moved on purpose; tests/test_wave7_gaps_v14420.py
    # pins each): "send it to my phone" offers the destination tools; Slack
    # as where Jarvis messages the user is a destination, never the Slack app;
    # Iron Jarvis's own backup is its "Backup copy folder" setting, never a
    # schedule or the OneDrive app; a web service cannot call in, so no webhook
    # tool is offered as that way in.
    ("Connect my Gmail and summarize my latest emails every morning at 8 and send it to my phone.",
     ("schedule_create", "app_connect", "channel_connect"), (SC, AP, S, CH)),
    ("Set up Slack so you can message me when a long job finishes.", ("channel_connect",), (CH, S)),
    ("Add my local Ollama model and make it my default.", SET + ("model_server_add", "model_server_default"), (S,)),
    ("Add a second Claude account and use it when the first hits its limit.", (), (S,)),
    ("Keep this chat on my PC only.", (), (S,)),
    (r"Make a project for the Johnson audit in D:\Clients\Johnson and remember that I want workpapers in Excel.",
     (), ()),
    ("Create an agent named reviewer that only reads files and uses GPT.", (), ()),
    ("Schedule a weekly backup of my settings to my OneDrive folder.", SET, (S,)),
    ('Turn on the night shift and queue "update the README" for tonight.', READ, (S,)),
    ("Connect GitHub and open a pull request for my branch.", ("app_connect",), (AP, S)),
    ("Pair my Edge browser so you can fill forms.", (), (S,)),
    ("Change the theme to something green and calm.", SET, (S,)),
    ("Let me use Jarvis from my laptop on the same network.", SET, (S,)),
    ("Add the QuickBooks MCP server I use in Claude Desktop.", (), (AP, S)),
    ("Stop asking me before you run tests.", SET, (S,)),
    ("Make the app start with Windows and keep my PC awake while you work.", SET, (S,)),
    ("Watch my Downloads folder and file new PDFs into the right client folder.", ("sentinel_add",) + SET, (SE, S)),
    ("Create a workflow that drafts a reply to every new email from a client.",
     ("reflex_create", "channel_connect"), (WF, RE, CH)),
    ("Set a monthly spending limit for my builder agent.", (), (S,)),
    ("Remember that I prefer short answers.", ("remember_preference",), (S,)),
    ("Use Notion as your long-term memory.", (), (S, AP)),
    ("Read my screen and click through this installer.", (), ()),
    ("Turn on voice so I can talk to you.", READ, (S,)),
    ("Make Build \u203a Code use Codex on my work account in Full access for this repo.", ("build_defaults_set",), (S,)),
    ("Share my profile with Claude Code in Build.", SET, (S,)),
    ("Set up a webhook so Zapier can start a task.", (), ()),
    ("Back up everything to my external drive automatically.", SET, (S,)),
    ("Show me what you changed today and undo the last file edit.", (), (UN,)),
    ("Make my coding sessions use a team: Codex writes, Claude reviews.", (), (S,)),
    ("Switch to the stable update channel.", SET, (S,)),
    ("Add my OpenAI API key.", ("config_secret",), (S,)),
    ("From now on keep answers short.", ("remember_preference",), (S,)),
    ("When a client email arrives, start an intake session.", ("reflex_create",), (RE, CH)),
    ("Turn on the emergency stop.", SET, (S,)),
    ("Make Gemini my default model.", SET, (S,)),
    # variants.py
    ("Make Ollama my default model.", SET, (S,)),
    ("Use my local Ollama model as the default.", SET, (S,)),
    ("Turn on Iron-Proxy so I can use several accounts.", SET, (S,)),
    ("Turn on keep awake.", SET, (S,)),
    ("Keep my PC awake while you work.", SET, (S,)),
    ("Start Iron Jarvis when Windows starts.", READ, (S,)),
    ("Let other computers on my network reach this Iron Jarvis.", SET, (S,)),
    ("Turn on LAN access.", SET, (S,)),
    ("Turn on profile sharing with Claude Code.", SET, (S,)),
    (r"Copy my backups to E:\Backups.", SET, (S,)),
    (r"Set the backup copy folder to E:\Backups.", SET, (S,)),
    ("Approve tests without asking.", SET, (S,)),
    ("Switch approvals to Accept edits.", SET, (S,)),
    ("Read replies aloud.", SET, (S,)),
    ("Use the stable update channel.", SET, (S,)),
    ("Set up a webhook and a rule so Zapier can start a task.", ("reflex_create",), (RE,)),
    ("When a webhook from zapier happens, start a session.", ("reflex_create",), (RE, WH)),
    ("Create a watcher on my Downloads folder.", ("sentinel_add",), (SE, S)),
    (r"Create a sentinel for D:\Downloads that files PDFs.", ("sentinel_add",), (SE, S)),
    ("Undo that.", ("config_undo",), (UN,)),
    ("Undo my last change.", ("config_undo",), (UN,)),
    ("Create a schedule that runs every morning at 8.", ("schedule_create",), (SC,)),
    ("Set a goal to finish the Johnson audit.", ("goal_add",), (GO,)),
    ("Connect Telegram so you can text me.", ("channel_connect",), (CH, S)),
    ("Add a Discord channel for alerts.", ("channel_connect",), (CH, S)),
    ("Make a new project for the Johnson audit.", (), ()),
    ("Use the pay-per-use limit of $20 for my team.", SET, (S,)),
    ("Turn on night shift.", READ, (S,)),
    ("How do I keep this chat on my PC only?", (), ()),
    ("How do I add a second Claude account?", (), ()),
]

# ---------------------------------------------------------------------------
# Ordinary sentences: a cue verb, an everyday word a setting is also called,
# a time, a channel — and NO settings or record writer.
# ---------------------------------------------------------------------------
NEGATIVE_ROWS: list[str] = [
    "change the writing style of this paragraph to be more formal",
    "make the tone of this email friendlier",
    "use my profile picture on the title slide",
    "set the font to Arial in this document",
    "make the theme of my essay clearer",
    "what's on TV channel 5 tonight",
    "send a status update to the team",
    "the model of my car is a 2019 Civic",
    "switch the columns in this spreadsheet",
    "turn this list into a table",
    "keep it under 200 words",
    "start a new paragraph after the second sentence",
    "stop the timer",
    "update the spreadsheet with the new numbers",
    "add a column for the totals",
    "remove the duplicates from this list",
    "make a chart of revenue by month",
    "use the attached template for the letter",
    "enable macros in this workbook",
    "turn on track changes in the document",
    "translate this email to French",
    "summarize the K-1 I attached",
    "draft a reply to the client about the deadline",
    "update the financial model with Q3 numbers",
    "use a model like ARIMA for the forecast",
    "use my browser to look up the IRS form",
    "change the settings in Word so the margins are wider",
    "turn the power off on the printer",
    "copy the backup to the USB folder",
    "use dark colors in the chart",
    "let me know when the report is ready",
    "make the answers shorter in this FAQ document",
    "keep the meeting notes short",
    "set up a meeting with the client",
    "schedule a call with the client next Tuesday",
    "I got too many notifications from my bank",
    "share my screen with the team",
    "move the cursor to the end of the line",
    "add slack time to the project plan",
    "cut me some slack on the deadline",
    "change the default font in Word",
    "lower the reading level of this document",
    "read this article and tell me what it says",
    "make it shorter",
    "use the Daylight Savings rules for the dates",
    "start the presentation from slide 3",
    "explain how theme parks make money",
    "write a function that sorts a list",
    "give me three ideas for a blog post",
    "put the totals in column D",
    "I go to the gym every morning",
    "my standup is every Monday at 9",
    "when the package arrives, put it in the garage",
    "what are the rules of the game?",
    "is the update channel for Windows broken?",
    "set the monthly budget in the spreadsheet to 5000",
]

# ---------------------------------------------------------------------------
# The independent review's sentences (written fresh, measured on master, on
# the first cut and on the fix): each one a miss that showed a defect, fixed
# before it was added. Requests first, then everyday sentences.
# ---------------------------------------------------------------------------
REVIEW_ROWS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("please make the app dark", SET, (S,)),
    ("make Jarvis light again", SET, (S,)),
    ("stop reading your replies out loud", SET, (S,)),
    # A two-word option still counts as a switch's target, beside its row's
    # word, or with "by default".
    ("switch to Arc Cyan", SET, (S,)),
    ("go back to Liquid Glass", SET, (S,)),
    ("set comm trust to full trust", SET, (S,)),
    ("let runs from my phone have full trust", SET, (S,)),
    ("use OpenAI Images for pictures by default", SET, (S,)),
    ("don't ask me for approval before edits", SET, (S,)),
    ("stop asking me for permission every time", SET, (S,)),
    ("stop asking for approval before you run commands", SET, (S,)),
    # A calendar word is fine when the person asks to be reminded, or when it
    # is what the schedule READS.
    ("remind me every Tuesday to prepare for the team meeting", ("schedule_create",), (SC,)),
    ("every weekday at 9 check my calendar and tell me about meetings", ("schedule_create",), (SC, AP, S)),
    ("I need a summary of my inbox every morning", ("schedule_create",), (SC, AP)),
    ("I need you to check the bank feeds every morning", ("schedule_create",), (SC,)),
    ("create a watcher for my Desktop folder", ("sentinel_add",), (SE, S)),
    ("make a goal: ship v2 by June", ("goal_add",), (GO,)),
]
REVIEW_NEGATIVES: list[str] = [
    # Two-word options alone (config_set can lower the approval posture).
    "don't ask me what I want for dinner, just pick something",
    "don't ask how I know",
    "I'll ask my boss first and get back to you",
    "I'll ask Sarah first and then reply",
    "please stop and tell me what you think",
    "use Claude Code to refactor my repo",
    "use claude code style comments in the README",
    "never run this script on a Friday",
    "never run with scissors",
    "make the logo gold and red",
    "read and act on the reviewer's comments",
    "stop asking clarifying questions and just write it",
    # A calendar EVENT on a recurring time; a person's own routine.
    "set up a meeting every Tuesday with the team",
    "book a dentist appointment every six months",
    "set my alarm every weekday at 6",
    "set up a call every Monday with the client",
    "plan a team lunch every Friday",
    "I need to pay rent every month",
    "I need to file taxes every quarter",
    "every morning I check the bank feeds",
    "every Sunday we go to church",
    # A record noun in the code a coding chat writes.
    "create a webhook handler in my Express app",
    "write a webhook route in Flask",
    "set up a watcher in my React code that re-renders on change",
    "add a watcher function to my Vue component",
    "add a goal section to my business plan",
    "make a goal-setting worksheet for my team",
    "the goal-setting meeting went well, make a summary",
    "add a goal line to the chart",
]

# ---------------------------------------------------------------------------
# v1.448.0 (Wave A "chat configures everything", A1): MODEL AND PROVIDER WORDS
# reach a tool, and the Wave-4 record tools take everyday nouns. The 38-sentence
# re-audit probe (docs/PRODUCT-READINESS-LANES-v1447.md, lane F-K) found each
# request below arming nothing, or only the settings reader.
# ---------------------------------------------------------------------------
MS = ("model_server_add", "model_server_default")
WAVE_A_ROWS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    # A cloud vendor or plan asked to answer: the default-provider / default-
    # model rows (settings.setup_words.USE_VENDOR / USE_PLAN / SET_UP_VENDOR).
    ("Use Claude for this chat instead of the default.", SET, (S,)),
    ("I have a ChatGPT Plus plan, can you use that to answer?", SET, (S,)),
    ("Set up Gemini so I can try Google's model.", SET, (S,)),
    ("Use OpenRouter for the big questions.", SET, (S,)),
    ("switch to Gemini from now on", SET, (S,)),
    ("answer with GPT-5 for all my chats", SET, (S,)),
    ("try Google's model for my questions", SET, (S,)),
    ("enable Grok for this chat", SET, (S,)),
    ("we pay for Claude Max, use it to answer", SET, (S,)),
    ("Use Claude Sonnet 4.5 for this conversation.", SET, (S,)),
    # A local program said to be RUNNING here (records_connections): the
    # model server tools; "connect to it" also reads as an app's connect.
    ("I'm running Ollama with llama3 on this PC, use it.", MS, (S,)),
    ("LM Studio is running on port 1234, connect to it.", MS, (S, AP)),
    ("I installed vLLM on this machine, point Jarvis at it", MS, (S,)),
    # The Wave-4 tools by their everyday nouns.
    (r"Point your long-term memory at my Obsidian vault in D:\Notes.", ("memory_base_add",), ()),
    ("keep your memory in Notion", ("memory_base_add",), (AP,)),
    ("For the ClosePilot folder, always use Codex in Auto mode.", ("build_defaults_set",), ()),
    # "by default" + the Claude Code option also reads as the default-model
    # setting (the trigger's older two-word-option rule) — a real neighbour.
    ("in the Johnson project, by default use Claude Code", ("build_defaults_set",), (S,)),
    ("Have my coding sessions run with a reviewer that checks every change.", ("build_team_set",), ()),
    ("add a code reviewer to my coding sessions", ("build_team_set",), ()),
    ("have Claude review every change in my coding sessions", ("build_team_set",), ()),
    # A night TIME before coding work: the night task beside the schedule.
    (r"Every night at 11 run the tests in C:\Projects\ClosePilot and fix what breaks.",
     ("night_task_add", "schedule_create"), (SC,)),
    ("every night at 10pm fix the failing tests in this repo", ("night_task_add", "schedule_create"), (SC,)),
]
WAVE_A_NEGATIVES: list[str] = [
    # The vendor's name in another sense, or with no request.
    "Claude Monet painted water lilies, tell me about them.",
    "my ChatGPT history is long",
    "Gemini is my star sign",
    "the Ollama logo is a llama",
    "a port of the game to Windows",
    "the Google model of management",
    "use the Google model of management for the memo",
    "use Claude Code to refactor my repo",
    "use Claude for this",
    "try Gemini on this spreadsheet",
    "Anthropic published a new paper, summarize it",
    "OpenAI's stock is not public",
    # The Wave-4 nouns in their everyday senses.
    "my memory of the vault is hazy",
    "put the memory card in the folder",
    "the night shift at the hospital starts at 7",
    "every night I run the tests before bed",
    # ("Every night at 11 run the dishwasher" is a recurring time, so the
    # older schedule shape still offers a schedule for it — it is a night-TASK
    # near-miss only, pinned in tests/_records_build_table_v14380.py.)
    "the reviewer at the Times panned my book",
    "have the reviewer check the sessions at the conference",
    "for the Johnson folder, use the 2024 template",
    # A CPA's schedule is nobody's automation.
    "Remind me how depreciation schedules work for a vehicle.",
    "my payment schedule shows twelve installments",
    "the client's depreciation schedule is in the workpapers",
    "what does the amortization schedule show for year 3",
    # A program said to be running, with no use asked.
    "Ollama is running slow on my PC",
    "I'm running Ollama on my laptop for fun",
]


def _all_rows():
    return (
        SETTINGS_ROWS + RECORD_ROWS + SETUP_ROWS + REVIEW_ROWS + WAVE_A_ROWS
        + [(t, (), ()) for t in NEGATIVE_ROWS + REVIEW_NEGATIVES + WAVE_A_NEGATIVES]
    )


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    home = tmp_path_factory.mktemp("arming") / "home"
    app = create_app(str(home))
    return SimpleNamespace(platform=app.state.platform)


def lanes(d, text: str, *, auto_tools: bool = True, names=()) -> tuple[list[str], list[str]]:
    """``(post_armed, stream_ask)`` — the POST lane arms exactly
    ``_resolve_armed_tools``; the stream lane arms the same ``armed`` plus its
    ask tier (``select_ask_tools`` then ``settings_ask_tools``, in the lane's
    own order)."""
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=auto_tools)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    ask: list[str] = []
    if auto_tools:
        ask = [t for t in select_ask_tools(text) if t not in armed]
        ask += CT.settings_ask_tools(d, text, armed, ask, workflow_names=names)
    return armed, ask


def check(d, text, offer, allowed, *, names=()) -> str:
    """"" when the row holds, else why not."""
    armed, ask = lanes(d, text, names=names)
    offered = set(armed) | set(ask)
    why = []
    missing = [t for t in offer if t not in offered]
    if missing:
        why.append(f"missing {missing}")
    if "config_set" in offer and "config_set" not in armed:
        why.append("config_set not in the POST lane's armed set")
    ok_writers = frozenset().union(*(FAMILIES[f] for f in allowed)) if allowed else frozenset()
    stray = sorted((offered & WRITERS) - ok_writers)
    if stray:
        why.append(f"unrelated writers {stray}")
    if text in ("rename the Intercom project", "rename this chat to Smith 2025"):
        if {"code_search", "code_load"} & offered:
            why.append("Code Lab armed off 'rename'")
    return "; ".join(why)


def _failures(d, rows):
    return [(t, why) for t, offer, allowed in rows if (why := check(d, t, offer, allowed))]


def test_the_setting_sentences_arm_the_right_tool(d):
    """B1.1 + B1.2 — at least 90% (the plan's bar); today every row holds, so
    every row is pinned."""
    bad = _failures(d, SETTINGS_ROWS)
    rate = 1 - len(bad) / len(SETTINGS_ROWS)
    assert len(SETTINGS_ROWS) == 57
    assert rate >= 0.9, bad
    assert bad == []


def test_the_record_and_setup_sentences_arm_the_right_tool(d):
    assert _failures(d, RECORD_ROWS) == []
    assert _failures(d, SETUP_ROWS) == []


def test_the_reviews_requests_arm_the_right_tool(d):
    assert _failures(d, REVIEW_ROWS) == []


def test_wave_a_model_words_and_everyday_nouns_arm_the_right_tool(d):
    """v1.448.0 (Wave A, A1). MUTATION (each red): drop USE_VENDOR / USE_PLAN
    / SET_UP_VENDOR from trigger._EXTRA_REFERENCES -> the vendor rows lose
    SET; drop records_connections' running-brand branch -> the Ollama / LM
    Studio rows lose model_server_add; drop the new _MEMORY_RULES row, the
    named-folder _DEFAULTS_SET alternative, the reviewer _TEAM_SET
    alternatives or _NIGHT_TIME -> their rows lose the tool."""
    assert _failures(d, WAVE_A_ROWS) == []
    assert len(WAVE_A_ROWS) >= 20 and len(WAVE_A_NEGATIVES) >= 20
    # …and the near-misses arm none of the Wave-4 record writers either (they
    # are outside FAMILIES, so the general negative test cannot see them).
    wave4 = {
        "model_server_add", "model_server_default", "model_server_remove", "account_add", "memory_base_add",
        "night_task_add", "night_task_cancel", "night_task_move",
        "build_defaults_set", "build_defaults_clear", "build_team_set", "build_team_clear",
    }
    bad = []
    for text in WAVE_A_NEGATIVES:
        armed, ask = lanes(d, text)
        stray = sorted((set(armed) | set(ask)) & (WRITERS | wave4))
        if stray:
            bad.append((text, stray))
    assert bad == []


def test_a_click_in_a_desktop_program_ask_arms_no_browser_tool(d):
    """v1.448.0 (Wave A, A1): the desktop reading wins — the stream lane's ask
    tier offers no browser click for a desktop program (the model would try
    the page tools on a program that is no page); a page click keeps it."""
    for text in (
        "Click the Submit button in the QuickBooks desktop app for me.",
        "type the totals in the Excel app and press Save",
        "double-click the icon on my desktop",
    ):
        armed, ask = lanes(d, text)
        assert not [t for t in armed + ask if t.startswith("browser_")], (text, armed, ask)
        assert not [t for t in select_ask_tools(text) if t.startswith("browser_")], text
    assert "browser_click" in select_ask_tools("click the Buy button on the checkout page")
    assert "browser_click" in select_ask_tools('click "Submit" on the form in Chrome')


def test_no_ordinary_sentence_arms_a_settings_or_record_writer(d):
    bad = []
    for text in NEGATIVE_ROWS + REVIEW_NEGATIVES + WAVE_A_NEGATIVES:
        armed, ask = lanes(d, text)
        stray = sorted((set(armed) | set(ask)) & WRITERS)
        if stray:
            bad.append((text, stray))
    assert bad == []


def test_no_sentence_in_the_table_arms_an_unrelated_writer(d):
    """The table both ways: every row's writers are inside its own families."""
    bad = []
    for text, _offer, allowed in _all_rows():
        armed, ask = lanes(d, text)
        ok = frozenset().union(*(FAMILIES[f] for f in allowed)) if allowed else frozenset()
        stray = sorted(((set(armed) | set(ask)) & WRITERS) - ok)
        if stray:
            bad.append((text, stray))
    assert bad == []
    assert len(_all_rows()) >= 200


# ------------------------------------------------------------------ guards


def test_a_question_arms_only_the_reader(d):
    for text in ("what is my default model?", "which theme am I using?", "is keep awake on?"):
        armed, ask = lanes(d, text)
        assert "config_list" in armed, text
        assert not (set(armed) | set(ask)) & FAMILIES["settings"], text
        assert settings_ask(text).writers is False


def test_a_mention_without_a_change_arms_only_the_reader(d):
    """D2's change cue: naming a setting is not asking to change it — the
    reader comes (the model may want to look), no writer."""
    for text in (
        "the update channel for Windows is broken",
        "dark mode looks great on this laptop",
        "I like the Liquid Glass theme",
        "Iron-Proxy crashed again yesterday",
    ):
        ask = settings_ask(text)
        assert ask.reader is True and ask.writers is False, (text, ask)
        armed, stream_ask = lanes(d, text)
        assert "config_list" in armed, text
        assert not (set(armed) | set(stream_ask)) & FAMILIES["settings"], text


def test_auto_tools_off_arms_the_reader_only(d):
    """B1.2: with Auto tools OFF a settings request still arms config_list
    (the model reads the setting and says how to change it); no writer."""
    for text in ("turn on read aloud", "keep my PC awake while you work", "turn Auto tools back on"):
        armed, ask = lanes(d, text, auto_tools=False)
        assert armed == ["config_list"], (text, armed)
        assert ask == []
    armed, _ = lanes(d, "summarize the K-1 I attached", auto_tools=False)
    assert armed == []


def test_the_settings_tools_have_their_own_place_outside_the_cap(d):
    """B1.2: a busy sentence fills the six slots with readers; the settings
    tools still come, and the drop signal still measures only the cap."""
    text = r"set the backup folder to D:\Backups and look up what is in it"
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    sel = CT._resolve_armed_tools(d, body)
    armed = list(sel[0])
    assert {"config_list", "config_set", "config_secret"} <= set(armed)
    others = [t for t in armed if not t.startswith("config_")]
    assert len(others) == CT._MAX_ARMED_TOOLS
    capped = CT._resolve_armed_tools(d, body, max_tools=2)
    assert {"config_list", "config_set"} <= set(capped[0])
    assert capped.ceiling == 2 and capped.dropped == CT._MAX_ARMED_TOOLS - 2


def test_a_run_verb_on_a_saved_workflows_own_name_arms_workflow_run(d):
    """B1.4: "run triage now" names no workflow word — the saved name does."""
    assert "workflow_run" not in lanes(d, "run triage now")[1]
    assert "workflow_run" in lanes(d, "run triage now", names=["triage"])[1]
    assert "workflow_run" in lanes(d, "please kick off the Month-End Close", names=["Month-End Close"])[1]
    # A question, or a name inside a rule, never runs it.
    assert "workflow_run" not in lanes(d, "did triage run?", names=["triage"])[1]
    assert R.wants_workflow_names("run triage now") is True
    assert R.wants_workflow_names("summarize the K-1") is False


def test_saved_workflow_names_reads_the_store(d):
    from iron_jarvis.workflows.store import WorkflowStore

    WorkflowStore(d.platform.engine).save(
        "triage", "name: triage\nsteps:\n  - id: a\n    kind: agent\n    task: hi\n"
    )
    assert "triage" in R.saved_workflow_names(d.platform)
    assert R.saved_workflow_names(SimpleNamespace(engine=None)) == []


# ------------------------------------------------------------------ seams


async def test_the_stream_lane_cards_a_create_tool_and_the_post_lane_never_offers_it(tmp_path):
    """B1.3: schedule_create is allow tier, yet asked for in chat it CARDS
    first (the "ask" override); the POST lane does not arm it at all."""
    from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path / "home"))
    args = {"name": "weekday-digest", "cron": "0 8 * * 1-5", "kind": "event", "payload": {"type": "x"}}
    fake = _calls_stream([("schedule_create", args)])
    app.state.platform.router.stream = fake
    seen = []

    async def deny(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    await _drive_stream(app, _body("make a schedule that runs every weekday at 8am"), deny)
    assert "schedule_create" in fake.tools[0]
    assert seen == ["schedule_create"], "a create tool asked for in chat asks first"
    assert app.state.platform.scheduler.get("weekday-digest") is None
    # Don't ask: no card, as for every ask-tier tool.
    fake2 = _calls_stream([("schedule_create", args)])
    app.state.platform.router.stream = fake2

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("make a schedule that runs every weekday at 8am", "yolo"), never)
    assert app.state.platform.scheduler.get("weekday-digest") is not None
    # The POST lane arms none of the create tools.
    body = ChatBody(messages=[{"role": "user", "content": "make a schedule that runs every weekday at 8am"}],
                    auto_tools=True)
    armed = CT._resolve_armed_tools(SimpleNamespace(platform=app.state.platform), body)[0]
    assert not set(armed) & R.ASK_FIRST_CREATE_TOOLS


def test_ask_first_overrides_narrow_only_and_fail_closed():
    class Perms:
        def mode_for(self, key, overrides):
            from iron_jarvis.core.models import PermissionMode

            return PermissionMode(overrides.get(key, "allow"))

    class Reg:
        def get(self, name):
            return SimpleNamespace(perm_key=lambda: name)

    d = SimpleNamespace(platform=SimpleNamespace(registry=Reg(), permissions=Perms()))
    ov = {"goal_add": "deny"}
    failed = CT.ask_first_overrides(d, ov, ["schedule_create", "goal_add", "shell", "webhook_add"], ["webhook_add"])
    assert failed == set()
    assert ov == {"goal_add": "deny", "schedule_create": "ask"}, "never over a deny, never an armed pick"

    class Broken(Perms):
        def mode_for(self, key, overrides):
            raise RuntimeError("boom")

    d2 = SimpleNamespace(platform=SimpleNamespace(registry=Reg(), permissions=Broken()))
    assert CT.ask_first_overrides(d2, {}, ["schedule_create", "shell"], []) == {"schedule_create"}


async def test_a_coding_session_turn_arms_the_settings_tools(tmp_path):
    """Chat › Code (the build-turn route, harness on, Auto tools off): a
    request about a setting gets the settings tools on their own tiers; an
    ordinary coding request does not."""
    from tests.test_build_sessions_v1367 import Script, new_session, wire
    from tests.test_build_turns_v1367 import _run

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work)["id"]
    script = wire(app, Script(["ok."]))
    await _run(app, sid, "turn on read aloud", "bt-settings")
    tools = set(script.calls[0]["tools"])
    assert {"config_list", "config_set", "config_change", "config_change_protected"} <= tools
    script2 = wire(app, Script(["ok."]))
    await _run(app, sid, "add a docstring to app.py", "bt-plain")
    assert not {"config_list", "config_set", "config_change"} & set(script2.calls[0]["tools"])


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_both_lanes_harness_turns_take_the_settings_tools_on_their_own_tiers(tmp_path, lane, monkeypatch):
    """Lock-step: ``_build_harness`` is shared; the POST lane drops the ask
    tier (it can card nobody)."""
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from iron_jarvis.platform import build_platform
    from tests.test_build_sessions_v1367 import CLAUDE, Script

    platform = build_platform(str(tmp_path / "home"))
    script = Script(["ok."])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    work = tmp_path / "work"
    work.mkdir()
    body = ChatBody(
        messages=[{"role": "user", "content": "keep my PC awake while you work"}],
        provider=CLAUDE[0], model=CLAUDE[1], workspace_dir=str(work),
    )
    kw = {"harness": True, "session_id": "build:lane"}
    if lane == "stream":
        async for _ in await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw):
            pass
    else:
        await run_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    tools = set(script.calls[0]["tools"])
    assert {"config_list", "config_set", "config_secret"} <= tools
    if lane == "stream":
        assert {"config_change", "config_change_protected"} <= tools
    else:
        assert not {"config_change", "config_change_protected"} & tools


def test_harness_asked_tools_plan_mode_and_questions():
    def body(t):
        return ChatBody(messages=[{"role": "user", "content": t}])

    assert CT.harness_asked_tools(body("turn on read aloud")) == [
        "config_list", "config_set", "config_secret", "config_change", "config_change_protected",
    ]
    assert CT.harness_asked_tools(body("turn on read aloud"), plan_mode=True) == ["config_list"]
    assert CT.harness_asked_tools(body("is read aloud on?")) == ["config_list"]
    assert CT.harness_asked_tools(body("fix the failing test")) == []


def test_the_automation_agent_can_make_a_watcher():
    """B1.3: a watcher was the one automation no agent could make."""
    from iron_jarvis.agents.types import AgentType, get_agent_definition

    assert "sentinel_add" in get_agent_definition(AgentType.AUTOMATION).tools
