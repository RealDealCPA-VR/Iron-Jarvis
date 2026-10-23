# IJ harness walkthrough: three tasks, press by press

Written 2026-10-23 for Phase 7 of `docs/IJ-HARNESS-PLAN-2.md` (target
v1.378.0). The same three tasks, step by step, in the IJ harness (driven for
real) and in the Claude Code desktop app (counted from its documentation, not
driven).

## How it was run

**IJ harness: driven for real.** A scratch Iron Jarvis daemon (port 8827, a
fresh home whose default model is claude-cli, this PC's own Claude Code
login, Iron-Proxy off) and the dashboard (`next dev` on 8828), driven by
Playwright on the installed Edge at 1280 × 800. The model was the real
claude-cli default (the receipt says Opus 4.8). The project was a scratch git
repository made for this in the temp folder (`shop`: a small cart module with
pytest tests and a static shop page served by `python -m http.server` on
port 8821).

**The pull request step ran against a stand-in.** The repository's `origin`
has a GitHub-shaped address, but pushes go to a local bare repository
(`pushInsteadOf`), and `gh` was a small script on the scratch daemon's PATH
that records its arguments and answers like a signed-in GitHub CLI (it
printed a made-up pull request address). No GitHub repository or pull
request was created, and the real GitHub was not involved.

**Claude Code desktop: not driven.** This agent cannot drive the Claude
desktop app, so its screenshots are marked "not captured: needs the user or
computer use". Its press counts come from Claude Code's desktop
documentation, <https://code.claude.com/docs/en/desktop.md> (read
2026-10-23). Each count is labelled:

- **documented** — the page says which control or key does it;
- **estimated** — the page does not say, and the number is a judgement
  (for example, how many presses picking a folder takes, or how many edits
  the model makes and therefore how many approvals Manual mode asks for).

What the page says that the counts rely on: a session starts from the
environment selector and the project folder control and Enter; "By default,
**Worktree** is off and Claude works in the project folder itself" (turned on
in the project folder control); `Cmd`+`N` is New session (`Ctrl`+`N` on
Windows); the diff view "lets you review modifications file by file" and
opens by clicking the line counts in the Changes card (or `Cmd`+`Shift`+`D`);
the terminal opens with `Ctrl`+`` ` ``; "Claude can start a dev server and
open it in the Browser pane to verify its changes", "Your local dev servers
and project files don't need approval", and the Browser pane opens with
`Cmd`+`Shift`+`B`; there is a **Create PR** button (the page's SSH section
says Claude "pushes the branch and runs `gh pr create`" when you click it),
and PR monitoring needs `gh`. The page documents no Commit or Push control.

**Which permission mode a new session starts in is not documented.** The page
lists Manual ("Claude asks before editing files or running commands. You see a
diff and can accept or reject each change") under the settings key `default`,
but also says "To approve each change yourself, switch to Manual", that a mode
you pick "is remembered per folder", and that `permissions.defaultMode` sets
it. So every Claude Code approval count below is a RANGE: Manual (every edit
and every command asks) at the top, Auto (no routine prompts; a background
classifier checks instead) at the bottom; Accept edits (edits pass, other
commands ask) sits between. (The review of this walkthrough corrected an
earlier draft that called Manual the default.)

**Counting rules (both apps).** A click is one press; a key that is not
typing (Enter to send, Escape, a shortcut) is one press; typing the message
itself is free; filling any other field (a folder path, an address) counts
as one press (a paste); each approval card answered is one press. A step that
leaves a dialog open counts the press that closes it.

## Task 1 — a bug fix with a test

"Bug: cart.total() ignores each item's qty — two pens at $1.50 should cost
$3.00. Fix it, add a test for quantities in tests/test_cart.py, and run the
tests with python -m pytest." Worked in the folder directly (no branch).
The model read two files, edited `cart.py` and `tests/test_cart.py`, ran
`python -m pytest` (4 passed) — 24 s.

| Step | IJ harness presses | Claude Code presses | Screenshot |
|---|---|---|---|
| Start a session on the folder | **4**: New session · paste the folder · "Work on a new branch" off · Start (the first session in this window; a later one gets the folder filled in, see Task 2) | **3** (estimated): `Ctrl`+`N` (documented) · the project folder control (documented) · the folder in its list (estimated); Worktree is off by default (documented) | [IJ](walkthrough/t1-start.jpg) · Claude Code: not captured: needs the user or computer use |
| Ask | **1**: Enter | **1**: Enter (documented) | [IJ](walkthrough/t1-ask.jpg) · not captured |
| Approve what it asks | **1**: the test command's card (`python -m pytest`), answered "Allow for this session"; the two edits needed no card (the default, "Ask when risky", lets edits through) | **0–3** (estimated): this work was 2 edits + 1 command; Manual asks before each (3), Accept edits only before the command (1), Auto before none (0) — the modes are documented, which one a new session starts in is not | [IJ card](walkthrough/t1-card.jpg), [IJ after](walkthrough/t1-approve.jpg) · not captured |
| See the change | **3**: the reply's "2 files changed" line · a file (its diff opens) · Escape (the diff is a dialog). Each further file: 2 more | **1**: the line counts in the Changes card open the diff view (documented); it shows the change "file by file" — whether a further file takes a press is not documented | [IJ](walkthrough/t1-changes.jpg) · not captured |
| Run or see the test | **2**: the reply's work line · the test step (the command and pytest's output: 4 passed); it read "Ran · shell" when this was driven, "Ran python" since the review | **1** (estimated): open the command's output in the transcript (not documented); or `Ctrl`+`` ` `` (documented) and run it yourself | [IJ](walkthrough/t1-test.jpg) · not captured |
| **Total** | **11** | **6–9** (estimated) | |

## Task 2 — a UI change checked in the browser

"On the shop page (web/style.css, served at http://localhost:8821/), make
the Buy button green with rounded corners. Then open the page in your
browser and check that the button looks right." Worked in the folder
directly. The model read two files, changed `.buy` in `web/style.css`, opened
the page in its own browser and took a screenshot (`page_open`,
`page_screenshot`) — 23 s.

| Step | IJ harness presses | Claude Code presses | Screenshot |
|---|---|---|---|
| Start a session on the folder | **3**: New session (the folder is filled in from the session in front) · "Work on a new branch" off · Start. **Was 4** before this phase's fix (the box was empty) | **1–3** (estimated): `Ctrl`+`N` (documented); whether the last project folder is kept is not documented | [IJ](walkthrough/t2-start.jpg) · not captured |
| Ask | **1**: Enter | **1**: Enter (documented) | [IJ](walkthrough/t2-ask.jpg) · not captured |
| Approve what it asks | **1**: the card for opening `http://localhost:8821/` in the agent's browser ("It is not the page open in this session's Browser") | **0–1** (estimated): the edit, in Manual only (documented); opening the local dev server needs none (documented: "Your local dev servers and project files don't need approval") | [IJ card](walkthrough/t2-card.jpg), [IJ after](walkthrough/t2-approve.jpg) · not captured |
| See the change | **3**: the "1 file changed" line · the file · Escape | **1**: the line counts in the Changes card (documented) | [IJ](walkthrough/t2-changes.jpg) · not captured |
| See the page in the Browser pane | **1**: Browser — "The agent is on localhost:8821" and the picture its browser took after its last action. **2** for the live page: Browser · the address under "Seen in this session" | **1**: `Ctrl`+`Shift`+`B` or Browser in the title bar (documented); Claude opens the dev server there itself (documented) | [IJ](walkthrough/t2-browser.jpg), [IJ live page](walkthrough/t2-live.jpg) · not captured |
| **Total** | **9** (10 with the live page) | **4–7** (estimated) | |

## Task 3 — a feature on a branch, ending in a pull request

"Add a discount(total, code) function to cart.py: the code SAVE10 takes 10%
off, any other code changes nothing, and the result is rounded to cents. Add
tests for it in tests/test_cart.py and run them." On its own branch. The
model read two files, edited both, ran the tests in the worktree (5 passed) —
25 s. The main checkout stayed on `main`, untouched by this session.

| Step | IJ harness presses | Claude Code presses | Screenshot |
|---|---|---|---|
| Start a session on the folder, on a branch | **1**: the folder group's **+** (starts at once on `ij/session-eb77` from `main`, in `shop.ij-worktrees`) | **4** (estimated): `Ctrl`+`N` · the project folder control · the folder (estimated) · turn on Worktree (documented) | [IJ](walkthrough/t3-start.jpg) · not captured |
| Ask | **1**: Enter | **1**: Enter (documented) | [IJ](walkthrough/t3-ask.jpg) · not captured |
| Approve what it asks | **1**: the test command's card | **0–3** (estimated): 2 edits + 1 command — 3 in Manual, 1 in Accept edits, 0 in Auto | [IJ card](walkthrough/t3-card.jpg), [IJ after](walkthrough/t3-approve.jpg) · not captured |
| See the change | **3**: Changes (it opens on the Branch view since this phase's fix; its file list is already open) · a file · Escape. **Was 4** (Changes · Branch · a file · Escape) | **1**: the line counts in the Changes card (documented) | [IJ](walkthrough/t3-after-changes.jpg) · not captured |
| Commit | **2**: Commit (the session's model drafted "Add discount function with SAVE10 code to cart" and a body) · Commit | **1–2** (estimated): no Commit control is documented — ask Claude in the chat (Enter), and allow its `git commit` in Manual | [IJ](walkthrough/t3-commit.jpg) (taken before the review's fix: the line beside "Committed" still showed the branch from before the commit) · not captured |
| Push | **1**: Push (the local bare remote got `ij/session-eb77`). Optional: Create PR pushes first when the remote lacks the newest commit | **0** (estimated): the documentation says Claude pushes the branch when you click Create PR | [IJ](walkthrough/t3-push.jpg) · not captured |
| Open the PR | **2**: Create PR (a box with the drafted title and body) · Create pull request — answered by the stand-in `gh`: "Pull request #1 is open" | **1–3**: Create PR (documented) + allowing Claude's `git push` / `gh pr create` in Manual (estimated 0–2) | [IJ](walkthrough/t3-pr.jpg) · not captured |
| **Total** | **11** (10 without the optional Push) | **8–14** (estimated) | |

## Every step was possible in the IJ harness

All 17 steps of the three tasks were done in the IJ harness screen, with the
real model, and checked on disk: task 1's and task 2's edits in the folder,
task 3's commit on `ij/session-eb77` in the bare remote with the main
checkout still on `main`.

**Presses, against the plan's "no more presses than in Claude Code": not
met.** Totals (IJ harness / Claude Code): task 1 **11 / 6–9**, task 2
**9 / 4–7**, task 3 **11 / 8–14**. Every Claude Code total is an estimate
from its documentation, and its range is mostly the permission mode a new
session starts in, which the page does not state. Tasks 1 and 2 took more
presses in the IJ harness whatever that mode is; task 3 took fewer than
Manual would and more than Auto would. The gaps below are where the
difference comes from.

## Small fixes made in this phase (each with a test that failed first)

- **New session opens on the folder of the session in front**
  (`components/harness/IjHarnessView.tsx`). Before, its box came from the
  Terminals view (the terminal in front, else the last folder picked there):
  empty in this window, so a second session in the same folder meant pasting
  the folder again. Task 2's start: 4 presses → 3.
  `dashboard/__tests__/ij-harness-new-folder-v1378.test.tsx`; four mutations,
  all red.
- **In a session on its own branch, Changes opens on the Branch view**
  (`components/harness/HarnessPanes.tsx`). Before, every look at the
  branch's diff and its Commit / Push / Create PR buttons took Changes, then
  Branch. "This session" (the undo journal) is one press away; a session in
  its folder directly is unchanged. Task 3's "see the change": 4 → 3.
  `dashboard/__tests__/ij-harness-changes-branch-default-v1378.test.tsx`;
  three mutations, all red.

The review of this phase changed four more things, each with a test that
failed first (`dashboard/__tests__/ij-harness-phase7-review-v1378.test.tsx`,
`tests/test_step_target_v1378.py`, `tests/test_chat_changes_whole_path_v1378.py`):

- Changes opens on the Branch view only while the branch is live work. A
  session whose worktree was removed (its Branch view is one sentence) or
  whose branch was merged (its diff against the base is empty) opens on
  "This session", as before the phase. The Branch view does show work that
  is not committed yet, so a live branch keeps the new first view.
- After Commit, Push or Create PR, the branch is read again BEFORE the note
  says what happened, so "Committed …" no longer sits beside the line from
  before the commit (and a grey Push). No press changed.
- A finished reply's steps say what they touched ("Ran python", "Changed
  cart.py"), the same words the live rows used. No press changed.
- A changed file the model named by its whole path is listed by its path
  in the folder (`web/style.css`), as the Branch view lists it.

## Next gaps

Where the IJ harness still needs more presses than Claude Code (or shows
less), in plain words:

1. **Seeing a change takes a press per file, and a dialog to close.** The
   reply's "N files changed" line opens a list; each file is one more press
   and its diff is a dialog closed with Escape. Claude Code's line counts
   open its diff view in a pane, "file by file" (its page does not say
   whether a further file takes a press). (Task 1: 3 vs 1; task 2: 3 vs 1;
   task 3: 3 vs 1.)
2. **Working in the folder directly costs a press each time.** "Work on a
   new branch" is on by default in a git folder (the user's decision 1 of
   this plan), so a session in the folder directly needs the switch turned
   off, and the folder group's **+** always starts on a branch. Claude Code is
   the reverse (Worktree off by default), which is why task 3 was cheaper in
   the IJ harness (1 vs about 4) and tasks 1 and 2 were dearer.
3. **The test run takes two presses to see.** The reply's work line, then
   the step. (A finished reply's steps used to read "Ran · shell",
   "Changed · edit_file"; since the review they say "Ran python", "Changed
   cart.py", as the live rows do.) The step's output still opens with the
   native runtime's "[warning] sandbox: … NOT enforced …" line.
4. **Create PR is two presses.** It opens a box with the drafted title and
   body and then needs "Create pull request"; Claude Code's page mentions a
   Create PR button and says nothing of a box after it. (The box is the
   chance to read the title; a one-press path would use the draft as it
   is.)
5. **The live page is two presses.** Browser shows the picture the agent's
   browser took; the live page needs the address under "Seen in this
   session" as well. Claude Code's Browser pane shows the live page (it is
   where Claude opened it).
6. **Seen along the way (no press difference), both fixed in the review:**
   right after Commit, the Branch line still read "0 commits … 2 files not
   committed · not on origin yet" and Push was greyed until the branch was
   read again (a moment later); a reply in the folder directly listed its
   changed file by its whole path (`C:/Users/…/ij-walkth…`) where the Branch
   view says the path in the folder.

What the IJ harness did with fewer presses: approvals compared with Claude
Code's Manual mode (its default lets edits through and asks for commands,
and "Allow for this session" covers the rest of a session; Claude Code's
Auto mode would ask for none), a session on a branch in one press, and a
commit whose message the model drafted.
