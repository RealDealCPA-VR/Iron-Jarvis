# Vocabulary — one name per concept

The rule: a user meets **one word per idea**, everywhere — on screen, in
search, and (phase 2) in the assistant's own sentences. Before v1.113.0 the
same Notion memory was a *base* where you created it, a *source* in the filter
beside it, a *brain* in directory copy, and a *connector* in chat's "+" menu.
Nobody hit an error; they hit hesitation — and hesitation never files a bug
report, it just stops opening the app.

`dashboard/__tests__/vocabulary.test.ts` enforces this table. If you're naming
something new, look here first; if the idea isn't here, add it here in the same
PR that ships it.

## The canon

| Concept | The one name | Retired words (kept as search aliases) |
|---|---|---|
| The program on this PC that does the work (what the window talks to) | **Iron Jarvis** | daemon |
| One piece of work Jarvis does for you: what a chat hands an agent, a step of an objective, a queued task for an agent, a schedule's fire, a task another Jarvis sent | **task**; one attempt at it is a **run** | job, assignment |
| Anything recall reads | **memory base** | source, memory source, brain, LTM |
| Anything the app talks to | **connection** | integration, connector |
| An MCP server (a connection that adds tools) | **extension** | plug-in, pack, tool pack, MCP pack |
| The built-in stand-in that answers when no AI model is chosen (provider id `mock`) | **demo model**; what it says is "a scripted demo, not real answers"; under one reply: "Demo answer. No AI model ran." | mock, offline mock, offline demo, offline model, simulated mode |
| One shell or CLI window on the Build page | **terminal** | pane |
| Build's other view, where Jarvis writes, runs and checks code in a folder, one conversation per session | **Coding sessions**; one of them is a **coding session** | IJ harness, harness |
| A coding session's reply drawn on one time axis, the tab beside Plan (v1.390.0): the model you talk to, each step it took, each helper with its model | **Timeline**; its pieces are **steps**, **model calls** and **helpers** (the run log's words) | trace, span, waterfall, orchestrator, LLM call, round (never shown) |
| Anything Iron Jarvis knows how to do | **skill** | — |
| Where alerts go | **Notifications** (page) / **destination** (row) | channels |
| A destination you can talk back to | **two-way destination** | gateway, messaging platform |
| The one-tap connect gallery | **Directory** | marketplace |
| The one thing you ask the team to get done (the Agents page's New task; a project's screen; the bell's "Your objective is done") | **objective** | mission |
| Several agents answering one @-mention message in chat (each conversation is a *thread*, its seated agents the *panel*; pre-v1.308.0 rooms open read-only) | **round-table** | — |
| The user's own Chrome/Edge, driven by Jarvis | **Your browser** | browser bridge, chrome extension, browser agent |
| A Jarvis-owned browser profile (phase 2) | **Jarvis browser** | managed browser, headless browser |
| The thing you load into Chrome so Jarvis can reach it | **browser add-on** | chrome extension, browser extension |

Two-way is a per-destination *upgrade*, not a new noun (decided in the
messaging plan, shipped v1.136.0): the toggle that creates one reads
**"Chat with Iron Jarvis from this destination"**. "Gateway" and "messaging
platform" never shipped as user-visible words, so they are not search aliases
in `lib/nav.ts` — they are retired-on-arrival; if one ever gets added to
search, it goes in as an alias only.

### mission → objective, and the round table moves to chat (v1.309.0)

Since v1.308.0 the Agents page is one composer: you give Jarvis **one
objective** and the team works it. The screen already said "objective"
("Recent objectives", "Your objective"), while search, the Guide and this
table still described the Agents page as a round table where agents "talk it
out" — so a user who typed what they saw found nothing, and the Guide
confidently described a screen that no longer exists. **objective** is the
user's word; "mission" survives in the URL (`/agents?mission=<id>`) and the
API (`/missions`) and is kept as a search alias. The round table did not
vanish — it is the panel that answers an `@`-mention in chat — so its row
stays, re-scoped to where it lives now.

### One unit of work, one demo, one Iron Jarvis (v1.357.0)

The readiness audit counted the names a new user met for ONE thing. A piece
of work was a session, a run, a task, an objective, a Mission, a job and an
assignment; the demo model had six names ("Mock answer", "offline mock",
"Simulated mode", "the offline demo", "the built-in offline model", "Demo
replies"); Build said terminal on one line and pane on the next; a dozen
error lines still said "the daemon". So:

- **task** is the unit of work. A **run** is one attempt at a task (a task
  that is retried has two runs). The team's one task is still an
  **objective** (row above). "Session" is the engine's record of a run and
  names the Advanced Sessions page. It is NOT retired yet: about 120 other
  lines still say it (the run page, the board, the bell's message row), so
  the scanner does not police it; folding those into task/run is a later
  wave's job, and this note goes when it ships.
- A task another Jarvis sent reads **From <caller>** wherever its origin is
  shown (origin `remote:<caller>`, `OriginChip.originLabel` and the bell).
- The **demo model** is the one name for the scripted stand-in, and every
  surface takes its words from `dashboard/lib/demoWords.ts`. Iron Jarvis's
  own sentences say the same: the note under a demo reply in chat (both
  lanes), a teammate's route note, the browser sidebar's line, the
  connections registry's name for the row and the refusals that need a real
  model. `tests/test_one_vocabulary_v13570.py` reads `demoWords.ts` and
  drives each one.
- **Iron Jarvis** is the program; a version too old for a feature is "this
  version of Iron Jarvis". A real file name (`daemon.log`) is a file name.
- **terminal** is one window on the Build page; the page itself is Build.
  The Windows toast and the update dialog say terminal (and background
  **task**) like the bell (`desktop/main.js`, pinned beside
  `lib/paneAsks.ts`).

`dashboard/__tests__/vocabulary.test.ts` now SCANS: every string literal and
JSX text node in `dashboard/app`, `components` and `lib` is read through the
TypeScript parser (`__tests__/helpers/retiredWords.ts`) and a retired word in
anything a user can read fails the test. Comments, ids, test ids, routes,
object keys, comparisons, matcher arguments and search aliases are not prose
and are skipped by rule. One shape cannot be told from an id and is not read:
a single lower-case word kept in a variable or an object value and shown
later (`{ mcp: "pack" }` then `{LABEL[k]}`) — write a label capitalised or in
a sentence and the scan sees it. The few deliberate uses are listed in the test's
allowlist, each with a one-line reason, and an allowlist entry that no longer
matches anything fails too (so a fixed line takes its entry with it).

### Beyond the dashboard (v1.358.0)

The dashboard scan reads only `dashboard/app`, `components` and `lib`, so the
words Iron Jarvis and its desktop shell write THEMSELVES still reached people
unchecked: the tray said "the daemon keeps stalling", the startup dialog's
title was "Iron Jarvis — daemon did not start", a schedule with no reason
pointed at "the daemon log". `tests/test_vocabulary_beyond_dashboard_v13580.py`
is the same rule on the other side, reading the SAME retired-word list
(`dashboard/__tests__/helpers/retiredWords.ts`, parsed, never copied):

- **desktop/main.js** — every string a person can read (tray tooltips,
  Windows notifications, dialogs, menu labels, the startup-failure reasons
  shown under "Details:"). Log lines (`desktopLog`, `fileLogger`, `console`)
  and ids with no space and no capital (`"daemon"`, `"pane:"`) are code and
  are skipped, exactly like the dashboard scan. The crash-loop words are also
  driven under node with the code's own label, because a `${label}` piece is
  invisible to a static read.
- **settings/schema.py** — every label, help line, option label, group name
  and placeholder (search aliases are kept on purpose and not read).
- **scheduling/run_outcome.py** and **comm/notifier.py** — every sentence
  they hold (docstrings, log calls, dict keys and comparisons are not read).

Its allowlist works like the dashboard's: each entry names its file, a piece
of its text and why, and an entry that no longer matches anything fails.
The program is **Iron Jarvis** in all of these; where the desktop has to tell
its two children apart, the window's own server is **the Iron Jarvis
dashboard**.

### Everything Iron Jarvis answers (v1.360.0)

The v1.358.0 scan still read a FIXED list of daemon files, so sentences
outside it reached people in retired words: the Status map's "N of M tool
packs running" and "N open panes", an extension's failure reason "the pack's
server couldn't be reached" (with a fix pointing at "Tools → Connected
packs"), the queue's "no assignment …" / "assign the job to a worker agent",
"This mission is still running", "no such connector", "check the daemon
logs". `tests/test_vocabulary_daemon_answers_v13600.py` widens it two ways,
so a new sentence is read the day it lands:

- **every sentence** in every `daemon/routes/*.py` (a glob, not a list), the
  extension failure words (`mcp/tools.py`), the import door
  (`mcp/import_config.py`, `import_sources.py`, `pack_door.py`), the request
  models' 422 sentences (`daemon/schemas.py`), the Directory's card lines
  (`connectors/service.py`) and the queue (`assignments/`);
- **every answer position** anywhere else in `src/iron_jarvis`: an
  exception's message (`HTTPException(detail=…)`, `ValueError("…")` — a route
  hands `str(exc)` back) and any value under an answer key (`detail`,
  `reason`, `fix`, `note`, `error`, `summary`, `title`, `message`, `hint`,
  `words`, `held_reason`, `blocked_reason`, `why`).

Its `ALLOW` holds the deliberate uses (a stored vault description the Secrets
page reads as a marker, a prompt only the model reads, the file name
`daemon.log`); its `PENDING` holds lines in files a wave's lane did not own,
reworded by the integrator and deleted with their fix. The words that
replaced the retired ones: an MCP server is an **extension** ("the
extension's server exited", "Tools → Connected extensions", "N of M
extensions running"); a Build window is a **terminal** ("N open terminals",
"This terminal's shell has ended — open a new terminal."); a queued unit of
work is a **queued task** ("no queued task 'x'", "the supervisor cannot take
a queued task"); the team's one task is an **objective** ("objective not
found"); a Directory row is a **connection** ("no such connection"). Ids,
keys, routes and API fields (`assignment_id`, `/assignments`, the
`pack_restart` tool) are the wire and keep their names. "plug-in" is retired
here but not on the scanner's word list, so the run-without-asking note
("extension tools") is pinned by hand.

### IJ harness → Coding sessions (harness gaps, after v1.388.2)

Build has two views. The second — where Jarvis works on code in a folder,
one conversation per session, and keeps going when you leave — shipped as
the **IJ harness** (v1.369.0), the engine room's name for the thing that
drives a coding model. The v1.388.0 re-audit (0g.6) found it on Build's view
switch, the view's sidebar, the bell ("A change to a harness file is
suggested") and two terminal refusals ("relaunch the harness"), and no Help
entry at all. The user's word is **Coding sessions** (confirmed 2026-10-25):
the switch reads "Terminals | Coding sessions", one session is "a coding
session", and "New session" stays inside the view. What runs in a Build
terminal (Claude Code, Codex) is **the coding tool** ("start the coding tool
again in the Build terminal"). Build's one-line blurb (Ctrl K, Everything,
Help) names both views.

The dashboard reads the name from ONE place, `dashboard/lib/buildWords.ts`,
so a different choice is a two-line change. "IJ harness" and "harness" stay
search aliases on Build (`lib/nav.ts`). "harness" is on the scanners' retired
list; the deliberate uses are allowlisted with a reason: the real
`.ironjarvis/harness` folder and its files (a folder name is a name), the
`/history` API's `harness` parameter, the `ironjarvis bench-harness`
developer command, and two programming-error messages no request can reach.
The wire is unchanged: the `harness/` package, `ij.build.shape` value
`"harness"`, test ids and routes. "Build session" (a session in Build) is not
retired yet; it is still written in the bell, the desktop notices and the
daemon's sentences, and a later wave folds it into "coding session".

## Reserved

- **Pack** — reserved for the future staff-export bundle ("hand your setup to
  firm staff as a Pack", decided 2026-07-25). Do not use it for anything else;
  freeing this word was the point of renaming MCP packs away from "pack".

### plug-in → extension (v1.216.0)

From a UX review of the Tools page: *"'Plug-ins (MCP)' is insider jargon on a
first-run screen."* The word was chosen to free up "pack", which it did — but
it never told a first-time reader what the thing IS, and the page it leads is
the one place a new user meets the concept.

**extension** is the word the surrounding software already uses for "a separate
program that adds abilities" (browsers, editors, IDEs), so it arrives already
understood. The section that hosts them says *Extensions* and keeps the
protocol in a parenthetical for people who know it: MCP is the wire, not the
noun. "plug-in" joins "tool pack" as a retired word kept as a search alias, so
anyone who learned the old name still finds the page.

### Computer Control → Browser, and why the add-on is not an "extension" (v1.235.0)

Two things arrived at once and the second one nearly broke the rule above.

The page at `/computeruse` was called **Computer Control**, which described the
mechanism (a headless Chromium the app drives) rather than the thing the user
came for. It is now **Browser**, and it hosts two modes with two names: **Your
browser**, the Chrome the user is logged into, reached through an add-on they
load themselves; and **Jarvis browser**, a profile the app owns, which is phase
2 and says so. The route stays `/computeruse` — identities are contracts, see
the three-layer rule below.

The trap: the thing you load into Chrome is, in Chrome's own words, an
extension — and **extension** was already spent here on an MCP server
(v1.216.0, above). One word cannot mean two things on the same screen, and the
MCP meaning is older, load-bearing, and printed on a page users already know.
So the browser side yields: it is the **browser add-on** everywhere a user can
read it. Engineering identifiers keep Chrome's word (`extensions/chrome/`,
`extension_id`, `chrome-extension://`) because those are the wire, not the
noun.

## The three-layer rule

1. **What users read** (titles, labels, copy, tooltips, toasts) — follows this
   table, no exceptions.
2. **The wire** (API routes, request fields, config keys, source *names* like
   the builtin `brain`) — never renamed for vocabulary reasons; identities are
   contracts.
3. **What the model reads** (tool descriptions, system prompts) — should follow
   the table too, because the model's replies teach users vocabulary; migrate
   deliberately and re-test agent behaviour when you do (phase 2, open).

## Allowed exceptions

- Third-party concepts keep their own names: a **Slack channel** is Slack's
  word; a **Notion database** is Notion's.
- Identifiers shown as identifiers (the `brain` source id in a picker) are
  data, not prose.

## When search vocabulary changes

Every rename adds the OLD word to the page's `aliases` in `dashboard/lib/nav.ts`
— muscle memory must keep working forever. `nav.test.ts` pins the friction
aliases; extend it when you retire a word.
