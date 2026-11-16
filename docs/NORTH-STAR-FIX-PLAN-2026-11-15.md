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

- [ ] **4.1 Undo is written before the change.** `tools/registry.py:1050,
  1270, 1450`. The journal row is written with the intent and kept on every
  outcome that is not provably clean (stop, deadline, failed save, crash).
  An orphaned before-copy file is reclaimed.
- [ ] **4.2 "Clear completed" moves (D7).** `daemon/routes/sessions.py:827`,
  `agents/orchestrator.py:2985`. Folders go to the app's trash; the button and
  the confirm say what happens.
- [ ] **4.3 A set-aside database is shown, and backups wait.**
  `core/db.py:400, 506`, `maintenance.py:178`. A marker is kept; Home and
  doctor show it with one press to restore; no backup is pruned while it is
  unanswered.
- [ ] **4.4 Writers save beside, then swap.** `documents/excel_tools.py:239,
  1344`, `documents/excel_reconcile.py:595`, `tools/builtins.py:977, 1110`,
  `harness/tools.py:215, 951`. One atomic-write helper.
- [ ] **4.5 Three tools stop overwriting silently.**
  `documents/tools.py:732` (convert), `tools/images.py:681, 746` (convert,
  resize). Each keeps the replaced file (the `_keep_replaced` pattern) and can
  be undone.
- [ ] **4.6 A cut-off run keeps its branch.** `agents/orchestrator.py:3306`
  (only with `git_native`). Never prune a run that was interrupted or can be
  resumed.
- [ ] **4.7 The approval card tells the truth about Undo.**
  `tools/reversible.py:41`, `dashboard/lib/approvalWords.ts:140`. The card
  reads what this call can do, not the tool's class.
- [ ] **4.8 A delete on a page is recorded and can be undone.**
  `daemon/routes/system.py:860`, `workflows.py:594`, `agents.py:4084`,
  `comm.py:281` and the other `@app.delete` routes. Each goes through the
  record journal chat already uses. Scan test: every delete route journals or
  is on the allow list.
- [ ] **4.9 The clean-up says what it removes (D8).** `core/db.py:725`,
  `settings/schema.py:977`. The description names run details and Undo
  history; an aged run says so; `AgentRun.result` is kept.
- [ ] **4.10 Backups include runs' own folders, or say they do not.**
  `maintenance.py:81, 130`. Files a run's result names are included.
- [ ] **4.11 Sleep is not a failure.** `desktop/main.js:6164`,
  `tools/registry.py:1097`. Suspend and resume reach the daemon; live
  deadlines are extended by the time asleep.

## Group 5. Never stalls, approve once

Area rules: `docs/rules/automations.md`, `daemon.md`, `tools.md`.

- [ ] **5.1 A workflow tool step has a time limit and can be cancelled.**
  `workflows/engine.py:1247`, `daemon/routes/workflows.py:251`.
- [ ] **5.2 A schedule or workflow can be approved once (D10).**
  `platform.py:2367`, `workflows/engine.py:1075`, `agents/runtime.py:1784`.
  New grant scopes; the schedule fire passes its agent's name.
- [ ] **5.3 A workflow whose driver raises ends Failed.**
  `agents/orchestrator.py:854`, `workflows/engine.py` `run_record`.
- [ ] **5.4 Database writes leave the event loop.**
  `agents/orchestrator.py:1292, 1641, 2068, 2393`, `reflex/router.py:218,
  239, 276`, `motivation/engine.py:656`, `workflows/tools.py:337, 358`,
  `scheduling/tools.py:109`.
- [ ] **5.5 A scan test for child processes.** Every `subprocess` or
  `os.startfile` outside `core/procs` is on an explicit allow list.

## Group 6. Chat reaches everything

Area rules: `docs/rules/chat.md`, `messaging.md`, `desktop.md`, `browser.md`.

- [ ] **6.1 One page table.** Daemon-side: every page, its name, its link and
  what can be done there. The lookup gets a "pages" part from it, and a page
  row the lookup returns becomes a door (`capability_search/index.py:310`,
  `tool.py:226`, `daemon/chat_turn.py:4613`). Scan test: every dashboard page
  has a row.
- [ ] **6.2 Improvements from chat.** Tools to list waiting suggestions and to
  answer one through its own route.
- [ ] **6.3 Pairing, backup, restore and updates have doors.**
  `daemon/page_doors.py:886, 917`. Pairing and backup become page steps;
  "Back up now" becomes a tool; restore and update are doors.
- [ ] **6.4 A thing made in chat has a door.** `daemon/doors.py:65`. The
  catalog is keyed on chat's record tools. Test: every chat tool that creates
  a record has a door.
- [ ] **6.5 The quick window goes through chat (D11).** `desktop/main.js:3246`.
- [ ] **6.6 The small actions.** Delete or archive a coding session, delete a
  chat, forget a folder, delete a key: a tool where it can be undone or
  carded, else a door.
- [ ] **6.7 The phone gets the read-only record tools, and a true reason.**
  `daemon/chat_turn.py:5093`, `capability_search/join.py:310`.
- [ ] **6.8 Phone history goes through the planner.** `comm/inbound.py:1222`,
  `daemon/routes/comm.py:781`, `comm/threads.py:346`.
- [ ] **6.9 The identity spine at two more seams.** `agents/decompose.py:1226`
  (the final answer of a planned run), `daemon/routes/agents.py:2067` (apply a
  skill). Add both to the seam test.
- [ ] **6.10 The browser sidebar says what belongs in the main window.**
  `daemon/routes/chat.py:4946`.

## Group 7. Learns from what went wrong

Area rules: `docs/rules/memory.md`.

- [ ] **7.1 An undo, a correction of substance and a thumbs-down with a note
  each leave a suggestion**, with the turn as evidence, kept to the folder.
  `learning/corrections.py:16`, `daemon/routes/chat.py:3307`.
- [ ] **7.2 Tool failures are read.** `improvement/engine.py:719` `sweep` and
  `scan_tool_failures` run from an existing daily loop.
- [ ] **7.3 "Reflect" leaves suggestions that can be answered.**
  `dashboard/components/memory/Lessons.tsx:487`.
- [ ] **7.4 A late rating changes the run's score.**
  `improvement/engine.py:170`.

## Group 8. Calm and plain

Area rules: `docs/rules/dashboard.md`, `daemon.md`.

- [ ] **8.1 No raw exception text on screen.** `plain_failure(what, exc)` at
  every broad `except` in `daemon/routes/` (about 168). Scan test.
- [ ] **8.2 One name per page.** `daemon/routes/system_map.py:595, 652` takes
  its labels from the page table (6.1); the map gains Improvements and Clients.
- [ ] **8.3 No "lane" on screen.** `daemon/chat_turn.py:2862, 2902, 2914`.
- [ ] **8.4 "Proposals" becomes "suggestions".**
  `dashboard/app/autonomy/page.tsx:586`, `daemon/page_doors.py:930`; the word
  joins the retired list.

## Not reviewed

The inside of coding sessions and the night shift (`harness/`, `hosted/`),
browser and computer-use tools, `fleet/` and `iron_proxy/`, shared computers
beyond the task door, and a line-by-line comparison of the two chat lanes. A
second review of these follows group 8.
