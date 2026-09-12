# Iron Jarvis — Agent Operating Manual

You are working on Iron Jarvis: a local-first AI operating system. One Python
daemon (FastAPI), one Next.js dashboard, one Electron desktop wrapper. The user
runs the PACKAGED desktop app daily — treat every change as production.

## The three processes

| Process | What | Port | Source |
|---|---|---|---|
| Daemon | FastAPI, all state + agents + tools | 127.0.0.1:8787 | `src/iron_jarvis/` |
| Dashboard | Next.js 15 (43 routes), arc-reactor-cyan aesthetic | 127.0.0.1:8788 | `dashboard/` |
| Desktop | Electron: spawns both, tray, updates, Spotlight | — | `desktop/main.js` |

Packaged layout: PyInstaller-frozen daemon (`packaging/ironjarvis.spec`) +
Next standalone run by Electron's node + electron-builder NSIS installer.
State home: dev = `~/.ironjarvis` unless `--root`; packaged =
`%APPDATA%/Iron Jarvis/.ironjarvis` (config.toml, ironjarvis.db (SQLite),
secrets/, skills/, terminals.json, backups/). The desktop app's per-install
bearer token: `%APPDATA%/Iron Jarvis/token.txt` — every daemon request needs
`Authorization: Bearer <it>`.

## Commands

```bash
# Backend tests (4800+, offline). ALWAYS run before shipping.
# Serial ~16min; -n auto runs one worker per core (~4.5min) and is what CI
# uses. Measured parallel-safe over three runs — identical pass counts.
uv run pytest -q --no-header -n auto
# Dashboard build (must show "Generating static pages (43/43)")
cd dashboard && pnpm build
# Syntax-check desktop changes
cd desktop && node --check main.js
# Dev run
uv run ironjarvis serve            # daemon on 8787
cd dashboard && pnpm dev           # dashboard
```

## Release flow (how the user receives your work)

**EVERY PUSH GETS A VERSION BUMP. No exceptions, and the exceptions are the
point.** Not "every user-visible change", not "every product change" — every
change you push. A test-only fix, a comment, a doc edit, a CI tweak: bump it.

This is a standing instruction from the user, given after v1.214.0 shipped and
two follow-up commits went to master WITHOUT a bump. The reasoning that
produced those commits sounded good — "no product code changed, so a bump would
push an identical installer to the daily driver" — and it is exactly the
reasoning this rule exists to overrule. Do not re-derive it. The version is how
the user knows what is on master and what they are running; a commit with no
version is a change they cannot name, and deciding on their behalf which of
their changes deserve a number is not your call to make.

So: if you are about to `git push`, you have already edited the three files
below. If you find yourself writing a commit message that explains why this one
does not need a bump, stop and bump it.

1. Bump the version in **three files, with ANCHORED edits** (never blanket
   search/replace — it once rewrote a dependency pin): `pyproject.toml`
   (`version = `), `src/iron_jarvis/__init__.py` (`__version__`),
   `desktop/package.json` (`"version"`).
2. Commit + push to master. CI (`.github/workflows/release.yml`) detects the
   bump, RUNS THE OFFLINE SUITE AS A GATE (the `suite` job; the installer job
   `needs:` it), PRE-CREATES the tag+release (electron-builder 422s otherwise),
   builds the frozen daemon + installer, publishes `Iron-Jarvis-Setup-X.Y.Z.exe`
   + blockmap + `latest.yml` (~35 min now that the gate runs first). A push with
   no version bump skips the gate AND the installer entirely — which is why an
   unbumped push is not a cheap shortcut but a change that never reaches the
   user at all, and why the rule above is absolute.
   **The gate is new in v1.177.0 and this file used to claim it already
   existed.** It did not: `Tests` and `Release` are separate workflows and ran
   CONCURRENTLY, so on v1.176.0 Release published a green installer in 8 minutes
   and Tests went red 16 minutes later — a red suite shipped to the user's
   daily driver, which auto-downloads. If you ever split these again, the
   installer must still not be reachable without a green suite.
3. The desktop app auto-downloads (checks at boot + every 30 min) and installs
   only when the user clicks Restart-to-update (tray item / notification /
   Updates page). `latest.yml` missing assets = release still uploading.
4. **State the current (or new target) app version in EVERY response** so the
   user always knows which version to expect when they pull an update. The
   SessionStart hook (`.claude/session-start.sh`) injects the live version +
   repo state at session start — trust it over stale docs.

## Hard rules (each one was learned the expensive way)

- **The identity spine reaches EVERY prompt seam** (v1.144.0). `profile/`
  renders the user's profile and `personas/voice.py` the assistant's voice;
  both are appended in `daemon/chat_turn.py`, the `/chat/stream` mirror in
  `routes/chat.py`, `agents/runtime.py`, and `agents/threads.py` (the round
  table takes `include=("how",)` only — panelists must stay distinct). A NEW
  surface that talks to the user adds its injection in the same change:
  `tests/test_profile_v1144.py::test_profile_reaches_every_prompt_seam` drives
  all of them end-to-end, and each seam is mutation-proven. "Chat has it,
  agents don't" is the exact bug that wave existed to fix.
- **History is BUDGETED, never sliced** (chat v1.146.0, agents v1.152.0). Both
  chat lanes call `_plan_context` → `context.plan_history`; the perceive→act
  loop calls `context.agent_window.plan_agent_transcript` once per step. Both
  fit the transcript to the answering model's window (`_context_window`: pin →
  measured envelope (v1.201.0, probed/partial/tuned profiles only — seeded and
  trusted never speak here) → fleet probe → default) and report what they
  dropped. Do not reintroduce a fixed
  `messages[-N:]`, and if you add to the system prompt, add it BEFORE the
  planner runs or its cost is invisible to the budget.
- **Compaction is MODEL-written and LEDGER-checked** (v1.153.0). `context/
  compaction.py` lets a model write a real structured summary of the older
  conversation, then removes every line carrying a claim the record will not
  support: file paths must appear in the transcript or in
  `agents/outcome.session_result` (derived from `ToolInvocation` +
  `UndoJournal`), and quoted spans must appear verbatim. A summary that
  survives verification empty is NOT shown — the deterministic recap keeps the
  job. Same honest-mock rule as skill distill: no real model ⇒ no summary,
  because this text is injected into the system prompt of every later turn and
  read back as authoritative. The two lanes differ by who is present: chat
  SIGNALS at `SUGGEST_AT` (0.70) and lets the user choose via
  `POST /chat/compact`, and only acts alone at `AUTO_AT` (~0.92); an agent run
  has nobody to ask, so it compacts itself at the ceiling and emits
  `context.compacted`. Pressure is measured on RAW demand (`raw_tokens`), never
  on the planned transcript — that fits by construction and so can never report
  the 70% the whole feature keys off. Coverage always restarts from the
  beginning and feeds the previous summary back in as `prior`; covering only
  the new blocks would silently discard everything the first summary said.
- **An assistant turn and its `role="tool"` results are ONE unit** (v1.152.0).
  Any code that trims, slices, or replays an agent transcript must move them
  together — a `tool_use` without its `tool_result` makes strict providers
  reject the ENTIRE conversation, so a context fix that splits them is worse
  than the overflow it prevents. `plan_agent_transcript` sacrifices in order:
  stale tool output → whole blocks (oldest first) → the task itself, clipped.
  The task is `messages[0]` and is never dropped; dropped work is summarized
  into the SYSTEM prompt, never injected as fake assistant turns.
- **Keep big results OUT of the context, don't trim them after** (v1.159.0).
  `repl/` is a per-session persistent Python namespace (a subprocess speaking
  newline-JSON, spawned by re-executing the app itself via the hidden
  `repl-worker` subcommand — `run_code` uses `shutil.which("python")` and so
  cannot run Python at all on a packaged install). Any tool call may carry
  `_store_as="name"`: `registry.invoke` strips it, binds the result into that
  session's namespace and returns a ONE-LINE RECEIPT, and the `repl` tool then
  reaches the value by name. A 5,000-entry listing becomes `len(files)` and a
  slice. This is the counterpart to the budget (v1.152.0) and compaction
  (v1.153.0): those decide what to throw away once a payload has arrived, this
  decides what never has to arrive. The value crosses as a JSON string LITERAL,
  never interpolated as code — a tool result must not be executable. `repl` is
  on the DENY FLOOR and defaults to `ask`: it runs model-written code AND the
  namespace persists for a whole session, so consent to one call is not consent
  to what accumulates. `_store_as` is advertised only on `VERBOSE_TOOLS` —
  putting it on all ~60 tools would spend more context than the feature saves.
- **Every chat reply is ACCOUNTABLE where the user is standing** (v1.165.0).
  Three honesty mechanisms existed and ALL THREE missed the mock-answer
  incident: the downgrade event fired (ledger had it), the banner rendered on
  the Overview only, and the "answered by X" chip suppressed itself on the
  default route (it compared against the EXPLICIT pick). The fix is
  server-side truth: `RouteResult` carries `requested` (`""` = no explicit
  pick — the default's name is NOT echoed, or "picked X" and "default is X"
  become indistinguishable) and `reason`
  (explicit/default/failover/prompted-tools/auto-tier/local-oracle/mock), with
  `reason=="mock"` iff `provider=="mock"` (`_disclosed_reason` — applied at
  EVERY terminal site in BOTH `complete()` and `stream()`, a lock-step pair).
  Both chat lanes emit `route:{requested,provider,model,reason}`; error paths
  emit NO route (a half-built route is an authoritative-looking lie).
  `documents` now also merges `result.created_paths` from every SUCCESSFUL
  tool (failed tools' paths are excluded to match the undo-ledger convention;
  RELATIVE paths are dropped, not resolved — resolving a lying tool's path
  against a guessed base could disclose the wrong file). The dashboard renders
  this as the TurnReceipt under each reply (mock/failover/mismatch warnings
  amber and visible WITHOUT expanding; "prompted-tools"/"auto-tier"/
  "local-oracle" stay quiet — user-configured automation is not substitution),
  the ArtifactsRail (per-conversation files: preview/download/copy/dismiss),
  and the PreflightNote above the composer (the active provider is
  known-unreachable BEFORE the user types; watches the explicit pick, else the
  DEFAULT — silent for "auto" and on first-poll-failure). The legacy
  viaProvider chip and "used:" footer render ONLY for pre-v1.165.0 messages
  (`!m.route`) — showing both would say everything twice.
- **A remote agent is EDITABLE, and an edit must never eat its credential**
  (v1.164.0). The row offered only Test and Delete, so one wrong character in a
  base URL meant re-entering the record. `PATCH /agents/remote/{name}` +
  `RemoteAgentRegistry.update` do a PARTIAL update. Do NOT "simplify" this back
  into a re-POST of the create body: `upsert` assigns EVERY column including
  `row.secret_name`, and the bearer token is stored encrypted and NEVER returned
  so no UI can prefill it — a re-post therefore sends an empty token and
  silently drops a working credential the user cannot retype, which is worse
  than the retyping it was meant to save. The token has THREE intents and
  conflating any two loses a secret: send one to replace, send NOTHING to keep,
  set `clear_token` to remove. An empty string is "I didn't type one", never
  "delete it" — mutation-proven, and the flag alone is not enough to assert
  (treating `""` as a new token leaves `has_credential` true while overwriting
  the vault entry with an empty string, so tests check the VALUE). `name` is
  deliberately immutable: panels and threads refer to a remote by name
  (`participantKey("remote", name)`), so renaming would orphan them silently.
- **A draft the user will SEND is fenced, boxed, and copied as RICH TEXT**
  (v1.161.0). `DRAFT_BLOCK` in `daemon/chat_turn.py` tells the model to wrap an
  email/message it drafts for the user in a ```email fence with a `Subject:`
  first line; `components/chat/DraftCard.tsx` renders that fence as a compact
  card whose Copy writes `text/html` AND `text/plain` in one go, so pasting
  into Outlook/Gmail keeps bold, lists and links instead of arriving as literal
  asterisks. The HTML is read off the RENDERED DOM (`cleanHtml`) rather than
  generated a second time — two renderers drift, and reading the node the user
  is looking at makes a mismatch impossible — with `class`/`style` stripped so
  the app's dark theme never lands in a composer. **STRIPPING ALONE IS NOT
  ENOUGH (v1.163.0):** semantic HTML pastes into Outlook FLAT, because Outlook
  renders through WORD's engine and Word gives a bare `<p>` a ZERO margin — the
  blank lines a browser shows come from the browser's own default stylesheet,
  which never crosses a clipboard (measured under a zero-margin reset: 0px
  paragraph gap before, 13px after). So the strip is FOLLOWED by `EMAIL_STYLES`
  — inline margins in POINTS, Word's unit — while colour/font stay out so the
  text adopts the composer's theme. `hardenLineBreaks` fixes the other half: a
  single newline is a SPACE in markdown, so a signature block pasted as one
  line. Both live behind `draftFromFence`, which `chat/page.tsx` and the tests
  BOTH call — they used to hold separate copies of that sequence, and a
  mutation deleting the real call site left every frontend test green, so the
  CALL SITE is asserted from Python (`tests/test_draft_spacing_v1163.py`).
  THIS IS A THREE-PARTY
  AGREEMENT and every party fails SILENTLY: the instruction must reach BOTH
  chat lanes (the streaming mirror in `routes/chat.py` is the one users watch),
  and `DRAFT_LANGS` must keep naming the same word as `DRAFT_BLOCK` or the
  model emits a fence nobody renders. `tests/test_draft_card_v1161.py` asserts
  both. A degraded copy SAYS SO ("Copied as text"): the desktop bridge
  (`clipboard:writeHtml`, one `clipboard.write` carrying both flavours — two
  calls clobber each other) is preferred because `navigator.clipboard` can be
  permission-gated in Electron, and claiming a rich copy that did not happen is
  a lie about the only thing the card does.
- **The REPL's writes are CONFINED; its reads are not** (v1.160.0). Every file
  tool routes through `core/fs_policy`; the `repl` child routed through nothing,
  and it was measurable — `read_file` refused the app's own Fernet key while a
  cell printed it, and a cell writing to an absolute path outside the workspace
  succeeded while `created_paths` said `[]`, because that diff only ever scans
  INSIDE the workspace. An invisible write is worse than an untidy one.
  `repl/worker.install_confinement` arms a `sys.addaudithook` before any cell
  runs; `repl/session.confinement_env` computes the policy from `fs_policy`
  (the worker is stdlib-only and cannot import it). READS STAY BROAD ON
  PURPOSE — the user's tax documents live all over the disk and a REPL that
  cannot open them is a worse tool — so only protected roots and an explicit
  `IRONJARVIS_FS_ALLOWLIST` restrict reading. WRITES pin to the workspace (the
  grounded project's folder when chat resolved one) plus a PRIVATE scratch dir:
  the whole system temp root would expose every other program's temp files, and
  with no redirect at all `tempfile` probes, fails, and silently falls back to
  `os.getcwd()` — filling the user's project with `tmpXXXX` files. Subprocess
  spawning and NEW `ctypes` loads are refused because each walks around the
  rule; `ctypes` is imported BEFORE the hook is armed, since Windows evaluates
  `windll.kernel32` at import time and a blanket refusal breaks `import ctypes`
  itself. `PYTHONDONTWRITEBYTECODE` is set for tidiness, NOT correctness —
  `importlib` swallows a refused `.pyc` write. Be honest in any doc you write
  about this: an audit hook inside the interpreter it polices is a guardrail
  against a careless model, NOT a sandbox against hostile code.
- **NOTHING BLOCKING RUNS ON THE EVENT LOOP** (v1.153.1). The daemon is ONE
  asyncio loop, so a synchronous filesystem walk, a big file read, or any
  CPU-bound work inside a tool freezes every request in the app — and it does
  not look like a freeze. It looks like "Daemon offline": the dashboard's fetch
  times out, `lib/api.ts` maps a dead fetch to status 0, Retry issues another
  request onto the same blocked loop, and no threads load. That was a real
  four-hour outage on the user's install, diagnosed as 84% CPU with the
  MainThread parked in `pathlib.is_file` under `ListFilesTool.execute`. Any
  tool touching the filesystem or CPU goes through `asyncio.to_thread` AND is
  bounded — `tools/builtins._walk_files` caps
  entries, enforces a deadline, and prunes heavy dirs with `os.walk` (`rglob`
  cannot prune). Truncation is always REPORTED: a silently short listing reads
  as complete and the model then says a file does not exist.
  **CHECK WHICH IMPLEMENTATION IS REGISTERED** (v1.175.0). This rule was
  obeyed by `tools/builtins.ShellTool` — and that class is DEAD CODE:
  `platform.py` registers `sandbox/shell_tool.SandboxedShellTool` under the
  same `shell` name, and for two years it ran `subprocess.run(shell=True)`
  straight on the loop. The protection sat in the shadowed copy where every
  reader (and this file) kept finding it. Both are offloaded now
  (`sandbox/shell_tool` hops ONCE for `manager.get()` + `sandbox.run()` —
  the Docker probe is a socket round-trip that hangs when Docker Desktop is
  wedged — and `tools/dynamic.CommandTool` for every `custom:*` tool), and
  `tests/test_event_loop_offload_v1175.py` asserts the worker thread AND that
  the loop kept ticking. When a rule cites a class, confirm that class is the
  one the registry actually holds.
  **A CANCELLED AWAIT DOES NOT CANCEL THE THREAD, AND A DEADLINE IS A
  RESULT** (v1.228.0, audit Wave 2). The offload above has a second half: a
  client disconnect (or Cancel) lands as `CancelledError` at the `await`,
  while the worker thread finishes and the write LANDS — and `_record` sat
  after `execute`, so 597 live resets could each leave an effect with no
  ledger row. `registry.invoke` now records a failed row + `tool.executed`
  on `CancelledError` from ONE independent task created before any await
  (Starlette's anyio cancel scope re-cancels EVERY later await of the
  response task; a shielded await there landed the row and lost the event),
  then re-raises. `sandbox/native.py` is Popen + `communicate(timeout)` and
  kills the TREE (`taskkill /T /F` / `killpg`) — `subprocess.run(shell=True,
  timeout=)` killed only cmd.exe and then blocked on pipes the command still
  held. And a tool call in an agent run has a deadline
  (`config.tool_call_timeout_s`, passed as `registry.invoke(deadline_s=)`):
  the timeout is an ORDINARY failed ToolResult through the same `_record` +
  `tool.executed` path, so it is one row with the right words and the run
  continues; both chat lanes pass it too since v1.246.0. The deadline is
  `asyncio.timeout(...) as cm`
  and only `cm.expired()` earns the deadline wording: on 3.11+
  `asyncio.TimeoutError` IS the builtin, so a `TimeoutError` the TOOL raised
  (socket.timeout, an inner wait_for) must keep its own message — a bare
  `except TimeoutError` around `wait_for` misattributed it and, with no
  deadline, crashed formatting `None`. A step streaming past `_MAX_STEP_STREAM_CHARS`
  closes the stream and ends the run FAILED with the reason.
- **Every door ends in `create_session`, so the folder and the ask live
  THERE** (v1.231.0, audit Wave 5, AE1/AE17). Six doors (schedule, reflex,
  goal, comm one-shot, comm escalation, autonomy) each handed the
  orchestrator a different subset of project/root/origin, and the two
  that mattered — the folder and the right to ask — were per-door: a
  schedule bound to a project ran in `workspaces/<sid>` and refused its own
  project files, and the runtime's ask allowlist (`ASKING_ORIGINS`) only
  admitted origins nobody at those doors stamped, so a phone-started job
  could never ask the phone. Now `create_session` resolves a project-tagged
  session's folder through `fs_policy.root_problem` (the ONE definition;
  `routes/projects._root_problem` and `usable_workspace_root` delegate to
  it) and records an unusable root on the row (`Folder note:` in
  `summary`, kept under the result by every finalizer); every door stamps
  `<door>:<name>`; and the allowlist admits the doors whose asks the
  v1.200.0 fan-out delivers. Do not add a door that passes `project_id`
  and resolves its own folder, and do not stamp an origin the allowlist
  has never heard of — `tests/test_execution_seam_v1231.py` drives each
  door end to end. `delegate`/`spawn_agent` (`SAFE_HEADLESS_TOOLS`) are
  exempt from the pause: the daemon grants them with nobody present, so
  asking a human about them is five minutes of noise.
- **A step that RAISES is a failed step, and a result is written when it
  LANDS** (v1.231.0, audit Wave 5, AE3/AE4). The tool branch of the
  workflow engine caught `Exception` and returned a failed output; the agent
  branch beside it caught only `CancelledError`, so a session that re-raised
  a provider error ("fleet down at 3am") escaped the attempt loop —
  no retry, no `on_failure`, no `workflow.step_completed`, a silent halt —
  and outputs were persisted once per BATCH, so a crash mid-parallel-group
  dropped every finished member and Resume re-delivered them. Two sibling
  branches that handle the same failure must handle it the same way, and a
  record that claims to make resume honest must be written per step, not
  per batch (`_exec_step` writes its own output; `_update_record` snapshots
  the dict because siblings now mutate it). `tests/test_workflow_engine_v1231.py`.
- **A cancel goes to the task's OWN loop, and a fire that did not happen is
  written where the user reads** (v1.231.0, audit Wave 5, AE2/AE9/AE11/
  AE12). A schedule fire runs under `asyncio.run` on the APScheduler thread;
  `task.cancel()` from any other thread neither wakes that loop's selector
  nor is thread-safe, so Cancel landed when the model call returned on its
  own. `cancel_session` compares `task.get_loop()` with the running loop and
  uses `loop.call_soon_threadsafe(task.cancel)` across loops (the cancel
  route is a sync `def`, so every route cancel is cross-thread). The
  scheduler's silent outcomes — APScheduler's 1 s default misfire grace
  turned a PC asleep at 03:00 into a WARNING in its own logger; a skipped
  overlap likewise; a fire the app was closed for left `next_run` in the
  past under an `ok` — are now rows: `misfire_grace_time=300` on recurring
  jobs, an `EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED` listener, and
  `start()` reconciling recurring rows to `missed` WITHOUT firing. Two traps
  in `scheduling/service.py`: cron/interval `next_run` is stored as the
  scheduler zone's wall clock while `last_run` is UTC (SQLite drops tzinfo),
  so each is read on its own clock; and `misfire_grace_time=None` means
  UNLIMITED, not default. Every trigger is handed the scheduler's zone —
  APScheduler re-resolves tzlocal per trigger, so a UTC fallback on the
  scheduler alone would have booted the daemon and then aborted `start()` on
  the first cron task. `tests/test_scheduler_trust_v1231.py`.
- **A refused credential is not an empty batch, and at-most-once must
  leave a trace** (v1.231.0, audit Wave 5, AE8/AE14). `TelegramChannel.poll`
  mapped EVERY non-ok answer to `([], offset)` and the IMAP poll wrapped
  `login` in the same blanket `except`, so a revoked bot token was a
  healthy `_tick("inbound", True)` every 15 s forever — the same disease
  as the v1.229.0 sampler, one layer down. A poll raises
  `comm.base.ChannelAuthError` for 401/403 / a refused LOGIN only (a 5xx
  keeps the fail-safe empty batch), `poll_once` turns it into an error
  row + `poll_errors[name]`, and the loop ticks off `poll_verdict` — read
  the status off the RAW response (`_get_raw` + `auth_refusal`), because
  `interpret_json` flattens a 401 to `None` before anyone can see it. The
  inbound offset is still persisted BEFORE handling (a duplicate remote
  action is worse than a dropped reply), but the update in flight rides
  the same write (`inflight_update_id`/`inflight_chat_id`) and is cleared
  only when `_handle` RETURNS — a `CancelledError` at shutdown must leave
  it — so the next boot apologises to that chat and publishes
  `comm.dropped`. Do not "tidy" the clear into a `finally`.
- **A rule that lives in one page's JSX is not a rule, and a delete must
  untag what SPAWNS** (v1.220.0, projects-module review). "An archived project
  takes no new tasks" was enforced by hiding the composer on
  `projects/[id]` — the chat module's Tasks surface and `POST
  /projects/{id}/task` never heard of it. Put the refusal in the route (it
  now answers "unarchive the project first", like `/activate`). Same review:
  `DELETE /projects/{id}` untagged sessions/threads/runs but left task
  schedules (payload `project_id`), goal contracts and reflex rules bound —
  each of those CREATES sessions with its project id, so the ghost project
  kept accruing work while `_project_context` found no row and grounded
  nothing, silently. Anything that carries a project id AND spawns must be
  untagged on delete (the response's `untagged` map is the checklist). Also
  found there: a project root skipped the `usable_workspace_root` door that
  `POST /sessions` applies to an explicit `workspace_root` (now
  `_root_problem`, checked at create/patch AND at task time for older rows);
  a knowledge file was staged at `<home>/uploads/<name>` — the exact path
  `/documents/upload` keeps the user's documents at — and clobbered them
  (now a one-off staging dir, removed after extraction); and a deliverable
  stem kept `:*?"<>|` and `..` (`_deliverable_stem`).
- **The roster is the CONTRACT, and the gate is in the registry** (v1.227.0,
  audit Wave 1). For two years the tool list an agent was shown was the only
  thing that limited it: `registry.invoke` looked the model's name up in the
  FULL registry, so a read-only Reviewer that emitted `rename_file` renamed
  the file, and chat with only `read_file` armed executed a `write_file` the
  model invented — allow-tier tools need no ask, `safe_path` confined the
  write, the undo journal caught it, and the green suite pinned only the
  nothing-armed case. `registry.invoke(..., allowed_names=...)` now refuses a
  name outside the armed set through the normal `deny_reason` path (ledgered,
  `tool.denied` kind `not armed`); the runtime passes its step's `tool_specs`
  names and BOTH chat lanes pass their armed set, lock-step. `None` keeps the
  old behaviour for callers you have not audited — do not pass it from a new
  lane. Same wave: every session row (`_session_view(session, d)`) carries
  `outcome` (the ledger's verdict on the JOB, separate from `status`) and
  `waiting_on` (the ask a paused run is parked on; `AgentState.WAITING` is
  now really set), and every finalize path releases the run's worklist claims.
  A test fake for `invoke` MUST take `**kw` (bit again this wave).
- **Model output is ALMOST JSON; recover it in ONE place** (v1.225.0). The
  daily driver's default model is a local fleet endpoint, and every workflow
  creation door assumed exact JSON: the OpenAI-compatible adapter turned a
  tool call with a trailing comma into `arguments={}`, the draft sanitizer
  returned None for a JSON-string `steps`, the chat lanes only made the card
  from the `workflow_draft` tool call, and the page generator 422'd "try
  rephrasing" on a numbered list. Each near-miss read as "workflows from
  chat are unreliable". `core/jsonish.loads_object` is the one recovery
  ladder (fences, prose around the object, trailing commas, single quotes,
  first balanced object — never invented content); the adapter, the
  sanitizer (`_sanitize_draft`: string args, string/numbered/sentence
  steps, key aliases) and `_draft_from_text` all use it; both chat lanes
  also accept an UNARMED `workflow_create` call as the card
  (`_draft_from_calls`), and the generator takes one repair round. A draft
  born in a project CARRIES the project (`workflow_draft["project_id"]`,
  both lanes) — Save used to write it unpinned and Run forced `""`. A step
  that cannot run is refused at SAVE time (`_validate_step_shapes`), and a
  run whose pinned folder is gone says so on the record (`notes_json`,
  `engine._project_folder`) instead of silently using a scratch workspace.
  **VALID JSON CAN STILL BE THE WRONG SHAPE** (v1.228.0, audit Wave 2): the
  live `KeyError: 'query'` was a model wrapping its call in an
  `{"arguments": "<json>"}` envelope — `json.loads` succeeded, so the ladder
  never ran. `jsonish.unwrap_arguments` peels an object whose ONLY key is
  `arguments` at BOTH adapter parse sites (`_parse` and `_parse_sse`, which
  now share the `loads_object` fallback), and `registry.invoke` checks
  `required` + top-level types BEFORE `execute`, answering `missing
  required: <k> — <tool> needs [...]; got [...]` through the same `_record`
  + `tool.executed` path as any failure. A traceback string names nothing
  the model can correct; a missing key does.
- **Armed is not healthy: a loop reports its OWN cycles** (v1.229.0, audit
  Wave 3). `_tick("fleet", True)` sat at ARM time and `FleetSampler._loop`
  swallowed every cycle at DEBUG; the Slack pump reconnects internally; so a
  sampler whose every cycle raised and a socket that never once connected
  both read `ok` on `/diagnostics` forever, while the boot rehydrate steps
  wrote `error` and every other loop wrote `last_error` — the dashboard read
  one key, rendered "N failed", and the hero said nominal above it. A
  background loop takes an `on_tick(ok, exc)` seam and reports after the
  work (`sampler.py`, `slack_socket.py`); nothing in `app.py` records ok
  before the first cycle has run. One failure key everywhere (`last_error` +
  `at`), and a failing loop is NAMED with its reason where the user is
  standing (Overview note in both modes, health list, bell after 5 min).
  Same wave (U4/D2): a skipped MCP server keeps its exception text on a
  load record (`mcp/tools.load_status`) that the Tools row, `/diagnostics`
  and the doctor `mcp` check read — a WARNING line is not a place the user
  looks. A PROBE must not write that record: `/mcp/servers/{name}/test` and
  the Connections test connect but register nothing, so they pass
  `mcp_tools(record=False)`, else a green Test cleared a boot failure while
  agents still held zero tools (the doctor also trusts the registry's live
  count over a clean record); and the desktop hotkey is a LADDER whose registered key
  (`hotkeyState`, IPC `shell:getState`) every surface reads — a hard-coded
  "Ctrl+Shift+J" in the tray, README and tips card was a claim the OS had
  already refused (Win32 1409).
- **A log handler on the app tree runs inside every `except` branch the
  daemon has** (v1.229.0, audit Wave 3, OBS5). `RecentErrorsHandler` (the
  ring behind `GET /diagnostics/errors`) first shipped with a bare
  `f"{exc}"` and an exception whose `__str__` raised turned
  `log.exception("scheduler failed to start")` in the lifespan into a boot
  abort — the log call that was supposed to RECORD the failure became the
  failure. Anything attached by `configure_logging` wraps its whole `emit`
  and ends in `handleError`; `tests/test_diagnostics_maintenance_v1229.py::
  test_ring_handler_never_raises_into_the_caller` pins it. Same wave: a
  restore over a LIVE home stages first and moves the DB with `os.replace`
  after deleting `-wal`/`-shm` (`maintenance.restore_backup_live`) — on
  Windows a held-open DB then fails loudly before any other file moved,
  which is the honest outcome; extracting straight over the home (what the
  CLI does with nothing open) would have written under an open connection.
- **Shipping the mechanism is not shipping the feature** (v1.218.0). v1.217.0
  built a real pane-state classifier, proved it against a live PTY, and put the
  answer into a chip that renders NOTHING for `unknown` — on a canvas where
  every pane holds a plain shell, which classifies as `unknown`. The user
  opened Build and correctly reported it "looks the exact same". Everything
  measured was true and the feature did not exist for them. Two checks that
  would have caught it, both cheap: open the surface in the state the USER's
  machine is in (not the staged one the test drove), and ask what the feature
  changes for someone who does nothing differently. The same wave had already
  applied this reasoning to the pane NAME and missed applying it to its own
  headline.
- **An injected value must be injected, and a field must have a way IN**
  (v1.217.0). Two halves of the same lesson, both found by DRIVING the product
  rather than reading it. (a) The pane identity (`IRONJARVIS_BUILD`,
  `_PANE_ID`, `_PANE_CWD`, `_PANE_NAME`, `_PANE_CLI`) was set on the session
  AFTER the shell was spawned, with a comment claiming it applied to "anything
  the pane starts next" — nothing applied it, `pane_env()` had no caller in the
  codebase, and every test passed because every test asked the DICT what it
  held. The id is now minted before the spawn and the variables are merged into
  the child environment (`_with_pane_env` — the backends REPLACE the
  environment when handed one, so the merge must start from the base they would
  have used or the shell loses its PATH), and
  `tests/test_pane_identity_v1217.py` asserts the env the BACKEND RECEIVED.
  (b) `name` was settable only at creation and the New-terminal button never
  asks; `agent_cli` was known only to the browser, because `launchCli` types
  into an already-running shell. Both were reachable from `curl` and from
  nowhere else, which is the Raster Studio lesson restated: a green suite
  proves the library works, not that a user can reach it. `PATCH
  /terminals/{id}` is PARTIAL (omitted = keep, `""` = clear) for the same
  reason the remote-agent update is — a re-post silently destroys the field it
  does not carry.
- **One PTY, many attaches: only the PRIMARY attach resizes it** (v1.232.0,
  audit Wave 6). Every TerminalPane sent `resize` on open and on every
  ResizeObserver tick, and the daemon keeps last-writer semantics, so a
  phone attaching over Tailscale reflowed the desktop's running session into
  40 columns and the desktop's next tick reflowed it back. `sendResize` now
  consults `components/terminal/resizeGate.resizeAllowed` — the pane is
  visible AND `document.hasFocus()` — and a window gaining focus claims the
  size. Keep the gate on the CLIENT: the daemon cannot tell a phone from the
  desktop, and "the window the user is looking at" is a fact only the
  window knows. Same wave: `GET /sessions/{id}/review` answers `200
  {"review": null}` for the normal no-review state (it 404'd on every
  detail visit), and the session page reads that shape.
- **A Build terminal OUTLIVES the Build page** (v1.243.0). The user's report:
  leave Build, come back, and the panes "disconnect or show a strange string
  of characters" and take a while to return. Every TerminalPane owned its
  xterm AND its socket inside a mount effect, so a module switch (or a Rail ⇄
  Canvas flip, which swaps the wrapper element) disposed both, and the return
  rebuilt them from a full replay plus the repaint wiggle. The replay
  re-parsed old QUERIES (a live pane's scrollback held Codex's OSC 10/11
  colour queries), and the 800 ms `TERM_REPORT_RE` knew only CSI `c/n/R/t`, so
  xterm's colour answers were typed into the shell. `components/terminal/
  paneHost.ts` now holds each pane's terminal + socket in a module registry:
  an unmount PARKS (the wrapper moves to an off-screen, inert lot — never
  `display:none`; xterm's renderer pauses itself off-screen), a mount ADOPTS
  (`acquirePaneHost` → `adopt` → `waitForStableSize` → `settle` → `start`).
  Only `closeTerminal` → `disposePaneHost` and the page's load-time
  `retainPaneHosts` end a host — never put `term.dispose()` or `new WebSocket`
  back in the pane. A REAL re-attach (reload, restart, lost link) is
  DELIMITED: the daemon ends the replay with one EMPTY binary frame, and the
  host drops every `isTerminalReport` answer until xterm's `write("", cb)`
  says the parser has passed it. Daemon half: `TerminalSession.read()` fans
  each chunk out to every `subscribe()`d attach (a read used to hand a chunk
  to ONE caller, so two attaches — or the drain thread at the instant of
  attach — tore escape sequences), `subscribe` snapshots the replay under the
  same lock, and the last `unsubscribe` starts the background drain, because
  a PTY nobody reads fills its pipe and the Claude in it BLOCKS until someone
  looks. `tests/test_terminal_attach_v1243.py`,
  `dashboard/__tests__/terminal-host-v1243.test.ts`.
- **A chat handed a file gets a FOLDER, project or not** (v1.244.0). The
  user's report: attach a document in chat, ask for work — "a lagging delay, a
  request for information and then a completed screen with absolutely no
  output"; fine inside a project. Replayed on the live model: a no-project
  chat armed NO file tools (the page arms `PROJECT_FILE_TOOLS` only when a
  project is selected) and its tool workspace was the hidden `home/uploads`,
  so it could not create the workbook, escalated, and the agent ran in
  `workspaces/<sid>` under AppData — built the right file, stopped twice on
  shell approvals, hit max steps, "Task failed" beside a file nobody could
  find. The sticky `ij_chat_workspace` default made it worse: the live
  install's was `C:\Users`, which `root_problem` refuses, so both the turn and
  the hand-off fell back to hidden folders. Now the page's `placeInWorkfolder`
  runs on every attach with no project: `POST /documents/workfolder` makes
  `<chat_files_dir>/<YYYY-MM-DD> <title>` (`config.chat_files_root`, default
  the Documents Known Folder + `Iron Jarvis`, `core/userdirs.py`), copies the
  uploads in (ONLY files under `home/uploads` — it must not become a general
  copy primitive; `into` must sit under `chat_files_dir`), keeps a `prefer`
  folder that passes `root_problem` (no copies), and NAMES a refused one in
  `note`. The page binds it as `workspace_dir` (per conversation — never
  written to `WORKSPACE_KEY`), arms `PROJECT_FILE_TOOLS` over an empty set,
  and shows it as a chip with Open. A folder failure attaches the upload
  exactly as before and says why. `tests/test_chat_workfolder_v1244.py`,
  `dashboard/__tests__/chat-workfolder-v1244.test.tsx`. TWO MORE DEFECTS the
  same replay found once the folder worked, both "Done!" over nothing usable:
  (1) the model called `write_document` with the rows as a JSON STRING and the
  .xlsx writer (text = lines) put the whole table in cell A1, QA-lint clean —
  `writers._spreadsheet_content` now reads .xlsx/.csv text as the table it
  spells (JSON rows/sheets via `core.jsonish`, records, `{name: rows}`, a
  markdown pipe table with the text around it kept) and recovered rows take
  the sheets path so numbers/dates/header are real; plain text keeps one line
  per row (`tests/test_sheet_from_text_v1244.py`). (2) the working-folder
  grounding block said "opened from a Build terminal pane" and listed "this
  project", so the model called a plain chat's folder "your project folder" —
  the block is surface-neutral now and names a project only when one is.
- **Build daily-driver pass, B1** (v1.245.0; the 2026-09-11 reliability audit
  measured each of these). (1) ConPTY sends a DA1 query at shell start and
  holds ALL output until answered (first byte 3.05 s vs 0.06 s): only an
  attached xterm answered, so `TerminalSession.read` now answers `ESC[?1;2c`
  itself when no pane is subscribed. (2) The WS input handler closed with
  4000 ("shell exited" — the one close a pane never reconnects from) on ANY
  exception, so a malformed resize killed a live pane for good: 4000 only
  when `not session.alive`. (3) Same-size resizes reflowed ConPTY and
  repainted the TUI: the route skips them after the attach's first (wiggle)
  resize, and the pane debounces its ResizeObserver (`RESIZE_SETTLE_MS`) and
  skips an unchanged `host.sentSize` unless forced (open, window focus).
  (4) The snapshot was written only on graceful paths: a lifespan loop calls
  `snapshot_if_changed` every 30 s (`output_seq` + identity key). (5) A
  restored pane kept `agent_cli` and its chip claimed a CLI that died with the
  old daemon: restore sets `resume_cli` instead and the pane's `ResumeStrip`
  types `RESUME_COMMANDS[cli]` (`claude --continue`, `codex resume --last`;
  others get no button). (6) `activity()` is cached per output change, the
  2.5 s poll left the access log, per-message deflate is off, a loop-lag
  heartbeat logs stalls > 250 ms, and Ctrl+C with a selection copies.
  `tests/test_build_stability_v1245.py`,
  `dashboard/__tests__/build-stability-v1245.test.tsx`.
- **A chat turn never goes quiet, never ends empty** (v1.246.0, C1). The
  user's report: chat "gets hung up from time to time" and an attach-and-ask
  turn ended on "a completed screen with absolutely no output". (1) Keepalives
  were sent only during an approval wait, so a model thinking or a tool running
  was total silence: `/chat/stream` is served by `HeartbeatStreamingResponse`
  (a `: keepalive` after `_SSE_HEARTBEAT_S` = 10 s of silence; the body
  iterator is still advanced in the response task, so cancellation and the
  ledger are untouched). (2) `streamSSE` had no limit: it ends a turn after
  `STREAM_STALL_MS` (60 s) with NO bytes — keepalives reset it — and after
  `STREAM_PREP_MS` (10 min) with no response, each with a Retry sentence; the
  caller's own abort stays silent. Keep the stall well above the heartbeat.
  (3) The bubble says what it waits on and for how long (`useChatStream`
  `phase`/`withFiles`/`startedAt`/`lastEventAt`; `components/chat/TurnClock`
  ticks itself so the page never re-renders per second; "Reading your files…"
  only while `preparing` a turn that carries attachments). (4) Both chat lanes
  pass `deadline_s=chat_tool_deadline(platform)` — the agent run's setting.
  (5) A model that was reached but wrote nothing is asked ONCE, without tools
  and within `_FINAL_ANSWER_TIMEOUT_S`, for its answer
  (`_final_answer_after_tools`, billed); only then does `_no_text_reply` say in
  plain words what happened — never the bare "(no reply)". A draft exit or an
  escalation is an answer and gets no nudge. (6) `rag_block` runs via
  `asyncio.to_thread` (chunking + per-chunk embedding froze the loop), and the
  chat ledger row carries the turn's real start (`started_at`). Every one of
  these is lock-step across `chat_turn.py` and `routes/chat.py`.
  `tests/test_chat_never_hangs_v1246.py`,
  `dashboard/__tests__/chat-never-hangs-v1246.test.tsx`.
- **An attended ask WAITS; a batch is ONE ask; office work stays in chat**
  (v1.247.0, C3). 26 of 31 asks on the 2026-08-23 rename job expired at 300 s
  and each was recorded as work not done. Runs whose origin starts with
  `ATTENDED_ORIGINS` (chat/job/project/user) wait with no clock
  (`ATTENDED_APPROVAL_TIMEOUT_S = None`) until answered, declined or
  cancelled; the unattended doors (goal/schedule/workflow/reflex/comm/
  autonomy) keep `SESSION_APPROVAL_TIMEOUT_S`, because the trust ladder is
  built on their timeout receipts. `/chat/stream` waits with
  `CHAT_ASK_TIMEOUT_S = None` and checks Stop and a dropped connection every
  `_ASK_POLL_S`. `timeout_s: 0` on the event/frame means "no expiry" and the
  card says "nothing runs until you answer". `runtime._pause_needed` is the ONE
  gate: a step's same-permission asks share ONE pause (`count` + ≤3 redacted
  examples; the shared task is cancelled in the gather's `finally` so a
  cancelled run never leaves it parked); 'once' covers exactly that batch,
  'deny' refuses all of it, and every call still gets its own ledger row.
  Listings (pending, `waiting_on`, the bell) carry NUMBERS, never arguments.
  The stream lane groups a round's asks with `_would_card`, which REPEATS the
  per-call `_needs_card` predicate — change one and you must change the other.
  `_round_budget` gives a turn with a document-writing tool
  `_DOC_TOOL_ROUNDS` (12) in BOTH lanes, and its last round ends in chat with
  `OUT_OF_ROUNDS_INSTRUCTION` instead of escalating; every other turn keeps
  `_MAX_TOOL_ROUNDS` and escalates as before. A test that drives an attended
  timeout must set the bound explicitly. `tests/test_approvals_office_v1247.py`,
  `dashboard/__tests__/approvals-office-v1247.test.tsx`.
- **The Build terminal path is BYTES, PUSHED and paced; only the pane on
  screen draws** (v1.248.0, B2). DAEMON: pywinpty handed output over as TEXT
  (a character split across two reads became U+FFFD) and could only be
  polled, so every attached pane's pump ran read/take/sleep(10 ms) forever —
  8 idle panes measured a whole CPU core (99.7% → 0.08% after). `ConPtyBackend`
  (ctypes) delivers raw bytes from a reader thread through
  `TerminalSession._ingest` → `_record_locked`, the ONE output path shared
  with `read()`; the handler is installed BEFORE `start`; a pushing session
  starts no drain; the route's pump sleeps until the subscription's waker
  fires (5 s safety net). `OutputSubscription._push` and `take` share the
  session's read lock — that is what makes a reader thread safe; never give
  a subscription its own lock. The pseudoconsole calls come from pywinpty's
  bundled `conpty.dll` + `OpenConsole.exe` when both exist (echo 0.5 ms vs
  14.8 ms on the inbox conhost), else kernel32; `IRONJARVIS_PTY_BACKEND=
  pywinpty|pipe` and `IRONJARVIS_CONPTY_HOST=inbox` force the others, and a
  pseudoconsole that cannot be made marks ConPTY broken for the process and
  the manager retries on the next backend. FLOW CONTROL: the pane counts bytes
  written to xterm and not yet parsed and sends `{"type":"flow","paused":
  <bool>}` at 512/128 KiB; the daemon consumes ONLY that exact shape and holds
  the SEND, never the READ (the 8 MB bound → 1013 still applies). An OLDER
  daemon TYPES every non-resize text frame into the shell, so the pane sends
  flow frames only when `/health` reports ≥ `FLOW_MIN_DAEMON` (1.248.0) — any
  new client→daemon text frame needs the same gate. DASHBOARD: `paneHost`
  loads `@xterm/addon-webgl` only while a terminal is on screen and releases
  it on park/release (browsers cap live WebGL contexts at ~16); a lost
  context falls back to the DOM renderer on the same buffer; `ij.build.webgl`
  = "off" disables it. A rail pane behind the focused one is `parked`: its
  terminal moves to the lot but its view and socket stay (badges live, no
  replay); it parks only after fitting and starting at its true size and
  never fits or resizes while parked. `tests/test_terminal_datapath_v1248.py`,
  `dashboard/__tests__/terminal-render-v1248.test.ts`. SAME RELEASE, found by
  its own Handbook bullet: the Guide's `_chunk` cut an oversize paragraph (a
  bullet list has no blank lines) at FIXED 1600-char offsets, so any edit above
  a bullet moved every later cut — "Restart to update" fell off the chunk that
  answers "how do updates install". `_line_pieces` now cuts at line
  boundaries, before the last list item that began in the piece;
  `tests/test_guide_chunk_v1248.py`. And the browser check found one U+FFFD
  in 4.3 MB: `output_tail` decoded a window cut at a BYTE offset, so its
  first (and, with bytes recorded as they arrive, last) character could be
  half a code point. `_utf8_window` trims only those edges — invalid bytes
  inside still show; `tests/test_terminal_tail_utf8_v1248.py`.
- **An update stops tidily, and what it interrupted is offered back**
  (v1.249.0, R-02/R-03/R-04/R-06 — the user's Upgrade Ballot). The installer is
  verified BEFORE anything stops: a bad download must never cost a live daemon.
  Then the daemon gets Quit's tidy stop (`requestDaemonShutdown`), and a handoff
  that fails RESPAWNS the children before the dialog — `quitAndInstall` returns
  false and routes failures through its own `error` event, so the teardown must
  not run first. The busy warning counts what the old one could not see: chat
  replies (session_id "chat", no Session row — `core/turns.CHAT_INFLIGHT`) and
  Build panes whose activity is working/blocked, with "Install when idle"
  re-checking every 60 s. `Session.interrupted_at` is the tag the boot reconcile
  writes, ONE `sessions.interrupted` event per boot (never one per session), and
  `GET /sessions/interrupted` feeds one bell row plus one Overview note whose
  Continue clears the tag. CLOSING TO THE TRAY DESTROYS THE RENDERER, so a
  waiting ask is announced by `installAskWatcher` in main.js — never a page
  toast (DesktopNotifyBridge's old comment claimed otherwise); reminders report
  how long the ask has ACTUALLY waited. A shutdown-shaped child exit
  (0x40010004 / session-end) only DEFERS a respawn and is not a crash.
  `classifyStartupFailure` names the cause from the child's exit and its last
  log lines — port in use (uvicorn prints `[Errno 10048]`, not `WinError`), a
  locked database, a failed data upgrade, a missing binary — and the dialog
  offers Retry / Open logs / Quit. R-01 WAS REJECTED BY THE USER:
  `quitAndInstall(false, true)` stays as it is, and nothing relaunches the app
  after a successful install. `tests/test_desktop_reliability_v1249.py`,
  `tests/test_update_busy_v1249.py`,
  `dashboard/__tests__/interrupted-jobs-v1249.test.tsx`.
- **A backup that never leaves the disk it protects is one failure from
  nothing** (v1.249.0, R-05). `backup_mirror_dir` copies each new archive, plus
  `artifacts/` + `creative-thumbs/` into `<mirror>/media/`, after automatic AND
  manual backups. Three rules: the folder is validated at `PUT /settings`
  through `fs_policy.root_problem` and NEVER at load, or an unplugged drive
  stops the app booting; the mirror is pruned ONLY over the app's own archive
  glob (`prune_backups` globs `ironjarvis-backup-*.tar.gz`), because the user's
  own files live in that folder; and a failed copy records state
  (`backups/mirror-status.json`, `loop_health["backup_mirror"]`, a doctor line)
  and never fails the local backup. Media is incremental (size+mtime, FAT
  slack) and bounded (5000 files / 600 s), and reports what it skipped. The
  copy holds the database, settings and secrets — the card says so.
  `tests/test_backup_mirror_v1249.py`,
  `dashboard/__tests__/backup-mirror-v1249.test.tsx`.
- **Cost scales with attention, and the composer is not the page** (v1.250.0,
  S-03/S-04/S-05/S-08/S-09 of the Upgrade Ballot). A streamed token used to
  `setText` in the hook, re-rendering the 7,750-line chat page per WORD (4.1
  page renders per keystroke, 105 bubble renders per keystroke, 173 per token —
  measured). Now: the live text lives in a ref, is published at most once a
  frame through `useChatStream`'s store, and is read by `useLiveText` in the
  ONE component that shows it — with a synchronous flush on done/error/abort so
  the last words land exactly, never a frame late. Settled markdown is memoized
  and only the tail re-parsed, cut at blank lines OUTSIDE fences. Typing writes
  `lib/composerStore.ts`, which the textarea, pickers and send arrow subscribe
  to; THE PAGE MUST NEVER SUBSCRIBE (it reads text synchronously on send), or
  every keystroke redraws every message again. Each message is a memoized
  `MessageRow` taking handlers in one `h` object and `isLast` from the page —
  both halves are pinned, because a row that always renders the newest-reply
  affordances, or a page that hands every row `isLast`, puts them on every
  reply. `useApi` seeds from `lib/apiCache.ts` (bounded, OUTSIDE `lib/api.ts`,
  which ~71 test files mock wholesale) and revalidates behind it; timers use
  `useVisibleInterval`; `/models` has ONE reader, `useModels`; animation is
  `m.*` under the layout's `LazyMotion` (a `layout` prop or framer `drag` needs
  `domMax` or it silently does nothing — and `app/page.tsx` importing `motion`
  directly cost that route 6 kB). TWO MOCK CONTRACTS THIS CREATED, and they
  broke 264 tests when they were missed: every `@/lib/useChatStream` mock must
  export `useLiveText`, and every `framer-motion` mock must export `m`.
  `dashboard/__tests__/{chat-stream-perf,api-cache,composer-store,lazy-motion,visible-interval}*`.
- **A warm-up without single-flight is duplicated work, and a boot must be able
  to explain itself** (v1.251.0, S-01). The two desktop boot gates ran
  SERIALLY, so the splash waited out the whole daemon boot before probing a
  dashboard that had been ready in ~0.1 s: they now run together
  (`Promise.allSettled`, `GATE_POLL_MS` 150) with the DAEMON's verdict still
  reported first when both fail, because its failure is the one that explains
  the other and `classifyStartupFailure` keys off it. `warm_opencode` resolves
  the allowlist on a boot thread — but `_opencode_cache` is written only when
  the shell-out RETURNS, so the desktop's first `/health` raced the warm and
  ran a SECOND `opencode models`: interleaved on the frozen build that was
  0.08 s SLOWER than no warm-up at all, while every source test stayed green
  (the test waited for the warm before calling). `_opencode_allowed` is
  single-flight with the fast path OUTSIDE the lock; do not simplify that lock
  away. Measured −0.42 s to the first healthy `/health`, 5/5 interleaved pairs;
  cross-session variance here reaches 2×, so only within-session interleaving
  counts. THE BALLOT'S 7–10 s "State home → server process" GAP DID NOT
  REPRODUCE on an empty scratch root (0.73 s), so the daemon now measures its
  own boot instead: every `_rehydrate_step`, `scheduler.start()` AND
  `build_platform` (uvicorn prints "Started server process" BEFORE the
  lifespan, so the blamed window IS `create_app` — timing only the rehydrate
  steps would measure everything except the suspect), one INFO summary line
  (`startup 8.42 s: platform 4.10, skills 1.90, …`) and a `startup` block on
  `GET /diagnostics`. Names and numbers only — never a path, a file name or a
  credential. Every phase records from a `finally`, and the record helper and
  the summary emit are each wrapped: a boot's own report must never become what
  breaks the boot (the v1.229.0 OBS5 lesson). `tests/test_startup_speed_v1250.py`,
  `tests/test_boot_timing_v1250.py`.
- **A grant is written where the NEXT run reads, and yolo never rides an
  escalation** (v1.232.0, audit Wave 6, A6/A7/A9). "Allow for this
  conversation" on a session's ask widened an in-memory set and nothing
  else; the chat's next message is a NEW session row built from the parent's
  `allow_tools_json`, so the user re-approved what they had just approved.
  `runtime._pause_for_approval` now persists a `conversation` grant into the
  row at resolve time AND onto the Session object in hand — the finalizers
  `merge` that object, and a merge of a stale column silently undoes the row
  write. `ContinueBody.allow_tools` is UNIONED, never assigned. The chat
  posture rides `SessionCreate`/`SpawnBody`/`ContinueBody.approval_mode`,
  normalised in ONE place (`runtime.inherited_approval_mode`, called from
  `create_session`/`continue_session`): `yolo` lands as `approve_for_me` at
  every door, because auto-approval was consented to one watched turn at a
  time and a background batch is a different blast radius. Do not add a door
  that writes `Session.approval_mode` without that helper.
- **Frozen-build verification**: anything touching native deps or subprocess
  spawning MUST be verified in the packaged daemon, not just source. The
  terminals feature shipped dead once because PyInstaller dropped
  `OpenConsole.exe`/`winpty-agent.exe` (now bundled in the .spec). New Python
  deps with native wheels (paramiko/bcrypt/nacl style) need spec entries.
- **`GET /sessions/{id}` returns `{session, transcript}` — NESTED.**
  `POST /sessions`, `POST /sessions/{id}/continue`, `/cancel`, `/rerun` return
  the session FLAT. `GET /sessions` returns `{sessions: [...]}`. Reading
  `.status` off the nested endpoint's top level silently yields undefined —
  this exact bug shipped twice (chat spinner-forever, Spotlight notification
  never firing). When in doubt, curl the endpoint.
- **PDF redaction edits the page; it never regenerates it** (v1.154.0).
  `documents/pdf_redact.py`: pikepdf (MPL-2.0) rewrites the content stream so
  the glyphs are genuinely deleted, pdfplumber supplies word geometry for true
  black boxes, and the written file is RE-READ to prove the values are gone —
  `RedactionUnverified` deletes the output rather than hand back a PDF that
  looks redacted and still carries the SSN. Only then does the old rebuild
  (`write_document` from extracted text) run, and the note says which path
  produced the file. Do not "simplify" this by trusting the transform: matching
  text in content streams is heuristic (values split across operators, odd
  encodings), and the verification is the only thing making it safe. pikepdf is
  a NATIVE wheel and has a `packaging/ironjarvis.spec` entry. Detection also
  runs PER LINE and the address pattern is case-insensitive — both were live
  defects: `\s` separators welded numbers across line breaks (6 of 7 "phone"
  hits on a real return were ownership percentages), and uppercase tax-document
  addresses were never matched at all.
- **A tool that writes a file says WHERE, absolutely** (v1.153.2), and a reply
  that CLAIMS a file is checked against the ledger. Two halves of one live
  report ("it told me it saved the file; the path it gave has no file"):
  (a) `redact_pii`/`write_document`/`convert_document` reported a
  WORKSPACE-RELATIVE path, which is a bare filename whenever the output lands
  in the workspace root — the model relays it and the user looks next to the
  source. They now report the absolute path and carry `abs_path` in `data`.
  (b) `_creation_honesty_note` keys off the USER's phrasing, so "redact this
  K-1" matched nothing and a reply announcing a saved file went unchecked —
  the ledger showed only `redact_scan`, which writes nothing.
  `_claimed_write_note` now judges the reply's own claim against what actually
  ran, in BOTH chat lanes. Note the destinations differ by lane: chat's tool
  workspace is `home/uploads`, an agent session's is its own session dir.
- **Never let a real-provider failure return mock output**, and since v1.162.0
  that includes a provider that is merely NOT CONNECTED. The router
  (`providers/router.py`) raises for a failed real provider; mock is ONLY for
  the offline/mock-default path (`wanted == "mock"` — a fresh install ships
  `default_provider = "mock"`, so first-run and the whole offline suite are
  untouched). The old code refused only an EXPLICIT pick under the strict pin
  and let the DEFAULT route fall through to mock — and chat sends no provider,
  so every chat turn took that branch. A user whose local fleet endpoint went
  down got the mock's scripted "Done. Wrote RESULT.md summarizing the task."
  and read it as finished work; the mock also EMITS a `write_file` call, so
  with a document tool armed the fabrication reaches DISK. `complete()` and
  `stream()` now both publish `provider.downgraded` (`used: "none"` — the
  banner still points at Connections) and then RAISE `_unavailable_error`,
  which names the provider. No automatic substitute even when other providers
  are connected: this box holds client tax documents, and moving a chat from a
  local endpoint to a cloud API is the user's privacy decision, not a routing
  fallback (asked and confirmed 2026-08-11). Mid-call failures were already
  guarded by `if wanted != "mock": raise` in both lanes — the gap was the
  PRE-RUN availability check only.
- **"It answered, so it is up" is false for a proxy** (v1.228.0, audit Wave
  2). The v1.162.0 refusal was scoped to a local primary that never ANSWERED;
  one that answered 429/5xx/404 "still fails over exactly as before" — by
  design, docstring and pinned tests. The live 2026-08-28 event was a LiteLLM
  proxy answering 500 because ITS GPU box was unreachable, and the
  conversation went to claude-cli. The answered case is now
  `config.local_primary_policy` (`refuse` default | `failover`), read live
  and FAIL-CLOSED by `ModelRouter._refuses_failover` (kind
  `answered_error`); transport shapes refuse under both values, cloud
  primaries and Auto are untouched, both lanes lock-step. Same wave: the
  failover reason is DERIVED (`failure_reason(exc)` → `unreachable` /
  `timeout` / `interrupted` / `http <status>` / `transient error` / `error`),
  never a string typed at the publish site — "rate limited" was a lie for
  every 500 — and it rides `RouteResult.from_provider`/`why`, the final
  frame's `from`/`why`, both chat lanes' `route` object and the TurnReceipt.
  And a local primary with a base_url gets a ~2.5 s `GET /v1/models`
  pre-probe (`_refuse_if_dead`, before `provider.routed`, both lanes) through
  the adapter's OWN client: a dead box refuses at once; only connect-shaped
  failures and timeouts count as dead, any answer or an inconclusive probe
  (fake client, odd transport) lets the real attempt — and the cold-load
  retry ladder — decide. Fake adapters have no `_endpoint`, so the offline
  suite never probes.
  **A DEATH AFTER THE FIRST TOKEN STILL COUNTS, AND THE BREAKER GATES THE
  PRIMARY** (v1.232.0, audit Wave 6, R3/R4). `stream()` still never swaps
  providers mid-answer, but `if committed: raise` sat BEFORE
  `record_failure`/`provider.failed`, so the same death one token later was
  invisible to the breaker, the ledger and the notifier — `_committed_failure`
  records it, publishes `partial: true`, and wraps a LOCAL transport death as
  the `interrupted` refusal ("dropped mid-answer, so the reply above is
  incomplete"; an `httpx.ReadError("")` used to render as a BLANK error line
  under half a reply). `provider.failover` is published on the alternate's
  FIRST frame, because a client disconnect cancels the generator and the
  record of a turn that MOVED must already exist. And `ProviderHealth` gated
  only the failover candidates while its docstring claimed otherwise:
  `_refuse_if_open` (both lanes, beside `_refuse_if_dead`) refuses an OPEN
  circuit by name with the seconds left, `/health` rows carry `circuit:
  {open, retry_in_s}`, and the PreflightNote says it before the user types.
  Auto and the HALF-OPEN probe still go through.
- **OpenAI ChatGPT-account backend retires model ids** (gpt-5-codex, gpt-5.1*,
  codex-mini-latest are all dead). The adapter
  (`providers/adapters/openai.py`) keeps a fallback ladder
  (`_CHATGPT_FALLBACK_MODELS`) + rejected-id cache. If OpenAI-via-subscription
  starts 400ing "model is not supported", extend the ladder — do NOT hardcode
  a single id anywhere.
- **One-shot agent utilities** (terminal assist, workflow builder) go through
  `_complete_with_retry` + `_one_shot_complete` in `daemon/app.py`: transient
  429/overloaded retries, then cross-provider failover. Keep new one-shot
  endpoints on that path.
- **Event payloads**: `agent.state_changed` carries `{from, to}` (NOT
  `state`); `agent.completed` `{run_id, ok, result}`; `tool.executed`
  `{tool, ok, mode, invocation_id, reversibility, risk_class}` (`risk_class`
  since v1.237.0). All tagged with `session_id`. Grep
  `core/events.py` + `agents/runtime.py` before consuming events.
- **Parallel agent work**: one file per agent, period. Shared files
  (`daemon/app.py`, `Sidebar.tsx`, `types.ts`, `ui.tsx`, `main.js`) are owned
  by the coordinating session. Don't run the full test suite while agents are
  mid-edit.
- **A frontend `waitFor` must wait for the THING YOU ARE ASSERTING**, never for
  a proxy signal that lands earlier. TWICE now this exact shape has cost a
  release: v1.177.1 (`JobPostCard` — waited for the POST to be recorded, then
  asserted the boxes had cleared, which happens in a LATER state update) and
  v1.178.0 (`canvas-v1170` — waited for `post("/workflows")`, then clicked Run
  and asserted the fork was unpinned; `setLoadedPin(null)` runs AFTER that
  awaited post). Both were green locally and on most CI runs — a contended
  runner is the only place the window is wide enough to see. The rule: put the
  real assertion INSIDE `waitFor`, or wait on a signal set at the END of the
  handler (the success note, the re-enabled button), not on the first
  observable side effect. A handler that does `await x` and then sets state has
  a window between the two, and CI will find it eventually.
  A THIRD instance, v1.251.0, names the trap underneath: a click on a
  DISABLED button dispatches NOTHING, so `fireEvent.click` on one is swallowed
  in silence and every later wait in that test waits for work never started.
  `portrait-crop-v1214` waited for the cropper's MODAL before clicking "Use
  this" — but the modal mounts before the picked image decodes, and the button
  is `disabled={!natural}` until it does. Two gates died there: v1.236.1
  ("Unable to find an element with the text: boom") and v1.251.0 ("Test timed
  out in 5000ms") — one swallowed click, two symptoms. Three corollaries:
    - A dialog being on screen is not its CONTENT being ready. Wait for the
      control to be ENABLED — that is the precondition the click needs.
    - Never give a `waitFor` a bound equal to vitest's per-test budget
      (5000ms default). It can then never report its own failure: the test
      times out first and prints nothing about what it waited for. v1.236.2
      did exactly that here and turned a legible error into a bare timeout.
    - A race fix is verified only if the MUTATION reproduces the failure on an
      idle machine. Hold the async step (an `Image` whose load fires when the
      test says so) instead of hoping a loaded runner shows it; v1.251.1's
      mutation reproduced v1.236.1's exact error text locally.
  THE RELEASE GATE IS A SMALLER MACHINE THAN THE TESTS GATE, and that asymmetry
  is why the same commit passes one and fails the other (v1.254.0-.2, three red
  gates in one cycle, each on a DIFFERENT timing-sensitive dashboard test).
  `tests.yml` splits work across four jobs, so its vitest gets a runner to
  itself. `release.yml` runs ONE `suite` job: the add-on `pnpm install`, the
  add-on build, the dashboard suite, the node syntax checks, THEN the whole
  Python suite at `-n auto`. Same command, far less machine. So:
    - A dashboard test that passes in Tests and fails in Release is a LOAD
      signal, not a flake and not a product defect. Read it as "this test has
      a wait that is wrong" first, and fix the wait.
    - vitest's per-test budget is an absolute wall-clock threshold measuring
      the runner (the rule this repo already holds about p95 assertions).
      `dashboard/vitest.config.ts` sets `testTimeout: 15_000` for that reason.
      Raising it hides nothing — a wrong expectation still fails with its own
      message — which is exactly what a `waitFor` bound equal to the budget
      cannot promise.
    - Reproduce the ordering locally instead of guessing: delay the one async
      hop the assertion depends on (a mocked read, an `Image` whose load you
      release by hand) and watch the OLD shape fail on an idle machine. Both
      v1.251.1 and v1.254.2 reproduced CI's exact error text that way.
- **A PACKAGE THAT RE-EXPORTS A FUNCTION SHADOWS THE MODULE OF THE SAME NAME**
  (cost time twice, v1.254.0 and v1.256.0). `onboarding/__init__.py` does
  `from .doctor import doctor`, so `from iron_jarvis.onboarding import doctor`
  binds the FUNCTION and `doctor.CHECKS` / `doctor.__file__` raise
  `AttributeError: 'function' object has no attribute ...`. Reach the module
  with `importlib.import_module("iron_jarvis.onboarding.doctor")`. The failure
  reads like the code is broken when the import is.
- **A DESTRUCTIVE BULK ACTION MOVES, IT DOES NOT DELETE** (v1.256.0, R-01). The
  undo journal has two file kinds — `file_restore` (needs the prior bytes) and
  `file_delete` (created new → unlink on undo) — and NEITHER can reverse
  "remove bytes that already existed": the first would demand a pre-image of the
  very bytes being freed (700 MB of video, in the case this shipped for) and the
  second inverts to UNLINKING, which destroys rather than restores. So a bulk
  clear moves into `<home>/trash/<stamp>/` keeping relative paths, names what it
  moved, and the report keeps counting those bytes until a SEPARATE press
  deletes them. Do not "simplify" this into an unlink: the move IS the
  recoverability, and the two presses are what let the first one be confident.
  Never promise Undo can reverse something the journal cannot.
- **A folder is WRITABLE when a file lands in it, and `tempfile` is not the
  way to find out** (v1.228.0, audit Wave 2). `fs_policy.usable_workspace_root`
  accepted `C:\Users` (absolute, a dir, allowlist-clean, not protected) and
  the first `write_document` died naming a hidden `.tmp-<pid>` sibling. It
  now probes (`dir_writable`: ONE `os.open(O_CREAT|O_EXCL)` of an
  `.ij-probe-*` name, unlinked at once) — `os.access(W_OK)` ignores ACLs on
  Windows, and BECAUSE it does, `tempfile.mkstemp`/`NamedTemporaryFile`
  catch `PermissionError` and retry `TMP_MAX` (10,000) times on an RX-only
  folder: this task's first cut hung the suite for minutes. The probe is
  BLOCKING, so every route seam wraps it in `asyncio.to_thread`
  (`_root_problem`'s sync create/patch callers already run in FastAPI's
  threadpool). A write tool that still hits `PermissionError` says
  `cannot write in <workspace>: ... not writable` via
  `tools/base.unwritable_workspace_error` — but only for an errno-bearing
  one; `safe_path`'s escape refusal is also a `PermissionError` and keeps
  its own words.
- **Freshness is the SERVER's header, and a caller's abort is not an outage**
  (v1.230.0, audit Wave 4). `lib/api.ts` sent `cache: "no-store"` on every
  fetch, which made Chromium skip its CORS *preflight* cache too — one OPTIONS
  per GET against a daemon answering max-age 600, half of all traffic. The
  option is gone and `NoStoreMiddleware` (inside CORS, so the preflight stays
  cacheable) puts `Cache-Control: no-store` on every response instead. Do
  not remove EITHER the middleware or the test that pins it: with neither
  side saying no-store, Chromium writes session JSON — client file names —
  into the Electron disk cache. Same wave: `api()` used to map every fetch
  rejection to "daemon offline" + the app-wide network signal, so the
  palette's per-keystroke abort restarted DaemonProvider's poll loop and one
  slow /health flashed the banner. A caller's own abort is
  `ApiError("cancelled", 0, cancelled=true)` and signals nothing; the
  provider owns its verdict (in-flight guard, sequence number, two misses
  before offline) and the /health timeout does not signal either.
- **Cost scales with attention, and a conditional GET is only as good as the
  header the browser may READ** (v1.230.0, audit Wave 4, FP2/FP3). One window
  ran THREE 5 s pollers of `/health` (DaemonProvider, ModelSwitcher, the
  Overview) and none of them paused while minimised. Now `lib/useDocumentVisible`
  feeds `usePolledApi` (interval torn down while hidden, ONE refetch on the
  visible edge) and `DaemonProvider` (30 s while hidden; going hidden costs no
  request), and `/health` has one reader: `useDaemon().health` + `refresh()`
  (`provided` tells a hook whether a provider is above it — `useProviderHealth`
  polls for itself only when it is not). `GET /sessions` answers a weak ETag and
  a bodiless 304; the dashboard's `useApi` sends the tag it HOLDS (`lib/etag.ts`
  keys the tag by the payload object — per response, never per path, or hook A
  sends hook B's newer tag and 304s itself into stale data) and keeps its data on
  the `NOT_MODIFIED` marker. Two traps: ETag is not a CORS-safelisted response
  header, so without `expose_headers=["ETag"]` on BOTH CORSMiddleware branches
  `res.headers.get("etag")` is null cross-origin and the path silently never
  engages (pinned in `tests/test_sessions_etag_v1230.py`); and the marker
  helpers live OUTSIDE `lib/api.ts` because 71 test files mock that module
  wholesale — importing them from `./api` made every mocked `get` look failed.
  Same wave (FP6): `/events` is ONE socket per window — `EventsProvider` in
  `layout.tsx` owns the `EventsHub`, every `useEvents` is a fan-out subscriber
  with its own window, reconnect is 2.5 s doubling to 30 s ±20% and resets on
  open, and a hook never appends an id it holds. A test that counts sockets
  mounts its hooks under ONE provider (each provider owns a socket).
- **"Connected" is the ROUTER's answer, and a status row that reads its own
  store is a second truth** (v1.230.0, audit Wave 4, U5). `available()` had
  resolved a keyless `anthropic` to the logged-in `claude` CLI since the
  OAuth compliance wave (`_INHERIT_ALIAS`), so /health said available and the
  switcher offered claude-opus — while `ConnectionRegistry.status()` read only
  the `ConnectionRecord` and printed "Not connected" on the same screen. Any
  surface that says whether a provider works reads
  `ProviderManager.inherited_from` / `available()`; the registry takes the
  oracle as an attribute (`platform.py` assigns it after the manager exists —
  the manager closes over the registry, so it cannot be a constructor arg)
  and reports `source` beside `connected`. Same wave: `components/Markdown.tsx`
  is the ONE markdown renderer — a surface that shows a model-written
  paragraph renders through it (or `plainText` for a truncated row); do not
  print `session.summary` raw again.
- **A SOURCE PIN READS A FILE THE CI RUNNER CHECKED OUT WITH CRLF** (v1.232.1). GitHub's Windows runners set `core.autocrlf=true`, so every
  file the suite reads as TEXT has `\r\n` line ends there and LF here. A
  pin whose needle carries an embedded newline therefore matches every
  local run and NEVER matches on CI: `page-copy-v1232` searched for
  `"Auto tools\n"` (a JSX text node that owns its line), got `-1`, and
  took the v1.232.0 installer down with it — the local suite, the local
  dashboard run and the review had all been green. Normalise at the READER
  (`readFileSync(...).replace(/\r\n/g, "\n")`, one helper per test file), never
  at each call site. The same trap sits in every fixed-size window around a
  match (`[\s\S]{0,400}?`): add a prop and the window stops reaching the
  closing tag, so the pin reports "never rendered" rather than "a prop
  moved" (v1.232.0 hit that too). Pin the CONTENT; keep the window generous.
- **Windows dev shell**: PowerShell 5.1 — no `&&` chaining; Git Bash available.
  This machine lacks ffmpeg on PATH.

- **Before deferring a boot step into the daemon lifespan, ask who builds a
  platform WITHOUT one.** v1.257.0 (S-01) was designed as a background connect in
  `lifespan`, until `grep build_platform` showed `daemon/cli.py` calling it from
  ~40 entrypoints that have no lifespan at all — every one of them would have
  silently lost its MCP tools, a capability regression hidden inside a speed fix.
  The fix was to make the existing call site CONCURRENT instead (a
  `ThreadPoolExecutor` whose `pool.map` preserves config order), which also kept
  `tests/test_mcp_execution.py`'s restart-survival assertion a real guarantee
  rather than converting it into a race. Parallel beats deferred when a
  synchronous contract depends on the work being finished.
- **A pin that survives the obvious revert is UNCHECKED — mutate what the pin
  itself claims.** Reverting to the old implementation only falsifies pins the
  old implementation violates. In v1.257.0 the serial revert reddened 2 of 5
  S-01 pins: serial code is in-order, records per-pack and builds no pool BY
  CONSTRUCTION, so for the other three that revert proved nothing until each got
  its own mutation (`pool.map` -> `as_completed`, drop the failure record, remove
  the single-pack shortcut). Worse, the first four S-02 pins passed every
  mutation because they measured REACT, not the code: all tokens were emitted
  inside one `act()` so React batched the notifications into one render, and
  `useSyncExternalStore` re-reads `get()` on ANY re-render (and `stop()` sets
  state), so an on-screen assertion held even with the synchronous flush
  deleted. Rewritten to count publishes to the store's own subscriber, one
  `act()` per token, all five then went red. Corollary: a mutation harness must
  restore in a `finally` and decode/encode child output as UTF-8 — a cp1252
  crash mid-round left a deliberately broken file in the tree.

- **Edit N tests, run all N — verify the FILE, not the one test you were
  thinking about.** v1.257.0 converted three fixed-sleep waits in
  `tests/test_fix_sessions.py`, and the reproduction harness that proved the fix
  targets ONE of them, so the other two were never executed after the edit. One
  referenced `SessionStatus.RUNNING`, which does not exist — `RUNNING` is a member
  of `AgentState`, a DIFFERENT enum in the same module — and it reached the
  full-suite gate as an `AttributeError`. Two lessons: a targeted re-run proves
  the case you had in mind, never the edit; and when two enums in one module share
  plausible member names, read the enum rather than recalling it (`SessionStatus`
  is ACTIVE / QUEUED / COMPLETED / FAILED / CANCELLED — there is no RUNNING
  session).

- **To falsify a WAIT, mutate it by DELETING it — shrinking it to `sleep(0)`
  proves nothing.** v1.257.0 converted three fixed sleeps in
  `tests/test_fix_sessions.py` and checked them by shrinking each to `sleep(0)`.
  One stayed green, so I concluded the wait was decoration and dropped it —
  wrongly: DELETING that wait fails with `ACTIVE is not CANCELLED`, because with
  no yield at all `run_session` never enters its try block, `task.cancel()`
  unwinds a task that never armed the CancelledError handler, and
  `_finalize_cancelled` never runs. A single yield satisfies some waits, so
  `sleep(0)` is a weaker mutation than absence. Check a wait both ways: delete it
  (does anything still need it?) and shrink it (is the DURATION load-sensitive, or
  only the ordering?). The genuinely decorative wait in that same file was the one
  where DELETION also left the test green — `delete_session` refuses on a live
  task in `_running` armed synchronously one line above, so nothing was ever being
  waited for.

- **A component is not its own chunk: before deferring one, check what else its
  module exports as a VALUE.** Bundling is per-module. v1.258.0 (S-03) started with
  eight `next/dynamic` candidates on the chat route and two were dead on arrival:
  `CompactionCard` ships beside `CompactionChip` and `WorkflowDraftCard` beside
  `WorkflowRunChip`, both of which the page RENDERS (`:6642`, `:1673`/`:1760`) — so
  those modules load either way and "deferring" the card would have moved zero
  bytes while looking like progress. Type-only siblings are harmless
  (`CompactionInfo`, `BatchPreview`, `RunResult`, `ProjectSurfaceView`): types
  erase at compile time, so keep them as `import type` and defer the component.
  Corollary for measuring: do NOT attribute bytes by grepping built chunks for
  identifiers — minification erases them, and probing produced a string of false
  negatives here (even literal UI text went unfound). Compare route weights from
  `.next/app-build-manifest.json` BEFORE and AFTER the change on the same machine;
  that A/B put the real figure at -122.5 KiB for /chat, nearly triple the 43.6 KiB
  the one chunk I could positively identify would have suggested.
- **Inserting a declaration above a named one ORPHANS its doc comment — check for
  one first.** Twice in v1.257.0/v1.258.0 a patch script inserted a block
  immediately before a named declaration that already had a `/** ... */` above it,
  leaving that comment describing the wrong thing: `<AgentLiveText>` landed between
  `StreamingText`'s comment and `StreamingText`, and a `MODULE_OF` table landed
  between the anti-vacuity comment and the `MUST_STAY_STATIC` list it explains.
  Neither breaks behaviour, which is exactly why it survives review; in an
  8,200-line file a comment over the wrong function is worse than no comment.
  Before inserting at a name, look at the line above it.

## Map (where things live)

- `src/iron_jarvis/daemon/` — `app.py` is factory + glue only (platform build,
  lifespan boot-rehydration + background loops, middleware, the shared `d` deps
  object); the ~240 endpoint handlers live in `routes/<domain>.py` (24 modules;
  search by route string ACROSS routes/), request models in `schemas.py`.
  Handlers reach shared state via `d.*`; tests monkeypatch `_MAX_UPLOAD_BYTES`
  and `_graceful_stop` on the app module, so routes access those via
  `_app.<name>` at call time — keep that pattern. Logging: two logger
  namespaces (`ironjarvis`, `iron_jarvis`) are aliased in
  `core/logging.configure_logging` — never add a third; the noise filters
  and the 500 envelope's `err_` id live in `core/logging.py` and
  `daemon/auth.unhandled_error_response`.
- `agents/` — orchestrator (sessions/reviews/continue), runtime (the
  perceive→act loop), dynamic agents. `providers/` — manager (per-provider
  factories), router (routing/failover), adapters/. `terminals/` — manager
  (+ restart-survival snapshot, pane NAME + `agent_cli`, and the pane's
  `IRONJARVIS_*` identity merged into the shell's own env at spawn), session
  (scrollback, `activity()`), `agent_state.py` (v1.217.0: the pure
  tail→`working`/`blocked`/`idle`/`done`/`unknown` classifier), ai_clis (Launch
  detection), shells, backend (ConPTY/pipe/Fake). `tools/pane_tools.py` is the
  agent-facing side (list/read/spawn/send/wait; send+spawn on the deny floor).
- `repl/` — the session NAMESPACE (v1.159.0). `worker.py` is the child:
  stdlib-only (it is spawned from a frozen binary), newline-JSON on
  stdin/stdout, one persistent globals dict, output capped and truncation
  reported. `session.py` is the parent: one child per session, every
  blocking step through `asyncio.to_thread`, a reader thread so a deadline
  can actually be honoured, kill-on-timeout, and an honest `restarted` flag
  so a model is never told its variables survived when they did not.
  `tools/repl_tool.py` is the tool. Do not add a `get()`-first path: the
  registry creates a namespace on demand through `execute`, and a
  `get()`-first caller fails on the FIRST call of every session. Namespaces
  are keyed by `namespace_key(session_id, workspace)` — one per (session,
  FOLDER) pair, because chat runs every turn as session id `"chat"` while its
  workspace follows the grounded project, so keying on the id alone pinned the
  write root to whichever project opened first. The registry's PUBLIC surface
  (`get`/`dispose`/`sweep`/`session_ids`/`in`) still speaks session ids and
  covers every folder that session used; only the internal dict key is
  composite.
- `context/` — `budget.plan_history`: the per-turn CHAT history planner (pure,
  offline, deterministic recap), consumed by both chat lanes.
  `agent_window.plan_agent_transcript`: the same job for an agent RUN, and a
  separate module because it protects the oldest message (the task) instead of
  the newest, and because tool pairs are indivisible. Shares `budget.py`'s
  token estimator and reserves so both lanes count tokens identically.
  `compaction.py` is the layer above both: thresholds, the structured prompt,
  and the verification pass that makes a model-written summary admissible.
  `store.py` caches one summary per covered prefix, CONTENT-ADDRESSED (a chat
  turn carries no thread id, an unsaved thread has none, and a forked thread
  should inherit its parent's summary for free) — and it is registered in
  `core.db._LATE_MODEL_MODULES`, without which its lazily-created table lands
  on fresh test DBs and on no real install (the v1.151.2 lesson).
- `guide/` — THE IRON JARVIS GUIDE (v1.224.0; v1.223.0 shipped it as a chat
  persona, which the user rejected — it belongs in the AGENTS module). A
  built-in agent, `AgentType.GUIDE` in `agents/types.py`, in the roster
  beside the other builtins. BASE KNOWLEDGE: `agents/runtime` injects
  `guide.base_knowledge` (the Handbook's overview + this install's live
  version facts) into every Guide session. TOOLS (`guide/tools.py`, all
  read-only, all `allow`): `guide_search`/`guide_read` over `corpus.py` — the
  bundled docs (`BUNDLED_DOCS`, shipped into `_MEIPASS/ijdocs` by the .spec,
  which IMPORTS that list so the bundle cannot drift) plus live catalogs from
  the running daemon (version, providers, tools, skills, personas, agent
  types, every API route with its docstring) — and `app_search`/`app_status`
  over the user's OWN things in this install (projects, workflows,
  schedules, reflex rules, goals, skills, agents, threads, sessions, memory
  bases, custom tools), each hit with the dashboard path that opens it, and
  an unreadable store NAMED rather than reported empty. Retrieval is BM25 +
  phrase bonus + a user-facing doc prior — deterministic, no embedder. THE
  ROUND TABLE HAS NO TOOLS (`PANEL_NO_TOOLS`), so for a Talk with the Guide
  `threads._speak_local` runs the retrieval + app_search itself and appends
  the material AFTER the no-tools rule (`_guide_material`); Give-work and a
  `guide` session get the real tools. `routes/guide.py` is the inspection
  surface (`/guide/status|search|ground`); the Help page's "Ask the Guide"
  box lands in `/agents?talk=guide&ask=…`. Chat can also arm
  `guide_search`/`app_search`/`app_status` (autoselect, narrow vocabulary).
  The Guide knows what the DOCS know: when a surface ships, the Handbook is
  part of the change set, or the Guide answers "not covered" about a feature
  that exists (the doctor check `guide_docs` catches a missing FILE, not a
  stale one).
- `profile/` — the user profile (ONE row): `models` (record), `store`
  (read-never-writes, partial save), `presets` (vocabularies; unknown key =
  free text), `language` (pure script-level leakage detector), `block`
  (the one renderer, bounded + never raises). `personas/builtins.py` holds the
  built-in catalog (importable — `app.py` still exposes it as `d._PERSONAS`).
- `skills/` — recursive discovery incl. `~/.claude/skills`, `~/.claude/plugins`,
  `~/.codex/skills` (`framework.py::external_skill_roots`); registry
  repopulates IN PLACE; skills inject into prompts (provider-agnostic), the
  agent-facing tools are just search/load. `workflows/` — store + engine
  (note: `POST /workflows/run` spawns the run in the background and returns the
  record immediately; it does NOT block until steps finish). `ltm/`,
  `memory/`, `comm/`, `computeruse/`, `sandbox/`, `scheduling/`.
- `documents/` — `pdf_classify.py` is the per-PAGE scan router (v1.176.0,
  wraps the MIT `pdf-inspector` Rust extension). It answers "which pages of
  this PDF are scans", which `looks_scanned_pdf` structurally cannot: that
  asks ONE question of the whole file, so a native-text return with a scanned
  K-1 stapled in at page 12 reads as fully readable and the scan is silently
  invisible. THE ASYMMETRY IS THE SAFETY ARGUMENT and must survive any edit:
  `needs_ocr` ORs the classifier with the old heuristic and never ANDs, an
  empty plan falls back to harvesting rather than to harvesting nothing, and
  every entry point returns `None` (never raises) so an absent or broken
  extension degrades to exactly the v1.174.0 behaviour. A classifier may make
  the app read MORE of a client's document, never less. It is a NATIVE wheel:
  `packaging/ironjarvis.spec` entry + a RECOMMENDED `doctor` check, because a
  packaged build that dropped it would degrade silently forever (the pikepdf
  lesson). Cache version bumped to 2 — v1 records covered the first N pages
  and serving one for a mixed file would freeze that blindness in permanently.
  Also: readers (extract_text: pdf/docx/xlsx/pptx/csv/text/images),
  writers (markdown-AWARE rich creation: headings/lists/tables/code become
  real structure in docx/pdf/pptx/html; xlsx multi-sheet dict + formulas),
  markdown.py (the shared block parser), tools (read/write/extract_pdf/
  convert_document). `tools/images.py` — view_image (vision via the router),
  image_convert/resize/info (Pillow). `tools/pixio.py` — generative media.
- **Build (`app/terminals/page.tsx`) has TWO shapes** (v1.218.0), chosen by
  `ij.build.shape` and defaulting to `rail`. `rail` is
  `components/terminal/PaneRail.tsx` — every live pane in a column with its
  state, one pane focused beside it; `canvas` is the original free-form
  react-rnd surface. ONE pane body serves both: only the wrapper differs, and
  keeping it that way is the point (two copies would drift inside the pane the
  user works in). In the rail every pane still RENDERS — same box, all mounted,
  `visibility` toggled — because the v1.190.0 rule stands: a terminal whose
  holder has no size wraps its replay into a buffer no later fit can re-wrap,
  so a hidden pane must keep a real box. Never `display:none`, never an
  unmount. The rail also needs a fallback focus (`activeId`): on the canvas a
  null focus is harmless, in the rail it renders an empty workspace. The
  TerminalPane component itself CAN unmount (leaving Build, a shape flip) —
  since v1.243.0 its xterm and socket live in `components/terminal/
  paneHost.ts` and are parked, not destroyed (see the hard rule).
- `dashboard/app/<route>/page.tsx` per page; shared in `dashboard/components/`
  (`ui.tsx` primitives, `Sidebar.tsx` nav incl. Simple/Advanced mode,
  `ModelSwitcher.tsx` quality dial) and `dashboard/lib/` (`api.ts` fetch+auth,
  `useEvents.ts` one socket per window (EventsProvider), `types.ts`). Canvas editors: `components/workflow/`
  (agents.ts lives HERE, not lib/). Terminals page = free-form react-rnd
  canvas; pane header class `ij-term-drag` is the drag handle.
- `desktop/main.js` — supervisor (auto-restart children), tray, global
  hotkeys (Ctrl+Shift+J window, Ctrl+Shift+Space Spotlight), updater +
  update IPC, native clipboard IPC, media permissions. `preload.js` exposes
  `window.ironjarvis` (token, clipboard, update bridge).

## Verifying against the LIVE app (the user's running install)

```bash
tok=$(cat "C:/Users/VR/AppData/Roaming/Iron Jarvis/token.txt")
curl -s -H "Authorization: Bearer $tok" http://127.0.0.1:8787/health
# Event forensics (provider failures etc.):
#   SQLite: %APPDATA%/Iron Jarvis/.ironjarvis/ironjarvis.db, table eventrecord
```
Live-probing beats speculation — most "it's broken" reports this project has
seen were diagnosed in one curl (wrong version running, retired model id,
rate-limited provider, mock default).

## Direction (the product thesis)

Daily driver for creative + coding + office work, with high
interconnectedness. The known missing link is a **context spine**: a
first-class Project/workspace concept that chat, terminals, workflows, and
documents all tag into, so every agent call carries "what the user is working
on". Prefer features that CONNECT existing surfaces over new standalone
surfaces. Never trade trust for magic: honest errors beat fabricated output,
suggest-don't-act for anything autonomous, everything reviewable.
