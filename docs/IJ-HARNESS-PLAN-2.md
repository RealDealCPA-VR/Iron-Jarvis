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

**Result (doer, before review).** Live on a scratch daemon with the local
model (glm-5.3-flash through the LiteLLM proxy) and a 3,000-token window
pinned in the scratch config, driven through the real IJ harness screen:

| Check | Outcome |
|---|---|
| Meter | 35% after the first turn, rising each turn; the sixth turn read 73% and the box offered **Compact** (the daemon's own `level: suggest`) |
| Compact | 6 earlier messages summarized on custom / glm-5.3-flash, 0 claims removed (the model named only files the conversation and the ledger hold), billed to the session (the usage shows the extra call); the 12 lines of the conversation were all still there |
| The summarized part | "what were the codename and the port from the start?" was answered correctly (CORAL-TELESCOPE-17, 6431) while the next turn's history held no line from the turns that carried them; the same after a second and a third Compact (`/compact` from the box) |
| Task list | a turn that called the plan tool showed the checklist under the live reply; the settled reply folded it to "Plan · 1 of 3 done" |

Found by the live run and fixed in the same phase: the first summary was as
long as the three turns it covered, so the next prompt got heavier. Compact
now asks for a third of what it replaces and keeps a summary only when it is
lighter. Not driven live: the finish notice (a turn longer than a minute) —
proven against the real route with a stand-in clock, the real bell and the
desktop app's tray code under node.

**Review.** Found and fixed, each red first and mutation-checked:

| Defect | Fix |
|---|---|
| The summary's file paths were checked against every call the session ever made — a REWOUND turn's rows (its lines gone) and a turn after the coverage (never shown to the summarizer) corroborated a path, so a summary every later turn reads as fact could name work the conversation no longer holds | only the covered turns' own calls may back it (`compact.turn_windows`: each turn's first line to its last) |
| Compact parsed the transcript (up to 200 lines with steps and thinking), hashed it and wrote the usage row on the event loop | off the loop, like every other transcript read in the build routes |
| A parked or signed-out account answered "Claude Code couldn't finish this answer Press Retry. If it happens again, pick another model for this chat." | the account's own sentence (the turn's limit card's words), 409; other failures in whole sentences |
| The meter called the app's own 32,000 safe size "what this model can read at once" for any model whose window nobody knows (a Codex or API-key session reads 95% of a window it does not have) | the reading carries `window_known`; the meter says "of what Iron Jarvis sends this model at once ... its own limit is not known"; compaction turned off in Settings shows the number with no Compact |
| At 390 px with a model that offers reasoning levels, "Summarizing earlier turns…" / "Earlier turns summarized" pushed Send ~60 px out of the box (the percent and Compact 4–15 px) — measured in a real browser | the box's right-hand group wraps, right-aligned, bounded by its row; re-measured live |
| A finish the user watched end was counted again by "While you were away" after a reload or a close to the tray | the watched ids are kept beside the "looking" note and left out of the replay |
| A settled Build reply folded its plan with the chat's run rule: steps it did not reach read "Not attempted" | decided: a Build plan carries over to the next turn — the step it was on is not ticked off (failed with a failed turn), the rest "not started" |

Pinned (a mutation used to pass): the summary's prompt-guard scan, Compact's
refusal while a turn runs, Local only and the held model on the REAL router,
the session's one Iron-Proxy account (and nothing else when it is parked), a
refused rewind keeps the summary, no door but Compact writes one. Decided and
pinned: a session whose turn was lowered may compact (no tools, the
summarizer never reads a tool's output, and the draft is scanned before it is
kept); a helper's own end raises no notice (the turn's covers it).

Re-driven live on a scratch daemon (custom / glm-5.3-flash, a 3,000-token
window pinned, the real screen at 390 px): 72% offered Compact; the press
summarized 10 messages (0 removed, billed: 9 turns on the usage line); the
next turn answered the codename and port from the summary; a second Compact
covered 14 with the first fed in; with the pin removed the meter read 6% "of
what Iron Jarvis sends this model ... not known"; a 69 s turn (a shell
command that slept) raised ONE `build.turn_finished` and the bell's row
"Your Build session finished — review drive · took 1 min 9 s". Still open:
Build turns also run the chat's own automatic compaction at 92% (content-
addressed, transcript-checked only), so two summaries can stack; the chat
page's own gauge still calls an unknown window the model's; a return to a
session mid-Compact shows no "Summarizing…" (a send is refused in words and
the words go back in the box).

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

**Result (doer, before review).** Live on a scratch daemon (port 8817, the
local model custom / glm-5.3-flash, a scratch repository with a local bare
remote in the temp folder), through the API:

| Check | Outcome |
|---|---|
| New session in the repository | `ij/session-dbf2` in `shop.ij-worktrees\session-dbf2` beside `shop`, base main; the main checkout stayed on main, `git status` empty, main's commit unmoved; listed under `shop` |
| The agent's edit | 7.6 s, `apply_patch` in the worktree; `app.py` gained `greet()` there, the checkout's `app.py` untouched |
| Branch view | `app.py` modified +4 −0, and `__pycache__/…pyc` (the model ran Python in the worktree; the scratch repo had no `.gitignore`) |
| The agent asked to commit and push | the model found git by its full path (`"C:\Program Files\Git\bin\git.exe" … commit … && … push`); each call put up an "Allow once / Deny" card ("This pushes to origin. Every push from a session's branch … asks you first."), denied twice, no commit made; the model then asked the user what to do (`ask_user`) |
| Commit | glm-5.3-flash drafted "Add greet function to app.py"; committed (edited) as the repository's own author, 2 files |
| Push | the bare remote got `ij/session-dbf2` at the branch's commit |
| Merge into main | a merge commit in the main checkout with the two parents; `app.py` there now has `greet()` |
| Remove the worktree | moved to `<home>\trash\<stamp>\worktree-session-dbf2`; `git worktree list` shows only main; the branch kept; the session archived |

The real screen (e2e on Edge, the demo model): the switch, the header's
branch, the Branch view, Commit (the demo model drafts nothing and says
so), Push, Merge and Remove the worktree, and no sideways pan at 390 px.
Its first run FOUND A RACE, fixed in this phase: right after Merge the
Branch view re-reads the branch (git calls whose working folder is the
worktree) while the user presses "Remove the worktree" — a folder a
process is in cannot be renamed on Windows, so the remove said "in use".
A session's git reads and actions now run one at a time (a per-session
lock, taken off the loop), and a brief hold (a virus scanner) is retried
for a few seconds before Discard says the folder is in use.

Decisions made in this phase (each pinned by a test):

- **Branch name**: `ij/<slug>-<4 hex>` — the session has no title yet when
  it starts, so `ij/session-3f2a`; the worktree folder has the same short
  name. A pick inside the repository works in the worktree's copy of that
  folder.
- **Grouping**: under the folder the user PICKED (the repository's), never a
  `<repo>.ij-worktrees` group; the session is named by that folder.
- **Project**: the project of the folder the user picked (the worktree sits
  outside every project's root). The project's "Project folder" line in the
  prompt names the session's worktree of it, never the main checkout, and a
  paragraph tells the model the main checkout is the user's.
- **Harness layer and rule files**: the worktree's own when it has one (a
  tracked layer comes with the branch), else the main checkout's (an
  untracked `.ironjarvis/harness/` or `AGENTS.local.md` is not in a new
  worktree) — per rule file. The experience library belongs to the folder
  the user picked (its accepted edits are written there).
- **Merge when the base is checked out nowhere**: the same merge commit is
  made without touching any working tree (`merge-tree` → `commit-tree` →
  `update-ref` with the old value). A checked-out base is merged in that
  checkout with `git merge --no-ff`, refused first when that checkout has
  uncommitted changes or the merge would conflict (`git merge-tree`); a
  merge git itself stops (a hook) is aborted and said so.
- **The agent's commit / push**: from `shell`, `process_start` or
  `process_write`, a `git commit`, `git push` or `gh pr create|merge`
  ASKS in every posture (auto-approve included), "Allow once" and Deny only,
  past every grant (a tool spend check with `once_only`); the POST lane,
  which cannot ask, refuses it; a helper's run pauses the same way. A force
  push (`-f`, `--force*`, `--mirror`, `+refspec`) is refused by the tool
  itself. Only while the session works on a branch: a session in its folder
  directly is exactly as before. It reads the command's text, so it is a
  guardrail against a careless model, not a sandbox.
- **Archive** keeps the worktree. **Delete** moves it to the trash first
  (the branch stays); a worktree that cannot be moved refuses the delete in
  words. **Discard** archives the session.
- **Commit identity**: the user's own git name and e-mail; Iron Jarvis's
  default only when git names no one, and the answer says so.
- **gh**: Create PR only when installed AND signed in for the remote's host
  (`gh auth status`, remembered 60 s); otherwise the GitHub compare link.
  Checks are read when asked and every 60 s while the Branch view is on
  screen. A doctor row says when `gh` is missing (developer rows, not the
  first-run card).

Tests: 63 in `tests/test_build_git{,_agent,_pr}_v1375.py` (real git in a
temp folder with a local bare remote; a fake `gh` on PATH for signed in,
signed out and missing) and 24 in
`dashboard/__tests__/ij-harness-git-v1375.test.tsx`; 39 Python and 19
dashboard mutations, each red. The e2e (`e2e/ij-harness-git.spec.ts`) and
the 10 earlier IJ harness e2e steps pass on Edge.

Not done here: the real GitHub was not driven (a fake `gh` in the tests);
the packaged app was not built (git and gh are found with `shutil.which`
on the PATH the desktop app hands the daemon). A helper agent started by a
branch session works in the worktree and its commit / push asks the same
way, but its own prompt does not carry the branch paragraph.

**Review.** Each defect below was shown red on the phase's code first,
fixed, and mutation-checked (every mutation red); real git in a temp folder
with a local bare remote, a fake `gh`, never the real GitHub.

| Found | What it did | Now |
|---|---|---|
| The worktree's HEAD was trusted as the session's branch | a `git switch main` in the worktree (git allows it while the user's checkout is on another branch) made **Commit commit onto the user's main** | Commit, Merge and Create PR refuse in words until it is switched back |
| Discard ran `git worktree prune` | it forgot EVERY worktree whose folder was missing at that moment: a user's own worktree on an unplugged drive lost its index and HEAD ("not a git repository" once plugged back) | `git worktree remove <path>` (no `--force`) forgets only this one |
| Discard with the trash on another drive copied, then deleted | a file held open (a dev server's log) was met part of the way through: the worktree was left with only that file, its `.git` gone, and every later Discard and Delete refused it | renamed beside itself first (one step, refused with nothing moved while a file is held), copied, put back if the copy fails |
| Merge into a base "checked out nowhere" | a checkout in the middle of a rebase of the base reads as detached: the base moved under it and the user's rebase failed at its end | a dry-run `git fetch . <merge>:<base>` asks git's own guard first; nothing is written |
| Merge with the base in a folder that is not there | a 500 | said in words; an untracked file the merge would overwrite is named too |
| A start git refused | a failing `post-checkout` hook (Git LFS's own, with git-lfs missing) or a path past MAX_PATH left a stray `ij/` branch (and a whole worktree) per try | the just-made worktree and branch are undone (the branch only while it still points where it was made) |
| A folder inside a submodule | its worktree landed inside the superproject's `.git` | a submodule cannot branch (words on New session) |
| `gh` resolved to a batch file | a pull request title `x" & echo owned>PWNED.txt & rem "` RAN through cmd.exe | refused before the push |
| `GIT_DIR` / `GIT_INDEX_FILE` in the app's environment (git sets them for hooks) | every Build git call acted on THAT repository | `git_env()` drops them |
| The agent's git guard | `cmd /c "git push"`, `bash -lc 'git commit'`, `git -c alias.ci=commit ci` ran on Auto-approve with no card; `reset --hard HEAD~1`, `rebase`, `branch -D`, `worktree prune` ran; `push origin :branch` and `commit --amend` were carded as an ordinary push / commit; a refused force push showed the shell card first | stray quotes stripped, `-c alias` expanded; REFUSED with no card in any posture: force push, deleting a remote branch, history rewrites, deleting / renaming / force-moving a branch or tag, `worktree remove/prune/move`, `gh repo delete`; CARDED once-only: also git that throws work away (`reset --hard`, `clean -f`, `checkout -- .`, `restore`, `stash drop`), `git config` writes (the repository config is the one the user's checkout reads) and any other gh change |
| The library | a branch whose worktree carries a TRACKED harness layer: an accepted edit was written into the MAIN checkout's copy (leaving the user's checkout changed, which then blocked Merge) and the session never read it | edits target the copy the session reads; lessons stay with the picked folder |
| Two presses | a second git press while one ran said "still answering"; a record was written onto the facts read before the wait (a Merge dropped a pull request's link) | "busy with another git action"; records land on the facts as stored now |
| The screen | Merge stayed until the re-read; Discard's second press said nothing of uncommitted work | Merge hides when it answers; "Confirm: 2 uncommitted files go to the trash" |
| Leftover | a helper's prompt lacked the branch paragraph | it carries it |

Four of nine sampled phase pins survived their mutation (reads taking
optional locks in the user's checkout, `update-ref` without the old value,
Delete without the git lock, the facts whitelist); each has a pin now.

**The agent's shell and git, in the product.** The shell and the program
tools run on the scrubbed environment (`sandbox.modify_env = "deny"`, the
default): PATH is the app's own folder, System32 and Windows, and there is no
HOME / USERPROFILE. So `git` does not resolve by name (nor node, npm or
python; only `py.exe`), and git named by its full path cannot read the
user's global config: a commit the user allows on the card fails with
"Author identity unknown" unless the repository names someone, and a push
cannot reach the user's stored credentials. The Commit and Push buttons run
with the app's own environment and work. Not changed here (it is the shell's
environment for every session, not this phase's); it limits what the agent
can do in a coding session and needs its own decision.

Live, on a scratch daemon (8817, custom / glm-5.3-flash, the repository and
its bare remote on D:, the app's home on C:): the agent edited in the worktree;
`reset --hard HEAD~1` and `push origin :main` were refused with no card on
Auto-approve, `cmd /c ""…git.exe" push"` carded once-only (denied, twice);
the drafted Commit, Push, a refused Commit on a detached worktree, Merge into
main in the main checkout (clean after), Discard refused with a file held
open (worktree whole) and then moved D: → C: trash with nothing left behind,
the worktree forgotten and the branch kept.

Still open: rewinding a committed turn puts its files back as changes not
committed (the commit stays on the branch) and the rewind does not say so; a
repository git refuses for "dubious ownership" reads as "not a repository";
on git older than 2.38 Merge says only that it could not check the merge;
Local only still shows the shell's own card before a refused git command.

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

**Result (doer, before review).** Live on a scratch daemon (port 8817, this
PC's own Claude Code and Codex logins, Iron-Proxy off), a scratch site served
by `python -m http.server` whose `app.js` looked up `#cartCount` while the
page's element is `#cart-count` (so Add to cart threw), the Browser pane's
address sent with each message:

| Check | Outcome |
|---|---|
| Claude session (claude-cli) | 22.8 s, no card: `page_open` → `page_click "Add to cart"` → `page_read` showed "Uncaught TypeError: Cannot set properties of null (setting 'textContent')" (quoted in the reply) → `edit_file` (`cartCount` → `cart-count`) → `page_open` → click → `page_read`: "Cart: 1 item", console clean. The pane's picture shows "Cart: 1 item" |
| Codex session (codex-cli) | 28.6 s, no card: `page_open` → `page_click` (with `role: button`) → `page_read` (1 console error) → read both files → `apply_patch` → `page_open` → click → `page_read`: "Cart: 1 item" |
| Archive | the session's driver and 8 Edge processes all gone; the pane says "closed" |
| Stop while the model thought (after `page_open`) | the turn ended "stopped", the browser closed (`closed_reason: stopped`), no process left |
| Stop during an action (`page_open` of a local address that never answers) | the call ended 0.2 s after Stop ("Stopped: you pressed Stop, so the agent's browser was closed."), no process left |
| Idle (`IRONJARVIS_PAGE_BROWSER_IDLE_S=30`) | closed 37.7 s after the last action (30 s + the check), every process gone |
| Daemon shutdown (`POST /shutdown`) | 11 browser processes gone 0.6 s after |
| Daemon KILLED (TerminateProcess of the daemon alone) | 9 browser processes gone within 0.2 s (the job) |

Frozen build (`pyinstaller packaging/ironjarvis.spec`, into the worktree's
`packaging/dist`): `ironjarvis.exe page-browser-check http://127.0.0.1:<port>/`
opened a local page in headless Edge, read its text and its console error,
closed it and found none of its 8 processes alive (`"ok": true`); the same
check on a build with v1.375.0's spec (Playwright excluded) says the driver is
missing (`"ok": false`, in words). The frozen daemon boots and serves
`/health`. Size: 298,236,152 → 408,555,153 bytes (+110,319,001, ~105 MiB;
Playwright's driver is 103 MiB of it: `node.exe` 88 MiB + its package). The
installed (NSIS) build was not made here.

Decisions made in this phase (each pinned by a test):

- **The browser**: the installed Edge (`executable_path` of the Edge the
  doctor finds — Edge only, the user's decision names it), headless, a
  throwaway profile and downloads folder in the SYSTEM temp
  (`ij-agent-browser-*`; downloads are refused anyway), opened on the first
  `page_open`, one per Build session — a helper agent uses its session's.
- **Processes**: Playwright starts its own driver, so it is ADOPTED into a
  kill-on-close Job right after it starts and before it launches anything
  (`procs.adopt`); Edge and every process Edge starts are born in the job
  (measured: all members). Close = the context, the driver, then the job.
  `ALLOWED_RAW_SPAWNS` is unchanged (no file of ours spawns here). A killed
  daemon leaves its throwaway folder (and Playwright's own empty
  `playwright-artifacts-*` folder in the temp folder) behind; the next start
  removes `ij-agent-browser-*` folders older than 6 hours.
- **The thread**: Playwright's ASYNC API on one dedicated thread with its own
  event loop for every session's browser; the daemon's loop only awaits a
  future from it, and a cancel there cancels the action's task. One action at
  a time per session; each has a time limit in words (open 45 s, click / type
  / read 15 s), and a wedged driver past it closes the browser.
- **Stop**: CLOSES the session's browser — at once when an action is under
  way (the tool races its action against the turn's Stop; Playwright's driver
  would otherwise finish a click or a page load on its own), and when the turn
  ends stopped. The next turn opens a fresh one.
- **The address rules on every navigation**: the Browser pane's dev server
  and an address the user allowed for this browser open without a card;
  another local address asks once (`page_open`'s spend check — allowed, it
  stays allowed until the browser closes); a public site is refused. A
  navigation the page starts (a link, a form, `location=`, a meta refresh, a
  popup) is stopped by route interception BEFORE its request; a server
  redirect is followed by Chromium without asking the route (measured), so
  its hop is judged the moment the browser starts it and the page is sent
  away (its request has left; the page is never read, shown or acted on),
  with an arrival guard behind it; the browser returns to the last allowed
  page and the result says where the page tried to go. Every action re-checks
  its page.
- **Typing**: a password field, a payment field (card number, security code,
  bank details, `cc-*`), a file picker, or text that reads as a key —
  REFUSED in words, never carded.
- **Tiers**: `page_open` ask (its spend check lifts the pane's page);
  `page_click`, `page_type`, `page_read` allow. Local only cards every open,
  click and typed entry (`OPENS_PAGES_TOOLS`); low trust refuses all four.
  Plan mode opens and reads, never clicks or types. No dialect renames them
  (Claude Code and Codex have no local browser tool).
- **The pane**: "The agent is on localhost:5173/cart" (host and path, never
  the query string) and the picture taken after its last action, read when
  the tab opens, when a page step finishes and when a turn ends — never on a
  timer. `page_screenshot` with no address looks at the agent's page.

Tests: 58 in `tests/test_build_browser_v1376.py` (a fake browser for the
fast pins; two against the real Edge and a local `http.server`, one of them a
KILLED daemon), 16 in `dashboard/__tests__/ij-harness-browser-v1376.test.tsx`
and `ij-harness-step-words-v1376.test.ts`, and an e2e step (the pane at 390 px,
no sideways pan). 36 mutations of the daemon's side and 14 of the
dashboard's (its words, the pane, the low-trust families), each red.

Not done here: the installed (NSIS) build was not made; no hand test of a
page that opens a popup, a dialog-heavy page or a dev server on HTTPS; the
throwaway folders a killed daemon leaves are swept only after 6 hours.

**Review (before ship).** Each defect pinned red first and mutation-checked
(`tests/test_build_browser_review_v1376.py`,
`dashboard/__tests__/ij-harness-browser-review-v1376.test.tsx`):

- *Typing through a label.* Playwright's fill() follows a `<label>` (or
  anything inside one) to its control, while the refusal read the label's
  facts: a css target naming the label of a password field typed the password
  (real Edge). The facts are now those of the field fill() reaches.
- *A page that asks before leaving* (unsaved changes) was answered "stay",
  which cancelled the agent's own page_open with words about a download. The
  agent's navigation now leaves it and says the page asked; confirm() and
  prompt() are still answered Cancel ("the agent's browser never says yes for
  you"), an alert is closed.
- *Edge closed or crashed* (or its driver killed) left a session whose every
  page_open raised a raw error until the idle close. The dead browser is
  closed (job, folder) and the next page_open starts a fresh one, saying so.
- *A process outside the job.* About two seconds in, Edge has an
  `identity_helper.exe` that is NOT in the browser's job (Windows starts it in
  a job of its own; a job allowing no breakaway does not keep it either). It
  exits ~0.2 s after Edge, but the process list and the close missed it; the
  doer's "every process is in the job" pin held only in the first two seconds.
  The list is now the job's members and the driver's whole tree, and a close
  ends both.
- *No job, no browser.* When Windows will not put the driver in a job, Edge
  is no longer launched (the job is what ends it when the daemon is killed).
- *Playwright's own temp folder* (`playwright-artifacts-*`) was left in the
  PC's temp folder by a crash or a killed daemon, forever. The driver and Edge
  now get the browser's throwaway folder as their temp folder.
- *A cancelled call that only waited its turn* (a helper's, behind another
  helper's click) closed the session's browser under the click. Only a call
  that was acting closes it now.
- *A Stop during an action* closed the browser twice; the second close
  returned before the first had ended a process (the doer's Stop pin saw a live
  browser 1 run in 3). One close per browser; every caller waits for it.
- *A refusal quoting the page.* A tool's failure reaches the model unfenced
  (only its output is fenced), yet the refusal for a page the agent may not act
  on quoted its address. It names none now. Proved through the real stream
  lane: a planted "ignore all previous instructions" page is fenced and
  withheld, the turn turns low trust, and the next click is refused.
- *The pane* said a paired browser lets the session browse the web; a Build
  session never uses it. A paired browser is named with what is true; nothing
  is said when none is paired.

Decisions:

- **Where a page may send (Handbook words):** "The agent's browser opens,
  reads and acts only on pages on this PC or your network. A page's own
  requests (a script from a CDN, an image, its API calls, the next hop of a
  redirect) go where the page sends them, as in any browser. In a Local only
  session nothing the browser does reaches a public address: it runs behind a
  proxy that refuses everything outside this PC and your network, so a script,
  a request, a WebSocket or a redirect to the internet is stopped before it
  leaves, and listed as stopped." Measured on the real Edge: outside Local only
  the test page's CDN script, API call and beacon reached the public host;
  under Local only none did, this PC still answered. A redirect's request is
  therefore stopped at the network under Local only and, outside it, treated
  like any other request a page makes (closing it there would mean fetching
  every page from the driver instead of the browser).
- **Clicks:** a click on a button that reads as deleting, sending or paying
  (computer use's and the browser add-on's one vocabulary, read off the page,
  never the model's words) asks once, every time, in every posture; the click
  reads the button again and is made only if it still says what the card said.
  ("Deploy" is not in that vocabulary; adding it would change computer use and
  the add-on too.)
- **"Ask first"** cards every click and typed entry in the agent's browser;
  opening and reading the Browser's page do not ask. (The chip's words do not
  yet mention it; they are pinned in the Handbook.)
- **At most four** agent browsers at once; a fifth closes the least recently
  used one that is not acting (its pane says so); when all four are acting,
  the open is refused in words.
- **The driver runs on the desktop app's own engine.** The packaged app no
  longer ships Playwright's node.exe (88 MiB): Electron 42.5.0 is Node 24.17.0
  and drives Edge exactly as node.exe did. Frozen (rebuilt here): 316,874,920
  bytes (408,555,153 before the review); `page-browser-check` with
  `IRONJARVIS_PLAYWRIGHT_NODE` set to Electron: ok, 9 processes, none left;
  without it, it says the browser starts only inside the app. The release
  check must set that variable to the installed `Iron Jarvis.exe`.

Live (scratch daemon 8817, this PC's Claude Code login, a scratch site): the
Claude session found the console error, fixed app.js, reloaded, confirmed
"Cart: 1 item", and its click on "Delete item" carded once with the button's
words; allowed, the page said "Item deleted". A Local only session on the
user's own model (glm-5.3-flash) carded page_open and read the page's two
requests to a public name as "stopped: not on this PC or your network (Local
only)"; the dev server saw only 127.0.0.1. Archiving left no browser process.

## Phase 6 — Smaller touches and loose ends (target v1.377.0)

- **Next step:** after a turn, up to two suggested follow-ups as buttons
  (the chat's follow-ups, off by default like there).
- **Branch from a message:** start a new session from any message, with the
  conversation up to it.
- **Search sessions** in the left pane.
- **Keyboard:** Ctrl+N new session, Esc stop, Ctrl+L focus the box, Ctrl+K
  the model menu.
- **The agent's terminal finds your tools** (decided by the user,
  2026-10-23, after the Phase 4 review found that `git`, `node`, `npm` and
  `python` do not run from it): a Build session's `shell` and process tools
  get your normal PATH and home folder (HOME / USERPROFILE and what git, npm
  and python read from it), so they work as they do in your own terminal;
  secret variables are still stripped; chat, agents and unattended runs keep
  the scrubbed environment.
- **Loose ends from the v1.371.0 review:**
  - Rewind puts a message's pictures back in the box with its words.
  - Past 20 files, say so instead of dropping the extras.
  - Continuation turns (Resume when ready, Continue on another account)
    carry the Browser pane's address.
  - Decide whether a screenshot is scanned for hidden instructions before the
    model sees it (only its text is fenced today).

**Done when.** Each has a screen or server test that fails on today's code,
and one live walkthrough shows them working together.

### Result (v1.377.0)

**The agent's terminal finds your tools.** ONE switch, keyed on the session,
never on a tool, a posture or a model's argument: `harness/command_env.py`
(`is_build_call(session_id)`) answers yes for a `build:<id>` call and for a
helper started by one (registered in `harness/helpers` under a `build:`
parent); `shell` (the registered `SandboxedShellTool`, native runs only) and
`process_start` then run with `sandbox/native.build_env()`; everything else
(chat, agents, a non-Build agent run, schedules, goals, workflows, the phone,
unattended doors) keeps `scrubbed_env()` exactly as before. `build_env` is the
daemon's own environment (the desktop hands it the user's) minus, by NAME
only (`secret_name`, case-insensitive; no value is ever read or printed):

- names holding KEY, TOKEN, SECRET, PASSWORD, PASSWD, PASSPHRASE,
  CREDENTIAL, SESSION, COOKIE, PRIVATE, ASKPASS, or AUTH (but never AUTHOR —
  `GIT_AUTHOR_*` is the user's commit identity), or a `_`-part PAT / DSN;
- whole families: ANTHROPIC_, OPENAI_, GEMINI_, GOOGLE_, AZURE_, AWS_, GH_,
  GITHUB_, HF_ / HUGGING*_, IRONJARVIS_ (the install token), IRON_PROXY_,
  PLAYWRIGHT_, CLAUDE_CODE_ / CLAUDE_AGENT_SDK_ and a parent Claude Code
  session's markers, SENTRY*, PyInstaller's;
- exact names: ELECTRON_RUN_AS_NODE, CHROME_CRASHPAD_PIPE_NAME, VIRTUAL_ENV,
  UV_RUN_RECURSION_DEPTH, BAGGAGE; the git variables that point git at
  another repository (what `git_env` drops); this app's own folders on PATH
  (a dev daemon's virtual environment, a frozen daemon's bundle);
- plus `GIT_TERMINAL_PROMPT=0` (no git waits on a password typed into a
  terminal nobody sees).

Kept: PATH (minus the app's own folders), HOME / USERPROFILE / HOMEDRIVE /
HOMEPATH, APPDATA / LOCALAPPDATA, TEMP, the user's own git / npm / pip / nvm /
pnpm settings. The model is told the truth: in a Build session the shell's
description and the harness's runtime note say "Windows cmd.exe with the
user's own PATH and home folder … Found on it: git, node, npm, …" (read off
that PATH with `shutil.which`, off the loop, remembered 60 s; a Microsoft
Store python alias does not count). Known cost of the families: AWS_PROFILE
and other non-secret AWS_/GITHUB_/GOOGLE_ settings go too (their CLIs still
read their own config files); SESSIONNAME is dropped by the SESSION word.
Low trust still refuses a native shell; Local only still cards every command;
the git guard still cards a bare `git push` once-only on a branch session.
Terminal panes are untouched.

**Next step.** `POST /build/sessions/{id}/followups` reuses the chat's
follow-ups and its setting (`chat_followups`, off by default): at most two,
asked of the session's own model (held, and pinned to its account), billed to
the session, none while a reply runs, none for the demo model or a reply with
no words. A press puts the words in the box; it never sends.

**Branch from a message** (a reply or a question): `POST
/build/sessions/{id}/branch` makes a NEW session in the same folder, titled
"<title> (branch)", carrying the session's model, reasoning, account,
approval setting, Local only and Browser address, in act mode, with no plan
and no grants. From a reply the conversation up to it is copied; from a
question the conversation before it is copied and the question goes in the
new session's box. A session on its own git branch gives the new session its
OWN new branch (never the same worktree) started from the same base: the
base branch's tip, else the commit the first session started from. The old
session is not changed. The new session says "Branched from …" (a button that
opens the old one).

**Search sessions** in the left pane, by title and folder name; Escape clears
it first.

**Keys** (`lib/harnessKeys.ts`): Ctrl+N New session, Esc stop, Ctrl+L the
message box, Ctrl+K the model menu — INSIDE the IJ harness only (the focus is
in the harness view, or nothing is focused and the last click was in it).
Ctrl+K precedence: inside the harness it is the model menu, taken in the
capture phase and stopped there so the app's command palette never sees it;
anywhere else (the title bar's search, the sidebar, every other page) it is
the palette, untouched. Esc stops only while a reply runs, and never when the
Escape belongs to something else (a dialog, an open menu or list — read
before anything handles the press — the question dock, another text field,
an Escape a handler already took). Never during IME composition.

**Loose ends.** Rewind puts back a message's pictures and `@` files with its
words; past the box's limits (20 files, 4 read) it says how many were left
out. (Its "/" skill does not go back yet: the daemon's preview names it, the
box does not restore it.) Continuation turns (Resume when ready, Continue on another account)
carry the Browser pane's address (stored with the session's setup, re-checked
as local). A rewind over a committed turn says so: "Some of this work was
committed: N commit(s) on <branch> (“subject”) came after this message.
Rewinding puts the files back, but the commits stay on the branch, so the
files show as changes that are not committed yet." Returning to a session
mid-Compact shows "Summarizing earlier turns…" (`compacting` on the session
view; re-read every 2 s until it ends). One sign-in rule for the model menu
and the header chip: the daemon's — an account that is signed out stays
pickable in both (the cached list can be stale; the turn shows the sign-in
card), one turned off or missing is not, with the reason in words. "Ask
first" now reads "Asks before it edits files, uses the internet, runs
commands, or clicks or types in a browser." The chat's gauge never calls the
safe size the model's limit when the window is not known (`window_known`).

**The screenshot scan: decided NOT to OCR-scan screenshots.** Measured here
(headless Edge on a local server, Windows OCR, the chat's `scan_context`):
OCR 0.02–0.12 s and the scan ≤ 0.06 s, so cost is not the reason. Of six
pages, the planted injection was flagged — and so were a chatbot admin page
with a "System prompt" field and a docs page that QUOTES the phrase "ignore
all previous instructions": false positives that would turn low trust on
exactly the AI-app work this harness exists for. Faint text (#f2f2f2 on
white) was not read by the OCR at all, so the scan misses the hidden case it
was meant for. A page's TEXT already reaches the model fenced and scanned
(`page_read`); a picture stays unscanned.

Tests: 29 in `tests/test_build_env_v1377.py` and
`tests/test_build_phase6_v1377.py`, 25 in
`dashboard/__tests__/ij-harness-phase6-v1377.test.tsx`, an e2e step (the keys
inside the harness only, the search, Branch from here). Each new behaviour
was run on the v1.376.0 code first and was red there (21 of the 25 screen
tests; the other 4 are pure-helper and control pins). Mutations: 30 of the
daemon's side and 21 of the screen's, each red. Pins moved on purpose:
`reads-follow-setting-v13600`, `ij-harness-model-accounts-v1376`,
`ij-harness-rewind-library-v1370`, `test_harness_model_fold_v1376` and its
fixture (signed-out accounts are pickable).

Live (scratch daemon 8817, a throwaway Iron-Proxy folder, Iron-Proxy off,
this PC's Claude Code login, a scratch git project): the agent ran
`git status`, `node -v`, `npm test` and `python -m pytest` by name; its
`git commit` carded once-only and, allowed, was authored with this PC's git
identity (the project has none of its own); the names its commands received
(names only) held none of the planted secret, the install token, Iron-Proxy's,
Claude Code's markers or the venv's, and held PATH, USERPROFILE, HOMEDRIVE,
HOMEPATH, APPDATA, LOCALAPPDATA, TEMP, PNPM_HOME and NVM_HOME. Branching the
session from a reply gave a new session on its own branch from `main`;
follow-ups came back from the session's model and were billed to it. On the
screen (`next dev` 8818): the Next step buttons filled the box; Ctrl+K in the
harness opened the model menu and from the title bar's search the palette;
Ctrl+L focused the box; Esc stopped a running reply ("Cut off after 3.1 s");
the search found sessions by folder and title; Branch from a question opened
the new session with the question in its box and "Branched from …"; Rewind to
here on the committed turn showed the commits sentence; 390 px, no sideways
pan.

Still open: a claude-cli session returned empty replies from the turn after
it was asked to dump its environment (even "Say hello" came back empty; a
fresh session answered) — seen live, not explained, not this change's code.
Ctrl+N works in the desktop app (its menu has no Ctrl+N) and in the e2e; in an
ordinary Chrome or Edge tab the browser keeps Ctrl+N for a new window and the
page never sees it (the New session button is the way there). A rewound or
branched question's "/" skill is not put back in the box. The branch
name comes from the session title's first words. The account carried by a
branch was not tried live with Iron-Proxy on.

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
