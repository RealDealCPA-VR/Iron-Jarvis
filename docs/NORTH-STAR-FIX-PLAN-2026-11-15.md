# North-star fix plan

Written 2026-11-15 at v1.500.2, from a six-part read-only review of the code
against the north star in `CLAUDE.md`. About 70 findings. Each was read from
code; none was reproduced, so **every fix starts with a test that goes red**.
A finding that does not go red is struck from this plan with the reason, not
"fixed".

Status marks: `[ ]` open, `[x]` shipped (with the version), `[-]` struck (with
the reason).

## How the fixes are made

**1. Fix the cause once, not seventy places.** Most findings share nine causes.
Each gets one shared piece of code, and every site calls it:

| Cause | The one piece | Closes |
|---|---|---|
| Recall that skips the client wall | every recalled store passes through `workmap.clients.wall` | 1.1-1.4 |
| A model's guess kept as the owner's word | one "proposed, with evidence" mint path; only the owner's own words mint "confirmed" | 1.5-1.8 |
| Background jobs that forget Local only | the job runs inside the source run's `local_only_scope` | 2.3, 2.6 |
| Outside text trusted as instructions | `returns_untrusted_content` on every tool that returns file, web or signal text; one taint deny list | 2.1, 2.4, 2.7, 2.8 |
| "Done" read from the run's status | one helper that reads `Session.outcome` (the ledger verdict) and gives status, words and ok | 3.1-3.6, 3.11 |
| A claim nobody checked | `change_claims` judges by name against recorded paths and sends | 3.7-3.10 |
| Undo written after the change | the journal row is written with the intent, before the tool runs; one atomic-write helper | 4.1, 4.4 |
| Pages chat cannot see | one daemon-side page table that feeds the lookup, the doors and the Home map | 6.1-6.6, 8.2 |
| Engine text on screen | one `plain_failure(what, exc)` for every broad `except` in a route | 8.1 |

**2. A scan test keeps each cause closed.** Where a class of mistake can be
found by reading source (a route that deletes without the journal, a write tool
without a before-copy, a page with no lookup line, a raw `subprocess`), the
group adds a scan test with an explicit allow list. The test is the rule.

**3. Order follows the north star.** Data crossing clients or leaving the PC
first (principles 1 and 2), then false "done", then loss, then stalls, then
reach, learning and wording.

**4. Each group is one release** (rules in `CLAUDE.md`, "Release flow"): the
touched test files locally (at most 4 workers in total), the full suite on
GitHub on the branch, one bump, straight to master. A group that changes what
the owner sees adds its Handbook section and the changelog. Notes for agents go
in the area file under `docs/rules/`.

**5. Who does what.** Implementation: Opus 5.5 at high effort, one agent per
set of files, never two agents in one file. Review and correction: the lead
session reads every diff against this plan, mutates the pinned code to see each
new test go red, and corrects before the commit.

## Decisions taken as recommended

Each changes behaviour the owner will notice. Say so to change any of them.

| # | Decision | Taken |
|---|---|---|
| D1 | A picture on a local or picked text-only model | Never moved to a provider that leaves this network. Offline reading of the picture first; else refused by name with a "send once" offer |
| D2 | A preference an agent run inferred | Waits in Improvements as a suggestion with the run as evidence. Only a chat turn where the owner said it keeps it at once |
| D3 | Lessons a model condensed overnight | Wait in Improvements; the originals are kept until answered |
| D4 | A run that has read flagged content | Loses `web_fetch`, `web_search`, `browse` and `web_action` for the rest of the run |
| D5 | `read_file`, `grep`, `shell` output | Treated as outside text (fenced and scanned). A coding session's own repository files are not flagged |
| D6 | An agent making an outbound webhook | Asks, as chat already does |
| D7 | "Clear completed" | Moves the runs' folders to the app's trash and says so; records are kept 30 days in the trash before removal |
| D8 | 90-day clean-up | Still 90 days by default. The setting says exactly what goes, an aged run says its details were removed, and a run's final answer is kept |
| D9 | Dictation with an OpenAI key present | The built-in offline model unless the owner picks OpenAI for voice |
| D10 | A schedule or workflow that needs an OK | "Always for this schedule" is offered; the grant is kept on the schedule and shown on its row with a Remove press |
| D11 | The hotkey quick window | Goes through chat (the same tools, cards and doors), and hands off to a run only when chat does |
| D12 | A queued job, workflow step or night task that ended short | Shown as "Needs you" or "Part failed", never "Done" |

---

## Group 1. The client wall and learning consent

Area rules: `docs/rules/memory.md`, `docs/rules/agents.md`.

**Shipped in v1.501.0.** As built, where it differs from the lines below:
1.1 a reflection carries no folder, so reflections are left out of recall and
the other lessons are walled by the run or chat their evidence links. 1.2 the
same telling also covers `memory_search`, `memory_read` and a write over
another client's entry. 1.3 a `replace` keeps the notes the run was not shown
(the lead's correction: without it, a replace deleted another client's
notes). 1.4 is a context value set by the run, not an option copied to the
child. 1.7 the record is an event in Activity; the Memory page does not show
it yet. 1.8 "kept" means every confirmed preference, a suggestion the owner
kept included; lessons the owner did not state are still capped at eight.
Left open: a preference an agent saved before v1.501.0 cannot be told apart
from a stated one; outcome credit is forgotten on restart.

- [x] **1.1 Lessons pass the wall.** `memory/fabric.py:667` `_lessons` reads
  every lesson. Fix: leave out `reflection` rows (as the prompt already does)
  and pass the rest through `_walled` by the lesson's source run. Same in
  `learning/tools.py` `recall_lessons`. Test: a reflection from a run in client
  A's folder is not recalled into a turn in client B's folder.
- [x] **1.2 `memory_write` keeps its scope.** `memory/tools.py:44` drops
  `scope_id`. Fix: stamp the writing session (and its folder) on the record;
  `fabric._memory` filters by the turn's scope and passes hits through the
  wall. A row written before this change has no origin and is told as before.
  Tests: two runs writing `("session","plan")` do not overwrite each other; a
  `project` entry written in A's folder is not recalled in B's.
- [x] **1.3 An agent's notebook is per folder.** `agents/files.py:362`
  `NOTES.md` is per agent. Fix: notes written during work in a client's folder
  are told only in that client's folders (the wall decides). Test as 1.1.
- [x] **1.4 "Skip memory" reaches helpers.** `agents/delegate_tool.py:726`
  forwards options only to a coding helper. Fix: a child inherits
  `skip_memory` (and Local only where the context does not already carry it).
  Test: a child of a skip-memory run gets no lessons, index or recall.
- [x] **1.5 A guessed preference is a suggestion (D2).** `agents/types.py:184`
  tells every agent to call `remember_preference` when it "notices";
  `learning/engine.py:140` keeps it confirmed, weight 5, origin "said". Fix:
  outside a chat turn whose own user message carried it, mint
  `status=proposed, origin="noticed"` with the run as evidence, shown in
  Improvements. Reword the tool and the agent line. Test: an agent run's call
  leaves a proposed row that reaches no prompt; chat's path is unchanged.
- [x] **1.6 Condensed lessons are suggestions (D3).** `learning/engine.py:375`
  writes `source="distilled"` rows that default to confirmed and deletes the
  originals. Fix: mint proposed with the source runs as evidence; keep the
  originals until the owner answers. Update
  `tests/test_memory_knows_you_v1279.py:69`, which pins the old behaviour.
- [x] **1.7 Lesson tidy-up leaves a record.** `daemon/app.py:1421` dedup and
  distill delete rows with only a log line. Fix: one event per pass naming
  what was merged or removed, shown on the Memory page.
- [x] **1.8 Every kept preference reaches the prompt.** `learning/engine.py:224`
  takes eight, ranked partly by a bonus from unrelated runs
  (`improvement/engine.py:233, 367`). Fix: all confirmed `origin="said"`
  preferences are included, budgeted by characters; the bonus applies only to
  rows the owner did not state, and only from runs they were injected into.
  Test: a ninth stated preference with a negative bonus still reaches the
  prompt.
- [x] **1.9 Low-trust work writes no lesson.** `agents/orchestrator.py:2109`
  reflects on every finished run. Fix: skip `reflect()` for a low-trust run
  (a phone or other-computer task), as the coding lessons already do.
- [x] **1.10 `skill_create` never replaces a skill silently.**
  `skills/loader.py:131`. Fix: refuse an existing name and point to
  `skill_update` (which keeps the old version). Test: a second create with the
  same name leaves the first file unchanged.
- [x] **1.11 `memory_write` over an existing key can be undone.**
  `memory/layers.py:63`. Fix: journal the earlier text as the inverse.

## Group 2. Nothing leaves the PC unasked

Area rules: `docs/rules/tools.md`, `providers.md`, `connections.md`,
`messaging.md`, `automations.md`.

**Shipped in v1.502.0.** As built, where it differs from the lines below:
2.1 the web tools are kept from a run only after it has READ flagged content
(a separate list, `TAINTED_DENY`); a phone chat, low only because of its
door, keeps web search. The browser add-on's page actions are not listed
(each already asks). 2.3 the autonomy finding did not reproduce (its events
carry no payload) and is struck; a skill drafted from a Local only run is
also never approved automatically. 2.4 a command's output in a session's own
Git repository counts as its own source (the lead's correction: otherwise a
project's own test output could taint a coding session, which then loses the
shell). 2.6 one Local only chat no longer marks every later chat (the lead's
correction: all chats share one session id). 2.8 tool arguments and notify
messages keep the signal's words; only the agent step's task is fenced. 2.10
a description that changed but is not flagged is not quarantined. 2.11
`secret_set` stays registered but takes no value. 2.13 "picked" means any
provider the caller named, so a named cloud model that cannot see is refused
with "send once" too. 2.16 only a PAIRED computer's reply is pinned.
Left open: an unpaired, hand-made task door still answers at the address the
task named (pinning it needs the sender's address stored at intake);
`workflow.completed`, `build.*` and `skill.proposal_created` events carry no
Local only mark when published outside a scope; one-shot callers outside
`daemon/app.py` do not yet pass `named=`; a signal-fired workflow's TOOL
steps still run at full trust.

- [x] **2.1 A tainted run cannot fetch (D4).** `core/trust.py` `LOW_TRUST_DENY`
  lacks the web tools. Fix: add `web_fetch`, `web_search`, `browse`,
  `web_action`. Test through the registry (the layer that refuses).
- [x] **2.2 App packs get a clean environment.** `mcp/tools.py:483` hands a
  pack `os.environ`, the install token included. Fix: build the pack's
  environment the way `sandbox/native.py` and `terminals/manager.py` do, then
  add the pack's own `env` and `env_secrets`. Test: `IRONJARVIS_TOKEN` and a
  provider key are absent from a pack's environment.
- [x] **2.3 Background learning honours Local only.** `daemon/app.py:3312`
  (skill learning), `coach/engine.py:320`, `motivation/engine.py:550`. Fix:
  a Local only run's material is used only inside `local_only_scope(True)`,
  so the existing gate refuses a model that leaves the network.
- [x] **2.4 File and command output is outside text (D5).**
  `tools/builtins.py:694` `read_file`, plus `grep` and `shell`. Fix: set
  `returns_untrusted_content` (not for a coding session's own repository
  files). Update `tests/test_build_stop_trust_v14230.py:165`.
- [x] **2.5 "Open in its app" opens documents only.**
  `daemon/routes/documents.py:95, 644`. Fix: folders and a list of document
  and media types open; anything else answers "show in folder". Test: `.bat`,
  `.hta`, `.exe`, `.lnk`, `.js`, `.ps1` are refused.
- [x] **2.6 Alerts and webhooks honour Local only.** `comm/notifier.py:301`,
  `webhooks/outbound.py:380`. Fix: an event published under the scope is
  stamped; the notifier sends the title-only line and the webhook sender
  skips it.
- [x] **2.7 An agent asks before making an outbound webhook (D6).**
  `core/config.py:243`. Fix: `webhook_add` with `direction="outbound"` asks.
- [x] **2.8 A signal's text is fenced, and its run starts at low trust.**
  `reflex/router.py:288, 358`. Fix: fence the `Trigger` output in
  `workflows/engine.render_template`; a rule fired by a webhook, email, Slack
  or calendar signal starts at low trust (the `comm_trust` rule).
- [x] **2.9 The app-tools switch never lifts a locked app.**
  `platform.py:1692`. Fix: the resolver answers False for a tool whose pack is
  locked.
- [x] **2.10 A pack's tool descriptions are capped and scanned.**
  `mcp/tools.py:292`. Fix: cap and scan at registration; a flagged or changed
  description is quarantined as a changed tool already is.
- [x] **2.11 A secret is never a tool argument.** `secrets/tools.py:53`. Fix:
  agent runs get the name-only tool that raises the secure card, as chat does.
- [x] **2.12 Dictation stays on the PC unless picked (D9).**
  `daemon/app.py:2729`, `daemon/routes/voice.py:40`. Fix: the offline model
  unless a voice backend is chosen; the route takes the chat's Local only flag.
- [x] **2.13 A picture never moves the turn off this network (D1).**
  `providers/router.py:1926, 2187` and the `stream()` mirror. Fix: when the
  primary is local or explicitly picked, no swap to a provider that leaves the
  network: offline OCR first, else a refusal by name with "send once".
- [x] **2.14 One-shot helpers honour Strict model pin.** `daemon/app.py:3136`.
  Fix: no failover when the caller named a provider and the pin is on; publish
  the failover event when a move does happen.
- [x] **2.15 `serve --host` on the network needs a token.**
  `daemon/auth.py:646`, `daemon/cli.py:404`. Fix: refuse to start on a
  non-loopback host with no token.
- [x] **2.16 A task's reply goes only to the paired address.**
  `agents/task_door.py:667`. Fix: pin `reply_to` to the paired computer's
  stored address; the pairing and network cards say the link is not encrypted.

## Group 3. Done means done

Area rules: `docs/rules/agents.md`, `automations.md`, `chat.md`, `build.md`.

**Shipped in v1.503.0.** As built, where it differs from the lines below:
3.3 a step never attempted (or none passing) ends the run Failed; some passed
and some failed stays Completed with the verdict "part failed". 3.4 a job
that fell short keeps status `done` with its verdict and reason; its failure
streak is neither reset nor raised. 3.6 the bell and the tray say the same
sentence as the phone (the lead's addition). 3.9 where a command, code, an
app tool or a user-made tool ran there is no record to compare against, so
nothing is judged there, as before. 3.10 a hand-off still stops the judging
of settings and records; a one-word setting label is not judged after other
tools; the send check reads English only; the run-summary check is wired
into both settle sites (the lead's addition). 3.11 is a new stored verdict,
`did_nothing`, for a schedule, a queued job, a goal, a rule and autonomy
only: a phone task and a task from another computer are left out (the lead's
correction: their answer goes straight back to whoever asked). 3.12 a card
that timed out is "paused"; it still ends the check-again loop.
Left open: a coding helper whose plan step failed but whose last change
landed is still eased to "completed" (the v1.465.0 helper rule); a
`notify` that reached one channel of several counts as sent for any claim;
`harness/finish._REFUSAL_PREFIXES` does not list "paused" (same result today).
To watch: `tests/test_mcp_direct_launch_refresh_v14150.py::
test_a_running_direct_server_keeps_its_files_and_answers_through_a_daily_check`
needed one retry on the group's second GitHub run (the log keeps no trace of
the first try). It passed first time when that shard was run again, in six
local runs, and in the six release runs before it. Group 3 did not touch
that code; if it needs a retry again, group 2's change to how an app pack is
started (2.2) is the first suspect.

- [x] **3.1 One verdict helper.** New: one function that turns a finished
  session into `(status, outcome, ok, words)` from `Session.outcome`. Every
  item below calls it.
- [x] **3.2 A workflow's agent step reads the verdict.**
  `workflows/engine.py:1139`. `needs_you` and `completed_with_failures` are a
  failed step (so `on_failure` routes it), and the run and its Schedules row
  say so.
- [x] **3.3 A planned run with failed or unattempted steps is not Completed.**
  `agents/runtime.py:3311`, `agents/decompose.py:1475`. `run_decomposed`
  returns its step results; the run ends Failed or part failed. Update the
  v1.132 test that pins Completed.
- [x] **3.4 A queued job shows how it really ended (D12).**
  `agents/orchestrator.py:2190`, `assignments/store.py:510`. The row carries
  the outcome; the failure count is not reset on a shortfall.
- [x] **3.5 A lead gets the checked result.** `agents/delegate_tool.py:958`.
  The tool result carries the settled summary and `outcome`; `needs_you` and
  part-failed are not `ok`.
- [x] **3.6 A night task with failing tests is not "done".**
  `harness/night.py:1050`, `daemon/routes/night.py:715`. Failed tests or unmet
  points make it "Needs you"; the morning notice counts it there.
- [x] **3.7 The false-save check counts only writes that landed.**
  `agents/outcome.py:429`, `agents/orchestrator.py:1958`. Build the list from
  `ok` rows of this run. Test: a declined `write_file` followed by "I saved
  report.docx" is marked.
- [x] **3.8 "No tool ran" is said only when none ran.**
  `daemon/chat_turn.py:4669`. Branch on the calls made, not on the output
  text; no bare "Press Retry" after a call that changes things.
- [x] **3.9 File claims are checked by name.** `daemon/chat_turn.py:9545`,
  `daemon/until_done.py:242`. When the turn has recorded paths, a claimed name
  absent from the record and the disk is marked, whatever else ran.
- [x] **3.10 Setting and record claims stay judged; "sent" is a kind.**
  `daemon/change_claims.py:2049, 2076`. A claim naming a setting label or a
  record kind is judged after any tool; a claimed send is answered only by a
  successful send tool this turn; a run's summary gets the same check.
- [x] **3.11 A run that did nothing is not "done".** `agents/outcome.py:97,
  581`. An unattended run with an empty ledger gets its own words ("finished
  without doing anything. Check what it said").
- [x] **3.12 "Permission denied" in a tool's output is not the owner's No.**
  `daemon/chat_turn.py:2936`. Match the registry's prefix only; a timed-out
  card gets its own label.
- [x] **3.13 A team job's receipt includes its workers' files.**
  `agents/outcome.py:394`, `agents/chat_delivery.py:187`,
  `agents/task_door.py:558`. `session_result` gets a team mode that reuses
  `mission._team_documents`.

## Group 4. Nothing is lost

Area rules: `docs/rules/tools.md`, `daemon.md`, `documents.md`, `settings.md`.

**Shipped in v1.504.0.** As built, where it differs from the lines below:
4.1 the inverse is kept after a failure only for the one-file kinds (a file
restored or removed, a memory note); a patch over several files, a rename
and the settings kinds are still dropped on failure, as before. An
interrupted write shows in the Undo list at once; pressing Undo before the
write has landed answers "nothing to undo yet". 4.2 a cleared run's rows are
written to a record file in the trash (kept 30 days) and then deleted: there
is no one-press "bring back" for the rows, and none is promised. A coding
run's branch is kept. 4.3 the Home line and `repair` picking the backup from
before the damage are the lead's additions. 4.7 the card's line for a change
that cannot be undone stays "This may not be undoable." 4.8 personas are
routed too (the lead's addition). 4.9 an `AgentRun` row is kept whole, so
that table now only grows. 4.10 run folders are carried up to 100 MB in all,
a single file over 25 MB left out. 4.11 is done inside the daemon with a
clock that does not count sleep; nothing was added to the desktop app.
Left open: page deletes of a phone channel, an app pack and a connector
(their Undo needs the key copy chat makes) and about 20 routes with no
record kind are on the scan test's allow list, each with its reason;
`harness/rewind.py` does not put back an interrupted write (`/undo` does);
a Stop that changed nothing can be listed as a change until the next start
settles it; the sleep fix was not run on a sleeping PC or the frozen build.

- [x] **4.1 Undo is written before the change.** `tools/registry.py:1050,
  1270, 1450`. The journal row is written with the intent and kept on every
  outcome that is not provably clean (stop, deadline, failed save, crash).
  An orphaned before-copy file is reclaimed.
- [x] **4.2 "Clear completed" moves (D7).** `daemon/routes/sessions.py:827`,
  `agents/orchestrator.py:2985`. Folders go to the app's trash; the button and
  the confirm say what happens.
- [x] **4.3 A set-aside database is shown, and backups wait.**
  `core/db.py:400, 506`, `maintenance.py:178`. A marker is kept; Home and
  doctor show it with one press to restore; no backup is pruned while it is
  unanswered.
- [x] **4.4 Writers save beside, then swap.** `documents/excel_tools.py:239,
  1344`, `documents/excel_reconcile.py:595`, `tools/builtins.py:977, 1110`,
  `harness/tools.py:215, 951`. One atomic-write helper.
- [x] **4.5 Three tools stop overwriting silently.**
  `documents/tools.py:732` (convert), `tools/images.py:681, 746` (convert,
  resize). Each keeps the replaced file (the `_keep_replaced` pattern) and can
  be undone.
- [x] **4.6 A cut-off run keeps its branch.** `agents/orchestrator.py:3306`
  (only with `git_native`). Never prune a run that was interrupted or can be
  resumed.
- [x] **4.7 The approval card tells the truth about Undo.**
  `tools/reversible.py:41`, `dashboard/lib/approvalWords.ts:140`. The card
  reads what this call can do, not the tool's class.
- [x] **4.8 A delete on a page is recorded and can be undone.**
  `daemon/routes/system.py:860`, `workflows.py:594`, `agents.py:4084`,
  `comm.py:281` and the other `@app.delete` routes. Each goes through the
  record journal chat already uses. Scan test: every delete route journals or
  is on the allow list.
- [x] **4.9 The clean-up says what it removes (D8).** `core/db.py:725`,
  `settings/schema.py:977`. The description names run details and Undo
  history; an aged run says so; `AgentRun.result` is kept.
- [x] **4.10 Backups include runs' own folders, or say they do not.**
  `maintenance.py:81, 130`. Files a run's result names are included.
- [x] **4.11 Sleep is not a failure.** `desktop/main.js:6164`,
  `tools/registry.py:1097`. Suspend and resume reach the daemon; live
  deadlines are extended by the time asleep.

## Group 5. Never stalls, approve once

Area rules: `docs/rules/automations.md`, `daemon.md`, `tools.md`.

**Shipped in v1.505.1** (v1.505.0 reached master but was never published: its
release run was cancelled at its time limit. The cause was the lead's own
addition, the hook that drops a removed workflow's OKs: it kept every
platform a test worker built alive, so the suite slowed until a test timed
out. v1.505.1 holds the grant store weakly and pins it with a test.)
As built, where it differs from the lines below:
5.1 Cancel stops the tool call itself (a table of calls in flight kept by
the engine, by run id), not the run's driver task: a scheduled workflow's
driver is not registered anywhere to cancel, and cancelling it would write no
result. 5.2 the OK is the existing kind (this tool, exactly these details,
30 days), now kept on the schedule or workflow; "Always" also reaches the
bell and the run's own page, which never offered it before, and a removed
schedule or workflow drops its OKs (both the lead's additions). 5.4 the
session start, a continued run, the queue's "queued" write, a helper's
settle, a failed run's record, a rule's fire, an autonomy booking, a
schedule made from chat and the calendar trigger's claim are off the loop;
the workflow engine's reads too.
Left open, for the owner: an OK for a schedule covers one exact step. A
scheduled job whose steps differ each run (code a model writes) will still
ask each time; an OK for "this tool, any details, on this schedule" is a
wider permission and is the owner's call. The 30 days are the owner's call
too. Also open: a workflow's TOOL steps take no standing OK; writes still on
the loop in `goals/engine.py`, `spawn_managed`, `_dequeue_next`, the
post-run review build, `agents/threads.py` and several page routes (listed
in the group's report); a few child-process sites (the developer `up`
command, the placeholder shell) are bounded but not tree-killed, and say so
in the scan test's allow list.

- [x] **5.1 A workflow tool step has a time limit and can be cancelled.**
  `workflows/engine.py:1247`, `daemon/routes/workflows.py:251`.
- [x] **5.2 A schedule or workflow can be approved once (D10).**
  `platform.py:2367`, `workflows/engine.py:1075`, `agents/runtime.py:1784`.
  New grant scopes; the schedule fire passes its agent's name.
- [x] **5.3 A workflow whose driver raises ends Failed.**
  `agents/orchestrator.py:854`, `workflows/engine.py` `run_record`.
- [x] **5.4 Database writes leave the event loop.**
  `agents/orchestrator.py:1292, 1641, 2068, 2393`, `reflex/router.py:218,
  239, 276`, `motivation/engine.py:656`, `workflows/tools.py:337, 358`,
  `scheduling/tools.py:109`.
- [x] **5.5 A scan test for child processes.** Every `subprocess` or
  `os.startfile` outside `core/procs` is on an explicit allow list.

## Group 6. Chat reaches everything

Area rules: `docs/rules/chat.md`, `messaging.md`, `desktop.md`, `browser.md`.

**Shipped in v1.506.0.** As built, where it differs from the lines below:
6.1 the page table is `daemon/pages.py` (44 rows: every page plus seven
Settings sections); a page becomes a door only when it is in the lookup's
first three lines, and only for the model's own lookup call. 6.2 every
answer from chat is a card, not only the accepts that act at once. 6.3
`backup_now` is a tool (allow tier); restore, pairing and updates are doors.
6.4 an app that connected gets no door (an earlier decision, pinned), and a
phone channel has no success door (it would push out its own set-up door).
6.5 the words wait in the desktop app and the chat page takes them once;
they never ride in a link. 6.6 no new tools: doors for removing a key,
forgetting a folder, deleting a project and templates, and a "not offered"
line for deleting, archiving or renaming a chat or coding session (the place
is the list the user is looking at). 6.7 the phone lane arms the read-only
and allow-tier record tools only.
Left open: forgetting a folder could be a tool with an Undo; deleting or
archiving a chat or a coding session from chat; the door for a page found by
the routing call rather than the model's own lookup.

- [x] **6.1 One page table.** Daemon-side: every page, its name, its link and
  what can be done there. The lookup gets a "pages" part from it, and a page
  row the lookup returns becomes a door (`capability_search/index.py:310`,
  `tool.py:226`, `daemon/chat_turn.py:4613`). Scan test: every dashboard page
  has a row.
- [x] **6.2 Improvements from chat.** Tools to list waiting suggestions and to
  answer one through its own route.
- [x] **6.3 Pairing, backup, restore and updates have doors.**
  `daemon/page_doors.py:886, 917`. Pairing and backup become page steps;
  "Back up now" becomes a tool; restore and update are doors.
- [x] **6.4 A thing made in chat has a door.** `daemon/doors.py:65`. The
  catalog is keyed on chat's record tools. Test: every chat tool that creates
  a record has a door.
- [x] **6.5 The quick window goes through chat (D11).** `desktop/main.js:3246`.
- [x] **6.6 The small actions.** Delete or archive a coding session, delete a
  chat, forget a folder, delete a key: a tool where it can be undone or
  carded, else a door.
- [x] **6.7 The phone gets the read-only record tools, and a true reason.**
  `daemon/chat_turn.py:5093`, `capability_search/join.py:310`.
- [x] **6.8 Phone history goes through the planner.** `comm/inbound.py:1222`,
  `daemon/routes/comm.py:781`, `comm/threads.py:346`.
- [x] **6.9 The identity spine at two more seams.** `agents/decompose.py:1226`
  (the final answer of a planned run), `daemon/routes/agents.py:2067` (apply a
  skill). Add both to the seam test.
- [x] **6.10 The browser sidebar says what belongs in the main window.**
  `daemon/routes/chat.py:4946`.

## Group 7. Learns from what went wrong

Area rules: `docs/rules/memory.md`.

**Shipped in v1.507.1** (v1.507.0 was never published: its release run
failed one test, see "Found along the way"). As built, where it differs
from the lines below:
7.1 the three doors are in `learning/went_wrong.py`. A correction of
substance is read in ONE shape only ("no, this, not that" in plain lower-case
words, straight after a reply, no model call). An undo is linked only when
exactly one saved reply names the change; its words are the app's own line
for the change plus the owner's request, quoted. A thumbs-down note is no
longer a lesson at once. Kept rows are walled by the chat they came from.
7.2 runs in the daily lesson tidy-up loop; the last 7 days, refusals left
out, once per week of failures; the kind is the existing "wants" suggestion
(maintainer agent, high risk, never starts itself). Lead review: the task
names the Activity rows and does not quote what the tool answered. 7.3 only
what a model wrote is stored (lead review: the fallback written from the
scores alone is shown, never stored). 7.4 re-scores by the difference.
Left open: corrections of substance in Chat › Code and agent runs; an undo
in a chat that was forked or where two replies list the same file; kept
preferences and condensed lessons older than this release are still not
walled; the suggestion card under the reply itself (they wait in
Improvements only).

- [x] **7.1 An undo, a correction of substance and a thumbs-down with a note
  each leave a suggestion**, with the turn as evidence, kept to the folder.
  `learning/corrections.py:16`, `daemon/routes/chat.py:3307`.
- [x] **7.2 Tool failures are read.** `improvement/engine.py:719` `sweep` and
  `scan_tool_failures` run from an existing daily loop.
- [x] **7.3 "Reflect" leaves suggestions that can be answered.**
  `dashboard/components/memory/Lessons.tsx:487`.
- [x] **7.4 A late rating changes the run's score.**
  `improvement/engine.py:170`.

## Group 8. Calm and plain

Area rules: `docs/rules/dashboard.md`, `daemon.md`.

**8.1 and 8.4 shipped in v1.506.0** (they touched files nobody else was
editing). 8.1: `core/failures.plain_failure` at 141 sites in `daemon/routes/`,
with a scan test and an allow list for the sites whose text is the code's own
sentence. 8.4: the Autonomy page and the watcher line say "Suggestions", and
"proposal" is on the dashboard's retired-word list.

**8.2 and 8.3 shipped in v1.507.1.** 8.2: `system_map._node` takes each
star's name and link from `daemon/pages.py`; "Sessions", "Chats" and "Models"
became "Agent runs", "Chat" and "Connections"; new stars for Improvements
(no count: its suggestions live in eleven stores) and Clients. 8.3: the three
refusal phrases say "it needs your OK, which can only be given in the Iron
Jarvis window", with a scan test.

- [x] **8.1 No raw exception text on screen.** `plain_failure(what, exc)` at
  every broad `except` in `daemon/routes/` (about 168). Scan test.
- [x] **8.2 One name per page.** `daemon/routes/system_map.py:595, 652` takes
  its labels from the page table (6.1); the map gains Improvements and Clients.
- [x] **8.3 No "lane" on screen.** `daemon/chat_turn.py:2862, 2902, 2914`.
- [x] **8.4 "Proposals" becomes "suggestions".**
  `dashboard/app/autonomy/page.tsx:586`, `daemon/page_doors.py:930`; the word
  joins the retired list.

## Tests that passed only on a retry (to watch)

This project counts a test that passes only on GitHub's retry as red. Since
group 3, four runs each showed one or two, a different test each time, all
timing-sensitive and none in code the group touched. No run before group 1
showed any in the six release runs checked. The whole suite takes as long as
it did (15 to 17 minutes), so the suite did not slow down.

| Run | Test | What was done |
|---|---|---|
| group 3, branch | `test_mcp_direct_launch_refresh_v14150` (a real pack process and its process tree) | Passed first time when its shard was re-run, 6 of 6 locally. Not changed. If it recurs, 2.2 (how a pack is started) is the first suspect |
| v1.504.0 release | `test_build_app_check_again_v1497` (waited with fixed 0.1 s sleeps) | Fixed in v1.505.0: waits for the reply's turn to end |
| group 5, branch | `test_records_build_review_v14380::test_the_fresh_matchers_stay_linear` (wall-clock bound in a fresh interpreter) | Fixed in v1.506.0: warms up before timing. The matchers' timings are unchanged since v1.502.0 |
| group 5, branch | `test_records_projects_v14350::test_agent_pause_and_undo_puts_back_the_day_off_exactly` | 12 of 12 locally. GitHub keeps no trace of a first try, so the cause is unknown. Not changed |
| group 9, branch (2nd run) | `test_north_star_lesson_tidy_v1501::test_a_tidy_pass_publishes_one_event_naming_what_it_merged` | Fixed in v1.510.0: two notes written in the same clock tick tied on their time, so either could be merged into the other. The later row now always counts as newer (`learning/engine.dedup`), pinned by a test that gives both notes one time |
| group 9, branch (1st run) | no test named: shard 6 ran 30 minutes (others 9 to 15) and was cancelled; GitHub kept no log | Not reproduced: the same files passed here (2,281 in 8 minutes) and the second run's shard 6 took 15 minutes. Cause unknown. If a shard hangs again, let it reach its 45-minute limit so the log is kept |

To find the cause of an unexplained one, the test run would need to print
a retried test's first failure (`-rR` shows only its name); that is a change
to `.github/workflows`, left for the owner.

## Found along the way

- **Local test runs could start the owner's real Claude Code** (found
  2026-11-16, fixed in v1.507.0). Two hand-off tests undid their stand-in
  before their service had stopped; on this PC a start still in flight then
  launched the real `claude`, signed in, with the owner's extensions. Four
  were left running by a local run that was stopped early. None has a
  transcript, so none was sent a message. Fixed for the whole suite by
  `tests/_real_tool_guard.py` (see `docs/rules/ci.md`). GitHub's runners have
  no coding tool installed, so the suite never showed it there.
- **A helper stopped while its run was being created or announced stayed
  "active" for ever** (found 2026-11-16 by v1.507.0's release run, fixed in
  v1.507.1). `tests/test_team_review_finish_ship_v1401.py` gives a review a
  0.5 s limit; on one GitHub worker the reviewer's start took that long
  (1 ms on this PC; why is not known), the stop landed before the
  hand-off's settle guard, and the run was never settled. The test failed
  on its retry too, so nothing was published. The hole is older than this
  plan. Fixed in `Orchestrator.create_session` and `delegate`; the same
  window in the follow-up path (`continue_session`) is open.
- **A coding tool whose start is in flight when Iron Jarvis shuts down is
  started anyway** (fixed in v1.512.0, group 12). `hosted/service.py` sets `_closing` in `shutdown`
  but neither `_ensure_process` nor `_start_process` reads it after their
  waits, so the stop pass can finish before the process exists and nothing
  then stops it. Not fixed here: a change to how child processes start and
  stop has to be checked on the packaged build. First item for the second
  review of `hosted/`.

## Not reviewed

The inside of coding sessions and the night shift (`harness/`, `hosted/`),
browser and computer-use tools, `fleet/` and `iron_proxy/`, shared computers
beyond the task door, and a line-by-line comparison of the two chat lanes. A
second review of these was done on 2026-11-16: see "Second review"
below, which also lists what is still not read.

## Second review (2026-11-16, at v1.507.1)

Eight read-only passes over the areas the first review skipped; nothing was
run and nothing was changed. 98 findings. **Checked** means the lead read the
lines and found no guard; **pass only** means the pass reported it as
confirmed and the lead has not re-read it; **suspected** means a step on the
path was not verified. The number in brackets is the north-star behaviour it
breaks. The fixes are planned in "Second fix plan" at the end of this file.

Severity counts: 12 high, 40 medium, 46 low.

### Other computers (`fleet/`, `peers/`, the task door)

- [x] **F1 (2, high, checked). A task from another computer gets this PC's
  memory.** `daemon/routes/agents.py:3622-3645` starts the run with no
  `skip_memory`; `agents/runtime.py:705-717` builds the client wall from
  `session.task`, which is the sender's text; `recall`, `ltm_search`,
  `history_search` are not in `core/trust.LOW_TRUST_DENY`. The answer is
  posted back. Fix: `skip_memory` and an empty named set for a `remote:`
  run, and keep the cross-PC readers away until the owner can choose per
  computer.
- [ ] **F2 (1, high, checked). "Send coding work to a model server" has no
  reader.** Only `daemon/routes/fleet.py:581-668` (the view and the writer),
  the schema and the config field mention it; the page says "Coding work
  goes to <server>". Fix: wire it in the router (refuse by name when the
  server is down) or say it is not in effect.
- [x] **F3 (3, high, checked). An answer from another computer is sent
  once.** `agents/task_door.py:504-516` deletes the answer key before the
  POST; `666-673` allows one attempt; the asking chat and mission card wait
  for ever (`agents/threads.py:1941-1947`, `agents/mission.py:1582-1621`).
- [ ] **F4 (1, medium, checked). A failed or stopped task comes home marked
  "done".** `task_door.py:703-707` always sends `"kind": "done"`; no outcome,
  model or cost is sent. `tests/test_another_jarvis_v13570.py:488` pins it.
- [x] **F5 (2, medium, checked). Removing a pairing does not stop its running
  tasks**, and with the pairing gone the answer goes to the address the task
  named (`task_door.py:456-458`; `daemon/routes/peers.py:501-513`).
- [x] **F6 (2, medium, pass only).** A paired computer is remembered by the
  address it had when it paired (`routes/peers.py:366-372`,
  `task_door.py:427-441`).
- [x] **F7 (2, medium, pass only).** A model server's key is sent over plain
  http to any address, and an address with a password in it is accepted
  (`routes/fleet.py:375-402`, `fleet/endpoint_auth.py:45-56`).
- [ ] **F8 (4, medium, checked).** Refresh on the Model servers page does not
  force a re-check (`routes/fleet.py:721-722` calls `sample_once` without
  `force=True`); a server that came back is refused for up to ten minutes.
- [ ] **F9 (1, medium, pass only).** Tokens a proxy sent to the cloud are
  counted as "local" and "avoided spend" (`routes/fleet.py:1147-1162`).
- [x] **F10 (2, low, pass only).** With network access on, the daemon listens
  on every network the PC joins; the filter is the Host header
  (`desktop/main.js:190-200`, `daemon/auth.py:62-69`).
- [ ] **F11-F15 (low, pass only; F11, F12 and F14 fixed in v1.509.0).** The sampler probes addresses a proxy
  reported (`fleet/probes.py:597-617`); over 500 proxy names an unlisted
  name can read as local (`fleet/upstreams.py:289-290`); exception names on
  the Model servers page (`routes/fleet.py:282-284`, pinned by a test); the
  task door reads the database and the vault before any limit
  (`routes/agents.py:3527-3552`); a probe has no total time limit
  (`fleet/probes.py:65-70`, suspected).
- Not closed: a peer's answer is added to the owner's chat with no fence
  (`routes/agents.py:3380-3403`); `_reply_files` sends every path in the
  run's documents without re-checking the folder (`task_door.py:582-618`);
  `message_agent`, `blackboard_post` and `consult` are not kept from a
  low-trust run.

### Browser add-on, sidebar and the isolated browser (`browser/`, `computeruse/`, `extensions/chrome/`)

`computeruse/` drives a separate headless browser; nothing in the code clicks
or types on the Windows desktop.

- [x] **B1 (2, high, checked). A page can close the "untrusted data" fence.**
  `computeruse/safety.py:94-109` (`wrap_untrusted`) does not neutralise the
  closing marker; every browser read uses it (`browser/tools.py:309`). The
  cure exists in `memory/steward.py:352-362` (`_defang`).
- [x] **B2 (4, high, checked). Enter submits a form unjudged.**
  `browser/tools.py:1075-1085` never passes `press_enter` or the key to the
  risk check; the add-on then calls `form.requestSubmit()`
  (`extensions/chrome/src/content/actions.ts:763-780`).
- [x] **B3 (1, medium, checked).** When the answer to a click is lost the
  model is told to retry, so the action can run twice
  (`browser/errors.py:115-118, 149-153`; `browser/tools.py:1094-1101`).
- [ ] **B4 (2, medium, checked).** Every tab switch, page load and download
  is stored as an event with its full address for 90 days, whether or not
  Jarvis was asked anything (`extensions/chrome/src/background/index.ts:490-597`,
  `browser/extension_backend.py:1456-1528`).
- [ ] **B5 (2, medium, checked).** Text typed through `web_action` into a
  sensitive field is stored in plain text in the approvals table and shown
  on the card (`computeruse/tools.py:240-245, 278`; `approvals.py:60`).
- [x] **B6 (4, medium, pass only).** A risk approval nobody could be asked
  about stays pending; approving it later lets the next identical call run
  on another page (`browser/tools.py:1245-1274, 1351-1366`).
- [ ] **B7 (2, medium, pass only).** The isolated browser's site list is
  checked on the typed address only, not after a redirect or a click
  (`computeruse/tools.py:132-137`).
- [ ] **B8-B14 (low; B8, B11 and B14 fixed in v1.509.0, B9 and B10 in v1.511.0).** A download's name reaches the model unfenced
  (`tools/download_tools.py:201-221`); after an approved card a click by
  role and name runs on whatever page the tab then shows (suspected);
  nothing stops Jarvis being sent to the app's own pages, the daemon's port
  or `file:` (`browser/protocol.py:478-494`); an unpaired local program's
  text shows in `browser_get_status`; exception text on screen
  (`browser/panel.py:1710`, `computeruse/tools.py:395`); `web_action` does
  database work on the event loop (`computeruse/tools.py:257-311`); a
  terminal's line reaches the model unfenced from three pane tools
  (`tools/pane_tools.py:114-121`, suspected).

### The service that runs other coding tools (`hosted/`)

- [x] **H1 (4, high, checked). A coding tool that ignores its own interrupt
  cannot be stopped from the app.** `hosted/service.py:3859-3860` gives up
  after the wait; Close, Archive, Delete and Open in terminal are all
  refused while the turn runs (`3959-3968`, `1281-1286`). Fix: after a bound
  or a second Stop, `_stop_process` (the tree kill) and say so.
- [x] **H2 (3, medium, checked). A stop is not serialized with a start.**
  `_stop_process` (`service.py:2901-2924`) never takes `live.lock` and reads
  `live.turn` after its awaits: a message sent during an idle stop gets a
  second process and is then ended "Stopped"; Stop during a start followed
  by Delete leaves a tool running that nothing tracks. Same family as the
  open shutdown item. Needs checking on the packaged build.
- [x] **H3 (4, medium, checked).** Emergency stop skips a reply Jarvis
  started by itself once it was adopted across a restart
  (`hosted/adopt.py:482-501`, `hosted/carry_on.py:1231-1245`).
- [ ] **H4 (1, medium, checked).** A helper that was stopped or cut off is
  listed "done" after ten minutes or a restart (`hosted/handoff.py:195-210`).
- [x] **H5 (3, medium, checked).** Revert in the Changes pane overwrites a
  file with no copy and no record (`hosted/panes.py:1137-1150`).
- [x] **H6 (3, medium, checked).** A failed write of the session log drops
  that batch for good (`hosted/events.py:335-354`); a lost "done" can make
  the next start carry on a reply that had finished.
- [ ] **H7-H14 (low; H7 and H9 fixed in v1.511.0, H8, H13 and H14 in v1.512.0; H10, H11, H12 wait for groups 14 to 16).** A PATCH applies the tool's mode before the rest of
  the request is checked (`service.py:2211-2296`); a Stop as the start
  completes can still deliver the message (`service.py:3607`, checked); a
  branch copies Full access and the tool boxes without asking
  (`hosted/branching.py:684-696`); the archive summary lists Jarvis's own
  carry-on messages as the user's asks (`hosted/archive_summary.py:126-127`);
  exception names on screen (`service.py:2755-2761`); "on this network"
  judged from Jarvis's own address (suspected); a tool being stopped when
  the code-host link breaks is left running (suspected); a rewind or branch
  can leave a cut reply to be carried on at the next start (suspected).

### Coding-tool adapters and the accounts proxy (`hosted/<tool>.py`, `iron_proxy/`)

- [x] **A1 (3, high, checked). Removing an account deletes its whole folder
  for good.** `daemon/routes/iron_proxy.py:633-640` calls the bundled
  manager's `deleteProfile`, which removes the account's home recursively
  (`desktop/vendor/iron-proxy/iron-proxy.mjs:4726-4741`): its sign-in and
  every conversation kept on it. No trash, no check for a running session.
- [x] **A2 (1, high, checked in code and a recorded exchange). Claude Code's
  "Allow for this session" is saved for good.** `hosted/claude.py:656-671`
  passes Claude Code's own suggestion back whole, destination
  `localSettings` included, while the card says "for the rest of this
  session" (`682-700`, `1154-1158`). Fix: rewrite the destination to
  `session`. Claude Code's write to disk was not observed.
- [ ] **A3 (2, medium, checked).** The account manager prints its access
  token on start, and Jarvis sends that output to `logs/iron-proxy.log`,
  which every backup carries (`iron_proxy/service.py:834-847`).
- [ ] **A4 (2, medium, checked in the bundle).** The account manager's model
  routes need no token on this PC, so any local program can spend the
  subscriptions through it (`iron-proxy.mjs:5567-5576, 6316`). Fix is in
  the bundled program plus a switch at start.
- [x] **A5 (4, medium, checked).** A session in Ask first or Plan can hand
  work to a Pi or Antigravity helper that asks nothing
  (`hosted/handoff.py:324-327`, `hosted/service.py:308-311`); the card does
  not name the tool.
- [ ] **A6 (2, medium, suspected).** A session on this PC's own login
  inherits the user's API keys; the tool may bill the key while the receipt
  says list price on the subscription (`hosted/process.py:81-86`).
- [ ] **A7 (1, medium, suspected).** A Codex `turn/completed` from another
  thread ends the user's reply as done (`hosted/codex.py:1312-1315`; the
  guard exists only for `turn/started`).
- [ ] **A8 (2, medium, checked).** With every account of a provider turned
  off, chat runs on this PC's own login with nothing said
  (`iron_proxy/accounts.py:889-894`).
- [ ] **A9-A13 (low; A12 fixed in v1.511.0, A11 in v1.512.0).** Codex's move to another model is said without the
  model's name (`codex.py:1345-1346`); the usage check hands the vendor's
  program the daemon's whole environment, install token included
  (`iron_proxy/usage_read.py:286-299`, checked); Stop before Codex names the
  turn does nothing (`codex.py:1118-1131`); Pi keeps `--approve` after the
  user later declines the folder (`service.py:2795`); an unknown stop reason
  reads "completed" (`hosted/acp.py:464-465`), an unreported step is drawn
  "done" (`hosted/antigravity.py:1219-1224`), exception names on screen
  (`iron_proxy/service.py:793`, `client.py:199`).

### How a coding reply ends (`harness/finish.py`, `ci_fix.py`, `attempts.py`)

- [ ] **D1 (1, high, checked). The pull-request auto-fix says "Pushed the
  reviewed fix" when its commit left files out or committed nothing.**
  `daemon/routes/ci_watch.py:774-803` never reads `left_out`;
  `harness/ci_fix.py:807-813` turns "nothing to commit" into `None`.
- [ ] **D2 (1, high, checked). A carried-on reply can be recorded "tests
  passed" for a run that failed.** `harness/finish.py:4842` adds the cut
  part's commands to `_commands` with no entry in `_cmd_meta` (appended only
  at `2794`), so `_reusable` (`2855-2867`) pairs a command with a later
  command's result.
- [ ] **D3 (1, medium, checked).** A reply resumed after a restart whose cut
  part held the finish note ends with every "until done" guard off and no
  verdict (`daemon/chat_turn.py:8839-8840`, `finish.py:2983-2985, 3321`).
- [ ] **D4 (1, medium, checked).** The auto-fix reviews and pushes a fix
  whose own finish check recorded failing tests
  (`routes/ci_watch.py:594-666` never reads the line's finish record).
- [x] **D5 (4, medium, checked for the test).** A whole Maven, Gradle, tox
  or nox line counts as "the tests", so `mvn test deploy` can be run a
  second time by the check (`finish.py:885-886, 923-924`).
- [x] **D6 (4, medium, pass only).** Stop is not honoured while the check
  waits for a long test run on a GPT model (`finish.py:3839-3857`).
- [ ] **D7-D11 (low; D9 fixed in v1.512.0).** "reviewed by <member>" over a review that reached
  nobody (`finish.py:3984-3999`); ESLint "no problems in <file>" for a file
  it ignored (`harness/checkers.py:410-412`, checked); a watch whose
  worktree folder is gone stays "fixing" for ever (`ci_watch.py:623`);
  exception names on screen (`harness/helpers.py:1233-1236`,
  `attempts.py:1649, 1660`); an attempt cut at the step limit reads
  "Finished" (`attempts.py:1291-1308`).

### A coding session's tools, git, rewind and the night shift

- [x] **T1 (4, high, checked). A night task cancelled while it is starting
  is recorded "You stopped it" and then runs.**
  `daemon/routes/night.py:1154-1159` stops a turn only if one exists;
  `start_task` (`882-885`) never re-reads the task; `settle_running` reads
  the stop note only after the turn is over (`622-624`, `661`).
- [x] **T2 (4, high, checked; the owner's call). The git guard is off when a
  session works in the folder directly**, the composer's default
  (`harness/git_guard.py:640-641, 662-663`;
  `dashboard/components/harness/HarnessStartComposer.tsx:294`). After one
  "allow commands for this session", a push, force push, `reset --hard` or
  `clean` runs with no card on the user's own checkout.
- [x] **T3 (3, medium, checked).** Rewind does not put back files changed by
  a session's helpers, and answers that it rewound
  (`harness/rewind.py:202-221, 518`).
- [x] **T4 (3, medium, checked).** A patch over several files loses its Undo
  and its saved copies when it is cut off or the app dies while it writes
  (`tools/undo.py:330-336`); its writes are not atomic
  (`harness/tools.py:427-455`).
- [ ] **T5 (1, medium, checked).** A night task's "Done" or "Needs you" is
  read from the finish check's first answer, not from what was still open
  at the end (`harness/night.py:1083-1099`; `finish.py:4496-4518`).
- [ ] **T6 (1, medium, checked).** The reason for open points is filed as
  "info" because "unmet" is not in `NOTE_KINDS` (`night.py:92-96, 543`), so
  a "Needs you" row carries no reason.
- [ ] **T7 (1, low, pass only).** A night task that changed nothing and ran
  no check is announced "Done" (`night.py:1050-1051`).
- [x] **T8 (3, medium, checked for checkout and stash).** On its own branch
  the guard misses `git remote set-url`, `git reset <branch>`,
  `git checkout <file>`, `git stash pop` and a separator inside a quoted
  message (`git_guard.py:72, 414, 488, 502`).
- [ ] **T9-T10 (low).** `process_start` works out its environment on the
  event loop (`harness/process.py:676`, checked); "Night tasks run now,
  until 7:00 AM" although a started task runs past it
  (`harness/night_window.py:104-105`).

### A coding session's stored sessions, compaction and page browser

- [ ] **S1 (2, medium, checked).** The one-shot screenshot browser has no
  address guard, and the agent's browser judges only top-level pages, never
  frames (`harness/page_shot.py:110-130`, `page_browser.py:1146-1163`): a
  local page can show the model another local app's page.
- [x] **S2 (1, medium, checked).** A failed transcript write at a reply's
  end is swallowed (`daemon/routes/build.py:2017-2026`); the reply is gone
  from the session and the kept copy is deleted (`2328-2330`).
- [ ] **S3 (1, medium, checked on the daemon side).** A file in the folder
  (`.ironjarvis/harness/finish.toml`) can turn the finish check off and
  nothing on screen says so (`harness/components.py:545-557`,
  `finish.py:4787-4790`).
- [ ] **S4 (4, medium, checked in code, not run).** One search pattern can
  freeze the app: the search's time limit is read only between files
  (`harness/search.py:188-191, 233-235`).
- [ ] **S5 (1, medium, pass only).** A reply's failure marks never reach the
  next model or the summarizer (`harness/sessions.py:998-1022`,
  `compact.py:282-322`).
- [ ] **S6-S12 (low; S8, S11 and S12 fixed in v1.510.0, S6 in v1.511.0).** Typing with "submit" sends a form with no risky-click
  check (`page_browser.py:1921-1931`, suspected; the twin of B2); a lesson's
  first line goes into an event unmasked (`harness/library.py:1292-1304`,
  suspected); Delete stops a session's programs before it refuses, and
  Choose account writes the account before the turn is refused
  (`routes/build.py:4808-4814, 5362-5378`); page screenshots outlive their
  session in one shared folder (`page_shot.py:350`); a turn can start on any
  free account when the list could not be read (`routes/build.py:744-759`,
  suspected); accepting a suggested edit writes a file outside the undo
  journal (`library.py:1699-1720`); the 200-line cap cuts a transcript in
  the middle of a turn (`sessions.py:697`).

### The two chat lanes, side by side

The lanes match stage by stage wherever one shared helper does the work. The
differences are at the edges.

- [ ] **L1 (3, medium, checked).** Both phone doors run a turn without its
  chat id (`comm/inbound.py:1246`, `daemon/routes/comm.py:823`): the chat's
  own usage total stays at zero and its notes are keyed with every other
  phone conversation's (`memory/origin.py:47`).
- [ ] **L2 (2, medium, checked).** A reply typed at the desk into a phone
  conversation has no project and runs at full trust
  (`routes/comm.py:823`, against `inbound.py:1245-1257`).
- [ ] **L3 (8, medium, checked).** The automatic summary of a long chat is a
  paid model call that is on no receipt (`daemon/app.py:3409` drops the
  usage; `chat_turn.py:373-410`).
- [x] **L4 (1, medium, pass only).** A turn that fails after its tools ran
  says only that it failed; on the phone that is the whole message
  (`chat_turn.py:14547-14583`, `routes/chat.py:8093-8119`).
- [ ] **L5 (1, low, checked).** On a reply that ends in a workflow card the
  plain lane drops four honesty notes the streaming lane adds
  (`chat_turn.py:14716-14836` against `routes/chat.py:8457-8484`).
- [x] **L6 (3, low, checked).** A cancelled plain-lane turn leaves no usage
  row and no safety scan (`chat_turn.py:14547`; the streaming lane has
  `except BaseException` at `routes/chat.py:8120`).
- [x] **L7 (4, low, checked).** The plain lane does not honour "Ask before
  every action" (`chat_turn.py:13462, 14041`); only a direct caller of the
  route reaches it today.
- [ ] **L8 (7, low, pass only).** Engine words in replies: "stopped after 7
  tool rounds", raw tool names, a Python exception as a step's output.
- [ ] **L9 (4, low, checked).** The language rewrite has no time limit in
  either lane (`chat_turn.py:2443-2456`).

### Still not read

Each pass listed what it did not read. The larger pieces: the browser
sidebar's own code (`extensions/chrome/src/sidepanel/sidepanel.ts`) and
`browser/panel.py`, `snapshot.py`, `screenshot.py`, `pairing.py`,
`panetokens.py`; the door that gives coding tools Jarvis's own tools
(`mcpserver/`); `codehost.py`; most of `daemon/routes/build.py`
(`_ReplyWatch`, the resume pass); `harness/attempts.py` at night;
`tools/permissions.py` and `sandbox/cmd_guard.py`; the bundled account
manager beyond its server and delete code; how a reply is saved to its chat
and `daemon/durable_turns.py`; `terminals/`.

## Second fix plan (groups 9 to 16)

The owner said "Yes, plan and fix" on 2026-11-16. The 98 findings above are
fixed the way the first plan's were ("How the fixes are made", points 2, 4
and 5): one release per group, Opus 5.5 at high effort implementing, the lead
reading every diff and proving each new test red before green. A finding
marked "pass only" or "suspected" is first proven with a failing test; one
that cannot be made to fail is marked "not a defect" here with the reason,
and not changed.

**The shared causes this time**

| Cause | The one piece | Closes |
|---|---|---|
| A run started for another computer treated as the owner's | one "remote run" reading (`task_door.is_remote_task_origin`) that the memory injections, the wall and the tool roster all ask | F1, F5, F14 |
| Outside text closing its own fence | the marker is neutralised inside `wrap_untrusted`, so every caller is covered | B1, B8, B11, B14 |
| A stop that races a start | every stop of a coding tool takes the session's lock; a claim is refused while a start is in flight | H2, H8, the open shutdown item |
| An ending read from the wrong field | one reading of a finish record (`night.finish_needs_you`) that the night, the auto-fix and the day's notice share | T5, T6, T7, D4 |
| A second way to submit or discard that the gate does not see | the gate judges the effect (a submit, a discard, a push), not the spelling | B2, S6, T2, T8, D5 |
| A raw exception on screen | `core.failures.plain_failure` | L8, B12, H11, F13, D10, A13 |

**Order.** Data crossing to another computer or out of a fence first, then
loss, then consent, then Stop, then false "done", then secrets at rest, then
stalls, costs and wording.

| Group | Theme | Findings |
|---|---|---|
| 9 | Outside text stays data, other computers stay outside | F1, F5, F6, F7, F10, F11, F12, F14, B1, B8, B11, B14, and the three "not closed" lines under other computers |
| 10 | Nothing is lost | A1, F3, H5, H6, S2, S8, S11, S12, T3, T4, L4, L6, B3 |
| 11 | Every gate judges what will happen | B2, S6, T2, T8, A2, A5, A12, H7, H9, B6, B9, B10, L7, D5 |
| 12 | Stop means stopped | H1, H2, H3, H8, H13, H14, T1, A11, D6, D9, the open shutdown item and the open `continue_session` window |
| 13 | Done means done in a coding session | D1, D2, D3, D4, D7, D8, D11, T5, T6, T7, T10, S3, S5, L5 |
| 14 | What helpers, tools and servers report | H4, H10, F2, F4, F9, A7, A9, and the three status lines of A13 |
| 15 | Secrets at rest and what leaves the PC | A3, A4, A6, A8, A10, B4, B5, B7, S1, S7, S9, S10, L1, L2, H12, the `proxy.json` line of A13, and the Settings model-server slot left open by group 9 |
| 16 | Never stalls, every cost counted, plain words | S4, L9, T9 (and the git guard's file checks, left open by group 11), B13, F8, F15, L3, L8, B12, H11, F13, D10, the wording line of A13 |

**Decisions taken as recommended.** Each changes something the owner will
notice. Say so to change any of them.

| # | Decision | Taken |
|---|---|---|
| E1 | A task from a paired computer (F1) | Runs with none of this PC's memory, lessons, profile or notes, and cannot search memory, history or files outside its own folder, until a per-computer choice exists |
| E2 | Removing an account (A1) | Its folder is moved to Iron Jarvis's trash, never deleted; refused while a session or terminal runs on it; the button says what goes |
| E3 | Git commands in a session that works in the folder directly (T2) | A push, a force push, and anything that throws work away (`reset --hard`, `clean`, `checkout` of files, deleting a branch) ask every time, even after "allow commands for this session" and under "Don't ask". A commit does not ask |
| E4 | Enter in a form field (B2, S6) | Judged as a submit: it asks unless the form's own submit button is known and reads as harmless |
| E5 | Claude Code's "Allow for this session" (A2) | Kept for the session only; nothing is written to the folder's settings |
| E6 | A helper on a tool that cannot ask (A5) | Refused in words when the parent session is in Ask first or Plan |
| E7 | "Send coding work to a model server" (F2) | Decided when group 14 reads the setting: wired if its meaning is defined in code, else the page says plainly that it is not in effect |
| E8 | Raw error text on the Model servers page (F13) | A plain sentence on screen; the raw text stays under Details |
| E9 | The bundled account manager's open model routes (A4) | Closed from the Iron Jarvis side if the bundle allows it; a change to the bundle itself is the owner's to release |
| E10 | A paired computer whose address changed (F6) | Its address is never updated by itself. The card says it changed, and its answer is held back rather than sent to the old address, until the two are paired again |

### Group 9 as built (v1.509.0)

Fixed: F1, F5, F6, F7, F10, F11, F12, F14, B1, B8, B11, B14, and two of the
three "not closed" lines (a peer's answer is fenced; only files inside the
run's folder are sent). The third (`message_agent`, `blackboard_post`,
`consult`) was not a defect: a remote task's board is its own, and a consult
gives the consulted agent no tools.

Where it differs from the plan:

- **F1 became an allow list.** The implementer's ten-name deny list closed
  the memory tools. The lead's review found the allow tier also held
  `list_folder` and `read_document` (any folder on this PC), the owner's
  browser tabs, terminals, connected apps and settings, 99 tools in all. A
  remote run now keeps only `core/trust.REMOTE_RUN_KEEP`, and its reads are
  confined to its own folder in `core/fs_policy`. `consult` and
  `spawn_agent` are off the list on purpose.
- **F6 holds the answer back** instead of updating the address (decision
  taken by the lead: an address updated from a request would hand answers
  to anyone holding a captured token).
- **F7** reuses the rules of `peers.store.normalize_address`, not the
  function (a model server's address needs a path such as `/v1`).

Left open (group 15 takes the first):

- The Settings "your own model server" slot can still send a key over plain
  http to an address outside the network.
- The dashboard's add form saves a key to the vault before the edit that is
  then refused; the server is left saved with no key.
- A pairing kept under a host name cannot be compared with a source
  address, so it is never read as moved.
- `/chat/compact` builds its own history and still sees an agent's line
  unfenced.
- Four older copies of the fence-marker pattern still stand beside the
  shared one.
- The link part of the "only files inside the folder" test is skipped where
  the test machine cannot make links.

### Group 10 as built (v1.510.0)

Fixed: A1, F3, H5, H6, S2, S8, S11, S12, T3, T4, L4, L6, B3.

Where it differs from the plan, or went further:

- **A1** also works across drives (a full copy to the trash first), added
  in the lead's review; a plain rename cannot cross drives and the account
  could then never have been removed.
- **F3** stores "owed" in the run's reply spec plus the kept key (no new
  table), and rides the daemon's existing 30-second pass.
- **H5** also makes a revert wait while the tool or a helper is still
  working in the folder. The new refusal carries no operating-system error
  text (lead's review).
- **S2** needed the screen as well: the implementer's daemon change put the
  sentence on the last frame, and nothing drew it. The page now keeps the
  reply with the sentence.
- **T4** raised a hole the plan did not list: Rewind skipped a write that
  failed or was stopped with its Undo kept, and still answered "rewound".
  The lead's review made Rewind name it, with where to undo it.
- **L4** points Undo at the Activity page, which works from every surface.

Left open:

- The Undo of adding an account still deletes the profile directly (it
  only runs for an account that never signed in).
- A pairing removed while it is owed answers keeps their keys until the
  7-day let-go.
- A page reload drops a kept unsaved reply until the next start.
- A hand-off row is not linked to its exact helper session, so with two
  hand-offs in one rewind, neither is listed as a command if either
  helper left rows.
- A Local only refusal after a tool round does not carry the "what it
  already did" sentence.

### Group 11 as built (v1.511.0)

Fixed: B2, S6, T2, T8, A2, A5, A12, H7, H9, B6, B9, B10, L7, D5.

Where it differs from the plan:

- **E3 (T2) is wider than written.** In the user's own folder everything
  the guard reads except a commit asks every time: also a history rewrite, a
  git settings change (where `origin` points), taking work out of the stash,
  and a change on GitHub. Kept because both kinds of session then read the
  same; the Handbook says so and invites the owner to shorten the list.
- **B10 was narrowed in the lead's review.** The implementer made every
  click on a local page ask. Only GOING to a page on this computer asks now;
  Iron Jarvis's own pages are refused outright, which was the real risk.
- **A5 uses four levels of strictness**, not three: "edits files freely,
  asks before commands" is its own level, and Antigravity's "plan" is not
  counted as read-only (its own description says it can change files).
- **B9** is closed on the add-on side too, so it holds only once the
  installed add-on is the new one.

Left open:

- The guard's one existence check for a `reset` / `checkout` word runs on
  the event loop (group 16).
- Handing more work to an existing helper does not re-check that helper's
  mode against the parent's.
- With network access on, the dashboard reached at the PC's own network
  address is not refused by the browser tools (the check is loopback only).
- A chat box that sends on Enter through its own script (a textarea) is not
  judged as a submit, matching what the add-on does by default.

### Group 12 as built (v1.512.0)

Fixed: H1, H2, H3, H8, H13, H14, T1, A11, D6, D9, the open shutdown item and
the open `continue_session` window.

Where it went further than the plan:

- **A background run stopped before its very first step** stayed "active"
  for ever too (the task is cancelled before its coroutine runs, so the
  run's own cancel handler never did). Found while closing the
  `continue_session` window; fixed in `_start_managed`.
- **D9's suspected half was real and worse than written**: an unexpected
  error after a review left the watch "reviewing", and three passes then
  paid for three reviews of one fix and pushed it with no round counted.
- **H1 needed the screen**: the daemon answers "asked, not stopped yet",
  and the view now shows those words and offers Close it now.
- **The slow start on GitHub (v1.507.0)** has a likely cause now: two hops
  on a shared thread pool per run created.

Left open:

- None of the start / stop changes were driven on the packaged build or
  through the code host with a real coding tool; the tests use stand-ins.
  What would show a problem: a session that will not start after an idle
  stop, or a tool left in Task Manager after Delete or quit.
- A "Retry failed" stopped after its failed items were reset leaves them
  reset; a second Retry then says nothing failed.
- A stop that timed out on a link that stays up waits for the next link.
