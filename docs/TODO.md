# The Comprehensive To-Do

*Everything known to be open as of v1.170.0 (2026-08-13), consolidated from
the deep-review wow track, deferred backlogs across waves, and known limits.
The deep review's 11 confirmed bugs are all FIXED (v1.166.2–v1.167.0) — this
file is what remains.*

## Carried out of the 2026-09-04 audit, Wave 2 (v1.228.0)

- [x] R1 `local_primary_policy` (refuse | failover, default refuse): a LOCAL
  primary that answered 429/5xx/404 refuses by name (kind `answered_error`),
  both lanes; Auto stays the exception. R2 `provider.failover.reason` is
  derived (`router.failure_reason`), `route.from`/`route.why` on both lanes,
  TurnReceipt words it. R5 ~2.5 s `GET /v1/models` liveness pre-probe on a
  local primary with a base_url (dead box refuses at once; a live box keeps
  the cold-load retry ladder).
- [x] T2/T2b/T1(RT7) the `{"arguments": "<json>"}` envelope a local model
  emits is unwrapped (`jsonish.unwrap_arguments`) at BOTH OpenAI-compatible
  parse sites, the Responses-API parser gets the same `loads_object`
  fallback, and `registry.invoke` names a missing required / wrong-typed
  top-level argument (`missing required: query — file_search needs
  ['query']; got []`) through the normal `_record` + `tool.executed` path
  instead of handing the model `KeyError: 'query'`.
- [x] CL1 a client disconnect mid-tool leaves a failed ToolInvocation +
  `tool.executed` ("client disconnected while the tool was running — its
  effect may have landed"), written by an INDEPENDENT task because the anyio
  cancel scope re-cancels every later await of the response task. An
  unknown tool name is ledgered too (RT6's half).
- [x] RT3 `sandbox/native.py` is Popen + communicate(timeout); on timeout the
  whole tree is killed (`taskkill /T /F` on Windows, `killpg` elsewhere),
  drained, and the tool returns at ~timeout with `timed_out=True` — proven
  with a child + grandchild whose pids are gone.
- [x] RT6 `config.tool_call_timeout_s` (default 600, Settings → Automation)
  deadlines every tool call in an agent run via `registry.invoke(...,
  deadline_s=)` → a recorded failed row "<tool> did not finish within N s —
  it was stopped" and the run continues; a step streaming past 200k chars
  (`_MAX_STEP_STREAM_CHARS`) ends the run FAILED with the reason. Still
  open from RT6: a "waiting on <tool>" phase while a call is in flight.
- [x] T3 `fs_policy.usable_workspace_root` probes writability (`dir_writable`:
  a temp file created and deleted, off the loop at every route seam) —
  `C:\Users`, `C:\` and an RX-only folder are refused by `POST /sessions`,
  the agent spawn, and `_root_problem`'s fourth answer ("folder is not
  writable by this app: <root>"); the chat grounding block says "not
  writable"; `write_document`/`write_file`/`edit_file` say "cannot write in
  <workspace>: ... not writable" instead of the hidden `.tmp-<pid>` file.
- [x] T6 `tool_create`'s description and the runtime's `# Environment` block
  name the OS (`sandbox/native.host_os_line`: on Windows "cmd.exe; no POSIX
  mv/ls/cp") so a tool is authored for the machine it will run on.
- [ ] R3 stream() after the first token: `if committed: raise` skips
  `record_failure` + `provider.failed`; an empty `httpx.ReadError("")` reaches
  the client as a blank error line; `provider.failover` is published only
  after the failover stream is fully consumed.
- [ ] R4 the circuit breaker never gates the PRIMARY (`_resolve` /
  `complete()` never call `health.allow` for it); no surface can say "in
  cooldown".

## Carried out of the 2026-09-04 audit, Wave 1 (v1.227.0)

Wave 1 closed RT1 (armed-name gate in `registry.invoke`, both chat lanes and
the runtime), A1 (one approval card per ask), A2 (a conversation grant
releases sibling asks), A11 (honest pause wording), A3 (the worklist re-offers
a run's own claim), A8/RT2 (claims released on finish/cancel/reconcile), A4
(`AgentState.WAITING` + `waiting_on` on every session row), A5/U1
(`Session.outcome` + amber chips on four surfaces + "Re-run the N failed
items"), RT5 (the claimed-write note on the escalation hand-off). Left open
by the lanes:

- [ ] RT4 cancel-race shield: the runtime keeps no handle to the in-flight
  per-step `_save` future, so `_finalize_cancelled` cannot shield-await it
  (the `release_run` half landed). Store it on the runtime, then shield.
- [ ] A10: after a reload the chat page still shows no card for a paused run
  until an event arrives (the bell covers it within 15 s). A9: the bell
  answers one call at a time — add "Allow for this run".
- [ ] `Badge` applies CSS `capitalize`, so the amber chips read "Needs You"
  (`ui.tsx`, coordinator-owned).
- [ ] `tests/test_bulk_job_repair_v1177.py::test_ordering_has_a_total_tiebreaker`
  pins the count of `(key_norm, id)` orderings in `worklist/store.py`; bump it
  when adding another.

## Carried out of the v1.226.0 reliability wave (reviewer-graded, not speculative)

The six-dimension audit (contract, boot/loops, event loop, streaming, desktop
supervisor, dashboard error handling) closed 1 S1 + 12 S2 + 21 S3 in
v1.226.0. These are what the reviewers graded "defer" or "note":

- [ ] `/diagnostics` `background_loops.slack_socket` reads `ok: true` when the
  loop is ARMED (`daemon/app.py` `_tick("slack_socket", True)` before
  `_socket.run` connects); a socket that never connects but retries internally
  reads healthy forever. Record ok only on the first successful connect.
- [ ] `POST /diagnostics/repair` `db_vacuum` / `prune_events` 409 gate keys off
  DB rows (`GET /system/activity`), not `orchestrator._running`; a session
  stranded `active` inside a live process (only `_finalize_failed._save`
  failing can do that) would block Compact until restart. Intersect with
  `_running`.
- [ ] `useEvents` replay (`?since=`) covers reconnect gaps; the chat page's
  `approval.requested` card and `DesktopNotifyBridge` toasts still have no
  poll fallback of their own if the id was evicted (1000-event history).
- [ ] `core/streams.py` `SessionStream.reset()` and the `reset` frame are dead
  on both lanes (no router emits it, `useChatStream` has no case) — remove or
  wire.
- [ ] Chat: regenerate's pre-send save drops the previous reply from disk
  before the new one lands (a reload mid-regenerate loses it); a torn-down
  escalation POST that then FAILS leaves the original thread with an empty
  hand-off bubble and no error when reopened.
- [ ] Desktop: a session that adopted/swept a daemon sweeps by image name on
  Quit, so a dev daemon started later in that session on another port is
  collateral. Quit worst case ≈10 s (drain 5 s + probes), all timer-bounded.
- [ ] UNCONFIRMED from the audit (would need a packaged build): cold boot of
  the frozen daemon with the user's real DB vs the 90 s gate (`prune_events`
  bulk DELETE + `terminals.rehydrate` run before bind); NSIS silent install vs
  an orphaned Next server under `ELECTRON_RUN_AS_NODE` (`installer.nsh` kills
  only `ironjarvis.exe`); SQLAlchemy QueuePool (5+10, 30 s) exhaustion under
  VACUUM + 20 concurrent sessions; tzlocal `ZoneInfoNotFoundError` aborting
  `build_platform` on an exotic Windows zone.
- [x] PARTLY CLOSED v1.227.0 (audit Wave 1): finished/failed/cancelled runs
  and `reconcile_interrupted_sessions` now release worklist claims and settle
  non-terminal `AgentRun` rows; the shield below is still open.
- [ ] Agent cancel race (introduced by the v1.226.0 write offload, graded S3):
  `task.cancel()` can interrupt a per-step `_save(run)` that is mid-flight in
  the executor; `_finalize_cancelled` → `_persist_cancel` then settles
  CANCELLED while the stale merge thread is still pending, so a RUNNING
  AgentRun row can land after the settle (AgentRun badge only — Session
  status is the orchestrator's). Fix: keep the in-flight save future on the
  runtime and `await asyncio.shield(...)` it before `_persist_cancel`.
- [ ] Desktop: the "port went quiet after exit 75 → start our own" path has
  no cycle cap (a contrived /health-refuses-Node-but-not-Python split cycles
  every ~19 s with no toast; no realistic trigger found). A
  `rec.quietRestarts` counter with `notifyCrashLoop` at 3 closes it. Also
  `foreignWhy` says "not ours" when `foreignNotified` was latched by the
  "our stale daemon still held after a sweep" branch — mislabels our orphan.
- [ ] `put_settings` filters unknown keys silently — a future UI key that is
  not in `_SETTINGS_KEYS` is dropped without an error (contract audit note).

## Carried out of the v1.195.0 / v1.196.0 document waves (all MEASURED)

Each of these was reproduced during those waves and deliberately left out of
scope. None is speculative.

- [ ] **UTF-16 files are invisible to both search tools.** They contain NUL
  bytes, so they hit the binary sniff in `filesearch/service._read_text` and
  `GrepTool` and are skipped silently and uncounted. This matters on THIS
  machine specifically: PowerShell 5.1's `>` redirect writes UTF-16LE, so a log
  the user redirected cannot be found. A BOM check before the NUL sniff fixes
  it in ~3 lines. Same class as the cp1252 defect fixed in v1.195.0.
- [ ] **The file-size caps skip silently.** `file_search` (1 MiB) and `grep`
  (2 MB) drop oversized files with no count and no note, while the
  undecodable-file count added in v1.195.0 IS reported. Truncation is reported;
  these two were missed.
- [ ] **`undo.finalize_post_hash` re-hashes on the event loop.** Called from
  `tools/registry.py` after every raw write. Same class as the v1.195.0
  capture_undo fix, roughly half the cost (one read+hash, no write); it was left
  because the caller is synchronous by signature.
- [ ] **`convert_document`, `image_convert` and `image_resize` are IRREVERSIBLE
  writers absent from `agents/runtime._WRITE_TIER`.** The forward guard only
  catches REVERSIBLE tools, so a read-only agent roster can still gain all
  three. Pinned by name in `tests/test_change_intent_guard_v1196.py` so closing
  one forces the list to be updated rather than quietly shrunk.
- [ ] **`excel_edit` / `excel_apply_spec` report a workspace-RELATIVE path with
  no `abs_path`,** against the v1.153.2 rule ("a tool that writes a file says
  WHERE, absolutely"). In chat the workspace is the grounded project root, so a
  saved workbook is announced as a bare filename — the exact scenario that rule
  was written for. Pre-existing; fix both together.
- [x] **A refused chat workspace pick is silent.** When `body.workspace_dir`
  fails the policy check the turn quietly runs in `home/uploads` and the user is
  never told their chosen folder was rejected. Pre-existing, and a real instance
  of the never-silently-degrade rule. FIXED v1.210.0: `_workspace_grounding_block`
  renders the honest "bound to X, but that folder is not accessible right now"
  wording in BOTH lanes whenever the pick is refused, and instructs the model to
  say so rather than guess.
- [x] **The Agents module's dialogs were bounded by the card they were rendered
  from — CLOSED v1.214.0.** Reported: the add-agent "+" popup "is bound by the
  size of the thread (chat window) and on a small card doesn't show everthing
  from this pop up". `PanelPicker` is `fixed inset-0`, but it is returned from
  inside `RoundTable`, whose root is `.card-surface` — and that class carries
  `backdrop-filter: blur(18px)`, which makes an element the CONTAINING BLOCK
  for its fixed-position descendants. So `inset-0` resolved to the thread card
  and its `overflow-hidden` clipped the footer (the Save button). Fixed by
  `components/Modal.tsx`, which portals to `document.body` and owns the
  backdrop, Escape and the scroll lock.
- [x] **The same trap bit a second surface — PARTLY CLOSED v1.216.1.** The
  Projects folder picker was reported as "cut off by the top", and it was this
  exact defect. MEASURED on /projects at 1440x760 with the New-project box
  open: the overlay was `x=17 y=167 w=1406 h=411` — the `<Card>`, not the
  window — and the dialog moved from `y=106` to `y=6` when the page scrolled
  200px, because a `fixed` element pinned to a card travels with it. After the
  fix: overlay `0,0,1440,760` and the dialog at `y=103` at every scroll
  position. `FilePickerModal` (6 render sites across documents, filesearch,
  projects and projects/[id]), `CompactionCard` (proven inside the chat card)
  and `ShareChatDialog` now go through `Modal`.
- [ ] **Two overlays still write `fixed inset-0` inline:**
  `terminal/FilesPanel`'s preview and the creative lightbox. Both were left
  deliberately, not overlooked: each puts `role="dialog"` on the OVERLAY rather
  than an inner box and carries its own tab trap (`onKeyDown={trapTab}`), so
  converting them is a focus-management change rather than a wrapper swap, and
  neither has been MEASURED as currently affected. The predicate is unchanged:
  does the overlay have an ancestor with `backdrop-filter`, `transform`,
  `filter`, `perspective` or `contain: paint`? Drive the surface and read the
  overlay's `getBoundingClientRect()` — if it is not the viewport, it bites.

## Build / herdr wave (v1.217.0)

Ported from herdr (a terminal multiplexer built for coding agents) in the app's
own shape, after the user asked to compare Build against it. What landed: a
pane-state classifier (`terminals/agent_state.py`), a per-pane chip and a
"needs you" summary strip, named panes, an injected `IRONJARVIS_*` identity,
and five agent-facing pane tools on the Builder and Maintainer rosters.

- [x] **`blocked` missed an approval that had no selection caret — FIXED in
  this wave.** Found by driving a REAL PTY, not by reading the regexes: every
  phrase pattern keys off wording ("do you want to proceed"), and the one shape
  rule wanted `❯`, which is absent on unselected rows and sometimes lost with
  the ANSI. A CLI drawing `Edit src/app.py?` / `  1. Yes` / `  2. No` reported
  `unknown` — the one wrong answer this feature cannot afford. Now a question
  plus ≥2 numbered options counts, and BOTH halves are required so ordinary
  prose ending in "?" cannot arm it.
- [x] **An ANSWERED approval outranked live work — FIXED in this wave.** Same
  PTY drive, two commands later: a resolved prompt sat in the scanned window
  and kept the pane pinned to "needs you" while a spinner ran below it. BLOCKED
  now loses to any progress printed BELOW it; progress ABOVE it is the turn
  that led up to the question and still reads as blocked.
- [x] **The injected identity reached nothing — FIXED in this wave.** The first
  cut set `pane_env_extra` AFTER the spawn with a comment saying it applied to
  "anything the pane starts next". Nothing applied it; `pane_env()` had no
  caller in the codebase. The id is now minted before the spawn and the
  variables go into the shell's own environment, so every CLI launched in the
  pane inherits them. `tests/test_pane_identity_v1217.py` asserts the ENV THE
  BACKEND WAS HANDED, because a dict-shaped assertion is what let the gap
  through.
- [x] **`name` and `agent_cli` had no way in from the product — FIXED in this
  wave.** `name` was settable only at creation and the New-terminal button does
  not ask for one; `agent_cli` was known only to the browser, because
  `launchCli` types into an already-running shell. `PATCH /terminals/{id}`
  (partial, empty string clears) plus an in-header rename and a launch report
  close both. Same reachability lesson as Raster Studio: a green suite proves
  the library, not that a user can get to it.
- [x] **v1.217.0 shipped the STATES and not the SHAPE — FIXED v1.218.0.** The
  user opened Build and said "it looks the exact same, no tabs to see different
  terminals with a status pane on the left", and that was exactly right: the
  classifier was real, and it was wired into a free-form canvas of overlapping
  windows — the thing "never hunt for the stuck one" exists to replace. Worse,
  it was invisible by construction, because a chip renders nothing for
  `unknown` and every pane holding a plain shell IS `unknown`. Build now opens
  on `components/terminal/PaneRail.tsx`: every live pane in one column with its
  state, one pane in focus beside it. The canvas survives as a mode
  (`ij.build.shape`) because seeing three panes at once is a real way to work.
  THE LESSON, and it is not a UI lesson: shipping the mechanism is not shipping
  the feature. Reachability was checked for the pane NAME in v1.217.0 and not
  for the thing the whole wave was about.
- [x] **A launched Grok/Gemini/Aider pane reported as a plain shell — FIXED
  v1.219.0.** `known_clis()` returned the three CLIs with SNIFF PATTERNS, and
  `_detect_cli` gated the Launch catalog's hint on that same list. So the
  browser reported "I just started grok here", the daemon threw it away, and
  the rail said `shell` — for a CLI started from a menu this app owns. It read
  the sniffable set as if it were the knowable set. `known_clis()` now reads
  `AI_CLIS`, so a CLI added to the catalog becomes classifiable in the same
  edit, and the two lists cannot drift.
- [x] **The rail named the CLI by its internal id — FIXED v1.219.0.** Rows read
  `· grok` rather than `Grok CLI`. The label comes from the same catalog the
  Launch menu renders, so the rail says what the user clicked; an id the
  catalog does not know still shows, because a name we half-know beats none.
- [x] **A pane could only be renamed from its own header — FIXED v1.219.0.**
  Which meant naming five panes required visiting five panes. The rail is where
  you see them all, so it renames them: a pencil on hover and a double-click on
  the name, both leaving the plain click to do what the rail is for.
- [ ] **The "needs you" summary is Build-page-only.** herdr's framing is "never
  hunt for the stuck one", and a user on Chat or Documents still learns nothing
  about a blocked pane. A sidebar count is the obvious completion and was left
  out deliberately: this wave was scoped to the Build module, and it would add
  a global poll plus an edit to the shared `Sidebar.tsx`. Do it as its own
  change, with the poll folded into an existing app-wide fetch rather than a
  new one.
- [ ] **The rail does not preview a pane you are not on.** herdr's list shows a
  live snippet per agent; ours shows the state and the CLI. The parts already
  exist — `termPeek` keeps each pane's last output line for the peek strip —
  but the rail rows would need it lifted per-pane rather than for the focused
  one. Worth doing once the rail has been lived in; skipped here to keep the
  first cut of the shape legible.
- [ ] **`seen`-ness is per-browser, so `done` is too.** The daemon reports the
  settled state as `idle` and the page downgrades it to `done` using its own
  unseen-output tracking. Two windows open on Build therefore disagree about
  which panes finished unwatched. Correct for the single-window daily driver
  and wrong for nothing today; worth naming before anyone moves the downgrade
  server-side, where it would be wrong for everyone.

- [ ] **v1.213.0 reviewer nits (all bounded, dated 2026-08-24):** Pi/opencode
  usage folds count an unknown hosted vendor as local (fails-open on
  unrecognised, documented convention); Pi CACHE tokens (310M real on this
  machine, 120:1 vs everything else) ride only the source block, never the
  merged totals or any UI; stripAnsi leaks an OSC split across frames and the
  overflow of a >256-char OSC payload (VS Code shell-integration OSC 633 is
  the likely trigger); the peek strip halves the bottom resize grab band and
  overlays the chat composer's bottom ~24px while visible (≤15s); a closed
  pane's quiet-window timer can fire one harmless re-render; textTail
  regexes the whole streamed text per render (slice(-4*cap) first would
  bound it). `pi_sessions_dir`/`opencode_data_dir` are settable via the API
  but rendered by no Settings surface.
- [ ] **New v1.210.0 codebase-vocabulary signals have known read-only false
  positives** (reviewer finding, dated 2026-08-23): "node.js"/"deno" runtime
  names arm `read_file` via the extension list; "section 179 of the code" with a
  read verb within range matches the codebase rule (the verb-less IRC phrasing
  is pinned safe); `bob@notes.md` reads as a filename (`@` missing from the
  lookbehind); `code  base` (double space) is missed. All award AUTO_SAFE
  read-only tools, so cost is an ignored armed tool — tighten when convenient.
- [ ] **`ironjarvis file-search` surfaces a bad regex as a Typer traceback.**
  `daemon/cli.py` calls `platform.filesearch.search` bare; the HTTP route
  returns a clean 400. One `try/except BadSearchPattern`.
- [ ] **Change-intent scorer: remaining known gaps.** Verb-list limits, not
  position limits — "this needs converting to pdf", "the fee should be changed
  to 3000", "this spreadsheet needs fixing". Closing them means adding
  inflections, which widens every sentence those rules see; that sweep was not
  run. Live list: `tests/test_autoselect_gaps_v1196.py::_STILL_OPEN`.
- [ ] **The attachment block can NAME a verb the 6-tool cap then drops.**
  `live_file_line` renders what the gate wanted; `_resolve_armed_tools`
  truncates afterwards. Always the safe direction (over-naming, never
  over-arming) but still a residual inaccuracy. Closing it needs
  `_prepare_attachments` to see the resolved armed list.
- [ ] **With Auto OFF the block still names the READ verbs without arming
  them.** Same safe direction as above; the CHANGE half is honest.
- [x] **Scorer cost — CLOSED in v1.196.0, recorded for the history.** Fronting
  fourteen rules with the imperative test made a whitespace-heavy paste
  backtrack quadratically (~17 s on 4,000 newlines). Possessive quantifiers
  cut it ~90x to ~200 ms, and every caller — both chat lanes, the attachment
  consent gate, and `agents/runtime.arm_for_task` — now hops to a worker
  thread. It is NO LONGER on the event loop; the residual ~200 ms is paid off
  it. Pinned by `tests/test_arming_offload_v1196.py`.
- [ ] **`batch_documents` is deliberately NOT auto-armed** (it fans out one
  model call per document and keeps an IRREVERSIBLE default; it stays one click
  away in the "+" menu). Recorded as a DECISION so it is not re-filed as an
  oversight — see the note in `tools/autoselect.py`.

## Needs validation before any fix (found, never reproduced)

- [ ] **DocPreview save-copy busy flicker** — the 409-overwrite confirm
  recurses inside the catch; the outer `finally` clears `saving` while the
  overwrite POST is still in flight (buttons re-enable mid-save).
  `dashboard/components/chat/DocPreview.tsx:252`
- [ ] **Already-finished session strands the live card** — `GET
  /sessions/{id}/stream` subscribes without checking terminal state; a run
  that finishes in the fetch→subscribe window leaves "waiting for the first
  token" forever. `daemon/routes/sessions.py` + `sessions/[id]/page.tsx`

## Wow wave 1 — five small connections, one story — **SHIPPED v1.168.0** ✔

*All five below landed in v1.168.0, plus kanban team nesting and
promote-to-knowledge from the lists further down.*

- [x] **Redaction receipt** — after `redact_pii`, diff source vs. redacted in
  DocPreview (the diff engine already exists), badge removed spans by
  category. The nervous pre-email check becomes a shown proof.
- [x] **Job deliverables on the session page** — render the ledger-proven
  `files_created/changed` from `GET /sessions/{id}/result` as an ArtifactsRail
  on session detail, per delegate in the TeamTree.
- [ ] **Undo where you look** — join rail/TurnReceipt paths against the undo
  journal; "Undo this write" under the message that did it.
- [x] **Origin chips** — `Session.origin` is indexed and serialized; render
  it (schedule:nightly · comm:telegram · job:agents · autonomy) on session
  rows + detail, with a filter.
- [x] **The bell knows it's waiting on you** — fold workflow runs parked on
  an *ask* step (question text in `waiting_json`) into the notification bell
  with an inline answer box.

## More small wins

- [x] Kanban nests delegation teams under the parent card — shipped v1.168.0.
- [x] Promote any chat answer / rail file into Project Knowledge — shipped v1.168.0.
- [ ] `terminal_tail` tool — chat reads the pane you're staring at (read-only).
- [x] Project heartbeat — shipped v1.169.0.
- [ ] Cost per client project — GROUP BY `project_id` over session tokens on
  the Usage page; per-engagement cost line on project cards.
- [x] Compaction summary inspectable any time — shipped v1.169.0.
- [x] Model report card — shipped v1.169.0.

## Medium bets (each ≈ one wave)

- [ ] **Client-scan triage** — attach a 60-page intake scan → proposed named
  split ("p.1–3 W-2 Acme, p.4–9 1099-B Schwab") → confirm → mechanical,
  journaled cut. The single most tedious tax-intake chore.
- [ ] **Batch extractions → client memory** — file batch_documents'
  facts/entities/figures into the project's knowledge (suggest-don't-act);
  months later recall answers "what was on the 1099-B?" with the source path.
- [ ] **Roll-forward guard** — accounts_diff → remap ranges → excel_edit →
  formula_check every rewritten cell; month-end proof instead of hope.
- [ ] **DraftCard → real Outlook draft/send** — per-call to/subject on the
  email channel + one route; confirm-first; ends the paste roundtrip.
- [ ] **Standing intake watch** — "watch this folder" chip → nightly batch
  (content-hash resumable, so quiet nights cost nothing) + morning digest.
- [ ] **Command palette searches the whole fabric** — one thin GET over
  `MemoryFabric.recall`, scoped to the active project.
- [ ] **Variables drawer** — list the session namespace's bound names
  (type/size, preview a slice, dispose); makes `_store_as` tangible.
- [ ] **Undo preview** — show the diff of what will come back before the
  Revert button; warn on drift up front.
- [ ] **Creative gallery → iterate in chat** — tile action opens chat with
  the image pre-attached through the vision path.
- [ ] **Terminals join the spine via cwd** — a pane under a project root is
  that project's terminal; assist gains project grounding.
- [ ] **Living documents, project-grounded** — add `project_id` to
  LiveDocRecord; regeneration gains project knowledge; freshness on the
  project surface.


## Workflows wave — SHIPPED v1.170.0 ✔

*Chat runs saved workflows (workflow_list/workflow_run tools + prompt block +
live run chip + "+"-menu), name-only POST /workflows/run resolves pin
server-side (reflex/canvas pin drops fixed), parameterized runs (inputs →
pre-seeded outputs), verified steps (expect: files/summary_contains against
ledger evidence), tool-step data handoffs ({{Step.data}}), resume interrupted
runs, PATCH rename, save-time validation, run pruning, canvas ask-gate +
DAG honesty + one serializer, 5 starter workflows, /workflows in default nav,
workflow:<name> origin stamped.*

Still open from the workflows analysis:
- [ ] Adversarial-verify step kind (fresh-context reviewer step) — expect
  checks shipped as the deterministic v1.
- [ ] workflow_list/workflow_run for headless agents (chat-first shipped;
  agents/types.py allowlists deliberately not extended yet).
- [ ] Webhook/email/calendar/api trigger stubs in triggers.py remain stubs.

## Deferred from earlier waves (still valid)

- [ ] Per-step agent re-grounding; workflow-engine memory tools; agent
  `add_knowledge` tool (v1.141 deferrals).
- [ ] Schedule pause/enable toggles (W-B deferral); skipped-week schedule
  still notifies.
- [ ] "Workflows that prove their work": verified steps (declared outcomes +
  ToolInvocation evidence), adversarial-verify step kind, step-level
  checkpointing, artifact handoffs (concepts from the 2026-08-06 analysis).
- [ ] Terminal survival backlog (assessed 2026-08-04, user-deferred).
- [ ] Remote-agent health probe (cached), and a session-shaped run for
  remotes beyond the supervisor bridge.
- [ ] Agent-type selection on schedules (today every scheduled task runs as
  builder).
- [ ] `origin` populated by the remaining callers (chat/user_task/comm/
  reflex/workflow lanes pass None today).

## Known limits (documented, not bugs)

- Packaged installs can't run `run_code` unless Python is on PATH (the
  `repl` tool is the packaged-safe path).
- Legacy Office formats (.doc/.xls/.ppt/.odt) must be converted before
  preview/extraction.
- Images are described, not OCR'd, in previews (scanned-PDF OCR exists on
  the read path).
- The REPL's write-confinement is a guardrail against a careless model, not
  a sandbox against hostile code (its own docs say so).
- A restart loses in-flight sessions (they reconcile honestly as FAILED
  "interrupted by a daemon restart"; QUEUED rows included).


---

# Agent mode: one surface, no pre-configuration — SHIPPED v1.178.0 ✔

*Written 2026-08-16 from a live product observation by the user, after five
consecutive releases in which the acceptance job ("rename all files in this
folder", 26 real tax documents) failed for a different missing-capability
reason each time.*

**The observation.** Chat already works the way the whole app claims to: leave
Auto on, describe the job, and `chat_turn.py` calls `select_auto_tools` to arm
what the turn needs. The AGENT lane does not. It takes a STATIC roster fixed at
definition time, so the user is asked to have pre-decided what a job would
need — which is exactly the "mode picker" the product thesis says it does not
have. Every failure this week traces to that: `rename_file` absent from the
builder, the worklist on the supervisor only, `view_image` registered
platform-wide and on no roster.

**The principle to build to.** Separate agents are for HANDOFF AND TEAM SHAPE,
not for capability gating. When one human asks the app for work, "may it use
this tool?" is a PERMISSION question — already answered, fail-closed, with a
deny floor — and the roster is a second, redundant gate doing the same job
worse. A narrow roster stays available as a deliberate opt-in (a reviewer that
genuinely cannot write), never as the default a user inherits by accident.

## P0 — the live bug (ship alone, as a patch)

- [x] **A dashboard-created agent has NO tools.** `SetupCard.tsx:322` posts
  `tools: []` hardcoded — there is no picker in the UI — and
  `dynamic.py:200` passes that straight through as the definition's tool
  list. Every custom agent made from the Agents page holds an empty roster.
  Fix: an empty/absent list means "inherit the base type's tools", never "no
  tools". Nobody creates an agent intending it to hold none. This is the
  SIXTH roster gap in a row and the first the UI itself creates
  (history_search v1.142, workflow_list v1.172, view_image v1.174, the
  worklist v1.177.0, rename_file v1.177.2).

## P1 — capability-based arming for agent runs

- [x] **Reach `tools/autoselect.select_auto_tools` from the agent runtime**, as
  both chat lanes do. A solo human->app run arms what the task plausibly
  needs; permissions gate the rest. NOT "hand every agent all ~60 tools": the
  default provider here is a local model, and the measured failure mode is a
  model choosing `shell` over `read_file` when both are in front of it. More
  schema is its own regression — trading a missing-tool failure for a
  wrong-tool one.
- [x] **Keep the explicit roster as an opt-in** on the agent record: empty =
  auto, non-empty = exactly these. The constrained specialist is a real use;
  it just must be chosen.
- [x] **A roster-coverage test.** Assert every tool the acceptance jobs depend
  on is reachable by the agent type that runs them. Five gaps in five
  releases is a pattern, not a coincidence, and it deserves an assertion
  rather than another retrospective.
- [x] **The tools picker in the UI** — LAST, and mostly for building the
  opt-in specialist above. Shipping the picker first would only make the
  manual configuration the user does not want slightly less annoying.

## P2 — the Agents surface reads as a room, not a form

- [x] **Roster rail down the LEFT of the module.** `AgentFace` (v1.171.0)
  already renders deterministic faces + moods; move them out of the pickers
  into a persistent left rail: click a face to select that agent, or to
  resume the thread already in flight with them. Live state on the face
  (working / error / idle) is already modelled by `moodForStatus`.
- [x] **A gear-with-a-face at the bottom of the rail** — one affordance that
  configures a NEW agent, local or remote. Remote already has its own
  create/PATCH path (v1.164.0, token never returned); local is the dynamic
  registry. One door, two kinds.
- [x] Selection drives the job-post card and the thread view, so "continue
  working with this one" is a click, not a picker round-trip.

## P3 — a thread's worth outlives the thread

- [x] **"Extract and add to memory" on an agent thread.** Chat already has
  exactly this at `POST /chat/threads/{id}/remember` (distill budget,
  verbatim-excerpt fallback with no model). Agent threads
  (`agents/threads.py`) have no equivalent, so a decision reached between
  agents dies with the thread. Reuse the chat path rather than writing a
  second distiller — two implementations of "what mattered here" will drift.
- [x] Honest-mock rule applies: no real model => no distillation, offer the
  verbatim excerpts instead. Never fabricate what a thread concluded.
- [x] Land it as a review step, not a silent write: show the extracted items,
  let the user drop any, then commit.

## P4 — the agent asks for the tool it needs

- [x] **Agent-proposed capability, user-approved.** When a job would go better
  with a tool/MCP server/connection that is not present, the agent should be
  able to PROPOSE it and the user approve — the app already has every piece:
  `tool_create` (argv-template custom tools, gated under `custom:<name>`,
  default ask), the MCP client, and `memory_propose` as the established
  suggest-don't-act shape (proposes, changes nothing, waits for a click).
- [x] Model it on `memory_propose`, NOT on `tool_create` directly: a proposal
  record the user sees, with what it would add, why, and what it would be
  allowed to do. Approval is the only thing that creates it.
- [x] The deny floor still holds: `shell`, `repl`, `browser_use`,
  `web_action`, `mcp_call` can never be raised to `allow` by a definition, so
  a proposed tool cannot smuggle host reach in through the side door.
- [x] This is the honest closing of the five-gap pattern: when the app lacks a
  verb, the agent should be able to SAY SO and ask, instead of flailing in
  `shell` writing PyMuPDF scripts to re-read PDFs it had already read
  (measured, run_ab82dea4bf8a, v1.177.1).

## Ordering rationale

P0 is a live bug and ships alone. P1 is the substance — fix the default and
the picker becomes optional polish. P2 is the surface that makes agent mode
feel like a room you walk into. P3 and P4 are independent and can land in
either order, but P4 should follow P1: proposing new capability only makes
sense once the existing capability reliably reaches the agent.

## Carried forward from the v1.178.0/v1.179.0 waves — **ALL CLOSED v1.185.0** ✔

*Eight reviewer-found defects, diagnosed across two waves and carried through
six releases. They shared a shape worth naming: each was a place where the app
said something CONFIDENT about a fact it did not have, or kept one truth in two
places that agreed only by handshake.*

- [x] **Chat's remember ladder was a closure, so it existed twice.**
  `remember_chat_thread` lived inside `register(app, d)` — no importable
  symbol — so `agents/threads.py` re-derived the distill/verbatim ladder and
  reached into a ROUTE module for the budgets (the layering upside down). Now
  `memory/commit.py` owns the ladder and both routes call it, with the system
  prompt as a PARAMETER: a panel prompt must attribute claims per agent and
  never resolve a disagreement the panel left open, both meaningless for a
  two-party chat.
- [x] **`_effective_tools` returned `[]` on its except branch**
  (`daemon/routes/agents.py`), so `[]` meant both "unknown" and "genuinely
  none" — opposite instructions to the card. Returns `None` for unknown; the
  clients already spoke that dialect (`effectiveOrNull`, `ToolOrigin`
  "unreported").
- [x] **`LoaderInline`'s spinner ignored `prefers-reduced-motion`.** Guarded on
  the CLASS in `globals.css`, so all ~12 direct users of `animate-spin-slow`
  are covered at once; `role="status"` + an always-present label is the other
  half, since a stopped spinner with no text reads as an idle icon.
- [x] **`GET /capability/proposals` returned EVERY proposal ever filed.** The
  status filter shipped in v1.178.0 itself; the DEFAULT response was the
  unbounded part. Capped at `LIST_LIMIT`, ordered so the cap only ever bites
  into decided history, and `returned`/`truncated` report it.
- [x] **`approve()` was not atomic against a concurrent second approve.** The
  handlers are sync `def`, so FastAPI runs them in worker THREADS and the race
  was real: both clicks passed the guard and `_apply` ran twice. A claim now
  spans the whole sequence, with the PENDING check re-read inside it.
- [x] **`ListAgentsTool` did not emit `effective_tools`** — and the fix had to
  land in `output`, not `data`: the runtime hands the model `result.output` and
  nothing else.
- [x] **`PanelPicker` framed itself as "choose the panel", not "add one more".**
  Derived from `initialParticipants` rather than a new prop, because it is true
  of both edit call sites. A pending eviction is now named before the click.
- [x] **The gear and SetupCard held two independent open states over one
  localStorage key.** Split into the two questions they actually were —
  visibility (this visit, never persisted) and disclosure (the card's chevron,
  remembered) — with the page owning both and the card taking `open` as a prop.
