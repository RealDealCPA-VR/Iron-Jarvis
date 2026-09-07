# Iron Jarvis — The Handbook

*The user guide. What this app is, how to work it daily, and the rules it
holds itself to. Current as of v1.241.0 (2026-09-07).*

---

## What Iron Jarvis is

Iron Jarvis is a **local-first AI operating system** for daily creative,
coding, and office work. One Python daemon owns all state, agents, tools, and
memory on **your machine**; a dashboard renders it; an Electron desktop app
wraps both with a tray, global hotkeys, and auto-updates. Client documents,
chat history, memory, and credentials never leave the box unless *you* connect
a cloud provider — and even then, an unreachable model **refuses and names
itself** rather than silently substituting another one. That honesty rule is
load-bearing everywhere: honest errors beat fabricated output, suggest-don't-act
for anything autonomous, everything reviewable, everything undoable where an
undo can be captured truthfully.

**The three processes**

| Process | What | Where |
|---|---|---|
| Daemon | FastAPI — state, agents, tools, memory, events | `127.0.0.1:8787` |
| Dashboard | Next.js — every page below | `127.0.0.1:8788` |
| Desktop | Electron — tray, hotkeys, updater, Spotlight | wraps both |

**Hotkeys:** `Ctrl+Shift+J` toggles the window — or `Ctrl+Alt+J` when
another app holds it (the desktop app tries the two in order and retries a
taken key every 30 minutes); the tray menu and the Overview tips card show
the key that is live, or say "hotkey unavailable" when both are taken.
`Ctrl+Shift+Space` opens Spotlight. State lives in `%APPDATA%/Iron Jarvis/.ironjarvis/` (SQLite DB,
config.toml, secrets vault, skills, backups). Updates download automatically
(checked at boot and every 30 min) and install only when you click
**Restart to update**. While the window is hidden or minimised the dashboard
stops polling the daemon (the daemon check itself slows to every 30 s) and
refreshes everything the moment you bring it back.

---

## The surfaces, in the order you'll use them

### Chat — the main surface
Chat is where most work happens, and it is wired into everything:

- **Attachments**: drag-drop or the "+" menu (up to 4 files, 20 MB each).
  Images go to vision; documents are inlined or retrieved via RAG with page
  references, sized to the answering model's context window.
- **Files rail**: every file a conversation *makes or was given* appears on
  the right — preview (spreadsheets as sheets, PDFs and images inline, docx as
  a Word-faithful page), download, open in the native app, save a copy, and a
  **Changes** toggle that diffs a file you re-preview. Truncated previews say
  exactly how much is missing.
- **TurnReceipt**: under each reply — which provider/model actually answered
  and *why* (your pick, the default, a failover), plus the files that turn
  really wrote per the ledger. A mock or failover answer is flagged in amber.
- **Draft cards**: when the model drafts an email, it arrives as a card whose
  **Copy** writes rich text that survives pasting into Outlook (bold, lists,
  links — spacing included, because Outlook renders through Word).
- **"/" skills** anywhere in a message invoke a skill; **@mentions** pull
  agents into a panel bound to the thread; **escalation** hands a chat request
  to a real background agent session when it outgrows a chat turn.
- **Projects (the context spine)**: ground a chat in a project and every turn
  carries the project's folder, knowledge, and recap. File tools then operate
  inside that folder. A folder you cannot save in is refused when you pick it,
  with the reason (missing, protected, not a directory, or not writable) —
  the same door for a chat's folder, an escalated session's `workspace_root`,
  and a project's folder (checked again when an older project runs a task).
  If a bound folder turns read-only later, a write says "cannot write in
  <folder>: the folder this session is bound to is not writable" instead of
  naming a hidden temp file.
- **Compaction**: when a long thread nears the context ceiling, chat *offers*
  a model-written summary of the older messages (you choose); every claim in
  the summary is checked against the ledger before it is trusted.
- **The session namespace**: verbose tools (listings, big reads, shell
  output) can store results as variables (`_store_as`) instead of flooding
  the context; the model reaches them with the `repl` tool. The stored value
  is `{'output': the text, 'data': metadata}`.
- **Malformed tool calls are corrected, not crashed**: a tool called with a
  missing argument tells the model which one (`missing required: query —
  file_search needs ['query']; got []`), ledgered like any failed call, so
  the next step can fix it instead of relaying a Python error. A local model
  that wraps its arguments one level down (`{"arguments": "{…}"}`) is
  unwrapped before the tool ever sees it.
- **Search says what it did not read** (v1.232.0): `file_search` and `grep`
  now read UTF-16 files (what PowerShell's `>` redirect writes, so your own
  logs are searchable), and a file too big to scan is COUNTED and reported
  ("2 file(s) skipped as oversize (over 1 MB each). This search did NOT cover
  them") next to the existing unreadable-file note — a hole in a search is
  always said out loud, never left to look like "no match".
- **The composer explains itself** (v1.232.0): the "+" menu's **Web &
  research** and **Auto tools** switches each carry a one-line hint under
  them ("Lets this chat search the web and read pages" / "Each request picks
  the safe tools it needs"); the approval dropdown's hover title says it is
  the *approval posture for this chat*; the footer's model button reads
  **default · <model name>** (the same name the title bar shows) instead of
  "default model"; and a receipt line that used to say "3 tools max" now
  says **capped at 3 tools for this local model** — the envelope fitting your
  own measured model, not a limit you set.
- **What an escalated run inherits from the chat** (v1.232.0): the armed
  tools (on every message, not just the first), a grant you answered on a
  card, the grounded folder, and the approval posture — except *Auto-approve
  (YOLO)*, which escalates as *Approve for me*. See *Agents & jobs* for the
  rules, and the session's amber chips ("Waiting for you · shell",
  "Completed · needs you") now keep their own case.

### Agents & jobs
- **Post a job** on the Agents page: default target **"Team"** runs a
  supervisor that plans and delegates to specialist agents *in parallel*
  (builder, researcher, reviewer, and your custom agents; a remote agent is
  reached through an honest supervisor bridge). Origin-tagged `job:agents`.
- **Roster**: who can take work, with measured success stats. "Give work"
  posts a job at a specific agent; "Talk" opens a conversation instead.
- **Round table**: persistent multi-agent conversation threads — panelists
  answer in turn and see each other.
- **Custom agents**: author your own (name, prompt, tool list, pinned
  provider/model). **Remote agents**: register an agent running elsewhere
  (URL + bearer token, encrypted at rest; edits never eat the credential).
- **The roster is the contract** (v1.227.0): an agent may only call the tools
  it was given. If the model names any other installed tool (a read-only
  Reviewer inventing `rename_file`, a chat with only `read_file` armed calling
  `write_file`), the call is refused, nothing runs, the refusal sits on the
  run's ledger as "not armed", and the model is told plainly. Same rule in
  chat: the tools you armed with "+" or Auto are the whole set.
- **Approvals in a batch** (v1.227.0): when a run asks for permission several
  times at once (a folder rename asks once per file), the chat thread shows
  one card per pending ask; answering one leaves the others in place, and
  **Allow for this conversation** on any one of them answers every other
  pending ask for the same tool. An ask nobody answers in 5 minutes is
  reported to the model as *paused, not run* (never as "the user declined"),
  and the run carries a **needs you** verdict at the end.
- **Grants carry, and the bell can answer a batch** (v1.232.0): a tool you
  allow "for this conversation / for this run" is written to the session, so
  the next message you send to that run (and a re-run) does not ask again;
  the chat sends the tools it has armed on every message, not only the
  first. The notification bell's agent-ask row now has **Allow for this
  run** beside Approve once / Deny — one click clears every pending ask for
  that tool. A hard `deny` is never lifted by any grant.
- **The approval posture rides an escalation** (v1.232.0): when a chat hands
  work to an agent session, the chat's approval dropdown goes with it.
  *Approve for me* lets the tools you armed in chat run without a pause and
  asks once for anything else; *Ask for approval* asks once per run for
  every ask-tier tool, armed or not (Allow for this run covers the rest of
  the run). *Auto-approve (YOLO)* is **never inherited**: a YOLO chat's
  escalated run behaves as *Approve for me* — you consented to auto-approval
  one watched turn at a time, not for a background run making a batch of
  calls while you are elsewhere.

### Sessions & Kanban
Every agent run is a **session**: live token/tool streaming, the delegation
**TeamTree**, the team's shared **blackboard**, a ledger-derived result card
(files created/changed *proven from the ledger*, never from the model's
closing paragraph), transcript export, cancel/rerun/continue. Kanban shows
the same sessions as lanes — including **Queued** when a concurrency limit is
set. Cancel genuinely stops the agent, in every lane.

- **Waiting for you** (v1.227.0): a run paused on a permission ask sits in
  the **In review** column with an amber "Waiting for you · tool" chip, and
  the session page shows the same approval card chat shows. A run waiting
  for you shows in the bell and as an amber chip; an ask nobody answered is
  on the session's outcome (*needs you*) — the bell does not keep a record
  of asks that already expired.
- **The verdict is separate from the status** (v1.227.0): a finished run also
  carries an **outcome** — *completed*, *completed · with failures* (a call
  that changes files or state failed), or *completed · needs you* (an ask was
  never answered). The session header, the sessions list, the kanban card and
  a project's recent runs show it as an amber chip instead of a green badge,
  and the result card headlines "Finished — N calls were never approved".
  The Worklist panel offers **Re-run the N failed items**, which re-opens
  them and continues the run.
- **The worklist re-offers your own items** (v1.227.0): a run that claimed a
  chunk and reported only part of it is handed its own remaining items back,
  and finished, cancelled or interrupted runs release their claims at once
  instead of holding them for 15 minutes.
- **Summaries read like chat** (v1.230.0): the summary card on a session page
  renders the agent's markdown the way a chat reply does — bold, lists, code —
  and a project's **Recent runs** rows show the summary's words on one line,
  never its asterisks.
- **A session page in your words** (v1.232.0): the **Live activity** rows
  show the same short labels chat's progress line uses ("Using read_file…",
  "Step 2 of 4: …"; the raw event name is on hover), the **Traces** card is
  now **Model calls** (each request and answer this run made), **Time-travel**
  keeps its name and gains the hint "every action this run took, newest first
  — undo what allows it", and the sessions list's cleanup button is **Clean
  up leftover folders** (the git worktrees failed or deleted sessions left
  behind). A session with no pending review no longer logs a 404 on every
  visit — the daemon answers `{"review": null}`.

### Documents
Read/extract (PDF incl. scanned-with-OCR fallback, docx, xlsx, pptx, csv,
images described), create with real structure (markdown → real headings,
tables, code in docx/pdf/pptx/html; multi-sheet xlsx with formulas), convert
between formats, split/merge/arrange PDFs (originals never modified),
**redact PII** with a scan → confirm → verified-removal flow (the written PDF
is re-read to *prove* the values are gone), batch-process whole folders with
per-document extraction and synthesis, and an Excel engine that can check
formulas by computing them. The **Documents page** itself holds read/extract,
create, redact and the batch history; **convert, split, merge and batch
processing are done from Chat** — ask for it in chat, or use the "+" menu —
and the page's empty state offers those four as example chips that open Chat
with the request typed in.

### Memory
- **Long-term memory**: markdown bases (Obsidian vault supported), Notion,
  imports from ChatGPT/Claude/Takeout exports, all grounded into chat
  automatically ("# Relevant from memory"). Imports live on the **Long-term**
  tab of the Memory page (`/memory?scope=longterm`, also reachable as
  `/ltm`); since Simple mode hides `/ltm` from the nav, the Memory page's
  other tabs carry a one-line link **Import from ChatGPT/Claude/Takeout →
  Long-term memory** that opens it.
- **Project knowledge**: per-project notes and uploaded documents, embedded
  on write, retrieved on every grounded turn.
- **Memory steward**: a scheduled curator that proposes memory changes for
  your approval — it never silently rewrites what the app knows about you.
- **The 3D memory graph**, lessons, and a "What I can remember" index the
  model sees each turn.
- **Two search boxes, two jobs** (v1.232.0): the **Recall** box at the top
  of /memory searches every store at once; the box under the Working tab is
  titled **Search working memory** and says so — it searches only the
  working store (session · project · user). The count field beside each
  query is labelled **Results** (it was "k").

### Automation
- **Schedules**: cron/interval/date tasks that fire real agent sessions
  (project-bound, outcome recorded on the row, delivered to your channels).
  The row tells the truth about fires that did NOT happen too (v1.231.0): a
  fire missed while the PC slept or the app was closed shows as **missed** on
  the row (with the time) and never fires late twice — a fire up to five
  minutes late still runs, so a laptop that woke at 3:02 gets its 3:00 job;
  a tick that lands while the previous fire is still running shows as
  **skipped**, and Run-now on a task that is still running answers "already
  running" instead of starting a second copy. Cancel on a schedule-fired
  session stops it at once. On a Windows box whose time zone cannot be named,
  the daemon still boots, schedules run on UTC, and the doctor says so
  (`scheduler_timezone`).
- **Workflows**: multi-step (agent/tool/ask/notify steps, parallel groups,
  retries); a run parks on an *ask* step and waits for your answer — from the
  chat card or the Workflows page. The Workflows page has the visual editor
  (Load ▾, Save, Run), a **Saved workflows** list under it with **Load** and
  **Delete** on every row — Delete asks first and names any schedule or
  reflex rule that still fires that workflow, since those would fail until
  re-pointed — starter templates, a "build with chat" box, and the run
  history with Resume for interrupted runs. Workflows are born three ways:
  describe a repeatable process in Chat and a **draft card** appears (Save,
  Run once, or Open in the editor — a card born inside a project is pinned to
  that project, so its runs use the project's folder and knowledge); type a
  description into the Workflows page's "build with chat" box; or build one
  step by step in the editor. Every door accepts what a local model actually
  writes — JSON with a stray comma, steps as sentences, the workflow written
  in prose — and a step that could never run is refused when you save, by
  name. A run whose pinned project folder has gone missing says so on its
  run-history row instead of quietly working in a scratch folder (and the
  note clears on a Resume that finds the folder back). A step that
  **crashes** — the model endpoint down, a tool that raises — counts as
  **failed** for retries and `on_failure`, exactly like a step that reports
  a failure, so a `retry` re-attempts it and a `skip` continues past it
  with the reason on the row. **Finished steps survive a restart**: each
  step is written to the run record the moment it completes, so a Resume
  after the daemon died mid-way never re-runs (or re-notifies) work that
  was done. A later step that references a failed-and-skipped step gets
  `[step Name failed: reason]` in place of `{{Name}}` — never the error
  text passed off as the step's output — while `{{Name.data}}` of a failed
  step is empty (a failed tool records no data). Saving refuses a
  `{{Name.data}}` whose `Name` is not one of the workflow's steps (a typo
  like `{{Scna.data}}` would render nothing on every run); a bare
  `{{Name}}` naming no step is read as a run input.
- **Reflexes**: webhook-triggered actions. **Sentinels**: watched folders.
  **Autonomy**: off by default; when on, starts at *suggest* level with hard
  daily action/token caps and a kill switch.
- **A reflex rule that cannot start says so on its row and in the reply**
  (v1.231.0). When a signal matches a rule whose workflow was deleted or
  whose remote agent is gone, the rule's row on the Reflexes page shows
  *could not start: <reason>* until a later fire succeeds, the webhook's
  answer lists it under `failed` instead of counting it as fired, and a
  phone message that matched it gets `Rule "<name>" could not start:
  <reason>` back — it no longer falls through to an unrelated free-form
  answer. A webhook POST with a bad or missing signature is refused with
  **401** (unknown address: **404**) and the activity timeline records
  `webhook.rejected` with the reason, so a probed or misconfigured secret
  is visible rather than a silent 200.
- **A sentinel whose folder vanished keeps its memory** (v1.231.0). When a
  watched folder is unreachable (USB stick unplugged, OneDrive folder
  offline), the Sentinels page shows *root unreachable since <time>* on the
  row and the watcher keeps the files it had already seen; plugging the
  drive back in proposes nothing for untouched files instead of treating
  every one of them as new. You can add a sentinel for a folder that is not
  there yet — it baselines the first time the folder appears.
- **One execution seam** (v1.231.0): a scheduled task, a reflex rule, a goal
  iteration or a phone message bound to a project runs **in the project's
  folder** — the same folder, through the same check, as a task started
  from the project page — and it **can ask you**: when it hits a tool that
  needs approval, the bell and your phone get the question, and if nobody
  answers within five minutes the run ends as **needs you** rather than
  quietly skipping the work. Every such session is stamped with where it
  came from (`schedule:<name>`, `reflex:<rule>`, `comm:<channel>`,
  `autonomy`, `goal:<id>`, `workflow:<name>`), so the session list and the
  activity timeline can answer "did I start this, or did it start itself?".
  If a project's folder is set but cannot be used (missing, protected, not
  writable), the run works in a scratch workspace and says so on its
  session row instead of pretending it worked in your folder.
- **Channels**: desktop notifications, Slack, Telegram (chat-id
  auto-detect), email — with per-destination event routing.
- **A dead phone token is visible, and a dropped message says so**
  (v1.231.0): if your Telegram bot token is revoked or rotated (or your
  mailbox refuses the IMAP login), the Channels row turns red with **Not
  listening — …** and the exact reason (for Telegram: paste the current
  token from @BotFather), and the daemon's health line reports the inbound
  loop as failing instead of "polling fine" — before this, a dead token
  looked exactly like a quiet phone, forever. Separately, two-way messaging
  is at-most-once on purpose (a message being handled when the app is
  restarted is dropped rather than run twice); now the restart tells that
  chat *"I was restarted while handling your last message — please resend
  it"* and the Activity timeline records a `comm.dropped` event, so a lost
  message is never silent.
- **Goals trip on consecutive bad nights, and waiting is not work**
  (v1.231.0): a goal's circuit breaker trips — the goal stops iterating
  until you reopen it — after **3 failed iterations inside 30 minutes OR 3
  failed iterations in a row at any spacing**; a goal scheduled "every
  night at 3 am" that fails three nights running now trips on the third,
  where before each night's failure aged out of the window and it failed
  forever. One successful iteration resets the run. And the goal's
  `max_wallclock_s` budget is charged only for the time the iteration
  worked: minutes its session spent parked on an approval nobody was awake
  to answer are not billed (the iteration still ends with the honest
  "approval timed out" receipt).

### Terminals, Creative, and the rest
Free-form terminal canvas with AI assist per pane; a Creative gallery for
generated media (Pixio); Skills (yours + auto-suggested from successful
sessions, approval-gated); Connections (providers/health); Usage (token
costs); Activity (the full undo-capable action ledger); the **/you** page
(your profile, personas, accessibility presets — injected into every prompt
seam); Train (teach it your writing voice, suggest-only).

- **Connections tells one truth** (v1.230.0): a provider inherited from a
  logged-in CLI shows as **Inherited from claude-cli** (or codex-cli) — it is
  connected, the switcher and chat can use it, and the chat picker calls it
  *included* rather than *metered*, because the subscription pays. There is
  no Disconnect on that card (no key is stored here; log out of the CLI to
  drop it), and **Test** says the same. A provider is "Not connected" only
  when nothing — vault key, environment, or CLI login — can serve it, which
  is exactly when the health dot says so too.
- **The model switcher shows what is active first** (v1.230.0): the topbar
  switcher pins your **Active model** as the first row, then lists **All
  models** grouped by provider — your own hardware and flat-rate CLIs first,
  metered APIs after — and folds anything offline under one **Show offline
  (N)** line, so the active local brain is never 2,000 px down the list.
- **Build panes** (v1.232.0): every pane header has a **Clear scrollback**
  eraser — it wipes the pane's on-screen history and repaints (the shell
  keeps running; the daemon's own scrollback is untouched), which is the way
  out of a garbled replay after a reconnect. And a terminal session is only
  ever *resized* by the window you are looking at: a pane sends its size to
  the shell only while it is visible **and** its window has focus, so
  opening the same session from a phone or a second tab never reflows the
  desktop's running session (the last focused window wins).
- **Usage counts models that did work** (v1.232.0): the By-model list and
  "Across N models" leave out the offline mock provider and rows that moved
  zero tokens (a probe, a refused call, a misconfigured id); the API still
  carries the full list as `by_model_raw`.
- **Activity tiles are about the rows on screen** (v1.232.0): **Tokens in
  this view** / **Cost in this view** (they were "(loaded)") — account
  totals live on Usage. A decision row's second line reads "Picked a model
  for this turn · brain" instead of repeating the event name twice.
- **Local Fleet** (v1.232.0): a node whose probe has not answered yet wears
  **not detected yet** rather than "Unknown".
- **Updates from a phone or a browser tab** (v1.232.0): the page says
  *Updates install from the desktop app on your PC (tray →
  Restart to update)* — it can show you the version, nothing there installs.
  The
  git self-update card belongs to a daemon started from a source checkout.
- **Settings → Daemon access token** (v1.232.0): inside the desktop app the
  box is read-only and says **Set by the desktop app** (it seeds the token
  on every launch — nothing to paste or clear). The paste box, **Clear**, and
  the "leave this empty" line appear only in a browser without the desktop
  bridge (a deployed daemon, a phone over Tailscale).

---

### Browser — your own Chrome

The **Browser** page is where Jarvis meets a browser. There are two of them, and
the difference matters.

**Your browser** is the Chrome or Edge you already use, logged into the sites you
are already logged into. Jarvis reaches it through a small **browser add-on** you
load yourself. Nothing is automatic: the add-on has to be loaded, you have to
press **Pair** in Jarvis, and you have to grant it access to sites. Until all
three happen, Jarvis cannot see a single tab.

**Jarvis browser** is a browser the app owns, separate from yours and logged into
nothing. It is not built yet, and nothing in the app pretends otherwise.

Below those sits the older **computer use** machinery, which drives a headless
Chromium of its own behind a domain allowlist. It is unchanged, and it shares no
cookies or sessions with your browser.

**Setting it up (v1.240.0).** The Browser card has a **Set up my browser** button
that opens a guided window and walks the four steps in order, moving itself along
as each one lands: find the add-on folder on this machine (it names the real path
and copies it), load that folder into Chrome, press **Pair**, then allow site
access. While the window is open Jarvis opens the site-access page for you instead
of making you find it.

Two of those steps are yours and cannot be automated, and the guide says so rather
than leaving you wondering. Chrome will not let an app install an add-on into your
browser, so **Developer mode** and **Load unpacked** are your clicks until there is
a Web Store listing. And **Pair** is your click on purpose: the identity a browser
presents to Jarvis over a local connection is a header any program on your computer
could write, so a person has to confirm the browser knocking is really theirs. That
press is the security boundary of the whole feature, not a step waiting to be
optimised away.

The guide never changes a setting behind your back. Browser access is a control you
can see inside the window, and it is written only when you pick it.

**Turning it on.** Browser access has three settings and ships **off**:

| Setting | What Jarvis may do **in this version** |
|---|---|
| **Off** | Nothing. No browser tool exists. |
| **Read only** | Look, but never touch: list your open tabs, name the tab you are looking at, read the text of a page, and take a screenshot of it. |
| **Interactive** | Everything **Read only** does, and the ability to act on the page. Also act: click, type, scroll, navigate — press a key, and activate, open or close a tab. Every one of those asks you first (below). |

Off is the default because reading a logged-in browser is a real permission, not a
convenience, and this app does not grant itself those.

**What gets asked before an action.** Two layers, and the second is the one that
matters when you get comfortable:

- **Everything that acts asks, by default.** All eight acting abilities ship set to
  **ask**, so the first time Jarvis wants to click, type, press a key, scroll,
  navigate, or open, switch or close a tab, it stops and shows you an approval card
  naming the tab, the control and what it is about to do. Nothing is sent to your
  browser until you say yes.
- **The four that change a page can never be turned all the way off.** Clicking,
  typing, pressing a key and navigating sit on the **deny floor**: setting them to
  *allow* on the Permissions screen is dropped rather than obeyed. And even after you
  have said "allow for this conversation", a target that reads as **destructive or
  transactional** — *Delete account*, *Submit payment*, *Confirm transfer* — asks
  again, every time. So does typing into a field the page itself marks as a password
  or a payment detail, and so does a target Jarvis **could not identify at all**: if
  it cannot read what it is about to act on, it stops and waits for you rather than
  guessing.

What Jarvis types is never written down. The typed text is replaced with
`***REDACTED***` in the record before the record is written, always — not only for
password boxes — so it is not in the ledger, in an export, or in a backup.

**Downloads, and the one thing Jarvis cannot do.** If something you click starts a
download, Chrome handles it exactly as it always does and the file lands where your
own Chrome settings say — normally your **Downloads** folder. Jarvis is told when the
download finishes and the file's real path is recorded, so you can then say "read that
statement" or "put it in this project": it can read a completed download and copy it
into a project. What it **cannot** do is change where Chrome saves things, or quietly
redirect a download somewhere else. Moving the file is a copy you asked for, not a
setting Jarvis touched.

**Letting a coding harness use it.** A coding CLI you launch inside a **Build** pane
— Claude Code, Codex or Pi — can drive this same browser, through Jarvis, with the
same gates. Three steps, and none of them are automatic:

1. In **Build**, open the pane's **Capabilities** list and tick **Browser**. A pane
   starts with nothing ticked, and a pane that grants nothing is handed no
   credential at all.
2. Launch the CLI from that pane's **Launch** menu. Jarvis reads what the installed
   build of that CLI actually advertises, writes the configuration that points it at
   Jarvis, and hands the pane a credential of its own. The menu says which method it
   configured, and says plainly when a build cannot be pointed at Jarvis at all —
   then the pane launches exactly as it does today, with no Jarvis capabilities.
3. Ask the harness about your browser. It sees the same tools your chat sees, and
   every call it makes is asked about, gated and recorded the same way — the same
   deny floor, the same approval cards, the same Activity ledger, tagged with the
   pane it came from.

**That credential belongs to the pane, and to nothing else.** It is not the app's
access token and it cannot be used for anything else. It lives in memory only, so
closing the pane, or restarting Iron Jarvis, ends it — if a harness starts reporting
that Jarvis refuses it, relaunch it from the Build pane rather than looking for a
setting. Untick **Browser** and the harness's very next call is refused, without
anything having to be restarted. Jarvis's configuration file is written into the
pane's own folder and holds **no** credential — only a reference to one — so it is
safe if you commit it. If you already had a `.mcp.json` of your own there, your servers
are kept and Jarvis adds itself alongside them; you can delete the `iron-jarvis` entry
whenever you like, and the next launch writes it back.

**What the Capabilities boxes do, and what they do not.** The list has five boxes —
Files, Shell, Browser, Extensions, Memory — and in this version **only Browser is
enforced**. The other four are recorded and shown so you can see what a pane is meant
to be for, but nothing gates on them yet: a harness in that pane still has whatever
file and shell access its own CLI came with. Enforcing the other four is Phase 2
work, and this line will change when it lands rather than before. One more honest
edge, from the CLIs' side: some builds cannot be told to switch off their *own* web
tools, and where Jarvis cannot verify that it says so in the Launch menu instead of
implying an isolation it did not get.

**Where the add-on comes from.** The installer ships it **inside the app**, so
there is nothing to download and no repository to clone: the Browser card names
the add-on folder, and Chrome's **Load unpacked** takes it from there. Chrome's
picker wants a *directory*, so you need where the folder is as well as what it is
called — on a default Windows install that is
`%LOCALAPPDATA%\Programs\Iron Jarvis\resources\browser-addon`, and if you chose your own
install location it is `resources\browser-addon` underneath it. `docs/BROWSER.md`
walks the whole thing. (Running from a source checkout instead? The card says so
and gives the one build command first — the built files are deliberately not
committed, and Chrome refuses the folder without them.)

**What reading a page gives it, and what it does not.** Jarvis asks the page for a
structured summary, not its HTML: the visible text, the headings, the links, and the
things you could interact with, each with a short-lived id. It is **bounded** — a very
long page is cut, and when that happens Jarvis is told what was cut rather than being
handed a short page that looks complete. It covers the **tab you are looking at**, and
only its top document: nothing inside an `<iframe>` is read — not from another
site, and not from this one either — so an embedded viewer, a payment box or a
chat widget is absent from the reading rather than reported as empty. An open
shadow root is walked; a closed one cannot even be counted. Ask for the same page twice and the
ids change, because they describe that one reading and not the page forever.

**Pairing, once.** Open the Browser page, set access, then load the add-on from the
folder the card names. The card will say **Waiting to pair**; press **Pair** and it
becomes **Connected**. Pairing mints a credential that belongs only to that browser
and only to the browser socket — it is not the app's access token, and it cannot be
used for anything else. **Disconnect** ends the session and keeps the pairing;
**Forget browser** revokes it, so the next connection starts over.

**Site access.** Chrome will not let Jarvis ask for access to your sites on your
behalf; the request has to come from a button inside the add-on. So the card sends
you to a single-purpose page with one button on it. Grant once and normal use stops
prompting. You can narrow it later in Chrome's own extension controls, and Jarvis
will tell you when a page is out of reach rather than failing quietly.

**What it will never do.** All three are live as of this version, and each says so,
so you never have to guess whether a promise is in force or merely planned:

- **Page content is untrusted data, always** (live): a page that contains instructions
  does not get to give Jarvis orders, and a page that looks like it is trying to gets
  flagged in the result, with the rest of the page still handed over as data. A
  suspicious page does not silently end what you asked for.
- **Passwords are never read** (live): a password field arrives with its value
  stripped, and in fact **no** field's value is ever collected — not just password
  ones. The stripping happens inside the page, before anything is sent, so a password
  never reaches Iron Jarvis at all. Your browser's own password manager keeps working,
  because Jarvis never sees what it fills.
- **Changing a page asks first** (live): a click, typing, a key press or a
  navigation is gated the same way computer use is gated. Every one of them asks
  before it runs at all, and the four that change a page ask AGAIN — whatever you
  have already allowed — when the target looks destructive or transactional, when the
  page marks the field sensitive, or when the control cannot be identified well enough
  to judge. Typed text is redacted in the record, always.

**Test** on the card does a harmless round trip and reports what came back, which is
the fastest way to tell a browser that is not running from an add-on that is not
loaded.

**Supported browsers.** Chrome and Edge, **version 120 or newer** — the floor the
add-on's own Manifest V3 manifest declares, and an older Chrome refuses to load it.
Other Chromium browsers at 120 or above will most likely work; those two are what it
is tested against. Firefox and Safari are not supported.

**The security model, in five sentences.** There are five separate credentials in this
app and none of them substitutes for another: the app's own access token opens the
app's routes, the pairing credential opens the browser socket and nothing else, a
pane's credential opens the harness door and nothing else, provider secrets stay in
the encrypted vault, and whatever your own MCP servers authenticate with stays in
their own configuration, which Jarvis never borrows. Exactly one paired browser is connected at a time — a second one
takes over, the first is told why, and anything in flight on it fails with a reason
instead of hanging. The daemon listens on this computer's loopback address only, and
the browser socket admits the add-on's own fixed identity and refuses everything else.
No field's value is ever collected — the stripping happens inside the page, so a
password never reaches Iron Jarvis at all — and what Jarvis types is redacted before
the record is written. Everything a browser tool does is on the Activity ledger, with
no credential ever written down.

**What this first version cannot do**, stated here rather than discovered later. One
browser at a time, and a newer connection replaces the older. Chrome and Edge only,
version 120 or newer. Loaded unpacked, so Chrome may prompt about it at each start until a Web Store listing
exists. Site access is granted all-or-nothing from the add-on's own page and narrowed
afterwards in Chrome's controls, never per-site from Jarvis. Downloads land where
Chrome puts them — Jarvis learns the path and copies the file into a project, and
writing files stays inside the workspace, so that copy goes through the app's
save-a-copy route rather than renaming a file that lives outside it. No clicking by
coordinates: a control drawn on a canvas with no readable name cannot be clicked. No
file upload, no special handling for drop-down `select` menus, no hover, no drag and
drop. Nothing inside an embedded frame is read at all — this site's or another
site's — while an open shadow DOM root is walked and a closed one cannot be counted.
Element ids belong to one reading and are
refused once the page has moved on. Screenshots capture the visible part of the tab,
not a whole long page stitched together. A flagged page constrains what happens next
but does not end your turn. A pane's credential dies with the daemon, so relaunch the
harness after a restart. Only the Browser capability is enforced per pane. Keeping a
harness away from its *own* web tools is best effort and is reported honestly. There
is no Jarvis browser yet. And Chrome unloads a browser add-on that has gone quiet, so
a long-idle bridge reads as **Paired — not running** with Chrome open in front of
you; it is not broken and needs no re-pairing, and it reconnects by itself the moment
anything wakes the add-on — opening its popup, switching tabs, or restarting the
browser.

**The whole guide** is `docs/BROWSER.md` — the same ground at length. It is bundled
into the app for the Guide rather than dropped in a folder you can open, so the way to
read it in an installed copy is to ask the Guide. What comes next is the Jarvis-owned
browser and its per-project profiles, file upload, hover and drag and drop, reading
inside frames, richer extraction, and watching a page so a change can wake a rule;
after that, remembering and searching the pages you visited — and every one of those
last items is off by default and will ask for its own separate consent, because an
index of what you read records which client you were working on and when. (The
maintainers' full list is `docs/TODO.md` in the source repository, which is not part of
an installed copy.)

---

## The trust model (why you can rely on it)

1. **Ledger truth.** Every tool call is recorded; session results and file
   claims are derived from the ledger, and a reply that claims a file it
   never wrote is called out under the reply itself. Since v1.228.0 a tool
   that was interrupted by a disconnect is still on the ledger: close the
   window while a tool is mid-flight and its row reads "client disconnected
   while the tool was running — its effect may have landed", so the ledger
   never claims nothing ran (a call to a tool that does not exist is recorded
   too). A tool call inside an agent run also has a deadline (Settings →
   Automation, "Tool call deadline", default 600 s): a wedged tool is stopped,
   recorded as "did not finish within N s — it was stopped", and the run
   continues; a shell command that overruns its timeout is killed together
   with everything it started; and a model that streams more than 200,000
   characters in one step is cut off, with the reason on the run.
2. **Real undo.** Reversible mutations capture the prior bytes *before* the
   write; undo restores or removes exactly what changed, refuses when the
   target changed since, and **never fabricates an inverse** — if a capture
   failed, the action is honestly not undoable.
3. **No silent substitution.** A dead provider refuses by name. Mock output
   only ever appears on a fresh install that hasn't connected anything.
   Since v1.228.0 that covers a local model that *answered* with an error
   too (429, 500, "model not found"): under `local_primary_policy = refuse`
   — the default, on Settings → Models as "If my local model answers with an
   error" — the turn fails by name and nothing stands in, so a chat never
   leaves this machine unless you switch it to `failover`. A dead local
   endpoint is caught by a ~2 s liveness check before the turn starts
   instead of after three 60 s timeouts. When a failover *does* happen, the
   receipt under the reply names what failed and why ("answered by
   claude-cli — fleet-rtx6000ada returned HTTP 500"), and the phone/desktop
   alert carries the same reason. Auto is the one route that may substitute.
   Since v1.232.0 a provider that has failed repeatedly is put in a short
   **cooldown** and the next turn is refused *without* being sent — "fleet-
   custom is in cooldown, retry in 23 s" — and the composer says the same
   thing above the box before you type, so you never wait out a timeout the
   app already knew about. And a model that dies *mid-answer* is now recorded
   like any other failure ("the connection to fleet-custom dropped mid-answer,
   so the reply above is incomplete"), instead of a blank error line under
   half a reply.
4. **Fenced untrusted content.** Web pages, file text, MCP results, and
   agent replies are injection-fenced before a model sees them.
5. **Confined writes.** File tools and the REPL write inside the workspace
   (your project's folder when grounded); reads are policy-gated; protected
   paths (the vault, the DB) are refused both ways. A workspace is probed
   for writability before it is accepted (`C:\Users`, `C:\`, a read-only
   share are refused up front), and agents are told which OS they are on
   (Windows: cmd.exe, no POSIX `mv`/`ls`/`cp`) before they author a custom
   tool or a shell command. Shell commands are the honest exception: without
   Docker they run on the native runtime, where those limits are advisory
   rather than enforced. Since v1.232.0 that is visible instead of buried in
   one tool result — the session page wears an amber **"Shell ran unconfined
   (Docker unavailable)"** chip whenever any command in that run took the
   native path, and the confinement is on the ledger row itself.
6. **One event loop, never blocked.** Heavy work runs off-thread — a big
   render or a cold OneDrive folder can't freeze the app. Since v1.226.0 that
   includes notifications going out, project-knowledge lookups, and every
   run-record write, so a slow Slack or a busy database never shows up as
   "Daemon offline".
7. **Self-healing supervision (v1.226.0).** The desktop app watches the
   daemon's health every 30 s, not just its process: a daemon that is alive but
   wedged is restarted, a crash-restart gets the full boot grace, and a daemon
   left over from a hard crash is adopted (after proving it is *this*
   install's, by token) instead of fought over. A lost secrets key, a
   hand-edited `[comm]` section, or a slow notifier can no longer stop the
   daemon from booting; `/diagnostics` → `background_loops` now reports every
   background loop and the scheduler, not just backups. Since v1.229.0 that
   report is *true*, not "armed": the fleet sampler and the Slack socket say
   ok only after a cycle that ran or a connection that opened, and a loop
   that keeps failing says so with its last error — named on the Overview
   (the amber line under the hero in Simple mode; the Background loops tile
   and list under System health in Advanced) and, once it has been failing
   for five minutes, as a bell item ("Background task fleet has been failing
   for 12 min — …"). The hero never says "All systems nominal" over a dead
   loop. A tool pack (MCP server) that did not start is treated the same
   way: its row under **Tools → Connected packs** carries an amber "Didn’t
   start: <reason>" line (the exact error, e.g. `npx` not found) with a
   **Retry** that reloads it live, the Overview hero says "1 thing needs
   attention" with the pack named under it, and the doctor's `mcp` check
   names each pack that failed and a missing `npx`. **Test** on that row
   only proves the config (it loads nothing), so a green Test leaves the
   amber line and the "needs attention" note in place — press **Retry** to
   actually bring the pack's tools back. The dashboard keeps **one** live
   connection to the daemon's event feed per window (v1.230.0) — every page
   and widget shares it — and when the daemon is away it retries after
   2.5 s, then waits twice as long each time up to 30 s (slightly
   randomised, so several open windows don't all knock at once); the
   reconnect asks for what it missed, and a replayed event shows once.

## Ask the Guide

The **Guide** is a built-in agent — it sits in the roster on the Agents page
beside the builder, researcher and the rest — and it is the expert on Iron
Jarvis itself. **Talk** to it there (or use the **Ask the Guide** box at the
top of the Help page, which opens that conversation with your question ready
to send), **Give work** to it for a longer lookup, or run a session as the
*guide* agent. It starts every session knowing what the app is and how your
install is set up, and it looks the rest up with its own tools: the reference
(this Handbook, the other guides, the vocabulary and product reference, and
live catalogs of your install — version, connected models, tools, skills, and
every API route) and your own things inside the app (projects, saved
workflows, schedules, reflex rules, goals, skills, agents, threads, sessions,
memory bases), each answer naming the page that opens it. It cites the section
it drew on, and when neither the reference nor the app holds an answer it
says so and points you to the nearest place to look instead of guessing. It
reads; it never writes, runs commands, or starts work on its own.

## Troubleshooting in one minute

- **"Claude Code isn't signed in" / "Codex isn't signed in"** (v1.234.0) →
  the subscription CLI is installed but logged out, so Iron Jarvis refuses
  the turn instead of failing over. Open a terminal, run `claude` and then
  `/login` (or `codex login`), then **Re-detect** or **Test** on
  Connections. The Connections row reads **Installed — not signed in**
  until then, and the composer says so before you type. Installed is not
  connected: the app asks the CLI itself (`claude auth status`, `codex
  login status`) and re-checks every couple of minutes.
- **"Daemon offline"** → the desktop app restarts a crashed daemon by
  itself, and the banner needs two missed polls before it shows (one slow
  request never shows it; a search you superseded by typing is not counted
  as a miss). If it stays: quit from the tray and relaunch (or tray →
  **Restart Iron Jarvis**), then **Settings → Maintenance → Copy
  diagnostics** / **Open logs folder** to see why. The Help page's "If
  something looks wrong" card says the same. It is also, often, a
  provider/endpoint issue rather than the daemon: check Connections. In the
  desktop app the banner says the service is restarting; pages reload
  themselves the moment it is back (v1.226.0) — no need to navigate away.
  The desktop app restarts a crashed daemon or
  dashboard by itself with backoff (1 s → 60 s). It does not do so forever
  (v1.229.0): three deaths within seconds of a start make it verify the
  install first (a damaged install gets the Repair dialog), and after 10
  restarts in 15 minutes it stops, tells you, and the tray gains a
  **Restart Iron Jarvis** item that clears the counters and starts both
  processes again. A process that crashes only every few minutes is
  restarted every time, and you are told once it has died 3 times in a day.
  The tray tooltip reads "running" again as soon as the process is back.
  The per-process logs (tray → Open logs folder) stamp every line with a
  time and say `killed pid=… reason=quit|update|watchdog|restart` when the
  app itself stopped a process, so a crash and a kill no longer look alike.
- **"Tool pack didn't start: <name> — launcher 'npx' was not found"** (v1.233.0)
  → the pack's launcher (`npx` for Node packs, `uvx` for Python packs) is not
  installed or not where the app looks (PATH, then the usual per-user Node and
  uv folders). Install Node.js LTS or uv, restart Iron Jarvis, then press
  **Retry** on the pack under Tools. A pack whose launcher IS installed now
  starts from the packaged app too — before v1.233.0 the bare name never
  resolved on Windows even when it was on PATH.
- **Amber text or boxes unreadable on a light Mark** → fixed in v1.233.0; the
  light Marks remap every amber tint to deep amber ink. If a new surface
  shows pale yellow on white, it is a missing rule in the light-amber block
  of `globals.css` (a test names the class).
- **"Couldn't save this conversation"** (a chip above the composer, v1.226.0)
  → the thread could not be written. **Retry** re-sends what is on screen
  *now* (reply included) and the chip stays up, reading "Retrying…", until
  that save actually lands — an older save finishing in the meantime does
  not retire it (v1.232.0). If the thread was deleted elsewhere the next
  save quietly re-creates it. Your message is saved *before* the model is
  asked, so a reload mid-answer keeps the question, and an agent hand-off
  resumes when you reopen the thread. Two windows on the same thread (the
  app and a browser tab) no longer overwrite each other: a save from a stale
  copy is refused, the window reloads the thread and appends only its own
  new messages (v1.232.0). A reopened thread that ends on your question with
  no reply (the daemon restarted mid-answer) says **This didn't get a reply**
  with a **Retry** that re-sends it — chat threads only: a messaging thread
  (Telegram/Slack/email) ends on your phone's message while the daemon is
  composing, and the reply lands on its own; a reopened thread whose agent run is
  paused on a permission shows the approval card within a couple of seconds
  even before any live event arrives. If the daemon log says *"SQLite writer
  stuck past busy_timeout"*, one write held the database for over 30 s — the
  callers behind it report "database is locked" (never a pool limit) and the
  line is logged once; a restart clears it.
- **A terminal says "Connection lost"** → the daemon restarted; it keeps
  retrying while the daemon is down, or press **Reconnect** — the pane and its
  scrollback come back under the same id.
- **"Restart to update" asks before installing** (v1.226.0) → agent sessions
  or workflow runs are still running; **Later** leaves them alone, **Install
  now** marks them interrupted.
- **A model "isn't answering"** → read the TurnReceipt: it names what ran and
  why. A red PreflightNote above the composer means your pick is unreachable
  *before* you type.
- **An update seems stuck** → Updates page; a release can take ~10 min to
  finish uploading after the version bumps.
- **A message says "internal error [err_xxxxxxxx]: …"** (v1.229.0) → quote
  the `err_` id from the message. The same id sits on the traceback line in
  the daemon log (the desktop app's `logs` folder, `daemon.log`), so whoever
  looks can jump straight to the cause. Every line in that log now carries a
  timestamp and the logger name, and the routine noise — the dashboard's
  green polls and preflights, the Windows "connection reset on close"
  traceback — is filtered out, so what remains is what happened.
- **You need to show someone what is wrong, or go back to yesterday**
  (v1.229.0) → **Settings → Maintenance**. *Copy diagnostics* puts one JSON
  blob on the clipboard — version, health (`db_liveness` is the "does the
  database answer" probe; `db_integrity` is the same value kept for older
  readers), background loops, disk, provider failures and the last 50
  warnings/errors the daemon logged (`GET /diagnostics/errors`); an endpoint
  that failed appears as `{ "error": … }` rather than vanishing, and when no
  clipboard is reachable the text is shown to select by hand. *Open logs
  folder* (desktop app only; also in the tray menu) opens the folder holding
  `daemon.log`, `dashboard.log` and `desktop.log`. *Restore from backup…*
  lists the snapshots under `backups/`, names the file in the confirmation,
  replaces the database and settings with it and restarts the daemon — it is
  refused while an agent session or workflow run is in flight. Backups also
  no longer pile up on restarts: the boot snapshot is skipped while the
  newest one is younger than the auto-backup interval (24 h by default).
- **Something wrote the wrong thing** → Activity page (or the file's row in
  chat) → Undo. Session-level revert exists for whole runs.

## What changed in the audit waves (v1.227.0 → v1.232.0)

A six-wave audit of the whole app, one version per wave, fixed what it found.
**v1.227.0 (Wave 1, the roster is the contract):** an agent or a chat can only
call the tools it was armed with — any other call is refused and ledgered as
"not armed"; a run parked on an ask really waits, batches of asks show one
card each, and every finished run carries an honest outcome (*completed*,
*with failures*, *needs you*) beside its status. **v1.228.0 (Wave 2, honest
failures):** a tool interrupted by a closed window is still on the ledger,
a wedged tool call in a run hits a deadline and the run continues, a local
model that *answered* with an error refuses by name instead of failing over
to a cloud model (`local_primary_policy`, default *refuse*), a dead local
endpoint is caught in ~2 s, malformed tool arguments are corrected rather
than crashed, and a folder is only accepted as a workspace once a file
can actually land in it. **v1.229.0 (Wave 3, the truth about background
work):** a background loop reports its own cycles so a failing sampler or
socket can never read "ok", a tool pack that did not start says so with a
Retry, `internal error [err_…]` ids lead straight to the log line, Settings
→ Maintenance gained Copy diagnostics / Open logs / Restore from backup, the
desktop app stops restarting a daemon that keeps dying and tells you, and
the hotkey shown everywhere is the one the OS actually granted. **v1.230.0
(Wave 4, a lighter, truer dashboard):** one event socket per window,
polling that pauses while the window is hidden, no duplicate preflight
requests, the offline banner only after two misses, Connections and the
model switcher telling one truth about inherited CLI logins, and session
summaries rendered as markdown. **v1.231.0 (Wave 5, automation you can
trust):** every door (schedule, reflex, goal, phone, autonomy) runs in the
project's folder and may ask you; a crashing workflow step is a failed step
and finished steps survive a restart; a schedule fire that was missed or
skipped is written on its row; a revoked phone token is red on the Channels
row; a dropped message says so. **v1.232.0 (Wave 6, copy and states):**
the words on every surface match what the app does — chat hints, session
labels, usage without noise rows, Settings' token box inside the desktop
app, the Documents page pointing at Chat for convert/split/merge/batch, the
Memory page linking to imports, a terminal resized only by the window you
are looking at, and this Handbook, the Help page and the README kept in
step (a test now fails if this file's "Current as of" line lags the app).
