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
| v1.513.0 release | `test_harness_bench_product_path_review_v14250::test_a_turn_cut_off_after_its_stop_leaves_nothing_a_daemon_would_carry_on` (real time: a 4 s task limit, a 1 s grace, a real program) | Not reproduced: 6 of 6 here on a loaded PC, and it had passed in the branch run of the same commit and in the four releases before. GitHub keeps no first failure, so the cause is unknown. Not changed |
| v1.514.0 release (failed twice, not published) | `test_clients_lanes_v1499::test_a_chat_code_reply_keeps_its_client_question_and_reads_its_answer` (the lane's real one-second wait for the client question) | Fixed in v1.515.0: the test no longer races the wait, and a skipped question says why in the log. Not reproduced here; see "Group 15 as built" |
| v1.515.0 release | `test_finish_shell_writes_review_v1387::test_a_stalled_walk_never_holds_the_reply_and_is_never_started_twice` (a stall that ended by itself after 8 s; a 3 s bound on the reply's start) | Fixed in v1.516.0: the stall lasts until the test releases it, and the test asserts each reply ended while the walk was still stalled. Two mutations turn it red |

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
- [x] **F2 (1, high, checked). "Send coding work to a model server" has no
  reader.** Only `daemon/routes/fleet.py:581-668` (the view and the writer),
  the schema and the config field mention it; the page says "Coding work
  goes to <server>". Fix: wire it in the router (refuse by name when the
  server is down) or say it is not in effect.
- [x] **F3 (3, high, checked). An answer from another computer is sent
  once.** `agents/task_door.py:504-516` deletes the answer key before the
  POST; `666-673` allows one attempt; the asking chat and mission card wait
  for ever (`agents/threads.py:1941-1947`, `agents/mission.py:1582-1621`).
- [x] **F4 (1, medium, checked). A failed or stopped task comes home marked
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
- [x] **F8 (4, medium, checked).** Refresh on the Model servers page does not
  force a re-check (`routes/fleet.py:721-722` calls `sample_once` without
  `force=True`); a server that came back is refused for up to ten minutes.
- [x] **F9 (1, medium, pass only).** Tokens a proxy sent to the cloud are
  counted as "local" and "avoided spend" (`routes/fleet.py:1147-1162`).
- [x] **F10 (2, low, pass only).** With network access on, the daemon listens
  on every network the PC joins; the filter is the Host header
  (`desktop/main.js:190-200`, `daemon/auth.py:62-69`).
- [x] **F11-F15 (low, pass only; F11, F12 and F14 fixed in v1.509.0, F13 and F15 in v1.516.0).** The sampler probes addresses a proxy
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
- [x] **B4 (2, medium, checked).** Every tab switch, page load and download
  is stored as an event with its full address for 90 days, whether or not
  Jarvis was asked anything (`extensions/chrome/src/background/index.ts:490-597`,
  `browser/extension_backend.py:1456-1528`).
- [x] **B5 (2, medium, checked).** Text typed through `web_action` into a
  sensitive field is stored in plain text in the approvals table and shown
  on the card (`computeruse/tools.py:240-245, 278`; `approvals.py:60`).
- [x] **B6 (4, medium, pass only).** A risk approval nobody could be asked
  about stays pending; approving it later lets the next identical call run
  on another page (`browser/tools.py:1245-1274, 1351-1366`).
- [x] **B7 (2, medium, pass only).** The isolated browser's site list is
  checked on the typed address only, not after a redirect or a click
  (`computeruse/tools.py:132-137`).
- [x] **B8-B14 (low; B8, B11 and B14 fixed in v1.509.0, B9 and B10 in v1.511.0, B12 and B13 in v1.516.0).** A download's name reaches the model unfenced
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
- [x] **H4 (1, medium, checked).** A helper that was stopped or cut off is
  listed "done" after ten minutes or a restart (`hosted/handoff.py:195-210`).
- [x] **H5 (3, medium, checked).** Revert in the Changes pane overwrites a
  file with no copy and no record (`hosted/panes.py:1137-1150`).
- [x] **H6 (3, medium, checked).** A failed write of the session log drops
  that batch for good (`hosted/events.py:335-354`); a lost "done" can make
  the next start carry on a reply that had finished.
- [x] **H7-H14 (low; H7 and H9 fixed in v1.511.0, H8, H13 and H14 in v1.512.0, H10 in v1.514.0, H12 in v1.515.0, H11 in v1.516.0).** A PATCH applies the tool's mode before the rest of
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
- [x] **A3 (2, medium, checked).** The account manager prints its access
  token on start, and Jarvis sends that output to `logs/iron-proxy.log`,
  which every backup carries (`iron_proxy/service.py:834-847`).
- [ ] **A4 (2, medium, checked in the bundle; NOT fixed: it needs a change in the bundled program, see "Group 15 as built").** The account manager's model
  routes need no token on this PC, so any local program can spend the
  subscriptions through it (`iron-proxy.mjs:5567-5576, 6316`). Fix is in
  the bundled program plus a switch at start.
- [x] **A5 (4, medium, checked).** A session in Ask first or Plan can hand
  work to a Pi or Antigravity helper that asks nothing
  (`hosted/handoff.py:324-327`, `hosted/service.py:308-311`); the card does
  not name the tool.
- [x] **A6 (2, medium, suspected).** A session on this PC's own login
  inherits the user's API keys; the tool may bill the key while the receipt
  says list price on the subscription (`hosted/process.py:81-86`).
- [x] **A7 (1, medium, suspected).** A Codex `turn/completed` from another
  thread ends the user's reply as done (`hosted/codex.py:1312-1315`; the
  guard exists only for `turn/started`).
- [x] **A8 (2, medium, checked).** With every account of a provider turned
  off, chat runs on this PC's own login with nothing said
  (`iron_proxy/accounts.py:889-894`).
- [x] **A9-A13 (low; A12 fixed in v1.511.0, A11 in v1.512.0, A9 and A13's three status lines in v1.514.0, A10 and A13's `proxy.json` line in v1.515.0, A13's wording line in v1.516.0).** Codex's move to another model is said without the
  model's name (`codex.py:1345-1346`); the usage check hands the vendor's
  program the daemon's whole environment, install token included
  (`iron_proxy/usage_read.py:286-299`, checked); Stop before Codex names the
  turn does nothing (`codex.py:1118-1131`); Pi keeps `--approve` after the
  user later declines the folder (`service.py:2795`); an unknown stop reason
  reads "completed" (`hosted/acp.py:464-465`), an unreported step is drawn
  "done" (`hosted/antigravity.py:1219-1224`), exception names on screen
  (`iron_proxy/service.py:793`, `client.py:199`).

### How a coding reply ends (`harness/finish.py`, `ci_fix.py`, `attempts.py`)

- [x] **D1 (1, high, checked). The pull-request auto-fix says "Pushed the
  reviewed fix" when its commit left files out or committed nothing.**
  `daemon/routes/ci_watch.py:774-803` never reads `left_out`;
  `harness/ci_fix.py:807-813` turns "nothing to commit" into `None`.
- [x] **D2 (1, high, checked). A carried-on reply can be recorded "tests
  passed" for a run that failed.** `harness/finish.py:4842` adds the cut
  part's commands to `_commands` with no entry in `_cmd_meta` (appended only
  at `2794`), so `_reusable` (`2855-2867`) pairs a command with a later
  command's result.
- [x] **D3 (1, medium, checked).** A reply resumed after a restart whose cut
  part held the finish note ends with every "until done" guard off and no
  verdict (`daemon/chat_turn.py:8839-8840`, `finish.py:2983-2985, 3321`).
- [x] **D4 (1, medium, checked).** The auto-fix reviews and pushes a fix
  whose own finish check recorded failing tests
  (`routes/ci_watch.py:594-666` never reads the line's finish record).
- [x] **D5 (4, medium, checked for the test).** A whole Maven, Gradle, tox
  or nox line counts as "the tests", so `mvn test deploy` can be run a
  second time by the check (`finish.py:885-886, 923-924`).
- [x] **D6 (4, medium, pass only).** Stop is not honoured while the check
  waits for a long test run on a GPT model (`finish.py:3839-3857`).
- [x] **D7-D11 (low; D9 fixed in v1.512.0, D7, D8 and D11 in v1.513.0, D10 in v1.516.0).** "reviewed by <member>" over a review that reached
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
- [x] **T5 (1, medium, checked).** A night task's "Done" or "Needs you" is
  read from the finish check's first answer, not from what was still open
  at the end (`harness/night.py:1083-1099`; `finish.py:4496-4518`).
- [x] **T6 (1, medium, checked).** The reason for open points is filed as
  "info" because "unmet" is not in `NOTE_KINDS` (`night.py:92-96, 543`), so
  a "Needs you" row carries no reason.
- [x] **T7 (1, low, pass only).** A night task that changed nothing and ran
  no check is announced "Done" (`night.py:1050-1051`).
- [x] **T8 (3, medium, checked for checkout and stash).** On its own branch
  the guard misses `git remote set-url`, `git reset <branch>`,
  `git checkout <file>`, `git stash pop` and a separator inside a quoted
  message (`git_guard.py:72, 414, 488, 502`).
- [x] **T9-T10 (low; T10 fixed in v1.513.0, T9 in v1.516.0).** `process_start` works out its environment on the
  event loop (`harness/process.py:676`, checked); "Night tasks run now,
  until 7:00 AM" although a started task runs past it
  (`harness/night_window.py:104-105`).

### A coding session's stored sessions, compaction and page browser

- [x] **S1 (2, medium, checked).** The one-shot screenshot browser has no
  address guard, and the agent's browser judges only top-level pages, never
  frames (`harness/page_shot.py:110-130`, `page_browser.py:1146-1163`): a
  local page can show the model another local app's page.
- [x] **S2 (1, medium, checked).** A failed transcript write at a reply's
  end is swallowed (`daemon/routes/build.py:2017-2026`); the reply is gone
  from the session and the kept copy is deleted (`2328-2330`).
- [x] **S3 (1, medium, checked on the daemon side).** A file in the folder
  (`.ironjarvis/harness/finish.toml`) can turn the finish check off and
  nothing on screen says so (`harness/components.py:545-557`,
  `finish.py:4787-4790`).
- [x] **S4 (4, medium, checked in code, not run).** One search pattern can
  freeze the app: the search's time limit is read only between files
  (`harness/search.py:188-191, 233-235`).
- [x] **S5 (1, medium, pass only).** A reply's failure marks never reach the
  next model or the summarizer (`harness/sessions.py:998-1022`,
  `compact.py:282-322`).
- [x] **S6-S12 (low; S8, S11 and S12 fixed in v1.510.0, S6 in v1.511.0, S7, S9 and S10 in v1.515.0).** Typing with "submit" sends a form with no risky-click
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

- [x] **L1 (3, medium, checked).** Both phone doors run a turn without its
  chat id (`comm/inbound.py:1246`, `daemon/routes/comm.py:823`): the chat's
  own usage total stays at zero and its notes are keyed with every other
  phone conversation's (`memory/origin.py:47`).
- [x] **L2 (2, medium, checked).** A reply typed at the desk into a phone
  conversation has no project and runs at full trust
  (`routes/comm.py:823`, against `inbound.py:1245-1257`).
- [x] **L3 (8, medium, checked).** The automatic summary of a long chat is a
  paid model call that is on no receipt (`daemon/app.py:3409` drops the
  usage; `chat_turn.py:373-410`).
- [x] **L4 (1, medium, pass only).** A turn that fails after its tools ran
  says only that it failed; on the phone that is the whole message
  (`chat_turn.py:14547-14583`, `routes/chat.py:8093-8119`).
- [x] **L5 (1, low, checked).** On a reply that ends in a workflow card the
  plain lane drops four honesty notes the streaming lane adds
  (`chat_turn.py:14716-14836` against `routes/chat.py:8457-8484`).
- [x] **L6 (3, low, checked).** A cancelled plain-lane turn leaves no usage
  row and no safety scan (`chat_turn.py:14547`; the streaming lane has
  `except BaseException` at `routes/chat.py:8120`).
- [x] **L7 (4, low, checked).** The plain lane does not honour "Ask before
  every action" (`chat_turn.py:13462, 14041`); only a direct caller of the
  route reaches it today.
- [x] **L8 (7, low, pass only).** Engine words in replies: "stopped after 7
  tool rounds", raw tool names, a Python exception as a step's output.
- [x] **L9 (4, low, checked).** The language rewrite has no time limit in
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

### Group 13 as built (v1.513.0)

Fixed: D1, D2, D3, D4, D7, D8, D11, T5, T6, T7, T10, S3, S5, L5.

Where it differs from the plan, or went further:

- **One reading of a finish record** now lives in `harness/night.py` and
  includes the tests "as last seen". The implementer of T5 found that the
  daemon read only the FIRST test run: a fix that made the tests pass
  still said "Needs you: its tests failed", and a later failure said
  "Done". The lead added `finish_tests` and moved the night's notes and the
  auto-fix's words to it.
- **T5's case B in the review was half right**: with a failed first run the
  old code already said "Needs you"; the case that wrongly read "Done" was
  a point blocked in a later round with no tests.
- **S3**: `enabled` can only come from the folder's own file, so every
  "off" is the folder's.
- **S5** also notes an account limit, and follow-up suggestions stay quiet
  after a failed reply that wrote nothing (lead's review).
- **D7**: the benchmark report and the fix-request reader were taught the
  new records too (lead's review).

Left open:

- A night task in a folder that turned the check off reads "Done" when it
  changed files (it is read as "no check ran", like a folder with no
  tests). The reply itself says "Not checked".
- In a carried reply, points that come from a written description file are
  re-read only at the end.
- A commit the auto-fix made with files left out stays on the branch, not
  pushed and not undone; the message says so.

### Group 14 as built (v1.514.0)

Fixed: H4, H10, F2, F4, F9, A7, A9 and the three status lines of A13.

Where it differs from the plan:

- **E7 (F2) was wired**, in the orchestrator where a run's model is chosen
  (the router cannot tell "no model of its own": the default is filled in
  before it). Checked read-only on the owner's PC: the setting is off there
  and the default model is already the owner's own server.
- **F4 sends no cost.** This app shows no per-run cost to its own user, so
  none is sent to another computer either.
- **A7** keeps a Codex sub-agent's steps in the reply (lead's review: the
  implementer had dropped them with its words, which would have left a
  helper's file changes out of the receipt).
- **H4's second half was not a defect**: `check_again` already stops on the
  row's `handed_from`, which survives a restart.

Left open:

- With "Send coding work to a model server" on, a run started with no agent
  type is a builder and goes to the server.
- No cost comes home with an answer from another computer.
- A Codex sub-agent thread's token use is still added to the reply.
- A run cut off by a restart comes home as "failed"; there is no separate
  word for "cut off".

### Group 15 as built (v1.515.0)

Fixed: A3, A6 (Claude Code), A8, A10, B4, B5, B7, S1, S7, S9, S10, L1, L2
(its project half), H12, the `proxy.json` line of A13, and the Settings
model-server slot left open by group 9.

Not fixed, on purpose:

- **A4 (the account manager's model routes need no token on this PC).** The
  program is vendored from `RealDealCPA-VR/Iron-Proxy` with a pinned
  checksum, and its own start code decides this from the listen address
  alone. Decision E9: a change to that program is the owner's to release.
  What it needs there: require the token on `/v1/*` whatever the address
  (or a `--require-auth` switch), refuse a Host header that is not
  loopback, and stop printing the token on start. Iron Jarvis then passes
  the switch in `iron_proxy/service._spawn` and re-vendors with
  `scripts/vendor_iron_proxy.py`.
- **L2's trust half.** `docs/rules/tools.md` says a desk reply into a phone
  conversation stays at full trust on purpose (the user typed it). The
  implementer stopped on that rule; the lead agrees.
- **A6 for Codex.** Its start answers do not say which credential it uses.

Where it went further than the plan (lead's review):

- The run trace also kept typed text, and a click's result carried the full
  download link into the ledger: both closed with B5 and B4.
- Chat's own Claude Code child inherited the daemon's install token, like
  the usage check: closed with A10.
- "Fetch available models" sent a key over plain http to an outside
  address: closed with the Settings slot.
- The Session tab did not draw the new "cannot tell where" sentence:
  `lib/codePanes.ts` now does.

Left open:

- Rows written by older versions are not rewritten: browser events with
  full addresses (they age out within 90 days) and approval rows holding
  typed text. A one-time clean-up is the owner's call.
- The one-shot screenshot now needs Playwright's driver and was not driven
  on the packaged build. What would show a problem: `page_screenshot` of an
  address answering "could not start the browser" in a coding session.
- The Ollama slot's address is not judged; the CLI `ironjarvis connect`
  saves a key without the plain-http check.
- The secret mask also drops a proposed lesson that merely contains words
  like `token: str`.

**v1.514.0 was never published.** Its release run failed
`test_clients_lanes_v1499::test_a_chat_code_reply_keeps_its_client_question_and_reads_its_answer`
on the first try and on the retry (shard 6), although the branch run of the
same commit had passed. What is known: it is the only test that takes the
real "Is X a client?" question through a lane, so it raced the lane's real
one-second wait; v1.514.0's new test files moved the OCR tests into its
shard for the first time (computed with `scripts/ci_shards.py` for the last
five releases). What is not known: which step ran out of time, because a
skipped question left nothing in the log. Not reproduced here (6 of 6 beside
the OCR tests, 3 of 3 with a leftover event loop). Done in v1.515.0: the
test gets a wait a busy runner cannot beat (the wait itself stays pinned
with explicit deadlines in `test_clients_notice_v1499`), and every skipped
question now says why at INFO (`workmap/clients/store.question_for_turn`,
`chat_turn.client_question_for`), so a next time explains itself.

### Group 16 as built (v1.516.0)

Fixed: S4, L9, L3, L8, T9, the git guard's file checks (left open by group
11), B12, B13, F8, F13 (decision E8), F15, H11, D10, the wording line of
A13, and the Browser page's hidden-text wording.

Where the lead changed the implementers' work:

- **S4 was safe but slow.** The implementer sent every pattern with a
  repeat or a group to a separate program. Measured here: 0.5 to 0.9 s per
  search against 0.03 s, and most patterns a model writes carry a repeat.
  The lead added a middle path (`in_process_line_limit`): a pattern with no
  group and at most two repeats is matched in the daemon as long as no line
  is long enough to cost too much, with the time limit read between lines;
  the first longer line sends the whole search to the program. Ordinary
  searches are back to 0.03 s.
- **A glob could freeze the app the same way** (the implementer measured
  5.3 s for eight `**/` in a row and left it): a run of `**/` is now one,
  and too many are refused in words.
- **L8's note said "rounds of steps".** VOCABULARY.md never shows "round":
  it now says "model calls".
- **F13 logged on every poll.** The Model servers page reads every two
  seconds; the same failure is now logged once per five minutes.
- **A probe failure with no phrase of its own** showed the library's text on
  a server's row: now "could not be read" (or "certificate problem").
- **B12 kept one fact for the model**: "The page did not answer in time."

Left open:

- **The search program was not run on the packaged build.** Its start is
  proven from source and by the fast-path test only. Since the middle path,
  only a pattern with a group, a multiline search, three or more repeats or
  a very long line needs it. What would show a problem: such a search in a
  coding session answers "the search could not run: its search program did
  not start" (or "stopped without an answer").
- The agent runtime's own summary of a long run is still on no receipt.
- The Connections page and the old settings form show the new Model
  servers sentence after a colon and not the Details.
- Chat's own approval card still shows `***REDACTED***` for hidden typed
  text (pinned by `approval-preview-v1348.test.tsx`).
- `browser/panel.py` has other error texts in sentences (skills, recording,
  voice) and `browser/screenshot.py` one; `chat_turn.py`'s finish-check
  command runner returns a class name as its reason.
- `finish.py` compiles an ignore file's lines without the new glob counts.
- "rounds of fixes" is still said by the CI watch.

## Second fix plan: closing state (2026-11-16)

All eight groups (9 to 16) are released: v1.509.0, v1.510.0, v1.511.0,
v1.512.0, v1.513.0, v1.515.0 (which also carries v1.514.0, never published)
and v1.516.0. Of the 98 findings, one is deliberately not fixed (A4: it
needs a change in the bundled account manager, the owner's to release) and
the "Not closed" and "Left open" lines under each group say what else
remains.

For the owner to decide:

1. Whether an "Always for this schedule" approval may cover a tool with any
   details (today: one exact step, 30 days).
2. Changing the GitHub workflow so a retried test prints its first failure.
   Three retried or failed tests in this plan could not be explained from
   the log for want of it.
3. A one-time clean-up of rows older versions wrote: browser events with
   full addresses (they age out in 90 days) and approval rows holding typed
   text.
4. The A4 change in the Iron-Proxy repository (token on every route, a
   loopback Host check, no token printed on start).
5. Whether everything git but a commit should ask every time in your own
   folder (decision E3 as built is wider than written).
6. Whether a night task that only answers a question should read "Done"
   rather than "Needs you".
7. Cost is not sent with an answer from another computer.
8. Trying the two things that were tested with stand-ins only on the
   installed app: starting and stopping a real coding tool (group 12), and a
   search with a group in its pattern in a coding session (group 16).

## Third review (2026-11-16, at v1.516.0)

Ten read-only passes over everything the second review listed under "Still
not read" (the bundled account manager apart). Nothing was run and nothing
was changed; nothing here is fixed. Whether to fix it is the owner's call.

**Checked** means the lead read the lines and found no guard; **pass only**
means the pass reported it as confirmed and the lead has not re-read it;
**suspected** means a step on the path was not verified (the entry says
which). Every medium finding was re-read by the lead; most low ones are pass
only. The number in brackets is the north-star behaviour it breaks. Line
numbers are as of v1.516.0.

90 findings. Severity counts: 1 high, 35 medium, 54 low. By area: sidebar
14, the add-on's daemon side 6, the tools door and code host 8,
coding-session routes 14, attempts at night 8, permissions and the command
guard 10, saving a chat reply 10, terminals 20.

**For the owner: the findings that matter most**

1. **A held-back app tool can run in chat with no card (PG1, the one high
   finding).** When a connected app's update brings a new tool that can
   change things, Iron Jarvis is meant to ask before that tool runs until
   you trust it. With the app switched on for a chat, it runs unasked.
   Nearby, and medium: a command such as
   `del /q *.tmp >nul 2>&1; python run.py` gets past the guard that should
   refuse it and deletes `run.py` (PG6), and a scheduled workflow can add
   an outgoing webhook with nobody asked (PG2).
2. **A chat reply's only home is the page that shows it (CS1 to CS5).** The
   daemon runs the reply and keeps a record of it for seven days, but only a
   Chat page writes the reply into the chat. So: a reply that finishes with
   no page watching is not in the chat or in history search until that chat
   is opened, and is gone for good if that takes more than a week; coming
   back to Chat mid-reply can save the reply twice; opening such a chat
   while another chat is answering shows "failed, Retry" over a reply that
   ran; and after 60 seconds of silence the page says the turn "was stopped"
   while the daemon carries on with it.
3. **Stop is not remembered (RB1, CS4, SB2).** A Stop is a note in memory
   only. If Iron Jarvis closes before a stopped coding reply has wound
   down, the next start carries that reply on by itself and runs its steps.
   A Stop pressed right after Send can arrive before the daemon knows the
   turn, and the turn then runs to its end with "Stopped." on screen. In the
   browser sidebar, Stop does not end a turn waiting at a risk card, and
   Allow pressed afterwards still does the action.
4. **A night task tried on several models skips the night's own rules (NA1
   to NA3).** It reads "Done" in the morning whatever its attempts did (all
   failed, all cut off), shows no usage on the Last night row, and the
   pay-per-use limit is not applied to a pick that is the default model.

Also worth knowing:

- Text printed in a terminal reaches a model with no fence in the pane's
  "ask AI" and "make a workflow from this terminal", so a file or a
  program's output can steer the command the pane offers or the steps of a
  saved workflow (TM1).
- The Continue strip says "Continue on X", but the daemon is never told
  which account and picks one itself (TM4).
- After any restart of Iron Jarvis (every update), a coding tool that was
  kept running loses Iron Jarvis's tools until it is started again (MC3).
- Undo is refused for a renamed, moved or copied download, although its
  own answer says it can be reversed (PG4).

What the review found sound is listed under each area ("Holds"). In short:
the doors check who is calling, tokens stay out of files, logs and command
lines, page text stays inside its fence, and nothing a terminal prints
makes Jarvis type or change account by itself.

**The shared causes**

| Cause | Findings |
|---|---|
| The page, not the daemon, writes the reply into its chat | CS1, CS2, CS3, CS5, CS10, SB1, SB3 |
| A Stop lives in memory only and is not tied to the start or to a restart | RB1, RB5, CS4, SB2, SB5, SB13 |
| The screen names one thing and the daemon does another, or is never told | TM4, TM5, RB3, SB4, SB6, MC1, MC2, BR1, PG5 |
| A second way in that the gate does not judge | PG1, PG2, PG3, PG6, PG7, PG8, PG9 |
| "Try on several models" at night went around the night's checks | NA1, NA2, NA3, NA7 |
| Terminal output handed to a model with no fence | TM1, TM14 |
| A Python exception's name, or engine words, still reach the screen or another tool | RB12, RB13, MC8, NA6, TM7, TM8, PG4, PG10 |

### The browser sidebar's own code (`extensions/chrome/src/sidepanel/sidepanel.ts`, `browser/panel.py`)

Both files were read whole.

- [ ] **SB1 (4, medium, checked). A sidebar reply keeps talking to the
  connection it started on.** If the add-on's connection drops and comes
  back mid-reply, the rest of the answer, any approval card and the end of
  the turn go nowhere. The panel shows "running"; a turn that reaches an
  ordinary card waits with no limit, and every later Send is refused with
  "still working on the last message". `browser/panel.py:1267, 1603-1700`
  (the turn keeps `conn`), `802-828`; `open` on the new connection does not
  re-bind the turn or show the waiting card again (`709-736`).
- [x] **SB2 (4, medium, checked). Stop or Esc does not end a sidebar turn
  that is waiting at a risk card** (payment, password, delete). It stays
  "running" for up to five minutes, and Allow pressed after Stop still does
  the action. `panel.py:841-854` waits with no look at Stop; the Chat
  page's twin checks Stop every second
  (`daemon/routes/chat.py:5543-5565`).
- [x] **SB3 (3, medium, checked; the code calls it deliberate). The sidebar
  conversation is kept in memory only.** A restart of Iron Jarvis (every
  update) forgets it while the panel still shows it, so "now the second
  one" is answered with no context and nothing says so; once the panel is
  closed the conversation is in no chat list. `panel.py:640-655,
  1329-1354`; `sidepanel.ts:409-416`.
- [x] **SB4 (4, medium, checked). With the sidebar open in two browser
  windows, turning "Auto-allow" off in one is undone at the next tab
  switch.** The other window still holds "on" and sends it again; the
  daemon takes whichever arrives last. `sidepanel.ts:1028-1038, 1621,
  1627-1629` (read once, never re-read); `panel.py:714-715`.
- [x] **SB5 (1, medium, checked). Stop prints "Stop requested" and Allow or
  Deny hide the card without checking that the press reached Iron Jarvis.**
  With the connection down nothing was stopped or answered. Send, Steer and
  Reset do check. `sidepanel.ts:979, 1008, 1019, 1081`.
- [x] **SB6 (4, low, checked).** A risk card in the sidebar still offers
  "Allow all page actions": one press allows that risky action and turns
  Auto-allow on for every tab. The daemon marks the card (`floor`,
  `panel.py:837`); the panel never reads the mark.
- [ ] **SB7 to SB14 (low, pass only).** Stopping a dictation makes the
  *Done so far: SB11 (v1.520.0); SB13 (v1.522.0); SB8, SB9 (v1.524.0).*
  add-on's whole connection wait for the transcription, so a page action
  another chat is running can time out (`panel.py:1127-1175`); any error
  that is not about voice ends the turn on screen while it still runs
  (`sidepanel.ts:741-763`); a click whose answer was lost reads "Could not
  click …" followed by a sentence written for the model
  (`panel.py:1817-1825`); a Send the daemon refuses has already emptied the
  box and drawn a sent bubble (`sidepanel.ts:891, 912-948`); the add-on's
  worker passes on a sidebar action from any sender inside the add-on, a
  page script included (`extensions/chrome/src/background/index.ts:608-671`);
  "Start new chats local only" does not cover the sidebar or its dictation
  (`panel.py:1629-1654`); with two windows, closing one stops the other's
  reply, which then reads as finished (`sidepanel.ts:1586-1590`); the
  microphone stays open after a dictation the daemon ended at its size
  limit, and the final transcript wipes what was typed meanwhile
  (`sidepanel.ts:1265-1284`).

Holds: the panel never draws model or page text as HTML; a connection that
is not paired cannot send sidebar actions; the panel can answer only cards
it was shown; a sidebar turn gets only the browser tools; what the user
read is kept before the turn's end is announced; dictation audio goes only
to the daemon.

### The add-on's daemon side (`browser/snapshot.py`, `screenshot.py`, `pairing.py`, `panetokens.py`)

All four were read whole.

- [x] **BR1 (1, medium, checked). With Browser access off, Forget says
  "Forgotten" but keeps that browser's waiting connection.** When Browser
  access is switched back on, the forgotten browser is connected and usable
  again with no pairing. `browser/service.py:1345-1360` closes only the
  live connection (there is none while access is off);
  `daemon/routes/browser.py:1027-1041` promotes the waiting one without
  checking its token again. A second Forget does close it.
- [x] **BR2 (1, medium, suspected).** In a run held to a model that cannot
  take pictures, a screenshot with a question can come back with a
  "description" from a model that never saw the picture:
  `browser/screenshot.py:508-610` lacks the check `tools/images.py:253-271`
  has. Not verified: that such a run is given the screenshot tool, and what
  each adapter does with a picture it cannot take.
- [x] **BR3 to BR6 (low; BR3 and BR4 pass only, BR5 and BR6 suspected).** A
  *Done so far: BR6 (v1.520.0); BR4, BR5 (v1.523.0); BR3 (v1.524.0).*
  Pair press that lands while Browser access is off makes that browser the
  live one anyway (`browser/extension_backend.py:818-846`; the card does not
  offer Pair then); the screenshot tool promises "the result names the
  provider that saw it" and the result never does
  (`screenshot.py:581-610, 415-430`); a screenshot is of whichever tab is
  on screen at that instant but is filed under the tab asked for
  (`extensions/chrome/src/background/tabs.ts:312-348`); the shared scan for
  hidden instructions slows with the square of a long run of blank lines
  (`computeruse/safety.py:59-64`; not reachable through the add-on, other
  callers not checked).

Holds: the pairing token is random, stored only as a hash, compared safely
and never logged (a token in a logged address is masked,
`core/logging.py:248-336`); a web page cannot open the add-on's connection;
page text reaches a model only inside the fence; no field value passes the
daemon; a terminal's token lives in memory only and dies with its terminal.

### The door that gives coding tools Jarvis's tools, and the code host (`mcpserver/`, `codehost.py`)

All of both were read whole.

- [x] **MC1 (2, medium, checked on the restore side). A Memory, Browser or
  Hand off box turned OFF on a Build › Code session can be ON again after
  Iron Jarvis is killed and restarted.** The restore takes the boxes from
  the state kept beside the running tool, not from the session's record
  (`hosted/adopt.py:439-444`), and turning a box off does not refresh that
  kept state (`hosted/service.py:2402-2423`, pass only). The chip shows off
  while the tool is still served.
- [x] **MC2 (4, medium, checked on the daemon side). When a coding tool
  asks to click or type in the user's browser through this door, the only
  card is a bell row "An agent run wants to …".** It does not say which
  session asks, on which page, or what is clicked or typed, and it lapses
  after 45 seconds (`mcpserver/server.py:692-733`; the bell's words pass
  only, `dashboard/components/NotificationBell.tsx:1857-1945`). On the same
  card: "Allow for this run" is offered but counts once (`678-683`), and
  two of the card's answers are read as "nobody answered" (`727-733`).
- [ ] **MC3 (4, medium, checked for the bridge; suspected for tools that
  connect by themselves). After any restart of Iron Jarvis, or an hour
  without a call, a running coding tool loses Jarvis's tools until it is
  started again.** The door forgets the tool's session and answers every
  later call "no MCP session" (`mcpserver/server.py:917-922`,
  `session.py:59, 259-266`); the bridge never starts a new one
  (`stdio_shim.py:226-244`). Not verified: what each tool's own client does
  with that answer.
- [ ] **MC4 to MC8 (low; MC4 and MC7 suspected, the rest pass only).**
  *Done so far: MC5 (v1.520.0); MC6 (v1.524.0).*
  Stopping a tool that left a program holding its output open can hang the
  stop for 60 seconds (`codehost.py:547-559`; the daemon's own copy has the
  guard); a call through the door has no time limit and is not re-checked
  after its card, so a card left open when the session is stopped can still
  be allowed (`server.py:785-825`); the Browser box says clicks ask first
  and does not say that what the tool reads from any open tab goes to its
  vendor with no card (`dashboard/lib/buildApp.ts:684`); on a PC with a
  proxy set, the bridge may send its call to the proxy
  (`stdio_shim.py:94-98`); a crash while serving a call is answered to the
  tool as the exception's name and text
  (`daemon/routes/mcpserver.py:250-261`).

Holds: the door takes only a terminal's or a session's own token, never the
install token; the token is never on disk or a command line; a call goes
through the same tool gate as chat with a card for anything that asks, and
only the browser tools, five read-only memory tools and four hand-off tools
are served, so a session in Plan or Ask first reaches no write, send or
shell tool through it; the code host listens on this PC only and starts and
stops tools as a tree.

### Coding-session routes (`daemon/routes/build.py`: the reply watch, the save, the resume pass, the session routes)

The whole file was read, in two halves.

- [x] **RB1 (4, medium, checked; found by both halves). A reply the user
  stopped can carry on by itself at the next start.** If Iron Jarvis closes
  after Stop but before the reply has wound down (a running command gets up
  to three seconds), the reply is recorded "cut off by a restart", its kept
  work stays, and the resume pass has no check for "the user pressed Stop"
  (`build.py:2439-2441, 2468-2471, 4102-4137`). Stop is never written down
  (`core/turns.py:162`).
- [x] **RB2 (4, medium, suspected). A reply that finished normally can get
  an "account limit" card, arm a wait, and later start a hidden "carry on"
  turn on work that was done.** A limit note left by a side call (a helper,
  the reviewer, the summary) is read at any end of the reply, a good one
  included (`build.py:2380-2388`), and the limit branch comes before the
  "it finished" branch (`1884-1901`). Not verified: that a refused side
  call lets the main reply end normally.
- [x] **RB3 (4, medium, checked on the route; the lane pass only). Changing
  the permission level, or switching Plan on, while a reply runs is shown
  as applied at once, but the running reply keeps its old level.** A user
  going from "Don't ask" to "Ask before every action" to rein a reply in
  still gets commands run unasked until it ends, and nothing says so
  (`build.py:4816-4880`; the level is read once, `routes/chat.py:5063`).
- [-] **RB4 (2, medium, suspected). "Branch from here" does not copy the
  *Not a defect: no coding session ever gets such a hold. Its one writer runs only after an app kept on the user's own models was used, and that app refuses outside chat. Pinned by two tests; no code changed.*
  hold that keeps a session Local only after it read an app kept on the
  user's own models.** In the branch Local only can be switched off, and
  the copied conversation then goes to a cloud model
  (`build.py:5215-5226`; the hold is `providers/egress.py:789`). Not
  verified: that a coding session's reply gets such a hold written.
- [x] **RB5 (4, low, checked for the code; a narrow window).** A reply that
  had finished and was saved is carried on again at the next start if the
  app stops between the save and the removal of its kept copy
  (`build.py:2432-2441, 2468-2471`); the resume pass never checks that the
  session already holds the finished reply.
- [ ] **RB6 to RB14 (low, pass only; RB12 by two passes).** A "Resume when
  *Done so far: RB9 (v1.521.0); RB8 (v1.522.0); RB10, RB14 (v1.524.0).*
  ready" wait whose carry-on is refused for a lasting reason (folder gone,
  model no longer usable) is tried every 60 seconds for ever and never said
  (`build.py:3996-4002`); a failed write of the question at a reply's start
  answers "no such coding session" after the wait and its card were already
  removed (`3693-3702, 3737-3741`); Delete does not hold the session while
  it moves folders, so a reply Jarvis starts by itself in that moment
  begins in a session being deleted (`4931-4994`); "It will be put back
  when Iron Jarvis next starts" is false when the next start is more than
  seven days away (`2058-2060`, `daemon/durable_turns.py:1282-1283`); the
  bell, the tray and the phone say "Your coding session finished" for a
  reply cut at its step limit, its limit or its length (`1991-1993,
  2502-2514`); while the session cannot be written, every later piece of
  the reply tries again and logs a full error, and an unsaved "for this
  conversation" approval is dropped with no word (`2411-2412, 1689-1705`);
  a reply that breaks outside its own error handling puts the Python
  exception in the live stream and the stored record
  (`daemon/durable_turns.py:933-936`); when the folder check itself fails
  the user reads "This folder cannot be used: that folder cannot be used:
  PermissionError" (`harness/sessions.py:1775-1776`); a session's reply
  limit does not go with a branch made from it (`build.py:5215-5226`).

Holds: the reply and its limit card are written before the end is
announced; a failed write is retried, said, and put back at the next start;
the resume pass cannot run twice for one reply and never takes one that was
waiting on the user; Rewind is all or nothing and says truthfully what it
put back; a session route checks every field before it changes anything;
blocking work is off the event loop.

### Several attempts, as the night uses them (`harness/attempts.py`)

Read whole, with the night's call sites.

- [x] **NA1 (1, medium, checked). A night task tried on several models is
  announced "Done" whatever its attempts did.** All failed, all cut at a
  limit, or stopped by the night's own backstop: the morning notice still
  counts it under done with "open the session to keep one". The attempts'
  ending carries only the summary and the card (`attempts.py:1859`), and
  the night reads only fields that ending never has
  (`harness/night.py:1024-1056`); nothing in the night's code reads the
  card.
- [x] **NA2 (8, medium, checked). The Last night row of such a task shows
  no usage.** Up to three models worked for up to the task's whole time
  limit; `harness/night_morning.py:266-276` adds only the reply's own usage
  and its helpers', and each attempt's usage sits on the card. Not checked:
  whether the app-wide usage record still counts them.
- [x] **NA3 (2, medium, checked for the default-model gap; the dollar limit
  pass only). At night the pay-per-use limit is not applied to a pick that
  is the default model**, even at $0 or already spent
  (`daemon/routes/night.py:894-899` skips the night's check for such a
  task; `attempts.py:826` skips its own for the session's model, which at
  night is the default). No attempt gets the night's "what is left of the
  limit" dollar limit either (`harness/night.py:815-819`).
- [x] **NA4 (2, medium, suspected).** An attempt can start with no account
  chosen when the accounts list could not be read: the refusal an ordinary
  reply gets is swallowed (`daemon/routes/build_attempts.py:192-195`,
  checked). Not verified: which login then answers.
- [x] **NA5 (2, medium, suspected for what is pushed). In a session that
  works in its folder directly, "Try on…" commits every file not yet
  committed and not ignored, new files included, onto each attempt's
  branch with no screen for secrets or size** (`attempts.py:928-981`,
  checked). The night's own commit is screened; this one is not. After
  Keep, a later Push sends it. Not verified: what the Changes view shows
  before that Push.
- [x] **NA6 to NA8 (low; NA6 and NA7 pass only, NA8 suspected).** A night
  *Done so far: NA8 (v1.522.0); NA6, NA7 (v1.523.0).*
  task that fails to start keeps the note "It could not start (KeyError)."
  (`routes/night.py:975-977`); "Runs at once" counts such a task as one
  although it runs three replies at once (`routes/night.py:1036-1047`);
  deleting a session in the seconds after its attempts end can leave their
  folders and branches with no session that knows them (nothing is
  deleted; `routes/build.py:4936`).

Holds: each attempt works on its own branch in its own folder; clean-up
only moves folders to the trash; nobody picks a winner at night; Keep
refuses rather than overwrite; Stop, Cancel and Emergency stop reach every
attempt and its programs; a restart never leaves an attempt "running".

### Permissions and the command guard (`tools/permissions.py`, `sandbox/cmd_guard.py`, and their callers)

Both files were read whole, and each caller along the path of a call.
`cmd_guard.py` is only the guard for `;` in Windows cmd; whether a command
is dangerous is decided in `core/auto_approve.py`, read in part.

- [x] **PG1 (4, high, checked for the gate; that such a tool is armed with
  its app is pass only; the pass said medium, raised by the lead). A
  held-back app tool runs in chat with no card whenever its app is switched
  on for that chat.** A tool that arrives in a later update of a connected
  app and can change things is meant to ask until the user trusts it.
  Switching the app on arms every one of its tools by name
  (`daemon/chat_turn.py:5345`), every armed name becomes a grant
  (`daemon/routes/chat.py:5309-5314, 5342`), the card is skipped for a
  granted name (`6869-6873`), and the gate's one exception for a held-back
  tool is "its own name is in the grant" (`tools/permissions.py:266-268`).
  Both chat lanes. The test for the hold-back calls the gate directly,
  never a chat with the app switched on.
- [x] **PG2 (2, medium, checked). A workflow's tool step skips the tool's
  own "ask every time" check.** A scheduled workflow can add an outgoing
  webhook (Jarvis's events sent to another address from then on) with
  nobody asked: `workflows/engine.py:1497-1542` asks only the tier, and the
  tool's own check (`webhooks/tools.py:53`) is called by the chat lanes and
  agent runs only. An agent can write such a workflow unasked
  (`schedule_create` and `workflow_create` do not ask,
  `core/config.py:242-248`). Not verified: which agents are given those
  tools.
- [x] **PG3 (2, medium, checked; `repl` suspected). After a run reads
  flagged text it loses the web tools and its shell is isolated, but
  `run_code` and `code_run` still run model-written code on this PC with
  the network open.** Neither is on the lists in `core/trust.py`, and only
  the tool named `shell` is isolated (`agents/runtime.py:5102-5108`,
  `daemon/chat_turn.py:4358-4369`). It needs `run_code` already allowed
  for the run.
- [x] **PG4 (3, medium, checked). Undo is refused for a change made by any
  tool set to "Ask first".** On a default install that is every rename,
  move or copy of a download, whose own answer says it can be reversed from
  the receipt. `daemon/routes/undo.py:267-279` asks the gate with nobody to
  ask, and the refusal shows engine words ("grant it for one task with
  allow_tools …", `tools/permissions.py:309-315`). A user who sets file
  edits to "Ask first" loses every edit's Undo, and Rewind of a coding
  session then refuses (`harness/rewind.py:571-583`, pass only).
- [x] **PG5 (1, medium, checked on the daemon side). Setting web search or
  web fetch to "Ask first" in Settings is saved and shown, and both still
  run without asking.** `tools/permissions.py:206-212` turns "ask" into
  "allow" for these two on purpose (so unattended runs can search); the
  setting still offers the choice.
- [x] **PG6 (3, medium, checked by reading; not run). The `;` guard misses
  a command where `2>&1` comes before the `;`.**
  `del /q *.tmp >nul 2>&1; python run.py` is not refused, and Windows cmd
  hands `del` the later words, so `run.py` is deleted.
  `sandbox/cmd_guard.py:153-157` splits the line at the `&` inside `2>&1`.
  A command line has no Undo.
- [x] **PG7 (4, medium, checked for the match; the card suspected).
  "Always allow exactly this" ignores every argument whose name starts
  with an underscore** (`core/grants.py:102-120`). Meant for two of the
  app's own names, it also drops an app tool's `_id`, so an exact approval
  for one record covers every other. Not verified: that the card offers
  Always for such a tool.
- [x] **PG8 to PG10 (low; PG8 suspected, the rest pass only).** A line an
  agent types into a Build terminal meets none of the shell tool's checks
  (no git guard, no `;` guard), and a terminal it opens can be in any
  folder (`tools/pane_tools.py:202-210, 273-274`; both ask first);
  `if exist out.txt del out.txt; python gen.py` is not refused either
  (`cmd_guard.py:344-399`; its own notes list this limit); the refusal
  with nobody to ask advises a Settings change that Settings refuses for
  the shell and its like (`permissions.py:309-315`).

Holds: an unknown tool or level means ask, ask with nobody to ask means
refuse, and a deny is decided before any approval is read; a tool on the
floor cannot be raised to allow by an agent or by Settings; every refusal
is recorded; only the tool gate runs a tool; a helper's approvals come from
its parent, never from the model; the shell tool gives the guard the exact
text it runs.

### How a reply is saved to its chat, and `daemon/durable_turns.py`

`durable_turns.py` was read whole; the two chat lanes and the Chat page
only along the save path.

- [x] **CS1 (1, medium, checked). Opening a chat whose reply the daemon ran
  or is still running, while another chat's reply is on screen, shows it as
  a failed turn with Retry.** Retry asks the question again, so its steps
  run a second time, and the first reply is never shown.
  `dashboard/app/chat/page.tsx:6707` reads "busy" as it was when the click
  happened (the other chat's).
- [x] **CS2 (3, medium, checked for the merge; the two-pages path pass
  only). Ask, go to another page of the app, come back to Chat while the
  reply is still being written: the reply is saved twice.** The same with
  two windows on one chat. The first page never ends its stream; both save
  at the end, and the second save's merge adds its copy after the first
  (`page.tsx:6505-6516`); nothing matches the two by their turn. Suspected:
  a reply that hands off to an agent starts the agent run twice.
- [x] **CS3 (1, medium, checked). When the page hears nothing for 60
  seconds it says the turn "was stopped … press Retry", but the daemon's
  turn keeps running, steps included.** The cut text is saved as the reply,
  so the finished reply never reaches the chat, and Retry starts a second
  turn beside the first (`dashboard/lib/useChatStream.ts:1042-1061,
  1214-1236`; `page.tsx:9483-9548` sends no Stop and does not rejoin).
- [x] **CS4 (4, medium, checked). A Stop pressed right after Send can land
  before the daemon knows the turn.** The daemon answers "no such turn",
  the page ignores that, shows and saves "Stopped.", and the turn runs to
  its end with nobody watching, steps included
  (`daemon/routes/chat.py:3795-3840`: up to four waits before the turn is
  registered; `page.tsx:11037-11065`).
- [x] **CS5 (3, medium, checked). A reply that finishes while no page
  follows it is only in the turn's record, not in its chat.** It is not in
  history search until someone opens that chat, and a start more than seven
  days later deletes the record: the chat then ends on the question with
  Retry and the reply's words are gone (the record of its steps stays).
  `durable_turns.py:894-956` writes the record only; `1282-1283` deletes by
  age with no check that the reply reached its chat. `docs/rules/chat.md`
  says a line the daemon can write must be written by the daemon.
- [ ] **CS6 to CS10 (low; CS6 and CS10 partly suspected, the rest pass
  *Done so far: CS6, CS7, CS10 (v1.521.0); CS8 (v1.524.0).*
  only).** The first reply of every new chat runs with no chat id, so its
  cost is missing from that chat's usage line (`page.tsx:8666, 9240-9252`;
  `routes/chat.py:5710`); deleting a chat leaves its turn records (each
  reply's text, thinking, and tool inputs and results) in the database and
  in backups for up to seven days and until the next start
  (`routes/chat.py:333-361`); a turn's usage is written once, at the end,
  on the event loop, and a failed write is dropped with no log line
  (`daemon/chat_turn.py:11440-11464`); every start loads every stored turn
  record whole into memory on the event loop
  (`durable_turns.py:1269-1284`); a question whose own turn never started
  can be "answered" on reopening by replaying the turn before it
  (`dashboard/lib/turnRejoin.ts:11, 33-38`).

Holds: a second send of a turn that is running follows it and never starts
it twice; Stop reaches a turn after a page rejoined it; leaving a chat
mid-reply saves no half reply into the chat opened next; a tidy shutdown
marks running turns cut off; replaying a record runs no step and no model
call again.

### Build terminals (`terminals/`, `daemon/routes/terminals.py`)

All eighteen files in `terminals/` were read whole.

- [x] **TM1 (2, medium, checked). The pane's "ask AI" and "make a workflow
  from this terminal" send the terminal's output to a model with no
  fence.** Text a program printed (a downloaded file shown on screen, a
  repository's log) can steer the command the pane then offers on "Type it
  in", or the steps of a workflow that is saved
  (`daemon/routes/terminals.py:1206, 1277-1295` and `1427-1444`, where the
  output can also close the marks put around it). The second review's fix
  covered the three pane tools only.
- [ ] **TM2 (7, medium, checked for the missing limit; the rest pass
  only). The first time a page shows a reopened terminal at a different
  size, its shell is started a second time with no time limit.** If Windows
  hangs that start (the known Store PowerShell hang), the pane is blank and
  takes no keys until it is closed, and its still-working old shell is cut
  off from the screen (`routes/terminals.py:1051-1057`;
  `terminals/session.py:379-440`).
- [ ] **TM3 (3, medium, checked). A terminal whose shell cannot be started
  at a restart for any reason other than "stuck" is dropped with no word.**
  A program that was uninstalled or moved is enough: no terminal, nothing
  on the Build page, a debug log line only (`terminals/manager.py:1410-1411,
  1463-1464`), and its kept history is deleted at the next save
  (`985-996`, pass only).
- [x] **TM4 (2, medium, checked). The Continue strip says Continue on "X"
  and "Your conversation is copied to X", but the daemon is never told X
  and picks the account itself.** With three or more Claude accounts the
  two can differ, and the conversation is then copied into and resumed
  under another account than the one agreed to
  (`dashboard/components/terminal/ContinueStrip.tsx:151, 160, 226`;
  `routes/terminals.py:663-732`; `terminals/continue_on.py:587-680`).
- [x] **TM5 (1, medium, checked on the daemon side). The Launch menu's
  "With Iron Jarvis's tools (opens a new terminal)" rows say "Jarvis tools
  ready", and pressing one opens a bare terminal:** nothing is set up and
  no tool is started, because no boxes are ticked when the terminal is
  made and the set-up runs only then. The sentence that would say why goes
  to the log (`platform.py:403-426`; `terminals/recipes.py:545-557,
  716-718`; `dashboard/app/terminals/page.tsx:1118`).
- [ ] **TM6 to TM20 (low; TM10, TM14, TM15 and TM20 suspected, the rest
  *Done so far: TM14, TM15 (v1.520.0); TM13 (v1.523.0); TM16 (v1.524.0).*
  pass only), in the order written:**
  - Kept on this PC and in backups: each terminal's last 64 KB of output
    is written in the clear and carried by every backup, where it stays
    after the terminal is closed (`manager.py:927-946`,
    `maintenance.py:142-191`).
  - Raw errors: opening a terminal in a folder that is gone answers the
    raw Windows error (`backend.py:1232-1237`); "could not be carried over
    (PermissionError)" is said after the conversation file was in fact
    copied (`continue_on.py:421-447`).
  - Other systems and fallbacks: a terminal an agent opens has no reader
    on Linux and Mac, so its output is never recorded and reads "printed
    nothing" (`tools/pane_tools.py:192-217`; worth a look for the Linux
    work); on the older Windows fallback, closing a terminal ends only the
    shell (`backend.py:245-251`); one shell that exits at once marks
    terminals broken for the rest of the daemon's life
    (`manager.py:694-712`).
  - What is said: after a restart a terminal that was running an ordinary
    program comes back as a fresh shell with no line saying that it ended
    (`manager.py:1211-1252`); a script's echoed line such as "> vitest
    run" is read as "the tool is idle", so a wait reports finished while
    it has just begun (`terminals/agent_state.py:187-232`).
  - "Type it in" sends a block of several lines as is, and a bash-family
    shell runs the first lines without the user's Enter
    (`dashboard/components/terminal/TerminalPane.tsx:1000-1004`).
  - Accounts: a line on screen such as "resets in 90000 hours" parks the
    account for years on Continue (`terminals/limit_state.py:155,
    169-171`); Continue can refuse after it already told the account
    manager the account is limited (`continue_on.py:629-654`); Continue
    and "start fresh" leave no record in history
    (`routes/terminals.py:663-829`).
  - Light on the PC: the pane's "ask AI" reads and a resize run on the
    event loop (`routes/terminals.py:1224-1232, 1067`); a terminal the
    start gave up on cannot be dismissed and is tried again at every start
    (`manager.py:778-782`).
  - Only when run by hand with no token and open to the network (not the
    packaged app): the terminal connection is not held to this PC
    (`daemon/app.py:99-111`).

Holds: a terminal never inherits the install token; every terminal route
and its connection need the token; a model can type into a terminal only
through two tools that ask first and are refused to a low-trust run;
nothing a terminal prints makes Jarvis type or change account by itself
(a strip and a click only); close asks first and takes the whole tree;
buffers are bounded; a dead terminal is shown dead.

### Still not read

- Browser: the record-a-task path (`extensions/chrome/src/sidepanel/recording.ts`,
  `browser/recording.py`), `browser/protocol.py`, `models.py`, `risk.py`,
  the add-on's `bridge/socket.ts` and `content/scrub.ts`; whether
  screenshots are ever cleaned up, and whether the picture question is on
  a receipt.
- The tools door: whether the client wall applies to a memory read through
  it (`memory/origin.py`); what each coding tool's own client does when the
  door forgets its session; how such an ask reaches the phone.
- Coding sessions: `daemon/routes/build_git.py` (push, the Changes view),
  `harness/rewind.py`, `branches.py`, `compact.py`, `reply_summary.py`,
  `patience.py`, `attempt_spend.py`, `reply_cap.py`; the end of
  `daemon/routes/night.py`; `daemon/chat_stream.py`.
- Chat: the plain lane's body and the rest of the streaming lane beyond
  the save path; the Chat page's hand-off to an agent; the phone doors'
  recovery of a turn in flight; the tool gate's own record writes.
- Terminals: the Studio code that types into a terminal
  (`daemon/routes/creative.py`); the account manager's `signal` and `pick`
  (`iron_proxy/client.py`); the list of places allowed to start a process
  outside `core/procs`.
- From the second review's list and still open: the bundled account
  manager beyond its server and delete code.
- Permissions: most of `core/auto_approve.py` (where a command is judged
  dangerous or outward), `sandbox/git_bash.py`, `sandbox/manager.py` and
  the Docker sandbox, `harness/command_shell.py`, `harness/session_grants.py`,
  `core/approvals.py`, `repl/`, and the dashboard's Permissions page and
  approval card.

## Third fix plan (groups 17 to 23)

The owner said "Yes, plan fixes" on 2026-11-16. This is the plan only:
nothing below is built, and building starts on the owner's go-ahead. The 90
findings above are fixed the way the first two plans' were ("How the fixes
are made", points 2, 4 and 5): one release per group, Opus 5.5 at high
effort implementing, one agent per set of files, the lead reading every
diff and proving each new test red before green. A finding marked "pass
only" or "suspected" is first proven with a failing test; one that cannot
be made to fail is marked "not a defect" here with the reason, and not
changed.

Since the review was written, the lead closed the one open link in PG1 by
reading it: `tools/registry.py:824-830` hands a switched-on app's every
tool to the chat, held-back ones included (they are only ranked last when
the app has more tools than fit). PG1 is checked end to end; it has still
not been run.

**The shared causes this time**

| Cause | The one piece | Closes |
|---|---|---|
| A way in that the gate does not judge | Two moves. A held-back tool's name is left out of the approval that switching an app on makes, in the one function both chat lanes build that approval with, so only its own card can carry it. And a tool's own "ask every time" check moves into the tool gate (`registry.invoke`), so a caller that cannot ask is refused there instead of each lane remembering to call it | PG1, PG2, PG8, MC5 |
| The page, not the daemon, writes the reply into its chat | When a turn ends, the daemon adds the reply to its chat through `chat_append`, keyed by the turn. The page shows what the daemon wrote and stops saving replies itself; a save that meets a reply already stored by that turn drops its copy; a turn's record is never removed while its reply is not in its chat | CS1, CS2, CS3, CS5, CS6, CS10, SB3, RB9 |
| A Stop lives in memory only | The press is written on the turn's record at once ("stop asked"), also when it arrives before the turn is known, and every carry-on (the resume pass, the "Resume when ready" wait, a waiting card) reads it. A reply whose end was seen loses its kept copy whatever happens next | RB1, RB5, CS4, SB2, SB13 |
| The screen names one thing and the daemon is never told | Each press sends what the screen named (the account, the boxes, the setting as it is now), and the daemon refuses in words when it would do something else | TM4, TM5, SB4, SB6, MC1, BR1, BR3 |
| "Try on several models" went around the night's checks | One reading of an attempts card (how each attempt ended, what each used), shared by the night's ending, the Last night row and the pay-per-use check | NA1, NA2, NA3, NA7 |
| Terminal output handed to a model as the user's words | Every prompt built from a terminal's output goes through `wrap_untrusted` and the usual scan; a scan test lists the callers of `output_tail()` | TM1, TM14 |
| A raw exception or engine words on screen | `core.failures.plain_failure`, and the scan test from group 16 widened to the places found here | RB12, RB13, MC8, NA6, TM7, TM8, PG4, PG10 |

**Order.** A way past a gate and outside text first, then loss, then Stop,
then consent and what the screen promises, then false "done" and costs,
then what survives a restart, then stalls, what is kept at rest and
wording.

| Group | Theme | Findings |
|---|---|---|
| 17 | Every way in is judged, outside text stays data | PG1, PG2, PG3, PG6, PG7, PG8, PG9, TM1, TM14, TM15, MC5, BR6, SB11, NA5 |
| 18 | A reply's home is the daemon | CS1, CS2, CS3, CS5, CS6, CS7, CS10, SB3, RB9 |
| 19 | Stop is written down | RB1, RB2, RB5, RB8, CS4, SB2, SB5, SB13, NA8 |
| 20 | What the screen says is what happens | TM4, TM5, TM16, RB3, RB4, RB14, SB4, SB6, MC1, MC2, MC6, BR1, BR3, PG4, PG5, PG10 |
| 21 | Done means done, every cost counted | NA1, NA2, NA3, NA4, NA6, NA7, RB10, BR2, BR4, BR5, TM13, CS8, SB8, SB9 |
| 22 | Survives a restart and a dropped connection | MC3, MC4, MC7, SB1, SB7, SB10, SB14, TM2, TM3, TM9, TM10, TM11, TM12, TM17, TM19, RB7 |
| 23 | Never stalls, kept at rest, plain words | RB6, RB11, RB12, RB13, MC8, TM6, TM7, TM8, TM18, TM20, CS9, SB12 |

All 90 findings are in exactly one group (14 + 9 + 9 + 16 + 14 + 16 + 12).

**What each group changes, and where**

- **Group 17.** `daemon/chat_turn.py` and `daemon/routes/chat.py` together
  (the approval made by arming), `tools/registry.py` (the "ask every time"
  check in the gate), `workflows/engine.py`, `core/trust.py` (code-running
  tools on the list a flagged run loses), `sandbox/cmd_guard.py` (a
  redirect such as `2>&1` is kept whole; the command after `if exist` or
  `for … do` is judged), `core/grants.py` (only the app's two own names
  are left out of an exact approval), `tools/pane_tools.py` (a typed line
  is read by the git guard and the `;` guard), `daemon/routes/terminals.py`
  (the fence), `terminals/limit_state.py` (a reset more than 8 days away is
  not believed), `mcpserver/server.py`, `harness/attempts.py` (the carried
  files pass the night commit's screen). New tests that must go red first:
  a chat with a switched-on app that holds a held-back tool must show a
  card; a scheduled workflow whose step adds an outgoing webhook must be
  refused; `del /q *.tmp >nul 2>&1; python run.py` must be refused.
- **Group 18.** The largest change, in two steps inside one release. First
  the daemon writes the reply (`daemon/durable_turns.py`,
  `daemon/chat_append.py`, the save route in `daemon/routes/chat.py`) and
  the 7-day clean-up learns to keep a record whose reply is not in its
  chat. Then the Chat page (`dashboard/app/chat/page.tsx`,
  `lib/useChatStream.ts`, `lib/turnRejoin.ts`) follows a turn by its id,
  rejoins once after a silence instead of saying "stopped", and stops
  writing replies. The sidebar's conversation is saved the same way
  (`browser/panel.py`). Deleting a chat removes its turn records.
- **Group 19.** `core/turns.py`, `daemon/durable_turns.py`, the stop route
  and the start of `/chat/stream` in `daemon/routes/chat.py`,
  `daemon/routes/build.py` (the resume pass, the reply's end, Delete
  holding the session), `browser/panel.py` (a risk card ends on Stop) and
  the add-on's `sidepanel.ts` (Stop, Allow and Deny check that the press
  arrived). RB2 is first proven: a reply that ended normally must never
  get a limit card.
- **Group 20.** `daemon/routes/terminals.py`, `terminals/continue_on.py`,
  `terminals/recipes.py` and the Launch menu; the session PATCH and branch
  routes in `daemon/routes/build.py`; `hosted/adopt.py` and
  `hosted/service.py`; `mcpserver/server.py` and the bell;
  `browser/service.py`, `daemon/routes/browser.py`; `daemon/routes/undo.py`;
  `tools/permissions.py` and the Settings permission rows.
- **Group 21.** `harness/night.py`, `harness/night_morning.py`,
  `daemon/routes/night.py`, `daemon/routes/build_attempts.py`,
  `harness/attempts.py`; the finished notice in `daemon/routes/build.py`;
  `browser/screenshot.py`; `terminals/agent_state.py`; the usage write in
  `daemon/chat_turn.py`; two wordings in `browser/panel.py`.
- **Group 22.** `mcpserver/server.py`, `session.py`, `stdio_shim.py`,
  `codehost.py`; `browser/panel.py` and the add-on;
  `terminals/manager.py`, `session.py`, `backend.py`;
  `tools/pane_tools.py`; `daemon/routes/build.py` (a question that could
  not be saved).
- **Group 23.** `daemon/routes/build.py`, `daemon/durable_turns.py`,
  `harness/sessions.py`, `daemon/routes/mcpserver.py`,
  `terminals/backend.py`, `terminals/continue_on.py`, `maintenance.py`,
  `daemon/app.py`, `browser/panel.py`.

**Decisions taken as recommended.** Each changes something the owner will
notice. Say so to change any of them.

| # | Decision | Taken |
|---|---|---|
| G1 | A held-back app tool in chat (PG1) | Asks every time, even with its app switched on, until it is trusted on the app's card |
| G2 | A workflow step whose tool asks every time (PG2) | Refused in words in an unattended workflow, naming the step, unless an "Always for this workflow" approval covers that exact step |
| G3 | Code-running tools after flagged text (PG3) | Refused for the rest of that run, like the web tools |
| G4 | An Undo the user pressed (PG4) | Never asks; refused only for a tool set to Deny |
| G5 | "Ask first" for web search and web fetch (PG5) | Settings offers Allow or Deny for these two and says why; "Ask first" is no longer offered. (The other way, a card in attended chat only, is more work for a choice few make.) |
| G6 | Where a chat reply lives (CS1 to CS5) | The daemon writes every reply into its chat; a reply that finished while you were elsewhere is in the chat and in search at once |
| G7 | The sidebar's conversation (SB3) | Saved as a chat marked "from the browser sidebar" and listed with the others; Reset starts a new one |
| G8 | Silence for 60 seconds (CS3) | The page rejoins once and says it is still working; "stopped" is said only for a real stop |
| G9 | A stopped coding reply after a restart (RB1) | Never carried on by itself. It is shown as stopped, with "Continue from where it stopped" as your own press |
| G10 | A night task tried on several models (NA1) | Ends "Needs you: choose one" when at least one attempt finished, "Failed" when none did; never "Done" |
| G11 | The pay-per-use limit at night (NA3) | Applies to every pick, the default model included, and each attempt gets what is left of the limit |
| G12 | "Runs at once" (NA7) | A task tried on three models counts as three |
| G13 | Files carried into "Try on…" (NA5) | Pass the same screen as the night's commit; a file left out is named on the card |
| G14 | A stricter permission level set while a reply runs (RB3) | Applies to that reply from its next step; a looser one applies from your next message; the chip says which |
| G15 | "Continue on X" (TM4) | Goes to X, or refuses and names the account it would use instead |
| G16 | "With Iron Jarvis's tools" in the Launch menu (TM5) | Pressing it shows the boxes first (ticking one is the consent), then opens the terminal set up and starts the tool. A row never says "ready" for something it cannot do |
| G17 | A coding tool asking to act in your browser (MC2) | Its own card, naming the session or terminal and what is clicked or typed; "Allow once" and "Deny" only |
| G18 | A risk card in the sidebar (SB6) | "Allow once" and "Deny" only |
| G19 | Terminal history in backups (TM6) | Left out. The list of terminals is kept, so they reopen after a restore, with empty history |
| G20 | "Start new chats local only" (SB12) | Also covers the browser sidebar and its dictation |
| G21 | "Type it in" in a terminal (TM14) | Offered only for a one-line command |
| G22 | "Branch from here" (RB4, RB14) | The branch keeps the session's Local only hold and its reply limit |

**What must be tried on the installed app, not only in tests**

- Group 22: starting, stopping and reopening terminals, the code host's
  stop, and a real coding tool keeping Jarvis's tools across a restart.
  Like group 12, tests can only use stand-ins for these.
- Groups 19, 20 and 22 change the add-on's sidebar. They hold only once
  the add-on in the browser is the new one.
- Group 18: a long reply left running while the Chat page is closed, then
  opened in two windows.

**Worth settling before group 22.** TM9 (a terminal an agent opens records
nothing on Linux and Mac) is in the Linux work's path; that session should
know before it tests agent-opened terminals.

**Left for the owner as before.** A4 (the bundled account manager) is
unchanged by this plan, and so are the eight decisions at the end of the
second plan.

**Building (from 2026-11-16).** The owner started the fixing session the
same day. That session had drafted its own grouping (by area) before it saw
this plan; the two were compared and THIS plan is the one kept: these
groups, these decisions. What differs in the building, and why:

- **PG4, PG5 and PG10 are built with group 17, not group 20.** Their
  implementers were already at work under the first grouping when the
  plans were compared, and they share `tools/permissions.py` with PG1.
  Decisions G4 and G5 apply to them as written.
- Anything else that ends up built differently from a decision above is
  said in that group's "as built" note.

### Group 17 as built (v1.520.0)

Fixed: PG1, PG2, PG3, PG6, PG7, PG8, PG9, TM1, TM14, TM15, MC5, BR6, SB11,
NA5, and, early (see "Building"), PG4, PG5 and PG10.

Checked and not a defect: a LOCKED app (Microsoft 365) switched on for a
chat. The first implementer suspected it had PG1's gap. It does not: a
locked app is always kept on the user's own models, so the chat's Local
only card asks before every use at every approval level, the phone lane
does not offer its tools, and outside a Local only chat or in a task the
tool refuses itself. Seven new tests pin that.

Where it differs from the plan:

- **PG1 reached further than the two chat lanes.** A task handed off from
  chat starts with every name chat had switched on, so a held-back tool ran
  there too. The lead added the agent-run side; the implementer who tested
  it found three more ways through and closed them: a tool whose own check
  said "free" skipped the card, one card for several app tools in a step
  answered for the held-back one, and a sibling's "Allow for this run" woke
  the held-back tool's waiting card. A held-back tool now always has a card
  of its own, and "Allow for this run" does not carry into the next run.
- **PG2's check was not moved into the tool gate.** The workflow step reads
  the tool's own check through the helper the lanes share, and a scan test
  now lists every caller of the gate with how it honours that check (eleven
  call sites, none other missing it). Moving the check into the gate would
  need each of those callers to say whether it had asked; a mistake there
  refuses work that is allowed today. Left for a later decision.
- **PG8, the folder of a terminal an agent opens.** The implementer confined
  it to the session's folder. The lead widened that to the rule for what
  Iron Jarvis may read (never a protected folder, another computer, or
  outside "Folders Jarvis may read"), because "open a terminal in my
  project's folder" is asked about on the card anyway.
- **TM1.** Output in which the scan finds a line written as instructions to
  a model is held back whole. The lead added that "ask AI" says so ahead of
  its answer; the workflow route refuses before any model is called.
- **SB11.** The implementer also required "not a tab" for the side panel.
  The lead took that out: the page's address already keeps a page script
  out, and a browser that marked its side panel as a tab would have had
  every press in the sidebar refused.
- **Decision G5 as built:** a stored "Ask first" for web search or web fetch
  from an older version is shown as "Run without asking" (what it did).

Left open:

- "A locked app asks before each use" rests on every locked app also being
  kept on the user's own models. The catalog ties the two; nothing in the
  gate reads the lock itself apart from the "Run app tools without asking"
  switch.
- NA5: in a session that works in its folder directly, after Keep the
  session is in the attempt's folder, which lacks the file that was left
  out (a `.env`, say). The card and the keep note say so; the file is not
  brought across.
- PG8: the `;` guard knows a pane's shell only as it was started (a `cmd`
  started inside a PowerShell pane is not seen). The git guard on a typed
  line changes nothing today, because the pane tools are not offered inside
  a coding session's reply, the only place that guard acts.
- The Build page's "Run in terminal" (from a terminal's own chat) still
  sends every line of a block; there the press is the consent.
- `/terminals/{id}/context` returns the output as it is, for the user to
  copy; Iron Jarvis sends it to no model.
- MC5: `mcpserver/server._grant_now` repeats the end of
  `PaneTokenStore.resolve`. The time limit is the user's tool time limit
  (ten minutes by default); the bridge still gives up first, at one minute.
- `tools/apply_changes` and `tools/tool_script` read a tool's own check by
  their own code, not through the shared helper (the scan test pins that
  they read it).
- In both chat lanes a reply with no tool switched on ends without a record
  of a tool call the model made anyway.
- The reason for a gate with no way to ask at all still reads "no resolver
  in headless mode"; the app always has one, so it is not reachable.
- `tests/test_mcp_server_v1238.py` asks with `ref` where the tool wants
  `target`, so its approved-ask tests never reach the tool;
  `tests/test_browser_downloads_v14430.py` uses bank wording in its
  examples.

Tested with stand-ins only: the models, everywhere; the add-on's worker
(pinned by reading its source, type-checked, and run once in Node with a
stand-in browser, never in Chrome or Edge); terminals (a stand-in for the
shell); the tools door's terminals and Code session; the account manager's
own reading of a limit line. What would show a problem on the installed
app: a press in the browser sidebar that does nothing (SB11, with the new
add-on); a new app tool that runs with no card.

What the branch's full runs on GitHub showed (the release is v1.520.0 because
another session released v1.519.0 while this one was being tested):

- The dependency page (`docs/DEPENDENCIES.md`) was out of step: this
  group added imports between packages. Regenerated.
- Two tests passed only on a retry, which counts as red here. One was this
  group's own doing: a new test read a held-back app tool's permission key
  outside any task, which leaves the note "the coming question is about
  this tool" for the next test in the same worker. The read now checks
  "held back" first (`harness/session_grants.shareable`), and every test
  starts and ends with no such note (`tests/conftest.py`). The other was an
  older desktop test that runs a real Windows process listing under the
  app's own 10-second limit, which a loaded test machine can miss; it now
  runs the listing with a limit of its own. The second full run was green
  with no retried test.

### Group 18 as built (v1.521.0)

Fixed: CS1, CS2, CS3, CS5, CS6, CS7, CS10, SB3, RB9.

Where it differs from the plan:

- **The page still saves its own copy of a reply; the daemon's copy is the
  floor, not the only writer.** The plan said the page would stop saving
  replies. A reply's steps, receipts and documents are built by the page
  from the stream, and a line built by the daemon is plain text; had the
  page stopped saving, every chat would have lost them. As built: the
  daemon writes a plain reply at once when no page is following, and after
  fifteen seconds when a page was following and has not saved; each message
  of a reply carries the reply's name, the page's fuller message replaces
  the daemon's by that name, and a second copy is merged away on both
  sides. Decision G6's result holds: a reply that finished while the user
  was elsewhere is in its chat and in search at once.
- **Decision G7 as built.** The sidebar's chat is listed with a "Browser
  sidebar" label and can be read, searched, archived and deleted from the
  Chat page; it is carried on in the sidebar only (the banner says so, and
  so does typing in it).
- **Deleting a chat does not stop a reply of it that is still running**
  (it never did); the reply writes nothing and leaves no record.

Left open:

- The two halves were built to a written contract and tested apart: the
  page's tests answer for the daemon, the daemon's tests have no page.
  What must be tried on the installed app: a long reply left running with
  the Chat page closed, then the chat opened in two windows; a reply
  followed across a minute with the app asleep.
- Suspected, not proven and not fixed: with two pages on one chat, a reply
  that hands off to an agent may start the agent run twice. Each hand-off
  line now carries the reply's name, which a guard could check.
- The Build pane's own chat saves its whole conversation with no merge,
  links a new chat's first reply the old way, and still says "this turn was
  stopped" after a minute of silence. So does a coding session.
- After a silence, when Iron Jarvis cannot be reached at all, the half
  reply is saved under the reply's name; if the reply was in fact still
  running, its end is then not written over the half.
- A reply nobody watched keeps its steps and receipts only if the chat is
  opened within seven days while it is still the last reply; after that it
  stays plain text.
- A reply whose chat was never saved (no chat ever named) is removed with
  its record after seven days: there is nowhere to write it.
- The sidebar: a failed save of its chat is logged and the panel is not
  told; its usage is not counted on its chat; a very long sidebar chat
  keeps its newest 200 messages; `learning/went_wrong.py` would label a
  sidebar chat's evidence "phone".
- Words: the Stop button's hint still says "Stop this turn"; typing in a
  phone chat with no phone linked says "no longer linked to a destination".
- No test pins: the page following a held reply once it is free, the quiet
  put-back when filling in a plain reply fails, the Build pane chat's
  stamps.

Tested with stand-ins only: the models; the daemon, in every page test;
the browser sidebar (never run in Chrome or Edge; it needs the new add-on
for the "starts fresh" line to show).

### Group 19 as built (v1.522.0)

Fixed: RB1, RB2, RB5, RB8, CS4, SB2, SB5, SB13, NA8, and, from group 20,
SB4 and SB6 (they are in the same two sidebar files, so they were built and
released here).

RB2, RB8, NA8 and SB13 were marked suspected or pass only; each was first
proven with a failing test.

Where it differs from the plan:

- **A Stop for a reply not yet known answers "kept", not "no such reply".**
  The stop route says "already finished" only for a reply that really had
  finished; the page then shows that reply instead of writing "Stopped."
  over it.
- **The phone's lane registers a named reply too**, so the same Stop
  reaches it. Before, only a reply in the window could be stopped by name.
- **Decision G9 as built.** The stopped reply's line and the "Continue from
  where it stopped" press use what the page already had for a reply that
  ended on an error; no new control was added.
- **The sidebar's switch.** A press is its one truth and "off" always
  wins; an open may still restore "on" after Iron Jarvis restarts, so the
  setting survives an update.

Left open:

- The stopped line of a coding reply is drawn in the red tone of an error
  (`HarnessFailure.tsx`); the words are right.
- In Chat, the check "was the reply stopped" after a RISK card's answer has
  no test of its own (the ordinary card's has).
- A Stop kept for a reply that never starts is forgotten after two minutes;
  it is in memory only.
- The sidebar: a window that dies without closing (a crash) stays counted
  as open, so a later close of the last real window does not stop the
  reply; a Stop you press writes the half answer without a "stopped" note
  (closing the sidebar does write one); with an older add-on, "Allow all"
  on a risk card still does that one action (as "Allow once" would) and
  only the switch is refused.
- After a restart the record of a stopped coding reply still reads "cut
  off" in its own status; the session's line, which is what the user sees,
  says stopped.
- Not verified end to end: that a real refused side task lets the main
  reply end normally (RB2 was proven by setting the limit note directly).
- `routes/build.keep_stopped_reply` uses a private helper of
  `harness/reply_record`.

Tested with stand-ins only: the models; a whole coding reply was not driven
through the stop route in the Stop tests (the coding tests write the mark
with the real writer); the sidebar panel was never run in Chrome or Edge
(its built bundle is run by the dashboard suite's side panel test, which
now covers Stop, a press that was not sent, the risk card's two buttons
and "Allow all" as one answer), and needs the new add-on in the browser.

What the branch's first full run on GitHub showed (group 19):

- Three tests of the side panel failed. The dashboard suite runs the BUILT
  add-on, and the lead's local copy of that build was stale, so they passed
  here. They pinned the old behaviour (a line said, a card hidden, on the
  click itself); they now wait for the press to go out, and four tests were
  added that run this group's sidebar behaviour in the real bundle.
- One older test passed only on a retry: the coding session's "at most
  three helpers at once" test, which leaned on real time in three places.
  It now waits on events only.

### Group 21 as built (v1.523.0)

Fixed: NA1, NA2, NA3, NA4, NA6, NA7, BR2, BR4, BR5, TM13. Four findings the
plan put here (RB10, CS8, SB8, SB9) are in files group 20 also changes and
go out with group 20. NA4, BR2, BR5 and TM13 were suspected or pass only;
each was first proven with a failing test (BR5 by reading the add-on and
pinning its source).

Where it differs from the plan:

- **Decision G12 was not built as written.** "A task tried on three models
  counts as three" against "Night tasks at once" would have made every
  default install's three-model night task fail, because that setting is 2
  by default. As built the setting is kept another way: the task runs at
  most that many of its models at a time, the rest wait their turn inside
  it, and it counts for that many against the night's room. Nothing fails
  for its size.
- **Decision G11.** The models billed per use share ONE remainder of the
  night's limit rather than each getting a slice, so one can use what
  another does not need; together they cannot pass it by more than one
  model call each.
- **At night a model that may not start no longer stops the whole task**;
  by day a refusal for one model still refuses the request, as before.
- **BR5.** The add-on refuses the picture when another tab is on screen; it
  does not return the other tab's picture under its own name.

Left open:

- **Found along the way: a model call made inside a tool is on no usage
  row.** The screenshot's question, `view_image` and the reading of text in
  pictures each ask a model, and none of the three lanes counts it. The
  tokens now ride the screenshot tool's own record; the Usage page still
  does not show them. It needs one seam in the two chat lanes and the agent
  runtime, or in the router.
- With no work kept on one model, a text-only default and no model anywhere
  that can see, the picture stays on the text-only model, which may then
  "describe" it (`view_image` has the same gap).
- The screenshot's failure line still carries the provider's own error text
  (an older test pins it), and the "no model can see" line suggests models
  of two named companies.
- A try-on task whose models queue may take one time limit per round (three
  models at the default of two: up to twice the limit). A waiting attempt's
  row says "starting". After Keep, a model that never started reads "Not
  kept · branch kept" although it has no branch.
- TM13: a script echo with no version in it is not recognised.

Tested with stand-ins only: the models; "billed per use" (set in the test);
the money cut-off between attempts (a unit test of the shared limit, not a
model reporting dollars); the capture check in the add-on (source pins and
a type check, never a browser).

### Group 20 as built (v1.524.0)

Fixed: TM4, TM5, TM16, RB3, RB14, MC1, MC2, MC6, BR1, BR3, and four that the
plan put in group 21 but that live in files this group changes: RB10, CS8,
SB8, SB9. SB4 and SB6 went out with group 19, and PG4, PG5 and PG10 with
group 17.

Not a defect: RB4. A coding session never gets the hold that keeps a
session Local only after it read an app kept on the user's own models: the
only code that writes it runs after such an app was used, and that app
refuses outside chat. Two tests pin it. (Decision G22's other half, the
reply limit, is built.)

Where it differs from the plan:

- **MC1.** The session's record alone decides the boxes at a restart. The
  brief said the state kept beside the tool could still narrow them; that
  would have left a box showing ON while the door refused it.
- **MC2 (decision G17).** The card waits 45 seconds, not as long as chat's
  card: the bridge between the coding tool and Iron Jarvis gives up at one
  minute, and a longer wait would promise an answer the tool can no longer
  receive. Who, what and where are one sentence on the card.
- **RB3 (decision G14).** Plan switched on mid-reply was built for real: the
  running reply makes no change from its next step.
- **Decision G16.** The boxes shown are Memory and Browser (the two a Build
  terminal has).
- **Added along the way.** The browser card says "No browser was paired, so
  there was nothing to forget." when that is so. A change to Local only
  while a coding reply runs answers that it starts with the next message.

Left open:

- **Local only switched on while a coding reply runs does not reach that
  reply**, which keeps the model it started with until it ends. The daemon
  now says so, but an open coding session has no Local only switch at all
  (it exists only before a session starts), and the chat tool that refuses
  to change it for a coding session points to "its own Local only switch in
  Code". For the owner: add that switch, or correct the sentence.
- TM5: the start line typed into the new terminal was not tried with a real
  coding tool on the installed app; a terminal opened with tools is not
  given them again after Iron Jarvis restarts (as before).
- MC2: how the phone shows this card was not looked at; the 45 seconds for
  Codex's own client is from memory, not measured.
- RB3: a helper already running keeps the level it started with; the
  sentence in the reply's own instructions that says actions are
  pre-approved is not rewritten mid-reply (the cards and refusals hold).
- BR1: "could not close the browser's connection" can only be reached with
  a stand-in; in practice a close always closes.
- RB10: a Build › Code reply cut at the model's LENGTH limit still arrives
  as finished (`hosted/acp.py` turns that ending into "completed" before
  the notice sees it). The bell draws a reply stopped at its limit with the
  same check mark as a finished one, and the activity list shows the word
  "Limit" for it.
- CS8: the follow-up questions, an app's own model call and the reflection
  still write their usage on the event loop (a failed write is now logged
  there too). A hard kill in the middle of a reply still loses the usage of
  the model calls already made in it: that needs a row written after each
  call, and a start-up step that settles the ones left open.
- SB9: other failed steps in the sidebar still show a code before their
  words ("ELEMENT_NOT_FOUND: …").

Tested with stand-ins only: the models; the account manager (a recording
stand-in); the terminals' shell; the coding tool behind the door and the
browser tab it would act on; no card was looked at in the running app.

Found in this group's own local run: once a reply's usage is written off the
main loop, the sidebar's reply is still tidying up for an instant after the
panel was told it ended, and a message sent in that instant was refused as
"still working on the last message". A Send that arrives after the shown
end now waits for the tidy-up (at most five seconds).
