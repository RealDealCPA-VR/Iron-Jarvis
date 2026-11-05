# Deep review and chat-first improvement plan — 2026-11-04

For approval. Written against v1.432.0 (the tree on `feat/mode-menu-names`).

## In one paragraph

The app's parts mostly fit together: every call the dashboard, the desktop app,
the browser add-on and the command line make to the daemon reaches a real route,
and the two chat code paths agree on almost everything. But two things fall
short of "I can do everything from chat". First, **chat often never finds the
right tool**: whether chat can change a setting depends on one hand-kept list of
words, and a busy sentence can push the settings tools out entirely. Second,
**many things simply have no chat tool**: projects, agents' models, switching an
app off, removing a notification destination, Local only, the night shift,
accounts, model servers, undoing a file edit. Of 30 everyday setup requests
traced through the code, **2 finish in chat today, 10 get partway, 18 cannot be
done**. The review also found **17 real bugs**. A few of them mislead the user, for example a
"Setting changed" card for a theme that never changes on screen, and a Settings
"Restart" that quits instead and later raises a false "keeps crashing" warning.

## How this was checked

- Seven read-only reviewers each covered one area:
  - settings reachable from chat;
  - records (projects, agents, schedules…) manageable from chat;
  - the dashboard's calls to the daemon;
  - events, background loops and config;
  - the two chat code paths and the tool registry;
  - the desktop app and browser add-on;
  - 30 real requests walked end to end.
- What chat offers for a sentence was **measured**: scripts ran the real tool-choosing code on about 180 sentences. It was not guessed.
- I re-read the code behind every finding marked **V** below. The rest (**R**) were read and quoted by the reviewer; each will be re-checked when its fix is built.
- Nothing in the app was changed by the review. No server was started, and the live install was not touched.

---

## Part A — Bugs (fix first)

| # | What the user sees | Cause | Fix | |
|---|---|---|---|---|
| A1 | "Turn on dark mode", "read replies aloud", a default persona or "Auto tools" changed from chat (or Settings › Appearance) shows "Setting changed … Undo", and **nothing changes on screen**. | Four "device" settings are saved by the daemon (`device_prefs.json`). The screen reads its own browser storage (`ij_theme`, `ij_chat_persona`, `ij_chat_auto_tools`, `ironjarvis.tts.enabled`); only `device.approval_mode` is ever read back (`lib/approvalDefault.ts`). The theme row also accepts any text. | Make the page apply device settings: read them at load, and listen for the `config.changed` event. Give the theme row the real theme list. Settings › Appearance then has one theme control, not two. (Decision D1.) | V |
| A2 | Turning off "Run app tools without asking" **from chat** leaves some apps still running without asking after a restart. | The Tools page clears each app's own flag after the switch (`routes/agents.py:4416-4421`). The settings writer has no such step, so chat's change skips it. | Move the per-app clear into the writer. The page and chat then share it. | V |
| A3 | "Switch to the Smith project" can focus a project that **doesn't exist** (or is archived). New chats are then filed under a ghost project with no context until the next restart. | `active_project_id` has no check (`settings/schema.py:679-681`), and it is allow tier. | Check it with the same test as the Projects page's Activate. | V |
| A4 | Adding a Discord destination from chat sends its **webhook URL, which works as a password**, to the model, and keeps it in the tool log, exports and backups. | `channel_connect` takes `webhook_url` as a plain argument and hides nothing in the log (`settings/records.py:1455-1477`). App connection strings (Postgres) go the same way. | Ask for these on the secure card (vault), hide them in the log, and teach the paste guard the Discord URL shape. | V |
| A5 | The browser sidebar never shows the app's own words at the end of a turn: "the model wrote nothing after its tools", the honesty notes, refusal footnotes. It says "finished without an answer" instead. | The sidebar reads `text` from the final frame, but the frame carries `reply` (`browser/panel.py:1621` vs `daemon/routes/chat.py:7363`). Two tests feed it the wrong shape. | Read `reply`; send the missing part as words; correct the tests. | V |
| A6 | The sidebar shows a reply **twice**, or leaves raw `<tool_call>` text, after a rewrite. | The sidebar ignores the "start the reply over" frame (`panel.py:1525-1528`). | Handle it: clear the reply so far. | R |
| A7 | Sidebar errors show technical text where every other place shows plain words. | Only `detail` is shown (`panel.py:1636-1645`). | Show the plain title and fix, and keep the technical text folded. | R |
| A8 | Settings › Maintenance **"Restart Iron Jarvis" quits instead**. Running coding tools are stopped as on Quit, and after a few uses in a day the desktop shows a false **"Iron Jarvis has crashed 3 times"** warning. | The button posts `/shutdown` with no body, which means Quit (`MaintenanceCard.tsx:67`, `LegacySettingsForm.tsx:1123`). The desktop app counts the exit as a crash. | Restart through the desktop app's own restart, which marks it as on purpose. | V |
| A9 | After an update fails to install, the next Quit **runs that same installer anyway**, skipping the safety steps. | `abortUpdateInstall` forgets the update but leaves "install on quit" on (`desktop/main.js` ~4650-4673). | Turn install-on-quit off there. | V |
| A10 | After Approve or Reject on a review, the **bell keeps counting it and the orb stays red** until a reload. | Approve and Reject publish no event (`routes/sessions.py:1360-1371`); the bell and orb wait for one. | Publish `review.approved` / `review.rejected`. | V |
| A11 | Turn a feature off (Slack, calendar, phone, watchers, autonomy, fleet) after it failed, and its **"isn't working · for N min" alarm stays forever**. | The off-switch paths leave the failing health row (`daemon/app.py:1481, 1659, 1707, 1983`…). Only Iron-Proxy and the backup copy clear theirs. | Clear the row when a loop is turned off. | V |
| A12 | `curator_enabled`, `max_concurrent_children` and `assignments_enabled` in `config.toml` **do nothing**, though the docs promise them. | They are not Config fields, so loading drops them (`core/config.py:1259`). | Make them real (typed) settings, or remove the reads and the promises. | V |
| A13 | Asking chat to "make the app start with Windows" (or "make the app green") shows a **"Work on this in a coding session" button**. | The coding-offer rule reads "make the app" as building software (`tools/autoselect.py` `_BUILD_ME_RX`). | Require a software noun after the verb. | V |
| A14 | One approved card (or an "Always" grant) lowering "Keep activity history" can **wipe the record of what the model did**, including the Undo journal. The tool also wrongly says a restart is needed. | `event_retention_days` is plain ask tier with `restart=True` (`schema.py:704-706`), and the prune deletes the ledger too. | Make lowering it ask every time; set `restart=False`. | V |
| A15 | Using chat without the stream (API, phone fallback), refused tools are **never listed** on the receipt. | The POST path fills `denied_tools` but never returns it (`chat_turn.py:9920, 10839`). | Return it; drop the test's default that hides it. | V |
| A16 | A tool that is **new in an app since install** gets an ordinary approval card; the "new in its app" warning never shows. | The daemon sends `quarantined`, and the dashboard never reads it. | Show one line on the card. | V |
| A17 | `web_look`, the Pixio tools and `delegate_remote` ask every time even when read-only, and can't be tuned on the Permissions screen. | No permission rows (`core/config.py`). | Add the rows, and mark the read-only ones as reads. | V |

Smaller items found while checking (folded into the waves below):
- Busy-chat failures in three background loops are logged where nobody looks.
- Webhook event names are not validated.
- Goal news never reaches the bell or the tray.
- A pop-out Settings window gets no update progress.
- Sender checks are missing on some desktop bridges.
- `openExternal` accepts any link scheme.
- POST chat checks a mistyped "/" skill only after the grounding work.

---

## Part B — Everything from chat

### What "done" means

If the app can do it, a request in plain words does all of the setup in chat:
- the right tool is offered;
- secrets go through the secure card;
- risky changes show an approval card with Undo.

Where a step truly needs a page (signing in through a vendor's login, a Google
or Slack developer setup, installing the browser add-on), chat gives **one
button to the exact place** and says what is left. Where the app does not offer
something at all (controlling the desktop screen, opening the dashboard from
another PC), chat says so plainly and never pretends.

### B1. Chat must find the right tool (measured problems)

| # | Problem | Evidence |
|---|---|---|
| B1.1 | The settings tools only appear for words on one hand-kept list. "Keep my PC awake", "switch updates to the stable channel", "stop asking me before running commands", "make new chats start in local only", "use Sonnet by default", "let other computers on my network use Jarvis": **29 of 57 setting requests arm nothing**, and some arm the wrong tools (the notification-channel tools for "stable channel", `web_fetch` for "use my Ollama server"). | `settings/tools.py:57-69`; the schema's own aliases are used only for searching (V) |
| B1.2 | Settings tools get only the slots left after up to 6 other tools, and only while "Auto tools" is on. A busy sentence drops them; with Auto tools off, no setting can be changed from chat, not even "turn Auto tools back on". | `chat_turn.py:4191-4221` (V) |
| B1.3 | Asking to **create** a schedule, goal, webhook or watcher offers only the edit and delete tools. Creating needs a hand-off to an agent, and a watcher can't be created at all. | `records.py:208-231`; `sentinel_add` is on no agent (V) |
| B1.4 | "Run the triage workflow now" can't run it. "Whenever a new email comes in…" doesn't offer the rule tool. "Rename the Intercom project" offers code-search tools. | `records.py:130-132`, `autoselect.py:1736-1743` (R) |
| B1.5 | Chat › Code, Build › Code, the Build terminal's chat and the browser sidebar send no device id, so per-device settings are refused there ("open this chat in the app"). | only `app/chat/page.tsx` sends it (V) |

### B2. Missing chat tools (each verified to have no tool today)

| Area | What a user would ask | Today | Proposed chat tool(s) |
|---|---|---|---|
| Projects | "Make a project for the Johnson audit in D:\Clients\Johnson", "rename…", "archive…", "switch to…" | Page only | `project_create`, `project_update`, `project_archive`, safe focus (A3) |
| Agents | "Create an agent called reviewer on GPT that only reads", "give the builder a $20 monthly limit", "pause / delete it" | Create only, through the Automation agent; no model, no limit | `agent_create` (model, roles, allowance), `agent_update`, `agent_pause`, `agent_delete` |
| Apps | "Turn off the GitHub app", "disconnect Notion", "add the QuickBooks server I use in Claude Desktop" | Connect only | `app_switch`, `app_disconnect`, `app_import` (a preview card, keys on the secure card) |
| Notification destinations | "Stop sending to Slack", "add an email destination", "only send me failures" | Add Telegram/Slack/Discord; a two-way switch | `channel_remove`, alert choice, email type (password on the secure card) |
| Goals from Chat | "Stop the bookkeeping goal" | Can't see them | include them in `goal_list`; pause/stop/delete |
| Local only | "Keep this chat on my PC", "start new chats local only" | The chip only; nothing stops the model saying it's on | a tool for this chat (on = no card; off = always a card) plus a new-chat default the page reads |
| Night shift | "Queue 'update the README' for tonight" | Page only | `night_task_add`, list, cancel |
| Model servers | "Add my LM Studio server at 192.168.1.20:1234 and make it default" | Settings rows exist but are rarely offered; no model list | `model_server_add` (key on the secure card); the default model can then be picked from its list |
| Accounts | "Add a second Claude account", "put work and personal in separate groups" | Page only | `account_add`, then the sign-in door (the vendor's login has to run in a terminal); `account_groups` through settings |
| Memory | "Use Notion as your long-term memory", "use my memory profile X" | Notion lands as an app, not as memory | `memory_base_add` (token on the secure card, applied without a restart); the profile switch through settings |
| Undo and history | "What did you change today?", "undo the last file edit" | Settings and records only | `changes_list` (read) and `change_undo` (card), over the same Undo the receipts use |
| Coding sessions | "Use Codex on my work account for this repo", "Codex writes, Claude reviews" | Page only | folder defaults and the session team as tools (Full access stays out, by design) |
| Desktop app | "Start with Windows", "keep running in the background" | Tray only | settings the desktop app reads at start (like the update channel) |
| Preferences, lessons, personas, templates | "Forget that preference", "add a lesson", "make a persona that answers like a CPA" | Add a preference only | edit/forget/add tools |
| Profile language | "Answer me in Spanish from now on" | Profile page only | schema rows for language, "stick to it" and formatting rules |

### B3. Doors where a page is truly needed

These stay on a page, but chat names the step and opens it with one button:
- signing in an account (its sign-in terminal);
- Google or Slack developer setup;
- installing and pairing the browser add-on;
- downloading a local model;
- anything the user must approve in their vendor's account.

Today most of these have no door at all; the model answers from memory. B3
adds an "intent → page" door table, so a door appears whenever the message names
one of them.

---

## Part C — Disconnected features and cleanup

**Features the daemon has but no screen reaches** (each V: no client call):
- A Goal from Chat can never be deleted. The route exists; Autonomy has no button.
- An Iron-Proxy account can't be **signed out**, only removed. This matters for the shared-login fix.
- A coding session's **pull-request watch status** (rounds left, next check, gave up) is never shown.
- A saved calendar link can't be removed from the Reflex page.

**Dead code:**
- Seven dashboard modules are imported only by tests: `EventStream`, `FirstRunWizard`, `ModelSwitcher`, `AppGrid`, `HealthCard`, `PowerTips`, `tileDrag`.
- About 30 routes have no caller, including leftovers of the removed round table (`/agents/threads` create/say/remember/delete) and older copies of current routes.

**Small honesty gaps:**
- Help names a tray item ("Restart Iron Jarvis") that only appears when something is stuck.
- The hotkeys are shown nowhere in the app.

---

## Proposed waves

Each wave is its own release (or a few), tested on GitHub before it ships, with
the same review discipline as recent waves.

| Wave | What | Size | Done means |
|---|---|---|---|
| **1. Bugs** | A1–A17 and the small items. A1 follows decision D1. | medium | each bug pinned red-then-green; A5/A8/A9/A10 checked in a real browser or app where possible |
| **2. Chat finds the right tool** | B1.1 settings found from the schema's own words, with the question guard kept; B1.2 a reserved place for the settings tools, and `config_list` even with Auto tools off; B1.3 create tools offered (asking first), the watcher reachable; B1.4 phrasings; B1.5 the device id from every in-app chat. **The ~180 measured sentences become a pinned table, both ways.** | medium | at least 90% of the setting sentences arm the right tool; no sentence arms an unrelated writer |
| **3. Missing chat tools, part 1** | Projects, agents, apps (switch, disconnect, import), notification destinations, Goals from Chat, Local only, undo and history | large | each with an Undo card and asking first; secrets only on the secure card |
| **4. Missing chat tools, part 2** | Night shift, model servers, accounts, memory bases, coding-session defaults and team, desktop start settings, preferences/lessons/personas/templates, profile language | large | the same |
| **5. Doors and honesty** | The intent → page door table; plain "not offered" lines; the model never claims a setting it didn't change (a check like the existing "claimed a file" note) | medium | every page-only step has a door; a claimed-but-not-made change gets a note |
| **6. Disconnected and cleanup** | Part C, with a decision per dead module or route (remove, or reconnect) | small–medium | nothing orphaned without a reason written down |
| **7. Prove it** | The 30-request walkthrough becomes an automated test, and a live chat run of each request on a scratch app | medium | at least 26 of 30 done in chat; the rest end at a working door |

## Decisions for you

- **D1 — Device settings (theme, read aloud, persona, Auto tools).**
  - *Recommended:* make chat able to change them, with the page applying the change live.
  - *Alternative:* remove them from chat and Settings' second control, and leave them on their own controls.
- **D2 — How chat finds settings.**
  - *Recommended:* generate the trigger from every setting's name and aliases, and give settings tools their own place.
  - The cost: chat will offer the settings tools a little more often, and they are cheap to offer.
- **D3 — Local only from chat.**
  - *Recommended:* turning it ON needs no card; turning it OFF always shows a card (privacy).
- **D4 — Undo file edits from chat.**
  - *Recommended:* yes, over the same Undo the receipts already use, always with a card.
- **D5 — Start with Windows / keep running in background as settings.**
  - *Recommended:* yes, read by the desktop app at start.
- **D6 — Dead modules and round-table routes.**
  - *Recommended:* remove the seven dead modules.
  - Give chat-made rooms Remember/Delete in their transcript view, or remove those routes; I recommend removing them.
- **D7 — Wave order.**
  - *Recommended:* 1 → 2 → 3 → 5 → 4 → 6 → 7.
  - Doors (5) come before the larger part-2 tools, because they make every remaining gap honest quickly.

## Not changing (by design or out of reach)

- Controlling the Windows desktop screen: the app has no such feature (browser automation only). Chat should say so.
- Opening the dashboard from another computer: the dashboard listens on this PC only. LAN access lets another Iron Jarvis hand it tasks; it is not remote use of the screen.
- A Google or Slack developer app, and a vendor's own sign-in, cannot be done for the user; chat gives the door.
- Full access as a folder default stays refused (v1.426.0 decision).
