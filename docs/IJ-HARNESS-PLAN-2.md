# IJ harness, part 2: make it feel like Claude Code

Written 2026-10-22 against v1.371.0, for the user to review **before anything
is built**. Part 1 (`docs/IJ-HARNESS-PLAN.md`, v1.363.0 – v1.371.0) built the
harness: every coding tool, each model's own tool dialect, sessions that keep
running, one account per session, rewind, the library, pictures and the dev
server screenshot. This part closes the gaps that make the IJ harness still
feel different from the Claude Code desktop app.

Every phase is built the way part 1 was: a doer agent, then a separate
reviewer agent that tries to break it, each fix pinned by a test that fails
on the old code, a live run on a scratch daemon (never the user's app), and
one release per phase.

## Already at parity (no work planned)

| Claude Code desktop | IJ harness today |
|---|---|
| Pick model and effort | Model menu and reasoning chip |
| Ask / Accept edits / Plan / Bypass | Approvals chip and Plan first |
| Stop, steer while working, queue a message | Stop, "add a note while it works", Ctrl+Enter queue |
| Rewind to a message | Rewind to here (all or nothing, through Undo) |
| Thinking shown | Thinking folds under the reply |
| Sessions keep running in the background | Daemon-owned turns; rejoin after a reload |
| Terminal, diff, files, plan panes | Terminal, Changes, Files, Plan panes |
| Paste a picture | v1.371.0 |
| Look at the dev server | v1.371.0 (screenshot) |
| Speed | Measured: Jarvis on Claude passed 30/30 in half Claude Code's time |

## Decisions for you before building

These change what gets built. Each has a recommendation; nothing starts until
you answer.

**Decided 2026-10-22: the user accepted all six recommendations** ("I accept
all six recommended decisions. Start Phase 1 … and ship each phase when it's
green"). The phases are built and shipped in order.

1. **Branches for coding sessions (Phase 4).** When the folder is a git
   repository, should a new session work on its own branch in a separate
   worktree?
   - *Recommended:* yes by default, with a switch on New session to work in
     the folder directly (today's behaviour). A session that is not in a git
     folder works as today.
2. **Where worktrees live.** *Recommended:* beside the repository, in
   `<repo-parent>/<repo>.ij-worktrees/<session>`, so they never show up as
   changes inside the repository. The alternative is inside the repository's
   `.ironjarvis` folder, which needs a `.gitignore` entry.
3. **Finishing a branch.** *Recommended:* the session offers **Create PR**
   (when GitHub CLI is signed in) and **Merge into <base>** (a normal merge,
   never a force push, never a rewrite of the base branch), plus **Discard**
   (the worktree folder goes to the trash; the branch stays until you delete
   it).
4. **Helper agents (Phase 1).** How many may a session run at once, and do
   they count against the same account?
   - *Recommended:* at most 3 at a time, always on the session's own account
     and model (never another provider), each with the session's folder and
     approval setting.
5. **The agent's browser (Phase 5).** *Recommended:* an invisible browser the
   agent drives (your installed Edge, a throwaway profile), with its latest
   view shown in the Browser pane. The alternative is driving the pane you
   see, which would need the desktop app's own browser and only works while
   the window is open.
6. **Order.** The phases below are ordered so each builds on the last and the
   cheapest wins come first. The biggest single change (git) is Phase 4; say
   if you want it first instead.

## Phase 1 — Helper agents and skills in a session (target v1.372.0)

**What you see.** You ask "find every place we read the config and list the
ones that ignore the user's setting". The session starts two or three helper
agents that search in parallel. Each shows as one row in the conversation
("Helper: searching src/ … 12 files read"), which opens to its steps. The
main agent then answers with what they found. Skills from the folder and your
skill library load when the task matches.

**How.**
- Arm `delegate`, `spawn_agent`, `list_agents`, `skill_search` and
  `skill_load` in a harness turn (`harness/mode.CODING_TOOLS`). They exist
  and the parity table counts them, but no harness turn turns them on today.
- Give the dialects their shapes: Claude Code's `Task`/`Agent` tool in the
  Claude dialect, Codex's `spawn_agent` in the GPT dialect, translated to the
  real tools like every other dialect tool.
- A helper inherits the session's folder, account pin, model, Local only
  setting, trust and approval posture (yolo becomes approve-for-me, the
  v1.232.0 rule). It never moves to another provider. At most 3 run at once
  per session.
- Show helpers with the chat's existing helper row (`AgentRunRow`) and stream
  their progress through the mission screen's member mirroring
  (`StreamHub.link`). Stop stops the helpers too.

**Done when.**
- On a scratch daemon, a Claude session and a Codex session each start at
  least two helpers for a search task, the rows appear while they run, and
  the answer names files only the helpers read.
- Stop during the helpers ends every helper (no process left, checked by
  process id).
- With Iron-Proxy pinned to account A, every helper call goes to account A
  (the lease log shows no other account).
- A Local only session's helpers stay on the local model.
- The 30-task benchmark on Claude with helpers armed passes at least as many
  tasks as the v1.370.4 run (30/30) and its time does not grow by more than
  25%.

**Result (v1.372.0, 2026-10-22).** Live on scratch daemons (Iron-Proxy off,
this PC's own logins, the task "list the files under src/billing and
src/shipping that ignore the timeout setting", 7 files, 3 that ignore it):

| Check | Outcome |
|---|---|
| Claude session | 3 helpers (2 started together, 1 follow-up), all on claude-cli; the main agent made no tool call but `delegate`; all 3 files named; 123 s |
| Codex session | 2 helpers started together, both on codex-cli; main agent only `delegate`; all 3 files named; 117 s |
| Stop during helpers | 2 helpers running; the 7 processes the turn had started (2 Claude CLIs among them) were all gone 5 s after Stop; both helper runs ended "cancelled" |
| Local only session (glm-5.3-flash) | Local only on (`route.local_only: true`); 2 helpers, both on custom / glm-5.3-flash; all 3 files named; 87 s |
| Pinned Iron-Proxy account | Proven against the fake Iron-Proxy in the tests (every helper call is a pinned pick of the session's account); not driven against real accounts |

Two things the live run found and this release fixes: starting a helper put
up an approval card under the default setting, so the first live turn
parked on it (helper starts are now granted; what a helper does still asks),
and a session with no Iron-Proxy account could still be moved to the default
provider when its model failed (`providers/hold.py`). A second review of
those two changes found seven more gaps, each fixed and pinned red-first:
under "Ask before every action" a granted helper could edit files with no
card; reading a picture or scanned PDF, a helper's compaction and a
decomposed helper's planning could still reach another provider; the hold
leaked into background work an event started; a model without vision could
"describe" a picture it never saw; and Claude's background agents could run a
custom agent pinned to another model.

Benchmark (`bench-harness run --runner jarvis --provider claude-cli --tier
all`, helpers and skills armed): **30 / 30**, mean **30.7 s** per task
(v1.370.4: 30 / 30, 31.1 s), 6.8 tool calls, list-price equivalent $3.57
($3.66). No task started a helper or loaded a skill: the 30 tasks are small
and single-folder, and the model worked on them directly. So this shows that
arming the new tools costs nothing; it does not show helpers making a task
faster.

## Phase 2 — `/` and `@` in the message box (target v1.373.0)

**What you see.** Typing `/` opens a list: the folder's and your skills, plus
the session's own commands (`/plan`, `/compact`, `/rewind`, `/model`).
Typing `@` lists files in the session's folder as you type (fuzzy match);
picking one adds it to the message the way an attached file is.

**How.**
- Reuse the chat page's pieces: `lib/slash.ts` for the `/` menu and
  `lib/atMenuFiles.ts` / `atMenuRows.ts` for `@`, reading files from the
  session's folder (`GET /fs/files`).
- A picked file rides the turn as an attachment path (the lane already reads
  attached documents and pictures). A picked skill is loaded for the turn,
  as the chat's `/skill` playbook is.
- Session commands call what exists: `/plan` = Plan first, `/rewind` = the
  rewind list, `/compact` = Phase 3's Compact, `/model` = the model menu.

**Done when.**
- Screen tests: `/` lists skills and commands, filters as you type, Enter
  picks; `@` lists the folder's files, picks one, and the turn body carries
  its path; Escape closes either list without sending.
- A live session answers a question about a file picked with `@` that it
  could not have found by name alone.
- The menus never cover the box on a phone-width window (390 px check).

**Result (v1.373.0, 2026-10-22).** Doer + reviewer (10 findings fixed, each
pinned red-first; the worst: five uploads started a turn that silently read
four, and Enter while a list was loading sent the half-typed "@notes").
- Screen tests: the `/` and `@` lists, picks, Escape, the queue and the read
  limit — 34 in the two Phase 2 files, all passing.
- e2e on Edge: 10 of 10, including the 390 px step (both lists above the box,
  inside the window, no sideways pan) and a real turn that carried a folder
  skill and an `@` file.
- Live, on a scratch daemon with Claude: given only `@docs/deploy-notes.md`,
  the session answered "The deploy codename is CORAL-TELESCOPE-17, according
  to docs/deploy-notes.md"; the saved question records the file.

Same release, from the user's comparison with Claude's own app: the
conversation sits in one centred 760 px column with its box, in 15 px type
with roomier lines, and Inter now ships with the app.

Left open: Rewind restores the words, not the chips; a "Resume when ready"
continuation does not re-read the files or the skill; skill lists are read
once per session.

## Phase 3 — The conversation shows the work (target v1.374.0)

**What you see.**
- The task list sits under the reply while it works (the steps tick off as
  they finish), not only in the side Plan pane.
- A small meter shows how full the conversation is for this model, and a
  **Compact** button summarises older turns when it gets full.
- When a turn that took longer than a minute finishes while you are elsewhere
  in the app or the window is in the background, a desktop notice says so.

**How.**
- Put the chat's `PlanChecklist` under the live reply (it folds into the
  reply once the turn ends; the Plan pane stays).
- The done frame already carries usage; add the window the planner used
  (`_context_window`) as `context: {used, window}` and draw the meter.
- Compact for a Build session: the model-written, ledger-checked summary of
  `context/compaction.py`, stored on the session and used in place of the
  older turns (the transcript you see is never cut).
- A `build.turn_finished` event (with its duration) for the bell and the
  desktop notice, under the one-notice rules (v1.360.0 / v1.361.0).

**Done when.**
- Screen tests for each piece, including the meter's numbers coming from the
  daemon, not guessed.
- A live session that fills past 70% of a small model's window offers
  Compact, the summary passes the ledger check, and the next turn still
  answers a question about the summarised part.
- A long turn finished in the background raises exactly one notice.

## Phase 4 — Git: a branch per session, commit, PR, merge (target v1.375.0)

**What you see.** New session in a git folder starts on its own branch
(`ij/<short-name>`) in a separate worktree, so your main checkout is never
touched. The header shows the branch and its base. The Changes pane gains a
**Branch** view: the whole diff against the base, committed and not. Buttons:
**Commit** (the model drafts the message, you edit it), **Push**,
**Create PR** (with CI status while it is open), **Merge into <base>**, and
**Discard**.

**How.**
- Reuse `git/integration.GitSession` (already makes a worktree, lists
  changed files, diffs, commits, merges into base, discards). The session's
  folder becomes the worktree.
- Push, PR and CI through the user's own `git` and `gh` (GitHub CLI), run
  through `core/procs` with a bound. PR and CI only when `gh auth status`
  says signed in; otherwise the session offers the compare link.
- The guardrails stay: no force push, no history rewrite, no deleting a
  branch, destructive git only when you ask. Every button is a person's
  action; the agent can commit and push only through an approval card.
- Rewind and Undo keep working inside the worktree; Discard moves the
  worktree folder to the trash (never deletes).

**Done when.**
- On a scratch repository with a local bare remote: a session starts on a
  new branch in its own worktree, the main checkout's files and branch are
  unchanged, the agent edits, the Branch view shows the diff, Commit and
  Push land on the remote branch, Merge brings it into the base, and the
  worktree is removed.
- Create PR and CI status are driven against a fake `gh` (the real GitHub is
  checked by hand once, on a throwaway repository you name).
- A folder that is not a git repository behaves exactly as today.
- The packaged app finds `git` and `gh` on PATH, or says in plain words that
  it cannot.

## Phase 5 — A browser the agent can drive (target v1.376.0)

**What you see.** The agent can open your dev server, click, type, read the
console errors and failed requests, and take a screenshot, in one invisible
browser that stays open for the session. The Browser pane shows what the
agent last saw and where ("The agent is on localhost:5173/cart").

**How.**
- One browser per session on Playwright, driving the installed Edge
  (`channel="msedge"`, no extra download), reusing computer use's
  `PlaywrightBrowser` element targeting (role, label, text). Closed when the
  session is archived or idle for 10 minutes.
- Tools: `page_open`, `page_click`, `page_type`, `page_read` (visible text,
  console errors, failed requests), and the existing `page_screenshot`.
- The same address rules as v1.371.0: your Browser pane's page needs no
  approval, any other local page asks, public sites are refused, Local only
  asks for every one, low trust refuses. The throwaway profile and downloads
  stay in the system temp folder.

**Done when.**
- Live on a scratch dev server with a deliberate JavaScript error: a Claude
  session and a Codex session each find the error from the console, fix the
  code, reload, click the button that was broken, and confirm the new text.
- The packaged app's frozen daemon starts the browser (Playwright's driver
  is bundled; checked on the installed build).
- Stop, archive and the idle timer each leave no browser process behind.

## Phase 6 — Smaller touches and loose ends (target v1.377.0)

- **Next step:** after a turn, up to two suggested follow-ups as buttons
  (the chat's follow-ups, off by default like there).
- **Branch from a message:** start a new session from any message, with the
  conversation up to it.
- **Search sessions** in the left pane.
- **Keyboard:** Ctrl+N new session, Esc stop, Ctrl+L focus the box, Ctrl+K
  the model menu.
- **Loose ends from the v1.371.0 review:**
  - Rewind puts a message's pictures back in the box with its words.
  - Past 20 files, say so instead of dropping the extras.
  - Continuation turns (Resume when ready, Continue on another account)
    carry the Browser pane's address.
  - Decide whether a screenshot is scanned for hidden instructions before the
    model sees it (only its text is fenced today).

**Done when.** Each has a screen or server test that fails on today's code,
and one live walkthrough shows them working together.

## Phase 7 — Measure the feel (target v1.378.0)

- Rerun the 30-task benchmark for all five setups (Claude Code, Codex, Jarvis
  on Claude, Jarvis on Codex, Jarvis on the local model) with the new tools
  armed, and record it in this file.
- A side-by-side walkthrough of the same three tasks in the Claude Code
  desktop app and the IJ harness (a bug fix with a test, a UI change checked
  in the browser, a feature on a branch ending in a PR), with screenshots of
  both at each step.

**Done when.** The benchmark shows no task lost against v1.370.4, and every
step of the walkthrough is possible in the IJ harness with no more presses
than in Claude Code. Anything that still is not is written down as the next
gap.

## What this plan does not change

- The Terminals view stays exactly as it is.
- Nothing moves to another provider or account on its own.
- Nothing is published, pushed or merged without a person's press or an
  approval card.
