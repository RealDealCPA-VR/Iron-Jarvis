# Iron Jarvis — The Handbook

*The user guide. What this app is, how to work it daily, and the rules it
holds itself to. Current as of v1.324.0 (2026-10-04).*

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
**Restart to update**. Since v1.260.0 the check reads a small manifest file the
release process publishes (a plain, fast download) and only falls back to
GitHub's release feed if that file is missing — the feed is the page that made
update checks fail with a "504" on 2026-09-14. If GitHub still does not answer,
the Updates page says so in one line and the app tries again five minutes
later; nothing is wrong with your install, and the direct installer link on the
release page always works. While the window is hidden or minimised the dashboard
stops polling the daemon (the daemon check itself slows to every 30 s) and
refreshes everything the moment you bring it back.

---

## Getting started — your first five minutes

*(v1.321.0, the calm layout)* The aim of the first five minutes is one real
answer to one real question. There is no setup screen: Iron Jarvis opens on a
new chat with the box ready to type in.

**1. Ask something.** The home **is** a chat. Type a question and press
Enter. Until you choose a model, replies come from a built-in offline sample —
a **scripted demo**, not real answers (each such reply is marked in amber, and
an amber strip at the top says so).

**2. Choose which model answers — one press, always yours.** If this PC
already has a real model — you are signed in to Claude Code or Codex, or
Ollama is running — the empty chat shows a **Connect a model for real
answers** card with one button named for it (for example **Use Claude (your
Claude Code sign-in) for answers**), and once you have started chatting the
same offer sits beside the composer as a single **Use … for answers · one
tap**. Under each choice the app says where your questions would go — "stays
on this PC" or "goes to Anthropic" — before you press anything. Nothing
switches on its own: whether your questions go to a cloud service or stay on
this PC is your decision. No model at all? The card's three doors — **I
already pay for Claude or ChatGPT**, **Free & private on this PC**, **I have
an API key** — walk you through it.

**3. Pick a model any time.** The model is chosen in the composer (the small
model name under the box). Its menu lists every model; type to find one.
**Make this my default** saves the conversation's pick as the default — with
an Undo, like every setting. "Switch model" in Ctrl+K opens the same menu from
any page.

**4. Everything else is one or two clicks away.** The sidebar holds **New
chat**, four places — **Build**, **Projects**, **Everything**, **Settings** —
your recent chats and your projects. **Everything** lists every other part of
the app with one line each; **Ctrl+K** finds any page, any setting, any chat.

**5. The checklist and status.** *Connect your AI*, *Give it your first task*,
*Work with a document* and *Teach it your style* live in **Everything →
Status**, with what is running and how the app is doing. While setup is
unfinished, the top of Status holds that checklist, an **Ask Jarvis anything**
box (it opens a chat with your question written in — nothing runs until you
press send) and a few starters. Some features need a helper program — reading
old `.doc` files or scanned pages, and similar; a missing one is folded into a
single quiet **Optional extras (n)** line there, in plain words. When
something needs you — work cut off by a restart, a background task failing, a
job waiting for an answer — one quiet line appears above the chat box, with
**Open**.

---

## The calm layout (v1.321.0)

Iron Jarvis now looks like the chat apps you already know: the conversation
is the product and everything else is found when you need it. Nothing was
taken away — every feature moved to a place you can reach in two clicks or by
searching.

- **The sidebar.** New chat; **Build** (the terminals), **Projects**,
  **Everything**, **Settings**; then **Chats** (by day — on the chat screen
  this is the chat's own list, with search, rename, pin, move and delete) and
  **Projects**. At the bottom: whether Iron Jarvis is running and its version
  (press it for Status), and **Help** (the guides, *Ask the Guide*, and
  what's new). Collapse it to icons with the arrows at the top. On a phone it
  is the ☰ menu.
- **Pins.** On **Everything**, press the pin beside any entry to put it
  under the four places in the sidebar (up to three).
- **Everything.** Four columns — **Work** (Agents, Creative, Templates,
  Documents, File Search), **Automations** (Workflows, Schedules, Reflexes,
  Sentinels, Autonomy, Webhooks), **Knowledge** (Memory, Skills, Artifacts,
  You, Train), **System** (Sessions, Activity, Usage, Fleet, Self-development,
  Updates, Help) — plus **Setup**, and a **Status** tab: what was the Overview
  (running work, health, failing background tasks, recent events, the
  getting-started checklist).
- **Settings, in seven groups.** **Models**, **Connections**, **Agents &
  automation**, **Memory & you**, **Permissions & ledger**, **Appearance**,
  **System**. Search finds any setting by what you would call it. Pages that
  used to stand alone are sections now: Connections, the Directory of apps,
  Notifications, Browser and Secrets under **Connections**; your profile and
  Train under **Memory & you**; Tools and standing grants under
  **Permissions & ledger**; Updates and maintenance under **System**. Old
  links still work — they open the right section. Save shows how many changes
  you are saving and offers **Undo**.
- **Changed here or in chat.** Under **Permissions & ledger**, every change
  to a setting, a schedule, a workflow, a channel, an app or a saved key —
  made on a page or in chat — is listed with where it was made and its
  **Undo**.
- **The Session board** is the **Board** view of Sessions.

### Change settings by asking

Anything on the Settings page can be changed in chat, in your own words —
"turn on dry run for autonomy", "use the local Qwen model for coding tasks",
"run this workflow every weekday at 7am", "turn on Telegram notifications",
"connect my Notion", "undo that".

- **Every change is shown and can be undone.** The reply carries a card —
  **Setting changed: old → new [Undo]** (or *Schedule changed*, *Workflow
  removed*, …). Undo puts it back exactly.
- **What needs your OK still asks.** Safe preferences (the theme, the default
  model) change at once. Bigger ones ask on the usual approval card first.
  Protected ones — autonomy, the sandbox, tool permissions, turning a safety
  off — ask **every time**, even in Auto-approve, and are never offered
  "Always".
- **Keys and tokens never go in the chat.** When something needs a key or a
  token, the reply shows a **secure card**: paste it there and it goes
  straight into the encrypted vault — the model never sees it, and it is not
  written to the conversation, the history or any log. If you paste something
  that looks like a key into the chat box, Iron Jarvis holds the message and
  offers to **Save it securely** instead (or **Send anyway**).

## The surfaces, in the order you'll use them

### Chat — the main surface
Chat is where most work happens, and it is wired into everything:

- **Attachments**: drag-drop or the "+" menu (up to 4 files, 20 MB each).
  Images go to vision; documents are inlined or retrieved via RAG with page
  references, sized to the answering model's context window.
- **Scans are read on this PC first** (v1.252.0): drop in a photographed or
  scanned page — a W-2, a signed letter, a receipt — and this computer reads
  the words itself, offline. No cloud call, and it works with no vision model
  connected at all, which is the case where a scan used to come back as
  nothing. A cloud vision model is asked only for the pages this PC could not
  make out, so a scan of client papers does not leave the machine just to be
  read. The answer tells you which pages were read here, and flags anything
  mostly made of figures as worth your eye — machine reading can misplace a
  digit, and on a tax form one digit is the whole point. You can switch it off
  in Settings if you would rather every page went to a vision model.
- **Attach and ask, no project needed** (v1.244.0): the first file you attach
  to a chat that has no project gives that conversation its own folder —
  `Documents\Iron Jarvis\<date> <file name>` — with your file copied in, and
  arms the same file tools a project does (find, read, create a document,
  write a file). The chat then does the job itself and saves what it makes
  next to your file; the folder shows as a chip above the message box with an
  **Open** button. Later attachments in the same chat join the same folder.
  If the chat was already pointed at a folder the app can save in, that
  folder is used instead; if it was pointed at one it cannot (such as
  `C:\Users`), the chip says so. Change the parent folder with the
  `chat_files_root` setting.
- **A turn that is working says so** (v1.246.0): while a reply is on its
  way, the bubble names what it is waiting on — "Reading your files…" while
  your attachments are read, then "Thinking…" — with a clock once it passes a
  few seconds, and "Still working · 40s" under text that has gone quiet (a
  tool running, the model thinking between steps). The daemon sends a
  keepalive every 10 seconds, so if nothing at all arrives for a minute the
  turn is stopped with a sentence saying so and a Retry, instead of spinning
  forever. A tool call in chat has the same time limit as in an agent run
  (`tool_call_timeout_s`), and a model that finishes its work without writing
  an answer is asked once for it — you get the answer, not a raw tool dump or
  an empty bubble.
- **Approvals wait for you, and a batch is one click** (v1.247.0): when a
  job you started (from chat, the Agents page, a project, or by hand) needs
  your permission, it waits until you answer — it no longer gives up after
  5 minutes and records the step as not done. Scheduled and other automatic
  jobs still stop after 5 minutes, because nobody may be there to answer.
  When one step wants to do the same kind of thing several times — renaming
  8 files, say — you get one card ("× 8") with a few examples: **Allow these
  8** runs exactly those, **Deny all 8** refuses them. Chat also has room to
  finish document and spreadsheet work itself (12 steps instead of 6) and,
  if it still runs out, tells you what it did and what is left instead of
  handing the job to a background agent.
- **The app opens sooner, and says where its start-up time goes**
  (v1.251.0): Iron Jarvis now checks the two things it waits for at the same
  time instead of one after the other, and looks up your local model list
  while it starts rather than on the first check afterwards — about half a
  second off a cold start, measured on a real installed build. It also writes
  one line into its log each time it starts, naming the slowest steps and
  their seconds, and shows the same breakdown on the Diagnostics page. So if
  your start is slower than that, the app can now tell us exactly which part
  is taking the time instead of us guessing. Step names and numbers only —
  nothing about your files or accounts.
- **Chat stays quick however long it gets** (v1.250.0): typing no longer
  slows down as a conversation grows, long replies stream smoothly to the end,
  and you can scroll or type while one arrives. Going back to a page you were
  just on shows what you last saw instead of a grey placeholder, while it
  quietly checks for anything new. Minimised windows stop checking altogether
  until you return, and every screen loads a little less to get started.
- **Files rail**: every file a conversation *makes or was given* appears on
  the right — preview (spreadsheets as sheets, PDFs and images inline, docx as
  a Word-faithful page), download, open in the native app, save a copy, and a
  **Changes** toggle that diffs a file you re-preview. Truncated previews say
  exactly how much is missing.
- **A follow-up remembers your files** (v1.255.0): attach a return, ask for a
  summary, then say "now turn that into a memo" — and it knows which file you
  mean. Before this, the second message arrived carrying no file at all, so the
  chat either asked you which one or answered about nothing in particular.
  Every file this conversation has been given or has made — the ones on the
  Files rail — is named for each new turn, with its full location, so "it",
  "that return", or the file's own name all point somewhere real, and the tools
  that can change a document are ready when you ask for a change. Their
  contents are not read again: they are already earlier in the conversation, so
  you are not charged twice for the same pages. The newest eight are carried,
  and a file you are attaching right now is not counted a second time.
- **A folder of documents becomes one summary sheet** (v1.255.0): point a chat
  at a folder — 30 organizers, a year of statements — and a card appears above
  the message box saying how many documents it found and what the run will
  cost, **before** you press anything. One press reads them all and writes a
  single sheet summarising each one, counting up as it goes ("12 of 30"), so a
  long run is never a silent wait you have to guess about. The sheet lands in
  this conversation's own folder and shows up on the Files rail with
  everything else.
- **TurnReceipt**: under each reply — which provider/model actually answered
  and *why* (your pick, the default, a failover), plus the files that turn
  really wrote per the ledger. A mock or failover answer is flagged in amber.
- **Draft cards**: when the model drafts an email, it arrives as a card whose
  **Copy** writes rich text that survives pasting into Outlook (bold, lists,
  links — spacing included, because Outlook renders through Word).
- **Mail it without retyping it** (v1.253.0): the same draft card now has
  **Save to Drafts** and **Send**. Either one opens a confirm box first, where
  you check the To/Cc lines the model wrote, edit the subject, and tick any of
  this conversation's files to attach — so the letter and the spreadsheet the
  chat just made go out together. **Save to Drafts** puts it in your own
  mailbox's Drafts folder and sends nothing, which is the default on purpose:
  you open it in Outlook, read it once more, and press send yourself. Nothing
  reaches the mail server until you press the button in that box — the model
  cannot do this on its own. If the server accepts some recipients and refuses
  others, the card says which were refused instead of just "Sent". Needs your
  email account in Channels (IMAP to save drafts, SMTP to send).
- **"/" skills** anywhere in a message invoke a skill; **@mentions** pull
  agents into a panel bound to the thread — the agent is shown the chat so
  far, the conversation then stays with it (a strip above the box says
  **Talking to builder**; **Back to Jarvis** ends it), and an ask for WORK
  ("write me a PDF …", a file attached, or **Have builder do this**) runs as a
  real session of that agent with its tools and brings the file back into the
  chat (v1.284.0); **escalation** hands a chat request to a real background
  agent session when it outgrows a chat turn.
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
  **Default: <model name>** (since v1.314.0 — "Default: Demo model
  (scripted)" while replies are still the scripted demo, with the raw
  provider · model id on hover) instead of "default model"; and a receipt line that used to say "3 tools max" now
  says **capped at 3 tools for this local model** — the envelope fitting your
  own measured model, not a limit you set.
- **What an escalated run inherits from the chat** (v1.232.0): the armed
  tools (on every message, not just the first), a grant you answered on a
  card, the grounded folder, and the approval posture — except *Auto-approve
  (YOLO)*, which escalates as *Approve for me*. See *Agents & jobs* for the
  rules, and the session's amber chips ("Waiting for you · shell",
  "Completed · needs you") now keep their own case.

### Agents & jobs
**One objective, the whole team (v1.307.0; the only way since v1.308.0).**
The Agents page opens on **New task**: type one objective ("research our three
competitors and write a strategy report"), optionally pick a project, and press
Start. You don't pick agents — Jarvis splits the work and hands each part to the
right teammate. The screen has three parts. In the **centre** is your objective
and, under it, the **result** as it is written — **Report** (rendered),
**Markdown** (the same text, raw, to copy) and **Preview** (the files the team
created, opened in the document viewer). While the team works, the centre shows
the draft being written right now and says whose it is; when the team finishes,
it shows Jarvis's finished answer, which is the work product itself rather than
a summary of who did what. On the **right**, one compact card per teammate shows
its role, what it was handed, and its progress. Click a card for detail: its
latest action, what it reported, its files, and a link to the full run. **A bar
shows a percentage only when something is counted** (done is 100%, not started
is 0%, a planned run counts its finished steps). A teammate working with nothing
counted shows a moving stripe and what it is doing ("Working · step 3"), never an
invented number. Below the result, **Live activity** is the team's work as a
plain-words log ("Researcher read k1.pdf", "Jarvis gave Writer a task: draft the
strategy"). If Jarvis needs your OK for a tool, the question appears under your
objective with Allow once / Allow for this task / Decline, and nothing runs until
you answer. **Stop** ends the whole objective. Your recent objectives are listed
under New task, and **Your projects** below them open a project's own screen.

**When it finishes, the bell tells you (v1.309.0).** You don't have to watch
the screen: when the team finishes, the notification bell (and a desktop
notification, if you allowed them) says **"Your objective is done"**, and
clicking it opens that objective's screen with the result. If the team could
not finish, the bell says so instead; if it finished but a question of yours
went unanswered, it says **"Your objective finished — something needs you"**,
and if part of the work failed, **"Your objective finished, but part of it
failed"** — the same verdict the objective's own screen shows. The bell says
"is done" only when that verdict is in; without it the row reads plainly
**"Your objective finished"** and the screen tells you the rest. Pressing Stop
yourself rings nothing, and the individual
teammates never ring. The bell's agent rows (an agent paused, near its
allowance, blocked, or with a coach suggestion) open **that agent** on Your
team. **Continue** on an objective a restart cut off opens the continued
objective's screen, and a project's page has a **Team & objectives** link to
its own screen.

**A project's screen (v1.308.0).** Open a project (from Your projects, or by
picking it when you start an objective) and the same screen belongs to it: its
objectives run in the project's context and folder, and **only the project's
team does the work** — the right column lists the team, and **Edit team** (or
**Pick a team**) chooses it; Jarvis suggests agents that already worked on the
project. With no team picked, Jarvis chooses from all your agents. Under the
composer: **Board** (the project's work), **Waiting on you** (questions a job is
paused on, blocked work, work cut off by a restart) and **Completed**. An agent
on another computer that is on the team receives only the task Jarvis hands it —
never the project's files or the other agents' work.

**Your team (v1.308.0).** The left rail's **Agents** row opens every agent you
have — built-in, yours and remote — each with its portrait and face, its inbox,
and (for agents you made) its folder and coach, plus **New & manage** to create
an agent or connect one running on another computer. Work is not handed out
here: you give Jarvis an objective and it picks.

**The round table is gone (v1.308.0).** Old links to a round-table conversation
open it read-only, exactly as it was. To talk with one agent, @-mention it in
chat (that works as before); the agents' replies stay in that chat — there is
no separate page to open them on (v1.309.0). "Ask the Guide" on the Help page puts your question
into chat as `@guide …`, ready to send.
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

### Sessions & the Session board
Every agent run is a **session**: live token/tool streaming, the delegation
**TeamTree**, the team's shared **blackboard**, a ledger-derived result card
(files created/changed *proven from the ledger*, never from the model's
closing paragraph), transcript export, cancel/rerun/continue. The **Session
board** (`/kanban`; called Kanban before v1.315.0) shows the same sessions as
lanes — including **Queued** when a concurrency limit is
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
  never answered). The session header, the sessions list, the Session board card and
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
  and a project's **Recent runs** rows show the summary's words, never its
  asterisks (since v1.315.0 each row leads with what you asked, then the
  summary on a quieter line, then when it ran).
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
Read/extract (PDF incl. scans — read on this PC first, a vision model only for
the pages it cannot make out — docx, xlsx, pptx, csv,
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

**New in v1.254.0 — four things you used to do by hand:**

- **Change a Word document without retyping it.** Ask for "replace the fee
  with $4,200 in this letter" and the file keeps its letterhead, fonts,
  styles, headers and footers — only the words you asked about change. Until
  now the only way to "edit" a .docx was to rebuild it from scratch, which
  came back looking like a different firm's letter. It changes a copy by
  default, and the chat's **Undo** works on it. If the text you named isn't in
  the document, nothing is changed and you are told what wasn't found — it
  never half-edits a letter.
- **Fill in a PDF form, and know it took.** Hand over a fillable PDF and the
  app lists its fields, fills the ones you name, then **re-opens the finished
  file and checks every value is really there**. If any value did not stick,
  the copy is deleted and you are told — the same "prove it, don't claim it"
  rule the redaction flow uses. So "filled" always means filled.
- **See exactly what changed between two documents.** Point at two versions of
  a letter or a workbook and get a plain list: what was added, removed and
  reworded, sheet by sheet and figure by figure for spreadsheets. No more
  reading two copies side by side hunting for the one number that moved.
- **Old .xls and .doc files open.** Files from the 1997-2003 era now read
  properly: .xls directly, and .doc through Microsoft Word if you have it
  installed (only Word converts its own old format faithfully). If Word isn't
  here, the app says so and tells you to save the file as .docx rather than
  failing halfway through a job. The Diagnostics page shows which of the two
  is available on this PC.

### Settings → System → Maintenance
- **What the app is keeping on your disk** (v1.256.0): Maintenance now lists
  everything Iron Jarvis stores here — generated pictures and video, backups,
  undo history, the scan-text cache, code workspaces and the database — largest
  first, with a total. Before this it was invisible: on the machine this shipped
  from, 783 MB of the 814 MB was old generated video that nothing ever cleared
  and no screen ever mentioned.
- **Clearing old media takes two presses, on purpose** (v1.256.0): "Clear"
  shows you how many files and how much space, then **moves** them out of the
  way rather than deleting them — they sit in the app's own trash, still
  recoverable, and the screen keeps counting them because the space isn't freed
  yet. **Delete permanently** is the separate press that actually frees it. Your
  backups, undo history, project files and code workspaces are never offered and
  never touched, however old they are.
- **A tool pack that can't start tells you why, in words** (v1.256.0): if an
  add-on pack fails, Tools now says what actually happened — "npx exists on this
  PC but not on the PATH Iron Jarvis was started with" — and what to do about
  it, instead of only showing the error the computer produced. The raw text is
  still there underneath for a bug report. On this machine one pack had been
  failing silently at every start for weeks.
- **A model you can't reach says so** (v1.256.0): if the model you picked for
  routing or as your default can't be reached, the start-up check names it and
  says whether no address is configured or the server isn't answering. Iron
  Jarvis never quietly uses a different model instead, which is exactly why it
  has to tell you the setting is doing nothing.

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
- **Preferences you approve** (v1.305.0; the idea comes from
  agent-personalizer, MIT). Every preference Jarvis keeps about how you like
  to work has a status: **Kept**, **Suggested** or **Never ask again** — and
  only Kept ones ever reach a model. Saying "from now on…" keeps it at once,
  exactly as before ("Remembered: …" under the reply). New: when you correct
  the same thing a SECOND time ("shorter, please" today, "too long" last
  week), a quiet line under the reply asks once — "You've said this twice:
  … keep it as a standing preference?" — with **Keep**, **Edit** (the
  sentence is built from your own words; change it before keeping) and
  **Not this**. Not this is final: that correction is never suggested again
  unless you press **Ask again** on the Memory page. Spotting a correction is
  a plain word check on this PC — no model reads your messages for it — and
  it never fails a reply: only a message that reads like a correction is
  looked into, and that look gives up after two seconds rather than hold the
  reply's finish any longer. A correction you scope to one case ("for this
  client", "this time") is not suggested. At most three suggestions wait at
  a time. A correction you type in a Build pane's chat counts too, but a
  Build pane never asks: the question comes in the main chat, the next time
  you say it there. Answered it already on the Memory page or in another
  window? Pressing Keep or Not this on the old line just shows what you
  decided (or quietly removes the line if you asked again or forgot it).
  The Memory page's **What Jarvis knows about you** card lists all three
  groups (edit or forget a kept one there) and, when Claude Code or Codex is
  on this PC, offers **Look through my Claude Code and Codex sessions**: it
  reads only the messages YOU typed in your newest sessions (never the
  replies or tool output), only when you press it, and nothing leaves this
  PC; it says how many sessions it read and how many suggestions it made.
- **Share my profile with Build** (v1.306.0; the idea comes from
  agent-personalizer, MIT). A Build pane runs Claude Code or Codex, which
  never see Jarvis's own prompts, so your profile stopped at the pane's edge.
  At the foot of the **What Jarvis knows about you** card, **Share with
  Build** has one switch for each of those CLIs found on this PC — "Share my
  profile with Claude Code in Build" and "… with Codex" — and both are off
  until you turn them on. When on, Jarvis keeps a marked block in that CLI's
  own instructions file: Claude Code's `CLAUDE.md` in its config folder
  (`CLAUDE_CONFIG_DIR`, else `~/.claude`), Codex's `AGENTS.md` in its home
  (`CODEX_HOME`, else `~/.codex` — if an `AGENTS.override.md` with text in it
  sits there, Codex reads that one instead, so the block goes there). While
  Iron-Proxy is on, the same file in every Iron-Proxy account folder for that
  CLI gets the block too (an account that is this PC's own login is the same
  file, written once); turning Iron-Proxy off takes the block back out of
  those folders unless you edited it there. The block holds your profile and the preferences you said or kept
  — nothing else Jarvis remembers: no task notes, feedback notes, project
  knowledge or memory. It is checked for planted instructions (a flagged
  preference is left out), anything that looks like a key is masked, a path
  on this PC is replaced with "(a path on this PC)", and it stays under
  4,000 characters. The sentence under each switch names the files and says
  that Anthropic or OpenAI sees the block when that CLI runs. Nothing outside
  the block is ever changed, and before Jarvis first writes into a file you
  already had, a copy of it goes to Iron Jarvis's trash folder. A file that
  is a link to another file is left alone (the card says so), and Jarvis
  reads the file once more just before it writes and backs off if you saved
  it in the meantime. Change your
  profile or a preference and the block follows a moment later. Edit or
  delete the block yourself and Jarvis stops writing that file; the card
  says so, with **Overwrite** (put Jarvis's block back) and **Keep yours**
  (leave your version — that file is no longer updated). Turning a switch off
  takes the block out (an edited one is copied to the trash first) and
  deletes a file Jarvis created that holds nothing else; a file it could not
  change is still named on the card after the switch is off.
- **Memory steward**: a scheduled curator that proposes memory changes for
  your approval — it never silently rewrites what the app knows about you.
- **The 3D memory graph**, lessons, and a "What I can remember" index the
  model sees each turn.
- **Two search boxes, two jobs** (v1.232.0): the **Recall** box at the top
  of /memory searches every store at once; the box under the Working tab
  (and, since v1.316.0, the one under Long-term) is titled **Search only
  this tab** with the note "Recall, above, searches everything" — it
  searches only that store. The count field beside each
  query is labelled **Results** (it was "k").
- **One search for everything you have** (v1.253.0): the command palette
  (Ctrl+K) now answers from three places at once — your **files**, your
  **memory**, and **past conversations** — so you can look for "Dewerff
  engagement letter" without first deciding which of the three it lives in.
  Each result says which place it came from, and a file result opens in the
  preview pane without leaving the palette. If one of the three takes too
  long to answer, the palette says that part is incomplete rather than
  quietly showing you a short list.

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
  history with Resume for interrupted runs. Since v1.316.0, while no
  workflow is saved, a **Describe a workflow** box and the starter buttons
  sit ABOVE the editor, so the easy way in comes first. Workflows are born three ways:
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
  was done. Since v1.323.1 those writes land in the order the run made
  them: a slow write can no longer arrive after a newer one and put an
  older list of finished steps back on the record. A later step that references a failed-and-skipped step gets
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
  logged-in CLI shows as **Connected · via Claude Code** (or Codex; before
  v1.314.0 it read "Inherited from claude-cli") — it is
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
- **Build terminals survive leaving Build** (v1.243.0): switching to another
  page no longer closes your terminals. Each pane's terminal and its link to
  the shell stay open in the background, so coming back is instant — exactly
  the screen you left, with no replay, no redraw and no stray characters.
  Flipping Rail ⇄ Canvas keeps every terminal the same way. A Claude or Codex
  you left working keeps working: a pane nobody is watching is still read, so
  the program never stalls waiting for you to come back. A reload or a daemon
  restart still replays the pane's history, and the answers the terminal
  gives to questions in that old output (a colour or device query) are no
  longer typed into your shell as stray text. Two windows on one pane (the
  desktop and a phone) now each get the whole stream instead of half of it.
- **Build after an update: Resume** (v1.245.0): an app update or a crash
  still ends whatever was running in a pane — the shell comes back fresh in
  the same folder — but a pane that was running Claude Code or Codex now
  shows a strip: "*Claude Code was running here before Iron Jarvis
  restarted.*" **Resume** types `claude --continue` (or `codex resume
  --last`) and presses Enter, picking the last conversation back up; **×**
  dismisses it. Pane history is saved every 30 seconds, so a crash loses at
  most about half a minute of it. Also: a new terminal no longer sits blank
  for three seconds, dragging a pane edge no longer makes Claude redraw over
  and over, **Ctrl+C with text selected copies it** (with nothing selected it
  still interrupts), and a stray input can no longer mark a live shell
  "closed".
- **Faster, lighter, exact terminals** (v1.248.0): an idle pane now costs
  almost no CPU (eight idle panes used to keep a whole processor core busy),
  keystrokes echo in about a millisecond, and box-drawing, emoji and accented
  characters no longer turn into "�". The pane you're looking at draws on
  the graphics card; panes behind it in the rail stop drawing but keep
  running, and switching back to one is instant. A command that prints a huge
  amount can no longer freeze the page — the pane asks the app to hold the
  stream until it has caught up, while the program keeps running. If a
  machine has trouble: set `ij.build.webgl` to `off` in the browser storage
  to turn graphics-card drawing off, or start the app with
  `IRONJARVIS_PTY_BACKEND=pywinpty` to use the previous terminal engine.
- **Updating tells you what it will interrupt** (v1.249.0): **Restart to
  update** now lists what is actually running — a reply being written, Build
  panes with work in them, background jobs — and offers **Install now**,
  **Later**, or **Install when idle**, which waits until nothing is busy. The
  app then stops the way **Quit** does instead of being force-closed, and the
  update is checked before anything shuts down, so a half-downloaded update
  never costs you a running app.
- **Work a restart interrupted is offered back** (v1.249.0): jobs cut off by a
  restart appear on your Overview and in the bell with a **Continue** button,
  for three days. Pressing Continue picks the job up where it stopped.
- **Jobs waiting on you reach you with the window closed** (v1.249.0): when a
  job needs your permission and Iron Jarvis is in the tray, Windows shows a
  notice ("Jarvis is waiting for you: …"), the tray says how many are waiting,
  and it reminds you after an hour and again after eight. Clicking opens the
  job.
- **A Windows restart no longer leaves it closed** (v1.249.0): Iron Jarvis
  offers to start with Windows, quietly in the tray, so schedules and messages
  keep running after an overnight restart — and a Windows shutdown is no longer
  counted as a crash. You can turn it off in Settings.
- **If it cannot start, it says why** (v1.249.0): instead of always blaming the
  port, the message names the likely cause — another program on the port, a
  locked database, a failed data upgrade, a missing program file — and offers
  **Retry**, **Open logs** and **Quit**.
- **Backups can go to a second drive** (v1.249.0): Settings → Maintenance →
  Backups can copy every backup to another drive, and with it the images,
  video and audio Iron Jarvis has made for you (which backups never included
  before). The card says when the last copy happened, or names the folder if
  the drive is not plugged in — backups keep running on this PC either way.
  That copy holds your database, settings and saved logins, so keep the drive
  somewhere safe.
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

### Browser — your own Chrome or Edge

The **Browser** page is where Jarvis meets a browser. There are two of them, and
the difference matters.

**Your browser** is the Chrome or Edge you already use, logged into the sites you
are already logged into. Jarvis reaches it through a small **browser add-on** you
load yourself. Nothing is automatic: the add-on has to be loaded, you have to
press **Pair** in Jarvis, and you have to grant it access to sites. Until all
three happen, Jarvis cannot see a single tab.

**Jarvis browser** is a browser the app owns, separate from yours and logged into
nothing. It is not built yet, and nothing in the app pretends otherwise.

Below those sits the **agent browser** (the older computer-use machinery), which drives a headless
Chromium of its own behind a domain allowlist. It is unchanged, and it shares no
cookies or sessions with your browser.

**Setting it up (v1.240.0, five steps since v1.242.0).** The Browser card has a
**Set up my browser** button that opens a guided window and walks the steps in
order, moving itself along as each one lands: find the add-on folder on this
machine (it names the real path and copies it), load that folder into Chrome or Edge,
press **Pair**, allow site access, and open the sidebar. While the window is open
Jarvis opens the site-access page for you instead of making you find it.

**Edge works the same way.** It is the same add-on with the same identity, loaded
from `edge://extensions` instead of `chrome://extensions` — Developer mode, Load
unpacked, the same folder. Because the identity is identical, Jarvis could not
tell which browser had paired until the add-on started saying so: since
v1.259.0 the Browser card names it ("Microsoft Edge 153"), and the doctor's
browser line names the one installed on this PC.

**The steps are written for your browser (v1.261.0).** Iron Jarvis looks at
which browsers are installed on this PC and which one paired, and writes every
step of the guided window — and the card's own notes — for that one: on an
Edge-only machine you read Edge's page (`edge://extensions`) and Edge's way of
pinning the icon (the eye icon, **Show in toolbar**), with no Chrome in it. The
paired browser wins; with both installed and nothing paired yet it starts with
Chrome. A small **Steps written for** switch at the top of the window changes
it, and that choice is remembered on this PC.

**The last step is the one that makes the rest reachable.** Neither Chrome nor Edge
puts a newly loaded add-on on the toolbar — both hide it behind the **puzzle-piece**
button at the top right. Open that menu, find **Iron Jarvis**, and press the **pin**
(in Edge it is the eye icon, **Show in toolbar**), and
the icon stays on the toolbar where one click opens the sidebar. The Browser card
says this too, in every state, for anyone who set their browser up months ago and
never opens the guided window again.

**If clicking the icon opens a small popup rather than a sidebar**, your browser is
still running an older copy of the add-on: your browser keeps the copy it loaded
until you reload it, so updating Iron Jarvis does not update what it runs. Open
`chrome://extensions` (in Edge, `edge://extensions`) and press **Reload** on Iron Jarvis. The add-on carries the
same version number as the app it shipped with, the sidebar prints that number in
its own header, and the Browser card prints the app's — an older number in the
sidebar is the copy to reload.

Two of those steps are yours and cannot be automated, and the guide says so rather
than leaving you wondering. Chrome and Edge will not let an app install an add-on
into your browser, so **Developer mode** and **Load unpacked** are your clicks until there is
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
Files, Shell, Browser, Extensions, Memory. **Browser** is fully enforced. **Memory**
(v1.290.0) gates what a harness launched in the pane can read through Jarvis: with it
ticked, the harness may search and read your Jarvis memory (read-only — it can never
write to it); unticked, it gets none of it. Memory does not change what the pane's own
chat can do. So, of the boxes that reach a harness, only Browser is enforced for
the pane's chat as well. Files, Shell and Extensions are recorded and shown so you can
see what a pane is meant to be for, but nothing gates on them yet: a harness in that
pane still has whatever file and shell access its own CLI came with. Enforcing those
three is Phase 2 work, and this line will change when it lands rather than before. One more honest
edge, from the CLIs' side: some builds cannot be told to switch off their *own* web
tools, and where Jarvis cannot verify that it says so in the Launch menu instead of
implying an isolation it did not get.

**Where the add-on comes from.** The installer ships it **inside the app**, so
there is nothing to download and no repository to clone: the Browser card names
the add-on folder, and your browser's **Load unpacked** takes it from there. The
picker wants a *directory*, so you need where the folder is as well as what it is
called — on a default Windows install that is
`%LOCALAPPDATA%\Programs\Iron Jarvis\resources\browser-addon`, and if you chose your own
install location it is `resources\browser-addon` underneath it. `docs/BROWSER.md`
walks the whole thing. (Running from a source checkout instead? The card says so
and gives the one build command first — the built files are deliberately not
committed, and the browser refuses the folder without them.)

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

**Site access.** Chrome and Edge will not let Jarvis ask for access to your sites
on your behalf; the request has to come from a button inside the add-on. So the card sends
you to a single-purpose page with one button on it. Grant once and normal use stops
prompting. You can narrow it later in your browser's own extension controls, and Jarvis
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
  navigation is gated the same way the agent browser is gated. Every one of them asks
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

**Reasoning level, per conversation (v1.263.0).** Next to the model picker in
Chat, a small **reasoning** control appears for a model that offers one —
Claude 4 and 5 models, OpenAI's o-series and GPT-5, Gemini 2.5, the Claude and
Codex CLIs, and local reasoning models such as gpt-oss, DeepSeek-R1 and Qwen3.
Pick **low**, **medium** or **high** (or leave it on the model's default);
higher is slower and, on a metered API, costs more. The choice is saved with
the thread, sent on every turn, and the receipt under the reply says
"reasoning high" when it actually reached the model. For a model with no such
knob there is no control at all — nothing is sent, and nothing is quietly
dropped. If a server refuses the parameter, Iron Jarvis asks again without it,
so you get the answer rather than an error.

**Talk to the sidebar, and pick its model where you type (v1.269.0).** The
sidebar's top is now just the Iron Jarvis mark (the connection dot sits on it)
and the access pill. The model picker moved to the bottom right of the
composer as a small icon — click it and Jarvis's models appear; hover it to
see the one in use. Next to it is one button with two jobs: with nothing typed
it is a **microphone**, and as soon as you type it becomes the **send arrow**.
Press the microphone and speak; press it again to stop. What you said is
written into the box for you to read, then send. The audio goes through Iron
Jarvis's own speech engine — the bundled offline model when it is installed
(words appear as you speak), otherwise the transcription endpoint you set up
on the Connections page — and never through your browser's own cloud speech
service. If no speech engine is set up, the microphone is greyed and its
tooltip says what to connect.

**The sidebar remembers, has one switch, and folds its work (v1.270.0).**
Three changes from one report. *It remembers:* the sidebar's conversation now
lives in Iron Jarvis, so "now do the second one" refers to what you said
before; reopening the panel shows the conversation, and the **+** icon in the
header starts a new one (Jarvis forgets the old). The conversation is kept in
memory only — it ends when Iron Jarvis restarts. *One switch:* the header's
**Auto-allow** switch (shown at Interactive) replaces the per-action cards: on,
Jarvis acts on pages without asking each time, across every tab and every
message, until you turn it off; off, each page action shows a card with
**Allow**, **Always allow** (which turns the switch on) and **Deny**. The switch
is remembered in your browser. What it does NOT cover, on purpose: a payment or
password field, anything with "delete"/"remove"/"cancel" in its name, a page
that tried to give Jarvis instructions, and a control Jarvis cannot read still
stop and ask — those are decisions that stay yours. *The work is folded:* while
Jarvis works a page, the steps ("Clicking 'Next'…", "Typing into 'Search' —
done.") sit behind one **Working** line you can expand; when the turn ends it
reads "4 steps". A notice that a different model answered stays visible.

**Auto-allow actually allows (v1.270.1).** With the switch on, v1.270.0 skipped
the card but still refused the action a moment later ("needs your approval in
Settings"): the permission the card would have granted was never handed to the
tool. Fixed, and the same hole in v1.266.0's Allow-for-this-tab is closed with
it — a page action covered by the switch or by a tab grant now runs. And when
a step does fail, the folded line under **Working** now says why ("Could not
click 'Next'. the page refused: …") instead of only that it could not.

**The sidebar works in the tab you are looking at (v1.271.0).** Every job used
to start by opening a new tab — the tools' own descriptions told the model to
prefer one. Now Jarvis goes to pages, reads and acts in the tab you have open,
and opens a new tab only when you ask for one ("open a new tab…", "in another
tab", "in a separate window") or when you are mid-way through something on the
current page and must keep it. From the sidebar, the new-tab tool is not even
offered to the model unless your sentence asks for it. Also fixed: a web search
whose words include "buy" or "purchase" is no longer treated as a transaction —
the safety check now reads what a URL names (a /checkout or /transfer page
still asks) rather than the words you searched for.

**It works from a fresh tab (v1.271.1).** Asking the sidebar for a page from
the browser's own new-tab page used to fail with "pages are closed to add-ons"
— the tab you were on was judged as if Jarvis had to read it, when it only had
to leave it — so Jarvis worked in some other tab instead. Navigating away from
a new-tab page (or any browser-internal page), switching to such a tab and
closing one now just work; reading or acting ON such a page still cannot,
because the browser lets no add-on in there. The refusal now says "the
browser" rather than naming Chrome to an Edge user.

**The microphone can be allowed (v1.272.0).** Pressing the sidebar's
microphone used to say it was blocked with nowhere to allow it — a browser
side panel cannot show the microphone prompt at all. Now the first press opens
a small Iron Jarvis page in a tab with one button, **Allow the microphone**;
press it, choose Allow in your browser's prompt, come back to the sidebar and
press the microphone again. Nothing is recorded on that page; it only earns the
permission, which then covers the sidebar. If your browser had already blocked
the microphone for the add-on, the page tells you where to unblock it (the icon
left of the address bar).

**The tab Jarvis is working in glows (v1.273.0).** While the sidebar's agent
reads, clicks, types, scrolls or navigates a tab, that tab's page carries a soft
cyan glow around its edges — the app's own accent — so you can see at a glance
which tab the agent has. The glow lets every click of yours through (it is
purely visual), reads nothing on the page, and disappears when the turn ends,
when the connection drops, or, for a job driven from the Iron Jarvis window,
about twenty seconds after the last action. A page the browser closes to
add-ons (a settings page, a new-tab page) cannot show it.

**Fewer wasted steps, read off the day's ledger (v1.274.0).** Five things the
agent used to trip on, each seen on a real job: a screenshot of a tab that was
not on screen now brings that tab on screen and takes the picture, instead of
refusing; the site-access grant is now `<all_urls>` (the only shape the
browser accepts for screenshots — if you granted before, the Browser page and
the sidebar say "no site access" once, and the sidebar has a **Grant site
access** button now, so press it there); a tab showing the browser's own "can't
reach this site" page is named as exactly that, with the advice to navigate
elsewhere rather than read it again; the Edge Add-ons store is recognised as a
page closed to add-ons; and in both chat lanes the same tool call with the
same arguments is not run a third time after failing twice — the model is told
to change approach or tell you what is blocking. Also: approval cards and
Working steps now name the control ("click 'Sign in'") instead of "click on the
page"; the model is told only the browser tools it actually has; and switching
tabs no longer stalls the add-on while the model list refreshes.

**Attaching without the hassle (v1.275.0).** In chat you can now paste a
screenshot or a copied file straight into the message box (Ctrl+V) and it
becomes an attachment, the same as dropping it. A file alone is a message —
the send arrow works with no text typed. If you press Send while files are
still uploading, the message waits for them and goes with the files instead
of going without them silently. Several files upload a few at a time, in the
order you added them. And when a turn fails because the model you picked is
not reachable while your default model is, the banner offers **Retry with the
default model** in one press.

**The questions that are yours are asked where you are (v1.276.0).** The
four things Jarvis always stops for in a browser — a payment or password
field, a delete-shaped control, a page that tried to instruct it, a control
it cannot read — used to stop with a note telling you to approve it "in Iron
Jarvis" and ask again: from the sidebar, a dead end. That stop is now a card
in the sidebar itself, with the reason in it, and one Allow runs exactly that
one action. Two quieter savings: a page still loading is read again after a
moment instead of being handed back as a refusal, and a click named by what it
says ("Sign in") on a page Jarvis has not yet read makes Jarvis read the page
first, itself, rather than spend a turn being told to.

**Fewer reaches (v1.277.0).** Four small things that each cost a reach every
time. In the browser, **Alt+J** opens the sidebar (your browser's own shortcuts
page shows and changes the key), **Esc** stops a running turn from the box you
are typing in, and **Open Jarvis** brings the Iron Jarvis tab you already have
to the front instead of opening another. In chat, the model menu opens with a
box: type part of a model's name and every match across your providers is
listed — Enter picks the first — and with the box empty the menu opens on the
last three models you picked, above the provider list it always had.

**Steer a running turn, and edit a sent message (v1.278.0).** While Jarvis is
working on your message, the box says so, and **Enter** sends what you typed
as a note to that turn — "shorter", "in French", "use the second file" —
which Jarvis reads at its next step (it cannot interrupt a sentence it is
already writing, and the note says so under the reply). A note Jarvis read
stays in the conversation as your own message, marked **steer**, so the next
turn remembers it; a note it never reached is not kept, and the box tells you
if the turn had already finished. The sidebar has had this since v1.242.0;
now the chat page does too. And every message you sent carries a small
pencil on hover — **Edit and resend** puts it back in the box with its files,
removes everything after it, and Send runs it again over exactly the
conversation that came before.

**Jarvis learns how you like things, and the Memory page says what it knows
(v1.279.0).** Say a lasting preference in chat the way you would to a
colleague — "From now on, keep answers short", "Always give me numbered
steps", "Call me VR" — and Jarvis keeps it as a lesson it reads into every
later conversation; the receipt under the reply shows it was remembered, and
the **What I've learned** tab is where you forget one. (Inside agent runs this
already happened; in chat the tool was never offered.) The Memory page now
opens on **What Jarvis knows about you**: your profile, the preferences it
keeps, how many notes and past conversations it can search — and, when there
is nothing yet, the one sentence to type. Two quieter fixes: the "worked well
for …" notes Jarvis writes itself after every agent job are notes about past
jobs, not knowledge about you, so they no longer take up the lesson lines in
every conversation (they still feed the weekly distillation and the tab); and
the reasoning level you pick for a conversation is remembered with it — it
used to reset to the model's default every time you reopened the thread.

**Build knows who you are and what you are working on (v1.280.0).** The
AI-assist bar in a Build pane used to answer with nothing but the pane's
output — it was the one place in the app that knew neither your profile, nor
the project the folder belongs to, nor a single lesson. Now it gets the same
three things every chat turn gets, plus one line naming the pane, its folder
and the CLI running in it. Two quieter things: the Build page tidies up after
panes that no longer exist (their view, chat thread and canvas position used
to stay in the browser forever), and the Agents page's job card opens on the
agent you last handed work to instead of the Team every time.

**A Build pane can make its folder a project in one press (v1.281.0).** When
a pane's folder belongs to no project, the pane chat shows the folder name and
**Make this a project** beside it. One press creates a project rooted there —
the same thing the chat page's "Make this folder a project" does — and from
then on this pane's chat, its assist bar and any agent handed work here are
grounded in it, with its brief and instructions wherever you fill them in on
the Projects page.

**When Jarvis learns something about you, it says so (v1.282.0).** The line
under a reply now reads **Remembered: …** with the sentence Jarvis kept —
"Prefers short answers with numbered steps" — the moment it keeps one, in the
same place the reply's model and tools are named. Nothing about you is written
in silence; the What I've learned tab is where you forget it.

**A module in its own window, on your other screen (v1.283.0).** Two or three
modules at once, each on its own monitor: the small window icon in the title
bar opens the page you are on in a new window, and every row of the
navigation drawer has the same icon on hover for that module. When your desk
has a second screen the new window opens centred there; on one screen it
steps off the main window. Each window remembers where you left it, per
module, and comes back there next time (or on-screen, if that monitor is
gone). A popped-out window says **own window** in its strip and is named by
its module in the taskbar, so two Iron Jarvis windows never look like the app
opened twice. Closing it just closes it — the main window keeps its
keep-running behaviour, and Quit closes them all. Opening a module that is
already popped out brings that window to the front instead of making another.

**A remote agent is a conversation, both ways — from your desk and from your
phone (v1.285.0).** You asked what was stopping you from talking to your
remote agents the way you talk to Jarvis over Slack. The answer was the
plumbing: every call to a remote agent was one request carrying only the task
text, it kept no memory between calls, and it had no way to reach you. Now a
remote agent is sent the conversation — what you said, what Jarvis and the
other agents said, its own earlier replies — as real prior turns, with a
stable conversation id, so a remote that keeps state can hold the thread and
one that does not still answers in context. It can take a long job and say
"I have it" (an HTTP 202) instead of timing out; the chat says it is working
and will report back. And it can **message you back**: on the Agents page,
under Set up agents, each remote agent has **Let it message back** — press it
and Jarvis mints a token for that agent, shows it once with the address to
post to, and from then on the agent's progress lines, questions, results and
files arrive in the conversation it belongs to: as attributed lines in the
chat (a quiet one-liner for progress, a normal reply for a question or a
result, files in the Files rail with preview and download), and — when the
conversation is one you are having from
your phone — on your phone, named ("hermes: Done — 3 files."). **Rotate
token** mints a new one and the old stops working; **Turn off message-back**
forgets it, and disabling the agent silences it in both directions. One
honest limit: out of the box the daemon listens on this machine only
(127.0.0.1) and answers only requests addressed to it, so a remote agent
running on THIS machine can message back as is, while one on another
machine cannot until you choose to open a door — the token box says so and
names the two settings: start the daemon listening on your LAN address
(`ironjarvis serve --host <your LAN IP>`) with
`IRONJARVIS_HOST_ALLOWLIST=<your LAN IP>` set, or put a tunnel or reverse
proxy in front of 127.0.0.1:8787 and paste its address as the inbound URL.
Nothing opens on its own. Files a remote sends land under the app's remote inbox folder,
never in a project it was not given, with the same name, size and host checks
its earlier file hand-backs had. From your phone (Telegram, Slack, or any
chat-enabled channel), "@hermes what is the ledger total?" goes to hermes as a
conversation; the reply comes back named on the same thread; your next
messages keep going to hermes until you say **@jarvis**, which hands the
conversation back to Iron Jarvis. A local agent named from the phone still
goes through Jarvis. For someone wiring a remote agent: its outward request
now carries `conversation_id`, `history` and `reply_to` beside `task` (the
OpenAI-style kinds get real multi-turn `messages` / `input`); answering `202`
or `{"accepted": true}` means "working, will message back"; and messaging
back is a POST to `reply_to` with `Authorization: Bearer <inbound token>` and
`{conversation_id, message, kind: message|progress|question|done, files}`.

**An @-mentioned agent remembers the chat, stays in the conversation, and can
do the work (v1.284.0).** Four things were wrong, and you found all four.
First, "@builder make that a PDF" arrived with no "that": the agent was shown
only the panel's own transcript, never the chat that led up to it. Now every
agent you @-mention is shown the conversation so far — what you and Jarvis
said, and what earlier agents answered, in order and by name — fitted to its
model; if a long chat does not all fit, the reply says how many earlier
messages were left out. Second, you no longer type "@" every time: after a
round, a strip above the box says **Talking to builder** and the box reads
**Message builder…**; plain follow-ups go to builder, in your words, until you
press **Back to Jarvis** or @-mention someone else. Reopening the conversation
later picks up where it was. When you do go back, Jarvis reads the agent's
replies as the agent's, not as its own earlier words. Third, a brand-new
chat's first two @-rounds landed in two different rooms (the conversation had
no saved id yet), so the agent forgot the first exchange — one room now.
Fourth, and the one behind the PDF: a panel seat has no tools by design, so it
could only describe a PDF. Now when you ask one local agent for work — "write
me a PDF summary", "convert this to pdf", "draft a memo", or anything with a
file attached — Jarvis hands it to a real session of that agent with its
tools, says so ("builder is doing it in a real session"), and the reply comes
back with the file: preview beside the chat, download and open in the Files
rail, attributed to the agent. A question stays a conversation; under any
agent's answer, **Have builder do this** turns the proposal into that session
on your press. On the Agents page, **Give it to builder** now carries the
thread's recent conversation with the job (the receipt says how many
messages), and the finished job's files appear under the receipt — Finished,
the agent's summary, and each file with preview, open and download — instead
of on another page.

**The sidebar stays connected, and looks like the app (v1.268.0).** The
random "disconnected… reconnected" was the browser putting the add-on's
background worker to sleep after half a minute of quiet, which closed its
link; the next tab switch woke it and it came back. Iron Jarvis now sends the
add-on a heartbeat every 20 seconds and the add-on answers, which is exactly
what keeps that worker awake — so a connected sidebar stays connected. (If the
app itself restarts, the sidebar says so and reconnects when it is back.) And
the panel now wears the app's own look: the arc-reactor accent, soft surfaces
instead of boxed lines, your messages as bubbles on the right and Jarvis's on
the left, a rounded composer with the send button inside it, and the same
light or dark ground as your browser.

**A quieter sidebar, and the model is yours to pick (v1.267.0).** The
sidebar now shows only what changes what you can do: the connection, the
access mode as a small pill, the tab-allowed pill when it applies, the model,
and the box you type in. Every explanation that used to sit in a paragraph
lives in a tooltip — hover the pill, the Stop button or the text box. And the
sidebar has a **model picker**: the list is the same one the chat page
offers, **Default** names the model Jarvis would use anyway, a model that is
not connected is greyed out, and your pick is remembered in that browser.
If a picked model cannot be reached, the sidebar says so before anything is
sent; if the app answered with a different model than the one asked for
(a failover, or the built-in mock), one muted line says which.

**One approval per tab (v1.266.0).** The sidebar used to ask before every
page action, and **Allow for this task** only covered the rest of that one
message — ten messages into a job in one tab was ten cards. The approval card
now offers **Allow for this tab**: that action runs, and every later page
action in the same tab runs without asking — across messages — for as long as
the tab stays open. Closing the tab ends it; so does restarting the browser,
and Forget on the Browser page. The header says "This tab: allowed until it
closes" while it holds. **Allow once** and **Allow for this task** are still
there. What a tab approval never covers: the questions that are yours —
paying, entering a password or a card, deleting, and anything on a page that
looked like it was trying to steer Jarvis — those still stop and ask. The same
button appears on the chat page's approval card for browser actions.

**Pairing never dead-ends, and the sidebar matches your browser (v1.265.0).**
If the Browser page ever told you "This install is already paired with a
browser. Press Forget…" while no browser of yours was paired, this is the fix.
The add-on reconnects whenever the browser restarts its background worker, and
the page could keep offering **Pair** for the connection that had just closed;
pressing it created a pairing for nobody, and every later press was refused by
it. Now a closed connection takes its Pair offer with it, nothing is created
until the browser that asked is confirmed to be there, and a pairing that could
not be delivered is thrown away in the same step. **Pair replaces** a pairing
whose browser is not connected — a reinstalled add-on, or a second browser
opened while the first is closed — so there is no Forget-first step for the
common cases; the one time Forget is still needed is when another browser is
paired *and connected right now*, and **Forget is on the card in every state
that has a pairing**, not only under Connected. And the sidebar and the add-on's
site-access page now follow your browser's theme: light in a light Edge or
Chrome, dark in a dark one (Edge: Settings › Appearance; Chrome: Appearance ›
Mode; or the system setting when either is left on System).

**The sidebar's first minute (v1.264.0).** Three things that used to go wrong
right after loading the add-on, fixed: **Enter sends** in the sidebar (Steer
while Jarvis is working; Shift+Enter for a new line). If the sidebar says
"Iron Jarvis did not answer at 127.0.0.1:8787", the app is not running yet —
it restarts during an update — so start it and press **Connect**, which tries
again at once instead of waiting out the retry timer. And **Open Jarvis** from
the sidebar opens the dashboard in a browser tab, which has no token: the
banner at the top now offers **Open in the Iron Jarvis app**, which brings the
app's own window to that same page (the app registers `ironjarvis://` links),
and names the token file if you would rather paste it. A Browser card that
says "not signed in" is that same browser-tab situation — not a reason to
restart anything.

**The sidebar works the page for you (v1.262.0).** With access set to
**Interactive**, ask the sidebar for a job — "fill this form with my details",
"find the cheapest option on this page and open it", "book the first available
slot" — and Jarvis does it: it reads the page, clicks, types, presses keys,
scrolls and moves between tabs itself, step by step, and tells you what it did.
Every action that changes a page asks you first; press **Allow for this task**
on the first card and the rest of that job runs without asking again (the next
message starts fresh). Before this version the sidebar only got the tools your
sentence happened to name and ran out of steps after six — it read as a chat
box beside the page. At **Read only** it can look but not act, and it says so
in its header with the switch named; **Off** runs nothing. Jarvis still stops
to ask when a decision is yours — paying, sending, signing, deleting.

**The sidebar is a window onto Jarvis, not a second Jarvis.** The chat docked in
your browser holds no model, no agent loop and no settings of its own: the question
you type there is answered by the same engine, the same persona and the same
permission gates as the chat on the Jarvis page, and its answer is streamed back into
the panel. What it may do in your browser follows the Browser access setting exactly
— **Read only** gets the inspection tools, **Interactive** the full set with the
approval card still in front of every page action — and with Browser access **off**
it runs nothing at all and says so, rather than looking busy. An approval it raises
is answered in the panel, and a refusal is recorded as your decision.

**Stop and steer, and what neither can do.** Stop ends the answer being written and
prevents the next step; it does **not** kill a step already running — that step
finishes and its write lands, so Stop is not an undo. A steer note joins the
conversation at the next step boundary, so it cannot interrupt a half-written
sentence; the panel shows the note as pending until the turn actually takes it, and
if the turn ends first the panel says the note was not taken instead of pretending it
landed.

**What this first version cannot do**, stated here rather than discovered later. One
browser at a time, and a newer connection replaces the older. Chrome and Edge only,
version 120 or newer. Loaded unpacked, so your browser may prompt about it at each start until a Web Store listing
exists. Site access is granted all-or-nothing from the add-on's own page and narrowed
afterwards in your browser's controls, never per-site from Jarvis. Downloads land where
your browser puts them — Jarvis learns the path and copies the file into a project, and
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
harness after a restart. Browser is enforced per pane; Memory is enforced only for
what a launched harness can read through Jarvis (read-only). Keeping a
harness away from its *own* web tools is best effort and is reported honestly. There
is no Jarvis browser yet. And Chrome unloads a browser add-on that has gone quiet, so
a long-idle bridge reads as **Paired — not running** with Chrome open in front of
you; it is not broken and needs no re-pairing, and it reconnects by itself the moment
anything wakes the add-on — opening its side panel, switching tabs, or restarting
the browser.

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

## What changed in the look and feel (v1.313.0 →)

Every screen was photographed (a new install, a lived-in one, a phone, and
the light Daylight theme) and reworked so it reads plainly and looks calm —
nothing was taken away.

- **The demo is always named.** While replies are the built-in scripted
  demo, the title bar's model button says **Demo replies** (an amber dot on
  narrow windows) and never shows a model name; the amber strip at the top
  also appears when a model is connected but not yet chosen, and its
  **Choose a model** opens the model menu right there. Wherever the app used
  to say "mock" it now says **Demo model (scripted)**.
- **Themes everywhere.** Pick a theme from the dots in the title bar, from
  **Settings → Appearance**, from the menu drawer on a phone, or by typing
  "Theme" in the Ctrl+K search — all four change the same setting. The light
  themes now draw status colours and chips in deeper inks you can read.
- **Rate a reply in Chat** (v1.320.0). Under each reply in Chat there is a
  👍 / 👎 (the newest reply asks "Was this helpful?"; older replies show the
  buttons when you point at them). 👍 is noted at once. 👎 asks **What
  should be different next time?** — your answer is remembered and used in
  later replies (it appears with your other lessons on the Memory page);
  **Skip** records the 👎 without a rule. A rating is kept with the chat, so
  reopening it shows what you said. The setup checklist's **Teach it your
  style** now says exactly this, and its button opens Chat with an example
  ("From now on, keep your answers short and to the point") already typed —
  edit it and press Send. Agent runs can still be rated on their own page.
- **Settings opens short** (v1.319.0). In Simple mode, **Settings** shows
  the everyday choices — which AI answers, its model, and the default
  persona — plus Appearance and Maintenance. **Show all settings** opens the
  whole form (local models, automation limits, history, power-user options),
  and a link to a particular setting from elsewhere in the app opens it too.
  Advanced shows the whole form as before. The examples in Chat and in the
  setup window's first task are everyday tasks now (a packing list, the
  weekend's weather, a follow-up email). The title bar's theme dots show
  in Advanced; in Simple mode the theme is in the menu drawer, Settings →
  Appearance and Ctrl+K.
- **A calmer start: Simple mode's home and menu** (v1.318.0). Simple mode
  is the default (the **Advanced** switch at the bottom of the menu turns it
  off, and Advanced looks exactly as before). In Simple mode:
  - The menu lists seven places by what you came to do: **Home**, **Work**
    (Chat, Projects, Agents, Creative, Build), **Files** (Documents, File
    Search), **Automations** (Workflows, Schedules), **About me** (You,
    Train Jarvis on me, Memory), **Apps & settings** (Connections, Directory,
    Notifications, Settings, Updates) and **Help**. The place you are in
    opens to show its pages.
  - Pages show their place's other pages as tabs above the title
    ("Automations: Workflows · Schedules"), so the next page is one press
    away without opening the menu.
  - **Home** (the first page) leads with one "Ask Jarvis anything" box and a
    few everyday starters, then **Pick up where you left off** (your last
    three chats) and **Where to go** (the places above, each with one line
    about what it is for). **All modules** shows every module; Ctrl+K finds
    any page by name. Status figures, the module grid, the run-quality card
    and "Systems & admin" are on the Advanced Overview.
  - Where did a page go? Nowhere — every page keeps its address and is in
    Ctrl+K. Pages for power users (Reflexes, Sentinels, Webhooks, Secrets,
    Tools, Sessions, Activity, Usage, Local fleet, Self-development) appear
    in the menu with **Advanced** on, and as a tab while you are on them.
- **Make your own theme** (v1.317.0). In **Settings → Appearance**, press
  **Make your own**: pick a main colour (or start from one of the built-in
  themes), optionally a second colour for the background glows, how the
  background should feel (Neutral, Cool, Warm, or Tinted with your colour),
  and whether to show the **Dark** version, the **Light** version, or
  **Match Windows** (it follows your Windows light/dark setting by itself).
  Both versions are made for you and previewed side by side, and every word
  is kept readable: if a colour would be hard to read on a dark or a light
  background it is made a little brighter or deeper, and the panel says so.
  **Save and use** applies it at once; your themes also appear as dots in
  the title bar and as "Theme: <name>" in Ctrl+K, and you can edit or
  delete them later (up to 12, kept on this PC like the theme choice). The
  workflow canvas's wires now take the theme's main colour, and pop-out
  windows' minimise/close buttons follow a light theme too.
- **Phones.** Pages no longer slide sideways, the title bar's buttons no
  longer overlap, and search icons sit inside their boxes.
- **Plain words.** Sessions say who started them in words ("Schedule ·
  nightly-brief", "Mission", "Queued job") — the exact origin is still on
  hover. Approval cards say what the action will do; the tool's own name is
  in the details. The Overview's health card reads "Tasks reviewed",
  "Finished", "Tools worked" and "Typical reply time".
- **The busy screens, calmer** (v1.315.0). On a phone the chat's message
  box gets its own line with the buttons under it, and a chat's ⋯ menu is
  visible without hovering. The thread list says whether it shows this
  project's chats or all of them. The mission coordinator is called
  **Jarvis** everywhere. A session's page leads with what happened and what
  it made; the step-by-step log is still there below. Kanban is now the
  **Session board** (search finds it). Project cards have one main button
  (**Set as focus** / **Clear focus**, formerly Make active / Deactivate),
  the project header's model picker is labelled **Model** ("Same as app
  default" when empty), and Recent runs show what you asked first. Creative
  always asks before an upload is published to the public link. The Browser
  page names its two parts: **your browser** (your own Chrome or Edge) and
  the **agent browser**. File search's "By meaning" says it searches the
  folders Iron Jarvis has indexed.
- **Settings, usage, memory and connections, plainer** (v1.316.0). Change
  any setting and a **Save changes** bar stays at the bottom of the window
  until you save or reset; a row of links at the top jumps to each section.
  Usage starts on **Tokens** when nothing was billed, shows every day of
  the period (empty days included), and says what local models would have
  cost at a cloud list price as an estimate — never as money spent.
  Autonomy shows one list, **Simple goals**, with its on/off state at the
  top; goals made in Chat are listed separately as **Goals from Chat**.
  Links to /ltm and /lessons open the right part of Memory. Every form label
  is tied to its box (click the words to type). Agent names read as names
  ("Builder"), while the exact id is what is saved. Connections leads with
  choosing a model; cards that need a one-time developer setup fold it
  away. The Tools catalog says **Uses Node** when Node is installed instead
  of warning that it is missing.
- **Empty pages show the way.** An empty Reflexes, Sentinels, Webhooks,
  Schedules, Session board, Sessions or Terminals page says what the place is for,
  gives an example or two, and offers the first step (the button opens the
  page's own form). Memory keeps task notes apart under **Notes about past
  jobs** — they are not added to your prompts.

## What got faster (v1.258.0)

**Opening a chat.** A chat window can show a project board, a knowledge rail, a
share box, a folder-reading card, a run-result card and a goal offer — and until
now, opening any chat downloaded the code for all six, whether or not you ever
opened one. They now arrive when you open them. Measured on this PC: the code a
chat fetches before it can show you anything dropped by about a fifth (567 KB of
page-specific code down to 445 KB; the figure the build reports for a first visit
went from 307 KB to 270 KB). Nothing looks or behaves differently — each of those
six panels was already hidden when a chat opens, so there is nothing to see until
you ask for it, and then it appears as before.

**What did NOT change, deliberately.** Two cards that look like the same kind of
thing — the one summarising a compaction, and the one showing a workflow draft —
still load up front. Each lives in the same file as a small chip that a
conversation does show, so moving the card would have changed nothing about what
gets downloaded while making the code harder to follow.

## What got faster (v1.257.0)

**Opening the app when you have several tool packs.** Iron Jarvis says hello to
each of your tool packs when it starts. It used to do that one pack at a time,
so the wait was every pack's greeting added together — two packs meant twice the
wait, three meant three times. Now it greets them all at once, so the wait is
about as long as the slowest single pack no matter how many you have. Measured
on this PC with two packs: 2.2 seconds down to 1.1. Nothing about your packs
changed — a pack that cannot start still tells you why, in the same words, in
the same place.

**The app while an agent is typing.** When an agent writes a long answer, the
words arrive a few at a time. The app used to redraw the whole chat page for
every single word, which is what made typing, scrolling and clicking feel like
they were catching on something during a long answer. Now only the bubble
holding the answer redraws. The answer appears exactly as before, at the same
speed, in the same place — the rest of the app just stops stuttering while it
happens.

**Stopping an agent still keeps its words.** If you press Stop partway through
an answer, the part already written stays in the conversation, marked as cut
off. That was true before and it is still true.

**Less background chatter.** While an agent is working, the app checks on it
every second and a half. It used to keep checking with the window minimised;
now it stops while you are not looking and catches up the moment you come back.

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
   leaves this machine unless you switch it to **Let another model answer**
   (the `failover` setting). A dead local
   endpoint is caught by a ~2 s liveness check before the turn starts
   instead of after three 60 s timeouts. When a failover *does* happen, the
   receipt under the reply names what failed and why ("answered by
   Claude Code — fleet-rtx6000ada returned HTTP 500"), and the phone/desktop
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

The **Guide** is a built-in agent — it is on your team beside the builder,
researcher and the rest — and it is the expert on Iron Jarvis itself. Ask it
in chat with **@guide** (the **Ask the Guide** box at the top of the Help page
puts your question into chat that way, ready to send), or run a session as the
*guide* agent for a longer lookup. It starts every session knowing what the app is and how your
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

## Apps that talk back (v1.324.0)

Apps you added (on the Tools page) can now do more than answer a tool call.
Ideas from assistant-ui and tambo (MIT; ideas only) and the app standard
itself:

- **An app can ask you a question.** While one of its tools runs, an app may
  need something from you (which year, which account). A small form appears
  in the reply, saying which app is asking: fill it in and press **Send**, or
  **Decline** / **Not now**. The tool waits for you, with no time limit,
  until you answer or press Stop. Only answer apps you trust, and never type
  a password into one of these forms.
- **An app can ask to use your model, only with your OK.** Some apps want a
  model to write or summarize something for them. A card shows what the app
  wants to ask and which model would answer. Nothing is sent until you press
  **Allow once**, and only the model already answering this chat is used —
  never another one, never as a fallback. A demo model, a conversation that
  was marked low-trust, or **Deny** all answer the app with a plain no.
- **Progress.** A tool from an app that reports how far along it is shows it
  on its line ("40% · reading").
- **An app's prompts under "/".** Type **/** in the box: below your skills,
  **From your apps** lists the ready-made prompts your apps offer. Pick one,
  fill in its blanks, and its text lands in the box for you to edit. Nothing
  is sent until you press Enter.
- **An app's files under "@".** Type **@**: **From your apps** lists what
  your apps can hand over (notes, records, files). Pick one and it is
  attached to your next message, like a file. Iron Jarvis reads it when you
  send, checks it for hidden instructions, and says under the reply if it
  could not be read. Regenerate reads it again.
- These questions and requests only reach you in the main chat window. In a
  Build pane, the browser sidebar, a schedule or an agent run, an app that
  asks is told "no" straight away, so nothing waits for an answer nobody can
  give.

## Chat polish (v1.323.0)

More ideas from assistant-ui and tambo (MIT; ideas only):

- **See how it thought.** When the model shares its reasoning (Claude with a
  reasoning level set, many local models, some OpenAI-compatible servers),
  a folded **Thinking…** line sits above the reply while it works and becomes
  **Thought for N s** after. Press it to read the reasoning. It is never sent
  back to a model and never part of the answer.
- **Continue a cut-off answer.** When a reply stops because the model ran out
  of room ("stopped — the reply ran out of room"), or you pressed Stop, the
  newest reply offers **Continue**. The model carries on where it stopped and
  the two parts become one reply.
- **How long each step took.** Open the line under a reply: each tool shows
  its time ("read_file · 0.3 s", a failed one marked), with "First word after
  1.2 s · 42 tokens/s". While a reply runs, finished steps show their time
  too.
- **Times.** Hover a message to see when it was sent.
- **Drafts stay with their chat.** Leave a saved conversation with something
  half-typed, and it is back in the box when you open that conversation
  again. A new, unsaved chat still starts empty.
- **Read one reply aloud.** The speaker button under a reply reads just that
  reply (press again to stop), whether or not spoken replies are on.
- **Follow-up questions (off by default).** Turn on **Settings → Which AI
  answers → Suggest follow-up questions**, or ask in chat. Up to three short
  questions then appear under each new reply. Pressing one puts it in the box;
  nothing is sent until you press Enter. It makes one extra short call to the
  same model that answered (never a different one), and the demo model never
  suggests.

## Safer chat and apps (v1.322.0)

Ideas borrowed from two open-source chat projects (assistant-ui and tambo,
both MIT; ideas only, no code copied), first the ones about safety:

- **A picture from the web waits for you.** When a reply contains an image
  hosted on some website, the chat shows a button that names the site
  ("Load image from example.com?") instead of fetching it. Loading it would
  tell that site you read the reply, and a web address can carry text out of
  the conversation. Your own images (files on this PC, Creative output,
  inline pictures) still show at once.
- **Fetching a web page cannot reach inside your network.** The web-page
  tool now looks up where a site's name really points and refuses addresses
  on your own network or PC, including through a redirect, before anything
  is sent.
- **Files belong to the chat they were added to.** If you start a new chat
  (or open another one) while a file is still uploading, it is not attached
  to the new chat, and a message you had queued behind it is not sent there.
  One file that fails to upload no longer stops the others; the chat names
  the one that failed.
- **Deleting a chat takes two presses.** The first press says "Delete for
  good? Press again".
- **Stop and Edit give your words back.** Stop before the first word puts
  your question back in the box. After **Edit** on a sent message, an
  **Undo** brings the conversation back as it was.
- **Typing with an input method** (Japanese, Chinese, Korean…): pressing
  Enter to confirm a character no longer sends the message.
- **On a touch screen** the copy, rate and other reply buttons are always
  shown (there is no hover).
- **Apps (MCP packs) are more reliable.** A pack that sends progress notes
  before its answer no longer reads as an empty success; a pack that pings
  is answered; packs with more than one page of tools load them all; a long
  call to an online pack is no longer cut off at 30 seconds (it follows the
  tool time limit in Settings); and a document or picture a pack returns is
  described instead of showing as "[resource]".

## What the deep review fixed (v1.286.0 →)

A review of the whole app, in eight parts: chat, agents, the daemon, packs
and documents, the two sets of pages, the desktop app and the test suite.
Every problem it reported had to be proven first by a test that failed on the
code as it was, and a second pass tried to knock each proof down: 65 were
proven, 41 survived. Each fix was made by one agent and reviewed by another
that read the actual change and broke it on purpose to watch its test catch
it, then shipped only after the full suite and the release gate passed.
**v1.286.0 (Wave 1, the safety net):** the checks that guard every update
are trustworthy again — the local run no longer stalls near the end on old
leftover review tests, a stuck test now fails within five minutes and names
itself (a stuck GitHub run stops within the hour instead of six), the
developer's copy runs the same Python as your installed app, notes posted
to the Team board at the same instant keep the order they were posted in,
and three checks that measured how busy the computer was now test what the
code does. **v1.287.0 (Wave 2, chat that stops when you say stop):**
pressing Stop, closing the tab or pressing Retry on an answer from your
Claude, Codex or OpenCode subscription now ends that program instead of
leaving it running on your plan for minutes; the side panel's Stop works
even before the first word appears; a correction you type while Jarvis is
finishing comes back in the message box ("Jarvis finished before reading
this") instead of vanishing; a reply that reads several large documents
on a small local model stays inside what the model can hold; and a cloud
model that drops halfway says the reply is incomplete and to retry,
instead of a bare "stream error". **v1.288.0 (Wave 3, agents that finish
honestly):** an agent that lists a client folder or prints a file through
the shell now sees accented names as they are (José, Müller, Muñoz), where
one accented letter used to make the folder look empty; a command that
stops to ask a question carries on at once instead of freezing for a
minute; a Team worker that needs more than ten minutes keeps going and is
never recorded as "cancelled by the user"; workers can use the tools you
approved for the job; a job that fails no longer leaves an agent showing as
still running; a remote agent that answers too slowly stops at its time
limit instead of holding up the chat and your phone; and a job whose model
goes quiet after doing real work says what it ran and what came back,
instead of "(no final message)".
**v1.289.0:** an Overview tile can no longer be dragged off the screen —
it stops at the edge — and in the desktop app pushing a tile past the
edge opens that module in its own window instead, with your arrangement
left as it was.
**v1.293.0:** the Overview's modules are three screens of ten instead of
one wall of thirty: **Office** (Chat, Build, Projects, Creative, Documents,
File Search, Memory, You, Train Jarvis on me, Templates), **Operations** (Agents,
Workflows, Schedules, Tools, Skills, Autonomy, Sentinels, Reflexes,
Webhooks, Browser) and **System** (Sessions, Activity, Artifacts, Usage,
Connections, Local fleet, Secrets, Notifications, Settings,
Self-development). Change screens with the group tabs above the tiles
(each has its own icon), the arrows at either side, a swipe across the
background, a sideways trackpad/wheel gesture, or the arrow keys once the
strip has focus; the desktop reopens on the screen you left. Everything
else is as it was — most-used first within each screen until you drag,
hover for the description, drag to rearrange (a drag never changes
screens), push a tile past the edge to open it in its own window, Reset to
most-used — and an arrangement you saved before this version keeps its
order.
**v1.293.1:** with ten modules per screen there is room, so each Overview
tile is twice the size, and a screen is two rows of five until a very wide
window can show all ten across.
**v1.294.0:** no more swiping. The Overview opens on three large icons —
**Office**, **Operations**, **System** — each saying what it holds and how
many modules are behind it. Press one and the other two step aside while
that group's ten modules appear in place; the back control at the top left
(or Esc) brings the three icons back. Inside a group everything is as it
was: most-used first until you drag, hover for the description, drag to
rearrange, push a tile past the edge to open it in its own window, Reset to
most-used. The Overview reopens the way you left it, on a group or on the
three icons.
**v1.295.0 — your agents are employees now.** Every custom agent on the
Agents page has a **job card** and a **monthly allowance**. The job card
(open the row, or the "Employee details" disclosure when you create one)
holds: the base type it inherits (builder, researcher, planner, reviewer…),
its preferred model, its **approval posture** (ask me as usual, approve for
me, always ask), a **step budget** per run, who it **reports to** (you, a
builtin, or another of your agents — it is told so, and says plainly when it
is blocked or done), **skills** it always carries, and tools it may **never**
use. The allowance is tokens and/or dollars per calendar month (blank =
unlimited): a thin bar on the row shows what it has spent this month; at 80%
the bell warns you once; when the allowance is used up the agent is
**paused** with the reason on its row, the bell says so, and every door
refuses in plain words — the Run button, a schedule, a teammate delegating to
it, the Give-work card — rather than silently running it as a plain builder.
**Pause** any agent yourself with a reason (a day off); **Resume** brings it
back, and raising the allowance of an agent that was paused for running out
brings it back on its own. The phone, a chat @-mention and a teammate's
`consult` all answer with the same sentence for a paused agent. Nothing else changes: a paused agent keeps its memory, its face and its
history. When a run is served by the Claude CLI, the agent's remaining dollar
allowance is also handed to the CLI as its own hard budget for that run. Re-
creating an agent with a name that exists is refused — edit its row instead.
**v1.296.0 — give an agent a job, and the job waits for it.** An
**assignment** is work queued for a named agent (a builtin like builder, or
one of yours): it sits in that agent's **inbox** until the agent is free, not
paused and within its allowance, then runs as a normal session you can watch
on the Sessions page and the project Board. Queue one from the Agents page
(the Assign box in an agent's inbox, under the rail's Agents row), from a
project's Tasks tab ("Assign to" — the project folder rides
along), or let an agent hand work to a teammate with the new `assign_work`
tool (a manager delegating down the chart; two levels deep at most, twenty
waiting per agent). An agent works one assignment at a time, highest priority
first, oldest first. The inbox says why something is **held** (paused, out
of allowance, busy, every slot taken) in plain words. A run that fails is a
strike; three strikes in a row **block** the assignment with the last error
on it — the bell tells you, and Unblock or Retry puts it back. Cancel stops a
running one. After an update or a crash the queue picks itself back up
(nothing is lost or counted as a failure), and the bell says how many
resumed. Every agent now also shows real **health**: when it last ran and how
it ended, its last error, what is queued, running or blocked — and "idle"
means exactly that.
**v1.297.0 — an agent keeps a folder, learns from a coach, and its skills
are curated.** Every agent of yours now has a **folder** under the app's
home (`agents/<name>/`): its instructions as a file with a **history** (every
edit keeps the previous version; View shows the difference, Restore puts it
back), and a private **notebook** the agent can write to during its runs
(the `notebook` tool) and you can read or edit — trimmed to 4,000 characters
when it is injected into the agent's runs. Open the folder from the agent's
detail in the Agents dialog. The **coach** reads an agent's last ten runs
straight off the ledger (outcomes, failed tools, denials, unanswered asks,
step budgets, your thumbs-down), groups what went wrong into eight named
kinds (verifier-miss, avoidable-rework, tool-misuse, late-escalation,
scope-creep, instruction-miss, stale-context, human-correction) and, on
"Ask the coach", proposes the **smallest** change to that agent's
instructions — shown as a diff with Accept / Decline. Nothing changes until
you accept; a declined idea is not raised again for two weeks; the coach
never coaches itself, never drops a line that says "never" or "always", and
with no real model connected it says so instead of inventing a change. The
**curator** keeps the skills your agents wrote tidy: skills show who made
them (you, an agent, a proposal), a pin keeps one forever, and a daily sweep
**archives** agent-made skills nobody has used in a month (never deleted —
the whole skills folder is backed up first, archived skills sit in
`skills/.archive/` and Restore brings one back). The Skills page has the
curator panel with a dry run that only lists what it would do.
**v1.298.0 — trust, scoped.** A run now has a trust posture. It drops to
**low trust** in two cases: it started from an inbound message on the phone,
Slack or email (unattended work from outside the app; `comm_trust` in
settings turns this off), or, mid-run, it read something that looked like an
instruction smuggled into content — a web page, a document, a tool result.
In low trust the run keeps working, but it cannot change your memory,
preferences, settings, agents or skills, cannot queue work for teammates, and
a shell command must run in the sandbox (no sandbox reachable = refused in
plain words). The reason is on the session card and the session page as a
"low trust" chip and a banner, the bell says when a run dropped, and the
chat receipt carries a quiet line ("low trust: 4 tools kept away").
Teammates it delegates to inherit the posture. And every piece of context
that is injected into a prompt — project knowledge and instructions, an
agent's notebook, skills from outside the app or written by agents, uploaded
documents, retrieved memory, lessons — is now scanned first: a passage that
reads like an injection is replaced with `[BLOCKED: … — removed from <source>]`
while the rest loads unchanged (very long files are trimmed in the middle),
and the bell says how many passages were blocked and where. Your own
instructions and your own skills are never scanned: they are your words.
**v1.299.0 — grants that mean exactly what you approved, packs that
cannot slip in a new power, and schedules that know more.** Approval cards
now offer **Always allow exactly this**: it allows the call and keeps
allowing it only with these exact arguments, in this scope (this agent, this
project, this goal or chat), for thirty days — a different argument still
asks. The goals trust ladder offers the same exactness when three approved
asks were identical, and a per-tool grant otherwise; never for a shell
command without an exact argument. A grant you give an agent while watching
it also covers that agent's scheduled and goal runs for the month; a run in
low trust still cannot use it. Every standing grant is listed on the
Autonomy page with its scope, uses and expiry, and can be revoked. When an
MCP pack (a connected tool server) later gains a tool that can write or
destroy, that tool is **quarantined**: it asks until you press Trust on the
Tools page, even if the pack is set to auto-approve — answering its card
runs it that once, nothing blanket does, and the card says it is new; tools that only read
are never quarantined, and the pack you installed knowingly is trusted as it
was on that day. Schedules gained the knobs a real routine needs: **skills**
for the job, a **working folder** (and a rules file there, AGENTS.md or
.ironjarvis.md, is loaded — scanned like everything else), **use the result
of** another schedule, a **pre-run script** whose output lands in the prompt
(only you can set one, never an agent), and **skip memory** for a job that
should not inherit your lessons and notes. Schedules can be edited in place.
**v1.300.0 — your Claude subscription, done properly.** When a chat or an
agent runs on **Claude (your subscription)**, Iron Jarvis now talks to the
logged-in Claude app the way a real client does instead of pasting the whole
conversation into one prompt: earlier turns are replayed as real turns, the
answer streams in word by word, tools are real tool calls (several at once
when the model wants them), pictures you attach are seen, and a follow-up
reuses the conversation the model already read, so it is faster and lighter
on your plan's limits. Stop really stops it. The model list for Claude now
comes from the Claude app itself, so a new model appears the day your
subscription gets it, with its long-context (1M) badge and a "uses credits"
note where that applies; picking **subscription** means the model your Claude
app uses by default. Subscription runs are now counted: the receipt and the
Usage page show what the same work would have cost at Anthropic's list price
(marked "list" — you are not billed it), and an agent's monthly dollar
allowance and a goal's budget count them, where before a subscription run
counted as free; runs from before this update stay free. A model your plan
pays for with usage credits is real money and is shown as spent, not "list".
Token counts now include the part of a prompt read from cache, so they read
higher on long tool work than before. If a connection drops before an answer
arrives, the error says so in plain words and can be retried; an expired
sign-in shows the sign-in steps. An HTTPS proxy set in your environment is
used. A keyless Anthropic connection keeps its older models in the list after
the live ones, and the quality dial always picks the newest of each family.
The design follows Nous Research's open-source Claude subscription plugin
for Hermes (MIT licence).
**v1.301.0 — several accounts per provider, through Iron-Proxy.** The
Connections page has an **Iron-Proxy** card. Iron-Proxy is RealDealCPA's
open-source account switcher (MIT), and it now ships inside the app: turn it on
and Iron Jarvis starts it for you (or uses one that is already running on this
PC, such as the Iron-Proxy tray app, sharing the same accounts). Add a Claude,
Codex or Grok account, or press **Use this PC's login** to bring in the one you
already use; **Sign in** opens a Build terminal that runs that account's own
sign-in. Put the accounts in the order you want them used. From then on, every
Claude, Codex and Grok call Iron Jarvis makes runs as the first account that is
free; when one hits its limit, Iron-Proxy parks it until its reset time and the
next account takes over — only ever another account of the SAME provider,
never a different provider. The switch happens only before any of the answer
has appeared; a limit in the middle of an answer ends that answer and the next
message uses the next account. When no account can take the work, the error
says why for each one (request limit, usage limit, needs to sign in) and when
the first is free again — and the chat stays on that provider (with the
model set to Auto, it may move to another provider, and the receipt says so). A momentary
Anthropic overload is not blamed on an account; it is retried as before. While
Iron-Proxy is on, Iron Jarvis never quietly falls back to this PC's own login:
if Iron-Proxy is not answering, or is an older copy (for example an outdated
tray app), the error says so. Your accounts also keep Claude, Codex and Grok
usable when this PC's own sign-in has expired. Everything else about these calls is
unchanged: tools, pictures, streaming and the cost line work as before. API-key
accounts can be managed in Iron-Proxy too, but Iron Jarvis keeps using its own
keys for API providers. With Iron-Proxy off, nothing changes.

**Several accounts in Build (v1.302.0).** Chat, agents and the Build pane's
assistant switch accounts by themselves (above). A Claude Code, Codex or Grok
session you run in a Build terminal is different: the program signs in once,
when it starts, and talks to its company directly for as long as it runs — no
app can move a running session to another account, and Iron-Proxy never
handles your login tokens. So in Build you choose the account WHEN A PANE
STARTS:
- With Iron-Proxy on, a new pane starts on the first free account of each
  provider, the same order chat uses. The pane's header shows which one
  ("Claude · Work Max"); amber means that account is parked and says until when.
- **Launch → Claude Code → as <account>** starts Claude Code on exactly that
  account. Picking the pane's own account types the command into that pane, as
  Launch always has (press Enter to start it); picking another account opens a
  new pane next to it, already running, so the pane you were in keeps its
  session.
- A pane on an account keeps your other keys: only that provider's own API key
  is left out of it (so the program uses the account, not a key), and if the
  account is your own login on this PC, Claude Code keeps all of its usual
  settings, MCP servers and trusted folders.
- On the Connections page, each account on the Iron-Proxy card has **Open in
  Build**, which does the same in one press.
- **as this PC's login** runs the program on the login you made yourself in a
  terminal, outside Iron-Proxy.
An account is fixed for the life of its pane. Each account keeps its own
conversation history, so `claude --continue` in a pane on a different account
does not see the first account's conversations.

**Signing an account in (v1.303.3).** **Sign in** on the Iron-Proxy card opens a
Build pane that runs that account's own Claude login. As soon as Claude says
"Login successful", the pane says you are signed in and the card turns the
account to Ready by itself (for the few minutes after you press Sign in it keeps
checking; **Check again** checks at once). An account Anthropic signed out on its
own (an expired or revoked login) stays "Needs sign-in" until you sign it in
again — it is never quietly marked ready. The
first time you start `claude` on a new account, Claude Code shows its one-time
welcome — that is not a second sign-in. If the Iron-Proxy running on this PC is
an older copy that Iron Jarvis started, Iron Jarvis replaces it with the current
one; if it is one Iron Jarvis did not start (the tray app, or a copy started
from elsewhere), the card names that process so you can close or update it.

**When an account runs out (v1.303.0).** When Claude Code in a pane on an
Iron-Proxy account shows its own limit message — "You've hit your session
limit", "…weekly limit", "You're out of usage credits" — a strip appears in the
pane: which account ran out, when it is free again, and **Continue on
“<next account>”**. Nothing happens until you press it. When you do:
- Iron-Proxy is told that account is at its limit — it is sent only that one
  limit message, never anything else from the pane — so chat, agents and new
  panes skip it until it resets.
- A NEW pane opens in the same folder on the next free Claude account and
  resumes the same conversation there. The pane you were in stays as it was.
- Before you press, the strip says it plainly: your conversation is COPIED
  into the other account's history and continues there, which means it is sent
  to Anthropic under that account (worth knowing between a work and a personal
  account). Nothing is deleted or overwritten in either account.
- Iron Jarvis knows exactly which conversation a pane holds when it started
  Claude there (Launch, Open in Build, Continue). If you typed `claude`
  yourself, it carries the conversation only when exactly one conversation in
  that folder just hit the limit; otherwise the new pane starts fresh and says
  why (use `/resume` there to pick one).
- Taking turns works: when the second account runs out later and you continue
  back on the first, the first account's older copy of the conversation is
  brought up to date. That older copy is kept in Iron Jarvis's trash (Settings
  → Storage, "Cleared, awaiting deletion") until you press Empty trash. Close
  Claude in the old pane on that account first: if the conversation is still
  open there, the update is refused ("the conversation file is open in another
  pane") and anything that pane writes meanwhile would not be carried.
- If the next account cannot pick the conversation up (v1.303.2), the new pane
  says so and offers **Start fresh with what we were doing**: Iron Jarvis writes
  a short handoff — your first request, the last few messages, the files
  involved — into its own folder (never your project), closes that Claude and
  starts a new one on the same account that reads the handoff first. Nothing is
  lost: the old conversation stays in both accounts' histories. If the account
  needs to sign in again instead, the pane offers **Sign in** for it. You can
  still type `/clear` to start empty.
(v1.303.1) A Build pane always runs Claude Code as a session of its own, so its
conversations are saved even when Iron Jarvis itself was started from inside
another Claude Code window.
A "Context limit reached" message is not an account limit (the conversation is
full; use `/compact`) and shows no strip. It is always the same provider, and
never this PC's own login unless you added that login to Iron-Proxy.
**v1.290.0:** the Activity page has a **Safety checks** card: Iron Jarvis
looks at what its agents did — commands, files, web requests — against a set
of safety rules (reading a password or key file, sending secrets out,
download-and-run, deleting a whole drive, disabling Defender, and more) and
shows anything worth a look. It never blocks anything; a serious finding also
rings the bell. Below the timeline, **Other agents** lists your Claude Code and
Codex sessions on this PC, read-only, with the same checks — nothing is
changed and nothing is sent anywhere. And in Build, ticking **Memory** on a
pane now lets the program you launch there search and read your Jarvis memory
(never write to it). Several rules are adapted from the open-source
agent-beacon project (MIT licence). One thing to know: anyone holding this
install's access token — including a phone you connected with it — can read
those Claude Code and Codex transcripts through Jarvis, just as they can read
everything else Jarvis holds.
**v1.291.0 (Wave 4, Packs, spreadsheets and the phone):** an Excel workbook
with empty cells, blank separator rows or an empty column A now reads in a
fraction of a second instead of minutes (or an error), everywhere a
spreadsheet is read; a slow or stuck pack (Brave, GitHub, Gmail, Obsidian)
no longer freezes chat, Build panes, the dashboard or the phone, a call the
time limit stops is really stopped, accented names and emoji from packs
arrive intact, and a pack whose helper program crashes comes back on the
next call with an error that names the pack; a job you text from your phone
no longer blocks the phone — "approve", /status and /cancel work while it
runs, and if Jarvis restarts mid-job your phone is told; and in the
installed app, Codex in a Build pane now really gets the Jarvis tools.
**v1.292.0 (Wave 5, background work you can trust):** a living document set
to refresh on a schedule (or with Run now) really regenerates instead of only
saying "done"; a two-way Slack channel added, re-tokened or removed on the
Channels page connects or stops at once, no restart; on the Webhooks page an
outbound webhook must name at least one event (saving one with none is
refused in plain words — it used to say "all" and send nothing), and any
webhook can be removed with a two-press Remove button (its secret stays in the
vault, since another webhook may share it); after "Clear generated media",
backups and their mirror copies no longer swell with the cleared files; and
Settings → Maintenance now counts session workspaces, uploads, files from
remote agents, living documents, written documents and the browser profile,
plus an "Everything else" line, so the total shown is what is really on disk.

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
