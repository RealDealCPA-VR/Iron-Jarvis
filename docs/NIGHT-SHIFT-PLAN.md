# Night shift, your phone, GitHub, and proof on your own code

*Drafted 2026-10-26 at v1.395.0, after the long-jobs and model-team plan
(`docs/LONG-JOBS-AND-MODEL-TEAM-PLAN.md`). Six features, the decisions they
need from you, and the order to build them in.*

## What you get

1. **Night shift.** You queue coding tasks before bed. Each runs on its own
   branch overnight, local models first, under your usage limits. You wake up
   to finished branches with test results and one summary.
2. **A benchmark made from your own code.** Past commits that fixed something
   are undone, and each model or team tries to redo the fix. You learn which
   model is best for YOUR code, not for a public test set.
3. **A second model checks the work.** Before a coding reply ends, a model
   from a different family reviews the change. A local model does the first
   pass.
4. **Your phone as the remote.** "fix the failing test in Open Source QB"
   from your phone starts a coding session. Its questions come to the phone,
   and the result and a short Timeline summary come back.
5. **It sees UI work.** For a change to a page, the reply shows a screenshot
   from before and after.
6. **A GitHub loop.** An issue you label starts a session. A review comment on
   its pull request starts a follow-up on the same branch. A red CI run starts
   one fix attempt on that branch.

## Where it stands (v1.395.0, read from the code)

Already there, and reused:
- A coding turn can be started by Iron Jarvis itself, with no page open. The
  account-limit wait and the restart resume already do this.
- Every coding session in a git folder gets its own branch in its own folder
  (v1.375.0). A follow-up is just a new turn in the same session.
- The finish check, the Timeline, the team router with its 80%/95% rules, the
  pay-per-use limit, keep-awake, resume after a restart, and stall notices.
- The phone channel, approval questions on the phone (v1.392.0), and the
  agent's own browser and screenshots.
- `gh`, pull requests, and reading a pull request's checks.
- The benchmark: tasks, a hidden check, repeats, reports.

Missing or unsafe for work nobody watches:
- **A card waits forever.** A question in a coding session has no time limit.
  Safety cards (git, a project's own commands, risky page clicks, spending)
  never go to the phone. A phone answer that comes more than 5 minutes after
  the question arrived is refused while the card is still waiting (a bug).
- **Emergency stop and the "how many at once" limit do not cover coding
  sessions.** They cover other agent runs only.
- **A branch folder has no `.venv` or `node_modules`.** Tests there fail or
  try to install, so the finish check's verdict is unreliable.
- **Overnight work stays uncommitted.** The agent asks before every commit,
  and nobody is there to answer.
- **No queue, no morning summary.** Nothing runs a coding task at a set time,
  and the "reply finished" notice says only the title and the time.
- **The phone cannot start a coding session.** It cannot name a folder
  either. Phone messages run at low trust, which blocks most coding tools.
- **Nothing knows a reply changed UI files**, and screenshots go only to the
  model, never into the reply.
- **GitHub cannot reach this PC.** Iron Jarvis listens only on this PC, so the
  loop has to ask GitHub with `gh` on a timer. Nothing reads issues, review
  comments or failed CI logs yet.
- **Nothing makes benchmark tasks from git history.**

## Decisions for you

**Decided 2026-10-26: you accepted all nine recommendations below.**

1. **A card that comes up overnight.** Recommended: it goes to your phone (if
   set up). If nobody answers in 15 minutes, that one step is declined. The
   task carries on with the rest of its work, and the summary lists the step
   under "needs you". Safety cards are declined the same way, never allowed.
   The alternatives:
   - the task waits until morning (it stops there);
   - approve everything (unsafe).
2. **Committing overnight work.** Recommended: when a task finishes, Iron
   Jarvis itself commits it to the task's own branch, the way the Commit
   button does, with the task's words as the message. It never pushes and
   never merges. The agent still asks before its own commits. Alternative:
   leave the work uncommitted for you.
3. **Tests in a branch folder.** Recommended: the branch's checks use your main
   folder's existing `.venv` / `node_modules` (nothing is installed). The
   summary says so. Alternative: install packages in each branch folder,
   which needs the network and time.
4. **What a night task may do.** Recommended:
   - It edits files and runs commands inside its own branch folder without
     asking, like "Approve for me".
   - The safety floors stay: git commit/push by the agent, a project's own
     commands, risky clicks, spending.
   - Each task has a time limit (90 minutes by default) and runs at most 2 at
     a time, one per folder.
   - Night tasks start between 11 PM and 7 AM; a task not started by 7 AM
     waits for the next night.
   - Emergency stop stops them all.
5. **What the phone may start.** Recommended:
   - Only your allowed phone sender.
   - Only in folders you have already used in Iron Jarvis, found by name.
   - Always on a new branch.
   - The same rules as a night task (decision 4).
   - The session runs at full trust inside its branch. Phone messages stay at
     low trust everywhere else.
   - Alternative: low trust. That makes coding from the phone nearly useless:
     no commands, no tests.
6. **What GitHub may start.** Recommended:
   - Only repositories you add, polled every 5 minutes.
   - An issue gets a session only when you add a label you choose
     (`jarvis`).
   - A review comment starts a follow-up only on Iron Jarvis's own pull
     requests.
   - A red CI run starts one fix attempt on that branch, at most 3 per pull
     request.
   - Never on other people's pull requests or forks. Nothing is ever merged.
7. **The second-model check.** Recommended: on for coding replies that changed
   files.
   - A local model reviews first.
   - A cloud model of a different family than the one that wrote the code
     reviews only for larger changes or when the local reviewer is unsure,
     and only within your usage limits.
   - The verdict shows on the reply. A "changes needed" verdict gets one more
     round to fix.
   - A setting turns it off.
8. **UI before/after.** Recommended: automatic when the session has a page
   address (the Browser pane) and the reply changed page files. "Before" is
   taken before the first page edit lands, "after" at the end. Both show on
   the reply line. Alternative: only when you ask.
9. **Your own-code benchmark.** Recommended:
   - Iron Jarvis and Open Source QB.
   - 20 fixing commits each: commits that changed code and added or changed a
     test.
   - Run overnight like tonight's benchmark.
   - The tasks stay on this PC and are never published.

## The order

The current queue first:
1. **Phase 7: Measured** (v1.396.0), then tonight's live run and the tuning
   (v1.396.1). Already under way.
2. **Try one task on several models** (the long-jobs plan's Phase 5 Part B,
   v1.397.0). Night shift can then queue a task as "try it on two models".

Then this plan:

| Phase | What | Target |
|---|---|---|
| N1 | Night shift: the queue, safe unattended runs | v1.398.0 |
| N2 | Night shift: the morning | v1.399.0 |
| P | Your phone as the remote | v1.400.0 |
| R | A second model checks the work | v1.401.0 |
| U | UI before/after | v1.402.0 |
| G | GitHub loop | v1.403.0 |
| B | Benchmark from your own code | v1.404.0 |

P and U touch different parts of the app and can be built side by side, and
so can G and B. That saves about a day, with more risk of merge conflicts in
the shared coding-session code.

The rest of part 3 of the harness plan (`docs/IJ-HARNESS-PLAN-3.md`, phases 4
to 10) comes after this plan.

## N1 — Night shift: the queue (target v1.398.0)

**What you see.** A **Tonight** list in Coding sessions. Add a task: a folder,
your words, optionally a model or "the team". Each one runs on its own branch
when the night window opens, and its row says queued / running / done /
needs you.

**How.**
- A night-queue loop (shaped like the account-wait loop) starts each task's
  coding session and first turn on the main loop, never on the scheduler's
  thread.
- The unattended rules (decisions 1 and 4):
  - Cards time out and are declined with a note.
  - The time limit per task.
  - At most 2 at once.
- Emergency stop and the "how many at once" limit cover coding sessions, the
  automatic resumes included.
- Branch folders use the main folder's environment (decision 3).
- Keep-awake holds while a night task is queued or running, not only while a
  turn is working.
- The phone's 5-minute refusal of a coding session's answer is fixed.

**Done when.**
- A scripted night runs three tasks in two folders. One card is declined
  after its wait. Emergency stop halfway stops the rest.
- Each finished task sits on its own branch, with its finish check run
  against the main folder's environment.

## N2 — Night shift: the morning (target v1.399.0)

**What you see.** One **Last night** summary: in the bell, on your phone (if
set up), and as a page in Coding sessions. Per task:
- the branch, finished or not;
- tests passed or failed;
- files changed;
- time and usage per account;
- what needs you.

Buttons: open the session, see the changes, push, open a pull request, throw
the branch away.

**How.**
- Each finished task is committed to its branch by Iron Jarvis (decision 2).
- The summary is built from each reply's own record (the finish check, the
  Timeline, the branch's status), never written by a model.
- One notice per night, not one per task.

**Done when.** The scripted night's summary matches what happened, and a task
that failed or needs you is at the top.

## P — Your phone as the remote (target v1.400.0)

**What you see.**
- "code Open Source QB: fix the failing test" (or `/code …`) from your phone
  starts a coding session on a new branch.
- Its questions come to the phone; you answer with a number or yes/no.
- When it ends, you get a short summary: what changed, tests, time, the
  branch, and the reasons for each member's pick.
- "status" lists your running coding sessions; "stop" stops one.

**How.**
- A name→folder lookup across projects and folders you have used. More than
  one match asks which.
- The phone's trust rule (decision 5).
- The result is read from the reply line, never a model's own summary of
  itself.

**Done when.** A scripted phone conversation starts a session, answers its
question, and receives the summary. An unknown folder name and a folder you
never used are refused in words.

## R — A second model checks the work (target v1.401.0)

**What you see.** Under a coding reply that changed files: "Checked by glm
(local): looks right", or "Checked by Codex: changes needed — …", and when
changes were needed, one more round that fixes them.

**How.**
- The finish check gains a review step.
- A reviewer from a different family than the code's writer, picked by the
  team router with a new "not the same family" rule. A local first pass;
  cloud only per decision 7.
- The verdict is read the safe way, from the reviewer's own last line, as in
  v1.395.0. It counts toward the reviewer's and the writer's records.

**Done when.**
- A scripted reply with a planted bug is caught by the reviewer and fixed in
  the extra round.
- A clean reply passes with one local review and no cloud call.

## U — UI before/after (target v1.402.0)

**What you see.** Under a reply that changed a page: two screenshots, before
and after, side by side, at the page address the session uses.

**How.**
- Changed files are classified as page files (by extension and folder).
- "Before" is captured just before the first page edit.
- "After" is captured at the end.
- Pictures are saved and shown on the reply line (they go only to the model
  today).

**Done when.** A scripted session with a small local page changes a button
color, and the reply shows both pictures.

## G — GitHub loop (target v1.403.0)

**What you see.** Settings › Coding › GitHub: repositories you add, the label,
and what may start work (decision 6). Sessions started this way say which
issue, comment or run started them.

**How.**
- A loop asks GitHub every 5 minutes through `gh` for new labelled issues,
  new review comments on Iron Jarvis's own pull requests, and failed checks
  on its own branches.
- An index from pull request to session. A follow-up is a new turn in the
  SAME session, on its branch.
- Failed CI logs are read with `gh run view --log-failed`, cut to the failing
  part, and scanned like any fetched text.
- One fix attempt per failure, at most 3 per pull request.

**Done when.** A scripted `gh` (no network) drives an issue → session → pull
request → review comment → follow-up → red check → fix attempt, each on the
same branch.

## B — Benchmark from your own code (target v1.404.0)

**What you see.** `bench-harness make-tasks --repo <folder> --count 20`, then
`bench-harness run --tasks-dir …` with any runner or the team. The report is
the same table as today, for your code.

**How.**
- From git history, pick commits that changed code and added or changed a
  test.
- The task folder is the parent commit. The hidden check is the commit's
  tests, renamed so they never collide. The task text comes from the commit
  message.
- Tasks are kept under the benchmark's own folder, never shipped.

**Done when.** Twenty tasks are made from Iron Jarvis's own history. Each
fails before the fix and passes with the commit's own change.

## What this plan does not change

- Nothing merges on its own, and nothing pushes on its own. You decide in the
  morning.
- Local only stays absolute.
- The safety floors stay app-only: git by the agent, a project's own
  commands, risky clicks, spending.
- The model you talk to is the one you picked.
- Pay-per-use never goes above your limit (default $0).

## Honest limits

- Overnight work will sometimes be wrong. That is why it stays on branches,
  and why the summary shows the tests and the reviewer's verdict.
- A declined card means some tasks finish only part of the way.
- Sharing the main folder's environment means a task that changes
  dependencies is tested against the old ones. The summary says so.
- GitHub polling sees events up to 5 minutes late.
- "Before" screenshots need the page to be served from the branch before the
  first edit. A page that needs a build step may show the last build.
