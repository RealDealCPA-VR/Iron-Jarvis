# Iron Jarvis — The Handbook

*The user guide. What this app is, how to work it daily, and the rules it
holds itself to. Current as of v1.481.0 (2026-11-12).*

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

**The five places.** Everything you do is in one of five places, listed in
the sidebar (the ☰ menu on a phone):

- **Chat** — ask anything, attach files, and let it work. Its **Chat | Code**
  switch at the top opens **Code**: coding sessions that write, run and check
  code in a folder you pick.
- **Build** — your terminals, and the **Code** view that drives your own
  Claude Code, Codex, Pi, OpenCode, Grok or Antigravity.
- **Projects** — a home for each piece of work: a brief, a folder and saved
  knowledge every chat and task inside it starts from.
- **Everything** — every other page (agents, workflows, schedules, memory,
  documents, activity…) with one line each, and **Status**: what is running
  and how the app is doing.
- **Settings** — models, connections, automation, memory, permissions,
  appearance and system. Search finds any setting by what you would call it.

**Ctrl+K** finds any page, setting or chat from anywhere.

**The three processes**

| Process | What | Where |
|---|---|---|
| Daemon | FastAPI — state, agents, tools, memory, events | `127.0.0.1:8787` |
| Dashboard | Next.js — every page below | `127.0.0.1:8788` |
| Desktop | Electron — tray, hotkeys, updater, Spotlight | wraps both |

**Hotkeys:** `Ctrl+Shift+J` toggles the window — or `Ctrl+Alt+J` when
another app holds it (the desktop app tries the two in order and retries a
taken key every 30 minutes); the tray menu and **Help › Keyboard shortcuts**
show the key that is live, or say "hotkey unavailable" when both are taken.
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
Ollama is running with a model downloaded — the empty chat shows a **Connect
a model for real answers** card with one button named for it (for example
**Use Claude (your Claude Code sign-in) for answers**, or **Use Ollama (on
this PC) for answers**, which also connects Ollama — no address to type),
and once you have started chatting the same offer sits beside the composer
as a single **Use … for answers · one tap**. Under each choice the app says
where your questions would go — "stays on this PC" or "goes to Anthropic" —
before you press anything. After the press the chat keeps saying, in the
words you pressed, **Done — Claude (your Claude Code sign-in) answers you
now.** Nothing switches on its own: whether your questions go to a cloud
service or stay on this PC is your decision. Claude Code or Codex installed
but signed out? Press **Sign in** — on the card's door or on **Settings →
Connections → Connections** — and a sign-in window (a Build pane with
the tool running) opens inside the app; finish there and the row turns green
by itself. No model at all? The card's three doors — **I already pay for
Claude or ChatGPT**, **Free & private on this PC**, **I have an API key** —
walk you through it. *(v1.353.0)*

**3. Pick a model any time.** The model is chosen in the composer (the small
model name under the box). Its menu lists every model by name — Claude Code,
Codex, OpenCode, Your model server, and your endpoints by the names you gave
them; type to find one. **Settings → Models → Default model** picks from the
same list (**Other…** still takes any model id).
**Make this my default** saves the conversation's pick as the default — with
an Undo, like every setting. "Switch model" in Ctrl+K opens the same menu from
any page.

**4. Everything else is one or two clicks away.** The sidebar holds **New
chat**, the five places — **Chat**, **Build**, **Projects**, **Everything**,
**Settings** — your recent chats and your projects. **Everything** lists every other part of
the app with one line each; **Ctrl+K** finds any page, any setting, any chat.

**5. The checklist and status.** *Connect your AI*, *Give it your first task*,
*Work with a document* and *Teach it your style* live in **Everything →
Status**, with what is running and how the app is doing. While setup is
unfinished, the top of Status holds that checklist, an **Ask Jarvis anything**
box (it opens a chat with your question written in — nothing runs until you
press send) and a few starters. Some features need a helper program — reading
old `.doc` files or scanned pages, and similar; a missing one is folded into a
single quiet **Optional extras (n)** line there, in plain words. While you
are setting up, the empty chat says it too in one quiet line — **This PC: 5
of 6 helpers ready — …** with **Details** (Everything → Status) — and once a
real model answers it offers **Connect the apps you use** (GitHub, Notion,
Slack…), which opens the Directory; that is also an optional checklist step,
*Connect the apps you use (optional)*. When
something needs you — work cut off by a restart, a background task failing, a
job waiting for an answer — one quiet line appears above the chat box, with
**Open**.

---

## The calm layout (v1.321.0)

Iron Jarvis now looks like the chat apps you already know: the conversation
is the product and everything else is found when you need it. Nothing was
taken away — every feature moved to a place you can reach in two clicks or by
searching.

- **The sidebar.** New chat; **Chat** (with its Chat | Code switch, since
  v1.407.0), **Build** (the terminals), **Projects**, **Everything**,
  **Settings**; then **Chats** (by day — on the chat screen this is the
  chat's own list, with search, rename, pin, move and delete; in its Code
  view, the coding sessions) and **Projects**. At the bottom: whether Iron Jarvis is running and its version
  (press it for Status), and **Help** (the guides, *Ask the Guide*, and
  what's new). Collapse it to icons with the arrows at the top. On a phone it
  is the ☰ menu.
- **Pins.** On **Everything**, press the pin beside any entry to put it
  under the five places in the sidebar (up to three).
- **Everything.** Four columns — **Work** (Agents, Creative, Templates,
  Documents, File Search), **Automations** (Workflows, Schedules, Rules,
  Watchers, Autonomy, Webhooks), **Knowledge** (Memory, Skills, What agents
  made, You, Train), **System** (Sessions, Activity, Usage, Model servers,
  Self-development,
  Updates, Help) — plus **Setup**, and a **Status** tab (running work,
  health, failing background tasks, recent events, the getting-started
  checklist). Since v1.353.0 each column lists its everyday
  pages first; the developer pages (Webhooks, What agents made, Secrets, Model
  servers, Self-development, Session board) sit under that column's **More**,
  and typing in the filter still finds them.
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
  off — ask **every time**, even in Don't ask, and are never offered
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
  card, the grounded folder, and the approval posture — except *Don't ask*,
  which escalates as *Ask before commands* (called *Ask first* before v1.420.0 and
  *Ask when risky* before v1.407.0). See *Agents & jobs* for the
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
objective with Allow once / Allow for this run / Deny, and nothing runs until
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

**Talking with one agent.** @-mention it in chat; its replies stay in that
chat. "Ask the Guide" on the Help page puts your question
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
  **Allow for this chat** on any one of them answers every other
  pending ask for the same tool. An ask nobody answers in 5 minutes is
  reported to the model as *paused, not run* (never as "the user declined"),
  and the run carries a **needs you** verdict at the end.
- **Grants carry, and the bell can answer a batch** (v1.232.0): a tool you
  allow "for this chat / for this run" is written to the session, so
  the next message you send to that run (and a re-run) does not ask again;
  the chat sends the tools it has armed on every message, not only the
  first. The notification bell's agent-ask row now has **Allow for this
  run** beside Allow once / Deny — one click clears every pending ask for
  that tool. A hard `deny` is never lifted by any grant. (Since v1.352.0
  the bell's row says what and why: "Your nightly-brief run wants to save a
  file", the task on a quiet "for:" line, whether it can be undone, and,
  after you answer, "Approved. The run continues · Open run" for a moment.)
- **The approval posture rides an escalation** (v1.232.0): when a chat hands
  work to an agent session, the chat's approval dropdown goes with it.
  *Ask before commands* (called *Ask first* before v1.420.0, *Ask when risky*
  before v1.407.0) lets the tools you
  armed in chat run without a pause and asks once for anything else; *Ask
  before every action* (called *Ask first* before v1.407.0) asks once per run
  for every ask-tier tool, armed or not (Allow for this run covers the rest of
  the run). *Accept edits* and *Auto* ride along only to a run you are
  watching (see *One switch* below). *Don't ask* is **never inherited**: a
  Don't ask chat's escalated run behaves as *Ask before commands* — you consented to auto-approval
  one watched turn at a time, not for a background run making a batch of
  calls while you are elsewhere.

### Sessions & the Session board
Every agent run is a **session**: live token/tool streaming, the delegation
**TeamTree**, the team's shared **blackboard**, a ledger-derived result card
(files created/changed *proven from the ledger*, never from the model's
closing paragraph), transcript export, cancel/rerun/continue. The **Session
board** (`/kanban`; called Kanban before v1.315.0) shows the same sessions as
lanes — including **Queued** when a concurrency limit is
set. Cancel genuinely stops the agent, in every lane. Since v1.352.0 the
lanes use the same status words as every other surface: **Working**,
**Waiting for you**, **Done** and **Failed**; a run you stopped sits in
Failed and its card says **Stopped** (grey, not red).

- **Waiting for you** (v1.227.0): a run paused on a permission ask sits in
  the **Waiting for you** column (called In review before v1.352.0) with an
  amber "Waiting for you · tool" chip, and
  the session page shows the same approval card chat shows. A run waiting
  for you shows in the bell and as an amber chip; an ask nobody answered is
  on the session's outcome (*needs you*) — the bell does not keep a record
  of asks that already expired.
- **The verdict is separate from the status** (v1.227.0): a finished run also
  carries an **outcome** — *Done*, *Part failed* (a call that changes
  files or state failed), or *Needs you* (an ask was never answered; the
  words since v1.358.0 — they read "Done · part failed" / "Done · needs you"
  from v1.352.0). The session header, the sessions list, the Session board card and
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
- **A run's page reads as a receipt** (v1.354.0): opening a run (from chat,
  the board or the bell) shows what it was asked as the title — the run's
  id sits behind a copy button — then what it said, what it made, which
  agent and model ran it (a custom agent by its own name, a model server
  you set up by the name the model menu gives it) and **Took 2 min 5 s**.
  The live phase and steps use the chat's words ("Planning the work…",
  "Read · read_file"), never raw ids or JSON. Time-travel, Evaluation,
  the Agent runs and Tool invocations tables and Model calls sit under one
  collapsed **Full record** chevron. **Back** returns to where you came
  from. A run a restart (an update or a crash) cut off reads
  **Interrupted** and says **Stopped when Iron Jarvis restarted**, with the
  same **Continue** / **Dismiss** the bell offers: Continue resumes the
  run's own conversation and the page follows the new run; a follow-up
  typed in the box resumes it too.

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
  `/ltm`); the Memory page's other tabs carry a one-line link **Import from
  ChatGPT/Claude/Takeout → Long-term memory** that opens it.
  A base you add on the Memory page is READ BACK the moment it is saved:
  the note under **Add source** says "Added and answering.", or why not
  ("Added, but it is not answering: folder not found: …", in the warning
  tone) — the same words chat's "add my vault as memory" gives. The
  **Obsidian vault** and **Notion database** rows in Settings › Memory are
  read when Iron Jarvis starts, so they say "after a restart"; the Memory
  page's **Add a memory base** is the door that works at once.
- **Memory profiles** (v1.406.0; called Brain profiles until v1.408.0):
  choose where your durable memory lives and how Jarvis searches, saves to
  and trusts it — see "Your durable brain: Brain Profiles" and "Memory
  profiles: set up a memory server's tools, and check it" below.
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
- **Rules** (once called Reflexes): something happens, and an agent acts.
  **Watchers** (once called Sentinels): watched folders that suggest work.
  **Autonomy**: off by default; when on, starts at *suggest* level with hard
  daily action/token caps and a kill switch.
- **A reflex rule that cannot start says so on its row and in the reply**
  (v1.231.0). When a signal matches a rule whose workflow was deleted or
  whose remote agent is gone, the rule's row on the Rules page shows
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
  offline), the Watchers page shows *root unreachable since <time>* on the
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
  auto-detect), email — with per-destination event routing. Since v1.353.0
  two-way Telegram chat is set up on one screen: after the chat ID is
  detected, tick the chat switch right under it and the allowed sender fills
  in from your own private chat (nothing to type, no Advanced fold).
  Since v1.400.3 the destination's alert boxes (under Advanced, "Send this
  destination") include **The night shift's summary**. With every box ticked a
  destination gets every alert. Untick some and it gets only the ones still
  ticked; before, any smaller set also silently dropped the night summary.
- **Editing a destination keeps what is saved, and two-way says why it cannot
  hear you** (v1.427.6). Edit used to empty every box it could not show — the
  Slack bot and app tokens, the allowlist — and saving then threw them away, so
  every edit meant pasting the tokens again. Now a box left blank keeps what is
  saved (secret boxes say **Saved — leave blank to keep it**, the allowlist says
  how many are saved), typing replaces, and **Remove the saved one** under a
  saved field takes it out. Two-way Slack needs three things to hear you: the
  **App-level token** (xapp-…, with connections:write), the **Bot token**
  (xoxb-…, to answer), and **your Slack member ID in the allowlist** (in Slack:
  your profile picture → Profile → ⋯ → Copy member ID) — with the allowlist
  empty, Jarvis does not listen at all. When one is missing, the destination's
  row and the save say which, in plain words. Slack can now also hold a real
  conversation with Jarvis: tick **Chat with Iron Jarvis**. The Slack app
  manifest turns on the app's **Messages tab**; an app made from an older
  manifest needs it switched on once in Slack: App Home → Messages Tab → tick
  **Allow users to send Slash commands and messages from the messages tab**.
- **Writing to Jarvis in Slack gets an answer, wherever you write it**
  (v1.444.0). Before, Jarvis answered only a **direct message** to the Iron
  Jarvis app, and the Slack app was set up to send it direct messages only, so
  anything you wrote in the alerts channel (#iron-jarvis) never reached Jarvis
  at all. Now, for someone on the allowlist: a direct message is answered in
  the direct message; a message in the destination's **own channel** (the one
  the alerts go to) is answered **in a thread** under it (anyone in that
  channel can read the answer, so use a direct message for private things);
  and an **@Iron Jarvis** mention in any other channel is answered
  **privately, in your direct messages**. The answers come from Chat with your
  default model, and the conversation shows in Chat on the PC. Jarvis finds
  the channel by its name and joins it by itself when it can (in a private
  channel, type `/invite @Iron Jarvis`). **A Slack app made before v1.444.0
  needs one update:** at api.slack.com/apps, open the app → **App Manifest**,
  paste the manifest from Settings › Notifications → Add a destination → Slack,
  **Save**, and reinstall the app when Slack asks. The destination's row says
  what Jarvis has heard: "Connected to Slack, but Slack hasn't sent Jarvis any
  message yet" means the app still needs that update.
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
Build: live terminals with AI assist per terminal, for your own coding tools
(Claude Code, Codex, any command line). **Coding sessions**, where Jarvis
works as a coding agent in one of your folders (v1.369.0; called the IJ
harness before v1.389.0), are on the chat page behind its **Chat | Code**
switch since v1.407.0; a Creative gallery for
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
  Switching between Chat and Code on the chat page keeps a coding session's
  terminal the same way. A Claude or Codex
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
  restart appear on **Everything › Status** and in the bell with a
  **Continue** button,
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
- **Settings → System → Access token** (v1.232.0): inside the desktop app the
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

**What your browser downloads, Jarvis knows about (v1.444.0).** When a file
finishes downloading in your browser, the add-on tells Iron Jarvis where it
landed, and Jarvis keeps a list of the recent ones (the last 200: name, where it
is now, the site it came from, size, when; never the file's contents, and never
the part of the link after "?", where download links carry passwords). Ask in
chat ("what did I download today?", "rename the statement I just downloaded to
October 2026.pdf", "move my downloads into the Clients folder", "copy the
invoice I downloaded into this project"). Jarvis can **rename**, **move** or
**copy** a downloaded file, and it **asks you first every time**. It only works
on files your browser downloaded, never any other file on the PC; it never
overwrites (a name that is taken gets " (2)"); it won't use a network share or
a protected folder; and every change has **Undo** (an undone copy goes to Iron
Jarvis's trash, never deleted). To **change** a downloaded file's contents,
Jarvis copies it into the chat's work folder and edits the copy there, so the
download stays as it came. The Browser card lists the most recent downloads,
with **Show in folder**.

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
the icon stays on the toolbar where one click opens the sidebar. That icon is the
app's own "iU" tile (since v1.400.6), the same picture as the desktop app, and it
is what the puzzle-piece menu, the sidebar's header and your browser's add-ons
page show too — reload the add-on once after updating to see it. The Browser card
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
**Allow**, **Allow all page actions** (which turns the switch on for every
tab) and **Deny**. The switch
is remembered in your browser. What it does NOT cover, on purpose: a payment or
password field, anything with "delete"/"remove"/"cancel" in its name, a page
that tried to give Jarvis instructions, and a control Jarvis cannot read still
stop and ask — those are decisions that stay yours. *The work is folded:* while
Jarvis works a page, the steps ("Clicking 'Next'…", "Typing into 'Search' —
done.") sit behind one **Working** line you can expand; when the turn ends it
reads like "4 steps · 1 failed · 12 s" (amber when a step failed; no time when
the sidebar did not send that task itself). A notice that a different model
answered stays visible.

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

**One card for a whole form, and Point at it (v1.400.9).** Two ideas borrowed
from another browser agent's design. *One card for a whole form:* when Jarvis
fills in a form it now sends all of the fields as one list, and the sidebar (or
the chat page) shows ONE card that lists every step — "type 18 characters into
'Email'", "click 'I agree to the terms'" — never the characters themselves.
**Allow** runs exactly that list, in the tab it started in, and stops at the
first step that does not work or that leaves the page (a Submit that opens the
next page); Jarvis is told which steps are done, so it never types a field
twice. Nothing in the list is judged more leniently than the same step on its
own: a payment or password field, a "delete"-shaped button or a page that tried
to instruct Jarvis still stops at that step for its own card. Each step is sent
exactly as Jarvis wrote it: if one step changes the page so that a field Jarvis
read earlier is no longer the same, the list stops there and Jarvis reads the
page again, rather than typing into whatever box has taken its place. A step
you set to **Deny** refuses the whole list. *Point at it:* the
crosshair beside the model icon lets you click the thing you mean on the page —
a button, a field, a table. The page never receives that click (a clear layer
covers it while you point, so even a payment box embedded in the page is not
pressed); **Esc** cancels, and pointing stops by itself after a minute. The
sidebar shows "Pointing at button 'Apply coupon'" above the box, your next
message carries it, and Jarvis is told exactly which element "this" is and how
to reach it. What is read is what Jarvis reads on a page anyway — the element's
kind, its label, the visible words around it, and the page's address without
its query string — never what is typed in a field. The **×** forgets it before
you send; pressing **Send** also ends a pick still waiting for your click.
Pointing does not start while the page has a pop-up, dialog or full-screen
view open (they sit above anything the add-on can draw) — the sidebar says so;
close it and point again. Pointing needs the add-on's site access, like
reading a page.

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
panes that no longer exist (their view and chat thread used to stay in the
browser forever), and the Agents page's job card opens on the
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
honest limit: out of the box Iron Jarvis listens on this machine only
(127.0.0.1) and answers only requests addressed to it, so a remote agent
running on THIS machine can message back as is, while one on another
machine cannot until you choose to open a door — the token box says so and
names the two ways: turn on **Let other computers on my network reach this
Iron Jarvis** in Settings › Connections and restart Iron Jarvis (since
v1.358.0; Windows may ask once to allow it on your network, and every
request still needs its token — if that question was dismissed, other
computers stay blocked until you allow Iron Jarvis in Windows Security ›
Firewall & network protection › Allow an app through firewall), or put a tunnel or reverse proxy in front of
127.0.0.1:8787 and paste its address as the inbound URL. Nothing opens on
its own. Files a remote sends land under the app's remote inbox folder,
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
- **Simple and Advanced modes, v1.318.0–v1.320.0.** For a few versions the
  menu, the home page and Settings had a short "Simple" form and a full
  "Advanced" one. The calm layout (v1.321.0) replaced both: there is one
  sidebar with the five places, Settings in seven groups with a search box,
  and **Everything** for every other page.
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
  - **From chat** (v1.442.0). Ask for it in words: "change the theme to
    something green and calm", "make the app purple", "use a dark blue
    theme". Chat picks the colours (a main colour, an optional second one,
    the background feel, dark or light) and they are applied at once on the
    screen you asked from, with an **Undo** on the reply. It is the same
    theme maker underneath, so every word stays readable; the theme appears
    among your themes as one entry (named as chat named it, or "Theme from
    chat") and a later chat change replaces it rather than adding another —
    it never takes one of your 12 places. The last one set wins: asking for
    a built-in theme (or picking one on screen) replaces chat's colours, and
    asking for colours replaces the built-in theme. Undo goes back to what
    the screen showed before.
- **Phones.** Pages no longer slide sideways, the title bar's buttons no
  longer overlap, and search icons sit inside their boxes.
- **Plain words.** Sessions say who started them in words ("Schedule ·
  nightly-brief", "Mission", "Queued job") — the exact origin is still on
  hover. Approval cards say what the action will do; the tool's own name is
  in the details.
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
   Automation, "Time limit for one step (seconds)" — "Tool call deadline"
   before v1.358.0 — default 600 s): a wedged tool is stopped,
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
   "Iron Jarvis isn't responding" (the banner said "Daemon offline" before
   v1.354.0).
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
   that keeps failing says so with its last error — named on **Everything ›
   Status** and, once it has been failing
   for five minutes, as a bell item ("Background task fleet has been failing
   for 12 min — …"). The hero never says "All systems nominal" over a dead
   loop. A tool pack (MCP server) that did not start is treated the same
   way: its row under **Tools → Connected packs** carries an amber "Didn’t
   start: <reason>" line (the exact error, e.g. `npx` not found) with a
   **Retry** that reloads it live, Everything › Status says "1 thing needs
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
  the turn instead of failing over. Press **Sign in to Claude Code** (or
  **Codex**) on **Settings → Connections → Connections** (v1.353.0): a
  Build pane opens with the tool running on this PC's own login — type
  `/login` there for Claude Code (Codex shows its own sign-in screen) and
  finish in your browser. The row reads **Installed · not signed in** until
  then and turns green by itself, and the composer says so before you
  type. Installed is not
  connected: the app asks the CLI itself (`claude auth status`, `codex
  login status`) and re-checks every couple of minutes.
- **"Iron Jarvis isn't responding"** (it said "Daemon offline" before
  v1.354.0; your chats and files are saved while it shows, and when the
  service answers again the banner says **"Iron Jarvis is back."** for a
  few seconds — plus "Anything it stopped is in the bell." when a restart
  cut work off) → the desktop app restarts a crashed daemon by
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
- **"Starting Iron Jarvis…"** (v1.407.0) → a page that loads while Iron
  Jarvis is still starting (a browser tab, a window opened during a restart)
  says this, calmly, until Iron Jarvis first answers; only after a minute and
  a half with no answer does it say **isn't responding**. A start also no
  longer waits for a Build terminal whose shell is slow to open: after a few
  seconds Iron Jarvis is ready, and that terminal joins the Build page by
  itself the moment its shell starts. (It used to hold the whole start, and
  on some updates the app restarted itself after 90 s because of it.)
- **Build stuck at "Attaching to sessions…", then "Iron Jarvis isn't
  responding"** (fixed in v1.407.1) → with PowerShell installed from the
  Microsoft Store, each terminal used to make one start attempt that Windows
  always refuses for a Store program (putting it in a group Iron Jarvis uses
  to tidy up what a terminal leaves running), then try again; with two
  terminals doing that at once, Windows sometimes never finished, and Iron
  Jarvis stopped answering until the desktop app restarted it. Terminals now
  skip the refused attempt for Store programs and start them one at a time.
  Nothing you could see changes: Windows never allowed that group for these
  terminals. If Iron Jarvis ever freezes again, it now writes where it is
  stuck in its log (Settings › System › Open logs) before it is restarted:
  look for a line starting "event loop frozen for".
  Since v1.409.1 a freeze that stops every part of Iron Jarvis at once (it
  happened again on the first start after the v1.409.0 update, and nothing
  was written) is still recorded: the stack of every part of the app goes to
  **daemon-freeze.txt** beside the log, about 30 seconds into a freeze, and
  again every 30 seconds while it lasts. The file holds Iron Jarvis's own
  program file names (which include your Windows user folder), nothing you
  typed. Iron Jarvis also reports when every worker that answers requests is
  busy.
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
- **"Iron Jarvis did not answer within 90 seconds … Press Retry" after an
  update** (v1.400.4) → if Iron Jarvis is still running but has not answered
  by then, the app now restarts it once by itself and waits again before
  showing this. You see the message only if that second try also fails. A
  start that has not finished after 30 seconds now writes to `daemon.log`
  which step it is stuck in and where its code is waiting (code locations
  only, never your files or data), so the cause can be found.
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

## A task's required files are checked before the reply ends (v1.481.0)

When you give Jarvis a task in a chat that has a working folder, and your
message lists the files the task must produce (a "Deliverables" or "Success
criteria" list, or "write `OUTPUT/report.md`"), Jarvis now checks the work
itself instead of trusting the model's last words.

### What changed

- **The files are checked on disk.** Before the reply ends, Jarvis looks for
  each required file: it must exist, not be empty, and open (a workbook
  loads, a Word or PowerPoint file is whole, a JSON file parses, a text file
  reads). If one is missing and there is still room in the reply, the model
  is told once what is missing and asked to write it directly. When a few
  steps are left, it is told what is still missing.
- **A note under the reply says what really happened**, drawn from the
  disk, never from the plan: which files are done, which are not, which
  steps failed and were not done again, helper scripts that were written and
  never run, writes Jarvis refused, and anything that changed outside the
  folders the task may write. If a required file is missing, the note says
  "The task is NOT complete", whatever the reply says.
- **Where the task may write is enforced.** When your message says so
  ("Create files only in `OUTPUT/`", "the `INPUT/` folder is read-only",
  "do not modify `INPUT/`"), Jarvis refuses a write anywhere else before
  anything runs, and says why. A required file always goes in the folder you
  named. A helper script never goes into your input or output folders:
  Jarvis gives the model a scratch folder outside the task for that. With no
  such rule in your message, nothing about where Jarvis writes changes,
  except that when every required file goes into one output folder (a folder
  named `OUTPUT`, `results` or `deliverables`), that folder is the place to
  write.
- **Paths mean the task folder.** If the chat's folder is the task's own
  `INPUT` folder and your message says "`OUTPUT/`", Jarvis now works in the
  folder above it, so `OUTPUT/` is the real output folder, not a new
  `INPUT\OUTPUT`.
- **A failed step is a failed step.** A command that printed a Python error
  (a traceback, "No module named …") counts as failed even when it hid its
  exit status, and the model is told so in the step's own result, in every
  chat.

### What it does not do yet

- It works in the chat (Chat › Chat) when the chat has a folder. Coding
  sessions keep their own finish check; agent runs are not checked yet.
- It reads only your own message. A task described only in a README that
  Jarvis reads is not turned into a checklist; paste the deliverables into
  your message.
- A program that a command starts can still write anywhere it is allowed
  to. Jarvis cannot stop that ahead of time; it notices it afterwards and
  says so in the note.

## What Jarvis looks up, it can now use (v1.479.0)

This is the second step of the feature lookup from v1.478.0. It is still
**off by default** (Settings › Models › Feature lookup), and with it off
nothing in chat is different.

### What changed

In v1.478.0 Jarvis could look up what Iron Jarvis can do, but only tell you
about it. Now a tool the lookup finds joins the reply, so Jarvis can do the
thing in the same answer.

Example, typed in Spanish with approvals set to *Don't ask*: "Crea un nuevo
agente llamado Penny que vigile mis recetas de medicamentos." The word
patterns bring no tool for that sentence. With the lookup on, Jarvis looked
up "create a custom agent", the agent tool joined the reply, and its
approval card appeared.

### The safety rules

A tool your own words bring is allowed by your words. A tool Jarvis looked
up is not: a web page or a document could tell Jarvis to look something up
and use it. So:

1. **A change found by lookup always shows a card first.** This holds in
   every approval setting, including *Don't ask*, *Accept edits* and *Auto*.
   The card says "Jarvis found this tool by looking it up, so it asks every
   time before using it". It offers **Allow once** and **Deny** only, and
   pressing Enter never allows it: you have to click.
2. **A tool that reaches outside this PC is treated the same way**, even one
   that only reads: web search, fetching a page, searching a memory base
   that may be on another machine. It could carry what the reply has read
   off this PC.
3. **A tool that only reads, and stays on this PC, joins and runs** with no
   card, like the readers chat already brings (searching past chats, listing
   a folder, reading settings). Such a tool is never pointed at a folder on
   another computer.
4. **A reply that has read flagged content gets no new tools.** If a document
   or page in the reply was flagged as carrying planted instructions, the
   lookup still answers, and nothing joins.
5. **Only tools chat could already bring can join.** The lookup widens the
   words that reach a tool. It does not widen the set of tools. Tools that
   run commands or scripts, and the browser's click and type tools, are
   never brought by a lookup, and a script cannot use a tool a lookup
   brought as ask-first.
6. **Your own settings hold.** A tool set to *Never run* does not join. With
   Auto tools off, the lookup answers and nothing joins. In a Local only
   chat, an internet tool does not join. A protected setting always shows
   its card.
7. **Bounded.** At most 3 tools per lookup (the settings tools count as one
   together), and 2 lookups per reply bring tools.
8. **Where nobody can be asked, nothing that needs asking joins.** On the
   phone and for programs that call Iron Jarvis, only local readers join.

If you already answered "Allow for this chat" for a tool on an ordinary
card, that answer still stands when a lookup brings the same tool later.

### Measured with a real model

Every request of the held-out set from v1.478.0 was sent as a real chat
message to a test copy of the app, answered by Claude Haiku, once with the
lookup off and once with it on. Every approval card was declined, so nothing
that asks was changed. 161 requests ran (3 about backup folders were left
out for safety); 135 of them ask for a tool.

| | Lookup off | Lookup on |
|---|---|---|
| Jarvis called the right tool | 28 of 135 (21%) | 53 of 135 (39%) |
| The same, English | 27 of 111 | 44 of 111 |
| The same, other languages | 1 of 24 | 9 of 24 |
| The right tool was within reach | 42 of 135 (31%) | 84 of 135 (62%) |
| A looked-up change that ran without its card | not possible | 0 |
| Typical time for a reply | 6.5 s | 8.5 s |

26 requests were gained and 1 was lost. Jarvis looked something up in 57 of
the 135 replies.

What this does **not** show:

- **The plan's target was 95%. This is 39% (62% within reach).** Two things
  hold it back: the model looks things up in fewer than half of its replies,
  and the list does not always put the right entry near the top.
- One model, one run. A call counts as "called" even though its card was
  then declined.
- The run was made before the second reviewer's fixes below. Those make the
  cards stricter, not looser, so the count of right tools called should not
  fall, but it was not measured again in full.
- No local model was measured. The owner's model server refused the test
  for want of a key.

On a second, fresh set of 104 requests written after the last change (new
writers, and Italian), the right entry was among the lookup's first five
answers for 73 (70%), against 75% on the first set.

### What the first live runs changed

Scripted tests had all passed. The real model then showed three things, each
fixed as a general rule:

- It searched in the user's language. The list is in English, so the tool
  now says "English, whatever language the user writes in", and tells Jarvis
  when a search did not look like English.
- The lookup named an agent's own tool ahead of the chat tool for the same
  thing, and Jarvis gave up. Tools that no lookup can bring are now listed
  last.
- Shown the lookup tool alone, Jarvis said "I have no access to your earlier
  chats" without looking. While the lookup is on, a short note in Jarvis's
  instructions says what Iron Jarvis has tools for, and to look before
  saying it cannot do something.

### Checked by a second reviewer before release

A separate reviewer attacked the change and wrote a failing test for each
thing it broke. It found 13 defects; all are fixed. The ones that mattered:

- A web tool brought by a lookup could be run from inside a script with no
  card. A script can no longer call a tool the reply holds as ask-first, a
  script cannot make lookups, and a reader brought by a lookup never makes
  the script tool join.
- A memory search brought by a lookup could send Jarvis's words to a memory
  base on another machine with no card. It now asks first.
- A file reader brought by a lookup could be pointed at a folder on another
  computer. It is now refused.
- A protected setting found by lookup could change with no card when that
  tool had been allowed for the chat. It now always shows its card.
- Pressing Enter could approve a looked-up change while typing. Enter never
  does now.
- A reply stopped half way could leave its lookups for a later reply with
  the same id. They are dropped.

### What it does not do

- **It does not cover connected apps or your own command tools.** Those come
  in a later step.
- **It does not work in coding sessions, agent runs or the browser sidebar.**
  They keep their own tool lists.
- **A plain text file is not checked for planted instructions.** Documents
  read with the document reader are checked, and a flagged one stops new
  tools (rule 4). A plain text file read with the simple file reader is not
  checked. This is older than this change. Rule 1's card is what protects
  you there.
- **A match can still be wrong.** Jarvis is told to use only a tool that
  fits the request.
- **A document read by a tool the lookup brought may be sent to the model
  that reads pictures** (for a scanned page), as it is when your own words
  bring that tool.

## Jarvis can look up what it can do (v1.478.0)

This is the first step of a larger change. It is **off by default**, and with
it off nothing in chat is different.

### The problem, measured

Before Jarvis answers, Iron Jarvis decides which tools the reply gets by
matching your words against hand-written patterns. When your wording fits no
pattern, Jarvis has no tool for the request and says it cannot help, or
reaches for the wrong tool.

We measured how often that happens on wording nobody had written a pattern
for: 164 everyday requests for 47 things Iron Jarvis can do, written by a
separate model that had never seen the patterns, with every label checked by
hand.

| | Right tool reached |
|---|---|
| All 164 requests | 50 (30%) |
| English | 48 of 135 |
| Spanish, French, Portuguese, German | 2 of 29 |

68 of the misses were offered no tool at all. For example, "I'd like a new
helper called Penny who looks after my medication schedule" got no tool that
makes an agent.

### What is new

Jarvis has a new read-only tool that looks up what Iron Jarvis can do. Jarvis
describes the request in its own words, and Iron Jarvis answers from one list
of everything it has:

- every built-in tool, with whether it asks you first;
- every setting, with where to change it by hand;
- the steps that need a page (a sign-in, the browser add-on);
- the things Iron Jarvis does not offer, said plainly.

On the same 164 requests, with a model writing the search words, the right
entry was in the first five answers for **123 (75%)**: 96 of 135 in English
and 27 of 29 in the other languages. Counting a request as served when
either the patterns or the lookup find the right thing gives 134 of 164.

### What it does not do yet

- **It only answers.** A tool the lookup finds does not join the reply. If
  Jarvis did not already have that tool, it can tell you Iron Jarvis can do
  the thing and name the setting or page, but it cannot do it in that reply.
  Joining the found tool, behind the usual approval card, is the next step.
- **It does not list connected apps' tools or your own command tools.** Their
  descriptions are someone else's words. They come later, with a safety
  check.
- **A match can be wrong.** The lookup returns the closest entries it has,
  even for a request Iron Jarvis has nothing for. Jarvis is told to use
  only a line that fits.
- **The 75% is a search result, not a count of finished requests.** No real
  chat was measured: a model wrote search words for each request and the
  list was searched with them.
- **One writer wrote the test requests.** A second writer would move the
  numbers a few points.

### Turning it on

**Settings › Models › Feature lookup**, or say "turn on capability search"
in chat. While it is on, every chat reply carries the
lookup tool, and a reply that used it shows the step "Looked up what Iron
Jarvis can do". It reads only: it changes nothing and reaches nothing outside
this PC. If you set the tool to *Never run*, it is not offered.

### Checked by a second reviewer before release

A separate reviewer attacked the change and wrote a failing test for each
thing it broke. It found 11 defects; all are fixed. The ones that mattered:

- A command tool you made yourself was in the list, presented as Iron
  Jarvis's own.
- Four lookups in one reply made another tool (the script tool) join it.
- With the setting off, its own name made ordinary sentences such as "let
  Jarvis look up the rates" bring the settings tools. The setting was
  renamed.
- "Settings" and "setting" were different searches.
- A lookup that failed, or had no words to search with, was answered as
  "nothing matches", which Jarvis would have passed on as "no such feature".

## Build › Code calls a finished command a command (v1.477.2)

In Build › Code, a shell command that ended is no longer announced as "A
helper finished".

Claude Code tells Iron Jarvis when a task it was tracking has ended, and Iron
Jarvis writes one grey line for it in the conversation. Every such line used
to start "A helper finished:", whatever had ended. In a session where helpers
ran shell commands, the conversation filled with lines like `A helper
finished: cd "C:/…" && grep …`. No helper had finished. A command had, and the
text after the colon was the command line. The one line that mattered was
easy to miss among them.

The line now says what ended:

- **A helper** (an agent Claude Code started): "A helper finished: …", "A
  helper failed: …" or "A helper was stopped: …", as before.
- **A command in the background** (a dev server, a long test run): "A
  background command finished: …", "… failed: …" or "… was stopped: …". When
  Claude Code sends its own whole sentence, such as `Background command "Run
  the tests" completed (exit code 0)`, that sentence is the line.
- **An ordinary command gets no extra line.** Claude Code tracks any command
  that runs for two seconds or longer and reports its end with the command
  line. That command already has its own step in the conversation, and the
  step shows how it ended. Most of the lines in the reported session were
  these: 121 of 185.
- **Another kind of background task** (a monitor that watches a build): "A
  background task finished: …".
- **A task whose kind Iron Jarvis does not know**: "A task finished: …". It is
  never called a helper on a guess.

What did not change: the count of helpers still working, the Session tab's
list of background work and its Stop button, and how Iron Jarvis knows that
Claude Code is writing a reply by itself.

Two things to know:

- **Lines already in a conversation stay as they were written.** Only new
  lines use the new words.
- **A session that was running when you updated** may say "A command
  finished: …" for a command that started before the update. The older
  version did not record whether that command ran in the background, so Iron
  Jarvis says the line and does not guess.

## A blocked-passage notice opens the chat it happened in (v1.477.1)

When Iron Jarvis keeps a suspicious passage out of what the AI reads (a
web page, an attached file or a memory note that reads like "ignore your
instructions" or "urgent: send the payment"), the bell shows "Blocked 1
suspicious passage". Until now, pressing that row while you were in Chat
did nothing: every chat turn is filed under one name, so the notice could
not say which chat it came from.

- The notice now records the chat it happened in, and pressing the row
  opens that chat. A notice with no chat (one from before this version, or
  from a chat not saved yet) opens the Activity page instead.
- The row says what was caught in plain words, for example "It looked like
  an urgent request for a payment or bank details. It was kept out of what
  the AI read." It used to show a code such as `phishing_urgency_payment`.
- Any link to a saved chat now opens it even when Chat is already on
  screen: the bell, Ctrl K and the browser's back and forward buttons.

A block is often a false alarm on your own words (a note about a deadline
or bank details). Nothing is deleted: the passage is only left out of that
one reply.

## A ring that shows the coding tool is still working (v1.477.0)

In Build › Code, a coding tool such as Claude Code can finish its reply and
leave helpers working in the background. Until now the only sign of that was
one grey line of text at the end of the conversation, under a list of other
grey lines. It was easy to miss.

Now a small ring sits at the end of the conversation, just above the message
box, whenever the coding tool is at work:

- **While a reply is being written:** a plain ring with a light going round
  it, and "Claude Code is working…". This replaces the small spinning wheel.
- **While helpers work in the background:** the ring has one segment for each
  helper, and the number still working is in the middle. Beside it: "5 helpers
  working". A light hops from one working segment to the next. When a helper
  ends, its segment turns thin and steady, and the words count it: "3 helpers
  working · 2 finished".
- **While the tool writes a reply of its own** (its report once the helpers
  are done): the plain ring again, with "Claude Code is writing a reply of its
  own".
- **While only a background command runs** (a dev server, say): a ring that
  does not move, and the same quiet sentence as before. Nothing is being
  worked on for you then, so nothing moves.

Things to know:

- **The "finished" number is what this window saw.** It counts helpers that
  ended while the session was open on your screen. A helper that ended before
  you opened the session is not in it, so the number can be lower than the
  real one. It is never higher. It starts again when no helper is left.
- **It stays in view.** If you scroll up to read, the ring stays just above
  the message box, with a soft pill behind it so you can read it over the
  text. At the end of the conversation it has no box around it.
- **Nothing moves when it comes or goes.** Its place at the end of the
  conversation is always kept. That adds about 30 pixels of space above the
  message box, in every session, working or not.
- **It costs almost nothing over a long session.** The light moves in steps,
  less than twice a second, instead of turning smoothly. Measured in Edge with no
  window on screen, three rounds of 20 seconds each: with the ring standing
  still the browser's graphics process used 1.56% of one processor core; with
  the light hopping as built, 1.90%; with the same light turning smoothly,
  23.37%.
- **It stands still** when Windows is set to show less animation, and while
  the window is hidden. The ring, its number and the words still show.
- **A screen reader hears the same sentence as before** ("5 helpers are still
  working in the background. Their report will appear here when they
  finish."). Resting the pointer on the ring shows that sentence too.
- **Build › Code only.** Chat › Code keeps its own "Working for…" line.

## Skills that get used, one approval for many changes, faster scripts (v1.476.0)

### A skill you saved is used without typing "/"

A skill used to reach a chat only when you picked it with "/". A skill nobody
remembered to pick did nothing.

Now, when your message fits a skill, Jarvis is shown that skill's name and its
one-line description, and can read the skill before it answers. You see it as
a step in the reply ("Loaded a skill"). Nothing is chosen for the model: it is
the short list a person would glance at.

Which skills are offered:

- **Skills you made in Iron Jarvis** (on the Skills page, in chat, by
  recording a task) are offered when one telling word of your message is in
  the skill's name or description.
- **Skills you keep in another program's folder** (Claude Code, Codex) are
  offered only when your message uses the skill's name.
- **Skills that came with another program** (its own PDF, Word or browser
  skills) are never offered this way. They are written for that program's
  tools, and Iron Jarvis has its own tools for the same work. You can still
  pick one with "/".

A message that fits no skill changes nothing. With **Auto tools** off, or with
"load a skill" set to *Never run*, no skill is offered.

It matches by words, so it can miss. "Can my client deduct a new truck?" does
not find a skill called "tax advisement" unless the skill's description says
"deduct". When you make a skill in chat, Jarvis now writes the description
with the kinds of request it is for.

Measured on the owner's PC (43 skills from other programs): of 45 everyday
messages, 4 were offered a skill; the first version of this rule offered one
on 11 of 40, most of them wrong. Of 16 messages written to fit a skill, 11
were offered the right one.

### Change a skill from chat

"Update my month-end skill to also confirm payroll is paid" now shows a card
with the change. After you allow it, the skill is changed and the version
before is kept in the skill's own folder (the last 20). This works for your
own skills only, on the chat page.

### One approval for many changes

Renaming forty statements used to be forty steps, or one long command with no
Undo. Now Jarvis can plan the changes and ask once:

- The card lists **every** change, one line each ("Rename statement_01.csv to
  2026-01-statement.csv"). One **Allow** covers exactly that list.
- **Each batch is asked about on its own.** The card offers *Allow once* and
  *Deny* only. There is no "Allow for this chat", because the next batch holds
  different changes. With approvals set to *Don't ask*, a batch runs without
  its card.
- **What runs is what the card showed.** If the list changes after the card
  was shown, nothing runs and Jarvis has to ask again.
- Each change is still its own entry in Activity with its own **Undo**.
- If any change in the list is not allowed (a protected file, a tool you set
  to *Never run*), **nothing** is changed and the answer says why.
- A batch can hold file changes that can be undone: rename or move, write,
  edit, Word, PDF and Excel edits, redaction. It cannot send anything, delete
  for good, or run a command.
- A batch never changes one of Git's, Iron Jarvis's or a coding tool's own
  files (`.git`, `.ironjarvis`, `.claude`, an `AGENTS.md`), never replaces a
  file by renaming another file over it, and never puts a password on a PDF.
  Each of those is made as a single change, with its own card.

Chat now also offers the rename tool when you ask to rename or move files.
Before, chat had only the command line for that.

Two rules for a rename, in chat and in agent runs:

- **A rename onto, or away from, one of Git's or Iron Jarvis's own files asks
  every time**, the same as writing that file does.
- **A file replaced by a rename is kept.** When a rename is told to replace a
  file that is already there, the replaced file is moved to Iron Jarvis's
  trash folder first, and the answer says where it is. Undo moves the renamed
  file back; it does not put the replaced file back for you.

### Results go to the file without being typed again

When a script has worked out the rows for a workbook, the model used to print
them and type every one again into the file. That was slow, and it was the one
step where a figure could change on its way to the file.

Now the script keeps the rows, and the model's file call just names them.
The card shows what they are ("641 rows, up to 4 columns; first row: File
Name, Date, Payee, Amount"). What is written is exactly what the script
computed. Such a card is never offered as "Always": the same name can hold
different data next time.

**A request for a new file gets the file tool, however you word it.** "Save
the totals to totals.xlsx" always gave Jarvis the tool that writes a file.
"Put every payment into one workbook called payments-check.xlsx", "merge the
statements into one Excel file" and "put the totals in a Word document" did
not. In tests of this version with a real model, Jarvis then tried the command
line, asked for approval up to five times, and twice saved nothing. Two fixes:

- Jarvis now reads those requests too: a gathering word (put, merge, combine,
  collect, compile, list and the like) and then "into a" or "into one"
  workbook, spreadsheet, document or file, or "into" a file's name. "The
  workbook" and "my spreadsheet" are files you already have, and are left to
  the tools that edit them.
- If a reply still starts without the file tool and a script keeps a result
  for a file, the file tool joins the reply at that point, and the reply gets
  the longer step limit a document reply has.

Neither happens with **Auto tools** off, or with "write a document" set to
*Never run*.

**A table a script kept is written as a table, with real numbers.**

- A script that read CSV files holds every cell as text. Written to a
  workbook, clean numbers and dates (2026-01-02) now become values you can
  add up in Excel. A code with a leading zero (02134) and a number longer
  than 15 digits (a card or account number) stay text, so nothing is rounded
  or shortened.
- A table kept as "header" plus "rows" is now read as a table. In a test on
  the packaged app a script kept its table that way, and all 640 rows went
  into one cell while the reply said "640 rows".
- Content that cannot be read as rows is refused, and no file is written.
  Before, it was written into one cell as text, which looks like a finished
  file and is not one.
- Lines that were never split into columns are now said. In the same test a
  script split each line on tabs (the files use commas), and the workbook
  was one column of "2026-01-01,Vendor 42,260.39" under a reply that said
  "3 columns". Now the script's own result says "641 rows of ONE cell each,
  every cell holding 2 commas", and a workbook written that way gets a
  warning that tells Jarvis to split the lines and write the file again. One
  column of names, numbers or ordinary sentences is not flagged.
- Reading a CSV file now says that its cells are separated by commas. Jarvis
  shows the cells with tabs between them, and that is what misled the model.
- The words that point at a kept result are never written as the file. In
  another test Jarvis put those words into the workbook instead of the 640
  rows they point at, and the reply said "640 rows". That call is now
  refused, nothing is written, and Jarvis is told the call to make instead.
- A stand-in typed beside a kept result is ignored. Jarvis often names the
  kept result and also types a word such as "test", or repeats the heading
  row. That used to be refused, which cost a step in about one reply in
  three. Now the kept result is written. Real text typed beside a kept
  result is still refused, because the two are never joined by guesswork.

**A total Jarvis adds in its head is caught.** In two tests Jarvis typed
sixteen right totals from a script into a Word file and added a grand total
of its own, wrong both times. Telling it not to did not stop it. Now Iron
Jarvis remembers the figures a script or the calculator produced in the
chat. When a file holds at least three of those figures and another money
figure beside them, the file tool's answer names that figure and tells
Jarvis to work it out with a script or the calculator and write the file
again. A figure you gave Jarvis yourself is left as it is, and so are dates,
counts and percentages.

You are told too. If the file still holds such a figure when the reply
ends, a note under the reply names the file and the figure: "totals.docx
holds a figure Jarvis typed itself: 792,184.46. No script or calculator
worked it out, so check it before you rely on it." In tests where Jarvis
was told to add sixteen totals up by hand, it once checked its total with
the calculator after the warning, and once ignored the warning and answered
"Done" over a wrong total. The note is there for the second case.

### Scripts in more places, and offered when they would help

- **Offered by what is happening, not only by your wording.** After four reads
  of the same kind in one reply, the script tool joins the reply so the rest
  can be one step.
- **"Merge", "combine" and "consolidate" many files** now bring the script
  tool, the way "every" and "all" did.
- **Agent runs have it too**: schedules, goals, workflow steps, missions and
  tasks handed over from chat. Coding sessions do not need it (they have a
  command line).
- **Several calls in one step.** A script can now make many calls at once.
  They run side by side only when the calls wait on something outside (web
  pages, scans); reading workbooks or PDFs on this PC runs one after another,
  because side by side was measured no faster.
- **One bad file does not stop the rest.** Each call reports its own result.
- **Save a script with a skill.** A script that worked can be saved with the
  skill, and run again by the skill's name. A later chat, or a scheduled run,
  that uses that skill is given the script tool and the reading tools the
  script needs, so the saved script can really run. A saved script never
  brings a tool that changes something or asks first.

### What a script read stays on this PC

Once a script has read your files or data, that script cannot call a tool that
reaches outside this PC (a web search, a web page). Before, one script could
read a client file and look something up on the web in the same loop, with no
card between the two. The model can still make such a call directly, one at a
time, where you see it and where the usual cards apply.

What counts as "read", and what counts as "outside":

- A call that **failed** counts as a read. A tool's error often quotes what it
  found.
- A **memory base that is not on this PC** (Notion, a memory server) is
  outside. A notes folder on this PC is not.
- Each **connected app** is its own place: what one app said cannot be sent to
  another app from the same script.
- The **page open in the browser** is private, like a file.
- A **path on another computer** (`\\server\share\...`) is outside, unless
  the folder you are working in is itself on that share.

A script is also limited in what it can do by itself:

- It imports Python's own standard library, plus `openpyxl` and `dateutil`.
  Other libraries are refused. Several of them can open files and the
  internet by themselves, where the app cannot see it. A refused import now
  names the built-in modules to use instead. In one test Jarvis tried
  pandas, was refused, and then spent seven command-line steps without
  making the file.
- It cannot open a SQLite database, read the Windows registry, or ask Windows
  about a path on another computer.

This is a guard against a careless or misled model. It is not a locked room:
code that sets out to break it can still find a way, which is why a script
may only call tools that read.

### Measured

With a real model (Claude Haiku on the owner's Claude plan), on a scratch copy
of the app with made-up statements, one run each:

| Task | v1.472.1 | v1.476.0 |
|---|---|---|
| Add up 16 statements | 34.2 s, 5 approval cards | 17.5 s, no cards |
| 640 payments into a workbook | 141.1 s, 22,646 tokens written by the model | 39.0 s, 2,346 tokens |
| Rename 16 files | did not finish: nothing renamed | 11.7 s, one card listing all 16 |
| Use a skill without "/" | not possible | loaded by itself |
| Change a skill from chat | not possible | one card, version before kept |

Three more requests, each worded without "make" or "save", were run on this
version before and after the fix described above (same model, one run each;
every workbook was checked row by row against the 16 statements):

| Request | Before the fix | After |
|---|---|---|
| "Put every payment row … into one workbook called payments-check.xlsx" | no file, 2 approval cards | 34.9 s, no cards, 640 rows right |
| "Merge the statement CSV files … into one Excel file named merged.xlsx" | no file, 2 approval cards | 22.5 s, no cards, 640 rows right |
| "Put the 16 totals in a Word document called totals-summary.docx" | no file, 3 approval cards | 13.1 s, no cards |

Without a model: 24 reads that each wait 0.2 s took 5.2 s one at a time and
1.5 s in one step. Reading 12 workbooks or 12 PDFs took the same time either
way.

The same three requests were then run on the packaged program (the one the installer carries), built from this version: 21.7 s, 22.3 s and 13.9 s, with no approval cards. Both workbooks matched the 16 statements row by row (640 rows, with dates and amounts as real values), and all 16 totals in the Word file were right. A fourth request told Jarvis to add the sixteen totals up by hand: the file tool named the typed total, Jarvis checked it with the calculator and wrote the file again with the right figure (27.9 s). Earlier passes on the packaged program are how the last five fixes in this section were found.

### Checked by a second reviewer before release

A separate reviewer, who had not written any of it, attacked the change and
wrote a test for each thing it broke. It found 15 defects. All are fixed, and
each has a test that fails on the old code:

- A card could show one list and a different list could run (a script in the
  same step changed it after the card was built).
- "Allow for this chat" on one batch covered every later batch.
- A batch, and a rename, could write Git's and Iron Jarvis's own files with no
  safety card.
- A script could get round "what a script read stays on this PC" six ways:
  SQLite, a file copy, a failed call, a chat being saved while the script
  ran, a path on another computer, and third-party libraries that open files
  and the internet by themselves.
- A script could read the Windows registry, where your own environment
  settings are kept.
- Changing a skill from chat could drop part of the skill's text.
- A rename told to replace a file destroyed the replaced file.
- Text a script took from a document came back through a batch's result as
  trusted text.
- Building a card could hold the whole app up for most of a second.

### What this does not do

- **Your own model server was not tried.** Its gateway needs a key the
  developer session did not have. The numbers above are one cloud model.
- **Reading many files on this PC is not faster side by side.** The saving
  there is in steps and in what the model has to write, not in the reading.
- **The batch card is on the chat page only.** The phone, and agent runs,
  make changes one at a time as before.
- **In an agent run, an approval for a call that uses a script's result shows
  the result's name, not a summary of it.** The chat page shows the summary.
- **A script that imported another library before this version will be
  refused that import.** It is told to use the app's own reading tools.
- **A skill from another program's folder is sent to whichever model answers
  the chat** when Jarvis loads it, the same as when you pick it with "/".
- **The figure check does not stop the file.** A file with a figure that no
  script or calculator produced is still written; Jarvis is warned and you
  get the note. It reads only files that hold at least three produced
  figures, and only money figures (thousands commas, or two decimals). A
  figure Jarvis says in its reply, and not in a file, is not checked. In an
  agent run (a schedule, a mission) only the model is warned: the run's
  summary does not carry the note. For a total that matters, ask for it by
  name ("and a grand total"), so the script computes it.
- **The installed desktop app was not driven.** The packaged program that
  the installer carries was built from this version and given the same
  requests; the installer and its window were not.

## One chip for the coding tool, model and effort (v1.475.0)

In Build › Code, the coding tool, its model and its effort are now one chip
under the box: "Claude Code · Opus 5.5 · High". Press it (or Ctrl+Shift+M)
and one panel opens:

- **Left: the coding tools** on this PC. One that cannot start right now is
  greyed, with the reason under it, and a Sign in press when signing in is
  what it needs.
- **Middle: the tool's models**, grouped as the tool groups them, with a
  search box. Type a few letters to narrow the list.
- **Right: the effort levels** of the model you picked. Only the levels that
  model takes are shown, and "Default" first sends none.

Picking a model moves straight to its effort levels, so tool, model and
effort are one short trip. The keyboard does it all: the search box has the
cursor, the arrow keys move, Enter picks, Esc or Tab closes.

- **In a running session** the tool cannot change (start a new session for
  another tool); the chip still changes its model and effort.
- **A new session starts from what that tool last started with** on this PC:
  its model and effort. A folder that has its own defaults for that tool
  still wins, and a model the tool no longer offers is not used.
- **A model the tool no longer lists** stays named on the chip and in the
  list, marked "not in the list". It is never swapped for another one
  without telling you.
- **The list is read again** when you open the panel, at most once a minute,
  so a model a tool has just added shows up.
- **A change in a conversation leaves one quiet line** in it, such as "Model
  changed: Sonnet 5.5 → Opus 5.5, from your next message", so the
  conversation shows where the model changed. Nothing is written before the
  conversation's first message.

## A coding tool can hand work to another one (v1.474.0)

In Build › Code, the coding tool you are working with can now start another
coding tool you have on this PC on a piece of the work. For example, Claude
Code can hand the tests to Codex while it works on the code, and read what
Codex did when it is done.

- **Off until you turn it on.** Open the session's **Iron Jarvis tools** and
  tick **Hand off**. It is for that session only. Untick it and the next
  hand-off is refused.
- **Each helper is its own session.** It works in the same folder, is named
  "Helper: …" in the session list, and the session that started it lists its
  helpers with how each one stands ("Working", "Waiting for you", "Done") and
  an **Open** button. A helper says which session it is helping.
- **A helper asks you itself.** Its approval cards are its own: they appear in
  its session and in the bell. A helper never gets Full access. It works in
  the same mode as the session that started it when its tool offers that mode,
  and in its tool's own default otherwise.
- **No chains.** A helper cannot hand work on to another tool.
- **Your account groups hold.** A helper of the same tool uses the same
  account. A helper of another tool uses an account from the same account
  group as the session that started it, so work and personal accounts never
  mix. If no account in that group can take the work, nothing is handed over
  and the tool is told why.
- **Emergency stop covers helpers.** While it is on, no work is handed over,
  and turning it on stops helpers that are working.
- **At most three at a time.** A session can have three helpers working at
  once.
- **Stop stops them too.** Pressing Stop on the session's reply, archiving it
  or deleting it stops its working helpers. What a helper already changed
  stays.
- **What comes back is read, not obeyed.** The helper's final words and the
  files its edits named go back to the coding tool that started it, marked as
  information, never as instructions.
- **Where the words go.** The task is read by the helper tool's own model
  (Anthropic for Claude Code, OpenAI for Codex, and so on), and what the
  helper wrote is read by the starting tool's model.

Your coding tool may ask before its first hand-off call, as it does for any
tool server. Choosing its "allow for this session" answer saves asking again.

## Long coding sessions open fast and stay smooth (v1.473.0)

A long Build › Code session now draws only its newest messages, about 120 rows.
Above them, one quiet line says how many earlier messages there are, with
**Show earlier**. Each press draws another set and keeps your place on the
page. Once everything Iron Jarvis holds is drawn, **Load earlier** brings older
history as before. A short session looks exactly as it did.

While a long reply streams and you are at the bottom, the view keeps to one
set of rows. If you have scrolled up to read, it leaves you where you are.

A reply's **Copy** still copies the whole reply, including parts above the
drawn rows. Its text is put together only when you press Copy.

Measured on the same PC with a streamed 46-second reply:

| Session | Before (v1.472.0) | Now |
|---|---|---|
| 250 turns: page work during the reply | 42 s | 7.8 s |
| 250 turns: opening it | 0.76 s | 0.33 s |
| 600 turns: typical frame | 40 ms | 13 ms (the screen's own pace) |
| 600 turns: frames slower than 50 ms | 421 | 0 |
| 600 turns: opening it | 1.7 s | 0.36 s |

One thing to know: the browser's Find (Ctrl+F) only finds words in the rows
that are drawn. Press **Show earlier** to search further back.

## A Linux preview is attached to each release (v1.472.3)

Nothing changes in the Windows app.

From this release on, a few minutes after a new version is published, a Linux
build of Iron Jarvis is added to the same release page:
`Iron-Jarvis-<version>-linux-preview.AppImage`. It is one file: mark it as a
program and run it.

It is a **preview**:

- It does not update itself. To get a newer one, download it again.
- It starts on a test machine, but it has not been used on many real PCs yet.
- Windows-only features are not there: working in a workbook open in Excel,
  reading scanned pages with Windows' own text reader, and the Windows
  terminal handling.

The Linux build runs only after the Windows installer is already out, and it
can only add its one file. It cannot hold up, change or replace the Windows
installer or the files the Windows app checks for updates. There is no Mac
build yet.

## Test results you can trust after a quick fix (v1.472.2)

- **A test run right after an edit always reads the new code.** Python keeps
  a compiled copy of each file and reuses it when the file's time (to the
  second) and size have not changed. An agent that fixed a file and ran the
  tests again within the same second could be told they still failed.
  Commands Iron Jarvis runs no longer write those compiled copies, so your
  Python projects stop getting `__pycache__` folders from Jarvis's own
  commands. Commands you run yourself are unchanged.
- **Linux- and Mac-style folder paths are understood in more phrasings**, such
  as "use Codex for /home/sam/shop" with no word like "folder" in it.

More groundwork for Linux and Mac, none of it visible on Windows: stopping a
coding tool ends the programs it left running; idle coding tools are stopped
on a Mac; closing a terminal can no longer hang there; and fixing a file
name's capital letters works on a Mac. There is still no Linux or Mac
download.

## First steps toward Linux and Mac (v1.472.1)

Iron Jarvis is still a Windows app, and nothing changes on Windows except two
small things:

- **Folder paths written the Linux or Mac way are understood in chat.** "Use
  Codex for /home/sam/shop" or "add my notes folder at ~/notes as memory" now
  reach the same tools a Windows path does.
- **A folder never sorts above the files in it** when Jarvis lists a folder,
  biggest first.

Behind the scenes, the app's own tests now run on Linux and Mac as a trial,
and a complete Linux build was made and started for the first time. There is
no Linux or Mac download yet.

## Long coding sessions stream smoothly (v1.472.0)

Nothing on screen changes. Build › Code used to do work on every streamed word
that grew with the length of the whole session, so a long session got slower
the longer it ran. Now each word costs only its own work:

- **Only what arrived is added.** The conversation was rebuilt from every
  step of the session about once a frame while a reply streamed. Now each
  frame adds only what came in since the last one.
- **Only the end of the reply is formatted again.** A reply still being
  written was formatted from its first word on every frame. Now only the part
  after its last finished paragraph is formatted again. The chat page already
  worked this way.
- **The side panes stay put.** Changes, Usage, Files and Session no longer
  redraw for every streamed word.
- **Words travel together.** Iron Jarvis sends the words that come in within
  about 40 milliseconds as one message, instead of one message per word.
  Before it sends them, it checks the joined words again for keys, so a key
  split across two pieces is caught.

What did NOT change: the conversation still draws every row (rows you have
already seen are not drawn again), and the session is still read again on its
usual schedule.

## Three more requests read right (v1.471.1)

Nothing on screen changes. Chat now reads three kinds of request it got
wrong in v1.471.0: "save this as a skill instead" brings the skill card;
"create a Google Assistant skill" (or Siri, Alexa) does not; and trial
balances, ledgers, schedules, memos and budgets count as documents when you
ask about all of them, and "each contract's terms" is about each contract.
On a new set of 60 test messages written after the change and never used to
tune it, chat read every request right (v1.470.2 read 83% of the skill
requests and 73% of the many-files requests right).

## Faster tool calls, better reading of requests, sturdier scripts (v1.471.0)

Nothing on screen changes. Underneath:

- **Every tool call is quicker to record.** Each call writes three rows to
  the activity record (that it started, that it finished, and its event).
  They are now written directly, with exactly the same contents, instead of
  through a slower layer. Measured on this PC: a tool call went from 1.78 ms
  to 1.05 ms of overhead, and a call made from a script from 2.02 ms to
  1.25 ms. The record still lands whole or not at all.
- **Chat reads two kinds of request more accurately.** "Create a skill
  for…", "save this as a skill", "package these steps into a skill" and
  "create skills for…" now bring the skill card, while "practice my piano
  skill", "a skill-based pay plan" or "my staff need better research skills"
  no longer do. For the script tool, "page 5", "line 12", "box 14", a year
  ("the 2023 return") or "after all" no longer read as many files, while
  "which of these files…", "how many of the statements…" and "the six
  spreadsheets" do. On a test set written after the change and never tuned
  against, the skill reading went from 72.5% to 95.0% right and the
  many-files reading from 57.5% to 97.5%.
- **A script keeps going where it used to break.**
  - A tool answer with unusual data (a broken character, data that contains
    itself) no longer ends the script's worker and loses its variables.
  - A script that makes many slow calls is no longer stopped at the time
    one call is allowed. Each call keeps that limit; a whole script may run
    for up to an hour, and Stop still ends it at once.
  - In a new chat, what a script kept in its first message is still there
    in the second.
- **A file name with a broken character no longer breaks a tool call.**
  Windows allows such names. A tool that listed or read one used to fail
  before its call was recorded; the call is now recorded, with the
  character shown as "?".

## The installer is signed (v1.470.2)

Since v1.470.1 the Windows installer is signed as **RealDealCPA.Ai LLC**.
The README and the Help page's "Windows SmartScreen on install" entry now say
so: check that the publisher reads RealDealCPA.Ai LLC, then press **More
info** and **Run anyway** if Windows still shows its warning on a new
release. A release that comes out unsigned can no longer be published.

## The installer can be signed (v1.470.1)

The Windows installer can now be signed as **RealDealCPA.Ai LLC**, through
Microsoft Artifact Signing. A signed installer shows that name as the
publisher instead of "Unknown publisher".

- **It switches on by itself** once the six signing values are saved as
  GitHub secrets. Until then, releases are built unsigned, as before.
- **Every program is signed:** the desktop app, the daemon and its helpers,
  the uninstaller and the installer itself.
- **A signed release is checked before it is published.** Windows must call
  the installer, the app and the daemon validly signed by RealDealCPA.Ai LLC,
  and the signed daemon must still start. Any miss stops the release.
- **The release page tells the truth either way:** "Signed by RealDealCPA.Ai
  LLC" for a signed build, the old "isn't code-signed yet" words otherwise.
- **The Windows warning may not vanish at once.** Windows trusts a new
  publisher more as more people install; the first downloads may still see
  "Windows protected your PC", now with the publisher named.
- **Copies already installed update normally.** After the first signed
  release, an installed copy only accepts updates signed by the same name.

`docs/SIGNING.md` has the setup, what to keep alive, and the one command to
run after the first signed release.

## Scripts that call tools, and skills made from chat (v1.470.0)

### Ask chat for a skill and you get a skill

A **skill** and a **workflow** are two different things:

- A **skill** is written know-how: how to approach a kind of task, what to
  ask, what to check, how to word the result. Chats and agents load it when
  the task fits, and you can run one with `/`.
- A **workflow** is a list of steps the app runs, by hand or on a trigger.

Until now chat could only offer the workflow card, so "create a skill for tax
advisement" came back as a workflow. Now, when you ask chat to make or save a
skill ("create a skill for…", "save this as a skill", "turn that into a
reusable skill"), Jarvis writes the skill and shows the usual approval card
before it is saved. After you allow it, the skill is on the Skills page.

- A skill made this way is **pinned**, so the skill clean-up never archives
  it for sitting unused.
- It is still marked as written by a model, so it is checked for planted
  instructions before any model reads it.
- A question ("how do I make a skill?") is answered, not acted on.
- This works on the chat page. The phone and other places that cannot show a
  card do not make skills.

### One script instead of many tool calls

When a job is the same read done many times ("total the deposits in every
statement in this folder", twelve attached workbooks), Jarvis can now write
one short script that calls its tools itself. Before, each file was its own
round trip: the model asked, the tool answered, the whole result went into
the conversation, and the model asked for the next file.

What you gain:

- **Fewer steps.** Forty files is one script, not forty turns. On your own
  model servers this is where most of the time goes.
- **More room in the conversation.** The rows stay inside the script. The
  model sees only what the script prints, for example a count and a total.
- **Less of your data sent to the model.** Rows the script filters out never
  reach it.
- **Exact arithmetic.** Sums and filters are done by code.

When it is offered: with Auto tools on, when your message speaks of each,
every or all of some kind of thing, names four or more of them, or brings
four or more files and points at them ("total these"), and the turn already
has a tool that reads.

### What a script can and cannot do

The script runs in its own isolated process:

- no network and no other programs;
- no file writes (only private temporary files);
- it reads files only in the turn's working folder;
- none of your keys or tokens are in its environment.

So the only way a script does anything is by calling a tool, and each call
goes through the same permission check and the same activity log as a call
the model makes directly. On top of that, a script may call only a tool that:

- is one of that turn's tools;
- only reads;
- would run without asking you.

Anything that changes a file, sends something, costs money or needs your
approval is refused inside the script, and the model is told to call it
directly, where you see the card. A script is never a way around a card. In a
Local only chat a script cannot use the web tools or your connected apps.

Honest limits:

- Windows itself enforces "no other programs", a memory limit and "no
  clipboard" on the script's process. The network and file rules are checks
  inside that process. They stop a careless or misled model. They are not a
  barrier against code written to break out.
- One script may make at most 300 tool calls.
- Under **Ask before every action**, the script is shown on a card first.
- To turn scripts off, set **tool_script** to **Never run** in Settings ›
  Permissions. To see each one first, set it to **Ask**.
- Coding sessions (Chat › Code) and the built-in agents do not use scripts
  yet. A custom agent can, if you add `tool_script` to its tools.

No waiting you would notice: the script's process is started in the
background as soon as the tool is offered for a message and stays up for the
session. A tool call from a script takes under a thousandth of a second more
than a direct call.

Stop now really stops code that is running: pressing Stop while a script (or
a Python cell) runs ends its process at once. Its variables are gone and the
next run says so.
## Links to another workbook follow the folders Jarvis may read (v1.469.1)

A formula can link to another workbook, for example a summary sheet that
reads a figure from a client's own file. Jarvis writes such a link like any
other formula. From this version there is one exception. If you have set
**Folders Jarvis may read** (Settings › Permissions), Jarvis does not write a
link to a workbook outside those folders. Excel would read that file by
itself and put its figure into the sheet, so a note hidden in one client's
workbook could otherwise ask for another client's numbers.

- This holds for a workbook open in Excel and for a saved file.
- A link that spells out its folder is judged by that folder. A link by file
  name alone, to another workbook open in Excel, is judged by where Excel has
  that workbook open from.
- A link whose folder Jarvis cannot work out (a folder written in part, or a
  saved file's own numbered link) is refused while the folders are limited.
- The INDIRECT function builds its reference from text, so it could reach a
  workbook without naming it in a way Jarvis can see. While the folders are
  limited and a workbook from outside them is open in Excel, Jarvis does not
  write a formula that uses INDIRECT into the open workbook. Close that
  workbook, or type the formula yourself.
- When a link is refused, nothing is changed and Jarvis names the file. You
  can type the formula in Excel yourself.
- If you have not limited the folders, nothing changes: links are written as
  before. Iron Jarvis's own protected folders are never linked to.

## Matching two lists, and Excel's features in a saved workbook (v1.469.0)

**Matching two lists of transactions.** Ask Jarvis to reconcile a bank
statement to the books, tie a sub-ledger out to its control account, or
compare two exports, and it now does the matching in code instead of reading
the rows. Attach the two workbooks (or have them open in Excel) and say which
is which.

- Amounts are compared in whole cents, and each row is matched once.
- Rows are matched first by amount and reference (a cheque or invoice
  number), then by amount with dates within a few days of each other (five
  unless you say otherwise), then, only if you allow a tolerance, amounts that
  differ by no more than that.
- The answer gives both totals, how many pairs matched and how, and every row
  left over on each side, named by its row number in the sheet.
- It proves the result: the difference between the two totals must equal the
  left-over rows, and the answer says "It ties" or says how much is
  unexplained. This holds whatever the lists contain.
- It also lists, without counting them as matched: rows with the same amount
  but dates too far apart, and one row that equals several rows of one day on
  the other side (a deposit made of several receipts). Those are yours to
  judge.
- If the two lists use opposite signs (money out is negative in one and
  positive in the other) it says so rather than guess.
- A reference that repeats (the same "ACH" every month) only pairs rows whose
  dates are close, so a missing March is reported as March, not December.
- An amount it cannot read for certain is not guessed. A number written with
  a decimal comma (1.234,50) is left out and the answer names the row.
- It says when a list's last row looks like a total line (and can leave it
  out), when rows have no date, and when a money-out column is written in
  negative numbers.
- Ask for the result as a workbook and it writes a new file with Summary,
  Matched and the two left-over lists. It never changes either list, and
  never replaces an existing file.

Only the first 50,000 rows of each list are read; the answer says when a
list was cut.

**Colour rules, dropdowns and more in a saved workbook.** Jarvis could
already add these to a workbook open in Excel. It can now add them to a
saved file too: colour rules (greater than, between, contains text,
duplicates, top or bottom, a formula, data bars, colour scales), dropdown
lists and number or date limits, frozen headings, column widths, named
ranges, tables, cell notes and print layout. These were checked by opening
the result in Excel itself: the rules colour exactly the cells they should.
If one step of a request cannot be made, nothing is saved and the file is as
it was. The whole change is one Undo. A step that would leave a file Excel
refuses to open (a name that clashes with a table or reads like a cell, a
table laid over merged cells or over the sheet's filter) is refused before
anything is saved.

**A formula that sends data out is never written for you.** A workbook can
contain text that tells an AI to write a formula such as one that calls a web
address with a cell's content in it. Opened in Excel, that formula would send
the cell out, whatever the chat's own limits are. Jarvis does not write such
a formula, into an open workbook or a saved one, and says why; you can type
it in Excel yourself if you mean it. An ordinary link to a web page is fine.

**What still needs the workbook open in Excel.** Inserting or deleting rows
and columns, sorting, filtering, renaming a sheet, pivot tables and charts.
Done on a saved file from outside Excel, these would leave formulas pointing
at the wrong cells, so Jarvis says so and asks you to open the workbook,
where Excel itself does the work correctly. It does not try another way
round.

**Starter skills.** In the Accounting starter skills, the bank
reconciliation review now uses the matching tool, and the variance analysis
follows a figure back to the cells it is built from. A copy you loaded
before this version keeps its old text; load it again from Templates to get
the new one.

## Jarvis beside Excel (v1.468.0)

Jarvis can now sit beside your workbook and know what you have selected, the
way a panel inside Excel would.

**The line above the message box.** While Excel is open with a workbook, Chat
shows one quiet line: "Excel is open: Budget.xlsx". Press **Use it in this
chat** and it becomes "Working with Excel: Sales!B4:B9 in Budget.xlsx", the
sheet and cells you have selected. From then on you can simply say "what is
wrong here?", "fix this", "total these" and Jarvis reads the selection from
Excel. **Stop** switches it off again, and it stays off until you switch it
on. The switch belongs to that window and lasts until you close it.

**The window beside Excel.** In the desktop app, press **Open beside Excel**
on that line (or choose Iron Jarvis > Open Chat Beside Excel in the menu). A
narrow chat window opens at the right edge of the screen and stays on top, so
it is there while you work in the workbook. In that window the Excel switch
starts on. Move it where you like: it opens there next time. Jarvis does not
move or resize Excel; drag Excel's edge if you want the two side by side
without overlap.

**What is read, and when.**

- The line shows names only: the workbook, the sheet and the selected range.
  No cell content is read to draw it, and none of it goes to a model.
- It is read when you come back to the Jarvis window, and when you press the
  small refresh button. Nothing runs on a timer.
- Only when the switch is on does a message carry those three names, and
  only then may Jarvis look at the open workbook for that message whatever
  your words say. What it reads from the workbook goes to the model that
  answers, exactly like a file you attach, so a chat set to Local only keeps
  it on your own network.
- A change to the workbook still shows its card first, as before.

**Why not a panel inside Excel itself?** Excel only loads a side panel from a
secure web address with a certificate Windows trusts. For an app that runs
only on your own PC, that means installing a certificate into Windows as a
trusted authority and registering an add-in with Office, both of which change
this PC's security settings. Iron Jarvis does not do that by itself. The
window beside Excel gives the same working arrangement without it.

## How a number is built, what-if and goal seek (v1.467.0)

Jarvis can now explain a figure in a spreadsheet, tell you what would change
it, and take you to the cell. It works on a saved workbook and on one that is
open in Excel.

**Four questions it answers.**

- **How is this number built?** "How is the net profit in E14 calculated?"
  Jarvis follows the formula down to the cells that were typed in, level by
  level, and shows each cell with its heading, its value and its formula. A
  long chain is cut and it says so.
- **What uses this cell?** "What depends on the tax rate?" lists every formula
  that reads the cell, and what reads those.
- **What if?** "What would December's revenue be if growth were 8%?" Jarvis
  works the answer out on the side and shows each figure before and after.
  **Nothing in the workbook is changed.** You can change several cells at once.
- **Goal seek.** "What does the price need to be for profit to reach 20,000?"
  Jarvis searches for the value and says what the target comes to with it.
  Again nothing is changed. When a rounded figure on the way means no value
  lands on the goal exactly, it gives the closest value and says how far off
  it is, where Excel's own Goal Seek simply gives up. The answer is given as
  a round figure when a round figure reaches the goal.

**How far to trust the figures.** These answers were checked against Excel
itself: Excel was made to hold the same values for real and came to the same
figures. Before it answers a what-if, Jarvis also checks that it works out
today's figure the same as Excel saved it. If it does not, or the formula uses
something Jarvis leaves to Excel (a link to another workbook), it gives no
figure for that cell and says why, rather than guess. The same goes for an
array formula that your change reaches (Excel stores any formula that works on
a whole range this way): the cells that rest on it get no figure, because
Jarvis would otherwise repeat the old result. A what-if value can be a number,
a percent, a date or TRUE/FALSE, read the way a typed cell would hold it.

**Jump to the cell.** When Jarvis talks about a cell of a workbook that is
open in Excel, the cell is a link, like Calc!B8. Click it and Excel comes
forward with that cell selected. This only moves the selection: it changes
nothing in the workbook. You can also ask "take me to it" or "show me that
range". A hidden sheet, or a workbook that is no longer open, is said in words;
Jarvis never opens a file to do this.

## Working in a workbook that is open in Excel (v1.466.0)

Until now Jarvis worked on saved files, so a workbook had to be closed first.
It can now work in the workbook you have open in Excel, as it is on screen,
unsaved changes included. Excel itself does the calculating, so every function
Excel has works, and the figures Jarvis reports are Excel's own.

**How to use it.** Open the workbook in Excel and ask in Chat: "what is this
workbook for, and does anything look wrong?", "change the growth rate to 8% and
tell me December's revenue", "add a pivot table of Amount by Region", "sort
this by Amount and colour anything over 5,000 green". Jarvis switches this on
by itself when Excel is running and you talk about a spreadsheet, or when you
say "the open workbook", "the selected cells" or "the cell I am on". A file you
attach or name is still read as a file.

**What it can see.** Which workbooks are open, their sheets, what you have
selected, the cells of any range with their formulas, and a map of the whole
workbook: what each sheet is for, formulas filled down, totals, and mistakes
worth a look (a total that stops a row short, a typed number in a column of
formulas, a number stored as text). The question tools you already have
(grouping, filtering, checking a formula) can read the open workbook too.

**What it can change.** Values, formulas and formatting; new and renamed
sheets; inserted rows and columns; sorting and filters; column widths and
frozen headings; colour rules; dropdown lists and number limits; charts (new,
and changes to an existing one); pivot tables (new, and changes to an existing
one, including which items show); named ranges; tables; cell notes; and how a
sheet prints.

**How a change is kept safe.**

- **It asks first.** A change shows the usual card. "Allow for this chat" lets
  later changes in that chat through.
- **Replacing data is always shown first.** A change that replaces more than
  ten cells that hold data, or replaces any formula with a plain value, shows a
  card every time, even when changes are already allowed.
- **A deletion asks every time.** Deleting rows, columns or a sheet cannot be
  put back, so it always shows a card with Allow once only, and a copy of the
  workbook as it was is kept first.
- **All or nothing.** The steps of one request are made together. If one of
  them fails, the ones before it are put back and Jarvis says nothing was
  changed.
- **Every change is listed.** Each changed cell is shown with what it held
  before and after, and any formula that now shows an error is called out.
- **Undo.** The line "Excel changed ... Undo" under the reply puts the whole
  change back. A cell you changed yourself in the meantime is left alone and
  named. Excel's own Ctrl+Z history is cleared by any change made from outside
  Excel, which is why Jarvis keeps its own Undo. Undo only works on the same
  workbook, open from the same place: another workbook with the same name is
  never touched. After a sort is undone, the rows, their colours and their
  notes are back, but dropdown rules, links and row heights that moved with
  the sort are not put back. Undoing a filter removes it; it does not bring
  back a filter that was there before.
- **Nothing is saved.** You save the workbook in Excel when you are happy with
  it. If AutoSave is on, Excel saves by itself within seconds and Jarvis says
  so.
- **Rows hidden by a filter.** While a filter hides rows, Excel skips them
  when a block of cells is written, so the values would land on the wrong
  rows. Jarvis refuses to write, clear or sort a block across hidden rows and
  says so: clear the filter (or ask Jarvis to) and ask again.
- **A sort takes the whole table.** Sorting only some columns of a table
  would separate each row's cells from each other, so it is refused unless you
  say you want just that part sorted.
- **Text stays text.** A value such as 00123 or 1/2 is written as you gave it,
  not turned into a number or a date.
- **A formula that sends data out is never written.** A formula that would
  make Excel call a web address, another program or a network share is not
  written from chat; Jarvis says why, and you can type it yourself.

**What stays private.** Jarvis talks to Excel on this PC only. What it reads
from the workbook goes to the model that answers your chat, exactly like a file
you attach, so a chat set to Local only keeps it on your own network. A
workbook in a folder Jarvis may not read is not read, and its sheets and
selection are not listed. A workbook opened straight from OneDrive or
SharePoint has a web address, not a folder, so that folder rule cannot be
applied to it.

**When it says no.** While you are typing in a cell, or a dialog box is open
in Excel, Excel takes no requests: finish that and ask again. A workbook open
in Protected View cannot be read or changed until you press Enable Editing. A
read-only workbook cannot be changed.

**Not offered.** Macros are not written or run. Excel must be the Windows
desktop program; Excel in a browser is not reached. Agents working on their own
(schedules, goals, rules) do not use this; it is for Chat, where you are
present. To switch it off altogether, set the two tools "Look at Excel" and
"Change the open workbook" to Never run in Settings › Permissions.

## Spreadsheets Jarvis understands and works out itself (v1.465.0)

Jarvis used to read a workbook as a grid of cells and leave every formula to
Excel. It now works formulas out itself, understands how a workbook is built,
and checks its own arithmetic.

**Formulas are worked out, the way Excel does it.** A formula's result is
normally filled in by Excel when a person opens and saves the file. A workbook
that a program wrote has formulas with no results, so a total read as blank.
Jarvis now works those out: sums, averages, conditional sums and counts
(SUMIF, SUMIFS, COUNTIF), lookups (VLOOKUP, INDEX and MATCH, XLOOKUP), IF and
IFERROR, rounding, dates (EOMONTH, EDATE), text, loan and investment functions
(PMT, FV, PV, NPV, IRR) and depreciation (SLN, SYD, DDB), across sheets and
through named ranges. About 80 functions in all.

The answers were checked against Excel itself: 318 formulas in two test
workbooks, opened in the real Excel on this PC, and every one matches. That
check found three differences, all fixed:

- **Rounding.** Excel rounds a half away from zero and reads a number at 15
  digits. 750 times 7.25% rounded to two places is 54.38 in Excel; ordinary
  computer arithmetic says 54.37.
- **Order of a minus sign and a power.** In Excel `=-2^2` is 4.
- **Newer functions.** A formula with XLOOKUP, IFNA, MAXIFS, MINIFS, CONCAT or
  DAYS that Jarvis wrote into a workbook used to open in Excel as `#NAME?`.
  It is now stored the way Excel expects.

When a formula uses something Jarvis cannot work out, it says which cell and
why. It never guesses a number.

**Results are saved in the file.** When Jarvis writes or edits a workbook, it
works every formula out and saves each result in the file beside its formula.
The file then reads correctly in any program (a preview, an email attachment,
another tool), not only after Excel has opened it. Excel still works every
formula out again itself when it opens the file, so its own answer always
wins. After an edit, Jarvis reports what each formula it wrote comes to. After
writing a new workbook, it reports the totals and anything worth a look in
what it just wrote, so a total that stops two rows short is caught in the same
step and not after you open the file.

**A map of the workbook.** Before Jarvis works on a workbook it now reads a
map of it. For each sheet the map says:

- what the sheet looks like (an income statement, a budget, an invoice, a
  list of sales) and how it is laid out;
- where the header row is, what each column holds, and how it is formatted;
- which formulas are filled down a column, and the totals with what they come
  to;
- which other sheets it reads from, named ranges, and hidden sheets.

The map also lists anything **worth a look**, the things a careful reviewer
would stop at:

- a typed number in a column of formulas;
- a formula that does not follow the rest of its column;
- a SUM that stops short of the last rows;
- a typed total that does not equal the numbers above it;
- numbers stored as text, which sums skip;
- a formula that comes to an error.

These are prompts to check, not verdicts. A statement with subtotals can have
a typed total for a good reason.

**Formatting.** When Jarvis edits a workbook it can now set number formats
(money, percent, dates), bold and italic, fill and text colour, alignment and
wrapping, on one cell or a range.

**Questions about a sheet.** Three things that went wrong quietly are fixed:

- A breakdown by vendor or client listed only the first 20 groups and said
  nothing about the rest. The largest negative group (a refund) was the first
  to be dropped. It now ranks groups by size, says how many it left out and
  what they add up to, and always gives the total of all groups.
- A filter on a date ("on or after 1 March") matched nothing. Dates now match,
  typed either as 2026-03-01 or 3/1/2026.
- A small figure such as 0.004 was reported as 0.00. It keeps its digits.

**A calculator for every figure Jarvis states.** A language model adds badly.
In a test, a client memo had a correct table and three wrong numbers in its
sentences, because the sentences were worked out "in its head". Jarvis now has
a calculator that uses the same engine as the spreadsheet tools, and it is
switched on by itself when a message asks for arithmetic or when Jarvis reads
or writes a document about money. It is told to use it for every figure it
did not copy from a source.

**PDFs made from a table.** Rows written to a PDF used to run together: an
invoice number 1042 beside an amount 500 printed as "1042500". They are now a
real table. A character the PDF's font cannot draw (Chinese, Japanese, Korean,
Arabic, emoji) used to vanish; it is now shown as "?" and Jarvis says so and
suggests a Word document instead.

**Old file formats are refused, not faked.** Asking Jarvis to save as .xls,
.doc or .rtf used to write a few bytes of text under that name and report
success. Excel and Word could not open the result. Jarvis now says it cannot
write that format, names the one to use (.xlsx, .docx, .pptx), and writes
nothing.

**The "Part failed" label on a coding helper.** A helper was marked "Part
failed" whenever any command along the way ended with an error, such as a
test that failed before the fix. On this PC 53 of 70 helper runs carried the
label and only 5 of them ended on a failed step. A helper now reads "Part
failed" only when its last change failed.

**"This folder" means your work folder.** With Claude Code answering, Jarvis
sometimes looked for a file you named in a hidden scratch folder first, failed,
and then went looking for the right place. A file named that way is now read
from your work folder straight away.

**How this was checked.** Besides the 318 formulas compared with Excel, three
real requests were run on a test copy of the app with a real model and judged
by the files they produced. A client memo from an expenses workbook: all 12
money figures in it were right (the same request had 3 wrong figures before).
A review of a workbook with six planted mistakes: all six found, each with the
right cell. A new workbook with 39 formulas: opened in Excel afterwards, and
every result saved in the file equalled Excel's own.

What is still not done: Jarvis does not work out array formulas, INDIRECT,
OFFSET, or links to other workbooks; those cells are named and left for Excel.
A very large workbook (more than about 400,000 cells, or 12 MB) is not worked
out or mapped in full; Jarvis says so and gives the quick view instead.
It does not create charts from a description or pivot tables. Saving a
workbook from Jarvis can still simplify unusual content (some charts and
macros).

## Starter skills you can load (v1.464.0)

A skill is a set of instructions Jarvis follows for one kind of task, such as
"review a bank reconciliation" or "write release notes". Iron Jarvis now comes
with thirty starter skills in six bundles. They are on the **Templates** page,
in the **Starter skills** card:

| Bundle | What its five skills cover |
|---|---|
| Business | Meeting notes into actions, client email replies, procedures, decision memos, weekly status reports |
| Accounting | Month-end close checklist, bank reconciliation review, expense categorising, variance analysis, client document requests |
| Marketing | Campaign brief, content calendar, landing page copy, email campaigns, customer persona |
| Coding | Bug report to fix, code review, tests first, safe refactoring, release notes |
| Project management | Kickoff brief, work breakdown, risk register, status update, retrospective |
| Social media | Post variations, weekly plan, replies to comments, repurposing long content, performance review |

**Nothing is loaded until you press Load.** Open a bundle to read what each
skill does and untick the ones you do not want, or press **Load all**. A loaded
skill is an ordinary skill: it is listed on the Skills page (marked "Starter
skill"), Jarvis can pick it up when a task fits, and you can run it yourself by
typing `/` and its name in Chat.

What loading will never do:

- It never replaces a skill of your own with the same name. That skill is
  skipped and named.
- It never replaces a starter skill you changed after loading it, unless you
  ask. The card says "Loaded, and you changed it".
- **Remove** takes two presses and moves the bundle's skills to Iron Jarvis's
  trash folder. A skill you changed is kept.

**Load skills from GitHub.** The last row of the card loads skills someone else
shared in a public GitHub repository. Paste the address of the repository (or
of a folder in it) and press **Look**. Iron Jarvis lists the skills it found
and shows a warning, because a skill is instructions Jarvis follows and these
were written by someone else. Read them, untick what you do not want, then
press Load twice.

What Iron Jarvis does to keep this safe:

- Only the instructions (the file named SKILL.md) are downloaded. A skill's
  scripts and other files are not, and nothing from the repository is run. The
  list says how many files were left behind.
- What you looked at is what is loaded: the load reads the exact version the
  list showed, even if the repository changed in between.
- Each skill is checked before it is offered. One that reads as an attempt to
  override Iron Jarvis's rules cannot be loaded.
- A loaded skill is marked "From GitHub" on the Skills page and is checked
  again every time it is about to be used.
- It never replaces a skill you already have.
- The only thing sent to GitHub is the address you typed. A private repository
  cannot be read.

**Office files: seven awkward cases that used to go wrong quietly (v1.464.0).**
Each was found by running the real tools on ordinary but awkward files.

- **A long file read in Chat is no longer cut without a word.** Past 12,000
  characters Jarvis used to see only the start, with nothing to say there was
  more. A 1,000-row ledger reached it as the first 275 rows, so a total or a
  summary of "the file" covered a quarter of it. It now sees the start and the
  end, with a note in between saying how much was left out, and it asks for
  the rest in parts.
- **Long numbers keep every digit in a spreadsheet.** A spreadsheet number
  holds 15 digits. An 18-digit id or a 16-digit card number sent as text used
  to be written as a number and came back rounded. More than 15 digits now
  stays text, and so does a code like "-0042".
- **A file name with a colon is refused in words.** Windows cannot use ":" in
  a name. Saving "Re: Q1 budget.txt" used to answer "wrote 17 bytes" and leave
  an empty file called "Re".
- **Excel's "Unicode Text" files are read properly.** These (and files made
  with PowerShell's ">") used to come back as letters with gaps between them.
- **Text that only starts with "=" is text.** "=== see memo ===" used to be
  stored as a formula and showed an error in Excel. A real formula such as
  "=SUM(B2:B5)" is still a formula.
- **A file open in another program says so.** When a workbook was open in
  Excel, Jarvis used to say the folder was not writable and to pick another
  folder. It now says the file is open in another program, or read-only, and
  to close it there.

- **Formula results nobody saved are said out loud.** A formula's result is
  worked out by Excel. When Jarvis writes or edits a workbook, those results
  are blank until the workbook is opened and saved in Excel. Jarvis used to
  add up such a column and report the partial figure as the total (7 for a
  true 42). It now adds a warning that says how many cells were left out and
  that the answer is incomplete, and an edit of a workbook with formulas says
  the same before anyone asks. The fix on your side is unchanged: open the
  workbook in Excel and save it. (Since v1.465.0 Jarvis works these results
  out itself; the warning is only for a formula it cannot work out.)

## Reading web pages works on more sites; page screenshots work again (v1.463.0)

**Reading a web page.** When Jarvis reads a page for you, it used to tell the
site it was the Chrome browser. Sites that check for that (Wikipedia, cdc.gov,
dol.gov, the Mayo Clinic) refused it or left it waiting. Measured on this PC
against 90 ordinary sites, that read 61 of them. Jarvis now says what it is
first ("IronJarvis, a desktop assistant reading one page its user asked for":
nothing about you or your PC), and asks once more as a browser only when the
site did not serve it. The same 90 sites: 71 read.

Some sites refuse every automated reader (the New York Times, Yelp, the SEC's
search, Stack Overflow). For those Jarvis is now told "The site refuses
automated readers, so asking for this address again will not help", so it
looks for the fact somewhere else or asks you to open the page, and does not
keep asking the same address.

**Page screenshots in a coding session.** With Microsoft Edge 157 the "look at
this page" step failed every time with "the browser wrote no picture". Edge
now starts its real browser in a second step and returns at once; Jarvis ended
the call there and closed the browser before it had drawn anything. Jarvis now
waits for the browser it started to finish, inside the same time limit as
before. Checked on this PC with the real Edge: three of three screenshots, in
about one second each.

**Questions that need the web now reach it.** Asked "What is the IRS standard
mileage rate for business use in 2026?", Jarvis used to answer that it could
not look it up, because nothing in the sentence turned web search on. A
question about a published figure (a rate, limit, deadline, bracket,
deduction) for a named year, or for "current" / "today's" / "right now", now
turns web search on by itself. A question about your own figures ("what rate
did we charge in 2026", "the rate in the attached file", anything naming a
client, an invoice or a project) does not. Local only still keeps the web off.

**"Is anything broken?" is answered from the app's own health check** however
it is worded ("is anything broken in Jarvis right now", "any problems with the
app"). It used to be answered from memory unless the sentence ended on
"broken".

**A memory profile's guide is found by its title.** A memory profile names an
operating guide that Jarvis follows in every chat. If you named it by its
title ("Agent Operating Guide") and your memory base opens notes only by their
path ("procedures/agent-operating-guide.md"), the guide was never loaded and
each chat was only told it "could not be read". Jarvis now searches the base
for the name and reads the note when exactly one result has that same name.
Two notes with the name, or only a similar name, are never guessed between:
the guide is followed as instructions. The profile's Check says where it
found it.

## A missing file says where it really is (v1.462.0)

When an agent or a coding session's helper asks to read or edit a file that is
not there, the refusal now also says where a file of that name is in the same
folder: "no such file: apps/shared/src/tokens.ts. A file with that name is at
packages/shared/src/tokens.ts." With several, it lists up to three, the closest
match first. Only names inside the work folder are looked at, and dependency
and hidden folders are skipped.

Why: in two weeks of real coding work, 83 reads and edits ended "no such
file". For 46 of them (55%) a file of that name sat elsewhere in the folder,
and the model guessed again or fell back to a search command.

An edit whose text is not in the file, and where no line is even close, now
says to read the file again before editing. Before, it said "Copy the text
exactly", which only helps a near miss. A near miss still names the closest
line.

## Tools you already have are listed, not offered again (v1.461.0)

When you bring tools from Claude, Codex, Cursor and your other apps (Settings ›
Tools › Add a tool), or paste a config, a tool that is already in Iron Jarvis
now sits in a short **Already added** list under the new ones. It shows its
name, how it is here ("already added", "already connected", "already a memory
base") and what it is. It has no tick box, is never counted in **Add
selected**, and is never sent to be added again. A group where everything is
already added says "Everything here is already added." and has no Add button.

Before, such a tool was shown unticked with a box you could still tick, and
pressing Add then answered "skipped: already added". You manage an added tool
from its own row in Tools.

## Box starts again, and a broken extension says so (v1.460.0)

Connecting Box saved its keys, but the extension never started: its row read
"Didn't start: the extension's server exited (exit code 1)" with an
`ImportError` underneath. Box's own server (the `mcp-server-box` package)
is no longer looked after by Box, and the libraries it relies on changed
after it was published, so downloading it today fetched a newer library that
no longer has a part the server needs.

- **The Box card now starts Box's server exactly as it was the day it
  shipped** (5 June 2025): its libraries as they were then, the one old
  library it needs but never listed, and Python 3.13 (uv fetches it if this
  PC lacks it). It answers with its 30 Box tools.
- **If you already connected Box, you do not need to do anything.** The next
  time Iron Jarvis starts, it updates the saved Box extension to the new
  launch line, keeping your client id and secret. A Box extension you edited
  yourself is left as you set it.
- **An extension whose own code cannot load now says so** on its Tools row:
  "the extension's own code could not load part of what it needs (…), so
  its package is broken upstream … A missing key or setting does not cause
  this", with the next step: look for a newer version of the extension or
  remove it. Before, it read "a missing key or setting is the usual cause",
  which sent you to the wrong place.

Box also offers its own hosted server, set up by a Box administrator; Iron
Jarvis does not connect to that one yet.

## Six small fixes (v1.459.0)

- **Compact in Build › Code for Pi, OpenCode and Grok.** The Session tab's
  Context drop-down now has "Compact session" for these too (it said they
  "compact by itself"). The press sends each tool's own `/compact`. OpenCode
  does not list compact in its `/` menu, so Iron Jarvis adds it there.
  Antigravity is unchanged.
- **Chat no longer offers to change a setting you only mentioned.** "Only
  suggest, never act is my motto", "I should learn from coding sessions" or "set
  the model server … in our team doc" no longer bring up the settings tools.
  Asking for the change still works.
- **"Coding session", everywhere.** The last places that still said "Build
  session" now say "coding session", and a refusal that sent you to Build now
  sends you to Chat › Code.
- **A report from another computer finds a new chat.** A job a brand-new chat
  hands to another computer (before the chat is saved) now reports back into
  that chat once it is saved, not only into the agent room.
- **Web services keep their sign-in when you edit them.** Configure opens on
  what is saved instead of an empty box, and saving keeps the key and how it
  is sent unless you change them there (write `null` to remove one).
- **A failed "files changed" read says so.** When the list of a reply's
  changed files cannot load because of a server error, the "Part of this page
  didn't load" banner shows, as it does for other reads.

## Record a task in the browser, and Jarvis keeps it as a skill (v1.458.0)

- **Do a task once; Jarvis can do it again.** In the browser sidebar, press the
  round **Record** button (beside "Point at it"), then do the task in the tab
  you are looking at: open pages, click, type, choose from lists, press Enter.
  While it records, the page has a red edge and a small **"Recording your
  steps"** sign, and the sidebar counts the steps. Press **Stop** when you are
  done.
- **Review before anything is saved.** Stop shows the steps in plain words
  ("Click button 'New invoice'", "Type into textbox 'Customer name'"). Delete
  any step with **×**. Give the task a name (and, if you like, what it is for),
  then press **Save skill**; **Discard** throws it away. Nothing leaves your
  browser until you press Save.
- **What you type is never recorded.** The add-on writes down WHICH field you
  typed into or which list you chose from, never what you typed or chose. Each
  one becomes a value the skill asks for every time it runs (for example
  `{{customer_name}}`), unless you type a fixed text into that step's
  "…or always type" box during review. A password, card or file field is
  always yours to fill: the skill asks you to do it and waits. A fixed text
  that looks like a password or a key is refused. Addresses keep the site and
  page only, never the part after "?" where sign-in links carry their codes.
- **Using it.** The new skill is in the "/" list in the sidebar and in chat at
  once. Pick it, add the values after its name if you want ("/Enter a new
  invoice Acme Ltd"), and Jarvis follows the steps: it finds each button and
  field again on the live page by its name, asks you for any value it does not
  have, and stops to tell you which step did not match if the page has
  changed. Every click and typing step asks you as usual, and a payment, a
  password, a deletion or a page that tries to give Jarvis instructions always
  asks, whatever the skill says. From chat, picking a recorded skill turns on
  the browser tools for that message by itself.
- **Only your own actions are recorded.** Clicks a page makes by itself, what
  Jarvis does while you record, and a "Point at it" click are left out. Only the
  tab you started in is recorded, one recording at a time. Clicks inside an
  embedded frame (a payment box, a sign-in widget) are not recorded. A recording
  with nothing happening for 15 minutes stops by itself, and closing its tab
  stops it too; the steps so far are offered for review.
- **Lists by typing.** Jarvis can now choose an option in a page's drop-down
  list by typing its label into it, which is how a recorded "choose Net 30 in
  Terms" step is done.
- Recorded skills show **"Recorded in the browser"** on the Skills page. Page
  words in a recording that read like instructions planted for an AI refuse the
  save, naming the step to delete; the skill is checked again every time it is
  used.

## A tidier start box in Build › Code (v1.457.0)

Build › Code's start box now looks like Chat › Code's: the coding tool, its
mode, the model and the effort sit inside the box on its bottom row, beside
the paperclip and Send. Only the folder ("No folder" / Choose…) and a few quiet
notes stay under the box. Nothing about what the choices do has changed.

## Text size (v1.456.0)

- **Make everything in the app bigger or smaller.** Settings › Appearance ›
  **Text size**: Smaller (90%), Normal (100%), Large (110%), Larger (125%)
  or Largest (150%). Text, icons and spacing grow together, so nothing is
  cut off. It is kept for this PC, so a laptop and a desk monitor can each
  have their own.
- **From the keyboard:** Ctrl + = makes it larger, Ctrl + - smaller, and
  Ctrl + 0 puts it back to normal. Ctrl + mouse wheel does the same. The
  setting follows, so Settings always says the size you see.
- **From chat:** "make the text bigger" or "set the text size to 125%".
  A font size inside your own document ("change the font size in my
  report") is never taken as this setting.
- Pop-out windows follow the same size. In a browser tab, use the browser's
  own zoom instead.

## Long replies from your own model server, and a phone alert that names the chat (v1.455.1)

- **Your own model server may go quiet for up to 10 minutes.** While a
  model on your own server writes a big step, like a whole workbook, the
  server can send nothing until the step is finished. Iron Jarvis used to
  give up after 60 seconds of silence and cut the reply off. It now waits up
  to 10 minutes on a server on your computer or your own network, and the
  chat keeps saying it is still working. A cloud service keeps the
  60-second limit. Stop still ends a reply at once. A job itself has no time
  limit: a long job is many replies.
- **A failure names the server plainly**: "your model server", or the name
  you gave it, never "Fleet-custom" or "Custom endpoint".
- **The phone alert for a failed chat names the chat**, and says what to do
  next, for example: "Your chat “…” didn't finish: …".
  - **It is sent only when the reply really failed.** A failure that Iron
    Jarvis got past during the reply sends nothing.
  - **It is not sent while that chat is on your screen.** If the window is
    minimised or covered, the alert is sent.

## One word for each thing, and "did it work?" answered to the end (v1.455.0)

- **One word for each thing.** A server for models you run is a **model
  server** everywhere (it was "endpoint" or "node"). An MCP server is an
  **extension** everywhere (it was also "tool server"). The coding screens
  talk about **your accounts**; only the account manager's own card on
  Connections uses its program name, Iron-Proxy. After the rename to Rules,
  the bell, the tray and your phone say "rule" as well. The Sessions page is
  now **Agent runs**, and searching for "sessions" still finds it. About a
  dozen Settings rows have plain names, and their old names still search.
- **A failed button press says so plainly.** When Iron Jarvis hits a problem
  doing something you pressed, you see "Iron Jarvis hit a problem doing
  that. Try again."; the error itself is under Details. The "Part of this
  page didn't load" banner now only shows when a page really failed to load.
- **Work you started is counted while it runs.** The orb and the home line
  count a running workflow or a picture being made, not only agent runs.
- **A remote agent's report comes back to your chat.** When an agent run you
  started from chat hands work to another machine, that machine's report
  lands in the chat, and the bell rings for it.
- **Asks say why.** An agent run's approval card says why it is asking, and
  every approval dock, Chat › Code's included, has "No, tell it what to do
  instead".
- **When something fails, it says what was kept.** A failed run or coding
  reply names the files it wrote, which are kept. A failed workflow shows its
  first failed step, with Run again; a failed picture has Try again. A chat
  turn waiting on your approval card is never stopped for being unwatched,
  and a turn that is stopped for that says why.
- **Chat finds the right thing more often.** "Use my Claude Pro subscription
  with Jarvis", "Only let Jarvis look at C:\Clients", "Whenever a new PDF
  shows up in my Downloads folder…", "Install Codex for me" and "Fix the
  failing test in my repo" each reach the right setting, door or offer. "My
  schedule is packed" no longer offers to make a schedule. A long request in
  several parts can be given to the team as an objective. Attaching a
  document while a model on your own computer is connected offers, once, to
  keep that chat on this PC.
- **Connections has one home.** Settings › Connections opens with
  **Discover**, **Connected** and **Import** tabs, and the other places that
  hold connections link to it. Model servers are added in one place, on
  Connections. Settings › Connections offers "Install for me" for Claude Code
  and Codex, and the starter model when Ollama is running with no model.
- **Your own sign-in apps, step by step.** Help has a new guide, "Your own
  sign-in apps", for setting up Google Drive, Gmail, OneDrive and Dropbox.
  Each card links to its section.
- **More you can connect.** A web service can take its key as a header or a
  username and password, as well as a bearer key. The tool configs a repo
  keeps for itself are offered for import from the folder you last coded in.
  A connected app is still off in each chat until you turn it on; on the
  Chat page, naming it in your message is enough (Jarvis asks before each
  use).

## Skills, how Jarvis works on pages, and a pointer you can watch in the browser sidebar (v1.454.0)

- **Skills in the browser sidebar.** Type **/** in the sidebar's box and the
  same skills the chat's "/" menu offers are listed; type more to narrow them,
  use the arrows, and press **Enter** (or **Tab**) to pick one. It shows as
  "Skill: <name>" above the box and goes with your next message only; **×**
  takes it off. Typing "/<a skill's exact name> your words" and pressing
  **Enter** runs it straight away. The skill is applied exactly as in chat. A
  "/" word that is not a skill's name (a file path, say) is sent as ordinary
  text.
- **How Jarvis works on web pages.** Settings › Connections › Your browser has
  a box for your own standing instructions, one per line, for example "Look
  for primary sources" or "Answer concisely, only what I need to know". The
  example buttons under it add a line; **Save** keeps them. They are added to
  every task that uses the browser, in the sidebar and from chat, from your
  next message. Leave the box empty to add nothing. They are checked when you
  save: more than 2,000 characters, or a line that reads like an instruction
  planted for the assistant, is refused with a sentence saying why. You can
  also change them from chat ("change my browser preferences to …").
- **You can see Jarvis at work on the page.** While Jarvis works in a tab, a
  small **"Jarvis is controlling this tab"** badge sits at the top of the page,
  beside the glow around the tab's edges. Before it clicks or types, a pointer
  moves to the button or field, which is outlined for a moment; a click shows a
  ripple where it landed. None of this can be clicked, it reads nothing on the
  page, and it does not change what the click does (the click only waits a
  moment for the pointer to arrive). With your computer set to reduce motion,
  the pointer is placed without moving and the ripple stays still, but the
  outline of the button or field still shows. If Jarvis cannot find the thing
  on screen, it draws nothing and the click or typing goes ahead as before.

## Archiving a Build › Code session keeps a short summary of it (v1.453.0)

- **Archive is in each session's ⋯ menu** in Build › Code (the row in the
  list, and the session's own ⋯ at the top). It is refused, in plain words,
  while a reply is running or the tool still works in the background
  (helpers, background commands, or a reply it writes by itself), because
  archiving stops the tool.
- **Archiving writes a short Markdown summary** and keeps it with the
  session: which folder and project, which tool, model and account; the
  folder **before** the work (the last commit before the session started,
  and what the first message asked); **what was done** (the later asks, the
  files that differ from the start, the commits made, and the test runs, CI
  checks and pushes the session's own steps show — each said as "finished
  without an error" or "ended with an error", never "passed"); and the state
  **after** (the branch and commit it ended on, what is left uncommitted or
  not pushed, plan steps not marked done, and the end of the last reply,
  quoted).
- **No AI writes it.** Iron Jarvis builds it from the session's own log and
  git, so it never claims anything the record does not show; what the
  record cannot tell (a folder that is not a git repository, a log that
  dropped older events) is said instead. Secrets are masked.
- **Open an archived session** from "Archived sessions" to read its summary
  at the top, with **Copy**, **Open file** (the summary is a `summary.md`
  file in the session's own folder inside Iron Jarvis's data) and **Bring
  back**. A session brought back keeps its summary, folded under one press;
  archiving it again writes a new one.

## Plain page names, and coding straight in your folder (v1.452.0)

- **Four pages have plain names**: Sentinels is now **Watchers**, Reflexes
  is **Rules**, Artifacts is **What agents made**, and Local fleet is
  **Model servers**. Searching for an old name still finds the page.
- **Chat › Code works straight in your folder.** "Work in a separate copy
  of the folder" (it was "Work on a new branch") is now off unless you turn
  it on. When it is on, the Changes pane's **Keep these changes** commits
  what is not committed yet and brings the work back into the branch your
  folder is on, in one press. A merge that cannot go through cleanly
  changes nothing and says why.
- **A tool you turn on for a chat runs without asking** for the rest of
  that chat; turning it on is the OK. The Tools menu now says so under
  "Turned on for this chat", and × turns one off. A few safety checks still
  ask every time (a commit or push, a cloud send from a Local only chat, a
  paid generation, Iron Jarvis's own protected settings).
- Gmail and Slack still use your own Google or Slack developer app.

## Settings, the automations pages and Help, today (v1.451.0)

- **Settings** keeps expert rows under a closed "More options" per group; it
  says how many of them you have changed. A search or a link still opens the
  row it names.
- **Plainer words**: "Default AI", "Add a model server", "Server address",
  "Its own tool servers", "your accounts". The program name Iron-Proxy stays
  only on its own Connections card.
- **Rules and watchers can be edited** in place. A new rule starts from the
  everyday shape: a message arrives, and an agent does a task. A rule whose
  source is not listening says so on its row.
- **Schedules** never show a cron string. "Every minute" is last, with a
  warning. A schedule you don't name gets a name from its task.
- **Tonight** takes a folder with the folder picker. A folder that is not a
  repository offers "Make this folder a repository". It refuses a drive, your
  home folder, a folder inside another repository, and a folder too big for a
  first commit.
- **The workflow step editor** shows where its JSON is wrong. Save refuses a
  step naming a tool this install doesn't have.
- **No more browser pop-up questions**: Undo, Replace and Publish ask on the
  page itself, and a held Enter can never answer them.
- **Presses that are off say why.** A long pasted message folds, and the
  whole message is still sent. Escape closes every composer menu.
- **Desktop dialogs** speak in sentences, and the technical details go to the
  logs ("Open logs").
- **Build** never stays on "Detecting…" or "Reading…": it says it couldn't
  check, and offers Try again.
- **Help** describes the five places (Chat, Build, Projects, Everything,
  Settings). Settings › System › Updates shows what changed in your version.

## The first run, and every connection, in plain words (v1.450.0)

- **No Claude Code or Codex yet?** The connect door offers "Install Claude
  Code for me" / "Install Codex for me": a Build terminal opens and runs the
  vendor's own installer where you can watch it. Nothing is installed
  quietly. When it finishes, press "I've installed it — check now" (or wait
  a minute) and the Sign in press appears.
- **Ollama**: the door's steps light up as you go — "Download a starter
  model" waits until Ollama is on this PC, "running but has no model yet" is
  said plainly, and ONE press, "Use Ollama for answers", makes it answer your
  chats. Prefer no download? A Google Gemini key has a free tier.
- **Keys**: the Anthropic and OpenAI tabs say where a key comes from and that
  the account is billed per use. A key is TESTED before it is saved — on
  Settings › Connections, on the Model servers tab and on the secure card in
  chat; a refused key stays in the box and nothing changes.
- **The chat home** counts only the helpers you need ("This PC is ready · 2
  optional extras not set up") and, once a real model answers, says what to
  try next.
- **Google Drive, OneDrive, Dropbox**: the sign-in setup lives on the card —
  the vendor-console step in words, the redirect address with Copy, and the
  App ID and App secret boxes. Save, then Connect.
- **"Needs Node.js"** comes with a "Get Node.js (free)" link; the Microsoft
  365 card links "How your admin sets this up" to its Handbook section.
- **Removing the model that answers your chats** (a model server, an
  endpoint, a disconnected key) tells you first what Jarvis will use instead,
  and the default is moved to a working model before anything goes — never
  left pointing at nothing. Disconnecting a key while its plan's CLI is
  signed in keeps the same answers. The move is a ledger row with Undo.
- **The browser add-on's consent page** says what site access really allows,
  for your browser by name; the Your browser card says when the add-on is an
  older copy than the app and tells you to press Reload.
- **Memory bases** added on the Memory page are read back: "Added and
  answering." or why not, the same words the chat tool says. The Obsidian and
  Notion settings rows say they need a restart and point at "Add a memory
  base", which works at once.
- **Destination switches** read "Listen for incoming messages", "Chat with
  Iron Jarvis" — never "true/false".
- **Import your tools** now reads OpenCode, Grok, Antigravity and Pi configs
  too (names only; a key in an address or argument is masked and kept in the
  vault).

## Did it work? The app tells you, everywhere (v1.449.0)

Every place that reports on work now gives the same, plain verdict.

- **Build › Code receipts** open with a check mark or a warning sign, say
  "2 min 14 s" instead of "2 m 14 s", and count failed steps apart ("2
  failed").
- **Running now** (the orb) lists workflow runs, media being made and night
  tasks beside replies and sessions, and never lists the same piece of work
  twice.
- **Workflows › Run history** has a Stop on every live row; a run that is
  stopping says "Stopping".
- **The mission screen** uses the same approval card as chat, and its receipt
  says how long the mission took and how many actions it ran.
- **One status vocabulary**: "Done", "Failed", "Stopped", "Waiting for you",
  "Part failed" and "Working" mean the same thing on every list, badge,
  toast and headline.
- **Activity rows** have a plain headline for every kind of event ("A webhook
  was refused · github-push · bad signature"), and never show a path, a key,
  an e-mail address, an ID number or anything that looks like a credential.
- **Spotlight** notifications say Done / Needs you / Part failed / Failed /
  Stopped and name the folder the work was in.
- **The chat** says when its chat list could not be read (with Try again),
  shows small failures (an upload, a rename, a project move) as one quiet
  line you can dismiss, and tells a screen reader "Reply ready." or "Reply
  stopped." A failed reply keeps its own Retry line.
- **Eleven pages** that used to print a raw error now say "<Thing> couldn't
  load. Try again."
- **A remote agent's Test** says what went wrong in plain words and never
  echoes the token you typed.
- **Browser and desktop safety checks** (a payment page, a password field, a
  destructive word, a flagged page) now ask you in the chat you are in — in
  chat, in a coding session and in an agent run — instead of filing a request
  you had to approve elsewhere and then repeat. One Allow covers exactly one
  action. These safety cards never go to your phone.
- **A Local only coding session** that needs a cloud model offers "Choose a
  local model" or "Send to <provider> once" (never when the chat is held
  local by an app that read your data).
- **Grok CLI** gets the same Sign in press as Claude Code and Codex when it is
  signed out.

## Chat configures the rest: models, tool servers, web services (v1.448.0)

Three more things you can set up by asking, and four small honesty fixes.

**Models in plain words.** "Use Claude for this chat instead of the default",
"I have a ChatGPT Plus plan, can you use that?", "Set up Gemini", "Use
OpenRouter for the big questions" now reach the default-model settings (a
card asks first; a key still goes only on the secure card) and offer "Open
Connections to set up a model". The card says plainly that changing the
default changes EVERY chat; one chat picks its model from the menu above the
box. "Ollama is running on this PC, use it" and "LM Studio is running on port
1234, connect to it" offer to add that model server and make it the default.
"Which local models can run on this laptop?" gets a line about model servers.

**Everyday nouns for last week's tools.** "Point your long-term memory at my
Obsidian vault in D:\Notes" (a memory base), "For the ClosePilot folder,
always use Codex in Auto mode" (that folder's coding defaults), "Have my
coding sessions run with a reviewer that checks every change" (the session
team), "Every night at 11 run the tests in C:\Projects\ClosePilot" (a night
task beside the schedule) now reach their tools without the exact words.
"Click the Submit button in the QuickBooks desktop app" gets the plain line
that Iron Jarvis has no way of its own into other programs on your PC, and
no longer arms a browser click. "How do depreciation schedules work?" arms no
schedule tool. "Can you work with QuickBooks Online?" asks the Guide.

**A tool server from chat.** Paste a server's config block, command line or
address ("add the filesystem server from the Claude docs", "add this server:
npx -y @modelcontextprotocol/server-filesystem C:\Clients", "connect this
tool server at https://mcp.example.com/sse"). One card shows the command it
will start. A key or token is never taken from your message: a secret with a
value in the pasted text is refused in words, a blank or placeholder secret
is asked for on the secure card, and the server is added only once every
secret is saved. Undo removes a server chat added, and refuses if you edited
it on the Tools page since. Microsoft 365's locked program, a name in use,
the same server under another name and a missing Node.js or uv are refused
before anything starts.

**A web service from chat.** "Hook up our firm's pricing API at
https://api.example.com/v1 so you can look up fees" adds it to Your web
services; its key goes on the secure card; an address on this PC or your own
network is allowed and said so. Undo removes it. "Let the Jarvis on my office
PC take overnight jobs from this one" gets the plain line about tasks from
another computer.

**Honesty fixes.** A "Setting changed" card names the option ("Ask before
commands → Auto", "Mark 1 → Daylight"), never an internal value, and its link
opens that very row in Settings. The chat approval card can answer "No, tell
it what to do instead": type what you want, Send declines the action and
hands your words to the running reply (or sends them as the next message if
the reply has ended), and the dock says that the message box comes back when
you answer. A chat waiting for your OK says "Needs you" beside its name in the
chat list, not only by colour. Build › Code's Session tab says "Runs · cloud"
or "Runs · on this network" for the tool's model when that is known.

Not driven with a real model or in the packaged app yet; the matchers were
checked with 94 fresh sentences on a scratch app.

## The approvals menu shows names only (v1.430.0)

The menu behind the shield chip in the chat box (in Chat and in Chat › Code)
now lists the levels by name only, one line each:

- **Ask before every action**
- **Ask before commands** (the default)
- **Accept edits**
- **Auto** (called *Auto (its own safety check)* until now)
- **Plan** (a coding session only)
- **Don't ask**

To see what a level does, rest the pointer on its row for half a second: a
small box beside the menu says what that level does and what it may read.
Once one box is showing, moving to another row shows that row's at once.
The arrow keys do the same when the highlight rests on a row. On a touch
screen, hold a finger on a row for half a second; lifting it then does not
pick that level, and a normal tap still does. The chip itself still shows
the current level's full description when you rest the pointer on it.

Nothing about what each level does changed, and "Auto (its own safety
check)" typed in chat still finds Auto.

## Build › Code: more of each tool, and a calmer look (v1.447.0)

**Antigravity.**

- **Your own Antigravity agents.** The agents `agy agents` lists appear in
  the mode menu after Antigravity's own modes. A conversation keeps the agent
  it was started with, so choosing another one later is refused in words.
- **Iron Jarvis's own tools.** Tick Memory or Browser in the Iron Jarvis tools
  chip and Antigravity can use them. Iron Jarvis then starts Antigravity with
  one process per session, which is the only way Antigravity takes them.
- **Its MCP servers.** The Session tab lists your Antigravity MCP servers by
  name, turned on or off. Antigravity does not say whether a server
  connected, so "turned on" means only that.
- **Share your profile.** Settings › Memory's "Share with Build" now has an
  Antigravity switch. It writes Iron Jarvis's block into
  `~/.gemini/config/AGENTS.md` (not `~/.gemini/GEMINI.md`, which Google's
  Gemini CLI reads too).
- **Signing in.** Signing in by running `agy` in a terminal is enough for the
  Code view: Antigravity keeps one sign-in for both.

**Claude Code.**

- **Sign in to an MCP server.** A server that needs you to sign in has
  "Sign in…" in its menu on the Session tab. It opens the server's page in
  your browser, and the list changes on its own when you are signed in.
- **`/model sonnet[1m]`** keeps the 1M context window.
- **The `/` menu after a restart.** Your commands are listed again soon after
  Iron Jarvis starts, without waiting for a session to run in that folder.
  To read them, Iron Jarvis starts Claude Code for a moment in that folder:
  no message to a model, none of your MCP servers, none of your hooks.
- A full disk when Claude Code closes is now said in words.

**Codex.** The Session tab's MCP list has **Reload MCP servers**, and a
server your Codex settings turn off is listed as off.

**Grok.** A server your Grok settings turn off is listed as off.

**A calmer look across the app.**

- Form labels are in ordinary sentence case instead of small capitals.
- In lists of saved things (templates, destinations, extensions, memory
  profiles, saved workflows), Edit is a pencil and Delete is a trash can.
  Rest the pointer on one, or move to it with the keyboard, and its words
  show. Delete still takes two presses, and the second says "Delete?" in
  words.
- Refresh at the top of the Fleet and Usage pages is a refresh arrow. Copy
  beside the add-on's folder is a copy icon. Dismiss on Build's terminal
  notices is a ×.
- On a phone, the demo notice no longer squeezes down to one word per line.

## Antigravity's models and sign-in, and a chat that works in a folder (v1.443.0)

**Antigravity's models show up before you start a session.** Once you have
signed in to Antigravity (run `agy` in a terminal and sign in with Google),
Build › Code's model menu lists Antigravity's own models, each with the
thinking levels it takes, before any session has run. Iron Jarvis reads the
list in the background by running `agy models`. That makes no model call and
starts no conversation. It is read again every ten minutes, or sooner when
Antigravity's own files change. If Antigravity could not list its models,
the menu says why in one line.

**Antigravity on the Connections page.** The "Your plans and your own model
servers" card now has an Antigravity row:

- **Signed in · Build › Code** when Antigravity listed its models.
- **Installed · not signed in**, with a **Sign in** button that opens a
  Build terminal running `agy`. Sign in with Google there, then press
  **Check again**.
- **Not detected**, with a link to get it, when it is not on this PC.

Antigravity keeps its sign-in in Windows' own credential store, so Iron
Jarvis learns it only by asking `agy` itself. It asks only after
Antigravity's own sign-in has finished, so it never sets off a sign-in
prompt. Antigravity is used in Build › Code only: chat's model menu does not
use it. It is also separate from the **Google (Gemini)** card, which is
Google's pay-per-use API with its own key.

**Antigravity no longer updates itself while Iron Jarvis runs it.** Every
`agy` run starts Antigravity's own updater. Iron Jarvis turns that updater
off for the runs it starts, the model-list check and Code view sessions, so
stopping a session can never cut an update off half way. When you run `agy`
yourself, it still updates as usual.

**Work in a folder without a project.** On a new chat, the folder chip sits
beside "No project" and reads **No folder · Choose…**. Choose… opens
Windows' own folder picker, the same one Build › Code uses; in a browser you
get the app's folder list instead. Jarvis then works on the files in that
folder, saves what it makes there, and remembers the folder with the chat.

- **In a conversation**, the folder chip sits in the composer's bottom row
  next to the project. **+ › Choose a working folder** and the side panel's
  **Change** open the same picker.
- **Projects:** choosing a folder outside the chat's project takes the chat
  out of the project; a folder inside the project keeps it.
- **A folder Jarvis can't use:** if Jarvis can't work there (it can't save
  files there, for example), it says so under the chip.
- **Clearing:** **×**, or **+ › No folder for this chat**, goes back to no
  folder.
## Nothing goes quiet on you (v1.446.0)

The second group from the ease-of-use review: things that failed, waited or
paused without telling you.

- **A rule that can't start tells you.** When a rule matches but can't start
  its action (its workflow was deleted, its remote agent didn't answer,
  Emergency stop is on), the bell, the tray and your phone say "Your rule X
  couldn't start" and why — once, not for every signal that fails the same
  way. Pressing Test on the Rules page shows the answer right there instead.
- **A folder watcher's suggestion shows in the bell** as a quiet line, "Your
  watcher X noticed changes · Review". It never pings or messages your phone.
- **A workflow with a failed step says so.** A step set to "skip if it fails"
  that fails no longer hides behind Done: the run finishes "Part failed"
  (amber), and the bell, tray and phone say which step failed.
- **Emergency stop can't be forgotten.** While it is on, a red strip at the
  top of every page says "Emergency stop is on — nothing starts on its own",
  with Turn off. It has one name everywhere (the Autonomy page's button no
  longer says "Kill switch"). The Schedules page says schedules are skipped,
  and each next run reads "Paused by Emergency stop".
- **Schedules can be paused.** Each schedule has an On/Paused switch; pausing
  keeps everything about it. Editing a schedule uses the same Repeat list as
  adding one, so you don't need to write cron.
- **Restoring a backup can be undone.** Before anything is replaced, Iron
  Jarvis saves your current state as a backup marked "saved before a
  restore". If that can't be saved (a full disk), nothing is restored. It also
  won't restore while anything is running, and names what is.
- **Editing a notification destination changes only what you changed.** Its
  alert list stays as you set it, and settings the form doesn't show are kept.
  An email destination can listen for incoming mail from the Notifications
  page (under Advanced: reading mail server, port, "Listen for incoming
  mail", allowed senders). Adding a destination with a name you already use is
  refused instead of replacing it.
- **Every chat can be found.** "Show older chats" at the end of the list loads
  the next 100. "Search chats" looks through the names and the words said
  inside all your chats, with the matching words shown under each one. Ctrl K
  finds a chat by its name past the newest 100 too.

## Your first ten minutes, smoother (v1.445.0)

An ease-of-use review (docs/EASE-OF-USE-REVIEW-2026-11-07.md) walked the app
as a brand-new user. This release fixes nine of the ten things that most
decided the first session.

- **"Use Claude for answers" / "Use ChatGPT for answers"** now use your plan's
  own default model. Before, they saved a fixed model that a ChatGPT plan
  refuses, so the first answer could fail.
- **An API key is tested before it is saved.** A wrong key changes nothing
  and the reason stays on screen. A good key becomes what answers you, and
  the panel says "Done — Anthropic answers you now". Paste an OpenAI key into
  the Anthropic tab and Iron Jarvis spots it and switches tabs.
- **After you sign in to Claude Code or Codex** in the terminal that Sign in
  opens, the panel turns green within seconds. If it doesn't, press "I've
  signed in — check now".
- **While the demo model answers,** a "Connect a model" button stays just
  below the message box in every chat, before and after your first message.
  If Iron Jarvis already sees a model you can use, the strip at the top
  offers "Use Claude Code for answers": one press makes it the default for
  every chat, agent and schedule. In the model menu, a model that isn't
  connected says "Not connected yet", and the menu ends with "Connect a
  model…". A demo reply says it is the demo once, not four times. A saved web
  sign-in in the browser vault never counts as a connected model.
- **Creative Studio's "Ready" line tells the truth:** with Autopilot off it
  says the engine asks you in the terminal before each step. Autopilot itself
  still starts on: the Studio is built to run hands-off (your decision).
- **File Search looks in your own folders:** the Iron Jarvis chat files folder
  (Documents\Iron Jarvis), each active project's folder, and any folder you
  add. It never looks in Iron Jarvis's own data folder. The "Folders to
  search" card on the File Search page lists them, with "Add a folder…",
  "Also search Documents" and × to remove one; a change applies to your next
  search. "By meaning" looks only in folders you added, because chat may
  recall short passages from them. It needs a local embedding model (Ollama
  with `nomic-embed-text`). Documents is not searched by default: a contents
  search opens every file it looks at, which is slow in a large Documents
  folder.
- **The two Code views point at each other.** Chat › Code is where Jarvis
  writes the code; Build › Code runs your own Claude Code or Codex. Each start
  box has a one-line link to the other. With no coding tool on the PC, Build ›
  Code says so and offers "Get Claude Code", "Get Codex", or "let Jarvis write
  the code in Chat › Code". A signed-out Claude Code, Codex or Grok shows a
  Sign in button right beside it.
- **Removing an extension from its Tools card takes two presses**, like
  everywhere else. One press used to delete it and its saved keys.
- **Ctrl K and links move an open Settings page.** Choosing "Default model"
  from Ctrl K while Settings was already open used to change only the address.
- **On a phone,** the Tools page and the Microsoft 365 card no longer push
  the page sideways.

Not changed yet: Settings › Connections and chat's secure key card still save
a key before testing it; the review's other items are listed in that file.

## Setup from chat, proven on 30 requests (v1.442.0)

The chat-first plan set itself one test: 30 everyday setup requests, asked in
plain words. At v1.432.0 chat could finish 2 of them. Now 23 finish in chat
(for example "add my local Ollama model and make it my default", "make a
project for the Johnson audit in D:\Clients\Johnson", "keep this chat on my PC
only", "queue 'update the README' for tonight", "make my coding sessions use a
team: Codex writes, Claude reviews"). Pairing the browser add-on gives you a
button to its page. The other 6 are things the app does not offer, and chat
now says so plainly instead of pretending:

- **Using Jarvis from your laptop:** its screens open only on this PC.
- **Reading your screen and clicking through an installer:** Iron Jarvis does
  not control the Windows desktop.
- **A monthly spending limit for the built-in builder agent:** built-in agents
  have none; a custom agent you make (it can work like the builder) can have
  one.
- **Talking to Jarvis:** press the microphone in the chat box ("Dictate your
  message"), or use Voice chat in the ⋯ menu; "Read replies aloud" is the
  setting chat can switch.
- **Zapier starting a task:** a service on the internet cannot reach this PC.
  Jarvis can still send its events to Zapier.
- **A watcher filing your PDFs:** a folder watcher notices new files and
  suggests the work; you approve it in Autonomy › Proposals before anything
  runs.

New in this release:

- **Your own theme colours from chat.** "Change the theme to something green
  and calm" makes and shows a theme of your own colours at once, with Undo
  on the card. Picking a built-in theme afterwards replaces it.
- **Mail and messages.** "…and send it to my phone" offers to add a
  notification destination. Setting up Slack as the place Jarvis messages you
  gives a button to Settings › Notifications, where the Slack app's steps
  are, and no longer offers to connect the Slack app itself. Connecting
  Gmail says that Google needs a one-time setup and opens Gmail's card.
- **Backups.** "Back up my settings to my OneDrive folder" sets the backup
  copy folder (your synced folder on this PC, not the OneDrive app).
  Backups run once a day and keep the newest 7; how often is not a setting.
- **Rules for new email.** "Whenever a new email from a client comes in…"
  offers a rule and an email destination that listens. A workflow is made
  with the chat's workflow card (press Save), then the rule in the next
  message. Who may trigger it (Allowed senders) is set on the Notifications
  page.
- **Build › Code defaults** are offered for "make Build › Code use Codex for
  this repo". Full access is never a folder default, and an account is not
  kept with a folder.
- **Chat knows what each setting does.** When Jarvis looks a setting up, it
  now sees what the setting does and what each choice means. For example,
  "stop asking me before you run tests" now finds the right answer: the Auto
  approvals level runs your project's own tests without asking (after its
  safety check), and you confirm the change on a card.
- **Adding a second account** still needs "Several accounts" switched on in
  Settings › Connections first; chat says so and opens the page.

## Build › Code catches up with each tool (v1.441.0)

Build › Code runs your own coding tool (Claude Code, Codex, Pi, OpenCode, Grok
or Antigravity) and shows its work. This release brings much more of what each
tool offers in its own terminal into the Code view, and fixes what the review
of v1.439.0 found.

**Branch and rewind a conversation.** Point at one of your own messages and
press its ⋯.

- **Branch from here** opens a new session with the conversation before that
  message, on the same tool, model, mode and account. The message waits in the
  new session's box for you to change and send. The session you branched from
  stays exactly as it was. "Branch" in a session's own ⋯ menu branches the
  whole conversation.
- **Rewind to here** (press it twice) takes that message and everything after
  it out of the conversation, and puts the message back in the box.
- Neither one changes your files. To put a file back, use Revert in the
  Changes tab.
- Neither runs while a reply runs, while the tool still works in the
  background, or while the conversation is open in a terminal.

| Tool | What it offers |
|---|---|
| Claude Code, Codex, Pi | Branch, Branch from here and Rewind. With Claude Code, a branch or a rewind is made when you send its first message. |
| OpenCode | Branch, of the whole conversation only: OpenCode has no way to rewind. |
| Grok, Antigravity | Neither: these tools offer no way to branch. |

Only messages sent from this version on have a ⋯; "Branch" of the whole
conversation works for any session.

**The Session tab: background work and MCP servers.**

- **Background work.** The tab lists what the tool still runs in the
  background: Claude Code's helpers and background commands, and Codex's
  background commands. To stop one, open its ⋯ and choose Stop. Only that one
  stops; a running reply goes on, and the conversation says what you stopped.
  A Codex session that still runs a background command is no longer closed
  for being idle (two silent hours still end it).
- **MCP servers.** Each of the tool's own MCP servers has a ⋯ holding only
  what that tool can do. Claude Code: Reconnect, Turn off and Turn on (like
  its own `/mcp`, it remembers "off" for this folder). Codex: Sign in… on a
  server that needs it, which opens Codex's own sign-in page in your browser.
  Iron Jarvis never signs in for you.

**Claude Code.**

- **Effort changes at once**, with no restart. A reply that is already running
  keeps its effort; the new one applies from your next message, and the line
  under the box says so.
- **A plan card follows the mode you pick.** If you change the mode chip while
  a plan card waits, the card's buttons change with it, and approving the plan
  keeps the mode you picked. Approving never puts the session back in Full
  access by surprise, and Enter never approves a plan that would.
- `/model opus`, `/model sonnet` and `/model haiku` set the model chip. A name
  Iron Jarvis does not know goes to Claude Code as you typed it.
- Your own commands named like a Claude Code terminal command (for example
  your own `/status`) now reach Claude Code, and the `/` menu shows only this
  project's commands.
- A session renamed while its tool was stopped is renamed in Claude Code too,
  when the tool next starts.
- Typing `/export` points you at Export as Markdown in the session's ⋯ menu.

**Codex: Plan.** Choose Plan in the mode menu. Codex looks around and writes a
plan, and declines every file change while it plans; it still asks before it
runs a command, and a command you allow can still change files. When the plan
is ready, the card under it asks you: "Approve, then ask first" starts the
work at once in Ask first, and Deny keeps Codex planning so you can say what
to change. Plan has to name a model to Codex: when none is picked, Iron Jarvis
asks Codex which model the conversation runs on. If Codex cannot say, a line in
the conversation tells you this message was not put in Plan; pick a model in
the model chip and send it again.

**OpenCode.**

- The model menu shows OpenCode's own model list, even before your first
  OpenCode session. The list is read quietly in the background, with no model
  call.
- Your own OpenCode agents (the primary agents in your OpenCode settings)
  appear in the mode menu by name. They ask first, except where the agent's
  own permission rules say otherwise, and you can keep one as a folder's
  default. Shift+Tab never steps into one of your agents: pick it from the
  menu.
- Outside Full access, OpenCode does not start helper agents. A helper's
  questions cannot reach Iron Jarvis, so it would wait for ever. A rule of
  your own about helpers is kept.
- OpenCode's question tool stays off for the same reason: its questions never
  reach the screen.

**Grok.**

- A Grok conversation you started in a terminal now shows what was said
  before, read from Grok's own saved files (they are never changed).
- The Session tab lists Grok's own MCP servers by name with their state. If
  one needs a sign-in: press Open in terminal, type `/mcps`, choose the server
  and press i. Iron Jarvis never shows or keeps a server's address or key.
- **Share with Build** on the Memory page has a switch for Grok (off until you
  turn it on). Good to know: Grok also reads Claude Code's own
  instructions file (`~/.claude/CLAUDE.md`). With both the Claude Code and the
  Grok switches on, Grok reads your profile twice, and with only the Claude
  Code switch on, Grok still sees it. Grok's own setting
  `[compat.claude] agents = false` turns that off.

**Pi.** Start a message with `!` to run a command yourself: Pi runs it, and its
model sees the command and its output. Start it with `!!` to keep it out of
the conversation. A line under the box says so while the words would run.
Stop stops the command. Pi does not list its MCP servers to Iron Jarvis; type
`/mcp` in the box and Pi says each one's state.

**Antigravity.**

- A conversation you started in a terminal now shows what was said before.
- A note typed while a card or a question waits is refused, and your words
  stay in the box: answer the card first. Before, the reply failed.
- A question that lets you pick several answers shows checkboxes.
- The Iron Jarvis tools chip is not shown: Antigravity does not take them.
- Antigravity does not report its account's usage limits, so the Usage tab
  cannot show them; its own terminal's `/usage` does.
- Signing in to Antigravity in its own terminal may not be enough for the
  Code view: that has not been proven yet. If a reply still says Antigravity
  is not signed in after you signed in there, this is that known gap.

**Check usage now.**

- The button asks only when Iron-Proxy can keep the number. An older
  Iron-Proxy, or one that is not answering, says so at once.
- A login with no Claude plan says it has no plan usage to report, instead of
  "try again in a minute" for ever.
- While a coding session or a Build terminal is working on an account, Iron
  Jarvis checks its usage again by itself every few minutes (less often when
  the login reports no numbers). A session waiting for your answer is not
  checked, and nor is a terminal that has printed nothing for minutes.

**Export, Enter and Copy.**

- **Export as Markdown** saves the whole conversation, including the parts not
  on screen. A line says it is reading while it works. If the session's record
  trimmed its oldest steps, or a part could not be read, the file says so
  right under its title.
- **Copy reply** copies the whole reply, including the words before and after
  its steps.

**What each tool still cannot do, and why.**

| Tool | Not offered | Why |
|---|---|---|
| Claude Code | Signing in to an MCP server from the Session tab | Use Open in terminal, then `/mcp` |
| Codex | Reconnect or Turn off for an MCP server | Codex has no switch for one server |
| Codex | Auto review; Auto edits on Windows | Iron Jarvis cannot show you yet what Codex's own reviewer decided; Codex's Windows protection is not set up on this PC |
| OpenCode | Rewind; branching from one message; helpers outside Full access; its question tool | OpenCode offers no rewind, and its helpers' and questions' messages never reach Iron Jarvis |
| Grok, Antigravity | Branch and Rewind | Neither tool offers it |
| Pi | A list of its MCP servers | Pi says them only as a message (type `/mcp`) |
| Antigravity | Iron Jarvis's tools; usage numbers | It takes no tools from another app and reports no usage |

The full comparison of Build › Code against each tool's own terminal is in
`docs/BUILD-CODE-PARITY-2026-11-06.md`.

## Loose ends tied up (v1.440.0)

- **Delete a goal you made in chat** from the Autonomy page: press Delete
  twice. Its schedule goes too, and so does anything it was allowed to do
  without asking.
- **Sign an account out** on the Iron-Proxy card in Settings › Connections:
  press Sign out twice. The account stays and needs a new sign-in before it
  is used again. A terminal or coding session running on that account at the
  time may stop being able to answer.
- **A coding session's pull request says what Iron Jarvis is doing with its
  checks:** one quiet line under the pull request, for example "Iron Jarvis
  is watching its checks. Next look at 3:40 PM." It goes away once the pull
  request is merged.
- **Remove a saved calendar link** on the Reflex page (press Remove twice).
  The Reflex page also now shows correctly which sources are on; before this
  it showed every source as off.
- **Help shows your keyboard shortcuts** — the keys the desktop app really
  holds right now, including when another app took the usual one.
- **Tidier inside:** unused screens and a few old routes nobody called were
  removed. Nothing you use changed.

## Antigravity in the Code view, and usage you can check now (v1.439.0)

**Google Antigravity is the sixth tool in Build › Code.** If Google's
Antigravity CLI (`agy`) is installed on this PC, the Code view offers it
beside Claude Code, Codex, Pi, OpenCode and Grok. It runs with all of its own
tools, skills and settings; Iron Jarvis starts it, draws it, and answers its
questions as cards.

- **Sign in once, in Antigravity.** Antigravity uses its own sign-in, not
  your Iron-Proxy accounts. If it is not signed in, the reply says so: open a
  terminal (Build › Terminal can start `agy`) and sign in there.
- **Modes.** "Antigravity's own rules" is Antigravity's Default: it asks
  before every command, and file changes follow your own Antigravity
  permission settings. Out of the box, it changes files without asking,
  inside and outside the folder. Auto edits and Plan are Antigravity's own
  (Plan is not read-only). Full access lets it act without any card and
  starts a new Antigravity before your next message. To be asked before
  every file change, set `"toolPermission": "strict"` in Antigravity's own
  settings file (`~/.gemini/antigravity-cli/settings.json`). Iron Jarvis
  never changes that file for you.
- **Cards and questions.** Antigravity offers only Allow once or Deny, never
  "for this session". Denying ends its reply. When it asks you a question,
  the choices appear as a card, and you can type your own answer.
- **Models and thinking.** The model chip lists Antigravity's models, and
  the thinking chip lists only the levels that model has. Both change
  without a restart.
- **A message while it works** reaches the reply it is working on, as with
  the other tools. **Stop** ends it.
- **Your saved Antigravity conversations** for the folder appear in the list.
  "Open in terminal" reopens one with `agy --conversation <id>`.

**Check an account's usage now.** Next to an account's usage, a small refresh
icon ("Check usage now") asks that account's own Claude Code or Codex how much
of its windows is used. The icon is on the Iron-Proxy card in Settings ›
Connections, on the account cards in Chat › Code, and on the Usage tab in
Build › Code. The check is not a model call and uses none of your usage. The
new number reaches every place that shows it, including the model menu.

- While a reply runs on an account, Iron Jarvis checks it again by itself
  about every three minutes. When nothing runs, it checks nothing.
- One account is checked at most once every 20 seconds, so a second press
  shows the last answer.
- Claude Code sometimes has no usage answer ready; the line then says "try
  again in a minute".
- Grok and other accounts have no such check, so they show no icon.
- The Connections card now also shows each account's percent used, for
  example "13% of the 7-day window used".

**Smaller things in Build › Code:**

- **Copy a reply:** hover over a finished reply (or move to it with the
  keyboard) for a copy icon.
- **Export as Markdown:** the session's ⋯ menu saves your messages, the
  replies and the step titles to a `.md` file.
- **Claude Code's plan card** names its buttons by the mode each leaves you
  in, for example "Approve, then ask first" or "Approve and edit freely".
- **Typed settings:** `/model <name>` and `/effort <level>`, typed for Claude
  Code or Grok, set the model and effort chips.
- **Terminal-only commands:** Claude Code commands that only work in its own
  terminal, such as `/login` or `/permissions`, show in the `/` menu with a
  note saying so.
- **Grok's always-approve commands** (`/always-approve`, `/auto`) are not
  offered: use the mode chip.
- **Commands for this project only:** OpenCode's `/` list shows the commands
  of this project, never another project's, and shows each command's
  argument hint.
- **Codex warnings** about your config and failed hooks appear as one line
  each.
- **Renaming reaches the tool itself:** a renamed session is also renamed
  in Claude Code or Codex, so their own resume lists show the same name.

The full comparison of Build › Code against each tool's own terminal is in
`docs/BUILD-CODE-PARITY-2026-11-06.md`.

## More setup from chat (v1.438.0)

Each of these shows a card before anything changes, and each change has an
Undo that puts back only what that change did. If you changed the same thing
again since, Undo says so and leaves your newer work alone. Keys and tokens
go only on the secure card, never in the chat.

- **The night shift.** "Queue 'update the README' for tonight in
  D:\code\shop", "what's queued for tonight?", "cancel the README task",
  "move it to the top". The card says when the task runs and in which folder,
  and you click it to allow it once. A chat set to Local only cannot queue
  work that would run on a cloud model.
- **A folder's coding setup.** "Use Codex for D:\code\shop", "Codex writes,
  Claude reviews in this folder", "stop using the team here". Chat uses the
  folder you name, or the chat's project folder, and asks which folder when
  neither is known. Full access and "Don't ask" can't be a folder's default,
  and an account is picked when a session starts, not saved for the folder.
- **Model servers.** "Add my LM Studio server at 192.168.1.20:1234 and make it
  the default", "remove the DGX server". The card says whether the server is
  on this PC or your network, or on the internet. The default can only be a
  model that server lists itself; if the list can't be read, chat says so.
- **Accounts.** "Add a second Claude account" makes it in Iron-Proxy and opens
  its own sign-in window; you sign in there, and Iron Jarvis never sees the
  login. "Put TSI and VR2 in a group called Work" changes only that group;
  your other groups stay.
- **Memory bases.** "Use Notion as your long-term memory" (the token goes on
  the secure card and it works without a restart), or a notes folder such as
  an Obsidian vault. Chat can also make it where Jarvis saves what it learns.
  The reply reads the new base back — "Added and answering.", or why it is
  not — exactly as the Memory page does.
- **Preferences, lessons and personas.** "Forget the preference about short
  answers", "change it to bullet points", "add a lesson: always cite the IRS
  section", "make a persona that answers like a CPA", "delete the CPA
  persona". A preference about someone else ("the client's preference for
  paper statements") or a lesson in a course is left alone.
- **Answer language.** "Answer me in Spanish from now on", "stick to Spanish
  even when I write English", "use bullet points by default". These change
  your profile, so every later chat and agent follows them.
- **The desktop app.** "Start Iron Jarvis with Windows" (always asks first) and
  "keep running in the background when I close the window". The desktop app
  picks the change up within about 15 seconds, and the tray switch and these
  settings always agree; the newer choice wins.

## Set up a coding session before it starts (v1.437.0)

The box you start a Chat › Code session in now has the same row as an open
session: how much it asks (with Plan), **Try on…**, **Team**, the effort level
(only for a model that offers levels) and the model picker with your Claude
Code and Codex accounts. Set them before you type; **Send** makes the session
with all of them, and your first message is tried on the models you picked.

- **Try on** needs a folder in a git repository and a session that does not
  start by planning. Until then its chip says why.
- **Team**, left alone, is the folder's saved team.
- **Local only**: the model list, Try on and the team list offer only models on
  this PC or your network, and a cloud model you had picked is taken back, with
  a note saying so.
- If your first message is refused, nothing is lost: the box comes back with
  your words and every choice you made.

## A button to the right page, and honest answers (v1.436.0)

- **One button when a step needs a page.** Ask chat to sign in to Claude Code,
  Codex or Grok, add a second account, sign in to Microsoft 365, get your
  Microsoft 365 admin's approval, set up Google or Slack, install the browser
  add-on, or pick a local model, and one button appears under the reply that
  opens the exact place. For Claude Code, Codex and Grok it is the same Sign in
  button as in Settings. The button appears only when your message is about
  that step, and it does nothing until you press it.
- **Help can open at the right section.** A button can open the Help page
  scrolled to the part that explains the step.
- **Plain answers for what Iron Jarvis doesn't do.** Chat says clearly that it
  has no way of its own to use your desktop programs (it works in a browser,
  with your files and through apps you connect — you can paste a screenshot),
  that its screens open only on this PC, and that Full access and "Don't ask"
  can't be a folder's default.
- **A note when chat claims a change that didn't happen.** If a reply says it
  turned on read aloud, switched your theme, paused an agent, deleted a
  schedule, connected an app, or kept the reply local only — and nothing in
  that reply actually did it — a note under the reply says so, naming the
  setting the way Settings does. No note appears for questions, offers, your
  own documents (a depreciation schedule, a model in your spreadsheet), or a
  change that really went through.

## Projects, agents, apps and more from chat (v1.435.0)

Each of these shows a card before anything changes, and each change has an
Undo. An Undo puts back only what that change did; if you changed the same
thing again since, it says so and leaves your newer work alone.

- **Projects.** "Make a project for the Johnson audit in
  D:\Clients\Johnson", "rename it", "archive the Smith project", "switch to
  the Smith project". The folder must already exist; nothing on your disk is
  made or moved. Undoing a new project that already has chats or work filed
  in it archives it instead of removing it.
- **Agents.** "Create an agent called reviewer on GPT that only reads", "give
  the bookkeeper agent a $20 monthly limit", "pause the intake agent", "delete
  it". Only agents you made can be changed. A deleted agent's folder goes to
  the trash, and Undo brings it back.
- **Apps.** "Turn off the GitHub app", "disconnect Notion", "add the QuickBooks
  server I use in Claude Desktop". Disconnecting keeps an encrypted copy of
  the app's keys, only for the Undo; the copy is deleted with the Undo record
  (after 90 days by default). Bringing a server over shows what it will start,
  and its keys go straight from that program's settings into the vault —
  never through chat.
- **Where notifications go.** "Stop sending to Slack", "only send me
  failures", "add an email destination" (you type the password on the secure
  card). A finished task is one alert whether it worked or not, so "only
  failures" cannot leave out the tasks that worked; and schedule results and
  workflow notices reach every destination whatever you choose. Chat says
  both when you ask.
- **Goals made in chat.** Chat can now see them, and pause, resume, stop or
  delete one. Stopping a goal that is running stops that run too.
- **Local only.** "Keep this chat on my PC" turns it on at once. Turning it off
  always shows a card you must click (Enter does not answer it). A chat that
  Microsoft 365 holds stays local only. Either way it applies from your next
  message. Settings › Models › "Start new chats local only" makes every new
  chat start that way.
- **What changed, and Undo.** "What did you change today?" lists Iron Jarvis's
  recent changes by name only — never values, passwords or file contents.
  "Undo that" shows a card naming the exact change before it undoes it (click
  only; Enter does not answer it).

## Chat finds the right tool (v1.434.0)

- **Chat knows every setting by its name.** Ask "turn on follow-up
  suggestions", "use Ollama by default", "stop asking before every command" or
  "make the app dark", and chat offers the right setting. It knows each
  setting by the name Settings shows and by the other words people use. It
  changes a setting only when you ask for a change; a question such as "what
  theme am I using?" only looks it up.
- **Settings work in coding sessions too.** In Chat › Code you can change a
  setting the same way. While a session is planning, it can only tell you a
  setting's value.
- **With Auto tools off,** chat can still tell you what a setting is set to,
  but it can't change one.
- **Making a schedule, goal, webhook or watcher from chat asks first.** "Remind
  me every weekday at 8 to check the bank feeds" now brings up the schedule
  tool, and you see a card before anything is made, even if you had set that
  tool to run without asking. "Schedule a call with Dana" is no longer read as
  one of the app's schedules.
- **"Run the month-end workflow"** runs your saved workflow by its name, after
  asking.
- **A tool you set to Never run stays off.** Before, a sentence that asked for
  it could still bring it up in chat.
- **Every chat says which screen it's on.** A screen setting (theme, read
  aloud, persona, Auto tools) changed from a coding session, a Build
  terminal's chat, Try on several models, Choose account, or a plan you
  allowed now lands on the screen you are using.
- **Fewer false alarms.** "The Daylight Savings rules", "my stable channel",
  "slack time" and "don't ask me what I want for dinner" no longer bring up
  setting or rule tools.

## Seventeen fixes from the deep review (v1.433.0)

A deep review of the whole app found 17 bugs. All are fixed in this release:

- **Theme, read aloud, persona and Auto tools now work from chat.** Asking
  chat to switch to the Daylight theme, turn on read aloud, change your
  default persona or turn Auto tools on or off now changes the screen right
  away. Before, chat said "Setting changed" and nothing happened. Settings ›
  Appearance now has one theme control. A theme you made yourself is picked on
  the screen only, so chat reports the theme as "not set" while one is on.
- **Turning off "Run app tools without asking" from chat** now also clears
  each app's own setting, the same as the Tools page switch.
- **"Switch to the Smith project"** refuses a project that doesn't exist or is
  archived, instead of quietly filing new chats under it.
- **Secrets stay out of chat.** A Discord destination's webhook address and a
  database connection string with a password are now pasted on the secure
  card, never typed in chat. The message box holds them back if you paste one.
- **Approval cards** now say when a tool is new or changed in its app since
  you added the app.
- **The browser sidebar** now:
  - shows the app's own notes at the end of a turn (for example when the
    model wrote nothing, or said it saved a file that doesn't exist);
  - no longer shows a rewritten reply twice;
  - uses plain words for errors, with the technical text folded under Details;
  - never tells you to press a button it doesn't have.
- **Settings › Maintenance "Restart Iron Jarvis"** now really restarts it,
  keeping running coding tools, instead of quitting. It no longer causes a
  false "keeps crashing" warning.
- **A failed update install** is no longer run anyway on the next Quit.
- **Approving or rejecting a review** clears the bell and the orb at once.
- **Turning a feature off** (Slack, calendar, phone messages, watchers,
  autonomy, model servers) also clears its "isn't working" alarm.
- **Settings that did nothing** in config.toml now work and can be changed
  from chat: "Helpers one agent may run at once", "Start tasks queued for
  agents", and "Tidy away unused skills" with its three numbers.
- **Lowering "Keep activity history"** always asks first, because it deletes
  the record of what Iron Jarvis did.
- **A webhook** for an event Iron Jarvis never sends is refused when you save
  it, with the closest real event named.
- **Links** in the app open in your browser only when they are web or email
  addresses.
- Asking chat to "make the app start with Windows" no longer offers to start
  a coding session.

## Build › Code's mode menu shows names only too (v1.432.0)

The mode menu in Build › Code (the shield chip under the message box, and
the one on a new session) now lists the modes by name only, the same way as
the chat's approvals menu. Rest the pointer on a mode for half a second (or
hold a finger on it on a touch screen) and a small box beside the menu says
what that mode does.

A mode the coding tool does not offer here (for example Auto edits and Plan
for Codex on Windows) is now a greyed name below the others instead of a
block of sentences at the top. Resting on it says why it is not offered;
pressing it does nothing.

Full access still takes two presses. After the first press, "Press again to
let it do anything without asking" stays printed under its name. Holding a
finger on Full access to read what it does never counts as a press.

## The background glow costs less while the app sits idle (v1.410.1)

The soft glow that drifts behind every page now moves a step about twice a
second instead of 60 times a second. It follows the same path and looks the
same (it only moves about a pixel a second), but with the app sitting idle it
now costs about 2–4% of one CPU core and almost no graphics work, down from
11–15% and 1.3–1.8% GPU (measured on this PC in a 1440 × 900 window, on the
chat and Settings pages). It still stops when Windows is set to show less
motion and while an animated background is showing.

## "Iron Jarvis isn't responding" after an update, fixed (v1.410.2)

After the v1.409.x updates, Iron Jarvis could stop answering a few seconds
after every start, show **Iron Jarvis isn't responding**, restart itself and
stop again. The freeze record Iron Jarvis keeps (`logs/daemon-freeze.txt`)
named the cause every time. When Iron Jarvis starts, it reopens your Build
terminals and checks that **reading text from pictures on this PC** (Windows'
built-in text recognition) works. Windows sometimes takes a long time to start
the Store version of PowerShell for a terminal. While it did, the
text-recognition check waited inside Windows **and held up all of Iron
Jarvis** while it waited.

Text recognition now runs in a small separate helper program that Iron Jarvis
starts for each job and stops if it does not answer in time. If Windows stalls
it, only that one reading is lost: a scanned page then goes to a vision model
(or says it could not be read), exactly as it does on a PC without Windows text
recognition, and the next attempt tries again. Iron Jarvis itself keeps
answering. Reading a scan works as before; the first page of a job takes a
moment longer while the helper starts.

## An animated background behind the app (v1.410.0)

Your own themes can have a moving background. In **Settings → Appearance**,
press **Make your own** (or the pencil beside one of your themes) and pick
one under **Animated background**:

- **Aurora** — soft bands of colour drifting slowly.
- **Drift** — a quiet field of drifting points.
- **Waves** — thin lines rolling slowly across.
- **Grid** — a faint grid with beams of light moving along it.
- **Reactor** — a calm glow with slowly turning rings.

Each one draws only in your theme's colours (the main colour, the second
colour and the page colour), in both the dark and the light version.

**Nothing extra is installed.** The backgrounds are not part of Iron Jarvis's
installer. The one you pick is downloaded then — a single small file (7–10 KB)
from Iron Jarvis's own release on GitHub, the exact release you are running —
and checked against the fingerprint this version carries. A file that does
not match is refused ("did not match the one this version of Iron Jarvis
expects") and nothing is saved. It is kept on this PC until you delete it;
picking it again later downloads nothing. **Backgrounds on this PC** under
your themes lists what is kept and its size, and **Delete** frees the space
(a theme that used it simply shows its plain colours). After an update, a
background that did not change is kept as it is; one that did is downloaded
again the next time it is shown. The files live beside Iron Jarvis's data
folder, not in it, so backups do not carry them.

**Sealed off.** The background runs in a sealed frame behind the app. It
cannot read anything in Iron Jarvis (your sign-in, your chats, the page), it
cannot reach the internet or Iron Jarvis itself, it takes no clicks or
keyboard focus, and screen readers skip it.

**Every word stays readable.** A layer of your page colour lies over the
background, and the theme maker measures text over that layer and the
background, in both versions; the line under the preview then reads **Easy to
read in both versions, over the background too**. To keep that true, a theme
with a background may draw its hint text a little brighter (dark version) or
deeper (light version) than the same theme without one.

**Light on your PC.** Choose **Animated** or **Still**. It holds still on its
own on battery power and when Windows is set to show less motion (Settings ›
Accessibility › Visual effects › Animation effects), and it stops completely
while the window is hidden or minimised. A popped-out window shows the same
background. Measured on this PC (a 1440 × 900 window, idle, the browser's own
processes): the plain theme 15–24% of one CPU core and 2.0% GPU before v1.410.1 (its own slowly
drifting glow is what costs that; it is switched off while a background
shows); Reactor 10% and 0.7%, Grid 13% and 0.7%, Waves 14% and 0.8%, Drift
18% and 1.2%, Aurora 22% and 1.3%; any background on **Still** 2% and 0.1%.
Each figure is 20 seconds of the browser's own processes, measured between
two plain-theme readings.

## Memory profiles: set up a memory server's tools, and check it (v1.408.0)

Brain profiles are now called **Memory profiles** (Settings › Memory & you ›
Memory profile); everything they do is unchanged. Four things got more
dependable:

- **A memory server does only what you confirmed.** A memory server's tools
  have names of its own, and guessing from the names went wrong on a real
  server: "save a note" picked a tool that needed a type and tags it was never
  given, and "add to a note" left out the section heading the server requires.
  Now each memory server has **Set up tools** on its row (Memory ›
  Long-term): for each job — find notes, read one, save a new one, add to one,
  archive, delete, rename, move — Jarvis suggests a tool and where each of its
  settings comes from (the note's title, its text, the search words…), and you
  can give a setting a fixed value (say, type "concept" and tags "inbox").
  Until you press **Use these**, Jarvis can search and read it but never
  saves, adds to or changes a note there, and it says so instead of trying.
- **A refusal is not an outage.** When it answers "no" (its own
  rules turned the note down), the reply says the base refused it and why. Only
  a base that could not be reached keeps the note as an unsaved change for
  Retry.
- **No double notes.** If a save reached the base but the answer was lost on
  the way back, Jarvis checks before it saves again — in the same turn and when
  you press Retry — and finds the note already there.
- **Removing one from Long-term takes effect at once**, not after a restart, and the
  Long-term list shows each base's real kind.
- **Notion databases with their own title column.** A Notion database whose
  title column is not called "Name" (it may be "Title", "Page" or a word in
  your language) works: Jarvis reads the database's columns and uses the right
  one.

**Check** on a Memory profile runs a read-only check against the real base —
can it be reached, does a search answer, can the operating guide be read, and
can it save — and **Try a test save** (two presses) saves one note called
"Iron Jarvis test note" through the same path a chat uses and reads it back,
so you can see it work on your own Notion, vault or server. The test note is
left for you to delete.

## Your durable brain: Brain Profiles (v1.406.0)

If you keep your long-term memory somewhere specific — Notion with an
operating guide, an Obsidian vault, Iron Jarvis's own memory, a folder in a
repository — you can now tell Iron Jarvis that this is your **durable brain**,
instead of pasting a long "always check my Notion first…" prompt into every
chat. A **Brain Profile** says where it lives and how Jarvis treats it.

Set one up in **Settings › Memory & you › Brain profile**:

- **Primary** — any place you have added under Memory › Long-term
  (Iron Jarvis memory is always there).
- **Search it before answering** (on by default). Jarvis searches the brain
  with your message before it answers, and can search again or open a whole
  note while it works.
- **Save new knowledge to** — the brain, **Ask me each time** (every save
  shows a card first), or **Nowhere** (the brain is read-only).
- **Update an existing note instead of adding a duplicate** (on by default).
  A save looks for a note with the same title and adds to it.
- **When memory and the conversation disagree** — ask you, go with the
  newest thing you said, or go with the brain.
- **Operating guide** — the title or link of a note in that base. It is read
  at the start of every chat and agent run and **followed as instructions**.
  Everything else that comes from memory is treated as information, never as
  instructions (it is checked for planted commands and fenced as data).
- **Skills to load at the start** — skills Jarvis follows whenever the
  profile applies.
- **Send knowledge about code to the repository** (on by default). What is
  true about a code base goes into that repository's own files (AGENTS.md,
  CLAUDE.md, docs/), not into your personal brain.

Choose the profile for the whole app in the same section, and override it
per project on the project's page (**Same as the app**, **None in this
project**, or another profile) — a client project can use the client's
brain while everything else uses yours. A chat in a project shows which
brain applies in its project panel.

What you can rely on:

- **A save is reported only when it happened.** After every save Jarvis
  reads the note back; the reply's receipt says **Saved to <base>: <title>**
  (or "not confirmed" when it cannot be read back).
- **Nothing is lost when the brain is down.** If it cannot be reached
  (an unplugged drive, Notion offline), the note is kept as an **unsaved
  change**: the receipt says **Not saved yet** in amber, and the Memory page
  lists it with **Retry** and **Discard**.
- **Structural changes always ask.** Archiving, deleting, renaming or moving
  a note shows a card with **Allow once** every time, whatever your approval
  setting. In a folder a delete moves the note to the folder's `.trash`.
- **Local only is respected.** In a Local only chat a brain outside this PC
  is neither searched nor written.
- **Runs from a message on your phone** (low trust) can search the brain but
  never write to it.

The same rules reach both chat lanes, a Build terminal's chat and every agent
run, through four tools (brain search, read, save, change) that always go to
the profile's primary — so an agent never needs to know whether your brain is
Notion or a folder.

## Build: the Code view (v1.409.0)

Build's header now has a switch in its middle: **Code | Terminal**. It is the
same switch as the chat page's Chat | Code, in the same place on the page.

- **Terminal** is Build as you know it: your live terminals, the folder
  tree, Shell and New terminal. It is what Build opens on.
- **Code** runs the real **Claude Code**, **Codex** or **Pi** in a window instead of
  a terminal. The coding tool does everything itself, exactly as it would in
  a terminal: its own tools, its own settings, your CLAUDE.md / AGENTS.md,
  its MCP servers, its slash commands and its permission rules. Iron Jarvis
  starts it, draws what it does, and passes on your messages and your
  answers. (This is different from **Chat › Code**, where Iron Jarvis does
  the work and the model only thinks.)
- **The list** on the left holds your sessions, grouped by folder. Each row
  says which tool it runs (Claude Code or Codex) and which account it runs
  on. Under each folder, **Show the tool's saved conversations** lists the
  conversations Claude Code or Codex saved there by themselves ("saved by
  Claude Code"); the folder of the session you have open shows them without
  a press. Opening one carries that conversation on here. **Archived
  sessions** at the bottom lists what you archived, and **Bring back** (in a
  row's ⋯) returns one to the list.
- **New session** (and the Code view with nothing open) is the message box
  itself, ready to type — see "Start a session by typing" below.
- **The conversation** reads like a chat: your messages, the tool's replies,
  each step it took (open a step to see what went in and what came out), an
  edit's changes with Open in VS Code / Cursor, its plan as a checklist, and
  how many tokens each reply used.
- **When the tool asks**, its question takes the message box's place: Allow
  once, Allow for this session (only when the tool itself offers one), or
  Deny; a question with choices lists them A, B, C and has a box for
  something else. Esc denies. Enter allows an ordinary request, but never
  while the session runs in Full access and never a request the tool asks
  every time: those take a click. Your draft is kept underneath.
- **The chips under the box**: the tool and its model, the effort, the mode
  and the account. The modes are the tool's own (Ask first, Auto edits, Plan
  for Claude Code, and Full access). Codex on Windows offers only Ask first
  and Full access, and the mode menu says why (v1.420.0, read from Codex's
  own list of settings): on Windows Codex can run commands only with its own
  file protection switched off, and then it asks either before every file
  change and every command, or before none of them — it has no setting where
  edits run on their own while commands still ask. Its own plan mode is
  marked experimental, so Plan is not offered for Codex yet. **Full
  access** lets the tool do anything
  without asking, so it takes two presses. A change the tool can only pick
  up when it starts again (the effort, for example) says it applies from
  your next message. `/` lists the tool's own commands; `@` lists the
  folder's files. **Stop** interrupts the tool.
- **When something goes wrong** the reply says what and what to do. A tool
  that is signed out offers **Sign in**; an account that hit its limit offers
  **Continue on another account**, which moves the session (and a copy of
  the conversation) to your next account for that tool, or says why it
  cannot. A long session shows its newest steps, with one quiet line saying
  how many older ones were left out.
- **A reply keeps going when you leave.** Closing the page or switching to
  Terminal does not stop it; opening the session again picks the reply up
  where it is. If it is waiting for your OK while nobody is looking, the bell
  says "Claude Code is waiting for your OK" and opens the session.
- **Open in terminal** (the ⋯ beside a session) opens the same conversation
  in a Build terminal (`claude --resume` / `codex resume`), and Build shows
  that terminal. The other way works too: a terminal where Claude Code or
  Codex has finished offers **Open in Code view**, which carries that
  conversation on here. Both tools keep their own saved sessions, so
  `claude --resume` and `codex resume` keep working wherever you started.
- **The address** is `/terminals?view=code`, with `&code=<id>` for one
  session. Build remembers the view you last chose.
- **Pi** (v1.411.0) is the third coding tool. The Code view starts your own
  Pi (the `pi` you run in a terminal) with everything it has there: its
  tools, your extensions, skills and prompt templates, AGENTS.md and
  CLAUDE.md, and its settings. What is different about Pi:
  - **Pi does not ask before it runs a tool.** That is Pi's own rule, so a Pi
    session has one mode, **Pi's own rules**; the mode menu lists the other
    four and says why each is not offered. Only one of your Pi extensions can
    ask, and its question comes here as a card: a yes-or-no question is an
    Allow / Deny card (shown with the step it is about, such as the edit and
    its changes, when one step is running; Enter never allows it), a choice
    lists its options and takes one of them only, and a question that wants
    words has a box. A card closes by itself when Pi stops waiting for it. A message an extension shows (such
    as "Plan mode enabled") is a quiet line in the conversation.
  - **Plan mode** comes from Pi's plan extension, if you have it: type
    `/plan` in the box (it is in the `/` list with your other Pi commands,
    prompt templates and skills). It is not a mode chip, because the
    extension turns itself off when you choose to run the plan.
  - **Pi signs in by itself**, per model provider, so a Pi session has no
    account to choose: the chip says "Pi's own sign-in". If the model's
    provider is not signed in, the reply says so: open Pi in a terminal and
    type `/login`, or choose another model (typing `/login` in the Code view's
    box says the same, because Pi's sign-in screen needs a terminal). Continue
    on another account is not offered for Pi.
  - **The model** list (v1.429.0) is the one Pi's own `/model` shows:
    every model of every provider you signed Pi in to, grouped by provider,
    with each model named as Pi names it. It is there from the first look,
    even before you ever started a Pi session here: Iron Jarvis asks Pi for
    it once in the background (a quick, quiet Pi run that calls no model,
    saves no session, makes no network call and starts none of your MCP
    servers), and asks again when the list is older than ten minutes or when
    Pi's own model, sign-in or settings files changed (after a `/login`, say).
    While it is being read the composer says "Reading Pi's model list…".
    "Pi's default" names the model Pi starts on by itself. If Pi has no model
    it can use, the composer says so: open Pi in a terminal and type `/login`,
    or add a model to Pi's `models.json`. A list you narrowed in Pi with
    `enabledModels` is not followed here: Pi does not say which models those
    are, so the menu lists them all.
  - **The effort** is Pi's thinking level, and the menu offers only the
    levels the chosen model takes, by Pi's own rule: a model without thinking
    shows no effort menu, and many models stop at "high" or have no "off".
    Model and effort change at once, even while Pi runs. Once a conversation
    has messages, Pi reopens it on the model and thinking level it last used,
    so "Pi's default" is no longer offered for it. The usage line's cost is
    Pi's own figure from its price table and reads "the cost Pi reported":
    on a pay-per-use key it is what you spent, on a subscription it is not,
    and Pi does not say which.
  - **Pi's own `/` commands** (v1.429.0). The `/` menu lists Pi's own
    commands first, then your extensions' commands, your prompt templates and
    your skills (`/skill:name`), each marked with what it is. These do what
    they do in Pi's terminal: `/model provider/model` and `/thinking level`
    change the session's model and thinking level (as the menus do),
    `/compact` (with optional instructions) summarizes the conversation to
    make room, `/session` shows its messages, tokens and cost, `/name`
    names it, `/export` saves it as a web page in the session's folder, and
    `/reload` makes Pi read your extensions, skills and templates again
    before your next message. Before v1.429.0 these reached the model as
    plain words. A few of Pi's commands are its own screens or move it to
    another conversation, so they need Pi's terminal (**Open in terminal**):
    `/settings`, `/scoped-models`, `/hotkeys`, `/changelog`, `/trust`,
    `/bug`, `/login`, `/logout`, `/tree`, `/fork`, `/clone`, `/import` and
    `/share`; `/new` is **New session**, `/resume` is your session list,
    `/copy` is the copy button under a reply, and `/quit` is not needed.
    Typing one of these says why in one line, and the `/` menu shows that
    line as soon as you type its name. If an extension of yours has a command
    with the same name, Pi runs yours. A note sent while Pi works is plain
    words, so one of Pi's commands waits for its own message.
  - **A folder's own Pi extensions** (v1.431.0). A project folder can carry
    its own Pi extensions, skills, prompt templates, themes, settings, MCP
    servers or system prompt (in its `.pi` folder, or skills in an
    `.agents/skills` folder in it or above it). Pi loads those only for a
    folder you trust, and its terminal asks you the first time. In the Code
    view Pi cannot ask, so it skips them without a word. Iron Jarvis now
    notices this and says it in one quiet line above the box ("This folder
    has its own Pi extensions…"), with one press: **Load them for this
    session**. Nothing of the folder's loads before you press it. The press
    is for that session only (another session in the same folder asks
    again), and nothing is saved in Pi's own trust list; if Pi is running it
    starts again on the same conversation before your next message. **Stop
    loading them** goes back. A folder you already trust in Pi, or told Pi
    not to trust (or a Pi set to always or never trust folders), says
    nothing here: that is your own choice. To make a choice that lasts, open
    Pi in a terminal and type `/trust`. If one of your own Pi extensions
    decides trust itself, Pi may load a folder's extensions anyway; Iron
    Jarvis cannot see that. A folder's extensions run as programs on this PC,
    so load them only for a folder you trust.
  - **When an extension starts work by itself**, Pi works on it outside any
    message: the session stays busy until it finishes, and a message sent
    meanwhile is answered "Pi is still working on something an extension
    started".
  - **Saved sessions**: Pi saves every session in its own sessions folder,
    so a session started here is listed by Pi itself (including a folder
    Pi's `sessionDir` setting names, a project's own one too), and **Open in
    terminal** types `pi --session <id>` to carry it on there. While Pi has
    that conversation open in a terminal, the Code view waits: quit Pi there
    (`/quit`) first. A terminal
    where Pi has finished offers **Open in Code view**.
- **OpenCode** (v1.413.0) is the fourth coding tool: see "OpenCode in Build's Code view" below.

## Start a session by typing (v1.412.0)

A new coding session no longer starts with a form. In **Build › Code** (with
no session open, or after **New session**) and in **Chat › Code**, the
message box is there at once: type what you want and press Enter. A quiet row
of choices sits under the box:

- **Folder** — "No folder" until you choose one. Press **Choose…** to pick a
  folder: in the desktop app this opens Windows' own folder window, where you
  can go to any drive and any folder; in a browser it opens Iron Jarvis's own
  folder list. Iron Jarvis checks the folder when the session is made, and a
  folder it cannot use is named under the chip, with the reason, and nothing
  starts. With no folder, the session gets a folder of its own in your chat
  files folder (Documents\Iron Jarvis), named by the date and your first
  words, made with your first message — and taken back if that message never
  starts (or the tool could not start at all). Archiving the session keeps
  its folder, so unarchiving carries on where it was; deleting it removes the
  folder only when it is still empty. If you delete that folder yourself, it
  is made again at the same place with your next message. In Chat › Code, a
  folder chosen before the first message moves the new session into that
  folder (same model, account, effort, approvals, Local only, limit and team;
  what you typed comes along); while it moves, the box says "Moving to …" and
  sends nothing, and a file still uploading has to finish first. A session
  that already has a message is never moved or deleted this way. A New
  session you leave without typing anything is cleaned up, so empty "New
  session" rows do not pile up; a repository asks whether to work on a new
  branch, as before.
- **Tool** (Build › Code) — Claude Code, Codex or Pi: the one you used last,
  else the first one installed. One that cannot start is greyed, with the
  reason.
- **Account** — shown only when the tool has more than one login (the
  accounts Iron-Proxy holds for Claude Code or Codex). Choosing such a tool
  asks **which account** straight away; the one you used last for it is the
  default. With none remembered, the box asks before anything starts, even
  for the tool it opens on; if no account can start a session (all signed
  out or at their limit), Send stays off and the chip says why. A tool with one login, and Pi (it signs in by itself), never asks.
  In Chat › Code the model menu lists each account under Claude Code and
  Codex, so you choose the account as you choose the model.
- **Mode**, **model** and **effort** — effort only when the chosen model
  offers levels (a model that takes none hides it, and an effort the new model
  does not offer goes back to the default). A choice Iron Jarvis refuses is
  named under that choice.

Sending makes the session with those choices and sends your message in one
press.

### Pictures, the mode at any time, and notes while it works

- **Paste a picture** into the message box (a screenshot, a copied image),
  drop one on it, or press the paperclip: it shows as a thumbnail with a ×
  to take it off, and goes with your next message — in an open session and
  in the box that starts a new one. A picture alone is a message. The tool
  sees the picture itself: Claude Code and Pi as an image, Codex as a local
  picture file. A picture the tool cannot take as a picture (larger than it
  takes, or a Pi model that reads no pictures) goes as the file's path, and
  the conversation says so. Other files go by their path. Send waits for a
  picture that is still going up. A file on a network share is not sent
  (attach files from this PC's own drives), unless the session's own folder
  is on that share. **Chat › Code** works the same way (it already
  did).
- **The mode can change at any time**, also while the tool is working and
  while it waits on a card: the mode chip sits beside the card then. Claude
  Code changes at once; Codex keeps its mode for the message it is working on
  and uses the new one from your next message (it says so). Each mode in the
  menu says what it does. Claude Code now also offers its own **Auto**: its
  own safety check allows or refuses each action without asking you, and a
  few risky ones still ask. Auto and **Full access** are only ever switched on
  from the mode chip — never as a side effect of "Allow for this session" on a
  card — and Full access still takes two presses. A mode
  changed while a card waits leaves that card for you to answer (it was asked
  under the old mode).
- **Steer it while it works.** Type while the tool is working and press
  Enter (or the arrow beside Stop): your words go to the RUNNING reply as a
  note, not as a new message. Each tool takes it its own way — Claude Code
  reads it at its next step (or right after the reply, still as part of it),
  Codex at its next step, Pi after its current step. The note shows in the
  conversation where you sent it, and says when the tool read it. A note the
  tool never read before it stopped (Stop, or the reply ending first) comes
  back into your message box, with a line saying so — it is never lost. A
  note the tool read just as the reply ended is shown as read and does not
  come back (so it is not sent twice); one that arrives after the reply ended
  goes as an ordinary message instead, or stays in your box with the reason.

## OpenCode in Build's Code view (v1.413.0)

OpenCode is the fourth coding tool in Build's **Code** view, beside Claude
Code, Codex and Pi. The Code view starts your own OpenCode (the `opencode`
you run in a terminal) with everything it has there: its tools, its agents,
your plugins and MCP servers, AGENTS.md, and your OpenCode config. Iron
Jarvis starts it, shows what it does, and turns every permission question it
asks into a card you answer.

- **Starting one**: choose **OpenCode** on the Tool chip under the box, pick
  a folder (or none), and type. A session starts in **Ask first**.
- **Modes** are OpenCode's own permission rules, set for that one OpenCode
  process; your OpenCode config files are read, never changed:
  - **Ask first**: OpenCode asks before it changes a file, runs a command or
    uses the web. Reading files runs without asking, except a `.env` file
    (where keys and passwords live): OpenCode asks before it reads one, as it
    does on its own.
  - **Auto edits**: file changes run without asking; commands and the web
    still ask.
  - **Plan**: OpenCode's own plan agent. It looks around and plans, and
    changes no file: in Plan, OpenCode is not allowed to edit at all (not
    even a plan file), and it still asks before it runs a command.
  - **Full access**: everything runs without asking. Press it twice to
    confirm, as for the other tools.
  - **Auto** is not offered: OpenCode has no mode that decides each step for
    you.
  - Changing the mode applies from your next message (OpenCode is restarted
    on the same conversation, which takes a moment); a reply already running
    finishes in the mode it started in.
- **Your own permission rules come first.** If your OpenCode config (or a
  project's own `opencode.json`) has a rule for a tool, that rule stays as you
  wrote it, whatever mode you choose, and the session says which tools keep
  your rules. A mode never changes a rule of yours, in either direction
  (v1.420.0): a `deny` stays a `deny` (that includes the older `"tools":
  {"bash": false}` switches and an admin's settings in
  `C:\ProgramData\opencode`), and an `allow` stays an `allow` — before
  v1.420.0, Ask first turned a `"bash": "allow"` of yours into a card for
  every command. A rule that names tools by a pattern (`"web*"`) counts for
  every tool it matches, and a `"*"` rule of yours decides for every tool
  (only Plan still refuses edits). When a rule of yours differs from the
  mode, the mode names only OpenCode's built-in tools, so tools from the MCP
  servers and plugins in your OpenCode config run without asking unless a
  rule of yours says otherwise; the session says so.
- **Helpers**: in Ask first and Auto edits, OpenCode starts one of its
  helpers (its `task` tool) without a card, as Iron Jarvis's own coding
  sessions do; every step the helper takes still asks under the same mode.
  In Plan, OpenCode's own rule for helpers applies.
- **Cards**: a card shows what OpenCode wants to do (the command, or the file
  and its changes). **Allow once** allows that one step; **Allow for this
  session** is OpenCode's own "Always allow"; **Deny** says no, and OpenCode
  stops that step. OpenCode keeps an "Always allow" only while its process
  runs, and changing the mode starts a new one, so Iron Jarvis remembers your
  answer for the conversation and gives it again itself when the new
  OpenCode asks the same thing: every file change (and every web fetch,
  search, helper) when you allowed one for the session, or the exact same
  command. A similar but different command asks again (OpenCode's own rule
  covers a whole command family, which it does not tell Iron Jarvis). These
  answers are forgotten when Iron Jarvis restarts.
- **Notes while it works**: type while OpenCode is working and press Enter.
  OpenCode reads the note at its next step, and the reply goes on with it. If
  you press Stop before OpenCode reached a note, the note stays in OpenCode's
  conversation (it sees it with your next message), so it is not put back in
  your box.
- **Stop** stops the step it is on; the conversation is kept.
- **Pictures**: paste or attach a picture. It goes to OpenCode as a picture;
  a model that cannot read pictures says so.
- **Model and effort**: the model list is the one OpenCode itself lists
  (shown once an OpenCode session has started on this PC). The effort is the
  model's own levels, when it has any. Both change straight away. Once a
  conversation has messages, OpenCode reopens it on the model and effort it
  last used, so "OpenCode's default" is no longer offered for it.
- **Sign-in is OpenCode's own**, per provider: an OpenCode session has no
  account to choose, and the chip says "OpenCode's own sign-in". To sign in
  or change it, run `opencode auth login` in a Build terminal. Continue on
  another account is not offered for OpenCode.
- **Cost**: the usage line shows what OpenCode reported ("the cost OpenCode
  reported"). It is never called list price; OpenCode does not say whether a
  provider is a subscription or pay-per-use.
- **Saved sessions**: OpenCode keeps every session in its own store, so a
  session started here can be opened in a terminal (**Open in terminal**
  types `opencode --session <id>`), and a folder's saved OpenCode sessions
  are listed with the others. While OpenCode has that conversation open in a
  terminal, the Code view waits: quit OpenCode there (`/exit`) first. A
  terminal where OpenCode has finished offers **Open in Code view**.
- **Processes**: one OpenCode per open session, like the other tools. It is
  stopped after 15 minutes idle (the next message reopens the same
  conversation), when you archive or delete the session, and when Iron Jarvis
  quits; everything it started goes with it. OpenCode talks to Iron Jarvis
  over its own input and output, not over a network port. OpenCode also runs
  a small server of its own inside that process; Iron Jarvis starts it on
  this PC only (127.0.0.1), whatever the `server` settings in your OpenCode
  config say.

## Faster steps in a coding session on your Claude plan (v1.427.0)

A coding session in Chat › Code works in steps: the model asks for some
tools, Iron Jarvis runs them, the model reads the results and decides what
to do next. On your Claude plan each step used to start Claude Code again
and send it the whole conversation, which cost about half a second a step
and made Claude re-read the conversation instead of remembering it.

Now one Claude Code stays running for the whole reply:

- **Each step after the first starts faster.** On this PC a four-step reply
  went from about 6 seconds to about 3½ (a step after the first: about half a
  second instead of 1¼ to 2 seconds). On 12 harder coding tasks, run three
  times each on Sonnet, a task took about 46 seconds on average instead of 51
  (a typical one 34 instead of 42), and every task still passed.
- **Claude remembers the conversation between steps.** Each step only sends
  what is new: on those tasks Claude read about a third less of the
  conversation fresh, so less of your plan's usage goes to re-sending what it
  has already read.
- **The cost shown is the model that answered.** Each step is priced by the
  model Claude Code actually used (for "Sonnet", Sonnet 5), at list price,
  as before.
- **Iron Jarvis still runs every tool.** Claude Code is never allowed to run
  one itself; Iron Jarvis runs it, with your approval settings, and hands the
  result back. A step that waits on your approval keeps its Claude Code (we
  checked a wait of over five minutes); one nobody continues within 30
  minutes is ended, and the next step simply starts Claude Code again.
- **Nothing breaks when it can't be used.** If the conversation changed in a
  way the running Claude Code doesn't know about (an earlier message edited,
  the model or account changed, the tools changed), Iron Jarvis ends it and
  starts a fresh one exactly as before. Stop still ends Claude Code at once.

This applies to Chat › Code coding sessions on Claude Code. Plain chat,
agents and schedules work as before. If you ever need the old behaviour, set
the environment variable `IRONJARVIS_CLAUDE_LIVE=off` before starting Iron
Jarvis.

## See what a coding session is told, and folders that keep their own defaults (v1.426.0)

**The instructions, visible.** A coding session's Session tab now says which instructions the newest reply ran with: whose tool names the model was shown, for which model family, and what they were built from. If your folder's own instruction files (`.ironjarvis/harness/`) were NOT used, it says so in the warning tone, with the reason. "Show the instructions" shows the whole text exactly as a reply puts it together. It is read fresh each time, never stored, and secrets are masked. A folder file that looks like an instruction to the AI shows as a "[BLOCKED…]" placeholder there, as the model sees it.

**A coding request is never retyped.** When a plain chat reply ends with the "open a coding session" door (for example after "build me a todo app" or "fix the failing test in my repo"), pressing it opens Chat › Code with your request already in the box and the chat's project folder chosen. Nothing is made or sent until you press Send. A chat kept Local only opens its coding session Local only too.

**One writer per conversation.** After you type `/clear` in a Build terminal running Claude Code, Open in Code view picks up the new conversation. A conversation your own Claude app wrote in the same folder is never taken for the terminal's. A terminal still running a conversation holds it, so the Code view never opens a second writer on it. Pressing Open in terminal twice never opens two terminals on one conversation.

**Your coding tool's own setup.** Build › Code's Session tab lists the coding tool's own MCP servers as Claude Code or Codex reported them, each with its state: Connected, Starting…, Did not start (with the tool's own error), Needs you to sign in, or Turned off. A server waiting for a sign-in shows the tool's own way: for Claude Code, press Open in terminal, type `/mcp`, choose the server and pick Authenticate; for Codex, press Open in terminal, leave Codex with `/exit`, then run `codex mcp login <name>`. The tool uses the sign-in from its next start. Pi, OpenCode and Antigravity do not report their servers here, and the tab says so (since v1.441.0 Grok does, and Pi's line says to type `/mcp`). Codex's "/" menu lists its own compact and its skills (typed as `$name`, Codex's own way); sending exactly `/compact` makes Codex summarize the conversation, and the Context drop-down offers the same.

**Folder defaults.** A folder can keep defaults for new coding sessions started in it: in Chat › Code the model, reasoning level and approval level; in Build › Code the tool, model, effort and mode. Set them with the sliders button on the folder's heading, or "Use for new sessions in this folder" under the start box. A new session in that folder starts with them unless you change a choice yourself. The words under the start box say which defaults are in use, and say so when one cannot be used (a model that cannot answer right now, a tool that cannot start, a model that leaves this PC while Local only is on). "Don't ask" and Full access are never a folder's default: you choose those per session. A Clear or a save in the folder's panel takes effect in the open start box at once, and renaming the folder keeps its defaults.

## Working habits for your model team, and a truer coding session (v1.425.0)

**Working habits from your reviewers' findings.** When your team's reviewers have confirmed at least 4 of one member's findings fixed, across at least 2 coding sessions, Iron Jarvis suggests up to 3 working habits for that member — one line each, every one citing the findings it comes from. It asks the reviewer's own model, once a day at most, while the session is idle. It never runs near an account's limit, and never with a Local only finding on a model that leaves this network. The demo model suggests nothing. A suggestion waits for you on the member's own card (Agents › Your team); Settings › Model team lists each member's accepted habits. Accept writes the habit into the member's TEAM-NOTES.md in its agent folder, and from then on only that member's team pieces see it; Decline keeps that suggestion away for 14 days. If you edited TEAM-NOTES.md yourself, Iron Jarvis never writes over it — and if you save it while Iron Jarvis is updating it, your save is kept. When two members of the same role were given the same habit, the library suggests adding it to that role's brief instead (only with "learn from Build sessions" on). This is on by default; turn off "Learn working habits from reviews" in Settings › Model team to stop it (the habits you accepted are kept, but no model is shown them).

**Does a habit help?** For each accepted habit, Settings › Model team compares the member's work before and after you accepted it: after runs from the acceptance to now; before covers the same length of time just before it, at most 30 days, and never reaches back into a time the same habit was already in force (a habit removed and accepted again is compared with the time it was not). Work is counted by reply: a member reads its habits once per reply, so a reply that started before you accepted the habit counts entirely as "before". The comparison covers how often a reviewer approved its first piece, how many confirmed findings it got per piece, and how often the finish check passed. Once both sides have at least 5 pieces, a quiet line shows the numbers ("Since this habit: first-pass approval 2 of 6 → 5 of 7"). If the work looks worse since the habit, an amber line says "This habit may be making things worse — consider removing it". Iron Jarvis never removes a habit by itself; press Remove twice. With "learn from Build sessions" off, no library suggestion of any kind is made — even one whose model call was already running when you turned it off — and the same holds for team notes.

**Copy as a fix request knows the team.** The report now also lists what the reviewers found about the team's own way of working: a finding the lead said did not apply (with the lead's reason), one the lead never answered, one called fixed that no later piece confirmed, and the same kind of finding against one member in two or more sessions. A finding confirmed fixed is never listed. Nothing is read from a reply that ran at low trust — including one you stopped after it read something flagged, and one still running (the report says so). A "Try on several models" attempt is read only when it is known to have ended at full trust; the report says how many it left out.

**A stopped reply keeps its record.** If you stop a coding reply after it ran steps but before it wrote any words, its line still shows what it did.

**What the coding model is told is now true.** When the model reads a picture (Claude's Read, Qwen's read_file, Codex's view_image), Claude and Codex see the picture itself (shrunk if large) instead of another model's description; a model that cannot be shown pictures gets a description and is told it is one. Before the picture is sent, Iron Jarvis reads its text on this PC with Windows' own text recognition (offline, about half a second); if that text reads like instructions to the AI, the session goes to low trust and the AI is told the text was withheld, but it still gets the picture. If local text recognition is off, unavailable or slow, the picture goes unchecked. A BMP or TIFF named like a PNG is converted first. Claude's and Qwen's command tools wait 2 minutes when no timeout is given; a long file read stops at a whole line and says which lines it showed; the approval sentence the model reads says exactly what runs without asking in each permission setting; descriptions never mention tools the session does not have; and the "what I did last reply" note rides the newest reply that actually used tools.

**The benchmark measures what you use.** `ironjarvis bench-harness` now runs each task as a real Chat › Code coding session by default (`--path product`): "Ask first", every card answered "Allow once", the session's own step limit unless you give `--rounds`, and the time limit pressed as your Stop. `--path lane` keeps the old bare lane. The benchmark refuses a `--root` whose state folder is your user folder's `.ironjarvis` (every Iron Jarvis on the PC reads its settings from there), and a task the time limit cut off leaves nothing behind that the app would carry on after a restart.

**Coding sessions start sooner and check their work in big projects.** In a coding session in Chat › Code, Iron Jarvis looks at the project folder before any of the reply's commands run, so it can tell what those commands changed. It now does this at the same moment it asks the model instead of before, so the first words come sooner; the first command still waits until the look is finished. In a git repository it asks git for the list of files, so projects bigger than 5,000 files are checked too (before, the check gave up). A folder that is not a repository is still read file by file, as before — and so is a folder the repository's own `.gitignore` leaves out, a whole home folder that is a repository, and a project whose git data sits on a network share.

**Build › Code's "This message" is cheaper.** The Changes tab's "This message" view now keeps its own copy of git's file list for each session, so the picture taken before each message is about a quarter faster on a 50,000-file project. The copy is rebuilt whenever an ignore rule changes, so files you later tell git to ignore drop out of the list. It is never backed up and is deleted with its session.

**A seventh coding session waits instead of failing.** When six Build › Code sessions are already busy, a new message no longer fails at once: Iron Jarvis says it is waiting and starts the moment one finishes, giving up after 15 minutes with the old message. If a place can be freed, it stops the least recently used idle session first; it never stops a session that is working, starting, or still running background work. One session's slow start no longer holds up another session's message. Pressing Stop while a message is waiting, or while the coding tool is still starting, ends it there, and the tool never receives that message.

## Every subscription, everywhere (v1.424.0)

- **Failover stays within what you chose.** When the model you picked can't
  answer, Iron Jarvis tries another of your models — but it only moves to one
  that bills per use (an API key, a cloud endpoint, or a Claude model that
  draws usage credits, shown as "pay per use" in the menu) when the model you
  picked bills per use too. If you picked Claude Code, Codex, Grok or a model
  on your own network, it moves only among your subscriptions and local
  models. When nothing like that is left, the message says so, for example:
  "Iron Jarvis didn't switch to OpenAI, which bills per use, because you
  didn't choose a pay-per-use model for this." A picture follows the same
  rule: it is never sent to a paid key you didn't pick, and the reply says
  when an image was not seen.
- **Each model says what it can do.** On the Fleet page, each model behind
  one of your model servers shows its own tools and vision. A model nobody
  has checked uses the server's check of its default model and says so
  ("not checked for this model: from the server's check of glm"); press
  Check on its row to ask that model itself.
- **Codex's own models.** Every model menu lists the models your ChatGPT plan
  offers, from Codex's own list on this PC, with their effort levels
  (including Extra); your plan's default is marked. With no OpenAI key,
  OpenAI's list is Codex's list. Iron Jarvis refuses a model in words before
  anything is sent only when that list, for the account the work really runs
  on, is recent and does not hold it; otherwise Codex's own answer decides.
- **Choose an account anywhere.** With Iron-Proxy on, you can choose the
  account for a chat (in its model menu, under Claude Code, Codex and Grok, or
  "Any free account"), for a custom agent, a schedule or a night task. The
  choice covers everything that work does: its replies, follow-up
  suggestions, Summarize, a hand-off to an agent and its later continues. An
  account only works for a model of its own vendor; if it can't be used the
  work is refused in words and never runs on another login. A schedule whose
  agent has its own account must name that account or none. The chat's reply
  line says which account it ran on. Grok accounts can carry a coding session
  like Claude Code and Codex accounts.
- **Your Claude Code setup on your other Claude accounts.** When a Build
  terminal or a Build › Code session runs Claude Code on an account other
  than the one this PC is signed in to, it starts with your own MCP servers,
  skills, commands, agents, hooks and installed plugins, shown with "my:" in
  front (for example /my:pixio-story). Iron Jarvis keeps its own copies of
  your skills, commands and agents in its folder, so deleting that folder can
  never touch your originals; copies never include a file that holds a key,
  and your server keys never go into a file — they ride in the session's
  environment. Be aware that what your MCP servers can read (a QuickBooks
  server's books, say), your skills and commands, and what your hooks print
  reach the Claude account that session is signed in to, which may belong to
  another organization. Turn it off in Settings › Connections › Several
  accounts ("Use my Claude Code setup on my other accounts"); chat can turn it
  off at once but always asks before turning it on.
- **OpenCode behind a chat never acts on its own.** When OpenCode is the model
  behind a chat or an agent run, Iron Jarvis runs it as its own agent with
  every one of OpenCode's tools off: Iron Jarvis carries out every action
  itself, with its usual cards, record and Undo. If OpenCode ever cannot find
  that agent, the run stops before anything happens instead of using your own
  OpenCode agent. A reply cut off at its output limit says so and offers
  Continue. Build › Code is unchanged: there OpenCode keeps its own tools.
- **Grok's real output limit.** Grok replies are no longer stopped at 4,096
  tokens: Iron Jarvis sends the limit your Grok settings give (your
  `max_completion_tokens`, if set). A cut Grok reply offers Continue, and a
  Grok tool call whose details cannot be read runs nothing and says so.

## Your team's reviews are kept, and a report for fixing Iron Jarvis (v1.423.0)

- **What your reviewers found is kept.** When a coding session's reviewer
  checks a change, each problem it lists ("R1. app.py:12 — what is wrong —
  what to change") is now kept and credited to the team member whose work
  changed that file. The lead answers each one on its own line — "R1 fixed"
  or "R2 does not apply: why" — and those lines stay in the reply you read.
  When the reply ends, every problem is settled: "fixed" counts as confirmed
  only when a later piece of work really changed that file (otherwise it is
  kept as "said fixed"), and a problem nobody answered stays "not answered".
- **Settings › Models › Model team** shows each member's last 30 days in one
  quiet line, for example "Reviewers' findings (30 days): 6 confirmed, 1 did
  not apply · first-pass approval 3 of 7". First-pass approval counts the
  replies where the reviewer approved that member's first piece. There is no
  line when there is nothing to say. Only problems Jarvis asked the lead to
  answer at the reply's finish check are counted; a review the lead hands out
  itself is kept but not counted.
- **Nothing is kept from what reads like an instruction.** A reviewer's report
  that reads like an instruction gives no verdict and no finding; nothing is
  kept from a low-trust reviewer or reply; a single problem whose words read
  like an instruction is left out; keys and passwords are hidden in every line
  that is kept. A finding names one file of the reply: an exact name always
  wins, and a name that two of the reply's files end with is credited to no
  one and confirmed by nothing.
- **Copy as a fix request.** Settings › Models › Model team, and a coding
  session's Session tab, have a quiet "Copy as a fix request" button. It
  copies a report of the problems Iron Jarvis's own coding sessions and model
  team ran into in the last 7 days (or in that one session): a command the
  shell could not run, a tool a worker kept calling wrong, a helper that could
  not start or failed, the team router finding no one, a reply cut off at its
  output limit, a team review that did not run. Each problem says where it
  happened, what was seen, what was expected, which part of Iron Jarvis owns
  it, and up to three examples. Paste it to the coding agent that works on
  Iron Jarvis.
  - It says how many it copied ("Copied: 15 of 60 problems …" when the report
    could not hold them all), and says when something could not be read
    instead of "Nothing to report".
  - Your own choices (a card you declined, a Stop, your own rules) and your
    own code's failures (a test that ran and failed) are left out, and so is
    anything a low-trust run read. A failed command shows only the shell's own
    error, never what else it printed, and a write or edit shows a file's size,
    never its text.
  - Secrets are masked, but the report still holds your commands, file paths
    and errors: read it before you paste it anywhere.

## Never lose work (v1.422.0)

A long job should never be lost, cut off or stuck with nobody told and no
way to carry on. This release closes the ways it could happen.

**Chat › Code (Iron Jarvis's own coding sessions)**

- **A brief outage no longer ends a long reply.** If the model's service
  fails for a moment (busy, overloaded, a server error, a timeout, a dropped
  connection), the reply waits and asks the same model on the same account
  again: after 5, 15, 30 and then 60 seconds, honouring a "try again in"
  from the service. It never moves to another provider. While it waits, the
  reply says so, for example "Claude Code is busy (overloaded). Trying again
  in 30 s, attempt 2 of 5", and you get one notice, not one per try. Sign-in
  problems, your account's own usage limit and safety refusals are never
  retried. A reply that starts on a local model server that is off still
  says so at once; one whose server goes away partway through waits for it.
- **A reply that still fails keeps its work.** Its line offers **Continue
  from where it stopped**: the reply carries on from its last finished step,
  without asking your question again or redoing finished steps. **Retry**
  still asks the question again from the start. Sending a new message ends
  the offer, and kept work expires after a week. A reply that had read
  something suspicious stays at low trust when you continue it, and when
  Iron Jarvis carries it on after a restart.
- **Stop and Esc stop a running command.** Before, Stop ended the answer but
  a command that was running carried on for up to 10 minutes. Now, after a
  few seconds, the command and everything it started is ended and its step
  says you stopped it, and no later step of that reply starts. A program the
  session started in the background on purpose keeps running, and a
  question waiting for your answer simply closes.

**Build › Code (Claude Code, Codex, Pi, OpenCode, Grok, Antigravity)**

- **A restart of Iron Jarvis no longer stops your coding tools.** Your
  coding tool now runs in a small helper process of its own, not inside Iron
  Jarvis. When Iron Jarvis restarts (the watchdog, Restart in the tray, a
  crash), that helper and its tools keep running, and when Iron Jarvis is
  back it reconnects: a session that was idle answers your next message on
  the same running tool, a reply that was being written carries on and you
  see the rest as it comes, a card that was waiting for your answer is shown
  again, and helper agents and dev servers the tool started keep running.
  Quitting Iron Jarvis, or installing an update, ends the helper and every
  tool in it (each tool saves its conversation first). If Iron Jarvis does
  not come back, the helper stops its tools on its own after 10 minutes when
  nothing is working, and always within 3 hours. If Iron Jarvis cannot pick a
  session back up, or the helper cannot be used, the reply says so and
  carries on as described next.
- **When the tool did stop, the reply carries on.** After an update, a PC
  restart or a crash, a Build › Code reply that was working carries on by
  itself once, through the coding tool's own resume, told to check what was
  already done first. You see one line: "Carried on by itself after Iron
  Jarvis restarted." This happens only for a reply cut within the last week,
  and never for a reply you stopped, one that was waiting for your answer,
  one you followed with a newer message, or while Emergency stop is on; then
  the reply shows **Continue** and says why. On the first start after you
  update to this version, a reply the update itself cut off is only offered
  (it was cut before this version could carry replies on). If a reply Iron
  Jarvis carried on by itself fails, **Retry** runs that carry-on again.
- **Account limits are waited out, or moved past.** When an account reaches
  its limit and the reset time is known, the reply waits and carries on at
  the reset ("Waiting for Work to reset at 3:45 PM — it carries on by itself
  then", with **Cancel**). If the account is in one of your account groups,
  it moves to another free account of the same kind in that group instead.
  Claude Code and Codex conversations can move; Grok only waits. It never
  moves while the tool still runs background work, and after four tries in a
  row that the account refuses it stops and waits for you. Turning off
  "Carry on by itself after an account's limit" stops any wait; Emergency
  stop stops a reply carrying on by itself.
- **Your dev server survives the idle stop.** A coding session with nothing
  to do is still closed after 15 minutes to free memory (your next message
  opens it again where it left off), but the session you are looking at now
  waits 60 minutes, and a session whose tool started a program that is
  serving, like a dev server on a port, stays open while that program runs,
  up to 8 hours with nothing new. It says so ("Keeping Claude Code open: it
  runs a program on port 3000…") and offers **Close Claude Code**, which
  stops the tool and the program now (not while the tool is still working on
  its own). Whenever Iron Jarvis does close a tool that was running programs
  (to make room, for new settings, Open in terminal, moving accounts), the
  conversation names them, and your next message reminds you to start them
  again. The update warning now names a coding session running a program, so
  installing an update never ends a dev server without saying so.

**Terminals**

- **New terminals use your installed PowerShell 7, or Windows PowerShell.**
  The Microsoft Store copy of PowerShell 7 is still in the shell menu,
  labelled as such, but is no longer the default: starting it is what froze
  Iron Jarvis when it reopened terminals. Terminals you already have keep the
  shell they had. Windows PowerShell does not have some PowerShell 7
  shortcuts (`&&`, `??`); pick PowerShell 7 from the menu, or install it
  normally, if you want them.

### Also in v1.422.2

- **Coding tools start in a new Windows PowerShell terminal.** When no
  PowerShell script policy is set on the PC (the Windows default), Windows
  PowerShell refuses every script, including the ones npm installs to start
  Claude Code, Codex, OpenCode and Pi. A new or restored Windows PowerShell
  terminal now allows local scripts for that terminal only (PowerShell 7's
  own default). A policy you, your organisation or a group policy set is
  always respected, and nothing on the PC itself changes.
- **The team's reviewer reads git more strictly.** Each git command it may
  run now takes only a fixed list of read-only options (summaries, file
  names, a number of commits, dates, authors, searches), written in full;
  anything else is refused in one sentence naming the options it takes. The
  folder's own git settings can no longer point it at a file elsewhere on the
  PC (a name map or attributes file the folder names is ignored; your own
  global ones still apply). Searching files git does not track, or blaming a
  file's working copy, goes through the reviewer's ordinary file tools.
- **What was checked in the installed form of the app.** The background
  program that keeps your Build › Code tools running across a restart was
  built and run the way the installer ships it, with the real Claude Code:
  it starts with no window; after Iron Jarvis was killed outright, the same
  Claude Code answered the next message, and a long reply that was being
  written finished and was recorded once; quitting left nothing running.
- **Not tried yet:** moving a Codex conversation between two real ChatGPT
  accounts (only one is connected on the PC this was built on; it was tried
  between two copies). If the other account refuses the conversation, the
  reply fails in Codex's own words, the first account's copy is untouched,
  and nothing repeats by itself.
- **The reviewer's git never reads through a link (v1.422.3).** If a folder
  git tracks in your project has been replaced by a link to another place
  (a junction or a symbolic link), the team reviewer's read-only git
  declines, in plain words, rather than read whatever is on the other end.
  OneDrive's own folders are not links and keep working. In a very large or
  slow repository the check may take a moment, and after 20 seconds it
  declines rather than guess.

## A team that works (v1.421.0)

- **Helpers run commands the way your coding session does.** When a coding
  session in Chat › Code hands a piece of work to a helper (a worker on your
  model team, or a built-in helper), the helper's commands now run in Git
  for Windows' bash when it is installed: the same shell the session itself
  uses. Linux-style commands (`head`, `grep`, `sed`, `;`, here-documents)
  work instead of failing in Windows' Command Prompt. Without Git for
  Windows, helpers keep the Command Prompt as before. If a helper's test
  command ran in bash, the finish check runs it again in bash.
- **Helpers are held to the session's standard.** Each helper now sees what
  the session's own work is judged by: the folder's AGENTS.md or CLAUDE.md,
  the guardrails, the lessons you kept for that folder, the folder's brief
  for its role (`.ironjarvis/harness/sub_agents/<name>.md`) and what the
  finish check will look at. When a helper runs on a model that can only
  take in a little at once (a local model loaded with a small window), Iron
  Jarvis shortens that section to fit and tells the helper what it left
  out, so the helper never loses its task. When your folder has briefs for
  helpers, the session now hands a piece that fits one to a helper and
  tells it to read that brief, instead of doing the piece itself.
- **Your team's reviewer checks the change with git.** The reviewer can now
  ask git what changed, see the history and see who wrote a line, as well as
  read the files. It cannot change anything or run your tests (running the
  tests is still the finish check's job), and it cannot run any program the
  folder names. If the folder's own git settings would make git run a
  program or read outside the folder, the reviewer is told no in one
  sentence. It lists each problem on its own line, as "R1. file:line — what
  is wrong — what to change", and it is told which team member changed which
  file. The session's lead sees every one of those lines even when the
  report is long, and hands each problem to the worker who changed that
  file. A reviewer's report that reads like an instruction is held back from
  the lead, and that reply is treated as untrusted.
- **Every piece a team member works is on its record.** Work the lead hands
  to a member by name now counts on that member's record, as well as work
  asked for by role, so the team's routing and Settings › Models › Model
  team see all of it. A review that asks for changes counts only against
  the pieces that changed a file, never against a question a worker only
  answered.
- **A step written out as text is turned into a real step in every run.**
  When a local model writes a step out as text instead of making it (for
  example GLM's `<tool_call>` markup), Iron Jarvis now tells it once to make
  the call, in a helper, a teammate or any agent run, not only in a coding
  session. The written text is never run. This happens at most twice per
  run and never twice in a row, and each one shows on the Autonomy card as
  "a step a model wrote out as text and was told to run". A step quoted as
  an example (inside a code block with more of the report after it) is left
  alone, so a reviewer quoting a test file keeps its report.
- **A local model is planned at its real size.** A local model's context
  window now comes from its own server: vLLM's `max_model_len`, or the
  window Ollama loaded it with. For a model behind a LiteLLM proxy, it is
  read from the server the proxy sends it to. Before, these were planned at
  the 32,000-token default. If Ollama loaded your model with a small window,
  conversations are trimmed to fit it: raise `OLLAMA_CONTEXT_LENGTH`, or set
  the window yourself in Settings.
- **A model too small for the job says so.** When an agent's model has a
  known window (set by you, measured, or read from its server), Iron Jarvis
  now counts the agent's tool list against it as well as its instructions
  and conversation. If the task, or the results the agent has to act on
  next, cannot fit, the run stops at once with one sentence naming the
  window ("This model's context window (4,096 tokens) is too small for this
  job…") instead of repeating itself until its step limit. A window Iron
  Jarvis only guesses never stops a run. When older steps are left out to
  fit, a note Iron Jarvis added after a reply is kept or left out together
  with that reply. A coding helper's tools alone take about 6,000 tokens,
  so a local model loaded with a 4,096-token window cannot work as a coding
  helper: give a model you use as a helper a window of at least 16,384
  tokens.
- **The Autonomy card counts helpers' approvals in the right place.** A
  helper's approval card, and a card from "Try on several models", now
  count under the coding session they belong to instead of "unattributed".
- **A run whose memory profile cannot be read carries on.** Before, that
  read failing could fail the whole agent run.

## The Code views stay in the window (v1.420.2)

- **The window's own scrollbar no longer scrolls a coding session away.**
  In Chat › Code, a long conversation could make the whole page about ten
  screens tall: the scrollbar at the window's far right then scrolled the
  session up and out of sight, leaving an empty page. The cause was a label
  meant only for screen readers (the step words of an open plan, for
  example) that the conversation did not keep inside itself. The
  conversation, the empty view and the Tonight and Last night lists now keep
  what they hold, and so does every row of a plan, on every screen that
  shows one. On a wide window the page is exactly as tall as the window and
  only the conversation and the side panes scroll; on a narrow one the
  conversation and the panes stack and the page scrolls, as before. Build ›
  Code was not affected, and got the same guard.
- **A command's row shows its own output.** When a command ran without the
  sandbox (Docker not running), each command row, and the tools table on a
  run's own page, used to start with "[native runtime — limits advisory, see
  first shell result]", a note meant for the model. They now show the
  command's own first line, and opening the row says it once, plainly: "Ran
  without the sandbox, so its limits on files, the rest of this PC and the
  internet weren't enforced." The model still gets the note as before.

## Fewer things end in silence (v1.420.0)

- **Another computer that takes a task "for later" is followed to the
  end.** When you hand work to a remote agent (another Iron Jarvis, a Hermes
  on another PC) and it answers "I have it, I'll report back", the agent
  card on the mission screen now says "Accepted, will report back" instead of
  "Done" over an empty answer. When the remote posts its report back (Let it
  message back must be on, on its card), the card turns Done with the
  report's own words. With Let it message back off, the card says the answer
  cannot come back here.
- **A helper that failed says why in plain words.** When a helper agent or
  a teammate stops on an error, its parent, the mission's activity log and a
  coding session's helper row now say the same plain sentence a failed run
  says ("Codex is busy. Run it again shortly, or choose another model in
  Settings."), never "crashed: ProviderError: …". The technical text is kept
  in the details.
- **An account sentence is the headline.** When an Iron-Proxy account is at
  its limit or needs to sign in, the chat's failure line now leads with that
  exact sentence (which account, until when) and says where your accounts
  are, instead of hiding it behind "Press Retry".
- **The phone says it plainly too.** A problem the phone hears ("I hit a
  problem: …") is the same plain sentence, never a program error name.
- **Grok has a Sign in button.** Like Claude Code and Codex, a signed-out
  Grok CLI shows a Sign in press in Settings › Connections. It opens a Build
  terminal with Grok started on this PC's own login; Grok opens its sign-in
  there (or type `/login`). Grok's messages no longer tell you to run a
  command in a terminal.
- **The status light knows when it is waiting for you.** Iron Jarvis now
  tells the dashboard how many running replies are parked on your answer (a
  card or a question), so the status light can say "Waiting for you" instead
  of "Working".
- **The tray tells you when more things end.** With every window closed to
  the tray, Iron Jarvis now also raises a Windows notification when a
  mission ends ("Your objective is done", or why it is not), when an agent
  you started from chat ends, and when a chat answer that took longer than a
  minute is ready ("Jarvis finished your chat's answer"). Each is said once,
  and never while a window is open (the window shows it itself).

## A coding reply checks your project once, quickly (v1.420.0)

- **Your project's own checkers run once per reply.** When the project has
  ruff, mypy, ESLint or TypeScript set up, they used to run after every round
  that edited a file, each allowed a minute, and asked for each round. Now
  they run once, when the reply is about to end, over every file it changed,
  beside the finish check's test run, and what they find goes into that
  check's note so the agent fixes it before it stops.
- **15 seconds for all of them together.** The checkers share 15 seconds. One
  that does not finish in that time is stopped, and the note says "not
  finished in time" with its name, so a slow checker is never mistaken for a
  clean one.
- **TypeScript is checked only on a small project.** tsc checks the whole
  project, so it runs only when a changed file is TypeScript and the project
  holds at most 400 TypeScript files (not counting `node_modules`, build
  folders or hidden folders). On a bigger project the note says "skipped:
  project too large for a quick check". mypy now checks the changed files
  without checking the files they import.
- **The same cards as before.** Under "Don't ask", or when you allowed the
  command tool for the session, they run with no card. Otherwise the test
  run and the checkers share one card, and it says Iron Jarvis asks.
- **Turning the finish check off turns them off too.** A folder whose
  `.ironjarvis/harness/finish.toml` says `enabled = false` runs no checker.
  A folder's own checks in `plugins.toml` still run after each edit, as you
  set them up.

## A coding session remembers its last reply's work, and spends less per message (v1.420.0)

- **The next message knows what the last reply read, changed and ran.** A
  coding session's history is sent to the model as plain words (so it can
  move to another account or vendor). Before, that meant the model forgot
  which files it had just read and read them again. Now the newest reply in
  the history carries one short line Iron Jarvis writes from that reply's
  own steps, for example "read a.py, b.py; edited cart.py; ran `python -m
  pytest -q` (failed)". It names files, commands and whether they worked —
  never what a tool printed — hides anything that looks like a key, and is
  at most 1,500 characters. Only the newest reply has it; you never see it
  in the conversation.
- **The parts of the instructions that stay the same come first.** What
  changes with each question (memory notes, project knowledge retrieved for
  it, recent activity, attachments, a "/" skill) now goes after the parts
  that do not (who Jarvis is, your profile, the coding-session instructions,
  the tool list). Claude's prompt cache then reuses everything up to the
  changing part on the next message instead of reading it all again. The
  same words are sent; only their order changed.
- **A coding session is not sent chat-only instructions.** The email-draft
  and chart instructions, the browser section and your saved workflows list
  are left out of a coding reply (they never apply there). Chat is unchanged.
- **Codex sessions use Codex's real window.** A Codex reply used to plan as
  if the model could hold 32,000 tokens, so older messages were dropped and
  summarised early. Iron Jarvis now reads the window from Codex's own model
  list on this PC (`models_cache.json` in Codex's folder, read in the
  background) — 258,400 tokens for today's models, the share Codex itself
  lets a conversation fill — and uses that figure when the file cannot be
  read.
- **A long conversation is summarised once, not on every message.** Once a
  chat or coding session passed the automatic summary point, every new
  message paid for a fresh summary, because each message moved what the
  summary covered. A stored summary of the same opening messages is now
  reused until six more lines have passed it; then one new summary is
  written.

## Each Claude Code round wastes less time around the model (v1.420.0)

Every round of a coding session on your Claude plan starts Claude Code once,
sends it the conversation, and lets it make one request to Anthropic. Iron
Jarvis measured what that costs on this PC, phase by phase
(`docs/CLAUDE-NATIVE-ROUND-COST.md`): the model took about half a second a
round, and about as much again went to Claude Code starting up, a new secure
connection to Anthropic, and waiting for Claude Code to quit.

- **The answer arrives the moment it is complete.** Iron Jarvis used to wait
  for Claude Code to quit (30-45 ms after it finished) and then clean up
  (up to 50 ms) before showing the answer or running the next tool. Now the
  answer is used at once and the clean-up happens in the background. The
  next round of the same conversation still waits until the last copy of
  Claude Code has quit, so a conversation never has two running at once; one
  that does not quit within 3 seconds is stopped.
- **One connection to Anthropic is kept open.** Each round used to open a new
  secure connection (64-84 ms on this PC). Now the connection is kept for up
  to 30 seconds and reused, so only the first round pays for it.
- **Nothing else about the request changed.** Each round still makes exactly
  one request on your plan, Claude Code's own retries are still refused, a
  sign-in refresh is still allowed once, and Stop still ends Claude Code and
  everything it started.
- **What is left.** Claude Code itself takes about half a second to start on
  every round. Keeping one Claude Code running for a whole session would
  remove that; it changes how these sessions work, so it waits for your
  decision.

## Coding sessions: keys from the list, real titles, calmer panes and fewer controls (v1.420.0)

- **Keys work from the session list.** In Chat › Code the list of sessions
  sits in the app's sidebar. After you click a session there, Esc stops a
  running reply, Ctrl+L puts the cursor in the message box, Ctrl+K opens the
  model menu and Ctrl+N starts a new session, without clicking in the
  session first. ↑ and ↓ move between the sessions in the list; Enter opens
  the one you are on.
- **Real titles.** A session named by a long first message (cut off with
  "…") is renamed after its first reply: three to six words from the start
  of that reply. A short first message keeps its title, and a name you gave
  the session yourself is never changed. No model is asked for the title.
- **The panes on the right.** Changes is read again every few seconds while
  a reply works, so files show up as they are written. When you have not
  picked a tab, the panes open on Changes if the last reply changed files,
  otherwise on Session. The column can be hidden (the button at its top
  right) and has two widths; both are remembered.
- **Fewer controls in the message box's row.** Plan is a choice in the
  approvals chip (it was also a separate "Plan first" chip). The account is
  named once, in the model chip ("Opus · Work"); the session's header only
  shows the account's state (a limit, a sign-in, its usage). Undo asks above
  the message box: "Undo it" undoes, "Keep" leaves the file as it is. The
  Session tab says whether the session's account is the one new sessions
  start on, and offers "Make it the default" when it is not.
- **Build › Code.** A session's ⋯ menu now has Rename and Delete (Delete
  takes two presses, and a session whose reply is still running is not
  deleted: stop it first). A search box above the list finds sessions by
  title or folder.

## A reply starts sooner, and a long one costs less to keep (v1.418.0)

- **A slow memory source no longer holds the first word.** Before a reply,
  Iron Jarvis gathers what grounds it: your project's notes, your lessons,
  your memory bases (some of them on other machines), the saved workflows.
  It used to wait for the slowest of these. Now it waits at most 2.5 seconds
  for the optional ones; a source still answering then is left out of THAT
  reply, and the reply's receipt says so (`late` in its context numbers,
  naming the source). Your project's own instructions, its folder's
  AGENTS.md / CLAUDE.md, and the apps you switched on for the chat are never
  left out: the reply waits for those.
- **A coding session does not walk its whole folder before every reply.**
  The project map at the top of a coding reply is kept, and Iron Jarvis only
  looks at the folder's top two levels to see whether anything there changed
  (a file added, removed, renamed or edited at those levels makes a new map).
  A change deeper in the folder shows in the map at the next change near the
  top; the model reads the files it works on either way. The finish check
  still takes its own picture of the folder when a reply begins, so a second
  reply reads the folder once, not twice.
- **A long reply's record grows by what each step added.** A coding reply
  keeps its own conversation so it can carry on after a restart. That record
  used to be written out whole twice a step; it is now written whole once and
  then only added to. The live record of the reply's words is joined only
  when it is saved or someone rejoins it, instead of on every word. Nothing
  you see changes; a long reply simply spends less of your PC on keeping
  itself safe.

## Chat › Code starts at the box, reads in order, and gives archived sessions back (v1.418.0)

- **A new session starts where you type.** With nothing open, and after New
  session (or Ctrl+N, or a folder's +), Chat › Code shows the message box
  with the caret already in it. Under it are the choices for the session:
  the folder (Choose… opens Windows' own folder picker; with no folder the
  session keeps its files in a folder of its own), "Work on a new branch"
  (only for a folder in a git repository that can start one), how much it
  asks before acting, the model, and Local only. Type and press Enter: the
  session is made in that folder with those choices and your words go to it
  as its first message. Nothing is made until you send, so leaving the box
  leaves nothing behind. A folder Iron Jarvis cannot use is said on the
  folder chip and your words stay in the box; any other reason is said under
  the box. If the session is made but cannot take your first message, it is
  not kept: you are back at the box with your words, your choices and the
  reason. Pressing New session again while the box is open keeps what you
  typed.
- **A reply reads in the order it happened.** What the session said and what
  it did now alternate the way they happened: "Let me look at the cart",
  then the files it read, then "Found the bug, fixing it", then the edit.
  Each run of steps is one quiet line ("3 steps · read 2 files, edited 1
  file") that opens to the steps themselves; the run it is working in shows
  its steps as they go. A reply you watched keeps that order once it is
  saved. A reply you did not watch (it ran while the window was closed)
  shows its steps before its final words, because Iron Jarvis keeps only the
  last part of what a reply said.
- **One line under each reply.** Instead of a stack of lines, a finished
  reply has one: "Worked for 2 min 14 s · 7 steps · Checked: tests passed",
  then who answered. Press it to open everything else: every step, the
  check, the task list, the files it changed (with Undo) and, under the
  newest reply, where things stand. The buttons for the next step stay in
  sight. Failed tests, failed steps and a reply that was cut off are said on
  the line in the warning colour.
- **Archived sessions can come back.** Archived sessions are listed at the
  bottom of the session list under "Archived (N)". Each has a Bring back
  button, and a session you open from there (or from a link) says it is
  archived with a Bring back button where you type.

## Chat reads "my latest emails" and questions about Iron Jarvis (v1.420.0)

- **Your own mail is your mail, never a web search.** "Summarize my latest
  emails", "check my new mail", "any new emails from Bob?" and "read my
  unread messages" now mean the mail app you connected (Gmail, or Microsoft
  365 for Outlook): its tools are offered, and the words "latest" or "new"
  no longer send the question to a web search. With no mail app connected,
  the reply carries one Connect button naming both mail choices. "My new
  messages on Slack" is Slack, and "my mail carrier" or "email marketing
  tips" are not your mailbox.
- **Questions about Iron Jarvis ask the Guide.** "What apps can you work
  with?", "what can you connect to?", "what are missions?", "what is Build
  for?", "what does the Code view do?" and "how do I use Build?" look the
  answer up in the Handbook and in your own setup. The everyday meanings
  stay everyday: "what are missions in the Bible" or "what is the build for
  the release" are answered as usual.
- **Coding work can be offered a coding session.** Iron Jarvis can now tell
  a request to build a piece of software ("build me a todo app", "write me
  a Chrome extension") or work in a repository ("fix the failing test in my
  repo") from office work or a shed. Chat still answers; the offer is a
  link to Build › Code, never a hand-off. (The link under the reply is
  being wired into chat in a later step.)

## See one message's changes, and put a file back (v1.420.0)

- **This message or the whole session.** Build › Code's Changes tab has
  two buttons at the top: **Whole session** (what it always listed:
  everything since the session started here) and **This message** (only
  what changed while the newest message ran). Iron Jarvis tells the two
  apart by reading the folder before each message; when it did not get to
  read it in time, the pane says so and lists the whole session instead.
  The first message of a session is the whole session.
- **Revert this file.** In a git repository each changed file has a
  **Revert this file** button: press it, then press again to confirm. The
  file goes back to how it was at the start of what the list shows (the
  session's start, or this message's). A file that did not exist then is
  moved to Iron Jarvis's trash folder, never deleted. Nothing else moves:
  the branch, its commits, what git has staged and every other file stay as
  they are. Revert waits while a reply is running, refuses a file that
  changed after the list was read (read the changes again first), and is
  not offered when the folder is not a git repository (the coding tool's
  own undo, or git, is the way back there).

## Plain words when an agent or a key check fails, and a few doors that were missing (v1.420.0)

**An agent you @-mention says why it could not answer.** When `@builder`
(or any agent you name in chat) fails, its line now says what happened and
what to do in one sentence, like "Claude Code is busy. Try again shortly, or
pick another model on its card in Agents." It used to say only "couldn't
answer" beside "(no reply)". A remote agent's line no longer prints raw words
such as "done": a finished reply has no label, a question says "has a
question", and a remote still at it says "working…".

**Stop works while an agent answers.** While an @-agent round is running, the
send button turns into Stop (Esc does the same). Stop ends the round on Iron
Jarvis's side too and leaves a "Stopped." line; your question stays in the
chat.

**A key check says what it found, in words.** On the first-run key door and on
Settings › Connections, Test used to print the check's raw text ("could not
reach anthropic: ConnectError: [Errno 11001] …"). It now says, for example,
"Couldn't reach Anthropic to check the key. Check your internet connection,
then press Test again." or "Anthropic refused your key. Update it in Settings ›
Connections." The raw text is still there, folded under **Details**.

**Connections uses plainer words.** "Subscription & local providers" is now
**Your plans and your own model servers**, and its **Rescan local CLIs** button
is **Look again**. A Google, Microsoft or Dropbox card that needs an app id
says what it is for ("Dropbox needs one to know which app is asking you to sign
in") before the name to save it under. When you add a model server, Iron Jarvis
asks it how it answers when you press **Fetch available models**; the choice
between OpenAI-compatible and Anthropic-compatible appears only when it could
not tell. The email words follow suit: **Email (sending)** and **Email
(receiving)** instead of SMTP and IMAP, and the email dialog's button opens
**Notifications**.

**Where a mission ran.** A mission's receipt now ends with "· local" (it ran on
this PC or a machine on your network) or "· cloud" (it left your network), the
same words a chat reply uses. A helper in a coding session that ran on a model
server you named says the machine too ("glm on DGX Spark").

**The status orb waits with you.** When every reply that is running is waiting
for your answer, the orb in the sidebar turns amber and says "Waiting for you"
instead of "Working".

**A few doors that were missing.**

- **Fleet › Add a node** takes an API key, for a model server that asks for one.
  It is stored encrypted and sent only to that server, the same way the
  Connections endpoint form does it.
- The first-run key door says **Google Gemini · free tier**, and what the free
  tier costs in privacy (Google may use what you send on it to improve its
  products).
- The first-run checklist's **Connect the apps you use** step offers **Import
  your tools** when Claude Code, Claude Desktop, Codex, Cursor or VS Code on
  this PC already use tools you have not added. It opens the same list the
  Tools page uses; nothing is added until you tick and press Add.

## Two Codes told apart, and the approval levels named by what they do (v1.420.0)

**Two views called Code, told apart by the tool.** The chat page's **Chat |
Code** switch is where Jarvis writes, runs and checks the code itself
(**Chat › Code**). Build has a **Code | Terminal** switch of its own, where
your own Claude Code, Codex, Pi, OpenCode, Grok or Antigravity does the work and Iron
Jarvis shows it (**Build › Code**). Both stay "Code" — each page names its
own — and where a list has to tell them apart, a session says which tool
writes its code: **Running now** lists "Coding session (Jarvis) · 12 min" for
Chat › Code and "Coding session (Claude Code) · 3 min" (or Codex, Pi,
OpenCode, Grok, Antigravity) for Build › Code. Build's one-line description names both
its terminals and its Code view, and typing "code", "claude code" or "codex"
in search finds Build.

**The approval levels say what they do.** The chip's levels, strictest
first, are now:

- **Ask before every action** — unchanged.
- **Ask before commands** (the chip says *Ask for commands*) — the default,
  called *Ask first* until now. It edits files and searches the web on its
  own and asks before running a command. The old name meant the opposite in
  Build › Code, where *Ask first* also asks before a file change.
- **Accept edits** — like Ask before commands, and a file edit in the folder
  never asks, even where the folder's own rules would.
- **Auto (its own safety check)** (the chip still says *Auto*).
- **Don't ask** — runs commands, edits files and uses connected apps without
  asking; a commit or push on a coding session's own branch, and a few
  safety cards, still ask.

Nothing about what each level does changed, and saved chats keep their
level. Typing an old name in chat ("set approvals to Ask first") still finds
the right level.

**Plainer words around coding sessions.**

- A coding session that works on its own branch keeps **its own copy of the
  folder** beside the repository (it said "worktree"). The Branch view's
  button after a merge is **Remove its copy of the folder**.
- The account words in a coding session say **your accounts** (the
  *Several accounts* card on Connections): "No longer in your accounts" for
  an account that was removed, and Usage says "From the account manager's
  last look at this account".
- An undo button that cannot undo says **This change can't be undone.**
- Build says **Opening your terminals…** while it loads.
- **Local only** says what it promises: nothing leaves **this PC or your own
  network** (a model on your network, like a server on a Tailscale tailnet,
  stays on your side) without asking you.
- The **Session board** has a grey **Stopped** lane for runs you (or
  Emergency stop) ended; **Failed** is real failures only. A stopped card
  keeps Retry and Dismiss, and **Clear stopped** clears only those.

## Build › Code keeps its history, and lets a message wait (v1.418.0)

- **Older steps are a press away.** A long session shows its newest steps;
  above them a line says how many earlier steps there are, and **Load
  earlier** puts the page before them on top (press again for more) — the
  first message is never lost. Opening a session is also quicker: once Iron
  Jarvis has driven a session, it keeps the newest steps in memory instead of
  reading the whole record from disk each time.
- **A conversation from elsewhere shows what was said there.** Open a
  conversation Claude Code, Codex, Pi or OpenCode saved by itself, or bring
  one in from a terminal, and the Code view shows that conversation's own
  history — your messages, the answers and the steps — read from the tool's
  own saved file (never changed). After **Open in terminal** and back, what
  you did in the terminal shows in order with the rest.
- **Send while it works: your message waits.** Press Enter while the tool is
  working and your message waits under the box ("Waiting", with an × to take
  it off). When the reply ends normally, Iron Jarvis sends it as your next
  message — even if you closed the page. If the reply was stopped, failed, or
  hit an account's limit, or Iron Jarvis restarted meanwhile, nothing is
  sent: the words come back to your message box, and a line says so. To tell the running reply something now,
  press **Shift+Enter** (or the arrow) — that sends it a note, as before.
- **Keys.** **Esc** stops a running reply (when no list is open). **Shift+Tab**
  steps through the modes — Ask first, Auto edits, Plan, Auto — and never
  turns on Full access (that still takes two presses in the mode menu).
- **The `/` list** opens when a line starts with `/`, anywhere in your
  message, lists up to 20 of the tool's commands and shows what each one
  expects after it.
- **Changing the model while it works.** The model can be changed while
  Claude Code, Pi or OpenCode works (it switches at once or from your next
  message); going back to the tool's default, and Claude Code's effort, wait
  for the reply to end, because the tool has to start again for them — the
  chip says so.

## A calmer coding conversation in Chat › Code (v1.417.0)

Four things changed in how a coding session's conversation behaves while a
reply streams. Nothing about what the session does changed.

- **The answer no longer blinks out at the end.** When a reply finished,
  its live text used to vanish for a moment (up to about two seconds) while
  Iron Jarvis read the saved copy back, then reappear. The live text now
  stays where it is until the saved reply is on screen, and the two swap in
  one step.
- **The conversation follows the reply as it grows**, as long as you are at
  (or within a few lines of) the bottom. Scroll up to read something earlier
  and it stays where you put it.
- **The finish check no longer writes itself into the reply.** After a
  reply that changed files, Iron Jarvis checks the work (it runs your tests
  again and goes through what you asked for). That check's own notes used to
  stream into the reply and then disappear when the reply was saved. Now the
  reply shows one quiet line while the check runs, "Checking the work…",
  with "Running the tests…" (then "The tests passed" or "The tests failed")
  under it, and then the same "Checked: …" line the saved reply keeps.
- **Each session keeps what you were typing.** Switch to another session,
  to Chat, or close the window, and the words you had half-typed are back in
  the box when you return (they are forgotten once sent). When a session
  opens, the caret is in its box, unless you were typing somewhere else or a
  question is waiting for you. The box grows with your words, up to eight
  lines, then scrolls.

The session also stopped redrawing the whole conversation for every streamed
word, every key you type and every check of the session list, which kept long
conversations slow to scroll and type into.

## What fills a coding session's conversation: the Context drop-down (v1.415.0)

The **Session** tab of a coding session, in both Chat › Code and Build ›
Code, has a **Context** drop-down. Press the heading to close it; Iron Jarvis
remembers that for the window.

- **The headline**: how full the conversation is for the model ("72% used")
  and how many tokens that is out of the model's limit ("722.5k / 1M").
- **The parts**: one row each, with a colour, its tokens and its share of the
  limit: the messages, the instruction files (AGENTS.md, CLAUDE.md), memory
  and your profile, the project and folder, the system prompt, skills, files
  you attached, a summary of earlier messages, the room kept free for the
  automatic summary, and free space. The bar above the rows shows the same
  parts; each segment is named on the bar and in its row, so the colour is
  never the only way to tell them apart.
- **Not counted above**: in Chat › Code, the descriptions of the tools the
  model can use, which are sent with each message ("Sent with each message,
  not counted above"). Iron Jarvis has always measured how full a
  conversation is without them, so they are listed apart and the percent
  stays the one the Summarize offer uses. In Build › Code, Claude Code's tools
  that load only when needed.
- **Lists** you can open: the instruction files, the extension tools by
  extension, and (for Claude Code) its skills.
- **How far the automatic summary is** ("240k until earlier messages are
  summarized") and the button that summarizes the earlier messages now
  (**Summarize** in Chat › Code, **Compact session** in Build › Code), which
  you can press only when no reply is running.

**Chat › Code** (Iron Jarvis's own coding sessions) counts every part itself,
from the very text each reply sent, so the parts add up exactly to the
percent. **Summarize earlier messages at** is a real setting (Auto is 92%;
85%, 80% and 75% keep more room free). It applies to every chat and coding
session. Summarize writes the same checked summary as the box's Summarize.

**Build › Code** asks the coding tool itself, and never starts it to ask:

- **Claude Code** answers with its own `/context` breakdown (its quick
  reading, which sends nothing over the internet), asked only while no reply
  is running (while one runs, the last answer shows, and Claude Code
  is asked again when the reply ends). Its tools that load only when needed
  are listed apart. **Compact session** sends Claude Code's own `/compact` as
  a message.
- **Pi** says how full the conversation is, but not what fills it, and the
  tab says so. Right after Pi summarizes by itself it does not know yet, so
  the tab shows no number until its next answer.
- **Codex** and **OpenCode** say only the total after each reply, and the tab
  says so.
- Codex, Pi and OpenCode summarize by themselves when the conversation is
  full; Compact session is offered for Claude Code only.

Also fixed: in a long Claude Code session, the percent could read far too low
(1%). Iron Jarvis read the size of the last call in Claude Code's report, and
that call could be a side call (an advisor or a summary) rather than the
conversation. It now reads it the way Claude Code itself does.

## Build › Code shows the work as it happens (v1.418.0)

- **A step appears when it starts.** A file being written, an edit or a
  command shows its row the moment Claude Code starts writing it, and the row
  fills in as the words arrive ("Write notes.md · 1,234 characters so far").
  A Codex command shows its output while it runs.
- **Helpers fold inside their row.** When Claude Code hands part of the job to
  a helper, the helper's steps and its answer sit inside that one row. Closed,
  it says how many steps the helper took and what it answered; press it to
  see each step.
- **Thinking says when it is happening.** The thinking line reads
  "Thinking…" while Claude is thinking, then "Thought for N s".
- **What Claude Code says about its own work shows.** Your hooks' output
  (Iron Jarvis asks Claude Code for every hook's), "compacting the
  conversation", an action Claude Code refused by itself, and a background
  helper that finished are quiet lines in the conversation. A request Iron
  Jarvis does not answer (a hook callback, a form for one of its added tools)
  is still refused, and the line says what was asked.
- **A command is named in Claude Code's words.** Its row reads the short
  description Claude Code wrote ("List tracked files and remotes") with the
  command underneath; a description that had to be cut ends in "…".
- **Long output: Show all.** A step shows the first 1,500 characters of what
  it printed; when there was more, opening the step offers **Show all N
  characters**, which reads up to 20,000 back from the session's record.
- **Every edit shows its change, in every mode.** In Full access Claude Code
  often sends an edit without its change; Build › Code now reads the change
  from the edit itself, and the line it landed on from the file.

## Fewer cards, and clearer ones, in Build › Code (v1.417.0)

- **Allow all commands this session.** A card for a command (Claude Code's
  Bash or PowerShell) now offers it beside Allow once. One press lets Claude
  Code run every command for the rest of that session without asking; it is
  saved for that session only, never in your Claude Code settings. Claude
  Code's own offer, which covers only commands that start the same way, is
  still there as **Allow similar commands**. Not offered in Full access
  (nothing asks there), in Auto (Claude Code does not keep a rule that
  allows every command there: its own safety check decides) or in Plan
  (it only reads and plans). For Codex the same answer is Codex's own "for this
  session" for that command, and only when Codex offers one; Pi and OpenCode
  do not have it.
- **The plan, on the plan card.** In Plan mode, the card that asks to start
  working shows the plan itself. **No, tell it what to do instead** opens a
  line under the buttons: what you type goes to Claude Code with the no, and
  it plans again with your words.
- **Enter** allows a card once, as in Chat › Code. It still does nothing in
  the first moment after a card appears, and in Full access Allow is a
  click.
- **A failed reply says which tool failed** ("Claude Code couldn't finish
  this answer") and has a **Retry** press that sends the same message again.
  Each finished reply ends with one quiet line, for example "answered by
  Opus 5.5 on Work · 2 m 14 s · 7 actions · 3 files changed"; press it for
  the tokens and the cost.
- **Starting and waiting are said.** When a message has to start the tool
  (the first message, after 15 minutes idle, after an effort change) the view
  says "Starting Claude Code…" or "Resuming Claude Code after 15 minutes
  idle…". The end of a reply no longer waits up to 10 seconds to save the
  session's id, and a note sent while it worked holds the end for at most 5
  seconds.
- Your answers to these cards now count in **Autonomy › Hands-off** under
  coding sessions, as interruptions. A Build › Code reply is not counted as
  a task there (Iron Jarvis does not read its outcome), so it never changes
  the hands-off rate.

## One approval means one approval in Chat › Code (v1.417.0)

- **"Allow for this session" covers the rest of the reply too.** Answering
  it on a command card now also covers a command started in the background
  in the same reply, and when a helper of the session gets your "for this
  session", the reply that is running (and a card it is waiting on) is
  covered at once. A folder rule that asks about a tool still asks.
- **Commands that only read run without a card** under Ask first, Accept
  edits and Auto: `git status`, `git diff`, `git log`, `git show`, listing
  branches, `ls`, `dir`, `cat`, `type`, `rg`, `grep`, `find`, `head`, `tail`,
  `wc`, `pwd`, `which`, `where`, `echo` and `node`, `python`, `uv` or `git`
  with `--version`. Anything that could write, chain, redirect or run another
  program (`>`, `;`, `&&`, a pipe into anything but another of these, `rm`,
  `git push`, `find -delete`, `rg --pre` or `--hostname-bin`…), a path
  outside the folder, a program of the same name in the folder itself (any
  kind Windows would run: `.bat`, `.cmd`, `.js`, `.vbs`…), `git` run in a
  sub-folder (it may hold another repository whose settings start
  programs), and `npm`, `npx`, `pnpm` or `yarn` even with `--version` (they
  can fetch the version a project names) still ask. So does a folder rule
  that asks about running commands. "Ask before every action" still asks for
  all of them. These commands only read, but they can read any file inside
  the folder (a `.env` file too), as the file-reading tool can.
- **The finish check asks less.** When your reply's own test run passed
  AFTER its last change, the check does not run it again (the record says
  the tests passed and were not re-run). Under "Don't ask" a project's own
  checker (ruff, mypy, ESLint, tsc) runs with no card; a folder's own checks
  and a project's command file still ask.
- **A card says who asks and why.** A card for the finish check's test run
  or a project checker says Iron Jarvis asks, not the assistant, and every
  card carries one reason line (for example "it is not in this chat's grants
  yet", or the rule that kept it under Accept edits or Auto).
- **Auto decides faster.** The quick checks of one round run together and
  each waits at most 3 seconds; the check also reads what you first asked
  for and what the assistant just said.
- **Your posture survives your own press.** An approved plan's work, and a
  "Resume when ready" you pressed, keep Accept edits or Auto. A night task,
  a check fix and a resume after a restart still run as Ask first.

## Build › Code is part of the app (v1.414.0)

A coding session in Build's **Code** view (your own Claude Code, Codex, Pi or
OpenCode) is now seen, and can know you, everywhere else in Iron Jarvis.

**Seen everywhere.**

- **Running now** (the status orb's list) shows a Build › Code reply while it
  runs, as "Coding session · <tool> · 12 min", with "Waiting on you" when a
  card is open. Pressing it opens that session in Build › Code. The status
  orb, the home line and Everything › Status count it too.
- **Your PC stays awake** while a reply is working, like any other coding
  reply. A reply waiting on your card does not keep the PC awake: nothing
  happens until you answer.
- **Updates**: if you install an update while a reply runs, the warning
  names it ("1 coding session in Build › Code working (Claude Code)").
- **When a long reply ends**: a reply that ran longer than a minute and
  finished (or could not finish) rings the bell once, "Your coding session
  finished", and says so from the tray when the window is closed. Not when
  you were looking at that session, and never for a reply you stopped.
- **On your phone**: a card nobody answers in the app for five minutes goes
  to your phone (when a phone is set up in Settings › Connections), the same
  way a Chat › Code card does. Reply **approve** to allow it once, or
  **deny**. Never sent: a question that needs words, a card that must be
  allowed once every time, and any card while the session is in Full access.

**Iron Jarvis's own tools, if you want them.** The session's header has an
**Iron Jarvis tools** button with two boxes, both off until you tick one:

- **Memory**: the coding tool may search and read your memory and your
  memory profile (read-only; it can never write). What it reads goes to the
  tool's model provider (Anthropic for Claude Code, OpenAI for Codex, the
  provider you chose in Pi or OpenCode) with the conversation.
- **Browser**: the coding tool may use your browser through Iron Jarvis;
  every click, typing or page change asks you first.

These are the same two boxes a Build terminal has. Ticking the first box (or
unticking the last) applies from your next message: the tool starts again on
the same conversation, connected to Iron Jarvis. Unticking a box takes effect
on the tool's very next call. Iron Jarvis never changes your own config files
for this: Claude Code gets a configuration file of Iron Jarvis's own, Codex
gets the setting on its command line, Pi gets a small extension of Iron
Jarvis's own, and OpenCode gets it when the conversation opens. None of them
holds a password: the connection's key lives only while that process runs.
The connection is named `iron-jarvis-code` (the tools show up as, for
example, `mcp__iron-jarvis-code__memory_search`), never the `iron-jarvis`
name a Build terminal writes into a Codex or Claude Code config, so the two
never mix.
Build › Code sessions have no Local only setting; when you want nothing to
reach a cloud model, leave Memory off.

**Codex asks before it uses one of these tools.** In **Ask first**, Codex
asks before every call to a connected tool server — Iron Jarvis's Memory and
Browser, and any server you set up in Codex yourself. That question is an
ordinary card in the Code view, for example *Codex wants to: Search Iron
Jarvis memory for "project codename"*, with the call's details underneath:
**Allow once**, **Allow for this session** (only when Codex offers it for
that call), or **Deny**. Pressing Stop while the card waits says no. In
**Full access** Codex does not ask. When a tool server asks Codex to fill in
a form or open a web page, the Code view cannot answer that yet: it says so
and declines it.

**When a tool has to sign in again.** If Claude Code, Codex, Pi or OpenCode
stops because its sign-in ran out (for example "OAuth session expired and
could not be refreshed"), the reply says that tool needs to sign in again,
with a **Sign in** button for Claude Code and Codex, instead of suggesting
Retry — trying again cannot fix an expired sign-in.

**Share your profile with every tool.** Settings › Memory & you › Share with
Build now has a switch for **Pi** and **OpenCode** too (off by default):

- Pi reads `AGENTS.md` in its own folder (`%USERPROFILE%\.pi\agent`, or
  `PI_CODING_AGENT_DIR`). If you keep an `AGENTS.override.md` there, Pi reads
  that one instead, so the profile goes there.
- OpenCode reads `AGENTS.md` in `%USERPROFILE%\.config\opencode` (or
  `XDG_CONFIG_HOME`). While that file does not exist, OpenCode reads your
  Claude Code instructions (`.claude\CLAUDE.md`) instead, so Iron Jarvis
  will not create it then: making it would stop OpenCode reading your Claude
  Code file. Turn on **Share with Claude Code** instead, and OpenCode reads
  your profile there.
- A new session for a tool whose switch is off shows one quiet line under
  the box: **Share your profile with <tool>** turns the switch on;
  **No thanks** hides the offer for good.

**The right column: Changes, Usage, Files, Session.** A Build › Code session
has the same row of icons on its right that a Chat › Code session has (in a
narrower window the column sits under the conversation; scroll down to it).
Rest the pointer on an icon to see its name.

- **Changes** lists every file that changed, each diff in one scrolling list,
  with **Reviewed** marks and **Open in VS Code / Cursor** at each change. The
  coding tool writes files by itself, so Iron Jarvis reads the changes from
  **git**: every file in the session's folder that differs from where the
  repository was when the session started here, committed since or not, new
  files included. That includes changes you or another program made in the
  folder meanwhile (the files the tool edited itself are counted as its own),
  and files git ignores are not listed. In a folder that is **not a git
  repository**, the list is the tool's own record of the files it edited with
  its file tools; changes made by commands it ran (a script, `sed`) are not in
  it. There is **no Undo** here: use git, or the tool's own undo.
- **Usage** shows what is **left** of the account's limit windows (the 5-hour
  and 7-day windows, with when each resets): Iron-Proxy's last look at the
  account when the session runs on an Iron-Proxy account, and the numbers the
  tool itself reported after its last reply (Claude Code and Codex report
  them; Pi and OpenCode do not, and the tab says so). Before any number
  arrives it says **Not known yet**, never 0%. Below that: this session's
  replies and tokens. A plan's use is shown at list price: what it would
  cost, not what you paid. Nothing here asks the tool or the vendor anything;
  it shows what was already reported.
- **Files** is the session folder's files.
- **Session**: where it works (this computer, the folder with **Open**, the
  git branch, instruction files such as `CLAUDE.md` in the folder), what
  answers (the tool, model, effort, account, the permission mode and what it
  means, and which Iron Jarvis tools are on), how full the conversation was
  at the tool's last report, your last attachment, how many files changed
  (**See changes**), and the programs running under the tool right now (its
  own helpers included). When the tool is not running, nothing is listed; it
  starts again with your next message.


**Your Iron-Proxy accounts count as signed in.** If this PC's own Claude Code
or Codex login is signed out but one of your Iron-Proxy accounts for that tool
is ready, Build › Code still offers the tool, and a new session runs on that
account.

**A reply waits for its own answer.** If Claude Code still has older work in
hand when you send a message (for example, helpers a usage limit stopped),
it may finish that first. The reply now stays open until Claude Code has
answered your message, instead of ending early with "0 in, 0 out".

## A project knows its coding sessions (v1.414.0)

A project now sees the coding work done in its own folder.

- **Its page lists its coding sessions.** On a project's **Board** tab, and
  on the Board of its team screen (Agents › the project), a **Coding
  sessions** list shows every coding session whose folder is inside the
  project's folder: the ones in **Chat › Code** and the ones in **Build ›
  Code** (your own Claude Code, Codex, Pi or OpenCode). Each row says its
  title, which tool, what it is doing (Working, Waiting for you,
  Finished, Failed, Stopped, No reply yet) and when. **Open** takes you into
  that session in its own view. A project with no coding sessions looks
  exactly as before.
- **Waiting on you and Completed include them.** A coding session waiting
  for your answer (a card or a question) is listed under **Waiting on you**; one whose last reply finished is
  listed under **Completed**, named as a coding session.
- **How a session finds its project.** Nothing is stored. A coding session
  belongs to the project whose folder holds the session's folder (the most
  specific one when projects sit inside each other; never an archived
  project). Make a project over a folder and the coding sessions already
  there appear in it; archive the project and they leave it. A session on
  its own branch counts by the folder you picked, and a session started with
  no folder belongs to no project. Archived sessions are not listed.
- **Jarvis knows about them too.** The "Recent activity in this project"
  part of what Jarvis reads in a project chat or an agent run now includes
  the project's recent **chats** and **coding sessions**, newest first, up to
  eight lines: a title, what it is and how long ago. It never includes what
  was said in them. A title that reads like an attempt to give Jarvis
  instructions is left out. A chat or coding session you keep **on this PC
  only** (Local only) is never named to a model that is not on this
  network, and neither is an agent run you kept on this PC only (its task
  and summary stay out too). A Build › Code session on **Pi** or
  **OpenCode** may run on a model on your own network, so its title is kept
  from a model that is not on this network too (Claude Code and Codex
  sessions are named: their words already go to that company). A project with no chats and no coding sessions
  gets exactly the same lines as before.
- **Corrections in a coding session become a one-press preference.** When
  you correct how Jarvis works in a coding session in Chat › Code ("stop
  explaining every step") and then say it again, the reply gets the same
  quiet **Keep · Edit · Not this** line a chat reply gets. Your answer is
  remembered on the Memory page and shown the next time you open the
  session. A correction you tie to one place or moment ("too long in this
  repo", "be brief right now", "no caveats in this module") is never offered
  as a standing preference (unless you say it lasts: "you keep doing that,
  even here"): put that in the repository's own AGENTS.md or CLAUDE.md
  instead. Pressing **Retry** on a reply that failed does not count as
  saying it a second time. A turn Jarvis started by itself (carrying on after a
  restart, the night shift, fixing a check) never asks. Build › Code runs
  your own Claude Code or Codex, whose words do not pass through Jarvis, so
  it does not offer these.

## Tool packs use less memory: no launcher left running (v1.415.0)

A tool pack added as `npx -y <package>` or `uvx <package>` used to keep its
launcher running beside the real server for as long as the pack was on:
about 80 MB for each `npx` pack and about 160 MB for each `uvx` pack, doing
nothing. Jarvis now asks the launcher once which files it runs, remembers the
answer, and from then on starts the server from those files itself.

- **Nothing to set up.** The first start after the update still goes through
  the launcher, exactly as before; the launcher is asked in the background
  after that, and the next start (a restart, or **Retry**) is direct. A pack
  started this way also starts faster: `npx` checks the internet for a newer
  version on every start, which took 3 to 5 seconds.
- **Still up to date.** For a pack with no version pinned, Jarvis checks at
  most once a day whether a newer version is out, without changing anything
  (for `npx` packs it asks npm which version it would pick; a running pack's
  files are never touched). When there is one, the pack's next start goes
  through its launcher, which installs it, exactly as before.
- **Never a guess.** If anything is not certain (a copy installed globally or
  in the pack's folder, a version the saved copy does not match, an unusual
  launcher command line, or a `%NAME%` or `^` in its arguments that the
  Windows command line would change), the pack starts through its launcher as
  before. If a direct start stops before answering, the pack is started
  through its launcher right away; if it is still silent when the pack's
  start time runs out (15 seconds), it is tried once more through its
  launcher. Either way the pack's record says what happened.
- **Turning it off.** Set the environment variable
  `IRONJARVIS_MCP_DIRECT=off` before starting Iron Jarvis to start every
  pack through its launcher.
- One difference: `npx` adds its own `npm_…` settings to the server's
  environment; a direct start does not. No known pack reads them.

## Jarvis fixes its own pull request's failing checks (v1.407.0)

When a coding session on its own branch has opened a pull request, Iron
Jarvis watches the pull request's checks on GitHub. When a check fails, the
session fixes it by itself, a reviewer on Claude Code or Codex checks the
fix, and only a fix the reviewer approves is pushed.

What happens, step by step (each step is a line in the session and a row in
the bell):

1. **The checks are read.** While a check still runs, Iron Jarvis looks again
   after a minute, then less often (up to every 10 minutes), and stops
   watching after 6 hours if the checks never finish. When every check
   passed, it rests until the branch is pushed again.
2. **A check failed.** As soon as the session is free (never while a reply of
   yours is running), Iron Jarvis reads the failed check's log on GitHub and
   starts a reply in the session by itself: "Checks failed on this session's
   pull request: …". It runs on the session's own model and account, with its
   Local only and trust. Because nobody is watching and the log comes from
   outside, the reply works the way a night task does, whatever the session's
   approval setting: reading, editing and running commands in the session's
   folder happen without asking; anything else (a web page, a browser step, an
   app) asks you, and if nobody answers within the night shift's card wait it
   is not done and the fix waits for you. The log is shown to the model as
   data (secrets in it are masked).
3. **The fix is reviewed.** When the reply ends having changed files, the
   reviewer reads the change. It can read the session's files but cannot
   change them, and it ends its report with its verdict. The setting **Who
   reviews a check fix** picks Claude Code or Codex; Automatic picks the one
   that is connected and did not write the fix, when there is one.
4. **Approved: pushed.** Iron Jarvis commits the fix (leaving out anything
   that looks like a secret, a huge file or a dependency folder, and naming
   it) and pushes it to the pull request's own branch, with no card. This is
   the one push Iron Jarvis makes without asking: only after an approved
   review, only to the branch of that pull request, never to its base branch,
   never with force. It then watches the new checks; when they pass, you get
   one notice: "Your pull request's checks pass now".
5. **Not approved: a side branch.** The fix is committed to a new branch
   beside the pull request's (`ij/<name>-ci-fix-1`) and is not pushed. The
   session works on what the reviewer found and the reviewer looks again.
   When it approves, the pull request's branch takes the fix (a fast-forward)
   and it is pushed.
6. **At most 3 rounds.** A round is one fix and one review. After 3 rounds
   without passing checks or an approval, Iron Jarvis stops and tells you
   ("Iron Jarvis stopped fixing your pull request's checks"); everything stays
   on its branches for you. The count starts again when the checks pass or
   when you push the branch yourself.

What it never does:

- **Merge.** Merging the pull request is always your Merge press.
- **Push for a Local only session.** No cloud reviewer may read a Local only
  session's code, so its fix waits in the session's folder for you. The same
  happens when the fix reply went to low trust, or when neither Claude Code nor
  Codex is connected.
- **Push what the reviewer could not judge.** The fix waits for you, and the
  session says why, when: a step of the fix needed your OK and was not done;
  a commit Iron Jarvis did not make sits between the failing checks and the
  fix (it names the commit); the fix changes a file that is not text; or the
  branch would push anywhere but the pull request's own repository on GitHub
  (a folder on this PC, another host, an ssh shortcut name).
- **Work under Emergency stop.** With Emergency stop on, nothing is read or
  started; a fix reply that is running is stopped, a review in flight is
  stopped, and the commit (with the repository's own hooks) or the push in
  progress is ended. If a push was cut off, Iron Jarvis reads the pull
  request's branch back and says what really happened: pushed, or not.
- **Change what the agent itself may do.** A commit or a push the session's
  own model tries in its shell still asks you every time, as before.

You get a desktop or phone notice only twice: when Iron Jarvis gives up, and
when the checks pass after the fix it pushed. Every other step is a quiet row
in the bell and a line in the session.

To turn it off, switch off **Fix my pull requests' failing checks**
(Settings, Agents & automation). Turning it back on from a chat always asks
you first.

## One switch: Ask, Accept edits, Auto or Plan (v1.407.0)

The permission chip in the chat box (in a chat and in a coding session) is
now the ONE place you choose how much Jarvis asks. Its levels, strictest
first:

- **Ask before every action** (the chip says *Ask every time*): asks before
  it edits files, uses the internet, runs commands, or clicks or types in a
  browser. This is the level that was called *Ask first* before v1.407.0.
- **Ask before commands** (the chip says *Ask for commands*): the default. Edits
  files and searches the web on its own, asks before running commands, an
  app's tools and anything risky. This is the level that was called *Ask
  first* until v1.420.0 and *Ask when risky* before v1.407.0. Saved chats
  keep exactly what they had; only the names moved.
- **Accept edits**: as Ask before commands, and a file edit inside the chat's or the
  session's folder never stops for a card, even where the folder's own rules
  would ask. An edit outside the folder (or into a folder Iron Jarvis keeps
  for itself) still asks.
- **Auto**: lets routine work through and asks only about risky things. It
  starts from what Ask before commands would ask about and, for each of those, first
  applies fixed rules (no model call, no cost): reading, edits inside the
  folder, running the project's tests (`pytest`, `npm test` and the like,
  joined only by `&&`, with no output sent into a file), the session's own
  step list, skills and notes, reading or stopping a program the session
  started, and handing work to a helper all go ahead. Some things **always
  ask**, and no rule or model can wave them through: a protected setting,
  anything that asks once every time (a commit or push on a session's
  branch, a project's or plugin's command lines, a plan to approve), anything
  that costs money, anything that reaches outside this PC in a Local only
  chat, a new tool in an app, a chat that read something it does not trust,
  a command the folder itself wrote, anything that acts in your browser, on
  your desktop, in a terminal or in a running program, an app's tool that may
  change things, anything that may send, post or pay, and anything that may
  delete or overwrite (`rm`, `del`, `git push`, `git reset`, `npm publish`,
  `DROP TABLE` …). What the rules leave undecided (an ordinary command such
  as `ls`, an app's read-only tool) gets ONE quick check by the same model
  that is answering you, on the same account, under Local only when that is
  on, and billed to the reply. It sees only your request and the call (never
  a tool's output or a page) and may answer only "allow"; anything else — no
  answer within a few seconds, an error, a garbled answer, the demo model —
  keeps the card. Auto never asks about MORE than Ask before commands would.
- **Plan** (coding sessions): the session only reads and writes a plan for
  you to approve before it changes anything. Picking Plan keeps your level;
  picking a level turns plan mode off. The *Plan first* chip beside it still
  works and changes the same setting.
- **Don't ask**: last, with its warning, exactly as before.

Every call Accept edits or Auto lets through is still a real decision: it
has its row in the ledger, the reply's receipt says "3 actions approved
automatically" and, opened, lists each one and why ("it runs the project's
tests", "a quick check said it is routine for what you asked"), and the
hands-off numbers count it apart (it never counts as an interruption).

**Where they apply.** A chat or a coding session you are watching, and the
helpers and teammates it starts (a helper applies the rules only: what they
leave undecided still asks you). A schedule, a goal, a workflow, a rule, a
phone message, an assignment or anything else that runs with nobody there
gets *Ask before commands* instead, like Don't ask. A chat sent through the API (no
card can be shown there) runs only what the rules pass and says, in words,
that the rest needs your OK on the chat page. Turning Auto on from chat asks
first, as Don't ask does.

**What else always waits for you under Accept edits and Auto.**

- An edit of a file inside the folder that steers git, your editor, CI or
  Iron Jarvis itself: anything under `.git`, `.ironjarvis` or `.claude`,
  `.vscode/tasks.json` and `.vscode/settings.json`, anything under
  `.github/workflows`, and `.gitattributes`, `.gitconfig`, `.gitmodules`
  (whatever the letter case, and however the path is spelled).
- A test run in a reply that has changed a file the tests run first:
  `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`,
  `package.json`, a vitest or jest config, or a script named like the test
  program (`pytest.bat`). Auto does not pass that run by its rule; the quick
  check decides, and otherwise you are asked.
- Code Jarvis writes and runs (`run_code`) that deletes, overwrites, starts
  another program or sends something over the network. Other code gets the
  quick check.
- A coding-session reply Iron Jarvis carries on by itself (after a restart,
  when an account's limit resets, on another account, the work of a plan you
  approved, the night shift) runs as Ask before commands: nobody is watching it start.

**Edits to Git's own files and Jarvis's own rules ask under Ask before commands too
(v1.408.1).** File edits run on their own under Ask before commands, but an edit of a
file that steers Git, your editor, CI or Iron Jarvis itself — the same list
as above: anything under `.git` (its settings and hooks, which Git can run as
programs), `.ironjarvis` (this folder's own permission rules and harness),
`.claude` (Claude Code's settings and commands), the coding agents' own
instructions files (`AGENTS.md`, `CLAUDE.md`, their `.local.md` versions,
`AGENTS.override.md` and `.ironjarvis.md`, in any folder), `.vscode/tasks.json` and
`.vscode/settings.json`, `.github/workflows`, `.gitattributes`,
`.gitconfig`, `.gitmodules` — now stops for a card under **Ask before commands** and
**Ask before every action**, in a chat, a coding session and the helpers it
starts. The card says why in plain words ("it changes Git's own settings",
"it changes Iron Jarvis's own safety rules for this folder") and offers
**Allow once** and Deny only: it asks every time, no "for this chat" answer
or standing grant covers it, and Enter never allows it (click Allow once).
A chat sent through the API (no card) refuses it and says so; a schedule, a
goal, a rule, the phone or anything else running with nobody there refuses
it with a sentence instead of waiting for an answer; a night-shift task's
card waits the night's card wait and is then declined, like any other card
at night. Under **Don't ask** it runs without a card, as everything does —
you chose that level. Not covered yet: a shell command that writes into
those files (`echo … > .git/config`) follows the command's own card.

## Chat | Code: coding sessions moved to the chat page, Build is the terminals (v1.407.0)

The chat page now has one switch at the top, in the middle: **Chat | Code**.

- **Chat** is the chat, exactly as before.
- **Code** is the coding sessions: the screen that was Build's second view
  ("Coding sessions"). Your sessions are grouped by folder, the conversation
  sits in the middle, and its plan, timeline, terminal, changes, browser,
  files and session tabs sit beside it. Nothing about a session changed;
  only where it lives.
- **The switch stays put.** It sits on its own row above both views, so it
  never moves when you switch. Switching never loses your work: a chat you
  were typing or a reply that was still coming is exactly where you left it
  when you come back, and a coding reply keeps going while you look at Chat.
- **The sidebar shows the right list.** In Chat its list is your chats; in
  Code it is your coding sessions (with New session, Tonight and Last night
  at the top). On a phone the ☰ menu holds the same list.
- **The address says which.** A coding session opens at
  `/chat?view=code&session=<id>`, and Last night at `/chat?view=code&night=<date>`.
  The bell, Running now, Safety checks, Activity, the desktop app's
  notices and the night shift all open those. An old link that named Build
  (`/terminals?session=…`) still works: Build sends it on to the session.
  Each window remembers the view it last showed (a new window starts on
  Chat).
- **Build is the terminals only**: live terminals for your own coding tools
  (Claude Code, Codex, any command line). It has no view switch any more; a
  link to a terminal (`/terminals?focus=<id>`) opens it there, and a coding
  session's own Terminal tab is still a normal Build terminal.
- **Fewer rooms in the sidebar.** The sidebar is **Chat**, **Build**,
  **Projects**, **Everything** and **Settings**. The automation and admin
  pages — Agents, Autonomy (goals), Workflows, Schedules, Reflexes,
  Sentinels, Webhooks, Fleet, Sessions and the Session board, Activity,
  Usage and Self-development — are on **Everything** and in **Ctrl K**, as
  every page is; nothing was removed and every page still opens by its
  address.
- **New words.** A notice about a coding session says "coding session"
  ("Your coding session finished", "A coding session is waiting for your
  OK"), never "Build session". **Ctrl K** has a row for **Code** itself.

## The next step under a reply (v1.407.0)

When a reply finishes, the newest one can end with a short **"here's where
we are"** line and one or two buttons for the obvious next move, so you can
press instead of typing. It is in a chat and in a coding session, in the same
quiet style as the lines around it.

- **What it says** comes only from what the reply's own record already says —
  no extra model call: how many files it changed, how its finish check went
  (tests passed, failing, fixed, not run), what the team's reviewer said,
  points of your request still left, that it stopped at its step limit or
  the session's limit, that the changes on the session's branch are not
  committed yet, or what it offered to do next. A plain answer gets nothing:
  no box, never filler.
- **The buttons** (at most two; the first is the one it recommends) are
  always things you could already do:
  - in a coding session: **Keep going** after a step limit (the same press
    as before, now in one place), **Fix the failing tests**, **Fix what the
    reviewer found**, **Commit** (opens the branch's own Commit with its
    drafted message; nothing is committed until you press Commit there) and
    **Open a pull request** (opens the branch's own Create PR);
  - in both: **Yes, do it** when the reply ended by offering more work
    ("Want me to also add tests?") — it sends "Yes, go ahead.", exactly as
    if you typed it; in a chat, **Continue** once the app it offered to
    connect is connected.
- **Your decisions stay yours.** When the offer is something only you
  should decide — sending, posting, pushing, merging, deleting, paying — the
  button only names it: a press puts the words in the message box (or opens
  the existing Commit / Create PR form), and nothing happens until you press
  Send. A button never skips an approval card, a grant or an "Allow once"
  rule: it is the same as typing or pressing that thing yourself.
  Since v1.408.1 every PART of the offer is read: "run the tests and then
  push", "git push --force", "rm -rf the build folder", "wipe the logs",
  "empty the trash", "transfer the funds", "charge the card", "place the
  order", "cancel the subscription", "sign the contract", "revoke the token",
  "reset the database", "run the deploy script", "tag v2 and publish the
  release", "run the migration on staging" are all yours to decide. The same
  reading decides whether Jarvis, at the end of a coding reply, carries on by
  itself with something it offered: it never does for these — it keeps the
  question for you. Words that only name a piece of the app ("the save and
  cancel buttons", "the sign up pages", "the empty states") stay ordinary
  work.
- **It gets out of the way.** The line and buttons step aside as soon as
  you type, and the box is gone while a reply runs. It is never saved as a
  message: reopening a conversation shows it again under the newest reply
  only. A failed reply keeps its own line (Retry, Sign in) instead.
- **Suggested follow-up questions** (Settings › "Suggest follow-up
  questions", off by default) appear inside the same box, below the line.

## Two coding sessions side by side (v1.407.0)

In **Chat › Code** you can open a second coding session **beside** the first
and watch or drive both at once.

- **To open one beside:** point at a session in the list and press the
  small two-column button on its row, or press **Shift+Enter** on the row, or
  drag the row to the right edge of the screen and drop it there.
- **Each column is a whole session**: its own message box, its own approval
  cards and questions, its own Stop, plan and receipts. Both replies keep
  coming at the same time. Each column has a small header with the session's
  name and **×**, which closes that column and leaves the other session on
  screen (the closed session keeps going; it is still in the list).
- **The plan, timeline, terminal, changes, browser, files and session tabs
  on the right belong to the column you are working in** — the one with the
  coloured line on top, whose header says *on the right*; the line above
  those tabs says "Views for" and the session's name. Click anywhere in the
  other column (or type in its box) to see its own.
- **Keys stay in their column.** Esc stops the reply of the column you are
  in, Enter answers that column's card, Ctrl+L and Ctrl+K are that column's.
  A key never presses the other column's Allow.
- **At most two.** Opening a session that is already in one of the two
  columns brings that column to the front; opening a third session from the
  list puts it in the column you are working in.
- **On a narrow window or a phone** (narrower than 1280 px, where a single
  session already puts its tabs under the conversation) a split shows one
  column with a tab for each session above it; the other session keeps
  working out of sight.
- **Kept like the rest:** the address says both
  (`/chat?view=code&session=<one>&beside=<other>`), so a reload or a copied
  link opens the same two, and each window remembers its own split. While
  both sessions are on screen, neither's "finished" notice rings — you are
  watching them.

## Reviewing a coding session's changes, and opening them in your editor (v1.405.0)

A coding session's **Changes** tab is one scrolling review. Every file the
session changed shows its changes right there, one file after another: no
press to open a file and no dialog to close. Both views work this way: **This
session** (what this session changed) and **Branch** (everything the branch
changed against where it started, for a session on its own branch).

- **The top line** says how many files changed, how many lines were added
  and removed, and how many files you have marked reviewed ("3 files changed
  +42 −7 · 1 of 3 reviewed"). **Collapse reviewed** folds the files you have
  marked; **Expand all** opens every file again; **Wide view** shows the same
  review in a large window, side by side (before | after) on a wide screen.
- **Each file** starts with a line that stays at the top while you scroll
  through that file: what happened (edited, new file, deleted), its path, the
  lines added and removed, and the words that matter without a press —
  *changed since* (the file changed again after the session wrote it),
  *undone*, *binary*, or why there are no lines to show. Then:
  - **Open in VS Code** (or **Open in Cursor**) opens the file at its first
    changed line;
  - **Undo** (in **This session** only) takes that change back, the same Undo
    as under a reply. The Branch view has no Undo: committed work is not in
    the undo record;
  - **Reviewed** marks the file and folds it. The mark is kept for this
    session on this computer, and it is cleared by itself when the file
    changes again, so a file the session edits once more asks for a new look.
- **Each part of a file** (a hunk) says where it is and offers **Open at line
  N**, the line where that part starts in the new file. A deleted file opens
  nothing.
- **There is no Keep.** A change is already saved on disk when you see it;
  Undo takes one back.
- A long file shows its first 400 lines with **Show all N lines** for the
  rest. With many files, a file's lines are drawn as it comes near the screen.

**Open in your editor from anywhere in a coding session.** A step that worked
on a file (read, wrote, edited or patched it) has **Open in VS Code** at the
end of its line (shown when the pointer is on the line; always shown on a
touch screen); a read opens at the line it started from. In a reviewer's
report, every place it names like `src/app.py:12`, `src/app.py:12:5` or
`app.py#L12` can be pressed to open that file at that line. Only a file inside
the session's folder opens from a report; a web address or a path that climbs
out of the folder stays plain text.

**Which editor.** Iron Jarvis finds VS Code and Cursor on this PC by the links
they register when installed. **Settings › Agents & automation › Code editor**
chooses: **Automatic** (VS Code when it is installed, else Cursor), **VS
Code** or **Cursor**. With no editor found, no Open button is shown. On a Mac
or Linux the app cannot tell which editors are installed, so choose one there
to see the buttons. A press opens the editor through its own link; if the
file is gone, outside the folders Iron Jarvis may read, or on a network share,
the button says so in one line beside it. The chat page shows none of these
buttons, and neither does a session whose folder is on a network share. VS
Code shows its own confirmation dialog the first time a link from Iron Jarvis
opens it (asking whether to allow it); that question is VS Code's, and Iron
Jarvis cannot answer it for you. A workspace file (`.code-workspace`) is never
opened this way, because the editor would load that workspace's settings and
tasks, and a shortcut (a link) that leads into a network share is refused
before anything follows it.

## Stable or Fresh updates (v1.405.0)

Iron Jarvis ships often, and now and then a release has a problem the next one
fixes. So each PC can choose how soon it takes a release, in **Settings ›
System › Updates** (the desktop app):

- **Stable** gets a release once it has been out for a day with every test
  passing, unless you held it back. Choose it on the PC you rely on every day.
- **Fresh** gets every release as soon as it is published. This is how every
  PC worked before, and it stays the default. Choose it on your other PC, so
  it meets each release first.

The page shows which version each channel offers right now and which version
this PC runs. A change applies at the next update check (the page checks at
once); no restart is needed. A PC never goes back to an older version: if it
was on Fresh and runs a newer version than Stable offers, it stays on that
version until Stable passes it, and the page says so. An update Fresh had
downloaded, or was still downloading, when you moved to Stable is not offered
and does not install when you quit (unless Stable offers that same version).

**How a release reaches Stable.** Every hour a small job on GitHub (the Stable
channel workflow) looks at the releases and takes the newest one that:

- has been published for at least a day,
- has not been replaced: when a newer patch of it is out (1.388.1 after
  1.388.0, the way fixes ship), Stable waits for that one to be out a day
  instead. A newer feature release (1.389.0) does not count as a replacement,
- has its installer and update files,
- passed the full test run on GitHub (a run someone cancelled does not count
  either way), and
- you have not held back.

Stable only moves forward. If nothing qualifies, it stays where it is. If
GitHub's test results for the newest candidate cannot be read, the job puts
nothing older on Stable that hour, so a Stable PC never downloads two versions
an hour apart.

**The day's wait is time only.** Iron Jarvis cannot see whether your other PC
ran a version, how long for, or whether it worked. If a version misbehaves on
your Fresh PC, hold it back: on the Updates page of that PC, press **Keep this
version off Stable** twice. That asks GitHub, through the GitHub CLI (`gh`) on
that PC when it is installed and signed in, to run the Stable channel
workflow with that version held. The hold is recorded when that run happens;
`holds.txt` on the `updates-stable` branch shows it (a run that was already
putting the version on Stable can still finish first). Without `gh`, the page shows the exact
command to run and a link to the workflow's page on GitHub, where **Run
workflow** has three boxes:

- **hold**: keep a version off Stable;
- **unhold**: let a held version reach Stable again;
- **promote**: put a version on Stable now, without the day's wait (and even
  when a newer patch replaced it: naming it is your call, and the record says
  so). It is still refused for a version that is held, not published, failed
  its tests, or is not newer than what Stable offers; the run says why.

A version already on Stable cannot be held back: Stable never moves to an
older version.

Chat can switch a PC to Stable on its own; switching to Fresh from chat always
asks first, because it lets every release reach the PC.

For whoever ships Iron Jarvis: this release adds a workflow file, so its
release tag must be pushed by hand (`git tag vX.Y.Z <sha> && git push origin
vX.Y.Z`), as for any change under `.github/workflows/`.

## One approval for a whole coding session (v1.404.1)

In a coding session, helpers (and a team's workers) each run on their own.
Before this release, approving one helper's command covered only that helper,
so the next helper asked again, and a session could wait on several cards in
a row for the same kind of command.

- **"Allow for this session"** on any card of a coding session, from the
  session itself or from any of its helpers, now covers the whole session: the
  session, every helper it is running, and every helper it starts later. It is
  saved with the session, so its later replies keep it.
- **Helpers already waiting** on a card that the answer covers go on at once,
  without a card of their own.
- **Running a command is one approval.** Approving a command also covers a
  command started in the background and typing into one.
- **Some cards still ask every time**, whatever you allowed: a commit or push
  on the session's branch, a project's or plugin's command lines, a call that
  costs money, and anything leaving this PC in a Local only session. "Ask
  before every action" still asks each time.
- **Never shared with the whole session:** a connected app's tools (each app
  is asked for on its own), a newly added tool that is still waiting for your
  trust, and anything in a session set to "Ask before every action", in a
  Local only session, or in a low-trust reply. There, each card covers only
  the run that asked.

## The Session tab in a coding session (v1.404.0)

A coding session's right-hand pane has a seventh icon, **Session**, after
Files. It shows the session at a glance:

- **Where it works**: this computer, the folder (with **Open**), the branch
  it works on (or that it works in the folder directly), whether the session
  is Local only (nothing in it leaves this PC without asking you), and the
  instruction files the newest reply followed, such as CLAUDE.md.
- **What answers**: the model and its reasoning level, the account and how
  much of its window is used, the approval level, plan mode, and the team
  when the session has one.
- **Context**: how full the conversation is for the model, from the newest
  reply's own reading. Nothing is shown before the first reply. After
  **Compact**, the old reading is out of date, so the tab says the earlier
  turns are summarized until the next reply measures again.
- **Last attachment**: the pictures and files you sent most recently, or the
  files you picked with @, with the message's first words and when you sent
  it (and the / skill, when one rode the same message). A message you are
  sending right now counts at once. A picture or file opens in its own app
  (never an .svg). Earlier uploads are listed under **Earlier uploads**, each
  once.
- **Changes**: how many files this session changed (read again every 15
  seconds while a reply works), and **See changes**, which opens what this
  session changed even when it works on its own branch.
- **Background**: the helper agents working now, and the programs the
  session started in the background (a dev server, say), each with how long
  it has run. A command that ended but left what it started running (such as
  `start /b node server.js`) says so and can be stopped too. **Stop** ends the
  program and everything it started; it stays listed as stopped, and the
  session reads that it was stopped. If Iron Jarvis cannot reach a program a
  finished command left behind, Stop says so instead of claiming it. Programs
  a command left running with a plain `&` are counted, not listed: they are
  stopped when the session is archived or deleted, or when you quit Iron
  Jarvis (a window such a command opened, like an editor, is left open and is
  not counted). Programs a helper agent started belong to the helper's own run
  and are not listed here. The list is read again every few seconds only while
  something runs or the session is working, and only while the tab is shown.
  If the list cannot be read, the tab says so.

The tab reads only what the app already knows. A command line is cut to one
line, and a key in a known shape, or the value after a flag such as
`--api-key` or `--token` (quoted values whole), is hidden. A secret in no
known shape (such as `-pS3cret`) can still show.

## A team for a coding session (v1.403.0)

A coding session can have its own small team: one model leads, one or two
others write the code, and one checks every change. Telling a planner to hand
work to a team did not work (in 108 measured runs it handed out nothing), so
here the roles are kept by Iron Jarvis, not suggested.

- **The roles.**
  - **Team lead** is the session's own model. It talks with you, reads the
    project, writes the plan and what "done" means, splits the work into
    pieces, hands each piece to a worker, checks each worker's report,
    answers the reviewer and reports back to you.
  - **Workers** (one or two members of your model team) each take one piece
    at a time on their own model and account, in the session's folder and
    branch, under the session's approval setting, Local only and trust.
    Each is told to do only its piece, touch only the files the piece names,
    run the tests the piece names, and end with a report: the files it
    changed, the tests it ran and their results, and anything left undone.
  - **Reviewer** (none or one member) checks every change before the reply
    ends. It can only read.
- **How to set it.** A session's team is set on the session (pick its
  workers and reviewer from your model team, or make a member from a model
  you have connected: a worker gets the Writing code, Running and fixing
  tests and Reading roles, a reviewer the Reviewing role, and a member
  already on that model is reused and keeps everything else it has). A
  folder can keep a team too: a new session in that folder starts with it.
  A session with no workers works alone, exactly as before.
- **What the lead may not do.** The lead is not given any tool that edits a
  file or runs a command (no writing, editing, patching, shell, program or
  page clicking and typing). It keeps reading, searching, the plan, your
  questions, skills and the tools that hand work out. It can hand a piece to
  a worker by name, or ask for a role (code, test or read) and Iron Jarvis
  picks the worker. Only the session's own workers and reviewer can be
  handed work; anyone else, a built-in helper included, is refused.
- **The one exception.** When no worker can take a piece right now (every
  worker is at its usage limit, past the pay-per-use limit, not answering,
  or off this network in a Local only session), the lead gets the coding
  tools for the rest of that reply and must say in its reply that it did the
  piece itself and why. The reply shows "The team lead did this itself:"
  with the reason. A worker whose run failed does not count: the lead hands
  the piece again, or to the other worker. If every worker of the session
  was removed from your model team (or no longer writes code), the session
  still says so: the lead does the work and the reply shows the same line.
  A member that hands work out to other agents itself cannot be a worker.
- **The reviewer, twice at most.** When a reply changed files, Iron Jarvis
  hands the change to the session's reviewer before the reply ends (you do
  not need "Hand work to my team first" for this). If it says changes are
  needed and the lead has a worker fix them, the reviewer reads the fix once
  more. There is never a third review in one reply. The finish check's tests
  are the ones the workers ran, run again once.
- **What you see.** Each reply of a team names the lead, the workers and
  the reviewer, how many pieces went to the workers, and every time the lead
  did a piece itself.
- **Try on several models is different.** Try on gives the whole task to
  each model alone, without the team.
- **Plan mode** is unchanged: the lead only reads and plans there anyway.
- **Measuring it.** A benchmark team file can say `team = true` under
  `[planner]`: the planner then runs as this locked lead, the members with
  the code role are its workers and the member with only the review role is
  its reviewer. The report names that setup "lead (locked)".

## How hands-off Jarvis is (v1.402.0)

The goal: give Jarvis a task, approve what it may do once, and let it work
until it is done. **Autonomy** now has a card, **How hands-off Jarvis is**,
that says how close it gets over the last 30 days, in three numbers:

- **"62% of tasks finished with no help from you."** A task finished with no
  help when its last reply ended cleanly and you never had to step in: no
  approval card or question you answered, no nudge, no repeated request, no
  note typed while it worked, no Continue press.
- **"Asked you 1.4 times per task (0.6 could have been avoided)."** Every
  approval card and every question put to you counts, on every surface: chat,
  coding sessions, agent runs, the night shift. An ask *could have been
  avoided* when you approved the very same call again within one task,
  approved a tool you had already allowed for the chat or run, answered a
  question with "you decide" (or skipped it, or took its first or
  recommended choice). Approving a plan never counts as avoidable: that is
  the one approval you are meant to give. Neither do safety checks (a commit
  or a push, a project's own command lines, a Local only ask, a paid call, a
  browser risk check) or a "no".
- **"You nudged it 0.3 times per finished task."** A nudge is a short message
  that only moves the work on ("continue", "keep going", "try again", "still
  failing", a "yes" to an offer), the same request sent again, a Try again
  or an edited question in chat, or the chat's Continue press on a cut-off
  reply. "Continue the essay with a paragraph about tax" is a new request,
  not a nudge, and a plain "ok" after a reply that offered nothing is a
  thanks, not a nudge.

A quiet line under the numbers says how often Jarvis moved work on **by
itself** (an approved plan that ran without a new message, a run that carried
on past its step budget once, an offer to do more that it simply did). Those
are counted apart and never make a task "not hands-off".

**What a task is.** In a chat or a coding session, each message you type as a
new request starts a task, and everything after it, until your next request,
belongs to it. A run that a schedule, a goal, an objective, a queued task, a
rule, your phone or another computer started is one task by itself; it
finished when its result reads "completed". A run a chat started belongs to
that chat's task, and a coding session's helpers belong to their session.

**How it is counted.** Nothing here asks a model: the numbers come from what
Iron Jarvis already records. Each card or question is noted once when it is
asked and once when it is settled (what you answered, whether you answered in
the app or on your phone, and how long it waited). A card that "Allow for
this run" on another card answered for you is not counted a second time. A
new chat's first question is counted under that chat once it is saved. The
browser sidebar and a terminal's own tool asks are not counted under a task
yet (they keep no saved conversation). Those notes are for the count only. They never ring the bell, pop up on your desktop or go to your
phone, and they never hold the words of a card or your answer. **How this is
counted** on the card says the same in short. When there are more records
than one count reads (thousands of conversations or runs in the window), the
card says it counted only the newest.

**The coding benchmark** reports two of these too. Each benchmark task is one
prompt that nobody nudges, so its pass rate *is* the share of tasks finished
with no help. Because the benchmark answers every card itself, it also replays
each run's tool calls through the real approval rules ("Ask when risky", the
default, every card answered "Allow once") and reports how often a person
sitting there **would have been asked**, for Jarvis, Claude Code and Codex
alike: one card per round for calls of one kind, as the app shows them, and
every question the model asks. A call it cannot map to a
Jarvis tool (an app's tool, for example) is counted as not known, and so is
a call a very long run's record no longer holds.

## Approve once, and the work goes on by itself (v1.402.0)

Three places where you used to have to type "go" or "continue" now carry on
by themselves.

- **An approved plan runs.** In a coding session's plan mode, the plan card's
  button now reads **Allow and run**. Pressing it approves the plan, and as
  soon as the planning reply ends, Iron Jarvis starts carrying the plan out
  in the same session: the same model, account, folder and branch. You don't
  send anything, and the screen follows the work as it starts. **Deny**
  keeps the session planning, as before. Nothing starts by itself if you
  wrote something after approving (a note, a message waiting to go, or a new
  message), if the planning reply failed or was stopped, if you switched the
  session back to plan mode, or for a night task. If the planning reply read
  something flagged as trying to give it instructions, the plan is carried
  out at low trust (its commands need the sandbox), as a night task's next
  turn is.
- **Runs you start get room to finish.** An agent run you start and watch
  (from chat, an objective on the Agents page, or a project task) now gets 60
  steps instead of 12, and so do the helpers a coding session or an objective
  starts. The number is **Settings › Agents & automation › Steps for runs you
  start** (12 to 200). Runs that start on their own (schedules, goals,
  workflows, rules, the phone) and a run started from the Sessions page's New
  session form keep **Max steps per run**. A run whose caller set its own
  number keeps that number.
- **At its limit, such a run carries on once.** When a run you started uses
  all its steps before finishing, it carries on once by itself with the same
  number again. It keeps everything it already did, so no step is repeated.
  If it runs out a second time it stops, and the chat offers **Keep going**
  beside **Try again**. Keep going picks the run up where it stopped, with its
  work kept; Try again starts the task over. One of your own agents that is
  paused, or whose monthly allowance this run has used up, does not carry on:
  it stops at its limit.
- **Document and browser work in chat gets one more round of steps.** A chat
  reply that writes a document or works in your browser used to stop at its
  limit with "what is still left to do". It now gets the same number of steps
  once more, then stops and says what is left, as before. Ordinary chat
  replies are unchanged: they still hand longer work to an agent.

## Asks about what you want, decides the rest (v1.402.0)

Iron Jarvis now asks you fewer questions, and better ones.

- **One rule about asking.** Every coding session (whatever model it runs on,
  planning or not), every agent run, and every chat message that can change
  something is told the same thing: ask you only about what you want or what
  counts as done, never about how to do it. It decides the details itself
  (names, where files go, the tools your project already uses, the order of
  the work), says in its summary what it assumed, and keeps working. When it
  must ask, it asks once, gives the choices with its recommendation, and does
  not ask the same thing again. A folder whose own harness file
  `system_prompt.md` replaces "How you work" replaces this rule with it.
- **"Want me to …?" is done, not asked.** When a coding session's reply, or an
  agent's final answer, ends by offering more work ("Want me to also add
  tests?", "Shall I proceed?", "Let me know if you'd like me to …") and there
  is room left to work, Iron Jarvis tells it once: if that work is part of
  what you asked, or plainly needed to finish it, do it now and then sum up;
  if only you can decide, end with that one question and a recommendation.
  The reply you keep is the first answer, then what came after it.
- **When it does not do this.** Never in a plain chat (the reply ends where the
  model ended it), in plan mode, in a run marked low trust, with fewer than two
  rounds left, or more than once in a reply. A question about what you want
  ("Which colour should the button be?"), a choice between options, an offer
  to explain, and an idea it parks "for later" all end the reply as before.
  After the finish check has run, its answer ends the reply.
- **What stays your decision.** An offer to send, email, post, share,
  publish, commit, push, merge, deploy, open a pull request, pay, buy,
  delete, or remove files or data ("Want me to send this to the client?",
  "Shall I push?") — or to do anything to production — is never acted on:
  the reply keeps its question for you. The message that tells it to carry
  on says the same, so an offer that only points back ("Shall I proceed?")
  is not taken as permission for those either. A helper or teammate an agent
  handed a piece to is never told to carry on (the agent that gave out the
  piece reads its answer), and when the extra work runs out of steps the
  answer it had already finished still stands, with a line saying the extra
  work was cut short. English replies only.
- Each time it happens, the app counts it as one of its own nudges (an
  `autonomy.nudged` event), apart from the times you nudge it yourself.

## Clearer model, effort and pane tabs in a coding session (v1.401.2)

Three things in a coding session's box and panes read more simply.

- **No more "Your Claude plan" row.** It meant "whatever model Claude Code
  uses by default", and never said which. The model menu now lists that model
  once, by its own name: with Claude Code's default being Opus 5.5, there is
  just **Opus 5.5**, and a session set to the plan's default shows "Opus 5.5"
  on its model chip. This holds in a session's menu, the chat's model menu and
  **Try on…**. Lists that keep both choices, such as **Settings › Default
  model**, say "Claude Code's default (Opus 5.5)". Until Iron Jarvis has read Claude
  Code's model list (a moment after Claude Code is signed in), the old words
  stay. Codex does not say which model is its default, so "Your ChatGPT plan"
  stays.
- **Effort lists levels only, and Extra where it exists.** The list is
  **Low**, **Medium**, **High**, and **Extra** for a model that takes it. There
  is no "Default" row. With nothing picked, nothing is sent and the model runs
  at its own level; the chip says "Reasoning", or the level when the vendor
  documents one, and that level is marked in the list. **Extra** is Claude
  Code's own name for its level above High. It appears only for a model
  Claude Code itself says takes it; Codex, the API providers and local models
  do not show it.
- **The pane tabs are icons.** Plan, Timeline, Terminal, Changes, Browser and
  Files are now six small icons above the right-hand pane. Rest the pointer on
  one and its name appears just under it after a moment, and stays while the
  pointer stays; a pointer passing over shows nothing. With the keyboard, the
  arrow keys move between the tabs and the focused tab shows its name. Screen
  readers read each tab's name as before.

## Hand work to your team first (v1.400.0)

Your model team only helps when the planner hands it work. In the first
measured run, a Claude planner was shown the team in 36 runs and never
handed it a piece: nothing told it you wanted to save its usage.

- **The setting.** **Settings › Models › Model team › Hand work to my team
  first**. It is off by default.
- **What it changes, when it is on and you have a team.** A coding
  session's planner is told to:
  - hand reading and summarizing, test runs, first drafts and reviews to
    your team by role;
  - start several helpers at once when their pieces don't depend on each
    other;
  - keep the plan, the hard decisions and the final check itself.

  It names only the roles your members hold. The helper tool's description
  also points to your team first. A planner on a free local model is told the
  team should take its share of the work rather than told to save usage.
- **Iron Jarvis hands the review to your team itself (v1.401.0).** Telling
  the planner was not enough: in the next measured night it handed the team
  nothing in 108 runs. So when the setting is on, Iron Jarvis does one part of
  it on its own. When a coding reply changed files and is about to end, its
  finish check also hands the change to the member of your team with the
  **Review** role:
  - **Who reviews.** The team picks the member the usual way: a local one
    first when its record is good, no account past its limit, your account
    groups, the pay-per-use limit and Local only all apply. With no member
    holding the Review role, nothing changes.
  - **What it gets.** Your request, what the coding model says it did, and
    the change itself (the lines each file gained and lost, shortened when
    it is long). Keys and passwords in it are hidden first, and any part of
    the change that reads like an instruction to a model is taken out. It
    reads the folder's files with its own tools.
  - **It cannot change anything.** The reviewer is given only reading and
    searching tools; a write or a command it tries is refused.
  - **What happens next.** Its report goes into the same finish note the
    coding model already answers, which then fixes what is right and says
    which points did not apply. It is told the reviewer can be wrong.
  - **What you see.** The work line under the reply ends with "reviewed by
    codex: changes needed", "reviewed by codex: approved", "review failed",
    or "review not run" (hover it for why). The reviewer has its own row,
    like any helper.
  - **What it costs.** A review adds time to the reply and uses the model
    you set for the reviewer (a member with no model of its own reviews on
    the session's model). It has a limit of ten minutes: past that it is
    stopped and the reply goes on without it ("review stopped"; hover it for
    why). It is skipped for a reply that changed no files, in a reply that
    read something that looked like an instruction (low trust), and in plan
    mode.
  - **A report that reads like an instruction** is withheld from the coding
    model, and its verdict is not counted.
  - **Turn it off for one folder.** Put `review = false` in
    `.ironjarvis/harness/finish.toml` in that folder.
- **What it does not change.**
  - With the setting off, or with no team, the planner sees exactly what it
    saw before.
  - The team's own rules are unchanged: local first, no account past its
    limit, account groups and the pay-per-use limit.
  - A helper is never told to hand its work on again.
- **From chat**, turning it on always asks first, because it moves work and
  usage onto other models and accounts. Turning it off does not ask.
- **It reaches coding sessions only.** An objective's coordinator does not
  get these words yet.

## Benchmark tasks big enough for a team (v1.400.0)

The team benchmark (`ironjarvis bench-harness`, a developer tool) has a new
group of tasks, the **team** tier. Each task is large and splits into
separate pieces a helper could do from a written brief:

- **team-audit-events:** find every place 75 files write an event.
- **team-port-js:** port five JavaScript string helpers to Python.
- **team-spec-modules:** build three modules from a written spec (durations,
  version ranges, splitting money).
- **team-rename-option:** rename a setting everywhere it appears, keeping
  the old name working with a warning.
- **team-review-patch:** apply a colleague's patch made against an older
  copy. Some parts must be merged by hand, and some edits don't match the
  patch's own description.
- **team-test-triage:** make about 30 failing tests pass, caused by 13 bugs
  in ten packages.

How to use them:

- **Several tiers in one run.** `--tier` takes a list: `--tier frontier,team`
  runs both. The order you type them in doesn't matter, and adding a tier
  doesn't change where earlier results are kept.
- **Team first in a team file.** Add `team_first = true` under `[planner]`
  to run the team with **Hand work to my team first** turned on. The report
  shows it as its own setup, "jarvis-team (team-first)", next to the plain
  team and the planner alone.
- **The team at work.** Each team's answer starts by saying in how many runs
  the planner handed at least one piece to a team member. With team first
  on, it also says in how many runs Iron Jarvis's own finish check handed the
  change to the team's reviewer (v1.401.0). That review is never counted as
  a piece the planner handed.
- **No step limit on the team tasks (v1.400.2).** The other tiers give Jarvis
  60 steps per task. The team tasks give it 1,000, the same ceiling a Build
  reply has, so their 40-minute time limit is what decides. A planner working
  alone no longer runs out of steps on a job a team would split. A task can
  set its own `rounds` in `task.toml`; it can raise the runner's limit, never
  lower it.
- **Answer files in any encoding (v1.400.2).** A task that grades a written
  answer (`answer.json`) now reads it whether it was saved as UTF-8, UTF-8
  with a BOM, or UTF-16, the default for Windows PowerShell 5.1. Before, a
  right answer saved by PowerShell failed as "not valid JSON".

## The chat box stays on one row (v1.400.0)

The controls under the message box stay on one line whatever model you
pick, with Send at the right end. Before, a longer model name together with
the Reasoning chip pushed the right-hand controls onto a second line.

- **When space is short, the row gives way one control at a time, least
  needed first:**
  1. "Web" becomes its globe.
  2. "Auto tools" becomes its wrench. "Tools off" keeps "Off".
  3. "Local only" becomes "Local". It keeps its glow when on.
  4. Only then does the model's name shorten. The end of the name, which
     often names the machine, goes last.
  5. "Ask when risky" becomes its shield. "Ask first" has its own shield,
     and the no-ask level stays amber.
  6. "Reasoning" becomes a small brain icon. A level such as "Medium" stays
     in words.
- **The words are still there.** Hover over any control to see its full
  words; a screen reader reads them too. Typing never makes the row move.
- **The same rules apply in the Coding sessions box.** On a phone, the
  layout is unchanged.

## Night shift: the morning (v1.399.0)

When a night task ends, Iron Jarvis saves its work, and in the morning one
summary tells you how the night went.

- **Each task's work is saved on its own branch.**
  - Iron Jarvis commits it the way the Commit button does, using your task's
    words as the message.
  - It never pushes and never merges. Pushing and pull requests are yours.
  - A task that failed or needs you is saved too, so nothing is lost.
  - Nothing changed: nothing is committed, and the row says so.
  - If the commit can't be made (for example, a hook refused it or took
    longer than 2 minutes), the row says why, and the work stays in the
    branch folder for you.
- **Some files are left out of the night's commit.** They stay in the branch
  folder, uncommitted, and the row names each one with its reason:
  - files that look like secrets (`.env`, private keys, saved passwords) or
    new text that looks like a key or password;
  - files over 50 MB;
  - new files inside dependency or cache folders, like `node_modules`.

  This check knows common secret shapes only. Look at a night branch before
  you push it.
- **One summary per night.** It comes when the night's last task ends, or
  when the window closes, whichever is first.
  - You get one notice: one row in the bell, a desktop notice if no window
    is open, and one phone message if a phone channel is set up. Never one
    per task.
  - A task that ran with **Local only** on appears on the phone only as "A
    Local only task: open Iron Jarvis on this PC to see it". Its words stay
    on this PC.
- **The Last night view.** In Coding sessions, open **Last night** (under
  Tonight).
  - Failed tasks and tasks that need you come first.
  - Each row shows the branch, whether the task finished, whether its tests
    passed (or "not trusted"), the files changed, how long it ran, what it
    used on each account, and what needs you. A change to your main folder
    is shown in words on the row, never folded away.
  - Buttons: **Open**, **Changes**, **Push**, **Open a pull request**, and
    **Throw away** (two presses: the folder goes to the trash, and the
    branch is kept).
  - Older nights are listed below. The last 14 are kept.
- **Your main folder is checked again after the commit**, so a commit hook
  that writes there is caught too. A command that adds a git hook or
  changes your repository's git settings also counts as a change to your
  main folder, because those are shared with every branch.
- **Emergency stop** also stops a commit that is still running. The work is
  then left uncommitted, and the row says so.

## Night shift: the Tonight list (v1.398.0)

Queue coding tasks before bed, and they run overnight, each on its own
branch.

- **Adding a task.** In Coding sessions, open **Tonight** (under New
  session). Add a task: the folder, what to do, and what it runs on. The
  choices are your default model, one model, your model team, or try on 2
  or 3 models. Ctrl+Enter adds it. Queued tasks can be moved up or down,
  edited, cancelled or removed.
  - **Try it on several models** shows the same list as the **Try on…**
    button in a session's box: one row per provider, your own hardware
    first, each marked where it runs, every model marked local or cloud, no
    model listed twice. A provider's models open under its row; the find box
    searches every provider (v1.400.8).
- **When tasks run.**
  - Tasks start only in the night window: 11 PM to 7 AM by default.
  - At most 2 run at once, and never two in the same folder.
  - Each task has 90 minutes and runs on a new branch of its own.
  - A task that hasn't started by 7 AM waits for the next night.
  - Change these in **Settings › Agents & automation › Night shift**.
    Changing them from chat asks first.
- **What a night task may do without asking.** It edits files and runs
  commands in its own branch folder.
  - Anything else waits 15 minutes for your OK, going to your phone at once
    if a phone channel is set up, and is then skipped and listed for you.
    "Anything else" includes the web, a commit or push, your connected apps,
    a project's own command lines, risky clicks, and spending.
  - The task carries on with the rest of its work, and ends as **Needs
    you**.
- **Your main folder is watched.** Commands run as you, so they could reach
  other folders. Before and after each task, Iron Jarvis checks your main
  folder: its commit, branches, changed files and `.venv` packages. A change
  ends the task as **Needs you**, shown on its row.
- **Tests on a branch** use your main folder's `.venv` and `node_modules`.
  Nothing is installed.
  - If a package is installed in editable mode, the branch's own code comes
    first.
  - When that can't be done, the result says the tests are "not trusted".
- **Limits hold.** Pay-per-use stays within your monthly limit. A carry-on
  after a restart or an account reset happens only inside the window, and
  only with the task's own time left.
- **Emergency stop** stops every night task, and everything Iron Jarvis
  started by itself, such as an automatic resume. A coding reply you started
  yourself is never stopped.
- **The PC stays awake** while a night task runs, and from 6 hours before
  the window opens while one is queued.
- **In the morning**, each task's work is already saved on its branch and
  one summary tells you how the night went: see the next section.

## Try one task on several models (v1.397.0)

In a coding session in a git folder, you can try one task on up to three
models at once and keep the best result.

- **How to start it:**
  - Begin or end your message with a sentence naming the models, for example
    "Try this with Claude, Codex and the local model: fix the date parsing in
    `report.py`."
  - Or press **Try on…** in the box and pick one to three models. The list
    shows one row per provider, your own hardware first, each marked
    **local**, **cloud · on your plan**, **cloud · pay per use**, **cloud**,
    or **local + cloud** for a model server that sends some names on to a
    cloud service. Open a provider to tick its models; every model says
    local or cloud. Type to find a model across every provider. The models
    you picked are listed at the top. No model is listed twice: a model
    Claude Code (or Codex) serves for a keyless Anthropic (or OpenAI) is
    listed once, under Claude Code (or Codex), and your own model server is
    one row, named as you named it, without a separate "your model server"
    entry when it lists a model on your network (v1.400.7).
  - It only starts when you ask. Ordinary sentences such as "Try it with a
    smaller input" or "Try this with Claude's API format" stay ordinary
    messages.
- **While they run.** Each attempt works on its own branch, in its own folder
  beside your repository, on its own model and account. Each one shows as a
  row with its steps. An attempt's questions appear on its own row.
- **When they end.** One card shows the attempts side by side:
  - the model and account;
  - finished, failed, stopped, or held by an account limit;
  - tests;
  - files changed;
  - time;
  - usage (list price on your plan, money on pay per use, free on your own
    hardware).
- **Keep and Discard all.**
  - **Keep** puts the chosen attempt's work into your session. If the session
    had work not committed yet, the attempts start from it, and Keep lands
    the result as uncommitted changes, so committing stays yours.
  - **Discard all** (two presses) moves the attempts' folders to the trash.
  - Either way, every attempt's branch is kept. Your main folder is never
    touched.
- **Refused in one sentence before anything starts:**
  - more than three models;
  - a name that isn't one of your models;
  - a cloud model in a Local only session;
  - a model whose provider isn't connected;
  - an account at 95% of its usage window, or outside your account group;
  - pay-per-use over your monthly limit;
  - a folder that isn't a git repository;
  - plan mode.
- **Limits.** Attempts share the session's own time and cost cap, and their
  pay-per-use spend counts toward your monthly limit.
- **Stopping and cleaning up.**
  - **Stop** ends every attempt and everything it started.
  - Deleting the session moves its attempts' folders to the trash.
  - A rewind past a message that tried models asks you to keep one or discard
    them first.
  - After a restart, the attempts read "cut off" and nothing starts again on
    its own. One that finished can still be kept.

## Measuring your model team (v1.396.0)

`ironjarvis bench-harness` (a developer tool, not in the app's menus) can now
benchmark a model team against one model.

- **A team file** names the planner and the members:

  ```toml
  [planner]
  provider = "claude-cli"
  model = "sonnet"

  [[members]]
  name = "glm"
  provider = "custom"
  model = "glm-5.3-flash"
  roles = ["read", "code", "test"]

  [[members]]
  name = "codex"
  provider = "codex-cli"
  model = "gpt-6-astra"
  roles = ["code", "review"]
  ```

  A member may also set `effort` and `max_steps` (1–200). They default to
  `--effort` and the planner's round budget.
- **One command runs the team, then the planner alone**, one after the other,
  so neither uses the usage windows while the other runs:
  `ironjarvis bench-harness run --runner jarvis-team --team team.toml --root
  <scratch> --tier frontier --repeats 3 --effort medium --with-alone`. Run it
  with `--dry-run` first.
- **Checks before the first task.** Every model is checked first:
  - an unknown provider;
  - a CLI that is signed out or not installed;
  - a model server that does not answer, needs a key, or does not list the
    model.
  A problem stops the run in one sentence. What can't be checked in advance
  is listed as a note.
- **What the report shows:**
  - The team beside the planner alone: tasks passed, then Claude list price.
    The verdict comes first, and a team that passes fewer tasks is told so
    plainly.
  - If the planner never handed a piece to a team member, the answer is "No
    answer": the team row was the planner working alone, so it says nothing
    about the team (v1.399.2). If members worked in only some runs, the
    answer says in how many.
  - What else the team used: Codex list price, local tokens.
  - The usage-window readings your accounts sent. "Not comparable" when a
    window reset during the run; "not known" when none came.
  - Who did what, with the router's reasons.
  - Each member's record per role, which is what the router's defaults are
    tuned from.
- **Failures that aren't the team's fault.** A run where a member hit its
  account limit, or wasn't signed in, counts as "did not run" (not a team
  failure), and is tried again the next time the same command runs.
- **Keep `--root` short** (for example `C:\ijbench`). A deep folder makes
  Windows refuse the checks' working folders, so the run refuses it before
  starting.

## Local first, limits watched (v1.395.0)

- **The planner asks for a role, and Iron Jarvis picks the member.** When a
  coding session or an objective hands out a piece of work, the planner can
  ask for "a team member for coding" (or reading, reviewing, testing)
  instead of naming one. The order:
  1. Your local models first, when their record for that role is good or
     not known yet, and their machine isn't busy.
  2. Then your Claude Code and Codex plans, least used first.
  3. Then pay-per-use models.
  4. As a last resort, a busy local machine or a local model with a poor
     record. Work goes to a local model before it waits.
  - A member you name directly still runs, with the rules below.
- **Usage limits.**
  - No new work goes to an account at 80% of any of its usage windows.
  - Nothing new starts on an account at 95%, even for a member you named.
    The refusal says when the account resets.
  - Work already running on an account is never stopped.
  - When every account that could take a piece is near its limit and no
    local model can, the piece waits, and the reply says which account
    resets first.
  - Several pieces started at once are spread across your accounts instead
    of all landing on one.
- **Every pick says why**, on the helper's row, its Timeline row and the
  objective's member card. For example: "local: good record for reading" or
  "Claude Code (Spare): the local box is busy; skipped Claude Code (Work
  Max): 83% of its 5-hour window".
- **Account groups hold.** All the pieces of one piece of work stay in one
  account group: once a piece runs on a work account, the rest never move to
  a personal one.
- **Each member keeps a record per role**, shown in Settings › Models ›
  Model team, for example "Record: reading: good (4 of 5 passed) · coding:
  no record yet".
  - A piece passes when the reply's finish check passed, or a reviewer
    member approved it. A failed check or "changes needed" counts against
    it.
  - A record needs at least 3 pieces, 70% of them passed, to count as good.
    Two failures in a row stop a member getting that role first until it
    passes again.
  - A failure that wasn't the model's fault (its machine down, an account
    limit, a sign-in) is not counted.
  - The record belongs to the member's model: change the model and the
    record starts again.
- **A piece that fails twice on one member** goes one step up, and the row
  says why. It never leaves Local only, and never goes to someone outside
  the objective's team.
- **Pay-per-use limit.** **Settings › Models › Model team › Pay-per-use limit
  for your model team** is the most your team's pay-per-use members may spend
  in a month. It starts at $0, which means never.
  - Work you start yourself is not stopped by it, but what it spends counts.
  - Members on your own machines and on your Claude Code or Codex plan are
    not counted.
- **Queued and scheduled work for a member** now stays on that member's own
  model and account. A queued task that can't start yet (an account at its
  limit, or Local only for a cloud member) waits with the reason; a
  scheduled one is refused with the reason.

## Your model team (v1.394.0)

- **A team of the models you already have.** Give an agent one or more roles
  and it joins your model team. The roles are Reading and summarizing,
  Writing code, Reviewing a change, and Running and fixing tests.
  - Set them on the agent's card on the Agents page, under **Team roles**.
  - See the whole team in **Settings › Models › Model team**.
- **Each member works on its own model**, the one its card names. The model
  you talk to stays the one you picked.
  - When a coding session or an objective hands out a piece of work, the
    planner sees, for each member: its model, where it runs (this PC, your
    network or the cloud), what it costs (free, your plan or pay per use),
    and how much of its plan it has used.
  - Usage comes from numbers Iron Jarvis already has. When it has none for a
    member, it says "Not known yet".
- **Make a starter team.** In Settings › Models › Model team, **Make a
  starter team** shows a preview of the models you have connected. Nothing
  is added until you press **Create**.
  - Your local models come first. They are free, and up to three are ticked.
  - Claude Code and Codex are offered unticked, so you choose whether to add
    them.
  - Pay-per-use models appear only when you tick **Include pay-per-use
    models**.
  - With nothing connected, it says so and adds no one. The demo model is
    never a member.
- **Which account a member uses.** A member's row can name an account of its
  own. Without one:
  - in a coding session, it uses the session's account when both are the
    same kind (Claude Code or Codex); otherwise an account in the session's
    account group;
  - for other work, Iron-Proxy picks one.
  - Work never moves between account groups. A member whose account sits in
    a different group than the work's is refused, and the refusal says why.
- **A member stays on its model.** If its model is not connected, the work
  is refused with a plain sentence. It is never moved to another model.
- **Local only.** In a Local only chat or session, a member whose model
  leaves your network is not listed and cannot be started.
- **A fix for every custom agent.** Handing work to an agent that names its
  own model now runs it on that model, every way it can be started. Before,
  one way ignored the agent's model. Also, an agent that named only a
  provider could be handed another provider's default model.

## Account limits without a press (v1.393.0)

- **A reply at an account's limit carries on by itself.** When a coding
  reply stops because its Claude Code or Codex account reached a limit, it
  now waits for that account's reset and then carries on. You don't have to
  press **Resume when ready**.
  - The card says "Waiting for <account>'s reset at <time>; it carries on by
    itself".
  - **Cancel** stops the wait. **Choose account** moves the reply to another
    account now.
  - To turn this off: **Settings › Agents & automation › Carry on by itself
    after an account's limit**. With it off, nothing carries on by itself, and the card
    waits for your press as before.
- **Account groups.** On the Iron-Proxy card in Settings › Connections, each
  Claude Code and Codex account has a **Group** choice.
  - When an account in a group reaches its limit, another account of the
    **same group and the same kind** carries the reply on at once. The reply
    says so: "Work Max reached its limit; carried on with Spare (same group)".
  - Accounts known to be least used go first. An account Iron Jarvis has no
    numbers for yet is tried only after them.
  - An account in no group never moves, and never takes over.
  - A Claude Code account never takes over from Codex, or the other way round.
  - Put work and personal accounts in different groups. There are no groups
    until you make them, and changing groups from chat always asks first.
- **Usage you can see.** The account chip and the top of the Timeline show
  the account's current usage, for example "63% of the 5-hour window, resets
  3:40 PM". An account Iron Jarvis has no numbers for yet says "usage not
  known yet".

## A long coding reply never goes quiet without telling you (v1.392.0)

- **No progress for 10 minutes.** If a coding reply makes no progress for 10
  minutes, its line says so, and so does the bell (or the tray when no window
  is open). For example: "No progress for 10 minutes — waiting on this step:
  running a command (pytest)".
  - A command that keeps printing is making progress.
  - A reply waiting for your answer is not stalled.
  - A step that its own time limit is about to end is left to that limit.
  - **Stop the step** stops just that step: the reply is told and carries
    on.
  - **Stop** stops the reply.
- **Your phone gets the question.** If a coding session's question waits 5
  minutes unanswered in the app, it goes to your phone, if a phone channel is
  set up. Answering there counts like the card, and allows that one step only.
  These never go to the phone and wait in the app:
  - the cards about git, a project's own commands, risky page clicks, the
    plan, sending to the cloud or spending money;
  - every question from a **Local only** session (a phone channel is a cloud
    service).
- **A very long reply keeps its memory.** When a reply nearly fills what the
  model can read at once, it writes itself a summary of its earlier steps,
  checked against what really happened, instead of quietly dropping them. The
  reply line and the Timeline say "Summarized N earlier steps to fit the
  model".
- **An optional cap per session.** In the session's ⋯ menu you can set a time
  limit (minutes) or a cost limit (list-price dollars). It is off unless you
  set it. At the cap the reply stops and reports its progress, and **Keep
  going** continues. The cost limit counts the reply's own model calls, not
  its helpers'.

## Long coding replies survive sleep and restarts (v1.391.0)

- **The PC stays awake while work runs.** That covers a chat or coding reply,
  an agent run or mission, a workflow, and a terminal whose AI is working.
  - Work that is only waiting for your answer does not keep it awake.
  - The screen can still turn off.
  - While the PC is held awake, the tray menu's first line says "Keeping this
    PC awake while work runs".
  - To turn this off: **Settings › System › Power › Keep this PC awake while
    work runs**.
- **A coding reply carries on after a restart.** If a reply was working when
  Iron Jarvis stopped (an update, a restart or a crash), it carries on by
  itself at the next start, from its last finished step.
  - The reply above says Iron Jarvis stopped there, the next one says it
    carried on, and the bell says so too.
  - A step that was cut off midway is not run again blindly: the model is told
    it was cut off and checks before going on.
  - A reply that was waiting for your answer when Iron Jarvis stopped stays
    waiting, with **Retry**, as before.
  - When a very long reply's record is too big to keep whole, its newest steps
    are kept.
- **A reply a crash cut off keeps its Timeline.**
- **A helper is not offered Continue after a restart.** A helper that was cut
  off with its reply is no longer listed with **Continue** among the jobs a
  restart interrupted. The reply carries on and decides what still needs
  doing.

## See where a coding reply's time and money went: the Timeline (v1.390.0)

A coding session has a **Timeline** tab, right after Plan. It draws a reply as
bars on one time axis.

- **At the top:** how long the reply took, what it cost, the tokens, the model
  calls, the steps and the failed steps. Money from a Claude or ChatGPT plan
  says "list price", as on the receipt. A helper's own use is on its own line
  and is never added to the reply's.
- **The rows:**
  - the model you talk to: one bar, with each of its model calls as a piece
    of it;
  - every step, in the same words as the steps under the reply ("Read
    app.py");
  - Iron Jarvis's own checks (the finish check, a project check), marked as
    checks, not as the model's work;
  - each helper, with its model and its steps.
  Steps that ran at the same time overlap.
- **Press a bar, or tab to it,** for its card: when it started, how long it
  took, its tokens and cost. A step's card also shows what went in and what
  came out; these are kept for the first 60 steps of a reply.
- **Which reply:** a reply that is running draws live (a **Live** badge). A
  picker shows earlier replies. A reply from before this version, or one that
  stopped before any work, says why it has no timeline.

"Worked for" under a coding reply now measures the same length as the
Timeline. For now a helper's own model calls are drawn as one bar.

## Coding sessions: seen everywhere, with a way forward when a reply fails (v1.389.0)

- **The name.** Build's coding-agent view is now called **Coding sessions**,
  and the switch at the top of Build reads **Terminals | Coding sessions**.
  Older notes in this Handbook call it the IJ harness; it is the same view,
  and searching for "IJ harness" still finds it. The button on the meter that
  shows how full the conversation is now reads **Summarize**, the chat's word.
  Typing `/compact` still works.
- **A running reply shows outside Build.** While a coding session works,
  **Running now** lists it by its title, adding "Waiting on you" while it
  needs your answer, and its link opens the session. The status orb says
  **Working** while any chat or coding-session reply runs. The home line and
  Everything › Status count it too. Before, a coding session could work for an
  hour while the rest of the app said nothing was running.
- **A failed reply gives you a way forward.** Under the newest reply that
  failed or was cut off, you get:
  - **Retry**, which sends that question again with its files, pictures,
    skill or command.
  - **Details**, the technical text. It is kept after you reopen the session,
    with keys and sign-in tokens removed.
  - **Sign in**, when Claude Code or Codex is signed out.

  Retry is not offered while a reply runs, on a reply the provider declined
  (Rewind is the way out there), or beside an account-limit card. A coding
  session's failure now says "pick another model for this session", not
  "for this chat".
- **The receipt says what the chat's does.** The approvals you gave, the older
  messages left out to fit the model, and the chat's other receipt lines now
  show under a coding-session reply and in a Build terminal's chat. They are
  still there after you reopen the session.
- **The Plan tab shows the plan.** It said "No plan yet" beside a reply that
  showed an 11-step plan, because it read a copy cut at 300 characters. It now
  shows the running reply's plan, else whichever is newer: the newest reply's
  plan or the plan you approved.

## Closing Iron Jarvis never gets stuck waiting on finished work (v1.388.2)

When Iron Jarvis stops (Quit, or an update's tidy stop), it waits up to five
seconds for jobs started from your phone and for picture and video renders to
finish. If one of them had finished a moment before the stop began, that wait
could get stuck forever, using a whole processor core, and the five-second
limit could not end it. The wait now counts only the jobs that are still
running.

## A terminal link opens the terminal (v1.388.1)

Since v1.385.0, once you had looked at the IJ harness in a window, a link to
one terminal opened the IJ harness instead and hid the terminal: "Open the
Build terminal" after pressing Sign in, the starter model download, the
Microsoft 365 sign-in, and the bell's "a terminal needs you". Every such link
now opens the Terminals view on that terminal.

## Plain words at every door, and help where you are (v1.388.0)

The last gaps from the product-readiness audit.

- **A failed task says what went wrong in plain words, everywhere.** The run
  page, the Schedules row, the bell, the phone, the desktop notice, a
  workflow's step and a goal's stop all say the same sentence, for example
  "Codex couldn't finish this task. Run it again. If it happens again, open
  the run to see the details." The technical text is still on the run page,
  folded under Details. A schedule that could not even start its task says
  so, and never sends you to a run that does not exist.
- **One Sign in button for Claude Code and Codex.** When either is signed
  out, the button is beside it in Settings › Connections, under Rescan,
  above the chat box before you type, and on the Agents page before you
  start. A chat or run that fails for that reason says "Press Sign in beside
  Claude Code in Settings › Connections."
- **Every built-in tool reads in words** on approval cards, in the steps
  under a reply and in the desktop notice. The tool's exact name stays on
  hover.
- **A chat waiting for your OK is found where you are.** If you are on
  another page, the bell lists it and pings once; the chat you are looking
  at never pings, and neither does a reload or a quick hop between pages.
- **Connect an app somewhere else, then carry on.** When a reply offered to
  connect an app and you connect it in the Directory, by signing in, or with
  a key, the chat shows the app as connected with a Continue button.
  Nothing is sent until you press it.
- **The phone is honest about your apps.** A quick reply on the phone does
  not use connected apps. When you ask about one, the note under the reply
  says how to have it used: hand the task to an agent (it asks you on the
  phone before each use), or ask in Chat on your PC.
- **One phone message for a workflow waiting on you**, the one you can
  answer. If that message cannot be sent, the old lines still arrive.
- **Smaller fixes:** the browser sidebar's "not available" note names the
  sidebar, not a menu it does not have; Help's catch-all sends you to
  Everything › Status; Fleet groups are named by machine; a stopped scheduled
  workflow says "stopped before it finished".

## Your own model servers can write long replies (v1.387.2)

Your own model servers (a custom endpoint, Ollama, a Fleet machine) were held
to 4,096 tokens per reply. That is too little for one tool call that writes a
whole source file. When the model reached the limit partway through such a
call, the half call was dropped, and the reply ended with the code pasted into
chat instead of saved.

- **A reply budget that fits the work.** A local model now asks for up to
  32,768 tokens per reply (or the limit its server states, up to 65,536), and
  never more than fits after your conversation in what it can read at once.
  When that size is unknown it never asks for less than before. Hosted
  services (OpenAI, xAI, OpenRouter, Anthropic, Google), the sign-in programs
  (Claude Code, Codex, Grok, OpenCode) and a model name your proxy sends on
  to a cloud service keep exactly their old request. A reply that is not
  streamed (the phone, summaries and other one-off calls) keeps the old 4,096.
- **A long reply takes longer only when it runs long.** A model stops when
  its answer is done. One that runs to the full budget at about 55 tokens a
  second takes about ten minutes; Stop still ends it at once.
- **A server that refuses the budget is asked once more** with the size it
  says fits, and that size is remembered until Iron Jarvis restarts.
- **A reply cut off in the middle of a tool call is picked up again.** Iron
  Jarvis runs nothing from the half call (complete calls in the same reply
  still run), tells the model what happened, and asks it to carry on, writing
  a large file in parts. This happens at most twice in a row and eight times
  in one reply; after that the reply says plainly it was cut off and suggests
  asking for smaller pieces or a model with a larger output limit. The
  receipt under the reply says "Cut off at the output limit, picked up
  again". A long text answer cut off at the limit keeps its Continue button,
  as before.

## The finish check sees files a command wrote (v1.387.1)

The finish check (v1.386.1) only saw changes the agent made with its file
tools. Claude now often writes a file with a shell command instead, and then
the check never ran: both of its remaining benchmark failures were that.

- **The folder is compared before and after.** When a reply ran a command,
  Iron Jarvis compares the session folder as the reply began with the folder
  at the end, so a file a command wrote or changed counts. It takes a few
  hundredths of a second on a few thousand files. Caches, build output and
  anything `.gitignore` ignores never count.
- **Files a program made are listed apart,** with words telling the agent to
  keep a program's log or output that the task may need, and to remove only a
  scratch file it wrote itself.
- **Tests inside a long bash command are found** (a heredoc no longer hides
  them), and a test command is never re-run in a way that could report
  "passed" for failing tests.
- **No false "no file was written" note** after a command wrote one.

## A Build session knows your project and checks your code (v1.387.0)

- **A map of the project from the first message.** Each reply in a Build
  session starts with the folder's layout and the main classes and functions
  in its source files, so the agent does not spend steps listing folders. The
  names are read without running any of your code. The map takes a small,
  fixed share of what the model can read at once, and says so when a big
  project does not fit.
- **Your project's own checkers after an edit.** When the project already has
  ruff, mypy, ESLint or TypeScript set up (both its settings and the program),
  they run on the files the agent changed, and any problem they find goes
  back to the agent. They run your project's own programs, so they ask first
  unless you allowed the command tool for longer. A project without them
  works exactly as before. Iron Jarvis never installs a checker. (Since
  v1.420.0 they run once per reply, at its end; see "A coding reply checks
  your project once, quickly".)
- **Several files in one read** for the generic and Codex model families, so
  fewer rounds are spent reading.
- **Long results are cut honestly.** In a Build session a long command output
  or file keeps more (up to about 32,000 characters), and what is left out is
  said in the middle.
- **A made-up tool name no longer ends the reply.** If a model calls a tool it
  was not given, it is told plainly which tools it has and carries on.

## A Build session on Claude runs its commands in bash (v1.386.2)

Claude was trained in Claude Code, whose command tool is called Bash, so in a
Build session it writes bash commands. On Windows Iron Jarvis ran them in
cmd.exe, which reads them differently: a `;` does not separate commands
there. In a benchmark run the line `rm -f server.port; python server.py & …`
made `rm` delete the project's own `server.py`.

- **Claude's commands now run in Git for Windows' bash**, the way Claude Code
  runs them on Windows. Iron Jarvis finds it where Claude Code does
  (`CLAUDE_CODE_GIT_BASH_PATH`, then Git's own folder) and never uses the WSL
  `bash` in System32. Without Git for Windows the commands still run in cmd,
  and the agent is told so.
- **A program started with `&` keeps running** (a dev server, a watcher)
  until the session ends, and the command returns at once instead of
  waiting for it.
- **Each command starts in the session folder.** Use `cd folder && …` in one
  command when a step needs another folder.
- **A guard wherever cmd.exe still runs a model's command:** a file command
  (`rm`, `mv`, `cp`, `del`, a formatter, an installer, …) followed by `;` is
  refused before it runs, with a sentence telling the model to use `&&`.
- Plain chat, agent runs and the other model families still use cmd, exactly
  as before. The finish check re-runs a bash test command in bash; a folder's
  own checks still run in cmd.

## A Build reply checks its own work before it ends (v1.386.1)

When a Build session's reply changed files, it now takes one short extra
step before its final answer:

- **The tests run again.** The folder's own checks if it has them, else the
  test command the agent itself ran in this reply (only its test part: a
  `rm` or `git push` beside it never runs again). If neither exists, it says
  "no tests found". The command goes through the same approval rules as any
  other: a card where your setting asks, never a command your setting would
  refuse. "Tests passed" means the command's own exit code said so; a run cut
  off by its time limit counts as failed.
- **Files it made that you did not ask for are listed**, so the agent keeps
  or removes each one (a removal can be undone like any other change).
  Ignored files (`.gitignore`) are left out.
- **Your request is put back as a checklist**, including any written
  description your request points to (a docstring, a spec file). The agent
  checks its work against each point and fixes what it missed.

The reply's work line then reads, for example, "Checked: tests passed, 1
scratch file removed" or "Checked: tests failed, then fixed". It costs one
extra round, only for a reply that changed files; plain chat never does it. A
folder can turn it off with `.ironjarvis/harness/finish.toml`
(`enabled = false`). On Codex the extra round continues the same
conversation instead of starting a new one.

Known limits: files changed only through a shell command are not seen as
changes (the check does not run for that reply on their account), and
"tests passed" can describe code from before a later fix if the agent did not
run the tests again.

## Codex no longer thinks your folder is read-only (v1.385.1)

When Iron Jarvis worked through Codex (a chat or a Build session on
codex-cli), the model could answer "The working folder is read-only in this
session, so I couldn't apply the fix" and paste the change for you to make
by hand. The coding benchmark found it: on gpt-6-astra it was every one of
the 26 failed runs. Iron Jarvis starts Codex with a read-only setting on
purpose, so that Codex's own tools cannot act; Iron Jarvis carries out the
model's tools itself. The model is now told that plainly, so it makes the
change itself (still asking you first wherever your approval setting says
to). Nothing about what Codex itself may do has changed.

## Long Build replies finish (v1.385.0)

A Build session's reply used to stop after 60 steps: a two-hour task once
ended with "Nothing is built yet" because it ran out of steps while still
reading. It also stopped if nobody had looked at it for an hour.

- **No step limit unless you set one.** "Steps per Build session reply"
  (Settings) is 0 (no limit) by default: a reply works until it is done or
  you press Stop. A safety stop at 1,000 steps remains. A number you set is
  still honoured.
- **At a limit it says what it did, and offers Keep going.** The reply lists
  what it finished and what comes next. Keep going continues the same task
  in the same session. Nothing continues on its own.
- **Leaving does not stop it.** A Build reply keeps working while you are
  elsewhere in the app, for as long as it needs. It still stops on Stop,
  archive, delete, closing Iron Jarvis, your account's limit, or its step
  limit. A reply waiting for your OK on a card waits until you answer.
- **Coming back shows it at once.** The reply is on screen as you left it,
  your question included, and it carries on from there: no replay, no jump,
  and the page keeps your scroll position. After a reload, "Working for…"
  counts from when the reply really started.
- **The plan stays on the right.** While a reply works, its plan is in the
  right-hand Plan pane on a wide window, and under the reply only when the
  panes sit below the conversation. When a plan appears while you are on
  another pane tab, the Plan tab gets a dot; it never switches tabs for you.
- **The work line counts every step** of a long reply, not just the first 60.

## The coding benchmark can rank harnesses (v1.384.0)

`ironjarvis bench-harness` (a developer tool, not in the app's menus) was too
easy to tell the best coding harnesses apart: Claude Code, Codex and Iron
Jarvis on Codex all scored 30 of 30. This release makes it able to rank them.

- **Each task can run several times.** `--repeats 3` runs every task three
  times (every task's first run before any second run). The report gives each
  setup its average pass rate and its lowest-to-highest range across the
  runs, and lists which tasks passed every time, some of the time or never.
  One run per task reads as before.
- **The record says exactly which model ran.** `--model` is now required,
  and `--effort` (low, medium or high; medium by default) is passed to every
  setup. A run never uses "whatever the settings say". The report also shows
  the model each run said it used, and flags any run that used a different
  one.
- **Twelve harder tasks (`--tier frontier`).** They include long changes
  across many files, a page checked in a real browser, a search through about
  2,000 files, a project that will not build, a half-finished git merge, a
  running server to debug, and traps where the obvious fix is wrong. Each has
  a hidden check and a "plausible but wrong" fix that must fail it. A review
  wrote 54 more such wrong fixes, and every one fails.
- **Every run works alone.** Each run works in its own random folder outside
  the benchmark's folder, so a second run of a task cannot see the first
  run's work. Its prompt and environment do not name where the results are
  kept.

## Fixes in v1.382.0

- **A Connections card's buttons fit the card.** On a connected app's card
  (Settings › Connections), the switch, Test, Change key and Remove wrap onto
  a second line when the card is narrow, instead of "Change key" breaking in
  two and Remove running past the card's edge.
- **The model list always fits the window.** The model menu in chat (and in
  the IJ harness) opens where it fits, is never taller than the window, and
  scrolls its list inside while the search box stays at the top. It stays
  with its button if the page shifts, and on a phone a provider's own list
  opens under its row instead of covering the menu.

- **Your model server's key goes with every request — and only to it.** When
  a model server (for example a LiteLLM gateway) needs a key, Iron Jarvis now
  sends the key you saved for it on every request, including the background
  checks that see whether it is up, what models it has and what each model can
  do. Those checks used to go without the key, and the server refused them.
  The key is only ever sent to the exact server it was saved for (same
  address and port) — never to another server, never along a redirect, and a
  server that repeats it back in an error never gets it shown in chat or the
  logs. Voice no longer sends your model server's key to a separate speech
  server.
- **What a model can do is read from the server.** For a LiteLLM gateway,
  whether a model can look at pictures or use tools, and how much text it
  takes, now come from the gateway's own model list, so a vision model is no
  longer reported as unable to see. Iron Jarvis no longer tries Ollama-only
  addresses on servers that are not Ollama.

- **"Needs to sign in again" no longer sticks.** An Iron-Proxy account you
  had signed in to again could keep saying "Needs to sign in again" in the IJ
  harness model menu and on a Build terminal's account chip — even while it
  worked — because an old note from an earlier sign-in problem was still
  attached to it. The account's current state now decides, as it already did
  on the Connections page.

## Shell lines from a project's or plugin's command always ask (v1.381.0)

A Claude Code command file can contain shell lines (`` !`…` ``). Iron Jarvis
never runs them by itself; the agent runs them with its own terminal. From
now on, when the command comes from the **project** you opened or from a
**plugin**, those lines always ask you first, whatever the session's approval
setting — even "approve for me" or "don't ask":

- **In the turn the command starts** (and when that turn carries on, for
  example after you resume it on another account), every command the agent
  runs shows a card first: "This turn runs the project's /status command,
  which has shell lines, so every command in it asks you first." Helpers it
  starts follow the same rule, and so do the folder's own automatic checks.
- **In later turns** of that session, a command that contains one of that
  command file's shell lines asks again ("This runs “echo hello”, a shell
  line from the project's /status command."). Anything else follows your
  setting. A line the agent rewrites (other wording, a script file) is not
  recognised in a later turn.
- The card offers only **Allow once** and **Deny**, and **Enter does not
  allow it** — click Allow once (Esc declines), so a quick extra Enter after
  sending the command never runs anything.

Your **own** command files (`~/.claude/commands`) keep following the
session's setting, as before. A command with no shell lines asks nothing
extra.

## A new app icon (v1.380.0)

Iron Jarvis has a new icon: the blue-and-white "iU" on a dark rounded tile.
It is used for the desktop and Start-menu shortcuts, the taskbar button, the
tray icon and the installer, each drawn from a size made for it (16 to 256
pixels). After the update installs, Windows may keep showing the old picture
on a shortcut for a while; signing out and back in refreshes it.

## Coding sessions keep going while Claude Code's helpers work (v1.418.0)

When Claude Code hands work to helper agents in the background and its reply
ends ("I'll report as the teams finish"), Iron Jarvis now keeps Claude Code
running until the helpers are done. Before, it stopped Claude Code fifteen
minutes after the last reply, and every helper stopped with it, so the
session looked stuck.

- **You can see it.** A quiet line under the conversation says how many
  helpers are still working. When they finish, Claude Code writes its report
  by itself, and it appears in the session as it is written: you do not have
  to send anything or reopen the session.
- **Running now** lists the session ("helpers working"), and the PC stays
  awake while they run, like any other work.
- **You can still send a message** while helpers work. A change of model,
  effort or tools waits until they finish (a note says so), and Open in
  terminal is refused until then, because both would stop the helpers.
- **It is bounded.** If Claude Code says nothing at all for two hours, the
  session counts as idle again.

Only Claude Code reports its helpers; the other tools' sessions do not show
the line (since v1.441.0 the Session tab lists Codex's background commands).

- **A step never spins after its tool stopped (v1.427.2).** If Claude Code
  (or Iron Jarvis) stops while a step is still running, for example when you
  close or update the app while Claude Code is working on its own, that step
  now reads "did not finish" instead of spinning forever, and one line says
  why. Steps left spinning by an earlier version are fixed the next time
  Iron Jarvis starts. Send a message to carry on.

- **Two accounts can hold the same Claude login (v1.427.3).** Claude Code's
  sign-in uses whichever claude.ai account your browser has open, so signing
  a second account in can quietly give it your first account's login. Two
  such accounts share ONE usage limit, so moving between them never helps.
  Iron Jarvis now reads which Claude account each one is signed in to (only
  two ids from Claude Code's own `.claude.json`; never your e-mail). The
  Iron-Proxy card on Settings › Connections says which accounts share a
  login. **Continue on another account** skips them and says why. To fix one,
  press **Sign in** beside it after signing out of claude.ai in your browser,
  or open the sign-in link in a private window. When none is free but Iron-Proxy
  has an account marked "at its limit" that you know still has usage, the card
  offers **Try <name> anyway**: Iron-Proxy's mark ends and the session moves
  there. A signed-out account is never offered; sign it in first.

- **Chat › Code and chat skip a shared login too (v1.427.4).** The same rule
  now holds everywhere Iron Jarvis picks "the next account": a Chat › Code
  session that moves within an account group at its limit, **Choose
  account** on its limit card (an account with the same login is refused in
  words, and nothing moves), and plain chat when one account runs out.
  Plain chat goes to the next account with its own login, or says why none
  can take over; the account it passed over is never marked as limited. On
  the limit card's list, another account marked "Limit reached" now says
  "Picking it tries it anyway": picking it clears the mark first, for when
  you know it still has usage. (v1.427.5 ships this; it also fixes a rare
  error when turning **Share with Build** on or off.)

## Microsoft 365, locked down for client data (v1.428.0)

Microsoft 365 (Outlook, Calendar, OneDrive) is now set up the safe way for a
firm that holds client information. Connecting it always means:

- **Read and draft only.** Jarvis can read your mail, calendar, contacts and
  OneDrive files, and can save a reply as a **draft** in your mailbox. It can
  never send email and never change your calendar: those tools are not
  installed. You send drafts from Outlook yourself. Microsoft is asked for four
  permissions only: Calendars.Read, Contacts.Read, Files.Read and
  Mail.ReadWrite (the smallest permission that saves a draft).
- **One account.** You type your Microsoft email on the card. Only that
  account can sign in; any other account (your admin account, by mistake) is
  refused and its sign-in deleted.
- **Your firm's own app.** A work or school account needs your firm's app ID
  (the steps are below): the sign-in page shows your firm's app and goes to
  your firm's own directory, and you can switch it off in one place. A
  personal Outlook.com account can leave it empty.
- **A locked copy of the program.** Iron Jarvis installs the Microsoft 365
  program once, from a list of exact versions it ships, with a tamper check on
  every package. It never downloads a newer version by itself. Only one
  package runs an install step: the one that keeps the sign-in's key in
  Windows Credential Manager.
- **Your own models only, and only in Chat.** The mailbox works only in a
  chat on this PC with **Local only** on and one of your own models — never
  from your phone, in an agent's task or in a coding session. Anywhere else it
  says so and does nothing. Once a chat has used it, that chat stays Local
  only, because its replies may quote your mail: the switch says why and cannot
  be turned off, the chat never offers to send a message to a cloud model
  "once", it is never saved to a memory base that is not on this PC, and its
  mail is left behind when the chat hands a task to an agent.
- **It always asks first.** Jarvis asks you before each use of it; "run
  without asking" cannot be turned on for it, and it cannot sign out, switch
  or remove accounts from a chat — that happens only in its Sign in terminal.
- **Its sign-in stays with Iron Jarvis.** The sign-in is kept in Iron
  Jarvis's own folder, encrypted, with its key in Windows Credential Manager —
  not in the program's shared folder another copy of it would read.

**Set up your firm's app (once, about 5 minutes, with your Microsoft 365 admin
account, in your browser — never inside Iron Jarvis):**

1. Open the Microsoft Entra admin center (entra.microsoft.com) and sign in
   with your admin account.
2. Go to **Entra ID › App registrations › New registration**. Name it "Iron
   Jarvis (Outlook)". Choose **Accounts in this organizational directory only
   (single tenant)**. Leave the redirect address empty. Press **Register**.
3. On the app's **Overview** page, copy the **Application (client) ID**.
4. Open **Authentication**. Under **Advanced settings**, set **Allow public
   client flows** to **Yes**, then **Save**. (The sign-in uses a code you type
   on a Microsoft page, which needs this.)
5. Open **API permissions › Add a permission › Microsoft Graph › Delegated
   permissions**. Add Calendars.Read, Contacts.Read, Files.Read,
   Mail.ReadWrite, offline_access, openid and profile (User.Read is already
   there). Press **Grant admin consent** for your firm.
6. Limit who can use it: go to **Entra ID › Enterprise applications**, open
   "Iron Jarvis (Outlook)", then **Properties**. Set **Assignment required?**
   to **Yes** and **Save**. Then open **Users and groups › Add user/group** and
   add only your main account.

**Connect it in Iron Jarvis:** on Settings › Connections, open the Directory
card **Microsoft 365**. Choose **Work or school account**, type your main
email, paste the app ID, and press **Connect**. Then press **Sign in to
Microsoft**: a Build terminal shows a web address and a code. Open the address
in a private browser window (so your admin account is not used by mistake),
type the code, and sign in with your **main** account.

**Good to know:** the first Connect needs Node.js and the internet (it
installs the program, a few seconds). If your firm's Conditional Access
blocks code sign-ins, allow it for this app or the sign-in is refused. To cut
it off, delete the app in Enterprise applications, then press **Disconnect**
on its card. A Microsoft 365 that was set up before this version is not started: remove it
from the Tools page and connect it again from its card. It can only be added
from its card — pasting or importing its program as an extension is refused.
Asking the chat to connect it points you to the card.

## Grok in the Code view (v1.418.0)

If the Grok CLI is installed and signed in on this PC, the Code view offers
Grok beside Claude Code, Codex, Pi and OpenCode. Iron Jarvis asks Grok to
ask before it edits files or runs commands (your own Grok setting to approve
everything is not used here), and never chooses Grok's "always allow" or
"always reject", which would save a rule into your project. Auto edits and
Plan are not offered for Grok. Stop also ends the command Grok was running.

## Skills from your other coding tools work the same here (v1.418.0)

A skill you installed for Claude Code, Codex, Grok, Pi or OpenCode works in
a coding session (Chat › Code) the way it works in that tool:

- **The same skills.** Claude Code's skills folder, the skills of the
  plugins Claude Code really loads (installed and switched on — not one you
  only browsed or turned off), the claude.ai skills of the account Claude
  Code is signed in to, Codex's skills (`CODEX_HOME` respected),
  `~/.agents/skills`, Grok's, Pi's and OpenCode's own folders — and the
  skills the session's folder brings (`.claude/skills`, `.agents/skills`,
  `.grok/skills`, `.opencode/skills`, `.pi/skills`, from the folder up to
  its repository). Skills synced for a different claude.ai account no
  longer appear.
- **The model is told which skills it can use**, up front, the way its
  tool lists them (Claude Code's list, or Codex's with where each SKILL.md
  is), so it no longer has to search for them first.
- **A loaded skill says where it lives.** The model gets "Base directory for
  this skill: …" first, so a skill that ships scripts or reference files
  (`node scripts/run.mjs`) runs them from the skill's own folder. Words
  after the name fill the skill's `$ARGUMENTS` (and `$0`, `$1`, named
  arguments, `${CLAUDE_SKILL_DIR}`), for `/name words` and when the model
  loads it.
- **Their own switches are respected.** `disable-model-invocation: true`
  (Codex: `allow_implicit_invocation: false`) means only your `/name` runs
  it; `user-invocable: false` keeps it out of the `/` menu. A skill's
  `allowed-tools` never grants anything — every action still goes through
  your approval setting — and a skill that is not your own is checked for
  hidden instructions before the model reads it.
- **`/name` sends the whole skill** (up to 100,000 characters; it used to
  stop at 8,000).

## Claude Code's `/` commands, and sessions with no folder (v1.380.0)

**Type `/name` and your words, then press Enter**, in a Build session's box
(IJ harness) — the way Claude Code works. `/pixio-skill draw a cat` runs that
skill with "draw a cat"; a skill on its own (`/research`) is a message too.
The `/` list offers:

- **Your skills**, the ones Claude Code and Codex use on this PC (each row
  says where it comes from: "from Claude Code", "from Codex", "yours", "from
  <plugin>"), and the folder's own.
- **Claude Code's custom commands** — your own (`~/.claude/commands`), the
  project's (`.claude/commands` in the folder) and those of plugins you have
  installed and switched on (never a plugin you only browsed). A command's
  `$ARGUMENTS`, `$0`, `$1` … (`$0` is the first word, as in Claude Code) and
  named arguments are filled from what you type; `@file` lines attach that
  file when it is inside the session's folder. The app never runs a command's
  shell lines by itself: the agent is asked to run them with its own terminal,
  under your usual approval setting. A project's commands come from whatever
  folder you opened, so they are checked for text that tries to steer the
  agent, and you are told if anything was held back.
- **Claude Code's own commands**, where the harness has the same thing:
  `/compact` (words after it say what to keep), `/plan` (with words: plan
  first, then those words), `/rewind`, `/model` (with a name: that model),
  `/clear` (also `/new`: a new session on this folder; this one stays in the
  list), `/resume` (find another session), `/usage` (also `/cost`: what this
  session has used) and `/init` (asks the agent to write a CLAUDE.md for the
  folder).

Enter on a half-typed name picks the highlighted row (an exact name comes
first); Tab puts the name in the box so you can type its words first. A
`/word` that is not a command or skill — a path like `/etc/hosts` — is sent
as ordinary text.

**A session does not need a folder.** On New session the folder is
optional: press **Start with no folder** (or Start with the box empty). The
session keeps its files in a new folder of its own in your chat files folder
(Documents\Iron Jarvis), named by the date and your first words and made
only when you send the first message. The header shows that folder with
**Open**; these sessions sit together under **No folder** in the left pane.
Archiving or deleting the session removes its folder only when it is empty
and no other session uses it — a folder holding files is kept. With no folder
there is no repository, so the branch, commit and pull-request buttons are
not offered. Attaching a folder to a session that started without one is not
possible yet.

## The IJ harness, measured (v1.378.0)

The IJ harness was measured against Claude Code and Codex on the same 30
coding tasks, and walked through three everyday jobs side by side with the
Claude Code desktop app. The numbers and the walkthrough are in
`docs/IJ-HARNESS-PLAN-2.md` (Phase 7) and `docs/IJ-HARNESS-WALKTHROUGH.md`.
Small changes the walkthrough led to:

- **New session** opens on the folder of the session you are looking at, so
  a second session in the same folder no longer means choosing the folder
  again.
- **Changes** opens on the branch view in a session working on its own
  branch (it shows committed and not-yet-committed work alike). A session
  whose branch was merged or removed opens on "This session".
- **After Commit**, the branch line updates before the "Committed" note
  appears, so Push is ready at once.
- **A finished reply's steps** still say what they touched (the file read
  or changed, the program run), as they did while the reply was running.
- **Changed files** are listed by their place inside the folder
  (`web/style.css`), not by their whole path.

## When a model declines a conversation (v1.378.0)

Sometimes a model's own safety check declines to answer. Claude does this
for the whole conversation: once something earlier in a conversation has
been flagged, every new message in that same conversation is declined too —
even "Say hello" — so pressing Retry can never help. Iron Jarvis used to
show that as an empty reply with "Press Retry". Now it says what happened
and where the way out is:

- **In chat:** "Claude Code declined to answer this conversation. Its safety
  check on computer security flagged something in this conversation, so
  every new message here will be declined too. Start a new chat, or edit an
  earlier message to go back before the part it flagged." The reason ("on
  computer security") appears only when the model gives one. There is no
  Retry; a **Start a new chat** button moves what you had typed into a new
  chat. The declined chat is kept, never deleted. Every message you sent has
  **Edit and resend**, which takes the conversation back to before it.
- **In a Build session (IJ harness):** the same sentence ends with "Rewind to
  before the message that set it off, or start a new session", and **Choose
  where to rewind** opens the Rewind list so you pick the message.
- **In a Build terminal's chat:** "Open a new terminal to start a new
  conversation."
- **On your phone:** "Send /new to start a new conversation."
- **An agent run or workflow step** ends as failed with "… declined to do this
  task … Change the task, then run it again" and is not retried
  automatically; an agent that handed the task to a helper is told that
  handing it out again would be declined too.
- **If part of the answer had already appeared**, it stays, marked "The rest
  of this reply was declined", and that reply offers no Continue or
  Regenerate (both would send the same conversation to the same model).

A declined conversation is **never moved to another model or provider on its
own**, not even under Auto: the refusal is about the conversation's content,
and sending that content elsewhere is your decision. It does not count
against the model's health either, and it does not send the "couldn't
answer" alert to your phone or this PC. The same is done for refusals from
OpenAI (including Codex), Google Gemini and a model server that passes
Claude's answers through; where the service does not say whether it judged
the whole conversation, the words say later messages "may" be declined too.

## Your tools in the agent's terminal, and smaller touches (v1.377.0)

**The agent's terminal finds your tools.** In a Build session in the IJ
harness, the commands the agent runs see your normal PATH and home folder,
so `git`, `node`, `npm`, `python` and the rest of your own tools work by
name, the way they do in your own terminal, and a commit carries your own
git name and e-mail. Secrets are still kept out: any variable whose name or
value looks like a key, a token, a password or a sign-in (including a
password inside an address — the address is kept without it) never reaches
the agent's commands, and neither does anything of Iron Jarvis's own. Your
account selectors (for example the cloud profile you work under) and your
SSH agent are kept, so your tools act as the account you chose. Chat,
agents, and scheduled or other unattended runs keep the plain, stripped-down
environment they always had.

On a session's own branch, the agent's commits and pushes still ask you
every time, and so do your own git aliases that would commit or push — an
alias that would force a push is refused like the push itself.

**Smaller touches in the IJ harness:**

- **Next step** — with suggested follow-ups switched on (the same setting as
  in chat, off until you turn it on), up to two suggestions appear after a
  reply; pressing one puts it in the box and never sends it.
- **Branch from here** — on any message, starts a new session with the
  conversation up to that message. In a session on its own branch, the new
  session gets its own branch, starting from the code as it was at that
  message (work not yet committed stays behind, and the header says so).
- **Search sessions** in the left pane, by title or folder (Escape clears
  the search).
- **Keys** — Ctrl+N new session (in the desktop app; a browser tab keeps
  Ctrl+N for a new window), Esc stops a reply that is running, Ctrl+L puts
  the cursor in the box, Ctrl+K opens the model menu. Outside the IJ harness
  Ctrl+K is still the search palette.
- **Rewind to here** puts the message's pictures, `@` files and `/` skill
  back in the box with its words, and says so when part of the work it takes
  back was already committed (the commits stay on the branch).
- Coming back to a session while it is compacting shows "Summarizing earlier
  turns…".
- An account that needs signing in again can be picked in both the model
  menu and the account chip; the next reply then shows the sign-in card.
- **Ask first** now says what it covers: "Asks before it edits files, uses
  the internet, runs commands, or clicks or types in a browser."

## The agent's own browser, and accounts in the model menu (v1.376.0)

**The agent can use your dev server.** In a Build session in the IJ
harness, the agent can open a page in its own invisible browser (your
installed Microsoft Edge, with a throwaway profile — never your own), click,
type, read what the page shows, its console errors and its failed requests,
and take a screenshot. It works the way you would: open the page, find the
error, fix the code, reload, click again and check the result. The
**Browser** panel shows where it is ("The agent is on localhost:5173/cart")
and the picture taken after its last step.

- **Where it may go.** The page in your Browser panel opens without asking.
  Any other address on this PC or your network asks once (then stays allowed
  until that browser closes). Public websites are refused. A page that tries
  to send the agent somewhere else is stopped and brought back.
- **What a page may send.** A page's own requests (a script from a CDN, its
  API calls) go where the page sends them, as in any browser. In a **Local
  only** session nothing the browser does reaches the internet: those requests
  are stopped and listed as stopped.
- **What it never does.** It never types into a password, payment or
  file-upload field, or text that looks like a key; it never answers "OK" to a
  page's confirm box for you; downloads are refused. A click on a button that
  deletes, sends or pays asks you first, every time — even with Don't ask on.
  With **Ask first**, every click and typed entry in its browser asks.
- **Page text is not instructions.** What the agent reads on a page is
  treated as untrusted, like a web page in chat: a page that tries to give it
  orders is withheld, and the session stops acting on pages for the rest of
  the turn.
- **It closes itself** when you press Stop, archive or delete the session,
  after 10 minutes without use, and when Iron Jarvis quits — nothing is left
  running. At most four agent browsers are open at once; a fifth closes the
  one used least recently (its panel says so).

**Pick the account with the model.** The model menu in a Build session now
lists each company once: **Claude (Anthropic)** holds Claude Code and, when
you have an API key, the Anthropic API (pay per use); **OpenAI** holds Codex
and the OpenAI API. Without an API key, the "Anthropic" models were your
Claude Code login under another name, so they are listed once. When
Iron-Proxy holds more than one Claude Code (or Codex) account, each account
is its own entry with its plan and its state — ready, how much is used, near
its limit, at its limit and when it resets — and picking a model under an
account moves the session to that account and that model in one step. An
account that needs signing in again, is turned off or was removed from
Iron-Proxy is shown greyed, with what to do on hover. The chip on the
message box names both ("Opus 4.8 · Work"). Escape closes the menu from
any row. On a phone the menu opens as a sheet along the bottom. The chat
page's model menu is unchanged.

## A branch of its own for each Build session (v1.375.0)

**Your main checkout stays yours.** When you start a Build session in a
folder that is part of a git repository, **Work on a new branch** is on by
default: the session gets its own branch (for example `ij/session-3f2a`)
in its own folder beside the repository, `<repository>.ij-worktrees\…`, so
nothing it does touches the files or the branch you have checked out. Turn
the switch off to work in the folder directly, as before. A folder that is
not in a repository works exactly as before. If git is not installed, or the
repository cannot take a branch (no commits yet, a submodule), New session
says so.

The session's header shows its branch and where it came from
("ij/session-3f2a from main"). The session is listed under the folder you
picked, and it reads that folder's project, rule files and notes.

**The Branch view.** In the **Changes** panel, switch from **This session**
to **Branch** to see everything the branch changed against where it started
— committed and not — and finish it:

- **Commit** — the session's model drafts a message from the changes; edit
  it, then commit (as you, with your git name and e-mail).
- **Push** — to the repository's remote, on the branch's own name. Never a
  force push.
- **Create PR** — when the GitHub CLI (`gh`) is installed and signed in, a
  pull request with its checks shown while you look ("Check again" re-reads
  them). Otherwise the Branch view offers **Open a pull request on GitHub**.
- **Merge into main** (or whatever the base is) — two presses; a normal
  merge, never a rewrite. It is refused, with nothing changed, when the
  merge would conflict, when your checkout of main has changes you have not
  committed, or when that checkout is in the middle of a rebase.
- **Discard** — two presses; the second one says how many files are not
  committed. The worktree folder moves to Iron Jarvis's trash (never
  deleted) and the branch stays, with its commits, until you delete it
  yourself. A folder still in use (a program or terminal open in it) is
  refused with nothing moved. After a merge, **Remove the worktree** does
  the same.

**The agent and git.** On a branch, a commit or a push the agent runs in its
terminal always asks you first — Allow once or Deny — even with
auto-approve on, and so does git that would throw away work (a hard reset,
a clean, discarding changes) or change the repository's settings. Some
things the agent is never allowed to do on a branch, and it is told so
without asking you: a force push, deleting a branch (here or on the remote),
rewriting history (amend, rebase, a reset to another commit) and removing
worktrees. If you want one of those, do it yourself. (This reads the
commands the agent writes; it is a guardrail against a careless model, not a
sandbox.)

## Watch the work in the IJ harness (v1.374.0)

**The task list sits under the reply.** When a Build session plans its work
(the model's own to-do list), the steps show under the reply while it
works and tick off as they finish. When the turn ends they fold into one
line, for example "Plan · 1 of 3 done"; press it to see the list as the turn
left it. A step the turn did not reach reads "not started" — the plan carries
on with your next message. A helper agent that plans shows its plan in its
own row. The side **Plan** panel is unchanged.

**How full the conversation is.** A small ring and a percent beside the model
name show how much of what the model can read at once this conversation
fills. For a model whose own limit Iron Jarvis does not know, the number is
measured against the safe size Iron Jarvis sends it, and the ring's tooltip
says so. Past about 70% the ring turns amber and a **Compact** button
appears (you can also type `/compact`).

**Compact** asks the session's own model, on the session's own account, to
write a summary of the earlier turns — never another model, and in a Local
only session nothing leaves your network. Every file the summary names must be one those turns really
read or changed, and every quote must appear word for word; anything else is
removed before the summary is kept, and a summary is kept only when it is
shorter than what it replaces. From then on the model reads the summary in
place of those turns; **the conversation you see is never cut.** The box
says "Earlier turns summarized" — press it (or ⋯ → Read the summary of
earlier turns) to read exactly what the model now reads. The summary is
billed to the session like any other turn. If nothing could be summarized
(not enough conversation yet, the demo model, an account at its limit), the
box says why in a sentence and nothing changes. **Rewind to here** to a
message before the summary's turns drops the summary.

**A notice when a long turn ends.** A Build turn that took longer than a
minute tells you when it finishes (or could not finish): one bell row —
"Your Build session finished", its title and how long it took — and one
desktop notice that opens the session. You get nothing if you were looking
at that session when it ended, nothing for a turn you stopped yourself, and
never two notices for one turn (the same rules as every other notice: one
window speaks for the app, and the tray says it when no window is open).

## Type `/` or `@` in the IJ harness (v1.373.0)

**`/` opens a list** above the message box in a Build session:

- **/plan** switches the session to planning (or back, when it is planning
  already).
- **/rewind** lists your earlier messages, newest first, with how many files
  each would put back; pick one and press **Rewind to here** twice, as on the
  message itself.
- **/model** opens the model menu.
- Then the skills: the project folder's own skills (marked "this folder"),
  then your skill library. A picked skill shows as a chip in the box and is
  used for that one message only.

**`@` lists the files in the session's folder** as you type (letters in
order are enough: `@dpn` finds `deploy-notes.md`). A picked file shows as a
chip and the session reads it with your message, the way an attached file
is read. Only files inside the session's folder are offered or accepted.

- Type to filter, ↑ ↓ to move, Enter or Tab to pick, Esc to close the list
  without sending (your words stay in the box).
- One message reads at most **4 files** in all (attached and picked with
  `@`); the box says so before you send.
- Ctrl+Enter (queue a message while a reply runs) keeps the chips with the
  message. If a message cannot start (say its file was deleted), its words
  and chips come back into the box.
- **Rewind to here** puts the message's words back in the box, not its
  chips or attached files; pick them again if you need them.

**Easier to read.** A Build session's conversation now sits in one centred
column with its message box, in larger type with more room between lines,
paragraphs and list items — the narrow chat inside a Build terminal keeps its
compact look. The whole app now uses the **Inter** font, shipped with the
app (it was always the intended font, but Windows showed Segoe UI instead).

## Helper agents and skills in the IJ harness (v1.372.0)

**A session can split the work.** Ask a Build session in the IJ harness for
something that has separate parts, for example "find every place we read the
config and list the ones that ignore my setting", and it can start helper
agents that work at the same time (Claude calls them agents, Codex calls them
spawned agents). Each helper is one row in the conversation: who it is, what
it was asked, and how many steps it has taken. Open a row to see its steps as
they happen; when it finishes, the row shows its report and an **Open run**
link. The session then answers with what the helpers found.

- At most **3 helpers** work at once in a session. A fourth waits its turn
  and its row says so.
- Helpers use **the session's own model and account**, never another one,
  and work in the session's folder with its approval setting, Local only
  setting and the same tools the session has (never more).
- Starting a helper does not ask you. What a helper **does** still asks
  exactly as the session would: if it wants to run a command, its approval
  card appears on its row, and you answer it there. With **Ask before every
  action**, a helper also asks before it changes a file or opens a web page.
  The bell and the desktop notice take you to that session.
- **Stop** stops every helper with the session. Archiving or deleting a
  session stops them too.
- A custom agent set to a different model, or an agent on another computer,
  is not used as a helper; the session is told why.
- While planning (**Plan first**) the session starts no helpers, because a
  helper can change files.

**Skills.** A session can look through your skill library and load a skill
when the task matches, and it can load a skill kept in the project folder
(`.ironjarvis/harness/skills/<name>`). A skill that came from another program
or that an agent wrote is checked for hidden instructions before the model
reads it; your own skills are read as you wrote them.

**Fixed at the same time:** a Build session that was not using an Iron-Proxy
account (for example on this PC's own Claude or Codex login, a local model,
or an API key) could be moved to your default model when its own model
failed, and a picture could be sent to a separate vision model. Now a Build
session and its helpers always stay on the session's model: if that model
fails, you see its error instead of an answer from another one. That covers
everything the session does along the way too: reading a picture or a
scanned PDF, summarising a long helper run, and planning a big task in
steps. If the session's model cannot look at pictures, the session says so
instead of guessing what a picture shows. (Choosing **Auto** for a session
still lets Iron Jarvis pick.)

## Pictures and your dev server in the IJ harness (v1.371.0)

**Send a picture with your message.** In Build's IJ harness view, press the
paperclip under the message box, paste a picture straight into the box
(Ctrl+V), or drag one onto it. Each picture shows as a small thumbnail you
can remove with ×. A picture on its own is a message too. If you press Enter
while a picture is still uploading, Iron Jarvis waits for it and sends both
together. The picture stays in the conversation as a thumbnail.

**The agent can look at your dev server.** When the Browser pane shows a
page on this PC or your network (for example `http://localhost:5173`), the
agent knows which page you are looking at and can take a screenshot of it to
see how it looks, for example after it changes your code. It does this in a
private, invisible browser window (Edge or Chrome) with none of your logins
or extensions, and it never opens public websites this way.

- Looking at the page your Browser pane shows needs no approval: you opened
  it.
- Looking at any **other** page on this PC or your network always asks you
  first, whatever your approval setting. A screenshot goes to the model
  answering the session, and a local page can be another program's (an admin
  page, a page with client names on it).
- In a session kept on this PC (**Local only**), every screenshot asks first,
  your Browser pane's page included: a page can load things from the
  internet.
- Claude and Codex see the screenshot itself. A model that cannot take
  pictures stays on the session (it is never swapped for one that can) and is
  told where the screenshot was saved instead.
- The invisible browser keeps its files, and anything a page tries to
  download, in a temporary folder that is deleted afterwards, and pressing
  Stop closes it at once.

**Fixed at the same time:** a Codex coding session that had a picture in it
(attached, or a screenshot) was quietly answered by Claude instead, because
Iron Jarvis thought Codex could not take pictures. Codex now answers it
itself.

## How the coding harness measured up (v1.370.4)

On 2026-10-22 the coding benchmark (`ironjarvis bench-harness`) ran all 30
of its tasks on five setups: Claude Code and Codex on their own, Jarvis's IJ
harness on the same Claude and Codex plans, and Jarvis on your own model
server. Every run counted; none was left out for a limit or an unreachable
server.

| Setup | Tasks passed | Average time per task |
|---|---|---|
| Claude Code | 30 of 30 | 62 s |
| Jarvis on Claude | 30 of 30 | 31 s |
| Codex | 30 of 30 | 38 s |
| Jarvis on Codex | 29 of 30 | 44 s |
| Jarvis on your local model (glm-5.3-flash) | 27 of 30 | 23 s |

On Claude, Jarvis passed every task in about half the time and read about
60% fewer tokens than Claude Code. On Codex, Jarvis missed one hard task that
Codex passed. Your local model missed three of the ten hard tasks and costs
nothing per task. Thirty tasks is still a small sample: treat one task's
difference as a hint, not a verdict.

## Updates arrive sooner (v1.370.3)

A new version now reaches your app in about 15 to 20 minutes after it is
finished, instead of about 80. The checks that run before every release are
the same; they just run side by side now instead of one after another. The
full test suite is split across six machines at once, and the installer is
built while the tests run.

Nothing about how updates reach you has changed:

- An update is published only after **every** test has passed. If any test
  fails, nothing is published and your app keeps the version it has.
- Your app still downloads the update by itself and installs it only when you
  press **Restart to update**.
- A version that is already published is never replaced with a different
  build. If two release runs finish for the same version, the second one leaves
  the first one's files alone.

## Build's view switch stays put (v1.370.2)

The **Terminals | IJ harness** switch at the top of Build now sits in the
middle of the header and stays in the same place in both views. Before, it
moved: the Terminals view's Shell and New terminal controls pushed it left,
and in the IJ harness view it slid to the right edge. On a narrow window the
switch gets its own row, centred under the title.

## Picking a folder for a coding session works again (v1.370.1)

In Build's IJ harness view, **New session** → **Choose a folder** now opens
folders as you click them, all the way down to your project. Before, clicking
a folder started a session in it straight away: clicking `Users` said the
folder could not be used, and clicking your own user folder started a session
there instead of letting you go further. Click folders to open them, then
press **Start**.

The view also keeps the conversation readable on a smaller window. Below about
1280 px wide, the session list, the conversation and the panes stack. Before,
at 1024 px the conversation was squeezed to a sliver between them.

## Coding sessions and accounts

Coding sessions (called the IJ harness before v1.389.0) are Iron Jarvis
working as a coding agent in one of your
project folders, on whichever model you choose — the way Claude Code or Codex
work, but inside Iron Jarvis, with its approvals, Undo and activity log. It
lives in Build, beside your terminals. It was built in steps (v1.366.0 to
v1.370.0); this section is the whole picture in one place.

**A coding session**

- A coding session belongs to a project folder. It keeps its own
  conversation, model, approval setting and account, and it never shows up
  in your chat list, chat search or memory recall.
- A reply keeps going when you look away: close the window, reload or lose
  the connection and the session finishes its work. Stop ends it. A restart
  in the middle of a reply leaves one line saying it was cut off.
- **Plan first, if you like.** In plan mode the model can only read and plan.
  When the plan is ready it asks you once; **Allow and run** keeps the plan
  and the session starts doing the work at once, with no message from you
  (v1.402.0); Deny keeps it planning. No approval setting answers that
  question for you.
- The model can stop and ask you a question with choices, and waits for your
  answer.
- Deleting or archiving a session stops the programs it started.

**The harness: the same rules, each model's own tools**

- Every model gets the coding tools Claude Code and Codex give theirs:
  reading parts of files, exact edits, patches across files, search,
  programs that keep running, a step list. Each model sees them in the shape
  it was trained on (Claude sees Claude Code's tools, GPT sees Codex's, Qwen
  sees Qwen Code's, others see Iron Jarvis's own). Every call is translated
  to the real Iron Jarvis tool before it runs, so approvals, Undo, the
  activity log and your limits always apply.
- Four rules come first in every session and can never be removed: work
  inside the session's folder; never stop a program the session did not
  start; never end programs by name; only rewrite git history when asked.
- **A folder can add its own harness files** in `.ironjarvis/harness/`:
  `system_prompt.md` (how to work), `guardrails.md` (more rules — added after
  the four), `memory.md` (notes), `tool_guidance.md` (tool advice), `skills/`,
  `sub_agents/`, `scripts/` and `plugins.toml` (make a tool ask first or
  refuse it, and checks that run after an edit). A file the folder does not
  have keeps the built-in version. Everything is checked before it is used;
  a folder setup that is invalid or reads like an attempt to steer the model
  is ignored as a whole, with a note on the reply.

**Accounts**

- Each session runs on ONE account (a Claude Code or Codex account from
  Iron-Proxy; a new session starts on the one marked Default). It never moves
  to another account on its own.
- When that account reaches its limit, the reply stops after its last
  finished step, the files it changed stay changed, and the session shows
  when the account resets. **Resume when ready** waits for the reset and
  carries on by itself (even after a restart); **Choose account** carries on
  now on another account with the same conversation.
- A model on your own model server, or an API key, needs no account: the
  session names the place instead.

**Rewind (v1.370.0)**

- You can rewind a session to before any of its replies: point at a message
  you sent and press **Rewind to here**. The first press only shows what
  would happen; the second rewinds. The files its
  replies changed from that point on go back to how they were, newest change
  first, through the same Undo the activity log uses — files the session
  created are removed, files it changed get their earlier content back. The
  conversation is cut back to before your question, and the question goes
  back in your message box, so you can ask again differently. Rewinding to
  an earlier reply rewinds every later one too, and a suggestion from the
  library (below) that came from a rewound reply goes with it.
- **All or nothing.** If a file changed after the session changed it (you
  edited it yourself, say), or the saved copy of a file is gone, the rewind
  names the file and nothing is touched — neither the files nor the
  conversation. Before anything happens you can see the list of files that
  would go back.
- What a rewind does not undo, and says so: commands the session ran (their
  effects stay), programs it started (they keep running), and the money or
  usage already spent. A plan you approved in a rewound reply is taken back,
  and the session is in plan mode again.
- A session that is still answering is not rewound: stop it first.

**The experience library (v1.370.0)**

- Once a session has been idle for 10 minutes after replies that changed
  files or ran commands (or when you archive it), Iron Jarvis may suggest ONE
  short lesson for that folder — for example how the project's tests are
  run, or which file holds what — and sometimes a small edit to one of the
  folder's harness files (usually `memory.md`). That is ONE extra request
  for all the replies since the last one, never one per reply, sent to the
  session's own model on the session's own account — on a Claude Code or
  Codex plan it counts toward that plan's limits. The request is cut to a
  fixed size and has a time limit. It waits while the account is at its
  limit or Iron-Proxy says it has used 70% or more of a window, and a reply
  that read something flagged as an injection is never learned from. Turn
  it off with the **Learn from Build sessions** setting.
- **Checked against the record.** A file a lesson names must be one this
  session's own work touched or named, and text it quotes must be exactly
  what was asked, said or shown in that reply. A line that is not supported
  is removed; a lesson with nothing left, or nothing it can show, is not
  suggested. The demo model never writes a lesson.
- A suggestion shows as a card in the session, with **Accept** and
  **Decline**, and the bell tells you when one is made.
- **Nothing is kept until you accept it.** An accepted lesson is read by
  every later session in that folder, as its own short section of the
  harness. An accepted edit is written into the folder's file at that moment
  — if the file changed since the edit was suggested, or the folder's
  harness files would not be valid with it, nothing is written and you are
  told why. You can forget a kept lesson at any time. Declining one means the
  same suggestion is not made again for 14 days, and at most three
  suggestions wait in a folder at once.
- Lessons are kept in Iron Jarvis's own data, never in your folder — only an
  edit you accept writes into the folder.

**Improving the harness itself (developer tool, v1.370.0)**

- `ironjarvis bench-harness run --refine --k 2 --mode parallel` (or
  `--mode sequential`) improves the harness's own files with the benchmark.
  It runs the current harness on the chosen tasks, writes what happened as
  files (each task, what the agent did step by step, and whether the hidden
  check passed — never the check itself), and asks a model, pointed at those
  files, to propose harness file changes. Each proposal is checked like a
  folder's harness files (a bad one gets one more try, then is dropped), and
  tried on the same tasks with the same settings. The proposals that did at
  least as well are combined, the combination is tried too, and it is kept
  only if it scores at least as well as the current harness on the hidden
  checks.
- Parallel makes k proposals side by side from one look at the evidence
  (none sees another); sequential makes k in turn, each starting from the
  best so far. Both make and try one proposal at a time on this PC —
  "parallel" is about where the proposals start, not about running at once.
- What the model proposing reads never carries the hidden checks: a failed
  answer file says only that the answer is wrong (never which part, which
  would give the answer away), any line of what an agent did that carries a
  hidden check's text is removed, and the whole folder of evidence is checked
  for the grader before the model may read it.
- Each harness runs each task once, so the report says plainly that its
  numbers are a small sample: a difference of one task can be chance.
- A kept result is written as a proposed set of harness files in the
  benchmark's own folder. Nothing is applied to any project:
  `ironjarvis bench-harness adopt --from <that folder> --folder <project>`
  copies it into a project's `.ironjarvis/harness/`, and never overwrites a
  file there that is different. It refuses a folder that does not exist, and
  a harness folder or file there that is a link leading somewhere else.
- Every model call of the loop is counted in a ledger by stage, and the totals
  must add up; a run whose check could not run is left out of every
  comparison, never counted as a pass or a fail.

**The benchmark on this harness (2026-10-22, this PC)**

- **The harness as it ships in v1.370.0**, on your DGX Spark's
  `glm-5.3-flash` with Iron Jarvis's own tool names (all 30 tasks, one run
  each): **28 of 30** (93%) — 19 of 20 core tasks and 9 of 10 hard ones,
  none left unscored, at no cost. On average a task took 10.9 tool calls and
  34 seconds; the whole run read 2,164,692 tokens and wrote 49,397. The two
  it missed were `bugfix-csv-quoting` and `hard-idempotent-migration`. The
  same model, measured the same way when the tool dialects were chosen
  (v1.366.0), had also passed 28 of 30 but missed two other tasks
  (`hard-expression-evaluator` and `hard-scheduler-ordering`, both passed
  now) — one run per task, so a single task going either way can be chance.
- **The first improvement loop** (`--refine --k 2 --mode parallel` on three
  core tasks: `bugfix-csv-quoting`, `bugfix-pagination`, `feature-slugify`):
  both proposals were valid on the first try — one rewrote the tool guidance,
  the other "How you work" (both say, in short: make every change with a
  tool call, and never say tests pass without having run them). Each passed
  all three tasks, where the current harness had passed two; their
  combination passed all three as well and was kept as a proposed set of
  harness files. With one run per task this is a small sample, so it is a
  proposal for you to try, not a proven gain. The loop's ledger: 2 proposals
  and 9 task runs (the current harness's 3 runs were reused), 11 runs in all,
  66 model calls, 327,865 tokens in and 5,584 out, at no cost — inside the
  planned limit of 4 proposals and 12 task runs, with every stage adding up.

## The coding screen in Build (v1.369.0)

Step six of the coding screen: you can use it now. Open **Build** and choose
**IJ harness** at the top, beside **Terminals**.

- **Sessions on the left.** Your coding sessions are listed by folder, newest
  first, with a dot on one that is working or needs you. **New session**
  starts one in a folder you type or pick, and the **+** beside a folder
  starts another there. Rename a folder's group to give it a short name (for
  a project's folder, that renames the project). Below them, **Terminals**
  lists your open terminals with their state: press one to open it in the
  Terminals view, and press **IJ harness** to come back.
- **The conversation in the middle**, drawn like a chat reply: what it did,
  the reply, the files it changed with Undo, and who answered. A question it
  asks you, an approval, or a plan to approve takes the place of the message
  box, as in Chat. **Plan first** in the message box makes the session read
  and plan before it changes anything.
- **Five tabs on the right.** **Plan** shows its step list. **Terminal**
  opens a normal Build terminal in the session's folder. **Changes** lists
  every file the session changed, with Undo. **Browser** previews a page the
  session started on this PC or your own network. **Files** shows the folder.
- **Keep typing while it works.** Enter adds a note it reads at its next
  step, and Ctrl+Enter queues a message for after. A note it never got to
  goes back in your message box.
- **The account it runs on.** A session on a Claude Code or Codex account
  shows that account at the top, with its plan and how much of it is used,
  and you can switch accounts or make one the default there. When the account
  reaches its limit, a card says so. **Resume when ready** waits for the
  reset and carries on by itself (**Cancel** stops the wait), and **Choose
  account** moves the session to another account. Your conversation and work
  are kept either way. A new session starts on the account your list marks
  Default even when that account is at its limit, so its first reply can be
  that card.
- **The free-form terminal canvas is gone.** If you last used it, Build now
  opens on its terminals, every one of them still there.
- **Links open the session.** The bell, the tray and Activity open a coding
  session in Build, never in Chat — also when Build is already open on
  another session or on the Terminals view.
- **An Undo reads as undone.** After an Undo that worked, the changes line —
  here and under a chat reply — says "undone", never "changed since". It
  still says "changed since" when the file changed again after the Undo.
- **On a phone** the conversation and its message box are one screen-high
  box (the conversation scrolls inside it); the five tabs come after it.

## Each coding session runs on the account you picked (v1.368.0)

Step five of the coding screen: accounts. It needs Iron-Proxy turned on
(Connections → Iron-Proxy) with your Claude Code or Codex accounts in it.
Since v1.369.0, Build's IJ harness view shows it.

- **One account per session.** A new coding session starts on the account
  your list marks **Default**. You can pick another one for that session.
  The session only ever runs on that account: it never moves to another
  account on its own, not even when this one hits its limit.
- **Plan and usage come from the vendor.** Each account shows its plan
  (for example Max or Plus) and how much of its limit is used, as the vendor
  reports it. If the vendor sends no number, there is no number; nothing is
  estimated. A Codex account's numbers appear after its first use.
- **At a limit, the session pauses.** It stops after the last finished step,
  and the files it already changed stay changed. It tells you when the
  account resets. Then you choose:
  - **Resume when ready:** Iron Jarvis waits for the reset and carries on by
    itself, even after a restart. You can cancel the wait.
  - **Choose account:** pick another account and carry on with the same
    conversation. Choosing a Codex account switches the session to Codex's
    models.
- **Nothing runs on an account you did not pick.** If Iron-Proxy is off,
  out of date or not answering, the session says so instead of running on
  this PC's own sign-in.
- **The accounts' own numbers stay accurate.** After each reply, Iron Jarvis
  reports the vendor's usage headers to Iron-Proxy, and nothing else.

## Coding sessions keep going without you (v1.367.0)

Step four of the coding screen: the sessions it shows (Build's IJ harness
view, since v1.369.0). Nothing new appears in Chat.

- **A coding session belongs to a folder.** It keeps its own conversation,
  its model and its approval setting. It never shows up in your chat list,
  memory recall or chat search.
- **A reply keeps running when you look away.** Close the window, reload, or
  lose the connection, and the session finishes its work. Coming back shows
  where it is. A restart in the middle of a reply leaves one line saying the
  reply was cut off. Sending the same message twice never runs the work
  twice.
- **Plan first, if you want.** In plan mode the model can only read and
  plan. When it is ready it shows the plan and asks once. Allow switches the
  session to doing the work and keeps the plan; Deny keeps it planning. No
  approval setting answers that question for you.
- **The model can ask you a question.** In a coding session it can stop and
  ask, with choices. It waits until you answer or stop it.
- **Deleting or archiving stops what it started.** A session still answering
  can't be deleted from either place; stop it first.

## The coding harness speaks each model's language (v1.366.0)

Step three of the coding screen: the engine it runs on. Nothing new
appears in Chat. Build's IJ harness view uses it since v1.369.0.

- **One harness, many models.** For a coding session Jarvis gives the model
  its coding tools (reading, exact edits, patches, search, programs that keep
  running, a step list) and a short set of working rules. A model sees the
  tools in the shape it knows best:
  - Claude sees Claude Code's tools;
  - GPT sees Codex's tools;
  - Qwen sees Qwen Code's tools;
  - others see Jarvis's own.

  Every call is translated to the real Jarvis tool before it runs. Approvals,
  Undo, the activity log and your limits always apply. The log shows both
  the real tool and the name the model used.
- **Measured, not assumed.** GLM's maker recommends Claude Code's tools. On
  the benchmark, GLM passed the same tasks with Jarvis's own tools while
  using about 40% fewer steps and about 73% less input, so GLM uses Jarvis's
  own tools. The choice and its numbers are written into the code.
- **Rules a model can't argue away.** Every coding session starts with four
  rules:
  - work inside the session's folder;
  - never stop a program the session didn't start;
  - never end programs by name;
  - only rewrite git history when asked.

  A project folder can add its own rules and tools in `.ironjarvis/harness/`,
  but it can't remove these four. An invalid or suspicious folder setup is
  ignored as a whole, with a note.
- **A call written as text is not run.** If a model writes a tool call as
  plain text instead of making it, Jarvis asks it once to make the real call
  and never runs the text.

## Removing your own model server really removes it (v1.365.3)

If you had renamed your own model server (for example to "DGX Spark"),
Remove on the Connections page cleared its address but left a stored copy
behind. The old server stayed in the app with its old address, and you could
not remove it. Now a renamed server lives only while its address is set:
Remove takes it away, and a new address you type always replaces the old
one. If you set the address again later, your name for it comes back.

## Codex can use Jarvis's tools (v1.365.0)

When a chat or a task runs on Codex (your ChatGPT plan), Iron Jarvis used to
send the conversation as one block of text, so Codex could only answer in
words and never use a tool. Jarvis now talks to Codex the way the Codex app
does, through Codex's own app server.

- **Codex asks, Jarvis does.** Codex asks Jarvis to run a tool, and Jarvis
  runs it through the same approvals, Undo, activity log and limits as every
  other model. Codex's own shell, file editing, web search and add-ons are
  switched off, so nothing runs behind Jarvis's back.
- **One conversation, one Codex process.** A chat with several tool steps
  stays in one Codex session. The session is closed when the chat finishes,
  after 10 idle minutes, or when Iron Jarvis quits. At most six run at once.
- **Usage is shown as plan value.** The reply's details show what Codex used,
  at its list-price value. Goal summaries say "ChatGPT plan" for this work.
- **If your Codex is too old or different.** Jarvis checks first, without
  sending a message. If Codex can't work this way, Jarvis falls back to the
  old text-only way and says so once.
- **A model can now take over for Codex, and Codex for others.** Because
  Codex can use tools now, it can step in when another cloud model is briefly
  unavailable, and the reply says so ("instead of …"). A chat kept on this PC
  (Local only) never moves to Codex.

## The coding tools Claude Code and Codex have (v1.364.0)

Step two of the coding harness: Jarvis now has the tools that Claude Code and
Codex give their models. A table records every tool those two programs offer,
next to the Jarvis tool that does the same job or a plain reason it is not
needed. Nothing new appears in Chat yet. The coding screen will switch these
tools on when it arrives.

- **Read part of a file.** Reading can start at a given line, stop after a
  number of lines, and show line numbers. It always says when it showed only
  part of the file.
- **Exact edits.** An edit now changes text only where it appears exactly
  once. If the text is missing or appears more than once, nothing changes,
  and the reply says why (or "replace all" changes every match). Edits keep
  a file's line endings as they were.
- **Several changes at once.** One request can make several edits to a file,
  or apply a patch (Codex's patch format) across several files. Either every
  change lands or none does, and Undo puts every file back.
- **Find files and search.** Files can be found by pattern, newest first.
  Search can show lines around each match, count matches, or list the files
  that match.
- **Programs that keep running.** A dev server or file watcher can be
  started, read, sent input and stopped. It runs only in its session and is
  stopped when that session ends.
- **A plan list, notebooks, and app resources.** The model can keep a step
  list up to date, edit Jupyter notebook cells, and read resources from your
  connected apps.
- **A quick check after each code edit.** After it writes a Python, JSON,
  TOML or JavaScript file, Jarvis checks that the file still parses and says
  so in one line. The check never blocks the edit.
- **Your settings still apply.** "Ask before file edits" covers the new
  editing tools, and Undo, the activity log and low-trust limits treat them
  like every other tool.

## Measuring the coding harness (v1.363.0)

The coding screen planned for Build (the "IJ harness": Jarvis itself running
a coding session on any model you have) is being built in steps, and each
step is held to a measured number rather than a claim. This release adds the
measuring tool. Nothing on the screens changes yet.

- **What it is.** `ironjarvis bench-harness run` gives a set of 30 small
  coding tasks to one runner — Iron Jarvis with any model, Claude Code, or
  Codex — each in a fresh throwaway copy. The tasks are bug fixes, small
  features, refactors, explain-the-code questions, shell work, traps, and
  programs that keep running. They come in two tiers: 20 core tasks and 10
  harder ones. Afterwards `ironjarvis bench-harness report` writes one table
  comparing every runner and model that has been measured.
- **How it grades.** Each task's grader stays hidden from the agent and runs
  in a separate copy after the agent finishes, so the agent never sees the
  answer. A grader that could not run, or a run stopped by something outside
  the agent (a usage limit, an unreachable server, the demo model answering),
  is left out of the pass rate and listed on its own. A run that hung, hit
  its time or round limit, or stopped part-way counts as a fail.
- **It never touches your real data.** The runs need a scratch folder of
  their own (`--root`). Iron Jarvis refuses its own data folder and the
  source code folder. Every task is told to work only in its folder and
  never to stop a program it did not start.
- **First numbers (2026-10-21, this PC).**
  - Iron Jarvis on your DGX Spark's `glm-5.3-flash`: 20 of 20 core tasks and
    8 of 10 hard ones, at no cost.
  - The same model with thinking on (`glm-5.3-flash-think`): also 20 of 20
    and 8 of 10, but it failed different hard tasks.
  - Claude Code and Codex each passed all 6 tasks they were given (3 core
    and 3 hard). The rest of their runs wait for the weekly usage reset.
- It is a developer tool. It is not in the installed app's menus, and the
  packaged app refuses to run it.

## A model server that sends some names to the cloud (v1.362.0)

A model server on your own network can pass some of its models on to a cloud
service. A LiteLLM proxy on your DGX Spark, for example, can serve
`glm-5.3-flash` from a box on your network and `frontier` from OpenRouter.
Until now Iron Jarvis judged the whole server by its address, so a cloud name
like `frontier` read as local everywhere, and a Local only chat would send to
it.

- **Each name is judged by where it really goes.** Iron Jarvis reads the
  server's own routing list in the background (about once a minute) and marks
  every name that leaves your network as cloud. The model menu shows it as
  cloud, the reply's "answered by" line says cloud, and the model is told it
  runs in the cloud.
- **Local only refuses it.** In a Local only chat a name the server sends to
  the cloud gets the usual card ("Send once" / "Keep it local"). If it is
  refused, the message says which name and why: "DGX Spark passes frontier on
  to a cloud service." Its local names on the same server send as before.
- **Hidden names count as cloud on a mixed server.** A server can answer to a
  name it does not list (a shortcut such as `fleet`). Iron Jarvis cannot see
  where a hidden name goes without sending a message. On a server that sends
  any name to the cloud, a hidden name is treated as cloud in Local only chats.
  On a server whose every name stays home, it stays local. To keep a Local only
  chat on such a server, pick one of its listed local names.
- **What it can read.** This works for LiteLLM proxies. Iron Jarvis keeps the
  last list it read, including across a restart. A server that is not a proxy
  (Ollama, vLLM, LM Studio) is judged by its address as before. A proxy that
  shows its list only with a key is read with the key you saved for that
  server (since v1.365.2).

## No notice lost, the app you meant once more, and your machine named once (v1.361.0)

Each thing that happens is still announced once, and now no notice goes
missing. Chat again offers to connect the app you're asking about, and a
reply names the machine that answered once. The look of the app is the
same.

- **Notices reach you from a popped-out window.** Close the main window to
  the tray with a page popped out into its own window, and that window now
  shows the desktop notices the main window would. That includes a workflow
  waiting for your answer, a task that failed, a mission's outcome, finished
  media and your schedules' "done" lines. When you open the main window
  again, it takes over. Nothing is said twice.
- **A scheduled workflow says which step failed.**
  - When a step that is allowed to fail does fail, the schedule's row reads
    **Part failed**. Your phone, and every other place the schedule
    reports to, gets one line naming the step and why, for example "…ran,
    but its step “Fetch” could not finish: The inbox could not be reached."
  - A workflow that stopped names the step that stopped it.
- **A failed workflow is always announced.** That holds even when one of
  its steps failed at first, was announced, and then worked on a second try.
- **A single new request names itself.** When one new thing is waiting for
  your OK, the desktop notice says what it is, for example "1 workflow
  question waiting: “Nightly” asks “Send it?”". When several arrive
  together, you get the counts.
- **Chat offers the app you mean again.**
  - "did anyone message me on Slack?", "DM Bob on Slack", "gmail: anything
    from the IRS?", "list my Stripe disputes", "generate an image with
    Pixio" and "what's on my calendar in outlook" show the **Connect … in
    Settings** button again when that app isn't connected.
  - "check Slack and Gmail for anything urgent" offers one button that
    names both.
  - A sentence about an app rather than your own things in it still offers
    nothing: "write an article on Stripe", "compare Slack and Teams for a
    small team", "In my outlook, the market will recover".
- **Ask how Iron Jarvis works in your own words.** Questions like "how do I
  connect my phone", "how do I set up a backup", "how do I use the bell" and
  "Hi Jarvis! How do I connect my phone?" are looked up in the Handbook
  again. "How do I make a cake" isn't.
- **Your machine, named once.**
  - A reply from a stand-in names its machine once: "answered by Spark B
    (you asked for Claude Code) · local".
  - A run made through OpenCode keeps OpenCode's name on its page.
  - Your Ollama server is never called by the name of one of its models.
  - A run that never reached a model, for example on a cloud model that
    isn't connected, no longer says it went to the cloud.
- **The approval menu fits a short window.** If the reading rules arrive
  just after you tap the approval chip, the menu fits itself to the window
  again.

## One notice for each thing that happens, the app you meant, and your own machine by name (v1.360.0)

Background work tells you once, chat offers the app you actually meant, and
your own model server is called by the name you gave it. The look of the app
is the same.

- **One notice for each outcome.**
  - While Iron Jarvis is open, a scheduled task or workflow that fails or
    needs you raises one desktop notice, the bell's. It used to raise two or
    three, in different words.
  - A failed workflow is one row in the bell, not one row for the workflow
    and another for the step that stopped it.
  - When Iron Jarvis sits in the tray with no window open, a background run
    that failed or did not finish raises one Windows notice in the same
    words. Clicking it opens the run, or the Workflows page for a workflow.
    Nothing is said twice, and nothing you already saw in an open window is
    said again.
  - A request for your OK gets one notice too, whether it is an agent asking
    to use a tool or a scheduled workflow waiting for your answer. The bell
    announces it. Your phone still gets its own line.
  - Your phone gets one line per scheduled run, the schedule's own. Turning a
    schedule's notifications off now silences it completely.
  - A goal schedule whose run ended needing you says **Needs you** on its
    Schedules row, the same as the bell.
- **A helper run's result keeps its colour.** The finished line under a
  helper agent shows "failed" in red, and "needs you" or "part failed" in
  amber. That word stays visible when a narrow window cuts the line short.
  Hover over the line to read all of it.
- **Sessions' "Clean up now?"** is red when it asks you to confirm, like the
  button next to it.
- **Chat offers the app you meant.**
  - The **Connect … in Settings** button appears only when you ask about your
    own things in an app, for example "check my Stripe payments from
    yesterday" or "list my GitHub repos". Everyday and research sentences
    such as "stripe the table rows" or "explain how Postgres indexes work"
    offer nothing.
  - Mail and calendar words mean the app you connected. With Microsoft 365
    connected, "check my inbox" uses Outlook. With no mail app connected, one
    button names both choices, Gmail and Microsoft 365. "My calendar" with no
    calendar app offers nothing, because the words did not say which one.
  - Chat looks things up in the Handbook only when you ask about Iron Jarvis
    itself. "How do I install node" no longer sends it there.
- **Your own model server, by name.** A reply from the model server you set
  up says the name you gave it, for example "answered by DGX Spark". The model
  menu, the chip, Settings' model lists, a helper agent's card and row, and
  the run page all use the same name. A server you never named reads "your
  model server" everywhere.
- **The approvals say what Jarvis may read.** Once you limit reading to
  chosen folders in Settings › Permissions & ledger, the chat's approval chip,
  the Access summary and an agent's approval choices all say "Reads files only
  in the folders you chose, without asking".
- **Found extensions say what they are.** When you bring servers in from
  Claude, VS Code or another app, one Iron Jarvis doesn't know says what it
  is, for example "QuickBooks Desktop · runs a program on this PC
  (quickbooks-desktop-mcp)" or "Linear · reached at a web address
  (mcp.linear.app)". Once it is added and started, its row lists its tools
  instead.
- **The same words everywhere.** Error messages, the Status map and the
  extension fixes now use the words the rest of the app uses: extensions,
  Build terminals, tasks and memory bases. For example, the Status map says
  "4 of 4 extensions running" and "0 open terminals", and a broken
  extension's fix points at Tools › Connected extensions.

## The last raw words go, the Connect button always shows, and Emergency stop stops everything (v1.359.0)

The last places the app spoke in its own internal words now speak plainly.
Naming an app you haven't connected always gives you its Connect button, and
Emergency stop now also stops scheduled work that was waiting for your
answer. The look of the app is the same.

- **A run's log says what happened, in words.**
  - Every step of a run reads as what it did: "queued a task for a
    teammate", "switched a watcher on or off", "typed into a terminal", never
    an internal name with spaces in it.
  - A step that was refused says what it set out to do: "Builder tried to
    save a file, but it was not given that tool, so nothing ran".
  - A connected app's step reads the way the chat names it,
    "Gmail › search messages", both in "Step by step" and in "What it did".
  - A run you stop says "The agent was stopped before it finished." (or
    "…before it started." if it was still waiting its turn). Older stopped
    runs read the same sentence on their page.
  - When a run fails, the advice fits where you read it: "Run it again, or
    choose another model in Settings." Chat keeps "Press Retry, or pick
    another model for this chat." A scheduled run that failed shows that
    sentence on its Schedules row, never the raw error text.
- **The demo says it is the demo.** Before you choose a model, the demo
  model's reply opens "This is the demo model: a scripted demo, not real
  answers." It says it saved RESULT.md only when that really happened, and it
  is named once: in chat, in the browser sidebar, and in the reply another
  computer gets back.
- **Model names in words, in every list.** The model menu, the Try again
  menu, Settings' default model, an agent's preferred model, a terminal's
  model, templates, a project's default and the New task form all read
  "Your Claude plan", "Your ChatGPT plan", "Your model server" or "Opus 4.8".
  The exact name is in the hover text. Typing "Claude plan" or "model
  server" in the model menu's filter finds the row.
- **Connections reads Claude Code, Codex and OpenCode**, and the bell names a
  run that needs your OK by its task. The run's short code is only in the
  hover text.
- **The Connect button always shows.** Ask about your own things in an app
  you haven't connected ("find my tax files in Google Drive", "post this to
  Slack") and the reply ends with that app's **Connect … in Settings**
  button, whatever the AI does. Two apps share one button. A question about
  an app itself ("how much does Dropbox cost?") offers nothing. Everyday
  words aren't mistaken for apps either: "no notion" isn't Notion, and
  "slack in the schedule" isn't Slack.
- **Emergency stop stops everything that runs on its own.** Turning it on
  now also stops a scheduled workflow that is waiting for your answer. Its
  Schedules row says "Emergency stop was turned on, so your workflow … was
  stopped before you answered." Work you started yourself keeps going,
  including a workflow you ran that is waiting for you. The Autonomy page's
  "Stopped N running tasks" counts everything it stopped.

## Every screen tells one story, chat picks the right tools, another computer by a switch (v1.358.0)

What a run did now reads the same on every screen, chat reaches the web and
the right app tools again, and another computer can reach this one through a
setting you turn on. The look of the app is the same.

- **The outcome comes first, in the same words everywhere.**
  - A Schedules row starts with how the last run went, and the word itself
    is coloured when it matters: **Needs you**, **Part failed**, **Failed**,
    **Skipped**. The note it sends, **Run now**, the bell, the Sessions list,
    the board and the run's own page use the same words.
  - A scheduled workflow waiting for your answer reads **Needs you**. Once
    you answer and it finishes, its row says so ("Your workflow “Weekly
    report” finished after your answer.") and the home line stops counting
    it. If you stop it on the Workflows page before you answer, its row says
    it was stopped before you answered.
  - A goal that did not run reads **Skipped** and says why. A run that failed
    after nobody answered its question reads **Failed**, like its own page,
    and starts with "it asked for your OK and nobody answered in time".
  - An objective that finished with part of it failed is never announced as
    done, and the objectives list says **Part failed** or **Needs you**.
  - The bell shows a report's first words plainly (never "# Final Report"),
    and a browser task that stopped to ask reads "Your browser task is
    waiting for your OK", not as a failure. A failure that gave no reason
    says "Iron Jarvis kept the details in its log; Status shows how the app
    is doing."
- **One receipt for a helper's run in chat.** The row above the reply says
  how it ended, what it did, the model, the folder and, folded, **Step by
  step**. The reply under it shows only what the row does not: the files, any
  warning, what went wrong, and **Try again** / **Revert** / **Stop**. A reply
  that was cut off says "Cut off after 2.1 s", never a check mark. A call to
  one of your web services reads "Called crm". The second press of
  **Delete them all?** on Sessions is red again, and a failure in a Build
  terminal's chat folds its details under **Details**.
- **Chat picks the right tools.**
  - Research about an app ("look up the Stripe API docs for refunds",
    "search for Notion templates") searches the web again. A search in your
    own data ("search my Google Drive for the lease") goes to the app, or
    offers to connect it.
  - An app with more than 24 tools gets the 24 that fit your message, so
    "list my open pull requests" reaches GitHub's pull-request list. An app
    you turned on in the chat and also named in the message still gets 24.
  - The switches for your apps in a chat are in the **Tools** menu (the
    wrench) › **Connections**. Every sentence now says so; it used to say
    "+ › Connections", which is a different button.
  - A tool that was not turned on reads in words, with where to turn it on:
    "Jarvis couldn't save a file (not turned on for this chat) — turn on
    Auto tools in the chat's Tools menu, then ask for it in your message."
- **A failure names who failed.** "Claude Code didn't answer in time",
  "The connection to Claude Code dropped mid-answer", "Codex didn't answer in
  time", or "<the AI> couldn't finish this answer", with **Retry**. A
  newly connected app that did not start says so and why. A Pixio key that
  is missing or refused says "Update your Pixio key in Settings ›
  Connections, then try again."
- **The Directory and Connections agree.** Built-in tools are not offered
  again with a Connect button; their separate versions sit under **Separate
  versions of built-in tools**. "N connected" counts only apps that work,
  with switched-off ones and ones that need attention said apart. Microsoft
  365 reads **Sign in to Microsoft to finish** until you sign in, on its card
  and in chat. An app key (Pixio) is tested when you save it. Settings ›
  Secrets names a key "GitHub: GitHub personal access token". LM Studio and
  llama.cpp running on this PC are offered with one press wherever Ollama is
  (the chat's start, the first-run steps, the suggestion chip, Everything ›
  Status, Accounts), and **Re-detect** looks for them again at once.
- **The desktop speaks plainly.** The tray, Windows notifications and the
  startup message say **Iron Jarvis**. A question waiting while the window
  is closed uses the approval card's words and names the task ("save a file
  for “Draft the Q3 letter”").
- **Another computer can reach this one, on purpose.** Settings ›
  Connections › **Let other computers on my network reach this Iron Jarvis**
  is off until you turn it on (from chat it always asks), and works after a
  restart (Settings › System › Maintenance › **Restart Iron Jarvis**).
  Windows may ask once to allow it on your network. Every request still
  needs this computer's token. The **Another computer running Jarvis** tile
  links to it with **Open the setting**, and **Connect a remote agent** picks
  the task API for you when the address is another Iron Jarvis.
- **Plainer Settings names.** "How the assistant talks by default", "Use
  several accounts", "Time limit for one step (seconds)", "Work on my goals by
  itself", "Most model use in a day (tokens)", "Most actions it may take in a
  day" and "Follow a project folder's own instructions in chat". Searching
  the old names still finds them.

## Another computer as a teammate, your web services from chat, and one set of words (v1.357.0)

You can now hand a task to Iron Jarvis on another computer, let chat use a
web service you saved, and read the same words for the same thing on every
screen. The look of the app is the same.

- **Another computer running Jarvis is a teammate.**
  - *On the computer that will do the work:* open Agents › Your team ›
    **New & manage** and find **Another computer running Jarvis**. Type a name
    for the computer that will send it tasks (for example "laptop") and press
    **Turn on**. The tile shows a key once (**Copy token**) and the address to
    use (**Copy address**), and says whether the other computer can reach this
    one yet. **Rotate token** makes a new key; **Turn off** (press twice)
    forgets it.
  - *On the computer that sends tasks:* on its own **New & manage** tab, use
    **Connect a remote agent**: a name (for example "desk"), **http-task (task
    API)**, that address and that key. The form says when the address is another
    Iron Jarvis, and the answers come back to you without a second key.
    **Test** only checks the key with the other computer; it starts nothing
    there.
  - In a chat, "@desk summarise the Q3 notes" goes to that computer. The chat
    says "desk has the task and will report back", and the answer arrives in
    that same chat, credited to desk, with any files it made (up to a size
    limit).
  - On the computer doing the work, the task runs on its own, at low trust:
    nobody there is asked anything, so anything that would need an OK is not
    done, and **Emergency stop** refuses it and stops it. The bell, your phone
    and the run's page say **A task from laptop** and **From laptop**.
  - If an answer cannot be delivered it is tried once, never again, and the
    reason is written on the run and on the tile. A task sent from a
    local-only chat is refused there when that computer's model is off its
    own network.
  - If nobody has chosen a model on the computer doing the work yet, it
    answers with its demo model, and the answer says so: "this answer came
    from the demo model on that computer: a scripted demo, not real answers".
    Choose a model there to get real answers.
  - Both computers must be opened to each other on purpose: the installed app
    answers only on its own PC. The tiles say what to set; nothing is opened
    for you.
- **Your web services, from chat.** Settings › Connections › **More ways to
  connect** › **Your web services** keeps a service's address and its key;
  press **Test** to see that it answers. Then name it in a chat message ("get
  contact 42 from Acme CRM") and Jarvis can call it. A read asks first, like
  any other action; anything that changes something there (adding, changing,
  deleting) asks every time, even when you let Jarvis run without asking. A
  low-trust run can't call your web services, and a local-only chat asks
  before every call. The answer is cut to a readable length and the key never
  shows. Only key-based sign-in is supported, and a service on your own
  network (this PC or your office network) is refused for now: the card has
  no switch for it yet.
- **One list of tools.** Settings › Tools and the Directory read the same
  list, so a tool offered in both places has one name and one description:
  **Files & folders**, **Fetch web pages**, **Knowledge graph** and
  **Step-by-step reasoning** (chat still knows them by their old names). A
  tool on the Tools page that the Directory also offers links to it.
- **One set of words.** The demo is the **demo model** everywhere: under a
  chat reply ("this reply came from the demo model: a scripted demo, not real
  answers"), on a teammate's line, in the browser sidebar and in every
  refusal that needs a real model. The Windows notifications say **Build
  terminal**, **task** and **run** like the bell ("A Build terminal is waiting
  for you", "click to open the run", "2 tasks waiting for you").
  **Emergency stop** names what it stops: schedules, goals, rules, phone
  tasks, queued tasks and tasks from another computer.
- **An app you switched off stays in its chats, marked Off.** A chat that had
  an app turned on still lists it under Tools › Connections after you switch the
  app off: **Off**, with a line saying where to turn it on. It does nothing in
  that chat meanwhile and is back as soon as you switch the app on. **Remove
  from this chat** takes it out.
- **An agent's reply says what it could not see**, in the chat's own words:
  "Left out 3 older messages to fit the agent's model".

## Switch an app off without losing it, Microsoft 365, and the last plain words (v1.356.0)

You can now pause an app instead of removing it, connect Microsoft 365, and
read every control in plain words. The look of the app is the same.

- **Switch an app off, and back on.** Every extension on Settings › Tools and
  every connected card in the Directory has an On/Off switch. Off pauses its
  tools for chats and agents at once; its settings and keys are kept, so
  switching it back on needs nothing typed again. An app that is off reads
  **Off** everywhere: on its row, on its card, in Everything › Status ("2 of 2
  tool packs running (1 switched off)"), in the health check, and to Jarvis
  itself, which says it is switched off rather than broken. Asking the chat to
  "connect my Slack" while Slack is off tells you where to switch it on and
  never asks for the key again. A tool brought in from another app where it
  was switched off arrives switched off here too.
- **Microsoft 365 (Outlook, Calendar, OneDrive).** Press **Connect** on its
  Directory card and answer one question: **Work or school account** or
  **Personal account** (Outlook.com, Hotmail, Live). Then press **Sign in to
  Microsoft**: a Build pane opens with a web address and a code. Open the
  address, type the code and sign in. Turn it on in a chat with + ›
  Connections and ask, for example, what's on your calendar tomorrow. It reads
  and changes calendar events, reads, drafts and sends email, reads contacts
  and reads OneDrive files, and asks before each use unless you allow it. It
  needs Node.js. Saying "connect my Outlook" in chat sets it up the same way.
  (Changed in v1.428.0: it is now read and draft only, signs in one account,
  works only on your own models, and is set up from its card. See "Microsoft
  365, locked down for client data".)
- **Adding and editing tools without technical words.** The Tools page says
  how each tool connects in words ("A web address", "(older servers)"), keeps
  its command or address folded under **Show command** / **Show address**,
  names its settings as settings, and says **Remove**. A tool that is failing
  adds "Chats and agents can't use its tools until it works."
- **Chat controls in plain words.** A long chat offers "This chat is getting
  long. Summarize the start to keep it fast? The full transcript is kept." with
  a **Summarize** button. The tools menu lists each tool by what it does
  ("Read files", "gmail: search messages") with **Turn off**. A model that
  reads very long documents shows **long docs**; the thinking control's
  default reads **Model decides**; the first-run model server door asks for
  "Your model server's address" and "Model name".
- **Labels say what they lead to.** Hovering **low trust** says what that run
  can't do (change settings, schedules, rules, connections, memory, agents or
  skills, type into your terminals, spend credits); the session page says the
  same. A setting marked **after a restart** is a press that opens Settings ›
  System › Maintenance, where **Restart Iron Jarvis** is. The Sessions page
  button reads **Clear finished, failed and stopped** and asks before it
  deletes. Everything › Status names your models as the model menu does and
  names the browser you paired.
- **Choose the folders Jarvis may read.** Settings › Permissions & ledger has
  **Folders Jarvis may read**, one folder per line (a comma in a folder name
  is fine). Leave it empty to let Jarvis read anywhere except the folders
  that hold its own keys and records. With folders listed, chat and agents
  read only inside them and Jarvis's own working folders; a read elsewhere is
  refused with "That folder isn't one Jarvis may read. Change the folders in
  Settings › Permissions." A project or work folder outside them says the
  same place. **What Jarvis can reach** updates as soon as you save.
- **A paid request shows its price on the mission screen too.** When a
  teammate asks to make a paid picture, the approval says what it costs
  ("Generate 1 image on Pixio for 12 credits (your price)") before **Allow**.
- **One quiet status chip.** Status chips on Sessions, Tools (including the
  catalog cards) and Schedules are the same calm word with a small coloured
  dot. Only the **Close this terminal?** dialog and the **Restart Iron
  Jarvis** button keep their outline on purpose.
- **Bringing tools from your other apps, honestly.** A tool already added
  under a slightly different name is skipped with "already added as
  brave_search" unless you choose **Replace**. A saved Pixio key is said on
  its own line ("Your Pixio key is saved here for its built-in tools"), never
  as "already connected" for a different tool of the same name.
- **An agent's waiting time starts at your question.** Pressing Continue does
  not restart the clock on an @-mentioned agent's reply.
- **Gemini 3.8 Flash and Gemini 3.5 Flash-Lite are priced at Google's
  published rates** in usage and allowance estimates.

## Every reply says what happened, and every app has one way in (v1.355.0)

Replies now say what was left out, what you approved and how long they took;
a failure comes with the button that fixes it; and each app you connect is
added, changed and removed one way, wherever you start. The look of the app
is the same.

- **A failed answer has its fix beside it.** The line is the headline
  ("Claude Code isn't signed in") with the one press that fixes it: **Sign in
  to Claude Code** opens a sign-in window right there, and a refused key gets
  **Update the key in Connections**. The service's own words are folded under
  **Details**, **Retry** works as before, and "Your message is back in the
  box." says your question was kept. This is the same whether the answer was
  streaming or not.
- **A reply says what it had to leave out.** On a very long chat a model can
  refuse because it is too long. Iron Jarvis shortens it and asks once more,
  and the reply's line says so: "Left out 12 older messages to fit this
  model" (or "Shortened earlier tool results to fit this model" when nothing
  was left out). Open the reply's line to see why, and every approval you
  gave during it ("You approved: save a file (once)").
- **A plain answer says how long it took.** "Answered in 2.1 s" sits quietly
  above a reply that needed no steps.
- **Agents you @-mention say when they answered.** A reply reads "from
  hermes · 6 min 12 s". A remote agent still working says "· waiting 3 min"
  until its answer arrives. One that cannot message back says so, and how to
  turn **Let it message back** on.
- **Background problems are named, with what still works.** Instead of an
  internal task name, the bell and Everything › Status say "Slack messages
  aren't arriving · Open", note what keeps working (chat and your other
  channels), and **Open** goes to the page that fixes it. A task that keeps
  retrying shows how long it has really been failing. The chat home offers
  **Ask Jarvis** with "Why is a background task failing?" ready to send. When
  nothing answered a background job, the notice says the same sentence the
  job's own page says, adds "Nothing was changed in that step.", and offers
  **See the job**.
- **GitHub connects to GitHub's own server.** There is nothing to install:
  paste a personal access token and it works, from the Directory or from
  Tools (both store the same connection). An older GitHub connection keeps
  working, and its card says how to switch.
- **Change a key without disconnecting.** A connected app that holds a key
  has **Change key** on its Directory card. Paste the new one and it reloads
  in place. A key typed on the chat's secure card replaces the same key, and
  **Undo** there puts the old one back.
- **A connected app says what it can do.** "Can read · Can change (2 tools) ·
  Asks before changing anything", read off the app's real tools.
- **Disconnect removes everything.** The connection, its keys and what it
  remembered all go, so connecting again is a clean start, and a failed
  connect says the real reason.
- **Bringing tools from your other apps asks for what they left blank.** VS
  Code's `${input:…}` prompts and a plugin's `${TOKEN}` are not values: the
  row opens on a box for each one, and a header like `Bearer ${TOKEN}` asks
  for TOKEN and fills it in. A tool already added under a slightly different
  name reads "already added as brave_search" only when it really is the same
  tool. Being signed in to Google Drive here (that gives memory search) is
  said on its own line, not as "already connected".
- **Gemini takes a key.** Paste a key from Google AI Studio on the Google
  card or in the first-run door. Its Test checks the key, the current Gemini
  models are in the model menu, and the card says whether you connected by
  key or by signing in.
- **Local servers in one press.** The first-run door and Settings ›
  Connections both call it **Model servers on your network**, with **LM
  Studio on this PC** and **llama.cpp** as one-press choices that list the
  server's own models. With Ollama, **Download a starter model** opens a
  Build pane already downloading Llama 3.2. Saving a server that answered
  with an error now says it answered ("Check the model name and the key"),
  never that it couldn't be reached.
- **Forms for work that runs on its own say how asks work.** New schedules,
  goals and workflows say: "If a step needs your OK it waits 5 minutes (bell
  and desktop notice); if nobody answers, that step is skipped."
- **Two presses, everywhere.** Removing a link in the memory graph and ending
  a Creative session take two presses like every other removal, with no
  pop-up dialog.
- **A Brave Search key saved from the Directory now powers web search too.**

## Plain words everywhere; chat knows your apps (v1.354.0)

Iron Jarvis now says what happened in plain words, in the same words
wherever you meet them, and the chat knows which of your apps are connected.
The look of the app is the same.

- **When an answer fails, the line says why and what to do.** "OpenRouter
  refused your key. Update it in Settings › Connections." or "Claude Code
  isn't signed in. Press Sign in beside Claude Code in Settings ›
  Connections, then retry." The service's own message is still there: hover
  the line. A run that fails for the same reason says the same sentence on
  its page and in the bell ("The run failed: OpenRouter refused your key…"),
  and so does the message on your phone. Iron Jarvis still never switches to
  a different AI without asking; the words now say that, and that you can
  allow it in Settings.
- **Iron Jarvis, not "the daemon".** When the app stops answering, the
  banner says **Iron Jarvis isn't responding.** and that your chats and files
  are saved. When it answers again you see **Iron Jarvis is back.** for a
  few seconds, and anything a restart stopped is waiting in the bell. A panel
  that could not load says so in one quiet line ("The board couldn't load.
  Try again."); the code behind it is on hover. Settings › Maintenance says
  **Restart Iron Jarvis**, and the token card is called **Access token** like
  its row.
- **A run's page reads like a receipt.** It is titled by what you asked,
  says which agent and model did it and how long it took ("Took 2 min 5 s"),
  shows the steps in the chat's words, and folds the full record under one
  **Full record** line. A run a restart cut off reads **Interrupted**, says
  "Stopped when Iron Jarvis restarted", and offers the bell's **Continue** /
  **Dismiss**. Continue opens the new run, titled by what you first asked.
- **Chat knows your apps by name.** Say "check my Gmail", "search my Google
  Drive for the lease" or "is GitHub connected?" and the chat knows whether
  that app is connected. A connected app's tools are offered for that
  message (each use still asks you first); one that isn't connected is said
  so, with a button to connect it, and the chat never searches the web
  instead.
- **Bring your tools from your other apps.** Settings › Tools › **Add a
  tool** opens on **Bring your tools from Claude, Codex, Cursor…**. It also
  reads VS Code's `mcp.json`, a project's `.vscode` / `.cursor` settings,
  Windsurf, Gemini CLI, the Claude Code plugins you installed and — since
  the readiness plan's Wave C — the four coding tools Build › Code drives:
  OpenCode (`opencode.json(c)` / `config.json`, and a project's own), Grok
  (`~/.grok/config.toml`, and a project's `.grok/config.toml`), Antigravity
  (`~/.gemini/config/mcp_config.json`) and Pi (`~/.pi/agent/mcp.json`, and
  a project's `.pi/mcp.json`), each listed by the tool's name. Each tool
  says in one line what it does (the command is under **Details**); one you
  switched off in its app is listed unticked as "turned off in Claude
  Desktop"; one you already have reads "already connected". After **Add
  selected**, it names the tools that are ready and says "Use it in a chat:
  Tools › Connections"; one that didn't start offers **Open its row** to retry
  it. The paste box is now **Paste from a website or README (advanced)**.
  The Directory's "Found 3 tools in …" line opens this same door, and every
  **Add a tool** link lands right on it.
- **Your model server's card tells the truth.** A server on your network
  that answers reads **Connected · your endpoint · no key needed**, exactly
  when the model menu can use it. A mistyped address says it could not be
  reached instead of a green "saved". The old REST checks sit under
  **More ways to connect (advanced)** and say they only check a connection.
- **Settings rows are named like their pages.** Connections, Directory,
  Secrets, Tools, You and Train Jarvis on me; searching the old names still
  finds them.
- **Deleting asks twice, the same way everywhere.** Removing a writing
  sample and deleting a workflow from the canvas take two presses
  ("Remove" → "Remove sample?", "Delete" → "Delete workflow?").
- **Your phone and desktop alerts are in plain words.** "Your schedule
  morning brief could not finish — The run failed: …", "Your workflow nightly
  digest could not finish — the Workflows page shows which step stopped.",
  "A change is waiting for your review (2 files changed, high risk)." — the
  bell's words, never an internal event name or code.

## Connect what you use, and a clean first five minutes (v1.353.0)

A new install now tells you what it found, offers what you already have in
one press, and sends you to one place for each kind of thing. The chat looks
the same.

- **Installing.** The release page names the file to download
  (`Iron-Jarvis-Setup-<version>.exe`; ignore the `.blockmap` and `latest.yml`
  files) and what to press at Windows' "Windows protected your PC" warning:
  **More info**, then **Run anyway**.
- **Choosing what answers you.** The empty chat's heading reads "Ready —
  choose what answers you". If Ollama is running on this PC with a model
  downloaded, **Use Ollama (on this PC) for answers** connects it and
  chooses it in one press (if it isn't running, the press says so and changes
  nothing; an address you saved yourself is never replaced). After any press
  the home keeps saying "Done — Claude (your Claude Code sign-in) answers you
  now." in the words you pressed. If you already chose a model, a press
  changes nothing and says so.
- **Signing in without a terminal.** Signed out of Claude Code or Codex? On
  **Settings › Connections › Accounts & keys** (and on the chat's door), **Sign
  in to Claude Code** opens a sign-in window inside the app: type /login
  there and finish in your browser; the row turns green by itself. A tool
  that isn't installed links to its maker's page. The Local Ollama row offers
  **Use Ollama on this PC**, **Get Ollama** when it isn't running, or says
  "Not answering at the address you saved" when an address you saved is down.
- **Model names, not codes.** The model menu groups models as Anthropic,
  Claude Code, Codex, Your model server and OpenCode, and your own endpoints
  by the names you gave them. In Settings › Models the default model is
  picked from a list (Other… still takes any model id), and the list follows
  the provider chosen in the same form.
- **The machine check and your apps, on the home.** The empty chat shows one
  quiet line such as "This PC: 5 of 6 helpers ready — Reading old .doc and .xls
  files · Details" while setup is unfinished. Once a real model answers, a
  second line offers **Connect the apps you use — GitHub, Notion, Slack…**,
  which opens the Directory. The getting-started checklist has the same
  optional step, **Connect the apps you use**, and its **Open the Directory**
  press goes to the same place.
- **One Connections home.** Settings › Connections › Apps & tools (the
  Directory) names the tools your other apps already use ("Found 3 tools in
  Claude Desktop and Cursor") with an **Import** button (nothing is added
  until you tick tools and press **Add selected**), says what is built in
  (files, web, images, spreadsheets, memory), and links the other ways to
  connect. Packs you added yourself are grouped **Added by you**, described
  by what their tools do. A group with nothing in it has no filter chip.
- **What a card needs, before you press Connect.** Gmail says "Needs a
  one-time Google developer setup"; Google Drive, OneDrive and Dropbox say
  their developer setup until it is done, with **How to set it up**; a pack
  whose helper program is missing says "Needs Node.js". After a Drive sign-in
  the card says "Now choose a folder for memory." Puppeteer is no longer
  offered: the **Your browser** card opens the browser settings. Slack uses
  the package its makers now maintain, and GitHub, Postgres and Google Maps
  say they use an older package.
- **Asking chat what it can do.** "What can you do?" and how-do-I questions
  ("how do I chat with you from my phone") are answered from this Handbook.
- **When something is not set up, the reply leaves a button to the fix.**
  "Find my tax files in Google Drive" with Drive not set up ends with
  **Connect Google Drive in Settings**; a question about your browser when
  it isn't paired ends with **Pair your browser** (or **Turn on browser
  access**); asking to connect "puppeteer" or "the browser" points at the
  browser settings with the same button. A card you declined leaves no
  button. Asking for a picture, video or song with no media service connected
  shows the secure **Pixio API key** card straight away (the offline demo
  does this too), and the key goes in Settings › Connections › Accounts &
  keys. Connecting an app that signs in through the browser, or one that
  only needs a key, no longer shows a "connect an app" approval first: the
  key card itself is the question.
- **Build panes say what happened.** A pane that went quiet while you weren't
  looking reads **stopped — take a look** in grey (no green check: it may
  have stopped on an error). A pane whose coding tool is waiting on a
  question shows in the bell as "Claude Code in Build is waiting for you"
  (not on Build itself), and as a Windows notification when the window is
  closed to the tray. A failed Build-pane chat turn has **Retry**, and its
  receipt reads "answered by <model>" like chat, with each file listed once.
- **The browser says what happened.** A finished sidebar task folds to a
  line like "4 steps · 1 failed · 12 s". The agent browser's approval queue
  answers **Allow once** or **Deny** like every other approval, and says the
  truth: the agent's step stops there; allow it, then ask the agent to do
  that step again within 15 minutes. The bell row reads "A website action is
  waiting for your OK". Settings › Connections › Browser no longer promises
  desktop control.
- **Your approval setting is one dial.** Settings › Permissions' **Approvals
  in chat** and the chat's permission chip are the same setting: change one
  and a new chat starts from it, and a press on the chip shows up in
  Settings.
- **Local only asks first for a cloud endpoint.** In a chat kept on this PC,
  a model server you added that is really in the cloud now shows the
  "Send this message to …? It will leave this PC." card before anything is
  sent, instead of a refusal after the press.
- **Phone chat on one screen.** Typing "phone" in Ctrl K finds
  Notifications. On the Telegram form, tick chat right under the detected
  chat ID and the allowed sender fills in from your own private chat; untick
  it and listening goes back off. Help leads with two-way messaging; its
  "dashboard on your phone" recipe is marked Advanced and lists what it
  really needs.
- **Everything is tiered.** Each column lists everyday pages first, with
  developer pages under **More**; hovering a sidebar row such as Build says
  what it is for.

## One set of words for what happened, where, and what needs your OK (v1.352.0)

Every place that shows work now says it the same way: how it ended, who did
it, where it ran, and what it wants from you. The chat looks the same.

- **A run you stopped reads "Stopped".** Press Stop on a helper agent and the
  card under its reply says **Stopped** in grey, with Try again, instead of a
  red "Task failed". A run that used up its steps reads **Stopped at its step
  limit**. Only a real failure is red. The hand-off line settles at once:
  "The researcher worked for 45 s · stopped".
- **A helper agent says what it did, in words.** Its row reads "The
  researcher worked for 2 min 5 s · 6 steps · part failed" (or "· needs you",
  "· stopped", "· failed", "· hit its step limit"). When the hand-off did not
  name an agent, the row names the one that ran ("Your tax-reader agent
  worked for 3 min"). Open it for the plain lines: "Searched your chats 83
  times", "One memory lookup failed", "Ran on Opus 5.5 · cloud", and "in
  <folder> · Open folder" ("in its own work folder" when the run was given
  no folder of yours). The card under the reply reads "answered by Opus
  5.5 · cloud · 2 min 5 s · 6 steps · 86 tool calls", says when the model
  had to be swapped, and folds **What it did** step by step ("tax-reader
  read k1-2025.pdf", "needed your OK to run a command"). Tool names are plain
  words; a failure's own text is one click away.
- **Every reply says where it ran.** "answered by glm-4.6 on DGX Spark" for
  an endpoint you named, "· local" for your PC or a machine on your network,
  "· cloud" for a model that runs elsewhere. Older replies show nothing new.
  The model menu's badges read **local**, **cloud · on your plan** and
  **cloud · pay per use**, with the cost on hover; an endpoint you added on
  Connections that is outside your network (a cloud API) reads **cloud**,
  never "local", the same as its replies' receipt. If you picked a remote
  agent for a hand-off that has to run here, the line says so: "Running on
  this PC. Reach hermes with @hermes."
- **The same status words everywhere:** Working, Queued, Done, Needs you,
  Part failed, Failed, and a grey Stopped (since v1.358.0; "Done · needs
  you" / "Done · part failed" before). The Session board's
  columns are Working, Waiting for you, Done and Failed; a stopped card says
  Stopped, and an empty column says so in a sentence. A project's Schedules
  list uses the Schedules page's words.
- **App and browser steps read in words:** "gmail: search messages" under
  "Worked for 3 s · used gmail", and "Used your browser · click".
- **One approval language.** Every card answers **Allow once**, **Allow for
  this chat** (in a chat or a Build pane) or **Allow for this run** (an agent
  run's ask: the session page, a project's task, the mission screen, the
  bell), and **Deny**. After a wide answer the note says "Allowed for this
  chat." Settings and agent setup show the chat chip's three levels: **Ask
  first**, **Ask when risky**, **Don't ask** (typing an old name such as
  "approve for me" still sets the same level). The browser sidebar's wide
  answer is **Allow all page actions**, because it covers every tab.
- **A chat's card says why it asks, and who asks** (v1.417.0; a chat or a
  coding session's reply; an agent run's ask keeps the longer card). It is
  short: who wants to do what ("The assistant wants to save a file", or
  "Iron Jarvis wants to run a command" when it is Iron Jarvis's own check,
  such as re-running your tests), what will run, the price if it costs
  money, and ONE line saying why it asks: the rule that kept it under
  Accept edits or Auto, "not in this chat's grants", or the reason it asks
  every time. The tool's exact name is in the header's tooltip, and what a
  wider answer covers is in that button's tooltip. A card that the app
  answers once whatever you press (a paid generation, a local-only chat's
  card, a plan) shows only **Allow once** and **Deny**; before v1.417.0 a
  paid card also showed "Allow for this session", which still counted as
  once. On a card that asks every time for safety (a commit or push, a
  project's command, a risky click) Enter still never allows it, but now
  says so for a moment: "Click Allow once to run it".
- **Every ask says whether it can be undone.** "You can undo this." for a
  file save, "This may not be undoable." for a message or a command; a read
  says nothing. The line is on every card: the chat, a Build pane, the
  session page, a project's task, the mission screen and the bell.
- **The bell says what and why:** "Your nightly-brief run wants to save a
  file. The run waits up to 5 min for you.", what the run was for, whether
  it can be undone, and after you answer "Approved. The run continues · Open
  run" for a few seconds.
- **The mission screen uses the chat's words:** "Answered by fleet on DGX
  Spark" (and "Working with glm-4.6 on DGX Spark" while it runs), "Waiting
  for your OK to save a file.", how long each teammate took, and its activity
  log says "needed your OK to save a file", never a tool's code name.
- **See what is running.** The footer reads **Ready** when the app is up;
  press the orb for **Running now**, one row per job ("Schedule ·
  nightly-brief · 3 min") that opens its run. Status's Running number opens
  the same list.
- **What Jarvis can reach is written down.** Settings › Permissions & ledger
  opens with it: it reads files anywhere on this PC except its own key
  folders without asking, saves only in the chat's or the project's folder,
  which apps are connected, and your approval level.
- **A workflow you run yourself keeps going under Emergency stop.** Pressing
  Run on a workflow, or resuming one you started, is your own work: its steps
  run even with Emergency stop on, and turning the switch on does not stop
  them. A workflow that starts on its own (a schedule, a rule, a goal) still
  does not start.

## Jarvis tells you how things really went (v1.351.0)

Work that runs while you are away now says how it ended, in the same plain
words everywhere. The chat looks the same.

- **A scheduled job says how it really went.** The Schedules page (and a
  project's Schedules list) shows "✓ ok" only when the job was done. A run
  that stopped to ask you something nobody answered reads "finished · needs
  you", one where a step failed reads "finished · part failed", and a failed
  run says why in a plain sentence ("The run failed: the model endpoint is
  not answering"), never a code name. The note it sends says the same thing
  ("needs you" since v1.358.0), and **Run now** answers in the same words.
  "open session" still takes you to the run. Since v1.352.0 both lists use
  the app's one set of status words — since v1.358.0 **Done**, **Needs
  you**, **Part failed**, **Failed: <why>**, a grey **Stopped** and **Not
  run yet** (they read "✓ ok", "finished · needs you" and "finished · part
  failed" before v1.352.0, and "Done · needs you" / "Done · part failed"
  until v1.358.0).
- **You see it without going to look.** The Schedules light on the Status
  map turns amber when a schedule failed or fell short last time, and the
  line above the chat box offers "1 scheduled job needs a look · Open".
- **The bell rings for work nobody was watching.** When a schedule, a goal,
  a workflow, a rule, a message from your phone or an automatic run fails or
  falls short, the bell says so ("Your schedule morning-brief could not
  finish"), links to the run, and counts on its badge until you open it. A
  run that finished cleanly is a quiet row with no badge. A queued job that
  needs you, or partly failed, rings too.
- **The bell remembers while the window was closed.** Close the app to the
  tray, and when you open it again the bell lists what finished meanwhile
  under "While you were away", once.
- **A chat waiting for your OK is announced.** When a chat's question card
  is still waiting and no window is showing that chat any more (you opened
  another chat or a new one, or closed the app to the tray), the bell shows
  "A chat is waiting for your OK"; with the window closed to the tray, a
  desktop notice says the same. Clicking either opens that chat, where the
  card is. (Moving to another page in the same window, such as Build, keeps
  the chat following its reply, so the card simply waits there for you.)
- **Emergency stop really stops.** With it on, nothing that starts on its own
  can start: schedules, goals, rules, phone jobs, queued jobs and automatic
  work. Turning it on (from the Autonomy page, Settings, Ctrl K or chat) also
  stops those that were already running, and their rows say so. Queued jobs
  wait with the reason instead of failing, and a message from your phone, or
  a goal's **Run now**, gets the reason back instead of silence. Chat and the
  work you start yourself keep going.
- **Chat notes are fair.** The "nothing was written" note no longer appears
  after a file really changed (a resized photo, an edited sheet, a tool you
  made), and it now catches a reply that claims an edit that failed. In the
  offline demo the reply says once that it is the demo and to choose a model
  above. A model that runs in the cloud no longer tells you it runs on your
  computer.
- **Failures show in the work line.** The line under a reply counts failed
  steps as failures ("read 1 web page, 7 failed") with a warning mark in
  place of the check.
- **Green means it works.** A connected app in Connections reads "Connected"
  in green only when it is really working; one that isn't reads "Needs
  attention" with the reason and Retry. Adding or editing a tool pack that
  didn't start says so in amber, with the reason, instead of a green note.
  A working app's card says where to turn it on: "Tools › Connections" in a chat.
- **A mission that partly failed says so:** "Finished, but part of it
  failed", in amber, and the bell uses the same words.
- **Open on a chat's work folder opens the folder** in File Explorer.
- **Private settings stay private.** A password or token inside a tool pack's
  settings (an "Authorization: Bearer …" header, a database password in its
  address) is kept in the app's safe store and shown masked (`***`)
  everywhere; the pack still starts with the real value. Disconnecting an
  app removes them.
- **Connect an app right in the chat.** Ask "connect my GitHub", paste the
  token into the secure card, and the card says "GitHub connected · 26 tools"
  with **Continue**, which carries on with what you first asked, with GitHub
  turned on for that chat. If the app was already connected, Jarvis says so
  instead of asking for the token again.

## Sequential thinking starts; an endpoint's model list is its real models (v1.350.1)

- **The Sequential thinking tool pack works.** The app's own list of
  one-click packs named its package wrong (`server-sequentialthinking`;
  the real one is `server-sequential-thinking`), so it failed at its first
  call with "npm error 404 … is not in this registry". The list is fixed,
  and a pack already installed under the wrong name is repaired the next
  time the app starts. When a pack fails because its package doesn't exist,
  the Tools page now says so plainly ("the package … is not in its
  registry, so the pack's name for it is wrong") instead of asking you to
  read npm's output.
- **A model endpoint shows the models it really runs.** A LiteLLM proxy
  answers every name in its configuration, including old names that now
  point at the same model and embedding models that can't chat. The model
  menu now shows one entry per real model behind the endpoint (your chosen
  default name when it is one of them) and leaves embedding models out.
  Old names still work if something was set to use them.
- **A removed tool pack is gone everywhere** (v1.350.2). Removing a pack
  that had finished starting could leave it reading as "ready" in places
  that list pack health.
- **The model list stays on screen.** A connection's model list near the
  top of the menu used to run off the top of the window. It now fits the
  space above it, or opens downward when there isn't room, and scrolls.

## An agent's plan is a checklist; approving it first is optional (v1.350.0)

- **The plan ticks off.** When an agent that chat handed work to splits the
  job into steps (it happens most on local models), its row in the chat
  shows the plan as a quiet list: an empty circle for a step still waiting,
  an arrow for the one it is on, a tick with the words struck through for a
  step done, an x for one that failed, and a dash for one it never got to.
  Closed, the row adds "1 of 4 done". When the agent finishes, the chat
  keeps the plan as it ended, so a chat you reopen later still shows it. The
  session's own page shows the same list in its Live run card while the run
  is going. A screen reader hears each step's state in words.
- **Ask me to approve an agent's plan before it starts** (Settings, Agents &
  automation; OFF by default, and then nothing waits). Turn it on and an
  agent you are watching (from chat, the Agents page or a project) shows you
  its plan, numbered, in the question box's place before any step runs.
  **Allow once** runs the plan as shown; **Deny** ends the run before any
  step runs, and its result says you declined. A plan is never allowed "for
  this conversation" or "always": the next plan is a different one.
  Scheduled, workflow and phone runs never wait for this, and a run you
  Continue after a restart carries on with the plan it already had. A page
  you reload while a plan waits shows the same card with the same steps.

## Open a step to see what it did; one row for a helper agent (v1.349.0)

- **Every step opens.** The grey rows under a reply ("Read harbor.xlsx",
  "Ran a command") now open with a click, or Enter, to show what the step
  was given and what came back. A command shows as one terminal line, other
  steps as short "name: value" lines, and the result in plain text with
  "Show all" for long output. This works in chats you reopen later and in a
  Build pane's chat too. Keys and passwords in either are replaced with
  `***` before anything is saved. Steps saved by earlier versions look the
  same as before.
- **One row for a helper agent.** When chat hands work to an agent, the
  "Handing this to …" line and the separate live bubble are now one quiet
  row: "Builder is working · Step 2 of 4: … · 1:24", with a ticking clock.
  Open it for the steps. When the agent finishes, the row reads "Builder
  worked for 2 min 5 s · 6 steps" with **Open run**, and its answer
  appears below it as before.

## The chat shows its work more clearly (v1.348.0)

Small touches, with the chat's look unchanged:

- **One line while Jarvis works.** While a reply is being made, one quiet
  line counts up in the same words the finished reply uses ("Working for
  12 s · read 2 files, searched the web"). Only the newest three steps show;
  older ones fold into "N earlier steps", which opens them in place.
- **A soft light sweep** across the step that is running and across
  "Thinking…" replaces the old pulsing. With reduced motion turned on in
  Windows, the text stays still (and so do the spinners).
- **See the change before you approve it.** An approval card now shows what
  will happen: a file edit as a short before-and-after, a new file's first
  lines, a command as one terminal line, and anything else as tidy
  "name: value" lines instead of raw code.
- **Lettered choices.** A question with fixed answers shows them as A, B,
  C…; press the letter to choose. Where the question can be skipped, a quiet
  **Skip** does that.
- **Code in colour.** Code blocks in replies are coloured by language. The
  colouring loads only when a code block appears, so the chat opens as fast
  as before, and it follows your light or dark theme.
- **Pictures.** A picture you attach shows as a small thumbnail before you
  send. Clicking any picture in the chat opens it full screen; arrow keys
  move between the pictures of the same reply, Esc closes, and **Open**
  opens the file in its app (SVG drawings are shown but not opened that
  way).

## A picture made in a new chat appears in that chat (v1.347.0)

In v1.346, a picture asked for as the **first message of a new chat** could
finish without ever appearing there: the chat had not been saved yet when
the request went out, so the finished file had nowhere to go. It went to
the Creative library only, and the bell opened an empty chat page.

- The result now finds its chat through the message that asked for it, so
  it appears in that chat whether it finishes in two seconds or twenty
  minutes, and whether or not you are still looking at it.
- After this update, a picture an earlier version lost this way (in the
  last two weeks) is put into its chat once, the next time the app starts.
- If a result truly has no chat (an agent run started it), the bell opens
  the **Creative library**, where the file is, instead of an empty chat.
- **Fewer failed tries.** When you don't name a model, Jarvis now picks one
  that works from a description alone. Before, the cheapest "image" model on
  Pixio was often an editor that needs a picture to start from, and the
  first try failed. If a model is named that can't do the job (an editor
  with no picture attached, a model for another kind of media, or one that
  doesn't exist), Jarvis is told why and given models that would work,
  without anything being spent. A model name without its `pixio/` prefix is
  fixed for you.

## A tool pack's helper processes always stop with it (v1.346.1)

Some tool packs start through a launcher (`npx`, `uvx`) whose real server is
a second process. Stopping, retrying or removing such a pack could, in a
narrow moment right after it started, leave that second process running on
its own until the next restart. The pack's processes are now placed under
the app's control before they run a single instruction, so stopping the
pack always stops all of it.

## Make an image, a video or a song from chat (v1.346.0)

Ask in plain words ("make me a picture of a lighthouse at dusk", "make a
10-second video of waves", "compose a short jingle") and Jarvis makes it with
the media service you have connected, and the result appears in the chat.

- **Any service you choose.** Pixio is the first; OpenAI images is the
  second; more can be added the same way. With one service connected it is
  used; with more than one, pick the default under Settings → Connections →
  Media (otherwise Jarvis asks you to pick rather than guess).
- **You are asked before anything is spent.** Jarvis gets a price quote
  first. A paid generation shows the approval card with the price in your
  account's own credits ("Generate 1 video on Pixio for 120 credits");
  nothing runs until you press Allow. A **free** generation (for example a
  Pixio Maker daily allowance) runs without the card and the reply says so
  ("free, 4 left today"). If you would rather be asked every time, turn on
  "Ask even when free" in the same settings. A price that cannot be known
  in advance is treated as paid.
- **Long renders finish on their own.** An image usually comes back in the
  same reply. A video or song is started and the reply says it will appear
  in this chat; when it is done, the daemon puts it into that chat (even if
  you have moved on) and the bell says "Your video is ready", linking there.
  A render that was running when the app restarted is picked up again, not
  started twice.
- **The chat looks the same.** The picture, video or audio shows inline in
  the reply and in the files rail, like any other file; the only new things
  on screen are the price line on the approval card and the bell entry.
- A render that fails says why in plain words (not enough credits and how
  many short, a content-policy refusal, "the service is busy, try in N
  seconds"). Keys never appear anywhere, and the download of a finished file
  never carries your key.

## An @ reply comes home (v1.345.0)

When you ask an agent something with `@` in chat ("@hermes-mac-mini do some
tax research…"), its answer now always lands **in the chat you asked in**:

- The chat is saved the moment you send the `@`, and the daemon writes the
  reply into that chat itself when it arrives, even if you have moved to
  another chat or closed the window. The chat lights up as unread in the
  list; open it and the reply is there under your question.
- A reply that arrives after you have moved on is **never** written into
  whatever chat you are looking at. (Before this release that could happen
  with a slow remote agent, and it overwrote the other chat's messages.)
- A remote agent that reports back later, with files, lands the same way,
  files included.
- An agent job started from chat (local or remote, "@builder make me a
  spreadsheet") delivers its reply **and the files it made** into that chat
  when it finishes, whether or not you are still there.
- While an agent works, the chat shows who is working and for how long
  ("hermes-mac-mini is working · 2:13").

One small visible change: a reply that came from an agent job now always
carries the agent's name chip, even when you did not use `@`.

## Jarvis looks after itself (v1.344.0)

When something in the app goes wrong, Jarvis can now find out what, and fix
the common cases, from the chat:

- **It can check its own health.** Ask "something is broken", "why did that
  fail", "is the app healthy", "what errors has the daemon logged" or "why
  does the github pack keep failing", and Jarvis reads the app's own health
  (the model in use and whether it is reachable, failing background tasks,
  packs that did not start or keep failing with their reason and fix, the
  last errors the daemon logged, disk space, the doctor's checks) and a
  pack's last output. These are read-only and need no approval; keys and
  tokens never appear in what it reads.
- **It can make the two common repairs, with your OK.** "Restart the github
  pack" or "re-detect my providers" shows an approval card first (the same
  as Retry on the Tools page and Re-detect on Connections); nothing restarts
  without your press, and neither runs for content Jarvis does not trust.
- **It recovers from a failed step instead of flailing.** Every chat turn
  with tools now carries a short rule: read the error; fix a wrong argument
  once; never retry something you declined; if the failure is the app's
  (a pack exited, a model is down), check the app's health or tell you
  exactly where to fix it; never claim a result it did not get. The second
  failure of the same call is marked so the model changes approach (the
  third was already refused), and a reply now says in a footnote when a
  tool could not run because it was not turned on for the chat or was
  blocked under low trust, not only when you declined it.
- **Handing off to an agent keeps what the chat already did.** When a
  question grows into an agent job, the agent now starts with the files and
  results the chat already read (treated as data, never as instructions),
  instead of reading everything again.
- **A question about a schedule or workflow never arms a writer.** "What
  went wrong with my schedule" used to turn on the schedule-editing tools;
  it now turns on the readers only. An instruction ("delete the failed
  schedule") still arms what it asks for.

## Add a tool pack in one paste (v1.343.0)

*(Since v1.354.0 this area is called **Add a tool**, its first door is
**Bring your tools from Claude, Codex, Cursor…** and the paste door is
**Paste from a website or README (advanced)** — see "Plain words everywhere;
chat knows your apps (v1.354.0)".)*

Settings → Tools has a new **Add a pack** area with three doors that all end
in the same preview list:

- **Paste a config.** Paste whatever a pack's README gives you: the
  `{"mcpServers": …}` block Claude Desktop and Claude Code use, a Codex
  `[mcp_servers.name]` TOML table, a VS Code `"mcp"` block, a single `npx …`
  or `uvx …` line, or just a URL. Jarvis reads it and shows each pack it
  found: its name, how it launches, whether the launcher is installed
  ("Needs Node" / "Uses Node"), whether you already have it, and which keys
  it needs. A key the paste did not include gets a box to type it in. Press
  **Add selected** and they are added and started; a pack that did not start
  says why, like any other.
- **Import from your other apps.** One press lists the packs already set up
  in Claude Code, Claude Desktop, Codex, Cursor, VS Code, Windsurf, Gemini
  CLI, OpenCode, Grok, Antigravity and Pi on this PC, read from their own
  files (never changed), grouped by app, with the same preview rows. A
  placeholder the app would fill at start (`${TOKEN}`, OpenCode's
  `{env:TOKEN}` or `{file:…}`) becomes a box to type the value in — the file
  it names is never read. Pick the ones you want; their keys move straight
  from that file into Jarvis's encrypted vault without passing through the
  page.
- **HTTP server.** A pack that runs as a web service: its address, optional
  headers and an optional bearer token. Tokens and any header whose name
  looks like a secret are kept in the vault.

Every pack row also has **Edit** now: change the command, its arguments, its
settings, folder, address or headers in place. Secrets show as "set" with
Replace or Clear; a value you do not touch is kept. Saving restarts the pack.
The "Add from npm or GitHub" door lets you pick the launcher (npx, uvx or a
plain command) and names the pack the way the app does (dots become dashes),
so a package name is never refused for its spelling.

Nothing here ever shows a key or token back to you, and nothing is written
to your other apps' files.

## A tool pack says why it failed (v1.342.0)

Tool packs (the MCP servers under Settings → Tools) used to fail quietly.
A pack that crashed at start because it was missing a key showed "closed the
connection", its Test button said it had "connected but advertised no
tools", and the message the pack wrote on its way out was thrown away. Now:

- **Its last words are kept.** The last lines a pack prints before it dies
  are kept (passwords and tokens in them are masked) and shown on its row
  under "Last output", in the Test result, and in the error Jarvis reads in
  chat ("pack 'github' exited (exit code 1); its last output: Missing
  GITHUB_TOKEN … If it needs a key or setting, the user can fix it in
  Settings → Tools"). A missing key is now a one-line fix, not a mystery.
- **Test tells the truth.** Test reports the real reason a pack would not
  start, and a crash is called a crash, not "couldn't be reached".
- **The row shows its state.** Starting, "N tools", "Didn't start" or
  "Failing: N calls" (a pack that loads but fails every call used to stay
  green). Retry shows whenever a pack is failed, failing or has no tools. The
  bell tells you the first time a pack starts failing and links to the row.
- **Keys go in the vault.** A key or token typed on the Tools page (any
  setting whose name says token, key, secret, password, auth or credential)
  is stored encrypted in the vault, never in plain text in the settings file,
  and is never shown back; older installs are moved over once at the next
  start. The row lists the key names only.
- **Tidier behind the scenes.** Removing or reconnecting a pack now stops
  the program it started (it used to keep running until the next restart);
  removing a pack also forgets what it had been trusted to do, so adding it
  again starts clean. A failing pack's error text is treated as untrusted
  content in chat, like a web page, so a planted instruction in it is never
  followed.

## Restoring a backup waits out a busy moment (v1.341.1)

Restore from backup (Settings → Maintenance) used to refuse at once if a
background task happened to be touching the database in that instant, with
"the database is still held open". It now waits up to about two seconds for
that moment to pass and tries again; only a database that is really in use
(a running job) still refuses, with the same plain sentence.

## The Local only chip glows when it is on (v1.341.0)

In the chat composer, **Local only** now has a soft glow around it while it
is on, on every theme. Before, the only sign was the label turning green,
which is hard to tell from grey for many people with colour-blindness. The
glow is a change in brightness, not colour, so it reads either way; the label
is also a little heavier. Nothing moves, so there is nothing to switch off
under reduced motion.

Also in this release: the five settings tools a chat can use (`config_list`,
`config_set`, `config_change`, `config_change_protected`, `config_secret`)
now tell the model what arguments they take. Since v1.321.0 they had been
shown with no argument list at all, so "change my default model to X" worked
only when the model guessed `key` and `value` from the description; smaller
models often could not. A check now refuses any tool that is registered
without an argument list.

## Reading a web page brings its links (v1.340.0)

When Jarvis reads a web page for you, it now also gets the page's links as
full addresses, listed after the text ("Links on this page"). Before, it saw
only the words of each link, so when you asked it to dig into a site it
guessed the addresses of the other pages, and the guesses usually did not
exist (a run of "HTTP 404" steps). Now it opens the pages the site actually
links to. Links to stylesheets, scripts, images and fonts, links back to the
same page, and mail or script links are left out; long lists stop at 40 and
say how many more there were. The list sits inside the same "untrusted
content" fence as the page text and is checked for planted instructions the
same way.

## Answers survive a reload everywhere (v1.339.0)

v1.336.0 made the main chat's answers survive a reload; three places still
lost work. Now:

- **The chat beside a Build terminal.** Reloading, leaving Build or
  restarting the window no longer stops an answer that is being written: the
  question is saved as you send it, and when you come back the pane picks the
  answer up (or shows it, if it finished while you were away). Closing the
  terminal still stops it, as before.
- **The browser sidebar.** Its answers already kept going when the panel was
  closed; reopening the panel mid-answer now shows the question and the
  answer so far, and the rest keeps arriving under it (it used to start
  mid-sentence, under no question).
- **Jobs that split themselves into planned steps.** Continue after a restart
  now carries on from the step that was cut off: finished steps are not done
  again (their results are carried forward), and the interrupted step is told
  to check what already happened before redoing anything. Before, the job
  planned again from a short summary and repeated work.

## Every release rehearses an update (v1.338.0)

Two bugs in one week (backups after v1.335.0, Build terminals after an update)
happened only when Iron Jarvis started again on a new version, a path no test
went down. Now every release is checked by an **update rehearsal** before its
installer is built: a real copy of the app, on a throwaway data folder, is set
up with a Build terminal running a program, a backup copy folder, and the
half-finished work a crash leaves behind (a step that was running, a chat
answer being written, an agent job mid-way). It is stopped the way an update
stops it, and again by a hard crash, then started on the same folder. The
release is refused unless a backup is written, the terminal comes back and
types like a new one (drawn by the same terminal Build uses), the cut-off step
is on the record, the chat answer is kept, and Continue resumes the job.

The first rehearsal found a bug: after a **crash**, a terminal could come back
at the size it had up to 30 seconds earlier, and typing into it landed on its
prompt. A terminal that comes back is now started again at the size of your
window the first time you look at it, as long as nothing has been typed into
it yet.

**Lists of work.** When a job works through a list, the agent reports each
item done by its name. Agents often copied the item's name together with its
description, and the app refused it, so finished items stayed open (24 of 160
on one install). The name is now recognised when it is clearly one item; when
it is not, the app names the closest items so the agent can correct itself.

## Build terminals after an update (v1.337.2)

**Typing into a terminal that came back.** After an update or a restart,
Build brings your terminals back with their old screen and a fresh shell in
the same folder. Typing into one used to be choppy: only a few characters
showed, or nothing did, and closing it and opening a new one was the only fix.
Two things were wrong. The new shell's prompt was lost while the terminal was
being restored, and the new shell drew what you typed as if the screen were
blank while the old screen was still showing, so your keys landed on top of
old lines. Now the old screen moves up into the scrollback (scroll up to read
it), the new shell starts on a clean page with its prompt, and typing works
right away. A program that was running there (Claude Code, say) is not
running any more; the Resume strip on the terminal starts it again.

**New terminal opens where you were working.** Build remembers the folder
you last worked in: the folder you picked in the Folders tab, or the folder
of the terminal in front. **New terminal** opens there, after a restart too,
even if you closed the terminal that came back. A folder you pick in the
Folders tab still wins.

## Automatic backups work again (v1.337.1)

From v1.335.0 to v1.337.0 every automatic backup failed with "Background task
failing: auto_backup — PermissionError: [Errno 13] Permission denied". The
backup copies the files in the data folder, and v1.335.0 added one there that
the running app keeps locked so a second copy cannot open the same data
(`daemon.lock`). Windows refuses any other read of a locked file, so the
backup stopped at it. That file and its note (`daemon-owner.json`) belong to
the running app and are left out of backups now. Nothing was lost: backups
taken before the update are still in the backups folder, and because the
newest one is now more than a day old, a fresh backup is taken about a minute
after the updated app starts. If a backup ever fails on a file again, the
message names the file.

To keep a second copy on another drive (D:, a USB disk or a network share),
set **Backup copy folder** in Settings. Do not type it into `config.toml` by
hand: a Windows path in plain quotes (`"D:\Backups"`) is not valid there, and
Iron Jarvis then ignores the whole file and starts with default settings.

## Continue picks up the real conversation (v1.337.0)

Nothing looks different. When a restart or a crash cuts off an agent job and
you press **Continue** (on Everything › Status, in the bell, or on the job),
the job now carries on from its own conversation: the task, every file it
read, every result it saw and every step it took, exactly as they were.
Before, Continue started over from a one-paragraph summary, so the job had to
find its way back (and sometimes redid work).

A step that was running when Iron Jarvis stopped is shown to the job for what
it is: a step that only reads is safe to do again; anything else may already
have happened, and the job is told to check before doing it again.

This covers ordinary jobs. A job that split itself into planned steps still
continues from a summary, as before.

## Chat answers survive a reload (v1.336.0)

Nothing looks different. A chat reply no longer depends on the window that
asked for it.

- **Reload, close the window or switch chats: the answer keeps coming.** Iron
  Jarvis now writes the answer itself, as it goes, instead of the window doing
  it. Come back to the chat (or reload the page) and the reply carries on where
  it is, or is simply there if it finished while you were away. Only **Stop**
  stops an answer now. (An answer nobody has looked at for an hour stops by
  itself, so a question it is waiting on cannot hold it forever.)
- **A restart keeps what was written.** If Iron Jarvis stops in the middle of
  an answer (an update or a crash), the chat shows what it had written so far,
  says it was cut off, and offers Retry.
- **A message sent twice is answered once.** If the same message reaches Iron
  Jarvis twice (a retried connection, two windows), the second one follows the
  answer already being written instead of starting another.
- **Waiting messages are kept.** Messages you queued with Ctrl+Enter while a
  reply was being written are kept with the chat; after a reload they are still
  waiting and go out when the reply finishes, as before.

## Nothing done is forgotten (v1.335.0)

Nothing looks different. Iron Jarvis now keeps a better record of work that a
crash or restart cuts off, and recovers from a few failures by itself. (Ideas
borrowed from the open-source pi-durable agent harness; no code was taken.)

- **A tool that was running when the app stopped is on the record.** Every
  tool call is written down as "started" before it runs. If Iron Jarvis stops
  in the middle (a crash or a forced restart), the next start records that
  call as stopped while running, with the job's other steps and on the
  Activity page. A call that only reads says it is safe to run again; anything
  else says its effect may have landed, so you know to check before running it
  again. Before this, such a call left no trace at all.
- **Each chat totals what it has used.** The chat's ⋯ menu ends with one quiet
  line: the tokens this chat has used and, when it cost money, how much. Work a
  Claude or ChatGPT subscription covers is shown as a list-price equivalent,
  never as money spent. Chats are counted from this version on.
- **"Too long for this model" fixes itself once.** When a model refuses a
  message because the conversation no longer fits its window (often a proxy or
  endpoint whose real limit is smaller than it says), Iron Jarvis leaves the
  oldest messages out, tells the model it did, and tries once more. Your
  question and the work of the current step are never left out. It also
  remembers the limit the model reported, so the next messages fit from the
  start. If even the shortened request does not fit, the refusal is shown as
  before.
- **One copy of Iron Jarvis per data folder.** A second copy started on the
  same data (for example a development copy beside the installed app) now
  refuses to start and names the copy that is running, instead of both writing
  the same database at once.

## Status is a picture (v1.334.0)

Everything › Status no longer repeats the Directory's list of modules. It is
one picture of the whole app: every part of Iron Jarvis is a star around the
reactor, grouped like the Directory (Work, Automations, System, Knowledge,
Setup). A dim dashed star is not set up yet; a ringed star is set up; a bigger,
brighter star was used this week; rings ripple out from a star that is in use
right now; an amber star needs you (a schedule that failed, a goal its safety
breaker stopped). Point at a star for what it is and its numbers; press it to
open that part. Under the map: the last 24 hours of activity and five plain
readings (the model, what is running, runs this week, free disk space and
problems today). The setup checklist, the offer to continue work a restart
cut off and the notes about a failing background task stay at the top.

## Lighter on your PC, steadier over days (v1.333.0)

Nothing looks different. Iron Jarvis now uses far less of your computer while
it runs, and a Build workload can run for days without slowing the PC down.

- **Much less memory.** The app's background service used about 900 MB the
  moment it started, almost all of it reserved by a maths library for 24
  threads it never used. It now starts at under 100 MB. The helper programs it
  starts for Python cells and for Codex's connection to Iron Jarvis used to
  load the whole app each (about 800 MB apiece); they now load only what they
  need (about 15 MB).
- **Background work stays in the background.** Programs started by schedules,
  goals, workflows, queued assignments and the phone, and Build panes an agent
  opens, run at below-normal priority, so the window you are working in stays
  quick. Anything you start yourself keeps normal priority.
- **Nothing is left running after it should stop.** Programs the app starts
  (agent command-line tools, video encoding, git, Python cells, scripts) end
  with the app, even after a crash, and a command that runs past its time limit
  is stopped together with everything it started. Hidden programs a successful
  shell command started (a dev server, for example) keep running until that
  agent's run ends, so the next step can use them; a program with a window you
  can see is never stopped.
- **Closing a Build pane cleans up after it.** Hidden programs the pane started
  (a dev server, a watcher) stop when you close the pane or quit the app. Apps
  with a window you can see (VS Code, a browser, Notepad) are left alone. If the
  app ever stops unexpectedly, the next start cleans up what those panes left.
- **No more text typing itself into a terminal.** When a full-screen program
  (Claude Code, for example) crashed while it was tracking the mouse, every move
  of the pointer over the pane typed something like `[<35;10;5M` into the
  shell. Iron Jarvis now switches that mode off when the program is gone, drops
  stray mouse text sent to a bare prompt, and switches it off for panes
  restored after a restart (the mouse wheel scrolls again there too).
- **Too many background runs at once? The bell asks.** There is still no limit
  by default. When more than four background runs are going at the same time,
  the bell says so once and offers "Queue extras" (only three run at a time;
  the rest wait their turn, shown as Queued) or "Not now" (it stays quiet for a
  day). Change the number in Settings → Agents & automation → "Busy notice
  after", and the limit itself under "Runs at once"; Undo works from the
  Settings changes list like any other setting.
- **Less disk, fewer wake-ups.** Build panes save their history only when they
  print something new (about 8 times less writing with several panes open). The
  health check no longer starts the Claude and Codex programs every couple of
  minutes to ask whether you are signed in. Hidden windows stop polling, a
  stalled service no longer collects a pile of waiting requests, and the quick
  task window frees its memory after ten minutes unused.
- **Small fixes found on the way.** The chat list in the sidebar kept updating
  only for its first twenty changes; a quick task you cancelled was announced
  as "Task done"; the mood orb could spin "thinking" for days after a restart;
  a pop-out window that crashed now reloads like the main window.

## Local-only chats, and a faster app (v1.332.0)

**Keep a chat on this PC.** The "Local only" chip under the message box (or
⋯ → "Keep this chat on this PC") switches it on for that chat. While it is
on, nothing from the chat goes to a model that runs off your own network
unless you say yes for that one message.

- **Picking a cloud model asks first.** Send with Claude, Codex, Grok or an
  API model picked and the message box is replaced by "This chat is local
  only. Send this message to <model>? It will leave this PC." Send once
  (Enter) sends that one message; Keep it local (Esc) keeps your words in the
  box. The next message asks again. A reply sent this way says "sent with your
  OK" on its "answered by" line.
- **Nothing switches on its own.** In a local-only chat a failing model never
  falls back to your default or to another connected service, Auto only picks
  among your own models, and a picture or scan that only a cloud model could
  read is refused by name, with a "Send to <model> once" button.
- **Where "your own network" ends.** Ollama, your custom endpoint and your
  Fleet machines count as local only when their address is this PC, a private
  network address, a Tailscale address or a plain machine name. A custom
  endpoint pointed at an internet address (for example Ollama's cloud) counts
  as cloud. A machine on your network that itself forwards to a cloud service
  still counts as local; the app can only judge the address. (Since v1.362.0 a
  LiteLLM proxy's names are judged one by one: see "A model server that sends
  some names to the cloud".)
- **What else stays home.** Web search and web fetch are switched off in a
  local-only chat. Memory sources outside this PC are skipped. Follow-up
  suggestions, summaries, Share, Commit to memory, Turn into workflow, the
  summary-sheet card and @-mentioned agents all use local models only (an agent
  that runs on a cloud model sits that round out and says so). An agent session
  started from the chat stays local only, and so do its helpers. Another chat
  that @-mentions a local-only chat, or searches past chats, does not get its
  text.
- **Tools that reach the internet always ask.** Browser control, sending a
  message on a channel, shell commands and app packs show their approval card
  in a local-only chat even when the chat is set to approve things for you.
- **Not covered yet.** Voice dictation, read-aloud, memory notes and lessons you
  already saved, a schedule made from the chat (it runs later without the rule),
  and the background reflection after a session.

**Faster.**

- **The model menu stops waiting.** It no longer waits 1.5 seconds checking for
  Ollama when Ollama is not installed, no longer re-runs Grok's 20-second model
  listing on every load, asks every provider and machine at the same time, and
  keeps the list ready between loads (a change to your connections, settings,
  Fleet or sign-ins rebuilds it). A dead server gives up after 1.5 seconds.
- **Fleet machines list their real models** instead of only their default one.
- **Fewer health checks.** A local model that answered in the last 15 seconds
  is not checked again before the next step, so a turn with several tool steps
  checks once. A model that fails is checked again (and refused by name) on
  the next call.
- **A turn gets ready sooner.** Attachments, page text, app resources, linked
  chats, the skill, the folder and the tools are prepared at the same time
  instead of one after another. What the model is sent is unchanged.
- **API-key Claude calls reuse their connection** instead of opening a new one
  every step.

Also in this release: "Retry with the default model" now really uses your
default model (it resent to the model that had just failed). And v1.331.0 made
the Settings cards about 30% wider.

## The calm chat, finished (v1.330.0)

Calm chat, part five: the last loose ends, and a few honest words where the
app used to stay quiet.

- **The reasoning chip says the level the model will use.** With nothing
  picked, the chip shows the model's own default when the vendor documents
  one: "Medium", "Thinking off", "Auto". The menu's first row reads
  "Default (Medium)" and sends nothing; "Medium" below it pins medium. When the
  default is not known the chip still says "Reasoning" and its tooltip says the
  model decides.
- **A picked level works on the newer Claude models.** With an Anthropic API
  key, Claude 4.6 and newer (and the Fable and Mythos models) get the thinking
  format Anthropic now documents for them; older models get exactly what they
  got before. Signed-in Claude (the `claude` command) was never affected.
- **"no tools" when a reply ran without them.** If you pick a model that has
  not shown it can use tools while the chat is on Auto tools, the reply is text
  only, and the "answered by" line now ends "· no tools". The model is also
  told it can only answer in words this turn, so it no longer promises to hand
  work to another agent; if it still claims a hand-off, a short note says
  nothing was handed off.
- **Servers are named by their label.** "answered by" names a saved server by
  the label you gave it ("Spark proxy"), with its id in the tooltip.
- **Removing a proxy removes its models.** On the Fleet page, removing a proxy
  server takes the models it listed with it. A model listed by a proxy has no
  Remove of its own (it would come back on the next check); remove the proxy
  instead.
- **Anthropic-compatible servers, proven on a real one.** Tested against a real
  LiteLLM proxy, including an error it sends partway through a reply, which now
  shows as a plain refusal in the proxy's own words.
- **The last boxes go.** Delete, Disconnect and Remove buttons and the status
  chips on Settings > Connections, Fleet, Workflows, the Agents team, project
  Tasks and the Board are quiet now: no border, a small coloured dot says the
  state. Saved-endpoint rows put their tags on one line and their buttons on
  another. The Agents page waits with one quiet "Loading" line.
- **No empty Draft card.** A reply with an empty email block no longer draws an
  empty card with Send and Copy.

## One calm app (v1.329.0)

Calm chat, part four: the quiet look now reaches every corner (DeepSeek
Harness ideas only).

- **One chat list everywhere.** The sidebar on every page, and the phone
  menu, show the same list: chats under their projects, with a dot when one
  is working, waiting for you or unread, and a short age. Inside a project
  chat, that project comes first and the other projects stay listed (folded,
  with their dot), so work elsewhere is never hidden. "Only <project>" is a
  small filter above the list. The list away from the chat page has a search
  box too. Two chats with the same title show a time or a day instead of the
  age, so you can tell them apart. On a phone, **Chats** at the top of the
  chat opens the list; the old boxed chat rail is gone.
- **The open chat's ⋯ does it all.** Rename, Pin to top, Archive, Commit to
  memory, Turn into workflow, Add to project and Delete chat are in the top
  bar's ⋯, not only on the list row.
- **@ finds files and folders.** In a project with a folder, "@" also offers
  that folder's files and folders: a file is attached, a folder becomes the
  working folder. Chats in the @ menu show their date, and agents show their
  names.
- **Steps say what they did.** The "Worked for …" line and the reply details
  both read "Read harbor.xlsx", "Searched the web for pier 9 hours", "Ran
  excel_query", not tool ids. Only a short, safe part is kept (a file name,
  the start of a search, a web address's site), never keys or full paths.
- **One name for the model.** The model chip says "Opus 4.8" and the reply
  says "answered by Opus 4.8", even when another model stood in. Warnings in
  the reply details are plain sentences, for example "Mock answer. No real
  model ran."
- **Edits show what changed.** A reply that only edited existing files now
  shows the "N files changed" line too.
- **The Build page side chat** has the same calm look as the chat: the shared
  safe text (pictures from model-written links wait for your press), the
  "Worked for …" line, "N files changed", the permission chip, and a question
  that takes the message box's place. It reads properly in Daylight. The rest
  of the Build page lost its boxes: terminals are a plain list, Folders and
  Files are text tabs, and a missing folder is one plain line naming it.
- **The project panel, Tasks, Board and Media** are plain sections without
  cards. The panel's folder picker is the calm one, and its Terminal button
  opens a terminal on the Build page.
- **Anthropic-compatible servers.** In Settings → Connections, a custom
  endpoint has a **Server type**: OpenAI-compatible or Anthropic-compatible.
  **Fetch available models** sets it to the way the server answered. Replies
  then use that server's own API (an Anthropic-compatible server is asked at
  /v1/messages with its key). Like every custom server, it starts with tools
  off until **Verify tools** passes, and if it is down it says so; Iron Jarvis
  never switches to another provider on its own.
- **A refusal says what happened.** When the model you asked for cannot
  answer, the chat shows one plain line naming it (by the name you gave a
  custom server) with Retry beside it. No banner claims a stand-in answered,
  because none did. A background job that nothing could answer still gets a
  notice at the top, with a link to Connections.
- **A new server shows up at once.** A custom server you just saved is
  listed straight away as "not checked yet", then fills in once Iron Jarvis
  has checked it. The Fleet page names it by the way it talks
  (OpenAI-compatible or Anthropic-compatible).
- **The Agents page is calm too.** One message box for the objective, with
  Your projects, Team and the live work as plain sections below it.
- **Smaller things.** "Jump to latest" is a quiet chip and steps aside while
  a menu is open. Text uses whole-pixel sizes and theme colours throughout the
  chat, the Build page and the mission screen, so Daylight reads right. The
  mission screen's wording is plain too.

## Point at another chat, see what changed, archive (v1.328.0)

Calm chat, part three (DeepSeek Harness ideas only).

- **Point a message at another chat.** Type "@" in the message box: under
  the agents there is now a **Chats** section that searches your saved chats
  as you type. Picking one adds a small chip inside the message box (up to
  three; the × removes one). Jarvis reads those chats as reference for that
  message, and the reply details say "Read 2 earlier chats: …". A queued
  message keeps its own chats. Archived chats are not offered.
- **What a reply changed.** When a reply wrote files, a quiet line under it
  says how many and how much ("2 files changed +14 −3"). Press it to see each
  file; hover a file for the first changed lines, press it for the full
  side-by-side changes, and use Undo where Undo already applies. If a file
  changed again afterwards, the line says "changed since".
- **Archive a chat.** In the chat list, a chat's menu has **Archive**. If the
  chat is still working, you are asked first and "Stop and archive" stops it;
  anything that could not be stopped is named, never hidden. Archived chats
  leave the list; **Archived (N)** at the foot of the list shows them, with
  Unarchive and Delete. Opening an archived chat brings it back. A phone
  message into an archived chat also brings it back.

## Chat list, permissions and reply details (v1.327.0)

Calm chat, part two (DeepSeek Harness ideas only).

- **Chats are grouped under their projects.** The chat list on the left (and
  the Chats drawer on a phone) shows each project as a quiet heading with its
  chats under it; pinned chats come first and chats with no project last.
  Each chat shows a short age ("12m", "3h") and a small dot: amber when it is
  waiting for you (an approval or an app's question), a softly pulsing dot
  while it is working, and a small dot when it changed since you last looked.
  While a chat is working, the list checks again every few seconds.
- **One permission chip.** The approvals menu in the message box is now a
  single chip with three plain levels: **Ask first** (asks before it edits
  files, uses the internet or runs commands), **Ask when risky** (edits files
  and searches the web on its own, asks before running commands) and
  **Don't ask** (use with care). It works with the keyboard and its menu is
  never cut off on a phone.
- **The reply details say what the answer drew on.** Open "answered by …" and
  you see "Followed the project's AGENTS.md and CLAUDE.md" when the project's
  rule files were used, and "Read 2 earlier chats: …" when earlier chats were
  part of the answer. An earlier chat that had to be left out is named, in
  amber when it matters.

## A calmer chat (v1.326.0)

Ideas from DeepSeek Harness (MIT; ideas only, no code taken). The message box
is now the only box on the chat page; everything else is plain text on the
page.

- **A new chat starts in the middle.** "What can I help with?", the project
  you are in just above the message box, and three quiet suggestions under
  it ("More ideas" shows the rest). After your first message the same box
  moves to the bottom, keeping what you typed.
- **Everything is inside the message box.** "+" (files and tools), the
  project, approvals, tools, Web, the model, the microphone and Send all sit
  in the box's bottom row. Under it, one quiet line of key hints; while Jarvis
  answers it says what Enter, Ctrl+Enter and Esc do.
- **A slim bar on top.** The conversation's name, the Chat / Tasks / Board /
  Media tabs and, on the right, **Project** (opens the project's panel from
  the side), **Share** and **⋯** (everything else). Esc closes the project
  panel, except while you are typing in one of its boxes.
- **Replies are just text.** No box around Jarvis's answers; your own
  messages sit in a soft tinted bubble. Tables have thin lines and charts no
  frame. The buttons under a reply (copy, try again, like, read aloud, keep)
  are one quiet row.
- **The work folds away.** While Jarvis works you see one grey line per step
  (thinking, a tool, the current step); when it is done they fold into one
  line, such as "Worked for 3.2 s · read 2 files, ran 1 tool", that opens
  again on click.
- **"answered by …" ends each reply.** The model that answered is named at
  the end of the reply's button row; open it for the full details. Warnings
  (a different model answered than the one you picked, or a stand-in
  answered) still show in amber.
- **A question takes the message box's place.** When Jarvis or an app needs
  you (an approval, an app's question, an app asking to use the model), it
  appears where the message box was. Enter allows or sends, Esc declines,
  and "1 of 3" shows when several are waiting. What you were typing is kept
  underneath.
- **Your project's own rules are read.** When a chat is in a project whose
  folder has `AGENTS.md` or `CLAUDE.md` (or `AGENTS.local.md` /
  `CLAUDE.local.md`), Jarvis reads them on every message, checked for hidden
  instructions like any attached file and kept to about 8,000 characters
  together. Turn it off in Settings ("Read a project's AGENTS.md and
  CLAUDE.md in chat").
- **Fetch the models a custom server has.** Settings → Connections → a custom
  endpoint now has a **Fetch available models** link: it asks that server
  only when you press it, the list can be searched, and the key goes only to
  that server. The automatic check while you typed the address is gone.
  Since v1.329.0 it also asks Anthropic-compatible servers (see "One calm
  app").

## Charts, versions and more in chat (v1.325.0)

Ideas from assistant-ui and tambo (MIT; ideas only, no code taken).

- **Charts in replies.** When numbers read better as a picture, Jarvis can
  answer with a bar, line or pie chart drawn right in the reply. "Show as
  table" flips it to the numbers and "Copy data" copies them for a
  spreadsheet. A chart Jarvis could not draw shows as plain text instead.
- **Tables you can work with.** Every table in a reply can be sorted by
  pressing a column heading (press again for the other direction, a third time
  for the original order), copied as CSV or downloaded as `table.csv`. What
  you copy is what you see, in the order on screen.
- **Ask Jarvis about this page.** On any page, press Ctrl K and type "explain
  this page" (or "summarize this page", "what is this"). Chat opens with an
  "About: <page>" chip; what that page showed goes with your next message only
  (and again if you press Try again). Typed-in boxes, the Build terminal
  screens and hidden parts of the page are never included, and the page text
  is checked for hidden instructions like an attached file.
- **Earlier versions are kept.** Editing a sent question, or pressing Try
  again on an answer, no longer throws the old one away: a small "‹ 1 / 2 ›"
  appears and flips between versions. Only the version on screen is sent to
  the model. Up to 10 versions are kept per message (past that the oldest
  goes). A Try again that fails before writing anything puts the earlier
  answer back.
- **Try again with another model.** The arrow beside Try again lists your
  models; the one you pick answers THIS reply only, and the conversation keeps
  its own model. When the answer came from a model on this computer and you
  pick one that is not, the menu says the conversation will leave this
  computer before you press it.
- **Queue a message.** While Jarvis is answering, Enter still sends a steer
  note; Ctrl+Enter instead holds your message (and its files) and sends it
  when the answer finishes. Up to three can wait. After a Stop or an error
  they wait for you: "Send now", "Edit" or ×.
- **Quote a part of an answer.** Select some text in a reply and press Quote:
  it goes into the message box as a quote, ready for your question.
- **Conversation map.** "Map" under the message box lists every question in
  the chat; pick one (arrow keys and Enter work) to jump back to it.

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
  its time ("Read harbor.xlsx · 0.3 s", a failed one marked), with "First word after
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
**v1.289.0 – v1.294.0:** changes to the old Overview's module tiles (dragging,
screens of ten, then three groups). The calm layout (v1.321.0) replaced the
Overview: **Everything** lists every page, and its **Status** tab shows what
the Overview used to.
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
