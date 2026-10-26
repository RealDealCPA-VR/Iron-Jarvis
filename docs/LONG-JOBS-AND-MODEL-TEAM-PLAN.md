# Long jobs that finish, and a team of models

Written 2026-10-25 against v1.389.0, for the user to review **before anything
is built**. It answers two requests:

1. "What else would make this fully capable of running long jobs to
   completion?"
2. "One master planner model that delegates to the models I have, keeps tabs
   on them, and makes sure I'm not running into usage limits on a cloud
   subscription. Local models preferred."

It also adds the user's **Timeline** idea (2026-10-25): a waterfall beside a
coding session showing every worker, model and tool call on one time axis,
with time, tokens and cost.

Every phase is built the way the IJ harness plans were:
- A doer agent builds it, then a separate reviewer agent tries to break it.
- Each fix is pinned by a test that fails on the old code.
- Each phase gets a live run on a scratch daemon, never your app.
- Each phase is one release.

## Where it stands (v1.389.0, read from the code)

**Long jobs**

| | Today |
|---|---|
| Step limit | None for a coding reply since v1.385.0 (a safety stop at 1,000 rounds). |
| Leaving the page | The reply keeps running; coming back picks up where the page was. |
| PC goes to sleep | **Nothing keeps it awake.** A sleeping PC stops the reply mid-step. |
| Restart or crash | The reply is cut off. Its text is kept, and v1.389.0 offers **Retry**, but Retry starts the question **again from the beginning**. The model's own record of the reply (its tool calls and their results) is not saved, so it cannot pick up where it stopped. Agent runs can (v1.337.0); coding sessions cannot. |
| A step that hangs | Bounded per call: about 3 min of silence for Claude Code, 5 min for Codex, 10 min per tool. **No watch over the reply as a whole.** |
| Very long replies | The oldest rounds are dropped silently to fit the model, with a short typed recap. No summary written by the model mid-reply, unlike agent runs. |
| Waiting on you | A coding session's own question **never reaches your phone**. A helper agent's question does. |
| Account limits | The limit card's **Resume when ready** needs one press. Moving to another account needs one press. |

**A team of models**

| | Today |
|---|---|
| Delegating | A mission's planner can hand work to your custom agents. A custom agent pinned to a model runs on that model, so a planner talking to Claude can already hand a piece to an agent pinned to glm. |
| What the planner sees | A list of agents by name and description. **No model, no place (this PC / your network / cloud), no cost type, no usage.** |
| Choosing a model | The planner picks an **agent**, never a model. `spawn_agent` ignores an agent's model pin (`delegate` honours it), which is inconsistent. |
| Coding sessions | Helpers always run on the session's own model, by your decision for part 2 (decision 4). A helper pinned to another model is refused. |
| Usage limits | Iron-Proxy knows each account's usage (percent, window, reset time), but **only after a call returns it**. Nothing reads it before handing out work. |
| Local first | Auto routing has a local ladder that prefers a local model when its quality record is good enough. But the record only counts "the run finished", not "the work was right". It covers agent runs only. And any turn with tools goes to the heaviest tier, so in coding work the ladder never fires. |
| Seeing it | Helper rows show no model or cost (they were all the same model). There is no view across workers. |

## Decisions for you before building

These change what gets built. Each has a recommendation; nothing starts until
you answer.

**Decided 2026-10-25: the user accepted all ten recommendations** ("I accept
all ten recommended decisions. Start Phase 1 with doer and reviewer agents,
and ship each phase when it's green."). The phases are built and shipped in
order.

1. **Keep the PC awake while work runs (Phase 2).**
   - *Recommended:* yes. Only while a coding reply, a mission or an agent run
     is working, released the moment it stops.
   - The screen can still turn off; only sleep is held.
   - A switch in Settings turns it off.
2. **After a restart or crash, resume a running reply by itself (Phase 2).**
   - *Recommended:* yes, for a reply that was **working** when the app
     stopped. It continues from its last finished step, and one line under it
     says so.
   - A command that was cut off mid-run is not run again blindly. The model is
     told it was cut off, as agent runs already do.
   - A reply that was **waiting on you** stays waiting.
   - *Alternative:* offer **Continue** with one press instead.
3. **A question while you're away (Phase 3).**
   - *Recommended:* if a phone channel is set up, a coding session's question
     goes to your phone after 5 minutes unanswered in the app. An answer there
     counts like the card.
   - The safety cards stay **app-only**: git commit and push, a project's own
     command lines, and a page click that pays or sends. Those are the ones
     where seeing the whole card matters.
4. **Account limits without a press (Phase 4).**
   - *Recommended:* at a limit, the reply waits for the reset and carries on
     by itself (today's **Resume when ready**, on by default).
   - Moving to another account by itself only **within an account group you
     make** (for example "work"). Work and personal accounts never mix. Off
     until you make a group.
5. **Helpers on other models (Phase 5).** This changes your part 2
   decision 4 ("helpers always on the session's own model").
   - *Recommended:* a helper may run on any model **on your team list**
     (decision 6). It is held to that model, with no silent switching.
   - The model you talk to stays the one you picked.
   - Under **Local only**, only models on this PC or your network.
6. **Who is on the team by default (Phase 5).**
   - *Recommended:* one press offers a starter team built from what is
     connected:
     - your local models first (free): reading, summarizing and simple coding;
     - Claude Code and Codex accounts only if you add them;
     - pay-per-use APIs off.
   - Each member is a custom agent pinned to a model, so the Agents page, its
     monthly allowance and its notes all apply. Nothing new to learn.
7. **Who picks the model for each piece (Phase 6).**
   - *Recommended:* the planner names a **role** ("read these files", "write
     this module", "review this change"). Iron Jarvis picks the team member
     for that role.
   - It tries a local model first when that model's record for the role is
     good. "Good" is measured by the finish check and the reviewer, not just
     "it finished". It moves up only when local is busy, missing or not good
     enough.
   - The planner can still name a member directly.
8. **Usage limits (Phase 6).**
   - *Recommended:*
     - Stop giving **new** work to a subscription account at **80%** of any
       usage window. Work already running finishes.
     - Never start new work on an account at 95%.
     - When every cloud account is near its limit, work goes to local models
       or waits for the earliest reset, and the Timeline says which.
     - Pay-per-use only with a monthly cap you set (default $0).
     - No time or cost cap on your own coding replies by default (you asked
       for long jobs to finish). An optional per-session cap is there if you
       want one.
9. **Where you see it (Phase 1).**
   - *Recommended:* a **Timeline** tab beside Plan in a coding session, live
     while it works and kept with each reply.
   - The same picture later on the mission screen and an agent run's page.
10. **Order.**
    - *Recommended:* this plan next, Phases 1–4 (long jobs) before 5–7 (the
      team). Then the rest of part 3 (the harness from the command line,
      Linux, Terminal-Bench, showing it, editors).
    - Part 3's Phase 8 ("try a task on several models at once") folds into
      Phase 5 here, since it needs the same per-helper model hold.

## Phase 1 — The Timeline (target v1.390.0)

**What you see.** A **Timeline** tab beside Plan / Terminal / Changes /
Browser / Files.
- **At the top:** time, cost, tokens, model calls, tool calls and errors for
  the reply.
- **Rows:** the model you talk to, each helper with its model, and every tool
  call nested under the worker that made it.
- **Bars** on one time axis, each ending in its time and cost.
- **Press a bar** for a card: status, when it started, how long it ran,
  tokens in and out, cost, and what went in and what came out (masked, cut
  like a step's details).
- It works with one model today and answers "where did the 117 minutes and
  $18 go". With the team (Phase 5) the rows show different models.

**How.**
- The daemon records what it does not record yet:
  - when each step **started** (today only its length);
  - each model round's start, end, tokens and cost. Usage is totalled per
    reply today; the router already knows each call's numbers.
- They ride the turn's frames while it runs and the reply line after it ends.
  They are capped like steps: the first 60 with detail, the rest compact, at
  most 2,000.
- Helpers already keep their start, end, model, tokens and cost.
- The words are the run log's ("Read app.py", "Ran the tests"), never
  "trace" or "span". Subscription money is marked "list price" as everywhere
  else.

**Done when.**
- A scratch run with two helpers and twenty tool calls draws bars whose times
  add up to the reply's measured time (within one second) and whose costs add
  up to the reply's receipt.
- A reload draws the same picture from the saved line.
- An older reply with no timings shows "No timeline for this reply".

## Phase 2 — Survives sleep and restarts (target v1.391.0)

**What you see.**
- The PC does not sleep while a coding reply, mission or agent run works
  (decision 1).
- After an update, a crash or a Windows restart, a reply that was working
  carries on from its last finished step by itself, with one line saying it
  was resumed (decision 2).

**How.**
- **Keeping awake.** The desktop app holds Electron's sleep blocker
  (`prevent-app-suspension`) while the daemon reports running work. It reads
  v1.389.0's running-reply count and the run count, and releases the blocker
  the moment both are zero.
- **Saving the reply.** A coding reply saves the model's own record of the
  reply after every round: its tool calls and their results. This is the
  `agents/transcript` store agent runs use, with the same cap and image
  markers. A call that was cut off gets the reconcile's "cut off" note, never
  an unanswered call.
- **Resuming.** At boot, each reply the reconcile marks interrupted while
  working starts again from that record. It uses the same account pin and
  model hold, and the same folder and branch.

**Done when.**
- The update rehearsal (v1.338.0) gains a coding reply. Stopped with the
  app's update stop, and again with a hard kill, the reply resumes at its
  last round, re-runs no finished step (checked against the tool log), and
  ends with the same files a reply that never stopped would.
- The sleep blocker is held only while work runs (checked through the
  desktop's own functions under node).

## Phase 3 — Never stalls silently (target v1.392.0)

**What you see.**
- A reply with no progress for 10 minutes says so on its line and in the bell:
  "No progress for 10 minutes — waiting on <the step>". It offers **Stop the
  step** (the reply carries on without it) and **Stop**.
- A question waits for you on your phone too (decision 3).
- A very long reply keeps its memory: past about 90% of the model's window it
  writes itself a summary, the way agent runs do, instead of silently
  dropping its oldest rounds.
- An optional cap per session for time or list-price cost (decision 8, off by
  default).

**How.**
- **The watch.** It reads the turn's own frames: no new frame and no tool
  ending in the window means a stall. A long command that keeps printing is
  not a stall.
- **The phone.** A coding session's card publishes the ask event agent runs
  already publish, so the phone channel's existing answer path works. Safety
  cards are marked app-only.
- **The summary.** It reuses `AgentRuntime._maybe_compact` and its ledger
  check, so a summary that claims a file the record does not show is cut.

**Done when.**
- A scripted step that hangs produces the notice at the window, and **Stop
  the step** ends only that call (checked by process id).
- A card answered on a fake phone channel resumes the reply.
- A 300-round scripted reply keeps every file path it touched in its summary.

## Phase 4 — Limits without a press (target v1.393.0)

**What you see.**
- At an account limit, the reply waits for the reset and carries on by itself
  (decision 4).
- With an account group, it moves to the next account in the group and
  carries on, and the line names both accounts.
- Each account's usage shows on the Timeline and in the model menu, as today
  plus the window it is in.

**How.**
- Today's Resume-when-ready loop starts the wait on its own.
- Moving within a group uses v1.303.0's carry (the conversation is copied,
  never moved) and the pinned lease. Iron-Proxy still picks no account you
  did not group.
- Usage is read from the cached account snapshot, never a new vendor call.

**Done when.**
- On the fake Iron-Proxy, a scripted limit resumes at the reset with no
  press.
- A grouped account takes over.
- An ungrouped work account never hands a conversation to a personal one.

## Phase 5 — Your model team (target v1.394.0)

**What you see.**
- **Settings › Model team** lists the members: name, model, where it runs
  (this PC / your network / cloud), cost type (free / your plan / pay per
  use), roles, and current usage.
- **Make a starter team** builds one from what is connected (decision 6).
- In a coding session or a mission, the planner can hand pieces to members;
  the Timeline shows each piece on its own model.
- **Try on several models** (part 3's Phase 8) runs one task as attempts on
  members you name, each on its own branch, side by side.

**How.**
- **Members** are custom agents pinned to a model, with a new `roles` field.
  Allowances still apply.
- **The planner's list of who can take the work** gains each member's model,
  place, cost type, roles and usage. It is the same list, with more facts.
- **`delegate` and `spawn_agent`** honour a member's model the same way (the
  inconsistency above is fixed). In a coding session a helper is held to its
  **member's** model, not the session's (decision 5).
- **Local only** allows only local members, and cloud members are never shown
  to the planner there.

**Done when.**
- On a scratch daemon a coding session on Claude hands a reading task to a
  local member and a coding task to a Codex member.
- The Timeline shows three models, and each helper's model calls went only to
  its own provider (checked against the router's record).
- Under Local only the planner never sees a cloud member.

## Phase 6 — Local first, limits watched (target v1.395.0)

**What you see.**
- The planner asks for roles. Iron Jarvis picks the member: local first when
  its record for that role is good, cloud next, pay-per-use only under your
  cap (decisions 7 and 8).
- The Timeline says why each pick was made ("local: good record for reading",
  "Codex: local busy", "skipped Claude TSI: 83% of its 5-hour window").
- A piece that fails its check twice goes to the next member up, with the
  reason.

**How.**
- A **team router** sits in front of `delegate`:
  - role → candidates;
  - Local only and the allow switches first;
  - usage thresholds from the cached snapshot;
  - each member's record for the role;
  - the fleet's live load (a busy local box counts as busy);
  - one sentence saying why.
- **The record** counts per (member, role): passed the finish check, the
  reviewer's verdict, and finished. The completion-only local oracle is
  replaced for these roles, not for chat's Auto.

**Done when.**
- A scripted day that pushes one account past 80% sends the next pieces to
  local, then to the other account. Nothing new starts on the busy account,
  and the work in flight on it finishes.
- A local member that fails a role twice stops getting it first until it
  passes again.

## Phase 7 — Measured (target v1.396.0)

**What you see.** A benchmark row for the team next to the single-model rows
on the `frontier` tier.

**How.** The bench runs a coding session with a team: Claude as the planner,
the local model and Codex as members. It records:
- the pass rate (3 repeats);
- how much of each subscription window was used;
- the time.

**Done when.** The table answers: does the team pass as many tasks as Claude
alone while using less of the Claude window? The defaults (decision 7's
"good record", decision 8's 80%) are tuned from the numbers, and the README
says only what the numbers show.

## What this plan does not change

- The model you talk to is always the one you picked. Nothing moves your
  conversation to another provider or account outside a group you made.
- Local only stays absolute: no team member, resume or summary sends anything
  off your network.
- Every card that asks today still asks. Nothing in this plan grants a tool.
- The safety floors (git guard, project commands, payments) stay app-only and
  once-only.
- Nothing spends pay-per-use money above the cap you set (default $0).

## Honest limits

- Usage percentages come from the vendors **after** a call. The first call on
  an account after a quiet stretch starts without a fresh number; Codex's is
  read when a session starts.
- Holding sleep does not stop Windows Update from restarting the PC; Phase 2's
  resume covers that.
- The finish check and the reviewer are the best signals we have, not proof
  that work is right.
- A model cannot be swapped in the middle of a sentence; switches happen
  between pieces of work.
