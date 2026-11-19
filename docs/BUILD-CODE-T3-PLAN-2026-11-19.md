# Build › Code: checkpoints, switching tools, comments on changes

A build plan, written 2026-11-19 against master v1.542.2.

**Status.** Release A (feature 1, phases 1a to 1c) is built in v1.543.0, with
the recommended choices D1 and D2. Releases B, C and D are not built.

Two things differ from the text below, both on purpose:

- A new file that git already holds (staged, or committed since the message)
  does not stop a rewind. It is moved to the trash like any other new file.
  Coding tools commit often, and stopping there would make rewind unusable.
- A rewind also waits while another session is working on a reply in the same
  folder. The plan did not name that case.
- "Byte for byte" holds for Undo, and for a rewind wherever git changes no
  line endings. Where it does, a checkpoint does not record which kind a file
  had, so a restored file keeps the kind it has now. That is exact unless the
  line endings themselves were changed since the message, or the file was
  deleted (the preview says so). Exact bytes in those two cases would need a
  second snapshot of each message: an open decision.
- An independent review of the first version found that git itself deletes
  what stands in a restored file's way and writes over files it ignores. The
  restore now refuses or leaves those, and copies everything it replaces to
  the trash first.

It comes from a comparison with t3code, an open-source app that drives the
same coding tools. Three of its ideas are worth having. Ideas only: no t3code
code is taken.

## What you will get

1. **A checkpoint at every message.** "Rewind to here" puts back the
   conversation AND the files in one action, with a preview first and an Undo
   after. Today it rewinds the conversation only.
2. **Switch the coding tool inside one session.** A session that ran on Claude
   Code carries on with Codex (or any other tool) in the same conversation.
   The new tool is given a written hand-off. The limit card gets a "Continue
   on another tool" press.
3. **Comment on a changed line.** In the Changes view, press a line or a
   block, type a comment, and it rides your next message to the tool.

## Decisions for you

Each has my recommendation first. Nothing is built until you answer.

| # | Decision | Recommended | Other choice |
|---|---|---|---|
| D1 | Where a checkpoint is kept | In the folder's own git repository, under a hidden name | A private store under Jarvis's home |
| D2 | What "Rewind to here" does by default | Conversation and files, two presses, preview first | Conversation only stays the default; files are a second press |
| D3 | Folders that are not git repositories | Later phase (1d), with a private store | Leave them without checkpoints |
| D4 | How a tool switch is stored | The same session row | A new linked session shown as one conversation |
| D5 | What the hand-off contains | Your messages, the tool's final replies, files changed, commands by name. No command output | Also the end of each command's output |
| D6 | Consent for a switch | Two presses every time, naming the tool and what it will be sent | One press |
| D7 | Where comments work | Both Code views at once | Build › Code first |

Why D1: I tested it in a scratch repository on this PC. A hidden name that
points straight at a snapshot survives git's clean-up, does not show in the
history, branch or tag lists, and is removed with the session. The same
snapshot with no name was deleted by the clean-up.

Why D5: the rule today is that tool output is never copied into a hand-off,
because output can hold secrets and planted instructions. t3code copies it.

## Order and releases

One release per feature, in this order. Each is built by one agent, checked by
an independent reviewer, and every guard gets a mutation check.

| Release | Contents | Size | Why this order |
|---|---|---|---|
| A | Feature 1, phases 1a to 1c | Medium | 1a closes a real exposure and a bug that exist today |
| B | Feature 3 | Medium | Independent of the others; mostly screen work |
| C | Feature 2, phases 2a to 2c | Large | Touches the most assumptions; uses A's checkpoints |
| D | Phase 1d, if you say yes to D3 | Medium | Needs a new store |

These three files are touched by all three features, so only one agent works
in them at a time: `hosted/service.py`,
`dashboard/components/buildCode/BuildCodeView.tsx`,
`dashboard/components/buildCode/CodeConversation.tsx`.

---

## Feature 1: checkpoints and one-press rewind

### What exists today

- Each message already records a snapshot of the folder
  (`hosted/panes.py` `record_turn_start`, called from `service.py` `_drive`).
  It is a git tree id kept in `<home>/hosted/<sid>/turn_trees.json`.
- **Nothing keeps that snapshot alive.** No ref points at it, so git's own
  clean-up can delete it, by default after about two weeks.
- **A bug follows from that.** When the snapshot is gone, "Revert this file"
  (`revert_file`) reads the file as "not in the snapshot". A file that existed
  before is then refused with the wrong words, or moved to the trash as if it
  were new.
- The list keeps 60 rows, and idle reads share the 60 with message snapshots,
  so a long session loses its early checkpoints.
- "Rewind to here" (`hosted/branching.py`) rewinds the conversation for Claude
  Code, Codex and Pi. Its own text says files are never touched. OpenCode,
  Grok and Antigravity have no rewind.
- "Revert this file" puts back one file at a time, in git folders only.

### Design

**Phase 1a: checkpoints that last.**
- After the snapshot, write a hidden ref that points at the tree:
  `refs/ironjarvis/checkpoints/<sid>/<n>`. No commit is made. Store the ref
  name in the row.
- Count message checkpoints and idle reads separately: up to 300 message
  checkpoints and 30 idle reads per session. A checkpoint that falls off the
  list has its ref deleted.
- Delete a session's refs when the session is deleted
  (`service.delete`, beside `forget_kept_index`). Archive keeps them.
- Fix the bug: before any revert, check the snapshot still exists
  (`git cat-file -e <tree>^{tree}`). If it does not, refuse in words:
  "The checkpoint for this message is no longer kept, so nothing was changed."
- A message whose snapshot could not be taken (the 3-second wait ran out, a
  network folder, not a git folder) is marked "no checkpoint", and the screen
  does not offer a file rewind for it.

**Phase 1b: rewind files too.**
- New `panes.plan_restore(sid, turn)` only reads. It compares the checkpoint
  with a fresh snapshot of the folder and returns three lists:
  files that go back, new files that move to the trash, and problems.
- Problems stop the whole rewind before anything changes. They are: a file you
  staged as new since, a link, a file another program holds open, a submodule.
- The screen shows the lists first. The second press does it.
- The order on the second press:
  1. Check again that no reply, helper or background work is running.
  2. Record a checkpoint of the folder as it is now ("before rewind").
  3. Stop the tool's process, and name any program that stopped with it.
  4. Copy every file that will change into `<home>/trash/<stamp>/code-rewind/<sid>/`.
  5. Put the files back by their exact paths
     (`git restore --source=<tree> --worktree --pathspec-from-file`). Move the
     new files to the trash. Your staging area is not touched.
  6. Rewind the conversation through the tool, as today.
  7. Write one line in the conversation saying what went back.
- **Undo rewind.** The "before rewind" checkpoint makes the rewind reversible:
  one press restores the files to how they were. The conversation part cannot
  be undone for Codex and Pi, and the words say so before the second press.
- Only checkpoints taken at a message are used. The session's first base, which
  is a commit, is never restored, because that would wipe uncommitted work you
  had before the session began.
- If step 6 fails after the files went back, the line says exactly that, and
  Undo rewind is offered.

**Phase 1c: tools with no conversation rewind.**
- OpenCode, Grok and Antigravity get "Put the files back to before this
  message". The words say the conversation is unchanged.

**Phase 1d (decision D3): folders that are not git repositories.**
- A private store under `<home>/hosted/<sid>/` holds the checkpoints, bounded
  by the same file limits as the project map. Details are planned only after
  a yes.

### Rules this must keep

- Your index, branch and uncommitted work from before the session are never
  changed.
- Every git call runs under the session's git lock, off the event loop,
  bounded, through the existing helpers.
- A path outside the folder is refused by its text first. A network folder is
  refused before anything is looked up.
- Never while a reply, a helper, the tool's own background reply or a hand-off
  helper is running.
- Copy before overwrite. Refuse when the copy cannot be made.
- The v1.441.0 rule "files are never touched" by a rewind is replaced by this
  feature, and the area rules file is updated to say so.

### Done when

- A checkpoint from a message survives `git gc --prune=now` in a test repo.
- `git log --all`, `git branch -a` and `git tag` show nothing new.
- Deleting the session leaves no `refs/ironjarvis/` name behind.
- A rewind in a test repo returns every file byte for byte, moves new files to
  the trash, and leaves `git status` for staged files as it was.
- A rewind with one problem file changes nothing.
- Undo rewind returns the folder byte for byte.
- A live run on a scratch daemon with the real Claude Code and the real Codex:
  three messages that edit files, rewind to the first, check files and
  conversation.

### Tests to extend

`tests/test_build_code_revert_v1420.py`, `test_build_code_revert_review_v1420.py`,
`test_build_code_turn_tree_v14250.py`, `test_build_app_branch_v14410.py`,
`test_second_review_hosted_kept_v1510.py`,
`dashboard/__tests__/build-code-branch-v1441-0.test.tsx`,
`code-panes-revert-v1420.test.tsx`.

### Risks

- A dev server the tool started may write files during the restore. Step 3
  stops the tool, and the programs it started are named.
- Submodules and ignored files (`node_modules`, `.env`) are not in a
  checkpoint, so they are neither restored nor removed. The preview says so.
- Very large new files can make the 3-second snapshot wait run out. That
  message then has no checkpoint, and the screen says so.
- `git push --mirror` would push the hidden refs. Ordinary pushes do not.

---

## Feature 2: switch the coding tool inside one session

### What exists today

- A session's tool is fixed. The save route ignores a tool change
  (`store.py` `EDITABLE`), and the picker locks the tool (`lockTool`).
- Much per-tool state hangs off the session: the tool's own conversation id,
  rewind references, limit card and wait, cut-off record, the queue, the mode
  list, the account's vendor.
- Three pieces already exist to build on:
  - "Continue on another account" (`service.continue_on`): same row, new
    process, one line in the conversation, plan first then act.
  - The hand-off helpers (`hosted/handoff.py`): mode mapping that is never
    looser, and an account rule that never leaves the account group.
  - The archive summary (`hosted/archive_summary.py`): a masked summary built
    from our own record, not by a model.

### Design

**The switch is a service action, not a setting.**
`POST /build/app/sessions/{id}/switch-tool {tool, model?, effort?, confirm}`:

1. Refuse in words when: a reply runs, background work runs, the session is
   open in a terminal, archived, a helper session, or a rewind is pending.
2. Work out everything first, and refuse before anything changes:
   - **Account.** Same account group as the session's account, or none. Never
     across groups. Tools that sign in on their own take none.
   - **Mode.** The nearest mode of the new tool that is not looser. Full access
     is never carried over.
   - **Model and effort.** The folder's default for the new tool, else your
     last pick, else the tool's default.
3. Stop the old tool's process and name any program that stopped with it.
4. Write the earlier history into our own record if it only lives in the old
   tool's files (a session opened from a terminal or from disk).
5. Park the old tool's state in `tool_sessions.json`: its conversation id,
   account, model, effort, mode and the point in the record where it stopped.
6. Clear what belongs to the old tool: limit card and wait, cut-off record,
   queued items that are that tool's own commands.
7. Change the row to the new tool.
8. Write one line: "Switched from Claude Code to Codex."

**The hand-off.**
- Built from our own record, never by a model, so it works the same for every
  tool. It holds your first message, your later messages, the tool's final
  replies (newest first until the size limit), files changed, commands by
  name with whether they failed, and open plan steps.
- Every piece is masked before it is cut, then the whole text is masked again.
  The old tool's words go inside the "data, not instructions" fence.
- Size limit 12,000 characters. Saved at `<home>/handoffs/<sid>/switch-<n>.md`.
- **It is sent with your next message, not as a turn of its own.** Switching
  and typing nothing sends nothing and spends nothing.
- The conversation shows a foldable "Hand-off to Codex" row with the exact
  text, so you can see what the new tool was told.

**Switching back (phase 2b).**
- The parked state lets the earlier tool resume its own conversation. It is
  sent only what happened since it stopped.
- If that resume fails, it gets a full hand-off like a new tool.

**The screen.**
- The session's picker unlocks its tool list. Picking another tool shows one
  line: which tool, which account, and "It will be given a summary of this
  conversation." The second press switches.
- If the old tool ran a model on your own network and the new one is a cloud
  tool, the line says the summary leaves this PC.
- The limit card gains "Continue on another tool" (phase 2c).
- Each reply's line keeps the tool that wrote it. The fold reads the tool from
  each turn's own record, so earlier replies still say "Claude Code" after the
  switch.
- "Rewind to here" on a message from before the switch is offered as files
  only, because that part of the conversation belongs to the other tool.

### Rules this must keep

- One conversation, one row, one process. A parked conversation id still counts
  as held, so it cannot be opened in a second session.
- An account never crosses an account group, and never crosses vendors.
- Full access always needs its own two presses.
- Never while background work runs.
- Tool output is never copied into the hand-off (unless you choose otherwise in
  D5).

### Phases

- **2a** switch forward, hand-off with the next message, per-turn tool on the
  screen.
- **2b** switch back resumes the earlier tool's own conversation.
- **2c** "Continue on another tool" on the limit card.

### Done when

- A session with three Claude Code replies switches to Codex; the next message
  reaches Codex with the hand-off; earlier replies still read "Claude Code".
- A switch across account groups is refused with nothing changed.
- A planted secret in a reply is masked in the saved hand-off.
- Switching with no message sends nothing (checked by counting what the tool
  process received).
- A live run on a scratch daemon: Claude Code to Codex and back, with the real
  tools.

### Tests to extend

`tests/test_code_handoff_v1474.py`, `test_code_handoff_rules_v1474.py`,
`test_build_app_model_change_v1475.py`, `test_build_app_routes_v1409.py`,
`test_build_app_carry_on_v14220.py`,
`dashboard/__tests__/model-picker-v1475.test.tsx` (its "the tool is fixed"
case moves on purpose).

### Risks

- The largest risk is a missed assumption that the tool never changes. The
  research lists the known ones (`LiveSession.harness`, the driver choice, the
  resume flag, the uniqueness check). The reviewer's first task is to hunt for
  more.
- A summary is not the full conversation. The new tool can miss detail. The
  hand-off row shows what it was told, and the full record stays on screen.
- Moving a conversation between two real accounts of different vendors cannot
  be fully tested on this PC without both signed in.

---

## Feature 3: comment on a changed line

### What exists today

- The Changes view (`components/harness/ChangesReview.tsx`, drawn by both Code
  views) has one action per block: "Open at line N". Lines have no handle.
- A message can carry words and files. Nothing carries a comment.
- The pattern to copy is the browser add-on's "Point at it": the page sends a
  structured field, and the daemon writes the text the tool reads.

### Design

- **Where you comment.** A "Comment" button beside "Open at line N" on each
  block. In phase 3b, a press on a line number comments on that line, and
  Shift extends it to a range.
- **Where comments wait.** A small store per session, saved in the browser
  like the Reviewed marks, so comments survive a tab switch and a reload.
  Limits: 20 comments, 4,000 characters of quoted code each, 2,000 characters
  of comment each.
- **What a comment holds.** The file, the side (old or new), the line range,
  the quoted lines, your words, and the file's change mark. If the file
  changes again, the chip says "changed since".
- **In the message box.** A row of chips above the box, one per comment, each
  with a remove button. The same component serves both Code views.
- **How it is sent.** As a structured `comments` field on the message, the
  queue item and the saved question. The daemon then:
  1. checks each path the way attached files are checked;
  2. masks and bounds each quote;
  3. writes one block per comment. Your words sit outside the fence, because
     they are your instruction. The path and quoted code sit inside the "data,
     not instructions" fence and are scanned.
- **While a reply runs.** Comments ride a queued message. A note keeps them in
  the box for the next message. A refused send puts them back.
- **After sending.** The sent message shows its comment chips (phase 3c), and
  a rewind puts them back in the box.
- **Older daemon.** The screen offers comments only when the daemon says it
  takes them, so a comment is never silently dropped.

### Rules this must keep

- A streamed word never redraws the Changes rows. The row components keep
  plain-value props.
- Enter and Escape in the comment field never reach the message box.
- The word is "Comment". "Note" already means a message sent while a reply
  runs.
- The one-row toolbar is unchanged: chips go above the box.

### Phases

- **3a** block comments, chips, daemon side, both views.
- **3b** line and range comments.
- **3c** chips on the sent message, restored by a rewind.

### Done when

- A comment on a block reaches the tool as the block text plus your words,
  checked against what the tool process received, for Claude Code, Codex and
  Pi.
- A quote holding a key-shaped value arrives masked.
- A quote holding "ignore your instructions" arrives inside the fence and the
  safety scan sees it.
- 30 streamed words redraw the Changes rows zero times.
- Comments survive a reload, ride a queued message, and return after a
  refused send.

### Tests to extend

`dashboard/__tests__/review-pane-v1405.test.tsx`, `review-pane-memo-v1405.test.tsx`,
`build-code-panes-v1414.test.tsx`, `build-app-view-v1409.test.tsx` (the draw
count), `dashboard/e2e/review-pane.spec.ts`, and Python
`tests/test_build_slash_at_v1373.py`, `test_browser_pointed_v14009.py` (the
pattern).

### Risks

- Line numbers go stale when the file changes again. The change mark covers
  it.
- Wide view draws each file twice, so the comment form needs one owner.
- The real tools may repeat the comment block in their own transcripts.

---

## What this plan rests on

**Checked by me:**
- A hidden ref pointing at a tree survives clean-up and stays out of history,
  branch and tag lists. An unreferenced tree is deleted. Restoring by exact
  paths leaves the staging area alone. (Scratch repository, git on this PC.)
- `panes.py` keeps no ref, `_in_tree` returns False for a missing snapshot,
  and the list of 60 is shared with idle reads.
- `store.py` `EDITABLE` has no tool field, and `service.py` resumes whenever a
  conversation id is set.
- Which tools can rewind today (`branching.py` `CAPS`).

**From three research passes over master, not re-checked line by line:**
- The full list of per-tool assumptions in Feature 2.
- The composer and message-body details in Feature 3.
- The t3code details, read from a copy dated about 11 November with some
  files missing.

## Not in this plan

- An editable queue, find in session, one message to several tools, and commit
  and pull-request buttons in Build › Code. They were gaps 4 to 7 in the
  comparison.
- Running the project's own tests after any tool's reply. It is the strongest
  way to go past t3code and deserves its own plan.
