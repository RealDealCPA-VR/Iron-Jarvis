# IJ harness, part 3: measured against the best, and shown in public

Written 2026-10-24 against v1.382.0, for the user to review **before anything
is built**. Part 1 (`docs/IJ-HARNESS-PLAN.md`) built the harness and its
benchmark; part 2 (`docs/IJ-HARNESS-PLAN-2.md`) made it feel like Claude Code.
This part does two things:

1. Closes the gaps that keep the IJ harness from being the best coding harness
   rather than an equal one: finishing work properly, wasting fewer steps,
   running without the screen and inside a container, and the one thing no
   other harness can do (trying a task on several model families at once).
2. Measures that claim where others can check it: a harder benchmark of our
   own, then a public one (Terminal-Bench, run through Harbor), shown in the
   app and on a public leaderboard.

Every phase is built the way parts 1 and 2 were: a doer agent, then a separate
reviewer agent that tries to break it, each fix pinned by a test that fails on
the old code, a live run on a scratch daemon (never the user's app), and one
release per phase.

## Where it stands (v1.378.0 benchmark, read from the bench's own files)

| Setup | Passed | Mean time | Tool calls per task | List-price $ |
|---|---|---|---|---|
| Claude Code | 30/30 | 55.8 s | 4.8 | 6.19 |
| Jarvis on Claude (claude dialect) | 29/30 | 38.5 s | 6.9 | 3.98 |
| Codex | 30/30 | 29.6 s | 3.8 | n/a |
| Jarvis on Codex (gpt dialect) | 30/30 | 37.1 s | 7.4 | 1.02 |
| Jarvis on glm-5.3-flash (local) | 28/30 | 53.9 s | 9.2 | 0 |

What this shows and what it does not:

- On the same Claude model, the IJ harness was faster than Claude Code and
  about a third cheaper at list price.
- It takes 44–95% more tool calls than the vendor tools on the same model.
- Three setups scored 30/30. **The benchmark is at its ceiling**: it can say
  "matches", never "better". Each task ran once, and both tasks lost in that
  run passed twice when run alone, so one run per task is mostly noise.
- In 90 Jarvis runs no task used a part 2 tool (helpers, skills, the agent's
  browser). The tasks are small, single-folder, and have no web page.
- The bench records the vendor CLIs' model as "default", and the user's Claude
  settings changed between the last two runs. The two runs did not use the
  same model setup, and the record cannot say which models they were.

**Tools are not the gap.** `harness/parity.py` maps every tool Claude Code
2.1.306 (46) and Codex 0.157.0 (43) give their models. The ones marked "not
needed" are either done another way (a helper hands its result back by
itself; a turn already waits for apps that are starting) or are product
features (publishing, push notifications, onboarding). The only real
absences are language servers and tool search. The gap is what happens around the
model: how a turn finishes, how many steps it spends, where it can run, and
whether anyone outside this PC can check the numbers.

**Things that do not exist today** (checked in the code):

- No way to run the harness without the screen. `ironjarvis run` runs an
  agent session, not a harness turn. The bench's Jarvis runner calls the chat
  lane inside its own process. Claude Code has `claude -p`; Codex has
  `codex exec`.
- Never run on Linux. Every Python test job in CI runs on `windows-latest`
  (the Ubuntu jobs build the browser add-on and syntax-check the desktop
  files). The terminal panes (pywinpty) and OCR (winrt) are Windows-only
  dependencies. `core/procs` has POSIX branches, but nothing has driven them.
- No container to work in. The Docker sandbox (`sandbox/docker_runtime.py`)
  runs ONE command in a throwaway `python:3.12-slim` container, with the folder
  mounted, for runs that must be isolated (low trust). A session's files and
  commands live on this PC.
- No language servers. After an edit the harness runs a one-line syntax check
  (Python, JSON, TOML, notebooks, JavaScript); no type or lint errors.
- Only a folder that sets up its own harness layer gets automatic checks (its
  `plugins.toml` [[checks]], run after edits that match their file patterns).
  No turn checks its work before it says it is done.
- No editor integration (no VS Code, JetBrains or Zed door).
- No Agent Client Protocol (ACP) server.
- Docker Desktop is installed on this PC but was not running on 2026-10-24;
  Harbor is not installed.

## The public benchmark this plan aims at

**Terminal-Bench 4.0, run through Harbor.**

- **Why this one.** It ranks agent-plus-model pairs, not models alone, which
  is exactly what a harness is. Claude Code and Codex are built-in Harbor
  agents, so the comparison can be same model, same tasks, same machine:
  IJ on Claude vs Claude Code, IJ on GPT vs Codex. Its tasks run in Linux
  containers, each scored pass or fail by its own check.
- **How an agent plugs in.** Harbor runs a custom agent named as
  `module.path:ClassName`. `BaseInstalledAgent` installs the agent INSIDE the
  task's container and runs it there; `BaseAgent` drives the container from
  outside through its exec API.
- **What 4.0 changed.** It recalibrated task resources, gave every task an
  8-hour agent limit, fixed 19 tasks and removed 8 saturated ones. Some tasks
  need a GPU.
- **Results.** Harbor Hub stores results and builds leaderboards. A new board
  is private. Hub does not compute or verify scores: the owner enters them, so
  links to every run are what make a board believable. How results reach the
  official tbench.ai board is not described in the docs read for this plan.

**SWE-bench Pro (public set)** is the second choice: real issues in real
repositories, scored by their tests. The older SWE-bench Verified is widely
reported as contaminated, so this plan does not use it.

Sources (read 2026-10-24): <https://www.tbench.ai/news/terminal-bench-4-0>,
<https://www.tbench.ai/docs/run-terminal-bench-2-0>,
<https://docs.harborframework.com/agents/custom-agents.md>,
<https://docs.harborframework.com/harbor-hub/leaderboards.md>,
<https://docs.harborframework.com/sandboxes/pre-integrated-sandboxes.md>,
<https://snorkel.ai/leaderboard/terminal-bench/>,
<https://www.morphllm.com/swe-bench-pro>.

## Decisions for you before building

These change what gets built. Each has a recommendation; nothing starts until
you answer.

**Decided 2026-10-24: the user accepted all eleven recommendations** ("I
accept all eleven recommended decisions. Start Phase 1 with doer and reviewer
agents, and ship each phase when it's green"). The phases are built and
shipped in order.

1. **Headless runs: what may run without you there, and where it keeps its
   state (Phase 4).**
   - *Recommended (what may run):* by default a headless run asks nothing. It
     stops with a clear exit code and a list of what it needed approved.
     `--approve all` lets it do everything except the hard floors. Use that
     flag in containers and benchmarks, where nothing on this PC is at risk.
   - *Recommended (state):* it keeps its state in a folder of its own (by
     default beside the app's, or `--root`), never in the running app's
     database. Showing a headless run inside the app's Build view can come
     later.
2. **Paying for public runs (Phases 5 and 6).**
   - *Recommended:* API keys, paid per use, for every row: IJ and the vendor
     tools alike. Same model, same billing. Harbor's built-in Claude Code and
     Codex agents run that way too.
   - Never copy your Claude or ChatGPT sign-ins into a container.
   - The local model row is optional (free, but slow).
3. **How big, and how much money (Phase 6).**
   - *Recommended:* first a 5-task smoke run, which measures the cost per
     task. You then set a dollar cap.
   - The full set runs 3 times per task for the two same-model pairs, and the
     run stops at the cap.
   - Today's numbers cannot give an honest estimate: our own tasks cost about
     $0.13–0.21 each at list price on Claude, but Terminal-Bench tasks are far
     longer.
4. **Where Terminal-Bench runs (Phase 6).**
   - *Recommended:* in Docker on one of your own machines: this PC (Docker
     Desktop's Linux engine; Harbor on Windows is not proven, and WSL2 is the
     fallback) or one of your x86 Linux boxes.
   - Tasks that need a GPU are listed as "not run" rather than paid for in a
     cloud sandbox.
   - *Alternative:* a cloud sandbox Harbor supports (Modal, Daytona and
     others), which costs money and sends the work off your machines.
5. **Publishing (Phase 7).**
   - *Recommended:* a private Harbor Hub board first; it goes public only
     after you review it.
   - The words in the README and the Handbook follow the numbers: where the
     ranges overlap, "matches"; "ahead" only where they do not.
6. **The finish check (Phase 2).** *Recommended:* on by default in Build
   sessions, off in plain chat. Only for a turn that changed files, and at
   most one extra model round per turn.
7. **Code awareness (Phase 3).**
   - *Recommended:* a project map at the start of a session, plus the
     project's OWN linters and type checkers when they are installed (ruff,
     mypy, eslint, tsc). No bundled language servers.
   - Revisit language servers only if the benchmark shows the gap.
8. **Trying a task on several models at once (Phase 8).**
   - *Recommended:* build it, opt-in per message only. Each attempt runs on a
     model you pick for it; under Local only, only local models.
   - This is an exception to part 2's decision 4 ("always the session's own
     model"), and only when you ask for it.
9. **The branch switch on New session (Phase 9).**
   - Part 2's decision 1 starts a session on a new branch in any git folder.
     That is why the walkthrough's bug fix and UI change took an extra press.
   - *Recommended:* remember the last choice per folder. The first session in
     a folder starts on a branch, as today.
10. **Editors (Phase 10).** *Recommended:* yes, after the benchmark work.
    Add an ACP server so editors that speak the Agent Client Protocol (Zed
    first) can use the IJ harness.
11. **Order.**
    - *Recommended:* as below. Make our own benchmark able to rank (1). Improve
      what it measures (2, 3), which gives a before/after story on our
      subscriptions. Then the public run (4–6), shown (7). Then the features
      that win on daily use (8–10).
    - *Alternative:* the public run first (4–6), for an outside baseline
      before the improvements. That pays for the public set twice.

## Phase 1 — A benchmark that can rank (target v1.383.0)

**What you see.** `bench-harness run --repeats 3` runs every task three
times. The report gives each setup a pass rate with its range, says which
tasks passed every time, some of the time, or never, and names the exact
model and effort that answered. A new `frontier` tier holds tasks big enough
to separate the harnesses.

**How.**
- **Repeats.** `--repeats N` (1 to 10). Each repeat is its own run, with the
  repeat number in its identity, so resume still works. Per setup the report
  gives the mean pass rate, its lowest-to-highest range across repeats, and
  per-task counts ("3/3", "2/3", "0/3"). One-run reports are unchanged.
- **The exact model.**
  - The bench passes the model and effort to every runner explicitly (`-m`
    and the effort flag for the CLIs), so it never runs on "whatever the
    settings say".
  - It also records what each run reports back: Claude Code's `system/init`
    event names its model; Jarvis's route names provider, model and
    reasoning.
  - Each goes into the record, the run's identity and the report. A run that
    cannot say its model says so ("model not reported"), never "default".
- **The `frontier` tier**, about 12 tasks. Each has a hidden check and a
  pinned decoy that passes the visible tests and fails the hidden check, as
  the `hard` tier does:
  - long work across several modules, with its own test suite;
  - a UI change checked in a browser (the hidden check reads the served page
    through a headless browser);
  - a search across a generated repository of about 2,000 files, where
    helpers pay off;
  - setting up a project that will not build (missing dependency, wrong
    version);
  - resolving a git merge conflict without losing either side;
  - debugging a program while it runs;
  - a task whose obvious fix breaks something that only a careful reading of
    the code reveals.
- `core` and `hard` do not change, so every earlier record stays comparable.

**Done when.**
- Every new task fails untouched, passes its reference solution, and its
  decoy fails, all pinned in the suite.
- A `--repeats 3 --tier all` run on Claude Code, Jarvis on Claude and Jarvis
  on Codex completes. Its report shows each setup's range and the exact model,
  and its ledger matches every record.
- On the `frontier` tier, the three setups do not all score the same. If they
  do, the tier is not hard enough and gets harder tasks before the phase
  ships.

**Result (v1.384.0, 2026-10-24).** Built by a doer agent and checked by a
separate reviewer.
- **What shipped:** repeats with ranges; `--model` required; `--effort`
  passed to every runner; the model each run reports, recorded beside the one
  passed; twelve `frontier` tasks with setup steps.
- **Each run works alone:** in its own folder outside the bench root.
- **The checks got harder:** the reviewer's helpers wrote 54 more wrong
  fixes, and 38 of them passed the checks as first shipped. Every one fails
  now. 4 of 19 correct solutions had been wrongly failed; all pass now.
- **Bench code defects found and fixed:** `--model default` was accepted; a
  compiled copy of a hidden check (the whole answer key) was left where a
  later run could read it; two snapshot dates counted as one model; a task id
  could collide with a repeat's folder.
- **Live smoke runs:**
  - the doer's: 2 frontier tasks × 2 repeats on Claude Code (sonnet, low),
    Codex (gpt-6-astra, low) and Jarvis on Claude (sonnet, low), 4 of 4 each;
  - the reviewer's: frontier-debug-price-server × 2 on Claude Code, 2 of 2,
    and every price server it started was gone at the end.

The "Done when" items that need the full measurement (the three setups at
`--repeats 3 --tier all`, and whether the frontier tier separates them) are
run on the released version and recorded below when they finish.

**The measurement (2026-10-25, `--repeats 3 --tier all --seed 0`, effort
medium; read from the bench's own reports).**

| Setup | Model | Passed / scored | Mean | Range | core | hard | frontier |
|---|---|---|---|---|---|---|---|
| Claude Code | sonnet | 123 / 126 | 98% | 95%–100% | 60/60 | 27/30 | 36/36 |
| Jarvis on Claude | sonnet | 115 / 126 | 91% | 90%–93% | 57/60 | 25/30 | 33/36 |
| Jarvis on glm | glm-5.3-flash | 114 / 126 | 90% | 90%–90% | 58/60 | 24/30 | 32/36 |
| Jarvis on Codex | gpt-6-astra | pending | | | | | |

- The frontier tier does separate them: Claude Code 36/36, Jarvis on Claude
  33/36.
- **The Codex row is pending.** The first Codex run (v1.385.0) is not
  counted: all 26 of its failed runs ended with "the folder is read-only",
  which was fixed in v1.385.1, and the ChatGPT account hit its Codex usage
  limit part-way through. It is being run again on v1.385.1.
- **Most of Jarvis on Claude's gap is one defect, found by this run.** The
  claude dialect shows Claude its trained `Bash` tool, but on Windows the
  command ran in cmd.exe. In `process-http-server` the line
  `rm -f server.port; python server.py & …` made Git's `rm` delete
  `server.py`, in 2 of 3 repeats. Heredocs, `;` chains and `/dev/null` failed
  the same way in the shell and background-program tasks (process category:
  4/9 against Claude Code's 9/9). The fix (run `Bash` in Git for Windows'
  bash, as Claude Code does) shipped in v1.386.2: 7 of the 11 failed runs
  came from it, and the four recorded commands replayed through the fix all
  work (`server.py` survives and that task's hidden check passes).

## Phase 2 — The finish check (target v1.384.0)

**What you see.** When a turn changed files and the model is about to say it
is done, one more short step runs first:

- the project's tests run (the folder's checks if it has them, else the test
  command the model used this turn);
- files the turn created that nobody asked for are listed for the model to
  keep or remove;
- the model checks its work against each point of the request.

The reply's work line shows it as "Checked: tests passed, 1 scratch file
removed".

**How.**
- A harness component, `finish`, in the default layer: on in Build sessions
  (decision 6), configurable per folder like the other components, never in
  plain chat.
- It runs only when the turn changed files and ended without an error. At
  most one extra model round. It never runs a command the session's posture
  would not allow: the test command goes through the same `shell` gate, cards
  and git guard as any other.
- New files come from the undo ledger (files the turn created), not from
  guessing names.
- This targets what lost the last two tasks: an edge case its own check never
  tried (`min` with one number), and a scratch test file left behind that
  broke the grader.

**Done when.**
- The two lost runs, replayed from their recorded trajectories, are caught:
  the one-argument `min` by the request check, the scratch file by the
  new-file list.
- `--repeats 3` on all three Jarvis setups: no task passes fewer times than
  in Phase 1's run, and the mean time grows by at most 20%.
- A turn that changed nothing, and every plain chat turn, makes exactly the
  calls it made before (pinned).

**Result (v1.386.1, 2026-10-25; v1.386.0 was not published, its gates
failed on a test).** Built by a doer agent and checked by a
separate reviewer, who found 17 more defects (each red first, fixed and
mutation-checked).
- **What shipped:** the `finish` component as planned (`harness/finish.py`,
  `finish.toml` to turn it off per folder), the "Checked: …" work line and
  the `finish` record on the reply.
- **The two lost runs are caught, replayed from their trajectories through
  the real lane:** the request check put "min and max (one or more
  arguments)" in front of the model on the Claude run, and the new-file list
  named `test_spec.py` (with two other scratch files) on the glm run. Both
  hidden checks pass after the scripted fix; both fail without the check.
- **What the review fixed:** a command the git guard had refused was re-run;
  a docs line could make the check run a reset script; a folder's own check
  ran with no card under yolo; "then fixed" could be claimed from a run whose
  exit status was not the tests'; `pytest --version` read as "tests passed";
  nested `.gitignore` files and helper agents' writes were missed; on Codex
  every finish round started a second process and replayed the whole
  conversation (it now continues the same thread).
- **A reply that changed nothing makes exactly the calls it made before**
  (pinned), and plain chat never runs the check.
- **Not yet measured:** the `--repeats 3` comparison with Phase 1 (no task
  passing fewer times, mean time at most 20% longer) runs on the released
  version and is recorded here when it finishes.

## Phase 3 — Fewer steps, and code awareness (target v1.385.0)

**What you see.** The session starts knowing the shape of the project. After
an edit, real type and lint errors come back, not just "it parses". The same
task takes fewer steps.

**How.**
- **A project map** in the harness block at the start of a session:
  - the folder's tree to a set depth, plus each source file's top-level names
    (classes and functions);
  - kept to a fixed share of the window and counted by the planner;
  - cached until the files change;
  - built off the loop, with the same walk limits as `list_files`;
  - a folder too big for the share says so instead of cutting silently.
- **The project's own checkers after an edit**, decision 7. `harness/verify`
  learns the checkers the project already has (ruff, mypy, eslint, tsc) and
  runs them only when the project has them, only on the changed files, and
  bounded by time. Their findings come back on the edit's "Check:" lines.
- **Reading several files in one call.** `read_file` takes a list of paths,
  and each dialect maps its own multi-read shape onto it.

**Done when.**
- On the same model, tool calls per task are within 20% of the vendor CLI on
  `core` + `hard` (today 6.9 vs 4.8 on Claude, 7.4 vs 3.8 on Codex), with no
  task lost.
- A type error introduced by an edit is reported back to the model in that
  edit's result (pinned with a project that has mypy, and one with tsc).
- A project with no checkers installed behaves exactly as today (pinned).

## Phase 4 — The harness from the command line (target v1.386.0)

**What you see.**

```
ironjarvis code "fix the failing test in tests/test_cart.py" --folder . --model claude-cli:sonnet --json
```

This runs one IJ harness turn, the same turn a Build session runs, and prints
the reply. `--json` gives a record:

- the files changed and the commands run;
- tokens and cost;
- the model that actually answered;
- the outcome.

Exit codes: 0 done, 1 failed, 2 refused by the model, 3 stopped because it
needed an approval (the record lists what). `--session <id>` continues an
earlier run.

**How.**
- The command builds a platform from its own home (decision 1) and runs the
  stream lane exactly as a Build turn does. That means its own `build:` session
  id, the model held, the Build environment for its shell, and the git guard
  and Local only rules, all unchanged.
- No screen means no card. Any call that would show a card follows
  `--approve` (decision 1); the default is to stop with exit code 3.
- The bench's Jarvis runner switches to running this command as a separate
  process, as it already runs `claude -p` and `codex exec`. The benchmark then
  measures the same path a container will use.

**Done when.**
- The same task through `ironjarvis code` and through a Build session on a
  scratch daemon gives the same tools armed, the same prompt (by fingerprint)
  and the same file changes.
- Each exit code is pinned, including 3, which must leave nothing changed by
  the call that needed approval.
- The bench through the new command matches Phase 3's run (no task lost; time
  within 10%).

## Phase 5 — Iron Jarvis on Linux, inside a container (target v1.387.0)

**What you see.** `uv tool install` (or a wheel) puts `ironjarvis` on a Linux
machine, and `ironjarvis code` works there with an API key (or an
OpenAI-compatible endpoint such as the local model).

**How.**
- The command-line path imports nothing Windows-only. Terminal panes, OCR
  and the desktop pieces are left out on Linux, and each feature that is
  missing there says so instead of failing.
- `core/procs`, the shell tool, the process tools and the Build environment
  are driven on Linux. Their POSIX branches exist but have never run.
- A new Ubuntu CI job runs the harness's own tests (the tools, the dialects,
  the lanes, `ironjarvis code`) on Linux, so a Windows-only change cannot
  break it silently.
- A container image (Dockerfile in `packaging/`) for local runs and for Harbor.

**Done when.**
- In a Linux container, `ironjarvis code` passes `core` + `hard` with an API
  key on Claude and on GPT, scored by the same hidden checks.
- The Ubuntu CI job is green and gates the release like the Windows jobs.
- Nothing in the Windows app changes (the Windows suites are unchanged).

## Phase 6 — Terminal-Bench through Harbor (target v1.388.0)

**What you see.** `harbor run` with `-a iron_jarvis_harbor:IronJarvisAgent`
runs the IJ harness on Terminal-Bench. Its results sit beside Claude Code's
and Codex's from the same machine, the same tasks and the same models.

**How.**
- `iron_jarvis_harbor/agent.py`, a `BaseInstalledAgent`:
  - `install` puts Phase 5's package in the task container;
  - `run` calls `ironjarvis code "<instruction>" --approve all --json`;
  - the JSON record fills Harbor's token and cost fields.
- Run plan (decisions 2–4):
  1. A 5-task smoke run on all four rows (IJ+Claude, Claude Code, IJ+GPT,
     Codex) to check the setup and measure the cost per task.
  2. Then the full set, 3 times per task, up to the cap you set.
- GPU tasks are listed as not run (decision 4).
- Results are kept with the bench's other records, and the report says which
  version of Iron Jarvis, the dataset and Harbor were used.
- SWE-bench Pro follows if Harbor has a dataset for it; otherwise a later
  phase.

**Done when.**
- The smoke run completes on all four rows on one of your machines, and its
  cost per task is written down.
- The full run completes inside the cap, with a pass rate and range per row,
  each run linked to its trajectory.
- What happened is written into this file, whatever the result. If the IJ
  harness is behind, the gaps found become the next phase, not a footnote.

## Phase 7 — Showing it (target v1.389.0)

**What you see.**
- **A Benchmarks page** (Everything › Benchmarks). One table per benchmark
  (ours, Terminal-Bench): setup, pass rate with its range, mean time, tokens,
  cost, exact model, date and version. Each task opens to its trajectory and
  the hidden check's one-line verdict, never the check itself. The page only
  shows results; runs start from the command line, because a run spends money.
- **A Harbor Hub leaderboard,** private until you review it (decision 5),
  linking every run.
- **A table in the README and the Handbook,** with the date, versions,
  models and number of runs, worded by the numbers.

**How.**
- One read of the bench's records (results.json, the ledger, the
  trajectories) and of Harbor's job folders. No new numbers are computed
  anywhere but the bench's own report code, so the page, the board and the
  README cannot disagree.
- The hidden checks stay hidden: the tripwire that keeps them out of the
  agent's folder also keeps them off the page.

**Done when.**
- The page shows Phase 1's and Phase 6's results, and every number on it
  matches the bench's report (pinned against a fixture made from a real run).
- The board's rows match the same records.
- The README's claim follows the decision 5 wording rule (pinned).

## Phase 8 — Try it on several models at once (target v1.390.0)

**What you see.** "Try this with Claude, Codex and the local model." The
session starts three attempts, each on its own branch and its own model,
shown as three rows. When they finish, the finish check's results are side by
side: tests, files changed, time and cost. You keep one, and the others go to
the trash with their branches kept. No other harness can do this: Codex's
cloud runs several attempts on ONE model.

**How.**
- Built on part 2's pieces:
  - each attempt is a helper with its own worktree and branch;
  - its model is the one you named, held for that attempt;
  - Local only allows only local models;
  - Phase 2's finish check runs in each attempt.
- Opt-in only (decision 8): the words of the message or a "Try on…" control.
  A session never starts attempts on its own.
- A sixth benchmark row, "best of three", on our tier and, when the cap
  allows, on Terminal-Bench.

**Done when.**
- On a scratch daemon, three attempts on three models finish on three
  branches, the main checkout is untouched, and keeping one merges only that
  one.
- Stop ends all three (checked by process id).
- On the `frontier` tier, the best-of-three row passes at least as many tasks
  as the best single model.

## Phase 9 — Fewer presses (target v1.391.0)

**What you see.** The walkthrough's remaining gaps
(`docs/IJ-HARNESS-WALKTHROUGH.md`, "Next gaps"), closed:

- "N files changed" opens a diff pane with next and previous, not a dialog
  per file;
- the reply shows the last test run's result line, and one press opens its
  output (without the sandbox warning in front);
- Create PR is one press using the drafted title (edit it after);
- Browser opens on the live page;
- New session remembers the branch choice per folder (decision 9).

**Done when.** The walkthrough is driven again with the same counting rules.
The bug fix and the UI change need no more presses than Claude Code needs in
the same approval mode (counted from its documentation, as in the
walkthrough), and the branch task no more than today's 11.

## Phase 10 — Editors (target v1.392.0)

**What you see.** In an editor that speaks the Agent Client Protocol (Zed
first), the IJ harness is one of the agents to pick. It runs on the IJ
harness, with the same model menu, approvals and Build session record.

**How.**
- `ironjarvis acp` speaks ACP over stdio. Each ACP session is a Build session
  (Phase 4's path):
  - approvals become the editor's permission prompts;
  - file edits come back as the editor's diffs.
- Listed in the ACP registry, which Harbor also reads, so it gives a second
  way into the benchmark.

**Done when.** A real Zed session sends a task, sees the steps, answers an
approval, and the edit lands in the file, recorded in the app's Build view.

## What this plan does not change

- The Terminals view, the chat, and every non-harness surface stay as they
  are.
- Nothing moves to another provider or account on its own. Phase 8's attempts
  run only on models you named for them.
- Local only, low trust, the git guard and the command-file rule apply to
  headless runs, containers and attempts exactly as to a Build session.
- Your Claude and ChatGPT sign-ins never go into a container.
- Nothing is published (a leaderboard, the README claim) without your press.
- No run spends money from a button in the app; paid runs start from the
  command line, under a cap you set.
