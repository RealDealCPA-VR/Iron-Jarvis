"""v1.361.0 lane 11B — THE MATCHER, BOTH WAYS: one combined probe table.

The final regression hunt over v1.359.0..v1.360.1 found that v1.360.0's
"the Connect door only for the user's own data" and "the Guide only for a
question about Iron Jarvis" (lane 10B) fixed one direction by breaking the
other:

(1) the door was gone for everyday own-data requests that say "on <app>",
    "via <app>", "<app>:" or use a chat verb ("did anyone message me on
    slack?", "DM Bob on slack", "gmail: anything from the IRS?");
(2) "my <app> <thing>" lost it when the thing was not on a closed list
    ("list my stripe disputes", "did my github workflow pass?");
(3) the second app in "X and Y" lost it ("search dropbox and google drive
    for the lease" offered Dropbox only);
(4) asking to use Pixio (an API-key app) no longer offered "Connect Pixio";
(5) the Guide was no longer armed for how-tos about Jarvis features missing
    from the new word lists, or with "Jarvis" in the previous sentence;
(6) "what's on my calendar in outlook" (lower case) lost the Microsoft 365
    door and the model was told never to guess the app.

THE RULE THIS FILE EXISTS FOR: every phrasing the earlier waves pinned —
``tests/test_chat_matches_meant_v13600.py`` (OWN / NOT_OWN, the Guide
ABOUT / NOT_ABOUT tables), ``tests/test_rereview_wave10_v13600.py``,
``tests/test_connect_door_v13590.py``, ``tests/test_chat_guide_media_doors_
v13530.py`` — the final re-audit's probes and this hunt's own (its sentence
files ``hunt/sk/s_own.txt``, ``hunt/s1-3.txt``, ``hunt/g1/g3/g4.txt``,
``hunt/sk/g.txt``) sits in ONE table with its verdict written out, and the
whole table passes at once. A change that fixes one direction by breaking
the other turns rows red here.

Verdicts kept on purpose where v1.359.0 read otherwise (each says why):

* "check my inbox" / "check my email" — own data, and the door is ONE
  choice naming both mail apps (v1.360.0, an Outlook user was told Gmail);
* "what's on my calendar tomorrow?" — own data, but NO door: the only
  calendar card is Microsoft 365 and the words did not say Outlook
  (v1.360.0, on purpose; "… in outlook" now names it);
* "what's new in slack?" / "what's new on github?" — the product's news
  (v13600 pins "what's new in Notion this month" as research);
* "how much revenue did stripe show last month?" — the app as the SUBJECT of
  a question is also how news is asked ("what did Slack say about the
  outage?"), so it stays door-free;
* a bare how-to with no Jarvis signal anywhere ("how do I set it up", "how
  do I add a folder", "how do I add memory") — the everyday reading wins
  (v1.360.0 lane 10B, G38).

The matcher, the lanes and select_auto_tools are the real code; the model
is the only double in the lane tests.
"""

from __future__ import annotations

import pytest

from iron_jarvis.connectors.app_names import apps_named, connect_offers, own_data_apps
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.tools.autoselect import select_auto_tools

import tests.test_chat_guide_media_doors_v13530 as guide_v13530
import tests.test_chat_matches_meant_v13600 as meant_v13600
import tests.test_connect_door_v13590 as door_v13590
import tests.test_rereview_wave10_v13600 as rereview_v13600
from tests.test_chat_matches_meant_v13600 import _post_turn, _section, _stream_turn

GUIDE = {"guide_search", "app_search", "app_status"}
APPS_HREF = "/settings?section=connections-apps"
M365 = "Microsoft 365 (Outlook, Calendar, OneDrive)"


# =========================================================================== #
# THE TABLES — every phrasing, its verdict written out
# =========================================================================== #
#: ``(text, own_data_apps(text))`` with nothing connected — the apps the
#: message names for the user's OWN data (the Connect door's matcher).
#: ``[]`` = no door. A row whose text names no catalog app at all is marked.
DOOR = [
    # --- v13600_NOT_OWN
    ('stripe the table rows', []),
    ('explain how postgres indexes work', []),
    ('the sentry stood guard at the gate in my story', []),
    ('Write a LinkedIn post about our GitHub launch', []),
    ('stripe every other row in the spreadsheet', []),
    ('what does a sentry do in a medieval castle', []),
    ('the night sentry walked the wall twice', []),
    ('teach me postgres joins', []),
    ('write a blog post about Notion for beginners', []),
    ('our Slack launch went really well', []),
    ('our Notion launch party is on Friday', []),
    ('draft a tweet announcing our Stripe integration', []),
    ('is Postgres good for analytics workloads?', []),
    ('does Notion have an API?', []),
    ("design a pricing page like Stripe's", []),
    ('the gmail logo is red and white', []),
    ("what's new in Notion this month", []),
    ('describe how GitHub Actions caching works', []),
    ('give me tips on writing a Slack bot', []),
    ("summarize this article about Dropbox's layoffs", []),
    ('compare Postgres and MySQL for a small shop', []),
    ('how does Dropbox sync work', []),
    ('write a story where a sentry guards a castle', []),
    ('make the header stripe blue', []),
    ('a sentry gun sits in the corner of the level', []),
    ('who runs Sentry these days', []),
    ('tell me more about Dropbox', []),
    ('postgres is an object-relational database', []),
    ('Slack set up a new pricing tier', []),
    ('Stripe set up shop in Dublin last year', []),
    ('GitHub connected millions of developers', []),
    ('Slack got connected to Teams in 2020', []),
    ('GitHub got set up as a nonprofit', []),
    ('Stripe got linked to the scandal', []),
    ('write a poem about my Dropbox', []),
    ('draft a blog post about our Slack', []),
    ('explain what Stripe payouts are', []),
    ('can you explain what GitHub issues are?', []),
    # --- v13600_OWN
    ('show me the Slack messages from today', ['slack']),
    ('pull the latest invoices from Stripe', ['stripe']),
    ('list the repos on GitHub', ['github']),
    ('query the orders table in postgres', ['postgres']),
    ('open Notion', ['notion']),
    ('check Gmail for the receipt', ['gmail']),
    ('any new Sentry errors today?', ['sentry']),
    ("what's in my inbox", ['gmail']),
    ('refund the last charge in Stripe', ['stripe']),
    ('is github connected?', ['github']),
    ('summarize my Notion meeting notes', ['notion']),
    ('our Slack channel is noisy, summarize it', ['slack']),
    ('find the lease in Dropbox', ['dropbox']),
    ('save this to my Google Drive', ['google_drive']),
    ('post the summary to Slack', ['slack']),
    ('look through my Dropbox for the scans', ['dropbox']),
    ('which Notion pages mention Q3?', ['notion']),
    ('search Sentry for the login crash', ['sentry']),
    ('can you check my email', ['gmail']),
    ('upload the report to OneDrive', ['onedrive']),
    ('connect GitHub', ['github']),
    ('add the meeting to my calendar', ['microsoft_365']),
    ('explain my Stripe payouts from last week', ['stripe']),
    ('create a GitHub issue for the login bug', ['github']),
    ('find my tax files in Google Drive', ['google_drive']),
    ('search github for issues about login', ['github']),
    ('github connected?', ['github']),
    ("why isn't slack connected", ['slack']),
    ('has github been connected', ['github']),
    ("Notion isn't connected yet, right?", ['notion']),
    ('slack set up yet', ['slack']),
    ('is github connected to my account', ['github']),
    ('did slack get connected?', ['slack']),
    ('did github get set up', ['github']),
    ('has my notion got connected yet', ['notion']),
    ('my Slack is full of noise, help', ['slack']),
    ('can you see my Google Drive?', ['google_drive']),
    # --- rereview_MODIFIER_NOT_OWN
    ('remove the GitHub link from the footer', []),
    ('update the Slack logo in our app', []),
    ('add the Stripe button to the checkout page', []),
    ('make the Dropbox icon bigger', []),
    ('share our GitHub launch post on Twitter', []),
    ('move the GitHub link to the header', []),
    ('put the GitHub badge in the readme', []),
    ('review the Postgres migration in this PR', []),
    ('update the postgres version in docker-compose', []),
    # --- rereview_ACTS_ON_THE_APP
    ('list my github repos', ['github']),
    ("summarize today's Slack", ['slack']),
    ('check my Stripe dashboard', ['stripe']),
    ('show me my GitHub notifications', ['github']),
    ('query the postgres db', ['postgres']),
    ('star the GitHub repo', ['github']),
    ('check the github issues', ['github']),
    ('pull up Sentry', ['sentry']),
    # --- v13590_RESEARCH
    ('look up the stripe api docs for refunds', []),
    ('search for notion templates for a CRM', []),
    ('look up how to use github actions', []),
    ('search for the dropbox desktop app download', []),
    ('look up google maps api pricing', []),
    ('search for brave search api pricing', []),
    ('look up how to share a folder in my google drive', []),
    ('look up how to archive a channel in slack', []),
    ('look up reviews of notion', []),
    ('search for alternatives to slack', []),
    ("what's the latest news about github copilot", []),
    ('find a good postgres tutorial online', []),
    ('research stripe vs square fees', []),
    ('what is Notion?', []),
    ('is Dropbox better than Google Drive?', []),
    ('tell me about Stripe', []),
    ('search the web for Dropbox', []),
    ("what's the difference between Dropbox and Google Drive", []),
    ('should I use Dropbox or Google Drive?', []),
    ('which is better, Notion or Obsidian?', []),
    ('which one is cheaper, Dropbox or Box?', []),
    ('how much does Dropbox cost?', []),
    ('how much would Slack charge us for 10 seats', []),
    ('who founded Dropbox', []),
    ('who owns GitHub now?', []),
    # --- v13590_OWN
    ('search my google drive for the lease', ['google_drive']),
    ('search my gmail for the invoice from acme', ['gmail']),
    ('look up the invoice in my Dropbox', ['dropbox']),
    ('search for the lease in my Google Drive', ['google_drive']),
    ('look up the refund in stripe', ['stripe']),
    ('connect my Google Drive so you can find my tax files', ['google_drive']),
    ('is my Google Drive connected?', ['google_drive']),
    ('what is in my Dropbox?', ['dropbox']),
    ('how do I connect Dropbox', ['dropbox']),
    ('upload the latest report to Dropbox', ['dropbox']),
    ('save this web page to Notion', ['notion']),
    ('check my Outlook inbox', ['microsoft_365']),
    ('search my notion templates for the client one', ['notion']),
    ('check my gmail online for the receipt', ['gmail']),
    ('should I use my Dropbox or my Google Drive for this?', ['google_drive', 'dropbox']),
    ('who created this file in my Dropbox?', ['dropbox']),
    ('who created the folder in Dropbox?', ['dropbox']),
    ('how much space is left in Dropbox?', ['dropbox']),
    ('which folder in Dropbox has the best photos', ['dropbox']),
    ('find the differences between the two leases in my Google Drive', ['google_drive']),
    # --- reaudit
    ('show my github issues', ['github']),
    ('find the email from Dana in my gmail', ['gmail']),
    ('what is on my outlook calendar', ['microsoft_365']),
    ('upload this to my dropbox', ['dropbox']),
    ('check my stripe payments', ['stripe']),
    ('look up the stripe api docs', []),
    ('search my Google Drive for the lease', ['google_drive']),
    # --- hunt_s_own
    ('did anyone message me on slack?', ['slack']),
    ('how many open issues do we have on github?', ['github']),
    ('DM Bob on slack', ['slack']),
    ('what did Sarah say on Slack yesterday?', ['slack']),
    ('any messages for me on Slack?', ['slack']),
    ('catch me up on slack', ['slack']),
    ("tell the team on slack that I'm late", ['slack']),
    ('remind me on slack tomorrow at 9', ['slack']),
    ('how much did we make on stripe this month?', ['stripe']),
    ('what are the top errors on sentry this week?', ['sentry']),
    ('from postgres, how many users signed up yesterday?', ['postgres']),
    ('gmail: anything from the IRS?', ['gmail']),
    ('on github, list my open PRs', ['github']),
    ('email this to Ann via gmail', ['gmail']),
    ('what did Sarah say in slack today?', ['slack']),
    ('list my stripe disputes', ['stripe']),
    ('show my stripe revenue', ['stripe']),
    ('check my slack unreads', ['slack']),
    ('did my github workflow pass?', ['github']),
    ('is my postgres server up?', ['postgres']),
    ('send me my dropbox link', ['dropbox']),
    ('search dropbox and google drive for the lease', ['google_drive', 'dropbox']),
    ('check slack and gmail for anything urgent', ['slack', 'gmail']),
    ('generate an image with pixio', ['pixio']),
    ('make an image using pixio', ['pixio']),
    ('use pixio to make a video of a dog', ['pixio']),
    ("what's on my calendar in outlook", ['microsoft_365']),
    ("what's on my calendar in Outlook", ['microsoft_365']),
    ('slack announced a new pricing plan today', []),
    # --- hunt_s1
    ('show me my open pull requests on github', ['github']),
    ('what are my open github issues?', ['github']),
    ('any new issues in my repo on github?', ['github']),
    ('did anyone comment on my github PR?', ['github']),
    ('summarize the latest messages in slack', ['slack']),
    ('anything new in slack?', ['slack']),
    ('read my slack messages', ['slack']),
    ('post this to the #general channel on slack', ['slack']),
    ('send a slack message to Tom', ['slack']),
    ('find the lease in google drive', ['google_drive']),
    ('where is the Q3 report in my google drive?', ['google_drive']),
    ('put this file in google drive', ['google_drive']),
    ('save this to my notion', ['notion']),
    ('add this to notion', ['notion']),
    ('add a page to notion for the meeting notes', ['notion']),
    ("what's in my notion workspace?", ['notion']),
    ('find the onboarding doc in notion', ['notion']),
    ('make a notion page with these notes', []),  # NAMES NO APP
    ('what were the sentry errors last night?', ['sentry']),
    ('are there new errors in sentry?', ['sentry']),
    ('how many charges did we have in stripe this week?', ['stripe']),
    ('refund the last payment in stripe', ['stripe']),
    ("what's my stripe balance?", ['stripe']),
    ('list my stripe customers', ['stripe']),
    ('query my postgres database for active users', ['postgres']),
    ('how many rows are in the users table in postgres?', ['postgres']),
    ('run this query against postgres', ['postgres']),
    ('check my email', ['gmail']),
    ('check my inbox', ['gmail']),
    ("what's in my inbox today?", ['gmail']),
    ('any new emails from my accountant?', []),  # NAMES NO APP
    ('did I get an email from the bank?', []),  # NAMES NO APP
    ('search my email for the invoice from Acme', ['gmail']),
    ('read my latest emails', []),  # NAMES NO APP
    ('reply to the email from Bob in gmail', ['gmail']),
    ("what's on my calendar tomorrow?", ['microsoft_365']),
    ('what meetings do I have today?', []),  # NAMES NO APP
    ('book a meeting on my calendar for Friday at 3', ['microsoft_365']),
    ('add a dentist appointment to my calendar', ['microsoft_365']),
    ('am I free Thursday afternoon?', []),  # NAMES NO APP
    ('what is on my Outlook calendar', ['microsoft_365']),
    ('find the email from Dana in outlook', ['microsoft_365']),
    ('send an email from my outlook to Jim', ['microsoft_365']),
    ('open my onedrive', ['onedrive']),
    ('find the budget spreadsheet in onedrive', ['onedrive']),
    ('save the report to OneDrive', ['onedrive']),
    ('what files are in my dropbox?', ['dropbox']),
    ('find the contract in dropbox', ['dropbox']),
    ('share the dropbox folder with Ann', ['dropbox']),
    ('how much space is left in dropbox?', ['dropbox']),
    ('dropbox: find the 2023 receipts', ['dropbox']),
    ('github issues', ['github']),
    ('my github notifications', ['github']),
    ('my dropbox', ['dropbox']),
    ('gmail', ['gmail']),
    ('look in my box account for the w2', []),  # NAMES NO APP
    ('search box for the w2', []),  # NAMES NO APP
    ('find the pdf in Box', []),  # NAMES NO APP
    ('connect gmail', ['gmail']),
    ('set up dropbox', ['dropbox']),
    ('is my stripe linked', ['stripe']),
    ('hook up notion', ['notion']),
    ('can you get into my gmail?', ['gmail']),
    ('can you read my gmail?', ['gmail']),
    ('can you see my google drive?', ['google_drive']),
    ('do you have access to my dropbox?', ['dropbox']),
    ('use github to find the bug report', ['github']),
    ("use slack to tell Ben I'm out", ['slack']),
    ('use my gmail to send this', ['gmail']),
    ('pixio make me an image of a cat', ['pixio']),
    ('draft a reply to Dana in Gmail', ['gmail']),
    ('where did I put the lease? check dropbox and google drive', ['google_drive', 'dropbox']),
    ('I need the invoices from stripe for March', ['stripe']),
    ('get the invoices from stripe', ['stripe']),
    ('pull the invoices from Stripe', ['stripe']),
    ('grab my latest payouts from stripe', ['stripe']),
    ('I want to see my notion tasks', ['notion']),
    ('let me see my dropbox files', ['dropbox']),
    ('my notion', ['notion']),
    ('open notion', ['notion']),
    # --- hunt_s2
    ('who pinged me on slack today', ['slack']),
    ("what's happening on slack", ['slack']),
    ("what's new in slack?", []),
    ("what's new on github?", []),
    ('what did the team decide on slack about the launch?', ['slack']),
    ('look through notion and google drive for the roadmap', ['notion', 'google_drive']),
    ('is there anything urgent on slack?', ['slack']),
    ('whats going on in my github', ['github']),
    ('how many stars does my repo have on github', ['github']),
    ("what's the status of my PR on github?", ['github']),
    ('who opened the latest issue on github?', ['github']),
    ('when was the last commit to the repo on github?', ['github']),
    ('did the deploy fail? check sentry', ['sentry']),
    ('check sentry', ['sentry']),
    ('check stripe', ['stripe']),
    ('check github', ['github']),
    ('check gmail', ['gmail']),
    ('check slack', ['slack']),
    ('check notion', ['notion']),
    ('check dropbox', ['dropbox']),
    ('open dropbox', ['dropbox']),
    ('open github', ['github']),
    ('search gmail for the receipt', ['gmail']),
    ('search slack for the outage thread', ['slack']),
    ('search notion for the onboarding checklist', ['notion']),
    ('look up the customer in stripe', ['stripe']),
    ("look up Bob's address in gmail", ['gmail']),
    ("what's the latest in my gmail", ['gmail']),
    ('do I have any unread mail in gmail?', ['gmail']),
    ('any new mail in gmail?', ['gmail']),
    ('how much revenue did stripe show last month?', []),
    ('how much money came in through stripe this week', ['stripe']),
    ('total sales on stripe for September?', ['stripe']),
    ('I want my stripe payouts for last month', ['stripe']),
    ('what broke last night according to sentry?', ['sentry']),
    ('show the Q3 numbers from postgres', ['postgres']),
    ('in notion, find the hiring plan', ['notion']),
    ('in my dropbox, find the receipts', ['dropbox']),
    ('on slack, post the release notes to #eng', ['slack']),
    ('gmail - find the flight confirmation', ['gmail']),
    ("github - what's new on my repos?", ['github']),
    ('can you check my gmail', ['gmail']),
    ('can you check gmail for me', ['gmail']),
    ('could you look at my dropbox', ['dropbox']),
    ('please check github for new issues', ['github']),
    ('jarvis, check slack', ['slack']),
    ("hey jarvis what's in my dropbox", ['dropbox']),
    ("tell me what's in my notion", ['notion']),
    ('tell me about my github issues', ['github']),
    ('give me a summary of my slack messages', ['slack']),
    ('give me my github issues', ['github']),
    ('I need my stripe numbers', ['stripe']),
    ('I need to find a file in dropbox', ['dropbox']),
    ("I'm looking for the lease in google drive", ['google_drive']),
    ("I'm looking for an email in gmail from my landlord", ['gmail']),
    ('help me find the lease in dropbox', ['dropbox']),
    ('help me clean up my gmail', ['gmail']),
    ('write a reply to the last email in gmail', ['gmail']),
    ('draft an email in gmail to Ann', ['gmail']),
    ('compose a message in slack to the team', ['slack']),
    ('create a github issue for the login bug', ['github']),
    ('open an issue on github about the login bug', ['github']),
    ('file a bug on github for the login crash', ['github']),
    ('make a new page in notion for the trip', ['notion']),
    ('write a note in notion about the call', ['notion']),
    ('schedule a slack message for Monday', ['slack']),
    ('send the file via slack', ['slack']),
    ('send it through gmail', ['gmail']),
    ('share it on slack', ['slack']),
    ("ask Bob on slack if he's free", ['slack']),
    ('let the team know on slack', ['slack']),
    ('upload the photos to google drive', ['google_drive']),
    ('back up my photos to dropbox', ['dropbox']),
    ('move the pdf into my onedrive', ['onedrive']),
    ('download the invoice from dropbox', ['dropbox']),
    ('copy the report from google drive to dropbox', ['google_drive', 'dropbox']),
    ('generate a song with pixio', ['pixio']),
    ('can pixio make me a logo?', ['pixio']),
    # --- hunt_s3
    ('how much revenue is in my stripe this month', ['stripe']),
    ('what are my stripe sales this week', ['stripe']),
    ('list my stripe products', ['stripe']),
    ('show my github stars', ['github']),
    ('update my github profile', ['github']),
    ('set my slack status to away', ['slack']),
    ('any unreads in my slack', ['slack']),
    ("what's on my notion todo list", ['notion']),
    ('restart my postgres instance', ['postgres']),
    ('clean up my gmail labels', ['gmail']),
    ('create a gmail filter for newsletters', ['gmail']),
    ('show my github actions runs', ['github']),
    ('check my github builds', ['github']),
    ('list my github gists', ['github']),
    ("what's in my google drive storage", ['google_drive']),
    ('check my notion calendar', ['notion']),
    ('summarize my slack huddles', ['slack']),
    ('find my dropbox shared links', ['dropbox']),
    ("reply to Dana's gmail", ['gmail']),
    ("what's my sentry error rate", ['sentry']),
    ('check my sentry quota', ['sentry']),
    ('look at my onedrive photos', ['onedrive']),
    ('show my notion kanban', ['notion']),
    ('review my github pull request', ['github']),
    ('merge my github PR', ['github']),
    ('close my github issue', ['github']),
    ('any github notifications?', ['github']),
    ('any slack mentions today?', ['slack']),
    ('how many stripe payments failed today?', ['stripe']),
    ('how many gmail messages are unread', ['gmail']),
    ('what sentry alerts fired overnight?', ['sentry']),
    ('which dropbox folders are shared with Ann?', ['dropbox']),
    # --- hunt_g2
    # --- lane_neg
    ('write an article on Stripe', []),
    ('Slack announced a new pricing plan today', []),
    ('compare Slack and Teams for a small team', []),
    ('is pixio better than midjourney?', []),
    ('give me your outlook on the market', []),  # NAMES NO APP
    ("what's the outlook for the market in 2027", []),  # NAMES NO APP
    ('my outlook on life is pretty good', []),  # NAMES NO APP
    ('the outlook is grim', []),  # NAMES NO APP
    ('what can pixio do?', []),
    ('is pixio any good?', []),
    ('what is pixio?', []),
    ('how much does pixio cost?', []),
    ('tell me about pixio', []),
    ("I read an article on Stripe's new pricing", []),
    ("what's your opinion on Notion?", []),
    ('give me some tips on notion', []),
    ("I'm writing a blog post on GitHub Actions", []),
    ('can you write a story about a sentry on the wall?', []),
    ('integrate our checkout with Stripe', []),
    ("we're migrating our data from postgres to mysql", []),
    ("we're switching from slack to teams", []),
    ('migrate the users from Firebase to Postgres', []),
    ('use postgres for the backend', []),
    ('I love Notion', []),
    ('we use slack at work', []),
    ('our team uses github', []),
    ('Notion, Obsidian, or Logseq?', []),
    ('Stripe: the complete guide to online payments', []),
    ('GitHub - the good parts', []),
    ('is slack down right now?', []),
    ('how do I archive a channel in slack', []),
    ("what's the best way to learn postgres?", []),
    ('which is better for me, Notion or Obsidian?', []),
    ('should we use Dropbox or Google Drive?', []),
    ('update the Slack and GitHub links in the footer', []),
    ('make the Slack and GitHub icons bigger', []),
    ('add the Stripe and PayPal buttons to checkout', []),
    ('write a LinkedIn post about our GitHub launch', []),
    ('run postgres in docker', []),
    ('how do I run postgres locally?', []),
    ('there is slack in the schedule', []),  # NAMES NO APP
    ('I have no notion what to write', []),  # NAMES NO APP
    ('explain how Slack threads work', []),
    ('I watched a talk on Postgres internals', []),
    ('a course on GitHub for beginners', []),
    ('any good tutorials on Notion?', []),
    ('is there a free plan on Notion?', []),
    ('can I host a website on github pages?', []),
    ('what are people saying about Notion on Twitter?', []),
    ('I heard pixio is good for video', []),
    ("pixio's pricing page", []),
    ('who founded Stripe?', []),
    ('where is Slack headquartered?', []),
    ('what did Slack say about the outage?', []),
    # --- lane 11B: rows that prove one rule each (mutation anchors)
    ('I need the slack messages from today', ['slack']),
    ('notion and google drive: find the roadmap', ['notion', 'google_drive']),
    ('compare Slack and Notion for a small team', []),
    ('Slack and Notion are both great', []),
    ('show me the slack and gmail messages', ['slack', 'gmail']),
    ('add the Stripe button and the PayPal logo', []),
    # joined apps are read with the words after the LAST of them
    ('update the Slack and GitHub screenshots in the docs', []),
    # --- lane 11B review: "I need / I want / I'd like" open the clause with a
    # direct object only; with "to <verb>" the verb decides, so moving away
    # from an app stays about the app (as "we're switching…" above), both ways
    ('I need to switch from slack to teams', []),
    ('I want to migrate from notion to obsidian', []),
    ("I'd like to unsubscribe from stripe emails", []),
    ('I need you to pull the invoices from stripe', ['stripe']),
    ("I'd like the invoices from stripe for March", ['stripe']),
    ('I want to see the slack messages from today', ['slack']),
    # an acting verb keeps the place even beside a switching word
    ('download the converted pdf from dropbox', ['dropbox']),
]

#: ``(text, armed)`` — does Auto arm the Guide's three lookups
#: (``guide_search``, ``app_search``, ``app_status``) for this message?
GUIDE_ROWS = [
    # --- v13600_ABOUT_JARVIS
    ('how do I connect Slack', True),
    ('how do I set up a workflow', True),
    ('how do I turn on local only', True),
    ('how do I use Jarvis from my phone', True),
    ('how do I add a skill', True),
    ('how do I change the default model', True),
    ('how do I schedule a daily report', True),
    ('how do I find my missions', True),
    ('how do I open the handbook', True),
    ('how do I enable computer use here', True),
    ('how do I add an agent', True),
    ('how do I connect GitHub to Jarvis', True),
    ('how do I install the add-on', True),
    ('how do I pair the browser', True),
    ('how do I set up a reflex', True),
    ('how do I use the knowledge graph', True),
    ('how do I change your voice', True),
    ('how do I send you my notes', True),
    ('how do I configure a webhook', True),
    ('how do I disable approvals for this chat', True),
    ('how do I add a tool pack', True),
    ('how do I turn on emergency stop', True),
    ('how do I chat with you from my phone', True),
    ('how do I connect gmail', True),
    ('how do I schedule something', True),
    ('How do I text you?', True),
    ('how do I add a tool', True),
    ('how do I install the browser add-on', True),
    ('how do I pair my browser', True),
    ('how do I send you a file', True),
    ('how do I automate my month-end close', True),
    ('how do I turn on computer use', True),
    # --- v13600_NOT_ABOUT_JARVIS
    ('how do I connect my printer to wifi', False),
    ('how do I send a wire transfer', False),
    ('how do I install node', False),
    ('how do I change a tire', False),
    ('how do I open a pdf in python', False),
    ('how do I set up a VPN', False),
    ('how do I find my IP address', False),
    ('how do I turn off autocorrect on my iPhone', False),
    ('how do I enable dark mode in Windows', False),
    ('how do I use pivot tables', False),
    ('how do I disable cookies', False),
    ('how do I configure nginx', False),
    ('how do I add a printer', False),
    ('how do I install python on windows', False),
    ('how do I schedule a meeting with my team', False),
    ('how do I automate excel with python', False),
    ('how do I add a tool in excel', False),
    ('how do I chat with customers on my website', False),
    ('how do I send money to my brother', False),
    ('how do I text a photo from my iPhone', False),
    ('how do I install an add-on in firefox', False),
    ('how do I connect two monitors', False),
    # --- v13600_ABOUT_JARVIS_PAGES
    ('how do I open settings', True),
    ('how do I find the settings', True),
    ('how do I change the theme', True),
    ('how do I add a project', True),
    ('how do I set up a goal', True),
    ('how do I change the model', True),
    ('how do I add a model', True),
    ('how do I change the default provider', True),
    ('how do I add an api key', True),
    ('how do I add my openai key', True),
    ('how do I add a secret', True),
    ('how do I add an MCP server', True),
    ('how do I install a pack', True),
    ('how do I open a new chat', True),
    ('how do I use the terminal', True),
    ('how do I use build mode', True),
    ('how do I set up the fleet', True),
    ('how do I install ollama', True),
    ('how do I use voice', True),
    ('how do I add a memory', True),
    ('how do I use the knowledge base', True),
    ('how do I turn off notifications', True),
    ('how do I install the update', True),
    ('how do I find my old sessions', True),
    ('how do I use a template', True),
    ('how do I connect claude code', True),
    # --- v13600_NOT_ABOUT_JARVIS_EVERYDAY
    ('how do I open a pack of gum', False),
    ('how do I set up a model railway', False),
    ('how do I add memory to my laptop', False),
    ('how do I find my data usage', False),
    ('how do I change my internet provider', False),
    ('how do I set goals for the new year', False),
    ('how do I change the model in pytorch', False),
    ('how do I find new connections on linkedin', False),
    ('how do I change my voice for a podcast', False),
    ('how do I change the oil in my car', False),
    ('how do I turn off notifications on my iphone', False),
    ('how do I change the theme in vs code', False),
    ('how do I open a terminal on mac', False),
    ('how do I find my tax return', False),
    ('how do I set up a 401k', False),
    # --- v13600_NOT_ABOUT_JARVIS_PAGE_SENSE
    ('how do I find a project manager', False),
    ('how do I send a project proposal', False),
    ('how do I find a session musician', False),
    ('how do I find the theme of a poem', False),
    ('how do I send updates to my clients', False),
    ('how do I add updates to my linkedin profile', False),
    ('how do I use a resume template', False),
    ('how do I find a lease template', False),
    ('how do I change my settings on my tv', False),
    ('how do I change the settings on my camera', False),
    ('how do I find the settings on my router', False),
    ('how do I open a new chat on whatsapp', False),
    ('how do I add a goal to my fitness app', False),
    ('how do I send notifications to my team', False),
    ('how do I add a model to my portfolio', False),
    ('how do I schedule a dentist appointment', False),
    ('how do I send my project to the client', False),
    ('how do I install a terminal block', False),
    ('how do I install new window panes', False),
    ('how do I change my memory foam pillow', False),
    ('how do I find my memories of childhood', False),
    ('how do I install a fleet tracking system', False),
    ('how do I add vehicles to my fleet', False),
    ('how do I find where the terminal is at the airport', False),
    ('how do I add memory', False),
    ('how do I install more memory', False),
    # --- v13600_ABOUT_JARVIS_PAGE_SENSE
    ('how do I change the model to claude', True),
    ('how do I change the theme to dark', True),
    ('how do I find the settings page', True),
    ('how do I turn notifications off', True),
    ('how do I add a memory to a project', True),
    ('how do I set the fleet up', True),
    ('how do I change which model answers', True),
    ('how do I open settings and change the theme', True),
    ('how do I find where the settings are', True),
    ('how do I use different models for different chats', True),
    ('how do I find my project folder', True),
    ('how do I change the chat settings', True),
    ('how do I use the dark theme', True),
    ('how do I change the model you use', True),
    ('how do I turn on notifications', True),
    ('how do I find my chat history', True),
    ('how do I set up a new project', True),
    ('how do I add a template to a project', True),
    ('how do I change my default model', True),
    ('how do I use local models', True),
    ('how do I find old chats', True),
    ('how do I install updates', True),
    # --- v13530_ABOUT_JARVIS
    ('What can you do?', True),
    ('what could you help me with?', True),
    ('What else can you do?', True),
    ('What are you able to do?', True),
    ('what are your capabilities?', True),
    ('can I talk to you from my phone?', True),
    ('can you run this report on a schedule?', True),
    ('can you read pages from my browser?', True),
    ('where in the app is the undo page', True),
    # --- v13530_NOT_ABOUT_JARVIS
    ('what can you do about the late fee?', False),
    ('what can you do to fix this formula', False),
    ('hey, how are you today?', False),
    ('what is the capital of France', False),
    ('what does this function do', False),
    ('summarise this pdf', False),
    ('write me an email to the client about the invoice', False),
    ("what's the difference between a 1065 and an 1120S", False),
    # --- hunt_sk_g
    ('how do I connect my phone', True),
    ('how do I chat from my phone', True),
    ('how do I add another computer', True),
    ('how do I add a rest hookup', True),
    ('how do I open the status page', True),
    ('Hi Jarvis! How do I connect my phone?', True),
    ("I'm new to Jarvis. How do I set it up?", True),
    ('Hey Jarvis. How do I add a folder?', True),
    ('how do I connect my dgx spark', True),
    ('how do I set up a backup', True),
    ('how do I use the bell', True),
    ('how do I connect my claude subscription', True),
    ('how do I add a provider', True),
    ('how do I use the browser', True),
    ('how do I add a schedule', True),
    ('how do I make a cake', False),
    ('how do I fix my bike chain', False),
    # --- hunt_g1
    ('what can you do', True),
    ('how does local only work', False),
    ('how do I add a schedule?', True),
    ('how do I schedule this to run every morning', True),
    ('how do I schedule a task', True),
    ('how do I schedule a reminder', True),
    ('how do I set a reminder', False),
    ('how do I set up a reminder for tomorrow', False),
    ('how do I turn on dark mode', True),
    ('how do I change my model', True),
    ('how do I text you', True),
    ('how do I add a file', False),
    ('how do I add files to a chat', True),
    ('how do I add documents', False),
    ('how do I upload a file', False),
    ('how do I add a pdf', False),
    ('how do I add my documents so you can read them', True),
    ('how do I find my old chats', True),
    ('how do I find a past conversation', True),
    ('how do I find an old conversation', True),
    ('how do I open a terminal', True),
    ('how do I open the terminal', True),
    ('how do I open a project', True),
    ('how do I install a model', True),
    ('how do I add a local model', True),
    ('how do I change the language', False),
    ('how do I change the font size', False),
    ('how do I make the text bigger', False),
    ('how do I turn off the sound', False),
    ('how do I enable sound', False),
    ('how do I change the voice', False),
    ('how do I set my name', False),
    ('how do I change my name', False),
    ('how do I add a contact', False),
    ('how do I find a file', False),
    ('how do I find files on my computer', False),
    ('how do I open a file', False),
    ('how do I use computer use', True),
    ('how do I add a workflow', True),
    ('how do I automate my weekly report', True),
    ('how do I automate emails', True),
    ('how do I set this up', False),
    ('how do I set it up', False),
    ('how do I use this', False),
    ('how do I use it', False),
    ('how do I start', False),
    ('how do I get started', False),
    ('how do I install it', False),
    ('how do I enable it', False),
    ('how do I turn it off', False),
    ('how do I find it', False),
    ('how do I open it', False),
    ('how do I change it', False),
    ('how do I add one', False),
    ('how do I send it', False),
    ('how do I configure it', False),
    ('how do I configure the app', True),
    ('how do I use iron jarvis', True),
    ('how do I set up telegram', True),
    ('how do I set up an agent', True),
    ('how do I make you remember something', False),
    ('how do I teach you something', False),
    ('how do I delete a chat', False),
    ('how do I export my chats', False),
    ('how do I back up my data', False),
    ('how do I update', False),
    ('how do I update the app', True),
    ('how do I use local only', True),
    ('how do I keep everything local', False),
    ('how do I stop you', False),
    ('how do I pause a schedule', False),
    ('how do I see what you did', False),
    ('how do I see the logs', False),
    ('how do I find the logs', False),
    ('how do I see my usage', False),
    ('how do I check usage', False),
    ('how do I set a budget', False),
    ('how do I change the budget', False),
    ('how do I add a webhook', True),
    ('how do I connect slack', True),
    ('how do I connect to my server', False),
    ('how do I connect my model server', True),
    ('how do I connect to ollama', True),
    ('how do I connect the spark', True),
    ('how do I pair my phone', True),
    ('how do I enable approvals', True),
    ('how do I turn off approvals', True),
    ('how do I approve things', False),
    ('how do I undo', False),
    ('how do I undo that', False),
    ('how do I restore a backup', False),
    ('how do I add an extension', True),
    ('how do I install an extension', True),
    ('how do I add a pack', True),
    ('how do I install an mcp server', True),
    ('what else can you do', True),
    ('what can you help me with', True),
    ('where is my schedule', True),
    ('do I have any workflows', False),
    ('can I use this from my phone', True),
    ('can you run this on a schedule', True),
    ('where in the app is the usage page', True),
    ('which page shows usage', True),
    ('how do I use the dashboard', True),
    # --- hunt_g3
    ('how do I chat on my phone', True),
    ('how do I add a second computer', True),
    ('how do I connect another computer', True),
    ('how do I add my spark', True),
    ('how do I attach a file', False),
    ('how do I add a folder you can read', True),
    ('how do I add a folder', False),
    ('how do I give you access to my files', False),
    ('how do I find the status page', True),
    ('how do I change the approval mode', True),
    ('how do I set the approval level', True),
    ('how do I install the desktop app', False),
    ('how do I update to the latest version', False),
    ('how do I open the creative gallery', True),
    ('how do I make an image', False),
    ('how do I generate an image', False),
    ('how do I add a hookup', False),
    ('how do I set my readable folders', True),
    ('how do I open the inbox', False),
    ('how do I find the bell', True),
    ('how do I change the persona', True),
    ('how do I change your personality', True),
    ('how do I make the replies shorter', False),
    ('how do I add a reflex', True),
    ('how do I add a pane', True),
    ('how do I open a build pane', True),
    ('how do I find my files', False),
    ('how do I switch models', False),
    ('how do I add a model server', True),
    ('how do I connect to my model server', True),
    ('how do I connect to lm studio', True),
    ('how do I sign in to claude', False),
    ('how do I connect codex', True),
    ('how do I connect my chatgpt account', True),
    ('how do I sign in to codex', False),
    ('how do I use claude code here', True),
    ('how do I connect claude', True),
    ('how do I connect openai', True),
    ('how do I add my anthropic key', True),
    ('how do I add a key', False),
    ('how do I set up voice', False),
    ('how do I turn on voice', True),
    ('how do I use read aloud', True),
    ('how do I make you talk', False),
    ('how do I make you speak', False),
    ('how do I talk to you', False),
    ('how do I set up the phone', True),
    ('how do I set up whatsapp', False),
    ('how do I set up telegram chat', True),
    ('how do I send a message from my phone', False),
    ('how do I search my chats', False),
    ('how do I search my old chats', False),
    ('how do I find something I said last week', False),
    ('how do I start a new chat', False),
    ('how do I export a chat', False),
    ('how do I share a chat', False),
    ('how do I rename a chat', False),
    ('how do I pin a chat', False),
    ('how do I set up my profile', False),
    ('how do I change the colors', False),
    ('how do I change the colours', False),
    ('how do I enable local only mode', True),
    ('how do I keep my data local', False),
    ('how do I stop sending things to the cloud', False),
    ('how do I set a spending limit', False),
    ('how do I enable backups', True),
    ('how do I restore from a backup', False),
    ('how do I report a bug', False),
    # --- hunt_g4
    ('I just installed you. How do I get my files in?', False),
    ('Jarvis, how do I connect my phone?', True),
    ('Thanks! How do I find my old chats?', True),
    ('I installed the app. How do I add files?', True),
    # --- lane_g
    ('how do I connect my phone to my car', False),
    ('how do I connect another computer to my wifi', False),
    ('how do I set up a status page for my website', False),
    ('how do I replace the spark plug', False),
    ('how do I install the bell on my bike', False),
    ('how do I find a provider in my network', False),
    ('how do I set up a backup of my iphone', False),
    ('how do I change my chatgpt plan', False),
    ('how do I open a browser in python', False),
    ('how do I set up a spark cluster', False),
    ('how do I start a conversation with a stranger', False),
    ('how do I find a conversation partner', False),
    ('how do I text from my phone', False),
    # the name in another sentence does not make another product's how-to
    # about Jarvis
    ('Hey Jarvis. How do I turn off autocorrect on my iPhone?', False),
    # "the spark" is the DGX Spark, never a plug or a cluster
    ('how do I connect the spark plug wires', False),
    ('how do I set up the spark cluster for my team', False),
]


#: The door's own WORDS under the reply on a fresh install (nothing
#: connected), through the real POST lane — the kinds folded into one
#: choice, the apps of a coordination named in one door.
DOOR_WORDS = [
    # (1) the place, the chat verbs, the label
    ("did anyone message me on slack?", ["Connect Slack in Settings"]),
    ("DM Bob on slack", ["Connect Slack in Settings"]),
    ("gmail: anything from the IRS?", ["Connect Gmail in Settings"]),
    ("how many open issues do we have on github?", ["Connect GitHub in Settings"]),
    # (2) my <app> <thing>
    ("list my stripe disputes", ["Connect Stripe in Settings"]),
    # (3) both apps, one door
    ("search dropbox and google drive for the lease", ["Connect Google Drive and Dropbox in Settings"]),
    ("check slack and gmail for anything urgent", ["Connect Slack and Gmail in Settings"]),
    # (4) a service the user works with
    ("generate an image with pixio", ["Connect Pixio in Settings"]),
    # (6) lower-case outlook names Microsoft 365
    ("what's on my calendar in outlook", [f"Connect {M365} in Settings"]),
    # kept on purpose (v1.360.0)
    ("check my inbox", [f"Connect Gmail or {M365} in Settings"]),
    ("what's on my calendar tomorrow?", []),
    # the other direction
    ("write an article on Stripe", []),
    ("Slack announced a new pricing plan today", []),
    ("compare Slack and Teams for a small team", []),
    ("is pixio better than midjourney?", []),
    ("give me your outlook on the market", []),
    ("stripe the table rows", []),
    ("remove the GitHub link from the footer", []),
]

#: The acceptance rows of each regression, exactly as the hunt's suggested
#: pins name them (they must be IN the table with this verdict).
ACCEPTANCE_OWN = [
    # (1)
    ("did anyone message me on slack?", ["slack"]),
    ("how many open issues do we have on github?", ["github"]),
    ("DM Bob on slack", ["slack"]),
    ("what did Sarah say on Slack yesterday?", ["slack"]),
    ("any messages for me on Slack?", ["slack"]),
    ("catch me up on slack", ["slack"]),
    ("tell the team on slack that I'm late", ["slack"]),
    ("remind me on slack tomorrow at 9", ["slack"]),
    ("how much did we make on stripe this month?", ["stripe"]),
    ("what are the top errors on sentry this week?", ["sentry"]),
    ("from postgres, how many users signed up yesterday?", ["postgres"]),
    ("gmail: anything from the IRS?", ["gmail"]),
    ("on github, list my open PRs", ["github"]),
    ("email this to Ann via gmail", ["gmail"]),
    # (2)
    ("list my stripe disputes", ["stripe"]),
    ("check my slack unreads", ["slack"]),
    ("did my github workflow pass?", ["github"]),
    ("is my postgres server up?", ["postgres"]),
    ("send me my dropbox link", ["dropbox"]),
    # (3)
    ("check slack and gmail for anything urgent", ["slack", "gmail"]),
    ("search dropbox and google drive for the lease", ["google_drive", "dropbox"]),
    # (4)
    ("generate an image with pixio", ["pixio"]),
    ("use pixio to make a video of a dog", ["pixio"]),
    ("can pixio make me a logo?", ["pixio"]),
    # (6)
    ("what's on my calendar in outlook", ["microsoft_365"]),
]
ACCEPTANCE_NOT_OWN = [
    "write an article on Stripe",
    "Slack announced a new pricing plan today",
    "remove the GitHub link from the footer",
    "share our GitHub launch post on Twitter",
    "compare Slack and Teams for a small team",
    "is pixio better than midjourney?",
    "give me your outlook on the market",
]
#: (5) the Guide's twelve, and the controls that never armed it.
ACCEPTANCE_GUIDE = [
    "how do I connect my phone",
    "how do I add another computer",
    "how do I add a rest hookup",
    "how do I open the status page",
    "Hi Jarvis! How do I connect my phone?",
    "I'm new to Jarvis. How do I set it up?",
    "Hey Jarvis. How do I add a folder?",
    "how do I connect my dgx spark",
    "how do I set up a backup",
    "how do I use the bell",
    "how do I connect my claude subscription",
    "how do I add a provider",
]
ACCEPTANCE_NOT_GUIDE = [
    "how do I make a cake",
    "how do I connect my printer to wifi",
    "how do I send a wire transfer",
]


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


def _door_table() -> dict[str, list[str]]:
    return dict(DOOR)


def _guide_table() -> dict[str, bool]:
    return dict(GUIDE_ROWS)


# =========================================================================== #
# THE WHOLE TABLE PASSES AT ONCE — both directions
# =========================================================================== #


def test_every_door_row_reads_as_meant_both_ways():
    """ONE pass over the whole door table. MUTATION (each mutation-checked
    red, see the lane's notes): drop the loose place rule, the chat verbs,
    the label reading, the owned nouns, the "my <app> <anything>" reading,
    the coordination, the service rule or the lower-case outlook -> rows in
    "lost the door" go red; drop the switch / product-side / joined-modifier
    vetoes or the modifier list -> rows in "door where none belongs" go
    red."""
    lost, wrong, extra = [], [], []
    for text, want in DOOR:
        got = own_data_apps(text)
        if got == want:
            continue
        if not want:
            extra.append((text, got))
        elif not got:
            lost.append((text, want))
        else:
            wrong.append((text, want, got))
    assert not (lost or wrong or extra), (
        f"lost the door: {lost}\n door where none belongs: {extra}\n wrong apps: {wrong}"
    )


def test_a_no_door_row_offers_nothing_through_connect_offers():
    """The other direction through the door's own function: every ``[]`` row
    that names an app, with every named app not connected, offers no door."""
    offered = []
    for text, want in DOOR:
        if want:
            continue
        rows = [{"id": cid, "name": cid, "state": "not_connected"} for cid in apps_named(text)]
        if connect_offers(text, rows):
            offered.append(text)
    assert offered == []


def test_every_guide_row_reads_as_meant_both_ways():
    """ONE pass over the whole Guide table. MUTATION: drop the message-wide
    name reading in ``_guide_fires``, the new Handbook words, the phone /
    another-computer / provider words or the backup page word -> rows in
    "lost the Guide" go red; widen them past their guards (the spark plug,
    the bell on a bike, a status page for a website, an internet provider)
    -> rows in "Guide where none belongs" go red."""
    lost, extra = [], []
    for text, want in GUIDE_ROWS:
        got = GUIDE <= set(select_auto_tools(text, cap=99))
        if got and not want:
            extra.append(text)
        elif want and not got:
            lost.append(text)
    assert not (lost or extra), f"lost the Guide: {lost}\n Guide where none belongs: {extra}"


def test_the_tables_are_wide_both_ways():
    door = _door_table()
    guide = _guide_table()
    assert len(door) == len(DOOR), "a door row is listed twice"
    assert len(guide) == len(GUIDE_ROWS), "a Guide row is listed twice"
    assert sum(1 for v in door.values() if v) >= 150
    assert sum(1 for v in door.values() if not v) >= 120
    assert sum(1 for v in guide.values() if v) >= 120
    assert sum(1 for v in guide.values() if not v) >= 120


# =========================================================================== #
# THE TABLE HOLDS EVERY OLDER PHRASING, WITH THE OLDER VERDICT
# =========================================================================== #


def _older_door_rows() -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    rows += [(t, []) for t in meant_v13600.NOT_OWN]
    rows += [(t, list(ids)) for t, ids in meant_v13600.OWN]
    rows += [(t, []) for t in rereview_v13600.MODIFIER_NOT_OWN]
    rows += [(t, list(ids)) for t, ids in rereview_v13600.ACTS_ON_THE_APP]
    rows += [(t, []) for t in door_v13590.RESEARCH]
    rows += [(t, list(ids)) for t, ids in door_v13590.OWN]
    return rows


def _older_guide_rows() -> list[tuple[str, bool]]:
    rows: list[tuple[str, bool]] = []
    for name, want in (
        ("ABOUT_JARVIS", True), ("NOT_ABOUT_JARVIS", False), ("ABOUT_JARVIS_PAGES", True),
        ("NOT_ABOUT_JARVIS_EVERYDAY", False), ("NOT_ABOUT_JARVIS_PAGE_SENSE", False),
        ("ABOUT_JARVIS_PAGE_SENSE", True),
    ):
        rows += [(t, want) for t in getattr(meant_v13600, name)]
    rows += [(t, True) for t in guide_v13530.ABOUT_JARVIS]
    rows += [(t, False) for t in guide_v13530.NOT_ABOUT_JARVIS]
    return rows


def test_the_table_holds_every_older_door_phrasing_with_its_verdict():
    """A phrasing an older wave pinned may never drop out of this table or
    flip here quietly: the older tables are read, and each row must be here
    with the same verdict."""
    door = _door_table()
    missing = [t for t, _ in _older_door_rows() if t not in door]
    differ = [(t, want, door[t]) for t, want in _older_door_rows() if t in door and door[t] != want]
    assert missing == [] and differ == [], (missing, differ)


def test_the_table_holds_every_older_guide_phrasing_with_its_verdict():
    guide = _guide_table()
    missing = [t for t, _ in _older_guide_rows() if t not in guide]
    differ = [(t, want) for t, want in _older_guide_rows() if t in guide and guide[t] != want]
    assert missing == [] and differ == [], (missing, differ)


def test_the_hunts_acceptance_rows_are_in_the_table_with_their_verdict():
    door = _door_table()
    guide = _guide_table()
    assert [(t, ids) for t, ids in ACCEPTANCE_OWN if door.get(t) != ids] == []
    assert [t for t in ACCEPTANCE_NOT_OWN if door.get(t, ["absent"]) != []] == []
    assert [t for t in ACCEPTANCE_GUIDE if guide.get(t) is not True] == []
    assert [t for t in ACCEPTANCE_NOT_GUIDE if guide.get(t) is not False] == []


# =========================================================================== #
# AS A NEW USER MEETS IT — the real lanes, a fresh install
# =========================================================================== #


def test_the_door_words_under_the_reply_on_a_fresh_install(app):
    """The real POST lane (``run_chat_turn`` → ``_gather_grounding`` →
    ``with_connect_door``), nothing connected, a model that only answers in
    words: the door under the reply is exactly these words, or none."""
    platform = app.state.platform
    got = {}
    for text, _want in DOOR_WORDS:
        res, _ = _post_turn(platform, text)
        got[text] = [d["label"] for d in res["doors"] if d.get("href") == APPS_HREF]
    differ = [(t, want, got[t]) for t, want in DOOR_WORDS if got[t] != want]
    assert differ == [], differ


@pytest.mark.parametrize(
    "text, label",
    [
        ("did anyone message me on slack?", "Connect Slack in Settings"),
        ("search dropbox and google drive for the lease", "Connect Google Drive and Dropbox in Settings"),
        ("generate an image with pixio", "Connect Pixio in Settings"),
        ("what's on my calendar in outlook", f"Connect {M365} in Settings"),
    ],
)
def test_both_lanes_carry_the_same_door(app, text, label):
    """MIRROR NOTE (lock-step): the stream lane's done frame and the POST
    lane's response carry the same door — the hunt's four lane probes."""
    platform = app.state.platform
    done, _ = _stream_turn(platform, text)
    res, _ = _post_turn(platform, text)
    want = [{"href": APPS_HREF, "label": label}]
    assert done["doors"] == want, done["doors"]
    assert res["doors"] == want, res["doors"]


@pytest.mark.parametrize("text", ["write an article on Stripe", "Slack announced a new pricing plan today"])
def test_both_lanes_offer_no_door_for_a_sentence_about_the_app(app, text):
    platform = app.state.platform
    done, _ = _stream_turn(platform, text)
    res, _ = _post_turn(platform, text)
    assert done["doors"] == [] and res["doors"] == [], (done["doors"], res["doors"])


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_lower_case_outlook_tells_the_model_about_microsoft_365(app, lane):
    """(6): the apps section names Microsoft 365 as not connected and offers
    to connect it — never the "Calendar: … never guess which app they use"
    choice the lower-case word fell into."""
    platform = app.state.platform
    out, model = (_stream_turn if lane == "stream" else _post_turn)(
        platform, "what's on my calendar in outlook"
    )
    section = _section(model.systems[0])
    assert f"- {M365}: not connected." in section, section
    assert "never guess" not in section, section


@pytest.mark.parametrize(
    "text, armed",
    [
        ("I'm new to Jarvis. How do I set it up?", True),
        ("how do I add a rest hookup", True),
        ("how do I make a cake", False),
    ],
)
def test_the_post_lane_shows_the_model_the_guide_as_the_table_says(app, text, armed):
    """(5) as the model meets it: the real POST lane with Auto on shows the
    Guide's three lookups for a how-to about Jarvis, and none for a cake."""
    platform = app.state.platform
    _res, model = _post_turn(platform, text)
    shown = set(model.tools[0])
    assert (GUIDE <= shown) is armed, (text, sorted(shown))
    assert dict(GUIDE_ROWS)[text] is armed
